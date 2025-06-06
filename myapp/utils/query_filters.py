# myapp/utils/query_filters.py
"""
Sistema de filtrado automático de consultas por propietario activo.
Este módulo proporciona funcionalidades para interceptar y filtrar automáticamente
las consultas SQLAlchemy según el propietario activo en la sesión.
"""

from flask import current_app, g, has_request_context
from flask_login import current_user
from sqlalchemy import event, and_, or_
from sqlalchemy.orm.query import Query
from sqlalchemy.orm import Session

from ..models import (
    Propietario, Propiedad, Contrato, Factura, Gasto, 
    Inquilino, Documento, db
)
from .owner_session import get_active_owner_id, get_active_owner


# Variable global para controlar si el filtrado está activo
_filtering_enabled = True

# Lista de modelos que deben ser filtrados automáticamente
FILTERED_MODELS = {
    'Propiedad': {
        'model': Propiedad,
        'filter_field': 'propietario_id',
        'filter_type': 'direct'
    },
    'Contrato': {
        'model': Contrato,
        'filter_field': 'propiedad_id',
        'filter_type': 'subquery',
        'subquery_model': Propiedad,
        'subquery_field': 'propietario_id'
    },
    'Factura': {
        'model': Factura,
        'filter_field': 'propiedad_id',
        'filter_type': 'subquery',
        'subquery_model': Propiedad,
        'subquery_field': 'propietario_id'
    },
    'Gasto': {
        'model': Gasto,
        'filter_field': 'contrato_id',
        'filter_type': 'nested_subquery',
        'subquery_model': Contrato,
        'subquery_field': 'propiedad_id',
        'nested_model': Propiedad,
        'nested_field': 'propietario_id'
    },
    'Documento': {
        'model': Documento,
        'filter_field': 'contrato_id',
        'filter_type': 'nested_subquery',
        'subquery_model': Contrato,
        'subquery_field': 'propiedad_id',
        'nested_model': Propiedad,
        'nested_field': 'propietario_id'
    },
    'Inquilino': {
        'model': Inquilino,
        'filter_field': 'id',
        'filter_type': 'complex_subquery',
        'description': 'Inquilinos con al menos un contrato del propietario activo'
    }
}


def is_filtering_enabled():
    """Verifica si el filtrado automático está habilitado."""
    return _filtering_enabled and has_request_context()


def enable_filtering():
    """Habilita el filtrado automático."""
    global _filtering_enabled
    _filtering_enabled = True


def disable_filtering():
    """Deshabilita el filtrado automático."""
    global _filtering_enabled
    _filtering_enabled = False


def should_filter_query(mapper_class):
    """
    Determina si una consulta debe ser filtrada automáticamente.
    
    Args:
        mapper_class: Clase del modelo SQLAlchemy
        
    Returns:
        bool: True si debe filtrarse, False en caso contrario
    """
    if not is_filtering_enabled():
        return False
        
    if not current_user.is_authenticated:
        return False
        
    # Los administradores pueden ver todo por defecto, pero respetan el propietario activo si está establecido
    if current_user.role == 'admin':
        # Solo filtrar para admin si hay un propietario activo establecido explícitamente
        active_owner_id = get_active_owner_id()
        return active_owner_id is not None
        
    # Gestores y usuarios siempre deben tener filtrado activo
    if current_user.role in ('gestor', 'usuario'):
        active_owner_id = get_active_owner_id()
        return active_owner_id is not None
        
    return False


def get_filter_for_model(model_class, active_owner_id):
    """
    Construye el filtro SQLAlchemy apropiado para un modelo dado.
    
    Args:
        model_class: Clase del modelo SQLAlchemy
        active_owner_id: ID del propietario activo
        
    Returns:
        sqlalchemy condition: Condición de filtrado o None si no aplica
    """
    model_name = model_class.__name__
    
    if model_name not in FILTERED_MODELS:
        return None
        
    filter_config = FILTERED_MODELS[model_name]
    
    try:
        if filter_config['filter_type'] == 'direct':
            # Filtrado directo: modelo.campo = active_owner_id
            field = getattr(model_class, filter_config['filter_field'])
            return field == active_owner_id
            
        elif filter_config['filter_type'] == 'subquery':
            # Filtrado por subquery: modelo.campo IN (SELECT id FROM subquery_model WHERE subquery_field = active_owner_id)
            subquery_model = filter_config['subquery_model']
            subquery = db.session.query(subquery_model.id).filter(
                getattr(subquery_model, filter_config['subquery_field']) == active_owner_id
            ).subquery()
            
            field = getattr(model_class, filter_config['filter_field'])
            return field.in_(db.session.query(subquery.c.id))
            
        elif filter_config['filter_type'] == 'nested_subquery':
            # Filtrado por subquery anidada: dos niveles de JOIN
            nested_model = filter_config['nested_model']
            subquery_model = filter_config['subquery_model']
            
            # Primero, obtener IDs de subquery_model que pertenecen al propietario
            inner_subquery = db.session.query(subquery_model.id).join(
                nested_model, 
                getattr(subquery_model, filter_config['subquery_field']) == nested_model.id
            ).filter(
                getattr(nested_model, filter_config['nested_field']) == active_owner_id
            ).subquery()
            
            field = getattr(model_class, filter_config['filter_field'])
            return field.in_(db.session.query(inner_subquery.c.id))
            
        elif filter_config['filter_type'] == 'complex_subquery':
            # Para Inquilinos: inquilinos que tienen al menos un contrato con propiedades del propietario activo
            inquilino_ids = db.session.query(Contrato.inquilino_id).join(
                Propiedad, Contrato.propiedad_id == Propiedad.id
            ).filter(
                Propiedad.propietario_id == active_owner_id
            ).distinct().subquery()
            
            return model_class.id.in_(db.session.query(inquilino_ids.c.inquilino_id))
            
    except Exception as e:
        current_app.logger.error(
            f"Error construyendo filtro para {model_name}: {str(e)}"
        )
        return None
    
    return None


class NoFilterQuery(Query):
    """Query personalizada que bypass el filtrado automático."""
    _bypass_filtering = True


def query_without_filter(model_class):
    """
    Crear una consulta que bypasea el filtrado automático.
    
    Args:
        model_class: Clase del modelo SQLAlchemy
        
    Returns:
        Query: Consulta sin filtrado automático
    """
    return db.session.query(model_class.__class__).filter_by(__class__=NoFilterQuery)


# COMENTADO TEMPORALMENTE: Los eventos before_bulk_update y before_bulk_delete
# no están disponibles en versiones anteriores de SQLAlchemy

# @event.listens_for(Session, 'before_bulk_update')
# def before_bulk_update(update_context):
#     """Intercepta operaciones bulk_update para aplicar filtros."""
#     if not should_filter_query(update_context.mapper.class_):
#         return
#         
#     active_owner_id = get_active_owner_id()
#     if not active_owner_id:
#         return
#         
#     try:
#         filter_condition = get_filter_for_model(update_context.mapper.class_, active_owner_id)
#         if filter_condition is not None:
#             # Agregar filtro a la operación bulk
#             if update_context.whereclause is None:
#                 update_context.whereclause = filter_condition
#             else:
#                 update_context.whereclause = and_(update_context.whereclause, filter_condition)
#                 
#             current_app.logger.debug(
#                 f"Filtro aplicado a bulk_update para {update_context.mapper.class_.__name__} "
#                 f"con propietario {active_owner_id}"
#             )
#             
#     except Exception as e:
#         current_app.logger.error(
#             f"Error aplicando filtro en bulk_update para {update_context.mapper.class_.__name__}: {str(e)}"
#         )


# @event.listens_for(Session, 'before_bulk_delete')
# def before_bulk_delete(delete_context):
#     """Intercepta operaciones bulk_delete para aplicar filtros."""
#     if not should_filter_query(delete_context.mapper.class_):
#         return
#         
#     active_owner_id = get_active_owner_id()
#     if not active_owner_id:
#         return
#         
#     try:
#         filter_condition = get_filter_for_model(delete_context.mapper.class_, active_owner_id)
#         if filter_condition is not None:
#             # Agregar filtro a la operación bulk
#             if delete_context.whereclause is None:
#                 delete_context.whereclause = filter_condition
#             else:
#                 delete_context.whereclause = and_(delete_context.whereclause, filter_condition)
#                 
#             current_app.logger.debug(
#                 f"Filtro aplicado a bulk_delete para {delete_context.mapper.class_.__name__} "
#                 f"con propietario {active_owner_id}"
#             )
#             
#     except Exception as e:
#         current_app.logger.error(
#             f"Error aplicando filtro en bulk_delete para {delete_context.mapper.class_.__name__}: {str(e)}"
#         )


class FilteredQuery(Query):
    """Clase Query personalizada que aplica filtrado automático."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_owner_filter()
    
    def _apply_owner_filter(self):
        """Aplica el filtro de propietario activo si corresponde."""
        # Verificar si esta query ya tiene bypass
        if hasattr(self, '_bypass_filtering') and self._bypass_filtering:
            return
            
        # Obtener el modelo de la query
        if not self.column_descriptions:
            return
            
        model_class = None
        for desc in self.column_descriptions:
            if desc['type'] is not None and hasattr(desc['type'], '__tablename__'):
                model_class = desc['type']
                break
                
        if not model_class:
            return
            
        # Verificar si debe aplicarse filtrado
        if not should_filter_query(model_class):
            return
            
        active_owner_id = get_active_owner_id()
        if not active_owner_id:
            return
            
        try:
            filter_condition = get_filter_for_model(model_class, active_owner_id)
            if filter_condition is not None:
                # Aplicar filtro si no está ya aplicado
                if not self._has_owner_filter():
                    self._apply_filter_condition(filter_condition)
                    current_app.logger.debug(
                        f"Filtro automático aplicado para {model_class.__name__} "
                        f"con propietario {active_owner_id}"
                    )
                    
        except Exception as e:
            current_app.logger.error(
                f"Error aplicando filtro automático para {model_class.__name__}: {str(e)}"
            )
    
    def _has_owner_filter(self):
        """Verifica si la query ya tiene un filtro de propietario aplicado."""
        # Esta es una implementación simplificada
        # En un caso real, podrías inspeccionar self.whereclause más detalladamente
        return hasattr(self, '_owner_filter_applied')
    
    def _apply_filter_condition(self, condition):
        """Aplica la condición de filtro a la query."""
        try:
            # Marcar que el filtro fue aplicado
            self._owner_filter_applied = True
            
            # Aplicar el filtro
            return self.filter(condition)
            
        except Exception as e:
            current_app.logger.error(f"Error aplicando condición de filtro: {str(e)}")
            return self


def setup_automatic_filtering(app):
    """
    Configura el sistema de filtrado automático para la aplicación.
    
    Args:
        app: Instancia de Flask
    """
    # Configurar la clase Query personalizada
    db.Model.query_class = FilteredQuery
    
    app.logger.info("Sistema de filtrado automático configurado correctamente")


def bypass_filtering():
    """
    Context manager para bypasear temporalmente el filtrado automático.
    
    Usage:
        with bypass_filtering():
            # Estas consultas no serán filtradas
            all_properties = Propiedad.query.all()
    """
    class BypassContext:
        def __enter__(self):
            global _filtering_enabled
            self._old_state = _filtering_enabled
            _filtering_enabled = False
            return self
        
        def __exit__(self, exc_type, exc_val, exc_tb):
            global _filtering_enabled
            _filtering_enabled = self._old_state
    
    return BypassContext()


# Funciones auxiliares para debugging
def get_filtering_status():
    """Obtiene el estado actual del sistema de filtrado."""
    return {
        'filtering_enabled': is_filtering_enabled(),
        'has_request_context': has_request_context(),
        'user_authenticated': current_user.is_authenticated if has_request_context() else False,
        'user_role': current_user.role if (has_request_context() and current_user.is_authenticated) else None,
        'active_owner_id': get_active_owner_id() if has_request_context() else None,
        'filtered_models': list(FILTERED_MODELS.keys())
    }


def log_filtering_status():
    """Registra el estado actual del filtrado en los logs."""
    status = get_filtering_status()
    current_app.logger.debug(f"Estado del filtrado automático: {status}")
