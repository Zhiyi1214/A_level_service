import logging

import requests
from flask import Blueprint, jsonify, redirect, request, session, url_for

from auth.context import SESSION_USER_KEY
from config import settings
from extensions import limiter, oauth
from storage import store

log = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)


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
        'authenticated': False,
        'user': None,
    }
    if not settings.OAUTH_CONFIGURED:
        return jsonify(payload), 200

    uid = session.get(SESSION_USER_KEY)
    if not uid:
        return jsonify(payload), 200

    user = store.get_user(uid)
    if not user:
        session.pop(SESSION_USER_KEY, None)
        return jsonify(payload), 200

    payload['authenticated'] = True
    payload['user'] = {
        'id': user['id'],
        'email': user['email'],
        'display_name': user['display_name'],
        'avatar_url': user['avatar_url'],
    }
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
        session.clear()
        session.permanent = True
        session[SESSION_USER_KEY] = uid
        session.modified = True
        # 勿记录明文邮箱，便于合规与日志外泄场景下的隐私保护
        log.info('Google OAuth callback success: user_id=%s', uid)
        return _auth_status_redirect('ok')
    except Exception:
        log.exception('google_callback failed')
        return _auth_status_redirect('error')


@auth_bp.route('/auth/logout', methods=['POST'])
def logout():
    session.pop(SESSION_USER_KEY, None)
    return jsonify({'success': True}), 200
