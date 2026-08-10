"""控制台鉴权 + CORS + 健康检查 — 单元测试（TestClient 隔离 DB/config）。"""
from __future__ import annotations

import json

import pytest

from fastapi.testclient import TestClient


class TestAuthMiddleware:
    def test_healthz_is_public(self, client):
        resp = client.get("/api/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["auth"] == "enabled"
        # D3: healthz 新增字段
        assert "db" in body
        assert "cf_solver" in body
        assert "browser_pool_size" in body
        assert "version" in body

    def test_healthz_version_matches_app(self, client):
        """healthz 的 version 必须与 FastAPI app.version 一致（单一版本源，防两处漂移）。"""
        body = client.get("/api/healthz").json()
        assert body["version"] == client.app.version
        # semver 格式（x.y.z）
        import re
        assert re.match(r"^\d+\.\d+\.\d+$", body["version"])

    def test_static_frontend_served(self, client):
        """v3.1 T9：拆分后的前端三文件均可加载（无 404），且 app.css 无残留 </style>。"""
        assert client.get("/").status_code == 200
        js = client.get("/app.js")
        css = client.get("/app.css")
        assert js.status_code == 200
        assert css.status_code == 200
        assert "loadTokenHealth" in js.text  # T2 已接入
        reg_js = client.get("/register.js")
        assert reg_js.status_code == 200
        assert "checkProxyHealth" in reg_js.text  # T3 已接入（拆分后位于 register.js）
        assert "</style>" not in css.text  # 拆分残留已清理

    def test_sensitive_endpoint_requires_key(self, client):
        resp = client.get("/api/register/status")
        assert resp.status_code == 401

    def test_correct_key_allowed(self, client):
        resp = client.get("/api/register/status", headers={"X-Auth-Key": "test-admin-key"})
        assert resp.status_code == 200

    def test_wrong_key_rejected(self, client):
        resp = client.get("/api/register/status", headers={"X-Auth-Key": "wrong-key"})
        assert resp.status_code == 401

    def test_clear_requires_key(self, client):
        resp = client.post(
            "/api/register/clear", json={"confirm": "clear"}, headers={"X-Auth-Key": "test-admin-key"}
        )
        assert resp.status_code == 200

    def test_clear_rejected_without_key(self, client):
        resp = client.post("/api/register/clear", json={"confirm": "clear"})
        assert resp.status_code == 401

    def test_static_page_loads_without_key(self, client):
        resp = client.get("/")
        assert resp.status_code in (200, 404)


class TestAuthEnforced:
    """auth_enforced=true 时：占位符密钥必须 fail-fast，真实密钥必须严格鉴权。"""

    def _app_with_config(self, monkeypatch, isolated_db, config: dict):
        import api as api_init

        cfg = isolated_db / "config.json"
        cfg.write_text(json.dumps(config), encoding="utf-8")
        monkeypatch.setattr(api_init, "CONFIG_PATH", cfg)
        return api_init

    def test_enforced_with_placeholder_raises(self, monkeypatch, isolated_db):
        api_mod = self._app_with_config(
            monkeypatch, isolated_db, {"auth_enforced": True, "auth_key": "请修改为你的管理密钥"}
        )
        with pytest.raises(RuntimeError, match="auth_enforced"):
            api_mod.create_app()

    def test_enforced_with_empty_key_raises(self, monkeypatch, isolated_db):
        api_mod = self._app_with_config(
            monkeypatch, isolated_db, {"auth_enforced": True, "auth_key": ""}
        )
        with pytest.raises(RuntimeError, match="auth_enforced"):
            api_mod.create_app()

    def test_enforced_with_real_key_strict(self, monkeypatch, isolated_db):
        api_mod = self._app_with_config(
            monkeypatch, isolated_db, {"auth_enforced": True, "auth_key": "secret-123"}
        )
        app = api_mod.create_app()
        with TestClient(app) as c:
            body = c.get("/api/healthz").json()
            assert body["auth_enforced"] is True
            # 无密钥访问敏感端点被拒
            assert c.get("/api/register/status").status_code == 401
            # 正确密钥放行
            resp = c.get("/api/register/status", headers={"X-Auth-Key": "secret-123"})
            assert resp.status_code == 200

    def test_enforced_string_true_parsed(self, monkeypatch, isolated_db):
        # settings API 存字符串 'true'，必须解析为强制
        api_mod = self._app_with_config(
            monkeypatch, isolated_db, {"auth_enforced": "true", "auth_key": "secret-123"}
        )
        app = api_mod.create_app()
        with TestClient(app) as c:
            assert c.get("/api/register/status").status_code == 401


class TestCors:
    def test_no_wildcard_origin(self, client):
        # 恶意跨域源不应拿到通配 CORS
        resp = client.get("/api/healthz", headers={"Origin": "http://evil.example.com"})
        assert resp.headers.get("access-control-allow-origin") != "*"
