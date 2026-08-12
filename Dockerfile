# GPT 自动注册 —— 单容器（主服务 23457 + CF Solver 8001 同容器自启）
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# ─── 第1层：系统依赖（低频变化） ────────────────────────────────
# camoufox(firefox 内核) headless 运行库 + xvfb 兜底
# nodejs：sentinel 真实 SDK 求解（quickjs，Node vm 跑真实 sdk.js）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    libgtk-3-0 libx11-xcb1 libdbus-glib-1-2 libxt6 libasound2 \
    libxcomposite1 libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 \
    libnss3 libnspr4 libxss1 libxshmfence1 \
    xvfb \
    nodejs \
    && rm -rf /var/lib/apt/lists/*

# ─── 第2层：Python 依赖（仅 requirements.txt 变化时失效） ───────
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ─── 第3层：camoufox 浏览器引擎（低频变化） ─────────────────────
# 预下载 camoufox 浏览器引擎（避免运行期拉取）
RUN python -m camoufox fetch || echo "[warn] camoufox fetch 失败，运行期再试"

# ─── 第4层：应用源码（高频变化，独立缓存层） ────────────────────
# config.json / proxies.txt / data / logs 由 volume 挂载覆盖，不进镜像
COPY main.py ./
COPY api ./api
COPY services ./services
COPY cf_solver ./cf_solver
COPY web_dist ./web_dist
COPY config.example.json ./config.json
COPY sensitive_policy.json ./

RUN mkdir -p /app/data /app/logs
EXPOSE 23457 8001

# 主服务启动后按需自启 cf_solver 子进程（cf_solver_service.ensure_running）
CMD ["python", "main.py"]