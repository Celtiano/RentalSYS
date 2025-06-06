# myapp/utils/database_helpers.py
"""
Funciones auxiliares para consultas de base de datos filtradas por propietario activo.
Este módulo proporciona métodos convenientes para realizar consultas que respetan
automáticamente el propietario activo seleccionado en la sesión.
"""

from flask import current_app
from flask_login import current_user
from sqlalchemy import and_, or_
from sqlalchemy.orm import joinedload, selectinload

from ..models import (
    Propietario, Propiedad, Contrato, Factura, Gasto, 
    Inquilino, Documento, db
)
from .owner_session import get_active_owner_id, get_active_owner


class OwnerFilteredQueries:
    """
    Clase que proporciona métodos para realizar consultas filtradas por propietario activo.
    """
    
    @staticmethod
    def get_active_owner_or_raise():
        """
        Obtiene el propietario activo o lanza una excepción.
        
        Returns:
            Propietario: El propietario activo
            
        Raises:
            ValueError: Si no hay propietario activo
        """
        active_owner = get_active_owner()
        if not active_owner:
            raise ValueError("No hay propietario activo en la sesión")
        return active_owner
    
    @staticmethod
    def should_apply_filter():
        """
        Determina si se debe aplicar filtrado automático.
        
        Returns:
            bool: True si se debe filtrar, False en caso contrario
        """
        if not current_user.is_authenticated:
            return False
            
        # Admin puede ver todo, pero respeta propietario activo si está establecido
        if current_user.role == 'admin':
            return get_active_owner_id() is not None
            
        # Gestores y usuarios siempre necesitan algún tipo de filtrado
        # Ya sea por propietario activo o por propietarios asignados
        if current_user.role in ('gestor', 'usuario'):
            return True
            
        return False
    
    # === PROPIEDADES ===
    
    @staticmethod
    def get_propiedades(apply_filter=None, **filters):
        """
        Obtiene propiedades filtradas por propietario activo.
        
        Args:
            apply_filter (bool): Si aplicar filtrado (None = automático)
            **filters: Filtros adicionales para la consulta
            
        Returns:
            Query: Consulta de propiedades filtradas
        """
        query = Propiedad.query
        
        if apply_filter is None:
            apply_filter = OwnerFilteredQueries.should_apply_filter()
            
        if apply_filter:
            active_owner_id = get_active_owner_id()
            if active_owner_id:
                # Filtrar por propietario activo específico
                query = query.filter(Propiedad.propietario_id == active_owner_id)
            elif current_user.role in ('gestor', 'usuario'):
                # Si no hay propietario activo pero es gestor/usuario, 
                # filtrar por propietarios asignados
                assigned_owner_ids = [p.id for p in current_user.propietarios_asignados]
                if assigned_owner_ids:
                    query = query.filter(Propiedad.propietario_id.in_(assigned_owner_ids))
        
        # Aplicar filtros adicionales
        for field, value in filters.items():
            if hasattr(Propiedad, field):
                query = query.filter(getattr(Propiedad, field) == value)
        
        return query
    
    @staticmethod
    def get_propiedad_by_id(propiedad_id, apply_filter=None):
        """
        Obtiene una propiedad específica por ID.
        
        Args:
            propiedad_id (int): ID de la propiedad
            apply_filter (bool): Si aplicar filtrado (None = automático)
            
        Returns:
            Propiedad or None: La propiedad encontrada o None
        """
        query = OwnerFilteredQueries.get_propiedades(apply_filter=apply_filter)
        return query.filter(Propiedad.id == propiedad_id).first()
    
    # === CONTRATOS ===
    
    @staticmethod
    def get_contratos(apply_filter=None, include_relations=True, **filters):
        """
        Obtiene contratos filtrados por propietario activo.
        
        Args:
            apply_filter (bool): Si aplicar filtrado (None = automático)
            include_relations (bool): Si incluir relaciones cargadas
            **filters: Filtros adicionales para la consulta
            
        Returns:
            Query: Consulta de contratos filtrados
        """
        query = Contrato.query
        
        if include_relations:
            query = query.options(
                joinedload(Contrato.propiedad_ref),
                joinedload(Contrato.inquilino_ref)
            )
        
        if apply_filter is None:
            apply_filter = OwnerFilteredQueries.should_apply_filter()
            
        if apply_filter:
            active_owner_id = get_active_owner_id()
            if active_owner_id:
                # Filtrar por propietario activo específico
                query = query.join(Propiedad).filter(
                    Propiedad.propietario_id == active_owner_id
                )
            elif current_user.role in ('gestor', 'usuario'):
                # Si no hay propietario activo pero es gestor/usuario, 
                # filtrar por propietarios asignados
                assigned_owner_ids = [p.id for p in current_user.propietarios_asignados]
                if assigned_owner_ids:
                    query = query.join(Propiedad).filter(
                        Propiedad.propietario_id.in_(assigned_owner_ids)
                    )
        
        # Aplicar filtros adicionales
        for field, value in filters.items():
            if hasattr(Contrato, field):
                query = query.filter(getattr(Contrato, field) == value)
        
        return query
    
    @staticmethod
    def get_contrato_by_id(contrato_id, apply_filter=None, include_relations=True):
        """
        Obtiene un contrato específico por ID.
        
        Args:
            contrato_id (int): ID del contrato
            apply_filter (bool): Si aplicar filtrado (None = automático)
            include_relations (bool): Si incluir relaciones cargadas
            
        Returns:
            Contrato or None: El contrato encontrado o None
        """
        query = OwnerFilteredQueries.get_contratos(
            apply_filter=apply_filter, 
            include_relations=include_relations
        )
        return query.filter(Contrato.id == contrato_id).first()
    
    # === FACTURAS ===
    
    @staticmethod
    def get_facturas(apply_filter=None, include_relations=True, **filters):
        """
        Obtiene facturas filtradas por propietario activo.
        
        Args:
            apply_filter (bool): Si aplicar filtrado (None = automático)
            include_relations (bool): Si incluir relaciones cargadas
            **filters: Filtros adicionales para la consulta
            
        Returns:
            Query: Consulta de facturas filtradas
        """
        query = Factura.query
        
        if include_relations:
            query = query.options(
                joinedload(Factura.propiedad_ref),
                joinedload(Factura.inquilino_ref),
                joinedload(Factura.contrato_ref)
            )
        
        if apply_filter is None:
            apply_filter = OwnerFilteredQueries.should_apply_filter()
            
        if apply_filter:
            active_owner_id = get_active_owner_id()
            if active_owner_id:
                # Filtrar por propietario activo específico
                query = query.join(Propiedad).filter(
                    Propiedad.propietario_id == active_owner_id
                )
            elif current_user.role in ('gestor', 'usuario'):
                # Si no hay propietario activo pero es gestor/usuario, 
                # filtrar por propietarios asignados
                assigned_owner_ids = [p.id for p in current_user.propietarios_asignados]
                if assigned_owner_ids:
                    query = query.join(Propiedad).filter(
                        Propiedad.propietario_id.in_(assigned_owner_ids)
                    )
        
        # Aplicar filtros adicionales
        for field, value in filters.items():
            if hasattr(Factura, field):
                query = query.filter(getattr(Factura, field) == value)
        
        return query
    
    @staticmethod
    def get_factura_by_id(factura_id, apply_filter=None, include_relations=True):
        """
        Obtiene una factura específica por ID.
        
        Args:
            factura_id (int): ID de la factura
            apply_filter (bool): Si aplicar filtrado (None = automático)
            include_relations (bool): Si incluir relaciones cargadas
            
        Returns:
            Factura or None: La factura encontrada o None
        """
        query = OwnerFilteredQueries.get_facturas(
            apply_filter=apply_filter,
            include_relations=include_relations
        )
        return query.filter(Factura.id == factura_id).first()
    
    # === GASTOS ===
    
    @staticmethod
    def get_gastos(apply_filter=None, include_relations=True, **filters):
        """
        Obtiene gastos filtrados por propietario activo.
        
        Args:
            apply_filter (bool): Si aplicar filtrado (None = automático)
            include_relations (bool): Si incluir relaciones cargadas
            **filters: Filtros adicionales para la consulta
            
        Returns:
            Query: Consulta de gastos filtrados
        """
        query = Gasto.query
        
        if include_relations:
            query = query.options(joinedload(Gasto.contrato))
        
        if apply_filter is None:
            apply_filter = OwnerFilteredQueries.should_apply_filter()
            
        if apply_filter:
            active_owner_id = get_active_owner_id()
            if active_owner_id:
                # Filtrar por propietario activo específico
                query = query.join(Contrato).join(Propiedad).filter(
                    Propiedad.propietario_id == active_owner_id
                )
            elif current_user.role in ('gestor', 'usuario'):
                # Si no hay propietario activo pero es gestor/usuario, 
                # filtrar por propietarios asignados
                assigned_owner_ids = [p.id for p in current_user.propietarios_asignados]
                if assigned_owner_ids:
                    query = query.join(Contrato).join(Propiedad).filter(
                        Propiedad.propietario_id.in_(assigned_owner_ids)
                    )
        
        # Aplicar filtros adicionales
        for field, value in filters.items():
            if hasattr(Gasto, field):
                query = query.filter(getattr(Gasto, field) == value)
        
        return query
    
    @staticmethod
    def get_gasto_by_id(gasto_id, apply_filter=None, include_relations=True):
        """
        Obtiene un gasto específico por ID.
        
        Args:
            gasto_id (int): ID del gasto
            apply_filter (bool): Si aplicar filtrado (None = automático)
            include_relations (bool): Si incluir relaciones cargadas
            
        Returns:
            Gasto or None: El gasto encontrado o None
        """
        query = OwnerFilteredQueries.get_gastos(
            apply_filter=apply_filter,
            include_relations=include_relations
        )
        return query.filter(Gasto.id == gasto_id).first()
    
    # === INQUILINOS ===
    
    @staticmethod
    def get_inquilinos(apply_filter=None, include_relations=True, **filters):
        """
        Obtiene inquilinos filtrados por propietario activo.
        Los inquilinos se filtran si tienen al menos un contrato con propiedades del propietario activo.
        
        Args:
            apply_filter (bool): Si aplicar filtrado (None = automático)
            include_relations (bool): Si incluir relaciones cargadas
            **filters: Filtros adicionales para la consulta
            
        Returns:
            Query: Consulta de inquilinos filtrados
        """
        query = Inquilino.query
        
        if include_relations:
            query = query.options(selectinload(Inquilino.contratos))
        
        if apply_filter is None:
            apply_filter = OwnerFilteredQueries.should_apply_filter()
            
        if apply_filter:
            active_owner_id = get_active_owner_id()
            if active_owner_id:
                # Filtrar por propietario activo específico
                inquilinos_con_contratos = db.session.query(Contrato.inquilino_id).join(
                    Propiedad
                ).filter(
                    Propiedad.propietario_id == active_owner_id
                ).distinct().subquery()
                
                query = query.filter(
                    Inquilino.id.in_(
                        db.session.query(inquilinos_con_contratos.c.inquilino_id)
                    )
                )
            elif current_user.role in ('gestor', 'usuario'):
                # Si no hay propietario activo pero es gestor/usuario, 
                # filtrar por propietarios asignados
                assigned_owner_ids = [p.id for p in current_user.propietarios_asignados]
                if assigned_owner_ids:
                    inquilinos_con_contratos = db.session.query(Contrato.inquilino_id).join(
                        Propiedad
                    ).filter(
                        Propiedad.propietario_id.in_(assigned_owner_ids)
                    ).distinct().subquery()
                    
                    query = query.filter(
                        Inquilino.id.in_(
                            db.session.query(inquilinos_con_contratos.c.inquilino_id)
                        )
                    )
        
        # Aplicar filtros adicionales
        for field, value in filters.items():
            if hasattr(Inquilino, field):
                query = query.filter(getattr(Inquilino, field) == value)
        
        return query
    
    @staticmethod
    def get_inquilino_by_id(inquilino_id, apply_filter=None, include_relations=True):
        """
        Obtiene un inquilino específico por ID.
        
        Args:
            inquilino_id (int): ID del inquilino
            apply_filter (bool): Si aplicar filtrado (None = automático)
            include_relations (bool): Si incluir relaciones cargadas
            
        Returns:
            Inquilino or None: El inquilino encontrado o None
        """
        query = OwnerFilteredQueries.get_inquilinos(
            apply_filter=apply_filter,
            include_relations=include_relations
        )
        return query.filter(Inquilino.id == inquilino_id).first()
    
    # === DOCUMENTOS ===
    
    @staticmethod
    def get_documentos(apply_filter=None, include_relations=True, **filters):
        """
        Obtiene documentos filtrados por propietario activo.
        
        Args:
            apply_filter (bool): Si aplicar filtrado (None = automático)
            include_relations (bool): Si incluir relaciones cargadas
            **filters: Filtros adicionales para la consulta
            
        Returns:
            Query: Consulta de documentos filtrados
        """
        query = Documento.query
        
        if include_relations:
            query = query.options(joinedload(Documento.contrato_ref))
        
        if apply_filter is None:
            apply_filter = OwnerFilteredQueries.should_apply_filter()
            
        if apply_filter:
            active_owner_id = get_active_owner_id()
            if active_owner_id:
                # Filtrar por propietario activo específico
                query = query.join(Contrato).join(Propiedad).filter(
                    Propiedad.propietario_id == active_owner_id
                )
            elif current_user.role in ('gestor', 'usuario'):
                # Si no hay propietario activo pero es gestor/usuario, 
                # filtrar por propietarios asignados
                assigned_owner_ids = [p.id for p in current_user.propietarios_asignados]
                if assigned_owner_ids:
                    query = query.join(Contrato).join(Propiedad).filter(
                        Propiedad.propietario_id.in_(assigned_owner_ids)
                    )
        
        # Aplicar filtros adicionales
        for field, value in filters.items():
            if hasattr(Documento, field):
                query = query.filter(getattr(Documento, field) == value)
        
        return query
    
    @staticmethod
    def get_documento_by_id(documento_id, apply_filter=None, include_relations=True):
        """
        Obtiene un documento específico por ID.
        
        Args:
            documento_id (int): ID del documento
            apply_filter (bool): Si aplicar filtrado (None = automático)
            include_relations (bool): Si incluir relaciones cargadas
            
        Returns:
            Documento or None: El documento encontrado o None
        """
        query = OwnerFilteredQueries.get_documentos(
            apply_filter=apply_filter,
            include_relations=include_relations
        )
        return query.filter(Documento.id == documento_id).first()
    
    # === MÉTODOS DE CONVENIENCIA ===
    
    @staticmethod
    def get_stats_for_active_owner():
        """
        Obtiene estadísticas para el propietario activo.
        
        Returns:
            dict: Diccionario con estadísticas
        """
        active_owner = get_active_owner()
        if not active_owner:
            return {}
        
        try:
            stats = {}
            
            # Contar propiedades
            stats['propiedades_count'] = OwnerFilteredQueries.get_propiedades().count()
            
            # Contar contratos activos
            stats['contratos_activos'] = OwnerFilteredQueries.get_contratos(
                estado='activo'
            ).count()
            
            # Contar facturas pendientes
            stats['facturas_pendientes'] = OwnerFilteredQueries.get_facturas(
                estado='pendiente'
            ).count()
            
            # Contar inquilinos únicos
            stats['inquilinos_count'] = OwnerFilteredQueries.get_inquilinos().count()
            
            return stats
            
        except Exception as e:
            current_app.logger.error(f"Error calculando estadísticas: {str(e)}")
            return {}
    
    @staticmethod
    def validate_access_to_entity(entity_type, entity_id):
        """
        Valida que el usuario actual tenga acceso a una entidad específica.
        
        Args:
            entity_type (str): Tipo de entidad ('propiedad', 'contrato', etc.)
            entity_id (int): ID de la entidad
            
        Returns:
            bool: True si tiene acceso, False en caso contrario
        """
        try:
            if entity_type == 'propiedad':
                entity = OwnerFilteredQueries.get_propiedad_by_id(entity_id)
            elif entity_type == 'contrato':
                entity = OwnerFilteredQueries.get_contrato_by_id(entity_id)
            elif entity_type == 'factura':
                entity = OwnerFilteredQueries.get_factura_by_id(entity_id)
            elif entity_type == 'gasto':
                entity = OwnerFilteredQueries.get_gasto_by_id(entity_id)
            elif entity_type == 'inquilino':
                entity = OwnerFilteredQueries.get_inquilino_by_id(entity_id)
            elif entity_type == 'documento':
                entity = OwnerFilteredQueries.get_documento_by_id(entity_id)
            else:
                return False
            
            return entity is not None
            
        except Exception as e:
            current_app.logger.error(
                f"Error validando acceso a {entity_type} {entity_id}: {str(e)}"
            )
            return False


# Funciones de conveniencia para usar en las vistas
def get_filtered_propiedades(**filters):
    """Wrapper conveniente para obtener propiedades filtradas."""
    return OwnerFilteredQueries.get_propiedades(**filters)


def get_filtered_contratos(**filters):
    """Wrapper conveniente para obtener contratos filtrados."""
    return OwnerFilteredQueries.get_contratos(**filters)


def get_filtered_facturas(**filters):
    """Wrapper conveniente para obtener facturas filtradas."""
    return OwnerFilteredQueries.get_facturas(**filters)


def get_filtered_gastos(**filters):
    """Wrapper conveniente para obtener gastos filtrados."""
    return OwnerFilteredQueries.get_gastos(**filters)


def get_filtered_inquilinos(**filters):
    """Wrapper conveniente para obtener inquilinos filtrados."""
    return OwnerFilteredQueries.get_inquilinos(**filters)


def get_inquilinos_available_for_new_contracts(**filters):
    """
    Obtiene inquilinos disponibles para crear nuevos contratos.
    
    A diferencia de get_filtered_inquilinos(), esta función NO filtra por contratos existentes,
    permitiendo crear el primer contrato entre un propietario e inquilino.
    
    Solo respeta los permisos de propietarios asignados para gestores/usuarios.
    """
    from flask_login import current_user
    
    query = Inquilino.query.options(selectinload(Inquilino.contratos))
    
    # Solo aplicar filtrado por permisos de usuario, NO por propietario activo
    if current_user.role in ('gestor', 'usuario'):
        # Para gestores/usuarios: mostrar todos los inquilinos
        # (el sistema validará en el backend que solo puedan crear contratos 
        # con propiedades de sus propietarios asignados)
        pass
    # Para admin: mostrar todos los inquilinos sin restricción
    
    # Aplicar filtros adicionales
    for field, value in filters.items():
        if hasattr(Inquilino, field):
            query = query.filter(getattr(Inquilino, field) == value)
    
    return query


def get_filtered_documentos(**filters):
    """Wrapper conveniente para obtener documentos filtrados."""
    return OwnerFilteredQueries.get_documentos(**filters)


# Context manager para bypass temporal del filtrado
class bypass_owner_filtering:
    """
    Context manager para bypasear temporalmente el filtrado por propietario.
    
    Usage:
        with bypass_owner_filtering():
            # Estas consultas no serán filtradas
            all_properties = get_filtered_propiedades(apply_filter=False).all()
    """
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
    
    def get_propiedades(self, **filters):
        """Obtiene propiedades sin filtrado."""
        return OwnerFilteredQueries.get_propiedades(apply_filter=False, **filters)
    
    def get_contratos(self, **filters):
        """Obtiene contratos sin filtrado."""
        return OwnerFilteredQueries.get_contratos(apply_filter=False, **filters)
    
    def get_facturas(self, **filters):
        """Obtiene facturas sin filtrado."""
        return OwnerFilteredQueries.get_facturas(apply_filter=False, **filters)
    
    def get_gastos(self, **filters):
        """Obtiene gastos sin filtrado."""
        return OwnerFilteredQueries.get_gastos(apply_filter=False, **filters)
    
    def get_inquilinos(self, **filters):
        """Obtiene inquilinos sin filtrado."""
        return OwnerFilteredQueries.get_inquilinos(apply_filter=False, **filters)
    
    def get_documentos(self, **filters):
        """Obtiene documentos sin filtrado."""
        return OwnerFilteredQueries.get_documentos(apply_filter=False, **filters)
