# myapp/tasks.py
from datetime import date, timedelta
from flask import current_app, url_for # Importar url_for aquí
from .models import db, Contrato, Factura, Notification, User, Propiedad # Ajusta según tus modelos
from . import mail # Si vas a enviar emails
from flask_mail import Message
from sqlalchemy import extract, func, or_

def send_reminder_email(recipient_email, subject, html_body):
    """Función auxiliar para enviar emails."""
    if not recipient_email:
        current_app.logger.warning(f"Intento de envío de email sin destinatario para: {subject}")
        return

    settings = getattr(current_app, 'config', {}) # Usar app.config
    sender_email = settings.get('MAIL_DEFAULT_SENDER')
    sender_name = settings.get('MAIL_SENDER_DISPLAY_NAME', 'RentalSys')

    if not sender_email:
        current_app.logger.error(f"MAIL_DEFAULT_SENDER no configurado. No se pudo enviar email: {subject}")
        return

    sender = (sender_name, sender_email) if sender_name else sender_email
    msg = Message(subject=subject, sender=sender, recipients=[recipient_email], html=html_body)
    try:
        mail.send(msg)
        current_app.logger.info(f"Email de recordatorio enviado a {recipient_email} para: {subject}")
    except Exception as e:
        current_app.logger.error(f"Error enviando email de recordatorio a {recipient_email}: {e}", exc_info=True)

# --- Tarea: Contratos por Vencer ---
def check_expiring_contracts(app_context):
    with app_context.app_context(): # Necesitas el contexto de la aplicación
        current_app.logger.info("Tarea Programada: Verificando contratos por vencer...")
        today = date.today()
        ninety_days_later = today + timedelta(days=90)
        
        expiring_contracts = Contrato.query.filter(
            Contrato.estado == 'activo',
            Contrato.fecha_fin.isnot(None),
            Contrato.fecha_fin >= today, # Que aún no haya vencido
            Contrato.fecha_fin <= ninety_days_later
        ).all()

        admin_users = User.query.filter_by(role='admin').all()

        for contract in expiring_contracts:
            days_to_expiry = (contract.fecha_fin - today).days
            message = (f"El contrato '{contract.numero_contrato}' "
                       f"(Propiedad: {contract.propiedad_ref.direccion if contract.propiedad_ref else 'N/A'}, "
                       f"Inquilino: {contract.inquilino_ref.nombre if contract.inquilino_ref else 'N/A'}) "
                       f"vence en {days_to_expiry} días ({contract.fecha_fin.strftime('%d/%m/%Y')}).")
            
            related_url = url_for('contratos_bp.ver_contrato', id=contract.id, _external=True)

            # Notificación para administradores
            for admin in admin_users:
                # Evitar duplicados si ya existe una notificación similar reciente (opcional)
                existing_notif = Notification.query.filter(
                    Notification.message.like(f"%contrato '{contract.numero_contrato}'%vence%"),
                    Notification.user_id == admin.id,
                    Notification.timestamp > today - timedelta(days=7) # No notificar si ya se hizo en la última semana
                ).first()
                if not existing_notif:
                    notif = Notification(message=message, level='warning', related_url=related_url, user_id=admin.id)
                    db.session.add(notif)

            # Notificación y email para gestores asignados
            if contract.propiedad_ref and contract.propiedad_ref.propietario_ref:
                for gestor in contract.propiedad_ref.propietario_ref.usuarios_asignados:
                    if gestor.role == 'gestor':
                        existing_notif_gestor = Notification.query.filter(
                            Notification.message.like(f"%contrato '{contract.numero_contrato}'%vence%"),
                            Notification.user_id == gestor.id,
                            Notification.timestamp > today - timedelta(days=7)
                        ).first()
                        if not existing_notif_gestor:
                            notif_gestor = Notification(message=message, level='warning', related_url=related_url, user_id=gestor.id)
                            db.session.add(notif_gestor)
                            # Enviar email al gestor
                            if gestor.email:
                                subject_email = f"Recordatorio: Contrato {contract.numero_contrato} próximo a vencer"
                                html_body = f"<p>{message}</p><p>Puedes ver los detalles aquí: <a href='{related_url}'>{related_url}</a></p>"
                                send_reminder_email(gestor.email, subject_email, html_body)
        try:
            db.session.commit()
            current_app.logger.info(f"Notificaciones de contratos por vencer procesadas: {len(expiring_contracts)} contratos revisados.")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error haciendo commit de notificaciones de contratos: {e}")

# --- Tarea: Facturas Pendientes de Generar ---
def check_pending_invoices(app_context):
    with app_context.app_context():
        current_app.logger.info("Tarea Programada: Verificando facturas pendientes de generar...")
        today = date.today()
        
        # Contratos activos cuyo día de pago ya pasó hace más de X días en el mes actual
        # y para los que no existe factura este mes.
        # Por ejemplo, si hoy es 25, y el día de pago es 1, ya pasaron 24 días.
        # Si el día de pago es 5, pasaron 20 días.
        
        # Obtener todos los contratos activos
        active_contracts = Contrato.query.filter(Contrato.estado == 'activo').all()
        admin_users = User.query.filter_by(role='admin').all()

        for contract in active_contracts:
            dia_pago_contrato = contract.dia_pago if 1 <= contract.dia_pago <= 28 else 1 # Limitar a un día válido
            
            # Fecha de facturación esperada para este mes
            try:
                fecha_facturacion_esperada_este_mes = date(today.year, today.month, dia_pago_contrato)
            except ValueError: # Día no válido para el mes (ej. 31 en febrero)
                from calendar import monthrange
                ultimo_dia_mes = monthrange(today.year, today.month)[1]
                fecha_facturacion_esperada_este_mes = date(today.year, today.month, ultimo_dia_mes)

            # Si la fecha de facturación esperada ya pasó hace más de, por ejemplo, 20 días
            # Y aún no se ha generado la factura para ESTE MES
            if today >= fecha_facturacion_esperada_este_mes + timedelta(days=20):
                factura_existente_este_mes = Factura.query.filter(
                    Factura.contrato_id == contract.id,
                    extract('year', Factura.fecha_emision) == today.year,
                    extract('month', Factura.fecha_emision) == today.month
                ).first()

                if not factura_existente_este_mes:
                    message = (f"La factura para el contrato '{contract.numero_contrato}' "
                               f"(Prop.: {contract.propiedad_ref.direccion if contract.propiedad_ref else 'N/A'}, "
                               f"Inq.: {contract.inquilino_ref.nombre if contract.inquilino_ref else 'N/A'}) "
                               f"del periodo {today.month}/{today.year} parece no haberse generado. "
                               f"Han pasado más de 20 días desde el día de facturación ({dia_pago_contrato}).")
                    
                    related_url = url_for('facturas_bp.generar_facturas_mes', _external=True) # Enlace a la pág de generar

                    # Notificación para administradores
                    for admin in admin_users:
                        existing_notif = Notification.query.filter(
                            Notification.message.like(f"%factura para el contrato '{contract.numero_contrato}'%periodo {today.month}/{today.year}%no haberse generado%"),
                            Notification.user_id == admin.id,
                            Notification.timestamp > today - timedelta(days=7) 
                        ).first()
                        if not existing_notif:
                            notif = Notification(message=message, level='danger', related_url=related_url, user_id=admin.id)
                            db.session.add(notif)

                    # Notificación y email para gestores asignados
                    if contract.propiedad_ref and contract.propiedad_ref.propietario_ref:
                        for gestor in contract.propiedad_ref.propietario_ref.usuarios_asignados:
                            if gestor.role == 'gestor':
                                existing_notif_gestor = Notification.query.filter(
                                    Notification.message.like(f"%factura para el contrato '{contract.numero_contrato}'%periodo {today.month}/{today.year}%no haberse generado%"),
                                    Notification.user_id == gestor.id,
                                    Notification.timestamp > today - timedelta(days=7)
                                ).first()
                                if not existing_notif_gestor:
                                    notif_gestor = Notification(message=message, level='danger', related_url=related_url, user_id=gestor.id)
                                    db.session.add(notif_gestor)
                                    if gestor.email:
                                        subject_email = f"Alerta: Factura pendiente de generar ({contract.numero_contrato})"
                                        html_body = f"<p>{message}</p><p>Puedes generarla aquí: <a href='{related_url}'>{related_url}</a></p>"
                                        send_reminder_email(gestor.email, subject_email, html_body)
        try:
            db.session.commit()
            current_app.logger.info("Notificaciones de facturas pendientes procesadas.")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error haciendo commit de notificaciones de facturas pendientes: {e}")

# --- Tarea: Revisiones de IPC/IRAV Próximas ---
def check_ipc_reviews(app_context):
    with app_context.app_context():
        current_app.logger.info("Tarea Programada: Verificando revisiones de IPC/IRAV...")
        today = date.today()
        current_month = today.month
        next_month_num = (current_month % 12) + 1 # Mes siguiente
        
        # Contratos cuya fecha de inicio (mes) coincide con el mes actual o el siguiente
        # y que tienen actualiza_ipc o actualiza_irav activado.
        contracts_for_review = Contrato.query.filter(
            Contrato.estado == 'activo',
            or_(Contrato.actualiza_ipc == True, Contrato.actualiza_irav == True),
            Contrato.fecha_inicio.isnot(None),
            or_(
                extract('month', Contrato.fecha_inicio) == current_month,
                extract('month', Contrato.fecha_inicio) == next_month_num
            )
        ).all()

        admin_users = User.query.filter_by(role='admin').all()

        for contract in contracts_for_review:
            # Determinar si la revisión es este mes o el siguiente
            review_month = contract.fecha_inicio.month
            review_year = today.year # La revisión se aplica en el aniversario anual
            
            # Evitar notificar si la revisión es para un aniversario pasado
            # (ej. si el contrato empezó en un enero anterior y estamos en diciembre notificando para enero)
            if review_year < contract.fecha_inicio.year: # Caso de contrato que aún no ha cumplido su primer año
                continue
            if review_year == contract.fecha_inicio.year and review_month < contract.fecha_inicio.month: # Aniversario ya pasó este año
                continue

            # Construir mensaje
            index_type = "IPC" if contract.actualiza_ipc else "IRAV" if contract.actualiza_irav else "Índice"
            message = (f"Próxima revisión de renta por {index_type} para el contrato '{contract.numero_contrato}' "
                       f"(Prop.: {contract.propiedad_ref.direccion if contract.propiedad_ref else 'N/A'}, "
                       f"Inq.: {contract.inquilino_ref.nombre if contract.inquilino_ref else 'N/A'}). "
                       f"Aniversario en {app_context.config['MESES_STR'][review_month]}/{review_year}. " # Usar MESES_STR de config si existe
                       f"Mes de referencia para índice: {app_context.config['MESES_STR'][contract.ipc_mes_inicio]}/{contract.ipc_ano_inicio}.")
            
            related_url = url_for('ipc_bp.listar_indices', _external=True) # Enlace a la gestión de índices

            # Notificación para administradores
            for admin in admin_users:
                existing_notif = Notification.query.filter(
                    Notification.message.like(f"%revisión de renta por {index_type} para el contrato '{contract.numero_contrato}'%Aniversario en {app_context.config['MESES_STR'][review_month]}/{review_year}%"),
                    Notification.user_id == admin.id,
                    Notification.timestamp > today - timedelta(days=25) # No notificar muy seguido para el mismo evento
                ).first()
                if not existing_notif:
                    notif = Notification(message=message, level='info', related_url=related_url, user_id=admin.id)
                    db.session.add(notif)

            # Notificación y email para gestores asignados
            if contract.propiedad_ref and contract.propiedad_ref.propietario_ref:
                for gestor in contract.propiedad_ref.propietario_ref.usuarios_asignados:
                    if gestor.role == 'gestor':
                        existing_notif_gestor = Notification.query.filter(
                            Notification.message.like(f"%revisión de renta por {index_type} para el contrato '{contract.numero_contrato}'%Aniversario en {app_context.config['MESES_STR'][review_month]}/{review_year}%"),
                            Notification.user_id == gestor.id,
                            Notification.timestamp > today - timedelta(days=25)
                        ).first()
                        if not existing_notif_gestor:
                            notif_gestor = Notification(message=message, level='info', related_url=related_url, user_id=gestor.id)
                            db.session.add(notif_gestor)
                            if gestor.email:
                                subject_email = f"Recordatorio: Revisión {index_type} para contrato {contract.numero_contrato}"
                                html_body = f"<p>{message}</p><p>Revisa los índices aquí: <a href='{related_url}'>{related_url}</a></p>"
                                send_reminder_email(gestor.email, subject_email, html_body)
        try:
            db.session.commit()
            current_app.logger.info(f"Notificaciones de revisiones IPC/IRAV procesadas: {len(contracts_for_review)} contratos revisados.")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error haciendo commit de notificaciones de IPC/IRAV: {e}")