"""add conversation.dify_file_cache for Dify upload_file_id reuse

Revision ID: b7e2d4a1c9f0
Revises: 8f16fd464bec
Create Date: 2026-03-31

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'b7e2d4a1c9f0'
down_revision = '8f16fd464bec'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'conversations',
        sa.Column(
            'dify_file_cache',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade():
    op.drop_column('conversations', 'dify_file_cache')
