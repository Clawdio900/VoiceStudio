"""Validate user-supplied media URLs before handing them to a downloader.

yt-dlp fetches whatever it is given, so on a server a request such as
``http://169.254.169.254/...`` or ``http://10.0.0.5/admin`` turns the backend
into an SSRF proxy. Media URLs must be http(s) and every address the host
resolves to must be globally routable. Set
``OMNIVOICE_ALLOW_PRIVATE_MEDIA_URLS=1`` to allow LAN media servers.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit

_TRUTHY = {"1", "true", "yes", "on"}


class UnsafeMediaUrl(ValueError):
    pass


def require_public_http_url(url: str) -> str:
    url = (url or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeMediaUrl("URL must start with http:// or https://")
    if os.environ.get("OMNIVOICE_ALLOW_PRIVATE_MEDIA_URLS", "").strip().lower() in _TRUTHY:
        return url
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as exc:
        raise UnsafeMediaUrl("URL host could not be resolved") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if getattr(address, "ipv4_mapped", None):
            address = address.ipv4_mapped
        if not address.is_global:
            raise UnsafeMediaUrl("URL points to a private or local network address")
    return url
