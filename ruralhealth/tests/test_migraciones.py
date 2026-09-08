"""Las migraciones tienen que poder aplicarse sobre PostgreSQL, no solo SQLite.

El defecto que estas pruebas previenen tardó en aparecer porque las pruebas
corren sobre SQLite, y SQLite **no valida la tabla referenciada** al crear una
clave foránea. PostgreSQL sí.

La migración base creaba las tablas en orden alfabético.
`patient_doctor_subscription` referencia a `user` y se creaba 134 líneas antes.
Sobre SQLite pasaba sin queja; sobre una base PostgreSQL limpia el esquema no
se podía crear: `relation "user" does not exist`.

Consecuencia práctica: el esquema **nunca se había aplicado a un PostgreSQL
limpio**. La base anterior tuvo que crearse con `db.create_all()`, que sí
ordena por dependencia, y por eso el fallo no se vio hasta montar una
instancia nueva.

Reordenar no lo arregla: `patient_doctor_subscription` y
`payment_verification_ticket` se referencian mutuamente, y un ciclo no tiene
orden topológico. La solución es la estándar para un ciclo: crear las tablas
sin esas restricciones y añadirlas al final con `op.create_foreign_key`.

Esta comprobación es estática, no ejecuta las migraciones: hacerlo exigiría un
PostgreSQL en el entorno de pruebas. Mira el código y verifica la propiedad que
importa, que es que ninguna clave foránea apunte a una tabla creada después.
"""

import glob
import os
import re

import pytest

MIGRACIONES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'migrations', 'versions')

CREATE_TABLE = re.compile(r"^    op\.create_table\('([a-z_]+)',$", re.M)
FK_EN_TABLA = re.compile(
    r"sa\.ForeignKeyConstraint\(\[[^\]]*\],\s*\['\"?([a-z_]+)\"?\.")


def _bloques(fuente):
    """Cada `op.create_table` con su cuerpo, en orden de aparición."""
    marcas = [(m.group(1), m.start()) for m in CREATE_TABLE.finditer(fuente)]
    for i, (nombre, inicio) in enumerate(marcas):
        fin = marcas[i + 1][1] if i + 1 < len(marcas) else len(fuente)
        yield nombre, fuente[inicio:fin]


@pytest.mark.parametrize('ruta', sorted(glob.glob(
    os.path.join(MIGRACIONES, '*.py'))))
def test_ninguna_clave_foranea_apunta_hacia_adelante(ruta):
    """Una FK a una tabla creada después revienta en PostgreSQL."""
    with open(ruta, encoding='utf-8') as f:
        fuente = f.read()

    bloques = list(_bloques(fuente))
    if not bloques:
        pytest.skip('no crea tablas')

    posicion = {nombre: i for i, (nombre, _) in enumerate(bloques)}
    adelantadas = []
    for nombre, cuerpo in bloques:
        for m in FK_EN_TABLA.finditer(cuerpo):
            destino = m.group(1)
            # Una autorreferencia no obliga a nada: la tabla ya se está creando.
            if destino == nombre:
                continue
            if destino in posicion and posicion[destino] > posicion[nombre]:
                adelantadas.append('%s -> %s' % (nombre, destino))

    assert not adelantadas, (
        'en %s hay claves foráneas que apuntan a una tabla creada después: %s. '
        'Sobre SQLite pasa; sobre PostgreSQL el esquema no se puede crear. '
        'Muévelas al final con op.create_foreign_key.'
        % (os.path.basename(ruta), ', '.join(adelantadas)))


def test_las_claves_diferidas_se_sueltan_en_el_downgrade():
    """Con el ciclo, ninguna de las dos tablas se puede tirar sin soltarlas."""
    ruta = os.path.join(
        MIGRACIONES, '2f0500654aaf_esquema_base_con_seguridad_clinica_.py')
    with open(ruta, encoding='utf-8') as f:
        fuente = f.read()

    creadas = set(re.findall(r"op\.create_foreign_key\('([a-z_0-9]+)'", fuente))
    soltadas = set(re.findall(r"\('([a-z_0-9]+)',\s*'[a-z_]+'\)", fuente))

    assert creadas, 'la migración base ya no difiere ninguna clave foránea'
    faltan = creadas - soltadas
    assert not faltan, (
        'estas claves foráneas se crean pero el downgrade no las suelta: %s'
        % ', '.join(sorted(faltan)))


def test_la_cadena_de_revisiones_es_lineal_y_sin_huecos():
    """Dos migraciones con el mismo padre dejan el despliegue en un estado
    que Alembic no sabe resolver solo."""
    revisiones, padres = {}, {}
    for ruta in glob.glob(os.path.join(MIGRACIONES, '*.py')):
        with open(ruta, encoding='utf-8') as f:
            fuente = f.read()
        rev = re.search(r"^revision = '([^']+)'", fuente, re.M)
        down = re.search(r"^down_revision = (?:'([^']+)'|None)", fuente, re.M)
        if not rev:
            continue
        revisiones[rev.group(1)] = os.path.basename(ruta)
        padres[rev.group(1)] = down.group(1) if down and down.group(1) else None

    raices = [r for r, p in padres.items() if p is None]
    assert len(raices) == 1, 'hay %d migraciones sin padre: %s' % (
        len(raices), ', '.join(revisiones[r] for r in raices))

    hijos = {}
    for rev, padre in padres.items():
        if padre:
            hijos.setdefault(padre, []).append(rev)
    bifurcaciones = {p: h for p, h in hijos.items() if len(h) > 1}
    assert not bifurcaciones, 'la cadena se bifurca en: %s' % bifurcaciones

    huerfanas = [revisiones[r] for r, p in padres.items()
                 if p and p not in revisiones]
    assert not huerfanas, 'apuntan a un padre que no existe: %s' % huerfanas
