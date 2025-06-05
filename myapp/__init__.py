# myapp/__init__.py
import os
import sys # Para detectar ejecución desde PyInstaller
import logging # Para logging a archivo
from logging.handlers import RotatingFileHandler # Para logging a archivo
from datetime import datetime, date # Para el context processor 'now'
from decimal import Decimal, InvalidOperation
import atexit  # Para cerrar el scheduler limpiamente

from flask import Flask, send_from_directory, g, request, current_app
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, or_  # Para funciones de agregación como count()
from flask_migrate import Migrate
from flask_mail import Mail
from sqlalchemy.exc import OperationalError # Para manejar error de BD al inicio
from flask_login import LoginManager, current_user
from flask_wtf.csrf import CSRFProtect
from apscheduler.schedulers.background import BackgroundScheduler

# --- Instancias de Extensiones Globales ---
db = SQLAlchemy()
migrate = Migrate()
mail = Mail()
csrf = CSRFProtect()
login_manager = LoginManager()


# --- Configuración de Flask-Login ---
login_manager.login_view = 'auth_bp.login'
login_manager.login_message = "Por favor, inicia sesión para acceder a esta página."
login_manager.login_message_category = "info"

@login_manager.user_loader
def load_user(user_id):
    """Función para cargar un usuario para Flask-Login."""
    from .models import User # Importación local para evitar ciclos
    return db.session.get(User, int(user_id))

# --- Constantes para Rutas de Carpetas de Subida (Relativas a instance_path) ---
UPLOAD_FOLDER_LOGOS_REL = 'uploads/logos'
UPLOAD_FOLDER_EXPENSES_REL = 'uploads/expenses'
UPLOAD_FOLDER_CONTRACTS_REL = 'uploads/contracts' # Asumiendo que esta existe
UPLOAD_FOLDER_INVOICES_REL = 'facturas'          # Asumiendo que esta existe

# --- Constantes para los nombres de meses ---
MESES_STR = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", 
             "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]

def format_currency_safe(value):
    if value is None: return "" # O "0,00 €" si prefieres
    try:
        dec_value = value if isinstance(value, Decimal) else Decimal(str(value))
        num_str = "{:,.2f}".format(dec_value)
        int_part, dec_part = num_str.split('.')
        int_part_formatted = int_part.replace(',', '.')
        currency_symbol = "€"
        settings = getattr(g, 'settings', None)
        if settings and settings.currency:
            if settings.currency == "USD": currency_symbol = "$"
            elif settings.currency == "GBP": currency_symbol = "£"
        return f"{int_part_formatted},{dec_part} {currency_symbol}"
    except (ValueError, TypeError, InvalidOperation):
        if current_app: current_app.logger.error(f"Error en filtro 'currency' formateando '{value}'", exc_info=False)
        return str(value)

def format_date_filter(value, fmt=None):
    if not value or not isinstance(value, (date, datetime)): return ""
    date_format_to_use = fmt
    settings = getattr(g, 'settings', None)
    if settings and settings.date_format and not fmt: # Usar settings solo si no se especifica fmt
        date_format_to_use = settings.date_format
    if not date_format_to_use: date_format_to_use = '%d/%m/%Y'
    try:
        return value.strftime(date_format_to_use)
    except Exception:
        if current_app: current_app.logger.error(f"Error en filtro 'date' formateando '{value}' con '{date_format_to_use}'", exc_info=False)
        return str(value)

# --- Factory de la Aplicación ---
def create_app():
    """Crea y configura la instancia de la aplicación Flask."""

    # --- Determinar rutas para PyInstaller ---
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # Aplicación ejecutándose como un bundle de PyInstaller
        print(f"INFO: RentalSys ejecutándose desde bundle PyInstaller: {sys._MEIPASS}")
        # Las plantillas y estáticos se empaquetan RELATIVOS a la raíz del bundle de la app
        template_folder = os.path.join(sys._MEIPASS, 'myapp', 'templates')
        static_folder_abs = os.path.join(sys._MEIPASS, 'myapp', 'static')
        static_folder = static_folder_abs if os.path.exists(static_folder_abs) else None
        # La carpeta de instancia irá al lado del .exe para persistencia de datos
        instance_dir_name = 'instance_data_rentalsys'
        executable_dir = os.path.dirname(sys.executable)
        instance_path_abs = os.path.join(executable_dir, instance_dir_name)
    else:
        # Aplicación ejecutándose en modo desarrollo normal
        print("INFO: RentalSys ejecutándose en modo desarrollo.")
        current_dir = os.path.dirname(os.path.abspath(__file__)) # Directorio de este archivo (__init__.py)
        template_folder = os.path.join(current_dir, 'templates')
        static_folder_abs = os.path.join(current_dir, 'static') # myapp/static
        static_folder = static_folder_abs if os.path.exists(static_folder_abs) else None
        # Flask maneja bien instance_path si instance_relative_config=True
        instance_path_abs = None # Flask usará el default relativo al root del proyecto (o app)

    # --- Crear Instancia de la Aplicación ---
    app = Flask(
        __name__,
        instance_path=instance_path_abs, # Establecer explícitamente si es bundle
        instance_relative_config=True if instance_path_abs is None else False, # Solo True si Flask debe calcularlo
        template_folder=template_folder,
        static_folder=static_folder
    )
    print(f"INFO: Usando instance_path: {app.instance_path}")
    print(f"INFO: Usando template_folder: {app.template_folder}")
    if static_folder: print(f"INFO: Usando static_folder: {app.static_folder}")


    # --- Configuración Principal de la Aplicación ---
    app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'una-clave-secreta-muy-fuerte-y-diferente-para-produccion')
    app.config['WTF_CSRF_SECRET_KEY'] = app.config['SECRET_KEY'] # Reutilizar para CSRF
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # Límite subida archivos a 5MB
    app.config['MESES_STR'] = MESES_STR  # Añadir constante de meses para las tareas
    
    app.config['SERVER_NAME'] = os.environ.get('FLASK_SERVER_NAME', '127.0.0.1:5000')
    app.template_filter('currency')(format_currency_safe)
    app.template_filter('date')(format_date_filter)

    # --- Crear Carpeta de Instancia si no existe ---
    try:
        os.makedirs(app.instance_path, exist_ok=True)
    except OSError as e:
        app.logger.error(f"ERROR: No se pudo crear la carpeta de instancia en '{app.instance_path}': {e}")
        # Considerar si abortar la app aquí si la carpeta de instancia es crítica y no se puede crear

    # --- Configuración de Base de Datos y Carpetas de Subida (usan app.instance_path) ---
    db_path = os.path.join(app.instance_path, 'rentalsys.db')
    app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{db_path}"
    print(f"INFO: Base de datos configurada en: {app.config['SQLALCHEMY_DATABASE_URI']}")

    app.config['UPLOAD_FOLDER_LOGOS'] = os.path.join(app.instance_path, UPLOAD_FOLDER_LOGOS_REL)
    app.config['UPLOAD_FOLDER_EXPENSES'] = os.path.join(app.instance_path, UPLOAD_FOLDER_EXPENSES_REL)
    app.config['UPLOAD_FOLDER_CONTRACTS'] = os.path.join(app.instance_path, UPLOAD_FOLDER_CONTRACTS_REL)
    app.config['UPLOAD_FOLDER_INVOICES'] = os.path.join(app.instance_path, UPLOAD_FOLDER_INVOICES_REL)

    # Crear carpetas de upload si no existen (podría ir en models.initialize_database)
    for folder_key in ['UPLOAD_FOLDER_LOGOS', 'UPLOAD_FOLDER_EXPENSES', 'UPLOAD_FOLDER_CONTRACTS', 'UPLOAD_FOLDER_INVOICES']:
        try:
            os.makedirs(app.config[folder_key], exist_ok=True)
        except OSError as e:
            app.logger.error(f"ERROR: No se pudo crear la carpeta de upload '{app.config[folder_key]}': {e}")


    # --- Inicializar Extensiones ---
    db.init_app(app)
    migrate.init_app(app, db)
    mail.init_app(app) # Configuración de mail se hará en before_request
    csrf.init_app(app)
    login_manager.init_app(app)

    # --- Configurar y Luego Inicializar Flask-Mail ---
    with app.app_context():
        from .models import SystemSettings # Importar aquí para acceso a BD
        settings_db = None
        try:
            settings_db = db.session.get(SystemSettings, 1)
            if settings_db: app.logger.info("SystemSettings(ID=1) para config Mail cargado desde DB.")
            else: app.logger.warning("SystemSettings(ID=1) no en DB. Usando ENV/defaults para Mail.")
        except OperationalError: app.logger.warning("Tabla system_settings no existe. Usando ENV/defaults para Mail.")
        except Exception as e_cfg_mail: app.logger.error(f"Error cargando SystemSettings para Mail: {e_cfg_mail}", exc_info=True)

        # Lógica de configuración de Mail (prioridad: DB, luego ENV, luego defaults)
        app.config['MAIL_SERVER'] = (settings_db.mail_server if settings_db else None) or \
                                  os.environ.get('MAIL_SERVER', 'smtp.office365.com')
        app.config['MAIL_PORT'] = (settings_db.mail_port if settings_db else None) or \
                                  int(os.environ.get('MAIL_PORT', 587))
        
        db_tls = settings_db.mail_use_tls if settings_db and settings_db.mail_use_tls is not None else None
        app.config['MAIL_USE_TLS'] = db_tls if db_tls is not None else \
                                     (os.environ.get('MAIL_USE_TLS', 'true').lower() == 'true')
        
        db_ssl = settings_db.mail_use_ssl if settings_db and settings_db.mail_use_ssl is not None else None
        app.config['MAIL_USE_SSL'] = db_ssl if db_ssl is not None else \
                                     (os.environ.get('MAIL_USE_SSL', 'false').lower() == 'true')

        app.config['MAIL_USERNAME'] = (settings_db.mail_username if settings_db else None) or \
                                    os.environ.get('MAIL_USERNAME')
        
        # Contraseña: SIEMPRE desde variable de entorno por seguridad.
        # El campo settings_db.mail_password del modelo SystemSettings debería eliminarse o no usarse aquí.
        app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')

        sender_from_db = (settings_db.mail_default_sender if settings_db else None) or \
                         (settings_db.mail_username if settings_db else None)
        sender_from_env = os.environ.get('MAIL_DEFAULT_SENDER') or app.config.get('MAIL_USERNAME')
        app.config['MAIL_DEFAULT_SENDER'] = sender_from_db or sender_from_env

        app.config['MAIL_SENDER_DISPLAY_NAME'] = (settings_db.mail_sender_display_name if settings_db else None) or \
                                               os.environ.get('MAIL_SENDER_DISPLAY_NAME', 'RentalSys')

        app.logger.debug(f"MAIL CONFIG: SERVER={app.config.get('MAIL_SERVER')}, PORT={app.config.get('MAIL_PORT')}, USER={app.config.get('MAIL_USERNAME')}, PASS_IS_SET={bool(app.config.get('MAIL_PASSWORD'))}, TLS={app.config.get('MAIL_USE_TLS')}, SSL={app.config.get('MAIL_USE_SSL')}")
        if not app.config.get('MAIL_PASSWORD'):
            app.logger.warning("ADVERTENCIA CRÍTICA: MAIL_PASSWORD no está configurada para Flask-Mail (revisa variables de entorno).")

    mail.init_app(app) # Inicializar Mail DESPUÉS de que app.config esté poblado

    # --- Configurar Logging a Archivo (Especialmente útil para el .exe) ---
    if not app.debug or (getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')):
        log_dir = os.path.join(app.instance_path, 'logs') # Logs dentro de la carpeta de instancia
        try:
            os.makedirs(log_dir, exist_ok=True)
            log_file = os.path.join(log_dir, 'rentalsys.log')
            file_handler = RotatingFileHandler(log_file, maxBytes=102400, backupCount=5) # 100KB por log, 5 backups
            file_handler.setFormatter(logging.Formatter(
                '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
            ))
            file_handler.setLevel(logging.INFO) # INFO, WARNING, ERROR, CRITICAL
            app.logger.addHandler(file_handler)
            app.logger.setLevel(logging.INFO) # Nivel general de la app
            app.logger.info('RentalSys Iniciado (Logging configurado)')
        except Exception as e_log:
            print(f"ERROR configurando logging a archivo: {e_log}")

    # --- Inicializar APScheduler ---
    # Solo iniciar el scheduler si no estamos en el proceso hijo del reloader
    # o si la app no está en modo debug con reloader.
    # En producción (ej. con Waitress), esto se ejecutará una vez.
    if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        scheduler = BackgroundScheduler(daemon=True)
        
        # Importar las tareas
        from .tasks import check_expiring_contracts, check_pending_invoices, check_ipc_reviews
        
        # Programar las tareas para que se ejecuten todos los días a una hora específica
        # Pasar el app como argumento para que las tareas tengan acceso al contexto
        scheduler.add_job(
            func=check_expiring_contracts, 
            args=[app], 
            trigger="cron", 
            hour=2, 
            minute=0,
            id='check_expiring_contracts'
        )
        scheduler.add_job(
            func=check_pending_invoices, 
            args=[app], 
            trigger="cron", 
            hour=2, 
            minute=15,
            id='check_pending_invoices'
        )
        scheduler.add_job(
            func=check_ipc_reviews, 
            args=[app], 
            trigger="cron", 
            hour=2, 
            minute=30,
            id='check_ipc_reviews'
        )
        
        scheduler.start()
        app.logger.info("APScheduler iniciado y tareas programadas.")
        
        # Registrar función para apagar el scheduler cuando la aplicación se cierra
        atexit.register(lambda: scheduler.shutdown())

    # --- Lógica a ejecutar antes de cada request ---
    @app.before_request
    def load_app_settings_to_g():
        if 'settings' not in g:
            from .models import SystemSettings
            try:
                settings_obj_g = db.session.get(SystemSettings, 1)
                if not settings_obj_g:
                    app.logger.info("SystemSettings(ID=1) no encontrado. Creando defaults y guardando en BD para 'g'.")
                    settings_obj_g = SystemSettings(id=1)
                    db.session.add(settings_obj_g)
                    db.session.commit()
                    settings_obj_g = db.session.get(SystemSettings, 1) # Recargar
                g.settings = settings_obj_g
            except Exception as e_g_settings: # Captura más general
                app.logger.error(f"Error cargando SystemSettings para 'g': {e_g_settings}", exc_info=True)
                g.settings = SystemSettings(id=1) # Fallback a objeto en memoria


    # --- Registrar Blueprints ---
    from .routes.auth import auth_bp
    from .routes.main import main_bp
    from .routes.propietarios import propietarios_bp
    from .routes.inquilinos import inquilinos_bp
    from .routes.propiedades import propiedades_bp
    from .routes.contratos import contratos_bp
    from .routes.facturas import facturas_bp
    from .routes.ipc import ipc_bp
    from .routes.admin_users import admin_users_bp
    from .routes.reports import reports_bp
    from .routes.external_db_api import external_db_api_bp    

    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(admin_users_bp, url_prefix='/admin/users')
    app.register_blueprint(main_bp) # Rutas como '/', '/dashboard', '/ajustes'
    app.register_blueprint(propietarios_bp, url_prefix='/propietarios')
    app.register_blueprint(inquilinos_bp,   url_prefix='/inquilinos')
    app.register_blueprint(propiedades_bp,  url_prefix='/propiedades')
    app.register_blueprint(contratos_bp,    url_prefix='/contratos')
    app.register_blueprint(facturas_bp,     url_prefix='/facturas')
    app.register_blueprint(ipc_bp,          url_prefix='/ipc')
    app.register_blueprint(reports_bp,      url_prefix='/informes')

    app.jinja_env.filters['currency'] = format_currency_safe
    app.jinja_env.filters['date'] = format_date_filter
    app.register_blueprint(external_db_api_bp)


    # --- Context Processors (variables disponibles en todos los templates) ---
    @app.context_processor
    def inject_global_template_vars():
        """Inyecta variables globales en el contexto de las plantillas."""
        return dict(
            current_user=current_user,
            settings=getattr(g, 'settings', None), # Pasar g.settings
            now=datetime.utcnow # Pasar datetime.utcnow
        )

    @app.context_processor
    def inject_unread_notifications_count_ctx():
        count = 0
        if current_user.is_authenticated:
            from .models import Notification # Importación local para evitar ciclos
            try:
                if current_user.role == 'admin':
                    # Admin ve sus notificaciones asignadas + las globales (user_id IS NULL)
                    count = db.session.query(func.count(Notification.id)).filter(
                        Notification.is_read == False,
                        or_(Notification.user_id == current_user.id, Notification.user_id.is_(None)) # <--- or_ se usa aquí
                    ).scalar()
                else:
                    # Otros usuarios solo ven sus notificaciones asignadas
                    count = db.session.query(func.count(Notification.id)).filter(
                        Notification.is_read == False,
                        Notification.user_id == current_user.id
                    ).scalar()
            except Exception as e:
                # Usar current_app.logger si está disponible
                logger_to_use = current_app.logger if hasattr(current_app, 'logger') else logging.getLogger(__name__)
                logger_to_use.error(f"Error al contar notificaciones no leídas para {current_user.username}: {e}", exc_info=False)
        return dict(unread_notifications_count=(count or 0))

    # Los filtros Jinja 'currency' y 'date' se asume que están en main.py y registrados con
    # @main_bp.app_template_filter('currency')
    # Si los definieras aquí, los registrarías con app.jinja_env.filters['nombre'] = funcion_filtro

    # --- NUEVO Context Processor para 'now' y 'today_date' ---
    # (si los moviste de main.py y ya no están allí)
    @app.context_processor
    def utility_processor():
        return dict(now=datetime.utcnow, today_date=date.today().isoformat())

    return app