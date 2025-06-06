# myapp/routes/owner_selector.py
"""
Rutas para el selector de propietario activo.
Proporciona endpoints para:
- Mostrar página de selección de propietario
- API AJAX para cambio dinámico de propietario
- Obtener información del propietario activo
"""

from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, current_app
from flask_login import login_required, current_user

from ..models import Propietario, db
from ..decorators import role_required
from ..utils.owner_session import (
    set_active_owner, 
    get_active_owner, 
    clear_active_owner, 
    get_user_available_owners,
    get_active_owner_context,
    user_has_access_to_owner,
    auto_select_owner_if_needed,
    validate_session_integrity
)

# Crear el blueprint
owner_selector_bp = Blueprint('owner_selector_bp', __name__, url_prefix='/owner-selector')


@owner_selector_bp.route('/')
@owner_selector_bp.route('/select', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'gestor', 'usuario')
def select_owner():
    """
    Redirige a la página principal de propietarios que ahora funciona como selector.
    """
    # Preservar parámetros next si existen
    next_url = request.args.get('next')
    if next_url:
        return redirect(url_for('propietarios_bp.listar_propietarios', next=next_url))
    else:
        return redirect(url_for('propietarios_bp.listar_propietarios'))


@owner_selector_bp.route('/legacy', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'gestor', 'usuario')
def select_owner_legacy():
    """
    Página legacy para seleccionar el propietario activo.
    GET: Muestra la página de selección
    POST: Procesa la selección del propietario
    """
    try:
        # Validar integridad de la sesión
        validate_session_integrity()
        
        # Obtener propietarios disponibles para el usuario
        available_owners = get_user_available_owners()
        
        if not available_owners:
            flash(
                "No tienes propietarios asignados. Contacta con el administrador.",
                "warning"
            )
            return redirect(url_for('main_bp.dashboard'))
        
        # Si solo hay un propietario disponible, seleccionarlo automáticamente
        if len(available_owners) == 1:
            propietario = available_owners[0]
            if set_active_owner(propietario.id):
                flash(
                    f"Propietario {propietario.nombre} seleccionado automáticamente.",
                    "success"
                )
                # Redirigir a la URL original o al dashboard
                next_url = request.args.get('next') or url_for('main_bp.dashboard')
                return redirect(next_url)
        
        if request.method == 'POST':
            try:
                propietario_id = request.form.get('propietario_id', type=int)
                
                if not propietario_id:
                    flash("Debes seleccionar un propietario.", "danger")
                    return render_template(
                        'owner_selector/select_owner.html',
                        available_owners=available_owners,
                        active_owner=get_active_owner()
                    )
                
                # Verificar que el propietario está en la lista de disponibles
                owner_ids = {owner.id for owner in available_owners}
                if propietario_id not in owner_ids:
                    flash("El propietario seleccionado no está disponible.", "danger")
                    return render_template(
                        'owner_selector/select_owner.html',
                        available_owners=available_owners,
                        active_owner=get_active_owner()
                    )
                
                # Establecer el propietario activo
                if set_active_owner(propietario_id):
                    propietario = next(o for o in available_owners if o.id == propietario_id)
                    flash(f"Propietario {propietario.nombre} seleccionado correctamente.", "success")
                    
                    # Redirigir a la URL original o al dashboard
                    next_url = request.form.get('next') or request.args.get('next') or url_for('main_bp.dashboard')
                    return redirect(next_url)
                else:
                    flash("Error al seleccionar el propietario. Inténtalo de nuevo.", "danger")
                    
            except Exception as e:
                current_app.logger.error(f"Error al procesar selección de propietario: {str(e)}")
                flash("Error interno al seleccionar propietario.", "danger")
        
        # Mostrar página de selección (GET o POST con errores)
        return render_template(
            'owner_selector/select_owner.html',
            available_owners=available_owners,
            active_owner=get_active_owner(),
            next_url=request.args.get('next')
        )
        
    except Exception as e:
        current_app.logger.error(f"Error en select_owner: {str(e)}")
        flash("Error al cargar la página de selección de propietario.", "danger")
        return redirect(url_for('main_bp.dashboard'))


@owner_selector_bp.route('/api/change', methods=['POST'])
@login_required
@role_required('admin', 'gestor', 'usuario')
def api_change_owner():
    """
    API AJAX para cambiar el propietario activo.
    """
    try:
        # Obtener datos tanto de JSON como de formulario para mayor compatibilidad
        if request.is_json:
            data = request.get_json()
            propietario_id = data.get('propietario_id')
        else:
            # Datos de formulario (para compatibilidad con CSRF)
            propietario_id = request.form.get('propietario_id')
        
        if not propietario_id:
            return jsonify({
                'success': False,
                'error': 'missing_owner_id',
                'message': 'Se requiere propietario_id.'
            }), 400
        
        try:
            propietario_id = int(propietario_id)
        except (ValueError, TypeError):
            return jsonify({
                'success': False,
                'error': 'invalid_owner_id',
                'message': 'ID de propietario inválido.'
            }), 400
        
        # Verificar que el usuario tiene acceso al propietario
        if not user_has_access_to_owner(propietario_id):
            return jsonify({
                'success': False,
                'error': 'access_denied',
                'message': 'No tienes acceso a este propietario.'
            }), 403
        
        # Establecer el propietario activo
        if set_active_owner(propietario_id):
            # Obtener información del nuevo propietario activo
            propietario = get_active_owner()
            return jsonify({
                'success': True,
                'message': f'Propietario {propietario.nombre} seleccionado correctamente.',
                'active_owner': {
                    'id': propietario.id,
                    'nombre': propietario.nombre,
                    'nif': propietario.nif
                }
            })
        else:
            return jsonify({
                'success': False,
                'error': 'set_owner_failed',
                'message': 'Error al establecer el propietario activo.'
            }), 500
            
    except Exception as e:
        current_app.logger.error(f"Error en api_change_owner: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'internal_error',
            'message': 'Error interno del servidor.'
        }), 500


@owner_selector_bp.route('/api/current')
@login_required
@role_required('admin', 'gestor', 'usuario')
def api_get_current_owner():
    """
    API para obtener información del propietario activo.
    """
    try:
        context = get_active_owner_context()
        
        active_owner_data = None
        if context['active_owner']:
            owner = context['active_owner']
            active_owner_data = {
                'id': owner.id,
                'nombre': owner.nombre,
                'nif': owner.nif,
                'email': owner.email,
                'ciudad': owner.ciudad
            }
        
        available_owners_data = []
        for owner in context['available_owners']:
            available_owners_data.append({
                'id': owner.id,
                'nombre': owner.nombre,
                'nif': owner.nif
            })
        
        return jsonify({
            'success': True,
            'active_owner': active_owner_data,
            'available_owners': available_owners_data,
            'has_active_owner': context['has_active_owner'],
            'can_change_owner': context['can_change_owner']
        })
        
    except Exception as e:
        current_app.logger.error(f"Error en api_get_current_owner: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'internal_error',
            'message': 'Error al obtener información del propietario.'
        }), 500


@owner_selector_bp.route('/api/clear', methods=['POST'])
@login_required
@role_required('admin', 'gestor', 'usuario')
def api_clear_owner():
    """
    API para limpiar el propietario activo de la sesión.
    """
    try:
        clear_active_owner()
        return jsonify({
            'success': True,
            'message': 'Propietario activo limpiado de la sesión.'
        })
        
    except Exception as e:
        current_app.logger.error(f"Error en api_clear_owner: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'internal_error',
            'message': 'Error al limpiar propietario activo.'
        }), 500


@owner_selector_bp.route('/api/auto-select', methods=['POST'])
@login_required
@role_required('admin', 'gestor', 'usuario')
def api_auto_select_owner():
    """
    API para intentar selección automática de propietario.
    """
    try:
        if auto_select_owner_if_needed():
            active_owner = get_active_owner()
            return jsonify({
                'success': True,
                'message': f'Propietario {active_owner.nombre} seleccionado automáticamente.',
                'active_owner': {
                    'id': active_owner.id,
                    'nombre': active_owner.nombre,
                    'nif': active_owner.nif
                }
            })
        else:
            return jsonify({
                'success': False,
                'error': 'auto_select_failed',
                'message': 'No se pudo seleccionar automáticamente un propietario.'
            })
            
    except Exception as e:
        current_app.logger.error(f"Error en api_auto_select_owner: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'internal_error',
            'message': 'Error en selección automática.'
        }), 500


@owner_selector_bp.route('/widget')
@login_required
@role_required('admin', 'gestor', 'usuario')
def owner_widget():
    """
    Widget para mostrar/cambiar propietario activo (para incluir en otras páginas).
    """
    try:
        context = get_active_owner_context()
        return render_template(
            'owner_selector/widget.html',
            **context
        )
        
    except Exception as e:
        current_app.logger.error(f"Error en owner_widget: {str(e)}")
        return "Error al cargar widget de propietario", 500


# Context processor para inyectar información del propietario en todos los templates
@owner_selector_bp.app_context_processor
def inject_owner_context():
    """
    Inyecta información del propietario activo en todos los templates.
    """
    if current_user.is_authenticated:
        try:
            context = get_active_owner_context()
            return {
                'owner_context': context
            }
        except Exception as e:
            current_app.logger.error(f"Error al inyectar contexto de propietario: {str(e)}")
            return {}
    
    return {}
