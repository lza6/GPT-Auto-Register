#!/usr/bin/env python3
"""完整 OAuth 流程获取标准 JWT：OTP验证 → 提交姓名生日 → callback code → PKCE 换 token"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import sys
import uuid
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, ".")

from scripts.fetch_tokens_browser import get_cf_cookies
from services.email_service import email_service


def generate_pkce() -> tuple[str, str]:
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).decode().rstrip("=")
    return code_verifier, code_challenge


def get_oauth_tokens(email: str, password: str, client_id: str,
                     refresh_token_91kami: str, proxy_url: str) -> dict | None:
    """OTP → create_account → callback code → PKCE 换标准 JWT"""
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        return None

    cf = get_cf_cookies("https://chatgpt.com/", timeout=60)
    print(f"  CF cookies: {len(cf)}")

    s = cffi_requests.Session(impersonate="firefox", timeout=60)
    if proxy_url:
        s.proxies = {"http": proxy_url, "https": proxy_url}
    for n, v in cf.items():
        s.cookies.set(n, v)

    try:
        # 1. 登录页
        s.get("https://chatgpt.com/auth/login")
        print("  [1] 登录页 OK")

        # 2. CSRF
        csrf = s.get("https://chatgpt.com/api/auth/csrf",
                     headers={"Referer": "https://chatgpt.com/auth/login"}).json()
        csrf_token = csrf.get("csrfToken", "")
        print(f"  [2] CSRF OK")

        # 3. Signin（触发 OTP）
        signin = s.post(
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
        ).json()
        auth_url = signin.get("url", "")
        if not auth_url:
            print("  [3] 未获取到 auth_url")
            return None
        print(f"  [3] Signin OK")

        # 4. 访问授权页（触发 OTP 邮件）
        auth = s.get(auth_url, allow_redirects=True)
        final_url = str(auth.url)
        if "email-verification" not in final_url:
            print(f"  [4] 未到 email-verification: {final_url[:60]}")
            return None
        print(f"  [4] email-verification OK")

        # 5. 等待 OTP
        loop = asyncio.new_event_loop()
        otp = loop.run_until_complete(
            email_service.wait_for_otp(email, password, client_id, refresh_token_91kami,
                                       timeout_sec=300, poll_interval=5, skip_existing=False)
        )
        loop.close()
        if not otp:
            print("  [5] OTP 超时")
            return None
        print(f"  [5] OTP: {otp}")

        # 6. 提交 OTP
        otp_resp = s.post(
            "https://auth.openai.com/api/accounts/email-otp/validate",
            json={"code": otp},
            headers={"Content-Type": "application/json",
                     "Origin": "https://auth.openai.com",
                     "Referer": final_url,
                     "Accept": "application/json"},
        )
        if otp_resp.status_code != 200:
            print(f"  [6] OTP 提交失败: {otp_resp.status_code}")
            return None
        otp_data = otp_resp.json()
        print(f"  [6] OTP 验证 OK, continue_url: {otp_data.get('continue_url', '')[:40]}")

        # 7. 提交姓名生日（create_account）→ 拿 callback URL
        name = "user" + str(uuid.uuid4())[:8]
        import random
        birthdate = f"19{random.randint(80, 99)}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"
        create_resp = s.post(
            "https://auth.openai.com/api/accounts/create_account",
            json={"name": name, "birthdate": birthdate},
            headers={
                "Content-Type": "application/json",
                "Origin": "https://auth.openai.com",
                "Referer": "https://auth.openai.com/about-you",
                "x-access-flow-invocation-id": str(uuid.uuid4()),
            },
        )
        if create_resp.status_code != 200:
            print(f"  [7] create_account 失败: {create_resp.status_code} {create_resp.text[:200]}")
            return None
        create_data = create_resp.json()
        callback_url = create_data.get("continue_url", "")
        print(f"  [7] create_account OK")
        if not callback_url or "code=" not in callback_url:
            print(f"  [7] 未获取到含 code 的 callback URL: {callback_url[:80]}")
            return None

        # 8. 提取 code
        parsed = urlparse(callback_url)
        params = parse_qs(parsed.query)
        auth_code = params.get("code", [""])[0]
        print(f"  [8] auth_code: {auth_code[:50]}...")

        # 9. PKCE 换 token
        cv, cc = generate_pkce()
        tr = s.post(
            "https://auth.openai.com/api/accounts/oauth/token",
            json={
                "client_id": "app_X8zY6vW2pQ9tR3dE7nK1jL5gH",
                "code_verifier": cv,
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": "https://chatgpt.com/api/auth/callback/openai",
            },
            headers={
                "Content-Type": "application/json",
                "Origin": "https://chatgpt.com",
                "auth0-client": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJvaSIsInRlbmFudCI6Im9pIiwiaXNzIjoiaHR0cHM6Ly9hdXRoLm9wZW5haS5jb20vIiwiYXVkIjoiaHR0cHM6Ly9hcGkub3BlbmFpLmNvbS92MSIsImlhdCI6MTc0OTk5OTk5OX0.dummy",
            },
        )
        print(f"  [9] Token exchange: HTTP {tr.status_code}")
        if tr.status_code == 200:
            try:
                td = tr.json()
                at = td.get("access_token", "")
                rt = td.get("refresh_token", "")
                it = td.get("id_token", "")
                print(f"  [9] ✅ access_token: {at[:60]}... (len={len(at)})")
                print(f"  [9] ✅ refresh_token: {rt[:40]}...")
                print(f"  [9] ✅ id_token: {it[:40]}...")
                return {"access_token": at, "refresh_token": rt, "id_token": it}
            except Exception:
                print(f"  [9] 响应非 JSON: {tr.text[:200]}")
                return None
        else:
            print(f"  [9] 失败: {tr.text[:300]}")
            return None

    except Exception as e:
        print(f"  异常: {e}")
        return None
    finally:
        s.close()


if __name__ == "__main__":
    import sqlite3
    conn = sqlite3.connect("data/register.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM accounts WHERE email='vuuexsxpgoovz@outlook.com'").fetchone()
    conn.close()
    a = dict(row)
    r = get_oauth_tokens(a["email"], a["password"], a["client_id"], a["refresh_token"],
                         "http://127.0.0.1:10808")
    print("\nRESULT:", "SUCCESS" if r else "FAILED")
