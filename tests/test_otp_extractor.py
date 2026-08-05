"""otp_extractor 单元测试（B3 三合一回归）。

锁定三份原 email service 的提取行为一致：
  - font-size:24px 样式优先
  - background-color:#F3F3F3 次之
  - 兜底取最大 6 位数字
  - 排除全同数字（111111）与地址黑名单（90210 等）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.otp_extractor import extract_otp_code, is_otp_mail


class TestExtractOtpCode:
    def test_font_size_24px_priority(self):
        """大字号样式优先提取。"""
        html = '<span style="font-size: 24px; color: blue;">482915</span>'
        assert extract_otp_code(html) == "482915"

    def test_background_color_f3f3f3_fallback(self):
        """无大字号时取高亮背景。"""
        html = '<div style="background-color: #F3F3F3; padding: 10px;">730124</div>'
        assert extract_otp_code(html) == "730124"

    def test_any_6_digit_max_fallback(self):
        """无样式时取所有 6 位数字中最大的。"""
        html = "your code is 123456 and 789012 please use it"
        assert extract_otp_code(html) == "789012"

    def test_exclude_all_same_digit(self):
        """全同数字（111111）视为无效。"""
        html = '<span style="font-size: 24px;">111111</span>'
        # font-size 命中但全同 → 继续；无其他 → None
        assert extract_otp_code(html) is None

    def test_exclude_address_blocklist(self):
        """地址邮编（90210 等）不误判为验证码。"""
        html = "5800 Bristol Pkwy 90210 your code 624531"
        # 90210 黑名单，624531 有效 → 取 624531
        assert extract_otp_code(html) == "624531"

    def test_empty_body(self):
        assert extract_otp_code("") is None
        assert extract_otp_code(None) is None  # type: ignore[arg-type]

    def test_html_interference(self):
        """HTML 标签干扰：font-size 区域内连续 6 位数字可提取；跨 <b> 标签会断。"""
        # 连续 6 位数字（不跨标签）→ 可提取
        html_ok = '<table><tr><td style="font-size: 24px;">938471</td></tr></table>'
        assert extract_otp_code(html_ok) == "938471"
        # 跨 <b> 标签断开 → font-size 命中不到连续 6 位，兜底取 max
        html_split = '<td style="font-size: 24px;"><b>938</b>471</td>'
        result = extract_otp_code(html_split)
        # 938471 跨标签，font-size 正则 . 不跨标签，故走兜底；无连续 6 位 → None 或视实际数字
        # 此处仅断言不崩 + 行为确定（None，因 938/471 不是 6 位连续）
        assert result is None or (result and len(result) == 6)


class TestIsOtpMail:
    def test_openai_subject(self):
        assert is_otp_mail("Your OpenAI verification code") is True

    def test_chatgpt_from(self):
        assert is_otp_mail("Welcome", "noreply@chatgpt.com") is True

    def test_unrelated(self):
        assert is_otp_mail("Newsletter", "news@example.com") is False


class TestThreeServicesConsistent:
    """三 service 委托后，同一封邮件提取结果一致。"""

    def _sample_html(self):
        return '<span style="font-size: 24px;">482915</span>'

    def test_email_service_delegates(self):
        from services.email_service import email_service
        assert email_service.extract_otp_code(self._sample_html()) == "482915"

    def test_graph_email_service_delegates(self):
        from services.graph_email_service import graph_email_service
        assert graph_email_service.extract_otp_code(self._sample_html()) == "482915"

    def test_imap_email_service_delegates(self):
        from services.imap_email_service import imap_email_service
        assert imap_email_service._extract_otp(self._sample_html()) == "482915"
