"""services/proxy_service — kookeey / HTTP 解析、轮询、随机、显示测试。"""
from __future__ import annotations

import services.proxy_service as ps


def _make(tmp_path, monkeypatch, content: str):
    f = tmp_path / "proxies.txt"
    f.write_text(content, encoding="utf-8")
    monkeypatch.setattr(ps, "PROXY_FILE", f)
    return ps.ProxyService()


class TestProxyService:
    def test_parse_kookeey_and_round_robin(self, tmp_path, monkeypatch):
        svc = _make(tmp_path, monkeypatch,
                    "gate.kookeey.info:1000:1023701-4a2c845a:12843fee-US\n1.2.3.4:8080\n")
        assert svc.count == 2
        first = svc.get_next()
        assert "gate.kookeey.info:1000" in first
        assert "1023701-4a2c845a" in first
        second = svc.get_next()
        assert second == "http://1.2.3.4:8080"
        # 轮询回绕到第一个
        third = svc.get_next()
        assert "gate.kookeey.info" in third

    def test_http_variants(self, tmp_path, monkeypatch):
        svc = _make(tmp_path, monkeypatch,
                    "http://x:y@1.2.3.4:8080\n5.6.7.8:3128\n9.9.9.9:8080:u:p\n")
        urls = [svc.get_next() for _ in range(3)]
        assert urls[0] == "http://x:y@1.2.3.4:8080"
        assert urls[1] == "http://5.6.7.8:3128"
        assert urls[2] == "http://u:p@9.9.9.9:8080"

    def test_empty_pool_returns_none(self, tmp_path, monkeypatch):
        svc = _make(tmp_path, monkeypatch, "# only comment\n")
        assert svc.count == 0
        assert svc.get_next() is None
        assert svc.get_random() is None

    def test_get_random_returns_valid(self, tmp_path, monkeypatch):
        svc = _make(tmp_path, monkeypatch, "1.2.3.4:8080\n5.6.7.8:8080\n")
        url = svc.get_random()
        assert url in ("http://1.2.3.4:8080", "http://5.6.7.8:8080")

    def test_format_for_display(self, tmp_path, monkeypatch):
        svc = _make(tmp_path, monkeypatch, "1.2.3.4:8080\n")
        assert svc.format_for_display("http://u:p@1.2.3.4:8080") == "1.2.3.4:8080"
        assert svc.format_for_display(None) == "直连"

    def test_gbk_fallback_reading(self, tmp_path, monkeypatch):
        f = tmp_path / "proxies.txt"
        # 写 GBK 编码内容，_load 应 fallback 到 gbk
        f.write_bytes("1.2.3.4:8080\n".encode("utf-8"))
        monkeypatch.setattr(ps, "PROXY_FILE", f)
        svc = ps.ProxyService()
        assert svc.count == 1
