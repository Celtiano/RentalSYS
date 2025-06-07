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

@reports_bp.route('/api/contratos/<int:propietario_id>')
@login_required
@role_required(['admin', 'gestor'])
def obtener_contratos_propietario(propietario_id):
    """API para obtener contratos de un propietario específico."""
    try:
        # Verificar que el propietario existe
        propietario = db.session.get(Propietario, propietario_id)
        if not propietario:
            return jsonify({'error': 'Propietario no encontrado'}), 404
        
        # Obtener contratos activos del propietario
        contratos = db.session.query(Contrato)\
            .join(Propiedad)\
            .filter(Propiedad.propietario_id == propietario_id)\
            .filter(Contrato.activo == True)\
            .order_by(Propiedad.direccion)\
            .all()
        
        # Formatear datos para el frontend
        contratos_data = []
        for contrato in contratos:
            contratos_data.append({
                'id': contrato.id,
                'descripcion': f"{contrato.propiedad_ref.direccion} - {contrato.inquilino.nombre}",
                'direccion': contrato.propiedad_ref.direccion,
                'inquilino': contrato.inquilino.nombre,
                'precio_mensual': float(contrato.precio_mensual) if contrato.precio_mensual else 0
            })
        
        return jsonify({
            'success': True,
            'contratos': contratos_data,
            'total': len(contratos_data)
        })
        
    except Exception as e:
        current_app.logger.error(f"Error obteniendo contratos del propietario {propietario_id}: {e}")
        return jsonify({'error': 'Error interno del servidor'}), 500

@reports_bp.route('/')
@login_required
@role_required(['admin', 'gestor'])
def index():
    """Página principal de informes y exportaciones (versión simplificada)."""
    try:
        csrf_form = CSRFOnlyForm()
        
        # Obtener contexto del propietario activo de forma segura
        try:
            active_owner_context = get_active_owner_context()
        except Exception as e:
            current_app.logger.warning(f"Error obteniendo contexto de propietario activo: {e}")
            active_owner_context = None
        
        # Obtener propietarios disponibles
        propietarios_disponibles = []
        try:
            if current_user.role == 'admin':
                propietarios_disponibles = Propietario.query.order_by(Propietario.nombre).all()
            else:
                propietarios_disponibles = list(current_user.propietarios_asignados)
        except Exception as e:
            current_app.logger.warning(f"Error obteniendo propietarios: {e}")
            propietarios_disponibles = []
        
        # Extraer información del contexto de forma segura
        active_owner = None
        preselected_owner_id = None
        
        if active_owner_context:
            active_owner = active_owner_context.get('active_owner')
            preselected_owner_id = active_owner_context.get('active_owner_id')
        
        current_app.logger.info(f"Reports index: Usuario={current_user.username}, Propietario activo={active_owner.nombre if active_owner else 'Ninguno'}")
        
        return render_template(
            'reports/index.html',
            title="Informes y Exportaciones",
            csrf_form=csrf_form,
            propietarios=propietarios_disponibles,
            active_owner=active_owner,
            preselected_owner_id=preselected_owner_id
        )
        
    except Exception as e:
        current_app.logger.error(f"Error en reports index: {e}", exc_info=True)
        
        # Fallback en caso de error
        csrf_form = CSRFOnlyForm()
        flash("Advertencia: Algunos datos pueden no estar disponibles", "warning")
        
        return render_template(
            'reports/index.html',
            title="Informes y Exportaciones",
            csrf_form=csrf_form,
            propietarios=[],
            active_owner=None,
            preselected_owner_id=None
        )

@reports_bp.route('/exportar-facturas', methods=['POST'])
@login_required
@role_required(['admin', 'gestor'])
def exportar_facturas_excel():
    """Exporta facturas a formato Excel XLS mejorado (versión simplificada)."""
    import datetime as dt_module
    from decimal import Decimal as Dec
    
    if not XLSXWRITER_AVAILABLE:
        flash("La librería xlsxwriter no está instalada. Ejecuta: pip install xlsxwriter", "error")
        return redirect(url_for('reports_bp.index'))
    
    try:
        current_app.logger.info("=== EXPORTANDO FACTURAS A EXCEL ===")
        
        # Obtener parámetros del formulario (igual que función PDF)
        propietario_id = request.form.get('propietario_id')
        fecha_desde = request.form.get('fecha_desde')
        fecha_hasta = request.form.get('fecha_hasta')
        
        current_app.logger.info(f"Params Excel: prop={propietario_id}, desde={fecha_desde}, hasta={fecha_hasta}")
        
        # Validaciones básicas
        if not propietario_id:
            flash("Debe seleccionar un propietario", "error")
            return redirect(url_for('reports_bp.index'))
            
        if not fecha_desde or not fecha_hasta:
            flash("Debe especificar las fechas", "error")
            return redirect(url_for('reports_bp.index'))
        
        # Conversiones seguras (igual que función PDF)
        try:
            propietario_id = int(propietario_id)
            fecha_desde_dt = dt_module.datetime.strptime(fecha_desde, '%Y-%m-%d').date()
            fecha_hasta_dt = dt_module.datetime.strptime(fecha_hasta, '%Y-%m-%d').date()
        except (ValueError, TypeError) as e:
            current_app.logger.error(f"Error convirtiendo datos: {e}")
            flash(f"Error en formato de datos: {e}", "error")
            return redirect(url_for('reports_bp.index'))
        
        # Obtener propietario
        propietario = db.session.get(Propietario, propietario_id)
        if not propietario:
            flash("Propietario no encontrado", "error")
            return redirect(url_for('reports_bp.index'))
        
        current_app.logger.info(f"Propietario Excel: {propietario.nombre}")
        
        # Consulta SIMPLE (igual que función PDF)
        propiedades_ids = db.session.query(Propiedad.id).filter(Propiedad.propietario_id == propietario_id).all()
        propiedades_ids = [p[0] for p in propiedades_ids]
        
        facturas = db.session.query(Factura)\
            .filter(Factura.fecha_emision >= fecha_desde_dt)\
            .filter(Factura.fecha_emision <= fecha_hasta_dt)\
            .filter(Factura.propiedad_id.in_(propiedades_ids))\
            .order_by(Factura.fecha_emision.desc(), Factura.numero_factura.desc())\
            .all()
            
        current_app.logger.info(f"Facturas Excel encontradas: {len(facturas)}")
        
        if not facturas:
            flash(f"No se encontraron facturas para {propietario.nombre} en el período {fecha_desde} - {fecha_hasta}", "warning")
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
        for row, factura in enumerate(facturas, 1):
            # Obtener datos de contrato de forma segura
            contrato_numero = 'N/A'
            propiedad_direccion = 'N/A'
            inquilino_nombre = 'N/A'
            inquilino_nif = 'N/A'
            
            try:
                if factura.contrato_id:
                    contrato = db.session.get(Contrato, factura.contrato_id)
                    if contrato:
                        contrato_numero = contrato.numero_contrato or 'N/A'
                        if contrato.propiedad_ref:
                            propiedad_direccion = contrato.propiedad_ref.direccion or 'N/A'
                        if contrato.inquilino_ref:
                            inquilino_nombre = contrato.inquilino_ref.nombre or 'N/A'
                            inquilino_nif = contrato.inquilino_ref.nif or 'N/A'
            except Exception as e:
                current_app.logger.warning(f"Error obteniendo datos de contrato para factura {factura.id}: {e}")
            
            # Calcular importes de forma segura
            base_imponible = Dec(str(factura.subtotal or 0))
            importe_iva = Dec(str(factura.iva or 0))
            importe_irpf = Dec(str(factura.irpf or 0))
            total = Dec(str(factura.total or 0))
            
            # Calcular porcentajes
            porc_iva = Dec('21') if importe_iva > 0 else Dec('0')
            porc_irpf = Dec('19') if importe_irpf > 0 else Dec('0')
            
            # Escribir fila de datos
            worksheet.write(row, 0, factura.fecha_emision, date_format)
            worksheet.write(row, 1, factura.serie or '', data_format)
            worksheet.write(row, 2, factura.numero_factura or '', data_format)
            worksheet.write(row, 3, inquilino_nif, data_format)
            worksheet.write(row, 4, inquilino_nombre, data_format)
            worksheet.write(row, 5, float(base_imponible), number_format)
            worksheet.write(row, 6, f"{porc_iva}%", percent_format)
            worksheet.write(row, 7, float(importe_iva), number_format)
            worksheet.write(row, 8, f"{porc_irpf}%" if importe_irpf > 0 else "", percent_format)
            worksheet.write(row, 9, float(importe_irpf), number_format)
            worksheet.write(row, 10, float(total), number_format)
            worksheet.write(row, 11, "ALQ", data_format)
            worksheet.write(row, 12, factura.concepto or "Alquiler", data_format)
            worksheet.write(row, 13, propiedad_direccion, data_format)
        
        # Agregar fila de totales
        if facturas:
            row = len(facturas) + 1
            total_base = sum(Dec(str(f.subtotal or 0)) for f in facturas)
            total_iva = sum(Dec(str(f.iva or 0)) for f in facturas)
            total_irpf = sum(Dec(str(f.irpf or 0)) for f in facturas)
            total_general = sum(Dec(str(f.total or 0)) for f in facturas)
            
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
        filename = f"facturas_{propietario.nombre.replace(' ', '_')}_{fecha_desde}_{fecha_hasta}.xlsx"
        
        current_app.logger.info(f"Excel generado exitosamente: {filename}")
        
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        
    except Exception as e:
        current_app.logger.error(f"ERROR generando Excel: {e}", exc_info=True)
        flash(f"Error generando Excel: {str(e)}", "error")
        return redirect(url_for('reports_bp.index'))

@reports_bp.route('/listado-facturacion', methods=['POST'])
@login_required
@role_required(['admin', 'gestor'])
def listado_facturacion():
    """Versión ultra-simplificada que SIEMPRE funciona."""
    import datetime as dt_module
    from decimal import Decimal as Dec
    
    try:
        current_app.logger.info("=== LISTADO ULTRA-SIMPLE ===")
        
        # Obtener parámetros básicos
        propietario_id = request.form.get('propietario_id')
        fecha_desde = request.form.get('fecha_desde')  
        fecha_hasta = request.form.get('fecha_hasta')
        
        current_app.logger.info(f"Params: prop={propietario_id}, desde={fecha_desde}, hasta={fecha_hasta}")
        
        # Validación básica
        if not propietario_id:
            flash("Debe seleccionar un propietario", "error")
            return redirect(url_for('reports_bp.index'))
            
        if not fecha_desde or not fecha_hasta:
            flash("Debe especificar las fechas", "error")
            return redirect(url_for('reports_bp.index'))
        
        # Conversiones seguras
        try:
            propietario_id = int(propietario_id)
            fecha_desde_dt = dt_module.datetime.strptime(fecha_desde, '%Y-%m-%d').date()
            fecha_hasta_dt = dt_module.datetime.strptime(fecha_hasta, '%Y-%m-%d').date()
        except (ValueError, TypeError) as e:
            current_app.logger.error(f"Error convirtiendo datos: {e}")
            flash(f"Error en formato de datos: {e}", "error")
            return redirect(url_for('reports_bp.index'))
        
        # Obtener propietario
        propietario = db.session.get(Propietario, propietario_id)
        if not propietario:
            flash("Propietario no encontrado", "error")
            return redirect(url_for('reports_bp.index'))
        
        current_app.logger.info(f"Propietario: {propietario.nombre}")
        
        # Consulta ULTRA-SIMPLE - Solo facturas por propiedad
        current_app.logger.info(f"🔍 CONSULTA SQL con fechas: {fecha_desde_dt} <= fecha_emision <= {fecha_hasta_dt}")
        
        # Primero verificar qué propiedades tiene el propietario
        propiedades_ids = db.session.query(Propiedad.id).filter(Propiedad.propietario_id == propietario_id).all()
        propiedades_ids = [p[0] for p in propiedades_ids]
        current_app.logger.info(f"🏠 Propiedades del propietario: {propiedades_ids}")
        
        # Consulta con debug detallado
        query = db.session.query(Factura)\
            .filter(Factura.fecha_emision >= fecha_desde_dt)\
            .filter(Factura.fecha_emision <= fecha_hasta_dt)\
            .filter(Factura.propiedad_id.in_(propiedades_ids))\
            .order_by(Factura.fecha_emision.asc())
            
        # Debug: mostrar todas las facturas del propietario sin filtro de fecha
        todas_facturas = db.session.query(Factura)\
            .filter(Factura.propiedad_id.in_(propiedades_ids))\
            .order_by(Factura.fecha_emision.asc())\
            .all()
            
        current_app.logger.info(f"📊 Total facturas sin filtro fecha: {len(todas_facturas)}")
        for f in todas_facturas[:5]:  # Solo las primeras 5 para no spam
            current_app.logger.info(f"   Factura {f.numero_factura}: {f.fecha_emision} (tipo: {type(f.fecha_emision)})")
        
        facturas = query.all()
        current_app.logger.info(f"📊 Facturas CON filtro fecha: {len(facturas)}")
        
        # Si no hay facturas, mostrar debug adicional
        if not facturas and todas_facturas:
            current_app.logger.warning(f"⚠️ Hay {len(todas_facturas)} facturas sin filtro, pero 0 con filtro")
            current_app.logger.warning(f"   Fechas buscar: {fecha_desde_dt} a {fecha_hasta_dt}")
            current_app.logger.warning(f"   Fechas en BD: {[f.fecha_emision for f in todas_facturas[:3]]}")
            
        current_app.logger.info(f"Facturas encontradas: {len(facturas)}")
        
        if not facturas:
            flash(f"No se encontraron facturas para {propietario.nombre} en el período {fecha_desde} - {fecha_hasta}", "warning")
            return redirect(url_for('reports_bp.index'))
        
        # Agrupar facturas por contrato
        facturas_por_contrato = {}
        
        for factura in facturas:
            contrato_id = factura.contrato_id
            
            # Obtener información del contrato si no existe
            if contrato_id not in facturas_por_contrato:
                try:
                    contrato = db.session.get(Contrato, contrato_id)
                    facturas_por_contrato[contrato_id] = {
                        'contrato': contrato,
                        'facturas': [],
                        'totales': {
                            'base_imponible': Dec('0'),
                            'importe_iva': Dec('0'),
                            'importe_irpf': Dec('0'),
                            'total': Dec('0')
                        }
                    }
                except Exception as e:
                    current_app.logger.warning(f"Error obteniendo contrato {contrato_id}: {e}")
                    continue
            
            # Añadir factura al contrato
            facturas_por_contrato[contrato_id]['facturas'].append(factura)
            
            # Sumar totales del contrato
            facturas_por_contrato[contrato_id]['totales']['base_imponible'] += Dec(str(factura.subtotal or 0))
            facturas_por_contrato[contrato_id]['totales']['importe_iva'] += Dec(str(factura.iva or 0))
            facturas_por_contrato[contrato_id]['totales']['importe_irpf'] += Dec(str(factura.irpf or 0))
            facturas_por_contrato[contrato_id]['totales']['total'] += Dec(str(factura.total or 0))
        
        # Calcular totales generales
        totales_generales = {
            'base_imponible': Dec('0'),
            'importe_iva': Dec('0'),
            'importe_irpf': Dec('0'),
            'total': Dec('0')
        }
        
        for contrato_data in facturas_por_contrato.values():
            totales_generales['base_imponible'] += contrato_data['totales']['base_imponible']
            totales_generales['importe_iva'] += contrato_data['totales']['importe_iva']
            totales_generales['importe_irpf'] += contrato_data['totales']['importe_irpf']
            totales_generales['total'] += contrato_data['totales']['total']
        
        # Preparar datos para template
        datos = {
            'propietario': propietario,
            'facturas_por_contrato': facturas_por_contrato,
            'totales_generales': totales_generales,
            'fecha_desde': fecha_desde_dt,
            'fecha_hasta': fecha_hasta_dt,
            'total_facturas': len(facturas)
        }
        
        current_app.logger.info(f"Datos OK - Facturas: {len(facturas)}, Total: {totales_generales['total']}")
        
        # Renderizar template simple
        return render_template(
            'reports/listado_simple.html',
            **datos,
            csrf_form=CSRFOnlyForm()
        )
        
    except Exception as e:
        current_app.logger.error(f"ERROR CRÍTICO: {e}", exc_info=True)
        flash(f"Error interno: {str(e)}", "error")
        return redirect(url_for('reports_bp.index'))


@reports_bp.route('/listado-facturacion-pdf', methods=['POST'])
@login_required
@role_required(['admin', 'gestor'])
def listado_facturacion_pdf():
    """Generar listado de facturación en PDF usando reportlab."""
    import datetime as dt_module
    from decimal import Decimal as Dec
    from io import BytesIO
    
    try:
        current_app.logger.info("=== GENERANDO PDF LISTADO ===")
        current_app.logger.info("Importando librerías reportlab...")
        
        # Imports de reportlab con manejo de errores
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, letter
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        
        current_app.logger.info("✅ Reportlab importado correctamente")
        
        # Obtener parámetros (igual que función HTML)
        propietario_id = request.form.get('propietario_id')
        fecha_desde = request.form.get('fecha_desde')  
        fecha_hasta = request.form.get('fecha_hasta')
        
        # Validaciones (igual que función HTML)
        if not propietario_id or not fecha_desde or not fecha_hasta:
            flash("Parámetros incompletos para generar PDF", "error")
            return redirect(url_for('reports_bp.index'))
        
        # Conversiones (igual que función HTML)
        propietario_id = int(propietario_id)
        fecha_desde_dt = dt_module.datetime.strptime(fecha_desde, '%Y-%m-%d').date()
        fecha_hasta_dt = dt_module.datetime.strptime(fecha_hasta, '%Y-%m-%d').date()
        
        # Obtener datos (igual que función HTML)
        propietario = db.session.get(Propietario, propietario_id)
        if not propietario:
            flash("Propietario no encontrado", "error")
            return redirect(url_for('reports_bp.index'))
        
        # Misma consulta que función HTML
        propiedades_ids = db.session.query(Propiedad.id).filter(Propiedad.propietario_id == propietario_id).all()
        propiedades_ids = [p[0] for p in propiedades_ids]
        
        facturas = db.session.query(Factura)\
            .filter(Factura.fecha_emision >= fecha_desde_dt)\
            .filter(Factura.fecha_emision <= fecha_hasta_dt)\
            .filter(Factura.propiedad_id.in_(propiedades_ids))\
            .order_by(Factura.fecha_emision.asc())\
            .all()
            
        if not facturas:
            flash("No hay facturas para generar PDF", "warning")
            return redirect(url_for('reports_bp.index'))
        
        # Agrupar por contratos igual que función HTML
        facturas_por_contrato = {}
        
        for factura in facturas:
            contrato_id = factura.contrato_id
            
            if contrato_id not in facturas_por_contrato:
                try:
                    contrato = db.session.get(Contrato, contrato_id)
                    facturas_por_contrato[contrato_id] = {
                        'contrato': contrato,
                        'facturas': [],
                        'totales': {
                            'base_imponible': Dec('0'),
                            'importe_iva': Dec('0'),
                            'importe_irpf': Dec('0'),
                            'total': Dec('0')
                        }
                    }
                except Exception as e:
                    current_app.logger.warning(f"Error obteniendo contrato {contrato_id}: {e}")
                    continue
            
            facturas_por_contrato[contrato_id]['facturas'].append(factura)
            facturas_por_contrato[contrato_id]['totales']['base_imponible'] += Dec(str(factura.subtotal or 0))
            facturas_por_contrato[contrato_id]['totales']['importe_iva'] += Dec(str(factura.iva or 0))
            facturas_por_contrato[contrato_id]['totales']['importe_irpf'] += Dec(str(factura.irpf or 0))
            facturas_por_contrato[contrato_id]['totales']['total'] += Dec(str(factura.total or 0))
        
        # Calcular totales generales
        total_subtotal = sum(data['totales']['base_imponible'] for data in facturas_por_contrato.values())
        total_iva = sum(data['totales']['importe_iva'] for data in facturas_por_contrato.values())
        total_irpf = sum(data['totales']['importe_irpf'] for data in facturas_por_contrato.values())
        total_general = sum(data['totales']['total'] for data in facturas_por_contrato.values())
        
        # Crear PDF en memoria
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=0.7*inch, bottomMargin=0.5*inch)
        
        # Estilos optimizados para menos espacio
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=16,
            textColor=colors.darkblue,
            spaceAfter=12,
            alignment=1  # Centrado
        )
        
        subtitle_style = ParagraphStyle(
            'CustomSubtitle',
            parent=styles['Heading2'],
            fontSize=11,
            textColor=colors.black,
            spaceAfter=8
        )
        
        contract_info_style = ParagraphStyle(
            'ContractInfo',
            parent=styles['Normal'],
            fontSize=9,
            textColor=colors.darkgreen,
            spaceAfter=4,
            leftIndent=10
        )
        
        # Contenido del PDF
        story = []
        
        # Título
        story.append(Paragraph("LISTADO DE FACTURACIÓN", title_style))
        story.append(Spacer(1, 6))
        
        # Información del propietario (separada en dos líneas)
        propietario_info = f"<b>Propietario:</b> {propietario.nombre} | <b>NIF:</b> {propietario.nif}"
        periodo_info = f"<b>Período:</b> {fecha_desde_dt.strftime('%d/%m/%Y')} - {fecha_hasta_dt.strftime('%d/%m/%Y')}"
        story.append(Paragraph(propietario_info, subtitle_style))
        story.append(Paragraph(periodo_info, subtitle_style))
        story.append(Spacer(1, 10))
        
        # Generar tablas por contrato
        for contrato_id, contrato_data in facturas_por_contrato.items():
            contrato = contrato_data['contrato']
            
            # Título del contrato con información completa
            story.append(Spacer(1, 12))
            contract_title = f"<b>CONTRATO: {contrato.numero_contrato if contrato else contrato_id}</b>"
            story.append(Paragraph(contract_title, subtitle_style))
            
            # Información detallada del contrato
            if contrato:
                # Información de la propiedad
                if contrato.propiedad_ref:
                    direccion = contrato.propiedad_ref.direccion or "Sin dirección"
                    ref_catastral = contrato.propiedad_ref.referencia_catastral or "Sin ref. catastral"
                    propiedad_info = f"🏠 <b>Propiedad:</b> {direccion} | <b>Ref. Catastral:</b> {ref_catastral}"
                    story.append(Paragraph(propiedad_info, contract_info_style))
                
                # Información del inquilino
                if contrato.inquilino_ref:
                    inquilino_nombre = contrato.inquilino_ref.nombre or "Sin nombre"
                    inquilino_nif = contrato.inquilino_ref.nif or "Sin NIF"
                    inquilino_info = f"👤 <b>Inquilino:</b> {inquilino_nombre} | <b>DNI/NIF:</b> {inquilino_nif}"
                    story.append(Paragraph(inquilino_info, contract_info_style))
            
            story.append(Spacer(1, 6))
            
            # Tabla del contrato
            data = [['Fecha', 'Nº Factura', 'Subtotal', 'IVA', 'IRPF', 'Total']]
            
            for factura in contrato_data['facturas']:
                data.append([
                    factura.fecha_emision.strftime('%d/%m/%Y') if factura.fecha_emision else '-',
                    factura.numero_factura or '-',
                    f"{float(factura.subtotal or 0):.2f} €",
                    f"{float(factura.iva or 0):.2f} €", 
                    f"{float(factura.irpf or 0):.2f} €",
                    f"{float(factura.total or 0):.2f} €"
                ])
            
            # Totales del contrato
            data.append([
                'TOTAL',
                '',
                f"{float(contrato_data['totales']['base_imponible']):.2f} €",
                f"{float(contrato_data['totales']['importe_iva']):.2f} €",
                f"{float(contrato_data['totales']['importe_irpf']):.2f} €",
                f"{float(contrato_data['totales']['total']):.2f} €"
            ])
            
            # Crear tabla del contrato (más compacta)
            table = Table(data, colWidths=[1*inch, 1.3*inch, 0.9*inch, 0.9*inch, 0.9*inch, 1.1*inch])
            table.setStyle(TableStyle([
                # Header
                ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('ROWBACKGROUNDS', (0, 0), (-1, 0), [colors.grey]),
                
                # Datos
                ('FONTNAME', (0, 1), (-1, -2), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -2), 8),
                ('GRID', (0, 0), (-1, -2), 0.5, colors.black),
                ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.lightgrey]),
                
                # Totales del contrato (última fila)
                ('BACKGROUND', (0, -1), (-1, -1), colors.lightblue),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, -1), (-1, -1), 9),
                ('GRID', (0, -1), (-1, -1), 1, colors.black),
            ]))
            
            story.append(table)
        
        # Tabla de totales generales (más compacta)
        story.append(Spacer(1, 15))
        story.append(Paragraph("<b>TOTALES GENERALES DEL PROPIETARIO</b>", subtitle_style))
        
        totales_data = [
            ['Concepto', 'Importe'],
            ['Subtotal', f"{float(total_subtotal):.2f} €"],
            ['IVA', f"{float(total_iva):.2f} €"],
            ['IRPF', f"{float(total_irpf):.2f} €"],
            ['TOTAL GENERAL', f"{float(total_general):.2f} €"]
        ]
        
        totales_table = Table(totales_data, colWidths=[2.2*inch, 1.8*inch])
        totales_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('FONTSIZE', (0, 1), (-1, -2), 9),
            ('FONTSIZE', (0, -1), (-1, -1), 11),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white]),
        ]))
        
        story.append(totales_table)
        story.append(Spacer(1, 8))
        
        # Resumen compacto en una línea
        total_facturas_count = sum(len(data['facturas']) for data in facturas_por_contrato.values())
        resumen_info = f"<b>Contratos:</b> {len(facturas_por_contrato)} | <b>Facturas:</b> {total_facturas_count} | <b>Importe Total:</b> {float(total_general):.2f} €"
        story.append(Paragraph(resumen_info, contract_info_style))
        
        # Generar PDF
        doc.build(story)
        
        # Preparar respuesta
        buffer.seek(0)
        filename = f"listado_facturacion_{propietario.nombre.replace(' ', '_')}_{fecha_desde}_{fecha_hasta}.pdf"
        
        current_app.logger.info(f"PDF generado: {filename}")
        
        return send_file(
            buffer,
            as_attachment=True,
            download_name=filename,
            mimetype='application/pdf'
        )
        
    except ImportError as e:
        current_app.logger.error(f"ERROR de import en PDF: {e}", exc_info=True)
        flash(f"Error: Librería reportlab no disponible. {str(e)}", "error")
        return redirect(url_for('reports_bp.index'))
    except Exception as e:
        current_app.logger.error(f"ERROR generando PDF: {e}", exc_info=True)
        flash(f"Error generando PDF: {str(e)}", "error")
        return redirect(url_for('reports_bp.index'))


@reports_bp.route('/debug-fechas/<int:propietario_id>')
@login_required
@role_required(['admin', 'gestor'])
def debug_fechas(propietario_id):
    """Debug de fechas para entender el problema de filtrado."""
    import datetime as dt_module
    
    try:
        # Obtener propietario
        propietario = db.session.get(Propietario, propietario_id)
        if not propietario:
            flash("Propietario no encontrado", "error")
            return redirect(url_for('reports_bp.index'))
        
        # Obtener propiedades
        propiedades_ids = db.session.query(Propiedad.id).filter(Propiedad.propietario_id == propietario_id).all()
        propiedades_ids = [p[0] for p in propiedades_ids]
        
        # Obtener TODAS las facturas del propietario
        todas_facturas = db.session.query(Factura)\
            .filter(Factura.propiedad_id.in_(propiedades_ids))\
            .order_by(Factura.fecha_emision.asc())\
            .all()
        
        # Preparar información debug
        info_debug = []
        info_debug.append(f"📊 <b>Propietario:</b> {propietario.nombre}")
        info_debug.append(f"🏠 <b>Propiedades:</b> {len(propiedades_ids)} ({propiedades_ids})")
        info_debug.append(f"🧾 <b>Total facturas:</b> {len(todas_facturas)}")
        info_debug.append("<br>")
        
        if todas_facturas:
            info_debug.append("<b>📅 Facturas por fecha:</b>")
            for factura in todas_facturas:
                fecha_str = factura.fecha_emision.strftime('%Y-%m-%d') if factura.fecha_emision else 'Sin fecha'
                tipo_fecha = type(factura.fecha_emision).__name__
                info_debug.append(f"   • {factura.numero_factura}: {fecha_str} ({tipo_fecha})")
        else:
            info_debug.append("❌ <b>No hay facturas para este propietario</b>")
        
        # Test con fechas específicas
        info_debug.append("<br><b>🧪 Test con fecha específica:</b>")
        fecha_test = dt_module.date(2025, 1, 1)  # 01-01-2025
        
        facturas_test = db.session.query(Factura)\
            .filter(Factura.propiedad_id.in_(propiedades_ids))\
            .filter(Factura.fecha_emision >= fecha_test)\
            .filter(Factura.fecha_emision <= fecha_test)\
            .all()
        
        info_debug.append(f"   Buscando facturas para: {fecha_test}")
        info_debug.append(f"   Facturas encontradas: {len(facturas_test)}")
        
        if facturas_test:
            for f in facturas_test:
                info_debug.append(f"      ✅ {f.numero_factura}: {f.fecha_emision}")
        else:
            # Ver si hay facturas cercanas
            facturas_cercanas = db.session.query(Factura)\
                .filter(Factura.propiedad_id.in_(propiedades_ids))\
                .filter(Factura.fecha_emision >= dt_module.date(2024, 12, 30))\
                .filter(Factura.fecha_emision <= dt_module.date(2025, 1, 5))\
                .all()
            
            info_debug.append(f"   📍 Facturas en rango cercano (30-dic a 5-ene): {len(facturas_cercanas)}")
            for f in facturas_cercanas:
                info_debug.append(f"      📅 {f.numero_factura}: {f.fecha_emision}")
        
        # Crear HTML de respuesta simple
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Debug Fechas - {propietario.nombre}</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                .info {{ background: #f0f0f0; padding: 15px; border-radius: 5px; margin: 10px 0; }}
                .back {{ background: #007bff; color: white; padding: 10px 15px; text-decoration: none; border-radius: 5px; }}
            </style>
        </head>
        <body>
            <h1>🔍 Debug de Fechas</h1>
            <div class="info">
                {"<br>".join(info_debug)}
            </div>
            <br>
            <a href="{url_for('reports_bp.index')}" class="back">← Volver a Informes</a>
        </body>
        </html>
        """
        
        return html_content
        
    except Exception as e:
        current_app.logger.error(f"Error en debug fechas: {e}", exc_info=True)
        flash(f"Error en debug: {str(e)}", "error")
        return redirect(url_for('reports_bp.index'))

@reports_bp.route('/debug-listado/<int:propietario_id>')
@login_required
@role_required(['admin', 'gestor'])
def debug_listado(propietario_id):
    """Ruta de debug para probar componentes del listado."""
    try:
        # Test 1: Verificar propietario
        propietario = db.session.get(Propietario, propietario_id)
        if not propietario:
            return f"❌ Propietario {propietario_id} no encontrado"
        
        # Test 2: Contar facturas
        total_facturas = db.session.query(Factura).count()
        
        # Test 3: Contar facturas del propietario
        facturas_propietario = db.session.query(Factura)\
            .join(Contrato, Factura.contrato_id == Contrato.id)\
            .join(Propiedad, Contrato.propiedad_id == Propiedad.id)\
            .filter(Propiedad.propietario_id == propietario_id)\
            .count()
        
        # Test 4: Contar contratos del propietario
        contratos = db.session.query(Contrato)\
            .join(Propiedad)\
            .filter(Propiedad.propietario_id == propietario_id)\
            .count()
        
        # Test 5: Verificar template
        import os
        template_path = "myapp/templates/reports/listado_facturacion.html"
        template_exists = os.path.exists(template_path)
        
        csrf_form = CSRFOnlyForm()
        
        resultado = f"""
        <h1>🔍 Debug Listado - Propietario {propietario_id}</h1>
        <h2>✅ Propietario: {propietario.nombre}</h2>
        <p><strong>Total facturas en sistema:</strong> {total_facturas}</p>
        <p><strong>Facturas del propietario:</strong> {facturas_propietario}</p>
        <p><strong>Contratos del propietario:</strong> {contratos}</p>
        <p><strong>Template existe:</strong> {'✅ Sí' if template_exists else '❌ No'}</p>
        
        <h3>🧪 Test de Listado:</h3>
        <form method="POST" action="/reports/listado-facturacion">
            <input type="hidden" name="csrf_token" value="{csrf_form.csrf_token.current_token}">
            <input type="hidden" name="propietario_id" value="{propietario_id}">
            <label>Fecha desde: <input type="date" name="fecha_desde" value="2024-01-01" required></label><br><br>
            <label>Fecha hasta: <input type="date" name="fecha_hasta" value="2024-12-31" required></label><br><br>
            <input type="hidden" name="contrato_ids" value="todos">
            <button type="submit">🚀 Probar Listado</button>
        </form>
        
        <p><a href="/reports/">← Volver a Informes</a></p>
        """
        
        return resultado
        
    except Exception as e:
        return f"❌ Error en debug: {e}"

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
            Factura.contrato_ref.has(
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
            facturas_por_contrato[contrato_id]['totales']['base_imponible'] += factura.subtotal or Decimal('0')
            facturas_por_contrato[contrato_id]['totales']['importe_iva'] += factura.iva or Decimal('0')
            facturas_por_contrato[contrato_id]['totales']['importe_irpf'] += factura.irpf or Decimal('0')
            facturas_por_contrato[contrato_id]['totales']['total'] += factura.total or Decimal('0')
        
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
                inquilino = factura.contrato_ref.inquilino_ref if factura.contrato_ref else None
                table_data.append([
                    factura.fecha_emision.strftime('%d/%m/%Y') if factura.fecha_emision else '-',
                    factura.numero_factura or '-',
                    inquilino.nif if inquilino else '-',
                    inquilino.nombre if inquilino else '-',
                    f"{factura.subtotal or 0:.2f} €",
                    f"{factura.iva or 0:.2f} €",
                    f"{factura.irpf or 0:.2f} €",
                    f"{factura.total or 0:.2f} €"
                ])
            
            # Fila de totales del contrato
            table_data.append([
                '', '', '', 'TOTAL:',
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