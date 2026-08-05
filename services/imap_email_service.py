from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from services.db import add_log


class ImapEmailService:
    """用 IMAP 直接登录 Outlook 邮箱读取邮件（每个邮箱独立收件箱）"""

    async def get_otp_code(self, email: str, password: str,
                           timeout_sec: int = 600, poll_interval: int = 10) -> str | None:
        """等待并获取 ChatGPT 验证码（用 IMAP 直接登录邮箱）"""
        start_time = time.time()
        seen_ids: set[str] = set()

        while time.time() - start_time < timeout_sec:
            try:
                code = await self._fetch_latest_otp(email, password, seen_ids)
                if code:
                    add_log("info", "IMAP 成功提取验证码", {"email": email})
                    return code
            except Exception as e:
                add_log("warning", f"IMAP 轮询异常: {e}", {"email": email})
            await asyncio.sleep(poll_interval)

        add_log("error", f"IMAP 验证码等待超时 ({timeout_sec}s)", {"email": email})
        return None

    async def _fetch_latest_otp(self, email: str, password: str,
                                 seen_ids: set[str]) -> str | None:
        """用 IMAP 获取最新 ChatGPT 验证码"""
        from imapclient import IMAPClient

        # Outlook IMAP 服务器
        server = "outlook.office365.com"
        port = 993

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._imap_fetch, server, port, email, password, seen_ids
        )

    def _imap_fetch(self, server: str, port: int, email: str,
                    password: str, seen_ids: set[str]) -> str | None:
        """同步 IMAP 获取验证码"""
        from imapclient import IMAPClient

        with IMAPClient(server, port=port, ssl=True) as client:
            client.login(email, password)
            client.select_folder("INBOX")

            # 搜索 ChatGPT/OpenAI 的邮件（最近 10 封）
            messages = client.search(["FROM", "noreply@tm.openai.com"])
            if not messages:
                messages = client.search(["SUBJECT", "ChatGPT"])
            if not messages:
                return None

            # 取最新的几封
            latest = sorted(messages, reverse=True)[:5]
            for msg_id in latest:
                msg_str = str(msg_id)
                if msg_str in seen_ids:
                    continue
                seen_ids.add(msg_str)

                # 获取邮件正文
                raw = client.fetch([msg_id], ["BODY[]"])
                body = raw[msg_id][b"BODY[]"].decode("utf-8", errors="replace")

                # 提取验证码
                code = self._extract_otp(body)
                if code:
                    return code

        return None

    def _extract_otp(self, body: str) -> str | None:
        """从邮件正文中提取 6 位验证码"""
        # 方法1: 匹配大字体验证码区域
        m = re.search(r'font-size:\s*24px[^>]*>.*?(\d{6})', body, re.DOTALL)
        if m and not re.match(r'^(\d)\1{5}$', m.group(1)):
            return m.group(1)
        # 方法2: 匹配灰色背景验证码区域
        m = re.search(r'background-color:\s*#F3F3F3[^>]*>.*?(\d{6})', body, re.DOTALL)
        if m and not re.match(r'^(\d)\1{5}$', m.group(1)):
            return m.group(1)
        # 方法3: 提取所有 6 位数字，返回最大的
        codes = re.findall(r'\b(\d{6})\b', body)
        valid = [
            c for c in codes
            if not re.match(r'^(\d)\1{5}$', c)
            and c not in ('90210', '90230', '10000', '20000', '30000', '40000', '50000', '60000', '70000', '80000', '90000')
        ]
        if valid:
            return max(valid)
        return None


imap_email_service = ImapEmailService()
