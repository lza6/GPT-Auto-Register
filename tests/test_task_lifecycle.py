"""任务生命周期：进度落库、断点续跑标记、批量去重优化 — 单元测试。"""
from __future__ import annotations

import pytest

from services import db
from services.register_engine import RegisterEngine


def _create_running_task(total: int = 2) -> int:
    conn = db.get_conn()
    with conn:
        cur = conn.execute(
            "INSERT INTO tasks (task_type, status, total) VALUES ('batch_register','running',?)",
            (total,),
        )
    conn.close()
    return cur.lastrowid


@pytest.fixture
def engine(monkeypatch):
    """最小化 RegisterEngine：0 间隔 + 假 register_one（恒成功）"""
    eng = RegisterEngine({"register_interval_sec": 0})

    async def fake_register_one(email, password, client_id, refresh_token):
        return {
            "email": email, "status": "success", "access_token": "eyJx",
            "refresh_token": "rt", "id_token": "", "name": "n",
            "birthdate": "2000-01-01", "proxy": "直连", "error": "",
        }

    monkeypatch.setattr(eng, "register_one", fake_register_one)
    monkeypatch.setattr(eng, "_append_token", lambda token: None)
    return eng


class TestTaskDb:
    def test_update_task_progress_sets_counts(self, isolated_db):
        task_id = _create_running_task()
        db.update_task_progress(task_id, completed=1, failed=0, skipped=0)
        t = db.get_task(task_id)
        assert t["completed"] == 1
        assert t["status"] == "running"

    def test_update_task_progress_sets_status(self, isolated_db):
        task_id = _create_running_task()
        db.update_task_progress(task_id, completed=1, failed=0, skipped=1, status="completed")
        t = db.get_task(task_id)
        assert t["status"] == "completed"
        assert t["skipped"] == 1

    def test_get_task_returns_none_for_missing(self, isolated_db):
        assert db.get_task(99999) is None

    def test_reset_stale_tasks_marks_running_as_interrupted(self, isolated_db):
        conn = db.get_conn()
        with conn:
            run_id = conn.execute(
                "INSERT INTO tasks (task_type,status) VALUES ('a','running')"
            ).lastrowid
            done_id = conn.execute(
                "INSERT INTO tasks (task_type,status) VALUES ('a','completed')"
            ).lastrowid
        conn.close()

        n = db.reset_stale_tasks()
        assert n == 1
        assert db.get_task(run_id)["status"] == "interrupted"
        assert db.get_task(done_id)["status"] == "completed"


class TestRunBatch:
    async def test_run_batch_skips_existing_success(self, isolated_db, engine):
        # 已存在的成功账号应被跳过
        db.insert_account(
            email="a@b.com", password="p", client_id="c", refresh_token="r",
            status="success", access_token="eyJx",
        )
        emails = [
            {"email": "a@b.com", "password": "p", "client_id": "c", "refresh_token": "r"},
            {"email": "c@d.com", "password": "p", "client_id": "c", "refresh_token": "r"},
        ]
        stats = await engine.run_batch(emails, task_id=1)
        assert stats["skipped"] == 1
        assert stats["completed"] == 1

    async def test_run_batch_updates_task_progress(self, isolated_db, engine):
        task_id = _create_running_task(total=1)
        emails = [
            {"email": "x@y.com", "password": "p", "client_id": "c", "refresh_token": "r"},
        ]
        await engine.run_batch(emails, task_id=task_id)
        t = db.get_task(task_id)
        assert t["completed"] == 1
        assert t["failed"] == 0
        assert t["status"] == "completed"

    async def test_run_batch_loads_success_set_once(self, isolated_db, engine, monkeypatch):
        """去重集合只应全表查询一次（预加载），而非每账号一次（O(N²)）。"""
        import services.register_engine as re_mod

        calls = {"n": 0}

        def counting_get_accounts(status="", limit=0, offset=0):
            calls["n"] += 1
            return []

        monkeypatch.setattr(re_mod, "get_accounts", counting_get_accounts)
        emails = [
            {"email": f"u{i}@y.com", "password": "p", "client_id": "c", "refresh_token": "r"}
            for i in range(3)
        ]
        await engine.run_batch(emails, task_id=1)
        assert calls["n"] == 1
