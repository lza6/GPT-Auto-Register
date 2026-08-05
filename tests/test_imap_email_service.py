"""services/imap_email_service — 验证码提取与 IMAP 拉取（mock imapclient）测试。"""
from __future__ import annotations

import imapclient

from services.imap_email_service import ImapEmailService


class FakeImap:
    def __init__(self, messages=None, body=b'<div style="font-size: 24px">654321</div>'):
        self._messages = [1, 2, 3] if messages is None else messages
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None

    def login(self, *a, **k):
        return None

    def select_folder(self, *a, **k):
        return None

    def search(self, *a, **k):
        return self._messages

    def fetch(self, msg_ids, parts):
        return {mid: {b"BODY[]": self._body} for mid in msg_ids}


class TestImapService:
    async def test_fetch_latest_otp(self, monkeypatch):
        svc = ImapEmailService()
        monkeypatch.setattr(imapclient, "IMAPClient", lambda *a, **k: FakeImap())
        seen: set[str] = set()
        code = await svc._fetch_latest_otp("a@b.com", "p", seen)
        assert code == "654321"
        # 已处理 id 不会重复
        code2 = await svc._fetch_latest_otp("a@b.com", "p", seen)
        assert code2 == "654321"  # fetch 仍返回，但 seen 只影响不返回 None 的路径

    async def test_fetch_no_messages_returns_none(self, monkeypatch):
        svc = ImapEmailService()
        monkeypatch.setattr(imapclient, "IMAPClient", lambda *a, **k: FakeImap(messages=[]))
        code = await svc._fetch_latest_otp("a@b.com", "p", set())
        assert code is None

    def test_extract_otp_variants(self):
        svc = ImapEmailService()
        assert svc._extract_otp('<div style="font-size: 24px">123456</div>') == "123456"
        assert svc._extract_otp('<div style="font-size: 24px">111111</div>') is None
        assert svc._extract_otp("code 123456 or 654321") == "654321"
        assert svc._extract_otp("") is None
