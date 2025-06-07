# myapp/routes/reports.py
from flask import Blueprint, render_template, flash, redirect, url_for
from flask_login import login_required, current_user
from ..forms import CSRFOnlyForm

reports_bp = Blueprint('reports_bp', __name__, url_prefix='/reports')

@reports_bp.route('/')
@login_required
def index():
    """Página principal de informes y exportaciones."""
    csrf_form = CSRFOnlyForm()
    
    return render_template(
        'reports/index.html',
        title="Informes y Exportaciones", 
        csrf_form=csrf_form,
        propietarios=[],
        active_owner=None,
        preselected_owner_id=None
    )

@reports_bp.route('/test')
@login_required
def test():
    """Página de prueba simple."""
    return f"<h1>¡Funciona!</h1><p>Usuario: {current_user.username}</p><p>Rol: {current_user.role}</p>"