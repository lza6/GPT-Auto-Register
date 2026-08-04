#!/usr/bin/env python3
"""把注册成功的账号导入到 chatgpt2api 项目。

用法：
    python scripts/import_to_chatgpt2api.py [--chatgpt2api-dir PATH]

功能：
    1. 从注册数据库读取成功账号（有 access_token 和 refresh_token）
    2. 用 refresh_token 调用 OAuth 刷新获取真正的 JWT access_token
    3. 把账号写入 chatgpt2api 的账号池
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

# 注册项目的数据库
REGISTER_DB = Path(__file__).resolve().parent.parent / "data" / "register.db"

# chatgpt2api 的账号池文件
CHATGPT2API_DATA = Path(__file__).resolve().parent.parent.parent / "chatgpt2api" / "data" / "accounts.json"


def get_register_accounts() -> list[dict]:
    """从注册数据库读取成功账号"""
    import sqlite3
    conn = sqlite3.connect(str(REGISTER_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM accounts WHERE status IN ('success', 'success_no_token') ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def refresh_access_token(refresh_token: str, client_id: str = "app_2SKx67EdpoN0G6j64rFvigXD") -> dict | None:
    """用 refresh_token 调用 OAuth 刷新获取真正的 access_token"""
    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                "https://auth.openai.com/oauth/token",
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
                },
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                },
            )
            data = resp.json()
            if resp.status_code == 200 and data.get("access_token"):
                return {
                    "access_token": data["access_token"],
                    "refresh_token": data.get("refresh_token", refresh_token),
                    "id_token": data.get("id_token", ""),
                }
            print(f"  OAuth 刷新失败: HTTP {resp.status_code} {data.get('error_description', data.get('error', 'unknown'))}")
            return None
    except Exception as e:
        print(f"  OAuth 刷新异常: {e}")
        return None


def import_to_chatgpt2api(accounts: list[dict], chatgpt2api_dir: str) -> dict:
    """把账号导入到 chatgpt2api 的账号池"""
    chatgpt2api_data = Path(chatgpt2api_dir) / "data" / "accounts.json"
    if not chatgpt2api_data.exists():
        print(f"chatgpt2api 账号池文件不存在: {chatgpt2api_data}")
        return {"added": 0, "skipped": 0, "error": "file not found"}

    # 读取现有账号
    existing = json.loads(chatgpt2api_data.read_text(encoding="utf-8"))
    existing_tokens = {a.get("access_token") for a in existing if a.get("access_token")}

    added = 0
    skipped = 0
    refreshed = 0
    failed_refresh = 0

    for acc in accounts:
        email = acc.get("email", "")
        access_token = acc.get("access_token", "")
        refresh_token = acc.get("refresh_token", "")
        name = acc.get("name", "")

        if not access_token or not refresh_token:
            skipped += 1
            continue

        # 如果 access_token 是 HEX 格式（CSRF token），需要刷新获取真正的 JWT
        if not access_token.startswith("eyJ"):
            print(f"  刷新 {email} 的 token...")
            token_data = refresh_access_token(refresh_token)
            if token_data:
                access_token = token_data["access_token"]
                refresh_token = token_data["refresh_token"]
                refreshed += 1
            else:
                failed_refresh += 1
                continue

        if access_token in existing_tokens:
            skipped += 1
            continue

        # 构建 chatgpt2api 账号格式
        account_item = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "id_token": "",  # id_token 需要从 OAuth 响应中获取
            "email": email,
            "password": acc.get("password", ""),
            "type": "free",
            "status": "正常",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        existing.append(account_item)
        existing_tokens.add(access_token)
        added += 1

    # 写回账号池
    chatgpt2api_data.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "added": added,
        "skipped": skipped,
        "refreshed": refreshed,
        "failed_refresh": failed_refresh,
        "total": len(existing),
    }


def main():
    parser = argparse.ArgumentParser(description="导入注册账号到 chatgpt2api")
    parser.add_argument("--chatgpt2api-dir", default=str(Path(__file__).resolve().parent.parent.parent / "chatgpt2api"),
                        help="chatgpt2api 项目目录")
    parser.add_argument("--refresh-all", action="store_true",
                        help="强制刷新所有账号的 token")
    args = parser.parse_args()

    print("读取注册账号...")
    accounts = get_register_accounts()
    print(f"共 {len(accounts)} 个成功账号")

    if args.refresh_all:
        print("强制刷新所有账号的 token...")
        for acc in accounts:
            if acc.get("refresh_token"):
                token_data = refresh_access_token(acc["refresh_token"])
                if token_data:
                    acc["access_token"] = token_data["access_token"]
                    acc["refresh_token"] = token_data["refresh_token"]

    print(f"导入到 chatgpt2api: {args.chatgpt2api_dir}")
    result = import_to_chatgpt2api(accounts, args.chatgpt2api_dir)
    print(f"导入完成: 新增 {result['added']}, 跳过 {result['skipped']}, 刷新 {result['refreshed']}, 刷新失败 {result['failed_refresh']}")
    print(f"chatgpt2api 账号池总数: {result['total']}")


if __name__ == "__main__":
    main()
