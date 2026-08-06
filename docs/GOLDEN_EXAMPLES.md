# 黄金代码范例（GOLDEN EXAMPLES）

> 从本代码库提取的、经生产验证的最佳实践。新代码**优先仿照这些模式**，而非另起炉灶。
> 每条给出：真实出处 + 为什么好。

## 1. 数据库访问统一收口（db_session）

出处：`services/db.py:27`

```python
@contextmanager
def db_session() -> sqlite3.Connection:
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

**为什么好**：commit/rollback/close 收口一处，异常回滚不泄漏连接。全项目禁止手写 connect/close 样板。

## 2. 原子占位防并发竞态（try_start）

出处：`services/register_engine.py:59`

```python
def try_start(self) -> bool:
    if self._running or self._starting:
        return False
    self._stop_requested = False   # 清陈旧停止标志（否则下次 start 被误中止）
    self._starting = True
    return True
```

**为什么好**：check-then-set 原子化，两个并发 /start 只能一个占位成功。配套 `abort_start()` 在异常路径释放占位，防"永久已在运行中"。

## 3. 配置类型防御（_as_int / _as_bool / _coerce_value）

出处：`services/register_engine.py:10`、`api/settings.py:_coerce_value`

```python
def _as_int(value, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default
```

**为什么好**：settings API 可能把数字存成字符串（历史脏数据 `register_concurrency:"true"`），防御式解析避免 `int('true')` 崩溃。写入侧用 `_coerce_value` 按 config_schema 转正确类型，堵住脏数据源头。

## 4. 代理解析一致性（_resolve_proxy）

出处：`services/token_refresher.py:_resolve_proxy`

```python
def _resolve_proxy(self) -> str | None:
    proxy_url = self._config.get("proxy_url")
    if proxy_url:
        return proxy_url
    if use_proxy 为真:
        return proxy_service.get_next()
    return None
```

**为什么好**：所有出站 OpenAI 调用（注册/token巡检/导出）走同一套代理解析，保证"仅代理可达 OpenAI"的部署里各链路行为一致。

## 5. 敏感配置开关解析（tls_verify_enabled）

出处：`services/constants.py:tls_verify_enabled`

```python
def tls_verify_enabled(config: dict) -> bool:
    v = config.get("tls_verify", True)
    if isinstance(v, bool): return v
    if isinstance(v, str): return v.strip().lower() in ("1","true","yes","on")
    return bool(v)
```

**为什么好**：bool/int/str 全兼容，默认安全（true），SSL 拦截代理可回退。是所有"字符串/布尔混存"配置解析的范式。

## 6. 并发限流（Semaphore + gather + 失败分类）

出处：`services/register_engine.py:run_batch`

```python
sem = asyncio.Semaphore(concurrency)
async def process_one(i, mail):
    async with sem:
        await self._pause_event.wait()      # 暂停语义
        if not self._running: return         # 停止语义
        # ... 注册 ...
        # interval sleep 在 sem 内（否则不限速）+ 分段 sleep 响应 stop
results = await asyncio.gather(*(process_one(...) for ...), return_exceptions=True)
```

**为什么好**：Semaphore 限流 + Event 暂停 + 标志位停止 + return_exceptions 收集单点失败不炸整体。**interval 限速必须在 sem 内**（否则槽位 release 后下一个立即启动）。

## 7. 前端鉴权 401 自动收集（api 封装）

出处：`web_dist/app.js:api()`

```javascript
async function api(path, opts = {}, _retried = false) {
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  const key = getAuthKey();
  if (key) headers['X-Auth-Key'] = key;
  const resp = await fetch(API + '/api' + path, { ...opts, headers });
  if (resp.status === 401 && !_retried) {
    const entered = await promptAuthKey();
    if (entered) { setAuthKey(entered); return api(path, opts, true); }
  }
  if (!resp.ok) { if (resp.status === 401) setAuthKey(''); throw new Error(msg); }
  return resp.json();
}
```

**为什么好**：单点封装鉴权头注入 + 401 弹窗收集 + 重试一次 + 错误密钥自动清除。所有 API 调用走这一个封装，行为一致。

## 8. 失败分类诊断（_build_failure_diagnosis）

出处：`services/db.py:_build_failure_diagnosis`

```python
hints = {
    "risk_control": f"⚠️ 风控失败占比 {top_pct:.0%}，建议更换代理出口 IP",
    "otp_timeout":  f"⏱️ 验证码超时占比 {top_pct:.0%}，建议检查邮件 API",
    ...
}
```

**为什么好**：把失败原因按类型聚合并给一句话可行动引导，前端可视化瓶颈，而非只报"失败 N 个"。

## 9. 优雅停机（shutdown 清理浏览器池/CF/token巡检）

出处：`api/__init__.py:shutdown_handler`

```python
cleanup_fn = getattr(browser_register, "cleanup", None)
if cleanup_fn is not None:
    if inspect.iscoroutinefunction(cleanup_fn):
        await cleanup_fn()
    else:
        cleanup_fn()
```

**为什么好**：`getattr` + `iscoroutinefunction` 防御式调用，未实现/同步/异步都安全。资源（浏览器池、CF solver、巡检任务）在停机时统一清理。

---

**反面教材（勿学）**：见 `workflow_status.md` 复现坑段——PKCE 错配、RT 不落库、bat for/f 遇括号、root logger DEBUG 泄密、for /f 读 config 等，全是血泪实证。
