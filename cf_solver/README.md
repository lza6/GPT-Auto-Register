# CF Solver（Cloudflare 挑战求解子服务）

> 本项目内置的 Cloudflare / WAF 挑战求解子服务，基于 FastAPI + camoufox（异步浏览器）。
> 源自上游 Boterdrop-Solver（印尼语），已适配本项目：端口、启动方式、代理、配置均已本地化。
> 上游参考：SGAHSCAJASCJ/Turnstile-Solver · verfired8975/recaptcha-v3-solver · najibyahya/Boterdrop-Solver

## 这是什么

注册主链路遇到 Cloudflare 人机验证（Turnstile / cf_clearance）时，主服务会调用本子服务
来自动求解。它是**独立进程**，通过 HTTP 与主服务通信，端口 **8001**。

- 主服务调用方：`services/cf_solver_service.py`（健康检查 + 发起求解 + 轮询结果）
- 启动方式：由根目录 `启动.bat` 自动拉起（`cf_solver/boterdrop_wrapper.py`），**通常无需手动启动**
- 工作目录：`cf_solver/`（`boterdrop_wrapper.py` 以它为 cwd）

## 端口与契约

| 项 | 值 | 说明 |
|---|---|---|
| 端口 | **8001** | 主服务 `cf_solver_service.py:CF_SOLVER_PORT` 与健康检查均固定 8001 |
| 地址 | `http://127.0.0.1:8001` | 仅本机回环，不应对外暴露 |

> 注意：本 README 修正了上游原文的端口笔误（上游示例写的 8000，本项目实际用 **8001**）。

## 启动（一般无需手动）

`启动.bat` 会自动：检测依赖 → 启动 CF Solver（等待端口 8001 Listen，最长 40s）→ 启动主服务。
若 CF Solver 未起来，主服务遇 CF 挑战会降级为 `cf_blocked`，日志见 `logs/cf_solver.log`。

手动启动（调试时）：

```bash
# 在项目根目录，用主服务的 venv
./.venv/Scripts/python.exe cf_solver/boterdrop_wrapper.py
```

依赖随主服务 `requirements.txt` 一起安装（`camoufox[fetch]` 等），**不要**按上游原文
`pip install fastapi==0.95.2`（版本过旧且与本项目不符）。

## 配置（`cf_solver/config.json`）

```json
{
    "headless": true,
    "thread": 1,
    "page_count": 1,
    "proxy_support": true,
    "proxy_file": "../proxies.txt",
    "host": "127.0.0.1",
    "port": 8001,
    "debug": false,
    "cleanup_interval_minutes": 10
}
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `headless` | `true` | 无头浏览器（VPS 无 DISPLAY 时必须 true） |
| `thread` / `page_count` | `1` / `1` | 浏览器实例数 × 每实例页面数（受 RAM 限制，约 0.3GB/槽） |
| `proxy_support` | `true` | 是否用代理池（`proxy_file` 指向根目录 `proxies.txt`） |
| `port` | `8001` | 必须与主服务 `CF_SOLVER_PORT` 一致 |
| `cleanup_interval_minutes` | `10` | 强制清理浏览器内存的间隔（防小内存 VPS 泄漏） |

## API（异步任务 + 轮询）

先创建任务拿 `task_id`（202），再轮询 `GET /result?id=<task_id>` 直到 `success`/`error`。

| 任务 | 端点 | 必需参数 |
|---|---|---|
| Turnstile | `GET /turnstile` | `url`, `sitekey` |
| cf_clearance | `GET /clearance` | `url`, 可选 `timeout` |
| AWS WAF | `GET /aws-token` | `url`, 可选 `timeout` |
| reCAPTCHA v3 | `GET/POST /recaptchaV3` | `url`, `sitekey`, 可选 `action` |

示例（创建 + 轮询）：

```bash
curl "http://127.0.0.1:8001/clearance?url=https://target.cc/&timeout=30"
# → {"task_id":"...","status":"accepted"}
curl "http://127.0.0.1:8001/result?id=<task_id>"
# → 处理中 202；成功 200 {status:success, cf_clearance, cookies, user_agent}
```

状态码：`200` 成功 / `202` 处理中 / `404` task 过期 / `408` 超时 / `422` 求解失败。

## 与主服务的关系（排障）

- 主服务 `GET /api/healthz` 的 `cf_solver` 字段探测 8001 是否 Listen；`unknown` 表示未启动。
- CF Solver 宕机不阻塞主服务：注册遇 CF 会降级 `cf_blocked`，恢复后自动可用。
- 求解调试截图/快照落 `cf_solver/debug_logs/`。

## 许可

MIT — 见 [LICENSE](LICENSE)。
