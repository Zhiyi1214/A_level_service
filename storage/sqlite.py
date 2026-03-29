from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


class SQLiteStore:
    """SQLite-backed conversation store.

    Each thread gets its own connection (via threading.local) so the store
    is safe to use with multi-threaded WSGI servers.
    """

    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._local = threading.local()
        self._image_cache: dict[str, list] = {}
        self._init_db()

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, 'conn', None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def _init_db(self):
        conn = self._conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id                       TEXT PRIMARY KEY,
                user_id                  TEXT NOT NULL,
                source_id                TEXT NOT NULL DEFAULT '',
                source_name              TEXT NOT NULL DEFAULT '',
                upstream_conversation_id TEXT NOT NULL DEFAULT '',
                created_at               TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT    NOT NULL,
                role            TEXT    NOT NULL,
                content         TEXT    NOT NULL,
                timestamp       TEXT    NOT NULL,
                FOREIGN KEY (conversation_id)
                    REFERENCES conversations(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_conv_user
                ON conversations(user_id);
            CREATE INDEX IF NOT EXISTS idx_msg_conv
                ON messages(conversation_id);
        """)
        conn.commit()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self, session_id: str, user_id: str, source_id: str, source_name: str
    ) -> dict:
        now = datetime.now().isoformat()
        self._conn().execute(
            "INSERT INTO conversations"
            " (id, user_id, source_id, source_name, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (session_id, user_id, source_id, source_name, now),
        )
        self._conn().commit()
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
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            return None
        messages = conn.execute(
            "SELECT role, content, timestamp FROM messages"
            " WHERE conversation_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
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
                    'content': _json_loads(m['content']),
                    'timestamp': m['timestamp'],
                }
                for m in messages
            ],
        }

    def get_summary(self, session_id: str) -> dict | None:
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            return None
        last_msg = conn.execute(
            "SELECT role, content, timestamp FROM messages"
            " WHERE conversation_id = ? ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        msg_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM messages WHERE conversation_id = ?",
            (session_id,),
        ).fetchone()['cnt']
        return {
            'id': row['id'],
            'created_at': row['created_at'],
            'message_count': msg_count,
            'last_message': (
                {
                    'role': last_msg['role'],
                    'content': _json_loads(last_msg['content']),
                    'timestamp': last_msg['timestamp'],
                }
                if last_msg
                else None
            ),
            'source_id': row['source_id'],
            'source_name': row['source_name'],
        }

    def list_by_user(self, user_id: str) -> dict:
        rows = self._conn().execute(
            "SELECT id FROM conversations WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        result: dict[str, dict] = {}
        for row in rows:
            summary = self.get_summary(row['id'])
            if summary:
                result[row['id']] = summary
        return result

    def delete(self, session_id: str) -> bool:
        cursor = self._conn().execute(
            "DELETE FROM conversations WHERE id = ?", (session_id,)
        )
        self._conn().commit()
        self._image_cache.pop(session_id, None)
        return cursor.rowcount > 0

    def append_message(
        self, session_id: str, role: str, content: Any, timestamp: str
    ) -> None:
        self._conn().execute(
            "INSERT INTO messages (conversation_id, role, content, timestamp)"
            " VALUES (?, ?, ?, ?)",
            (session_id, role, _json_dumps(content), timestamp),
        )
        self._conn().commit()

    def update_upstream_id(
        self, session_id: str, upstream_conversation_id: str
    ) -> None:
        self._conn().execute(
            "UPDATE conversations SET upstream_conversation_id = ? WHERE id = ?",
            (upstream_conversation_id, session_id),
        )
        self._conn().commit()

    def count_by_user(self, user_id: str) -> int:
        return self._conn().execute(
            "SELECT COUNT(*) AS cnt FROM conversations WHERE user_id = ?",
            (user_id,),
        ).fetchone()['cnt']

    def count_all(self) -> int:
        return self._conn().execute(
            "SELECT COUNT(*) AS cnt FROM conversations"
        ).fetchone()['cnt']

    def delete_oldest_by_user(self, user_id: str) -> bool:
        oldest = self._conn().execute(
            "SELECT id FROM conversations WHERE user_id = ?"
            " ORDER BY created_at ASC LIMIT 1",
            (user_id,),
        ).fetchone()
        if not oldest:
            return False
        return self.delete(oldest['id'])

    # ------------------------------------------------------------------
    # Transient image cache (in-memory, not persisted)
    # ------------------------------------------------------------------

    def set_image_cache(self, session_id: str, images: list) -> None:
        self._image_cache[session_id] = images

    def get_image_cache(self, session_id: str) -> list:
        return self._image_cache.get(session_id, [])


# ------------------------------------------------------------------
# JSON serialisation helpers
# ------------------------------------------------------------------

def _json_dumps(val: Any) -> str:
    if isinstance(val, str):
        return val
    return json.dumps(val, ensure_ascii=False)


def _json_loads(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw
