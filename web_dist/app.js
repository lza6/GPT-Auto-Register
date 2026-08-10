// ── 入口模块：ES Module 拆分后的入口文件 ──
import { initTheme, startPolling, loadState } from "./utils.js";
import { refreshStatus } from "./stats.js";
import { loadAccounts, loadPlatforms } from "./register.js";
import { loadLogs, startLogSse } from "./logs.js";
import { loadSettings, loadTokenHealth } from "./settings.js";

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
  const { switchTab } = await import("./utils.js");
  switchTab(savedTab, tabEl);
} else {
  // 默认 accounts
  const defaultTab = document.querySelector('.tab');
  if (defaultTab) {
    const { switchTab } = await import("./utils.js");
    switchTab('accounts', defaultTab);
  }
}