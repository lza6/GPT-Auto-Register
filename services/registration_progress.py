from __future__ import annotations

import contextvars
import functools
import inspect
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from services.sanitizer import sanitize, sanitize_text

# 进度 JSONL 文件路径（相对于项目根目录）
PROGRESS_PATH = Path("data/registration_progress.jsonl")

# 当前注册进度上下文（每个注册任务独立，contextvars 确保 asyncio 并发安全）
_current: contextvars.ContextVar["RegistrationProgress | None"] = contextvars.ContextVar(
    "registration_progress",
    default=None,
)
# JSONL 写入锁（线程安全，跨协程共享）
_write_lock = threading.Lock()


class RegistrationProgress:
    """单次注册的进度跟踪对象。

    记录注册流程中每个阶段的事件、状态和耗时，支持持久化到 JSONL 文件。
    """

    def __init__(self, email: str = ""):
        self.run_id = uuid.uuid4().hex
        self.email = str(email or "")
        self.started_at = int(time.time())
        self.events: list[dict[str, Any]] = []
        self.last_stage = "started"
        self.stage("started")

    def stage(self, name: str, status: str = "running", detail: str = "") -> None:
        """记录一个阶段事件。"""
        self.last_stage = str(name or "unknown")
        event: dict[str, Any] = {
            "stage": self.last_stage,
            "status": str(status or "running"),
            "at": int(time.time()),
        }
        if detail:
            event["detail"] = sanitize_text(detail)[:240]
        self.events.append(event)

    def snapshot(self) -> dict[str, Any]:
        """返回当前进度的快照字典（不可变拷贝）。"""
        return {
            "run_id": self.run_id,
            "last_stage": self.last_stage,
            "started_at": self.started_at,
            "events": list(self.events),
        }

    def persist(self, result: dict[str, Any] | None, error: str = "") -> None:
        """将注册进度持久化到 JSONL 文件。

        根据 result 的 status 字段判断成功/失败，写入前经过脱敏处理。
        """
        success = bool((result or {}).get("status") == "success")
        final_error = sanitize_text(error or (result or {}).get("error") or "")[:300]
        self.stage("completed" if success else "failed", "success" if success else "failed", final_error)
        row = sanitize({
            "run_id": self.run_id,
            "email": self.email or str((result or {}).get("email") or ""),
            "success": success,
            "error": final_error,
            "started_at": self.started_at,
            "finished_at": int(time.time()),
            "last_stage": self.last_stage,
            "events": self.events,
        })
        with _write_lock:
            PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
            with PROGRESS_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n")


def registration_stage(name: str, status: str = "running", detail: str = "") -> None:
    """在当前注册流程中记录一个阶段事件。

    必须在 @track_registration 装饰的函数内部调用，否则为无操作。
    """
    progress = _current.get()
    if progress is None:
        return
    progress.stage(name, status, detail)


def track_registration(func: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """装饰器：包装注册函数，自动创建/持久化注册进度。

    兼容同步和异步函数。装饰后：
    - 函数执行前创建 RegistrationProgress 并注入上下文
    - 函数返回/异常时持久化进度到 JSONL
    - 在结果 dict 中注入 ``registration_progress`` 快照

    支持链式调用：``registration_stage()`` 在装饰函数内自动关联当前进度。
    """
    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            email = kwargs.get("email") or ""
            progress = RegistrationProgress(email)
            token = _current.set(progress)
            result: dict[str, Any] | None = None
            error = ""
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as exc:
                error = str(exc)
                raise
            finally:
                try:
                    progress.persist(result, error)
                    if isinstance(result, dict):
                        result["registration_progress"] = progress.snapshot()
                finally:
                    _current.reset(token)

        return async_wrapper

    @functools.wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        email = kwargs.get("email") or ""
        progress = RegistrationProgress(email)
        token = _current.set(progress)
        result: dict[str, Any] | None = None
        error = ""
        try:
            result = func(*args, **kwargs)
            return result
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            try:
                progress.persist(result, error)
                if isinstance(result, dict):
                    result["registration_progress"] = progress.snapshot()
            finally:
                _current.reset(token)

    return sync_wrapper