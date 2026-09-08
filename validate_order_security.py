#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Verifica el sello criptográfico de una orden médica.

Para qué sirve
--------------
Una orden médica lleva un sello que liga al profesional, al paciente, a la
institución y a la lista exacta de medicamentos. Si alguien altera una cantidad
o cambia un fármaco después de firmada, el sello deja de coincidir.

Esta herramienta permite comprobarlo desde fuera de la aplicación, que es lo que
hace falta cuando hay una discusión sobre una orden concreta: una auditoría, un
reclamo, o una entrega que el establecimiento farmacéutico no reconoce.

Cómo se usa
-----------
Verificar una orden que ya está en la base de datos, por su número:

    python validate_order_security.py --order 1234

Verificar un sello suelto, indicando a mano lo que debería contener:

    python validate_order_security.py 7 42 --clinic-id 1 \\
        --meds-json '[{"nombre_med":"Amoxicilina","cantidad":21}]' \\
        --token 'eyJ...abc.def...'

Códigos de salida
-----------------
    0   el sello es válido
    1   el sello NO es válido, o la orden no existe
    2   error de uso

Se distinguen porque la herramienta está pensada para usarse dentro de guiones
de auditoría. Una que imprima «INVALIDO» y salga con cero obliga a analizar su
salida como texto, y ese análisis se rompe el día que cambie una palabra.

Advertencia sobre los datos
---------------------------
La opción `--order` muestra los medicamentos de la orden, que son datos
clínicos. No pegue esa salida en un ticket ni en un chat.
"""

import argparse
import json
import sys


def _cargar_orden(order_id):
    from models import MedicalOrder
    return MedicalOrder.query.filter_by(id=order_id).execution_options(
        include_all_clinics=True).first()


def _verificar_orden(args):
    from security import verify_order_hash
    from time_utils import colombia_now

    orden = _cargar_orden(args.order)
    if orden is None:
        print('No existe la orden %s.' % args.order)
        return 1

    token = orden.hash_seguridad or orden.verification_hash
    if not token:
        print('La orden %s no tiene sello. Fue creada antes de que se firmaran '
              'las órdenes, o el sello se perdió.' % orden.id)
        return 1

    valido = verify_order_hash(orden.doctor_id, orden.patient_id, token,
                               orden.meds_json, orden.clinic_id)

    print('Orden:        %s' % (orden.order_number or orden.id))
    print('Profesional:  %s' % orden.doctor_id)
    print('Paciente:     %s' % orden.patient_id)
    print('Institución:  %s' % orden.clinic_id)
    print('Emitida:      %s' % (orden.signed_at or orden.created_at))
    print('Vence:        %s' % (orden.expires_at or 'sin fecha'))

    if orden.annulled_at:
        print('Anulada:      %s' % orden.annulled_at)
        print('Motivo:       %s' % (orden.annulment_reason or 'sin motivo registrado'))

    if orden.expires_at and orden.expires_at < colombia_now():
        print('Estado:       VENCIDA')

    if args.show_meds:
        try:
            medicamentos = json.loads(orden.meds_json or '[]')
        except (ValueError, TypeError):
            medicamentos = []
        print('Medicamentos:')
        for med in medicamentos:
            print('   %s x%s' % (med.get('nombre_med', '?'), med.get('cantidad', '?')))

    print()
    print('Sello: %s' % ('VALIDO' if valido else 'INVALIDO'))
    if not valido:
        print()
        print('Un sello inválido significa que alguno de estos datos no coincide')
        print('con lo que se firmó: profesional, paciente, institución, la lista')
        print('de medicamentos, o que la orden ya venció.')
    return 0 if valido else 1


def _verificar_token(args):
    from security import generate_signed_order_hash, verify_order_hash

    if args.token:
        valido = verify_order_hash(args.doctor_id, args.patient_id, args.token,
                                   args.meds_json, args.clinic_id)
        print('VALIDO' if valido else 'INVALIDO')
        return 0 if valido else 1

    # Sin `--token` la herramienta genera uno. Sirve para reproducir el sello
    # de una orden y compararlo a mano.
    print(generate_signed_order_hash(args.doctor_id, args.patient_id,
                                     args.meds_json, args.clinic_id))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog='validate_order_security.py',
        description='Verifica el sello criptográfico de una orden médica.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('--order', type=int,
                        help='Número interno de la orden en la base de datos.')
    parser.add_argument('--show-meds', action='store_true',
                        help='Muestra los medicamentos. Son datos clínicos.')
    parser.add_argument('doctor_id', type=int, nargs='?',
                        help='Identificador del profesional (modo manual).')
    parser.add_argument('patient_id', type=int, nargs='?',
                        help='Identificador del paciente (modo manual).')
    parser.add_argument('--clinic-id', type=int, default=0)
    parser.add_argument('--meds-json', default='')
    parser.add_argument('--token', help='Sello a verificar. Sin él, se genera uno.')
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.order is None and (args.doctor_id is None or args.patient_id is None):
        parser.error('Indique --order, o bien doctor_id y patient_id.')

    from app import app
    with app.app_context():
        if args.order is not None:
            return _verificar_orden(args)
        return _verificar_token(args)


if __name__ == '__main__':
    sys.exit(main())
