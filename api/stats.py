from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter

from services.db import get_stats, count_accounts, count_emails, db_session
from services.sanitizer import sanitize

router = APIRouter()


@router.get("/", summary="统计数据", description="注册统计概览：成功/失败/处理中数量等。")
async def stats() -> dict:
    return sanitize(get_stats())


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


@router.get("/account-readiness")
async def account_readiness() -> dict:
    """v3.4 T94：账号资产健康度聚合——c2api 就绪率（AT/RT/密码/取件凭证 四维）。

    前端用来渲染"就绪率卡片"和"仅健康导出"选项。
    """
    with db_session() as conn:
        row = conn.execute(
            """SELECT
                COUNT(*) AS total_success,
                SUM(CASE WHEN access_token IS NOT NULL AND access_token != '' AND access_token LIKE 'eyJ%' THEN 1 ELSE 0 END) AS at_ok,
                SUM(CASE WHEN openai_refresh_token IS NOT NULL AND openai_refresh_token != '' THEN 1 ELSE 0 END) AS rt_ok,
                SUM(CASE WHEN openai_password IS NOT NULL AND openai_password != '' OR password IS NOT NULL AND password != '' THEN 1 ELSE 0 END) AS pw_ok,
                SUM(CASE WHEN client_id IS NOT NULL AND client_id != '' AND refresh_token IS NOT NULL AND refresh_token != '' THEN 1 ELSE 0 END) AS mail_ok
               FROM accounts WHERE status = 'success'"""
        ).fetchone()
    r = dict(row) if row else {}
    r["fully_ready"] = min(r.get("at_ok", 0), r.get("rt_ok", 0), r.get("pw_ok", 0), r.get("mail_ok", 0))
    return r
