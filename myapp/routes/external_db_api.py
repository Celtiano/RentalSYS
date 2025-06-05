import os
import fdb 
from datetime import date, datetime
from flask import Blueprint, jsonify, request, current_app
from flask_login import login_required

from ..decorators import role_required
from .. import db 
from ..models import Propietario as PropietarioApp, Inquilino as InquilinoApp, Propiedad as PropiedadApp
from ..utils.file_helpers import get_owner_document_path


external_db_api_bp = Blueprint('external_db_api_bp', __name__, url_prefix='/api/external')

def get_firebird_connection(db_alias="GENERAL"):
    host = os.getenv(f'FB_{db_alias.upper()}_HOST')
    port_str = os.getenv(f'FB_{db_alias.upper()}_PORT')
    db_path = os.getenv(f'FB_{db_alias.upper()}_DB_PATH')
    user = os.getenv(f'FB_{db_alias.upper()}_USER')
    password = os.getenv(f'FB_{db_alias.upper()}_PASSWORD')
    charset = os.getenv(f'FB_{db_alias.upper()}_CHARSET', 'NONE')    
    fb_client_library_path = os.getenv(f'FB_{db_alias.upper()}_CLIENT_LIB')

    if not all([host, port_str, db_path, user, password]):
        current_app.logger.error(f"Faltan variables de entorno para la conexión Firebird DB alias '{db_alias}'")
        return None    
    
    dsn = None 
    try:
        port = int(port_str)
        dsn = f"{host}/{port}:{db_path}"
        connect_args = {'dsn': dsn, 'user': user, 'password': password, 'charset': charset}
        
        if fb_client_library_path:
            if os.path.exists(fb_client_library_path):
                connect_args['fb_library_name'] = fb_client_library_path
                current_app.logger.info(f"Intentando usar librería cliente Firebird especificada: {fb_client_library_path}")
            else:
                current_app.logger.warning(f"Librería cliente Firebird especificada en FB_{db_alias.upper()}_CLIENT_LIB ('{fb_client_library_path}') no encontrada. fdb intentará la detección automática.")
        else:
            current_app.logger.info(f"No se especificó FB_{db_alias.upper()}_CLIENT_LIB. fdb intentará la detección automática de la librería cliente.")
        
        current_app.logger.info(f"Intentando conectar a Firebird '{db_alias}' con DSN: '{dsn}', User: '{user}', Charset: '{charset}'")
        conn = fdb.connect(**connect_args)
        current_app.logger.info(f"Conexión a Firebird '{db_alias}' exitosa.")
        return conn
    except Exception as e:
        error_details = f"Error al conectar con Firebird DB alias '{db_alias}'"
        if dsn: error_details += f" usando DSN '{dsn}'"
        if 'connect_args' in locals() and connect_args.get('fb_library_name'): error_details += f" con fb_library_name='{connect_args['fb_library_name']}'"
        error_details += f": {e}"
        current_app.logger.error(error_details, exc_info=True)
        return None


def format_iban_display(iban_raw):
    """Formatea un IBAN para visualización: ESXX XXXX XXXX XXXX XXXX XXXX"""
    if not iban_raw or not isinstance(iban_raw, str):
        return iban_raw # Devuelve como está si es None o no es string
    
    iban_clean = iban_raw.replace(" ", "").upper()
    if len(iban_clean) < 4: # No se puede formatear si es muy corto
        return iban_clean

    # Asumimos que los dos primeros caracteres son el código de país (ES)
    # y los dos siguientes los dígitos de control.
    # Luego agrupamos de 4 en 4.
    parts = [iban_clean[:4]] # ESXX
    for i in range(4, len(iban_clean), 4):
        parts.append(iban_clean[i:i+4])
    return " ".join(parts)


@external_db_api_bp.route('/lookup_propietario_general/<string:nif_to_lookup>', methods=['GET'])
@login_required
@role_required('admin', 'gestor')
def lookup_propietario_from_general_db(nif_to_lookup):
    nif_limpio = nif_to_lookup.strip()
    if not nif_limpio: return jsonify({"error": "NIF no proporcionado."}), 400
    
    existing_owner_in_app = PropietarioApp.query.filter_by(nif=nif_limpio).first()
    
    datos_de_clientes_fb = None
    conn_fb = None
    cur = None

    try:
        conn_fb = get_firebird_connection("GENERAL")
        if conn_fb:
            cur = conn_fb.cursor()
            sql_query = """
                SELECT TRIM(CIFCLI), TRIM(NOMBRCLI_1), 
                       TRIM(COALESCE(SIGLFCLI, '')), TRIM(COALESCE(CALLFCLI, '')), TRIM(COALESCE(NUMEFCLI, '')), 
                       TRIM(COALESCE(ESCAFCLI, '')), TRIM(COALESCE(PISOFCLI, '')), TRIM(COALESCE(PUERFCLI, '')),
                       TRIM(CPOSFCLI), TRIM(POBLFCLI), TRIM(TEL1FCLI), TRIM(COALESCE(CORRFCLI_1, '')), 
                       TRIM(IBAN2CLI),
                       TRIM(COALESCE(REGISCLI, '')), TRIM(COALESCE(LIBROCLI, '')), TRIM(COALESCE(FOLIOCLI, '')),
                       TRIM(COALESCE(INSCRCLI, '')), TRIM(COALESCE(HOJACLI, '')),
                       TRIM(COALESCE(PFNOMCLI_1, '')), TRIM(COALESCE(PFAP1CLI_1, '')), TRIM(COALESCE(PFAP2CLI_1, '')),
                       TRIM(COALESCE(PJURICLI_1, ''))
                FROM CLIENTES WHERE TRIM(CIFCLI) = ?
            """
            cur.execute(sql_query, (nif_limpio,))
            row = cur.fetchone()
            if row:
                siglfcli = row[2].title() if row[2] else ''; callfcli_formateado = row[3].title() if row[3] else ''; 
                numefcli = row[4].upper() if row[4] else ''; escafcli = row[5].upper() if row[5] else ''; 
                pisofcli = row[6].upper() if row[6] else ''; puerfcli = row[7].upper() if row[7] else '' 
                part1_direccion = " ".join(filter(None,[siglfcli, callfcli_formateado])); 
                part2_direccion_fields = [numefcli, escafcli, pisofcli, puerfcli]; 
                part2_direccion_filtered = [part for part in part2_direccion_fields if part]; 
                direccion_str_part2 = " ".join(part2_direccion_filtered)
                if part1_direccion and direccion_str_part2: direccion_completa = f"{part1_direccion}, {direccion_str_part2}"
                elif part1_direccion: direccion_completa = part1_direccion
                elif direccion_str_part2: direccion_completa = direccion_str_part2
                else: direccion_completa = ""
                
                poblacion_ciudad = row[9].title() if row[9] else None
                email_contacto = row[11].lower() if row[11] else None
                
                pie_factura = ""
                if row[13]: 
                    pie_factura_parts = ["Inscrita en el R.M. de", row[13]]
                    if row[14]: pie_factura_parts.extend([", Libro", row[14]]) 
                    if row[15]: pie_factura_parts.extend([", Folio", row[15]]) 
                    if row[16]: pie_factura_parts.extend([", Inscripción", row[16]]) 
                    if row[17]: pie_factura_parts.extend([", Hoja", row[17]]) 
                    pie_factura = " ".join(pie_factura_parts) + "."
                
                nombre_para_ruta = f"{row[19]} {row[20]}, {row[18]}" if row[18] else (row[21] if row[21] else "")
                documentos_ruta_base_final = os.path.join("D:\\Archivo\\Expedientes\\", "".join(c if c.isalnum() or c in (' ', '_', '-', ',') else '_' for c in nombre_para_ruta.strip()), "Varios", "Arrendamientos") if nombre_para_ruta.strip() else ""
                
                # ========= INICIO FORMATEO IBAN =========
                iban_bruto = row[12] if row[12] else None
                iban_formateado = format_iban_display(iban_bruto)
                # ========= FIN FORMATEO IBAN =========

                datos_de_clientes_fb = {
                    "nif": row[0], "nombre": row[1], 
                    "direccion": direccion_completa.strip() or None, 
                    "codigo_postal": row[8], "ciudad": poblacion_ciudad, 
                    "telefono": row[10], "email": email_contacto, 
                    "cuenta_bancaria": iban_formateado, # Usar el IBAN formateado
                    "pie_factura": pie_factura or None, 
                    "documentos_ruta_base": documentos_ruta_base_final or None
                }
    except fdb.fbcore.DatabaseError as fb_error: 
        current_app.logger.error(f"Error FB (lookup_propietario GENERAL): {fb_error.args[0] if fb_error.args else fb_error}")
    except Exception as e_gen:
        current_app.logger.error(f"Error (lookup_propietario GENERAL): {e_gen}", exc_info=True)
    finally:
        if cur: 
            try: cur.close() 
            except Exception as e_cur: current_app.logger.warning(f"Error cerrando cursor FB (lookup_propietario GENERAL): {e_cur}")
        if conn_fb: 
            try: conn_fb.close()
            except Exception as e_close: current_app.logger.warning(f"Error cerrando conexión FB (lookup_propietario GENERAL): {e_close}")

    if existing_owner_in_app:
        return jsonify({"message": f"El NIF {nif_limpio} corresponde a un propietario existente en la app (ID: {existing_owner_in_app.id})." + (" Se encontraron datos en BD externa para actualizar." if datos_de_clientes_fb else " No se encontraron datos adicionales en BD externa."), "exists_in_app": True, "id_app": existing_owner_in_app.id, "data": datos_de_clientes_fb}), 200
    elif datos_de_clientes_fb:
        return jsonify({"message": "Datos de propietario encontrados en BD externa.", "exists_in_app": False, "data": datos_de_clientes_fb}), 200
    else:
        return jsonify({"message": f"No se encontró propietario con NIF {nif_limpio} en la BD externa.", "exists_in_app": False, "data": None}), 404

@external_db_api_bp.route('/lookup_inquilino_general/<string:nif_to_lookup>', methods=['GET'])
@login_required
@role_required('admin', 'gestor')
def lookup_inquilino_from_general_db(nif_to_lookup):
    nif_limpio = nif_to_lookup.strip()
    if not nif_limpio: return jsonify({"error": "NIF no proporcionado."}), 400
    
    existing_inquilino_in_app = InquilinoApp.query.filter_by(nif=nif_limpio).first()
    
    datos_de_datosfis = None
    conn_fb = None
    cur = None

    try:
        conn_fb = get_firebird_connection("GENERAL")
        if conn_fb:
            cur = conn_fb.cursor()
            sql_query_inquilino = """SELECT TRIM(NIF), TRIM(COALESCE(PFNOMBRE_1, '')), TRIM(COALESCE(PFAPELL1_1, '')), TRIM(COALESCE(PFAPELL2_1, '')), TRIM(COALESCE(PJURIDIC_1, '')), TRIM(COALESCE(SIGLA, '')), TRIM(COALESCE(CALLE, '')), TRIM(COALESCE(NUMERO, '')), TRIM(COALESCE(ESCALERA, '')), TRIM(COALESCE(PISO, '')), TRIM(COALESCE(PUERTA, '')), TRIM(CPOSTAL), TRIM(POBLACIO), TRIM(TELEFON1) FROM DATOSFIS WHERE TRIM(NIF) = ?"""
            cur.execute(sql_query_inquilino, (nif_limpio,))
            row = cur.fetchone()
            if row:
                nombre_inq_completo = " ".join(filter(None, [row[1], row[2], row[3]])) if row[1] else (row[4] if row[4] else "")
                sigla_inq = row[5].title(); calle_inq = row[6].title(); num_inq = row[7].upper(); esc_inq = row[8].upper(); piso_inq = row[9].upper(); puerta_inq = row[10].upper()
                p1_dir_i = " ".join(filter(None, [sigla_inq, calle_inq])); p2_dir_i = " ".join(filter(None, [num_inq, esc_inq, piso_inq, puerta_inq]))
                dir_comp_inq = f"{p1_dir_i}, {p2_dir_i}" if p1_dir_i and p2_dir_i else p1_dir_i or p2_dir_i
                poblacion_ciudad_inq = row[12].title() if row[12] else None
                datos_de_datosfis = {"nif": row[0], "nombre": nombre_inq_completo.strip(), "direccion": dir_comp_inq.strip(), "codigo_postal": row[11], "ciudad": poblacion_ciudad_inq, "telefono": row[13], "email": None}
    except fdb.fbcore.DatabaseError as fb_error: current_app.logger.warning(f"Error FB (lookup_inquilino DATOSFIS): {fb_error.args[0] if fb_error.args else fb_error}")
    except Exception as e_gen: current_app.logger.warning(f"Error (lookup_inquilino DATOSFIS): {e_gen}", exc_info=True)
    finally:
        if cur: 
            try: cur.close()
            except Exception as e_cur: current_app.logger.warning(f"Error cerrando cursor FB (lookup_inquilino DATOSFIS): {e_cur}")
        if conn_fb: 
            try: conn_fb.close()
            except Exception as e_close: current_app.logger.warning(f"Error cerrando conexión FB (lookup_inquilino DATOSFIS): {e_close}")

    if existing_inquilino_in_app:
        return jsonify({"message": f"El NIF {nif_limpio} corresponde a un inquilino existente en la app (ID: {existing_inquilino_in_app.id})." + (" Se encontraron datos en BD externa para actualizar." if datos_de_datosfis else " No se encontraron datos adicionales en BD externa."), "exists_in_app": True, "id_app": existing_inquilino_in_app.id, "data": datos_de_datosfis}), 200
    elif datos_de_datosfis:
        return jsonify({"message": "Datos de inquilino encontrados en BD externa (no existe en la app).", "exists_in_app": False, "data": datos_de_datosfis}), 200
    else:
        return jsonify({"message": f"No se encontró inquilino con NIF {nif_limpio} en la BD externa.", "exists_in_app": False, "data": None}), 404


@external_db_api_bp.route('/fetch_owner_assets_fiscal/<string:propietario_nif_app>', methods=['GET'])
@login_required
@role_required('admin', 'gestor')
def fetch_owner_assets_from_fiscal_db(propietario_nif_app):
    if not propietario_nif_app:
        return jsonify({"error": "NIF del propietario de la aplicación no proporcionado."}), 400

    propietario_en_app = PropietarioApp.query.filter_by(nif=propietario_nif_app).first()
    if not propietario_en_app:
        return jsonify({"error": f"No se encontró el propietario con NIF {propietario_nif_app} en la aplicación."}), 404

    conn_fiscal = get_firebird_connection("FISCAL")
    if not conn_fiscal:
        return jsonify({"error": "No se pudo conectar con la base de datos 'FISCAL'."}), 503

    conn_general = None 
    # ========= Asegurar inicialización de todas las variables usadas en la respuesta JSON =========
    todas_las_propiedades_fiscal = [] 
    todos_los_nifs_inquilinos_fiscal_set = set()
    inquilinos_data_para_importar = {} 
    errores_busqueda = []
    # =========================================================================================
    
    cur_fiscal = None
    cur_general = None

    try:
        cur_fiscal = conn_fiscal.cursor()
        
        sql_to_execute_fiscal = """
            SELECT 
                TRIM(COALESCE(FLOCALES.CLAVELOC, '')), TRIM(COALESCE(FLOCALES.REFERLOC, '')),
                TRIM(COALESCE(FLOCALES.SIGLALOC, '')), TRIM(COALESCE(FLOCALES.CALLELOC, '')), 
                TRIM(COALESCE(FLOCALES.NUMERLOC, '')), TRIM(COALESCE(FLOCALES.ESCALLOC, '')), 
                TRIM(COALESCE(FLOCALES.PISOLOC, '')), TRIM(COALESCE(FLOCALES.PUERTLOC, '')),
                TRIM(COALESCE(FLOCALES.CPOSTLOC, '')), TRIM(COALESCE(FLOCALES.POBLALOC, '')),
                TRIM(COALESCE(FLOCARRE.CIFARR, ''))
            FROM 
                FEMPRESA 
            INNER JOIN FLOCALES 
                ON FEMPRESA.EMPRESA = FLOCALES.EMPRESA
            INNER JOIN FLOCARRE 
                ON FLOCALES.INTERNO = FLOCARRE.PADREARR 
            WHERE 
                TRIM(FEMPRESA.CIFCLI) = ? 
                AND FLOCARRE.FINCOARR IS NULL
        """
        current_app.logger.info(f"Ejecutando consulta FISCAL para NIF: {propietario_nif_app}")
        cur_fiscal.execute(sql_to_execute_fiscal, (propietario_nif_app.strip(),))
        
        for row_fiscal in cur_fiscal.fetchall():
            numero_local_f = row_fiscal[0] 
            ref_cat_f = row_fiscal[1]
            nif_inquilino_f = row_fiscal[10].strip() if row_fiscal[10] else None

            ya_existe_prop_en_app = False
            if ref_cat_f and ref_cat_f.strip():
                if PropiedadApp.query.filter_by(referencia_catastral=ref_cat_f.strip(), propietario_id=propietario_en_app.id).first():
                    ya_existe_prop_en_app = True
            
            sigla_loc = row_fiscal[2].title(); calle_loc = row_fiscal[3].title()
            num_loc = row_fiscal[4].upper(); esc_loc = row_fiscal[5].upper()
            piso_loc = row_fiscal[6].upper(); puerta_loc = row_fiscal[7].upper()
            part1_dir = " ".join(filter(None, [sigla_loc, calle_loc]))
            part2_dir = " ".join(filter(None, [num_loc, esc_loc, piso_loc, puerta_loc]))
            dir_completa = f"{part1_dir}, {part2_dir}" if part1_dir and part2_dir else part1_dir or part2_dir

            todas_las_propiedades_fiscal.append({ # Se añade a esta lista
                "numero_local_fiscal": numero_local_f.strip() if numero_local_f else None,
                "referencia_catastral_fiscal": ref_cat_f.strip() if ref_cat_f else None,
                "direccion_fiscal": dir_completa.strip(),
                "codigo_postal_fiscal": row_fiscal[8].strip() if row_fiscal[8] else None,
                "ciudad_fiscal": row_fiscal[9].title() if row_fiscal[9] else None,
                "nif_inquilino_asociado_fiscal": nif_inquilino_f,
                "ya_existe_en_app": ya_existe_prop_en_app
            })

            if nif_inquilino_f: todos_los_nifs_inquilinos_fiscal_set.add(nif_inquilino_f)

        if todos_los_nifs_inquilinos_fiscal_set:
            if not conn_general or getattr(conn_general, 'closed', True):
                conn_general = get_firebird_connection("GENERAL")
            if conn_general:
                if not cur_general or getattr(cur_general, 'closed', True):
                    cur_general = conn_general.cursor()
                
                for nif_inq in todos_los_nifs_inquilinos_fiscal_set:
                    inquilino_app_existente = InquilinoApp.query.filter_by(nif=nif_inq).first()
                    if inquilino_app_existente:
                        inquilinos_data_para_importar[nif_inq] = {"nif": nif_inq, "nombre": inquilino_app_existente.nombre, "exists_in_app": True, "id_app": inquilino_app_existente.id}
                        continue
                    sql_datosfis = """SELECT TRIM(NIF), TRIM(COALESCE(PFNOMBRE_1, '')), TRIM(COALESCE(PFAPELL1_1, '')), TRIM(COALESCE(PFAPELL2_1, '')), TRIM(COALESCE(PJURIDIC_1, '')), TRIM(COALESCE(SIGLA, '')), TRIM(COALESCE(CALLE, '')), TRIM(COALESCE(NUMERO, '')), TRIM(COALESCE(ESCALERA, '')), TRIM(COALESCE(PISO, '')), TRIM(COALESCE(PUERTA, '')), TRIM(CPOSTAL), TRIM(POBLACIO), TRIM(TELEFON1) FROM DATOSFIS WHERE TRIM(NIF) = ?"""
                    cur_general.execute(sql_datosfis, (nif_inq,))
                    row_inq_gen = cur_general.fetchone()
                    if row_inq_gen:
                        nombre_inq_gen = " ".join(filter(None, [row_inq_gen[1], row_inq_gen[2], row_inq_gen[3]])) if row_inq_gen[1] else (row_inq_gen[4] if row_inq_gen[4] else "")
                        sigla_ig = row_inq_gen[5].title(); calle_ig = row_inq_gen[6].title(); num_ig = row_inq_gen[7].upper(); esc_ig = row_inq_gen[8].upper(); piso_ig = row_inq_gen[9].upper(); puerta_ig = row_inq_gen[10].upper()
                        p1_dir_ig = " ".join(filter(None, [sigla_ig, calle_ig])); p2_dir_ig = " ".join(filter(None, [num_ig, esc_ig, piso_ig, puerta_ig]))
                        dir_comp_ig = f"{p1_dir_ig}, {p2_dir_ig}" if p1_dir_ig and p2_dir_ig else p1_dir_ig or p2_dir_ig
                        inquilinos_data_para_importar[nif_inq] = {"nif": row_inq_gen[0], "nombre": nombre_inq_gen.strip(), "direccion": dir_comp_ig.strip(), "codigo_postal": row_inq_gen[11], "ciudad": row_inq_gen[12].title() if row_inq_gen[12] else None, "telefono": row_inq_gen[13], "email": None, "exists_in_app": False}
                    else:
                        inquilinos_data_para_importar[nif_inq] = {"nif": nif_inq, "nombre": f"Inquilino NIF {nif_inq} (No en DATOSFIS)", "exists_in_app": False}
                        errores_busqueda.append(f"Inquilino NIF {nif_inq} no encontrado en BD GENERAL (DATOSFIS).")
                # No cerrar cur_general aquí si se va a reusar en el bucle
            else:
                errores_busqueda.append("No se pudo conectar a BD GENERAL para buscar datos completos de inquilinos.")
                for nif_inq in todos_los_nifs_inquilinos_fiscal_set:
                    if nif_inq not in inquilinos_data_para_importar:
                        inquilinos_data_para_importar[nif_inq] = {"nif": nif_inq, "nombre": f"Inquilino NIF {nif_inq} (Búsqueda GENERAL falló)", "exists_in_app": False}
        
        # Esta es la línea del error en el traceback: if not todas_las_propiedades_fiscal ...
        if not todas_las_propiedades_fiscal and not any(not data.get("exists_in_app", True) for data in inquilinos_data_para_importar.values()):
             return jsonify({"message": "No se encontraron nuevas propiedades o inquilinos para importar.", "propiedades_fiscal": [], "inquilinos_potenciales": [], "errors": errores_busqueda}), 200

        return jsonify({
            "message": "Búsqueda en BD Fiscal completada.",
            "propietario_app_id": propietario_en_app.id,
            "propietario_app_nombre": propietario_en_app.nombre,
            "propiedades_fiscal": todas_las_propiedades_fiscal, # Se usa aquí
            "inquilinos_potenciales": list(inquilinos_data_para_importar.values()),
            "errors": errores_busqueda
        }), 200

    except fdb.fbcore.DatabaseError as fb_error:
        error_msg_fb = fb_error.args[0] if fb_error.args else str(fb_error)
        current_app.logger.error(f"Error de Firebird en importación FISCAL para NIF {propietario_nif_app}: {error_msg_fb}")
        return jsonify({"error": f"Error consultando BD FISCAL: {str(error_msg_fb)[:150]}"}), 500
    except Exception as e:
        current_app.logger.error(f"Error inesperado en importación FISCAL para NIF {propietario_nif_app}: {e}", exc_info=True)
        return jsonify({"error": "Error interno del servidor durante la importación desde BD FISCAL."}), 500
    finally:
        if cur_fiscal: 
            try: cur_fiscal.close()
            except Exception as e_cf_close: current_app.logger.warning(f"Error cerrando cursor FISCAL: {e_cf_close}")
        if conn_fiscal:
            try: conn_fiscal.close()
            except Exception as e_cf_conn_close: current_app.logger.warning(f"Error cerrando conexión FISCAL: {e_cf_conn_close}")
        if cur_general:
            try: cur_general.close()
            except Exception as e_cg_close: current_app.logger.warning(f"Error cerrando cursor GENERAL: {e_cg_close}")
        if conn_general:
            try: conn_general.close()
            except Exception as e_cg_conn_close: current_app.logger.warning(f"Error cerrando conexión GENERAL: {e_cg_conn_close}")

# --- execute_import_owner_assets (SIN CAMBIOS, ya la tienes) ---
@external_db_api_bp.route('/execute_import_owner_assets', methods=['POST'])
@login_required
@role_required('admin', 'gestor')
def execute_import_owner_assets():
    # ... (código existente de esta función)
    data = request.get_json()
    if not data: return jsonify({"error": "No se recibieron datos."}), 400
    propietario_app_id = data.get('propietario_app_id')
    propiedades_data = data.get('propiedades', [])
    inquilinos_data = data.get('inquilinos', [])
    if not propietario_app_id: return jsonify({"error": "Falta ID propietario."}), 400
    propietario_app = db.session.get(PropietarioApp, propietario_app_id)
    if not propietario_app: return jsonify({"error": f"Propietario ID {propietario_app_id} no encontrado."}), 404
    props_creadas_c, inqs_creados_c = 0, 0
    errores_creacion_final = []
    propiedades_realmente_creadas_info = [] 
    inquilinos_realmente_creados_info = []    
    try:
        for inq_d in inquilinos_data:
            if not inq_d.get('nif') or not inq_d.get('nombre'):
                errores_creacion_final.append(f"Inquilino NIF: {inq_d.get('nif', 'N/A')} sin datos completos. Omitido."); continue
            if InquilinoApp.query.filter_by(nif=inq_d['nif']).first():
                errores_creacion_final.append(f"Inquilino NIF {inq_d['nif']} ya existe (verificación final). Omitido."); continue
            try:
                nuevo_inq = InquilinoApp(
                    nif=inq_d['nif'], nombre=inq_d['nombre'], direccion=inq_d.get('direccion'),
                    codigo_postal=inq_d.get('codigo_postal'), ciudad=inq_d.get('ciudad'),
                    telefono=inq_d.get('telefono'), email=inq_d.get('email'), estado='activo'
                )
                db.session.add(nuevo_inq); inqs_creados_c += 1
                inquilinos_realmente_creados_info.append(nuevo_inq.nif)
            except Exception as ei: errores_creacion_final.append(f"Error creando inquilino {inq_d['nif']}: {str(ei)[:50]}")
        for prop_d in propiedades_data:
            if not prop_d.get('direccion_fiscal'):
                errores_creacion_final.append("Propiedad sin dirección. Omitida."); continue
            ref_cat_para_check = prop_d.get('referencia_catastral_fiscal')
            if ref_cat_para_check and ref_cat_para_check.strip():
                if PropiedadApp.query.filter_by(referencia_catastral=ref_cat_para_check.strip(), propietario_id=propietario_app_id).first():
                    errores_creacion_final.append(f"Propiedad RefCat {ref_cat_para_check} ya existe. Omitida."); continue
            try:
                nueva_prop = PropiedadApp(
                    direccion=prop_d['direccion_fiscal'], ciudad=prop_d.get('ciudad_fiscal'), codigo_postal=prop_d.get('codigo_postal_fiscal'),
                    referencia_catastral=ref_cat_para_check.strip() if ref_cat_para_check else None, 
                    numero_local=prop_d.get('numero_local_fiscal'),
                    tipo=prop_d.get('tipo', 'Comercial'), propietario_id=propietario_app_id, estado_ocupacion='vacia'
                )
                db.session.add(nueva_prop); props_creadas_c += 1
                propiedades_realmente_creadas_info.append(nueva_prop.direccion)
            except Exception as ep: errores_creacion_final.append(f"Error creando propiedad {prop_d['direccion_fiscal']}: {str(ep)[:50]}")
        if props_creadas_c > 0 or inqs_creados_c > 0:
            db.session.commit()
            msg = f"Importación OK: {props_creadas_c} propiedades ({', '.join(propiedades_realmente_creadas_info)}) y {inqs_creados_c} inquilinos ({', '.join(inquilinos_realmente_creados_info)}) creados."
            if errores_creacion_final: msg += " Advertencias: " + " | ".join(errores_creacion_final)
            return jsonify({"message": msg}), 200
        else:
            msg = "No se importaron nuevos datos."
            if errores_creacion_final: msg += " Errores/Advertencias: " + " | ".join(errores_creacion_final)
            return jsonify({"message": msg, "errors": errores_creacion_final}), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error crítico ejec. importación: {e}", exc_info=True)
        return jsonify({"error": "Error interno al procesar importación."}), 500