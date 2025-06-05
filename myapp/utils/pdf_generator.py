# myapp/utils/pdf_generator.py

import io
import os
import json
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

from flask import current_app, g, abort
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, KeepInFrame
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4

# --- Importar db y modelos ---
try:
    from .. import db
    from ..models import Factura, Propietario, Inquilino, Propiedad, SystemSettings
except ImportError:
    # Fallback for direct execution or different structure
    db = None 
    class MockModel:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
        @classmethod
        def query(cls): return cls()
        def options(self, *args): return self
        def joinedload(self, *args): return self
        def get(self, *args): return self
        
    class MockFactura(MockModel): 
        items = []
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            if 'items' not in self.__dict__:
                self.items = []

    class MockPropietario(MockModel): pass
    class MockInquilino(MockModel): pass
    class MockPropiedad(MockModel): 
        propietario_ref = MockPropietario()
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            if 'propietario_ref' not in self.__dict__:
                self.propietario_ref = MockPropietario()

    class MockSystemSettings(MockModel): pass

    Factura, Propietario, Inquilino, Propiedad, SystemSettings = MockFactura, MockPropietario, MockInquilino, MockPropiedad, MockSystemSettings

# --- Constantes y Estilos ---
PAGE_WIDTH, PAGE_HEIGHT = A4
styles = getSampleStyleSheet()
TWO_PLACES = Decimal('0.01')

# Color principal para acentos (azul profesional)
PRIMARY_COLOR = colors.HexColor('#1e3a5f')
SECONDARY_COLOR = colors.HexColor('#f0f4f8')
ACCENT_COLOR = colors.HexColor('#e8f0fe')

# Estilos personalizados basados en la imagen
styles.add(ParagraphStyle(name='InvoiceTitle', 
                          alignment=TA_LEFT,
                          fontSize=28, 
                          fontName='Helvetica-Bold',
                          textColor=PRIMARY_COLOR,
                          spaceBefore=0,
                          spaceAfter=6,
                          leading=32
                         ))

styles.add(ParagraphStyle(name='InvoiceDetails', 
                          alignment=TA_LEFT,
                          fontSize=10,
                          fontName='Helvetica',
                          textColor=colors.HexColor('#666666'),
                          leading=14,
                          spaceBefore=2 
                         ))

styles.add(ParagraphStyle(name='CompanyName', 
                          alignment=TA_RIGHT,
                          fontSize=13, 
                          fontName='Helvetica-Bold',
                          textColor=PRIMARY_COLOR,
                          spaceBefore=0,
                          spaceAfter=4
                         ))

styles.add(ParagraphStyle(name='CompanyDetails', 
                          alignment=TA_RIGHT,
                          fontSize=9,
                          fontName='Helvetica',
                          textColor=colors.HexColor('#666666'),
                          leading=12
                         ))

styles.add(ParagraphStyle(name='SectionTitle', 
                          alignment=TA_LEFT, 
                          fontSize=10, 
                          fontName='Helvetica-Bold', 
                          textColor=PRIMARY_COLOR,
                          spaceBefore=15, 
                          spaceAfter=6
                         ))

styles.add(ParagraphStyle(name='SectionTitleRight', 
                          alignment=TA_RIGHT, 
                          fontSize=10, 
                          fontName='Helvetica-Bold', 
                          textColor=PRIMARY_COLOR,
                          spaceBefore=15, 
                          spaceAfter=6
                         ))

styles.add(ParagraphStyle(name='ClientName', 
                          alignment=TA_LEFT, 
                          fontSize=11, 
                          fontName='Helvetica-Bold', 
                          textColor=colors.HexColor('#2E3B4E'),
                          spaceAfter=3
                         ))

styles.add(ParagraphStyle(name='ClientDetails', 
                          alignment=TA_LEFT, 
                          fontSize=9, 
                          fontName='Helvetica', 
                          textColor=colors.HexColor('#666666'),
                          leading=12
                         ))

styles.add(ParagraphStyle(name='ClientDetailsRight', 
                          alignment=TA_RIGHT, 
                          fontSize=9, 
                          fontName='Helvetica', 
                          textColor=colors.HexColor('#666666'),
                          leading=12
                         ))

styles.add(ParagraphStyle(name='TableHeader', 
                          alignment=TA_LEFT, 
                          fontSize=9, 
                          fontName='Helvetica-Bold',
                          textColor=colors.white
                         ))

styles.add(ParagraphStyle(name='TableHeaderRight', 
                          alignment=TA_RIGHT, 
                          fontSize=9, 
                          fontName='Helvetica-Bold',
                          textColor=colors.white
                         ))

styles.add(ParagraphStyle(name='TableCell', 
                          alignment=TA_LEFT, 
                          fontSize=9, 
                          fontName='Helvetica',
                          textColor=colors.HexColor('#333333')
                         ))

styles.add(ParagraphStyle(name='TableCellRight', 
                          alignment=TA_RIGHT, 
                          fontSize=9, 
                          fontName='Helvetica',
                          textColor=colors.HexColor('#333333')
                         ))

styles.add(ParagraphStyle(name='TotalLabel', 
                          alignment=TA_LEFT, 
                          fontSize=9, 
                          fontName='Helvetica',
                          textColor=colors.HexColor('#666666')
                         ))

styles.add(ParagraphStyle(name='TotalValue', 
                          alignment=TA_RIGHT, 
                          fontSize=9, 
                          fontName='Helvetica',
                          textColor=colors.HexColor('#333333')
                         ))

styles.add(ParagraphStyle(name='GrandTotalLabel', 
                          alignment=TA_LEFT, 
                          fontSize=11, 
                          fontName='Helvetica-Bold',
                          textColor=PRIMARY_COLOR
                         ))

styles.add(ParagraphStyle(name='GrandTotalValue', 
                          alignment=TA_RIGHT, 
                          fontSize=11, 
                          fontName='Helvetica-Bold',
                          textColor=PRIMARY_COLOR
                         ))

styles.add(ParagraphStyle(name='PaymentTitle', 
                          alignment=TA_LEFT, 
                          fontSize=10, 
                          fontName='Helvetica-Bold', 
                          textColor=PRIMARY_COLOR,
                          spaceBefore=15, 
                          spaceAfter=6
                         ))

styles.add(ParagraphStyle(name='PaymentDetails', 
                          alignment=TA_LEFT, 
                          fontSize=9, 
                          fontName='Helvetica', 
                          textColor=colors.HexColor('#666666'),
                          leading=12
                         ))

styles.add(ParagraphStyle(name='NotesTitle', 
                          alignment=TA_LEFT, 
                          fontSize=10, 
                          fontName='Helvetica-Bold', 
                          textColor=PRIMARY_COLOR,
                          spaceBefore=15, 
                          spaceAfter=6
                         ))

styles.add(ParagraphStyle(name='NotesText', 
                          alignment=TA_LEFT, 
                          fontSize=9, 
                          fontName='Helvetica', 
                          textColor=colors.HexColor('#666666'),
                          leading=12
                         ))

styles.add(ParagraphStyle(name='FooterStyle', 
                          alignment=TA_CENTER, 
                          fontSize=8, 
                          fontName='Helvetica',
                          textColor=colors.HexColor('#999999')
                         ))

styles.add(ParagraphStyle(name='IRPFText', 
                          alignment=TA_LEFT, 
                          fontSize=9, 
                          fontName='Helvetica',
                          textColor=colors.HexColor('#E74C3C')
                         ))

styles.add(ParagraphStyle(name='IRPFValue', 
                          alignment=TA_RIGHT, 
                          fontSize=9, 
                          fontName='Helvetica',
                          textColor=colors.HexColor('#E74C3C')
                         ))

# --- Funciones Auxiliares de Formato ---
def format_currency(value):
    if value is None: return "0,00 €"
    try:
        dec_value = value if isinstance(value, Decimal) else Decimal(str(value))
        quantized_value = dec_value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        formatted = "{:,.2f}".format(quantized_value)
        int_part, dec_part = formatted.split('.')
        formatted_es = int_part.replace(',', '.') + ',' + dec_part
        return f"{formatted_es} €"
    except (TypeError, ValueError, InvalidOperation) as e:
        logger = current_app.logger if 'current_app' in globals() and hasattr(current_app, 'logger') else print
        logger(f"Advertencia en format_currency: No se pudo formatear el valor '{value}'. Error: {e}")
        return str(value)

def format_date(value):
    if not value: return ""
    date_format = '%d/%m/%Y'
    try:
        return value.strftime(date_format)
    except AttributeError:
        return str(value)

def calculate_due_date(emission_date, days=30):
    """Calcula la fecha de vencimiento agregando días a la fecha de emisión"""
    if not emission_date:
        return ""
    try:
        from datetime import timedelta
        due_date = emission_date + timedelta(days=days)
        return format_date(due_date)
    except:
        return ""

# --- Función Principal de Generación ---
def generate_invoice_pdf(invoice_id):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=1.5*cm, bottomMargin=2*cm)
    story = []
    current_page_width = PAGE_WIDTH - doc.leftMargin - doc.rightMargin
    
    # Define logger
    logger_func = print
    if 'current_app' in globals() and hasattr(current_app, 'logger'):
        logger_func = current_app.logger

    try:
        invoice = None
        if db:
            invoice = Factura.query.options(
                db.joinedload(Factura.inquilino_ref),
                db.joinedload(Factura.propiedad_ref).joinedload(Propiedad.propietario_ref)
            ).get(invoice_id)
        elif 'mock_invoice_global' in globals() and globals()['mock_invoice_global'].id == invoice_id:
            invoice = globals()['mock_invoice_global']

        if not invoice:
            logger_func.error(f"generate_invoice_pdf: Factura con ID {invoice_id} no encontrada.")
            return None

        inquilino = invoice.inquilino_ref
        propiedad = invoice.propiedad_ref
        emisor = propiedad.propietario_ref if propiedad else None

        if not inquilino or not propiedad or not emisor:
            logger_func.error(f"generate_invoice_pdf: Datos incompletos para factura ID {invoice_id}")
            return None

        settings = None
        if 'g' in globals() and hasattr(g, 'settings'):
            settings = g.settings
        if not settings:
            if db: 
                settings = SystemSettings.query.get(1) or SystemSettings()
            else: 
                settings = SystemSettings()

    except Exception as e_data:
        logger_func.error(f"Error obteniendo datos para PDF factura {invoice_id}: {e_data}")
        return None

    # 1. Cabecera con título FACTURA y datos de la empresa
    header_left = [
        Paragraph("FACTURA", styles['InvoiceTitle']),
        Paragraph(f"Número: <b>{getattr(invoice, 'numero_factura_mostrado_al_cliente', 'SIN NÚMERO')}</b>", styles['InvoiceDetails']),
        Paragraph(f"Fecha: <b>{format_date(getattr(invoice, 'fecha_emision', None))}</b>", styles['InvoiceDetails'])
    ]

    # Datos de la empresa (lado derecho)
    emisor_nombre = getattr(emisor, 'nombre', "Nombre Emisor No Disponible")
    emisor_info_parts = []
    
    if hasattr(emisor, 'direccion') and emisor.direccion:
        emisor_info_parts.append(emisor.direccion)
    
    cp_ciudad = f"{getattr(emisor, 'codigo_postal', '')} {getattr(emisor, 'ciudad', '')}".strip()
    if cp_ciudad:
        emisor_info_parts.append(cp_ciudad)
    
    if hasattr(emisor, 'nif') and emisor.nif:
        emisor_info_parts.append(f"NIF/CIF: {emisor.nif}")
    
    if hasattr(emisor, 'telefono') and emisor.telefono:
        emisor_info_parts.append(f"Tel: {emisor.telefono}")

    header_right = [
        Paragraph(emisor_nombre, styles['CompanyName']),
        Paragraph("<br/>".join(emisor_info_parts), styles['CompanyDetails'])
    ]

    header_data = [[header_left, header_right]]
    header_table = Table(header_data, colWidths=[current_page_width * 0.45, current_page_width * 0.55])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0,0), (0,0), 0),
        ('RIGHTPADDING', (1,0), (1,0), 0),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 0.3*cm))

    # 2. Línea separadora elegante
    line_table = Table([['']], colWidths=[current_page_width])
    line_table.setStyle(TableStyle([
        ('LINEBELOW', (0, 0), (-1, -1), 2, PRIMARY_COLOR)
    ]))
    story.append(line_table)
    story.append(Spacer(1, 0.5*cm))

    # 3. Información del cliente y referencia de propiedad
    # Cliente (lado izquierdo)
    client_block = []
    client_block.append(Paragraph("FACTURAR A:", styles['SectionTitle']))
    
    client_name = getattr(inquilino, 'nombre', "Cliente N/A")
    client_block.append(Paragraph(client_name, styles['ClientName']))
    
    client_info_parts = []
    if hasattr(inquilino, 'direccion') and inquilino.direccion:
        client_info_parts.append(inquilino.direccion)
    
    cp_ciudad_cliente = f"{getattr(inquilino, 'codigo_postal', '')} {getattr(inquilino, 'ciudad', '')}".strip()
    if cp_ciudad_cliente:
        client_info_parts.append(cp_ciudad_cliente)
    
    if hasattr(inquilino, 'nif') and inquilino.nif:
        client_info_parts.append(f"NIF/CIF: {inquilino.nif}")
    
    if client_info_parts:
        client_block.append(Paragraph("<br/>".join(client_info_parts), styles['ClientDetails']))
    
    # Referencia (lado derecho)
    ref_block = []
    ref_block.append(Paragraph("REFERENTE A:", styles['SectionTitleRight']))
    
    ref_info_parts = []
    propiedad_dir = getattr(propiedad, 'direccion', "Dirección N/A")
    ref_info_parts.append(propiedad_dir)
    
    contrato_ref = getattr(invoice, 'contrato_ref', None)
    if contrato_ref and hasattr(contrato_ref, 'numero_contrato') and contrato_ref.numero_contrato:
        ref_info_parts.append(f"Contrato: {contrato_ref.numero_contrato}")
    
    if hasattr(propiedad, 'referencia_catastral') and propiedad.referencia_catastral:
        ref_info_parts.append(f"Ref. Catastral: {propiedad.referencia_catastral}")

    if hasattr(propiedad, 'descripcion') and propiedad.descripcion:
        ref_info_parts.append(propiedad.descripcion)
    
    ref_block.append(Paragraph("<br/>".join(ref_info_parts), styles['ClientDetailsRight']))
    
    # Tabla con cliente y referencia lado a lado
    client_ref_data = [[client_block, ref_block]]
    client_ref_table = Table(client_ref_data, colWidths=[current_page_width * 0.55, current_page_width * 0.45])
    client_ref_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0,0), (0,0), 0),
        ('RIGHTPADDING', (1,0), (1,0), 0),
        ('ALIGN', (1,0), (1,0), 'RIGHT'),
    ]))
    story.append(client_ref_table)
    story.append(Spacer(1, 0.8*cm))

    # 4. Tabla de conceptos con diseño mejorado
    concept_header = [
        [Paragraph("DESCRIPCIÓN", styles['TableHeader']), 
         Paragraph("IMPORTE", styles['TableHeaderRight'])]
    ]

    items_list = getattr(invoice, 'items', [])
    if not isinstance(items_list, list): 
        items_list = []

    concept_data = []
    
    if items_list:
        for item in items_list:
            if isinstance(item, dict):
                desc = item.get('description', 'N/A')
                total_item = format_currency(item.get('total', 0.0))
                
                concept_data.append([
                    Paragraph(desc, styles['TableCell']),
                    Paragraph(total_item, styles['TableCellRight'])
                ])
    else:
        concept_data.append([
            Paragraph('(Sin conceptos)', styles['TableCell']), 
            Paragraph('0,00 €', styles['TableCellRight'])
        ])

    # Combinar header y data
    all_table_data = concept_header + concept_data
    
    concept_table = Table(all_table_data, colWidths=[current_page_width * 0.72, current_page_width * 0.28])
    concept_table.setStyle(TableStyle([
        # Estilo del header
        ('BACKGROUND', (0, 0), (-1, 0), PRIMARY_COLOR),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('TOPPADDING', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('LEFTPADDING', (0, 0), (-1, -1), 15),
        ('RIGHTPADDING', (0, 0), (-1, -1), 15),
        
        # Estilo del contenido
        ('TOPPADDING', (0, 1), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 8),
        
        # Alternar colores de fila
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, ACCENT_COLOR]),
        
        # Bordes
        ('LINEBELOW', (0, 0), (-1, 0), 1, PRIMARY_COLOR),
        ('LINEBELOW', (0, -1), (-1, -1), 1, colors.HexColor('#E0E0E0')),
        
        # Alineación
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(concept_table)
    story.append(Spacer(1, 0.8*cm))

    # 5. Totales con diseño mejorado
    subtotal_val = getattr(invoice, 'subtotal', Decimal('0.00'))
    iva_val = getattr(invoice, 'iva', Decimal('0.00'))
    irpf_val = getattr(invoice, 'irpf', Decimal('0.00'))
    total_val = getattr(invoice, 'total', Decimal('0.00'))

    # Calcular porcentajes
    iva_rate = (iva_val / subtotal_val * 100) if subtotal_val and iva_val is not None and subtotal_val != Decimal('0.00') else Decimal('0.0')
    irpf_rate = (irpf_val / subtotal_val * 100) if subtotal_val and irpf_val is not None and subtotal_val != Decimal('0.00') else Decimal('0.0')

    totals_data = [
        [Paragraph('Base Imponible:', styles['TotalLabel']), 
         Paragraph(format_currency(subtotal_val), styles['TotalValue'])],
        [Paragraph(f'IVA ({iva_rate:.0f}%):'.replace('.', ','), styles['TotalLabel']), 
         Paragraph(format_currency(iva_val), styles['TotalValue'])]
    ]

    if irpf_val and irpf_val > 0:
        totals_data.append([
            Paragraph(f'Retención IRPF ({irpf_rate:.0f}%):'.replace('.', ','), styles['IRPFText']), 
            Paragraph(f"-{format_currency(irpf_val)}", styles['IRPFValue'])
        ])

    # Separador antes del total
    totals_data.append([Paragraph('', styles['TotalLabel']), Paragraph('', styles['TotalValue'])])
    
    totals_data.append([
        Paragraph('TOTAL A PAGAR:', styles['GrandTotalLabel']), 
        Paragraph(format_currency(total_val), styles['GrandTotalValue'])
    ])

    # Crear tabla de totales con fondo
    totals_table = Table(totals_data, colWidths=[4.5*cm, 3*cm])
    totals_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), SECONDARY_COLOR),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LINEABOVE', (0, -1), (-1, -1), 2, PRIMARY_COLOR),
        ('TOPPADDING', (0, -1), (-1, -1), 10),
        ('BOTTOMPADDING', (0, -1), (-1, -1), 10),
    ]))

    # Alinear tabla de totales a la derecha
    totals_container = Table([[None, totals_table]], 
                           colWidths=[current_page_width - 6.5*cm, 6.5*cm])
    totals_container.setStyle(TableStyle([
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
        ('VALIGN', (1, 0), (1, 0), 'TOP'),
    ]))
    
    story.append(totals_container)
    story.append(Spacer(1, 1*cm))

    # 6. Sección de pago y notas en columnas
    # Datos de pago (izquierda)
    payment_block = []
    payment_block.append(Paragraph("DATOS DE PAGO:", styles['PaymentTitle']))
    
    payment_info = []
    if hasattr(emisor, 'banco') and emisor.banco:
        payment_info.append(f"Banco: {emisor.banco}")
    else:
        payment_info.append("")
    
    if hasattr(emisor, 'cuenta_bancaria') and emisor.cuenta_bancaria:
        payment_info.append(f"IBAN: {emisor.cuenta_bancaria}")
    elif hasattr(emisor, 'iban') and emisor.iban:
        payment_info.append(f"IBAN: {emisor.iban}")
    else:
        payment_info.append("IBAN: ES00 0000 0000 0000 0000 0000")
    
    if hasattr(emisor, 'swift') and emisor.swift:
        payment_info.append(f"SWIFT/BIC: {emisor.swift}")

    payment_block.append(Paragraph("<br/>".join(payment_info), styles['PaymentDetails']))

    # Notas (derecha)
    notes_block = []
    notes_block.append(Paragraph("NOTAS:", styles['NotesTitle']))
    notas_text = "Forma de pago: Transferencia bancaria.<br/>Gracias por su negocio."
    
    if hasattr(invoice, 'notas') and invoice.notas:
        notas_text = invoice.notas
    elif settings and hasattr(settings, 'notas_factura') and settings.notas_factura:
        notas_text = settings.notas_factura
    
    notes_block.append(Paragraph(notas_text, styles['NotesText']))

    # Tabla con pago y notas lado a lado
    payment_notes_data = [[payment_block, notes_block]]
    payment_notes_table = Table(payment_notes_data, colWidths=[current_page_width * 0.5, current_page_width * 0.5])
    payment_notes_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0,0), (0,0), 0),
        ('RIGHTPADDING', (1,0), (1,0), 0),
        ('TOPPADDING', (0,0), (-1,0), 0),
        ('BOTTOMPADDING', (0,0), (-1,0), 0),
    ]))
    story.append(payment_notes_table)

    # 8. Construir el PDF
    try:
        logger_func.info(f"Construyendo PDF para factura ID {invoice_id}...")
        doc.build(story, onFirstPage=lambda c, d: _draw_footer(c, d, settings),
                         onLaterPages=lambda c, d: _draw_footer(c, d, settings))
        buffer.seek(0)
        logger_func.info(f"PDF generado exitosamente para factura {invoice_id}")
        return buffer
    except Exception as e_build:
        logger_func.error(f"Error construyendo PDF para factura {invoice_id}: {e_build}")
        return None

def _draw_footer(canvas, doc, system_settings):
    """Dibuja el pie de página con diseño mejorado"""
    canvas.saveState()
    
    # Línea separadora elegante
    canvas.setStrokeColor(colors.HexColor('#E0E0E0'))
    canvas.setLineWidth(1)
    canvas.line(doc.leftMargin, 2*cm, doc.width + doc.leftMargin, 2*cm)
    
    # Texto del pie
    footer_text = "Esta factura se emite de acuerdo con la legislación vigente."
    if system_settings and hasattr(system_settings, 'pie_factura') and system_settings.pie_factura:
        footer_text = system_settings.pie_factura
    
    p = Paragraph(footer_text, styles['FooterStyle'])
    w, h = p.wrapOn(canvas, doc.width, doc.bottomMargin)
    p.drawOn(canvas, doc.leftMargin, 1.2*cm)
    
    canvas.restoreState()

# --- Mock Data and Test Execution ---
if __name__ == '__main__':
    from datetime import datetime, date

    class MockLogger: 
        def _log(self, level, msg, exc_info=False):
            print(f"{level}: {msg}")
            if exc_info:
                import traceback
                traceback.print_exc()
        def info(self, msg): self._log("INFO", msg)
        def error(self, msg, exc_info=False): self._log("ERROR", msg, exc_info=exc_info)
        def warning(self, msg): self._log("WARNING", msg)

    class MockApp:
        def __init__(self):
            self.logger = MockLogger()

    class MockG:
        def __init__(self):
            self.settings = SystemSettings(pie_factura="Esta factura se emite de acuerdo con la legislación vigente.")

    current_app = MockApp() 
    g = MockG() 
    db = None
    
    mock_emisor = Propietario(
        id=1,
        nombre="INVERSIONES LADEIRA, S.L.",
        nif="B36422657",
        direccion="CL. Domingo Bueno, 4-2A",
        codigo_postal="36400",
        ciudad="O Porriño",
        email="inversionesladeira@gestem.net",
        telefono="630931925",
        cuenta_bancaria="ES53 2080 5054 0030 4002 7117",
        banco="Banco Ejemplo S.A.",
        swift="EJEMPLOESXXX"
    )
    
    mock_inquilino = Inquilino(
        id=1,
        nombre="ROBERTO OCAMPO CARDALDA",
        nif="35549915B",
        direccion="Cl/ Antonio Palacios, 6-3A",
        codigo_postal="36400",
        ciudad="O Porriño"
    )
    
    mock_propiedad = Propiedad(
        id=1,
        direccion="Cl/ Antonio Palacios, 6-3A",
        referencia_catastral="jjhghgh",
        propietario_ref=mock_emisor
    )
    
    class MockContrato:
        def __init__(self, numero_contrato):
            self.numero_contrato = numero_contrato

    mock_invoice_global = Factura(
        id=1, 
        numero_factura="INVDEF-1-1-2025-001", 
        numero_factura_mostrado_al_cliente="INVDEF-1-1-2025-001",
        fecha_emision=datetime.strptime("01/05/2025", "%d/%m/%Y").date(),
        inquilino_ref=mock_inquilino,
        propiedad_ref=mock_propiedad,
        contrato_ref=MockContrato(numero_contrato="CONTRATO-2025-001"),
        items=[ 
            {
                "description": "Alquiler Mayo 2025", 
                "quantity": 1, 
                "unitPrice": Decimal("640.73"), 
                "total": Decimal("640.73")
            },
            {
                "description": "Gasto: Cide Energía. Fra nº FE20255100978357", 
                "quantity": 1, 
                "unitPrice": Decimal("39.00"), 
                "total": Decimal("39.00")
            },
            {
                "description": "Gasto: Comercializadora Regulada, Gas & Power, S.A. Fra nº FE25137009343240", 
                "quantity": 1, 
                "unitPrice": Decimal("88.18"), 
                "total": Decimal("88.18")
            }
        ],
        subtotal=Decimal("767.91"),
        iva=Decimal("0.00"),
        irpf=Decimal("0.00"),
        total=Decimal("767.91"),
        contrato_id=1,
        propiedad_id=1,
        notas="Factura correspondiente al periodo de Mayo/2025. Incluye 2 gasto(s) repercutido(s)."
    )
    
    pdf_buffer = generate_invoice_pdf(1) 

    if pdf_buffer:
        with open("factura_mejorada.pdf", "wb") as f:
            f.write(pdf_buffer.getvalue())
        print("PDF generado: factura_mejorada.pdf")
    else:
        print("Error al generar el PDF.")