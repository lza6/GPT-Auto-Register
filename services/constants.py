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


# ── IP 地理 → 语言/时区联动（v4.0 P0-2）────────────────────────
# 代理出口国家 → (主语言, Accept-Language 全量 q 值, IANA 时区)
# 原则：IP 在哪个国家，浏览器就说什么语言、时区指向该国家城市。
# 否则「出口 IP 是美/日/新加坡，Accept-Language 却是 zh-CN」是明显异常信号，
# Cloudflare/OpenAI 一眼识别批量注册。
# 常见代理国家（kookeey 国家码为 2 位大写），缺失回退 en-US。
COUNTRY_LOCALE: dict[str, dict[str, str]] = {
    "US": {"lang": "en-US", "lang_full": "en-US,en;q=0.9", "timezone": "America/New_York"},
    "CA": {"lang": "en-CA", "lang_full": "en-CA,en;q=0.9,fr-CA;q=0.8", "timezone": "America/Toronto"},
    "GB": {"lang": "en-GB", "lang_full": "en-GB,en;q=0.9", "timezone": "Europe/London"},
    "DE": {"lang": "de-DE", "lang_full": "de-DE,de;q=0.9,en;q=0.8", "timezone": "Europe/Berlin"},
    "FR": {"lang": "fr-FR", "lang_full": "fr-FR,fr;q=0.9,en;q=0.8", "timezone": "Europe/Paris"},
    "JP": {"lang": "ja-JP", "lang_full": "ja-JP,ja;q=0.9,en;q=0.8", "timezone": "Asia/Tokyo"},
    "SG": {"lang": "en-SG", "lang_full": "en-SG,en;q=0.9,zh-SG;q=0.8", "timezone": "Asia/Singapore"},
    "AU": {"lang": "en-AU", "lang_full": "en-AU,en;q=0.9", "timezone": "Australia/Sydney"},
    "NL": {"lang": "nl-NL", "lang_full": "nl-NL,nl;q=0.9,en;q=0.8", "timezone": "Europe/Amsterdam"},
    "IT": {"lang": "it-IT", "lang_full": "it-IT,it;q=0.9,en;q=0.8", "timezone": "Europe/Rome"},
    "ES": {"lang": "es-ES", "lang_full": "es-ES,es;q=0.9,en;q=0.8", "timezone": "Europe/Madrid"},
    "BR": {"lang": "pt-BR", "lang_full": "pt-BR,pt;q=0.9,en;q=0.8", "timezone": "America/Sao_Paulo"},
    "KR": {"lang": "ko-KR", "lang_full": "ko-KR,ko;q=0.9,en;q=0.8", "timezone": "Asia/Seoul"},
    "IN": {"lang": "en-IN", "lang_full": "en-IN,en;q=0.9,hi;q=0.8", "timezone": "Asia/Kolkata"},
    "TR": {"lang": "tr-TR", "lang_full": "tr-TR,tr;q=0.9,en;q=0.8", "timezone": "Europe/Istanbul"},
    "HK": {"lang": "zh-HK", "lang_full": "zh-HK,zh;q=0.9,en;q=0.8", "timezone": "Asia/Hong_Kong"},
    "TW": {"lang": "zh-TW", "lang_full": "zh-TW,zh;q=0.9,en;q=0.8", "timezone": "Asia/Taipei"},
    "ID": {"lang": "id-ID", "lang_full": "id-ID,id;q=0.9,en;q=0.8", "timezone": "Asia/Jakarta"},
    "MY": {"lang": "ms-MY", "lang_full": "ms-MY,ms;q=0.9,en;q=0.8", "timezone": "Asia/Kuala_Lumpur"},
    "VN": {"lang": "vi-VN", "lang_full": "vi-VN,vi;q=0.9,en;q=0.8", "timezone": "Asia/Ho_Chi_Minh"},
    "TH": {"lang": "th-TH", "lang_full": "th-TH,th;q=0.9,en;q=0.8", "timezone": "Asia/Bangkok"},
    "PH": {"lang": "en-PH", "lang_full": "en-PH,en;q=0.9,fil;q=0.8", "timezone": "Asia/Manila"},
    "CH": {"lang": "de-CH", "lang_full": "de-CH,de;q=0.9,fr-CH;q=0.8,it-CH;q=0.7", "timezone": "Europe/Zurich"},
    "PL": {"lang": "pl-PL", "lang_full": "pl-PL,pl;q=0.9,en;q=0.8", "timezone": "Europe/Warsaw"},
    "SE": {"lang": "sv-SE", "lang_full": "sv-SE,sv;q=0.9,en;q=0.8", "timezone": "Europe/Stockholm"},
    "NO": {"lang": "nb-NO", "lang_full": "nb-NO,nb;q=0.9,en;q=0.8", "timezone": "Europe/Oslo"},
    "DK": {"lang": "da-DK", "lang_full": "da-DK,da;q=0.9,en;q=0.8", "timezone": "Europe/Copenhagen"},
    "FI": {"lang": "fi-FI", "lang_full": "fi-FI,fi;q=0.9,en;q=0.8", "timezone": "Europe/Helsinki"},
    "IE": {"lang": "en-IE", "lang_full": "en-IE,en;q=0.9", "timezone": "Europe/Dublin"},
    "AT": {"lang": "de-AT", "lang_full": "de-AT,de;q=0.9,en;q=0.8", "timezone": "Europe/Vienna"},
    "BE": {"lang": "fr-BE", "lang_full": "fr-BE,fr;q=0.9,nl;q=0.8,en;q=0.7", "timezone": "Europe/Brussels"},
    "PT": {"lang": "pt-PT", "lang_full": "pt-PT,pt;q=0.9,en;q=0.8", "timezone": "Europe/Lisbon"},
    "RU": {"lang": "ru-RU", "lang_full": "ru-RU,ru;q=0.9,en;q=0.8", "timezone": "Europe/Moscow"},
    "UA": {"lang": "uk-UA", "lang_full": "uk-UA,uk;q=0.9,ru;q=0.8", "timezone": "Europe/Kiev"},
    "MX": {"lang": "es-MX", "lang_full": "es-MX,es;q=0.9,en;q=0.8", "timezone": "America/Mexico_City"},
    "AR": {"lang": "es-AR", "lang_full": "es-AR,es;q=0.9,en;q=0.8", "timezone": "America/Argentina/Buenos_Aires"},
    "CO": {"lang": "es-CO", "lang_full": "es-CO,es;q=0.9,en;q=0.8", "timezone": "America/Bogota"},
    "CL": {"lang": "es-CL", "lang_full": "es-CL,es;q=0.9,en;q=0.8", "timezone": "America/Santiago"},
    "PE": {"lang": "es-PE", "lang_full": "es-PE,es;q=0.9,en;q=0.8", "timezone": "America/Lima"},
    "ZA": {"lang": "en-ZA", "lang_full": "en-ZA,en;q=0.9", "timezone": "Africa/Johannesburg"},
    "NG": {"lang": "en-NG", "lang_full": "en-NG,en;q=0.9", "timezone": "Africa/Lagos"},
    "EG": {"lang": "ar-EG", "lang_full": "ar-EG,ar;q=0.9,en;q=0.8", "timezone": "Africa/Cairo"},
    "SA": {"lang": "ar-SA", "lang_full": "ar-SA,ar;q=0.9,en;q=0.8", "timezone": "Asia/Riyadh"},
    "AE": {"lang": "ar-AE", "lang_full": "ar-AE,ar;q=0.9,en;q=0.8", "timezone": "Asia/Dubai"},
    "IL": {"lang": "he-IL", "lang_full": "he-IL,he;q=0.9,en;q=0.8", "timezone": "Asia/Jerusalem"},
    "NZ": {"lang": "en-NZ", "lang_full": "en-NZ,en;q=0.9", "timezone": "Pacific/Auckland"},
}


def country_locale(country: str) -> dict[str, str]:
    """按国家码返回 (lang, lang_full, timezone)；未知/空回退美区默认。"""
    return COUNTRY_LOCALE.get(str(country or "").upper(), COUNTRY_LOCALE["US"])
