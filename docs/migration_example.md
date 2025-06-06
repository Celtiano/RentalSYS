# Ejemplo Práctico de Migración al Sistema de Filtrado Automático

## Descripción del Ejemplo

Este documento muestra cómo migrar vistas existentes de RentalSYS para usar el nuevo sistema de middleware de filtrado automático. Usaremos como ejemplo las vistas de propiedades, contratos y facturas.

## Estado Actual (Antes de la Migración)

### Vista de Propiedades Original

```python
# myapp/routes/propiedades.py (ANTES)

@propiedades_bp.route('/')
@login_required
@role_required('admin', 'gestor', 'usuario')
def listar_propiedades():
    """Lista las propiedades según el rol del usuario."""
    
    if current_user.role == 'admin':
        # Admin ve todas las propiedades
        propiedades = Propiedad.query.order_by(Propiedad.direccion).all()
    else:
        # Gestor/Usuario solo ve propiedades de sus propietarios asignados
        owner_ids = [p.id for p in current_user.propietarios_asignados]
        if not owner_ids:
            flash("No tienes propietarios asignados.", "warning")
            return redirect(url_for('main_bp.dashboard'))
        
        propiedades = Propiedad.query.filter(
            Propiedad.propietario_id.in_(owner_ids)
        ).order_by(Propiedad.direccion).all()
    
    return render_template('propiedades.html', propiedades=propiedades)


@propiedades_bp.route('/<int:id>')
@login_required
@owner_access_required()
def ver_propiedad(id):
    """Muestra los detalles de una propiedad específica."""
    
    # El decorador owner_access_required ya valida acceso
    propiedad = db.session.get(Propiedad, id)
    if not propiedad:
        abort(404)
    
    # Obtener contratos relacionados manualmente
    if current_user.role == 'admin':
        contratos = Contrato.query.filter_by(propiedad_id=id).all()
    else:
        # Verificar que la propiedad pertenece a propietarios asignados
        owner_ids = [p.id for p in current_user.propietarios_asignados]
        if propiedad.propietario_id not in owner_ids:
            abort(403)
        contratos = Contrato.query.filter_by(propiedad_id=id).all()
    
    return render_template('ver_propiedad.html', 
                         propiedad=propiedad, 
                         contratos=contratos)


@propiedades_bp.route('/create')
@login_required
@role_required('admin', 'gestor')
def crear_propiedad():
    """Formulario para crear nueva propiedad."""
    
    # Obtener propietarios disponibles según el rol
    if current_user.role == 'admin':
        propietarios = Propietario.query.order_by(Propietario.nombre).all()
    else:
        propietarios = current_user.propietarios_asignados
    
    if not propietarios:
        flash("No hay propietarios disponibles para crear propiedades.", "warning")
        return redirect(url_for('propiedades_bp.listar_propiedades'))
    
    # ... resto de la lógica
```

### Problemas del Código Original

1. **Lógica repetitiva** de verificación de roles
2. **Consultas manuales** para filtrar por propietarios asignados
3. **Validación manual** de acceso a entidades
4. **Código duplicado** en múltiples vistas
5. **Difícil mantenimiento** cuando cambian los requisitos de acceso

## Estado Migrado (Después de Implementar el Sistema)

### Vista de Propiedades Migrada

```python
# myapp/routes/propiedades.py (DESPUÉS)

from ..decorators import filtered_list_view, filtered_detail_view, with_owner_filtering
from ..utils.database_helpers import get_filtered_propiedades, OwnerFilteredQueries

@propiedades_bp.route('/')
@login_required
@filtered_list_view(entity_type='propiedad', log_queries=True)
def listar_propiedades():
    """Lista las propiedades filtradas automáticamente por propietario activo."""
    
    # El filtrado se aplica automáticamente según el propietario activo
    propiedades = get_filtered_propiedades().order_by(Propiedad.direccion).all()
    
    # Las estadísticas están disponibles automáticamente en g.owner_stats
    return render_template('propiedades.html', propiedades=propiedades)


@propiedades_bp.route('/<int:id>')
@login_required
@filtered_detail_view('propiedad', 'id', log_queries=True)
def ver_propiedad(id):
    """Muestra los detalles de una propiedad específica."""
    
    # La validación de acceso se hace automáticamente
    # Solo se ejecuta si el usuario tiene acceso a la propiedad
    propiedad = OwnerFilteredQueries.get_propiedad_by_id(id, include_relations=True)
    
    if not propiedad:
        abort(404)  # No encontrada o sin acceso
    
    # Obtener contratos relacionados (también filtrados automáticamente)
    contratos = get_filtered_contratos(propiedad_id=id).all()
    
    return render_template('ver_propiedad.html', 
                         propiedad=propiedad, 
                         contratos=contratos)


@propiedades_bp.route('/create')
@login_required
@with_owner_filtering(require_active_owner=True)
def crear_propiedad():
    """Formulario para crear nueva propiedad."""
    
    # Los propietarios disponibles están en el contexto automáticamente
    # g.owner_context['available_owners'] contiene los propietarios disponibles
    propietarios = g.owner_context['available_owners']
    
    if not propietarios:
        flash("No hay propietarios disponibles para crear propiedades.", "warning")
        return redirect(url_for('propiedades_bp.listar_propiedades'))
    
    # ... resto de la lógica (simplificada)
```

### Mejoras Logradas

1. **Eliminación de lógica repetitiva** - Los decoradores manejan automáticamente la verificación
2. **Consultas simplificadas** - Las funciones helper aplican filtros automáticamente  
3. **Validación automática** - Los decoradores validan acceso sin código manual
4. **Código más limpio** - Enfoque en la lógica de negocio, no en validaciones
5. **Fácil mantenimiento** - Cambios centralizados en el sistema de filtrado

## Migración de Vista de Contratos

### Antes

```python
# myapp/routes/contratos.py (ANTES)

@contratos_bp.route('/')
@login_required
@role_required('admin', 'gestor', 'usuario')
def listar_contratos():
    if current_user.role == 'admin':
        contratos = Contrato.query.options(
            joinedload(Contrato.propiedad_ref),
            joinedload(Contrato.inquilino_ref)
        ).order_by(Contrato.fecha_inicio.desc()).all()
    else:
        # Obtener contratos de propiedades de propietarios asignados
        owner_ids = [p.id for p in current_user.propietarios_asignados]
        contratos = Contrato.query.join(Propiedad).filter(
            Propiedad.propietario_id.in_(owner_ids)
        ).options(
            joinedload(Contrato.propiedad_ref),
            joinedload(Contrato.inquilino_ref)
        ).order_by(Contrato.fecha_inicio.desc()).all()
    
    return render_template('contratos.html', contratos=contratos)

@contratos_bp.route('/<int:id>')
@login_required
@owner_access_required()
def ver_contrato(id):
    contrato = db.session.get(Contrato, id)
    if not contrato:
        abort(404)
    
    # Validación manual adicional
    if current_user.role != 'admin':
        owner_ids = [p.id for p in current_user.propietarios_asignados]
        if contrato.propiedad_ref.propietario_id not in owner_ids:
            abort(403)
    
    # Obtener facturas del contrato
    facturas = Factura.query.filter_by(contrato_id=id).order_by(
        Factura.fecha_emision.desc()
    ).all()
    
    return render_template('ver_contrato.html', contrato=contrato, facturas=facturas)
```

### Después

```python
# myapp/routes/contratos.py (DESPUÉS)

@contratos_bp.route('/')
@login_required
@filtered_list_view(entity_type='contrato', log_queries=True)
def listar_contratos():
    """Lista contratos filtrados automáticamente."""
    
    # Filtrado automático + relaciones cargadas
    contratos = get_filtered_contratos(
        include_relations=True
    ).order_by(Contrato.fecha_inicio.desc()).all()
    
    return render_template('contratos.html', contratos=contratos)


@contratos_bp.route('/<int:id>')
@login_required
@filtered_detail_view('contrato', 'id', log_queries=True)
def ver_contrato(id):
    """Muestra detalles de un contrato específico."""
    
    # Validación automática de acceso
    contrato = OwnerFilteredQueries.get_contrato_by_id(id, include_relations=True)
    
    if not contrato:
        abort(404)
    
    # Facturas también filtradas automáticamente
    facturas = get_filtered_facturas(contrato_id=id).order_by(
        Factura.fecha_emision.desc()
    ).all()
    
    return render_template('ver_contrato.html', contrato=contrato, facturas=facturas)
```

## Migración de Vista de Facturas

### Antes

```python
# myapp/routes/facturas.py (ANTES)

@facturas_bp.route('/')
@login_required
@role_required('admin', 'gestor', 'usuario')
def listar_facturas():
    # Paginación manual
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    if current_user.role == 'admin':
        facturas_query = Factura.query.options(
            joinedload(Factura.propiedad_ref),
            joinedload(Factura.inquilino_ref)
        )
    else:
        # Filtrar por propietarios asignados
        owner_ids = [p.id for p in current_user.propietarios_asignados]
        facturas_query = Factura.query.join(Propiedad).filter(
            Propiedad.propietario_id.in_(owner_ids)
        ).options(
            joinedload(Factura.propiedad_ref),
            joinedload(Factura.inquilino_ref)
        )
    
    # Filtros adicionales de la request
    estado = request.args.get('estado')
    if estado:
        facturas_query = facturas_query.filter(Factura.estado == estado)
    
    facturas_paginadas = facturas_query.order_by(
        Factura.fecha_emision.desc()
    ).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    return render_template('facturas.html', facturas=facturas_paginadas)

@facturas_bp.route('/dashboard')
@login_required
@role_required('admin', 'gestor', 'usuario')  
def dashboard_facturas():
    """Dashboard con estadísticas de facturas."""
    
    # Cálculos manuales de estadísticas
    if current_user.role == 'admin':
        total_facturas = Factura.query.count()
        facturas_pendientes = Factura.query.filter_by(estado='pendiente').count()
        total_recaudado = db.session.query(func.sum(Factura.total)).filter_by(estado='pagada').scalar() or 0
    else:
        owner_ids = [p.id for p in current_user.propietarios_asignados]
        base_query = Factura.query.join(Propiedad).filter(
            Propiedad.propietario_id.in_(owner_ids)
        )
        
        total_facturas = base_query.count()
        facturas_pendientes = base_query.filter_by(estado='pendiente').count()
        total_recaudado = db.session.query(func.sum(Factura.total)).select_from(
            Factura
        ).join(Propiedad).filter(
            Propiedad.propietario_id.in_(owner_ids),
            Factura.estado == 'pagada'
        ).scalar() or 0
    
    estadisticas = {
        'total_facturas': total_facturas,
        'facturas_pendientes': facturas_pendientes,
        'total_recaudado': total_recaudado
    }
    
    return render_template('dashboard_facturas.html', stats=estadisticas)
```

### Después  

```python
# myapp/routes/facturas.py (DESPUÉS)

@facturas_bp.route('/')
@login_required
@filtered_list_view(entity_type='factura', log_queries=True)
def listar_facturas():
    """Lista facturas con filtrado automático y paginación."""
    
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    # Query base con filtrado automático
    facturas_query = get_filtered_facturas(include_relations=True)
    
    # Filtros adicionales de la request
    estado = request.args.get('estado')
    if estado:
        facturas_query = facturas_query.filter(Factura.estado == estado)
    
    # Paginación
    facturas_paginadas = facturas_query.order_by(
        Factura.fecha_emision.desc()
    ).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    return render_template('facturas.html', facturas=facturas_paginadas)


@facturas_bp.route('/dashboard')
@login_required
@inject_owner_stats()  # Inyecta automáticamente estadísticas
def dashboard_facturas():
    """Dashboard con estadísticas automáticas."""
    
    # Las estadísticas básicas están en g.owner_stats
    # Calcular estadísticas adicionales específicas de facturas
    facturas_query = get_filtered_facturas()
    
    estadisticas_adicionales = {
        'total_recaudado': db.session.query(func.sum(Factura.total)).select_from(
            facturas_query.filter(Factura.estado == 'pagada').subquery()
        ).scalar() or 0,
        'promedio_factura': db.session.query(func.avg(Factura.total)).select_from(
            facturas_query.subquery()
        ).scalar() or 0
    }
    
    return render_template('dashboard_facturas.html', 
                         stats_adicionales=estadisticas_adicionales)
```

## Actualización de Templates

### Template Original

```html
<!-- propiedades.html (ANTES) -->
{% extends "base.html" %}

{% block content %}
<div class="container">
    <h1>Propiedades</h1>
    
    {% if current_user.role == 'admin' %}
        <p class="text-muted">Mostrando todas las propiedades del sistema</p>
    {% else %}
        <p class="text-muted">Mostrando propiedades de tus propietarios asignados</p>
    {% endif %}
    
    <div class="row">
        {% for propiedad in propiedades %}
        <div class="col-md-4 mb-3">
            <div class="card">
                <div class="card-body">
                    <h5 class="card-title">{{ propiedad.direccion }}</h5>
                    <p class="card-text">{{ propiedad.tipo }}</p>
                    <p class="card-text">
                        <small class="text-muted">
                            Propietario: {{ propiedad.propietario_ref.nombre }}
                        </small>
                    </p>
                    <a href="{{ url_for('propiedades_bp.ver_propiedad', id=propiedad.id) }}" 
                       class="btn btn-primary">Ver Detalles</a>
                </div>
            </div>
        </div>
        {% endfor %}
    </div>
</div>
{% endblock %}
```

### Template Actualizado

```html
<!-- propiedades.html (DESPUÉS) -->
{% extends "base.html" %}

{% block content %}
<div class="container">
    <div class="d-flex justify-content-between align-items-center mb-4">
        <h1>Propiedades</h1>
        
        <!-- Información del propietario activo (automática) -->
        {% if has_active_owner %}
            <div class="badge bg-primary">
                Viendo: {{ active_owner.nombre }}
            </div>
        {% endif %}
    </div>
    
    <!-- Estadísticas automáticas -->
    {% if owner_stats %}
    <div class="row mb-4">
        <div class="col-md-3">
            <div class="card bg-light">
                <div class="card-body text-center">
                    <h3>{{ owner_stats.propiedades_count }}</h3>
                    <p class="text-muted">Propiedades</p>
                </div>
            </div>
        </div>
        <div class="col-md-3">
            <div class="card bg-light">
                <div class="card-body text-center">
                    <h3>{{ owner_stats.contratos_activos }}</h3>
                    <p class="text-muted">Contratos Activos</p>
                </div>
            </div>
        </div>
        <!-- Más estadísticas... -->
    </div>
    {% endif %}
    
    <!-- Cambio de propietario (si disponible) -->
    {% if user_can_change_owner and available_owners|length > 1 %}
    <div class="mb-3">
        <select class="form-select" onchange="changeOwner(this.value)">
            <option value="">Cambiar propietario...</option>
            {% for owner in available_owners %}
                {% if owner.id != active_owner.id %}
                <option value="{{ owner.id }}">{{ owner.nombre }}</option>
                {% endif %}
            {% endfor %}
        </select>
    </div>
    {% endif %}
    
    <div class="row">
        {% for propiedad in propiedades %}
        <div class="col-md-4 mb-3">
            <div class="card">
                <div class="card-body">
                    <h5 class="card-title">{{ propiedad.direccion }}</h5>
                    <p class="card-text">{{ propiedad.tipo }}</p>
                    <!-- El propietario ya no se muestra porque es redundante -->
                    <a href="{{ url_for('propiedades_bp.ver_propiedad', id=propiedad.id) }}" 
                       class="btn btn-primary">Ver Detalles</a>
                </div>
            </div>
        </div>
        {% endfor %}
    </div>
    
    {% if not propiedades %}
        <div class="alert alert-info">
            {% if not has_active_owner %}
                <p>Selecciona un propietario para ver las propiedades.</p>
                <a href="{{ url_for('owner_selector_bp.select_owner') }}" 
                   class="btn btn-primary">Seleccionar Propietario</a>
            {% else %}
                <p>No hay propiedades para el propietario seleccionado.</p>
            {% endif %}
        </div>
    {% endif %}
</div>

<script>
function changeOwner(ownerId) {
    if (!ownerId) return;
    
    fetch('{{ url_for("owner_selector_bp.api_change_owner") }}', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({propietario_id: parseInt(ownerId)})
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            location.reload();
        } else {
            alert('Error: ' + data.message);
        }
    });
}
</script>
{% endblock %}
```

## Checklist de Migración

### Paso 1: Preparación
- [ ] Verificar que el sistema de filtrado automático esté implementado
- [ ] Identificar vistas que necesitan migración
- [ ] Revisar decoradores actuales en las vistas
- [ ] Documentar lógica de filtrado actual

### Paso 2: Migración de Decoradores
- [ ] Reemplazar `@role_required` con decoradores de filtrado
- [ ] Usar `@filtered_list_view` para vistas de listado
- [ ] Usar `@filtered_detail_view` para vistas de detalle
- [ ] Agregar `@with_owner_filtering` donde sea necesario

### Paso 3: Migración de Consultas
- [ ] Reemplazar consultas manuales con funciones helper
- [ ] Usar `get_filtered_*()` en lugar de queries directas
- [ ] Eliminar lógica manual de filtrado por roles
- [ ] Simplificar validaciones de acceso

### Paso 4: Actualización de Templates
- [ ] Aprovechar variables automáticas (`active_owner`, `owner_stats`)
- [ ] Implementar cambio dinámico de propietario
- [ ] Actualizar mensajes de estado y navegación
- [ ] Remover lógica redundante de mostrar propietario

### Paso 5: Pruebas
- [ ] Probar con diferentes roles de usuario
- [ ] Verificar filtrado correcto por propietario activo
- [ ] Confirmar validación de acceso a entidades
- [ ] Validar funcionalidad de cambio de propietario

### Paso 6: Optimización
- [ ] Revisar logs de consultas filtradas
- [ ] Optimizar queries con `include_relations=True`
- [ ] Ajustar paginación si es necesario
- [ ] Configurar logging de debug si se requiere

## Beneficios de la Migración

### Reducción de Código
- **Antes**: ~150 líneas por vista compleja
- **Después**: ~50 líneas por vista equivalente
- **Reducción**: ~67% menos código

### Mejora en Mantenibilidad
- Lógica de filtrado centralizada
- Validaciones automáticas
- Menor probabilidad de errores de seguridad
- Fácil extensión para nuevos requisitos

### Experiencia de Usuario Mejorada
- Cambio dinámico de propietario
- Estadísticas automáticas
- Navegación más intuitiva
- Mejor feedback visual

### Seguridad Reforzada
- Filtrado automático garantizado
- Validación centralizada de acceso
- Menor superficie de ataque
- Logs detallados para auditoría

## Conclusión

La migración al sistema de filtrado automático simplifica significativamente el código de las vistas, mejora la seguridad y proporciona una mejor experiencia de usuario. El proceso de migración es sistemático y los beneficios justifican ampliamente el esfuerzo invertido.
