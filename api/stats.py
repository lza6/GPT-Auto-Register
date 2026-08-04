from __future__ import annotations

from fastapi import APIRouter

from services.db import get_stats, count_accounts, count_emails

router = APIRouter()


@router.get("/")
async def stats() -> dict:
    return get_stats()
