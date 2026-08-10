"""protocol_register — 失败分类、邮件时间时区、验证码提取测试。"""
from __future__ import annotations

import base64
import hashlib
import re

from services.protocol_register import (
    ProtocolRegister,
    _build_authorize_url,
    _extract_code,
    _gen_pkce,
    gen_password,
)


class FakeResp:
    def __init__(self, status, data=None, text=""):
        self.status_code = status
        self._data = data or {}
        self.text = text

    def json(self):
        return self._data


class TestClassifyFailure:
    def test_account_deactivated_risk(self):
        resp = FakeResp(403, {"error": "account_deactivated"})
        _, fb, ftype = ProtocolRegister._classify_failure(resp, {"error": "account_deactivated"}, "user/register")
        assert fb is False
        assert ftype == "risk_control"

    def test_rate_limit_risk(self):
        resp = FakeResp(429, {"error": "rate_limit"})
        _, fb, ftype = ProtocolRegister._classify_failure(resp, {"error": "rate_limit"}, "email-otp/validate")
        assert ftype == "risk_control"
        assert fb is False

    def test_invalid_otp_timeout(self):
        """invalid_or_expired_otp 归入 mailbox 分类 → otp_timeout。"""
        resp = FakeResp(400, {"error": "invalid_or_expired_otp"})
        _, fb, ftype = ProtocolRegister._classify_failure(resp, {"error": "invalid_or_expired_otp"}, "email-otp/validate")
        assert ftype == "otp_timeout"
        assert fb is False

    def test_turnstile_network_fallback(self):
        """turnstile/cloudflare 归入 network 分类，可降级浏览器兜底。"""
        resp = FakeResp(200, {"error": "turnstile required"})
        _, fb, ftype = ProtocolRegister._classify_failure(resp, {"error": "turnstile"}, "authorize")
        assert ftype == "network"
        assert fb is True

    def test_server_5xx_fallbacks(self):
        resp = FakeResp(500, {})
        _, fb, ftype = ProtocolRegister._classify_failure(resp, {}, "user/register")
        assert fb is True
        assert ftype == "server_5xx"

    def test_unknown_4xx_no_fallback(self):
        resp = FakeResp(400, {"error": "weird"})
        _, fb, ftype = ProtocolRegister._classify_failure(resp, {"error": "weird"}, "step")
        assert fb is False
        assert ftype == "unknown"


class TestMailTime:
    def test_utc_z_is_parsed_to_naive(self):
        reg = ProtocolRegister({})
        d = reg._mail_time({"received_time": "2026-08-05T07:00:00Z"})
        assert d.tzinfo is None
        assert (d.year, d.month, d.day) == (2026, 8, 5)

    def test_invalid_returns_epoch(self):
        reg = ProtocolRegister({})
        d = reg._mail_time({"received_time": "not-a-date"})
        assert d.year == 1970


class TestCodeFrom:
    def test_extracts_code_from_html(self, monkeypatch):
        reg = ProtocolRegister({})
        monkeypatch.setattr(
            reg, "_mail_api",
            lambda *a, **k: {"body_html": '<div style="font-size:24px">987654</div>'},
        )
        code = reg._code_from({"email": "e", "client_id": "c", "refresh_token": "r"}, {"id": "m1"})
        assert code == "987654"

    def test_extracts_code_with_keyword(self, monkeypatch):
        reg = ProtocolRegister({})
        monkeypatch.setattr(
            reg, "_mail_api",
            lambda *a, **k: {"body_html": "Your code:123456 <br>其他 654321"},
        )
        code = reg._code_from({"email": "e", "client_id": "c", "refresh_token": "r"}, {"id": "m1"})
        assert code == "123456"

    def test_no_code_returns_none(self, monkeypatch):
        reg = ProtocolRegister({})
        monkeypatch.setattr(
            reg, "_mail_api", lambda *a, **k: {"body_html": "no digits here"},
        )
        code = reg._code_from({"email": "e", "client_id": "c", "refresh_token": "r"}, {"id": "m1"})
        assert code is None

    def test_api_error_returns_none(self, monkeypatch):
        reg = ProtocolRegister({})
        def boom(*a, **k):
            raise RuntimeError("api down")
        monkeypatch.setattr(reg, "_mail_api", boom)
        code = reg._code_from({"email": "e", "client_id": "c", "refresh_token": "r"}, {"id": "m1"})
        assert code is None


class TestOtpWindowConfig:
    def test_window_seconds_loaded_from_config(self):
        reg = ProtocolRegister({"otp_min_age_window_sec": "300", "otp_fallback_after_sec": "50", "otp_backfill_window_min": "20"})
        assert reg.otp_min_age_window_sec == 300
        assert reg.otp_fallback_after_sec == 50
        assert reg.otp_backfill_window_min == 20

    def test_defaults(self):
        reg = ProtocolRegister({})
        # v3.1 审计：otp_min_age_window_sec 默认统一为 120（对齐 config.example/schema/README/browser_register）
        assert reg.otp_min_age_window_sec == 120
        assert reg.otp_fallback_after_sec == 40
        assert reg.otp_backfill_window_min == 15


class TestPureHelpers:
    def test_gen_password_length_and_charset(self):
        for _ in range(3):
            pw = gen_password()
            assert len(pw) == 16
            assert pw.isalnum()

    def test_gen_pkce_verifier_and_challenge(self):
        verifier, challenge = _gen_pkce()
        assert verifier and challenge
        # S256 challenge = base64url(sha256(verifier)) 无填充
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        assert challenge == expected

    def test_build_authorize_url_contains_login_hint(self):
        url = _build_authorize_url("user@example.com", "test_challenge_abc")
        assert "auth.openai.com/api/accounts/authorize" in url
        assert "login_hint=user%40example.com" in url
        assert "code_challenge_method=S256" in url
        assert "code_challenge=test_challenge_abc" in url
        assert "client_id=app_2SKx67EdpoN0G6j64rFvigXD" in url

    def test_authorize_url_challenge_matches_exchange_verifier(self):
        """v3.1 审计回归：authorize 的 code_challenge 必须与换 token 的 code_verifier 同对。

        原 bug：_build_authorize_url 内部独立 _gen_pkce() 丢弃 verifier，与 _register_sync
        的 verifier 不是同一对 → sha256(verifier) != challenge → OpenAI 换 token 必失败。
        """
        from urllib.parse import parse_qs, urlparse

        verifier, challenge = _gen_pkce()  # 模拟 _register_sync 生成同一对
        url = _build_authorize_url("u@example.com", challenge)
        # 从 URL 取出实际上报的 code_challenge
        qs = parse_qs(urlparse(url).query)
        reported = qs["code_challenge"][0]
        # OpenAI 校验逻辑：sha256(verifier) == 上报的 challenge
        recomputed = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        assert reported == recomputed, "code_challenge 必须与 code_verifier 同对（PKCE 一致）"

    def test_extract_code_variants(self):
        assert _extract_code("https://x/callback?code=ac_xyz") == "ac_xyz"
        assert _extract_code("https://x/callback?state=1&code=abc123&x=2") == "abc123"
        assert _extract_code("https://x/no-code") == ""

    def test_is_otp_mail_detection(self):
        reg = ProtocolRegister({})
        assert reg._is_otp_mail({"subject": "ChatGPT 验证码", "from_address": "x@y.com"})
        assert reg._is_otp_mail({"subject": "Hello", "from_address": "noreply@openai.com"})
        assert not reg._is_otp_mail({"subject": "Newsletter", "from_address": "news@y.com"})

    def test_otp_poll_configured(self):
        reg = ProtocolRegister({"otp_poll_interval_sec": "7"})
        assert reg.otp_poll == 7
