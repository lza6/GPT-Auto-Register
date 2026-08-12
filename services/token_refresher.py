"""token 保鲜巡检（G1：定期用 refresh_token 换新 access_token）。

背景：access_token（OpenAI JWT）会过期；refresh_token 可长期续期。
本项目注册成功已拿三件套，但 access_token 存库后不刷新，
chatgpt2api 导入前若 token 已过期需手动刷新。本模块定期巡检刷新。

设计：
- 后台 asyncio 任务，每 token_refresh_interval_sec（默认 21600=6h）扫描。
- 用 refresh_token 调 OpenAI oauth/token 换新 access_token，更新 db。
- 并发限 5，避免 OpenAI 限流。
- config.token_refresh_enabled 默认 false（避免与 chatgpt2api 自动刷新冲突）。
- 失败仅记 log + 跳过，不删旧 token（保留可用旧值）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from services.constants import OAUTH_CLIENT_ID, OAUTH_TOKEN_URL, OAUTH_REDIRECT_URI, tls_verify_enabled
from services.db import add_log, db_session


class TokenRefresher:
    """后台 token 保鲜巡检任务。"""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        interval = config.get("token_refresh_interval_sec", 21600)
        # 兼容字符串
        try:
            self._interval = max(60, int(interval))
        except (TypeError, ValueError):
            self._interval = 21600
        self._concurrency = 5
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_scan_at: float = 0
        self._last_result: dict[str, int] = {"scanned": 0, "refreshed": 0, "failed": 0,
                                             "active": 0, "unknown": 0, "deactivated": 0}
        self._scanning = False  # v3.4 T84：防重入哨兵（扫描慢于 interval 时不叠加）
        self._consecutive_errors = 0  # v3.4 T84：连续异常计数，驱动退避重启

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_scan_at(self) -> float:
        return self._last_scan_at

    @property
    def last_result(self) -> dict[str, int]:
        return dict(self._last_result)

    def start(self) -> None:
        """启动后台巡检任务（幂等，重复调用安全）。"""
        if self._task is not None and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        # v3.4 T84：崩溃自愈——任务异常退出（非主动 stop）时自动重启（带退避，防异常风暴刷日志）
        self._task.add_done_callback(self._on_task_done)
        add_log("info", f"token 巡检任务已启动，间隔 {self._interval}s")

    def _on_task_done(self, task: asyncio.Task) -> None:
        """巡检任务结束回调：主动 stop 则静默；异常退出且仍 _running 则退避后自动重启。"""
        if not self._running:
            return  # 主动 stop，不重启
        exc = task.exception() if not task.cancelled() else None
        if exc is None:
            return  # 正常结束（理论上 _run_loop 不会正常退出，除非 _running=False）
        self._consecutive_errors += 1
        backoff = min(300, 5 * (2 ** min(self._consecutive_errors - 1, 6)))  # 5s→10s→…→300s 封顶
        add_log("error", f"token 巡检任务异常退出（{type(exc).__name__}: {exc}），{backoff}s 后自动重启（第 {self._consecutive_errors} 次）")
        try:
            loop = asyncio.get_running_loop()
            loop.call_later(backoff, self._restart)
        except RuntimeError:
            pass  # 事件循环已关闭

    def _restart(self) -> None:
        """重启巡检任务（由 _on_task_done 退避后触发）。"""
        if not self._running:
            return
        self._task = None
        self.start()

    async def stop(self) -> None:
        """停止巡检任务。"""
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        add_log("info", "token 巡检任务已停止")

    async def _run_loop(self) -> None:
        """主循环：周期扫描，异常不退出（v3.4：防重入 + 退避，崩溃由 _on_task_done 兜底重启）。"""
        # 启动后等 60s 再首次扫描，避免与启动并发
        await asyncio.sleep(60)
        while self._running:
            started_at = time.time()
            try:
                await self._scan_once()
                self._consecutive_errors = 0  # 成功一轮，清零退避计数
            except asyncio.CancelledError:
                raise  # 主动 stop，上抛退出循环
            except Exception as e:
                add_log("error", f"token 巡检异常: {e}")
            # 防重入：本轮耗时从 interval 里扣除，扫描慢时自然顺延而非叠加
            elapsed = time.time() - started_at
            wait = max(5, self._interval - elapsed)
            # 分段 sleep，便于 stop 及时响应
            slept = 0.0
            while self._running and slept < wait:
                step = min(5.0, wait - slept)
                await asyncio.sleep(step)
                slept += step

    async def _scan_once(self) -> dict[str, int]:
        """扫描所有 success 账号，刷新过期 token（v3.4：防重入 + 局部统计消除并发读写共享 dict）。"""
        if self._scanning:
            add_log("warning", "token 巡检上一轮未结束，跳过本轮（防重入）")
            return self._last_result
        self._scanning = True
        try:
            # 取所有 success 账号（v4.0 P1-4：探活优先，active 跳过刷新省配额）
            with db_session() as conn:
                rows = conn.execute(
                    "SELECT email, openai_refresh_token, access_token, id_token FROM accounts "
                    "WHERE status = 'success'"
                ).fetchall()
            accounts = [dict(r) for r in rows]
            self._last_scan_at = time.time()
            # v3.4：用局部 dict 统计，gather 结束一次性赋值，消除并发 += 共享 _last_result 的竞态
            # v4.0 P1-4：active=健康跳过；unknown=403/429/传输失败不误判；deactivated=永久停用
            result = {"scanned": len(accounts), "refreshed": 0, "failed": 0,
                      "active": 0, "unknown": 0, "deactivated": 0}

            if not accounts:
                add_log("info", "token 巡检：无 success 账号可扫描")
                self._last_result = result
                return result

            sem = asyncio.Semaphore(self._concurrency)

            async def probe_one(acc: dict) -> None:
                async with sem:
                    try:
                        proxy_url = self._resolve_proxy()  # 一次解析，探活+恢复共用同出口
                        liveness = await self._probe(acc, proxy_url)
                        status = liveness.get("status")
                        email = acc["email"]
                        if status == "active":
                            result["active"] += 1
                            return
                        if status == "token_invalid":
                            # 永久停用账号终态化：不再浪费重试（仅统计+日志，保留现场）
                            low = (liveness.get("error") or "").lower()
                            if any(k in low for k in ("account_deactivated", "deleted or deactivated")):
                                result["deactivated"] += 1
                                add_log("warning", f"token 巡检 [{email}] 账号已停用（终态），不再重试")
                                return
                            if await self._recover_via_rt(acc, proxy_url):
                                result["refreshed"] += 1
                            else:
                                result["failed"] += 1
                            return
                        # unknown：403/429/传输失败，不误判 AT 失效，留待复查
                        result["unknown"] += 1
                        if liveness.get("error"):
                            add_log("debug", f"token 巡检 [{email}] 探活 unknown: {liveness['error'][:120]}")
                    except Exception as e:
                        add_log("warning", f"token 巡检 [{acc['email']}] 异常: {e}")
                        result["unknown"] += 1

            await asyncio.gather(*(probe_one(a) for a in accounts))
            self._last_result = result
            add_log("info",
                    f"token 巡检完成：扫描 {result['scanned']}，健康 {result['active']}，"
                    f"刷新 {result['refreshed']}，失败 {result['failed']}，"
                    f"未知 {result['unknown']}，停用 {result['deactivated']}")
            return result
        finally:
            self._scanning = False

    def _resolve_proxy(self) -> str | None:
        """解析代理：优先 config.proxy_url，其次代理池（use_proxy 时）。与注册链路一致。

        v3.1 T7：注册链路走代理，巡检若不走则在「OpenAI 仅能经代理可达」的部署里永远失败。
        """
        try:
            proxy_url = self._config.get("proxy_url")
            if proxy_url:
                return proxy_url
            use_proxy = self._config.get("use_proxy")
            if use_proxy is True or (isinstance(use_proxy, str) and use_proxy.strip().lower() in ("1", "true", "yes", "on")):
                from services.proxy_service import proxy_service
                return proxy_service.get_next()
        except Exception:
            pass
        return None

    async def _probe(self, acc: dict, proxy_url: str | None = None) -> dict:
        """探活账号 AT（三态：active/token_invalid/unknown）。"""
        from services.account_liveness import probe_access_token
        return await probe_access_token(
            acc.get("access_token", ""),
            id_token=acc.get("id_token", ""),
            proxy=proxy_url,
            verify=tls_verify_enabled(self._config),
        )

    async def _recover_via_rt(self, acc: dict, proxy_url: str | None = None) -> bool:
        """AT 失效恢复链（第一级）：refresh_token 换新 AT → 二次探活确认 active → 落盘。

        换到的新 AT 必须先二次探活返回 active 才写库，防止坏 token 覆盖好 token
        （GPT-Register-Tool account_recovery 的 _verify_and_persist_candidate 同口径）。
        """
        rt = acc.get("openai_refresh_token") or ""
        if not rt:
            add_log("warning", f"token 巡检 [{acc['email']}] AT 失效但无 refresh_token，无法恢复")
            return False
        tokens = await self._refresh_token(rt)
        if not tokens or not tokens.get("access_token"):
            add_log("warning", f"token 巡检 [{acc['email']}] RT 恢复失败（刷新未返回 AT）")
            return False
        from services.account_liveness import probe_access_token
        confirm = await probe_access_token(
            tokens["access_token"],
            id_token=tokens.get("id_token", ""),
            proxy=proxy_url,
            verify=tls_verify_enabled(self._config),
        )
        if confirm.get("status") != "active":
            add_log("warning",
                    f"token 巡检 [{acc['email']}] 恢复后探活未确认 active"
                    f"（{confirm.get('status')}），不落盘")
            return False
        # 同时落库新 access_token 与轮换后的新 refresh_token（否则库存 RT 会逐步耗尽）
        self._update_tokens(acc["email"], tokens["access_token"], tokens.get("refresh_token", ""))
        return True

    async def _refresh_token(self, refresh_token: str) -> dict | None:
        """用 refresh_token 换新三件套（可被测试 monkeypatch）。走配置代理。

        返回 {access_token, refresh_token, id_token}；refresh_token 可能为空（未轮换时）。失败返回 None。
        """
        proxy_url = self._resolve_proxy()
        try:
            proxy_kwargs: dict[str, Any] = {}
            if proxy_url:
                proxy_kwargs["proxy"] = proxy_url
            async with httpx.AsyncClient(timeout=30, verify=tls_verify_enabled(self._config), **proxy_kwargs) as client:
                resp = await client.post(
                    OAUTH_TOKEN_URL,
                    headers={
                        "accept": "application/json",
                        "content-type": "application/json",
                        "origin": "https://platform.openai.com",
                        "referer": "https://platform.openai.com/",
                    },
                    json={
                        "client_id": OAUTH_CLIENT_ID,
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "redirect_uri": OAUTH_REDIRECT_URI,
                    },
                )
                data = resp.json() if resp.text else {}
                if resp.status_code == 200 and data.get("access_token"):
                    # 必须带回新 refresh_token：OpenAI 会轮换 RT 且旧的重用数次后作废
                    # （实测 refresh_token_reused），不落库新 RT 会导致库存 RT 逐步失效。
                    return {
                        "access_token": data["access_token"],
                        "refresh_token": data.get("refresh_token", ""),
                        "id_token": data.get("id_token", ""),
                    }
                add_log("warning", f"token 刷新失败: HTTP {resp.status_code}")
                return None
        except Exception as e:
            add_log("warning", f"token 刷新异常: {e}")
            return None

    def _update_tokens(self, email: str, access_token: str, refresh_token: str = "") -> None:
        """更新账号 access_token + 轮换后的新 refresh_token（关键：RT 轮换不落库会耗尽库存 RT）。

        仅当返回了新 refresh_token 时才覆盖旧值（空则保留旧 RT，防误清）。
        """
        with db_session() as conn:
            if refresh_token:
                conn.execute(
                    "UPDATE accounts SET access_token = ?, openai_refresh_token = ? WHERE email = ?",
                    (access_token, refresh_token, email),
                )
            else:
                conn.execute(
                    "UPDATE accounts SET access_token = ? WHERE email = ?",
                    (access_token, email),
                )


token_refresher: TokenRefresher | None = None


def get_token_refresher(config: dict[str, Any]) -> TokenRefresher:
    """获取全局 token 巡检单例。"""
    global token_refresher
    if token_refresher is None:
        token_refresher = TokenRefresher(config)
    return token_refresher
