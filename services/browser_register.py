from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import secrets
import string
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from services.db import add_log
from services.email_service import email_service
from services.graph_email_service import graph_email_service
from services.name_service import name_service
from services.proxy_service import proxy_service
from services.browser_selectors import (
    COOKIE_REJECT_SELECTORS,
    EMAIL_FALLBACK,
    EMAIL_PRIMARY,
    NAME_FALLBACK,
    NAME_PRIMARY,
    NEW_PASSWORD_PRIMARY,
    OTP_FALLBACK,
    OTP_PRIMARY,
    SUBMIT_FALLBACK,
    SUBMIT_PRIMARY,
    SWITCH_OTP_SELECTORS,
)

# B7：debug 截图目录，自动创建
_DEBUG_DIR = Path(__file__).resolve().parent.parent / "data" / "debug"


def _ensure_debug_dir() -> None:
    _DEBUG_DIR.mkdir(parents=True, exist_ok=True)


async def _capture_debug(page, email: str, tag: str = "") -> str:
    """截取 full_page 截图 + DOM 快照，保存到 data/debug/，返回截图路径（相对 data/）。"""
    _ensure_debug_dir()
    prefix = email.split("@")[0] if email else "unknown"
    ts = int(time.time())
    safename = "".join(c for c in prefix if c.isalnum() or c in "._-")[:30]
    try:
        shot_path = f"debug/{safename}_{tag}_{ts}.png"
        await page.screenshot(
            path=str(_DEBUG_DIR.parent / shot_path),
            full_page=True,
        )
        # DOM 快照
        dom_path = f"debug/{safename}_{tag}_{ts}.html"
        dom = await page.content()
        (_DEBUG_DIR.parent / dom_path).write_text(dom, encoding="utf-8")
        return shot_path
    except Exception:
        return ""

# OAuth PKCE 常量：收敛到 services/constants.py（B5），此处保留别名供旧 import 向后兼容
from services.constants import (
    OAUTH_CLIENT_ID,
    OAUTH_REDIRECT_URI,
    OAUTH_AUDIENCE,
    OAUTH_AUTH0_CLIENT,
    tls_verify_enabled,
)


def _generate_pkce() -> tuple[str, str]:
    """生成 PKCE code_verifier 与 S256 code_challenge"""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def gen_password(length: int = 16) -> str:
    """生成 OpenAI 账号密码（随机字母数字）"""
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(length))


def _as_int(value, default: int) -> int:
    """防御式整数解析：settings API 存字符串，非法/空回退默认。"""
    if value is None or value == "":
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


class BrowserRegister:
    """使用 camoufox 浏览器完成 ChatGPT 注册（执行 JS，触发验证码发送）"""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.otp_timeout = _as_int(config.get("otp_wait_timeout_sec"), 600)
        self.otp_poll = _as_int(config.get("otp_poll_interval_sec"), 5)
        # CF 挑战解不开时换代理重试次数（每次重试 = 关 context → 换代理 → 重新 goto）
        # 默认 2：第一代理 CF 拦截，换一个出口 IP 再试一轮仍失败才降级 cf_blocked
        self._cf_retry_max = _as_int(config.get("cf_retry_max"), 2)

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
                min_age_window_sec=_as_int(self.config.get("otp_min_age_window_sec"), 120),
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

    # ─────────────────────────────────────────────
    # 页面形态检测 / CF 处理（配合 services.login_detector）
    # ─────────────────────────────────────────────

    async def _collect_page_inputs(self, page) -> list[str]:
        """采集页面可见 input 的 name/type/placeholder/autocomplete。"""
        try:
            return await page.evaluate(
                """() => Array.from(document.querySelectorAll('input'))
                    .map(i => (i.name || i.type || i.placeholder || i.autocomplete) || '')
                    .filter(Boolean).slice(0, 20)"""
            )
        except Exception:
            return []

    async def _page_html(self, page) -> str:
        try:
            return await page.content()
        except Exception:
            return ""

    async def _page_body_text(self, page) -> str:
        try:
            return await page.evaluate(
                "() => document.body ? document.body.innerText.slice(0, 500) : ''"
            )
        except Exception:
            return ""

    async def _try_click_turnstile(self, page) -> bool:
        """遍历 frame 尝试点掉 Turnstile 复选框，返回是否点中。"""
        try:
            for frame in page.frames:
                url = (frame.url or "").lower()
                if "turnstile" in url or "challenges.cloudflare" in url:
                    checkbox = frame.locator(
                        "#challenge-stage input[type=checkbox], input[type=checkbox]"
                    ).first
                    if await checkbox.count():
                        await checkbox.click(timeout=1500)
                        return True
        except Exception:
            pass
        return False

    async def _resolve_cf(self, page, email: str, max_attempts: int = 3) -> bool:
        """尝试自动解 Cloudflare 挑战（点复选框 + 调 CF solver 拿 token 兜底）。"""
        from services.login_detector import is_cf_challenge
        from services.cf_solver_service import cf_solver_service

        for attempt in range(max_attempts):
            clicked = await self._try_click_turnstile(page)
            if clicked:
                add_log("info", f"[{email}] 点击了 Turnstile 复选框 (第 {attempt + 1} 次)")
                await asyncio.sleep(4)
            if not is_cf_challenge(page.url, await self._page_html(page)):
                return True
            # v3.4 T87：复选框解不开时调 CF solver 拿 Turnstile token
            if not clicked:
                try:
                    sitekey = await page.evaluate(
                        """() => {
                            const el = document.querySelector('[data-sitekey]');
                            return el ? el.getAttribute('data-sitekey') : '';
                        }"""
                    )
                    if sitekey:
                        add_log("info", f"[{email}] 调用 CF solver 解 Turnstile (sitekey={sitekey[:8]}...)")
                        solver_result = await cf_solver_service.get_turnstile_token(page.url, sitekey, timeout=60)
                        if solver_result.get("status") == "success" and solver_result.get("value"):
                            token = solver_result["value"]
                            await page.evaluate(f"""() => {{
                                const el = document.querySelector('[name="cf-turnstile-response"]');
                                if (el) el.value = '{token}';
                            }}""")
                            await asyncio.sleep(3)
                            if not is_cf_challenge(page.url, await self._page_html(page)):
                                add_log("info", f"[{email}] CF solver 成功解 Turnstile")
                                return True
                except Exception as e:
                    add_log("warning", f"[{email}] CF solver 调用异常: {e}")
            await asyncio.sleep(3)
        return False

    async def register_one(self, email: str, password: str, client_id: str,
                           refresh_token: str) -> dict[str, Any]:
        """用浏览器注册单个账号"""
        result: dict[str, Any] = {
            "email": email, "status": "failed", "error": "",
            "access_token": "", "refresh_token": "", "id_token": "",
            "openai_password": "",
            "name": "", "birthdate": "", "proxy": "",
            "screenshot": "",
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
            from browserforge.fingerprints import Screen  # 屏幕尺寸约束
        except ImportError:
            result["error"] = "camoufox 未安装"
            add_log("error", f"[{email}] camoufox 未安装")
            return result

        camoufox = None
        browser = None
        # v3.1 T6：浏览器池复用（仅 config.browser_pool_size>0 时启用；kookeey 动态代理默认 0 不池化防串 IP）。
        # 代理按 new_context 设置、browser 实例本身代理无关，池化 browser + 每账号新建 context 不会串 IP。
        from services.browser_pool import get_browser_pool
        pool = get_browser_pool(self.config)
        pooled_inst = None
        pooled_ok = False
        try:
            add_log("info", f"[{email}] 启动浏览器...")
            if pool is not None:
                pooled_inst = await pool.acquire(proxy_url or "")
            if pooled_inst is not None:
                if pooled_inst.camoufox is None:
                    # 占位实例：锁外填充（camoufox 启动慢），启动后存入池实例供后续复用
                    pooled_inst.camoufox = AsyncCamoufox(
                        headless=True,
                        exclude_addons=[DefaultAddons.UBO],
                        args=["--no-sandbox", "--disable-setuid-sandbox"],
                        # v3.4 T86：浏览器指纹一致性——伪装为美国 Windows 系统，防 American IP + 中文时区被地理校验标记
                        os=["windows", "macos"],
                        screen=Screen(max_width=1920, max_height=1080),
                        locale="en-US",
                        block_webrtc=True,
                        disable_coop=True,
                        humanize=1.5,
                    )
                    pooled_inst.browser = await pooled_inst.camoufox.start()
                    add_log("info", f"[{email}] 浏览器池：新实例已启动并入池")
                else:
                    add_log("info", f"[{email}] 浏览器池：复用已启动实例")
                camoufox = pooled_inst.camoufox
                browser = pooled_inst.browser
            else:
                # 冷启动（默认路径：未池化，或池满 acquire 超时兜底）
                camoufox = AsyncCamoufox(
                    headless=True,
                    exclude_addons=[DefaultAddons.UBO],
                    args=["--no-sandbox", "--disable-setuid-sandbox"],
                    # v3.4 T86：浏览器指纹一致性——伪装为美国 Windows 系统，防 American IP + 中文时区被地理校验标记
                    os=["windows", "macos"],
                    screen=Screen(max_width=1920, max_height=1080),
                    locale="en-US",
                    block_webrtc=True,
                    disable_coop=True,
                    humanize=1.5,
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

            # v3.4 T86：浏览器地理/时区一致性——使 Intl API 时区与 kookeey 出口 IP 一致
            # camoufox 的 geoip 基于启动时公共 IP，per-context 代理下可能取宿主 IP 而非代理出口 IP，
            # 稳妥从 kookeey 行的 country 字段显式映射 timezone/locale 到 new_context。
            # 覆盖 kookeey 支持的 40+ 国家（v3.4 补全：此前仅 14 国，其余回退到 US 时区）
            _country_to_timezone = {
                # 北美
                "US": "America/New_York", "CA": "America/Toronto", "MX": "America/Mexico_City",
                # 欧洲
                "GB": "Europe/London", "DE": "Europe/Berlin", "FR": "Europe/Paris",
                "IT": "Europe/Rome", "ES": "Europe/Madrid", "NL": "Europe/Amsterdam",
                "BE": "Europe/Brussels", "CH": "Europe/Zurich", "SE": "Europe/Stockholm",
                "NO": "Europe/Oslo", "DK": "Europe/Copenhagen", "FI": "Europe/Helsinki",
                "PL": "Europe/Warsaw", "AT": "Europe/Vienna", "IE": "Europe/Dublin",
                "PT": "Europe/Lisbon", "GR": "Europe/Athens", "CZ": "Europe/Prague",
                "RO": "Europe/Bucharest", "HU": "Europe/Budapest", "UA": "Europe/Kyiv",
                "RU": "Europe/Moscow", "TR": "Europe/Istanbul",
                # 亚太
                "JP": "Asia/Tokyo", "SG": "Asia/Singapore", "HK": "Asia/Hong_Kong",
                "TW": "Asia/Taipei", "KR": "Asia/Seoul", "CN": "Asia/Shanghai",
                "AU": "Australia/Sydney", "NZ": "Pacific/Auckland", "IN": "Asia/Kolkata",
                "TH": "Asia/Bangkok", "MY": "Asia/Kuala_Lumpur", "PH": "Asia/Manila",
                "ID": "Asia/Jakarta", "VN": "Asia/Ho_Chi_Minh", "PK": "Asia/Karachi",
                "BD": "Asia/Dhaka", "IL": "Asia/Jerusalem", "SA": "Asia/Riyadh",
                "AE": "Asia/Dubai",
                # 南美
                "BR": "America/Sao_Paulo", "AR": "America/Argentina/Buenos_Aires",
                "CL": "America/Santiago", "CO": "America/Bogota", "PE": "America/Lima",
                # 中东 / 非洲
                "ZA": "Africa/Johannesburg", "NG": "Africa/Lagos", "KE": "Africa/Nairobi",
                "EG": "Africa/Cairo",
            }
            proxy_country = proxy_service.country if hasattr(proxy_service, 'country') else "US"
            context_kwargs["locale"] = "en-US"
            context_kwargs["timezone_id"] = _country_to_timezone.get(proxy_country, "America/New_York")

            context = await browser.new_context(**context_kwargs)
            page = await context.new_page()

            # ── Step 1: 访问登录/注册页面（优先 OAuth authorize，可拿 refresh_token；失败自动回退 chatgpt 入口） ──
            entry_url = authorize_url or "https://chatgpt.com/auth/login"
            add_log("info", f"[{email}] Step 1: 访问登录页面 ({'OAuth authorize' if authorize_url else 'chatgpt.com'})...")
            await page.goto(entry_url, wait_until="networkidle", timeout=60000)
            await asyncio.sleep(5)

            # 先关闭 cookie 弹窗（如果有的话）
            try:
                cookie_reject = page.locator(", ".join(COOKIE_REJECT_SELECTORS)).first
                if await cookie_reject.count() > 0:
                    await cookie_reject.click()
                    add_log("info", f"[{email}] 已关闭 cookie 弹窗")
                    await asyncio.sleep(2)
            except Exception:
                pass

            # ── Step 1.1: 页面形态检测与分流（登录页可能是邮箱/密码/验证码/CF） ──
            # 修复：此前只识别 input[name=email]，密码页 / CF 页 / 改版页会裸超时 15s。
            from services.login_detector import (
                detect_login_page, decide_login_action, summarize_page_text,
            )
            inputs = await self._collect_page_inputs(page)
            page_kind = detect_login_page(page.url, await self._page_html(page), inputs)
            action = decide_login_action(page_kind)
            add_log("info", f"[{email}] 页面形态: {page_kind} (动作: {action})")

            # CF 挑战：先尝试自动解；解不开则换代理重试最多 _cf_retry_max 次，仍失败才 cf_blocked（不裸超时）
            if page_kind == "cf":
                cf_attempts = 0
                cf_resolved = False
                while cf_attempts <= self._cf_retry_max:
                    add_log("info", f"[{email}] 检测到 Cloudflare 挑战，尝试自动解 (第 {cf_attempts + 1} 次)...")
                    if await self._resolve_cf(page, email):
                        cf_resolved = True
                        break
                    if cf_attempts >= self._cf_retry_max:
                        break
                    # 换代理重试：关当前 context，从池取下一个代理开新 context 重新 goto
                    new_proxy = proxy_service.get_next() if self.config.get("use_proxy", False) else None
                    if not new_proxy:
                        add_log("warning", f"[{email}] CF 未解且代理池空，不再重试")
                        break
                    add_log("info", f"[{email}] CF 未解，换代理重试: {proxy_service.format_for_display(new_proxy)}")
                    proxy_url = new_proxy
                    # 同步更新 result["proxy"]，避免落库记录的是换代理前的旧代理
                    result["proxy"] = proxy_service.format_for_display(new_proxy)
                    try:
                        await context.close()
                    except Exception:
                        pass
                    # 新 context/page 创建可能抛异常（代理无效/浏览器卡死），必须有兜底：
                    # 失败则跳出循环直接 cf_blocked，避免 page 悬空后续访问 NPE
                    try:
                        ctx_kwargs2: dict[str, Any] = {"proxy": {"server": new_proxy}} if new_proxy else {}
                        context = await browser.new_context(**ctx_kwargs2)
                        page = await context.new_page()
                    except Exception as e:
                        add_log("warning", f"[{email}] 换代理后创建 context 失败，降级 cf_blocked: {e}")
                        break
                    try:
                        await page.goto(entry_url, wait_until="networkidle", timeout=60000)
                        await asyncio.sleep(5)
                    except Exception as e:
                        add_log("warning", f"[{email}] 换代理后 goto 异常: {e}")
                    cf_attempts += 1

                if not cf_resolved:
                    result["status"] = "cf_blocked"
                    result["error"] = "Cloudflare 人机验证无法自动通过"
                    result["screenshot"] = await _capture_debug(page, email, "cf_blocked")
                    add_log("warning", f"[{email}] CF 挑战未通过 (尝试 {cf_attempts + 1} 次)")
                    return result
                inputs = await self._collect_page_inputs(page)
                page_kind = detect_login_page(page.url, await self._page_html(page), inputs)
                action = decide_login_action(page_kind)
                add_log("info", f"[{email}] 解 CF 后页面形态: {page_kind}")

            # 无法识别：附带页面文字摘要，便于快速适配上游改版
            if action == "fail":
                body_text = summarize_page_text(await self._page_body_text(page))
                result["screenshot"] = await _capture_debug(page, email, "unknown_page")
                result["error"] = f"无法识别登录页: {page.url[:80]}"
                add_log("error", f"[{email}] 无法识别登录页，页面文字: {body_text[:200]}")
                return result

            # 密码登录页：切换到邮箱验证码登录（半成品账号补完路径）
            if action == "switch_otp_login":
                add_log("info", f"[{email}] 密码登录页，切换邮箱验证码登录...")
                clicked = False
                for sel in SWITCH_OTP_SELECTORS:
                    try:
                        btn = page.locator(sel).first
                        if await btn.count():
                            await btn.click(timeout=5000)
                            clicked = True
                            break
                    except Exception:
                        continue
                if not clicked:
                    result["screenshot"] = await _capture_debug(page, email, "switch_otp_fail")
                    result["error"] = "密码登录页但无法切换到邮箱验证码登录"
                    add_log("error", f"[{email}] {result['error']}")
                    return result
                add_log("info", f"[{email}] 已切换到邮箱验证码登录")
                await asyncio.sleep(3)

            # 已位于回调页：直接捕获 OAuth code 换 token（三件套已是最终凭证）
            if page_kind == "callback":
                add_log("info", f"[{email}] 已在回调页，直接捕获 OAuth code 换 token...")
                code = await self._wait_for_oauth_code(page, timeout=15)
                if code:
                    tokens = await self._exchange_code(code, code_verifier, proxy_url)
                    if tokens and tokens.get("access_token"):
                        result["access_token"] = tokens["access_token"]
                        result["refresh_token"] = tokens.get("refresh_token", "")
                        result["id_token"] = tokens.get("id_token", "")
                        result["status"] = "success"
                        add_log("info", f"[{email}] ✅ 回调页直接获取到 token 三件套")
                        return result
                result["error"] = "回调页但未捕获到 OAuth code"
                add_log("error", f"[{email}] {result['error']}")
                return result

            # 已位于 about-you / 验证码页：不再输入邮箱
            skip_email_input = action in ("wait_otp", "switch_otp_login", "skip_login")
            already_on_about_you = page_kind == "about-you"

            # ── Step 1.5: 新账号注册页（create-account/password）→ 设置 OpenAI 密码 ──
            openai_password_set = False
            if authorize_url and "create-account" in page.url:
                add_log("info", f"[{email}] 检测到新账号注册页，设置 OpenAI 密码...")
                openai_password = gen_password()
                result["openai_password"] = openai_password
                try:
                    new_pw = page.locator(NEW_PASSWORD_PRIMARY)
                    await new_pw.wait_for(state="visible", timeout=10000)
                    await new_pw.click()
                    await new_pw.fill("")
                    await page.keyboard.type(openai_password, delay=30)
                    await asyncio.sleep(0.5)
                    await page.locator(SUBMIT_PRIMARY).first.click(timeout=10000)
                    add_log("info", f"[{email}] ✅ 已设置 OpenAI 密码 (可账号密码登录)")
                    openai_password_set = True
                    await asyncio.sleep(3)
                except Exception as e:
                    add_log("warning", f"[{email}] 设置密码失败: {e}")

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

            # ── Step 2: 输入邮箱并点击继续（已设密码 / 已在后续页面则跳过）──
            if not openai_password_set and not skip_email_input:
                add_log("info", f"[{email}] Step 2: 输入邮箱...")
                email_input = page.locator(EMAIL_PRIMARY)
                try:
                    await email_input.wait_for(state="visible", timeout=8000)
                except Exception:
                    # 尝试其他选择器
                    email_input = page.locator(EMAIL_FALLBACK).first
                    try:
                        await email_input.wait_for(state="visible", timeout=8000)
                    except Exception:
                        result["screenshot"] = await _capture_debug(page, email, "email_not_visible")
                        result["error"] = "邮箱输入框不可见"
                        add_log("error", f"[{email}] 邮箱输入框不可见，已截图")
                        return result

                # 用 click + type 模拟真实键盘输入（触发 Vue/React 状态更新）
                await email_input.click()
                await asyncio.sleep(0.3)
                await email_input.fill("")  # 清空
                await page.keyboard.type(email, delay=50)  # 模拟逐字输入
                add_log("info", f"[{email}] 邮箱已输入")
                await asyncio.sleep(1)

                # 点击 Continue 按钮（确保按钮可见且可交互）
                continue_btn = page.locator(SUBMIT_PRIMARY)
                try:
                    await continue_btn.wait_for(state="visible", timeout=10000)
                    await continue_btn.click(timeout=10000)
                except Exception as e:
                    add_log("warning", f"[{email}] Continue 按钮点击异常: {e}，尝试 Enter...")
                    await page.keyboard.press("Enter")
                add_log("info", f"[{email}] 已提交邮箱，等待页面跳转...")
            else:
                add_log("info", f"[{email}] 已设置密码或已在后续页面，跳过填邮箱")

            # ── Step 3: 等待跳转到验证码页面（带重试；已到后续页则跳过） ──
            if already_on_about_you:
                add_log("info", f"[{email}] 已在 about-you 页，跳过验证码等待")
            elif skip_email_input:
                add_log("info", f"[{email}] Step 3: 等待验证码输入框出现...")
                try:
                    otp_box = page.locator(OTP_PRIMARY).first
                    await otp_box.wait_for(state="visible", timeout=20000)
                except Exception:
                    add_log("warning", f"[{email}] 验证码输入框未在 20s 内出现，继续等待邮件")
            else:
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
                                email_input2 = page.locator(EMAIL_PRIMARY)
                                await email_input2.wait_for(state="visible", timeout=10000)
                                await email_input2.click()
                                await email_input2.fill("")
                                await page.keyboard.type(email, delay=50)
                                await asyncio.sleep(0.5)
                                continue_btn2 = page.locator(SUBMIT_PRIMARY)
                                await continue_btn2.click(timeout=10000)
                            except Exception as e2:
                                add_log("warning", f"[{email}] 重试提交异常: {e2}")
                        else:
                            result["screenshot"] = await _capture_debug(page, email, "page_jump_fail")
                            result["error"] = f"页面跳转异常: {current_url[:80]}"
                            return result

            # ── Step 4: 等待验证码邮件（用 Graph API 直接读取，跳过旧验证码） ──
            if already_on_about_you:
                add_log("info", f"[{email}] 已位于 about-you 页，无需验证码邮件")
                otp_code = ""
            else:
                add_log("info", f"[{email}] Step 4: 等待验证码邮件...")
                otp_code = await self._wait_for_new_otp(
                    email, password, client_id, refresh_token, set(),
                    timeout_sec=self.otp_timeout,
                    poll_interval=self.otp_poll,
                    skip_code=old_codes.pop() if old_codes else "",
                )
                if not otp_code:
                    result["screenshot"] = await _capture_debug(page, email, "otp_timeout")
                    result["error"] = "验证码等待超时"
                    add_log("error", f"[{email}] {result['error']}")
                    return result
                add_log("info", f"[{email}] 验证码获取成功: {otp_code}")

            # ── Step 5: 输入验证码（about-you 页无验证码，跳过） ──
            if otp_code:
                add_log("info", f"[{email}] Step 5: 输入验证码...")
                # 验证码输入框是单个 input[name="code"]，maxlength=6
                code_input = page.locator('input[name="code"]')
                try:
                    await code_input.wait_for(state="visible", timeout=10000)
                except Exception:
                    # 兜底选择器
                    code_input = page.locator(OTP_FALLBACK).first
                await code_input.click()
                await code_input.fill("")
                await page.keyboard.type(otp_code, delay=100)
                add_log("info", f"[{email}] 验证码已输入")

                await asyncio.sleep(0.5)

                # 点击 Continue 按钮提交
                verify_btn = page.locator(SUBMIT_PRIMARY).first
                if await verify_btn.count() == 0:
                    verify_btn = page.locator(SUBMIT_FALLBACK).first
                await verify_btn.click()
                add_log("info", f"[{email}] 已提交验证码")
            else:
                add_log("info", f"[{email}] 无验证码需要输入（已在 about-you 页）")

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
                        result["screenshot"] = await _capture_debug(page, email, "aboutyou_jump_fail")
                        result["error"] = f"验证码提交后跳转异常: {current_url[:80]}"
                        return result

            # ── Step 7: 填写姓名和生日 ──
            add_log("info", f"[{email}] Step 7: 填写姓名和生日: {name}, {birthdate}...")
            await asyncio.sleep(2)  # 等待页面完全加载

            # 姓名输入框
            name_input = page.locator(NAME_PRIMARY)
            try:
                await name_input.wait_for(state="visible", timeout=10000)
            except Exception:
                # 兜底选择器
                name_input = page.locator(NAME_FALLBACK).first
                try:
                    await name_input.wait_for(state="visible", timeout=5000)
                except Exception:
                        result["screenshot"] = await _capture_debug(page, email, "name_not_visible")
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
            finish_btn = page.locator(SUBMIT_PRIMARY).first
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
            pooled_ok = True  # 正常完成：实例健康，可归还池复用

        except Exception as e:
            result["screenshot"] = await _capture_debug(page, email, "exception") if 'page' in dir() and page else ""
            result["error"] = f"浏览器注册异常: {e}"
            add_log("error", f"[{email}] 异常: {e}")
        finally:
            if pooled_inst is not None:
                if pooled_ok:
                    await pool.release(pooled_inst)  # 正常归还，供下次复用
                else:
                    # 异常：实例可能已损坏，先关闭再标记为占位让 release 丢弃（不复用坏实例）
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
                    pooled_inst.camoufox = None
                    pooled_inst.browser = None
                    await pool.release(pooled_inst)  # camoufox=None → release 走占位丢弃分支
            else:
                # 冷启动路径：用完即关
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

    async def cleanup(self) -> None:
        """优雅停机：清理浏览器池持有的持久实例（v3.1 T6）。未池化时无操作。

        api/__init__.py 的 shutdown_handler 通过 getattr(browser_register, "cleanup") 调用。
        """
        try:
            from services.browser_pool import browser_pool
            if browser_pool is not None:
                n = await browser_pool.cleanup()
                if n:
                    add_log("info", f"浏览器池已清理 {n} 个持久实例")
        except Exception as e:
            add_log("warning", f"浏览器池清理异常: {e}")

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
            async with httpx.AsyncClient(timeout=60, verify=tls_verify_enabled(self.config), **proxy_kwargs) as client:
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
