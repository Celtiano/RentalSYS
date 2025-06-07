# myapp/decorators.py
from functools import wraps
from flask import abort, flash, redirect, url_for, request, jsonify, g, current_app
from flask_login import current_user
from .models import db, Propiedad, Contrato, Factura, Gasto, Propietario, User # Importar modelos necesarios
from .utils.owner_session import has_active_owner, auto_select_owner_if_needed, get_active_owner, get_active_owner_context
from .utils.database_helpers import OwnerFilteredQueries

def role_required(*roles):
    """Decorador para requerir uno o más roles."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                # Aunque @login_required debería encargarse, es una doble verificación.
                flash("Debes iniciar sesión para acceder a esta página.", "warning")
                return redirect(url_for('auth_bp.login', next=request.url))

            # Manejar tanto listas como argumentos separados
            allowed_roles = roles[0] if len(roles) == 1 and isinstance(roles[0], (list, tuple)) else roles
            
            if current_user.role not in allowed_roles:
                print(f"Acceso denegado a {current_user.username}. Rol requerido: {allowed_roles}, Rol actual: {current_user.role}")
                flash("No tienes permiso para acceder a esta página.", "danger")
                # Redirigir al dashboard o a donde sea apropiado
                return redirect(url_for('main_bp.dashboard'))
                # Alternativa: abort(403) # Forbidden

            return f(*args, **kwargs)
        return decorated_function
    return decorator

def owner_access_required(check_creation=False):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401) # No debería llegar aquí si se usa @login_required antes

            # Admin siempre tiene acceso
            if current_user.role == 'admin':
                return f(*args, **kwargs)

            # Permiso para CREAR (admin ya pasó, gestor puede crear)
            if check_creation:
                if current_user.role == 'gestor':
                    return f(*args, **kwargs)
                else: # Usuario no puede crear (en este ejemplo)
                    flash("No tienes permiso para crear este recurso.", "danger")
                    return redirect(request.referrer or url_for('main_bp.dashboard'))

            # Permiso para LEER/EDITAR/BORRAR
            target_propietario_id = None
            resource_id = kwargs.get('id')
            endpoint = request.endpoint

            if not resource_id: # No hay ID en la URL, no se puede verificar acceso específico
                 print(f"WARN @owner_access_required: No se encontró ID en kwargs para {endpoint}")
                 abort(404) # O redirigir con error

            try:
                # Determinar propietario_id según el blueprint/recurso
                if endpoint.startswith('propietarios_bp.'):
                    target_propietario_id = resource_id
                elif endpoint.startswith('propiedades_bp.'):
                    prop = db.session.get(Propiedad, resource_id)
                    target_propietario_id = prop.propietario_id if prop else None
                elif endpoint.startswith('contratos_bp.'):
                     # --- LÓGICA PARA CONTRATOS ---
                     # Necesitamos buscar el contrato y luego su propiedad->propietario
                     contrato = db.session.get(Contrato, resource_id)
                     if contrato and contrato.propiedad_ref:
                         target_propietario_id = contrato.propiedad_ref.propietario_id
                     else: # Contrato no encontrado o sin propiedad asociada
                          target_propietario_id = None
                     # --- FIN LÓGICA CONTRATOS ---
                elif endpoint.startswith('facturas_bp.'):
                    if 'gastos' in endpoint:
                        gasto = db.session.get(Gasto, resource_id)
                        if gasto and gasto.contrato and gasto.contrato.propiedad_ref:
                            target_propietario_id = gasto.contrato.propiedad_ref.propietario_id
                        else: target_propietario_id = None
                    else: # Factura
                        factura = db.session.get(Factura, resource_id)
                        if factura and factura.propiedad_ref:
                            target_propietario_id = factura.propiedad_ref.propietario_id
                        else: target_propietario_id = None
                # Añadir más 'elif' para otros blueprints si es necesario

            except Exception as e:
                 print(f"ERROR @owner_access_required: buscando recurso {endpoint} ID {resource_id}: {e}")
                 flash("Error al verificar permisos del recurso.", "danger")
                 abort(500) # Error interno


            if target_propietario_id is None:
                print(f"WARN @owner_access_required: No se pudo determinar propietario para {endpoint} ID {resource_id}")
                flash("Recurso no encontrado o no se pudo determinar el propietario.", "warning")
                abort(404) # O redirigir

            # Comprobar si el usuario (gestor/usuario) tiene acceso a ESE propietario
            assigned_owner_ids = {p.id for p in current_user.propietarios_asignados}
            if target_propietario_id not in assigned_owner_ids:
                print(f"Acceso denegado para {current_user.username} (Rol: {current_user.role}) al propietario ID: {target_propietario_id}. Asignados: {assigned_owner_ids}")
                flash("No tienes permiso para acceder o modificar este recurso específico.", "danger")
                # Intentar redirigir a la lista correspondiente
                try:
                    parts = endpoint.split('.')
                    list_route = f"{parts[0]}.listar_{parts[1].split('_')[0]}" # Intentar construir nombre de ruta de listado
                    return redirect(url_for(list_route))
                except:
                    return redirect(url_for('main_bp.dashboard')) # Fallback

            # Si todo OK, ejecutar la función de la vista
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def active_owner_required(auto_select=True, redirect_to_selector=True):
    """
    Decorador que verifica que hay un propietario activo en la sesión.
    
    Args:
        auto_select (bool): Si True, intenta seleccionar automáticamente un propietario
                           si el usuario tiene acceso a exactamente uno
        redirect_to_selector (bool): Si True, redirige al selector de propietario
                                   cuando no hay uno activo. Si False, devuelve error 400
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                # No debería llegar aquí si se usa @login_required antes
                abort(401)
            
            # Los administradores pueden necesitar propietario activo según el contexto
            # No hacemos excepción automática para admin como en owner_access_required
            
            # Intentar selección automática si está habilitada
            if auto_select:
                auto_select_owner_if_needed()
            
            # Verificar si hay propietario activo
            if not has_active_owner():
                # Determinar si es una petición AJAX
                is_ajax = (
                    request.is_json or 
                    request.headers.get('X-Requested-With') == 'XMLHttpRequest' or
                    'application/json' in request.headers.get('Accept', '')
                )
                
                if is_ajax:
                    # Para peticiones AJAX, devolver JSON
                    return jsonify({
                        'error': 'no_active_owner',
                        'message': 'No hay un propietario activo seleccionado.',
                        'redirect_url': url_for('owner_selector_bp.select_owner') if redirect_to_selector else None
                    }), 400
                
                elif redirect_to_selector:
                    # Para peticiones normales, redirigir al selector
                    flash(
                        "Debes seleccionar un propietario antes de continuar.",
                        "warning"
                    )
                    return redirect(url_for('owner_selector_bp.select_owner', next=request.url))
                
                else:
                    # Devolver error 400 sin redirección
                    flash(
                        "No hay un propietario activo seleccionado.",
                        "danger"
                    )
                    abort(400)
            
            # Verificar que el usuario sigue teniendo acceso al propietario activo
            active_owner = get_active_owner()
            if not active_owner:
                # get_active_owner ya limpia la sesión si hay problemas
                if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify({
                        'error': 'invalid_active_owner',
                        'message': 'El propietario activo ya no es válido.',
                        'redirect_url': url_for('owner_selector_bp.select_owner') if redirect_to_selector else None
                    }), 400
                
                flash(
                    "El propietario seleccionado ya no es válido. Por favor, selecciona otro.",
                    "warning"
                )
                if redirect_to_selector:
                    return redirect(url_for('owner_selector_bp.select_owner', next=request.url))
                else:
                    abort(400)
            
            # Todo OK, ejecutar la función
            return f(*args, **kwargs)
        
        return decorated_function
    return decorator


def inject_active_owner_context():
    """
    Decorador que inyecta información del propietario activo en el contexto de la vista.
    Útil para vistas que necesitan mostrar información del propietario activo sin requerir que esté presente.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Importar aquí para evitar imports circulares
            from .utils.owner_session import get_active_owner_context
            
            # Ejecutar la función original
            result = f(*args, **kwargs)
            
            # Si el resultado es un template renderizado con contexto, inyectar información del propietario
            if hasattr(result, 'get_data'):
                # Es una respuesta de template
                try:
                    # Obtener contexto del propietario activo
                    owner_context = get_active_owner_context()
                    
                    # Esto es más complejo de implementar sin modificar Flask internamente
                    # Por ahora, retornamos el resultado original
                    # En una implementación real, esto requeriría modificar el contexto del template
                    pass
                except Exception as e:
                    # Si hay error, continuar sin inyectar contexto
                    current_app.logger.error(f"Error al inyectar contexto de propietario: {str(e)}")
            
            return result
        
        return decorated_function
    return decorator


def with_owner_filtering(require_active_owner=True, auto_select=True):
    """
    Decorador que configura el contexto de filtrado automático por propietario activo.
    
    Args:
        require_active_owner (bool): Si es obligatorio tener un propietario activo
        auto_select (bool): Si intentar selección automática de propietario
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            
            # Intentar selección automática si está habilitada
            if auto_select:
                auto_select_owner_if_needed()
            
            # Verificar propietario activo si es requerido
            if require_active_owner and not has_active_owner():
                # Determinar si es una petición AJAX
                is_ajax = (
                    request.is_json or 
                    request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                )
                
                if is_ajax:
                    return jsonify({
                        'error': 'no_active_owner',
                        'message': 'No hay un propietario activo seleccionado.',
                        'redirect_url': url_for('owner_selector_bp.select_owner')
                    }), 400
                else:
                    flash("Debes seleccionar un propietario antes de continuar.", "warning")
                    return redirect(url_for('owner_selector_bp.select_owner', next=request.url))
            
            # Configurar contexto de filtrado en g
            g.owner_filtering_enabled = True
            g.active_owner = get_active_owner()
            g.owner_context = get_active_owner_context()
            
            # Ejecutar función
            try:
                return f(*args, **kwargs)
            except Exception as e:
                current_app.logger.error(f"Error en vista con filtrado de propietario: {str(e)}")
                raise
        
        return decorated_function
    return decorator


def filtered_view(require_active_owner=True, auto_select=True, log_queries=False):
    """
    Decorador combinado que aplica verificación de propietario activo y configura filtrado.
    Es un alias conveniente que combina @active_owner_required y @with_owner_filtering.
    
    Args:
        require_active_owner (bool): Si es obligatorio tener un propietario activo
        auto_select (bool): Si intentar selección automática de propietario
        log_queries (bool): Si registrar las consultas filtradas en los logs
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            
            # Intentar selección automática si está habilitada
            if auto_select:
                auto_select_owner_if_needed()
            
            # Verificar propietario activo si es requerido
            if require_active_owner and not has_active_owner():
                is_ajax = (
                    request.is_json or 
                    request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                )
                
                if is_ajax:
                    return jsonify({
                        'error': 'no_active_owner',
                        'message': 'No hay un propietario activo seleccionado.',
                        'redirect_url': url_for('owner_selector_bp.select_owner')
                    }), 400
                else:
                    flash("Debes seleccionar un propietario antes de continuar.", "warning")
                    return redirect(url_for('owner_selector_bp.select_owner', next=request.url))
            
            # Configurar contexto de filtrado
            g.owner_filtering_enabled = True
            g.active_owner = get_active_owner()
            g.owner_context = get_active_owner_context()
            g.log_filtered_queries = log_queries
            
            if log_queries:
                current_app.logger.debug(
                    f"Vista {f.__name__} ejecutándose con propietario activo: "
                    f"{g.active_owner.id if g.active_owner else 'None'}"
                )
            
            # Ejecutar función
            try:
                result = f(*args, **kwargs)
                
                if log_queries:
                    current_app.logger.debug(f"Vista {f.__name__} completada exitosamente")
                
                return result
                
            except Exception as e:
                current_app.logger.error(
                    f"Error en vista filtrada {f.__name__}: {str(e)}", 
                    exc_info=True
                )
                raise
        
        return decorated_function
    return decorator


def validate_entity_access(entity_type, id_param='id'):
    """
    Decorador que valida el acceso a una entidad específica.
    
    Args:
        entity_type (str): Tipo de entidad ('propiedad', 'contrato', 'factura', etc.)
        id_param (str): Nombre del parámetro que contiene el ID de la entidad
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            
            # Obtener ID de la entidad
            entity_id = kwargs.get(id_param)
            if not entity_id:
                current_app.logger.warning(
                    f"ID de {entity_type} no encontrado en parámetros de {f.__name__}"
                )
                abort(404)
            
            try:
                entity_id = int(entity_id)
            except (ValueError, TypeError):
                abort(404)
            
            # Validar acceso usando las funciones de filtrado
            has_access = OwnerFilteredQueries.validate_access_to_entity(entity_type, entity_id)
            
            if not has_access:
                current_app.logger.warning(
                    f"Acceso denegado a {entity_type} {entity_id} para usuario {current_user.username}"
                )
                
                # Determinar si es AJAX
                is_ajax = (
                    request.is_json or 
                    request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                )
                
                if is_ajax:
                    return jsonify({
                        'error': 'access_denied',
                        'message': f'No tienes acceso a este {entity_type}.'
                    }), 403
                else:
                    flash(f"No tienes acceso a este {entity_type}.", "danger")
                    abort(403)
            
            # Configurar contexto
            g.validated_entity_type = entity_type
            g.validated_entity_id = entity_id
            
            return f(*args, **kwargs)
        
        return decorated_function
    return decorator


def inject_owner_stats():
    """
    Decorador que inyecta estadísticas del propietario activo en el contexto.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Obtener estadísticas solo si hay propietario activo
            if has_active_owner():
                try:
                    g.owner_stats = OwnerFilteredQueries.get_stats_for_active_owner()
                except Exception as e:
                    current_app.logger.error(f"Error obteniendo estadísticas: {str(e)}")
                    g.owner_stats = {}
            else:
                g.owner_stats = {}
            
            return f(*args, **kwargs)
        
        return decorated_function
    return decorator


# Combinación de decoradores más usados
def filtered_list_view(entity_type=None, log_queries=False):
    """
    Decorador combinado para vistas de listado con filtrado automático.
    
    Args:
        entity_type (str): Tipo de entidad para logging específico
        log_queries (bool): Si registrar las consultas en los logs
    """
    def decorator(f):
        # Aplicar múltiples decoradores
        decorated = with_owner_filtering(require_active_owner=True, auto_select=True)(f)
        decorated = inject_owner_stats()(decorated)
        
        if log_queries:
            @wraps(decorated)
            def logging_wrapper(*args, **kwargs):
                current_app.logger.debug(
                    f"Vista de listado {f.__name__} ({entity_type}) iniciada"
                )
                result = decorated(*args, **kwargs)
                current_app.logger.debug(
                    f"Vista de listado {f.__name__} ({entity_type}) completada"
                )
                return result
            return logging_wrapper
        
        return decorated
    
    return decorator


def filtered_detail_view(entity_type, id_param='id', log_queries=False):
    """
    Decorador combinado para vistas de detalle con validación de acceso.
    
    Args:
        entity_type (str): Tipo de entidad ('propiedad', 'contrato', etc.)
        id_param (str): Nombre del parámetro que contiene el ID
        log_queries (bool): Si registrar las consultas en los logs
    """
    def decorator(f):
        # Aplicar múltiples decoradores en orden
        decorated = with_owner_filtering(require_active_owner=True, auto_select=True)(f)
        decorated = validate_entity_access(entity_type, id_param)(decorated)
        
        if log_queries:
            @wraps(decorated)
            def logging_wrapper(*args, **kwargs):
                entity_id = kwargs.get(id_param)
                current_app.logger.debug(
                    f"Vista de detalle {f.__name__} ({entity_type} {entity_id}) iniciada"
                )
                result = decorated(*args, **kwargs)
                current_app.logger.debug(
                    f"Vista de detalle {f.__name__} ({entity_type} {entity_id}) completada"
                )
                return result
            return logging_wrapper
        
        return decorated
    
    return decorator