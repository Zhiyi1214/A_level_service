"""对出站 http(s) URL 做主机解析后的 SSRF 缓解（配置型上游 api_url / endpoint）。"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlsplit

from config import settings

log = logging.getLogger(__name__)

_LINK_LOCAL_V4 = ipaddress.ip_network('169.254.0.0/16')
_LINK_LOCAL_V6 = ipaddress.ip_network('fe80::/10')


def upstream_http_url_blocked_reason(url: str) -> str | None:
    """
    若 URL 不应被服务端发起请求，返回简短英文/技术原因（供日志）；否则返回 None。

    策略：仅允许 http(s)；解析主机名得到全部 A/AAAA 后，若任一地址命中禁止规则则拒绝。
    """
    if not url or not isinstance(url, str):
        return 'empty url'
    raw = url.strip()
    if not raw or len(raw) > 8192:
        return 'url too long or empty'

    parts = urlsplit(raw)
    if parts.scheme not in ('http', 'https'):
        return f'scheme not allowed: {parts.scheme!r}'

    host = parts.hostname
    if not host:
        return 'missing host'

    port = parts.port
    if port is None:
        port = 443 if parts.scheme == 'https' else 80

    try:
        infos = socket.getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except OSError as exc:
        return f'dns resolution failed: {exc!s}'

    if not infos:
        return 'no addresses resolved'

    for _fam, _type, _proto, _canon, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return f'invalid ip from dns: {ip_str!r}'

        if not settings.UPSTREAM_HTTP_ALLOW_LOOPBACK and ip.is_loopback:
            return f'loopback not allowed: {ip}'

        if ip.version == 4 and ip in _LINK_LOCAL_V4:
            return f'ipv4 link-local not allowed: {ip}'

        if ip.version == 6 and ip in _LINK_LOCAL_V6:
            return f'ipv6 link-local not allowed: {ip}'

        if ip.is_multicast:
            return f'multicast not allowed: {ip}'

        if ip.version == 4 and ip.is_reserved:
            return f'reserved ipv4 not allowed: {ip}'

        if ip.version == 6 and ip.is_reserved:
            return f'reserved ipv6 not allowed: {ip}'

        if settings.UPSTREAM_HTTP_BLOCK_PRIVATE_NETWORKS and ip.is_private:
            return f'private network not allowed: {ip}'

    return None
