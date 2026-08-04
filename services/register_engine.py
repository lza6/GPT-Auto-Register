from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from curl_cffi import requests as cffi_requests

from services.db import add_log, insert_account, mark_email_status, get_accounts
from services.email_service import email_service
from services.name_service import name_service
from services.proxy_service import proxy_service


class RegisterEngine:
    """ChatGPT 自动注册引擎 — curl_cffi Firefox 指纹 + CF solver 兜底"""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.ua = config.get(
            "user_agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
        )
        self.otp_timeout = int(config.get("otp_wait_timeout_sec", 120))
        self.otp_poll = int(config.get("otp_poll_interval_sec", 5))
        self.token_file = config.get("token_output_file", "已经获取到的token.txt")
        self._running = False
        self._paused = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True
        add_log("info", "注册任务已暂停")

    def resume(self) -> None:
        self._paused = False
        add_log("info", "注册任务已继续")

    def stop(self) -> None:
        self._running = False
        self._paused = False
        add_log("info", "注册任务已停止")

    def _append_token(self, token: str) -> None:
        try:
            with open(self.token_file, "a", encoding="utf-8") as f:
                f.write(token.strip() + "\n")
        except Exception as e:
            add_log("error", f"写入 token 文件失败: {e}")

    def _make_session(self, proxy_url: str | None = None) -> cffi_requests.Session:
        """创建 curl_cffi session（Firefox 指纹）"""
        session = cffi_requests.Session(
            impersonate="firefox",
            timeout=60,
        )
        if proxy_url:
            session.proxies = {
                "http": proxy_url,
                "https": proxy_url,
            }
        return session

    def _register_sync(self, email: str, password: str, client_id: str,
                       refresh_token: str, proxy_url: str | None) -> dict[str, Any]:
        """同步注册逻辑（curl_cffi 是同步库）"""
        result: dict[str, Any] = {
            "email": email, "status": "failed", "error": "",
            "access_token": "", "name": "", "birthdate": "", "proxy": "",
        }
        name = name_service.generate()
        birthdate = name_service.generate_birthdate()
        result["name"] = name
        result["birthdate"] = birthdate
        result["proxy"] = proxy_service.format_for_display(proxy_url)

        session = self._make_session(proxy_url)
        try:
            # ── Step 0: 先访问登录页面获取必要 cookie ──
            add_log("info", f"[{email}] Step 0: 访问登录页面...")
            login_page = session.get("https://chatgpt.com/auth/login")
            if login_page.status_code != 200:
                result["error"] = f"登录页面访问失败: HTTP {login_page.status_code}"
                add_log("error", f"[{email}] {result['error']}")
                return result
            add_log("info", f"[{email}] 登录页面访问成功")

            # ── Step 1: 获取 CSRF token ──
            add_log("info", f"[{email}] Step 1: 获取 CSRF token...")
            csrf_resp = session.get(
                "https://chatgpt.com/api/auth/csrf",
                headers={"Referer": "https://chatgpt.com/auth/login"},
            )
            if csrf_resp.status_code != 200:
                result["error"] = f"CSRF 获取失败: HTTP {csrf_resp.status_code}"
                add_log("error", f"[{email}] {result['error']}")
                return result
            csrf_token = csrf_resp.json().get("csrfToken", "")
            if not csrf_token:
                result["error"] = "CSRF token 为空"
                return result
            add_log("info", f"[{email}] CSRF token 获取成功")

            # ── Step 2: 发起登录（提交邮箱） ──
            add_log("info", f"[{email}] Step 2: 提交登录请求...")
            device_id = str(uuid.uuid4())
            session_log_id = str(uuid.uuid4())
            signin_resp = session.post(
                "https://chatgpt.com/api/auth/signin/openai",
                data={
                    "callbackUrl": "/",
                    "csrfToken": csrf_token,
                    "json": "true",
                },
                params={
                    "prompt": "login",
                    "ext-oai-did": device_id,
                    "auth_session_logging_id": session_log_id,
                    "screen_hint": "login_or_signup",
                    "login_hint": email,
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": "https://chatgpt.com",
                    "Referer": "https://chatgpt.com/auth/login",
                },
            )
            if signin_resp.status_code != 200:
                result["error"] = f"登录请求失败: HTTP {signin_resp.status_code}"
                add_log("error", f"[{email}] {result['error']}")
                return result

            signin_data = signin_resp.json()
            auth_url = signin_data.get("url", "")
            if not auth_url:
                result["error"] = "未获取到授权 URL"
                add_log("error", f"[{email}] {result['error']}")
                return result
            add_log("info", f"[{email}] 登录请求成功，跳转授权页面")

            # ── Step 3: 访问授权 URL（跳转到 auth.openai.com） ──
            add_log("info", f"[{email}] Step 3: 访问授权页面...")
            auth_resp = session.get(
                auth_url,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
                allow_redirects=True,
            )
            final_url = str(auth_resp.url)
            add_log("info", f"[{email}] 授权页面访问成功 (HTTP {auth_resp.status_code}, URL: {final_url[:80]})")

            # ── Step 4: 等待验证码邮件 ──
            add_log("info", f"[{email}] Step 4: 等待验证码邮件 (最长 {self.otp_timeout} 秒)...")
            loop = asyncio.new_event_loop()
            try:
                otp_code = loop.run_until_complete(
                    email_service.wait_for_otp(
                        email, password, client_id, refresh_token,
                        timeout_sec=self.otp_timeout,
                        poll_interval=self.otp_poll,
                        skip_existing=False,
                    )
                )
            finally:
                loop.close()

            if not otp_code:
                result["error"] = "验证码等待超时"
                add_log("error", f"[{email}] {result['error']}")
                return result
            add_log("info", f"[{email}] 验证码获取成功: {otp_code}")

            # ── Step 5: 提交验证码 ──
            add_log("info", f"[{email}] Step 5: 提交验证码...")
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
                result["error"] = f"验证码提交失败: HTTP {otp_resp.status_code} {otp_resp.text[:200]}"
                add_log("error", f"[{email}] {result['error']}")
                return result
            otp_data = otp_resp.json()
            add_log("info", f"[{email}] 验证码验证成功")

            # ── Step 6: 创建账号（提交姓名和生日） ──
            add_log("info", f"[{email}] Step 6: 创建账号 name={name}, birthdate={birthdate}...")
            create_resp = session.post(
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
                result["error"] = f"创建账号失败: HTTP {create_resp.status_code} {create_resp.text[:200]}"
                add_log("error", f"[{email}] {result['error']}")
                return result
            create_data = create_resp.json()
            callback_url = create_data.get("continue_url", "")
            add_log("info", f"[{email}] 账号创建成功")

            # ── Step 7: 获取 access_token ──
            add_log("info", f"[{email}] Step 7: 获取 access_token...")
            if callback_url:
                session.get(callback_url, allow_redirects=True)
                try:
                    session_resp = session.get("https://chatgpt.com/api/auth/session")
                    if session_resp.status_code == 200:
                        session_data = session_resp.json()
                        access_token = session_data.get("accessToken", "")
                        if access_token:
                            result["access_token"] = access_token
                except Exception as e:
                    add_log("warning", f"[{email}] session 获取异常: {e}")

            if result["access_token"]:
                result["status"] = "success"
                add_log("info", f"[{email}] ✅ 注册成功！已获取 access_token")
            else:
                result["status"] = "success_no_token"
                result["error"] = "注册成功但未获取到 access_token"
                add_log("warning", f"[{email}] ⚠️ 注册完成但未获取到 token")

        except Exception as e:
            result["error"] = f"注册异常: {e}"
            add_log("error", f"[{email}] 异常: {e}")
        finally:
            session.close()

        return result

    async def register_one(self, email: str, password: str, client_id: str,
                           refresh_token: str) -> dict[str, Any]:
        """注册单个账号 — 优先浏览器方式（执行 JS 触发验证码发送），浏览器失败不降级"""
        # 优先使用浏览器方式（能执行 JS，触发验证码邮件发送）
        use_browser = self.config.get("use_browser", True)
        if use_browser:
            try:
                from services.browser_register import get_browser_register
                browser_reg = get_browser_register(self.config)
                result = await browser_reg.register_one(email, password, client_id, refresh_token)
                return result
            except Exception as e:
                add_log("error", f"[{email}] 浏览器注册异常: {e}")
                return {"email": email, "status": "failed", "error": str(e),
                        "access_token": "", "name": "", "birthdate": "", "proxy": ""}

        # 非浏览器模式：curl_cffi 方式（无法触发新邮件，仅用于已有验证码的场景）
        proxy_url = proxy_service.get_next() if self.config.get("use_proxy", False) else None
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._register_sync, email, password, client_id, refresh_token, proxy_url
        )

    async def run_batch(self, emails: list[dict[str, str]], task_id: int) -> dict[str, int]:
        """批量注册"""
        self._running = True
        stats = {"total": len(emails), "completed": 0, "failed": 0, "skipped": 0}

        for i, mail in enumerate(emails):
            if not self._running:
                add_log("info", "批量注册被停止")
                break
            while self._paused:
                await asyncio.sleep(1)

            email = mail["email"]
            add_log("info", f"━━━ [{i+1}/{len(emails)}] 开始注册: {email} ━━━")

            existing = get_accounts(status="success")
            if any(acc["email"] == email for acc in existing):
                add_log("info", f"[{email}] 已注册过，跳过")
                stats["skipped"] += 1
                mark_email_status(email, "used")
                continue

            result = await self.register_one(
                email=email,
                password=mail["password"],
                client_id=mail["client_id"],
                refresh_token=mail["refresh_token"],
            )

            if result["status"] == "success":
                stats["completed"] += 1
                mark_email_status(email, "used")
                if result["access_token"]:
                    self._append_token(result["access_token"])
                insert_account(
                    email=email, password=mail["password"],
                    client_id=mail["client_id"], refresh_token=mail["refresh_token"],
                    openai_refresh_token=result.get("refresh_token", ""), id_token=result.get("id_token", ""),
                    access_token=result["access_token"],
                    name=result["name"], birthdate=result["birthdate"],
                    proxy=result["proxy"], status="success",
                )
            elif result["status"] == "cf_blocked":
                stats["skipped"] += 1
                insert_account(
                    email=email, password=mail["password"],
                    client_id=mail["client_id"], refresh_token=mail["refresh_token"],
                    openai_refresh_token=result.get("refresh_token", ""), id_token=result.get("id_token", ""),
                    proxy=result["proxy"], status="cf_blocked",
                    error="遇到 Cloudflare 人机验证",
                )
            elif result["status"] == "success_no_token":
                stats["completed"] += 1
                mark_email_status(email, "used")
                insert_account(
                    email=email, password=mail["password"],
                    client_id=mail["client_id"], refresh_token=mail["refresh_token"],
                    openai_refresh_token=result.get("refresh_token", ""), id_token=result.get("id_token", ""),
                    name=result["name"], birthdate=result["birthdate"],
                    proxy=result["proxy"], status="success_no_token",
                    error="注册成功但未获取到 token",
                )
            else:
                stats["failed"] += 1
                insert_account(
                    email=email, password=mail["password"],
                    client_id=mail["client_id"], refresh_token=mail["refresh_token"],
                    openai_refresh_token=result.get("refresh_token", ""), id_token=result.get("id_token", ""),
                    proxy=result["proxy"], status="failed",
                    error=result["error"],
                )

            interval = int(self.config.get("register_interval_sec", 10))
            if i < len(emails) - 1 and self._running:
                add_log("info", f"等待 {interval} 秒后继续下一个...")
                await asyncio.sleep(interval)

        self._running = False
        add_log("info", f"━━━ 批量注册完成: 成功 {stats['completed']}, 失败 {stats['failed']}, 跳过 {stats['skipped']} ━━━")
        return stats


register_engine: RegisterEngine | None = None


def get_engine(config: dict[str, Any]) -> RegisterEngine:
    global register_engine
    if register_engine is None:
        register_engine = RegisterEngine(config)
    return register_engine
