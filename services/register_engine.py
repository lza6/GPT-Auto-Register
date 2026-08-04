from __future__ import annotations

import asyncio
import json
from typing import Any

from services.db import add_log, insert_account, mark_email_status, get_accounts, update_task_progress


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

    async def register_one(self, email: str, password: str, client_id: str,
                           refresh_token: str) -> dict[str, Any]:
        """注册单个账号 — 浏览器方式。

        OpenAI 需要真实浏览器执行 sentinel 风控参数，纯协议（curl_cffi）注册已被官方拒绝，
        因此不再提供协议降级，避免静默失败。
        """
        use_browser = self.config.get("use_browser", True)
        if not use_browser:
            add_log("error", f"[{email}] use_browser=false 已不再支持：纯协议注册被 OpenAI 拒绝，请使用浏览器模式")
            return {"email": email, "status": "failed", "error": "纯协议注册已不可用，请使用浏览器模式",
                    "access_token": "", "name": "", "birthdate": "", "proxy": ""}
        try:
            from services.browser_register import get_browser_register
            browser_reg = get_browser_register(self.config)
            return await browser_reg.register_one(email, password, client_id, refresh_token)
        except Exception as e:
            add_log("error", f"[{email}] 浏览器注册异常: {e}")
            return {"email": email, "status": "failed", "error": str(e),
                    "access_token": "", "name": "", "birthdate": "", "proxy": ""}

    async def run_batch(self, emails: list[dict[str, str]], task_id: int) -> dict[str, int]:
        """批量注册：实时更新任务进度，支持断点续跑（emails 按 pending 状态驱动）。"""
        self._running = True
        stats = {"total": len(emails), "completed": 0, "failed": 0, "skipped": 0}
        # 预加载成功账号集合，只查一次，避免每账号全表扫描（O(N²)）
        success_emails = {a["email"] for a in get_accounts(status="success")}
        interrupted = False

        for i, mail in enumerate(emails):
            if not self._running:
                interrupted = True
                add_log("info", "批量注册被停止")
                break
            while self._paused:
                await asyncio.sleep(1)

            email = mail["email"]
            add_log("info", f"━━━ [{i+1}/{len(emails)}] 开始注册: {email} ━━━")

            if email in success_emails:
                add_log("info", f"[{email}] 已注册过，跳过")
                stats["skipped"] += 1
                mark_email_status(email, "used")
                update_task_progress(task_id, stats["completed"], stats["failed"], stats["skipped"])
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
                    openai_password=result.get("openai_password", ""),
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
                    openai_password=result.get("openai_password", ""),
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
                    openai_password=result.get("openai_password", ""),
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
                    openai_password=result.get("openai_password", ""),
                    proxy=result["proxy"], status="failed",
                    error=result["error"],
                )

            update_task_progress(task_id, stats["completed"], stats["failed"], stats["skipped"])

            interval = int(self.config.get("register_interval_sec", 10))
            if i < len(emails) - 1 and self._running:
                add_log("info", f"等待 {interval} 秒后继续下一个...")
                await asyncio.sleep(interval)

        self._running = False
        task_status = "stopped" if interrupted else "completed"
        update_task_progress(
            task_id, stats["completed"], stats["failed"], stats["skipped"],
            status=task_status, result=json.dumps(stats, ensure_ascii=False),
        )
        add_log("info", f"━━━ 批量注册完成: 成功 {stats['completed']}, 失败 {stats['failed']}, 跳过 {stats['skipped']} ━━━")
        return stats


register_engine: RegisterEngine | None = None


def get_engine(config: dict[str, Any]) -> RegisterEngine:
    global register_engine
    if register_engine is None:
        register_engine = RegisterEngine(config)
    return register_engine
