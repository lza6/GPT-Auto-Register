import { api } from "./api.js";
import { escapeHtml, withBusy, toast, toastMsg, copyText, openModal, closeModal, saveState } from "./utils.js";
import { copyFrom, pwCell, tokenCell, c2apiReadyCell, refreshStatus } from "./stats.js";

const ACC_PAGE_SIZE = 50;
const EM_PAGE_SIZE = 50;

// ── 导入邮箱 ──
export function openImport() {
  const src = document.getElementById('cfgEmailSource').value || '';
  openModal('📥 导入邮箱', `
    <div class="form-group"><label>邮箱源 URL (91kami)</label>
      <input type="url" id="impUrl" placeholder="https://mai.91kami.com/cpd/..." value="${escapeHtml(src)}"></div>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="window.closeModal()">取消</button>
      <button class="btn btn-success" id="btnImportModal" onclick="window.importEmails()">开始导入</button>
    </div>`);
}
export async function importEmails() {
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
export function openManualAdd() {
  openModal('✍️ 手动添加邮箱', `
    <div class="hint">每行一个邮箱，格式：<code>邮箱----密码----client_id----refresh_token</code>。也可以只填邮箱（密码等留空）。</div>
    <div class="form-group"><label>目标平台（同邮箱可在不同平台各注册一次）</label>
      <select id="manualPlatform" style="width:100%">
        <option value="chatgpt">chatgpt（ChatGPT/OpenAI）</option>
      </select></div>
    <div class="form-group"><label>邮箱列表</label>
      <textarea id="manualEmails" style="min-height:220px" placeholder="xxx@outlook.com----密码----client_id----refresh_token"></textarea></div>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="window.closeModal()">取消</button>
      <button class="btn btn-success" id="btnManualAddModal" onclick="window.submitManualAdd()">添加</button>
    </div>`);
  // 动态填充平台选项（含已注册过的平台）
  api('/emails/platforms').then(d => {
    const sel = document.getElementById('manualPlatform');
    if (!sel || !d.platforms) return;
    sel.innerHTML = d.platforms.map(p =>
      `<option value="${escapeHtml(p)}">${escapeHtml(p)}</option>`).join('');
  }).catch(() => {});
}
export async function submitManualAdd() {
  const text = document.getElementById('manualEmails').value;
  if (!text.trim()) { toastMsg('内容为空', 'error'); return; }
  const platform = (document.getElementById('manualPlatform') || {}).value || 'chatgpt';
  const btn = document.getElementById('btnManualAddModal');
  if (btn) btn.disabled = true;
  try {
    const data = await api('/emails/manual-add', { method: 'POST', body: JSON.stringify({ text, platform }) });
    closeModal();
    toast(`已添加 ${data.inserted} 个邮箱, 跳过 ${data.skipped} 个`);
    refreshStatus(); window.loadEmails(); window.loadPlatforms();
  } catch (e) { toastMsg('添加失败: ' + e.message, 'error'); }
  finally { if (btn) btn.disabled = false; }
}

// 平台切换下拉：从 /api/emails/platforms 拉所有平台
export async function loadPlatforms() {
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
export async function startRegister() {
  const btn = document.getElementById("btnStart");
  await withBusy(btn, "启动中...", async () => {
    const count = parseInt(document.getElementById("cfgBatchSize").value) || 0;
    const data = await api("/register/start", { method: "POST", body: JSON.stringify({ count }) });
    toastMsg(`注册任务已启动，共 ${data.total}/${data.pending_total} 个邮箱`, "success");
    await refreshStatus();
  });
}
export async function controlRegister(action) {
  await withBusy(null, '', async () => {
    await api('/register/control', { method: 'POST', body: JSON.stringify({ action }) });
    refreshStatus();
  });
}

// v3.4 T78：重试失败/CF 账号
export async function retryFailed() {
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
export async function exportTokens() {
  const btn = document.getElementById('btnExportTokens') || document.querySelector('button[onclick="exportTokens()"]');
  await withBusy(btn, '导出中...', async () => {
    const data = await api('/register/accounts/export');
    if (data.count === 0) { toast('暂无 token'); return; }
    await copyText(data.tokens.join('\n'));
    toast(`已复制 ${data.count} 个 token 到剪贴板`);
  });
}

// v3.1.2：一键补齐 Token（为有 openai_refresh_token 但缺有效 access_token 的账号刷新补齐）
export async function replenishTokens() {
  if (!confirm('为有 refresh_token 但缺 access_token 的账号刷新补齐？\n（会真实调用 OpenAI 刷新，轮换的新 RT 自动落库）')) return;
  const btn = document.querySelector('button[onclick="replenishTokens()"]');
  await withBusy(btn, '补齐中...', async () => {
    const d = await api('/register/replenish-tokens', { method: 'POST', body: '{}' });
    toast(`补齐完成：成功 ${d.replenished} · 失败 ${d.failed} · 无RT需重新注册 ${d.need_reregister || 0}`);
    window.loadAccounts(); refreshStatus();
  });
}

// v3.1.2：推送成功账号到 chatgpt2api 账号池（含三件套 + 取件凭证 mail_credential）
export async function pushToChatgpt2api() {
  if (!confirm('把所有成功账号推送到 chatgpt2api 账号池？\n（含 access_token/refresh_token/id_token + OpenAI密码 + 取件凭证）')) return;
  const btn = document.querySelector('button[onclick="pushToChatgpt2api()"]');
  await withBusy(btn, '推送中...', async () => {
    const d = await api('/register/push-chatgpt2api', { method: 'POST', body: '{}' });
    if (d.success === false) { toastMsg('推送失败: ' + (d.error || '未知错误'), 'error'); return; }
    toast(`已推送 ${d.pushed} 个账号到 chatgpt2api（HTTP ${d.status}）`);
  });
}

export function openClear() {
  openModal('🗑️ 一键清空库', `
    <div class="warn">⚠️ 该操作将<b>永久删除</b>：注册记录（accounts）、邮箱池（emails）、任务记录（tasks）。运行日志将保留。建议先「导出 Token」备份。</div>
    <div class="form-group"><label>输入 <b>clear</b> 确认清空</label>
      <input type="text" id="clearConfirm" placeholder="输入 clear"></div>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="window.closeModal()">取消</button>
      <button class="btn btn-danger" onclick="window.confirmClear()" id="btnConfirmClear">确认清空</button>
    </div>`);
}
export async function confirmClear() {
  const v = document.getElementById('clearConfirm').value.trim();
  if (v !== 'clear') { toast('请先输入 clear 确认'); return; }
  const btn = document.getElementById('btnConfirmClear');
  btn.disabled = true;
  try {
    const data = await api('/register/clear', { method: 'POST', body: JSON.stringify({ confirm: v }) });
    closeModal();
    toast(`已清空: 注册记录 ${data.deleted.accounts}, 邮箱 ${data.deleted.emails}, 任务 ${data.deleted.tasks}`);
    refreshStatus(); window.loadAccounts(); window.loadEmails();
  } catch (e) { toastMsg('清空失败: ' + e.message, 'error'); }
  btn.disabled = false;
}

// ── 注册记录 ──
export async function loadAccounts(manual) {
  const btn = manual ? document.querySelector('#tab-accounts button[onclick*="loadAccounts"]') : null;
  await withBusy(btn, '加载中...', async () => {
    const skel = document.getElementById('accountsSkeleton');
    const empty = document.getElementById('accountsEmpty');
    if (skel) skel.style.display = 'block';
    if (empty) empty.style.display = 'none';
    try {
      const search = document.getElementById('accSearch').value.trim();
      const status = document.getElementById('accStatus').value;
      const params = new URLSearchParams({ limit: ACC_PAGE_SIZE, offset: window.accPage * ACC_PAGE_SIZE });
      if (status) params.set('status', status);
      if (search) params.set('search', search);
      saveState();
      const data = await api('/register/accounts?' + params.toString());
      const tbody = document.getElementById('accountsBody');
      document.getElementById('accCount').textContent = data.total || 0;
      const pageCount = Math.max(1, Math.ceil((data.total || 0) / ACC_PAGE_SIZE));
      document.getElementById('accPageInfo').textContent = `第 ${window.accPage + 1}/${pageCount} 页`;
      if (!data.accounts || data.accounts.length === 0) {
        if (window.accPage > 0) { window.accPage = 0; return loadAccounts(manual); }
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
          <td>${window.accPage * ACC_PAGE_SIZE + i + 1}</td>
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
export async function loadEmails(manual) {
  const btn = manual ? document.querySelector('#tab-emails button[onclick*="loadEmails"]') : null;
  await withBusy(btn, '加载中...', async () => {
    const skel = document.getElementById('emailsSkeleton');
    const empty = document.getElementById('emailsEmpty');
    if (skel) skel.style.display = 'block';
    if (empty) empty.style.display = 'none';
    try {
      const search = document.getElementById('emSearch').value.trim();
      const status = document.getElementById('emStatus').value;
      const platform = (document.getElementById('emPlatform') || {}).value || '';
      const params = new URLSearchParams({ limit: EM_PAGE_SIZE, offset: window.emPage * EM_PAGE_SIZE });
      if (status) params.set('status', status);
      if (platform) params.set('platform', platform);
      if (search) params.set('search', search);
      saveState();
      const data = await api('/emails/?' + params.toString());
      const tbody = document.getElementById('emailsBody');
      const pageCount = Math.max(1, Math.ceil((data.total || 0) / EM_PAGE_SIZE));
      document.getElementById('emPageInfo').textContent = `第 ${window.emPage + 1}/${pageCount} 页`;
      if (!data.emails || data.emails.length === 0) {
        if (window.emPage > 0) { window.emPage = 0; return loadEmails(manual); }
        tbody.innerHTML = ''; empty.style.display = 'block'; return;
      }
      empty.style.display = 'none';
      tbody.innerHTML = data.emails.map((e, i) => {
        const statusTag = e.status === 'pending' ? '<span class="tag pending">待注册</span>'
          : e.status === 'used' ? '<span class="tag used">已使用</span>'
          : '<span class="tag skipped">' + escapeHtml(e.status) + '</span>';
        const time = e.created_at ? new Date(e.created_at * 1000).toLocaleString() : '-';
        return `<tr>
          <td>${window.emPage * EM_PAGE_SIZE + i + 1}</td>
          <td class="mono">${escapeHtml(e.email)}</td>
          <td>${pwCell(e.password)}</td>
          <td>${tokenCell(e.client_id)}</td>
          <td>${tokenCell(e.refresh_token)}</td>
          <td>${statusTag}</td>
          <td style="white-space:nowrap">${time}</td>
          <td><button class="btn btn-danger btn-sm" onclick="window.deleteEmail(${e.id})">删除</button></td>
        </tr>`;
      }).join('');
      if (skel) skel.style.display = 'none';
    } catch (e) { console.error(e); if (manual) toast('加载邮箱失败: ' + e.message); if (skel) skel.style.display = 'none'; }
  });
}

export async function deleteEmail(id) {
  if (!confirm('确定删除该邮箱？')) return;
  try {
    await api('/emails/' + id, { method: 'DELETE' });
    toast('已删除');
    window.loadEmails(); refreshStatus();
  } catch (e) { toastMsg('删除失败: ' + e.message, 'error'); }
}

export async function clearEmails() {
  if (!confirm('确定清空整个邮箱池？')) return;
  await withBusy(document.querySelector('button[onclick="clearEmails()"]'), '清空中...', async () => {
    const data = await api('/emails/clear', { method: 'POST', body: '{}' });
    toast('已清空 ' + data.deleted + ' 个邮箱');
    window.loadEmails(); refreshStatus();
  });
}

// ── 代理池 ──
let proxyHealthMap = {};  // line(已 trim) -> {ok, error}（v3.1 T3）

export async function loadProxies(manual) {
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
export async function checkProxyHealth() {
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

export function openProxyEditor() {
  api('/proxies/').then(data => {
    openModal('✏️ 编辑代理池', `
      <div class="hint">每行一个代理。kookeey：<code>gate.kookeey.info:1000:用户ID-子用户:密码-国家</code>；通用：<code>ip:port:user:pass</code> 或 <code>http://ip:port</code>。保存后立即生效。</div>
      <div class="form-group"><label>代理列表（当前 ${data.count} 条）</label>
        <textarea id="proxyContent" style="min-height:280px">${escapeHtml(data.content)}</textarea></div>
      <div class="modal-actions">
        <button class="btn btn-ghost" onclick="window.clearProxies()">清空</button>
        <button class="btn btn-ghost" onclick="window.closeModal()">取消</button>
        <button class="btn btn-success" onclick="window.saveProxies()">保存</button>
      </div>`);
  }).catch(e => toastMsg('加载代理池失败: ' + e.message, 'error'));
}
export async function saveProxies() {
  const content = document.getElementById('proxyContent').value;
  try {
    const data = await api('/proxies/', { method: 'POST', body: JSON.stringify({ content }) });
    closeModal();
    toast('代理池已保存: ' + data.count + ' 条');
    loadProxies(); refreshStatus();
  } catch (e) { toastMsg('保存失败: ' + e.message, 'error'); }
}
export async function clearProxies() {
  if (!confirm('确定清空代理池？')) return;
  try {
    const data = await api('/proxies/', { method: 'POST', body: JSON.stringify({ content: '' }) });
    closeModal();
    toast('代理池已清空');
    loadProxies(); refreshStatus();
  } catch (e) { toastMsg('失败: ' + e.message, 'error'); }
}

// ── 导出账号密码 ──
let exportTextCache = '';

export function openExportAccounts() {
  openModal('📤 导出账号密码', `
    <div class="hint">账号密码是长期资产。默认导出格式：<code>邮箱----密码</code>（每行一个，按邮箱去重）。点「chatgpt2api 格式」则导出含 access_token 的 JSON。</div>
    <div class="controls" style="margin-bottom:12px">
      <button class="btn btn-success" onclick="window.loadCredExport()">🔍 账号密码清单</button>
      <button class="btn btn-ghost" onclick="window.loadC2apiExport()">📤 chatgpt2api 格式</button>
      <button class="btn btn-primary" onclick="window.copyExportText()">📋 复制</button>
      <button class="btn btn-primary" onclick="window.downloadExportText()">⬇️ 下载</button>
    </div>
    <div id="exportStats" class="muted" style="margin-bottom:8px"></div>
    <div class="form-group"><label>导出内容</label><textarea id="exportText" readonly style="min-height:240px"></textarea></div>
    <div class="modal-actions"><button class="btn btn-ghost" onclick="window.closeModal()">关闭</button></div>`);
  loadCredExport();
}

export async function loadCredExport() {
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

export async function loadC2apiExport() {
  document.getElementById('exportStats').textContent = '正在生成 chatgpt2api 格式...';
  try {
    const data = await api('/register/export-accounts', { method: 'POST', body: '{}' });
    if (data.success === false) {
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

export function copyExportText() {
  if (!exportTextCache) { toast('请先点击生成'); return; }
  copyText(exportTextCache);
}

export function downloadExportText() {
  if (!exportTextCache) { toast('请先点击生成'); return; }
  const blob = new Blob([exportTextCache], { type: 'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = '账号密码.txt';
  a.click();
  URL.revokeObjectURL(a.href);
}

// 挂载到全局以兼容 onclick
window.openImport = openImport;
window.importEmails = importEmails;
window.openManualAdd = openManualAdd;
window.submitManualAdd = submitManualAdd;
window.loadPlatforms = loadPlatforms;
window.startRegister = startRegister;
window.controlRegister = controlRegister;
window.retryFailed = retryFailed;
window.exportTokens = exportTokens;
window.replenishTokens = replenishTokens;
window.pushToChatgpt2api = pushToChatgpt2api;
window.openClear = openClear;
window.confirmClear = confirmClear;
window.loadAccounts = loadAccounts;
window.loadEmails = loadEmails;
window.deleteEmail = deleteEmail;
window.clearEmails = clearEmails;
window.loadProxies = loadProxies;
window.checkProxyHealth = checkProxyHealth;
window.openProxyEditor = openProxyEditor;
window.saveProxies = saveProxies;
window.clearProxies = clearProxies;
window.openExportAccounts = openExportAccounts;
window.loadCredExport = loadCredExport;
window.loadC2apiExport = loadC2apiExport;
window.copyExportText = copyExportText;
window.downloadExportText = downloadExportText;