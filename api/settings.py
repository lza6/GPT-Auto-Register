from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.db import get_setting, set_setting

router = APIRouter()

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


class SettingsUpdateRequest(BaseModel):
    key: str
    value: str


# 命中这些关键词的配置项视为敏感（密钥/口令），对外脱敏展示
SENSITIVE_KEY_HINTS = ("key", "password", "auth", "secret")


def _mask_sensitive(config: dict) -> dict:
    """掩码敏感配置值，避免 auth_key / 管理密钥明文暴露到前端。"""
    out = {}
    for k, v in config.items():
        if any(hint in str(k).lower() for hint in SENSITIVE_KEY_HINTS) and isinstance(v, str) and v:
            out[k] = "******"
        else:
            out[k] = v
    return out


@router.get("/")
async def get_all_settings() -> dict:
    config = {}
    if CONFIG_PATH.exists():
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return _mask_sensitive(config)


@router.post("/")
async def update_setting(req: SettingsUpdateRequest) -> dict:
    set_setting(req.key, req.value)
    # 同步更新 config.json
    config = {}
    if CONFIG_PATH.exists():
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    config[req.key] = req.value
    CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"success": True}


@router.get("/config")
async def get_config() -> dict:
    if CONFIG_PATH.exists():
        return _mask_sensitive(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    return {}
