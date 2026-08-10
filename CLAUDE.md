# GPT 自动注册项目 — CLAUDE.md

> Python 3.11 / FastAPI / SQLite / Docker
> 最后更新: 2026-08-10

---

## 项目概览

批量注册 OpenAI 账号的管理系统，带 Web 控制台。协议优先（curl_cffi + sentinel）→ 浏览器兜底（camoufox）双引擎注册。

**线上实例**: 腾讯云东京 `43.165.173.36:23457`（Docker 容器，与 chatgpt2api 同机部署）

---

## 目录结构

```
├── main.py                   # 入口：uvicorn 启动 + 配置校验 + 日志清理
├── config.json               # 运行时配置（不提交 git）
├── config.example.json       # 配置模板
├── requirements.txt          # 唯一依赖来源
├── Dockerfile / docker-compose.yml
├── sensitive_policy.json     # 敏感数据脱敏策略
│
├── api/                      # FastAPI 路由层
│   ├── __init__.py           # create_app() — 路由挂载 + CORS + 鉴权中间件
│   ├── register.py           # 注册/清空/导出/chatgpt2api对接
│   ├── emails.py             # 邮箱池 CRUD
│   ├── proxies.py            # 代理池 CRUD + 健康探测
│   ├── settings.py           # 系统设置 CURD
│   ├── stats.py              # 统计/健康检查
│   └── logs.py               # 日志拉取
│
├── services/                 # 业务逻辑层
│   ├── db.py                 # SQLite 数据层（所有表操作）
│   ├── register_engine.py    # 注册调度引擎（协议优先→浏览器兜底）
│   ├── protocol_register.py  # 协议注册（curl_cffi + sentinel PoW）
│   ├── browser_register.py   # 浏览器注册（camoufox + login_detector）
│   ├── email_service.py      # 98faka API 邮箱
│   ├── graph_email_service.py# Graph API 邮箱
│   ├── imap_email_service.py # IMAP 直连 Outlook
│   ├── otp_extractor.py      # 验证码提取
│   ├── proxy_service.py      # 代理池（kookeey + 通用 HTTP）
│   ├── proxy_chain.py        # 链式代理
│   ├── token_refresher.py    # token 保鲜巡检
│   ├── cf_solver_service.py  # CF 验证解决
│   ├── sentinel.py           # PoW 生成
│   ├── name_service.py       # 姓名生成
│   ├── notifier.py           # 告警推送
│   ├── login_detector.py     # 登录页面形态检测
│   ├── browser_selectors.py  # 浏览器选择器常量
│   ├── browser_pool.py       # 浏览器实例池
│   ├── config_schema.py      # 配置类型校验
│   └── constants.py          # OAuth 常量
│
├── cf_solver/                # 独立 CF Solver 服务（端口 8001）
├── web_dist/                 # 前端静态文件
├── data/                     # 运行时数据（SQLite + 日志 + 备份，不提交）
├── tests/                    # pytest 测试（~40 文件）
└── docs/                     # 文档
```

---

## 开发命令

```bash
# 安装依赖
pip install -r requirements.txt

# 运行测试
pytest -v --tb=short
pytest -v --tb=short -x                          # 失败即停
pytest -v --cov=services --cov=api --cov-report=term-missing

# 运行服务
python main.py                                   # 开发模式 localhost:23457
GPT_REGISTER_PORT=23457 python main.py

# 类型检查
pip install mypy
mypy services/ api/ --ignore-missing-imports

# Docker
docker compose up -d --build
docker compose logs -f
```

---

## 架构规则

### 层次依赖

```
api/ (路由) → services/ (业务) → db.py (数据层)
```

- `api/` 只做 HTTP 参数解析/响应序列化，不包含业务逻辑
- `services/` 不直接 import `api/` 模块
- `db.py` 是所有数据访问的唯一入口，`api/` 和 `services/` 都不直接操作 SQLite
- `services/` 内部模块可通过 `db.py` 互相引用，但避免循环导入

### 注册引擎

```
register_engine.py
  ├─ protocol_first=True → protocol_register.py (curl_cffi + sentinel PoW)
  └─ 失败或协议不可用 → browser_register.py (camoufox 浏览器 + login_detector)
```

- 注册引擎通过 `get_engine(config)` 工厂函数创建
- 注册状态机：`pending → running → success / failed`
- 并发控制：`register_concurrency` 配置 + `register_interval_sec` 间隔

### 数据库

- SQLite 单文件 `data/register.db`
- 连接管理：`get_conn()` 返回新连接，`db_session()` 上下文管理器
- 所有写操作通过 `init_db()` 初始化后的表结构
- 日志使用批量异步刷写（`_log_flusher` 线程 + `_write_log_batch`）

---

## 编码规范

### Python

- Python 3.11+，`from __future__ import annotations` 启用 PEP 604
- 类型注解：所有函数签名必须标注类型
- `None` 返回的函数必须标注 `-> None`
- 异常处理：不静默吞掉异常，日志记录上下文
- 配置：通过 `config.json` 读取，`os.environ` 覆盖（`GPT_REGISTER_PORT` 等）

### 命名

- 函数/变量：`snake_case`
- 常量：`UPPER_SNAKE_CASE`
- 类：`PascalCase`
- 私有函数：`_prefix` 前缀
- 文件：`snake_case.py`

### 安全

- 敏感字段（token、secret、password）在日志/API 响应中必须脱敏
- 脱敏策略见 `sensitive_policy.json`（`api/settings.py` 的 `_mask_sensitive`）
- 密码/refresh_token 等仅存储在数据库，不落入日志
- 第三方 HTTP 库日志级别压到 `WARNING`（防凭据泄露）

### 测试

- pytest + pytest-asyncio
- `tests/conftest.py` 提供测试夹具
- `tests/fixtures/` 目录存放测试数据
- 测试文件命名：`test_*.py`
- 测试覆盖率目标：>=80%

---

## Git 工作流

```bash
# 提交格式
<type>: <中文描述>

# 类型: feat / fix / refactor / docs / test / chore / perf / ci
```

- 敏感文件（`config.json`、`proxies.txt`、`data/`）在 `.gitignore` 中排除
- 不改动已提交的 `proxies.txt`（历史凭据问题已存在，只做轮换提醒）

---

## 部署

### Docker（线上）

```bash
cp config.example.json config.json  # 设置强 auth_key
docker compose up -d --build
```

### 环境变量

| 变量 | 说明 |
|------|------|
| `GPT_REGISTER_PORT` | 覆盖 `config.json` 的 `port` |
| `LLM_API_BASE` | 模型路由 API base |
| `ANTHROPIC_AUTH_TOKEN` | Claude API 认证 |

### 配置项（config.json）

| 键 | 类型 | 说明 |
|-----|------|------|
| `auth_key` | string | 管理密钥（必须修改） |
| `auth_enforced` | bool | 占位符密钥是否拒绝启动 |
| `port` | int | 服务端口 |
| `register_concurrency` | int | 并发注册数 |
| `register_interval_sec` | int | 注册间隔秒数 |
| `protocol_first` | bool | 是否协议优先 |
| `use_proxy` | bool | 是否使用代理 |
| `token_refresh_enabled` | bool | 是否启用 token 巡检 |

---

## 关键模式

### 敏感数据脱敏

所有 API 响应经过 `_mask_sensitive()` 处理，脱敏规则见 `sensitive_policy.json`。

### 原子写

数据库写操作（清空、备份）使用事务 + 文件锁保证原子性，见 `api/register.py:_backup_before_clear()`。

### 一账号一 IP

kookeey 动态住宅代理 + 每账号独立代理分配，防风控。见 `proxy_service.py`。

### 一账号一指纹

TLS 指纹池轮换（curl_cffi） + UA 配套，防风控检测。见 `protocol_register.py`。

---

## 参考文档

- `SKILLS.md` — 可复用技能文档（理解架构、添加供应商/平台/代理类型、调试）
- `docs/` — 审计报告、SOP、改进指南
- `sensitive_policy.json` — 脱敏策略定义

---

## 检查清单

提交前：
- [ ] 测试通过（`pytest -v --tb=short`）
- [ ] 无硬编码凭据
- [ ] 响应数据经过脱敏
- [ ] 日志不泄露敏感字段
- [ ] 配置变更同步到 `config.example.json`