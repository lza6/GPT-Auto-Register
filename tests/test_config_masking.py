"""config 脱敏：/settings/config 不得泄露密钥类字段。"""
from __future__ import annotations


class TestConfigMasking:
    def test_config_masks_auth_key(self, client):
        resp = client.get("/api/settings/config", headers={"X-Auth-Key": "test-admin-key"})
        assert resp.status_code == 200
        cfg = resp.json()
        assert cfg["auth_key"] == "******"

    def test_config_keeps_non_sensitive(self, client):
        resp = client.get("/api/settings/config", headers={"X-Auth-Key": "test-admin-key"})
        cfg = resp.json()
        assert cfg["port"] == 23457
