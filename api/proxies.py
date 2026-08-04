from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from services.proxy_service import proxy_service

router = APIRouter()

PROXY_FILE = Path(__file__).resolve().parent.parent / "proxies.txt"


def _read_proxy_file() -> str:
    if not PROXY_FILE.exists():
        return ""
    try:
        return PROXY_FILE.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return PROXY_FILE.read_text(encoding="gbk", errors="ignore")


def _count_lines(content: str) -> int:
    return len([l for l in content.splitlines() if l.strip() and not l.strip().startswith("#")])


class SaveProxiesRequest(BaseModel):
    content: str = ""


@router.get("/")
async def get_proxies() -> dict:
    content = _read_proxy_file()
    return {
        "content": content,
        "count": _count_lines(content),
        "active_count": proxy_service.count,
    }


@router.post("/")
async def save_proxies(req: SaveProxiesRequest) -> dict:
    PROXY_FILE.write_text(req.content, encoding="utf-8")
    proxy_service.reload()
    return {"success": True, "count": _count_lines(req.content), "active_count": proxy_service.count}
