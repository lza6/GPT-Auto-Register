"""P0 回归测试：阶段1 对标改造修复的安全/数据正确缺陷。

覆盖：
- P0-2 email_service 98faka 请求必须携带 verify（TLS 校验防 MITM）
- P0-3 Graph token 缓存 key 区分 refresh_token（防串号读他人收件箱）
- P0-4 insert_account UPSERT 语义（re-insert 保留 id/created_at/registered_at）
- P0-5 proxy_service 并发加锁（多线程 get_next/mark_bad 无崩溃、游标不越界）
"""
from __future__ import annotations

import threading

from services import db
from services import graph_email_service as g
from services import proxy_service as ps
from services.email_service import EmailService
from services.proxy_service import ProxyService


# ── P0-2：98faka 请求必须携带 verify ────────────────────────────────
class TestTLSVerify:
    async def test_get_email_list_passes_verify(self, monkeypatch):
        captured: dict = {}

        class Client:
            """每次构造记录 kwargs，post 返回空 200。"""

            def __init__(self, *a, **k):
                captured["kwargs"] = k

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def post(self, url, *a, **k):
                return type("R", (), {"json": lambda self: {"code": 200, "data": []},
                                      "raise_for_status": lambda self: None})()

        monkeypatch.setattr("services.email_service.httpx.AsyncClient", Client)
        await EmailService().get_email_list("e@f.com", "p", "c", "rt")
        assert "verify" in captured["kwargs"], "get_email_list 未传 verify（TLS 校验缺失）"
        assert isinstance(captured["kwargs"]["verify"], bool)

    async def test_get_email_body_passes_verify(self, monkeypatch):
        captured: dict = {}

        class Client:
            def __init__(self, *a, **k):
                captured["kwargs"] = k

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def post(self, url, *a, **k):
                return type("R", (), {"json": lambda self: {"code": 200, "body_html": ""},
                                      "raise_for_status": lambda self: None})()

        monkeypatch.setattr("services.email_service.httpx.AsyncClient", Client)
        await EmailService().get_email_body("e@f.com", "m1", "c", "rt")
        assert "verify" in captured["kwargs"], "get_email_body 未传 verify（TLS 校验缺失）"


# ── P0-3：Graph token 缓存 key 区分 refresh_token ──────────────────
class _TokenFakeClient:
    def __init__(self, *a, **k):
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def post(self, *a, **k):
        self.calls += 1
        return type("R", (), {"json": lambda self: {"access_token": "tok", "expires_in": 3600}})()


class TestGraphCacheIsolation:
    async def test_same_client_id_different_rt_no_cache_share(self, monkeypatch):
        """同 client_id 不同 refresh_token 必须各自请求，杜绝复用他人 access_token（串号修复）。"""
        svc = g.GraphEmailService()
        fake = _TokenFakeClient()
        monkeypatch.setattr(g.httpx, "AsyncClient", lambda *a, **k: fake)
        assert await svc._get_access_token("cid", "rt_a") == "tok"
        assert await svc._get_access_token("cid", "rt_b") == "tok"
        assert fake.calls == 2

    async def test_same_rt_hits_cache(self, monkeypatch):
        """同一邮箱（同 client_id+RT）应命中缓存，不重复请求。"""
        svc = g.GraphEmailService()
        fake = _TokenFakeClient()
        monkeypatch.setattr(g.httpx, "AsyncClient", lambda *a, **k: fake)
        assert await svc._get_access_token("cid", "rt") == "tok"
        assert await svc._get_access_token("cid", "rt") == "tok"
        assert fake.calls == 1


# ── P0-4：insert_account UPSERT 语义 ──────────────────────────────
class TestInsertAccountUpsert:
    def test_reinsert_keeps_id_and_created_at(self, isolated_db):
        db.insert_account("a@b.com", "p1", "c", "rt", status="failed", error="first")
        first = db.get_accounts(search="a@b.com")[0]
        first_id, created = first["id"], first["created_at"]

        # 重试注册同一 email：字段更新，但 id/created_at 保留（不再整行替换）
        db.insert_account("a@b.com", "p2", "c", "rt", status="success", openai_password="op")
        rows = db.get_accounts(search="a@b.com")
        assert len(rows) == 1
        assert rows[0]["id"] == first_id
        assert rows[0]["created_at"] == created
        assert rows[0]["password"] == "p2"
        assert rows[0]["status"] == "success"
        assert rows[0]["registered_at"] is not None  # success 时记录注册时间

    def test_reinsert_failed_after_success_keeps_registered_at(self, isolated_db):
        db.insert_account("x@e.com", "p", "c", "rt", status="success")
        reg_at = db.get_accounts(search="x@e.com")[0]["registered_at"]
        assert reg_at is not None

        # 再次插入非 success 状态：registered_at 不应被抹掉（COALESCE 保留原值）
        db.insert_account("x@e.com", "p2", "c", "rt", status="failed", error="boom")
        rows = db.get_accounts(search="x@e.com")
        assert len(rows) == 1
        assert rows[0]["registered_at"] == reg_at


# ── P0-5：proxy_service 并发加锁 ──────────────────────────────────
class TestProxyConcurrency:
    def test_concurrent_get_next_no_crash(self, monkeypatch, tmp_path):
        pfile = tmp_path / "proxies.txt"
        pfile.write_text("http://h1:1\nhttp://h2:2\nhttp://h3:3\n", encoding="utf-8")
        bfile = tmp_path / "blacklist.json"
        monkeypatch.setattr(ps, "PROXY_FILE", pfile)
        monkeypatch.setattr(ps, "PROXY_BLACKLIST_FILE", bfile)

        svc = ProxyService()
        results: list[str] = []
        errors: list[Exception] = []

        def worker():
            try:
                for _ in range(30):
                    u = svc.get_next()
                    if u:
                        results.append(u)
            except Exception as e:  # pragma: no cover - 仅测试异常上报
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # 池内有 3 个健康代理，并发游标推进不得越界返回 None（锁保护）
        assert all(r.startswith("http://") for r in results)
        # 三个代理都应被取到（游标轮询未因竞态跳项）
        assert {u.split("//")[1].split(":")[0] for u in results} == {"h1", "h2", "h3"}

    def test_concurrent_mark_bad_and_get_next(self, monkeypatch, tmp_path):
        pfile = tmp_path / "proxies.txt"
        pfile.write_text("http://h1:1\nhttp://h2:2\nhttp://h3:3\n", encoding="utf-8")
        bfile = tmp_path / "blacklist.json"
        monkeypatch.setattr(ps, "PROXY_FILE", pfile)
        monkeypatch.setattr(ps, "PROXY_BLACKLIST_FILE", bfile)

        svc = ProxyService()
        errors: list[Exception] = []

        def badder():
            try:
                for _ in range(10):
                    svc.mark_bad("http://h1:1", reason="test")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        def fetcher():
            try:
                for _ in range(30):
                    svc.get_next()
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=badder) for _ in range(2)] + \
                  [threading.Thread(target=fetcher) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert svc.blacklist_size() >= 1  # h1 已被标记
