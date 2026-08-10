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
        # 迁移（v3.3 多平台邮箱库）：emails/accounts 补 platform 字段，标记邮箱用于哪个平台注册。
        # 默认 'chatgpt'（现有数据）。后续扩展 grok/claude 等平台自动化注册时按 platform 区分，
        # 同一邮箱可在不同平台各注册一次（跨平台去重），同平台内 email 唯一防重复注册。
        for tbl in ("emails", "accounts"):
            try:
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN platform TEXT DEFAULT 'chatgpt'")
            except Exception:
                pass
        # 平台使用记录表：一个邮箱在多个平台的使用情况（去重/审计的核心）。
        # (email, platform) 唯一 → 同邮箱同平台只记一次；换平台可再注册。
        conn.execute("""
            CREATE TABLE IF NOT EXISTS platform_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                platform TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                account_email TEXT,
                used_at REAL,
                created_at REAL DEFAULT (strftime('%s','now')),
                UNIQUE(email, platform)
            )
        """)
        # 索引：高频查询字段补索引，避免数据量大时全表扫描（L4 配套性能优化）
        # CREATE INDEX IF NOT EXISTS 幂等，旧库升级与新库首次初始化都安全
        for idx_sql in (
            "CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts(status)",
            "CREATE INDEX IF NOT EXISTS idx_emails_status ON emails(status)",
            "CREATE INDEX IF NOT EXISTS idx_logs_created_at ON logs(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)",
            "CREATE INDEX IF NOT EXISTS idx_emails_platform ON emails(platform)",
            "CREATE INDEX IF NOT EXISTS idx_platform_usage_email ON platform_usage(email)",
            "CREATE INDEX IF NOT EXISTS idx_platform_usage_platform ON platform_usage(platform)",
        ):
            try:
                conn.execute(idx_sql)
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


def insert_email(email: str, password: str, client_id: str, refresh_token: str,
                 platform: str = "chatgpt") -> bool:
    """插入邮箱；唯一冲突（OR IGNORE 未实际插入）返回 False，调用方据此次数准确计数。

    platform：标记该邮箱用于哪个平台注册（默认 chatgpt）；同时在 platform_usage 记一行。
    """
    try:
        with db_session() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO emails (email, password, client_id, refresh_token, platform) VALUES (?, ?, ?, ?, ?)",
                (email, password, client_id, refresh_token, platform or "chatgpt"),
            )
            # 平台使用记录（去重核心）：同邮箱同平台只记一次
            conn.execute(
                "INSERT OR IGNORE INTO platform_usage (email, platform, status) VALUES (?, ?, 'pending')",
                (email, platform or "chatgpt"),
            )
        return cur.rowcount > 0  # 唯一约束冲突时 OR IGNORE 插入 0 行 → False（不误报新增）
    except Exception:
        return False


# ── 平台使用记录（多平台注册去重/审计）────────────────────────────
def list_platforms() -> list[str]:
    """返回所有出现过的平台（去重），供前端平台切换按钮。"""
    with db_session() as conn:
        rows = conn.execute(
            "SELECT DISTINCT platform FROM platform_usage WHERE platform IS NOT NULL AND platform != '' ORDER BY platform"
        ).fetchall()
    plats = [r["platform"] for r in rows]
    return plats or ["chatgpt"]


def email_platform_map(email: str) -> list[dict]:
    """查一个邮箱在各平台的使用情况（哪些平台用过/状态）。"""
    with db_session() as conn:
        rows = conn.execute(
            "SELECT platform, status, account_email, used_at FROM platform_usage WHERE email = ? ORDER BY platform",
            (email,),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_platform_usage(email: str, platform: str, status: str, account_email: str = "") -> None:
    """更新某邮箱在某平台的使用状态（注册成功/失败/使用）。"""
    with db_session() as conn:
        conn.execute(
            """INSERT INTO platform_usage (email, platform, status, account_email, used_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(email, platform) DO UPDATE SET
                 status=excluded.status, account_email=excluded.account_email, used_at=excluded.used_at""",
            (email, platform or "chatgpt", status, account_email,
             time.time() if status != "pending" else None),
        )


def platform_stats() -> dict:
    """各平台邮箱使用统计：{platform: {total, used, pending}}。"""
    with db_session() as conn:
        rows = conn.execute(
            """SELECT platform,
                      COUNT(*) AS total,
                      SUM(CASE WHEN status='used' THEN 1 ELSE 0 END) AS used,
                      SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending
               FROM platform_usage GROUP BY platform ORDER BY platform"""
        ).fetchall()
    return {r["platform"]: {"total": r["total"], "used": r["used"] or 0, "pending": r["pending"] or 0} for r in rows}


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

    # v3.0 A4A6：失败分级诊断建议（前端统计卡片展示）
    stats["failure_diagnosis"] = _build_failure_diagnosis(stats["last_task_failure_types"])
    return stats


def _build_failure_diagnosis(failure_types: dict) -> str:
    """按失败占比最高类型给一句话引导（空则返回空串）。"""
    if not failure_types:
        return ""
    total = sum(failure_types.values())
    if total == 0:
        return ""
    # 找占比最高的类型
    top_type = max(failure_types, key=lambda k: failure_types.get(k, 0))
    top_count = failure_types.get(top_type, 0)
    top_pct = top_count / total
    hints = {
        "risk_control": f"⚠️ 风控失败占比 {top_pct:.0%}（{top_count}/{total}），建议更换代理出口 IP",
        "otp_timeout": f"⏱️ 验证码超时占比 {top_pct:.0%}（{top_count}/{total}），建议检查邮件 API 或延长 otp_wait_timeout_sec",
        "network": f"🌐 网络失败占比 {top_pct:.0%}（{top_count}/{total}），建议检查代理可用性与 email_api_base",
        "server_5xx": f"🔥 服务器 5xx 占比 {top_pct:.0%}（{top_count}/{total}），OpenAI 服务抖动，稍后重试",
        "unknown": f"❓ 未知失败占比 {top_pct:.0%}（{top_count}/{total}），查看运行日志定位",
    }
    return hints.get(top_type, f"失败占比最高：{top_type} {top_pct:.0%}")


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


def get_task_history(limit: int = 50) -> list[dict]:
    """返回最近任务历史（按 id 倒序），供前端任务历史查看。"""
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


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


# === VACUUM 策略 ===
import time as _time
import logging as _logging

_log = _logging.getLogger("gpt-register.vacuum")

VACUUM_DELETE_RATIO = 0.20
VACUUM_FILE_SIZE_MB = 50

_last_vacuum_time: float = 0.0


def _get_db_path() -> Path:
    return DB_PATH


def _get_deleted_ratio() -> float:
    conn = get_conn()
    try:
        total, deleted = 0, 0
        for tbl in ("emails", "accounts", "logs", "platform_usage"):
            row = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            total += row[0] if row else 0
            try:
                drow = conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE status='deleted'").fetchone()
                deleted += drow[0] if drow else 0
            except Exception:
                pass
        return deleted / max(total, 1)
    finally:
        conn.close()


def vacuum_if_needed(force: bool = False) -> str:
    global _last_vacuum_time
    db_path = _get_db_path()
    if not db_path.exists():
        return "skipped:no_db"
    size_mb = db_path.stat().st_size / 1024 / 1024
    if not force:
        if size_mb < VACUUM_FILE_SIZE_MB:
            return f"skipped:file_size={size_mb:.1f}MB<{VACUUM_FILE_SIZE_MB}MB"
        ratio = _get_deleted_ratio()
        if ratio < VACUUM_DELETE_RATIO:
            return f"skipped:delete_ratio={ratio:.2%}<{VACUUM_DELETE_RATIO:.0%}"
    try:
        conn = get_conn()
        try:
            conn.execute("VACUUM")
            conn.commit()
            _last_vacuum_time = _time.time()
            _log.info("VACUUM 完成（force=%s, size=%.1fMB）", force, size_mb)
            return "ok"
        finally:
            conn.close()
    except Exception as e:
        _log.error("VACUUM 失败: %s", e)
        return f"error:{e}"


def get_last_vacuum_time() -> float:
    return _last_vacuum_time


init_db()
