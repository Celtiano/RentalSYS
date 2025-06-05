# myapp/utils/file_helpers.py (NUEVO ARCHIVO o añadir a uno existente de utils)
import os
from flask import current_app
from werkzeug.utils import secure_filename # Ya la usas

def get_owner_document_path(propietario, subfolder_type, year=None, filename_to_secure=None):
    """
    Construye la ruta completa para un documento de un propietario.
    Crea las carpetas necesarias si no existen.

    Args:
        propietario (Propietario): El objeto Propietario.
        subfolder_type (str): "Facturas", "Gastos", "Contratos".
        year (int, optional): El año para subcarpetas anuales (Facturas, Gastos).
        filename_to_secure (str, optional): El nombre de archivo original para asegurar.

    Returns:
        str: La ruta completa a la carpeta de destino, o None si hay error.
             Si filename_to_secure se proporciona, devuelve la ruta completa al archivo.
    """
    if not propietario:
        current_app.logger.error("get_owner_document_path: Objeto Propietario no proporcionado.")
        return None

    base_path_owner_specific = None
    if propietario.documentos_ruta_base and propietario.documentos_ruta_base.strip():
        base_path_owner_specific = os.path.abspath(os.path.expanduser(propietario.documentos_ruta_base.strip()))
        # Validar que la ruta no intente salirse de un directorio "seguro" si es necesario (más complejo)
        # Por ahora, confiamos en la ruta proporcionada.
    else:
        # Fallback a la carpeta de instancia de la aplicación si el propietario no tiene ruta específica
        base_path_owner_specific = os.path.join(current_app.instance_path, 'owner_documents', f"owner_{propietario.id}")
        # current_app.logger.info(f"Propietario {propietario.id} sin ruta base, usando fallback: {base_path_owner_specific}")

    if not base_path_owner_specific: # Doble check
        current_app.logger.error(f"No se pudo determinar la ruta base para el propietario {propietario.id}.")
        return None

    # Construir subcarpetas
    # 1. Carpeta del tipo de documento (Facturas, Gastos, Contratos)
    path_with_type = os.path.join(base_path_owner_specific, subfolder_type)

    # 2. Carpeta del año (si aplica)
    final_folder_path = path_with_type
    if year and subfolder_type in ["Facturas Alquiler", "Facturas Gastos"]:
        final_folder_path = os.path.join(path_with_type, str(year))
    
    # Crear carpetas si no existen
    try:
        os.makedirs(final_folder_path, exist_ok=True)
    except OSError as e:
        current_app.logger.error(f"Error creando directorio '{final_folder_path}': {e}")
        # Podrías querer usar un fallback a la instancia aquí si la ruta del usuario falla
        # Por ahora, si falla la creación de la ruta del usuario, devolvemos None.
        # Fallback de ejemplo:
        # current_app.logger.warning(f"Usando fallback a instance_path para propietario {propietario.id} debido a error en ruta personalizada.")
        # fallback_base = os.path.join(current_app.instance_path, 'fallback_owner_documents', f"owner_{propietario.id}", subfolder_type)
        # if year and subfolder_type in ["Facturas", "Gastos"]:
        #     fallback_base = os.path.join(fallback_base, str(year))
        # try:
        #     os.makedirs(fallback_base, exist_ok=True)
        #     final_folder_path = fallback_base
        # except Exception as e_fb:
        #     current_app.logger.error(f"Error creando directorio fallback '{fallback_base}': {e_fb}")
        #     return None # Falló incluso el fallback
        return None # Si falla la ruta principal y no hay fallback robusto

    if filename_to_secure:
        safe_name = secure_filename(filename_to_secure)
        return os.path.join(final_folder_path, safe_name)
    
    return final_folder_path