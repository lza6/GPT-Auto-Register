from __future__ import annotations

import asyncio
import json
from typing import Any

from services.db import add_log, insert_account, mark_email_status, get_accounts, update_task_progress


def _as_bool(value, default: bool = True) -> bool:
    """解析配置布尔值。settings API 把开关存为字符串（'true'/'false'），需兼容。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)  # 0/1 数值开关
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default


def _as_int(value, default: int) -> int:
    """解析配置整数。settings API 存字符串，非法/空值回退默认（防 int('true') 崩溃）。"""
    if value is None or value == "":
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


class RegisterEngine:
    """ChatGPT 自动注册引擎 — curl_cffi Firefox 指纹 + CF solver 兜底"""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.ua = config.get(
            "user_agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
        )
        self.otp_timeout = _as_int(config.get("otp_wait_timeout_sec"), 120)
        self.otp_poll = _as_int(config.get("otp_poll_interval_sec"), 5)
        self.token_file = config.get("token_output_file", "已经获取到的token.txt")
        self._running = False
        self._paused = False
        self._starting = False  # 防并发 /start 竞态：后台任务置位前先占位
        self._stop_requested = False  # stop 独立标志，不被 run_batch 开头覆盖
        # 暂停/恢复用 asyncio.Event（避免 while+sleep 空转），stop 时 set 唤醒等待槽位
        self._pause_event = asyncio.Event()
        self._pause_event.set()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused

    def try_start(self) -> bool:
        """原子尝试占位启动，防并发 /register/start 竞态（check-then-set）。

        后台任务（run_batch）尚未把 _running 置位时，_starting 挡住第二个启动请求。
        """
        if self._running or self._starting:
            return False
        self._starting = True
        return True

    def abort_start(self) -> None:
        """启动占位后因无待处理邮箱等提前中止时复位标记。"""
        self._starting = False

    def pause(self) -> None:
        self._paused = True
        self._pause_event.clear()
        add_log("info", "注册任务已暂停")

    def resume(self) -> None:
        self._paused = False
        self._pause_event.set()
        add_log("info", "注册任务已继续")

    def stop(self) -> None:
        self._running = False
        self._paused = False
        self._stop_requested = True  # 独立停止标志，避免被 run_batch 开头的 _running=True 覆盖
        self._pause_event.set()  # 唤醒等待中的槽位，让它们检查 running 后退出
        add_log("info", "注册任务已停止")

    def _append_token(self, token: str) -> None:
        try:
            with open(self.token_file, "a", encoding="utf-8") as f:
                f.write(token.strip() + "\n")
        except Exception as e:
            add_log("error", f"写入 token 文件失败: {e}")

    async def register_one(self, email: str, password: str, client_id: str,
                           refresh_token: str) -> dict[str, Any]:
        """注册单个账号 — 协议优先，浏览器兜底。

        主路径：curl_cffi + sentinel 纯协议（services.protocol_register）。
        协议失败且标记 ``fallback_browser``（网络/服务器临时错误）→ 降级浏览器；
        风控/限流类失败不降级（浏览器同样会被拒，避免无谓重试）。
        """
        use_browser = _as_bool(self.config.get("use_browser"), True)
        protocol_first = _as_bool(self.config.get("protocol_first"), True)

        if protocol_first:
            from services.protocol_register import get_protocol_register
            protocol_reg = get_protocol_register(self.config)
            result = await protocol_reg.register_one(email, password, client_id, refresh_token)
            if result["status"] == "success":
                return result
            if use_browser and result.get("fallback_browser"):
                add_log("warning", f"[{email}] 协议注册失败（{result['error']}），降级浏览器兜底")
                return await self._register_with_browser(email, password, client_id, refresh_token)
            return result

        # 非协议优先模式：直接浏览器
        return await self._register_with_browser(email, password, client_id, refresh_token)

    async def _register_with_browser(self, email: str, password: str, client_id: str,
                                     refresh_token: str) -> dict[str, Any]:
        try:
            from services.browser_register import get_browser_register
            browser_reg = get_browser_register(self.config)
            return await browser_reg.register_one(email, password, client_id, refresh_token)
        except Exception as e:
            add_log("error", f"[{email}] 浏览器注册异常: {e}")
            return {"email": email, "status": "failed", "error": str(e),
                    "access_token": "", "name": "", "birthdate": "", "proxy": ""}

    async def run_batch(self, emails: list[dict[str, str]], task_id: int) -> dict[str, Any]:
        """批量注册（并发）：并发度 = config.register_concurrency。

        - 每账号独立注册（幂等），用 Semaphore 限流，asyncio.gather 并发执行。
        - 暂停用 asyncio.Event 等待，停止时唤醒所有槽位并检查 running 退出。
        - stats 累加与进度落库用 asyncio.Lock 保护。
        - 断点续跑：emails 按 pending 状态驱动，已成功的账号跳过。
        - 失败原因按 failure_type 分类统计（risk_control/otp_timeout/network/server_5xx/unknown）。
        """
        self._running = True
        self._paused = False
        self._starting = False
        was_stopped = self._stop_requested
        self._stop_requested = False
        if was_stopped:
            # 在 try_start 占位到 run_batch 真正开始之间收到了 stop：不覆盖停止请求，直接结束
            self._running = False
            stats = {"total": len(emails), "completed": 0, "failed": 0, "skipped": 0, "failure_types": {}}
            update_task_progress(
                task_id, 0, 0, 0, status="stopped",
                result=json.dumps(stats, ensure_ascii=False),
            )
            add_log("info", "批量注册在启动前已被停止")
            return stats
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        concurrency = max(1, _as_int(self.config.get("register_concurrency"), 1))
        stats: dict[str, Any] = {
            "total": len(emails), "completed": 0, "failed": 0, "skipped": 0,
            "failure_types": {},
        }
        # 预加载成功账号集合，只查一次，避免每账号全表扫描（O(N²)）
        success_emails = {a["email"] for a in get_accounts(status="success")}
        stats_lock = asyncio.Lock()
        sem = asyncio.Semaphore(concurrency)
        stopped = {"flag": False}

        async def process_one(i: int, mail: dict[str, str]) -> None:
            email = mail["email"]
            async with sem:
                # 暂停等待 + 停止检查（放在 sem 内：已排队但未开始的槽位也会被 pause/stop 拦下）
                await self._pause_event.wait()
                if not self._running:
                    stopped["flag"] = True
                    return

                add_log("info", f"━━━ [{i+1}/{len(emails)}] 开始注册: {email} ━━━")

                if email in success_emails:
                    add_log("info", f"[{email}] 已注册过，跳过")
                    async with stats_lock:
                        stats["skipped"] += 1
                        mark_email_status(email, "used")
                        update_task_progress(task_id, stats["completed"], stats["failed"], stats["skipped"])
                    return

                try:
                    result = await self.register_one(
                        email=email,
                        password=mail["password"],
                        client_id=mail["client_id"],
                        refresh_token=mail["refresh_token"],
                    )
                except Exception as exc:
                    # register_one 抛异常（非 dict 返回）也计入失败并落库，避免邮箱静默丢失
                    add_log("error", f"[{email}] 注册异常: {exc}")
                    async with stats_lock:
                        stats["failed"] += 1
                        stats["failure_types"]["unknown"] = stats["failure_types"].get("unknown", 0) + 1
                        insert_account(
                            email=email, password=mail["password"],
                            client_id=mail["client_id"], refresh_token=mail["refresh_token"],
                            proxy=self.config.get("proxy_url") or "直连",
                            status="failed", error=f"注册异常: {exc}",
                        )
                        update_task_progress(task_id, stats["completed"], stats["failed"], stats["skipped"])
                    return

                async with stats_lock:
                    if result["status"] == "success":
                        # 先落库成功再标 used：insert 抛错则邮箱保持 pending，下轮可重试
                        insert_account(
                            email=email, password=mail["password"],
                            client_id=mail["client_id"], refresh_token=mail["refresh_token"],
                            openai_refresh_token=result.get("refresh_token", ""), id_token=result.get("id_token", ""),
                            openai_password=result.get("openai_password", ""),
                            access_token=result["access_token"],
                            name=result["name"], birthdate=result["birthdate"],
                            proxy=result["proxy"], status="success",
                        )
                        stats["completed"] += 1
                        mark_email_status(email, "used")
                        if result["access_token"]:
                            self._append_token(result["access_token"])
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
                        insert_account(
                            email=email, password=mail["password"],
                            client_id=mail["client_id"], refresh_token=mail["refresh_token"],
                            openai_refresh_token=result.get("refresh_token", ""), id_token=result.get("id_token", ""),
                            openai_password=result.get("openai_password", ""),
                            name=result["name"], birthdate=result["birthdate"],
                            proxy=result["proxy"], status="success_no_token",
                            error="注册成功但未获取到 token",
                        )
                        stats["completed"] += 1
                        mark_email_status(email, "used")
                    else:
                        stats["failed"] += 1
                        ftype = result.get("failure_type") or "unknown"
                        stats["failure_types"][ftype] = stats["failure_types"].get(ftype, 0) + 1
                        insert_account(
                            email=email, password=mail["password"],
                            client_id=mail["client_id"], refresh_token=mail["refresh_token"],
                            openai_refresh_token=result.get("refresh_token", ""), id_token=result.get("id_token", ""),
                            openai_password=result.get("openai_password", ""),
                            proxy=result["proxy"], status="failed",
                            error=result["error"],
                        )
                    update_task_progress(task_id, stats["completed"], stats["failed"], stats["skipped"])

            interval = _as_int(self.config.get("register_interval_sec"), 10)
            if interval > 0 and self._running:
                await asyncio.sleep(interval)

        results = await asyncio.gather(
            *(process_one(i, mail) for i, mail in enumerate(emails)),
            return_exceptions=True,
        )
        for exc in results:
            if isinstance(exc, Exception):
                add_log("error", f"并发注册槽异常: {exc}")

        self._running = False
        task_status = "stopped" if stopped["flag"] else "completed"
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
