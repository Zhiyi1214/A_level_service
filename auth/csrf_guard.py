"""通过要求非标头缓解 CSRF：跨站简单表单无法设置 X-Requested-With。

前端须经 static/script.js 的 apiFetch / withAjaxHeaders 发 /api 与 /auth/logout 的非常规方法请求，
勿裸用 fetch 以免遗漏 X-Requested-With 导致 403。
"""

from __future__ import annotations

from flask import jsonify, request


HEADER_NAME = 'X-Requested-With'
HEADER_VALUE = 'XMLHttpRequest'
_SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS', 'TRACE'})


def init_csrf_header_guard(app):
    @app.before_request
    def _require_trusted_ajax_header():
        if request.method in _SAFE_METHODS:
            return None
        path = request.path
        if not (path.startswith('/api/') or path == '/auth/logout'):
            return None
        got = (request.headers.get(HEADER_NAME) or '').strip()
        if got != HEADER_VALUE:
            return jsonify({
                'error': 'forbidden',
                'detail': '此请求须由本站页面发起（缺少或无效的 X-Requested-With）。',
            }), 403
        return None
