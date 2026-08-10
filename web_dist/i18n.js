// web_dist/i18n.js — 极简国际化支持
const LANG_KEY = "gpt-register-lang";

const messages = {
    "zh-CN": {
        title: "GPT 自动注册控制台",
        emails_total: "邮箱总数",
        pending: "待注册",
        success: "注册成功",
        failed: "注册失败",
        skipped: "已跳过",
        total: "账号总数",
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
        cancel: "取消",
        confirm: "确认",
        save: "保存",
        close: "关闭",
        copy: "复制",
        download: "下载",
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
        settings: "设置",
        advanced: "高级设置",
        proxies: "代理池",
        check_health: "探测健康",
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

export function getLang() { return currentLang; }

export function setLang(lang) {
    if (!messages[lang]) return;
    currentLang = lang;
    localStorage.setItem(LANG_KEY, lang);
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
    const themeBtn = document.querySelector(".btn-ghost");
    if (themeBtn && themeBtn.parentNode) {
        themeBtn.parentNode.insertBefore(sel, themeBtn.nextSibling);
    }
    return sel;
}