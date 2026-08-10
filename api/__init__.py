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
PUBLIC_API_PATHS = {"/api/healthz", "/metrics", "/docs", "/openapi.json", "/redoc"}


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


def _resolve_token_refresh_enabled(config: dict) -> bool:
    """读取 token_refresh_enabled：默认 false（避免与 chatgpt2api 自动刷新冲突）。
    兼容布尔或字符串。"""
    env = os.environ.get("GPT_REGISTER_TOKEN_REFRESH_ENABLED")
    v = env if env is not None else config.get("token_refresh_enabled", False)
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _check_cf_solver() -> str:
    """探测 CF Solver (:8001) 是否 Listen。返回 ok/unknown。"""
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        result = s.connect_ex(("127.0.0.1", 8001))
        s.close()
        return "ok" if result == 0 else "unknown"
    except Exception:
        return "unknown"


def _get_browser_pool_size() -> int:
    """返回当前 camoufox 浏览器池实例数（未初始化 0）。

    v3.0：替代原固定返回 1 的逻辑，反映真实池大小。
    max_size=0（未启用池化）时返回 0，保持原按需启停行为不变。
    """
    try:
        from services.browser_pool import pool_size_for_healthz
        return pool_size_for_healthz()
    except Exception:
        return 0


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
    app_version = str(config.get("version", "3.4.0"))
    app = FastAPI(title="GPT 自动注册", version=app_version)

    # B15: Prometheus 指标端点（需在 AuthKeyMiddleware 之前注册，放行白名单）
    from services.metrics import metrics_endpoint
    app.add_route("/metrics", metrics_endpoint, ["GET"])

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

    @app.get("/api/healthz", summary="健康检查", description="返回系统各组件健康状态：DB、CF Solver、浏览器池、代理池、邮件API、鉴权状态和版本号。")
    async def healthz() -> dict:
        # DB 可达性
        db_ok = True
        db_error = ""
        try:
            from services.db import get_conn
            conn = get_conn()
            conn.execute("SELECT 1")
            conn.close()
        except Exception as e:
            db_ok = False
            db_error = str(e)[:50]
        # CF Solver 状态（:8001 端口是否 Listen）
        cf_status = _check_cf_solver()
        # 浏览器池实例数（camoufox 注册用，未初始化时 0）
        browser_pool_size = _get_browser_pool_size()
        # 代理池可用代理数
        proxy_pool_available = 0
        try:
            from services.proxy_service import proxy_service
            proxy_pool_available = proxy_service.count
        except Exception:
            pass
        # 邮件API 可达性（探测 98faka 或 Outlook 端点，5s 超时）
        email_api_reachable = "unknown"
        try:
            import httpx
            import asyncio
            email_api_base = str(config.get("email_api_base", "") or "").strip()
            if email_api_base:
                probe_url = email_api_base.rstrip("/") + "/"
                r = httpx.get(probe_url, timeout=5)
                email_api_reachable = "ok" if r.status_code < 500 else "error"
            else:
                # 回退探测 Outlook 端点
                r = httpx.get("https://outlook.live.com", timeout=5)
                email_api_reachable = "ok" if r.status_code < 500 else "error"
        except Exception:
            email_api_reachable = "unreachable"

        # 用 config 中的版本号（而非 hardcoded）
        version = str(config.get("version", "3.4.0"))
        all_ok = db_ok and cf_status in ("ok", "unknown")
        return {
            "status": "ok" if all_ok else "degraded",
            "db": "ok" if db_ok else "error",
            "db_error": db_error,
            "cf_solver": cf_status,
            "browser_pool_size": browser_pool_size,
            "proxy_pool_available": proxy_pool_available,
            "email_api_reachable": email_api_reachable,
            "auth": "enabled" if auth_key else "disabled",
            "auth_enforced": auth_enforced,
            "version": version,
        }

    # 优雅停机：uvicorn shutdown 时关浏览器池 + CF solver
    @app.on_event("shutdown")
    async def shutdown_handler() -> None:
        import inspect
        import logging
        log = logging.getLogger("gpt-register")

        # 浏览器池清理（H3 池化后池持有持久实例；未池化时无实例可清，仅打日志）
        try:
            from services.browser_register import browser_register
            if browser_register is not None:
                # 池化后浏览器注册实例可能暴露 cleanup()；未池化时安全跳过
                cleanup_fn = getattr(browser_register, "cleanup", None)
                if cleanup_fn is not None:
                    log.info("shutdown: 清理浏览器实例...")
                    if inspect.iscoroutinefunction(cleanup_fn):
                        await cleanup_fn()
                    else:
                        cleanup_fn()
                else:
                    log.info("shutdown: 浏览器注册为按需启停模式，无需清理持久实例")
        except Exception as e:
            log.warning(f"shutdown: 浏览器清理异常: {e}")

        # CF Solver 停止：cf_solver_service.stop 是 async 方法，用 iscoroutinefunction 防御
        # （原写法 `await x.stop() if hasattr(...) else None` 在 stop 不存在时 await None 会 crash）
        try:
            from services.cf_solver_service import cf_solver_service
            if cf_solver_service is not None:
                stop_fn = getattr(cf_solver_service, "stop", None)
                if stop_fn is not None:
                    if inspect.iscoroutinefunction(stop_fn):
                        await stop_fn()
                    else:
                        stop_fn()
                    log.info("shutdown: CF Solver 已停止")
        except Exception as e:
            log.warning(f"shutdown: CF Solver 停止异常: {e}")

        # token 巡检任务停止（v3.0 G1）
        try:
            from services.token_refresher import token_refresher
            if token_refresher is not None and token_refresher.is_running:
                log.info("shutdown: 停止 token 巡检任务...")
                await token_refresher.stop()
        except Exception as e:
            log.warning(f"shutdown: token 巡检停止异常: {e}")

        # B9：停止代理池自动清理后台线程
        try:
            from services.proxy_service import proxy_service
            proxy_service.stop_cleanup()
            log.info("shutdown: 代理池自动清理已停止")
        except Exception as e:
            log.warning(f"shutdown: 代理池自动清理停止异常: {e}")

    # token 保鲜巡检：startup 事件（async 上下文）内启动后台任务（v3.0 G1）
    # 注意：不能在 create_app 同步执行，create_task 需 running event loop
    token_refresh_enabled = _resolve_token_refresh_enabled(config)

    @app.on_event("startup")
    async def startup_handler() -> None:
        import logging
        log = logging.getLogger("gpt-register")
        # 启动代理池自动清理（B9）
        try:
            from services.proxy_service import proxy_service
            proxy_service.start_cleanup()
            log.info("代理池自动清理后台线程已启动（每 30min 检查一次）")
        except Exception as e:
            log.warning(f"代理池自动清理启动失败: {e}")
        if token_refresh_enabled:
            try:
                from services.token_refresher import get_token_refresher
                get_token_refresher(config).start()  # startup 内有 running loop
                log.info("token 巡检任务已启动")
            except Exception as e:
                log.warning(f"token 巡检启动失败: {e}")

        # B16: 每日凌晨 4 点定时 VACUUM
        async def _vacuum_loop() -> None:
            import asyncio
            import time as _time
            _log = logging.getLogger("gpt-register.vacuum")
            while True:
                now = _time.localtime()
                seconds_till_4am = ((4 - now.tm_hour + 24) % 24) * 3600 - now.tm_min * 60 - now.tm_sec
                if seconds_till_4am <= 0:
                    seconds_till_4am += 86400
                await asyncio.sleep(seconds_till_4am)
                from services.db import vacuum_if_needed
                result = vacuum_if_needed()
                _log.info("定时 VACUUM: %s", result)

        import asyncio
        asyncio.create_task(_vacuum_loop())

    app.include_router(register_router.router, prefix="/api/register", tags=["register"])
    app.include_router(stats_router.router, prefix="/api/stats", tags=["stats"])
    app.include_router(emails_router.router, prefix="/api/emails", tags=["emails"])
    app.include_router(logs_router.router, prefix="/api/logs", tags=["logs"])
    app.include_router(settings_router.router, prefix="/api/settings", tags=["settings"])
    app.include_router(proxies_router.router, prefix="/api/proxies", tags=["proxies"])

    # v3.1.2：静态资源 no-cache，强制浏览器每次校验拉取最新 UI（git pull 后无需强刷/清缓存）
    @app.middleware("http")
    async def _static_no_cache(request, call_next):
        resp = await call_next(request)
        path = request.url.path
        if path == "/" or path.endswith((".html", ".js", ".css")):
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp

    # 静态文件（前端）
    web_dist = Path(__file__).resolve().parent.parent / "web_dist"
    if web_dist.exists():
        app.mount("/", StaticFiles(directory=str(web_dist), html=True), name="static")

    return app
