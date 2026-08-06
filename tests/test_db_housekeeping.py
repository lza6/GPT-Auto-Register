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


class TestLogsCapacityM4:
    """M4: logs 表硬上限 MAX_LOG_ROWS，超量自动删旧（每 100 次插入触发一次）。"""

    def test_max_log_rows_constant(self):
        assert db.MAX_LOG_ROWS == 20000

    def test_logs_capped_after_overflow(self, isolated_db, monkeypatch):
        """临时降 MAX_LOG_ROWS 到 50，插 250 条（触发 2 次清理），验证容量受控。"""
        monkeypatch.setattr(db, "MAX_LOG_ROWS", 50)
        # 重置计数器，确保触发清理
        db._LOG_TRIM_COUNTER["n"] = 0
        for i in range(250):
            db.add_log("info", f"msg-{i}")
        logs = db.get_logs(limit=500)
        # 清理后应 ≤ MAX_LOG_ROWS(50) + 100 次插入窗口内的余量
        assert len(logs) <= 50 + 100, f"logs 应受上限，实际 {len(logs)}"
        assert len(logs) > 0


class TestGetStatsAggregatedL4:
    """L4: get_stats 聚合 SQL 不破坏返回结构且计数正确。"""

    def test_stats_returns_all_fields(self, isolated_db):
        stats = db.get_stats()
        expected = {
            "emails_total", "emails_pending", "emails_used",
            "accounts_total", "accounts_success", "accounts_failed",
            "accounts_skipped", "accounts_pending", "accounts_registering",
            "last_task_failure_types", "failure_diagnosis",
        }
        assert set(stats.keys()) == expected
        assert isinstance(stats["emails_total"], int)
        assert stats["last_task_failure_types"] == {}
        # v3.0 A4A6：无失败时诊断建议为空串
        assert stats["failure_diagnosis"] == ""

    def test_stats_counts_correct(self, isolated_db):
        db.insert_email("a@e.com", "p", "c", "r")
        db.insert_email("b@e.com", "p", "c", "r")
        db.mark_email_status("a@e.com", "used")
        db.insert_account(email="x@e.com", password="p", client_id="c",
                          refresh_token="r", proxy="直连", status="success", access_token="t")
        stats = db.get_stats()
        assert stats["emails_total"] == 2
        assert stats["emails_used"] == 1
        assert stats["emails_pending"] == 1
        assert stats["accounts_success"] == 1
        assert stats["accounts_total"] == 1


class TestIndexes:
    """L4 配套：高频查询字段有索引，避免数据量大时全表扫描。"""

    def test_indexes_created(self, isolated_db):
        """init_db 后 accounts/emails/logs/tasks 状态字段应有索引。"""
        with db.db_session() as conn:
            idxs = {row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()}
        assert "idx_accounts_status" in idxs
        assert "idx_emails_status" in idxs
        assert "idx_logs_created_at" in idxs
        assert "idx_tasks_status" in idxs

    def test_index_idempotent_reinit(self, isolated_db):
        """重复 init_db 不报错（CREATE INDEX IF NOT EXISTS 幂等）。"""
        db.init_db()
        db.init_db()
        with db.db_session() as conn:
            assert "idx_accounts_status" in {r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
