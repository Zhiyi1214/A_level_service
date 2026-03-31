import logging

from flask import Blueprint, jsonify, request

from auth.context import effective_user_id, oauth_login_required_response
from config import settings
from extensions import limiter
from services import image_service
from services.dify_conversations import hydrate_dify_titles
from storage import store

log = logging.getLogger(__name__)

conversations_bp = Blueprint('conversations', __name__)


def _conversation_access_denied(conv):
    """若当前请求无权访问该会话，返回 (jsonify, status)；否则 None。"""
    uid = effective_user_id()
    if settings.OAUTH_CONFIGURED and not uid:
        return oauth_login_required_response()
    if conv.get('user_id') and uid != conv['user_id']:
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
        try:
            hydrate_dify_titles(raw, user_id)
        except Exception:
            log.exception('hydrate_dify_titles failed')
        hydrated = {}
        for cid, summary in raw.items():
            if not isinstance(summary, dict):
                hydrated[cid] = summary
                continue
            s = dict(summary)
            s.pop('upstream_conversation_id', None)
            dname = (s.pop('dify_conversation_name', None) or '').strip()
            s['dify_title'] = dname
            lm = s.get('last_message')
            if isinstance(lm, dict):
                lm = dict(lm)
                lm['content'] = image_service.rewrite_content_image_refs(
                    lm.get('content'),
                    viewer_user_id=user_id,
                )
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
        msg_limit = request.args.get('message_limit', type=int)
        if msg_limit is not None and (msg_limit < 1 or msg_limit > 200):
            return jsonify({'error': 'Invalid message_limit'}), 400

        before_mid = request.args.get('before_message_id', type=int)
        if before_mid is not None and before_mid < 1:
            return jsonify({'error': 'Invalid before_message_id'}), 400

        conv = store.get(
            conversation_id,
            message_limit=msg_limit,
            before_message_id=before_mid,
        )
        if not conv:
            return jsonify({'error': 'Conversation not found'}), 404

        denied = _conversation_access_denied(conv)
        if denied:
            return denied

        messages = image_service.hydrate_messages_for_client(
            conv['messages'],
            viewer_user_id=conv.get('user_id'),
        )
        return jsonify({
            'success': True,
            'id': conversation_id,
            'created_at': conv['created_at'],
            'messages': messages,
            'source_id': conv.get('source_id', ''),
            'source_name': conv.get('source_name', ''),
            'dify_title': (conv.get('dify_conversation_name') or '').strip(),
            'messages_truncated': bool(conv.get('messages_truncated')),
            'message_count_total': int(conv.get('message_count_total') or len(messages)),
            'has_more_older': bool(conv.get('has_more_older')),
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
