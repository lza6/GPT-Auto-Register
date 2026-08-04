#!/usr/bin/env python3
"""用账号密码直接登录 OpenAI，验证自动注册的账号是否可用。

流程:
    1. 导航 OAuth authorize (PKCE) 登录页（邮箱自动预填）
    2. 用数据库里的 password 填密码并提交
    3. 判断结果：
       - 密码正确 → 登录成功 → 账号可用（顺带捕获 code 换取 token 作附加参考）
       - 密码错误 → 登录失败，说明该密码不是 OpenAI 账号密码

主结论 = 账号密码能否登录（= 账号是否可用）；token 仅作附加参考。
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import secrets
import sqlite3
import sys
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
    d = dict(row)
    # 优先用 OpenAI 账号密码；无则回退数据库 password（微软邮箱密码）
    d["login_password"] = d.get("openai_password") or d.get("password") or ""
    return d


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


async def run(email: str, password: str, proxy_url: str | None, headful: bool) -> int:
    reg = BrowserRegister({
        "otp_wait_timeout_sec": 300,
        "otp_poll_interval_sec": 5,
        "proxy_url": proxy_url,
        "use_proxy": bool(proxy_url),
    })

    verifier, challenge = _generate_pkce()
    authorize_url = _build_authorize_url(email, verifier, challenge)
    print("[1/4] 构造 OAuth authorize 登录 URL (PKCE)")

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
    print(f"[2/4] 导航登录页: {authorize_url[:80]}...")

    try:
        await page.goto(authorize_url, wait_until="networkidle", timeout=60000)
        await asyncio.sleep(4)

        # 密码输入框（诊断确认页面是 log-in/password，邮箱已预填）
        print("[3/4] 填写密码并提交...")
        pwd_input = page.locator('input[name="current-password"]').first
        try:
            await pwd_input.wait_for(state="visible", timeout=15000)
        except Exception:
            await page.screenshot(path=str(ROOT / "verify_pwd_debug.png"))
            body = await page.evaluate("() => document.body ? document.body.innerText.slice(0,300) : ''")
            print(f"   [失败] 未找到密码输入框, URL: {page.url[:80]}")
            print(f"   页面文字: {body[:200]}")
            return 1

        await pwd_input.click()
        await pwd_input.fill("")
        await page.keyboard.type(password, delay=50)
        await asyncio.sleep(0.5)
        await page.locator('button[type="submit"]').first.click(timeout=10000)

        # ── 判断登录结果 ──
        print("[4/4] 等待登录结果...")
        await asyncio.sleep(6)

        # 情况1: 登录成功 → 跳转携带 code
        code = await reg._wait_for_oauth_code(page, timeout=20)
        if code:
            print("✅ 密码正确，登录成功！")
            tokens = await reg._exchange_code(code, verifier, proxy_url)
            if tokens and tokens.get("access_token"):
                at = tokens.get("access_token", "")
                rt = tokens.get("refresh_token", "")
                it = tokens.get("id_token", "")
                print("\n  附加抓取到 token 三件套:")
                print(f"    access_token : {at[:36]}... (len={len(at)}, {_jwt_peek(at)})")
                print(f"    refresh_token: {rt[:36]}... (len={len(rt)})" + ("  ← ✅" if rt else "  ← 无"))
                print(f"    id_token     : {it[:36]}... (len={len(it)})")
                # 回写数据库（刷新长期 token）
                try:
                    conn = sqlite3.connect(str(ROOT / "data" / "register.db"))
                    conn.execute(
                        "UPDATE accounts SET access_token=?, openai_refresh_token=?, id_token=?, status='success' WHERE email=?",
                        (at, rt, it, email),
                    )
                    conn.commit()
                    conn.close()
                    print("   ✅ 已回写 token 到数据库 (可长期自动续期)")
                except Exception as e:
                    print(f"   [警告] 回写数据库失败: {e}")
            print("\n🔑 结论: 该账号密码可登录 OpenAI，账号可用。")
            return 0

        # 情况2: 密码错误或其他
        body = ""
        try:
            body = await page.evaluate("() => document.body ? document.body.innerText : ''") or ""
        except Exception:
            pass
        cur = page.url
        low = body.lower()
        if any(w in low for w in ["incorrect", "wrong password", "invalid password", "password incorrect"]):
            print("❌ 密码错误，登录失败。")
            print("\n💡 说明: 数据库存的 password 是【微软邮箱密码】，不是 OpenAI 账号密码。")
            print("   该 OpenAI 账号显示密码登录页，但系统未保存其真实密码（注册用邮箱+验证码创建）。")
            print("   因此「账号密码登录 OpenAI」无法用这个密码验证。")
            return 1
        if "two-step" in low or "2fa" in low or "authentication" in low and "code" in low:
            print("⚠️ 密码通过，但进入两步验证（MFA）页面，未走验证码通道。")
            print("   说明该账号密码有效，账号可用（需额外 2FA）。")
            return 2

        print(f"❓ 登录结果不明。URL: {cur[:100]}")
        print(f"   页面文字: {body[:200]}")
        await page.screenshot(path=str(ROOT / "verify_result_debug.png"))
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
    parser = argparse.ArgumentParser(description="用账号密码登录 OpenAI 验证/刷新账号 token")
    parser.add_argument("--email", default="", help="指定账号邮箱（默认取第一个标准 JWT 账号）")
    parser.add_argument("--all", action="store_true", help="批量刷新所有有 OpenAI 密码的账号（逐个登录，耗时较长）")
    parser.add_argument("--proxy", default="", help="代理 URL（默认直连）")
    parser.add_argument("--headful", action="store_true", help="显示浏览器窗口（调试用）")
    args = parser.parse_args()

    proxy = args.proxy or None

    if args.all:
        conn = sqlite3.connect(str(ROOT / "data" / "register.db"))
        rows = conn.execute(
            "SELECT email, openai_password, client_id, refresh_token FROM accounts "
            "WHERE openai_password IS NOT NULL AND openai_password != '' ORDER BY id"
        ).fetchall()
        conn.close()
        print(f"待批量刷新: {len(rows)} 个有 OpenAI 密码的账号\n")
        ok = fail = 0
        for i, (email, pw, cid, rt) in enumerate(rows, 1):
            print(f"=== [{i}/{len(rows)}] {email} ===")
            code = asyncio.run(run(email, pw, proxy, args.headful))
            if code == 0:
                ok += 1
            else:
                fail += 1
            print(f"   结果: {'✅' if code == 0 else '❌'}\n")
        print(f"批量完成: 成功 {ok}, 失败 {fail}")
        sys.exit(0)

    acc = _pick_account(args.email or None)
    pw = acc["login_password"]
    print(f"使用账号: {acc['email']}")
    print(f"密码: {pw[:4]}*** (长度 {len(pw)}, {'OpenAI密码' if acc.get('openai_password') else '微软邮箱密码'})")
    print(f"代理: {proxy or '直连'}")

    exit_code = asyncio.run(run(
        acc["email"], pw,
        proxy, args.headful,
    ))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
