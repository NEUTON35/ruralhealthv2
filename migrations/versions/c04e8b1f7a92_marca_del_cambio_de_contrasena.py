"""Marca del cambio de contraseña

`password_changed_at` es la línea de corte: toda sesión abierta y todo JWT
emitido antes de ese momento dejan de valer.

Sin ella, restablecer la contraseña no expulsaba a nadie. El comentario del
código afirmaba que «las sesiones y tokens anteriores dejan de valer» y lo
único que se ejecutaba debajo era borrar el contador de intentos fallidos. Le
roban la cookie a una médica en el equipo compartido del puesto de salud, la
administradora emite un código, la médica fija una clave nueva, y la sesión del
atacante sigue abriendo la historia clínica de todos sus pacientes. Con el token
de refresco era peor: se renovaba solo, siete días cada vez, indefinidamente.

Las cuentas existentes quedan en NULL, que se interpreta como «nunca se
cambió»: sus sesiones actuales siguen valiendo hasta el primer cambio de
contraseña. Invalidarlas todas de golpe echaría del sistema a un puesto de
salud entero sin avisar.

Revision ID: c04e8b1f7a92
Revises: b93f27ac1d05
Create Date: 2026-09-08 12:05:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c04e8b1f7a92'
down_revision = 'b93f27ac1d05'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('password_changed_at', sa.DateTime(),
                                      nullable=True))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('password_changed_at')
