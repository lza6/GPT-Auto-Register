"""services/two_factor_service — TOTP 2FA 快路径（mock session）。"""
from __future__ import annotations

import json

from services.two_factor_service import (
    enroll_totp,
    hotp,
    totp_now,
    verify_totp,
)


class FakeResp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code = status
        self._data = data if data is not None else {}
        self.text = text if text else json.dumps(self._data)

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, get_map=None, post_map=None):
        self._get_map = get_map or {}
        self._post_map = post_map or {}
        self.posted = []

    def get(self, url, **kwargs):
        for key, resp in self._get_map.items():
            if key in url:
                return resp
        return FakeResp(404)

    def post(self, url, **kwargs):
        self.posted.append((url, kwargs.get("json")))
        for key, resp in self._post_map.items():
            if key in url:
                return resp
        return FakeResp(404)


class TestRfc6238:
    def test_hotp_matches_rfc6238_totp_vectors(self):
        # RFC 6238 附录 B 官方向量（SHA1，TOTP counter 从 T=1 起）
        assert hotp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", 1, 8) == "94287082"
        # T(1111111109) = 1111111109 // 30 = 37037036
        assert hotp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", 37037036, 8) == "07081804"

    def test_totp_now_is_6_digits(self):
        code = totp_now("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ")
        assert len(code) == 6 and code.isdigit()

    def test_verify_totp_accepts_current(self):
        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        assert verify_totp(secret, totp_now(secret)) is True


class TestEnrollTotp:
    def _session(self, *, already=False, enroll_ok=True, activate_ok=True):
        if already:
            mfa = FakeResp(200, {"mfa_enabled": True, "factors": {"totp": {"id": "f1"}}})
        else:
            mfa = FakeResp(200, {"mfa_enabled": False, "factors": {}})
        enroll = FakeResp(200, {"secret": "JBSWY3DPEHPK3PXP", "session_id": "sess-1",
                                "factor": {"id": "factor-1"}}) if enroll_ok else FakeResp(500, {"error": "x"})
        activate = FakeResp(200, {"success": True}) if activate_ok else FakeResp(429, {"error": "rate"})
        return FakeSession(
            get_map={"mfa_info": mfa},
            post_map={"mfa/enroll": enroll, "activate_enrollment": activate},
        )

    def test_success_returns_secret(self):
        session = self._session()
        result = enroll_totp(session, "at-1")
        assert result["secret"] == "JBSWY3DPEHPK3PXP"
        assert result["factor_id"] == "factor-1"
        assert result["session_id"] == "sess-1"
        # enroll body 是 totp
        assert any("mfa/enroll" in u and j.get("factor_type") == "totp" for u, j in session.posted)
        assert any("activate_enrollment" in u and j.get("code") and j.get("session_id") == "sess-1"
                   for u, j in session.posted)

    def test_already_enrolled_skips(self):
        session = self._session(already=True)
        result = enroll_totp(session, "at-1")
        assert result is None
        # 不应发 enroll 请求
        assert not any("mfa/enroll" in u for u, _ in session.posted)

    def test_enroll_failure_returns_none(self):
        session = self._session(enroll_ok=False)
        assert enroll_totp(session, "at-1") is None

    def test_activate_failure_returns_none(self):
        session = self._session(activate_ok=False)
        assert enroll_totp(session, "at-1") is None

    def test_network_error_returns_none(self):
        session = FakeSession()

        def boom(*a, **k):
            raise OSError("connection reset")
        session.get = boom
        assert enroll_totp(session, "at-1") is None
