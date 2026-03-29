from __future__ import annotations

import logging
import re

import requests

log = logging.getLogger(__name__)

_UUID_CONV_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)

# ======================================================================
# Response parsing
# ======================================================================

def extract_answer(resp) -> str:
    """Extract the assistant reply from a Dify blocking-mode response."""
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


def extract_conversation_id(resp) -> str:
    """Extract upstream conversation_id (may be nested under data)."""
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


def sanitize_conversation_id(raw) -> str:
    """Only pass valid UUIDs upstream; local timestamps would cause new sessions."""
    s = (raw or '').strip()
    return s if _UUID_CONV_RE.match(s) else ''


# ======================================================================
# Upstream API calls
# ======================================================================

def call_source_api(
    source, message, conversation_id, user_id,
    image_data=None, image_files=None,
):
    """Dispatch to the correct upstream backend based on source type."""
    if not source:
        return None, 'source is required'
    if not source.get('api_key') and source.get('type') in {'dify_chat', 'dify_workflow'}:
        return None, f"Missing API key env: {source.get('auth_ref')}"

    source_type = source.get('type')
    try:
        if source_type == 'dify_chat':
            return _call_dify_chat(source, message, conversation_id, user_id, image_files)
        if source_type == 'dify_workflow':
            return _call_dify_workflow(source, message, user_id)
        if source_type == 'custom_api':
            return _call_custom(source, message, conversation_id, user_id, image_data)
        return None, f'Unsupported source type: {source_type}'
    except requests.exceptions.Timeout:
        return None, 'Request timed out (60s). Check if upstream is running.'
    except requests.exceptions.RequestException as exc:
        return None, str(exc)[:240]
    except Exception as exc:
        return None, str(exc)[:240]


# ======================================================================
# Internal helpers
# ======================================================================

def _source_headers(source, *, include_content_type=True):
    headers: dict[str, str] = {}
    if include_content_type:
        headers['Content-Type'] = 'application/json'
    api_key = source.get('api_key', '')
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    extra = source.get('headers', {})
    if isinstance(extra, dict):
        for k, v in extra.items():
            if isinstance(k, str) and isinstance(v, str):
                if not include_content_type or k.lower() != 'content-type':
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


def _upload_dify_file(source, user_id, file_item):
    api_endpoint = f"{source['api_url']}/files/upload"
    headers = _source_headers(source, include_content_type=False)
    files = {
        'file': (file_item['filename'], file_item['content'], file_item['mime_type']),
    }
    data = {'user': user_id}
    response = requests.post(
        api_endpoint, data=data, files=files, headers=headers, timeout=60
    )
    log.debug("Upload response %s (%d bytes)", response.status_code, len(response.text))
    if 200 <= response.status_code < 300:
        try:
            body = response.json()
        except ValueError:
            return None, 'Upload API returned invalid JSON'
        if not isinstance(body, dict):
            return None, f'Upload API returned non-object JSON ({type(body).__name__})'
        upload_file_id = str(body.get('id') or '').strip()
        if not upload_file_id:
            return None, 'Upload API response missing file id'
        return upload_file_id, None
    snippet = (response.text or '')[:200].replace('\n', ' ')
    return None, f'Upload failed: HTTP {response.status_code}' + (f': {snippet}' if snippet else '')


def _call_dify_chat(source, message, conversation_id, user_id, image_files=None):
    api_endpoint = f"{source['api_url']}{source.get('chat_endpoint', '/chat-messages')}"
    headers = _source_headers(source)
    payload = {
        'inputs': source.get('default_inputs', {}),
        'query': message,
        'response_mode': 'blocking',
        'conversation_id': sanitize_conversation_id(conversation_id),
        'user': user_id,
    }
    if image_files:
        payload['files'] = []
        for img in image_files:
            upload_file_id, upload_error = _upload_dify_file(source, user_id, img)
            if not upload_file_id:
                return None, upload_error
            payload['files'].append({
                'type': 'image',
                'transfer_method': 'local_file',
                'upload_file_id': upload_file_id,
            })
    log.info("Calling source[%s] chat: %s", source['id'], api_endpoint)
    return _request_json(api_endpoint, payload, headers)


def _call_dify_workflow(source, message, user_id):
    api_endpoint = f"{source['api_url']}{source.get('workflow_endpoint', '/workflows/run')}"
    headers = _source_headers(source)
    inputs = dict(source.get('default_inputs', {}))
    inputs.setdefault('query', message)
    inputs.setdefault('message', message)
    payload = {
        'inputs': inputs,
        'response_mode': 'blocking',
        'user': user_id,
    }
    log.info("Calling source[%s] workflow: %s", source['id'], api_endpoint)
    return _request_json(api_endpoint, payload, headers)


def _call_custom(source, message, conversation_id, user_id, image_data=None):
    endpoint = source.get('chat_endpoint', '/chat')
    api_endpoint = (
        endpoint if endpoint.startswith('http') else f"{source['api_url']}{endpoint}"
    )
    headers = _source_headers(source)
    payload = {
        'message': message,
        'conversation_id': conversation_id,
        'user_id': user_id,
        'files': image_data or [],
        'inputs': source.get('default_inputs', {}),
    }
    custom_payload = source.get('custom_payload')
    if isinstance(custom_payload, dict):
        payload.update(custom_payload)
    log.info("Calling source[%s] custom: %s", source['id'], api_endpoint)
    return _request_json(api_endpoint, payload, headers)


def _join_workflow_outputs(outputs) -> str:
    if not isinstance(outputs, dict):
        return ''
    parts: list[str] = []
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
