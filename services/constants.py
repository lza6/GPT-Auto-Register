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
