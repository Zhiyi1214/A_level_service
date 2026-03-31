import json
import logging
import re
import uuid
from datetime import datetime

from flask import Blueprint, Response, jsonify, request, stream_with_context

from auth.context import effective_user_id, oauth_login_required_response
from config import settings
from extensions import limiter
from services import chat_service, image_service
from services.source_service import source_service
from storage import store

log = logging.getLogger(__name__)

chat_bp = Blueprint('chat', __name__)


def _normalize_inbound_user_plaintext(text: str) -> str:
    """请求里整段被多包一层 JSON 字符串引号时去掉（否则落库后前端会看到两侧多出的 \"）。"""
    if not isinstance(text, str) or not text:
        return text
    s = text.strip()
    if len(s) < 2 or s[0] != '"' or s[-1] != '"':
        return text
    try:
        parsed = json.loads(s)
        if isinstance(parsed, str):
            return parsed
    except json.JSONDecodeError:
        pass
    return text


# 与 static/script.js 中 ASSISTANT_SAFE_N_PREFIX 一致：字面量 \n 转真换行时勿拆 LaTeX 命令（\neq、\nabla 等）
_ASSISTANT_SAFE_N_PREFIX = re.compile(
    r'^(abla|eq|i\b|u\b|ot|otin|parallel|subseteq|supseteq|subset|supset|'
    r'rightarrow|leftarrow|Rightarrow|Leftarrow|Leftrightarrow|warrow|earrow|exists|uplus|atural)'
)


def _assistant_decode_literal_escapes(s: str) -> str:
    """
    将仍含字面量反斜杠转义的正文还原（Dify 偶发双写）；不得使用全局 .replace('\\\\r')/.replace('\\\\t')，
    否则会误伤 LaTeX 的 \\rightarrow、\\text、\\right（子串 \\\\r 会匹配 \\rightarrow 的前两个字符）。
    """
    if not re.search(r'\\[nr"]', s):
        return s
    s = s.replace('\\r\\n', '\n')

    def _sub_n(m: re.Match) -> str:
        after = s[m.end() :]
        if _ASSISTANT_SAFE_N_PREFIX.match(after):
            return m.group(0)
        return '\n'

    s = re.sub(r'\\n', _sub_n, s)
    return s.replace('\\"', '"')


def _normalize_assistant_plaintext(text: str) -> str:
    """上游偶发返回整段 JSON 字符串（带首尾引号与字面量 \\n）；落库前还原为普通正文。"""
    if not isinstance(text, str) or not text:
        return text
    s = text.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        try:
            parsed = json.loads(s)
            if isinstance(parsed, str):
                return _assistant_decode_literal_escapes(parsed)
        except json.JSONDecodeError:
            pass
    return _assistant_decode_literal_escapes(s)


def _accumulate_stream_chunks(parts: list[str], chunk: str) -> None:
    """合并流式片段：兼容纯增量与「每帧为当前全文」两种上游，并忽略与当前全文相同的重复帧。"""
    if not chunk:
        return
    current = ''.join(parts)
    if chunk == current:
        return
    if current and chunk.startswith(current):
        parts.clear()
        parts.append(chunk)
        return
    if not parts:
        parts.append(chunk)
        return
    parts.append(chunk)


@chat_bp.route('/api/sessions', methods=['POST'])
@limiter.limit("10 per minute")
def create_session():
    try:
        data = request.get_json(silent=True, force=False) or {}
        source_id = (data.get('source_id') or '').strip()
        user_id = effective_user_id()
        if settings.OAUTH_CONFIGURED and not user_id:
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
        # ----- parse request -----
        data = request.get_json(silent=True, force=False)
        if data is not None:
            user_message = (data.get('message') or '').strip()
            conversation_id = (data.get('conversation_id') or '').strip()
            source_id = (data.get('source_id') or '').strip()
        else:
            user_message = (request.form.get('message') or '').strip()
            conversation_id = (request.form.get('conversation_id') or '').strip()
            source_id = (request.form.get('source_id') or '').strip()

        user_id = effective_user_id()
        if settings.OAUTH_CONFIGURED and not user_id:
            return oauth_login_required_response()

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

        if settings.OAUTH_CONFIGURED and session.get('user_id') != user_id:
            return jsonify({'error': 'Forbidden', 'detail': 'Not your conversation.'}), 403

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

        # ----- process images (before empty-body check: allow image-only turns) -----
        image_data: list[dict] = []
        image_files: list[dict] = []
        if 'files' in request.files:
            try:
                processed = image_service.build_processed_images(
                    request.files.getlist('files'),
                    user_id=user_id,
                    conversation_id=conversation_id,
                )
            except ValueError as exc:
                return jsonify({'error': 'image_rejected', 'detail': str(exc)}), 400
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

        # ----- validate message / images -----
        if not user_message and not image_data:
            return jsonify({'error': 'Message cannot be empty'}), 400

        user_message = _normalize_inbound_user_plaintext(user_message)
        if not user_message and not image_data:
            return jsonify({'error': 'Message cannot be empty'}), 400

        if user_message and len(user_message) > settings.MAX_MESSAGE_LENGTH:
            return jsonify({
                'error': f'Message too long (max {settings.MAX_MESSAGE_LENGTH} chars)',
            }), 400

        message_content = user_message
        if image_data:
            message_content = [{'type': 'text', 'text': user_message}] + image_data

        # ----- stream upstream (SSE) -----
        # Dify：每轮请求只带本轮 multipart；同会话同图 sha256 命中 Postgres 缓存则复用 upload_file_id
        upstream_cid = (session.get('upstream_conversation_id') or '').strip()

        def _sse_pack(obj: dict) -> str:
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        def _merge_stream_meta(
            upstream: str, mid, usg: dict, ev: dict
        ) -> tuple[str, object, dict]:
            c = (ev.get('conversation_id') or '').strip()
            if c:
                upstream = c
            if ev.get('message_id') is not None:
                mid = ev.get('message_id')
            u = ev.get('usage')
            if isinstance(u, dict) and u:
                usg = u
            return upstream, mid, usg

        @stream_with_context
        def generate():
            acc: list[str] = []
            stream_upstream = upstream_cid
            msg_id = None
            usage: dict = {}
            dify_cache: dict[str, str] = {}
            dify_get = None
            dify_put = None
            if source.get('type') == 'dify_chat':
                dify_cache = store.get_dify_file_cache(conversation_id)

                def dify_get(h: str):
                    return dify_cache.get(h)

                def dify_put(h: str, fid: str):
                    dify_cache[h] = fid
                    store.put_dify_file_cache_entry(conversation_id, h, fid)

            try:
                for ev in chat_service.iter_source_api_stream(
                    source=source,
                    message=user_message,
                    conversation_id=upstream_cid,
                    user_id=user_id,
                    image_data=image_data,
                    image_files=image_files,
                    dify_file_cache_get=dify_get,
                    dify_file_cache_put=dify_put,
                ):
                    k = ev.get('kind')
                    if k == 'delta':
                        t = ev.get('text') or ''
                        if t:
                            _accumulate_stream_chunks(acc, t)
                            yield _sse_pack({'event': 'delta', 'text': t})
                    elif k in ('meta', 'finished'):
                        stream_upstream, msg_id, usage = _merge_stream_meta(
                            stream_upstream, msg_id, usage, ev
                        )
                    elif k == 'error':
                        yield _sse_pack({
                            'event': 'error',
                            'detail': ev.get('message') or 'Unknown error',
                            'source_id': locked_source_id,
                        })
                        return

                answer_text = _normalize_assistant_plaintext(''.join(acc))
                if stream_upstream:
                    store.update_upstream_id(conversation_id, stream_upstream)

                now = datetime.now().isoformat()
                store.append_message(conversation_id, 'user', message_content, now)
                store.append_message(conversation_id, 'assistant', answer_text, now)

                yield _sse_pack({
                    'event': 'done',
                    'success': True,
                    'conversation_id': conversation_id,
                    'response': answer_text,
                    'message_id': msg_id,
                    'usage': usage,
                    'source_id': locked_source_id,
                    'source_name': source.get('name', locked_source_id),
                })
            except GeneratorExit:
                raise
            except Exception:
                log.exception("chat stream failed")
                yield _sse_pack({'event': 'error', 'detail': 'Internal server error'})

        resp = Response(generate(), mimetype='text/event-stream')
        resp.headers['Cache-Control'] = 'no-cache'
        resp.headers['X-Accel-Buffering'] = 'no'
        return resp
    except Exception:
        log.exception("chat failed")
        return jsonify({'error': 'Internal server error'}), 500
