"""pytest 全局 fixture：隔离数据库、提供 API 测试客户端。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """把 services.db 的数据库路径隔离到临时目录，避免污染真实 data/register.db。"""
    import services.db as db

    # v3.4 日志异步化：切换 DB 前停掉后台 flusher，防旧线程把日志写进上一个/下一个隔离 DB
    db.stop_log_flusher()
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "register.db")
    db.init_db()
    yield tmp_path
    db.stop_log_flusher()


@pytest.fixture(autouse=True)
def _reset_service_singletons():
    """每个测试前重置引擎/服务单例，防跨测试状态残留（顺序无关/可重复）。

    v3.1.1 终局审计发现：get_engine/get_protocol_register/get_browser_register 等是模块级
    单例，前序测试的 _running/_starting/_stop_requested/config 会泄漏到后续测试，导致
    同一测试单独跑过、全量跑失败（"覆盖率≠上线不出 bug"的测试可靠性隐患）。
    """
    import services.register_engine as re_mod
    import services.protocol_register as pr_mod
    import services.browser_register as br_mod
    import services.browser_pool as bp_mod
    import services.token_refresher as tr_mod

    re_mod.register_engine = None
    pr_mod.protocol_register = None
    br_mod.browser_register = None
    bp_mod.browser_pool = None
    tr_mod.token_refresher = None
    yield
    # 测试后再清一次，避免后台任务句柄残留
    re_mod.register_engine = None
    pr_mod.protocol_register = None
    br_mod.browser_register = None
    bp_mod.browser_pool = None
    tr_mod.token_refresher = None


@pytest.fixture
def client(monkeypatch, tmp_path):
    """构造隔离配置的 FastAPI TestClient。

    - 隔离 config.json（写一份最小配置到 tmp，指向临时 auth_key）
    - 隔离 DB
    """
    import json

    import services.db as db
    import api as api_init
    import api.settings as api_settings

    # 隔离数据库（v3.4：切换前停后台日志 flusher，防跨隔离 DB 写串库）
    db.stop_log_flusher()
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "register.db")
    db.init_db()

    # 隔离 config.json
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps({"auth_key": "test-admin-key", "port": 23457}),
        encoding="utf-8",
    )
    monkeypatch.setattr(api_init, "CONFIG_PATH", cfg)
    # settings.py 有独立的 CONFIG_PATH 模块级引用，需单独隔离，否则写设置会污染真实 config.json
    monkeypatch.setattr(api_settings, "CONFIG_PATH", cfg)

    from fastapi.testclient import TestClient

    app = api_init.create_app()
    return TestClient(app)
