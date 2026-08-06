"""v3.1 T7 — token 巡检真实刷新冒烟（真实 OpenAI token，非 mock）。

用途：用真实 refresh_token 验证 token_refresher 的 _scan_once 端到端——
真实经配置代理连 OpenAI token 端点，换新 access_token 并落库。

前置（缺一不可，否则属硬外部边界）：
  - 至少 1 个带真实 openai_refresh_token 的账号（真实库 accounts 表，
    或 data/export_chatgpt2api.json 里有 refresh_token 的账号）
  - 代理可达 OpenAI（config.proxy_url 或代理池）

运行：
    ./.venv/Scripts/python.exe scripts/smoke_token_refresh.py

退出码：0=PASS，1=FAIL，2=无可用真实凭证（跳过，非失败）。
安全：在临时库运行，不动真实 data/register.db；不打印完整 token。
注意：OpenAI 每次刷新返回新 refresh_token，但旧的在窗口期内仍可用（已实证）。
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _find_real_account() -> dict | None:
    """找一个带真实 refresh_token 的账号：先真实库，再导出文件。"""
    import services.db as dbmod
    try:
        rows = dbmod.get_accounts(status="success")
        for a in rows:
            if a.get("openai_refresh_token"):
                return {"email": a["email"], "refresh_token": a["openai_refresh_token"]}
    except Exception:
        pass
    exp = Path(__file__).resolve().parent.parent / "data" / "export_chatgpt2api.json"
    if exp.exists():
        data = json.loads(exp.read_text(encoding="utf-8"))
        accs = data if isinstance(data, list) else data.get("accounts", [])
        for a in accs:
            if a.get("refresh_token"):
                return {"email": a.get("email", "?"), "refresh_token": a["refresh_token"]}
    return None


def _mask(email: str) -> str:
    return email[:3] + "***" + email[email.find("@"):] if "@" in email else "***"


async def main() -> int:
    import services.db as dbmod
    from services.token_refresher import TokenRefresher

    acc = _find_real_account()
    if not acc:
        print("SKIP: 本机无带 refresh_token 的真实账号（硬外部边界，非代码问题）")
        return 2

    # 读真实 config 取代理（与生产一致）
    cfg_path = Path(__file__).resolve().parent.parent / "config.json"
    config = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}

    # 临时库隔离
    tmp = tempfile.mkdtemp()
    orig = dbmod.DB_PATH
    dbmod.DB_PATH = Path(tmp) / "t.db"
    dbmod.init_db()
    try:
        dbmod.insert_account(acc["email"], "p", "c", "r",
                             access_token="OLD_PLACEHOLDER", status="success",
                             openai_refresh_token=acc["refresh_token"])
        r = TokenRefresher(config)
        print("代理:", r._resolve_proxy() or "(直连)")
        res = await r._scan_once()  # 真实 _refresh_token，不 mock
        row = dbmod.get_accounts(status="success")[0]
        new_at = row["access_token"]
        ok = (res["refreshed"] == 1 and new_at.startswith("eyJ")
              and new_at != "OLD_PLACEHOLDER")
        print(f"账号: {_mask(acc['email'])}")
        print(f"_scan_once: {res} | access_token 已更新为真实JWT: {new_at.startswith('eyJ')}")
        print("T7_REAL_REFRESH_SMOKE:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        dbmod.DB_PATH = orig
        dbmod.init_db()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
