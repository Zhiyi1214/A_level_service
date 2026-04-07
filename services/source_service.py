from __future__ import annotations

import json
import logging
import os
import time

from config import settings

log = logging.getLogger(__name__)


class SourceService:
    """Manages the knowledge-source registry with hot-reload support."""

    def __init__(self):
        self._registry: dict[str, dict] = {}
        self._config_mtime: float | None = None
        self._last_check_time: float = 0.0
        self._check_interval: float = 5.0
        self._reload()  # 启动时加载；失败则保持空 registry

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, source_id: str) -> dict | None:
        sid = (source_id or '').strip()
        return self._registry.get(sid) if sid else None

    def public_list(self) -> list[dict]:
        return [_public_info(s) for s in self._registry.values()]

    @property
    def count(self) -> int:
        return len(self._registry)

    def maybe_reload(self):
        """Re-read sources.json when the file has been modified on disk."""
        now = time.time()
        if now - self._last_check_time < self._check_interval:
            return
        self._last_check_time = now
        mtime = self._mtime()
        if mtime is None:
            return
        if self._config_mtime is None or mtime != self._config_mtime:
            if self._reload():
                log.info("Reloaded sources from %s", settings.SOURCES_CONFIG_PATH)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _mtime(self) -> float | None:
        try:
            return settings.SOURCES_CONFIG_PATH.stat().st_mtime
        except OSError:
            return None

    def _reload(self) -> bool:
        """解析成功并写入内存时返回 True；任一步失败则保留原 registry 且返回 False。"""
        configured: list = []
        path = settings.SOURCES_CONFIG_PATH
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding='utf-8'))
                if isinstance(raw, list):
                    configured = raw
                elif isinstance(raw, dict) and isinstance(raw.get('sources'), list):
                    configured = raw['sources']
                else:
                    log.error(
                        "Invalid sources config shape (expected list or {sources: []}). "
                        "Keeping previous registry."
                    )
                    return False
            except Exception as exc:
                log.error(
                    "Failed to load sources config: %s. Keeping previous registry.",
                    exc,
                )
                return False

        loaded: dict[str, dict] = {}
        for item in configured:
            source = _normalize_source(item)
            if not source or not source.get('enabled', True):
                continue
            source['api_key'] = os.getenv(source['auth_ref'], '')
            loaded[source['id']] = source

        self._registry = loaded
        self._config_mtime = self._mtime()
        return True


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _public_info(source: dict) -> dict:
    return {
        'id': source['id'],
        'name': source['name'],
        'type': source['type'],
        'description': source.get('description', ''),
        'enabled': bool(source.get('enabled', True)),
    }


def _normalize_source(item) -> dict | None:
    if not isinstance(item, dict):
        return None
    source_id = str(item.get('id', '')).strip()
    if not source_id:
        return None

    source_type = str(item.get('type', 'dify_chat')).strip() or 'dify_chat'
    api_url = str(item.get('api_url') or item.get('base_url') or '').strip().rstrip('/')
    if not api_url:
        api_url = settings.DIFY_API_URL

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
        'default_inputs': (
            item.get('default_inputs')
            if isinstance(item.get('default_inputs'), dict)
            else {}
        ),
    }
    if item.get('custom_payload') and isinstance(item['custom_payload'], dict):
        source['custom_payload'] = item['custom_payload']
    if item.get('headers') and isinstance(item['headers'], dict):
        source['headers'] = item['headers']
    return source


# Singleton – created once at import time.
source_service = SourceService()
