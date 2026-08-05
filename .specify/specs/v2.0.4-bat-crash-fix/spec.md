# Feature Specification: 启动.bat 双击闪退修复 + 部署体验闭环

**Feature Branch**: `v2.0.4-bat-crash-fix`

**Created**: 2026-08-05

**Status**: Shipped (v2.0.4)

**Input**: 用户报告"双击 启动.bat 闪退"，要求修复并推送仓库创建发行版，且要求终局闭环审计、补漏、文档同步、整理成可复用工作流+skill。

## User Scenarios & Testing

### User Story 1 - 新用户双击启动 (Priority: P1)
新用户 clone 仓库后，在 Windows 资源管理器双击 `启动.bat`，期望弹出 cmd 窗口自动完成环境检测→venv→依赖→CF Solver→主服务，最终可访问 http://localhost:23457。

**Why this priority**: 这是用户首次接触项目的入口，闪退 = 用户流失 + 信任崩塌，生产部署者第一道卡。

**Independent Test**: 清空环境（杀残留 python）后双击 `启动.bat`，120s 内 23457 + 8001 均 Listen。

**Acceptance Scenarios**:
1. **Given** 系统已装 Python 3.11+ 与 Git for Windows，**When** 双击 `启动.bat`，**Then** cmd 窗口持续运行，2 分钟内 http://localhost:23457 可访问。
2. **Given** 上次启动残留进程占用 23457/8001，**When** 再次双击，**Then** bat 先杀残留再启动，不因端口占用崩。
3. **Given** camoufox 浏览器数据已就绪，**When** 启动，**Then** 不重复下载 ~900MB。

### User Story 2 - 部署者按文档拉起 (Priority: P2)
部署者按 README 拉起服务，遇到问题时能按排障段定位，无需问人。

**Why this priority**: 生产部署的"可排障性"决定运维成本。

**Independent Test**: 删掉 `.venv` 模拟新机，按 README 步骤+排障段操作可恢复。

**Acceptance Scenarios**:
1. **Given** README "快速开始" 段，**When** 部署者照做，**Then** 无隐藏前提。
2. **Given** 双击闪退（历史 bug），**When** 查 README 排障段，**Then** 能找到"编码劫持/GNU timeout"说明与解法。

### Edge Cases
- 中文路径 `C:\...\私单\GPT自动化注册的项目\` 下双击：cmd `%~dp0` 必须正确解析。
- 用户系统装了 Git for Windows，PATH 里 GNU `timeout.exe` 排在 System32 前。
- 用户系统代码页是 936（GBK）而非 65001：bat 不能依赖 chcp 65001 行先执行。
- 端口 8001 处于 Bound（非 Listen）状态：杀端口逻辑要覆盖。
- 首次运行无 `.venv`：venv 创建失败要 `goto :failed` 不闪退。
- camoufox 离线 zip 缺失且 GitHub 限流：降级提示，不阻塞主服务。

## Requirements

### Functional Requirements
- **FR-001**: `启动.bat` 双击必须在中文路径+GBK 代码页+GNU 工具劫持环境下正常启动，不闪退。
- **FR-002**: `停止.bat` 必须能停干净 23457/8001 两个端口的所有状态（Listen/Bound）进程。
- **FR-003**: 所有 `.bat` 脚本必须纯 ASCII（中文 rem/echo 译为英文），免疫代码页错位。
- **FR-004**: 所有 `timeout /t N` 必须替换为 `ping -n N+1 127.0.0.1`，免疫 GNU timeout 劫持。
- **FR-005**: `chcp` 必须用 `%SystemRoot%\System32\chcp.com` 绝对路径，免疫 PATH 劫持。
- **FR-006**: README 必须有"双击闪退"排障段，说明根因与解法。
- **FR-007**: 本轮变更必须有 ADR-004 记录决策背景。

### Key Entities
- **启动.bat**: 一键启动主服务+CF Solver 的入口脚本。
- **停止.bat**: 停止服务的入口脚本。
- **PATH 劫持**: Git for Windows 的 GNU 工具（timeout/findstr 等）排在 System32 前的现象。

## Success Criteria

### Measurable Outcomes
- **SC-001**: `os.startfile(启动.bat)` 模拟双击，120s 内 23457+8001 均 Listen（已验证 main=True cf=True）。
- **SC-002**: `启动.bat`/`停止.bat` 字节级纯 ASCII（非 ASCII 字节数 = 0，已验证）。
- **SC-003**: README 含"双击闪退"排障段。
- **SC-004**: ADR-004 落地，决策可追溯。

## Assumptions
- 用户使用 Windows 10/11，已装 Python 3.11+ 与 Git for Windows（PATH 含 GNU 工具）。
- camoufox 浏览器数据首次运行已通过 `install_camoufox.ps1` 安装就绪。
- 不覆盖 macOS/Linux（bat 是 Windows 专属，无跨平台需求）。
