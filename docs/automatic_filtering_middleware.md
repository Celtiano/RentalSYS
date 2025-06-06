# Sistema de Middleware de Filtrado Automático - RentalSYS

## Descripción General

El Sistema de Middleware de Filtrado Automático intercepta y filtra automáticamente todas las consultas de la aplicación por el propietario activo seleccionado en la sesión. Este sistema proporciona una capa transparente de filtrado que garantiza que los usuarios solo vean datos relevantes a sus propietarios asignados.

## Arquitectura del Sistema

### Componentes Principal

1. **Query Filters** (`myapp/utils/query_filters.py`)
   - Sistema de interceptación de consultas SQLAlchemy
   - Configuración de filtros por modelo
   - Gestión de estado de filtrado global

2. **Database Helpers** (`myapp/utils/database_helpers.py`)
   - Funciones auxiliares para consultas filtradas
   - Métodos de conveniencia para cada modelo
   - Context managers para bypass temporal

3. **Decoradores Avanzados** (`myapp/decorators.py`)
   - Decoradores para configurar contexto de filtrado
   - Validación automática de acceso a entidades
   - Combinaciones predefinidas para casos comunes

4. **Context Processors** (en `myapp/__init__.py`)
   - Inyección automática de variables en templates
   - Estadísticas del propietario activo
   - Estado de filtrado para debugging

5. **Middleware** (en `myapp/__init__.py`)
   - Validación de sesión antes de cada request
   - Configuración de contexto de filtrado
   - Logging de actividad de filtrado

## Configuración de Filtros por Modelo

### Modelos Filtrados Automáticamente

```python
FILTERED_MODELS = {
    'Propiedad': {
        'model': Propiedad,
        'filter_field': 'propietario_id',
        'filter_type': 'direct'  # Filtrado directo
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
        'filter_type': 'nested_subquery',  # Dos niveles de JOIN
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
        'filter_type': 'complex_subquery'  # Lógica personalizada
    }
}
```

### Tipos de Filtrado

1. **Direct**: Filtrado directo por campo
   ```sql
   WHERE propietario_id = ?
   ```

2. **Subquery**: Filtrado mediante subquery simple
   ```sql
   WHERE propiedad_id IN (SELECT id FROM propiedad WHERE propietario_id = ?)
   ```

3. **Nested Subquery**: Filtrado con subquery anidada
   ```sql
   WHERE contrato_id IN (
       SELECT id FROM contrato WHERE propiedad_id IN (
           SELECT id FROM propiedad WHERE propietario_id = ?
       )
   )
   ```

4. **Complex Subquery**: Lógica personalizada
   ```sql
   WHERE id IN (
       SELECT DISTINCT inquilino_id FROM contrato 
       JOIN propiedad ON contrato.propiedad_id = propiedad.id 
       WHERE propiedad.propietario_id = ?
   )
   ```

## Uso de Funciones de Database Helpers

### Funciones Básicas de Consulta

```python
from myapp.utils.database_helpers import (
    get_filtered_propiedades,
    get_filtered_contratos,
    get_filtered_facturas,
    get_filtered_gastos,
    get_filtered_inquilinos,
    get_filtered_documentos
)

# En una vista de propiedades
@app.route('/propiedades')
@login_required
@filtered_view()
def listar_propiedades():
    # Automáticamente filtradas por propietario activo
    propiedades = get_filtered_propiedades().all()
    return render_template('propiedades.html', propiedades=propiedades)

# Con filtros adicionales
@app.route('/contratos/activos')
@login_required
@filtered_view()
def contratos_activos():
    contratos = get_filtered_contratos(estado='activo').all()
    return render_template('contratos.html', contratos=contratos)
```

### Clase OwnerFilteredQueries

```python
from myapp.utils.database_helpers import OwnerFilteredQueries

# Métodos estáticos para consultas específicas
def mi_vista():
    # Obtener propiedades con relaciones cargadas
    propiedades = OwnerFilteredQueries.get_propiedades(
        include_relations=True
    ).all()
    
    # Obtener una propiedad específica
    propiedad = OwnerFilteredQueries.get_propiedad_by_id(123)
    
    # Validar acceso a una entidad
    tiene_acceso = OwnerFilteredQueries.validate_access_to_entity(
        'propiedad', 123
    )
    
    # Obtener estadísticas
    stats = OwnerFilteredQueries.get_stats_for_active_owner()
```

### Bypass Temporal del Filtrado

```python
from myapp.utils.database_helpers import bypass_owner_filtering

# Context manager para bypass
with bypass_owner_filtering() as bypass:
    # Estas consultas no serán filtradas
    todas_propiedades = bypass.get_propiedades().all()
    todos_contratos = bypass.get_contratos().all()

# O usar parámetro apply_filter=False
propiedades_sin_filtro = OwnerFilteredQueries.get_propiedades(
    apply_filter=False
).all()
```

## Decoradores de Filtrado

### Decoradores Básicos

```python
from myapp.decorators import (
    with_owner_filtering,
    filtered_view,
    validate_entity_access,
    inject_owner_stats
)

# Configuración básica de filtrado
@app.route('/mi-ruta')
@login_required
@with_owner_filtering()
def mi_vista():
    # g.owner_filtering_enabled = True
    # g.active_owner = propietario activo
    # g.owner_context = contexto completo
    pass

# Decorador combinado completo
@app.route('/dashboard')
@login_required
@filtered_view(log_queries=True)
def dashboard():
    # Incluye validación, filtrado y logging
    pass

# Validación de acceso a entidad específica
@app.route('/propiedad/<int:id>')
@login_required
@validate_entity_access('propiedad', 'id')
def ver_propiedad(id):
    # g.validated_entity_type = 'propiedad'
    # g.validated_entity_id = id
    # Solo ejecuta si el usuario tiene acceso
    pass

# Inyección de estadísticas
@app.route('/dashboard')
@login_required
@inject_owner_stats()
def dashboard():
    # g.owner_stats = estadísticas del propietario activo
    pass
```

### Decoradores Combinados

```python
from myapp.decorators import filtered_list_view, filtered_detail_view

# Para vistas de listado
@app.route('/propiedades')
@login_required
@filtered_list_view(entity_type='propiedad', log_queries=True)
def listar_propiedades():
    # Incluye filtrado + estadísticas + logging
    propiedades = get_filtered_propiedades().all()
    return render_template('propiedades.html', propiedades=propiedades)

# Para vistas de detalle
@app.route('/propiedad/<int:id>')
@login_required
@filtered_detail_view('propiedad', 'id', log_queries=True)
def ver_propiedad(id):
    # Incluye filtrado + validación de acceso + logging
    propiedad = OwnerFilteredQueries.get_propiedad_by_id(id)
    return render_template('ver_propiedad.html', propiedad=propiedad)
```

## Variables de Template Inyectadas

### Variables Automáticas

El sistema inyecta automáticamente las siguientes variables en todos los templates:

```html
<!-- Información del propietario activo -->
{% if active_owner %}
    <h2>Propietario: {{ active_owner.nombre }}</h2>
    <p>NIF: {{ active_owner.nif }}</p>
{% endif %}

<!-- Lista de propietarios disponibles -->
{% if available_owners %}
    <select name="cambiar_propietario">
        {% for owner in available_owners %}
            <option value="{{ owner.id }}" 
                    {% if owner.id == active_owner.id %}selected{% endif %}>
                {{ owner.nombre }}
            </option>
        {% endfor %}
    </select>
{% endif %}

<!-- Estado del propietario activo -->
{% if has_active_owner %}
    <p>Hay propietario activo seleccionado</p>
{% else %}
    <p>No hay propietario seleccionado</p>
{% endif %}

<!-- Capacidad de cambio -->
{% if user_can_change_owner %}
    <button onclick="showOwnerSelector()">Cambiar Propietario</button>
{% endif %}

<!-- Contexto completo -->
{{ owner_context.active_owner.nombre }}
{{ owner_context.available_owners|length }}

<!-- Estadísticas del propietario -->
{% if owner_stats %}
    <div class="stats">
        <p>Propiedades: {{ owner_stats.propiedades_count }}</p>
        <p>Contratos activos: {{ owner_stats.contratos_activos }}</p>
        <p>Facturas pendientes: {{ owner_stats.facturas_pendientes }}</p>
        <p>Inquilinos: {{ owner_stats.inquilinos_count }}</p>
    </div>
{% endif %}
```

### Variables de Debug (solo en desarrollo)

```html
{% if debug_mode %}
    <div class="debug-info">
        <p>Filtrado habilitado: {{ filtering_enabled }}</p>
        <p>Rol usuario: {{ g.debug_filtering_info.user_role }}</p>
        <p>ID propietario activo: {{ g.debug_filtering_info.active_owner_id }}</p>
    </div>
{% endif %}
```

## Middleware y Context Processors

### Context Processors Configurados

1. **inject_owner_context**: Inyecta información del propietario activo
2. **inject_filtering_status**: Estado del filtrado (solo en debug)
3. **inject_owner_stats**: Estadísticas del propietario activo

### Middleware de Request

1. **validate_owner_session**: Valida integridad de la sesión
2. **setup_filtering_context**: Configura contexto en `g`
3. **log_filtering_activity**: Logging post-request (solo en debug)

### Configuración en Aplicación

```python
# En myapp/__init__.py

# Context processors se registran automáticamente
@app.context_processor
def inject_owner_context():
    # Se ejecuta en cada render de template
    return owner_context_data

# Middleware se registra automáticamente
@app.before_request
def setup_filtering_context():
    # Se ejecuta antes de cada request
    g.active_owner = get_active_owner()
    g.filtering_should_apply = should_apply_filter()

@app.after_request
def log_filtering_activity(response):
    # Se ejecuta después de cada request
    if app.debug:
        log_filtering_info()
    return response
```

## Compatibilidad con Roles

### Comportamiento por Rol

1. **Administrador (admin)**:
   - Puede ver todos los datos sin filtrar por defecto
   - Si selecciona un propietario activo, se respeta el filtro
   - Puede usar bypass del filtrado cuando sea necesario

2. **Gestor (gestor)**:
   - Solo ve datos de propietarios asignados
   - Debe tener propietario activo para acceder a vistas filtradas
   - No puede bypass el filtrado

3. **Usuario (usuario)**:
   - Solo ve datos de propietarios asignados
   - Debe tener propietario activo para acceder a vistas filtradas
   - No puede bypass el filtrado

### Configuración de Bypass para Admin

```python
from myapp.decorators import admin_or_filtered

@app.route('/admin/all-data')
@login_required
@admin_or_filtered(bypass_for_admin=True)
def admin_all_data():
    if current_user.role == 'admin' and not has_active_owner():
        # Admin ve todo sin filtrar
        propiedades = Propiedad.query.all()
    else:
        # Filtrado normal
        propiedades = get_filtered_propiedades().all()
    
    return render_template('admin_data.html', propiedades=propiedades)
```

## Logging y Debugging

### Configuración de Logging

```python
# En config.py o variables de entorno
DEBUG = True
LOG_FILTERING_ACTIVITY = True
INJECT_FILTERING_DEBUG = True

# En la aplicación Flask
app.config['LOG_FILTERING_ACTIVITY'] = True
app.config['INJECT_FILTERING_DEBUG'] = True
```

### Logs Generados

```
DEBUG - Filtrado - GET propiedades_bp.listar: Usuario: gestor1 (gestor), Propietario activo: 3, Filtrado aplicado: True
DEBUG - Vista listar_propiedades (propiedad) iniciada
DEBUG - Vista listar_propiedades (propiedad) completada
```

### Funciones de Debug

```python
from myapp.utils.query_filters import get_filtering_status, log_filtering_status

# En una vista de debug
def debug_filtering():
    status = get_filtering_status()
    log_filtering_status()
    return jsonify(status)
```

## Migración de Vistas Existentes

### Antes (sin filtrado automático)

```python
@app.route('/propiedades')
@login_required
@role_required('gestor', 'usuario')
def listar_propiedades():
    if current_user.role == 'admin':
        propiedades = Propiedad.query.all()
    else:
        # Lógica manual de filtrado
        owner_ids = [p.id for p in current_user.propietarios_asignados]
        propiedades = Propiedad.query.filter(
            Propiedad.propietario_id.in_(owner_ids)
        ).all()
    
    return render_template('propiedades.html', propiedades=propiedades)
```

### Después (con filtrado automático)

```python
@app.route('/propiedades')
@login_required
@filtered_list_view(entity_type='propiedad')
def listar_propiedades():
    # Filtrado automático aplicado
    propiedades = get_filtered_propiedades().all()
    return render_template('propiedades.html', propiedades=propiedades)
```

### Migración Paso a Paso

1. **Reemplazar decorador de rol**:
   ```python
   # Antes
   @role_required('gestor', 'usuario')
   
   # Después
   @filtered_view()
   ```

2. **Simplificar consultas**:
   ```python
   # Antes
   if current_user.role == 'admin':
       facturas = Factura.query.all()
   else:
       facturas = Factura.query.join(Propiedad).filter(...)
   
   # Después
   facturas = get_filtered_facturas().all()
   ```

3. **Usar validación de acceso**:
   ```python
   # Antes
   @app.route('/propiedad/<int:id>')
   def ver_propiedad(id):
       propiedad = Propiedad.query.get_or_404(id)
       # Lógica manual de validación
       if not user_has_access_to_propiedad(propiedad):
           abort(403)
   
   # Después
   @filtered_detail_view('propiedad', 'id')
   def ver_propiedad(id):
       propiedad = OwnerFilteredQueries.get_propiedad_by_id(id)
       # Validación automática
   ```

## Mejores Prácticas

### Uso Recomendado

1. **Para vistas de listado**:
   ```python
   @filtered_list_view(entity_type='entidad')
   def listar_entidades():
       entidades = get_filtered_entidades().all()
       return render_template('list.html', entidades=entidades)
   ```

2. **Para vistas de detalle**:
   ```python
   @filtered_detail_view('entidad', 'id')
   def ver_entidad(id):
       entidad = OwnerFilteredQueries.get_entidad_by_id(id)
       return render_template('detail.html', entidad=entidad)
   ```

3. **Para APIs**:
   ```python
   @app.route('/api/propiedades')
   @login_required
   @with_owner_filtering()
   def api_propiedades():
       propiedades = get_filtered_propiedades().all()
       return jsonify([p.to_dict() for p in propiedades])
   ```

### Evitar

1. **No usar consultas directas en vistas filtradas**:
   ```python
   # ❌ Evitar
   propiedades = Propiedad.query.all()  # No respeta filtrado
   
   # ✅ Usar
   propiedades = get_filtered_propiedades().all()
   ```

2. **No bypass innecesario**:
   ```python
   # ❌ Evitar bypass sin justificación
   with bypass_owner_filtering():
       data = get_all_data()
   
   # ✅ Solo cuando sea necesario para admin
   if current_user.role == 'admin' and specific_condition:
       with bypass_owner_filtering():
           data = get_admin_data()
   ```

## Resolución de Problemas

### Problemas Comunes

1. **Consultas no filtradas**:
   - Verificar uso de funciones `get_filtered_*`
   - Confirmar decorador aplicado correctamente
   - Revisar logs de filtrado

2. **Acceso denegado inesperado**:
   - Verificar propietario activo en sesión
   - Confirmar asignación de propietarios al usuario
   - Revisar validación de entidad específica

3. **Variables de template no disponibles**:
   - Verificar context processors registrados
   - Confirmar middleware configurado
   - Revisar errores en logs

### Debugging

```python
# Verificar estado de filtrado
def debug_view():
    from myapp.utils.query_filters import get_filtering_status
    from myapp.utils.database_helpers import OwnerFilteredQueries
    
    status = get_filtering_status()
    should_filter = OwnerFilteredQueries.should_apply_filter()
    
    return jsonify({
        'filtering_status': status,
        'should_filter': should_filter,
        'active_owner': g.get('active_owner', {}).id if g.get('active_owner') else None
    })
```

## Conclusión

El Sistema de Middleware de Filtrado Automático proporciona una solución completa y transparente para filtrar datos por propietario activo en RentalSYS. Su diseño modular permite fácil mantenimiento y extensión, mientras que los decoradores y funciones auxiliares simplifican significativamente el desarrollo de nuevas funcionalidades.

La implementación garantiza seguridad a nivel de datos, compatibilidad con el sistema de roles existente, y proporciona herramientas robustas para debugging y monitoreo del sistema de filtrado.
