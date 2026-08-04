#!/usr/bin/env python3
"""用代理从 session API 获取真正的 JWT access_token。

用法：
    python scripts/refresh_tokens.py [--proxy http://127.0.0.1:10808]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

import httpx

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "register.db"
TOKEN_FILE = Path(__file__).resolve().parent.parent / "已经获取到的token.txt"


def get_accounts() -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM accounts WHERE status IN ('success', 'success_no_token') ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_token(email: str, access_token: str) -> None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "UPDATE accounts SET access_token = ?, status = 'success' WHERE email = ?",
        (access_token, email),
    )
    conn.commit()
    conn.close()


def fetch_token_with_browser(email: str, password: str, proxy_url: str | None) -> str | None:
    """用浏览器访问 chatgpt.com 获取真正的 access_token"""
    try:
        from camoufox.sync_api import SyncCamoufox
        from camoufox import DefaultAddons
    except ImportError:
        print("camoufox 未安装")
        return None

    camoufox = None
    browser = None
    try:
        camoufox = SyncCamoufox(
            headless=True,
            exclude_addons=[DefaultAddons.UBO],
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        browser = camoufox.start()

        context_kwargs = {}
        if proxy_url:
            from urllib.parse import urlparse
            parsed = urlparse(proxy_url)
            if parsed.hostname:
                server = f"{parsed.scheme or 'http'}://{parsed.hostname}:{parsed.port or 1000}"
                if parsed.username and parsed.password:
                    context_kwargs["proxy"] = {
                        "server": server,
                        "username": parsed.username,
                        "password": parsed.password,
                    }
                else:
                    context_kwargs["proxy"] = {"server": server}

        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        # 访问登录页面
        page.goto("https://chatgpt.com/auth/login", wait_until="networkidle", timeout=60000)
        time.sleep(3)

        # 关闭 cookie 弹窗
        try:
            cookie_reject = page.locator('button:has-text("Reject optional")').first
            if cookie_reject.count() > 0:
                cookie_reject.click()
                time.sleep(2)
        except Exception:
            pass

        # 输入邮箱
        email_input = page.locator('input[name="email"]')
        email_input.wait_for(state="visible", timeout=15000)
        email_input.click()
        email_input.fill("")
        page.keyboard.type(email, delay=50)
        time.sleep(1)

        # 点击 Continue
        continue_btn = page.locator('button[type="submit"]').first
        continue_btn.click()
        time.sleep(5)

        # 检查是否到达验证码页面
        current_url = page.url
        if "email-verification" not in current_url:
            print(f"  {email}: 未到达验证码页面，当前: {current_url[:60]}")
            page.close()
            context.close()
            return None

        # 等待验证码邮件（用 98faka API）
        from services.email_service import email_service
        otp_code = None
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            otp_code = loop.run_until_complete(
                email_service.wait_for_otp(
                    email, password, "", "",
                    timeout_sec=300,
                    poll_interval=5,
                    skip_existing=False,
                )
            )
        finally:
            loop.close()

        if not otp_code:
            print(f"  {email}: 验证码等待超时")
            page.close()
            context.close()
            return None

        # 输入验证码
        code_input = page.locator('input[name="code"]')
        code_input.wait_for(state="visible", timeout=10000)
        code_input.click()
        code_input.fill("")
        page.keyboard.type(otp_code, delay=100)
        time.sleep(0.5)

        # 点击 Continue
        verify_btn = page.locator('button[type="submit"]').first
        verify_btn.click()
        time.sleep(5)

        # 检查是否到达 about-you 页面
        current_url = page.url
        if "about-you" not in current_url:
            print(f"  {email}: 未到达 about-you 页面，当前: {current_url[:60]}")
            page.close()
            context.close()
            return None

        # 填写姓名和生日（用已有数据）
        # 这里不需要重新填写，因为我们只是要获取 token
        # 直接跳到最后一步获取 session

        # 从 session API 获取 token
        time.sleep(5)
        session_resp = page.request.get("https://chatgpt.com/api/auth/session", timeout=15000)
        if session_resp.status == 200:
            session_data = session_resp.json()
            access_token = session_data.get("accessToken", "")
            if access_token:
                page.close()
                context.close()
                return access_token

        # 从 cookie 中提取 JWT token
        cookies = context.cookies()
        for cookie in cookies:
            value = cookie.get("value", "")
            if value.startswith("eyJ") and len(value) > 200:
                page.close()
                context.close()
                return value

        page.close()
        context.close()
        return None

    except Exception as e:
        print(f"  {email}: 浏览器获取 token 异常: {e}")
        return None
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if camoufox:
            try:
                camoufox.stop()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="用代理获取真正的 JWT access_token")
    parser.add_argument("--proxy", default="http://127.0.0.1:10808", help="代理地址")
    parser.add_argument("--limit", type=int, default=0, help="限制处理数量（0=全部）")
    args = parser.parse_args()

    print("读取注册账号...")
    accounts = get_accounts()
    print(f"共 {len(accounts)} 个账号")

    # 只处理 HEX token 的账号（需要重新获取 JWT）
    hex_accounts = [a for a in accounts if a.get("access_token") and not a["access_token"].startswith("eyJ")]
    print(f"其中 HEX token 账号: {len(hex_accounts)} 个（需要重新获取 JWT）")

    if args.limit > 0:
        hex_accounts = hex_accounts[:args.limit]
        print(f"本次处理: {len(hex_accounts)} 个")

    success = 0
    failed = 0
    for acc in hex_accounts:
        email = acc["email"]
        password = acc.get("password", "")
        print(f"\n处理 {email}...")

        token = fetch_token_with_browser(email, password, args.proxy)
        if token:
            update_token(email, token)
            success += 1
            print(f"  ✅ 获取成功: {token[:50]}...")
        else:
            failed += 1
            print(f"  ❌ 获取失败")

        time.sleep(5)  # 间隔

    print(f"\n完成: 成功 {success}, 失败 {failed}")

    # 更新 token 文件
    accounts = get_accounts()
    jwt_tokens = [a["access_token"] for a in accounts if a.get("access_token", "").startswith("eyJ")]
    TOKEN_FILE.write_text("\n".join(jwt_tokens) + "\n", encoding="utf-8")
    print(f"已更新 token 文件: {len(jwt_tokens)} 个 JWT token")


if __name__ == "__main__":
    main()
