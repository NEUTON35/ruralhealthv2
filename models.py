from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import MetaData
from flask_login import UserMixin
from sqlalchemy.orm import declared_attr
from time_utils import colombia_now
from security import EncryptedText

# Convencion de nombres para las restricciones.
#
# SQLite no sabe alterar una tabla en sitio: Alembic la reconstruye copiando los
# datos, y para eso necesita poder nombrar cada restriccion. Sin esta convencion,
# cualquier migracion que toque una tabla con una restriccion anonima falla con
# "Constraint must have a name" a mitad de camino, dejando el esquema a medias.
#
# Tambien sirve en PostgreSQL: permite referirse a una restriccion por su nombre
# en lugar del identificador que el motor genere.
NAMING_CONVENTION = {
    'ix': 'ix_%(column_0_label)s',
    'uq': 'uq_%(table_name)s_%(column_0_name)s',
    'ck': 'ck_%(table_name)s_%(constraint_name)s',
    'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
    'pk': 'pk_%(table_name)s',
}

db = SQLAlchemy(metadata=MetaData(naming_convention=NAMING_CONVENTION))

ROLE_SUPER = 'super'
ROLE_CLINIC_ADMIN = 'admin'
ROLE_RECEPTIONIST = 'receptionist'
ROLE_STAFF = 'staff'
ROLE_EXPENDOR = 'expendedor'
ROLE_EXPENDEDOR = ROLE_EXPENDOR
ROLE_DOCTOR = 'doctor'
ROLE_PATIENT = 'patient'
STAFF_ROLE_VALUES = (ROLE_STAFF, ROLE_RECEPTIONIST)
CLINIC_ACTIVE = 'active'
CLINIC_SUSPENDED = 'suspended'
CLINIC_ACCESS_PUBLIC = 'public'
CLINIC_ACCESS_PRIVATE = 'private'
ACCESS_ACTIVE = 'active'
ACCESS_REVOKED = 'revoked'
ACCESS_EXPIRED = 'expired'
PLAN_PER_CONSULTATION = 'per_consultation'
PLAN_MONTHLY = 'monthly'
PLAN_QUARTERLY = 'quarterly'
PLAN_ANNUAL = 'annual'
PAYMENT_PENDING = 'pending'
PAYMENT_APPROVED = 'approved'
PAYMENT_REJECTED = 'rejected'

# Tipos de documento admitidos por RIPS (Resolucion 1036 de 2022 y concordantes).
DOC_CEDULA = 'CC'
DOC_TARJETA_IDENTIDAD = 'TI'
DOC_REGISTRO_CIVIL = 'RC'
DOC_CEDULA_EXTRANJERIA = 'CE'
DOC_PASAPORTE = 'PA'
DOC_MENOR_SIN_ID = 'MS'
DOC_ADULTO_SIN_ID = 'AS'
DOC_PERMISO_ESPECIAL = 'PE'
DOC_PERMISO_PROTECCION = 'PT'
DOCUMENT_TYPES = (
    DOC_CEDULA, DOC_TARJETA_IDENTIDAD, DOC_REGISTRO_CIVIL, DOC_CEDULA_EXTRANJERIA,
    DOC_PASAPORTE, DOC_MENOR_SIN_ID, DOC_ADULTO_SIN_ID, DOC_PERMISO_ESPECIAL,
    DOC_PERMISO_PROTECCION,
)

# Modalidad de atencion (Resolucion 2654 de 2019).
CARE_IN_PERSON = 'presencial'
CARE_TELEMEDICINE = 'telemedicina'
CARE_MODALITIES = (CARE_IN_PERSON, CARE_TELEMEDICINE)

# Formas farmaceuticas. La Resolucion 1403 de 2007 exige consignarla junto a la
# concentracion: "amoxicilina 500 mg" no dice si es capsula o suspension, y esa
# diferencia cambia como se dispensa y como se administra.
DOSAGE_FORMS = (
    'tableta', 'tableta recubierta', 'capsula', 'jarabe', 'suspension',
    'solucion oral', 'solucion inyectable', 'polvo para inyeccion', 'ampolla',
    'crema', 'unguento', 'gel', 'gotas', 'colirio', 'supositorio', 'ovulo',
    'parche', 'inhalador', 'aerosol', 'polvo para reconstituir', 'sobre',
    'jeringa prellenada', 'otro',
)

# Vias de administracion.
ADMINISTRATION_ROUTES = (
    'oral', 'sublingual', 'intravenosa', 'intramuscular', 'subcutanea',
    'topica', 'oftalmica', 'otica', 'nasal', 'rectal', 'vaginal',
    'inhalatoria', 'transdermica', 'otra',
)

# Movimientos del libro mayor de inventario.
STOCK_MOVE_RECEIPT = 'ingreso'
STOCK_MOVE_DISPENSE = 'dispensacion'
STOCK_MOVE_RESERVE = 'reserva'
STOCK_MOVE_RELEASE = 'liberacion'
STOCK_MOVE_ADJUST = 'ajuste'
STOCK_MOVE_TRANSFER_OUT = 'traslado_salida'
STOCK_MOVE_TRANSFER_IN = 'traslado_entrada'
STOCK_MOVE_EXPIRY = 'baja_vencimiento'

class ClinicScoped:
    @declared_attr
    def clinic_id(cls):
        return db.Column(db.Integer, db.ForeignKey('clinic.id'), nullable=True, index=True)

class Clinic(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(180), nullable=False)
    legal_name = db.Column(db.String(220), nullable=True)
    nit = db.Column(db.String(50), nullable=True)
    # Codigo de habilitacion en el REPS. RIPS lo exige como identificador del
    # prestador; el NIT no lo sustituye.
    habilitacion_code = db.Column(db.String(20), nullable=True, index=True)
    department_code = db.Column(db.String(2), nullable=True)   # DANE
    municipality_code = db.Column(db.String(3), nullable=True)  # DANE
    location = db.Column(db.String(220), nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    access_type = db.Column(db.String(30), default=CLINIC_ACCESS_PUBLIC, nullable=False)
    plan = db.Column(db.String(80), default='starter', nullable=False)
    plan_pago = db.Column(db.String(80), default='starter', nullable=False)
    status = db.Column(db.String(50), default=CLINIC_ACTIVE, nullable=False)
    activa = db.Column(db.Boolean, default=True, nullable=False)
    contact_email = db.Column(db.String(180), nullable=True)
    logo_path = db.Column(db.String(300), nullable=True)
    opening_hours = db.Column(db.Text, nullable=True)
    policy_consent_code = db.Column(db.String(64), unique=True, nullable=True, index=True)
    policy_consent_generated_at = db.Column(db.DateTime, nullable=True)
    subscription_provider = db.Column(db.String(80), nullable=True)
    subscription_customer_id = db.Column(db.String(180), nullable=True)
    subscription_status = db.Column(db.String(50), default='manual', nullable=False)
    created_at = db.Column(db.DateTime, default=colombia_now)

class Pharmacy(ClinicScoped, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(180), nullable=False)
    address = db.Column(db.String(220), nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    phone = db.Column(db.String(80), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=colombia_now)
    clinic = db.relationship('Clinic')

class User(UserMixin, ClinicScoped, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), nullable=False)
    name = db.Column(EncryptedText, nullable=False)
    cedula = db.Column(EncryptedText, nullable=False)
    cedula_hash = db.Column(db.String(64), unique=True, nullable=True, index=True)
    phone = db.Column(EncryptedText, nullable=True)
    email = db.Column(EncryptedText, nullable=True)
    specialty = db.Column(db.String(150), nullable=True)

    # --- Identificacion desagregada (exigida por RIPS) ---
    # El campo `name` guarda el nombre completo para presentacion; RIPS exige los
    # componentes por separado y no admite deducirlos partiendo la cadena.
    document_type = db.Column(db.String(4), default=DOC_CEDULA, nullable=True)
    first_surname = db.Column(EncryptedText, nullable=True)
    second_surname = db.Column(EncryptedText, nullable=True)
    first_name = db.Column(EncryptedText, nullable=True)
    second_name = db.Column(EncryptedText, nullable=True)
    birth_date = db.Column(db.Date, nullable=True)
    sex = db.Column(db.String(1), nullable=True)          # M | F
    department_code = db.Column(db.String(2), nullable=True)   # DANE
    municipality_code = db.Column(db.String(3), nullable=True)  # DANE
    zone = db.Column(db.String(1), default='R', nullable=True)  # U urbana | R rural
    insurer_code = db.Column(db.String(20), nullable=True)      # codigo EPS/entidad
    insurer_name = db.Column(db.String(180), nullable=True)
    affiliation_regime = db.Column(db.String(30), nullable=True)  # contributivo | subsidiado | ...

    # --- Datos que exige el perfil PatientRDA del Ministerio -----------------
    # El perfil los marca obligatorios (Resolucion 1888 de 2025). Se guardan
    # como codigo del catalogo oficial, no como texto libre: el Ministerio
    # valida contra su ValueSet.
    #
    # Ninguno se rellena solo. La pertenencia etnica es un dato sensible y
    # autorreconocido: inventarlo, o suponerlo por el municipio, es peor que
    # dejarlo vacio. Nacionalidad trae 170 (Colombia) por ser el caso
    # abrumadoramente mayoritario, pero el formulario permite cambiarlo.
    nationality_code = db.Column(db.String(3), default='170', nullable=True)
    ethnic_group = db.Column(db.String(2), nullable=True)   # ColombianEthnicGroup
    disability = db.Column(db.String(2), nullable=True)     # ColombianDisabilityClassification
    occupation_code = db.Column(db.String(10), nullable=True)  # CIUO

    # --- Datos clinicos basicos del paciente ---
    is_pregnant = db.Column(db.Boolean, default=False, nullable=False)
    pregnancy_updated_at = db.Column(db.DateTime, nullable=True)
    blood_type = db.Column(db.String(5), nullable=True)

    # --- Ciclo de vida de la cuenta ---
    created_at = db.Column(db.DateTime, default=colombia_now, nullable=True, index=True)
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)
    password_changed_at = db.Column(db.DateTime, nullable=True)
    is_active_account = db.Column(db.Boolean, default=True, nullable=False)
    deactivated_at = db.Column(db.DateTime, nullable=True)
    last_login_at = db.Column(db.DateTime, nullable=True)
    medical_registration = db.Column(db.String(50), nullable=True)
    signature_path = db.Column(db.String(300), nullable=True)
    rating = db.Column(db.Float, default=0.0)
    is_available = db.Column(db.Boolean, default=True)
    schedule = db.Column(db.String(200), nullable=True)
    peak_hours = db.Column(db.String(200), nullable=True)
    avg_response_time = db.Column(db.String(50), nullable=True)
    avg_consultation_time = db.Column(db.String(50), nullable=True)
    profile_pic = db.Column(db.String(300), nullable=True)
    attending_chat_id = db.Column(db.Integer, nullable=True)
    accepted_terms_at = db.Column(db.DateTime, nullable=True)
    accepted_privacy_at = db.Column(db.DateTime, nullable=True)
    accepted_transparency_at = db.Column(db.DateTime, nullable=True)
    sensitive_data_consent_at = db.Column(db.DateTime, nullable=True)
    atencion_inicio = db.Column(db.String(5), nullable=True)
    atencion_fin = db.Column(db.String(5), nullable=True)
    address = db.Column(EncryptedText, nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    location_consent_at = db.Column(db.DateTime, nullable=True)
    office_address = db.Column(EncryptedText, nullable=True)
    office_latitude = db.Column(db.Float, nullable=True)
    office_longitude = db.Column(db.Float, nullable=True)
    show_office_on_map = db.Column(db.Boolean, default=False, nullable=False)
    is_autonomous = db.Column(db.Boolean, default=False, nullable=False)
    affiliation_type = db.Column(db.String(30), default='clinic', nullable=False)
    affiliation_name = db.Column(db.String(180), nullable=True)
    policy_consent_code = db.Column(db.String(64), unique=True, nullable=True, index=True)
    policy_consent_generated_at = db.Column(db.DateTime, nullable=True)
    pharmacy_id = db.Column(db.Integer, db.ForeignKey('pharmacy.id'), nullable=True, index=True)
    clinic = db.relationship('Clinic')
    pharmacy = db.relationship('Pharmacy')

class ClinicAccessCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinic.id'), nullable=False, index=True)
    code = db.Column(db.String(80), unique=True, nullable=False, index=True)
    description = db.Column(db.String(220), nullable=True)
    max_uses = db.Column(db.Integer, default=1, nullable=False)
    uses = db.Column(db.Integer, default=0, nullable=False)
    duration_days = db.Column(db.Integer, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=colombia_now, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=True)
    clinic = db.relationship('Clinic')
    created_by = db.relationship('User', foreign_keys=[created_by_user_id])

class UserClinicAccess(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinic.id'), nullable=False, index=True)
    source = db.Column(db.String(50), default='code', nullable=False)
    status = db.Column(db.String(30), default=ACCESS_ACTIVE, nullable=False, index=True)
    code_id = db.Column(db.Integer, db.ForeignKey('clinic_access_code.id'), nullable=True)
    policy_enrollment_id = db.Column(db.Integer, db.ForeignKey('user_policy_enrollment.id'), nullable=True)
    starts_at = db.Column(db.DateTime, default=colombia_now, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True)
    revoked_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=colombia_now, nullable=False)
    user = db.relationship('User', foreign_keys=[user_id])
    clinic = db.relationship('Clinic')
    code = db.relationship('ClinicAccessCode')
    revoked_by = db.relationship('User', foreign_keys=[revoked_by_user_id])

class DoctorTariff(ClinicScoped, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, unique=True, index=True)
    price_per_consultation = db.Column(db.Float, nullable=True)
    price_monthly = db.Column(db.Float, nullable=True)
    price_quarterly = db.Column(db.Float, nullable=True)
    price_annual = db.Column(db.Float, nullable=True)
    default_policy_discount_percent = db.Column(db.Float, default=0.0, nullable=False)
    payment_methods_image = db.Column(db.String(300), nullable=True)
    payment_instructions = db.Column(db.Text, nullable=True)
    accepts_manual_payment = db.Column(db.Boolean, default=True, nullable=False)
    updated_at = db.Column(db.DateTime, default=colombia_now, onupdate=colombia_now)
    doctor = db.relationship('User', foreign_keys=[doctor_id])

class PaymentVerificationTicket(ClinicScoped, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    plan_type = db.Column(db.String(40), nullable=False)
    amount = db.Column(db.Float, nullable=True)
    discount_percent = db.Column(db.Float, default=0.0, nullable=False)
    final_amount = db.Column(db.Float, nullable=True)
    proof_image = db.Column(db.String(300), nullable=True)
    status = db.Column(db.String(30), default=PAYMENT_PENDING, nullable=False, index=True)
    patient_note = db.Column(db.Text, nullable=True)
    doctor_note = db.Column(db.Text, nullable=True)
    reviewed_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    subscription_id = db.Column(db.Integer, db.ForeignKey('patient_doctor_subscription.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=colombia_now, nullable=False)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    patient = db.relationship('User', foreign_keys=[patient_id])
    doctor = db.relationship('User', foreign_keys=[doctor_id])
    reviewed_by = db.relationship('User', foreign_keys=[reviewed_by_user_id])

class PatientDoctorSubscription(ClinicScoped, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    source = db.Column(db.String(50), default='manual_payment', nullable=False)
    plan_type = db.Column(db.String(40), nullable=False)
    status = db.Column(db.String(30), default=ACCESS_ACTIVE, nullable=False, index=True)
    starts_at = db.Column(db.DateTime, default=colombia_now, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=True)
    payment_ticket_id = db.Column(db.Integer, db.ForeignKey('payment_verification_ticket.id'), nullable=True)
    policy_enrollment_id = db.Column(db.Integer, db.ForeignKey('user_policy_enrollment.id'), nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True)
    revoked_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=colombia_now, nullable=False)
    patient = db.relationship('User', foreign_keys=[patient_id])
    doctor = db.relationship('User', foreign_keys=[doctor_id])
    revoked_by = db.relationship('User', foreign_keys=[revoked_by_user_id])

class Policy(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(80), unique=True, nullable=False, index=True)
    name = db.Column(db.String(180), nullable=False)
    insurer_name = db.Column(db.String(180), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=colombia_now, nullable=False)

class PolicyNetworkProvider(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    policy_id = db.Column(db.Integer, db.ForeignKey('policy.id'), nullable=False, index=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinic.id'), nullable=True, index=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    consent_code = db.Column(db.String(64), nullable=False, index=True)
    discount_percent = db.Column(db.Float, default=0.0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=colombia_now, nullable=False)
    policy = db.relationship('Policy')
    clinic = db.relationship('Clinic')
    doctor = db.relationship('User', foreign_keys=[doctor_id])

class PolicyInviteBatch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    policy_id = db.Column(db.Integer, db.ForeignKey('policy.id'), nullable=False, index=True)
    provider_id = db.Column(db.Integer, db.ForeignKey('policy_network_provider.id'), nullable=True)
    consent_code = db.Column(db.String(64), nullable=False, index=True)
    max_codes = db.Column(db.Integer, default=50, nullable=False)
    generated_count = db.Column(db.Integer, default=0, nullable=False)
    duration_days = db.Column(db.Integer, default=30, nullable=False)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=colombia_now, nullable=False)
    policy = db.relationship('Policy')
    provider = db.relationship('PolicyNetworkProvider')
    created_by = db.relationship('User', foreign_keys=[created_by_user_id])

class PolicyInviteCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey('policy_invite_batch.id'), nullable=False, index=True)
    policy_id = db.Column(db.Integer, db.ForeignKey('policy.id'), nullable=False, index=True)
    code = db.Column(db.String(80), unique=True, nullable=False, index=True)
    status = db.Column(db.String(30), default='unused', nullable=False, index=True)
    redeemed_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    redeemed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=colombia_now, nullable=False)
    batch = db.relationship('PolicyInviteBatch')
    policy = db.relationship('Policy')
    redeemed_by = db.relationship('User', foreign_keys=[redeemed_by_user_id])

class UserPolicyEnrollment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    policy_id = db.Column(db.Integer, db.ForeignKey('policy.id'), nullable=False, index=True)
    invite_code_id = db.Column(db.Integer, db.ForeignKey('policy_invite_code.id'), nullable=True)
    status = db.Column(db.String(30), default=ACCESS_ACTIVE, nullable=False, index=True)
    starts_at = db.Column(db.DateTime, default=colombia_now, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=True)
    policy_document_path = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, default=colombia_now, nullable=False)
    user = db.relationship('User')
    policy = db.relationship('Policy')
    invite_code = db.relationship('PolicyInviteCode')

class Chat(ClinicScoped, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(50), default='open')
    closed_by = db.Column(db.String(50), nullable=True)
    reason = db.Column(EncryptedText, nullable=True)
    mode = db.Column(db.String(50), default='triage')
    active_flow_id = db.Column(db.Integer, nullable=True)
    active_flow_node = db.Column(db.String(50), nullable=True)
    timestamp = db.Column(db.DateTime, default=colombia_now)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointment.id'), nullable=True)
    patient = db.relationship('User', foreign_keys=[patient_id])
    doctor = db.relationship('User', foreign_keys=[doctor_id])
    messages = db.relationship('Message', backref='chat', lazy=True)

class Message(ClinicScoped, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.Integer, db.ForeignKey('chat.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(EncryptedText, nullable=True)
    file_path = db.Column(db.String(300), nullable=True)
    timestamp = db.Column(db.DateTime, default=colombia_now)
    is_flow_question = db.Column(db.Boolean, default=False)
    flow_options = db.Column(db.Text, nullable=True)
    flow_input_type = db.Column(db.String(50), nullable=True)
    is_read = db.Column(db.Boolean, default=False)
    sender = db.relationship('User', foreign_keys=[sender_id])

# Estados de una cita. `cancelada` y `no_show` liberan el horario para que otro
# paciente pueda tomarlo; el resto lo ocupan.
APPOINTMENT_PENDING = 'pending'
APPOINTMENT_IN_PROGRESS = 'in_progress'
APPOINTMENT_ATTENDED = 'attended'
APPOINTMENT_NO_SHOW = 'no_show'
APPOINTMENT_CANCELLED = 'cancelada'
APPOINTMENT_FREEING_STATUSES = (APPOINTMENT_NO_SHOW, APPOINTMENT_CANCELLED)


class Appointment(ClinicScoped, db.Model):
    # Indice unico parcial sobre el horario del profesional.
    #
    # Antes la reserva era "consultar y luego insertar", sin nada que impidiera
    # que dos pacientes pulsaran el mismo horario a la vez: ambos pasaban la
    # comprobacion y ambos quedaban agendados con el mismo medico a la misma hora.
    # Solo una restriccion en la base de datos cierra esa ventana.
    #
    # Es parcial —excluye citas canceladas y no asistidas— para que un horario
    # liberado pueda volver a ofrecerse.
    __table_args__ = (
        db.Index(
            'uq_appointment_active_slot',
            'doctor_id', 'date', 'time',
            unique=True,
            sqlite_where=db.text("status NOT IN ('cancelada', 'no_show')"),
            postgresql_where=db.text("status NOT IN ('cancelada', 'no_show')"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    # Fecha en que el usuario SOLICITO la cita, distinta de la fecha asignada.
    # Sin ella no puede calcularse la oportunidad, que es el indicador con el
    # que se mide el acceso (Resolucion 1552 de 2013).
    requested_at = db.Column(db.DateTime, default=colombia_now, nullable=True,
                             index=True)
    date = db.Column(db.String(10), nullable=False)
    time = db.Column(db.String(5), nullable=False)
    appointment_type = db.Column(db.String(80), default='Consulta', nullable=False)
    description = db.Column(EncryptedText, nullable=True)
    status = db.Column(db.String(50), default='pending')
    started_at = db.Column(db.DateTime, nullable=True)
    patient = db.relationship('User', foreign_keys=[patient_id])
    doctor = db.relationship('User', foreign_keys=[doctor_id])
    chat = db.relationship('Chat', backref='appointment', uselist=False)

class Rating(ClinicScoped, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    chat_id = db.Column(db.Integer, db.ForeignKey('chat.id'), nullable=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointment.id'), nullable=True)
    stars = db.Column(db.Integer, nullable=False)
    comment = db.Column(EncryptedText, nullable=True) # NUEVO: Comentario escrito
    tags = db.Column(db.String(300), nullable=True) # NUEVO: Etiquetas separadas por coma (Ejemplo: "Puntual,Amable")
    doctor = db.relationship('User', foreign_keys=[doctor_id])
    patient = db.relationship('User', foreign_keys=[patient_id])
    chat = db.relationship('Chat', foreign_keys=[chat_id])
    appointment = db.relationship('Appointment', foreign_keys=[appointment_id])

class Favorite(ClinicScoped, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    patient = db.relationship('User', foreign_keys=[patient_id])
    doctor = db.relationship('User', foreign_keys=[doctor_id])

class QuestionFlow(ClinicScoped, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    flow_data = db.Column(db.Text, nullable=False)

# NUEVO: Horarios del Doctor
class DoctorSchedule(ClinicScoped, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    day_of_week = db.Column(db.Integer, nullable=True) # 0=Lunes, 6=Domingo (Para horarios semanales)
    specific_date = db.Column(db.String(10), nullable=True) # YYYY-MM-DD (Para días específicos bloqueados/disponibles)
    start_time = db.Column(db.String(5), nullable=False) # HH:MM
    end_time = db.Column(db.String(5), nullable=False) # HH:MM
    is_available = db.Column(db.Boolean, default=True) # True=Horario laboral, False=Bloqueado
    
    doctor = db.relationship('User', foreign_keys=[doctor_id])

# --- Catalogos del RIPS (Resolucion 948 de 2026) -----------------------------
# Modalidad de atencion. Los codigos 06 a 09 son telemedicina: registrarlos es
# lo que exige la Resolucion 2654 de 2019 para dejar constancia de la modalidad.
MODALIDAD_INTRAMURAL = '01'
MODALIDAD_TELEMEDICINA_INTERACTIVA = '06'
MODALIDADES_TELEMEDICINA = ('06', '07', '08', '09')


class MedicalHistory(ClinicScoped, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    chat_id = db.Column(db.Integer, db.ForeignKey('chat.id'), nullable=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointment.id'), nullable=True)
    record_type = db.Column(db.String(80), default='note', nullable=False)
    summary = db.Column(EncryptedText, nullable=True)
    diagnosis = db.Column(EncryptedText, nullable=True)
    cie10_code = db.Column(db.String(10), nullable=True, index=True)
    cups_code = db.Column(db.String(20), nullable=True, index=True)
    treatment = db.Column(EncryptedText, nullable=True)
    created_at = db.Column(db.DateTime, default=colombia_now)

    # --- Datos que exige el reporte, y que antes se inventaban --------------
    # `rips_service` quemaba una causa externa fija ("enfermedad general") para
    # toda atencion. Ese campo distingue enfermedad general de accidente de
    # trabajo (ARL), de transito (SOAT) y de lesion por agresion. Reportarlo
    # todo igual traslada el costo al pagador equivocado y, sobre todo, borra
    # del reporte los casos de agresion, que son los que activan rutas de
    # proteccion. Ahora lo determina el profesional en cada atencion.
    external_cause = db.Column(db.String(2), nullable=True, index=True)
    consultation_purpose = db.Column(db.String(2), nullable=True)
    # Resolucion 2654 de 2019: la historia clinica debe dejar constancia de si
    # la atencion fue presencial o por telemedicina.
    care_modality = db.Column(db.String(2), default=MODALIDAD_INTRAMURAL,
                              nullable=True, index=True)

    # --- Adenda (Resolucion 1995 de 1999) ----------------------------------
    # La historia clinica no se corrige borrando: se corrige por adenda, que
    # deja el registro anterior intacto y anade uno nuevo que lo enmienda. Sin
    # esto, un profesional que consigna un diagnostico equivocado no tiene
    # salida, y ese diagnostico ya viajo al IHCE.
    amends_id = db.Column(db.Integer, db.ForeignKey('medical_history.id'),
                          nullable=True, index=True)
    amendment_reason = db.Column(EncryptedText, nullable=True)

    patient = db.relationship('User', foreign_keys=[patient_id], backref='medical_histories')
    doctor = db.relationship('User', foreign_keys=[doctor_id])
    amends = db.relationship('MedicalHistory', remote_side=[id],
                             foreign_keys=[amends_id])

    @property
    def is_telemedicine(self):
        return (self.care_modality or '') in MODALIDADES_TELEMEDICINA

    @property
    def is_amendment(self):
        return self.amends_id is not None

class InventoryItem(ClinicScoped, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(180), nullable=False)
    sku = db.Column(db.String(80), nullable=True)
    unit = db.Column(db.String(40), default='unidad', nullable=False)
    stock = db.Column(db.Integer, default=0, nullable=False)
    min_stock = db.Column(db.Integer, default=0, nullable=False)
    expires_on = db.Column(db.String(10), nullable=True)
    created_at = db.Column(db.DateTime, default=colombia_now)

class Stock(ClinicScoped, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pharmacy_id = db.Column(db.Integer, db.ForeignKey('pharmacy.id'), nullable=True, index=True)
    nombre_med = db.Column(db.String(180), nullable=False, index=True)
    medicamento = db.Column(db.String(180), nullable=True, index=True)
    cantidad = db.Column(db.Integer, default=0, nullable=False)
    cantidad_comprometida = db.Column(db.Integer, default=0, nullable=False)
    unidad = db.Column(db.String(40), default='unidad', nullable=False)
    pharmacy = db.relationship('Pharmacy')

class MedicalOrder(ClinicScoped, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(50), nullable=True, index=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    meds_json = db.Column(db.Text, nullable=False)
    diagnosis_json = db.Column(db.Text, nullable=True)
    patient_age = db.Column(db.String(50), nullable=True)
    patient_level = db.Column(db.String(10), default='1', nullable=True)
    insurance_name = db.Column(db.String(180), nullable=True)
    insurance_plan = db.Column(db.String(80), nullable=True)
    insurance_regime = db.Column(db.String(50), nullable=True)
    observations = db.Column(db.Text, nullable=True)

    # --- Datos del paciente en el momento de prescribir --------------------
    # Se copian a la orden en lugar de leerse del perfil. Una orden medica es un
    # documento con fecha: debe reflejar los datos que tenia el paciente cuando
    # se emitio, no los que tenga hoy. Si el paciente cambia de direccion, la
    # orden de hace seis meses no puede cambiar sola.
    #
    # La Resolucion 1403 de 2007 los exige expresamente (numeral 2.5 del Manual
    # de Condiciones Esenciales del Servicio Farmaceutico).
    patient_document_type = db.Column(db.String(4), nullable=True)
    patient_document = db.Column(EncryptedText, nullable=True)
    patient_address = db.Column(EncryptedText, nullable=True)
    patient_phone = db.Column(EncryptedText, nullable=True)
    # Numero de historia clinica. Exigido por la norma y ausente hasta ahora.
    clinical_record_number = db.Column(db.String(50), nullable=True, index=True)

    # --- Modalidad de atencion ---------------------------------------------
    # La Resolucion 2654 de 2019 obliga a dejar constancia de si la atencion fue
    # presencial o por telemedicina, y en este ultimo caso a registrar el
    # consentimiento informado especifico para esa modalidad.
    care_modality = db.Column(db.String(30), default=CARE_IN_PERSON, nullable=True)
    telemedicine_consent_id = db.Column(
        db.Integer, db.ForeignKey('informed_consent_log.id'), nullable=True)

    # --- Anulacion ----------------------------------------------------------
    # Una orden mal emitida no se borra ni se edita: se anula dejando constancia.
    # Antes solo se podia esperar a que venciera, de modo que una orden con un
    # error seguia siendo dispensable hasta su fecha de caducidad.
    annulled_at = db.Column(db.DateTime, nullable=True)
    annulled_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    annulment_reason = db.Column(EncryptedText, nullable=True)
    replaced_by_order_id = db.Column(db.Integer, db.ForeignKey('medical_order.id'), nullable=True)

    # --- Prescripcion no financiada con UPC ---------------------------------
    # Los medicamentos fuera del plan de beneficios se prescriben por MIPRES.
    # El sistema no se integra con esa plataforma; guarda el numero para que la
    # orden y el reporte oficial queden vinculados.
    mipres_number = db.Column(db.String(40), nullable=True, index=True)

    # --- Firma profesional ---
    # `signature_hash` sella el contenido de la orden junto con la identidad y el
    # registro medico de quien la firma. Permite demostrar despues que la orden
    # no se altero y quien la autorizo.
    signature_hash = db.Column(db.String(256), nullable=True)
    doctor_registration = db.Column(db.String(50), nullable=True)
    signed_at = db.Column(db.DateTime, nullable=True)

    # --- Verificacion de seguridad clinica ---
    safety_report_json = db.Column(db.Text, nullable=True)
    safety_kb_version = db.Column(db.String(30), nullable=True)
    safety_override_reason = db.Column(EncryptedText, nullable=True)
    safety_override_at = db.Column(db.DateTime, nullable=True)

    verification_hash = db.Column(db.String(512), nullable=False, index=True)
    hash_seguridad = db.Column(db.String(512), nullable=True, index=True)
    status = db.Column(db.String(30), default='pendiente', nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=colombia_now, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    fecha_expiracion = db.Column(db.DateTime, nullable=True)
    doctor = db.relationship('User', foreign_keys=[doctor_id])
    patient = db.relationship('User', foreign_keys=[patient_id])

class MedicationPickupTicket(ClinicScoped, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('medical_order.id'), nullable=True, index=True)
    parent_ticket_id = db.Column(db.Integer, db.ForeignKey('medication_pickup_ticket.id'), nullable=True, index=True)  # For sub-tickets (partial deliveries)
    pharmacy_id = db.Column(db.Integer, db.ForeignKey('pharmacy.id'), nullable=True, index=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    staff_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    expendedor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    pickup_code = db.Column(db.String(24), unique=True, nullable=False, index=True)
    pickup_hash = db.Column(db.String(512), unique=True, nullable=False, index=True)
    meds_json = db.Column(db.Text, nullable=False)  # Medications requested in this ticket
    delivered_json = db.Column(db.Text, nullable=True)  # Medications actually delivered (partial)
    pickup_date = db.Column(db.String(10), nullable=False)
    pickup_time = db.Column(db.String(5), nullable=False)
    pickup_location = db.Column(db.String(220), nullable=True)
    note = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(30), default='autorizado', nullable=False, index=True)
    # Status values: 'autorizado' | 'sin_stock' | 'parcial' | 'entregado' | 'cancelado'
    priority = db.Column(db.String(20), default='normal', nullable=False, index=True)  # 'normal' | 'alta'
    is_partial = db.Column(db.Boolean, default=False, nullable=False)  # True = this is a partial delivery sub-ticket
    created_at = db.Column(db.DateTime, default=colombia_now, nullable=False)
    delivered_at = db.Column(db.DateTime, nullable=True)
    order = db.relationship('MedicalOrder')
    pharmacy = db.relationship('Pharmacy')
    patient = db.relationship('User', foreign_keys=[patient_id])
    staff = db.relationship('User', foreign_keys=[staff_id])
    expendedor = db.relationship('User', foreign_keys=[expendedor_id])
    parent_ticket = db.relationship('MedicationPickupTicket', remote_side='MedicationPickupTicket.id',
                                    foreign_keys=[parent_ticket_id], backref='sub_tickets')

class ReplenishmentAlert(ClinicScoped, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pharmacy_id = db.Column(db.Integer, db.ForeignKey('pharmacy.id'), nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey('medical_order.id'), nullable=True, index=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('medication_pickup_ticket.id'), nullable=True, index=True)
    med_name = db.Column(db.String(180), nullable=False, index=True)
    required_quantity = db.Column(db.Integer, default=0, nullable=False)
    available_quantity = db.Column(db.Integer, default=0, nullable=False)
    status = db.Column(db.String(30), default='abierta', nullable=False, index=True)
    note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=colombia_now, nullable=False)
    resolved_at = db.Column(db.DateTime, nullable=True)
    pharmacy = db.relationship('Pharmacy')
    order = db.relationship('MedicalOrder')
    ticket = db.relationship('MedicationPickupTicket')

class StockTransferRequest(ClinicScoped, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    source_pharmacy_id = db.Column(db.Integer, db.ForeignKey('pharmacy.id'), nullable=False, index=True)
    target_pharmacy_id = db.Column(db.Integer, db.ForeignKey('pharmacy.id'), nullable=False, index=True)
    requester_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    med_name = db.Column(db.String(180), nullable=False, index=True)
    quantity = db.Column(db.Integer, default=1, nullable=False)
    status = db.Column(db.String(30), default='solicitada', nullable=False, index=True)
    note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=colombia_now, nullable=False)
    resolved_at = db.Column(db.DateTime, nullable=True)
    source_pharmacy = db.relationship('Pharmacy', foreign_keys=[source_pharmacy_id])
    target_pharmacy = db.relationship('Pharmacy', foreign_keys=[target_pharmacy_id])
    requester = db.relationship('User', foreign_keys=[requester_id])

class Lead(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(180), nullable=False)
    institucion = db.Column(db.String(220), nullable=True)
    telefono = db.Column(db.String(80), nullable=True)
    email = db.Column(db.String(180), nullable=True)
    ciudad = db.Column(db.String(120), nullable=True)
    contacto = db.Column(db.String(300), nullable=True)
    mensaje = db.Column(db.Text, nullable=True)
    fecha = db.Column(db.DateTime, default=colombia_now, nullable=False)

class AuditLog(db.Model):
    """Registro de auditoria con encadenamiento hash.

    Cada entrada incorpora el hash de la anterior. Alterar o eliminar una entrada
    rompe la cadena en ese punto y en todas las siguientes, de modo que la
    manipulacion posterior es detectable aunque quien la haga tenga acceso
    directo a la base de datos. `manage.py verify-audit-chain` recorre la cadena.
    """
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=True, index=True)
    clinic_id = db.Column(db.Integer, nullable=True, index=True)
    event = db.Column(db.String(100), nullable=False, index=True)
    path = db.Column(db.String(300), nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(300), nullable=True)
    details = db.Column(EncryptedText, nullable=True)
    timestamp = db.Column(db.DateTime, default=colombia_now, index=True)
    # Encadenamiento: hash de esta entrada y de la inmediatamente anterior.
    entry_hash = db.Column(db.String(64), nullable=True, index=True)
    previous_hash = db.Column(db.String(64), nullable=True)

class PasswordHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=colombia_now)

class JWTRevokedToken(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    jti = db.Column(db.String(128), unique=True, nullable=False, index=True)
    user_id = db.Column(db.Integer, nullable=True)
    token_type = db.Column(db.String(20), nullable=False)
    revoked_at = db.Column(db.DateTime, default=colombia_now)

class InformedConsentLog(db.Model):
    """Constancia de consentimiento informado.

    Registra tambien *que texto* acepto el titular: sin la version y el hash del
    documento vigente en ese momento, la constancia no permite demostrar el
    alcance de lo consentido si el texto cambia despues.
    """
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinic.id'), nullable=True)
    consent_type = db.Column(db.String(50), nullable=False, index=True)
    document_version = db.Column(db.String(30), nullable=True)
    document_hash = db.Column(db.String(64), nullable=True)
    granted = db.Column(db.Boolean, default=True, nullable=False)
    revoked_at = db.Column(db.DateTime, nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(300), nullable=True)
    digital_signature_hash = db.Column(db.String(128), nullable=False)
    timestamp = db.Column(db.DateTime, default=colombia_now, index=True)
    patient = db.relationship('User', foreign_keys=[patient_id])

class CIE10(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(10), unique=True, nullable=False, index=True)
    description = db.Column(db.Text, nullable=False)

class CUPS(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    description = db.Column(db.Text, nullable=False)

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinic.id'), nullable=True)
    title = db.Column(db.String(150), nullable=False)
    message = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    type = db.Column(db.String(50), nullable=True)
    timestamp = db.Column(db.DateTime, default=colombia_now)
    user = db.relationship('User', foreign_keys=[user_id])


class IncomingShipment(ClinicScoped, db.Model):
    """Tracks a medication shipment 'en camino' to a pharmacy."""
    id = db.Column(db.Integer, primary_key=True)
    pharmacy_id = db.Column(db.Integer, db.ForeignKey('pharmacy.id'), nullable=False, index=True)
    supplier_name = db.Column(db.String(180), nullable=True)
    expected_date = db.Column(db.String(10), nullable=False)  # YYYY-MM-DD
    note = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(30), default='en_camino', nullable=False, index=True)
    # Status: 'en_camino' | 'recibido' | 'cancelado'
    created_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    received_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=colombia_now, nullable=False)
    pharmacy = db.relationship('Pharmacy')
    created_by = db.relationship('User', foreign_keys=[created_by_user_id])
    items = db.relationship('IncomingShipmentItem', backref='shipment', lazy=True)


class IncomingShipmentItem(ClinicScoped, db.Model):
    """Individual medication item within an incoming shipment."""
    id = db.Column(db.Integer, primary_key=True)
    shipment_id = db.Column(db.Integer, db.ForeignKey('incoming_shipment.id'), nullable=False, index=True)
    med_name = db.Column(db.String(180), nullable=False, index=True)
    quantity_incoming = db.Column(db.Integer, nullable=False, default=0)
    quantity_reserved = db.Column(db.Integer, default=0, nullable=False)  # Auto-reserved for pending tickets
    reserved_ticket_ids_json = db.Column(db.Text, nullable=True)  # JSON list of ticket IDs with reservations
    created_at = db.Column(db.DateTime, default=colombia_now, nullable=False)


# =============================================================================
# Seguridad clinica del paciente
# =============================================================================

class PatientAllergy(ClinicScoped, db.Model):
    """Alergia o reaccion adversa registrada de un paciente.

    Es la fuente que consulta `clinical_safety` antes de permitir la firma de una
    orden. Se conserva quien la registro y cuando: una alergia mal atribuida
    tambien causa dano, al cerrar opciones terapeuticas validas sin fundamento.
    """
    __tablename__ = 'patient_allergy'

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    substance = db.Column(EncryptedText, nullable=False)
    # Copia normalizada y sin cifrar del principio activo, para poder comparar en
    # base de datos. Por si sola no identifica al paciente.
    substance_normalized = db.Column(db.String(180), nullable=True, index=True)
    reaction = db.Column(EncryptedText, nullable=True)
    severity = db.Column(db.String(20), default='moderada', nullable=False)   # leve|moderada|grave|anafilaxia
    status = db.Column(db.String(20), default='reportada', nullable=False, index=True)  # confirmada|reportada|descartada
    onset_date = db.Column(db.Date, nullable=True)
    notes = db.Column(EncryptedText, nullable=True)
    recorded_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=colombia_now, nullable=False)
    updated_at = db.Column(db.DateTime, default=colombia_now, onupdate=colombia_now)

    patient = db.relationship('User', foreign_keys=[patient_id], backref='allergies')
    recorded_by = db.relationship('User', foreign_keys=[recorded_by_id])

    @property
    def is_active(self):
        return self.status != 'descartada'


class PatientChronicCondition(ClinicScoped, db.Model):
    """Condicion cronica activa. Aporta contexto a la verificacion clinica."""
    __tablename__ = 'patient_chronic_condition'

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    condition = db.Column(EncryptedText, nullable=False)
    cie10_code = db.Column(db.String(10), nullable=True, index=True)
    status = db.Column(db.String(20), default='activa', nullable=False, index=True)
    diagnosed_on = db.Column(db.Date, nullable=True)
    recorded_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=colombia_now, nullable=False)

    patient = db.relationship('User', foreign_keys=[patient_id], backref='chronic_conditions')
    recorded_by = db.relationship('User', foreign_keys=[recorded_by_id])


# =============================================================================
# Libro mayor de inventario y dispensacion
# =============================================================================

class StockLedgerEntry(ClinicScoped, db.Model):
    """Asiento inmutable de movimiento de inventario.

    Ningun cambio de existencias debe ocurrir sin su asiento correspondiente.
    `balance_before` y `balance_after` permiten reconstruir el saldo en cualquier
    momento del pasado y detectar mutaciones hechas por fuera de la aplicacion.
    Va encadenado por hash, igual que el registro de auditoria.
    """
    __tablename__ = 'stock_ledger_entry'

    id = db.Column(db.Integer, primary_key=True)
    stock_id = db.Column(db.Integer, db.ForeignKey('stock.id'), nullable=True, index=True)
    pharmacy_id = db.Column(db.Integer, db.ForeignKey('pharmacy.id'), nullable=True, index=True)
    med_name = db.Column(db.String(180), nullable=False, index=True)

    movement_type = db.Column(db.String(30), nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False)          # positivo entra, negativo sale
    balance_before = db.Column(db.Integer, nullable=False)
    balance_after = db.Column(db.Integer, nullable=False)
    committed_before = db.Column(db.Integer, default=0, nullable=False)
    committed_after = db.Column(db.Integer, default=0, nullable=False)

    batch_number = db.Column(db.String(80), nullable=True)
    expiry_date = db.Column(db.Date, nullable=True)

    ticket_id = db.Column(db.Integer, db.ForeignKey('medication_pickup_ticket.id'), nullable=True, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey('medical_order.id'), nullable=True, index=True)
    shipment_id = db.Column(db.Integer, db.ForeignKey('incoming_shipment.id'), nullable=True)

    performed_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    reason = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, default=colombia_now, nullable=False, index=True)

    entry_hash = db.Column(db.String(64), nullable=True, index=True)
    previous_hash = db.Column(db.String(64), nullable=True)

    stock = db.relationship('Stock')
    pharmacy = db.relationship('Pharmacy')
    performed_by = db.relationship('User', foreign_keys=[performed_by_id])


class DispensingLedgerEntry(ClinicScoped, db.Model):
    """Acta de entrega de un medicamento a un paciente.

    Un asiento por medicamento y por acto de entrega. A diferencia de
    `delivered_json`, que se sobrescribia en cada entrega parcial y destruia el
    historial, estos asientos nunca se modifican: son la trazabilidad que exige
    el servicio farmaceutico.
    """
    __tablename__ = 'dispensing_ledger_entry'

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('medication_pickup_ticket.id'), nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey('medical_order.id'), nullable=True, index=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    pharmacy_id = db.Column(db.Integer, db.ForeignKey('pharmacy.id'), nullable=True, index=True)
    dispensed_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)

    med_name = db.Column(db.String(180), nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False)
    unit = db.Column(db.String(40), default='unidad', nullable=False)
    batch_number = db.Column(db.String(80), nullable=True)
    expiry_date = db.Column(db.Date, nullable=True)

    # Verificacion de identidad de quien retira. Se guarda el indice ciego del
    # documento presentado, nunca el documento en claro.
    receiver_kind = db.Column(db.String(20), default='paciente', nullable=False)  # paciente|tercero
    receiver_name = db.Column(EncryptedText, nullable=True)
    receiver_document_hash = db.Column(db.String(64), nullable=True)
    identity_verified = db.Column(db.Boolean, default=False, nullable=False)

    dispensed_at = db.Column(db.DateTime, default=colombia_now, nullable=False, index=True)
    notes = db.Column(EncryptedText, nullable=True)

    entry_hash = db.Column(db.String(64), nullable=True, index=True)
    previous_hash = db.Column(db.String(64), nullable=True)

    ticket = db.relationship('MedicationPickupTicket', backref='dispensing_entries')
    order = db.relationship('MedicalOrder')
    patient = db.relationship('User', foreign_keys=[patient_id])
    dispensed_by = db.relationship('User', foreign_keys=[dispensed_by_id])
    pharmacy = db.relationship('Pharmacy')


# =============================================================================
# Autenticacion
# =============================================================================

class PasswordResetToken(db.Model):
    """Token de un solo uso para restablecer contrasena.

    Se almacena solo el hash del token: quien lea la base de datos no puede
    usarlo para tomar cuentas ajenas.
    """
    __tablename__ = 'password_reset_token'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    issued_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    issued_ip = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=colombia_now, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    used_at = db.Column(db.DateTime, nullable=True)
    used_ip = db.Column(db.String(64), nullable=True)

    user = db.relationship('User', foreign_keys=[user_id])
    issued_by = db.relationship('User', foreign_keys=[issued_by_id])

    def is_usable(self, now=None):
        now = now or colombia_now()
        return self.used_at is None and self.expires_at > now


class LoginAttempt(db.Model):
    """Contador de intentos fallidos, persistido en base de datos.

    En memoria del proceso el contador se perdia al reiniciar y no se compartia
    entre workers, de modo que el bloqueo era evitable. Aqui es global al sistema
    y sobrevive a los reinicios.
    """
    __tablename__ = 'login_attempt'

    id = db.Column(db.Integer, primary_key=True)
    # Origen + usuario, normalizados y con hash.
    attempt_key = db.Column(db.String(64), unique=True, nullable=False, index=True)
    ip_address = db.Column(db.String(64), nullable=True)
    failure_count = db.Column(db.Integer, default=0, nullable=False)
    first_failure_at = db.Column(db.DateTime, default=colombia_now, nullable=False)
    last_failure_at = db.Column(db.DateTime, default=colombia_now, nullable=False)
    locked_until = db.Column(db.DateTime, nullable=True, index=True)


# =============================================================================
# Habeas data (Ley 1581 de 2012)
# =============================================================================

class DataSubjectRequest(db.Model):
    """Solicitud del titular sobre sus datos personales.

    La ley obliga a atender consulta, rectificacion, supresion y revocatoria en
    plazos determinados. Sin registro de la solicitud no hay forma de acreditar
    que se cumplieron.
    """
    __tablename__ = 'data_subject_request'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinic.id'), nullable=True, index=True)
    request_type = db.Column(db.String(30), nullable=False, index=True)   # acceso|rectificacion|supresion|revocatoria
    status = db.Column(db.String(30), default='recibida', nullable=False, index=True)
    detail = db.Column(EncryptedText, nullable=True)
    resolution_note = db.Column(EncryptedText, nullable=True)
    requested_at = db.Column(db.DateTime, default=colombia_now, nullable=False, index=True)
    due_at = db.Column(db.DateTime, nullable=True, index=True)   # plazo legal de respuesta
    resolved_at = db.Column(db.DateTime, nullable=True)
    resolved_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    requester_ip = db.Column(db.String(64), nullable=True)

    user = db.relationship('User', foreign_keys=[user_id])
    resolved_by = db.relationship('User', foreign_keys=[resolved_by_id])

    @property
    def is_overdue(self):
        if self.resolved_at or not self.due_at:
            return False
        return colombia_now() > self.due_at


# =============================================================================
# Facturacion
# =============================================================================
#
# Dos cosas que el modelo anterior no contemplaba y que en Colombia no son
# opcionales:
#
# 1. **Quien factura.** Una consulta puede facturarla la clinica (persona
#    juridica, con su NIT y su resolucion de numeracion) o el medico
#    independiente (persona natural, con su propia numeracion y su propio perfil
#    tributario). Son emisores distintos, con numeraciones distintas que no
#    pueden mezclarse: cada resolucion de la DIAN autoriza un rango a un emisor
#    concreto.
#
# 2. **El dinero.** Estaba en `Float`. A las magnitudes de esta aplicacion no
#    produce errores —lo comprobe— pero es el tipo equivocado para dinero: basta
#    con acumular o comparar para que aparezca la diferencia. `Numeric` es exacto
#    por construccion.
#
# Lo que este modulo **no** hace: enviar la factura a la DIAN. Eso exige un
# proveedor tecnologico autorizado y las credenciales del prestador. La
# estructura queda lista para enchufarlo (`billing.py`).

INVOICE_DRAFT = 'borrador'
INVOICE_ISSUED = 'emitida'
INVOICE_SENT = 'radicada'          # entregada al proveedor tecnologico
INVOICE_ACCEPTED = 'aceptada'      # validada por la DIAN
INVOICE_REJECTED = 'rechazada'
INVOICE_ANNULLED = 'anulada'

# Tipo de documento soporte.
DOC_ELECTRONIC_INVOICE = 'factura_electronica'
DOC_EQUIVALENT = 'documento_equivalente'
DOC_SUPPORT = 'documento_soporte'     # cuando el adquiriente no esta obligado
DOC_CREDIT_NOTE = 'nota_credito'

ISSUER_CLINIC = 'clinica'
ISSUER_INDEPENDENT = 'profesional_independiente'


class BillingProfile(db.Model):
    """Perfil tributario de quien emite. Una clinica o un medico independiente.

    El medico independiente factura a su propio nombre: su documento, su
    direccion fiscal y su propia resolucion de numeracion de la DIAN. Mezclar su
    numeracion con la de la clinica invalidaria ambas.

    Nada de lo que hay aqui determina las obligaciones tributarias de nadie: son
    los datos que el titular declara, para que la aplicacion los use tal cual.
    Quien decide si esta obligado a facturar electronicamente es su contador.
    """
    __tablename__ = 'billing_profile'

    id = db.Column(db.Integer, primary_key=True)
    issuer_kind = db.Column(db.String(30), nullable=False, index=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinic.id'), nullable=True, index=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)

    # --- Identificacion fiscal ---
    legal_name = db.Column(db.String(220), nullable=False)
    document_type = db.Column(db.String(4), default='NIT', nullable=False)  # NIT | CC
    document_number = db.Column(db.String(40), nullable=False)
    verification_digit = db.Column(db.String(1), nullable=True)
    fiscal_address = db.Column(db.String(300), nullable=True)
    department_code = db.Column(db.String(2), nullable=True)
    municipality_code = db.Column(db.String(3), nullable=True)
    email = db.Column(db.String(180), nullable=True)
    phone = db.Column(db.String(80), nullable=True)

    # --- Regimen ---
    # Los servicios de salud humana estan excluidos de IVA (Estatuto Tributario,
    # articulo 476 numeral 1). Se declara en la factura, no se calcula.
    is_vat_responsible = db.Column(db.Boolean, default=False, nullable=False)
    tax_regime = db.Column(db.String(60), nullable=True)
    economic_activity_code = db.Column(db.String(10), nullable=True)  # CIIU

    # --- Numeracion autorizada por la DIAN ---
    resolution_number = db.Column(db.String(40), nullable=True)
    resolution_date = db.Column(db.Date, nullable=True)
    resolution_valid_until = db.Column(db.Date, nullable=True)
    invoice_prefix = db.Column(db.String(10), nullable=True)
    range_from = db.Column(db.Integer, nullable=True)
    range_to = db.Column(db.Integer, nullable=True)
    # Ultimo consecutivo usado. La asignacion se hace bajo bloqueo de fila.
    last_number = db.Column(db.Integer, default=0, nullable=False)

    # --- Proveedor tecnologico ---
    provider_name = db.Column(db.String(80), nullable=True)
    provider_configured = db.Column(db.Boolean, default=False, nullable=False)

    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=colombia_now, nullable=False)
    updated_at = db.Column(db.DateTime, default=colombia_now, onupdate=colombia_now)

    clinic = db.relationship('Clinic')
    doctor = db.relationship('User', foreign_keys=[doctor_id])

    @property
    def display_document(self):
        if self.document_type == 'NIT' and self.verification_digit:
            return f'{self.document_number}-{self.verification_digit}'
        return self.document_number

    @property
    def has_numbering(self):
        return bool(self.resolution_number and self.range_from and self.range_to)

    def numbering_exhausted(self):
        return bool(self.range_to and self.last_number >= self.range_to)

    def numbering_expired(self, today=None):
        if not self.resolution_valid_until:
            return False
        from datetime import date as _date
        return self.resolution_valid_until < (today or _date.today())


class Invoice(db.Model):
    """Documento de cobro por un servicio prestado."""
    __tablename__ = 'invoice'

    id = db.Column(db.Integer, primary_key=True)
    billing_profile_id = db.Column(
        db.Integer, db.ForeignKey('billing_profile.id'), nullable=False, index=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinic.id'), nullable=True, index=True)

    document_type = db.Column(db.String(30), default=DOC_ELECTRONIC_INVOICE, nullable=False)
    # Numero completo con prefijo, tal como se radica.
    number = db.Column(db.String(40), nullable=True, unique=True, index=True)
    consecutive = db.Column(db.Integer, nullable=True)

    # --- Adquiriente ---
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    buyer_name = db.Column(EncryptedText, nullable=True)
    buyer_document_type = db.Column(db.String(4), nullable=True)
    buyer_document = db.Column(EncryptedText, nullable=True)

    # --- Importes. `Numeric` y no `Float`: el dinero debe ser exacto. ---
    currency = db.Column(db.String(3), default='COP', nullable=False)
    subtotal = db.Column(db.Numeric(14, 2), default=0, nullable=False)
    discount_amount = db.Column(db.Numeric(14, 2), default=0, nullable=False)
    tax_amount = db.Column(db.Numeric(14, 2), default=0, nullable=False)
    total = db.Column(db.Numeric(14, 2), default=0, nullable=False)
    # Motivo por el que no se cobra IVA. Debe constar en el documento.
    tax_exclusion_note = db.Column(db.String(300), nullable=True)

    # --- Concepto ---
    concept = db.Column(db.String(300), nullable=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointment.id'), nullable=True)
    chat_id = db.Column(db.Integer, db.ForeignKey('chat.id'), nullable=True)
    subscription_id = db.Column(
        db.Integer, db.ForeignKey('patient_doctor_subscription.id'), nullable=True)
    payment_ticket_id = db.Column(
        db.Integer, db.ForeignKey('payment_verification_ticket.id'), nullable=True)

    status = db.Column(db.String(30), default=INVOICE_DRAFT, nullable=False, index=True)
    issued_at = db.Column(db.DateTime, nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=colombia_now, nullable=False)

    # --- Trazabilidad con la DIAN ---
    cufe = db.Column(db.String(120), nullable=True, index=True)
    provider_reference = db.Column(db.String(120), nullable=True)
    provider_response = db.Column(db.Text, nullable=True)
    sent_at = db.Column(db.DateTime, nullable=True)

    # --- Anulacion ---
    # Una factura emitida no se borra ni se edita: se anula con nota credito.
    annulled_at = db.Column(db.DateTime, nullable=True)
    annulment_reason = db.Column(db.String(300), nullable=True)
    credit_note_for_id = db.Column(db.Integer, db.ForeignKey('invoice.id'), nullable=True)

    entry_hash = db.Column(db.String(64), nullable=True, index=True)
    previous_hash = db.Column(db.String(64), nullable=True)

    billing_profile = db.relationship('BillingProfile')
    patient = db.relationship('User', foreign_keys=[patient_id])
    credit_note_for = db.relationship('Invoice', remote_side='Invoice.id')

    @property
    def is_editable(self):
        return self.status == INVOICE_DRAFT

    @property
    def is_annulled(self):
        return self.annulled_at is not None


class InvoiceLine(db.Model):
    """Renglon de una factura."""
    __tablename__ = 'invoice_line'

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'), nullable=False, index=True)
    description = db.Column(db.String(300), nullable=False)
    cups_code = db.Column(db.String(20), nullable=True)
    quantity = db.Column(db.Numeric(10, 2), default=1, nullable=False)
    unit_price = db.Column(db.Numeric(14, 2), default=0, nullable=False)
    discount_amount = db.Column(db.Numeric(14, 2), default=0, nullable=False)
    tax_rate = db.Column(db.Numeric(5, 2), default=0, nullable=False)
    line_total = db.Column(db.Numeric(14, 2), default=0, nullable=False)

    invoice = db.relationship('Invoice', backref=db.backref('lines', lazy=True))


class LegalConfiguration(db.Model):
    """Datos del prestador que completan los textos legales.

    Los documentos de `legal_documents.py` llevan marcadores como
    `[[NIT_OPERADOR]]`. Un documento legal con un marcador sin reemplazar no es
    un documento legal, así que estos valores son los que lo vuelven publicable.

    Se guardan como pares clave/valor y no como columnas fijas porque los textos
    evolucionan: añadir una cláusula que exija un dato nuevo no debe requerir una
    migración de esquema.
    """
    __tablename__ = 'legal_configuration'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(60), unique=True, nullable=False, index=True)
    value = db.Column(db.Text, nullable=True)
    updated_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    updated_at = db.Column(db.DateTime, default=colombia_now, onupdate=colombia_now)

    updated_by = db.relationship('User', foreign_keys=[updated_by_id])


class RetentionPolicy(db.Model):
    """Politica de retencion documental por tipo de registro.

    La historia clinica se conserva como minimo 15 anos (Resolucion 839 de 2017);
    otros registros tienen plazos distintos. Tener la politica en datos, y no en
    la memoria de alguien, es lo que permite auditarla.
    """
    __tablename__ = 'retention_policy'

    id = db.Column(db.Integer, primary_key=True)
    record_type = db.Column(db.String(80), unique=True, nullable=False, index=True)
    retention_years = db.Column(db.Integer, nullable=False)
    legal_basis = db.Column(db.String(300), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, default=colombia_now, onupdate=colombia_now)


# =============================================================================
# Interoperabilidad IHCE (Resolucion 1888 de 2025)
# =============================================================================

RDA_PENDIENTE = 'pendiente'
RDA_ENVIANDO = 'enviando'
RDA_ACEPTADO = 'aceptado'
RDA_DUPLICADO = 'duplicado'
RDA_RECHAZADO = 'rechazado'
RDA_BLOQUEADO = 'bloqueado'

RDA_ESTADOS_FINALES = (RDA_ACEPTADO, RDA_DUPLICADO)


class RDASubmission(ClinicScoped, db.Model):
    """Envio del Resumen Digital de Atencion al IHCE.

    Por que hay una cola y no una llamada directa
    ---------------------------------------------
    La Resolucion 1888 de 2025 obliga a remitir un RDA por cada atencion. La
    tentacion es llamar al Ministerio al cerrar la consulta, pero eso ata la
    atencion clinica a que la red responda. En un puesto de salud rural la red
    es justamente lo que falla, y un profesional no puede quedarse sin poder
    cerrar una historia porque un servidor de Bogota no contesta.

    Asi que al cerrar la atencion solo se encola. Un proceso aparte transmite y
    reintenta. La atencion nunca depende de la disponibilidad del Ministerio, y
    el deber de remitir queda registrado en una tabla que puede auditarse: en
    cualquier momento se puede responder cuantas atenciones estan pendientes de
    remision y por que.

    Estados
    -------
    pendiente  encolado, aun no transmitido
    enviando   tomado por un proceso, en vuelo
    aceptado   el Ministerio lo recibio (200)
    duplicado  el Ministerio ya lo tenia (409). Cumple igual: no se reintenta
    rechazado  400, estructura invalida. Necesita correccion, no reintento
    bloqueado  faltan datos locales para armarlo. Se corrige en la interfaz
    """
    __tablename__ = 'rda_submission'

    id = db.Column(db.Integer, primary_key=True)
    medical_history_id = db.Column(db.Integer, db.ForeignKey('medical_history.id'),
                                   nullable=False, index=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    status = db.Column(db.String(20), default=RDA_PENDIENTE, nullable=False, index=True)
    attempts = db.Column(db.Integer, default=0, nullable=False)
    # Huella del Bundle transmitido. Permite demostrar despues exactamente que
    # se envio, sin conservar una segunda copia del dato clinico.
    bundle_hash = db.Column(db.String(64), nullable=True)
    # Identificador que devuelve el Ministerio. Es el acuse de recibo.
    remote_id = db.Column(db.String(120), nullable=True, index=True)
    # Motivo del ultimo fallo. Solo texto del OperationOutcome, que describe el
    # campo invalido y no el dato del paciente.
    last_error = db.Column(db.Text, nullable=True)
    guide_version = db.Column(db.String(20), nullable=True)

    created_at = db.Column(db.DateTime, default=colombia_now, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=colombia_now, onupdate=colombia_now)
    next_attempt_at = db.Column(db.DateTime, nullable=True, index=True)
    sent_at = db.Column(db.DateTime, nullable=True)

    medical_history = db.relationship('MedicalHistory', foreign_keys=[medical_history_id])
    patient = db.relationship('User', foreign_keys=[patient_id])
    doctor = db.relationship('User', foreign_keys=[doctor_id])

    __table_args__ = (
        # Una atencion se remite una sola vez. Si el proceso se ejecuta dos
        # veces en paralelo, la base lo impide en lugar de confiar en el codigo.
        db.UniqueConstraint('medical_history_id', name='uq_rda_por_atencion'),
    )

    @property
    def is_final(self):
        return self.status in RDA_ESTADOS_FINALES

    @property
    def needs_attention(self):
        """Requiere que una persona intervenga."""
        return self.status in (RDA_RECHAZADO, RDA_BLOQUEADO)


class RIPSReferenceCode(db.Model):
    """Tabla de referencia del RIPS publicada por el Ministerio en SISPRO.

    Por que vive en base de datos y no en el codigo
    ----------------------------------------------
    Son catalogos oficiales que el Ministerio actualiza sin avisar y sin
    cambiar la norma. Quemarlos en el codigo obliga a desplegar cada vez que
    cambian, y garantiza que tarde o temprano se queden viejos.

    Ademas, el codigo semilla que trae la aplicacion es **parcial**: cubre las
    primeras entradas de cada tabla. La carga completa se hace con
    `manage.py load-rips-tables`, contra el archivo que publica el Ministerio.
    Hasta entonces, exportar un RIPS con un codigo que no este aqui se rechaza
    en lugar de radicarse: la regla RVC096 de la Resolucion 948 de 2026 bloquea
    los codigos de relleno, y es mejor detectarlo antes de enviarlo.
    """
    __tablename__ = 'rips_reference_code'

    id = db.Column(db.Integer, primary_key=True)
    table_name = db.Column(db.String(60), nullable=False, index=True)
    code = db.Column(db.String(10), nullable=False)
    description = db.Column(db.String(250), nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    updated_at = db.Column(db.DateTime, default=colombia_now, onupdate=colombia_now)

    __table_args__ = (
        db.UniqueConstraint('table_name', 'code', name='uq_rips_ref_tabla_codigo'),
    )

    @staticmethod
    def opciones(tabla):
        """Codigos activos de una tabla, ordenados. Para los desplegables."""
        filas = (RIPSReferenceCode.query
                 .filter_by(table_name=tabla, active=True)
                 .order_by(RIPSReferenceCode.code).all())
        return [(f.code, f.description) for f in filas]

    @staticmethod
    def es_valido(tabla, codigo):
        if not codigo:
            return False
        return RIPSReferenceCode.query.filter_by(
            table_name=tabla, code=str(codigo).strip(), active=True).first() is not None


# Nombres de las tablas de referencia que usa la aplicacion.
TABLA_CAUSA_EXTERNA = 'RIPSCausaExternaVersion2'
TABLA_FINALIDAD = 'RIPSFinalidadConsultaVersion2'
TABLA_MODALIDAD = 'ModalidadAtencion'
TABLA_TIPO_USUARIO = 'RIPSTipoUsuarioVersion2'
TABLA_ZONA = 'ZonaVersion2'
TABLA_CONCEPTO_RECAUDO = 'conceptoRecaudo'
TABLA_TIPO_DIAGNOSTICO = 'RIPSTipoDiagnosticoPrincipal'


# =============================================================================
# Vigilancia en salud publica (Decreto 3518 de 2006)
# =============================================================================

SIVIGILA_INMEDIATA = 'inmediata'
SIVIGILA_SEMANAL = 'semanal'

SIVIGILA_PENDIENTE = 'pendiente'
SIVIGILA_NOTIFICADA = 'notificada'
SIVIGILA_DESCARTADA = 'descartada'


class NotifiableEvent(db.Model):
    """Evento de interes en salud publica del catalogo del INS.

    El catalogo vive en base de datos por lo mismo que las tablas del RIPS: el
    Instituto Nacional de Salud lo actualiza cada ano en sus lineamientos, sin
    que cambie el decreto. La semilla que trae la aplicacion es PARCIAL y esta
    marcada como tal.
    """
    __tablename__ = 'notifiable_event'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(10), unique=True, nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    # `inmediata` obliga a notificar apenas se sospecha el caso; `semanal`
    # admite el cierre de la semana epidemiologica.
    periodicity = db.Column(db.String(12), default=SIVIGILA_SEMANAL, nullable=False)
    # Prefijos CIE-10 que disparan la sospecha, separados por coma. Se comparan
    # por prefijo porque un evento agrupa varios codigos (A90, A91... dengue).
    cie10_prefixes = db.Column(db.String(300), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    active = db.Column(db.Boolean, default=True, nullable=False)

    @property
    def prefixes(self):
        return [p.strip().upper() for p in (self.cie10_prefixes or '').split(',') if p.strip()]

    @property
    def is_immediate(self):
        return self.periodicity == SIVIGILA_INMEDIATA


class SivigilaNotification(ClinicScoped, db.Model):
    """Caso detectado que debe notificarse al Sivigila.

    Que hace y que NO hace
    ----------------------
    La notificacion al Sivigila se radica en el sistema del INS (Sivigila Web),
    no aqui. Esta aplicacion no puede radicarla por el prestador, y fingir que
    lo hace seria peor que no tener nada.

    Lo que si hace, que es donde estaba el hueco: **que un caso notificable no
    pase inadvertido**. Al guardar una atencion cuyo diagnostico corresponde a
    un evento de interes en salud publica, se abre este registro, se avisa a
    quien atiende y queda constancia de si se notifico, cuando y quien.

    El Decreto 3518 de 2006 obliga a notificar y preve sanciones. Antes de
    esto, el sistema no tenia forma de saber que un caso era notificable, asi
    que la unica defensa era que el profesional se acordara.
    """
    __tablename__ = 'sivigila_notification'

    id = db.Column(db.Integer, primary_key=True)
    medical_history_id = db.Column(db.Integer, db.ForeignKey('medical_history.id'),
                                   nullable=False, index=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    event_code = db.Column(db.String(10), nullable=False, index=True)
    event_name = db.Column(db.String(200), nullable=False)
    periodicity = db.Column(db.String(12), nullable=False)
    cie10_code = db.Column(db.String(10), nullable=True)

    status = db.Column(db.String(12), default=SIVIGILA_PENDIENTE, nullable=False, index=True)
    detected_at = db.Column(db.DateTime, default=colombia_now, nullable=False, index=True)
    due_at = db.Column(db.DateTime, nullable=True, index=True)
    notified_at = db.Column(db.DateTime, nullable=True)
    notified_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    # Numero de la ficha radicada en Sivigila. Es la prueba de que se notifico.
    ficha_reference = db.Column(db.String(60), nullable=True)
    resolution_note = db.Column(EncryptedText, nullable=True)

    medical_history = db.relationship('MedicalHistory', foreign_keys=[medical_history_id])
    patient = db.relationship('User', foreign_keys=[patient_id])
    notified_by = db.relationship('User', foreign_keys=[notified_by_id])

    __table_args__ = (
        db.UniqueConstraint('medical_history_id', 'event_code',
                            name='uq_sivigila_atencion_evento'),
    )

    @property
    def is_overdue(self):
        return (self.status == SIVIGILA_PENDIENTE and self.due_at is not None
                and colombia_now() > self.due_at)


# =============================================================================
# PQRS del servicio de salud
# =============================================================================
#
# Distinto del habeas data. `DataSubjectRequest` atiende lo que la Ley 1581
# obliga sobre los DATOS del titular. Esto atiende lo que el paciente reclama
# sobre LA ATENCION: que no le dieron cita, que lo trataron mal, que el
# medicamento no llego.
#
# Los documentos legales de la plataforma ya prometen por escrito un canal de
# PQRS y respuesta dentro de los quince dias habiles. Prometer un plazo sin el
# sistema que lo sostiene es peor que no prometerlo: deja la constancia del
# incumplimiento y ninguna del cumplimiento.

PQRS_PETICION = 'peticion'
PQRS_QUEJA = 'queja'
PQRS_RECLAMO = 'reclamo'
PQRS_SUGERENCIA = 'sugerencia'
PQRS_FELICITACION = 'felicitacion'

PQRS_TIPOS = (PQRS_PETICION, PQRS_QUEJA, PQRS_RECLAMO, PQRS_SUGERENCIA,
              PQRS_FELICITACION)

PQRS_RECIBIDA = 'recibida'
PQRS_EN_TRAMITE = 'en_tramite'
PQRS_RESUELTA = 'resuelta'

# Dias habiles de respuesta. Quince es el plazo que la plataforma se
# comprometio a cumplir en sus terminos y condiciones.
PQRS_DIAS_RESPUESTA = 15


class ServiceComplaint(ClinicScoped, db.Model):
    """Peticion, queja, reclamo, sugerencia o felicitacion sobre la atencion."""
    __tablename__ = 'service_complaint'

    id = db.Column(db.Integer, primary_key=True)
    # Numero visible para el usuario. Sin el, quien reclama no tiene con que
    # hacer seguimiento ni con que acreditar que radico.
    ticket_code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    complaint_type = db.Column(db.String(20), nullable=False, index=True)
    subject = db.Column(db.String(200), nullable=False)
    detail = db.Column(EncryptedText, nullable=False)

    status = db.Column(db.String(20), default=PQRS_RECIBIDA, nullable=False, index=True)
    submitted_at = db.Column(db.DateTime, default=colombia_now, nullable=False, index=True)
    due_at = db.Column(db.DateTime, nullable=True, index=True)
    resolved_at = db.Column(db.DateTime, nullable=True)
    resolved_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    resolution = db.Column(EncryptedText, nullable=True)
    # Una queja sobre una atencion concreta se vincula a ella; una sobre el
    # servicio en general, no.
    related_history_id = db.Column(db.Integer, db.ForeignKey('medical_history.id'),
                                   nullable=True)

    user = db.relationship('User', foreign_keys=[user_id])
    resolved_by = db.relationship('User', foreign_keys=[resolved_by_id])

    @property
    def is_overdue(self):
        if self.resolved_at or not self.due_at:
            return False
        return colombia_now() > self.due_at

    @property
    def is_open(self):
        return self.status != PQRS_RESUELTA


# =============================================================================
# Farmacovigilancia (Resolucion 1403 de 2007)
# =============================================================================
#
# La norma obliga al servicio farmaceutico a tener un programa de
# farmacovigilancia y a reportar al INVIMA las reacciones adversas a
# medicamentos.
#
# `PatientAllergy` no cubre esto, aunque lo parezca. Esa tabla registra la
# alergia DE UN PACIENTE para impedir una prescripcion futura: mira hacia
# adelante y es de uso clinico. El reporte de farmacovigilancia mira hacia
# atras y es de uso poblacional: sirve para que el INVIMA detecte que un lote,
# un principio activo o un fabricante estan causando dano a mucha gente.
#
# El sistema detectaba el riesgo antes de prescribir y no hacia nada con el
# evento cuando ocurria.

FARMACO_SOSPECHA = 'sospecha'
FARMACO_REPORTADO = 'reportado'
FARMACO_DESCARTADO = 'descartado'

# Seriedad segun la clasificacion que usa el formato de reporte del INVIMA.
SERIEDAD_NO_SERIA = 'no_seria'
SERIEDAD_SERIA = 'seria'

# Una reaccion seria se reporta en 72 horas; el resto, dentro del mes.
FARMACO_HORAS_SERIA = 72
FARMACO_DIAS_NO_SERIA = 30


class AdverseDrugEvent(ClinicScoped, db.Model):
    """Sospecha de reaccion adversa a un medicamento, para reporte al INVIMA.

    Que hace y que no
    -----------------
    No radica el reporte. El INVIMA lo recibe por su propio formato (FOREAM) y
    sus canales. Igual que con el Sivigila, fingir la radicacion dejaria al
    prestador creyendo que cumplio.

    Lo que hace es que el evento exista como registro: que se pueda abrir desde
    la consulta, que tenga plazo segun su seriedad, que quede quien lo reporto
    y con que numero, y que un evento serio no se pierda entre las notas de una
    historia clinica.
    """
    __tablename__ = 'adverse_drug_event'

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    reported_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    medical_history_id = db.Column(db.Integer, db.ForeignKey('medical_history.id'),
                                   nullable=True, index=True)

    # Medicamento sospechoso. El nombre normalizado permite agrupar sin
    # descifrar, igual que en PatientAllergy.
    medication = db.Column(EncryptedText, nullable=False)
    medication_normalized = db.Column(db.String(180), nullable=True, index=True)
    # Codigo unico de medicamento del INVIMA, cuando se conozca. Es lo que
    # permite al INVIMA llegar al lote y al fabricante.
    cum_code = db.Column(db.String(30), nullable=True, index=True)
    batch_number = db.Column(db.String(60), nullable=True)

    description = db.Column(EncryptedText, nullable=False)
    seriousness = db.Column(db.String(12), default=SERIEDAD_NO_SERIA,
                            nullable=False, index=True)
    onset_date = db.Column(db.Date, nullable=True)

    status = db.Column(db.String(12), default=FARMACO_SOSPECHA, nullable=False, index=True)
    detected_at = db.Column(db.DateTime, default=colombia_now, nullable=False, index=True)
    due_at = db.Column(db.DateTime, nullable=True, index=True)
    reported_at = db.Column(db.DateTime, nullable=True)
    # Numero del reporte radicado ante el INVIMA. Es la constancia.
    invima_reference = db.Column(db.String(60), nullable=True)
    resolution_note = db.Column(EncryptedText, nullable=True)

    patient = db.relationship('User', foreign_keys=[patient_id])
    reported_by = db.relationship('User', foreign_keys=[reported_by_id])
    medical_history = db.relationship('MedicalHistory', foreign_keys=[medical_history_id])

    @property
    def is_serious(self):
        return self.seriousness == SERIEDAD_SERIA

    @property
    def is_overdue(self):
        return (self.status == FARMACO_SOSPECHA and self.due_at is not None
                and colombia_now() > self.due_at)
