"""register_engine.run_batch — 并发、暂停/恢复/停止、失败分类统计测试。"""
from __future__ import annotations

import asyncio

from services import db
from services.register_engine import RegisterEngine


def _ok(email="x@y.com"):
    return {
        "email": email, "status": "success", "access_token": "eyJx",
        "refresh_token": "rt", "id_token": "", "openai_password": "",
        "name": "n", "birthdate": "2000-01-01", "proxy": "直连",
        "error": "", "fallback_browser": False, "failure_type": "",
    }


def _fail(email, ftype, error="boom"):
    return {
        "email": email, "status": "failed", "error": error,
        "access_token": "", "refresh_token": "", "id_token": "",
        "openai_password": "", "name": "", "birthdate": "",
        "proxy": "直连", "fallback_browser": False, "failure_type": ftype,
    }


def _emails(n: int):
    return [
        {"email": f"u{i}@y.com", "password": "p", "client_id": "c", "refresh_token": "r"}
        for i in range(n)
    ]


def _create_task(total: int) -> int:
    conn = db.get_conn()
    with conn:
        cur = conn.execute(
            "INSERT INTO tasks (task_type, status, total) VALUES ('batch_register','running',?)",
            (total,),
        )
    conn.close()
    return cur.lastrowid


class TestRunBatchConcurrency:
    async def test_concurrency_capped_by_config(self, isolated_db, monkeypatch):
        eng = RegisterEngine({"register_interval_sec": 0, "register_concurrency": 3})
        lock = asyncio.Lock()
        running = {"n": 0, "max": 0}

        async def slow(email, password, client_id, refresh_token):
            async with lock:
                running["n"] += 1
                running["max"] = max(running["max"], running["n"])
            await asyncio.sleep(0.02)
            async with lock:
                running["n"] -= 1
            return _ok(email)

        monkeypatch.setattr(eng, "register_one", slow)
        monkeypatch.setattr(eng, "_append_token", lambda t: None)

        stats = await eng.run_batch(_emails(9), task_id=1)
        assert stats["completed"] == 9
        assert running["max"] <= 3

    async def test_default_concurrency_is_one(self, isolated_db, monkeypatch):
        # 未配置 register_concurrency 时并发度 = 1（保持原串行语义）
        eng = RegisterEngine({"register_interval_sec": 0})
        lock = asyncio.Lock()
        running = {"n": 0, "max": 0}

        async def slow(email, password, client_id, refresh_token):
            async with lock:
                running["n"] += 1
                running["max"] = max(running["max"], running["n"])
            await asyncio.sleep(0.01)
            async with lock:
                running["n"] -= 1
            return _ok(email)

        monkeypatch.setattr(eng, "register_one", slow)
        monkeypatch.setattr(eng, "_append_token", lambda t: None)

        await eng.run_batch(_emails(5), task_id=1)
        assert running["max"] == 1

    async def test_pause_blocks_and_resume_continues(self, isolated_db, monkeypatch):
        eng = RegisterEngine({"register_interval_sec": 0, "register_concurrency": 1})
        started = {"n": 0}
        gate = asyncio.Event()
        entered = asyncio.Event()

        async def slow(email, password, client_id, refresh_token):
            started["n"] += 1
            entered.set()
            if started["n"] == 1:
                await gate.wait()  # 第一个账号阻塞，等待测试控制
            else:
                await asyncio.sleep(0.01)
            return _ok(email)

        monkeypatch.setattr(eng, "register_one", slow)
        monkeypatch.setattr(eng, "_append_token", lambda t: None)

        task = asyncio.create_task(eng.run_batch(_emails(3), task_id=1))
        await entered.wait()  # 第一个已开始
        eng.pause()
        gate.set()  # 放行第一个
        await asyncio.sleep(0.05)  # 第一个完成，第二个应卡在暂停
        assert started["n"] == 1, "暂停期间不应继续处理"
        eng.resume()
        stats = await asyncio.wait_for(task, timeout=5)
        assert stats["completed"] == 3

    async def test_stop_prevents_remaining(self, isolated_db, monkeypatch):
        task_id = _create_task(total=3)
        eng = RegisterEngine({"register_interval_sec": 0, "register_concurrency": 1})
        entered = asyncio.Event()

        async def slow(email, password, client_id, refresh_token):
            entered.set()
            await asyncio.sleep(0.01)
            return _ok(email)

        monkeypatch.setattr(eng, "register_one", slow)
        monkeypatch.setattr(eng, "_append_token", lambda t: None)

        task = asyncio.create_task(eng.run_batch(_emails(3), task_id=task_id))
        await entered.wait()
        eng.stop()
        stats = await asyncio.wait_for(task, timeout=5)
        assert stats["completed"] <= 1
        t = db.get_task(task_id)
        assert t["status"] == "stopped"

    async def test_no_duplicate_registration_on_pause(self, isolated_db, monkeypatch):
        """暂停/恢复不应导致同一邮箱重复注册（注册次数 == 去重邮箱数）。"""
        eng = RegisterEngine({"register_interval_sec": 0, "register_concurrency": 2})
        calls = {"n": 0}

        async def slow(email, password, client_id, refresh_token):
            calls["n"] += 1
            await asyncio.sleep(0.005)
            return _ok(email)

        monkeypatch.setattr(eng, "register_one", slow)
        monkeypatch.setattr(eng, "_append_token", lambda t: None)

        emails = _emails(4)
        task = asyncio.create_task(eng.run_batch(emails, task_id=1))
        await asyncio.sleep(0.02)
        eng.pause()
        await asyncio.sleep(0.02)
        eng.resume()
        stats = await asyncio.wait_for(task, timeout=5)
        assert stats["completed"] == 4
        assert calls["n"] == 4  # 每个邮箱恰好注册一次


class TestFailureTypes:
    async def test_failure_types_collected_in_stats(self, isolated_db, monkeypatch):
        eng = RegisterEngine({"register_interval_sec": 0})
        task_id = _create_task(total=3)

        async def fake(email, password, client_id, refresh_token):
            if email == "u0@y.com":
                return _fail(email, "risk_control", "account_deactivated")
            if email == "u1@y.com":
                return _fail(email, "otp_timeout", "验证码等待超时")
            return _ok(email)

        monkeypatch.setattr(eng, "register_one", fake)
        monkeypatch.setattr(eng, "_append_token", lambda t: None)

        stats = await eng.run_batch(_emails(3), task_id=task_id)
        assert stats["failed"] == 2
        assert stats["completed"] == 1
        assert stats["failure_types"] == {"risk_control": 1, "otp_timeout": 1}

        # get_stats 暴露最近一次任务的失败分布（M9 验收）
        s = db.get_stats()
        assert s["last_task_failure_types"] == {"risk_control": 1, "otp_timeout": 1}

    async def test_failure_without_type_defaults_unknown(self, isolated_db, monkeypatch):
        eng = RegisterEngine({"register_interval_sec": 0})
        task_id = _create_task(total=1)

        async def fake(email, password, client_id, refresh_token):
            r = _fail(email, "")
            r.pop("failure_type", None)  # 模拟老引擎返回无类型
            return r

        monkeypatch.setattr(eng, "register_one", fake)
        monkeypatch.setattr(eng, "_append_token", lambda t: None)

        stats = await eng.run_batch(_emails(1), task_id=task_id)
        assert stats["failed"] == 1
        assert stats["failure_types"].get("unknown") == 1
