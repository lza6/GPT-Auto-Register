"""services/graph_email_service — token LRU 缓存与验证码提取测试。"""
from __future__ import annotations

import time

from services import graph_email_service as g


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class FakeAsyncClient:
    """mock httpx.AsyncClient：post 返回 token，get 按 URL 返回邮件/正文。"""

    def __init__(self, *a, **k):
        self.calls = 0
        self._messages = {"value": []}
        self._body = {"body": {"content": ""}}

    def set_messages(self, messages):
        self._messages = {"value": messages}

    def set_body(self, html):
        self._body = {"body": {"content": html}}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def post(self, *a, **k):
        self.calls += 1
        return FakeResponse({"access_token": "tok", "expires_in": 3600})

    async def get(self, url, *a, **k):
        if "/messages/" in url:
            return FakeResponse(self._body)
        return FakeResponse(self._messages)


class TestTokenCache:
    async def test_cache_hit_does_not_refetch(self, monkeypatch):
        svc = g.GraphEmailService()
        fake = FakeAsyncClient()
        monkeypatch.setattr(g.httpx, "AsyncClient", lambda *a, **k: fake)
        assert await svc._get_access_token("cid1", "rt") == "tok"
        assert fake.calls == 1
        assert await svc._get_access_token("cid1", "rt") == "tok"
        assert fake.calls == 1  # 命中缓存，未重复请求

    async def test_expired_token_refetches(self, monkeypatch):
        svc = g.GraphEmailService()
        fake = FakeAsyncClient()
        monkeypatch.setattr(g.httpx, "AsyncClient", lambda *a, **k: fake)
        # 塞入已过期条目（expire_time - 60 < now）；key 为复合 key（cid:rt）
        svc._token_cache["cid1:rt"] = ("old-token", time.time() - 10)
        token = await svc._get_access_token("cid1", "rt")
        assert token == "tok"
        assert fake.calls == 1  # 过期后重新请求

    async def test_cache_lru_evicts_oldest(self, monkeypatch):
        svc = g.GraphEmailService()
        fake = FakeAsyncClient()
        monkeypatch.setattr(g.httpx, "AsyncClient", lambda *a, **k: fake)
        for i in range(svc.MAX_CACHE_ENTRIES + 10):
            await svc._get_access_token(f"cid{i}", f"rt{i}")
        assert len(svc._token_cache) <= svc.MAX_CACHE_ENTRIES
        # 最旧应被逐出：cid0:rt0 不在缓存中
        assert "cid0:rt0" not in svc._token_cache

    async def test_used_key_moved_to_end_on_hit(self, monkeypatch):
        svc = g.GraphEmailService()
        fake = FakeAsyncClient()
        monkeypatch.setattr(g.httpx, "AsyncClient", lambda *a, **k: fake)
        for i in range(svc.MAX_CACHE_ENTRIES):
            await svc._get_access_token(f"cid{i}", f"rt{i}")
        # 命中 cid0:rt0（最旧），LRU 应把它移到末尾
        await svc._get_access_token("cid0", "rt0")
        assert next(reversed(svc._token_cache)) == "cid0:rt0"


class TestOtpExtract:
    def test_extracts_24px_code(self):
        svc = g.GraphEmailService()
        html = '<div style="font-size: 24px">123456</div>'
        assert svc.extract_otp_code(html) == "123456"

    def test_rejects_repeated_digits(self):
        svc = g.GraphEmailService()
        html = '<div style="font-size: 24px">111111</div>'
        assert svc.extract_otp_code(html) is None

    def test_falls_back_to_largest_valid(self):
        svc = g.GraphEmailService()
        html = "code is 90210 or maybe 654321 and 123456"
        assert svc.extract_otp_code(html) == "654321"

    def test_empty_html_returns_none(self):
        svc = g.GraphEmailService()
        assert svc.extract_otp_code("") is None


def _otp_msg(mid, subject="Your ChatGPT code", received="2026-08-05T00:00:00Z"):
    return {
        "id": mid,
        "subject": subject,
        "from": {"emailAddress": {"address": "noreply@tm.openai.com"}},
        "receivedDateTime": received,
        "bodyPreview": "",
    }


class TestMailReading:
    async def test_get_email_list_parses_messages(self, monkeypatch):
        svc = g.GraphEmailService()
        fake = FakeAsyncClient()
        fake.set_messages([_otp_msg("m1")])
        monkeypatch.setattr(g.httpx, "AsyncClient", lambda *a, **k: fake)
        mails = await svc.get_email_list("e@f.com", "cid", "rt")
        assert len(mails) == 1
        assert mails[0]["id"] == "m1"
        assert mails[0]["from_address"] == "noreply@tm.openai.com"

    async def test_get_email_body(self, monkeypatch):
        svc = g.GraphEmailService()
        fake = FakeAsyncClient()
        fake.set_body('<div style="font-size: 24px">654321</div>')
        monkeypatch.setattr(g.httpx, "AsyncClient", lambda *a, **k: fake)
        body = await svc.get_email_body("e@f.com", "m1", "cid", "rt")
        assert "654321" in body

    async def test_wait_for_otp_returns_code(self, monkeypatch):
        svc = g.GraphEmailService()
        fake = FakeAsyncClient()
        fake.set_messages([_otp_msg("m1")])
        fake.set_body('<div style="font-size: 24px">654321</div>')
        monkeypatch.setattr(g.httpx, "AsyncClient", lambda *a, **k: fake)
        code = await svc.wait_for_otp("e@f.com", "cid", "rt", timeout_sec=1, poll_interval=0)
        assert code == "654321"

    async def test_wait_for_new_otp_filters_by_after_time(self, monkeypatch):
        svc = g.GraphEmailService()
        fake = FakeAsyncClient()
        # 早于 after_time 的旧邮件不应返回
        fake.set_messages([_otp_msg("m1", received="2026-08-05T00:00:00Z")])
        fake.set_body('<div style="font-size: 24px">654321</div>')
        monkeypatch.setattr(g.httpx, "AsyncClient", lambda *a, **k: fake)
        code = await svc.wait_for_new_otp(
            "e@f.com", "cid", "rt", after_time="2026-08-05T01:00:00Z",
            timeout_sec=0, poll_interval=0,
        )
        assert code is None  # 全部早于 after_time，超时返回 None

    async def test_wait_for_new_otp_returns_new(self, monkeypatch):
        svc = g.GraphEmailService()
        fake = FakeAsyncClient()
        fake.set_messages([_otp_msg("m1", received="2026-08-05T02:00:00Z")])
        fake.set_body('<div style="font-size: 24px">654321</div>')
        monkeypatch.setattr(g.httpx, "AsyncClient", lambda *a, **k: fake)
        code = await svc.wait_for_new_otp(
            "e@f.com", "cid", "rt", after_time="2026-08-05T01:00:00Z",
            timeout_sec=1, poll_interval=0,
        )
        assert code == "654321"

    async def test_get_email_list_error_returns_empty(self, monkeypatch):
        svc = g.GraphEmailService()
        fake = FakeAsyncClient()

        async def boom(*a, **k):
            raise RuntimeError("network down")

        fake.get = boom
        monkeypatch.setattr(g.httpx, "AsyncClient", lambda *a, **k: fake)
        mails = await svc.get_email_list("e@f.com", "cid", "rt")
        assert mails == []

    async def test_wait_for_otp_ignores_non_otp_mail(self, monkeypatch):
        svc = g.GraphEmailService()
        fake = FakeAsyncClient()
        # 非验证码主题 + 非 openai 发件人 → 不应返回
        fake.set_messages([_otp_msg("m1", subject="Newsletter", received="2026-08-05T02:00:00Z")])
        monkeypatch.setattr(g.httpx, "AsyncClient", lambda *a, **k: fake)
        code = await svc.wait_for_otp("e@f.com", "cid", "rt", timeout_sec=0, poll_interval=0)
        assert code is None
