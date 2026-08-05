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

# chatgpt2api 默认地址（可被 config 覆盖）
CHATGPT2API_DEFAULT_URL = "http://localhost:8787"
