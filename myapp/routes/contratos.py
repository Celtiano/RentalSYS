# myapp/routes/contratos.py

import os
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, abort, current_app, send_from_directory
)
from sqlalchemy import or_, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload
from werkzeug.utils import secure_filename
import uuid

# --- IMPORTS AUTH/ROLES ---
from flask_login import login_required, current_user
from ..decorators import (
    role_required, owner_access_required,
    filtered_list_view, filtered_detail_view, with_owner_filtering, validate_entity_access
)
# --------------------------

from ..models import db, Contrato, Propiedad, Inquilino, Documento, Propietario, HistorialActualizacionRenta
from ..utils.file_helpers import get_owner_document_path
from ..utils.database_helpers import (
    get_filtered_contratos, get_filtered_propiedades, get_filtered_inquilinos,
    OwnerFilteredQueries
)
from ..utils.owner_session import get_active_owner_context

from ..forms import CSRFOnlyForm 

contratos_bp = Blueprint('contratos_bp', __name__)

# --- Protección del Blueprint ---
@contratos_bp.before_request
@login_required # Requiere login para todas las rutas
def before_request():
    # Puedes añadir aquí lógica específica de roles si es necesario para todo el blueprint
    pass
# ---------------------------------

ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx', 'xls', 'xlsx'}

def allowed_file(filename):
    """Comprueba si el fichero tiene una extensión permitida."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def _link_contract_documents(contrato_obj, files_selected_by_user):
    linked_docs_db = []
    if not contrato_obj or \
       not contrato_obj.propiedad_ref or \
       not contrato_obj.propiedad_ref.propietario_ref or \
       not files_selected_by_user:
        current_app.logger.warning("_link_contract_documents: Faltan datos de contrato, propietario o archivos seleccionados.")
        return linked_docs_db

    propietario_del_contrato = contrato_obj.propiedad_ref.propietario_ref
    contracts_folder_path = get_owner_document_path(
        propietario=propietario_del_contrato,
        subfolder_type="Contratos", 
        year=None 
    )

    if not contracts_folder_path:
        flash(f'Error crítico: No se pudo determinar la carpeta de destino para los documentos del contrato del propietario {propietario_del_contrato.nombre}. Verifica su ruta base.', 'danger')
        current_app.logger.error(f"No se pudo obtener la carpeta de contratos para propietario {propietario_del_contrato.id}")
        return linked_docs_db

    for file_item_storage in files_selected_by_user:
        if file_item_storage and file_item_storage.filename:
            original_filename_from_user = file_item_storage.filename # Ej: "2016-07-01 Contrato arrendamiento Mos.pdf"
            
            # ========= INICIO DE LA CORRECCIÓN =========
            # Para buscar el archivo en disco, usamos el nombre original tal cual,
            # ya que así es como lo guardaste manualmente.
            # No aplicamos secure_filename al nombre que esperamos encontrar.
            filename_to_check_on_disk = original_filename_from_user 
            
            # El nombre que guardaremos en Documento.filename (para el enlace y borrado futuro)
            # SÍ debe ser seguro y único si fuera necesario (aunque para vincular, el original es más directo)
            # Si quieres que Documento.filename sea el original (pero seguro), o uno único:
            # Opción 1: Usar el original asegurado para Documento.filename
            # db_filename_to_store = secure_filename(original_filename_from_user)
            # if not db_filename_to_store : db_filename_to_store = f"archivo_subido_{uuid.uuid4().hex[:8]}"

            # Opción 2 (MÁS SIMPLE PARA VINCULAR): Usar el mismo nombre que en disco para Documento.filename
            # Esto asume que los nombres de archivo en tu carpeta ya son "razonablemente" seguros
            # o que tu función get_owner_document_path y send_from_directory manejan bien los caracteres.
            # Si `filename_to_check_on_disk` puede tener caracteres problemáticos para una URL,
            # entonces `secure_filename` es bueno para el `Documento.filename`.
            # Por ahora, asumiremos que `filename_to_check_on_disk` es lo que se guardará en `Documento.filename`.
            # Y `original_filename_from_user` se guardará en `Documento.original_filename`.
            
            db_filename_to_store = filename_to_check_on_disk # Este será el nombre en Documento.filename
            # ========= FIN DE LA CORRECCIÓN =========

            if not db_filename_to_store: # Si original_filename_from_user era problemático y secure_filename lo vació (si usaras la Opción 1 arriba)
                flash(f"El nombre de archivo '{original_filename_from_user}' no es válido y fue omitido.", "warning")
                current_app.logger.warning(f"Nombre de archivo '{original_filename_from_user}' no válido.")
                continue

            expected_physical_file_path = os.path.join(contracts_folder_path, filename_to_check_on_disk) # Usa el nombre sin secure_filename

            current_app.logger.info(f"Verificando existencia de: {expected_physical_file_path}")

            if os.path.exists(expected_physical_file_path) and os.path.isfile(expected_physical_file_path):
                existing_doc_for_contract = Documento.query.filter_by(
                    contrato_id=contrato_obj.id,
                    filename=db_filename_to_store # Compara con el nombre que se guardará en BD
                ).first()

                if existing_doc_for_contract:
                    flash(f"El documento '{original_filename_from_user}' ya está vinculado a este contrato.", "info")
                    current_app.logger.info(f"Documento '{db_filename_to_store}' ya vinculado al contrato ID {contrato_obj.id}. Omitiendo.")
                    continue
                try:
                    doc = Documento(
                        filename=db_filename_to_store, # Nombre que se usará para servir/borrar el registro
                        original_filename=original_filename_from_user, # Nombre original para mostrar al usuario
                        contrato_id=contrato_obj.id
                    )
                    linked_docs_db.append(doc)
                    current_app.logger.info(f"Documento '{original_filename_from_user}' (referenciado como '{db_filename_to_store}') VINCULADO al contrato ID {contrato_obj.id}.")
                except Exception as e:
                    flash(f'Error creando registro para el documento "{original_filename_from_user}": {e}', 'danger')
                    current_app.logger.error(f"Error creando objeto Documento para {original_filename_from_user}: {e}", exc_info=True)
            else:
                flash(f"Archivo '{original_filename_from_user}' no encontrado en la ruta esperada: '{contracts_folder_path}'. No se pudo vincular.", "warning")
                current_app.logger.warning(f"Archivo físico '{filename_to_check_on_disk}' no encontrado en '{contracts_folder_path}' para contrato ID {contrato_obj.id}.")
        elif file_item_storage and not file_item_storage.filename:
             current_app.logger.info("Se recibió un FileStorage sin nombre de archivo, omitiendo.")
    return linked_docs_db


def _generate_next_contract_number():
    """
    Genera una sugerencia para el próximo número de contrato basada en:
    "CONTRATO - AÑO_ACTUAL - (ID_DEL_ULTIMO_CONTRATO + 1)"
    Si no hay contratos, usa "CONTRATO - AÑO_ACTUAL - 1".
    """
    try:
        # Obtener el último contrato por ID para asegurar que tomamos el más reciente creado
        last_contract = db.session.query(Contrato.id).order_by(Contrato.id.desc()).first()
        
        next_id_suffix = 1
        if last_contract and last_contract.id:
            next_id_suffix = last_contract.id + 1
            
        current_year = datetime.now().year
        
        # Formatear el número de contrato sugerido
        # Puedes ajustar el formato del ID si quieres ceros a la izquierda, ej: f"{next_id_suffix:03d}"
        suggested_number = f"CONTRATO - {current_year} - {next_id_suffix}"
        
        current_app.logger.info(f"Sugerencia de número de contrato generada: {suggested_number}")
        return suggested_number
    except Exception as e:
        current_app.logger.error(f"Error generando siguiente número de contrato: {e}", exc_info=True)
        # Fallback muy genérico si hay algún error
        return f"CONTRATO-{datetime.now().year}-TEMP"


@contratos_bp.route('/', methods=['GET'])
@filtered_list_view(entity_type='contrato', log_queries=True)
def listar_contratos():
    """Lista contratos filtrados automáticamente por propietario activo."""
    csrf_form = CSRFOnlyForm()
    suggested_contract_number = _generate_next_contract_number()
    por_vencer_count = 0

    try:
        # Filtrado automático aplicado - solo contratos del propietario activo
        contratos_list_display = get_filtered_contratos(
            include_relations=True
        ).order_by(Contrato.fecha_inicio.desc()).all()

        # Propiedades filtradas automáticamente
        propiedades_para_select = get_filtered_propiedades().order_by(Propiedad.direccion).all()
        
        # Inquilinos disponibles para NUEVOS contratos (sin filtrar por contratos existentes)
        from ..utils.database_helpers import get_inquilinos_available_for_new_contracts
        inquilinos_para_select = get_inquilinos_available_for_new_contracts().order_by(Inquilino.nombre).all()
        
        # Propietarios disponibles del contexto automático
        owner_context = get_active_owner_context()
        propietarios_para_filtro_tabla = owner_context.get('available_owners', [])

        # Calcular contratos por vencer
        today = date.today()
        limit_date = today + timedelta(days=30)
        por_vencer_count = sum(
            1 for c in contratos_list_display
            if c.estado == 'activo' and c.fecha_fin is not None and isinstance(c.fecha_fin, date) and today < c.fecha_fin <= limit_date
        )

    except Exception as e:
        flash(f'Error cargando datos de contratos: {e}', 'danger')
        current_app.logger.error(f"Error en GET /contratos: {e}", exc_info=True)
        contratos_list_display, propiedades_para_select, inquilinos_para_select, propietarios_para_filtro_tabla = [], [], [], []
        por_vencer_count = 0

    return render_template(
        'contratos.html',
        title='Contratos',
        contratos=contratos_list_display,         # Lista de contratos a mostrar
        propiedades=propiedades_para_select,     # Para los <select> en modales
        inquilinos=inquilinos_para_select,       # Para los <select> en modales
        propietarios=propietarios_para_filtro_tabla, # Para el filtro de propietario en la tabla
        por_vencer_count=por_vencer_count,
        suggested_number=suggested_contract_number,
        csrf_form=csrf_form
    )


@contratos_bp.route('/add', methods=['POST'])
@role_required('admin', 'gestor')  # Solo admin y gestor pueden añadir
@with_owner_filtering(require_active_owner=False)  # No requiere propietario activo para creación
def add_contrato():
    # Obtención de datos del formulario
    num_contrato_identificador_form = request.form.get('contractNumber')
    prop_id_form_str    = request.form.get('contractProperty')
    ten_id_form_str     = request.form.get('contractTenant')
    start_date_form_str = request.form.get('contractStartDate')
    end_date_form_str   = request.form.get('contractEndDate')
    price_form_str      = request.form.get('contractPrice')
    deposit_str = request.form.get('contractDeposit', '').strip()
    contract_type_form_val     = request.form.get('contractType', 'Local de Negocio')
    payment_day_form_str       = request.form.get('contractPaymentDay', '1')
    status_form_val            = request.form.get('contractStatus', 'pendiente')
    notes_form_val             = request.form.get('contractNotes', '').strip() or None
    apply_iva_form_bool        = (request.form.get('contractApplyIVA') == 'on')
    apply_irpf_form_bool       = (request.form.get('contractApplyIRPF') == 'on')
    tipo_actualizacion_renta_form_val = request.form.get('contractTipoActualizacionRenta', 'manual')
    actualiza_ipc_chk_form_bool       = (request.form.get('contractIPC') == 'on')
    actualiza_irav_chk_form_bool      = (request.form.get('contractIRAV') == 'on')
    ipc_ano_inicio_form_str           = request.form.get('contractIPCYear')
    ipc_mes_inicio_form_str           = request.form.get('contractIPCMonth')
    importe_fijo_form_str             = request.form.get('contractImporteActualizacionFija')
    aplicar_retroactivo_chk_form_bool = (request.form.get('contractAplicarIndiceRetroactivo') == 'on')
    serie_prefijo_form_val            = request.form.get('contractSeriePrefijo', '').strip() or None
    serie_proximo_numero_form_str_val = request.form.get('contractSerieProximoNumero', '1').strip()
    serie_formato_digitos_form_str_val= request.form.get('contractSerieFormatoDigitos', '4').strip()
    files_from_form = request.files.getlist('contractDocuments')

    try:
        deposit_dec_final = (Decimal(deposit_str.replace(',', '.')) if deposit_str else Decimal('0.00'))
    except InvalidOperation:
        deposit_dec_final = Decimal('0.00')
        flash("El importe de la fianza no tiene un formato válido. Se usará 0.00 €.", "warning")

    if not all([num_contrato_identificador_form, prop_id_form_str, ten_id_form_str, start_date_form_str, price_form_str]):
        flash('Nº Contrato, Propiedad, Inquilino, Fecha Inicio y Precio son obligatorios.', 'warning')
        return redirect(url_for('contratos_bp.listar_contratos'))

    if db.session.query(Contrato.id).filter_by(numero_contrato=num_contrato_identificador_form).first():
        flash(f'El identificador de contrato "{num_contrato_identificador_form}" ya existe.', 'warning')
        return redirect(url_for('contratos_bp.listar_contratos'))

    # No necesitamos validar extensiones aquí si solo vamos a leer el nombre del archivo
    # invalid_files = [f.filename for f in files_from_form if f and f.filename and not allowed_file(f.filename)]
    # if invalid_files:
    #     flash(f'Archivos no permitidos: {", ".join(invalid_files)}.', 'warning')
    #     return redirect(url_for('contratos_bp.listar_contratos'))

    try:
        start_date_obj_final = datetime.strptime(start_date_form_str, '%Y-%m-%d').date()
        end_date_obj_final   = (datetime.strptime(end_date_form_str, '%Y-%m-%d').date() if end_date_form_str else None)
        if end_date_obj_final and start_date_obj_final >= end_date_obj_final:
            raise ValueError("Fecha fin debe ser posterior a la fecha de inicio.")

        price_dec_final = Decimal(price_form_str.replace(',', '.'))
        if price_dec_final < 0 or deposit_dec_final < 0:
            raise ValueError("Precio y depósito no pueden ser negativos.")
        payment_day_int_final = int(payment_day_form_str or 1)
        if not (1 <= payment_day_int_final <= 31): payment_day_int_final = 1

        # Validar acceso a la propiedad usando el sistema de filtrado
        if not OwnerFilteredQueries.validate_access_to_entity('propiedad', int(prop_id_form_str)):
            flash("No tienes permiso para crear contratos para esta propiedad.", "danger")
            return redirect(url_for('contratos_bp.listar_contratos'))
            
        # Validar acceso al inquilino usando el sistema de filtrado
        if not OwnerFilteredQueries.validate_access_to_entity('inquilino', int(ten_id_form_str)):
            flash("No tienes permiso para crear contratos con este inquilino.", "danger")
            return redirect(url_for('contratos_bp.listar_contratos'))

        prop_obj_db   = db.session.get(Propiedad, int(prop_id_form_str))
        tenant_obj_db = db.session.get(Inquilino, int(ten_id_form_str))
        if not prop_obj_db: raise ValueError("La propiedad seleccionada no existe.")
        if not tenant_obj_db: raise ValueError("El inquilino seleccionado no existe.")
        
        final_actualiza_ipc_bool, final_actualiza_irav_bool = False, False
        ipc_ano_int_val, ipc_mes_int_val, importe_fijo_dec_final = None, None, None
        if tipo_actualizacion_renta_form_val in ['indice', 'indice_mas_fijo']:
            if not (actualiza_ipc_chk_form_bool ^ actualiza_irav_chk_form_bool):
                raise ValueError("Debes seleccionar solo IPC o IRAV, no ambos, para actualización por índice.")
            final_actualiza_ipc_bool  = actualiza_ipc_chk_form_bool
            final_actualiza_irav_bool = actualiza_irav_chk_form_bool
            if not ipc_ano_inicio_form_str or not ipc_mes_inicio_form_str:
                raise ValueError("Año y Mes de referencia del índice son obligatorios para este tipo de actualización.")
            ipc_ano_int_val = int(ipc_ano_inicio_form_str)
            ipc_mes_int_val = int(ipc_mes_inicio_form_str)
            if not (1 <= ipc_mes_int_val <= 12 and 1900 < ipc_ano_int_val < 2200):
                raise ValueError("Año o Mes de referencia del índice inválido.")
        if tipo_actualizacion_renta_form_val in ['fijo', 'indice_mas_fijo']:
            if not importe_fijo_form_str: raise ValueError("Importe fijo es obligatorio para este tipo de actualización.")
            try: importe_fijo_dec_final = Decimal(importe_fijo_form_str.replace(',', '.'))
            except InvalidOperation: raise ValueError("Formato de importe fijo inválido.")

        if serie_prefijo_form_val:
            existing_serie = Contrato.query.join(Propiedad)\
                .filter(Propiedad.propietario_id == prop_obj_db.propietario_id,
                        Contrato.serie_facturacion_prefijo == serie_prefijo_form_val,
                        Contrato.serie_facturacion_ano_actual == start_date_obj_final.year).first()
            if existing_serie:
                propietario_nombre_serie = prop_obj_db.propietario_ref.nombre if prop_obj_db.propietario_ref else "ID " + str(prop_obj_db.propietario_id)
                flash(f"El prefijo de serie '{serie_prefijo_form_val}' ya está en uso este año para el propietario '{propietario_nombre_serie}'.", 'danger')
                return redirect(url_for('contratos_bp.listar_contratos'))
        
        serie_proximo_numero_int_final = int(serie_proximo_numero_form_str_val) if serie_proximo_numero_form_str_val.isdigit() else 1
        serie_formato_digitos_int_final = int(serie_formato_digitos_form_str_val) if serie_formato_digitos_form_str_val.isdigit() else 4
        if not (1 <= serie_formato_digitos_int_final <= 10): serie_formato_digitos_int_final = 4

        # YA NO SE VALIDA EL SOLAPAMIENTO
        # overlap_q = ...

        new_contract = Contrato(
            numero_contrato=num_contrato_identificador_form, propiedad_id=prop_obj_db.id, inquilino_id=tenant_obj_db.id,
            tipo=contract_type_form_val, fecha_inicio=start_date_obj_final, fecha_fin=end_date_obj_final,
            precio_mensual=price_dec_final, deposito=deposit_dec_final, dia_pago=payment_day_int_final,
            estado=status_form_val, notas=notes_form_val, aplicar_iva=apply_iva_form_bool, aplicar_irpf=apply_irpf_form_bool,
            tipo_actualizacion_renta=tipo_actualizacion_renta_form_val, actualiza_ipc=final_actualiza_ipc_bool,
            actualiza_irav=final_actualiza_irav_bool, ipc_ano_inicio=ipc_ano_int_val, ipc_mes_inicio=ipc_mes_int_val,
            importe_actualizacion_fija=importe_fijo_dec_final, aplicar_indice_retroactivo=aplicar_retroactivo_chk_form_bool,
            serie_facturacion_prefijo=serie_prefijo_form_val,
            serie_facturacion_ultimo_numero=serie_proximo_numero_int_final - 1,
            serie_facturacion_ano_actual=start_date_obj_final.year,
            serie_facturacion_formato_digitos=serie_formato_digitos_int_final
        )
        db.session.add(new_contract)
        db.session.flush() 

        archivos_seleccionados_para_vincular = [f for f in files_from_form if f and f.filename] # Solo necesitamos que tengan nombre
        if new_contract.id and archivos_seleccionados_para_vincular:
            current_app.logger.info(f"ADD_CONTRATO: {len(archivos_seleccionados_para_vincular)} documentos seleccionados para vincular.")
            docs_db_registros = _link_contract_documents(new_contract, archivos_seleccionados_para_vincular) 
            if docs_db_registros:
                db.session.add_all(docs_db_registros)
        else:
            current_app.logger.info("ADD_CONTRATO: No se seleccionaron documentos o no se pudo obtener ID del contrato.")

        if new_contract.estado == 'activo' and prop_obj_db.estado_ocupacion != 'ocupada':
            prop_obj_db.estado_ocupacion = 'ocupada'
            db.session.add(prop_obj_db)

        db.session.commit()
        flash('Contrato añadido y documentos vinculados (si se encontraron en la ruta esperada).', 'success')

    except (ValueError, InvalidOperation) as ve:
        db.session.rollback()
        flash(f'Error en datos al crear contrato: {ve}', 'danger')
    except IntegrityError as ie:
        db.session.rollback()
        current_app.logger.error(f"IntegrityError add_contrato: {ie.orig if hasattr(ie, 'orig') else ie}", exc_info=True)
        flash('Error de base de datos al añadir contrato (posible duplicado o valor incorrecto).', 'danger')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error inesperado add_contrato: {e}", exc_info=True)
        flash(f'Error inesperado al añadir contrato: {e}', 'danger')

    return redirect(url_for('contratos_bp.listar_contratos'))



@contratos_bp.route('/edit/<int:id>', methods=['POST'])
@login_required
@validate_entity_access('contrato', 'id')  # Validación automática de acceso
def edit_contrato(id):
    # La validación de acceso ya se hizo automáticamente
    contract = OwnerFilteredQueries.get_contrato_by_id(id, include_relations=True)
    if not contract:
        flash('Contrato no encontrado o sin acceso.', 'warning')
        return redirect(url_for('contratos_bp.listar_contratos'))

    # Guardar el precio mensual anterior para compararlo después
    precio_mensual_anterior = contract.precio_mensual

    # Obtención de todos los datos del formulario
    new_num_contrato_identificador_form = request.form.get('editContractNumber', contract.numero_contrato)
    prop_id_edit_form_str = request.form.get('editContractProperty', str(contract.propiedad_id))
    ten_id_edit_form_str = request.form.get('editContractTenant', str(contract.inquilino_id))
    start_date_edit_form_str = request.form.get('editContractStartDate', contract.fecha_inicio.isoformat() if contract.fecha_inicio else '')
    end_date_edit_form_str = request.form.get('editContractEndDate', contract.fecha_fin.isoformat() if contract.fecha_fin else '')
    price_edit_form_str = request.form.get('editContractPrice', str(contract.precio_mensual))
    deposit_edit_form_str = request.form.get('editContractDeposit', str(contract.deposito or '0'))
    contract_type_edit_form_val = request.form.get('editContractType', contract.tipo)
    payment_day_edit_form_str = request.form.get('editContractPaymentDay', str(contract.dia_pago or '1'))
    status_edit_form_val = request.form.get('editContractStatus', contract.estado)
    notes_edit_form_val = request.form.get('editContractNotes', contract.notas or '').strip() or None
    apply_iva_edit_form_bool = request.form.get('editContractApplyIVA') == 'on'
    apply_irpf_edit_form_bool = request.form.get('editContractApplyIRPF') == 'on'
    tipo_actualizacion_renta_edit_form_val = request.form.get('editContractTipoActualizacionRenta', contract.tipo_actualizacion_renta)
    actualiza_ipc_chk_edit_form_bool = request.form.get('editContractIPC') == 'on'
    actualiza_irav_chk_edit_form_bool = request.form.get('editContractIRAV') == 'on'
    ipc_ano_edit_str = request.form.get('editContractIPCYear') # Leído dentro del try
    ipc_mes_edit_str = request.form.get('editContractIPCMonth') # Leído dentro del try
    importe_fijo_str_edit = request.form.get('editContractImporteActualizacionFija') # Leído dentro del try
    aplicar_retroactivo_chk_edit_form_bool = request.form.get('editContractAplicarIndiceRetroactivo') == 'on'
    serie_prefijo_edit_form_val = request.form.get('editContractSeriePrefijo', '').strip() or None
    serie_prox_num_edit_str_val = request.form.get('editContractSerieProximoNumero', '1').strip()
    serie_formato_digitos_edit_str_val = request.form.get('editContractSerieFormatoDigitos', '').strip()
    
    files_to_upload_edit_list = request.files.getlist('editContractDocuments') 
    remove_doc_ids_str_list_form = request.form.getlist('removeDocumentIds')
    remove_doc_ids_int_list = [int(doc_id) for doc_id in remove_doc_ids_str_list_form if doc_id.isdigit()]

    if not all([new_num_contrato_identificador_form, prop_id_edit_form_str, ten_id_edit_form_str, start_date_edit_form_str, price_edit_form_str]):
        flash('Nº Contrato, Propiedad, Inquilino, Fecha Inicio y Precio son obligatorios.', 'warning')
        return redirect(url_for('contratos_bp.listar_contratos'))
    
    if new_num_contrato_identificador_form != contract.numero_contrato and \
       db.session.query(Contrato.id).filter(Contrato.numero_contrato == new_num_contrato_identificador_form, Contrato.id != id).first():
        flash(f'El identificador de contrato "{new_num_contrato_identificador_form}" ya existe.', 'warning')
        return redirect(url_for('contratos_bp.listar_contratos'))

    try:
        start_date_obj_edit_final = datetime.strptime(start_date_edit_form_str, '%Y-%m-%d').date()
        end_date_obj_edit_final = datetime.strptime(end_date_edit_form_str, '%Y-%m-%d').date() if end_date_edit_form_str else None
        if end_date_obj_edit_final and start_date_obj_edit_final >= end_date_obj_edit_final: raise ValueError("Fecha fin debe ser posterior a inicio.")

        prop_id_edit_int = int(prop_id_edit_form_str)
        new_prop_obj_db = db.session.get(Propiedad, prop_id_edit_int)
        if not new_prop_obj_db: raise ValueError("La propiedad seleccionada no existe.")
        if current_user.role == 'gestor' and prop_id_edit_int != contract.propiedad_id:
            assigned_owner_ids_edit = {p.id for p in current_user.propietarios_asignados}
            if new_prop_obj_db.propietario_id not in assigned_owner_ids_edit:
                 flash("No tienes permiso para asignar contratos a esta nueva propiedad.", "danger"); return redirect(url_for('contratos_bp.listar_contratos'))
        
        ten_id_edit_int = int(ten_id_edit_form_str)
        new_tenant_obj_db = db.session.get(Inquilino, ten_id_edit_int)
        if not new_tenant_obj_db: raise ValueError("El inquilino seleccionado no existe.")

        nuevo_precio_mensual_form_dec = Decimal(price_edit_form_str.replace(',', '.')) # Convertir a Decimal
        try: deposit_dec_edit_final = Decimal(deposit_edit_form_str.replace(',', '.')) if deposit_edit_form_str else Decimal('0.00')
        except InvalidOperation: deposit_dec_edit_final = Decimal('0.00'); flash("Fianza inválida, usando 0.00€.", "warning")

        if nuevo_precio_mensual_form_dec < 0 or deposit_dec_edit_final < 0: raise ValueError("Precio y depósito no negativos.")
        payment_day_edit_int_final = int(payment_day_edit_form_str or 1);
        if not (1 <= payment_day_edit_int_final <= 31): payment_day_edit_int_final = 1
        
        final_actualiza_ipc_edit_bool, final_actualiza_irav_edit_bool = False, False
        ipc_ano_edit_int_val, ipc_mes_edit_int_val, importe_fijo_dec_edit_final = None, None, None
        if tipo_actualizacion_renta_edit_form_val in ['indice', 'indice_mas_fijo']:
            if not (actualiza_ipc_chk_edit_form_bool ^ actualiza_irav_chk_edit_form_bool): raise ValueError("Seleccione solo IPC o IRAV.")
            final_actualiza_ipc_edit_bool = actualiza_ipc_chk_edit_form_bool; final_actualiza_irav_edit_bool = actualiza_irav_chk_edit_form_bool
            if not ipc_ano_edit_str or not ipc_mes_edit_str: raise ValueError("Año y Mes ref. índice obligatorios.")
            ipc_ano_edit_int_val = int(ipc_ano_edit_str); ipc_mes_edit_int_val = int(ipc_mes_edit_str)
            if not (1 <= ipc_mes_edit_int_val <= 12 and 1900 < ipc_ano_edit_int_val < 2200): raise ValueError("Año o Mes ref. índice inválido.");
        if tipo_actualizacion_renta_edit_form_val in ['fijo', 'indice_mas_fijo']:
            if not importe_fijo_str_edit: raise ValueError("Importe act. fija obligatorio.");
            try: importe_fijo_dec_edit_final = Decimal(importe_fijo_str_edit.replace(',', '.'))
            except InvalidOperation: raise ValueError("Formato importe fijo inválido.")

        if serie_prefijo_edit_form_val:
            propietario_id_serie_edit = new_prop_obj_db.propietario_id
            ano_ref_serie_edit = start_date_obj_edit_final.year
            if (contract.serie_facturacion_prefijo != serie_prefijo_edit_form_val or
                (contract.propiedad_ref and contract.propiedad_ref.propietario_id != propietario_id_serie_edit) or
                (contract.serie_facturacion_ano_actual != ano_ref_serie_edit and serie_prefijo_edit_form_val)):
                conflicting_serie_edit = Contrato.query.join(Propiedad).filter(Propiedad.propietario_id == propietario_id_serie_edit, Contrato.serie_facturacion_prefijo == serie_prefijo_edit_form_val, Contrato.serie_facturacion_ano_actual == ano_ref_serie_edit, Contrato.id != id).first()
                if conflicting_serie_edit:
                    propietario_nombre_serie_conflicto = db.session.get(Propietario, propietario_id_serie_edit).nombre
                    flash(f"Prefijo de serie '{serie_prefijo_edit_form_val}' ya en uso para prop. '{propietario_nombre_serie_conflicto}' en año {ano_ref_serie_edit}.", 'danger')
                    return redirect(url_for('contratos_bp.listar_contratos'))

        serie_prox_num_edit_int_val = int(serie_prox_num_edit_str_val) if serie_prox_num_edit_str_val.isdigit() else 1
        if serie_prox_num_edit_int_val < 1: serie_prox_num_edit_int_val = 1
        serie_formato_digitos_edit_int_val = int(serie_formato_digitos_edit_str_val) if serie_formato_digitos_edit_str_val.isdigit() else (contract.serie_facturacion_formato_digitos or 4)
        if not (1 <= serie_formato_digitos_edit_int_val <= 10): serie_formato_digitos_edit_int_val = 4
        
        old_prop_id_val, old_estado_val = contract.propiedad_id, contract.estado
        
        contract.numero_contrato = new_num_contrato_identificador_form
        contract.propiedad_id = prop_id_edit_int; contract.inquilino_id = ten_id_edit_int
        contract.tipo = contract_type_edit_form_val; contract.fecha_inicio = start_date_obj_edit_final
        contract.fecha_fin = end_date_obj_edit_final
        # El precio se actualiza DESPUÉS de comprobar si es un cambio manual
        # contract.precio_mensual = nuevo_precio_mensual_form_dec
        contract.deposito = deposit_dec_edit_final; contract.dia_pago = payment_day_edit_int_final
        contract.estado = status_edit_form_val; contract.notas = notes_edit_form_val
        contract.aplicar_iva = apply_iva_edit_form_bool; contract.aplicar_irpf = apply_irpf_edit_form_bool
        contract.tipo_actualizacion_renta = tipo_actualizacion_renta_edit_form_val
        contract.actualiza_ipc = final_actualiza_ipc_edit_bool; contract.actualiza_irav = final_actualiza_irav_edit_bool
        contract.ipc_ano_inicio = ipc_ano_edit_int_val; contract.ipc_mes_inicio = ipc_mes_edit_int_val
        contract.importe_actualizacion_fija = importe_fijo_dec_edit_final
        contract.aplicar_indice_retroactivo = aplicar_retroactivo_chk_edit_form_bool

        # Lógica de Serie de Facturación
        current_year_for_serie_edit = start_date_obj_edit_final.year
        reset_serie_needed = False
        if (contract.serie_facturacion_prefijo != serie_prefijo_edit_form_val or \
           (serie_prefijo_edit_form_val and contract.serie_facturacion_ano_actual != current_year_for_serie_edit) or \
           (serie_prefijo_edit_form_val and contract.propiedad_ref and contract.propiedad_ref.propietario_id != new_prop_obj_db.propietario_id) or \
           (serie_prefijo_edit_form_val and serie_prox_num_edit_int_val == 1 and (contract.serie_facturacion_ultimo_numero or 0) > 0) ): 
            reset_serie_needed = True
        if serie_prefijo_edit_form_val:
            if reset_serie_needed:
                contract.serie_facturacion_ultimo_numero = serie_prox_num_edit_int_val - 1
                contract.serie_facturacion_ano_actual = current_year_for_serie_edit
            elif serie_prox_num_edit_int_val > (contract.serie_facturacion_ultimo_numero or 0) or \
                 (contract.serie_facturacion_ano_actual != current_year_for_serie_edit and contract.serie_facturacion_ano_actual is not None) :
                 contract.serie_facturacion_ultimo_numero = serie_prox_num_edit_int_val -1
                 contract.serie_facturacion_ano_actual = current_year_for_serie_edit
            contract.serie_facturacion_prefijo = serie_prefijo_edit_form_val
            contract.serie_facturacion_formato_digitos = serie_formato_digitos_edit_int_val
        else: 
            contract.serie_facturacion_prefijo = None; contract.serie_facturacion_ultimo_numero = 0
            contract.serie_facturacion_ano_actual = None; contract.serie_facturacion_formato_digitos = 4

        # --- REGISTRAR CAMBIO MANUAL DE RENTA ---
        # Si el precio nuevo del formulario es diferente al precio que tenía el contrato ANTES de esta edición,
        # Y si el tipo de actualización es 'manual' (o si no se están aplicando otros tipos de actualización automática aquí)
        if nuevo_precio_mensual_form_dec.compare(precio_mensual_anterior) != Decimal('0'):
            # Solo registrar como manual si el TIPO de actualización es manual
            # O si no se han proporcionado datos para otros tipos de actualización automática
            # que podrían haber sido la causa del cambio de precio.
            # La lógica actual de _calculate_updated_rent_for_invoice es la que crea el historial para IPC/Fijo.
            # Aquí, solo registramos si el tipo es explícitamente manual o si el cambio es "puramente manual".
            
            # Condición: si el precio cambió Y el tipo de actualización del contrato es manual
            if tipo_actualizacion_renta_edit_form_val == 'manual':
                 historial_manual = HistorialActualizacionRenta(
                     contrato_id=contract.id,
                     factura_id=None, 
                     fecha_actualizacion=date.today(),
                     renta_anterior=precio_mensual_anterior,
                     renta_nueva=nuevo_precio_mensual_form_dec, # Usar el nuevo precio del formulario
                     tipo_actualizacion='Manual',
                     descripcion_adicional=f"Renta actualizada manualmente a través del formulario de edición de contrato por {current_user.username}."
                 )
                 db.session.add(historial_manual)
                 current_app.logger.info(f"Registrada actualización manual de renta (form edic.) para contrato {contract.id}: de {precio_mensual_anterior} a {nuevo_precio_mensual_form_dec}")
            
            # Actualizar el precio del contrato con el nuevo valor del formulario
            contract.precio_mensual = nuevo_precio_mensual_form_dec
        # --- FIN REGISTRAR CAMBIO MANUAL ---

        # Procesamiento de Documentos
        if remove_doc_ids_int_list:
            docs_to_remove_from_db_list = Documento.query.filter(Documento.id.in_(remove_doc_ids_int_list), Documento.contrato_id == contract.id).all()
            for doc_to_remove_item in docs_to_remove_from_db_list: db.session.delete(doc_to_remove_item)
        
        archivos_nuevos_seleccionados_para_vincular = [f for f in files_to_upload_edit_list if f and f.filename and allowed_file(f.filename)]
        if archivos_nuevos_seleccionados_para_vincular:
            new_docs_db_registros = _link_contract_documents(contract, archivos_nuevos_seleccionados_para_vincular)
            if new_docs_db_registros: db.session.add_all(new_docs_db_registros)
        
        # Actualizar estado de propiedad
        if contract.estado == 'activo':
            if new_prop_obj_db.estado_ocupacion != 'ocupada': new_prop_obj_db.estado_ocupacion = 'ocupada'; db.session.add(new_prop_obj_db)
        if old_prop_id_val != contract.propiedad_id or (old_estado_val == 'activo' and contract.estado != 'activo'):
            old_prop_obj = db.session.get(Propiedad, old_prop_id_val)
            if old_prop_obj and not Contrato.query.filter(Contrato.propiedad_id == old_prop_id_val, Contrato.estado == 'activo', Contrato.id != contract.id).first():
                old_prop_obj.estado_ocupacion = 'vacia'; db.session.add(old_prop_obj)
        
        if db.session.dirty or db.session.new or db.session.deleted:
            db.session.commit()
            flash(f'Contrato "{contract.numero_contrato}" actualizado correctamente.', 'success')
        else:
            flash('No se realizaron cambios en el contrato.', 'info')

    except (ValueError, InvalidOperation) as ve:
        db.session.rollback(); flash(f'Error en los datos al editar contrato: {ve}', 'danger')
    except IntegrityError as ie:
        db.session.rollback(); current_app.logger.error(f"IntegrityError editando contrato {id}: {ie.orig if hasattr(ie, 'orig') else ie}", exc_info=True)
        err_msg = f'Error de BD al editar. {str(ie.orig)[:100] if hasattr(ie,"orig") else ""}'
        if hasattr(ie, 'orig') and "numero_contrato" in str(ie.orig).lower(): err_msg = f'Identificador "{new_num_contrato_identificador_form}" ya existe.'
        flash(err_msg, 'danger')
    except Exception as e:
        db.session.rollback(); flash(f'Error inesperado al editar el contrato: {e}', 'danger')
        current_app.logger.error(f"Error inesperado en edit_contrato {id}: {e}", exc_info=True)
        
    return redirect(url_for('contratos_bp.listar_contratos'))



@contratos_bp.route('/delete/<int:id>', methods=['POST'])
@role_required('admin', 'gestor') # Solo admin o gestor pueden borrar
@validate_entity_access('contrato', 'id') # Validación automática de acceso
def delete_contrato(id):
    # La validación de acceso ya se hizo automáticamente
    contract = OwnerFilteredQueries.get_contrato_by_id(id, include_relations=True)

    if not contract:
        flash('Contrato no encontrado o sin acceso.', 'warning')
        return redirect(url_for('contratos_bp.listar_contratos'))

    # Opcional: Verificación de permiso adicional si owner_access_required no es suficiente
    # o si se basa en algo más que el propietario de la propiedad del contrato.
    # if current_user.role != 'admin' and not current_user_has_access_to_contract(contract):
    #     flash("No tienes permiso para eliminar este contrato.", "danger")
    #     return redirect(url_for('contratos_bp.listar_contratos'))

    try:
        contract_num_display = contract.numero_contrato # Guardar para el mensaje flash
        prop_id_original = contract.propiedad_id
        was_active = (contract.estado == 'activo')

        current_app.logger.info(f"Iniciando eliminación del Contrato ID: {id}, Número: {contract_num_display}.")
        current_app.logger.info(f"Los archivos físicos asociados al contrato NO serán eliminados del disco.")

        # Si NO tienes cascade="all, delete-orphan" en la relación Contrato.documentos,
        # y quieres eliminar los registros de Documento de la BD, descomenta el siguiente bloque:
        """
        if contract.documentos:
            current_app.logger.info(f"Eliminando registros de {len(contract.documentos)} documentos asociados de la BD para Contrato ID: {id}.")
            for doc in list(contract.documentos): # Usar list() para evitar problemas al modificar la colección
                db.session.delete(doc)
        """
        
        # Eliminar el contrato. Si cascade="all, delete-orphan" está en Contrato.facturas
        # y Contrato.documentos, esos registros relacionados también se eliminarán.
        db.session.delete(contract)
        current_app.logger.info(f"Contrato ID: {id} marcado para eliminar de la BD.")

        # Actualizar estado de la propiedad si el contrato eliminado era el único activo
        if was_active and prop_id_original:
            prop_original = db.session.get(Propiedad, prop_id_original)
            if prop_original:
                # Contar cuántos contratos *otros* (excluyendo el actual que se está borrando) siguen activos en esa propiedad
                # Esta query se hace ANTES del commit, por lo que el contrato actual aún existe en la sesión
                # pero está marcado para delete. Usar filter(Contrato.id != id) es más explícito.
                still_active_contracts_count = db.session.query(func.count(Contrato.id)).filter(
                    Contrato.propiedad_id == prop_id_original,
                    Contrato.estado == 'activo',
                    Contrato.id != id # Excluir explícitamente el contrato que se está eliminando
                ).scalar()

                if still_active_contracts_count == 0:
                    prop_original.estado_ocupacion = 'vacia'
                    db.session.add(prop_original) # Marcar para actualizar
                    current_app.logger.info(f"Propiedad ID {prop_id_original} marcada como vacía tras eliminar contrato {id}.")
        
        db.session.commit()
        flash(f'Contrato "{contract_num_display}" y sus registros asociados en la base de datos han sido eliminados. Los archivos físicos permanecen en disco.', 'success')
    
    except Exception as e:
        db.session.rollback()
        flash(f'Error al eliminar el contrato: {e}', 'danger')
        current_app.logger.error(f"Error en delete_contrato ID {id}: {e}", exc_info=True)

    return redirect(url_for('contratos_bp.listar_contratos'))


@contratos_bp.route('/uploads/contracts/<path:filename>')
@login_required
@with_owner_filtering()
def serve_contract_upload(filename):
    try:
        doc = Documento.query.filter_by(filename=filename).options(
            joinedload(Documento.contrato_ref).joinedload(Contrato.propiedad_ref).joinedload(Propiedad.propietario_ref)
        ).first()
        
        if not doc or not doc.contrato_ref or not doc.contrato_ref.propiedad_ref or not doc.contrato_ref.propiedad_ref.propietario_ref:
            current_app.logger.warning(f"serve_contract_upload: Documento o propietario no encontrado para filename {filename}")
            abort(404)

        # Validar acceso al contrato usando el sistema de filtrado
        if not OwnerFilteredQueries.validate_access_to_entity('contrato', doc.contrato_ref.id):
            flash("No tienes permiso para ver este documento.", "danger")
            abort(403)

        propietario_del_documento = doc.contrato_ref.propiedad_ref.propietario_ref

        # Obtener la ruta de la carpeta donde está el archivo
        # El 'filename' en la BD es el nombre real en disco (puede ser el UUID.hex o el original asegurado)
        document_folder_path = get_owner_document_path(
            propietario=propietario_del_documento,
            subfolder_type="Contratos",
            year=None # No aplica año para contratos
        )

        if not document_folder_path:
            current_app.logger.error(f"No se pudo determinar la carpeta para el documento {filename} del prop. {propietario_del_documento.id}")
            abort(404)

        # filename ya es el nombre seguro/único almacenado en la BD
        return send_from_directory(document_folder_path, filename) # filename es el que está en la BD

    except FileNotFoundError:
        flash(f'Archivo de contrato "{filename}" no encontrado.', 'warning')
        abort(404)
    except Exception as e:
        flash(f'Error al servir archivo de contrato: {e}', 'danger')
        current_app.logger.error(f"Error sirviendo archivo de contrato {filename}: {e}", exc_info=True)
        abort(500)

@contratos_bp.route('/ver/<int:id>', methods=['GET'])
@login_required
@filtered_detail_view('contrato', 'id', log_queries=True) # Validación automática de acceso y filtrado
def ver_contrato(id):
    # La validación de acceso ya se hizo automáticamente
    contract = OwnerFilteredQueries.get_contrato_by_id(id, include_relations=True)
    if not contract:
        abort(404)
    
    # Ordenar el historial por fecha descendente para mostrar el más reciente primero
    # Hacemos esto en Python ya que el selectinload no permite order_by directamente en la relación cargada de esta forma.
    # Si la lista es muy grande, considera hacer una query separada con order_by.
    historial_ordenado = sorted(contract.historial_actualizaciones, key=lambda h: h.fecha_actualizacion, reverse=True)

    return render_template(
        "ver_contrato.html", 
        contract=contract, 
        historial_actualizaciones=historial_ordenado, # Pasar el historial ordenado
        title=f"Contrato {contract.numero_contrato}"
    )