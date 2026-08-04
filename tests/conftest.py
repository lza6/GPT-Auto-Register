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

    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "register.db")
    db.init_db()
    return tmp_path


@pytest.fixture
def client(monkeypatch, tmp_path):
    """构造隔离配置的 FastAPI TestClient。

    - 隔离 config.json（写一份最小配置到 tmp，指向临时 auth_key）
    - 隔离 DB
    """
    import json

    import services.db as db
    import api as api_init

    # 隔离数据库
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

    from fastapi.testclient import TestClient

    app = api_init.create_app()
    return TestClient(app)
