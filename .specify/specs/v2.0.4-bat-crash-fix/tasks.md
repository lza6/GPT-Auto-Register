# Tasks: 启动.bat 双击闪退修复 + 部署体验闭环

**Feature**: v2.0.4-bat-crash-fix
**Created**: 2026-08-05

## Phase 2: 修复落地

- [x] T1: `启动.bat` 全文转纯 ASCII（中文 rem/echo 译英文） — `启动.bat`
- [x] T2: `启动.bat` `timeout /t N /nobreak >nul` → `ping -n N+1 127.0.0.1 >nul`（L29/L137/L170） — `启动.bat`
- [x] T3: `启动.bat` `chcp 65001` → `%SystemRoot%\System32\chcp.com 65001` — `启动.bat`
- [x] T4: `停止.bat` 同步修复 chcp — `停止.bat`
- [P] [x] T5: 字节级验证纯 ASCII（非 ASCII = 0） — 已验证

## Phase 3: 真实验证

- [x] T6: `os.startfile` 双击模拟 → main=True cf=True — 已验证
- [x] T7: 端口 23457/8001 均 Listen — 已验证
- [x] T8: `cf_solver.log` 产生新日志 — 已验证

## Phase 4: 治理资产补建

- [x] T9: spec.md — `.specify/specs/v2.0.4-bat-crash-fix/spec.md`
- [x] T10: plan.md — `.specify/specs/v2.0.4-bat-crash-fix/plan.md`
- [x] T11: tasks.md（本文件）
- [x] T12: ADR-004.md — `docs/ADR/ADR-004.md`
- [x] T13: README "双击闪退"排障段 — `README.md`
- [x] T14: workflow_status.md 增量更新 — `workflow_status.md`

## Phase 5: 工作流+skill 整理

- [x] T15: 提取"bat 编码劫持修复"可复用 skill — `.claude/skills/bat-encoding-hardening/SKILL.md` + `docs/skills/bat-encoding-hardening.md`
- [x] T16: 记忆点标注（下次改 bat 优先读 ADR-004） — memory
- [x] T17: HTML 变更报告 v2.0.4 — `docs/CHANGE_REPORT_v2.0.4.html`
- [x] T18: 验收测验（quiz） — 嵌入 HTML 报告

## 覆盖映射
- FR-001 → T1/T2/T3/T6/T7
- FR-002 → T4
- FR-003 → T1/T5
- FR-004 → T2
- FR-005 → T3/T4
- FR-006 → T13
- FR-007 → T12
- SC-001 → T6/T7
- SC-002 → T5
- SC-003 → T13
- SC-004 → T12
