from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.models import SettingsResponse

from services.db import get_setting, set_setting

router = APIRouter()

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


class SettingsUpdateRequest(BaseModel):
    key: str
    value: str


# 允许通过设置接口修改的配置白名单。
# 白名单外（尤其 auth_key）禁止写入，防止配置注入 / 覆盖鉴权密钥。
ALLOWED_SETTINGS_KEYS = {
    "email_source_url",
    "register_interval_sec",
    "otp_wait_timeout_sec",
    "otp_poll_interval_sec",
    "batch_size",
    "chatgpt2api_url",
    "chatgpt2api_admin_key",
    "protocol_first",
    "use_browser",
    "use_oauth_pkce",
    "register_concurrency",
    "email_api_base",
    "user_agent",
    "auth_enforced",
    "browser_pool_size",
    "token_refresh_enabled",
    "token_refresh_interval_sec",
    "cf_retry_max",
}


def _coerce_value(key: str, value: str):
    """按 config_schema 把字符串值转为正确类型，供写入 config.json（消除脏数据告警）。

    仅转换能可靠识别的值：int 键收纯数字字符串转 int；bool 键收 true/false 等转布尔。
    无法识别的原样返回（交给 config_schema warn-only 校验提示，不静默吞）。
    DB settings 表仍存原始字符串（get_setting 返回 str 的契约不变）。
    """
    try:
        from services.config_schema import _CONFIG_SCHEMA
        if key in _CONFIG_SCHEMA:
            expected = _CONFIG_SCHEMA[key][0]  # 期望类型元组
            sval = str(value).strip()
            if int in expected and sval.lstrip("-").isdigit():
                return int(sval)
            if bool in expected:
                low = sval.lower()
                if low in ("1", "true", "yes", "on"):
                    return True
                if low in ("0", "false", "no", "off"):
                    return False
    except Exception:
        pass
    return value


def _mask_sensitive(config: dict) -> dict:
    """掩码敏感配置值，避免 auth_key / 管理密钥明文暴露到前端。

    只对以 key/password/secret 结尾的键掩码；auth_enforced 是布尔开关，不掩码（否则前端读不回）。
    """
    out = {}
    for k, v in config.items():
        low = str(k).lower()
        if low == "auth_enforced":
            out[k] = v
        elif low.endswith(("key", "password", "secret")) and isinstance(v, str) and v:
            out[k] = "******"
        else:
            out[k] = v
    return out


@router.get("/", summary="获取所有设置", description="返回当前所有配置项（敏感字段已掩码）。")
async def get_all_settings() -> dict:
    config = {}
    if CONFIG_PATH.exists():
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return _mask_sensitive(config)


@router.post("/", summary="更新设置项", response_model=SettingsResponse,
            description="更新单个配置项，同时写入 DB 和 config.json。仅允许修改白名单内的键。")
async def update_setting(req: SettingsUpdateRequest) -> dict:
    key = req.key.strip()
    if key not in ALLOWED_SETTINGS_KEYS:
        raise HTTPException(400, f"不允许修改的配置项: {key}")
    # 按 config_schema 转为正确类型再存 DB（消除 DB 脏数据）
    coerced = _coerce_value(key, req.value)
    set_setting(key, str(coerced))
    # 同步更新 config.json（按 config_schema 转为正确类型，避免 int/bool 被存成字符串触发告警）
    config = {}
    if CONFIG_PATH.exists():
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    config[key] = coerced
    CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"success": True}


@router.get("/config", summary="获取 config.json", description="返回原始 config.json 内容（敏感字段已掩码）。")
async def get_config() -> dict:
    if CONFIG_PATH.exists():
        return _mask_sensitive(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    return {}


@router.post("/vacuum", summary="手动 VACUUM", description="手动触发数据库 VACUUM 回收空间。")
async def trigger_vacuum() -> dict:
    """手动触发数据库 VACUUM。"""
    from services.db import vacuum_if_needed

    result = vacuum_if_needed(force=True)
    return {"status": result}
