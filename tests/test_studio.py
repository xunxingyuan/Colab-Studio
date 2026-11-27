"""End-to-end tests for Colab Studio control plane (zero billing, isolated temp home)."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.routes import create_app
from backend.config import WorkspacePaths, resolve_websocket_proxy_kwargs
from backend.models import BackendMode
from backend.orchestrator import StudioOrchestrator
from backend.recipe_loader import load_recipe_dir


def _make_orch(tmp_path: Path) -> StudioOrchestrator:
    paths = WorkspacePaths.discover(home_override=tmp_path)
    return StudioOrchestrator(paths, initial_mode=BackendMode.MOCK, use_memory_vault=True)


def test_proxy_resolution_bypasses_loopback(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    monkeypatch.delenv("NO_PROXY", raising=False)
    assert resolve_websocket_proxy_kwargs("ws://127.0.0.1:8770/ws/events") == {}
    assert resolve_websocket_proxy_kwargs("wss://runtime.colab.dev/colab/ssh") == {
        "http_proxy_host": "127.0.0.1",
        "http_proxy_port": 7897,
        "proxy_type": "http",
    }


def test_recipe_loader_forbids_port_8080(tmp_path: Path):
    rdir = tmp_path / "bad-recipe"
    rdir.mkdir()
    (rdir / "worker.py").write_text("print('hi')")
    (rdir / "recipe.toml").write_text(
        'id = "bad-recipe"\nname = "Bad"\n[[endpoints]]\nid = "x"\nlabel = "X"\nport = 8080\n'
    )
    try:
        load_recipe_dir(rdir)
        assert False, "Expected port 8080 ValueError"
    except ValueError as exc:
        assert "8080" in str(exc)


def test_full_video_workbench_lifecycle_api(tmp_path: Path):
    orch = _make_orch(tmp_path)
    app = create_app(orch)
    client = TestClient(app)

    # 1. Bootstrap returns seeded mock account, 1 orphan L4, and 2 video recipes
    snap = client.get("/api/bootstrap").json()
    assert snap["settings"]["backend_mode"] == "mock"
    assert len(snap["recipes"]) >= 2
    orphans = [s for s in snap["sessions"] if not s["managed"]]
    assert len(orphans) == 1

    # 2. One-click adopt orphan + inject watchdog
    adopted = client.post("/api/sessions/adopt", json={"endpoint": orphans[0]["endpoint"]}).json()
    assert adopted["managed"] is True
    assert adopted["adopted"] is True
    assert adopted["watchdog"]["active"] is True

    # 3. Deploy comfyui-video reusing the adopted session
    run = client.post(
        "/api/deployments",
        json={"recipe_id": "comfyui-video", "session_name": adopted["name"]},
    ).json()
    for _ in range(30):
        cur = client.get(f"/api/deployments/{run['run_id']}").json()
        if cur["state"] in ("ready", "failed"):
            break
        time.sleep(0.1)
    assert cur["state"] == "ready"
    assert len(cur["endpoints"]) == 1

    # 4. Harvest outputs with auto_stop=True -> verifies SHA256 artifact + unassign receipt
    harv = client.post(
        f"/api/sessions/{adopted['name']}/harvest",
        json={"auto_stop": True},
    ).json()
    assert len(harv["harvested"]) >= 1
    assert len(harv["harvested"][0]["sha256"]) == 64
    assert harv["receipt"]["verified_unassigned"] is True

    # 5. Verify zero remaining sessions
    after = client.get("/api/bootstrap").json()
    assert len(after["sessions"]) == 0
    assert len(after["artifacts"]) >= 1
    assert len(after["receipts"]) >= 1


def test_batch_comfyui_queue_and_chinese_subtitle_compose(tmp_path: Path):
    orch = _make_orch(tmp_path)
    app = create_app(orch)
    client = TestClient(app)

    # Adopt mock L4 session
    snap = client.get("/api/bootstrap").json()
    orphan_ep = snap["sessions"][0]["endpoint"]
    adopted = client.post("/api/sessions/adopt", json={"endpoint": orphan_ep}).json()

    # Submit 2-shot batch job with Chinese subtitles, auto_stitch + burn_subtitles + auto_stop
    job = client.post(
        "/api/batch",
        json={
            "title": "皮衣归来测试",
            "session_name": adopted["name"],
            "shots": [
                {"prompt": "Stormy boardroom", "subtitle": "董事会拍桌放狠话：公司离了你照样转！"},
                {"prompt": "Wall Street green +31%", "subtitle": "王者归来！单日暴涨 31%！"},
            ],
            "auto_stitch": True,
            "burn_subtitles": True,
            "auto_stop": True,
        },
    ).json()

    for _ in range(60):
        cur = client.get(f"/api/batch/{job['job_id']}").json()
        if cur["state"] in ("done", "failed"):
            break
        time.sleep(0.2)

    assert cur["state"] == "done", cur["message"]
    assert cur["final_artifact_id"] is not None
    assert cur["srt_path"] is not None
    assert Path(cur["srt_path"]).is_file()
    srt_text = Path(cur["srt_path"]).read_text(encoding="utf-8")
    assert "王者归来！单日暴涨 31%！" in srt_text

    # Also test standalone /api/compose on the 2 individual shot artifacts
    shot_ids = [s["artifact_id"] for s in cur["shots"]]
    comp = client.post(
        "/api/compose",
        json={
            "artifact_ids": shot_ids,
            "subtitles": ["第一句中文字幕", "第二句中文字幕"],
            "title": "独立字幕合成测试",
            "burn_subtitles": True,
        },
    ).json()
    assert comp["artifact"]["size_bytes"] > 1000
    assert Path(comp["artifact"]["local_path"]).is_file()

    # Auto-stop should have verified 0 remaining sessions
    final_snap = client.get("/api/bootstrap").json()
    assert len(final_snap["sessions"]) == 0


def test_comfyui_requirements_filter_preserves_torchsde():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "comfy_worker",
        Path(__file__).resolve().parent.parent / "recipes" / "comfyui-video" / "worker.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    filtered = mod.filter_requirements_lines(
        ["torch>=2.0", "torchvision", "torchaudio==2.5", "torchsde", "einops", "# comment"]
    )
    assert "torchsde" in filtered
    assert "einops" in filtered
    assert not any(x.startswith("torch>=") or x == "torchvision" or x.startswith("torchaudio") for x in filtered)


