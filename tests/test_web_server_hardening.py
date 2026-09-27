"""Hardening for running VoiceStudio as a web app on a server.

Covers: reverse-proxy / DNS-rebinding loopback trust, PIN brute-force
throttling, media URL SSRF guard, bundle import extension/size limits, and
media static-file headers.
"""
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from core import public_url
from core.proxy_guard import UNTRUSTED_PLACEHOLDER, effective_client_host
from services import network_share as ns


def _scope(client="127.0.0.1", headers=()):
    return {
        "type": "http",
        "client": (client, 5000),
        "headers": [(k.encode(), v.encode()) for k, v in headers],
    }


# ── proxy guard ─────────────────────────────────────────────────────────────


def test_plain_loopback_keeps_trust():
    assert effective_client_host(_scope(headers=[("host", "localhost:3900")])) is None
    assert effective_client_host(_scope(headers=[("host", "127.0.0.1:3900")])) is None
    assert effective_client_host(_scope(headers=[("host", "[::1]:3900")])) is None


def test_non_loopback_peer_untouched():
    scope = _scope("10.0.0.5", [("x-forwarded-for", "127.0.0.1")])
    assert effective_client_host(scope) is None


def test_reverse_proxy_uses_forwarded_client():
    scope = _scope(headers=[("host", "voice.example.com"), ("x-forwarded-for", "8.8.8.8, 203.0.113.9")])
    assert effective_client_host(scope) == "203.0.113.9"


def test_forwarded_header_rfc7239():
    scope = _scope(headers=[("forwarded", 'for="[2001:db8::1]:4711";proto=https')])
    assert effective_client_host(scope) == "2001:db8::1"


def test_proxy_claiming_loopback_is_not_trusted():
    scope = _scope(headers=[("x-forwarded-for", "127.0.0.1")])
    assert effective_client_host(scope) == UNTRUSTED_PLACEHOLDER


def test_dns_rebinding_host_is_not_trusted():
    scope = _scope(headers=[("host", "attacker.example:3900")])
    assert effective_client_host(scope) == UNTRUSTED_PLACEHOLDER


def test_proxied_request_cannot_reach_admin(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)
    from main import app

    c = TestClient(app, client=("127.0.0.1", 1))
    direct = c.post("/system/set-env", json={})
    proxied = c.post(
        "/system/set-env",
        json={},
        headers={"X-Forwarded-For": "198.51.100.7"},
    )
    assert direct.status_code != 403
    assert proxied.status_code in (401, 403)


# ── PIN throttling ──────────────────────────────────────────────────────────


def test_pin_bruteforce_is_throttled(monkeypatch):
    import main

    monkeypatch.setattr(main, "_pin_failures", {})
    main.app.state.network_share = ns.ShareState(True, 3901, "654321", [])
    try:
        c = TestClient(main.app, client=("10.0.0.5", 1))
        for guess in range(main._PIN_CLIENT_LIMIT):
            r = c.get("/api/voices", headers={"X-OmniVoice-Pin": f"{100000 + guess}"})
            assert r.status_code == 401
        blocked = c.get("/api/voices", headers={"X-OmniVoice-Pin": "654321"})
        assert blocked.status_code == 429
        assert "retry-after" in blocked.headers
        other = TestClient(main.app, client=("10.0.0.6", 1))
        assert other.get("/api/voices", headers={"X-OmniVoice-Pin": "654321"}).status_code != 429
    finally:
        main.app.state.network_share = ns.ShareState()


def test_pin_cookie_is_httponly(monkeypatch):
    import main

    monkeypatch.setattr(main, "_pin_failures", {})
    main.app.state.network_share = ns.ShareState(True, 3901, "654321", [])
    try:
        c = TestClient(main.app, client=("10.0.0.5", 1))
        r = c.get("/api/voices", headers={"X-OmniVoice-Pin": "654321"})
        cookie = r.headers.get("set-cookie", "")
        assert "ov_pin=" in cookie and "HttpOnly" in cookie
    finally:
        main.app.state.network_share = ns.ShareState()


# ── media URL SSRF guard ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "--config-locations=/tmp/x",
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.1/",
        "http://[::1]/",
    ],
)
def test_private_or_bad_media_urls_rejected(url, monkeypatch):
    monkeypatch.delenv("OMNIVOICE_ALLOW_PRIVATE_MEDIA_URLS", raising=False)
    with pytest.raises(public_url.UnsafeMediaUrl):
        public_url.require_public_http_url(url)


def test_public_media_url_allowed(monkeypatch):
    monkeypatch.setattr(
        public_url.socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    assert public_url.require_public_http_url("https://example.com/v") == "https://example.com/v"


def test_private_media_url_opt_in(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_ALLOW_PRIVATE_MEDIA_URLS", "1")
    assert public_url.require_public_http_url("http://10.0.0.1/v.mp4")


# ── bundle import ───────────────────────────────────────────────────────────


def _bundle(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("metadata.json", json.dumps({"profile_name": "x"}))
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_bundle_rejects_non_audio_extension(tmp_path, monkeypatch):
    from api.routers import marketplace

    monkeypatch.setattr(marketplace, "VOICES_DIR", str(tmp_path))
    from main import app

    c = TestClient(app, client=("127.0.0.1", 1))
    r = c.post(
        "/marketplace/import",
        files={"file": ("v.omnivoice", _bundle({"ref_audio.html": b"<script>x</script>"}))},
    )
    assert r.status_code == 400
    assert list(tmp_path.iterdir()) == []


def test_bundle_cleans_up_on_failure(tmp_path, monkeypatch):
    from api.routers import marketplace

    monkeypatch.setattr(marketplace, "VOICES_DIR", str(tmp_path))

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(marketplace, "_insert_imported_profile", boom)
    from main import app

    c = TestClient(app, client=("127.0.0.1", 1), raise_server_exceptions=False)
    r = c.post(
        "/marketplace/import",
        files={"file": ("v.omnivoice", _bundle({"ref_audio.wav": b"RIFF...."}))},
    )
    assert r.status_code == 500
    assert list(tmp_path.iterdir()) == []


def test_bundle_entry_size_capped(tmp_path, monkeypatch):
    from api.routers import marketplace

    monkeypatch.setattr(marketplace, "VOICES_DIR", str(tmp_path))
    monkeypatch.setattr(marketplace, "MAX_BUNDLE_ENTRY_BYTES", 1024)
    from main import app

    c = TestClient(app, client=("127.0.0.1", 1))
    r = c.post(
        "/marketplace/import",
        files={"file": ("v.omnivoice", _bundle({"ref_audio.wav": b"\0" * 4096}))},
    )
    assert r.status_code == 413
    assert list(tmp_path.iterdir()) == []


# ── media static headers ────────────────────────────────────────────────────


def test_voice_audio_served_with_nosniff_and_sandbox():
    import os

    from main import app

    # Resolve the directory the mount actually serves; other tests may have
    # re-pointed core.config's data dir after the app was built.
    mount = next(r for r in app.routes if getattr(r, "name", None) == "voice_audio")
    VOICES_DIR = mount.app.directory
    os.makedirs(VOICES_DIR, exist_ok=True)
    path = os.path.join(VOICES_DIR, "hardening_probe.html")
    with open(path, "w") as fh:
        fh.write("<script>alert(1)</script>")
    try:
        c = TestClient(app, client=("127.0.0.1", 1))
        r = c.get("/voice_audio/hardening_probe.html")
        assert r.status_code == 200
        assert r.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in r.headers["content-security-policy"]
    finally:
        os.remove(path)
