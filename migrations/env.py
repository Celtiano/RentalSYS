# migrations/env.py (COMPLETO v4 - Simplificado tras corregir singleton db)
import logging
from logging.config import fileConfig

from flask import current_app

from alembic import context

# --- Importar el módulo de modelos ---
# Esto es suficiente para que los modelos (que usan la db global) se registren
from myapp import models

# --- NO necesitamos importar 'db' explícitamente aquí ---
# from myapp.models import db as flask_db_instance

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
logger = logging.getLogger('alembic.env')

# --- LOGGING ---
logger.info("-----------------------------------------------------")
logger.info("Ejecutando migrations/env.py")
logger.info(f"FLASK_APP detectada: {current_app.name}")

def get_engine():
    try:
        # this works with Flask-SQLAlchemy<3 and Alchemical
        engine = current_app.extensions['migrate'].db.get_engine()
        logger.info(f"Motor DB obtenido (vía get_engine): {engine.url}")
        return engine
    except (TypeError, AttributeError):
        # this works with Flask-SQLAlchemy>=3
        engine = current_app.extensions['migrate'].db.engine
        logger.info(f"Motor DB obtenido (vía engine): {engine.url}")
        return engine

def get_engine_url():
    try:
        url = get_engine().url.render_as_string(hide_password=False).replace(
            '%', '%%')
        logger.info(f"URL del motor: {url}")
        return url
    except AttributeError:
        url = str(get_engine().url).replace('%', '%%')
        logger.info(f"URL del motor (fallback): {url}")
        return url

db_url = get_engine_url()
config.set_main_option('sqlalchemy.url', db_url)

# Obtener la instancia de SQLAlchemy de la extensión Flask-Migrate
target_db = current_app.extensions['migrate'].db

# --- Comprobación de metadata (Usamos target_db directamente) ---
logger.info("Verificando metadata desde target_db (current_app.extensions['migrate'].db)...")
if hasattr(target_db, 'metadata'):
    # Esta debería ser la metadata correcta ahora que db es singleton
    _metadata_check = target_db.metadata
    logger.info(f"Tablas detectadas en target_db.metadata: {list(_metadata_check.tables.keys()) if _metadata_check else 'None'}")
else:
    logger.warning("target_db (db de la extensión) no tiene atributo 'metadata'")

def get_metadata():
    """Devuelve el objeto MetaData correcto."""
    logger.info("Llamando a get_metadata()...")
    # En Flask-SQLAlchemy, la metadata está en db.metadata
    if hasattr(target_db, 'metadata'):
        logger.info("Usando target_db.metadata")
        meta = target_db.metadata
        logger.info(f"Tablas encontradas en metadata devuelta: {list(meta.tables.keys()) if meta else 'None'}")
        return meta
    # El fallback a 'metadatas' probablemente ya no sea necesario con versiones recientes
    if hasattr(target_db, 'metadatas'):
        logger.warning("Usando fallback target_db.metadatas[None]")
        meta = target_db.metadatas[None]
        logger.info(f"Tablas encontradas en metadata (fallback): {list(meta.tables.keys()) if meta else 'None'}")
        return meta

    logger.error("No se pudo encontrar el objeto metadata!")
    return None # O lanzar un error si no se encuentra

# Obtener la metadata ANTES de configurar el contexto
target_metadata = get_metadata()
if target_metadata is None:
     logger.error("TARGET_METADATA ES NONE. Lanzando error.")
     raise RuntimeError("No se pudo obtener la metadata de la base de datos desde Flask-SQLAlchemy.")
else:
    # Comprobación final antes de pasarla a Alembic
    final_tables = list(target_metadata.tables.keys())
    logger.info(f"Metadata obtenida. Tablas finales para Alembic: {final_tables}")
    if not final_tables:
         logger.warning("¡La metadata final pasada a Alembic está vacía!")

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    logger.info("Ejecutando run_migrations_offline()")
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata, # Usar la metadata obtenida
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
    logger.info("run_migrations_offline() completado.")

def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    logger.info("Ejecutando run_migrations_online()")
    def process_revision_directives(context, revision, directives):
        logger.info("Ejecutando process_revision_directives...")
        if getattr(config.cmd_opts, 'autogenerate', False):
            script = directives[0]
            if script.upgrade_ops.is_empty():
                logger.info('Directiva process_revision: Sin cambios detectados.')
                directives[:] = []
            else:
                 logger.info('Directiva process_revision: Cambios detectados.')

    conf_args = {}
    migrate_ext = current_app.extensions.get('migrate')
    if migrate_ext and hasattr(migrate_ext, 'configure_args') and migrate_ext.configure_args:
         conf_args = migrate_ext.configure_args
    if conf_args.get("process_revision_directives") is None:
        conf_args["process_revision_directives"] = process_revision_directives
        logger.info("Callback process_revision_directives asignado.")
    connectable = get_engine()
    logger.info("Estableciendo conexión...")
    with connectable.connect() as connection:
        logger.info("Conexión establecida. Configurando contexto Alembic...")
        context.configure(
            connection=connection,
            target_metadata=target_metadata, # Usar la metadata obtenida
            **conf_args # Pasar argumentos adicionales (como process_revision_directives)
        )
        logger.info("Contexto configurado. Iniciando transacción y ejecutando migraciones...")
        with context.begin_transaction():
            context.run_migrations()
        logger.info("Migraciones ejecutadas y transacción completada.")
    logger.info("run_migrations_online() completado.")
    logger.info("-----------------------------------------------------")

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()