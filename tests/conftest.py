"""Infraestructura común de las pruebas.

Cada prueba recibe una aplicación y una base de datos limpias. Las pruebas que
tocan concurrencia usan un archivo SQLite en disco en lugar de memoria, porque
las conexiones en memoria no se comparten entre hilos.
"""

import os
import sys
import tempfile

import pytest
from flask.testing import FlaskClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Debe fijarse antes de importar la aplicación: `config.load_config` lo lee al
# construir la configuración.
os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('RURALHEALTH_SECRET_KEY', 'x' * 48)
os.environ.setdefault('RURALHEALTH_JWT_SECRET_KEY', 'y' * 48)
os.environ.setdefault('RURALHEALTH_HASH_PEPPER', 'z' * 48)
os.environ.setdefault('RURALHEALTH_FIELD_KEY_SEED', 'w' * 48)


@pytest.fixture
def app():
    """Aplicación con base de datos temporal en disco."""
    from app import create_app
    from models import db as _db
    from security import bootstrap_schema

    handle, db_path = tempfile.mkstemp(suffix='.db')
    os.close(handle)
    uri = 'sqlite:///' + db_path.replace(os.sep, '/')

    application = create_app('testing', config_override={
        'SQLALCHEMY_DATABASE_URI': uri,
        'UPLOAD_FOLDER': tempfile.mkdtemp(),
        'WTF_CSRF_ENABLED': False,
    })

    with application.app_context():
        _db.create_all()
        bootstrap_schema(_db, create_tables=True)

    yield application

    with application.app_context():
        _db.session.remove()
        _db.engine.dispose()
    try:
        os.unlink(db_path)
    except OSError:
        pass


@pytest.fixture
def db(app):
    from models import db as _db
    with app.app_context():
        yield _db


class CSRFAwareClient(FlaskClient):
    """Cliente de pruebas que aporta el token CSRF real en cada envío.

    La protección CSRF no se desactiva en pruebas: se cumple igual que en
    producción. Desactivarla dejaría sin cubrir precisamente el control que
    protege cada formulario, y haría que las pruebas pasaran sobre un
    comportamiento distinto del real.
    """

    def open(self, *args, **kwargs):
        method = (kwargs.get('method') or (args[1] if len(args) > 1 else 'GET')).upper()
        if method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            with self.session_transaction() as session:
                token = session.get('_csrf_token')
                if not token:
                    token = 'token-de-prueba-' + 'a' * 16
                    session['_csrf_token'] = token

            data = kwargs.get('data')
            if isinstance(data, dict) and '_csrf_token' not in data:
                data['_csrf_token'] = token

            headers = dict(kwargs.get('headers') or {})
            headers.setdefault('X-CSRFToken', token)
            kwargs['headers'] = headers

        return super().open(*args, **kwargs)


@pytest.fixture
def client(app):
    app.test_client_class = CSRFAwareClient
    return app.test_client()


@pytest.fixture
def raw_client(app):
    """Cliente sin token CSRF, para comprobar que la protección rechaza."""
    return app.test_client()


# --- Constructores de datos --------------------------------------------------

VALID_PASSWORD = 'Rural-Health-2026#Seg'


@pytest.fixture
def make_user(app):
    """Crea un usuario con los campos mínimos coherentes."""
    from models import User, db as _db
    from security import hash_password, pii_hash
    from time_utils import colombia_now

    counter = {'n': 0}

    def _make(role='patient', clinic_id=1, username=None, password=VALID_PASSWORD, **kwargs):
        counter['n'] += 1
        index = counter['n']
        username = username or f'{role}{index}'
        cedula = kwargs.pop('cedula', f'10000{index:05d}')

        with app.app_context():
            user = User(
                clinic_id=clinic_id,
                username=username,
                password=hash_password(password),
                role=role,
                name=kwargs.pop('name', f'Usuario {index}'),
                cedula=cedula,
                cedula_hash=pii_hash(cedula),
                created_at=colombia_now(),
                accepted_terms_at=colombia_now(),
                accepted_privacy_at=colombia_now(),
                accepted_transparency_at=colombia_now(),
                sensitive_data_consent_at=colombia_now(),
                **kwargs,
            )
            _db.session.add(user)
            _db.session.commit()
            _db.session.refresh(user)
            _db.session.expunge(user)
            return user

    return _make


@pytest.fixture
def login(client):
    """Inicia sesión mediante el formulario real, no manipulando la sesión."""
    def _login(username, password=VALID_PASSWORD):
        return client.post(
            '/login',
            data={'username': username, 'password': password},
            follow_redirects=False,
        )
    return _login


@pytest.fixture
def make_stock(app):
    from models import Pharmacy, Stock, db as _db

    def _make(med_name, quantity, clinic_id=1, committed=0, pharmacy_id=None):
        with app.app_context():
            if pharmacy_id is None:
                pharmacy = Pharmacy.query.filter_by(clinic_id=clinic_id).first()
                pharmacy_id = pharmacy.id if pharmacy else None
            stock = Stock(
                clinic_id=clinic_id,
                pharmacy_id=pharmacy_id,
                nombre_med=med_name,
                medicamento=med_name,
                cantidad=quantity,
                cantidad_comprometida=committed,
                unidad='unidad',
            )
            _db.session.add(stock)
            _db.session.commit()
            stock_id = stock.id
        return stock_id

    return _make
