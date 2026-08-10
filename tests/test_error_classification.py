"""error_classification — 独立分类函数 + _classify_failure 集成测试。"""
from __future__ import annotations

from services.protocol_register import (
    classify_error,
    error_text,
    NETWORK_ERROR_MARKERS,
    ACCOUNT_ERROR_MARKERS,
    MAILBOX_ERROR_MARKERS,
    AUTH_STATE_ERROR_MARKERS,
    RATE_LIMIT_MARKERS,
    ProtocolRegister,
)


class FakeResp:
    def __init__(self, status, data=None, text=""):
        self.status_code = status
        self._data = data or {}
        self.text = text

    def json(self):
        return self._data


# ── classify_error: 独立函数测试 ──────────────────────────────

class TestClassifyError:
    """classify_error() 独立函数全覆盖测试。"""

    # ── account ──

    def test_account_deactivated(self):
        assert classify_error({"error": "account_deactivated"}) == "account"

    def test_account_deactivated_phrase(self):
        assert classify_error("account has been deactivated") == "account"

    def test_deleted_or_deactivated(self):
        assert classify_error("deleted or deactivated") == "account"

    def test_registration_disallowed(self):
        assert classify_error("registration_disallowed") == "account"

    def test_invalid_grant(self):
        assert classify_error("invalid_grant") == "account"

    def test_authentication_failed(self):
        assert classify_error("AuthenticationFailed") == "account"

    def test_invalid_credentials(self):
        assert classify_error("invalid credentials") == "account"

    def test_wrong_email_otp_code(self):
        assert classify_error("wrong_email_otp_code") == "account"

    def test_password_verify_failed(self):
        assert classify_error("password_verify_failed") == "account"

    def test_phone_recently_used(self):
        assert classify_error("phone_recently_used") == "account"

    def test_unsupported_phone_number(self):
        assert classify_error("unsupported_phone_number") == "account"

    def test_fraud_guard(self):
        assert classify_error("fraud_guard") == "account"

    def test_token_invalidated(self):
        assert classify_error("token_invalidated") == "account"

    def test_max_check_attempts(self):
        assert classify_error("max_check_attempts") == "account"

    # ── rate_limit (优先于 account) ──

    def test_rate_limit(self):
        assert classify_error("rate_limit") == "rate_limit"

    def test_too_many_requests(self):
        assert classify_error("too many requests") == "rate_limit"

    def test_ratelimit(self):
        assert classify_error("ratelimit") == "rate_limit"

    # ── mailbox ──

    def test_otp_timeout(self):
        assert classify_error("otp timeout") == "mailbox"

    def test_mailbox_otp_timeout(self):
        assert classify_error("mailbox_otp_timeout") == "mailbox"

    def test_invalid_or_expired_otp(self):
        assert classify_error("invalid_or_expired_otp") == "mailbox"

    def test_invalid_code(self):
        assert classify_error("invalid code") == "mailbox"

    # ── network ──

    def test_tls_error(self):
        assert classify_error("tls handshake failed") == "network"

    def test_ssl_error(self):
        assert classify_error("ssl error") == "network"

    def test_eof_occurred(self):
        assert classify_error("eof occurred") == "network"

    def test_connection_reset(self):
        assert classify_error("connection reset by peer") == "network"

    def test_connection_aborted(self):
        assert classify_error("connection aborted") == "network"

    def test_proxy_error(self):
        assert classify_error("proxy connect error") == "network"

    def test_socks_error(self):
        assert classify_error("socks5 proxy error") == "network"

    def test_dns_error(self):
        assert classify_error("dns resolution failed") == "network"

    def test_curl_code_35(self):
        assert classify_error("curl: (35)") == "network"

    def test_curl_code_28(self):
        assert classify_error("curl: (28)") == "network"

    def test_curl_code_6(self):
        assert classify_error("curl: (6)") == "network"

    def test_curl_code_7(self):
        assert classify_error("curl: (7)") == "network"

    def test_cloudflare(self):
        assert classify_error("cloudflare challenge") == "network"

    def test_max_retries_exceeded(self):
        assert classify_error("max retries exceeded") == "network"

    def test_broken_pipe(self):
        assert classify_error("broken pipe") == "network"

    def test_certificate_verify_failed(self):
        assert classify_error("certificate verify failed") == "network"

    def test_econnrefused(self):
        assert classify_error("econnrefused") == "network"

    def test_econnreset(self):
        assert classify_error("econnreset") == "network"

    def test_etimedout(self):
        assert classify_error("etimedout") == "network"

    # ── auth_state ──

    def test_invalid_auth_step(self):
        assert classify_error("invalid_auth_step") == "auth_state"

    def test_invalid_state(self):
        assert classify_error("invalid_state") == "auth_state"

    def test_session_no_longer_valid(self):
        assert classify_error("sign-in session is no longer valid") == "auth_state"

    # ── unknown ──

    def test_unknown_error(self):
        assert classify_error("weird internal error") == "unknown"

    def test_empty_string(self):
        assert classify_error("") == "unknown"

    def test_none(self):
        assert classify_error(None) == "unknown"

    def test_empty_dict(self):
        assert classify_error({}) == "unknown"


# ── error_text: 扁平化工具函数 ────────────────────────────

class TestErrorText:
    def test_dict_concatenates_keys(self):
        text = error_text({"error": "rate_limit", "message": "too many"})
        assert "rate_limit" in text
        assert "too many" in text

    def test_nested_dict(self):
        text = error_text({"error": "invalid_grant", "refresh": {"error": "token_expired"}})
        assert "invalid_grant" in text
        assert "token_expired" in text

    def test_string_lowercased(self):
        assert error_text("RateLimit Exceeded") == "ratelimit exceeded"

    def test_none_returns_empty(self):
        assert error_text(None) == ""

    def test_int_returns_string(self):
        assert error_text(429) == "429"


# ── _classify_failure: 集成测试 ──────────────────────────

class TestClassifyFailureIntegration:
    """_classify_failure 集成测试：验证 classify_error 被正确委派，且 fallback_browser 逻辑不变。"""

    def test_account_deactivated_no_fallback(self):
        resp = FakeResp(403, {"error": "account_deactivated"})
        _, fb, ftype = ProtocolRegister._classify_failure(resp, {"error": "account_deactivated"}, "user/register")
        assert fb is False
        assert ftype == "risk_control"

    def test_rate_limit_no_fallback(self):
        resp = FakeResp(429, {"error": "rate_limit"})
        _, fb, ftype = ProtocolRegister._classify_failure(resp, {"error": "rate_limit"}, "email-otp/validate")
        assert ftype == "risk_control"
        assert fb is False

    def test_invalid_otp_no_fallback(self):
        resp = FakeResp(400, {"error": "invalid_or_expired_otp"})
        _, fb, ftype = ProtocolRegister._classify_failure(resp, {"error": "invalid_or_expired_otp"}, "email-otp/validate")
        assert ftype == "otp_timeout"
        assert fb is False

    def test_invalid_code_no_fallback(self):
        resp = FakeResp(400, {"error": "invalid code"})
        _, fb, ftype = ProtocolRegister._classify_failure(resp, {"error": "invalid code"}, "email-otp/validate")
        assert ftype == "otp_timeout"
        assert fb is False

    def test_registration_disallowed_no_fallback(self):
        resp = FakeResp(403, {"error": "registration_disallowed"})
        _, fb, ftype = ProtocolRegister._classify_failure(resp, {"error": "registration_disallowed"}, "user/register")
        assert ftype == "risk_control"
        assert fb is False

    def test_invalid_grant_no_fallback(self):
        resp = FakeResp(400, {"error": "invalid_grant"})
        _, fb, ftype = ProtocolRegister._classify_failure(resp, {"error": "invalid_grant"}, "oauth/token")
        assert ftype == "risk_control"
        assert fb is False

    def test_network_error_falls_back(self):
        resp = FakeResp(502, {"error": "connection reset"})
        _, fb, ftype = ProtocolRegister._classify_failure(resp, {"error": "connection reset"}, "authorize")
        assert fb is True
        assert ftype == "network"

    def test_network_in_text_falls_back(self):
        resp = FakeResp(200, {})
        # 模拟网络层异常文本在 resp.text 中
        resp.text = "tls handshake failed"
        _, fb, ftype = ProtocolRegister._classify_failure(resp, {}, "authorize")
        assert fb is True
        assert ftype == "network"

    def test_server_5xx_falls_back(self):
        resp = FakeResp(500, {})
        _, fb, ftype = ProtocolRegister._classify_failure(resp, {}, "user/register")
        assert fb is True
        assert ftype == "server_5xx"

    def test_unknown_4xx_no_fallback(self):
        resp = FakeResp(400, {"error": "weird"})
        _, fb, ftype = ProtocolRegister._classify_failure(resp, {"error": "weird"}, "step")
        assert fb is False
        assert ftype == "unknown"

    def test_turnstile_network_falls_back(self):
        # turnstile/cloudflare 现在归入 network 分类，可降级
        resp = FakeResp(200, {"error": "turnstile required"})
        _, fb, ftype = ProtocolRegister._classify_failure(resp, {"error": "turnstile"}, "authorize")
        assert fb is True
        assert ftype == "network"

    def test_cloudflare_network_falls_back(self):
        resp = FakeResp(403, {"error": "cloudflare challenge"})
        _, fb, ftype = ProtocolRegister._classify_failure(resp, {"error": "cloudflare challenge"}, "authorize")
        assert fb is True
        assert ftype == "network"

    def test_max_check_attempts_risk(self):
        resp = FakeResp(400, {"error": "max_check_attempts"})
        _, fb, ftype = ProtocolRegister._classify_failure(resp, {"error": "max_check_attempts"}, "email-otp/validate")
        assert ftype == "risk_control"
        assert fb is False

    def test_auth_state_no_fallback(self):
        resp = FakeResp(400, {"error": "invalid_auth_step"})
        _, fb, ftype = ProtocolRegister._classify_failure(resp, {"error": "invalid_auth_step"}, "authorize")
        assert ftype == "risk_control"
        assert fb is False

    def test_otp_timeout_in_text_no_fallback(self):
        resp = FakeResp(400, {})
        resp.text = "mailbox_otp_timeout"
        _, fb, ftype = ProtocolRegister._classify_failure(resp, {}, "email-otp/validate")
        assert ftype == "otp_timeout"
        assert fb is False


# ── 标记常量完整性测试 ─────────────────────────────────

class TestMarkerConstants:
    def test_network_markers_are_tuples(self):
        assert isinstance(NETWORK_ERROR_MARKERS, tuple)
        assert len(NETWORK_ERROR_MARKERS) > 20  # 充分覆盖

    def test_account_markers_are_tuples(self):
        assert isinstance(ACCOUNT_ERROR_MARKERS, tuple)
        assert len(ACCOUNT_ERROR_MARKERS) > 10

    def test_mailbox_markers_are_tuples(self):
        assert isinstance(MAILBOX_ERROR_MARKERS, tuple)
        assert len(MAILBOX_ERROR_MARKERS) >= 5

    def test_auth_state_markers_are_tuples(self):
        assert isinstance(AUTH_STATE_ERROR_MARKERS, tuple)
        assert len(AUTH_STATE_ERROR_MARKERS) >= 3

    def test_rate_limit_markers_are_tuples(self):
        assert isinstance(RATE_LIMIT_MARKERS, tuple)
        assert len(RATE_LIMIT_MARKERS) >= 3

    def test_no_duplicate_markers(self):
        """确保各标记集合之间无重叠导致分类优先级异常。"""
        all_account = set(ACCOUNT_ERROR_MARKERS)
        all_mailbox = set(MAILBOX_ERROR_MARKERS)
        all_network = set(NETWORK_ERROR_MARKERS)
        all_auth = set(AUTH_STATE_ERROR_MARKERS)
        all_rate = set(RATE_LIMIT_MARKERS)
        # 检查交集
        overlap = (
            all_account & all_mailbox
            | all_account & all_network
            | all_account & all_auth
            | all_account & all_rate
            | all_mailbox & all_network
            | all_mailbox & all_auth
            | all_mailbox & all_rate
            | all_network & all_auth
            | all_network & all_rate
            | all_auth & all_rate
        )
        # 允许 "invalid code" 在 mailbox 和 account 同时出现（不同上下文）
        allowed_duplicates = {"invalid code", "wrong_email_otp_code"}
        actual = overlap - allowed_duplicates
        assert not actual, f"标记集合之间有重叠: {actual}"