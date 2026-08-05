#!/usr/bin/env python3
"""用验证码登录（email OTP）获取真正的 JWT access_token。

流程：
1. CF solver 获取 cf_clearance
2. curl_cffi 访问 chatgpt.com 登录页面
3. 输入邮箱 → 触发发送验证码
4. 98faka API 获取验证码
5. 提交验证码 → 获取 authorization code
6. 用 code 换 token（OAuth PKCE）

用法：
    python scripts/fetch_tokens_with_otp.py [--proxy http://127.0.0.1:10808] [--limit N]
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "register.db"
TOKEN_FILE = Path(__file__).resolve().parent.parent / "已经获取到的token.txt"
CF_SOLVER_URL = "http://127.0.0.1:8001"

# OpenAI OAuth 配置（从 chatgpt2api 复制）
AUTH_BASE = "https://auth.openai.com"
PLATFORM_BASE = "https://platform.openai.com"
PLATFORM_OAUTH_CLIENT_ID = "app_X8zY6vW2pQ9tR3dE7nK1jL5gH"
PLATFORM_OAUTH_AUDIENCE = "https://api.openai.com/v1"
PLATFORM_OAUTH_REDIRECT_URI = "https://platform.openai.com/auth/callback"
PLATFORM_AUTH0_CLIENT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJvaSIsInRlbmFudCI6Im9pIiwiaXNzIjoiaHR0cHM6Ly9hdXRoLm9wZW5haS5jb20vIiwiYXVkIjoiaHR0cHM6Ly9hcGkub3BlbmFpLmNvbS92MSIsImlhdCI6MTc0OTk5OTk5OX0.dummy"


def get_accounts() -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM accounts WHERE status IN ('success', 'success_no_token') ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_token(email: str, access_token: str, refresh_token: str = "", id_token: str = "") -> None:
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


def generate_pkce() -> tuple[str, str]:
    """生成 PKCE code_verifier 和 code_challenge"""
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        __import__('hashlib').sha256(code_verifier.encode()).digest()
    ).decode().rstrip('=')
    return code_verifier, code_challenge


def fetch_token_with_otp(email: str, password: str, proxy_url: str | None) -> dict | None:
    """用验证码登录获取真正的 JWT access_token"""
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
    print(f"  CF clearance 获取成功")

    # Step 1: 创建 curl_cffi session
    session = cffi_requests.Session(impersonate="firefox", timeout=60)
    if proxy_url:
        session.proxies = {"http": proxy_url, "https": proxy_url}

    for name, value in cf_cookies.items():
        session.cookies.set(name, value)

    try:
        # Step 2: 访问登录页面
        session.get("https://chatgpt.com/auth/login")

        # Step 3: CSRF
        csrf_resp = session.get("https://chatgpt.com/api/auth/csrf",
                                headers={"Referer": "https://chatgpt.com/auth/login"})
        if csrf_resp.status_code != 200:
            print(f"  CSRF 获取失败")
            return None
        csrf_token = csrf_resp.json().get("csrfToken", "")

        # Step 4: Signin（触发发送验证码）
        device_id = str(uuid.uuid4())
        session_log_id = str(uuid.uuid4())
        signin_resp = session.post(
            "https://chatgpt.com/api/auth/signin/openai",
            data={"callbackUrl": "/", "csrfToken": csrf_token, "json": "true"},
            params={
                "prompt": "login",
                "ext-oai-did": device_id,
                "auth_session_logging_id": session_log_id,
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

        # Step 5: 访问授权页面（触发发送验证码邮件）
        auth_resp = session.get(auth_url, allow_redirects=True)
        final_url = str(auth_resp.url)

        if "email-verification" not in final_url:
            print(f"  未到达验证码页面: {final_url[:60]}")
            return None

        # Step 6: 等待验证码（用 98faka API）
        print(f"  等待验证码...")
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

        otp_data = otp_resp.json()
        continue_url = otp_data.get("continue_url", "")

        # Step 8: 从 continue_url 提取 authorization code
        auth_code = ""
        if continue_url:
            parsed = parse_qs(urlparse(continue_url).query)
            auth_code = str((parsed.get("code") or [""])[0]).strip()

        if not auth_code:
            print(f"  未获取到 authorization code")
            return None

        # Step 9: 用 code 换 token（OAuth PKCE）
        print(f"  用 code 换 token...")
        code_verifier, code_challenge = generate_pkce()
        token_resp = session.post(
            f"{AUTH_BASE}/api/accounts/oauth/token",
            headers={
                "Accept": "*/*",
                "Content-Type": "application/json",
                "Origin": PLATFORM_BASE,
                "Referer": f"{PLATFORM_BASE}/",
                "auth0-client": PLATFORM_AUTH0_CLIENT,
            },
            json={
                "client_id": PLATFORM_OAUTH_CLIENT_ID,
                "code_verifier": code_verifier,
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": PLATFORM_OAUTH_REDIRECT_URI,
            },
        )

        if token_resp.status_code != 200:
            print(f"  token 兑换失败: HTTP {token_resp.status_code}")
            return None

        token_data = token_resp.json()
        access_token = token_data.get("access_token", "")
        refresh_token = token_data.get("refresh_token", "")
        id_token = token_data.get("id_token", "")

        if access_token:
            return {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "id_token": id_token,
            }

        return None

    except Exception as e:
        print(f"  异常: {e}")
        return None
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description="用验证码登录获取真正的 JWT access_token")
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

        token_data = fetch_token_with_otp(email, password, args.proxy)
        if token_data and token_data.get("access_token"):
            update_token(email, token_data["access_token"],
                        token_data.get("refresh_token", ""),
                        token_data.get("id_token", ""))
            success += 1
            print(f"  [OK] 获取成功: {token_data['access_token'][:50]}...")
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
