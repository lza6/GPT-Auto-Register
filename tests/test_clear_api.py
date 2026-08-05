"""清空接口 — 服务端确认 + 自动备份测试。"""
from __future__ import annotations

import json

from services import db

HDR = {"X-Auth-Key": "test-admin-key"}


def _seed_data() -> None:
    db.insert_account(
        email="a@b.com", password="p", client_id="c", refresh_token="r",
        status="success", access_token="eyJx",
    )
    db.insert_email("e@f.com", "p", "c", "r")


class TestClearConfirm:
    def test_clear_requires_confirm(self, client, isolated_db):
        _seed_data()
        resp = client.post("/api/register/clear", json={}, headers=HDR)
        assert resp.status_code == 400
        assert db.count_accounts() == 1  # 未确认不得清空

    def test_clear_wrong_confirm_rejected(self, client, isolated_db):
        _seed_data()
        resp = client.post("/api/register/clear", json={"confirm": "nope"}, headers=HDR)
        assert resp.status_code == 400
        assert db.count_accounts() == 1

    def test_clear_with_confirm_creates_backup(self, client, isolated_db):
        _seed_data()
        resp = client.post("/api/register/clear", json={"confirm": "clear"}, headers=HDR)
        assert resp.status_code == 200
        body = resp.json()
        assert body["deleted"]["accounts"] == 1
        assert body["deleted"]["emails"] == 1
        backups = list((isolated_db / "backups").glob("backup_*.json"))
        assert len(backups) == 1
        snap = json.loads(backups[0].read_text(encoding="utf-8"))
        assert len(snap["accounts"]) == 1
        assert snap["accounts"][0]["email"] == "a@b.com"
        assert db.count_accounts() == 0

    def test_backup_rotation_keeps_newest_seven(self, client, isolated_db):
        # 反复清空应只保留最近 7 份备份
        for _ in range(9):
            _seed_data()
            resp = client.post("/api/register/clear", json={"confirm": "clear"}, headers=HDR)
            assert resp.status_code == 200
        backups = sorted((isolated_db / "backups").glob("backup_*.json"))
        assert len(backups) <= 7
