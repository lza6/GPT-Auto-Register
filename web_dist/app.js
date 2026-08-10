const API = '';
// 内部工具：无管理密钥鉴权
let pollTimer = null;
let showPasswords = false;
let accPage = 0;
let emPage = 0;
let lastLogId = 0;
let taskRunning = false;
let accSearchTimer = null;
let emSearchTimer = null;
const ACC_PAGE_SIZE = 50;
const EM_PAGE_SIZE = 50;

// v3.4 B10: localStorage 持久化
function saveState() {
  try {
    const state = {
      activeTab: document.querySelector('.tab.active')?.textContent?.trim()?.toLowerCase()?.replace(/[^\w]/g, '') || 'accounts',
      accSearch: document.getElementById('accSearch')?.value || '',
      emSearch: document.getElementById('emSearch')?.value || '',
      accStatus: document.getElementById('accStatus')?.value || '',
      emStatus: document.getElementById('emStatus')?.value || '',
      accPage: accPage,
      emPage: emPage,
    };
    localStorage.setItem('gpt-reg-ui-state', JSON.stringify(state));
  } catch (e) { /* ignore */ }
}

function loadState() {
  try {
    const raw = localStorage.getItem('gpt-reg-ui-state');
    if (!raw) return;
    const state = JSON.parse(raw);
    if (state.accSearch !== undefined) document.getElementById('accSearch').value = state.accSearch;
    if (state.emSearch !== undefined) document.getElementById('emSearch').value = state.emSearch;
    if (state.accStatus !== undefined) document.getElementById('accStatus').value = state.accStatus;
    if (state.emStatus !== undefined) document.getElementById('emStatus').value = state.emStatus;
    if (state.accPage !== undefined) accPage = state.accPage;
    if (state.emPage !== undefined) emPage = state.emPage;
    return state.activeTab || 'accounts';
  } catch (e) { return 'accounts'; }
}

// 搜索防抖：避免每敲一个字符就发一次请求
function onAccSearch() { accPage = 0; saveState(); clearTimeout(accSearchTimer); accSearchTimer = setTimeout(() => loadAccounts(), 300); }
function onEmSearch() { emPage = 0; saveState(); clearTimeout(emSearchTimer); emSearchTimer = setTimeout(() => loadEmails(), 300); }

function escapeHtml(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  })[c]);
}

// ── 鉴权（v3.1 审计补）：服务端配置真实 auth_key 后，前端须携带 X-Auth-Key。
// 密钥存 localStorage；收到 401 时弹窗收集一次并重试。未配置（占位符）时后端全部放行，无需密钥。
function getAuthKey() { try { return localStorage.getItem('gpt-reg-auth-key') || ''; } catch (e) { return ''; } }
function setAuthKey(k) { try { k ? localStorage.setItem('gpt-reg-auth-key', k) : localStorage.removeItem('gpt-reg-auth-key'); } catch (e) {} }

function promptAuthKey() {
  return new Promise(resolve => {
    openModal('🔐 需要管理密钥', `
      <div class="hint">服务端已开启鉴权（auth_enforced + 真实 auth_key）。请输入 config.json 中的 <code>auth_key</code> 以继续。</div>
      <div class="form-group"><label>管理密钥 (auth_key)</label>
        <input type="password" id="authKeyInput" placeholder="输入 auth_key" autocomplete="off"></div>
      <div class="modal-actions">
        <button class="btn btn-ghost" id="authKeyCancel">取消</button>
        <button class="btn btn-primary" id="authKeyOk">确定</button>
      </div>`);
    const done = v => { window.__authKeyResolve = null; closeModal(); resolve(v); };
    window.__authKeyResolve = done;
    document.getElementById('authKeyOk').onclick = () => done(document.getElementById('authKeyInput').value.trim());
    document.getElementById('authKeyCancel').onclick = () => done('');
    setTimeout(() => { const i = document.getElementById('authKeyInput'); if (i) i.focus(); }, 50);
  });
}

async function api(path, opts = {}, _retried = false) {
  // 内部工具：后端 auth_key 为占位符时全部放行；配置真实密钥后前端携带 X-Auth-Key
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  const key = getAuthKey();
  if (key) headers['X-Auth-Key'] = key;
  const resp = await fetch(API + '/api' + path, { ...opts, headers });
  if (resp.status === 401 && !_retried) {
    const entered = await promptAuthKey();
    if (entered) { setAuthKey(entered); return api(path, opts, true); }
  }
  if (!resp.ok) {
    let msg = 'HTTP ' + resp.status;
    try { const j = await resp.json(); msg = j.detail || msg; } catch (e) {}
    if (resp.status === 401) setAuthKey('');  // 密钥错误则清除，下次重新询问
    throw new Error(msg);
  }
  return resp.json();
}

function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.display = 'block';
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.style.display = 'none', 2500);
}

// v3.4 T89：三态 toast（info/success/error），error 红色更久可关闭
function toastMsg(msg, level = 'info') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast toast-' + level;
  t.style.display = 'block';
  clearTimeout(t._timer);
  const duration = level === 'error' ? 5000 : 2500;
  t._timer = setTimeout(() => { t.style.display = 'none'; t.className = 'toast'; }, duration);
}

// v3.4 T89：withBusy 防连点封装
async function withBusy(btn, busyText, fn) {
  if (!btn) return fn();
  if (btn.disabled) return;
  const orig = btn.textContent;
  btn.disabled = true; btn.textContent = busyText;
  try { return await fn(); }
  finally { btn.disabled = false; btn.textContent = orig; }
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text || '');
    toast('已复制');
  } catch(e) {
    const ta = document.createElement('textarea');
    ta.value = text || ''; document.body.appendChild(ta);
    ta.select(); document.execCommand('copy'); ta.remove();
    toast('已复制');
  }
}

// ── 弹窗 ──
function openModal(title, bodyHtml) {
  document.getElementById('modalTitle').innerHTML = '<h3>' + title + '</h3>';
  document.getElementById('modalBody').innerHTML = bodyHtml;
  document.getElementById('overlay').classList.add('show');
}
function closeModal() {
  document.getElementById('overlay').classList.remove('show');
}

// ── 状态轮询 ──
async function refreshStatus() {
  try {
    const data = await api('/register/status');
    const badge = document.getElementById('statusBadge');
    if (data.is_running && !data.is_paused) {
      badge.textContent = '运行中'; badge.className = 'badge running';
    } else if (data.is_paused) {
      badge.textContent = '已暂停'; badge.className = 'badge paused';
    } else {
      badge.textContent = '已停止'; badge.className = 'badge stopped';
    }
    document.getElementById('proxyInfo').textContent = '代理: ' + (data.proxy_count || 0) + ' 条';
    document.getElementById('btnStart').disabled = data.is_running;
    document.getElementById('btnPause').disabled = !data.is_running || data.is_paused;
    document.getElementById('btnResume').disabled = !data.is_running || !data.is_paused;
    document.getElementById('btnStop').disabled = !data.is_running;
    taskRunning = !!data.is_running;
    updateStats(data.stats);
    updateTask(data.task);
  } catch (e) { console.error(e); }
}

function updateTask(task) {
  const bar = document.getElementById('progressBar');
  const fill = document.getElementById('progressFill');
  const text = document.getElementById('progressText');
  if (!task || !task.total) {
    bar.style.display = 'none'; fill.style.width = '0';
    text.textContent = '';
    return;
  }
  const done = (task.completed || 0) + (task.failed || 0) + (task.skipped || 0);
  const pct = task.total ? Math.min(100, Math.round(done / task.total * 100)) : 0;
  bar.style.display = 'block';
  fill.style.width = pct + '%';
  const statusText = task.status === 'running' ? '运行中'
    : task.status === 'completed' ? '已完成'
    : task.status === 'interrupted' ? '已中断'
    : task.status === 'stopped' ? '已停止'
    : (task.status || '');
  // v3.4 T94：速率/ETA——前端本地算，零后端改动
  const now = Date.now();
  if (!window._taskSnap) window._taskSnap = { done: 0, ts: now };
  const snap = window._taskSnap;
  const elapsed = (now - snap.ts) / 1000 / 60;  // 分钟
  let rateText = '';
  if (elapsed > 0.1 && done > snap.done) {
    const rate = (done - snap.done) / elapsed;  // 条/分钟
    const eta = rate > 0 ? Math.round((task.total - done) / rate) : 0;
    rateText = ` · 速率 ${rate.toFixed(1)}条/分` + (eta > 0 ? ` · 预计剩余 ${eta}分钟` : '');
    // 60s 无推进时进度条变红
    if (now - snap.ts > 60000 && (done - snap.done) === 0) {
      fill.style.background = 'var(--red)';
    } else {
      fill.style.background = '';
    }
  }
  window._taskSnap = { done, ts: now };
  text.textContent = `${statusText} · ${done}/${task.total}（成功 ${task.completed||0} · 失败 ${task.failed||0} · 跳过 ${task.skipped||0}）${rateText}`;
}

function updateStats(s) {
  if (!s) return;
  // 移除骨架屏（数据首次加载后）
  document.querySelectorAll('.stat-card .value.skeleton').forEach(el => {
    el.classList.remove('skeleton');
    el.style.width = ''; el.style.height = '';
  });
  document.getElementById('sEmailsTotal').textContent = s.emails_total || 0;
  document.getElementById('sEmailsPending').textContent = s.emails_pending || 0;
  document.getElementById('sAccountsSuccess').textContent = s.accounts_success || 0;
  document.getElementById('sAccountsFailed').textContent = s.accounts_failed || 0;
  document.getElementById('sAccountsSkipped').textContent = s.accounts_skipped || 0;
  document.getElementById('sAccountsTotal').textContent = s.accounts_total || 0;
  // v3.0 A4A6 / v3.1 T8：失败分级诊断（分类徽章 + 一句话引导）
  const diag = document.getElementById('failureDiagnosis');
  if (diag) {
    const ft = s.last_task_failure_types || {};
    const hint = s.failure_diagnosis || '';
    if (Object.keys(ft).length) {
      diag.innerHTML = renderFailureDiagnosis(ft, hint);
      diag.style.display = 'block';
    } else {
      diag.style.display = 'none';
      diag.innerHTML = '';
    }
  }
  // v3.4 T94：转化漏斗（邮箱 → 已消耗 → 成功 → 有token）
  updateFunnel(s);
  // v3.4 T94：c2api 就绪率
  updateReadiness();
}

// v3.1 T8：把失败原因分布渲染为分类徽章卡片 + 占比 + 引导语
function renderFailureDiagnosis(ft, hint) {
  const total = Object.values(ft).reduce((a, b) => a + (b || 0), 0);
  if (!total) return '';
  const labels = { risk_control: '⚠️ 风控', otp_timeout: '⏱️ 验证码超时', network: '🌐 网络', server_5xx: '🔥 5xx', unknown: '❓ 未知' };
  const cls = { risk_control: 'failed', otp_timeout: 'pending', network: 'cf', server_5xx: 'cf', unknown: 'skipped' };
  const badges = Object.entries(ft).sort((a, b) => b[1] - a[1]).map(([k, v]) => {
    const pct = Math.round(v / total * 100);
    return `<span class="tag ${cls[k] || 'skipped'}" style="margin:2px 6px 2px 0">${labels[k] || escapeHtml(k)} ${v} (${pct}%)</span>`;
  }).join('');
  return `<div style="margin-bottom:6px"><b>失败分类</b>（共 ${total}） ${badges}</div>`
    + (hint ? `<div>${escapeHtml(hint)}</div>` : '');
}

// v3.4 T94：c2api 就绪率聚合
async function updateReadiness() {
  const grid = document.getElementById('readinessGrid');
  const fullyReady = document.getElementById('sFullyReady');
  if (!grid || !fullyReady) return;
  try {
    const d = await api('/stats/account-readiness');
    if (d.total_success > 0) {
      const pct = Math.round(d.fully_ready / d.total_success * 100);
      fullyReady.textContent = `${d.fully_ready}/${d.total_success} (${pct}%)`;
      grid.style.display = 'grid';
    }
  } catch (e) { /* 不阻断 */ }
}

// v3.4 T94：转化漏斗条（邮箱 → 已消耗 → 成功 → 有token）
function updateFunnel(s) {
  const bar = document.getElementById('funnelBar');
  const seg = document.getElementById('funnelSegments');
  if (!bar || !seg) return;
  const total = s.emails_total || 0;
  if (total === 0) { bar.style.display = 'none'; return; }
  const used = (s.emails_total || 0) - (s.emails_pending || 0);
  const success = s.accounts_success || 0;
  const withToken = s.accounts_total || 0;
  const segments = [
    { label: '邮箱', val: total, color: 'var(--accent)' },
    { label: '已消耗', val: used, color: 'var(--yellow)' },
    { label: '成功', val: success, color: 'var(--green)' },
    { label: '有token', val: withToken, color: 'var(--blue, #3b82f6)' },
  ];
  const maxVal = Math.max(total, 1);
  seg.innerHTML = segments.map(seg => {
    const pct = Math.round(seg.val / maxVal * 100);
    const conv = total > 0 ? Math.round(seg.val / total * 100) + '%' : '';
    return `<div style="flex:${pct};background:${seg.color};padding:4px 8px;border-radius:4px;text-align:center;color:#fff;min-width:60px;font-size:11px">
      ${seg.label}<br><strong>${seg.val}</strong> ${conv}</div>`;
  }).join('');
  bar.style.display = 'block';
}

function copyFrom(el) {
  copyText(el.dataset.full);
}

function pwCell(v) {
  if (!v) return '-';
  return showPasswords
    ? `<span class="copyable mono" title="点击复制" data-full="${escapeHtml(v)}" onclick="copyFrom(this)">${escapeHtml(v)}</span>`
    : '<span class="pw-mask">••••••</span>';
}

function tokenCell(v) {
  if (!v) return '-';
  return `<span class="mono ellipsis copyable" title="${escapeHtml(v)}" data-full="${escapeHtml(v)}" onclick="copyFrom(this)">${escapeHtml(v.slice(0,28))}…</span>`;
}

// v3.1.2：chatgpt2api 就绪度徽章——一眼看清四项关键凭证是否齐备
// AT=access_token, RT=openai_refresh_token, 密=OpenAI密码, 取件=mail_credential(client_id+微软RT)
function c2apiReadyCell(a) {
  const items = [
    ['AT', !!(a.access_token && String(a.access_token).startsWith('eyJ')), 'access_token'],
    ['RT', !!a.openai_refresh_token, 'openai_refresh_token'],
    ['密', !!(a.openai_password || a.password), 'OpenAI密码'],
    ['取件', !!(a.client_id && a.refresh_token), '取件凭证(client_id+微软refresh_token)'],
  ];
  return items.map(([label, ok, tip]) =>
    `<span class="tag ${ok ? 'success' : 'failed'}" title="${tip}${ok ? '：已具备' : '：缺失'}" style="margin-right:2px">${label}${ok ? '✓' : '✗'}</span>`
  ).join('');
}

// ── 导入邮箱 ──
function openImport() {
  const src = document.getElementById('cfgEmailSource').value || '';
  openModal('📥 导入邮箱', `
    <div class="form-group"><label>邮箱源 URL (91kami)</label>
      <input type="url" id="impUrl" placeholder="https://mai.91kami.com/cpd/..." value="${escapeHtml(src)}"></div>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeModal()">取消</button>
      <button class="btn btn-success" id="btnImportModal" onclick="importEmails()">开始导入</button>
    </div>`);
}
async function importEmails() {
  const url = document.getElementById('impUrl').value.trim();
  const btnModal = document.getElementById('btnImportModal');
  const btnHead = document.getElementById('btnImport');
  if (btnModal) btnModal.disabled = true;
  btnHead.disabled = true; btnHead.textContent = '导入中...';
  try {
    const data = await api('/register/import-emails', { method: 'POST', body: JSON.stringify({ source_url: url }) });
    closeModal();
    toast(`导入完成: 新增 ${data.data.inserted}, 跳过 ${data.data.skipped}`);
    refreshStatus();
  } catch (e) { toastMsg('导入失败: ' + e.message, 'error'); }
  finally {
    if (btnModal) btnModal.disabled = false;
    btnHead.disabled = false; btnHead.textContent = '📥 导入邮箱';
  }
}

// ── 手动添加邮箱 ──
function openManualAdd() {
  openModal('✍️ 手动添加邮箱', `
    <div class="hint">每行一个邮箱，格式：<code>邮箱----密码----client_id----refresh_token</code>。也可以只填邮箱（密码等留空）。</div>
    <div class="form-group"><label>目标平台（同邮箱可在不同平台各注册一次）</label>
      <select id="manualPlatform" style="width:100%">
        <option value="chatgpt">chatgpt（ChatGPT/OpenAI）</option>
      </select></div>
    <div class="form-group"><label>邮箱列表</label>
      <textarea id="manualEmails" style="min-height:220px" placeholder="xxx@outlook.com----密码----client_id----refresh_token"></textarea></div>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeModal()">取消</button>
      <button class="btn btn-success" id="btnManualAddModal" onclick="submitManualAdd()">添加</button>
    </div>`);
  // 动态填充平台选项（含已注册过的平台）
  api('/emails/platforms').then(d => {
    const sel = document.getElementById('manualPlatform');
    if (!sel || !d.platforms) return;
    sel.innerHTML = d.platforms.map(p =>
      `<option value="${escapeHtml(p)}">${escapeHtml(p)}</option>`).join('');
  }).catch(() => {});
}
async function submitManualAdd() {
  const text = document.getElementById('manualEmails').value;
  if (!text.trim()) { toastMsg('内容为空', 'error'); return; }
  const platform = (document.getElementById('manualPlatform') || {}).value || 'chatgpt';
  const btn = document.getElementById('btnManualAddModal');
  if (btn) btn.disabled = true;
  try {
    const data = await api('/emails/manual-add', { method: 'POST', body: JSON.stringify({ text, platform }) });
    closeModal();
    toast(`已添加 ${data.inserted} 个邮箱, 跳过 ${data.skipped} 个`);
    refreshStatus(); loadEmails(); loadPlatforms();
  } catch (e) { toastMsg('添加失败: ' + e.message, 'error'); }
  finally { if (btn) btn.disabled = false; }
}

// 平台切换下拉：从 /api/emails/platforms 拉所有平台
async function loadPlatforms() {
  try {
    const d = await api('/emails/platforms');
    const sel = document.getElementById('emPlatform');
    if (!sel || !d.platforms) return;
    const cur = sel.value;
    sel.innerHTML = '<option value="">全部平台</option>' + d.platforms.map(p => {
      const st = (d.stats || {})[p] || {};
      const label = `${p} (${st.used || 0}/${st.total || 0})`;
      return `<option value="${escapeHtml(p)}">${escapeHtml(label)}</option>`;
    }).join('');
    sel.value = cur;
  } catch (e) { /* 平台列表加载失败不阻断邮箱池 */ }
}

// ── 注册控制 ──
async function startRegister() {
  const btn = document.getElementById("btnStart");
  await withBusy(btn, "启动中...", async () => {
    const count = parseInt(document.getElementById("cfgBatchSize").value) || 0;
    const data = await api("/register/start", { method: "POST", body: JSON.stringify({ count }) });
    toastMsg(`注册任务已启动，共 ${data.total}/${data.pending_total} 个邮箱`, "success");
    await refreshStatus();
  });
}
async function controlRegister(action) {
  await withBusy(null, '', async () => {
    await api('/register/control', { method: 'POST', body: JSON.stringify({ action }) });
    refreshStatus();
  });
}

// v3.4 T78：重试失败/CF 账号
async function retryFailed() {
  const btn = document.getElementById('btnRetryFailed');
  try {
    await withBusy(btn, '重试中...', async () => {
      const d = await api('/register/retry-failed', { method: 'POST', body: '{}' });
      toastMsg(`已回置 ${d.requeued} 个邮箱为待注册，可点「开始注册」重试`, 'success');
      refreshStatus();
    });
  } catch (e) { toastMsg('重试失败: ' + e.message, 'error'); }
}

// ── 导出 / 清空 ──
async function exportTokens() {
  const btn = document.getElementById('btnExportTokens') || document.querySelector('button[onclick="exportTokens()"]');
  await withBusy(btn, '导出中...', async () => {
    const data = await api('/register/accounts/export');
    if (data.count === 0) { toast('暂无 token'); return; }
    await copyText(data.tokens.join('\n'));
    toast(`已复制 ${data.count} 个 token 到剪贴板`);
  });
}

// v3.1.2：一键补齐 Token（为有 openai_refresh_token 但缺有效 access_token 的账号刷新补齐）
async function replenishTokens() {
  if (!confirm('为有 refresh_token 但缺 access_token 的账号刷新补齐？\n（会真实调用 OpenAI 刷新，轮换的新 RT 自动落库）')) return;
  const btn = document.querySelector('button[onclick="replenishTokens()"]');
  await withBusy(btn, '补齐中...', async () => {
    const d = await api('/register/replenish-tokens', { method: 'POST', body: '{}' });
    toast(`补齐完成：成功 ${d.replenished} · 失败 ${d.failed} · 无RT需重新注册 ${d.need_reregister || 0}`);
    loadAccounts(); refreshStatus();
  });
}

// v3.1.2：推送成功账号到 chatgpt2api 账号池（含三件套 + 取件凭证 mail_credential）
async function pushToChatgpt2api() {
  if (!confirm('把所有成功账号推送到 chatgpt2api 账号池？\n（含 access_token/refresh_token/id_token + OpenAI密码 + 取件凭证）')) return;
  const btn = document.querySelector('button[onclick="pushToChatgpt2api()"]');
  await withBusy(btn, '推送中...', async () => {
    const d = await api('/register/push-chatgpt2api', { method: 'POST', body: '{}' });
    if (d.success === false) { toastMsg('推送失败: ' + (d.error || '未知错误'), 'error'); return; }
    toast(`已推送 ${d.pushed} 个账号到 chatgpt2api（HTTP ${d.status}）`);
  });
}

function openClear() {
  openModal('🗑️ 一键清空库', `
    <div class="warn">⚠️ 该操作将<b>永久删除</b>：注册记录（accounts）、邮箱池（emails）、任务记录（tasks）。运行日志将保留。建议先「导出 Token」备份。</div>
    <div class="form-group"><label>输入 <b>clear</b> 确认清空</label>
      <input type="text" id="clearConfirm" placeholder="输入 clear"></div>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeModal()">取消</button>
      <button class="btn btn-danger" onclick="confirmClear()" id="btnConfirmClear">确认清空</button>
    </div>`);
}
async function confirmClear() {
  const v = document.getElementById('clearConfirm').value.trim();
  if (v !== 'clear') { toast('请先输入 clear 确认'); return; }
  const btn = document.getElementById('btnConfirmClear');
  btn.disabled = true;
  try {
    const data = await api('/register/clear', { method: 'POST', body: JSON.stringify({ confirm: v }) });
    closeModal();
    toast(`已清空: 注册记录 ${data.deleted.accounts}, 邮箱 ${data.deleted.emails}, 任务 ${data.deleted.tasks}`);
    refreshStatus(); loadAccounts(); loadEmails();
  } catch (e) { toastMsg('清空失败: ' + e.message, 'error'); }
  btn.disabled = false;
}

// ── 注册记录 ──
async function loadAccounts(manual) {
  const btn = manual ? document.querySelector('#tab-accounts button[onclick*="loadAccounts"]') : null;
  await withBusy(btn, '加载中...', async () => {
    // 显示骨架屏
    const skel = document.getElementById('accountsSkeleton');
    const empty = document.getElementById('accountsEmpty');
    if (skel) skel.style.display = 'block';
    if (empty) empty.style.display = 'none';
    try {
      const search = document.getElementById('accSearch').value.trim();
      const status = document.getElementById('accStatus').value;
      const params = new URLSearchParams({ limit: ACC_PAGE_SIZE, offset: accPage * ACC_PAGE_SIZE });
      if (status) params.set('status', status);
      if (search) params.set('search', search);
      saveState();
      const data = await api('/register/accounts?' + params.toString());
      const tbody = document.getElementById('accountsBody');
      const empty = document.getElementById('accountsEmpty');
      document.getElementById('accCount').textContent = data.total || 0;
      const pageCount = Math.max(1, Math.ceil((data.total || 0) / ACC_PAGE_SIZE));
      document.getElementById('accPageInfo').textContent = `第 ${accPage + 1}/${pageCount} 页`;
      if (!data.accounts || data.accounts.length === 0) {
        if (accPage > 0) { accPage = 0; return loadAccounts(manual); }
        tbody.innerHTML = ''; empty.style.display = 'block'; return;
      }
      empty.style.display = 'none';
      tbody.innerHTML = data.accounts.map((a, i) => {
        const statusTag =
          a.status === 'success' ? '<span class="tag success">成功</span>'
          : a.status === 'failed' ? '<span class="tag failed">失败</span>'
          : a.status === 'cf_blocked' ? '<span class="tag cf">CF拦截</span>'
          : a.status === 'skipped' ? '<span class="tag skipped">跳过</span>'
          : a.status === 'success_no_token' ? '<span class="tag success">成功(无token)</span>'
          : '<span class="tag pending">待处理</span>';
        const time = a.registered_at ? new Date(a.registered_at * 1000).toLocaleString() : '-';
        return `<tr>
          <td>${accPage * ACC_PAGE_SIZE + i + 1}</td>
          <td class="mono">${escapeHtml(a.email)}</td>
          <td>${pwCell(a.openai_password || a.password)}</td>
          <td>${escapeHtml(a.name || '-')}</td>
          <td>${escapeHtml(a.birthdate || '-')}</td>
          <td>${statusTag}</td>
          <td class="mono">${escapeHtml(a.proxy || '直连')}</td>
          <td>${tokenCell(a.access_token)}</td>
          <td style="white-space:nowrap">${c2apiReadyCell(a)}</td>
          <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis">${escapeHtml(a.error || '-')}</td>
          <td style="white-space:nowrap">${time}</td>
        </tr>`;
      }).join('');
      if (skel) skel.style.display = 'none';
    } catch (e) { console.error(e); if (manual) toast('加载注册记录失败: ' + e.message); if (skel) skel.style.display = 'none'; }
  });
}

// ── 邮箱池 ──
async function loadEmails(manual) {
  const btn = manual ? document.querySelector('#tab-emails button[onclick*="loadEmails"]') : null;
  await withBusy(btn, '加载中...', async () => {
    // 显示骨架屏
    const skel = document.getElementById('emailsSkeleton');
    const empty = document.getElementById('emailsEmpty');
    if (skel) skel.style.display = 'block';
    if (empty) empty.style.display = 'none';
    try {
      const search = document.getElementById('emSearch').value.trim();
      const status = document.getElementById('emStatus').value;
      const platform = (document.getElementById('emPlatform') || {}).value || '';
      const params = new URLSearchParams({ limit: EM_PAGE_SIZE, offset: emPage * EM_PAGE_SIZE });
      if (status) params.set('status', status);
      if (platform) params.set('platform', platform);
      if (search) params.set('search', search);
      saveState();
      const data = await api('/emails/?' + params.toString());
      const tbody = document.getElementById('emailsBody');
      const empty = document.getElementById('emailsEmpty');
      const pageCount = Math.max(1, Math.ceil((data.total || 0) / EM_PAGE_SIZE));
      document.getElementById('emPageInfo').textContent = `第 ${emPage + 1}/${pageCount} 页`;
      if (!data.emails || data.emails.length === 0) {
        if (emPage > 0) { emPage = 0; return loadEmails(manual); }
        tbody.innerHTML = ''; empty.style.display = 'block'; return;
      }
      empty.style.display = 'none';
      tbody.innerHTML = data.emails.map((e, i) => {
        const statusTag = e.status === 'pending' ? '<span class="tag pending">待注册</span>'
          : e.status === 'used' ? '<span class="tag used">已使用</span>'
          : '<span class="tag skipped">' + escapeHtml(e.status) + '</span>';
        const time = e.created_at ? new Date(e.created_at * 1000).toLocaleString() : '-';
        return `<tr>
          <td>${emPage * EM_PAGE_SIZE + i + 1}</td>
          <td class="mono">${escapeHtml(e.email)}</td>
          <td>${pwCell(e.password)}</td>
          <td>${tokenCell(e.client_id)}</td>
          <td>${tokenCell(e.refresh_token)}</td>
          <td>${statusTag}</td>
          <td style="white-space:nowrap">${time}</td>
          <td><button class="btn btn-danger btn-sm" onclick="deleteEmail(${e.id})">删除</button></td>
        </tr>`;
      }).join('');
      if (skel) skel.style.display = 'none';
    } catch (e) { console.error(e); if (manual) toast('加载邮箱失败: ' + e.message); if (skel) skel.style.display = 'none'; }
  });
}

async function deleteEmail(id) {
  if (!confirm('确定删除该邮箱？')) return;
  try {
    await api('/emails/' + id, { method: 'DELETE' });
    toast('已删除');
    loadEmails(); refreshStatus();
  } catch (e) { toastMsg('删除失败: ' + e.message, 'error'); }
}

async function clearEmails() {
  if (!confirm('确定清空整个邮箱池？')) return;
  await withBusy(document.querySelector('button[onclick="clearEmails()"]'), '清空中...', async () => {
    const data = await api('/emails/clear', { method: 'POST', body: '{}' });
    toast('已清空 ' + data.deleted + ' 个邮箱');
    loadEmails(); refreshStatus();
  });
}

// ── 代理池 ──
let proxyHealthMap = {};  // line(已 trim) -> {ok, error}（v3.1 T3）

async function loadProxies(manual) {
  const btn = manual ? document.querySelector('button[onclick*="loadProxies"]') : null;
  const fn = async () => {
    const data = await api('/proxies/');
    const body = document.getElementById('proxiesBody');
    const empty = document.getElementById('proxiesEmpty');
    document.getElementById('proxyCount').innerHTML =
      '共 <b>' + data.count + '</b> 条配置（有效 ' + data.active_count + ' 条）';
    const lines = data.content.split('\n').filter(l => l.trim() && !l.trim().startsWith('#'));
    if (lines.length === 0) { body.innerHTML = ''; empty.style.display = 'block'; return; }
    empty.style.display = 'none';
    body.innerHTML = lines.slice(0, 100).map((l, i) => {
      const key = l.trim();
      const type = /^[^:]+:[^:]+:[^:]+-[^:]+:[^:]+-/.test(key) ? '<span class="tag cf">kookeey动态</span>' : '<span class="tag success">HTTP</span>';
      const h = proxyHealthMap[key];
      const rowCls = (h && !h.ok) ? ' class="failed"' : '';
      return `<tr${rowCls}><td>${i + 1}</td><td class="mono">${escapeHtml(l)}</td><td>${type}</td><td>${renderProxyHealth(key)}</td></tr>`;
    }).join('') + (lines.length > 100 ? `<tr><td colspan="4" class="muted">… 还有 ${lines.length - 100} 条未显示，点击「编辑代理池」查看全部</td></tr>` : '');
  };
  await withBusy(btn, '刷新中...', fn);
}

// v3.4 T88：批量探测代理池可用性（真实 HTTP 出口探测，含出口 IP/国家/延迟）
async function checkProxyHealth() {
  toast('探测中...（HTTP 出口探测，8s 超时，并发 10）');
  try {
    const d = await api('/proxies/health');
    proxyHealthMap = {};
    (d.results || []).forEach(r => { proxyHealthMap[String(r.line).trim()] = r; });
    document.getElementById('proxyHealthSummary').innerHTML =
      '可用 <b style="color:var(--green)">' + d.ok + '</b> / 失效 <b style="color:var(--red)">' + d.failed + '</b> / 总计 ' + d.total
      + (d.blacklist_size ? ' · 黑名单 <b style="color:var(--red)">' + d.blacklist_size + '</b>' : '');
    loadProxies();
  } catch (e) { toast('代理探测失败: ' + e.message); }
}

function renderProxyHealth(line) {
  const h = proxyHealthMap[line];
  if (!h) return '<span class="muted">未探测</span>';
  if (h.ok) {
    let label = '✅ ';
    if (h.exit_ip) {
      label += h.exit_ip;
      if (h.country) label += ' (' + h.country + ')';
      if (h.latency_ms) label += ' ' + h.latency_ms + 'ms';
    } else {
      label += '可达(TCP)';
    }
    return `<span class="tag success" title="出口IP: ${h.exit_ip || 'N/A'}">${label}</span>`;
  }
  return `<span class="tag danger" title="${escapeHtml(h.error)}">❌ ${escapeHtml(h.error)}</span>`;
}

function openProxyEditor() {
  api('/proxies/').then(data => {
    openModal('✏️ 编辑代理池', `
      <div class="hint">每行一个代理。kookeey：<code>gate.kookeey.info:1000:用户ID-子用户:密码-国家</code>；通用：<code>ip:port:user:pass</code> 或 <code>http://ip:port</code>。保存后立即生效。</div>
      <div class="form-group"><label>代理列表（当前 ${data.count} 条）</label>
        <textarea id="proxyContent" style="min-height:280px">${escapeHtml(data.content)}</textarea></div>
      <div class="modal-actions">
        <button class="btn btn-ghost" onclick="clearProxies()">清空</button>
        <button class="btn btn-ghost" onclick="closeModal()">取消</button>
        <button class="btn btn-success" onclick="saveProxies()">保存</button>
      </div>`);
  }).catch(e => toastMsg('加载代理池失败: ' + e.message, 'error'));
}
async function saveProxies() {
  const content = document.getElementById('proxyContent').value;
  try {
    const data = await api('/proxies/', { method: 'POST', body: JSON.stringify({ content }) });
    closeModal();
    toast('代理池已保存: ' + data.count + ' 条');
    loadProxies(); refreshStatus();
  } catch (e) { toastMsg('保存失败: ' + e.message, 'error'); }
}
async function clearProxies() {
  if (!confirm('确定清空代理池？')) return;
  try {
    const data = await api('/proxies/', { method: 'POST', body: JSON.stringify({ content: '' }) });
    closeModal();
    toast('代理池已清空');
    loadProxies(); refreshStatus();
  } catch (e) { toastMsg('失败: ' + e.message, 'error'); }
}

// ── 日志（v3.4 T95：SSE 实时推送，轮询降级兜底；级别筛选/搜索/暂停滚动/导出） ──
let logSseSource = null;
let logSseFailed = 0;
let logPaused = false;

async function startLogSse() {
  if (logSseSource) return;
  logSseFailed = 0;
  try {
    const key = getAuthKey();
    const resp = await fetch('/api/logs/stream', {
      headers: key ? { 'X-Auth-Key': key } : {},
    });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    logSseSource = true;

    const readLoop = async () => {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n\n');
        buf = lines.pop() || '';
        for (const block of lines) {
          if (!block.startsWith('data: ')) continue;
          const payload = block.slice(6);
          let log;
          try { log = JSON.parse(payload); } catch (e) { continue; }
          if (log.error && logSseFailed++ >= 3) {
            // SSE 连续失败，回退轮询
            logSseSource = null;
            startPollingLogs();
            return;
          }
          appendLogEntry(log);
        }
      }
      logSseSource = null;
    };
    readLoop().catch(() => { logSseSource = null; });
  } catch (e) {
    logSseFailed++;
    if (logSseFailed >= 3) {
      logSseSource = null;
      startPollingLogs();
    }
  }
}

function appendLogEntry(l) {
  if (!l || !l.message) return;
  // 级别过滤
  const levelFilter = document.getElementById('logLevelFilter');
  if (levelFilter && levelFilter.value && levelFilter.value !== 'all' && l.level !== levelFilter.value) return;
  // 搜索过滤
  const searchInput = document.getElementById('logSearchInput');
  if (searchInput && searchInput.value.trim()) {
    const q = searchInput.value.trim().toLowerCase();
    if (!l.message.toLowerCase().includes(q)) return;
  }

  const box = document.getElementById('logBox');
  if (!box) return;
  const time = new Date((l.created_at || 0) * 1000).toLocaleTimeString();
  const cls = l.level === 'error' ? 'log-error' : l.level === 'warning' ? 'log-warning' : 'log-info';
  const div = document.createElement('div');
  div.className = cls;
  div.innerHTML = `<span class="log-time">[${time}]</span> ${escapeHtml(l.message)}`;
  box.appendChild(div);

  // 裁剪上限
  const MAX_LOG_NODES = 1500;
  while (box.childElementCount > MAX_LOG_NODES) box.removeChild(box.firstChild);

  // 自动滚动（暂停时不动）
  if (!logPaused) box.scrollTop = box.scrollHeight;
}

async function loadLogs(force) {
  // v3.4 T95：如有 SSE 则不再轮询；否则回退到此轮询逻辑
  if (logSseSource) {
    if (force) {
      // 手动刷新：重置后重新连接 SSE
      stopLogSse();
      startLogSse();
    }
    return;
  }
  if (force) lastLogId = 0;
  try {
    const url = '/logs?limit=1000' + (lastLogId ? '&after_id=' + lastLogId : '');
    const data = await api(url);
    const box = document.getElementById('logBox');
    if (!data.logs || data.logs.length === 0) {
      if (!lastLogId) box.innerHTML = '<div class="log-info">暂无日志</div>';
      return;
    }
    if (!lastLogId) box.innerHTML = '';
    data.logs.forEach(l => appendLogEntry(l));
    data.logs.forEach(l => { if (l.id > lastLogId) lastLogId = l.id; });
  } catch (e) { console.error(e); if (force) toast('加载日志失败: ' + e.message); }
}

function stopLogSse() {
  logSseSource = null;
}

function startPollingLogs() {
  // 轮询降级兜底（已有 loadLogs 实现）
}

function toggleLogScroll() {
  logPaused = !logPaused;
  const btn = document.getElementById('btnLogScroll');
  if (btn) btn.textContent = logPaused ? '▶ 继续滚动' : '⏸ 暂停滚动';
}

function exportLogs() {
  const box = document.getElementById('logBox');
  if (!box) return;
  const text = box.innerText;
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'logs.txt';
  a.click();
  URL.revokeObjectURL(a.href);
}

async function clearLogs() {
  if (!confirm('确定清空日志？')) return;
  await withBusy(document.querySelector('button[onclick="clearLogs()"]'), '清空中...', async () => {
    await api('/logs/clear', { method: 'POST', body: '{}' });
    loadLogs(true);
    toast('日志已清空');
  });
}

// ── 设置 ──
async function loadSettings() {
  try {
    const cfg = await api('/settings/config');
    document.getElementById('cfgEmailSource').value = cfg.email_source_url || '';
    document.getElementById('cfgInterval').value = cfg.register_interval_sec || 10;
    document.getElementById('cfgOtpTimeout').value = cfg.otp_wait_timeout_sec || 600;
    document.getElementById('cfgBatchSize').value = cfg.batch_size || 100;
    document.getElementById('cfgC2apiUrl').value = cfg.chatgpt2api_url || 'http://127.0.0.1:23456';
    document.getElementById('cfgC2apiKey').value = cfg.chatgpt2api_admin_key || '';
    document.getElementById('cfgConcurrency').value = cfg.register_concurrency || 1;
    document.getElementById('cfgOtpPoll').value = cfg.otp_poll_interval_sec || 5;
    document.getElementById('cfgEmailApiBase').value = cfg.email_api_base || 'https://app.98faka.top';
    document.getElementById('cfgUserAgent').value = cfg.user_agent || '';
    // 注册方式开关：兼容未配置 / 布尔 / 字符串 'true'/'false'（settings API 存字符串）
    const boolOf = (v, def) => (v === undefined || v === null) ? def
      : (v === true || v === 'true' || v === '1' || v === 1);
    document.getElementById('cfgProtocolFirst').checked = boolOf(cfg.protocol_first, true);
    document.getElementById('cfgUseBrowser').checked = boolOf(cfg.use_browser, true);
  } catch (e) { console.error(e); }
}

async function saveSettings() {
  try {
    const settings = [
      { key: 'email_source_url', value: document.getElementById('cfgEmailSource').value },
      { key: 'register_interval_sec', value: document.getElementById('cfgInterval').value },
      { key: 'otp_wait_timeout_sec', value: document.getElementById('cfgOtpTimeout').value },
      { key: 'batch_size', value: document.getElementById('cfgBatchSize').value },
      { key: 'chatgpt2api_url', value: document.getElementById('cfgC2apiUrl').value },
      { key: 'register_concurrency', value: document.getElementById('cfgConcurrency').value },
      { key: 'otp_poll_interval_sec', value: document.getElementById('cfgOtpPoll').value },
      { key: 'email_api_base', value: document.getElementById('cfgEmailApiBase').value },
      { key: 'user_agent', value: document.getElementById('cfgUserAgent').value },
      { key: 'browser_pool_size', value: document.getElementById('cfgBrowserPoolSize').value },
      { key: 'cf_retry_max', value: document.getElementById('cfgCfRetryMax').value },
    ];
    // 掩码占位符不覆盖真实密钥（服务端已对敏感字段脱敏为 ******）
    const keyValue = document.getElementById('cfgC2apiKey').value;
    if (keyValue && keyValue !== '******') {
      settings.push({ key: 'chatgpt2api_admin_key', value: keyValue });
    }
    // 注册方式：协议优先 / 浏览器兜底（存字符串，后端 _as_bool 解析）
    settings.push({ key: 'protocol_first', value: document.getElementById('cfgProtocolFirst').checked ? 'true' : 'false' });
    settings.push({ key: 'use_browser', value: document.getElementById('cfgUseBrowser').checked ? 'true' : 'false' });
    for (const s of settings) await api('/settings/', { method: 'POST', body: JSON.stringify(s) });
    toast('设置已保存');
  } catch (e) { toastMsg('保存失败: ' + e.message, 'error'); }
}

// ── Token 保鲜巡检仪表盘（v3.1 T2） ──
async function loadTokenHealth() {
  const body = document.getElementById('tokenHealthBody');
  if (!body) return;
  try {
    const d = await api('/stats/token-health');
    if (!d.enabled) {
      body.innerHTML = '<span class="muted">巡检未启用。在「⚙️ 高级设置」开启 <code>token_refresh_enabled</code> 并设置 <code>token_refresh_interval_sec</code>（默认 21600=6h），重启后生效。</span>';
      return;
    }
    const r = d.result || {};
    const human = d.last_scan_at_human || '从未巡检';
    body.innerHTML =
      '<div class="stat-row"><span class="badge running">运行中</span><span class="muted" style="margin-left:12px">上次巡检：' + escapeHtml(human) + '</span></div>'
      + '<div class="stat-row" style="margin-top:8px">'
      + '<span class="tag skipped">扫描 ' + (r.scanned || 0) + '</span>'
      + '<span class="tag success">刷新成功 ' + (r.refreshed || 0) + '</span>'
      + '<span class="tag failed">失败 ' + (r.failed || 0) + '</span>'
      + '</div>';
  } catch (e) { body.innerHTML = '<span class="muted">token 健康度加载失败</span>'; }
}

// ── 导出账号密码 ──
let exportTextCache = '';

function openExportAccounts() {
  openModal('📤 导出账号密码', `
    <div class="hint">账号密码是长期资产。默认导出格式：<code>邮箱----密码</code>（每行一个，按邮箱去重）。点「chatgpt2api 格式」则导出含 access_token 的 JSON。</div>
    <div class="controls" style="margin-bottom:12px">
      <button class="btn btn-success" onclick="loadCredExport()">🔍 账号密码清单</button>
      <button class="btn btn-ghost" onclick="loadC2apiExport()">📤 chatgpt2api 格式</button>
      <button class="btn btn-primary" onclick="copyExportText()">📋 复制</button>
      <button class="btn btn-primary" onclick="downloadExportText()">⬇️ 下载</button>
    </div>
    <div id="exportStats" class="muted" style="margin-bottom:8px"></div>
    <div class="form-group"><label>导出内容</label><textarea id="exportText" readonly style="min-height:240px"></textarea></div>
    <div class="modal-actions"><button class="btn btn-ghost" onclick="closeModal()">关闭</button></div>`);
  loadCredExport();
}

async function loadCredExport() {
  document.getElementById('exportStats').textContent = '正在生成账号密码清单...';
  try {
    const data = await api('/register/export-credentials', { method: 'POST', body: '{}' });
    exportTextCache = data.text || '';
    document.getElementById('exportText').value = exportTextCache;
    document.getElementById('exportStats').textContent = `✅ 共 ${data.total} 个账号密码`;
  } catch (e) {
    document.getElementById('exportStats').textContent = '生成失败: ' + e.message;
  }
}

async function loadC2apiExport() {
  document.getElementById('exportStats').textContent = '正在生成 chatgpt2api 格式...';
  try {
    const data = await api('/register/export-accounts', { method: 'POST', body: '{}' });
    if (data.success === false) {
      // 后端错误时 HTTP 仍 200（约定 success=false），必须检查，避免误报成功/导出空文件
      document.getElementById('exportStats').textContent = '导出失败: ' + (data.error || '未知错误');
      return;
    }
    exportTextCache = JSON.stringify(data.accounts, null, 2);
    document.getElementById('exportText').value = exportTextCache;
    document.getElementById('exportStats').textContent = `✅ chatgpt2api 格式 ${data.accounts.length} 个账号`;
  } catch (e) {
    document.getElementById('exportStats').textContent = '生成失败: ' + e.message;
  }
}

function copyExportText() {
  if (!exportTextCache) { toast('请先点击生成'); return; }
  copyText(exportTextCache);
}

function downloadExportText() {
  if (!exportTextCache) { toast('请先点击生成'); return; }
  const blob = new Blob([exportTextCache], { type: 'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = '账号密码.txt';
  a.click();
  URL.revokeObjectURL(a.href);
}

function openAdvanced() {
  api('/settings/config').then(cfg => {
    const boolOf = (v, def) => (v === undefined || v === null) ? def
      : (v === true || v === 'true' || v === '1' || v === 1);
    // v3.1 审计：数字强转，防配置脏值（含引号）从 value 属性逃逸
    const numVal = (v, def) => { const n = parseInt(v, 10); return Number.isFinite(n) ? n : def; };
    const rows = Object.entries(cfg).map(([k, v]) =>
      `<tr><td class="mono">${escapeHtml(k)}</td><td class="mono">${escapeHtml(v)}</td></tr>`).join('');
    openModal('⚙️ 高级设置', `
      <div class="hint">以下为 v3.0 新增可编辑项，保存后重启生效。完整 config.json 见下方只读表。</div>
      <div class="form-row">
        <div class="form-group"><label>浏览器池大小 (0=不池化)</label>
          <input type="number" id="cfgBrowserPoolSize" value="${numVal(cfg.browser_pool_size, 0)}" min="0"></div>
        <div class="form-group"><label>CF 重试次数 (0=不重试)</label>
          <input type="number" id="cfgCfRetryMax" value="${numVal(cfg.cf_retry_max, 2)}" min="0"></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Token 巡检间隔 (秒, ≥60)</label>
          <input type="number" id="cfgTokenRefreshInterval" value="${numVal(cfg.token_refresh_interval_sec, 21600)}" min="60"></div>
        <div class="form-group"><label>Token 保鲜巡检</label>
          <label class="switch-row" style="margin-top:10px"><input type="checkbox" id="cfgTokenRefresh" ${boolOf(cfg.token_refresh_enabled, false) ? 'checked' : ''}> 启用（定期用 refresh_token 换新 access_token）</label></div>
      </div>
      <div class="warn">⚠️ 浏览器池仅在「固定通用 HTTP 代理」场景开启；kookeey 动态住宅代理每账号独立 IP，池化复用会串 IP，请保持 0。</div>
      <div class="modal-actions">
        <button class="btn btn-ghost" onclick="closeModal()">取消</button>
        <button class="btn btn-primary" onclick="saveAdvanced()">💾 保存高级设置</button>
      </div>
      <div class="hint" style="margin-top:18px">config.json 完整配置（只读）：</div>
      <div style="overflow-x:auto"><table>
        <thead><tr><th>键</th><th>值</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="2" class="muted">空配置</td></tr>'}</tbody>
      </table></div>`);
  }).catch(e => toastMsg('加载高级设置失败: ' + e.message, 'error'));
}

// v3.1 T4：保存高级设置（4 个 v3.0 键，后端按 config_schema 转类型存 config.json）
async function saveAdvanced() {
  try {
    const settings = [
      { key: 'browser_pool_size', value: document.getElementById('cfgBrowserPoolSize').value },
      { key: 'cf_retry_max', value: document.getElementById('cfgCfRetryMax').value },
      { key: 'token_refresh_interval_sec', value: document.getElementById('cfgTokenRefreshInterval').value },
      { key: 'token_refresh_enabled', value: document.getElementById('cfgTokenRefresh').checked ? 'true' : 'false' },
    ];
    for (const s of settings) await api('/settings/', { method: 'POST', body: JSON.stringify(s) });
    closeModal();
    toast('高级设置已保存，重启后生效');
    loadTokenHealth();  // 刷新 token 仪表盘状态
  } catch (e) { toastMsg('保存失败: ' + e.message, 'error'); }
}

// ── 通用 ──
function togglePw() {
  showPasswords = document.getElementById('showPw').checked;
  loadAccounts(); loadEmails();
}

// ── 主题（深浅色） ──
function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  try { localStorage.setItem('gpt-reg-theme', theme); } catch (e) {}
}
function toggleTheme() {
  const cur = document.documentElement.getAttribute('data-theme');
  applyTheme(cur === 'light' ? 'dark' : 'light');
}
// 初始化：优先本地记忆，其次跟随系统
(function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem('gpt-reg-theme'); } catch (e) {}
  const theme = saved || (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
  document.documentElement.setAttribute('data-theme', theme);
})();

function switchTab(name, el) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  if (el) el.classList.add('active');
  document.getElementById('tab-' + name).classList.add('active');
  saveState();
  if (name === 'accounts') loadAccounts();
  else if (name === 'emails') { loadPlatforms(); loadEmails(); }
  else if (name === 'proxies') loadProxies();
  else if (name === 'logs') { loadLogs(true); startLogSse(); }
  else if (name === 'settings') { loadSettings(); loadTokenHealth(); }
}

// ── 轮询（v3.0 P2-3：自适应间隔；降载：页面隐藏暂停；注册记录仅任务运行时刷新；日志增量拉取） ──
function startPolling() {
  if (pollTimer) clearTimeout(pollTimer);
  // 自适应：任务运行时 2s 高频（及时感知状态/日志），停止时 10s 降频（减负载）
  const interval = taskRunning ? 2000 : 10000;
  pollTimer = setTimeout(function poll() {
    if (!document.hidden) {
      refreshStatus();
      if (taskRunning) loadAccounts();
      loadLogs();
    }
    // 递归 setTimeout，下一轮按最新 taskRunning 状态选间隔
    const next = taskRunning ? 2000 : 10000;
    pollTimer = setTimeout(poll, next);
  }, interval);
}
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) {
    refreshStatus(); loadAccounts(); loadLogs(true);
  }
});

// ── 初始化 ──
const savedTab = loadState();
refreshStatus();
loadAccounts();
loadLogs(true);
loadSettings();
startPolling();
// 恢复 tab 选中状态
const tabEl = document.querySelector(`.tab[onclick*="'${savedTab}'"]`);
if (tabEl) {
  switchTab(savedTab, tabEl);
} else {
  // 默认 accounts
  const defaultTab = document.querySelector('.tab');
  if (defaultTab) switchTab('accounts', defaultTab);
}
