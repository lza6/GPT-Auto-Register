"""v4.0 P0-3 warmup 种 cookie + 请求头补全（sec-ch-ua / sec-fetch-*）。"""
from __future__ import annotations

import json

import pytest

from services.protocol_register import ProtocolRegister, RegistrationContext


class FakeCookieJar:
    """带 get 的 cookie jar（模拟 curl_cffi）。"""

    def __init__(self, cookies: dict | None = None):
        self._cookies = dict(cookies or {})

    def set(self, name, value, domain=None):
        self._cookies[name] = value
        return None

    def get(self, name, default=None):
        return self._cookies.get(name, default)


class FakeResp:
    def __init__(self, status=200, url=""):
        self.status_code = status
        self.url = url


class FakeSession:
    def __init__(self, get_resp=None, cookies: dict | None = None):
        self._get_resp = get_resp or FakeResp(200, "https://chatgpt.com/")
        self.cookies = FakeCookieJar(cookies)
        self.gets = []
        self.closed = 0

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs.get("headers", {})))
        return self._get_resp

    def close(self):
        self.closed += 1


def _reg(monkeypatch, session=None):
    reg = ProtocolRegister({})
    session = session or FakeSession()
    monkeypatch.setattr(reg, "_make_session", lambda *a, **k: session)
    monkeypatch.setattr(reg, "_resolve_proxy", lambda: "http://new-ip:8080")
    return reg, session


def _ctx(reg, session):
    ctx = RegistrationContext(proxy_url="http://old-ip:8080", fingerprint="chrome146", fp_ua="ua")
    ctx.session = session
    return ctx


class TestWarmup:
    def test_success_when_oai_did_present(self, monkeypatch):
        reg, session = _reg(monkeypatch)
        ctx = _ctx(reg, session)
        session.cookies.set("oai-did", "dev-123")
        assert reg._warmup(ctx) is True
        # 只 GET chatgpt.com 一次
        assert session.gets[0][0] == "https://chatgpt.com"
        assert len(session.gets) == 1

    def test_fail_retries_with_new_ip(self, monkeypatch):
        reg, session = _reg(monkeypatch)
        ctx = _ctx(reg, session)
        # 无 oai-did → 重试（换 IP/session）直到 warmup_retries 上限
        assert reg._warmup(ctx) is False
        assert session.closed >= 2  # 每次失败关掉旧 session
        assert ctx.proxy_url == "http://new-ip:8080"  # 换出口 IP

    def test_disabled_returns_true_immediately(self, monkeypatch):
        reg = ProtocolRegister({"warmup_enabled": False})
        ctx = _ctx(reg, FakeSession())
        assert reg._warmup(ctx) is True

    def test_network_error_does_not_raise(self, monkeypatch):
        reg, session = _reg(monkeypatch)

        def boom(*a, **k):
            raise OSError("connection reset")
        monkeypatch.setattr(session, "get", boom)
        ctx = _ctx(reg, session)
        assert reg._warmup(ctx) is False  # 容错，不抛异常

    def test_stage_authorize_invokes_warmup(self, monkeypatch):
        reg, session = _reg(monkeypatch)
        session.cookies.set("oai-did", "dev-123")
        calls = {"n": 0}

        def fake_warmup(ctx):
            calls["n"] += 1
            return True
        monkeypatch.setattr(reg, "_warmup", fake_warmup)
        # authorize 走 200 → 识别 create-account
        session._get_resp = FakeResp(200, "https://auth.openai.com/authorize?response_type=code#create-account")
        ctx = _ctx(reg, session)
        state = reg._stage_authorize(ctx)
        assert calls["n"] == 1


class TestNavigationHeaders:
    def test_contains_client_hints(self):
        reg = ProtocolRegister({})
        h = reg._navigation_headers("ua", "ja-JP,ja;q=0.9", "chrome146")
        assert h["sec-ch-ua"].startswith('"Chromium";v="146"')
        assert h["sec-ch-ua-mobile"] == "?0"
        assert h["sec-ch-ua-platform"] == '"Windows"'
        assert h["sec-fetch-dest"] == "document"
        assert h["sec-fetch-mode"] == "navigate"
        assert h["sec-fetch-site"] == "none"
        assert h["accept-language"] == "ja-JP,ja;q=0.9"

    def test_sec_ch_ua_versions(self):
        reg = ProtocolRegister({})
        assert ';v="136"' in reg._sec_ch_ua("chrome136")["sec-ch-ua"]
        assert '"Not/A)Brand";v="8"' in reg._sec_ch_ua("chrome142")["sec-ch-ua"]
        assert '"Not?A_Brand";v="99"' in reg._sec_ch_ua("chrome146")["sec-ch-ua"]
        # 无指纹回退默认版本
        assert reg._sec_ch_ua("")["sec-ch-ua"]
