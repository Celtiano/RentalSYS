# myapp/routes/reports_simple.py - Versión ultra-simplificada para emergency fallback

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from datetime import datetime
from decimal import Decimal
from ..decorators import role_required
from ..models import db, Factura, Contrato, Propiedad, Propietario
from ..forms import CSRFOnlyForm

def listado_facturacion_simple():
    """Versión ultra-simplificada del listado de facturación."""
    try:
        current_app.logger.info("=== INICIO LISTADO SIMPLE ===")
        
        # Obtener parámetros
        propietario_id = request.form.get('propietario_id')
        fecha_desde = request.form.get('fecha_desde')
        fecha_hasta = request.form.get('fecha_hasta')
        
        current_app.logger.info(f"Params: propietario={propietario_id}, desde={fecha_desde}, hasta={fecha_hasta}")
        
        if not all([propietario_id, fecha_desde, fecha_hasta]):
            flash("Faltan parámetros obligatorios", "error")
            return redirect(url_for('reports_bp.index'))
        
        # Convertir parámetros
        propietario_id = int(propietario_id)
        fecha_desde_dt = datetime.strptime(fecha_desde, '%Y-%m-%d').date()
        fecha_hasta_dt = datetime.strptime(fecha_hasta, '%Y-%m-%d').date()
        
        # Obtener propietario
        propietario = Propietario.query.get(propietario_id)
        if not propietario:
            flash("Propietario no encontrado", "error")
            return redirect(url_for('reports_bp.index'))
        
        current_app.logger.info(f"Propietario encontrado: {propietario.nombre}")
        
        # Consulta ULTRA-SIMPLE
        facturas = db.session.query(Factura).filter(
            Factura.fecha_emision >= fecha_desde_dt,
            Factura.fecha_emision <= fecha_hasta_dt,
            Factura.propiedad_id.in_(
                db.session.query(Propiedad.id).filter(
                    Propiedad.propietario_id == propietario_id
                )
            )
        ).all()
        
        current_app.logger.info(f"Facturas encontradas: {len(facturas)}")
        
        if not facturas:
            flash("No se encontraron facturas en el período especificado", "warning")
            return redirect(url_for('reports_bp.index'))
        
        # Calcular totales simples
        total_subtotal = sum(f.subtotal or 0 for f in facturas)
        total_iva = sum(f.iva or 0 for f in facturas)
        total_irpf = sum(f.irpf or 0 for f in facturas)
        total_general = sum(f.total or 0 for f in facturas)
        
        # Datos para template simplificado
        datos = {
            'propietario': propietario,
            'facturas': facturas,
            'fecha_desde': fecha_desde_dt,
            'fecha_hasta': fecha_hasta_dt,
            'totales': {
                'base_imponible': total_subtotal,
                'importe_iva': total_iva,
                'importe_irpf': total_irpf,
                'total': total_general
            },
            'total_facturas': len(facturas)
        }
        
        current_app.logger.info(f"Totales: {datos['totales']}")
        
        # Usar template simple
        return render_template(
            'reports/listado_simple.html',
            **datos,
            csrf_form=CSRFOnlyForm()
        )
        
    except Exception as e:
        current_app.logger.error(f"ERROR en listado simple: {e}", exc_info=True)
        flash(f"Error: {e}", "error")
        return redirect(url_for('reports_bp.index'))
