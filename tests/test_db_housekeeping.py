"""db 连接收敛(db_session) / 日志保留(purge) / config 模板对齐 测试。"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from services import db


class TestLogRetention:
    def test_purge_removes_old_keeps_new(self, isolated_db):
        with db.db_session() as conn:
            conn.execute(
                "INSERT INTO logs (level, message, created_at) VALUES ('info', 'old-log', ?)",
                (time.time() - 40 * 86400,),
            )
            conn.execute(
                "INSERT INTO logs (level, message, created_at) VALUES ('info', 'new-log', ?)",
                (time.time(),),
            )
        n = db.purge_old_logs(retention_days=30)
        assert n == 1
        logs = db.get_logs(limit=100)
        assert all(l["message"] != "old-log" for l in logs)
        assert any(l["message"] == "new-log" for l in logs)

    def test_purge_retention_days_respected(self, isolated_db):
        with db.db_session() as conn:
            conn.execute(
                "INSERT INTO logs (level, message, created_at) VALUES ('info', 'mid', ?)",
                (time.time() - 10 * 86400,),
            )
        # 保留期 30 天 → 10 天前的日志不删
        n = db.purge_old_logs(retention_days=30)
        assert n == 0


class TestDbSession:
    def test_session_commits(self, isolated_db):
        with db.db_session() as conn:
            conn.execute("INSERT INTO settings (key, value) VALUES ('k1', 'v1')")
        assert db.get_setting("k1") == "v1"

    def test_session_rolls_back_on_error(self, isolated_db):
        with pytest.raises(RuntimeError):
            with db.db_session() as conn:
                conn.execute("INSERT INTO settings (key, value) VALUES ('k2', 'v2')")
                raise RuntimeError("boom")
        assert db.get_setting("k2", "missing") == "missing"

    def test_all_existing_functions_still_work(self, isolated_db):
        # 重构后回归：核心读写函数行为不变
        db.insert_email("a@b.com", "p", "c", "r")
        assert len(db.get_pending_emails()) == 1
        db.mark_email_status("a@b.com", "used")
        assert len(db.get_pending_emails()) == 0
        db.insert_account(email="a@b.com", password="p", client_id="c", refresh_token="r", status="success")
        assert db.count_accounts("success") == 1
        assert db.count_emails("used") == 1


class TestConfigTemplate:
    def test_config_example_is_valid_and_complete(self):
        """config.example.json 应覆盖运行时全部关键字段（M4 对齐）。"""
        root = Path(__file__).resolve().parent.parent
        cfg = json.loads((root / "config.example.json").read_text(encoding="utf-8"))
        for key in (
            "auth_key", "auth_enforced", "register_concurrency", "protocol_first",
            "use_browser", "use_oauth_pkce", "otp_min_age_window_sec",
            "otp_fallback_after_sec", "otp_backfill_window_min", "log_retention_days",
        ):
            assert key in cfg, f"config.example.json 缺少字段: {key}"
