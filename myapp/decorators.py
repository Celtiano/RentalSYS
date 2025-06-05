# myapp/decorators.py
from functools import wraps
from flask import abort, flash, redirect, url_for, request
from flask_login import current_user
from .models import db, Propiedad, Contrato, Factura, Gasto, Propietario, User # Importar modelos necesarios

def role_required(*roles):
    """Decorador para requerir uno o más roles."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                # Aunque @login_required debería encargarse, es una doble verificación.
                flash("Debes iniciar sesión para acceder a esta página.", "warning")
                return redirect(url_for('auth_bp.login', next=request.url))

            if current_user.role not in roles:
                print(f"Acceso denegado a {current_user.username}. Rol requerido: {roles}, Rol actual: {current_user.role}")
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