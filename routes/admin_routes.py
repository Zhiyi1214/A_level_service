"""管理台：独立页面与 /api/admin/*（须配置 ADMIN_SECRET 或 ADMIN_EMAILS+主站登录）。"""

from __future__ import annotations

import hashlib
import hmac
import logging
from functools import wraps

from flask import Blueprint, jsonify, make_response, redirect, render_template, request, session, url_for

from auth.admin_auth import (
    SESSION_ADMIN_LOGIN_NEXT,
    clear_admin_session,
    is_admin,
    set_admin_session_from_secret,
)
from auth.context import SESSION_USER_KEY
from config import settings
from extensions import limiter
from services import image_service
from services.dify_conversations import hydrate_dify_titles
from storage import store

log = logging.getLogger(__name__)

admin_bp = Blueprint('admin_console', __name__)


def _digest_utf8(value: str) -> bytes:
    return hashlib.sha256(value.encode('utf-8')).digest()


def _admin_json_required(view):
    @wraps(view)
    def wrapped(*_a, **_kw):
        if not is_admin():
            return jsonify({'error': 'forbidden', 'detail': '需要管理员身份'}), 403
        return view(*_a, **_kw)

    return wrapped


@admin_bp.route('/admin', methods=['GET'])
def admin_page():
    if settings.AUTH_CONFIGURED and not is_admin():
        uid = session.get(SESSION_USER_KEY)
        if not uid:
            session[SESSION_ADMIN_LOGIN_NEXT] = '/admin'
            session.permanent = True
            session.modified = True
            return redirect(url_for('index', admin='1'))
        return redirect(url_for('index'))
    session.pop(SESSION_ADMIN_LOGIN_NEXT, None)
    resp = make_response(
        render_template(
            'admin.html',
            admin_secret_configured=bool(settings.ADMIN_SECRET),
            auth_configured=settings.AUTH_CONFIGURED,
            admin_emails_configured=bool(settings.ADMIN_EMAILS),
        )
    )
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp


@admin_bp.route('/api/admin/status', methods=['GET'])
@limiter.exempt
def admin_status():
    return jsonify(
        {
            'authenticated': is_admin(),
            'secret_login_available': bool(settings.ADMIN_SECRET),
            'whitelist_login_available': bool(
                settings.AUTH_CONFIGURED and settings.ADMIN_EMAILS
            ),
        }
    ), 200


@admin_bp.route('/api/admin/metrics', methods=['GET'])
@limiter.exempt
@_admin_json_required
def admin_metrics():
    snap = store.admin_metrics_snapshot()
    return jsonify(
        {
            'app': {
                'frontend_version': settings.FRONTEND_VERSION,
                'app_env': settings.APP_ENV,
                'flask_env': settings.APP_ENV,
                'auth_configured': settings.AUTH_CONFIGURED,
                'oauth_configured': settings.OAUTH_CONFIGURED,
                'email_auth_configured': settings.EMAIL_AUTH_CONFIGURED,
                'use_redis_session': settings.USE_REDIS_SESSION,
            },
            'database': snap,
        }
    ), 200


@admin_bp.route('/api/admin/login', methods=['POST'])
@limiter.limit('30 per hour')
def admin_login():
    if not settings.ADMIN_SECRET:
        return jsonify({'error': 'forbidden', 'detail': '未启用口令登录'}), 403
    data = request.get_json(silent=True, force=False) or {}
    secret = (data.get('secret') or '').strip()
    if not hmac.compare_digest(
        _digest_utf8(secret), _digest_utf8(settings.ADMIN_SECRET)
    ):
        log.warning('admin login failed from remote')
        return jsonify({'error': 'unauthorized', 'detail': '口令错误'}), 401
    set_admin_session_from_secret()
    log.info('admin session established (secret login)')
    return jsonify({'success': True}), 200


@admin_bp.route('/api/admin/logout', methods=['POST'])
@limiter.limit('60 per hour')
def admin_logout():
    clear_admin_session()
    return jsonify({'success': True}), 200


_ADMIN_USER_SORTS = frozenset(
    {
        'recent_activity',
        'message_volume',
        'conversation_count',
        'signup',
        'email',
    }
)


@admin_bp.route('/api/admin/users', methods=['GET'])
@limiter.exempt
@_admin_json_required
def admin_list_users():
    q = (request.args.get('q') or '').strip()
    raw_sort = (request.args.get('sort') or 'recent_activity').strip()
    sort = raw_sort if raw_sort in _ADMIN_USER_SORTS else 'recent_activity'
    limit = min(100, request.args.get('limit', default=30, type=int) or 30)
    offset = max(0, request.args.get('offset', default=0, type=int) or 0)
    rows, total = store.admin_list_users(q, sort, limit=limit, offset=offset)
    return jsonify({'success': True, 'users': rows, 'total': total, 'sort': sort}), 200


@admin_bp.route('/api/admin/users/<user_id>/conversations', methods=['GET'])
@limiter.exempt
@_admin_json_required
def admin_user_conversations(user_id: str):
    raw = store.list_by_user(user_id)
    try:
        hydrate_dify_titles(raw, user_id)
    except Exception:
        log.exception('admin hydrate_dify_titles failed')
    hydrated: dict = {}
    for cid, summary in raw.items():
        if not isinstance(summary, dict):
            hydrated[cid] = summary
            continue
        s = dict(summary)
        s.pop('upstream_conversation_id', None)
        dname = (s.pop('dify_conversation_name', None) or '').strip()
        s['dify_title'] = dname
        lm = s.get('last_message')
        if isinstance(lm, dict):
            lm = dict(lm)
            lm['content'] = image_service.rewrite_content_image_refs(
                lm.get('content'),
                viewer_user_id=user_id,
            )
            s['last_message'] = lm
        hydrated[cid] = s
    return jsonify({'success': True, 'conversations': hydrated}), 200


@admin_bp.route('/api/admin/conversations/<conversation_id>', methods=['GET'])
@limiter.exempt
@_admin_json_required
def admin_get_conversation(conversation_id: str):
    msg_limit = request.args.get('message_limit', type=int)
    if msg_limit is not None and (msg_limit < 1 or msg_limit > 200):
        return jsonify({'error': 'Invalid message_limit'}), 400
    before_mid = request.args.get('before_message_id', type=int)
    if before_mid is not None and before_mid < 1:
        return jsonify({'error': 'Invalid before_message_id'}), 400

    conv = store.get(
        conversation_id,
        message_limit=msg_limit,
        before_message_id=before_mid,
    )
    if not conv:
        return jsonify({'error': 'Conversation not found'}), 404

    messages = image_service.hydrate_messages_for_client(
        conv['messages'],
        viewer_user_id=conv.get('user_id'),
    )
    return jsonify(
        {
            'success': True,
            'id': conversation_id,
            'created_at': conv['created_at'],
            'messages': messages,
            'source_id': conv.get('source_id', ''),
            'source_name': conv.get('source_name', ''),
            'dify_title': (conv.get('dify_conversation_name') or '').strip(),
            'messages_truncated': bool(conv.get('messages_truncated')),
            'message_count_total': int(conv.get('message_count_total') or len(messages)),
            'has_more_older': bool(conv.get('has_more_older')),
            'user_id': conv.get('user_id', ''),
        }
    ), 200
