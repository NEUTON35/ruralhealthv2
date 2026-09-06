import math

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
        pharmacies.sort(key=lambda pharmacy: pharmacy_distance(patient, pharmacy) if pharmacy_distance(patient, pharmacy) is not None else 999999)
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
    if not pharmacies:
        return None
    ordered = sorted(pharmacies, key=lambda pharmacy: pharmacy_distance(patient, pharmacy) if pharmacy_distance(patient, pharmacy) is not None else 999999)
    return ordered[0]


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
    
    # Notificar a los administradores
    try:
        from models import Notification
        admins = User.query.filter_by(clinic_id=clinic_id, role='admin').all()
        pharmacy = Pharmacy.query.get(pharmacy_id)
        pharmacy_name = pharmacy.name if pharmacy else "Farmacia"
        
        for admin in admins:
            notif = Notification(
                user_id=admin.id,
                clinic_id=clinic_id,
                title="Alerta de Stock (Reposición)",
                message=f"Se requiere reponer {med['nombre_med']} en {pharmacy_name}. Solicitado: {med['cantidad']}, Disponible: {max(0, available)}.",
                type="stock_alert",
                timestamp=colombia_now()
            )
            db.session.add(notif)
    except Exception as e:
        print(f"Error creating notification: {e}")
        
    return alert
