"""Prometheus 指标定义与导出。

指标前缀: gpt_register_
"""
from __future__ import annotations

import time
from pathlib import Path

from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
from starlette.requests import Request

# === 注册计数 ===
register_total = Counter(
    "gpt_register_register_total",
    "注册请求总数",
    labelnames=["status"],
)
register_duration = Histogram(
    "gpt_register_register_duration_seconds",
    "注册耗时（秒）",
    buckets=[10, 30, 60, 120, 300, 600],
)

# === 邮箱库存 ===
emails_total = Gauge("gpt_register_emails_total", "邮箱总数")
emails_pending = Gauge("gpt_register_emails_pending", "待注册邮箱数")
accounts_success = Gauge("gpt_register_accounts_success", "注册成功账号数")
accounts_failed = Gauge("gpt_register_accounts_failed", "注册失败账号数")

# === 引擎状态 ===
engine_running = Gauge("gpt_register_engine_running", "引擎是否运行中（1/0）")
engine_queue_depth = Gauge("gpt_register_engine_queue_depth", "注册队列深度")
engine_concurrency = Gauge("gpt_register_engine_concurrency", "当前并发注册数")
proxy_pool_size = Gauge("gpt_register_proxy_pool_size", "代理池可用数量")

# === 系统 ===
db_file_size_bytes = Gauge("gpt_register_db_file_size_bytes", "数据库文件大小（字节）")
last_vacuum_timestamp = Gauge("gpt_register_last_vacuum_timestamp", "上次 VACUUM 时间戳")


async def metrics_endpoint(request: Request) -> Response:
    """返回 Prometheus 格式指标。"""
    # 刷新动态指标
    _refresh_db_gauges()
    _refresh_system_gauges()
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _refresh_db_gauges() -> None:
    """从数据库刷新邮箱库相关 gauge。"""
    try:
        from services.db import get_stats
        stats = get_stats()
        emails_total.set(stats.get("emails_total", 0))
        emails_pending.set(stats.get("emails_pending", 0))
        accounts_success.set(stats.get("accounts_success", 0))
        accounts_failed.set(stats.get("accounts_failed", 0))
    except Exception:
        pass


def _refresh_system_gauges() -> None:
    """刷新系统相关 gauge。"""
    try:
        from services.db import DB_PATH
        db_path = Path(str(DB_PATH))
        if db_path.exists():
            db_file_size_bytes.set(db_path.stat().st_size)
    except Exception:
        pass
    # 刷新引擎状态
    try:
        from services.register_engine import register_engine
        if register_engine is not None:
            engine_running.set(1 if register_engine.is_running else 0)
            # 队列深度和并发度暂为 0（无直接接口），占位预留
    except Exception:
        pass
    # 刷新代理池大小
    try:
        from services.proxy_service import proxy_service
        if proxy_service is not None:
            pool = getattr(proxy_service, "_pool", None) or getattr(proxy_service, "proxies", None) or []
            proxy_pool_size.set(len(pool) if isinstance(pool, (list, set)) else 0)
    except Exception:
        pass