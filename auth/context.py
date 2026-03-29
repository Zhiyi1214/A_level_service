from __future__ import annotations

from flask import jsonify, request, session

from config import settings

SESSION_USER_KEY = 'user_id'


def effective_user_id() -> str | None:
    """当前请求对应的业务 user_id。

    启用 OAuth 时仅从 session 读取；未登录返回 None。
    未启用 OAuth 时从 JSON / form / query 读取，缺省为 default_user。
    """
    if settings.OAUTH_CONFIGURED:
        uid = session.get(SESSION_USER_KEY)
        return uid if uid else None

    data = request.get_json(silent=True) or {}
    uid = (data.get('user_id') or request.form.get('user_id') or '').strip()
    if not uid:
        uid = (request.args.get('user_id') or '').strip()
    return uid or 'default_user'


def oauth_login_required_response():
    return jsonify({
        'error': 'unauthorized',
        'detail': '请先登录。',
        'login_url': '/auth/google',
    }), 401
