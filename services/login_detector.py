"""OpenAI 登录页形态检测与 Cloudflare 挑战识别（纯逻辑，供注册流程与测试复用）。

不依赖任何浏览器 / 网络库，方便单元测试。
"""
from __future__ import annotations

CF_CHALLENGE_MARKERS = (
    "challenge-platform",
    "cf_chl_",
    "just a moment",
    "checking your browser",
    "正在检查你的浏览器",
    "turnstile",
)

EMAIL_INPUT_NAMES = ("email",)
PASSWORD_INPUT_NAMES = ("current-password", "password", "new-password")
OTP_INPUT_NAMES = ("code", "one-time-code", "otp")


def is_cf_challenge(url: str = "", html: str = "") -> bool:
    """判断页面是否处于 Cloudflare 挑战（URL 或 HTML 命中特征）。"""
    if not url and not html:
        return False
    blob = (url + " " + html).lower()
    return any(marker in blob for marker in CF_CHALLENGE_MARKERS)


def detect_login_page(url: str = "", html: str = "", input_names: list[str] | None = None) -> str:
    """识别 OpenAI 登录页形态，返回以下之一：

    - ``email``       邮箱输入页（新账号注册 / 登录第一步）
    - ``password``    密码登录页
    - ``otp``         验证码输入页
    - ``about-you``   资料填写页
    - ``callback``    已拿到 OAuth code
    - ``cf``          Cloudflare 挑战
    - ``unknown``     无法识别

    ``input_names``：页面可见 input 的 name / type / placeholder / autocomplete 集合，
    由调用方从浏览器采集。
    """
    if "about-you" in url:
        return "about-you"
    if "auth/callback" in url and "code=" in url:
        return "callback"
    # CF 优先：挑战页可能残留隐藏 input，必须最先识别
    if is_cf_challenge(url, html):
        return "cf"
    names = {str(n).lower() for n in (input_names or []) if n}
    if names & set(EMAIL_INPUT_NAMES):
        return "email"
    if names & set(PASSWORD_INPUT_NAMES):
        return "password"
    if names & set(OTP_INPUT_NAMES):
        return "otp"
    return "unknown"


def summarize_page_text(text: str, limit: int = 300) -> str:
    """压缩页面文字用于日志诊断（不会打印完整 token）。"""
    if not text:
        return ""
    collapsed = " ".join(str(text).split())
    return collapsed[:limit]


# 页面形态 → 下一步动作的映射
_ACTION_BY_PAGE_KIND = {
    "email": "fill_email",
    "password": "switch_otp_login",
    "otp": "wait_otp",
    "about-you": "skip_login",
    "callback": "skip_login",
}


def decide_login_action(page_kind: str) -> str:
    """根据检测到的页面形态决定注册流程的下一步动作。

    返回：
    - ``fill_email``      邮箱输入页 → 输入邮箱并提交
    - ``switch_otp_login`` 密码登录页 → 切换「邮箱验证码登录」
    - ``wait_otp``        已在验证码页 → 直接等待/填写验证码
    - ``skip_login``      已到 about-you / callback → 跳过登录阶段
    - ``fail``            无法识别（含 CF，CF 需在分流前单独处理）→ 明确报错

    ``cf`` 形态不应走到这里：调用方应在 detect 后先处理 CF 挑战再重新 detect。
    """
    return _ACTION_BY_PAGE_KIND.get(page_kind, "fail")
