"""services/email_service — 91kami 解析、98faka 邮件、验证码提取、wait_for_otp 测试（mock httpx）。"""
from __future__ import annotations

import asyncio
import time

import pytest

from services import db
from services.email_service import EmailService


# ── 假 HTTP ──
class FakeResp:
    def __init__(self, json_data, is_error=False):
        self._json = json_data
        self._is_error = is_error

    def raise_for_status(self):
        if self._is_error:
            raise RuntimeError("http error")

    def json(self):
        return self._json


class FakeClient:
    def __init__(self, *a, **k):
        self.calls = 0
        self.responses = {}

    def set(self, path_fragment, json_data):
        self.responses[path_fragment] = json_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def post(self, url, *a, **k):
        self.calls += 1
        for frag, data in self.responses.items():
            if frag in url:
                return FakeResp(data)
        return FakeResp({"code": 404})


KAMI_URL = "https://mai.91kami.com/cpd/ed7e7db0.aspx"


def _kami_ok():
    return {"IsSuccess": True, "Data": [{"CardPwdArr": [
        {"c": "a@b.com----p1----cid1----rt1"},
        {"c": "c@d.com----p2----cid2----rt2"},
    ]}]}


class TestFetchFromSource:
    async def test_parses_card_pwd_arr(self, monkeypatch):
        client = FakeClient()
        client.set("/api/Cpd/Detail", _kami_ok())
        monkeypatch.setattr("services.email_service.httpx.AsyncClient", lambda *a, **k: client)
        svc = EmailService()
        accts = await svc.fetch_emails_from_source(KAMI_URL)
        assert len(accts) == 2
        assert accts[0]["email"] == "a@b.com"
        assert accts[0]["refresh_token"] == "rt1"

    async def test_invalid_url_token_returns_empty(self, monkeypatch):
        client = FakeClient()
        monkeypatch.setattr("services.email_service.httpx.AsyncClient", lambda *a, **k: client)
        svc = EmailService()
        accts = await svc.fetch_emails_from_source("https://example.com/not-a-cpd-url")
        assert accts == []

    async def test_unsuccessful_response_returns_empty(self, monkeypatch, isolated_db):
        client = FakeClient()
        client.set("/api/Cpd/Detail", {"IsSuccess": False, "Error_Msg": "bad"})
        monkeypatch.setattr("services.email_service.httpx.AsyncClient", lambda *a, **k: client)
        svc = EmailService()
        accts = await svc.fetch_emails_from_source(KAMI_URL)
        assert accts == []

    async def test_data_as_json_string_is_parsed(self, monkeypatch):
        import json
        client = FakeClient()
        client.set("/api/Cpd/Detail", {"IsSuccess": True, "Data": json.dumps([{"CardPwdArr": [{"c": "x@y.com----p----c----rt"}]}])})
        monkeypatch.setattr("services.email_service.httpx.AsyncClient", lambda *a, **k: client)
        svc = EmailService()
        accts = await svc.fetch_emails_from_source(KAMI_URL)
        assert len(accts) == 1
        assert accts[0]["email"] == "x@y.com"


class TestImportEmails:
    async def test_import_counts_inserted_and_skipped(self, monkeypatch, isolated_db):
        client = FakeClient()
        client.set("/api/Cpd/Detail", _kami_ok())
        monkeypatch.setattr("services.email_service.httpx.AsyncClient", lambda *a, **k: client)
        svc = EmailService()
        result = await svc.import_emails(KAMI_URL)
        assert result["inserted"] == 2
        assert len(db.get_pending_emails()) == 2


class Test98Faka:
    async def test_get_email_list_parses_code_200(self, monkeypatch):
        client = FakeClient()
        client.set("/api/emails", {"code": 200, "data": [{"id": "m1", "subject": "chatgpt"}]})
        monkeypatch.setattr("services.email_service.httpx.AsyncClient", lambda *a, **k: client)
        svc = EmailService()
        mails = await svc.get_email_list("e@f.com", "p", "c", "rt")
        assert len(mails) == 1
        assert mails[0]["id"] == "m1"

    async def test_get_email_list_non_200_returns_empty(self, monkeypatch):
        client = FakeClient()
        client.set("/api/emails", {"code": 500, "data": []})
        monkeypatch.setattr("services.email_service.httpx.AsyncClient", lambda *a, **k: client)
        svc = EmailService()
        mails = await svc.get_email_list("e@f.com", "p", "c", "rt")
        assert mails == []

    async def test_get_email_body(self, monkeypatch):
        client = FakeClient()
        client.set("/api/email-body", {"code": 200, "body_html": "<div>123456</div>"})
        monkeypatch.setattr("services.email_service.httpx.AsyncClient", lambda *a, **k: client)
        svc = EmailService()
        body = await svc.get_email_body("e@f.com", "m1", "c", "rt")
        assert "123456" in body


class TestExtractOtpCode:
    def test_extracts_24px(self):
        svc = EmailService()
        assert svc.extract_otp_code('<div style="font-size: 24px">654321</div>') == "654321"

    def test_rejects_repeated_digits(self):
        svc = EmailService()
        assert svc.extract_otp_code('<div style="font-size: 24px">111111</div>') is None

    def test_returns_largest_valid(self):
        svc = EmailService()
        assert svc.extract_otp_code("code 123456 other 654321") == "654321"

    def test_empty_returns_none(self):
        svc = EmailService()
        assert svc.extract_otp_code("") is None
        assert svc.extract_otp_code(None) is None


class TestWaitForOtp:
    async def test_returns_new_otp_and_respects_window(self, monkeypatch, isolated_db):
        from datetime import datetime, timedelta

        svc = EmailService()
        calls = {"n": 0}
        now = datetime.now()

        async def fake_list(email, password, client_id, refresh_token, folder="inbox"):
            calls["n"] += 1
            # 第一次：旧邮件（窗口外）；之后：新验证码邮件（窗口内）
            if calls["n"] == 1:
                return [{"id": "old", "subject": "chatgpt code", "received_time": (now - timedelta(days=1)).isoformat()}]
            return [{"id": "new", "subject": "ChatGPT verification", "received_time": now.isoformat()}]

        async def fake_body(email, mail_id, client_id, refresh_token):
            return '<div style="font-size: 24px">123456</div>'

        monkeypatch.setattr(svc, "get_email_list", fake_list)
        monkeypatch.setattr(svc, "get_email_body", fake_body)
        code = await svc.wait_for_otp("e@f.com", "p", "c", "rt", timeout_sec=1, poll_interval=0)
        assert code == "123456"

    async def test_timeout_returns_none(self, monkeypatch, isolated_db):
        svc = EmailService()

        async def fake_list(*a, **k):
            return []

        monkeypatch.setattr(svc, "get_email_list", fake_list)
        code = await svc.wait_for_otp("e@f.com", "p", "c", "rt", timeout_sec=0, poll_interval=0)
        assert code is None
