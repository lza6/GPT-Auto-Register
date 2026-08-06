# add-feature（仓库副本）

> 本地自动加载技能位于 `.claude/skills/add-feature/SKILL.md`。本文件为仓库副本，供离线/评审参考。内容同步。

---


---

# 新增功能/API 规范工作流（GPT 自动注册项目）

> 本项目的功能新增方法论。目标：让新能力"一次接入就前后端贯通、可被调用方直接调用、有测试有文档"，且不破坏既有逻辑。

## 0. 动手前必读（顺序强制）

1. **`docs/VALIDATION_RECORDS.md`** — 验证记录登记册。**先看要改的区域是否已验证**：已验证且未改动 → 复用结论不重复验证；改动过 → 标 `⚠️ 需复验`。
2. **`.specify/memory/constitution.md`** — 最高准则（真实落地/生产可用/增量迭代/先思考后编码/证据驱动）。
3. **`workflow_status.md`** — 验证历史 + 复现坑。
4. **记忆文件** `gpt-auto-register-v2-state.md` — 关键已修复点 + 复现坑（如 PKCE 配对、RT 轮换、bat for/f 坑、root logger、TLS verify）。
5. **判断过时**：若记录引用的文件/行号/函数已变，先 `git diff` + Read 当前真实代码校准，**不信考古结论**。

## 1. 分层归属（不跨层）

- 路由/参数/校验 → `api/<domain>.py`
- 业务逻辑 → `services/<domain>_service.py`
- 数据访问 → 只走 `services/db.py` 的 `db_session()`，**禁止在 api/services 手写 connect/close**
- 前端 → `web_dist/app.js`（逻辑）+ `index.html`（结构）+ `app.css`（样式）

## 2. 新增 API 端点清单

- [ ] 路由挂到 `api/__init__.py:create_app`（`app.include_router(..., prefix="/api/<x>")`）
- [ ] **鉴权**：`/api/*` 自动过 `AuthKeyMiddleware`；敏感操作（清空/导出/推送）需额外 `confirm` 或校验
- [ ] **参数校验**：pydantic 模型；分页 `limit = min(limit,500) if limit>0 else 100`、`offset=max(0,offset)`
- [ ] **返回结构**：统一信封 `{success, data/accounts/..., total?}`；错误 `HTTPException(4xx, "中文提示")`
- [ ] **错误处理**：显式，禁裸 `except:`（至少 `add_log` 记录）
- [ ] **TDD 三态测试**：成功 / 失败 / 鉴权（401）三态；用 `client` fixture（conftest 已隔离 DB + config）

## 3. 新增配置项清单

- [ ] `services/config_schema.py` 的 `_CONFIG_SCHEMA` 加一行（类型/默认/可空）
- [ ] `config.example.json` 加示例键
- [ ] 若要前端可写：`api/settings.py` 的 `ALLOWED_SETTINGS_KEYS` 加白名单（`_coerce_value` 会自动按 schema 转型存 config.json）
- [ ] 读取处用 `_as_int`/`_as_bool` 防御（settings 可能存字符串）
- [ ] **默认值三处对齐**：config_schema / config.example / 代码兜底 / 前端占位，**必须一致**（历史坑：otp 默认值 120/600 三方分裂）
- [ ] README 配置表加一行

## 4. 外部 HTTP 调用（OpenAI/邮件/第三方）

- [ ] **必须走代理**：用 `token_refresher._resolve_proxy` 同款逻辑（优先 config.proxy_url，其次代理池）
- [ ] **TLS 校验**：`verify=tls_verify_enabled(config)`（默认 true；勿写死 verify=False，SSL 拦截代理由用户设 false）
- [ ] 带 timeout；失败 `add_log` 记录原因（不静默吞）
- [ ] **凭据不落日志**：refresh_token/密码/验证码/取货 URL 严禁明文写日志

## 5. 前端功能清单

- [ ] 按钮 onclick 有真实 handler 且后端有对应端点（禁假功能）
- [ ] 空态/加载态/错误态/成功反馈/防重复点击/危险操作确认
- [ ] innerHTML 拼接处 `escapeHtml`；value 属性注入用数字强转
- [ ] 轮询降载（页面隐藏暂停）；列表分页越界自愈
- [ ] **三验证**：`node --check app.js` + getElementById 的 id 双向比对 + playwright 无头浏览器点一遍（0 console error）

## 6. 改 .bat 红线（ADR-004）

- [ ] **纯 ASCII**（禁中文/特殊符号如 em-dash），改后字节级检查非 ASCII=0
- [ ] 读 config 别用 `for /f ('python -c ...')`（python 里的 `)` 会提前闭合 for 命令串）→ 用 python 直写临时文件 + `set /p VAR=<file`
- [ ] 验证用 `os.startfile(bat,'open')` 真实双击，**不要用** `cmd /c`（中文路径坑）

## 7. 收尾（缺一不可）

- [ ] `pytest tests/ -p no:warnings` 全绿
- [ ] 真实冒烟（uvicorn 起实例 curl 端点；涉外部服务写清降级边界）
- [ ] README / 相关 md 同步
- [ ] `workflow_status.md` 增量记录（验证证据 + 复现坑）
- [ ] 记忆文件更新（若踩了新坑 / 推翻了旧认知）

## 反模式（禁止）

- 伪实现 / mock 假闭环 / 占位当完成
- 只改代码不改文档/配置/测试
- 引入高并发重基础设施（Kafka/Redis/负载均衡）——本项目是单机工具，YAGNI
- 为"更优雅"重写工作正常的模块
