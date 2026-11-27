"""Zero-dependency VM-side cost-guard watchdog.

Runs inside the Colab VM in a detached process group. Even if the local machine
disconnects or sleeps, this watchdog calls ``POST http://$TBE_RUNTIME_ADDR/unassign``
when either the hard deadline expires or both GPU utilization and task heartbeats
remain idle beyond ``--idle-seconds``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

STATE_FILE = Path("/tmp/colab_studio_watchdog.json")


def _gpu_util_percent() -> int:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).strip()
        vals = [int(line.strip()) for line in out.splitlines() if line.strip().isdigit()]
        return max(vals) if vals else 0
    except Exception:
        return 0


def _unassign_self(reason: str) -> None:
    addr = os.environ.get("TBE_RUNTIME_ADDR", "").strip()
    if not addr:
        return
    url = f"http://{addr}/unassign"
    try:
        req = urllib.request.Request(url, method="POST", data=b"")
        urllib.request.urlopen(req, timeout=15)
    except Exception:
        pass
    try:
        STATE_FILE.write_text(json.dumps({"stopped": True, "reason": reason, "ts": time.time()}))
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-seconds", type=int, default=7200)
    parser.add_argument("--idle-seconds", type=int, default=1200)
    parser.add_argument("--heartbeat-file", default="/tmp/colab_studio_heartbeat")
    args = parser.parse_args()

    now = time.time()
    deadline = now + max(60, args.max_seconds)
    last_active = now
    hb_path = Path(args.heartbeat_file)

    while True:
        now = time.time()
        # Allow external deadline extension via STATE_FILE override
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text())
                if isinstance(data.get("deadline_epoch"), (int, float)):
                    deadline = max(deadline, float(data["deadline_epoch"]))
            except Exception:
                pass

        gpu_util = _gpu_util_percent()
        hb_mtime = hb_path.stat().st_mtime if hb_path.exists() else 0.0
        if gpu_util >= 5 or (now - hb_mtime) < 60:
            last_active = now

        try:
            STATE_FILE.write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "deadline_epoch": deadline,
                        "idle_timeout_s": args.idle_seconds,
                        "last_active_epoch": last_active,
                        "gpu_util_percent": gpu_util,
                        "updated_at": now,
                    }
                )
            )
        except Exception:
            pass

        if now >= deadline:
            _unassign_self("max_duration_exceeded")
            return
        if (now - last_active) >= max(120, args.idle_seconds):
            _unassign_self("idle_timeout_exceeded")
            return

        time.sleep(15)


if __name__ == "__main__":
    main()
