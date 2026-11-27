"""Cloud worker for ComfyUI Video Studio (runs inside Colab VM)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

STATUS_FILE = Path(os.environ.get("STUDIO_STATUS_FILE", "/tmp/colab_studio_status.json"))
HEARTBEAT_FILE = Path(os.environ.get("STUDIO_HEARTBEAT_FILE", "/tmp/colab_studio_heartbeat"))
PORT = int(os.environ.get("STUDIO_INPUT_PORT", "8188"))
PRESET = os.environ.get("STUDIO_INPUT_PRESET_MODEL", "wan21_1.3b")
INSTALL_MANAGER = os.environ.get("STUDIO_INPUT_INSTALL_MANAGER", "true").lower() == "true"

_PROTECTED_TORCH_PKGS = frozenset({"torch", "torchvision", "torchaudio"})
_REQ_PKG_RE = re.compile(r"^\s*([A-Za-z0-9._-]+)")


def filter_requirements_lines(lines: list[str]) -> list[str]:
    """Excludes only exact torch/torchvision/torchaudio packages while keeping torchsde etc."""
    out: list[str] = []
    for ln in lines:
        stripped = ln.strip()
        if stripped and not stripped.startswith("#"):
            m = _REQ_PKG_RE.match(stripped)
            if m and m.group(1).lower().replace("_", "-") in _PROTECTED_TORCH_PKGS:
                continue
        out.append(ln)
    return out


def emit(state: str, stage: str, progress: float, message: str) -> None:
    HEARTBEAT_FILE.write_text(str(time.time()))
    STATUS_FILE.write_text(json.dumps({"state": state, "stage": stage, "progress": progress, "message": message}))
    print(f"[{stage}] {message}", flush=True)


def run_cmd(cmd: list[str], cwd: str | None = None) -> None:
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = f"/usr/lib64-nvidia:{env.get('LD_LIBRARY_PATH', '')}"
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def _read_log_tail(log_path: Path, max_lines: int = 20) -> str:
    if not log_path.exists():
        return ""
    return "\n".join(log_path.read_text(errors="replace").splitlines()[-max_lines:])


def main() -> None:
    comfy_dir = Path("/content/ComfyUI")
    emit("running", "setup", 0.15, "Cloning ComfyUI & installing non-torch requirements...")
    if not comfy_dir.exists():
        run_cmd(["git", "clone", "--depth", "1", "https://github.com/comfyanonymous/ComfyUI.git", str(comfy_dir)])
    req_lines = filter_requirements_lines((comfy_dir / "requirements.txt").read_text().splitlines())
    filtered = Path("/tmp/comfy_req.txt")
    filtered.write_text("\n".join(req_lines))
    run_cmd([sys.executable, "-m", "pip", "install", "-q", "-r", str(filtered)])

    if INSTALL_MANAGER:
        mgr = comfy_dir / "custom_nodes" / "ComfyUI-Manager"
        if not mgr.exists():
            run_cmd(["git", "clone", "--depth", "1", "https://github.com/ltdrdata/ComfyUI-Manager.git", str(mgr)])

    if PRESET == "wan21_1.3b":
        emit("running", "models", 0.45, "Downloading Wan2.1-T2V-1.3B weights in parallel (~9.8 GB)...")
        base = "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files"
        targets = [
            (
                f"{base}/diffusion_models/wan2.1_t2v_1.3B_fp16.safetensors",
                comfy_dir / "models/diffusion_models/wan2.1_t2v_1.3B_fp16.safetensors",
            ),
            (
                f"{base}/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
                comfy_dir / "models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
            ),
            (f"{base}/vae/wan_2.1_vae.safetensors", comfy_dir / "models/vae/wan_2.1_vae.safetensors"),
        ]
        procs = []
        for url, dst in targets:
            dst.parent.mkdir(parents=True, exist_ok=True)
            procs.append(subprocess.Popen(["curl", "-fL", "-sS", "-C", "-", url, "-o", str(dst)]))
        while any(p.poll() is None for p in procs):
            HEARTBEAT_FILE.write_text(str(time.time()))
            time.sleep(5)
        if any(p.returncode != 0 for p in procs):
            raise RuntimeError("Failed to download one or more Wan2.1 model files.")
    else:
        emit("running", "models", 0.70, "Skipping model pre-download; launching clean ComfyUI...")

    emit("running", "server", 0.85, f"Starting ComfyUI server on 127.0.0.1:{PORT}...")
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = f"/usr/lib64-nvidia:{env.get('LD_LIBRARY_PATH', '')}"
    log_path = Path("/tmp/comfyui.log")
    with open(log_path, "w", encoding="utf-8") as log_fh:
        proc = subprocess.Popen(
            [sys.executable, "main.py", "--listen", "127.0.0.1", "--port", str(PORT)],
            cwd=str(comfy_dir),
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    for _ in range(90):
        HEARTBEAT_FILE.write_text(str(time.time()))
        if proc.poll() is not None:
            tail = _read_log_tail(log_path)
            raise RuntimeError(f"ComfyUI exited early (code {proc.returncode}):\n{tail}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/system_stats", timeout=2) as r:
                if r.status == 200:
                    emit("ready", "server", 1.0, f"ComfyUI Video Studio live on port {PORT}")
                    return
        except Exception:
            time.sleep(2)
    tail = _read_log_tail(log_path)
    raise RuntimeError(f"ComfyUI failed to listen on port {PORT} within timeout.\n{tail}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        emit("failed", "server", 0.0, str(exc))
        raise
