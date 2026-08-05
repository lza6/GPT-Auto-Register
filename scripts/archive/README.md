# archive / 旧版实验

这些是早期抓包实验脚本（浏览器 fetch token / OAuth 全流程），已被正式实现取代：

- **正式注册引擎**：`services/protocol_register.py`（协议） + `services/browser_register.py`（浏览器兜底）
- **正式验证脚本**：`scripts/verify_account_login.py` / `scripts/verify_all_accounts.py` / `scripts/verify_microsoft_login.py`
- **正式 chatgpt2api 对接**：`api/register.py` 导出/推送 + `scripts/sync_to_chatgpt2api.py` / `scripts/auto_import_chatgpt2api.py`

这些脚本仅彼此自引用，不被 `services/`、`api/`、`main.py` 调用。保留在此供历史参考，**不参与运行**，可按需删除。
