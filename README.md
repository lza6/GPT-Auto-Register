# GPT 自动注册

批量注册 OpenAI（ChatGPT）账号的管理系统，带 **Web 控制台**。支持邮箱池管理、代理池管理、注册任务控制、账号密码导出、一键清空库，并可对接 [chatgpt2api](https://github.com/) 项目。

---

## ✨ 功能特性

| 功能 | 说明 |
|------|------|
| 🌐 **Web 控制台** | 一键管理界面，访问 `http://localhost:23457` |
| 📧 **邮箱池管理** | 从 91kami 网址批量导入；支持手动批量添加 `邮箱----密码----client_id----refresh_token`；逐条删除 / 清空 |
| 🤖 **注册引擎** | camoufox 浏览器自动化，邮箱 + OTP 验证码注册，自动填写姓名/年龄/生日，**自动设置 OpenAI 账号密码**，获取 access_token |
| 🛡️ **CF Solver** | 独立服务（端口 8001），Cloudflare 人机验证兜底 |
| 🛡️ **代理池** | 支持 kookeey 动态住宅代理（每账号独立 IP）+ 通用 HTTP 代理轮询，前端可编辑保存 |
| 📤 **一键导出账号密码** | 导出 `邮箱----OpenAI密码` 清单（按邮箱去重），也可导出 chatgpt2api 兼容格式 JSON |
| 🗑️ **一键清空库** | 导出后开始新一轮，清空注册记录/邮箱池/任务（保留日志），需输入 `clear` 确认 |
| 📝 **运行日志** | 实时查看注册过程日志，可清空 |
| ⚙️ **系统设置** | 邮箱源 URL、注册间隔、OTP 超时、批量数量、chatgpt2api 地址等 |

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
├── config.json              # 运行配置（勿提交，含密钥）
├── api/                     # FastAPI 路由
│   ├── register.py          # 注册任务 / 导出 / 清空 / chatgpt2api 对接
│   ├── emails.py            # 邮箱池（手动添加 / 删除 / 清空）
│   ├── proxies.py           # 代理池（读写 proxies.txt）
│   ├── settings.py          # 系统设置
│   ├── stats.py             # 统计
│   └── logs.py              # 日志
├── services/                # 核心服务
│   ├── browser_register.py  # camoufox 浏览器注册引擎
│   ├── register_engine.py   # 注册引擎（批量调度）
│   ├── email_service.py     # 邮箱验证码获取
│   ├── proxy_service.py     # 代理池（kookeey + 通用 HTTP）
│   ├── cf_solver_service.py # CF Solver 对接
│   └── db.py                # SQLite 数据库
├── cf_solver/               # CF 验证 solver（端口 8001）
├── web_dist/index.html      # 前端控制台
├── scripts/                 # 独立脚本（批量/对接 chatgpt2api）
└── data/                    # SQLite 数据库 + 日志（勿提交）
```

---

## ⚙️ 配置说明（config.json）

| 键 | 说明 | 默认 |
|----|------|------|
| `auth_key` | 控制台管理密钥（/api 接口鉴权，启用后前端设置页需填写） | - |
| `port` | 主服务端口 | `23457` |
| `proxy_file` | 代理池文件 | `proxies.txt` |
| `email_source_url` | 91kami 邮箱源 URL | - |
| `email_api_base` | 邮件 API 地址 | `https://app.98faka.top` |
| `register_interval_sec` | 注册间隔（秒） | `10` |
| `otp_wait_timeout_sec` | 验证码等待超时 | `600` |
| `batch_size` | 默认批量数量 | `100` |
| `use_oauth_pkce` | 注册时走 OAuth PKCE 获取 OpenAI refresh_token（长期续期） | `true` |
| `chatgpt2api_url` | chatgpt2api 地址 | `http://127.0.0.1:23456` |
| `chatgpt2api_admin_key` | chatgpt2api 管理密钥（留空自动读取） | - |

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
