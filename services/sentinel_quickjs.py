"""QuickJS 驱动的 Sentinel token 生成器（真实 sdk.js 求解）。

移植自 https://github.com/zc-zhangchen/any-auto-register（MIT License），
由 LocalFlow Register 项目适配后二次移植到本项目。

为什么需要它：
  纯 Python 合成 PoW（services/sentinel.py）能过 OpenAI 的表层校验
  （/sentinel/req 返回 200），但 OTP 分发服务会在服务端运行真实的
  sentinel SDK JS 做深度校验，合成 token 过不了 → 邮件验证码被静默丢弃
  （silent-drop）。要过就必须在 JS VM 里运行 OpenAI 真实下发的 sdk.js，
  产出与真浏览器一致的 token。

实现：
  - 每次 token 请求 spawn 一次 ``node -e <wrapper>``（Node 内置 vm 模块，V8）
  - wrapper 加载 OpenAI 的 sdk.js + ``openai_sentinel_quickjs.js``（本模块同目录）
  - 两段式：action=requirements → request_p，POST /sentinel/req → challenge，
    action=solve → sdk_token + so_token
  - 返回 ``(sdk_token, so_token)``：sdk_token 直接作为
    ``openai-sentinel-token`` header 值（字符串，与合成路径的 JSON 不同），
    so_token 放 ``openai-sentinel-so-token``（服务端未要求则为空串）

公共 API：
  - ``get_sentinel_token_via_quickjs(session, device_id, *, flow, ...) -> (str, str) | None``
  - ``QuickJSNetworkError``：链路级 TLS/网络瞬断（应上抛交给上层 network 分类，
    而非当成 PoW 失败降级）
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# sdk 版本随 OpenAI 侧更新（本地缓存在 /tmp，版本变化时清理缓存目录即可）
SENTINEL_VERSION = "20260219f9f6"
SENTINEL_SDK_URL = f"https://sentinel.openai.com/sentinel/{SENTINEL_VERSION}/sdk.js"
SENTINEL_REQ_URL = "https://sentinel.openai.com/backend-api/sentinel/req"

# 链路级 TLS 瞬断 / 网络错误标记（curl_cffi/ssl/连接层）
_NETWORK_ERROR_MARKERS = (
    "tls", "ssl", "sslerror", "eof occurred", "connection",
    "connect error", "timeout", "timed out", "proxy", "socks",
    "dns", "name resolution", "curl: (35)", "curl: (28)", "curl: (6)",
    "curl: (7)", "remote disconnected", "connection reset",
    "connection aborted", "max retries exceeded", "econnrefused",
    "econnreset", "etimedout", "broken pipe",
)


class QuickJSNetworkError(RuntimeError):
    """链路级网络/TLS 瞬断。上抛给上层按 network 分类/降级，而非降级合成 PoW。"""


def _resolve_node_binary() -> str:
    return (os.getenv("OPENAI_SENTINEL_NODE_PATH", "") or "").strip() or "node"


def node_available() -> bool:
    """node 可执行文件是否可用（找不到时 quickjs 路径自动降级合成）。"""
    try:
        return bool(shutil.which(_resolve_node_binary()))
    except Exception:
        return False


def _quickjs_script_path() -> Path:
    return Path(__file__).resolve().parent / "openai_sentinel_quickjs.js"


def quickjs_script_available() -> bool:
    return _quickjs_script_path().exists()


def _looks_like_network_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _NETWORK_ERROR_MARKERS)


_sdk_file_cache: Optional[Path] = None


def _ensure_sdk_file(session: Any, timeout_ms: int) -> Path:
    """下载 OpenAI 真实 sdk.js 到临时目录缓存（每个版本一次性）。"""
    global _sdk_file_cache
    if _sdk_file_cache and _sdk_file_cache.exists():
        return _sdk_file_cache

    cache_dir = Path(tempfile.gettempdir()) / "openai-sentinel-demo" / SENTINEL_VERSION
    cache_dir.mkdir(parents=True, exist_ok=True)
    sdk_file = cache_dir / "sdk.js"
    if sdk_file.exists() and sdk_file.stat().st_size > 0:
        _sdk_file_cache = sdk_file
        return sdk_file

    try:
        resp = session.get(
            SENTINEL_SDK_URL,
            headers={
                "accept": "*/*",
                "accept-language": "zh-CN,zh;q=0.9",
                "referer": "https://auth.openai.com/",
                "sec-fetch-dest": "script",
                "sec-fetch-mode": "no-cors",
                "sec-fetch-site": "same-site",
            },
            timeout=max(10, int(timeout_ms / 1000)),
        )
    except Exception as e:
        if _looks_like_network_error(e):
            raise QuickJSNetworkError(f"下载 sdk.js 网络异常: {e}") from e
        raise
    if getattr(resp, "status_code", 0) != 200:
        raise RuntimeError(f"下载 sdk.js 失败: HTTP {resp.status_code}")
    content = getattr(resp, "content", b"") or (resp.text or "").encode()
    if not content:
        raise RuntimeError("下载 sdk.js 失败: 响应为空")
    sdk_file.write_bytes(content)
    _sdk_file_cache = sdk_file
    return sdk_file


def _run_quickjs_action(
    *,
    action: str,
    sdk_file: Path,
    quickjs_script: Path,
    payload: dict,
    timeout_ms: int,
) -> dict:
    body = dict(payload)
    body["action"] = action
    try:
        proc = subprocess.run(
            [_resolve_node_binary(), str(quickjs_script)],
            input=json.dumps(body, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=max(10, int(timeout_ms / 1000) + 5),
            env={**os.environ, "OPENAI_SENTINEL_SDK_FILE": str(sdk_file)},
        )
    except OSError as e:
        raise RuntimeError(f"QuickJS 无法启动 node: {e}") from e
    except subprocess.TimeoutExpired:
        raise RuntimeError("QuickJS 执行超时")
    if proc.returncode != 0:
        raise RuntimeError(
            f"QuickJS 执行失败: {(proc.stderr or proc.stdout or 'unknown').strip()[:300]}"
        )
    out = (proc.stdout or "").strip()
    if not out:
        raise RuntimeError("QuickJS 返回空输出")
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"QuickJS 输出不是 JSON: {out[:200]}") from e
    if not isinstance(data, dict):
        raise RuntimeError("QuickJS 输出不是 JSON 对象")
    return data


def _fetch_sentinel_challenge(
    session: Any,
    *,
    device_id: str,
    flow: str,
    request_p: str,
    timeout_ms: int,
) -> dict:
    body = {"p": request_p, "id": device_id, "flow": flow}
    try:
        resp = session.post(
            SENTINEL_REQ_URL,
            data=json.dumps(body, separators=(",", ":")),
            headers={
                "origin": "https://sentinel.openai.com",
                "referer": (
                    f"https://sentinel.openai.com/backend-api/sentinel/"
                    f"frame.html?sv={SENTINEL_VERSION}"
                ),
                "content-type": "text/plain;charset=UTF-8",
                "accept": "*/*",
                "accept-encoding": "gzip, deflate, br, zstd",
                "accept-language": "zh-CN,zh;q=0.9",
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
            },
            timeout=max(10, int(timeout_ms / 1000)),
        )
    except Exception as e:
        if _looks_like_network_error(e):
            raise QuickJSNetworkError(f"/sentinel/req 网络异常: {e}") from e
        raise
    if getattr(resp, "status_code", 0) != 200:
        raise RuntimeError(f"/sentinel/req HTTP {resp.status_code}")
    try:
        payload = resp.json()
    except Exception as e:
        raise RuntimeError("/sentinel/req 响应不是 JSON") from e
    if not isinstance(payload, dict):
        raise RuntimeError("Sentinel challenge 响应不是 JSON 对象")
    return payload


def get_sentinel_token_via_quickjs(
    session: Any,
    device_id: str,
    *,
    flow: str = "authorize_continue",
    timeout_ms: int = 45000,
    user_agent: str = "",
    screen: str = "",
    lang: str = "",
    lang_full: str = "",
    timezone: str = "",
    platform: str = "",
    vendor: str | None = None,
    hardware_concurrency: int = 0,
    device_memory: int | None = None,
    max_touch_points: int = 0,
    device_pixel_ratio: float = 0.0,
) -> tuple[str, str] | None:
    """QuickJS 路径求解 sentinel token。

    返回 ``(sdk_token, so_token)``；任何 JS/PoW 失败返回 None（由调用方降级合成）。
    链路级网络/TLS 瞬断抛 :class:`QuickJSNetworkError`（上抛给上层 network 分类），
    不被当作 PoW 失败静默吞掉。

    指纹一致性：platform/vendor/hardware_concurrency 等按调用方浏览器画像喂给
    sdk.js 的 navigator，避免 UA 说 Windows Chrome 但 navigator 报 MacIntel。
    """
    did = str(device_id or uuid.uuid4())

    screen_w, screen_h = "1920", "1080"
    if screen and "x" in screen:
        parts = screen.split("x", 1)
        screen_w, screen_h = parts[0], parts[1]

    lang_primary = lang or "en-US"
    languages = [lang_primary]
    if lang_full:
        for part in lang_full.split(","):
            tag = part.split(";")[0].strip()
            if tag and tag not in languages:
                languages.append(tag)

    ua_l = (user_agent or "").lower()
    if not platform:
        if "iphone" in ua_l:
            platform = "iPhone"
        elif "windows" in ua_l:
            platform = "Win32"
        elif "mac" in ua_l:
            platform = "MacIntel"
        else:
            platform = "Win32"
    if vendor is None:
        if "firefox" in ua_l:
            vendor = ""
        elif "chrome" in ua_l:
            vendor = "Google Inc."
        else:
            vendor = "Apple Computer, Inc."
    hw_conc = int(hardware_concurrency) if hardware_concurrency else 8

    env_payload: dict[str, Any] = {
        "device_id": did,
        "user_agent": user_agent or "Mozilla/5.0",
        "screen_width": screen_w,
        "screen_height": screen_h,
        "language": lang_primary,
        "languages": languages,
        "platform": platform,
        "vendor": vendor,
        "hardware_concurrency": hw_conc,
        "device_pixel_ratio": float(device_pixel_ratio) if device_pixel_ratio else 1.0,
        "max_touch_points": int(max_touch_points),
        "timezone": timezone or "UTC",
    }
    if device_memory is not None:
        env_payload["device_memory"] = int(device_memory)

    try:
        sdk_file = _ensure_sdk_file(session, timeout_ms)

        requirements = _run_quickjs_action(
            action="requirements",
            sdk_file=sdk_file,
            quickjs_script=_quickjs_script_path(),
            payload=env_payload,
            timeout_ms=timeout_ms,
        )
        request_p = str(requirements.get("request_p") or "").strip()
        if not request_p:
            logger.info("Sentinel QuickJS 失败: requirements 未返回 request_p")
            return None

        challenge = _fetch_sentinel_challenge(
            session, device_id=did, flow=flow, request_p=request_p, timeout_ms=timeout_ms,
        )
        c_value = str(challenge.get("token") or "").strip()
        if not c_value:
            logger.info("Sentinel QuickJS 失败: challenge token 为空")
            return None

        solve_payload = dict(env_payload)
        solve_payload.update({
            "request_p": request_p,
            "challenge": challenge,
            "flow": flow,
            "behavior_duration_ms": 4200,
        })
        solved = _run_quickjs_action(
            action="solve",
            sdk_file=sdk_file,
            quickjs_script=_quickjs_script_path(),
            payload=solve_payload,
            timeout_ms=timeout_ms,
        )

        so_token_raw = str(solved.get("so_token") or "").strip()
        # SO token 要不要，由服务端 challenge 决定（challenge.so.required === true 才要）。
        # username_password_create 顶层根本没有 so 键，真实浏览器同样不会产生 SO token。
        so_required = bool((challenge.get("so") or {}).get("required") is True)

        sdk_token = str(solved.get("token") or "").strip()
        if not sdk_token:
            logger.info("Sentinel QuickJS 失败: SDK token 为空，中止以避免封号")
            return None
        if so_required and not so_token_raw:
            logger.info("Sentinel QuickJS 失败: 服务端要求 SO token 但求解为空")
            return None
        logger.debug(
            "Sentinel QuickJS OK (len=%s, so=%s)",
            len(sdk_token), "Y" if so_token_raw else "N/A(服务端未要求)",
        )
        return sdk_token, so_token_raw
    except QuickJSNetworkError:
        raise
    except Exception as e:
        logger.debug("Sentinel QuickJS 异常: %s", e)
        return None
