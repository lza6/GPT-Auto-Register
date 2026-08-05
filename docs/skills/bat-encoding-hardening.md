---
name: bat-encoding-hardening
description: Windows .bat 启动脚本编码与命令劫持防御。当编写/修改/审查 Windows .bat 启动脚本时使用——防止双击闪退、中文路径乱码、GNU 工具 PATH 劫持。项目 ADR-004 的可复用封装。
---

# bat 编码与命令劫持防御

> 本 skill 是 [ADR-004](../ADR/ADR-004.md) 的可复用封装。下次改任何 `.bat` 脚本前先读这条。
> 项目内 Claude Code 用户：本地副本在 `.claude/skills/bat-encoding-hardening/SKILL.md`（自动加载）。

## 何时用

- 编写新的 Windows `.bat` 启动脚本
- 修改现有 `.bat`（尤其是双击入口：`启动.bat`/`停止.bat`/`run_*.bat`）
- 审查 `.bat` 脚本双击闪退问题
- 跨项目移植 Windows 启动脚本

## 三条硬规则（不可违反）

### 1. 纯 ASCII
所有 `.bat` 脚本**字节级纯 ASCII**。中文 `rem`/`echo`/`title` 一律译为英文。
- 理由：cmd 按系统默认代码页（中文 Windows 为 GBK/936）读取 bat 字节流，中文字节被当 GBK 多字节字符首字节与后续字节错位组合，破坏 bat 词法，导致 `cd /d "%~dp0"` 失败 → rc=255 闪退。
- 验证：`sum(1 for x in open(p,'rb').read() if x>127) == 0`

### 2. Windows 原生命令 + 绝对路径
| 危险写法 | 安全写法 | 理由 |
|---------|---------|------|
| `timeout /t N /nobreak >nul` | `ping -n N+1 127.0.0.1 >nul` | GNU coreutils `timeout.exe`（Git for Windows）劫持 PATH，`/t` 被吃报错 |
| `chcp 65001 >nul` | `%SystemRoot%\System32\chcp.com 65001 >nul` | 免疫 PATH 劫持 |
| `findstr ...` | `%SystemRoot%\System32\findstr.exe ...` | 同上（可选，findstr 劫持较少见） |
| `taskkill /F /PID X` | `%SystemRoot%\System32\taskkill.exe /F /PID X` | 同上 |

`ping -n N+1` 实际等 N 秒（N+1 个包间隔 1 秒，首包立即发），与 `timeout /t N` 对齐。

### 3. 杀端口覆盖 Bound 状态
```bat
for /f "tokens=5" %%P in ('netstat -ano ^| findstr LISTENING ^| findstr /c:":%PORT% "') do (
    taskkill /f /pid %%P >nul 2>nul
)
```
只杀 LISTENING 不够——端口可能处于 Bound（TIME_WAIT/半开）。补：
```bat
for /f "tokens=5" %%P in ('%SystemRoot%\System32\netstat.exe -ano ^| findstr /c:":%PORT% "') do (
    taskkill /f /pid %%P >nul 2>nul
)
```
去掉 LISTENING 过滤，覆盖所有占用端口的进程。

## 真实双击模拟方法

**不要用** `subprocess.Popen(["cmd","/c",bat], cwd=...)` 测双击——CreateProcessW 传中文 cwd 会乱码。
**不要用** `cmd /c "带空格的中文路径"` ——shell 切词。
**要用**：
```python
import os
os.startfile(r"C:\path\启动.bat", "open")  # ShellExecuteW，等同真实双击
```
然后轮询 `socket.connect(("127.0.0.1", port))` 判断服务起来。

## 检查清单

改完 `.bat` 自查：
- [ ] 文件非 ASCII 字节数 = 0
- [ ] 无 `timeout /t`，全用 `ping -n`
- [ ] `chcp` 用 `%SystemRoot%\System32\chcp.com`
- [ ] 杀端口覆盖 Bound 状态（去 LISTENING 过滤）
- [ ] `cd /d "%~dp0"` 在最前（工作目录锁定）
- [ ] `:failed` 分支有 `pause`（让用户看到错误，不闪退）
- [ ] `setlocal EnableExtensions EnableDelayedExpansion` 开启

## 反例（v2.0.3 及更早，已废弃）

```bat
@echo off
chcp 65001 >nul                          ❌ 中文 bat + chcp 被劫持
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title GPT 自动注册                        ❌ 中文 title，GBK 下乱码
...
timeout /t 1 /nobreak >nul               ❌ GNU timeout 劫持
```

## 正例（v2.0.4+）

```bat
@echo off
rem Pure ASCII, immune to codepage mismatch
%SystemRoot%\System32\chcp.com 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title GPT Auto Register
...
ping -n 2 127.0.0.1 >nul
```
