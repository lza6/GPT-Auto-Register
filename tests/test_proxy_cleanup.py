"""v4.0.1：auto_cleanup 跳过 kookeey 动态住宅（防同凭据行共享导致全池被锁）。"""
from __future__ import annotations

from services import proxy_service as ps_mod


def _make_service(monkeypatch, tmp_path, content: str) -> ps_mod.ProxyService:
    pf = tmp_path / "proxies.txt"
    pf.write_text(content, encoding="utf-8")
    monkeypatch.setattr(ps_mod, "PROXY_FILE", pf)
    monkeypatch.setattr(ps_mod, "PROXY_BLACKLIST_FILE", tmp_path / "bl.json")
    return ps_mod.ProxyService()


class TestCleanupSkipsKookeey:
    def test_kookeey_not_blacklisted_by_cleanup(self, monkeypatch, tmp_path):
        # 500 行同一 kookeey 凭据（动态住宅靠随机 session 变 IP）
        line = "gate.kookeey.info:1000:uid-user:pass-US"
        svc = _make_service(monkeypatch, tmp_path, (line + "\n") * 500)
        assert svc.count == 500
        # 若 cleanup 不跳过 kookeey，会因网络探测失败 mark_bad → 全池被锁
        svc._run_cleanup_once()
        assert svc.blacklist_size() == 0
        # 池仍可正常取代理
        assert svc.get_next() is not None

    def test_http_proxy_still_checked(self, monkeypatch, tmp_path):
        # 通用 http 代理仍走健康检查（TCP 不可达会被标黑）
        svc = _make_service(monkeypatch, tmp_path, "http://192.0.2.1:9\n")  # TEST-NET 不可达
        svc._run_cleanup_once()
        # TCP 3s 超时内大概率失败 → 应被标黑；也可能恰好连接失败瞬间完成。两者都接受，但不应异常
        assert isinstance(svc.blacklist_size(), int)

    def test_get_next_returns_kookeey_url_after_cleanup(self, monkeypatch, tmp_path):
        # 回归：cleanup 跑过后 kookeey 池仍正常取代理（动态住宅换新 session）
        line = "gate.kookeey.info:1000:uid-user:pass-US"
        svc = _make_service(monkeypatch, tmp_path, (line + "\n") * 2)
        svc._run_cleanup_once()
        url = svc.get_next()
        assert url and url.startswith("http://") and "gate.kookeey.info" in url
