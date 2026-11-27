"""Abstract ColabBackend contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from backend.models import Accelerator, AccountUsage


class ColabBackendError(RuntimeError):
    def __init__(self, kind: str, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code


@dataclass
class RawAssignment:
    endpoint: str
    accelerator: Accelerator
    runtime_url: str
    proxy_token: str
    gpu_name: Optional[str] = None
    vram_total_mib: Optional[int] = None


@dataclass
class ExecResult:
    ok: bool
    stdout: str
    stderr: str
    exit_code: int = 0


class ColabBackend(ABC):
    @property
    @abstractmethod
    def is_mock(self) -> bool: ...

    @abstractmethod
    def begin_oauth_login(self) -> dict[str, str]: ...

    @abstractmethod
    def complete_oauth_login(self, flow_id: str, auth_code: str) -> dict[str, str]: ...

    @abstractmethod
    def get_usage(self, account_id: str, token_json: str) -> AccountUsage: ...

    @abstractmethod
    def list_assignments(self, account_id: str, token_json: str) -> list[RawAssignment]: ...

    @abstractmethod
    def assign_runtime(
        self,
        account_id: str,
        token_json: str,
        accelerator: Accelerator,
        high_mem: bool = False,
    ) -> RawAssignment: ...

    @abstractmethod
    def unassign_runtime(self, account_id: str, token_json: str, endpoint: str) -> bool: ...

    @abstractmethod
    def exec_code(
        self,
        assignment: RawAssignment,
        code: str,
        *,
        timeout: float = 120.0,
    ) -> ExecResult: ...

    @abstractmethod
    def list_remote_outputs(self, assignment: RawAssignment, output_dir: str) -> list[dict[str, str | int]]: ...

    @abstractmethod
    def download_remote_file(self, assignment: RawAssignment, remote_path: str) -> bytes: ...
