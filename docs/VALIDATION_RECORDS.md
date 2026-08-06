# 验证记录登记册（VALIDATION RECORDS）

> **下次接手先读这个文件**。它记录"哪些区域已验证、验证范围、结论、何时需要复验"。
> 目的：避免每次盲目重复跑同一套检查（如反复猎杀已确认健康的慢查询），把精力聚焦到真正改动过的区域。
>
> **使用规则**：
> 1. 改代码前先看本表——该区域若已验证且未改动，直接复用结论，不重复验证。
> 2. 改了某区域 → 把该行"状态"改为 `⚠️ 需复验`，验证后改回 `✅` 并更新日期/证据。
> 3. 发现记录与实际代码不符（行号/函数变了）→ 先校准记录，再据真实代码验证。

| 区域/主题 | 验证范围 | 结论 | 状态 | 验证日期 | 复验触发条件 |
|---|---|---|---|---|---|
| SQL 注入面 | 全仓 `execute(f"...")` 拼接点 | 动态标识符全硬编码、值全参数化，无注入 | ✅ | 2026-08-06 | 新增手写 SQL 拼接时 |
| 慢查询/索引 | 5万行测试库 EXPLAIN 热点查询 | accounts/emails 按 status 命中索引、logs 走主键、get_stats 聚合走 covering index，无毒 SQL | ✅ | 2026-08-06 | 新增查询/改 schema/数据量量级跳变时 |
| N+1 查询 | get_stats 单条聚合 SQL | 9 次 COUNT 已收敛为 1 条聚合 | ✅ | 2026-08-06 | 改 get_stats 时 |
| 并发竞态 /start | try_start 原子占位 + 双 start 测试 | 仅一个占位成功 | ✅ | 2026-08-06 | 改启动/引擎时 |
| 并发写 config.json | 8 并发写设置 | 不产生损坏 JSON；最后写入者胜出可接受 | ✅ | 2026-08-06 | 改 settings 写入时 |
| 引擎单例测试隔离 | conftest autouse 重置单例 | 测试顺序无关、可重复 | ✅ | 2026-08-06 | 改引擎/服务单例时 |
| 极限参数 | limit=-1/0/1e9/abc/1.5、offset 负 | 全收敛，不崩溃不退化全表 | ✅ | 2026-08-06 | 改分页时 |
| 注入/恶意输入 | search 注入、畸形行、超大文本(2000行) | 参数化不注入，大批量计数准确 | ✅ | 2026-08-06 | 改导入/搜索时 |
| 鉴权边界 | 错密钥401、无密钥401、auth_key 不可写、清空需 confirm | 全部正确 | ✅ | 2026-08-06 | 改鉴权/清空时 |
| 前端鉴权 401 | playwright 实测 401→弹窗→输密钥→恢复 | 全链路通过 | ✅ | 2026-08-06 | 改 api()/鉴权中间件时 |
| PKCE 配对 | sha256(verifier)==challenge 实证 + 回归测试 | 同对贯穿，换 token 合法 | ✅ | 2026-08-06 | 改 protocol_register OAuth 时 |
| RT 轮换落库 | 真实 OpenAI token 实测新 AT+新 RT 均落库 | token_refresher/导出都落库新 RT | ✅ | 2026-08-06 | 改 token 刷新时 |
| TLS verify | 实测 verify=True 经代理刷新成功 | 默认 true 可用，tls_verify 可回退 | ✅ | 2026-08-06 | 改出站 HTTP 时 |
| 注册间隔限速 | 回归测试：sem 内 sleep 真实间隔 | interval 生效 | ✅ | 2026-08-06 | 改 run_batch 时 |
| stop 后重启 | 回归测试：stop→try_start→run_batch 正常 | 不再被陈旧标志中止 | ✅ | 2026-08-06 | 改 try_start/stop 时 |
| A4A6 自适应暂停 | server_5xx 连续3次暂停、风控>40%暂停、network 不停 | 分支正确 | ✅ | 2026-08-06 | 改自适应逻辑时 |
| bat 双击启动 | os.startfile 实测 + 纯 ASCII 字节检查=0 | 不闪退、读 config 端口正确(set/p) | ✅ | 2026-08-06 | 改 .bat 时（**必做字节检查**） |
| 前端 UI | playwright 点遍 5 标签页+高级弹窗，0 console error | 全渲染 | ✅ | 2026-08-06 | 改 web_dist 时 |
| 启动冒烟 | uvicorn 起实例 healthz 3.1.1，无 schema 告警 | 正常 | ✅ | 2026-08-06 | 改启动/config 时 |

## 已知边界（外部受限，非 bug，勿当新问题）

| 项 | 真实状态 | 缺什么 |
|---|---|---|
| 完整真实注册流（register_one 遇 CF/取码） | 协议/浏览器注册各分支 mock + PKCE/RT 真实验证，但未跑完整真实注册（需真实 OpenAI 账号+代理） | 真实账号配额 + 可用代理出口 |
| T3 代理健康 | 仅 TCP 端口可达 ≠ 账号可用 | 真实 HTTP 出口探测（v3.2） |
| 完整真实并发注册压测 | 引擎并发逻辑有测试，但未用真实代理+账号跑大批量 | 同上 |

## 复验快捷命令

```bash
./.venv/Scripts/python.exe -m pytest tests/ -p no:warnings   # 全量（275+）
./.venv/Scripts/python.exe -m pytest tests/test_stress_security.py   # 压测/渗透
./.venv/Scripts/python.exe scripts/smoke_browser_pool.py     # 真实浏览器池
./.venv/Scripts/python.exe scripts/smoke_token_refresh.py    # 真实 token 刷新
```
