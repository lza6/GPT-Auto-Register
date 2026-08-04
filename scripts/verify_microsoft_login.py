#!/usr/bin/env python3
"""用邮箱账号密码登录微软账户 (login.live.com)，验证自动注册使用的邮箱是否真实可用。

账号密码能登录微软账户 → 邮箱账号真实可用 → 程序自动注册用的邮箱有效。
"""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")


def _pick_account(email: str | None) -> dict:
    conn = sqlite3.connect(str(ROOT / "data" / "register.db"))
    conn.row_factory = sqlite3.Row
    if email:
        row = conn.execute(
            "SELECT email, password FROM accounts WHERE email = ?", (email,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT email, password FROM accounts WHERE status='success' "
            "AND access_token LIKE 'eyJ%' AND length(access_token) > 200 ORDER BY id LIMIT 1"
        ).fetchone()
    conn.close()
    if not row:
        print("[错误] 未找到可用账号")
        sys.exit(1)
    return dict(row)


async def run(email: str, password: str, headful: bool) -> int:
    from camoufox.async_api import AsyncCamoufox
    from camoufox import DefaultAddons

    camoufox = AsyncCamoufox(
        headless=not headful,
        exclude_addons=[DefaultAddons.UBO],
        args=["--no-sandbox", "--disable-setuid-sandbox"],
    )
    browser = await camoufox.start()
    context = await browser.new_context()
    page = await context.new_page()

    try:
        print(f"[1/5] 导航 login.live.com...")
        await page.goto("https://login.live.com/", wait_until="networkidle", timeout=60000)
        await asyncio.sleep(3)

        # 输入邮箱
        print("[2/5] 输入邮箱...")
        email_input = page.locator('input[type="email"], #i0116').first
        await email_input.wait_for(state="visible", timeout=15000)
        await email_input.click()
        await email_input.fill("")
        await page.keyboard.type(email, delay=30)
        await asyncio.sleep(0.5)
        try:
            await page.locator('button[type="submit"], input[type="submit"], #idSIButton9').first.click(timeout=8000)
        except Exception:
            await page.keyboard.press("Enter")
        await asyncio.sleep(3)

        # 判断邮箱是否正确（错误则直接结束）
        body = await page.evaluate("() => document.body ? document.body.innerText : ''") or ""
        if "find your account" in body.lower() or "doesn't exist" in body.lower() \
                or "That Microsoft account doesn't exist" in body:
            print("❌ 邮箱账号不存在，微软账户不可用。")
            return 1

        # 输入密码
        print("[3/5] 输入密码...")
        pwd_input = page.locator('input[type="password"], #i0118').first
        try:
            await pwd_input.wait_for(state="visible", timeout=15000)
        except Exception:
            body = await page.evaluate("() => document.body ? document.body.innerText : ''") or ""
            print(f"❓ 未进入密码页, URL: {page.url[:100]}")
            print(f"   页面文字: {body[:200]}")
            return 1
        await pwd_input.click()
        await pwd_input.fill("")
        await page.keyboard.type(password, delay=30)
        await asyncio.sleep(0.5)
        try:
            await page.locator('button[type="submit"], input[type="submit"], #idSIButton9').first.click(timeout=8000)
        except Exception:
            await page.keyboard.press("Enter")

        # 等待登录结果
        print("[4/5] 等待登录结果...")
        await asyncio.sleep(8)

        body = await page.evaluate("() => document.body ? document.body.innerText : ''") or ""
        low = body.lower()
        cur = page.url

        # 密码错误
        if "incorrect" in low or "password error" in low or "your account or password is incorrect" in low:
            print("❌ 密码错误，无法登录微软账户。")
            print("   说明: 该邮箱的密码与数据库存储不符，或邮箱密码已被修改。")
            return 1
        # 账号锁定/安全
        if "locked" in low or "security" in low and "verify" in low:
            print("⚠️ 账号被锁定或要求安全验证。")
            return 2

        # 登录成功判断
        success = False
        try:
            # 出现 "Stay signed in?" 或跳转到账户页
            kmsi = page.locator('#KmsiTitle, input[value="No"], input[value="Yes"]').first
            if await kmsi.count() > 0:
                success = True
        except Exception:
            pass
        if "account.microsoft.com" in cur or "mysignins" in cur or "login.live.com/oauth20" in cur:
            success = True

        print("[5/5] 结果...")
        print("=" * 60)
        if success:
            print(f"✅ 账号密码登录成功！邮箱 {email} 真实可用。")
            print("   结论: 程序自动注册使用的邮箱账号有效。")
            return 0
        print(f"❓ 登录结果不明。URL: {cur[:100]}")
        print(f"   页面文字: {body[:250]}")
        await page.screenshot(path=str(ROOT / "ms_login_debug.png"))
        return 1
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[异常] {e}")
        return 1
    finally:
        try:
            await context.close()
        except Exception:
            pass
        try:
            await browser.close()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="验证微软邮箱账号密码能否登录")
    parser.add_argument("--email", default="", help="指定账号邮箱")
    parser.add_argument("--headful", action="store_true", help="显示浏览器窗口")
    args = parser.parse_args()

    acc = _pick_account(args.email or None)
    print(f"使用账号: {acc['email']}")
    print(f"密码: {acc['password'][:4]}*** (长度 {len(acc['password'])})")

    exit_code = asyncio.run(run(acc["email"], acc["password"], args.headful))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
