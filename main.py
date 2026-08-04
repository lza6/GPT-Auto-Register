from __future__ import annotations

import json
import logging
import logging.handlers
import os
from pathlib import Path

import uvicorn

from api import create_app

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


def load_config() -> dict:
    config_path = Path(__file__).resolve().parent / "config.json"
    if config_path.exists():
        return json.loads(config_path.read_text(encoding="utf-8"))
    return {}


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
