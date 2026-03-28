import os
import re
import json
import logging
import secrets
import uuid
import requests
import base64
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).with_name('.env'), override=False)

app = Flask(__name__, template_folder='templates', static_folder='static')
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))
log = app.logger
_cors = [o.strip() for o in os.getenv('CORS_ORIGINS', '*').split(',') if o.strip()]
CORS(app, origins=_cors or ['*'])
app.secret_key = os.getenv('SECRET_KEY') or secrets.token_hex(32)

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri=os.getenv('RATELIMIT_STORAGE_URI', 'memory://'),
)

app.config['MAX_CONTENT_LENGTH'] = int(os.getenv('MAX_CONTENT_LENGTH', 52428800))
app.config['UPLOAD_FOLDER'] = os.getenv('UPLOAD_FOLDER', './uploads')
ALLOWED_EXTENSIONS = set(os.getenv('ALLOWED_EXTENSIONS', 'jpg,jpeg,png,gif,webp,pdf,txt,doc,docx').split(','))

DIFY_API_URL = (os.getenv('DIFY_API_URL') or 'http://localhost/v1').rstrip('/')
SOURCES_CONFIG_PATH = Path(os.getenv('SOURCES_CONFIG_PATH', './config/sources.json'))
if not SOURCES_CONFIG_PATH.is_absolute():
    SOURCES_CONFIG_PATH = Path(__file__).parent / SOURCES_CONFIG_PATH

Path(app.config['UPLOAD_FOLDER']).mkdir(parents=True, exist_ok=True)

MAX_MESSAGE_LENGTH = int(os.getenv('MAX_MESSAGE_LENGTH', 10000))
MAX_CONVERSATIONS_PER_USER = int(os.getenv('MAX_CONVERSATIONS_PER_USER', 50))

conversations = {}
source_registry = {}


def _source_public_info(source):
    """Return safe source fields for frontend display."""
    return {
        'id': source['id'],
        'name': source['name'],
        'type': source['type'],
        'description': source.get('description', ''),
        'enabled': bool(source.get('enabled', True))
    }


def _normalize_source(item):
    if not isinstance(item, dict):
        return None
    source_id = str(item.get('id', '')).strip()
    if not source_id:
        return None
    source_type = str(item.get('type', 'dify_chat')).strip() or 'dify_chat'
    api_url = str(item.get('api_url') or item.get('base_url') or '').strip().rstrip('/')
    if not api_url:
        api_url = DIFY_API_URL

    source = {
        'id': source_id,
        'name': str(item.get('name') or source_id),
        'type': source_type,
        'api_url': api_url,
        'auth_ref': str(item.get('auth_ref') or 'DIFY_API_KEY'),
        'description': str(item.get('description') or ''),
        'enabled': bool(item.get('enabled', True)),
        'chat_endpoint': str(item.get('chat_endpoint') or '/chat-messages'),
        'workflow_endpoint': str(item.get('workflow_endpoint') or '/workflows/run'),
        'default_inputs': item.get('default_inputs') if isinstance(item.get('default_inputs'), dict) else {},
    }
    if item.get('custom_payload') and isinstance(item.get('custom_payload'), dict):
        source['custom_payload'] = item['custom_payload']
    if item.get('headers') and isinstance(item.get('headers'), dict):
        source['headers'] = item['headers']
    return source


def load_sources():
    """Load source registry from config/sources.json."""
    configured = []
    if SOURCES_CONFIG_PATH.exists():
        try:
            raw = json.loads(SOURCES_CONFIG_PATH.read_text(encoding='utf-8'))
            if isinstance(raw, list):
                configured = raw
            elif isinstance(raw, dict) and isinstance(raw.get('sources'), list):
                configured = raw['sources']
        except Exception as e:
            app.logger.error(f"Failed to load sources config: {e}")

    loaded = {}
    for item in configured:
        source = _normalize_source(item)
        if not source or not source.get('enabled', True):
            continue
        source['api_key'] = os.getenv(source['auth_ref'], '')
        loaded[source['id']] = source
    return loaded


def get_source_or_none(source_id):
    sid = (source_id or '').strip()
    if not sid:
        return None
    return source_registry.get(sid)


def public_sources():
    return [_source_public_info(s) for s in source_registry.values()]


source_registry = load_sources()


def _sources_file_mtime():
    try:
        return SOURCES_CONFIG_PATH.stat().st_mtime
    except OSError:
        return None


_sources_config_mtime = _sources_file_mtime()


def maybe_reload_sources():
    """Bind-mount 下宿主机改了 sources.json 后，无需重建镜像即可生效。"""
    global source_registry, _sources_config_mtime
    mtime = _sources_file_mtime()
    if mtime is None:
        return
    if _sources_config_mtime is None or mtime != _sources_config_mtime:
        source_registry = load_sources()
        _sources_config_mtime = mtime
        log.info("Reloaded sources from %s", SOURCES_CONFIG_PATH)


@app.before_request
def _before_request_reload_sources():
    maybe_reload_sources()


@app.after_request
def _set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    return response


def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _join_workflow_outputs(outputs):
    """Workflow 阻塞响应里正文常在 data.outputs（无顶层 answer）。"""
    if not isinstance(outputs, dict):
        return ''
    parts = []
    for val in outputs.values():
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, str) and item.strip():
                    parts.append(item.strip())
                elif isinstance(item, dict):
                    for sub in item.values():
                        if isinstance(sub, str) and sub.strip():
                            parts.append(sub.strip())
    return '\n\n'.join(parts) if parts else ''


def dify_extract_answer(resp):
    """从 Dify 阻塞模式响应中取出助手正文（兼容 Chat / Workflow / Advanced Chat）"""
    if not resp:
        return ''
    if not isinstance(resp, dict):
        return str(resp)

    a = resp.get('answer')
    if isinstance(a, str) and a.strip():
        return a
    if a is not None and not isinstance(a, str):
        return str(a)

    data = resp.get('data')
    if isinstance(data, dict):
        inner = data.get('answer')
        if isinstance(inner, str) and inner.strip():
            return inner
        merged = _join_workflow_outputs(data.get('outputs'))
        if merged:
            return merged
        msg = data.get('message')
        if isinstance(msg, dict):
            ma = msg.get('answer')
            if isinstance(ma, str) and ma.strip():
                return ma

    for key in ('output', 'text', 'result'):
        v = resp.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return ''


def dify_extract_conversation_id(resp):
    """Dify 部分应用在嵌套 data 里返回 conversation_id。"""
    if not isinstance(resp, dict):
        return ''
    s = (resp.get('conversation_id') or '').strip()
    if s:
        return s
    data = resp.get('data')
    if isinstance(data, dict):
        s = (data.get('conversation_id') or '').strip()
        if s:
            return s
    return ''


_UUID_CONV_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)


def dify_payload_conversation_id(raw):
    """Dify 只接受会话 UUID；勿把本地时间戳 id 传上去，否则每次都会开新会话。"""
    s = (raw or '').strip()
    return s if _UUID_CONV_RE.match(s) else ''


def get_image_mime_type(filename):
    """Get MIME type for image"""
    ext = filename.rsplit('.', 1)[1].lower()
    mime_types = {
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
        'png': 'image/png',
        'gif': 'image/gif',
        'webp': 'image/webp'
    }
    return mime_types.get(ext, 'image/jpeg')


@app.route('/')
def index():
    """Serve the main HTML page"""
    return render_template('index.html')


@app.route('/api/sources', methods=['GET'])
@limiter.limit("30 per minute")
def list_sources():
    """Return available knowledge sources for frontend dynamic rendering."""
    try:
        return jsonify({'success': True, 'sources': public_sources()}), 200
    except Exception:
        log.exception("list_sources failed")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/sessions', methods=['POST'])
@limiter.limit("10 per minute")
def create_session():
    """
    Create a chat session and lock source_id at creation time.
    Once the session is created, source_id cannot be changed.
    """
    try:
        data = request.get_json(silent=True, force=False) or {}
        source_id = (data.get('source_id') or '').strip()
        user_id = (data.get('user_id') or 'default_user').strip() or 'default_user'

        source = get_source_or_none(source_id)
        if not source:
            return jsonify({'error': 'Invalid source_id'}), 400

        user_conv_ids = [cid for cid, c in conversations.items() if c.get('user_id') == user_id]
        if len(user_conv_ids) >= MAX_CONVERSATIONS_PER_USER:
            oldest = min(user_conv_ids, key=lambda cid: conversations[cid]['created_at'])
            del conversations[oldest]

        session_id = str(uuid.uuid4())
        conversations[session_id] = {
            'messages': [],
            'created_at': datetime.now().isoformat(),
            'user_id': user_id,
            'source_id': source['id'],
            'source_name': source['name'],
            'upstream_conversation_id': ''
        }

        return jsonify({
            'success': True,
            'session_id': session_id,
            'conversation_id': session_id,
            'source_id': source['id'],
            'source_name': source['name']
        }), 200
    except Exception:
        log.exception("create_session failed")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/chat', methods=['POST'])
@limiter.limit("20 per minute")
def chat():
    """
    Chat endpoint with source lock:
    - Source is selectable before chat starts
    - Once a session starts, source_id is locked by session_id
    """
    try:
        user_message = ''
        conversation_id = ''
        user_id = 'default_user'
        source_id = ''

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

        if not user_message:
            return jsonify({'error': 'Message cannot be empty'}), 400

        if len(user_message) > MAX_MESSAGE_LENGTH:
            return jsonify({'error': f'Message too long (max {MAX_MESSAGE_LENGTH} chars)'}), 400

        if not conversation_id:
            return jsonify({
                'error': 'conversation_id is required',
                'detail': 'Please create a session via POST /api/sessions before chatting.'
            }), 400

        session = conversations.get(conversation_id)
        if not session:
            return jsonify({
                'error': 'Conversation not found',
                'detail': 'Session expired or invalid conversation_id.'
            }), 404

        locked_source_id = session['source_id']

        if source_id and source_id != locked_source_id:
            return jsonify({
                'error': 'source_locked',
                'detail': f'Current session is locked to source_id={locked_source_id}'
            }), 409

        source = get_source_or_none(locked_source_id)
        if not source:
            return jsonify({
                'error': 'Source unavailable',
                'detail': f'source_id={locked_source_id} is not enabled'
            }), 503

        image_data = []
        if 'files' in request.files:
            for file in request.files.getlist('files'):
                if not (file and file.filename):
                    continue
                fname = secure_filename(file.filename)
                if not fname or not allowed_file(fname):
                    continue
                ext = fname.rsplit('.', 1)[1].lower() if '.' in fname else ''
                if ext in {'jpg', 'jpeg', 'png', 'gif', 'webp'}:
                    raw = file.read()
                    img_b64 = base64.b64encode(raw).decode('utf-8')
                    image_data.append({
                        'type': 'image',
                        'url': f'data:{get_image_mime_type(fname)};base64,{img_b64}'
                    })

        message_content = user_message
        if image_data:
            message_content = [{'type': 'text', 'text': user_message}] + image_data

        upstream_conversation_id = session.get('upstream_conversation_id', '')
        response_data, upstream_error = call_source_api(
            source=source,
            message=user_message,
            conversation_id=upstream_conversation_id,
            user_id=user_id,
            image_data=image_data
        )

        if response_data is None:
            return jsonify({
                'error': 'Failed to get response from upstream API',
                'detail': upstream_error or 'Unknown error',
                'source_id': locked_source_id
            }), 502

        maybe_upstream_cid = dify_extract_conversation_id(response_data)
        if maybe_upstream_cid:
            session['upstream_conversation_id'] = maybe_upstream_cid

        session['messages'].append({
            'role': 'user',
            'content': message_content,
            'timestamp': datetime.now().isoformat()
        })
        answer_text = dify_extract_answer(response_data)
        session['messages'].append({
            'role': 'assistant',
            'content': answer_text,
            'timestamp': datetime.now().isoformat()
        })

        return jsonify({
            'success': True,
            'conversation_id': conversation_id,
            'response': answer_text,
            'message_id': response_data.get('message_id'),
            'usage': response_data.get('usage', {}),
            'source_id': locked_source_id,
            'source_name': source.get('name', locked_source_id)
        }), 200
    except Exception:
        log.exception("chat failed")
        return jsonify({'error': 'Internal server error'}), 500


def _source_headers(source):
    headers = {'Content-Type': 'application/json'}
    api_key = source.get('api_key', '')
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    extra_headers = source.get('headers', {})
    if isinstance(extra_headers, dict):
        for k, v in extra_headers.items():
            if isinstance(k, str) and isinstance(v, str):
                headers[k] = v
    return headers


def _request_json(api_endpoint, payload, headers):
    response = requests.post(api_endpoint, json=payload, headers=headers, timeout=60)
    log.debug("Upstream response %s (%d bytes)", response.status_code, len(response.text))
    if 200 <= response.status_code < 300:
        try:
            body = response.json()
            if not isinstance(body, dict):
                return None, f'Upstream returned non-object JSON ({type(body).__name__})'
            return body, None
        except ValueError:
            return None, 'Upstream returned invalid JSON'
    snippet = (response.text or '')[:200].replace('\n', ' ')
    return None, f'HTTP {response.status_code}' + (f': {snippet}' if snippet else '')


def call_dify_chat_api(source, message, conversation_id, user_id, image_data=None):
    api_endpoint = f"{source['api_url']}{source.get('chat_endpoint', '/chat-messages')}"
    headers = _source_headers(source)
    payload = {
        'inputs': source.get('default_inputs', {}),
        'query': message,
        'response_mode': 'blocking',
        'conversation_id': dify_payload_conversation_id(conversation_id),
        'user': user_id,
        'files': []
    }
    if image_data:
        for img in image_data:
            if img.get('type') == 'image':
                payload['files'].append({'type': 'image', 'url': img.get('url')})
    log.info("Calling source[%s] chat: %s", source['id'], api_endpoint)
    return _request_json(api_endpoint, payload, headers)


def call_dify_workflow_api(source, message, user_id):
    api_endpoint = f"{source['api_url']}{source.get('workflow_endpoint', '/workflows/run')}"
    headers = _source_headers(source)
    inputs = dict(source.get('default_inputs', {}))
    inputs.setdefault('query', message)
    inputs.setdefault('message', message)
    payload = {
        'inputs': inputs,
        'response_mode': 'blocking',
        'user': user_id
    }
    log.info("Calling source[%s] workflow: %s", source['id'], api_endpoint)
    return _request_json(api_endpoint, payload, headers)


def call_custom_api(source, message, conversation_id, user_id, image_data=None):
    endpoint = source.get('chat_endpoint', '/chat')
    api_endpoint = endpoint if endpoint.startswith('http') else f"{source['api_url']}{endpoint}"
    headers = _source_headers(source)
    payload = {
        'message': message,
        'conversation_id': conversation_id,
        'user_id': user_id,
        'files': image_data or [],
        'inputs': source.get('default_inputs', {})
    }
    custom_payload = source.get('custom_payload')
    if isinstance(custom_payload, dict):
        payload.update(custom_payload)
    log.info("Calling source[%s] custom: %s", source['id'], api_endpoint)
    return _request_json(api_endpoint, payload, headers)


def call_source_api(source, message, conversation_id, user_id, image_data=None):
    """
    Generic source dispatcher.
    Supported types:
      - dify_chat
      - dify_workflow
      - custom_api
    """
    if not source:
        return None, 'source is required'
    if not source.get('api_key') and source.get('type') in {'dify_chat', 'dify_workflow'}:
        return None, f"Missing API key env: {source.get('auth_ref')}"

    source_type = source.get('type')
    try:
        if source_type == 'dify_chat':
            return call_dify_chat_api(source, message, conversation_id, user_id, image_data=image_data)
        if source_type == 'dify_workflow':
            return call_dify_workflow_api(source, message, user_id)
        if source_type == 'custom_api':
            return call_custom_api(source, message, conversation_id, user_id, image_data=image_data)
        return None, f'Unsupported source type: {source_type}'
    except requests.exceptions.Timeout:
        return None, 'Request timed out (60s). Check if upstream is running.'
    except requests.exceptions.RequestException as e:
        hint = str(e)[:240]
        return None, hint
    except Exception as e:
        return None, str(e)[:240]


@app.route('/api/conversations', methods=['GET'])
def get_conversations():
    """Get list of all conversations"""
    try:
        user_id = request.args.get('user_id', 'default_user')
        user_conversations = {
            conv_id: {
                'id': conv_id,
                'created_at': conv['created_at'],
                'message_count': len(conv['messages']),
                'last_message': conv['messages'][-1] if conv['messages'] else None,
                'source_id': conv.get('source_id', ''),
                'source_name': conv.get('source_name', '')
            }
            for conv_id, conv in conversations.items()
            if conv.get('user_id') == user_id
        }
        return jsonify({'success': True, 'conversations': user_conversations}), 200
    except Exception:
        log.exception("get_conversations failed")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/conversations/<conversation_id>', methods=['GET'])
def get_conversation(conversation_id):
    """Get specific conversation"""
    try:
        if conversation_id not in conversations:
            return jsonify({'error': 'Conversation not found'}), 404

        conv = conversations[conversation_id]
        req_user = (request.args.get('user_id') or '').strip()
        if req_user and conv.get('user_id') and req_user != conv['user_id']:
            return jsonify({'error': 'Forbidden'}), 403

        return jsonify({
            'success': True,
            'id': conversation_id,
            'created_at': conv['created_at'],
            'messages': conv['messages'],
            'source_id': conv.get('source_id', ''),
            'source_name': conv.get('source_name', '')
        }), 200
    except Exception:
        log.exception("get_conversation failed")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/conversations/<conversation_id>', methods=['DELETE'])
def delete_conversation(conversation_id):
    """Delete a conversation"""
    try:
        if conversation_id not in conversations:
            return jsonify({'error': 'Conversation not found'}), 404

        conv = conversations[conversation_id]
        req_user = (request.args.get('user_id') or '').strip()
        if req_user and conv.get('user_id') and req_user != conv['user_id']:
            return jsonify({'error': 'Forbidden'}), 403

        del conversations[conversation_id]
        return jsonify({'success': True, 'message': 'Conversation deleted'}), 200
    except Exception:
        log.exception("delete_conversation failed")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/health', methods=['GET'])
@limiter.exempt
def health_check():
    """Health check endpoint — only exposes non-sensitive info."""
    sources = public_sources()
    configured_count = sum(
        1 for s in sources
        if bool((get_source_or_none(s['id']) or {}).get('api_key'))
    )

    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'active_conversations': len(conversations),
        'active_sources': len(sources),
        'configured_sources': configured_count,
    }), 200


@app.route('/uploads/<filename>')
def download_file(filename):
    safe_name = secure_filename(filename)
    if not safe_name or safe_name != filename:
        return jsonify({'error': 'Invalid filename'}), 400
    return send_from_directory(app.config['UPLOAD_FOLDER'], safe_name)


@app.errorhandler(429)
def ratelimit_exceeded(error):
    return jsonify({'error': 'Too many requests', 'detail': str(error.description)}), 429


@app.errorhandler(413)
def request_entity_too_large(error):
    max_mb = app.config['MAX_CONTENT_LENGTH'] / (1024 * 1024)
    return jsonify({'error': f'文件过大，最大允许 {max_mb:.0f}MB'}), 413


@app.errorhandler(415)
def unsupported_media_type(error):
    return jsonify({'error': 'Unsupported Media Type'}), 415


@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors"""
    return jsonify({'error': 'Not found'}), 404


@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors"""
    return jsonify({'error': 'Internal server error'}), 500


if __name__ == '__main__':
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_ENV') == 'development'

    log.info("Starting AI Assistant — http://%s:%s", host, port)
    log.info("Sources config: %s — %d active", SOURCES_CONFIG_PATH, len(public_sources()))
    if not debug:
        log.warning("Running Flask dev server in production — use gunicorn instead")

    app.run(host=host, port=port, debug=debug)


