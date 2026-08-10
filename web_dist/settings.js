import { api } from "./api.js";
import { escapeHtml, toast, toastMsg, openModal, closeModal } from "./utils.js";

// ── 设置 ──
export async function loadSettings() {
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

export async function saveSettings() {
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
export async function loadTokenHealth() {
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

export function openAdvanced() {
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
        <button class="btn btn-ghost" onclick="window.closeModal()">取消</button>
        <button class="btn btn-primary" onclick="window.saveAdvanced()">💾 保存高级设置</button>
      </div>
      <div class="hint" style="margin-top:18px">config.json 完整配置（只读）：</div>
      <div style="overflow-x:auto"><table>
        <thead><tr><th>键</th><th>值</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="2" class="muted">空配置</td></tr>'}</tbody>
      </table></div>`);
  }).catch(e => toastMsg('加载高级设置失败: ' + e.message, 'error'));
}

// v3.1 T4：保存高级设置（4 个 v3.0 键，后端按 config_schema 转类型存 config.json）
export async function saveAdvanced() {
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

// 挂载到全局以兼容 onclick
window.loadSettings = loadSettings;
window.saveSettings = saveSettings;
window.openAdvanced = openAdvanced;
window.saveAdvanced = saveAdvanced;
window.loadTokenHealth = loadTokenHealth;