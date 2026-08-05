"""services/proxy_chain — kookeey 凭据从 proxies.txt 解析（不再硬编码）测试。"""
from __future__ import annotations

from services.proxy_chain import load_kookeey_credentials


class TestLoadKookeeyCredentials:
    def test_parses_kookeey_line(self, tmp_path):
        f = tmp_path / "p.txt"
        f.write_text(
            "# comment\ngate.kookeey.info:1000:1023701-4a2c845a:12843fee-US\n"
            "1.2.3.4:8080\n",
            encoding="utf-8",
        )
        user, pwd = load_kookeey_credentials(f)
        assert user == "1023701-4a2c845a"
        assert pwd == "12843fee-US"

    def test_returns_empty_when_no_kookeey(self, tmp_path):
        f = tmp_path / "p.txt"
        f.write_text("1.2.3.4:8080\nhttp://x:y@1.2.3.4:8080\n", encoding="utf-8")
        user, pwd = load_kookeey_credentials(f)
        assert user == "" and pwd == ""

    def test_returns_empty_when_file_missing(self, tmp_path):
        user, pwd = load_kookeey_credentials(tmp_path / "missing.txt")
        assert user == "" and pwd == ""

    def test_skips_comments_and_blanks(self, tmp_path):
        f = tmp_path / "p.txt"
        f.write_text("\n# only comment\n", encoding="utf-8")
        user, pwd = load_kookeey_credentials(f)
        assert user == "" and pwd == ""
