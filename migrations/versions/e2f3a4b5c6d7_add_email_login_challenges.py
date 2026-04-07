"""add email_login_challenges for email OTP login

Revision ID: e2f3a4b5c6d7
Revises: d4e5f6a7b8c9
Create Date: 2026-04-05

"""
from alembic import op
import sqlalchemy as sa


revision = 'e2f3a4b5c6d7'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'email_login_challenges',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('email', sa.Text(), nullable=False),
        sa.Column('code_hash', sa.Text(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_email_login_challenges_email'),
        'email_login_challenges',
        ['email'],
        unique=False,
    )


def downgrade():
    op.drop_index(op.f('ix_email_login_challenges_email'), table_name='email_login_challenges')
    op.drop_table('email_login_challenges')
