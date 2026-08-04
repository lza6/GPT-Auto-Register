from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import secrets
import time
import uuid
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from services.db import add_log
from services.email_service import email_service
from services.graph_email_service import graph_email_service
from services.imap_email_service import imap_email_service
from services.name_service import name_service
from services.proxy_service import proxy_service

# OAuth PKCE 常量（与 chatgpt2api 同一 client，注册成功后可拿到 refresh_token 长期续期）
OAUTH_CLIENT_ID = "app_2SKx67EdpoN0G6j64rFvigXD"
OAUTH_REDIRECT_URI = "https://platform.openai.com/auth/callback"
OAUTH_AUDIENCE = "https://api.openai.com/v1"
OAUTH_AUTH0_CLIENT = "eyJuYW1lIjoiYXV0aDAtc3BhLWpzIiwidmVyc2lvbiI6IjEuMjEuMCJ9"


def _generate_pkce() -> tuple[str, str]:
    """生成 PKCE code_verifier 与 S256 code_challenge"""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


class BrowserRegister:
    """使用 camoufox 浏览器完成 ChatGPT 注册（执行 JS，触发验证码发送）"""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.otp_timeout = int(config.get("otp_wait_timeout_sec", 120))
        self.otp_poll = int(config.get("otp_poll_interval_sec", 5))

    async def _wait_for_new_otp(self, email: str, password: str, client_id: str,
                                 refresh_token: str, old_mail_ids: set[str],
                                 timeout_sec: int = 300, poll_interval: int = 5,
                                 skip_code: str = "") -> str | None:
        """等待新到达的 ChatGPT 验证码邮件（优先 98faka API，降级 Graph API）"""
        # 优先用 98faka API（用 email+password+client_id+refresh_token 定位邮箱，验证码不同）
        try:
            code = await email_service.wait_for_otp(
                email, password, client_id, refresh_token,
                timeout_sec=timeout_sec,
                poll_interval=poll_interval,
                skip_existing=False,
            )
            if code:
                return code
        except Exception as e:
            add_log("warning", f"98faka API 获取验证码异常，降级 Graph API: {e}", {"email": email})

        # 降级：Graph API（共享收件箱，验证码可能相同）
        return await graph_email_service.wait_for_otp(
            email, client_id, refresh_token,
            timeout_sec=timeout_sec,
            poll_interval=poll_interval,
            skip_code=skip_code,
        )

    async def register_one(self, email: str, password: str, client_id: str,
                           refresh_token: str) -> dict[str, Any]:
        """用浏览器注册单个账号"""
        result: dict[str, Any] = {
            "email": email, "status": "failed", "error": "",
            "access_token": "", "refresh_token": "", "id_token": "",
            "name": "", "birthdate": "", "proxy": "",
        }
        name = name_service.generate()
        birthdate = name_service.generate_birthdate()
        result["name"] = name
        result["birthdate"] = birthdate

        # 优先使用本地代理（config.proxy_url），其次 kookeey
        proxy_url = self.config.get("proxy_url") or (
            proxy_service.get_next() if self.config.get("use_proxy", False) else None
        )
        result["proxy"] = proxy_service.format_for_display(proxy_url) if proxy_url else "直连"

        # OAuth PKCE：注册走 authorize 流程，成功后拿 access_token + refresh_token + id_token（长期续期）
        use_pkce = self.config.get("use_oauth_pkce", True)
        code_verifier = ""
        code_challenge = ""
        authorize_url = ""
        if use_pkce:
            try:
                code_verifier, code_challenge = _generate_pkce()
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
                    "code_challenge": code_challenge,
                    "code_challenge_method": "S256",
                    "auth0Client": OAUTH_AUTH0_CLIENT,
                    "login_hint": email,
                }
                authorize_url = f"https://auth.openai.com/api/accounts/authorize?{urlencode(params)}"
            except Exception as e:
                add_log("warning", f"[{email}] PKCE 生成失败，回退普通注册: {e}")

        try:
            from camoufox.async_api import AsyncCamoufox
            from camoufox import DefaultAddons
        except ImportError:
            result["error"] = "camoufox 未安装"
            add_log("error", f"[{email}] camoufox 未安装")
            return result

        camoufox = None
        browser = None
        try:
            add_log("info", f"[{email}] 启动浏览器...")
            camoufox = AsyncCamoufox(
                headless=True,
                exclude_addons=[DefaultAddons.UBO],
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            browser = await camoufox.start()

            # 设置代理
            context_kwargs: dict[str, Any] = {}
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

            # ── Step 1: 访问登录/注册页面（优先 OAuth authorize，可拿 refresh_token；失败自动回退 chatgpt 入口） ──
            entry_url = authorize_url or "https://chatgpt.com/auth/login"
            add_log("info", f"[{email}] Step 1: 访问登录页面 ({'OAuth authorize' if authorize_url else 'chatgpt.com'})...")
            await page.goto(entry_url, wait_until="networkidle", timeout=60000)
            await asyncio.sleep(5)

            # 先关闭 cookie 弹窗（如果有的话）
            try:
                cookie_reject = page.locator('button:has-text("Reject optional"), button:has-text("Reject"), button:has-text("拒绝")').first
                if await cookie_reject.count() > 0:
                    await cookie_reject.click()
                    add_log("info", f"[{email}] 已关闭 cookie 弹窗")
                    await asyncio.sleep(2)
            except Exception:
                pass

            # ── Step 2: 输入邮箱并点击继续 ──
            add_log("info", f"[{email}] Step 2: 输入邮箱...")
            # 等待邮箱输入框可交互
            email_input = page.locator('input[name="email"]')
            try:
                await email_input.wait_for(state="visible", timeout=15000)
            except Exception:
                # 尝试其他选择器
                email_input = page.locator('input[type="email"], input[placeholder*="email" i], input[id*="email" i]').first
                try:
                    await email_input.wait_for(state="visible", timeout=10000)
                except Exception:
                    await page.screenshot(path=f"debug_login_{email.split('@')[0]}.png")
                    result["error"] = "邮箱输入框不可见"
                    add_log("error", f"[{email}] 邮箱输入框不可见，已截图")
                    return result

            # 先记录当前收件箱的旧验证码（用 Graph API 直接读取）
            add_log("info", f"[{email}] 记录旧验证码...")
            old_codes: set[str] = set()
            try:
                existing_emails = await graph_email_service.get_email_list(email, client_id, refresh_token, top=10)
                for mail in existing_emails:
                    subject = mail.get("subject", "")
                    if "chatgpt" in subject.lower() or "openai" in subject.lower():
                        body = await graph_email_service.get_email_body(email, mail.get("id", ""), client_id, refresh_token)
                        code = graph_email_service.extract_otp_code(body)
                        if code:
                            old_codes.add(code)
                add_log("info", f"[{email}] 已记录 {len(old_codes)} 个旧验证码")
            except Exception as e:
                add_log("warning", f"[{email}] 记录旧验证码异常: {e}")

            # 用 click + type 模拟真实键盘输入（触发 Vue/React 状态更新）
            await email_input.click()
            await asyncio.sleep(0.3)
            await email_input.fill("")  # 清空
            await page.keyboard.type(email, delay=50)  # 模拟逐字输入
            add_log("info", f"[{email}] 邮箱已输入")
            await asyncio.sleep(1)

            # 点击 Continue 按钮（确保按钮可见且可交互）
            continue_btn = page.locator('button[type="submit"]')
            try:
                await continue_btn.wait_for(state="visible", timeout=10000)
                await continue_btn.click(timeout=10000)
            except Exception as e:
                add_log("warning", f"[{email}] Continue 按钮点击异常: {e}，尝试 Enter...")
                await page.keyboard.press("Enter")
            add_log("info", f"[{email}] 已提交邮箱，等待页面跳转...")

            # ── Step 3: 等待跳转到验证码页面（带重试） ──
            add_log("info", f"[{email}] Step 3: 等待验证码页面...")
            max_retries = 2
            for retry in range(max_retries):
                try:
                    await page.wait_for_url("**/email-verification**", timeout=30000)
                    add_log("info", f"[{email}] 已到达验证码页面")
                    break
                except Exception:
                    current_url = page.url
                    add_log("warning", f"[{email}] 未到达验证码页面 (第 {retry+1} 次)，当前: {current_url[:80]}")
                    if "email-verification" in current_url:
                        break
                    if retry < max_retries - 1:
                        # 重试：重新输入邮箱并提交
                        add_log("info", f"[{email}] 重试提交邮箱...")
                        try:
                            email_input2 = page.locator('input[name="email"]')
                            await email_input2.wait_for(state="visible", timeout=10000)
                            await email_input2.click()
                            await email_input2.fill("")
                            await page.keyboard.type(email, delay=50)
                            await asyncio.sleep(0.5)
                            continue_btn2 = page.locator('button[type="submit"]')
                            await continue_btn2.click(timeout=10000)
                        except Exception as e2:
                            add_log("warning", f"[{email}] 重试提交异常: {e2}")
                    else:
                        result["error"] = f"页面跳转异常: {current_url[:80]}"
                        return result

            # ── Step 4: 等待验证码邮件（用 Graph API 直接读取，跳过旧验证码） ──
            add_log("info", f"[{email}] Step 4: 等待验证码邮件...")
            otp_code = await self._wait_for_new_otp(
                email, password, client_id, refresh_token, set(),
                timeout_sec=self.otp_timeout,
                poll_interval=self.otp_poll,
                skip_code=old_codes.pop() if old_codes else "",
            )
            if not otp_code:
                result["error"] = "验证码等待超时"
                add_log("error", f"[{email}] {result['error']}")
                return result
            add_log("info", f"[{email}] 验证码获取成功: {otp_code}")

            # ── Step 5: 输入验证码 ──
            add_log("info", f"[{email}] Step 5: 输入验证码...")
            # 验证码输入框是单个 input[name="code"]，maxlength=6
            code_input = page.locator('input[name="code"]')
            try:
                await code_input.wait_for(state="visible", timeout=10000)
            except Exception:
                # 兜底选择器
                code_input = page.locator('input[placeholder*="code" i], input[placeholder*="Code" i]').first
            await code_input.click()
            await code_input.fill("")
            await page.keyboard.type(otp_code, delay=100)
            add_log("info", f"[{email}] 验证码已输入")

            await asyncio.sleep(0.5)

            # 点击 Continue 按钮提交
            verify_btn = page.locator('button[type="submit"]').first
            if await verify_btn.count() == 0:
                verify_btn = page.locator('button:has-text("Continue"), button:has-text("Verify"), button:has-text("继续")').first
            await verify_btn.click()
            add_log("info", f"[{email}] 已提交验证码")

            # ── Step 6: 等待跳转到 about-you 页面（带重试） ──
            add_log("info", f"[{email}] Step 6: 等待 about-you 页面...")
            try:
                await page.wait_for_url("**/about-you**", timeout=20000)
                add_log("info", f"[{email}] 已到达 about-you 页面")
            except Exception:
                current_url = page.url
                add_log("warning", f"[{email}] 未到达 about-you 页面，当前: {current_url[:80]}")
                if "about-you" not in current_url:
                    # 尝试刷新页面
                    add_log("info", f"[{email}] 尝试刷新页面...")
                    await page.reload(wait_until="networkidle", timeout=15000)
                    await asyncio.sleep(2)
                    current_url = page.url
                    if "about-you" not in current_url:
                        result["error"] = f"验证码提交后跳转异常: {current_url[:80]}"
                        return result

            # ── Step 7: 填写姓名和生日 ──
            add_log("info", f"[{email}] Step 7: 填写姓名和生日: {name}, {birthdate}...")
            await asyncio.sleep(2)  # 等待页面完全加载

            # 姓名输入框
            name_input = page.locator('input[name="name"]')
            try:
                await name_input.wait_for(state="visible", timeout=10000)
            except Exception:
                # 兜底选择器
                name_input = page.locator('input[placeholder*="name" i], input[placeholder*="Name" i], input[id*="name" i]').first
                try:
                    await name_input.wait_for(state="visible", timeout=5000)
                except Exception:
                    await page.screenshot(path=f"debug_aboutyou_{email.split('@')[0]}.png")
                    result["error"] = "姓名输入框不可见"
                    add_log("error", f"[{email}] 姓名输入框不可见，已截图")
                    return result

            await name_input.click()
            await name_input.fill("")
            await page.keyboard.type(name, delay=50)
            add_log("info", f"[{email}] 姓名已输入")
            await asyncio.sleep(0.5)

            # 年龄输入框（about-you 页面用 input[name="age"] 数字输入，不是 select）
            age_input = page.locator('input[name="age"]')
            try:
                # 计算年龄
                from datetime import datetime
                birth_year = int(birthdate.split("-")[0])
                current_year = datetime.now().year
                age = current_year - birth_year
                # 用 JavaScript 直接设置年龄值（避免 click 被遮挡的问题）
                await page.evaluate(f"""() => {{
                    const ageInput = document.querySelector('input[name=\"age\"]');
                    if (ageInput) {{
                        ageInput.value = '{age}';
                        ageInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        ageInput.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    }}
                }}""")
                add_log("info", f"[{email}] 年龄已输入: {age}")
            except Exception as e:
                add_log("warning", f"[{email}] 年龄输入异常: {e}")

            # 设置隐藏的生日字段
            try:
                await page.evaluate(f"() => {{ const b = document.querySelector('input[name=\"birthday\"]'); if (b) b.value = '{birthdate}'; }}")
                add_log("info", f"[{email}] 生日已设置: {birthdate}")
            except Exception:
                pass

            await asyncio.sleep(0.5)

            # 点击 "Finish creating account" 按钮
            finish_btn = page.locator('button[type="submit"]').first
            try:
                await finish_btn.wait_for(state="visible", timeout=10000)
                await finish_btn.click()
            except Exception:
                await page.keyboard.press("Enter")
            add_log("info", f"[{email}] 已提交姓名和生日")

            # ── Step 8: 等待注册完成并获取 token ──
            add_log("info", f"[{email}] Step 8: 等待注册完成...")
            await asyncio.sleep(8)

            # 若走了 OAuth authorize，注册完成后会自动重定向到 redirect_uri 并携带 code，
            # 用 code + verifier 换取 access_token + refresh_token + id_token（长期续期）
            if authorize_url:
                code = await self._wait_for_oauth_code(page, timeout=45)
                if code:
                    add_log("info", f"[{email}] 捕获 OAuth code，换取 token 三件套...")
                    tokens = await self._exchange_code(code, code_verifier, proxy_url)
                    if tokens and tokens.get("access_token"):
                        result["access_token"] = tokens["access_token"]
                        result["refresh_token"] = tokens.get("refresh_token", "")
                        result["id_token"] = tokens.get("id_token", "")
                        add_log("info", f"[{email}] ✅ 已获取 OpenAI 三件套 (refresh_token 可用于长期续期)")
                    else:
                        add_log("warning", f"[{email}] OAuth code 换 token 失败，回退 session API")
                else:
                    add_log("warning", f"[{email}] 未捕获 OAuth code，回退 session API")

            # 先导航到 chatgpt.com 主页（确保登录状态稳定）
            add_log("info", f"[{email}] 导航到 chatgpt.com 主页...")
            try:
                await page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(5)
            except Exception:
                pass

            # 方法1: 从 session API 获取标准 JWT access_token（带重试）
            for attempt in range(4):
                try:
                    session_resp = await page.request.get("https://chatgpt.com/api/auth/session", timeout=15000)
                    if session_resp.status == 200:
                        session_data = await session_resp.json()
                        access_token = session_data.get("accessToken", "")
                        if access_token:
                            result["access_token"] = access_token
                            add_log("info", f"[{email}] 从 session API 获取到 JWT token")
                            break
                except Exception as e:
                    add_log("warning", f"[{email}] session 获取异常 (第 {attempt+1} 次): {e}")
                await asyncio.sleep(3)

            # 方法2: 从 cookie 中提取（兜底）
            if not result["access_token"]:
                try:
                    cookies = await context.cookies()
                    for cookie in cookies:
                        name = cookie.get("name", "")
                        value = cookie.get("value", "")
                        # 标准 JWT 通常较长且含 3 个点分隔段
                        if value.startswith("eyJ") and len(value) > 500:
                            result["access_token"] = value
                            add_log("info", f"[{email}] 从 cookie 获取到 JWT token: {name}")
                            break
                except Exception as e:
                    add_log("warning", f"[{email}] cookie 获取异常: {e}")

            if result["access_token"]:
                result["status"] = "success"
                add_log("info", f"[{email}] ✅ 注册成功！已获取 access_token")
            else:
                result["status"] = "success_no_token"
                result["error"] = "注册成功但未获取到 token"
                add_log("warning", f"[{email}] ⚠️ 注册完成但未获取到 token")

            await page.close()
            await context.close()

        except Exception as e:
            result["error"] = f"浏览器注册异常: {e}"
            add_log("error", f"[{email}] 异常: {e}")
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

        return result

    async def _wait_for_oauth_code(self, page: Any, timeout: int = 45) -> str:
        """轮询页面 URL，等待 OAuth authorize 重定向到 redirect_uri 并携带 code"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                url = page.url
                if url and "code=" in url:
                    q = parse_qs(urlparse(url).query)
                    code = (q.get("code") or [""])[0]
                    if code:
                        return code
            except Exception:
                pass
            await asyncio.sleep(1)
        return ""

    async def _exchange_code(self, code: str, code_verifier: str,
                             proxy_url: str | None) -> dict | None:
        """用 OAuth code + verifier 换取 access_token / refresh_token / id_token"""
        try:
            proxy_kwargs: dict[str, Any] = {}
            if proxy_url:
                proxy_kwargs["proxy"] = proxy_url
            async with httpx.AsyncClient(timeout=60, verify=False, **proxy_kwargs) as client:
                resp = await client.post(
                    "https://auth.openai.com/api/accounts/oauth/token",
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "Origin": "https://platform.openai.com",
                        "Referer": "https://platform.openai.com/",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
                    },
                    json={
                        "client_id": OAUTH_CLIENT_ID,
                        "code_verifier": code_verifier,
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": OAUTH_REDIRECT_URI,
                    },
                )
                data = resp.json() if resp.text else {}
                if resp.status_code == 200 and data.get("access_token"):
                    return {
                        "access_token": data["access_token"],
                        "refresh_token": data.get("refresh_token", ""),
                        "id_token": data.get("id_token", ""),
                    }
                add_log("warning", f"OAuth code 换 token 失败: HTTP {resp.status_code}")
                return None
        except Exception as e:
            add_log("warning", f"OAuth code 换 token 异常: {e}")
            return None


browser_register: BrowserRegister | None = None


def get_browser_register(config: dict[str, Any]) -> BrowserRegister:
    global browser_register
    if browser_register is None:
        browser_register = BrowserRegister(config)
    return browser_register
