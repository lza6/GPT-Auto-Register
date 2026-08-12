"""services/sentinel_quickjs + protocol_register quickjs 集成（mock node/网络，无需真实 node）。"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

import services.protocol_register as pr_mod
import services.sentinel_quickjs as sq


class FakeResp:
    def __init__(self, status_code=200, text="", content=b""):
        self.status_code = status_code
        self.text = text
        self.content = content

    def json(self):
        return json.loads(self.text)


class FakeProc:
    def __init__(self, returncode=0, stdout="{}", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeSession:
    """同时支持 get（下 sdk）与 post（sentinel req）的伪 session。"""

    def __init__(self, sdk_resp=None, req_resp=None):
        self._sdk_resp = sdk_resp or FakeResp(200, content=b"var SentinelSDK=...;")
        self._req_resp = req_resp or FakeResp(
            200,
            json.dumps({"token": "challenge_token", "proofofwork": {"required": True, "seed": "s", "difficulty": "0"}}),
        )
        self.get_urls: list[str] = []
        self.post_urls: list[str] = []
        self.post_bodies: list[str] = []
        self.cookies = SimpleNamespace(set=lambda *a, **k: None)

    def get(self, url, headers=None, timeout=None):
        self.get_urls.append(url)
        return self._sdk_resp

    def post(self, url, data=None, headers=None, timeout=None):
        self.post_urls.append(url)
        self.post_bodies.append(data)
        return self._req_resp


def _mock_subprocess(monkeypatch, proc: FakeProc):
    def fake_run(*args, **kwargs):
        return proc
    monkeypatch.setattr(subprocess, "run", fake_run)


def _solve_payload(token: str = "sdk_token_x", so_token: str = "") -> dict:
    return {"token": token, "so_token": so_token}


# ── sentinel_quickjs 模块 ────────────────────────────────────


class TestQuickjsModule:
    def test_script_available(self):
        assert sq.quickjs_script_available() is True

    def test_node_available_is_bool(self):
        assert isinstance(sq.node_available(), bool)

    def test_looks_like_network_error(self):
        assert sq._looks_like_network_error(Exception("curl: (35) TLS connect error"))
        assert sq._looks_like_network_error(Exception("timed out"))
        assert not sq._looks_like_network_error(Exception("SyntaxError: unexpected token"))

    def test_ensure_sdk_file_caches(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sq, "_sdk_file_cache", None)
        monkeypatch.setattr(sq.tempfile, "gettempdir", lambda: str(tmp_path))
        session = FakeSession(sdk_resp=FakeResp(200, content=b"sdk-content"))
        path = sq._ensure_sdk_file(session, 30000)
        assert path.exists()
        assert path.read_bytes() == b"sdk-content"
        # 命中缓存：不再发第二次请求
        monkeypatch.setattr(session, "_sdk_resp", FakeResp(500))
        path2 = sq._ensure_sdk_file(session, 30000)
        assert path2 == path

    def test_ensure_sdk_file_network_error_raises_network(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sq, "_sdk_file_cache", None)
        monkeypatch.setattr(sq.tempfile, "gettempdir", lambda: str(tmp_path))

        def boom(*a, **k):
            raise OSError("TLS connect error")
        session = FakeSession()
        monkeypatch.setattr(session, "get", boom)
        with pytest.raises(sq.QuickJSNetworkError):
            sq._ensure_sdk_file(session, 30000)

    def test_run_quickjs_action_parses_json(self, monkeypatch):
        proc = FakeProc(0, '{"request_p": "abc"}')
        _mock_subprocess(monkeypatch, proc)
        out = sq._run_quickjs_action(
            action="requirements", sdk_file="s", quickjs_script="q",
            payload={"device_id": "d"}, timeout_ms=30000,
        )
        assert out == {"request_p": "abc"}

    def test_run_quickjs_action_nonzero_raises(self, monkeypatch):
        _mock_subprocess(monkeypatch, FakeProc(1, "", "boom"))
        with pytest.raises(RuntimeError, match="QuickJS 执行失败"):
            sq._run_quickjs_action(
                action="requirements", sdk_file="s", quickjs_script="q",
                payload={}, timeout_ms=30000,
            )


# ── get_sentinel_token_via_quickjs 全链路 ───────────────────


class TestQuickjsFullFlow:
    def _run(self, monkeypatch, tmp_path, challenge=None, solve=None, sdk_resp=None):
        monkeypatch.setattr(sq, "_sdk_file_cache", None)
        monkeypatch.setattr(sq.tempfile, "gettempdir", lambda: str(tmp_path))
        challenge = challenge or {"token": "challenge_token", "proofofwork": {"required": True, "seed": "s", "difficulty": "0"}}
        req_resp = FakeResp(200, json.dumps(challenge))
        session = FakeSession(sdk_resp=sdk_resp or FakeResp(200, content=b"sdk"), req_resp=req_resp)

        results = iter(solve or [_solve_payload()])
        def fake_run(*args, **kwargs):
            body = json.loads(kwargs["input"])
            if body["action"] == "requirements":
                return FakeProc(0, '{"request_p": "reqp"}')
            return FakeProc(0, json.dumps(next(results)))
        monkeypatch.setattr(subprocess, "run", fake_run)
        return sq.get_sentinel_token_via_quickjs(
            session, "dev-1", flow="authorize_continue", user_agent="Mozilla/5.0 Chrome/146",
        ), session

    def test_success_with_so_token(self, monkeypatch, tmp_path):
        solve = [_solve_payload(token="sdk_tok", so_token="so_tok")]
        challenge = {"token": "ct", "so": {"required": True}, "proofofwork": {"required": True}}
        result, session = self._run(monkeypatch, tmp_path, challenge=challenge, solve=solve)
        assert result == ("sdk_tok", "so_tok")
        assert session.post_urls == ["https://sentinel.openai.com/backend-api/sentinel/req"]

    def test_so_not_required_no_so_token(self, monkeypatch, tmp_path):
        # username_password_create 类 flow：服务端不下发 so 块 → so_token 为空不判失败
        challenge = {"token": "ct", "proofofwork": {"required": True}}
        result, _ = self._run(monkeypatch, tmp_path, challenge=challenge)
        assert result == ("sdk_token_x", "")

    def test_so_required_but_missing_returns_none(self, monkeypatch, tmp_path):
        challenge = {"token": "ct", "so": {"required": True}, "proofofwork": {"required": True}}
        result, _ = self._run(monkeypatch, tmp_path, challenge=challenge, solve=[_solve_payload(so_token="")])
        assert result is None

    def test_network_error_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sq, "_sdk_file_cache", None)
        monkeypatch.setattr(sq.tempfile, "gettempdir", lambda: str(tmp_path))
        session = FakeSession()
        # 走到 /sentinel/req 前 requirements 已成功
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: FakeProc(0, '{"request_p": "reqp"}') if json.loads(k["input"])["action"] == "requirements" else FakeProc(0, '{"token":"x","so_token":""}'),
        )

        def boom_post(*a, **k):
            raise OSError("TLS connect error curl: (35)")
        monkeypatch.setattr(session, "post", boom_post)
        with pytest.raises(sq.QuickJSNetworkError):
            sq.get_sentinel_token_via_quickjs(session, "dev", flow="authorize_continue")


# ── protocol_register._sentinel_headers 集成 ─────────────────


class TestSentinelHeadersIntegration:
    def _reg(self):
        return pr_mod.ProtocolRegister({"sentinel_quickjs": True})

    def test_quickjs_returns_real_headers(self, monkeypatch):
        reg = self._reg()
        reg._quickjs = True
        monkeypatch.setattr(
            pr_mod, "get_sentinel_token_via_quickjs",
            lambda *a, **k: ("sdk_real_token", "so_real_token"),
        )
        headers = reg._sentinel_headers(FakeSession(), "dev", "username_password_create")
        assert headers["openai-sentinel-token"] == "sdk_real_token"
        assert headers["openai-sentinel-so-token"] == "so_real_token"

    def test_quickjs_no_so_token(self, monkeypatch):
        reg = self._reg()
        reg._quickjs = True
        monkeypatch.setattr(
            pr_mod, "get_sentinel_token_via_quickjs",
            lambda *a, **k: ("sdk_real_token", ""),
        )
        headers = reg._sentinel_headers(FakeSession(), "dev", "username_password_create")
        assert "openai-sentinel-so-token" not in headers

    def test_quickjs_network_error_propagates(self, monkeypatch):
        reg = self._reg()
        reg._quickjs = True

        def boom(*a, **k):
            raise sq.QuickJSNetworkError("TLS connect error")
        monkeypatch.setattr(pr_mod, "get_sentinel_token_via_quickjs", boom)
        with pytest.raises(sq.QuickJSNetworkError):
            reg._sentinel_headers(FakeSession(), "dev", "flow")

    def test_quickjs_failure_falls_back_to_synthetic(self, monkeypatch):
        reg = self._reg()
        reg._quickjs = True
        monkeypatch.setattr(
            pr_mod, "get_sentinel_token_via_quickjs",
            lambda *a, **k: None,
        )
        # 合成路径：build_sentinel_token 正常跑（mock session 只需 post）
        session = FakeSession()
        headers = reg._sentinel_headers(session, "dev", "flow")
        assert "openai-sentinel-token" in headers
        assert headers["openai-sentinel-token"].startswith("{")  # JSON 结构

    def test_quickjs_disabled_uses_synthetic(self):
        reg = self._reg()
        reg._quickjs = False
        headers = reg._sentinel_headers(FakeSession(), "dev", "flow")
        assert headers["openai-sentinel-token"].startswith("{")
