from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from services.db import add_log, insert_email, mark_email_status


class EmailService:
    """邮箱服务：91kami 取件 + 98faka 邮件 API"""

    def __init__(self, api_base: str = "https://app.98faka.top") -> None:
        self.api_base = api_base.rstrip("/")

    async def fetch_emails_from_source(self, source_url: str) -> list[dict[str, str]]:
        """从 91kami 链接解析邮箱数据。
        91kami 是 SPA，需要调用其 API /api/Cpd/Detail 获取数据。
        URL 格式: https://mai.91kami.com/cpd/{token}.aspx
        """
        accounts: list[dict[str, str]] = []
        try:
            # 从 URL 提取 token
            token = ""
            m = re.search(r'/cpd/([a-z0-9]+)\.aspx', source_url)
            if m:
                token = m.group(1)
            if not token:
                add_log("error", f"无法从 URL 提取 token: {source_url}")
                return accounts

            # 调用 91kami API
            api_url = "https://mai.91kami.com/api/Cpd/Detail"
            async with httpx.AsyncClient(timeout=30, follow_redirects=True, verify=False) as client:
                resp = await client.post(api_url, json={"token": token})
                resp.raise_for_status()
                data = resp.json()
                if not data.get("IsSuccess"):
                    add_log("error", f"91kami API 返回失败: {data.get('Error_Msg', '未知错误')}")
                    return accounts

                detail = data.get("Data", {})
                # Data 可能是 JSON 字符串，需要二次解析
                if isinstance(detail, str):
                    try:
                        detail = json.loads(detail)
                    except json.JSONDecodeError:
                        pass
                if not detail:
                    add_log("error", "91kami API 返回空数据")
                    return accounts

                # 解析返回的卡密数据
                # 91kami 返回结构: Data -> [0].CardPwdArr[].c (格式: 邮箱----密码----client_id----refresh_token)
                if isinstance(detail, list) and len(detail) > 0:
                    card_pwd_arr = detail[0].get("CardPwdArr", [])
                    for card in card_pwd_arr:
                        c = card.get("c", "")
                        if not c:
                            continue
                        parts = c.split("----")
                        if len(parts) >= 4:
                            accounts.append({
                                "email": parts[0].strip(),
                                "password": parts[1].strip(),
                                "client_id": parts[2].strip(),
                                "refresh_token": parts[3].strip(),
                            })
                elif isinstance(detail, str):
                    # 兜底：纯文本格式
                    for line in detail.splitlines():
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        parts = line.split("----")
                        if len(parts) >= 4:
                            accounts.append({
                                "email": parts[0].strip(),
                                "password": parts[1].strip(),
                                "client_id": parts[2].strip(),
                                "refresh_token": parts[3].strip(),
                            })

                add_log("info", f"从 91kami 解析到 {len(accounts)} 个邮箱", {"token": token})
        except Exception as e:
            add_log("error", f"邮箱源解析失败: {e}", {"source_url": source_url})
        return accounts

    async def import_emails(self, source_url: str) -> dict[str, int]:
        """从源 URL 导入邮箱到数据库"""
        accounts = await self.fetch_emails_from_source(source_url)
        inserted = 0
        skipped = 0
        for acc in accounts:
            if not acc["email"]:
                skipped += 1
                continue
            if insert_email(acc["email"], acc["password"], acc["client_id"], acc["refresh_token"]):
                inserted += 1
            else:
                skipped += 1
        add_log("info", f"邮箱导入完成: 新增 {inserted}, 跳过 {skipped}")
        return {"inserted": inserted, "skipped": skipped, "total": len(accounts)}

    async def get_email_list(self, email: str, password: str, client_id: str,
                             refresh_token: str, folder: str = "inbox") -> list[dict]:
        """调用 98faka API 获取邮件列表"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.api_base}/api/emails",
                json={
                    "email": email,
                    "password": password,
                    "client_id": client_id,
                    "refresh_token": refresh_token,
                    "folder": folder,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") == 200:
                return data.get("data", [])
            return []

    async def get_email_body(self, email: str, message_id: str, client_id: str,
                             refresh_token: str) -> str:
        """调用 98faka API 获取邮件正文"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.api_base}/api/email-body",
                json={
                    "email": email,
                    "message_id": message_id,
                    "client_id": client_id,
                    "refresh_token": refresh_token,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") == 200:
                return data.get("body_html", "")
            return ""

    def extract_otp_code(self, body_html: str) -> str | None:
        """从 ChatGPT 验证邮件 HTML 中提取 6 位验证码。
        优先提取大字体验证码（font-size: 24px 区域的），
        其次提取灰色背景验证码区域，最后提取所有 6 位数字中最大的一个。
        """
        if not body_html:
            return None
        # 方法1: 匹配大字体验证码区域（font-size: 24px 背景的 6 位数字）
        m = re.search(r'font-size:\s*24px[^>]*>.*?(\d{6})', body_html, re.DOTALL)
        if m and not re.match(r'^(\d)\1{5}$', m.group(1)):
            return m.group(1)
        # 方法2: 匹配灰色背景验证码区域（background-color: #F3F3F3 的 6 位数字）
        m = re.search(r'background-color:\s*#F3F3F3[^>]*>.*?(\d{6})', body_html, re.DOTALL)
        if m and not re.match(r'^(\d)\1{5}$', m.group(1)):
            return m.group(1)
        # 方法3: 提取所有 6 位数字，排除重复数字和明显非验证码的
        codes = re.findall(r'\b(\d{6})\b', body_html)
        valid_codes = [
            c for c in codes
            if not re.match(r'^(\d)\1{5}$', c)
            and c not in ('90210', '90230', '10000', '20000', '30000', '40000', '50000', '60000', '70000', '80000', '90000')
        ]
        if valid_codes:
            return max(valid_codes)
        return None

    async def wait_for_otp(self, email: str, password: str, client_id: str,
                           refresh_token: str, timeout_sec: int = 120,
                           poll_interval: int = 5,
                           after_time: float | None = None,
                           skip_existing: bool = True) -> str | None:
        """轮询等待 ChatGPT 验证码邮件。
        after_time: 只读取该时间戳之后收到的邮件，避免读到旧验证码。
        skip_existing: 先记录当前收件箱所有邮件 ID，只读取新到达的邮件。
        """
        start_time = time.time()
        seen_ids: set[str] = set()

        # 先记录当前收件箱里所有邮件 ID（跳过这些旧邮件）
        if skip_existing:
            try:
                existing = await self.get_email_list(email, password, client_id, refresh_token)
                for mail in existing:
                    seen_ids.add(mail.get("id", ""))
                add_log("info", f"已记录 {len(seen_ids)} 封旧邮件，等待新验证码...", {"email": email})
            except Exception as e:
                add_log("warning", f"读取旧邮件列表异常: {e}", {"email": email})

        # 时间过滤：只读取最近 120 秒内收到的邮件
        from datetime import datetime
        min_time = start_time - 120
        while time.time() - start_time < timeout_sec:
            try:
                emails = await self.get_email_list(email, password, client_id, refresh_token)
                for mail in emails:
                    mail_id = mail.get("id", "")
                    if mail_id in seen_ids:
                        continue
                    # 时间过滤：跳过太旧的邮件
                    mail_time_str = mail.get("received_time", "")
                    if mail_time_str:
                        try:
                            mt = datetime.fromisoformat(mail_time_str.replace("Z", "+00:00")).timestamp()
                            if mt < min_time:
                                seen_ids.add(mail_id)
                                continue
                        except Exception:
                            pass
                    seen_ids.add(mail_id)
                    subject = mail.get("subject", "")
                    from_addr = mail.get("from_address", "")
                    # 匹配 ChatGPT/OpenAI 验证邮件
                    is_otp_mail = (
                        "chatgpt" in subject.lower()
                        or "openai" in subject.lower()
                        or "openai" in from_addr.lower()
                        or "noreply@tm.openai.com" in from_addr.lower()
                    )
                    if is_otp_mail:
                        body = await self.get_email_body(email, mail_id, client_id, refresh_token)
                        code = self.extract_otp_code(body)
                        if code:
                            add_log("info", f"成功提取验证码: {code}", {"email": email})
                            return code
            except Exception as e:
                add_log("warning", f"轮询验证码异常: {e}", {"email": email})
            await asyncio.sleep(poll_interval)
        add_log("error", f"验证码等待超时 ({timeout_sec}s)", {"email": email})
        return None


import asyncio

email_service = EmailService()
