from __future__ import annotations

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from services.db import (
    add_log, get_pending_emails, get_accounts, get_stats,
    get_setting, set_setting, get_conn, get_latest_task,
)
from services.register_engine import get_engine
from services.email_service import email_service
from services.proxy_service import proxy_service

router = APIRouter()


def _load_config() -> dict[str, Any]:
    from api import load_config
    return load_config()


class ImportEmailsRequest(BaseModel):
    source_url: str = ""


class StartRegisterRequest(BaseModel):
    count: int = 0  # 0 = 全部待处理邮箱


class RegisterControlRequest(BaseModel):
    action: str  # pause / resume / stop


class ClearRequest(BaseModel):
    confirm: str = ""


@router.post("/import-emails")
async def import_emails(req: ImportEmailsRequest) -> dict:
    config = _load_config()
    source_url = req.source_url or config.get("email_source_url", "")
    if not source_url:
        raise HTTPException(400, "邮箱源 URL 未配置")
    result = await email_service.import_emails(source_url)
    return {"success": True, "data": result}


@router.post("/start")
async def start_register(req: StartRegisterRequest, background_tasks: BackgroundTasks) -> dict:
    config = _load_config()
    engine = get_engine(config)
    if engine.is_running:
        raise HTTPException(400, "注册任务已在运行中")

    pending = get_pending_emails(limit=req.count)
    if not pending:
        raise HTTPException(400, "没有待注册的邮箱")

    emails = [
        {
            "email": e["email"],
            "password": e["password"],
            "client_id": e["client_id"],
            "refresh_token": e["refresh_token"],
        }
        for e in pending
    ]

    # 创建任务记录
    conn = get_conn()
    with conn:
        cursor = conn.execute(
            "INSERT INTO tasks (task_type, status, total) VALUES (?, ?, ?)",
            ("batch_register", "running", len(emails)),
        )
        task_id = cursor.lastrowid
    conn.close()

    background_tasks.add_task(engine.run_batch, emails, task_id)
    add_log("info", f"批量注册任务已启动，共 {len(emails)} 个邮箱", {"task_id": task_id})
    return {"success": True, "task_id": task_id, "total": len(emails)}


@router.post("/control")
async def control_register(req: RegisterControlRequest) -> dict:
    config = _load_config()
    engine = get_engine(config)
    if req.action == "pause":
        engine.pause()
    elif req.action == "resume":
        engine.resume()
    elif req.action == "stop":
        engine.stop()
    else:
        raise HTTPException(400, f"不支持的操作: {req.action}")
    return {"success": True, "action": req.action, "is_running": engine.is_running, "is_paused": engine.is_paused}


@router.get("/status")
async def register_status() -> dict:
    config = _load_config()
    engine = get_engine(config)
    stats = get_stats()
    return {
        "is_running": engine.is_running,
        "is_paused": engine.is_paused,
        "stats": stats,
        "proxy_count": proxy_service.count,
        "task": get_latest_task(),
    }


@router.get("/accounts")
async def list_accounts(status: str = "", limit: int = 100, offset: int = 0, search: str = "") -> dict:
    accounts = get_accounts(status=status, limit=limit, offset=offset, search=search)
    total = len(get_accounts(status=status, search=search))
    return {"accounts": accounts, "total": total}


@router.get("/accounts/export")
async def export_accounts() -> dict:
    accounts = get_accounts(status="success")
    tokens = [acc["access_token"] for acc in accounts if acc.get("access_token")]
    return {"tokens": tokens, "count": len(tokens)}


def _backup_before_clear(conn) -> Path | None:
    """清空前把 accounts/emails/tasks 导出为 JSON 备份，保留最近 7 份。

    防止误删后无法恢复：备份落在 data/backups/backup_YYYYMMDD_HHMMSS.json。
    """
    from services.db import DATA_DIR

    backup_dir = DATA_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "accounts": [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()],
        "emails": [dict(r) for r in conn.execute("SELECT * FROM emails").fetchall()],
        "tasks": [dict(r) for r in conn.execute("SELECT * FROM tasks").fetchall()],
    }
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = backup_dir / f"backup_{ts}.json"
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    # 保留最近 7 份，更旧的自动删除
    for old in sorted(backup_dir.glob("backup_*.json"))[:-7]:
        try:
            old.unlink()
        except Exception:
            pass
    return path


@router.post("/clear")
async def clear_data(req: ClearRequest) -> dict:
    """一键清空注册库（注册记录/邮箱池/任务）。需 confirm='clear'，清空前自动备份。"""
    if req.confirm != "clear":
        raise HTTPException(400, "需提供 confirm='clear' 确认后才能清空")
    config = _load_config()
    engine = get_engine(config)
    if engine.is_running:
        raise HTTPException(400, "注册任务运行中，请先停止后再清空")

    conn = get_conn()
    deleted: dict[str, int] = {}
    backup_path = None
    try:
        backup_path = _backup_before_clear(conn)
        with conn:
            for table in ("accounts", "emails", "tasks"):
                cur = conn.execute(f"DELETE FROM {table}")
                deleted[table] = cur.rowcount
    finally:
        conn.close()

    add_log("info", f"数据已清空: {deleted}", {"backup": str(backup_path) if backup_path else None})
    return {
        "success": True,
        "deleted": deleted,
        "backup": str(backup_path) if backup_path else None,
    }


# ─────────────────────────────────────────────
# 导出 / 推送账号到 chatgpt2api
# chatgpt2api 账号池格式: {access_token, refresh_token, id_token, email, password, type, status, created_at}
# OpenAI 账号没有密码（邮箱+OTP 注册），长期凭证是 refresh_token；chatgpt2api 导入后会自动刷新。
# ─────────────────────────────────────────────

CHATGPT2API_CLIENT_ID = "app_2SKx67EdpoN0G6j64rFvigXD"


def _refresh_oauth(refresh_token: str) -> dict | None:
    """用 OpenAI OAuth refresh_token 刷新，拿到标准 JWT access_token + id_token。"""
    try:
        resp = httpx.post(
            "https://auth.openai.com/oauth/token",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CHATGPT2API_CLIENT_ID,
            },
            timeout=60,
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("access_token"):
            return {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", refresh_token),
                "id_token": data.get("id_token", ""),
            }
        return None
    except Exception:
        return None


def _build_chatgpt2api_account(acc: dict, token_data: dict) -> dict:
    return {
        "access_token": token_data["access_token"],
        "refresh_token": token_data["refresh_token"],
        "id_token": token_data.get("id_token", ""),
        "email": acc.get("email", ""),
        "password": acc.get("password", ""),
        "type": "free",
        "status": "正常",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


# 导出时 OAuth 并发刷新上限（避免大量账号时打爆 OpenAI 限流）
_EXPORT_REFRESH_CONCURRENCY = 5


async def _collect_export_accounts() -> list[dict]:
    """读取成功账号，导出为 chatgpt2api 格式（并发刷新 refresh_token）。

    新版注册账号带有 openai_refresh_token（OAuth PKCE 获取），可刷新拿到最新三件套；
    老账号只有 access_token（标准 JWT），直接导出，有效期内可被 chatgpt2api 直接使用。
    刷新用 asyncio.gather + Semaphore 限流，单账号失败不阻塞整体。
    """
    all_accounts = get_accounts()
    accounts: list[dict] = []
    to_refresh: list[tuple[dict, str]] = []
    for acc in all_accounts:
        if acc.get("status") not in ("success", "success_no_token"):
            continue
        ort = acc.get("openai_refresh_token") or ""
        at = acc.get("access_token") or ""
        if ort:
            to_refresh.append((acc, ort))
        elif at.startswith("eyJ") and len(at) > 200:
            accounts.append(_build_chatgpt2api_account(acc, {
                "access_token": at,
                "refresh_token": "",
                "id_token": "",
            }))

    sem = asyncio.Semaphore(_EXPORT_REFRESH_CONCURRENCY)

    async def _refresh_one(pair: tuple[dict, str]) -> dict | None:
        acc, ort = pair
        async with sem:
            return await asyncio.to_thread(_refresh_oauth, ort)

    results = await asyncio.gather(*(_refresh_one(p) for p in to_refresh), return_exceptions=True)
    for (acc, _ort), td in zip(to_refresh, results):
        if isinstance(td, dict) and td.get("access_token", "").startswith("eyJ"):
            accounts.append(_build_chatgpt2api_account(acc, td))
    return accounts


def _get_chatgpt2api_admin_key() -> str:
    """获取 chatgpt2api 管理密钥：优先本项目 config，其次读 chatgpt2api 自身 config。"""
    config = _load_config()
    key = str(config.get("chatgpt2api_admin_key") or "").strip()
    if key:
        return key
    try:
        c2 = Path(__file__).resolve().parent.parent.parent / "chatgpt2api" / "config.json"
        if c2.exists():
            d = json.loads(c2.read_text(encoding="utf-8"))
            key = str(d.get("auth-key") or d.get("auth_key") or "").strip()
    except Exception:
        pass
    return key or "chatgpt2api"


@router.post("/export-accounts")
async def export_accounts_chatgpt2api() -> dict:
    """一键导出所有成功账号（并发刷新 token 后，chatgpt2api 格式）。"""
    try:
        accounts = await _collect_export_accounts()
    except Exception as e:
        add_log("error", f"导出账号失败: {e}")
        return {"success": False, "error": str(e), "accounts": []}
    add_log("info", f"导出账号到 chatgpt2api 格式: {len(accounts)} 个")
    return {"success": True, "accounts": accounts, "total": len(accounts)}


@router.post("/export-credentials")
async def export_credentials() -> dict:
    """一键导出所有账号密码清单（按邮箱去重）。格式: 邮箱----密码，每行一个。"""
    all_accounts = get_accounts()
    seen: set[str] = set()
    accounts: list[dict] = []
    for a in all_accounts:
        email = (a.get("email") or "").strip()
        if not email or email in seen:
            continue
        seen.add(email)
        accounts.append({
            "email": email,
            "password": a.get("password") or "",
            "openai_password": a.get("openai_password") or "",
            "client_id": a.get("client_id") or "",
            "refresh_token": a.get("refresh_token") or "",
            "access_token": a.get("access_token") or "",
            "name": a.get("name") or "",
            "birthdate": a.get("birthdate") or "",
            "status": a.get("status") or "",
            "registered_at": a.get("registered_at"),
        })
    # 账号密码清单优先用 OpenAI 密码（可登录 OpenAI），无则回退微软邮箱密码
    text = "\n".join(
        f"{a['email']}----{(a.get('openai_password') or a.get('password') or '')}" for a in accounts
    )
    return {"success": True, "accounts": accounts, "total": len(accounts), "text": text}


@router.post("/push-chatgpt2api")
async def push_chatgpt2api() -> dict:
    """把成功账号（含 refresh_token）推送导入到 chatgpt2api 账号池，自动去重 + 自动刷新。"""
    accounts = await _collect_export_accounts()
    if not accounts:
        return {"success": False, "error": "没有可推送的账号（可能 refresh_token 全部失效）", "accounts": []}

    config = _load_config()
    base_url = str(config.get("chatgpt2api_url") or "http://127.0.0.1:23456").rstrip("/")
    url = f"{base_url}/api/accounts"
    key = _get_chatgpt2api_admin_key()

    try:
        resp = httpx.post(
            url,
            json={"accounts": accounts},
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            timeout=120,
        )
        result = resp.json() if resp.content else {}
        add_log(
            "info",
            f"推送 {len(accounts)} 个账号到 chatgpt2api: HTTP {resp.status_code}",
            {"result": result},
        )
        return {
            "success": resp.status_code < 300,
            "status": resp.status_code,
            "pushed": len(accounts),
            "result": result,
        }
    except Exception as e:
        add_log("error", f"推送 chatgpt2api 失败: {e}")
        return {"success": False, "error": str(e), "pushed": 0}
