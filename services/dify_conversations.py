"""Dify GET /conversations：按会话 id 拉取 name（控制台「标题」）。"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from services.chat_service import BLOCK_TIMEOUT
from services.http_url_guard import upstream_http_url_blocked_reason

log = logging.getLogger(__name__)


def _source_headers(source: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    api_key = source.get('api_key', '')
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    extra = source.get('headers', {})
    if isinstance(extra, dict):
        for k, v in extra.items():
            if isinstance(k, str) and isinstance(v, str):
                headers[k] = v
    return headers


def fetch_conversation_names_map(
    source: dict[str, Any],
    user_id: str,
    wanted_upstream_ids: set[str],
) -> dict[str, str]:
    """分页请求 Dify 会话列表，直到找全 *wanted_upstream_ids* 或无更多页。"""
    wanted = {x.strip() for x in wanted_upstream_ids if (x or '').strip()}
    if not wanted:
        return {}

    api_url = str(source.get('api_url') or '').strip().rstrip('/')
    if not api_url:
        return {}

    endpoint = f'{api_url}/conversations'
    if (ssrf := upstream_http_url_blocked_reason(endpoint)):
        log.warning('Blocked upstream URL (ssrf): %s — %s', endpoint, ssrf)
        return {}
    headers = _source_headers(source)
    found: dict[str, str] = {}
    last_id: str | None = None

    try:
        with httpx.Client(timeout=BLOCK_TIMEOUT) as client:
            while wanted - found.keys():
                params: dict[str, str | int] = {
                    'user': user_id,
                    'limit': 100,
                }
                if last_id:
                    params['last_id'] = last_id
                response = client.get(endpoint, params=params, headers=headers)
                if response.status_code < 200 or response.status_code >= 300:
                    log.debug(
                        'Dify list conversations HTTP %s: %s',
                        response.status_code,
                        (response.text or '')[:200],
                    )
                    break
                try:
                    body = response.json()
                except ValueError:
                    log.debug('Dify list conversations: invalid JSON')
                    break
                if not isinstance(body, dict):
                    break
                items = body.get('data')
                if not isinstance(items, list):
                    break
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    iid = str(item.get('id') or '').strip()
                    if iid in wanted and iid not in found:
                        found[iid] = str(item.get('name') or '').strip()
                if not body.get('has_more'):
                    break
                if not items:
                    break
                tail = str(items[-1].get('id') or '').strip()
                if not tail or tail == last_id:
                    break
                last_id = tail
    except httpx.HTTPError as exc:
        log.debug('Dify list conversations request failed: %s', exc)

    return found


def hydrate_dify_titles(summaries_by_id: dict[str, dict], user_id: str) -> None:
    """根据 Dify 会话 name 更新 summaries 中的 dify_conversation_name，并写入数据库。"""
    from storage import store
    from services.source_service import source_service

    by_source: dict[str, list[tuple[str, str]]] = {}
    for cid, s in summaries_by_id.items():
        up = (s.get('upstream_conversation_id') or '').strip()
        sid = (s.get('source_id') or '').strip()
        if not up or not sid:
            continue
        src = source_service.get(sid)
        if not src or src.get('type') != 'dify_chat':
            continue
        if not (str(src.get('api_key') or '')).strip():
            continue
        by_source.setdefault(sid, []).append((cid, up))

    for sid, pairs in by_source.items():
        src = source_service.get(sid)
        if not src:
            continue
        wanted = {up for _, up in pairs}
        try:
            name_map = fetch_conversation_names_map(src, user_id, wanted)
        except Exception:
            log.exception('Dify hydrate titles failed for source %s', sid)
            continue
        for local_cid, up in pairs:
            name = (name_map.get(up) or '').strip()
            if not name:
                continue
            prev = (summaries_by_id[local_cid].get('dify_conversation_name') or '').strip()
            if name != prev:
                store.update_dify_conversation_name(local_cid, name)
            summaries_by_id[local_cid]['dify_conversation_name'] = name
