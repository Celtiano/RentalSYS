# myapp/routes/ipc.py (o indices.py)
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app, jsonify
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy import distinct, func, extract # func y extract pueden no ser necesarios aquí si los cálculos se hacen en python
from datetime import datetime, date
import requests
import json
import calendar
from decimal import Decimal, InvalidOperation

# --- IMPORTS DE TU PROYECTO ---
from ..models import db, IPCData, IRAVData # Importar ambos modelos de datos
from ..decorators import role_required # Para restringir acceso
from ..forms import CSRFOnlyForm # Para los tokens CSRF

# --- IMPORTS DE AUTENTICACIÓN ---
from flask_login import login_required, current_user

# --- Blueprint ---
# Considera renombrar ipc_bp a algo más genérico como indices_bp
ipc_bp = Blueprint('ipc_bp', __name__) # O indices_bp si renombras

# --- Protección del Blueprint ---
@ipc_bp.before_request
@login_required
def before_request():
    """Protege todas las rutas de este blueprint."""
    # Aquí podrías añadir un chequeo de rol si solo admin/gestor pueden ver/gestionar índices
    # if current_user.role not in ['admin', 'gestor']:
    #     flash("No tienes permiso para acceder a la gestión de índices.", "warning")
    #     return redirect(url_for('main_bp.dashboard'))
    pass

# --- Constantes ---
MESES_STR = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
INE_API_BASE_URL = "https://servicios.ine.es/wstempus/js/ES/DATOS_SERIE"
INE_SERIE_CODE_IPC = "IPC251856"  # Índice de Precios de Consumo, Base 2021, General Nacional, Variación anual
INE_SERIE_CODE_IRAV = "IRAV1"    # Índice de Referencia de Alquiler de Vivienda, Índice General, Variación anual
                                # ¡Verifica que IRAV1 es la variación anual! Si es el índice base, el cálculo es diferente.

# --- Funciones Helper ---
def get_last_day_of_month(year, month):
    return calendar.monthrange(year, month)[1]

def parse_ine_response(data, index_name_for_log, expected_year=None, expected_month=None):
    """Parsea la respuesta JSON de la API del INE. Adaptada para ser más genérica."""
    try:
        serie_data = None
        if isinstance(data, list) and data and isinstance(data[0], dict):
            serie_data = data[0]
        elif isinstance(data, dict):
            serie_data = data
        else:
            current_app.logger.warning(f"Formato inesperado de respuesta INE para {index_name_for_log}: {type(data)}. Contenido: {str(data)[:200]}")
            return None

        if "Data" not in serie_data or not isinstance(serie_data["Data"], list):
            current_app.logger.warning(f"Formato inesperado: No se encontró 'Data' como lista en la serie para {index_name_for_log}. Serie: {str(serie_data)[:200]}")
            return None

        data_points = serie_data["Data"]
        if not data_points:
            current_app.logger.info(f"La lista 'Data' del INE para {index_name_for_log} está vacía.")
            return None

        target_point = None
        if expected_year is not None and expected_month is not None:
            for point in data_points:
                if isinstance(point, dict) and point.get("Anyo") == expected_year:
                    month = None
                    if point.get("FK_Periodo") is not None: month = int(point["FK_Periodo"])
                    elif point.get("Mes") is not None: month = int(point["Mes"])
                    elif isinstance(point.get("T3_Periodo"), str) and point["T3_Periodo"].startswith("M"):
                        try: month = int(point["T3_Periodo"][1:])
                        except ValueError: pass
                    elif isinstance(point.get("Fecha"), str):
                        try: month = int(point["Fecha"].split("T")[0].split("-")[1])
                        except (IndexError, ValueError): pass
                    if month == expected_month:
                        target_point = point; break
            if not target_point:
                 current_app.logger.warning(f"No se encontró punto INE para {index_name_for_log} en {expected_year}-{expected_month:02d}.")
                 return None
        else: # nult=1
            target_point = data_points[-1]

        if target_point and isinstance(target_point, dict) and "Anyo" in target_point and "Valor" in target_point:
            year = int(target_point["Anyo"])
            try: percentage = float(target_point["Valor"])
            except (ValueError, TypeError):
                 current_app.logger.error(f"Valor {index_name_for_log} inválido en punto INE: {target_point.get('Valor')}"); return None
            month = None
            if target_point.get("FK_Periodo") is not None: month = int(target_point["FK_Periodo"])
            elif target_point.get("Mes") is not None: month = int(target_point["Mes"])
            elif isinstance(target_point.get("T3_Periodo"), str) and target_point["T3_Periodo"].startswith("M"):
                try: month = int(target_point["T3_Periodo"][1:])
                except ValueError: pass
            elif isinstance(target_point.get("Fecha"), str):
                 try: month = int(target_point["Fecha"].split("T")[0].split("-")[1])
                 except (IndexError, ValueError): pass

            if month is None or not (1 <= month <= 12) or not (1950 < year < 2100):
                current_app.logger.error(f"Datos fecha inválidos para {index_name_for_log}: Año={year}, Mes={month}")
                if expected_year and expected_month: year, month = expected_year, expected_month
                else: return None
            return year, month, percentage
        else:
             current_app.logger.warning(f"Punto INE para {index_name_for_log} con formato incorrecto: {target_point}"); return None
    except Exception as e:
        current_app.logger.error(f"Error parseando respuesta INE para {index_name_for_log}: {e}. Data: {str(data)[:500]}", exc_info=True)
        return None

def _fetch_and_update_index_data(serie_code, db_model_class, index_name):
    """Función genérica para obtener y guardar datos de un índice del INE."""
    api_url = f"{INE_API_BASE_URL}/{serie_code}?nult=1"
    error_message, status_code = None, 500
    try:
        current_app.logger.info(f"Solicitando ÚLTIMO {index_name} desde: {api_url}")
        response = requests.get(api_url, timeout=20)
        status_code = response.status_code
        response.raise_for_status()
        data = response.json()
        parsed_data = parse_ine_response(data, index_name)
        if parsed_data:
            year, month, percentage = parsed_data
            exists = db.session.query(db_model_class.id).filter_by(year=year, month=month).first()
            if not exists:
                new_entry = db_model_class(year=year, month=month, percentage_change=percentage)
                db.session.add(new_entry); db.session.commit()
                msg = f"¡Éxito! Se añadió el dato {index_name} para {MESES_STR[month]}/{year} ({percentage:.2f}%)."
                current_app.logger.info(msg)
                return msg, 200
            else:
                msg = f"El último dato {index_name} del INE ({year}-{month:02d}) ya estaba registrado."
                current_app.logger.info(msg)
                return msg, 200
        else:
            error_message = f"No se pudieron extraer datos válidos de la respuesta INE para {index_name}."
            status_code = 404 # Not found in response
    except requests.exceptions.Timeout: error_message = f"Error: Timeout API INE para {index_name}."; status_code = 504
    except requests.exceptions.RequestException as e: error_message = f"Error conexión API INE ({status_code}) para {index_name}: {e}"; status_code = status_code if status_code >= 400 else 503
    except json.JSONDecodeError: error_message = f"Error: Respuesta inválida INE (no JSON) para {index_name}."; status_code = 502
    except IntegrityError: db.session.rollback(); error_message = f"Error: Dato {index_name} ya existe (concurrente)."; status_code = 409
    except Exception as e: db.session.rollback(); error_message = f"Error inesperado procesando {index_name}: {e}"; current_app.logger.error(f"Error _fetch_and_update_index_data ({index_name}): {e}", exc_info=True); status_code = 500

    if error_message: current_app.logger.error(error_message)
    else: error_message = f"No se encontraron datos válidos para {index_name} en la respuesta INE."; status_code = 404
    return error_message, status_code

def _get_specific_ine_value(serie_code, db_model_class, index_name, year, month):
    """Busca valor específico de un índice en API INE o localmente."""
    if not (1 <= month <= 12) or not (1950 < year < 2100):
        return jsonify({'status': 'error', 'message': 'Fecha inválida.'}), 400

    local_entry = db.session.query(db_model_class).filter_by(year=year, month=month).first()
    if local_entry:
        return jsonify({'status': 'exists', 'value': float(local_entry.percentage_change), 'message': f'Valor {index_name} ya registrado localmente.'})

    try:
        last_day = get_last_day_of_month(year, month)
        start_date_str = f"{year}{month:02d}01"
        end_date_str = f"{year}{month:02d}{last_day:02d}"
        api_url = f"{INE_API_BASE_URL}/{serie_code}?date={start_date_str}:{end_date_str}"
        # Asegúrate de que la serie del INE devuelve la variación anual directamente.
        # Si es un índice base, necesitarías pedir el valor para el mes actual y el mismo mes del año anterior
        # y calcular (ValorActual / ValorAnterior - 1) * 100
        # Ej: IRAV1 es el índice, no la variación. IPC251856 es la variación.
        # Para IRAV, si es el índice base, la lógica de cálculo de porcentaje debe cambiar.
        # Por ahora, asumimos que AMBAS series devuelven la *variación porcentual anual*.

        current_app.logger.info(f"Solicitando {index_name} específico desde INE: {api_url}")
        response = requests.get(api_url, timeout=15)
        status_code = response.status_code
        response.raise_for_status()
        data = response.json()
        parsed_data = parse_ine_response(data, index_name, expected_year=year, expected_month=month)

        if parsed_data:
            _, _, percentage = parsed_data
            return jsonify({'status': 'found', 'value': percentage, 'message': f'Valor {index_name} encontrado para {MESES_STR[month]}/{year}.'})
        else:
            return jsonify({'status': 'not_found', 'message': f'INE no devolvió valor {index_name} para {MESES_STR[month]}/{year}.'}), 404
    except requests.exceptions.Timeout: return jsonify({'status': 'error', 'message': f'Timeout API INE para {index_name}.'}), 504
    except requests.exceptions.RequestException as e: return jsonify({'status': 'error', 'message': f'Error conexión INE ({status_code}) para {index_name}: {e}'}), status_code if status_code >= 400 else 503
    except json.JSONDecodeError: return jsonify({'status': 'error', 'message': f'Respuesta inválida INE (no JSON) para {index_name}.'}), 502
    except Exception as e: current_app.logger.error(f"Error _get_specific_ine_value ({index_name}, {year}-{month}): {e}", exc_info=True); return jsonify({'status': 'error', 'message': f'Error inesperado: {e}'}), 500

def _add_edit_index_data(db_model_class, index_name, form_data, entry_id=None):
    """Añade o edita un dato de índice manualmente."""
    year_str = form_data.get('ipcYear') # Nombres genéricos en el form del modal
    month_str = form_data.get('ipcMonth')
    percent_str = form_data.get('ipcPercentage')

    if not all([year_str, month_str, percent_str]):
        flash('Año, Mes y Porcentaje son obligatorios.', 'warning'); return False
    try:
        year = int(year_str); month = int(month_str)
        percentage_val = Decimal(percent_str.replace(',', '.'))
        if not (1 <= month <= 12): raise ValueError("Mes debe estar entre 1 y 12.")
        if not (1950 < year < 2100): raise ValueError("Año inválido.")

        # Comprobar si ya existe (para otro ID si es edición)
        query = db.session.query(db_model_class.id).filter_by(year=year, month=month)
        if entry_id: query = query.filter(db_model_class.id != entry_id)
        existing = query.first()

        if existing:
            flash(f"Error: Ya existe un registro {index_name} para {MESES_STR[month]}/{year}.", 'danger'); return False

        if entry_id: # Editar
            entry = db.session.get(db_model_class, entry_id)
            if not entry: flash(f"Registro {index_name} no encontrado para editar.", "warning"); return False
            entry.year = year; entry.month = month; entry.percentage_change = percentage_val
            flash(f'Dato {index_name} para {MESES_STR[month]}/{year} actualizado.', 'success')
        else: # Añadir
            new_entry = db_model_class(year=year, month=month, percentage_change=percentage_val)
            db.session.add(new_entry)
            flash(f'Dato {index_name} para {MESES_STR[month]}/{year} añadido.', 'success')
        db.session.commit()
        return True
    except (ValueError, InvalidOperation) as ve: db.session.rollback(); flash(f'Error en datos para {index_name}: {ve}', 'danger')
    except IntegrityError: db.session.rollback(); flash(f'Error de integridad BD para {index_name} (¿duplicado?).', 'danger')
    except Exception as e: db.session.rollback(); flash(f'Error inesperado guardando {index_name}: {e}', 'danger'); current_app.logger.error(f"Error _add_edit_index_data ({index_name}): {e}", exc_info=True)
    return False

# --- Rutas ---
@ipc_bp.route('/', endpoint='listar_indices') # Renombrar endpoint para claridad
def listar_indices(): # Renombrada
    ipc_list, irav_list = [], []
    latest_ipc, latest_irav = None, None
    available_years_ipc, available_years_irav = [], []
    current_year_avg_ipc, current_year_avg_irav = None, None
    now = datetime.now()
    default_year, default_month = now.year, now.month -1
    try:
        ipc_list = IPCData.query.order_by(IPCData.year.desc(), IPCData.month.desc()).all()
        latest_ipc = ipc_list[0] if ipc_list else None
        year_tuples_ipc = db.session.query(IPCData.year).distinct().order_by(IPCData.year.desc()).all()
        available_years_ipc = [y[0] for y in year_tuples_ipc]
        avg_ipc_q = db.session.query(func.avg(IPCData.percentage_change)).filter(IPCData.year == default_year).scalar()
        if avg_ipc_q is not None: current_year_avg_ipc = round(float(avg_ipc_q), 2)

        irav_list = IRAVData.query.order_by(IRAVData.year.desc(), IRAVData.month.desc()).all()
        latest_irav = irav_list[0] if irav_list else None
        year_tuples_irav = db.session.query(IRAVData.year).distinct().order_by(IRAVData.year.desc()).all()
        available_years_irav = [y[0] for y in year_tuples_irav]
        avg_irav_q = db.session.query(func.avg(IRAVData.percentage_change)).filter(IRAVData.year == default_year).scalar()
        if avg_irav_q is not None: current_year_avg_irav = round(float(avg_irav_q), 2)

    except Exception as e:
        flash(f'Error cargando datos de índices: {e}', 'danger')
        current_app.logger.error(f"Error en listar_indices: {e}", exc_info=True)

    csrf_form = CSRFOnlyForm()
    return render_template(
        'ipc.html', # O 'indices.html' si renombras el template
        title='Gestión de Índices (IPC/IRAV)',
        ipc_data=ipc_list, irav_data=irav_list,
        latest_ipc=latest_ipc, latest_irav=latest_irav,
        available_years_ipc=available_years_ipc, available_years_irav=available_years_irav,
        current_year_avg_ipc=current_year_avg_ipc, current_year_avg_irav=current_year_avg_irav,
        default_year=default_year, default_month=default_month,
        meses=MESES_STR, csrf_form=csrf_form
    )

# --- Rutas para Actualizar desde INE ---
@ipc_bp.route('/update_ipc', methods=['POST'])
@role_required('admin', 'gestor')
def update_ipc_from_ine_route():
    result_message, status_code = _fetch_and_update_index_data(INE_SERIE_CODE_IPC, IPCData, "IPC")
    flash(result_message, 'success' if status_code == 200 and "¡Éxito!" in result_message else ('info' if status_code == 200 else 'danger'))
    return redirect(url_for('ipc_bp.listar_indices'))

@ipc_bp.route('/update_irav', methods=['POST'])
@role_required('admin', 'gestor')
def update_irav_from_ine_route():
    result_message, status_code = _fetch_and_update_index_data(INE_SERIE_CODE_IRAV, IRAVData, "IRAV")
    flash(result_message, 'success' if status_code == 200 and "¡Éxito!" in result_message else ('info' if status_code == 200 else 'danger'))
    return redirect(url_for('ipc_bp.listar_indices'))

# --- Rutas para Obtener Valor Específico (AJAX) ---
@ipc_bp.route('/get_ine_ipc_value/<int:year>/<int:month>')
def get_ine_ipc_value_route(year, month):
    return _get_specific_ine_value(INE_SERIE_CODE_IPC, IPCData, "IPC", year, month)

@ipc_bp.route('/get_ine_irav_value/<int:year>/<int:month>')
def get_ine_irav_value_route(year, month):
    return _get_specific_ine_value(INE_SERIE_CODE_IRAV, IRAVData, "IRAV", year, month)

# --- Rutas para CRUD Manual ---
@ipc_bp.route('/add_ipc_manual', methods=['POST']) # Renombrar para evitar colisión con quickAddForm
@role_required('admin', 'gestor')
def add_ipc_manual_route():
    _add_edit_index_data(IPCData, "IPC", request.form)
    return redirect(url_for('ipc_bp.listar_indices'))

@ipc_bp.route('/add_irav_manual', methods=['POST'])
@role_required('admin', 'gestor')
def add_irav_manual_route():
    _add_edit_index_data(IRAVData, "IRAV", request.form)
    return redirect(url_for('ipc_bp.listar_indices'))

@ipc_bp.route('/edit_ipc/<int:id>', methods=['POST'])
@role_required('admin', 'gestor')
def edit_ipc_route(id):
    _add_edit_index_data(IPCData, "IPC", request.form, entry_id=id)
    return redirect(url_for('ipc_bp.listar_indices'))

@ipc_bp.route('/edit_irav/<int:id>', methods=['POST'])
@role_required('admin', 'gestor')
def edit_irav_route(id):
    _add_edit_index_data(IRAVData, "IRAV", request.form, entry_id=id)
    return redirect(url_for('ipc_bp.listar_indices'))

@ipc_bp.route('/delete_ipc/<int:id>', methods=['POST'])
@role_required('admin', 'gestor')
def delete_ipc_route(id):
    entry = db.session.get(IPCData, id)
    if entry: db.session.delete(entry); db.session.commit(); flash(f'Dato IPC eliminado.', 'success')
    else: flash('Registro IPC no encontrado.', 'warning')
    return redirect(url_for('ipc_bp.listar_indices'))

@ipc_bp.route('/delete_irav/<int:id>', methods=['POST'])
@role_required('admin', 'gestor')
def delete_irav_route(id):
    entry = db.session.get(IRAVData, id)
    if entry: db.session.delete(entry); db.session.commit(); flash(f'Dato IRAV eliminado.', 'success')
    else: flash('Registro IRAV no encontrado.', 'warning')
    return redirect(url_for('ipc_bp.listar_indices'))