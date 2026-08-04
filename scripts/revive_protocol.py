#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
revive_protocol.py — 全协议(无浏览器)完成 OpenAI 账号注册/登录 + 导入 chatgpt2api
===============================================================================
协议(从浏览器真实抓包 protocol_capture.jsonl 提取):
  1. GET  /api/accounts/authorize            → 302 → create-account/password (建立会话cookie)
  2. POST /api/accounts/user/register        {password, username} +sentinel → continue_url=email-otp/send
  3. GET  /api/accounts/email-otp/send       → 触发验证码邮件
  4. POST /api/accounts/email-otp/validate   {code} +sentinel → about-you
  5. POST /api/accounts/create_account       {name, birthdate} +sentinel → callback?code=ac_...
  6. code → oauth/token → 三件套 → 导入 chatgpt2api(带email+password)

已设过密码的账号(log-in/password): 改用 POST /api/accounts/passwordless/send-otp 触发 OTP。

用法:
  python revive_protocol.py --only x@y.com    # 单个调试
  python revive_protocol.py --limit N
  python revive_protocol.py                    # 全部(跳过 skip/已成功)
"""
import argparse
import datetime as dt
import json
import os
import random
import re
import sys
import time
import uuid

import requests
from curl_cffi import requests as cffi

# 复用取码/换token/导入
import revive_import as R

# 引入 chatgpt2api 的 sentinel 构造器
_C2API_DIR = r"C:\Users\Administrator.DESKTOP-EGNE9ND\Desktop\私单\chatgpt2api"
sys.path.insert(0, _C2API_DIR)
from utils.sentinel import build_sentinel_token  # noqa: E402

AUTH_BASE = "https://auth.openai.com"
# 每个端点用各自正确的 sentinel flow(已抓包确认)
FLOW_REGISTER = "username_password_create"   # user/register
FLOW_OTP = "email_otp_validate"              # email-otp/validate
FLOW_CREATE = "create_account"              # create_account


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def _base_headers(referer: str, **extra) -> dict:
    h = {
        "accept": "application/json",
        "accept-language": "zh-CN,zh;q=0.9",
        "content-type": "application/json",
        "referer": referer,
        "user-agent": R.USER_AGENT,
    }
    h.update(extra)
    return h


def _sentinel_headers(session, device_id: str, flow: str) -> dict:
    sentinel, oai_sc = build_sentinel_token(session, device_id, flow)
    if oai_sc:
        session.cookies.set("oai-sc", oai_sc, domain=".openai.com")
    return {"openai-sentinel-token": sentinel}


def _extract_code(cu: str) -> str | None:
    m = re.search(r"[?&]code=([^&]+)", cu or "")
    return m.group(1) if m else None


def run_protocol(card: dict) -> tuple[str, str, str] | None:
    """返回 (callback_code, verifier, password); 失败 None。只走创建账号+密码路径。"""
    email = card["email"]
    password = R.gen_password()
    authorize_url, verifier, _state = R.build_authorize_url(email)
    device_id = uuid.uuid4().hex

    session = cffi.Session(impersonate="chrome", proxy=R.PROXY, verify=False)
    session.cookies.set("oai-did", device_id, domain=".auth.openai.com")
    try:
        # ── 1. authorize, 建立会话 ──────────────────────────
        r = session.get(authorize_url, headers={
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "user-agent": R.USER_AGENT, "sec-ch-ua": '"Chromium";v="150";"Not;A=Brand";v="8"',
            "sec-ch-ua-platform": '"Windows"', "upgrade-insecure-requests": "1",
        }, allow_redirects=True, timeout=40)
        final = str(r.url)
        log(f"  authorize -> {final.split('?')[0][-50:]} (HTTP {r.status_code})")
        if r.status_code not in (200, 302):
            log(f"  authorize 失败 HTTP {r.status_code}: {r.text[:200]}")
            return None
        if "create-account" not in final:
            log(f"  非创建账号页面: {final[:90]} (跳过)")
            return None

        # ── 2. user/register 设置密码 ───────────────────────
        h = _base_headers("https://auth.openai.com/create-account/password",
                          **{"oai-device-id": device_id})
        h.update(_sentinel_headers(session, device_id, FLOW_REGISTER))
        r2 = session.post(f"{AUTH_BASE}/api/accounts/user/register",
                          json={"password": password, "username": email},
                          headers=h, timeout=40)
        j2 = r2.json() if r2.text else {}
        log(f"  user/register -> HTTP {r2.status_code}")
        if r2.status_code != 200 or not (j2.get("continue_url") or ""):
            log(f"  user/register 失败: {r2.text[:250]}")
            return None
        log(f"  已设密码 {password}")

        # ── 3. 触发 OTP 邮件 ──────────────────────────────
        otp_trigger = dt.datetime.now()
        session.get(f"{AUTH_BASE}/api/accounts/email-otp/send", allow_redirects=False,
                    headers=_base_headers("https://auth.openai.com/create-account/password"), timeout=30)

        # ── 4. 取验证码(仅本次触发后最新) ───────────────────
        code = R.fetch_newest_otp(card, otp_trigger)
        if not code:
            log("  未取到本次验证码")
            return None
        log(f"  验证码 {code}")

        # ── 5. email-otp/validate ──────────────────────────
        h = _base_headers("https://auth.openai.com/email-verification", **{"oai-device-id": device_id})
        h.update(_sentinel_headers(session, device_id, FLOW_OTP))
        r3 = session.post(f"{AUTH_BASE}/api/accounts/email-otp/validate",
                          json={"code": code}, headers=h, timeout=40)
        j3 = r3.json() if r3.text else {}
        cu = j3.get("continue_url") or ""
        log(f"  email-otp/validate -> HTTP {r3.status_code} -> {cu.split('?')[0][-40:]}")
        if r3.status_code != 200:
            log(f"  validate 失败: {r3.text[:250]}")
            return None

        # ── 6. 若需 about-you, 走 create_account ───────────
        if "about-you" in cu:
            name = "".join(random.choices("abcdefghijklmnopqrstuvwxyz", k=random.randint(6, 10)))
            year = random.randint(1986, 2006)             # 19-40 岁
            birthdate = f"{year}-{random.randint(1,12):02d}-{random.randint(1,28):02d}"
            h = _base_headers("https://auth.openai.com/about-you", **{"oai-device-id": device_id})
            h.update(_sentinel_headers(session, device_id, FLOW_CREATE))
            r4 = session.post(f"{AUTH_BASE}/api/accounts/create_account",
                              json={"name": name, "birthdate": birthdate}, headers=h, timeout=40)
            j4 = r4.json() if r4.text else {}
            cu = j4.get("continue_url") or ""
            log(f"  create_account -> HTTP {r4.status_code} -> {cu.split('?')[0][-50:]}")
            if r4.status_code != 200 or not _extract_code(cu):
                log(f"  create_account 失败: {r4.text[:250]}")
                return None

        cb_code = _extract_code(cu)
        if not cb_code:
            log(f"  未拿到授权码: {cu[:200]}")
            return None
        return (cb_code, verifier, password)
    finally:
        session.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="只跑指定邮箱")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    cards = R.load_cards(R.CARDS_FILE)
    skip = R.load_skip()
    done = R.load_done()
    log(f"共 {len(cards)} 张卡, 已完成 {len(done)}, 跳过 {len(skip)}")

    ok = fail = 0
    for i, card in enumerate(cards, 1):
        email = card["email"]
        low = email.lower()
        if args.only and low != args.only.lower():
            continue
        if low in done:
            continue
        if low in skip:
            log(f"[{i}/{len(cards)}] 跳过(清单) {email}")
            continue
        if args.limit and (ok + fail) >= args.limit:
            break
        log(f"[{i}/{len(cards)}] 处理 {email}")
        try:
            res = run_protocol(card)
            if res:
                cb_code, verifier, password = res
                tokens = R.exchange_code(cb_code, verifier)
                payload = {"email": email, "source_type": "password" if password else "oauth_login"}
                if password:
                    payload["password"] = password
                tokens.update(payload)
                imp = R.import_account(tokens)
                if password:
                    R.save_password(email, password)
                R.append_result({"email": email, "status": "success",
                                 "added": imp.get("added", 0),
                                 "ts": dt.datetime.now().isoformat()})
                ok += 1
                log(f"  [OK] 成功 {email}")
            else:
                R.append_result({"email": email, "status": "fail",
                                 "ts": dt.datetime.now().isoformat()})
                fail += 1
                log(f"  [FAIL] 失败 {email}")
        except Exception as e:
            R.append_result({"email": email, "status": "error", "error": str(e),
                             "ts": dt.datetime.now().isoformat()})
            fail += 1
            log(f"  [ERR] {email}: {e}")

        if i < len(cards) and not args.only:
            d = random.randint(*R.BETWEEN_ACCOUNT_SEC)
            log(f"  等待 {d}s...")
            time.sleep(d)

    log(f"完成: 成功 {ok}, 失败 {fail}")


if __name__ == "__main__":
    main()
