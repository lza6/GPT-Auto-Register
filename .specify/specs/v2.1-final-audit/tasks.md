# Tasks: v2.1 终局闭环审计

**Feature**: v2.1-final-audit
**Created**: 2026-08-06

## Phase 1: 修自己漏洞 (P0 止血)

- [x] T1: verify 截图目录自动创建 `data/debug/`（不再静默吞错） — `scripts/verify_account_login.py`
- [x] T2: refresh_all.py 适配新退出码（rc==3→reason=cf_blocked, rc==4→reason=unknown_page） — `scripts/refresh_all.py`
- [x] T3: browser_register CF 重试 new_context 异常兜底（try/except + page 重新赋值 + return cf_blocked） — `services/browser_register.py`
- [P] [x] T4: _writeback_token 边界（db 不可达返回 False，调用方据此返回 EXIT_FAIL） — `scripts/verify_account_login.py`

## Phase 2: 核心链路补漏 (P1)

- [x] T5: C3/H4 去重 O(1) — 已闭环(v2.0)，register_engine.py:164-165 预加载集合 — 校准无需重修
- [x] T6: D2 任务表落库 — 已闭环(v2.0)，每号 update_task_progress — 校准无需重修
- [ ] T7: D2 status 接口返回真实进度（completed/total） — `api/register.py`（长尾，get_latest_task 已返回部分）
- [ ] T8: D1 启动重置 running→interrupted — `main.py` 启动时（长尾）

## Phase 3: 架构收敛 (P2)

- [x] T9: B3 OTP 提取三合一 → 新建 `services/otp_extractor.py`，三 service 统一 import — `services/email_service.py`/`graph_email_service.py`/`imap_email_service.py`
- [x] T10: B5 常量集中 → 新建 `services/constants.py`，browser_register import（向后兼容） — `services/constants.py`

## Phase 4: 治理资产 (P1)

- [ ] T11: ADR-005 终局审计决策 — `docs/ADR/ADR-005.md`
- [ ] T12: HTML 变更报告 + 6 题测验 — `docs/CHANGE_REPORT_v2.1.html`
- [ ] T13: workflow_status.md 增量更新（需求追踪矩阵 + 验证历史 + 复现坑） — `workflow_status.md`
- [ ] T14: 工作流 + skill 整理（终局审计工作流 + critical-code-reviewer skill） — `.claude/skills/`
- [ ] T15: 记忆更新（v2.1 状态） — `~/.claude/.../memory/gpt-auto-register-v2-state.md`

## Phase 5: 提交发行版 (P1)

- [ ] T16: git add + commit feat v2.1
- [ ] T17: git push origin main
- [ ] T18: gh release create v2.1（notes 含完整变更）

## Phase 6: 长尾（沉淀，后续会话续）

- [ ] H3: 浏览器池复用（CamoufoxPool）
- [ ] F1-F7: 前端 UI/UX 全面升级
- [ ] G1: token 保鲜后台任务
- [ ] G2-G6: 账号健康/失败统计/代理测速/webhook/多任务队列
- [ ] H1-H4(工程): pytest 套件扩展、性能基准
- [ ] M3/M4/M5/M8: db_session/日志容量/前端轮询/启动脚本优化
- [ ] A4: 失败分级与重试策略
- [ ] A6: 并发注册落地
- [ ] B1: 双引擎统一（registration_flow.py）
- [ ] B2: 死代码清理（curl_cffi 协议注册、imap、空目录）
- [ ] B4: 配置校验
- [ ] D3: 健康检查 /api/healthz
- [ ] D4: 启动.bat 去重复安装
- [ ] M10: 硬编码凭据迁移 config/env
- [ ] L1-L5: 前端细节打磨

## Phase 7: 生产级增强（长尾，后续会话+Agent管理Agent自动推进）

> 用户要求"自动根据项目挑选联网搜索高价值能生产上线、高并发、安全、性能、UI美观高级等"。
> 本项目是单机 SQLite + camoufox 注册工具，非高并发 SaaS，以下按适用性筛选，非全量照搬。

### 适用项（单机工具可受益）
- [ ] D3: /api/healthz 健康检查（DB 可达 + CF Solver 状态 + 浏览器池实例数）— 真实可做
- [ ] L4: get_stats 合并为一条聚合 SQL（7 次 COUNT→1 次）— 简单真实
- [ ] M3: db_session contextmanager 已有，补连接池/写锁串行化（并发=3 防数据库锁）— 已部分
- [ ] M4: logs 表容量上限 + 自动归档 + 分页（MAX_LOG_ROWS=20000）— 真实可做
- [ ] L5: 优雅停机（uvicorn shutdown 关浏览器池/CF solver）— 简单

### 不适用项（高并发 SaaS 模式，单机工具无收益，勿强加）
- ~~Load Balancer~~（单进程单机）
- ~~Redis Caching~~（SQLite WAL 够用）
- ~~CDN~~（本地 web_dist 单页）
- ~~Database Replication~~（单库）
- ~~Sharding~~（单库）
- ~~Message Queues Kafka/RabbitMQ~~（asyncio.gather 够用）
- ~~Circuit Breaker~~（register_engine 已有失败分级）
- ~~Rate Limiting~~（单用户本地，非公开 API）

### SOP/工程化（适用）
- [ ] H1(工程): pytest 套件扩展至 ≥90% 核心覆盖
- [ ] H4: 性能基准 benchmarks.md（10 号串行基线）
- [ ] 文档: README 并发/鉴权/健康/token 保鲜说明补齐
- [ ] 提交规范: conventional commits + CHANGELOG

### 商业/产品（用户提到，但本项目是工具非 SaaS，仅记录）
- [ ] token 保鲜 G1（后台续期闭环）— 真实可做
- [ ] 账号健康巡检 G2 — 真实可做
- [ ] 失败原因分类统计 G3 — 真实可做
