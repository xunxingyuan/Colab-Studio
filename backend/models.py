"""Strongly-typed Pydantic domain models for Colab Studio."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class BackendMode(str, Enum):
    MOCK = "mock"
    REAL = "real"


class Accelerator(str, Enum):
    NONE = "NONE"
    T4 = "T4"
    L4 = "L4"
    A100 = "A100"
    H100 = "H100"
    G4 = "G4"


GPU_ACCELERATORS = {Accelerator.T4, Accelerator.L4, Accelerator.A100, Accelerator.H100, Accelerator.G4}
HIGH_MEM_ONLY_ACCELERATORS = {Accelerator.A100, Accelerator.H100, Accelerator.G4}

GPU_ESTIMATED_BURN_CCU_HR: dict[Accelerator, float] = {
    Accelerator.NONE: 0.08,
    Accelerator.T4: 1.20,
    Accelerator.L4: 4.82,
    Accelerator.A100: 11.77,
    Accelerator.H100: 18.50,
    Accelerator.G4: 16.00,
}

GPU_TYPICAL_VRAM_MIB: dict[Accelerator, int] = {
    Accelerator.NONE: 0,
    Accelerator.T4: 15360,
    Accelerator.L4: 23034,
    Accelerator.A100: 40960,
    Accelerator.H100: 81559,
    Accelerator.G4: 97887,
}


class GuardPolicy(BaseModel):
    auto_stop_minutes: int = Field(default=120, ge=10, le=720)
    idle_stop_minutes: int = Field(default=20, ge=5, le=240)
    balance_floor_ccu: float = Field(default=5.0, ge=0.0)
    session_spend_cap_ccu: float = Field(default=25.0, ge=1.0)
    max_vram_retries: int = Field(default=4, ge=1, le=10)


class AppSettings(BaseModel):
    backend_mode: BackendMode = BackendMode.MOCK
    default_accelerator: Accelerator = Accelerator.L4
    auto_forward_endpoints: bool = True
    auto_adopt_watchdog: bool = True
    guards: GuardPolicy = Field(default_factory=GuardPolicy)


class AccountUsage(BaseModel):
    status: Literal["live", "stale", "unknown"] = "unknown"
    balance_ccu: Optional[float] = None
    burn_rate_hourly: Optional[float] = None
    assignments_count: Optional[int] = None
    eligible_gpus: list[str] = Field(default_factory=list)
    fetched_at: Optional[str] = None
    stale_reason: Optional[str] = None


class AccountInfo(BaseModel):
    id: str
    email: str
    display_name: str
    added_at: str
    is_active: bool = False
    has_credential: bool = False
    usage: AccountUsage = Field(default_factory=AccountUsage)


class WatchdogState(BaseModel):
    active: bool = False
    deadline_epoch: Optional[float] = None
    idle_timeout_s: Optional[int] = None
    warning: Optional[str] = None


class TunnelInfo(BaseModel):
    id: str
    session_name: str
    remote_port: int
    local_port: int
    label: str
    path: str = "/"
    status: Literal["open", "reconnecting", "error", "closed"] = "open"
    local_url: str
    error: Optional[str] = None


class RuntimeSession(BaseModel):
    name: str
    endpoint: str
    account_id: str
    account_email: str
    accelerator: Accelerator = Accelerator.NONE
    gpu_name: Optional[str] = None
    vram_total_mib: Optional[int] = None
    vram_used_mib: Optional[int] = None
    gpu_util_percent: Optional[int] = None
    managed: bool = True
    adopted: bool = False
    status: Literal["provisioning", "running", "stopping", "stopped"] = "running"
    created_at: Optional[str] = None
    estimated_burn_hourly: float = 0.0
    runtime_url: Optional[str] = None
    watchdog: WatchdogState = Field(default_factory=WatchdogState)
    tunnels: list[TunnelInfo] = Field(default_factory=list)


class RecipeInput(BaseModel):
    id: str
    type: Literal["text", "number", "bool", "select", "secret"] = "text"
    label: str
    help: str = ""
    default: Any = ""
    options: list[str] = Field(default_factory=list)
    min: Optional[int] = None
    max: Optional[int] = None


class RecipeStage(BaseModel):
    id: str
    label: str


class RecipeEndpoint(BaseModel):
    id: str
    label: str
    port: Optional[int] = None
    port_input: Optional[str] = None
    path: str = "/"
    health: str = "/"


class RecipeSpec(BaseModel):
    id: str
    name: str
    description: str
    version: str = "1.0.0"
    category: str = "video"
    tags: list[str] = Field(default_factory=list)
    default_accelerator: Accelerator = Accelerator.L4
    min_vram_mib: int = 0
    high_mem: bool = False
    needs: list[str] = Field(default_factory=list)
    inputs: list[RecipeInput] = Field(default_factory=list)
    stages: list[RecipeStage] = Field(default_factory=list)
    endpoints: list[RecipeEndpoint] = Field(default_factory=list)
    output_dir: str = "/content/ComfyUI/output"
    script_path: str = ""


class DeploymentRun(BaseModel):
    run_id: str
    recipe_id: str
    recipe_name: str
    account_id: str
    session_name: str
    accelerator: Accelerator
    state: Literal["queued", "provisioning", "running", "ready", "failed", "cancelled"] = "queued"
    stage: str = "init"
    progress: float = 0.0
    message: str = ""
    logs: list[str] = Field(default_factory=list)
    endpoints: list[TunnelInfo] = Field(default_factory=list)
    output_dir: str = "/content/ComfyUI/output"
    started_at: str
    finished_at: Optional[str] = None


class UnassignReceipt(BaseModel):
    receipt_id: str
    timestamp: str
    account_id: str
    account_email: str
    session_name: str
    endpoint: str
    accelerator: Accelerator
    reason: str
    duration_seconds: Optional[float] = None
    estimated_ccu_spent: float = 0.0
    balance_after_ccu: Optional[float] = None
    verified_unassigned: bool = False
    backend_mode: BackendMode = BackendMode.MOCK


class HarvestArtifact(BaseModel):
    id: str
    filename: str
    local_path: str
    remote_path: str
    session_name: str
    size_bytes: int
    sha256: str
    media_type: Literal["video", "image", "other"] = "video"
    created_at: str


class BatchShotItem(BaseModel):
    index: int
    prompt: str
    subtitle: str = ""
    seed: int = 42
    state: Literal["pending", "running", "done", "failed"] = "pending"
    artifact_id: Optional[str] = None
    error: Optional[str] = None


class BatchJob(BaseModel):
    job_id: str
    title: str
    session_name: str
    workflow_mode: Literal["wan21_builtin", "custom_json"] = "wan21_builtin"
    width: int = 640
    height: int = 384
    frames: int = 33
    steps: int = 20
    auto_stitch: bool = True
    burn_subtitles: bool = True
    auto_stop: bool = False
    state: Literal["queued", "running", "composing", "done", "failed"] = "queued"
    progress: float = 0.0
    message: str = ""
    shots: list[BatchShotItem] = Field(default_factory=list)
    final_artifact_id: Optional[str] = None
    srt_path: Optional[str] = None
    started_at: str
    finished_at: Optional[str] = None

