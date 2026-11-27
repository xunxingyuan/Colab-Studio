"""Cross-platform OpenSSH -L port forwarding over Colab WebSocket (/colab/ssh)."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlencode, urlparse

import websocket

from backend.config import WorkspacePaths, resolve_websocket_proxy_kwargs
from backend.models import TunnelInfo

PUBKEY_HEADER = "X-Colab-Ssh-Pubkey"
CLIENT_AGENT_HEADER = "X-Colab-Client-Agent"
CLIENT_AGENT_VALUE = "colab-cli"


def build_colab_ssh_ws_url(runtime_url: str, proxy_token: str) -> str:
    parsed = urlparse(runtime_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    query = urlencode(
        [
            ("colab-runtime-proxy-token", proxy_token),
            ("colab-client-agent", CLIENT_AGENT_VALUE),
            ("colab-client-agent", "ssh-mode-proxy"),
        ]
    )
    return f"{scheme}://{parsed.netloc}/colab/ssh?{query}"


def find_free_port(preferred: int = 0) -> int:
    if preferred and 1024 <= preferred <= 65535:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", preferred)) != 0:
                try:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind(("127.0.0.1", preferred))
                    return preferred
                except OSError:
                    pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.25)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ensure_ed25519_keypair(ssh_dir: Path) -> tuple[Path, str]:
    ssh_dir.mkdir(parents=True, exist_ok=True)
    priv = ssh_dir / "id_ed25519"
    pub = ssh_dir / "id_ed25519.pub"
    if not priv.exists() or not pub.exists():
        keygen = shutil.which("ssh-keygen")
        if not keygen:
            raise RuntimeError("OpenSSH 'ssh-keygen' not found on PATH.")
        subprocess.run(
            [keygen, "-t", "ed25519", "-N", "", "-C", "colab-studio", "-f", str(priv)],
            check=True,
            capture_output=True,
            text=True,
        )
    if sys.platform != "win32":
        os.chmod(priv, 0o600)
    return priv, pub.read_text(encoding="utf-8").strip()


def run_websocket_stdio_bridge(ws_url: str, pubkey: str) -> int:
    ws = websocket.WebSocket()
    headers = [f"{PUBKEY_HEADER}: {pubkey}", f"{CLIENT_AGENT_HEADER}: {CLIENT_AGENT_VALUE}"]
    proxy_kwargs = resolve_websocket_proxy_kwargs(ws_url)
    try:
        try:
            ws.connect(ws_url, header=headers, timeout=15)
        except websocket.WebSocketBadStatusException:
            raise
        except Exception:
            if not proxy_kwargs:
                raise
            ws = websocket.WebSocket()
            ws.connect(ws_url, header=headers, timeout=15, **proxy_kwargs)
    except Exception as exc:
        sys.stderr.write(f"[colab-studio-ssh] connect failed: {type(exc).__name__}: {exc}\n")
        return 1

    closed = threading.Event()
    ws_lock = threading.Lock()

    def close_ws() -> None:
        if not closed.is_set():
            closed.set()
            try:
                ws.close()
            except Exception:
                pass

    def pump_stdin() -> None:
        fd = sys.stdin.buffer.fileno()
        try:
            while not closed.is_set():
                chunk = os.read(fd, 16384)
                if not chunk:
                    break
                with ws_lock:
                    ws.send_binary(chunk)
        except Exception:
            pass
        finally:
            close_ws()

    threading.Thread(target=pump_stdin, daemon=True).start()
    try:
        while not closed.is_set():
            opcode, payload = ws.recv_data(control_frame=True)
            if opcode == websocket.ABNF.OPCODE_CLOSE:
                break
            if opcode in (websocket.ABNF.OPCODE_BINARY, websocket.ABNF.OPCODE_TEXT):
                data = payload.encode("utf-8") if isinstance(payload, str) else payload
                if data:
                    sys.stdout.buffer.write(data)
                    sys.stdout.buffer.flush()
    except Exception:
        pass
    finally:
        close_ws()
    return 0


def proxy_cli_main(argv: Optional[list[str]] = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) >= 2 and args[0] == "--ticket":
        p = Path(args[1])
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        finally:
            p.unlink(missing_ok=True)
        return run_websocket_stdio_bridge(payload["ws_url"], payload["pubkey"])
    sys.stderr.write("Usage: colab-studio-ssh-proxy --ticket <path>\n")
    return 2


class SshTunnelManager:
    def __init__(self, paths: WorkspacePaths) -> None:
        self.paths = paths
        self._lock = threading.Lock()
        self._tunnels: dict[str, TunnelInfo] = {}
        self._procs: dict[str, subprocess.Popen[str]] = {}
        self._specs: dict[str, dict[str, object]] = {}
        self._closed = False
        threading.Thread(target=self._monitor_loop, daemon=True).start()

    def _spawn_ssh(
        self,
        *,
        runtime_url: str,
        proxy_token: str,
        remote_port: int,
        local_port: int,
    ) -> subprocess.Popen[str]:
        ssh_bin = shutil.which("ssh")
        if not ssh_bin:
            raise RuntimeError("OpenSSH 'ssh' not found on PATH.")
        priv_key, pubkey = ensure_ed25519_keypair(self.paths.ssh_dir)
        ws_url = build_colab_ssh_ws_url(runtime_url, proxy_token)

        fd, ticket_path = tempfile.mkstemp(prefix="colab-studio-ticket-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"ws_url": ws_url, "pubkey": pubkey}, f)

        proxy_cmd_parts = [sys.executable, "-m", "backend.ssh_bridge", "--ticket", ticket_path]
        if sys.platform == "win32":
            proxy_cmd = subprocess.list2cmdline(proxy_cmd_parts)
        else:
            proxy_cmd = " ".join(shlex.quote(x) for x in proxy_cmd_parts)

        null_dev = "NUL" if sys.platform == "win32" else "/dev/null"
        cmd = [
            ssh_bin,
            "-N",
            "-T",
            "-i",
            str(priv_key),
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            f"UserKnownHostsFile={null_dev}",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
            "-o",
            f"ProxyCommand={proxy_cmd}",
            "-L",
            f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}",
            "root@colab-runtime",
        ]
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            if is_port_open(local_port):
                return proc
            time.sleep(0.15)

        stderr_out = (proc.stderr.read() if proc.stderr else "").strip()
        proc.terminate()
        Path(ticket_path).unlink(missing_ok=True)
        raise RuntimeError(f"Failed to establish SSH tunnel {remote_port}->{local_port}: {stderr_out or 'timeout'}")

    def _monitor_loop(self) -> None:
        while not self._closed:
            time.sleep(5.0)
            with self._lock:
                dead_ids = [
                    tid
                    for tid, proc in list(self._procs.items())
                    if proc.poll() is not None and tid in self._specs
                ]
            for tid in dead_ids:
                with self._lock:
                    spec = self._specs.get(tid)
                    info = self._tunnels.get(tid)
                    if not spec or not info:
                        continue
                    info.status = "reconnecting"
                try:
                    new_proc = self._spawn_ssh_with_429_recovery(
                        runtime_url=str(spec["runtime_url"]),
                        proxy_token=str(spec["proxy_token"]),
                        remote_port=int(spec["remote_port"]),
                        local_port=int(spec["local_port"]),
                        on_429_kill_stale_sshd=spec.get("on_429_kill_stale_sshd"),  # type: ignore[arg-type]
                    )
                    with self._lock:
                        if tid in self._tunnels:
                            self._procs[tid] = new_proc
                            self._tunnels[tid].status = "open"
                            self._tunnels[tid].error = None
                        else:
                            new_proc.terminate()
                except Exception as exc:
                    with self._lock:
                        if tid in self._tunnels:
                            self._tunnels[tid].status = "error"
                            self._tunnels[tid].error = str(exc)

    def _spawn_ssh_with_429_recovery(
        self,
        *,
        runtime_url: str,
        proxy_token: str,
        remote_port: int,
        local_port: int,
        on_429_kill_stale_sshd: Optional[Callable[[], None]] = None,
    ) -> subprocess.Popen[str]:
        try:
            return self._spawn_ssh(
                runtime_url=runtime_url,
                proxy_token=proxy_token,
                remote_port=remote_port,
                local_port=local_port,
            )
        except RuntimeError as exc:
            if ("429" in str(exc) or "already-active-session" in str(exc)) and on_429_kill_stale_sshd is not None:
                try:
                    on_429_kill_stale_sshd()
                except Exception:
                    pass
                time.sleep(1.5)
                return self._spawn_ssh(
                    runtime_url=runtime_url,
                    proxy_token=proxy_token,
                    remote_port=remote_port,
                    local_port=local_port,
                )
            raise

    def list_tunnels(self, session_name: Optional[str] = None) -> list[TunnelInfo]:
        with self._lock:
            items = list(self._tunnels.values())
        return [t for t in items if not session_name or t.session_name == session_name]

    def open_tunnel(
        self,
        *,
        session_name: str,
        runtime_url: str,
        proxy_token: str,
        remote_port: int,
        label: str,
        path: str = "/",
        is_mock: bool = False,
        on_429_kill_stale_sshd: Optional[Callable[[], None]] = None,
    ) -> TunnelInfo:
        if remote_port == 8080:
            raise ValueError("Port 8080 is reserved by Colab's internal proxy.")

        local_port = find_free_port(remote_port)
        tid = f"tun-{uuid.uuid4().hex[:8]}"
        info = TunnelInfo(
            id=tid,
            session_name=session_name,
            remote_port=remote_port,
            local_port=local_port,
            label=label,
            path=path,
            status="open",
            local_url=f"http://127.0.0.1:{local_port}{path if path != '/' else ''}",
        )
        if is_mock:
            with self._lock:
                self._tunnels[tid] = info
            return info

        proc = self._spawn_ssh_with_429_recovery(
            runtime_url=runtime_url,
            proxy_token=proxy_token,
            remote_port=remote_port,
            local_port=local_port,
            on_429_kill_stale_sshd=on_429_kill_stale_sshd,
        )
        with self._lock:
            self._tunnels[tid] = info
            self._procs[tid] = proc
            self._specs[tid] = {
                "runtime_url": runtime_url,
                "proxy_token": proxy_token,
                "remote_port": remote_port,
                "local_port": local_port,
                "on_429_kill_stale_sshd": on_429_kill_stale_sshd,
            }
        return info

    def close_session_tunnels(self, session_name: str) -> None:
        with self._lock:
            tids = [tid for tid, t in self._tunnels.items() if t.session_name == session_name]
            for tid in tids:
                self._tunnels.pop(tid, None)
                self._specs.pop(tid, None)
                proc = self._procs.pop(tid, None)
                if proc and proc.poll() is None:
                    proc.terminate()

    def close_all(self) -> None:
        self._closed = True
        with self._lock:
            for proc in self._procs.values():
                if proc.poll() is None:
                    proc.terminate()
            self._procs.clear()
            self._specs.clear()
            self._tunnels.clear()


if __name__ == "__main__":
    raise SystemExit(proxy_cli_main())
