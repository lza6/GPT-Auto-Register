#!/usr/bin/env python3
"""用 OpenAI 账号密码登录验证/刷新账号 token（支持二次验证码 + 断点续跑）。

流程:
    1. OAuth authorize (PKCE) → 识别登录页
    2. 填 OpenAI 密码提交
    3. 若 OpenAI 要求二次验证码 → 用微软邮箱收码输入
    4. 捕获 code → 换 token 三件套 → 回写数据库

用法:
    python scripts/verify_account_login.py --email xxx@outlook.com   # 单个
    python scripts/verify_account_login.py --all                     # 批量（断点续跑）
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import secrets
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from services.browser_register import (
    BrowserRegister,
    OAUTH_AUDIENCE,
    OAUTH_AUTH0_CLIENT,
    OAUTH_CLIENT_ID,
    OAUTH_REDIRECT_URI,
    _generate_pkce,
)

RESULT_FILE = ROOT / "data" / "refresh_results.jsonl"


def _pick_account(email: str | None) -> dict:
    conn = sqlite3.connect(str(ROOT / "data" / "register.db"))
    conn.row_factory = sqlite3.Row
    if email:
        row = conn.execute(
            "SELECT email, password, openai_password, client_id, refresh_token, access_token FROM accounts WHERE email = ?",
            (email,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT email, password, openai_password, client_id, refresh_token, access_token FROM accounts "
            "WHERE status='success' AND access_token LIKE 'eyJ%' AND length(access_token) > 200 ORDER BY id LIMIT 1"
        ).fetchone()
    conn.close()
    if not row:
        print("[错误] 未找到可用账号")
        sys.exit(1)
    return dict(row)


def _build_authorize_url(email: str, verifier: str, challenge: str) -> str:
    params = {
        "issuer": "https://auth.openai.com",
        "client_id": OAUTH_CLIENT_ID,
        "audience": OAUTH_AUDIENCE,
        "redirect_uri": OAUTH_REDIRECT_URI,
        "device_id": str(uuid.uuid4()),
        "screen_hint": "login_or_signup",
        "max_age": "0",
        "scope": "openid profile email offline_access",
        "response_type": "code",
        "response_mode": "query",
        "state": secrets.token_urlsafe(16),
        "nonce": secrets.token_urlsafe(16),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "auth0Client": OAUTH_AUTH0_CLIENT,
        "login_hint": email,
    }
    return f"https://auth.openai.com/api/accounts/authorize?{urlencode(params)}"


def _jwt_peek(token: str) -> str:
    try:
        hdr = json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "=="))
        return f"alg={hdr.get('alg')} enc={hdr.get('enc', '')}"
    except Exception:
        return "无法解码"


def _result_success(email: str) -> bool:
    """该账号是否已在结果文件中标记为成功（成功才跳过，失败会重试）"""
    if not RESULT_FILE.exists():
        return False
    for line in RESULT_FILE.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
            if d.get("email") == email and d.get("success"):
                return True
        except Exception:
            pass
    return False


def _save_result(email: str, success: bool, exit_code: int, reason: str = ""):
    with open(RESULT_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"email": email, "success": success, "exit": exit_code, "reason": reason}, ensure_ascii=False) + "\n")


async def run(email: str, openai_pw: str, ms_password: str, ms_client_id: str,
              ms_refresh_token: str, proxy_url: str | None, headful: bool) -> int:
    reg = BrowserRegister({
        "otp_wait_timeout_sec": 180,
        "otp_poll_interval_sec": 5,
        "proxy_url": proxy_url,
        "use_proxy": bool(proxy_url),
    })

    verifier, challenge = _generate_pkce()
    authorize_url = _build_authorize_url(email, verifier, challenge)

    from camoufox.async_api import AsyncCamoufox
    from camoufox import DefaultAddons

    camoufox = AsyncCamoufox(
        headless=not headful,
        exclude_addons=[DefaultAddons.UBO],
        args=["--no-sandbox", "--disable-setuid-sandbox"],
    )
    browser = await camoufox.start()
    context_kwargs: dict = {}
    if proxy_url:
        from urllib.parse import urlparse
        p = urlparse(proxy_url)
        if p.hostname:
            server = f"{p.scheme or 'http'}://{p.hostname}:{p.port or 1000}"
            context_kwargs["proxy"] = (
                {"server": server, "username": p.username, "password": p.password}
                if p.username and p.password else {"server": server}
            )
    context = await browser.new_context(**context_kwargs)
    page = await context.new_page()

    try:
        try:
            await page.goto(authorize_url, wait_until="commit", timeout=45000)
        except Exception:
            await page.goto(authorize_url, wait_until="commit", timeout=45000)
        await asyncio.sleep(6)

        # 密码输入框（邮箱已预填）
        pwd_input = page.locator('input[name="current-password"]').first
        try:
            await pwd_input.wait_for(state="visible", timeout=20000)
        except Exception:
            body = await page.evaluate("() => document.body ? document.body.innerText.slice(0,200) : ''") or ""
            print(f"   [跳过] 未到密码页, URL: {page.url[:80]}, 页面: {body[:100]}")
            return 1

        await pwd_input.click()
        await pwd_input.fill("")
        await page.keyboard.type(openai_pw, delay=40)
        await asyncio.sleep(0.5)
        # 记录密码提交时刻：后续只接收此之后到达的新验证码邮件，避免读到已过期的旧码
        from datetime import datetime, timezone
        submit_baseline = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            await page.locator('button[type="submit"]').first.click(timeout=10000)
        except Exception:
            await page.keyboard.press("Enter")

        # 等待登录结果 / 二次验证码 / code
        code = ""
        reason = ""
        for attempt in range(2):
            await asyncio.sleep(6)
            code = await reg._wait_for_oauth_code(page, timeout=15)
            if code:
                break

            body = await page.evaluate("() => document.body ? document.body.innerText : ''") or ""
            low = body.lower()
            cur = page.url

            if "incorrect" in low or "wrong password" in low or "invalid password" in low:
                reason = "密码错误"
                print("   ❌ 密码错误")
                return 1
            if "account doesn't exist" in low or "not found" in low:
                reason = "账号不存在"
                print("   ❌ 账号不存在")
                return 1

            # 二次验证码（email-verification / 2FA）
            vcode_input = page.locator('input[name="code"], input[autocomplete="one-time-code"], input[inputmode="numeric"]').first
            if await vcode_input.count() > 0 and await vcode_input.is_visible():
                print("   [2FA] OpenAI 要求二次验证码，用微软邮箱接收新验证码...")
                from services.graph_email_service import graph_email_service
                vcode = await graph_email_service.wait_for_new_otp(
                    email, ms_client_id, ms_refresh_token, submit_baseline,
                    timeout_sec=600, poll_interval=10,
                )
                if not vcode:
                    reason = "二次验证码未收到"
                    print("   ❌ 二次验证码未收到")
                    return 1
                await vcode_input.click()
                await vcode_input.fill("")
                await page.keyboard.type(vcode, delay=50)
                await asyncio.sleep(0.5)
                try:
                    await page.locator('button[type="submit"]').first.click(timeout=10000)
                except Exception:
                    await page.keyboard.press("Enter")
                continue

            # 已通过但停在 MFA 选择页
            if "two-step" in low or "authenticator" in low:
                reason = "需2FA应用验证"
                print("   ⚠️ 需要 2FA 应用验证，跳过")
                return 2

            reason = f"未知页面: {cur[:80]}"
            break

        if not code:
            print(f"   ❌ {reason}")
            return 1

        # 换 token 三件套并回写
        tokens = await reg._exchange_code(code, verifier, proxy_url)
        if not tokens or not tokens.get("access_token"):
            print("   ❌ code 换 token 失败")
            return 1

        at = tokens.get("access_token", "")
        rt = tokens.get("refresh_token", "")
        it = tokens.get("id_token", "")
        print(f"   ✅ 登录成功! access_token len={len(at)} ({_jwt_peek(at)}), refresh_token {'✅' if rt else '❌'}")
        try:
            conn = sqlite3.connect(str(ROOT / "data" / "register.db"))
            conn.execute(
                "UPDATE accounts SET access_token=?, openai_refresh_token=?, id_token=?, status='success' WHERE email=?",
                (at, rt, it, email),
            )
            conn.commit()
            conn.close()
            print("   ✅ 已回写 token 到数据库")
        except Exception as e:
            print(f"   [警告] 回写失败: {e}")
        return 0
    except Exception as e:
        print(f"   [异常] {e}")
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
    parser = argparse.ArgumentParser(description="用 OpenAI 密码登录验证/刷新账号 token")
    parser.add_argument("--email", default="", help="指定账号邮箱")
    parser.add_argument("--all", action="store_true", help="批量刷新所有有 OpenAI 密码的账号（断点续跑）")
    parser.add_argument("--proxy", default="", help="代理 URL")
    parser.add_argument("--headful", action="store_true", help="显示浏览器窗口")
    args = parser.parse_args()

    proxy = args.proxy or None

    if args.all:
        conn = sqlite3.connect(str(ROOT / "data" / "register.db"))
        rows = conn.execute(
            "SELECT email, password, openai_password, client_id, refresh_token FROM accounts "
            "WHERE openai_password IS NOT NULL AND openai_password != '' ORDER BY id"
        ).fetchall()
        conn.close()
        print(f"待批量刷新: {len(rows)} 个有 OpenAI 密码的账号", flush=True)
        print(f"结果写入: {RESULT_FILE}（已处理的将跳过，断点续跑）\n", flush=True)
        ok = fail = skip = 0
        for i, (email, ms_pw, opw, cid, rt) in enumerate(rows, 1):
            if _result_success(email):
                skip += 1
                continue
            print(f"=== [{i}/{len(rows)}] {email} ===", flush=True)
            code = asyncio.run(run(email, opw, ms_pw, cid, rt, proxy, args.headful))
            is_ok = code == 0
            if is_ok:
                ok += 1
            else:
                fail += 1
            _save_result(email, is_ok, code)
            print(f"   结果: {'✅ 可用' if is_ok else '❌ 不可用'}\n", flush=True)
            time.sleep(2)
        print(f"\n批量完成: 可用 {ok}, 不可用 {fail}, 跳过已处理 {skip}")
        sys.exit(0)

    acc = _pick_account(args.email or None)
    opw = acc.get("openai_password") or ""
    if not opw:
        print(f"[错误] 账号 {acc['email']} 没有 OpenAI 密码，无法用密码登录验证")
        sys.exit(1)
    print(f"使用账号: {acc['email']}")
    print(f"代理: {proxy or '直连'}")

    exit_code = asyncio.run(run(
        acc["email"], opw, acc.get("password") or "", acc.get("client_id") or "",
        acc.get("refresh_token") or "", proxy, args.headful,
    ))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
