"""SQLAlchemy models — schema 由 Flask-Migrate 管理，与既有 PostgreSQL DDL 一致。"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB

from extensions import db


class Conversation(db.Model):
    __tablename__ = 'conversations'

    id = db.Column(db.Text, primary_key=True)
    user_id = db.Column(db.Text, nullable=False, index=True)
    source_id = db.Column(db.Text, nullable=False, server_default=text("''"))
    source_name = db.Column(db.Text, nullable=False, server_default=text("''"))
    upstream_conversation_id = db.Column(
        db.Text, nullable=False, server_default=text("''")
    )
    dify_conversation_name = db.Column(
        db.Text, nullable=False, server_default=text("''")
    )
    dify_file_cache = db.Column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    created_at = db.Column(db.DateTime(timezone=True), nullable=False)

    messages = db.relationship(
        'Message',
        backref='conversation',
        cascade='all, delete-orphan',
        passive_deletes=True,
        order_by='Message.id',
    )


class Message(db.Model):
    __tablename__ = 'messages'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    conversation_id = db.Column(
        db.Text,
        db.ForeignKey('conversations.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    role = db.Column(db.Text, nullable=False)
    content = db.Column(JSONB, nullable=False)
    timestamp = db.Column(db.DateTime(timezone=True), nullable=False)


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Text, primary_key=True)
    email = db.Column(db.Text, nullable=True)
    display_name = db.Column(db.Text, nullable=True)
    avatar_url = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False)

    identities = db.relationship(
        'UserIdentity',
        backref='user',
        cascade='all, delete-orphan',
        passive_deletes=True,
    )


class UserIdentity(db.Model):
    __tablename__ = 'user_identities'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(
        db.Text,
        db.ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    provider = db.Column(db.Text, nullable=False)
    provider_subject = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False)

    __table_args__ = (
        db.UniqueConstraint('provider', 'provider_subject', name='uq_user_identity_provider_subject'),
    )
