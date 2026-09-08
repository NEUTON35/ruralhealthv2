# -*- coding: utf-8 -*-
"""Traduce a español los estados que la base guarda en inglés.

La aplicación se usa en Colombia, por personal de salud que no tiene por qué
leer inglés, y hasta ahora la interfaz mostraba el valor crudo de la columna:
un profesional veía «OPEN» sobre una consulta abierta, «pending» y «attended»
en el historial de citas, y «no_show» cuando el paciente no llegó.

Los valores de la base no se tocan. Cambiarlos obligaría a migrar datos y a
revisar cada comparación del código, y los estados también viajan al RIPS y al
IHCE. Lo que cambia es solo lo que se muestra.

Falta deliberada de sorpresas: si aparece un estado que no está en la tabla,
se devuelve tal cual en lugar de una cadena vacía. Un estado desconocido en
pantalla es feo; un estado invisible es peligroso.
"""

ESTADOS = {
    # --- Consultas y chats ---
    'open': 'Abierta',
    'closed': 'Cerrada',
    'abierta': 'Abierta',
    'cerrada': 'Cerrada',

    # --- Citas ---
    'pending': 'Pendiente',
    'attended': 'Atendida',
    'no_show': 'No asistió',
    'in_progress': 'En curso',
    'booked': 'Agendada',
    'available': 'Disponible',
    'cancelada': 'Cancelada',
    'cancelado': 'Cancelado',
    'atendida': 'Atendida',
    'pendiente': 'Pendiente',

    # --- Órdenes y tiquetes de entrega ---
    'autorizado': 'Autorizado',
    'autorizada': 'Autorizada',
    'entregado': 'Entregado',
    'recibido': 'Recibido',
    'recibida': 'Recibida',
    'en_camino': 'En camino',
    'sin_stock': 'Sin existencias',
    'pendiente_stock': 'Pendiente de existencias',
    'parcial': 'Entrega parcial',
    'partial': 'Entrega parcial',
    'full': 'Completa',
    'none': 'Sin entregar',
    'vencido': 'Vencido',
    'anulada': 'Anulada',
    'activa': 'Activa',
    'redeemed': 'Redimido',
    'unused': 'Sin usar',

    # --- Pagos y aprobaciones ---
    'approved': 'Aprobado',
    'rejected': 'Rechazado',

    # --- Transferencias entre farmacias ---
    'solicitada': 'Solicitada',
    'aprobada': 'Aprobada',
    'completada': 'Completada',
    'rechazada': 'Rechazada',

    # --- PQRS y farmacovigilancia ---
    'reportada': 'Reportada',
    'resuelta': 'Resuelta',
    'descartada': 'Descartada',
    'radicada': 'Radicada',
    'en_tramite': 'En trámite',

    # --- Cuentas, clínicas y afiliaciones ---
    'active': 'Activa',
    'inactive': 'Inactiva',
    'suspended': 'Suspendida',
    'expired': 'Vencida',
}


def traducir(valor):
    """Devuelve el estado en español; si no se conoce, el valor original."""
    if valor is None:
        return ''
    clave = str(valor).strip().lower()
    return ESTADOS.get(clave, str(valor))
