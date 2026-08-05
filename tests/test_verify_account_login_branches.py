"""verify_account_login.run 分流回归测试 — 防止 CF 页 / unknown 页裸超时 180s。

C1 修复：refresh_all.py 每号 180s 强杀，根因是 verify.run() 没复用
browser_register 的 detect_login_page / _resolve_cf 形态分流，遇到 CF 挑战页
或 OpenAI 改版页就裸等密码框 / 裸等 OAuth code 直到被强杀。

本测试用 FakeBrowser 套件模拟三种卡死场景，断言每种都明确早退（不卡到 180s）：
  - CF 挑战页 → 返回码 3 (cf_blocked)
  - unknown 页（无 email/password/code input）→ 返回码 4 (unknown_page)
  - 密码页改版（无 current-password input 但有 password）→ 正常进入或明确失败，不裸超时
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import camoufox.async_api as camoufox_api


# ── 可配置假浏览器 ──
class VFakeLocator:
    def __init__(self, sel: str = ""):
        self.sel = sel

    @property
    def first(self):
        return self

    async def count(self):
        return 1 if "type=\"submit\"" in self.sel else 0

    async def click(self, **kwargs):
        return None

    async def fill(self, value):
        return None

    async def wait_for(self, state="visible", timeout=0):
        # 仅密码框可见，其余不可见 → 触发 verify 的 "未到密码页" 早退
        if self.sel == 'input[name="current-password"]':
            return None
        raise TimeoutError(f"locator({self.sel!r}) not visible")

    async def is_visible(self):
        return self.sel == 'input[name="current-password"]'


class VFakeKeyboard:
    async def type(self, text, delay=0):
        return None

    async def press(self, key):
        return None


class VFakePage:
    """可编程假页面：url / html / inputs / body 可由测试注入。"""

    def __init__(self, url: str = "https://auth.openai.com/", html: str = "",
                 inputs: list[str] | None = None, body: str = ""):
        self._url = url
        self._html = html
        self._inputs = inputs or []
        self._body = body
        self.keyboard = VFakeKeyboard()
        self.frames: list = []

    @property
    def url(self):
        return self._url

    def locator(self, sel):
        return VFakeLocator(sel)

    async def goto(self, *args, **kwargs):
        return None

    async def content(self):
        return self._html

    async def evaluate(self, js):
        # _collect_page_inputs 用 evaluate 取 input 列表；body 文字也用 evaluate 取
        if "Array.from(document.querySelectorAll('input'))" in js:
            return self._inputs
        return self._body[:200]

    async def screenshot(self, path):
        return None


class VFakeContext:
    def __init__(self, page: VFakePage):
        self._page = page

    async def new_page(self):
        return self._page

    async def close(self):
        return None

    async def cookies(self):
        return []


class VFakeBrowser:
    def __init__(self, page: VFakePage):
        self._ctx = VFakeContext(page)

    async def new_context(self, **kwargs):
        return self._ctx

    async def close(self):
        return None


class VFakeCamoufox:
    def __init__(self, page: VFakePage, **kwargs):
        self._browser = VFakeBrowser(page)

    async def start(self):
        return self._browser

    async def stop(self):
        return None


@pytest.fixture
def fast_asyncio(monkeypatch):
    """加速 asyncio.sleep，否则 verify.run 的 sleep(6) 累计拖慢测试。"""
    import asyncio

    async def _fast_sleep(sec):
        return None

    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)


def _patch_camoufox(monkeypatch, page: VFakePage):
    """让 verify.run 内部 `from camoufox.async_api import AsyncCamoufox` 拿到假实现。"""
    import services.browser_register as br

    def _factory(**kwargs):
        return VFakeCamoufox(page, **kwargs)

    monkeypatch.setattr(camoufox_api, "AsyncCamoufox", _factory)
    # verify.run 用 `from camoufox.async_api import AsyncCamoufox` 动态导入，
    # 必须同时 patch camoufox.async_api.AsyncCamoufox 与 sys.modules 中的引用。
    monkeypatch.setattr("camoufox.async_api.AsyncCamoufox", _factory, raising=False)
    return _factory


class TestVerifyRunBranches:
    """verify.run 在三种卡死场景下必须明确早退，不裸超时。"""

    async def test_cf_challenge_returns_cf_blocked_code(
        self, isolated_db, fast_asyncio, monkeypatch
    ):
        from scripts.verify_account_login import run

        page = VFakePage(
            url="https://auth.openai.com/",
            html="Just a moment... checking your browser (Cloudflare turnstile)",
            inputs=[],  # CF 页无可见 input
            body="Just a moment...",
        )
        _patch_camoufox(monkeypatch, page)

        # _resolve_cf 失败（模拟点不掉 Turnstile）
        from services.browser_register import BrowserRegister
        original_resolve = BrowserRegister._resolve_cf

        async def _fail_resolve(self, page, email, max_attempts=3):
            return False

        monkeypatch.setattr(BrowserRegister, "_resolve_cf", _fail_resolve)

        rc = await run(
            "u@outlook.com", "openaipw", "mspw", "cid", "rt", proxy_url=None, headful=False
        )
        # 断言：CF 页明确返回 cf_blocked 码 3，而不是卡到 180s 被强杀
        assert rc == 3, f"CF 页应返回 3 (cf_blocked)，实际 {rc}"

    async def test_unknown_page_returns_unknown_code(
        self, isolated_db, fast_asyncio, monkeypatch
    ):
        from scripts.verify_account_login import run

        page = VFakePage(
            url="https://auth.openai.com/some-new-page",
            html="<html><body>Sorry, something went wrong</body></html>",
            inputs=[],  # 无 email/password/code input → unknown
            body="Sorry, something went wrong",
        )
        _patch_camoufox(monkeypatch, page)

        rc = await run(
            "u@outlook.com", "openaipw", "mspw", "cid", "rt", proxy_url=None, headful=False
        )
        # 断言：unknown 页明确返回码 4，而不是裸等密码框 20s
        assert rc == 4, f"unknown 页应返回 4 (unknown_page)，实际 {rc}"

    async def test_password_page_normal_flow(self, isolated_db, fast_asyncio, monkeypatch):
        """密码页（current-password 可见）走原有逻辑，code 捕不到明确失败返回 1。"""
        from scripts.verify_account_login import run

        page = VFakePage(
            url="https://auth.openai.com/log-in",
            html="<input name='current-password' type='password'>",
            inputs=["current-password"],
            body="Sign in",
        )
        _patch_camoufox(monkeypatch, page)

        # _wait_for_oauth_code 永远捕不到 code（模拟 2 轮循环走满）
        from services.browser_register import BrowserRegister

        async def _no_code(self, page, timeout=15):
            return ""

        monkeypatch.setattr(BrowserRegister, "_wait_for_oauth_code", _no_code)

        rc = await run(
            "u@outlook.com", "openaipw", "mspw", "cid", "rt", proxy_url=None, headful=False
        )
        # 走满 2 轮 + 无 code → 原逻辑返回 1（失败），不卡 180s
        assert rc == 1, f"密码页无 code 应返回 1，实际 {rc}"
