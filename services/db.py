from __future__ import annotations

import json
import sqlite3
import time
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
    conn.close()


def add_log(level: str, message: str, data: Any = None) -> None:
    conn = get_conn()
    with conn:
        conn.execute(
            "INSERT INTO logs (level, message, data, created_at) VALUES (?, ?, ?, ?)",
            (level, message, json.dumps(data, ensure_ascii=False) if data else None, time.time()),
        )
    conn.close()


def get_logs(limit: int = 100, offset: int = 0) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM logs ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def insert_email(email: str, password: str, client_id: str, refresh_token: str) -> bool:
    conn = get_conn()
    try:
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO emails (email, password, client_id, refresh_token) VALUES (?, ?, ?, ?)",
                (email, password, client_id, refresh_token),
            )
        return True
    except Exception:
        return False
    finally:
        conn.close()


def get_pending_emails(limit: int = 0) -> list[dict]:
    conn = get_conn()
    sql = "SELECT * FROM emails WHERE status = 'pending' ORDER BY id ASC"
    if limit > 0:
        sql += f" LIMIT {limit}"
    rows = conn.execute(sql).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_email_status(email: str, status: str) -> None:
    conn = get_conn()
    with conn:
        conn.execute(
            "UPDATE emails SET status = ?, used_at = ? WHERE email = ?",
            (status, time.time() if status != 'pending' else None, email),
        )
    conn.close()


def insert_account(email: str, password: str, client_id: str, refresh_token: str,
                   access_token: str = "", name: str = "", birthdate: str = "",
                   proxy: str = "", status: str = "pending", error: str = "") -> None:
    conn = get_conn()
    with conn:
        conn.execute(
            """INSERT OR REPLACE INTO accounts
               (email, password, client_id, refresh_token, access_token, name, birthdate, proxy, status, error, registered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (email, password, client_id, refresh_token, access_token, name, birthdate, proxy, status, error,
             time.time() if status == 'success' else None),
        )
    conn.close()


def get_accounts(status: str = "", limit: int = 0, offset: int = 0) -> list[dict]:
    conn = get_conn()
    sql = "SELECT * FROM accounts"
    params: list = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY id DESC"
    if limit > 0:
        sql += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_accounts(status: str = "") -> int:
    conn = get_conn()
    if status:
        row = conn.execute("SELECT COUNT(*) as c FROM accounts WHERE status = ?", (status,)).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) as c FROM accounts").fetchone()
    conn.close()
    return row["c"] if row else 0


def count_emails(status: str = "") -> int:
    conn = get_conn()
    if status:
        row = conn.execute("SELECT COUNT(*) as c FROM emails WHERE status = ?", (status,)).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) as c FROM emails").fetchone()
    conn.close()
    return row["c"] if row else 0


def get_stats() -> dict:
    return {
        "emails_total": count_emails(),
        "emails_pending": count_emails("pending"),
        "emails_used": count_emails("used"),
        "accounts_total": count_accounts(),
        "accounts_success": count_accounts("success"),
        "accounts_failed": count_accounts("failed"),
        "accounts_skipped": count_accounts("skipped"),
        "accounts_pending": count_accounts("pending"),
        "accounts_registering": count_accounts("registering"),
    }


def get_setting(key: str, default: str = "") -> str:
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    conn = get_conn()
    with conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.close()


init_db()
