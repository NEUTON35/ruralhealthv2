"""Punto de reposicion por medicamento y sede, y origen de la alerta

El stock por farmacia no tenia umbral propio. El unico minimo del sistema
estaba en `inventory_item`, que es de la clinica entera, asi que la pantalla
del expendedor avisaba con un cinco fijo igual para cualquier medicamento.

Se anaden dos columnas:

  stock.cantidad_minima            punto de reposicion de ese medicamento en
                                   esa sede. Cero significa "sin definir".
  replenishment_alert.origin       si la alerta nacio porque un paciente se
                                   quedo sin su medicamento ('demanda') o
                                   porque el stock bajo del punto de
                                   reposicion ('punto_reposicion').

Las filas que ya existen quedan con `cantidad_minima = 0`, sin umbral, que es
lo unico honesto: nadie ha dicho todavia cuanto es suficiente en cada sede. Y
las alertas historicas se marcan como 'demanda', que es lo unico que el
sistema sabia generar hasta ahora.

Revision ID: a7c41d92be03
Revises: 2a3878fe5611
Create Date: 2026-09-08 09:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7c41d92be03'
down_revision = '2a3878fe5611'
branch_labels = None
depends_on = None


def upgrade():
    # `server_default` para que las filas existentes no violen el NOT NULL, y
    # se retira despues: el valor por defecto lo pone el modelo.
    with op.batch_alter_table('stock', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cantidad_minima', sa.Integer(),
                                      nullable=False, server_default='0'))
    with op.batch_alter_table('stock', schema=None) as batch_op:
        batch_op.alter_column('cantidad_minima', server_default=None)

    with op.batch_alter_table('replenishment_alert', schema=None) as batch_op:
        batch_op.add_column(sa.Column('origin', sa.String(length=30),
                                      nullable=False, server_default='demanda'))
        batch_op.create_index(batch_op.f('ix_replenishment_alert_origin'),
                              ['origin'], unique=False)
    with op.batch_alter_table('replenishment_alert', schema=None) as batch_op:
        batch_op.alter_column('origin', server_default=None)


def downgrade():
    with op.batch_alter_table('replenishment_alert', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_replenishment_alert_origin'))
        batch_op.drop_column('origin')

    with op.batch_alter_table('stock', schema=None) as batch_op:
        batch_op.drop_column('cantidad_minima')
