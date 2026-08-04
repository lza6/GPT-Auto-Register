#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
revive_import.py — 完成 OpenAI 账号注册(设密码) + 三件套导入 chatgpt2api
=======================================================================
设计原则(吸取前车之鉴):
  * 每账号: 只设一次密码、只触发一次 OTP、只取"本次触发后最新"的验证码、只提交一次。
  * 失败即换下一个, 绝不在同一账号上反复重试(会触发 email-otp max_check_attempts 限流)。
  * 跳过的账号写入 revive_skip.txt (邮箱每行一个), 主循环自动跳过。

流程:
  1. 自建 PKCE, 构造 authorize_url(login_hint=邮箱)
  2. 浏览器走代理: create-account/password → 设新密码 → 提交(触发 OTP)
  3. 等 OTP 页 → 98faka 取"触发后最新"验证码 → 填入单输入框 → 提交一次
  4. about-you → 填纯字母姓名+生日(19-40岁) → 提交
  5. 等 platform.openai.com/auth/callback?code=... → 自换 token
  6. POST /api/accounts 带 email+password 导入 chatgpt2api(支持自动重登)
  7. 密码记入 revive_passwords.txt (邮箱----密码)

用法:
  python revive_import.py                  # 全部(跳过 skip 清单)
  python revive_import.py --only xxx@outlook.com
  python revive_import.py --limit N
"""
import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import random
import re
import secrets
import string
import time
from urllib.parse import urlencode

import requests
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.abspath(__file__))
CARDS_FILE = os.path.join(ROOT, "本次一百个号.txt")
RESULTS_FILE = os.path.join(ROOT, "revive_results.jsonl")
PW_FILE = os.path.join(ROOT, "revive_passwords.txt")
SKIP_FILE = os.path.join(ROOT, "revive_skip.txt")
SHOTS_DIR = os.path.join(ROOT, "revive_shots")

C2API = "http://127.0.0.1:23456"
AUTH = "Bearer chatgpt2api"
PROXY = "http://127.0.0.1:10808"
MAIL_API = "https://app.98faka.top"

AUTH_BASE = "https://auth.openai.com"
OAUTH_CLIENT_ID = "app_2SKx67EdpoN0G6j64rFvigXD"
REDIRECT_URI = "https://platform.openai.com/auth/callback"
AUTH0_CLIENT = "eyJuYW1lIjoiYXV0aDAtc3BhLWpzIiwidmVyc2lvbiI6IjEuMjEuMCJ9"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")

OTP_POLL_SEC = 4
OTP_WAIT_MAX = 90          # 等验证码邮件
CB_WAIT_SEC = 50           # 提交后等 callback/about-you
CF_WAIT_MAX = 180
BETWEEN_ACCOUNT_SEC = (10, 20)
STEP_TO = 40000
HEADERS = {"Authorization": AUTH, "Content-Type": "application/json"}


class DeactivatedError(Exception):
    """账号被 OpenAI 停用(account_deactivated), 永久不可用, 不再重试."""


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def load_cards(path: str) -> list[dict]:
    cards = []
    for ln in open(path, encoding="utf-8", errors="replace"):
        ln = ln.strip()
        parts = ln.split("----")
        if len(parts) >= 4 and "@" in parts[0]:
            cards.append({"email": parts[0].strip(), "password": parts[1].strip(),
                          "client_id": parts[2].strip(),
                          "refresh_token": "----".join(parts[3:]).strip()})
    return cards


def load_skip() -> set[str]:
    s = set()
    if os.path.exists(SKIP_FILE):
        for ln in open(SKIP_FILE, encoding="utf-8"):
            if "@" in ln:
                s.add(ln.strip().lower())
    return s


def load_done() -> set[str]:
    s = set()
    if os.path.exists(RESULTS_FILE):
        for ln in open(RESULTS_FILE, encoding="utf-8"):
            try:
                r = json.loads(ln)
                if r.get("status") == "success" and r.get("email"):
                    s.add(r["email"].lower())
            except Exception:
                pass
    return s


def append_result(rec: dict) -> None:
    with open(RESULTS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def save_password(email: str, password: str) -> None:
    with open(PW_FILE, "a", encoding="utf-8") as f:
        f.write(f"{email}----{password}\n")


def load_passwords() -> dict[str, str]:
    m = {}
    if os.path.exists(PW_FILE):
        for ln in open(PW_FILE, encoding="utf-8"):
            parts = ln.strip().split("----")
            if len(parts) >= 2 and "@" in parts[0]:
                m[parts[0].lower()] = "----".join(parts[1:])
    return m


def gen_password() -> str:
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))


def gen_pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorize_url(email: str) -> tuple[str, str, str]:
    verifier, challenge = gen_pkce()
    params = {
        "issuer": AUTH_BASE, "client_id": OAUTH_CLIENT_ID,
        "audience": "https://api.openai.com/v1", "redirect_uri": REDIRECT_URI,
        "device_id": secrets.token_hex(16), "screen_hint": "login_or_signup",
        "max_age": "0", "login_hint": email,
        "scope": "openid profile email offline_access",
        "response_type": "code", "response_mode": "query",
        "state": f"{secrets.token_hex(16)}.{secrets.token_urlsafe(16)}",
        "nonce": secrets.token_urlsafe(32),
        "code_challenge": challenge, "code_challenge_method": "S256",
        "auth0Client": AUTH0_CLIENT,
    }
    return f"{AUTH_BASE}/api/accounts/authorize?{urlencode(params)}", verifier, params["state"]


def exchange_code(code: str, verifier: str) -> dict:
    from curl_cffi import requests as cffi
    session = cffi.Session(impersonate="chrome", proxy=PROXY, verify=False)
    try:
        r = session.post(f"{AUTH_BASE}/api/accounts/oauth/token",
                         headers={"accept": "application/json", "content-type": "application/json",
                                  "origin": "https://platform.openai.com",
                                  "referer": "https://platform.openai.com/",
                                  "auth0-client": AUTH0_CLIENT, "user-agent": USER_AGENT},
                         json={"client_id": OAUTH_CLIENT_ID, "code_verifier": verifier,
                               "grant_type": "authorization_code", "code": code,
                               "redirect_uri": REDIRECT_URI}, timeout=60)
        data = r.json() if r.text else {}
        if r.status_code != 200 or not data.get("access_token"):
            raise RuntimeError(f"换token失败 HTTP{r.status_code}: {str(data)[:300]}")
        return {"access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", ""),
                "id_token": data.get("id_token", "")}
    finally:
        session.close()


def import_account(payload: dict) -> dict:
    r = requests.post(C2API + "/api/accounts", json={"accounts": [payload]},
                      headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------- 代理池
def load_proxies(path: str) -> list[str]:
    """读取代理线路文件(kookeey格式: host:port:user:pass-geo) → 代理URL列表.

    每行一个, 格式: gate.kookeey.info:1000:1023701-4a2c845a:12843fee-US-会话
    → http://1023701-4a2c845a:12843fee-US-会话@gate.kookeey.info:1000
    """
    out = []
    for ln in open(path, encoding="utf-8", errors="replace"):
        ln = ln.strip()
        if not ln:
            continue
        parts = ln.split(":")
        if len(parts) >= 4 and parts[0]:
            host, port = parts[0], parts[1]
            user = ":".join(parts[2:-1])
            pw = parts[-1]
            out.append(f"http://{user}:{pw}@{host}:{port}")
        elif "@" in ln:  # 已是 URL 格式
            out.append(ln)
    return out


# ---------------------------------------------------------------- 98faka 取验证码
def _mail_api(path: str, payload: dict, proxy: str = "") -> dict:
    r = requests.post(MAIL_API + path, json=payload,
                      proxies={"http": proxy or PROXY, "https": proxy or PROXY},
                      headers={"content-type": "application/json", "origin": MAIL_API,
                               "referer": MAIL_API + "/", "user-agent": USER_AGENT},
                      timeout=30)
    r.raise_for_status()
    return r.json()


def _is_otp_mail(m: dict) -> bool:
    subj = m.get("subject") or ""
    frm = (m.get("from_address") or "").lower()
    return "openai" in frm or "chatgpt" in subj.lower() or "验证码" in subj


def _mail_time(m: dict) -> dt.datetime:
    try:
        raw = (m.get("received_time") or m.get("date") or "").strip()
        d = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if d.tzinfo is not None:
            d = d.astimezone().replace(tzinfo=None)  # 转本地 naive
        return d
    except Exception:
        return dt.datetime.fromtimestamp(0)


def fetch_newest_otp(card: dict, after: dt.datetime, proxy: str = "") -> str | None:
    """取 after 时间之后收到的【最新】验证码邮件里的 6 位数字.

    兜底: 若 after 后无新邮件, 且收件箱最新验证码邮件在 15 分钟内, 退回用它.
    """
    payload = {"email": card["email"], "password": card["password"],
               "client_id": card["client_id"], "refresh_token": card["refresh_token"],
               "folder": "inbox"}
    deadline = time.time() + OTP_WAIT_MAX
    t0 = time.time()
    last_candidates = []
    while time.time() < deadline:
        try:
            mails = ( _mail_api("/api/emails", payload, proxy).get("data") or [] )
            cands = [m for m in mails if _is_otp_mail(m) and _mail_time(m) >= after - dt.timedelta(seconds=8)]
            if cands:
                newest = max(cands, key=_mail_time)
                code = _code_from(card, newest, proxy)
                if code:
                    return code
            # 兜底(仅当 40s 后仍无触发后新邮件): 用最新的 15 分钟内邮件
            if not cands and time.time() - t0 > 40:
                recent = [m for m in mails if _is_otp_mail(m)
                          and (dt.datetime.now() - _mail_time(m)) < dt.timedelta(minutes=15)]
                if recent:
                    newest = max(recent, key=_mail_time)
                    code = _code_from(card, newest, proxy)
                    if code:
                        log(f"  40s 后仍无新邮件, 退回用 {_mail_time(newest):%H:%M:%S} 的码")
                        return code
            if cands:
                last_candidates = [(str(_mail_time(m)), m.get("subject")) for m in cands]
        except Exception as e:
            log(f"  取件异常: {e}")
        time.sleep(OTP_POLL_SEC)
    if last_candidates:
        log(f"  有候选邮件但未提取到码: {last_candidates[:3]}")
    return None


def _code_from(card: dict, mail: dict, proxy: str = "") -> str | None:
    try:
        body = _mail_api("/api/email-body", {"email": card["email"],
                                             "message_id": mail["id"],
                                             "client_id": card["client_id"],
                                             "refresh_token": card["refresh_token"]},
                         proxy)
        html = body.get("body_html") or ""
        text = re.sub(r"<[^>]+>", " ", html)          # 去掉标签(含属性里的假数字)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            text = body.get("body_preview") or ""
        nums = re.findall(r"\b(\d{6})\b", text or "")
        if not nums:
            return None
        # 优先 "code/码/mã" 附近的码, 否则取最后一个
        for m in re.finditer(r"(?:code|码|mã|verification)[\s:：\-]{0,4}(\d{6})", text, re.I):
            return m.group(1)
        return nums[-1]
    except Exception as e:
        log(f"  取正文异常: {e}")
        return None


# ---------------------------------------------------------------- 浏览器
def _is_cf(page) -> bool:
    url = page.url.lower()
    try:
        html = page.content()[:20000]
    except Exception:
        html = ""
    return ("challenge-platform" in url or "cf_chl" in url or "just a moment" in html.lower()
            or "正在检查你的浏览器" in html)


def _click(page, selectors: list[str]) -> bool:
    for s in selectors:
        try:
            el = page.locator(s).first
            if el.count() and el.is_visible():
                el.click(timeout=STEP_TO)
                return True
        except Exception:
            continue
    return False


def _fill(page, selector: str, value: str) -> bool:
    try:
        el = page.locator(selector).first
        if el.count() and el.is_visible():
            el.fill(value)
            return True
    except Exception:
        pass
    return False


def _has_input(page, selector: str) -> bool:
    try:
        return bool(page.locator(selector).first.count())
    except Exception:
        return False


def _handle_about_you(page, email: str) -> bool:
    """about-you: 填姓名 + 年龄(19-40数字), 点"创建账户", 让前端带 sentinel 发 create_account."""
    if "about-you" not in page.url:
        return False
    name = "".join(random.choices("abcdefghijklmnopqrstuvwxyz", k=random.randint(6, 10)))
    age = random.randint(19, 40)                     # 年龄 19-40
    log(f"  about-you 填 姓名={name} 年龄={age}")
    filled = False
    for s in ['input[name="name"]', 'input#name', 'input[placeholder*="姓名"]']:
        if _fill(page, s, name):
            filled = True
            break
    age_ok = _fill(page, 'input[name="age"]', str(age))
    if not (filled and age_ok):
        log("  姓名/年龄输入框未找到")
        page.screenshot(path=os.path.join(SHOTS_DIR, f"about_fail_{email}.png"))
        return False
    time.sleep(0.5)
    _click(page, ['button[type="submit"]', 'button:has-text("创建账户")',
                  'button:has-text("Continue")', 'button:has-text("继续")'])
    return True


def run_account(card: dict, browser, headless: bool, known_pw: str = "", proxy: str = "") -> tuple[str, str, str, str] | None:
    """返回 (callback_url, code, verifier, password_used); 失败 None。一次通过, 不重试。

    create-account/password → 设新密码(立即存盘) → OTP → about-you → callback
    log-in/password        → 用已知密码登录 → OTP(如需要) → callback
    proxy: 该账号专用代理(如 kookeey 粘性IP), 空则用全局 PROXY
    """
    email = card["email"]
    authorize_url, verifier, _state = build_authorize_url(email)
    ctx_kwargs = dict(user_agent=USER_AGENT, locale="zh-CN",
                      viewport={"width": 1280, "height": 900})
    if proxy:
        ctx_kwargs["proxy"] = {"server": proxy}
    ctx = browser.new_context(**ctx_kwargs)
    page = ctx.new_page()
    auth_resp: list[tuple[str, int, str]] = []

    def _on_response(r):
        try:
            if "auth.openai.com/api/accounts/" in r.url and r.request.method == "POST":
                body = ""
                try:
                    body = (r.text() or "")[:200]
                except Exception:
                    pass
                auth_resp.append((r.url.split("/api/accounts/")[1][:40], r.status, body))
        except Exception:
            pass
    page.on("response", _on_response)

    def _dump_auth():
        for u, st, b in auth_resp[-5:]:
            log(f"    resp {st} {u} {b.replace(chr(10),' ')[:160]}")
    try:
        page.goto(authorize_url, timeout=STEP_TO, wait_until="commit")
        page.wait_for_timeout(2000)

        for _ in range(CF_WAIT_MAX // 5):
            if not _is_cf(page):
                break
            log("  [!] CF 挑战中, 请在浏览器手动通过...")
            page.wait_for_timeout(5000)
        else:
            log("  CF 超时")
            return None

        otp_trigger = dt.datetime.now()
        password = ""
        about_done = False
        if "create-account" in page.url:
            password = gen_password()
            if not _fill(page, 'input[name="new-password"]', password):
                log("  create-account 页无密码输入框, 放弃")
                return None
            time.sleep(0.4)
            if not _click(page, ['button[type="submit"]']):
                log("  点击创建失败")
                return None
            save_password(email, password)          # 立即存盘, 防中途失败丢失
            log(f"  已设密码 {password}")
            otp_trigger = dt.datetime.now()
        elif "log-in" in page.url:
            password = known_pw or load_passwords().get(email.lower(), "")
            # 用"邮箱验证码登录"触发全新 OTP(半成品账号最可靠)
            if not _click(page, ['button:has-text("邮箱验证码")', 'button:has-text("验证码")',
                                 'button:has-text("email verification")']):
                if password:
                    if not _fill(page, 'input[name="current-password"]', password):
                        log("  log-in 页无密码框, 放弃")
                        return None
                    time.sleep(0.4)
                    if not _click(page, ['button[type="submit"]']):
                        log("  点击登录失败")
                        return None
                    log(f"  用已知密码登录 {password}")
                else:
                    log("  log-in 页既无验证码按钮也无密码, 跳过")
                    return None
            else:
                log("  点击邮箱验证码登录")
            otp_trigger = dt.datetime.now()
        else:
            log(f"  意外页面: {page.url[:90]}")
            page.screenshot(path=os.path.join(SHOTS_DIR, f"unexpected_{email}.png"))
            return None

        # ② 等 OTP 页(或直接 callback/about-you) → 取码 → 提交一次
        otp_seen = time.time() + 90
        forced = False
        while time.time() < otp_seen:
            if _has_input(page, 'input[name="code"]') or \
               _has_input(page, 'input[autocomplete="one-time-code"]'):
                break
            if "platform.openai.com/auth/callback" in page.url and "code=" in page.url:
                m = re.search(r"[?&]code=([^&]+)", page.url)
                if m:
                    return (page.url, m.group(1), verifier, password)
            if "about-you" in page.url and not about_done:
                about_done = True
                _handle_about_you(page, email)
            # 尝试点掉 Turnstile/CF 复选框(会挡住 OTP 输入框)
            try:
                for fr in page.frames:
                    if "turnstile" in (fr.url or "").lower() or "challenges.cloudflare" in (fr.url or "").lower():
                        try:
                            cb = fr.locator("#challenge-stage input[type=checkbox], input[type=checkbox]").first
                            if cb.count():
                                cb.click(timeout=1000)
                                log("  点了 Turnstile 复选框")
                        except Exception:
                            pass
            except Exception:
                pass
            # 15s 后仍未到 OTP 页, 强制导航 email-otp/send 再触发一次
            if not forced and time.time() > otp_seen - 75:
                forced = True
                log("  OTP 页未自动出现, 强制导航 email-otp/send")
                try:
                    page.goto("https://auth.openai.com/api/accounts/email-otp/send",
                              timeout=20000, wait_until="commit")
                except Exception:
                    pass
            page.wait_for_timeout(1500)
        else:
            log("  OTP 页未出现")
            page.screenshot(path=os.path.join(SHOTS_DIR, f"otp_no_{email}.png"))
            return None

        code = fetch_newest_otp(card, otp_trigger, proxy)
        if not code:
            log("  未取到本次验证码")
            _dump_auth()
            return None
        log(f"  用本次最新验证码 {code}")

        if not (_fill(page, 'input[name="code"]', code)
                or _fill(page, 'input[autocomplete="one-time-code"]', code)
                or _fill(page, 'input[inputmode="numeric"]', code)):
            log("  OTP 输入框未找到")
            return None
        time.sleep(0.5)
        _click(page, ['button[type="submit"]', 'button:has-text("Continue")',
                      'button:has-text("继续")', 'button:has-text("验证")'])

        # ③ 等 callback (或 about-you 走协议)
        deadline = time.time() + CB_WAIT_SEC
        while time.time() < deadline:
            # 检测账号被停用(account_deactivated)
            for u, st, b in auth_resp[-4:]:
                if st == 403 and ("deleted or deactivated" in b or "deactivated" in b.lower()):
                    raise DeactivatedError(f"account_deactivated: {email}")
            url = page.url
            if "platform.openai.com/auth/callback" in url and "code=" in url:
                m = re.search(r"[?&]code=([^&]+)", url)
                if m:
                    page.screenshot(path=os.path.join(SHOTS_DIR, f"cb_{email}.png"))
                    return (url, m.group(1), verifier, password)
            if "about-you" in url and not about_done:
                about_done = True
                _handle_about_you(page, email)
            page.wait_for_timeout(1500)
        log("  等待 callback 超时")
        _dump_auth()
        page.screenshot(path=os.path.join(SHOTS_DIR, f"cb_timeout_{email}.png"))
        return None
    finally:
        try:
            ctx.close()
        except Exception:
            pass


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="只跑指定邮箱")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--from", dest="from_idx", type=int, default=0,
                    help="账号区间起点(1-based, 含), 用于并发分片")
    ap.add_argument("--to", dest="to_idx", type=int, default=0,
                    help="账号区间终点(1-based, 含), 用于并发分片")
    ap.add_argument("--proxy", help="覆盖代理, 如 kookeey: http://1023701-4a2c845a:12843fee-US-会话@gate.kookeey.info:1000")
    ap.add_argument("--proxy-file", help="代理池文件(每行一个kookeey线路, 每账号轮询分配一条=一账号一IP)")
    args = ap.parse_args()

    global PROXY
    if args.proxy:
        PROXY = args.proxy
        log(f"代理切换为 {PROXY}")

    proxies = load_proxies(args.proxy_file) if args.proxy_file else []
    if proxies:
        log(f"代理池 {len(proxies)} 条, 每账号轮询分配")

    os.makedirs(SHOTS_DIR, exist_ok=True)
    cards = load_cards(CARDS_FILE)
    if args.from_idx:
        cards = cards[args.from_idx - 1: args.to_idx or len(cards)]
        log(f"分片区间 [{args.from_idx}..{args.to_idx or len(cards)}] 共 {len(cards)} 个")
    skip = load_skip()
    done = load_done()
    log(f"共 {len(cards)} 张卡, 已成功 {len(done)}, 跳过清单 {len(skip)}, 待处理 "
        f"{sum(1 for c in cards if c['email'].lower() not in done and c['email'].lower() not in skip)}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome", headless=args.headless, proxy={"server": PROXY},
            args=["--no-first-run", "--disable-blink-features=AutomationControlled"])
        ok = fail = 0
        try:
            for i, card in enumerate(cards, 1):
                email = card["email"]
                low = email.lower()
                if args.only and low != args.only.lower():
                    continue
                if low in done:
                    log(f"[{i}/{len(cards)}] 跳过(已成功) {email}")
                    continue
                if low in skip:
                    log(f"[{i}/{len(cards)}] 跳过(清单) {email}")
                    continue
                if args.limit and (ok + fail) >= args.limit:
                    break
                log(f"[{i}/{len(cards)}] 处理 {email}")
                card_proxy = proxies[(i - 1) % len(proxies)] if proxies else ""
                res = None
                for attempt in range(1, 3):          # 超时最多重试一次
                    try:
                        res = run_account(card, browser, args.headless, proxy=card_proxy)
                        break
                    except DeactivatedError:
                        with open(SKIP_FILE, "a", encoding="utf-8") as f:
                            f.write(f"{email}\n")
                        append_result({"email": email, "status": "dead",
                                       "ts": dt.datetime.now().isoformat()})
                        fail += 1
                        log(f"  [DEAD] 账号被OpenAI停用 {email}")
                        res = None
                        break
                    except Exception as e:
                        if attempt < 2 and "Timeout" in str(e):
                            log(f"  超时, 重试 {email}")
                            continue
                        append_result({"email": email, "status": "error", "error": str(e),
                                       "ts": dt.datetime.now().isoformat()})
                        fail += 1
                        log(f"  [FAIL] 异常 {email}: {e}")
                        res = None
                        break
                if res:
                    cb, code, verifier, password = res
                    tokens = exchange_code(code, verifier)
                    payload = {"email": email,
                               "source_type": "password" if password else "oauth_login"}
                    if password:
                        payload["password"] = password
                    tokens.update(payload)
                    imp = import_account(tokens)
                    append_result({"email": email, "status": "success",
                                   "added": imp.get("added", 0),
                                   "ts": dt.datetime.now().isoformat()})
                    ok += 1
                    log(f"  [OK] 成功 {email}")
                else:
                    append_result({"email": email, "status": "fail",
                                   "ts": dt.datetime.now().isoformat()})
                    fail += 1
                    log(f"  [FAIL] 失败 {email}")

                if i < len(cards) and not args.only:
                    d = random.randint(*BETWEEN_ACCOUNT_SEC)
                    log(f"  等待 {d}s...")
                    time.sleep(d)
        finally:
            browser.close()
    log(f"完成: 成功 {ok}, 失败 {fail} (密码在 revive_passwords.txt)")


if __name__ == "__main__":
    main()
