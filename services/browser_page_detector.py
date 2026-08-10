"""页面形态检测与 CF 处理（browser_register 子模块）。

提取自 browser_register.py 的页面检测/CF 挑战逻辑，保持向后兼容。
"""
from __future__ import annotations

import asyncio
from typing import Any

from services.db import add_log


async def collect_page_inputs(page) -> list[str]:
    """采集页面可见 input 的 name/type/placeholder/autocomplete。"""
    try:
        return await page.evaluate(
            """() => Array.from(document.querySelectorAll('input'))
                .map(i => (i.name || i.type || i.placeholder || i.autocomplete) || '')
                .filter(Boolean).slice(0, 20)"""
        )
    except Exception:
        return []


async def page_html(page) -> str:
    """获取页面 HTML 内容。"""
    try:
        return await page.content()
    except Exception:
        return ""


async def page_body_text(page) -> str:
    """获取页面 body 文本（前 500 字符）。"""
    try:
        return await page.evaluate(
            "() => document.body ? document.body.innerText.slice(0, 500) : ''"
        )
    except Exception:
        return ""


async def try_click_turnstile(page) -> bool:
    """遍历 frame 尝试点掉 Turnstile 复选框，返回是否点中。"""
    try:
        for frame in page.frames:
            url = (frame.url or "").lower()
            if "turnstile" in url or "challenges.cloudflare" in url:
                checkbox = frame.locator(
                    "#challenge-stage input[type=checkbox], input[type=checkbox]"
                ).first
                if await checkbox.count():
                    await checkbox.click(timeout=1500)
                    return True
    except Exception:
        pass
    return False


async def resolve_cf(page, email: str, max_attempts: int = 3) -> bool:
    """尝试自动解 Cloudflare 挑战（点复选框 + 调 CF solver 拿 token 兜底）。"""
    from services.login_detector import is_cf_challenge
    from services.cf_solver_service import cf_solver_service

    for attempt in range(max_attempts):
        clicked = await try_click_turnstile(page)
        if clicked:
            add_log("info", f"[{email}] 点击了 Turnstile 复选框 (第 {attempt + 1} 次)")
            await asyncio.sleep(4)
        if not is_cf_challenge(page.url, await page_html(page)):
            return True
        # v3.4 T87：复选框解不开时调 CF solver 拿 Turnstile token
        if not clicked:
            try:
                sitekey = await page.evaluate(
                    """() => {
                        const el = document.querySelector('[data-sitekey]');
                        return el ? el.getAttribute('data-sitekey') : '';
                    }"""
                )
                if sitekey:
                    add_log("info", f"[{email}] 调用 CF solver 解 Turnstile (sitekey={sitekey[:8]}...)")
                    solver_result = await cf_solver_service.get_turnstile_token(page.url, sitekey, timeout=60)
                    if solver_result.get("status") == "success" and solver_result.get("value"):
                        token = solver_result["value"]
                        await page.evaluate(f"""() => {{
                            const el = document.querySelector('[name="cf-turnstile-response"]');
                            if (el) el.value = '{token}';
                        }}""")
                        await asyncio.sleep(3)
                        if not is_cf_challenge(page.url, await page_html(page)):
                            add_log("info", f"[{email}] CF solver 成功解 Turnstile")
                            return True
            except Exception as e:
                add_log("warning", f"[{email}] CF solver 调用异常: {e}")
        await asyncio.sleep(3)
    return False