// ── 鉴权（v3.1 审计补）：服务端配置真实 auth_key 后，前端须携带 X-Auth-Key。
// 密钥存 localStorage；收到 401 时弹窗收集一次并重试。未配置（占位符）时后端全部放行，无需密钥。
function getAuthKey() { try { return localStorage.getItem('gpt-reg-auth-key') || ''; } catch (e) { return ''; } }
function setAuthKey(k) { try { k ? localStorage.setItem('gpt-reg-auth-key', k) : localStorage.removeItem('gpt-reg-auth-key'); } catch (e) {} }

function promptAuthKey() {
  return new Promise(resolve => {
    window.openModal('🔐 需要管理密钥', `
      <div class="hint">服务端已开启鉴权（auth_enforced + 真实 auth_key）。请输入 config.json 中的 <code>auth_key</code> 以继续。</div>
      <div class="form-group"><label>管理密钥 (auth_key)</label>
        <input type="password" id="authKeyInput" placeholder="输入 auth_key" autocomplete="off"></div>
      <div class="modal-actions">
        <button class="btn btn-ghost" id="authKeyCancel">取消</button>
        <button class="btn btn-primary" id="authKeyOk">确定</button>
      </div>`);
    const done = v => { window.__authKeyResolve = null; window.closeModal(); resolve(v); };
    window.__authKeyResolve = done;
    document.getElementById('authKeyOk').onclick = () => done(document.getElementById('authKeyInput').value.trim());
    document.getElementById('authKeyCancel').onclick = () => done('');
    setTimeout(() => { const i = document.getElementById('authKeyInput'); if (i) i.focus(); }, 50);
  });
}

const API = '';
export async function api(path, opts = {}, _retried = false) {
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