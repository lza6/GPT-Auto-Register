"""services/token_refresher — 探活优先 + RT 恢复链（v4.0 P1-4/5）。"""
from __future__ import annotations

import asyncio

import pytest

from services.db import db_session, insert_account
from services.token_refresher import TokenRefresher


def _refresher(config=None):
    return TokenRefresher(config or {"token_refresh_enabled": True})


def _insert(email="a@x.com", access="at1", idt="idt1", rt="ort1"):
    insert_account(
        email=email, password="p", client_id="c", refresh_token="rt",
        openai_refresh_token=rt, access_token=access, id_token=idt,
        status="success",
    )


def _get(email="a@x.com", col="access_token"):
    with db_session() as conn:
        row = conn.execute(f"SELECT {col} FROM accounts WHERE email = ?", (email,)).fetchone()
    return row[0] if row else None


class TestScanProbeFirst:
    def test_active_skips_refresh(self, isolated_db, monkeypatch):
        _insert()
        r = _refresher()

        async def fake_probe(acc, proxy_url=None):
            return {"status": "active", "error": ""}
        monkeypatch.setattr(r, "_probe", fake_probe)
        result = asyncio.run(r._scan_once())
        assert result["active"] == 1
        assert result["refreshed"] == 0

    def test_token_invalid_recovers(self, isolated_db, monkeypatch):
        _insert()
        r = _refresher()

        async def fake_probe(acc, proxy_url=None):
            return {"status": "token_invalid", "error": "401"}
        monkeypatch.setattr(r, "_probe", fake_probe)

        async def fake_recover(acc, proxy_url=None):
            return True
        monkeypatch.setattr(r, "_recover_via_rt", fake_recover)
        result = asyncio.run(r._scan_once())
        assert result["refreshed"] == 1

    def test_unknown_not_misclassified(self, isolated_db, monkeypatch):
        _insert()
        r = _refresher()

        async def fake_probe(acc, proxy_url=None):
            return {"status": "unknown", "error": "429 rate limit"}
        monkeypatch.setattr(r, "_probe", fake_probe)
        result = asyncio.run(r._scan_once())
        assert result["unknown"] == 1
        assert result["refreshed"] == 0

    def test_success_missing_at_recovers_via_rt(self, isolated_db, monkeypatch):
        """M1（审查）：success + 空 access_token 账号（探活 missing_access_token），
        有 RT 时直接走 RT 恢复，不得被探活优先逻辑跳过。"""
        _insert(email="m@x.com", access="", idt="", rt="ort1")  # 空 AT
        r = _refresher()

        async def fake_probe(acc, proxy_url=None):
            return {"status": "unknown", "error": "missing_access_token"}

        async def fake_recover(acc, proxy_url=None):
            return True

        monkeypatch.setattr(r, "_probe", fake_probe)
        monkeypatch.setattr(r, "_recover_via_rt", fake_recover)
        result = asyncio.run(r._scan_once())
        assert result["refreshed"] == 1
        assert result["unknown"] == 0

    def test_deactivated_terminal(self, isolated_db, monkeypatch):
        _insert()
        r = _refresher()

        async def fake_probe(acc, proxy_url=None):
            return {"status": "token_invalid", "error": "account_deactivated"}
        monkeypatch.setattr(r, "_probe", fake_probe)
        result = asyncio.run(r._scan_once())
        assert result["deactivated"] == 1
        assert result["refreshed"] == 0

    def test_empty_accounts(self, isolated_db):
        r = _refresher()
        result = asyncio.run(r._scan_once())
        assert result["scanned"] == 0


class TestRecoverViaRt:
    def test_confirmed_active_persists(self, isolated_db, monkeypatch):
        _insert()
        r = _refresher()

        async def fake_refresh(rt):
            return {"access_token": "new-at", "refresh_token": "new-rt", "id_token": "new-id"}
        monkeypatch.setattr(r, "_refresh_token", fake_refresh)

        import services.account_liveness as al

        async def fake_probe(at, **k):
            return {"status": "active", "error": ""}
        monkeypatch.setattr(al, "probe_access_token", fake_probe)

        ok = asyncio.run(r._recover_via_rt({"email": "a@x.com", "openai_refresh_token": "ort1"}, None))
        assert ok is True
        assert _get("a@x.com", "access_token") == "new-at"
        assert _get("a@x.com", "openai_refresh_token") == "new-rt"

    def test_not_persist_when_confirm_fails(self, isolated_db, monkeypatch):
        _insert()
        r = _refresher()

        async def fake_refresh(rt):
            return {"access_token": "new-at", "refresh_token": "new-rt"}
        monkeypatch.setattr(r, "_refresh_token", fake_refresh)

        import services.account_liveness as al

        async def fake_probe(at, **k):
            return {"status": "token_invalid", "error": "401"}
        monkeypatch.setattr(al, "probe_access_token", fake_probe)

        ok = asyncio.run(r._recover_via_rt({"email": "a@x.com", "openai_refresh_token": "ort1"}, None))
        assert ok is False
        # 旧 token 未被坏 token 覆盖
        assert _get("a@x.com", "access_token") == "at1"

    def test_no_refresh_token_returns_false(self, isolated_db):
        _insert()
        r = _refresher()
        ok = asyncio.run(r._recover_via_rt({"email": "a@x.com", "openai_refresh_token": ""}, None))
        assert ok is False

    def test_refresh_failure_returns_false(self, isolated_db, monkeypatch):
        _insert()
        r = _refresher()

        async def fake_refresh(rt):
            return None
        monkeypatch.setattr(r, "_refresh_token", fake_refresh)
        ok = asyncio.run(r._recover_via_rt({"email": "a@x.com", "openai_refresh_token": "ort1"}, None))
        assert ok is False
