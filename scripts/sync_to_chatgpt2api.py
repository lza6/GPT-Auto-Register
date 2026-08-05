#!/usr/bin/env python3
"""把注册项目数据库里已恢复（标准 token）的账号同步到 chatgpt2api。

背景：chatgpt2api 的 61 个账号 token 无效，用 verify_account_login.py 密码登录
（自动收验证码）恢复后，数据库里已有标准 access_token + openai_refresh_token。
本脚本把这些新 token 同步替换 chatgpt2api 里的旧无效条目。

用法：
    python scripts/sync_to_chatgpt2api.py [--dry-run] [--emails a,b]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
API_BASE = os.environ.get("CHATGPT2API_URL", "http://127.0.0.1:23499")


def load_admin_key() -> str:
    """优先环境变量 CHATGPT2API_ADMIN_KEY，其次本项目 config.json 的 chatgpt2api_admin_key。

    不再内置弱默认凭据（原硬编码 'chatgpt2api' 已被移除）。
    """
    env = os.environ.get("CHATGPT2API_ADMIN_KEY") or ""
    if env:
        return env
    cfg = ROOT / "config.json"
    if cfg.exists():
        try:
            return str(
                json.loads(cfg.read_text(encoding="utf-8")).get("chatgpt2api_admin_key") or ""
            ).strip()
        except Exception:
            pass
    return ""


def _require_auth_key() -> None:
    if not AUTH_KEY:
        print(
            "[错误] 未配置 chatgpt2api 管理密钥。请设置环境变量 CHATGPT2API_ADMIN_KEY，"
            "或在 config.json 的 chatgpt2api_admin_key 填入真实密钥。",
            file=sys.stderr,
        )
        sys.exit(1)


AUTH_KEY = load_admin_key()
HEADERS = {"Authorization": f"Bearer {AUTH_KEY}"}


def _is_std(token: str) -> bool:
    return bool(token) and token.startswith("eyJ") and len(token) > 500


def api_get(path: str) -> dict:
    r = httpx.get(f"{API_BASE}{path}", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def api_delete(path: str, body: dict) -> dict:
    r = httpx.request("DELETE", f"{API_BASE}{path}", headers=HEADERS, json=body, timeout=60)
    r.raise_for_status()
    return r.json()


def api_post(path: str, body: dict) -> dict:
    r = httpx.post(f"{API_BASE}{path}", headers=HEADERS, json=body, timeout=300)
    r.raise_for_status()
    return r.json()


def main() -> None:
    _require_auth_key()
    parser = argparse.ArgumentParser(description="同步恢复的 token 到 chatgpt2api")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要同步的账号，不实际修改")
    parser.add_argument("--emails", default="", help="只同步指定邮箱（逗号分隔）")
    args = parser.parse_args()

    # 1. chatgpt2api 当前账号（按 email 索引）
    items = api_get("/api/accounts").get("items") or []
    c2a_by_email: dict[str, dict] = {}
    for acc in items:
        email = str(acc.get("email") or "").strip()
        if email:
            c2a_by_email[email] = acc
    print(f"chatgpt2api 现有 {len(items)} 个账号")

    # 2. 注册项目数据库：标准 token 账号
    conn = sqlite3.connect(str(ROOT / "data" / "register.db"))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT email, access_token, openai_refresh_token, id_token, openai_password FROM accounts"
    ).fetchall()
    conn.close()
    db_by_email = {r["email"]: dict(r) for r in rows}

    only = {e.strip() for e in args.emails.split(",") if e.strip()}

    to_sync = []  # (email, chatgpt2api 旧条目, 新 token 数据)
    for email, c2a in c2a_by_email.items():
        if only and email not in only:
            continue
        old_token = str(c2a.get("access_token") or "")
        if _is_std(old_token):
            continue  # chatgpt2api 已是标准 token，无需同步
        db = db_by_email.get(email)
        if not db:
            continue
        new_token = str(db.get("access_token") or "")
        if not _is_std(new_token):
            continue  # 数据库也还没恢复
        to_sync.append((email, c2a, db))

    print(f"待同步 {len(to_sync)} 个已恢复账号")
    if args.dry_run:
        for email, _, _ in to_sync:
            print(f"  [dry-run] {email}")
        return

    for i, (email, c2a, db) in enumerate(to_sync, 1):
        old_token = str(c2a.get("access_token") or "")
        new_token = str(db["access_token"] or "")
        new_rt = str(db.get("openai_refresh_token") or "")
        new_it = str(db.get("id_token") or "")
        password = str(db.get("openai_password") or "")

        print(f"[{i}/{len(to_sync)}] {email}  旧token={old_token[:12]}... → 新token={new_token[:12]}...")
        try:
            # 删除旧无效条目
            if old_token:
                api_delete("/api/accounts", {"tokens": [old_token]})
            # 导入新 token 条目（保留 email/password 供后续续期）
            payload = {
                "access_token": new_token,
                "refresh_token": new_rt,
                "id_token": new_it,
                "email": email,
                "password": password,
                "type": "free",
                "status": "正常",
            }
            api_post("/api/accounts", {"accounts": [payload], "tokens": [new_token]})
            print(f"  ✅ 已同步")
        except Exception as exc:
            print(f"  ❌ 同步失败: {exc}")
        time.sleep(1)

    # 3. 汇总
    items = api_get("/api/accounts").get("items") or []
    std = sum(1 for a in items if _is_std(str(a.get("access_token") or "")))
    print(f"\n同步完成。chatgpt2api 账号池 {len(items)} 个，其中标准 token {std} 个。")


if __name__ == "__main__":
    main()
