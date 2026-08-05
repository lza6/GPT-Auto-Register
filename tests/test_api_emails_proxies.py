"""api/emails 与 api/proxies — 手动添加/删除/清空、代理池读写测试。"""
from __future__ import annotations

from services import db

import api.proxies as proxies_mod

HDR = {"X-Auth-Key": "test-admin-key"}


class TestEmailsApi:
    def test_manual_add_full_and_email_only(self, client, isolated_db):
        text = "a@b.com----p1----c1----r1\n# 注释\nb@c.com\n"
        resp = client.post("/api/emails/manual-add", json={"text": text}, headers=HDR)
        assert resp.status_code == 200
        body = resp.json()
        assert body["inserted"] == 2
        assert body["skipped"] == 0

    def test_manual_add_empty_rejected(self, client):
        resp = client.post("/api/emails/manual-add", json={"text": "   "}, headers=HDR)
        assert resp.status_code == 400

    def test_delete_email_and_404(self, client, isolated_db):
        db.insert_email("a@b.com", "p", "c", "r")
        conn = db.get_conn()
        eid = conn.execute("SELECT id FROM emails WHERE email='a@b.com'").fetchone()["id"]
        conn.close()
        resp = client.delete(f"/api/emails/{eid}", headers=HDR)
        assert resp.status_code == 200
        resp2 = client.delete(f"/api/emails/{eid}", headers=HDR)
        assert resp2.status_code == 404

    def test_clear_emails(self, client, isolated_db):
        db.insert_email("a@b.com", "p", "c", "r")
        resp = client.post("/api/emails/clear", headers=HDR)
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 1

    def test_pending_endpoint(self, client, isolated_db):
        db.insert_email("a@b.com", "p", "c", "r")
        resp = client.get("/api/emails/pending", headers=HDR)
        assert resp.status_code == 200
        assert resp.json()["total"] == 1


class TestProxiesApi:
    def test_get_proxies_reads_file(self, client, tmp_path, monkeypatch):
        f = tmp_path / "proxies.txt"
        f.write_text("# comment\ngate.kookeey.info:1000:u-s:p-US\n1.2.3.4:8080\n", encoding="utf-8")
        monkeypatch.setattr(proxies_mod, "PROXY_FILE", f)
        resp = client.get("/api/proxies/", headers=HDR)
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2  # 注释行不计

    def test_save_proxies_writes_file(self, client, tmp_path, monkeypatch):
        f = tmp_path / "proxies.txt"
        f.write_text("old\n", encoding="utf-8")
        monkeypatch.setattr(proxies_mod, "PROXY_FILE", f)
        resp = client.post("/api/proxies/", json={"content": "5.6.7.8:8080\n"}, headers=HDR)
        assert resp.status_code == 200
        assert f.read_text(encoding="utf-8") == "5.6.7.8:8080\n"
