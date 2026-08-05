"""浏览器页面选择器集中管理（browser_register 专用）。

上游 OpenAI 改版（选择器变化）时只需修改本文件，无需改动注册主流程。
选择器字符串与 browser_register 原实现完全一致，行为不变，仅收敛常量。
"""

# ── 邮箱输入框：首选精确 name，兜底多候选 ──
EMAIL_PRIMARY = 'input[name="email"]'
EMAIL_FALLBACK = 'input[type="email"], input[placeholder*="email" i], input[id*="email" i]'

# ── 新账号设置 OpenAI 密码 ──
NEW_PASSWORD_PRIMARY = 'input[name="new-password"]'

# ── 验证码输入框 ──
OTP_PRIMARY = 'input[name="code"], input[autocomplete="one-time-code"]'
OTP_FALLBACK = 'input[placeholder*="code" i], input[placeholder*="Code" i]'

# ── 姓名（about-you 页） ──
NAME_PRIMARY = 'input[name="name"]'
NAME_FALLBACK = 'input[placeholder*="name" i], input[placeholder*="Name" i], input[id*="name" i]'

# ── 提交按钮 ──
SUBMIT_PRIMARY = 'button[type="submit"]'
SUBMIT_FALLBACK = 'button:has-text("Continue"), button:has-text("Verify"), button:has-text("继续")'

# ── cookie 弹窗拒绝 ──
COOKIE_REJECT_SELECTORS = (
    'button:has-text("Reject optional")',
    'button:has-text("Reject")',
    'button:has-text("拒绝")',
)

# ── 密码登录页 → 切换邮箱验证码登录 ──
SWITCH_OTP_SELECTORS = (
    'button:has-text("邮箱验证码")',
    'button:has-text("验证码")',
    'a:has-text("one-time code")',
    'button:has-text("one-time code")',
    'button:has-text("email verification")',
)
