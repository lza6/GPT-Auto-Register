"""OTP 验证码提取公共模块（B3：三合一，消除三份重复正则）。

email_service / graph_email_service / imap_email_service 原各自实现：
  1. font-size:24px 的 6 位数字
  2. background-color:#F3F3F3 的 6 位数字
  3. 兜底 \b\d{6}\b
  4. 过滤全同数字（111111/222222...）

三份完全相同，改一处漏两处。现收敛到本模块。
"""
from __future__ import annotations

import re

# 全同数字模式（111111、222222...）：OpenAI 验证码不会是全同数字
_ALL_SAME_DIGIT = re.compile(r"^(\d)\1{5}$")
# 地址/邮编等常见误命中数字（非验证码）：从原 graph_email_service 黑名单迁移
_BLOCKLIST = frozenset({
    "90210", "90230", "10000", "20000", "30000", "40000",
    "50000", "60000", "70000", "80000", "90000",
})
# OpenAI 验证码邮件样式特征（大字号 / 高亮背景）
_FONT_24PX = re.compile(r"font-size:\s*24px[^>]*>.*?(\d{6})", re.DOTALL)
_BG_F3F3F3 = re.compile(r"background-color:\s*#F3F3F3[^>]*>.*?(\d{6})", re.DOTALL)
# 兜底：任意 6 位数字
_ANY_6 = re.compile(r"\b(\d{6})\b")


def _is_valid_code(code: str) -> bool:
    """排除全同数字与黑名单地址数字（非真实验证码）。"""
    return bool(code) and not _ALL_SAME_DIGIT.match(code) and code not in _BLOCKLIST


def extract_otp_code(body_html: str) -> str | None:
    """从邮件 HTML 正文提取 OpenAI 6 位验证码。

    优先级：大字号样式 > 高亮背景样式 > 任意 6 位数字。
    全同数字（111111 等）视为无效，跳过。
    """
    if not body_html:
        return None
    # 1. 大字号样式（最可靠，OpenAI 官方模板）
    m = _FONT_24PX.search(body_html)
    if m and _is_valid_code(m.group(1)):
        return m.group(1)
    # 2. 高亮背景样式
    m = _BG_F3F3F3.search(body_html)
    if m and _is_valid_code(m.group(1)):
        return m.group(1)
    # 3. 兜底：取所有非全同/非黑名单 6 位数字中最大的一个
    # （原 email/graph service 行为：最新验证码通常数值最大）
    valid = [c for c in _ANY_6.findall(body_html) if _is_valid_code(c)]
    if valid:
        return max(valid)
    return None


def is_otp_mail(subject: str, from_addr: str = "") -> bool:
    """判断是否为 OpenAI/ChatGPT 验证码邮件。"""
    blob = f"{subject} {from_addr}".lower()
    return any(kw in blob for kw in ("openai", "chatgpt", "verification", "verify", "code"))
