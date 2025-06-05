# myapp/routes/main.py  ·  v4 Corregido
import os, uuid
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
from flask_login import login_required, current_user
from flask import (
    Blueprint, render_template, flash, redirect, url_for,
    request, current_app, g, abort, send_from_directory
)
from sqlalchemy import func, extract, desc, or_
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import joinedload
from werkzeug.utils import secure_filename

from myapp.models import (
    db, Propiedad, Contrato, Factura, SystemSettings,
    UPLOAD_FOLDER_LOGOS_REL, Notification, Gasto, Inquilino, Propietario
)

# Importar meses para IPC
from ..routes.ipc import MESES_STR

from ..decorators import role_required

from ..forms import CSRFOnlyForm 
from ..models import ( db, Propiedad, Contrato, Factura, SystemSettings,
    Notification, Gasto, Inquilino, Propietario, User 
)
main_bp = Blueprint('main_bp', __name__)

# ---------- Filtros Jinja (sin cambios) ----------
# def format_currency_safe(value):
    # if value is None: return ""
    # try:
        # # Convertir a Decimal si no lo es, para asegurar precisión
        # dec_value = value if isinstance(value, Decimal) else Decimal(str(value))
        # # Formatear con separador de miles punto y decimal coma
        # num_str = "{:,.2f}".format(dec_value) # Esto usa coma para miles y punto para decimal
        # int_part, dec_part = num_str.split('.')
        # int_part_formatted = int_part.replace(',', '.') # Cambia coma de miles por punto
        # # Usar la moneda de los ajustes si está disponible
        # currency_symbol = "€" # Default
        # if hasattr(g, 'settings') and g.settings and g.settings.currency:
            # if g.settings.currency == "USD": currency_symbol = "$"
            # elif g.settings.currency == "GBP": currency_symbol = "£"
        # return f"{int_part_formatted},{dec_part} {currency_symbol}"
    # except (ValueError, TypeError, InvalidOperation):
        # return str(value) # Fallback


# @main_bp.app_template_filter('date')
# def format_date_filter(value, fmt=None):
    # print(f"DEBUG FILTER 'date': Recibido valor = {value}, tipo = {type(value)}, fmt = {fmt}") # LOG 1
    # if not value or not isinstance(value, (date, datetime)):
        # print("DEBUG FILTER 'date': Valor es None o tipo incorrecto, devolviendo ''") # LOG 2
        # return ""

    # date_format_setting = fmt
    # settings_date_format = None
    # if hasattr(g, 'settings') and g.settings: # Comprobar si g.settings existe
        # settings_date_format = getattr(g.settings, 'date_format', None) # Usar getattr para evitar AttributeError
        # print(f"DEBUG FILTER 'date': g.settings.date_format = {settings_date_format}") # LOG 3
    # else:
        # print("DEBUG FILTER 'date': g.settings NO está disponible") # LOG 4

    # if settings_date_format:
        # date_format_setting = settings_date_format
    
    # if not date_format_setting: # Si fmt era None y g.settings.date_format no existía
         # date_format_setting = '%d/%m/%Y'
    
    # print(f"DEBUG FILTER 'date': Usando formato final = {date_format_setting}") # LOG 5
    # try:
        # formatted_date = value.strftime(date_format_setting)
        # print(f"DEBUG FILTER 'date': Fecha formateada = {formatted_date}") # LOG 6
        # return formatted_date
    # except Exception as e:
        # current_app.logger.error(f"Error en filtro 'date' formateando '{value}' con '{date_format_setting}': {e}")
        # print(f"DEBUG FILTER 'date': EXCEPCIÓN strftime: {e}, devolviendo str(value)") # LOG 7
        # try:
            # return value.isoformat() # Fallback más robusto
        # except:
            # return str(value)

# @main_bp.app_template_filter('currency')
# def currency_filter(value):
    # print(f"DEBUG FILTER 'currency': Recibido valor = {value}, tipo = {type(value)}") # LOG 8
    # if value is None:
        # print("DEBUG FILTER 'currency': Valor es None, devolviendo ''") # LOG 9
        # return ""
    # try:
        # dec_value = value if isinstance(value, Decimal) else Decimal(str(value))
        # num_str = "{:,.2f}".format(dec_value)
        # int_part, dec_part = num_str.split('.')
        # int_part_formatted = int_part.replace(',', '.')
        
        # currency_symbol = "€" # Default
        # settings_currency = None
        # if hasattr(g, 'settings') and g.settings: # Comprobar si g.settings existe
            # settings_currency = getattr(g.settings, 'currency', None)
            # print(f"DEBUG FILTER 'currency': g.settings.currency = {settings_currency}") # LOG 10
        # else:
             # print("DEBUG FILTER 'currency': g.settings NO está disponible") # LOG 11

        # if settings_currency:
            # if settings_currency == "USD": currency_symbol = "$"
            # elif settings_currency == "GBP": currency_symbol = "£"
        
        # formatted_currency = f"{int_part_formatted},{dec_part} {currency_symbol}"
        # print(f"DEBUG FILTER 'currency': Moneda formateada = {formatted_currency}") # LOG 12
        # return formatted_currency
    # except (ValueError, TypeError, InvalidOperation) as e:
        # current_app.logger.error(f"Error en filtro 'currency' formateando '{value}': {e}")
        # print(f"DEBUG FILTER 'currency': EXCEPCIÓN formateando: {e}, devolviendo str(value)") # LOG 13
        # return str(value)


# ---------- Helpers ----------
def normalize_percent(raw):
    """
    Convierte entrada (str, int, float, Decimal o None) en Decimal entre 0 y 1.
    Ej: '21,5' -> Decimal('0.215'). Devuelve el default si falla.
    """
    if raw in (None, '', False):
        return None # O podría devolver 0.0 si se prefiere un valor numérico

    try:
        # Forzar a string y normalizar separador decimal a punto
        txt = str(raw).replace(',', '.').strip()
        if not txt: return None # Si era solo espacios

        # Intentar convertir a Decimal
        pct = Decimal(txt)

        # Si es > 1, asumir que es un porcentaje (ej: 21) y dividir por 100
        if abs(pct) > 1:
             pct /= 100

        # Asegurar que está en un rango razonable (ej: -100% a +1000%)
        if not (Decimal('-1.0') <= pct <= Decimal('10.0')):
             # Podríamos lanzar error o loguear y devolver None/default
             current_app.logger.warning(f"Valor de porcentaje '{raw}' resulta en '{pct}', fuera de rango esperado (-1 a 10).")
             # raise ValueError(f"Porcentaje '{raw}' fuera de rango esperado.")
             return None # O devolver la tasa por defecto aquí

        return pct # Devuelve Decimal (ej: 0.21)

    except (InvalidOperation, ValueError) as e:
        # Loguear el error y devolver None o lanzar excepción
        current_app.logger.error(f"Error convirtiendo porcentaje '{raw}' a Decimal: {e}")
        raise ValueError(f"«{raw}» no es un porcentaje válido.")
        # return None # O devolver la tasa por defecto


# ---------- Context processors ----------
@main_bp.app_context_processor
def inject_today_date(): return dict(today_date=date.today().isoformat())

# @main_bp.app_context_processor
# def utility_processor(): return dict(now=datetime.utcnow)

# --- NUEVO: Context Processor para Contar Notificaciones No Leídas ---
@main_bp.app_context_processor
def inject_unread_notifications_count():
    """Inyecta el número de notificaciones no leídas en el contexto global."""
    try:
        # Asegurarse de que estamos en un contexto de aplicación
        if current_app:
            count = db.session.query(func.count(Notification.id)).filter_by(is_read=False).scalar()
            return dict(unread_notifications_count=count or 0)
    except Exception as e:
        # Loguear error si falla la consulta, pero no detener la app
        if current_app:
            current_app.logger.error(f"Error al contar notificaciones no leídas: {e}", exc_info=False) # exc_info=False para no llenar logs
    return dict(unread_notifications_count=0) # Devolver 0 si hay error o no hay contexto

# ---------- Cargar ajustes en g ----------
@main_bp.before_app_request
def load_settings():
    # Evitar cargar en rutas estáticas o de debug toolbar si se usa
    if request.endpoint and request.endpoint.startswith(('static', 'debugtoolbar')):
        return

    if 'settings' not in g:
        try:
            # Usar get_or_404 sería más directo si la app no puede funcionar sin settings
            # settings = db.session.get(SystemSettings, 1) # Más eficiente que query().get()
            # Pero crear uno si no existe es más robusto para el primer arranque
            settings = db.session.query(SystemSettings).first() # Buscar el primero (asumiendo ID=1)
            if not settings:
                current_app.logger.info("No SystemSettings found, creating default entry with ID 1.")
                # Usar los defaults del modelo
                settings = SystemSettings(id=1)
                db.session.add(settings)
                db.session.commit()
                current_app.logger.info("Default SystemSettings created and committed.")
                # Volver a cargar para asegurar que está en la sesión actual de g
                settings = db.session.get(SystemSettings, 1)

            g.settings = settings
            # print(f"Settings loaded: IVA={g.settings.iva_rate}, IRPF={g.settings.irpf_rate}") # Debug
        except OperationalError as oe:
             # Común si la tabla no existe aún (antes de la primera migración)
             current_app.logger.warning(f"Database operation error loading settings (likely table missing): {oe}")
             g.settings = SystemSettings() # Usar un objeto vacío con defaults
        except Exception as e:
            current_app.logger.error(f"¡Error crítico cargando SystemSettings!: {e}", exc_info=True)
            # Proveer un objeto default para evitar errores en plantillas/rutas
            g.settings = SystemSettings() # Usar un objeto vacío con defaults

# ---------- Rutas ----------
@main_bp.route('/')
def home(): return redirect(url_for('main_bp.dashboard'))

@main_bp.route('/dashboard')
@login_required # La protección de login ya debería estar
def dashboard():
    stats = {
        'properties_total': 0,
        'active_contracts': 0,
        'potential_monthly_income': Decimal('0.00'),
        'vacant_properties': 0,
        'occupancy_rate': 0.0,
        'pending_expenses_total': Decimal('0.00')
    }
    expiring_contracts = []
    unread_notifications_for_dashboard = [] # Nombre específico para esta lista
    upcoming_ipc_reviews = []
    recent_activity = []
    vacant_properties_list_for_dashboard = [] # Para la sección de propiedades vacías

    try:
        # --- KPIs ---
        stats['properties_total'] = db.session.query(func.count(Propiedad.id)).scalar() or 0
        stats['active_contracts'] = db.session.query(func.count(Contrato.id)).filter(Contrato.estado == 'activo').scalar() or 0

        potential_income_raw = db.session.query(func.sum(Contrato.precio_mensual)).filter(Contrato.estado == 'activo').scalar()
        stats['potential_monthly_income'] = potential_income_raw or Decimal('0.00')

        stats['vacant_properties'] = db.session.query(func.count(Propiedad.id)).filter(Propiedad.estado_ocupacion == 'vacia').scalar() or 0

        if stats['properties_total'] > 0:
            occupied_count = stats['properties_total'] - stats['vacant_properties']
            stats['occupancy_rate'] = round((occupied_count / stats['properties_total']) * 100, 1)
        else:
            stats['occupancy_rate'] = 0.0

        pending_expenses_raw = db.session.query(func.sum(Gasto.importe)).filter(Gasto.estado == 'Pendiente').scalar()
        stats['pending_expenses_total'] = pending_expenses_raw or Decimal('0.00')

        # --- Secciones con Listas ---
        today = date.today()
        ninety_days_later = today + timedelta(days=90)

        # Contratos Próximos a Vencer (90 días)
        expiring_contracts = db.session.query(Contrato).options(
            joinedload(Contrato.propiedad_ref),
            joinedload(Contrato.inquilino_ref)
        ).filter(
            Contrato.estado == 'activo',
            Contrato.fecha_fin.isnot(None),
            Contrato.fecha_fin >= today,
            Contrato.fecha_fin <= ninety_days_later
        ).order_by(Contrato.fecha_fin.asc()).limit(5).all()

        # --- MODIFICADO: Últimas Notificaciones Sin Leer (Filtradas por Rol) ---
        query_unread_notifications = Notification.query.filter(Notification.is_read == False)

        if current_user.role == 'admin':
            query_unread_notifications = query_unread_notifications.filter(
                or_(Notification.user_id == current_user.id, Notification.user_id.is_(None))
            )
        # Para gestores, si quieres que vean globales en el dashboard:
        elif current_user.role == 'gestor':
             query_unread_notifications = query_unread_notifications.filter(
                 or_(Notification.user_id == current_user.id, Notification.user_id.is_(None)) # Incluye globales
             )
        # Para otros roles (o gestores si solo ven las suyas):
        else:
            query_unread_notifications = query_unread_notifications.filter_by(user_id=current_user.id)
        
        unread_notifications_for_dashboard = query_unread_notifications.order_by(
            Notification.timestamp.desc()
        ).limit(5).all()
        # --- FIN MODIFICACIÓN NOTIFICACIONES ---

        # Próximas Revisiones IPC/IRAV (Mes actual o siguiente)
        current_month = today.month
        next_month_num = (current_month % 12) + 1

        upcoming_ipc_reviews = db.session.query(Contrato).options(
            joinedload(Contrato.propiedad_ref), joinedload(Contrato.inquilino_ref)
        ).filter(
            Contrato.estado == 'activo',
            # Filtrar por contratos que usan índice (IPC o IRAV)
            or_(Contrato.actualiza_ipc == True, Contrato.actualiza_irav == True),
            Contrato.fecha_inicio.isnot(None),
            or_(
                extract('month', Contrato.fecha_inicio) == current_month,
                extract('month', Contrato.fecha_inicio) == next_month_num
            )
        ).order_by(extract('month', Contrato.fecha_inicio), Contrato.id).limit(5).all()

        # Actividad Reciente
        limit_per_type_activity = 3
        final_limit_activity = 5
        combined_activity = []

        # Consultas individuales para actividad reciente
        recent_contracts_activity = Contrato.query.order_by(Contrato.fecha_creacion.desc()).limit(limit_per_type_activity).all()
        for item in recent_contracts_activity:
             combined_activity.append({
                 'type': 'Contrato', 'timestamp': item.fecha_creacion, 'icon': 'fa-file-contract',
                 'description': f"Contrato: {item.numero_contrato}",
                 'url': url_for('contratos_bp.ver_contrato', id=item.id, _external=False) # Asume que tienes esta ruta
             })

        recent_properties_activity = Propiedad.query.order_by(Propiedad.fecha_creacion.desc()).limit(limit_per_type_activity).all()
        for item in recent_properties_activity:
             combined_activity.append({
                 'type': 'Propiedad', 'timestamp': item.fecha_creacion, 'icon': 'fa-home',
                 'description': f"Propiedad: {item.direccion}",
                 'url': url_for('propiedades_bp.listar_propiedades', _anchor=f'prop-{item.id}', _external=False) # Asume esta ruta
             })
        
        recent_tenants_activity = Inquilino.query.order_by(Inquilino.fecha_creacion.desc()).limit(limit_per_type_activity).all()
        for item in recent_tenants_activity:
             combined_activity.append({
                 'type': 'Inquilino', 'timestamp': item.fecha_creacion, 'icon': 'fa-users',
                 'description': f"Inquilino: {item.nombre}",
                 'url': url_for('inquilinos_bp.listar_inquilinos', _anchor=f'inq-{item.id}', _external=False) # Asume esta ruta
             })

        recent_owners_activity = Propietario.query.order_by(Propietario.fecha_creacion.desc()).limit(limit_per_type_activity).all()
        for item in recent_owners_activity:
             combined_activity.append({
                 'type': 'Propietario', 'timestamp': item.fecha_creacion, 'icon': 'fa-user-tie',
                 'description': f"Propietario: {item.nombre}",
                 'url': url_for('propietarios_bp.listar_propietarios', _anchor=f'owner-{item.id}', _external=False) # Cambiado _anchor
             })

        recent_expenses_activity = Gasto.query.order_by(Gasto.upload_date.desc()).limit(limit_per_type_activity).all()
        for item in recent_expenses_activity:
             combined_activity.append({
                 'type': 'Gasto', 'timestamp': item.upload_date, 'icon': 'fa-receipt',
                 'description': f"Gasto: {item.concepto} ({item.importe})", # Formatear importe si es Decimal
                 'url': url_for('facturas_bp.gestionar_gastos', _anchor=f'gasto-{item.id}', _external=False) # Asume esta ruta
             })
        
        combined_activity.sort(key=lambda x: x['timestamp'], reverse=True)
        recent_activity = combined_activity[:final_limit_activity]

        # Lista de propiedades vacías para el widget del dashboard
        vacant_properties_list_for_dashboard = db.session.query(Propiedad).filter_by(estado_ocupacion='vacia').order_by(desc(Propiedad.fecha_creacion)).limit(3).all()


    except Exception as e:
        flash(f'Error cargando datos del dashboard: {e}', 'danger')
        current_app.logger.error(f"Error cargando dashboard: {e}", exc_info=True)
        # Asegurar valores por defecto en caso de error para todas las listas
        expiring_contracts = []
        unread_notifications_for_dashboard = []
        upcoming_ipc_reviews = []
        recent_activity = []
        vacant_properties_list_for_dashboard = []

    return render_template('dashboard.html',
                           title='Dashboard',
                           stats=stats,
                           expiring_contracts=expiring_contracts,
                           unread_notifications=unread_notifications_for_dashboard, # Usar la variable correcta
                           upcoming_ipc_reviews=upcoming_ipc_reviews,
                           recent_activity=recent_activity,
                           vacant_properties=vacant_properties_list_for_dashboard, # Pasar la lista correcta
                           MESES_STR=MESES_STR # Pasar MESES_STR al template para el widget de IPC
                           )
# --- NUEVAS RUTAS PARA NOTIFICACIONES ---

@main_bp.route('/notifications')
@login_required 
def notifications():
    """Muestra la página de notificaciones filtradas para el usuario actual."""
    all_notifications_for_user = []
    try:
        query = Notification.query

        if current_user.role == 'admin':
            # Los administradores ven sus notificaciones personales Y las globales (user_id IS NULL)
            query = query.filter(
                or_(Notification.user_id == current_user.id, Notification.user_id.is_(None))
            )
        else:
            # Otros usuarios (gestor, usuario) solo ven las notificaciones asignadas a ellos.
            query = query.filter_by(user_id=current_user.id)
        
        all_notifications_for_user = query.order_by(
            Notification.is_read.asc(),      # No leídas primero
            Notification.timestamp.desc()    # Luego por más recientes
        ).all()

    except Exception as e:
        flash(f"Error al cargar notificaciones: {e}", "danger")
        current_app.logger.error(f"Error en GET /notifications para {current_user.username}: {e}", exc_info=True)

    csrf_form_instance = CSRFOnlyForm() # Para los botones de acción en el template
    return render_template(
        'notifications.html',
        title="Notificaciones",
        notifications=all_notifications_for_user,
        csrf_form=csrf_form_instance
    )

# --- RUTA PARA MARCAR UNA NOTIFICACIÓN COMO LEÍDA (CON VERIFICACIÓN DE PERMISO) ---
@main_bp.route('/notifications/read/<int:id>', methods=['POST'])
@login_required
def mark_notification_read(id):
    """Marca una notificación específica como leída."""
    notification = db.session.get(Notification, id)
    if not notification:
        flash("Notificación no encontrada.", "warning")
        return redirect(request.referrer or url_for('main_bp.notifications'))

    # --- VERIFICACIÓN DE PERMISO ---
    can_mark_read = False
    if notification.user_id == current_user.id: # Es el dueño de la notificación
        can_mark_read = True
    elif notification.user_id is None and current_user.role == 'admin': # Es global y el usuario es admin
        can_mark_read = True
    
    if not can_mark_read:
        flash("No tienes permiso para marcar esta notificación como leída.", "danger")
        return redirect(url_for('main_bp.notifications'))
    # --- FIN VERIFICACIÓN ---

    if not notification.is_read:
        try:
            notification.is_read = True
            db.session.commit()
            current_app.logger.info(f"Notificación {id} marcada como leída por usuario {current_user.username}.")
            # No es necesario un flash message aquí si el frontend actualiza la UI,
            # o si la redirección es suficiente feedback.
        except Exception as e:
            db.session.rollback()
            flash(f"Error al marcar notificación como leída: {e}", "danger")
            current_app.logger.error(f"Error en POST /notifications/read/{id} para {current_user.username}: {e}", exc_info=True)

    # Determinar a dónde redirigir.
    # Si el JS `handleNotificationClick` ya maneja la redirección a `related_url`,
    # esta redirección de fallback es suficiente.
    # Si no hay `related_url`, o si el JS no redirige, volver a la lista de notificaciones.
    if notification.related_url and request.form.get('navigate_to_url') == 'true': # Suponiendo que JS podría enviar esto
        return redirect(notification.related_url)
    return redirect(request.referrer or url_for('main_bp.notifications'))

@main_bp.route('/notifications/delete/<int:id>', methods=['POST'])
def delete_notification(id):
    """Elimina una notificación específica."""
    notification = Notification.query.get_or_404(id)
    try:
        db.session.delete(notification)
        db.session.commit()
        flash("Notificación eliminada.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error al eliminar notificación: {e}", "danger")
        current_app.logger.error(f"Error en POST /notifications/delete/{id}", exc_info=True)
    return redirect(url_for('main_bp.notifications'))

@main_bp.route('/notifications/delete_all_read', methods=['POST'])
@login_required
def delete_all_read_notifications():
    """Elimina todas las notificaciones marcadas como leídas para el usuario actual (o globales si es admin)."""
    try:
        query_to_delete = Notification.query.filter_by(is_read=True)

        if current_user.role == 'admin':
            # Admin borra sus leídas Y las globales leídas
            query_to_delete = query_to_delete.filter(
                or_(Notification.user_id == current_user.id, Notification.user_id.is_(None))
            )
        else:
            # Otros usuarios solo borran SUS notificaciones leídas
            query_to_delete = query_to_delete.filter_by(user_id=current_user.id)
        
        num_deleted = query_to_delete.delete(synchronize_session='fetch') # 'fetch' es una estrategia segura
        db.session.commit()

        if num_deleted > 0:
            flash(f"Se eliminaron {num_deleted} notificaciones leídas.", "success")
        else:
            flash("No había notificaciones leídas para eliminar.", "info")
    except Exception as e:
        db.session.rollback()
        flash(f"Error al eliminar notificaciones leídas: {e}", "danger")
        current_app.logger.error(f"Error en POST /notifications/delete_all_read para {current_user.username}: {e}", exc_info=True)
    
    return redirect(url_for('main_bp.notifications'))


    # --- VERIFICACIÓN DE PERMISO ---
    can_mark_read = False
    if notification.user_id == current_user.id: # Es el dueño de la notificación
        can_mark_read = True
    elif notification.user_id is None and current_user.role == 'admin': # Es global y el usuario es admin
        can_mark_read = True
    
    if not can_mark_read:
        flash("No tienes permiso para marcar esta notificación como leída.", "danger")
        return redirect(url_for('main_bp.notifications'))
    # --- FIN VERIFICACIÓN ---

    if not notification.is_read:
        try:
            notification.is_read = True
            db.session.commit()
            current_app.logger.info(f"Notificación {id} marcada como leída por usuario {current_user.username}.")
        except Exception as e:
            db.session.rollback()
            flash(f"Error al marcar notificación como leída: {e}", "danger")
            current_app.logger.error(f"Error en POST /notifications/read/{id}: {e}", exc_info=True)

    if notification.related_url:
        # Si la notificación marcó como leída y tiene URL, intentar redirigir allí.
        # El JS handleNotificationClick podría manejar esto del lado del cliente también.
        # return redirect(notification.related_url)
        pass # El JS se encarga de la redirección si hay related_url

    return redirect(request.referrer or url_for('main_bp.notifications'))
# ---------- Ruta AJUSTES ----------
@main_bp.route('/ajustes', methods=['GET', 'POST'])
@login_required      # Asegurar que el usuario está logueado
@role_required('admin') # Solo el rol 'admin' puede acceder a los ajustes
def ajustes_view():
    # g.settings ya debería estar cargado por el before_app_request 'load_settings'
    # pero es bueno tener un fallback o una comprobación robusta.
    settings_obj = getattr(g, 'settings', None)

    # Si 'settings' no está en g o no es una instancia válida, intentar cargarlo/crearlo
    if not isinstance(settings_obj, SystemSettings) or not settings_obj.id:
        current_app.logger.warning("SystemSettings no encontrado en g o inválido en ajustes_view. Intentando cargar/crear.")
        try:
            settings_obj = db.session.get(SystemSettings, 1) # Intentar obtener por ID 1
            if not settings_obj:
                current_app.logger.info("No SystemSettings (ID=1) encontrado, creando defaults.")
                settings_obj = SystemSettings(id=1) # Crear con ID 1
                db.session.add(settings_obj)
                db.session.commit()
                settings_obj = db.session.get(SystemSettings, 1) # Recargar para asegurar que está en sesión
            g.settings = settings_obj # Actualizar g.settings
        except OperationalError: # Común si la tabla no existe (antes de migrate)
            flash("Error: La tabla de ajustes del sistema no existe. Ejecuta las migraciones.", "danger")
            current_app.logger.error("Tabla SystemSettings no existe en ajustes_view.")
            return redirect(url_for('main_bp.dashboard')) # Redirigir si hay un error grave de BD
        except Exception as e:
            flash(f"Error crítico al cargar los ajustes: {e}", "danger")
            current_app.logger.error(f"Error crítico cargando SystemSettings en ajustes_view: {e}", exc_info=True)
            return redirect(url_for('main_bp.dashboard'))

    # Si settings_obj sigue siendo None después de los intentos, es un problema serio
    if not settings_obj:
        flash("No se pudieron cargar los ajustes del sistema. Contacta al administrador.", "critical")
        return redirect(url_for('main_bp.dashboard'))

    # Crear instancia del formulario CSRF para el template
    csrf_form_instance = CSRFOnlyForm() # Renombrar para evitar conflicto si usas un 'SettingsForm' completo

    if request.method == 'POST':
        # Obtener el objeto desde la sesión para actualizarlo
        settings_to_update = db.session.get(SystemSettings, 1)
        if not settings_to_update:
             flash("Error crítico: No se encontraron los ajustes para guardar.", "danger")
             # Pasar csrf_form incluso al redirigir o re-renderizar con error
             return render_template('ajustes.html', title='Ajustes', settings=settings_obj, csrf_form=csrf_form_instance)

        try:
            # --- General ---
            settings_to_update.language = request.form.get('language', settings_to_update.language)
            settings_to_update.timezone = request.form.get('timezone', settings_to_update.timezone)
            settings_to_update.date_format = request.form.get('date_format', settings_to_update.date_format)
            settings_to_update.currency = request.form.get('currency', settings_to_update.currency)
            settings_to_update.dark_mode = 'darkModeToggle' in request.form
            settings_to_update.show_tutorial = 'show_tutorial' in request.form

            # --- Visualización ---
            settings_to_update.default_view = request.form.get('default_view', settings_to_update.default_view)
            try:
                items_per_page_val = int(request.form.get('items_per_page', settings_to_update.items_per_page))
                settings_to_update.items_per_page = items_per_page_val if items_per_page_val > 0 else 10
            except ValueError:
                flash("«Elementos por página» debe ser un número entero.", "warning")
            settings_to_update.show_stats = 'show_stats' in request.form
            settings_to_update.show_alerts = 'show_alerts' in request.form

            # --- Empresa ---
            settings_to_update.company_name = request.form.get('company_name', settings_to_update.company_name)
            settings_to_update.company_nif = request.form.get('company_nif', settings_to_update.company_nif)
            settings_to_update.company_address = request.form.get('company_address', settings_to_update.company_address)
            settings_to_update.company_city = request.form.get('company_city', settings_to_update.company_city)
            settings_to_update.company_zip = request.form.get('company_zip', settings_to_update.company_zip)
            settings_to_update.company_country = request.form.get('company_country', settings_to_update.company_country)
            settings_to_update.company_phone = request.form.get('company_phone', settings_to_update.company_phone)
            settings_to_update.company_email = request.form.get('company_email', settings_to_update.company_email)
            settings_to_update.company_website = request.form.get('company_website', settings_to_update.company_website)

            # --- Logo ---
            logo_file = request.files.get('company_logo')
            if logo_file and logo_file.filename:
                allowed = {'png', 'jpg', 'jpeg', 'gif', 'svg'}
                filename_lower = logo_file.filename.lower()
                ext = filename_lower.rsplit('.', 1)[-1] if '.' in filename_lower else ''
                if ext in allowed:
                    unique_filename_part = secure_filename(f"logo_{uuid.uuid4().hex}")
                    filename = f"{unique_filename_part}.{ext}"
                    upload_folder = current_app.config.get('UPLOAD_FOLDER_LOGOS')
                    if not upload_folder:
                        flash("Carpeta de logos no configurada.", "danger")
                        raise ValueError("UPLOAD_FOLDER_LOGOS no definido en config.")
                    os.makedirs(upload_folder, exist_ok=True)
                    old_logo = settings_to_update.company_logo_filename
                    if old_logo and old_logo != filename:
                        try: os.remove(os.path.join(upload_folder, old_logo))
                        except OSError as e: current_app.logger.warning(f"No se pudo eliminar logo anterior {old_logo}: {e}")
                    try:
                         logo_file.save(os.path.join(upload_folder, filename))
                         settings_to_update.company_logo_filename = filename
                    except Exception as e_save:
                         flash(f"Error al guardar nuevo logo: {e_save}", "danger")
                else:
                    flash(f"Tipo de archivo no permitido: '.{ext}'.", "warning")

            # --- Notificaciones (Email Remitente de Notificaciones - diferente a config SMTP) ---
            # settings_to_update.sender_email = request.form.get('sender_email', settings_to_update.sender_email) # Si lo mantienes
            # settings_to_update.sender_name = request.form.get('sender_name', settings_to_update.sender_name)   # Si lo mantienes

            # --- Configuración de Correo SMTP ---
            settings_to_update.mail_server = request.form.get('mail_server', settings_to_update.mail_server).strip()
            try:
                port = int(request.form.get('mail_port', settings_to_update.mail_port))
                settings_to_update.mail_port = port if 1 <= port <= 65535 else 587
            except (ValueError, TypeError):
                flash("Puerto de correo inválido. Usando 587.", "warning"); settings_to_update.mail_port = 587
            settings_to_update.mail_use_tls = 'mail_use_tls' in request.form
            settings_to_update.mail_use_ssl = 'mail_use_ssl' in request.form
            settings_to_update.mail_username = request.form.get('mail_username', settings_to_update.mail_username).strip() or None
            # Contraseña de correo (si decides guardarla, considera cifrado)
            new_smtp_password = request.form.get('mail_password')
            if new_smtp_password:
                # Aquí iría la lógica de cifrado si la implementas:
                # settings_to_update.mail_password = encrypt_data(new_smtp_password)
                # Por ahora, sin cifrado (NO RECOMENDADO):
                settings_to_update.mail_password = new_smtp_password
            settings_to_update.mail_default_sender = request.form.get('mail_default_sender', settings_to_update.mail_default_sender).strip() or None
            settings_to_update.mail_sender_display_name = request.form.get('mail_sender_display_name', settings_to_update.mail_sender_display_name).strip() or None

            # --- Seguridad ---
            settings_to_update.log_activity = 'log_activity' in request.form
            
            # --- Avanzado ---
            settings_to_update.backup_frequency = request.form.get('backup_frequency', settings_to_update.backup_frequency)

            # --- Tasas IVA / IRPF ---
            try:
                iva_input = request.form.get('iva_rate')
                new_iva_rate = normalize_percent(iva_input)
                if new_iva_rate is not None: settings_to_update.iva_rate = new_iva_rate
                elif iva_input: flash(f"Valor IVA '{iva_input}' inválido.", "warning")
            except ValueError as e_iva: flash(f"Error en tasa IVA: {e_iva}", 'warning')
            try:
                irpf_input = request.form.get('irpf_rate')
                new_irpf_rate = normalize_percent(irpf_input)
                if new_irpf_rate is not None: settings_to_update.irpf_rate = new_irpf_rate
                elif irpf_input: flash(f"Valor IRPF '{irpf_input}' inválido.", "warning")
            except ValueError as e_irpf: flash(f"Error en tasa IRPF: {e_irpf}", 'warning')

            settings_to_update.generate_invoice_if_index_missing = 'generate_invoice_if_index_missing' in request.form

            db.session.commit()
            flash("Ajustes guardados correctamente.", "success")
            g.settings = settings_to_update # Actualizar g
            # No es necesario reiniciar para cambios de config de mail si se recargan en before_request
            return redirect(url_for('main_bp.ajustes_view'))

        except Exception as e:
            db.session.rollback()
            flash(f"Error inesperado al guardar los ajustes: {e}", 'danger')
            current_app.logger.error("Error guardando ajustes", exc_info=True)
            # Re-renderizar el template con los datos actuales y el formulario CSRF
            return render_template('ajustes.html', title='Ajustes', settings=settings_obj, csrf_form=csrf_form_instance)

    # Para método GET
    return render_template(
        'ajustes.html',
        title='Ajustes',
        settings=settings_obj, # Usar el objeto settings cargado
        csrf_form=csrf_form_instance # Pasar el formulario CSRF
    )


# ---------- Servir logos (sin cambios) ----------
@main_bp.route('/uploads/logos/<path:filename>')
def serve_logo(filename):
    logo_folder = current_app.config.get('UPLOAD_FOLDER_LOGOS')
    if not logo_folder or not os.path.isdir(logo_folder):
         current_app.logger.error(f"UPLOAD_FOLDER_LOGOS no configurado o no existe: {logo_folder}")
         abort(404)

    # Sanear filename para seguridad
    safe_filename = secure_filename(filename)
    if safe_filename != filename:
         abort(404)

    try:
         # send_from_directory ya maneja seguridad básica contra path traversal si
         # el directorio base es absoluto y seguro.
         return send_from_directory(logo_folder, safe_filename)
    except FileNotFoundError:
         abort(404)
    except Exception as e:
         current_app.logger.error(f"Error sirviendo logo {safe_filename}: {e}")
         abort(500)