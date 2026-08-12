"""services/account_liveness — 三态探活 + 额度解析（mock httpx）。"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from services.account_liveness import (
    account_chatgpt_id,
    chatgpt_id_from_token,
    classify_liveness,
    format_wham_usage_label,
    parse_wham_usage,
    probe_access_token,
)


class FakeResp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body) if isinstance(body, dict) else str(body)

    def json(self):
        return self._body


class FakeAsyncClient:
    def __init__(self, resp):
        self._resp = resp
        self.sent_headers = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        self.sent_headers = headers
        return self._resp


def _patch_client(monkeypatch, resp, raise_exc=None):
    def factory(*a, **k):
        if raise_exc:
            raise raise_exc
        return FakeAsyncClient(resp)
    monkeypatch.setattr(httpx, "AsyncClient", factory)


class TestClassifyLiveness:
    def test_active_2xx(self):
        assert classify_liveness(200, "") == "active"
        assert classify_liveness(204, "ok") == "active"

    def test_token_invalid_401(self):
        assert classify_liveness(401, "") == "token_invalid"
        assert classify_liveness(403, "authentication token has been invalidated") == "token_invalid"
        assert classify_liveness(200, "invalid_grant") == "token_invalid"

    def test_unknown_never_misclassify(self):
        # 403/429/传输失败一律 unknown，不误判 AT 失效
        assert classify_liveness(403, "") == "unknown"
        assert classify_liveness(429, "rate limit") == "unknown"
        assert classify_liveness(0, "") == "unknown"


class TestJwtAccountId:
    def test_extracts_from_id_token_payload(self):
        import base64
        payload = base64.urlsafe_b64encode(
            json.dumps({"https://api.openai.com/auth": {"chatgpt_account_id": "acc-123"}}).encode()
        ).rstrip(b"=").decode()
        token = f"header.{payload}.sig"
        assert chatgpt_id_from_token(token) == "acc-123"
        assert account_chatgpt_id("", token) == "acc-123"

    def test_empty_token(self):
        assert chatgpt_id_from_token("") == ""
        assert chatgpt_id_from_token("notajwt") == ""

    def test_account_chatgpt_id_prefers_id_token(self):
        import base64
        def _tok(aid):
            p = base64.urlsafe_b64encode(
                json.dumps({"https://api.openai.com/auth": {"chatgpt_account_id": aid}}).encode()
            ).rstrip(b"=").decode()
            return f"h.{p}.s"
        assert account_chatgpt_id(_tok("at-1"), _tok("id-1")) == "id-1"


class TestWhamUsage:
    def test_parse_wham_usage(self):
        body = {"usage": {"5h": {"used": 1200, "limit": 10000}, "7d": {"used": 3000, "limit": 50000}}}
        usage = parse_wham_usage(body)
        assert usage["5h"]["used"] == 1200
        assert usage["5h"]["limit"] == 10000
        assert usage["5h"]["percent"] == 12.0

    def test_parse_non_dict(self):
        assert parse_wham_usage("not-json") is None
        assert parse_wham_usage(None) is None

    def test_format_label(self):
        usage = {"5h": {"used": 1200, "limit": 10000, "remaining": 8800, "percent": 12.0}}
        label = format_wham_usage_label(usage)
        assert "5h: 1.2K/10.0K (12%)" in label
        assert format_wham_usage_label(None) == ""


class TestProbe:
    def test_active(self, monkeypatch):
        _patch_client(monkeypatch, FakeResp(200, {"usage": {"5h": {"used": 1, "limit": 10}}}))
        result = asyncio.run(probe_access_token("at-1"))
        assert result["status"] == "active"
        assert result["status_code"] == 200
        assert "5h:" in result["quota_label"]

    def test_token_invalid(self, monkeypatch):
        _patch_client(monkeypatch, FakeResp(401, {"error": {"message": "authentication token has been invalidated"}}))
        result = asyncio.run(probe_access_token("at-dead"))
        assert result["status"] == "token_invalid"

    def test_unknown_on_transport_error(self, monkeypatch):
        _patch_client(monkeypatch, None, raise_exc=httpx.ConnectError("connection reset"))
        result = asyncio.run(probe_access_token("at-1"))
        assert result["status"] == "unknown"
        assert result["status_code"] == 0

    def test_missing_token(self):
        result = asyncio.run(probe_access_token(""))
        assert result["status"] == "unknown"
        assert result["error"] == "missing_access_token"

    def test_requests_include_account_id(self, monkeypatch):
        import base64
        payload = base64.urlsafe_b64encode(
            json.dumps({"https://api.openai.com/auth": {"chatgpt_account_id": "acc-xyz"}}).encode()
        ).rstrip(b"=").decode()
        _patch_client(monkeypatch, FakeResp(200, {}))
        result = asyncio.run(probe_access_token(f"h.{payload}.s"))
        assert result["status"] == "active"
