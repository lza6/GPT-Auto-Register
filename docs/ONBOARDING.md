# 新人保姆指南（ONBOARDING）

> 0 基础接手 GPT 自动注册项目。读完这篇你能：跑起来、懂架构、加功能、不踩坑。
> 项目：FastAPI + SQLite(WAL) + 协议注册优先(curl_cffi+sentinel)/浏览器(camoufox)兜底 + Web 单页控制台。已在生产使用。

## 一、30 秒跑起来

```bash
# Windows：双击 启动.bat（自动建 venv、装依赖、拉起 CF Solver + 主服务）
# 打开控制台
http://localhost:23457     # 主服务 Web 控制台
http://localhost:8001      # CF Solver（可选，解 Cloudflare）
```

停止：双击 `停止.bat`（按 config.json 的端口精准清理）。

## 二、它是干什么的

批量注册 OpenAI（ChatGPT）账号：从邮箱池取邮箱 → 协议/浏览器注册 → 拿到 access_token + refresh_token 三件套 → 导出或推送到 chatgpt2api。

```
邮箱池(emails) → 注册引擎(register_engine) → 协议优先(protocol_register) ┐
                                          ↘ 浏览器兜底(browser_register) ┘→ accounts → 导出/推送 chatgpt2api
                                              ↑ CF 挑战时调 CF Solver(:8001)
                                              ↑ 代理池(proxy_service) 每账号独立 IP
```

## 三、目录地图（改代码前先看这）

| 目录/文件 | 职责 | 什么时候碰它 |
|---|---|---|
| `api/` | FastAPI 路由（register/emails/proxies/settings/stats/logs） | 加/改接口 |
| `services/` | 业务逻辑（注册引擎、取码、代理、token 巡检、浏览器池…） | 加/改业务 |
| `services/db.py` | 唯一数据访问层（`db_session()`） | 加/改表、查询 |
| `web_dist/` | 前端三文件（index.html / app.js / app.css） | 改 UI |
| `cf_solver/` | CF 挑战求解子服务（独立进程 :8001） | 一般不动 |
| `services/config_schema.py` | config.json 类型校验 | 加配置项 |
| `services/constants.py` | OpenAI OAuth 常量 + tls_verify_enabled | 改 OAuth 常量 |
| `启动.bat`/`停止.bat` | 一键启停 | 改部署 |

## 四、改代码前必读（避免重复踩坑）

1. **`docs/VALIDATION_RECORDS.md`** — 验证记录登记册。先看要改的地方是否已验证：已验证且没改动 → 直接用结论，别重复验证。
2. **`.specify/memory/constitution.md`** — 项目宪法（真实落地/生产可用/增量迭代/先思考后编码/证据驱动）。
3. **`workflow_status.md`** — 验证历史 + 复现坑。
4. **记忆文件**（若用 Claude）：`gpt-auto-register-v2-state.md` 的关键已修复点与复现坑。
5. **判断记录是否过时**：行号/函数可能已变，`git diff` + Read 当前真实代码校准，不信考古。

## 五、加新功能/API 的规范（强制）

读 `.claude/skills/add-feature/SKILL.md`（或 `docs/skills/add-feature.md`）。要点速查：

- **新 API**：路由挂 `api/__init__.py`；参数 pydantic 校验；返回统一信封 `{success, ...}`；TDD 三态（成功/失败/鉴权 401）。
- **新配置项**：`config_schema._CONFIG_SCHEMA` + `config.example.json` + （前端可写则）`settings.ALLOWED_SETTINGS_KEYS`；**默认值三处对齐**（schema/example/代码兜底/前端占位）。
- **外部 HTTP**：必须走代理（`_resolve_proxy` 同款）+ `verify=tls_verify_enabled(config)` + 失败记日志；**凭据不落日志**。
- **前端**：按钮有真 handler、空/加载/错/成功态、防重复、escapeHtml；三验证（node --check + id 比对 + playwright 点一遍）。
- **改 .bat**：纯 ASCII（字节检查非 ASCII=0）；读 config 用 python 写临时文件 + `set /p`（别用 for/f，python 括号会破它）；验证用 `os.startfile` 双击。

## 六、测试与验证

```bash
./.venv/Scripts/python.exe -m pytest tests/ -p no:warnings   # 全量（275+ 全绿）
./.venv/Scripts/python.exe -m coverage run -m pytest tests/  # 覆盖率
./.venv/Scripts/python.exe scripts/smoke_browser_pool.py     # 真实浏览器池冒烟
./.venv/Scripts/python.exe scripts/smoke_token_refresh.py    # 真实 token 刷新冒烟（需真实账号）
```

- 每个新 API 覆盖 成功/失败/鉴权 三态；每个新配置覆盖 默认/字符串'true'/布尔 三态解析。
- 外部受限（真实 OpenAI 账号、付费代理）写清边界 + 降级，不假装测过。

## 七、生产部署红线

- 设真实 `auth_key` + `auth_enforced=true`（否则接口裸奔）。可用环境变量 `GPT_REGISTER_AUTH_KEY`/`GPT_REGISTER_AUTH_ENFORCED` 注入。
- `config.json`、`proxies.txt`、`data/` 含密钥/凭据，**勿提交 git**（已 gitignore）。
- 清空库需 `confirm='clear'`，清空前自动备份到 `data/backups/`（留 7 份）。

## 八、常见坑（血泪记录，先看再改）

| 坑 | 真相 | 对策 |
|---|---|---|
| 协议注册"看似正常"但从不成功 | PKCE verifier/challenge 必须同一对，否则换 token 必失败（被浏览器兜底掩盖） | `_gen_pkce()` 一次生成，challenge 进 authorize、verifier 换 token |
| token 越刷越少 | OpenAI 轮换 RT，旧的重用 2-3 次就 401 refresh_token_reused | 任何刷新必须落库新 RT（access+refresh 都存） |
| 开鉴权后 Web 全 401 | 前端必须带 X-Auth-Key | 已内置 401 弹窗收集 + localStorage |
| 注册间隔不起作用 | sleep 必须在 sem 内，否则不限速 | register_engine 已修 |
| bat 双击闪退/读不到端口 | 中文/特殊符号 + for/f 遇 python 括号 | 纯 ASCII + set/p 读临时文件 |
| root logger 设 DEBUG | httpx 会把含凭据的请求头/体写进日志 | 只 gpt-register=DEBUG，压 httpx 到 WARNING |

## 九、架构资产（深入时读）

- `docs/ADR/ADR-001~006` — 关键架构决策（为什么协议优先、为什么并发、安全基线、bat 防御、终局审计决策）
- `docs/CHANGE_REPORT_v*.html` — 各版本变更报告（含验收测验）
- `docs/adr/../ADR` + `workflow_status.md` — 决策与验证的单一事实源
