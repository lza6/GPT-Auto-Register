#!/usr/bin/env python3
"""批量自动监测所有注册账号是否可用（API 方式，无需浏览器，并发快速）。

对每个账号检查两维可用性：
  1. OpenAI 账号：优先用 openai_refresh_token 刷新；否则用 access_token 调 backend-api/me
  2. 微软邮箱：调用邮件 API 能否读取收件箱（能收件 = 邮箱可用）

用法:
    python scripts/verify_all_accounts.py [--limit N] [--concurrency N]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from services.db import get_conn

OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_2SKx67EdpoN0G6j64rFvigXD"
BACKEND_ME = "https://chatgpt.com/backend-api/me"


def _refresh_openai(refresh_token: str) -> bool:
    try:
        r = httpx.post(
            OAUTH_TOKEN_URL,
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": CLIENT_ID},
            timeout=30,
        )
        return r.status_code == 200 and bool(r.json().get("access_token"))
    except Exception:
        return False


def _check_openai_api(access_token: str) -> bool:
    try:
        r = httpx.get(BACKEND_ME, headers={"Authorization": f"Bearer {access_token}"}, timeout=25)
        return r.status_code == 200
    except Exception:
        return False


async def _check_mail(email: str, password: str, client_id: str, refresh_token: str) -> bool:
    """调用 98faka 邮件 API 能否读取收件箱"""
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.post(
                "https://app.98faka.top/api/emails",
                json={
                    "email": email, "password": password,
                    "client_id": client_id, "refresh_token": refresh_token,
                    "folder": "inbox",
                },
            )
            data = resp.json() if resp.content else {}
            return resp.status_code == 200 and data.get("code") == 200
    except Exception:
        return False


async def _check_one(acc: dict, sem: asyncio.Semaphore) -> dict:
    async with sem:
        email = acc.get("email", "")
        ort = acc.get("openai_refresh_token") or ""
        at = acc.get("access_token") or ""
        pw = acc.get("openai_password") or ""

        openai_ok = False
        openai_way = ""
        if ort:
            openai_way = "refresh"
            openai_ok = _refresh_openai(ort)
        elif at.startswith("eyJ") and len(at) > 200:
            openai_way = "api"
            openai_ok = _check_openai_api(at)
        else:
            openai_way = "no_token"

        mail_ok = await _check_mail(email, acc.get("password") or "",
                                    acc.get("client_id") or "", acc.get("refresh_token") or "")

        return {
            "email": email,
            "status": acc.get("status"),
            "openai": openai_ok,
            "openai_way": openai_way,
            "mail": mail_ok,
            "has_openai_pw": bool(pw),
        }


def _load_accounts(limit: int) -> list[dict]:
    conn = get_conn()
    if limit > 0:
        rows = conn.execute(
            "SELECT * FROM accounts WHERE status IN ('success','success_no_token') ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM accounts WHERE status IN ('success','success_no_token') ORDER BY id"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


async def main(limit: int, concurrency: int):
    accounts = _load_accounts(limit)
    print(f"待检查账号: {len(accounts)} 个（并发 {concurrency}）\n")

    sem = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(*[_check_one(a, sem) for a in accounts])

    openai_ok = sum(1 for r in results if r["openai"])
    openai_fail = sum(1 for r in results if not r["openai"])
    mail_ok = sum(1 for r in results if r["mail"])
    mail_fail = sum(1 for r in results if not r["mail"])
    with_pw = sum(1 for r in results if r["has_openai_pw"])

    print("=" * 80)
    print(f"{'邮箱':<38} {'状态':<8} {'OpenAI':<8} {'方式':<10} {'邮箱':<8} {'OpenAI密码'}")
    print("-" * 80)
    for r in results:
        print(f"{r['email']:<38} {r['status']:<8} "
              f"{'✅' if r['openai'] else '❌':<6} {r['openai_way']:<10} "
              f"{'✅' if r['mail'] else '❌':<6} {'✅' if r['has_openai_pw'] else '-'}")
    print("=" * 80)
    print(f"汇总: OpenAI可用 {openai_ok}/{len(results)}, 邮箱可用 {mail_ok}/{len(results)}, 有OpenAI密码 {with_pw}")
    print(f"      OpenAI不可用: {openai_fail}, 邮箱不可用: {mail_fail}")

    # 输出 JSON 报告
    report = {
        "total": len(results),
        "openai_ok": openai_ok,
        "openai_fail": openai_fail,
        "mail_ok": mail_ok,
        "mail_fail": mail_fail,
        "with_openai_password": with_pw,
        "items": results,
    }
    out = ROOT / "data" / "accounts_check_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已保存: {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="批量监测账号可用性")
    parser.add_argument("--limit", type=int, default=0, help="只检查前 N 个（0=全部）")
    parser.add_argument("--concurrency", type=int, default=8, help="并发数")
    args = parser.parse_args()
    asyncio.run(main(args.limit, args.concurrency))
