# myapp/routes/propietarios.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, abort
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import selectinload
# --- IMPORTS AUTH ---
from flask_login import login_required, current_user
from ..decorators import role_required, owner_access_required # Importar decoradores
# --------------------
from ..models import db, Propietario, Propiedad # Importar Propiedad para la comprobación de borrado
from ..forms import PropietarioForm, CSRFOnlyForm # Importar formulario
from ..utils.owner_session import (
    set_active_owner, 
    get_active_owner, 
    get_user_available_owners,
    user_has_access_to_owner
)

propietarios_bp = Blueprint('propietarios_bp', __name__)

# --- Proteger todas las rutas de este blueprint ---
@propietarios_bp.before_request
@login_required # Primero login
def propietarios_before_request(): # Renombrar para unicidad si es necesario
    """Protege el blueprint y verifica acceso general."""
    # Si 'usuario' no debe acceder a NADA de propietarios, podrías añadir:
    # if current_user.role == 'usuario':
    #     flash("No tienes permiso para acceder a la gestión de propietarios.", "warning")
    #     return redirect(url_for('main_bp.dashboard'))
    pass

# --- Listar Propietarios (con filtrado por rol) + Selector de Propietario Activo ---
@propietarios_bp.route('/', methods=['GET', 'POST'])
def listar_propietarios():
    # Manejar selección de propietario activo
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'select_owner':
            propietario_id = request.form.get('propietario_id', type=int)
            if propietario_id:
                # Verificar que el usuario tiene acceso a este propietario
                if user_has_access_to_owner(propietario_id):
                    if set_active_owner(propietario_id):
                        propietario = Propietario.query.get(propietario_id)
                        flash(f"Propietario {propietario.nombre} seleccionado como activo.", "success")
                        # Redirigir al dashboard o donde corresponda
                        return redirect(url_for('main_bp.dashboard'))
                    else:
                        flash("Error al establecer el propietario activo.", "danger")
                else:
                    flash("No tienes acceso a este propietario.", "danger")
            else:
                flash("ID de propietario inválido.", "danger")
            
            # Redirigir de vuelta a la página de propietarios
            return redirect(url_for('propietarios_bp.listar_propietarios'))
    
    propietarios_list = []
    # Formulario para el modal de CREACIÓN.
    # El prefijo ayuda si se renderizan múltiples formularios o para validación específica.
    form_create = PropietarioForm(prefix="create_")
    # Formulario solo CSRF para modales que no usan un WTForm completo (ej. delete)
    # o para el modal de edición si se rellena solo con JS.
    csrf_form_general = CSRFOnlyForm()

    try:
        query = db.session.query(Propietario).options(selectinload(Propietario.propiedades))

        if current_user.role == 'admin':
            propietarios_list = query.order_by(Propietario.nombre).all()
        elif current_user.role in ['gestor', 'usuario']: # Gestor y Usuario ven solo los suyos
            assigned_owner_ids = [p.id for p in current_user.propietarios_asignados]
            if assigned_owner_ids:
                query = query.filter(Propietario.id.in_(assigned_owner_ids))
                propietarios_list = query.order_by(Propietario.nombre).all()
            else:
                # Si no hay propietarios asignados, no mostrar ninguno
                propietarios_list = [] # Lista vacía
        else: # Rol desconocido o sin acceso configurado
             propietarios_list = []


    except OperationalError as oe:
        flash('Error de base de datos al cargar propietarios. Verifica la conexión y las tablas.', 'danger')
        current_app.logger.error(f"Error BD (OperationalError) cargando propietarios: {oe}", exc_info=True)
    except Exception as e:
        flash(f'Error inesperado cargando propietarios: {e}', 'danger')
        current_app.logger.error(f"Error general cargando propietarios: {e}", exc_info=True)
        propietarios_list = []

    # Obtener información del propietario activo
    active_owner = get_active_owner()
    
    return render_template('propietarios.html',
                           title='Propietarios',
                           propietarios=propietarios_list,
                           form=form_create, # Para el modal de creación
                           csrf_form=csrf_form_general, # Para otros modales (delete, y edit si no usa WTForm)
                           active_owner=active_owner,
                           has_active_owner=active_owner is not None)


# --- Añadir Propietario (Con lógica especial para gestor) ---
@propietarios_bp.route('/add', methods=['POST'])
@role_required('admin', 'gestor')
def add_propietario():
    # Usar el prefijo "create_" para que los IDs de campo coincidan con el modal de creación
    form = PropietarioForm(request.form, prefix="create_")
    
    if form.validate_on_submit():
        try:
            new_owner = Propietario()
            # populate_obj asignará todos los campos del form que coincidan con atributos del modelo
            form.populate_obj(new_owner) 
            db.session.add(new_owner)

            # Si el usuario es un 'gestor', se le asigna automáticamente el propietario que crea
            if current_user.role == 'gestor':
                current_user.propietarios_asignados.append(new_owner)
                db.session.add(current_user) # Marcar current_user como modificado para la sesión
                current_app.logger.info(f"Gestor {current_user.username} creó y se auto-asignó propietario {new_owner.nombre}")

            db.session.commit()
            flash(f'Propietario "{new_owner.nombre}" añadido correctamente.', 'success')
            return redirect(url_for('propietarios_bp.listar_propietarios'))
        except IntegrityError as e:
            db.session.rollback()
            # WTForms debería manejar errores de validación, pero los de BD (unique) se capturan aquí.
            if "propietario.nif" in str(e.orig).lower():
                form.nif.errors.append("Este NIF ya está registrado.")
            elif hasattr(e.orig, 'message') and "propietario.email" in str(e.orig.message).lower(): # Si el email es unique en BD
                form.email.errors.append("Este email ya está registrado.")
            else:
                 current_app.logger.error(f"IntegrityError no manejado específicamente al añadir propietario: {e.orig}", exc_info=True)
                 flash('Error de base de datos al añadir (posible valor duplicado).', 'danger')
            # No redirigir, se re-renderizará abajo con el form con errores
        except Exception as e:
            db.session.rollback()
            flash(f'Error inesperado al añadir propietario: {e}', 'danger')
            current_app.logger.error(f"Error añadiendo propietario: {e}", exc_info=True)
            # No redirigir, se re-renderizará abajo

    # Si la validación falla (form.validate_on_submit() es False) o hay otro error no manejado arriba,
    # necesitamos recargar la lista de propietarios y re-renderizar el template.
    # El 'form' que se pasa aquí es el form de creación (con prefijo create_) CON SUS ERRORES.
    propietarios_list_display = []
    try:
        query = db.session.query(Propietario).options(selectinload(Propietario.propiedades))
        if current_user.role != 'admin': # Re-aplicar filtro de rol para el template
            assigned_owner_ids = [p.id for p in current_user.propietarios_asignados]
            if assigned_owner_ids: query = query.filter(Propietario.id.in_(assigned_owner_ids))
            else: query = query.filter(Propietario.id == -1) # No mostrar nada si no hay asignados
        propietarios_list_display = query.order_by(Propietario.nombre).all()
    except Exception as e_load:
        flash(f"Error cargando lista propietarios tras fallo de guardado: {e_load}", "warning")
        current_app.logger.error(f"Error cargando lista propietarios tras fallo de guardado: {e_load}", exc_info=True)

    csrf_form_general = CSRFOnlyForm()
    return render_template('propietarios.html',
                           title='Propietarios',
                           propietarios=propietarios_list_display,
                           form=form, # Este es el PropietarioForm de CREACIÓN (con prefijo) CON ERRORES
                           csrf_form=csrf_form_general,
                           open_create_modal_with_errors=True if form.errors else False) # Flag para JS


@propietarios_bp.route('/edit/<int:id>', methods=['POST'])
@owner_access_required() # Verifica acceso al propietario_id que se está editando
def edit_propietario(id):
    owner = db.session.get(Propietario, id)
    if not owner:
        flash("Propietario no encontrado.", "warning")
        return redirect(url_for('propietarios_bp.listar_propietarios'))

    # Instanciar el formulario:
    # - request.form: para obtener los datos enviados por el usuario.
    # - obj=owner: para poblar el formulario con los datos existentes del propietario.
    # - original_obj=owner: para que tus validadores de unicidad (ej. validate_nif) puedan comparar.
    # - prefix="edit_": Si los names de tus inputs HTML en el modal de edición tienen este prefijo.
    form_edit = PropietarioForm(request.form, obj=owner, original_obj=owner, prefix="edit_")

    if form_edit.validate_on_submit(): # Implica POST y datos válidos
        try:
            # populate_obj actualiza el objeto SQLAlchemy `owner` con los datos validados del `form_edit`.
            # Esto incluye 'documentos_ruta_base' si el mapeo de nombres (con prefijo) es correcto.
            form_edit.populate_obj(owner)
            
            # Opcional: Si un string vacío para documentos_ruta_base debe ser NULL en la BD
            if owner.documentos_ruta_base == "":
                owner.documentos_ruta_base = None

            if db.session.is_modified(owner):
                current_app.logger.info(f"EDIT_PROP ID {id}: Propietario modificado. Ruta base ahora: '{owner.documentos_ruta_base}'")
                db.session.commit()
                flash(f'Propietario "{owner.nombre}" actualizado correctamente.', 'success')
            else:
                flash('No se detectaron cambios para guardar.', 'info')
            
            return redirect(url_for('propietarios_bp.listar_propietarios'))

        except IntegrityError as e:
            db.session.rollback()
            current_app.logger.error(f"IntegrityError al editar Propietario ID {id}: {e.orig if hasattr(e, 'orig') else e}", exc_info=True)
            if hasattr(e, 'orig') and "propietario.nif" in str(e.orig).lower():
                flash("Error: El NIF introducido ya existe para otro propietario.", 'danger')
            # Añadir más comprobaciones de error de integridad si es necesario (ej. email único)
            else:
                flash('Error de base de datos al guardar los cambios (posible valor duplicado).', 'danger')
            # Idealmente, re-renderizar con el form con errores, pero redirigir es más simple por ahora.
            return redirect(url_for('propietarios_bp.listar_propietarios')) 
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Excepción general al editar Propietario ID {id}: {e}", exc_info=True)
            flash(f'Error inesperado al actualizar el propietario: {e}', 'danger')
            return redirect(url_for('propietarios_bp.listar_propietarios'))
    else:
        # La validación del formulario falló. WTForms añade los errores a form_edit.errors.
        current_app.logger.warning(f"Falló la validación del formulario al editar Propietario ID {id}. Errores: {form_edit.errors}")
        for fieldName, errorMessages in form_edit.errors.items():
            for err in errorMessages:
                field_label_obj = getattr(form_edit, fieldName, None)
                field_label_text = field_label_obj.label.text if hasattr(field_label_obj, 'label') else fieldName.replace('_', ' ').capitalize()
                flash(f"Error en el campo '{field_label_text}': {err}", "danger")
        
        return redirect(url_for('propietarios_bp.listar_propietarios'))


@propietarios_bp.route('/delete/<int:id>', methods=['POST'])
@owner_access_required() 
@role_required('admin', 'gestor') 
def delete_propietario(id):
    owner = db.session.get(Propietario, id)
    if owner:
        try:
            if db.session.query(Propiedad.id).filter_by(propietario_id=id).first():
                flash(f'No se puede eliminar "{owner.nombre}" porque tiene propiedades asociadas. Elimina primero sus propiedades.', 'warning')
                return redirect(url_for('propietarios_bp.listar_propietarios'))

            owner_name = owner.nombre
            # Desvincular de usuarios asignados (si es necesario y no hay cascade delete en la relación)
            if hasattr(owner, 'usuarios_asignados'):
                 owner.usuarios_asignados = [] # Vaciar la lista de la relación muchos-a-muchos

            db.session.delete(owner)
            db.session.commit()
            flash(f'Propietario "{owner_name}" eliminado correctamente.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error al eliminar propietario: {e}', 'danger')
            current_app.logger.error(f"Error eliminando propietario {id}: {e}", exc_info=True)
    else:
        flash('Propietario no encontrado.', 'warning')

    return redirect(url_for('propietarios_bp.listar_propietarios'))
    