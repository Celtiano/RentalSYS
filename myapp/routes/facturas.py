# myapp/routes/facturas.py
import os
import json
import calendar
from datetime import datetime, date, timedelta
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
import uuid
import io
import traceback
import re

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, abort, current_app, jsonify, g, send_from_directory,
    send_file
)
from werkzeug.exceptions import NotFound # Importar para manejo de errores
from sqlalchemy import func, or_, extract
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import joinedload, selectinload
from werkzeug.utils import secure_filename
from flask_mail import Message

from ..models import (
    db, Factura, Inquilino, Propiedad, Contrato, Gasto, Propietario,
    IPCData, IRAVData, DEFAULT_IVA_RATE, DEFAULT_IRPF_RATE, SystemSettings, Notification, User, HistorialActualizacionRenta
)
from .ipc import MESES_STR # Asumiendo que ipc.py existe y tiene MESES_STR
from ..utils.file_helpers import get_owner_document_path
from ..utils.pdf_generator import generate_invoice_pdf # O la v2 que prefieras
from ..forms import CSRFOnlyForm
from .. import mail
from flask_login import login_required, current_user
from ..decorators import (
    role_required, owner_access_required,
    filtered_list_view, filtered_detail_view, with_owner_filtering, validate_entity_access
)
from ..utils.database_helpers import (
    get_filtered_facturas, get_filtered_contratos, get_filtered_propiedades, 
    get_filtered_inquilinos, OwnerFilteredQueries
)
from ..utils.owner_session import get_active_owner_context



facturas_bp = Blueprint('facturas_bp', __name__)

@facturas_bp.before_request
@login_required
def before_request():
    """Protege todas las rutas del blueprint."""
    pass

# --- Constantes y Helpers ---
TWO_PLACES = Decimal('0.01')
ALLOWED_EXTENSIONS_EXPENSES = {'pdf', 'png', 'jpg', 'jpeg', 'gif'}
MESES_STR = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]

def allowed_expense_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS_EXPENSES

def get_rates():
    """Obtiene las tasas de IVA e IRPF desde g.settings o los defaults del modelo."""
    settings = getattr(g, 'settings', None)
    # Usar los defaults del modelo si settings o sus atributos no existen o son None
    iva_rate_from_settings = getattr(settings, 'iva_rate', None) if settings else None
    irpf_rate_from_settings = getattr(settings, 'irpf_rate', None) if settings else None

    try:
        iva = Decimal(str(iva_rate_from_settings)) if iva_rate_from_settings is not None else DEFAULT_IVA_RATE
    except InvalidOperation:
        iva = DEFAULT_IVA_RATE
    try:
        irpf = Decimal(str(irpf_rate_from_settings)) if irpf_rate_from_settings is not None else DEFAULT_IRPF_RATE
    except InvalidOperation:
        irpf = DEFAULT_IRPF_RATE
    return iva, irpf
    
def last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]    

def _calculate_updated_rent_for_invoice(contract: Contrato, target_year: int, target_month: int, current_base_rent: Decimal):
    log_info = []
    conceptos_adicionales = []
    info_indice_aplicado_para_factura = None 
    
    renta_para_linea_principal_alquiler = current_base_rent
    nueva_renta_contrato_a_persistir_calculada = current_base_rent 
    indice_pendiente_valor_raw = None 

    # --- 1. MANEJO DE ÍNDICES PENDIENTES (SI `aplicar_indice_retroactivo` ES TRUE) ---
    if contract.aplicar_indice_retroactivo and \
       contract.indice_pendiente_mes and contract.indice_pendiente_ano and contract.indice_pendiente_tipo and \
       contract.renta_base_pre_actualizacion_pendiente is not None:

        log_info.append(f"Intentando resolver índice pendiente: {contract.indice_pendiente_tipo} de {contract.indice_pendiente_mes}/{contract.indice_pendiente_ano} sobre base {contract.renta_base_pre_actualizacion_pendiente}.")
        db_model_to_query = IPCData if contract.indice_pendiente_tipo == 'IPC' else IRAVData if contract.indice_pendiente_tipo == 'IRAV' else None
        
        if db_model_to_query:
            indice_pendiente_valor_raw = db.session.query(db_model_to_query.percentage_change)\
                                        .filter_by(year=contract.indice_pendiente_ano, month=contract.indice_pendiente_mes)\
                                        .scalar()
            if indice_pendiente_valor_raw is not None:
                log_info.append(f"RESOLVIENDO PENDIENTE: Índice {contract.indice_pendiente_tipo} ({contract.indice_pendiente_mes}/{contract.indice_pendiente_ano}) DISPONIBLE: {indice_pendiente_valor_raw}%.")
                renta_base_del_periodo_pendiente = contract.renta_base_pre_actualizacion_pendiente
                pct_pendiente = (Decimal(str(indice_pendiente_valor_raw)) / Decimal('100')).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)
                incremento_del_indice_pendiente_mensual = (renta_base_del_periodo_pendiente * pct_pendiente).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
                incremento_del_fijo_pendiente_una_vez = Decimal('0.00')
                
                info_indice_aplicado_para_factura = {
                    'type': contract.indice_pendiente_tipo,
                    'month': contract.indice_pendiente_mes,
                    'year': contract.indice_pendiente_ano, # Año del índice
                    'percentage': float(indice_pendiente_valor_raw)
                }

                if incremento_del_indice_pendiente_mensual != Decimal('0.00'):
                    conceptos_adicionales.append({
                        "description": f"Aplic. Act. Pendiente ({contract.indice_pendiente_mes}/{contract.indice_pendiente_ano}): {contract.indice_pendiente_tipo} s/ {renta_base_del_periodo_pendiente:.2f}",
                        "quantity": 1, "unitPrice": float(incremento_del_indice_pendiente_mensual), "total": float(incremento_del_indice_pendiente_mensual)
                    })
                    log_info.append(f"Añadido concepto por aplicación de índice pendiente ({contract.indice_pendiente_tipo}) este mes: {incremento_del_indice_pendiente_mensual}")

                if contract.tipo_actualizacion_renta == 'indice_mas_fijo' and contract.importe_actualizacion_fija is not None:
                    incremento_del_fijo_pendiente_una_vez = contract.importe_actualizacion_fija.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
                    if incremento_del_fijo_pendiente_una_vez != Decimal('0.00'):
                        conceptos_adicionales.append({
                            "description": f"Aplic. Act. Fija Pendiente ({contract.importe_actualizacion_fija:.2f})",
                            "quantity": 1, "unitPrice": float(incremento_del_fijo_pendiente_una_vez), "total": float(incremento_del_fijo_pendiente_una_vez)
                        })
                        log_info.append(f"Añadido concepto por aplicación de fijo pendiente (una vez): {incremento_del_fijo_pendiente_una_vez}")
                
                nueva_renta_contrato_a_persistir_calculada = (renta_base_del_periodo_pendiente + incremento_del_indice_pendiente_mensual + incremento_del_fijo_pendiente_una_vez).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
                renta_para_linea_principal_alquiler = renta_base_del_periodo_pendiente
                log_info.append(f"Nueva base contrato a persistir: {nueva_renta_contrato_a_persistir_calculada}. Renta para línea alquiler este mes: {renta_para_linea_principal_alquiler}")

                if contract.indice_pendiente_ano_original_aplicacion and contract.indice_pendiente_mes_original_aplicacion:
                    fecha_inicio_efectiva_actualizacion = date(contract.indice_pendiente_ano_original_aplicacion, contract.indice_pendiente_mes_original_aplicacion, 1)
                    fecha_factura_actual_obj = date(target_year, target_month, 1)
                    meses_atraso = 0
                    temp_date_atraso = fecha_inicio_efectiva_actualizacion
                    while temp_date_atraso < fecha_factura_actual_obj:
                        meses_atraso += 1
                        m_temp, y_temp = (temp_date_atraso.month + 1, temp_date_atraso.year) if temp_date_atraso.month < 12 else (1, temp_date_atraso.year + 1)
                        temp_date_atraso = date(y_temp, m_temp, 1)

                    if meses_atraso > 0 and incremento_del_indice_pendiente_mensual != Decimal('0.00'):
                        total_atrasos_indice = (incremento_del_indice_pendiente_mensual * meses_atraso).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
                        conceptos_adicionales.append({
                            "description": f"Atrasos act. {contract.indice_pendiente_tipo} ({incremento_del_indice_pendiente_mensual:.2f}/mes) - {meses_atraso} mes(es)",
                            "quantity": 1, "unitPrice": float(total_atrasos_indice), "total": float(total_atrasos_indice)
                        })
                        log_info.append(f"ATRASOS ÍNDICE ({contract.indice_pendiente_mes}/{contract.indice_pendiente_ano}): {meses_atraso} meses. Total: {total_atrasos_indice}.")
                else:
                    log_info.append("ADVERTENCIA: No se pudieron calcular atrasos (faltan fechas originales de aplic. del índice pendiente).")

                contract.indice_pendiente_mes = None; contract.indice_pendiente_ano = None; contract.indice_pendiente_tipo = None
                contract.renta_base_pre_actualizacion_pendiente = None
                contract.indice_pendiente_mes_original_aplicacion = None; contract.indice_pendiente_ano_original_aplicacion = None
            else: # El índice pendiente SIGUE sin estar disponible
                renta_para_linea_principal_alquiler = contract.renta_base_pre_actualizacion_pendiente 
                nueva_renta_contrato_a_persistir_calculada = contract.renta_base_pre_actualizacion_pendiente
                log_info.append(f"Índice {contract.indice_pendiente_tipo} ({contract.indice_pendiente_mes}/{contract.indice_pendiente_ano}) PENDIENTE y AÚN NO disponible. Facturando con renta guardada: {renta_para_linea_principal_alquiler}.")
        else: # No se pudo determinar db_model_to_query
            log_info.append(f"ADVERTENCIA: Tipo de índice pendiente '{contract.indice_pendiente_tipo}' no reconocido. No se puede resolver pendiente.")
            if contract.renta_base_pre_actualizacion_pendiente is not None:
                 renta_para_linea_principal_alquiler = contract.renta_base_pre_actualizacion_pendiente
                 nueva_renta_contrato_a_persistir_calculada = contract.renta_base_pre_actualizacion_pendiente
    
    # --- 2. LÓGICA DE ACTUALIZACIÓN PERIÓDICA (ANUAL, etc., para ESTE mes) ---
    renta_base_para_actualizacion_periodica = nueva_renta_contrato_a_persistir_calculada
    
    es_mes_aniversario_contrato = (target_month == contract.fecha_inicio.month)
    
    # Condición final para aplicar actualización periódica de índice:
    # 1. Es el mes de aniversario del contrato.
    # 2. Ha pasado al menos un año desde el inicio del contrato (target_year > inicio.year O (mismo año Y target_month > inicio.month))
    # 3. El año de la factura actual (target_year) es igual o mayor que el año de inicio de IPC configurado en el contrato.
    debe_intentar_actualizacion_periodica_indice = \
        es_mes_aniversario_contrato and \
        (target_year > contract.fecha_inicio.year or \
         (target_year == contract.fecha_inicio.year and target_month > contract.fecha_inicio.month)) and \
        (contract.ipc_ano_inicio is not None and target_year >= contract.ipc_ano_inicio)

    log_info.append(f"Chequeo Act. Periódica: MesAniversario={es_mes_aniversario_contrato}, "
                    f"TargetYear({target_year}) >= IpcAnoInicio({contract.ipc_ano_inicio if contract.ipc_ano_inicio else 'N/A'}), "
                    f"Cond. Completa={debe_intentar_actualizacion_periodica_indice}")

    incremento_indice_periodico_este_mes = Decimal('0.00')
    incremento_fijo_periodico_este_mes = Decimal('0.00')
    descripcion_indice_periodico_este_mes = ""

    # Solo procesar actualización periódica si NO hay un índice pendiente activo
    if not (contract.indice_pendiente_mes and contract.indice_pendiente_ano): 
        if debe_intentar_actualizacion_periodica_indice:
            # A. Actualización Periódica por ÍNDICE
            if contract.tipo_actualizacion_renta in ['indice', 'indice_mas_fijo'] and \
               (contract.actualiza_ipc or contract.actualiza_irav) and \
               contract.ipc_mes_inicio: # Ya no necesitamos contract.ipc_ano_inicio aquí porque se usó arriba
                
                index_model_periodico = IPCData if contract.actualiza_ipc else IRAVData
                index_name_periodico = "IPC" if contract.actualiza_ipc else "IRAV"
                mes_indice_referencia_contrato = contract.ipc_mes_inicio 
                
                # Corrección Lógica del Año del Índice a Consultar
                ano_indice_a_consultar = target_year
                if mes_indice_referencia_contrato > target_month:
                    ano_indice_a_consultar = target_year - 1
                elif mes_indice_referencia_contrato == target_month: # Si el índice es del mismo mes que la factura, se usa el del año anterior
                    ano_indice_a_consultar = target_year - 1
                
                log_info.append(f"Intento Act. Periódica: target_year={target_year}, target_month={target_month}, mes_indice_ref_contrato={mes_indice_referencia_contrato} => ano_indice_a_consultar={ano_indice_a_consultar}")

                if not (1 <= mes_indice_referencia_contrato <= 12):
                    log_info.append(f"Error: Mes de referencia del índice ({mes_indice_referencia_contrato}) en contrato no es válido.");
                else:
                    indice_periodico_valor_raw = db.session.query(index_model_periodico.percentage_change)\
                                                 .filter_by(year=ano_indice_a_consultar, month=mes_indice_referencia_contrato)\
                                                 .scalar()

                    if indice_periodico_valor_raw is not None:
                        pct_periodico = (Decimal(str(indice_periodico_valor_raw)) / Decimal('100')).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)
                        incremento_indice_periodico_este_mes = (renta_base_para_actualizacion_periodica * pct_periodico).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
                        descripcion_indice_periodico_este_mes = f"Actualización {index_name_periodico} ({mes_indice_referencia_contrato}/{ano_indice_a_consultar})"
                        log_info.append(f"ACT. PERIÓDICA ÍNDICE ({MESES_STR[target_month]}/{target_year}): {descripcion_indice_periodico_este_mes}. Incremento: {incremento_indice_periodico_este_mes} sobre base {renta_base_para_actualizacion_periodica}.")
                        
                        if info_indice_aplicado_para_factura is None: # Solo si no se resolvió uno pendiente antes
                            info_indice_aplicado_para_factura = {
                                'type': index_name_periodico,
                                'month': mes_indice_referencia_contrato,
                                'year': ano_indice_a_consultar, 
                                'percentage': float(indice_periodico_valor_raw)
                            }
                    else: 
                        if contract.aplicar_indice_retroactivo:
                            if not (contract.indice_pendiente_tipo and contract.indice_pendiente_mes and contract.indice_pendiente_ano):
                                contract.renta_base_pre_actualizacion_pendiente = renta_base_para_actualizacion_periodica
                                contract.indice_pendiente_mes = mes_indice_referencia_contrato 
                                contract.indice_pendiente_ano = ano_indice_a_consultar       
                                contract.indice_pendiente_tipo = index_name_periodico
                                contract.indice_pendiente_mes_original_aplicacion = target_month 
                                contract.indice_pendiente_ano_original_aplicacion = target_year  
                                log_info.append(f"ACT. PERIÓDICA ÍNDICE: {index_name_periodico} ({mes_indice_referencia_contrato}/{ano_indice_a_consultar}) NO disponible. MARCADO COMO PENDIENTE.")
                            else:
                                log_info.append(f"ACT. PERIÓDICA ÍNDICE: {index_name_periodico} ({mes_indice_referencia_contrato}/{ano_indice_a_consultar}) NO disponible, pero YA EXISTE OTRO ÍNDICE PENDIENTE.")
                        
                        elif getattr(g, 'settings', SystemSettings()).generate_invoice_if_index_missing:
                            log_info.append(f"ADVERTENCIA (ACT. PERIÓDICA): {index_name_periodico} ({mes_indice_referencia_contrato}/{ano_indice_a_consultar}) no encontrado. Factura sin este incremento (config lo permite).")
                        else: 
                            raise ValueError(f"Índice periódico {index_name_periodico} ({mes_indice_referencia_contrato}/{ano_indice_a_consultar}) no encontrado y config no permite generar factura ni aplicar retroactivamente.")
            
            # B. Actualización Periódica por IMPORTE FIJO
            es_aniversario_real_posterior_al_inicio_para_fijo = (target_month == contract.fecha_inicio.month) and \
                                                               (target_year > contract.fecha_inicio.year or \
                                                               (target_year == contract.fecha_inicio.year and target_month > contract.fecha_inicio.month))
            
            es_mes_aplicacion_fija_valida = (es_aniversario_real_posterior_al_inicio_para_fijo or \
                                     (contract.mes_aplicacion_fija is not None and \
                                      target_month == contract.mes_aplicacion_fija and \
                                      (target_year > contract.fecha_inicio.year or \
                                       (target_year == contract.fecha_inicio.year and target_month >= contract.fecha_inicio.month))
                                     )
                                    )

            if contract.tipo_actualizacion_renta in ['fijo', 'indice_mas_fijo'] and \
               contract.importe_actualizacion_fija is not None and es_mes_aplicacion_fija_valida:
                
                aplicar_fijo_periodico_ahora = not (contract.tipo_actualizacion_renta == 'indice_mas_fijo' and contract.aplicar_indice_retroactivo and indice_pendiente_valor_raw is not None and 'incremento_del_fijo_pendiente_una_vez' in locals() and locals().get('incremento_del_fijo_pendiente_una_vez', Decimal('0.00')) != Decimal('0.00'))
                
                if aplicar_fijo_periodico_ahora:
                    incremento_fijo_periodico_este_mes = contract.importe_actualizacion_fija.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
                    log_info.append(f"ACT. PERIÓDICA FIJA ({MESES_STR[target_month]}/{target_year}): Aplicando incremento: {incremento_fijo_periodico_este_mes} sobre base {renta_base_para_actualizacion_periodica}.")

    # --- Añadir conceptos de actualización periódica de ESTE MES (si los hubo y no hay pendiente activo) ---
    if not (contract.indice_pendiente_mes and contract.indice_pendiente_ano and \
            contract.renta_base_pre_actualizacion_pendiente == renta_base_para_actualizacion_periodica and \
            contract.indice_pendiente_mes_original_aplicacion == target_month and \
            contract.indice_pendiente_ano_original_aplicacion == target_year): 

        if incremento_indice_periodico_este_mes != Decimal('0.00'):
            conceptos_adicionales.append({
                "description": descripcion_indice_periodico_este_mes,
                "quantity": 1, "unitPrice": float(incremento_indice_periodico_este_mes), "total": float(incremento_indice_periodico_este_mes)
            })
            nueva_renta_contrato_a_persistir_calculada = (nueva_renta_contrato_a_persistir_calculada + incremento_indice_periodico_este_mes).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        if incremento_fijo_periodico_este_mes != Decimal('0.00'):
            conceptos_adicionales.append({
                "description": f"Actualización Renta Importe Fijo ({MESES_STR[target_month]}/{target_year})",
                "quantity": 1, "unitPrice": float(incremento_fijo_periodico_este_mes), "total": float(incremento_fijo_periodico_este_mes)
            })
            nueva_renta_contrato_a_persistir_calculada = (nueva_renta_contrato_a_persistir_calculada + incremento_fijo_periodico_este_mes).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    # Determinar si la renta del contrato realmente cambió para persistir
    nueva_renta_contrato_a_persistir_final = None
    if nueva_renta_contrato_a_persistir_calculada.compare(current_base_rent) != Decimal('0'):
        nueva_renta_contrato_a_persistir_final = nueva_renta_contrato_a_persistir_calculada
    
    # Caso especial: Si se marcó un índice como pendiente EN ESTE CICLO
    if contract.aplicar_indice_retroactivo and \
       contract.indice_pendiente_mes and contract.indice_pendiente_ano and \
       contract.renta_base_pre_actualizacion_pendiente is not None and \
       contract.indice_pendiente_mes_original_aplicacion == target_month and \
       contract.indice_pendiente_ano_original_aplicacion == target_year:
        renta_para_linea_principal_alquiler = contract.renta_base_pre_actualizacion_pendiente
        if nueva_renta_contrato_a_persistir_final is not None and \
           nueva_renta_contrato_a_persistir_final.compare(contract.renta_base_pre_actualizacion_pendiente) != Decimal('0') and \
           (incremento_indice_periodico_este_mes != Decimal('0.00') or incremento_fijo_periodico_este_mes != Decimal('0.00')):
             if not (indice_pendiente_valor_raw is not None and 'incremento_del_indice_pendiente_mensual' in locals()):
                 # Si el cambio en nueva_renta_contrato_a_persistir_final se debió SOLO a la actualización periódica
                 # que AHORA está pendiente, entonces la renta del contrato NO debe cambiar.
                 # Se revierte a la renta base original si esa era la renta antes de este intento de actualización periódica.
                 if current_base_rent.compare(contract.renta_base_pre_actualizacion_pendiente) == Decimal('0'):
                     nueva_renta_contrato_a_persistir_final = None # No cambiar la renta del contrato
                 else: # Esto es un caso raro, la renta ya había cambiado por un pendiente anterior resuelto
                     nueva_renta_contrato_a_persistir_final = contract.renta_base_pre_actualizacion_pendiente


    log_info_str = " | ".join(log_info) if log_info else "Sin logs específicos de cálculo de renta."
    log_info.append(f"FINAL: Renta Línea Principal={renta_para_linea_principal_alquiler}, ConceptosAdicionales={len(conceptos_adicionales)}, NuevaRentaContratoPersistir={nueva_renta_contrato_a_persistir_final}, InfoIndice={info_indice_aplicado_para_factura}")
    return renta_para_linea_principal_alquiler, conceptos_adicionales, nueva_renta_contrato_a_persistir_final, " | ".join(log_info), info_indice_aplicado_para_factura

# --- Ruta Generar Facturas ---
@facturas_bp.route('/generar', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'gestor') # Solo admin y gestor pueden generar
def generar_facturas_mes():
    if request.method == 'POST':
        send_emails_auto = request.form.get('send_emails_auto') == 'on'
        response_messages = []
        n, o, emails_sent_ok, emails_failed, emails_skipped = 0, 0, 0, 0, 0
        errs_gen, upd_ids_contrato, nuevas_facturas_ids = [], [], []

        try:
            year = int(request.form['year'])
            month = int(request.form['month'])
            if not (1 <= month <= 12 and 1900 < year < 2200):
                raise ValueError("Fecha de facturación inválida.")

            n, o, errs_gen, upd_ids_contrato, nuevas_facturas_ids = generate_monthly_invoices(year, month)

            gen_msg = f"{MESES_STR[month]}/{year}: {n} nuevas facturas generadas, {o} omitidas."
            if upd_ids_contrato: gen_msg += f" {len(upd_ids_contrato)} contrato(s) actualizados con índice."
            
            gen_category = 'info' # Default
            if n > 0 and not any('Error:' in e for e in errs_gen): gen_category = 'success'
            elif any('Error:' in e for e in errs_gen): gen_category = 'danger'
            elif errs_gen: gen_category = 'warning' # Solo advertencias
            response_messages.append({"category": gen_category, "message": gen_msg})

            for error_item in errs_gen: # Añadir todos los errores/advertencias individuales
                response_messages.append({"category": 'warning' if 'Advertencia' in error_item else 'danger', "message": error_item})

            real_errors_in_generation = [e for e in errs_gen if 'Advertencia' not in e]
            if send_emails_auto and n > 0 and not real_errors_in_generation:
                current_app.logger.info(f"--> Iniciando envío automático de {len(nuevas_facturas_ids)} facturas...")
                if not nuevas_facturas_ids:
                     response_messages.append({"category": "warning", "message": "No hay facturas nuevas para enviar (posiblemente IDs no generados tras commit)."})
                else:
                    for invoice_id_to_send in nuevas_facturas_ids:
                        email_status = _send_single_invoice_email(invoice_id_to_send, include_bcc_owner=True)
                        if email_status == "SENT": emails_sent_ok += 1
                        elif email_status in ["NO_TENANT", "NO_EMAIL"]: emails_skipped += 1
                        else: emails_failed += 1 # PDF_ERROR, CONFIG_ERROR, SEND_ERROR
                
                if emails_sent_ok > 0: response_messages.append({"category": "success", "message": f"{emails_sent_ok} email(s) enviados correctamente."})
                if emails_skipped > 0: response_messages.append({"category": "info", "message": f"{emails_skipped} email(s) omitidos (ej. inquilino sin email)."})
                if emails_failed > 0: response_messages.append({"category": "danger", "message": f"Errores críticos enviando {emails_failed} email(s). Revisa logs."})

            elif send_emails_auto and real_errors_in_generation:
                response_messages.append({"category": "warning", "message": "Errores críticos en generación, envío automático cancelado."})

            final_status_code = 200
            if any(m['category'] == 'danger' for m in response_messages): final_status_code = 500 # Si hay errores graves
            
            return jsonify({ "messages": response_messages}), final_status_code

        except ValueError as ve:
            return jsonify({"messages": [{"category": "danger", "message": f"Error en datos de entrada: {ve}"}]}), 400
        except Exception as e:
            current_app.logger.error(f"Error general en POST /facturas/generar: {e}", exc_info=True)
            return jsonify({"messages": [{"category": "danger", "message": "Error inesperado en el servidor."}]}), 500

    hoy = date.today()
    años = list(range(hoy.year + 2, hoy.year - 5, -1)) # Rango de años para el selector
    csrf_form = CSRFOnlyForm()
    return render_template('generar_facturas.html', title="Generar Facturas",
                           default_year=hoy.year, default_month=hoy.month, # Mes actual para el default
                           years_list=años, meses=MESES_STR, csrf_form=csrf_form)

# --- FUNCIÓN generate_monthly_invoices (COMPLETA Y CORREGIDA) ---
def generate_monthly_invoices(year: int, month: int):
    iva_rate_global, irpf_rate_global = get_rates()
    nuevas, omitidas = 0, 0
    errores, contratos_actualizados_con_renta, nuevas_notificaciones_a_anadir_db, committed_invoice_ids = [], [], [], []
    historial_actualizaciones_a_anadir = []
    
    inicio_periodo_factura = date(year, month, 1)
    fin_periodo_factura = date(year, month, last_day_of_month(year, month))
    contratos_a_procesar = []

    try:
        query_contratos = db.session.query(Contrato).options(
            joinedload(Contrato.propiedad_ref).joinedload(Propiedad.propietario_ref),
            joinedload(Contrato.inquilino_ref)
        ).filter(
            Contrato.estado == 'activo',
            Contrato.fecha_inicio <= fin_periodo_factura,
            or_(Contrato.fecha_fin.is_(None), Contrato.fecha_fin >= inicio_periodo_factura)
        )
        # Primero verificar si hay propietario activo
        active_owner_context = get_active_owner_context()
        if active_owner_context and active_owner_context.get('active_owner'):
            # Si hay propietario activo, solo generar facturas para ese propietario
            active_owner_id = active_owner_context['active_owner'].id
            query_contratos = query_contratos.join(Propiedad, Contrato.propiedad_id == Propiedad.id)\
                                             .filter(Propiedad.propietario_id == active_owner_id)
            current_app.logger.info(f"generate_monthly_invoices: Generando facturas solo para propietario activo: {active_owner_context['active_owner'].nombre} (ID: {active_owner_id})")
        elif current_user.role != 'admin':
            # Si no hay propietario activo, aplicar filtro por rol para gestores
            assigned_owner_ids = [p.id for p in current_user.propietarios_asignados]
            if not assigned_owner_ids:
                current_app.logger.info("generate_monthly_invoices: Usuario no admin sin propietarios asignados.")
                return 0, 0, ["No tienes propietarios asignados para generar facturas."], [], []
            query_contratos = query_contratos.join(Propiedad, Contrato.propiedad_id == Propiedad.id)\
                                             .filter(Propiedad.propietario_id.in_(assigned_owner_ids))
        
        contratos_a_procesar = query_contratos.all()

        if not contratos_a_procesar:
            msg = f"No hay contratos activos ({current_user.role}) para generar facturas en {MESES_STR[month]}/{year}."
            current_app.logger.info(msg)
            # Devolver lista vacía de errores si no hay contratos, en lugar de mensaje de error,
            # a menos que sea un admin y no haya contratos activos en absoluto.
            no_active_contracts_at_all_system = not db.session.query(Contrato.id).filter_by(estado='activo').first()
            if current_user.role == 'admin' and no_active_contracts_at_all_system:
                return 0,0,[msg],[],[]
            return 0, 0, [], [], [] # No es un error, simplemente no hay nada que procesar para este usuario

        contrato_ids_a_procesar = [c.id for c in contratos_a_procesar]
        facturas_existentes_tuplas = db.session.query(Factura.contrato_id).filter(
            extract('year', Factura.fecha_emision) == year,
            extract('month', Factura.fecha_emision) == month,
            Factura.contrato_id.in_(contrato_ids_a_procesar)
        ).distinct().all()
        facturas_existentes = {f[0] for f in facturas_existentes_tuplas} # Convertir lista de tuplas a set de IDs

    except Exception as e_initial:
        db.session.rollback()
        errores.append(f"Error inicial obteniendo datos para generación: {e_initial}")
        current_app.logger.error(f"Error fatal en generate_monthly_invoices (fase inicial): {e_initial}", exc_info=True)
        return 0, 0, errores, [], []

    facturas_nuevas_a_anadir_db = []
    gastos_a_vincular_con_factura = {} 
    contratos_modificados_por_actualizacion_renta_o_serie = []
    ultimos_secuenciales_usados_en_sesion = {}

    for contrato in contratos_a_procesar:
        factura_temporal_obj = None 
        log_mensajes_este_contrato_iteracion = []

        if contrato.id in facturas_existentes:
            omitidas += 1
            current_app.logger.info(f"Contrato {contrato.numero_contrato}: Factura ya existe para {month}/{year}, omitiendo.")
            continue
        
        try:
            renta_original_contrato_antes_calculo = contrato.precio_mensual
            renta_principal_a_facturar, items_actualizacion_y_atrasos, \
            nueva_renta_base_contrato_a_persistir, log_calculo_renta, \
            info_indice_para_esta_factura = \
                _calculate_updated_rent_for_invoice(contrato, year, month, renta_original_contrato_antes_calculo)
            
            if log_calculo_renta: log_mensajes_este_contrato_iteracion.append(f"Cálculo Renta: {log_calculo_renta}")

            items_gastos_para_factura, total_importe_gastos_mes, gastos_objetos_a_actualizar_en_bd = [], Decimal('0.00'), []
            gastos_pendientes_query = Gasto.query.filter(Gasto.contrato_id == contrato.id, Gasto.estado == 'Pendiente',
                or_((Gasto.month == month) & (Gasto.year == year), (Gasto.month.is_(None)) & (Gasto.year.is_(None)),
                    (Gasto.month.is_(None)) & (Gasto.year == year), (Gasto.month == month) & (Gasto.year.is_(None)))
            ).order_by(Gasto.upload_date).all()
            for gasto_obj_db in gastos_pendientes_query:
                items_gastos_para_factura.append({"description": f"Gasto: {gasto_obj_db.concepto}", "quantity": 1, "unitPrice": float(gasto_obj_db.importe), "total": float(gasto_obj_db.importe)})
                total_importe_gastos_mes += gasto_obj_db.importe; gastos_objetos_a_actualizar_en_bd.append(gasto_obj_db)

            items_json_final_factura = [{"description": f"Alquiler {MESES_STR[month]} {year}", "quantity": 1, "unitPrice": float(renta_principal_a_facturar), "total": float(renta_principal_a_facturar)}]
            items_json_final_factura.extend(items_actualizacion_y_atrasos); items_json_final_factura.extend(items_gastos_para_factura)
            subtotal_calculado_factura = renta_principal_a_facturar
            for item_adicional in items_actualizacion_y_atrasos: subtotal_calculado_factura += Decimal(str(item_adicional.get('total', '0.00')))
            subtotal_calculado_factura = (subtotal_calculado_factura + total_importe_gastos_mes).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            tasa_iva_a_aplicar_factura = iva_rate_global if contrato.aplicar_iva else Decimal('0.00')
            tasa_irpf_a_aplicar_factura = irpf_rate_global if contrato.aplicar_irpf else Decimal('0.00')
            iva_monto_calculado_factura = (subtotal_calculado_factura * tasa_iva_a_aplicar_factura).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            irpf_monto_calculado_factura = (subtotal_calculado_factura * tasa_irpf_a_aplicar_factura).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            total_calculado_factura = (subtotal_calculado_factura + iva_monto_calculado_factura - irpf_monto_calculado_factura).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            fecha_emision_calculada = date(year, month, contrato.dia_pago if 1 <= contrato.dia_pago <= last_day_of_month(year, month) else 1)
            
            
            numero_factura_visible_final = ""
            numero_factura_bd_final = "" 
            serie_usada_log_detalle_local = "No especificada" 
            
            if contrato.serie_facturacion_prefijo and contrato.serie_facturacion_prefijo.strip():
                # --- CASO 1: El contrato SÍ tiene un prefijo de serie definido ---
                serie_usada_log_detalle_local = f"Contrato ({contrato.serie_facturacion_prefijo})"
                prefijo_de_contrato = contrato.serie_facturacion_prefijo
                ultimo_numero_guardado_en_contrato = contrato.serie_facturacion_ultimo_numero or 0
                ano_serie_guardado_en_contrato = contrato.serie_facturacion_ano_actual
                formato_digitos_de_contrato = contrato.serie_facturacion_formato_digitos or 4 # Default 4 si no está
                
                proximo_secuencial_para_este_contrato = 0
                if ano_serie_guardado_en_contrato is None or ano_serie_guardado_en_contrato != year:
                    # Si es un año nuevo para la serie, o si nunca se ha usado, empezar en 1
                    proximo_secuencial_para_este_contrato = 1
                else:
                    proximo_secuencial_para_este_contrato = ultimo_numero_guardado_en_contrato + 1
                
                secuencial_formateado = f"{proximo_secuencial_para_este_contrato:0{formato_digitos_de_contrato}d}"
                
                # Construir la parte visible
                if str(year) in prefijo_de_contrato: # Si el año ya está en el prefijo (poco común pero posible)
                    numero_factura_visible_final = f"{prefijo_de_contrato}{secuencial_formateado}"
                else:
                    numero_factura_visible_final = f"{prefijo_de_contrato.rstrip('-')}-{year}-{secuencial_formateado}"
                
                # El número para la BD siempre lleva el identificador del contrato
                numero_factura_bd_final = f"C{contrato.id}-{numero_factura_visible_final}" 
                
                # Actualizar el contrato con el último número y año de la serie
                contrato.serie_facturacion_ultimo_numero = proximo_secuencial_para_este_contrato
                contrato.serie_facturacion_ano_actual = year
                if contrato not in contratos_modificados_por_actualizacion_renta_o_serie:
                    contratos_modificados_por_actualizacion_renta_o_serie.append(contrato)
                log_mensajes_este_contrato_iteracion.append(f"Serie Contrato '{prefijo_de_contrato}', año {year}. Próximo secuencial: {proximo_secuencial_para_este_contrato}.")

            else: 
                # --- CASO 2: El contrato NO tiene un prefijo de serie definido ---
                serie_usada_log_detalle_local = "Sin prefijo (Año-Secuencial)"
                # El prefijo para la BD contendrá el ID del contrato y el año.
                # La parte visible será AÑO-SECUENCIAL.
                
                # Usaremos el último número y año guardados en el contrato si existen,
                # incluso sin prefijo, para mantener una secuencia por contrato/año.
                ultimo_numero_guardado_sin_prefijo = contrato.serie_facturacion_ultimo_numero or 0
                ano_serie_guardado_sin_prefijo = contrato.serie_facturacion_ano_actual
                formato_digitos_sin_prefijo = contrato.serie_facturacion_formato_digitos or 3 # Default 3 para este caso si lo prefieres

                proximo_secuencial_sin_prefijo = 0
                if ano_serie_guardado_sin_prefijo is None or ano_serie_guardado_sin_prefijo != year:
                    proximo_secuencial_sin_prefijo = 1
                else:
                    proximo_secuencial_sin_prefijo = ultimo_numero_guardado_sin_prefijo + 1
                
                secuencial_formateado_sin_prefijo = f"{proximo_secuencial_sin_prefijo:0{formato_digitos_sin_prefijo}d}"
                
                numero_factura_visible_final = f"{year}-{secuencial_formateado_sin_prefijo}"
                numero_factura_bd_final = f"C{contrato.id}-{numero_factura_visible_final}"

                # Actualizar el contrato con el último número y año, incluso sin prefijo,
                # para que la secuencia continúe correctamente la próxima vez.
                contrato.serie_facturacion_ultimo_numero = proximo_secuencial_sin_prefijo
                contrato.serie_facturacion_ano_actual = year
                # No es necesario guardar `serie_facturacion_prefijo = None` explícitamente si ya es None.
                # `serie_facturacion_formato_digitos` se mantendría o usaría el default.
                if contrato not in contratos_modificados_por_actualizacion_renta_o_serie:
                    contratos_modificados_por_actualizacion_renta_o_serie.append(contrato)
                log_mensajes_este_contrato_iteracion.append(f"Serie sin prefijo. Próximo secuencial para contrato {contrato.id}, año {year}: {proximo_secuencial_sin_prefijo}")

            # --- FIN LÓGICA MODIFICADA ---
            
            current_app.logger.info(f"Contrato {contrato.numero_contrato}: Info Serie Factura: {' '.join(log_mensajes_este_contrato_iteracion)} | Serie Usada Final: {serie_usada_log_detalle_local}. Número BD: {numero_factura_bd_final}, Visible: {numero_factura_visible_final}")
            
            
            
            notas_factura_final_list = [f"Factura correspondiente al periodo de {MESES_STR[month]}/{year}."]
            if info_indice_para_esta_factura and isinstance(info_indice_para_esta_factura, dict):
                tipo_idx, mes_idx, ano_idx, porc_idx = info_indice_para_esta_factura.get('type'), info_indice_para_esta_factura.get('month'), info_indice_para_esta_factura.get('year'), info_indice_para_esta_factura.get('percentage')
                nota_idx = f"Incluye actualización de renta por {tipo_idx or 'Índice'}"
                if mes_idx and ano_idx: nota_idx += f" de {MESES_STR[int(mes_idx)]}/{ano_idx}"
                if porc_idx is not None: nota_idx += f" ({porc_idx:+.2f}%)".replace('.', ',')
                notas_factura_final_list.append(nota_idx + ".")
            if any("Atrasos act." in item.get("description","") for item in items_actualizacion_y_atrasos if isinstance(item, dict)): notas_factura_final_list.append("Incluye regularización de atrasos por actualización de índice.")
            if gastos_objetos_a_actualizar_en_bd: notas_factura_final_list.append(f"Incluye {len(gastos_objetos_a_actualizar_en_bd)} gasto(s) repercutido(s).")
            notas_factura_para_bd = " ".join(notas_factura_final_list)

            factura_temporal_obj = Factura(numero_factura=numero_factura_bd_final, contrato_id=contrato.id, inquilino_id=contrato.inquilino_id, propiedad_id=contrato.propiedad_id, fecha_emision=fecha_emision_calculada, subtotal=subtotal_calculado_factura, iva=iva_monto_calculado_factura, irpf=irpf_monto_calculado_factura, total=total_calculado_factura, estado='pendiente', items_json=json.dumps(items_json_final_factura, ensure_ascii=False), notas=notas_factura_para_bd.strip(), iva_rate_applied=tasa_iva_a_aplicar_factura, irpf_rate_applied=tasa_irpf_a_aplicar_factura, indice_aplicado_info=info_indice_para_esta_factura)
            db.session.add(factura_temporal_obj); db.session.flush()
            
            if factura_temporal_obj.id and contrato.propiedad_ref and contrato.propiedad_ref.propietario_ref:
                nombre_pdf = f"factura_{factura_temporal_obj.numero_factura_mostrado_al_cliente}.pdf"
                ruta_pdf = get_owner_document_path(contrato.propiedad_ref.propietario_ref, "Facturas Alquiler", year, nombre_pdf)
                if ruta_pdf:
                    pdf_buffer = generate_invoice_pdf(factura_temporal_obj.id)
                    if pdf_buffer:
                        try:
                            with open(ruta_pdf, 'wb') as f_pdf: f_pdf.write(pdf_buffer.getbuffer())
                        except Exception as e_save_pdf: current_app.logger.error(f"Error guardando PDF {nombre_pdf}: {e_save_pdf}"); errores.append(f"Adv: No se guardó PDF para {factura_temporal_obj.numero_factura}.")
                    else: errores.append(f"Adv: No se generó PDF para {contrato.numero_contrato}.")
            
            if factura_temporal_obj not in facturas_nuevas_a_anadir_db: facturas_nuevas_a_anadir_db.append(factura_temporal_obj)
            nuevas += 1
            if gastos_objetos_a_actualizar_en_bd: gastos_a_vincular_con_factura[factura_temporal_obj] = gastos_objetos_a_actualizar_en_bd

            if nueva_renta_base_contrato_a_persistir is not None and contrato.precio_mensual != nueva_renta_base_contrato_a_persistir:
                hist_entry = HistorialActualizacionRenta(contrato_id=contrato.id, factura_id=factura_temporal_obj.id, fecha_actualizacion=date(year,month,1), renta_anterior=renta_original_contrato_antes_calculo, renta_nueva=nueva_renta_base_contrato_a_persistir, tipo_actualizacion=contrato.tipo_actualizacion_renta)
                if info_indice_para_esta_factura:
                    hist_entry.indice_nombre=info_indice_para_esta_factura.get('type'); hist_entry.indice_mes=info_indice_para_esta_factura.get('month')
                    hist_entry.indice_ano=info_indice_para_esta_factura.get('year'); hist_entry.indice_porcentaje=Decimal(str(info_indice_para_esta_factura.get('percentage','0.0')))
                if contrato.tipo_actualizacion_renta in ['fijo', 'indice_mas_fijo'] and contrato.importe_actualizacion_fija: hist_entry.importe_fijo_aplicado = contrato.importe_actualizacion_fija
                historial_actualizaciones_a_anadir.append(hist_entry)
                contrato.precio_mensual = nueva_renta_base_contrato_a_persistir
                if contrato.id not in contratos_actualizados_con_renta: contratos_actualizados_con_renta.append(contrato.id)
                if contrato not in contratos_modificados_por_actualizacion_renta_o_serie: contratos_modificados_por_actualizacion_renta_o_serie.append(contrato)
            if db.session.is_modified(contrato) and contrato not in contratos_modificados_por_actualizacion_renta_o_serie: contratos_modificados_por_actualizacion_renta_o_serie.append(contrato)

        except ValueError as ve_calc: 
            db.session.rollback(); errores.append(f"Error Contrato {contrato.numero_contrato}: {ve_calc}"); omitidas +=1
            current_app.logger.warning(f"ValueError procesando Contrato {contrato.numero_contrato}: {ve_calc}")
            # ... (lógica de notificación por error de índice) ...
        except Exception as e_item: 
            db.session.rollback(); errores.append(f"Error Inesperado Contrato {contrato.numero_contrato}: {e_item}"); omitidas +=1
            current_app.logger.error(f"Error procesando contrato {contrato.numero_contrato}: {e_item}\n{traceback.format_exc()}")
            if factura_temporal_obj in facturas_nuevas_a_anadir_db: facturas_nuevas_a_anadir_db.remove(factura_temporal_obj); nuevas = max(0, nuevas-1)
            if factura_temporal_obj in gastos_a_vincular_con_factura: del gastos_a_vincular_con_factura[factura_temporal_obj]

    if facturas_nuevas_a_anadir_db or nuevas_notificaciones_a_anadir_db or contratos_modificados_por_actualizacion_renta_o_serie or any(gastos_a_vincular_con_factura.values()) or historial_actualizaciones_a_anadir:
        try:
            if nuevas_notificaciones_a_anadir_db: db.session.add_all(nuevas_notificaciones_a_anadir_db)
            if historial_actualizaciones_a_anadir: db.session.add_all(historial_actualizaciones_a_anadir)
            for contrato_mod in contratos_modificados_por_actualizacion_renta_o_serie:
                if contrato_mod not in db.session: db.session.add(contrato_mod) # Si no está ya en sesión (aunque debería)
            
            # db.session.flush() # No es estrictamente necesario aquí si el flush ya se hizo en el bucle para obtener IDs de factura.
            
            for factura_obj_con_gastos, lista_gastos in gastos_a_vincular_con_factura.items():
                if factura_obj_con_gastos.id: 
                    for gasto_a_upd in lista_gastos: 
                        gasto_a_upd.factura_id = factura_obj_con_gastos.id; gasto_a_upd.estado = 'Facturado'; gasto_a_upd.integrated = True
                        if gasto_a_upd not in db.session: db.session.add(gasto_a_upd) # Solo añadir si no estaba ya
            
            db.session.commit()
            committed_invoice_ids = [f.id for f in facturas_nuevas_a_anadir_db if f.id]
        except Exception as e_commit: 
            db.session.rollback(); errores.append(f"Error crítico commit final: {e_commit}")
            current_app.logger.error(f"Error crítico en commit final generate_monthly_invoices: {e_commit}\n{traceback.format_exc()}")
            nuevas=0; omitidas=len(contratos_a_procesar); contratos_actualizados_con_renta=[]; committed_invoice_ids=[]
            
    return nuevas, omitidas, errores, list(set(contratos_actualizados_con_renta)), committed_invoice_ids

@facturas_bp.route('/enviar_masivo', methods=['GET', 'POST'])
@login_required # Asegurar que está protegido
# @role_required('admin', 'gestor') # Podrías añadir esto si solo estos roles pueden usarlo
def enviar_facturas_masivo():
    csrf_form = CSRFOnlyForm() # Para el método GET y el formulario

    if request.method == 'POST':
        try:
            year = int(request.form['year'])
            month = int(request.form['month'])
            if not (1 <= month <= 12): raise ValueError("Mes inválido")
            if not (1900 < year < 2200): raise ValueError("Año inválido")

            current_app.logger.info(f"Iniciando búsqueda de facturas para envío masivo por {current_user.username}: {month}/{year}")

            # --- Query Base para Facturas ---
            query_facturas = db.session.query(Factura).filter(
                extract('year', Factura.fecha_emision) == year,
                extract('month', Factura.fecha_emision) == month
            ).options( # Cargar relaciones necesarias para la función de envío
                joinedload(Factura.inquilino_ref),
                joinedload(Factura.propiedad_ref).joinedload(Propiedad.propietario_ref),
                joinedload(Factura.contrato_ref),
                selectinload(Factura.gastos_incluidos) # Para adjuntar gastos
            )

            # *** APLICAR FILTRO POR PROPIETARIO ACTIVO Y ROL ***
            # Primero verificar si hay propietario activo
            active_owner_context = get_active_owner_context()
            if active_owner_context and active_owner_context.get('active_owner'):
                # Si hay propietario activo, solo enviar facturas de ese propietario
                active_owner_id = active_owner_context['active_owner'].id
                query_facturas = query_facturas.join(Factura.propiedad_ref).filter(
                    Propiedad.propietario_id == active_owner_id
                )
                current_app.logger.info(f"Envío masivo limitado al propietario activo: {active_owner_context['active_owner'].nombre} (ID: {active_owner_id})")
            elif current_user.role != 'admin':
                # Si no hay propietario activo, aplicar filtro por rol para gestores
                assigned_owner_ids = [p.id for p in current_user.propietarios_asignados]
                if not assigned_owner_ids:
                    flash(f"No tienes propietarios asignados para el envío masivo de facturas.", 'warning')
                    current_app.logger.warning(f"Usuario {current_user.username} (rol {current_user.role}) intentó envío masivo sin propietarios asignados.")
                    return redirect(url_for('facturas_bp.enviar_facturas_masivo'))
                
                # Filtrar facturas para que solo incluya las de propiedades de sus propietarios asignados
                query_facturas = query_facturas.join(Factura.propiedad_ref).filter(
                    Propiedad.propietario_id.in_(assigned_owner_ids)
                )
                current_app.logger.info(f"Envío masivo para {current_user.username} limitado a propietarios asignados: {assigned_owner_ids}")
            else:
                current_app.logger.info(f"Envío masivo como ADMIN (todos los propietarios aplicables al mes/año).")
            # *** FIN FILTRO ***

            facturas_a_enviar = query_facturas.all()

            if not facturas_a_enviar:
                flash(f"No se encontraron facturas ({'tus ' if current_user.role != 'admin' else ''}facturas) emitidas en {MESES_STR[month]}/{year} para enviar.", 'warning')
                return redirect(url_for('facturas_bp.enviar_facturas_masivo', csrf_form=csrf_form))

            current_app.logger.info(f"Se encontraron {len(facturas_a_enviar)} facturas. Iniciando proceso de envío masivo...")

            # --- Proceso de Envío Masivo (sin cambios en la lógica interna del bucle) ---
            emails_sent_ok, emails_failed, emails_skipped = 0, 0, 0
            for factura in facturas_a_enviar:
                # ... (tu lógica existente para comprobar email de inquilino y llamar a _send_single_invoice_email) ...
                if not factura.inquilino_ref or not factura.inquilino_ref.email:
                     current_app.logger.warning(f"[Envío Masivo] Saltando Factura {factura.numero_factura}: Inquilino sin email.")
                     emails_skipped += 1
                     continue
                try:
                    success = _send_single_invoice_email(factura.id, include_bcc_owner=True)
                    if success is True: emails_sent_ok += 1
                    elif success is False: emails_failed += 1
                    else: emails_skipped +=1 # Considerar valor inesperado como omitido/otro
                except Exception as e_send_masivo:
                     current_app.logger.error(f"[Envío Masivo] Excepción enviando email para factura ID {factura.id}: {e_send_masivo}", exc_info=True)
                     emails_failed += 1
            # --- Fin Proceso de Envío ---

            # Mensajes flash con el resumen
            flash(f"Proceso de envío masivo para {MESES_STR[month]}/{year} completado.", 'info')
            if emails_sent_ok > 0: flash(f"{emails_sent_ok} email(s) enviados/omitidos correctamente.", 'success')
            if emails_skipped > 0: flash(f"{emails_skipped} factura(s) omitidas (ej. inquilino sin email).", 'warning')
            if emails_failed > 0: flash(f"Hubo errores críticos al enviar {emails_failed} email(s). Revisa los logs.", 'danger')

        except ValueError as ve:
            flash(f"Error en los datos: {ve}", 'danger')
        except Exception as e:
            flash(f"Error inesperado durante el envío masivo: {e}", 'danger')
            current_app.logger.error(f"Error en POST /enviar_masivo: {e}", exc_info=True)
            db.session.rollback() # Rollback por si acaso, aunque aquí no modificamos BD

        return redirect(url_for('facturas_bp.enviar_facturas_masivo'))

    # --- GET Request ---
    # Preparar datos para los selectores de año/mes (similar a generar_facturas)
    csrf_form = CSRFOnlyForm()
    hoy = date.today()
    # Lista de años recientes (puedes ajustar el rango)
    years_list = list(range(hoy.year + 1, hoy.year - 5, -1))
    
    # Incluir información del propietario activo si existe
    active_owner_context = get_active_owner_context()
    active_owner_info = None
    if active_owner_context and active_owner_context.get('active_owner'):
        active_owner_info = {
            'id': active_owner_context['active_owner'].id,
            'nombre': active_owner_context['active_owner'].nombre
        }
    
    return render_template(
        'enviar_facturas_masivo.html',
        title="Envío Masivo Emails",
        years_list=years_list,
        meses=MESES_STR,
        csrf_form=csrf_form,
        active_owner_info=active_owner_info
    )


# --- Rutas CRUD Facturas ---
@facturas_bp.route('/', methods=['GET'])
@filtered_list_view(entity_type='factura', log_queries=True)
def listar_facturas():
    """Lista facturas filtradas automáticamente por propietario activo."""
    iva_rate_actual, irpf_rate_actual = get_rates()
    csrf_form_inst = CSRFOnlyForm()
    pagination = None # Inicializar para evitar NameError si hay excepción temprana

    current_app.logger.info(f"--- LISTAR FACTURAS - Usuario: {current_user.username}, Rol: {current_user.role} ---")

    try:
        # Filtrado automático aplicado - solo facturas del propietario activo
        facturas_a_mostrar = get_filtered_facturas(
            include_relations=True
        ).order_by(Factura.fecha_emision.desc(), Factura.id.desc()).all()
        
        current_app.logger.info(f"Número de facturas recuperadas después del filtro automático: {len(facturas_a_mostrar)}")

        # Datos filtrados automáticamente para los selectores y modales
        propiedades_para_modales = get_filtered_propiedades().order_by(Propiedad.direccion).all()
        inquilinos_para_select = get_filtered_inquilinos().order_by(Inquilino.nombre).all()
        contratos_para_select = get_filtered_contratos(include_relations=True).order_by(Contrato.numero_contrato).all()
        
        # Propietarios disponibles del contexto automático
        owner_context = get_active_owner_context()
        propietarios_para_select = owner_context.get('available_owners', [])

    except Exception as e:
        flash(f"Error crítico al cargar datos de facturas: {e}", 'danger')
        current_app.logger.error("Error severo en GET /facturas: %s", e, exc_info=True)
        facturas_a_mostrar, inquilinos_para_select, propiedades_para_modales, propietarios_para_select, contratos_para_select = [], [], [], [], []
        pagination = None

    return render_template(
        'facturas.html',
        title='Facturas',
        facturas=facturas_a_mostrar,
        pagination=pagination, # Será None si no se usa paginación
        propietarios=propietarios_para_select, # Para el filtro de propietario
        inquilinos=inquilinos_para_select,     # Para el filtro de inquilino y modales
        propiedades=propiedades_para_modales, # Para el modal de crear factura
        contratos=contratos_para_select,       # Para el filtro de contrato
        IRPF_RATE=float(irpf_rate_actual),
        IVA_RATE=float(iva_rate_actual),
        csrf_form=csrf_form_inst
    )


# --- NUEVA RUTA PARA BORRADO MASIVO DE FACTURAS ---
@facturas_bp.route('/borrado_masivo', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'gestor') # O el rol que consideres apropiado
def borrar_facturas_masivo():
    csrf_form = CSRFOnlyForm()
    propietarios_para_select = []
    todos_los_contratos_objs = [] # Renombrado para claridad
    contratos_para_select_json = []

    # Lógica para poblar selectores en GET
    if current_user.role == 'admin':
        propietarios_para_select = Propietario.query.order_by(Propietario.nombre).all()
        todos_los_contratos_objs = Contrato.query.options(
            joinedload(Contrato.propiedad_ref).joinedload(Propiedad.propietario_ref), 
            joinedload(Contrato.inquilino_ref)
        ).order_by(Contrato.numero_contrato).all()
    elif current_user.role == 'gestor':
        # Asegurar que es una lista y no una relación perezosa si viene directo de current_user
        propietarios_para_select = sorted(list(current_user.propietarios_asignados), key=lambda p: p.nombre)
        assigned_owner_ids = [p.id for p in propietarios_para_select]
        if assigned_owner_ids:
            todos_los_contratos_objs = Contrato.query.join(Propiedad)\
                .filter(Propiedad.propietario_id.in_(assigned_owner_ids))\
                .options(joinedload(Contrato.propiedad_ref).joinedload(Propiedad.propietario_ref), 
                         joinedload(Contrato.inquilino_ref))\
                .order_by(Contrato.numero_contrato).all()
    
    for c_data in todos_los_contratos_objs:
        contratos_para_select_json.append({
            "id": c_data.id, "numero": c_data.numero_contrato,
            "propietario_id": c_data.propiedad_ref.propietario_id if c_data.propiedad_ref else None,
            "direccion_propiedad": c_data.propiedad_ref.direccion if c_data.propiedad_ref else "N/A",
            "nombre_inquilino": c_data.inquilino_ref.nombre if c_data.inquilino_ref else "N/A"
        })

    if request.method == 'POST':
        current_app.logger.info("--- INICIO BORRADO MASIVO FACTURAS (POST) ---")
        propietario_id_str = request.form.get('propietario_id')
        contrato_id_str = request.form.get('contrato_id')
        mes_desde_str = request.form.get('mes_desde')
        mes_hasta_str = request.form.get('mes_hasta')
        ano_str = request.form.get('ano')

        if not ano_str or not mes_desde_str or not mes_hasta_str:
            flash("Año, Mes Desde y Mes Hasta son obligatorios.", "warning")
            return redirect(url_for('facturas_bp.borrar_facturas_masivo'))
        
        try:
            ano = int(ano_str); mes_desde = int(mes_desde_str); mes_hasta = int(mes_hasta_str)
            if not (1 <= mes_desde <= 12 and 1 <= mes_hasta <= 12 and mes_desde <= mes_hasta): raise ValueError("Rango de meses inválido.")
            if not (2000 < ano < 2200): raise ValueError("Año inválido.")
        except ValueError as ve:
            flash(f"Error en los parámetros de fecha: {ve}", "danger")
            return redirect(url_for('facturas_bp.borrar_facturas_masivo'))

        query_facturas_a_borrar = db.session.query(Factura).options(
            joinedload(Factura.contrato_ref), 
            joinedload(Factura.gastos_incluidos),
            joinedload(Factura.actualizacion_renta_origen) # Cargar el historial asociado
        ).filter(
            extract('year', Factura.fecha_emision) == ano,
            extract('month', Factura.fecha_emision) >= mes_desde,
            extract('month', Factura.fecha_emision) <= mes_hasta
        )

        if propietario_id_str and propietario_id_str != 'all':
            try:
                propietario_id_int = int(propietario_id_str)
                if current_user.role != 'admin':
                    assigned_owner_ids = {p.id for p in current_user.propietarios_asignados}
                    if propietario_id_int not in assigned_owner_ids:
                        flash("No tienes permiso para borrar facturas de este propietario.", "danger"); return redirect(url_for('facturas_bp.borrar_facturas_masivo'))
                query_facturas_a_borrar = query_facturas_a_borrar.join(Factura.propiedad_ref).filter(Propiedad.propietario_id == propietario_id_int)
            except ValueError:
                flash("ID de propietario inválido.", "danger"); return redirect(url_for('facturas_bp.borrar_facturas_masivo'))
        elif current_user.role != 'admin': # Si es 'all' pero el usuario no es admin, aplicar filtro de sus propietarios
            assigned_owner_ids = {p.id for p in current_user.propietarios_asignados}
            if not assigned_owner_ids:
                 flash("No tienes propietarios asignados.", "warning"); return redirect(url_for('facturas_bp.borrar_facturas_masivo'))
            query_facturas_a_borrar = query_facturas_a_borrar.join(Factura.propiedad_ref).filter(Propiedad.propietario_id.in_(assigned_owner_ids))

        if contrato_id_str and contrato_id_str not in ['all_contracts_of_owner', 'all_contracts_global']:
            try:
                contrato_id_int = int(contrato_id_str)
                query_facturas_a_borrar = query_facturas_a_borrar.filter(Factura.contrato_id == contrato_id_int)
            except ValueError:
                flash("ID de contrato inválido.", "danger"); return redirect(url_for('facturas_bp.borrar_facturas_masivo'))
        
        facturas_seleccionadas = query_facturas_a_borrar.all()
        current_app.logger.info(f"Se encontraron {len(facturas_seleccionadas)} facturas para borrar según criterios.")

        if not facturas_seleccionadas:
            flash("No se encontraron facturas que coincidan con los criterios para eliminar.", "info")
            return redirect(url_for('facturas_bp.borrar_facturas_masivo'))

        ids_facturas_a_borrar = {f.id for f in facturas_seleccionadas}
        contratos_afectados_renta_ids = {f.contrato_id for f in facturas_seleccionadas if f.contrato_id and f.actualizacion_renta_origen}
        contratos_afectados_serie_ids = {f.contrato_id for f in facturas_seleccionadas if f.contrato_id}
        
        rentas_reversion_calculadas = {}

        # 1. Calcular rentas de reversión ANTES de borrar nada
        for contrato_id_rev in contratos_afectados_renta_ids:
            ultimo_historial_restante = HistorialActualizacionRenta.query \
                .filter(HistorialActualizacionRenta.contrato_id == contrato_id_rev) \
                .filter(db.or_(HistorialActualizacionRenta.factura_id.is_(None), 
                               HistorialActualizacionRenta.factura_id.notin_(ids_facturas_a_borrar))) \
                .order_by(HistorialActualizacionRenta.fecha_actualizacion.desc(), HistorialActualizacionRenta.id.desc()) \
                .first()
            if ultimo_historial_restante:
                rentas_reversion_calculadas[contrato_id_rev] = ultimo_historial_restante.renta_nueva
            else:
                # Si no hay historial restante, podríamos tener una renta base en el contrato o loguear
                contrato_temp = db.session.get(Contrato, contrato_id_rev)
                if contrato_temp and hasattr(contrato_temp, 'precio_base_original'): # Si tuvieras este campo
                     rentas_reversion_calculadas[contrato_id_rev] = contrato_temp.precio_base_original
                else:
                    current_app.logger.warning(f"Contrato ID {contrato_id_rev}: Sin historial de renta válido restante ni precio base. No se revierte renta automáticamente.")
        
        num_facturas_borradas_count = 0
        try:
            current_app.logger.info("Iniciando proceso de borrado de facturas y reseteo de gastos...")
            historial_para_borrar_directo_ids = []
            for factura_a_borrar in facturas_seleccionadas:
                if hasattr(factura_a_borrar, 'gastos_incluidos'):
                    for gasto_item in factura_a_borrar.gastos_incluidos:
                        gasto_item.estado = 'Pendiente'; gasto_item.integrated = False; gasto_item.factura_id = None
                        db.session.add(gasto_item)
                if factura_a_borrar.actualizacion_renta_origen:
                    historial_para_borrar_directo_ids.append(factura_a_borrar.actualizacion_renta_origen.id)
                db.session.delete(factura_a_borrar)
                num_facturas_borradas_count += 1
            
            if historial_para_borrar_directo_ids:
                db.session.query(HistorialActualizacionRenta).filter(HistorialActualizacionRenta.id.in_(historial_para_borrar_directo_ids)).delete(synchronize_session='fetch')
                current_app.logger.info(f"{len(historial_para_borrar_directo_ids)} registros de HistorialActualizacionRenta eliminados (asociados directamente).")

            contratos_renta_modificada_count = 0
            for contrato_id_rev, nueva_renta_calculada in rentas_reversion_calculadas.items():
                contrato_obj_rev = db.session.get(Contrato, contrato_id_rev)
                if contrato_obj_rev and contrato_obj_rev.precio_mensual != nueva_renta_calculada:
                    contrato_obj_rev.precio_mensual = nueva_renta_calculada
                    db.session.add(contrato_obj_rev)
                    contratos_renta_modificada_count += 1
                    current_app.logger.info(f"Contrato {contrato_obj_rev.numero_contrato}: Renta ajustada a {nueva_renta_calculada} post-borrado masivo.")
            
            for contrato_id_serie in contratos_afectados_serie_ids:
                contrato_obj_serie = db.session.get(Contrato, contrato_id_serie)
                if not contrato_obj_serie or not contrato_obj_serie.serie_facturacion_prefijo: continue
                
                # Año de la serie a recalcular: el año de las facturas borradas
                # o el año actual de la serie del contrato si es diferente y relevante.
                # Por simplicidad, si se borran facturas de un `ano` específico, recalculamos la serie para ESE `ano`.
                ano_serie_a_recalcular = ano 
                
                ultima_factura_restante_db = db.session.query(Factura.numero_factura)\
                    .filter(Factura.contrato_id == contrato_id_serie,
                            extract('year', Factura.fecha_emision) == ano_serie_a_recalcular)\
                    .order_by(Factura.id.desc()).first()

                if ultima_factura_restante_db:
                    try:
                        num_f_bd_r = ultima_factura_restante_db[0]
                        part_vis_r = num_f_bd_r.split('-',1)[1] if num_f_bd_r.startswith(f"C{contrato_id_serie}-") else num_f_bd_r
                        sec_str_r = part_vis_r.rsplit('-', 1)[-1]; nuevo_ult_num = int(sec_str_r)
                        
                        contrato_obj_serie.serie_facturacion_ultimo_numero = nuevo_ult_num
                        contrato_obj_serie.serie_facturacion_ano_actual = ano_serie_a_recalcular
                        db.session.add(contrato_obj_serie)
                        current_app.logger.info(f"Contrato {contrato_obj_serie.numero_contrato}: Serie ajustada a {nuevo_ult_num} para año {ano_serie_a_recalcular}.")
                    except Exception as e_parse:
                        current_app.logger.warning(f"Fallo parseo serie para contrato {contrato_obj_serie.numero_contrato} (factura {ultima_factura_restante_db[0]}): {e_parse}. Reseteando para {ano_serie_a_recalcular}.")
                        contrato_obj_serie.serie_facturacion_ultimo_numero = 0
                        contrato_obj_serie.serie_facturacion_ano_actual = ano_serie_a_recalcular
                        db.session.add(contrato_obj_serie)
                else: # No quedan facturas en este año de serie
                    if contrato_obj_serie.serie_facturacion_ano_actual == ano_serie_a_recalcular or \
                       (contrato_obj_serie.serie_facturacion_ano_actual is None and ano_serie_a_recalcular == ano): # si el año de la serie era el que se borró
                        contrato_obj_serie.serie_facturacion_ultimo_numero = 0
                        contrato_obj_serie.serie_facturacion_ano_actual = ano_serie_a_recalcular # Mantener el año para el que se reseteó
                        db.session.add(contrato_obj_serie)
                        current_app.logger.info(f"Contrato {contrato_obj_serie.numero_contrato}: No quedan facturas para año {ano_serie_a_recalcular}. Serie reseteada.")
            
            db.session.commit()
            flash_msg = f"{num_facturas_borradas_count} factura(s) eliminada(s) correctamente."
            if contratos_renta_modificada_count > 0: flash_msg += f" {contratos_renta_modificada_count} renta(s) de contrato ajustadas."
            if contratos_afectados_serie_ids: flash_msg += " Series de facturación de contratos afectadas han sido ajustadas."
            flash(flash_msg, "success")
            current_app.logger.info("Commit de borrado masivo EXITOSO.")

        except Exception as e_borrado_general:
            db.session.rollback()
            flash(f"Error durante el proceso de borrado masivo: {e_borrado_general}", "danger")
            current_app.logger.error(f"Error en borrado masivo (general): {e_borrado_general}", exc_info=True)

        return redirect(url_for('facturas_bp.borrar_facturas_masivo'))

    # Para GET request - Incluir propietario activo si existe
    active_owner_context = get_active_owner_context()
    preselected_owner_id = None
    if active_owner_context and active_owner_context.get('active_owner'):
        preselected_owner_id = active_owner_context['active_owner'].id
    
    return render_template('reports/borrar_facturas_masivo.html',
                           title="Borrado Masivo de Facturas",
                           propietarios=propietarios_para_select,
                           contratos_json=json.dumps(contratos_para_select_json), # Pasar los contratos formateados para JS
                           csrf_form=csrf_form,
                           meses=MESES_STR,
                           preselected_owner_id=preselected_owner_id)


@facturas_bp.route('/add', methods=['POST'])
@role_required('admin', 'gestor')
@with_owner_filtering(require_active_owner=False)
def add_factura():
    """Crea una nueva factura manual."""
    iva_rate, irpf_rate = get_rates()
    try:
        # Recoger datos del formulario
        tenant_id_str = request.form.get('invoiceTenant')
        prop_id_str = request.form.get('invoiceProperty')
        fecha_str = request.form.get('invoiceDate')
        items_json_str = request.form.get('itemsJson', '[]') # Recoge el JSON del input hidden
        contrato_id_str = request.form.get('invoiceContract') # Puede ser None
        notas = request.form.get('invoiceNotes', '').strip() or None

        # Validaciones básicas
        if not all([tenant_id_str, prop_id_str, fecha_str]):
            flash("Inquilino, Propiedad y Fecha son obligatorios.", 'warning')
            return redirect(url_for('facturas_bp.listar_facturas'))

        # Conversiones y validación de tipos
        try:
            tenant_id = int(tenant_id_str)
            prop_id = int(prop_id_str)
            contrato_id = int(contrato_id_str) if contrato_id_str else None
            fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
            items = json.loads(items_json_str) # Parsear items
            if not isinstance(items, list) or not items:
                raise ValueError("Debe añadir al menos un concepto válido.")
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            flash(f"Error en los datos del formulario: {e}", 'danger')
            return redirect(url_for('facturas_bp.listar_facturas'))

        # Validar y calcular items
        subtotal = Decimal('0.00')
        validated_items = [] # Guardaremos los items validados aquí para el JSON final
        for i, item_data in enumerate(items):
            if not isinstance(item_data, dict) or not all(k in item_data for k in ['quantity', 'unitPrice', 'description']):
                raise ValueError(f"Formato inválido en concepto #{i+1}.")
            try:
                desc = str(item_data['description']).strip()
                qty = Decimal(str(item_data['quantity']))
                up = Decimal(str(item_data['unitPrice']))
                if not desc: raise ValueError(f"Descripción vacía en concepto #{i+1}.")
                if qty <= 0: raise ValueError(f"Cantidad debe ser positiva en concepto #{i+1}.")
                if up < 0: raise ValueError(f"Precio unitario no puede ser negativo en concepto #{i+1}.")
            except (InvalidOperation, KeyError, ValueError, TypeError) as e_val:
                raise ValueError(f"Valores inválidos en concepto #{i+1}: {e_val}")

            line_total = (qty * up).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            subtotal += line_total
            # Guardar como float en el JSON para compatibilidad, pero usar Decimal para cálculos
            validated_items.append({"description": desc, "quantity": float(qty), "unitPrice": float(up), "total": float(line_total)})

        # Calcular impuestos y total
        subtotal = subtotal.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        iva_amt = (subtotal * iva_rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        irpf_amt = (subtotal * irpf_rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        total = (subtotal + iva_amt - irpf_amt).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        # Generar número de factura manual
        prefix = f"MAN-{prop_id}-{tenant_id}-{fecha.strftime('%Y%m%d')}-"
        last_num_str = db.session.query(func.max(Factura.numero_factura)).filter(Factura.numero_factura.like(f"{prefix}%")).scalar()
        seq = 1
        if last_num_str:
            try: seq = int(last_num_str.rsplit('-', 1)[-1]) + 1
            except (ValueError, IndexError): pass
        numero = f"{prefix}{seq:03d}"

        # Crear y guardar factura
        inv = Factura(
            numero_factura=numero, contrato_id=contrato_id, inquilino_id=tenant_id,
            propiedad_id=prop_id, fecha_emision=fecha, subtotal=subtotal,
            iva=iva_amt, irpf=irpf_amt, total=total, estado='pendiente',
            items_json=json.dumps(validated_items, ensure_ascii=False), notas=notas
        )
        db.session.add(inv)
        db.session.commit()
        flash(f'Factura manual {numero} creada correctamente.', 'success')

    except ValueError as ve: # Captura errores de validación de items
        db.session.rollback()
        flash(f"Error en los datos: {ve}", 'danger')
    except IntegrityError: # Captura error de número de factura duplicado
        db.session.rollback()
        flash('Error: Posible número de factura duplicado.', 'danger')
    except Exception as e: # Captura otros errores inesperados
        db.session.rollback()
        flash(f"Error inesperado al crear factura manual: {e}", 'danger')
        current_app.logger.error("Error en POST /facturas/add", exc_info=True)

    return redirect(url_for('facturas_bp.listar_facturas'))


@facturas_bp.route('/mark_as_paid/<int:id>', methods=['POST'])
def mark_as_paid(id):
    """Marca una factura como pagada."""
    inv = Factura.query.get_or_404(id)
    if inv.estado == 'pagada':
        flash('La factura ya estaba marcada como pagada.', 'info')
    elif inv.estado == 'cancelada':
        flash('No se puede marcar como pagada una factura cancelada.', 'warning')
    else:
        try:
            inv.estado = 'pagada'
            db.session.commit()
            flash(f'Factura {inv.numero_factura} marcada como pagada.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f"Error al marcar la factura como pagada: {e}", 'danger')
            current_app.logger.error(f"Error en POST /mark_as_paid/{id}", exc_info=True)
    return redirect(url_for('facturas_bp.listar_facturas'))

@facturas_bp.route('/delete/<int:id>', methods=['POST'])
@role_required('admin', 'gestor')
@validate_entity_access('factura', 'id')
def delete_factura(id):
    inv = db.session.query(Factura).options(
        db.joinedload(Factura.contrato_ref),
        db.joinedload(Factura.actualizacion_renta_origen)
    ).get(id)

    if not inv: flash("Factura no encontrada.", "warning"); return redirect(url_for('facturas_bp.listar_facturas'))
    
    can_delete = current_user.role == 'admin' or (inv.propiedad_ref and inv.propiedad_ref.propietario_id in {p.id for p in current_user.propietarios_asignados})
    if not can_delete: flash("No tienes permiso para eliminar esta factura.", "danger"); return redirect(url_for('facturas_bp.listar_facturas'))

    numero_factura_visible_borrada = inv.numero_factura_mostrado_al_cliente
    contrato_asociado = inv.contrato_ref
    renta_revertida_info = None; gastos_reseteados_count = 0

    try:
        if hasattr(inv, 'gastos_incluidos') and inv.gastos_incluidos:
            for gasto in inv.gastos_incluidos: gasto.estado = 'Pendiente'; gasto.integrated = False; gasto.factura_id = None; db.session.add(gasto); gastos_reseteados_count += 1
        
        historial_asociado = inv.actualizacion_renta_origen
        if historial_asociado and contrato_asociado:
            ultima_act_contrato = HistorialActualizacionRenta.query.filter_by(contrato_id=contrato_asociado.id).order_by(HistorialActualizacionRenta.fecha_actualizacion.desc(), HistorialActualizacionRenta.id.desc()).first()
            if ultima_act_contrato and ultima_act_contrato.id == historial_asociado.id:
                renta_revertida_info = {'anterior': historial_asociado.renta_anterior, 'nueva': historial_asociado.renta_nueva}
                contrato_asociado.precio_mensual = historial_asociado.renta_anterior
                db.session.add(contrato_asociado); db.session.delete(historial_asociado)
        
        if contrato_asociado and contrato_asociado.serie_facturacion_prefijo and inv.fecha_emision and contrato_asociado.serie_facturacion_ano_actual == inv.fecha_emision.year:
            try:
                partes_num_vis = inv.numero_factura_mostrado_al_cliente.split('-')
                sec_borrado_int = int(partes_num_vis[-1])
                if contrato_asociado.serie_facturacion_ultimo_numero == sec_borrado_int:
                    contrato_asociado.serie_facturacion_ultimo_numero = max(0, sec_borrado_int - 1)
                    db.session.add(contrato_asociado)
            except: pass # Ignorar errores de parseo de serie aquí

        db.session.delete(inv)
        db.session.commit() 
        flash_msg = f'Factura {numero_factura_visible_borrada} eliminada.'
        if gastos_reseteados_count > 0: flash_msg += f" {gastos_reseteados_count} gasto(s) reseteados."
        if renta_revertida_info: flash_msg += f" Renta del contrato revertida de {renta_revertida_info['nueva']:.2f}€ a {renta_revertida_info['anterior']:.2f}€."
        flash(flash_msg, 'success')
    except Exception as e:
        db.session.rollback(); flash(f"Error eliminando factura {numero_factura_visible_borrada}: {e}", 'danger')
        current_app.logger.error(f"Error eliminando factura ID {id}: {e}", exc_info=True)
    return redirect(url_for('facturas_bp.listar_facturas'))


@facturas_bp.route('/ver/<int:id>', methods=['GET'])
@login_required
@filtered_detail_view('factura', 'id', log_queries=True)
def ver_factura(id):
    """Muestra la página de detalle de una factura con validación automática de acceso."""
    csrf_form = CSRFOnlyForm()  # Para el botón de email en el template
    inv = None
    propietario_emisor = None
    # Usar tasas por defecto del modelo o configuración inicial
    iva_rate = DEFAULT_IVA_RATE
    irpf_rate = DEFAULT_IRPF_RATE

    try:
        # La validación de acceso ya se hizo automáticamente
        inv = OwnerFilteredQueries.get_factura_by_id(id, include_relations=True)
        if not inv:
            abort(404)
            if inv.propiedad_ref.propietario_id not in assigned_owner_ids:
                 flash("No tienes permiso para ver esta factura.", "danger")
                 current_app.logger.warning(f"Acceso denegado a factura {inv.id} para usuario {current_user.username}. Propietario no asignado.")
                 return redirect(url_for('facturas_bp.listar_facturas'))
        # --- FIN VERIFICACIÓN ---

        # 3. Si todo está OK, obtener datos adicionales
        propietario_emisor = inv.propiedad_ref.propietario_ref if inv.propiedad_ref else None

        # 4. Obtener y calcular tasas IVA/IRPF usadas en la factura
        # Usar get_rates() que ya considera los settings
        iva_rate_config, irpf_rate_config = get_rates()
        calculated_iva_rate = iva_rate_config   # Tasa por defecto de la configuración
        calculated_irpf_rate = irpf_rate_config # Tasa por defecto de la configuración

        try:
            # Intentar recalcular las tasas basadas en los valores guardados en la factura
            # Esto es útil si las tasas globales cambiaron después de emitir la factura
            if inv.subtotal is not None and inv.iva is not None and inv.subtotal != Decimal('0.00'):
                 calculated_iva_rate = (inv.iva / inv.subtotal)
            if inv.subtotal is not None and inv.irpf is not None and inv.subtotal != Decimal('0.00'):
                 calculated_irpf_rate = (inv.irpf / inv.subtotal)
        except (TypeError, InvalidOperation, ZeroDivisionError):
             current_app.logger.warning(f"No se pudieron recalcular tasas para factura {inv.id}, usando defaults de config.")
             pass # Mantener tasas de configuración si hay error en cálculo o datos faltantes

        # Usar las tasas calculadas (o las de config si el cálculo falló) para pasar al template
        iva_rate = calculated_iva_rate
        irpf_rate = calculated_irpf_rate

    # Flask maneja el 404 de get_or_404 automáticamente.
    # Capturar otras excepciones que puedan ocurrir.
    except Exception as e:
        # El error 404 ya habrá sido manejado, esto es para otros errores
        if not isinstance(e, werkzeug.exceptions.NotFound): # No flashear si es un 404 estándar
            flash(f"Error al cargar el detalle de la factura: {e}", 'danger')
            current_app.logger.error(f"Error en GET /facturas/ver/{id}: {e}", exc_info=True)
        # Asegurarse que 'inv' es None si hay error para que el template muestre el bloque 'else'
        inv = None
        # No redirigir aquí, dejar que el template maneje 'invoice=None'


    return render_template(
        'ver_factura.html',
        title=f"Factura {inv.numero_factura}" if inv else "Factura No Encontrada",
        invoice=inv, # Puede ser None si no se encontró o hubo un error (excepto 404)
        propietario=propietario_emisor,
        iva_rate=iva_rate,
        irpf_rate=irpf_rate,
        csrf_form=csrf_form
    )


@facturas_bp.route('/download/<int:id>', methods=['GET'])
def download_factura(id):
    """Genera y descarga el PDF de una factura."""
    invoice = Factura.query.get_or_404(id)
    try:
        current_app.logger.info(f"Iniciando generación PDF para Factura ID: {id}")
        pdf_buffer = generate_invoice_pdf(id) # Llama a la función importada (v1 o v2)

        if pdf_buffer:
            filename = f"factura_{invoice.numero_factura_mostrado_al_cliente.replace('/', '-')}.pdf"
            current_app.logger.info(f"Enviando PDF generado: {filename}")
            return send_file(
                pdf_buffer,
                mimetype='application/pdf',
                as_attachment=True,
                download_name=filename
            )
        else:
            flash("Error interno al generar el PDF de la factura.", 'danger')
            current_app.logger.error(f"generate_invoice_pdf devolvió None para Factura ID: {id}")
            return redirect(url_for('facturas_bp.ver_factura', id=id))

    except Exception as e:
        current_app.logger.error(f"Error inesperado en download_factura {id}: {e}", exc_info=True)
        flash("Ocurrió un error inesperado al procesar la descarga del PDF.", 'danger')
        # Considerar abort(500) si es un error grave irrecuperable
        return redirect(url_for('facturas_bp.ver_factura', id=id))

@facturas_bp.route('/edit/<int:id>', methods=['POST'])
@role_required('admin', 'gestor')
@validate_entity_access('factura', 'id')
def edit_factura(id):
    # La validación de acceso ya se hizo automáticamente
    inv = OwnerFilteredQueries.get_factura_by_id(id, include_relations=True)
    if not inv:
        flash('Factura no encontrada o sin acceso.', 'warning')
        return redirect(url_for('facturas_bp.listar_facturas'))

    if inv.estado == 'cancelada':
        flash('Las facturas canceladas no se pueden editar.', 'warning')
        return redirect(url_for('facturas_bp.listar_facturas'))

    try:
        items_json_str = request.form.get('editItemsJson', '[]') # Input oculto donde JS pone el JSON
        notas = request.form.get('editInvoiceNotes', '').strip() or None
        
        current_app.logger.info(f"Editando Factura ID {id}. Items JSON recibidos: {items_json_str[:200]}...")
        current_app.logger.info(f"Notas recibidas: {notas}")

        items = json.loads(items_json_str) # Parsear el JSON de items
        
        # ESTA ES LA VALIDACIÓN QUE LANZA EL ERROR SI 'items' ES UNA LISTA VACÍA
        if not isinstance(items, list) or not items: 
             raise ValueError("La factura debe tener al menos un concepto válido.")

        subtotal_calculado_desde_items = Decimal('0.00')
        validated_items = []
        for i, item_data in enumerate(items):
            if not isinstance(item_data, dict) or not all(k in item_data for k in ['quantity', 'unitPrice', 'description']):
                raise ValueError(f"Formato inválido en concepto #{i+1} recibido del formulario.")
            try:
                desc = str(item_data['description']).strip()
                qty = Decimal(str(item_data['quantity']))
                up = Decimal(str(item_data['unitPrice']))

                if not desc: raise ValueError(f"Descripción vacía en concepto #{i+1}.")
                if qty <= 0: raise ValueError(f"Cantidad debe ser positiva en concepto #{i+1}.")
                if up < 0: raise ValueError(f"Precio unitario no puede ser negativo en concepto #{i+1}.")
            except (InvalidOperation, KeyError, ValueError, TypeError) as e_val_item:
                raise ValueError(f"Valores inválidos en concepto #{i+1}: {e_val_item}")

            line_total = (qty * up).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            subtotal_calculado_desde_items += line_total
            validated_items.append({
                "description": desc, "quantity": float(qty), 
                "unitPrice": float(up), "total": float(line_total)
            })
        
        tasa_iva_a_usar = inv.iva_rate_applied
        if tasa_iva_a_usar is None:
            tasa_iva_a_usar = getattr(g, 'settings', SystemSettings()).iva_rate if (inv.contrato_ref and inv.contrato_ref.aplicar_iva) else Decimal('0.00')
        
        tasa_irpf_a_usar = inv.irpf_rate_applied
        if tasa_irpf_a_usar is None:
            tasa_irpf_a_usar = getattr(g, 'settings', SystemSettings()).irpf_rate if (inv.contrato_ref and inv.contrato_ref.aplicar_irpf) else Decimal('0.00')

        if not isinstance(tasa_iva_a_usar, Decimal): tasa_iva_a_usar = Decimal(str(tasa_iva_a_usar))
        if not isinstance(tasa_irpf_a_usar, Decimal): tasa_irpf_a_usar = Decimal(str(tasa_irpf_a_usar))

        inv.subtotal = subtotal_calculado_desde_items.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        inv.iva = (inv.subtotal * tasa_iva_a_usar).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        inv.irpf = (inv.subtotal * tasa_irpf_a_usar).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        inv.total = (inv.subtotal + inv.iva - inv.irpf).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        
        inv.items_json = json.dumps(validated_items, ensure_ascii=False)
        inv.notas = notas

        db.session.commit()
        flash(f'Factura {inv.numero_factura} actualizada correctamente.', 'success')

    except (ValueError, json.JSONDecodeError) as ve:
        db.session.rollback()
        flash(f"Error en los datos al editar la factura: {ve}", 'danger') # El mensaje de error vendrá de aquí
        current_app.logger.warning(f"Error de validación/JSON editando factura {id}: {ve}")
    except Exception as e:
        db.session.rollback()
        flash(f"Error inesperado al editar la factura: {e}", 'danger')
        current_app.logger.error(f"Error en POST /facturas/edit/{id}: {e}", exc_info=True)

    return redirect(url_for('facturas_bp.listar_facturas'))


# --- Rutas CRUD Gastos ---
@facturas_bp.route('/gastos', methods=['GET', 'POST'])
@login_required # Proteger toda la ruta
def gestionar_gastos():
    csrf_form = CSRFOnlyForm()
    
    if request.method == 'POST':
        contrato_id_str = request.form.get('contractSelect')
        concepto = request.form.get('expenseConcepto')
        importe_str = request.form.get('expenseImporte')
        month_str = request.form.get('expenseMonth')
        year_str = request.form.get('expenseYear')
        files = request.files.getlist('expenseFiles')

        if not all([contrato_id_str, concepto, importe_str]):
            flash("Contrato, Concepto e Importe son obligatorios.", "warning")
            return redirect(url_for('facturas_bp.gestionar_gastos'))
        if not contrato_id_str.isdigit():
             flash("Debe seleccionar un contrato válido.", "warning")
             return redirect(url_for('facturas_bp.gestionar_gastos'))
        if not files or all(f.filename == '' for f in files):
             flash("Debe adjuntar al menos un archivo justificante.", "warning")
             return redirect(url_for('facturas_bp.gestionar_gastos'))

        try:
            contrato_id = int(contrato_id_str)
            contrato_obj = db.session.get(Contrato, contrato_id) # Usar el objeto contrato
            if not contrato_obj:
                raise ValueError(f"El contrato seleccionado (ID: {contrato_id}) no existe.")
            if not contrato_obj.propiedad_ref or not contrato_obj.propiedad_ref.propietario_ref:
                raise ValueError(f"El contrato (ID: {contrato_id}) no tiene un propietario asociado correctamente.")

            propietario_del_gasto = contrato_obj.propiedad_ref.propietario_ref

            if current_user.role != 'admin':
                assigned_owner_ids = {p.id for p in current_user.propietarios_asignados}
                if propietario_del_gasto.id not in assigned_owner_ids:
                    flash("No tienes permiso para añadir gastos a este contrato/propietario.", "danger")
                    return redirect(url_for('facturas_bp.gestionar_gastos'))

            importe_input = importe_str.strip().replace(',', '.')
            importe_dec = Decimal(importe_input)
            if importe_dec <= 0: raise ValueError("El importe debe ser un valor positivo.")
            
            month_gasto = int(month_str) if month_str and month_str.isdigit() else None
            year_gasto = int(year_str) if year_str and year_str.isdigit() else None
            
            if month_gasto and not (1 <= month_gasto <= 12): raise ValueError("Mes del gasto inválido (1-12).")
            if year_gasto and not (1900 < year_gasto < 2200): raise ValueError("Año del gasto inválido.")

        except (ValueError, InvalidOperation) as ve:
            flash(f"Error en los datos del gasto: {ve}", 'danger')
            return redirect(url_for('facturas_bp.gestionar_gastos'))
        except Exception as e_val:
            flash(f"Error procesando datos del formulario de gasto: {e_val}", 'danger')
            current_app.logger.error(f"Error validación datos gasto POST: {e_val}", exc_info=True)
            return redirect(url_for('facturas_bp.gestionar_gastos'))

        gastos_guardados_count = 0
        errores_archivos = []
        db_objects_to_add = []

        for file_item in files: # Cambiado 'file' a 'file_item' para evitar conflicto con la función allowed_file
            if file_item and file_item.filename and allowed_expense_file(file_item.filename):
                original_filename_user = file_item.filename
                
                fecha_creacion_str = datetime.now().strftime("%Y%m%d-%H%M%S")
                concepto_saneado = "".join(c if c.isalnum() or c in (' ', '-') else '_' for c in concepto.strip()[:50])
                ext_gasto = original_filename_user.rsplit('.', 1)[1].lower() if '.' in original_filename_user else ''
                
                # Nombre para guardar en disco: "YYYYMMDD-HHMMSS - SaneadoConcepto.ext"
                nombre_archivo_gasto_en_disco = f"{fecha_creacion_str} - {concepto_saneado}.{ext_gasto}" if ext_gasto else f"{fecha_creacion_str} - {concepto_saneado}"
                
                # Nombre para la BD (único con UUID)
                nombre_archivo_gasto_para_bd = f"gasto_{uuid.uuid4().hex}.{ext_gasto}" if ext_gasto else f"gasto_{uuid.uuid4().hex}"

                # El año para la subcarpeta de gastos. Usar el del gasto si se especifica, sino el año actual.
                year_subfolder_gasto = year_gasto if year_gasto else datetime.now().year
                
                full_file_path_on_disk = get_owner_document_path(
                    propietario=propietario_del_gasto,
                    subfolder_type="Facturas Gastos",
                    year=year_subfolder_gasto, 
                    filename_to_secure=nombre_archivo_gasto_en_disco # Usar el nombre descriptivo para el disco
                )

                if not full_file_path_on_disk:
                    errores_archivos.append(f"No se pudo determinar la ruta para guardar '{original_filename_user}'.")
                    continue

                try:
                    file_item.save(full_file_path_on_disk)
                    gasto_obj = Gasto(
                        contrato_id=contrato_id, concepto=concepto.strip()[:255],
                        importe=importe_dec, month=month_gasto, year=year_gasto,
                        filename=nombre_archivo_gasto_para_bd, # Guardar el nombre único en BD
                        original_filename=secure_filename(original_filename_user), # Guardar el original (asegurado) en BD
                        estado='Pendiente'
                    )
                    db_objects_to_add.append(gasto_obj)
                    gastos_guardados_count += 1
                except Exception as e_save:
                    err_msg = f"Error guardando archivo '{original_filename_user}': {e_save}"
                    errores_archivos.append(err_msg)
                    current_app.logger.error(f"{err_msg}\n{traceback.format_exc()}")
                    if os.path.exists(full_file_path_on_disk):
                        try: os.remove(full_file_path_on_disk)
                        except OSError: pass
            elif file_item and file_item.filename:
                errores_archivos.append(f"Archivo '{file_item.filename}' ignorado (tipo no permitido).")

        if db_objects_to_add:
            try:
                db.session.add_all(db_objects_to_add)
                db.session.commit()
                flash(f"{gastos_guardados_count} gasto(s) registrado(s) correctamente para el contrato {contrato_obj.numero_contrato}.", "success")
            except Exception as e_db:
                db.session.rollback()
                flash(f"Error al guardar gastos en la base de datos: {e_db}", "danger")
                current_app.logger.error(f"Error en commit de gastos: {e_db}\n{traceback.format_exc()}")
        elif not errores_archivos:
             flash("No se seleccionaron archivos válidos para registrar como gastos.", "warning")

        if errores_archivos:
            flash("Problemas al procesar algunos archivos:<br>" + "<br>".join(errores_archivos), "warning")
        
        return redirect(url_for('facturas_bp.gestionar_gastos'))

    # --- LÓGICA GET ---
    # ... (sin cambios respecto a tu versión anterior, ya es robusta) ...
    propietarios = []
    contratos_todos = []
    gastos = []
    try:
        if current_user.role == 'admin':
            propietarios = db.session.query(Propietario).order_by(Propietario.nombre).all()
            contratos_todos = db.session.query(Contrato).options(
                joinedload(Contrato.propiedad_ref).joinedload(Propiedad.propietario_ref),
                joinedload(Contrato.inquilino_ref)
            ).order_by(Contrato.numero_contrato).all()
        elif current_user.role in ['gestor', 'usuario']:
            propietarios = list(current_user.propietarios_asignados) # Convertir a lista real
            assigned_owner_ids = [p.id for p in propietarios]
            if assigned_owner_ids:
                contratos_todos = db.session.query(Contrato).join(Propiedad).filter(
                    Propiedad.propietario_id.in_(assigned_owner_ids)
                ).options(
                    joinedload(Contrato.propiedad_ref).joinedload(Propiedad.propietario_ref),
                    joinedload(Contrato.inquilino_ref)
                ).order_by(Contrato.numero_contrato).all()
        query_gastos = db.session.query(Gasto).options(
            joinedload(Gasto.contrato).joinedload(Contrato.propiedad_ref),
            joinedload(Gasto.factura)
        )
        if current_user.role != 'admin':
            if 'assigned_owner_ids' not in locals():
                 assigned_owner_ids = [p.id for p in current_user.propietarios_asignados]
            if not assigned_owner_ids:
                query_gastos = query_gastos.filter(Gasto.id == -1)
            else:
                query_gastos = query_gastos.join(Gasto.contrato).join(Contrato.propiedad_ref).filter(
                    Propiedad.propietario_id.in_(assigned_owner_ids)
                )
        gastos = query_gastos.order_by(
            Gasto.estado.asc(), Gasto.upload_date.desc(),
            Gasto.year.desc().nullslast(), Gasto.month.desc().nullslast()
        ).all()
    except Exception as e_get:
        flash(f"Error al cargar los datos de gastos: {e_get}", 'danger')
        current_app.logger.error(f"Error en GET /gastos: {e_get}", exc_info=True)
        propietarios, contratos_todos, gastos = [], [], []
    # Incluir propietario activo si existe
    active_owner_context = get_active_owner_context()
    preselected_owner_id = None
    
    if active_owner_context and active_owner_context.get('active_owner'):
        active_owner = active_owner_context['active_owner']
        preselected_owner_id = active_owner.id
        
        # Asegurar que el propietario activo esté en la lista de propietarios
        # Si no está, agregarlo (para casos donde admin selecciona propietario no asignado)
        propietarios_ids = [p.id for p in propietarios]
        if preselected_owner_id not in propietarios_ids:
            propietarios.append(active_owner)
    
    return render_template(
        'gastos.html', title="Gestión de Gastos",
        propietarios=propietarios, contratos=contratos_todos,
        gastos=gastos, csrf_form=csrf_form,
        preselected_owner_id=preselected_owner_id
    )


@facturas_bp.route('/gastos/edit/<int:id>', methods=['POST'])
# @owner_access_required() # Necesitaría adaptar el decorador para gastos
@role_required('admin', 'gestor') # Solo admin/gestor pueden editar gastos
def edit_gasto(id):
    gasto = db.session.get(Gasto, id) # Usar get es más eficiente que query().get()
    if not gasto: abort(404)

    # *** Añadir verificación de permiso explícita ***
    if current_user.role != 'admin':
        assigned_owner_ids = {p.id for p in current_user.propietarios_asignados}
        # Comprobar propietario del contrato original del gasto
        if not gasto.contrato or not gasto.contrato.propiedad_ref or gasto.contrato.propiedad_ref.propietario_id not in assigned_owner_ids:
             flash("No tienes permiso para editar este gasto.", "danger")
             return redirect(url_for('facturas_bp.gestionar_gastos'))
    # *** Fin Verificación ***

    if gasto.estado == 'Facturado':
         flash(f"No se puede editar el gasto '{gasto.concepto}' porque ya está facturado.", "warning")
         return redirect(url_for('facturas_bp.gestionar_gastos'))

    # Recoger datos del formulario
    contrato_id_str = request.form.get('editContractSelect')
    concepto = request.form.get('editExpenseConcepto')
    importe_str = request.form.get('editExpenseImporte')
    month_str = request.form.get('editExpenseMonth')
    year_str = request.form.get('editExpenseYear')

    # Validaciones
    if not all([contrato_id_str, concepto, importe_str]):
        flash("Contrato, Concepto e Importe son obligatorios al editar.", "warning")
        return redirect(url_for('facturas_bp.gestionar_gastos'))

    try:
        new_contrato_id = int(contrato_id_str)
        new_contrato = db.session.get(Contrato, new_contrato_id)
        if not new_contrato: raise ValueError("El nuevo contrato seleccionado no existe.")

        # *** VERIFICACIÓN PERMISO GESTOR (si cambia de contrato) ***
        if current_user.role != 'admin':
             assigned_owner_ids = {p.id for p in current_user.propietarios_asignados}
             if not new_contrato.propiedad_ref or new_contrato.propiedad_ref.propietario_id not in assigned_owner_ids:
                  flash("No tienes permiso para asignar este gasto a ese contrato.", "danger")
                  return redirect(url_for('facturas_bp.gestionar_gastos'))
        # *** FIN VERIFICACIÓN ***

        importe_input = importe_str.strip().replace(',', '.')
        importe_dec = Decimal(importe_input)
        if importe_dec <= 0: raise ValueError("El importe debe ser positivo.")
        month = int(month_str) if month_str else None
        year = int(year_str) if year_str else None
        if month and not (1 <= month <= 12): raise ValueError("Mes inválido.")
        if year and not (1900 < year < 2200): raise ValueError("Año inválido.")

        # Actualizar objeto Gasto
        gasto.contrato_id = new_contrato_id
        gasto.concepto = concepto.strip()[:255]
        gasto.importe = importe_dec
        gasto.month = month
        gasto.year = year

        db.session.commit()
        flash(f"Gasto '{gasto.concepto}' actualizado correctamente.", "success")

    except (ValueError, InvalidOperation) as ve:
        db.session.rollback()
        flash(f"Error en los datos al editar gasto: {ve}", 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f"Error inesperado al editar gasto: {e}", 'danger')
        current_app.logger.error(f"Error en POST /gastos/edit/{id}", exc_info=True)

    return redirect(url_for('facturas_bp.gestionar_gastos'))


@facturas_bp.route('/gastos/delete/<int:id>', methods=['POST'])
# @owner_access_required() # Adaptar o usar verificación explícita
@role_required('admin', 'gestor')
def delete_gasto(id):
    gasto = Gasto.query.options(joinedload(Gasto.contrato).joinedload(Contrato.propiedad_ref)).get_or_404(id)

    # *** Añadir verificación de permiso explícita ***
    if current_user.role != 'admin':
        assigned_owner_ids = {p.id for p in current_user.propietarios_asignados}
        if not gasto.contrato or not gasto.contrato.propiedad_ref or gasto.contrato.propiedad_ref.propietario_id not in assigned_owner_ids:
             flash("No tienes permiso para eliminar este gasto.", "danger")
             return redirect(url_for('facturas_bp.gestionar_gastos'))
    # *** Fin Verificación ***

    if gasto.estado == 'Facturado':
        flash(f"No se puede eliminar gasto '{gasto.concepto}' porque ya está facturado.", "warning")
        return redirect(url_for('facturas_bp.gestionar_gastos'))

    filename_to_delete = gasto.filename
    concepto_borrado = gasto.concepto
    upload_folder_expenses = current_app.config.get('UPLOAD_FOLDER_EXPENSES')

    try:
        # Eliminar archivo físico
        file_deleted = False
        if upload_folder_expenses and filename_to_delete:
            file_path = os.path.join(upload_folder_expenses, secure_filename(filename_to_delete)) # Usar secure_filename
            if os.path.exists(file_path):
                try:
                    os.remove(file_path); file_deleted = True
                    current_app.logger.info(f"Archivo gasto eliminado: {file_path}")
                except OSError as e_file:
                    current_app.logger.error(f"Error eliminando archivo físico {file_path}: {e_file}")
                    flash(f"Error al eliminar archivo '{filename_to_delete}'.", "warning")
            else:
                current_app.logger.warning(f"Archivo no encontrado para eliminar: {file_path}")

        # Eliminar registro de la BD
        db.session.delete(gasto)
        db.session.commit()
        flash(f"Gasto '{concepto_borrado}' eliminado" + (" y su archivo." if file_deleted else "."), "success")

    except Exception as e:
        db.session.rollback()
        flash(f"Error al eliminar el gasto: {e}", 'danger')
        current_app.logger.error(f"Error eliminando gasto {id}", exc_info=True)

    return redirect(url_for('facturas_bp.gestionar_gastos'))


@facturas_bp.route('/gastos/download/<path:filename>')
@login_required # Ya cubierto por before_request
def download_expense(filename):
    try:
        # 'filename' aquí es el nombre único almacenado en Gasto.filename (ej. gasto_uuid.ext)
        gasto = Gasto.query.filter_by(filename=filename).options(
            joinedload(Gasto.contrato).joinedload(Contrato.propiedad_ref).joinedload(Propiedad.propietario_ref)
        ).first()
        
        if not gasto or not gasto.contrato or not gasto.contrato.propiedad_ref or not gasto.contrato.propiedad_ref.propietario_ref:
            current_app.logger.warning(f"download_expense: Gasto o propietario no encontrado para filename {filename}")
            abort(404)

        propietario_del_gasto = gasto.contrato.propiedad_ref.propietario_ref

        if current_user.role != 'admin':
             assigned_owner_ids = {p.id for p in current_user.propietarios_asignados}
             if propietario_del_gasto.id not in assigned_owner_ids:
                 flash("No tienes permiso para descargar este archivo de gasto.", "danger")
                 abort(403)

        year_subfolder_gasto = gasto.year if gasto.year else gasto.upload_date.year
        
        # Obtener la CARPETA donde se guardó el archivo
        # El `filename_to_secure` aquí es el nombre con el que se guardó en disco
        # que reconstruimos o recuperamos si es diferente al Gasto.filename
        
        # Reconstruir el nombre con el que se guardó en disco: "fecha - concepto.ext"
        fecha_creacion_gasto_str = gasto.upload_date.strftime("%Y%m%d-%H%M%S") # Usar upload_date para consistencia
        concepto_saneado_gasto = "".join(c if c.isalnum() or c in (' ', '-') else '_' for c in gasto.concepto.strip()[:50])
        ext_gasto_original = ''
        if gasto.original_filename and '.' in gasto.original_filename: # Usar extensión del original_filename
            ext_gasto_original = gasto.original_filename.rsplit('.', 1)[1].lower()
        
        nombre_archivo_real_en_disco = f"{fecha_creacion_gasto_str} - {concepto_saneado_gasto}.{ext_gasto_original}" if ext_gasto_original else f"{fecha_creacion_gasto_str} - {concepto_saneado_gasto}"
        
        gasto_folder_path = get_owner_document_path(
            propietario=propietario_del_gasto,
            subfolder_type="Facturas Gastos",
            year=year_subfolder_gasto
            # No pasamos filename_to_secure aquí si solo queremos la carpeta
        )

        if not gasto_folder_path:
            current_app.logger.error(f"No se pudo determinar la carpeta para el gasto con DB filename {filename} del prop. {propietario_del_gasto.id}")
            abort(404)
        
        # Usamos el nombre_archivo_real_en_disco para buscarlo, y gasto.original_filename para la descarga
        # secure_filename se aplica al nombre real en disco por si acaso.
        return send_from_directory(gasto_folder_path, 
                                   secure_filename(nombre_archivo_real_en_disco), # El nombre tal como está en el disco
                                   as_attachment=True, 
                                   download_name=gasto.original_filename or secure_filename(nombre_archivo_real_en_disco))

    except FileNotFoundError:
        flash(f'Archivo de gasto "{filename}" (o su nombre en disco) no encontrado.', 'warning')
        current_app.logger.warning(f"FileNotFoundError para Gasto.filename: {filename}, intentando con nombre en disco: {nombre_archivo_real_en_disco if 'nombre_archivo_real_en_disco' in locals() else 'No calculado'}")
        abort(404)
    except Exception as e:
        flash(f'Error al servir archivo de gasto: {e}', 'danger')
        current_app.logger.error(f"Error sirviendo archivo de gasto (DB filename: {filename}): {e}\n{traceback.format_exc()}")
        abort(500)

def _send_single_invoice_email(invoice_id, include_bcc_owner=True):
    current_app.logger.info(f"--- INICIO _send_single_invoice_email para ID: {invoice_id} ---")
    invoice = Factura.query.options(
        db.joinedload(Factura.inquilino_ref),
        db.joinedload(Factura.propiedad_ref).joinedload(Propiedad.propietario_ref),
        db.joinedload(Factura.contrato_ref),
        db.selectinload(Factura.gastos_incluidos)
    ).get(invoice_id)

    if not invoice: current_app.logger.error(f"Factura ID {invoice_id} no encontrada."); return "PDF_ERROR" # Cambiado de False
    
    inquilino = invoice.inquilino_ref
    propiedad = invoice.propiedad_ref
    propietario_obj = propiedad.propietario_ref if propiedad else None
    # contrato = invoice.contrato_ref # Ya cargado

    if not inquilino: current_app.logger.warning(f"Factura {invoice.id} sin inquilino."); return "NO_TENANT"
    if not inquilino.email: current_app.logger.warning(f"Inquilino {inquilino.id} sin email."); return "NO_EMAIL"

    ine_ipc_link = None
    if invoice.indice_aplicado_info and isinstance(invoice.indice_aplicado_info, dict):
        info_idx = invoice.indice_aplicado_info
        if all(k in info_idx for k in ['month', 'year']) and isinstance(info_idx['month'], int) and 1<=info_idx['month']<=12 and isinstance(info_idx['year'], int) and 1900<info_idx['year']<2200:
            mes_ine, anyo_fin_ine = info_idx['month'], info_idx['year']
            anyo_ini_ine = anyo_fin_ine - 1
            ine_ipc_link = f"https://www.ine.es/varipc/verVariaciones.do?idmesini={mes_ine}&anyoini={anyo_ini_ine}&idmesfin={mes_ine}&anyofin={anyo_fin_ine}&ntipo=1&enviar=Calcular"
    
    settings_obj = getattr(g, 'settings', None) or SystemSettings.query.get(1) or SystemSettings()
    subject = f"Factura Alquiler: {invoice.numero_factura_mostrado_al_cliente} - {propiedad.direccion if propiedad else 'Propiedad'}"
    sender_email = settings_obj.mail_default_sender or current_app.config.get('MAIL_DEFAULT_SENDER')
    sender_name = settings_obj.mail_sender_display_name or current_app.config.get('MAIL_SENDER_DISPLAY_NAME', 'RentalSys')
    if not sender_email: current_app.logger.error("MAIL_DEFAULT_SENDER no configurado."); return "CONFIG_ERROR"
    sender = (sender_name, sender_email) if sender_name else sender_email
    recipients = [inquilino.email]; bcc_list = [propietario_obj.email] if include_bcc_owner and propietario_obj and propietario_obj.email else []
    pdf_filename = f"factura_{invoice.numero_factura_mostrado_al_cliente.replace('/', '-')}.pdf"; pdf_buffer = None

    try:
        pdf_buffer = generate_invoice_pdf(invoice_id)
        if not pdf_buffer: raise RuntimeError("Generador PDF devolvió None.")
        html_body = render_template('email/invoice_email.html', invoice=invoice, inquilino=inquilino, propiedad=propiedad, propietario=propietario_obj, settings=settings_obj, ine_link=ine_ipc_link)
        msg = Message(subject=subject, sender=sender, recipients=recipients, bcc=bcc_list, html=html_body)
        pdf_buffer.seek(0); msg.attach(filename=pdf_filename, content_type='application/pdf', data=pdf_buffer.read())
        
        # Adjuntar Gastos
        expense_folder_base = current_app.config.get('UPLOAD_FOLDER_EXPENSES_REL_FOR_TASKS', 'uploads/expenses') # Necesitarás pasar la ruta base absoluta o construirla
        if invoice.gastos_incluidos:
            for gasto in invoice.gastos_incluidos:
                if gasto.filename and gasto.contrato and gasto.contrato.propiedad_ref and gasto.contrato.propiedad_ref.propietario_ref:
                    # Reconstruir ruta al archivo de gasto
                    year_subfolder_gasto = gasto.year if gasto.year else gasto.upload_date.year
                    fecha_creacion_gasto_str = gasto.upload_date.strftime("%Y%m%d-%H%M%S")
                    concepto_saneado_gasto = "".join(c if c.isalnum() or c in (' ', '-') else '_' for c in gasto.concepto.strip()[:50])
                    ext_gasto_original = gasto.original_filename.rsplit('.', 1)[1].lower() if gasto.original_filename and '.' in gasto.original_filename else ''
                    nombre_archivo_real_en_disco = f"{fecha_creacion_gasto_str} - {concepto_saneado_gasto}.{ext_gasto_original}" if ext_gasto_original else f"{fecha_creacion_gasto_str} - {concepto_saneado_gasto}"

                    gasto_file_path_on_disk = get_owner_document_path(
                        propietario=gasto.contrato.propiedad_ref.propietario_ref,
                        subfolder_type="Facturas Gastos", # O el subfolder_type correcto
                        year=year_subfolder_gasto,
                        filename_to_secure=nombre_archivo_real_en_disco # Este es el nombre en disco
                    )
                    if gasto_file_path_on_disk and os.path.exists(gasto_file_path_on_disk):
                        with open(gasto_file_path_on_disk, 'rb') as fp_gasto:
                            attach_name_gasto = gasto.original_filename or secure_filename(nombre_archivo_real_en_disco)
                            # Determinar content_type para el gasto
                            ext_g = attach_name_gasto.rsplit('.', 1)[-1].lower() if '.' in attach_name_gasto else ''
                            ct_gasto = 'application/octet-stream'
                            if ext_g == 'pdf': ct_gasto = 'application/pdf'
                            elif ext_g in ['png', 'jpg', 'jpeg', 'gif']: ct_gasto = f'image/{ext_g}'
                            msg.attach(filename=attach_name_gasto, content_type=ct_gasto, data=fp_gasto.read())
                    else: current_app.logger.warning(f"Archivo de gasto no encontrado en disco: {gasto_file_path_on_disk}")
        
        mail.send(msg); return "SENT"
    except Exception as e_mail: current_app.logger.error(f"EXCEPCIÓN enviando email factura ID {invoice_id}: {e_mail}", exc_info=True); return "SEND_ERROR"
    finally:
        if pdf_buffer: pdf_buffer.close()



# --- RUTA PARA ENVIAR FACTURA POR EMAIL (ACTUALIZADA con enlace IPC) ---
@facturas_bp.route('/send_email/<int:id>', methods=['POST'])
@login_required
def send_invoice_email(id):
    """Envía la factura PDF por email al inquilino con CCO al propietario."""

    # Llamamos a la función auxiliar interna
    success = _send_single_invoice_email(id, include_bcc_owner=True) # Pasamos True para incluir CCO

    if success:
        # Intentamos obtener el número de factura para el mensaje flash
        invoice_num = db.session.query(Factura.numero_factura).filter_by(id=id).scalar() or f"ID {id}"
        flash(f"Factura {invoice_num} enviada por email. Revisa logs para detalles.", 'success')
    else:
        flash(f"Error crítico al enviar la factura ID {id}. Revisa la configuración de email y los logs.", 'danger')

    # Redirigir siempre a la lista, independientemente del resultado del envío
    return redirect(url_for('facturas_bp.listar_facturas'))                            