from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
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


@router.get("/health")
async def proxies_health() -> dict:
    """批量探测代理池可用性（v3.0 P2-4）。

    对每条代理尝试建立 TCP/HTTP 连接，3s 超时，并发限 10。
    返回每条代理的 ✅/❌ 状态，前端展示徽章 + 失效标红。
    """
    content = _read_proxy_file()
    lines = [l.strip() for l in content.splitlines()
             if l.strip() and not l.strip().startswith("#")]
    sem = asyncio.Semaphore(10)

    async def check_one(line: str) -> dict:
        async with sem:
            # kookeey 格式用 _kookeey_url 探测，其余用通用 _http_url
            parts = line.split(":")
            if len(parts) >= 4 and "-" in parts[2]:
                proxy_url = proxy_service._kookeey_url(line)
            else:
                proxy_url = proxy_service._http_url(line)
            # 解析 host:port 探测 TCP 可达性
            try:
                from urllib.parse import urlparse
                u = urlparse(proxy_url or "")
                host = u.hostname
                port = u.port
                if not host or not port:
                    return {"line": line, "ok": False, "error": "解析失败"}
                # TCP 连通性探测（3s 超时）
                try:
                    _, _ = await asyncio.wait_for(
                        asyncio.open_connection(host, port), timeout=3
                    )
                except (asyncio.TimeoutError, OSError) as e:
                    return {"line": line, "ok": False, "error": f"不可达: {type(e).__name__}"}
                return {"line": line, "ok": True, "error": ""}
            except Exception as e:
                return {"line": line, "ok": False, "error": str(e)[:50]}

    results = await asyncio.gather(*(check_one(l) for l in lines))
    ok_count = sum(1 for r in results if r["ok"])
    return {
        "total": len(results),
        "ok": ok_count,
        "failed": len(results) - ok_count,
        "results": results,
    }
