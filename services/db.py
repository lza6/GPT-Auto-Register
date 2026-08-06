from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "register.db"


def _ensure_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_conn() -> sqlite3.Connection:
    _ensure_dir()
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def db_session() -> sqlite3.Connection:
    """数据库会话上下文管理器：自动 commit / rollback / close。

    收敛手写样板（get_conn + with conn + close），确保异常时回滚且不泄漏连接。
    """
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    conn = get_conn()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                client_id TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at REAL DEFAULT (strftime('%s','now')),
                used_at REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                client_id TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                access_token TEXT,
                openai_refresh_token TEXT,
                id_token TEXT,
                openai_password TEXT,
                name TEXT,
                birthdate TEXT,
                proxy TEXT,
                status TEXT DEFAULT 'pending',
                error TEXT,
                registered_at REAL,
                created_at REAL DEFAULT (strftime('%s','now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                total INTEGER DEFAULT 0,
                completed INTEGER DEFAULT 0,
                failed INTEGER DEFAULT 0,
                skipped INTEGER DEFAULT 0,
                result TEXT,
                created_at REAL DEFAULT (strftime('%s','now')),
                updated_at REAL DEFAULT (strftime('%s','now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT DEFAULT 'info',
                message TEXT NOT NULL,
                data TEXT,
                created_at REAL DEFAULT (strftime('%s','now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        # 迁移：为已有 accounts 表补充 OpenAI token / 密码字段（区分微软邮箱凭据与 OpenAI 凭据）
        for col in ("openai_refresh_token", "id_token", "openai_password"):
            try:
                conn.execute(f"ALTER TABLE accounts ADD COLUMN {col} TEXT")
            except Exception:
                pass
    conn.close()


MAX_LOG_ROWS = 20000  # logs 表硬上限：超量删最旧一批，防单日高频爆量（M4）
_LOG_TRIM_COUNTER = {"n": 0}  # 每 100 次插入才检查一次上限，避免每次插入都跑 DELETE 子查询


def add_log(level: str, message: str, data: Any = None) -> None:
    with db_session() as conn:
        conn.execute(
            "INSERT INTO logs (level, message, data, created_at) VALUES (?, ?, ?, ?)",
            (level, message, json.dumps(data, ensure_ascii=False) if data else None, time.time()),
        )
    # M4 容量上限：每 100 次插入才检查一次，避免高频写入时 DELETE 子查询拖慢
    _LOG_TRIM_COUNTER["n"] += 1
    if _LOG_TRIM_COUNTER["n"] % 100 == 0:
        with db_session() as conn:
            conn.execute(
                "DELETE FROM logs WHERE id <= (SELECT COALESCE(MAX(id), 0) FROM logs) - ?",
                (MAX_LOG_ROWS,),  # 删到只剩 MAX_LOG_ROWS 条
            )


def get_logs(limit: int = 100, offset: int = 0) -> list[dict]:
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM logs ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
    return [dict(r) for r in rows]


def get_logs_after(after_id: int, limit: int = 1000) -> list[dict]:
    """返回 id > after_id 的日志（增量拉取，前端轮询降载用）。"""
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM logs WHERE id > ? ORDER BY id ASC LIMIT ?", (after_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]


def purge_old_logs(retention_days: int = 30) -> int:
    """删除 logs 表超过保留期（retention_days 天）的记录，返回删除条数。

    防止长期运行日志无限增长。启动时与（可选的）定时任务调用。
    """
    cutoff = time.time() - max(1, int(retention_days)) * 86400
    with db_session() as conn:
        cur = conn.execute("DELETE FROM logs WHERE created_at < ?", (cutoff,))
    return cur.rowcount


def insert_email(email: str, password: str, client_id: str, refresh_token: str) -> bool:
    try:
        with db_session() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO emails (email, password, client_id, refresh_token) VALUES (?, ?, ?, ?)",
                (email, password, client_id, refresh_token),
            )
        return True
    except Exception:
        return False


def get_pending_emails(limit: int = 0) -> list[dict]:
    sql = "SELECT * FROM emails WHERE status = 'pending' ORDER BY id ASC"
    if limit > 0:
        sql += f" LIMIT {limit}"
    with db_session() as conn:
        rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def mark_email_status(email: str, status: str) -> None:
    with db_session() as conn:
        conn.execute(
            "UPDATE emails SET status = ?, used_at = ? WHERE email = ?",
            (status, time.time() if status != 'pending' else None, email),
        )


def insert_account(email: str, password: str, client_id: str, refresh_token: str,
                   access_token: str = "", name: str = "", birthdate: str = "",
                   proxy: str = "", status: str = "pending", error: str = "",
                   openai_refresh_token: str = "", id_token: str = "",
                   openai_password: str = "") -> None:
    with db_session() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO accounts
               (email, password, client_id, refresh_token, access_token, openai_refresh_token, id_token, openai_password, name, birthdate, proxy, status, error, registered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (email, password, client_id, refresh_token, access_token, openai_refresh_token, id_token,
             openai_password, name, birthdate, proxy, status, error,
             time.time() if status == 'success' else None),
        )


def get_accounts(status: str = "", limit: int = 0, offset: int = 0, search: str = "") -> list[dict]:
    sql = "SELECT * FROM accounts"
    params: list = []
    conds: list[str] = []
    if status:
        conds.append("status = ?")
        params.append(status)
    if search:
        conds.append("email LIKE ?")
        params.append(f"%{search.strip()}%")
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY id DESC"
    if limit > 0:
        sql += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
    with db_session() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def count_accounts(status: str = "", search: str = "") -> int:
    with db_session() as conn:
        conds: list[str] = []
        params: list = []
        if status:
            conds.append("status = ?")
            params.append(status)
        if search:
            conds.append("email LIKE ?")
            params.append(f"%{search.strip()}%")
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        row = conn.execute(f"SELECT COUNT(*) as c FROM accounts{where}", params).fetchone()
    return row["c"] if row else 0


def count_emails(status: str = "", search: str = "") -> int:
    with db_session() as conn:
        conds: list[str] = []
        params: list = []
        if status:
            conds.append("status = ?")
            params.append(status)
        if search:
            conds.append("email LIKE ?")
            params.append(f"%{search.strip()}%")
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        row = conn.execute(f"SELECT COUNT(*) as c FROM emails{where}", params).fetchone()
    return row["c"] if row else 0


def get_stats() -> dict:
    # 单条聚合 SQL 取 emails + accounts 全量/分状态计数（替代原 9 次独立 COUNT 查询，L4 性能优化）
    with db_session() as conn:
        row = conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM emails) AS emails_total,
              (SELECT COUNT(*) FROM emails WHERE status='pending') AS emails_pending,
              (SELECT COUNT(*) FROM emails WHERE status='used') AS emails_used,
              (SELECT COUNT(*) FROM accounts) AS accounts_total,
              (SELECT COUNT(*) FROM accounts WHERE status='success') AS accounts_success,
              (SELECT COUNT(*) FROM accounts WHERE status='failed') AS accounts_failed,
              (SELECT COUNT(*) FROM accounts WHERE status='skipped') AS accounts_skipped,
              (SELECT COUNT(*) FROM accounts WHERE status='pending') AS accounts_pending,
              (SELECT COUNT(*) FROM accounts WHERE status='registering') AS accounts_registering
            """
        ).fetchone()
    stats = {
        "emails_total": row["emails_total"] if row else 0,
        "emails_pending": row["emails_pending"] if row else 0,
        "emails_used": row["emails_used"] if row else 0,
        "accounts_total": row["accounts_total"] if row else 0,
        "accounts_success": row["accounts_success"] if row else 0,
        "accounts_failed": row["accounts_failed"] if row else 0,
        "accounts_skipped": row["accounts_skipped"] if row else 0,
        "accounts_pending": row["accounts_pending"] if row else 0,
        "accounts_registering": row["accounts_registering"] if row else 0,
    }
    # 最近一次批量任务的失败原因分类分布（run_batch 写入 tasks.result.failure_types）
    stats["last_task_failure_types"] = {}
    task = get_latest_task()
    if task and task.get("result"):
        try:
            data = json.loads(task["result"])
            ft = data.get("failure_types") or {}
            stats["last_task_failure_types"] = ft if isinstance(ft, dict) else {}
        except Exception:
            pass
    return stats


def get_setting(key: str, default: str = "") -> str:
    with db_session() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with db_session() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))


# ────────────────────────────────
# 任务生命周期：进度落库 / 断点续跑
# ────────────────────────────────

def update_task_progress(task_id: int, completed: int = 0, failed: int = 0,
                         skipped: int = 0, status: str = "running",
                         result: str = "") -> None:
    """更新任务进度（调用方传入累计值，整体覆盖）。"""
    with db_session() as conn:
        conn.execute(
            """UPDATE tasks SET completed = ?, failed = ?, skipped = ?, status = ?,
               result = ?, updated_at = ? WHERE id = ?""",
            (completed, failed, skipped, status, result, time.time(), task_id),
        )


def get_task(task_id: int) -> dict | None:
    with db_session() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(row) if row else None


def get_latest_task() -> dict | None:
    with db_session() as conn:
        row = conn.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def reset_stale_tasks() -> int:
    """服务启动时把遗留的 running 任务标记为 interrupted（断点续跑信号）。

    emails 表按 status=pending 驱动续跑，因此已成功的账号不会重复注册。
    """
    with db_session() as conn:
        cur = conn.execute(
            "UPDATE tasks SET status = 'interrupted', updated_at = ? WHERE status = 'running'",
            (time.time(),),
        )
    return cur.rowcount


init_db()
