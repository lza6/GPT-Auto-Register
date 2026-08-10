from __future__ import annotations

import time

from fastapi import APIRouter

from services.db import get_stats, count_accounts, count_emails

router = APIRouter()


@router.get("/", summary="统计数据", description="注册统计概览：成功/失败/处理中数量等。")
async def stats() -> dict:
    return get_stats()


@router.get("/token-health", summary="Token 健康度", description="token 保鲜巡检健康度：最近一次巡检时间、扫描/刷新/失败数、巡检是否启用。")
async def token_health() -> dict:
    """token 保鲜巡检健康度（v3.0 G1）。

    返回最近一次巡检时间、扫描/刷新/失败数、巡检是否启用。
    供前端设置页展示 token 健康度仪表盘。
    """
    try:
        from services.token_refresher import token_refresher
        if token_refresher is not None:
            return {
                "enabled": token_refresher.is_running,
                "last_scan_at": token_refresher.last_scan_at,
                "last_scan_at_human": time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(token_refresher.last_scan_at)
                ) if token_refresher.last_scan_at else "",
                "result": token_refresher.last_result,
            }
    except Exception:
        pass
    return {"enabled": False, "last_scan_at": 0, "last_scan_at_human": "", "result": {}}
