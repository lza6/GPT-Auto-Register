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
import hashlib
import json
import random
import re
import secrets
import string
import time
import uuid
from urllib.parse import parse_qs, urlencode, urlparse
from typing import Any

import requests

from services.sentinel import build_sentinel_token

# OpenAI OAuth 常量：收敛到 services/constants.py（B5），保留别名向后兼容
from services.constants import (
    OAUTH_CLIENT_ID,
    OAUTH_REDIRECT_URI as REDIRECT_URI,
    OAUTH_AUDIENCE,
    OAUTH_AUTH0_CLIENT as AUTH0_CLIENT,
    OAUTH_ISSUER as AUTH_BASE,
    tls_verify_enabled,
)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)

# 各端点对应的 sentinel flow（抓包确认）
FLOW_REGISTER = "username_password_create"
FLOW_OTP = "email_otp_validate"
FLOW_CREATE = "create_account"

MAIL_API = "https://app.98faka.top"
OTP_POLL_SEC = 4


def gen_password(length: int = 16) -> str:
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(length))


def _as_int(value, default: int) -> int:
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
        self.proxy_url = config.get("proxy_url") or ""
        self.mail_api = (config.get("email_api_base") or MAIL_API).rstrip("/")

    # ── 对外异步入口 ──────────────────────────────
    async def register_one(self, email: str, password: str, client_id: str,
                           refresh_token: str) -> dict[str, Any]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._register_sync, email, password, client_id, refresh_token
        )

    # ── 工具 ─────────────────────────────────────
    def _make_session(self) -> Any:
        from curl_cffi import requests as cffi

        session = cffi.Session(impersonate="chrome", verify=tls_verify_enabled(self.config), timeout=40)
        if self.proxy_url:
            session.proxies = {"http": self.proxy_url, "https": self.proxy_url}
        return session

    def _sentinel_headers(self, session, device_id: str, flow: str) -> dict:
        sentinel, oai_sc = build_sentinel_token(session, device_id, flow, user_agent=self.ua)
        if oai_sc:
            session.cookies.set("oai-sc", oai_sc, domain=".openai.com")
        return {"openai-sentinel-token": sentinel}

    def _base_headers(self, referer: str, device_id: str) -> dict:
        return {
            "accept": "application/json",
            "accept-language": "zh-CN,zh;q=0.9",
            "content-type": "application/json",
            "referer": referer,
            "user-agent": self.ua,
            "oai-device-id": device_id,
        }

    # 98faka 同步取码（协议流程全程同步，无法用异步 email_service）
    def _mail_api(self, path: str, payload: dict) -> dict:
        resp = requests.post(
            self.mail_api + path,
            json=payload,
            proxies={"http": self.proxy_url, "https": self.proxy_url} if self.proxy_url else None,
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

    def _code_from(self, card: dict, mail: dict) -> str | None:
        try:
            body = self._mail_api("/api/email-body", {
                "email": card["email"],
                "message_id": mail["id"],
                "client_id": card["client_id"],
                "refresh_token": card["refresh_token"],
            })
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

    def _fetch_newest_otp(self, card: dict, after: dt.datetime) -> str | None:
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
                mails = self._mail_api("/api/emails", payload).get("data") or []
                consecutive_failures = 0
                window = dt.timedelta(seconds=self.otp_min_age_window_sec)
                cands = [m for m in mails if self._is_otp_mail(m)
                         and self._mail_time(m) >= after - window]
                if cands:
                    newest = max(cands, key=self._mail_time)
                    code = self._code_from(card, newest)
                    if code:
                        return code
                if not cands and time.time() - t0 > self.otp_fallback_after_sec:
                    recent = [m for m in mails if self._is_otp_mail(m)
                              and (dt.datetime.now() - self._mail_time(m))
                              < dt.timedelta(minutes=self.otp_backfill_window_min)]
                    if recent:
                        newest = max(recent, key=self._mail_time)
                        code = self._code_from(card, newest)
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

    def _exchange_code(self, code: str, verifier: str) -> dict | None:
        """code + verifier → access/refresh/id 三件套（可被测试 monkeypatch）。"""
        from curl_cffi import requests as cffi

        session = cffi.Session(impersonate="chrome", proxy=self.proxy_url or None,
                               verify=tls_verify_enabled(self.config), timeout=60)
        try:
            r = session.post(
                f"{AUTH_BASE}/api/accounts/oauth/token",
                headers={
                    "accept": "application/json", "content-type": "application/json",
                    "origin": "https://platform.openai.com",
                    "referer": "https://platform.openai.com/",
                    "auth0-client": AUTH0_CLIENT, "user-agent": self.ua,
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

    # ── 主流程（同步） ────────────────────────────
    def _register_sync(self, email: str, password: str, client_id: str,
                       refresh_token: str) -> dict[str, Any]:
        card = {"email": email, "password": password,
                "client_id": client_id, "refresh_token": refresh_token}
        result: dict[str, Any] = {
            "email": email, "status": "failed", "error": "",
            "access_token": "", "refresh_token": "", "id_token": "",
            "openai_password": "", "name": "", "birthdate": "",
            "proxy": self.proxy_url or "直连", "fallback_browser": False,
            "failure_type": "unknown",
        }
        device_id = uuid.uuid4().hex
        verifier, challenge = _gen_pkce()  # 同一对：challenge 进 authorize，verifier 换 token
        name = "".join(random.choices(string.ascii_lowercase, k=random.randint(6, 10)))
        year = random.randint(1986, 2006)
        birthdate = f"{year}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"
        result["name"] = name
        result["birthdate"] = birthdate

        session = self._make_session()
        try:
            session.cookies.set("oai-did", device_id, domain=".auth.openai.com")

            # ── 1. authorize，建立会话并识别账号形态 ──
            r = session.get(
                _build_authorize_url(email, challenge),
                headers={
                    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "user-agent": self.ua,
                    "upgrade-insecure-requests": "1",
                },
                allow_redirects=True,
            )
            final = str(r.url)
            if r.status_code not in (200, 302):
                result["error"] = f"authorize HTTP {r.status_code}"
                result["fallback_browser"] = True  # 网络/服务问题 → 可降级
                result["failure_type"] = "server_5xx" if r.status_code >= 500 else "network"
                return result

            if "create-account" in final:
                flow_kind = "create-account"
            elif "log-in" in final:
                flow_kind = "log-in"
            else:
                result["error"] = f"authorize 未识别形态: {final.split('?')[0][-60:]}"
                result["fallback_browser"] = True
                result["failure_type"] = "unknown"
                return result

            otp_trigger = dt.datetime.now()
            openai_password = ""

            # ── 2a. create-account：设密码 ──
            if flow_kind == "create-account":
                openai_password = gen_password()
                h = self._base_headers(f"{AUTH_BASE}/create-account/password", device_id)
                h.update(self._sentinel_headers(session, device_id, FLOW_REGISTER))
                r2 = session.post(f"{AUTH_BASE}/api/accounts/user/register",
                                  json={"password": openai_password, "username": email},
                                  headers=h)
                j2 = r2.json() if r2.text else {}
                if r2.status_code != 200 or not (j2.get("continue_url") or ""):
                    result["error"], result["fallback_browser"], result["failure_type"] = \
                        self._classify_failure(r2, j2, "user/register")
                    return result
                r_send = session.get(f"{AUTH_BASE}/api/accounts/email-otp/send",
                                     allow_redirects=False,
                                     headers=self._base_headers(f"{AUTH_BASE}/create-account/password", device_id))
                if r_send.status_code != 200:
                    result["error"] = f"email-otp/send HTTP {r_send.status_code}"
                    result["fallback_browser"] = True
                    result["failure_type"] = "server_5xx" if r_send.status_code >= 500 else "network"
                    return result

            # ── 2b. log-in：passwordless 触发 OTP（半成品/已存在账号） ──
            else:
                try:
                    h = self._base_headers(f"{AUTH_BASE}/log-in/password", device_id)
                    r2 = session.post(f"{AUTH_BASE}/api/accounts/passwordless/send-otp",
                                      json={"username": email}, headers=h)
                    if r2.status_code != 200:
                        result["error"] = f"passwordless/send-otp HTTP {r2.status_code}"
                        result["fallback_browser"] = True
                        result["failure_type"] = "server_5xx" if r2.status_code >= 500 else "network"
                        return result
                except Exception as e:
                    result["error"] = f"passwordless/send-otp 异常: {e}"
                    result["fallback_browser"] = True
                    result["failure_type"] = "network"
                    return result

            # ── 3. 取本次触发的最新验证码 ──
            code = self._fetch_newest_otp(card, otp_trigger)
            if not code:
                result["error"] = "验证码等待超时"
                result["fallback_browser"] = True  # 邮箱/邮件通道异常可降级
                result["failure_type"] = "otp_timeout"
                return result

            # ── 4. email-otp/validate ──
            h = self._base_headers(f"{AUTH_BASE}/email-verification", device_id)
            h.update(self._sentinel_headers(session, device_id, FLOW_OTP))
            r3 = session.post(f"{AUTH_BASE}/api/accounts/email-otp/validate",
                              json={"code": code}, headers=h)
            j3 = r3.json() if r3.text else {}
            continue_url = j3.get("continue_url") or ""
            if r3.status_code != 200 or not continue_url:
                result["error"], result["fallback_browser"], result["failure_type"] = \
                    self._classify_failure(r3, j3, "email-otp/validate")
                return result

            # ── 5. about-you → create_account ──
            if "about-you" in continue_url:
                h = self._base_headers(f"{AUTH_BASE}/about-you", device_id)
                h.update(self._sentinel_headers(session, device_id, FLOW_CREATE))
                r4 = session.post(f"{AUTH_BASE}/api/accounts/create_account",
                                  json={"name": name, "birthdate": birthdate}, headers=h)
                j4 = r4.json() if r4.text else {}
                continue_url = j4.get("continue_url") or ""
                if r4.status_code != 200 or not _extract_code(continue_url):
                    result["error"], result["fallback_browser"], result["failure_type"] = \
                        self._classify_failure(r4, j4, "create_account")
                    return result

            cb_code = _extract_code(continue_url)
            if not cb_code:
                result["error"] = "未拿到 OAuth 授权码"
                result["fallback_browser"] = True
                result["failure_type"] = "network"
                return result

            # ── 6. 换三件套 ──
            tokens = self._exchange_code(cb_code, verifier)
            if not tokens or not tokens.get("access_token"):
                result["error"] = "code 换 token 失败"
                result["fallback_browser"] = True
                result["failure_type"] = "network"
                return result

            result.update(tokens)
            result["openai_password"] = openai_password
            result["status"] = "success"
            return result

        except Exception as e:
            result["error"] = f"协议注册异常: {e}"
            result["fallback_browser"] = True
            result["failure_type"] = "network"
            return result
        finally:
            try:
                session.close()
            except Exception:
                pass

    @staticmethod
    def _classify_failure(resp, data: dict, step: str) -> tuple[str, bool, str]:
        """把协议失败分类，返回 (错误信息, 是否可降级浏览器, 失败类型)。

        风控 / 限流 / OTP 类错误浏览器也过不了，不降级（避免无谓重试触发限流）；
        服务器临时错误（>=500）或网络异常 → 可降级浏览器兜底。
        failure_type 取值: risk_control / otp_timeout / network / server_5xx / unknown
        """
        text = str(data)[:250]
        low = (text + (getattr(resp, "text", None) or "")).lower()
        msg = f"{step} 失败 HTTP {getattr(resp, 'status_code', '?')}: {text or ''}"
        if any(k in low for k in (
            "deactivated", "registration_disallowed", "max_check_attempts",
            "invalid_or_expired_otp", "invalid code", "rate_limit", "too_many",
            "cloudflare", "turnstile",
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
