import logging

from flask import Blueprint, jsonify, request

from auth.context import effective_user_id, oauth_login_required_response
from config import settings
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
    req_user = (request.args.get('user_id') or '').strip()
    if req_user and conv.get('user_id') and req_user != conv['user_id']:
        return jsonify({'error': 'Forbidden'}), 403
    return None


@conversations_bp.route('/api/conversations', methods=['GET'])
def get_conversations():
    try:
        user_id = effective_user_id()
        if settings.OAUTH_CONFIGURED and not user_id:
            return oauth_login_required_response()
        return jsonify({
            'success': True,
            'conversations': store.list_by_user(user_id),
        }), 200
    except Exception:
        log.exception("get_conversations failed")
        return jsonify({'error': 'Internal server error'}), 500


@conversations_bp.route('/api/conversations/<conversation_id>', methods=['GET'])
def get_conversation(conversation_id):
    try:
        conv = store.get(conversation_id)
        if not conv:
            return jsonify({'error': 'Conversation not found'}), 404

        denied = _conversation_access_denied(conv)
        if denied:
            return denied

        return jsonify({
            'success': True,
            'id': conversation_id,
            'created_at': conv['created_at'],
            'messages': conv['messages'],
            'source_id': conv.get('source_id', ''),
            'source_name': conv.get('source_name', ''),
        }), 200
    except Exception:
        log.exception("get_conversation failed")
        return jsonify({'error': 'Internal server error'}), 500


@conversations_bp.route('/api/conversations/<conversation_id>', methods=['DELETE'])
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
