"""纯协议注册引擎（curl_cffi + sentinel，无浏览器）。

OpenAI 需要 sentinel 风控参数才能通过 create_account；本项目将 chatgpt2api
的 SentinelTokenGenerator 移植到 :mod:`services.sentinel`，协议注册不再依赖外部项目。

支持两种账号形态：
- ``create-account``（新号）：authorize → user/register(设密码) → email-otp/send →
  取码 → email-otp/validate → (about-you) create_account → callback code → 换三件套
- ``log-in``（半成品 / 已存在）：authorize → passwordless/send-otp 触发 OTP →
  取码 → email-otp/validate → (如需 about-you) create_account → callback code → 换三件套

失败时会给出 ``fallback_browser`` 标记，供调用方决定是否降级浏览器。
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import enum
import hashlib
import logging
import random
import re
import secrets
import string
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import json

import requests

from services.sentinel import build_sentinel_token
from services.sentinel_quickjs import (
    QuickJSNetworkError,
    get_sentinel_token_via_quickjs,
    node_available,
    quickjs_script_available,
)

logger = logging.getLogger(__name__)

# OpenAI OAuth 常量：收敛到 services/constants.py（B5），保留别名向后兼容
from services.constants import (
    OAUTH_CLIENT_ID,
    OAUTH_REDIRECT_URI as REDIRECT_URI,
    OAUTH_AUDIENCE,
    OAUTH_AUTH0_CLIENT as AUTH0_CLIENT,
    OAUTH_ISSUER as AUTH_BASE,
    tls_verify_enabled,
    country_locale,
)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)

# 各端点对应的 sentinel flow（抓包确认）
FLOW_REGISTER = "username_password_create"

# ── 错误分类常量 ──
NETWORK_ERROR_MARKERS = (
    "tls", "ssl", "sslerror", "eof occurred", "connection",
    "connect error", "timeout", "timed out", "proxy",
    "socks", "dns", "name resolution", "winerror 10060",
    "curl: (35)", "curl: (28)", "curl: (6)", "curl: (7)",
    "remote disconnected", "connection reset", "connection aborted",
    "max retries exceeded", "sentinel", "cloudflare", "turnstile",
    "broken pipe", "certificate verify failed", "econnrefused",
    "econnreset", "etimedout",
)
ACCOUNT_ERROR_MARKERS = (
    "account_deactivated", "account deactivated", "account has been deactivated",
    "deleted or deactivated", "registration_disallowed",
    "invalid_grant", "authenticationfailed", "invalid credentials",
    "wrong_email_otp_code", "password_verify_failed",
    "phone_recently_used", "unsupported_phone_number", "fraud_guard",
    "token_invalidated", "max_check_attempts",
)
MAILBOX_ERROR_MARKERS = (
    "outlook otp timeout", "email_otp_poll_timeout", "mailbox otp timeout",
    "invalid_or_expired_otp", "invalid code", "mailbox_otp_timeout",
    "otp timeout",
)
AUTH_STATE_ERROR_MARKERS = (
    "invalid_auth_step", "invalid_state", "sign-in session is no longer valid",
)
RATE_LIMIT_MARKERS = (
    "rate_limit", "too_many", "429", "too many", "ratelimit",
)


def error_text(value) -> str:
    if isinstance(value, dict):
        parts = []
        for key in ("error", "error_code", "message", "body", "status"):
            item = value.get(key)
            if item:
                parts.append(str(item))
        for key in ("refresh", "oauth", "relogin", "token_probe"):
            item = value.get(key)
            if isinstance(item, dict):
                parts.append(error_text(item))
        if not parts:
            try:
                parts.append(json.dumps(value, ensure_ascii=False, default=str)[:1000])
            except Exception:
                pass
        return " ".join(parts).lower()
    return str(value or "").lower()


def classify_error(value) -> str:
    text = error_text(value)
    if any(marker in text for marker in ACCOUNT_ERROR_MARKERS):
        return "account"
    if any(marker in text for marker in MAILBOX_ERROR_MARKERS):
        return "mailbox"
    if any(marker in text for marker in NETWORK_ERROR_MARKERS):
        return "network"
    if any(marker in text for marker in AUTH_STATE_ERROR_MARKERS):
        return "auth_state"
    if any(marker in text for marker in RATE_LIMIT_MARKERS):
        return "rate_limit"
    return "unknown"


# ── 错误分类常量结束 ──
FLOW_OTP = "email_otp_validate"
FLOW_CREATE = "create_account"

MAIL_API = "https://app.98faka.top"


# ── 状态机枚举 ──────────────────────────────────────────


class RegistrationState(enum.Enum):
    """注册流程状态机阶段枚举。

    两种账号形态共用同一状态集，但路径不同：

    create-account（新号）:
        AUTHORIZED → USER_REGISTER → EMAIL_OTP_SEND → EMAIL_OTP_WAIT →
        EMAIL_OTP_VALIDATE → (CREATE_ACCOUNT) → EXCHANGE_TOKEN → COMPLETED

    log-in（半成品/已存在）:
        AUTHORIZED → LOGIN_OTP_TRIGGER → EMAIL_OTP_WAIT →
        EMAIL_OTP_VALIDATE → (CREATE_ACCOUNT) → EXCHANGE_TOKEN → COMPLETED

    任何阶段均可转移至 FAILED。
    """
    AUTHORIZED = "authorized"
    USER_REGISTER = "user_register"
    EMAIL_OTP_SEND = "email_otp_send"
    LOGIN_OTP_TRIGGER = "login_otp_trigger"
    EMAIL_OTP_WAIT = "email_otp_wait"
    EMAIL_OTP_VALIDATE = "email_otp_validate"
    CREATE_ACCOUNT = "create_account"
    EXCHANGE_TOKEN = "exchange_token"
    COMPLETED = "completed"
    FAILED = "failed"


# 合法状态转移表（FAILED 可从任何状态到达）
_TRANSITIONS: dict[RegistrationState, set[RegistrationState]] = {
    RegistrationState.AUTHORIZED: {
        RegistrationState.USER_REGISTER,
        RegistrationState.LOGIN_OTP_TRIGGER,
        RegistrationState.FAILED,
    },
    RegistrationState.USER_REGISTER: {
        RegistrationState.EMAIL_OTP_SEND,
        RegistrationState.FAILED,
    },
    RegistrationState.EMAIL_OTP_SEND: {
        RegistrationState.EMAIL_OTP_WAIT,
        RegistrationState.FAILED,
    },
    RegistrationState.LOGIN_OTP_TRIGGER: {
        RegistrationState.EMAIL_OTP_WAIT,
        RegistrationState.FAILED,
    },
    RegistrationState.EMAIL_OTP_WAIT: {
        RegistrationState.EMAIL_OTP_VALIDATE,
        RegistrationState.FAILED,
    },
    RegistrationState.EMAIL_OTP_VALIDATE: {
        RegistrationState.CREATE_ACCOUNT,
        RegistrationState.EXCHANGE_TOKEN,
        RegistrationState.FAILED,
    },
    RegistrationState.CREATE_ACCOUNT: {
        RegistrationState.EXCHANGE_TOKEN,
        RegistrationState.FAILED,
    },
    RegistrationState.EXCHANGE_TOKEN: {
        RegistrationState.COMPLETED,
        RegistrationState.FAILED,
    },
    RegistrationState.COMPLETED: set(),
    RegistrationState.FAILED: set(),
}


class RegistrationStateMachine:
    """注册状态机 — 追踪阶段转移、校验合法性、记录历史。

    用法::

        sm = RegistrationStateMachine()
        sm.transition(RegistrationState.USER_REGISTER, "开始设置密码")
        # ... 执行业务逻辑 ...
        sm.transition(RegistrationState.FAILED, "密码设置失败")
        print(sm.snapshot())
    """

    def __init__(self) -> None:
        self.current = RegistrationState.AUTHORIZED
        self.history: list[dict[str, Any]] = []

    def transition(self, state: RegistrationState, detail: str = "") -> None:
        """转移到目标状态，校验合法性并记录历史。

        Raises:
            ValueError: 如果转移非法（不在 _TRANSITIONS 表中）。
        """
        allowed = _TRANSITIONS.get(self.current, set())
        if state not in allowed:
            raise ValueError(
                f"非法状态转移: {self.current.value} -> {state.value} "
                f"(允许: {[s.value for s in allowed]})"
            )
        self.history.append({
            "from": self.current.value,
            "to": state.value,
            "detail": detail,
            "at": time.time(),
        })
        self.current = state

    def fail(self, detail: str = "") -> None:
        """转移到 FAILED 终态（幂等）。"""
        if self.current == RegistrationState.FAILED:
            return
        self.history.append({
            "from": self.current.value,
            "to": RegistrationState.FAILED.value,
            "detail": detail,
            "at": time.time(),
        })
        self.current = RegistrationState.FAILED

    def snapshot(self) -> dict[str, Any]:
        """返回可审计/可调试的状态机快照。"""
        return {
            "current": self.current.value,
            "history": list(self.history),
        }


# ── 注册上下文 ──────────────────────────────────────────


@dataclass
class RegistrationContext:
    """单次注册的上下文 — 所有可变状态集中管理。

    每个字段在一次注册流程中确定后不再变更（不可变语义），
    状态机各阶段通过此对象传递数据，而非通过 result dict 传递。
    """
    email: str = ""
    password: str = ""
    client_id: str = ""
    refresh_token: str = ""
    card: dict = field(default_factory=dict)
    proxy_url: str = ""
    fingerprint: str = ""
    fp_ua: str = ""
    device_id: str = ""
    verifier: str = ""          # PKCE code_verifier（用于换 token）
    challenge: str = ""         # PKCE code_challenge（用于 authorize）
    session: Any = None          # curl_cffi Session
    name: str = ""
    birthdate: str = ""
    openai_password: str = ""
    otp_trigger: dt.datetime = field(default_factory=dt.datetime.now)
    otp_code: str = ""           # 验证码（从邮件取到后暂存）
    continue_url: str = ""       # OAuth 后续跳转 URL
    flow_kind: str = ""          # "create-account" 或 "log-in"
    result: dict = field(default_factory=dict)  # 累积结果字典
    state_machine: RegistrationStateMachine = field(default_factory=RegistrationStateMachine)
    # v4.0 P0-2：指纹地理联动——按出口 IP 国家生成的浏览器画像
    geo_country: str = "US"
    geo_lang: str = "en-US"          # 主语言（如 ja-JP）
    geo_lang_full: str = "en-US,en;q=0.9"  # Accept-Language 全量
    geo_timezone: str = "America/New_York"  # IANA 时区


# ── 工具函数 ──────────────────────────────────────────


def gen_password(length: int = 16) -> str:
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(length))


def _as_int(value: Any, default: int) -> int:
    """防御式整数解析：settings API 存字符串，非法/空回退默认。"""
    if value is None or value == "":
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _gen_pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _build_authorize_url(email: str, challenge: str) -> str:
    # PKCE 关键：challenge 必须与后续 _exchange_code 用的 verifier 来自同一对，
    # 否则 sha256(verifier) != challenge，OpenAI 换 token 必失败（v3.1 审计修复：
    # 原实现此处独立 _gen_pkce() 丢弃 verifier，与 _register_sync 的 verifier 不是同一对）。
    params = {
        "issuer": AUTH_BASE,
        "client_id": OAUTH_CLIENT_ID,
        "audience": OAUTH_AUDIENCE,
        "redirect_uri": REDIRECT_URI,
        "device_id": uuid.uuid4().hex,
        "screen_hint": "login_or_signup",
        "max_age": "0",
        "login_hint": email,
        "scope": "openid profile email offline_access",
        "response_type": "code",
        "response_mode": "query",
        "state": f"{secrets.token_hex(16)}.{secrets.token_urlsafe(16)}",
        "nonce": secrets.token_urlsafe(32),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "auth0Client": AUTH0_CLIENT,
    }
    return f"{AUTH_BASE}/api/accounts/authorize?{urlencode(params)}"


def _extract_code(continue_url: str) -> str:
    m = re.search(r"[?&]code=([^&]+)", continue_url or "")
    return m.group(1) if m else ""


# ── 主注册类 ──────────────────────────────────────────


class ProtocolRegister:
    """curl_cffi + sentinel 纯协议注册。"""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.ua = config.get("user_agent", USER_AGENT)
        self.otp_timeout = _as_int(config.get("otp_wait_timeout_sec"), 600)
        self.otp_poll = _as_int(config.get("otp_poll_interval_sec"), 5)
        self.otp_min_age_window_sec = _as_int(config.get("otp_min_age_window_sec"), 120)
        self.otp_fallback_after_sec = _as_int(config.get("otp_fallback_after_sec"), 40)
        self.otp_backfill_window_min = _as_int(config.get("otp_backfill_window_min"), 15)
        # 固定代理覆盖项：设置后所有账号共用该出口（优先级高于代理池）
        self.proxy_url = config.get("proxy_url") or ""
        self.mail_api = (config.get("email_api_base") or MAIL_API).rstrip("/")
        # 一账号一指纹：不在实例存可变指纹（单例并发注册会竞态），改为每次注册按需生成
        # （_register_sync 开头 _fresh_fingerprint 选一次，线程局部贯穿全流程，不同账号不同指纹）。
        # v3.4 T80：注册专用线程池（由 register_engine 注入；None 时 run_in_executor 走默认池，向后兼容）。
        self._executor = None
        # 真实 sentinel SDK 求解（quickjs）：优先于合成 PoW，JS/PoW 失败自动降级合成。
        # 需要 node 可执行文件 + 脚本存在 + config.sentinel_quickjs 开启（默认开）。
        self._quickjs = False
        try:
            qj = str(config.get("sentinel_quickjs") or "true").strip().lower()
            self._quickjs = qj not in ("0", "false", "off", "no") and node_available() and quickjs_script_available()
        except Exception:
            self._quickjs = False

    def _fresh_fingerprint(self) -> tuple[str, str]:
        """为一次注册生成 (TLS指纹, 配套UA)。每账号调用一次 → 一账号一指纹。

        UA：用户显式配 user_agent 则用它（向后兼容）；否则跟随指纹（防指纹/UA 矛盾被风控识别）。
        """
        from services.constants import pick_fingerprint, ua_for_fingerprint
        fp = pick_fingerprint(self.config)
        if str(self.config.get("user_agent") or "").strip():
            return fp, self.ua
        return fp, ua_for_fingerprint(fp)

    def _resolve_proxy(self) -> str:
        """每账号解析出口代理：config.proxy_url 固定优先 → use_proxy 时代理池取下一个 → 直连。

        与 token_refresher._resolve_proxy 口径一致。kookeey 动态住宅每次 get_next
        生成随机 session = 新出口 IP，实现「一账号一 IP」；通用 HTTP 代理按行轮询。
        """
        if self.proxy_url:
            return self.proxy_url
        use_proxy = self.config.get("use_proxy")
        if use_proxy is True or (isinstance(use_proxy, str) and use_proxy.strip().lower() in ("1", "true", "yes", "on")):
            from services.proxy_service import proxy_service
            return proxy_service.get_next() or ""
        return ""

    # ── 对外异步入口 ──────────────────────────────

    async def register_one(self, email: str, password: str, client_id: str,
                           refresh_token: str) -> dict[str, Any]:
        loop = asyncio.get_event_loop()
        # v3.4 T80：优先用 register_engine 注入的注册专用线程池（有界、大小=并发数），
        # 未注入（独立脚本/测试直调）时回退默认池，向后兼容。
        return await loop.run_in_executor(
            self._executor, self._register_sync, email, password, client_id, refresh_token
        )

    # ── 工具 ─────────────────────────────────────

    def _make_session(self, proxy_url: str = "", fingerprint: str = "chrome") -> Any:
        from curl_cffi import requests as cffi

        # 一账号一指纹：用本账号选定的 TLS 指纹（非固定 chrome），防批量注册 JA3 聚类
        session = cffi.Session(impersonate=fingerprint, verify=tls_verify_enabled(self.config), timeout=40)
        if proxy_url:
            session.proxies = {"http": proxy_url, "https": proxy_url}
        return session

    def _sentinel_headers(self, session: Any, device_id: str, flow: str, ua: str = "",
                          lang: str = "", lang_full: str = "", timezone: str = "") -> dict:
        """生成 sentinel 请求头。

        quickjs（真实 sdk.js）优先：sdk_token 直接作为 openai-sentinel-token，
        so_token 放 openai-sentinel-so-token（服务端要求时）。
        语言/时区等浏览器画像传给 sdk.js 的 navigator，保持与 UA 一致（v4.0 P0-2）。
        链路级网络/TLS 瞬断（QuickJSNetworkError）上抛——它是可恢复的网络问题，
        走上层 network 分类/降级，而不是降级合成（合成照样过不了服务端深度校验）。
        其余 JS/PoW 失败自动降级合成 PoW（开发环境无 node 时也能跑）。
        """
        if self._quickjs:
            try:
                res = get_sentinel_token_via_quickjs(
                    session, device_id, flow=flow, user_agent=ua or self.ua,
                    lang=lang or "", lang_full=lang_full or "", timezone=timezone or "",
                )
                if res:
                    sdk_token, so_token = res
                    headers = {"openai-sentinel-token": sdk_token}
                    if so_token:
                        headers["openai-sentinel-so-token"] = so_token
                    return headers
                logger.debug("Sentinel QuickJS 返回空，降级合成 PoW (flow=%s)", flow)
            except QuickJSNetworkError:
                raise
            except Exception as e:
                logger.debug("Sentinel QuickJS 异常，降级合成 PoW (flow=%s): %s", flow, e)
        # 合成路径（原有）：JSON sentinel value + oai-sc cookie
        sentinel, oai_sc = build_sentinel_token(session, device_id, flow, user_agent=ua or self.ua)
        if oai_sc:
            session.cookies.set("oai-sc", oai_sc, domain=".openai.com")
        return {"openai-sentinel-token": sentinel}

    def _base_headers(self, referer: str, device_id: str, ua: str = "", accept_lang: str = "") -> dict:
        # v4.0 P0-2：Accept-Language 跟随出口 IP 国家（不硬编码 zh-CN，
        # 否则「IP 美国、语言中文」是批量注册的明显异常信号）
        return {
            "accept": "application/json",
            "accept-language": accept_lang or "en-US,en;q=0.9",
            "content-type": "application/json",
            "referer": referer,
            "user-agent": ua or self.ua,
            "oai-device-id": device_id,
        }

    # 98faka 同步取码（协议流程全程同步，无法用异步 email_service）
    def _mail_api(self, path: str, payload: dict, proxy_url: str = "") -> dict:
        resp = requests.post(
            self.mail_api + path,
            json=payload,
            proxies={"http": proxy_url, "https": proxy_url} if proxy_url else None,
            headers={"content-type": "application/json", "user-agent": self.ua},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def _is_otp_mail(self, mail: dict) -> bool:
        subj = mail.get("subject") or ""
        frm = (mail.get("from_address") or "").lower()
        return "openai" in frm or "chatgpt" in subj.lower() or "验证码" in subj

    def _mail_time(self, mail: dict) -> dt.datetime:
        try:
            raw = (mail.get("received_time") or mail.get("date") or "").strip()
            d = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if d.tzinfo is not None:
                d = d.astimezone().replace(tzinfo=None)
            return d
        except Exception:
            return dt.datetime.fromtimestamp(0)

    def _code_from(self, card: dict, mail: dict, proxy_url: str = "") -> str | None:
        try:
            body = self._mail_api("/api/email-body", {
                "email": card["email"],
                "message_id": mail["id"],
                "client_id": card["client_id"],
                "refresh_token": card["refresh_token"],
            }, proxy_url)
            html = body.get("body_html") or ""
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
            if not text:
                text = body.get("body_preview") or ""
            nums = re.findall(r"\b(\d{6})\b", text or "")
            if not nums:
                return None
            for m in re.finditer(r"(?:code|码|mã|verification)[\s:：\-]{0,4}(\d{6})", text, re.I):
                return m.group(1)
            return nums[-1]
        except Exception:
            return None

    def _fetch_newest_otp(self, card: dict, after: dt.datetime, proxy_url: str = "") -> str | None:
        """取 after 时间之后收到的【最新】验证码（可被测试 monkeypatch）。"""
        payload = {
            "email": card["email"], "password": card["password"],
            "client_id": card["client_id"], "refresh_token": card["refresh_token"],
            "folder": "inbox",
        }
        deadline = time.time() + self.otp_timeout
        t0 = time.time()
        consecutive_failures = 0
        while time.time() < deadline:
            try:
                mails = self._mail_api("/api/emails", payload, proxy_url).get("data") or []
                consecutive_failures = 0
                window = dt.timedelta(seconds=self.otp_min_age_window_sec)
                cands = [m for m in mails if self._is_otp_mail(m)
                         and self._mail_time(m) >= after - window]
                if cands:
                    newest = max(cands, key=self._mail_time)
                    code = self._code_from(card, newest, proxy_url)
                    if code:
                        return code
                if not cands and time.time() - t0 > self.otp_fallback_after_sec:
                    recent = [m for m in mails if self._is_otp_mail(m)
                              and (dt.datetime.now() - self._mail_time(m))
                              < dt.timedelta(minutes=self.otp_backfill_window_min)]
                    if recent:
                        newest = max(recent, key=self._mail_time)
                        code = self._code_from(card, newest, proxy_url)
                        if code:
                            return code
            except Exception as e:
                consecutive_failures += 1
                # 邮件 API 连续失败：指数退避，仅对持续故障提前结束（瞬时抖动/429 不误判为超时）
                if consecutive_failures >= 6:
                    return None
                time.sleep(min(self.otp_poll * (2 ** min(consecutive_failures - 1, 3)), 20))
                continue
            time.sleep(self.otp_poll)
        return None

    def _exchange_code(self, code: str, verifier: str, proxy_url: str = "",
                       fingerprint: str = "chrome", ua: str = "") -> dict | None:
        """code + verifier → access/refresh/id 三件套（可被测试 monkeypatch）。"""
        from curl_cffi import requests as cffi

        # 与 _make_session 同一指纹：换 token 与 authorize 同账号同一 TLS 指纹
        session = cffi.Session(impersonate=fingerprint, proxy=proxy_url or None,
                               verify=tls_verify_enabled(self.config), timeout=60)
        try:
            r = session.post(
                f"{AUTH_BASE}/api/accounts/oauth/token",
                headers={
                    "accept": "application/json", "content-type": "application/json",
                    "origin": "https://platform.openai.com",
                    "referer": "https://platform.openai.com/",
                    "auth0-client": AUTH0_CLIENT, "user-agent": ua or self.ua,
                },
                json={
                    "client_id": OAUTH_CLIENT_ID, "code_verifier": verifier,
                    "grant_type": "authorization_code", "code": code,
                    "redirect_uri": REDIRECT_URI,
                },
            )
            data = r.json() if r.text else {}
            if r.status_code == 200 and data.get("access_token"):
                return {
                    "access_token": data["access_token"],
                    "refresh_token": data.get("refresh_token", ""),
                    "id_token": data.get("id_token", ""),
                }
            return None
        except Exception:
            return None
        finally:
            session.close()

    # ── 状态机阶段方法 ────────────────────────────

    def _stage_authorize(self, ctx: RegistrationContext) -> RegistrationState:
        """阶段 1：authorize，建立会话并识别账号形态。"""
        ctx.session = self._make_session(ctx.proxy_url, ctx.fingerprint)
        ctx.session.cookies.set("oai-did", ctx.device_id, domain=".auth.openai.com")

        r = ctx.session.get(
            _build_authorize_url(ctx.email, ctx.challenge),
            headers={
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "user-agent": ctx.fp_ua,
                "upgrade-insecure-requests": "1",
            },
            allow_redirects=True,
        )
        final = str(r.url)
        if r.status_code not in (200, 302):
            ctx.result["error"] = f"authorize HTTP {r.status_code}"
            ctx.result["fallback_browser"] = True
            ctx.result["failure_type"] = "server_5xx" if r.status_code >= 500 else "network"
            return RegistrationState.FAILED

        if "create-account" in final:
            ctx.flow_kind = "create-account"
            return RegistrationState.USER_REGISTER
        elif "log-in" in final:
            ctx.flow_kind = "log-in"
            return RegistrationState.LOGIN_OTP_TRIGGER
        else:
            ctx.result["error"] = f"authorize 未识别形态: {final.split('?')[0][-60:]}"
            ctx.result["fallback_browser"] = True
            ctx.result["failure_type"] = "unknown"
            return RegistrationState.FAILED

    def _stage_user_register(self, ctx: RegistrationContext) -> RegistrationState:
        """阶段 2a：create-account — 设密码。"""
        ctx.openai_password = gen_password()
        h = self._base_headers(f"{AUTH_BASE}/create-account/password", ctx.device_id, ctx.fp_ua, ctx.geo_lang_full)
        h.update(self._sentinel_headers(ctx.session, ctx.device_id, FLOW_REGISTER, ctx.fp_ua,
                                        ctx.geo_lang, ctx.geo_lang_full, ctx.geo_timezone))
        r2 = ctx.session.post(f"{AUTH_BASE}/api/accounts/user/register",
                              json={"password": ctx.openai_password, "username": ctx.email},
                              headers=h)
        j2 = r2.json() if r2.text else {}
        if r2.status_code != 200 or not (j2.get("continue_url") or ""):
            ctx.result["error"], ctx.result["fallback_browser"], ctx.result["failure_type"] = \
                self._classify_failure(r2, j2, "user/register")
            return RegistrationState.FAILED
        return RegistrationState.EMAIL_OTP_SEND

    def _stage_email_otp_send(self, ctx: RegistrationContext) -> RegistrationState:
        """阶段 2b：触发 OTP 发送（create-account 路径）。"""
        ctx.otp_trigger = dt.datetime.now()
        r_send = ctx.session.get(f"{AUTH_BASE}/api/accounts/email-otp/send",
                                 allow_redirects=False,
                                 headers=self._base_headers(
                                     f"{AUTH_BASE}/create-account/password",
                                     ctx.device_id, ctx.fp_ua, ctx.geo_lang_full,
                                 ))
        if r_send.status_code != 200:
            ctx.result["error"] = f"email-otp/send HTTP {r_send.status_code}"
            ctx.result["fallback_browser"] = True
            ctx.result["failure_type"] = "server_5xx" if r_send.status_code >= 500 else "network"
            return RegistrationState.FAILED
        return RegistrationState.EMAIL_OTP_WAIT

    def _stage_login_otp_trigger(self, ctx: RegistrationContext) -> RegistrationState:
        """阶段 2c：log-in — passwordless 触发 OTP。"""
        ctx.otp_trigger = dt.datetime.now()
        try:
            h = self._base_headers(f"{AUTH_BASE}/log-in/password", ctx.device_id, ctx.fp_ua, ctx.geo_lang_full)
            r2 = ctx.session.post(f"{AUTH_BASE}/api/accounts/passwordless/send-otp",
                                  json={"username": ctx.email}, headers=h)
            if r2.status_code != 200:
                ctx.result["error"] = f"passwordless/send-otp HTTP {r2.status_code}"
                ctx.result["fallback_browser"] = True
                ctx.result["failure_type"] = "server_5xx" if r2.status_code >= 500 else "network"
                return RegistrationState.FAILED
        except Exception as e:
            ctx.result["error"] = f"passwordless/send-otp 异常: {e}"
            ctx.result["fallback_browser"] = True
            ctx.result["failure_type"] = "network"
            return RegistrationState.FAILED
        return RegistrationState.EMAIL_OTP_WAIT

    def _stage_email_otp_wait(self, ctx: RegistrationContext) -> RegistrationState:
        """阶段 3：取本次触发的最新验证码。"""
        code = self._fetch_newest_otp(ctx.card, ctx.otp_trigger, ctx.proxy_url)
        if not code:
            ctx.result["error"] = "验证码等待超时"
            ctx.result["fallback_browser"] = True
            ctx.result["failure_type"] = "otp_timeout"
            return RegistrationState.FAILED
        ctx.otp_code = code
        return RegistrationState.EMAIL_OTP_VALIDATE

    def _stage_email_otp_validate(self, ctx: RegistrationContext) -> RegistrationState:
        """阶段 4：email-otp/validate。"""
        h = self._base_headers(f"{AUTH_BASE}/email-verification", ctx.device_id, ctx.fp_ua, ctx.geo_lang_full)
        h.update(self._sentinel_headers(ctx.session, ctx.device_id, FLOW_OTP, ctx.fp_ua,
                                        ctx.geo_lang, ctx.geo_lang_full, ctx.geo_timezone))
        r3 = ctx.session.post(f"{AUTH_BASE}/api/accounts/email-otp/validate",
                              json={"code": ctx.otp_code}, headers=h)
        j3 = r3.json() if r3.text else {}
        ctx.continue_url = j3.get("continue_url") or ""
        if r3.status_code != 200 or not ctx.continue_url:
            ctx.result["error"], ctx.result["fallback_browser"], ctx.result["failure_type"] = \
                self._classify_failure(r3, j3, "email-otp/validate")
            return RegistrationState.FAILED

        if "about-you" in ctx.continue_url:
            return RegistrationState.CREATE_ACCOUNT
        return RegistrationState.EXCHANGE_TOKEN

    def _stage_create_account(self, ctx: RegistrationContext) -> RegistrationState:
        """阶段 5：about-you → create_account。"""
        h = self._base_headers(f"{AUTH_BASE}/about-you", ctx.device_id, ctx.fp_ua, ctx.geo_lang_full)
        h.update(self._sentinel_headers(ctx.session, ctx.device_id, FLOW_CREATE, ctx.fp_ua,
                                        ctx.geo_lang, ctx.geo_lang_full, ctx.geo_timezone))
        r4 = ctx.session.post(f"{AUTH_BASE}/api/accounts/create_account",
                              json={"name": ctx.name, "birthdate": ctx.birthdate}, headers=h)
        j4 = r4.json() if r4.text else {}
        ctx.continue_url = j4.get("continue_url") or ""
        if r4.status_code != 200 or not _extract_code(ctx.continue_url):
            ctx.result["error"], ctx.result["fallback_browser"], ctx.result["failure_type"] = \
                self._classify_failure(r4, j4, "create_account")
            return RegistrationState.FAILED
        return RegistrationState.EXCHANGE_TOKEN

    def _stage_exchange_token(self, ctx: RegistrationContext) -> RegistrationState:
        """阶段 6：用 OAuth 授权码换三件套。"""
        cb_code = _extract_code(ctx.continue_url)
        if not cb_code:
            ctx.result["error"] = "未拿到 OAuth 授权码"
            ctx.result["fallback_browser"] = True
            ctx.result["failure_type"] = "network"
            return RegistrationState.FAILED

        tokens = self._exchange_code(
            cb_code, ctx.verifier, ctx.proxy_url, ctx.fingerprint, ctx.fp_ua,
        )
        if not tokens or not tokens.get("access_token"):
            ctx.result["error"] = "code 换 token 失败"
            ctx.result["fallback_browser"] = True
            ctx.result["failure_type"] = "network"
            return RegistrationState.FAILED

        ctx.result.update(tokens)
        ctx.result["openai_password"] = ctx.openai_password
        ctx.result["status"] = "success"
        return RegistrationState.COMPLETED

    # ── 主流程（同步，状态机驱动） ──────────────────

    def _register_sync(self, email: str, password: str, client_id: str,
                       refresh_token: str) -> dict[str, Any]:
        from services.proxy_service import proxy_service

        card = {"email": email, "password": password,
                "client_id": client_id, "refresh_token": refresh_token}
        # 每账号解析一次出口代理并贯穿全流程（authorize/取码/换 token 同一出口 IP，
        # 防中途换 IP 触发风控）；kookeey 动态住宅每账号新 IP。
        proxy_url = self._resolve_proxy()
        # v4.0 P0-2：指纹地理联动——按出口 IP 国家生成语言/时区画像。
        # config.geo_country 显式覆盖；否则读 kookeey 代理行的国家（get_next 已更新 last_country）。
        geo_country = str(self.config.get("geo_country") or "").strip()
        if not geo_country and proxy_url:
            from services.proxy_service import proxy_service as _ps
            geo_country = _ps.last_country
        geo = country_locale(geo_country or "US")
        # 一账号一指纹：本账号选一次 TLS 指纹 + 配套 UA，贯穿全流程（authorize/取码/换token），
        # 不同账号不同指纹（配合每账号独立 IP → 一账号一指纹一 IP，网络层+传输层双隔离）。
        fingerprint, fp_ua = self._fresh_fingerprint()
        result: dict[str, Any] = {
            "email": email, "status": "failed", "error": "",
            "access_token": "", "refresh_token": "", "id_token": "",
            "openai_password": "", "name": "", "birthdate": "",
            # 只存 host:port 展示格式，不把 kookeey 账密写进库（与浏览器路径一致）
            "proxy": proxy_service.format_for_display(proxy_url) if proxy_url else "直连",
            "fingerprint": fingerprint,
            "fallback_browser": False,
            "failure_type": "unknown",
        }
        device_id = uuid.uuid4().hex
        verifier, challenge = _gen_pkce()  # 同一对：challenge 进 authorize，verifier 换 token
        name = "".join(random.choices(string.ascii_lowercase, k=random.randint(6, 10)))
        year = random.randint(1986, 2006)
        birthdate = f"{year}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"
        result["name"] = name
        result["birthdate"] = birthdate

        # 初始化上下文
        ctx = RegistrationContext(
            email=email,
            password=password,
            client_id=client_id,
            refresh_token=refresh_token,
            card=card,
            proxy_url=proxy_url,
            fingerprint=fingerprint,
            fp_ua=fp_ua,
            device_id=device_id,
            verifier=verifier,
            challenge=challenge,
            name=name,
            birthdate=birthdate,
            result=result,
            geo_country=str(geo_country or "US"),
            geo_lang=geo["lang"],
            geo_lang_full=geo["lang_full"],
            geo_timezone=geo["timezone"],
        )

        # 状态机阶段映射
        stage_map: dict[RegistrationState, Any] = {
            RegistrationState.AUTHORIZED: self._stage_authorize,
            RegistrationState.USER_REGISTER: self._stage_user_register,
            RegistrationState.EMAIL_OTP_SEND: self._stage_email_otp_send,
            RegistrationState.LOGIN_OTP_TRIGGER: self._stage_login_otp_trigger,
            RegistrationState.EMAIL_OTP_WAIT: self._stage_email_otp_wait,
            RegistrationState.EMAIL_OTP_VALIDATE: self._stage_email_otp_validate,
            RegistrationState.CREATE_ACCOUNT: self._stage_create_account,
            RegistrationState.EXCHANGE_TOKEN: self._stage_exchange_token,
        }
        sm = ctx.state_machine

        try:
            # 状态机主循环：持续执行当前阶段直到终态
            while sm.current not in (RegistrationState.COMPLETED, RegistrationState.FAILED):
                stage_fn = stage_map.get(sm.current)
                if stage_fn is None:
                    sm.fail(f"未知阶段: {sm.current.value}")
                    break
                next_state = stage_fn(ctx)
                if next_state == RegistrationState.FAILED:
                    sm.fail()
                    break
                sm.transition(next_state, "阶段完成")
            return ctx.result
        except Exception as e:
            result["error"] = f"协议注册异常: {e}"
            result["fallback_browser"] = True
            result["failure_type"] = "network"
            return result
        finally:
            try:
                if ctx.session:
                    ctx.session.close()
            except Exception:
                pass

    @staticmethod
    def _classify_failure(resp: Any, data: dict, step: str) -> tuple[str, bool, str]:
        """把协议失败分类，返回 (错误信息, 是否可降级浏览器, 失败类型)。

        风控 / 限流 类错误浏览器也过不了，不降级（避免无谓重试触发限流）；
        OTP 超时类标记 otp_timeout；
        Cloudflare/turnstile 网络类标记 network，可降级浏览器兜底；
        服务器临时错误（>=500）或网络异常 → 可降级浏览器兜底。
        failure_type 取值: risk_control / otp_timeout / network / server_5xx / unknown
        """
        text = str(data)[:250]
        resp_text = getattr(resp, "text", None) or ""
        low = (text + resp_text).lower()
        msg = f"{step} 失败 HTTP {getattr(resp, 'status_code', '?')}: {text or ''}"

        # 1. OTP 超时类：invalid_or_expired_otp / invalid code / mailbox_otp_timeout → otp_timeout，不降级
        if any(k in low for k in ("invalid_or_expired_otp", "invalid code", "mailbox_otp_timeout")):
            return msg, False, "otp_timeout"

        # 2. 网络类：cloudflare / turnstile / 网络错误标记 → network，可降级浏览器兜底
        if any(k in low for k in ("cloudflare", "turnstile")):
            return msg, True, "network"
        if any(k in low for k in NETWORK_ERROR_MARKERS):
            return msg, True, "network"

        # 3. 风控限流类：不降级浏览器
        if any(k in low for k in (
            "deactivated", "registration_disallowed", "max_check_attempts",
            "rate_limit", "too_many", "invalid_grant", "invalid_auth_step",
            "fraud_guard",
        )):
            return msg, False, "risk_control"

        status = getattr(resp, "status_code", 0)
        if status >= 500:
            return msg, True, "server_5xx"
        return msg, False, "unknown"


protocol_register: ProtocolRegister | None = None


def get_protocol_register(config: dict[str, Any]) -> ProtocolRegister:
    global protocol_register
    if protocol_register is None:
        protocol_register = ProtocolRegister(config)
    return protocol_register