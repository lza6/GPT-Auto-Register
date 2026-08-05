#!/usr/bin/env python3
"""用代理 + curl_cffi 从 session API 获取真正的 JWT access_token。

用法：
    python scripts/refresh_tokens_simple.py [--proxy http://127.0.0.1:10808] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

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


def fetch_token_simple(email: str, password: str, proxy_url: str | None) -> str | None:
    """用 curl_cffi 从 session API 获取 token（需要先登录）"""
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        print("curl_cffi 未安装")
        return None

    session = cffi_requests.Session(impersonate="firefox", timeout=60)
    if proxy_url:
        session.proxies = {"http": proxy_url, "https": proxy_url}

    try:
        # Step 1: 访问登录页面获取 cookie
        session.get("https://chatgpt.com/auth/login")

        # Step 2: CSRF
        csrf_resp = session.get("https://chatgpt.com/api/auth/csrf",
                                headers={"Referer": "https://chatgpt.com/auth/login"})
        if csrf_resp.status_code != 200:
            return None
        csrf_token = csrf_resp.json().get("csrfToken", "")

        # Step 3: Signin
        import uuid
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
            return None
        signin_data = signin_resp.json()
        auth_url = signin_data.get("url", "")
        if not auth_url:
            return None

        # Step 4: 访问授权页面
        auth_resp = session.get(auth_url, allow_redirects=True)
        final_url = str(auth_resp.url)

        if "email-verification" not in final_url:
            return None

        # Step 5: 等待验证码（用 98faka API）
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
            return None

        # Step 6: 提交验证码
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
            return None

        # Step 7: 创建账号（跳过，因为账号已注册）
        # 直接从 session 获取 token
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
        print(f"  {email}: 异常: {e}")
        return None
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description="用代理获取真正的 JWT access_token")
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

        token = fetch_token_simple(email, password, args.proxy)
        if token:
            update_token(email, token)
            success += 1
            print(f"  ✅ 获取成功: {token[:50]}...")
        else:
            failed += 1
            print(f"  ❌ 获取失败")

        time.sleep(5)

    print(f"\n完成: 成功 {success}, 失败 {failed}")

    # 更新 token 文件
    accounts = get_accounts()
    jwt_tokens = [a["access_token"] for a in accounts if a.get("access_token", "").startswith("eyJ")]
    TOKEN_FILE.write_text("\n".join(jwt_tokens) + "\n", encoding="utf-8")
    print(f"已更新 token 文件: {len(jwt_tokens)} 个 JWT token")


if __name__ == "__main__":
    main()
