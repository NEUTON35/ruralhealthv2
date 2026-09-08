import hashlib
import io
import json
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from models import ACCESS_ACTIVE, ALERTA_POR_PUNTO_REPOSICION, APPOINTMENT_FREEING_STATUSES, ACCESS_REVOKED, Appointment, Chat, ClinicAccessCode, InventoryItem, MedicationPickupTicket, Message, PasswordResetToken, PatientDoctorSubscription, Pharmacy, PolicyNetworkProvider, Rating, ReplenishmentAlert, ROLE_DOCTOR, ROLE_EXPENDOR, ROLE_RECEPTIONIST, ROLE_STAFF, STAFF_ROLE_VALUES, Stock, StockTransferRequest, User, UserClinicAccess, UserPolicyEnrollment, db
from pharmacy_utils import (alerta_por_punto_de_reposicion, available_quantity,
                           estado_de_stock, resolver_alerta_de_punto_de_reposicion)
from pharmacy_utils import ensure_default_pharmacy
from security import audit, hash_password, pii_hash, role_required, validate_password
from time_utils import colombia_now
import bleach
from monetization import active_clinic_access, generate_human_code, remaining_label, normalize_code
from rips_service import RIPSValidationError, generate_rips

admin_bp = Blueprint('admin', __name__)


@admin_bp.route('/dashboard', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def dashboard():
    default_pharmacy = ensure_default_pharmacy(current_user.clinic)
    db.session.commit()

    if request.method == 'POST':
        action = request.form.get('action')
        pharmacies = Pharmacy.query.filter_by(clinic_id=current_user.clinic_id).order_by(Pharmacy.name.asc()).all()
        pharmacy_ids = {pharmacy.id for pharmacy in pharmacies}
        if action == 'add_doctor':
            username = request.form.get('username', '').strip()
            name = request.form.get('name', '').strip()
            specialty = request.form.get('specialty', '').strip()
            password = request.form.get('password') or ''
            cedula = request.form.get('cedula', '').strip()
            phone = (request.form.get('phone') or '').strip()
            email = (request.form.get('email') or '').strip()
            office_address = (request.form.get('office_address') or '').strip()[:220]
            office_latitude = request.form.get('office_latitude', type=float)
            office_longitude = request.form.get('office_longitude', type=float)
            medical_registration = (request.form.get('medical_registration') or '').strip()[:50]
            password_error = validate_password(password, username=username, name=name)
            cedula_hash = pii_hash(cedula)

            if password_error:
                flash(password_error)
            elif not medical_registration:
                # Sin registro profesional la cuenta podria emitir ordenes con
                # validez legal firmadas por alguien no identificable como medico.
                flash(
                    'El numero de registro medico profesional es obligatorio para '
                    'crear una cuenta de medico.'
                )
            elif User.query.filter_by(username=username).first():
                flash('El nombre de usuario ya existe.')
            elif User.query.filter_by(cedula_hash=cedula_hash).first():
                flash('Esta cedula ya esta registrada en el sistema.')
            else:
                new_doc = User(
                    username=username,
                    clinic_id=current_user.clinic_id,
                    name=name,
                    specialty=specialty,
                    medical_registration=medical_registration,
                    password=hash_password(password),
                    role=ROLE_DOCTOR,
                    cedula=cedula,
                    cedula_hash=cedula_hash,
                    created_at=colombia_now(),
                    # La cuenta nace con la clave que escribio el administrador;
                    # el titular debe sustituirla en su primer ingreso.
                    must_change_password=True,
                    phone=phone or None,
                    email=email or None,
                    office_address=office_address or None,
                    office_latitude=office_latitude,
                    office_longitude=office_longitude,
                    show_office_on_map=bool(office_latitude and office_longitude),
                    affiliation_type='clinic',
                    affiliation_name=current_user.clinic.name if current_user.clinic else None,
                )
                db.session.add(new_doc)
                audit('doctor_created', details=f'username={username}')
                db.session.commit()
                flash('Doctor agregado exitosamente')

        elif action == 'add_staff':
            username = request.form.get('username', '').strip()
            name = request.form.get('name', '').strip()
            cedula = request.form.get('cedula', '').strip()
            password = request.form.get('password') or ''
            role = request.form.get('role')
            atencion_inicio = request.form.get('atencion_inicio') or None
            atencion_fin = request.form.get('atencion_fin') or None
            pharmacy_id = request.form.get('pharmacy_id', type=int)
            password_error = validate_password(password, username=username, name=name)
            if role not in {ROLE_DOCTOR, ROLE_STAFF, ROLE_RECEPTIONIST, ROLE_EXPENDOR}:
                flash('Rol no permitido para administración de clínica.')
            elif password_error:
                flash(password_error)
            elif User.query.filter_by(username=username).first():
                flash('El nombre de usuario ya existe')
            elif User.query.filter_by(cedula_hash=pii_hash(cedula)).first():
                flash('Esta cédula ya está registrada en el sistema')
            else:
                staff = User(
                    clinic_id=current_user.clinic_id,
                    username=username,
                    name=name,
                    cedula=cedula,
                    cedula_hash=pii_hash(cedula),
                    password=hash_password(password),
                    role=role,
                    created_at=colombia_now(),
                    must_change_password=True,
                    atencion_inicio=atencion_inicio,
                    atencion_fin=atencion_fin,
                    pharmacy_id=pharmacy_id if role == ROLE_EXPENDOR and pharmacy_id in pharmacy_ids else None,
                )
                db.session.add(staff)
                audit('clinic_staff_created', details=f'username={username}; role={role}')
                db.session.commit()
                flash('Cuenta de staff creada.')

        elif action == 'add_pharmacy':
            name = bleach.clean((request.form.get('name') or '').strip())[:180]
            address = (request.form.get('address') or '').strip()[:220]
            phone = (request.form.get('phone') or '').strip()[:80]
            latitude = request.form.get('latitude', type=float)
            longitude = request.form.get('longitude', type=float)
            if not name:
                flash('La farmacia necesita un nombre.')
            else:
                pharmacy = Pharmacy(
                    clinic_id=current_user.clinic_id,
                    name=name,
                    address=address or None,
                    phone=phone or None,
                    latitude=latitude,
                    longitude=longitude,
                    is_active=True,
                )
                db.session.add(pharmacy)
                audit('pharmacy_created', details=f'name={name}')
                db.session.commit()
                flash('Farmacia agregada.')

        elif action == 'update_pharmacy':
            pharmacy_id = request.form.get('pharmacy_id', type=int)
            pharmacy = Pharmacy.query.filter_by(id=pharmacy_id, clinic_id=current_user.clinic_id).first()
            if not pharmacy:
                flash('Farmacia no encontrada.')
            else:
                pharmacy.name = (request.form.get('name') or pharmacy.name).strip()[:180]
                pharmacy.address = (request.form.get('address') or '').strip()[:220] or None
                pharmacy.phone = (request.form.get('phone') or '').strip()[:80] or None
                pharmacy.latitude = request.form.get('latitude', type=float)
                pharmacy.longitude = request.form.get('longitude', type=float)
                pharmacy.is_active = request.form.get('is_active') == 'on'
                audit('pharmacy_updated', details=f'pharmacy_id={pharmacy.id}')
                db.session.commit()
                flash('Farmacia actualizada.')

        elif action == 'assign_expendedor':
            user_id = request.form.get('user_id', type=int)
            pharmacy_id = request.form.get('pharmacy_id', type=int)
            user = User.query.filter_by(id=user_id, clinic_id=current_user.clinic_id, role=ROLE_EXPENDOR).first()
            if not user or pharmacy_id not in pharmacy_ids:
                flash('Asignacion invalida.')
            else:
                user.pharmacy_id = pharmacy_id
                audit('expendedor_pharmacy_assigned', details=f'user_id={user.id}; pharmacy_id={pharmacy_id}')
                db.session.commit()
                flash('Expendedor reasignado.')

        elif action == 'create_clinic_access_code':
            raw_code = normalize_code(request.form.get('code'))
            code = raw_code or generate_human_code('CLINIC')
            max_uses = max(1, min(request.form.get('max_uses', type=int) or 1, 500))
            duration_days = request.form.get('duration_days', type=int)
            description = (request.form.get('description') or '').strip()[:220] or None
            if ClinicAccessCode.query.filter_by(code=code).first():
                flash('Ese codigo ya existe. Usa otro o deja el campo vacio para generar uno.')
            else:
                access_code = ClinicAccessCode(
                    clinic_id=current_user.clinic_id,
                    code=code,
                    description=description,
                    max_uses=max_uses,
                    duration_days=duration_days if duration_days and duration_days > 0 else None,
                    created_by_user_id=current_user.id,
                )
                db.session.add(access_code)
                audit('clinic_access_code_created', details=f'code_id=pending; max_uses={max_uses}')
                db.session.commit()
                flash(f'Codigo creado: {code}')

        elif action == 'revoke_clinic_access':
            access_id = request.form.get('access_id', type=int)
            access = UserClinicAccess.query.filter_by(id=access_id, clinic_id=current_user.clinic_id).first()
            if access:
                access.status = ACCESS_REVOKED
                access.revoked_at = colombia_now()
                access.revoked_by_user_id = current_user.id
                audit('clinic_access_revoked', details=f'access_id={access.id}; user_id={access.user_id}')
                db.session.commit()
                flash('Acceso revocado.')

        elif action == 'set_punto_reposicion':
            # El administrador puede fijarlo para cualquier sede de su clinica.
            # El expendedor solo para la suya; los dos escriben el mismo campo.
            stock_id = request.form.get('stock_id', type=int)
            minimo = max(0, request.form.get('cantidad_minima', type=int) or 0)
            item = Stock.query.filter_by(
                id=stock_id, clinic_id=current_user.clinic_id).first()
            if item is None:
                flash('Ese medicamento no está en el inventario de la clínica.')
            else:
                anterior = item.cantidad_minima or 0
                item.cantidad_minima = minimo
                db.session.flush()
                alerta_por_punto_de_reposicion(item)
                resolver_alerta_de_punto_de_reposicion(item)
                audit('stock_minimo_actualizado',
                      details=f'stock_id={item.id}; antes={anterior}; ahora={minimo}')
                db.session.commit()
                flash(f'Punto de reposición de {item.nombre_med}: {minimo}.')

        elif action == 'resolve_replenishment_alert':
            alert_id = request.form.get('alert_id', type=int)
            alert = ReplenishmentAlert.query.filter_by(id=alert_id, clinic_id=current_user.clinic_id).first()
            if alert:
                alert.status = 'resuelta'
                alert.resolved_at = colombia_now()
                audit('replenishment_alert_resolved', details=f'alert_id={alert.id}')
                db.session.commit()
                flash('Alerta marcada como resuelta.')

        elif action == 'resolve_transfer':
            transfer_id = request.form.get('transfer_id', type=int)
            status = request.form.get('status')
            transfer = StockTransferRequest.query.filter_by(id=transfer_id, clinic_id=current_user.clinic_id).first()
            if transfer and status in {'aprobada', 'rechazada', 'completada'}:
                transfer.status = status
                if status in {'rechazada', 'completada'}:
                    transfer.resolved_at = colombia_now()
                audit('stock_transfer_updated', details=f'transfer_id={transfer.id}; status={status}')
                db.session.commit()
                flash('Solicitud de transferencia actualizada.')

        elif action == 'delete_user':
            # Archiva, no borra.
            #
            # La version anterior eliminaba los chats y las citas del profesional.
            # Esos chats son consultas clinicas *de sus pacientes*: dar de baja a
            # un medico destruia la historia de terceros que no tenian nada que
            # ver con la baja, y que la ley obliga a conservar 15 anos.
            user_id = request.form.get('user_id', type=int)
            user = db.session.get(User, user_id) if user_id else None
            archivable = {ROLE_DOCTOR, ROLE_STAFF, ROLE_RECEPTIONIST, ROLE_EXPENDOR}

            if user and user.clinic_id == current_user.clinic_id and \
                    not user.is_autonomous and user.role in archivable:
                now = colombia_now()
                user.is_active_account = False
                user.deactivated_at = now
                user.is_available = False

                # Las consultas abiertas se cierran para que no queden en la
                # bandeja de alguien que ya no atiende.
                abiertos = Chat.query.filter(
                    Chat.clinic_id == current_user.clinic_id,
                    Chat.doctor_id == user.id,
                    Chat.status == 'open',
                ).all()
                for chat in abiertos:
                    chat.status = 'closed'
                    chat.closed_by = 'profesional_archivado'

                # Las citas futuras se cancelan: el horario queda libre y se puede
                # reasignar a otro profesional.
                hoy = now.strftime('%Y-%m-%d')
                futuras = Appointment.query.filter(
                    Appointment.clinic_id == current_user.clinic_id,
                    Appointment.doctor_id == user.id,
                    Appointment.date >= hoy,
                    Appointment.status.notin_(APPOINTMENT_FREEING_STATUSES),
                ).all()
                for cita in futuras:
                    cita.status = 'cancelada'

                audit(
                    'user_archived',
                    details=(f'user_id={user_id}; rol={user.role}; '
                             f'chats_cerrados={len(abiertos)}; citas_canceladas={len(futuras)}'),
                )
                db.session.commit()
                flash(
                    f'{user.name} archivado: cuenta desactivada, {len(abiertos)} consulta(s) '
                    f'cerrada(s) y {len(futuras)} cita(s) futura(s) cancelada(s). '
                    'Las consultas ya registradas se conservan por el plazo legal.'
                )
            else:
                flash('Solo pueden archivarse profesionales y personal de la propia clinica.')

        elif action == 'add_inventory':
            item = InventoryItem(
                clinic_id=current_user.clinic_id,
                name=bleach.clean((request.form.get('name') or '').strip())[:180],
                sku=(request.form.get('sku') or '').strip()[:80],
                unit=(request.form.get('unit') or 'unidad')[:40],
                stock=max(0, request.form.get('stock', type=int) or 0),
                min_stock=max(0, request.form.get('min_stock', type=int) or 0),
                expires_on=(request.form.get('expires_on') or '')[:10] or None,
            )
            if item.name:
                db.session.add(item)
                audit('inventory_item_created', details=f'name={item.name}')
                db.session.commit()
                flash('Medicamento agregado al inventario.')

        return redirect(url_for('admin.dashboard'))

    now = colombia_now()
    doctors = User.query.filter_by(role=ROLE_DOCTOR, clinic_id=current_user.clinic_id, is_autonomous=False).all()
    receptionists = User.query.filter(User.clinic_id == current_user.clinic_id, User.role.in_(STAFF_ROLE_VALUES)).all()
    expendedores = User.query.filter_by(role=ROLE_EXPENDOR, clinic_id=current_user.clinic_id).all()
    if current_user.clinic and current_user.clinic.access_type == 'private':
        active_accesses_for_patients = UserClinicAccess.query.filter(
            UserClinicAccess.clinic_id == current_user.clinic_id,
            UserClinicAccess.status == ACCESS_ACTIVE,
            (UserClinicAccess.expires_at == None) | (UserClinicAccess.expires_at > now),
        ).all()
        patient_ids = [access.user_id for access in active_accesses_for_patients]
        patients = User.query.filter(User.role == 'patient', User.id.in_(patient_ids)).execution_options(include_all_clinics=True).all() if patient_ids else []
    else:
        patients = User.query.filter_by(role='patient', clinic_id=current_user.clinic_id).all()
    attended_count = Appointment.query.filter_by(clinic_id=current_user.clinic_id, status='attended').count()
    inventory_items = InventoryItem.query.filter_by(clinic_id=current_user.clinic_id).order_by(InventoryItem.name.asc()).all()
    pharmacies = Pharmacy.query.filter_by(clinic_id=current_user.clinic_id).order_by(Pharmacy.name.asc()).all()
    replenishment_alerts = ReplenishmentAlert.query.filter_by(
        clinic_id=current_user.clinic_id,
        status='abierta',
    ).order_by(ReplenishmentAlert.created_at.desc()).limit(25).all()
    puntos_de_reposicion = [
        {'stock': fila,
         'disponible': available_quantity(fila),
         'estado': estado_de_stock(fila)}
        for fila in Stock.query.filter_by(clinic_id=current_user.clinic_id)
        .join(Pharmacy, Stock.pharmacy_id == Pharmacy.id)
        .order_by(Pharmacy.name.asc(), Stock.nombre_med.asc()).all()
    ]
    # Lo que necesita atencion va arriba: primero agotados, luego los que
    # cruzaron su punto de reposicion, y al final los que ni siquiera lo
    # tienen definido — que son los que el administrador debe configurar.
    ORDEN = {'agotado': 0, 'bajo': 1, 'sin_umbral': 2, 'normal': 3}
    puntos_de_reposicion.sort(key=lambda f: (ORDEN[f['estado']],
                                             f['stock'].nombre_med.lower()))

    transfer_requests = StockTransferRequest.query.filter_by(
        clinic_id=current_user.clinic_id,
    ).order_by(StockTransferRequest.created_at.desc()).limit(25).all()
    access_codes = ClinicAccessCode.query.filter_by(clinic_id=current_user.clinic_id).order_by(ClinicAccessCode.created_at.desc()).limit(50).all()
    clinic_accesses = UserClinicAccess.query.filter(
        UserClinicAccess.clinic_id == current_user.clinic_id,
        UserClinicAccess.status == ACCESS_ACTIVE,
        (UserClinicAccess.expires_at == None) | (UserClinicAccess.expires_at > now),
    ).order_by(UserClinicAccess.created_at.desc()).limit(100).all()
    subscription_rows = []
    doctor_ids = [doctor.id for doctor in doctors]
    if doctor_ids:
        subscriptions = PatientDoctorSubscription.query.filter(
            PatientDoctorSubscription.doctor_id.in_(doctor_ids),
            PatientDoctorSubscription.status == ACCESS_ACTIVE,
            (PatientDoctorSubscription.expires_at == None) | (PatientDoctorSubscription.expires_at > now),
        ).all()
        for subscription in subscriptions:
            if subscription.plan_type not in {'monthly', 'quarterly', 'annual', 'policy'}:
                continue
            patient = User.query.filter_by(id=subscription.patient_id).execution_options(include_all_clinics=True).first()
            doctor = User.query.filter_by(id=subscription.doctor_id).execution_options(include_all_clinics=True).first()
            subscription_rows.append({
                'subscription': subscription,
                'patient': patient,
                'doctor': doctor,
                'remaining': remaining_label(subscription.expires_at, now),
                'has_policy': subscription.source == 'policy',
            })
    clinic_access_rows = [
        {
            'access': access,
            'patient': User.query.filter_by(id=access.user_id).execution_options(include_all_clinics=True).first(),
            'remaining': remaining_label(access.expires_at, now),
            'has_policy': access.source == 'policy',
        }
        for access in clinic_accesses
    ]
    return render_template(
        'admin_dashboard.html',
        doctors=doctors,
        receptionists=receptionists,
        expendedores=expendedores,
        patients=patients,
        attended_count=attended_count,
        inventory_items=inventory_items,
        pharmacies=pharmacies,
        default_pharmacy=default_pharmacy,
        replenishment_alerts=replenishment_alerts,
        puntos_de_reposicion=puntos_de_reposicion,
        transfer_requests=transfer_requests,
        access_codes=access_codes,
        clinic_accesses=clinic_accesses,
        clinic_access_rows=clinic_access_rows,
        subscription_rows=subscription_rows,
    )


@admin_bp.route('/export_rips', methods=['GET'])
@login_required
@role_required('admin')
def export_rips():
    from rips_service import generate_rips
    from datetime import datetime, timedelta
    from flask import send_file
    import io
    
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    
    now_rips = colombia_now()
    if start_date_str and end_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1) - timedelta(seconds=1)
        except ValueError:
            flash('Fechas inválidas')
            return redirect(url_for('admin.dashboard'))
    else:
        start_date = now_rips.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date = now_rips
        
    invoice_number = (request.args.get('invoice') or '').strip()[:40] or None

    try:
        zip_data = generate_rips(
            current_user.clinic_id, start_date, end_date, invoice_number=invoice_number
        )
    except RIPSValidationError as error:
        # La exportacion se detiene en vez de rellenar los huecos con valores
        # inventados, como hacia antes. Radicar datos falsos ante el sistema de
        # salud tiene consecuencias mayores que tener que completarlos ahora.
        audit('rips_export_blocked', details=f'inconsistencias={len(error.issues)}')
        db.session.commit()
        return render_template(
            'admin_rips_validation.html',
            issues=error.issues,
            start_date=start_date,
            end_date=end_date,
        ), 422
    except ValueError as error:
        flash(str(error))
        return redirect(url_for('admin.dashboard'))

    audit(
        'rips_exported',
        details=(
            f'periodo={start_date:%Y-%m-%d}..{end_date:%Y-%m-%d}; '
            f'factura={"si" if invoice_number else "no"}'
        ),
    )
    db.session.commit()

    return send_file(
        io.BytesIO(zip_data),
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'RIPS_{current_user.clinic_id}_{start_date.strftime("%Y%m")}.zip'
    )


@admin_bp.route('/rips/validar', methods=['GET'])
@login_required
@role_required('admin')
def validate_rips():
    """Revisa si el periodo puede exportarse, sin generar el archivo.

    Permite corregir los faltantes antes de intentar radicar, en lugar de
    descubrirlos cuando el validador oficial rechaza el envio.
    """
    from datetime import datetime, timedelta
    from rips_service import preview_validation

    start_str = request.args.get('start_date')
    end_str = request.args.get('end_date')
    now = colombia_now()

    if start_str and end_str:
        try:
            start_date = datetime.strptime(start_str, '%Y-%m-%d')
            end_date = datetime.strptime(end_str, '%Y-%m-%d') + timedelta(days=1) - timedelta(seconds=1)
        except ValueError:
            flash('Fechas invalidas.')
            return redirect(url_for('admin.dashboard'))
    else:
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date = now

    result = preview_validation(current_user.clinic_id, start_date, end_date)
    return render_template(
        'admin_rips_validation.html',
        issues=result['issues'],
        summary=result,
        start_date=start_date,
        end_date=end_date,
    )


@admin_bp.route('/emitir_reset/<int:user_id>', methods=['POST'])
@login_required
@role_required('admin')
def issue_password_reset(user_id):
    """Emite un codigo de restablecimiento de un solo uso.

    Antes no habia ninguna via de recuperacion: un profesional que olvidara su
    clave quedaba fuera del sistema de forma permanente.

    El codigo se muestra una sola vez, en pantalla, para que el administrador lo
    entregue en persona. En la base de datos solo queda su hash, de modo que ni
    siquiera quien lea la base puede usarlo para tomar la cuenta.
    """
    target = User.query.filter_by(
        id=user_id, clinic_id=current_user.clinic_id
    ).first_or_404()

    # Un administrador de clinica no puede restablecer la clave de un
    # superadministrador ni la de otro administrador: seria una via de escalada
    # de privilegios dentro del propio sistema.
    if target.role in {'super', 'admin'} and target.id != current_user.id:
        audit('password_reset_issue_denied', details=f'target_id={target.id}')
        db.session.commit()
        abort(403)

    # Los codigos pendientes anteriores se invalidan: solo debe existir uno vivo.
    for pending in PasswordResetToken.query.filter_by(user_id=target.id, used_at=None).all():
        pending.used_at = colombia_now()

    raw_token = secrets.token_urlsafe(24)
    ttl = current_app.config.get('PASSWORD_RESET_TTL_MINUTES', 30)

    db.session.add(PasswordResetToken(
        user_id=target.id,
        token_hash=hashlib.sha256(raw_token.encode('utf-8')).hexdigest(),
        issued_by_id=current_user.id,
        issued_ip=request.remote_addr,
        created_at=colombia_now(),
        expires_at=colombia_now() + timedelta(minutes=ttl),
    ))

    # Se marca la cuenta para que, tras entrar con el codigo, tenga que definir
    # una contrasena propia.
    target.must_change_password = True

    audit('password_reset_issued', details=f'target_id={target.id}; ttl_min={ttl}')
    db.session.commit()

    reset_url = url_for('auth.complete_password_reset', token=raw_token, _external=True)
    return render_template(
        'admin_password_reset_issued.html',
        target=target,
        reset_url=reset_url,
        raw_token=raw_token,
        ttl_minutes=ttl,
    )


@admin_bp.route('/auditoria')
@login_required
@role_required('admin')
def audit_log():
    """Visor del registro de auditoria.

    El registro se escribia desde antes, pero nadie podia consultarlo: un
    registro que no se lee no cumple ninguna funcion de control. Ademas de
    mostrarlo, esta vista verifica la cadena de hashes, que es lo que permite
    detectar si alguien altero o elimino entradas despues de escritas.
    """
    from models import AuditLog
    from security import verify_audit_chain

    event_filter = (request.args.get('event') or '').strip()
    user_filter = request.args.get('user_id', type=int)
    days = min(max(request.args.get('days', type=int) or 7, 1), 90)
    page = max(request.args.get('page', type=int) or 1, 1)
    per_page = 100

    since = colombia_now() - timedelta(days=days)

    query = AuditLog.query.filter(
        AuditLog.clinic_id == current_user.clinic_id,
        AuditLog.timestamp >= since,
    )
    if event_filter:
        query = query.filter(AuditLog.event.like(f'%{event_filter}%'))
    if user_filter:
        query = query.filter(AuditLog.user_id == user_filter)

    total = query.count()
    entries = query.order_by(AuditLog.timestamp.desc()) \
                   .offset((page - 1) * per_page).limit(per_page).all()

    # Eventos de seguridad que merecen atencion inmediata del administrador.
    critical_events = (
        'login_failed', 'login_blocked_by_lockout', 'jwt_blocked_by_lockout',
        'medical_order_hash_rejected', 'prescription_safety_override',
        'delivery_identity_mismatch', 'account_deactivated_by_request',
        'prescription_blocked_by_safety',
    )
    alerts = AuditLog.query.filter(
        AuditLog.clinic_id == current_user.clinic_id,
        AuditLog.timestamp >= since,
        AuditLog.event.in_(critical_events),
    ).order_by(AuditLog.timestamp.desc()).limit(25).all()

    # La verificacion recorre la cadena completa, asi que se limita para no
    # bloquear la vista en instalaciones con historial largo.
    chain_breaks = verify_audit_chain(limit=5000)

    actor_names = {}
    for entry in entries:
        if entry.user_id and entry.user_id not in actor_names:
            actor = User.query.filter_by(id=entry.user_id).execution_options(
                include_all_clinics=True).first()
            actor_names[entry.user_id] = actor.name if actor else f'Usuario {entry.user_id}'

    return render_template(
        'admin_audit_log.html',
        entries=entries,
        alerts=alerts,
        actor_names=actor_names,
        chain_breaks=chain_breaks,
        total=total,
        page=page,
        per_page=per_page,
        pages=max(1, (total + per_page - 1) // per_page),
        days=days,
        event_filter=event_filter,
    )


@admin_bp.route('/reporte_stock')
@login_required
@role_required('admin')
def reporte_stock():
    """Report on medication stock outs and partial deliveries."""
    days = request.args.get('days', 30, type=int)
    since = colombia_now() - timedelta(days=days)
    
    # Get all tickets that were sin_stock or parcial in the last N days
    tickets = MedicationPickupTicket.query.filter(
        MedicationPickupTicket.clinic_id == current_user.clinic_id,
        MedicationPickupTicket.created_at >= since,
        MedicationPickupTicket.status.in_(['sin_stock', 'parcial', 'entregado'])
    ).all()
    
    stats = {} # med_name -> {sin_stock_count, total_requested}
    
    for t in tickets:
        try:
            meds = json.loads(t.meds_json or '[]')
        except:
            continue
        
        is_break = t.status in ['sin_stock', 'parcial']
        
        for med in meds:
            name = med.get('nombre_med', 'Desconocido')
            if name not in stats:
                stats[name] = {'sin_stock_count': 0, 'total_requested': 0}
            
            stats[name]['total_requested'] += 1
            if is_break:
                stats[name]['sin_stock_count'] += 1
                
    # Sort by sin_stock_count DESC
    sorted_stats = sorted(stats.items(), key=lambda x: x[1]['sin_stock_count'], reverse=True)
    
    return render_template('admin_reporte_stock.html', stats=sorted_stats, days=days)
