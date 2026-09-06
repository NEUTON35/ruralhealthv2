import argparse

from app import app
from security import generate_order_hash, verify_order_hash


def main():
    parser = argparse.ArgumentParser(description='Valida o genera hashes de ordenes medicas RuralHealth.')
    parser.add_argument('doctor_id', type=int)
    parser.add_argument('patient_id', type=int)
    parser.add_argument('--clinic-id', type=int, default=0)
    parser.add_argument('--meds-json', default='')
    parser.add_argument('--token', help='Hash/token de seguridad a validar.')
    args = parser.parse_args()

    with app.app_context():
        if args.token:
            is_valid = verify_order_hash(args.doctor_id, args.patient_id, args.token, args.meds_json, args.clinic_id)
            print('VALIDO' if is_valid else 'INVALIDO')
            return
        print(generate_order_hash(args.doctor_id, args.patient_id, args.meds_json, args.clinic_id))


if __name__ == '__main__':
    main()
