# Sistema de Gestión de Propietario Activo - RentalSYS

## Descripción General

El Sistema de Gestión de Propietario Activo permite a los usuarios gestionar fácilmente el propietario con el que están trabajando en cada sesión. Este sistema proporciona una experiencia de usuario fluida y segura para cambiar entre diferentes propietarios según los permisos asignados.

## Componentes Implementados

### 1. Funciones Auxiliares (`myapp/utils/owner_session.py`)

**Funciones principales:**
- `set_active_owner(propietario_id)` - Establece el propietario activo
- `get_active_owner()` - Obtiene el objeto del propietario activo
- `get_active_owner_id()` - Obtiene solo el ID del propietario activo
- `clear_active_owner()` - Limpia la sesión del propietario activo
- `user_has_access_to_owner(propietario_id)` - Verifica permisos de acceso
- `get_user_available_owners()` - Lista de propietarios disponibles
- `has_active_owner()` - Verifica si hay propietario activo
- `auto_select_owner_if_needed()` - Selección automática cuando corresponde
- `validate_session_integrity()` - Valida la integridad de la sesión
- `get_active_owner_context()` - Contexto completo para templates

### 2. Decorador Personalizado (`myapp/decorators.py`)

**Decorador principal:**
```python
@active_owner_required(auto_select=True, redirect_to_selector=True)
```

**Parámetros:**
- `auto_select`: Intenta selección automática si hay exactamente un propietario
- `redirect_to_selector`: Redirige al selector o devuelve error 400

**Uso típico:**
```python
@app.route('/mi-ruta')
@login_required
@active_owner_required()
def mi_vista():
    active_owner = get_active_owner()
    # Tu lógica aquí
```

### 3. Blueprint de API (`myapp/routes/owner_selector.py`)

**Endpoints disponibles:**

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/owner-selector/select` | GET/POST | Página de selección de propietario |
| `/owner-selector/api/change` | POST | Cambiar propietario (AJAX) |
| `/owner-selector/api/current` | GET | Información del propietario actual |
| `/owner-selector/api/clear` | POST | Limpiar propietario activo |
| `/owner-selector/api/auto-select` | POST | Selección automática |
| `/owner-selector/widget` | GET | Widget para incluir en otras páginas |

### 4. Templates

**Archivos de template:**
- `templates/owner_selector/select_owner.html` - Página principal de selección
- `templates/owner_selector/widget.html` - Widget para incluir en otras páginas

### 5. Middleware de Validación

Middleware automático que valida la integridad de la sesión del propietario activo en cada request para usuarios autenticados.

## Características Principales

### 🔐 Seguridad
- Validación automática de permisos de usuario
- Verificación de acceso a propietarios específicos
- Limpieza automática de sesiones inválidas
- Protección contra acceso no autorizado

### 🎨 Interfaz de Usuario
- Diseño responsivo con Tailwind CSS
- Soporte completo para modo oscuro
- Interfaz AJAX para cambios dinámicos
- Widget integrado en el header de todas las páginas

### ⚡ Funcionalidad Avanzada
- Selección automática cuando el usuario tiene acceso a exactamente un propietario
- Persistencia de propietario activo en la sesión
- Validación de integridad en cada request
- Soporte para peticiones AJAX y navegación normal

### 📱 Experiencia de Usuario
- Cambio dinámico de propietario sin recargar página
- Indicador visual del propietario activo
- Navegación intuitiva entre propietarios
- Mensajes informativos y de error claros

## Roles y Permisos

### Administrador (`admin`)
- Acceso a todos los propietarios
- Puede gestionar cualquier propietario sin restricciones

### Gestor (`gestor`)
- Acceso solo a propietarios asignados
- Puede cambiar entre sus propietarios asignados

### Usuario (`usuario`)
- Acceso solo a propietarios asignados
- Puede cambiar entre sus propietarios asignados

## Casos de Uso

### 1. Usuario con Múltiples Propietarios
```python
# El usuario ve un dropdown en el header para cambiar propietario
# Puede usar el widget o ir a la página de selección completa
```

### 2. Usuario con Un Solo Propietario
```python
# Se selecciona automáticamente
# No se muestra el dropdown de cambio
```

### 3. Usuario sin Propietarios Asignados
```python
# Se muestra mensaje de contactar al administrador
# Se redirige al dashboard con mensaje informativo
```

### 4. Validación de Acceso en Vistas
```python
@app.route('/propiedades')
@login_required
@active_owner_required()
def listar_propiedades():
    active_owner = get_active_owner()
    # Solo ve propiedades del propietario activo
    propiedades = Propiedad.query.filter_by(propietario_id=active_owner.id).all()
    return render_template('propiedades.html', propiedades=propiedades)
```

## API de JavaScript

### Cambiar Propietario (AJAX)
```javascript
fetch('/owner-selector/api/change', {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest'
    },
    body: JSON.stringify({
        propietario_id: 123
    })
})
.then(response => response.json())
.then(data => {
    if (data.success) {
        // Éxito: data.message, data.active_owner
        location.reload(); // O actualizar UI dinámicamente
    } else {
        // Error: data.error, data.message
    }
});
```

### Obtener Propietario Actual
```javascript
fetch('/owner-selector/api/current')
.then(response => response.json())
.then(data => {
    console.log('Propietario activo:', data.active_owner);
    console.log('Propietarios disponibles:', data.available_owners);
});
```

## Integración en Vistas Existentes

### 1. Agregar Decorador a Vistas
```python
# Antes
@app.route('/mi-vista')
@login_required
@role_required('gestor', 'usuario')
def mi_vista():
    # ...

# Después
@app.route('/mi-vista')
@login_required
@role_required('gestor', 'usuario')
@active_owner_required()
def mi_vista():
    active_owner = get_active_owner()
    # Usar active_owner en tu lógica
```

### 2. Filtrar Datos por Propietario Activo
```python
def listar_propiedades():
    active_owner = get_active_owner()
    
    if current_user.role == 'admin':
        # Admin puede ver todas, pero respeta propietario activo si está establecido
        if active_owner:
            propiedades = Propiedad.query.filter_by(propietario_id=active_owner.id).all()
        else:
            propiedades = Propiedad.query.all()
    else:
        # Gestor/Usuario solo ve del propietario activo
        propiedades = Propiedad.query.filter_by(propietario_id=active_owner.id).all()
    
    return render_template('propiedades.html', propiedades=propiedades)
```

### 3. Usar Contexto en Templates
```html
<!-- El contexto owner_context está disponible automáticamente -->
{% if owner_context.has_active_owner %}
    <p>Trabajando con: {{ owner_context.active_owner.nombre }}</p>
{% else %}
    <p>No hay propietario seleccionado</p>
{% endif %}
```

## Personalización

### Widget Personalizado
El widget se puede personalizar modificando `templates/owner_selector/widget.html`:

```html
<!-- Versión minimal del widget -->
<div class="bg-gray-100 p-2 rounded">
    {% if owner_context.has_active_owner %}
        <span>{{ owner_context.active_owner.nombre }}</span>
        {% if owner_context.can_change_owner %}
            <a href="{{ url_for('owner_selector_bp.select_owner') }}">Cambiar</a>
        {% endif %}
    {% else %}
        <a href="{{ url_for('owner_selector_bp.select_owner') }}">Seleccionar Propietario</a>
    {% endif %}
</div>
```

### Estilos Personalizados
Los estilos están basados en Tailwind CSS y se pueden personalizar modificando las clases en los templates.

## Consideraciones de Rendimiento

### Optimizaciones Implementadas
- Validación de sesión solo cuando es necesario
- Caché de propietarios disponibles durante la sesión
- Consultas SQL optimizadas
- Lazy loading de relaciones

### Recomendaciones
- Usar índices en las tablas relacionadas
- Considerar caché Redis para sesiones en producción
- Monitorear queries SQL en desarrollo

## Resolución de Problemas

### Problemas Comunes

1. **Usuario no puede ver propietarios**
   - Verificar que el usuario tenga propietarios asignados
   - Revisar la tabla `user_propietario_association`

2. **Propietario activo se pierde**
   - Verificar configuración de sesiones de Flask
   - Comprobar que `SECRET_KEY` esté configurado

3. **Errores de permisos**
   - Verificar que se use `@login_required` antes de `@active_owner_required`
   - Comprobar que el usuario tenga el rol correcto

4. **Widget no aparece**
   - Verificar que el template base incluya el widget
   - Comprobar que el context processor esté funcionando

### Logs de Depuración
El sistema incluye logging detallado que se puede revisar en los logs de la aplicación para diagnosticar problemas.

## Migración y Mantenimiento

### Datos Requeridos
El sistema requiere que existan:
- Tabla `user` con usuarios y roles
- Tabla `propietario` con propietarios
- Tabla de asociación `user_propietario` con relaciones

### Actualizaciones Futuras
- Soporte para múltiples propietarios activos simultáneos
- Integración con notificaciones push
- API REST completa para aplicaciones móviles
- Dashboard de estadísticas de uso

## Conclusión

El Sistema de Gestión de Propietario Activo proporciona una base sólida y extensible para gestionar el contexto de propietario en RentalSYS. Su diseño modular permite fácil mantenimiento y extensión según las necesidades futuras del proyecto.
