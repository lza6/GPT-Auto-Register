from __future__ import annotations

import random
import string
from pathlib import Path

PROXY_FILE = Path(__file__).resolve().parent.parent / "proxies.txt"


class ProxyService:
    """代理池服务 — 支持两种格式（每行一个）：

    1. kookeey 动态住宅代理（原格式，推荐）
       格式: gate.kookeey.info:1000:UserID-SecurityUser:SecurityPass-Country-RandomSession
       每次连接自动切换出口 IP。

    2. 通用 HTTP 代理
       格式: http://ip:port  或  host:port  或  host:port:user:pass
       按行轮询（round-robin）使用，每个账号使用池中下一个代理。
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
        self._load()

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
        """按顺序取池中下一个代理（kookeey 动态生成新 IP）"""
        if not self._entries:
            return None
        entry = self._entries[self._idx % len(self._entries)]
        self._idx += 1
        if entry["type"] == "kookeey":
            return self._kookeey_url(entry["line"]) or None
        return self._http_url(entry["line"])

    def get_random(self) -> str | None:
        """随机取一个代理（kookeey 动态生成新 IP）"""
        if not self._entries:
            return None
        entry = random.choice(self._entries)
        if entry["type"] == "kookeey":
            return self._kookeey_url(entry["line"]) or None
        return self._http_url(entry["line"])

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


proxy_service = ProxyService()
