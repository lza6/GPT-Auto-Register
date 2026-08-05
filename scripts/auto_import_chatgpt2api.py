#!/usr/bin/env python3
"""把导出账号自动导入 chatgpt2api，并用密码登录恢复无效 token 的账号。

用法：
    python scripts/auto_import_chatgpt2api.py import [--file data/export_chatgpt2api.json]
    python scripts/auto_import_chatgpt2api.py relogin [--batch N] [--interval S] [--limit N] [--resume]

import  : 把导出 JSON 里的账号批量导入 chatgpt2api（走 POST /api/accounts，带 password）。
relogin : 自动分批对"无效 token 但有密码"的账号执行密码重新登录（re-login），
          每批 --batch 个、批间 --interval 秒，遇限流自动退避，失败账号可 --resume 续跑。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
API_BASE = os.environ.get("CHATGPT2API_URL", "http://127.0.0.1:23499")
EXPORT_FILE = ROOT / "data" / "export_chatgpt2api.json"
PROGRESS_FILE = ROOT / "data" / "relogin_progress.json"


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


def _is_std_token(token: str) -> bool:
    """标准 RS256 JWT 的粗略判断（eyJ 开头且 >500 字符）。"""
    return bool(token) and token.startswith("eyJ") and len(token) > 500


def api_get(path: str) -> dict:
    r = httpx.get(f"{API_BASE}{path}", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def api_post(path: str, body: dict) -> dict:
    r = httpx.post(f"{API_BASE}{path}", headers=HEADERS, json=body, timeout=300)
    r.raise_for_status()
    return r.json()


def load_accounts() -> list[dict]:
    if not EXPORT_FILE.exists():
        print(f"导出文件不存在: {EXPORT_FILE}（先运行 import_to_chatgpt2api.py --output）")
        sys.exit(1)
    return json.loads(EXPORT_FILE.read_text(encoding="utf-8"))


def cmd_import(args: argparse.Namespace) -> None:
    accounts = load_accounts()
    print(f"导入 {len(accounts)} 个账号到 chatgpt2api ...")
    tokens = [a["access_token"] for a in accounts if a.get("access_token")]
    result = api_post("/api/accounts", {"accounts": accounts, "tokens": tokens})
    print(f"新增 {result.get('added', 0)}, 跳过重复 {result.get('skipped', 0)}, 已刷新 {result.get('refreshed', 0)}")
    errors = result.get("errors") or []
    if errors:
        print(f"刷新错误 {len(errors)} 条（首个: {errors[0].get('error', '')}）")
    items = result.get("items") or api_get("/api/accounts").get("items") or []
    from collections import Counter
    print("账号池状态:", dict(Counter(i.get("status") for i in items)))


def _pending_relogin_tokens(items: list[dict], done: set[str]) -> list[dict]:
    """无效 token 且有密码、且未处理过的账号。"""
    pending = []
    for acc in items:
        token = str(acc.get("access_token") or "")
        if _is_std_token(token):
            continue  # 已是标准 token，无需恢复
        if not str(acc.get("password") or "").strip():
            continue  # 无密码无法 re-login
        if token in done:
            continue
        pending.append(acc)
    return pending


def cmd_relogin(args: argparse.Namespace) -> None:
    batch = max(1, args.batch)
    interval = max(10, args.interval)
    # 提交后等待 re-login 异步线程完成的时间：单账号密码登录约 10-30s
    wait_secs = max(45, min(180, batch * 20))
    done: set[str] = set()

    if args.resume and PROGRESS_FILE.exists():
        done = set(json.loads(PROGRESS_FILE.read_text(encoding="utf-8")).get("done", []))
        print(f"续跑模式: 已处理 {len(done)} 个")

    while True:
        items = api_get("/api/accounts").get("items") or []
        pending = _pending_relogin_tokens(items, done)
        if not pending:
            print("全部无效 token 账号已处理完成。")
            break
        if args.limit and len(done) >= args.limit:
            print(f"达到 --limit {args.limit}，停止。")
            break

        chunk = pending[:batch]
        tokens = [a["access_token"] for a in chunk]
        emails = [a.get("email") for a in chunk]
        print(f"\n本批 {len(chunk)} 个: {', '.join(emails)}")

        try:
            resp = api_post("/api/accounts/re-login", {"access_tokens": tokens})
            progress_id = resp.get("progress_id")
            print(f"  re-login 已提交, progress={progress_id}，等待 {wait_secs}s 完成...")
        except httpx.HTTPStatusError as exc:
            print(f"  提交失败 HTTP {exc.response.status_code}: {exc.response.text[:200]}")
            time.sleep(interval)
            continue

        # re-login 是后端异步线程，固定等待后直接查账号状态判断结果，避免死等 progress
        time.sleep(wait_secs)
        print(f"  本批等待完成（{wait_secs}s）")

        # 检查本批结果：标准 token 视为成功，否则失败（可能限流）
        items = api_get("/api/accounts").get("items") or []
        token_by_acc = {a["access_token"]: a for a in items}
        success, failed = [], []
        for token, email in zip(tokens, emails):
            acc = token_by_acc.get(token)
            # re-login 成功会轮换 token（旧 token 变 alias），用 get_account 查不到就用 email 再核对
            ok = False
            if acc is not None and _is_std_token(str(acc.get("access_token") or "")):
                ok = True
            if not ok:
                # token 轮换后旧 token 查不到：按 email 找
                by_email = [a for a in items if a.get("email") == email]
                if by_email and any(_is_std_token(str(a.get("access_token") or "")) for a in by_email):
                    ok = True
            if ok:
                success.append(email)
            else:
                failed.append(email)
            done.add(token)
            PROGRESS_FILE.write_text(json.dumps({"done": sorted(done)}, ensure_ascii=False), encoding="utf-8")

        print(f"  成功 {len(success)}: {', '.join(success) if success else '无'}")
        print(f"  失败 {len(failed)}: {', '.join(failed) if failed else '无'}")
        if failed:
            print(f"  ⚠ 失败可能是 OpenAI 限流，已暂停 {interval}s 后继续")

        if args.limit and len(done) >= args.limit:
            print(f"达到 --limit {args.limit}，停止。")
            break
        time.sleep(interval)


def main() -> None:
    _require_auth_key()
    parser = argparse.ArgumentParser(description="自动导入并密码登录恢复 chatgpt2api 账号")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_import = sub.add_parser("import", help="导入导出文件到 chatgpt2api")
    p_import.set_defaults(func=cmd_import)

    p_relogin = sub.add_parser("relogin", help="分批密码重新登录恢复无效 token 账号")
    p_relogin.add_argument("--batch", type=int, default=5, help="每批账号数（默认 5）")
    p_relogin.add_argument("--interval", type=int, default=90, help="批间间隔秒数（默认 90）")
    p_relogin.add_argument("--limit", type=int, default=0, help="最多处理多少个（测试用，0=不限）")
    p_relogin.add_argument("--resume", action="store_true", help="从上次进度续跑")
    p_relogin.set_defaults(func=cmd_relogin)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
