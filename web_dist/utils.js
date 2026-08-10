// ── 全局可变状态（保留在 window 上以兼容 HTML inline onclick） ──
window.accPage = 0;
window.emPage = 0;
window.lastLogId = 0;
window.taskRunning = false;
window.showPasswords = false;
window.accSearchTimer = null;
window.emSearchTimer = null;

export const ACC_PAGE_SIZE = 50;
export const EM_PAGE_SIZE = 50;

// v3.4 B10: localStorage 持久化
export function saveState() {
  try {
    const s = {
      activeTab: document.querySelector('.tab.active')?.textContent?.trim()?.toLowerCase()?.replace(/[^\w]/g, '') || 'accounts',
      accSearch: document.getElementById('accSearch')?.value || '',
      emSearch: document.getElementById('emSearch')?.value || '',
      accStatus: document.getElementById('accStatus')?.value || '',
      emStatus: document.getElementById('emStatus')?.value || '',
      accPage: window.accPage,
      emPage: window.emPage,
    };
    localStorage.setItem('gpt-reg-ui-state', JSON.stringify(s));
  } catch (e) { /* ignore */ }
}

export function loadState() {
  try {
    const raw = localStorage.getItem('gpt-reg-ui-state');
    if (!raw) return;
    const s = JSON.parse(raw);
    if (s.accSearch !== undefined) document.getElementById('accSearch').value = s.accSearch;
    if (s.emSearch !== undefined) document.getElementById('emSearch').value = s.emSearch;
    if (s.accStatus !== undefined) document.getElementById('accStatus').value = s.accStatus;
    if (s.emStatus !== undefined) document.getElementById('emStatus').value = s.emStatus;
    if (s.accPage !== undefined) window.accPage = s.accPage;
    if (s.emPage !== undefined) window.emPage = s.emPage;
    return s.activeTab || 'accounts';
  } catch (e) { return 'accounts'; }
}

// 搜索防抖：避免每敲一个字符就发一次请求
export function onAccSearch() { window.accPage = 0; saveState(); clearTimeout(window.accSearchTimer); window.accSearchTimer = setTimeout(() => window.loadAccounts(), 300); }
export function onEmSearch() { window.emPage = 0; saveState(); clearTimeout(window.emSearchTimer); window.emSearchTimer = setTimeout(() => window.loadEmails(), 300); }

// 分页辅助函数（替代 HTML 内联的 accPage=X;loadAccounts() 表达式）
export function accPagePrev() { window.accPage = Math.max(0, window.accPage - 1); window.loadAccounts(); }
export function accPageNext() { window.accPage++; window.loadAccounts(); }
export function accResetLoad() { window.accPage = 0; window.loadAccounts(); }
export function emPagePrev() { window.emPage = Math.max(0, window.emPage - 1); window.loadEmails(); }
export function emPageNext() { window.emPage++; window.loadEmails(); }
export function emResetLoad() { window.emPage = 0; window.loadEmails(); }

export function escapeHtml(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  })[c]);
}

export function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.display = 'block';
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.style.display = 'none', 2500);
}

// v3.4 T89：三态 toast（info/success/error），error 红色更久可关闭
export function toastMsg(msg, level = 'info') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast toast-' + level;
  t.style.display = 'block';
  clearTimeout(t._timer);
  const duration = level === 'error' ? 5000 : 2500;
  t._timer = setTimeout(() => { t.style.display = 'none'; t.className = 'toast'; }, duration);
}

// v3.4 T89：withBusy 防连点封装
export async function withBusy(btn, busyText, fn) {
  if (!btn) return fn();
  if (btn.disabled) return;
  const orig = btn.textContent;
  btn.disabled = true; btn.textContent = busyText;
  try { return await fn(); }
  finally { btn.disabled = false; btn.textContent = orig; }
}

export async function copyText(text) {
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
export function openModal(title, bodyHtml) {
  document.getElementById('modalTitle').innerHTML = '<h3>' + title + '</h3>';
  document.getElementById('modalBody').innerHTML = bodyHtml;
  document.getElementById('overlay').classList.add('show');
}
export function closeModal() {
  document.getElementById('overlay').classList.remove('show');
}

// ── 通用 ──
export function togglePw() {
  window.showPasswords = document.getElementById('showPw').checked;
  window.loadAccounts(); window.loadEmails();
}

// ── 主题（深浅色） ──
export function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  try { localStorage.setItem('gpt-reg-theme', theme); } catch (e) {}
}
export function toggleTheme() {
  const cur = document.documentElement.getAttribute('data-theme');
  applyTheme(cur === 'light' ? 'dark' : 'light');
}
export function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem('gpt-reg-theme'); } catch (e) {}
  const theme = saved || (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
  document.documentElement.setAttribute('data-theme', theme);
}

export function switchTab(name, el) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  if (el) el.classList.add('active');
  document.getElementById('tab-' + name).classList.add('active');
  saveState();
  if (name === 'accounts') window.loadAccounts();
  else if (name === 'emails') { window.loadPlatforms(); window.loadEmails(); }
  else if (name === 'proxies') window.loadProxies();
  else if (name === 'logs') { window.loadLogs(true); window.startLogSse(); }
  else if (name === 'settings') { window.loadSettings(); window.loadTokenHealth(); }
}

// ── 轮询（v3.0 P2-3：自适应间隔；降载：页面隐藏暂停；注册记录仅任务运行时刷新；日志增量拉取） ──
export function startPolling() {
  if (window.pollTimer) clearTimeout(window.pollTimer);
  const interval = window.taskRunning ? 2000 : 10000;
  window.pollTimer = setTimeout(function poll() {
    if (!document.hidden) {
      window.refreshStatus();
      if (window.taskRunning) window.loadAccounts();
      window.loadLogs();
    }
    const next = window.taskRunning ? 2000 : 10000;
    window.pollTimer = setTimeout(poll, next);
  }, interval);
}
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) {
    window.refreshStatus(); window.loadAccounts(); window.loadLogs(true);
  }
});

// 挂载到全局以兼容 onclick
window.togglePw = togglePw;
window.toggleTheme = toggleTheme;
window.switchTab = switchTab;
window.onAccSearch = onAccSearch;
window.onEmSearch = onEmSearch;
window.closeModal = closeModal;
window.accPagePrev = accPagePrev;
window.accPageNext = accPageNext;
window.accResetLoad = accResetLoad;
window.emPagePrev = emPagePrev;
window.emPageNext = emPageNext;
window.emResetLoad = emResetLoad;