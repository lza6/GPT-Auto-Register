"""v4.0 P1-8/9：think_time 人类节奏 + TLS 瞬断重试。"""
from __future__ import annotations

import time

import pytest

from services.protocol_register import ProtocolRegister


class TestTlsRetry:
    def test_transient_tls_error_retries(self, monkeypatch):
        reg = ProtocolRegister({"tls_retries": 2})
        calls = {"n": 0}

        def fn(*a, **k):
            calls["n"] += 1
            if calls["n"] < 2:
                raise OSError("curl: (35) TLS connect error")
            return "ok"
        wrapped = reg._tls_retry_wrap(fn)
        assert wrapped() == "ok"
        assert calls["n"] == 2

    def test_persistent_tls_error_raises(self, monkeypatch):
        reg = ProtocolRegister({"tls_retries": 2})
        calls = {"n": 0}

        def fn(*a, **k):
            calls["n"] += 1
            raise OSError("TLS connect error")
        wrapped = reg._tls_retry_wrap(fn)
        with pytest.raises(OSError):
            wrapped()
        assert calls["n"] == 3  # 1 次原始 + 2 次重试

    def test_http_error_not_retried(self):
        reg = ProtocolRegister({"tls_retries": 2})
        calls = {"n": 0}

        def fn(*a, **k):
            calls["n"] += 1
            raise RuntimeError("HTTP 429 too many")
        wrapped = reg._tls_retry_wrap(fn)
        with pytest.raises(RuntimeError):
            wrapped()
        assert calls["n"] == 1  # 业务/HTTP 错不重试（避免像异常流量）

    def test_disabled_no_retry(self):
        reg = ProtocolRegister({"tls_retries": 0})
        calls = {"n": 0}

        def fn(*a, **k):
            calls["n"] += 1
            raise OSError("TLS error")
        wrapped = reg._tls_retry_wrap(fn)
        with pytest.raises(OSError):
            wrapped()
        assert calls["n"] == 1

    def test_looks_tls_error(self):
        assert ProtocolRegister._looks_tls_error(OSError("curl: (35) TLS connect error")) is True
        assert ProtocolRegister._looks_tls_error(OSError("connection reset")) is True
        assert ProtocolRegister._looks_tls_error(RuntimeError("sentinel_req_failed_500")) is False


class TestThinkTime:
    def test_disabled_no_sleep(self, monkeypatch):
        reg = ProtocolRegister({"think_time_ms": 0})
        slept = []
        monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
        reg._sleep_think_time()
        assert slept == []

    def test_enabled_sleeps_in_range(self, monkeypatch):
        reg = ProtocolRegister({"think_time_ms": 1000})
        slept = []
        monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
        reg._sleep_think_time()
        assert len(slept) == 1
        # 1000ms 的 0.5~1.5 倍 = 0.5s~1.5s
        assert 0.4 <= slept[0] <= 1.6

    def test_default_enabled(self):
        reg = ProtocolRegister({})
        assert reg._think_time_ms > 0
