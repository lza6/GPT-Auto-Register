from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from services.db import add_log

CF_SOLVER_DIR = Path(__file__).resolve().parent.parent / "cf_solver"
CF_SOLVER_PORT = 8001
CF_SOLVER_URL = f"http://127.0.0.1:{CF_SOLVER_PORT}"


class CFSolverService:
    """CF 人机验证自动解决服务 — 封装 Boterdrop-Solver"""

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def ensure_running(self) -> bool:
        """确保 CF solver 服务在运行"""
        if await self._check_health():
            self._running = True
            return True
        return await self.start()

    async def start(self) -> bool:
        """启动 CF solver 服务"""
        if self._process and self._process.poll() is None:
            add_log("info", "CF solver 已在运行")
            return True

        add_log("info", "启动 CF solver 服务...")
        wrapper = CF_SOLVER_DIR / "boterdrop_wrapper.py"
        if not wrapper.exists():
            add_log("error", f"CF solver wrapper 不存在: {wrapper}")
            return False

        try:
            self._process = subprocess.Popen(
                [sys.executable, str(wrapper)],
                cwd=str(CF_SOLVER_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            # 等待服务就绪
            for i in range(30):
                await asyncio.sleep(2)
                if await self._check_health():
                    self._running = True
                    add_log("info", f"CF solver 启动成功 (等待 {i*2+2} 秒)")
                    return True
            add_log("error", "CF solver 启动超时")
            return False
        except Exception as e:
            add_log("error", f"CF solver 启动失败: {e}")
            return False

    async def stop(self) -> None:
        """停止 CF solver 服务"""
        if self._process and self._process.poll() is None:
            self._process.terminate()
            self._process.wait(timeout=10)
            self._running = False
            add_log("info", "CF solver 已停止")

    async def _check_health(self) -> bool:
        """检查 CF solver 健康状态"""
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(f"{CF_SOLVER_URL}/")
                return resp.status_code in (200, 404)
        except Exception:
            return False

    async def get_cf_clearance(self, url: str = "https://chatgpt.com/", timeout: int = 60) -> dict[str, Any]:
        """获取 cf_clearance cookie"""
        if not await self._check_health():
            add_log("warning", "CF solver 未运行，尝试启动...")
            if not await self.start():
                return {"status": "error", "error": "CF solver 未运行"}

        try:
            async with httpx.AsyncClient(timeout=timeout + 30) as client:
                # 提交 clearance 任务
                resp = await client.get(
                    f"{CF_SOLVER_URL}/clearance",
                    params={"url": url, "timeout": timeout},
                )
                if resp.status_code != 202:
                    return {"status": "error", "error": f"提交失败: HTTP {resp.status_code}"}
                task_data = resp.json()
                task_id = task_data.get("task_id")
                if not task_id:
                    return {"status": "error", "error": "未获取到 task_id"}

                # 轮询结果
                deadline = time.time() + timeout + 30
                while time.time() < deadline:
                    await asyncio.sleep(2)
                    result_resp = await client.get(
                        f"{CF_SOLVER_URL}/result",
                        params={"id": task_id},
                    )
                    if result_resp.status_code == 202:
                        continue  # 还在处理中
                    if result_resp.status_code == 200:
                        result = result_resp.json()
                        add_log("info", f"CF clearance 获取成功: {result.get('elapsed_time', '?')}s")
                        return result
                    if result_resp.status_code in (408, 422):
                        result = result_resp.json()
                        add_log("error", f"CF clearance 获取失败: {result.get('message', result.get('value', 'unknown'))}")
                        return result

                return {"status": "error", "error": "CF clearance 获取超时"}
        except Exception as e:
            add_log("error", f"CF clearance 获取异常: {e}")
            return {"status": "error", "error": str(e)}

    async def get_turnstile_token(self, url: str, sitekey: str, timeout: int = 120) -> dict[str, Any]:
        """获取 Turnstile token"""
        if not await self._check_health():
            add_log("warning", "CF solver 未运行，尝试启动...")
            if not await self.start():
                return {"status": "error", "error": "CF solver 未运行"}

        try:
            async with httpx.AsyncClient(timeout=timeout + 30) as client:
                resp = await client.get(
                    f"{CF_SOLVER_URL}/turnstile",
                    params={"url": url, "sitekey": sitekey},
                )
                if resp.status_code != 202:
                    return {"status": "error", "error": f"提交失败: HTTP {resp.status_code}"}
                task_data = resp.json()
                task_id = task_data.get("task_id")
                if not task_id:
                    return {"status": "error", "error": "未获取到 task_id"}

                deadline = time.time() + timeout + 30
                while time.time() < deadline:
                    await asyncio.sleep(2)
                    result_resp = await client.get(
                        f"{CF_SOLVER_URL}/result",
                        params={"id": task_id},
                    )
                    if result_resp.status_code == 202:
                        continue
                    if result_resp.status_code == 200:
                        result = result_resp.json()
                        add_log("info", f"Turnstile token 获取成功: {result.get('elapsed_time', '?')}s")
                        return result
                    if result_resp.status_code in (408, 422):
                        result = result_resp.json()
                        add_log("error", f"Turnstile 解题失败: {result.get('message', result.get('value', 'unknown'))}")
                        return result

                return {"status": "error", "error": "Turnstile 解题超时"}
        except Exception as e:
            add_log("error", f"Turnstile 解题异常: {e}")
            return {"status": "error", "error": str(e)}


cf_solver_service = CFSolverService()
