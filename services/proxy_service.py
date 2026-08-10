from __future__ import annotations

import json
import random
import string
import threading
import time
from pathlib import Path

PROXY_FILE = Path(__file__).resolve().parent.parent / "proxies.txt"
PROXY_BLACKLIST_FILE = Path(__file__).resolve().parent.parent / "data" / "proxy_blacklist.json"


class ProxyService:
    """代理池服务 — 支持两种格式（每行一个）：

    1. kookeey 动态住宅代理（原格式，推荐）
       格式: gate.kookeey.info:1000:UserID-SecurityUser:SecurityPass-Country-RandomSession
       每次连接自动切换出口 IP。

    2. 通用 HTTP 代理
       格式: http://ip:port  或  host:port  或  host:port:user:pass
       按行轮询（round-robin）使用，每个账号使用池中下一个代理。

    v3.4 T88：支持代理黑名单——risk_control 后 mark_bad(proxy_url, reason, ttl_sec)，
    黑名单内的代理在 TTL 内不会被 get_next/get_random 取到，避免已风控的出口被重复使用。
    """

    def __init__(self) -> None:
        self._entries: list[dict] = []
        self._idx = 0
        # kookeey 基础字段（保留兼容，取自第一条 kookeey 配置）
        self._base_proxy: str = ""
        self._user_id: str = ""
        self._security_user: str = ""
        self._security_pass: str = ""
        self._country: str = "US"
        self._gateway: str = "gate.kookeey.info"
        self._port: str = "1000"
        # v3.4 T88：代理黑名单 {proxy_url_line: {"expires_at": timestamp, "reason": str}}
        self._blacklist: dict[str, dict] = {}
        self._load()
        self._load_blacklist()

    def _load(self) -> None:
        entries: list[dict] = []
        if PROXY_FILE.exists():
            try:
                lines = PROXY_FILE.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                lines = PROXY_FILE.read_text(encoding="gbk", errors="ignore").splitlines()
            for line in lines:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(":")
                if len(parts) >= 4 and "-" in parts[2]:
                    # kookeey 格式: gateway:port:UserID-SecurityUser:Pass-Country
                    if not self._user_id:
                        self._gateway = parts[0]
                        self._port = parts[1]
                        user_parts = parts[2].split("-")
                        if len(user_parts) >= 2:
                            self._user_id = user_parts[0]
                            self._security_user = user_parts[1]
                        pass_parts = parts[3].split("-")
                        if len(pass_parts) >= 1:
                            self._security_pass = pass_parts[0]
                        if len(pass_parts) >= 2:
                            self._country = pass_parts[1]
                    entries.append({"type": "kookeey", "line": line})
                else:
                    # 通用 HTTP 代理
                    entries.append({"type": "http", "line": line})
        self._entries = entries

    @property
    def count(self) -> int:
        return len(self._entries)

    @property
    def country(self) -> str:
        return self._country

    def _kookeey_url(self, line: str) -> str:
        """根据 kookeey 行生成带随机 session 的代理 URL"""
        parts = line.split(":")
        if len(parts) < 4:
            return ""
        gateway, port = parts[0], parts[1]
        user_parts = parts[2].split("-")
        user_id = user_parts[0] if user_parts else ""
        security_user = user_parts[1] if len(user_parts) > 1 else ""
        pass_parts = parts[3].split("-")
        security_pass = pass_parts[0] if pass_parts else ""
        country = pass_parts[1] if len(pass_parts) > 1 else "US"
        session = ''.join(random.choices(string.digits, k=8))
        auth = f"{user_id}-{security_user}:{security_pass}-{country}-{session}"
        return f"http://{auth}@{gateway}:{port}"

    def _http_url(self, line: str) -> str:
        """把通用代理行转成完整 http URL"""
        s = line.strip()
        if s.startswith(("http://", "https://")):
            return s
        parts = s.split(":")
        if len(parts) == 4:
            # host:port:user:pass
            host, port, user, pw = parts
            return f"http://{user}:{pw}@{host}:{port}"
        if len(parts) == 2:
            return f"http://{parts[0]}:{parts[1]}"
        return "http://" + s

    def get_next(self) -> str | None:
        """按顺序取池中下一个代理（kookeey 动态生成新 IP），跳过黑名单内未到期的代理。"""
        if not self._entries:
            return None
        start_idx = self._idx
        for _ in range(len(self._entries)):
            entry = self._entries[self._idx % len(self._entries)]
            self._idx += 1
            # 跳过黑名单内的代理
            if entry["line"] in self._blacklist:
                blk = self._blacklist[entry["line"]]
                if time.time() < blk["expires_at"]:
                    continue  # 黑名单未到期，跳过
                # 已到期，从黑名单清除
                del self._blacklist[entry["line"]]
            if entry["type"] == "kookeey":
                return self._kookeey_url(entry["line"]) or None
            return self._http_url(entry["line"])
        # 所有代理都在黑名单中，回退到第一个（让调用方能拿到错误，不静默返回 None）
        self._idx = start_idx
        return None

    def get_random(self) -> str | None:
        """随机取一个代理（kookeey 动态生成新 IP），跳过黑名单内未到期的代理。"""
        if not self._entries:
            return None
        candidates = [e for e in self._entries if e["line"] not in self._blacklist
                      or time.time() >= self._blacklist[e["line"]]["expires_at"]]
        if not candidates:
            return None
        entry = random.choice(candidates)
        if entry["type"] == "kookeey":
            return self._kookeey_url(entry["line"]) or None
        return self._http_url(entry["line"])

    def mark_bad(self, proxy_url: str, reason: str = "risk_control", ttl_sec: int = 1800) -> None:
        """把代理加入黑名单，TTL 内不再被 get_next/get_random 取到。

        proxy_url: 完整代理 URL（如 http://user:pass@host:port），能匹配到 proxies.txt 某行则标记该行。
        reason: 标记原因（如 risk_control / network / timeout）。
        ttl_sec: 黑名单有效期（默认 30min），到期后自动恢复。
        """
        if not proxy_url:
            return
        # 从 proxy_url 反查 proxies.txt 行（反向匹配 host:port）
        try:
            from urllib.parse import urlparse
            u = urlparse(proxy_url)
            target = f"{u.hostname}:{u.port}" if u.port else u.hostname
        except Exception:
            target = proxy_url
        # 匹配最接近的行
        line = proxy_url  # 默认用完整 url
        for entry in self._entries:
            if target in entry["line"] or proxy_url in entry["line"]:
                line = entry["line"]
                break
        self._blacklist[line] = {
            "expires_at": time.time() + ttl_sec,
            "reason": reason,
        }
        # 持久化
        self._save_blacklist()

    def _load_blacklist(self) -> None:
        """从文件加载持久化黑名单（进程重启不丢失）。"""
        try:
            if PROXY_BLACKLIST_FILE.exists():
                raw = json.loads(PROXY_BLACKLIST_FILE.read_text(encoding="utf-8"))
                now = time.time()
                self._blacklist = {
                    k: v for k, v in raw.items()
                    if v.get("expires_at", 0) > now  # 只加载未过期的
                }
        except Exception:
            self._blacklist = {}

    def _save_blacklist(self) -> None:
        """持久化黑名单到文件。"""
        try:
            PROXY_BLACKLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
            PROXY_BLACKLIST_FILE.write_text(
                json.dumps(self._blacklist, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    def blacklist_size(self) -> int:
        """当前黑名单中未到期的代理数。"""
        now = time.time()
        return sum(1 for v in self._blacklist.values() if v.get("expires_at", 0) > now)

    def format_for_display(self, proxy_url: str | None) -> str:
        if not proxy_url:
            return "直连"
        try:
            from urllib.parse import urlparse
            u = urlparse(proxy_url)
            return f"{u.hostname}:{u.port}"
        except Exception:
            return proxy_url

    def reload(self) -> None:
        self._idx = 0
        self._load()

    # ── B9: auto_cleanup 后台线程 ──────────────────────────────

    def start_cleanup(self) -> None:
        """启动后台线程每 30min 自动检查一次所有代理健康，不健康的自动标记黑名单 30min。"""
        if hasattr(self, '_cleanup_thread') and self._cleanup_thread and self._cleanup_thread.is_alive():
            return
        self._cleanup_stop = threading.Event()

        def _loop() -> None:
            """后台循环：每 30min 检查一次所有代理健康。"""
            _cleanup_interval = 1800  # 30min
            while not self._cleanup_stop.is_set():
                # 使用 wait 替代 sleep 以实现可停止
                if self._cleanup_stop.wait(timeout=_cleanup_interval):
                    return
                try:
                    self._run_cleanup_once()
                except Exception:
                    pass

        self._cleanup_thread = threading.Thread(target=_loop, name="proxy-cleanup", daemon=True)
        self._cleanup_thread.start()

    def stop_cleanup(self) -> None:
        """停止后台清理线程。"""
        if hasattr(self, '_cleanup_stop'):
            self._cleanup_stop.set()

    def _run_cleanup_once(self) -> None:
        """执行一次代理健康检查：遍历所有代理，TCP 连通性 + HTTP 出口探测，不健康自动标记黑名单 30min。"""
        import asyncio
        import httpx
        from urllib.parse import urlparse

        for entry in self._entries:
            line = entry["line"]
            # 跳过已在黑名单中的代理
            if line in self._blacklist and time.time() < self._blacklist[line]["expires_at"]:
                continue

            # 构建代理 URL
            if entry["type"] == "kookeey":
                proxy_url = self._kookeey_url(line)
            else:
                proxy_url = self._http_url(line)

            if not proxy_url:
                continue

            try:
                u = urlparse(proxy_url)
                host = u.hostname
                port = u.port
                if not host or not port:
                    continue

                # TCP 连通性检查（同步，短超时）
                import socket
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(3)
                try:
                    result = s.connect_ex((host, port))
                    s.close()
                    if result != 0:
                        self.mark_bad(proxy_url, reason="auto_cleanup: TCP 不可达", ttl_sec=1800)
                        continue
                except Exception:
                    s.close()
                    self.mark_bad(proxy_url, reason="auto_cleanup: TCP 异常", ttl_sec=1800)
                    continue

                # HTTP 出口探测（需异步运行 httpx）
                try:
                    loop = asyncio.new_event_loop()
                    r = loop.run_until_complete(
                        _async_check_http(proxy_url)
                    )
                    loop.close()
                    if not r:
                        self.mark_bad(proxy_url, reason="auto_cleanup: HTTP 出口不可达", ttl_sec=1800)
                except Exception:
                    self.mark_bad(proxy_url, reason="auto_cleanup: HTTP 探测异常", ttl_sec=1800)
            except Exception:
                continue


async def _async_check_http(proxy_url: str) -> bool:
    """异步检查代理 HTTP 出口是否可达。"""
    try:
        import httpx
        async with httpx.AsyncClient(proxy=proxy_url, timeout=8, verify=False) as client:
            r = await client.get("https://api.ipify.org?format=json")
        return r.status_code == 200
    except Exception:
        return False


proxy_service = ProxyService()
