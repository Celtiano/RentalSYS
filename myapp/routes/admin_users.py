# myapp/routes/admin_users.py
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from ..models import db, User, Propietario
from ..forms import UserCreateForm, UserEditForm
from ..decorators import role_required # Importar decorador de rol

admin_users_bp = Blueprint('admin_users_bp', __name__)

# Proteger TODO este blueprint para que solo accedan administradores
@admin_users_bp.before_request
@login_required
@role_required('admin')
def before_request():
    """Protege todas las rutas de este blueprint para admins."""
    pass

@admin_users_bp.route('/')
def list_users():
    """Muestra la lista de todos los usuarios."""
    try:
        users = User.query.order_by(User.username).all()
    except Exception as e:
        flash(f"Error al cargar usuarios: {e}", "danger")
        users = []
    return render_template('admin_users/list_users.html', title="Gestionar Usuarios", users=users)

@admin_users_bp.route('/create', methods=['GET', 'POST'])
def create_user():
    """Crea un nuevo usuario."""
    form = UserCreateForm()
    if form.validate_on_submit():
        try:
            new_user = User(
                username=form.username.data,
                email=form.email.data,
                role=form.role.data,
                is_active=form.is_active.data
            )
            new_user.set_password(form.password.data)

            # Asignar propietarios seleccionados
            selected_propietarios = Propietario.query.filter(Propietario.id.in_(form.propietarios.data)).all()
            new_user.propietarios_asignados = selected_propietarios

            db.session.add(new_user)
            db.session.commit()
            flash(f"Usuario '{new_user.username}' creado exitosamente.", "success")
            return redirect(url_for('admin_users_bp.list_users'))
        except Exception as e:
            db.session.rollback()
            flash(f"Error al crear usuario: {e}", "danger")
    else:
        # Mostrar errores de validación si es POST y falla
        if request.method == 'POST':
            for field, errors in form.errors.items():
                for error in errors:
                    flash(f"Error en {getattr(form, field).label.text}: {error}", 'danger')

    return render_template('admin_users/create_user.html', title="Crear Usuario", form=form)

@admin_users_bp.route('/edit/<int:id>', methods=['GET', 'POST'])
def edit_user(id):
    """Edita un usuario existente."""
    user = db.session.get(User, id)
    if not user:
        flash("Usuario no encontrado.", "warning")
        return redirect(url_for('admin_users_bp.list_users'))

    # Pasar username/email originales para validación unique
    form = UserEditForm(original_username=user.username, original_email=user.email, obj=user)

    if form.validate_on_submit():
        try:
            user.username = form.username.data
            user.email = form.email.data
            user.role = form.role.data
            user.is_active = form.is_active.data

            # Actualizar contraseña solo si se proporcionó una nueva
            if form.password.data:
                user.set_password(form.password.data)

            # Actualizar propietarios asignados
            selected_propietarios = Propietario.query.filter(Propietario.id.in_(form.propietarios.data)).all()
            user.propietarios_asignados = selected_propietarios

            db.session.commit()
            flash(f"Usuario '{user.username}' actualizado exitosamente.", "success")
            return redirect(url_for('admin_users_bp.list_users'))
        except Exception as e:
            db.session.rollback()
            flash(f"Error al actualizar usuario: {e}", "danger")
    else:
        # Mostrar errores de validación si es POST y falla
         if request.method == 'POST':
            for field, errors in form.errors.items():
                for error in errors:
                    flash(f"Error en {getattr(form, field).label.text}: {error}", 'danger')

    # Si es GET, cargar los propietarios asignados actuales en el formulario
    if request.method == 'GET':
        form.propietarios.data = [p.id for p in user.propietarios_asignados]

    return render_template('admin_users/edit_user.html', title="Editar Usuario", form=form, user=user)


@admin_users_bp.route('/delete/<int:id>', methods=['POST'])
def delete_user(id):
    """Elimina un usuario (excepto a sí mismo)."""
    if current_user.id == id:
        flash("No puedes eliminar tu propia cuenta.", "danger")
        return redirect(url_for('admin_users_bp.list_users'))

    user = db.session.get(User, id)
    if user:
        try:
            username = user.username
            # Desvincular relaciones (SQLAlchemy podría hacerlo con cascade si está configurado)
            user.propietarios_asignados = []
            db.session.delete(user)
            db.session.commit()
            flash(f"Usuario '{username}' eliminado.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Error al eliminar usuario: {e}", "danger")
    else:
        flash("Usuario no encontrado.", "warning")

    return redirect(url_for('admin_users_bp.list_users'))