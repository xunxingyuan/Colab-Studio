"""Deterministic zero-billing Mock Colab Backend for safe development & UI testing."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone

from backend.backends.base import ColabBackend, ExecResult, RawAssignment
from backend.models import GPU_ESTIMATED_BURN_CCU_HR, GPU_TYPICAL_VRAM_MIB, Accelerator, AccountUsage


class MockColabBackend(ColabBackend):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._balance_ccu: float = 200.0
        self._assignments: dict[str, RawAssignment] = {
            "gpu-l4-mock-orphan01": RawAssignment(
                endpoint="gpu-l4-mock-orphan01",
                accelerator=Accelerator.L4,
                runtime_url="https://mock-l4-orphan01.colab.local",
                proxy_token="mock-proxy-token-orphan",
                gpu_name="NVIDIA L4 (24GB)",
                vram_total_mib=23034,
            )
        }
        self._mock_outputs: dict[str, list[dict[str, str | int]]] = {}

    @property
    def is_mock(self) -> bool:
        return True

    def begin_oauth_login(self) -> dict[str, str]:
        fid = f"mock-oauth-{uuid.uuid4().hex[:8]}"
        return {"flow_id": fid, "auth_url": f"https://accounts.google.com/mock-consent?flow={fid}"}

    def complete_oauth_login(self, flow_id: str, auth_code: str) -> dict[str, str]:
        return {
            "email": f"creator-{flow_id[-4:]}@gmail.com",
            "display_name": "Mock Video Creator",
            "token_json": '{"mock": true}',
        }

    def get_usage(self, account_id: str, token_json: str) -> AccountUsage:
        with self._lock:
            burn = sum(GPU_ESTIMATED_BURN_CCU_HR.get(a.accelerator, 1.0) for a in self._assignments.values())
            count = len(self._assignments)
            bal = round(self._balance_ccu, 2)
        return AccountUsage(
            status="live",
            balance_ccu=bal,
            burn_rate_hourly=round(burn, 2),
            assignments_count=count,
            eligible_gpus=["G4", "A100", "L4", "T4"],
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

    def list_assignments(self, account_id: str, token_json: str) -> list[RawAssignment]:
        with self._lock:
            return list(self._assignments.values())

    def assign_runtime(
        self,
        account_id: str,
        token_json: str,
        accelerator: Accelerator,
        high_mem: bool = False,
    ) -> RawAssignment:
        time.sleep(0.35)
        ep = f"gpu-{accelerator.value.lower()}-mock-{uuid.uuid4().hex[:8]}"
        vram = GPU_TYPICAL_VRAM_MIB.get(accelerator, 15360)
        assignment = RawAssignment(
            endpoint=ep,
            accelerator=accelerator,
            runtime_url=f"https://{ep}.colab.local",
            proxy_token=f"mock-token-{uuid.uuid4().hex[:12]}",
            gpu_name=f"NVIDIA {accelerator.value} ({vram // 1024}GB)",
            vram_total_mib=vram,
        )
        with self._lock:
            self._assignments[ep] = assignment
            self._mock_outputs[ep] = [
                {
                    "path": f"/content/ComfyUI/output/wan21_demo_{ep[-4:]}.mp4",
                    "name": f"wan21_demo_{ep[-4:]}.mp4",
                    "size": 12480,
                }
            ]
        return assignment

    def unassign_runtime(self, account_id: str, token_json: str, endpoint: str) -> bool:
        with self._lock:
            removed = self._assignments.pop(endpoint, None)
            if removed:
                self._balance_ccu = max(0.0, self._balance_ccu - 0.12)
        return True

    def exec_code(self, assignment: RawAssignment, code: str, *, timeout: float = 120.0) -> ExecResult:
        time.sleep(0.15)
        if "nvidia-smi" in code:
            vram = assignment.vram_total_mib or GPU_TYPICAL_VRAM_MIB.get(assignment.accelerator, 23034)
            return ExecResult(ok=True, stdout=f"NVIDIA {assignment.accelerator.value}, {vram}, 2048, 34\n", stderr="")
        return ExecResult(ok=True, stdout="mock-exec-ok\n", stderr="")

    def list_remote_outputs(self, assignment: RawAssignment, output_dir: str) -> list[dict[str, str | int]]:
        with self._lock:
            return list(
                self._mock_outputs.get(
                    assignment.endpoint,
                    [{"path": f"{output_dir}/wan21_preview_0001.mp4", "name": "wan21_preview_0001.mp4", "size": 8192}],
                )
            )

    def download_remote_file(self, assignment: RawAssignment, remote_path: str) -> bytes:
        # Return a tiny deterministic valid-header placeholder bytes payload in mock mode
        return b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + f"mock-video:{remote_path}".encode("utf-8")
