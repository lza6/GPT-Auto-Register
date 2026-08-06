# GPT 自动注册

批量注册 OpenAI（ChatGPT）账号的管理系统，带 **Web 控制台**。支持邮箱池管理、代理池管理、注册任务控制、账号密码导出、一键清空库，并可对接 [chatgpt2api](https://github.com/) 项目。

---

## ✨ 功能特性

| 功能 | 说明 |
|------|------|
| 🌐 **Web 控制台** | 一键管理界面，访问 `http://localhost:23457` |
| 📧 **邮箱池管理** | 从 91kami 网址批量导入；支持手动批量添加 `邮箱----密码----client_id----refresh_token`；逐条删除 / 清空 |
| 🤖 **注册引擎** | **协议优先（curl_cffi + sentinel）→ 浏览器兜底（camoufox）**，自动设置 OpenAI 账号密码，获取 access_token + refresh_token 三件套（长期续期） |
| ⚡ **并发注册** | `register_concurrency` 并发批量，充分利用多核/多代理；暂停/继续/停止语义完整 |
| 🛡️ **CF Solver** | 独立服务（端口 8001），Cloudflare 人机验证兜底 |
| 🛡️ **代理池** | 支持 kookeey 动态住宅代理（每账号独立 IP）+ 通用 HTTP 代理轮询，前端可编辑保存 |
| 📤 **一键导出账号密码** | 导出 `邮箱----OpenAI密码` 清单（按邮箱去重），也可导出 chatgpt2api 兼容格式 JSON |
| 🔍 **搜索/筛选/分页** | 注册记录、邮箱池支持邮箱搜索、状态筛选、分页浏览 |
| 🌓 **主题切换** | 深色/浅色一键切换（跟随系统），移动端自适应布局 |
| 🗑️ **一键清空库** | 需输入 `clear` 确认，**清空前自动备份**到 `data/backups/`（保留最近 7 份） |
| 📊 **失败原因分类** | 批量任务失败按 风控/验证码超时/网络/服务器5xx/未知 分类统计，前端可量化瓶颈 |
| 📝 **运行日志** | 实时增量拉取（轮询降载），可清空，超过保留期自动清理 |
| ⚙️ **系统设置** | 邮箱源 URL、注册间隔、OTP 超时/轮询间隔、并发数、批量数量、邮件 API Base、UA、chatgpt2api 等 |

---

## 🚀 快速开始（Windows）

```bash
# 1. 双击启动
启动.bat

# 2. 打开控制台
#    http://localhost:23457   （主服务）
#    http://localhost:8001    （CF Solver，可选）
```

`启动.bat` 会自动完成：
1. 精准清理上次残留进程（仅按端口 23457 / 8001）
2. 定位 Python 3.11+（系统 / py launcher / 常见安装路径）
3. 创建虚拟环境 `.venv`（如不存在）
4. 安装依赖（fastapi、uvicorn、httpx、loguru、psutil、camoufox）
5. 检查前端 `web_dist/index.html`
6. 启动 CF Solver + 主服务

> 服务异常退出会自动重启（最多无限次，关闭窗口即停止）。

> **v2.0.4 起，双击 `启动.bat` 闪退问题已修复**。根因是 Windows cmd 在中文路径 + GBK 代码页下解析 UTF-8 中文 bat 字节错位，叠加 Git for Windows 的 GNU `timeout.exe` 劫持 PATH 导致 `timeout /t N` 报错。修复方案：bat 全文纯 ASCII + `ping` 替代 `timeout` + `%SystemRoot%\System32\chcp.com` 绝对路径。详见 [ADR-004](docs/ADR/ADR-004.md)。

### 🔧 双击启动排障

| 症状 | 原因 | 解决 |
|------|------|------|
| 双击 `启动.bat` 窗口一闪而过 | v2.0.3 及更早版本在中文路径 + GBK 代码页下中文字节错位 | 升级到 v2.0.4+（`git pull` 或下载最新 Release） |
| 双击后 cmd 报 `invalid time interval '/t'` | Git for Windows 的 GNU `timeout.exe` 排在 System32 前，劫持了 `timeout` 命令 | v2.0.4+ 已用 `ping` 替代；旧版可临时把 `C:\Windows\System32` 提到 PATH 最前 |
| 端口 23457/8001 起不来 | 上次进程残留占用端口 | 双击 `停止.bat`，或手动 `taskkill /F /IM python.exe` |
| CF Solver 8001 起不来 | camoufox 浏览器数据未就绪 | 查看 `logs/cf_solver.log`；首次运行把 `camoufox-*-win.x86_64.zip` 放 `tools\camoufox\` 后重试 |
| 主服务起来但访问 23457 报错 | 检查 `data/logs/server.log` 与 `logs/cf_solver.log` | 按 [ADR-004](docs/ADR/ADR-004.md) 排查 |
| Python 未找到 | 未装 Python 3.11+ | 从 https://www.python.org/downloads/ 安装，勾选 "Add to PATH" |

### 使用流程

1. **添加邮箱**：在「邮箱池」页点「✍️ 手动添加」，粘贴 `邮箱----密码----client_id----refresh_token`（每行一个）；或在设置里配置 91kami 邮箱源 URL 后点「📥 导入邮箱」
2. **配置代理**：在「代理池」页点「✏️ 编辑代理池」，粘贴代理（格式见下）
3. **开始注册**：点「▶️ 开始注册」，可暂停/继续/停止
4. **导出账号**：注册完成后点「📤 导出账号」，一键复制/下载账号密码清单
5. **新一轮**：点「🗑️ 一键清空库」，输入 `clear` 确认后重新开始

---

## 🛡️ 代理池格式

在「代理池」页编辑，每行一个代理，支持两种格式：

```
# kookeey 动态住宅代理（推荐，每账号独立 IP）
gate.kookeey.info:1000:用户ID-子用户:密码-国家

# 通用 HTTP 代理（按行轮询）
ip:port
ip:port:user:pass
http://ip:port
```

---

## 📂 目录结构

```
GPT-Auto-Register/
├── 启动.bat                 # 一键启动（Web 控制台 + CF Solver）
├── 停止.bat                 # 一键停止（按端口精准清理）
├── main.py                  # 服务入口
├── requirements.txt         # 依赖清单（唯一来源，启动.bat 据此安装）
├── config.json              # 运行配置（勿提交，含密钥）
├── api/                     # FastAPI 路由
│   ├── register.py          # 注册任务 / 导出 / 清空 / chatgpt2api 对接
│   ├── emails.py            # 邮箱池（手动添加 / 删除 / 清空 / 搜索分页）
│   ├── proxies.py           # 代理池（读写 proxies.txt）
│   ├── settings.py          # 系统设置（白名单校验）
│   ├── stats.py             # 统计（含失败分类）
│   └── logs.py              # 日志（增量拉取 / 保留清理）
├── services/                # 核心服务
│   ├── protocol_register.py # 纯协议注册引擎（curl_cffi + sentinel）
│   ├── browser_register.py  # camoufox 浏览器注册引擎（兜底）
│   ├── browser_selectors.py # 页面选择器集中管理（上游改版只改这一处）
│   ├── register_engine.py   # 注册引擎（并发批量调度 + 失败分类）
│   ├── sentinel.py          # OpenAI Sentinel PoW token 生成
│   ├── email_service.py     # 98faka 邮箱验证码获取
│   ├── graph_email_service.py # Microsoft Graph 取码（token LRU 缓存）
│   ├── imap_email_service.py # IMAP 取码
│   ├── proxy_service.py     # 代理池（kookeey + 通用 HTTP）
│   ├── proxy_chain.py       # 本地链式代理服务（kookeey 凭据从 proxies.txt 读取）
│   ├── cf_solver_service.py # CF Solver 对接
│   ├── login_detector.py    # 登录页形态检测 / CF 识别
│   ├── name_service.py      # 随机姓名生成
│   └── db.py                # SQLite 数据库（db_session 连接管理）
├── cf_solver/               # CF 验证 solver（端口 8001）
├── web_dist/index.html      # 前端控制台（搜索/分页/主题）
├── scripts/                 # 独立脚本（批量/对接 chatgpt2api）
│   └── archive/             # 已归档的旧版实验脚本
├── tests/                   # 测试套件（覆盖率 ≥70%）
├── 计划书/                   # 改进指南 / 规划文档
└── data/                    # SQLite 数据库 + 日志 + 备份（勿提交）
```

---

## ⚙️ 配置说明（config.json）

| 键 | 说明 | 默认 |
|----|------|------|
| `auth_key` | 控制台管理密钥（/api 接口鉴权，启用后前端设置页需填写） | - |
| `auth_enforced` | 强制鉴权：`true` 且 auth_key 为占位符时启动失败（fail-fast），杜绝"假安全" | `false` |
| `port` | 主服务端口 | `23457` |
| `proxy_file` | 代理池文件 | `proxies.txt` |
| `email_source_url` | 91kami 邮箱源 URL | - |
| `email_api_base` | 邮件 API 地址 | `https://app.98faka.top` |
| `register_concurrency` | 批量注册并发数 | `1` |
| `register_interval_sec` | 注册间隔（秒） | `10` |
| `otp_wait_timeout_sec` | 验证码等待超时 | `600` |
| `otp_poll_interval_sec` | 验证码轮询间隔 | `5` |
| `otp_min_age_window_sec` | 验证码时间过滤窗口（只取触发点 N 秒内到达的邮件） | `120` |
| `otp_fallback_after_sec` | 无新邮件多久后回看历史邮件 | `40` |
| `otp_backfill_window_min` | 回看历史邮件的窗口（分钟） | `15` |
| `batch_size` | 默认批量数量 | `100` |
| `use_oauth_pkce` | 注册时走 OAuth PKCE 获取 OpenAI refresh_token（长期续期） | `true` |
| `protocol_first` | 纯协议注册优先（curl_cffi + sentinel，无浏览器），失败自动降级浏览器 | `true` |
| `use_browser` | 是否允许浏览器兜底（camoufox）；`false` 时协议失败即停止 | `true` |
| `user_agent` | 自定义 User-Agent | Chrome UA |
| `log_retention_days` | 运行日志保留天数（超过自动清理） | `30` |
| `chatgpt2api_url` | chatgpt2api 地址 | `http://127.0.0.1:23456` |
| `chatgpt2api_admin_key` | chatgpt2api 管理密钥（留空自动读取） | - |
| `cf_retry_max` | CF 挑战解不开时换代理重试次数（0=不重试直接 cf_blocked） | `2` |

> **日志容量上限**（`services/db.py` 常量）：`MAX_LOG_ROWS=20000`，超量自动删旧，防单日高频爆量。无需配置。
> **CF Solver 端口**：固定 `8001`，随 `启动.bat` 自动拉起，`/api/healthz` 的 `cf_solver` 字段反映其状态。

---

## 🛡️ 注册方式：协议 vs 浏览器

系统采用 **协议为主、浏览器兜底** 策略：

| 方式 | 说明 | 触发 |
|------|------|------|
| **纯协议**（默认优先） | curl_cffi + sentinel 风控参数，**完全无浏览器**，快、省资源。见 `services/protocol_register.py` | `protocol_first=true`（默认） |
| **浏览器兜底** | camoufox 真实浏览器执行 JS，处理协议被拒的场景。见 `services/browser_register.py` | 协议失败（网络/5xx）且 `use_browser=true` |

**降级规则**：
- 协议成功 → 直接返回三件套（`access_token + refresh_token + id_token`）
- 协议失败（网络 / 5xx / 超时）且 `use_browser=true` → 自动降级浏览器注册
- 协议失败（风控：`account_deactivated` / `registration_disallowed` / 验证码限流）→ **不降级**，直接失败（浏览器同样会被拒，避免无谓重试触发限流）

**sentinel 来源**：协议注册所需的风控参数生成器已从 chatgpt2api 移植到本项目 `services/sentinel.py`，运行**不依赖** chatgpt2api 项目路径。独立批量协议脚本见 `scripts/revive_protocol.py`。

> 批量实测：`cd scripts && python revive_protocol.py --limit 10`（纯协议）或 `python revive_import.py`（浏览器）。

---

## 🛡️ 生产部署安全建议

1. **强制鉴权（推荐）**：`config.json` 设置真实 `auth_key` 并开启 `auth_enforced=true`。
   未设置真实密钥时服务**拒绝启动**（fail-fast），避免"看似有鉴权实际裸奔"。
   - 也可用环境变量注入，零改配置文件：
     ```bash
     set GPT_REGISTER_AUTH_KEY=你的真实密钥
     set GPT_REGISTER_AUTH_ENFORCED=1
     ```
2. **清空保护**：清空库需 `confirm='clear'` 服务端确认，且清空前自动备份到 `data/backups/backup_*.json`（保留最近 7 份）。
3. **凭据治理**：`chatgpt2api_admin_key` 从 `config.json` 或环境变量 `CHATGPT2API_ADMIN_KEY` 读取，**源码不含任何明文凭据**；
   kookeey 代理账密从 `proxies.txt` 自动解析。
4. **日志脱敏**：验证码不再明文写入日志；敏感配置对外掩码为 `******`（`auth_enforced` 布尔开关除外）。
5. **外部暴露**：若服务暴露公网，务必配置 Nginx 反向代理 + HTTPS + 限制 `/api/*` 来源，并设置真实 `auth_key`。
6. **健康检查**：部署探活用 `GET /api/healthz`（无需鉴权），返回 `db/cf_solver/browser_pool_size/version/auth` 状态。
   ```bash
   curl http://localhost:23457/api/healthz
   # {"status":"ok","db":"ok","cf_solver":"ok","browser_pool_size":0,"auth":"enabled","version":"3.1.0"}
   ```
   - `cf_solver=unknown` 表示 CF Solver(:8001) 未启动，注册遇 CF 挑战时会降级 `cf_blocked`。
   - `browser_pool_size` 为 0 正常（camoufox 按需启动，A5 浏览器池落地后改持久池）。

---

## 🔧 排障速查

| 现象 | 排查方向 |
|------|---------|
| 双击 `启动.bat` 闪退 | 见上「双击启动排障」；纯 ASCII + ping 替代 timeout + chcp 绝对路径 |
| `verify_account_login` 180s 超时强杀 | 现已形态分流早退；看 `data/refresh_results.jsonl` 的 `reason`：`cf_blocked`/`unknown_page`/`timeout` |
| 注册遇 CF 一直 `cf_blocked` | 换代理出口 IP（`proxies.txt`）；CF Solver(:8001) 是否 Listen |
| 页面改版 verify 返回 `unknown_page` | 看 `data/debug/verify_unknown_*.png` 截图 + 日志页面文字摘要，适配新选择器 `services/browser_selectors.py` |
| 日志表越来越大 | `log_retention_days` 配置保留天数（默认 30）；`MAX_LOG_ROWS=20000` 硬上限自动删旧 |
| SQLite `database is locked` | 并发写已用 `db_session` + WAL；若仍锁，降 `register_concurrency` |

---

## 🔗 对接 chatgpt2api

控制台「📤 导出账号」→「chatgpt2api 格式」会导出含 `access_token` 的 JSON，
可直接粘贴到 chatgpt2api 账号池，或在设置里配置 `chatgpt2api_url` / `chatgpt2api_admin_key` 后调用后端推送端点。

> 详见 `scripts/import_to_chatgpt2api.py`（命令行导入）与 `scripts/auto_register_import.py`（注册→导入闭环）。

---

## ❓ 常见问题

**Q: token 会过期吗？账号密码呢？**
- **账号密码（邮箱 + OpenAI 密码）长期有效**，可登录 OpenAI。新注册账号会自动设置 OpenAI 密码并存入数据库 `openai_password` 字段。
- 老账号若用 revive 流程注册过密码，密码在 `scripts/revive_passwords.txt`，可用脚本批量导入数据库。
- `access_token`（OpenAI JWT）会过期；`refresh_token` 可长期续期（chatgpt2api 自动刷新）。

**Q: 如何让 chatgpt2api 自动续期 token？**
注册流程通过 OAuth PKCE 在注册成功时获取 OpenAI 的 `access_token + refresh_token + id_token` 三件套，
chatgpt2api 导入后即可自动刷新。已有账号可用密码登录刷新（见下）。

**Q: 如何验证账号是否可用？**
`scripts/` 提供三个验证脚本：
- `verify_all_accounts.py`：批量监测所有账号（OpenAI token + 微软邮箱），API 方式并发快速
- `verify_account_login.py`：用账号密码登录 OpenAI，验证可用并刷新 token 三件套（`--all` 批量）
- `verify_microsoft_login.py`：用邮箱密码登录微软账户，验证邮箱账号可用

**Q: 清空库会删除什么？**
清空「注册记录 + 邮箱池 + 任务记录」，**保留运行日志**。请先导出账号密码备份。

---

## ⚠️ 免责声明

本项目仅供**学习与技术研究**。请遵守 OpenAI 及第三方平台的服务条款，用户须自行承担使用后果。
