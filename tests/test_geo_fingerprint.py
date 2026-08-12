"""v4.0 P0-2 指纹地理联动：IP 国家 → 语言/时区/Accept-Language。"""
from __future__ import annotations

import pytest

import services.protocol_register as pr_mod
from services import proxy_service as ps_mod
from services.constants import COUNTRY_LOCALE, country_locale


class TestCountryLocale:
    def test_us_default_when_unknown_or_empty(self):
        assert country_locale("")["lang"] == "en-US"
        assert country_locale("xx")["lang"] == "en-US"
        assert country_locale(None)["lang"] == "en-US"

    def test_jp_profile(self):
        geo = country_locale("JP")
        assert geo["lang"] == "ja-JP"
        assert geo["timezone"] == "Asia/Tokyo"
        assert geo["lang_full"].startswith("ja-JP")

    def test_case_insensitive(self):
        assert country_locale("us")["lang"] == "en-US"
        assert country_locale("jP")["timezone"] == "Asia/Tokyo"

    def test_accept_lang_has_q_values(self):
        for cc, geo in COUNTRY_LOCALE.items():
            assert geo["lang_full"].startswith(geo["lang"]), cc
            assert ";q=" in geo["lang_full"], cc
            assert "/" in geo["timezone"], cc  # IANA 时区格式

    def test_common_countries_present(self):
        for cc in ("US", "CA", "GB", "DE", "FR", "JP", "SG", "AU", "KR", "IN", "BR"):
            assert cc in COUNTRY_LOCALE, cc


class TestProxyLastCountry:
    def _make_service(self, monkeypatch, tmp_path, content: str) -> ps_mod.ProxyService:
        pf = tmp_path / "proxies.txt"
        pf.write_text(content, encoding="utf-8")
        monkeypatch.setattr(ps_mod, "PROXY_FILE", pf)
        return ps_mod.ProxyService()

    def test_kookeey_updates_last_country(self, monkeypatch, tmp_path):
        svc = self._make_service(
            monkeypatch, tmp_path,
            "gate.kookeey.info:1000:uid-user:pass-JP-Random\n",
        )
        url = svc.get_next()
        assert url and "JP" in url
        assert svc.last_country == "JP"

    def test_http_proxy_keeps_default_country(self, monkeypatch, tmp_path):
        svc = self._make_service(monkeypatch, tmp_path, "http://1.2.3.4:8080\n")
        svc.get_next()
        # 通用 http 代理无国家信息 → 保持默认（US）
        assert svc.last_country == "US"


class TestGeoApplied:
    def test_base_headers_accept_lang_follows_geo(self):
        reg = pr_mod.ProtocolRegister({})
        h = reg._base_headers("https://auth.openai.com/x", "dev", "", "ja-JP,ja;q=0.9,en;q=0.8")
        assert h["accept-language"] == "ja-JP,ja;q=0.9,en;q=0.8"
        # 未传时回退美区默认（不再硬编码 zh-CN）
        h2 = reg._base_headers("https://auth.openai.com/x", "dev")
        assert h2["accept-language"] == "en-US,en;q=0.9"

    def test_register_context_geo_fields(self):
        ctx = pr_mod.RegistrationContext()
        assert ctx.geo_lang == "en-US"
        assert ctx.geo_timezone == "America/New_York"

    def test_geo_country_config_override(self, monkeypatch):
        # config.geo_country 显式指定国家时优先（不依赖代理探测）
        reg = pr_mod.ProtocolRegister({"geo_country": "JP"})
        # _register_sync 里 geo_country 取 config 优先；这里直接验证 country_locale 配合
        from services.constants import country_locale as cl
        assert cl(reg.config.get("geo_country"))["lang"] == "ja-JP"
