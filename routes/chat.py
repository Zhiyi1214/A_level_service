import logging
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request

from config import settings
from extensions import limiter
from services import chat_service, image_service
from services.source_service import source_service
from storage import store

log = logging.getLogger(__name__)

chat_bp = Blueprint('chat', __name__)


@chat_bp.route('/api/sessions', methods=['POST'])
@limiter.limit("10 per minute")
def create_session():
    try:
        data = request.get_json(silent=True, force=False) or {}
        source_id = (data.get('source_id') or '').strip()
        user_id = (data.get('user_id') or 'default_user').strip() or 'default_user'

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
        # ----- parse request -----
        data = request.get_json(silent=True, force=False)
        if data is not None:
            user_message = (data.get('message') or '').strip()
            conversation_id = (data.get('conversation_id') or '').strip()
            user_id = (data.get('user_id') or 'default_user').strip() or 'default_user'
            source_id = (data.get('source_id') or '').strip()
        else:
            user_message = (request.form.get('message') or '').strip()
            conversation_id = (request.form.get('conversation_id') or '').strip()
            user_id = (request.form.get('user_id') or 'default_user').strip() or 'default_user'
            source_id = (request.form.get('source_id') or '').strip()

        # ----- validate -----
        if not user_message:
            return jsonify({'error': 'Message cannot be empty'}), 400

        if len(user_message) > settings.MAX_MESSAGE_LENGTH:
            return jsonify({
                'error': f'Message too long (max {settings.MAX_MESSAGE_LENGTH} chars)',
            }), 400

        if not conversation_id:
            return jsonify({
                'error': 'conversation_id is required',
                'detail': 'Please create a session via POST /api/sessions before chatting.',
            }), 400

        session = store.get(conversation_id)
        if not session:
            return jsonify({
                'error': 'Conversation not found',
                'detail': 'Session expired or invalid conversation_id.',
            }), 404

        locked_source_id = session['source_id']

        if source_id and source_id != locked_source_id:
            return jsonify({
                'error': 'source_locked',
                'detail': f'Current session is locked to source_id={locked_source_id}',
            }), 409

        source = source_service.get(locked_source_id)
        if not source:
            return jsonify({
                'error': 'Source unavailable',
                'detail': f'source_id={locked_source_id} is not enabled',
            }), 503

        # ----- process images -----
        image_data: list[dict] = []
        image_files: list[dict] = []
        if 'files' in request.files:
            processed = image_service.build_processed_images(
                request.files.getlist('files')
            )
            image_files = [
                {'filename': p['filename'], 'mime_type': p['mime_type'], 'content': p['content']}
                for p in processed
            ]
            image_data = [{'type': 'image', 'url': p['data_url']} for p in processed]

        message_content = user_message
        if image_data:
            message_content = [{'type': 'text', 'text': user_message}] + image_data

        # ----- call upstream -----
        active_image_files = image_files or store.get_image_cache(conversation_id)
        upstream_cid = session.get('upstream_conversation_id', '')

        response_data, upstream_error = chat_service.call_source_api(
            source=source,
            message=user_message,
            conversation_id=upstream_cid,
            user_id=user_id,
            image_data=image_data,
            image_files=active_image_files,
        )

        if response_data is None:
            return jsonify({
                'error': 'Failed to get response from upstream API',
                'detail': upstream_error or 'Unknown error',
                'source_id': locked_source_id,
            }), 502

        # ----- persist state -----
        maybe_cid = chat_service.extract_conversation_id(response_data)
        if maybe_cid:
            store.update_upstream_id(conversation_id, maybe_cid)
        if image_files:
            store.set_image_cache(conversation_id, image_files)

        now = datetime.now().isoformat()
        store.append_message(conversation_id, 'user', message_content, now)
        answer_text = chat_service.extract_answer(response_data)
        store.append_message(conversation_id, 'assistant', answer_text, now)

        return jsonify({
            'success': True,
            'conversation_id': conversation_id,
            'response': answer_text,
            'message_id': response_data.get('message_id'),
            'usage': response_data.get('usage', {}),
            'source_id': locked_source_id,
            'source_name': source.get('name', locked_source_id),
        }), 200
    except Exception:
        log.exception("chat failed")
        return jsonify({'error': 'Internal server error'}), 500
