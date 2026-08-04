#!/usr/bin/env python3
"""用 CF solver + camoufox 完成验证码登录，获取真正的 JWT access_token。

流程：
1. CF solver 获取 cf_clearance + cookie
2. 把 cookie 注入 camoufox 浏览器 context
3. 浏览器访问 chatgpt.com（不再被 CF 拦截）
4. 输入邮箱 → 触发发送验证码
5. 98faka API 获取验证码
6. 输入验证码 → 登录成功
7. 从 session API 获取 JWT access_token

用法：
    python scripts/fetch_tokens_browser.py [--proxy http://127.0.0.1:10808] [--limit N]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "register.db"
TOKEN_FILE = Path(__file__).resolve().parent.parent / "已经获取到的token.txt"
CF_SOLVER_URL = "http://127.0.0.1:8001"


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


def get_cf_cookies(url: str = "https://chatgpt.com/", timeout: int = 60) -> dict[str, str]:
    """用 CF solver 获取 cf_clearance 和完整 cookie"""
    import httpx
    try:
        with httpx.Client(timeout=timeout + 30) as client:
            resp = client.get(f"{CF_SOLVER_URL}/clearance", params={"url": url, "timeout": timeout})
            if resp.status_code != 202:
                return {}
            task_id = resp.json().get("task_id")
            if not task_id:
                return {}

            deadline = time.time() + timeout + 30
            while time.time() < deadline:
                time.sleep(2)
                result_resp = client.get(f"{CF_SOLVER_URL}/result", params={"id": task_id})
                if result_resp.status_code == 202:
                    continue
                if result_resp.status_code == 200:
                    result = result_resp.json()
                    cookie_header = result.get("cookies", "")
                    cookies = {}
                    for item in cookie_header.split("; "):
                        if "=" in item:
                            k, v = item.split("=", 1)
                            cookies[k] = v
                    return cookies
                if result_resp.status_code in (408, 422):
                    return {}
            return {}
    except Exception as e:
        print(f"  CF solver 异常: {e}")
        return {}


async def fetch_token_browser(email: str, password: str, proxy_url: str | None,
                               client_id: str = "", refresh_token: str = "") -> str | None:
    """用 CF solver + camoufox 获取真正的 JWT access_token"""
    try:
        from camoufox.async_api import AsyncCamoufox
        from camoufox import DefaultAddons
    except ImportError:
        print("  camoufox 未安装")
        return None

    # Step 0: 用 CF solver 获取 cf_clearance
    print(f"  获取 CF clearance...")
    cf_cookies = get_cf_cookies("https://chatgpt.com/", timeout=60)
    if not cf_cookies:
        print(f"  CF clearance 获取失败")
        return None
    print(f"  CF clearance 获取成功 ({len(cf_cookies)} cookies)")

    camoufox = None
    browser = None
    try:
        camoufox = AsyncCamoufox(
            headless=True,
            exclude_addons=[DefaultAddons.UBO],
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        browser = await camoufox.start()

        # 设置代理
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

        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()

        # 注入 CF cookie
        for name, value in cf_cookies.items():
            await context.add_cookies([{
                "name": name,
                "value": value,
                "domain": ".chatgpt.com",
                "path": "/",
            }])
            await context.add_cookies([{
                "name": name,
                "value": value,
                "domain": "chatgpt.com",
                "path": "/",
            }])

        # Step 1: 访问登录页面
        print(f"  访问登录页面...")
        await page.goto("https://chatgpt.com/auth/login", wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(3)

        # 关闭 cookie 弹窗
        try:
            cookie_reject = page.locator('button:has-text("Reject optional"), button:has-text("Reject")').first
            if await cookie_reject.count() > 0:
                await cookie_reject.click()
                await asyncio.sleep(2)
        except Exception:
            pass

        # Step 2: 输入邮箱（直接 fill，不用 fill("") + type）
        email_input = page.locator('input[name="email"]')
        await email_input.wait_for(state="visible", timeout=15000)
        await email_input.fill(email)
        await asyncio.sleep(1)

        # Step 3: 点击 Continue（触发发送验证码）
        continue_btn = page.locator('button[type="submit"]')
        await continue_btn.wait_for(state="visible", timeout=10000)
        await continue_btn.click()
        print(f"  已提交邮箱，等待页面跳转...")

        # Step 4: 等待验证码页面
        try:
            await page.wait_for_url("**/email-verification**", timeout=30000)
            print(f"  已到达验证码页面")
        except Exception:
            current_url = page.url
            print(f"  未到达验证码页面: {current_url[:60]}")
            if "email-verification" not in current_url:
                await page.close()
                await context.close()
                return None

        # Step 5: 等待验证码（用 98faka API，需要 client_id 和 refresh_token）
        print(f"  等待验证码...")
        from services.email_service import email_service
        otp_code = await email_service.wait_for_otp(
            email, password, client_id, refresh_token,
            timeout_sec=300, poll_interval=5, skip_existing=False,
        )

        if not otp_code:
            print(f"  验证码等待超时")
            await page.close()
            await context.close()
            return None

        # Step 6: 输入验证码
        code_input = page.locator('input[name="code"]')
        await code_input.wait_for(state="visible", timeout=10000)
        await code_input.click()
        await code_input.fill("")
        await page.keyboard.type(otp_code, delay=100)
        await asyncio.sleep(0.5)

        # Step 7: 点击 Continue
        verify_btn = page.locator('button[type="submit"]')
        await verify_btn.wait_for(state="visible", timeout=10000)
        await verify_btn.click()
        print(f"  已提交验证码，等待登录...")

        # Step 8: 等待登录完成
        await asyncio.sleep(8)

        # Step 9: 从 session API 获取 token
        for attempt in range(3):
            try:
                session_resp = await page.request.get("https://chatgpt.com/api/auth/session", timeout=15000)
                if session_resp.status == 200:
                    session_data = await session_resp.json()
                    access_token = session_data.get("accessToken", "")
                    if access_token:
                        await page.close()
                        await context.close()
                        return access_token
            except Exception as e:
                print(f"  session 获取异常 (第 {attempt+1} 次): {e}")
                await asyncio.sleep(3)

        # 从 cookie 中提取 JWT
        cookies = await context.cookies()
        for cookie in cookies:
            value = cookie.get("value", "")
            if value.startswith("eyJ") and len(value) > 200:
                await page.close()
                await context.close()
                return value

        await page.close()
        await context.close()
        return None

    except Exception as e:
        print(f"  异常: {e}")
        return None
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        if camoufox:
            try:
                await camoufox.stop()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="用 CF solver + camoufox 获取真正的 JWT access_token")
    parser.add_argument("--proxy", default="http://127.0.0.1:10808", help="代理地址")
    parser.add_argument("--limit", type=int, default=0, help="限制处理数量（0=全部）")
    args = parser.parse_args()

    print("读取注册账号...")
    accounts = get_accounts()
    print(f"共 {len(accounts)} 个账号")

    # 只处理 HEX token 的账号
    hex_accounts = [a for a in accounts if a.get("access_token") and not a["access_token"].startswith("eyJ")]
    print(f"其中 HEX token 账号: {len(hex_accounts)} 个")

    if args.limit > 0:
        hex_accounts = hex_accounts[:args.limit]
        print(f"本次处理: {len(hex_accounts)} 个")

    success = 0
    failed = 0
    for acc in hex_accounts:
        email = acc["email"]
        password = acc.get("password", "")
        client_id = acc.get("client_id", "")
        refresh_token = acc.get("refresh_token", "")
        print(f"\n处理 {email}...")

        token = asyncio.run(fetch_token_browser(email, password, args.proxy,
                                                client_id, refresh_token))
        if token:
            update_token(email, token)
            success += 1
            print(f"  [OK] 获取成功: {token[:50]}...")
        else:
            failed += 1
            print(f"  [FAIL] 获取失败")

        time.sleep(5)

    print(f"\n完成: 成功 {success}, 失败 {failed}")

    # 更新 token 文件
    accounts = get_accounts()
    jwt_tokens = [a["access_token"] for a in accounts if a.get("access_token", "").startswith("eyJ")]
    TOKEN_FILE.write_text("\n".join(jwt_tokens) + "\n", encoding="utf-8")
    print(f"已更新 token 文件: {len(jwt_tokens)} 个 JWT token")


if __name__ == "__main__":
    main()
