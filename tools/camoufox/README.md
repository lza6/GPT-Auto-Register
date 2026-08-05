# Camoufox 离线安装包目录

**网络好时无需此目录**：`启动.bat` 会自动检测并在线下载 Camoufox 浏览器引擎（~470MB，仅首次）。

**网络不好 / 自动下载失败时**，手动下载后放在本目录：

1. 浏览器打开 Camoufox 官方 Releases：https://github.com/daijro/camoufox/releases/latest
2. 下载 **Windows x64** 的 zip（形如 `camoufox-152.0.4-beta.28-win.x86_64.zip`）
3. 把 zip 原样放进本目录（`tools\camoufox\`）
4. 双击 `启动.bat` —— 脚本检测到 zip 后会自动离线安装，无需联网

> 下载替代镜像（GitHub 限速时可用加速前缀）：
> `https://ghproxy.net/` 或 `https://gh-proxy.com/` 拼在官方链接前面。
>
> 示例：`https://ghproxy.net/https://github.com/daijro/camoufox/releases/download/v152.0.4-beta.28/camoufox-152.0.4-beta.28-win.x86_64.zip`

安装后数据存放在 `%LOCALAPPDATA%\camoufox\camoufox\Cache`，本目录的 zip 可删除。

> 本目录的 `*.zip` 已被 `.gitignore` 忽略，不会提交到仓库（文件约 500MB，超出 GitHub 单文件限制）。
