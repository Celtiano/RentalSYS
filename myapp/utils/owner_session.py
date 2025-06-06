# myapp/utils/owner_session.py
"""
Funciones auxiliares para gestionar el propietario activo en la sesión del usuario.
Este módulo proporciona funcionalidades para:
- Establecer y obtener el propietario activo de la sesión
- Validar permisos de acceso a propietarios
- Limpiar sesión cuando sea necesario
"""

from flask import session, current_app
from flask_login import current_user
from sqlalchemy.orm import selectinload
from ..models import Propietario, Propiedad, Contrato, db


# Clave para almacenar el propietario activo en la sesión
ACTIVE_OWNER_SESSION_KEY = 'active_owner_id'


def set_active_owner(propietario_id):
    """
    Establece el propietario activo en la sesión del usuario.
    
    Args:
        propietario_id (int): ID del propietario a establecer como activo
        
    Returns:
        bool: True si se estableció correctamente, False en caso contrario
    """
    try:
        # Validar que el ID sea válido
        if not propietario_id or not isinstance(propietario_id, int):
            current_app.logger.warning(f"ID de propietario inválido: {propietario_id}")
            return False
            
        # Validar que el usuario tenga acceso al propietario
        if not user_has_access_to_owner(propietario_id):
            current_app.logger.warning(
                f"Usuario {current_user.username} intenta acceder a propietario {propietario_id} sin permisos"
            )
            return False
            
        # Validar que el propietario existe
        propietario = db.session.get(Propietario, propietario_id)
        if not propietario:
            current_app.logger.warning(f"Propietario con ID {propietario_id} no encontrado")
            return False
            
        # Establecer en la sesión
        session[ACTIVE_OWNER_SESSION_KEY] = propietario_id
        current_app.logger.info(
            f"Propietario activo establecido: {propietario_id} ({propietario.nombre}) "
            f"para usuario {current_user.username}"
        )
        return True
        
    except Exception as e:
        current_app.logger.error(f"Error al establecer propietario activo {propietario_id}: {str(e)}")
        return False


def get_active_owner_id():
    """
    Obtiene el ID del propietario activo de la sesión.
    
    Returns:
        int or None: ID del propietario activo o None si no hay ninguno establecido
    """
    return session.get(ACTIVE_OWNER_SESSION_KEY)


def get_active_owner():
    """
    Obtiene el objeto Propietario activo de la sesión.
    
    Returns:
        Propietario or None: Objeto propietario activo o None si no hay ninguno
    """
    propietario_id = get_active_owner_id()
    if not propietario_id:
        return None
        
    try:
        # Validar que el usuario sigue teniendo acceso al propietario
        if not user_has_access_to_owner(propietario_id):
            current_app.logger.warning(
                f"Usuario {current_user.username} ya no tiene acceso al propietario activo {propietario_id}"
            )
            clear_active_owner()
            return None
            
        propietario = db.session.get(Propietario, propietario_id)
        if not propietario:
            current_app.logger.warning(
                f"Propietario activo {propietario_id} no encontrado en BD, limpiando sesión"
            )
            clear_active_owner()
            return None
            
        return propietario
        
    except Exception as e:
        current_app.logger.error(f"Error al obtener propietario activo {propietario_id}: {str(e)}")
        clear_active_owner()
        return None


def clear_active_owner():
    """
    Limpia el propietario activo de la sesión.
    """
    if ACTIVE_OWNER_SESSION_KEY in session:
        old_owner_id = session.pop(ACTIVE_OWNER_SESSION_KEY)
        current_app.logger.info(
            f"Propietario activo {old_owner_id} removido de sesión para usuario {current_user.username}"
        )


def user_has_access_to_owner(propietario_id):
    """
    Verifica si el usuario actual tiene acceso al propietario especificado.
    
    Args:
        propietario_id (int): ID del propietario a verificar
        
    Returns:
        bool: True si el usuario tiene acceso, False en caso contrario
    """
    if not current_user.is_authenticated:
        return False
        
    # Los administradores tienen acceso a todos los propietarios
    if current_user.role == 'admin':
        return True
        
    # Para gestores y usuarios, verificar si el propietario está en su lista asignada
    try:
        assigned_owner_ids = {p.id for p in current_user.propietarios_asignados}
        return propietario_id in assigned_owner_ids
    except Exception as e:
        current_app.logger.error(
            f"Error al verificar acceso del usuario {current_user.username} "
            f"al propietario {propietario_id}: {str(e)}"
        )
        return False


def get_user_available_owners():
    """
    Obtiene la lista de propietarios disponibles para el usuario actual.
    
    Returns:
        list: Lista de objetos Propietario a los que el usuario tiene acceso
    """
    if not current_user.is_authenticated:
        return []
        
    try:
        if current_user.role == 'admin':
            # Los administradores ven todos los propietarios
            try:
                return Propietario.query.options(
                    selectinload(Propietario.propiedades).selectinload(Propiedad.contratos)
                ).order_by(Propietario.nombre).all()
            except Exception as e:
                current_app.logger.warning(f"Error cargando relaciones, fallback a consulta simple: {str(e)}")
                # Fallback: consulta simple sin relaciones cargadas
                return Propietario.query.order_by(Propietario.nombre).all()
        else:
            # Gestores y usuarios solo ven sus propietarios asignados
            try:
                # Recargar con las relaciones necesarias
                propietario_ids = [p.id for p in current_user.propietarios_asignados]
                if not propietario_ids:
                    return []
                return Propietario.query.filter(
                    Propietario.id.in_(propietario_ids)
                ).options(
                    selectinload(Propietario.propiedades).selectinload(Propiedad.contratos)
                ).order_by(Propietario.nombre).all()
            except Exception as e:
                current_app.logger.warning(f"Error cargando relaciones, fallback a consulta simple: {str(e)}")
                # Fallback: usar propietarios asignados directamente
                return list(current_user.propietarios_asignados)
    except Exception as e:
        current_app.logger.error(
            f"Error al obtener propietarios disponibles para usuario {current_user.username}: {str(e)}"
        )
        return []


def has_active_owner():
    """
    Verifica si hay un propietario activo válido en la sesión.
    
    Returns:
        bool: True si hay un propietario activo válido, False en caso contrario
    """
    return get_active_owner() is not None


def auto_select_owner_if_needed():
    """
    Selecciona automáticamente un propietario si el usuario no tiene ninguno activo
    pero tiene acceso a exactamente uno.
    
    Returns:
        bool: True si se seleccionó automáticamente, False en caso contrario
    """
    # Si ya hay un propietario activo, no hacer nada
    if has_active_owner():
        return False
        
    # Obtener propietarios disponibles
    available_owners = get_user_available_owners()
    
    # Si el usuario tiene acceso a exactamente un propietario, seleccionarlo automáticamente
    if len(available_owners) == 1:
        propietario = available_owners[0]
        if set_active_owner(propietario.id):
            current_app.logger.info(
                f"Propietario {propietario.id} ({propietario.nombre}) "
                f"seleccionado automáticamente para usuario {current_user.username}"
            )
            return True
            
    return False


def validate_session_integrity():
    """
    Valida la integridad de la sesión del propietario activo.
    Limpia la sesión si el propietario ya no es válido o accesible.
    
    Returns:
        bool: True si la sesión es válida o se limpió correctamente, False si hay errores
    """
    try:
        propietario_id = get_active_owner_id()
        if not propietario_id:
            return True  # No hay propietario activo, sesión válida
            
        # Verificar que el usuario sigue teniendo acceso
        if not user_has_access_to_owner(propietario_id):
            clear_active_owner()
            return True
            
        # Verificar que el propietario existe
        propietario = db.session.get(Propietario, propietario_id)
        if not propietario:
            clear_active_owner()
            return True
            
        return True
        
    except Exception as e:
        current_app.logger.error(f"Error al validar integridad de sesión: {str(e)}")
        try:
            clear_active_owner()
        except:
            pass  # Si no podemos limpiar, al menos no fallar
        return False


def get_active_owner_context():
    """
    Obtiene el contexto completo del propietario activo para usar en templates.
    
    Returns:
        dict: Diccionario con información del propietario activo y disponibles
    """
    context = {
        'active_owner': get_active_owner(),
        'available_owners': get_user_available_owners(),
        'has_active_owner': has_active_owner(),
        'can_change_owner': len(get_user_available_owners()) > 1
    }
    
    return context
