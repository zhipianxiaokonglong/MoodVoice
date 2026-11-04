"""URL validation for user-supplied TTS endpoints.

User connections can point anywhere, so they get a stricter policy than the
built-in providers: no credentials in URL, no fragments, no cloud metadata,
and remote endpoints must use HTTPS unless explicitly allowed.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

from ..errors import ConnectionFailure

_MAX_URL = 2048

_BLOCKED_HOSTS = {
    "169.254.169.254",
    "metadata.google.internal",
    "metadata",
}


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_private_or_link_local(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_link_local


def validate_base_url(raw: str, *, allow_insecure_http: bool = False) -> str:
    value = raw.strip()
    if not value:
        raise ConnectionFailure("URL 不能为空", code="invalid_url", stage="url")
    if len(value) > _MAX_URL:
        raise ConnectionFailure("URL 过长", code="invalid_url", stage="url")
    if any(ch in value for ch in ("\n", "\r", "\t", " ")):
        raise ConnectionFailure("URL 不能包含空白字符", code="invalid_url", stage="url")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"}:
        raise ConnectionFailure("URL 必须是 http 或 https", code="invalid_url", stage="url")
    if parts.username or parts.password:
        raise ConnectionFailure(
            "URL 不能包含用户名或密码，请把密钥填在 API Key 字段",
            code="invalid_url",
            stage="url",
        )
    if parts.fragment:
        raise ConnectionFailure("URL 不能包含 #fragment", code="invalid_url", stage="url")
    query = parts.query.lower()
    if any(
        token in query
        for token in ("api_key=", "apikey=", "access_token=", "subscription-key=", "token=")
    ):
        raise ConnectionFailure(
            "URL 查询参数中不能包含密钥或令牌，请使用 API Key 字段",
            code="invalid_url",
            stage="url",
        )
    host = (parts.hostname or "").strip().lower()
    if not host:
        raise ConnectionFailure("URL 缺少主机名", code="invalid_url", stage="url")
    if host in _BLOCKED_HOSTS:
        raise ConnectionFailure("禁止访问云元数据地址", code="blocked_url", stage="url")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise ConnectionFailure("URL 端口无效", code="invalid_url", stage="url") from exc

    # Resolve once for metadata/link-local checks. A hostname can resolve to
    # loopback, which is fine; link-local metadata is never fine.
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        infos = []
    loopback_host = _is_loopback_host(host)
    resolved_private = False
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if str(ip) in _BLOCKED_HOSTS:
            raise ConnectionFailure("禁止访问云元数据地址", code="blocked_url", stage="url")
        if ip.is_loopback and not loopback_host:
            raise ConnectionFailure(
                "远程域名解析到了本机地址，已拒绝（可能是 DNS rebinding）",
                code="blocked_loopback_resolution",
                stage="url",
            )
        if (ip.is_private or ip.is_link_local) and not loopback_host:
            resolved_private = True
            if not allow_insecure_http:
                raise ConnectionFailure(
                    "目标解析到内网/链路本地地址；如确需访问局域网服务，"
                    "请勾选“允许局域网 HTTP（仅当你信任该服务）”",
                    code="blocked_private_network",
                    stage="url",
                )

    if parts.scheme == "http" and not loopback_host:
        private_target = resolved_private or _is_private_or_link_local(host)
        if not (allow_insecure_http and private_target):
            raise ConnectionFailure(
                "远程 API 必须使用 HTTPS；局域网 HTTP 需勾选“允许局域网 HTTP（仅当你信任该服务）”",
                code="insecure_url",
                stage="url",
            )

    # Keep path, drop query? Query can be meaningful for gateways. Keep it.
    cleaned = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), parts.query, ""))
    return cleaned


def is_local_host(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return _is_loopback_host(host)
