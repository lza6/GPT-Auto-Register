#!/usr/bin/env python3
"""批量刷新控制器：每个账号独立子进程，超时强杀，断点续跑。

解决单个账号卡死（camoufox 无响应）拖累整个批量的问题：
    - 每个账号启动独立 python 子进程（verify_account_login --email xxx）
    - 180 秒超时自动 taskkill /T 杀树（含浏览器）
    - 结果写入 data/refresh_results.jsonl，已成功的账号自动跳过
    - 进程被中断后重启本脚本即可从断点继续

用法:
    python scripts/refresh_all.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

PY = ROOT / ".venv" / "Scripts" / "python.exe"
SCRIPT = ROOT / "scripts" / "verify_account_login.py"
RESULT = ROOT / "data" / "refresh_results.jsonl"
LOCK = ROOT / "data" / "refresh.lock"
PER_ACCOUNT_TIMEOUT = 180  # 秒


def _acquire_lock() -> bool:
    """单实例锁：已有存活实例则拒绝启动"""
    if LOCK.exists():
        try:
            pid = int(LOCK.read_text().strip())
            if psutil.pid_exists(pid):
                print(f"已有 refresh_all 实例 (PID {pid}) 在运行，本实例退出。", flush=True)
                return False
        except Exception:
            pass
    LOCK.write_text(str(os.getpid()))
    return True


def _load_processed() -> set[str]:
    """启动时一次性把结果文件中的邮箱载入 set，之后 O(1) 查询。

    原实现每次对单个邮箱重读整个文件（O(N²)），批量 1000 账号时性能退化。
    无论成败都记入，避免反复重跑卡在难处理的账号上。
    """
    processed: set[str] = set()
    if not RESULT.exists():
        return processed
    for line in RESULT.read_text(encoding="utf-8").splitlines():
        try:
            email = json.loads(line).get("email")
            if email:
                processed.add(email)
        except Exception:
            pass
    return processed


def main():
    if not _acquire_lock():
        sys.exit(0)
    # 确保数据库已初始化（新环境首次运行不报 "no such table: accounts"）
    import services.db as sdb
    sdb.init_db()
    conn = sqlite3.connect(str(ROOT / "data" / "register.db"))
    rows = conn.execute(
        "SELECT email, openai_password FROM accounts "
        "WHERE openai_password IS NOT NULL AND openai_password != '' ORDER BY id"
    ).fetchall()
    conn.close()

    total = len(rows)
    processed = _load_processed()
    print(f"待处理账号: {total}（含已成功的将跳过）", flush=True)
    ok = fail = skip = 0
    start = time.time()

    for i, (email, _opw) in enumerate(rows, 1):
        if email in processed:
            skip += 1
            print(f"[{i}/{total}] {email} → 跳过（已处理）", flush=True)
            continue

        print(f"[{i}/{total}] {email} → 处理中...", flush=True)
        p = subprocess.Popen(
            [str(PY), str(SCRIPT), "--email", email],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(ROOT),
        )
        try:
            rc = p.wait(timeout=PER_ACCOUNT_TIMEOUT)
            is_ok = rc == 0
            reason = ""
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
            is_ok = False
            reason = "timeout"
            print(f"    ⚠️ 超时强杀 ({PER_ACCOUNT_TIMEOUT}s)", flush=True)

        with open(RESULT, "a", encoding="utf-8") as f:
            f.write(json.dumps({"email": email, "success": is_ok, "exit": 0 if is_ok else 1, "reason": reason}, ensure_ascii=False) + "\n")
        if is_ok:
            ok += 1
            print(f"    ✅ 可用", flush=True)
        else:
            fail += 1
            print(f"    ❌ 不可用{(' (' + reason + ')') if reason else ''}", flush=True)
        time.sleep(1)

    elapsed = int(time.time() - start)
    print(f"\n=== 完成: 可用 {ok}, 不可用 {fail}, 跳过 {skip}, 总 {total}, 耗时 {elapsed//60}分{elapsed%60}秒 ===", flush=True)
    try:
        LOCK.unlink()
    except Exception:
        pass


if __name__ == "__main__":
    main()
