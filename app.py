import logging
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, make_response, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from config import settings
from extensions import init_extensions, limiter
from services import image_service
from services.source_service import source_service
from storage import store


def create_app() -> Flask:
    """应用工厂：便于测试与避免导入期循环依赖；Gunicorn 仍使用模块级 `app`。"""
    app = Flask(__name__, template_folder='templates', static_folder='static')
    import models  # noqa: F401 — 注册 ORM 元数据供 Flask-Migrate 使用

    app.secret_key = settings.SECRET_KEY
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=14)
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE'] = settings.SESSION_COOKIE_SECURE
    app.config['MAX_CONTENT_LENGTH'] = settings.MAX_CONTENT_LENGTH

    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=settings.PROXY_FIX_X_FOR,
        x_proto=1,
        x_host=1,
        x_prefix=1,
    )

    logging.basicConfig(level=settings.LOG_LEVEL)

    image_service.ensure_bucket_exists()
    init_extensions(app)

    from routes.auth_routes import auth_bp
    from routes.chat import chat_bp
    from routes.conversations import conversations_bp
    from routes.media import media_bp
    from routes.sources import sources_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(conversations_bp)
    app.register_blueprint(sources_bp)
    app.register_blueprint(media_bp)

    if settings.admin_console_enabled():
        from routes.admin_routes import admin_bp

        app.register_blueprint(admin_bp)

    from auth.csrf_guard import init_csrf_header_guard

    init_csrf_header_guard(app)

    @app.before_request
    def _reload_sources():
        source_service.maybe_reload()

    @app.after_request
    def _security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = (
            'camera=(), microphone=(), geolocation=()'
        )
        return response

    @app.route('/')
    def index():
        html = render_template(
            'index.html',
            frontend_version=settings.FRONTEND_VERSION,
            asset_tag=settings.STATIC_ASSET_TAG,
            admin_console_enabled=settings.admin_console_enabled(),
        )
        resp = make_response(html)
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        return resp

    @app.route('/api/health', methods=['GET'])
    @limiter.exempt
    def health_check():
        """公网探活仅返回最少字段；开发环境可加 ?verbose=1 查看聚合指标。"""
        payload: dict = {
            'status': 'healthy',
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }
        if settings.APP_ENV != 'production' and str(
            request.args.get('verbose') or ''
        ).lower() in ('1', 'true', 'yes', 'on'):
            sources = source_service.public_list()
            configured_count = sum(
                1 for s in sources
                if bool((source_service.get(s['id']) or {}).get('api_key'))
            )
            payload['active_conversations'] = store.count_all()
            payload['active_sources'] = len(sources)
            payload['configured_sources'] = configured_count
        return jsonify(payload), 200

    @app.errorhandler(429)
    def ratelimit_exceeded(error):
        _ = error
        return jsonify({
            'error': 'Too many requests',
            'detail': '请求过于频繁，请稍后再试。',
        }), 429

    @app.errorhandler(413)
    def request_entity_too_large(error):
        max_mb = app.config['MAX_CONTENT_LENGTH'] / (1024 * 1024)
        return jsonify({'error': f'文件过大，最大允许 {max_mb:.0f}MB'}), 413

    @app.errorhandler(415)
    def unsupported_media_type(error):
        return jsonify({'error': 'Unsupported Media Type'}), 415

    @app.errorhandler(404)
    def not_found(error):
        return jsonify({'error': 'Not found'}), 404

    @app.errorhandler(500)
    def internal_error(error):
        return jsonify({'error': 'Internal server error'}), 500

    return app


app = create_app()

if __name__ == '__main__':
    import sys

    print('本地开发请运行: python dev.py', file=sys.stderr)
    print('生产部署请使用: gunicorn -c gunicorn.conf.py wsgi:app', file=sys.stderr)
    sys.exit(1)
