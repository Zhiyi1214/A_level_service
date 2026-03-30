from __future__ import annotations

import threading
import uuid
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from storage.serialize import json_dumps_content, json_loads_content


_DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS conversations (
        id                       TEXT PRIMARY KEY,
        user_id                  TEXT NOT NULL,
        source_id                TEXT NOT NULL DEFAULT '',
        source_name              TEXT NOT NULL DEFAULT '',
        upstream_conversation_id TEXT NOT NULL DEFAULT '',
        created_at               TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        id              SERIAL PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role            TEXT NOT NULL,
        content         TEXT NOT NULL,
        timestamp       TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id)",
    """
    CREATE TABLE IF NOT EXISTS users (
        id           TEXT PRIMARY KEY,
        email        TEXT,
        display_name TEXT,
        avatar_url   TEXT,
        created_at   TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_identities (
        id               SERIAL PRIMARY KEY,
        user_id          TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        provider         TEXT NOT NULL,
        provider_subject TEXT NOT NULL,
        created_at       TEXT NOT NULL,
        UNIQUE (provider, provider_subject)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_user_identities_user ON user_identities(user_id)",
]


class PostgresStore:
    """PostgreSQL-backed conversation store (thread-local connections)."""

    def __init__(self, dsn: str):
        self._dsn = dsn
        self._local = threading.local()
        self._image_cache: dict[str, list] = {}
        self._init_db()

    def _conn(self) -> psycopg.Connection:
        conn = getattr(self._local, 'conn', None)
        if conn is None or conn.closed:
            conn = psycopg.connect(self._dsn, row_factory=dict_row)
            self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        conn = self._conn()
        with conn.cursor() as cur:
            for stmt in _DDL_STATEMENTS:
                cur.execute(stmt)
        conn.commit()

    def create(
        self, session_id: str, user_id: str, source_id: str, source_name: str
    ) -> dict:
        now = datetime.now().isoformat()
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO conversations"
                " (id, user_id, source_id, source_name, created_at)"
                " VALUES (%s, %s, %s, %s, %s)",
                (session_id, user_id, source_id, source_name, now),
            )
        conn.commit()
        return {
            'id': session_id,
            'user_id': user_id,
            'source_id': source_id,
            'source_name': source_name,
            'upstream_conversation_id': '',
            'messages': [],
            'created_at': now,
        }

    def get(self, session_id: str) -> dict | None:
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM conversations WHERE id = %s", (session_id,))
            row = cur.fetchone()
            if not row:
                return None
            cur.execute(
                "SELECT role, content, timestamp FROM messages"
                " WHERE conversation_id = %s ORDER BY id",
                (session_id,),
            )
            messages = cur.fetchall()
        return {
            'id': row['id'],
            'user_id': row['user_id'],
            'source_id': row['source_id'],
            'source_name': row['source_name'],
            'upstream_conversation_id': row['upstream_conversation_id'],
            'created_at': row['created_at'],
            'messages': [
                {
                    'role': m['role'],
                    'content': json_loads_content(m['content']),
                    'timestamp': m['timestamp'],
                }
                for m in messages
            ],
        }

    def get_summary(self, session_id: str) -> dict | None:
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM conversations WHERE id = %s", (session_id,))
            row = cur.fetchone()
            if not row:
                return None
            cur.execute(
                "SELECT role, content, timestamp FROM messages"
                " WHERE conversation_id = %s ORDER BY id DESC LIMIT 1",
                (session_id,),
            )
            last_msg = cur.fetchone()
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM messages WHERE conversation_id = %s",
                (session_id,),
            )
            msg_count = cur.fetchone()['cnt']
        return {
            'id': row['id'],
            'created_at': row['created_at'],
            'message_count': msg_count,
            'last_message': (
                {
                    'role': last_msg['role'],
                    'content': json_loads_content(last_msg['content']),
                    'timestamp': last_msg['timestamp'],
                }
                if last_msg
                else None
            ),
            'source_id': row['source_id'],
            'source_name': row['source_name'],
        }

    def list_by_user(self, user_id: str) -> dict:
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM conversations WHERE user_id = %s ORDER BY created_at DESC",
                (user_id,),
            )
            rows = cur.fetchall()
        result: dict[str, dict] = {}
        for row in rows:
            summary = self.get_summary(row['id'])
            if summary:
                result[row['id']] = summary
        return result

    def delete(self, session_id: str) -> bool:
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM conversations WHERE id = %s", (session_id,))
            deleted = cur.rowcount > 0
        conn.commit()
        self._image_cache.pop(session_id, None)
        return deleted

    def append_message(
        self, session_id: str, role: str, content: Any, timestamp: str
    ) -> None:
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO messages (conversation_id, role, content, timestamp)"
                " VALUES (%s, %s, %s, %s)",
                (session_id, role, json_dumps_content(content), timestamp),
            )
        conn.commit()

    def update_upstream_id(
        self, session_id: str, upstream_conversation_id: str
    ) -> None:
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE conversations SET upstream_conversation_id = %s WHERE id = %s",
                (upstream_conversation_id, session_id),
            )
        conn.commit()

    def count_by_user(self, user_id: str) -> int:
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM conversations WHERE user_id = %s",
                (user_id,),
            )
            return cur.fetchone()['cnt']

    def count_all(self) -> int:
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM conversations")
            return cur.fetchone()['cnt']

    def delete_oldest_by_user(self, user_id: str) -> bool:
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM conversations WHERE user_id = %s"
                " ORDER BY created_at ASC LIMIT 1",
                (user_id,),
            )
            oldest = cur.fetchone()
        if not oldest:
            return False
        return self.delete(oldest['id'])

    def set_image_cache(self, session_id: str, images: list) -> None:
        self._image_cache[session_id] = images

    def get_image_cache(self, session_id: str) -> list:
        return self._image_cache.get(session_id, [])

    def get_user(self, user_id: str) -> dict | None:
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, email, display_name, avatar_url, created_at"
                " FROM users WHERE id = %s",
                (user_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            'id': row['id'],
            'email': row['email'] or '',
            'display_name': row['display_name'] or '',
            'avatar_url': row['avatar_url'] or '',
            'created_at': row['created_at'],
        }

    def upsert_user_from_provider(
        self,
        provider: str,
        provider_subject: str,
        email: str | None,
        display_name: str | None,
        avatar_url: str | None,
    ) -> str:
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id FROM user_identities"
                " WHERE provider = %s AND provider_subject = %s",
                (provider, provider_subject),
            )
            row = cur.fetchone()
            now = datetime.now().isoformat()
            if row:
                uid = row['user_id']
                cur.execute(
                    "UPDATE users SET email = %s, display_name = %s, avatar_url = %s"
                    " WHERE id = %s",
                    (email, display_name, avatar_url, uid),
                )
                conn.commit()
                return uid

            uid = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO users (id, email, display_name, avatar_url, created_at)"
                " VALUES (%s, %s, %s, %s, %s)",
                (uid, email, display_name, avatar_url, now),
            )
            cur.execute(
                "INSERT INTO user_identities"
                " (user_id, provider, provider_subject, created_at)"
                " VALUES (%s, %s, %s, %s)",
                (uid, provider, provider_subject, now),
            )
        conn.commit()
        return uid
