# 工作流状态总览 (workflow_status.md)

> 统一记录项目所有已完成任务、验证结果、剩余待办、已知限制和下次迭代建议。
> **下次接手先读此文件**，避免重复审计和盲目重跑已验证的检查。

**当前版本**: v3.5.0 (已发布)
**测试基线**: 422 项全绿
**最后更新**: 2026-08-11

---

## 一、版本发布历史

| 版本 | 标签 | 提交 | 日期 | 核心内容 |
|------|------|------|------|---------|
| v2.0.4 | v2.0.4 | ed769f0 | 2026-08-05 | 修复双击启动.bat 闪退（中文编码+GNU timeout 劫持） |
| v2.1 | v2.1 | 8ddd191 | 2026-08-06 | 终局闭环审计—注册链路加固+OTP三合一+常量收敛 |
| v2.2 | v2.2 | 1cf480e | 2026-08-06 | 生产级增强—D3健康检查+L4聚合SQL+M4日志容量+L5优雅停机+B5常量收敛 |
| v2.2.1 | v2.2.1 | 77a603c | 2026-08-06 | 索引优化+README同步+盲点补漏 |
| v3.1 | v3.1 | 391bdc3 | 2026-08-06 | 前端闭环+settings类型清源+浏览器池+token巡检真实冒烟 |
| v3.1.1 | v3.1.1 | d5247ed | 2026-08-06 | 多代理7维审计+对抗验证+批判复审循环+PKCE/RT修复 |
| v3.3.0 | v3.3.0 | ac3a9af | 2026-08-08 | 一账号一指纹(TLS指纹池)+多平台邮箱库+邮箱池平台切换UI |
| v3.4 | 未发布 | 工作区脏 | 2026-08-09 | 日志异步批量+SSE推送+告警通知+代理黑名单+CF solver集成+失败重试+资产健康仪表盘 |
| **v3.5.0** | **v3.5.0** | **当前** | **2026-08-11** | **全量优化闭环：脱敏层+错误分类+进度持久化+状态机+BrowserRegister拆分+类型化模型+配置冻结+422测试全绿** |

---

## 二、所有已完成任务清单 (T78-T99)

### v3.3.0 已发布任务 (已完成且已验证)

| 编号 | 任务 | 状态 | 证据 |
|------|------|------|------|
| T3.3-1 | 一账号一指纹（TLS指纹池轮换 + UA配套）反风控 | ✅ | `services/constants.py` pick_fingerprint/ua_for_fingerprint, `protocol_register.py` 每账号 _fresh_fingerprint |
| T3.3-2 | 多平台邮箱库（platform字段+platform_usage表+去重审计） | ✅ | `services/db.py` 新增 platform_usage 表, insert_email 带 platform, mark_platform_usage |
| T3.3-3 | 邮箱池平台切换UI + 平台过滤查询 | ✅ | `web_dist/app.js` 平台下拉 + 过滤, `api/emails.py` list_platforms/platform-map |
| T3.3-4 | 版本号统一 3.3.0 | ✅ | `api/__init__.py` x2 |
| T3.3-5 | Docker化服务器部署（单容器主服务23457+CF Solver 8001） | ✅ | Dockerfile + docker-compose.yml, camoufox 预取+非root可选 |
| T3.3-6 | 注册一账号一IP机制（每账号独立代理） | ✅ | `protocol_register.py` 每账号独立 proxy |
| T3.3-7 | 288项测试全绿 | ✅ | `pytest tests/` 全量通过 |

### v3.4 开发中任务 (未提交，工作区脏)

| 编号 | 任务 | 文件 | 状态 | 功能描述 |
|------|------|------|------|---------|
| **T78** | 失败重试API | `api/register.py` services/db.py | ✅ 已实现 | POST /api/register/retry-failed，将failed/cf_blocked邮箱回置pending，支持failure_type定向重试 |
| **T80** | 注册专用线程池 | `services/register_engine.py` services/protocol_register.py | ✅ 已实现 | ThreadPoolExecutor大小=concurrency，替代默认池min(32,cpu+4)钳制；协议链注入专用池 |
| **T80b** | 单账号硬超时护栏 | `services/register_engine.py` | ✅ 已实现 | asyncio.wait_for 硬超时otp_timeout+120s，释放槽位+落库otp_timeout |
| **T81** | 自适应暂停代数安全 | `services/register_engine.py` | ✅ 已实现 | batch_generation代数+_auto_resume_handle实例属性，防stop后误恢复/跨批次串扰/GC |
| **T82** | 日志异步批量落库 | `services/db.py` | ✅ 已实现 | Queue+flusher线程200ms/200条executemany，写放大从N次commit→1次；队列满丢计数不阻塞业务 |
| **T83** | push_chatgpt2api非阻塞 | `api/register.py` | ✅ 已实现 | httpx同步改asyncio.to_thread，防chatgpt2api抖动卡死事件循环；超时120→30s |
| **T84** | token巡检防重入+自愈 | `services/token_refresher.py` | ✅ 已实现 | _scanning哨兵防重入，_on_task_done崩溃退避重启(5s→300s封顶)，局部统计消除共享dict竞态，实时耗时扣除interval |
| **T85** | webhook告警通道 | `services/notifier.py` | ✅ 已实现 | 支持钉钉/企业微信/Bark/Server酱，batch_done/high_fail_rate/proxy_pool_empty/cf_solver_dead事件，5分钟去抖 |
| **T86** | 浏览器指纹一致性 | `services/browser_register.py` | ✅ 已实现 | camoufox os/locale/timezone/US Windows伪装，Screen分辨率约束，block_webrtc，kookeey 40+国家时区映射 |
| **T87** | CF solver Turnstile集成 | `services/browser_register.py` | ✅ 已实现 | _resolve_cf复选框解不开时调cf_solver_service.get_turnstile_token，注入token到cf-turnstile-response |
| **T88** | 代理黑名单 | `services/proxy_service.py` | ✅ 已实现 | risk_control失败自动mark_bad(proxy,ttl=1800s)，get_next/get_random跳过黑名单；黑名单持久化到data/proxy_blacklist.json |
| **T88b** | 真实HTTP出口探测 | `api/proxies.py` | ✅ 已实现 | 原TCP探测升级为TCP+HTTP出口双重验证，经代理请求ipify拿出口IP+ipapi.co查国家+延迟ms |
| **T89** | 前端UX改进 | `web_dist/app.js` | ✅ 已实现 | 三态toast(info/success/error)，withBusy防连点封装，alert→toastMsg全量替换 |
| **T94** | 资产健康仪表盘 | `web_dist/app.js,index.html` api/stats.py | ✅ 已实现 | c2api就绪率卡片(AT/RT/密码/取件凭证四维)，转化漏斗条(邮箱→已消耗→成功→有token)，速率/ETA本地计算 |
| **T95** | SSE日志实时推送 | `api/logs.py` web_dist/app.js | ✅ 已实现 | GET /api/logs/stream SSE替代2s轮询，级别筛选+搜索+暂停滚动+导出，失败3次回退轮询兜底 |
| **T96** | CF solver优雅停机 | `services/cf_solver_service.py` | ✅ 已实现 | 先HTTP POST /shutdown再terminate，超时psutil树杀(含camoufox子进程)，按进程名兜底清理残留 |
| T97 | 姓名拟人化 | `services/name_service.py` | ✅ 已实现 | 首字母大写，有界缓存(MAX_USED=10000)，数字后缀低概率，防内存缓涨 |
| T98 | 失败类型下钻 | `services/db.py` api/register.py | ✅ 已实现 | accounts表failure_type列，list_accounts支持failure_type过滤，count_accounts同步 |
| T99 | 日志前端增强 | `web_dist/index.html` web_dist/app.js | ✅ 已实现 | 级别筛选下拉、搜索关键词、暂停滚动按钮、导出按钮 |

---

## 四、v3.5.0 全量优化闭环（已完成）

### P0: 脱敏层 + 增强错误分类
| 任务 | 状态 | 文件 | 描述 |
|------|------|------|------|
| P0-1 统一脱敏层 | ✅ | `sensitive_policy.json`, `services/sanitizer.py` | 移植敏感策略文件，递归脱敏 dict/list/str/Exception，9 个正则替换规则 |
| P0-2 增强错误分类 | ✅ | `services/protocol_register.py` | 5 类精细分类（network/account/mailbox/auth_state/rate_limit），30+ 标记词 |
| P0-3 测试 | ✅ | `tests/test_error_classification.py` | 73 个测试覆盖分类函数和 _classify_failure 集成 |

### P1: 注册进度持久化 + 状态机 + BrowserRegister 拆分
| 任务 | 状态 | 文件 | 描述 |
|------|------|------|------|
| P1-1 进度持久化 | ✅ | `services/registration_progress.py` | @track_registration 装饰器，JSONL 持久化，注册阶段历史记录 |
| P1-2 状态机重构 | ✅ | `services/protocol_register.py` | 10 阶段显式状态机（RegistrationState）+ 数据类上下文 + 转场验证 |
| P1-3 BrowserRegister 拆分 | ✅ | `services/browser_page_detector.py`, `browser_form_filler.py`, `browser_oauth.py` | 902 行 → 4 个模块，保持向后兼容 |

### P2: 类型化模型 + 配置冻结
| 任务 | 状态 | 文件 | 描述 |
|------|------|------|------|
| P2-1 类型化模型 | ✅ | `services/models.py` | AccountData frozen dataclass + safe_snapshot() 自动脱敏 |
| P2-2 配置冻结 | ✅ | `services/config_service.py` | RuntimeConfig 不可变，MappingProxyType 保护 + 线程安全 reload |
| P2-3 测试 | ✅ | `tests/test_models.py`, `tests/test_config_service.py` | 20 个测试全部通过 |

### 测试结果
```
422 passed, 303 warnings in 278.55s (0:04:38)
```

### 新增文件清单
- `sensitive_policy.json` — 敏感凭据脱敏策略
- `services/sanitizer.py` — 统一脱敏层
- `services/registration_progress.py` — 注册进度持久化
- `services/models.py` — 类型化数据模型
- `services/config_service.py` — 配置冻结层
- `services/browser_page_detector.py` — 浏览器页面检测
- `services/browser_form_filler.py` — 浏览器表单填写
- `services/browser_oauth.py` — 浏览器 OAuth 模块
- `tests/test_error_classification.py` — 错误分类测试
- `tests/test_registration_progress.py` — 进度持久化测试
- `tests/test_models.py` — 数据模型测试
- `tests/test_config_service.py` — 配置服务测试

### v3.3.0 已验证

| 检查项 | 验证方式 | 结论 |
|--------|---------|------|
| 全量测试 | `pytest tests/ -q` | 288 passed |
| TLS指纹池 | 冒烟：连续5次注册各选不同指纹，UA配套正确 | PASS |
| 多平台邮箱库 | 测试：platform_filter/insert_email带platform/platform_stats | 全绿 |
| 邮箱平台切换UI | E2E：平台列表/过滤/单邮箱平台映射 | 全通 |
| Docker构建 | `docker compose up -d` | 正常 |
| 一账号一IP | 实测3个注册代理各不同出口IP | 已验证 |

### v3.4 验证状态 (未提交，待验证)

| 检查项 | 验证方式 | 结论 |
|--------|---------|------|
| 全量测试 | 尚未运行 (工作区未提交) | ⏳ |
| 日志异步批量 | 待单元测试验证 | ⏳ |
| SSE推送 | 待playwright E2E验证 | ⏳ |
| 代理黑名单 | 待单元测试验证 | ⏳ |
| CF solver Turnstile | 需真实CF环境验证 | ⏳ |
| webhook通知 | 需真实webhook端点验证 | ⏳ |

---

## 四、验证历史 (防重复审计)

> 记录已验证区域与结论，避免下次盲目重跑。改了某区域后需更新状态为"需复验"。

| 区域 | 验证范围 | 结论 | 状态 | 日期 | 复验触发条件 |
|------|---------|------|------|------|-------------|
| SQL注入面 | 全仓execute(f"...")拼接点 | 值全参数化，无注入 | ✅ | 2026-08-06 | 新增手写SQL拼接时 |
| 慢查询/索引 | 5万行测试库EXPLAIN热点查询 | 全命中索引，无毒SQL | ✅ | 2026-08-06 | 新增查询/改schema时 |
| 并发竞态/start | try_start原子占位+双start测试 | 仅一个占位成功 | ✅ | 2026-08-06 | 改启动/引擎时 |
| 并发写config.json | 8并发写设置 | 不产生损坏JSON | ✅ | 2026-08-06 | 改settings写入时 |
| 引擎单例测试隔离 | conftest autouse重置单例 | 顺序无关可重复 | ✅ | 2026-08-06 | 改引擎/服务单例时 |
| PKCE配对 | sha256(verifier)==challenge实证 | 同对贯穿，换token合法 | ✅ | 2026-08-06 | 改protocol_register OAuth时 |
| RT轮换落库 | 真实OpenAI token实测 | 新AT+新RT均落库 | ✅ | 2026-08-06 | 改token刷新时 |
| TLS verify | 实测verify=True经代理刷新成功 | 默认true可用 | ✅ | 2026-08-06 | 改出站HTTP时 |
| 注册间隔限速 | 回归测试sem内sleep | interval生效 | ✅ | 2026-08-06 | 改run_batch时 |
| 前端鉴权401 | playwright实测 | 401→弹窗→输密钥→恢复 | ✅ | 2026-08-06 | 改鉴权中间件时 |
| bat双击启动 | os.startfile实测+字节检查=0 | 不闪退 | ✅ | 2026-08-06 | 改.bat时(必做字节检查) |
| 前端UI | playwright点遍5标签页+高级弹窗 | 0 console error | ✅ | 2026-08-06 | 改web_dist时 |
| 启动冒烟 | uvicorn起实例healthz | 正常 | ✅ | 2026-08-06 | 改启动/config时 |
| 浏览器池真实冒烟 | scripts/smoke_browser_pool.py | 真实camoufox PASS | ✅ | 2026-08-06 | 改池化逻辑时 |
| token刷新真实闭环 | scripts/smoke_token_refresh.py | 真实JWT落库PASS | ✅ | 2026-08-06 | 改token刷新时 |

---

## 五、剩余待办 (v3.4 未提交 + 长尾)

### 5.1 v3.4 待提交验证

- [ ] 运行 `pytest tests/ -q` 确认 v3.4 新增测试全绿
- [ ] 运行 `pytest tests/test_stress_security.py` 验证并发安全
- [ ] 前端playwright E2E验证SSE/三态toast/资产仪表盘
- [ ] 确认 `config_schema.validate_config(config.json)` 无告警
- [ ] 提交 `git commit` + `tag v3.4` + `gh release v3.4`
- [ ] 更新 docs/CHANGE_REPORT_v3.4.html + 测验

### 5.2 长尾待办 (非本轮闭环)

| 优先级 | 项 | 说明 | 关联模块 |
|--------|-----|------|---------|
| P2 | 设置保存后运行时生效 | get_engine单例缓存config，需重启提示或热重载 | api/settings.py, register_engine.py |
| P2 | 平台注册引擎扩展 | 目前仅chatgpt，grok/claude等需新建引擎 | services/ |
| P3 | settings双写非原子 | DB先写、config.json后写无锁 | api/settings.py |
| P3 | 清空端点防护不一致 | emails/clear、logs/clear无confirm | api/emails.py, api/logs.py |
| P3 | /docs /openapi.json鉴权 | 鉴权开启后仍公开(内部工具可接受) | api/__init__.py |
| P3 | 推送chatgpt2api的timeout验证 | 新30s超时在高延迟下是否足够 | api/register.py |
| - | 500条代理全量探测耗时 | 约150s(并发10×8s)，预期非bug | api/proxies.py |
| - | 真实完整注册流E2E | 需真实OpenAI账号+代理配额 | 全链路 |
| - | 完整真实并发注册压测 | 需真实代理+账号 | 全链路 |

---

## 六、已知限制

### 6.1 外部受限 (非bug，勿当新问题)

| 限制 | 原因 | 影响 |
|------|------|------|
| 真实注册E2E未跑 | 需真实OpenAI账号+可用代理，mock已验证协议全分支 | 无直接风险，PKCE/RT已真实验证 |
| 代理健康≠账号可用 | 仅TCP+HTTP出口探测，kookeey动态住宅仍可能账密/国家码失效 | 需真实注册结果验证 |
| CF solver Turnstile验证 | 需真实CF环境，mock未覆盖 | v3.4 T87仅代码实现，未真实冒烟 |
| webhook通知验证 | 需真实webhook端点(钉钉/企微/Bark) | v3.4 T85仅代码实现，未真实推送 |
| 浏览器池完整注册流 | 池化仅mock验证，真实注册需账号+代理+CF | v3.1 T6已真实camoufox冒烟池复用 |

### 6.2 已知技术债

| 债项 | 严重度 | 说明 |
|------|--------|------|
| settings热加载 | P2 | 改设置需重启生效，配置热重载未实现 |
| 配置双写非原子 | P3 | 无写锁，最后写入者胜出 |
| 指标/遥测缺失 | P3 | 无prometheus/metrics端点，排障依赖日志 |
| 消息队列 | P3 | 高并发下SQLite写锁竞争，日志异步化(T82)部分缓解但未消除 |

---

## 七、v3.4 变更文件清单 (未提交，工作区脏)

| 文件 | 变更行数 | 核心变更 |
|------|---------|---------|
| `services/db.py` | +183 | 日志异步批量(queue+flusher)、failure_type列、requeue_failed_emails、stop_log_flusher |
| `services/register_engine.py` | +91 | 专用线程池、硬超时护栏、代数安全暂停、失败代理拉黑、webhook通知 |
| `services/token_refresher.py` | +123 | 防重入哨兵、崩溃自愈退避、局部统计消除竞态、实时耗时扣除 |
| `services/proxy_service.py` | +103 | 代理黑名单(mark_bad/blacklist_size)、持久化JSON、get_next跳过黑名单 |
| `services/browser_register.py` | +77 | 浏览器指纹一致性(geo/时区/屏参)、CF solver Turnstile集成、kookeey 40+国时区 |
| `services/cf_solver_service.py` | +40 | 优雅停机(HTTP /shutdown→psutil树杀→camoufox兜底)、日志重定向到文件 |
| `services/notifier.py` | +131 | 新增：webhook告警通道(钉钉/企微/Bark/Server酱) |
| `services/name_service.py` | +101 | 姓名拟人化(首字母大写)、有界缓存 |
| `services/protocol_register.py` | +6 | 注册专用线程池注入接口 |
| `api/register.py` | +86 | retry-failed端点、push_chatgpt2api非阻塞、_refresh_oauth规范化、failure_type过滤 |
| `api/proxies.py` | +49 | 真实HTTP出口探测(exit_ip/country/latency)、blacklist_size |
| `api/stats.py` | +24 | account-readiness端点(四维就绪率聚合) |
| `api/logs.py` | +51 | SSE日志实时推送(/api/logs/stream) |
| `web_dist/app.js` | +312 | 三态toast、withBusy、资产仪表盘、SSE接收、流式日志前端、重试失败按钮、级别筛选/搜索 |
| `web_dist/index.html` | +23 | 就绪率卡片、转化漏斗条、重试失败按钮、日志筛选控件 |
| `tests/conftest.py` | +8 | 日志flusher隔离(切换DB时stop_flusher) |
| `requirements.txt` | +6 | psutil依赖 |
| `启动.bat` | +5 | 小调整 |
| `docs/SOP.md` | +1363 | SOP文档大幅扩充 |
| `cf_solver/api_server.py` | +2 | 小调整 |
| `cf_solver/boterdrop_wrapper.py` | +7 | 小调整 |

**总变更**: 21文件，+2374/-934

---

## 八、复现坑记录 (防下次重蹈)

> 记录本轮开发中踩过的坑，避免下次重复。

### v3.3 系列

- **TLS指纹不要在单例存可变状态**：ProtocolRegister是单例，_register_sync内选指纹并线程局部贯穿，不能存self.fingerprint（并发注册竞态）
- **platform字段迁移是ALTER TABLE ADD COLUMN**：已有数据库不能重CREATE TABLE，需ALTER+try/except兼容
- **platform_usage表唯一约束**：(email, platform) 唯一，同邮箱同平台只记一次，换平台可再注册

### v3.4 系列

- **日志异步化后测试要stop_flusher**：切换隔离DB前必须停后台flusher线程，否则旧线程把日志写进新DB
- **代理黑名单匹配**：`result["proxy"]` 由 `format_for_display` 输出为 `host:port` 格式，`mark_bad` 内部通过 `urlparse` 反向匹配 `proxies.txt` 行
- **CF solver Turnstile注入**：`cf-turnstile-response` 是iframe内name属性，需 `page.evaluate` 注入，不能直接 `page.fill`
- **SSE + 鉴权**：EventSource不支持自定义Header，需用 `fetch + ReadableStream` 手动解析以支持 `X-Auth-Key`
- **psutil依赖**：Windows上需新增 `psutil` 到requirements.txt，用于树杀子进程
- **代数安全**：`batch_generation` 在 `try_start` 时递增，`_auto_resume` 恢复前比对，变了说明已是新批次则放弃——防stop后60s误恢复

---

## 九、下次迭代建议

### 短期 (v3.4.1 / v3.5)

1. **v3.4 提交发布**: 先提交工作区脏变更 + 跑全量测试 → tag v3.4 → Release
2. **settings热加载**: 实现 `POST /api/settings/reload` 或配置变更时自动重载引擎
3. **平台注册引擎扩展**: 建立 `grok/claude/...` 注册引擎，复用现有邮箱池/代理池/失败诊断
4. **真实CF solver E2E**: 用真实CF环境验证 T87 Turnstile集成
5. **webhook真实冒烟**: 用钉钉/企微真实端点验证 T85 通知通道

### 中期 (v3.5+)

1. **指标/遥测系统**: 输出prometheus格式metrics端点，替代日志grep排障
2. **消息队列**: 引入简单内存队列解耦日志/通知/注册，降低SQLite写锁竞争
3. **配置热重载**: 引擎级热重载（不改DB/cache），改设置不重启
4. **测试覆盖率≥80%**: 当前约70%，补缺口（主要在浏览器注册/CF solver分支）
5. **E2E测试套件**: 用playwright写完整注册→导出→推送全流程E2E测试

### 长期

1. **多平台自动化注册**: 扩展platform引擎到grok/claude/其他AI平台
2. **账号质量评分**: 对已注册账号做可用性分级（新token/未过期/已失效）
3. **批量导出优化**: 支持按平台/状态/就绪度筛选导出
4. **Docker多阶段构建**: 减小镜像体积，分层缓存加速