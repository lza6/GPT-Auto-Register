from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api import register as register_router
from api import stats as stats_router
from api import emails as emails_router
from api import logs as logs_router
from api import settings as settings_router
from api import proxies as proxies_router

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {}


def create_app() -> FastAPI:
    config = load_config()
    app = FastAPI(title="GPT 自动注册", version="1.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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
