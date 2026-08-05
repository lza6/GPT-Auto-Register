# Plan: 启动.bat 双击闪退修复 + 部署体验闭环

**Feature**: v2.0.4-bat-crash-fix
**Created**: 2026-08-05
**Status**: Shipped

## 架构/技术约束
- 启动脚本必须 Windows cmd 原生兼容，不依赖任何 Unix 工具。
- 编码策略：纯 ASCII（字节级 0 非 ASCII），任何代码页下解析一致。
- 命令调用策略：`%SystemRoot%\System32\<cmd>` 绝对路径，免疫 PATH 劫持。
- 定时策略：`ping -n N+1 127.0.0.1` 替代 `timeout /t N`（Windows 原生，免疫 GNU 劫持）。
- 不引入新依赖、不改后端业务逻辑（宪法：增量迭代、拒绝重构）。

## 阶段划分

### Phase 1: 闪退根因定位（已完成）
- 复现：`os.startfile` 真实双击模拟（CreateProcessW 传中文 cwd 会乱码，必须 ShellExecuteW）。
- 根因双重：① UTF-8 无 BOM 含中文 → cmd 按 GBK 读取中文字节错位 → `cd /d "%~dp0"` 失败 → rc=255；② GNU `timeout.exe` 劫持 PATH → `timeout /t N` 报 `invalid time interval '/t'`。

### Phase 2: 修复落地（已完成）
- `启动.bat` 全文转纯 ASCII（中文 rem/echo 译英文）。
- `timeout /t N /nobreak >nul` → `ping -n N+1 127.0.0.1 >nul`（3 处：L29/L137/L170）。
- `chcp 65001` → `%SystemRoot%\System32\chcp.com 65001`。
- `停止.bat` 同步修复 chcp。

### Phase 3: 真实验证（已完成）
- `os.startfile` 双击模拟：main=True cf=True，端口 23457/8001 均 Listen。
- `cf_solver.log` 产生新日志，浏览器引擎初始化成功（Pool siap: 1 halaman）。
- 字节级验证：非 ASCII = 0。

### Phase 4: 治理资产补建（进行中）
- `.specify/specs/v2.0.4-bat-crash-fix/` spec/plan/tasks。
- `docs/ADR/ADR-004.md`。
- README 排障段补充。
- workflow_status.md 增量更新。

### Phase 5: 工作流+skill 整理（待子代理回报后）
- 提取本轮"bat 编码劫持修复"为可复用 skill。
- 记忆点标注：下次改 bat 类脚本优先读 ADR-004 + 本 spec。

## 数据模型
无数据模型变更（纯脚本层修复）。

## 技术约束
- 不改后端 Python 代码（上轮 190 项测试全绿，本轮未动业务逻辑）。
- 不引入跨平台抽象（bat 是 Windows 专属）。
- 修复必须可回滚：纯 ASCII + ping 是最保守方案，无副作用。
