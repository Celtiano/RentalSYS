# myapp/routes/reports.py
import csv
import io
from datetime import datetime
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, current_app, make_response
)
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload

from ..models import db, Factura, Propiedad, Propietario
from ..forms import CSRFOnlyForm # Para el token CSRF
from ..decorators import role_required

reports_bp = Blueprint('reports_bp', __name__)

@reports_bp.before_request
@login_required
def before_request():
    """Protege todas las rutas de este blueprint."""
    pass

@reports_bp.route('/facturas_csv', methods=['GET', 'POST'])
@role_required('admin', 'gestor') # Solo admin y gestor pueden exportar
def exportar_facturas_csv():
    csrf_form_instance = CSRFOnlyForm() # Crear instancia para pasarla al template

    if request.method == 'GET':
        propietarios_list = []
        # Lógica para poblar el selector de propietarios según el rol
        if current_user.role == 'admin':
            propietarios_list = Propietario.query.order_by(Propietario.nombre).all()
        elif current_user.role == 'gestor':
            # Asegurarse de que current_user.propietarios_asignados es una lista y no una query
            # y que los objetos Propietario están cargados y ordenados.
            propietarios_list = sorted(list(current_user.propietarios_asignados), key=lambda p: p.nombre)

        return render_template('reports/exportar_facturas_form.html',
                               title="Exportar Facturas a CSV",
                               propietarios=propietarios_list,
                               csrf_form=csrf_form_instance) # Pasar el formulario CSRF

    if request.method == 'POST':
        propietario_id_str = request.form.get('propietario_id')
        fecha_desde_str = request.form.get('fecha_desde')
        fecha_hasta_str = request.form.get('fecha_hasta')

        if not all([propietario_id_str, fecha_desde_str, fecha_hasta_str]):
            flash("Propietario y rango de fechas son obligatorios.", "warning")
            return redirect(url_for('reports_bp.exportar_facturas_csv'))

        try:
            propietario_id = int(propietario_id_str)
            fecha_desde = datetime.strptime(fecha_desde_str, '%Y-%m-%d').date()
            fecha_hasta = datetime.strptime(fecha_hasta_str, '%Y-%m-%d').date()

            if fecha_desde > fecha_hasta:
                flash("La fecha 'desde' no puede ser posterior a la fecha 'hasta'.", "warning")
                return redirect(url_for('reports_bp.exportar_facturas_csv'))

        except ValueError:
            flash("Formato de ID o fecha inválido.", "danger")
            return redirect(url_for('reports_bp.exportar_facturas_csv'))

        # Verificar permiso para el propietario seleccionado
        if current_user.role == 'gestor':
            assigned_owner_ids = {p.id for p in current_user.propietarios_asignados}
            if propietario_id not in assigned_owner_ids:
                flash("No tienes permiso para exportar facturas de este propietario.", "danger")
                return redirect(url_for('reports_bp.exportar_facturas_csv'))

        propietario_obj = db.session.get(Propietario, propietario_id)
        if not propietario_obj:
            flash("Propietario no encontrado.", "warning")
            return redirect(url_for('reports_bp.exportar_facturas_csv'))

        # Query para obtener las facturas
        facturas = Factura.query.join(Propiedad, Factura.propiedad_id == Propiedad.id)\
            .filter(Propiedad.propietario_id == propietario_id)\
            .filter(Factura.fecha_emision >= fecha_desde)\
            .filter(Factura.fecha_emision <= fecha_hasta)\
            .options(
                joinedload(Factura.inquilino_ref),
                joinedload(Factura.propiedad_ref) # Propietario ya está cargado a través de propiedad_obj
            ).order_by(Factura.fecha_emision.asc(), Factura.numero_factura.asc()).all()

        if not facturas:
            flash(f"No se encontraron facturas para {propietario_obj.nombre} en el rango de fechas especificado.", "info")
            return redirect(url_for('reports_bp.exportar_facturas_csv'))

        # Generar CSV
        output = io.StringIO()
        # Usar ; como delimitador y asegurar que los strings con ; se entrecomillen
        writer = csv.writer(output, delimiter=';', quotechar='"', quoting=csv.QUOTE_MINIMAL)

        headers = [
            "Fecha", "Numero", "NIF_Inquilino", "Nombre_Inquilino",
            "Base_Imponible", "IVA_Porc", "IVA_Cuota",
            "IRPF_Porc", "IRPF_Cuota", "Total", "Propiedad_Direccion", "Propiedad_Numero_Local", "Propiedad_Ref_Catastral"
        ]
        writer.writerow(headers)

        for f in facturas:
            # Formatear números con coma como decimal para CSV europeo
            base_imp_str = str(f.subtotal).replace('.', ',')
            iva_cuota_str = str(f.iva).replace('.', ',')
            irpf_cuota_str = str(f.irpf).replace('.', ',')
            total_str = str(f.total).replace('.', ',')

            iva_porc_str = str(round(f.iva_rate_applied * 100, 2) if f.iva_rate_applied is not None else 0.0).replace('.', ',')
            irpf_porc_str = str(round(f.irpf_rate_applied * 100, 2) if f.irpf_rate_applied is not None else 0.0).replace('.', ',')

            row_data = [
                f.fecha_emision.strftime('%d/%m/%Y') if f.fecha_emision else '',
                f.numero_factura,
                f.inquilino_ref.nif if f.inquilino_ref else '',
                f.inquilino_ref.nombre if f.inquilino_ref else '',
                base_imp_str,
                iva_porc_str,
                iva_cuota_str,
                irpf_porc_str,
                irpf_cuota_str,
                total_str,
                f.propiedad_ref.direccion if f.propiedad_ref else '',
                f.propiedad_ref.numero_local if f.propiedad_ref and f.propiedad_ref.numero_local else '',
                f.propiedad_ref.referencia_catastral if f.propiedad_ref and f.propiedad_ref.referencia_catastral else ''
            ]
            writer.writerow(row_data)

        output.seek(0)

        sanitized_owner_name = "".join(c if c.isalnum() else "_" for c in propietario_obj.nombre)
        filename = f"facturas_{sanitized_owner_name}_{fecha_desde_str}_a_{fecha_hasta_str}.csv"

        response_data = output.getvalue()
        # Asegurar BOM para UTF-8 para compatibilidad con Excel
        response_data_utf8_bom = b'\xef\xbb\xbf' + response_data.encode('utf-8')

        response = make_response(response_data_utf8_bom)
        response.headers["Content-Disposition"] = f"attachment; filename=\"{filename}\"" # Entrecomillar filename
        response.headers["Content-type"] = "text/csv; charset=utf-8"
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

        return response