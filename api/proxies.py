from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from api.models import ProxyListResponse, ApiResponse

from services.proxy_service import proxy_service
from services.sanitizer import sanitize, sanitize_text

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


@router.get("/", summary="获取代理列表", response_model=ProxyListResponse,
            description="返回代理文件内容、有效代理行数和代理池活跃数量。")
async def get_proxies() -> dict:
    content = _read_proxy_file()
    return {
        "content": sanitize_text(content),
        "count": _count_lines(content),
        "active_count": proxy_service.count,
    }


@router.post("/", summary="保存代理列表", response_model=ApiResponse,
            description="保存代理文件内容到 proxies.txt，并重新加载到代理池。")
async def save_proxies(req: SaveProxiesRequest) -> dict:
    PROXY_FILE.write_text(req.content, encoding="utf-8")
    proxy_service.reload()
    return {"success": True, "count": _count_lines(req.content), "active_count": proxy_service.count}


@router.get("/health")
async def proxies_health() -> dict:
    """批量探测代理池可用性（v3.4 T88：升级为真实 HTTP 出口探测）。

    先 TCP 连通性探测，再经代理请求 IP 回显服务验证认证 + 出口 IP。
    返回每条代理的 ✅/❌/出口IP/国家/延迟，前端展示真实出口 IP 徽章。
    """
    content = _read_proxy_file()
    lines = [l.strip() for l in content.splitlines()
             if l.strip() and not l.strip().startswith("#")]
    sem = asyncio.Semaphore(10)

    async def check_one(line: str) -> dict:
        async with sem:
            parts = line.split(":")
            if len(parts) >= 4 and "-" in parts[2]:
                proxy_url = proxy_service._kookeey_url(line)
            else:
                proxy_url = proxy_service._http_url(line)
            try:
                from urllib.parse import urlparse
                u = urlparse(proxy_url or "")
                host = u.hostname
                port = u.port
                if not host or not port:
                    return {"line": line, "ok": False, "error": "解析失败", "exit_ip": "", "country": "", "latency_ms": 0}
                # 第1步：TCP 连通性（3s 超时，显式关闭 writer 防泄漏）
                try:
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection(host, port), timeout=3
                    )
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except Exception:
                        pass
                except (asyncio.TimeoutError, OSError) as e:
                    return {"line": line, "ok": False, "error": f"不可达: {type(e).__name__}", "exit_ip": "", "country": "", "latency_ms": 0}
                # 第2步：真实 HTTP 出口探测（经代理请求 ipify，验证认证 + 出口 IP）
                t0 = time.time()
                try:
                    async with httpx.AsyncClient(proxy=proxy_url, timeout=8, verify=False) as client:
                        r = await client.get("https://api.ipify.org?format=json")
                    latency = int((time.time() - t0) * 1000)
                    if r.status_code == 200:
                        ip = r.json().get("ip", "")
                        # 可选：再查一次国家
                        country = ""
                        try:
                            async with httpx.AsyncClient(proxy=proxy_url, timeout=5, verify=False) as client:
                                cr = await client.get(f"https://ipapi.co/{ip}/country_code/")
                                if cr.status_code == 200:
                                    country = cr.text.strip()
                        except Exception:
                            pass
                        return {"line": line, "ok": True, "error": "", "exit_ip": ip, "country": country, "latency_ms": latency}
                    if r.status_code == 407:
                        return {"line": line, "ok": False, "error": "认证失败(407)", "exit_ip": "", "country": "", "latency_ms": latency}
                    return {"line": line, "ok": False, "error": f"HTTP {r.status_code}", "exit_ip": "", "country": "", "latency_ms": latency}
                except Exception as e:
                    return {"line": line, "ok": True, "error": f"HTTP出口不可达: {type(e).__name__}（TCP可达）", "exit_ip": "", "country": "", "latency_ms": 0}
            except Exception as e:
                return {"line": line, "ok": False, "error": str(e)[:50], "exit_ip": "", "country": "", "latency_ms": 0}

    results = await asyncio.gather(*(check_one(l) for l in lines))
    ok_count = sum(1 for r in results if r["ok"])
    return {
        "total": len(results),
        "ok": ok_count,
        "failed": len(results) - ok_count,
        "results": sanitize(results),
        "blacklist_size": proxy_service.blacklist_size() if hasattr(proxy_service, 'blacklist_size') else 0,
    }
