"""register_engine.register_one — 协议优先、浏览器兜底的降级决策测试。"""
from __future__ import annotations

from services.register_engine import RegisterEngine


class FakeProto:
    def __init__(self, result):
        self.result = result

    async def register_one(self, *a, **k):
        return self.result


class FakeBrowser:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def register_one(self, *a, **k):
        self.calls += 1
        return self.result


def _ok(result_kwargs=None):
    base = {"email": "x@y.com", "status": "success", "access_token": "eyJat",
            "refresh_token": "eyJrt", "id_token": "eyJit", "openai_password": "",
            "name": "n", "birthdate": "2000-01-01", "proxy": "直连", "error": "",
            "fallback_browser": False}
    base.update(result_kwargs or {})
    return base


def _fail(error="boom", fallback=False):
    return {"email": "x@y.com", "status": "failed", "error": error,
            "access_token": "", "refresh_token": "", "id_token": "",
            "openai_password": "", "name": "", "birthdate": "",
            "proxy": "直连", "fallback_browser": fallback}


class TestProtocolFirstFallback:
    async def test_protocol_success_does_not_use_browser(self, monkeypatch):
        eng = RegisterEngine({})
        proto = FakeProto(_ok())
        browser = FakeBrowser(_ok())
        monkeypatch.setattr("services.protocol_register.get_protocol_register", lambda cfg: proto)
        monkeypatch.setattr("services.browser_register.get_browser_register", lambda cfg: browser)

        result = await eng.register_one("x@y.com", "p", "c", "r")
        assert result["status"] == "success"
        assert result["access_token"] == "eyJat"
        assert browser.calls == 0

    async def test_protocol_failure_falls_back_to_browser(self, monkeypatch):
        eng = RegisterEngine({})
        proto = FakeProto(_fail(error="authorize HTTP 500", fallback=True))
        browser = FakeBrowser(_ok(result_kwargs={"access_token": "eyJbrowser"}))
        monkeypatch.setattr("services.protocol_register.get_protocol_register", lambda cfg: proto)
        monkeypatch.setattr("services.browser_register.get_browser_register", lambda cfg: browser)

        result = await eng.register_one("x@y.com", "p", "c", "r")
        assert result["status"] == "success"
        assert result["access_token"] == "eyJbrowser"
        assert browser.calls == 1

    async def test_business_failure_no_fallback(self, monkeypatch):
        # 风控/账号级失败：浏览器同样被拒，不降级
        eng = RegisterEngine({})
        proto = FakeProto(_fail(error="create_account 失败: account_deactivated", fallback=False))
        browser = FakeBrowser(_ok())
        monkeypatch.setattr("services.protocol_register.get_protocol_register", lambda cfg: proto)
        monkeypatch.setattr("services.browser_register.get_browser_register", lambda cfg: browser)

        result = await eng.register_one("x@y.com", "p", "c", "r")
        assert result["status"] == "failed"
        assert "deactivated" in result["error"]
        assert browser.calls == 0

    async def test_no_fallback_when_browser_disabled(self, monkeypatch):
        eng = RegisterEngine({"use_browser": False})
        proto = FakeProto(_fail(error="authorize HTTP 500", fallback=True))
        browser = FakeBrowser(_ok())
        monkeypatch.setattr("services.protocol_register.get_protocol_register", lambda cfg: proto)
        monkeypatch.setattr("services.browser_register.get_browser_register", lambda cfg: browser)

        result = await eng.register_one("x@y.com", "p", "c", "r")
        assert result["status"] == "failed"
        assert browser.calls == 0

    async def test_browser_mode_when_protocol_disabled(self, monkeypatch):
        eng = RegisterEngine({"protocol_first": False})
        browser = FakeBrowser(_ok(result_kwargs={"access_token": "eyJbrowser"}))
        monkeypatch.setattr("services.browser_register.get_browser_register", lambda cfg: browser)

        result = await eng.register_one("x@y.com", "p", "c", "r")
        assert result["status"] == "success"
        assert result["access_token"] == "eyJbrowser"
        assert browser.calls == 1

    async def test_protocol_first_string_false_uses_browser(self, monkeypatch):
        # settings API 把开关存为字符串 'false'，必须解析为布尔假，走浏览器而非协议
        eng = RegisterEngine({"protocol_first": "false"})
        browser = FakeBrowser(_ok(result_kwargs={"access_token": "eyJbrowser"}))
        monkeypatch.setattr("services.browser_register.get_browser_register", lambda cfg: browser)

        def boom(cfg):
            raise AssertionError("protocol_first='false' 时不应调用协议引擎")

        monkeypatch.setattr("services.protocol_register.get_protocol_register", boom)

        result = await eng.register_one("x@y.com", "p", "c", "r")
        assert result["status"] == "success"
        assert browser.calls == 1

    async def test_use_browser_string_false_no_fallback(self, monkeypatch):
        eng = RegisterEngine({"use_browser": "false"})
        proto = FakeProto(_fail(error="boom", fallback=True))
        browser = FakeBrowser(_ok())
        monkeypatch.setattr("services.protocol_register.get_protocol_register", lambda cfg: proto)
        monkeypatch.setattr("services.browser_register.get_browser_register", lambda cfg: browser)

        result = await eng.register_one("x@y.com", "p", "c", "r")
        assert result["status"] == "failed"
        assert browser.calls == 0
