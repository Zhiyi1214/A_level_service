import logging

from flask import Blueprint, jsonify, request

from auth.context import effective_user_id, oauth_login_required_response
from config import settings
from extensions import limiter
from services import image_service
from storage import store

log = logging.getLogger(__name__)

conversations_bp = Blueprint('conversations', __name__)


def _conversation_access_denied(conv):
    """若当前请求无权访问该会话，返回 (jsonify, status)；否则 None。"""
    if settings.OAUTH_CONFIGURED:
        uid = effective_user_id()
        if not uid:
            return oauth_login_required_response()
        if conv.get('user_id') and uid != conv['user_id']:
            return jsonify({'error': 'Forbidden'}), 403
        return None
    # 无 OAuth：须用 ?user_id= 与落库的 user_id 一致，避免仅凭 conversation_id 越权读取
    req_user = (request.args.get('user_id') or '').strip()
    conv_uid = (conv.get('user_id') or '').strip()
    if conv_uid:
        if not req_user or req_user != conv_uid:
            return jsonify({'error': 'Forbidden'}), 403
    return None


@conversations_bp.route('/api/conversations', methods=['GET'])
@limiter.limit('120 per minute')
def get_conversations():
    try:
        user_id = effective_user_id()
        if settings.OAUTH_CONFIGURED and not user_id:
            return oauth_login_required_response()
        raw = store.list_by_user(user_id)
        hydrated = {}
        for cid, summary in raw.items():
            if not isinstance(summary, dict):
                hydrated[cid] = summary
                continue
            s = dict(summary)
            lm = s.get('last_message')
            if isinstance(lm, dict):
                lm = dict(lm)
                lm['content'] = image_service.rewrite_content_image_refs(lm.get('content'))
                s['last_message'] = lm
            hydrated[cid] = s
        return jsonify({
            'success': True,
            'conversations': hydrated,
        }), 200
    except Exception:
        log.exception("get_conversations failed")
        return jsonify({'error': 'Internal server error'}), 500


@conversations_bp.route('/api/conversations/<conversation_id>', methods=['GET'])
@limiter.limit('120 per minute')
def get_conversation(conversation_id):
    try:
        conv = store.get(conversation_id)
        if not conv:
            return jsonify({'error': 'Conversation not found'}), 404

        denied = _conversation_access_denied(conv)
        if denied:
            return denied

        messages = image_service.hydrate_messages_for_client(conv['messages'])
        return jsonify({
            'success': True,
            'id': conversation_id,
            'created_at': conv['created_at'],
            'messages': messages,
            'source_id': conv.get('source_id', ''),
            'source_name': conv.get('source_name', ''),
        }), 200
    except Exception:
        log.exception("get_conversation failed")
        return jsonify({'error': 'Internal server error'}), 500


@conversations_bp.route('/api/conversations/<conversation_id>', methods=['DELETE'])
@limiter.limit('120 per minute')
def delete_conversation(conversation_id):
    try:
        conv = store.get(conversation_id)
        if not conv:
            return jsonify({'error': 'Conversation not found'}), 404

        denied = _conversation_access_denied(conv)
        if denied:
            return denied

        store.delete(conversation_id)
        return jsonify({'success': True, 'message': 'Conversation deleted'}), 200
    except Exception:
        log.exception("delete_conversation failed")
        return jsonify({'error': 'Internal server error'}), 500
