"""v3.1.2 — chatgpt2api 导出适配 + 一键补齐 + 静态 no-cache 测试。

实证基础：chatgpt2api 的 api/accounts.py + services/account_service.py 导入契约
（password=OpenAI账号密码、mail_credential={client_id, 微软refresh_token} 供 OTP 重登）。
"""
from __future__ import annotations

from services import db

HDR = {"X-Auth-Key": "test-admin-key"}


class TestBuildChatgpt2apiAccount:
    def test_password_is_openai_password_not_microsoft(self):
        from api.register import _build_chatgpt2api_account
        acc = {"email": "a@x.com", "password": "MS_PW", "openai_password": "OPENAI_PW",
               "client_id": "cid", "refresh_token": "ms_rt"}
        out = _build_chatgpt2api_account(acc, {"access_token": "at", "refresh_token": "rt", "id_token": "id"})
        assert out["password"] == "OPENAI_PW", "chatgpt2api 凭据重登需要 OpenAI 密码，不是微软邮箱密码"

    def test_password_fallback_to_microsoft_when_no_openai(self):
        from api.register import _build_chatgpt2api_account
        acc = {"email": "a@x.com", "password": "MS_PW", "openai_password": "",
               "client_id": "cid", "refresh_token": "ms_rt"}
        out = _build_chatgpt2api_account(acc, {"access_token": "at", "refresh_token": "rt"})
        assert out["password"] == "MS_PW"

    def test_mail_credential_present_when_client_id_and_rt(self):
        from api.register import _build_chatgpt2api_account
        acc = {"email": "a@x.com", "password": "p", "openai_password": "op",
               "client_id": "cid", "refresh_token": "ms_rt"}
        out = _build_chatgpt2api_account(acc, {"access_token": "at", "refresh_token": "rt"})
        assert out["mail_credential"] == {"client_id": "cid", "refresh_token": "ms_rt"}

    def test_mail_credential_omitted_when_missing(self):
        from api.register import _build_chatgpt2api_account
        acc = {"email": "a@x.com", "password": "p", "client_id": "", "refresh_token": ""}
        out = _build_chatgpt2api_account(acc, {"access_token": "at", "refresh_token": "rt"})
        assert "mail_credential" not in out


class TestReplenishTokens:
    def test_replenish_success(self, client, isolated_db, monkeypatch):
        import api.register as reg_mod
        db.insert_account("a@x.com", "p", "c", "ms_rt", access_token="", status="success_no_token",
                          openai_refresh_token="ort_a")
        from services.token_refresher import get_token_refresher
        refresher = get_token_refresher({})

        async def fake_refresh(rt):
            return {"access_token": "eyJNEW", "refresh_token": "ort_a_new", "id_token": ""}
        monkeypatch.setattr(refresher, "_refresh_token", fake_refresh)

        resp = client.post("/api/register/replenish-tokens", json={}, headers=HDR)
        assert resp.status_code == 200
        body = resp.json()
        assert body["replenished"] == 1 and body["failed"] == 0
        # 落库验证：access_token 更新 + 轮换新 RT 落库
        acc = db.get_accounts(status="success_no_token")[0]
        assert acc["access_token"] == "eyJNEW"
        assert acc["openai_refresh_token"] == "ort_a_new"

    def test_replenish_no_rt_marks_need_reregister(self, client, isolated_db):
        # 无 openai_refresh_token 的账号无法自动补齐
        db.insert_account("b@x.com", "p", "c", "ms_rt", access_token="", status="success_no_token",
                          openai_refresh_token="")
        resp = client.post("/api/register/replenish-tokens", json={}, headers=HDR)
        body = resp.json()
        assert body["replenished"] == 0
        assert body["need_reregister"] == 1

    def test_replenish_skips_valid_token_accounts(self, client, isolated_db, monkeypatch):
        # 已有有效 access_token 的账号不应被补齐（不误刷）
        db.insert_account("c@x.com", "p", "c", "ms_rt", access_token="eyJ" + "x" * 300,
                          status="success", openai_refresh_token="ort_c")
        import api.register as reg_mod
        from services.token_refresher import get_token_refresher
        refresher = get_token_refresher({})
        called = {"n": 0}

        async def fake_refresh(rt):
            called["n"] += 1
            return None
        monkeypatch.setattr(refresher, "_refresh_token", fake_refresh)
        resp = client.post("/api/register/replenish-tokens", json={}, headers=HDR)
        assert resp.json()["replenished"] == 0
        assert called["n"] == 0  # 没动有效账号


class TestStaticNoCache:
    def test_static_assets_no_cache(self, client):
        # v3.1.2：静态资源 no-cache，保证每次启动加载最新 UI
        for path in ("/", "/app.js", "/app.css"):
            r = client.get(path)
            assert r.status_code == 200
            assert "no-cache" in r.headers.get("Cache-Control", ""), f"{path} 应带 no-cache"
