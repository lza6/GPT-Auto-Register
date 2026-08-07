"""OpenAI OAuth 常量集中管理（B5：消除散落硬编码）。

之前 client_id/redirect_uri/audience/auth0Client 在 6 处各写一遍，
改一处漏五处。现收敛到本模块，调用方统一 import。

向后兼容：browser_register/protocol_register/revive_import 原内联常量
保留为对本模块常量的别名，不破坏旧 import 路径。
"""
from __future__ import annotations

# OpenAI OAuth PKCE 常量（与 chatgpt2api 同一 client，注册成功可拿 refresh_token 长期续期）
OAUTH_CLIENT_ID = "app_2SKx67EdpoN0G6j64rFvigXD"
OAUTH_REDIRECT_URI = "https://platform.openai.com/auth/callback"
OAUTH_AUDIENCE = "https://api.openai.com/v1"
OAUTH_AUTH0_CLIENT = "eyJuYW1lIjoiYXV0aDAtc3BhLWpzIiwidmVyc2lvbiI6IjEuMjEuMCJ9"
OAUTH_ISSUER = "https://auth.openai.com"
OAUTH_TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
OAUTH_AUTHORIZE_URL = "https://auth.openai.com/api/accounts/authorize"


def tls_verify_enabled(config: dict) -> bool:
    """TLS 证书校验开关（默认 true）。

    v3.1 安全审计：原各 OpenAI 出站请求全局 verify=False，携带 refresh_token/账号密码/
    OAuth code 经第三方代理时有 MITM 风险。实测经本地/CONNECT 隧道代理 verify=True 可用
    （不中断端到端 TLS），故默认开启；若部署代理做 SSL 拦截导致证书错误，设 tls_verify=false 回退。
    """
    v = config.get("tls_verify", True)
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


# ── 反风控 TLS 指纹池（一账号一指纹）────────────────────────────
# OpenAI/Cloudflare 风控不只看 IP，还看 TLS ClientHello 的 JA3/JA4 指纹 + HTTP/2 帧序。
# 批量注册全用同一 chrome 指纹会被聚类识别为"同一客户端在批量注册"。
# 配合一账号一 IP（网络层隔离），指纹层再隔离一层 → 一账号一指纹一 IP。
#
# 默认池只放 Chrome 各版本（行为最接近现状，最保守）；Firefox/Safari 可作为可选池
# （tls_fingerprint_pool 显式配置才混入）。curl_cffi 0.16.0 实测可用。
TLS_FINGERPRINT_POOL = (
    "chrome120", "chrome123", "chrome124", "chrome131", "chrome133a",
    "chrome136", "chrome142", "chrome145", "chrome146",
)

# 各指纹族对应的 User-Agent（指纹与 UA 必须配套，否则矛盾反而更显眼被风控识别）
_UA_BY_FAMILY = {
    "chrome": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/{ver}.0.0.0 Safari/537.36",
    "firefox": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{rv}.0) "
               "Gecko/20100101 Firefox/{rv}.0",
    "safari": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/{ver}.0 Safari/605.1.15",
    "edge": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/{ver}.0.0.0 Safari/537.36 Edg/{ver}.0.0.0",
}


def pick_fingerprint(config: dict) -> str:
    """按配置选 TLS 指纹（一账号一指纹的入口）。

    - config.tls_fingerprint 固定单个指纹（调试用，空=池内随机）
    - config.tls_fingerprint_pool 自定义池（逗号分隔，空=默认 Chrome 池）
    - 非法值回退默认池随机
    """
    import random
    fixed = str(config.get("tls_fingerprint") or "").strip()
    if fixed:
        return fixed
    pool_raw = str(config.get("tls_fingerprint_pool") or "").strip()
    pool = [p.strip() for p in pool_raw.split(",") if p.strip()] if pool_raw else list(TLS_FINGERPRINT_POOL)
    return random.choice(pool or list(TLS_FINGERPRINT_POOL))


def ua_for_fingerprint(fingerprint: str) -> str:
    """按 TLS 指纹产出配套的 User-Agent（指纹与 UA 必须一致，否则风控更易识别矛盾）。

    chrome123 → Chrome/123 UA；firefox135 → Firefox/135 UA；safari180 → Safari UA。
    版本号从指纹名解析（chrome123→123；chrome133a→133；safari180→18.0 取 18）。
    """
    import re
    fp = str(fingerprint or "").strip().lower()
    m = re.search(r"(\d+)", fp)
    ver = m.group(1) if m else "120"
    if fp.startswith("firefox"):
        rv = ver[:3]  # firefox133→133
        return _UA_BY_FAMILY["firefox"].format(rv=rv)
    if fp.startswith("safari"):
        # safari180 → 18.0 → 取主版本 18
        maj = ver[:2] if len(ver) >= 2 else ver
        return _UA_BY_FAMILY["safari"].format(ver=maj)
    if fp.startswith("edge"):
        return _UA_BY_FAMILY["edge"].format(ver=ver)
    # 默认 chrome（含 chrome131_android 等，UA 仍用桌面 Chrome 即可——指纹是 TLS 层）
    return _UA_BY_FAMILY["chrome"].format(ver=ver)
