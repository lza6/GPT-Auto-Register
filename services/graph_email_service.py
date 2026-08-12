from __future__ import annotations

import asyncio
import re
import time
from collections import OrderedDict
from datetime import datetime
from typing import Any

import httpx

from services.db import add_log


def _iso_to_epoch(s: str) -> float:
    """ISO8601 时间字符串(可带 Z)转 epoch 秒，解析失败返回 0。"""
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


class GraphEmailService:
    """直接用 Microsoft Graph API 读取邮件（替代 98faka API）"""

    MAX_CACHE_ENTRIES = 64  # token 缓存 LRU 上限，防止长期运行内存膨胀

    def __init__(self) -> None:
        self._token_cache: OrderedDict[str, tuple[str, float]] = OrderedDict()

    def _cache_put(self, key: str, value: tuple[str, float]) -> None:
        self._token_cache[key] = value
        self._token_cache.move_to_end(key)
        if len(self._token_cache) > self.MAX_CACHE_ENTRIES:
            self._token_cache.popitem(last=False)  # 逐出最久未用

    async def _get_access_token(self, client_id: str, refresh_token: str) -> str | None:
        """用 refresh_token 获取 access_token（带 LRU 缓存）"""
        # 复合 key：同 client_id 下不同邮箱（不同 refresh_token）对应不同用户，
        # 只取 client_id 会让第二个邮箱复用第一个的 access_token 读别人的收件箱（P0-3 串号修复）。
        cache_key = f"{client_id}:{refresh_token}"
        if cache_key in self._token_cache:
            token, expire_time = self._token_cache[cache_key]
            if time.time() < expire_time - 60:
                self._token_cache.move_to_end(cache_key)  # 命中 → LRU 置新
                return token
            # 过期项惰性清除
            del self._token_cache[cache_key]

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                    data={
                        "client_id": client_id,
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "scope": "https://graph.microsoft.com/.default",
                    },
                )
                data = resp.json()
                access_token = data.get("access_token", "")
                expires_in = data.get("expires_in", 3600)
                if access_token:
                    self._cache_put(cache_key, (access_token, time.time() + expires_in))
                    return access_token
                add_log("error", f"Token 刷新失败: {data.get('error_description', data.get('error', 'unknown'))}")
                return None
        except Exception as e:
            add_log("error", f"Token 刷新异常: {e}")
            return None

    async def get_email_list(self, email: str, client_id: str, refresh_token: str,
                             top: int = 10) -> list[dict[str, Any]]:
        """用 Graph API 获取邮件列表"""
        access_token = await self._get_access_token(client_id, refresh_token)
        if not access_token:
            return []

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={
                        "$top": top,
                        "$orderby": "receivedDateTime desc",
                        "$select": "id,subject,from,receivedDateTime,bodyPreview",
                    },
                )
                data = resp.json()
                messages = data.get("value", [])
                # 转为 98faka 兼容格式
                result = []
                for msg in messages:
                    result.append({
                        "id": msg.get("id", ""),
                        "subject": msg.get("subject", ""),
                        "from_address": msg.get("from", {}).get("emailAddress", {}).get("address", ""),
                        "received_time": msg.get("receivedDateTime", ""),
                        "body_preview": msg.get("bodyPreview", ""),
                    })
                return result
        except Exception as e:
            add_log("error", f"Graph API 获取邮件列表异常: {e}", {"email": email})
            return []

    async def get_email_body(self, email: str, message_id: str, client_id: str,
                             refresh_token: str) -> str:
        """用 Graph API 获取邮件正文"""
        access_token = await self._get_access_token(client_id, refresh_token)
        if not access_token:
            return ""

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"https://graph.microsoft.com/v1.0/me/messages/{message_id}",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={"$select": "body"},
                )
                data = resp.json()
                body = data.get("body", {})
                return body.get("content", "")
        except Exception as e:
            add_log("error", f"Graph API 获取邮件正文异常: {e}", {"email": email})
            return ""

    def extract_otp_code(self, body_html: str) -> str | None:
        """从 ChatGPT 验证邮件 HTML 中提取 6 位验证码（委托公共模块，B3 三合一）。"""
        from services.otp_extractor import extract_otp_code as _extract
        return _extract(body_html)

    async def wait_for_otp(self, email: str, client_id: str, refresh_token: str,
                           timeout_sec: int = 600, poll_interval: int = 5,
                           skip_code: str = "") -> str | None:
        """获取最新的 ChatGPT 验证码（直接读最新邮件，不等待新邮件）"""
        start_time = time.time()
        seen_codes: set[str] = set()
        if skip_code:
            seen_codes.add(skip_code)

        while time.time() - start_time < timeout_sec:
            try:
                emails = await self.get_email_list(email, client_id, refresh_token, top=5)
                for mail in emails:
                    subject = mail.get("subject", "")
                    from_addr = mail.get("from_address", "")
                    is_otp_mail = (
                        "chatgpt" in subject.lower()
                        or "openai" in subject.lower()
                        or "noreply@tm.openai.com" in from_addr.lower()
                    )
                    if is_otp_mail:
                        mail_id = mail.get("id", "")
                        body = await self.get_email_body(email, mail_id, client_id, refresh_token)
                        code = self.extract_otp_code(body)
                        if code and code not in seen_codes:
                            seen_codes.add(code)
                            add_log("info", "成功提取验证码", {"email": email})
                            return code
            except Exception as e:
                add_log("warning", f"轮询验证码异常: {e}", {"email": email})
            await asyncio.sleep(poll_interval)
        add_log("error", f"验证码等待超时 ({timeout_sec}s)", {"email": email})
        return None

    async def wait_for_new_otp(self, email: str, client_id: str, refresh_token: str,
                               after_time: str, timeout_sec: int = 300,
                               poll_interval: int = 5) -> str | None:
        """等待在 after_time 之后到达的 ChatGPT 验证码（跳过旧码，只取新邮件）。

        after_time 为 ISO8601 时间字符串（如 2026-08-05T07:00:00Z），
        用于在触发登录后只接收新发的验证码邮件，避免读到已过期的旧码。
        """
        start_time = time.time()
        while time.time() - start_time < timeout_sec:
            try:
                emails = await self.get_email_list(email, client_id, refresh_token, top=8)
                for mail in emails:
                    received = str(mail.get("received_time") or "")
                    # 只接受晚于 after_time 的新邮件（统一转 epoch 比较，避免字符串序误判）
                    if received and _iso_to_epoch(received) <= _iso_to_epoch(after_time):
                        continue
                    subject = str(mail.get("subject") or "").lower()
                    from_addr = str(mail.get("from_address") or "").lower()
                    is_otp_mail = (
                        "chatgpt" in subject
                        or "openai" in subject
                        or "noreply@tm.openai.com" in from_addr
                    )
                    if is_otp_mail:
                        mail_id = mail.get("id", "")
                        body = await self.get_email_body(email, mail_id, client_id, refresh_token)
                        code = self.extract_otp_code(body)
                        if code:
                            add_log("info", "成功提取新验证码（已脱敏）", {"email": email})
                            return code
            except Exception as e:
                add_log("warning", f"轮询新验证码异常: {e}", {"email": email})
            await asyncio.sleep(poll_interval)
        add_log("error", f"新验证码等待超时 ({timeout_sec}s)", {"email": email})
        return None


graph_email_service = GraphEmailService()
