from __future__ import annotations

import base64
import hashlib
import io
import logging
from pathlib import Path

from PIL import Image, ImageOps
from werkzeug.utils import secure_filename

from config import settings

log = logging.getLogger(__name__)


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
        log.exception("Image compression skipped for %s", filename)
        return filename, get_mime_type(filename), raw_bytes


def build_processed_images(files) -> list[dict]:
    """Deduplicate and compress uploaded images before upstream upload."""
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
        processed.append({
            'filename': opt_name,
            'mime_type': mime_type,
            'content': opt_bytes,
            'data_url': f"data:{mime_type};base64,{base64.b64encode(opt_bytes).decode('utf-8')}",
        })

    return processed
