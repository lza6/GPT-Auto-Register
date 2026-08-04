#!/usr/bin/env python3
"""Non-interactive launcher for Boterdrop-Solver."""
import os
import sys

# Force UTF-8 output on Windows
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
sys.path.insert(0, BASE_DIR)

import api_server as app_mod
import uvicorn

app_mod._auto_install()
config = app_mod._load_config()
app_mod._check_xvfb(config.get("headless", True))

app = app_mod.create_app(
    headless=config["headless"],
    thread=config["thread"],
    page_count=config["page_count"],
    proxy_support=config["proxy_support"],
    proxy_file=config.get("proxy_file", "proxies.txt"),
    cleanup_interval_minutes=config.get("cleanup_interval_minutes", 10),
)
print(f"[wrapper] boterdrop-solver starting {config['host']}:{config['port']}", flush=True)
uvicorn.run(app, host=config["host"], port=config["port"])
