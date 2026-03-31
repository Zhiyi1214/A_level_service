from __future__ import annotations

import base64
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import redis
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import selectinload

from config import settings
from extensions import db
from models import Conversation, Message, User, UserIdentity

log = logging.getLogger(__name__)

_IMG_CACHE_PREFIX = 'a_level:imgcache:'
_IMG_CACHE_TTL_SEC = 7200


def _encode_images_for_redis(images: list) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for img in images:
        if not isinstance(img, dict):
            continue
        d = dict(img)
        c = d.get('content')
        if isinstance(c, bytes):
            d['content'] = base64.b64encode(c).decode('ascii')
            d['__content_b64__'] = True
        out.append(d)
    return out


def _decode_images_from_redis(data: list) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        d = dict(item)
        if d.pop('__content_b64__', False):
            raw = d.get('content', '')
            if isinstance(raw, str):
                d['content'] = base64.b64decode(raw)
        out.append(d)
    return out


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
        self._image_cache_local: dict[str, list] = {}
        self._redis_img: redis.Redis | None = None
        if settings.REDIS_URL:
            self._redis_img = redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
            )

    def _image_cache_key(self, session_id: str) -> str:
        return f'{_IMG_CACHE_PREFIX}{session_id}'

    def create(
        self, session_id: str, user_id: str, source_id: str, source_name: str
    ) -> dict:
        now = datetime.now(timezone.utc)
        row = Conversation(
            id=session_id,
            user_id=user_id,
            source_id=source_id,
            source_name=source_name,
            upstream_conversation_id='',
            created_at=now,
        )
        db.session.add(row)
        db.session.commit()
        created_iso = _isoformat_utc(now)
        return {
            'id': session_id,
            'user_id': user_id,
            'source_id': source_id,
            'source_name': source_name,
            'upstream_conversation_id': '',
            'messages': [],
            'created_at': created_iso,
        }

    def get(self, session_id: str) -> dict | None:
        conv = db.session.scalar(
            select(Conversation)
            .where(Conversation.id == session_id)
            .options(selectinload(Conversation.messages))
        )
        if conv is None:
            return None
        messages = sorted(conv.messages, key=lambda m: m.id)
        return {
            'id': conv.id,
            'user_id': conv.user_id,
            'source_id': conv.source_id,
            'source_name': conv.source_name,
            'upstream_conversation_id': conv.upstream_conversation_id,
            'created_at': _isoformat_utc(conv.created_at),
            'messages': [
                {
                    'role': m.role,
                    'content': m.content,
                    'timestamp': _isoformat_utc(m.timestamp),
                }
                for m in messages
            ],
        }

    def get_summary(self, session_id: str) -> dict | None:
        conv = db.session.get(Conversation, session_id)
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
        }

    def list_by_user(self, user_id: str) -> dict:
        """与逐条 get_summary 语义一致；用批量查询避免 N+1。"""
        convs = db.session.scalars(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.created_at.desc())
        ).all()
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
            }
        return result

    def delete(self, session_id: str) -> bool:
        conv = db.session.get(Conversation, session_id)
        if conv is None:
            return False
        db.session.delete(conv)
        db.session.commit()
        if self._redis_img is not None:
            try:
                self._redis_img.delete(self._image_cache_key(session_id))
            except RedisError:
                log.warning("redis delete image cache failed session_id=%s", session_id)
        else:
            self._image_cache_local.pop(session_id, None)
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
        conv = db.session.get(Conversation, session_id)
        if conv is not None:
            conv.upstream_conversation_id = upstream_conversation_id
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

    def set_image_cache(self, session_id: str, images: list) -> None:
        if self._redis_img is not None:
            payload = json.dumps(
                _encode_images_for_redis(images),
                ensure_ascii=False,
            )
            self._redis_img.set(
                self._image_cache_key(session_id),
                payload,
                ex=_IMG_CACHE_TTL_SEC,
            )
        else:
            self._image_cache_local[session_id] = images

    def get_image_cache(self, session_id: str) -> list:
        if self._redis_img is not None:
            try:
                raw = self._redis_img.get(self._image_cache_key(session_id))
            except RedisError:
                log.warning("redis get image cache failed session_id=%s", session_id)
                return []
            if not raw:
                return []
            try:
                data = json.loads(raw)
                if not isinstance(data, list):
                    return []
                return _decode_images_from_redis(data)
            except (json.JSONDecodeError, TypeError, ValueError):
                log.warning("image cache decode failed session_id=%s", session_id)
                return []
        return self._image_cache_local.get(session_id, [])

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
        uid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        db.session.add(
            User(
                id=uid,
                email=email,
                display_name=display_name,
                avatar_url=avatar_url,
                created_at=now,
            )
        )
        db.session.flush()

        stmt = (
            insert(UserIdentity)
            .values(
                user_id=uid,
                provider=provider,
                provider_subject=provider_subject,
                created_at=now,
            )
            .on_conflict_do_update(
                constraint='uq_user_identity_provider_subject',
                set_={
                    'user_id': UserIdentity.user_id,
                    'created_at': UserIdentity.created_at,
                },
            )
            .returning(UserIdentity.user_id)
        )
        actual_uid = db.session.execute(stmt).scalar_one()

        if actual_uid != uid:
            orphan = db.session.get(User, uid)
            if orphan is not None:
                db.session.delete(orphan)

        user = db.session.get(User, actual_uid)
        if user is not None:
            user.email = email
            user.display_name = display_name
            user.avatar_url = avatar_url
        db.session.commit()
        return actual_uid
