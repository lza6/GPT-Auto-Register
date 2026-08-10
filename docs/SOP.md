# SOP 标准运维手册（Standard Operating Procedures）

> 面向部署/运维/交接。每个流程给出：目的 -> 步骤 -> 验证 -> 排障 -> 回滚。
> 原则：可照做、可验证、可回退。

---

## 目录

- [SOP-01 项目概述与环境要求](#sop-01-项目概述与环境要求)
- [SOP-02 全新环境部署（Windows）](#sop-02-全新环境部署windows)
- [SOP-03 服务器部署（Docker）](#sop-03-服务器部署docker)
- [SOP-04 配置说明（config.json 全字段）](#sop-04-配置说明configjson-全字段)
- [SOP-05 开启鉴权（生产必做）](#sop-05-开启鉴权生产必做)
- [SOP-06 导入邮箱](#sop-06-导入邮箱)
- [SOP-07 配置代理](#sop-07-配置代理)
- [SOP-08 批量注册](#sop-08-批量注册)
- [SOP-09 导出与推送 chatgpt2api](#sop-09-导出与推送-chatgpt2api)
- [SOP-10 一键补齐 Token](#sop-10-一键补齐-token)
- [SOP-11 Token 保鲜巡检（可选）](#sop-11-token-保鲜巡检可选)
- [SOP-12 备份与恢复](#sop-12-备份与恢复)
- [SOP-13 日志与排障定位](#sop-13-日志与排障定位)
- [SOP-14 更新升级步骤](#sop-14-更新升级步骤)
- [SOP-15 安全基线检查单](#sop-15-安全基线检查单)
- [SOP-16 常见问题排查（FAQ）](#sop-16-常见问题排查faq)
- [SOP-17 独立脚本工具](#sop-17-独立脚本工具)
- [SOP-18 API 接口速查](#sop-18-api-接口速查)
- [附录A 目录结构](#附录a-目录结构)
- [附录B 代理格式详解](#附录b-代理格式详解)
- [附录C 注册流程原理](#附录c-注册流程原理)

---

## SOP-01 项目概述与环境要求

### 项目概述

GPT 自动注册是一个批量注册 OpenAI（ChatGPT）账号的管理系统，带 Web 控制台。核心功能：

- 从邮箱服务商（91kami）批量导入微软邮箱
- 自动完成 OpenAI 注册流程（协议优先，浏览器兜底）
- 获取 OAuth 三件套（access_token + refresh_token + id_token），长期有效
- 对接 chatgpt2api 项目，自动推送账号
- 一账号一 IP + 一账号一 TLS 指纹，防风控

### 环境要求

| 项目 | 要求 |
|------|------|
| Python | 3.11+（推荐 3.12） |
| 操作系统 | Windows 10/11（本地部署）或 Linux（服务器 Docker） |
| 内存 | 最低 2GB（推荐 4GB+） |
| 磁盘 | 最低 5GB 可用空间 |
| 网络 | 可访问 OpenAI/Google 等境外服务（需代理） |
| 浏览器引擎 | camoufox（首次启动自动下载，约 500MB） |

### 对外端口

| 端口 | 服务 | 说明 |
|------|------|------|
| 23457 | 主服务（FastAPI + Web 控制台） | 可配置（config.json port） |
| 8001 | CF Solver（camoufox 浏览器引擎） | 固定端口，主服务自启子进程 |

---

## SOP-02 全新环境部署（Windows）

### 目的

从 0 在 Windows 机器上拉起 GPT 自动注册服务。

### 步骤

**1. 安装 Python 3.11+**

```batch
:: 从 https://www.python.org/downloads/ 下载安装
:: 必须勾选 "Add Python to PATH"
:: 验证安装
python --version
```

**2. 克隆仓库**

```bash
git clone <仓库地址>
cd "GPT自动化注册的项目"
```

**3. 准备配置文件**

```bash
copy config.example.json config.json
```

**4. 编辑 config.json**

按需修改关键配置（详见 SOP-04）：
- `email_source_url`：91kami 邮箱源 URL
- `auth_key`：管理密钥（生产环境必设）
- `port`：服务端口（默认 23457）

**5. 准备代理文件**

编辑 `proxies.txt`，每行一个代理（详见附录B）。

**6. 启动服务**

双击 `启动.bat`，或命令行运行：

```batch
启动.bat
```

启动脚本会自动完成：
1. 清理端口 23457/8001 的残留进程
2. 定位 Python 3.11+（系统路径 / py launcher / 常见安装路径）
3. 创建虚拟环境 `.venv`
4. 从 `requirements.txt` 安装依赖
5. 检查/安装 camoufox 浏览器数据
6. 启动 CF Solver（端口 8001）
7. 启动主服务（端口 23457）

### 验证

```bash
curl http://localhost:23457/api/healthz
```

预期返回（JSON）：

```json
{"status":"ok","db":"ok","cf_solver":"ok","browser_pool_size":0,"auth":"enabled","version":"3.3.0"}
```

- `status: "ok"` 表示服务正常
- `cf_solver: "ok"` 表示 CF 人机验证服务正常
- `version` 为当前版本号（与 Release 一致）

打开浏览器访问 `http://localhost:23457` 看到 Web 控制台界面。

### 排障

| 现象 | 排查方向 |
|------|----------|
| 双击 `启动.bat` 窗口一闪而过 | 见 README「双击启动排障」；v2.0.4+ 已修复，用 ping 替代 timeout 防止 GNU 工具劫持 |
| 端口 23457 起不来 | 上次进程残留 → 双击 `停止.bat`，或手动 `taskkill /F /IM python.exe` |
| CF Solver 8001 起不来 | 查看 `logs/cf_solver.log`；camoufox 浏览器数据未下载 |
| Python 未找到 | 未装 Python 3.11+，或未勾选 "Add to PATH" |
| 依赖安装失败 | 检查网络，手动重试：`.venv\Scripts\python.exe -m pip install -r requirements.txt` |
| 启动后页面访问报错 | 检查 `data/logs/server.log` 中的错误信息 |

### 回滚

```bash
git checkout <上一个版本号>
:: 然后双击 启动.bat 重启
```

---

## SOP-03 服务器部署（Docker）

### 目的

在 Linux 服务器上通过 Docker 部署服务（与 chatgpt2api 同机部署）。

### 步骤

**1. 服务器准备**

```bash
# 安装 Docker + Docker Compose
apt-get install docker.io docker-compose-plugin
```

**2. 准备配置文件**

```bash
cp config.example.json config.json
# 编辑 config.json：
#   - 设置强 auth_key
#   - 设置 chatgpt2api_url=http://host.docker.internal:23456
#   - 配置 email_source_url
```

**3. 准备代理文件**

编辑 `proxies.txt`，填入 kookeey 动态住宅代理（每行一条）。

**4. 构建并启动**

```bash
docker compose up -d
```

### 验证

```bash
curl http://localhost:23457/api/healthz
# 或从外部访问：http://服务器IP:23457
```

### Docker Compose 配置说明

```yaml
services:
  register:
    build: .
    image: gpt-register:local
    container_name: gpt-register
    restart: unless-stopped
    ports:
      - "23457:23457"
    stop_grace_period: 20s
    init: true
    volumes:
      - ./data:/app/data          # 持久化数据库 + 日志
      - ./config.json:/app/config.json
      - ./proxies.txt:/app/proxies.txt
    environment:
      - GPT_REGISTER_PORT=23457
```

### 关键说明

- **单容器内**：主服务（23457）按需自启 CF Solver 子进程（camoufox 过 Cloudflare），无需单独编排
- **数据持久化**：`data/` 目录挂载 volume，重启不丢失数据库和日志
- **对接 chatgpt2api**：compose 中设 `extra_hosts` 或用 `host.docker.internal` 访问宿主机 chatgpt2api

### 回滚

```bash
docker compose down
git checkout <上一个版本号>
docker compose up -d --build
```

---

## SOP-04 配置说明（config.json 全字段）

### 文件位置

项目根目录 `config.json`（从 `config.example.json` 复制，**不提交 git**）。

### 配置字段说明

#### 核心配置

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `auth_key` | string | `""` | 控制台管理密钥。`/api` 接口鉴权用，前端遇 401 自动弹窗收集。占位符值（`"请修改为你的管理密钥"` / `"change-me"`）视为未配置 |
| `auth_enforced` | bool | `false` | 强制鉴权开关。`true` 且 auth_key 为占位符时服务拒绝启动（fail-fast），杜绝"假安全" |
| `port` | int | `23457` | 主服务端口。也受环境变量 `GPT_REGISTER_PORT` 覆盖 |
| `proxy_file` | string | `"proxies.txt"` | 代理池文件路径 |

#### 邮箱配置

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `email_source_url` | string | `""` | 91kami 邮箱源 URL。格式：`https://mai.91kami.com/cpd/{token}.aspx` |
| `email_api_base` | string | `"https://app.98faka.top"` | 邮件 API 地址（取件/取码用） |

#### 注册参数

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `register_concurrency` | int | `1` | 批量注册并发数。每并发消耗一个代理出口，建议与代理数量匹配 |
| `register_interval_sec` | int | `10` | 每账号注册间隔（秒）。间隔越大越不易被风控，但整体速度下降 |
| `otp_wait_timeout_sec` | int | `600` | 验证码等待超时（秒）。邮件 API 延迟高时需加大 |
| `otp_poll_interval_sec` | int | `5` | 验证码轮询间隔（秒） |
| `otp_min_age_window_sec` | int | `120` | 验证码时间过滤窗口，只取轮询开始前 N 秒内到达的邮件 |
| `otp_fallback_after_sec` | int | `40` | 无新邮件多久后回看历史邮件（秒） |
| `otp_backfill_window_min` | int | `15` | 回看历史邮件的窗口（分钟） |
| `batch_size` | int | `100` | 前端批量数量默认值 |
| `user_agent` | string | Chrome UA | 自定义 User-Agent。留空则自动跟随 TLS 指纹生成配套 UA |

#### 代理配置

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `use_proxy` | bool | `true` | 是否启用代理池。`false` 时直连（所有账号同 IP，易风控） |
| `proxy_url` | string | `""` | 固定代理 URL。非空时所有账号共用此代理（代理池不生效），优先级高于代理池 |

#### 注册方式

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `protocol_first` | bool | `true` | 纯协议注册优先（curl_cffi + sentinel），失败自动降级浏览器 |
| `use_browser` | bool | `true` | 是否允许浏览器兜底（camoufox）。`false` 时协议失败即停止 |
| `use_oauth_pkce` | bool | `true` | 注册时走 OAuth PKCE 获取 OpenAI refresh_token（长期续期）。建议始终开启 |

#### 导出 / 对接 chatgpt2api

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `token_output_file` | string | `"已经获取到的token.txt"` | 成功注册的 access_token 追加写入文件 |
| `email_output_file` | string | `"获取到的所有邮箱.txt"` | 成功注册的邮箱追加写入文件 |
| `chatgpt2api_url` | string | `"http://127.0.0.1:23456"` | chatgpt2api 服务地址 |
| `chatgpt2api_admin_key` | string | `""` | chatgpt2api 管理密钥。留空自动读取 chatgpt2api 自身 config.json |

#### 运维配置

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `log_retention_days` | int | `30` | 运行日志保留天数，超过自动清理 |
| `tls_verify` | bool | `true` | 出站 HTTPS 请求 TLS 证书校验。SSL 拦截代理环境设 `false` 回退 |
| `cf_retry_max` | int | `2` | CF 挑战解不开时换代理重试次数。0=不重试直接 cf_blocked |

#### 高级配置

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `browser_pool_size` | int | `0` | 浏览器实例池大小。0=不池化按需启停。仅固定通用 HTTP 代理场景才开，kookeey 动态代理池化会串 IP |
| `token_refresh_enabled` | bool | `false` | 启用 token 保鲜巡检（后台定期刷新 access_token）。默认关避免与 chatgpt2api 冲突 |
| `token_refresh_interval_sec` | int | `21600` | token 巡检间隔（秒，最小 60，默认 6 小时） |
| `tls_fingerprint` | string | `""` | 固定 TLS 指纹（空=池内随机）。调试用，空则每次注册随机选一个 |
| `tls_fingerprint_pool` | string | `""` | 自定义 TLS 指纹池（逗号分隔）。空=默认 Chrome 池 |

#### 告警配置（v3.4+）

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `notify_webhook_url` | string | `""` | Webhook 告警地址（支持钉钉/企业微信/Bark/Server酱） |
| `notify_events` | array | `["batch_done","high_fail_rate","proxy_pool_empty","cf_solver_dead"]` | 告警事件列表 |

### 环境变量覆盖

以下环境变量优先级高于 config.json（零改配置注入）：

| 环境变量 | 覆盖配置项 |
|----------|-----------|
| `GPT_REGISTER_AUTH_KEY` | auth_key |
| `GPT_REGISTER_AUTH_ENFORCED` | auth_enforced |
| `GPT_REGISTER_PORT` | port |
| `GPT_REGISTER_TOKEN_REFRESH_ENABLED` | token_refresh_enabled |

### 配置校验

启动时自动校验 config.json 类型，发现数字被存为字符串等问题会打印告警（不阻断启动）：

```
[config] 检测到 2 项配置问题（warn-only，不阻断启动）：
  - register_concurrency: 期望 int，实际 str("true")。布尔开关建议直接写 true/false。
  - register_interval_sec: 期望 int，实际 str("15")。数字被存为字符串，建议改为 int 类型。
```

---

## SOP-05 开启鉴权（生产必做）

### 目的

防止 `/api/*` 接口被未授权访问，避免凭据泄露。

### 步骤

**方法一：配置文件（推荐）**

```json
{
  "auth_key": "your-real-secret-key-here",
  "auth_enforced": true
}
```

修改后重启服务。

**方法二：环境变量注入（零改配置）**

```bash
set GPT_REGISTER_AUTH_KEY=your-real-secret-key-here
set GPT_REGISTER_AUTH_ENFORCED=1
启动.bat
```

### 验证

```bash
# 不带密钥 → 401
curl http://localhost:23457/api/stats/
# 返回 {"detail":"未授权：缺少或错误的 X-Auth-Key"}

# 带密钥 → 200
curl -H "X-Auth-Key: your-real-secret-key-here" http://localhost:23457/api/stats/
# 返回正常数据
```

前端遇 401 会自动弹窗收集密钥，输入一次后存于本机浏览器 localStorage。

### 注意

- `auth_enforced=true` 且 auth_key 为占位符时服务**拒绝启动**（fail-fast），先设真实密钥
- 健康检查接口 `/api/healthz` 和前端静态资源不受鉴权影响
- 敏感配置（auth_key、password、secret 结尾的键）在前端返回时掩码为 `******`

---

## SOP-06 导入邮箱

### 目的

充实邮箱池（微软邮箱，用于接收 OpenAI 验证码）。

### 方式一：91kami 自动导入

**前置条件**：config.json 中配置了 `email_source_url`（91kami 卡密链接）。

**步骤**：
1. 打开 Web 控制台 `http://localhost:23457`
2. 进入「设置」页面
3. 在「邮箱源 URL」粘贴 91kami 链接（格式：`https://mai.91kami.com/cpd/{token}.aspx`）
4. 点击「保存」
5. 回到「邮箱池」页面，点击「📥 导入邮箱」

**验证**：页面 toast 显示"新增 N / 跳过 M"；邮箱池列表出现条目，状态为 `pending`。

### 方式二：手动批量添加

**步骤**：
1. 打开 Web 控制台「邮箱池」页面
2. 点击「✍️ 手动添加」
3. 在文本框中粘贴邮箱数据，格式为：

```
邮箱1----密码1----client_id1----refresh_token1
邮箱2----密码2----client_id2----refresh_token2
```

每行一条，`----` 分隔四段。

4. 点击「添加」

### 邮箱格式说明

| 字段 | 说明 | 来源 |
|------|------|------|
| 邮箱 | 微软邮箱地址 | 91kami 购买 |
| 密码 | 微软邮箱密码 | 91kami 购买 |
| client_id | Microsoft OAuth 应用 ID | 91kami 购买 |
| refresh_token | 微软邮箱 OAuth 刷新令牌 | 91kami 购买 |

### 排障

| 现象 | 排查方向 |
|------|----------|
| 导入 0 条 | 91kami URL 无效或已过期；格式不是四段 `----` 分隔 |
| 导入提示跳过 | 邮箱已存在（去重）；格式不对 |
| 自动导入按钮灰色 | config.json 未设置 `email_source_url` |

---

## SOP-07 配置代理

### 目的

配置代理池，使注册请求使用不同出口 IP，降低被 OpenAI 风控的概率。

### 步骤

1. 打开 Web 控制台「代理池」页面
2. 点击「✏️ 编辑代理池」
3. 粘贴代理内容，每行一个（格式见附录B）
4. 点击「保存」
5. 可选：点击「🔍 探测健康」测试代理可用性

### 代理格式

支持两种格式：

**kookeey 动态住宅代理（推荐，每账号独立 IP）**

```
gate.kookeey.info:1000:用户ID-子用户:密码-国家
```

每次连接自动生成随机 session，对应全新出口 IP。

**通用 HTTP 代理（按行轮询）**

```
ip:port
ip:port:user:pass
http://ip:port
http://user:pass@ip:port
```

### 出口 IP 策略

| 配置 | 行为 |
|------|------|
| `proxy_url` 非空 | 所有账号共用该固定代理（优先级最高，代理池不生效） |
| `proxy_url` 空 + `use_proxy=true` | 每账号从代理池取下一个：kookeey 动态生成新 session = 新 IP；通用 HTTP 按行轮询 |
| `use_proxy=false` | 直连（所有账号同一 IP，**不建议**批量注册） |

**关键**：单账号全流程（authorize -> 取码 -> 换 token）固定同一出口 IP，不中途换 IP。

### 代理健康探测

点击「🔍 探测健康」后，系统会：
1. TCP 连通性探测（3s 超时）
2. 真实 HTTP 出口探测（经代理请求 ipify 获取出口 IP + 国家）
3. 返回每条的延迟、出口 IP、国家、可用状态

结果以徽章形式展示：
- ✅ 绿色：可用，显示出口 IP 和国家
- ❌ 红色：不可用，显示原因

### 风控代理黑名单

v3.4+：注册被风控（risk_control）时，系统自动将该代理加入黑名单（TTL 30 分钟），黑名单内的代理不会被后续注册使用。

---

## SOP-08 批量注册

### 前置条件

- [ ] 邮箱池已有待注册邮箱（状态为 `pending`）
- [ ] 代理池已配置有效代理
- [ ] CF Solver 端口 8001 已启动（`/api/healthz` 的 `cf_solver` 为 `ok`）

### 步骤

1. 打开 Web 控制台
2. 在「仪表盘」或「注册控制」区域设置参数：
   - **并发数**：建议 `1` 起步，逐步增加（受代理数量限制）
   - **注册间隔**：建议 `10-30` 秒，间隔越大越不易风控
   - **批量数量**：本次注册的邮箱数量（0=全部待处理）
3. 点击「▶️ 开始注册」
4. 注册过程中可随时：
   - **⏸️ 暂停**：暂停后当前正在注册的账号完成后停止，可点「继续」恢复
   - **▶️ 继续**：恢复暂停的注册任务
   - **⏹️ 停止**：停止注册任务，已完成的账号保留，未开始的邮箱保持 pending

### 验证

- 进度条实时推进
- 注册记录出现 `success` 条目（含 access_token）
- 运行日志实时显示每步操作

### 失败分类

系统自动将失败原因分类统计：

| 类型 | 含义 | 处理建议 |
|------|------|----------|
| `risk_control` | 风控失败 | 更换代理出口 IP |
| `otp_timeout` | 验证码超时 | 检查邮件 API 或延长 `otp_wait_timeout_sec` |
| `network` | 网络失败 | 检查代理可用性与 `email_api_base` |
| `server_5xx` | 服务器 5xx | OpenAI 服务抖动，稍后重试 |
| `cf_blocked` | CF 挑战未通过 | 检查 CF Solver 状态，换代理 |
| `unknown` | 未知失败 | 查看运行日志定位 |

### 自适应暂停机制

- **连续 3 次 server_5xx**：自动暂停 60 秒后恢复
- **风控失败占比 >40%**：自动暂停，提示用户更换代理，手动点「继续」恢复
- **注册被风控的代理自动拉黑**：TTL 30 分钟，黑名单内不会被复用

### 断点续跑

中途停止/崩溃后，重启服务并点「开始注册」会从 `pending` 邮箱继续（已成功的账号自动跳过，不会重复注册）。

### 排障

| 现象 | 排查方向 |
|------|----------|
| 大量 `cf_blocked` | 换代理出口 IP；确认 CF Solver 8001 在线 |
| 大量 `risk_control` | 失败占比 >40% 会自适应暂停，换代理后点「继续」 |
| 大量 `otp_timeout` | 检查邮件 API（email_api_base）或延长 `otp_wait_timeout_sec` |
| 一直 `pending` 不开始 | 检查是否有正在运行的任务；检查代理是否可用 |
| 注册极慢 | 检查 `register_concurrency` 并发设置；检查代理延迟 |

---

## SOP-09 导出与推送 chatgpt2api

### 导出账号密码清单

**Web 控制台**：操作控制 -> 「📤 导出账号」 -> 「账号密码清单」

输出格式：
```
邮箱1----OpenAI密码1
邮箱2----OpenAI密码2
```

按邮箱去重，OpenAI 密码优先（可登录 OpenAI 平台），无则回退微软邮箱密码。

### 导出 chatgpt2api 格式 JSON

**Web 控制台**：操作控制 -> 「📤 导出账号」 -> 「chatgpt2api 格式」

输出为 chatgpt2api 兼容的 JSON，含 access_token / refresh_token / id_token / 邮箱 / 密码 / mail_credential。

导出时会自动用 refresh_token 刷新换取最新 access_token（并发刷新，上限 5 个），轮换后的新 refresh_token 自动写回数据库。

### 推送 chatgpt2api（一键）

**前置条件**：
- config.json 中设置了 `chatgpt2api_url`（默认 `http://127.0.0.1:23456`）
- 设置了 `chatgpt2api_admin_key`（chatgpt2api 管理密钥）

**步骤**：
1. Web 控制台操作控制 -> 「🚀 推送 chatgpt2api」
2. 系统自动刷新所有账号的 token 并推送

**验证**：chatgpt2api 账号池出现新账号。

### 推送到 chatgpt2api 的字段映射

| 导出字段 | chatgpt2api 用途 |
|----------|-----------------|
| `access_token` / `refresh_token` / `id_token` | OAuth 三件套，直接使用 + 自动刷新 |
| `email` | 账号邮箱 |
| `password` | **OpenAI 账号密码**（凭据重登用，非微软邮箱密码） |
| `mail_credential` | `{client_id, refresh_token(微软邮箱)}`，chatgpt2api 凭据过期后 OTP 重登取码用 |

### 排障

| 现象 | 排查方向 |
|------|----------|
| 推送报"未配置密钥" | 设置 `chatgpt2api_admin_key` |
| 报"没有可推送账号" | 成功账号的 refresh_token 可能已失效 |
| 导出账号列表为空 | 没有 `success` 状态的账号 |

---

## SOP-10 一键补齐 Token

### 目的

为「有 openai_refresh_token 但缺有效 access_token」的账号刷新补齐。连 refresh_token 都没有的账号会如实标为 `need_reregister`。

### 步骤

Web 控制台 -> 操作控制 -> 「🔧 一键补齐Token」

### 原理

1. 扫描所有 `success` / `success_no_token` 状态的账号
2. 筛选出有 openai_refresh_token 但 access_token 缺失或无效的账号
3. 用 refresh_token 调 OpenAI OAuth 端点换取新三件套
4. 轮换后的新 refresh_token 自动落库（防库存 RT 被 OpenAI 重用检测耗尽）
5. 并发刷新，上限 5 个

### 验证

返回结果中显示：
- `scanned`：扫描总数
- `replenished`：成功补齐数
- `failed`：刷新失败数
- `need_reregister`：没有 refresh_token 需重新注册的数

---

## SOP-11 Token 保鲜巡检（可选）

### 目的

后台定期自动用 refresh_token 换取新 access_token，防止 token 过期。

### 步骤

1. 打开 Web 控制台「设置」页面
2. 开启「Token 保鲜巡检」开关
3. 设置巡检间隔（默认 21600 秒 = 6 小时）
4. 保存后重启服务

或直接修改 config.json：

```json
{
  "token_refresh_enabled": true,
  "token_refresh_interval_sec": 21600
}
```

### 验证

- 设置页「Token 保鲜巡检」卡片显示上次巡检时间 + 扫描/刷新/失败数
- 或调用 API：`curl /api/stats/token-health`

### 说明

- **默认关闭**：避免与 chatgpt2api 的自动刷新冲突
- **巡检会把轮换后的新 RT 一并落库**：OpenAI 每次刷新会轮换 refresh_token，老旧 RT 重用数次后作废，不落库新 RT 会导致库存 RT 逐步耗尽
- **崩溃自愈**：巡检任务异常退出时自动重启（带退避，5s -> 10s -> ... -> 300s 封顶）
- **防重入**：扫描慢于 interval 时不会叠加新扫描

---

## SOP-12 备份与恢复

### 自动备份

**触发条件**：「一键清空库」操作前自动备份。

**备份内容**：accounts（注册记录）、emails（邮箱池）、tasks（任务记录）。

**备份位置**：`data/backups/backup_YYYYMMDD_HHMMSS.json`

**保留策略**：保留最近 7 份，更旧的自动删除。

### 手动备份

```bash
# 停止服务后复制数据库文件
copy data\register.db data\register.db.backup
```

### 恢复

**从数据库文件恢复**：

```bash
# 停止服务 → 覆盖数据库 → 重启
停止.bat
copy data\register.db.backup data\register.db
启动.bat
```

**从备份 JSON 恢复**：

备份 JSON 文件可直接查看，但恢复需通过 API 手动导入（Web 控制台「邮箱池」->「手动添加」粘贴邮箱数据）。

### 数据位置

| 数据 | 位置 | 说明 |
|------|------|------|
| SQLite 数据库 | `data/register.db` | 账号、邮箱、任务、日志、设置 |
| Token 文件 | `已经获取到的token.txt` | 成功注册的 access_token 追加记录 |
| 日志文件 | `data/logs/server.log` | 轮转日志（5MB x 3） |
| CF Solver 日志 | `logs/cf_solver.log` | CF 服务日志 |
| 备份文件 | `data/backups/backup_*.json` | 清空库前的自动备份 |
| 代理黑名单 | `data/proxy_blacklist.json` | 持久化代理黑名单 |

---

## SOP-13 日志与排障定位

### 日志查看方式

**Web 控制台**：点击「📝 运行日志」，实时增量拉取（SSE 推送，1 秒间隔）。页面隐藏时自动暂停拉取。

**SSE 日志流**：`GET /api/logs/stream`（Server-Sent Events，失败 3 次自动回退轮询）

**文件日志**：
- `data/logs/server.log`：主服务日志（轮转 5MB x 3）
- `logs/cf_solver.log`：CF Solver 日志

### 日志级别

| Logger | 级别 | 说明 |
|--------|------|------|
| `gpt-register` | DEBUG | 详细的注册流程日志 |
| root | INFO | 全局日志，不含第三方库 DEBUG |
| httpx / httpcore / urllib3 / playwright / asyncio | WARNING | 第三方 HTTP/浏览器库，防凭据/请求体落盘 |

### 日志容量

- `MAX_LOG_ROWS = 20000`（`services/db.py` 常量），超量自动删最旧
- `log_retention_days` 配置保留天数，启动时/定时任务清理过期日志

### 排障速查

| 现象 | 排查方向 |
|------|----------|
| 双击 `启动.bat` 闪退 | 见 README「双击启动排障」；v2.0.4+ 已修复 |
| 注册遇 CF 一直 `cf_blocked` | 换代理出口 IP；CF Solver(:8001) 是否 Listen |
| 页面改版 verify 返回 `unknown_page` | 看 `data/debug/verify_unknown_*.png` 截图 |
| 日志表越来越大 | `log_retention_days` 配置；`MAX_LOG_ROWS=20000` 硬上限 |
| SQLite `database is locked` | 降 `register_concurrency`；WAL 模式已启用 |
| 验证码一直取不到 | 检查 `email_api_base` 是否可达；检查邮箱 OAuth 凭据是否过期 |
| 代理探测全部失败 | 代理 IP 可能被封；检查 `proxies.txt` 格式 |

### 调试文件

- `data/debug/verify_result_debug.png`：验证流程调试截图
- 各种 `debug_*_{email前缀}.png`：注册失败的页面截图

---

## SOP-14 更新升级步骤

### 版本升级

**步骤**：

```bash
# 1. 拉取最新代码
git pull
# 或下载最新 Release 包解压覆盖

# 2. 停止服务
停止.bat

# 3. 启动服务（自动安装新依赖 + 自动迁移数据库）
启动.bat
```

**验证**：

```bash
curl http://localhost:23457/api/healthz
# 确认 version 为新版本号
```

### 数据库迁移

所有数据库迁移（ALTER TABLE）设计为幂等操作：

```sql
-- 示例：新增字段，已存在的表不会报错
ALTER TABLE accounts ADD COLUMN openai_refresh_token TEXT;
```

执行多次安全，不会重复创建或报错。旧库直接升级兼容。

### 回滚

```bash
git checkout <上一个版本号/标签>
停止.bat
启动.bat
```

### 变更日志

查看 `workflow_status.md` 或 Release Notes 了解版本变更详情。

---

## SOP-15 安全基线检查单

### 交接时逐一确认

- [ ] `auth_key` 已设为真实密钥，非占位符
- [ ] `auth_enforced` 已开启（`true`）
- [ ] config.json 不在 git 跟踪中（`.gitignore` 已忽略）
- [ ] proxies.txt 不在 git 跟踪中
- [ ] data/ 目录不在 git 跟踪中
- [ ] 服务不直接暴露公网（或已通过 Nginx 反向代理 + HTTPS + 限制来源）
- [ ] `tls_verify=true`（除非代理做 SSL 中间人拦截）
- [ ] 备份机制已知（`data/backups/` 自动备份）
- [ ] 日志脱敏已确认（验证码不明文写入日志；敏感配置掩码为 `******`）
- [ ] kookeey 代理凭据/chatgpt2api 管理密钥等敏感信息已妥善保管

### 安全功能说明

| 安全措施 | 说明 |
|----------|------|
| 鉴权 | `auth_key` + `X-Auth-Key` 请求头，前端 401 弹窗收集密钥 |
| fail-fast | `auth_enforced=true` 时占位符密钥拒绝启动，杜绝假安全 |
| 配置掩码 | 敏感配置（key/password/secret 结尾）前端返回 `******` |
| TLS 校验 | 出站请求默认校验证书，防 MITM |
| 日志脱敏 | 验证码不再明文写入日志；第三方库日志压到 WARNING |
| 清空保护 | 需 `confirm='clear'` 确认，清空前自动备份 |
| 配置白名单 | settings API 只允许修改白名单内的键，禁止覆盖 auth_key 等 |
| 限流防护 | API 参数 limit 收敛安全范围（默认 100，上限 500） |

---

## SOP-16 常见问题排查（FAQ）

### Q1: 双击 `启动.bat` 闪退/窗口一闪而过

**原因**：v2.0.3 及更早版本在中文路径 + GBK 代码页下中文字节错位。

**解决**：
- 升级到 v2.0.4+（`git pull` 或下载最新 Release）
- 临时方案：把 `C:\Windows\System32` 提到 PATH 最前

### Q2: 端口 23457/8001 起不来

**原因**：上次进程残留占用端口。

**解决**：
```bash
双击 停止.bat
# 或手动
taskkill /F /IM python.exe
```

### Q3: 注册全部返回 `cf_blocked`

**原因**：Cloudflare 人机验证无法自动通过。

**排查**：
1. 检查 CF Solver 是否正常运行（`/api/healthz` 的 `cf_solver` 字段）
2. 检查 `logs/cf_solver.log` 是否有错误
3. 更换代理出口 IP
4. 调整 `cf_retry_max` 增加重试次数

### Q4: 注册大量 `otp_timeout`

**原因**：验证码邮件未及时到达或 API 无法获取。

**排查**：
1. 检查 `email_api_base` 配置是否可达
2. 检查邮箱的 OAuth 凭据（client_id + refresh_token）是否过期
3. 延长 `otp_wait_timeout_sec`（默认 600 秒）
4. 检查 98faka 邮件 API 服务状态

### Q5: 注册大量 `risk_control`

**原因**：OpenAI 风控拦截。

**排查**：
1. 更换代理出口 IP（kookeey 动态住宅每个 session 新 IP）
2. 降低 `register_concurrency` 并发数
3. 增加 `register_interval_sec` 间隔
4. 确认出口 IP 国家与浏览器时区一致（v3.4+ 自动处理）

### Q6: SQLite `database is locked`

**原因**：并发写入过多。

**解决**：降低 `register_concurrency`。数据库已启用 WAL 模式 + busy_timeout（5s），正常情况下不会锁。

### Q7: 验证码一直取不到

**原因**：邮件 API 异常或邮箱凭据过期。

**排查**：
1. 检查 `data/logs/server.log` 中邮件 API 的请求状态
2. 手动测试邮件 API 是否可达
3. 检查邮箱的 refresh_token 是否过期
4. 如果邮箱凭据过期，需要重新从 91kami 获取

### Q8: 推送 chatgpt2api 报错

**排查**：
1. 确认 `chatgpt2api_url` 配置正确
2. 确认 `chatgpt2api_admin_key` 已配置
3. 检查 chatgpt2api 服务是否正常运行
4. 查看 `data/logs/server.log` 中的推送错误详情

### Q9: 清空库后想恢复数据

**检查**：`data/backups/` 目录下是否有备份文件。清空前自动备份 accounts/emails/tasks 数据。

**恢复**：手动从备份 JSON 提取邮箱数据，通过 Web 控制台「手动添加」重新导入。

### Q10: Docker 部署后页面能访问但注册全失败

**排查**：
1. 检查容器内代理是否可达（Docker 容器需要能访问代理服务器）
2. 检查 `chatgpt2api_url` 配置，容器内访问宿主机需用 `host.docker.internal`
3. 检查 volume 挂载的 `proxies.txt` 是否在容器内正确读取
4. 查看容器日志：`docker logs gpt-register`

---

## SOP-17 独立脚本工具

### 脚本位置

`scripts/` 目录下提供多个独立脚本：

| 脚本 | 用途 | 用法 |
|------|------|------|
| `verify_all_accounts.py` | 批量验证所有账号（OpenAI token + 微软邮箱） | `python scripts/verify_all_accounts.py` |
| `verify_account_login.py` | 用账号密码登录 OpenAI 验证 | `python scripts/verify_account_login.py --all` |
| `verify_microsoft_login.py` | 验证微软邮箱账号可用 | `python scripts/verify_microsoft_login.py` |
| `refresh_all.py` | 批量刷新所有 token | `python scripts/refresh_all.py`（或双击 `run_refresh.bat`） |
| `revive_protocol.py` | 纯协议批量注册（不依赖 Web 控制台） | `python scripts/revive_protocol.py --limit 10` |
| `revive_import.py` | 浏览器批量注册（不依赖 Web 控制台） | `python scripts/revive_import.py` |
| `import_to_chatgpt2api.py` | 导入账号到 chatgpt2api | `python scripts/import_to_chatgpt2api.py` |
| `auto_register_import.py` | 自动注册+导入 chatgpt2api（全自动） | `python scripts/auto_register_import.py` |
| `sync_to_chatgpt2api.py` | 同步账号到 chatgpt2api | `python scripts/sync_to_chatgpt2api.py` |
| `smoke_browser_pool.py` | 浏览器池真实 camoufox 复用冒烟测试 | `python scripts/smoke_browser_pool.py` |
| `smoke_token_refresh.py` | Token 巡检真实刷新冒烟测试 | `python scripts/smoke_token_refresh.py` |

### 批量刷新 Token

双击 `run_refresh.bat` 或命令行运行：

```bash
python scripts/refresh_all.py
```

### 验证账号

```bash
# 验证全部账号
python scripts/verify_all_accounts.py

# 验证单个账号登录
python scripts/verify_account_login.py --email user@example.com
```

---

## SOP-18 API 接口速查

### 基础信息

| 项目 | 值 |
|------|-----|
| 基础 URL | `http://localhost:23457` |
| 鉴权方式 | `X-Auth-Key` 请求头 |
| 无需鉴权 | `/api/healthz`、前端静态资源 |

### 健康检查

```http
GET /api/healthz
```

返回：`{"status":"ok","db":"ok","cf_solver":"ok","browser_pool_size":0,"auth":"enabled","version":"3.3.0"}`

### 统计

```http
GET /api/stats/                              # 全量统计
GET /api/stats/token-health                  # Token 巡检健康度
GET /api/stats/account-readiness             # c2api 就绪率（四维：AT/RT/密码/取件凭证）
```

### 邮箱管理

```http
GET    /api/emails/?status=pending&limit=100&offset=0&search=   # 邮箱列表（分页/搜索/筛选）
GET    /api/emails/pending?limit=100                             # 待注册邮箱
GET    /api/emails/platforms                                      # 平台列表
GET    /api/emails/platform-map/{email}                           # 邮箱在各平台使用情况
POST   /api/emails/manual-add                                    # 手动添加邮箱
DELETE /api/emails/{id}                                           # 删除单个邮箱
POST   /api/emails/clear                                         # 清空邮箱池
```

### 注册控制

```http
POST   /api/register/import-emails                   # 从 91kami 导入邮箱
POST   /api/register/start                           # 开始注册
POST   /api/register/control                         # 暂停/继续/停止
GET    /api/register/status                           # 注册状态
GET    /api/register/accounts                         # 账号列表（分页/搜索/筛选）
GET    /api/register/accounts/export                  # 导出 access_token
POST   /api/register/retry-failed                     # 重试失败账号
POST   /api/register/export-accounts                  # 导出 chatgpt2api 格式
POST   /api/register/export-credentials               # 导出账号密码清单
POST   /api/register/push-chatgpt2api                 # 推送 chatgpt2api
POST   /api/register/replenish-tokens                 # 一键补齐 Token
POST   /api/register/clear                            # 清空数据（需 confirm='clear'）
```

### 代理管理

```http
GET    /api/proxies/        # 获取代理池内容
POST   /api/proxies/        # 保存代理池
GET    /api/proxies/health  # 批量探测代理可用性
```

### 设置

```http
GET  /api/settings/        # 获取所有配置
POST /api/settings/        # 修改配置（key + value）
GET  /api/settings/config  # 获取原始 config.json
```

### 日志

```http
GET  /api/logs/        # 日志列表（分页）
GET  /api/logs/stream  # SSE 实时日志推送
POST /api/logs/clear   # 清空日志
```

---

## 附录A 目录结构

```
GPT-Auto-Register/
├── 启动.bat                    # 一键启动（Web 控制台 + CF Solver）
├── 停止.bat                    # 一键停止（按端口精准清理）
├── run_refresh.bat             # 批量刷新 token（调用 scripts/refresh_all.py）
├── main.py                     # 服务入口（FastAPI 应用）
├── requirements.txt            # 依赖清单（唯一来源）
├── config.json                 # 运行配置（勿提交，含密钥）
├── config.example.json         # 配置示例（不含敏感信息）
├── proxies.txt                 # 代理池文件
├── Dockerfile                  # Docker 构建文件
├── docker-compose.yml          # Docker Compose 编排
├── install_camoufox.ps1        # Camoufox 浏览器引擎安装脚本
│
├── api/                        # FastAPI 路由层
│   ├── __init__.py             # 应用工厂 + 鉴权中间件 + 路由注册
│   ├── register.py             # 注册任务 / 导出 / 清空 / chatgpt2api 对接
│   ├── emails.py               # 邮箱池 CRUD（手动添加/删除/清空/搜索分页）
│   ├── proxies.py              # 代理池读写 + 健康探测
│   ├── settings.py             # 系统设置（白名单校验 + 类型转换）
│   ├── stats.py                # 统计（含失败分类 + token 健康度 + c2api 就绪率）
│   └── logs.py                 # 日志查询 + SSE 实时推送
│
├── services/                   # 核心业务层
│   ├── register_engine.py      # 注册引擎（并发批量调度 + 失败分类 + 自适应暂停）
│   ├── register_base.py        # 双引擎统一契约（Protocol 鸭子类型）
│   ├── protocol_register.py    # 纯协议注册引擎（curl_cffi + sentinel，无浏览器）
│   ├── browser_register.py     # 浏览器注册引擎（camoufox 兜底，含浏览器池接入）
│   ├── browser_pool.py         # 浏览器实例池（LRU + 代理绑定，默认不池化）
│   ├── browser_selectors.py    # 页面选择器集中管理（上游改版只改这一处）
│   ├── email_service.py        # 98faka 邮箱验证码服务
│   ├── graph_email_service.py  # Microsoft Graph 取码（token LRU 缓存）
│   ├── imap_email_service.py   # IMAP 直连取码（备用）
│   ├── otp_extractor.py        # 验证码提取公共模块（font/bg/黑名单三合一）
│   ├── sentinel.py             # OpenAI Sentinel PoW Token 生成
│   ├── login_detector.py       # 登录页形态检测 / CF 识别
│   ├── name_service.py         # 随机姓名/生日生成
│   ├── proxy_service.py        # 代理池服务（kookeey + 通用 HTTP + 黑名单）
│   ├── proxy_chain.py          # 本地链式代理服务
│   ├── token_refresher.py      # Token 保鲜巡检（后台定期刷新 + 崩溃自愈）
│   ├── cf_solver_service.py    # CF Solver 对接（启动/停止/健康检查/token 获取）
│   ├── config_schema.py        # config.json 类型校验（warn-only）
│   ├── constants.py            # OpenAI OAuth 常量 + tls_verify_enabled
│   ├── notifier.py             # Webhook 告警推送（钉钉/企业微信/Bark）
│   └── db.py                   # SQLite 数据库（db_session 连接管理 + 异步日志批量落库）
│
├── cf_solver/                  # CF 人机验证服务
│   ├── boterdrop_wrapper.py    # Boterdrop-Solver 封装
│   └── api_server.py           # CF Solver API 服务
│
├── web_dist/                   # 前端控制台（三个文件，无构建工具）
│   ├── index.html              # 页面结构
│   ├── app.js                  # 交互逻辑（搜索/分页/主题/鉴权/仪表盘/SSE 日志）
│   └── app.css                 # 样式（深色/浅色主题变量）
│
├── scripts/                    # 独立工具脚本
│   ├── verify_all_accounts.py  # 批量验证账号
│   ├── verify_account_login.py # 账号密码登录验证
│   ├── verify_microsoft_login.py # 微软邮箱验证
│   ├── refresh_all.py          # 批量刷新 token
│   ├── revive_protocol.py      # 纯协议批量注册
│   ├── revive_import.py        # 浏览器批量注册
│   ├── import_to_chatgpt2api.py    # 导入到 chatgpt2api
│   ├── auto_register_import.py     # 自动注册+导入
│   ├── sync_to_chatgpt2api.py      # 同步到 chatgpt2api
│   ├── smoke_browser_pool.py       # 浏览器池冒烟测试
│   ├── smoke_token_refresh.py      # Token 巡检冒烟测试
│   └── archive/                    # 已归档旧版实验脚本
│
├── tests/                      # 测试套件
│   └── fixtures/               # 测试夹具（OpenAI API 响应快照）
│
├── tools/                      # 工具目录
│   └── camoufox/               # 离线浏览器引擎安装包存放处
│       └── README.md
│
├── data/                       # 运行时数据（勿提交 git）
│   ├── register.db             # SQLite 数据库
│   ├── logs/server.log         # 主服务日志（轮转）
│   ├── backups/                # 清空库前的自动备份
│   ├── proxy_blacklist.json    # 风控代理黑名单
│   ├── debug/                  # 调试截图
│   └── archive/                # 归档数据
│
├── docs/                       # 文档
│   ├── SOP.md                  # 标准运维手册（本文档）
│   ├── ADR/                    # 架构决策记录
│   ├── GOLDEN_EXAMPLES.md      # 测试用例示例
│   ├── VALIDATION_RECORDS.md   # 验证记录
│   └── ONBOARDING.md           # 新手上手指南
│
└── 计划书/                     # 改进指南 / 规划文档
```

---

## 附录B 代理格式详解

### kookeey 动态住宅代理（推荐）

**格式**：

```
gate.kookeey.info:1000:用户ID-子用户:密码-国家
```

**说明**：
- 每次 `get_next()` 生成随机 8 位数字 session
- 每个 session 对应一个全新的美国住宅出口 IP
- 不同账号 = 不同随机 session = 不同出口 IP（实测三账号出口 IP 各不相同）
- 推荐用于批量注册，实现真·一账号一 IP

**示例**：

```
gate.kookeey.info:1000:u12345-user:pass123-US
gate.kookeey.info:1000:u12345-user:pass123-JP
gate.kookeey.info:1000:u67890-user:pass456-GB
```

### 通用 HTTP 代理

**格式**：

```
# 方式一：IP + 端口
192.168.1.1:8080

# 方式二：IP + 端口 + 用户名 + 密码
192.168.1.1:8080:user:pass

# 方式三：完整 URL（http/https）
http://192.168.1.1:8080
http://user:pass@192.168.1.1:8080

# 方式四：完整 URL + socks5
socks5://user:pass@192.168.1.1:1080
```

**说明**：
- 按行轮询（round-robin），每个账号使用池中下一个代理
- 不支持动态换 IP，多个账号可能用同一出口（取决于代理本身）

### 代理策略选择

| 场景 | 推荐代理类型 | 配置 |
|------|-------------|------|
| 批量注册（大量账号） | kookeey 动态住宅 | `proxy_url` 留空 + `use_proxy=true` |
| 少量注册/调试 | 固定 HTTP 代理 | `proxy_url="http://..."` |
| 服务器部署 | kookeey 动态住宅 | `proxy_url` 留空 + `use_proxy=true` |
| 不代理（直连） | 无 | `use_proxy=false` |

---

## 附录C 注册流程原理

### 协议注册流程（protocol_first=true）

```
1. Authorize 请求（确定账号形态：新号 / 半成品）
   ↓
2. create-account 分支（新号）
   ├── user/register → 设置 OpenAI 密码
   └── email-otp/send → 触发 OTP 发送
   ↓
   log-in 分支（半成品/已存在）
   └── passwordless/send-otp → 触发 OTP 发送
   ↓
3. 等待验证码邮件（轮询 98faka API）
   ↓
4. email-otp/validate → 提交验证码
   ↓
5. about-you → create_account → 填写姓名/生日
   ↓
6. OAuth code → 换三件套（access_token + refresh_token + id_token）
```

### 浏览器注册流程（协议失败兜底）

```
1. 启动 camoufox 浏览器
   ↓
2. 访问 OAuth authorize 页面（或 chatgpt.com 登录页）
   ↓
3. 检测页面形态（邮箱输入 / 密码 / CF 挑战 / 验证码 / about-you）
   ↓
4. 解 CF 挑战（自动点 Turnstile 复选框 + 调 CF Solver 兜底）
   ↓
5. 输入邮箱 → 点击继续
   ↓
6. 等待 OTP 验证码（Graph API 读取）
   ↓
7. 输入验证码 → 提交
   ↓
8. 填写姓名/生日 → 提交
   ↓
9. 捕获 OAuth code → 换三件套
   ↓
10. 回退：从 session API 获取 JWT token
```

### 一账号一 IP 一指纹

```
每个账号注册流程：
  1. 从代理池取下一个代理（kookeey 动态生成随机 session = 新 IP）
  2. 从 TLS 指纹池随机选一个指纹（chrome120/chrome123/...）
  3. 生成配套 User-Agent
  4. 全流程（authorize → 取码 → 换 token）使用同一 IP + 同一指纹
  5. 不同账号 = 不同 IP + 不同指纹 → 双隔离（网络层 + 传输层）
```

### 失败降级规则

```
协议成功 → 直接返回三件套
协议失败（网络/5xx/超时）且 use_browser=true → 自动降级浏览器注册
协议失败（风控/deactivated/registration_disallowed/限流）→ 不降级，直接失败
  （浏览器同样会被拒，避免无谓重试触发限流）
```

### OAuth PKCE 三件套

```
authorize_code (一次性) + code_verifier
    ↓
OAuth Token 端点
    ↓
├── access_token (JWT，短期有效，用于 API 调用)
├── refresh_token (长期有效，可续期换新 access_token)
└── id_token (JWT，用户身份信息)
```

**注意**：OpenAI 每次刷新会**轮换** refresh_token 旧值。不落库新的 refresh_token 会导致库存 RT 逐步耗尽。