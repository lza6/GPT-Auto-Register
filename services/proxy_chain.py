"""本地链式代理服务：客户端 → v2ray(SOCKS5:10808) → kookeey(美国动态IP) → 目标

用法（独立进程）：
    python -m services.proxy_chain --listen 127.0.0.1:10809

链路：
    浏览器(只配 127.0.0.1:10809)
        → SOCKS5 连 v2ray(10808) 出墙
        → v2ray 出口连 kookeey 网关(gate.kookeey.info:1000)（账密认证）
        → kookeey CONNECT 目标
        → 双向转发

服务器部署（香港）时直接连 kookeey：
    配置 --upstream "" 则跳过 v2ray，直连 kookeey
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import sys
from pathlib import Path
from urllib.parse import urlparse

UPSTREAM_SOCKS5 = "socks5://127.0.0.1:10808"
KOOKEEY_GATEWAY = "gate.kookeey.info"
KOOKEEY_PORT = 1000


def load_kookeey_credentials(proxy_file: str | Path | None = None) -> tuple[str, str]:
    """从 proxies.txt 第一条 kookeey 行解析 (user, password)。

    kookeey 行格式: gateway:port:UserID-SecurityUser:Pass-Country
    解析结果: user = "UserID-SecurityUser", password = "Pass-Country"。
    未找到返回 ("", "")。
    """
    path = Path(proxy_file) if proxy_file else Path(__file__).resolve().parent.parent / "proxies.txt"
    if not path.exists():
        return "", ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        lines = path.read_text(encoding="gbk", errors="ignore").splitlines()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) >= 4 and "-" in parts[2]:
            user_parts = parts[2].split("-")
            pass_parts = parts[3].split("-")
            user = f"{user_parts[0]}-{user_parts[1] if len(user_parts) > 1 else ''}"
            pwd = f"{pass_parts[0]}-{pass_parts[1] if len(pass_parts) > 1 else 'US'}"
            return user, pwd
    return "", ""


# kookeey 账密：从 proxies.txt 解析，不再硬编码（可用 --kookeey-user/--kookeey-pass 覆盖）
KOOKEEY_USER, KOOKEEY_PASS = load_kookeey_credentials()
if not KOOKEEY_USER:
    sys.stderr.write("[proxy-chain] 警告: proxies.txt 中未找到 kookeey 凭据，链式代理认证将失败\n")


async def pipe_forward(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """单向数据转发：reader -> writer"""
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def socks5_connect(proxy_url: str, host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """通过 SOCKS5 代理建立到 host:port 的连接，返回 (reader, writer)"""
    pu = urlparse(proxy_url)
    proxy_host = pu.hostname or "127.0.0.1"
    proxy_port = pu.port or 10808
    username = pu.username or ""
    password = pu.password or ""

    reader, writer = await asyncio.open_connection(proxy_host, proxy_port)

    # SOCKS5 握手：无认证（0x00）或用户名密码认证（0x02）
    if username:
        writer.write(b"\x05\x01\x02")
        await writer.drain()
        resp = await reader.readexactly(2)
        if resp[1] != 0x02:
            writer.close()
            raise ConnectionError("SOCKS5 服务器不支持用户名密码认证")
        # 用户名密码子协商
        user_b = username.encode()
        pass_b = password.encode()
        auth = b"\x01" + bytes([len(user_b)]) + user_b + bytes([len(pass_b)]) + pass_b
        writer.write(auth)
        await writer.drain()
        auth_resp = await reader.readexactly(2)
        if auth_resp[1] != 0x00:
            writer.close()
            raise ConnectionError("SOCKS5 认证失败")
    else:
        writer.write(b"\x05\x01\x00")
        await writer.drain()
        resp = await reader.readexactly(2)
        if resp[1] != 0x00:
            writer.close()
            raise ConnectionError("SOCKS5 握手失败")

    # CONNECT 请求
    host_b = host.encode()
    if len(host_b) > 255:
        raise ConnectionError("主机名过长")
    connect_req = b"\x05\x01\x00\x03" + bytes([len(host_b)]) + host_b + port.to_bytes(2, "big")
    writer.write(connect_req)
    await writer.drain()

    # 读取响应
    resp = await reader.readexactly(4)
    if resp[1] != 0x00:
        writer.close()
        raise ConnectionError(f"SOCKS5 CONNECT {host}:{port} 失败: 状态 {resp[1]}")
    # 读取地址部分（跳过）
    atyp = resp[3]
    if atyp == 0x01:  # IPv4
        await reader.readexactly(4 + 2)
    elif atyp == 0x03:  # 域名
        length = (await reader.readexactly(1))[0]
        await reader.readexactly(length + 2)
    elif atyp == 0x04:  # IPv6
        await reader.readexactly(16 + 2)

    return reader, writer


async def connect_via_http_proxy(proxy_url: str, host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """通过 HTTP 代理（带账密）建立 CONNECT 隧道，返回 (reader, writer)"""
    pu = urlparse(proxy_url)
    proxy_host = pu.hostname or "127.0.0.1"
    proxy_port = pu.port or 8080

    reader, writer = await asyncio.open_connection(proxy_host, proxy_port)

    # HTTP CONNECT 请求（带 Basic Auth）
    headers = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n"
    if pu.username:
        auth_str = f"{pu.username}:{pu.password or ''}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()
        headers += f"Proxy-Authorization: Basic {auth_b64}\r\n"
    headers += "\r\n"

    writer.write(headers.encode())
    await writer.drain()

    # 读取响应头
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = await reader.read(1024)
        if not chunk:
            writer.close()
            raise ConnectionError(f"HTTP 代理 {proxy_url} CONNECT {host}:{port} 失败: 连接关闭")
        response += chunk

    status_line = response.split(b"\r\n", 1)[0].decode("utf-8", errors="replace")
    if " 200 " not in status_line:
        writer.close()
        raise ConnectionError(f"HTTP 代理 CONNECT {host}:{port} 失败: {status_line}")

    return reader, writer


async def handle_client(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter,
                        upstream_socks5: str | None, gateway: str, gateway_port: int) -> None:
    """处理客户端 CONNECT 请求，建立两级代理链"""
    peer = client_writer.get_extra_info("peername")
    kk_writer: asyncio.StreamWriter | None = None
    try:
        # 1. 读取客户端 CONNECT 请求
        request = b""
        while b"\r\n\r\n" not in request:
            chunk = await client_reader.read(1024)
            if not chunk:
                client_writer.close()
                return
            request += chunk

        request_text = request.decode("utf-8", errors="replace")
        first_line = request_text.split("\r\n", 1)[0]
        parts = first_line.split()
        if len(parts) < 3 or parts[0].upper() != "CONNECT":
            client_writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            await client_writer.drain()
            client_writer.close()
            return

        target = parts[1]
        if ":" in target:
            target_host, target_port = target.rsplit(":", 1)
            target_port = int(target_port)
        else:
            target_host, target_port = target, 443

        # 2. 第一跳：通过 v2ray(SOCKS5) 连到 kookeey 网关
        if upstream_socks5:
            kk_reader, kk_writer = await socks5_connect(upstream_socks5, gateway, gateway_port)
        else:
            # 直连 kookeey（服务器部署模式）
            kk_reader, kk_writer = await asyncio.open_connection(gateway, gateway_port)

        # 3. 第二跳：通过 kookeey 网关 CONNECT 目标（HTTP 代理带账密）
        # 直接把 kookeey 的 reader/writer 当作 HTTP 代理客户端
        kookeey_connect = (
            f"CONNECT {target_host}:{target_port} HTTP/1.1\r\n"
            f"Host: {target_host}:{target_port}\r\n"
            f"Proxy-Authorization: Basic {base64.b64encode(f'{KOOKEEY_USER}:{KOOKEEY_PASS}'.encode()).decode()}\r\n"
            f"\r\n"
        )
        kk_writer.write(kookeey_connect.encode())
        await kk_writer.drain()

        # 读取 kookeey 隧道响应
        kookeey_resp = b""
        while b"\r\n\r\n" not in kookeey_resp:
            chunk = await kk_reader.read(1024)
            if not chunk:
                raise ConnectionError("kookeey 隧道连接关闭")
            kookeey_resp += chunk

        status_line = kookeey_resp.split(b"\r\n", 1)[0].decode("utf-8", errors="replace")
        if " 200 " not in status_line:
            raise ConnectionError(f"kookeey CONNECT {target_host}:{target_port} 失败: {status_line}")

        # 4. 通知客户端隧道建立成功
        client_writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await client_writer.drain()

        # 5. 双向转发
        await asyncio.gather(
            pipe_forward(client_reader, kk_writer),
            pipe_forward(kk_reader, client_writer),
        )
    except Exception as e:
        sys.stderr.write(f"[proxy-chain] {peer} 错误: {e}\n")
        try:
            client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await client_writer.drain()
        except Exception:
            pass
        client_writer.close()
        # 释放已建立的上游 kookeey 连接，避免泄漏
        if kk_writer is not None:
            try:
                kk_writer.close()
            except Exception:
                pass


async def main():
    global KOOKEEY_USER, KOOKEEY_PASS

    parser = argparse.ArgumentParser(description="本地链式代理服务")
    parser.add_argument("--listen", default="127.0.0.1:10809", help="监听地址")
    parser.add_argument("--upstream", default=UPSTREAM_SOCKS5, help="上游 v2ray SOCKS5 代理（留空则直连 kookeey）")
    parser.add_argument("--gateway", default=KOOKEEY_GATEWAY, help="kookeey 网关")
    parser.add_argument("--gateway-port", type=int, default=KOOKEEY_PORT, help="kookeey 端口")
    parser.add_argument("--kookeey-user", default=KOOKEEY_USER, help="kookeey 用户名（默认从 proxies.txt 解析）")
    parser.add_argument("--kookeey-pass", default=KOOKEEY_PASS, help="kookeey 密码（默认从 proxies.txt 解析）")
    args = parser.parse_args()
    if args.kookeey_user:
        KOOKEEY_USER = args.kookeey_user
        KOOKEEY_PASS = args.kookeey_pass

    host, port = args.listen.split(":", 1)
    port = int(port)
    upstream = args.upstream if args.upstream else None

    server = await asyncio.start_server(
        lambda r, w: handle_client(r, w, upstream, args.gateway, args.gateway_port),
        host, port,
    )
    print(f"[proxy-chain] 链式代理已启动: {host}:{port}", flush=True)
    if upstream:
        print(f"[proxy-chain] 链路: 客户端 → {upstream} → kookeey({args.gateway}:{args.gateway_port}) → 目标", flush=True)
    else:
        print(f"[proxy-chain] 链路: 客户端 → kookeey({args.gateway}:{args.gateway_port}) → 目标（直连）", flush=True)

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("[proxy-chain] 已停止")
