"""Emisión de documentos de cobro.

Qué resuelve
------------
El sistema cobraba con un flujo manual: el paciente subía la foto de una
transferencia y el médico la aprobaba. Eso registra que alguien pagó, pero no es
un documento de cobro: no tiene numeración autorizada, ni identificación fiscal
del emisor, ni constancia de por qué no se cobra IVA, ni forma de anularse.

Dos emisores, no uno
--------------------
Una consulta puede facturarla la clínica —persona jurídica, con su NIT— o el
médico independiente, que factura a su propio nombre con su propia resolución de
numeración. Son emisores distintos ante la DIAN y sus consecutivos **no pueden
mezclarse**: cada resolución autoriza un rango a un emisor concreto, y usar un
número fuera del rango asignado invalida el documento.

Por eso la numeración vive en `BillingProfile` y no en la clínica.

IVA
---
Los servicios de salud humana están excluidos de IVA (Estatuto Tributario,
artículo 476 numeral 1). "Excluido" no es "exento": no se cobra y no da derecho a
descontar. El documento debe decirlo, así que se consigna la nota en cada
factura en lugar de calcular un impuesto que no aplica.

Lo que este módulo NO hace
--------------------------
No envía nada a la DIAN. La radicación electrónica exige un proveedor tecnológico
autorizado y las credenciales del prestador. Aquí queda la interfaz
(`BillingProvider`) y un proveedor de marcador de posición que deja la factura
lista y marcada como pendiente de radicar, sin fingir que se envió.

Nada de este módulo determina las obligaciones tributarias de nadie. Guarda lo
que el titular declara y lo usa tal cual; quién está obligado a facturar
electrónicamente lo decide su contador.
"""

import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func

from models import (
    DOC_CREDIT_NOTE,
    DOC_ELECTRONIC_INVOICE,
    INVOICE_ANNULLED,
    INVOICE_DRAFT,
    INVOICE_ISSUED,
    ISSUER_CLINIC,
    ISSUER_INDEPENDENT,
    BillingProfile,
    Invoice,
    InvoiceLine,
    db,
)
from time_utils import colombia_now

CENT = Decimal('0.01')

# Nota que debe constar en el documento cuando no se cobra IVA.
VAT_EXCLUSION_NOTE = (
    'Servicio excluido de IVA conforme al articulo 476 numeral 1 del Estatuto '
    'Tributario (servicios de salud humana).'
)


class BillingError(Exception):
    """No se puede emitir el documento."""


class NumberingExhausted(BillingError):
    """El rango autorizado por la DIAN se agotó o venció."""


def money(value):
    """Convierte a decimal exacto con dos cifras.

    Todo importe pasa por aquí. El modelo anterior usaba `float`; a las
    magnitudes de esta aplicación no producía errores —lo comprobé— pero es el
    tipo equivocado para dinero y basta con acumular para que aparezcan.
    """
    if value is None:
        return Decimal('0.00')
    if isinstance(value, Decimal):
        return value.quantize(CENT, rounding=ROUND_HALF_UP)
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


# --- Perfil de facturación ---------------------------------------------------

def profile_for_doctor(doctor):
    """Perfil con el que factura un profesional.

    El médico independiente factura a su nombre; el vinculado a una clínica lo
    hace bajo el perfil de la institución. Si el independiente todavía no ha
    configurado el suyo, devuelve None: no se inventa un emisor.
    """
    if not doctor:
        return None

    if getattr(doctor, 'is_autonomous', False):
        return BillingProfile.query.filter_by(
            doctor_id=doctor.id, issuer_kind=ISSUER_INDEPENDENT, is_active=True,
        ).first()

    if doctor.clinic_id:
        return BillingProfile.query.filter_by(
            clinic_id=doctor.clinic_id, issuer_kind=ISSUER_CLINIC, is_active=True,
        ).first()

    return None


def describe_readiness(profile):
    """Qué le falta a un perfil para poder emitir. Lista legible, no booleano."""
    if profile is None:
        return ['No hay perfil de facturacion configurado.']

    faltan = []
    if not profile.legal_name:
        faltan.append('Falta la razon social o el nombre del profesional.')
    if not profile.document_number:
        faltan.append('Falta el NIT o la cedula del emisor.')
    if not profile.fiscal_address:
        faltan.append('Falta la direccion fiscal.')
    if not profile.has_numbering:
        faltan.append(
            'Falta la resolucion de numeracion de la DIAN con su rango autorizado.'
        )
    else:
        if profile.numbering_expired():
            faltan.append(
                f'La resolucion {profile.resolution_number} vencio el '
                f'{profile.resolution_valid_until}. Solicita una nueva ante la DIAN.'
            )
        if profile.numbering_exhausted():
            faltan.append(
                f'El rango autorizado ({profile.range_from}-{profile.range_to}) '
                'se agoto. Solicita un rango nuevo ante la DIAN.'
            )
        elif profile.range_to and profile.last_number:
            restantes = profile.range_to - profile.last_number
            if restantes <= 50:
                faltan.append(
                    f'Quedan {restantes} numeros en el rango autorizado. '
                    'Solicita uno nuevo antes de agotarlo.'
                )
    if not profile.provider_configured:
        faltan.append(
            'No hay proveedor tecnologico configurado: el documento se genera '
            'pero queda pendiente de radicar ante la DIAN.'
        )
    return faltan


# --- Numeración --------------------------------------------------------------

def _next_consecutive(profile):
    """Reserva el siguiente número del rango, de forma atómica.

    La numeración de una factura no admite huecos ni repeticiones: cada número
    dentro del rango autorizado debe usarse una sola vez. Con dos emisiones
    simultáneas, leer y luego escribir produciría dos facturas con el mismo
    número, que es un documento inválido ante la DIAN.

    Se resuelve con un UPDATE condicional: la comprobación del rango viaja
    dentro de la propia sentencia, igual que en el descuento de inventario.
    """
    if not profile.has_numbering:
        raise NumberingExhausted(
            'El emisor no tiene una resolucion de numeracion registrada.'
        )
    if profile.numbering_expired():
        raise NumberingExhausted(
            f'La resolucion {profile.resolution_number} vencio el '
            f'{profile.resolution_valid_until}.'
        )

    resultado = db.session.execute(
        db.text(
            'UPDATE billing_profile '
            'SET last_number = CASE '
            '        WHEN COALESCE(last_number, 0) < :desde THEN :desde '
            '        ELSE COALESCE(last_number, 0) + 1 END '
            'WHERE id = :perfil '
            '  AND (CASE '
            '        WHEN COALESCE(last_number, 0) < :desde THEN :desde '
            '        ELSE COALESCE(last_number, 0) + 1 END) <= :hasta'
        ),
        {'perfil': profile.id, 'desde': profile.range_from, 'hasta': profile.range_to},
    )
    if resultado.rowcount != 1:
        raise NumberingExhausted(
            f'El rango autorizado ({profile.range_from}-{profile.range_to}) se agoto. '
            'Solicita un rango nuevo ante la DIAN antes de seguir facturando.'
        )

    db.session.expire(profile)
    consecutivo = db.session.get(BillingProfile, profile.id).last_number
    return consecutivo, f'{profile.invoice_prefix or ""}{consecutivo}'


# --- Encadenamiento ----------------------------------------------------------

def _chain(invoice):
    """Encadena la factura por hash a la anterior del mismo emisor.

    Se excluye la propia factura y las que aún no tienen hash: el borrador ya
    tiene identificador cuando llega aquí, así que una consulta por id
    descendente sin filtrar se devolvería a sí misma y toda la cadena quedaría
    con eslabón previo nulo.
    """
    anterior = (
        db.session.query(Invoice.entry_hash)
        .filter(
            Invoice.billing_profile_id == invoice.billing_profile_id,
            Invoice.id != invoice.id,
            Invoice.entry_hash.isnot(None),
        )
        .order_by(Invoice.id.desc())
        .limit(1)
        .first()
    )
    previo = anterior[0] if anterior else None
    material = json.dumps({
        'perfil': invoice.billing_profile_id,
        'numero': invoice.number,
        'total': str(invoice.total),
        'paciente': invoice.patient_id,
        'emitida': invoice.issued_at.isoformat() if invoice.issued_at else None,
    }, sort_keys=True, separators=(',', ':'))
    invoice.previous_hash = previo
    invoice.entry_hash = hashlib.sha256(
        f'{previo or "GENESIS"}|{material}'.encode('utf-8')
    ).hexdigest()


# --- Emisión -----------------------------------------------------------------

def build_draft(profile, patient, lines, concept=None, clinic_id=None,
                appointment_id=None, chat_id=None, subscription_id=None,
                payment_ticket_id=None):
    """Crea el borrador. No consume numeración: eso ocurre solo al emitir.

    Args:
        lines: iterable de dicts con `description`, `quantity`, `unit_price`
            y, opcionalmente, `discount_amount` y `cups_code`.
    """
    if profile is None:
        raise BillingError('No hay perfil de facturacion configurado para el emisor.')
    if not lines:
        raise BillingError('La factura necesita al menos un renglon.')

    factura = Invoice(
        billing_profile_id=profile.id,
        clinic_id=clinic_id or profile.clinic_id,
        document_type=DOC_ELECTRONIC_INVOICE,
        patient_id=patient.id,
        buyer_name=patient.name,
        buyer_document_type=getattr(patient, 'document_type', None),
        buyer_document=patient.cedula,
        currency='COP',
        concept=(concept or '')[:300] or None,
        appointment_id=appointment_id,
        chat_id=chat_id,
        subscription_id=subscription_id,
        payment_ticket_id=payment_ticket_id,
        status=INVOICE_DRAFT,
        created_at=colombia_now(),
    )
    db.session.add(factura)
    db.session.flush()

    subtotal = Decimal('0.00')
    descuentos = Decimal('0.00')
    impuestos = Decimal('0.00')

    for renglon in lines:
        cantidad = money(renglon.get('quantity', 1))
        precio = money(renglon.get('unit_price', 0))
        descuento = money(renglon.get('discount_amount', 0))
        # Los servicios de salud no llevan IVA; se admite una tasa explicita por
        # si se factura algo que si lo lleve.
        tasa = money(renglon.get('tax_rate', 0))

        bruto = money(cantidad * precio)
        neto = money(bruto - descuento)
        impuesto = money(neto * tasa / Decimal('100'))

        db.session.add(InvoiceLine(
            invoice_id=factura.id,
            description=str(renglon.get('description', ''))[:300],
            cups_code=(renglon.get('cups_code') or None),
            quantity=cantidad,
            unit_price=precio,
            discount_amount=descuento,
            tax_rate=tasa,
            line_total=money(neto + impuesto),
        ))

        subtotal += bruto
        descuentos += descuento
        impuestos += impuesto

    factura.subtotal = money(subtotal)
    factura.discount_amount = money(descuentos)
    factura.tax_amount = money(impuestos)
    factura.total = money(subtotal - descuentos + impuestos)

    if impuestos == 0:
        factura.tax_exclusion_note = VAT_EXCLUSION_NOTE

    return factura


def issue(invoice, provider=None):
    """Asigna número, sella la factura y la entrega al proveedor.

    A partir de aquí el documento es inmutable: cualquier corrección se hace con
    una nota crédito, no editando la factura.
    """
    if invoice.status != INVOICE_DRAFT:
        raise BillingError(f'La factura ya no es un borrador (estado: {invoice.status}).')

    perfil = invoice.billing_profile
    consecutivo, numero = _next_consecutive(perfil)

    invoice.consecutive = consecutivo
    invoice.number = numero
    invoice.issued_at = colombia_now()
    invoice.status = INVOICE_ISSUED
    _chain(invoice)

    proveedor = provider or get_provider(perfil)
    resultado = proveedor.submit(invoice)
    invoice.provider_reference = resultado.get('reference')
    invoice.provider_response = json.dumps(resultado, ensure_ascii=False)[:2000]
    if resultado.get('cufe'):
        invoice.cufe = resultado['cufe']
    if resultado.get('status'):
        invoice.status = resultado['status']
    if resultado.get('sent'):
        invoice.sent_at = colombia_now()

    return invoice


def annul(invoice, reason, actor_id=None):
    """Anula una factura emitida mediante nota crédito.

    Una factura emitida no se borra ni se edita: se compensa con una nota
    crédito por el mismo valor, y ambas quedan en el registro.
    """
    if invoice.status == INVOICE_DRAFT:
        # Un borrador nunca se radicó: puede retirarse sin nota crédito.
        invoice.status = INVOICE_ANNULLED
        invoice.annulled_at = colombia_now()
        invoice.annulment_reason = (reason or '')[:300]
        return None

    if invoice.is_annulled:
        raise BillingError('La factura ya estaba anulada.')
    if not (reason or '').strip():
        raise BillingError('La anulacion requiere un motivo escrito.')

    invoice.annulled_at = colombia_now()
    invoice.annulment_reason = reason[:300]
    invoice.status = INVOICE_ANNULLED

    nota = Invoice(
        billing_profile_id=invoice.billing_profile_id,
        clinic_id=invoice.clinic_id,
        document_type=DOC_CREDIT_NOTE,
        patient_id=invoice.patient_id,
        buyer_name=invoice.buyer_name,
        buyer_document_type=invoice.buyer_document_type,
        buyer_document=invoice.buyer_document,
        currency=invoice.currency,
        subtotal=invoice.subtotal,
        discount_amount=invoice.discount_amount,
        tax_amount=invoice.tax_amount,
        total=invoice.total,
        tax_exclusion_note=invoice.tax_exclusion_note,
        concept=f'Nota credito de la factura {invoice.number}: {reason[:200]}',
        credit_note_for_id=invoice.id,
        status=INVOICE_DRAFT,
        created_at=colombia_now(),
    )
    db.session.add(nota)
    db.session.flush()

    for linea in invoice.lines:
        db.session.add(InvoiceLine(
            invoice_id=nota.id,
            description=linea.description,
            cups_code=linea.cups_code,
            quantity=linea.quantity,
            unit_price=linea.unit_price,
            discount_amount=linea.discount_amount,
            tax_rate=linea.tax_rate,
            line_total=linea.line_total,
        ))

    return nota


def verify_chain(billing_profile_id):
    """Recorre las facturas del emisor y devuelve los eslabones rotos."""
    facturas = (
        Invoice.query
        .filter(Invoice.billing_profile_id == billing_profile_id,
                Invoice.entry_hash.isnot(None))
        .order_by(Invoice.id.asc())
        .all()
    )
    rotos = []
    esperado = None
    for factura in facturas:
        if esperado is not None and factura.previous_hash != esperado:
            rotos.append({'id': factura.id, 'numero': factura.number})
        esperado = factura.entry_hash
    return rotos


def totals_for_profile(billing_profile_id, since=None, until=None):
    """Totales facturados, excluyendo lo anulado. Base del registro contable."""
    consulta = db.session.query(
        func.count(Invoice.id),
        func.coalesce(func.sum(Invoice.total), 0),
    ).filter(
        Invoice.billing_profile_id == billing_profile_id,
        Invoice.status != INVOICE_ANNULLED,
        Invoice.document_type != DOC_CREDIT_NOTE,
        Invoice.issued_at.isnot(None),
    )
    if since:
        consulta = consulta.filter(Invoice.issued_at >= since)
    if until:
        consulta = consulta.filter(Invoice.issued_at <= until)

    cantidad, total = consulta.one()
    return {'documentos': int(cantidad or 0), 'total': money(total or 0)}


# --- Proveedor tecnológico ---------------------------------------------------

class BillingProvider:
    """Interfaz de un proveedor de facturación electrónica.

    Para conectar un facturador real, se implementa `submit` y se registra con
    `register_provider`. La aplicación no necesita conocer nada más.
    """

    name = 'base'

    def submit(self, invoice):
        """Radica el documento. Devuelve dict con `reference`, `cufe`, `status`, `sent`."""
        raise NotImplementedError


class PendingProvider(BillingProvider):
    """Proveedor por defecto: **no radica nada**.

    Deja la factura emitida y numerada, pero explícitamente sin enviar. Es
    deliberado: fingir una radicación que no ocurrió haría creer al prestador
    que cumplió una obligación que sigue pendiente.
    """

    name = 'pendiente'

    def submit(self, invoice):
        return {
            'reference': None,
            'cufe': None,
            'status': INVOICE_ISSUED,
            'sent': False,
            'nota': (
                'Documento generado y numerado, pendiente de radicar ante la DIAN. '
                'Configura un proveedor tecnologico autorizado para la radicacion '
                'electronica.'
            ),
        }


_PROVIDERS = {'pendiente': PendingProvider()}


def register_provider(provider):
    _PROVIDERS[provider.name] = provider
    return provider


def get_provider(profile):
    if profile and profile.provider_name and profile.provider_name in _PROVIDERS:
        return _PROVIDERS[profile.provider_name]
    return _PROVIDERS['pendiente']
