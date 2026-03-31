from gevent import monkey

monkey.patch_all()

import psycogreen.gevent

psycogreen.gevent.patch_psycopg()

import logging
from datetime import datetime, timedelta

from flask import Flask, jsonify, make_response, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

from config import settings
from extensions import init_extensions, limiter
from services import image_service
from services.source_service import source_service
from storage import store

# ---------------------------------------------------------------------------
# App creation
# ---------------------------------------------------------------------------
app = Flask(__name__, template_folder='templates', static_folder='static')
import models  # noqa: F401 — 注册 ORM 元数据供 Flask-Migrate 使用
app.secret_key = settings.SECRET_KEY
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=14)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = settings.SESSION_COOKIE_SECURE
app.config['MAX_CONTENT_LENGTH'] = settings.MAX_CONTENT_LENGTH

# 信任一层反向代理（Nginx 经 real_ip 校验后把 X-Forwarded-For 设为单一可信客户端 IP）
app.wsgi_app = ProxyFix(
    app.wsgi_app,
    x_for=1,
    x_proto=1,
    x_host=1,
    x_prefix=1,
)

logging.basicConfig(level=settings.LOG_LEVEL)
log = app.logger

image_service.ensure_bucket_exists()

init_extensions(app)

# ---------------------------------------------------------------------------
# Register blueprints
# ---------------------------------------------------------------------------
from routes.auth_routes import auth_bp    # noqa: E402
from routes.chat import chat_bp           # noqa: E402
from routes.conversations import conversations_bp  # noqa: E402
from routes.sources import sources_bp     # noqa: E402
from routes.media import media_bp          # noqa: E402

app.register_blueprint(auth_bp)
app.register_blueprint(chat_bp)
app.register_blueprint(conversations_bp)
app.register_blueprint(sources_bp)
app.register_blueprint(media_bp)

from auth.csrf_guard import init_csrf_header_guard  # noqa: E402

init_csrf_header_guard(app)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

@app.before_request
def _reload_sources():
    source_service.maybe_reload()


@app.after_request
def _security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    return response

# ---------------------------------------------------------------------------
# Top-level routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    html = render_template(
        'index.html',
        frontend_version=settings.FRONTEND_VERSION,
        asset_tag=settings.static_asset_tag(),
    )
    resp = make_response(html)
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp


@app.route('/api/health', methods=['GET'])
@limiter.exempt
def health_check():
    sources = source_service.public_list()
    configured_count = sum(
        1 for s in sources
        if bool((source_service.get(s['id']) or {}).get('api_key'))
    )
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'active_conversations': store.count_all(),
        'active_sources': len(sources),
        'configured_sources': configured_count,
    }), 200


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(429)
def ratelimit_exceeded(error):
    return jsonify({'error': 'Too many requests', 'detail': str(error.description)}), 429


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

# ---------------------------------------------------------------------------
# Dev server entry
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    debug = settings.FLASK_ENV == 'development'
    log.info("Starting AI Assistant — http://%s:%s", settings.HOST, settings.PORT)
    log.info("Sources config: %s — %d active", settings.SOURCES_CONFIG_PATH, source_service.count)
    if not debug:
        log.warning("Running Flask dev server in production — use gunicorn instead")
    app.run(host=settings.HOST, port=settings.PORT, debug=debug)
