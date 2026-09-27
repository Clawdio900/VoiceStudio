"""Stop proxied or DNS-rebound requests from inheriting loopback trust.

The backend grants loopback peers admin rights. When VoiceStudio runs as a
web app behind a reverse proxy on the same host (nginx, Caddy, Traefik,
cloudflared), EVERY browser request reaches uvicorn from 127.0.0.1, so without
this guard any visitor on the internet would be treated as the local owner.
A DNS-rebinding page has the same effect from the user's own browser.

This outermost ASGI middleware rewrites ``scope["client"]`` for a loopback
peer when either:

* the request carries a proxy header (``X-Forwarded-For``, ``Forwarded``,
  ``X-Real-IP``), in which case the client becomes the address the proxy
  appended (rightmost hop), or a non-routable placeholder when unparseable;
* the ``Host`` header names something other than a loopback host, which is
  what a DNS-rebinding page or a proxy that rewrites nothing looks like.

Every downstream check (auth principal, PIN gate, per-route loopback guards)
reads ``scope["client"]``, so one rewrite covers them all.
"""

from __future__ import annotations

import ipaddress

from core.auth import is_loopback

#: TEST-NET-1 (RFC 5737). Never routable, never loopback, and never inside a
#: sensible OMNIVOICE_TRUSTED_NETWORKS range.
UNTRUSTED_PLACEHOLDER = "192.0.2.1"

_PROXY_HEADERS = (b"x-forwarded-for", b"forwarded", b"x-real-ip")
#: "testserver" is Starlette's TestClient default Host. A single-label name
#: no public DNS can serve, so it cannot be used for rebinding.
_LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1", "testserver"})


def _host_name(raw: str) -> str:
    raw = raw.strip().lower()
    if raw.startswith("["):
        return raw[1:].split("]", 1)[0]
    if raw.count(":") == 1:
        return raw.split(":", 1)[0]
    return raw


def _parse_ip(value: str) -> str | None:
    value = value.strip().strip('"')
    if value.lower().startswith("for="):
        value = value[4:].strip('"')
    if value.startswith("["):
        value = value[1:].split("]", 1)[0]
    elif value.count(":") == 1:
        value = value.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def _forwarded_client(headers: dict[bytes, str]) -> str | None:
    if b"x-forwarded-for" in headers:
        return _parse_ip(headers[b"x-forwarded-for"].split(",")[-1])
    if b"forwarded" in headers:
        last = headers[b"forwarded"].split(",")[-1]
        for part in last.split(";"):
            if part.strip().lower().startswith("for="):
                return _parse_ip(part)
        return None
    if b"x-real-ip" in headers:
        return _parse_ip(headers[b"x-real-ip"])
    return None


def effective_client_host(scope) -> str | None:
    """Return the host downstream code should trust, or None to keep as-is."""
    client = scope.get("client")
    if not client or not is_loopback(client[0]):
        return None
    headers: dict[bytes, str] = {}
    for key, value in scope.get("headers") or ():
        headers[key.lower()] = value.decode("latin-1")
    if any(h in headers for h in _PROXY_HEADERS):
        forwarded = _forwarded_client(headers)
        if forwarded and not is_loopback(forwarded):
            return forwarded
        return UNTRUSTED_PLACEHOLDER
    host = headers.get(b"host")
    if host is not None and _host_name(host) not in _LOOPBACK_HOSTNAMES:
        return UNTRUSTED_PLACEHOLDER
    return None


class ProxiedLoopbackMiddleware:
    """Pure ASGI so streaming bodies and WebSockets pass through untouched."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            replacement = effective_client_host(scope)
            if replacement is not None:
                port = scope["client"][1] if scope.get("client") else 0
                scope = dict(scope)
                scope["client"] = (replacement, port)
        return await self.app(scope, receive, send)
