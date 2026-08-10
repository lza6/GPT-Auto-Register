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
    add_log, get_pending_emails, get_accounts, get_stats, get_task_history,
    get_conn, get_latest_task, db_session, count_accounts, requeue_failed_emails,
)
from services.register_engine import get_engine
from services.email_service import email_service
from services.proxy_service import proxy_service

router = APIRouter()


def _load_config() -> dict[str, Any]:
    from api import load_config
    return load_config()

from api.models import (
    ApiResponse,
    RegisterStatusResponse,
    AccountListResponse,
    ClearResponse,
)


class ImportEmailsRequest(BaseModel):
    source_url: str = ""


class StartRegisterRequest(BaseModel):
    count: int = 0  # 0 = 全部待处理邮箱


class RegisterControlRequest(BaseModel):
    action: str  # pause / resume / stop


class ClearRequest(BaseModel):
    confirm: str = ""


class RetryFailedRequest(BaseModel):
    failure_type: str = ""


@router.post("/import-emails", summary="导入邮箱", description="从配置的邮箱源 URL 拉取待注册邮箱。")
async def import_emails(req: ImportEmailsRequest) -> dict:
    config = _load_config()
    source_url = req.source_url or config.get("email_source_url", "")
    if not source_url:
        raise HTTPException(400, "邮箱源 URL 未配置")
    result = await email_service.import_emails(source_url)
    return {"success": True, "data": result}


@router.post("/start", summary="启动批量注册", description="使用待处理邮箱启动批量注册任务。可指定注册数量（0=全部）。返回 task_id 供后续查询。")
async def start_register(req: StartRegisterRequest, background_tasks: BackgroundTasks) -> dict:
    config = _load_config()
    engine = get_engine(config)
    # 原子占位启动，防两个并发 /start 都通过检查、导致同一批邮箱重复注册
    if not engine.try_start():
        raise HTTPException(400, "注册任务已在运行中")

    try:
        pending = get_pending_emails(limit=req.count)
        if not pending:
            engine.abort_start()
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

        # 创建任务记录（db_session 自动提交/回滚/关闭，异常不泄漏连接）
        with db_session() as conn:
            cursor = conn.execute(
                "INSERT INTO tasks (task_type, status, total) VALUES (?, ?, ?)",
                ("batch_register", "running", len(emails)),
            )
            task_id = cursor.lastrowid
    except HTTPException:
        raise  # 业务错误（无待注册邮箱）保持 400
    except Exception:
        engine.abort_start()  # 异常路径释放启动占位，避免后续 /start 永久 400"已在运行中"
        raise

    background_tasks.add_task(engine.run_batch, emails, task_id)
    add_log("info", f"批量注册任务已启动，共 {len(emails)} 个邮箱", {"task_id": task_id})
    return {"success": True, "task_id": task_id, "total": len(emails)}


@router.post("/control", summary="控制注册任务", description="暂停/恢复/停止正在运行的批量注册任务。")
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


@router.get("/status", summary="注册任务状态", response_model=RegisterStatusResponse,
            description="查询当前注册任务运行状态、统计数据、代理数量和最近任务。")
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


@router.get("/accounts", summary="查询账号列表", response_model=AccountListResponse,
            description="分页查询注册成功的账号列表，支持按状态和失败类型筛选，支持邮箱搜索。")
async def list_accounts(status: str = "", limit: int = 100, offset: int = 0, search: str = "",
                        failure_type: str = "") -> dict:
    # v3.1 审计：limit 收敛安全范围（0/负数→默认 100，上限 500），offset 防负
    limit = min(limit, 500) if limit > 0 else 100
    offset = max(0, offset)
    accounts = get_accounts(status=status, limit=limit, offset=offset, search=search,
                            failure_type=failure_type)
    total = count_accounts(status=status, search=search, failure_type=failure_type)  # COUNT 查询，避免全表装载
    return {"accounts": accounts, "total": total}


@router.post("/retry-failed", summary="重试失败账号",
            description="把 failed/cf_blocked 账号对应的邮箱回置为 pending，使其可被 /start 重新注册。可选 failure_type 定向重试。")
async def retry_failed(req: RetryFailedRequest) -> dict:
    """把 failed/cf_blocked 账号对应的邮箱回置为 pending，使其可被 /start 重新注册（v3.4 失败重试闭环）。

    可选 failure_type 定向重试某类失败子集（前端点失败分类徽章时带上）。
    回置 emails.status 的同时清空 accounts.failure_type，确保诊断分类准确。
    """
    config = _load_config()
    engine = get_engine(config)
    if engine.is_running:
        raise HTTPException(400, "注册任务运行中，请先停止后再重试")
    n = requeue_failed_emails(failure_type=req.failure_type.strip())
    add_log("info", f"重试失败账号：{n} 个邮箱已回置为待注册"
            + (f"（类型: {req.failure_type}）" if req.failure_type else ""))
    return {"success": True, "requeued": n, "failure_type": req.failure_type}


@router.get("/accounts/export", summary="导出 Token 清单", description="导出所有成功账号的 access_token 列表，供外部系统直接使用。")
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


@router.post("/clear", summary="清空注册数据", response_model=ClearResponse,
            description="一键清空注册库（注册记录/邮箱池/任务）。需 confirm='clear' 确认，清空前自动备份。")
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


def _resolve_refresh_proxy() -> str | None:
    """解析导出刷新的代理：优先 config.proxy_url，其次代理池（use_proxy 时）。

    与 token_refresher._resolve_proxy 一致——注册链路走代理，导出刷新若不走，
    在「OpenAI 仅能经代理可达」的部署里会全部失败（v3.1 审计修复）。
    """
    config = _load_config()
    proxy_url = str(config.get("proxy_url") or "").strip()
    if proxy_url:
        return proxy_url
    use_proxy = config.get("use_proxy")
    if use_proxy is True or (isinstance(use_proxy, str) and use_proxy.strip().lower() in ("1", "true", "yes", "on")):
        from services.proxy_service import proxy_service
        return proxy_service.get_next()
    return None


def _refresh_oauth(refresh_token: str, proxy_url: str | None = None) -> dict | None:
    """用 OpenAI OAuth refresh_token 刷新，拿到标准 JWT access_token + id_token。走配置代理。"""
    if proxy_url is None:
        proxy_url = _resolve_refresh_proxy()  # 未显式传入时按配置解析（注册链路走代理，导出刷新须一致）
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
            proxy=proxy_url,
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("access_token"):
            return {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", refresh_token),
                "id_token": data.get("id_token", ""),
            }
        add_log("warning", f"导出刷新失败: HTTP {resp.status_code}")
        return None
    except Exception as e:
        # 不再静默吞：导出刷新失败需可排障（v3.1 审计修复）
        add_log("warning", f"导出刷新异常: {type(e).__name__}: {e}")
        return None


def _build_chatgpt2api_account(acc: dict, token_data: dict) -> dict:
    """构建 chatgpt2api 账号载荷。

    字段对齐 chatgpt2api 导入契约（api/accounts.py + services/account_service.py 实证）：
    - password 必须是 **OpenAI 账号密码**（chatgpt2api 凭据登录/重登用），不是微软邮箱密码。
    - mail_credential = {client_id, refresh_token(微软邮箱)}，供 chatgpt2api 凭据过期后 OTP 重登取码。
    """
    payload = {
        "access_token": token_data["access_token"],
        "refresh_token": token_data["refresh_token"],
        "id_token": token_data.get("id_token", ""),
        "email": acc.get("email", ""),
        # 关键修复：OpenAI 账号密码优先（重登用），回退微软邮箱密码
        "password": acc.get("openai_password") or acc.get("password") or "",
        "type": "free",
        "status": "正常",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    # 邮箱取件凭证（client_id + 微软 refresh_token）：chatgpt2api 凭据重登取 OTP 用
    client_id = (acc.get("client_id") or "").strip()
    mail_rt = (acc.get("refresh_token") or "").strip()
    if client_id and mail_rt:
        payload["mail_credential"] = {"client_id": client_id, "refresh_token": mail_rt}
    return payload


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
    # 代理解析一次统一传入：避免每账号重读 config + 反复推进代理池游标（v3.1 审查修复）
    export_proxy = _resolve_refresh_proxy()

    async def _refresh_one(pair: tuple[dict, str]) -> dict | None:
        acc, ort = pair
        async with sem:
            return await asyncio.to_thread(_refresh_oauth, ort, export_proxy)

    results = await asyncio.gather(*(_refresh_one(p) for p in to_refresh), return_exceptions=True)
    for (acc, _ort), td in zip(to_refresh, results):
        if isinstance(td, dict) and td.get("access_token", "").startswith("eyJ"):
            accounts.append(_build_chatgpt2api_account(acc, td))
            # 轮换的新 RT 写回 DB：OpenAI 重用检测会作废旧 RT，不写回则反复导出/巡检会耗尽库存 RT
            new_rt = td.get("refresh_token") or ""
            if new_rt and new_rt != _ort and acc.get("email"):
                _writeback_rotated_tokens(acc["email"], td["access_token"], new_rt)
    return accounts


def _writeback_rotated_tokens(email: str, access_token: str, refresh_token: str) -> None:
    """导出刷新成功后把轮换的新 RT 写回 DB（v3.1 审计修复：防止库存 RT 被重用检测耗尽）。"""
    try:
        with db_session() as conn:
            conn.execute(
                "UPDATE accounts SET access_token = ?, openai_refresh_token = ? WHERE email = ?",
                (access_token, refresh_token, email),
            )
    except Exception as e:
        # 写回失败不影响导出主流程，但必须可排障（否则库存 RT 静默耗尽）
        add_log("warning", f"RT 写回失败 [{email}]: {type(e).__name__}: {e}")


def _get_chatgpt2api_admin_key() -> str:
    """获取 chatgpt2api 管理密钥：优先本项目 config，其次读 chatgpt2api 自身 config。

    不再回退硬编码弱默认凭据（原 'chatgpt2api'）；读不到返回空，由调用方明确提示。
    """
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
    return key


@router.post("/export-accounts", summary="导出账号（chatgpt2api 格式）",
            description="一键导出所有成功账号，并发刷新 OAuth token 后组装为 chatgpt2api 兼容格式。")
async def export_accounts_chatgpt2api() -> dict:
    """一键导出所有成功账号（并发刷新 token 后，chatgpt2api 格式）。"""
    try:
        accounts = await _collect_export_accounts()
    except Exception as e:
        add_log("error", f"导出账号失败: {e}")
        return {"success": False, "error": str(e), "accounts": []}
    add_log("info", f"导出账号到 chatgpt2api 格式: {len(accounts)} 个")
    return {"success": True, "accounts": accounts, "total": len(accounts)}


@router.post("/export-credentials", summary="导出账号密码清单",
            description="一键导出所有账号密码清单（按邮箱去重）。格式: 邮箱----密码，每行一个。")
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


@router.post("/push-chatgpt2api", summary="推送账号到 chatgpt2api",
            description="把成功账号（含 refresh_token）推送导入到 chatgpt2api 账号池，自动去重 + 自动刷新。")
async def push_chatgpt2api() -> dict:
    """把成功账号（含 refresh_token）推送导入到 chatgpt2api 账号池，自动去重 + 自动刷新。"""
    config = _load_config()
    base_url = str(config.get("chatgpt2api_url") or "http://127.0.0.1:23456").rstrip("/")
    url = f"{base_url}/api/accounts"
    key = _get_chatgpt2api_admin_key()
    if not key:
        add_log("error", "推送 chatgpt2api 失败：未配置 chatgpt2api_admin_key")
        return {"success": False, "error": "未配置 chatgpt2api 管理密钥（config.chatgpt2api_admin_key）", "pushed": 0}

    accounts = await _collect_export_accounts()
    if not accounts:
        return {"success": False, "error": "没有可推送的账号（可能 refresh_token 全部失效）", "accounts": []}

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


@router.get("/task-history", summary="任务历史",
            description="获取最近 N 条任务历史记录。")
async def task_history() -> dict:
    return {"history": get_task_history(limit=50)}


@router.post("/replenish-tokens", summary="一键补齐 Token",
            description="为「有 openai_refresh_token 但缺有效 access_token」的账号刷新补齐 Token。")
async def replenish_tokens() -> dict:
    """一键补齐 Token：为「有 openai_refresh_token 但缺有效 access_token」的账号刷新补齐。

    复用 token_refresher 的刷新链路（走代理 + TLS 校验 + 轮换新 RT 一并落库）。
    无法自动补齐的（连 openai_refresh_token 都没有）如实报为 need_reregister，不假装补齐。
    """
    config = _load_config()
    from services.token_refresher import get_token_refresher
    refresher = get_token_refresher(config)

    with db_session() as conn:
        rows = conn.execute(
            "SELECT email, openai_refresh_token FROM accounts "
            "WHERE status IN ('success','success_no_token') "
            "AND (access_token IS NULL OR access_token = '' OR access_token NOT LIKE 'eyJ%')"
        ).fetchall()
    targets = [dict(r) for r in rows if (r["openai_refresh_token"] or "").strip()]
    no_rt = len(rows) - len(targets)  # 连 refresh_token 都没有 → 无法自动补齐，需重新注册

    if not targets:
        return {"success": True, "replenished": 0, "failed": 0,
                "need_reregister": no_rt, "note": "没有可补齐的账号"}

    sem = asyncio.Semaphore(5)
    result = {"replenished": 0, "failed": 0}

    async def _one(acc: dict) -> None:
        async with sem:
            try:
                tokens = await refresher._refresh_token(acc["openai_refresh_token"])
                if tokens and tokens.get("access_token"):
                    # 轮换的新 RT 一并落库（防库存 RT 被 OpenAI 重用检测耗尽）
                    refresher._update_tokens(acc["email"], tokens["access_token"], tokens.get("refresh_token", ""))
                    result["replenished"] += 1
                else:
                    result["failed"] += 1
            except Exception as e:
                add_log("warning", f"补齐 token [{acc['email']}] 异常: {e}")
                result["failed"] += 1

    await asyncio.gather(*(_one(a) for a in targets))
    add_log("info",
            f"一键补齐 token：补 {result['replenished']}，失败 {result['failed']}，"
            f"无RT需重新注册 {no_rt}，共扫 {len(rows)}")
    return {"success": True, "scanned": len(rows), "need_reregister": no_rt, **result}
