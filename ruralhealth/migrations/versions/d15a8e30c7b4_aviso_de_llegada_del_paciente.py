"""Aviso de llegada del paciente

El botón «Estoy en camino» notificaba al expendedor y al personal, pero no
dejaba rastro. Al paciente no le quedaba ninguna señal de haber avisado (el
mensaje de confirmación desaparece con la siguiente página), así que volvía a
pulsarlo, y cada pulsación notificaba otra vez a todo el mundo. Seis
pulsaciones, doce notificaciones para un solo paciente, y la bandeja de la
farmacia inservible.

`arrival_notified_at` es la marca: permite enseñarle al paciente a qué hora
avisó, e impide que el siguiente clic vuelva a notificar dentro de la media
hora siguiente.

Revision ID: d15a8e30c7b4
Revises: b93f27ac1d05
Create Date: 2026-09-08 11:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd15a8e30c7b4'
down_revision = 'b93f27ac1d05'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('medication_pickup_ticket', schema=None) as batch_op:
        batch_op.add_column(sa.Column('arrival_notified_at', sa.DateTime(),
                                      nullable=True))


def downgrade():
    with op.batch_alter_table('medication_pickup_ticket', schema=None) as batch_op:
        batch_op.drop_column('arrival_notified_at')
