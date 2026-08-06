"""browser_register — OAuth code 捕获 / 换 token / push-chatgpt2api 失败分支测试。"""
from __future__ import annotations

import json

from services import db
from services.browser_register import BrowserRegister

HDR = {"X-Auth-Key": "test-admin-key"}


class FakePageUrl:
    def __init__(self, url: str):
        self._url = url

    @property
    def url(self):
        return self._url


class TestWaitForOauthCode:
    async def test_returns_code_from_url(self):
        reg = BrowserRegister({})
        page = FakePageUrl("https://platform.openai.com/auth/callback?code=ac_xyz")
        code = await reg._wait_for_oauth_code(page, timeout=1)
        assert code == "ac_xyz"

    async def test_timeout_returns_empty(self):
        reg = BrowserRegister({})
        page = FakePageUrl("https://auth.openai.com/")
        code = await reg._wait_for_oauth_code(page, timeout=0)
        assert code == ""


class FakeResp:
    def __init__(self, data):
        self._data = data
        self.status_code = 200
        self.text = json.dumps(data)

    def json(self):
        return self._data


class FakeAsyncClient:
    def __init__(self, resp_data):
        self._resp = FakeResp(resp_data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def post(self, *a, **k):
        return self._resp


class TestExchangeCode:
    async def test_success(self, monkeypatch):
        import services.browser_register as br

        reg = BrowserRegister({})
        fake = FakeAsyncClient({"access_token": "eyJat", "refresh_token": "rt", "id_token": "it"})
        monkeypatch.setattr(br.httpx, "AsyncClient", lambda *a, **k: fake)
        tokens = await reg._exchange_code("code", "verifier", None)
        assert tokens["access_token"] == "eyJat"
        assert tokens["refresh_token"] == "rt"

    async def test_empty_access_token_returns_none(self, monkeypatch):
        import services.browser_register as br

        reg = BrowserRegister({})
        fake = FakeAsyncClient({"access_token": ""})
        monkeypatch.setattr(br.httpx, "AsyncClient", lambda *a, **k: fake)
        tokens = await reg._exchange_code("code", "verifier", None)
        assert tokens is None

    async def test_network_error_returns_none(self, monkeypatch):
        import services.browser_register as br

        reg = BrowserRegister({})

        def boom(*a, **k):
            raise RuntimeError("conn refused")

        monkeypatch.setattr(br.httpx, "AsyncClient", boom)
        tokens = await reg._exchange_code("code", "verifier", None)
        assert tokens is None


class TestPushChatgpt2api:
    async def test_push_network_failure_returns_error(self, client, isolated_db, monkeypatch):
        db.insert_account(email="a@b.com", password="p", client_id="c", refresh_token="r",
                          status="success", openai_refresh_token="rt", access_token="eyJx")
        import api.register as reg_mod

        monkeypatch.setattr(reg_mod, "_get_chatgpt2api_admin_key", lambda: "real-key")
        monkeypatch.setattr(
            reg_mod, "_refresh_oauth",
            lambda ort, proxy=None: {"access_token": "eyJ" + "x" * 200, "refresh_token": ort, "id_token": ""},
        )

        def boom(*a, **k):
            raise RuntimeError("conn refused")

        monkeypatch.setattr(reg_mod.httpx, "post", boom)
        resp = client.post("/api/register/push-chatgpt2api", json={}, headers=HDR)
        assert resp.status_code == 200
        assert resp.json()["success"] is False
        assert resp.json()["pushed"] == 0

    async def test_push_missing_key_returns_error(self, client, isolated_db, monkeypatch):
        import api.register as reg_mod

        # 明确 mock 读不到密钥（config 未配置 chatgpt2api_admin_key），推送应报错而非静默用弱密钥
        monkeypatch.setattr(reg_mod, "_get_chatgpt2api_admin_key", lambda: "")
        resp = client.post("/api/register/push-chatgpt2api", json={}, headers=HDR)
        assert resp.status_code == 200
        assert resp.json()["success"] is False
        assert "密钥" in resp.json()["error"]

    async def test_push_no_accounts_returns_error(self, client, isolated_db, monkeypatch):
        import api.register as reg_mod

        monkeypatch.setattr(reg_mod, "_get_chatgpt2api_admin_key", lambda: "real-key")
        resp = client.post("/api/register/push-chatgpt2api", json={}, headers=HDR)
        assert resp.status_code == 200
        assert resp.json()["success"] is False
