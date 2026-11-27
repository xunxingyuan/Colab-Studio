"""Native Direct Google Colab Backend (HTTPS + Jupyter Kernel WS v5.3, proxy-aware)."""

from __future__ import annotations

import base64
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote, urlencode, urljoin, urlparse

import requests
import websocket
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from backend.backends.base import ColabBackend, ColabBackendError, ExecResult, RawAssignment
from backend.config import resolve_websocket_proxy_kwargs
from backend.models import GPU_ACCELERATORS, HIGH_MEM_ONLY_ACCELERATORS, Accelerator, AccountUsage

XSSI_PREFIX = ")]}'\n"
COLAB_DOMAIN = "https://colab.research.google.com"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
REMOTE_REDIRECT_URI = "https://sdk.cloud.google.com/applicationdefaultauthcode.html"

COLAB_PUBLIC_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/colaboratory",
    "https://www.googleapis.com/auth/drive.file",
]

COLAB_OAUTH_CLIENT_CONFIG = {
    "installed": {
        "client_id": "764086051850-6qr4p6gpi6hn506pt8ejuq83di341hur.apps.googleusercontent.com",
        "project_id": "colab-cli",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_secret": "d-FL95Q19q7MQmFpd7hHD0Ty",
        "redirect_uris": ["http://localhost"],
    }
}

CLIENT_AGENT_HEADERS = {"Accept": "application/json", "X-Colab-Client-Agent": "colab-cli"}
XSRF_HEADER = "X-Goog-Colab-Token"


def _strip_xssi(body: str) -> str:
    if body.startswith(XSSI_PREFIX):
        return body[len(XSSI_PREFIX) :]
    if body.startswith(")]}'"):
        idx = body.find("\n")
        return body[idx + 1 :] if idx != -1 else ""
    return body


def _uuid_to_nbh(u: uuid.UUID) -> str:
    return u.hex


def _parse_accelerator(raw: Any) -> Accelerator:
    val = str(raw or "NONE").upper().strip()
    for acc in Accelerator:
        if acc.value == val:
            return acc
    return Accelerator.NONE


class DirectColabBackend(ColabBackend):
    def __init__(self, *, on_token_refreshed: Optional[Any] = None) -> None:
        self._on_token_refreshed = on_token_refreshed
        self._pending_flows: dict[str, InstalledAppFlow] = {}
        self._lock = threading.Lock()

    @property
    def is_mock(self) -> bool:
        return False

    def begin_oauth_login(self) -> dict[str, str]:
        flow = InstalledAppFlow.from_client_config(COLAB_OAUTH_CLIENT_CONFIG, COLAB_PUBLIC_SCOPES)
        flow.redirect_uri = REMOTE_REDIRECT_URI
        auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline", token_usage="remote")
        flow_id = f"oauth-{uuid.uuid4().hex[:10]}"
        with self._lock:
            self._pending_flows[flow_id] = flow
        return {"flow_id": flow_id, "auth_url": auth_url}

    def complete_oauth_login(self, flow_id: str, auth_code: str) -> dict[str, str]:
        with self._lock:
            flow = self._pending_flows.pop(flow_id, None)
        if flow is None:
            raise ColabBackendError("flow_expired", "OAuth flow expired; please start login again.")
        try:
            flow.oauth2session.scope = None
            token = flow.fetch_token(code=auth_code.strip())
            flow.oauth2session.scope = token.get("scope")
            creds = flow.credentials
        except Exception as exc:
            raise ColabBackendError("oauth_failed", f"OAuth exchange failed: {type(exc).__name__}") from exc

        sess = AuthorizedSession(creds)
        try:
            resp = sess.get(USERINFO_URL, timeout=15)
            info = resp.json() if resp.ok else {}
        finally:
            sess.close()
        email = str(info.get("email") or "").strip()
        if not email:
            raise ColabBackendError("unverified_account", "Failed to read Google account email.")
        return {
            "email": email,
            "display_name": str(info.get("name") or email.split("@")[0]),
            "token_json": creds.to_json(),
        }

    def _authorized_session(self, account_id: str, token_json: str) -> AuthorizedSession:
        creds = Credentials.from_authorized_user_info(json.loads(token_json))
        if (creds.expired or not creds.token) and creds.refresh_token:
            creds.refresh(Request())
            if self._on_token_refreshed:
                self._on_token_refreshed(account_id, creds.to_json())
        return AuthorizedSession(creds)

    def _colab_request(
        self,
        account_id: str,
        token_json: str,
        path: str,
        *,
        method: str = "GET",
        params: Optional[dict[str, str]] = None,
        headers: Optional[dict[str, str]] = None,
        timeout: float = 45.0,
    ) -> Any:
        url = urljoin(COLAB_DOMAIN + "/", path.lstrip("/"))
        q = {"authuser": "0", **(params or {})}
        req_headers = {**CLIENT_AGENT_HEADERS, **(headers or {})}
        sess = self._authorized_session(account_id, token_json)
        try:
            resp = sess.request(method, url, params=q, headers=req_headers, timeout=timeout)
        except requests.exceptions.Timeout as exc:
            raise ColabBackendError("timeout", f"Colab timeout on {method} {path}") from exc
        except Exception as exc:
            raise ColabBackendError("network", f"Colab network error: {type(exc).__name__}") from exc
        finally:
            sess.close()

        if not resp.ok:
            snippet = _strip_xssi(resp.text or "").strip()[:220]
            raise ColabBackendError(
                f"http_{resp.status_code}",
                f"Colab HTTP {resp.status_code} on {method} {path}: {snippet or resp.reason}",
                status_code=resp.status_code,
            )
        cleaned = _strip_xssi(resp.text or "").strip()
        return json.loads(cleaned) if cleaned else {}

    def get_usage(self, account_id: str, token_json: str) -> AccountUsage:
        now = datetime.now(timezone.utc).isoformat()
        try:
            data = self._colab_request(account_id, token_json, "/tun/m/ccu-info", timeout=20.0)
            assignments = self.list_assignments(account_id, token_json)
            eligible: list[str] = []
            for item in data.get("eligibleGpus", []) or []:
                if isinstance(item, dict) and item.get("accelerator"):
                    eligible.append(str(item["accelerator"]))
                elif isinstance(item, str):
                    eligible.append(item)
            return AccountUsage(
                status="live",
                balance_ccu=float(data.get("currentBalance", 0.0)),
                burn_rate_hourly=float(data.get("consumptionRateHourly", 0.0)),
                assignments_count=len(assignments),
                eligible_gpus=eligible,
                fetched_at=now,
            )
        except Exception as exc:
            return AccountUsage(status="stale", stale_reason=str(exc), fetched_at=now)

    def list_assignments(self, account_id: str, token_json: str) -> list[RawAssignment]:
        data = self._colab_request(account_id, token_json, "/tun/m/assignments", timeout=25.0)
        items = data.get("assignments") if isinstance(data, dict) else []
        out: list[RawAssignment] = []
        for item in items or []:
            if not isinstance(item, dict) or not item.get("endpoint"):
                continue
            proxy = item.get("runtimeProxyInfo") or {}
            out.append(
                RawAssignment(
                    endpoint=str(item["endpoint"]),
                    accelerator=_parse_accelerator(item.get("accelerator")),
                    runtime_url=str(proxy.get("url") or "").rstrip("/"),
                    proxy_token=str(proxy.get("token") or ""),
                )
            )
        return out

    def assign_runtime(
        self,
        account_id: str,
        token_json: str,
        accelerator: Accelerator,
        high_mem: bool = False,
    ) -> RawAssignment:
        params: dict[str, str] = {"nbh": _uuid_to_nbh(uuid.uuid4())}
        if accelerator in GPU_ACCELERATORS:
            params["variant"] = "GPU"
            params["accelerator"] = accelerator.value
        if high_mem and accelerator not in HIGH_MEM_ONLY_ACCELERATORS:
            params["shape"] = "hm"

        first = self._colab_request(account_id, token_json, "/tun/m/assign", method="GET", params=params, timeout=45.0)
        xsrf = str(first.get("token") or "")
        if not xsrf and first.get("endpoint"):
            proxy = first.get("runtimeProxyInfo") or {}
            return RawAssignment(
                endpoint=str(first["endpoint"]),
                accelerator=_parse_accelerator(first.get("accelerator") or accelerator.value),
                runtime_url=str(proxy.get("url") or "").rstrip("/"),
                proxy_token=str(proxy.get("token") or ""),
            )
        if not xsrf:
            raise ColabBackendError("protocol_error", "Colab /tun/m/assign GET returned no XSRF token.")

        res = self._colab_request(
            account_id,
            token_json,
            "/tun/m/assign",
            method="POST",
            params=params,
            headers={XSRF_HEADER: xsrf},
            timeout=120.0,
        )
        proxy = res.get("runtimeProxyInfo") or {}
        if not res.get("endpoint") or not proxy.get("url"):
            raise ColabBackendError("assign_incomplete", "Colab assign response missing endpoint or runtimeProxyInfo.")
        return RawAssignment(
            endpoint=str(res["endpoint"]),
            accelerator=_parse_accelerator(res.get("accelerator") or accelerator.value),
            runtime_url=str(proxy["url"]).rstrip("/"),
            proxy_token=str(proxy.get("token") or ""),
        )

    def unassign_runtime(self, account_id: str, token_json: str, endpoint: str) -> bool:
        path = f"/tun/m/unassign/{quote(endpoint, safe='')}"
        first = self._colab_request(account_id, token_json, path, method="GET", timeout=30.0)
        xsrf = str(first.get("token") or "")
        if xsrf:
            self._colab_request(
                account_id, token_json, path, method="POST", headers={XSRF_HEADER: xsrf}, timeout=30.0
            )
        for _ in range(3):
            active = [a.endpoint for a in self.list_assignments(account_id, token_json)]
            if endpoint not in active:
                return True
            time.sleep(1.0)
        return False

    def _ensure_kernel(self, assignment: RawAssignment) -> str:
        headers = {
            **CLIENT_AGENT_HEADERS,
            "Authorization": f"Bearer {assignment.proxy_token}",
            "X-Colab-Runtime-Proxy-Token": assignment.proxy_token,
        }
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                r = requests.get(f"{assignment.runtime_url}/api/kernels", headers=headers, timeout=30)
                if r.status_code in (502, 503, 504) and attempt < 2:
                    time.sleep(2 ** (attempt + 1))
                    continue
                if r.ok and isinstance(r.json(), list) and r.json():
                    return str(r.json()[0]["id"])
                r2 = requests.post(
                    f"{assignment.runtime_url}/api/kernels",
                    headers=headers,
                    json={"name": "python3"},
                    timeout=35,
                )
                if r2.status_code in (502, 503, 504) and attempt < 2:
                    time.sleep(2 ** (attempt + 1))
                    continue
                if not r2.ok:
                    raise ColabBackendError("kernel_error", f"Failed to start kernel (HTTP {r2.status_code})")
                return str(r2.json()["id"])
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_err = exc
                if attempt < 2:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise ColabBackendError("kernel_timeout", f"连接云端 Jupyter Kernel 超时（已重试 3 次）: {exc}") from exc
        raise ColabBackendError("kernel_error", f"Failed to start kernel: {last_err}")

    def exec_code(self, assignment: RawAssignment, code: str, *, timeout: float = 120.0) -> ExecResult:
        kernel_id = self._ensure_kernel(assignment)
        parsed = urlparse(assignment.runtime_url)
        ws_scheme = "wss" if parsed.scheme == "https" else "ws"
        msg_id = uuid.uuid4().hex
        session_id = uuid.uuid4().hex
        query = urlencode(
            {
                "session_id": session_id,
                "token": assignment.proxy_token,
                "colab-runtime-proxy-token": assignment.proxy_token,
            }
        )
        ws_url = f"{ws_scheme}://{parsed.netloc}/api/kernels/{kernel_id}/channels?{query}"
        headers = [
            "X-Colab-Client-Agent: colab-cli",
            f"Authorization: Bearer {assignment.proxy_token}",
            f"X-Colab-Runtime-Proxy-Token: {assignment.proxy_token}",
        ]
        req_frame = {
            "header": {
                "msg_id": msg_id,
                "username": "studio",
                "session": session_id,
                "msg_type": "execute_request",
                "version": "5.3",
            },
            "parent_header": {},
            "metadata": {},
            "content": {
                "code": code,
                "silent": False,
                "store_history": False,
                "user_expressions": {},
                "allow_stdin": False,
                "stop_on_error": True,
            },
            "buffers": [],
            "channel": "shell",
        }

        ws = websocket.WebSocket()
        proxy_kwargs = resolve_websocket_proxy_kwargs(ws_url)
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        ok = True
        try:
            try:
                ws.connect(ws_url, header=headers, timeout=15)
            except Exception:
                if not proxy_kwargs:
                    raise
                ws = websocket.WebSocket()
                ws.connect(ws_url, header=headers, timeout=15, **proxy_kwargs)
            ws.settimeout(1.0)
            ws.send(json.dumps(req_frame))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if not isinstance(raw, str):
                    continue
                msg = json.loads(raw)
                if msg.get("parent_header", {}).get("msg_id") != msg_id:
                    continue
                mtype = msg.get("msg_type")
                content = msg.get("content") or {}
                if mtype == "stream":
                    if content.get("name") == "stderr":
                        stderr_parts.append(str(content.get("text") or ""))
                    else:
                        stdout_parts.append(str(content.get("text") or ""))
                elif mtype == "error":
                    ok = False
                    stderr_parts.append("\n".join(content.get("traceback") or [str(content.get("evalue") or "Error")]))
                elif mtype == "status" and content.get("execution_state") == "idle":
                    break
        finally:
            try:
                ws.close()
            except Exception:
                pass
        return ExecResult(ok=ok, stdout="".join(stdout_parts), stderr="".join(stderr_parts), exit_code=0 if ok else 1)

    def list_remote_outputs(self, assignment: RawAssignment, output_dir: str) -> list[dict[str, str | int]]:
        code = (
            "import json, pathlib\n"
            f"root = pathlib.Path({output_dir!r})\n"
            "items = []\n"
            "if root.exists():\n"
            "    for p in sorted(root.rglob('*')):\n"
            "        if p.is_file() and p.suffix.lower() in {'.mp4','.webm','.mov','.gif','.png','.jpg','.webp'}:\n"
            "            items.append({'path': str(p), 'name': p.name, 'size': p.stat().st_size})\n"
            "print(json.dumps(items))\n"
        )
        res = self.exec_code(assignment, code, timeout=30.0)
        if not res.ok:
            return []
        try:
            return json.loads(res.stdout.strip().splitlines()[-1])
        except Exception:
            return []

    def download_remote_file(self, assignment: RawAssignment, remote_path: str) -> bytes:
        code = (
            "import base64, pathlib\n"
            f"data = pathlib.Path({remote_path!r}).read_bytes()\n"
            "print(base64.b64encode(data).decode('ascii'))\n"
        )
        res = self.exec_code(assignment, code, timeout=180.0)
        if not res.ok:
            raise ColabBackendError("download_failed", res.stderr or f"Failed to read {remote_path}")
        return base64.b64decode("".join(res.stdout.strip().splitlines()))
