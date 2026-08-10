# P2 优化项完整闭环实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 闭环 6 项 P2 优化（B15-B20），通过测试验证，部署到服务器并创建 GitHub Release。

**架构：** 6 项互相独立，可并行执行。B15 新增 metrics 模块和端点；B16 增强 db.py VACUUM 策略；B17 拆分前端模块；B18 新增 i18n 层；B19 补全 7 个 API 路由文件的 docstrings/models；B20 优化 Dockerfile 分层。

**技术栈：** Python/FastAPI, Prometheus client, Docker, 纯 JS i18n（无框架）, pytest

---

### 任务 B15：Prometheus 指标导出

**文件：**
- 创建：`services/metrics.py`
- 修改：`api/__init__.py`（挂载 `/metrics` 端点）
- 修改：`services/register_engine.py`（埋点）
- 修改：`services/db.py`（埋点）
- 修改：`requirements.txt`（加 `prometheus-client>=0.19`）
- 测试：`tests/test_metrics.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_metrics.py
import pytest
from fastapi.testclient import TestClient

def test_metrics_endpoint():
    """验证 /metrics 端点返回 Prometheus 格式数据"""
    from api.__init__ import create_app
    app = create_app()
    client = TestClient(app)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "gpt_register_" in resp.text
    assert "register_total" in resp.text
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_metrics.py -v`
预期：FAIL - ImportError 或 404

- [ ] **步骤 3：创建 services/metrics.py**

```python
# services/metrics.py
"""Prometheus 指标定义与导出。

指标前缀: gpt_register_
"""
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
from starlette.requests import Request

# === 注册计数 ===
register_total = Counter(
    "gpt_register_register_total",
    "注册请求总数",
    labelnames=["status"],  # success / failed / skipped / cf_blocked
)
register_duration = Histogram(
    "gpt_register_register_duration_seconds",
    "注册耗时（秒）",
    buckets=[10, 30, 60, 120, 300, 600],
)

# === 邮箱库存 ===
emails_total = Gauge("gpt_register_emails_total", "邮箱总数")
emails_pending = Gauge("gpt_register_emails_pending", "待注册邮箱数")
accounts_success = Gauge("gpt_register_accounts_success", "注册成功账号数")
accounts_failed = Gauge("gpt_register_accounts_failed", "注册失败账号数")

# === 引擎状态 ===
engine_running = Gauge("gpt_register_engine_running", "引擎是否运行中（1/0）")
engine_queue_depth = Gauge("gpt_register_engine_queue_depth", "注册队列深度")
engine_concurrency = Gauge("gpt_register_engine_concurrency", "当前并发注册数")
proxy_pool_size = Gauge("gpt_register_proxy_pool_size", "代理池可用数量")

# === 系统 ===
db_file_size_bytes = Gauge("gpt_register_db_file_size_bytes", "数据库文件大小（字节）")
last_vacuum_timestamp = Gauge("gpt_register_last_vacuum_timestamp", "上次 VACUUM 时间戳")


async def metrics_endpoint(request: Request) -> Response:
    """返回 Prometheus 格式指标。"""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_metrics.py -v`
预期：PASS

- [ ] **步骤 5：在 api/__init__.py 中挂载 metrics 端点**

在 `app = FastAPI(...)` 之后的适当位置添加：

```python
# Prometheus 指标端点（在 auth 中间件之前注册，允许无鉴权访问）
from services.metrics import metrics_endpoint
app.add_route("/metrics", metrics_endpoint)
```

注意：必须在 `app.add_middleware(AuthKeyMiddleware, ...)` 之前添加，或者将 `/metrics` 加入 `PUBLIC_API_PATHS`。

- [ ] **步骤 6：在 register_engine.py 埋点**

在注册成功/失败/跳过/cf_blocked 的回调中增加：

```python
from services.metrics import register_total, register_duration
import time

# 在注册开始前记录 start_time
# 在回调中：
register_total.labels(status=status).inc()
register_duration.observe(time.time() - start_time)
```

- [ ] **步骤 7：在 db.py 的 get_stats() 中刷新 gauge**

```python
from services.metrics import (emails_total, emails_pending,
    accounts_success, accounts_failed, db_file_size_bytes)

# 在 get_stats 返回前调用：
emails_total.set(row["emails_total"])
emails_pending.set(row["emails_pending"])
accounts_success.set(row["accounts_success"])
accounts_failed.set(row["accounts_failed"])
```

- [ ] **步骤 8：更新 requirements.txt**

```txt
prometheus-client>=0.19
```

- [ ] **步骤 9：运行全量测试**

运行：`pytest tests/ -v`
预期：ALL PASS

- [ ] **步骤 10：Commit**

```bash
git add services/metrics.py api/__init__.py services/register_engine.py services/db.py requirements.txt tests/test_metrics.py
git commit -m "feat: B15 Prometheus 指标导出（注册计数/耗时/邮箱库/引擎状态）"
```

---

### 任务 B16：数据库定时 VACUUM 策略

**文件：**
- 修改：`services/db.py`
- 修改：`api/settings.py`（新增手动 VACUUM 端点）
- 测试：`tests/test_vacuum.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_vacuum.py
import pytest
from services.db import vacuum_if_needed

def test_vacuum_if_needed():
    """VACUUM 应成功执行而不报错。"""
    # 直接调用 vacuum_if_needed（force=True 跳过大小检查）
    result = vacuum_if_needed(force=True)
    assert result is True or result == "ok"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_vacuum.py -v`
预期：FAIL - ImportError

- [ ] **步骤 3：在 services/db.py 中实现 VACUUM 策略**

在文件末尾添加：

```python
# === VACUUM 策略 ===
import os
import time
from pathlib import Path
import logging

_log = logging.getLogger("gpt-register.vacuum")

# 删除行超过 20% 或文件超过 50MB 触发 VACUUM
VACUUM_DELETE_RATIO = 0.20
VACUUM_FILE_SIZE_MB = 50

# 记录上次 VACUUM 时间戳（用于 Prometheus）
_last_vacuum_time: float = 0.0

def _get_db_path() -> Path:
    """获取数据库文件路径。"""
    return Path(__file__).resolve().parent.parent / "data" / "gpt_register.db"


def _get_deleted_ratio() -> float:
    """计算已删除行占总行数的比例。"""
    conn = get_conn()
    try:
        total, deleted = 0, 0
        for tbl in ("emails", "accounts", "logs", "platform_usage"):
            row = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            total += row[0] if row else 0
            # 获取 deleted 行数（如果有软删除标记）
            try:
                drow = conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE status='deleted'").fetchone()
                deleted += drow[0] if drow else 0
            except Exception:
                pass
        return deleted / max(total, 1)
    finally:
        conn.close()


def vacuum_if_needed(force: bool = False) -> str:
    """检查是否需要 VACUUM，满足条件则执行。

    Args:
        force: 强制 VACUUM，跳过大小和删除比例检查

    Returns:
        "ok" / "skipped" / "error:xxx"
    """
    global _last_vacuum_time

    db_path = _get_db_path()
    if not db_path.exists():
        return "skipped:no_db"

    # 检查文件大小
    size_mb = db_path.stat().st_size / 1024 / 1024

    if not force:
        if size_mb < VACUUM_FILE_SIZE_MB:
            return f"skipped:file_size={size_mb:.1f}MB<{VACUUM_FILE_SIZE_MB}MB"
        ratio = _get_deleted_ratio()
        if ratio < VACUUM_DELETE_RATIO:
            return f"skipped:delete_ratio={ratio:.2%}<{VACUUM_DELETE_RATIO:.0%}"

    try:
        conn = get_conn()
        try:
            conn.execute("VACUUM")
            conn.commit()
            _last_vacuum_time = time.time()
            _log.info("VACUUM 完成（force=%s, size=%.1fMB）", force, size_mb)
            # 更新 Prometheus 指标
            try:
                from services.metrics import last_vacuum_timestamp, db_file_size_bytes
                last_vacuum_timestamp.set(_last_vacuum_time)
                db_file_size_bytes.set(db_path.stat().st_size)
            except ImportError:
                pass
            return "ok"
        finally:
            conn.close()
    except Exception as e:
        _log.error("VACUUM 失败: %s", e)
        return f"error:{e}"


def get_last_vacuum_time() -> float:
    """返回上次 VACUUM 时间戳。"""
    return _last_vacuum_time
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_vacuum.py -v`
预期：PASS

- [ ] **步骤 5：在 api/settings.py 中新增手动 VACUUM 端点**

```python
@router.post("/vacuum")
async def trigger_vacuum():
    """手动触发数据库 VACUUM。"""
    from services.db import vacuum_if_needed
    result = vacuum_if_needed(force=True)
    return {"status": result}
```

- [ ] **步骤 6：在 api/__init__.py 的 startup_handler 中注册定时 VACUUM**

```python
# 在 startup_handler 中，在 token 巡检启动后添加：
import asyncio

async def _vacuum_loop():
    """每天凌晨 4 点执行 VACUUM。"""
    while True:
        now = time.localtime()
        # 计算到下一个凌晨 4 点的秒数
        seconds_till_4am = ((4 - now.tm_hour + 24) % 24) * 3600 - now.tm_min * 60 - now.tm_sec
        if seconds_till_4am <= 0:
            seconds_till_4am += 86400
        await asyncio.sleep(seconds_till_4am)
        from services.db import vacuum_if_needed
        result = vacuum_if_needed()
        log.info("定时 VACUUM: %s", result)

asyncio.create_task(_vacuum_loop())
```

- [ ] **步骤 7：运行全量测试**

运行：`pytest tests/ -v`
预期：ALL PASS

- [ ] **步骤 8：Commit**

```bash
git add services/db.py api/settings.py tests/test_vacuum.py
git commit -m "feat: B16 数据库定时 VACUUM 策略（自动+手动触发）"
```

---

### 任务 B17：前端 app.js 拆分为 ES Module

**文件：**
- 创建：`web_dist/api.js`
- 创建：`web_dist/stats.js`
- 创建：`web_dist/register.js`
- 创建：`web_dist/settings.js`
- 创建：`web_dist/utils.js`
- 修改：`web_dist/app.js`（入口文件，精简至 ~100 行）
- 修改：`web_dist/index.html`（改为 `<script type="module">`）

**原则：** 纯拆分，不改变任何业务逻辑。每个模块 export 其函数，入口 import 并挂载到 window（兼容现有 onclick 绑定）。

- [ ] **步骤 1：创建 web_dist/utils.js**

```javascript
// web_dist/utils.js — 通用工具函数
// 由 app.js 作为入口模块导入

// === 全局状态 ===
export let showPasswords = false;
export let pollTimer = null;
export let accPage = 1;
export let emPage = 1;
export let lastLogId = 0;
export let taskRunning = false;
export let logSseSource = null;
export let logSseFailed = 0;
export let logPaused = false;
export let proxyHealthMap = {};

// === 搜索防抖 ===
let searchDebounce = null;
export function debounceSearch(fn, ms = 300) {
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(fn, ms);
}

// === 工具函数 ===
export function escapeHtml(text) {
    if (!text) return "";
    const d = document.createElement("div");
    d.textContent = text;
    return d.innerHTML;
}

export function toast(msg, type = "info", duration = 3000) {
    const existing = document.querySelector(".toast-container");
    if (existing) existing.remove();
    const c = document.createElement("div");
    c.className = "toast-container";
    c.innerHTML = `<div class="toast toast-${type}">${escapeHtml(msg)}</div>`;
    document.body.appendChild(c);
    setTimeout(() => c.remove(), duration);
}

export function toastMsg(type, msg) {
    toast(msg, type);
}

export function withBusy(btn, promise) {
    if (!btn) return promise;
    btn.disabled = true;
    btn.textContent = "处理中…";
    return promise.finally(() => {
        btn.disabled = false;
        btn.textContent = btn.dataset.origText || btn.textContent;
    });
}

export function copyText(text) {
    navigator.clipboard.writeText(text).then(
        () => toast("已复制", "success"),
        () => toast("复制失败", "error")
    );
}

export function openModal(id) {
    document.getElementById(id).style.display = "flex";
}

export function closeModal(id) {
    document.getElementById(id).style.display = "none";
}

// === 主题 ===
export function applyTheme(dark) {
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    localStorage.setItem("gpt-register-theme", dark ? "dark" : "light");
}

export function toggleTheme() {
    const isDark = document.documentElement.getAttribute("data-theme") !== "light";
    applyTheme(!isDark);
}

export function initTheme() {
    const saved = localStorage.getItem("gpt-register-theme");
    applyTheme(saved !== "light");
}

// === 开关 ===
export function togglePw() {
    showPasswords = document.getElementById("showPw").checked;
    loadAccounts();
}

export function switchTab(name) {
    document.querySelectorAll(".tab-content").forEach(el => el.style.display = "none");
    const target = document.getElementById("tab-" + name);
    if (target) target.style.display = "block";
    document.querySelectorAll(".tab-btn").forEach(el => el.classList.remove("active"));
    const btn = document.querySelector(`.tab-btn[data-tab="${name}"]`);
    if (btn) btn.classList.add("active");
}
```

- [ ] **步骤 2：创建 web_dist/api.js**

```javascript
// web_dist/api.js — API 调用封装
export function getAuthKey() {
    return localStorage.getItem("gpt-register-auth-key") || "";
}

export function setAuthKey(key) {
    localStorage.setItem("gpt-register-auth-key", key);
}

export function promptAuthKey() {
    const key = prompt("请输入管理密钥（X-Auth-Key）：");
    if (key) setAuthKey(key);
    return key;
}

export async function api(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    const authKey = getAuthKey();
    if (authKey) headers["X-Auth-Key"] = authKey;
    const resp = await fetch("/api" + path, {
        ...options,
        headers,
    });
    if (resp.status === 401) {
        const newKey = promptAuthKey();
        if (newKey) return api(path, options);
        throw new Error("未授权");
    }
    return resp;
}
```

- [ ] **步骤 3：创建 web_dist/stats.js**

```javascript
// web_dist/stats.js — 统计仪表盘、健康卡片、转化漏斗
import { api, toast } from "./api.js";
import { escapeHtml, taskRunning } from "./utils.js";

export async function refreshStatus() {
    try {
        const resp = await api("/register/status");
        const data = await resp.json();
        document.getElementById("statusBadge").textContent = data.status === "running" ? "运行中" : "已停止";
        document.getElementById("statusBadge").className = "badge " + (data.status === "running" ? "running" : "stopped");
        taskRunning = data.status === "running";
        updateStats(data);
        updateReadiness(data);
        updateFunnel(data);
        renderFailureDiagnosis(data);
        return data;
    } catch (e) {
        console.error("refreshStatus error:", e);
        return null;
    }
}

export function updateStats(data) {
    document.getElementById("sEmailsTotal").textContent = data.email_stats?.total ?? 0;
    document.getElementById("sEmailsPending").textContent = data.email_stats?.pending ?? 0;
    document.getElementById("sAccountsSuccess").textContent = data.account_stats?.success ?? 0;
    document.getElementById("sAccountsFailed").textContent = data.account_stats?.failed ?? 0;
    document.getElementById("sAccountsSkipped").textContent = data.account_stats?.skipped ?? 0;
    document.getElementById("sAccountsTotal").textContent = (data.account_stats?.success ?? 0) + (data.account_stats?.failed ?? 0) + (data.account_stats?.skipped ?? 0);
    // 更新进度条
    updateProgress(data);
    // 更新 ETA
    updateEta(data);
}

function updateProgress(data) {
    const bar = document.getElementById("progressBar");
    if (!bar) return;
    const total = data.email_stats?.pending ?? 0;
    const done = data.account_stats?.success ?? 0;
    if (total > 0) {
        const pct = Math.min(100, Math.round(done / total * 100));
        bar.style.width = pct + "%";
        bar.textContent = pct + "%";
    } else {
        bar.style.width = "0%";
        bar.textContent = "";
    }
}

function updateEta(data) {
    const el = document.getElementById("etaDisplay");
    if (!el) return;
    const speed = data.speed ?? 0;
    const pending = data.email_stats?.pending ?? 0;
    if (speed > 0 && pending > 0) {
        const sec = Math.round(pending / speed);
        el.textContent = `速率: ${speed.toFixed(1)}/min, ETA: ${Math.round(sec / 60)}min`;
    } else {
        el.textContent = "";
    }
}

export function updateReadiness(data) {
    const grid = document.getElementById("readinessGrid");
    if (!grid) return;
    const r = data.readiness;
    if (!r) { grid.style.display = "none"; return; }
    grid.style.display = "flex";
    document.getElementById("sFullyReady").textContent = `${r.fully_ready ?? 0}/${r.total ?? 0}`;
    // 可以展开更多维度
}

export function updateFunnel(data) {
    const bar = document.getElementById("funnelBar");
    if (!bar) return;
    const f = data.funnel;
    if (!f) { bar.style.display = "none"; return; }
    bar.style.display = "block";
    const segs = document.getElementById("funnelSegments");
    segs.innerHTML = "";
    const items = [
        { label: "邮箱", v: f.emails, c: "#58a6ff" },
        { label: "已消耗", v: f.consumed, c: "#d29922" },
        { label: "成功", v: f.success, c: "#3fb950" },
        { label: "有Token", v: f.has_token, c: "#1f6feb" },
    ];
    const max = Math.max(...items.map(i => i.v), 1);
    items.forEach(item => {
        const pct = Math.round(item.v / max * 100);
        const div = document.createElement("div");
        div.style.cssText = `flex:${pct};background:${item.c};padding:4px;text-align:center;border-radius:4px;color:#fff;min-width:30px`;
        div.textContent = `${item.label} ${item.v}`;
        segs.appendChild(div);
    });
}

export function renderFailureDiagnosis(data) {
    const el = document.getElementById("failureDiagnosis");
    if (!el) return;
    const fd = data.failure_diagnosis;
    if (!fd || !fd.length) { el.style.display = "none"; return; }
    el.style.display = "block";
    el.innerHTML = "<strong>失败诊断建议：</strong><br>" + fd.map(f => `• ${escapeHtml(f)}`).join("<br>");
}

// 轮询调度在 app.js 中实现
```

- [ ] **步骤 4：创建 web_dist/register.js**

```javascript
// web_dist/register.js — 注册控制、导入邮箱、账号列表、导出
import { api, toast } from "./api.js";
import { escapeHtml, withBusy, showPasswords } from "./utils.js";

// === 导入邮箱 ===
export function openImport() {
    document.getElementById("importModal").style.display = "flex";
}

export async function importEmails() {
    const btn = document.getElementById("btnImportDo");
    const source = document.getElementById("importSource").value;
    if (!source) { toast("请选择邮箱来源", "error"); return; }
    // ... 导入逻辑
}

export function openManualAdd() {
    document.getElementById("manualAddModal").style.display = "flex";
}

export async function submitManualAdd() {
    // ... 手动添加逻辑
}

export async function loadPlatforms() {
    // ... 加载平台下拉
}

// === 注册控制 ===
export async function startRegister() {
    const btn = document.getElementById("btnStart");
    return withBusy(btn, (async () => {
        const resp = await api("/register/start", { method: "POST" });
        const data = await resp.json();
        toast(data.message || "已启动", "success");
    })());
}

export async function controlRegister(action) {
    const resp = await api("/register/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
    });
    const data = await resp.json();
    toast(data.message || "操作成功", "success");
}

export async function retryFailed() {
    const type = document.getElementById("retryType")?.value || "";
    const resp = await api("/register/retry-failed", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ failure_type: type || null }),
    });
    const data = await resp.json();
    toast(`已回置 ${data.count} 个邮箱`, "success");
}

// === 导出/清空 ===
export async function exportTokens() {
    const resp = await api("/register/accounts/export?include_rt=true");
    const text = await resp.text();
    copyText(text);
}

export async function replenishTokens() {
    const resp = await api("/register/replenish-tokens", { method: "POST" });
    const data = await resp.json();
    toast(`已补齐 ${data.count} 个`, "success");
}

export async function pushToChatgpt2api() {
    const resp = await api("/register/push-chatgpt2api", { method: "POST" });
    const data = await resp.json();
    toast(`已推送 ${data.count} 个`, "success");
}

export function openClear() {
    document.getElementById("clearModal").style.display = "flex";
}

export async function confirmClear() {
    const resp = await api("/register/clear", { method: "POST" });
    const data = await resp.json();
    toast(data.message, "success");
    refreshStatus();
    loadAccounts();
}

// === 注册记录列表 ===
export async function loadAccounts() {
    const page = accPage;
    const search = document.getElementById("searchAcc")?.value || "";
    const status = document.getElementById("filterAccStatus")?.value || "";
    const resp = await api(`/register/accounts?page=${page}&search=${encodeURIComponent(search)}&status=${status}`);
    const data = await resp.json();
    renderAccounts(data);
}

function renderAccounts(data) {
    const tbody = document.getElementById("accountsBody");
    tbody.innerHTML = "";
    (data.accounts || []).forEach(ac => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td>${escapeHtml(ac.email)}</td>
            <td>${escapeHtml(ac.status)}</td>
            <td>${escapeHtml(ac.failure_type || "")}</td>
            <td>${tokenCell(ac.access_token)}</td>
            <td>${c2apiReadyCell(ac.c2api_ready)}</td>
        `;
        tbody.appendChild(tr);
    });
    renderPagination(data.total, accPage, "accPageNav", p => { accPage = p; loadAccounts(); });
}

// 注册列表也导出到全局用于 onclick
window.startRegister = startRegister;
window.controlRegister = controlRegister;
window.retryFailed = retryFailed;
window.exportTokens = exportTokens;
window.replenishTokens = replenishTokens;
window.pushToChatgpt2api = pushToChatgpt2api;
window.openClear = openClear;
window.confirmClear = confirmClear;
window.openImport = openImport;
window.importEmails = importEmails;
window.openManualAdd = openManualAdd;
window.submitManualAdd = submitManualAdd;
```

- [ ] **步骤 5：创建 web_dist/settings.js**

```javascript
// web_dist/settings.js — 设置管理
import { api, toast } from "./api.js";

export async function loadSettings() {
    // ... 加载基础设置
}

export async function saveSettings() {
    // ... 保存基础设置
}

export async function openAdvanced() {
    // ... 打开高级设置弹窗
}

export async function saveAdvanced() {
    // ... 保存高级设置
}

// 注册到全局
window.loadSettings = loadSettings;
window.saveSettings = saveSettings;
window.openAdvanced = openAdvanced;
window.saveAdvanced = saveAdvanced;
```

- [ ] **步骤 6：创建 web_dist/app.js（入口，精简版）**

```javascript
// web_dist/app.js — 入口模块，按需导入各模块功能
import { refreshStatus, loadAccounts } from "./stats.js";
import { loadLogs, startLogSse } from "./logs.js";
import { loadSettings } from "./settings.js";
import { initTheme, startPolling } from "./utils.js";

// 初始化
initTheme();
refreshStatus();
loadAccounts();
loadLogs(true);
loadSettings();
startPolling();

// 全局挂载（兼容现有 onclick 绑定已在各模块中完成）
```

- [ ] **步骤 7：修改 index.html**

将 `<script src="app.js"></script>` 改为：

```html
<script type="module" src="app.js"></script>
```

- [ ] **步骤 8：验证前端不报错**

打开浏览器控制台，确认无 `Uncaught ReferenceError` 或模块加载错误。

- [ ] **步骤 9：Commit**

```bash
git add web_dist/
git commit -m "refactor: B17 前端 app.js 拆分为 ES Module（6 模块）"
```

---

### 任务 B18：国际化 i18n 支持

**文件：**
- 创建：`web_dist/i18n.js`
- 修改：`web_dist/index.html`（加语言选择器）
- 修改：`web_dist/app.js`（入口模块导入 i18n）

- [ ] **步骤 1：创建 web_dist/i18n.js**

```javascript
// web_dist/i18n.js — 极简国际化支持
// 支持 zh-CN / en-US，语言偏好存 localStorage

const LANG_KEY = "gpt-register-lang";

const messages = {
    "zh-CN": {
        title: "GPT 自动注册控制台",
        // 统计卡片
        emails_total: "邮箱总数",
        pending: "待注册",
        success: "注册成功",
        failed: "注册失败",
        skipped: "已跳过",
        total: "账号总数",
        // 操作
        start: "▶️ 开始注册",
        pause: "⏸️ 暂停",
        resume: "▶️ 恢复",
        stop: "⏹️ 停止",
        import_email: "📥 导入邮箱",
        manual_add: "✍️ 手动添加邮箱",
        retry_failed: "🔄 重试失败",
        export_tokens: "📤 导出 Token",
        push_c2api: "🚀 推送 chatgpt2api",
        clear: "🗑️ 清空",
        search: "搜索",
        status_running: "运行中",
        status_stopped: "已停止",
        // 通用
        cancel: "取消",
        confirm: "确认",
        save: "保存",
        close: "关闭",
        copy: "复制",
        download: "下载",
        // 日志
        log_level: "日志级别",
        all: "全部",
        info: "信息",
        warn: "警告",
        error: "错误",
        debug: "调试",
        log_search: "搜索日志…",
        pause_scroll: "暂停滚动",
        resume_scroll: "恢复滚动",
        export_logs: "导出日志",
        clear_logs: "清空日志",
        // 设置
        settings: "设置",
        advanced: "高级设置",
        // 代理
        proxies: "代理池",
        check_health: "探测健康",
        // 语言
        language: "语言",
    },
    "en-US": {
        title: "GPT Auto Register Console",
        emails_total: "Emails Total",
        pending: "Pending",
        success: "Success",
        failed: "Failed",
        skipped: "Skipped",
        total: "Accounts Total",
        start: "▶️ Start",
        pause: "⏸️ Pause",
        resume: "▶️ Resume",
        stop: "⏹️ Stop",
        import_email: "📥 Import Emails",
        manual_add: "✍️ Manual Add",
        retry_failed: "🔄 Retry Failed",
        export_tokens: "📤 Export Tokens",
        push_c2api: "🚀 Push to chatgpt2api",
        clear: "🗑️ Clear",
        search: "Search",
        status_running: "Running",
        status_stopped: "Stopped",
        cancel: "Cancel",
        confirm: "Confirm",
        save: "Save",
        close: "Close",
        copy: "Copy",
        download: "Download",
        log_level: "Log Level",
        all: "All",
        info: "Info",
        warn: "Warning",
        error: "Error",
        debug: "Debug",
        log_search: "Search logs…",
        pause_scroll: "Pause Scroll",
        resume_scroll: "Resume Scroll",
        export_logs: "Export Logs",
        clear_logs: "Clear Logs",
        settings: "Settings",
        advanced: "Advanced Settings",
        proxies: "Proxies",
        check_health: "Check Health",
        language: "Language",
    },
};

let currentLang = localStorage.getItem(LANG_KEY) || "zh-CN";

export function t(key) {
    return messages[currentLang]?.[key] || messages["zh-CN"]?.[key] || key;
}

export function getLang() {
    return currentLang;
}

export function setLang(lang) {
    if (!messages[lang]) return;
    currentLang = lang;
    localStorage.setItem(LANG_KEY, lang);
    // 触发页面重渲染
    document.dispatchEvent(new CustomEvent("langchange", { detail: lang }));
}

export function renderLangSelector() {
    const sel = document.createElement("select");
    sel.id = "langSelector";
    sel.style.cssText = "margin-left:8px;padding:2px 6px;border-radius:4px;font-size:12px";
    sel.innerHTML = `
        <option value="zh-CN" ${currentLang === "zh-CN" ? "selected" : ""}>中文</option>
        <option value="en-US" ${currentLang === "en-US" ? "selected" : ""}>English</option>
    `;
    sel.addEventListener("change", () => setLang(sel.value));
    // 插入到 header 中的主题按钮后面
    const themeBtn = document.querySelector(".btn-ghost");
    if (themeBtn && themeBtn.parentNode) {
        themeBtn.parentNode.insertBefore(sel, themeBtn.nextSibling);
    }
    return sel;
}
```

- [ ] **步骤 2：在 app.js 中导入 i18n**

在入口 `app.js` 中添加：

```javascript
import { renderLangSelector } from "./i18n.js";
renderLangSelector();
```

- [ ] **步骤 3：Commit**

```bash
git add web_dist/i18n.js web_dist/index.html web_dist/app.js
git commit -m "feat: B18 国际化 i18n 支持（zh-CN/en-US）"
```

---

### 任务 B19：OpenAPI 完善

**文件：**
- 修改：`api/register.py`
- 修改：`api/emails.py`
- 修改：`api/stats.py`
- 修改：`api/logs.py`
- 修改：`api/settings.py`
- 修改：`api/proxies.py`
- 修改：`api/__init__.py`
- 修改：`api/__init__.py`（版本号统一）

**方法：** 为每个端点补充 Pydantic response model + docstring。不改变业务逻辑。

- [ ] **步骤 1：统一定义 response models**

在 `api/__init__.py` 中或创建 `api/models.py`：

```python
# api/models.py
from pydantic import BaseModel
from typing import Optional, Any

class ApiResponse(BaseModel):
    """通用 API 响应。"""
    status: str = "ok"
    message: Optional[str] = None
    data: Optional[Any] = None

class HealthzResponse(BaseModel):
    """健康检查响应。"""
    status: str  # ok / degraded
    db: str  # ok / error
    cf_solver: str  # ok / unknown
    browser_pool_size: int
    auth: str  # enabled / disabled
    auth_enforced: bool
    version: str
```

- [ ] **步骤 2：补全 api/register.py 的 docstrings**

为 `/start`, `/control`, `/status`, `/accounts`, `/import-emails` 等端点补充：

```python
@router.post("/start", summary="启动注册任务", description="原子化启动注册引擎，已运行则返回冲突。")
async def start_register():
    """启动注册任务。"""
    ...

@router.post("/control", summary="控制注册引擎", description="暂停/恢复/停止注册引擎。")
async def control_register(action: str):
    """控制注册引擎。"""
    ...

@router.get("/status", summary="引擎状态", description="返回引擎运行状态、统计、代理池大小、最新任务信息。")
async def register_status():
    """获取引擎状态。"""
    ...
```

- [ ] **步骤 3：补全 api/emails.py 的 docstrings**

```python
@router.get("/pending", summary="获取待处理邮箱", description="返回当前待注册的邮箱列表。")
async def get_pending_emails():
    ...
```

- [ ] **步骤 4：补全 api/stats.py 的 docstrings**

```python
@router.get("/", summary="统计数据", description="返回邮箱库和账号库的汇总统计。")
async def get_stats():
    ...
```

- [ ] **步骤 5：补全 api/logs.py 的 docstrings**

```python
@router.get("", summary="日志列表", description="返回日志列表，支持 after_id 增量拉取。")
@router.get("/", summary="日志列表（带斜杠）")
async def get_logs(limit: int = 200, after_id: int = 0, level: str = "", search: str = ""):
    ...
```

- [ ] **步骤 6：补全 api/settings.py 的 docstrings**

```python
@router.get("/", summary="读取设置", description="返回所有设置项，敏感键已掩码。")
async def get_settings():
    ...

@router.post("/", summary="更新设置", description="白名单校验 + 类型转换 + 同步 config.json。")
async def update_setting(data: dict):
    ...
```

- [ ] **步骤 7：补全 api/proxies.py 的 docstrings**

```python
@router.get("/", summary="代理池列表", description="返回代理池内容及统计。")
async def get_proxies():
    ...
```

- [ ] **步骤 8：版本号统一**

在 `api/__init__.py` 中：

```python
VERSION = "3.3.0"
app = FastAPI(title="GPT 自动注册", version=VERSION)
# 和 /api/healthz 中的 "version": VERSION
```

- [ ] **步骤 9：启用 /docs 端点**

在 `api/__init__.py` 添加到 PUBLIC_API_PATHS：

```python
PUBLIC_API_PATHS = {"/api/healthz", "/docs", "/openapi.json", "/redoc"}
```

- [ ] **步骤 10：运行全量测试**

运行：`pytest tests/ -v`
预期：ALL PASS

- [ ] **步骤 11：Commit**

```bash
git add api/ tests/
git commit -m "docs: B19 OpenAPI 完善（response models + docstrings + 版本号统一）"
```

---

### 任务 B20：Docker 分层缓存优化

**文件：**
- 修改：`Dockerfile`
- 修改：`.dockerignore`

- [ ] **步骤 1：创建 .dockerignore**

```dockerignore
.git
.gitignore
README.md
docs/
tests/
scripts/
*.md
__pycache__/
*.pyc
data/
logs/
config.json
proxies.txt
```

- [ ] **步骤 2：优化 Dockerfile 分层**

```dockerfile
# GPT 自动注册 —— 单容器（主服务 23457 + CF Solver 8001 同容器自启）
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 第1层：系统依赖（低频变化）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    libgtk-3-0 libx11-xcb1 libdbus-glib-1-2 libxt6 libasound2 \
    libxcomposite1 libxdamage1 libxrandr2 libgbm1 libpango-1-0-0 libcairo2 \
    libnss3 libnspr4 libxss1 libxshmfence1 \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

# 第2层：Python 依赖（仅 requirements.txt 变化时失效）
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 第3层：camoufox 浏览器引擎（低频变化）
RUN python -m camoufox fetch || echo "[warn] camoufox fetch 失败，运行期再试"

# 第4层：应用源码（高频变化，独立缓存层）
COPY main.py ./
COPY api ./api
COPY services ./services
COPY cf_solver ./cf_solver
COPY web_dist ./web_dist
COPY config.example.json ./config.json

RUN mkdir -p /app/data /app/logs
EXPOSE 23457 8001

CMD ["python", "main.py"]
```

- [ ] **步骤 3：验证 Docker 构建**

运行：`docker build -t gpt-register:local .`
预期：构建成功，各层缓存生效

- [ ] **步骤 4：Commit**

```bash
git add Dockerfile .dockerignore
git commit -m "chore: B20 Docker 分层缓存优化（.dockerignore + 分层顺序）"
```

---

### 任务 7：测试 + 验证

**文件：** 无新增，运行测试

- [ ] **步骤 1：安装所有依赖**

```bash
pip install -r requirements.txt
pip install prometheus-client
```

- [ ] **步骤 2：运行全量测试**

```bash
pytest tests/ -v 2>&1
```

- [ ] **步骤 3：Per-file 代码审查**

对照 CHANGE_REPORT_v3.4.html 检查每个变更文件的一致性和完整性。

- [ ] **步骤 4：前端验证**

检查 `index.html` 的 `<script type="module">` 是否正确，模块间 import 链路是否完整。

---

### 任务 8：部署更新到服务器

- [ ] **步骤 1：推送到仓库**

```bash
git add .
git commit -m "chore: P2 优化闭环（B15-B20）"
```

- [ ] **步骤 2：创建 Tag**

```bash
git tag v3.4.0
git push origin main --tags
```

- [ ] **步骤 3：创建 GitHub Release**

```bash
gh release create v3.4.0 --title "v3.4.0 P2优化闭环" --notes "B15 Prometheus 指标导出\nB16 数据库定时VACUUM\nB17 前端模块拆分\nB18 国际化i18n\nB19 OpenAPI 完善\nB20 Docker分层缓存优化"
```

- [ ] **步骤 4：SSH 到服务器更新**

```bash
ssh -p <port> <user>@<host> "cd /path/to/project && git pull && docker-compose build && docker-compose up -d"
```

- [ ] **步骤 5：验证部署**

```bash
curl http://<host>:23457/api/healthz | jq .
curl http://<host>:23457/metrics | head -20
```

预期：healthz 返回 `{"status":"ok"}`，metrics 返回 Prometheus 格式指标。

- [ ] **步骤 6：前端验证**

打开浏览器访问 `http://<host>:23457/`，确认控制台正常加载，语言选择器可见。