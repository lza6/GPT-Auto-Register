# GPT 自动注册项目 — 复用技能文档

> 版本: 3.3.0 | 最后更新: 2026-08-09
> 本文件是项目**可复用技能清单**，描述常见维护任务的步骤、模式与坑。
> 改代码前先读 `docs/ONBOARDING.md` 和 `docs/GOLDEN_EXAMPLES.md`。

---

## 目录

1. [理解项目架构](#1-理解项目架构)
2. [添加新的邮箱供应商](#2-添加新的邮箱供应商)
3. [添加新的 AI 平台注册](#3-添加新的-ai-平台注册)
4. [添加新的代理类型](#4-添加新的代理类型)
5. [调试注册失败](#5-调试注册失败)
6. [处理 API 变更](#6-处理-api-变更)

---

## 1. 理解项目架构

### 1.1 系统总览

```
┌─────────────┐     ┌──────────────────────────────────────┐
│  Web 控制台  │────▶│           FastAPI 主服务 :23457        │
│  web_dist/   │     │  api/register.py (注册API)           │
└─────────────┘     │  api/emails.py   (邮箱API)           │
                    │  api/proxies.py  (代理API)           │
                    │  api/settings.py (设置API)           │
                    │  api/stats.py    (统计API)           │
                    │  api/logs.py     (日志API)           │
                    └──────────┬───────────────────────────┘
                               │
                    ┌──────────▼───────────────────────────┐
                    │        services/ (业务逻辑层)          │
                    │                                      │
                    │  register_engine.py  ← 注册调度引擎    │
                    │    ├─ protocol_register.py (协议优先)  │
                    │    └─ browser_register.py (浏览器兜底)  │
                    │       ├─ login_detector.py (形态检测)  │
                    │       └─ browser_selectors.py (选择器) │
                    │  email_service.py      (98faka API)   │
                    │  graph_email_service.py (Graph API)   │
                    │  imap_email_service.py (IMAP 直连)    │
                    │  proxy_service.py      (代理池)       │
                    │  proxy_chain.py         (链式代理)    │
                    │  token_refresher.py     (token 巡检)  │
                    │  cf_solver_service.py   (CF 解决)     │
                    │  db.py                 (SQLite 数据层) │
                    │  sentinel.py           (PoW 生成)     │
                    │  otp_extractor.py      (验证码提取)    │
                    │  name_service.py       (姓名生成)     │
                    │  notifier.py           (告警推送)     │
                    │  browser_pool.py       (浏览器池)     │
                    │  config_schema.py      (配置校验)     │
                    │  constants.py          (OAuth 常量)   │
                    └──────────┬───────────────────────────┘
                               │
                    ┌──────────▼───────────────────────────┐
                    │        data/ (持久化层)                │
                    │  register.db  (SQLite WAL)            │
                    │  logs/        (server.log + cf solver) │
                    │  backups/     (清空前自动备份)         │
                    │  proxy_blacklist.json (代理黑名单)     │
                    └────────────────────────────────────────┘

┌──────────────────────┐
│  CF Solver :8001     │  ← 独立子进程，主服务按需自启
│  cf_solver/          │
│  ├─ api_server.py    │
│  └─ boterdrop_wrapper.py
└──────────────────────┘
```

### 1.2 核心数据流（注册一个账号）

```
1. api/register.py /start → register_engine.run_batch()
2. 邮箱池 from emails 表 (status='pending')
3. 每账号独立调用 register_engine.register_one(email, password, client_id, refresh_token)
4. 路径: protocol_first? → protocol_register (curl_cffi + sentinel)
   └─ 失败 + fallback_browser? → browser_register (camoufox)
5. OAuth PKCE 流程:
   authorize → create-account(设密码) / log-in(触发OTP)
   → 取码(98faka/Graph API/IMAP) → email-otp/validate
   → about-you(填姓名生日) → create_account
   → callback code → 换 access_token + refresh_token + id_token
6. 结果写入 accounts 表，邮箱标 used
```

### 1.3 关键设计决策

| 决策 | 原因 | 文件 |
|------|------|------|
| 协议优先，浏览器兜底 | 协议速度快、资源少；浏览器兜底处理 CF 挑战 | `register_engine.py` |
| 一账号一 IP | 防批量注册被 OpenAI 风控聚类 | `protocol_register.py:_resolve_proxy` |
| 一账号一 TLS 指纹 | 防 JA3/JA4 指纹聚类 | `constants.py:pick_fingerprint` |
| OAuth PKCE | 获取 refresh_token 可长期续期，非一次性 JWT | `protocol_register.py` |
| 双引擎统一契约 | `RegisterEngineProtocol` 抽象，未来加引擎不改调度方 | `register_base.py` |
| 异步日志批量落库 | 防万级并发写 SQLite 写锁风暴 | `db.py:_log_flusher` |
| 配置类型校验 warn-only | 兼容老部署，渐进式修复 | `config_schema.py` |

### 1.4 配置项速查

配置文件 `config.json`，完整 schema 见 `config_schema.py:_CONFIG_SCHEMA`。

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `auth_key` | str | `""` | 管理密钥，环境变量 `GPT_REGISTER_AUTH_KEY` 优先 |
| `auth_enforced` | bool | false | 强制鉴权，占位符密钥拒绝启动 |
| `port` | int | 23457 | 主服务端口，环境变量 `GPT_REGISTER_PORT` 优先 |
| `proxy_file` | str | `proxies.txt` | 代理文件路径 |
| `email_source_url` | str | `""` | 91kami 邮箱源 URL |
| `email_api_base` | str | `https://app.98faka.top` | 邮件 API 基地址 |
| `register_concurrency` | int | 1 | 并发注册数 |
| `register_interval_sec` | int | 10 | 账号间间隔秒数 |
| `otp_wait_timeout_sec` | int | 600 | 验证码等待超时 |
| `use_proxy` | bool | true | 启用代理池 |
| `use_oauth_pkce` | bool | true | 使用 OAuth PKCE |
| `use_browser` | bool | true | 允许浏览器兜底 |
| `protocol_first` | bool | true | 协议优先模式 |
| `proxy_url` | str | `""` | 固定代理（覆盖代理池） |
| `tls_fingerprint` | str | `""` | 固定 TLS 指纹（空=池随机） |
| `tls_fingerprint_pool` | str | `""` | 自定义指纹池（逗号分隔） |
| `browser_pool_size` | int | 0 | 浏览器池大小（0=不池化） |
| `token_refresh_enabled` | bool | false | 启用 token 巡检 |
| `cf_retry_max` | int | 2 | CF 挑战换代理重试次数 |
| `tls_verify` | bool | true | TLS 证书校验 |
| `notify_webhook_url` | str | `""` | 告警 webhook 地址 |
| `chatgpt2api_url` | str | `http://127.0.0.1:23456` | chatgpt2api 地址 |
| `chatgpt2api_admin_key` | str | `""` | chatgpt2api 管理密钥 |

### 1.5 黄金模式速查

从 `docs/GOLDEN_EXAMPLES.md` 提取的核心模式：

| 模式 | 出处 | 何时用 |
|------|------|--------|
| `db_session()` 上下文管理器 | `services/db.py:27` | 所有数据库操作 |
| `try_start()` 原子占位 | `services/register_engine.py:59` | 防并发竞态 |
| `_as_int()` / `_as_bool()` 防御解析 | `register_engine.py` | 配置值解析 |
| `_resolve_proxy()` 一致性 | `token_refresher.py` | 所有出站 OpenAI 调用 |
| `tls_verify_enabled()` 兼容解析 | `constants.py:21` | 所有外部 HTTP 请求 |
| Semaphore + gather + 分段 sleep | `register_engine.py` | 并发限流 |

---

## 2. 添加新的邮箱供应商

### 2.1 架构背景

当前有三种邮箱取件方式，按优先级降序：

1. **98faka API**（`email_service.py`）— 主路径，通过 `email_api_base` 配置的 API 拉取邮件列表和正文
2. **Microsoft Graph API**（`graph_email_service.py`）— 降级路径，用 `client_id + refresh_token` 直接调微软 Graph API
3. **IMAP 直连**（`imap_email_service.py`）— 兜底，用 `email + password` 直接登录 Outlook IMAP

注册流程中取 OTP 验证码的统一入口在 `browser_register.py:_wait_for_new_otp()`：
- 优先 98faka API
- 降级 Graph API
- IMAP 暂未接入浏览器注册流程（仅独立使用）

### 2.2 新增供应商的步骤

#### 步骤 1：实现邮件服务类

在 `services/` 下新建文件，实现以下接口：

```python
from __future__ import annotations

class NewEmailService:
    """新邮箱供应商服务"""

    async def wait_for_otp(self, email: str, password: str, client_id: str,
                           refresh_token: str, timeout_sec: int = 600,
                           poll_interval: int = 5, **kwargs) -> str | None:
        """获取 ChatGPT 验证码，返回 6 位码或 None"""
        # 实现轮询逻辑
        # 返回前调用 otp_extractor.extract_otp_code(body) 提取验证码
        ...

    async def get_email_list(self, email: str, password: str, client_id: str,
                              refresh_token: str, top: int = 10) -> list[dict]:
        """获取邮件列表，返回兼容格式:
        [{"id", "subject", "from_address", "received_time", "body_preview"}]
        """
        ...

    async def get_email_body(self, email: str, message_id: str, client_id: str,
                              refresh_token: str) -> str:
        """获取邮件正文 HTML"""
        ...
```

#### 步骤 2：验证码提取统一收口

**不要自己实现验证码提取逻辑。** 统一调用 `services/otp_extractor.py`：

```python
from services.otp_extractor import extract_otp_code

code = extract_otp_code(body_html)  # 返回 6 位码或 None
```

`extract_otp_code` 按优先级：大字号 24px 样式 → 背景高亮 F3F3F3 → 任意 6 位数字，并过滤全同数字（111111）和黑名单数字。

#### 步骤 3：接入浏览器注册流程

修改 `browser_register.py:_wait_for_new_otp()`，在 98faka 降级后添加新供应商：

```python
# 降级：Graph API
code = await graph_email_service.wait_for_otp(...)
if code:
    return code

# 新增：新供应商
code = await new_email_service.wait_for_otp(...)
if code:
    return code
```

#### 步骤 4：协议注册也接入

修改 `protocol_register.py:_fetch_newest_otp()` 或 `_register_sync()`，在 98faka 取码失败后尝试新供应商。

#### 步骤 5：添加配置项

如果新供应商需要 API 地址/密钥，在 `config_schema.py:_CONFIG_SCHEMA` 添加，并在 `config.example.json` 给出默认值。三处对齐：schema 默认、代码兜底、example 文件。

#### 步骤 6：测试

```python
# 测试文件: tests/test_new_email_service.py
@pytest.mark.asyncio
async def test_new_email_service_extract_otp():
    service = NewEmailService()
    code = await service.wait_for_otp(
        "test@example.com", "pass", "client_id", "rt",
        timeout_sec=10, poll_interval=1,
    )
    assert code is None or len(code) == 6
```

### 2.3 现有供应商的形态差异

| 供应商 | 数据源 | 需要什么凭据 | 优点 | 缺点 |
|--------|--------|-------------|------|------|
| 98faka | API | email + password + client_id + refresh_token | 快速，API 调用 | 需第三方服务可用 |
| Graph API | 微软 Graph | client_id + refresh_token | 无需第三方 | 共享收件箱有码重复风险 |
| IMAP | 邮箱直连 | email + password | 完全独立 | 慢，需第三方库 |

### 2.4 常见坑

- **验证码重复**：Graph API 共享收件箱时，多个注册任务可能读到同一封邮件的验证码。用 `skip_code` 参数跳过已用过的码。
- **凭据安全**：`refresh_token` 和密码在日志中脱敏，不要打印到日志。
- **时间过滤**：`min_age_window_sec` 控制只接受最近 N 秒内到达的邮件，避免读到过期验证码导致验证失败。

---

## 3. 添加新的 AI 平台注册

### 3.1 架构背景

当前项目只支持 ChatGPT（OpenAI）注册。双引擎架构（`RegisterEngineProtocol`）设计为可扩展：

```python
# register_base.py:14
@runtime_checkable
class RegisterEngineProtocol(Protocol):
    def register_one(self, email: str, password: str, client_id: str,
                      refresh_token: str) -> Any:
        """注册单个账号，返回标准化结果字典"""
        ...
```

新平台只需实现 `register_one` 方法，返回统一格式的字典即可被调度。

### 3.2 添加新平台的步骤

#### 步骤 1：分析目标平台的注册流程

确定目标平台的注册 API 流程，一般是：
- 邮箱注册 / OAuth 第三方登录
- 验证码 / 密码设置
- 资料填写（姓名、生日等）
- token 获取（access_token / refresh_token / session）

#### 步骤 2：实现新平台注册引擎

创建 `services/grok_register.py`（示例），实现 `RegisterEngineProtocol`：

```python
from __future__ import annotations
from typing import Any
from services.register_base import RegisterEngineProtocol

class GrokRegister:
    """Grok 注册引擎"""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    async def register_one(self, email: str, password: str, client_id: str,
                            refresh_token: str) -> dict[str, Any]:
        """注册单个 Grok 账号"""
        result: dict[str, Any] = {
            "email": email, "status": "failed", "error": "",
            "access_token": "", "refresh_token": "", "id_token": "",
            "openai_password": "",  # 平台特有字段保留，或改名为 platform_password
            "name": "", "birthdate": "", "proxy": "",
            "failure_type": "unknown",
        }
        # 实现注册逻辑
        # 可以用 curl_cffi（协议）或 camoufox（浏览器）
        # 复用 proxy_service 做代理解析
        # 复用 name_service 做姓名生成
        return result
```

#### 步骤 3：扩展 DB schema

如果新平台需要不同字段，在 `db.py:init_db()` 添加迁移列：

```python
# 添加 Grok 平台字段
for col in ("grok_access_token", "grok_refresh_token"):
    try:
        conn.execute(f"ALTER TABLE accounts ADD COLUMN {col} TEXT")
    except Exception:
        pass
```

目前 `accounts` 表已有 `platform TEXT DEFAULT 'chatgpt'` 字段，新增平台注册走 `platform` 区分。`platform_usage` 表按 `(email, platform)` 唯一约束去重。

#### 步骤 4：更新注册调度引擎

修改 `register_engine.py` 或新建平台调度器：

```python
class MultiPlatformEngine:
    """多平台注册调度"""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self._engines: dict[str, Any] = {
            "chatgpt": get_engine(config),
            # "grok": GrokRegister(config),
        }

    async def register_one(self, email: str, password: str, client_id: str,
                            refresh_token: str, platform: str = "chatgpt") -> dict:
        engine = self._engines.get(platform)
        if not engine:
            return {"email": email, "status": "failed", "error": f"不支持的平台: {platform}"}
        return await engine.register_one(email, password, client_id, refresh_token)
```

#### 步骤 5：添加 API 路由

在 `api/register.py` 或新建 `api/grok.py` 添加平台相关 API，在 `api/__init__.py` 挂载路由。

#### 步骤 6：更新平台去重逻辑

`db.py` 中的 `platform_usage` 表已按 `(email, platform)` 唯一约束，新平台注册时调用 `mark_platform_usage(email, "grok", "used")`。

### 3.3 可复用组件清单

| 组件 | 文件 | 是否可复用 |
|------|------|-----------|
| 代理池 `proxy_service.get_next()` | `proxy_service.py` | 是，平台无关 |
| 代理解析 `_resolve_proxy()` | `protocol_register.py` | 是，平台无关 |
| 姓名生成 `name_service.generate()` | `name_service.py` | 是，平台无关 |
| 生日生成 `name_service.generate_birthdate()` | `name_service.py` | 是，平台无关 |
| TLS 指纹 `pick_fingerprint()` | `constants.py` | 是，平台无关 |
| 验证码提取 `extract_otp_code()` | `otp_extractor.py` | 否，邮件格式可能不同 |
| 邮箱服务 `email_service` | `email_service.py` | 否，需适配新平台邮件格式 |
| 浏览器池 `browser_pool` | `browser_pool.py` | 是，平台无关 |
| Sentinel PoW `build_sentinel_token()` | `sentinel.py` | 否，OpenAI 特有 |
| CF Solver `cf_solver_service` | `cf_solver_service.py` | 是，平台无关 |
| 通知 `notifier` | `notifier.py` | 是，平台无关 |
| 数据库 `db_session()` | `db.py` | 是，平台无关 |

### 3.4 常见坑

- **不要复制协议注册的 sentinel 逻辑**：sentinel PoW 是 OpenAI 特有，其他平台不适用。
- **不要假设 OAuth PKCE 流程**：不同平台 OAuth 实现不同，需抓包确认。
- **platform_usage 去重**：同邮箱不同平台可各注册一次，同平台同邮箱只能一次。
- **`openai_password` 字段名**：如果新平台也需要密码字段，建议用泛化字段名（如 `platform_password`）或每平台一个字段。

---

## 4. 添加新的代理类型

### 4.1 架构背景

当前代理池 `proxy_service.py` 支持两种格式：

1. **kookeey 动态住宅代理**：`gate.kookeey.info:1000:UserID-SecurityUser:Pass-Country`，每次连接生成随机 session = 新出口 IP
2. **通用 HTTP 代理**：`http://ip:port` 或 `host:port:user:pass`，按行轮询

`proxy_chain.py` 提供链式代理：客户端 → v2ray(SOCKS5:10808) → kookeey 网关 → 目标（服务器部署无 v2ray 时直连 kookeey）。

### 4.2 添加新代理类型的步骤

#### 步骤 1：确定代理格式

明确定义新代理的行格式和解析规则。例如添加 **SOCKS5 代理**：

```
socks5://user:pass@host:port
socks5://host:port
```

#### 步骤 2：扩展 `proxy_service.py`

在 `_load()` 方法中添加新类型解析，在 `_kookeey_url()` / `_http_url()` 同级添加生成方法：

```python
def _socks5_url(self, line: str) -> str:
    """把 SOCKS5 行转成完整 URL"""
    s = line.strip()
    if s.startswith("socks5://"):
        return s
    # 格式: host:port 或 host:port:user:pass
    parts = s.split(":")
    if len(parts) == 4:
        host, port, user, pw = parts
        return f"socks5://{user}:{pw}@{host}:{port}"
    if len(parts) == 2:
        return f"socks5://{parts[0]}:{parts[1]}"
    return s
```

在 `get_next()` 和 `get_random()` 中添加新类型分支：

```python
if entry["type"] == "kookeey":
    return self._kookeey_url(entry["line"]) or None
elif entry["type"] == "http":
    return self._http_url(entry["line"])
elif entry["type"] == "socks5":
    return self._socks5_url(entry["line"])
```

#### 步骤 3：更新 `proxy_chain.py`（如需要链式代理）

如果新代理类型需要链式组合，在 `proxy_chain.py` 的 `handle_client()` 中添加连接逻辑。

#### 步骤 4：更新调用方

检查所有使用 `proxy_service` 的地方是否兼容新代理 URL 格式：

- `protocol_register.py:_make_session()` — curl_cffi Session 的 `proxies` 参数
- `browser_register.py:register_one()` — camoufox `new_context()` 的 proxy 参数
- `token_refresher.py:_refresh_token()` — httpx 的 proxy 参数
- `api/register.py:_refresh_oauth()` — httpx 的 proxy 参数

每个调用方处理代理 URL 的方式不同，需要逐个适配。

#### 步骤 5：更新黑名单支持

`proxy_service.mark_bad()` 通过 `urlparse` 反查 `host:port` 来匹配 proxies.txt 行。新代理类型需要确保反查逻辑仍然正确。

#### 步骤 6: 测试

```python
def test_socks5_proxy_parsing():
    """测试 SOCKS5 代理行解析"""
    service = ProxyService()
    # 构造测试 entries
    url = service._socks5_url("host:1080:user:pass")
    assert url.startswith("socks5://")
```

### 4.3 代理类型对比

| 类型 | 格式 | 特性 | 适用场景 |
|------|------|------|---------|
| kookeey 动态住宅 | `gw:port:UID-SU:Pass-Country` | 每次会话新 IP，美国住宅 | 反 OpenAI 风控 |
| 通用 HTTP | `http://user:pass@host:port` | 固定 IP 轮询 | 快速测试、低防风控要求 |
| SOCKS5 | `socks5://user:pass@host:port` | 支持 UDP/TCP | 需要 SOCKS5 协议的场景 |
| 链式代理 | v2ray(:10808) → kookeey | 双层代理 | 墙内服务器出墙+住宅IP |

### 4.4 常见坑

- **kookeey 随机 session 不适用于所有场景**：固定代理场景不用 kookeey，用 `proxy_url` 配置。
- **代理黑名单 TTL 30min**：`mark_bad` 标记的代理 30 分钟后自动恢复。可在 `proxy_service.py` 调整 `ttl_sec` 参数。
- **代理 URL 格式不一致**：`proxy_url` config 是完整 URL，`proxies.txt` 行是简写格式，`format_for_display()` 输出 `host:port` 展示格式。三个格式转换时注意一致性。
- **浏览器 context 代理参数**：camoufox 的 `new_context()` 需要 `{"server": ..., "username": ..., "password": ...}` 格式，与 httpx/curl_cffi 不同。

---

## 5. 调试注册失败

### 5.1 失败分类体系

所有注册失败按 `failure_type` 分类，值域：

| 类型 | 含义 | 常见原因 | 可降级浏览器？ |
|------|------|---------|---------------|
| `risk_control` | 风控/限流 | OpenAI 检测到批量注册、IP 黑名单、指纹异常 | 否（浏览器也过不了） |
| `otp_timeout` | 验证码超时 | 邮件 API 不可用、邮箱不可达、验证码被过滤 | 是（可尝试浏览器流程） |
| `network` | 网络异常 | 代理不可用、DNS 解析失败、连接超时 | 是（可尝试浏览器流程） |
| `server_5xx` | 服务器 5xx | OpenAI 服务端错误、临时故障 | 是（服务恢复后浏览器可过） |
| `cf_blocked` | CF 挑战拦截 | Cloudflare 人机验证无法自动通过 | 否（浏览器路径已试过 CF） |
| `unknown` | 未知 | 未分类异常 | 视情况 |

### 5.2 调试步骤

#### 步骤 1：查看失败统计

访问 Web 控制台 → 统计卡片，查看失败类型分布，或直接调 API：

```bash
curl http://localhost:23457/api/register/status
```

返回 `stats.last_task_failure_types` 字段显示各类型计数和诊断建议。

#### 步骤 2：查看日志

```bash
# 查看运行日志
curl http://localhost:23457/api/logs?limit=50

# 或直接读文件
cat data/logs/server.log | tail -100
```

日志中搜索 `[email]` 前缀查看特定账号的注册流程。

#### 步骤 3：按类型定向重试

```bash
# 重试所有失败
curl -X POST http://localhost:23457/api/register/retry-failed \
  -H "Content-Type: application/json" -d '{"failure_type": ""}'

# 只重试风控失败的（换代理后再试）
curl -X POST http://localhost:23457/api/register/retry-failed \
  -H "Content-Type: application/json" -d '{"failure_type": "risk_control"}'

# 只重试网络异常的
curl -X POST http://localhost:23457/api/register/retry-failed \
  -H "Content-Type: application/json" -d '{"failure_type": "network"}'
```

#### 步骤 4：分析具体失败原因

**风控 (`risk_control`)**：
- 检查代理 IP 是否被 OpenAI 封锁（换一批代理再试）
- 检查 `tls_fingerprint` 配置是否合理（一账号一指纹已启用？）
- 检查 `register_interval_sec` 是否太短（建议 ≥10s）
- 检查 `register_concurrency` 是否太高（建议 ≤5）

**验证码超时 (`otp_timeout`)**：
- 检查 `email_api_base` 是否可达（`curl https://app.98faka.top`）
- 检查邮箱是否还有余额（91kami 卡密可能已用完）
- 检查 `otp_wait_timeout_sec` 是否足够（建议 ≥300）
- 检查 `otp_min_age_window_sec` 是否太小（建议 ≥120）

**网络异常 (`network`)**：
- 检查代理是否可达（`curl -x http://proxy:port https://chatgpt.com`）
- 检查 `tls_verify` 是否需要关闭（SSL 拦截代理）
- 检查 `proxy_chain` 是否运行中（服务器部署模式）

**服务器 5xx (`server_5xx`)**：
- 通常是 OpenAI 临时故障，等待后重试即可
- 系统会自动暂停 60s 后恢复（连续 3 次时）

#### 步骤 5：浏览器注册截图调试

如果 `browser_register` 失败，会在 `data/` 生成 `debug_*.png` 截图：

```python
# 在 browser_register.py 中，无法识别页面时自动截图：
await page.screenshot(path=f"debug_unknown_{email.split('@')[0]}.png")
```

截图文件名包含邮箱前缀，查看截图可定位页面形态识别问题。

### 5.3 自适应暂停机制

| 触发条件 | 行为 | 如何恢复 |
|---------|------|---------|
| 连续 3 次 `server_5xx` | 自动暂停 60s | 60s 后自动恢复 |
| 风控占比 >40%（≥5 账号后） | 自动暂停，提示换代理 | 手动点「继续」 |
| 手动暂停 | 暂停 | 手动点「继续」 |
| 手动停止 | 停止 | 重新点「开始」 |

### 5.4 代理黑名单

`risk_control` 失败时，`register_engine.py` 自动调用 `proxy_service.mark_bad(proxy_url, "risk_control", ttl_sec=1800)` 把该代理拉黑 30 分钟，避免后续账号继续使用已风控的出口 IP。

### 5.5 常见问题快速排查

| 症状 | 首查 | 次查 |
|------|------|------|
| "authorize HTTP 429" | 注册间隔太短 | 代理 IP 被限流 |
| "验证码等待超时" | 邮箱 API 状态 | 邮箱余额 |
| "CF 挑战未通过" | CF Solver 是否运行 | 代理 IP 是否被 CF 标记 |
| "OAuth code 换 token 失败" | PKCE verifier 是否匹配 | 代理 IP 是否地理稳定 |
| "注册成功但未获取到 token" | session API 是否被 CF 拦截 | 浏览器 cookie 是否写入 |
| token 越刷越少 | 新 RT 是否落库 | 检查 `_update_tokens` 调用 |

---

## 6. 处理 API 变更

### 6.1 上游 API 变更的常见类型

| 类型 | 影响范围 | 紧急程度 |
|------|---------|---------|
| OpenAI 登录页 UI 改版 | `browser_selectors.py`、`login_detector.py` | 高（注册立即中断） |
| OpenAI OAuth 端点变更 | `constants.py` OAuth 常量 | 高（协议注册立即中断） |
| 98faka API 格式变更 | `email_service.py` | 中（取码失败） |
| kookeey 代理格式变更 | `proxy_service.py` | 中（代理连接失败） |
| 91kami 卡密格式变更 | `email_service.py:fetch_emails_from_source` | 中（邮箱导入失败） |
| curl_cffi API 变更 | `protocol_register.py` | 低（升级依赖时） |
| camoufox API 变更 | `browser_register.py` | 低（升级依赖时） |

### 6.2 检测 API 变更的方法

#### 被动检测（运营中）

- 注册成功率突然下降 → 检查 `failure_type` 分布
- 所有账号都 `otp_timeout` → 98faka 或邮箱 API 可能变更
- 所有账号 `cf_blocked` → CF 挑战算法更新
- 前端页面空白 → API 路由或 CORS 配置问题

#### 主动检测

- 定期抓包比对 OpenAI 登录页 HTML 结构（`browser_selectors.py` 的选择器是否仍匹配）
- 跟踪 `curl_cffi` 和 `camoufox` 的版本发布日志
- 使用 `tests/` 中的 mock 测试验证各模块行为

### 6.3 处理 UI 改版（最常见）

#### 步骤 1: 识别变化

从浏览器注册失败截图（`debug_*.png`）或日志中 `page_kind` 判断：

```
页面形态: unknown (动作: fail)
页面文字: "Sign in to your account" "Continue with Google" ...
```

#### 步骤 2: 更新选择器

修改 `services/browser_selectors.py`，添加新选择器或调整优先级：

```python
# 原选择器
EMAIL_PRIMARY = 'input[name="email"]'

# 如果新 UI 改用 id 属性，添加备选：
EMAIL_PRIMARY = 'input[name="email"], input[id="email-input"]'
```

#### 步骤 3: 更新页面形态检测

修改 `services/login_detector.py`，如果新 UI 使用了不同的 input name 或 URL 路径：

```python
def detect_login_page(url, html, input_names):
    # 添加新 URL 特征
    if "signin" in url:
        return "email"
    # 原有逻辑不变
    ...
```

#### 步骤 4: 更新验证码提取

如果 OpenAI 改变了验证码邮件模板，修改 `services/otp_extractor.py`：

```python
# 新增样式特征
_FONT_32PX = re.compile(r"font-size:\s*32px[^>]*>.*?(\d{6})", re.DOTALL)

def extract_otp_code(body_html):
    # 新样式优先
    m = _FONT_32PX.search(body_html)
    if m and _is_valid_code(m.group(1)):
        return m.group(1)
    # 原有逻辑
    ...
```

### 6.4 处理 OAuth 端点变更

#### 步骤 1: 更新常量

修改 `services/constants.py`：

```python
OAUTH_CLIENT_ID = "新_client_id"
OAUTH_REDIRECT_URI = "新_redirect_uri"
OAUTH_AUDIENCE = "新_audience"
OAUTH_AUTH0_CLIENT = "新_auth0Client"
```

#### 步骤 2: 验证 PKCE 流程

修改后运行协议注册测试，确认 `authorize → code → token` 流程完整。

### 6.5 优雅降级策略

当 API 变更导致主路径中断时，系统已有降级机制：

| 主路径 | 降级路径 | 配置开关 |
|--------|---------|---------|
| 协议注册 (curl_cffi) | 浏览器注册 (camoufox) | `protocol_first=true` |
| 98faka API 取码 | Graph API 取码 | 自动降级 |
| 自动 CF 挑战解 | CF Solver 兜底 | `cf_solver_service` |
| 代理池轮询 | 固定代理 `proxy_url` | `use_proxy=true` |

### 6.6 测试新 API 变更

每次 API 变更后必须验证的测试项：

```bash
# 1. 单元测试
./.venv/Scripts/python.exe -m pytest tests/ -p no:warnings

# 2. 浏览器选择器冒烟（如果改了 browser_selectors.py）
./.venv/Scripts/python.exe -m pytest tests/test_browser_selectors.py -v

# 3. 验证码提取测试（如果改了 otp_extractor.py）
./.venv/Scripts/python.exe -m pytest tests/test_otp_extractor.py -v

# 4. 真实浏览器冒烟测试（可选，需真实环境）
./.venv/Scripts/python.exe scripts/smoke_browser_pool.py
```

### 6.7 记录变更

每次处理 API 变更后：
1. 更新 `docs/CHANGE_REPORT_v*.html`（如果存在）
2. 在 `docs/ADR/` 中添加架构决策记录（如果变更涉及架构层面的选择）
3. 更新 `workflow_status.md` 的验证历史

---

## 附录

### A. 文件索引速查

| 文件 | 职责 | 常见维护操作 |
|------|------|-------------|
| `services/register_engine.py` | 注册调度引擎 | 添加新注册路径、调整并发 |
| `services/protocol_register.py` | 协议注册（curl_cffi） | 调整 OAuth 流程、改指纹策略 |
| `services/browser_register.py` | 浏览器注册（camoufox） | 调整浏览器参数、改 CF 处理 |
| `services/browser_selectors.py` | 浏览器选择器 | 上游 UI 改版时更新 |
| `services/login_detector.py` | 登录页形态检测 | 上游 UI 改版时更新 |
| `services/email_service.py` | 98faka 邮箱 API | 邮箱 API 格式变更 |
| `services/graph_email_service.py` | Graph API 邮箱 | Graph API 版本更新 |
| `services/imap_email_service.py` | IMAP 直连邮箱 | IMAP 服务器变更 |
| `services/otp_extractor.py` | 验证码提取 | 验证码模板变更 |
| `services/proxy_service.py` | 代理池 | 添加新代理类型 |
| `services/proxy_chain.py` | 链式代理 | 调整代理链路 |
| `services/token_refresher.py` | token 巡检 | 调整刷新策略 |
| `services/sentinel.py` | Sentinel PoW 生成 | PoW 算法更新 |
| `services/constants.py` | OAuth 常量 | OAuth 端点/凭据变更 |
| `services/browser_pool.py` | 浏览器池 | 调整池化策略 |
| `services/cf_solver_service.py` | CF 挑战解决 | CF 算法更新 |
| `services/notifier.py` | 告警推送 | 添加新通知渠道 |
| `services/name_service.py` | 姓名生成 | 调整命名策略 |
| `services/db.py` | 数据库层 | 加表/加字段/加索引 |
| `services/config_schema.py` | 配置校验 | 加配置项 |
| `api/__init__.py` | FastAPI 入口 | 加路由/改鉴权 |
| `api/register.py` | 注册 API | 加注册端点 |
| `main.py` | 服务入口 | 改启动配置 |

### B. 测试命令速查

```bash
# 全量测试
./.venv/Scripts/python.exe -m pytest tests/ -p no:warnings -v

# 带覆盖率
./.venv/Scripts/python.exe -m coverage run -m pytest tests/ -p no:warnings
./.venv/Scripts/python.exe -m coverage report

# 单文件测试
./.venv/Scripts/python.exe -m pytest tests/test_protocol_register.py -v

# 冒烟测试
./.venv/Scripts/python.exe scripts/smoke_browser_pool.py
./.venv/Scripts/python.exe scripts/smoke_token_refresh.py

# 真实浏览器测试（需手动确认）
./.venv/Scripts/python.exe scripts/verify_all_accounts.py
```

### C. 相关文档索引

| 文档 | 内容 |
|------|------|
| `README.md` | 项目概述、功能列表、部署说明 |
| `docs/ONBOARDING.md` | 新人入门（目录地图、常见坑） |
| `docs/GOLDEN_EXAMPLES.md` | 黄金代码范例（生产验证的最佳实践） |
| `docs/ADR/ADR-001~006` | 架构决策记录 |
| `docs/VALIDATION_RECORDS.md` | 验证记录登记册 |
| `workflow_status.md` | 验证历史 + 复现坑 |
| `config.example.json` | 配置示例与默认值 |
| `计划书/下一步改进指南-v3.3.md` | v3.3 改进计划（T36-T74） |
| `计划书/下一步改进指南-v3.4.md` | v3.4 改进计划（T75-T103） |