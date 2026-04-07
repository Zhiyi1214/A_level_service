from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import load_only, selectinload

from config import settings
from extensions import db
from models import Conversation, EmailLoginChallenge, Message, User, UserIdentity

log = logging.getLogger(__name__)

_CONVERSATIONS_TABLE = 'conversations'
# 进程内缓存：避免每次请求 inspect；部署后执行 migrate 需重启进程以识别新列
_conversation_column_names: frozenset[str] | None | bool = False  # False = 未探测


def _conversation_cols() -> frozenset[str] | None:
    """返回 conversations 表当前列名；探测失败时返回 None（按「列齐全」走 ORM）。"""
    global _conversation_column_names
    if _conversation_column_names is not False:
        return _conversation_column_names  # type: ignore[return-value]
    try:
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(db.engine)
        names = frozenset(
            c['name'] for c in insp.get_columns(_CONVERSATIONS_TABLE)
        )
        _conversation_column_names = names
        return names
    except Exception:
        log.exception('inspect conversations columns failed')
        _conversation_column_names = None
        return None


def _has_dify_conversation_name_column() -> bool:
    cols = _conversation_cols()
    if cols is None:
        return True
    return 'dify_conversation_name' in cols


def _conversation_load_only_core():
    """不含 dify_conversation_name，供缺列库使用。"""
    return load_only(
        Conversation.id,
        Conversation.user_id,
        Conversation.source_id,
        Conversation.source_name,
        Conversation.upstream_conversation_id,
        Conversation.dify_file_cache,
        Conversation.created_at,
    )


def _load_conversation_row(session_id: str) -> Conversation | None:
    """按是否已有 dify_conversation_name 列选择加载方式，避免 SELECT 引用不存在的列。"""
    if _has_dify_conversation_name_column():
        return db.session.get(Conversation, session_id)
    return db.session.scalar(
        select(Conversation)
        .where(Conversation.id == session_id)
        .options(_conversation_load_only_core())
    )


def _to_utc(dt: datetime | str) -> datetime:
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    s = dt.replace('Z', '+00:00') if dt.endswith('Z') else dt
    parsed = datetime.fromisoformat(s)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _isoformat_utc(value: datetime | str) -> str:
    if isinstance(value, str):
        return value
    dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


class PostgresStore:
    """PostgreSQL-backed conversation store（Flask-SQLAlchemy；须在应用上下文中调用）。"""

    def __init__(self, dsn: str):
        # 保留参数以兼容 storage.__init__；实际连接串来自 app.config['SQLALCHEMY_DATABASE_URI']
        _ = dsn

    def create(
        self, session_id: str, user_id: str, source_id: str, source_name: str
    ) -> dict:
        now = datetime.now(timezone.utc)
        if _has_dify_conversation_name_column():
            row = Conversation(
                id=session_id,
                user_id=user_id,
                source_id=source_id,
                source_name=source_name,
                upstream_conversation_id='',
                created_at=now,
            )
            db.session.add(row)
        else:
            # 库表尚未 alembic 升级含 dify_conversation_name 时，避免 INSERT 引用不存在的列
            db.session.execute(
                insert(Conversation.__table__).values(
                    id=session_id,
                    user_id=user_id,
                    source_id=source_id,
                    source_name=source_name,
                    upstream_conversation_id='',
                    created_at=now,
                )
            )
        db.session.commit()
        created_iso = _isoformat_utc(now)
        return {
            'id': session_id,
            'user_id': user_id,
            'source_id': source_id,
            'source_name': source_name,
            'upstream_conversation_id': '',
            'dify_conversation_name': '',
            'messages': [],
            'created_at': created_iso,
        }

    @staticmethod
    def _message_to_dict(m: Message) -> dict[str, Any]:
        return {
            'id': m.id,
            'role': m.role,
            'content': m.content,
            'timestamp': _isoformat_utc(m.timestamp),
        }

    def get(
        self,
        session_id: str,
        *,
        message_limit: int | None = None,
        before_message_id: int | None = None,
    ) -> dict | None:
        """读取会话。未传 message_limit 时返回全部消息；传入时返回按 id 排序的一段窗口（用于分页）。"""

        if message_limit is None:
            opts = [selectinload(Conversation.messages)]
            if not _has_dify_conversation_name_column():
                opts.append(_conversation_load_only_core())
            conv = db.session.scalar(
                select(Conversation)
                .where(Conversation.id == session_id)
                .options(*opts)
            )
            if conv is None:
                return None
            messages = sorted(conv.messages, key=lambda m: m.id)
            dname = (
                (conv.dify_conversation_name or '')
                if _has_dify_conversation_name_column()
                else ''
            )
            total = len(messages)
            return {
                'id': conv.id,
                'user_id': conv.user_id,
                'source_id': conv.source_id,
                'source_name': conv.source_name,
                'upstream_conversation_id': conv.upstream_conversation_id,
                'dify_conversation_name': dname,
                'created_at': _isoformat_utc(conv.created_at),
                'messages': [self._message_to_dict(m) for m in messages],
                'message_count_total': total,
                'messages_truncated': False,
                'has_more_older': False,
            }

        conv = _load_conversation_row(session_id)
        if conv is None:
            return None
        dname = (
            (conv.dify_conversation_name or '')
            if _has_dify_conversation_name_column()
            else ''
        )
        total = int(
            db.session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == session_id)
            )
            or 0
        )
        q = select(Message).where(Message.conversation_id == session_id)
        if before_message_id is not None:
            q = q.where(Message.id < before_message_id)
        q = q.order_by(Message.id.desc()).limit(message_limit)
        rows = list(db.session.scalars(q).all())
        rows.reverse()
        oldest_id = rows[0].id if rows else None
        has_more_older = False
        if oldest_id is not None:
            n_older = db.session.scalar(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.conversation_id == session_id,
                    Message.id < oldest_id,
                )
            )
            has_more_older = int(n_older or 0) > 0
        return {
            'id': conv.id,
            'user_id': conv.user_id,
            'source_id': conv.source_id,
            'source_name': conv.source_name,
            'upstream_conversation_id': conv.upstream_conversation_id,
            'dify_conversation_name': dname,
            'created_at': _isoformat_utc(conv.created_at),
            'messages': [self._message_to_dict(m) for m in rows],
            'message_count_total': total,
            'messages_truncated': total > len(rows),
            'has_more_older': has_more_older,
        }

    def get_summary(self, session_id: str) -> dict | None:
        conv = _load_conversation_row(session_id)
        if conv is None:
            return None
        msg_count = db.session.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == session_id)
        )
        last_msg = db.session.scalar(
            select(Message)
            .where(Message.conversation_id == session_id)
            .order_by(Message.id.desc())
            .limit(1)
        )
        return {
            'id': conv.id,
            'created_at': _isoformat_utc(conv.created_at),
            'message_count': int(msg_count or 0),
            'last_message': (
                {
                    'role': last_msg.role,
                    'content': last_msg.content,
                    'timestamp': _isoformat_utc(last_msg.timestamp),
                }
                if last_msg
                else None
            ),
            'source_id': conv.source_id,
            'source_name': conv.source_name,
            'upstream_conversation_id': conv.upstream_conversation_id,
            'dify_conversation_name': (
                (conv.dify_conversation_name or '')
                if _has_dify_conversation_name_column()
                else ''
            ),
        }

    def list_by_user(self, user_id: str) -> dict:
        """与逐条 get_summary 语义一致；用批量查询避免 N+1。"""
        q = select(Conversation).where(Conversation.user_id == user_id).order_by(
            Conversation.created_at.desc()
        )
        if not _has_dify_conversation_name_column():
            q = q.options(_conversation_load_only_core())
        convs = db.session.scalars(q).all()
        if not convs:
            return {}
        conv_ids = [c.id for c in convs]
        count_rows = db.session.execute(
            select(Message.conversation_id, func.count(Message.id))
            .where(Message.conversation_id.in_(conv_ids))
            .group_by(Message.conversation_id)
        ).all()
        count_map = {row[0]: int(row[1]) for row in count_rows}
        max_mid = (
            select(
                Message.conversation_id.label('cid'),
                func.max(Message.id).label('mid'),
            )
            .where(Message.conversation_id.in_(conv_ids))
            .group_by(Message.conversation_id)
        ).subquery()
        last_msgs = {
            m.conversation_id: m
            for m in db.session.scalars(
                select(Message).join(
                    max_mid,
                    (Message.conversation_id == max_mid.c.cid)
                    & (Message.id == max_mid.c.mid),
                )
            ).all()
        }
        result: dict[str, dict] = {}
        for conv in convs:
            cid = conv.id
            last_msg = last_msgs.get(cid)
            result[cid] = {
                'id': cid,
                'created_at': _isoformat_utc(conv.created_at),
                'message_count': count_map.get(cid, 0),
                'last_message': (
                    {
                        'role': last_msg.role,
                        'content': last_msg.content,
                        'timestamp': _isoformat_utc(last_msg.timestamp),
                    }
                    if last_msg
                    else None
                ),
                'source_id': conv.source_id,
                'source_name': conv.source_name,
                'upstream_conversation_id': conv.upstream_conversation_id,
                'dify_conversation_name': (
                    (conv.dify_conversation_name or '')
                    if _has_dify_conversation_name_column()
                    else ''
                ),
            }
        return result

    def delete(self, session_id: str) -> bool:
        conv = _load_conversation_row(session_id)
        if conv is None:
            return False
        db.session.delete(conv)
        db.session.commit()
        return True

    def append_message(
        self, session_id: str, role: str, content: Any, timestamp: datetime | str
    ) -> None:
        ts = _to_utc(timestamp)
        db.session.add(
            Message(
                conversation_id=session_id,
                role=role,
                content=content,
                timestamp=ts,
            )
        )
        db.session.commit()

    def update_upstream_id(
        self, session_id: str, upstream_conversation_id: str
    ) -> None:
        conv = _load_conversation_row(session_id)
        if conv is not None:
            conv.upstream_conversation_id = upstream_conversation_id
            db.session.commit()

    def update_dify_conversation_name(self, session_id: str, name: str) -> None:
        if not _has_dify_conversation_name_column():
            return
        conv = _load_conversation_row(session_id)
        if conv is not None:
            conv.dify_conversation_name = name or ''
            db.session.commit()

    def get_dify_file_cache(self, session_id: str) -> dict[str, str]:
        conv = _load_conversation_row(session_id)
        if conv is None:
            return {}
        raw = conv.dify_file_cache
        if not isinstance(raw, dict):
            return {}
        out: dict[str, str] = {}
        for k, v in raw.items():
            if isinstance(k, str) and isinstance(v, str) and k and v:
                out[k] = v
        return out

    def put_dify_file_cache_entry(
        self, session_id: str, content_sha256: str, upload_file_id: str
    ) -> None:
        h = (content_sha256 or '').strip()
        fid = (upload_file_id or '').strip()
        if not h or not fid:
            return
        conv = _load_conversation_row(session_id)
        if conv is None:
            return
        cache: dict[str, str] = {}
        prev = conv.dify_file_cache
        if isinstance(prev, dict):
            for k, v in prev.items():
                if isinstance(k, str) and isinstance(v, str) and k and v:
                    cache[k] = v
        if h in cache:
            del cache[h]
        cache[h] = fid
        limit = max(1, settings.MAX_DIFY_FILE_CACHE_ENTRIES)
        while len(cache) > limit:
            cache.pop(next(iter(cache)))
        conv.dify_file_cache = cache
        db.session.commit()

    def count_by_user(self, user_id: str) -> int:
        n = db.session.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.user_id == user_id)
        )
        return int(n or 0)

    def count_all(self) -> int:
        n = db.session.scalar(select(func.count()).select_from(Conversation))
        return int(n or 0)

    def delete_oldest_by_user(self, user_id: str) -> bool:
        oldest_id = db.session.scalar(
            select(Conversation.id)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.created_at.asc())
            .limit(1)
        )
        if not oldest_id:
            return False
        return self.delete(oldest_id)

    def get_user(self, user_id: str) -> dict | None:
        u = db.session.get(User, user_id)
        if u is None:
            return None
        return {
            'id': u.id,
            'email': u.email or '',
            'display_name': u.display_name or '',
            'avatar_url': u.avatar_url or '',
            'created_at': _isoformat_utc(u.created_at),
        }

    def upsert_user_from_provider(
        self,
        provider: str,
        provider_subject: str,
        email: str | None,
        display_name: str | None,
        avatar_url: str | None,
    ) -> str:
        now = datetime.now(timezone.utc)
        for _attempt in range(2):
            identity = db.session.scalar(
                select(UserIdentity).where(
                    UserIdentity.provider == provider,
                    UserIdentity.provider_subject == provider_subject,
                )
            )
            if identity is not None:
                uid = identity.user_id
                user = db.session.get(User, uid)
                if user is not None:
                    user.email = email
                    user.display_name = display_name
                    user.avatar_url = avatar_url
                db.session.commit()
                return uid

            uid = str(uuid.uuid4())
            db.session.add(
                User(
                    id=uid,
                    email=email,
                    display_name=display_name,
                    avatar_url=avatar_url,
                    created_at=now,
                )
            )
            db.session.add(
                UserIdentity(
                    user_id=uid,
                    provider=provider,
                    provider_subject=provider_subject,
                    created_at=now,
                )
            )
            try:
                db.session.commit()
                return uid
            except IntegrityError:
                db.session.rollback()
        raise RuntimeError(
            'upsert_user_from_provider: concurrent identity insert retry exhausted'
        )

    def replace_email_login_challenge(
        self,
        email: str,
        code_hash: str,
        expires_at: datetime,
    ) -> None:
        now = datetime.now(timezone.utc)
        db.session.execute(
            delete(EmailLoginChallenge).where(EmailLoginChallenge.email == email)
        )
        db.session.add(
            EmailLoginChallenge(
                email=email,
                code_hash=code_hash,
                expires_at=expires_at,
                created_at=now,
            )
        )
        db.session.commit()

    def verify_and_consume_email_login_code(self, email: str, code: str) -> bool:
        import hmac

        from services.email_auth import hash_login_code

        now = datetime.now(timezone.utc)
        row = db.session.scalar(
            select(EmailLoginChallenge)
            .where(EmailLoginChallenge.email == email)
            .order_by(EmailLoginChallenge.created_at.desc())
            .limit(1)
        )
        if row is None or row.expires_at < now:
            return False
        expect = hash_login_code(email, (code or '').strip())
        if not hmac.compare_digest(row.code_hash, expect):
            return False
        db.session.delete(row)
        db.session.commit()
        return True
