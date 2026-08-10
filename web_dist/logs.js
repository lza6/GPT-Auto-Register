import { api } from "./api.js";
import { escapeHtml, toast, toastMsg, withBusy } from "./utils.js";

// ── 日志（v3.4 T95：SSE 实时推送，轮询降级兜底；级别筛选/搜索/暂停滚动/导出） ──
let logSseSource = null;
let logSseFailed = 0;
let logPaused = false;

export async function startLogSse() {
  if (logSseSource) return;
  logSseFailed = 0;
  try {
    function getAuthKey() { try { return localStorage.getItem('gpt-reg-auth-key') || ''; } catch (e) { return ''; } }
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

export async function loadLogs(force) {
  // v3.4 T95：如有 SSE 则不再轮询；否则回退到此轮询逻辑
  if (logSseSource) {
    if (force) {
      // 手动刷新：重置后重新连接 SSE
      stopLogSse();
      startLogSse();
    }
    return;
  }
  if (force) window.lastLogId = 0;
  try {
    const url = '/logs?limit=1000' + (window.lastLogId ? '&after_id=' + window.lastLogId : '');
    const data = await api(url);
    const box = document.getElementById('logBox');
    if (!data.logs || data.logs.length === 0) {
      if (!window.lastLogId) box.innerHTML = '<div class="log-info">暂无日志</div>';
      return;
    }
    if (!window.lastLogId) box.innerHTML = '';
    data.logs.forEach(l => appendLogEntry(l));
    data.logs.forEach(l => { if (l.id > window.lastLogId) window.lastLogId = l.id; });
  } catch (e) { console.error(e); if (force) toast('加载日志失败: ' + e.message); }
}

export function stopLogSse() {
  logSseSource = null;
}

export function startPollingLogs() {
  // 轮询降级兜底（已有 loadLogs 实现）
}

export function toggleLogScroll() {
  logPaused = !logPaused;
  const btn = document.getElementById('btnLogScroll');
  if (btn) btn.textContent = logPaused ? '▶ 继续滚动' : '⏸ 暂停滚动';
}

export function exportLogs() {
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

export async function clearLogs() {
  if (!confirm('确定清空日志？')) return;
  await withBusy(document.querySelector('button[onclick="clearLogs()"]'), '清空中...', async () => {
    await api('/logs/clear', { method: 'POST', body: '{}' });
    loadLogs(true);
    toast('日志已清空');
  });
}

// 挂载到全局以兼容 onclick
window.loadLogs = loadLogs;
window.clearLogs = clearLogs;
window.toggleLogScroll = toggleLogScroll;
window.exportLogs = exportLogs;
window.startLogSse = startLogSse;