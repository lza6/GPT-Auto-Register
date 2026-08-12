"""账号存活探活（v4.0 P1-4）：wham/usage 三态分类 + 额度解析。

移植自 GPT-Register-Tool 的 account_liveness.py（canonical 探活实现）。

为什么需要它：
  巡检只靠 refresh_token 盲刷，会：
  - 白白消耗配额/触发限流（账号其实还活着，AT 没过期）；
  - 账号 AT 已被吊销（401）却无感知，chatgpt2api 导入了坏账号。
  探活把账号分成三态：
    active         → 2xx，正常可用（跳过刷新，省配额）
    token_invalid  → 401 / token invalidated / invalid_grant（AT 失效，触发恢复链）
    unknown        → 403/429/传输失败（绝不误判为 AT 失效，留待复查）

唯一探活端点：``chatgpt.com/backend-api/wham/usage``，请求头模拟 codex_cli_rs。
"""
from __future__ import annotations

import base64
import json
import re
from typing import Any

import httpx

CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CODEX_QUOTA_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal",
}


def chatgpt_id_from_token(token: Any) -> str:
    """从 JWT（id_token/access_token）提取 chatgpt_account_id（Chatgpt-Account-Id 头）。"""
    if isinstance(token, dict):
        return str(token.get("chatgpt_account_id") or token.get("chatgptAccountId") or "").strip()
    text = str(token or "").strip()
    parts = text.split(".")
    if len(parts) < 2:
        return ""
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        data = json.loads(decoded.decode("utf-8"))
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    auth = data.get("https://api.openai.com/auth")
    if isinstance(auth, dict):
        account_id = str(auth.get("chatgpt_account_id") or auth.get("chatgptAccountId") or "").strip()
        if account_id:
            return account_id
    return str(data.get("chatgpt_account_id") or data.get("chatgptAccountId") or "").strip()


def account_chatgpt_id(access_token: str = "", id_token: str = "") -> str:
    """综合 id_token / access_token 提取账号 ID（任一非空即返回）。"""
    for token in (id_token, access_token):
        cid = chatgpt_id_from_token(token)
        if cid:
            return cid
    return ""


def classify_liveness(status_code: int, error_text: str) -> str:
    """三态分类：active / token_invalid / unknown。

    token_invalid：401 或「authentication token has been invalidated」等吊销标记。
    403/429/传输失败一律 unknown —— 绝不把限流/临时错误误判成 AT 失效，
    否则恢复链会用坏 token 覆盖好 token。
    """
    low = str(error_text or "").lower()
    if status_code == 401 or re.search(
        r"\b401\b|unauthorized|authentication token has been invalidated|"
        r"token has been invalidated|invalid_grant",
        low,
    ):
        return "token_invalid"
    if 200 <= int(status_code or 0) < 300:
        return "active"
    return "unknown"


def parse_wham_usage(body: Any) -> dict[str, Any] | None:
    """解析 5h/7d 两个窗口的 used/limit/remaining/percent（可选增强，失败返回 None）。"""
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except Exception:
            return None
    if not isinstance(body, dict):
        return None
    result: dict[str, Any] = {}
    for window_key in ("5h", "7d"):
        parsed = _parse_usage_window(body, window_key)
        if parsed:
            result[window_key] = parsed
    return result or None


def format_wham_usage_label(usage: dict[str, Any] | None) -> str:
    """把额度格式化为 '5h: 1.2K/10K (12%) | 7d: ...' 展示标签。"""
    if not usage:
        return ""
    parts = []
    for window_key in ("5h", "7d"):
        window = usage.get(window_key)
        if not isinstance(window, dict):
            continue
        used = int(window.get("used", 0) or 0)
        limit = int(window.get("limit", 0) or 0)
        percent = float(window.get("percent", 0) or 0)
        parts.append(f"{window_key}: {_fmt(used)}/{_fmt(limit)} ({percent:.0f}%)")
    return " | ".join(parts)


def _parse_usage_window(body: dict[str, Any], window_key: str) -> dict[str, Any] | None:
    containers = [
        section
        for key in ("usage", "rate_limits", "limits", "rate_limits_info")
        if isinstance((section := body.get(key)), dict)
    ]
    containers.append(body)
    alternatives = {
        "5h": ("5h", "300min", "five_hours", "short"),
        "7d": ("7d", "10080min", "seven_days", "weekly", "long"),
    }
    for container in containers:
        window = next(
            (container.get(key) for key in alternatives.get(window_key, (window_key,))
             if isinstance(container.get(key), dict)),
            None,
        )
        if not isinstance(window, dict):
            continue

        def _pick(keys: tuple[str, ...]) -> int | None:
            for key in keys:
                value = window.get(key)
                if value is not None:
                    try:
                        return int(value)
                    except (TypeError, ValueError):
                        pass
            return None

        used = _pick(("used", "num_tokens_used", "tokens_used", "consumed"))
        limit = _pick(("limit", "num_tokens_limit", "tokens_limit", "max", "cap"))
        remaining = _pick(("remaining", "num_tokens_remaining", "tokens_remaining", "available"))
        if remaining is None and used is not None and limit is not None:
            remaining = max(0, limit - used)
        if used is None and remaining is not None and limit is not None:
            used = max(0, limit - remaining)
        if used is not None or limit is not None or remaining is not None:
            return {
                "used": used or 0,
                "limit": limit or 0,
                "remaining": remaining or 0,
                "percent": round((used or 0) * 100.0 / limit, 1) if limit else 0.0,
            }
    return None


def _fmt(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


async def probe_access_token(
    access_token: str,
    *,
    id_token: str = "",
    proxy: str | None = None,
    timeout: int = 30,
    verify: bool = True,
) -> dict[str, Any]:
    """探活单个 access_token，返回 {status, status_code, error, quota_label, wham_usage}。

    status ∈ {active, token_invalid, unknown}。任何异常都归 unknown（不误判 AT 失效）。
    """
    if not str(access_token or "").strip():
        return {"status": "unknown", "status_code": 0, "error": "missing_access_token",
                "quota_label": "缺少AT", "wham_usage": None}
    headers = dict(CODEX_QUOTA_HEADERS)
    headers["Authorization"] = f"Bearer {access_token}"
    account_id = account_chatgpt_id(access_token, id_token)
    if account_id:
        headers["Chatgpt-Account-Id"] = account_id
    proxy_kwargs: dict[str, Any] = {}
    if proxy:
        proxy_kwargs["proxy"] = proxy
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=verify, **proxy_kwargs) as client:
            resp = await client.get(CODEX_USAGE_URL, headers=headers)
        try:
            body = resp.json()
        except Exception:
            body = {"raw": str(resp.text or "")[:500]}
        error_text = _extract_error_text(resp.status_code, body)
        status = classify_liveness(resp.status_code, error_text)
        usage = parse_wham_usage(body)
        return {
            "status": status,
            "status_code": resp.status_code,
            "error": error_text,
            "quota_label": format_wham_usage_label(usage),
            "wham_usage": usage,
        }
    except Exception as exc:
        return {
            "status": "unknown",
            "status_code": 0,
            "error": str(exc)[:300],
            "quota_label": "检测失败",
            "wham_usage": None,
        }


def _extract_error_text(status_code: int, body: Any) -> str:
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return " ".join(str(error.get(k) or "") for k in ("message", "code") if error.get(k))[:300]
        if error:
            return str(error)[:300]
        if body.get("message"):
            return str(body["message"])[:300]
        return str(status_code)
    return str(body or "")[:300]
