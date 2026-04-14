import logging
from datetime import datetime, timedelta, timezone

import requests
from flask import Blueprint, jsonify, redirect, request, session, url_for

from auth.admin_auth import (
    SESSION_ADMIN_LOGIN_NEXT,
    clear_admin_session,
    is_admin,
    show_footer_admin_link,
)
from auth.context import SESSION_USER_KEY
from config import settings
from extensions import limiter, oauth
from flask_limiter.util import get_remote_address
from services.email_auth import (
    generate_six_digit_code,
    hash_login_code,
    is_valid_email_shape,
    normalize_email,
    spawn_send_login_code_email,
)
from storage import store

log = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)


def _email_verify_rate_limit_key() -> str:
    """验证码校验按邮箱计数，减轻换 IP 对同一邮箱的暴力猜码（无效邮箱时退回按 IP）。"""
    data = request.get_json(silent=True, force=False) or {}
    email = normalize_email(data.get('email') or '')
    if is_valid_email_shape(email):
        return f'email_verify:{email}'
    return f'email_verify_fallback_ip:{get_remote_address()}'


def _oauth_redirect_uri() -> str:
    """优先使用显式配置，否则按当前请求动态生成回调地址。"""
    if settings.GOOGLE_REDIRECT_URI_EXPLICIT:
        return settings.GOOGLE_REDIRECT_URI_EXPLICIT
    return url_for('auth.google_callback', _external=True)


def _auth_status_redirect(status: str):
    return redirect(f'/?auth={status}')


@auth_bp.route('/api/me', methods=['GET'])
@limiter.exempt
def api_me():
    payload = {
        'oauth_configured': settings.OAUTH_CONFIGURED,
        'email_auth_configured': settings.EMAIL_AUTH_CONFIGURED,
        'auth_configured': settings.AUTH_CONFIGURED,
        'authenticated': False,
        'user': None,
        'show_admin_link': show_footer_admin_link(),
        'is_admin': is_admin(),
        'secret_login_available': bool(settings.ADMIN_SECRET),
    }
    if not settings.AUTH_CONFIGURED:
        return jsonify(payload), 200

    uid = session.get(SESSION_USER_KEY)
    if not uid:
        payload['show_admin_link'] = show_footer_admin_link()
        payload['is_admin'] = is_admin()
        return jsonify(payload), 200

    user = store.get_user(uid)
    if not user:
        session.pop(SESSION_USER_KEY, None)
        payload['show_admin_link'] = show_footer_admin_link()
        payload['is_admin'] = is_admin()
        return jsonify(payload), 200

    payload['authenticated'] = True
    payload['user'] = {
        'id': user['id'],
        'email': user['email'],
        'display_name': user['display_name'],
        'avatar_url': user['avatar_url'],
    }
    payload['show_admin_link'] = show_footer_admin_link()
    payload['is_admin'] = is_admin()
    return jsonify(payload), 200


@auth_bp.route('/auth/google', methods=['GET'])
@limiter.limit('30 per minute')
def google_login():
    if not settings.OAUTH_CONFIGURED:
        return jsonify({'error': 'OAuth is not configured on this server'}), 503
    redirect_uri = _oauth_redirect_uri()
    log.info(
        'Starting Google OAuth redirect: host=%s redirect_uri=%s',
        request.host,
        redirect_uri,
    )
    try:
        return oauth.google.authorize_redirect(redirect_uri)
    except Exception:
        log.exception('google_login failed')
        return _auth_status_redirect('error')


@auth_bp.route('/auth/google/callback', methods=['GET'])
@limiter.exempt
def google_callback():
    if not settings.OAUTH_CONFIGURED:
        return jsonify({'error': 'OAuth is not configured on this server'}), 503
    try:
        token = oauth.google.authorize_access_token()
        userinfo = token.get('userinfo')
        if not userinfo and token.get('access_token'):
            r = requests.get(
                'https://openidconnect.googleapis.com/v1/userinfo',
                headers={'Authorization': f'Bearer {token["access_token"]}'},
                timeout=15,
            )
            r.raise_for_status()
            userinfo = r.json()
        if not userinfo:
            return _auth_status_redirect('error')
        sub = userinfo.get('sub')
        if not sub:
            return _auth_status_redirect('error')
        uid = store.upsert_user_from_provider(
            'google',
            str(sub),
            userinfo.get('email'),
            userinfo.get('name') or userinfo.get('given_name'),
            userinfo.get('picture'),
        )
        next_after_login = session.pop(SESSION_ADMIN_LOGIN_NEXT, None)
        session.clear()
        session.permanent = True
        session[SESSION_USER_KEY] = uid
        session.modified = True
        # 勿记录明文邮箱，便于合规与日志外泄场景下的隐私保护
        log.info('Google OAuth callback success: user_id=%s', uid)
        if next_after_login == '/admin' and is_admin():
            return redirect(next_after_login)
        return _auth_status_redirect('ok')
    except Exception:
        log.exception('google_callback failed')
        return _auth_status_redirect('error')


@auth_bp.route('/auth/logout', methods=['POST'])
def logout():
    session.pop(SESSION_USER_KEY, None)
    session.pop(SESSION_ADMIN_LOGIN_NEXT, None)
    # 主站登出时一并结束「管理台口令登录」session，避免仍持有 SESSION_ADMIN_KEY
    clear_admin_session()
    return jsonify({'success': True}), 200


@auth_bp.route('/api/auth/email/request', methods=['POST'])
@limiter.limit('30 per hour')
def email_login_request():
    if not settings.EMAIL_AUTH_CONFIGURED:
        return jsonify({'error': 'email login is not configured on this server'}), 503
    data = request.get_json(silent=True, force=False) or {}
    email = normalize_email(data.get('email') or '')
    if not is_valid_email_shape(email):
        return jsonify({'error': 'invalid email'}), 400
    code = generate_six_digit_code()
    code_hash = hash_login_code(email, code)
    ttl = max(1, settings.EMAIL_LOGIN_CODE_TTL_MINUTES)
    expires = datetime.now(timezone.utc) + timedelta(minutes=ttl)
    try:
        store.replace_email_login_challenge(email, code_hash, expires)
    except Exception:
        log.exception('email_login_request failed')
        return jsonify({'error': 'send failed'}), 500
    spawn_send_login_code_email(email, code)
    return jsonify({'success': True}), 200


@auth_bp.route('/api/auth/email/verify', methods=['POST'])
@limiter.limit('30 per minute', key_func=get_remote_address)
@limiter.limit('8 per minute', key_func=_email_verify_rate_limit_key)
def email_login_verify():
    if not settings.EMAIL_AUTH_CONFIGURED:
        return jsonify({'error': 'email login is not configured on this server'}), 503
    data = request.get_json(silent=True, force=False) or {}
    email = normalize_email(data.get('email') or '')
    code = (data.get('code') or '').strip()
    if not is_valid_email_shape(email) or len(code) != 6 or not code.isdigit():
        return jsonify({'error': 'invalid request'}), 400
    if not store.verify_and_consume_email_login_code(email, code):
        return jsonify({'error': 'invalid or expired code'}), 401
    local = email.split('@', 1)[0] if '@' in email else email
    uid = store.upsert_user_from_provider(
        'email',
        email,
        email,
        local,
        None,
    )
    next_after_login = session.pop(SESSION_ADMIN_LOGIN_NEXT, None)
    session.clear()
    session.permanent = True
    session[SESSION_USER_KEY] = uid
    session.modified = True
    log.info('Email login verify success: user_id=%s', uid)
    if next_after_login == '/admin' and is_admin():
        return jsonify({'success': True, 'redirect': '/admin'}), 200
    return jsonify({'success': True}), 200
