"""B16: 数据库定时 VACUUM 策略测试。

注意：不使用 conftest 的 isolated_db/client 等 fixture，因为 _reset_service_singletons
autouse fixture 会触发 services.notifier 导入失败（已有项目问题）。
VACUUM 测试仅依赖 services.db，直接 monkeypatch 数据库路径即可。
"""
from __future__ import annotations


def test_vacuum_if_needed(monkeypatch, tmp_path):
    """VACUUM 应成功执行而不报错。"""
    import services.db as db

    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "register.db")
    db.init_db()

    from services.db import vacuum_if_needed

    result = vacuum_if_needed(force=True)
    assert result == "ok", f"期望 'ok'，实际得到: {result}"


def test_vacuum_output_ok(monkeypatch, tmp_path):
    """VACUUM 返回 'ok' 字符串。"""
    import services.db as db

    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "register.db")
    db.init_db()

    from services.db import vacuum_if_needed

    result = vacuum_if_needed(force=True)
    assert result == "ok"


def test_vacuum_skip_when_no_db(monkeypatch, tmp_path):
    """无数据库文件时应返回跳过信息。"""
    import services.db as db

    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "register.db")
    # 不调用 init_db，即无数据库文件

    from services.db import vacuum_if_needed

    result = vacuum_if_needed(force=False)
    assert result.startswith("skipped"), f"期望跳过，实际得到: {result}"


def test_get_last_vacuum_time_initial():
    """get_last_vacuum_time 应返回 0.0（初始值）。"""
    # 重置模块级状态
    import services.db as db

    db._last_vacuum_time = 0.0

    from services.db import get_last_vacuum_time

    assert get_last_vacuum_time() == 0.0


def test_vacuum_updates_last_time(monkeypatch, tmp_path):
    """VACUUM 后 get_last_vacuum_time 应更新。"""
    import services.db as db

    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "register.db")
    db.init_db()
    db._last_vacuum_time = 0.0

    from services.db import vacuum_if_needed, get_last_vacuum_time

    before = get_last_vacuum_time()
    vacuum_if_needed(force=True)
    after = get_last_vacuum_time()

    assert before == 0.0
    assert after > 0.0