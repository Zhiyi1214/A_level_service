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

SOURCES_CONFIG_PATH = Path(os.getenv('SOURCES_CONFIG_PATH', './config/sources.json'))
if not SOURCES_CONFIG_PATH.is_absolute():
    SOURCES_CONFIG_PATH = BASE_DIR / SOURCES_CONFIG_PATH

# ---------------------------------------------------------------------------
# Flask
# ---------------------------------------------------------------------------
FLASK_ENV = os.getenv('FLASK_ENV', 'production')
_secret_key_env = (os.getenv('SECRET_KEY') or '').strip()
if FLASK_ENV == 'production' and not _secret_key_env:
    raise RuntimeError(
        '生产环境必须设置环境变量 SECRET_KEY；不得在运行时随机生成，否则 Gunicorn '
        '多 Worker 下各进程 Session 签名密钥不一致，用户会被频繁登出或校验失败。'
    )
SECRET_KEY = _secret_key_env or secrets.token_hex(32)
HOST = os.getenv('HOST', '0.0.0.0')
PORT = int(os.getenv('PORT', 5000))
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
# 页脚展示用；可被 .env 覆盖。静态资源 ?v= 使用 static_asset_tag()（文件 mtime），避免只改代码却被 .env 旧值或浏览器强缓存拖死。
FRONTEND_VERSION = os.getenv('FRONTEND_VERSION', '47')


def static_asset_tag() -> str:
    """用于 style.css / script.js 的 ?v=：FRONTEND_VERSION + 文件 mtime，改版本或改文件都会换 URL。"""
    mtimes: list[int] = []
    for name in ('script.js', 'style.css'):
        try:
            p = BASE_DIR / 'static' / name
            mtimes.append(int(p.stat().st_mtime))
        except OSError:
            continue
    suffix = str(max(mtimes)) if mtimes else '0'
    return f'{FRONTEND_VERSION}-{suffix}'

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
_cors_raw = os.getenv('CORS_ORIGINS', '*')
CORS_ORIGINS = [o.strip() for o in _cors_raw.split(',') if o.strip()] or ['*']

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------
RATELIMIT_STORAGE_URI = os.getenv('RATELIMIT_STORAGE_URI', 'memory://')

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
# Image processing
# ---------------------------------------------------------------------------
MAX_UPSTREAM_IMAGES = int(os.getenv('MAX_UPSTREAM_IMAGES', 3))
# 单会话内缓存的 Dify upload_file_id 条数上限（LRU：超出则丢弃最久未更新的键）
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

# 生产环境 HTTPS 下建议设为 true，否则浏览器可能不发送 Session Cookie
SESSION_COOKIE_SECURE = os.getenv('SESSION_COOKIE_SECURE', '').lower() in (
    '1',
    'true',
    'yes',
)
