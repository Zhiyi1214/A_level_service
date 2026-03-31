"""add conversations.dify_conversation_name for Dify sidebar title

Revision ID: d4e5f6a7b8c9
Revises: b7e2d4a1c9f0
Create Date: 2026-03-31

"""
from alembic import op
import sqlalchemy as sa

revision = 'd4e5f6a7b8c9'
down_revision = 'b7e2d4a1c9f0'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'conversations',
        sa.Column(
            'dify_conversation_name',
            sa.Text(),
            server_default=sa.text("''"),
            nullable=False,
        ),
    )


def downgrade():
    op.drop_column('conversations', 'dify_conversation_name')
