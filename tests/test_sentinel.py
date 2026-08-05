"""services/sentinel — OpenAI Sentinel PoW token 生成（mock session，无需真实网络）。"""
from __future__ import annotations

import json

import pytest

from services.sentinel import SentinelTokenGenerator, build_sentinel_token


class FakeResp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text

    def json(self):
        return json.loads(self.text)


class FakeSession:
    def __init__(self, resp):
        self._resp = resp
        self.posted_urls = []
        self.posted_payloads = []

    def post(self, url, data=None, headers=None, timeout=None, verify=None):
        self.posted_urls.append(url)
        self.posted_payloads.append(data)
        return self._resp


class TestBuildSentinelToken:
    def test_success_no_pow(self):
        resp = FakeResp(200, '{"token":"challenge_token","proofofwork":{"required":false}}')
        session = FakeSession(resp)
        sentinel_value, oai_sc = build_sentinel_token(session, "dev-123", "email_otp_validate")

        # 请求发往 sentinel 服务
        assert session.posted_urls == ["https://sentinel.openai.com/backend-api/sentinel/req"]
        # oai-sc = "0" + 服务端 challenge token
        assert oai_sc == "0challenge_token"
        # 返回的 sentinel value 是含 flow 的 JSON
        data = json.loads(sentinel_value)
        assert data["flow"] == "email_otp_validate"
        assert data["id"] == "dev-123"
        assert data["t"] == ""
        # p 值（PoW/requirements token）以 gAAAA 开头
        assert data["p"].startswith("gAAAA")

    def test_server_error_raises(self):
        # 500 且返回 JSON 时明确报错
        session = FakeSession(FakeResp(500, '{"token":""}'))
        with pytest.raises(RuntimeError, match="sentinel"):
            build_sentinel_token(session, "dev", "flow")

    def test_empty_token_raises(self):
        session = FakeSession(FakeResp(200, '{"token":"","proofofwork":{}}'))
        with pytest.raises(RuntimeError, match="sentinel"):
            build_sentinel_token(session, "dev", "flow")

    def test_bad_json_falls_back(self):
        # 服务端返回不可解析内容时，走 fallback 而非崩溃
        session = FakeSession(FakeResp(200, "not-json"))
        sentinel_value, oai_sc = build_sentinel_token(session, "dev", "flow")
        assert oai_sc == ""
        data = json.loads(sentinel_value)
        assert data["id"] == "dev"


class TestSentinelTokenGenerator:
    def test_requirements_token_prefix(self):
        gen = SentinelTokenGenerator("dev", "ua")
        assert gen.generate_requirements_token().startswith("gAAAAAC")

    def test_generate_token_with_difficulty_zero_is_fast(self):
        gen = SentinelTokenGenerator("dev", "ua")
        token = gen.generate_token("seed", "0")
        assert token.startswith("gAAAAAB")

    def test_fnv1a_deterministic(self):
        gen = SentinelTokenGenerator("dev", "ua")
        assert gen._fnv1a_32("hello") == gen._fnv1a_32("hello")
        assert len(gen._fnv1a_32("hello")) == 8
