from __future__ import annotations

from fastapi import APIRouter

from services.db import get_logs, get_logs_after, db_session

router = APIRouter()


@router.get("")
@router.get("/")
async def list_logs(limit: int = 1000, offset: int = 0, after_id: int = 0) -> dict:
    if after_id > 0:
        logs = get_logs_after(after_id, limit=limit)
    else:
        logs = get_logs(limit=limit, offset=offset)
    return {"logs": logs, "total": len(logs)}


@router.post("/clear")
async def clear_logs() -> dict:
    with db_session() as conn:
        cur = conn.execute("DELETE FROM logs")
    return {"success": True, "deleted": cur.rowcount}
