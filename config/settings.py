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
SECRET_KEY = os.getenv('SECRET_KEY') or secrets.token_hex(32)
FLASK_ENV = os.getenv('FLASK_ENV', 'production')
HOST = os.getenv('HOST', '0.0.0.0')
PORT = int(os.getenv('PORT', 5000))
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
FRONTEND_VERSION = os.getenv('FRONTEND_VERSION', '41')

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

_upload_folder = os.getenv('UPLOAD_FOLDER', './uploads')
UPLOAD_FOLDER = str(BASE_DIR / _upload_folder) if not os.path.isabs(_upload_folder) else _upload_folder

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
MAX_IMAGE_SIDE = int(os.getenv('MAX_IMAGE_SIDE', 1600))
IMAGE_JPEG_QUALITY = int(os.getenv('IMAGE_JPEG_QUALITY', 82))
MAX_COMPRESSED_IMAGE_BYTES = int(os.getenv('MAX_COMPRESSED_IMAGE_BYTES', 1_500_000))

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
# 设置 DATABASE_URL（postgresql://...）时使用 PostgreSQL；否则使用本地 SQLite。
DATABASE_URL = (os.getenv('DATABASE_URL') or '').strip()
DATABASE_PATH = str(BASE_DIR / 'data' / 'conversations.db')
USE_POSTGRES = bool(
    DATABASE_URL
    and DATABASE_URL.lower().startswith('postgresql')
)

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
