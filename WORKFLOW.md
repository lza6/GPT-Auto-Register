# GPT 自动注册 — 正式项目工作流（合并后唯一系统）

> 本目录 `开发完毕的GPT自动化注册的项目` 是**唯一正式注册系统**（原 `GPT自动注册` 已归档）。
> 含：Web 控制台(:23457) + CF 自动过(cf_solver/:8001) + 批量注册脚本 + chatgpt2api 导入。

## 一、两个运行入口

| 入口 | 用途 | 启动 |
|------|------|------|
| **Web 控制台** | 手动看板/单号注册 | `启动.bat`（主服务:23457 + CF Solver:8001） |
| **批量脚本** | 一文件 N 账号批量注册+导入 | `scripts/revive_import.py` |

## 二、批量注册（推荐）

```
输入卡密 → scripts/本次一百个号.txt （格式：邮箱----密码----client_id----M.Cxxx）
运行      → cd scripts && python revive_import.py
产出      → scripts/revive_passwords.txt（邮箱----密码）
            scripts/revive_results.jsonl（每账号状态）
导入      → 自动 POST :23456/api/accounts（含 email+password，可长期自动重登）
```

### 并发分片（多进程）
```bash
python revive_import.py --from 1 --to 33 --proxy "http://user:pass-US-会话A@gate.kookeey.info:1000" &
python revive_import.py --from 34 --to 66 --proxy "http://user:pass-US-会话B@gate.kookeey.info:1000" &
python revive_import.py --from 67 --to 100 --proxy "http://user:pass-US-会话C@gate.kookeey.info:1000" &
```
每账号一个 kookeey 粘性会话 IP，3-5 路并发，100 号约 25-35 分钟。

### 脚本参数
`--only 邮箱`（单号）· `--limit N`（前 N 个）· `--from/--to`（分片）· `--proxy`（换代理）
跳过清单 `scripts/revive_skip.txt`；已成功自动跳过（断点续跑）。

## 三、注册协议要点（2026-08 实测有效）
```
1. GET  /api/accounts/authorize?login_hint=邮箱     → create-account/password（建会话）
2. POST /api/accounts/user/register  {password,username} sentinel flow=username_password_create
3. GET  /api/accounts/email-otp/send                → 触发验证码邮件
4. POST /api/accounts/email-otp/validate {code}     sentinel flow=email_otp_validate → about-you
5. about-you: 填 input[name=name] + input[name=age](19-40数字)，前端带 sentinel 发 create_account
   → callback?code=ac_...   （纯协议过不了 create_account，必须真实浏览器/前端 sentinel）
6. code → oauth/token → access/refresh/id 三件套 → 导入 chatgpt2api
```

## 四、已踩的坑
- **验证码**：邮件 HTML 有多个 6 位数字，必须先剥 `<[^>]+>` 再取，否则拿到假码
- **时区**：98faka received_time 带 Z(UTC)，转本地 naive 再比
- **限流**：`email-otp/validate` 有 max_check_attempts，失败即换号，绝不重试
- **about-you**：填年龄数字不是生日；纯协议 create_account 被 registration_disallowed 拒
- **半成品账号**（已设密码验证码待验）→ log-in/password，点"邮箱验证码登录"补完
- **CF**：真实 Chrome 自动过大多数；项目自带 camoufox cf_solver(:8001) 可解 Turnstile

## 五、chatgpt2api 维护
- 账号池：`GET http://127.0.0.1:23456/api/accounts`（鉴权 Bearer chatgpt2api）
- 密码重登：账号带 password + source_type=password → 过期自动密码重登
- 去重：同一邮箱多 token 时按邮箱去重，保留最新 token+密码的
