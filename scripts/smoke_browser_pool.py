"""v3.1 T6 — 浏览器池真实 camoufox 复用冒烟（真实浏览器，非 mock）。

用途：验证 services/browser_pool 的占位→填充→release→同代理再 acquire 复用机制
在真实 camoufox 浏览器下成立（跨 acquire 不重启浏览器进程）。

运行：
    ./.venv/Scripts/python.exe scripts/smoke_browser_pool.py

退出码：0=PASS，1=FAIL。
说明：用本地 HTTP 服务器做真实导航目标，避免外网依赖。首次运行 camoufox 需已 fetch 浏览器数据。
"""
from __future__ import annotations

import asyncio
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b"<html><title>pool-smoke</title><body>ok</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # 静默
        pass


async def main() -> int:
    from camoufox import DefaultAddons
    from camoufox.async_api import AsyncCamoufox

    from services.browser_pool import BrowserPool

    srv = HTTPServer(("127.0.0.1", 23550), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    launch_count = {"n": 0}

    async def real_launch():
        launch_count["n"] += 1
        c = AsyncCamoufox(headless=True, exclude_addons=[DefaultAddons.UBO],
                          args=["--no-sandbox", "--disable-setuid-sandbox"])
        b = await c.start()
        return c, b

    pool = BrowserPool(max_size=2)
    proxy = ""  # 直连，聚焦验证池复用机制

    # 第一次：占位 → 锁外真实启动 → 导航 → release
    inst1 = await pool.acquire(proxy)
    assert inst1 is not None and inst1.camoufox is None, "首次应为占位实例"
    inst1.camoufox, inst1.browser = await real_launch()
    ctx = await inst1.browser.new_context()
    pg = await ctx.new_page()
    await pg.goto("http://127.0.0.1:23550/", timeout=20000)
    t1 = await pg.title()
    await pg.close()
    await ctx.close()
    await pool.release(inst1)
    print(f"[1] 首次启动真实浏览器 title={t1} launch_count={launch_count['n']} idle={pool.idle_size()}")

    # 第二次同代理：复用同一实例，不重启
    inst2 = await pool.acquire(proxy)
    reused = inst2 is inst1 and inst2.camoufox is inst1.camoufox
    ctx2 = await inst2.browser.new_context()
    pg2 = await ctx2.new_page()
    await pg2.goto("http://127.0.0.1:23550/", timeout=20000)
    t2 = await pg2.title()
    await pg2.close()
    await ctx2.close()
    await pool.release(inst2)
    print(f"[2] 同代理复用 reused={reused} title={t2} launch_count={launch_count['n']}（应仍为 1）")

    n = await pool.cleanup()
    srv.shutdown()
    print(f"[3] cleanup 关闭 {n} 个实例, 池 size={pool.size()}")

    ok = (t1 == "pool-smoke" and t2 == "pool-smoke" and reused
          and launch_count["n"] == 1 and n == 1 and pool.size() == 0)
    print("T6_REAL_POOL_SMOKE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
