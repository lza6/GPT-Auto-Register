"""config.json 类型校验（P0/P2-6：堵住脏数据源头）。

背景：settings API (api/settings.py) 把所有值存为字符串，导致
`register_concurrency="true"`、`register_interval_sec="15"` 等脏数据，
运行时 _as_int 兜底为默认值，用户以为开了并发实际是 1。

本模块只做 warn-only 校验（不阻断启动，兼容老部署），返回结构化问题清单，
main.py 启动时打印；settings.py 写入时同步校验给出建议。
"""
from __future__ import annotations

from typing import Any

# 配置 schema：键 → (期望类型元组, 默认值, 是否可为空)
# 期望类型用元组兼容「int 或数字字符串」这类历史脏数据场景
_CONFIG_SCHEMA: dict[str, tuple[tuple[type, ...], Any, bool]] = {
    "auth_key": ((str,), "", True),
    "auth_enforced": ((bool, str), False, False),
    "port": ((int,), 23457, False),
    "proxy_file": ((str,), "proxies.txt", False),
    "email_source_url": ((str,), "", True),
    "email_api_base": ((str,), "https://app.98faka.top", False),
    "register_concurrency": ((int,), 1, False),
    "register_interval_sec": ((int,), 10, False),
    "otp_wait_timeout_sec": ((int,), 600, False),
    "otp_poll_interval_sec": ((int,), 5, False),
    "otp_min_age_window_sec": ((int,), 120, False),
    "otp_fallback_after_sec": ((int,), 40, False),
    "otp_backfill_window_min": ((int,), 15, False),
    "user_agent": ((str,), "", False),
    "token_output_file": ((str,), "已经获取到的token.txt", False),
    "email_output_file": ((str,), "获取到的所有邮箱.txt", False),
    "batch_size": ((int,), 100, False),
    "use_proxy": ((bool,), True, False),
    "use_oauth_pkce": ((bool,), True, False),
    "use_browser": ((bool, str), True, False),
    "protocol_first": ((bool, str), True, False),
    "proxy_url": ((str,), "", True),
    "chatgpt2api_url": ((str,), "http://127.0.0.1:23456", False),
    "chatgpt2api_admin_key": ((str,), "", True),
    "log_retention_days": ((int,), 30, False),
    # v3.0 新增（schema 留位，缺省走默认）
    "browser_pool_size": ((int,), 0, False),
    "token_refresh_enabled": ((bool, str), False, False),
    "token_refresh_interval_sec": ((int,), 21600, False),
    "cf_retry_max": ((int,), 2, False),
    # v3.1 安全审计：TLS 证书校验开关（默认 true，SSL 拦截代理可设 false）
    "tls_verify": ((bool, str), True, False),
    # 一账号一指纹（v3.3）：TLS 指纹固定值/自定义池（空=默认 Chrome 池随机）
    "tls_fingerprint": ((str,), "", True),
    "tls_fingerprint_pool": ((str,), "", True),
    # 真实 sentinel SDK 求解（v4.0）：Node vm 跑真实 sdk.js，防合成 PoW 被服务端深度校验识破
    # （邮件验证码 silent-drop）。无 node 或失败时自动降级合成 PoW。
    "sentinel_quickjs": ((bool, str), True, False),
    # warmup 种 cookie（v4.0 P0-3）：注册前 GET chatgpt.com 种 oai-did，防 authorize 409
    "warmup_enabled": ((bool, str), True, False),
    "warmup_retries": ((int,), 2, False),
    # 注册成功后自动绑 TOTP 2FA（v4.0 P1-7）：同会话快路径，失败不阻塞注册成功
    "totp_enabled": ((bool, str), True, False),
    # 阶段间随机 think_time（v4.0 P1-8，毫秒，0=关闭）
    "think_time_ms": ((int,), 1500, False),
    # 链路级 TLS 瞬断重试次数（v4.0 P1-9，同 session 重试）
    "tls_retries": ((int,), 2, False),
}


class ConfigIssue:
    """单个配置问题（结构化，便于前端/日志展示）。"""

    __slots__ = ("key", "expected", "actual_type", "actual_value", "hint")

    def __init__(self, key: str, expected: str, actual_type: str,
                 actual_value: str, hint: str) -> None:
        self.key = key
        self.expected = expected
        self.actual_type = actual_type
        self.actual_value = actual_value
        self.hint = hint

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "expected": self.expected,
            "actual_type": self.actual_type,
            "actual_value": self.actual_value,
            "hint": self.hint,
        }

    def __str__(self) -> str:
        return (
            f"  - {self.key}: 期望 {self.expected}，实际 {self.actual_type}"
            f"({self.actual_value})。{self.hint}"
        )


def _type_name(t: tuple[type, ...]) -> str:
    return " 或 ".join(x.__name__ for x in t)


def validate_config(config: dict[str, Any]) -> list[ConfigIssue]:
    """校验 config 字典，返回问题清单（空列表 = 全部合规）。

    warn-only：不抛异常、不阻断启动，仅返回结构化告警。
    兼容「数字字符串」历史脏数据：int 键若为纯数字字符串，提示但不视为致命。
    """
    issues: list[ConfigIssue] = []
    if not isinstance(config, dict):
        return issues

    for key, (expected_types, default, allow_empty) in _CONFIG_SCHEMA.items():
        if key not in config:
            continue  # 缺省走默认值，不报
        value = config[key]

        # 布尔开关兼容字符串 'true'/'false'（settings API 存字符串）
        if bool in expected_types and isinstance(value, str):
            if value.strip().lower() in ("true", "false", "1", "0", "yes", "on"):
                continue  # 布尔字符串，可接受
            issues.append(ConfigIssue(
                key=key, expected=_type_name(expected_types),
                actual_type=type(value).__name__, actual_value=repr(value)[:60],
                hint=f"布尔开关建议直接写 true/false，当前字符串无法识别。",
            ))
            continue

        # int 键兼容纯数字字符串（历史脏数据，提示修正）
        if int in expected_types and isinstance(value, str):
            if value.strip().lstrip("-").isdigit():
                issues.append(ConfigIssue(
                    key=key, expected=_type_name(expected_types),
                    actual_type=type(value).__name__, actual_value=repr(value)[:60],
                    hint=f"数字被存为字符串，建议改为 int 类型（如 {int(value)}）。",
                ))
                continue
            issues.append(ConfigIssue(
                key=key, expected=_type_name(expected_types),
                actual_type=type(value).__name__, actual_value=repr(value)[:60],
                hint=f"应为整数，当前是非法字符串（运行时兜底为默认 {default}）。",
            ))
            continue

        # 类型严格匹配（bool 是 int 子类，单独先判）
        if not isinstance(value, expected_types):
            # bool 被当 int 用时 isinstance(True, int) 为 True，这里不会误报
            issues.append(ConfigIssue(
                key=key, expected=_type_name(expected_types),
                actual_type=type(value).__name__, actual_value=repr(value)[:60],
                hint=f"类型不符，建议改为 {expected_types[0].__name__} 类型。",
            ))
            continue

        # 空值检查
        if not allow_empty and isinstance(value, str) and not value.strip():
            issues.append(ConfigIssue(
                key=key, expected=_type_name(expected_types),
                actual_type=type(value).__name__, actual_value=repr(value)[:60],
                hint=f"该键不允许为空。",
            ))

    return issues


def format_issues(issues: list[ConfigIssue]) -> str:
    """格式化问题清单为控制台友好文本。"""
    if not issues:
        return ""
    lines = [f"[config] 检测到 {len(issues)} 项配置问题（warn-only，不阻断启动）："]
    for issue in issues:
        lines.append(str(issue))
    return "\n".join(lines)
