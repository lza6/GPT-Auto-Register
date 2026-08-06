"""api/settings — 配置更新白名单测试。"""
from __future__ import annotations

import json

from services.db import get_setting

HDR = {"X-Auth-Key": "test-admin-key"}


class TestSettingsWhitelist:
    def test_reject_unknown_key(self, client):
        resp = client.post("/api/settings/", json={"key": "evil_key", "value": "1"}, headers=HDR)
        assert resp.status_code == 400

    def test_reject_auth_key(self, client):
        # 覆盖 auth_key 会导致鉴权失效，必须禁止
        resp = client.post("/api/settings/", json={"key": "auth_key", "value": "hacked"}, headers=HDR)
        assert resp.status_code == 400

    def test_mask_sensitive_keeps_auth_enforced(self):
        # 布尔开关 auth_enforced 不掩码（否则前端读不回）；密钥类掩码
        from api.settings import _mask_sensitive

        out = _mask_sensitive({
            "auth_key": "secret", "chatgpt2api_admin_key": "k",
            "auth_enforced": True, "protocol_first": "true",
        })
        assert out["auth_key"] == "******"
        assert out["chatgpt2api_admin_key"] == "******"
        assert out["auth_enforced"] is True
        assert out["protocol_first"] == "true"

    def test_allow_whitelisted_key(self, client, isolated_db):
        resp = client.post(
            "/api/settings/",
            json={"key": "register_interval_sec", "value": "15"},
            headers=HDR,
        )
        assert resp.status_code == 200
        assert get_setting("register_interval_sec") == "15"

    def test_allow_new_keys_used_by_frontend(self, client):
        for key in ("protocol_first", "use_browser", "use_oauth_pkce", "register_concurrency"):
            resp = client.post("/api/settings/", json={"key": key, "value": "true"}, headers=HDR)
            assert resp.status_code == 200, f"{key} 应在白名单内"


def _read_cfg() -> dict:
    """读取 conftest client fixture 隔离后的临时 config.json。"""
    import api.settings as api_settings

    return json.loads(api_settings.CONFIG_PATH.read_text(encoding="utf-8"))


class TestSettingsV3Whitelist:
    """v3.1 T4a：4 个 v3.0 新键已入白名单，前端保存不再 400。"""

    def test_new_v3_keys_accepted(self, client):
        for key in ("browser_pool_size", "token_refresh_enabled",
                    "token_refresh_interval_sec", "cf_retry_max"):
            resp = client.post("/api/settings/", json={"key": key, "value": "1"}, headers=HDR)
            assert resp.status_code == 200, f"{key} 应在白名单内"


class TestSettingsCoercion:
    """v3.1 T4b：写入 config.json 按 config_schema 转正确类型，DB 仍存字符串。"""

    def test_int_field_coerced_in_config_json(self, client):
        client.post("/api/settings/", json={"key": "browser_pool_size", "value": "2"}, headers=HDR)
        assert _read_cfg()["browser_pool_size"] == 2  # int，非 "2"

    def test_bool_field_coerced_in_config_json(self, client):
        client.post("/api/settings/", json={"key": "token_refresh_enabled", "value": "true"}, headers=HDR)
        assert _read_cfg()["token_refresh_enabled"] is True
        client.post("/api/settings/", json={"key": "token_refresh_enabled", "value": "false"}, headers=HDR)
        assert _read_cfg()["token_refresh_enabled"] is False

    def test_db_stays_string_but_config_typed(self, client):
        client.post("/api/settings/", json={"key": "token_refresh_interval_sec", "value": "21600"}, headers=HDR)
        assert get_setting("token_refresh_interval_sec") == "21600"  # DB 字符串契约不变
        assert _read_cfg()["token_refresh_interval_sec"] == 21600  # config.json int

    def test_existing_int_key_coerced(self, client):
        # 既有 int 键保存后 config.json 也应为 int（修复历史脏数据源头）
        client.post("/api/settings/", json={"key": "register_interval_sec", "value": "15"}, headers=HDR)
        assert _read_cfg()["register_interval_sec"] == 15
        assert get_setting("register_interval_sec") == "15"  # DB 字符串契约不变

    def test_unrecognizable_passthrough_for_schema_warning(self, client):
        # 无法识别的值原样写入，交给 config_schema warn-only 校验提示（不静默吞/不静默转）
        client.post("/api/settings/", json={"key": "cf_retry_max", "value": "abc"}, headers=HDR)
        assert _read_cfg()["cf_retry_max"] == "abc"
