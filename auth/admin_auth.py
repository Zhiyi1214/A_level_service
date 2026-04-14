"""管理台身份：口令 session 或（主站已登录且）用户邮箱在 ADMIN_EMAILS 白名单。"""

from __future__ import annotations

from flask import session

from auth.context import SESSION_USER_KEY
from config import settings
from storage import store

SESSION_ADMIN_KEY = 'admin_console_ok'
# 未登录访问 /admin 时记下，主站 OAuth/邮箱登录成功后跳回 /admin（须与 session.clear() 前 pop 配合）
SESSION_ADMIN_LOGIN_NEXT = 'admin_login_next'


def show_footer_admin_link() -> bool:
    """页脚 Admin：未开主站登录时人人可见；开启后仅管理员可见。"""
    if not settings.admin_console_enabled():
        return False
    if not settings.AUTH_CONFIGURED:
        return True
    return is_admin()


def is_admin() -> bool:
    if session.get(SESSION_ADMIN_KEY):
        return True
    if settings.AUTH_CONFIGURED and settings.ADMIN_EMAILS:
        uid = session.get(SESSION_USER_KEY)
        if not uid:
            return False
        user = store.get_user(uid)
        if not user:
            return False
        email = (user.get('email') or '').strip().lower()
        if email and email in settings.ADMIN_EMAILS:
            return True
    return False


def set_admin_session_from_secret() -> None:
    session[SESSION_ADMIN_KEY] = True
    session.permanent = True
    session.modified = True


def clear_admin_session() -> None:
    session.pop(SESSION_ADMIN_KEY, None)
    session.modified = True
