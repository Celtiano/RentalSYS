# myapp/routes/auth.py
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash
from ..models import db, User
from ..forms import LoginForm # Crear LoginForm en forms.py

auth_bp = Blueprint('auth_bp', __name__)

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main_bp.dashboard')) # Redirigir si ya está logueado

    form = LoginForm() # Usar Flask-WTF para el form de login
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        # Verificar usuario y contraseña
        if user and user.check_password(form.password.data) and user.is_active:
            login_user(user, remember=form.remember_me.data) # Loguear al usuario
            flash('Inicio de sesión exitoso.', 'success')
            # Redirigir a la página solicitada originalmente o al dashboard
            next_page = request.args.get('next')
            return redirect(next_page or url_for('main_bp.dashboard'))
        else:
            flash('Usuario o contraseña incorrectos, o cuenta inactiva.', 'danger')

    return render_template('login.html', title='Iniciar Sesión', form=form)

@auth_bp.route('/logout')
@login_required # Solo usuarios logueados pueden desloguearse
def logout():
    logout_user()
    flash('Has cerrado sesión.', 'info')
    return redirect(url_for('auth_bp.login'))

# Podrías añadir rutas para registro, recuperación de contraseña, etc. aquí