from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import JSONResponse

from api import register as register_router
from api import stats as stats_router
from api import emails as emails_router
from api import logs as logs_router
from api import settings as settings_router
from api import proxies as proxies_router

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

# 默认占位符视为「未配置」→ 不强制鉴权（兼容老部署），同时打日志提示
DEFAULT_AUTH_PLACEHOLDERS = {"", "请修改为你的管理密钥", "change-me"}

# 无需鉴权的 API 白名单（健康检查等）
PUBLIC_API_PATHS = {"/api/healthz"}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {}


def _resolve_auth_key() -> str:
    """读取 auth_key：优先环境变量 GPT_REGISTER_AUTH_KEY，其次 config.json。
    占位符视为未配置（返回空串 = 不强制鉴权）。环境变量便于生产注入，无需改配置文件。"""
    key = os.environ.get("GPT_REGISTER_AUTH_KEY") or str(load_config().get("auth_key") or "").strip()
    return "" if key in DEFAULT_AUTH_PLACEHOLDERS else key


def _resolve_auth_enforced() -> bool:
    """读取 auth_enforced：优先环境变量 GPT_REGISTER_AUTH_ENFORCED，其次 config。
    兼容布尔或字符串 'true'/'false'（settings API 存字符串）。"""
    env = os.environ.get("GPT_REGISTER_AUTH_ENFORCED")
    v = env if env is not None else load_config().get("auth_enforced", False)
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return False


class AuthKeyMiddleware:
    """极简鉴权：``/api/*`` 请求必须携带 ``X-Auth-Key`` 且等于 config.auth_key。

    - 未配置 auth_key（或占位符）时全部放行，保证老部署不中断
    - 健康检查与前端静态资源（非 /api 前缀）放行，保证页面可加载
    """

    def __init__(self, app, auth_key: str) -> None:
        self.app = app
        self.auth_key = auth_key

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not self.auth_key:
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        # 放行健康检查与前端静态资源
        if path in PUBLIC_API_PATHS or not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return
        provided = ""
        for key, value in scope.get("headers", []):
            if key == b"x-auth-key":
                provided = value.decode("utf-8", "ignore")
                break
        if provided and provided == self.auth_key:
            await self.app(scope, receive, send)
            return
        response = JSONResponse({"detail": "未授权：缺少或错误的 X-Auth-Key"}, status_code=401)
        await response(scope, receive, send)


def create_app() -> FastAPI:
    config = load_config()
    app = FastAPI(title="GPT 自动注册", version="2.0.0")

    # CORS：前端由本站同源静态服务提供，仅放行本地调试源，关闭凭据通配
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost",
            "http://127.0.0.1",
            "http://localhost:23457",
            "http://127.0.0.1:23457",
        ],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    auth_key = _resolve_auth_key()
    auth_enforced = _resolve_auth_enforced()
    if auth_enforced and not auth_key:
        # fail-fast：管理员声明强制鉴权但密钥未配置/为占位符 → 拒绝启动
        # （避免"以为有鉴权、实际裸奔"的假安全状态）
        raise RuntimeError(
            "config.auth_enforced=true 但 auth_key 未配置或为占位符，拒绝启动以保证安全。"
            "请先在 config.json 设置真实 auth_key。"
        )
    app.add_middleware(AuthKeyMiddleware, auth_key=auth_key)

    @app.get("/api/healthz")
    async def healthz() -> dict:
        db_ok = True
        try:
            from services.db import get_conn
            conn = get_conn()
            conn.execute("SELECT 1")
            conn.close()
        except Exception:
            db_ok = False
        return {
            "status": "ok" if db_ok else "degraded",
            "db": "ok" if db_ok else "error",
            "auth": "enabled" if auth_key else "disabled",
            "auth_enforced": auth_enforced,
        }

    app.include_router(register_router.router, prefix="/api/register", tags=["register"])
    app.include_router(stats_router.router, prefix="/api/stats", tags=["stats"])
    app.include_router(emails_router.router, prefix="/api/emails", tags=["emails"])
    app.include_router(logs_router.router, prefix="/api/logs", tags=["logs"])
    app.include_router(settings_router.router, prefix="/api/settings", tags=["settings"])
    app.include_router(proxies_router.router, prefix="/api/proxies", tags=["proxies"])

    # 静态文件（前端）
    web_dist = Path(__file__).resolve().parent.parent / "web_dist"
    if web_dist.exists():
        app.mount("/", StaticFiles(directory=str(web_dist), html=True), name="static")

    return app
