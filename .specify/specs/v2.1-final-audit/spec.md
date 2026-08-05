# Feature Specification: v2.1 终局闭环审计 + 注册链路加固 + 治理资产补建

**Feature Branch**: `v2.1-final-audit`
**Created**: 2026-08-06
**Status**: In Progress
**Input**: 用户要求"终局闭环总审计/主动补位/真实验收/深度反向修复"，已在生产部署，需把上次未落地的 H1-H7/M1-M10/模块A-G 全量需求做需求追踪矩阵+反向审计+真实闭环，最终提交推送发行版+HTML报告+测验+工作流化。

## User Scenarios & Testing

### User Story 1 - 真实用户跑 verify 不再卡死 180s (Priority: P0)
用户/调用方跑 `refresh_all.py` 批量刷新，遇到 CF 挑战页或 OpenAI 改版页时，verify 链路必须明确早退（cf_blocked/unknown_page），不再裸等密码框被 180s 强杀。

**Why this priority**: 注册链路失败 = 整批卡死 = 生产不可用，止血最高优先。
**Independent Test**: mock CF 页/unknown 页/密码页三态，断言退出码 3/4/1；真实账号跑（外部受限时降级为 mock 证据）。

**Acceptance Scenarios**:
1. **Given** verify 跑到 CF 挑战页, **When** `_resolve_cf` 失败, **Then** 返回码 3 (cf_blocked) 且 refresh_all 记录 `reason=cf_blocked`。
2. **Given** verify 跑到 unknown 页（无 email/password/code input）, **When** 形态检测, **Then** 返回码 4 (unknown_page) + 截图落 `data/debug/` + 页面文字摘要入日志。
3. **Given** CF 解开后页面变 email/otp/about-you, **When** detect, **Then** 明确早退码 1（本脚本是密码登录流程），不裸等密码框。

### User Story 2 - browser_register CF 重试不泄漏资源 (Priority: P0)
register_one 遇 CF 解不开时换代理重试，必须保证：旧 context 关闭失败不阻塞、新 context 创建失败有兜底、page 变量不悬空、finally 重复关闭不抛。

**Why this priority**: 真实 camoufox 资源泄漏 = 僵尸进程累积 = 生产 OOM。
**Independent Test**: mock new_context 抛异常，断言 register_one 不抛未捕获异常、返回 cf_blocked。

### User Story 3 - 任务状态实时落库 (Priority: P1)
run_batch 每完成/失败/跳过一个账号即 UPDATE tasks 表；前端进度条展示真实进度；服务重启后 running 状态重置为 interrupted。

**Why this priority**: 前端进度条当前是假的，任务历史不可信，无法断点续跑。
**Independent Test**: 跑 1 个 email mock register_one，断言 tasks 表 completed=1/status=completed；kill 进程后重启断言 interrupted。

### User Story 4 - 批量去重 O(1) (Priority: P1)
run_batch 循环外一次性加载已成功 email 集合，循环内 O(1) 判断，不再每号全表查询。

**Why this priority**: 100 号 = 100 次全表查询，账号越多越慢。
**Independent Test**: mock 100 个 pending email，断言 get_accounts(status="success") 只调用 1 次。

### User Story 5 - OTP 提取三合一 + 常量集中 (Priority: P2)
三个 email service 的 OTP 正则收敛到 `services/otp_extractor.py`；OAuth client_id 等常量收敛到 `services/constants.py`。

**Why this priority**: DRY，改一处漏两处的反模式。
**Independent Test**: 同一封样例邮件在三个 service 下提取结果一致；`rg "app_2SKx67"` 只命中 constants.py。

### User Story 6 - 治理资产 + 发行版 (Priority: P1)
完整 spec/plan/tasks 三件套、ADR-005、HTML 变更报告+测验、workflow_status 增量、提交推送 GitHub Release v2.1、工作流+skill 整理、记忆更新。

**Why this priority**: 用户明确要求"提交推送仓库发行版"+HTML报告+测验+工作流化。
**Independent Test**: git log 有 feat v2.1 提交；gh release list 含 v2.1；HTML 报告含测验且答案可校验。

### Edge Cases
- `data/debug/` 目录不存在时 screenshot 静默失败（宪法禁止）→ 必须自动建目录。
- verify 返回码 3/4 但 refresh_all.py 仍记 exit=1 → 前后端契约断 → refresh_all 必须适配新码。
- CF 重试时 new_context 抛异常 → 旧 context 已关、page 悬空 → 必须有 try/except 兜底 + return cf_blocked。
- _writeback_token 数据库不可达 → 必须明确返回失败码，不能静默吞。
- 常量收敛后，旧 import 路径（browser_register 内联 OAUTH_*）必须保持向后兼容。

## Requirements

### Functional Requirements
- **FR-001**: verify_account_login.run() 必须复用 detect_login_page/_resolve_cf 形态分流，CF/unknown 页明确早退（码 3/4），不裸超时。
- **FR-002**: browser_register CF 重试（_cf_retry_max 次）换代理时，旧 context 关闭、新 context 创建、page 重新赋值必须全在 try/except 内，资源不泄漏。
- **FR-003**: refresh_all.py 必须适配 verify 新退出码（3=cf_blocked, 4=unknown_page），结果文件记录对应 reason。
- **FR-004**: run_batch 必须每号完成后 UPDATE tasks 表（completed/failed/skipped/status/result），status 接口返回真实进度。
- **FR-005**: run_batch 去重集合必须在循环外一次性加载（O(1) 判断），不再循环内全表查询。
- **FR-006**: 三个 email service 的 OTP 提取正则收敛到 services/otp_extractor.py，统一 import。
- **FR-007**: OAuth client_id/redirect_uri/audience/auth0Client 收敛到 services/constants.py，全库无散落硬编码。
- **FR-008**: verify 截图必须落 data/debug/，目录不存在自动创建（不再静默吞错）。
- **FR-009**: 提交推送 GitHub Release v2.1，附 HTML 变更报告（含上下文/直觉/做了什么/测验）。
- **FR-010**: 整理为可复用工作流 + skill，记忆与规则同步更新。

### Success Criteria
- **SC-001**: `pytest tests/ -q` 全绿，覆盖率不降于 69%（上轮基线）。
- **SC-002**: CF 重试资源安全测试通过（mock new_context 抛异常不泄漏）。
- **SC-003**: 任务表落库测试通过（mock run_batch，断言 UPDATE 发生）。
- **SC-004**: 去重 O(1) 测试通过（mock 100 email，get_accounts 只调 1 次）。
- **SC-005**: OTP 三合一测试通过（同封邮件三 service 结果一致）。
- **SC-006**: HTML 报告含 6+ 题测验，答案可校验。
- **SC-007**: git log 含 v2.1 提交，gh release v2.1 存在。

## Non-Goals
- H3 浏览器池复用（需独立会话深度做，本次沉淀到 tasks）
- F 系列前端全面升级（同上）
- G1 token 保鲜后台任务（同上）
- 真实跑注册（无账号，降级为 mock 证据 + 边界披露）
