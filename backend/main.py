"""Colab Studio unified server & CLI entrypoint."""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from typing import Optional

import httpx
import uvicorn

from backend.api.routes import create_app
from backend.config import WorkspacePaths, build_local_httpx_kwargs
from backend.models import BackendMode
from backend.orchestrator import StudioOrchestrator

DEFAULT_PORT = 8770


def _call_api(api_url: str, method: str, path: str, payload: Optional[dict] = None) -> dict:
    with httpx.Client(base_url=api_url.rstrip("/"), timeout=60.0, **build_local_httpx_kwargs()) as client:
        resp = client.request(method, path, json=payload)
        resp.raise_for_status()
        return resp.json()


def main(argv: Optional[list[str]] = None) -> int:
    raw = argv if argv is not None else sys.argv[1:]
    subcommands = {"status", "adopt", "deploy", "harvest", "stop", "stop-all"}

    if raw and raw[0] in subcommands:
        parser = argparse.ArgumentParser(prog="colab-studio")
        sub = parser.add_subparsers(dest="cmd", required=True)

        p_st = sub.add_parser("status")
        p_st.add_argument("--api-url", default=f"http://127.0.0.1:{DEFAULT_PORT}")

        p_ad = sub.add_parser("adopt")
        p_ad.add_argument("endpoint")
        p_ad.add_argument("--api-url", default=f"http://127.0.0.1:{DEFAULT_PORT}")

        p_dep = sub.add_parser("deploy")
        p_dep.add_argument("recipe_id")
        p_dep.add_argument("--accel", default=None)
        p_dep.add_argument("--session", default=None)
        p_dep.add_argument("--api-url", default=f"http://127.0.0.1:{DEFAULT_PORT}")

        p_har = sub.add_parser("harvest")
        p_har.add_argument("session")
        p_har.add_argument("--auto-stop", action="store_true")
        p_har.add_argument("--api-url", default=f"http://127.0.0.1:{DEFAULT_PORT}")

        p_stop = sub.add_parser("stop")
        p_stop.add_argument("session")
        p_stop.add_argument("--api-url", default=f"http://127.0.0.1:{DEFAULT_PORT}")

        p_all = sub.add_parser("stop-all")
        p_all.add_argument("--api-url", default=f"http://127.0.0.1:{DEFAULT_PORT}")

        args = parser.parse_args(raw)
        if args.cmd == "status":
            res = _call_api(args.api_url, "GET", "/api/bootstrap")
        elif args.cmd == "adopt":
            res = _call_api(args.api_url, "POST", "/api/sessions/adopt", {"endpoint": args.endpoint})
        elif args.cmd == "deploy":
            res = _call_api(
                args.api_url,
                "POST",
                "/api/deployments",
                {"recipe_id": args.recipe_id, "accelerator": args.accel, "session_name": args.session},
            )
        elif args.cmd == "harvest":
            res = _call_api(
                args.api_url,
                "POST",
                f"/api/sessions/{args.session}/harvest",
                {"auto_stop": args.auto_stop},
            )
        elif args.cmd == "stop":
            res = _call_api(args.api_url, "POST", f"/api/sessions/{args.session}/stop")
        else:
            res = _call_api(args.api_url, "POST", "/api/sessions/stop-all")
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    parser = argparse.ArgumentParser(prog="colab-studio", description="Colab Studio GPU Video Workbench Console")
    mode_grp = parser.add_mutually_exclusive_group()
    mode_grp.add_argument("--mock", action="store_true", help="Start in zero-billing Sandbox mode")
    mode_grp.add_argument("--real", action="store_true", help="Start in Real Google Colab mode")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args(raw)

    mode = BackendMode.REAL if args.real else BackendMode.MOCK
    paths = WorkspacePaths.discover()
    orch = StudioOrchestrator(paths, initial_mode=mode)
    app = create_app(orch)

    url = f"http://{args.host}:{args.port}"
    print("=" * 60)
    print(f"  Colab Studio Console  |  {url}")
    print(f"  Backend Mode          |  {mode.value.upper()}")
    print("=" * 60)
    if args.open_browser:
        webbrowser.open(url)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
