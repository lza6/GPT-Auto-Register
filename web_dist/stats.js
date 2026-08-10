import { api } from "./api.js";
import { escapeHtml, copyText, toast } from "./utils.js";

// ── 状态轮询 ──
export async function refreshStatus() {
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
    window.taskRunning = !!data.is_running;
    updateStats(data.stats);
    updateTask(data.task);
  } catch (e) { console.error(e); }
}

export function updateTask(task) {
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

export function updateStats(s) {
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

export function copyFrom(el) {
  copyText(el.dataset.full);
}

export function pwCell(v) {
  if (!v) return '-';
  return window.showPasswords
    ? `<span class="copyable mono" title="点击复制" data-full="${escapeHtml(v)}" onclick="copyFrom(this)">${escapeHtml(v)}</span>`
    : '<span class="pw-mask">••••••</span>';
}

export function tokenCell(v) {
  if (!v) return '-';
  return `<span class="mono ellipsis copyable" title="${escapeHtml(v)}" data-full="${escapeHtml(v)}" onclick="copyFrom(this)">${escapeHtml(v.slice(0,28))}…</span>`;
}

// v3.1.2：chatgpt2api 就绪度徽章——一眼看清四项关键凭证是否齐备
// AT=access_token, RT=openai_refresh_token, 密=OpenAI密码, 取件=mail_credential(client_id+微软RT)
export function c2apiReadyCell(a) {
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

// 挂载到全局以兼容 onclick
window.refreshStatus = refreshStatus;
window.copyFrom = copyFrom;