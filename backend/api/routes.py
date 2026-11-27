"""FastAPI application: REST endpoints, WebSocket event stream, media streaming & SPA hosting."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.backends.base import ColabBackendError
from backend.models import Accelerator
from backend.orchestrator import StudioOrchestrator


class OAuthCompleteReq(BaseModel):
    flow_id: str
    auth_code: str


class SecretReq(BaseModel):
    name: str
    value: str


class AdoptReq(BaseModel):
    endpoint: str


class ExtendReq(BaseModel):
    minutes: int = 30


class DeployReq(BaseModel):
    recipe_id: str
    accelerator: Optional[Accelerator] = None
    min_vram_mib: Optional[int] = None
    session_name: Optional[str] = None
    inputs: dict[str, Any] = {}


class HarvestReq(BaseModel):
    output_dir: str = "/content/ComfyUI/output"
    auto_stop: bool = False


class BatchShotReq(BaseModel):
    prompt: str
    subtitle: str = ""
    seed: Optional[int] = None


class BatchJobReq(BaseModel):
    title: str = "批量视频短剧"
    session_name: str
    shots: list[BatchShotReq]
    workflow_mode: str = "wan21_builtin"
    custom_workflow_json: Optional[str] = None
    width: int = 640
    height: int = 384
    frames: int = 33
    steps: int = 20
    auto_stitch: bool = True
    burn_subtitles: bool = True
    auto_stop: bool = False


class ComposeReq(BaseModel):
    artifact_ids: list[str]
    subtitles: list[str] = []
    title: str = "中文字幕合成成片"
    burn_subtitles: bool = True


def create_app(orch: StudioOrchestrator) -> FastAPI:
    app = FastAPI(title="Colab Studio Control Plane", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8770", "http://localhost:8770"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _wrap(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ColabBackendError as exc:
            raise HTTPException(status_code=exc.status_code or 400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "mode": orch.get_settings().backend_mode.value}

    @app.get("/api/bootstrap")
    def bootstrap() -> dict[str, Any]:
        return _wrap(orch.bootstrap_snapshot)

    @app.patch("/api/settings")
    def patch_settings(body: dict[str, Any]) -> dict[str, Any]:
        return _wrap(orch.update_settings, body).model_dump(mode="json")

    @app.post("/api/accounts/oauth/start")
    def oauth_start() -> dict[str, str]:
        return _wrap(orch.begin_oauth)

    @app.post("/api/accounts/oauth/complete")
    def oauth_complete(body: OAuthCompleteReq) -> dict[str, Any]:
        return _wrap(orch.complete_oauth, body.flow_id, body.auth_code).model_dump(mode="json")

    @app.post("/api/accounts/{account_id}/activate")
    def activate_account(account_id: str) -> dict[str, Any]:
        _wrap(orch.set_active_account, account_id)
        return {"ok": True}

    @app.delete("/api/accounts/{account_id}")
    def delete_account(account_id: str) -> dict[str, Any]:
        _wrap(orch.remove_account, account_id)
        return {"ok": True}

    @app.post("/api/secrets")
    def save_secret(body: SecretReq) -> dict[str, Any]:
        return {"secret_names": _wrap(orch.set_secret, body.name.strip(), body.value.strip())}

    @app.delete("/api/secrets/{name}")
    def delete_secret(name: str) -> dict[str, Any]:
        return {"secret_names": _wrap(orch.delete_secret, name)}

    @app.post("/api/sessions/adopt")
    def adopt_session(body: AdoptReq) -> dict[str, Any]:
        return _wrap(orch.adopt_orphan, body.endpoint).model_dump(mode="json")

    @app.post("/api/sessions/{session_name}/extend")
    def extend_session(session_name: str, body: ExtendReq) -> dict[str, Any]:
        return _wrap(orch.extend_watchdog, session_name, body.minutes).model_dump(mode="json")

    @app.post("/api/sessions/{session_name}/harvest")
    def harvest_session(session_name: str, body: HarvestReq) -> dict[str, Any]:
        return _wrap(orch.harvest_outputs, session_name, output_dir=body.output_dir, auto_stop=body.auto_stop)

    @app.post("/api/sessions/{session_name}/stop")
    def stop_session(session_name: str) -> dict[str, Any]:
        return _wrap(orch.stop_session, session_name).model_dump(mode="json")

    @app.post("/api/sessions/stop-all")
    def stop_all() -> dict[str, Any]:
        return _wrap(orch.stop_all)

    @app.post("/api/deployments")
    def deploy(body: DeployReq) -> dict[str, Any]:
        run = _wrap(
            orch.start_deployment,
            recipe_id=body.recipe_id,
            accelerator=body.accelerator,
            min_vram_mib=body.min_vram_mib,
            session_name=body.session_name,
            inputs=body.inputs,
        )
        return run.model_dump(mode="json")

    @app.get("/api/deployments/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        run = orch.db.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        return run.model_dump(mode="json")

    @app.post("/api/batch")
    def create_batch(body: BatchJobReq) -> dict[str, Any]:
        job = _wrap(
            orch.start_batch_job,
            title=body.title,
            session_name=body.session_name,
            shots=[s.model_dump() for s in body.shots],
            workflow_mode=body.workflow_mode,
            custom_workflow_json=body.custom_workflow_json,
            width=body.width,
            height=body.height,
            frames=body.frames,
            steps=body.steps,
            auto_stitch=body.auto_stitch,
            burn_subtitles=body.burn_subtitles,
            auto_stop=body.auto_stop,
        )
        return job.model_dump(mode="json")

    @app.get("/api/batch/{job_id}")
    def get_batch(job_id: str) -> dict[str, Any]:
        job = orch.db.get_batch_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Batch job not found")
        return job.model_dump(mode="json")

    @app.post("/api/compose")
    def compose_video(body: ComposeReq) -> dict[str, Any]:
        return _wrap(
            orch.compose_artifacts,
            artifact_ids=body.artifact_ids,
            subtitles=body.subtitles,
            title=body.title,
            burn_subtitles=body.burn_subtitles,
        )

    @app.get("/api/artifacts/{artifact_id}/file")
    def serve_artifact(artifact_id: str) -> FileResponse:
        art = orch.db.get_artifact(artifact_id)
        if not art:
            raise HTTPException(status_code=404, detail="Artifact not found")
        p = Path(art.local_path)
        if not p.is_file():
            raise HTTPException(status_code=404, detail="File missing on disk")
        return FileResponse(p, filename=art.filename)

    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket) -> None:
        await ws.accept()
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)

        def push(evt: dict[str, Any]) -> None:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, evt)
            except Exception:
                pass

        orch.add_event_listener(push)
        try:
            while True:
                evt = await queue.get()
                await ws.send_json(evt)
        except WebSocketDisconnect:
            pass
        finally:
            orch.remove_event_listener(push)

    # Serve compiled React SPA if present
    dist_dir = orch.paths.frontend_dist_dir
    if (dist_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=str(dist_dir / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        if full_path.startswith("api/") or full_path.startswith("ws/"):
            raise HTTPException(status_code=404, detail="Not found")
        index_html = dist_dir / "index.html"
        if index_html.is_file():
            return FileResponse(index_html)
        return {"message": "Colab Studio API running. Build frontend with `cd frontend && npm run build`."}

    return app
