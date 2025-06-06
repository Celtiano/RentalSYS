from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app 
from sqlalchemy.exc import IntegrityError, OperationalError
from datetime import datetime

from ..models import db, Inquilino, Contrato, Factura

from flask_login import login_required, current_user
from ..forms import CSRFOnlyForm
from ..decorators import (
    role_required, 
    filtered_list_view, filtered_detail_view, with_owner_filtering, validate_entity_access
)
from ..utils.database_helpers import (
    get_filtered_inquilinos, get_filtered_contratos,
    OwnerFilteredQueries
)
from ..utils.owner_session import get_active_owner_context

inquilinos_bp = Blueprint('inquilinos_bp', __name__)

@inquilinos_bp.before_request
@login_required # Requiere login para CUALQUIER ruta de este blueprint
def before_request():
    """Protege todas las rutas del blueprint."""
    pass

@inquilinos_bp.route('/', methods=['GET'])
@filtered_list_view(entity_type='inquilino', log_queries=True)
def listar_inquilinos():
    """
    Lista inquilinos filtrados automáticamente por propietario activo.
    """
    csrf_form = CSRFOnlyForm()
    try:
        # Filtrado automático aplicado - solo inquilinos del propietario activo
        inquilinos_list = get_filtered_inquilinos().order_by(Inquilino.nombre).all()
    except OperationalError:
        flash('Error de base de datos al cargar inquilinos.', 'danger')
        inquilinos_list = []
    except Exception as e:
        flash(f'Error cargando inquilinos: {e}', 'danger')
        inquilinos_list = []
    return render_template('inquilinos.html',
                           title='Inquilinos',
                           inquilinos=inquilinos_list,
                           csrf_form=csrf_form)

@inquilinos_bp.route('/add', methods=['POST'])
@role_required('admin', 'gestor')
@with_owner_filtering(require_active_owner=False) 
def add_inquilino():
    """
    Añade un nuevo inquilino. Requiere campos en el form:
      - tenantName
      - tenantNIF
      - tenantEmail (opcional)
      - tenantAddress, tenantCP, tenantCity, tenantPhone, tenantStatus
      - tenantStartDate, tenantEndDate (opcionales)
    """
    if request.method == 'POST':
        nombre = request.form.get('tenantName')
        nif = request.form.get('tenantNIF')
        email = request.form.get('tenantEmail') or None

        if not nombre or not nif:
            flash('Nombre y NIF obligatorios.', 'warning')
            return redirect(url_for('inquilinos_bp.listar_inquilinos'))

        # Comprobar duplicados
        if db.session.query(Inquilino.id).filter_by(nif=nif).first():
            flash(f'NIF {nif} ya existe.', 'warning')
            return redirect(url_for('inquilinos_bp.listar_inquilinos'))
        if email and db.session.query(Inquilino.id).filter_by(email=email).first():
            flash(f'Email {email} ya existe.', 'warning')
            return redirect(url_for('inquilinos_bp.listar_inquilinos'))

        try:
            # Convertir fechas en objetos date
            start_str = request.form.get('tenantStartDate')
            end_str = request.form.get('tenantEndDate')
            start_date = datetime.strptime(start_str, '%Y-%m-%d').date() if start_str else None
            end_date = datetime.strptime(end_str, '%Y-%m-%d').date() if end_str else None

            new_tenant = Inquilino(
                nombre=nombre,
                nif=nif,
                email=email,
                direccion=request.form.get('tenantAddress'),
                codigo_postal=request.form.get('tenantCP'),
                ciudad=request.form.get('tenantCity'),
                telefono=request.form.get('tenantPhone'),
                estado=request.form.get('tenantStatus', 'activo'),
                fecha_inicio_relacion=start_date,
                fecha_fin_relacion=end_date
            )
            db.session.add(new_tenant)
            db.session.commit()
            flash('Inquilino añadido.', 'success')
        except ValueError:
            db.session.rollback()
            flash('Formato de fecha inválido para inicio/fin de relación.', 'danger')
        except IntegrityError as e:
            db.session.rollback()
            if "UNIQUE constraint failed: inquilino.nif" in str(e):
                flash('Error al añadir inquilino: NIF duplicado.', 'danger')
            elif "UNIQUE constraint failed: inquilino.email" in str(e):
                flash('Error al añadir inquilino: email duplicado.', 'danger')
            else:
                flash('Error al añadir inquilino: problema de base de datos.', 'danger')
        except Exception as e:
            db.session.rollback()
            flash(f'Error al añadir inquilino: {e}', 'danger')
            current_app.logger.error(f"Error añadiendo inquilino: {e}", exc_info=True)

    return redirect(url_for('inquilinos_bp.listar_inquilinos'))

@inquilinos_bp.route('/edit/<int:id>', methods=['POST'])
@role_required('admin', 'gestor')
@validate_entity_access('inquilino', 'id')
def edit_inquilino(id):
    """
    Edita un inquilino existente con validación automática de acceso.
    """
    # La validación de acceso ya se hizo automáticamente
    tenant = OwnerFilteredQueries.get_inquilino_by_id(id)
    if not tenant:
        flash('Inquilino no encontrado o sin acceso.', 'warning')
        return redirect(url_for('inquilinos_bp.listar_inquilinos'))

    if request.method == 'POST':
        new_nombre = request.form.get('editTenantNombre')
        new_nif = request.form.get('editTenantNIF')
        new_email = request.form.get('editTenantEmail') or None

        if not new_nombre or not new_nif:
            flash('Nombre y NIF obligatorios.', 'warning')
            return redirect(url_for('inquilinos_bp.listar_inquilinos'))

        # Comprobamos si el NIF o email ya existen para otro
        if not new_nombre or not new_nif:
            flash('Nombre y NIF obligatorios al editar.', 'warning')
            return redirect(url_for('inquilinos_bp.listar_inquilinos'))

        # Comprobar duplicados (NIF)
        if new_nif != tenant.nif and \
           db.session.query(Inquilino.id).filter(Inquilino.nif == new_nif, Inquilino.id != id).first():
            flash(f'El NIF "{new_nif}" ya existe para otro inquilino.', 'warning')
            return redirect(url_for('inquilinos_bp.listar_inquilinos'))

        if (new_email and new_email != tenant.email and
            db.session.query(Inquilino.id)
            .filter(Inquilino.email == new_email, Inquilino.id != id).first()):
            flash(f'Email {new_email} ya existe.', 'warning')
            return redirect(url_for('inquilinos_bp.listar_inquilinos'))

        try:
            tenant.nombre = new_nombre
            tenant.nif = new_nif
            tenant.email = new_email
            tenant.direccion = request.form.get('editTenantDireccion')
            tenant.codigo_postal = request.form.get('editTenantCP')
            tenant.ciudad = request.form.get('editTenantPoblacion')
            tenant.telefono = request.form.get('editTenantTelefono')
            db.session.commit()
            flash(f'Inquilino "{tenant.nombre}" actualizado correctamente.', 'success')
        except IntegrityError:
            db.session.rollback()
            flash('Error de base de datos al actualizar: posible valor duplicado.', 'danger')
        except Exception as e:
            db.session.rollback()
            flash(f'Error inesperado al actualizar inquilino: {e}', 'danger')
            current_app.logger.error(f"Error editando inquilino {id}: {e}", exc_info=True)

    return redirect(url_for('inquilinos_bp.listar_inquilinos'))

@inquilinos_bp.route('/delete/<int:id>', methods=['POST'])
@role_required('admin', 'gestor')
@validate_entity_access('inquilino', 'id')
def delete_inquilino(id):
    """Elimina un inquilino con validación automática de acceso."""
    # La validación de acceso ya se hizo automáticamente
    tenant = OwnerFilteredQueries.get_inquilino_by_id(id)
    if not tenant:
        flash('Inquilino no encontrado o sin acceso.', 'warning')
        return redirect(url_for('inquilinos_bp.listar_inquilinos'))

    # --- Verificación de Permiso (similar a editar) ---
    # if current_user.role == 'gestor': ... (lógica si es necesaria) ...
    # ---------------------------------------------------

    try:
        # Revisar si tiene contratos o facturas (usar .first() para eficiencia)
        if db.session.query(Contrato.id).filter_by(inquilino_id=id).first():
            flash(f'No se puede eliminar a "{tenant.nombre}" porque tiene contratos asociados.', 'warning')
        elif db.session.query(Factura.id).filter_by(inquilino_id=id).first():
            flash(f'No se puede eliminar a "{tenant.nombre}" porque tiene facturas asociadas.', 'warning')
        else:
            tenant_name = tenant.nombre
            db.session.delete(tenant)
            db.session.commit()
            flash(f'Inquilino "{tenant_name}" eliminado.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al eliminar inquilino: {e}', 'danger')
        # Usar current_app importado para el logger
        current_app.logger.error(f"Error eliminando inquilino {id}: {e}", exc_info=True)

    return redirect(url_for('inquilinos_bp.listar_inquilinos'))
