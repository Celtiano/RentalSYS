# myapp/routes/propiedades.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import joinedload
from sqlalchemy import or_ # Necesario para búsqueda

# --- IMPORTS AUTH/ROLES ---
from flask_login import login_required, current_user
from ..decorators import role_required, owner_access_required
# --------------------------
from ..models import db, Propiedad, Propietario, Contrato, Factura # Modelos necesarios
from ..forms import CSRFOnlyForm # Importar form CSRF

propiedades_bp = Blueprint('propiedades_bp', __name__)

# --- Proteger todas las rutas de este blueprint ---
@propiedades_bp.before_request
@login_required
def before_request_propiedades(): # Renombrar para evitar colisión con otros before_request
    pass

# --- Listar Propiedades (Filtrado por Rol/Propietario) ---
@propiedades_bp.route('/', methods=['GET'])
@login_required # Ya cubierto por before_request del blueprint
def listar_propiedades():
    propiedades_list_display = [] # Para la tabla
    propietarios_para_select = [] # Para los selects en los modales
    csrf_form = CSRFOnlyForm()

    try:
        # --- Filtrado y carga de Propiedades para la tabla ---
        query_propiedades = db.session.query(Propiedad).options(joinedload(Propiedad.propietario_ref))
        if current_user.role == 'admin':
            propiedades_list_display = query_propiedades.order_by(Propiedad.direccion).all()
            propietarios_para_select = Propietario.query.order_by(Propietario.nombre).all() # Admin ve todos para selects
        elif current_user.role == 'gestor':
            assigned_owner_ids = [p.id for p in current_user.propietarios_asignados]
            if assigned_owner_ids:
                query_propiedades = query_propiedades.filter(Propiedad.propietario_id.in_(assigned_owner_ids))
                propiedades_list_display = query_propiedades.order_by(Propiedad.direccion).all()
                # Para los selects, el gestor solo puede elegir entre sus propietarios asignados
                propietarios_para_select = Propietario.query.filter(Propietario.id.in_(assigned_owner_ids)).order_by(Propietario.nombre).all()
            else:
                propiedades_list_display = []
                propietarios_para_select = [] # No hay opciones si no tiene propietarios
        elif current_user.role == 'usuario':
            # Un usuario también ve propiedades solo de sus propietarios asignados
            assigned_owner_ids = [p.id for p in current_user.propietarios_asignados]
            if assigned_owner_ids:
                query_propiedades = query_propiedades.filter(Propiedad.propietario_id.in_(assigned_owner_ids))
                propiedades_list_display = query_propiedades.order_by(Propiedad.direccion).all()
            else:
                propiedades_list_display = []
            # Para los selects (si un 'usuario' pudiera crear/editar propiedades), serían solo sus propietarios
            propietarios_para_select = current_user.propietarios_asignados # o [] si no pueden crear/editar
        else: # Rol desconocido o sin acceso configurado
            propiedades_list_display = []
            propietarios_para_select = []


    except Exception as e: # Captura general
        flash(f'Error cargando datos de propiedades: {e}', 'danger')
        current_app.logger.error(f"Error inesperado cargando propiedades: {e}", exc_info=True)
        propiedades_list_display = []
        propietarios_para_select = []

    return render_template('propiedades.html',
                           title='Propiedades',
                           propiedades=propiedades_list_display,
                           # Esta variable 'propietarios' se usará en los modales para los <select>
                           propietarios=propietarios_para_select,
                           csrf_form=csrf_form)

# --- Añadir Propiedad (Permiso Admin/Gestor) ---
@propiedades_bp.route('/add', methods=['POST'])
@role_required('admin', 'gestor') # Solo admin y gestor pueden añadir
def add_propiedad():
    direccion = request.form.get('propertyAddress')
    owner_id_str = request.form.get('propertyOwner')
    numero_local_val = request.form.get('propertyNumeroLocal') or None
    superficie_str = request.form.get('propertySuperficie')
    ano_construccion_str = request.form.get('propertyAnoConstruccion')

    if not direccion or not owner_id_str:
        flash('Dirección y Propietario son obligatorios.', 'warning')
        return redirect(url_for('propiedades_bp.listar_propiedades'))

    try:
        owner_id = int(owner_id_str)
        if current_user.role == 'gestor':
            assigned_owner_ids = {p.id for p in current_user.propietarios_asignados}
            if owner_id not in assigned_owner_ids:
                flash("No tienes permiso para añadir propiedades a este propietario.", "danger")
                return redirect(url_for('propiedades_bp.listar_propiedades'))
        
        owner = db.session.get(Propietario, owner_id)
        if not owner:
            flash('Propietario seleccionado no encontrado.', 'warning')
            return redirect(url_for('propiedades_bp.listar_propiedades'))

        ref_catastral = request.form.get('propertyRefCatastral') or None
        
        # ========= INICIO CORRECCIÓN VALIDACIÓN UNICIDAD ADD =========
        current_app.logger.info(f"ADD_PROP: Intentando crear propiedad. RefCat Form: '{ref_catastral}', OwnerID Form: {owner_id}")
        if ref_catastral and ref_catastral.strip():
            existing_prop_for_owner = Propiedad.query.filter_by(
                referencia_catastral=ref_catastral.strip(),
                propietario_id=owner_id 
            ).first()
            if existing_prop_for_owner:
                flash(f'La Referencia Catastral "{ref_catastral.strip()}" ya existe para este propietario.', 'warning')
                return redirect(url_for('propiedades_bp.listar_propiedades'))
        # ========= FIN CORRECCIÓN VALIDACIÓN UNICIDAD ADD =========

        superficie_val = int(superficie_str) if superficie_str and superficie_str.isdigit() else None
        ano_construccion_val = int(ano_construccion_str) if ano_construccion_str and ano_construccion_str.isdigit() else None

        new_propiedad = Propiedad(
            direccion=direccion, propietario_id=owner_id,
            referencia_catastral=ref_catastral.strip() if ref_catastral else None,
            ciudad=request.form.get('propertyCity'),
            codigo_postal=request.form.get('propertyPostalCode'),
            tipo=request.form.get('propertyType'),
            descripcion=request.form.get('propertyDescription'),
            numero_local=numero_local_val,
            superficie_construida=superficie_val,
            ano_construccion=ano_construccion_val,
            estado_ocupacion=request.form.get('propertyStatus', 'vacia')
        )
        db.session.add(new_propiedad)
        db.session.commit()
        flash('Propiedad añadida correctamente.', 'success')

    except ValueError:
        flash('ID de propietario, superficie o año inválido.', 'danger')
    except IntegrityError as e: # Esto se activaría si la constraint de BD falla
        db.session.rollback()
        flash('Error: Referencia Catastral duplicada para este propietario (error de BD).', 'danger')
        current_app.logger.warning(f"IntegrityError al añadir propiedad: Ref.Cat. {ref_catastral} para prop. {owner_id} - {e.orig}")
    except Exception as e:
        db.session.rollback()
        flash(f'Error inesperado al añadir la propiedad: {e}', 'danger')
        current_app.logger.error(f"Error añadiendo propiedad: {e}", exc_info=True)

    return redirect(url_for('propiedades_bp.listar_propiedades'))


# --- Editar Propiedad (Permiso Admin/Gestor + Verificación Acceso Propietario) ---
@propiedades_bp.route('/edit/<int:id>', methods=['POST'])
@role_required('admin', 'gestor') # Rol general para editar
@owner_access_required()        # Verifica acceso al propietario actual de la propiedad
def edit_propiedad(id):
    prop = db.session.get(Propiedad, id)
    if not prop:
        flash('Propiedad no encontrada.', 'warning'); abort(404)

    new_dir = request.form.get('editPropertyAddress')
    new_owner_id_str = request.form.get('editPropertyOwner')
    new_numero_local = request.form.get('editPropertyNumeroLocal') or None
    new_superficie_str = request.form.get('editPropertySuperficie')
    new_ano_construccion_str = request.form.get('editPropertyAnoConstruccion')
    new_ref_catastral = request.form.get('editPropertyRefCatastral') or None # Obtenerla aquí
    
    if not new_dir or not new_owner_id_str:
        flash('Dirección y Propietario son obligatorios al editar.', 'warning')
        return redirect(url_for('propiedades_bp.listar_propiedades'))

    try:
        new_owner_id = int(new_owner_id_str)
        if current_user.role == 'gestor' and new_owner_id != prop.propietario_id:
            assigned_owner_ids = {p.id for p in current_user.propietarios_asignados}
            if new_owner_id not in assigned_owner_ids:
                flash("No tienes permiso para asignar esta propiedad a ese nuevo propietario.", "danger")
                return redirect(url_for('propiedades_bp.listar_propiedades'))
        
        owner = db.session.get(Propietario, new_owner_id)
        if not owner:
            flash('El nuevo propietario seleccionado no existe.', 'warning')
            return redirect(url_for('propiedades_bp.listar_propiedades'))

        # ========= INICIO CORRECCIÓN VALIDACIÓN UNICIDAD EDIT =========
        if new_ref_catastral and new_ref_catastral.strip():
            # Comprobar si la combinación (ref_catastral, propietario_id) ya existe para OTRA propiedad
            existing_prop_for_owner = Propiedad.query.filter(
                Propiedad.referencia_catastral == new_ref_catastral.strip(),
                Propiedad.propietario_id == new_owner_id,
                Propiedad.id != id  # Excluir la propiedad actual que se está editando
            ).first()
            if existing_prop_for_owner:
                flash(f'La Referencia Catastral "{new_ref_catastral.strip()}" ya existe para el propietario seleccionado en otra propiedad.', 'warning')
                return redirect(url_for('propiedades_bp.listar_propiedades'))
        # ========= FIN CORRECCIÓN VALIDACIÓN UNICIDAD EDIT =========

        new_superficie = int(new_superficie_str) if new_superficie_str and new_superficie_str.isdigit() else None
        new_ano_construccion = int(new_ano_construccion_str) if new_ano_construccion_str and new_ano_construccion_str.isdigit() else None

        prop.direccion = new_dir
        prop.propietario_id = new_owner_id
        prop.referencia_catastral = new_ref_catastral.strip() if new_ref_catastral else None
        prop.ciudad = request.form.get('editPropertyCity')
        prop.codigo_postal = request.form.get('editPropertyCP')
        prop.tipo = request.form.get('editPropertyType')
        prop.descripcion = request.form.get('editPropertyDescription')
        prop.numero_local = new_numero_local
        prop.superficie_construida = new_superficie
        prop.ano_construccion = new_ano_construccion
        prop.estado_ocupacion = request.form.get('editPropertyStatus', prop.estado_ocupacion)

        db.session.commit()
        flash(f'Propiedad "{prop.direccion}" actualizada correctamente.', 'success')

    except ValueError:
        db.session.rollback()
        flash('ID de propietario, superficie o año inválido.', 'danger')
    except IntegrityError as e:
        db.session.rollback()
        flash('Error: Referencia Catastral duplicada para este propietario (error de BD).', 'danger')
        current_app.logger.warning(f"IntegrityError al editar propiedad {id}: {e.orig}")
    except Exception as e:
        db.session.rollback()
        flash(f'Error inesperado al actualizar la propiedad: {e}', 'danger')
        current_app.logger.error(f"Error editando propiedad {id}: {e}", exc_info=True)
        
    return redirect(url_for('propiedades_bp.listar_propiedades'))


# --- Eliminar Propiedad (Permiso Admin/Gestor + Verificación Acceso Propietario) ---
@propiedades_bp.route('/delete/<int:id>', methods=['POST'])
@role_required('admin', 'gestor') # Rol general para eliminar
@owner_access_required()        # Verifica acceso al propietario de esta propiedad
def delete_propiedad(id):
    prop = db.session.get(Propiedad, id)
    if not prop:
        flash("Propiedad no encontrada.", "warning")
        return redirect(url_for('propiedades_bp.listar_propiedades'))

    try:
        # Comprobación explícita de dependencias (Contratos/Facturas)
        # Usar .first() es más eficiente que .count() > 0 si solo necesitas saber si existe al menos uno
        has_contracts = db.session.query(Contrato.id).filter_by(propiedad_id=id).first()
        has_invoices = db.session.query(Factura.id).filter_by(propiedad_id=id).first()

        if has_contracts or has_invoices:
            flash(f'No se puede eliminar la propiedad "{prop.direccion}" porque tiene contratos o facturas asociadas.', 'warning')
        else:
            prop_address = prop.direccion
            db.session.delete(prop)
            db.session.commit()
            flash(f'Propiedad "{prop_address}" eliminada correctamente.', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Error al eliminar la propiedad: {e}', 'danger')
        current_app.logger.error(f"Error eliminando propiedad {id}: {e}", exc_info=True)

    return redirect(url_for('propiedades_bp.listar_propiedades'))