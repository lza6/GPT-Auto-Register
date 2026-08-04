#!/usr/bin/env python3
"""完整闭环：注册新账号 → 获取标准 JWT → 自动去重导入 chatgpt2api

一次性完成：
1. 从 91kami 导入新邮箱
2. 逐个注册（CF solver 自动过验证 + 本地代理）
3. 注册完成即从 session API 获取标准 JWT access_token
4. 自动去重导入 chatgpt2api 账号池（重复 token 自动跳过）
5. 导出 token 到文件

用法：
    python scripts/auto_register_import.py --source <91kami链接> [--proxy http://127.0.0.1:10808]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn, init_db, insert_email, get_pending_emails, add_log
from services.email_service import email_service
from services.register_engine import get_engine

CHATGPT2API_URL = "http://localhost:23456"
CHATGPT2API_KEY = "chatgpt2api"


def import_emails_from_source(source_url: str) -> dict:
    """从 91kami 导入邮箱"""
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(email_service.import_emails(source_url))
    finally:
        loop.close()
    return result


def register_and_get_token(email: str, password: str, client_id: str,
                           refresh_token: str, proxy_url: str | None) -> dict:
    """注册单个账号并获取标准 JWT，返回结果"""
    import asyncio
    from services.browser_register import get_browser_register

    config = {
        "otp_wait_timeout_sec": 600,
        "otp_poll_interval_sec": 5,
        "proxy_url": proxy_url,
        "use_proxy": True,
    }
    browser_reg = get_browser_register(config)

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            browser_reg.register_one(email, password, client_id, refresh_token)
        )
    finally:
        loop.close()
    return result


def import_to_chatgpt2api(tokens: list[str]) -> dict:
    """把 token 导入 chatgpt2api 账号池（自动去重）"""
    import urllib.request

    body = json.dumps({"tokens": tokens}).encode("utf-8")
    req = urllib.request.Request(f"{CHATGPT2API_URL}/api/accounts", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {CHATGPT2API_KEY}")
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        result = json.loads(resp.read().decode("utf-8"))
        return {
            "added": result.get("added", 0),
            "skipped": result.get("skipped", 0),
            "errors": result.get("errors", []),
        }
    except Exception as e:
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="完整闭环：注册+标准JWT+自动去重导入chatgpt2api")
    parser.add_argument("--source", required=True, help="91kami 邮箱源链接")
    parser.add_argument("--proxy", default="http://127.0.0.1:10808", help="本地代理")
    parser.add_argument("--limit", type=int, default=0, help="最多注册数量（0=全部）")
    parser.add_argument("--chatgpt2api-url", default=CHATGPT2API_URL, help="chatgpt2api 地址")
    parser.add_argument("--chatgpt2api-key", default=CHATGPT2API_KEY, help="chatgpt2api API key")
    args = parser.parse_args()

    global CHATGPT2API_URL, CHATGPT2API_KEY
    CHATGPT2API_URL = args.chatgpt2api_url
    CHATGPT2API_KEY = args.chatgpt2api_key

    print("=" * 60)
    print("完整闭环：注册 → 标准JWT → 导入chatgpt2api")
    print("=" * 60)

    # Step 1: 导入邮箱
    print("\n[1/4] 导入邮箱...")
    init_db()
    imp = import_emails_from_source(args.source)
    print(f"  新增 {imp.get('inserted', 0)} 个邮箱")

    # Step 2: 注册并获取 token
    print(f"\n[2/4] 开始注册（代理: {args.proxy}）...")
    pending = get_pending_emails(limit=args.limit or 999999)
    print(f"  待注册: {len(pending)} 个")

    success_tokens: list[str] = []
    success_count = 0
    fail_count = 0

    for i, mail in enumerate(pending):
        email = mail["email"]
        password = mail["password"]
        client_id = mail["client_id"]
        refresh_token = mail["refresh_token"]

        print(f"\n  [{i+1}/{len(pending)}] 注册 {email}...")
        result = register_and_get_token(email, password, client_id, refresh_token, args.proxy)

        status = result.get("status", "failed")
        access_token = result.get("access_token", "")

        if status == "success" and access_token.startswith("eyJ") and len(access_token) > 500:
            # 标准 JWT
            success_count += 1
            success_tokens.append(access_token)
            print(f"    ✅ 成功+标准JWT ({len(access_token)} 字符)")

            # 更新数据库 token
            conn = get_conn()
            conn.execute(
                "UPDATE accounts SET access_token = ?, status = 'success' WHERE email = ?",
                (access_token, email),
            )
            conn.execute("UPDATE emails SET status = 'used' WHERE email = ?", (email,))
            conn.commit()
            conn.close()
        elif access_token:
            print(f"    ⚠️ 注册成功但 token 非标准格式 ({len(access_token)} 字符)，跳过导入")
            fail_count += 1
        else:
            print(f"    ❌ 失败: {result.get('error', 'unknown')}")
            fail_count += 1

        time.sleep(3)  # 间隔

    # Step 3: 导入 chatgpt2api
    print(f"\n[3/4] 导入标准 JWT 到 chatgpt2api...")
    print(f"  标准 JWT: {len(success_tokens)} 个")
    if success_tokens:
        imp_result = import_to_chatgpt2api(success_tokens)
        print(f"  导入结果: {json.dumps(imp_result, ensure_ascii=False)}")
    else:
        print("  没有标准 JWT 可导入")

    # Step 4: 导出 token 文件
    print(f"\n[4/4] 导出 token 文件...")
    if success_tokens:
        token_file = Path(__file__).resolve().parent.parent / "已经获取到的token.txt"
        token_file.write_text("\n".join(success_tokens) + "\n", encoding="utf-8")
        print(f"  已导出 {len(success_tokens)} 个 token 到 {token_file.name}")

    print("\n" + "=" * 60)
    print(f"完成！成功: {success_count}, 失败: {fail_count}")
    print("=" * 60)


if __name__ == "__main__":
    main()