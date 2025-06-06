# myapp/routes/reports.py

import os
import tempfile
from datetime import datetime, date
from decimal import Decimal
from flask import (
    Blueprint, render_template, request, redirect, url_for, 
    flash, send_file, current_app, jsonify
)
from sqlalchemy import and_, or_, func, extract
from sqlalchemy.orm import joinedload, selectinload
from io import BytesIO

# Imports opcionales para Excel y PDF
try:
    import xlsxwriter
    XLSXWRITER_AVAILABLE = True
except ImportError:
    XLSXWRITER_AVAILABLE = False

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter, A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

# Imports de auth y permisos
from flask_login import login_required, current_user
from ..decorators import role_required, with_owner_filtering

# Imports de modelos y utilidades
from ..models import db, Factura, Contrato, Propietario, Inquilino, Propiedad
from ..utils.database_helpers import get_filtered_facturas, get_filtered_contratos
from ..utils.owner_session import get_active_owner_context
from ..forms import CSRFOnlyForm

reports_bp = Blueprint('reports_bp', __name__, url_prefix='/reports')

@reports_bp.route('/')
@login_required
@role_required(['admin', 'gestor'])
def index():
    """Página principal de informes y exportaciones."""
    csrf_form = CSRFOnlyForm()
    
    # Obtener contexto del propietario activo
    active_owner_context = get_active_owner_context()
    propietarios_disponibles = []
    
    if current_user.role == 'admin':
        propietarios_disponibles = Propietario.query.order_by(Propietario.nombre).all()
    else:
        propietarios_disponibles = list(current_user.propietarios_asignados)
    
    return render_template(
        'reports/index.html',
        title="Informes y Exportaciones",
        csrf_form=csrf_form,
        propietarios=propietarios_disponibles,
        active_owner=active_owner_context.get('active_owner') if active_owner_context else None,
        preselected_owner_id=active_owner_context.get('active_owner_id') if active_owner_context else None
    )

@reports_bp.route('/exportar-facturas', methods=['POST'])
@login_required
@role_required(['admin', 'gestor'])
@with_owner_filtering()
def exportar_facturas_excel():
    """Exporta facturas a formato Excel XLS mejorado."""
    if not XLSXWRITER_AVAILABLE:
        flash("La librería xlsxwriter no está instalada. Ejecuta: pip install xlsxwriter", "error")
        return redirect(url_for('reports_bp.index'))
    
    try:
        # Obtener parámetros del formulario
        propietario_id = request.form.get('propietario_id')
        fecha_desde = request.form.get('fecha_desde')
        fecha_hasta = request.form.get('fecha_hasta')
        estado_factura = request.form.get('estado_factura', '')
        
        # Construir filtros base
        filtros = []
        
        # Filtro por propietario específico
        if propietario_id and propietario_id != 'todos':
            try:
                prop_id = int(propietario_id)
                filtros.append(
                    Factura.contrato.has(
                        Contrato.propiedad_ref.has(
                            Propiedad.propietario_id == prop_id
                        )
                    )
                )
            except ValueError:
                flash("ID de propietario inválido", "error")
                return redirect(url_for('reports_bp.index'))
        
        # Filtros de fecha
        if fecha_desde:
            try:
                fecha_desde_dt = datetime.strptime(fecha_desde, '%Y-%m-%d').date()
                filtros.append(Factura.fecha_emision >= fecha_desde_dt)
            except ValueError:
                flash("Fecha desde inválida", "error")
                return redirect(url_for('reports_bp.index'))
                
        if fecha_hasta:
            try:
                fecha_hasta_dt = datetime.strptime(fecha_hasta, '%Y-%m-%d').date()
                filtros.append(Factura.fecha_emision <= fecha_hasta_dt)
            except ValueError:
                flash("Fecha hasta inválida", "error")
                return redirect(url_for('reports_bp.index'))
        
        # Filtro por estado
        if estado_factura and estado_factura != 'todos':
            filtros.append(Factura.estado == estado_factura)
        
        # Consulta de facturas con filtrado automático por propietario activo
        query = get_filtered_facturas(include_relations=True)
        
        # Aplicar filtros adicionales
        if filtros:
            query = query.filter(and_(*filtros))
        
        facturas = query.order_by(Factura.fecha_emision.desc(), Factura.numero_factura.desc()).all()
        
        if not facturas:
            flash("No se encontraron facturas con los criterios especificados", "warning")
            return redirect(url_for('reports_bp.index'))
        
        # Crear archivo Excel
        output = BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        worksheet = workbook.add_worksheet('Facturas')
        
        # Formatos
        header_format = workbook.add_format({
            'bold': True,
            'bg_color': '#4472C4',
            'font_color': 'white',
            'border': 1,
            'align': 'center',
            'valign': 'vcenter'
        })
        
        data_format = workbook.add_format({
            'border': 1,
            'align': 'left',
            'valign': 'vcenter'
        })
        
        number_format = workbook.add_format({
            'border': 1,
            'align': 'right',
            'valign': 'vcenter',
            'num_format': '#,##0.00'
        })
        
        percent_format = workbook.add_format({
            'border': 1,
            'align': 'center',
            'valign': 'vcenter'
        })
        
        date_format = workbook.add_format({
            'border': 1,
            'align': 'center',
            'valign': 'vcenter',
            'num_format': 'dd/mm/yyyy'
        })
        
        # Headers según el formato de la imagen
        headers = [
            'Fecha', 'Serie', 'Factura', 'NIF', 'Nombre', 'Base', 
            '% IVA', 'Importe IVA', '% IRPF', 'Importe IRPF', 
            'Total', 'Cod.Concepto', 'Concepto', 'Local'
        ]
        
        # Escribir headers
        for col, header in enumerate(headers):
            worksheet.write(0, col, header, header_format)
        
        # Configurar ancho de columnas
        column_widths = [12, 8, 12, 15, 30, 12, 8, 12, 8, 12, 12, 12, 25, 20]
        for col, width in enumerate(column_widths):
            worksheet.set_column(col, col, width)
        
        # Escribir datos
        row = 1
        for factura in facturas:
            # Obtener datos relacionados
            contrato = factura.contrato
            inquilino = contrato.inquilino_ref if contrato else None
            propiedad = contrato.propiedad_ref if contrato else None
            
            # Calcular importes
            base_imponible = factura.importe_sin_iva or Decimal('0')
            importe_iva = factura.importe_iva or Decimal('0')
            importe_irpf = factura.importe_irpf or Decimal('0')
            total = factura.importe_total or Decimal('0')
            
            # Calcular porcentajes
            porc_iva = Decimal('21') if importe_iva > 0 else Decimal('0')  # Asumiendo IVA del 21%
            porc_irpf = Decimal('19') if importe_irpf > 0 else Decimal('0')  # IRPF común del 19%
            
            # Escribir fila de datos
            worksheet.write(row, 0, factura.fecha_emision, date_format)
            worksheet.write(row, 1, factura.serie or '', data_format)
            worksheet.write(row, 2, factura.numero_factura or '', data_format)
            worksheet.write(row, 3, inquilino.nif if inquilino else '', data_format)
            worksheet.write(row, 4, inquilino.nombre if inquilino else '', data_format)
            worksheet.write(row, 5, float(base_imponible), number_format)
            worksheet.write(row, 6, f"{porc_iva}%", percent_format)
            worksheet.write(row, 7, float(importe_iva), number_format)
            worksheet.write(row, 8, f"{porc_irpf}%" if importe_irpf > 0 else "", percent_format)
            worksheet.write(row, 9, float(importe_irpf), number_format)
            worksheet.write(row, 10, float(total), number_format)
            worksheet.write(row, 11, "ALQ", data_format)  # Código concepto alquiler
            worksheet.write(row, 12, factura.concepto or "Alquiler", data_format)
            worksheet.write(row, 13, propiedad.direccion if propiedad else '', data_format)
            
            row += 1
        
        # Agregar fila de totales
        if facturas:
            total_base = sum(f.importe_sin_iva or Decimal('0') for f in facturas)
            total_iva = sum(f.importe_iva or Decimal('0') for f in facturas)
            total_irpf = sum(f.importe_irpf or Decimal('0') for f in facturas)
            total_general = sum(f.importe_total or Decimal('0') for f in facturas)
            
            # Formato para totales
            total_format = workbook.add_format({
                'bold': True,
                'bg_color': '#D9E1F2',
                'border': 1,
                'align': 'right',
                'valign': 'vcenter',
                'num_format': '#,##0.00'
            })
            
            total_label_format = workbook.add_format({
                'bold': True,
                'bg_color': '#D9E1F2',
                'border': 1,
                'align': 'center',
                'valign': 'vcenter'
            })
            
            worksheet.write(row, 4, 'TOTALES:', total_label_format)
            worksheet.write(row, 5, float(total_base), total_format)
            worksheet.write(row, 7, float(total_iva), total_format)
            worksheet.write(row, 9, float(total_irpf), total_format)
            worksheet.write(row, 10, float(total_general), total_format)
        
        workbook.close()
        output.seek(0)
        
        # Generar nombre de archivo
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"facturas_export_{timestamp}.xlsx"
        
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        
    except Exception as e:
        current_app.logger.error(f"Error exportando facturas a Excel: {e}", exc_info=True)
        flash("Error al generar el archivo Excel. Inténtalo de nuevo.", "error")
        return redirect(url_for('reports_bp.index'))

@reports_bp.route('/listado-facturacion', methods=['POST'])
@login_required
@role_required(['admin', 'gestor'])
@with_owner_filtering()
def listado_facturacion():
    """Genera listado de facturación con visualización previa."""
    try:
        # Obtener parámetros del formulario
        propietario_id = request.form.get('propietario_id')
        contrato_ids = request.form.getlist('contrato_ids')
        fecha_desde = request.form.get('fecha_desde')
        fecha_hasta = request.form.get('fecha_hasta')
        
        # Validaciones básicas
        if not propietario_id:
            flash("Debe seleccionar un propietario", "error")
            return redirect(url_for('reports_bp.index'))
            
        if not fecha_desde or not fecha_hasta:
            flash("Debe especificar el rango de fechas", "error")
            return redirect(url_for('reports_bp.index'))
        
        try:
            propietario_id = int(propietario_id)
            fecha_desde_dt = datetime.strptime(fecha_desde, '%Y-%m-%d').date()
            fecha_hasta_dt = datetime.strptime(fecha_hasta, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            flash("Formato de datos inválido", "error")
            return redirect(url_for('reports_bp.index'))
        
        # Obtener propietario
        propietario = db.session.get(Propietario, propietario_id)
        if not propietario:
            flash("Propietario no encontrado", "error")
            return redirect(url_for('reports_bp.index'))
        
        # Construir consulta de facturas
        query = get_filtered_facturas(include_relations=True)
        
        # Filtros
        filtros = [
            Factura.fecha_emision >= fecha_desde_dt,
            Factura.fecha_emision <= fecha_hasta_dt,
            Factura.contrato.has(
                Contrato.propiedad_ref.has(
                    Propiedad.propietario_id == propietario_id
                )
            )
        ]
        
        # Filtro por contratos específicos si se seleccionaron
        if contrato_ids and contrato_ids != ['todos']:
            try:
                contrato_ids_int = [int(cid) for cid in contrato_ids if cid.isdigit()]
                if contrato_ids_int:
                    filtros.append(Factura.contrato_id.in_(contrato_ids_int))
            except ValueError:
                pass
        
        query = query.filter(and_(*filtros))
        facturas = query.order_by(Factura.fecha_emision.asc(), Factura.numero_factura.asc()).all()
        
        if not facturas:
            flash("No se encontraron facturas con los criterios especificados", "warning")
            return redirect(url_for('reports_bp.index'))
        
        # Agrupar facturas por contrato
        facturas_por_contrato = {}
        for factura in facturas:
            contrato_id = factura.contrato_id
            if contrato_id not in facturas_por_contrato:
                facturas_por_contrato[contrato_id] = {
                    'contrato': factura.contrato,
                    'facturas': [],
                    'totales': {
                        'base_imponible': Decimal('0'),
                        'importe_iva': Decimal('0'),
                        'importe_irpf': Decimal('0'),
                        'total': Decimal('0')
                    }
                }
            
            facturas_por_contrato[contrato_id]['facturas'].append(factura)
            
            # Sumar totales
            facturas_por_contrato[contrato_id]['totales']['base_imponible'] += factura.importe_sin_iva or Decimal('0')
            facturas_por_contrato[contrato_id]['totales']['importe_iva'] += factura.importe_iva or Decimal('0')
            facturas_por_contrato[contrato_id]['totales']['importe_irpf'] += factura.importe_irpf or Decimal('0')
            facturas_por_contrato[contrato_id]['totales']['total'] += factura.importe_total or Decimal('0')
        
        # Calcular totales generales
        totales_generales = {
            'base_imponible': sum(grupo['totales']['base_imponible'] for grupo in facturas_por_contrato.values()),
            'importe_iva': sum(grupo['totales']['importe_iva'] for grupo in facturas_por_contrato.values()),
            'importe_irpf': sum(grupo['totales']['importe_irpf'] for grupo in facturas_por_contrato.values()),
            'total': sum(grupo['totales']['total'] for grupo in facturas_por_contrato.values())
        }
        
        return render_template(
            'reports/listado_facturacion.html',
            title="Listado de Facturación",
            propietario=propietario,
            facturas_por_contrato=facturas_por_contrato,
            totales_generales=totales_generales,
            fecha_desde=fecha_desde_dt,
            fecha_hasta=fecha_hasta_dt,
            csrf_form=CSRFOnlyForm()
        )
        
    except Exception as e:
        current_app.logger.error(f"Error generando listado de facturación: {e}", exc_info=True)
        flash("Error al generar el listado. Inténtalo de nuevo.", "error")
        return redirect(url_for('reports_bp.index'))

@reports_bp.route('/listado-facturacion-pdf', methods=['POST'])
@login_required
@role_required(['admin', 'gestor'])
@with_owner_filtering()
def generar_pdf_facturacion():
    """Genera PDF del listado de facturación."""
    if not REPORTLAB_AVAILABLE:
        flash("La librería reportlab no está instalada. Ejecuta: pip install reportlab", "error")
        return redirect(url_for('reports_bp.index'))
    
    try:
        # Reutilizar la misma lógica que listado_facturacion para obtener datos
        propietario_id = request.form.get('propietario_id')
        contrato_ids = request.form.getlist('contrato_ids')
        fecha_desde = request.form.get('fecha_desde')
        fecha_hasta = request.form.get('fecha_hasta')
        
        # Validaciones básicas
        if not propietario_id:
            flash("Debe seleccionar un propietario", "error")
            return redirect(url_for('reports_bp.index'))
            
        if not fecha_desde or not fecha_hasta:
            flash("Debe especificar el rango de fechas", "error")
            return redirect(url_for('reports_bp.index'))
        
        try:
            propietario_id = int(propietario_id)
            fecha_desde_dt = datetime.strptime(fecha_desde, '%Y-%m-%d').date()
            fecha_hasta_dt = datetime.strptime(fecha_hasta, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            flash("Formato de datos inválido", "error")
            return redirect(url_for('reports_bp.index'))
        
        # Obtener propietario
        propietario = db.session.get(Propietario, propietario_id)
        if not propietario:
            flash("Propietario no encontrado", "error")
            return redirect(url_for('reports_bp.index'))
        
        # Construir consulta de facturas (igual que en listado_facturacion)
        query = get_filtered_facturas(include_relations=True)
        
        filtros = [
            Factura.fecha_emision >= fecha_desde_dt,
            Factura.fecha_emision <= fecha_hasta_dt,
            Factura.contrato.has(
                Contrato.propiedad_ref.has(
                    Propiedad.propietario_id == propietario_id
                )
            )
        ]
        
        if contrato_ids and contrato_ids != ['todos']:
            try:
                contrato_ids_int = [int(cid) for cid in contrato_ids if cid.isdigit()]
                if contrato_ids_int:
                    filtros.append(Factura.contrato_id.in_(contrato_ids_int))
            except ValueError:
                pass
        
        query = query.filter(and_(*filtros))
        facturas = query.order_by(Factura.fecha_emision.asc(), Factura.numero_factura.asc()).all()
        
        if not facturas:
            flash("No se encontraron facturas para generar el PDF", "warning")
            return redirect(url_for('reports_bp.index'))
        
        # Agrupar facturas por contrato (igual que en listado_facturacion)
        facturas_por_contrato = {}
        for factura in facturas:
            contrato_id = factura.contrato_id
            if contrato_id not in facturas_por_contrato:
                facturas_por_contrato[contrato_id] = {
                    'contrato': factura.contrato,
                    'facturas': [],
                    'totales': {
                        'base_imponible': Decimal('0'),
                        'importe_iva': Decimal('0'),
                        'importe_irpf': Decimal('0'),
                        'total': Decimal('0')
                    }
                }
            
            facturas_por_contrato[contrato_id]['facturas'].append(factura)
            
            # Sumar totales
            facturas_por_contrato[contrato_id]['totales']['base_imponible'] += factura.importe_sin_iva or Decimal('0')
            facturas_por_contrato[contrato_id]['totales']['importe_iva'] += factura.importe_iva or Decimal('0')
            facturas_por_contrato[contrato_id]['totales']['importe_irpf'] += factura.importe_irpf or Decimal('0')
            facturas_por_contrato[contrato_id]['totales']['total'] += factura.importe_total or Decimal('0')
        
        # Calcular totales generales
        totales_generales = {
            'base_imponible': sum(grupo['totales']['base_imponible'] for grupo in facturas_por_contrato.values()),
            'importe_iva': sum(grupo['totales']['importe_iva'] for grupo in facturas_por_contrato.values()),
            'importe_irpf': sum(grupo['totales']['importe_irpf'] for grupo in facturas_por_contrato.values()),
            'total': sum(grupo['totales']['total'] for grupo in facturas_por_contrato.values())
        }
        
        # Crear PDF
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        story = []
        
        # Estilos
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=18,
            spaceAfter=30,
            alignment=1,  # Center
            textColor=colors.darkblue
        )
        
        header_style = ParagraphStyle(
            'CustomHeader',
            parent=styles['Heading2'],
            fontSize=14,
            spaceAfter=12,
            textColor=colors.darkblue
        )
        
        normal_style = styles['Normal']
        
        # Título principal
        story.append(Paragraph("LISTADO DE FACTURACIÓN", title_style))
        story.append(Spacer(1, 12))
        
        # Información del propietario
        propietario_info = f"""
        <b>Propietario:</b> {propietario.nombre}<br/>
        <b>NIF:</b> {propietario.nif}<br/>
        <b>Email:</b> {propietario.email or 'No especificado'}<br/>
        <b>Teléfono:</b> {propietario.telefono or 'No especificado'}<br/>
        <b>Período:</b> {fecha_desde_dt.strftime('%d/%m/%Y')} - {fecha_hasta_dt.strftime('%d/%m/%Y')}
        """
        story.append(Paragraph(propietario_info, normal_style))
        story.append(Spacer(1, 20))
        
        # Tabla por cada contrato
        for contrato_id, datos in facturas_por_contrato.items():
            # Header del contrato
            contrato_header = f"Contrato {datos['contrato'].numero_contrato or contrato_id}"
            if datos['contrato'].inquilino_ref:
                contrato_header += f" - {datos['contrato'].inquilino_ref.nombre}"
            if datos['contrato'].propiedad_ref:
                contrato_header += f" - {datos['contrato'].propiedad_ref.direccion}"
            
            story.append(Paragraph(contrato_header, header_style))
            
            # Datos para la tabla
            table_data = [
                ['Fecha', 'Nº Factura', 'NIF', 'Nombre', 'Base Imp.', 'IVA', 'IRPF', 'Total']
            ]
            
            for factura in datos['facturas']:
                inquilino = factura.contrato.inquilino_ref if factura.contrato else None
                table_data.append([
                    factura.fecha_emision.strftime('%d/%m/%Y') if factura.fecha_emision else '-',
                    factura.numero_factura or '-',
                    inquilino.nif if inquilino else '-',
                    inquilino.nombre if inquilino else '-',
                    f"{factura.importe_sin_iva or 0:.2f} €",
                    f"{factura.importe_iva or 0:.2f} €",
                    f"{factura.importe_irpf or 0:.2f} €",
                    f"{factura.importe_total or 0:.2f} €"
                ])
            
            # Fila de totales del contrato
            table_data.append([
                '', '', '', 'TOTAL CONTRATO:',
                f"{datos['totales']['base_imponible']:.2f} €",
                f"{datos['totales']['importe_iva']:.2f} €",
                f"{datos['totales']['importe_irpf']:.2f} €",
                f"{datos['totales']['total']:.2f} €"
            ])
            
            # Crear tabla
            table = Table(table_data)
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('ALIGN', (3, 1), (3, -1), 'LEFT'),  # Nombres alineados a la izquierda
                ('ALIGN', (4, 1), (-1, -1), 'RIGHT'),  # Importes alineados a la derecha
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),  # Fila de totales
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                ('GRID', (0, 0), (-1, -1), 1, colors.black)
            ]))
            
            story.append(table)
            story.append(Spacer(1, 20))
        
        # Totales generales
        story.append(Paragraph("TOTALES GENERALES", header_style))
        totales_data = [
            ['Concepto', 'Importe'],
            ['Base Imponible', f"{totales_generales['base_imponible']:.2f} €"],
            ['Importe IVA', f"{totales_generales['importe_iva']:.2f} €"],
            ['Importe IRPF', f"{totales_generales['importe_irpf']:.2f} €"],
            ['TOTAL GENERAL', f"{totales_generales['total']:.2f} €"]
        ]
        
        totales_table = Table(totales_data, colWidths=[3*inch, 2*inch])
        totales_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('BACKGROUND', (0, -1), (-1, -1), colors.lightblue),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 12),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        
        story.append(totales_table)
        
        # Información adicional
        story.append(Spacer(1, 30))
        info_adicional = f"""
        <b>Listado generado:</b> {datetime.now().strftime('%d/%m/%Y a las %H:%M')}<br/>
        <b>Contratos:</b> {len(facturas_por_contrato)}<br/>
        <b>Facturas:</b> {len(facturas)}
        """
        story.append(Paragraph(info_adicional, normal_style))
        
        # Generar PDF
        doc.build(story)
        buffer.seek(0)
        
        # Generar nombre de archivo
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        sanitized_name = "".join(c if c.isalnum() else "_" for c in propietario.nombre)
        filename = f"listado_facturacion_{sanitized_name}_{timestamp}.pdf"
        
        return send_file(
            buffer,
            as_attachment=True,
            download_name=filename,
            mimetype='application/pdf'
        )
        
    except Exception as e:
        current_app.logger.error(f"Error generando PDF de facturación: {e}", exc_info=True)
        flash("Error al generar el PDF. Inténtalo de nuevo.", "error")
        return redirect(url_for('reports_bp.index'))