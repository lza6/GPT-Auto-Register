"""api/register — 导出 chatgpt2api 并发刷新测试（mock _refresh_oauth）。"""
from __future__ import annotations

import threading
import time

from services import db

import api.register as reg


def _seed_accounts(n: int) -> None:
    for i in range(n):
        db.insert_account(
            email=f"u{i}@y.com", password="p", client_id="c", refresh_token="r",
            status="success", openai_refresh_token=f"rt{i}",
        )


def _ok_token(ort: str) -> dict:
    return {"access_token": "eyJ" + "x" * 200, "refresh_token": ort, "id_token": ""}


class TestExportConcurrency:
    async def test_refresh_concurrency_capped(self, monkeypatch, isolated_db):
        _seed_accounts(12)
        running = {"n": 0, "max": 0}
        tl = threading.Lock()

        def fake_refresh(ort):
            with tl:
                running["n"] += 1
                running["max"] = max(running["max"], running["n"])
            time.sleep(0.02)
            with tl:
                running["n"] -= 1
            return _ok_token(ort)

        monkeypatch.setattr(reg, "_refresh_oauth", fake_refresh)
        accounts = await reg._collect_export_accounts()
        assert len(accounts) == 12
        assert running["max"] <= reg._EXPORT_REFRESH_CONCURRENCY

    async def test_failed_refresh_skipped_but_total_kept(self, monkeypatch, isolated_db):
        _seed_accounts(5)

        def fake_refresh(ort):
            if ort == "rt0":
                return None  # 单账号刷新失败
            return _ok_token(ort)

        monkeypatch.setattr(reg, "_refresh_oauth", fake_refresh)
        accounts = await reg._collect_export_accounts()
        assert len(accounts) == 4  # 失败账号被跳过
        assert all(a["email"] != "u0@y.com" for a in accounts)

    async def test_legacy_account_with_std_jwt_exported(self, monkeypatch, isolated_db):
        # 老账号无 openai_refresh_token，仅有标准 access_token（eyJ 且 >200）
        db.insert_account(
            email="old@y.com", password="p", client_id="c", refresh_token="r",
            status="success", openai_refresh_token="",
            access_token="eyJ" + "x" * 200,
        )
        called = {"n": 0}

        def fake_refresh(ort):
            called["n"] += 1
            return _ok_token(ort)

        monkeypatch.setattr(reg, "_refresh_oauth", fake_refresh)
        accounts = await reg._collect_export_accounts()
        assert len(accounts) == 1
        assert called["n"] == 0  # 无 refresh_token 的账号不走刷新
