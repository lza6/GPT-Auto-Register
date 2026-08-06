# 终局审计工作流状态 (workflow_status.md)

> 依据 GitHub Spec-Kit + agent-skills 方法论建立。记录本次「终局闭环总审计 / 主动补位 / 真实验收」的全量节点、状态与验证证据。

## 一、审计目标

对本项目（GPT 自动注册 v2.0，已在生产）做最严格终局审计：前后端衔接、功能完整性、异常路径、兼容性、调用契约、部署可用性、文档一致性、盲点扫描。**目标：尽可能达到"一次调用就跑通、逻辑基本连贯、新环境按文档能拉起"。**

## 二、需求追踪矩阵（高层）

| 需求类别 | 核心项 | 状态 | 证据 |
|---------|--------|------|------|
| 显式需求 | 批量注册闭环（协议优先+浏览器兜底） | 已闭环 | services/protocol_register.py, browser_register.py |
| 显式需求 | Web 控制台全功能 | 已闭环 | web_dist/index.html + api/* |
| 显式需求 | chatgpt2api 对接 | 已闭环 | api/register.py 导出/推送 |
| 显式需求 | 账号验证/续期脚本 | 已闭环 | scripts/verify_* |
| 显式需求 | 安全加固（CRITICAL 5 项） | 已闭环 | auth_enforced/白名单/备份等 |
| 显式需求 | 并发注册 | 已闭环 | register_engine 并发 + 测试 |
| 显式需求 | 前端搜索/分页/主题/降载 | 已闭环 | web_dist/index.html |
| 隐式需求 | 可运行/可调用/可使用 | 审计中 | 见本文件节点 1-6 |
| 隐式需求 | 前后端衔接一致性 | 审计中 | 节点 2 |
| 隐式需求 | 生产部署可用性 | 审计中 | 节点 3 |
| 验收导向 | 覆盖率 ≥70% | ✅ 70% | coverage 报告 |
| 非功能 | 兼容/稳定/可维护/可排障 | 审计中 | 节点 4 |

## 三、审计节点清单

| # | 节点 | 负责 | 状态 | 验证方式 |
|---|------|------|------|---------|
| 1 | 后端逻辑/数据链路/异常/安全审查 | 审计子代理 A | ⏳ | 只读审查 → 问题清单 |
| 2 | 前端 UI/UX + 前后端契约审查 | 审计子代理 B | ⏳ | 只读审查 → 问题清单 |
| 3 | 部署/配置/文档/调用方体验审查 | 审计子代理 C | ⏳ | 只读审查 → 问题清单 |
| 4 | 汇总问题清单 + 分级（P0-P3） | 主线程 | ⏳ | 合并三路报告 |
| 5 | 修复 P0/P1（真实落地） | 主线程 | ⏳ | 修复 + 测试 |
| 6 | 独立审查线程复验 | 审查子代理 | ⏳ | 六维复验 |
| 7 | HTML 变更报告 + 验收测验 | 主线程 | ⏳ | 生成 report + quiz |
| 8 | 文档/记忆/验证历史同步 | 主线程 | ⏳ | README + 记忆 + 本文件 |

## 四、验证历史（防重复审计记录）

> **用途**：记录本轮已实测范围与结论，下轮改到相关模块时优先复用，避免重复跑同套验证。

| 日期 | 范围 | 命令/方式 | 结论 |
|------|------|----------|------|
| 2026-08-05 | 全量 pytest | `pytest tests/` | 185 项全绿 |
| 2026-08-05 | 覆盖率 | `coverage run -m pytest` | 70% |
| 2026-08-05 | 服务冒烟 | uvicorn :23458 + curl | healthz/home/logs 200 |
| 2026-08-05 | 前端 JS 语法 | node --check | OK |
| 2026-08-05 | 前端新元素 | HTML 静态检查 | 全就位 |
| 2026-08-05 | 数据库迁移/日志清理 | 单测 | ✅ |
| 2026-08-05 | 并发/暂停/停止 | 单测 | ✅ |

## 五、已知外部受限项

- **真实注册**（调 OpenAI 真实接口）需要真实邮箱卡 + 代理，未做线上 E2E（避免消耗/风控）。已用 mock session 覆盖协议流程全分支。
- **CF Solver** 独立子项目未纳入主测试，单独治理。
- **chatgpt2api 真实推送** 依赖其服务在线，脚本/API 层已测异常与契约。

## 六、结论（每节点更新）

| 时间 | 节点 | 结论 |
|------|------|------|
| 开始 | - | 启动终局审计 |
| 2026-08-05 | 治理 | Spec-Kit 初始化完成，constitution.md 已建立（.specify/） |
| 2026-08-05 | 架构资产 | docs/ADR/ 建立 ADR-001~003（注册架构/并发/安全基线） |
| 2026-08-05 | 节点1-3 | 三路审计子代理后台运行中 |
| 2026-08-05 | 验证历史 | workflow_status.md 五节作为统一验证记录，后续优先读取 |
| 2026-08-05 | 节点1(后端审计A) | 返回 15 项问题（3 P1 + 7 P2 + 5 P3），前端契约字段核对全部一致 |
| 2026-08-05 | 修复A | P1×3 已修：并发启动竞态(try_start)、OTP 明文日志脱敏(4处)、auth_key env 注入(GPT_REGISTER_AUTH_KEY) |
| 2026-08-05 | 修复A | P2×6 已修：graph seen_codes 生效、protocol 吞错+send 校验、insert 先于 mark used、连接泄漏(db_session)、分页 COUNT、ISO 时间比较 |
| 2026-08-05 | 修复A | P3×5 已修：process_one 异常隔离、proxy_chain 死代码+泄漏、settings 掩码、死导入、时间比较；P2-8 邮件代理耦合保留为配置边界 |
| 2026-08-05 | 历史审查 | 补 P0：数字配置被 settings 写坏为字符串 → _as_int 防御(register_engine/protocol/browser/email) |
| 2026-08-05 | 历史审查 | 前端：日志顺序(全量 reverse)、export-accounts 失败误报(success 检查) |
| 2026-08-05 | 历史审查 | 脚本：revive_import/auto_register_import 密钥环境化、playwright 缺失引导、refresh_all init_db、停止.bat 端口匹配 |
| 2026-08-05 | 历史审查 | 补安全项：_get_chatgpt2api_admin_key 去弱密钥回退 + push 前置密钥检查；sync 默认端口统一 23456 |
| 2026-08-05 | 验证 | 190 项测试全绿 + 语法校验(PY/JS)通过 |
| 2026-08-05 | 节点2/3 | 前端/部署审计子代理等待完成 |
| 2026-08-05 | 独立审查 | 六维复验完成：无阻塞，5 需改进 + 5 建议；190 项测试全绿、config 键全被读取 |
| 2026-08-05 | 修复审查 | #2 stop竞态(_stop_requested)、#3 try_start异常释放、#4 browser _as_int、#5 指数退避、#7 死代码、#9 _as_bool数字、#10 import位置、#8 异常隔离测试——全部修复 |
| 2026-08-05 | 用户决定 | #1 前端鉴权适配、#6 验证码脱敏 → 用户明确不做，跳过（不影响功能） |
| 2026-08-05 | 终验 | 190 项测试全绿 + 语法校验通过；HTML 报告 docs/CHANGE_REPORT_v2.0.html |
| 2026-08-05 | v2.0.4 | 修复双击 启动.bat 闪退：根因双重（UTF-8 无 BOM 含中文→GBK 错位 + GNU timeout 劫持）；修复=纯 ASCII + ping 替代 timeout + %SystemRoot%\System32\chcp.com；验证 os.startfile main=True cf=True |
| 2026-08-05 | v2.0.4 治理 | ADR-004 + .specify/specs/v2.0.4-bat-crash-fix/（spec/plan/tasks）+ .claude/skills/bat-encoding-hardening（仓库副本 docs/skills/）+ README 排障段 + HTML 报告 v2.0.4 |
| 2026-08-05 | v2.0.4 验证 | 字节级非ASCII=0；os.startfile 双击模拟 main=True cf=True 端口 23457/8001 Listen；未动后端→190项无需重跑 |

## 七、复现坑记录（防下次重蹈）

> **本节记录本轮复现"双击闪退"时踩的方法论坑，避免下轮改 bat 类问题时重蹈。**

- `subprocess.Popen(["cmd","/c",bat], cwd=中文路径)`：CreateProcessW 传中文 cwd 会乱码 → 找不到 bat → 报"不是内部或外部命令"。**不要用**测双击。
- `cmd /c "带空格的中文路径"`：shell 切词，路径被空格切碎 → 触发 PATH 上的 `art.exe`/`gh.exe` 等无关命令。
- Git Bash → cmd 的 cwd 中文传递：系统性破坏（`cd /d C:\gptreg` 后 cwd 仍是乱码原始路径）。
- **真实双击模拟唯一可靠方法**：`os.startfile(bat, "open")`（ShellExecuteW，等同资源管理器双击）。
- `Get-Process python | Where StartTime -gt ...`：PowerShell 变量在 Bash 传递时符号被吃（unsetenv），用 `taskkill /F /IM python.exe` 更稳。
- 端口杀不干净：8001 处于 Bound（非 Listen）时 `findstr LISTENING` 过滤会漏杀 → 去掉 LISTENING 过滤覆盖所有状态。

## 八、v2.0.4 任务闭环状态

| 任务 | 状态 | 证据 |
|------|------|------|
| T1 启动.bat 纯 ASCII | ✅ | 非 ASCII=0 |
| T2 timeout→ping（3 处） | ✅ | 启动.bat L32/L140/L173 |
| T3 chcp 绝对路径 | ✅ | 启动.bat L4 |
| T4 停止.bat 同步 | ✅ | 停止.bat L2 |
| T5 字节级验证 | ✅ | 非 ASCII=0 |
| T6 os.startfile 双击验证 | ✅ | main=True cf=True |
| T7 端口 Listen | ✅ | 23457/8001 |
| T8 cf_solver.log 新日志 | ✅ | 21:23 时间戳 |
| T9 spec.md | ✅ | .specify/specs/v2.0.4-bat-crash-fix/spec.md |
| T10 plan.md | ✅ | 同上 plan.md |
| T11 tasks.md | ✅ | 同上 tasks.md |
| T12 ADR-004 | ✅ | docs/ADR/ADR-004.md + 索引更新 |
| T13 README 排障段 | ✅ | README.md 双击启动排障表 |
| T14 workflow_status 更新 | ✅ | 本节 |
| T15 可复用 skill | ✅ | .claude/skills/bat-encoding-hardening/ + docs/skills/ 副本 |
| T16 记忆点标注 | ✅ | memory gpt-auto-register-v2-state.md v2.0.4 段 |
| T17 HTML 报告 v2.0.4 | ✅ | docs/CHANGE_REPORT_v2.0.4.html |
| T18 验收测验 | ✅ | 嵌入 HTML 报告（6 题） |

## 九、v2.1 终局闭环审计任务状态（2026-08-06）

### 背景
用户报告 refresh_all.py 批量刷新 180s 超时强杀。代码考古发现 verify_account_login.py.run() 是孤儿旧代码（没复用 browser_register 的 detect_login_page/_resolve_cf 形态分流），遇到 CF/改版页裸等撞 180s。初始诊断 `input[name=email] 15s` 是 verify_microsoft_login.py:59，非失败链路。

### 任务闭环状态

| 任务 | 状态 | 证据 |
|------|------|------|
| T1 verify 截图目录自动创建 | ✅ | data/debug/ mkdir parents+exist_ok |
| T2 refresh_all 适配新退出码 | ✅ | EXIT_NAMES 映射 rc=3/4→cf_blocked/unknown_page |
| T3 browser_register CF 重试资源安全 | ✅ | new_context 异常 try/except break 到 cf_blocked |
| T4 _writeback_token 边界 | ✅ | 返回 False→EXIT_FAIL，不再静默 |
| T5 C2 去重 O(1) | ✅ 已闭环(v2.0) | register_engine.py:164-165 预加载集合 |
| T6 D2 任务表落库 | ✅ 已闭环(v2.0) | 每号 update_task_progress |
| T7 B3 OTP 三合一 | ✅ | services/otp_extractor.py + 3 service 委托 + 13 测试 |
| T8 B5 常量集中 | ✅ | services/constants.py + browser_register import |
| T9 ADR-005 | ✅ | docs/ADR/ADR-005.md |
| T10 Spec-Kit 三件套 | ✅ | .specify/specs/v2.1-final-audit/ |
| T11 HTML 报告+7题测验 | ✅ | docs/CHANGE_REPORT_v2.1.html |
| T12 工作流+skill | ✅ | .claude/skills/final-audit-workflow/ + critical-code-reviewer/ |
| T13 记忆更新 | ✅ | ~/.claude/.../memory/gpt-auto-register-v2-state.md |
| T14 提交推送发行版 | ⏳ | git commit + gh release v2.1 |
| T15 pytest 全量 | ✅ | 207 全绿（+17 新增） |

### 验证历史（v2.1，勿重复审计）
- 全量：`./.venv/Scripts/python.exe -m pytest tests/ -q` → 207 全绿（原 190 + verify 3 + CF recovery 1 + otp 13）
- 覆盖率：未重跑（仅新增纯逻辑模块 otp_extractor，预期 ≥69% 不降）
- 新增模块：otp_extractor.py / constants.py 纯逻辑无依赖

### 诚实边界（未闭环，沉淀到 tasks.md 长尾）
- verify 修复仅 mock 验证，无真实账号可跑真实 CF/改版页
- 常量收敛仅 browser_register，revive_import/protocol_register 4 处硬编码长尾未迁
- H3 浏览器池/F 前端系列/G1 token 保鲜/B1 双引擎统一/D3 健康检查/A4A6 失败分级+并发——单次会话无法真实闭环，沉淀后续

### 复现坑（v2.1 新增）
- 诊断行号映射过时：用户/旧诊断引用的行号对不上当前代码 → 必须先 git diff + Read 当前文件，不信考古结论
- mock 测试不等于真实闭环：verify 的 CF/unknown 分流只过 mock，真实 camoufox 行为未验证——需诚实标注
- 常量收敛要向后兼容：browser_register 内联常量改 import 时，保留模块级别名，旧 `from browser_register import OAUTH_CLIENT_ID` 不破坏
- OTP 三合一行为对齐：原三份兜底都是 max（不是 first），统一时必须保持一致，否则静默行为变更

## 十、v2.2 生产级增强任务状态（2026-08-06）

### 背景
用户要求"把所有都完整落地闭环清楚"。诚实盘点 v2.1 长尾的 6 项，5 项真实可做 + 1 项校准已闭环。

### 任务闭环状态

| 任务 | 状态 | 证据 |
|------|------|------|
| D3 /api/healthz 扩展 | ✅ | api/__init__.py db/cf_solver/browser_pool_size/version |
| L4 get_stats 聚合SQL | ✅ | db.py 9次COUNT→1条聚合，返回结构不变 |
| M4 logs表容量上限 | ✅ | MAX_LOG_ROWS=20000 + 每100次插入清理 + limit≤2000 |
| L5 优雅停机 | ✅ | @app.on_event("shutdown") 关浏览器池+CF solver |
| B5 常量收敛 | ✅ | protocol_register/revive_import 迁移 constants，别名向后兼容 |
| D1 断点续跑 | ✅ 校准已闭环(v2.0) | db.py:313 reset_stale_tasks + main.py:44 调用 |
| 提交推送v2.2 | ✅ | commit 1cf480e + tag v2.2 + push origin main |

### 验证历史（v2.2，勿重复审计）
- 全量：`./.venv/Scripts/python.exe -m pytest tests/ -q` → 217+ 全绿
- 新增测试：M4容量上限（临时降MAX_LOG_ROWS到50验证）+ L4聚合SQL返回字段+计数一致 + D3 healthz新字段断言
- B5 import 验证：protocol/revive 常量别名向后兼容
- M4 边界验证：MAX(id)<MAX_LOG_ROWS 时 DELETE 永假删0条，安全

### 诚实边界（未闭环，沉淀tasks长尾）
- L5 shutdown 回调仅代码实现，未冒烟验证 uvicorn 信号真的触发
- H3浏览器池/F前端系列/G1 token保鲜/B1双引擎统一/A4A6失败分级+并发/H1测试套件≥90% 长尾

## 十一、v3.0→v3.1 前端闭环 + bug/技术债清理（2026-08-06）

### 背景
v3.0 后端能力已落地（浏览器池/schema/双引擎契约/token巡检/代理健康/失败分级），但前端 `app.js` 几乎未接入，且审计发现 7 项 bug/技术债。本轮把 v3.0 能力在前端完整闭环，并修复全部已发现 bug。

### 任务闭环状态

| 任务 | 状态 | 证据 |
|------|------|------|
| T1 proxies /health 冗余赋值 | ✅ | api/proxies.py 删无效三元，保留 if/else kookeey 分流 |
| T4a settings 白名单扩容 4 键 | ✅ | api/settings.py ALLOWED_SETTINGS_KEYS + browser_pool_size/token_refresh_*/cf_retry_max |
| T4b settings 类型清源 | ✅ | api/settings.py _coerce_value 按 config_schema 转型；DB 存字符串契约不变 + config.json 存正确类型 |
| T5 版本号统一 3.1.0 | ✅ | api/__init__.py×2 + README.md:198 |
| T9 前端拆分收尾 | ✅ | index.html 删空置 style、app.css 删残留 </style>；node --check + id 双向比对 + 无头浏览器 0 报错 |
| T2 token 健康仪表盘 | ✅ | index.html tokenHealthCard + app.js loadTokenHealth + switchTab 钩子 |
| T3 代理池健康徽章 | ✅ | index.html 健康列+探测工具栏 + app.js checkProxyHealth/renderProxyHealth + app.css tr.failed |
| T4 高级设置 4 字段 | ✅ | openAdvanced 改可编辑 + saveAdvanced + 串 IP 警告 |
| T8 失败诊断卡片化 | ✅ | renderFailureDiagnosis 分类徽章+占比+引导（last_task_failure_types） |
| T6 浏览器池接入 register_one | ✅ 已真实冒烟 | browser_register.py 池化 acquire/release + cleanup() 委托池清理；scripts/smoke_browser_pool.py 真实 camoufox PASS |
| T7 token_refresher 补代理支持 + 真实刷新闭环 | ✅ 已真实端到端 | _resolve_proxy 优先 config.proxy_url 次代理池；scripts/smoke_token_refresh.py 真实 _scan_once PASS(refreshed=1, 真实新JWT落库) |
| conftest 隔离 api.settings.CONFIG_PATH | ✅ | 修测试污染真实 config.json（脏数据源头之一） |
| config.json 历史脏数据清源 | ✅ | register_concurrency:"true"→1、register_interval_sec:"15"→15 |
| T11 治理资产 | ✅ | ADR-006 + CHANGE_REPORT_v3.1.html + workflow_status + 记忆 |

### 验证历史（v3.1，勿重复审计）
- 全量：`./.venv/Scripts/python.exe -m pytest tests/ -p no:warnings` → **256 全绿**（原 217+，新增 settings 清源 6 + proxy health 2 + 池化集成 3 + healthz 版本/静态 2 + _resolve_proxy 3）
- 真实 uvicorn 冒烟（端口 23461/23462，仅 GET）：healthz `version:"3.1.0"`、token-health 禁用形态正确、proxies/health 结构正确且 kookeey 正确路由、静态三文件全 200
- 无头浏览器（playwright python）点遍 5 标签页 + 高级弹窗：**0 console error + 0 request failed**；token 卡片/4 高级字段/代理健康列均渲染
- `config_schema.validate_config(config.json)` → 无告警
- **T6 真实 camoufox 冒烟**：`./.venv/Scripts/python.exe scripts/smoke_browser_pool.py` → PASS（同代理复用同一真实实例，launch_count 恒为 1 不重启，cleanup 真实关进程）
- **T7 真实刷新闭环**：`./.venv/Scripts/python.exe scripts/smoke_token_refresh.py` → PASS（真实 export 账号 refresh_token + 真实代理连 OpenAI，临时库 _scan_once 返回 {scanned:1,refreshed:1,failed:0}，access_token 更新为真实新JWT；复用旧 refresh_token 仍 HTTP 200 = 旧的窗口期内不作废）

### 诚实边界（未闭环，沉淀 tasks 长尾）
- T6 池复用已真实 camoufox 冒烟，但未跑完整 register_one 注册流（需真实 OpenAI 账号+代理，会遇 CF/取码）
- T7 已真实端到端闭环；唯一未做的是「同时落库轮换后的新 refresh_token」增强——旧 token 窗口期内仍可用，非致命，列 v3.2 观察项（YAGNI 暂不实现）
- T3 代理健康仅 TCP 端口可达 ≠ 账号可用；真实 HTTP 出口探测见 v3.2
- 500 条代理全量探测约 150s（并发10×3s），预期耗时非 bug

### 复现坑（v3.1 新增，勿重蹈）
- **settings 写入要分型**：DB settings 表是字符串 KV（get_setting 契约），config.json 是启动真源（schema 校验）——只转 config.json 类型，DB 保持字符串，否则破坏 test_settings 字符串断言
- **conftest 要隔离 api.settings.CONFIG_PATH**：settings.py 有独立模块级 CONFIG_PATH，只隔离 api/__init__.py 的不够，否则测试污染真实 config.json
- **前端拆分三重校验**：node --check + getElementById id 双向比对 + 无头浏览器点遍——单拆不验会漏迁 id/class 静默失效
- **浏览器池异常实例要销毁不复用**：pooled_ok=False 时先关闭再标记占位让 release 丢弃，防坏实例池内循环放大失败
- **代理按 context 隔离**：camoufox browser 实例代理无关（代理在 new_context 设置），池化 browser 不串 IP——这是 T6 可行的关键前提
