from __future__ import annotations

# Dify 使用 POST + SSE；用 httpx-sse 解析事件流（多行 data、注释等）。
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Optional

from storage import store

import httpx
from httpx_sse import SSEError, connect_sse

from config import settings
from services import image_service
from services.http_url_guard import upstream_http_url_blocked_reason

log = logging.getLogger(__name__)


def client_safe_error(
    production_message: str,
    *,
    development_detail: str | None = None,
) -> str:
    """非 development 不向客户端返回堆栈、URL、上游响应片段等敏感信息。"""
    if settings.APP_ENV == 'development' and development_detail:
        return development_detail[:500]
    return production_message

_UUID_CONV_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)

STREAM_TIMEOUT = httpx.Timeout(
    connect=settings.HTTPX_CONNECT_TIMEOUT,
    read=settings.HTTPX_STREAM_READ_TIMEOUT,
    write=settings.HTTPX_STREAM_WRITE_TIMEOUT,
    pool=settings.HTTPX_POOL_TIMEOUT,
)
BLOCK_TIMEOUT = httpx.Timeout(
    connect=settings.HTTPX_CONNECT_TIMEOUT,
    read=settings.HTTPX_BLOCK_READ_TIMEOUT,
    write=settings.HTTPX_STREAM_WRITE_TIMEOUT,
    pool=settings.HTTPX_POOL_TIMEOUT,
)

# ======================================================================
# Request / response text normalization
# ======================================================================


def normalize_inbound_user_plaintext(text: str) -> str:
    """请求里整段被多包一层 JSON 字符串引号时去掉（否则落库后前端会看到两侧多出的 \\\"）。"""
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


def normalize_assistant_plaintext(text: str) -> str:
    """上游偶发返回整段 JSON 字符串（带首尾引号）；用 json.loads 还原，不用正则修补正文里的 \\n（避免误伤 LaTeX）。"""
    if not isinstance(text, str) or not text:
        return text
    s = text.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        try:
            parsed = json.loads(s)
            if isinstance(parsed, str):
                return parsed
        except json.JSONDecodeError:
            pass
    return text


def accumulate_stream_chunks(parts: list[str], chunk: str) -> None:
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


# ======================================================================
# Response parsing (blocking JSON)
# ======================================================================


def _non_empty_str(val: Any) -> str | None:
    if isinstance(val, str) and val.strip():
        return val
    return None


def extract_answer(resp) -> str:
    """Extract the assistant reply from a Dify blocking-mode response."""
    if not resp:
        return ''
    if not isinstance(resp, dict):
        return str(resp)

    if (s := _non_empty_str(resp.get('answer'))) is not None:
        return s
    a = resp.get('answer')
    if a is not None and not isinstance(a, str):
        return str(a)

    data = resp.get('data')
    if isinstance(data, dict):
        if (inner := _non_empty_str(data.get('answer'))) is not None:
            return inner
        if (merged := _join_workflow_outputs(data.get('outputs'))):
            return merged
        msg = data.get('message')
        if isinstance(msg, dict):
            if (ma := _non_empty_str(msg.get('answer'))) is not None:
                return ma

    for key in ('output', 'text', 'result'):
        if (v := _non_empty_str(resp.get(key))) is not None:
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
    *,
    dify_file_cache_get: Optional[Callable[[str], Optional[str]]] = None,
    dify_file_cache_put: Optional[Callable[[str, str], None]] = None,
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
        yield {
            'kind': 'error',
            'message': client_safe_error(
                '未配置该知识库所需的服务端密钥，请联系管理员。',
                development_detail=f"Missing API key env: {source.get('auth_ref')}",
            ),
        }
        return

    source_type = source.get('type')
    try:
        if source_type == 'dify_chat':
            # image_files 须为本轮请求的新附件；勿传入历史会话缓存，以免重复上传污染上下文
            yield from _stream_dify_chat(
                source,
                message,
                conversation_id,
                user_id,
                image_files,
                dify_file_cache_get=dify_file_cache_get,
                dify_file_cache_put=dify_file_cache_put,
            )
        elif source_type == 'dify_workflow':
            yield from _stream_dify_workflow(source, message, user_id)
        elif source_type == 'custom_api':
            yield from _stream_custom_api(
                source,
                message,
                conversation_id,
                user_id,
                _custom_api_image_payload(image_data),
            )
        else:
            yield {
                'kind': 'error',
                'message': client_safe_error(
                    '不支持的知识库类型。',
                    development_detail=f'Unsupported source type: {source_type}',
                ),
            }
    except httpx.TimeoutException:
        yield {
            'kind': 'error',
            'message': client_safe_error(
                '请求上游超时，请稍后重试。',
                development_detail='Request timed out. Check if upstream is reachable.',
            ),
        }
    except httpx.ConnectError as exc:
        yield {
            'kind': 'error',
            'message': client_safe_error(
                '无法连接上游服务，请稍后重试。',
                development_detail=f'Upstream connect failed: {exc!s}',
            ),
        }
    except httpx.RemoteProtocolError as exc:
        yield {
            'kind': 'error',
            'message': client_safe_error(
                '上游连接中断，请稍后再试。',
                development_detail=f'Upstream closed stream: {exc!s}',
            ),
        }
    except httpx.HTTPError as exc:
        yield {
            'kind': 'error',
            'message': client_safe_error(
                '与上游通信失败，请稍后重试。',
                development_detail=str(exc),
            ),
        }
    except Exception as exc:
        log.exception("iter_source_api_stream failed")
        yield {
            'kind': 'error',
            'message': client_safe_error(
                '服务暂时不可用，请稍后重试。',
                development_detail=str(exc),
            ),
        }


def iter_chat_sse_response(
    *,
    conversation_id: str,
    locked_source_id: str,
    source: dict[str, Any],
    upstream_cid: str,
    user_id: str | None,
    user_message: str,
    message_content: str | list[Any],
    image_data: list[Any],
    image_files: list[Any],
) -> Iterator[str]:
    """产生 SSE `data: ...\\n\\n` 行；落库与 upstream id 更新在流正常结束后执行。"""

    def sse_pack(obj: dict[str, Any]) -> str:
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    def merge_stream_meta(
        upstream: str, mid: Any, usg: dict[str, Any], ev: dict[str, Any]
    ) -> tuple[str, Any, dict[str, Any]]:
        c = (ev.get('conversation_id') or '').strip()
        if c:
            upstream = c
        if ev.get('message_id') is not None:
            mid = ev.get('message_id')
        u = ev.get('usage')
        if isinstance(u, dict) and u:
            usg = u
        return upstream, mid, usg

    acc: list[str] = []
    stream_upstream = upstream_cid
    msg_id = None
    usage: dict[str, Any] = {}
    dify_get: Callable[[str], Optional[str]] | None = None
    dify_put: Callable[[str, str], None] | None = None
    if source.get('type') == 'dify_chat':
        dify_cache = store.get_dify_file_cache(conversation_id)

        def dify_get(h: str) -> Optional[str]:
            return dify_cache.get(h)

        def dify_put(h: str, fid: str) -> None:
            dify_cache[h] = fid
            store.put_dify_file_cache_entry(conversation_id, h, fid)

    try:
        for ev in iter_source_api_stream(
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
                    accumulate_stream_chunks(acc, t)
                    yield sse_pack({'event': 'delta', 'text': t})
            elif k in ('meta', 'finished'):
                stream_upstream, msg_id, usage = merge_stream_meta(
                    stream_upstream, msg_id, usage, ev
                )
            elif k == 'error':
                yield sse_pack({
                    'event': 'error',
                    'detail': ev.get('message') or 'Unknown error',
                    'source_id': locked_source_id,
                })
                return

        answer_text = normalize_assistant_plaintext(''.join(acc))
        if stream_upstream:
            store.update_upstream_id(conversation_id, stream_upstream)

        now = datetime.now(timezone.utc).isoformat()
        store.append_message(conversation_id, 'user', message_content, now)
        store.append_message(conversation_id, 'assistant', answer_text, now)

        yield sse_pack({
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
        yield sse_pack({'event': 'error', 'detail': 'Internal server error'})


# ======================================================================
# Internal helpers
# ======================================================================


def _custom_api_image_payload(image_data) -> list | None:
    """浏览器用 /api/media；custom_api 上游需直接拉对象时改为内网预签名 URL。"""
    if not image_data:
        return image_data
    out: list = []
    for seg in image_data:
        if not isinstance(seg, dict):
            out.append(seg)
            continue
        if seg.get('type') != 'image':
            out.append(seg)
            continue
        item = dict(seg)
        key = item.get('object_key')
        if isinstance(key, str) and key and image_service.is_s3_configured():
            url = image_service.presigned_get_url_internal(key)
            if url:
                item['url'] = url
        out.append(item)
    return out


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
    if (ssrf := upstream_http_url_blocked_reason(api_endpoint)):
        log.warning('Blocked upstream URL (ssrf): %s — %s', api_endpoint, ssrf)
        return None, client_safe_error(
            '上游地址未通过安全校验，请联系管理员检查知识库配置。',
            development_detail=ssrf,
        )
    try:
        with httpx.Client(timeout=BLOCK_TIMEOUT) as client:
            response = client.post(api_endpoint, json=payload, headers=headers)
    except httpx.TimeoutException:
        return None, client_safe_error(
            '请求上游超时，请稍后重试。',
            development_detail='Request timed out (60s). Check if upstream is running.',
        )
    except httpx.HTTPError as exc:
        return None, client_safe_error(
            '与上游通信失败，请稍后重试。',
            development_detail=str(exc)[:240],
        )

    log.debug("Upstream response %s (%d bytes)", response.status_code, len(response.text))
    if 200 <= response.status_code < 300:
        try:
            body = response.json()
            if not isinstance(body, dict):
                return None, client_safe_error(
                    '上游返回数据格式异常。',
                    development_detail=f'Upstream returned non-object JSON ({type(body).__name__})',
                )
            return body, None
        except ValueError:
            return None, client_safe_error(
                '上游返回无效 JSON。',
                development_detail='Upstream returned invalid JSON',
            )
    snippet = (response.text or '')[:200].replace('\n', ' ')
    dev = f'HTTP {response.status_code}' + (f': {snippet}' if snippet else '')
    return None, client_safe_error(
        f'上游服务返回异常（HTTP {response.status_code}）。',
        development_detail=dev,
    )


def _upload_dify_file(source, user_id, file_item):
    api_endpoint = f"{source['api_url']}/files/upload"
    if (ssrf := upstream_http_url_blocked_reason(api_endpoint)):
        log.warning('Blocked upstream URL (ssrf): %s — %s', api_endpoint, ssrf)
        return None, client_safe_error(
            '上游地址未通过安全校验，请联系管理员检查知识库配置。',
            development_detail=ssrf,
        )
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
        return None, client_safe_error(
            '上传超时，请稍后重试。',
            development_detail='Upload timed out (60s).',
        )
    except httpx.HTTPError as exc:
        return None, client_safe_error(
            '上传请求失败，请稍后重试。',
            development_detail=str(exc)[:240],
        )

    log.debug("Upload response %s (%d bytes)", response.status_code, len(response.text))
    if 200 <= response.status_code < 300:
        try:
            body = response.json()
        except ValueError:
            return None, client_safe_error(
                '上传接口返回无效 JSON。',
                development_detail='Upload API returned invalid JSON',
            )
        if not isinstance(body, dict):
            return None, client_safe_error(
                '上传接口返回格式异常。',
                development_detail=f'Upload API returned non-object JSON ({type(body).__name__})',
            )
        upload_file_id = str(body.get('id') or '').strip()
        if not upload_file_id:
            return None, client_safe_error(
                '上传接口未返回文件 id。',
                development_detail='Upload API response missing file id',
            )
        return upload_file_id, None
    snippet = (response.text or '')[:200].replace('\n', ' ')
    dev = f'Upload failed: HTTP {response.status_code}' + (f': {snippet}' if snippet else '')
    return None, client_safe_error(
        f'上传失败（HTTP {response.status_code}）。',
        development_detail=dev,
    )


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
        yield {
            'kind': 'error',
            'message': client_safe_error(
                '上游返回错误，请稍后再试。',
                development_detail=str(msg),
            ),
        }
        return

    if event == 'ping':
        return

    # 其它事件：尝试抽取 conversation_id
    meta = _emit_meta_from_obj(obj)
    if meta:
        yield meta


def _execute_dify_sse_stream(
    source: dict[str, Any],
    api_endpoint: str,
    payload: dict[str, Any],
    *,
    stream_label: str,
) -> Iterator[dict[str, Any]]:
    headers = _source_headers(source)
    log.info("Streaming source[%s] %s: %s", source['id'], stream_label, api_endpoint)
    if (ssrf := upstream_http_url_blocked_reason(api_endpoint)):
        log.warning('Blocked upstream URL (ssrf): %s — %s', api_endpoint, ssrf)
        yield {
            'kind': 'error',
            'message': client_safe_error(
                '上游地址未通过安全校验，请联系管理员检查知识库配置。',
                development_detail=ssrf,
            ),
        }
        return
    workflow_acc: list[str] = []
    text_channel_lock: dict[str, Any] = {'channel': None}
    meta_cid = ''
    message_id = None
    usage: dict = {}

    try:
        with httpx.Client(timeout=STREAM_TIMEOUT) as client:
            with connect_sse(
                client,
                'POST',
                api_endpoint,
                json=payload,
                headers=headers,
            ) as event_source:
                response = event_source.response
                if response.status_code < 200 or response.status_code >= 300:
                    body = response.read().decode('utf-8', errors='replace')[:500]
                    dev_msg = f'HTTP {response.status_code}: {body}'.strip()[:240]
                    yield {
                        'kind': 'error',
                        'message': client_safe_error(
                            f'上游服务返回异常（HTTP {response.status_code}）。',
                            development_detail=dev_msg,
                        ),
                    }
                    return
                try:
                    for sse in event_source.iter_sse():
                        raw = (sse.data or '').strip()
                        if raw == '[DONE]':
                            continue
                        try:
                            obj = json.loads(raw)
                        except json.JSONDecodeError:
                            log.debug("Skip non-JSON SSE payload: %s...", raw[:80])
                            continue
                        if not isinstance(obj, dict):
                            continue
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
                except SSEError as exc:
                    yield {
                        'kind': 'error',
                        'message': client_safe_error(
                            '上游响应不是有效的 SSE（Content-Type 或帧格式异常）。',
                            development_detail=str(exc),
                        ),
                    }
                    return
    except httpx.RemoteProtocolError as exc:
        yield {
            'kind': 'error',
            'message': client_safe_error(
                '上游连接中断，请稍后再试。',
                development_detail=f'Upstream closed stream: {exc!s}',
            ),
        }
        return

    yield {
        'kind': 'finished',
        'conversation_id': meta_cid,
        'message_id': message_id,
        'usage': usage,
    }


def _stream_dify_chat(
    source,
    message,
    conversation_id,
    user_id,
    image_files=None,
    *,
    dify_file_cache_get: Optional[Callable[[str], Optional[str]]] = None,
    dify_file_cache_put: Optional[Callable[[str, str], None]] = None,
):
    """Streaming Dify chat. *image_files* must be files attached in this turn only."""
    api_endpoint = f"{source['api_url']}{source.get('chat_endpoint', '/chat-messages')}"
    payload = {
        'inputs': source.get('default_inputs', {}),
        'query': message,
        'response_mode': 'streaming',
        'conversation_id': sanitize_conversation_id(conversation_id),
        'user': user_id,
    }
    if image_files:
        payload['files'] = []
        for img in image_files:
            upload_file_id: str | None = None
            upload_error: str | None = None
            sha = (img.get('content_sha256') or '').strip() if isinstance(img, dict) else ''
            if sha and dify_file_cache_get is not None:
                try:
                    cached = dify_file_cache_get(sha)
                except Exception:
                    log.exception("dify_file_cache_get failed")
                    cached = None
                if isinstance(cached, str) and cached.strip():
                    upload_file_id = cached.strip()
            if not upload_file_id:
                upload_file_id, upload_error = _upload_dify_file(source, user_id, img)
                if upload_file_id and sha and dify_file_cache_put is not None:
                    try:
                        dify_file_cache_put(sha, upload_file_id)
                    except Exception:
                        log.exception("dify_file_cache_put failed")
            if not upload_file_id:
                yield {
                    'kind': 'error',
                    'message': client_safe_error(
                        '图片上传失败，请稍后重试。',
                        development_detail=upload_error or 'upload failed',
                    ),
                }
                return
            payload['files'].append({
                'type': 'image',
                'transfer_method': 'local_file',
                'upload_file_id': upload_file_id,
            })

    yield from _execute_dify_sse_stream(
        source, api_endpoint, payload, stream_label='chat'
    )


def _stream_dify_workflow(source, message, user_id):
    api_endpoint = f"{source['api_url']}{source.get('workflow_endpoint', '/workflows/run')}"
    inputs = dict(source.get('default_inputs', {}))
    inputs.setdefault('query', message)
    inputs.setdefault('message', message)
    payload = {
        'inputs': inputs,
        'response_mode': 'streaming',
        'user': user_id,
    }
    yield from _execute_dify_sse_stream(
        source, api_endpoint, payload, stream_label='workflow'
    )


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
