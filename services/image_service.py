from __future__ import annotations

import base64
import hashlib
import json
import io
import logging
import re
import uuid
from pathlib import Path
from typing import Any
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from PIL import Image, ImageOps
from werkzeug.utils import secure_filename

from config import settings

log = logging.getLogger(__name__)

_s3_client = None
_s3_presign_client = None


def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in settings.ALLOWED_EXTENSIONS


def get_mime_type(filename: str) -> str:
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    return {
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
        'png': 'image/png',
        'gif': 'image/gif',
        'webp': 'image/webp',
    }.get(ext, 'image/jpeg')


def is_s3_configured() -> bool:
    return bool(
        settings.S3_ENDPOINT_URL
        and settings.S3_ACCESS_KEY
        and settings.S3_SECRET_KEY
        and settings.S3_BUCKET
    )


def _get_s3_client():
    global _s3_client
    if _s3_client is None and is_s3_configured():
        _s3_client = boto3.client(
            's3',
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            config=Config(
                signature_version='s3v4',
                s3={'addressing_style': 'path'},
            ),
            region_name=settings.S3_REGION,
        )
    return _s3_client


def _presign_endpoint_url() -> str:
    """预签名 URL 的 Host 须与浏览器请求一致（SigV4 含 Host）；勿先签 minio:9000 再换成 localhost。"""
    pub = (settings.S3_BROWSER_BASE_URL or settings.S3_ENDPOINT_URL or '').strip().rstrip('/')
    return pub


def _get_s3_presign_client():
    global _s3_presign_client
    if _s3_presign_client is None and is_s3_configured():
        endpoint = _presign_endpoint_url()
        if not endpoint:
            return None
        _s3_presign_client = boto3.client(
            's3',
            endpoint_url=endpoint,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            config=Config(
                signature_version='s3v4',
                s3={'addressing_style': 'path'},
            ),
            region_name=settings.S3_REGION,
        )
    return _s3_presign_client


def ensure_bucket_exists() -> None:
    if not is_s3_configured():
        return
    client = _get_s3_client()
    if not client:
        return
    try:
        client.head_bucket(Bucket=settings.S3_BUCKET)
    except ClientError as exc:
        code = exc.response.get('Error', {}).get('Code', '')
        if code in ('404', 'NoSuchBucket', 404):
            try:
                client.create_bucket(Bucket=settings.S3_BUCKET)
                log.info("Created S3 bucket %s", settings.S3_BUCKET)
            except ClientError:
                log.exception("Failed to create bucket %s", settings.S3_BUCKET)
        else:
            log.warning("head_bucket %s: %s", settings.S3_BUCKET, exc)


def _safe_path_segment(s: str) -> str:
    t = (s or '').strip()
    if not t:
        return 'anon'
    return re.sub(r'[^a-zA-Z0-9._-]+', '_', t)[:128]


def build_object_key(user_id: str, conversation_id: str | None, filename: str) -> str:
    uid = _safe_path_segment(user_id)
    cid = _safe_path_segment(conversation_id or 'na')
    stem = secure_filename(filename) or 'image.bin'
    return f"chat/{uid}/{cid}/{uuid.uuid4().hex}_{stem}"


def _presigned_get_url(object_key: str) -> str:
    client = _get_s3_presign_client()
    if not client:
        return ''
    return client.generate_presigned_url(
        'get_object',
        Params={'Bucket': settings.S3_BUCKET, 'Key': object_key},
        ExpiresIn=settings.S3_PRESIGN_EXPIRES,
    )


def upload_image_bytes(
    user_id: str,
    conversation_id: str | None,
    filename: str,
    mime_type: str,
    body: bytes,
) -> dict[str, str]:
    """上传单张图片到 MinIO/S3，返回 url（预签名）与 object_key。"""
    if not is_s3_configured():
        return {'url': '', 'object_key': ''}
    client = _get_s3_client()
    if not client:
        return {'url': '', 'object_key': ''}
    key = build_object_key(user_id, conversation_id, filename)
    try:
        client.put_object(
            Bucket=settings.S3_BUCKET,
            Key=key,
            Body=body,
            ContentType=mime_type or 'application/octet-stream',
        )
    except ClientError:
        log.exception("put_object failed key=%s", key)
        raise
    return {'url': _presigned_get_url(key), 'object_key': key}


def download_object_bytes(object_key: str) -> bytes | None:
    """按 Object Key 从已配置的桶读取对象体（例如从仅含 key 的 Redis 缓存再水合为上传用字节）。"""
    key = (object_key or '').strip()
    if not key or not is_s3_configured():
        return None
    client = _get_s3_client()
    if not client:
        return None
    try:
        resp = client.get_object(Bucket=settings.S3_BUCKET, Key=key)
        return resp['Body'].read()
    except ClientError:
        log.exception("get_object failed key=%s", key)
        return None


def compress(filename: str, raw_bytes: bytes) -> tuple[str, str, bytes]:
    """Resize / compress a single image before upstream upload."""
    suffix = Path(filename).suffix.lower()
    if suffix == '.gif':
        return filename, 'image/gif', raw_bytes

    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            img = ImageOps.exif_transpose(img)
            max_side = max(img.size) if img.size else 0
            if max_side and max_side > settings.MAX_IMAGE_SIDE:
                scale = settings.MAX_IMAGE_SIDE / float(max_side)
                new_size = (
                    max(1, int(img.size[0] * scale)),
                    max(1, int(img.size[1] * scale)),
                )
                img = img.resize(new_size, Image.Resampling.LANCZOS)

            stem = Path(filename).stem or 'image'
            has_alpha = img.mode in ('RGBA', 'LA') or ('transparency' in img.info)

            if has_alpha:
                if img.mode != 'RGBA':
                    img = img.convert('RGBA')
                buf = io.BytesIO()
                img.save(buf, format='PNG', optimize=True)
                optimized = buf.getvalue()
                if len(optimized) <= settings.MAX_COMPRESSED_IMAGE_BYTES:
                    return f'{stem}.png', 'image/png', optimized
                background = Image.new('RGB', img.size, (255, 255, 255))
                background.paste(img, mask=img.getchannel('A'))
                img = background

            if img.mode != 'RGB':
                img = img.convert('RGB')

            quality = settings.IMAGE_JPEG_QUALITY
            optimized = raw_bytes
            while quality >= 45:
                buf = io.BytesIO()
                img.save(buf, format='JPEG', quality=quality, optimize=True, progressive=True)
                optimized = buf.getvalue()
                if len(optimized) <= settings.MAX_COMPRESSED_IMAGE_BYTES or quality == 45:
                    break
                quality -= 10
            return f'{stem}.jpg', 'image/jpeg', optimized
    except Exception:
        log.exception("Image compression failed for %s", filename)
        raise ValueError("无法处理该图像文件，可能已损坏或尺寸过大") from None


def _reject_oversized_for_data_url(opt_bytes: bytes) -> None:
    limit = settings.MAX_DATA_URL_IMAGE_BYTES
    if len(opt_bytes) <= limit:
        return
    kb = max(1, limit // 1024)
    raise ValueError(
        f'图片压缩后仍超过内联上限（约 {kb}KB）。未配置对象存储或上传失败时无法内联保存过大图片；'
        '请换一张较小的图或配置 S3/MinIO。'
    )


def build_processed_images(
    files,
    *,
    user_id: str = '',
    conversation_id: str | None = None,
) -> list[dict]:
    """Deduplicate and compress uploaded images; 配置 S3 时写入对象存储并返回预签名 URL。"""
    processed: list[dict] = []
    seen_hashes: set[str] = set()

    for file in files or []:
        if len(processed) >= settings.MAX_UPSTREAM_IMAGES:
            break
        if not (file and file.filename):
            continue

        fname = secure_filename(file.filename)
        if not fname or not allowed_file(fname):
            continue

        ext = fname.rsplit('.', 1)[1].lower() if '.' in fname else ''
        if ext not in {'jpg', 'jpeg', 'png', 'gif', 'webp'}:
            continue

        raw = file.read()
        if not raw:
            continue

        digest = hashlib.sha256(raw).hexdigest()
        if digest in seen_hashes:
            log.info("Skipped duplicate image: %s", fname)
            continue
        seen_hashes.add(digest)

        opt_name, mime_type, opt_bytes = compress(fname, raw)
        item: dict[str, Any] = {
            'filename': opt_name,
            'mime_type': mime_type,
            'content': opt_bytes,
            'content_sha256': digest,
        }
        if is_s3_configured():
            try:
                up = upload_image_bytes(
                    user_id, conversation_id, opt_name, mime_type, opt_bytes
                )
                item['url'] = up['url']
                item['object_key'] = up['object_key']
            except Exception:
                log.exception("S3 upload failed; falling back to data URL")
                _reject_oversized_for_data_url(opt_bytes)
                item['data_url'] = (
                    f"data:{mime_type};base64,{base64.b64encode(opt_bytes).decode('utf-8')}"
                )
                item['url'] = item['data_url']
        else:
            _reject_oversized_for_data_url(opt_bytes)
            item['data_url'] = (
                f"data:{mime_type};base64,{base64.b64encode(opt_bytes).decode('utf-8')}"
            )
            item['url'] = item['data_url']

        processed.append(item)

    return processed


def rewrite_content_image_refs(content: Any) -> Any:
    """为消息中的 object_key 刷新预签名 URL（深拷贝式改写）。"""
    if isinstance(content, str):
        raw = content.strip()
        if raw.startswith('[{'):
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError, ValueError):
                parsed = None
            if isinstance(parsed, list):
                return rewrite_content_image_refs(parsed)
        return content
    if isinstance(content, list):
        return [rewrite_content_image_refs(x) for x in content]
    if isinstance(content, dict):
        if content.get('type') == 'image' and content.get('object_key'):
            key = content['object_key']
            if isinstance(key, str) and key and is_s3_configured():
                url = _presigned_get_url(key)
                if url:
                    out = dict(content)
                    out['url'] = url
                    return out
        return {k: rewrite_content_image_refs(v) for k, v in content.items()}
    return content


def hydrate_messages_for_client(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            continue
        mm = dict(m)
        mm['content'] = rewrite_content_image_refs(m.get('content'))
        out.append(mm)
    return out
