"""browser_register 页面分流 — mock 浏览器验证关键分支（C1 修复回归测试）。

重点验证：unknown 形态明确报错（而非裸超时）、email 形态能进入验证码阶段、
CF 形态走 cf_blocked 降级。
"""
from __future__ import annotations

import pytest

import camoufox.async_api as camoufox_api

from services.browser_register import BrowserRegister


# ── 可配置假浏览器套件 ──
class FakeLocator:
    def __init__(self, sel: str = ""):
        self.sel = sel

    @property
    def first(self):
        return self

    async def count(self):
        # 仅 submit 按钮可见；其余选择器不可见
        return 1 if "type=\"submit\"" in self.sel else 0

    async def click(self, **kwargs):
        return None

    async def fill(self, value):
        return None

    async def wait_for(self, state="visible", timeout=0):
        visible_sels = ('input[name="email"]', 'input[name="code"]', 'button[type="submit"]')
        if self.sel in visible_sels:
            return None
        raise TimeoutError(f"locator({self.sel!r}) not visible")


class FakeKeyboard:
    async def type(self, text, delay=0):
        return None

    async def press(self, key):
        return None


class FakePage:
    def __init__(self, url: str = "https://auth.openai.com/"):
        self._url = url
        self.frames = []
        self.keyboard = FakeKeyboard()

    @property
    def url(self):
        return self._url

    def locator(self, sel):
        return FakeLocator(sel)

    async def goto(self, *args, **kwargs):
        return None

    async def reload(self, **kwargs):
        return None

    async def screenshot(self, path):
        return None

    async def wait_for_url(self, pattern, timeout=0):
        # 模拟页面跳转成功
        self._url = "https://auth.openai.com/email-verification"
        return None

    async def content(self):
        return ""

    async def evaluate(self, js):
        return []

    @property
    def request(self):
        class FakeRequest:
            async def get(self, *a, **k):
                class R:
                    status = 404
                return R()
        return FakeRequest()


class FakeContext:
    def __init__(self):
        self.page = FakePage()

    async def new_page(self):
        return self.page

    async def close(self):
        return None

    async def cookies(self):
        return []


class FakeBrowser:
    def __init__(self):
        self.context = FakeContext()

    async def new_context(self, **kwargs):
        return self.context

    async def close(self):
        return None


class FakeCamoufox:
    def __init__(self, **kwargs):
        self._browser = FakeBrowser()

    async def start(self):
        return self._browser

    async def stop(self):
        return None


@pytest.fixture
def fake_browser_env(monkeypatch):
    """把真实 camoufox / 邮件服务替换为假实现，并加速 asyncio.sleep（浏览器节奏模拟）。

    monkeypatch asyncio.sleep 仅在当前测试事件循环内生效（pytest-asyncio 自动恢复）。
    """
    import asyncio

    async def _fast_sleep(sec):
        return None

    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

    monkeypatch.setattr(camoufox_api, "AsyncCamoufox", FakeCamoufox)
    import services.browser_register as br

    monkeypatch.setattr(br, "graph_email_service", _FakeGraph())


class _FakeGraph:
    async def get_email_list(self, *a, **k):
        return []

    async def get_email_body(self, *a, **k):
        return ""

    def extract_otp_code(self, body):
        return None


def _make_register():
    return BrowserRegister({"otp_wait_timeout_sec": 30, "otp_poll_interval_sec": 5})


class TestRegisterOneBranches:
    async def test_unknown_page_returns_clear_error(self, isolated_db, fake_browser_env, monkeypatch):
        reg = _make_register()

        async def inputs(page):
            return []
        async def html(page):
            return ""
        async def body(page):
            return "Sorry, something went wrong"

        monkeypatch.setattr(reg, "_collect_page_inputs", inputs)
        monkeypatch.setattr(reg, "_page_html", html)
        monkeypatch.setattr(reg, "_page_body_text", body)

        result = await reg.register_one("u@example.com", "p", "cid", "rt")
        assert result["status"] == "failed"
        assert "无法识别登录页" in result["error"]

    async def test_email_page_reaches_otp_stage(self, isolated_db, fake_browser_env, monkeypatch):
        reg = _make_register()

        async def inputs(page):
            return ["email"]
        async def html(page):
            return ""

        monkeypatch.setattr(reg, "_collect_page_inputs", inputs)
        monkeypatch.setattr(reg, "_page_html", html)

        otp_calls = {"n": 0}

        async def fake_wait_otp(*a, **k):
            otp_calls["n"] += 1
            return None  # 验证码超时 → 提前 return

        monkeypatch.setattr(reg, "_wait_for_new_otp", fake_wait_otp)

        result = await reg.register_one("u@example.com", "p", "cid", "rt")
        # 分流正确进入验证码阶段：_wait_for_new_otp 被调用，且因超时明确报错
        assert otp_calls["n"] == 1
        assert result["status"] == "failed"
        assert "验证码等待超时" in result["error"]

    async def test_cf_page_returns_cf_blocked(self, isolated_db, fake_browser_env, monkeypatch):
        reg = _make_register()

        async def inputs(page):
            return []
        async def html(page):
            return "Just a moment..."
        async def no_resolve(page, email, max_attempts=3):
            return False  # 模拟解挑战失败

        monkeypatch.setattr(reg, "_collect_page_inputs", inputs)
        monkeypatch.setattr(reg, "_page_html", html)
        monkeypatch.setattr(reg, "_resolve_cf", no_resolve)

        result = await reg.register_one("u@example.com", "p", "cid", "rt")
        assert result["status"] == "cf_blocked"
        assert "Cloudflare" in result["error"]
