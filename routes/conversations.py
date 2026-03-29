import logging

from flask import Blueprint, jsonify, request

from storage import store

log = logging.getLogger(__name__)

conversations_bp = Blueprint('conversations', __name__)


@conversations_bp.route('/api/conversations', methods=['GET'])
def get_conversations():
    try:
        user_id = request.args.get('user_id', 'default_user')
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

        req_user = (request.args.get('user_id') or '').strip()
        if req_user and conv.get('user_id') and req_user != conv['user_id']:
            return jsonify({'error': 'Forbidden'}), 403

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

        req_user = (request.args.get('user_id') or '').strip()
        if req_user and conv.get('user_id') and req_user != conv['user_id']:
            return jsonify({'error': 'Forbidden'}), 403

        store.delete(conversation_id)
        return jsonify({'success': True, 'message': 'Conversation deleted'}), 200
    except Exception:
        log.exception("delete_conversation failed")
        return jsonify({'error': 'Internal server error'}), 500
