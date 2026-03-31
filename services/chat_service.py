from __future__ import annotations

# Dify 使用 POST + SSE；此处用 httpx.iter_lines 解析「data:」行。
import json
import logging
import re
from typing import Any, Iterator

import httpx

log = logging.getLogger(__name__)

_UUID_CONV_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)

# 上游流式连接：读超时放宽，避免长回复被切断；连接/写入仍有限制
STREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=10.0)
BLOCK_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)

# ======================================================================
# Response parsing (blocking JSON)
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
# Streaming: yields dict events for routes/chat SSE
# ======================================================================

# Event kinds: delta, meta, finished, error


def iter_source_api_stream(
    source,
    message,
    conversation_id,
    user_id,
    image_data=None,
    image_files=None,
) -> Iterator[dict[str, Any]]:
    """
    Stream upstream tokens / chunks as {'kind': 'delta', 'text': str}.
    Terminal: {'kind': 'finished', ...} or {'kind': 'error', 'message': str}.

    meta events {'kind': 'meta', 'conversation_id', 'message_id', ...} may appear
    when the upstream includes them in SSE payloads.

    For dify_chat, *image_files* must be the current request's uploads only (not a
    multi-turn cache), so Dify does not re-upload past images each round.
    """
    if not source:
        yield {'kind': 'error', 'message': 'source is required'}
        return
    if not source.get('api_key') and source.get('type') in {'dify_chat', 'dify_workflow'}:
        yield {'kind': 'error', 'message': f"Missing API key env: {source.get('auth_ref')}"}
        return

    source_type = source.get('type')
    try:
        if source_type == 'dify_chat':
            # image_files 须为本轮请求的新附件；勿传入历史会话缓存，以免重复上传污染上下文
            yield from _stream_dify_chat(
                source, message, conversation_id, user_id, image_files
            )
        elif source_type == 'dify_workflow':
            yield from _stream_dify_workflow(source, message, user_id)
        elif source_type == 'custom_api':
            yield from _stream_custom_api(
                source, message, conversation_id, user_id, image_data
            )
        else:
            yield {'kind': 'error', 'message': f'Unsupported source type: {source_type}'}
    except httpx.TimeoutException:
        yield {'kind': 'error', 'message': 'Request timed out. Check if upstream is reachable.'}
    except httpx.ConnectError as exc:
        yield {'kind': 'error', 'message': f'Upstream connect failed: {exc!s}'[:240]}
    except httpx.RemoteProtocolError as exc:
        yield {'kind': 'error', 'message': f'Upstream closed stream: {exc!s}'[:240]}
    except httpx.HTTPError as exc:
        yield {'kind': 'error', 'message': str(exc)[:240]}
    except Exception as exc:
        log.exception("iter_source_api_stream failed")
        yield {'kind': 'error', 'message': str(exc)[:240]}


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


def _request_json_httpx(api_endpoint, payload, headers):
    try:
        with httpx.Client(timeout=BLOCK_TIMEOUT) as client:
            response = client.post(api_endpoint, json=payload, headers=headers)
    except httpx.TimeoutException:
        return None, 'Request timed out (60s). Check if upstream is running.'
    except httpx.HTTPError as exc:
        return None, str(exc)[:240]

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
    try:
        with httpx.Client(timeout=BLOCK_TIMEOUT) as client:
            response = client.post(
                api_endpoint,
                data={'user': user_id},
                files={
                    'file': (
                        file_item['filename'],
                        file_item['content'],
                        file_item['mime_type'],
                    ),
                },
                headers=headers,
            )
    except httpx.TimeoutException:
        return None, 'Upload timed out (60s).'
    except httpx.HTTPError as exc:
        return None, str(exc)[:240]

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


def _iter_sse_data_objects(response: httpx.Response) -> Iterator[dict[str, Any]]:
    """Parse Dify-style SSE lines (data: {...}) from an httpx streaming response."""
    try:
        for line in response.iter_lines():
            if line is None:
                continue
            s = line.strip()
            if not s or s.startswith(':'):
                continue
            if not s.startswith('data: '):
                continue
            raw = s[6:].strip()
            if raw == '[DONE]':
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                log.debug("Skip non-JSON SSE payload: %s...", raw[:80])
                continue
            if isinstance(obj, dict):
                yield obj
    except httpx.RemoteProtocolError:
        raise
    except Exception:
        log.exception("SSE parse loop failed")
        raise


def _emit_meta_from_obj(obj: dict[str, Any]) -> dict[str, Any] | None:
    cid = (obj.get('conversation_id') or '').strip()
    mid = obj.get('message_id')
    usage = obj.get('usage')
    if not cid and mid is None and not isinstance(usage, dict):
        data = obj.get('data')
        if isinstance(data, dict):
            cid = (data.get('conversation_id') or '').strip()
    if cid or mid is not None or isinstance(usage, dict):
        return {
            'kind': 'meta',
            'conversation_id': cid,
            'message_id': mid,
            'usage': usage if isinstance(usage, dict) else {},
        }
    return None


def _handle_dify_sse_obj(
    obj: dict[str, Any],
    *,
    workflow_text_acc: list[str],
    text_channel_lock: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    event = (obj.get('event') or '').strip()

    if event in ('message', 'agent_message'):
        ans = obj.get('answer')
        if isinstance(ans, str) and ans:
            # Agent 等场景下 Dify 可能对同一段正文同时发 message 与 agent_message，拼接会翻倍
            if text_channel_lock is not None:
                locked = text_channel_lock.get('channel')
                if locked is None:
                    text_channel_lock['channel'] = event
                elif event != locked:
                    ans = ''
            if ans:
                yield {'kind': 'delta', 'text': ans}
        meta = _emit_meta_from_obj(obj)
        if meta:
            yield meta
        return

    if event == 'text_chunk':
        data = obj.get('data')
        if isinstance(data, dict):
            t = data.get('text')
            if isinstance(t, str) and t:
                workflow_text_acc.append(t)
                yield {'kind': 'delta', 'text': t}
        return

    if event == 'message_end':
        meta = _emit_meta_from_obj(obj)
        if meta:
            yield meta
        md = obj.get('metadata')
        if isinstance(md, dict) and isinstance(md.get('usage'), dict):
            yield {
                'kind': 'meta',
                'conversation_id': (obj.get('conversation_id') or '').strip(),
                'message_id': obj.get('message_id'),
                'usage': md.get('usage') or {},
            }
        return

    if event == 'workflow_finished':
        data = obj.get('data') if isinstance(obj.get('data'), dict) else {}
        outputs = data.get('outputs') if isinstance(data, dict) else None
        merged = _join_workflow_outputs(outputs)
        if merged and not workflow_text_acc:
            yield {'kind': 'delta', 'text': merged}
        meta = _emit_meta_from_obj(obj)
        if meta:
            yield meta
        return

    if event == 'error':
        msg = obj.get('message') or obj.get('code') or 'upstream error'
        yield {'kind': 'error', 'message': str(msg)[:500]}
        return

    if event == 'ping':
        return

    # 其它事件：尝试抽取 conversation_id
    meta = _emit_meta_from_obj(obj)
    if meta:
        yield meta


def _stream_dify_chat(source, message, conversation_id, user_id, image_files=None):
    """Streaming Dify chat. *image_files* must be files attached in this turn only."""
    api_endpoint = f"{source['api_url']}{source.get('chat_endpoint', '/chat-messages')}"
    headers = _source_headers(source)
    payload = {
        'inputs': source.get('default_inputs', {}),
        'query': message,
        'response_mode': 'streaming',
        'conversation_id': sanitize_conversation_id(conversation_id),
        'user': user_id,
    }
    # 仅上传本轮列表中的图片；调用方不得传入跨轮缓存
    if image_files:
        payload['files'] = []
        for img in image_files:
            upload_file_id, upload_error = _upload_dify_file(source, user_id, img)
            if not upload_file_id:
                yield {'kind': 'error', 'message': upload_error or 'upload failed'}
                return
            payload['files'].append({
                'type': 'image',
                'transfer_method': 'local_file',
                'upload_file_id': upload_file_id,
            })

    log.info("Streaming source[%s] chat: %s", source['id'], api_endpoint)
    workflow_acc: list[str] = []
    text_channel_lock: dict[str, Any] = {'channel': None}
    meta_cid = ''
    message_id = None
    usage: dict = {}

    try:
        with httpx.Client(timeout=STREAM_TIMEOUT) as client:
            with client.stream('POST', api_endpoint, json=payload, headers=headers) as response:
                if response.status_code < 200 or response.status_code >= 300:
                    body = response.read().decode('utf-8', errors='replace')[:500]
                    yield {
                        'kind': 'error',
                        'message': f'HTTP {response.status_code}: {body}'.strip()[:240],
                    }
                    return
                for obj in _iter_sse_data_objects(response):
                    for ev in _handle_dify_sse_obj(
                        obj,
                        workflow_text_acc=workflow_acc,
                        text_channel_lock=text_channel_lock,
                    ):
                        if ev.get('kind') == 'meta':
                            c = (ev.get('conversation_id') or '').strip()
                            if c:
                                meta_cid = c
                            if ev.get('message_id') is not None:
                                message_id = ev.get('message_id')
                            u = ev.get('usage')
                            if isinstance(u, dict) and u:
                                usage = u
                        yield ev
                        if ev.get('kind') == 'error':
                            return
    except httpx.RemoteProtocolError as exc:
        yield {'kind': 'error', 'message': f'Upstream closed stream: {exc!s}'[:240]}
        return

    yield {
        'kind': 'finished',
        'conversation_id': meta_cid,
        'message_id': message_id,
        'usage': usage,
    }


def _stream_dify_workflow(source, message, user_id):
    api_endpoint = f"{source['api_url']}{source.get('workflow_endpoint', '/workflows/run')}"
    headers = _source_headers(source)
    inputs = dict(source.get('default_inputs', {}))
    inputs.setdefault('query', message)
    inputs.setdefault('message', message)
    payload = {
        'inputs': inputs,
        'response_mode': 'streaming',
        'user': user_id,
    }
    log.info("Streaming source[%s] workflow: %s", source['id'], api_endpoint)
    workflow_acc: list[str] = []
    text_channel_lock: dict[str, Any] = {'channel': None}
    meta_cid = ''
    message_id = None
    usage: dict = {}

    try:
        with httpx.Client(timeout=STREAM_TIMEOUT) as client:
            with client.stream('POST', api_endpoint, json=payload, headers=headers) as response:
                if response.status_code < 200 or response.status_code >= 300:
                    body = response.read().decode('utf-8', errors='replace')[:500]
                    yield {
                        'kind': 'error',
                        'message': f'HTTP {response.status_code}: {body}'.strip()[:240],
                    }
                    return
                for obj in _iter_sse_data_objects(response):
                    for ev in _handle_dify_sse_obj(
                        obj,
                        workflow_text_acc=workflow_acc,
                        text_channel_lock=text_channel_lock,
                    ):
                        if ev.get('kind') == 'meta':
                            c = (ev.get('conversation_id') or '').strip()
                            if c:
                                meta_cid = c
                            if ev.get('message_id') is not None:
                                message_id = ev.get('message_id')
                            u = ev.get('usage')
                            if isinstance(u, dict) and u:
                                usage = u
                        yield ev
                        if ev.get('kind') == 'error':
                            return
    except httpx.RemoteProtocolError as exc:
        yield {'kind': 'error', 'message': f'Upstream closed stream: {exc!s}'[:240]}
        return

    yield {
        'kind': 'finished',
        'conversation_id': meta_cid,
        'message_id': message_id,
        'usage': usage,
    }


def _stream_custom_api(source, message, conversation_id, user_id, image_data=None):
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
    log.info("Calling source[%s] custom (blocking): %s", source['id'], api_endpoint)
    body, err = _request_json_httpx(api_endpoint, payload, headers)
    if err:
        yield {'kind': 'error', 'message': err}
        return
    answer = extract_answer(body or {})
    if answer:
        yield {'kind': 'delta', 'text': answer}
    yield {
        'kind': 'finished',
        'conversation_id': extract_conversation_id(body or {}),
        'message_id': (body or {}).get('message_id'),
        'usage': (body or {}).get('usage') if isinstance((body or {}).get('usage'), dict) else {},
    }


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
