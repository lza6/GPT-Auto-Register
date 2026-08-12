"""TOTP 2FA 自动绑定（v4.0 P1-7）：注册会话快路径。

移植自 LocalFlow two_factor.py 的 bind_totp_2fa_inline（2026-08-04 实测跑通）。

原理：注册链几十秒前刚做完 OTP 验证 + create_account，服务端眼里就是
「最近认证过」——直接复用注册 session + access_token 打 enroll/activate，
6 秒左右跑完，零 PoW 零邮件（桌面文档说必须重走登录链是推测，实测不成立）。

流程：mfa_info（幂等检查，已绑跳过）→ mfa/enroll（secret 仅本次下发一次！）
→ 本地算当前 TOTP 码 → mfa/user/activate_enrollment 激活 → mfa_info 复核。

★ secret 服务端不存明文、任何接口取不回；丢了该号 2FA 永久锁死。
  调用方拿到 secret 后必须【立即落库】。
★ 绑定即生效：之后登录要 6 位动态码。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import struct
import time
from typing import Any

logger = logging.getLogger(__name__)

CHATGPT_BASE = "https://chatgpt.com/backend-api"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)


# ── RFC 6238 手写实现（无 pyotp 依赖）─────────────────────────


def hotp(secret_b32: str, counter: int, digits: int = 6) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    o = h[-1] & 0x0F
    code = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def totp_now(secret_b32: str) -> str:
    """当前 30 秒窗口的 6 位码。"""
    return hotp(secret_b32, int(time.time()) // 30)


def verify_totp(secret_b32: str, code: str) -> bool:
    """前后各 1 窗口容错的本地自检。"""
    c = int(time.time()) // 30
    return code in {hotp(secret_b32, c + d) for d in (-1, 0, 1)}


# ── 三步：幂等检查 → enroll → activate ────────────────────────


def _headers(referer: str, ua: str = "") -> dict:
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "referer": referer,
        "user-agent": ua or DEFAULT_UA,
    }


def _json(resp: Any) -> dict:
    try:
        data = resp.json() if getattr(resp, "text", None) else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def enroll_totp(session: Any, access_token: str, *, ua: str = "") -> dict | None:
    """用注册会话直接 enroll TOTP，成功返回 {secret, factor_id, session_id}；失败返回 None。

    session: 协议注册的 curl_cffi Session（同一 cookie/UA/IP），带 proxy。
    access_token: 注册刚换到的 AT。
    任何失败返回 None（不抛异常），由调用方决定是否回落慢路径/放弃。
    """
    try:
        # 1. 幂等检查：已绑 totp 则不重复 enroll（secret 取不回）
        hh = _headers(f"{CHATGPT_BASE}/accounts/mfa_info", ua)
        hh["Authorization"] = f"Bearer {access_token}"
        r2 = session.get(f"{CHATGPT_BASE}/accounts/mfa_info", headers=hh, timeout=30)
        if getattr(r2, "status_code", 0) == 200:
            info = _json(r2)
            if info.get("mfa_enabled") and (info.get("factors") or {}).get("totp"):
                logger.info("TOTP 2FA 已绑定，跳过 enroll（secret 无法从服务端取回）")
                return None

        # 2. enroll：secret 只在本次响应出现一次
        hh = _headers(f"{CHATGPT_BASE}/accounts/mfa/enroll", ua)
        hh["Authorization"] = f"Bearer {access_token}"
        r3 = session.post(
            f"{CHATGPT_BASE}/accounts/mfa/enroll",
            headers=hh, json={"factor_type": "totp"}, timeout=30,
        )
        if getattr(r3, "status_code", 0) != 200:
            logger.warning("TOTP enroll %s: %s", r3.status_code, (r3.text or "")[:200])
            return None
        en = _json(r3)
        secret = str(en.get("secret") or "").strip()
        session_id = str(en.get("session_id") or "").strip()
        factor_id = str((en.get("factor") or {}).get("id") or "").strip()
        if not secret or not session_id:
            logger.warning("TOTP enroll 响应缺 secret/session_id: %s", str(en)[:200])
            return None

        # 3. 算码并激活
        code = totp_now(secret)
        hh = _headers(f"{CHATGPT_BASE}/accounts/mfa/user/activate_enrollment", ua)
        hh["Authorization"] = f"Bearer {access_token}"
        r4 = session.post(
            f"{CHATGPT_BASE}/accounts/mfa/user/activate_enrollment",
            headers=hh,
            json={"code": code, "factor_type": "totp", "session_id": session_id},
            timeout=30,
        )
        if getattr(r4, "status_code", 0) != 200:
            logger.warning(
                "TOTP activate_enrollment %s: %s（429 可等 60s 换码重试）",
                r4.status_code, (r4.text or "")[:200],
            )
            return None

        logger.info("TOTP 2FA 绑定成功（enroll+activate 均 200），secret 已下发待落库")
        return {"secret": secret, "factor_id": factor_id, "session_id": session_id}
    except Exception as e:  # noqa: BLE001 — 绝不能拖垮已注册成功的号
        logger.warning("TOTP 绑定异常（不阻塞注册成功）: %s", e)
        return None
