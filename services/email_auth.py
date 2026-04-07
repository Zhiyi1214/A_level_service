"""邮箱验证码登录：发信与验证码摘要。"""

from __future__ import annotations

import hmac
import hashlib
import logging
import re
import secrets
import smtplib
from email.message import EmailMessage

from config import settings

log = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def normalize_email(raw: str) -> str:
    return (raw or '').strip().lower()


def is_valid_email_shape(email: str) -> bool:
    if not email or len(email) > 320:
        return False
    return bool(_EMAIL_RE.match(email))


def generate_six_digit_code() -> str:
    return f'{secrets.randbelow(900000) + 100000:06d}'


def hash_login_code(email: str, code: str) -> str:
    msg = f'{email}:{code}'.encode()
    return hmac.new(
        settings.SECRET_KEY.encode(),
        msg,
        hashlib.sha256,
    ).hexdigest()


def send_login_code_email(to_addr: str, code: str) -> None:
    if not settings.EMAIL_AUTH_CONFIGURED:
        raise RuntimeError('email auth is not configured')
    ttl = max(1, settings.EMAIL_LOGIN_CODE_TTL_MINUTES)
    msg = EmailMessage()
    msg['Subject'] = '您的登录验证码'
    msg['From'] = settings.SMTP_FROM
    msg['To'] = to_addr
    msg.set_content(
        f'您的验证码是：{code}\n\n'
        f'{ttl} 分钟内有效。如非本人操作请忽略本邮件。\n'
    )
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as smtp:
        if settings.SMTP_USE_TLS:
            smtp.starttls()
        if settings.SMTP_USER:
            smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        smtp.send_message(msg)
    log.info('email login code sent: to_domain=%s', to_addr.split('@')[-1] if '@' in to_addr else '?')


def spawn_send_login_code_email(to_addr: str, code: str) -> None:
    """在 gevent 协程中发信，避免阻塞请求线程；失败仅记日志（验证码已写入库）。"""
    import gevent

    def _task() -> None:
        try:
            send_login_code_email(to_addr, code)
        except Exception:
            log.exception(
                'Background send_login_code_email failed to_domain=%s',
                to_addr.split('@')[-1] if '@' in to_addr else '?',
            )

    gevent.spawn(_task)
