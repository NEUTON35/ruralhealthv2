"""Reservas de existencias con dueño

`Stock.cantidad_comprometida` era un solo número, sin dueño. Un escalar no
puede decir de quién son las unidades, y de ahí salían dos daños distintos,
los dos reproducidos:

1. El paciente no podía recoger lo que tenía apartado. Al emitir el ticket se
   sumaban sus unidades al total comprometido; en el mostrador, la evaluación
   calculaba disponible = cantidad - comprometida, o sea le restaba al paciente
   su propia reserva. Con diez unidades en estante y un ticket por diez,
   disponible daba cero.

2. La reserva de un paciente se la llevaba otro. Al dispensar se restaba del
   total comprometido sin mirar de quién era.

`stock_reservation` guarda el reparto por ticket. El total se conserva porque
hay pantallas que lo leen, pero deja de ser la fuente de verdad.

Las reservas que ya existan quedan sin fila: no hay forma de saber a qué ticket
pertenecían. Se reconstruyen solas (al reactivar o al volver a emitir) y
mientras tanto el efecto es conservador: el ticket ve menos disponible del que
tiene, nunca más.

Revision ID: b93f27ac1d05
Revises: a7c41d92be03
Create Date: 2026-09-08 11:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b93f27ac1d05'
down_revision = 'a7c41d92be03'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'stock_reservation',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('clinic_id', sa.Integer(), nullable=False),
        sa.Column('stock_id', sa.Integer(), nullable=False),
        sa.Column('ticket_id', sa.Integer(), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('released_at', sa.DateTime(), nullable=True),
        sa.Column('release_reason', sa.String(length=120), nullable=True),
        sa.ForeignKeyConstraint(['clinic_id'], ['clinic.id']),
        sa.ForeignKeyConstraint(['stock_id'], ['stock.id']),
        sa.ForeignKeyConstraint(['ticket_id'], ['medication_pickup_ticket.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('stock_reservation', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_stock_reservation_clinic_id'),
                              ['clinic_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_stock_reservation_stock_id'),
                              ['stock_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_stock_reservation_ticket_id'),
                              ['ticket_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_stock_reservation_status'),
                              ['status'], unique=False)


def downgrade():
    with op.batch_alter_table('stock_reservation', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_stock_reservation_status'))
        batch_op.drop_index(batch_op.f('ix_stock_reservation_ticket_id'))
        batch_op.drop_index(batch_op.f('ix_stock_reservation_stock_id'))
        batch_op.drop_index(batch_op.f('ix_stock_reservation_clinic_id'))
    op.drop_table('stock_reservation')
