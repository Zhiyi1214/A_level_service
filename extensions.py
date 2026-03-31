from authlib.integrations.flask_client import OAuth
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from config import settings

oauth = OAuth()
db = SQLAlchemy()
migrate = Migrate()


def init_session(app):
    """服务端 Session：配置 REDIS_URL 时使用 Redis，便于多 gunicorn worker 共享登录态。"""
    if not settings.USE_REDIS_SESSION:
        return
    import redis
    from flask_session import Session

    app.config['SESSION_TYPE'] = 'redis'
    app.config['SESSION_REDIS'] = redis.from_url(
        settings.REDIS_URL,
        decode_responses=False,
    )
    app.config['SESSION_USE_SIGNER'] = True
    app.config['SESSION_KEY_PREFIX'] = 'a_level:sess:'
    Session(app)

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri=settings.RATELIMIT_STORAGE_URI,
)


def init_extensions(app):
    app.config['SQLALCHEMY_DATABASE_URI'] = settings.sqlalchemy_database_uri()
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_size': 10,
        'max_overflow': 20,
        'pool_recycle': 3600,
        'pool_pre_ping': True,
    }
    db.init_app(app)
    migrate.init_app(app, db)
    init_session(app)
    _cors_kwargs: dict = {'origins': settings.CORS_ORIGINS}
    # 带 Cookie 的跨域请求不能与 origins=* 同时使用
    if settings.OAUTH_CONFIGURED and not (
        len(settings.CORS_ORIGINS) == 1 and settings.CORS_ORIGINS[0] == '*'
    ):
        _cors_kwargs['supports_credentials'] = True
    CORS(app, **_cors_kwargs)
    limiter.init_app(app)
    if settings.OAUTH_CONFIGURED:
        oauth.init_app(app)
        oauth.register(
            name='google',
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET,
            authorize_url='https://accounts.google.com/o/oauth2/v2/auth',
            access_token_url='https://oauth2.googleapis.com/token',
            api_base_url='https://openidconnect.googleapis.com/v1/',
            jwks_uri='https://www.googleapis.com/oauth2/v3/certs',
            userinfo_endpoint='https://openidconnect.googleapis.com/v1/userinfo',
            client_kwargs={'scope': 'openid email profile'},
        )
