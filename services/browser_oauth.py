"""OAuth PKCE 生成、code 捕获与 token 交换（browser_register 子模块）。

提取自 browser_register.py 的 OAuth 相关逻辑，保持向后兼容（BrowserRegister 保留同名委托方法）。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from services.constants import (
    OAUTH_CLIENT_ID,
    OAUTH_REDIRECT_URI,
    OAUTH_TOKEN_URL,
    tls_verify_enabled,
)
from services.db import add_log


def generate_pkce() -> tuple[str, str]:
    """生成 PKCE code_verifier 与 S256 code_challenge。"""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


async def wait_for_oauth_code(page, timeout: int = 45) -> str:
    """轮询页面 URL，等待 OAuth authorize 重定向到 redirect_uri 并携带 code。

    返回捕获到的 code；超时返回空字符串。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            url = page.url
            if url and "code=" in url:
                q = parse_qs(urlparse(url).query)
                code = (q.get("code") or [""])[0]
                if code:
                    return code
        except Exception:
            pass
        await asyncio.sleep(1)
    return ""


async def exchange_code(code: str, code_verifier: str,
                         proxy_url: str | None,
                         config: dict[str, Any] | None = None) -> dict | None:
    """用 OAuth code + verifier 换取 access_token / refresh_token / id_token。

    返回格式：{"access_token": str, "refresh_token": str, "id_token": str}
    失败返回 None。
    """
    cfg = config or {}
    try:
        proxy_kwargs: dict[str, Any] = {}
        if proxy_url:
            proxy_kwargs["proxy"] = proxy_url
        async with httpx.AsyncClient(timeout=60, verify=tls_verify_enabled(cfg), **proxy_kwargs) as client:
            resp = await client.post(
                OAUTH_TOKEN_URL,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Origin": "https://platform.openai.com",
                    "Referer": "https://platform.openai.com/",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
                },
                json={
                    "client_id": OAUTH_CLIENT_ID,
                    "code_verifier": code_verifier,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": OAUTH_REDIRECT_URI,
                },
            )
            data = resp.json() if resp.text else {}
            if resp.status_code == 200 and data.get("access_token"):
                return {
                    "access_token": data["access_token"],
                    "refresh_token": data.get("refresh_token", ""),
                    "id_token": data.get("id_token", ""),
                }
            add_log("warning", f"OAuth code 换 token 失败: HTTP {resp.status_code}")
            return None
    except Exception as e:
        add_log("warning", f"OAuth code 换 token 异常: {e}")
        return None