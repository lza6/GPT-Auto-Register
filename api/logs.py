from __future__ import annotations

from fastapi import APIRouter

from api.models import LogListResponse, ClearResponse

from services.db import get_logs, get_logs_after, db_session

router = APIRouter()


@router.get("", summary="查询日志", response_model=LogListResponse,
            description="分页查询日志列表，支持按 after_id 增量拉取（轮询新日志）。")
@router.get("/", summary="查询日志", response_model=LogListResponse,
            description="分页查询日志列表，支持按 after_id 增量拉取（轮询新日志）。")
async def list_logs(limit: int = 1000, offset: int = 0, after_id: int = 0) -> dict:
    # M4: limit 校验上限，防前端一次拉超量拖垮
    limit = max(1, min(limit, 2000))
    offset = max(0, offset)
    if after_id > 0:
        logs = get_logs_after(after_id, limit=limit)
    else:
        logs = get_logs(limit=limit, offset=offset)
    return {"logs": logs, "total": len(logs)}


@router.post("/clear", summary="清空日志", response_model=ClearResponse,
            description="清空所有日志记录（谨慎操作，不可撤销）。")
async def clear_logs() -> dict:
    with db_session() as conn:
        cur = conn.execute("DELETE FROM logs")
    return {"success": True, "deleted": cur.rowcount}
