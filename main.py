from __future__ import annotations

import json
import logging
import logging.handlers
import os
from pathlib import Path

import uvicorn

from api import create_app
from services.db import reset_stale_tasks

DATA_DIR = Path(__file__).resolve().parent / "data"


def _init_logging() -> None:
    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    for logger_name in ("gpt-register", ""):
        target = logging.getLogger(logger_name)
        if any(isinstance(h, logging.handlers.RotatingFileHandler) for h in target.handlers):
            continue
        handler = logging.handlers.RotatingFileHandler(
            log_dir / "server.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(formatter)
        target.addHandler(handler)


_init_logging()

app = create_app()

# 服务启动：把上次遗留的 running 任务标记为 interrupted（断点续跑信号）
reset_stale_tasks()


def load_config() -> dict:
    config_path = Path(__file__).resolve().parent / "config.json"
    if config_path.exists():
        return json.loads(config_path.read_text(encoding="utf-8"))
    return {}


def _validate_config(config: dict) -> None:
    """启动时校验关键配置，缺失/占位符打印醒目警告（不阻断启动）。

    v3.0：接入 services.config_schema 做结构化类型校验，
    堵住 settings API 把数字存为字符串的脏数据源头。
    """
    # 1. 占位符密钥告警（原 v2.x 逻辑保留）
    auth_key = str(config.get("auth_key") or "").strip()
    if not auth_key or auth_key in ("请修改为你的管理密钥", "change-me"):
        print("[WARN] config.json 的 auth_key 未设置（占位符），/api 接口将不鉴权。生产环境请务必设置真实密钥。")
        print("      提示：设置 config.auth_enforced=true 后，占位符密钥将导致启动失败（fail-fast）。")

    # 2. 结构化类型校验（v3.0 P0/P2-6）
    try:
        from services.config_schema import validate_config, format_issues
        issues = validate_config(config)
        if issues:
            print(format_issues(issues))
            print("      提示：数字字段建议改为 int 类型；布尔字段建议直接写 true/false。")
    except Exception:
        pass  # schema 模块自身异常不阻断启动


def _purge_old_logs() -> None:
    """启动时清理超过 log_retention_days 的旧日志（防日志表无限增长）。"""
    try:
        config = load_config()
        days = int(config.get("log_retention_days", 30))
        from services.db import purge_old_logs
        n = purge_old_logs(days)
        if n:
            print(f"[startup] 已清理 {n} 条过期日志（保留 {days} 天）")
    except Exception:
        pass


_validate_config(load_config())
_purge_old_logs()


if __name__ == "__main__":
    config = load_config()
    port = int(os.getenv("GPT_REGISTER_PORT", str(config.get("port", 23457))))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        access_log=False,
        log_level="info",
    )
