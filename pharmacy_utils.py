import math

from flask import current_app

from sqlalchemy import func

from models import Pharmacy, ReplenishmentAlert, Stock, db


def available_quantity(stock):
    if not stock:
        return 0
    return max(0, (stock.cantidad or 0) - (stock.cantidad_comprometida or 0))


def distance_km(origin_lat, origin_lng, dest_lat, dest_lng):
    if None in (origin_lat, origin_lng, dest_lat, dest_lng):
        return None
    radius_km = 6371.0
    lat1 = math.radians(float(origin_lat))
    lat2 = math.radians(float(dest_lat))
    delta_lat = math.radians(float(dest_lat) - float(origin_lat))
    delta_lng = math.radians(float(dest_lng) - float(origin_lng))
    a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lng / 2) ** 2
    return round(radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 1)


def ensure_default_pharmacy(clinic):
    pharmacy = Pharmacy.query.filter_by(clinic_id=clinic.id).order_by(Pharmacy.id.asc()).first()
    if pharmacy:
        return pharmacy
    pharmacy = Pharmacy(
        clinic_id=clinic.id,
        name="Farmacia principal",
        address=clinic.location,
        is_active=True,
    )
    db.session.add(pharmacy)
    db.session.flush()
    return pharmacy


def pharmacies_for_clinic(clinic_id, active_only=True, patient=None):
    query = Pharmacy.query.filter_by(clinic_id=clinic_id)
    if active_only:
        query = query.filter_by(is_active=True)
    pharmacies = query.order_by(Pharmacy.name.asc()).all()

    if patient:
        # La distancia se calcula una vez por farmacia, no dos.
        #
        # La version anterior llamaba `pharmacy_distance` dos veces dentro de la
        # misma clave de ordenacion —una para comparar y otra para el valor por
        # defecto— y `sort` evalua la clave por elemento, asi que cada farmacia
        # pagaba dos veces la trigonometria. Con pocas farmacias no se nota;
        # con la red completa de un municipio, si.
        def orden(pharmacy):
            distancia = pharmacy_distance(patient, pharmacy)
            # Las farmacias sin coordenadas van al final, no al principio.
            return (distancia is None, distancia if distancia is not None else 0.0)

        pharmacies.sort(key=orden)

    return pharmacies


def pharmacy_distance(patient, pharmacy):
    return distance_km(
        getattr(patient, "latitude", None),
        getattr(patient, "longitude", None),
        pharmacy.latitude,
        pharmacy.longitude,
    )


def doctor_distance(patient, doctor):
    return distance_km(
        getattr(patient, "latitude", None),
        getattr(patient, "longitude", None),
        getattr(doctor, "office_latitude", None),
        getattr(doctor, "office_longitude", None),
    )


def doctor_visible_on_map(doctor):
    return bool(
        getattr(doctor, "show_office_on_map", False)
        and getattr(doctor, "office_latitude", None) is not None
        and getattr(doctor, "office_longitude", None) is not None
    )


def nearest_pharmacy(pharmacies, patient):
    """Farmacia mas cercana al paciente.

    Devuelve la primera de la lista si no hay coordenadas con las que comparar:
    sin ubicacion no hay "mas cercana", y elegir una al azar seria peor que
    elegir la primera de forma predecible.
    """
    if not pharmacies:
        return None

    def orden(pharmacy):
        distancia = pharmacy_distance(patient, pharmacy)
        return (distancia is None, distancia if distancia is not None else 0.0)

    return min(pharmacies, key=orden)


def stock_for_med(clinic_id, med_name, pharmacy_id=None):
    query = Stock.query.filter(
        Stock.clinic_id == clinic_id,
        (func.lower(Stock.nombre_med) == med_name.lower()) | (func.lower(Stock.medicamento) == med_name.lower()),
    )
    if pharmacy_id:
        query = query.filter(Stock.pharmacy_id == pharmacy_id)
    return query.first()


def build_stock_matrix(clinic_id, meds, patient=None):
    if not meds:
        return []
        
    pharmacies = pharmacies_for_clinic(clinic_id, active_only=True, patient=patient)
    if not pharmacies:
        return []
        
    pharmacy_ids = [p.id for p in pharmacies]
    med_names = {med["nombre_med"].strip().lower() for med in meds}
    
    # Batch query all stocks for all relevant pharmacies and meds at once
    stocks = Stock.query.filter(
        Stock.clinic_id == clinic_id,
        Stock.pharmacy_id.in_(pharmacy_ids),
        (func.lower(Stock.nombre_med).in_(list(med_names)) | func.lower(Stock.medicamento).in_(list(med_names)))
    ).all()
    
    # Index stocks in a fast lookup dictionary
    stock_lookup = {}
    for s in stocks:
        if s.nombre_med:
            stock_lookup[(s.pharmacy_id, s.nombre_med.strip().lower())] = s
        if s.medicamento:
            stock_lookup[(s.pharmacy_id, s.medicamento.strip().lower())] = s
            
    rows = []
    for pharmacy in pharmacies:
        med_rows = []
        can_fulfill_all = True
        any_available = False
        for med in meds:
            med_name_lower = med["nombre_med"].strip().lower()
            stock = stock_lookup.get((pharmacy.id, med_name_lower))
            
            available = available_quantity(stock)
            required = med["cantidad"]
            can_fulfill = available >= required
            if available > 0:
                any_available = True
            if not can_fulfill:
                can_fulfill_all = False
            med_rows.append({
                "med": med,
                "stock": stock,
                "available": available,
                "required": required,
                "can_fulfill": can_fulfill,
            })
        rows.append({
            "pharmacy": pharmacy,
            "distance_km": pharmacy_distance(patient, pharmacy) if patient else None,
            "meds": med_rows,
            "can_fulfill_all": bool(meds) and can_fulfill_all,
            "any_available": any_available,
        })
    return rows


def create_replenishment_alert(clinic_id, pharmacy_id, med, available, order_id=None, ticket_id=None, note=None):
    from models import User
    from time_utils import colombia_now
    alert = ReplenishmentAlert(
        clinic_id=clinic_id,
        pharmacy_id=pharmacy_id,
        order_id=order_id,
        ticket_id=ticket_id,
        med_name=med["nombre_med"],
        required_quantity=med["cantidad"],
        available_quantity=max(0, available),
        note=note,
    )
    db.session.add(alert)
    
    # Notificar a los administradores.
    #
    # El fallo al notificar no debe impedir que la alerta de reposicion se cree:
    # lo importante es que quede registrado el faltante. Pero tampoco debe
    # desaparecer sin rastro, que es lo que hacia el `print` anterior — en
    # produccion la salida estandar de un worker de Gunicorn no la lee nadie.
    try:
        from models import Notification
        admins = User.query.filter_by(clinic_id=clinic_id, role='admin').all()
        pharmacy = db.session.get(Pharmacy, pharmacy_id)
        pharmacy_name = pharmacy.name if pharmacy else 'la farmacia'

        for admin in admins:
            db.session.add(Notification(
                user_id=admin.id,
                clinic_id=clinic_id,
                title='Alerta de reposicion de inventario',
                message=(
                    f"Se requiere reponer {med['nombre_med']} en {pharmacy_name}. "
                    f"Solicitado: {med['cantidad']}, disponible: {max(0, available)}."
                ),
                type='stock_alert',
                timestamp=colombia_now(),
            ))
    except Exception:
        current_app.logger.exception(
            'No se pudo notificar la alerta de reposicion de %s en la farmacia %s',
            med.get('nombre_med'), pharmacy_id,
        )

    return alert
