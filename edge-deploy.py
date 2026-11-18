#!/usr/bin/env python3
"""Deploy a verified SSH proxy and Codex to one or more Linux devices."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import queue
import random
import re
import select
import shlex
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
STATE = Path(os.environ.get("EDGE_DEPLOY_STATE_DIR", Path.home() / ".local/state/edge-deploy"))
INSTALLER = "https://chatgpt.com/codex/install.sh"
AUTH = "https://auth.openai.com/"
SSH = ["ssh", "-S", "none", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
       "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=10",
       "-o", "ServerAliveCountMax=3", "-o", "ForwardAgent=no"]
HOST = re.compile(r"[\w][a-zA-Z0-9_.@-]*", re.ASCII)
REMOTE_DIR = re.compile(r"/tmp/edge-deploy\.[A-Za-z0-9]{8}")


class SetupError(RuntimeError):
    """A prerequisite needs operator action before a retry can help."""

# Runs inside the forwarding SSH session. The stream-local forward is private
# to the device user. The HTTP endpoint binds only to 127.0.0.1 regardless of
# the SSH server's GatewayPorts setting.
BRIDGE = r'''
import asyncio
import errno
import fcntl
import os
from pathlib import Path
import sys

folder = Path(sys.argv[1])
state = Path.home() / ".local/share/edge-deploy"
state.mkdir(parents=True, exist_ok=True, mode=0o700)
lock = open(state / "bridge.lock", "a")
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit("Another controller already owns this user's proxy. Stop it there first.")
port_file = state / "proxy-port"
last_port_file = state / "last-port"
wrapper = state / "with-proxy"
script = """#!/bin/sh
set -eu
edge_state=${0%/*}
if ! IFS= read -r edge_port < "$edge_state/proxy-port"; then
    echo 'Edge proxy tunnel is down. Check the controller with edge-deploy.py status.' >&2
    exit 1
fi
case "$edge_port" in ''|*[!0-9]*) exit 1 ;; esac
edge_url="http://127.0.0.1:$edge_port"
exec env HTTP_PROXY="$edge_url" HTTPS_PROXY="$edge_url" ALL_PROXY="$edge_url" \\
    http_proxy="$edge_url" https_proxy="$edge_url" all_proxy="$edge_url" \\
    npm_config_proxy="$edge_url" npm_config_https_proxy="$edge_url" \\
    NO_PROXY=localhost,127.0.0.1,::1 no_proxy=localhost,127.0.0.1,::1 "$@"
"""

def publish(path, data, mode):
    temp = state / (path.name + ".new")
    temp.write_text(data)
    temp.chmod(mode)
    os.replace(temp, path)

async def pump(source, destination):
    while True:
        block = await source.read(65536)
        if not block:
            destination.write_eof()
            return
        destination.write(block)
        await destination.drain()

async def relay(reader, writer):
    other = None
    tasks = []
    try:
        upstream, other = await asyncio.open_unix_connection(str(folder / "proxy.sock"))
        tasks = [asyncio.create_task(pump(reader, other)), asyncio.create_task(pump(upstream, writer))]
        await asyncio.gather(*tasks)
    except (ConnectionError, OSError):
        pass
    finally:
        for task in tasks:
            task.cancel()
        if other is not None:
            other.close()
        writer.close()

async def serve():
    try:
        preferred = int(last_port_file.read_text())
        if not 1024 <= preferred <= 65535:
            preferred = 0
    except (FileNotFoundError, ValueError):
        preferred = 0
    try:
        server = await asyncio.start_server(relay, "127.0.0.1", preferred)
    except OSError as error:
        if not preferred or error.errno != errno.EADDRINUSE:
            raise
        server = await asyncio.start_server(relay, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    publish(wrapper, script, 0o700)
    publish(last_port_file, str(port) + "\n", 0o600)
    publish(port_file, str(port) + "\n", 0o600)
    print("EDGE_PROXY_READY " + str(port), flush=True)
    # EOF means the control computer closed its SSH session.
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, sys.stdin.buffer)
    async with server:
        await reader.read()

try:
    asyncio.run(serve())
finally:
    port_file.unlink(missing_ok=True)
    (folder / "proxy.sock").unlink(missing_ok=True)
    folder.rmdir()
'''


def state_for(host):
    slug = hashlib.sha256(host.encode()).hexdigest()[:16]
    path = STATE / slug
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def write_state(path, data):
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(json.dumps(data) + "\n")
    tmp.replace(path)


def read_state(path, default=None):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def worker_running(state):
    with (state / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
    return False


def execute(command, timeout=20, **kwargs):
    result = subprocess.run(command, text=True, capture_output=True, stdin=subprocess.DEVNULL,
                            timeout=timeout, **kwargs)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or str(result.returncode)
        raise RuntimeError(detail[-900:])
    return result.stdout.strip()


def remote(host, command, timeout=20):
    return execute(SSH + ["-o", "ClearAllForwardings=yes", "-T", "--", host, command], timeout)


def probe_commands(prefix, check_url=None):
    curl = prefix + ["curl", "--silent", "--show-error", "--connect-timeout", "5",
                     "--max-time", "12", "--noproxy", "", "-o", "/dev/null"]
    if check_url:
        return [curl + ["-fL", "-w", "%{http_code}", check_url]]
    return [
        curl + ["-fL", INSTALLER],
        curl + ["-w", "%{http_code}", AUTH],
    ]


def verify_local(port, check_url=None, proxy_host="127.0.0.1"):
    proxy = ["--proxy", f"http://{proxy_host}:{port}"]
    try:
        for command in probe_commands([], check_url):
            status = execute(command + proxy, 15)
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"控制端 HTTP/Mixed 代理 {proxy_host}:{port} 未通过验证：{error}") from error
    verify_auth_http(status)


def choose_proxy_host(port, check_url):
    # On WSL NAT, the Windows proxy may listen on the host gateway rather
    # than WSL's loopback. Verify the actual HTTP path before choosing it.
    candidates = ["127.0.0.1"]
    try:
        if "microsoft" in Path("/proc/version").read_text().lower():
            routes = execute(["ip", "-4", "route", "show", "default"], 5)
            match = re.search(r"\bvia\s+(\d+\.\d+\.\d+\.\d+)", routes)
            if match:
                candidates.append(str(ipaddress.IPv4Address(match.group(1))))
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        pass
    for candidate in candidates:
        try:
            verify_local(port, check_url, candidate)
            return candidate
        except (RuntimeError, subprocess.TimeoutExpired):
            pass
    raise RuntimeError("无法连接本机代理。请检查 HTTP/Mixed 端口。WSL NAT 下 Windows 代理需监听 WSL 可访问的地址，或使用 mirrored 网络。也可用 --proxy-host 指定 IPv4。")


def verify_auth_http(status):
    if not re.fullmatch(r"[234][0-9]{2}", status):
        raise RuntimeError(f"无法确认目标连通性（HTTP {status}）")


def verify_device(host, check_url=None):
    wrapper = '"$HOME/.local/share/edge-deploy/with-proxy"'
    probe = '''import os, sys, urllib.error, urllib.request
proxy = os.environ["https_proxy"]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
for url in sys.argv[1:]:
    try:
        with opener.open(urllib.request.Request(url, headers={"User-Agent": "EdgeBridge/1"}), timeout=12) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code
    if not 200 <= status < (400 if url != sys.argv[-1] or len(sys.argv) == 2 else 500):
        raise SystemExit("HTTP {} for {}".format(status, url))
    print(status)
'''
    urls = [check_url] if check_url else [INSTALLER, AUTH]
    command = f"{wrapper} python3 -c {shlex.quote(probe)} " + " ".join(shlex.quote(url) for url in urls)
    status = remote(host, command, 32)
    status = status.splitlines()[-1]
    verify_auth_http(status)
    return status


def preflight_device(host):
    command = (
        "test \"$(uname -s)\" = Linux || { echo 'EDGE_PREFLIGHT: target must run Linux' >&2; exit 21; }; "
        "test \"$(id -u)\" != 0 || { echo 'EDGE_PREFLIGHT: connect as a non-root user' >&2; exit 22; }; "
        "command -v python3 >/dev/null || { echo 'EDGE_PREFLIGHT: python3 is missing (need 3.8+)' >&2; exit 23; }; "
        "python3 -c 'import sys, ssl; sys.exit(sys.version_info < (3, 8))' || "
        "{ echo 'EDGE_PREFLIGHT: Python 3.8+ with SSL support is required' >&2; exit 24; }"
    )
    try:
        remote(host, command)
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        detail = str(error)
        message = f"设备预检失败，检查 SSH 密钥/主机指纹及 Linux、非 root 账号、带 SSL 的 python3 ≥ 3.8：{detail}"
        if "EDGE_PREFLIGHT:" in detail or any(pattern in detail.lower() for pattern in (
            "permission denied", "host key verification failed", "host identification has changed",
            "command not found", "python3: not found",
        )):
            raise SetupError(message) from error
        raise RuntimeError(message) from error


def start_tunnel(host, port, log, proxy_host="127.0.0.1"):
    folder = remote(host, "umask 077; mktemp -d /tmp/edge-deploy.XXXXXXXX")
    if not REMOTE_DIR.fullmatch(folder):
        raise RuntimeError("设备 mktemp 输出异常，未创建转发")
    command = "python3 -c " + shlex.quote(BRIDGE) + " " + folder
    ssh_command = SSH + [
        "-T", "-o", "ExitOnForwardFailure=yes", "-R",
        f"{folder}/proxy.sock:{proxy_host}:{port}", "--", host, command,
    ]
    try:
        process = subprocess.Popen(ssh_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=log)
    except OSError:
        cleanup(host, folder, log)
        raise
    try:
        return process, folder, wait_handshake(process)
    except Exception:
        close_tunnel(process)
        cleanup(host, folder, log)
        raise


def wait_handshake(process):
    deadline = time.monotonic() + 20
    data = b""
    while time.monotonic() < deadline:
        ready, _, _ = select.select([process.stdout], [], [], max(0, deadline - time.monotonic()))
        if not ready:
            break
        block = os.read(process.stdout.fileno(), 4096)
        if not block:
            break
        data += block
        for line in data.split(b"\n")[:-1]:
            if re.fullmatch(rb"EDGE_PROXY_READY [0-9]+", line):
                return int(line.split()[-1])
        if len(data) > 16384:
            break
    raise RuntimeError("设备未启动代理转发。查看日志中的 SSH 转发限制或代理占用。")


def close_tunnel(process):
    process.stdin.close()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    process.stdout.close()


def cleanup(host, folder, log):
    try:
        remote(host, f"rm -f -- {folder}/proxy.sock && (rmdir -- {folder} 2>/dev/null || true)", 12)
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        note(log, f"临时套接字清理延后：{error}")


def note(log, message):
    print(f"{datetime.now().isoformat(timespec='seconds')} {message}", file=log, flush=True)


def wait_requested(request, config, seconds):
    end = time.monotonic() + seconds
    while read_state(request) == config:
        if time.monotonic() >= end:
            return True
        time.sleep(min(1, max(0, end - time.monotonic())))
    return False


def supervise(host):
    state = state_for(host)
    request = state / "request.json"
    status_file = state / "status.json"
    with (state / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        delay = 2
        log = sys.stderr
        while (config := read_state(request)) is not None:
            process = None
            folder = None
            try:
                write_state(status_file, {"state": "starting", "stage": "检查控制端代理", "generation": config["generation"]})
                verify_local(config["port"], config.get("check_url"), config.get("proxy_host", "127.0.0.1"))
                write_state(status_file, {"state": "starting", "stage": "检查设备 SSH 和环境", "generation": config["generation"]})
                preflight_device(host)
                write_state(status_file, {"state": "starting", "stage": "建立 SSH 代理转发", "generation": config["generation"]})
                process, folder, remote_port = start_tunnel(host, config["port"], log, config.get("proxy_host", "127.0.0.1"))
                write_state(status_file, {"state": "starting", "stage": "验证设备代理连接", "generation": config["generation"]})
                status = verify_device(host, config.get("check_url"))
                ready = {"state": "ready", "port": remote_port, "http": status,
                         "generation": config["generation"]}
                write_state(status_file, ready)
                note(log, f"代理已验证，设备监听 127.0.0.1:{remote_port}，验证目标 HTTP {status}")
                delay = 2
                next_check = time.monotonic() + 40
                failures = 0
                while read_state(request) == config:
                    if process.poll() is not None:
                        raise RuntimeError("SSH 隧道断开")
                    if time.monotonic() >= next_check:
                        try:
                            status = verify_device(host, config.get("check_url"))
                            failures = 0
                            write_state(status_file, dict(ready, http=status))
                        except (RuntimeError, subprocess.TimeoutExpired) as error:
                            failures += 1
                            write_state(status_file, {"state": "degraded", "error": str(error)})
                            note(log, f"端到端验证失败：{error}")
                            if failures >= 2:
                                raise RuntimeError("连续两次端到端验证失败，重建隧道") from error
                        next_check = time.monotonic() + (8 if failures else 40)
                    time.sleep(1)
            except SetupError as error:
                write_state(status_file, {"state": "blocked", "error": str(error),
                                          "generation": config["generation"]})
                note(log, f"{error}。修正后重新运行 up。")
                return 1
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                write_state(status_file, {"state": "retrying", "error": str(error), "generation": config["generation"]})
                note(log, f"{error}。将退避重试。")
            finally:
                if process is not None:
                    close_tunnel(process)
                if folder is not None:
                    cleanup(host, folder, log)
            if wait_requested(request, config, delay + random.random()):
                delay = min(delay * 2, 60)
            else:
                delay = 2
        write_state(status_file, {"state": "stopped"})
    return 0


def wait_ready(state, seconds, host=None):
    deadline = time.monotonic() + seconds
    next_notice = time.monotonic() + 15
    last_stage = None
    while time.monotonic() < deadline:
        status = read_state(state / "status.json", {})
        desired = read_state(state / "request.json", {})
        if desired.get("generation") == status.get("generation"):
            if status.get("state") == "blocked":
                raise SetupError(status["error"])
            if status.get("state") == "ready" and worker_running(state):
                return status
            if host and status.get("state") in ("starting", "retrying"):
                stage = status.get("stage", "等待连接") if status["state"] == "starting" else "连接失败，正在自动重试"
                if stage != last_stage or time.monotonic() >= next_notice:
                    elapsed = max(0, int(seconds - (deadline - time.monotonic())))
                    print(f"[{host}] {stage}…（已等待 {elapsed} 秒）", flush=True)
                    last_stage = stage
                    next_notice = time.monotonic() + 15
        time.sleep(1)
    status = read_state(state / "status.json", {})
    raise RuntimeError(f"等待代理验证超时，后台仍会重试。{status.get('error', '')}")


def verify_or_repair(host, state, args):
    try:
        verify_device(host, args.check_url if args.proxy_only else None)
    except (RuntimeError, subprocess.TimeoutExpired):
        write_state(state / "request.json", {"port": args.proxy_port, "proxy_host": args.proxy_host, "check_url": args.check_url if args.proxy_only else None, "generation": time.time_ns()})
        wait_ready(state, args.wait, host)


def run_install(command, log, host, attempt):
    print(f"[{host}] 正在安装 Codex（第 {attempt} 次），请稍候…", flush=True)
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, bufsize=1)
    messages = queue.Queue()

    def read_output():
        try:
            for line in process.stdout:
                messages.put(line)
        finally:
            messages.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    deadline = time.monotonic() + 400
    next_notice = time.monotonic() + 15
    output = []
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, 400)
            try:
                line = messages.get(timeout=min(1, remaining))
            except queue.Empty:
                if time.monotonic() >= next_notice:
                    print(f"[{host}] 安装仍在进行，等待设备返回结果…", flush=True)
                    next_notice = time.monotonic() + 15
                continue
            if line is None:
                break
            output.append(line)
            log.write(line)
            log.flush()
            print(f"[{host}] {line}", end="", flush=True)
            next_notice = time.monotonic() + 15
        process.wait(timeout=max(0.1, deadline - time.monotonic()))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        reader.join(timeout=1)
        process.stdout.close()
    note(log, f"安装尝试 {attempt}，退出码 {process.returncode}")
    return process.returncode, ''.join(output)


def bootstrap(host, args):
    state = state_for(host)
    request = state / "request.json"
    try:
        print(f"[{host}] 开始处理设备…", flush=True)
        # Serialize user commands separately from the background supervisor.
        with (state / "command.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            active = worker_running(state)
            if active and (read_state(request, {}).get("port") != args.proxy_port or read_state(request, {}).get("proxy_host", "127.0.0.1") != args.proxy_host):
                raise RuntimeError("此设备的转发配置不同或正在停止，先运行 down，再重试")
            check_url = args.check_url if args.proxy_only else None
            if active and read_state(request, {}).get("check_url") != check_url:
                write_state(request, {"port": args.proxy_port, "proxy_host": args.proxy_host, "check_url": check_url, "generation": time.time_ns()})
            if not active:
                write_state(state / "status.json", {"state": "starting"})
                write_state(request, {"port": args.proxy_port, "proxy_host": args.proxy_host, "check_url": check_url, "generation": time.time_ns()})
                with (state / "tunnel.log").open("a") as log:
                    subprocess.Popen(
                        [sys.executable, str(Path(__file__).resolve()), "_supervise", host],
                        stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                    )
            wait_ready(state, args.wait, host)
            # A running supervisor can have passed its last check some time ago.
            print(f"[{host}] 验证设备代理连接…", flush=True)
            verify_or_repair(host, state, args)
            status = wait_ready(state, args.wait, host)
            if args.proxy_only:
                return True, f"代理已验证（设备端口 {status['port']}）"

            command = ["bash", str(HERE / "edge-codex.sh"), "install", host, "--edge-proxy"]
            if args.bundle:
                command += ["--bundle", str(args.bundle), "--sha256", args.sha256]
            with (state / "install.log").open("w") as log:
                for attempt in range(1 if args.bundle else 2):
                    returncode, output = run_install(command, log, host, attempt + 1)
                    summary = [line for line in output.splitlines()
                               if line.startswith(("INSTALLED ", "LOGIN "))]
                    if returncode == 0 and summary and summary[0].startswith("INSTALLED "):
                        return True, "代理已验证；" + "；".join(summary)
                    if attempt == 0 and not args.bundle:
                        time.sleep(3)
                        verify_or_repair(host, state, args)
            raise RuntimeError("Codex 安装失败：" + (output.strip()[-700:] or str(returncode)))
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        return False, f"{error}\n  日志目录: {state}"


def require_host(host):
    if not HOST.fullmatch(host):
        raise ValueError(f"无效 SSH 主机名：{host!r}")
    return host


def targets_for(args):
    hosts = list(args.hosts)
    if args.host_file:
        for line in args.host_file.read_text().splitlines():
            alias = line.split("#", 1)[0].strip()
            if alias:
                hosts.append(alias)
    if not hosts:
        raise ValueError("请提供 SSH 主机别名或 --host-file")
    return list(dict.fromkeys(require_host(host) for host in hosts))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    up = commands.add_parser("up", help="配置、验证代理并安装 Codex")
    up.add_argument("hosts", nargs="*")
    up.add_argument("--host-file", type=Path)
    up.add_argument("--proxy-port", required=True, type=int, help="控制端 Mihomo/Clash 的 HTTP 或 Mixed 端口")
    up.add_argument("--proxy-host", default="auto", help="代理监听的 IPv4 地址，默认自动检测 WSL 网关")
    up.add_argument("--jobs", type=int, default=4)
    up.add_argument("--wait", type=int, default=90)
    up.add_argument("--proxy-only", action="store_true")
    up.add_argument("--check-url", default="https://example.com/", help="仅转发模式使用的 HTTPS 验证地址")
    up.add_argument("--login", action="store_true", help="安装完成后在单台设备上启动设备码授权")
    up.add_argument("--bundle", type=Path)
    up.add_argument("--sha256")
    for action in ("status", "down"):
        cmd = commands.add_parser(action)
        cmd.add_argument("hosts", nargs="*")
        cmd.add_argument("--host-file", type=Path)
    for action in ("login", "codex"):
        commands.add_parser(action).add_argument("host")

    if len(sys.argv) == 3 and sys.argv[1] == "_supervise":
        return supervise(require_host(sys.argv[2]))
    args = parser.parse_args()
    if args.action in ("login", "codex"):
        return open_codex(args.action, require_host(args.host))
    hosts = targets_for(args)
    if args.action == "up":
        if not 1 <= args.proxy_port <= 65535 or not 1 <= args.jobs <= 32 or args.wait < 10:
            parser.error("端口须为 1–65535，并发数 1–32，等待至少 10 秒")
        if args.login and (len(hosts) != 1 or args.proxy_only):
            parser.error("--login 仅适用于单台设备的安装")
        parsed_url = urlsplit(args.check_url)
        if parsed_url.scheme != "https" or not parsed_url.hostname or parsed_url.username or parsed_url.password:
            parser.error("--check-url 必须是没有账号密码的 HTTPS 地址")
        if args.proxy_host == "auto":
            print("正在检查控制电脑的代理，请稍候…", flush=True)
            args.proxy_host = choose_proxy_host(args.proxy_port, args.check_url if args.proxy_only else None)
        else:
            try:
                args.proxy_host = str(ipaddress.IPv4Address(args.proxy_host))
            except ipaddress.AddressValueError:
                parser.error("--proxy-host 必须是 IPv4 地址或 auto")
        if args.bundle or args.sha256:
            if not args.bundle or not args.bundle.is_file() or not re.fullmatch(r"[\da-fA-F]{64}", args.sha256 or "", re.ASCII):
                parser.error("--bundle 必须指向本地文件，并提供受信任的 64 位十六进制 --sha256")
        print(f"开始部署 {len(hosts)} 台设备；控制端代理 {args.proxy_host}:{args.proxy_port}", flush=True)
        def task(host):
            ok, result = bootstrap(host, args)
            print(f"[{'成功' if ok else '失败'}] {host}\n  {result}", flush=True)
            return ok
        with ThreadPoolExecutor(max_workers=min(args.jobs, len(hosts))) as pool:
            results = list(pool.map(task, hosts))
        print(f"结果：{sum(results)}/{len(hosts)} 台成功", flush=True)
        if args.login and all(results):
            print(f"[{hosts[0]}] 安装完成，正在启动设备码登录，请按终端提示在浏览器授权。", flush=True)
            return open_codex("login", hosts[0])
        return 0 if all(results) else 1

    for host in hosts:
        state = state_for(host)
        if args.action == "down":
            with (state / "command.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                (state / "request.json").unlink(missing_ok=True)
            print(f"{host}: 已请求停止转发")
        else:
            status = read_state(state / "status.json", {})
            if not worker_running(state) and not (status.get("state") == "blocked" and (state / "request.json").exists()):
                status = {"state": "stopped"}
            print(f"{host}: {json.dumps(status, ensure_ascii=False)}  日志: {state / 'tunnel.log'}")
    return 0


def open_codex(action, host):
    wait_ready(state_for(host), 10)
    verify_device(host)
    command = "login" if action == "login" else "run"
    return subprocess.call(["bash", str(HERE / "edge-codex.sh"), command, host, "--edge-proxy"])


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
