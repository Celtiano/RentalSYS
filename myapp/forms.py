# myapp/forms.py
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField, SelectField, SelectMultipleField, TextAreaField
from wtforms.validators import DataRequired, Length, Email, EqualTo, Optional, ValidationError, Regexp
from wtforms.widgets import ListWidget, CheckboxInput # Para SelectMultipleField
from .models import User, Propietario # Importar modelos necesarios
import re

# --- FORMULARIO VACÍO PARA GENERAR CSRF TOKEN ---
class CSRFOnlyForm(FlaskForm):
    pass
    
    
# --- FORMULARIO DE LOGIN ---
class LoginForm(FlaskForm):
    username = StringField('Nombre de Usuario', validators=[DataRequired(), Length(min=3, max=80)])
    password = PasswordField('Contraseña', validators=[DataRequired()])
    remember_me = BooleanField('Recordarme')
    submit = SubmitField('Iniciar Sesión')

# --- FORMULARIO CREAR USUARIO (Admin) ---
class UserCreateForm(FlaskForm):
    username = StringField('Nombre de Usuario', validators=[DataRequired(), Length(min=3, max=80)])
    email = StringField('Email', validators=[DataRequired(), Email(), Length(max=120)])
    password = PasswordField('Contraseña', validators=[DataRequired(), Length(min=8, message='La contraseña debe tener al menos 8 caracteres.')])
    confirm_password = PasswordField('Confirmar Contraseña', validators=[DataRequired(), EqualTo('password', message='Las contraseñas deben coincidir.')])
    role = SelectField('Rol', choices=[('admin', 'Administrador'), ('gestor', 'Gestor'), ('usuario', 'Usuario')], validators=[DataRequired()])
    is_active = BooleanField('Activo', default=True)
    # Campo múltiple para asignar propietarios (solo relevante para gestor/usuario)
    propietarios = SelectMultipleField(
        'Propietarios Asignados (para rol Gestor/Usuario)',
        coerce=int, # Los valores serán IDs enteros
        widget=ListWidget(prefix_label=False), # Estilo opcional
        option_widget=CheckboxInput(), # Mostrar como checkboxes
        validators=[Optional()] # No es obligatorio al crear, se pueden asignar después
    )
    submit = SubmitField('Crear Usuario')

    # Validadores personalizados para asegurar unicidad
    def validate_username(self, username):
        user = User.query.filter_by(username=username.data).first()
        if user:
            raise ValidationError('Este nombre de usuario ya está en uso.')

    def validate_email(self, email):
        user = User.query.filter_by(email=email.data).first()
        if user:
            raise ValidationError('Este email ya está registrado.')

    # Método para poblar las opciones del SelectMultipleField dinámicamente
    def __init__(self, *args, **kwargs):
        super(UserCreateForm, self).__init__(*args, **kwargs)
        # Obtener todos los propietarios para las opciones
        self.propietarios.choices = [(p.id, p.nombre) for p in Propietario.query.order_by(Propietario.nombre).all()]


# --- FORMULARIO EDITAR USUARIO (Admin) ---
class UserEditForm(FlaskForm):
    username = StringField('Nombre de Usuario', validators=[DataRequired(), Length(min=3, max=80)])
    email = StringField('Email', validators=[DataRequired(), Email(), Length(max=120)])
    # La contraseña es opcional al editar
    password = PasswordField('Nueva Contraseña (dejar vacío para no cambiar)')
    confirm_password = PasswordField('Confirmar Nueva Contraseña', validators=[EqualTo('password', message='Las contraseñas deben coincidir.')])
    role = SelectField('Rol', choices=[('admin', 'Administrador'), ('gestor', 'Gestor'), ('usuario', 'Usuario')], validators=[DataRequired()])
    is_active = BooleanField('Activo')
    propietarios = SelectMultipleField(
        'Propietarios Asignados (para rol Gestor/Usuario)',
        coerce=int,
        widget=ListWidget(prefix_label=False),
        option_widget=CheckboxInput(),
        validators=[Optional()]
    )
    submit = SubmitField('Guardar Cambios')

    # Guardar el ID del usuario que se está editando para las validaciones
    def __init__(self, original_username, original_email, *args, **kwargs):
        super(UserEditForm, self).__init__(*args, **kwargs)
        self.original_username = original_username
        self.original_email = original_email
        # Poblar choices igual que en Crear
        self.propietarios.choices = [(p.id, p.nombre) for p in Propietario.query.order_by(Propietario.nombre).all()]

    def validate_username(self, username):
        if username.data != self.original_username:
            user = User.query.filter_by(username=username.data).first()
            if user:
                raise ValidationError('Este nombre de usuario ya está en uso.')

    def validate_email(self, email):
        if email.data != self.original_email:
            user = User.query.filter_by(email=email.data).first()
            if user:
                raise ValidationError('Este email ya está registrado.')

# --- Aquí añadirías tus otros formularios (PropietarioForm, etc.) ---
# Ejemplo rápido PropietarioForm para ilustrar
class PropietarioForm(FlaskForm):
    nombre = StringField('Nombre Completo', validators=[DataRequired(), Length(min=3, max=150)])
    nif = StringField('NIF/CIF', validators=[DataRequired(), Length(min=5, max=20)])
    email = StringField('Email', validators=[Optional(), Email(message="Formato de email inválido."), Length(max=120)])
    direccion = StringField('Dirección', validators=[Optional(), Length(max=200)])
    codigo_postal = StringField('Código Postal', validators=[Optional(), Length(max=10)])
    ciudad = StringField('Ciudad', validators=[Optional(), Length(max=80)])
    telefono = StringField('Teléfono', validators=[Optional(), Length(max=20)])
    cuenta_bancaria = StringField('Cuenta Bancaria (IBAN)', validators=[Optional()])
    pie_factura = TextAreaField('Notas Pie de Factura', validators=[Optional(), Length(max=1000)]) # Permitir hasta 1000 caracteres
    documentos_ruta_base = StringField(
        'Ruta Base para Documentos', 
        validators=[Optional(), Length(max=500)],
        description="Ej: C:\\Users\\TuUsuario\\Documents\\Alquileres\\Propietario_Juan. Si se deja vacío, se usará la carpeta por defecto de la aplicación."
    )
    submit = SubmitField('Guardar Propietario') # Puedes quitar esto si el botón está en HTML

    def __init__(self, *args, **kwargs):
        # Extraer 'original_obj' de kwargs ANTES de pasarlo al constructor padre
        self.original_obj = kwargs.pop('original_obj', None)
        super(PropietarioForm, self).__init__(*args, **kwargs)
        # Después de super().__init__(), self.obj (si se pasó obj= al instanciar)
        # y self.original_obj estarán disponibles.

    def validate_nif(self, nif_field):
        nif_data = nif_field.data
        query = Propietario.query.filter_by(nif=nif_data)
        
        # Usar self.original_obj que definimos en __init__
        if self.original_obj and self.original_obj.id:
            query = query.filter(Propietario.id != self.original_obj.id)
        
        propietario_existente = query.first()
        
        if propietario_existente:
            raise ValidationError('Este NIF ya está registrado para otro propietario.')
 
    def validate_documentos_ruta_base(self, field):
        if field.data:
            path = field.data.strip()
            if not path:
                field.data = None 
                return

            # ========= INICIO DE LA CORRECCIÓN =========
            # Quitar ':' de los caracteres inválidos para permitir C:\ etc.
            invalid_chars_pattern = r'[<>|"|?*\x00-\x1F]' # ':' eliminado
            # ========= FIN DE LA CORRECCIÓN =========

            if re.search(invalid_chars_pattern, path):
                raise ValidationError("La ruta contiene caracteres inválidos (ej: < > | \" ? *).")
            
            # No validar os.path.exists() aquí, ya que la ruta podría no existir aún.
            field.data = path