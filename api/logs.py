from __future__ import annotations

from fastapi import APIRouter

from services.db import get_logs, get_conn

router = APIRouter()


@router.get("/")
async def list_logs(limit: int = 1000, offset: int = 0) -> dict:
    logs = get_logs(limit=limit, offset=offset)
    return {"logs": logs, "total": len(logs)}


@router.post("/clear")
async def clear_logs() -> dict:
    conn = get_conn()
    with conn:
        cur = conn.execute("DELETE FROM logs")
    conn.close()
    return {"success": True, "deleted": cur.rowcount}
