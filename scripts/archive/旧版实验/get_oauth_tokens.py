#!/usr/bin/env python3
"""通过 OpenAI OAuth 授权码流程获取标准 JWT access_token + refresh_token + id_token

流程（纯 API，不需要处理 about-you 页面）：
1. CF solver 获取 cf_clearance
2. curl_cffi 走登录页面拿到 CSRF token
3. 提交邮箱触发 OTP
4. 98faka API 获取验证码
5. 提交验证码 → 返回 continue_url（含 authorization code）
6. 用 code 换 token 三件套 (access_token + refresh_token + id_token)
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.fetch_tokens_browser import get_cf_cookies
from services.email_service import email_service

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "register.db"
CF_SOLVER_URL = "http://127.0.0.1:8001"


def get_accounts() -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM accounts WHERE status IN ('success', 'success_no_token') ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_account(email: str, access_token: str, refresh_token: str, id_token: str) -> None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "UPDATE accounts SET access_token = ?, status = 'success' WHERE email = ?",
        (access_token, email),
    )
    conn.commit()
    conn.close()


def generate_pkce() -> tuple[str, str, str]:
    """生成 PKCE 参数，返回 (code_verifier, code_challenge, state)"""
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).decode().rstrip("=")
    state = secrets.token_urlsafe(32)
    return code_verifier, code_challenge, state


def get_oauth_tokens_sync(email: str, password: str, client_id: str,
                          refresh_token_91kami: str, proxy_url: str) -> dict | None:
    """同步方式获取 OAuth token 三件套"""
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        print("  curl_cffi 未安装")
        return None

    # Step 1: CF solver 获取 cf_clearance
    print(f"  获取 CF clearance...")
    cf_cookies = get_cf_cookies("https://chatgpt.com/", timeout=60)
    if not cf_cookies:
        print(f"  CF clearance 获取失败")
        return None
    print(f"  CF clearance 获取成功 ({len(cf_cookies)} cookies)")

    # Step 2: 创建 session
    session = cffi_requests.Session(impersonate="firefox", timeout=60)
    if proxy_url:
        session.proxies = {"http": proxy_url, "https": proxy_url}
    for name, value in cf_cookies.items():
        session.cookies.set(name, value)

    try:
        # Step 3: 访问登录页
        session.get("https://chatgpt.com/auth/login")

        # Step 4: CSRF
        csrf_resp = session.get("https://chatgpt.com/api/auth/csrf",
                                headers={"Referer": "https://chatgpt.com/auth/login"})
        if csrf_resp.status_code != 200:
            print(f"  CSRF 获取失败: HTTP {csrf_resp.status_code}")
            return None
        csrf_token = csrf_resp.json().get("csrfToken", "")

        # Step 5: 发起 signin（触发发送验证码）
        import uuid
        device_id = str(uuid.uuid4())
        signin_resp = session.post(
            "https://chatgpt.com/api/auth/signin/openai",
            data={"callbackUrl": "/", "csrfToken": csrf_token, "json": "true"},
            params={
                "prompt": "login",
                "ext-oai-did": device_id,
                "auth_session_logging_id": str(uuid.uuid4()),
                "screen_hint": "login_or_signup",
                "login_hint": email,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Origin": "https://chatgpt.com",
                     "Referer": "https://chatgpt.com/auth/login"},
        )
        if signin_resp.status_code != 200:
            print(f"  Signin 失败: HTTP {signin_resp.status_code}")
            return None
        signin_data = signin_resp.json()
        auth_url = signin_data.get("url", "")
        if not auth_url:
            print(f"  未获取到授权 URL")
            return None

        # Step 6: 访问授权页面（触发 OTP 发送）
        auth_resp = session.get(auth_url, allow_redirects=True)
        final_url = str(auth_resp.url)
        print(f"  授权页面: {final_url[:60]}")

        if "email-verification" not in final_url:
            print(f"  未到达验证码页面")
            return None

        # Step 7: 等待验证码（用 98faka API）
        print(f"  等待验证码...")
        loop = asyncio.new_event_loop()
        try:
            otp_code = loop.run_until_complete(
                email_service.wait_for_otp(
                    email, password, client_id, refresh_token_91kami,
                    timeout_sec=300, poll_interval=5, skip_existing=False,
                )
            )
        finally:
            loop.close()

        if not otp_code:
            print(f"  验证码等待超时")
            return None
        print(f"  验证码: {otp_code}")

        # Step 8: 提交验证码 → 获取 continue_url（含 authorization code）
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

        if not continue_url:
            print(f"  未获取到 continue_url")
            return None
        print(f"  continue_url: {continue_url[:80]}...")

        # Step 9: 从 continue_url 提取 authorization code
        parsed = urlparse(continue_url)
        params = parse_qs(parsed.query)
        auth_code = params.get("code", [""])[0]
        print(f"  authorization code: {auth_code[:50]}...")

        if not auth_code:
            print(f"  未获取到 authorization code")
            return None

        # Step 10: 生成 PKCE 参数并交换 token
        code_verifier, code_challenge, state = generate_pkce()
        print(f"  交换 token...")

        token_resp = session.post(
            "https://auth.openai.com/api/accounts/oauth/token",
            headers={
                "Accept": "*/*",
                "Content-Type": "application/json",
                "Origin": "https://chatgpt.com",
                "Referer": "https://chatgpt.com/",
                "auth0-client": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJvaSIsInRlbmFudCI6Im9pIiwiaXNzIjoiaHR0cHM6Ly9hdXRoLm9wZW5haS5jb20vIiwiYXVkIjoiaHR0cHM6Ly9hcGkub3BlbmFpLmNvbS92MSIsImlhdCI6MTc0OTk5OTk5OX0.dummy",
            },
            json={
                "client_id": "app_X8zY6vW2pQ9tR3dE7nK1jL5gH",
                "code_verifier": code_verifier,
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": "https://chatgpt.com/api/auth/callback/openai",
            },
        )
        if token_resp.status_code != 200:
            print(f"  Token 交换失败: HTTP {token_resp.status_code} {token_resp.text[:200]}")
            return None

        token_data = token_resp.json()
        access_token = token_data.get("access_token", "")
        new_refresh_token = token_data.get("refresh_token", "")
        id_token = token_data.get("id_token", "")

        if access_token:
            print(f"  ✅ access_token: {access_token[:50]}... (len={len(access_token)})")
            print(f"  ✅ refresh_token: {new_refresh_token[:50]}...")
            print(f"  ✅ id_token: {id_token[:50]}...")
            return {
                "access_token": access_token,
                "refresh_token": new_refresh_token,
                "id_token": id_token,
            }

        return None

    except Exception as e:
        print(f"  异常: {e}")
        return None
    finally:
        session.close()


def import_to_chatgpt2api(tokens: list[dict]) -> dict:
    """导入三件套到 chatgpt2api"""
    import urllib.request

    payloads = []
    for t in tokens:
        payloads.append({
            "access_token": t["access_token"],
            "refresh_token": t["refresh_token"],
            "id_token": t["id_token"],
            "email": t.get("email", ""),
            "source_type": "oauth_login",
        })

    body = json.dumps({"accounts": payloads}).encode("utf-8")
    req = urllib.request.Request("http://localhost:23456/api/accounts", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer chatgpt2api")
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        result = json.loads(resp.read().decode("utf-8"))
        return result
    except Exception as e:
        return {"error": str(e)}


def main():
    print("=" * 60)
    print("OAuth 授权码流程获取标准 JWT 三件套")
    print("=" * 60)

    accounts = get_accounts()
    print(f"\n共 {len(accounts)} 个账号")

    # 只处理 HEX token 的账号（需要重新获取）
    hex_accounts = [a for a in accounts
                    if a.get("access_token") and not a["access_token"].startswith("eyJ")]
    print(f"HEX token 账号: {len(hex_accounts)} 个")

    # 加上 JWE token 的账号（虽然以 eyJ 开头但也不是标准 JWT）
    jwe_accounts = [a for a in accounts
                    if a.get("access_token", "").startswith("eyJ") and len(a.get("access_token", "")) < 500]
    print(f"JWE token 账号: {len(jwe_accounts)} 个")

    target_accounts = hex_accounts + jwe_accounts
    # 再加上无 token 的
    no_token = [a for a in accounts if not a.get("access_token")]
    print(f"无 token 账号: {len(no_token)} 个")
    target_accounts += no_token

    print(f"\n总计需要处理: {len(target_accounts)} 个")
    print(f"本次仅处理前 5 个验证可行性")

    success_tokens = []
    for acc in target_accounts[:5]:
        email = acc["email"]
        password = acc.get("password", "")
        client_id = acc.get("client_id", "")
        rt = acc.get("refresh_token", "")

        print(f"\n处理 {email}...")
        result = get_oauth_tokens_sync(email, password, client_id, rt, "http://127.0.0.1:10808")

        if result:
            update_account(email, result["access_token"], result["refresh_token"], result["id_token"])
            success_tokens.append(result)
            print(f"  ✅ 成功获取标准 JWT!")
        else:
            print(f"  ❌ 获取失败")

        time.sleep(5)

    # 导入 chatgpt2api
    if success_tokens:
        print(f"\n导入 {len(success_tokens)} 个到 chatgpt2api...")
        imp = import_to_chatgpt2api(success_tokens)
        print(f"导入结果: {json.dumps(imp, ensure_ascii=False)[:200]}")

    print(f"\n成功: {len(success_tokens)}, 失败: {5 - len(success_tokens)}")


if __name__ == "__main__":
    main()