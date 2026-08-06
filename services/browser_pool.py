"""浏览器实例池（H3：camoufox 实例复用框架）。

设计权衡：
- camoufox 实例启动慢（冷启动 3-5s），池化可复用已启动实例。
- 但本项目代理是 per-账号 独立（kookeey 每账号独立 IP），池实例绑定代理会串 IP。
- 故池默认 size=0（不池化，保持原按需启停行为），仅在代理固定场景才启用。

落地范围（v3.0）：
1. 提供 BrowserPool 框架（acquire/release/cleanup），独立可单测。
2. healthz 的 browser_pool_size 反映真实池大小（替代固定值 1）。
3. browser_register 默认走原冷启动路径；config.browser_pool_size>0 时走池。
"""
from __future__ import annotations

import asyncio
from typing import Any


class _PooledInstance:
    """池中一个 camoufox 实例对（camoufox 句柄 + browser 句柄）。"""

    __slots__ = ("camoufox", "browser", "proxy_url", "last_used")

    def __init__(self, camoufox: Any, browser: Any, proxy_url: str = "") -> None:
        self.camoufox = camoufox
        self.browser = browser
        self.proxy_url = proxy_url
        self.last_used = 0.0


class BrowserPool:
    """camoufox 实例池（LRU + 代理绑定）。

    线程/协程安全：asyncio.Lock 保护 idle 队列与 in_use 集合。
    池实例绑定 proxy_url（同一代理出口的实例可复用，避免串 IP）。
    """

    def __init__(self, max_size: int = 0) -> None:
        self._max_size = max(0, int(max_size))
        self._idle: list[_PooledInstance] = []  # LRU：末尾最新用
        self._in_use: set[_PooledInstance] = set()
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def max_size(self) -> int:
        return self._max_size

    def size(self) -> int:
        """当前池中实例总数（idle + in_use）。"""
        return len(self._idle) + len(self._in_use)

    def idle_size(self) -> int:
        return len(self._idle)

    async def acquire(self, proxy_url: str = "") -> _PooledInstance | None:
        """获取一个池实例。

        匹配 proxy_url 的空闲实例优先复用；无匹配且未达上限则 lazy 创建；
        池满则阻塞等待归还。max_size=0 时返回 None（调用方走原冷启动路径）。
        """
        if self._max_size == 0 or self._closed:
            return None

        async with self._lock:
            # 优先复用同代理的空闲实例
            for i, inst in enumerate(self._idle):
                if inst.proxy_url == proxy_url:
                    self._idle.pop(i)
                    self._in_use.add(inst)
                    inst.last_used = asyncio.get_event_loop().time()
                    return inst
            # 未达上限则创建（创建在锁外执行，避免阻塞其他 acquirer）
            if self.size() < self._max_size:
                inst = _PooledInstance(None, None, proxy_url)
                self._in_use.add(inst)
                # 释放锁后再创建（camoufox 启动慢）
                # 这里先返回占位，创建由调用方在锁外完成
                return inst  # inst.camoufox/browser 为 None → 调用方需填充
        # 池满：阻塞等待
        return await self._wait_for_release(proxy_url)

    async def _wait_for_release(self, proxy_url: str) -> _PooledInstance | None:
        """池满时阻塞等待归还（带超时，避免死锁）。"""
        deadline = asyncio.get_event_loop().time() + 30
        while asyncio.get_event_loop().time() < deadline:
            async with self._lock:
                for i, inst in enumerate(self._idle):
                    if inst.proxy_url == proxy_url:
                        self._idle.pop(i)
                        self._in_use.add(inst)
                        return inst
            await asyncio.sleep(0.5)
        return None  # 超时未获取，调用方走原路径

    async def release(self, inst: _PooledInstance | None) -> None:
        """归还实例到池（max_size=0 或实例无效时直接销毁）。"""
        if inst is None:
            return
        async with self._lock:
            if inst in self._in_use:
                self._in_use.discard(inst)
            if self._closed or self._max_size == 0:
                # 池关闭或未启用 → 销毁实例
                await self._destroy(inst)
                return
            if inst.camoufox is None:
                # 创建失败的占位实例，直接丢弃
                return
            self._idle.append(inst)

    async def _destroy(self, inst: _PooledInstance) -> None:
        """销毁单个实例（关闭 browser + 停止 camoufox）。"""
        try:
            if inst.browser:
                await inst.browser.close()
        except Exception:
            pass
        try:
            if inst.camoufox:
                await inst.camoufox.stop()
        except Exception:
            pass

    async def cleanup(self) -> int:
        """关闭所有实例子进程，返回清理数量。shutdown 时调用。"""
        async with self._lock:
            self._closed = True
            all_inst = self._idle + list(self._in_use)
            self._idle.clear()
            self._in_use.clear()
        n = 0
        for inst in all_inst:
            await self._destroy(inst)
            n += 1
        return n


# 全局池单例（max_size=0 时不池化，保持原按需启停行为）
browser_pool: BrowserPool | None = None


def get_browser_pool(config: dict[str, Any]) -> BrowserPool | None:
    """获取全局浏览器池单例。

    config.browser_pool_size <= 0 时返回 None（调用方走原冷启动路径）。
    """
    global browser_pool
    try:
        size = config.get("browser_pool_size", 0)
        # 兼容字符串（settings API 存字符串）
        if isinstance(size, str):
            size = int(size) if size.strip().lstrip("-").isdigit() else 0
        size = max(0, int(size))
    except Exception:
        size = 0

    if size == 0:
        return None  # 未启用池化

    if browser_pool is None or browser_pool.max_size != size:
        browser_pool = BrowserPool(max_size=size)
    return browser_pool


def pool_size_for_healthz(config: dict[str, Any] | None = None) -> int:
    """供 healthz 调用：返回当前池实例数（未初始化/未启用 = 0）。

    替代 api/__init__.py 原固定返回 1 的逻辑，反映真实池大小。
    """
    if browser_pool is None:
        return 0
    return browser_pool.size()
