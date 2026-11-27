"""Core orchestration engine: provisioning, orphan adoption + watchdog, deployment, harvesting, stop-all."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from backend.backends.base import ColabBackend, ColabBackendError, RawAssignment
from backend.backends.direct import DirectColabBackend
from backend.backends.mock import MockColabBackend
from backend.config import WorkspacePaths
from backend.db import StudioDatabase
from backend.models import (
    GPU_ESTIMATED_BURN_CCU_HR,
    Accelerator,
    AccountInfo,
    AccountUsage,
    AppSettings,
    BackendMode,
    BatchJob,
    BatchShotItem,
    DeploymentRun,
    HarvestArtifact,
    RecipeSpec,
    RuntimeSession,
    UnassignReceipt,
    WatchdogState,
)
from backend.recipe_loader import discover_recipes
from backend.ssh_bridge import SshTunnelManager
from backend.vault import SecretVault
from backend.video_composer import compose_video_with_subtitles, generate_mock_clip


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_error(text: str) -> str:
    return _ANSI_RE.sub("", text).strip()


class StudioOrchestrator:
    def __init__(
        self,
        paths: WorkspacePaths,
        *,
        initial_mode: Optional[BackendMode] = None,
        use_memory_vault: bool = False,
    ) -> None:
        self.paths = paths
        self.db = StudioDatabase(paths.db_path)
        self.vault = SecretVault(use_memory_only=use_memory_vault)
        self.tunnels = SshTunnelManager(paths)
        self._lock = threading.Lock()
        self._listeners: list[Callable[[dict[str, Any]], None]] = []

        self._settings = self.db.get_settings()
        if initial_mode is not None:
            self._settings.backend_mode = initial_mode
            self.db.save_settings(self._settings)

        self._mock_backend = MockColabBackend()
        self._direct_backend = DirectColabBackend(on_token_refreshed=self.vault.set_account_token)

        # Runtime tracking: endpoint -> RuntimeSession + RawAssignment
        self._managed_meta: dict[str, dict[str, Any]] = {}
        self._assignments_cache: dict[str, RawAssignment] = {}
        # Clean up any legacy seeded mock account from SQLite so real mode stays spotless
        self._purge_legacy_mock_account()

    def _purge_legacy_mock_account(self) -> None:
        for acc in self.db.list_accounts():
            if acc.id == "acc-mock-demo" or acc.email == "studio-demo@gmail.com":
                self.db.delete_account(acc.id)
                self.vault.delete_account_token(acc.id)

    def _virtual_mock_account(self) -> AccountInfo:
        return AccountInfo(
            id="acc-mock-sandbox",
            email="sandbox-preview@local",
            display_name="沙盒演示账号（仅 Mock 模式）",
            added_at=_utc_now(),
            is_active=True,
            has_credential=True,
        )

    @property
    def backend(self) -> ColabBackend:
        return self._mock_backend if self._settings.backend_mode == BackendMode.MOCK else self._direct_backend

    def add_event_listener(self, cb: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            self._listeners.append(cb)

    def remove_event_listener(self, cb: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            if cb in self._listeners:
                self._listeners.remove(cb)

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        msg = {"type": event_type, "timestamp": _utc_now(), "data": payload}
        with self._lock:
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(msg)
            except Exception:
                pass

    # ---------- Settings & Mode ----------

    def get_settings(self) -> AppSettings:
        return self._settings

    def update_settings(self, patch: dict[str, Any]) -> AppSettings:
        merged = self._settings.model_dump()
        for k, v in patch.items():
            if k == "guards" and isinstance(v, dict):
                merged["guards"].update(v)
            elif v is not None:
                merged[k] = v
        self._settings = AppSettings.model_validate(merged)
        self.db.save_settings(self._settings)
        self._emit("settings_updated", self._settings.model_dump(mode="json"))
        return self._settings

    # ---------- Accounts & Secrets ----------

    def begin_oauth(self) -> dict[str, str]:
        return self.backend.begin_oauth_login()

    def complete_oauth(self, flow_id: str, auth_code: str) -> AccountInfo:
        info = self.backend.complete_oauth_login(flow_id, auth_code)
        existing = [a for a in self.db.list_accounts() if a.email == info["email"]]
        acc_id = existing[0].id if existing else f"acc-{uuid.uuid4().hex[:8]}"
        acc = AccountInfo(
            id=acc_id,
            email=info["email"],
            display_name=info["display_name"],
            added_at=existing[0].added_at if existing else _utc_now(),
            is_active=True,
            has_credential=True,
        )
        self.vault.set_account_token(acc_id, info["token_json"])
        self.db.upsert_account(acc)
        self._emit("accounts_changed", {"active_id": acc_id})
        return acc

    def list_accounts_with_usage(self) -> list[AccountInfo]:
        accounts = self.db.list_accounts()
        if not accounts and self._settings.backend_mode == BackendMode.MOCK:
            mock_acc = self._virtual_mock_account()
            mock_acc.usage = self.backend.get_usage(mock_acc.id, '{"mock": true}')
            return [mock_acc]
        out: list[AccountInfo] = []
        for acc in accounts:
            token = self.vault.get_account_token(acc.id)
            acc.has_credential = bool(token)
            if token:
                acc.usage = self.backend.get_usage(acc.id, token)
            else:
                acc.usage = AccountUsage(status="unknown", stale_reason="No credential in vault")
            out.append(acc)
        return out

    def get_active_account(self) -> tuple[AccountInfo, str]:
        accounts = self.db.list_accounts()
        if not accounts and self._settings.backend_mode == BackendMode.MOCK:
            return self._virtual_mock_account(), '{"mock": true}'
        active = next((a for a in accounts if a.is_active), None) or (accounts[0] if accounts else None)
        if not active:
            raise ColabBackendError("no_account", "请先在「账号与护栏」中添加并登录 Google 账号。")
        token = self.vault.get_account_token(active.id)
        if not token:
            raise ColabBackendError("no_credential", f"账号 {active.email} 缺少有效 OAuth 令牌，请重新授权。")
        return active, token

    def set_active_account(self, account_id: str) -> None:
        self.db.set_active_account(account_id)
        self._emit("accounts_changed", {"active_id": account_id})

    def remove_account(self, account_id: str) -> None:
        self.db.delete_account(account_id)
        self.vault.delete_account_token(account_id)
        remaining = self.db.list_accounts()
        if remaining and not any(a.is_active for a in remaining):
            self.db.set_active_account(remaining[0].id)
        self._emit("accounts_changed", {"removed_id": account_id})

    def set_secret(self, name: str, value: str) -> list[str]:
        self.vault.set_secret(name, value)
        names = self.db.get_secret_names()
        if name not in names:
            names.append(name)
            self.db.set_secret_names(names)
        return self.db.get_secret_names()

    def delete_secret(self, name: str) -> list[str]:
        self.vault.delete_secret(name)
        names = [n for n in self.db.get_secret_names() if n != name]
        self.db.set_secret_names(names)
        return names

    # ---------- Sessions, Orphan Adoption & Watchdog ----------

    def _inject_watchdog(self, assignment: RawAssignment) -> WatchdogState:
        guards = self._settings.guards
        max_s = guards.auto_stop_minutes * 60
        idle_s = guards.idle_stop_minutes * 60
        wd_src = (Path(__file__).resolve().parent / "vm_assets" / "watchdog.py").read_text(encoding="utf-8")
        b64 = base64.b64encode(wd_src.encode("utf-8")).decode("ascii")
        code = (
            "import base64, pathlib, subprocess\n"
            f"pathlib.Path('/tmp/colab_studio_watchdog.py').write_bytes(base64.b64decode({b64!r}))\n"
            "subprocess.Popen(['python3', '/tmp/colab_studio_watchdog.py', "
            f"'--max-seconds', '{max_s}', '--idle-seconds', '{idle_s}'], start_new_session=True)\n"
            "print('watchdog-started')\n"
        )
        self.backend.exec_code(assignment, code, timeout=30.0)
        return WatchdogState(active=True, deadline_epoch=time.time() + max_s, idle_timeout_s=idle_s)

    def _probe_gpu(self, assignment: RawAssignment) -> tuple[Optional[str], Optional[int], Optional[int], Optional[int]]:
        res = self.backend.exec_code(
            assignment,
            "import subprocess; print(subprocess.check_output(['nvidia-smi','--query-gpu=name,memory.total,memory.used,utilization.gpu','--format=csv,noheader,nounits'], text=True).strip())",
            timeout=20.0,
        )
        if not res.ok or not res.stdout.strip():
            return assignment.gpu_name, assignment.vram_total_mib, None, None
        parts = [p.strip() for p in res.stdout.strip().splitlines()[0].split(",")]
        if len(parts) >= 4:
            try:
                return parts[0], int(parts[1]), int(parts[2]), int(parts[3])
            except ValueError:
                pass
        return assignment.gpu_name, assignment.vram_total_mib, None, None

    def list_sessions(self) -> list[RuntimeSession]:
        try:
            active, token = self.get_active_account()
        except ColabBackendError:
            return []
        assignments = self.backend.list_assignments(active.id, token)
        sessions: list[RuntimeSession] = []
        for a in assignments:
            self._assignments_cache[a.endpoint] = a
            meta = self._managed_meta.get(a.endpoint)
            if meta:
                name = meta["name"]
                managed = True
                adopted = bool(meta.get("adopted"))
                wd = meta.get("watchdog") or WatchdogState(active=False)
                created_at = meta.get("created_at")
                gpu_name = meta.get("gpu_name") or a.gpu_name
                vram_total = meta.get("vram_total_mib") or a.vram_total_mib
                vram_used = meta.get("vram_used_mib")
                gpu_util = meta.get("gpu_util_percent")
            else:
                name = f"orphan-{a.endpoint[-8:]}"
                managed = False
                adopted = False
                wd = WatchdogState(active=False, warning="网页端未受管实例（无自动关机看门狗，建议点击一键接管）")
                created_at = None
                gpu_name = a.gpu_name
                vram_total = a.vram_total_mib
                vram_used = None
                gpu_util = None

            sessions.append(
                RuntimeSession(
                    name=name,
                    endpoint=a.endpoint,
                    account_id=active.id,
                    account_email=active.email,
                    accelerator=a.accelerator,
                    gpu_name=gpu_name,
                    vram_total_mib=vram_total,
                    vram_used_mib=vram_used,
                    gpu_util_percent=gpu_util,
                    managed=managed,
                    adopted=adopted,
                    status="running",
                    created_at=created_at,
                    estimated_burn_hourly=GPU_ESTIMATED_BURN_CCU_HR.get(a.accelerator, 1.0),
                    runtime_url=a.runtime_url,
                    watchdog=wd,
                    tunnels=self.tunnels.list_tunnels(name),
                )
            )
        return sessions

    def adopt_orphan(self, endpoint: str) -> RuntimeSession:
        """One-click adoption of a web-created Colab GPU runtime + automatic watchdog injection."""
        sessions = self.list_sessions()
        target = next((s for s in sessions if s.endpoint == endpoint or s.name == endpoint), None)
        if not target:
            raise ColabBackendError("not_found", f"未找到实例 {endpoint}")
        assignment = self._assignments_cache[target.endpoint]
        gpu_name, vram_total, vram_used, gpu_util = self._probe_gpu(assignment)
        wd = self._inject_watchdog(assignment)
        adopted_name = f"adopted-{target.accelerator.value.lower()}-{target.endpoint[-6:]}"
        self._managed_meta[target.endpoint] = {
            "name": adopted_name,
            "adopted": True,
            "created_at": _utc_now(),
            "started_epoch": time.time(),
            "watchdog": wd,
            "gpu_name": gpu_name,
            "vram_total_mib": vram_total,
            "vram_used_mib": vram_used,
            "gpu_util_percent": gpu_util,
        }
        self._emit("session_adopted", {"session_name": adopted_name, "endpoint": target.endpoint})
        return next(s for s in self.list_sessions() if s.endpoint == target.endpoint)

    def extend_watchdog(self, session_name: str, minutes: int = 30) -> WatchdogState:
        sessions = self.list_sessions()
        target = next((s for s in sessions if s.name == session_name or s.endpoint == session_name), None)
        if not target:
            raise ColabBackendError("not_found", f"未找到实例 {session_name}")
        meta = self._managed_meta.get(target.endpoint)
        if not meta or not meta.get("watchdog"):
            raise ColabBackendError("no_watchdog", "该实例未挂载看门狗，请先一键接管。")
        wd: WatchdogState = meta["watchdog"]
        new_deadline = max(time.time(), wd.deadline_epoch or time.time()) + minutes * 60
        wd.deadline_epoch = new_deadline
        assignment = self._assignments_cache[target.endpoint]
        self.backend.exec_code(
            assignment,
            f"import json, pathlib; p=pathlib.Path('/tmp/colab_studio_watchdog.json'); "
            f"d=json.loads(p.read_text()) if p.exists() else {{}}; d['deadline_epoch']={new_deadline}; p.write_text(json.dumps(d))",
            timeout=15.0,
        )
        self._emit("watchdog_extended", {"session_name": target.name, "deadline_epoch": new_deadline})
        return wd

    # ---------- Provisioning & Recipe Deployment ----------

    def list_recipes(self) -> list[RecipeSpec]:
        return discover_recipes(self.paths.recipes_dir, self.paths.user_recipes_dir)

    def start_deployment(
        self,
        *,
        recipe_id: str,
        accelerator: Optional[Accelerator] = None,
        min_vram_mib: Optional[int] = None,
        session_name: Optional[str] = None,
        inputs: Optional[dict[str, Any]] = None,
    ) -> DeploymentRun:
        recipe = next((r for r in self.list_recipes() if r.id == recipe_id), None)
        if not recipe:
            raise ColabBackendError("recipe_not_found", f"未找到配方 {recipe_id}")
        active, _token = self.get_active_account()
        eff_accel = accelerator or recipe.default_accelerator
        eff_min_vram = recipe.min_vram_mib if min_vram_mib is None else min_vram_mib
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        sname = session_name or f"studio-{recipe.id[:10]}-{uuid.uuid4().hex[:4]}"

        run = DeploymentRun(
            run_id=run_id,
            recipe_id=recipe.id,
            recipe_name=recipe.name,
            account_id=active.id,
            session_name=sname,
            accelerator=eff_accel,
            state="queued",
            stage="provision",
            progress=0.05,
            message="正在排队启动部署...",
            output_dir=recipe.output_dir,
            started_at=_utc_now(),
        )
        self.db.save_run(run)
        self._emit("run_updated", run.model_dump(mode="json"))

        threading.Thread(
            target=self._execute_deployment_worker,
            args=(run, recipe, eff_accel, eff_min_vram, session_name, inputs or {}),
            daemon=True,
        ).start()
        return run

    def _update_run(self, run: DeploymentRun, **fields: Any) -> None:
        for k, v in fields.items():
            setattr(run, k, v)
        self.db.save_run(run)
        self._emit("run_updated", run.model_dump(mode="json"))

    def _execute_deployment_worker(
        self,
        run: DeploymentRun,
        recipe: RecipeSpec,
        accelerator: Accelerator,
        min_vram_mib: int,
        reuse_session: Optional[str],
        user_inputs: dict[str, Any],
    ) -> None:
        active, token = self.get_active_account()
        assignment: Optional[RawAssignment] = None
        try:
            if reuse_session:
                sessions = self.list_sessions()
                existing = next((s for s in sessions if s.name == reuse_session or s.endpoint == reuse_session), None)
                if not existing:
                    raise ColabBackendError("session_missing", f"复用实例 {reuse_session} 不存在。")
                if not existing.managed:
                    existing = self.adopt_orphan(existing.endpoint)
                assignment = self._assignments_cache[existing.endpoint]
                run.session_name = existing.name
                run.accelerator = existing.accelerator
                run.logs.append(f"复用并托管已有实例 {existing.name} ({existing.accelerator.value})")
                self._update_run(run, state="running", stage="setup", progress=0.2, message=f"已接管实例 {existing.name}")
            else:
                self._update_run(run, state="provisioning", stage="provision", progress=0.1, message=f"正在向 Colab 申请 {accelerator.value} GPU...")
                max_tries = self._settings.guards.max_vram_retries
                for attempt in range(1, max_tries + 1):
                    cand = self.backend.assign_runtime(active.id, token, accelerator, high_mem=recipe.high_mem)
                    gpu_name, vram_total, vram_used, gpu_util = self._probe_gpu(cand)
                    run.logs.append(f"[尝试 {attempt}/{max_tries}] 分配到 {gpu_name or accelerator.value} ({vram_total or 0} MiB)")
                    if min_vram_mib > 0 and vram_total is not None and vram_total < min_vram_mib:
                        run.logs.append(f"显存 {vram_total} MiB < 保底 {min_vram_mib} MiB，自动释放重抽...")
                        self.backend.unassign_runtime(active.id, token, cand.endpoint)
                        continue
                    assignment = cand
                    wd = self._inject_watchdog(cand)
                    self._managed_meta[cand.endpoint] = {
                        "name": run.session_name,
                        "adopted": False,
                        "created_at": _utc_now(),
                        "started_epoch": time.time(),
                        "watchdog": wd,
                        "gpu_name": gpu_name,
                        "vram_total_mib": vram_total,
                        "vram_used_mib": vram_used,
                        "gpu_util_percent": gpu_util,
                    }
                    self._assignments_cache[cand.endpoint] = cand
                    break
                if assignment is None:
                    raise ColabBackendError("vram_unmet", f"连续 {max_tries} 次未抽到满足 {min_vram_mib} MiB 显存的 GPU。")

            # Execute recipe stages
            if self.backend.is_mock:
                stages = recipe.stages or []
                for idx, st in enumerate(stages):
                    prog = 0.3 + 0.55 * ((idx + 1) / max(1, len(stages)))
                    run.logs.append(f"[{st.id}] {st.label} ...")
                    self._update_run(run, state="running", stage=st.id, progress=round(prog, 2), message=st.label)
                    time.sleep(0.35)
            else:
                worker_src = Path(recipe.script_path).read_text(encoding="utf-8")
                b64_worker = base64.b64encode(worker_src.encode("utf-8")).decode("ascii")
                env_map: dict[str, str] = {}
                for inp in recipe.inputs:
                    val = user_inputs.get(inp.id, inp.default)
                    env_map[f"STUDIO_INPUT_{inp.id.upper()}"] = str(val)
                if "hf_token" in recipe.needs:
                    hf = self.vault.get_secret("hf_token")
                    if hf:
                        env_map["HF_TOKEN"] = hf

                run.logs.append("正在向云端 VM 下发 worker.py 并启动视频工作台...")
                self._update_run(run, state="running", stage="setup", progress=0.35, message="正在云端安装依赖与拉取模型...")
                exec_code = (
                    "import base64, os, pathlib, subprocess\n"
                    f"pathlib.Path('/tmp/studio_worker.py').write_bytes(base64.b64decode({b64_worker!r}))\n"
                    f"env = {{**os.environ, **{env_map!r}}}\n"
                    "p = subprocess.run(['python3', '/tmp/studio_worker.py'], env=env, capture_output=True, text=True)\n"
                    "print(p.stdout)\n"
                    "if p.returncode != 0:\n"
                    "    raise RuntimeError(p.stderr[-1500:] or 'worker failed')\n"
                )
                res = self.backend.exec_code(assignment, exec_code, timeout=900.0)
                if not res.ok:
                    raise ColabBackendError("worker_failed", self.vault.redact(res.stderr or "云端部署脚本失败"))
                for line in self.vault.redact(res.stdout).splitlines()[-15:]:
                    run.logs.append(line)

            # Open SSH port-forward tunnels
            opened = []
            if self._settings.auto_forward_endpoints:
                for ep in recipe.endpoints:
                    rport = ep.port
                    if rport is None and ep.port_input:
                        default_inp = next((i.default for i in recipe.inputs if i.id == ep.port_input), 8188)
                        rport = int(user_inputs.get(ep.port_input, default_inp))
                    if rport:
                        def _kill_stale_sshd(a: RawAssignment = assignment) -> None:
                            self.backend.exec_code(
                                a,
                                "import subprocess; subprocess.run(['pkill', '-f', 'sshd: root'], check=False)",
                                timeout=15.0,
                            )

                        tun = self.tunnels.open_tunnel(
                            session_name=run.session_name,
                            runtime_url=assignment.runtime_url,
                            proxy_token=assignment.proxy_token,
                            remote_port=rport,
                            label=ep.label,
                            path=ep.path,
                            is_mock=self.backend.is_mock,
                            on_429_kill_stale_sshd=_kill_stale_sshd,
                        )
                        opened.append(tun)
                        run.logs.append(f"端口映射已就绪：{tun.local_url} -> VM:{rport}")

            self._update_run(
                run,
                state="ready",
                stage="server",
                progress=1.0,
                message=f"{recipe.name} 已就绪",
                endpoints=opened,
                finished_at=_utc_now(),
            )
        except Exception as exc:
            err_msg = _clean_error(self.vault.redact(str(exc)))
            run.logs.append(f"ERROR: {err_msg}")
            self._update_run(run, state="failed", message=err_msg, finished_at=_utc_now())

    # ---------- Output Harvesting ----------

    def harvest_outputs(self, session_name: str, *, output_dir: str = "/content/ComfyUI/output", auto_stop: bool = False) -> dict[str, Any]:
        sessions = self.list_sessions()
        target = next((s for s in sessions if s.name == session_name or s.endpoint == session_name), None)
        if not target:
            raise ColabBackendError("not_found", f"未找到运行中实例 {session_name}")
        assignment = self._assignments_cache[target.endpoint]
        remote_files = self.backend.list_remote_outputs(assignment, output_dir)
        saved: list[HarvestArtifact] = []
        session_dir = self.paths.outputs_dir / target.name
        session_dir.mkdir(parents=True, exist_ok=True)

        for rf in remote_files:
            rpath = str(rf["path"])
            fname = Path(str(rf["name"])).name
            raw_bytes = self.backend.download_remote_file(assignment, rpath)
            digest = hashlib.sha256(raw_bytes).hexdigest()
            dst = session_dir / fname
            dst.write_bytes(raw_bytes)
            ext = dst.suffix.lower()
            mtype = "video" if ext in {".mp4", ".webm", ".mov"} else ("image" if ext in {".png", ".jpg", ".webp", ".gif"} else "other")
            art = HarvestArtifact(
                id=f"art-{uuid.uuid4().hex[:8]}",
                filename=fname,
                local_path=str(dst),
                remote_path=rpath,
                session_name=target.name,
                size_bytes=len(raw_bytes),
                sha256=digest,
                media_type=mtype,
                created_at=_utc_now(),
            )
            self.db.save_artifact(art)
            saved.append(art)

        receipt = None
        if auto_stop:
            receipt = self.stop_session(target.name, reason="auto_stop_after_harvest")

        self._emit("harvest_completed", {"session_name": target.name, "count": len(saved), "auto_stopped": auto_stop})
        return {"harvested": [a.model_dump(mode="json") for a in saved], "receipt": receipt.model_dump(mode="json") if receipt else None}

    # ---------- Stop & Emergency Stop-All ----------

    def stop_session(self, session_name: str, *, reason: str = "manual_stop") -> UnassignReceipt:
        active, token = self.get_active_account()
        sessions = self.list_sessions()
        target = next((s for s in sessions if s.name == session_name or s.endpoint == session_name), None)
        if not target:
            raise ColabBackendError("not_found", f"未找到运行中实例 {session_name}")

        self.tunnels.close_session_tunnels(target.name)
        verified = self.backend.unassign_runtime(active.id, token, target.endpoint)
        meta = self._managed_meta.pop(target.endpoint, None)
        self._assignments_cache.pop(target.endpoint, None)

        dur = round(time.time() - meta["started_epoch"], 1) if meta and meta.get("started_epoch") else None
        est_spent = round((dur / 3600.0) * target.estimated_burn_hourly, 3) if dur else 0.0
        usage_after = self.backend.get_usage(active.id, token)

        receipt = UnassignReceipt(
            receipt_id=f"rcpt-{uuid.uuid4().hex[:8]}",
            timestamp=_utc_now(),
            account_id=active.id,
            account_email=active.email,
            session_name=target.name,
            endpoint=target.endpoint,
            accelerator=target.accelerator,
            reason=reason,
            duration_seconds=dur,
            estimated_ccu_spent=est_spent,
            balance_after_ccu=usage_after.balance_ccu,
            verified_unassigned=verified,
            backend_mode=self._settings.backend_mode,
        )
        self.db.save_receipt(receipt)
        self._emit("session_stopped", receipt.model_dump(mode="json"))
        return receipt

    def stop_all(self) -> dict[str, Any]:
        sessions = self.list_sessions()
        receipts: list[UnassignReceipt] = []
        for s in sessions:
            receipts.append(self.stop_session(s.name, reason="emergency_stop_all"))
        remaining = len(self.list_sessions())
        self.tunnels.close_all()
        return {
            "stopped_count": len(receipts),
            "remaining_sessions": remaining,
            "zero_burn_verified": remaining == 0 and all(r.verified_unassigned for r in receipts),
            "receipts": [r.model_dump(mode="json") for r in receipts],
        }

    # ---------- Batch ComfyUI Queue & Subtitle Composition ----------

    @staticmethod
    def _build_wan21_workflow(
        prompt: str,
        *,
        seed: int,
        prefix: str,
        width: int,
        height: int,
        frames: int,
        steps: int,
    ) -> dict[str, Any]:
        return {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.1_t2v_1.3B_fp16.safetensors", "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan", "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": "wan_2.1_vae.safetensors"}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry, low quality, distorted, watermark, text, ugly", "clip": ["2", 0]}},
            "6": {"class_type": "EmptyHunyuanLatentVideo", "inputs": {"width": width, "height": height, "length": frames, "batch_size": 1}},
            "7": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": 8.0}},
            "8": {"class_type": "KSampler", "inputs": {"model": ["7", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0], "seed": seed, "steps": steps, "cfg": 6.0, "sampler_name": "uni_pc", "scheduler": "simple", "denoise": 1.0}},
            "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
            "10": {"class_type": "CreateVideo", "inputs": {"images": ["9", 0], "fps": 16.0}},
            "11": {"class_type": "SaveVideo", "inputs": {"video": ["10", 0], "filename_prefix": prefix, "format": "mp4", "codec": "h264"}},
        }

    def _register_local_artifact(self, local_path: Path, *, remote_path: str, session_name: str) -> HarvestArtifact:
        raw = local_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        ext = local_path.suffix.lower()
        mtype = "video" if ext in {".mp4", ".webm", ".mov"} else ("image" if ext in {".png", ".jpg", ".webp", ".gif"} else "other")
        art = HarvestArtifact(
            id=f"art-{uuid.uuid4().hex[:8]}",
            filename=local_path.name,
            local_path=str(local_path),
            remote_path=remote_path,
            session_name=session_name,
            size_bytes=len(raw),
            sha256=digest,
            media_type=mtype,
            created_at=_utc_now(),
        )
        self.db.save_artifact(art)
        return art

    def _update_batch_job(self, job: BatchJob, **fields: Any) -> None:
        for k, v in fields.items():
            setattr(job, k, v)
        self.db.save_batch_job(job)
        self._emit("batch_updated", job.model_dump(mode="json"))

    def start_batch_job(
        self,
        *,
        title: str,
        session_name: str,
        shots: list[dict[str, Any]],
        workflow_mode: str = "wan21_builtin",
        custom_workflow_json: Optional[str] = None,
        width: int = 640,
        height: int = 384,
        frames: int = 33,
        steps: int = 20,
        auto_stitch: bool = True,
        burn_subtitles: bool = True,
        auto_stop: bool = False,
    ) -> BatchJob:
        if not shots:
            raise ColabBackendError("empty_shots", "请至少添加 1 条分镜提示词。")
        sessions = self.list_sessions()
        target = next((s for s in sessions if s.name == session_name or s.endpoint == session_name), None)
        if not target:
            raise ColabBackendError("not_found", f"未找到运行中实例 {session_name}")

        if workflow_mode == "custom_json":
            if not custom_workflow_json or not custom_workflow_json.strip():
                raise ColabBackendError("missing_workflow", "自定义工作流模式必须提供 ComfyUI API JSON。")
            try:
                json.loads(custom_workflow_json)
            except ValueError as exc:
                raise ColabBackendError("invalid_json", f"ComfyUI 工作流 JSON 格式错误: {exc}") from exc

        job_id = f"batch-{uuid.uuid4().hex[:8]}"
        shot_items = [
            BatchShotItem(
                index=idx + 1,
                prompt=str(s.get("prompt") or "").strip(),
                subtitle=str(s.get("subtitle") or "").strip(),
                seed=int(s.get("seed") or (42 + idx)),
            )
            for idx, s in enumerate(shots)
            if str(s.get("prompt") or "").strip()
        ]
        if not shot_items:
            raise ColabBackendError("empty_shots", "分镜提示词不能为空。")

        job = BatchJob(
            job_id=job_id,
            title=title.strip() or f"批量视频任务-{job_id[-4:]}",
            session_name=target.name,
            workflow_mode="custom_json" if workflow_mode == "custom_json" else "wan21_builtin",
            width=width,
            height=height,
            frames=frames,
            steps=steps,
            auto_stitch=auto_stitch,
            burn_subtitles=burn_subtitles,
            auto_stop=auto_stop,
            state="queued",
            progress=0.0,
            message=f"已排入 {len(shot_items)} 个视频分镜...",
            shots=shot_items,
            started_at=_utc_now(),
        )
        self.db.save_batch_job(job)
        self._emit("batch_updated", job.model_dump(mode="json"))

        threading.Thread(
            target=self._run_batch_worker,
            args=(job, target.endpoint, custom_workflow_json),
            daemon=True,
        ).start()
        return job

    def _run_batch_worker(self, job: BatchJob, endpoint: str, custom_workflow_json: Optional[str]) -> None:
        assignment = self._assignments_cache.get(endpoint)
        if not assignment:
            self._update_batch_job(job, state="failed", message="实例连接已丢失", finished_at=_utc_now())
            return

        out_dir = self.paths.outputs_dir / job.job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        clip_paths: list[Path] = []
        subtitles: list[str] = []
        total = len(job.shots)

        try:
            self._update_batch_job(job, state="running", message=f"开始执行 {total} 个 ComfyUI 分镜...")
            for i, shot in enumerate(job.shots):
                shot.state = "running"
                self._update_batch_job(
                    job,
                    progress=round(i / max(1, total) * 0.85, 2),
                    message=f"正在渲染分镜 {shot.index}/{total}...",
                )
                prefix = f"video/{job.job_id}_s{shot.index:02d}"
                local_clip = out_dir / f"s{shot.index:02d}.mp4"

                if self.backend.is_mock:
                    generate_mock_clip(local_clip, shot_index=shot.index, duration_s=2.5, width=job.width, height=job.height)
                    time.sleep(0.2)
                    remote_path = f"/content/ComfyUI/output/{prefix}_00001_.mp4"
                else:
                    if job.workflow_mode == "custom_json" and custom_workflow_json:
                        rendered = (
                            custom_workflow_json.replace("{{PROMPT}}", shot.prompt.replace('"', '\\"'))
                            .replace("{{SEED}}", str(shot.seed))
                            .replace("{{PREFIX}}", prefix)
                        )
                        wf = json.loads(rendered)
                    else:
                        wf = self._build_wan21_workflow(
                            shot.prompt,
                            seed=shot.seed,
                            prefix=prefix,
                            width=job.width,
                            height=job.height,
                            frames=job.frames,
                            steps=job.steps,
                        )
                    submit_code = (
                        "import json, time, urllib.request\n"
                        f"wf = {json.dumps(wf)}\n"
                        "req = urllib.request.Request('http://127.0.0.1:8188/prompt', data=json.dumps({'prompt': wf}).encode(), headers={'Content-Type':'application/json'})\n"
                        "pid = json.loads(urllib.request.urlopen(req).read())['prompt_id']\n"
                        "while True:\n"
                        "    time.sleep(3)\n"
                        "    h = json.loads(urllib.request.urlopen(f'http://127.0.0.1:8188/history/{pid}').read())\n"
                        "    if pid in h:\n"
                        "        outs = h[pid].get('outputs', {})\n"
                        "        found = ''\n"
                        "        for node_out in outs.values():\n"
                        "            for key in ('videos', 'gifs', 'images'):\n"
                        "                for item in node_out.get(key, []):\n"
                        "                    fn = item.get('filename', '')\n"
                        "                    sub = item.get('subfolder', '')\n"
                        "                    if fn.endswith(('.mp4', '.webm', '.mov')):\n"
                        "                        found = f'/content/ComfyUI/output/{sub}/{fn}' if sub else f'/content/ComfyUI/output/{fn}'\n"
                        "        print(found)\n"
                        "        break\n"
                    )
                    res = self.backend.exec_code(assignment, submit_code, timeout=900.0)
                    remote_path = res.stdout.strip().splitlines()[-1] if res.ok and res.stdout.strip() else ""
                    if not remote_path:
                        raise ColabBackendError("comfy_failed", res.stderr or f"分镜 {shot.index} 未生成视频文件")
                    local_clip.write_bytes(self.backend.download_remote_file(assignment, remote_path))

                art = self._register_local_artifact(local_clip, remote_path=remote_path, session_name=job.session_name)
                shot.state = "done"
                shot.artifact_id = art.id
                clip_paths.append(local_clip)
                subtitles.append(shot.subtitle)
                self._update_batch_job(
                    job,
                    progress=round((i + 1) / max(1, total) * 0.85, 2),
                    message=f"分镜 {shot.index}/{total} 已完成并下载校验",
                )

            if job.auto_stitch and clip_paths:
                self._update_batch_job(job, state="composing", progress=0.9, message="正在用 ffmpeg 拼接镜头并压制中文字幕...")
                safe_title = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", job.title).strip("_") or job.job_id
                final_mp4 = out_dir / f"{safe_title}_成片.mp4"
                out_mp4, srt_path = compose_video_with_subtitles(
                    clip_paths,
                    subtitles,
                    final_mp4,
                    burn_subtitles=job.burn_subtitles,
                )
                final_art = self._register_local_artifact(out_mp4, remote_path="composed://local", session_name=job.session_name)
                job.final_artifact_id = final_art.id
                job.srt_path = str(srt_path) if srt_path else None

            if job.auto_stop:
                self.stop_session(job.session_name, reason="auto_stop_after_batch")

            self._update_batch_job(
                job,
                state="done",
                progress=1.0,
                message="批量视频生成与字幕合成已全部完成！",
                finished_at=_utc_now(),
            )
        except Exception as exc:
            err = self.vault.redact(str(exc))
            for s in job.shots:
                if s.state == "running":
                    s.state = "failed"
                    s.error = err
            self._update_batch_job(job, state="failed", message=err, finished_at=_utc_now())

    def compose_artifacts(
        self,
        *,
        artifact_ids: list[str],
        subtitles: list[str],
        title: str = "中文字幕合成成片",
        burn_subtitles: bool = True,
    ) -> dict[str, Any]:
        if not artifact_ids:
            raise ColabBackendError("empty_artifacts", "请至少选择 1 个视频成品。")
        clip_paths: list[Path] = []
        for aid in artifact_ids:
            art = self.db.get_artifact(aid)
            if not art or not Path(art.local_path).is_file():
                raise ColabBackendError("missing_artifact", f"找不到本地成品文件 {aid}")
            clip_paths.append(Path(art.local_path))

        comp_dir = self.paths.outputs_dir / "composed"
        comp_dir.mkdir(parents=True, exist_ok=True)
        safe_title = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", title).strip("_") or f"composed_{uuid.uuid4().hex[:6]}"
        out_path = comp_dir / f"{safe_title}_{uuid.uuid4().hex[:4]}.mp4"
        out_mp4, srt_path = compose_video_with_subtitles(
            clip_paths,
            subtitles,
            out_path,
            burn_subtitles=burn_subtitles,
        )
        final_art = self._register_local_artifact(out_mp4, remote_path="composed://local", session_name="local-composer")
        self._emit("compose_completed", {"artifact_id": final_art.id})
        return {"artifact": final_art.model_dump(mode="json"), "srt_path": str(srt_path) if srt_path else None}

    def bootstrap_snapshot(self) -> dict[str, Any]:
        accounts = self.list_accounts_with_usage()
        active = next((a for a in accounts if a.is_active), None) or (accounts[0] if accounts else None)
        return {
            "settings": self._settings.model_dump(mode="json"),
            "accounts": [a.model_dump(mode="json") for a in accounts],
            "active_account": active.model_dump(mode="json") if active else None,
            "secret_names": self.db.get_secret_names(),
            "sessions": [s.model_dump(mode="json") for s in self.list_sessions()],
            "recipes": [r.model_dump(mode="json") for r in self.list_recipes()],
            "runs": [r.model_dump(mode="json") for r in self.db.list_runs(25)],
            "batch_jobs": [b.model_dump(mode="json") for b in self.db.list_batch_jobs(20)],
            "artifacts": [a.model_dump(mode="json") for a in self.db.list_artifacts(60)],
            "receipts": [r.model_dump(mode="json") for r in self.db.list_receipts(30)],
        }
