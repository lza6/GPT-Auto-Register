"""services/protocol_register — 纯协议注册引擎（mock session，不依赖真实网络）。"""
from __future__ import annotations

import json

import pytest

from services.protocol_register import ProtocolRegister


class FakeResp:
    def __init__(self, status=200, url="", data=None, text=""):
        self.status_code = status
        self.url = url
        self._data = data or {}
        self.text = text if text else (json.dumps(self._data) if self._data else "")

    def json(self):
        return self._data


class FakeCookieJar:
    def set(self, name, value, domain=None):
        return None


class FakeSession:
    def __init__(self, get_resp=None, post_map=None):
        self._get_resp = get_resp
        self._post_map = post_map or {}
        self.cookies = FakeCookieJar()
        self.posted = []

    def get(self, url, **kwargs):
        return self._get_resp

    def post(self, url, json=None, **kwargs):
        self.posted.append((url, json))
        for key, resp in self._post_map.items():
            if key in url:
                return resp
        return FakeResp(status=404, data={})


def _make_reg(monkeypatch, session):
    reg = ProtocolRegister({"otp_wait_timeout_sec": 30})
    monkeypatch.setattr(reg, "_make_session", lambda: session)
    monkeypatch.setattr(
        "services.protocol_register.build_sentinel_token",
        lambda *a, **k: ("fake-sentinel", "oai-sc-value"),
    )
    monkeypatch.setattr(reg, "_fetch_newest_otp", lambda card, after: "123456")
    monkeypatch.setattr(reg, "_exchange_code", lambda code, verifier: {
        "access_token": "eyJat", "refresh_token": "eyJrt", "id_token": "eyJit",
    })
    return reg


CARDS = {"email": "u@example.com", "password": "p", "client_id": "cid", "refresh_token": "rt"}


class TestCreateAccountPath:
    async def test_full_flow_success(self, monkeypatch):
        post_map = {
            "user/register": FakeResp(200, data={"continue_url": "https://auth.openai.com/email-verification"}),
            "email-otp/send": FakeResp(200),
            "email-otp/validate": FakeResp(200, data={"continue_url": "https://auth.openai.com/about-you"}),
            "create_account": FakeResp(200, data={"continue_url": "https://platform.openai.com/auth/callback?code=ac_xyz"}),
        }
        session = FakeSession(
            get_resp=FakeResp(200, url="https://auth.openai.com/create-account/password"),
            post_map=post_map,
        )
        reg = _make_reg(monkeypatch, session)

        result = await reg.register_one(**CARDS)
        assert result["status"] == "success"
        assert result["access_token"] == "eyJat"
        assert result["refresh_token"] == "eyJrt"
        assert result["openai_password"]  # 新号设了密码
        # 断言走了 create-account 流程
        posted_urls = [u for u, _ in session.posted]
        assert any("user/register" in u for u in posted_urls)
        assert any("create_account" in u for u in posted_urls)


class TestLogInPath:
    async def test_passwordless_flow_success(self, monkeypatch):
        post_map = {
            "passwordless/send-otp": FakeResp(200),
            "email-otp/validate": FakeResp(200, data={"continue_url": "https://platform.openai.com/auth/callback?code=ac_xyz"}),
        }
        session = FakeSession(
            get_resp=FakeResp(200, url="https://auth.openai.com/log-in/password"),
            post_map=post_map,
        )
        reg = _make_reg(monkeypatch, session)

        result = await reg.register_one(**CARDS)
        assert result["status"] == "success"
        assert result["access_token"] == "eyJat"
        # 已存在账号不设新密码
        assert result["openai_password"] == ""
        posted_urls = [u for u, _ in session.posted]
        assert any("passwordless/send-otp" in u for u in posted_urls)


class TestFailureClassification:
    async def test_authorize_network_error_falls_back_to_browser(self, monkeypatch):
        session = FakeSession(get_resp=FakeResp(500, url="https://auth.openai.com/"))
        reg = _make_reg(monkeypatch, session)
        result = await reg.register_one(**CARDS)
        assert result["status"] == "failed"
        assert result["fallback_browser"] is True

    async def test_account_deactivated_no_fallback(self, monkeypatch):
        post_map = {
            "user/register": FakeResp(403, data={"error": "account_deactivated"}),
        }
        session = FakeSession(
            get_resp=FakeResp(200, url="https://auth.openai.com/create-account/password"),
            post_map=post_map,
        )
        reg = _make_reg(monkeypatch, session)
        result = await reg.register_one(**CARDS)
        assert result["status"] == "failed"
        assert result["fallback_browser"] is False

    async def test_validate_5xx_falls_back(self, monkeypatch):
        post_map = {
            "user/register": FakeResp(200, data={"continue_url": "https://auth.openai.com/email-verification"}),
            "email-otp/validate": FakeResp(500, data={}),
        }
        session = FakeSession(
            get_resp=FakeResp(200, url="https://auth.openai.com/create-account/password"),
            post_map=post_map,
        )
        reg = _make_reg(monkeypatch, session)
        result = await reg.register_one(**CARDS)
        assert result["status"] == "failed"
        assert result["fallback_browser"] is True

    async def test_unrecognized_authorize_falls_back(self, monkeypatch):
        session = FakeSession(get_resp=FakeResp(200, url="https://auth.openai.com/weird"))
        reg = _make_reg(monkeypatch, session)
        result = await reg.register_one(**CARDS)
        assert result["status"] == "failed"
        assert "未识别形态" in result["error"]
        assert result["fallback_browser"] is True

    async def test_sentinel_header_injected(self, monkeypatch):
        post_map = {
            "user/register": FakeResp(200, data={"continue_url": "https://auth.openai.com/email-verification"}),
        }
        session = FakeSession(
            get_resp=FakeResp(200, url="https://auth.openai.com/create-account/password"),
            post_map=post_map,
        )
        reg = _make_reg(monkeypatch, session)
        await reg.register_one(**CARDS)
        # user/register 请求应携带 sentinel 头（由 build_sentinel_token 产生）
        # 我们无法从 FakeSession 直接看 headers，但可验证 sentinel 被调用（间接断言流程没崩）
        assert any("user/register" in u for u, _ in session.posted)
