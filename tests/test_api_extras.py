"""api 扩展 — 日志增量(after_id)、邮箱/账号搜索筛选测试。"""
from __future__ import annotations

from services import db

HDR = {"X-Auth-Key": "test-admin-key"}


class TestLogsAfterId:
    def test_after_id_returns_only_new(self, client, isolated_db):
        db.add_log("info", "one")
        db.add_log("info", "two")
        db.add_log("info", "three")
        logs = client.get("/api/logs", headers=HDR).json()["logs"]
        ids = sorted(l["id"] for l in logs)
        after = ids[1]
        new = client.get(f"/api/logs?after_id={after}", headers=HDR).json()["logs"]
        assert len(new) == 1
        assert new[0]["id"] == ids[2]
        assert new[0]["message"] == "three"


class TestSearch:
    def test_accounts_search_filters_email(self, client, isolated_db):
        db.insert_account(email="alpha@y.com", password="p", client_id="c", refresh_token="r", status="success")
        db.insert_account(email="beta@y.com", password="p", client_id="c", refresh_token="r", status="failed")
        data = client.get("/api/register/accounts?search=alpha", headers=HDR).json()
        assert data["total"] == 1
        assert data["accounts"][0]["email"] == "alpha@y.com"

    def test_accounts_search_and_status_combined(self, client, isolated_db):
        db.insert_account(email="a1@y.com", password="p", client_id="c", refresh_token="r", status="success")
        db.insert_account(email="a2@y.com", password="p", client_id="c", refresh_token="r", status="failed")
        data = client.get("/api/register/accounts?search=a&status=success", headers=HDR).json()
        assert data["total"] == 1
        assert data["accounts"][0]["email"] == "a1@y.com"

    def test_accounts_pagination_total(self, client, isolated_db):
        for i in range(5):
            db.insert_account(email=f"u{i}@y.com", password="p", client_id="c", refresh_token="r", status="success")
        # limit=2 → total 应为全量 5，而 accounts 只回 2 条
        data = client.get("/api/register/accounts?limit=2&offset=0", headers=HDR).json()
        assert data["total"] == 5
        assert len(data["accounts"]) == 2

    def test_emails_search_and_status(self, client, isolated_db):
        db.insert_email("a@y.com", "p", "c", "r")
        db.mark_email_status("a@y.com", "used")
        db.insert_email("b@y.com", "p", "c", "r")
        pending = client.get("/api/emails/?status=pending", headers=HDR).json()
        assert pending["total"] == 1
        assert pending["emails"][0]["email"] == "b@y.com"
        search = client.get("/api/emails/?search=a@y.com", headers=HDR).json()
        assert search["total"] == 1
        assert search["emails"][0]["email"] == "a@y.com"
