import os
import re
import requests
import base64
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# Load .env for local runs. In Docker, compose-injected env (PORT, DIFY_*, etc.)
# must win — override=False keeps existing os.environ from docker-compose.
load_dotenv(dotenv_path=Path(__file__).with_name('.env'), override=False)

# Initialize Flask app
app = Flask(__name__, template_folder='templates', static_folder='static')
_cors = [o.strip() for o in os.getenv('CORS_ORIGINS', '*').split(',') if o.strip()]
CORS(app, origins=_cors or ['*'])

# Configuration
app.config['MAX_CONTENT_LENGTH'] = int(os.getenv('MAX_CONTENT_LENGTH', 52428800))
app.config['UPLOAD_FOLDER'] = os.getenv('UPLOAD_FOLDER', './uploads')
ALLOWED_EXTENSIONS = set(os.getenv('ALLOWED_EXTENSIONS', 'jpg,jpeg,png,gif,webp,pdf,txt,doc,docx').split(','))

# Dify API Configuration（去掉末尾斜杠，避免 //chat-messages）
DIFY_API_URL = (os.getenv('DIFY_API_URL') or 'http://localhost/v1').rstrip('/')
DIFY_API_KEY = os.getenv('DIFY_API_KEY', '')

# Ensure upload folder exists
Path(app.config['UPLOAD_FOLDER']).mkdir(parents=True, exist_ok=True)

# Conversation storage (in-memory for demo, use database in production)
conversations = {}


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


def encode_image_to_base64(file_path):
    """Encode image file to base64 string"""
    with open(file_path, 'rb') as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')


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


@app.route('/api/chat', methods=['POST'])
def chat():
    """
    Main chat endpoint that:
    1. Receives text/images from frontend
    2. Processes and prepares the request
    3. Calls Dify API
    4. Returns response to frontend
    """
    try:
        # Log request details for debugging
        print(f"Request Content-Type: {request.content_type}")
        print(f"Request Method: {request.method}")
        
        # Handle both JSON and FormData (for file uploads)
        user_message = None
        conversation_id = None
        user_id = 'default_user'
        
        # Try to parse as JSON first (silent=True prevents 415 errors)
        data = request.get_json(silent=True, force=False)
        
        if data is not None:
            # It's JSON
            user_message = data.get('message', '').strip()
            conversation_id = data.get('conversation_id', '')
            user_id = data.get('user_id', 'default_user')
        else:
            # Handle FormData from frontend (multipart/form-data)
            user_message = request.form.get('message', '').strip()
            conversation_id = request.form.get('conversation_id', '')
            user_id = request.form.get('user_id', 'default_user')
        
        if not user_message:
            return jsonify({'error': 'Message cannot be empty'}), 400
        
        # Initialize or resume conversation (preserve client/Dify ids across server restarts)
        client_conv_id = (conversation_id or '').strip()
        if client_conv_id:
            conversation_id = client_conv_id
            if conversation_id not in conversations:
                conversations[conversation_id] = {
                    'messages': [],
                    'created_at': datetime.now().isoformat(),
                    'user_id': user_id
                }
        else:
            conversation_id = datetime.now().isoformat()
            conversations[conversation_id] = {
                'messages': [],
                'created_at': conversation_id,
                'user_id': user_id
            }
        
        # Process any uploaded files/images
        image_data = []
        if 'files' in request.files:
            for file in request.files.getlist('files'):
                if file and file.filename and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    if not filename or not allowed_file(filename):
                        continue
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_')
                    filename = timestamp + filename
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    file.save(filepath)
                    
                    # Encode image to base64 if it's an image
                    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
                    if ext in {'jpg', 'jpeg', 'png', 'gif', 'webp'}:
                        image_base64 = encode_image_to_base64(filepath)
                        image_data.append({
                            'type': 'image',
                            'url': f'data:{get_image_mime_type(filename)};base64,{image_base64}'
                        })
        
        # Prepare message content
        message_content = user_message
        if image_data:
            # If there are images, prepare multimodal format
            message_content = [
                {'type': 'text', 'text': user_message}
            ] + image_data
        
        # Call Dify API（新会话须传空字符串；勿把本地临时 id 当作 Dify 的 conversation_id）
        response_data, dify_error = call_dify_api(
            user_message,
            client_conv_id,
            user_id,
            image_data
        )
        
        # 勿用「not response_data」：Dify 若返回 {} 会被误判为失败，且外层 Nginx 可能对 HTML 502 误报
        if response_data is None:
            return jsonify({
                'error': 'Failed to get response from Dify API',
                'detail': dify_error or 'Unknown error',
            }), 502
        
        dify_conv_id = dify_extract_conversation_id(response_data)
        if dify_conv_id and dify_conv_id != conversation_id:
            if conversation_id in conversations:
                conversations[dify_conv_id] = conversations.pop(conversation_id)
            conversation_id = dify_conv_id
        if conversation_id not in conversations:
            conversations[conversation_id] = {
                'messages': [],
                'created_at': datetime.now().isoformat(),
                'user_id': user_id
            }

        # Store conversation
        conversations[conversation_id]['messages'].append({
            'role': 'user',
            'content': message_content,
            'timestamp': datetime.now().isoformat()
        })
        
        answer_text = dify_extract_answer(response_data)
        conversations[conversation_id]['messages'].append({
            'role': 'assistant',
            'content': answer_text,
            'timestamp': datetime.now().isoformat()
        })
        
        return jsonify({
            'success': True,
            'conversation_id': conversation_id,
            'response': answer_text,
            'message_id': response_data.get('message_id'),
            'usage': response_data.get('usage', {})
        }), 200
    
    except Exception as e:
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500


def call_dify_api(message, conversation_id, user_id, image_data=None):
    """
    Call Dify API with message and optional images.

    Returns:
        (response_dict, None) on success
        (None, safe_error_message) on failure
    """
    if not DIFY_API_KEY:
        return None, 'DIFY_API_KEY is not set in .env'

    try:
        # Prepare API endpoint
        api_endpoint = f"{DIFY_API_URL}/chat-messages"
        
        print(f"📤 Calling Dify API: {api_endpoint}")
        if DIFY_API_KEY:
            print(f"🔐 API Key: {DIFY_API_KEY[:4]}…(已配置)")
        else:
            print("🔐 API Key: (未配置)")
        
        headers = {
            'Authorization': f'Bearer {DIFY_API_KEY}',
            'Content-Type': 'application/json'
        }
        
        # Prepare payload
        dify_cid = dify_payload_conversation_id(conversation_id)
        payload = {
            'inputs': {},
            'query': message,
            'response_mode': 'blocking',
            'conversation_id': dify_cid,
            'user': user_id,
            'files': []
        }
        
        # Add images to files if present
        if image_data:
            for img in image_data:
                if img.get('type') == 'image':
                    payload['files'].append({
                        'type': 'image',
                        'url': img.get('url')
                    })
        
        # Make API request
        print(f"📨 Sending payload: {payload}")
        response = requests.post(api_endpoint, json=payload, headers=headers, timeout=60)
        
        print(f"📬 Response status: {response.status_code}")
        print(f"📬 Response body: {response.text[:500]}...")
        
        if 200 <= response.status_code < 300:
            try:
                body = response.json()
                if not isinstance(body, dict):
                    return None, f'Dify returned non-object JSON ({type(body).__name__})'
                return body, None
            except ValueError:
                return None, 'Dify returned invalid JSON'
        else:
            print(f"❌ Dify API error: {response.status_code} - {response.text}")
            snippet = (response.text or '')[:200].replace('\n', ' ')
            return None, f'HTTP {response.status_code}' + (f': {snippet}' if snippet else '')
    
    except requests.exceptions.Timeout:
        print("Dify API request timeout")
        return None, 'Request timed out (60s). Check if Dify is running.'
    except requests.exceptions.RequestException as e:
        print(f"Dify API request failed: {str(e)}")
        hint = str(e)[:240]
        if 'host.docker.internal' in (DIFY_API_URL or ''):
            hint += ' — If Flask runs on your Mac/PC (not in Docker), set DIFY_API_URL to http://localhost/v1 (or your Dify port).'
        return None, hint
    except Exception as e:
        print(f"Error calling Dify API: {str(e)}")
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
                'last_message': conv['messages'][-1] if conv['messages'] else None
            }
            for conv_id, conv in conversations.items()
            if conv.get('user_id') == user_id
        }
        return jsonify({'success': True, 'conversations': user_conversations}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/conversations/<conversation_id>', methods=['GET'])
def get_conversation(conversation_id):
    """Get specific conversation"""
    try:
        if conversation_id not in conversations:
            return jsonify({'error': 'Conversation not found'}), 404
        
        conv = conversations[conversation_id]
        return jsonify({
            'success': True,
            'id': conversation_id,
            'created_at': conv['created_at'],
            'messages': conv['messages']
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/conversations/<conversation_id>', methods=['DELETE'])
def delete_conversation(conversation_id):
    """Delete a conversation"""
    try:
        if conversation_id in conversations:
            del conversations[conversation_id]
            return jsonify({'success': True, 'message': 'Conversation deleted'}), 200
        return jsonify({'error': 'Conversation not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'dify_api_configured': bool(DIFY_API_KEY),
        'active_conversations': len(conversations)
    }), 200


@app.route('/uploads/<filename>')
def download_file(filename):
    """Serve uploaded files"""
    try:
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    except Exception as e:
        return jsonify({'error': 'File not found'}), 404


@app.errorhandler(413)
def request_entity_too_large(error):
    """Handle file too large error"""
    return jsonify({'error': 'File too large. Maximum size is 50MB'}), 413


@app.errorhandler(415)
def unsupported_media_type(error):
    """Handle unsupported media type error"""
    return jsonify({'error': f'Unsupported Media Type. Content-Type: {request.content_type}'}), 415


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
    
    print(f"🚀 Starting AI Assistant Backend")
    print(f"📍 API URL: http://{host}:{port}")
    print(f"🔗 Dify API: {DIFY_API_URL}")
    print(f"🔐 API Key configured: {bool(DIFY_API_KEY)}")
    
    app.run(host=host, port=port, debug=debug)


