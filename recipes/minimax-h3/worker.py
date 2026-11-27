"""Cloud worker for MiniMax-H3 Video CharacterSwap Studio."""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

STATUS_FILE = Path(os.environ.get("STUDIO_STATUS_FILE", "/tmp/colab_studio_status.json"))
HEARTBEAT_FILE = Path(os.environ.get("STUDIO_HEARTBEAT_FILE", "/tmp/colab_studio_heartbeat"))
PORT = int(os.environ.get("STUDIO_INPUT_PORT", "8188"))


def emit(state: str, stage: str, progress: float, message: str) -> None:
    HEARTBEAT_FILE.write_text(str(time.time()))
    STATUS_FILE.write_text(json.dumps({"state": state, "stage": stage, "progress": progress, "message": message}))


def main() -> None:
    comfy_dir = Path("/content/ComfyUI")
    emit("running", "setup", 0.25, "Preparing ComfyUI for MiniMax-H3 on high-VRAM GPU...")
    if not comfy_dir.exists():
        subprocess.run(
            ["git", "clone", "--depth", "1", "https://github.com/comfyanonymous/ComfyUI.git", str(comfy_dir)],
            check=True,
        )
    emit("running", "models", 0.65, "Ensuring model directories & runtime environment...")
    for sub in ("diffusion_models", "vae", "text_encoders", "loras"):
        (comfy_dir / "models" / sub).mkdir(parents=True, exist_ok=True)

    emit("running", "server", 0.85, f"Launching ComfyUI on 127.0.0.1:{PORT}...")
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = f"/usr/lib64-nvidia:{env.get('LD_LIBRARY_PATH', '')}"
    subprocess.Popen(
        ["python3", "main.py", "--listen", "127.0.0.1", "--port", str(PORT)],
        cwd=str(comfy_dir),
        env=env,
        stdout=open("/tmp/minimax_comfyui.log", "w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    for _ in range(90):
        HEARTBEAT_FILE.write_text(str(time.time()))
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/system_stats", timeout=2) as r:
                if r.status == 200:
                    emit("ready", "server", 1.0, f"MiniMax-H3 Studio live on port {PORT}")
                    return
        except Exception:
            time.sleep(2)
    raise RuntimeError(f"MiniMax-H3 ComfyUI did not start on port {PORT}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        emit("failed", "server", 0.0, str(exc))
        raise
