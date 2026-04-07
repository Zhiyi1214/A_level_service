import logging
import uuid
from typing import Any

from flask import Blueprint, Response, jsonify, request, stream_with_context

from auth.context import effective_user_id, oauth_login_required_response
from config import settings
from extensions import limiter
from services import chat_service, image_service
from services.source_service import source_service
from storage import store

log = logging.getLogger(__name__)

chat_bp = Blueprint('chat', __name__)


def _parse_chat_body() -> dict[str, str]:
    data = request.get_json(silent=True, force=False)
    if data is not None:
        return {
            'user_message': (data.get('message') or '').strip(),
            'conversation_id': (data.get('conversation_id') or '').strip(),
            'source_id': (data.get('source_id') or '').strip(),
        }
    return {
        'user_message': (request.form.get('message') or '').strip(),
        'conversation_id': (request.form.get('conversation_id') or '').strip(),
        'source_id': (request.form.get('source_id') or '').strip(),
    }


def _require_auth_user():
    user_id = effective_user_id()
    if settings.AUTH_CONFIGURED and not user_id:
        return None, oauth_login_required_response()
    return user_id, None


def _load_conversation_for_chat(
    user_id: str | None,
    conversation_id: str,
    source_id: str,
) -> tuple[dict[str, Any] | None, Any]:
    if not conversation_id:
        return None, (
            jsonify({
                'error': 'conversation_id is required',
                'detail': 'Please create a session via POST /api/sessions before chatting.',
            }),
            400,
        )

    conv = store.get(conversation_id)
    if not conv:
        return None, (
            jsonify({
                'error': 'Conversation not found',
                'detail': 'Session expired or invalid conversation_id.',
            }),
            404,
        )

    if conv.get('user_id') != user_id:
        return None, (jsonify({'error': 'Forbidden', 'detail': 'Not your conversation.'}), 403)

    locked_source_id = conv['source_id']

    if source_id and source_id != locked_source_id:
        return None, (
            jsonify({
                'error': 'source_locked',
                'detail': f'Current session is locked to source_id={locked_source_id}',
            }),
            409,
        )

    source = source_service.get(locked_source_id)
    if not source:
        return None, (
            jsonify({
                'error': 'Source unavailable',
                'detail': f'source_id={locked_source_id} is not enabled',
            }),
            503,
        )

    upstream_cid = (conv.get('upstream_conversation_id') or '').strip()
    return {
        'conv': conv,
        'source': source,
        'locked_source_id': locked_source_id,
        'upstream_cid': upstream_cid,
    }, None


def _process_chat_uploads(
    user_id: str | None,
    conversation_id: str,
) -> tuple[list[dict], list[dict], Any]:
    image_data: list[dict] = []
    image_files: list[dict] = []
    if 'files' not in request.files:
        return image_data, image_files, None
    try:
        processed = image_service.build_processed_images(
            request.files.getlist('files'),
            user_id=user_id,
            conversation_id=conversation_id,
        )
    except ValueError as exc:
        return [], [], (jsonify({'error': 'image_rejected', 'detail': str(exc)}), 400)
    image_files = [
        {
            'filename': p['filename'],
            'mime_type': p['mime_type'],
            'content': p['content'],
            'content_sha256': p.get('content_sha256') or '',
        }
        for p in processed
    ]
    for p in processed:
        seg = {'type': 'image', 'url': p['url']}
        obj_key = p.get('object_key')
        if obj_key:
            seg['object_key'] = obj_key
        image_data.append(seg)
    return image_data, image_files, None


def _validate_message_and_build_content(
    user_message: str,
    image_data: list[dict],
) -> tuple[str | list[Any], Any]:
    if not user_message and not image_data:
        return '', (jsonify({'error': 'Message cannot be empty'}), 400)

    user_message = chat_service.normalize_inbound_user_plaintext(user_message)
    if not user_message and not image_data:
        return '', (jsonify({'error': 'Message cannot be empty'}), 400)

    if user_message and len(user_message) > settings.MAX_MESSAGE_LENGTH:
        return '', (
            jsonify({
                'error': f'Message too long (max {settings.MAX_MESSAGE_LENGTH} chars)',
            }),
            400,
        )

    message_content: str | list[Any] = user_message
    if image_data:
        message_content = [{'type': 'text', 'text': user_message}] + image_data
    return message_content, None


@chat_bp.route('/api/sessions', methods=['POST'])
@limiter.limit("10 per minute")
def create_session():
    try:
        data = request.get_json(silent=True, force=False) or {}
        source_id = (data.get('source_id') or '').strip()
        user_id = effective_user_id()
        if settings.AUTH_CONFIGURED and not user_id:
            return oauth_login_required_response()

        source = source_service.get(source_id)
        if not source:
            return jsonify({'error': 'Invalid source_id'}), 400

        if store.count_by_user(user_id) >= settings.MAX_CONVERSATIONS_PER_USER:
            store.delete_oldest_by_user(user_id)

        session_id = str(uuid.uuid4())
        store.create(session_id, user_id, source['id'], source['name'])

        return jsonify({
            'success': True,
            'session_id': session_id,
            'conversation_id': session_id,
            'source_id': source['id'],
            'source_name': source['name'],
        }), 200
    except Exception:
        log.exception("create_session failed")
        return jsonify({'error': 'Internal server error'}), 500


@chat_bp.route('/api/chat', methods=['POST'])
@limiter.limit("20 per minute")
def chat():
    try:
        fields = _parse_chat_body()
        user_message = fields['user_message']
        conversation_id = fields['conversation_id']
        source_id = fields['source_id']

        user_id, err = _require_auth_user()
        if err:
            return err

        ctx, err = _load_conversation_for_chat(user_id, conversation_id, source_id)
        if err:
            return err[0], err[1]

        assert ctx is not None
        source = ctx['source']
        locked_source_id = ctx['locked_source_id']
        upstream_cid = ctx['upstream_cid']

        image_data, image_files, err = _process_chat_uploads(user_id, conversation_id)
        if err:
            return err[0], err[1]

        message_content, err = _validate_message_and_build_content(user_message, image_data)
        if err:
            return err[0], err[1]

        @stream_with_context
        def generate():
            yield from chat_service.iter_chat_sse_response(
                conversation_id=conversation_id,
                locked_source_id=locked_source_id,
                source=source,
                upstream_cid=upstream_cid,
                user_id=user_id,
                user_message=user_message,
                message_content=message_content,
                image_data=image_data,
                image_files=image_files,
            )

        resp = Response(generate(), mimetype='text/event-stream')
        resp.headers['Cache-Control'] = 'no-cache'
        resp.headers['X-Accel-Buffering'] = 'no'
        return resp
    except Exception:
        log.exception("chat failed")
        return jsonify({'error': 'Internal server error'}), 500
