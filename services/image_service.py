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


def gated_public_media_url(object_key: str, *, viewer_user_id: str | None) -> str:
    """同源受控下载地址（经 /api/media 据会话 Cookie 校验属主）。viewer_user_id 保留参数供调用方一致，URL 不再带 user_id。"""
    key = (object_key or '').strip()
    if not key:
        return ''
    return f'/api/media/{key}'


def presigned_get_url_internal(object_key: str, *, expires_seconds: int | None = None) -> str:
    """供内网上游服务拉取对象（如 custom_api）；使用 S3_ENDPOINT_URL 签名，勿发给浏览器。"""
    key = (object_key or '').strip()
    if not key or not is_s3_configured():
        return ''
    client = _get_s3_client()
    if not client:
        return ''
    exp = expires_seconds if expires_seconds is not None else settings.S3_PRESIGN_EXPIRES
    exp = max(60, min(int(exp), 86400))
    return client.generate_presigned_url(
        'get_object',
        Params={'Bucket': settings.S3_BUCKET, 'Key': key},
        ExpiresIn=exp,
    )


def open_chat_object_stream(object_key: str) -> tuple[Any, str] | None:
    """从桶读取对象，返回 (StreamingBody, Content-Type)。调用方负责关闭 Body。"""
    key = (object_key or '').strip()
    if not key or not is_s3_configured():
        return None
    client = _get_s3_client()
    if not client:
        return None
    resp = client.get_object(Bucket=settings.S3_BUCKET, Key=key)
    body = resp['Body']
    name = key.rsplit('/', 1)[-1] if '/' in key else key
    ctype = resp.get('ContentType') or get_mime_type(name)
    return body, str(ctype)


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


def upload_image_bytes(
    user_id: str,
    conversation_id: str | None,
    filename: str,
    mime_type: str,
    body: bytes,
) -> dict[str, str]:
    """上传单张图片到 MinIO/S3，返回 url（同源 /api/media）与 object_key。"""
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
    return {'url': gated_public_media_url(key, viewer_user_id=user_id), 'object_key': key}


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
    """Deduplicate and compress uploaded images; 配置 S3 时写入对象存储并返回同源 /api/media URL。"""
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


def rewrite_content_image_refs(
    content: Any,
    *,
    viewer_user_id: str | None = None,
) -> Any:
    """为消息中的 object_key 刷新同源 /api/media URL（深拷贝式改写）。"""
    if isinstance(content, str):
        raw = content.strip()
        if raw.startswith('[{'):
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError, ValueError):
                parsed = None
            if isinstance(parsed, list):
                return rewrite_content_image_refs(parsed, viewer_user_id=viewer_user_id)
        return content
    if isinstance(content, list):
        return [
            rewrite_content_image_refs(x, viewer_user_id=viewer_user_id) for x in content
        ]
    if isinstance(content, dict):
        if content.get('type') == 'image' and content.get('object_key'):
            key = content['object_key']
            if isinstance(key, str) and key and is_s3_configured():
                url = gated_public_media_url(key, viewer_user_id=viewer_user_id)
                if url:
                    out = dict(content)
                    out['url'] = url
                    return out
        return {
            k: rewrite_content_image_refs(v, viewer_user_id=viewer_user_id)
            for k, v in content.items()
        }
    return content


def hydrate_messages_for_client(
    messages: list[dict],
    *,
    viewer_user_id: str | None = None,
) -> list[dict]:
    out = []
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            continue
        mm = dict(m)
        mm['content'] = rewrite_content_image_refs(
            m.get('content'),
            viewer_user_id=viewer_user_id,
        )
        out.append(mm)
    return out
