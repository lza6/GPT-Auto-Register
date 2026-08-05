"""api/register — start/control/import/export-credentials/status 测试。"""
from __future__ import annotations

from services import db
from services.register_engine import RegisterEngine

HDR = {"X-Auth-Key": "test-admin-key"}


class TestRegisterApi:
    def test_status_returns_shape(self, client):
        resp = client.get("/api/register/status", headers=HDR)
        assert resp.status_code == 200
        body = resp.json()
        assert "is_running" in body
        assert "is_paused" in body
        assert "stats" in body
        assert "proxy_count" in body

    async def test_start_no_pending_returns_400(self, client):
        resp = client.post("/api/register/start", json={"count": 0}, headers=HDR)
        assert resp.status_code == 400

    async def test_start_creates_task(self, client, isolated_db, monkeypatch):
        db.insert_email("a@b.com", "p", "c", "r")

        async def fake_run_batch(self, emails, task_id):
            return {"total": len(emails), "completed": 0, "failed": 0, "skipped": 0}

        monkeypatch.setattr(RegisterEngine, "run_batch", fake_run_batch)
        resp = client.post("/api/register/start", json={"count": 0}, headers=HDR)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["task_id"] > 0

    async def test_start_rejects_when_running(self, client, isolated_db, monkeypatch):
        db.insert_email("a@b.com", "p", "c", "r")
        engine = RegisterEngine({})
        engine._running = True
        from services import register_engine as re_mod
        monkeypatch.setattr(re_mod, "register_engine", engine)
        resp = client.post("/api/register/start", json={"count": 0}, headers=HDR)
        assert resp.status_code == 400

    def test_control_all_actions(self, client):
        for action in ("pause", "resume", "stop"):
            resp = client.post("/api/register/control", json={"action": action}, headers=HDR)
            assert resp.status_code == 200, action
        resp = client.post("/api/register/control", json={"action": "nope"}, headers=HDR)
        assert resp.status_code == 400

    async def test_import_emails(self, client, monkeypatch):
        from services import email_service as es_mod

        async def fake_import(source_url):
            return {"inserted": 2, "skipped": 0, "total": 2}

        monkeypatch.setattr(es_mod.email_service, "import_emails", fake_import)
        resp = client.post("/api/register/import-emails", json={"source_url": "x"}, headers=HDR)
        assert resp.status_code == 200
        assert resp.json()["data"]["inserted"] == 2

    def test_export_credentials_password_priority(self, client, isolated_db):
        db.insert_account(email="a@b.com", password="p", client_id="c", refresh_token="r",
                          status="success", access_token="eyJx", openai_password="opw1")
        db.insert_account(email="b@c.com", password="p2", client_id="c", refresh_token="r",
                          status="success", access_token="eyJy")
        resp = client.post("/api/register/export-credentials", json={}, headers=HDR)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert "a@b.com----opw1" in body["text"]   # 优先 OpenAI 密码
        assert "b@c.com----p2" in body["text"]      # 无 OpenAI 密码则回退微软密码

    def test_export_tokens(self, client, isolated_db):
        db.insert_account(email="a@b.com", password="p", client_id="c", refresh_token="r",
                          status="success", access_token="eyJat")
        resp = client.get("/api/register/accounts/export", headers=HDR)
        assert resp.status_code == 200
        assert resp.json()["count"] == 1
