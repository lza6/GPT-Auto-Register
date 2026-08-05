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
