"""控制台鉴权 + CORS + 健康检查 — 单元测试（TestClient 隔离 DB/config）。"""
from __future__ import annotations


class TestAuthMiddleware:
    def test_healthz_is_public(self, client):
        resp = client.get("/api/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["auth"] == "enabled"

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
        resp = client.post("/api/register/clear", json={}, headers={"X-Auth-Key": "test-admin-key"})
        assert resp.status_code == 200

    def test_clear_rejected_without_key(self, client):
        resp = client.post("/api/register/clear", json={})
        assert resp.status_code == 401

    def test_static_page_loads_without_key(self, client):
        resp = client.get("/")
        assert resp.status_code in (200, 404)


class TestCors:
    def test_no_wildcard_origin(self, client):
        # 恶意跨域源不应拿到通配 CORS
        resp = client.get("/api/healthz", headers={"Origin": "http://evil.example.com"})
        assert resp.headers.get("access-control-allow-origin") != "*"
