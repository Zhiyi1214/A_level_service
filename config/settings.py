import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=BASE_DIR / '.env', override=False)

# ---------------------------------------------------------------------------
# Dify / upstream
# ---------------------------------------------------------------------------
DIFY_API_URL = (os.getenv('DIFY_API_URL') or 'http://localhost/v1').rstrip('/')

# 上游 httpx 出站 SSRF：对解析到的 IP 校验。默认同禁回环与链路本地（含 169.254.169.254）；
# 私网段（10/8、172.16/12、192.168/16 等）默认放行，以便 Docker / 内网 Dify。
_UPSTREAM_FLAG = ('1', 'true', 'yes', 'on')
UPSTREAM_HTTP_ALLOW_LOOPBACK = (
    (os.getenv('UPSTREAM_HTTP_ALLOW_LOOPBACK') or '').strip().lower() in _UPSTREAM_FLAG
)
UPSTREAM_HTTP_BLOCK_PRIVATE_NETWORKS = (
    (os.getenv('UPSTREAM_HTTP_BLOCK_PRIVATE_NETWORKS') or '').strip().lower() in _UPSTREAM_FLAG
)

SOURCES_CONFIG_PATH = Path(os.getenv('SOURCES_CONFIG_PATH', './config/sources.json'))
if not SOURCES_CONFIG_PATH.is_absolute():
    SOURCES_CONFIG_PATH = BASE_DIR / SOURCES_CONFIG_PATH

# ---------------------------------------------------------------------------
# Flask / 部署环境
# ---------------------------------------------------------------------------
# 优先 APP_ENV；未设置时沿用 FLASK_ENV（旧文档与镜像仍常见 FLASK_ENV）。
APP_ENV = (os.getenv('APP_ENV') or os.getenv('FLASK_ENV', 'production')).strip()


def flask_run_debug() -> bool:
    """Flask 2.2+ 起勿再用 FLASK_ENV 驱动 debug；显式 FLASK_DEBUG 优先，否则 development 默认开。"""
    raw = (os.getenv('FLASK_DEBUG') or '').strip().lower()
    if raw in ('0', 'false', 'no', 'off'):
        return False
    if raw in ('1', 'true', 'yes', 'on'):
        return True
    return APP_ENV == 'development'


_secret_key_env = (os.getenv('SECRET_KEY') or '').strip()
if APP_ENV == 'production' and not _secret_key_env:
    raise RuntimeError(
        '生产环境必须设置环境变量 SECRET_KEY；不得在运行时随机生成，否则 Gunicorn '
        '多 Worker 下各进程 Session 签名密钥不一致，用户会被频繁登出或校验失败。'
    )
SECRET_KEY = _secret_key_env or secrets.token_hex(32)
HOST = os.getenv('HOST', '0.0.0.0')
PORT = int(os.getenv('PORT', 5000))
# 位于几层「会改写 X-Forwarded-For」的可信反向代理之后（仅本机 Nginx→Gunicorn 为 1；
# 若前面还有一层公网 Nginx/SLB，且内层使用 proxy_add_x_forwarded_for，一般为 2）。
try:
    PROXY_FIX_X_FOR = max(1, min(5, int(os.getenv('PROXY_FIX_X_FOR', '1'))))
except ValueError:
    PROXY_FIX_X_FOR = 1
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
# 页脚展示用；可被 .env 覆盖。静态资源 ?v= 在进程启动时算一次（见 STATIC_ASSET_TAG），避免每个请求 stat 磁盘。
FRONTEND_VERSION = os.getenv('FRONTEND_VERSION', '47')


def _compute_static_asset_tag() -> str:
    """用于 style.css / script.js 的 ?v=：FRONTEND_VERSION + 文件 mtime。"""
    mtimes: list[int] = []
    for name in ('script.js', 'style.css'):
        try:
            p = BASE_DIR / 'static' / name
            mtimes.append(int(p.stat().st_mtime))
        except OSError:
            continue
    suffix = str(max(mtimes)) if mtimes else '0'
    return f'{FRONTEND_VERSION}-{suffix}'


STATIC_ASSET_TAG = _compute_static_asset_tag()


def static_asset_tag() -> str:
    """与 STATIC_ASSET_TAG 相同；保留函数名供旧代码调用。"""
    return STATIC_ASSET_TAG

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
# 开发未设置时允许 *；生产默认空列表（仅同源，不放宽 ACAO），避免 * 与自定义头 CSRF 缓解被跨域脚本滥用。
_cors_env = os.getenv('CORS_ORIGINS')
if _cors_env is None:
    CORS_ORIGINS = ['*'] if APP_ENV == 'development' else []
else:
    CORS_ORIGINS = [o.strip() for o in _cors_env.split(',') if o.strip()]

if APP_ENV == 'production' and len(CORS_ORIGINS) == 1 and CORS_ORIGINS[0] == '*':
    raise RuntimeError(
        '生产环境 CORS_ORIGINS 不能为 *（跨域站点可带 X-Requested-With 发起请求，削弱 CSRF 缓解）。'
        '请改为逗号分隔的明确源（如 https://app.example.com），或与前端同源部署时将 CORS_ORIGINS 留空。'
    )

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------
RATELIMIT_STORAGE_URI = os.getenv('RATELIMIT_STORAGE_URI', 'memory://')
# 全局限流开关（False 时 @limiter.limit 与 default_limits 均不生效，便于测试；生产务必 true）
# 未设置时：development 默认关，其余（含 production）默认开。
_rate_flag = (os.getenv('RATELIMIT_ENABLED') or '').strip().lower()
if _rate_flag in ('0', 'false', 'no', 'off'):
    RATELIMIT_ENABLED = False
elif _rate_flag in ('1', 'true', 'yes', 'on'):
    RATELIMIT_ENABLED = True
else:
    RATELIMIT_ENABLED = APP_ENV != 'development'

# ---------------------------------------------------------------------------
# File upload
# ---------------------------------------------------------------------------
MAX_CONTENT_LENGTH = int(os.getenv('MAX_CONTENT_LENGTH', 52428800))

ALLOWED_EXTENSIONS = set(
    os.getenv('ALLOWED_EXTENSIONS', 'jpg,jpeg,png,gif,webp,pdf,txt,doc,docx').split(',')
)

# ---------------------------------------------------------------------------
# Chat constraints
# ---------------------------------------------------------------------------
MAX_MESSAGE_LENGTH = int(os.getenv('MAX_MESSAGE_LENGTH', 10000))
MAX_CONVERSATIONS_PER_USER = int(os.getenv('MAX_CONVERSATIONS_PER_USER', 50))

# ---------------------------------------------------------------------------
# httpx（上游 Dify 等；可按环境调大 read 以排查慢模型/504）
# ---------------------------------------------------------------------------
HTTPX_CONNECT_TIMEOUT = float(os.getenv('HTTPX_CONNECT_TIMEOUT', '10'))
HTTPX_POOL_TIMEOUT = float(os.getenv('HTTPX_POOL_TIMEOUT', '10'))
HTTPX_STREAM_READ_TIMEOUT = float(os.getenv('HTTPX_STREAM_READ_TIMEOUT', '300'))
HTTPX_STREAM_WRITE_TIMEOUT = float(os.getenv('HTTPX_STREAM_WRITE_TIMEOUT', '60'))
HTTPX_BLOCK_READ_TIMEOUT = float(os.getenv('HTTPX_BLOCK_READ_TIMEOUT', '60'))

# ---------------------------------------------------------------------------
# Image processing
# ---------------------------------------------------------------------------
MAX_UPSTREAM_IMAGES = int(os.getenv('MAX_UPSTREAM_IMAGES', 3))
# 单会话内缓存的 Dify upload_file_id 条数上限（超出按插入顺序淘汰；同键再次写入会移到末尾）
MAX_DIFY_FILE_CACHE_ENTRIES = int(os.getenv('MAX_DIFY_FILE_CACHE_ENTRIES', 64))
MAX_IMAGE_SIDE = int(os.getenv('MAX_IMAGE_SIDE', 1600))
IMAGE_JPEG_QUALITY = int(os.getenv('IMAGE_JPEG_QUALITY', 82))
MAX_COMPRESSED_IMAGE_BYTES = int(os.getenv('MAX_COMPRESSED_IMAGE_BYTES', 1_500_000))
# 无 S3 或上传失败走 data URL 落库时，压缩后二进制不得超过此值（Base64 后更大，宜与上一项同阶或更严）
MAX_DATA_URL_IMAGE_BYTES = int(
    os.getenv('MAX_DATA_URL_IMAGE_BYTES', str(MAX_COMPRESSED_IMAGE_BYTES))
)

# ---------------------------------------------------------------------------
# S3 / MinIO（对象存储；未配置时图片回退为 data URL）
# ---------------------------------------------------------------------------
S3_ENDPOINT_URL = (os.getenv('S3_ENDPOINT_URL') or '').strip().rstrip('/')
S3_ACCESS_KEY = (os.getenv('S3_ACCESS_KEY') or '').strip()
S3_SECRET_KEY = (os.getenv('S3_SECRET_KEY') or '').strip()
S3_BUCKET = (os.getenv('S3_BUCKET') or 'a-level-uploads').strip()
S3_REGION = (os.getenv('S3_REGION') or 'us-east-1').strip()
# 内网 custom_api 等上游拉取对象时的预签名有效期（秒）；浏览器走 /api/media，不用此值生成外链
S3_PRESIGN_EXPIRES = int(os.getenv('S3_PRESIGN_EXPIRES', 3600))

# ---------------------------------------------------------------------------
# Storage（仅 PostgreSQL，不再支持 SQLite）
# ---------------------------------------------------------------------------
DATABASE_URL = (os.getenv('DATABASE_URL') or '').strip()


def validate_database_url() -> None:
    """启动 storage 前调用；缺少或非法时立即失败。"""
    if not DATABASE_URL:
        raise RuntimeError(
            '必须设置环境变量 DATABASE_URL（postgresql://...）。本项目已移除 SQLite。'
        )
    lower = DATABASE_URL.lower()
    if not (
        lower.startswith('postgresql://')
        or lower.startswith('postgresql+psycopg2://')
        or lower.startswith('postgresql+psycopg://')
    ):
        raise RuntimeError(
            'DATABASE_URL 须为 PostgreSQL 连接串（以 postgresql:// 等开头）。'
        )


def sqlalchemy_database_uri() -> str:
    """供 Flask-SQLAlchemy 使用；统一为 psycopg2 驱动 URL。"""
    validate_database_url()
    d = DATABASE_URL.strip()
    lower = d.lower()
    if lower.startswith('postgresql+psycopg2://'):
        return d
    if lower.startswith('postgresql://'):
        return 'postgresql+psycopg2://' + d[len('postgresql://') :]
    if lower.startswith('postgresql+psycopg://'):
        return 'postgresql+psycopg2://' + d[len('postgresql+psycopg://') :]
    return d

# ---------------------------------------------------------------------------
# Session & Redis（多 worker / 上云时建议启用）
# ---------------------------------------------------------------------------
REDIS_URL = (os.getenv('REDIS_URL') or '').strip()
USE_REDIS_SESSION = bool(REDIS_URL)

# ---------------------------------------------------------------------------
# OAuth (Google) — 与 Google Cloud Console 中「已授权的重定向 URI」须完全一致
# ---------------------------------------------------------------------------
GOOGLE_CLIENT_ID = (os.getenv('GOOGLE_CLIENT_ID') or '').strip()
GOOGLE_CLIENT_SECRET = (os.getenv('GOOGLE_CLIENT_SECRET') or '').strip()
GOOGLE_REDIRECT_URI_EXPLICIT = (os.getenv('GOOGLE_REDIRECT_URI') or '').strip()
OAUTH_CONFIGURED = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)

# ---------------------------------------------------------------------------
# 邮箱验证码登录（SMTP 发信；与 Google 可同时启用）
# ---------------------------------------------------------------------------
SMTP_HOST = (os.getenv('SMTP_HOST') or '').strip()
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
SMTP_USER = (os.getenv('SMTP_USER') or '').strip()
SMTP_PASSWORD = (os.getenv('SMTP_PASSWORD') or '').strip()
SMTP_USE_TLS = (os.getenv('SMTP_USE_TLS', 'true') or '').strip().lower() in (
    '1',
    'true',
    'yes',
)
SMTP_FROM = (os.getenv('SMTP_FROM') or SMTP_USER or '').strip()
EMAIL_LOGIN_CODE_TTL_MINUTES = int(os.getenv('EMAIL_LOGIN_CODE_TTL_MINUTES', '10'))
# 无认证 SMTP（如本机 MailHog）时 SMTP_USER 可留空；有密码时通常需同时配用户名
EMAIL_AUTH_CONFIGURED = bool(
    SMTP_HOST
    and SMTP_FROM
    and (SMTP_PASSWORD or not SMTP_USER)
)

AUTH_CONFIGURED = OAUTH_CONFIGURED or EMAIL_AUTH_CONFIGURED


def _parse_admin_emails() -> frozenset[str]:
    raw = (os.getenv('ADMIN_EMAILS') or '').strip()
    if not raw:
        return frozenset()
    return frozenset(
        part.strip().lower() for part in raw.split(',') if part.strip()
    )


# 管理台：二选一或同时启用 —— (1) 口令登录 (2) 已启用主站登录且当前用户邮箱在 ADMIN_EMAILS
ADMIN_SECRET = (os.getenv('ADMIN_SECRET') or '').strip()
ADMIN_EMAILS = _parse_admin_emails()


def admin_console_enabled() -> bool:
    if ADMIN_SECRET:
        return True
    if AUTH_CONFIGURED and ADMIN_EMAILS:
        return True
    return False


# 生产环境 HTTPS 下建议设为 true，否则浏览器可能不发送 Session Cookie
SESSION_COOKIE_SECURE = os.getenv('SESSION_COOKIE_SECURE', '').lower() in (
    '1',
    'true',
    'yes',
)
