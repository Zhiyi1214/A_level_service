from __future__ import annotations

import uuid

from flask import jsonify, session

from config import settings

SESSION_USER_KEY = 'user_id'
SESSION_ANON_KEY = 'anon_user_id'


def effective_user_id() -> str | None:
    """当前请求对应的业务 user_id。

    启用 OAuth 时仅从 session 读取；未登录返回 None。
    未启用 OAuth 时在服务端 session 中生成并固定匿名 id，不信任客户端传入的 user_id。
    """
    if settings.AUTH_CONFIGURED:
        uid = session.get(SESSION_USER_KEY)
        return uid if uid else None

    uid = session.get(SESSION_ANON_KEY)
    if not uid:
        uid = str(uuid.uuid4())
        session[SESSION_ANON_KEY] = uid
        session.permanent = True
        session.modified = True
    return uid


def oauth_login_required_response():
    return jsonify({
        'error': 'unauthorized',
        'detail': '请先登录。',
        'login_url': '/auth/google' if settings.OAUTH_CONFIGURED else None,
        'email_auth_configured': settings.EMAIL_AUTH_CONFIGURED,
    }), 401
