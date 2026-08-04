"""登录页形态检测 / CF 挑战识别 — 单元测试。"""
from __future__ import annotations

from services.login_detector import (
    decide_login_action,
    detect_login_page,
    is_cf_challenge,
    summarize_page_text,
)


class TestIsCfChallenge:
    def test_url_challenge_platform(self):
        assert is_cf_challenge(url="https://challenges.cloudflare.com/challenge-platform")

    def test_html_just_a_moment(self):
        assert is_cf_challenge(html="<title>Just a moment...</title>")

    def test_html_chinese_checking(self):
        assert is_cf_challenge(html="正在检查你的浏览器")

    def test_html_turnstile(self):
        assert is_cf_challenge(html="<iframe src=\"/cdn-cgi/challenge-platform/\">")

    def test_normal_page_not_cf(self):
        assert not is_cf_challenge(url="https://auth.openai.com/", html="<input name=email>")

    def test_empty_not_cf(self):
        assert not is_cf_challenge()


class TestDetectLoginPage:
    def test_about_you_url(self):
        assert detect_login_page(url="https://auth.openai.com/about-you") == "about-you"

    def test_callback_with_code(self):
        url = "https://platform.openai.com/auth/callback?code=ac_123&state=abc"
        assert detect_login_page(url=url) == "callback"

    def test_cf_takes_precedence_over_inputs(self):
        # CF 挑战页可能残留隐藏 input，CF 必须优先识别
        html = "Just a moment...<input name=email>"
        assert detect_login_page(html=html, input_names=["email"]) == "cf"

    def test_email_page(self):
        assert detect_login_page(input_names=["email", "password"]) == "email"

    def test_password_page(self):
        assert detect_login_page(input_names=["current-password"]) == "password"

    def test_new_password_page(self):
        assert detect_login_page(input_names=["new-password"]) == "password"

    def test_otp_page(self):
        assert detect_login_page(input_names=["code"]) == "otp"

    def test_otp_autocomplete_page(self):
        assert detect_login_page(input_names=["one-time-code"]) == "otp"

    def test_unknown_page(self):
        assert detect_login_page(url="https://auth.openai.com/error") == "unknown"

    def test_input_names_case_insensitive(self):
        assert detect_login_page(input_names=["Email"]) == "email"


class TestDecideLoginAction:
    def test_email_fills(self):
        assert decide_login_action("email") == "fill_email"

    def test_password_switches_to_otp_login(self):
        assert decide_login_action("password") == "switch_otp_login"

    def test_otp_waits(self):
        assert decide_login_action("otp") == "wait_otp"

    def test_about_you_skips_login(self):
        assert decide_login_action("about-you") == "skip_login"

    def test_callback_skips_login(self):
        assert decide_login_action("callback") == "skip_login"

    def test_unknown_fails(self):
        assert decide_login_action("unknown") == "fail"

    def test_cf_fails_but_should_be_handled_earlier(self):
        # CF 必须在上游处理；落到这里即视为失败
        assert decide_login_action("cf") == "fail"


class TestSummarizePageText:
    def test_collapses_whitespace(self):
        text = summarize_page_text("a\n\nb\t c")
        assert text == "a b c"

    def test_limits_length(self):
        text = summarize_page_text("x" * 1000)
        assert len(text) <= 300

    def test_empty(self):
        assert summarize_page_text("") == ""
