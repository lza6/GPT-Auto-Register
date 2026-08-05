# Implementation Plan: v2.1 终局闭环审计

**Feature**: v2.1-final-audit
**Created**: 2026-08-06

## Architecture / Stack
- 现状：FastAPI + SQLite(WAL) + camoufox + 协议注册优先/浏览器兜底 + Web 单页
- 本轮不动架构，只补漏 + 收敛 + 治理资产
- 新增模块：`services/otp_extractor.py`、`services/constants.py`（纯逻辑，无新依赖）

## Data Model
- accounts 表：无新列（cf_retry 次数走 config，不入库）
- tasks 表：已存在，本轮只需 run_batch 写回 completed/failed/skipped/status/result
- logs 表：M4 容量上限长尾项，本次不碰

## Phases

### Phase 1: 修自己的漏洞（P0 止血）
- verify 截图目录自动创建（data/debug/）
- refresh_all 适配新退出码（3=cf_blocked, 4=unknown_page）
- browser_register CF 重试资源安全（new_context 异常兜底）
- _writeback_token 边界（db 不可达明确失败码）

### Phase 2: 核心链路补漏（P1）
- C3/H4 去重 O(1)：run_batch 循环外加载成功 email 集合
- D2 任务表落库：run_batch 每号 UPDATE tasks + status 接口返回进度 + 启动重置 running→interrupted

### Phase 3: 架构收敛（P2）
- B3 OTP 提取三合一 → services/otp_extractor.py
- B5 常量集中 → services/constants.py（保持 browser_register 向后兼容）

### Phase 4: 治理资产（P1）
- ADR-005（终局审计决策）
- HTML 变更报告 + 测验
- workflow_status.md 增量
- 工作流 + skill 整理
- 记忆更新

### Phase 5: 提交发行版（P1）
- git commit + push origin main
- gh release create v2.1

## Technical Constraints
- 宪法优先：真实落地、增量迭代、证据驱动
- 不重构没坏的：协议注册优先不动、前端单页不拆分
- 兼容性：常量收敛后旧 import 路径保持可用
- 测试：每个 FR 对应测试，pytest 全绿，覆盖率不降

## Long-tail (deferred to tasks.md,后续会话续)
- H3 浏览器池复用、F 前端系列、G1 token 保鲜、模块 H 测试套件
