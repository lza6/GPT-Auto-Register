"""v3.0 测试：浏览器池（H3）+ config schema（P0/P2-6）+ token 巡检（G1）+ 失败自适应（A4A6）"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

# 确保项目根在 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── 浏览器池测试 ──────────────────────────────────
def _run(coro):
    """兼容 Py3.14 的 async 测试运行器（get_event_loop 在 3.14 报错）。"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


class TestBrowserPool:
    def test_max_size_zero_returns_none(self):
        from services.browser_pool import BrowserPool
        r = _run(pool_acquire(BrowserPool(max_size=0), "http://x:1"))
        assert r is None  # max_size=0 不池化

    def test_acquire_returns_placeholder(self):
        from services.browser_pool import BrowserPool
        async def run():
            pool = BrowserPool(max_size=2)
            inst = await pool.acquire("http://a:1")
            assert inst is not None
            assert inst.camoufox is None  # 占位，待填充
            return pool, inst
        pool, inst = _run(run())
        # release 占位实例（camoufox=None 不进 idle）
        _run(pool.release(inst))
        assert pool.idle_size() == 0

    def test_filled_instance_reuses_same_proxy(self):
        from services.browser_pool import BrowserPool
        async def run():
            pool = BrowserPool(max_size=2)
            inst = await pool.acquire("http://b:1")
            inst.camoufox = "fake"
            inst.browser = "fake_br"
            await pool.release(inst)
            assert pool.idle_size() == 1
            # 同代理复用
            inst2 = await pool.acquire("http://b:1")
            assert inst2 is inst
            assert pool.idle_size() == 0
        _run(run())

    def test_get_pool_disabled_returns_none(self):
        from services.browser_pool import get_browser_pool
        assert get_browser_pool({"browser_pool_size": 0}) is None
        assert get_browser_pool({}) is None  # 缺省也不池化

    def test_pool_size_for_healthz_zero(self):
        from services.browser_pool import pool_size_for_healthz
        assert pool_size_for_healthz() == 0  # 全局池未初始化

    def test_cleanup_destroys_all(self):
        from services.browser_pool import BrowserPool
        async def run():
            pool = BrowserPool(max_size=3)
            inst1 = await pool.acquire("http://a:1")
            inst1.camoufox = types.SimpleNamespace(stop=AsyncMock())
            inst1.browser = types.SimpleNamespace(close=AsyncMock())
            await pool.release(inst1)
            inst2 = await pool.acquire("http://b:1")
            inst2.camoufox = types.SimpleNamespace(stop=AsyncMock())
            inst2.browser = types.SimpleNamespace(close=AsyncMock())
            await pool.release(inst2)
            assert pool.size() == 2
            n = await pool.cleanup()
            assert n == 2
            assert pool.size() == 0
        _run(run())


async def pool_acquire(pool, proxy_url):
    return await pool.acquire(proxy_url)


class AsyncMock:
    """简单 async mock。"""
    def __await__(self):
        async def _noop():
            return None
        return _noop().__await__()


# ── config schema 测试 ──────────────────────────────
class TestConfigSchema:
    def test_clean_config_no_issues(self):
        from services.config_schema import validate_config
        clean = {"register_concurrency": 1, "register_interval_sec": 15, "use_browser": True}
        assert validate_config(clean) == []

    def test_dirty_string_int_detected(self):
        from services.config_schema import validate_config
        dirty = {"register_concurrency": "true", "register_interval_sec": "15"}
        issues = validate_config(dirty)
        assert len(issues) == 2  # 一个非法字符串，一个数字字符串

    def test_bool_string_accepted(self):
        from services.config_schema import validate_config
        # 布尔字符串 'true'/'false' 可接受（settings API 存字符串）
        assert validate_config({"use_browser": "true"}) == []
        assert validate_config({"protocol_first": "false"}) == []

    def test_invalid_string_int_reported(self):
        from services.config_schema import validate_config
        issues = validate_config({"register_concurrency": "abc"})
        assert len(issues) == 1
        assert "非法字符串" in issues[0].hint or "应为整数" in issues[0].hint

    def test_format_issues_empty(self):
        from services.config_schema import format_issues
        assert format_issues([]) == ""

    def test_format_issues_nonempty(self):
        from services.config_schema import validate_config, format_issues
        text = format_issues(validate_config({"register_concurrency": "true"}))
        assert "config" in text
        assert "register_concurrency" in text


# ── token 巡检测试 ─────────────────────────────────
class TestTokenRefresher:
    def test_init_defaults(self):
        from services.token_refresher import TokenRefresher
        r = TokenRefresher({})
        assert r._interval == 21600  # 默认 6h
        assert r.is_running is False
        assert r.last_result == {"scanned": 0, "refreshed": 0, "failed": 0,
                                 "active": 0, "unknown": 0, "deactivated": 0}

    def test_interval_string_compat(self):
        from services.token_refresher import TokenRefresher
        r = TokenRefresher({"token_refresh_interval_sec": "10800"})
        assert r._interval == 10800

    def test_interval_invalid_falls_back(self):
        from services.token_refresher import TokenRefresher
        r = TokenRefresher({"token_refresh_interval_sec": "abc"})
        assert r._interval == 21600

    def test_refresh_token_monkeypatch(self, monkeypatch):
        """monkeypatch _probe/_refresh_token 验证 scan_once 探活优先 + RT 恢复链落库"""
        from services.token_refresher import TokenRefresher
        from services import db as dbmod
        import services.account_liveness as almod

        # 用临时 db
        import tempfile, os
        tmp = tempfile.mkdtemp()
        orig = dbmod.DB_PATH
        dbmod.DB_PATH = dbmod.Path(tmp) / "test.db"
        dbmod.init_db()
        try:
            # 插入 success 账号
            dbmod.insert_account("a@e.com", "p", "c", "r",
                                 access_token="old", status="success",
                                 openai_refresh_token="rt_a")
            dbmod.insert_account("b@e.com", "p", "c", "r",
                                 access_token="old2", status="success",
                                 openai_refresh_token="rt_b")

            r = TokenRefresher({})
            # v4.0 P1-4/5：先探活（都判 AT 失效），再走 RT 恢复链
            async def fake_probe(acc, proxy_url=None):
                return {"status": "token_invalid", "error": "401"}
            r._probe = fake_probe

            # monkeypatch _refresh_token：rt_a 成功（返回三件套），rt_b 失败
            async def fake_refresh(rt):
                if rt == "rt_a":
                    return {"access_token": "new_tok", "refresh_token": "rt_a_rotated", "id_token": ""}
                return None
            r._refresh_token = fake_refresh

            # 恢复链的二次探活确认：new_tok 探活 active
            async def fake_probe_at(at, **k):
                return {"status": "active", "error": ""}
            monkeypatch.setattr(almod, "probe_access_token", fake_probe_at)

            result = _run(r._scan_once())
            assert result["scanned"] == 2
            assert result["refreshed"] == 1
            assert result["failed"] == 1

            # 验证 access_token + 轮换后的新 refresh_token 均已落库（v3.1 审计修复）
            accs = dbmod.get_accounts(status="success")
            a = next(x for x in accs if x["email"] == "a@e.com")
            assert a["access_token"] == "new_tok"
            assert a["openai_refresh_token"] == "rt_a_rotated", "轮换后的新 RT 必须落库，否则库存 RT 逐步耗尽"
        finally:
            dbmod.DB_PATH = orig
            dbmod.init_db()

    def test_get_token_refresher_singleton(self):
        from services.token_refresher import get_token_refresher, token_refresher
        r1 = get_token_refresher({})
        r2 = get_token_refresher({})
        assert r1 is r2

    def test_start_stop_task(self):
        """start 启动后台任务，stop 取消（需在 async 上下文里调 create_task）。"""
        from services.token_refresher import TokenRefresher
        async def run():
            r = TokenRefresher({"token_refresh_interval_sec": 3600})
            assert not r.is_running
            r.start()  # 在 running loop 内调用 create_task
            assert r.is_running
            r.start()  # 幂等
            assert r.is_running
            await r.stop()
            assert not r.is_running
        _run(run())

    def test_refresh_token_http_failure(self):
        """真实调用 _refresh_token（网络不可达）应返回 None 不抛异常。"""
        from services.token_refresher import TokenRefresher
        r = TokenRefresher({})
        # 用无效 refresh_token 调真实 OpenAI 端点（网络失败/401 都安全返回 None）
        result = _run(r._refresh_token("invalid_token_for_test"))
        # 网络可达时返回 None（401），不可达时也返回 None，两种都通过
        assert result is None

    def test_resolve_proxy_prefers_config_url(self):
        """v3.1 T7：巡检走代理——优先 config.proxy_url。"""
        from services.token_refresher import TokenRefresher
        r = TokenRefresher({"proxy_url": "http://127.0.0.1:10808"})
        assert r._resolve_proxy() == "http://127.0.0.1:10808"

    def test_resolve_proxy_none_when_unset(self):
        """无代理配置时返回 None（直连）。"""
        from services.token_refresher import TokenRefresher
        assert TokenRefresher({})._resolve_proxy() is None
        assert TokenRefresher({"use_proxy": False})._resolve_proxy() is None

    def test_resolve_proxy_from_pool_when_use_proxy(self, monkeypatch):
        """use_proxy 且无 proxy_url 时从代理池取（兼容字符串 'true'）。"""
        from services.token_refresher import TokenRefresher
        import services.proxy_service as ps
        monkeypatch.setattr(ps.proxy_service, "get_next", lambda: "http://pool:1000")
        assert TokenRefresher({"use_proxy": True})._resolve_proxy() == "http://pool:1000"
        assert TokenRefresher({"use_proxy": "true"})._resolve_proxy() == "http://pool:1000"


# ── token-health 端点测试 ────────────────────────
class TestTokenHealthEndpoint:
    def test_token_health_default_disabled(self):
        from api import create_app
        from starlette.testclient import TestClient
        app = create_app()
        with TestClient(app) as c:
            r = c.get('/api/stats/token-health')
            d = r.json()
            assert d['enabled'] is False
            assert 'result' in d
            assert d['last_scan_at'] == 0


# ── 失败分级诊断建议测试 ──────────────────────────
class TestFailureDiagnosis:
    def test_empty_no_hint(self):
        from services.db import _build_failure_diagnosis
        assert _build_failure_diagnosis({}) == ""

    def test_risk_control_hint(self):
        from services.db import _build_failure_diagnosis
        # risk_control 7, network 1, otp_timeout 2 → total 10, risk=70%
        h = _build_failure_diagnosis({"risk_control": 7, "network": 1, "otp_timeout": 2})
        assert "风控" in h
        assert "70%" in h

    def test_network_hint(self):
        from services.db import _build_failure_diagnosis
        h = _build_failure_diagnosis({"network": 5})
        assert "网络" in h

    def test_server_5xx_hint(self):
        from services.db import _build_failure_diagnosis
        h = _build_failure_diagnosis({"server_5xx": 3})
        assert "5xx" in h


# ── 双引擎统一抽象测试（B1）────────────────────────
class TestRegisterBaseProtocol:
    def test_protocol_register_implements(self):
        from services.register_base import is_register_engine, RegisterEngineProtocol
        from services.protocol_register import ProtocolRegister
        p = ProtocolRegister({})
        assert is_register_engine(p)
        assert isinstance(p, RegisterEngineProtocol)

    def test_browser_register_implements(self):
        from services.register_base import is_register_engine, RegisterEngineProtocol
        from services.browser_register import BrowserRegister
        b = BrowserRegister({})
        assert is_register_engine(b)
        assert isinstance(b, RegisterEngineProtocol)

    def test_non_engine_rejected(self):
        from services.register_base import is_register_engine, RegisterEngineProtocol
        assert not is_register_engine({})
        assert not is_register_engine("string")
        assert not is_register_engine(None)
        assert not is_register_engine([1, 2, 3])
