#!/usr/bin/env python3
"""用 CF solver + curl_cffi 获取真正的 JWT access_token。

流程：
1. CF solver 获取 cf_clearance + cookie
2. curl_cffi 用这些 cookie 访问 chatgpt.com
3. 走完整登录流程（邮箱 → 验证码 → session）
4. 从 session API 获取 JWT access_token

用法：
    python scripts/fetch_jwt_tokens.py [--proxy http://127.0.0.1:10808] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
import uuid
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
            # 提交 clearance 任务
            resp = client.get(f"{CF_SOLVER_URL}/clearance", params={"url": url, "timeout": timeout})
            if resp.status_code != 202:
                return {}
            task_id = resp.json().get("task_id")
            if not task_id:
                return {}

            # 轮询结果
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


def fetch_jwt_token(email: str, password: str, proxy_url: str | None) -> str | None:
    """用 CF solver + curl_cffi 获取真正的 JWT access_token"""
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        print("curl_cffi 未安装")
        return None

    # Step 0: 用 CF solver 获取 cf_clearance
    print(f"  获取 CF clearance...")
    cf_cookies = get_cf_cookies("https://chatgpt.com/", timeout=60)
    if not cf_cookies:
        print(f"  CF clearance 获取失败")
        return None
    print(f"  CF clearance 获取成功 ({len(cf_cookies)} cookies)")

    # Step 1: 创建 curl_cffi session（带 CF cookie）
    session = cffi_requests.Session(impersonate="firefox", timeout=60)
    if proxy_url:
        session.proxies = {"http": proxy_url, "https": proxy_url}

    # 设置 CF cookie
    for name, value in cf_cookies.items():
        session.cookies.set(name, value)

    try:
        # Step 2: 访问登录页面
        login_resp = session.get("https://chatgpt.com/auth/login")
        if login_resp.status_code != 200:
            print(f"  登录页面访问失败: HTTP {login_resp.status_code}")
            return None

        # Step 3: CSRF
        csrf_resp = session.get("https://chatgpt.com/api/auth/csrf",
                                headers={"Referer": "https://chatgpt.com/auth/login"})
        if csrf_resp.status_code != 200:
            print(f"  CSRF 获取失败: HTTP {csrf_resp.status_code}")
            return None
        csrf_token = csrf_resp.json().get("csrfToken", "")

        # Step 4: Signin
        signin_resp = session.post(
            "https://chatgpt.com/api/auth/signin/openai",
            data={"callbackUrl": "/", "csrfToken": csrf_token, "json": "true"},
            params={
                "prompt": "login",
                "ext-oai-did": str(uuid.uuid4()),
                "auth_session_logging_id": str(uuid.uuid4()),
                "screen_hint": "login_or_signup",
                "login_hint": email,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Origin": "https://chatgpt.com",
                     "Referer": "https://chatgpt.com/auth/login"},
        )
        if signin_resp.status_code != 200:
            print(f"  登录请求失败: HTTP {signin_resp.status_code}")
            return None
        signin_data = signin_resp.json()
        auth_url = signin_data.get("url", "")
        if not auth_url:
            print(f"  未获取到授权 URL")
            return None

        # Step 5: 访问授权页面
        auth_resp = session.get(auth_url, allow_redirects=True)
        final_url = str(auth_resp.url)

        if "email-verification" not in final_url:
            print(f"  未到达验证码页面: {final_url[:60]}")
            return None

        # Step 6: 等待验证码（用 98faka API）
        import asyncio
        from services.email_service import email_service
        loop = asyncio.new_event_loop()
        try:
            otp_code = loop.run_until_complete(
                email_service.wait_for_otp(
                    email, password, "", "",
                    timeout_sec=300, poll_interval=5, skip_existing=False,
                )
            )
        finally:
            loop.close()

        if not otp_code:
            print(f"  验证码等待超时")
            return None

        # Step 7: 提交验证码
        otp_resp = session.post(
            "https://auth.openai.com/api/accounts/email-otp/validate",
            json={"code": otp_code},
            headers={
                "Content-Type": "application/json",
                "Origin": "https://auth.openai.com",
                "Referer": final_url,
                "Accept": "application/json",
            },
        )
        if otp_resp.status_code != 200:
            print(f"  验证码提交失败: HTTP {otp_resp.status_code}")
            return None

        # Step 8: 从 session API 获取 token
        session_resp = session.get("https://chatgpt.com/api/auth/session")
        if session_resp.status_code == 200:
            session_data = session_resp.json()
            access_token = session_data.get("accessToken", "")
            if access_token:
                return access_token

        # 从 cookie 中提取 JWT
        for name, value in session.cookies.items():
            if value.startswith("eyJ") and len(value) > 200:
                return value

        return None

    except Exception as e:
        print(f"  异常: {e}")
        return None
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description="用 CF solver 获取真正的 JWT access_token")
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
        print(f"\n处理 {email}...")

        token = fetch_jwt_token(email, password, args.proxy)
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
