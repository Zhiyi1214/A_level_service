import logging

from botocore.exceptions import ClientError
from flask import Blueprint, Response, jsonify, stream_with_context

from auth.context import effective_user_id, oauth_login_required_response
from config import settings
from extensions import limiter
from services import image_service

log = logging.getLogger(__name__)

media_bp = Blueprint('media', __name__)


def _owner_from_chat_object_key(key: str) -> str | None:
    parts = key.split('/')
    if len(parts) < 4 or parts[0] != 'chat':
        return None
    return parts[1] or None


@media_bp.route('/api/media/<path:object_key>', methods=['GET'])
@limiter.limit('300 per minute')
def get_object(object_key: str):
    key = (object_key or '').strip()
    if not key or '..' in key or '//' in key or key.startswith('/'):
        return jsonify({'error': 'Bad request'}), 400

    if not image_service.is_s3_configured():
        return jsonify({'error': 'Not found'}), 404

    viewer = effective_user_id()
    if settings.AUTH_CONFIGURED:
        if not viewer:
            return oauth_login_required_response()

    owner = _owner_from_chat_object_key(key)
    if not owner or owner != viewer:
        return jsonify({'error': 'Forbidden'}), 403

    try:
        opened = image_service.open_chat_object_stream(key)
    except ClientError as exc:
        code = exc.response.get('Error', {}).get('Code', '')
        if code in ('NoSuchKey', '404', 404):
            return jsonify({'error': 'Not found'}), 404
        log.exception("get_object failed key=%s", key)
        return jsonify({'error': 'Internal server error'}), 500

    if not opened:
        return jsonify({'error': 'Not found'}), 404

    body, ctype = opened
    body_closed = False

    def _close_stream_body():
        nonlocal body_closed
        if body_closed:
            return
        body_closed = True
        try:
            body.close()
        except Exception:
            pass

    @stream_with_context
    def generate():
        try:
            for chunk in body.iter_chunks(chunk_size=65536):
                if chunk:
                    yield chunk
        finally:
            _close_stream_body()

    out = Response(generate(), mimetype=ctype)
    out.call_on_close(_close_stream_body)
    out.headers['Cache-Control'] = 'private, max-age=300'
    out.headers['X-Content-Type-Options'] = 'nosniff'
    return out
