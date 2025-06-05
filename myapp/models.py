# myapp/models.py
import os
import uuid
import json
from datetime import datetime, date
from decimal import Decimal, ROUND_HALF_UP

from flask import current_app # Necesario para current_app.instance_path
from werkzeug.utils import secure_filename
from sqlalchemy import UniqueConstraint, inspect, text, Numeric, Table, Column, Integer, ForeignKey, String, Boolean, DateTime, CheckConstraint, Text
from sqlalchemy.exc import IntegrityError
from . import db

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

# --- CONSTANTES ---
UPLOAD_FOLDER_CONTRACTS_REL = 'uploads/contracts'
UPLOAD_FOLDER_INVOICES_REL = 'facturas' # Usado para el path de facturas PDF (si decides guardarlas)
UPLOAD_FOLDER_LOGOS_REL = 'uploads/logos'
UPLOAD_FOLDER_EXPENSES_REL= 'uploads/expenses'

DEFAULT_IRPF_RATE = Decimal('0.19')
DEFAULT_IVA_RATE = Decimal('0.21')

# --- TABLA ASOCIACIÓN User <-> Propietario ---
user_propietario_association = Table('user_propietario', db.metadata,
    Column('user_id', Integer, ForeignKey('user.id', ondelete='CASCADE'), primary_key=True),
    Column('propietario_id', Integer, ForeignKey('propietario.id', ondelete='CASCADE'), primary_key=True),
    extend_existing=True # Útil si por alguna razón se re-evalúa el módulo
)

# --- FUNCIONES AUXILIARES ---
def ensure_folder_exists(relative_path): # Mover al final o al principio antes de su uso
    if not current_app: print("[ERROR] ensure_folder_exists necesita contexto."); return None
    folder_path = os.path.join(current_app.instance_path, relative_path)
    if not os.path.exists(folder_path):
        try: os.makedirs(folder_path); print(f"Carpeta creada: {folder_path}")
        except OSError as e: print(f"[ERROR] No se pudo crear {folder_path}: {e}"); return None
    return folder_path

def initialize_database():
    # ... (tu código existente)
    if not current_app:
        print("[ERROR] initialize_database necesita contexto.")
        return
    instance_path = current_app.instance_path
    if not os.path.exists(instance_path):
        try:
            os.makedirs(instance_path)
            print(f"Carpeta instance creada: {instance_path}")
        except OSError as e:
            print(f"[ERROR] No se pudo crear carpeta instance: {e}")
            return
    ensure_folder_exists(UPLOAD_FOLDER_CONTRACTS_REL)
    ensure_folder_exists(UPLOAD_FOLDER_INVOICES_REL)
    ensure_folder_exists(UPLOAD_FOLDER_LOGOS_REL)
    ensure_folder_exists(UPLOAD_FOLDER_EXPENSES_REL)

# --------------------------------------------

class Propietario(db.Model):
    __tablename__ = 'propietario'
    id = db.Column(Integer, primary_key=True)
    nombre = db.Column(String(150), nullable=False)
    nif = db.Column(String(20), unique=True, nullable=False)
    direccion = db.Column(String(200))
    codigo_postal = db.Column(String(10))
    ciudad = db.Column(String(80))
    telefono = db.Column(String(20))
    email = db.Column(String(120), unique=False, nullable=True) # Permitir emails duplicados
    cuenta_bancaria = db.Column(String(50))
    fecha_creacion = db.Column(DateTime, default=datetime.utcnow)
    pie_factura = db.Column(db.Text, nullable=True) # Puede ser largo, usamos Text
    propiedades = db.relationship('Propiedad', backref='propietario_ref', lazy='select', cascade="all, delete-orphan")
    documentos_ruta_base = db.Column(db.String(512), nullable=True) # Ruta a la carpeta base del propietario
    def __repr__(self): return f'<Propietario {self.id}: {self.nombre}>'

class Inquilino(db.Model):
    __tablename__ = 'inquilino'
    id = db.Column(Integer, primary_key=True)
    nombre = db.Column(String(150), nullable=False)
    nif = db.Column(String(20), unique=True, nullable=False)
    direccion = db.Column(String(200))
    codigo_postal = db.Column(String(10))
    ciudad = db.Column(String(80))
    telefono = db.Column(String(20))
    email = db.Column(String(120), unique=True, nullable=True) # Email puede ser único o nulo
    estado = db.Column(String(20), default='activo', nullable=False)
    fecha_inicio_relacion = db.Column(db.Date)
    fecha_fin_relacion = db.Column(db.Date)
    fecha_creacion = db.Column(DateTime, default=datetime.utcnow)
    contratos = db.relationship('Contrato', backref='inquilino_ref', lazy='select')
    facturas = db.relationship('Factura', backref='inquilino_ref', lazy='select')
    def __repr__(self): return f'<Inquilino {self.id}: {self.nombre}>'

class Propiedad(db.Model):
    __tablename__ = 'propiedad'
    id = db.Column(Integer, primary_key=True)
    direccion = db.Column(String(200), nullable=False)
    ciudad = db.Column(String(80))
    codigo_postal = db.Column(String(10))
    referencia_catastral = db.Column(String(20), nullable=True) # Puede ser nulo
    tipo = db.Column(String(50))
    descripcion = db.Column(db.Text)
    numero_local = db.Column(db.String(50), nullable=True)
    superficie_construida = db.Column(db.Integer, nullable=True) # NUEVO: Para M2
    ano_construccion = db.Column(db.Integer, nullable=True)    # NUEVO: Para año
    estado_ocupacion = db.Column(String(20), default='vacia', nullable=False)
    fecha_creacion = db.Column(DateTime, default=datetime.utcnow)
    propietario_id = db.Column(Integer, db.ForeignKey('propietario.id'), nullable=False)
    contratos = db.relationship('Contrato', backref='propiedad_ref', lazy='select')
    facturas = db.relationship('Factura', backref='propiedad_ref', lazy='select')
    
    __table_args__ = (
        db.UniqueConstraint('referencia_catastral', 'propietario_id', name='uq_propiedad_refcat_propietario_id'),
    )    
    
    def __repr__(self): return f'<Propiedad {self.id}: {self.direccion}>'

class Contrato(db.Model):
    __tablename__ = 'contrato'
    id = db.Column(Integer, primary_key=True)
    numero_contrato = db.Column(String(50), unique=True, nullable=False)
    tipo = db.Column(String(50), nullable=False, default='Local de Negocio')
    fecha_inicio = db.Column(db.Date, nullable=False)
    fecha_fin = db.Column(db.Date)
    precio_mensual = db.Column(Numeric(10, 2), nullable=False)
    deposito = db.Column(Numeric(10, 2), default=Decimal('0.00'))
    dia_pago = db.Column(Integer, default=1)
    estado = db.Column(String(20), default='pendiente', nullable=False) # pendiente, activo, cancelado, expirado
    notas = db.Column(db.Text)
    actualiza_ipc = db.Column(Boolean, default=False, nullable=False)
    actualiza_irav = db.Column(Boolean, default=False, nullable=False)
    ipc_ano_inicio = db.Column(Integer) # Año de referencia para el primer índice
    ipc_mes_inicio = db.Column(Integer) # Mes de referencia para el primer índice
    aplicar_iva = db.Column(Boolean, default=True, nullable=False)
    aplicar_irpf = db.Column(Boolean, default=True, nullable=False)
    fecha_creacion = db.Column(DateTime, default=datetime.utcnow)
    propiedad_id = db.Column(Integer, db.ForeignKey('propiedad.id'), nullable=False)
    inquilino_id = db.Column(Integer, db.ForeignKey('inquilino.id'), nullable=False)
    facturas = db.relationship('Factura', backref='contrato_ref', lazy='select', cascade="all, delete-orphan")
    documentos = db.relationship('Documento', backref='contrato_ref', lazy='select', cascade="all, delete-orphan")
    gastos = db.relationship('Gasto', backref='contrato', lazy='dynamic', cascade="all, delete-orphan")
    # --- NUEVOS CAMPOS PARA SERIES DE FACTURACIÓN ---
    serie_facturacion_prefijo = db.Column(db.String(30), nullable=True) # Aumentado un poco la longitud por si acaso
    serie_facturacion_ultimo_numero = db.Column(db.Integer, default=0, nullable=True)
    serie_facturacion_ano_actual = db.Column(db.Integer, nullable=True)
    serie_facturacion_formato_digitos = db.Column(db.Integer, default=4, nullable=True) # ej. 4 para 0001, 3 para 001

    # NUEVOS CAMPOS PARA ACTUALIZACIONES DIVERSAS
    # Tipo de actualización principal (para elegir la lógica)
    # Opciones: 'indice' (IPC/IRAV), 'fijo', 'indice_mas_fijo', 'manual' (sin actualización automática)
    tipo_actualizacion_renta = db.Column(String(20), default='indice', nullable=False)

    # Para 'fijo' o 'indice_mas_fijo'
    importe_actualizacion_fija = db.Column(Numeric(10, 2), nullable=True) # Puede ser positivo o negativo

    # Para controlar cuándo se aplica la actualización fija (si es anual o en una fecha específica)
    # Podrías usar el mes de aniversario del contrato (fecha_inicio.month) o añadir campos específicos:
    mes_aplicacion_fija = db.Column(Integer, nullable=True) # 1-12, si es diferente al aniversario para el fijo
    # Si el importe fijo es un % en lugar de un monto, necesitarías otro campo:
    # porcentaje_actualizacion_fija = db.Column(Numeric(5,2), nullable=True) # Ej: 2.5 para 2.5%

    # Para manejar atrasos y actualizaciones diferidas
    # Indica si la actualización de índice se aplica retroactivamente cuando el índice está disponible
    aplicar_indice_retroactivo = db.Column(Boolean, default=False, nullable=False)
    # Almacena la última renta base conocida ANTES de una posible actualización de índice pendiente
    renta_base_pre_actualizacion_pendiente = db.Column(Numeric(10, 2), nullable=True)
    # Almacena el mes/año del índice que está pendiente de aplicar
    indice_pendiente_mes = db.Column(Integer, nullable=True)
    indice_pendiente_ano = db.Column(Integer, nullable=True)
    indice_pendiente_tipo = db.Column(String(5), nullable=True) # 'IPC' o 'IRAV'
    indice_pendiente_mes_original_aplicacion = db.Column(db.Integer, nullable=True)
    indice_pendiente_ano_original_aplicacion = db.Column(db.Integer, nullable=True)

    __table_args__ = (
        # La constraint actual chk_ipc_or_irav_not_both podría necesitar ajustarse o eliminarse
        # si 'actualiza_ipc' y 'actualiza_irav' solo se usan cuando tipo_actualizacion_renta == 'indice'.
        # O podrías tener una constraint que diga:
        # IF tipo_actualizacion_renta = 'indice' THEN (actualiza_ipc XOR actualiza_irav)
        # IF tipo_actualizacion_renta = 'fijo' THEN importe_actualizacion_fija IS NOT NULL
        # Etc. Esto se puede hacer con CHECK constraints más complejas o en la lógica de la app.
        CheckConstraint(
            "NOT (actualiza_ipc = 1 AND actualiza_irav = 1) OR tipo_actualizacion_renta != 'indice'",
            name='chk_solo_un_tipo_de_indice_si_aplica_indice'
        ),
        CheckConstraint(
            "(tipo_actualizacion_renta = 'fijo' AND importe_actualizacion_fija IS NOT NULL) OR "
            "(tipo_actualizacion_renta = 'indice_mas_fijo' AND importe_actualizacion_fija IS NOT NULL) OR "
            "tipo_actualizacion_renta NOT IN ('fijo', 'indice_mas_fijo')",
            name='chk_importe_fijo_si_tipo_fijo'
        ),
        UniqueConstraint('numero_contrato', name='uq_numero_contrato'),
    )
    @property
    def progress_percent(self):
        if self.estado == 'expirado': return 100
        if self.estado in ('pendiente', 'cancelado'): return 0
        if self.estado == 'activo' and isinstance(self.fecha_inicio, date):
            today = date.today()
            if today < self.fecha_inicio: return 0
            if not self.fecha_fin: return 5 # Activo indefinido, un pequeño %
            if today >= self.fecha_fin: return 100
            total_d = (self.fecha_fin - self.fecha_inicio).days
            elapsed_d = (today - self.fecha_inicio).days
            if total_d <= 0: return 100 if elapsed_d >= 0 else 0
            return min(int((elapsed_d / total_d) * 100), 100)
        return 0
    def __repr__(self): return f'<Contrato {self.id}: {self.numero_contrato}>'

class Documento(db.Model):
    __tablename__ = 'documento'
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(200), nullable=False, unique=True)
    original_filename = db.Column(db.String(200), nullable=False)
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)
    contrato_id = db.Column(db.Integer, db.ForeignKey('contrato.id'), nullable=False)
    def __repr__(self): return f'<Documento {self.id}: {self.original_filename}>'

class Factura(db.Model):
    __tablename__ = 'factura'
    id = db.Column(Integer, primary_key=True)
    numero_factura = db.Column(db.String(70), unique=True, nullable=False)
    fecha_emision = db.Column(db.Date, nullable=False) # DEBE TENER VALOR
    subtotal = db.Column(Numeric(10, 2), nullable=False, default=Decimal('0.00'))
    iva = db.Column(Numeric(10, 2), nullable=False, default=Decimal('0.00'))
    irpf = db.Column(Numeric(10, 2), nullable=False, default=Decimal('0.00'))
    total = db.Column(Numeric(10, 2), nullable=False) # DEBE TENER VALOR
    estado = db.Column(String(20), default='pendiente', nullable=False) # pendiente, pagada, cancelada
    notas = db.Column(db.Text, nullable=True)
    items_json = db.Column(db.Text, nullable=False) # Contendrá un JSON array de los conceptos
    contrato_id = db.Column(Integer, db.ForeignKey('contrato.id'), nullable=True) # Puede ser una factura manual sin contrato
    inquilino_id = db.Column(Integer, db.ForeignKey('inquilino.id'), nullable=False)
    propiedad_id = db.Column(Integer, db.ForeignKey('propiedad.id'), nullable=False)
    gastos_incluidos = db.relationship('Gasto', backref='factura', lazy='select')
    # --- CAMPOS PARA ALMACENAR LAS TASAS USADAS EN ESTA FACTURA ---
    iva_rate_applied = db.Column(Numeric(5, 4), nullable=True)  # Ej: 0.21, 0.10, 0.00
    irpf_rate_applied = db.Column(Numeric(5, 4), nullable=True) # Ej: 0.19, 0.07, 0.00
    indice_aplicado_info = db.Column(db.JSON, nullable=True) # Almacena {'type': 'IPC', 'month': 11, 'year': 2024, 'percentage': 3.5}
    # -------------------------------------------------------------
    @property
    def items(self):
        try: return json.loads(self.items_json or '[]')
        except json.JSONDecodeError: return []
    def __repr__(self): return f'<Factura {self.id}: {self.numero_factura}>'
    
    @property
    def numero_factura_mostrado_al_cliente(self):
        """
        Devuelve la parte "visible" del número de factura.
        Asume que el formato almacenado es "C{ID_CONTRATO}-{PARTE_VISIBLE}"
        o "INVDEF-{...}-{PARTE_VISIBLE_FALLBACK}"
        """
        if self.numero_factura:
            if self.contrato_id and self.numero_factura.startswith(f"C{self.contrato_id}-"):
                parts = self.numero_factura.split('-', 1)
                if len(parts) > 1:
                    return parts[1]
        return self.numero_factura

    def __repr__(self):
        return f'<Factura {self.id}: {self.numero_factura}>'

class IPCData(db.Model):
    __tablename__ = 'ipc_data'
    id = db.Column(db.Integer, primary_key=True)
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)
    percentage_change = db.Column(Numeric(6, 3), nullable=False)
    __table_args__ = (UniqueConstraint('year', 'month', name='uix_ipc_year_month'),)
    def __repr__(self): return f'<IPCData {self.year}-{self.month:02d}: {self.percentage_change}%>'

class IRAVData(db.Model):
    __tablename__ = 'irav_data'
    id = db.Column(db.Integer, primary_key=True)
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False) # Mes al que se aplica el índice (ej. el publicado en marzo es para aplicar en abril)
    percentage_change = db.Column(Numeric(6, 3), nullable=False) # Variación interanual
    # El INE publica el IRAV con un mes de desfase (ej. el dato de marzo se publica en abril y es el de febrero)
    # Podríamos añadir un campo 'publication_date' o 'reference_period_end_date' si es necesario
    # Por ahora, asumimos que 'year' y 'month' se refieren al periodo al que afecta la variación.

    __table_args__ = (UniqueConstraint('year', 'month', name='uix_irav_year_month'),)

    def __repr__(self):
        return f'<IRAVData {self.year}-{self.month:02d}: {self.percentage_change}%>'


class SystemSettings(db.Model):
    __tablename__ = 'system_settings'
    id = db.Column(db.Integer, primary_key=True, default=1) # Asegurar ID 1
    language = db.Column(db.String(5), default='es')
    timezone = db.Column(db.String(50), default='UTC')
    date_format = db.Column(db.String(20), default='%d/%m/%Y')
    currency = db.Column(db.String(3), default='EUR')
    dark_mode = db.Column(db.Boolean, default=False)
    show_tutorial = db.Column(db.Boolean, default=True)
    default_view = db.Column(db.String(20), default='table')
    items_per_page = db.Column(db.Integer, default=10)
    show_stats = db.Column(db.Boolean, default=True)
    show_alerts = db.Column(db.Boolean, default=True)
    company_name = db.Column(db.String(200))
    company_nif = db.Column(db.String(50))
    company_address = db.Column(db.String(200))
    company_city = db.Column(db.String(80))
    company_zip = db.Column(db.String(20))
    company_country = db.Column(db.String(80))
    company_phone = db.Column(db.String(20))
    company_email = db.Column(db.String(120))
    company_website = db.Column(db.String(200))
    company_logo_filename = db.Column(db.String(200))
    email_new_contract = db.Column(db.Boolean, default=False)
    email_payment_received = db.Column(db.Boolean, default=False)
    email_payment_overdue = db.Column(db.Boolean, default=False)
    email_maintenance = db.Column(db.Boolean, default=False)
    system_reminders = db.Column(db.Boolean, default=False)
    system_alerts = db.Column(db.Boolean, default=False)
    # sender_email = db.Column(db.String(120)) # Eliminado, usar config email
    # sender_name = db.Column(db.String(120)) # Eliminado, usar config email
    log_activity = db.Column(db.Boolean, default=False)
    backup_frequency = db.Column(db.String(20), default='never')
    iva_rate = db.Column(Numeric(5, 4), default=DEFAULT_IVA_RATE)
    irpf_rate = db.Column(Numeric(5, 4), default=DEFAULT_IRPF_RATE)
    generate_invoice_if_index_missing = db.Column(db.Boolean, default=True, nullable=False)
    # default=False significa que por defecto NO se generará si falta IPC

    # --- CAMPOS PARA CONFIGURACIÓN DE CORREO ---
    mail_server = db.Column(db.String(120), default='smtp.office365.com')
    mail_port = db.Column(db.Integer, default=587)
    mail_use_tls = db.Column(db.Boolean, default=True)
    mail_use_ssl = db.Column(db.Boolean, default=False)
    mail_username = db.Column(db.String(120))
    mail_default_sender = db.Column(db.String(120))
    mail_sender_display_name = db.Column(db.String(120), default='RentalSys')
    def __repr__(self): return f'<SystemSettings {self.id}>'

class Gasto(db.Model):
    __tablename__ = 'gasto'
    id = db.Column(db.Integer, primary_key=True)
    contrato_id = db.Column(db.Integer, db.ForeignKey('contrato.id'), nullable=False, index=True)
    concepto = db.Column(db.String(255), nullable=False)
    importe = db.Column(db.Numeric(10, 2), nullable=False)
    month = db.Column(db.Integer, nullable=True)
    year = db.Column(db.Integer, nullable=True)
    filename = db.Column(db.String(200), nullable=False, unique=True)
    original_filename = db.Column(db.String(200), nullable=False)
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)
    integrated = db.Column(db.Boolean, default=False, nullable=False) # Redundante si usamos 'estado'
    estado = db.Column(db.String(20), nullable=False, default='Pendiente', index=True)  # Pendiente | Facturado
    factura_id = db.Column(db.Integer, db.ForeignKey('factura.id'), nullable=True, index=True)
    def __repr__(self): return f'<Gasto {self.id}: {self.concepto} - {self.importe} ({self.estado})>'

class Notification(db.Model):
    __tablename__ = 'notification'
    id = db.Column(db.Integer, primary_key=True)
    message = db.Column(db.Text, nullable=False)
    level = db.Column(db.String(20), nullable=False, default='info') # 'info', 'warning', 'danger', 'success'
    is_read = db.Column(db.Boolean, default=False, nullable=False, index=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    related_url = db.Column(db.String(255), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=True, index=True)
    # user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True) # Asociar a usuario si es necesario
    def __repr__(self): return f'<Notification {self.id} [{self.level}] Read: {self.is_read}>'

# --- NUEVO MODELO USER ---
class User(UserMixin, db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False) # Aumentada longitud para hashes modernos
    role = db.Column(db.String(20), nullable=False, default='usuario') # Roles: 'admin', 'gestor', 'usuario'
    is_active = db.Column(db.Boolean, default=True, nullable=False) # Para Flask-Login
    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow)
    notifications = db.relationship('Notification', backref='user', lazy='dynamic', foreign_keys=[Notification.user_id], cascade="all, delete-orphan")

    # Relación Muchos-a-Muchos con Propietario (para roles gestor/usuario)
    # 'secondary' apunta a la tabla de asociación definida globalmente
    # 'backref' crea dinámicamente Propietario.usuarios_asignados
    propietarios_asignados = db.relationship(
        'Propietario',
        secondary=user_propietario_association,
        backref=db.backref('usuarios_asignados', lazy='dynamic'),
        lazy='select' # Carga los propietarios solo cuando se accede a `user.propietarios_asignados`
    )

    def set_password(self, password):
        """Genera un hash seguro para la contraseña."""
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256:600000', salt_length=16)

    def check_password(self, password):
        """Verifica si la contraseña proporcionada coincide con el hash."""
        if not self.password_hash: # Manejo por si el hash no existe
            return False
        return check_password_hash(self.password_hash, password)

    # UserMixin proporciona implementaciones predeterminadas para:
    # is_authenticated, is_active (usa nuestro campo), is_anonymous, get_id

    def __repr__(self):
        return f'<User {self.id}: {self.username} [{self.role}]>'
        
        
class HistorialActualizacionRenta(db.Model):
    __tablename__ = 'historial_actualizacion_renta'
    id = db.Column(db.Integer, primary_key=True)
    contrato_id = db.Column(db.Integer, db.ForeignKey('contrato.id'), nullable=False, index=True)
    factura_id = db.Column(db.Integer, db.ForeignKey('factura.id'), nullable=True, index=True) # Factura que aplicó esta actualización
    fecha_actualizacion = db.Column(db.Date, nullable=False, default=date.today) # Fecha en que se aplicó
    
    renta_anterior = db.Column(Numeric(10, 2), nullable=False)
    renta_nueva = db.Column(Numeric(10, 2), nullable=False)
    
    tipo_actualizacion = db.Column(db.String(50)) # 'IPC', 'IRAV', 'Fijo', 'IndiceMasFijo', 'Manual'
    
    # Campos específicos del índice (si aplica)
    indice_nombre = db.Column(db.String(10), nullable=True) # 'IPC' o 'IRAV'
    indice_mes = db.Column(db.Integer, nullable=True)
    indice_ano = db.Column(db.Integer, nullable=True)
    indice_porcentaje = db.Column(Numeric(6, 3), nullable=True) # Porcentaje del índice aplicado
    
    # Campo para importe fijo (si aplica)
    importe_fijo_aplicado = db.Column(Numeric(10, 2), nullable=True)

    descripcion_adicional = db.Column(db.Text, nullable=True) # Para notas o detalles como "Resolución pendiente"

    contrato = db.relationship('Contrato', backref=db.backref('historial_actualizaciones', lazy='dynamic', cascade="all, delete-orphan"))
    factura = db.relationship('Factura', backref=db.backref('actualizacion_renta_origen', uselist=False))

    def __repr__(self):
        return f'<HistorialActualizacionRenta {self.id} Contrato {self.contrato_id}: {self.renta_anterior}->{self.renta_nueva}>'        