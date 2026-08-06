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


class TestConfigParsing:
    """数字配置被 settings API 写坏为字符串/非法值时不得崩溃（防御式 _as_int）。"""

    def test_as_int_parses_strings_and_falls_back(self):
        from services.register_engine import _as_int

        assert _as_int("3", 1) == 3
        assert _as_int("true", 1) == 1   # 非法字符串回退默认
        assert _as_int("", 1) == 1
        assert _as_int(None, 1) == 1
        assert _as_int(5, 1) == 5
        assert _as_int("12 ", 1) == 12   # 容忍首尾空格

    async def test_bad_concurrency_string_does_not_crash(self, isolated_db, monkeypatch):
        # 真实风险：前端把 register_concurrency 存成 'true'（曾发生），run_batch 不得 int('true') 崩溃
        eng = RegisterEngine({"register_concurrency": "true", "register_interval_sec": 0})

        async def fake(email, password, client_id, refresh_token):
            return _ok(email)

        monkeypatch.setattr(eng, "register_one", fake)
        monkeypatch.setattr(eng, "_append_token", lambda t: None)
        stats = await eng.run_batch(_emails(2), task_id=1)
        assert stats["completed"] == 2


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

    async def test_start_after_stop_does_not_abort(self, isolated_db, monkeypatch):
        """v3.1 审计回归：一次正常 stop 后，下一次 try_start+run_batch 不应被陈旧的
        _stop_requested 误判为「启动前已停止」而空跑（completed 应为真实注册数）。"""
        eng = RegisterEngine({"register_interval_sec": 0, "register_concurrency": 1})

        async def fast(email, password, client_id, refresh_token):
            return _ok(email)

        monkeypatch.setattr(eng, "register_one", fast)
        monkeypatch.setattr(eng, "_append_token", lambda t: None)

        # 第一批正常跑完
        stats1 = await eng.run_batch(_emails(2), task_id=_create_task(2))
        assert stats1["completed"] == 2
        # 用户随后调用 stop（此时无批次运行，残留 _stop_requested=True）
        eng.stop()
        assert eng._stop_requested is True
        # 第二批（全新邮箱，避免与第一批去重）：try_start 应清除陈旧标志，run_batch 正常跑而非空转
        assert eng.try_start() is True
        fresh = [{"email": f"v{i}@y.com", "password": "p", "client_id": "c", "refresh_token": "r"} for i in range(3)]
        stats2 = await eng.run_batch(fresh, task_id=_create_task(3))
        assert stats2["completed"] == 3, "stop 后的新批次不应被陈旧停止标志中止"

    async def test_register_interval_spaces_slot_start(self, isolated_db, monkeypatch):
        """v3.1 审计回归：register_interval_sec 移入 sem 后，interval 真实占用槽位时间。

        concurrency=1 + interval>0 时，两次注册启动间隔应 >= interval（原 sleep 在 sem 外失效）。
        用较小 interval 验证语义而不拖慢测试。
        """
        eng = RegisterEngine({"register_interval_sec": 1, "register_concurrency": 1})
        starts = []

        async def fast(email, password, client_id, refresh_token):
            starts.append(asyncio.get_event_loop().time())
            return _ok(email)

        monkeypatch.setattr(eng, "register_one", fast)
        monkeypatch.setattr(eng, "_append_token", lambda t: None)

        await eng.run_batch(_emails(2), task_id=_create_task(2))
        assert len(starts) == 2
        gap = starts[1] - starts[0]
        assert gap >= 0.9, f"interval 应间隔两次注册启动，实际间隔 {gap:.2f}s（sem 外时≈0）"

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

    async def test_register_one_raises_counts_failure_and_persists(self, isolated_db, monkeypatch):
        """register_one 抛异常（非 dict 返回）也应计失败并落库，避免邮箱静默丢失。"""
        eng = RegisterEngine({"register_interval_sec": 0})
        task_id = _create_task(total=1)

        async def boom(email, password, client_id, refresh_token):
            raise RuntimeError("network exploded")

        monkeypatch.setattr(eng, "register_one", boom)
        monkeypatch.setattr(eng, "_append_token", lambda t: None)

        stats = await eng.run_batch(_emails(1), task_id=task_id)
        assert stats["failed"] == 1
        assert stats["failure_types"].get("unknown") == 1
        conn = db.get_conn()
        row = conn.execute("SELECT status, error FROM accounts WHERE email='u0@y.com'").fetchone()
        conn.close()
        assert row is not None
        assert row["status"] == "failed"
        assert "注册异常" in row["error"]


class TestAdaptivePause:
    """v3.1.1 覆盖率补缺：A4A6 失败自适应暂停的真实分支（原未被覆盖）。"""

    async def test_server_5xx_streak_pauses(self, isolated_db, monkeypatch):
        """连续 3 次 server_5xx 失败 → 自动暂停（is_paused=True）。"""
        eng = RegisterEngine({"register_interval_sec": 0, "register_concurrency": 1})

        async def fail_5xx(email, password, client_id, refresh_token):
            return _fail(email, "server_5xx", "HTTP 503")

        monkeypatch.setattr(eng, "register_one", fail_5xx)
        monkeypatch.setattr(eng, "_append_token", lambda t: None)
        # 恰好 3 个：第 3 次触发暂停，无后续邮箱被 pause_event 阻塞
        await eng.run_batch(_emails(3), task_id=_create_task(3))
        assert eng.is_paused is True

    async def test_risk_control_over_40pct_pauses(self, isolated_db, monkeypatch):
        """风控占比 >40%（且 total_done>=5）→ 暂停提示换代理。"""
        eng = RegisterEngine({"register_interval_sec": 0, "register_concurrency": 1})

        async def fail_rc(email, password, client_id, refresh_token):
            return _fail(email, "risk_control", "风控拦截")

        monkeypatch.setattr(eng, "register_one", fail_rc)
        monkeypatch.setattr(eng, "_append_token", lambda t: None)
        await eng.run_batch(_emails(5), task_id=_create_task(5))
        assert eng.is_paused is True

    async def test_network_failure_does_not_pause(self, isolated_db, monkeypatch):
        """非 server_5xx/风控类失败（如 network）不应触发自适应暂停。"""
        eng = RegisterEngine({"register_interval_sec": 0, "register_concurrency": 1})

        async def fail_net(email, password, client_id, refresh_token):
            return _fail(email, "network", "连接超时")

        monkeypatch.setattr(eng, "register_one", fail_net)
        monkeypatch.setattr(eng, "_append_token", lambda t: None)
        await eng.run_batch(_emails(5), task_id=_create_task(5))
        assert eng.is_paused is False  # network 失败不触发暂停

    async def test_success_no_token_branch(self, isolated_db, monkeypatch):
        """success_no_token 分支：计入 completed、落库 success_no_token、不 append token。"""
        eng = RegisterEngine({"register_interval_sec": 0, "register_concurrency": 1})
        appended = []

        async def no_token(email, password, client_id, refresh_token):
            r = _ok(email)
            r["status"] = "success_no_token"
            r["access_token"] = ""
            return r

        monkeypatch.setattr(eng, "register_one", no_token)
        monkeypatch.setattr(eng, "_append_token", lambda t: appended.append(t))
        stats = await eng.run_batch(_emails(2), task_id=_create_task(2))
        assert stats["completed"] == 2
        assert appended == []  # 无 token 不写入
        accs = db.get_accounts(status="success_no_token")
        assert len(accs) == 2
