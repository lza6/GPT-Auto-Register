# SOP 标准运维手册（Standard Operating Procedures）

> 面向部署/运维/交接。每个流程给出：目的 → 步骤 → 验证 → 排障 → 回滚。
> 原则：可照做、可验证、可回退。

## SOP-01 全新环境部署

**目的**：从 0 拉起服务。
**步骤**：
1. 装 Python 3.11+（勾选 Add to PATH）。
2. `git clone <repo>` → 进入目录。
3. 复制 `config.example.json` 为 `config.json`，按需改 `port`/`email_source_url`/`proxy_url` 等。
4. 双击 `启动.bat`。
**验证**：`curl http://localhost:<port>/api/healthz` 返回 `"status":"ok"`、`version` 为当前版本。
**排障**：双击闪退 → 见 README「双击启动排障」（纯 ASCII + ping + chcp 绝对路径）。CF Solver 8001 起不来 → 看 `logs/cf_solver.log`（camoufox 数据未下载）。
**回滚**：`git checkout <上一个 tag>`。

## SOP-02 开启鉴权（生产必做）

**目的**：防止接口裸奔。
**步骤**：二选一：
- config.json 设 `"auth_key": "<真实密钥>"` + `"auth_enforced": true`，重启。
- 或环境变量：`set GPT_REGISTER_AUTH_KEY=<密钥>` + `set GPT_REGISTER_AUTH_ENFORCED=1`。
**验证**：`curl http://localhost:<port>/api/stats/` 无头返回 401；带 `X-Auth-Key` 返回 200。前端遇 401 会弹窗收集密钥（存本机浏览器）。
**注意**：`auth_enforced=true` 且 auth_key 为占位符时**拒绝启动**（fail-fast），先设真实密钥。

## SOP-03 导入邮箱

**目的**：充实邮箱池（微软邮箱，用于收 OTP）。
**步骤**：控制台「邮箱池」→「✍️ 手动添加」粘贴 `邮箱----密码----client_id----refresh_token`（每行一个）；或设置页配 `email_source_url`（91kami）后点「📥 导入邮箱」。
**验证**：邮箱池列表出现条目，状态 `pending`；导入 toast 显示"新增 N / 跳过 M"。
**排障**：导入 0 条 → 检查 91kami URL 是否有效、格式是否为四段 `----` 分隔。

## SOP-04 批量注册

**步骤**：设置 `register_concurrency`（并发）/`register_interval_sec`（间隔）→「▶️ 开始注册」。可随时暂停/继续/停止。
**验证**：进度条推进；注册记录出现 `success` 条目（含 access_token）。
**排障**：
- 大量 `cf_blocked` → 换代理出口 IP；确认 CF Solver 8001 在线。
- 大量 `风控` → 失败占比 >40% 会自适应暂停，换代理后点「继续」。
- 大量 `验证码超时` → 检查邮件 API（email_api_base）或延长 `otp_wait_timeout_sec`。
**断点续跑**：中途停止/崩溃后，重启点「开始注册」会从 pending 邮箱继续（已成功的不重复）。

## SOP-05 导出 / 推送 chatgpt2api

**导出账号密码**：控制台「📤 导出账号」→「账号密码清单」得 `邮箱----OpenAI密码`；或「chatgpt2api 格式」得含 token 的 JSON。
**推送**：配置 `chatgpt2api_url` + `chatgpt2api_admin_key` →「推送 chatgpt2api」。
**验证**：chatgpt2api 账号池出现新账号。
**排障**：推送报"未配置密钥" → 设 `chatgpt2api_admin_key`（或读 chatgpt2api 自身 config）；报"没有可推送账号" → 成功账号的 refresh_token 可能已失效。

## SOP-06 Token 保鲜巡检（可选）

**目的**：后台定期用 refresh_token 换新 access_token（防过期）。
**步骤**：高级设置开 `token_refresh_enabled` + 设 `token_refresh_interval_sec`（默认 21600=6h）→ 重启。
**验证**：设置页「Token 保鲜巡检」卡片显示 上次巡检时间 + 扫描/刷新/失败数；或 `curl /api/stats/token-health`。
**边界**：默认关（避免与 chatgpt2api 自动刷新冲突）。巡检会把**轮换后的新 RT 一并落库**（防库存 RT 被 OpenAI 重用检测耗尽）。

## SOP-07 备份与恢复

**自动备份**：「一键清空库」前自动备份 accounts/emails/tasks 到 `data/backups/backup_*.json`（留最近 7 份）。
**手动备份**：复制 `data/register.db`。
**恢复**：停止服务 → 用备份的 register.db 覆盖 `data/register.db` → 重启。或从 backup JSON 人工导入。

## SOP-08 日志与排障定位

- 运行日志：控制台「📝 运行日志」（实时增量，页面隐藏时暂停拉取）；文件 `data/logs/server.log`（轮转 5MB×3）。
- 日志级别：`gpt-register` DEBUG、root INFO、httpx 等第三方 WARNING（防凭据落盘）。
- CF Solver 日志：`logs/cf_solver.log`。
- 排障速查表见 README「🔧 排障速查」。

## SOP-09 升级

1. `git pull`（或下载新 Release）。
2. 双击 `启动.bat`（自动装新依赖）。
3. `curl /api/healthz` 确认 version 为新版本。
**回滚**：`git checkout <旧 tag>` + 重启。数据库自动迁移（`ALTER TABLE ... IF NOT EXISTS` 幂等，旧库兼容）。

## SOP-10 安全基线检查单（交接时过一遍）

- [ ] `auth_key` 非占位符 + `auth_enforced=true`
- [ ] config.json / proxies.txt / data/ 未提交 git
- [ ] 服务不直接暴露公网（或已套 Nginx + HTTPS + 限源）
- [ ] `tls_verify=true`（除非代理做 SSL 拦截）
- [ ] 备份机制已知（data/backups/）
