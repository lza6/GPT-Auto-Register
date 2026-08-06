from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.db import get_pending_emails, count_emails, insert_email, db_session

router = APIRouter()


class ManualAddRequest(BaseModel):
    """手动批量添加邮箱。每行格式: 邮箱----密码----client_id----refresh_token"""
    text: str = ""


@router.get("/pending")
async def pending_emails(limit: int = 100) -> dict:
    emails = get_pending_emails(limit=limit)
    return {"emails": emails, "total": count_emails("pending")}


@router.get("/")
async def list_emails(status: str = "", limit: int = 0, offset: int = 0, search: str = "") -> dict:
    """邮箱池列表，支持状态筛选 / 邮箱搜索 / 分页。total 用 COUNT 查询避免全表装载。"""
    # v3.1 审计：limit 收敛安全范围（0/负数→默认 100，上限 500），避免全表明文凭据被一次性拉取
    limit = min(limit, 500) if limit > 0 else 100
    offset = max(0, offset)
    conds: list[str] = []
    params: list = []
    if status:
        conds.append("status = ?")
        params.append(status)
    if search:
        conds.append("email LIKE ?")
        params.append(f"%{search.strip()}%")
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    sql = f"SELECT * FROM emails{where} ORDER BY id DESC LIMIT ? OFFSET ?"
    with db_session() as conn:
        rows = conn.execute(sql, params + [limit, offset]).fetchall()
    total = count_emails(status=status, search=search)
    return {"emails": [dict(r) for r in rows], "total": total}


@router.post("/manual-add")
async def manual_add(req: ManualAddRequest) -> dict:
    """手动批量添加邮箱（支持 91kami 格式多行粘贴）"""
    if not req.text.strip():
        raise HTTPException(400, "内容为空")
    inserted = 0
    skipped = 0
    for line in req.text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("----")
        if len(parts) < 1:
            skipped += 1
            continue
        email = parts[0].strip()
        password = parts[1].strip() if len(parts) > 1 else ""
        client_id = parts[2].strip() if len(parts) > 2 else ""
        refresh_token = parts[3].strip() if len(parts) > 3 else ""
        if not email:
            skipped += 1
            continue
        if insert_email(email, password, client_id, refresh_token):
            inserted += 1
        else:
            skipped += 1
    return {"success": True, "inserted": inserted, "skipped": skipped, "total": inserted + skipped}


@router.delete("/{email_id}")
async def delete_email(email_id: int) -> dict:
    with db_session() as conn:
        cur = conn.execute("DELETE FROM emails WHERE id = ?", (email_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "邮箱不存在")
    return {"success": True}


@router.post("/clear")
async def clear_emails() -> dict:
    with db_session() as conn:
        cur = conn.execute("DELETE FROM emails")
    return {"success": True, "deleted": cur.rowcount}
