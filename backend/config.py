"""Workspace paths and cross-platform proxy configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class WorkspacePaths:
    root: Path
    data_dir: Path
    db_path: Path
    ssh_dir: Path
    outputs_dir: Path
    recipes_dir: Path
    user_recipes_dir: Path
    frontend_dist_dir: Path

    @classmethod
    def discover(cls, home_override: str | Path | None = None) -> "WorkspacePaths":
        project_root = Path(__file__).resolve().parent.parent
        base = Path(home_override or os.environ.get("COLAB_STUDIO_HOME") or project_root).resolve()
        data_dir = base / "data"
        outputs_dir = base / "outputs"
        ssh_dir = data_dir / "ssh"
        user_recipes_dir = data_dir / "recipes"
        for d in (data_dir, outputs_dir, ssh_dir, user_recipes_dir):
            d.mkdir(parents=True, exist_ok=True)
        return cls(
            root=project_root,
            data_dir=data_dir,
            db_path=data_dir / "studio.db",
            ssh_dir=ssh_dir,
            outputs_dir=outputs_dir,
            recipes_dir=project_root / "recipes",
            user_recipes_dir=user_recipes_dir,
            frontend_dist_dir=project_root / "frontend" / "dist",
        )


def resolve_websocket_proxy_kwargs(target_url: str) -> dict[str, Any]:
    """Extracts websocket-client proxy kwargs from environment variables while bypassing loopback."""
    parsed_target = urlparse(target_url)
    host = (parsed_target.hostname or "").lower()
    if host in ("127.0.0.1", "localhost", "::1"):
        return {}

    no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    for item in no_proxy.split(","):
        rule = item.strip().lower()
        if not rule:
            continue
        if rule == "*" or host == rule or (rule.startswith(".") and host.endswith(rule)):
            return {}

    raw_proxy = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
        or os.environ.get("ALL_PROXY")
        or os.environ.get("all_proxy")
        or ""
    ).strip()
    if not raw_proxy:
        return {}

    parsed = urlparse(raw_proxy if "://" in raw_proxy else f"http://{raw_proxy}")
    if not parsed.hostname or not parsed.port:
        return {}

    scheme = (parsed.scheme or "http").lower()
    proxy_type = "http"
    if scheme.startswith("socks5"):
        proxy_type = "socks5h" if scheme == "socks5h" else "socks5"
    elif scheme.startswith("socks4"):
        proxy_type = "socks4"

    kwargs: dict[str, Any] = {
        "http_proxy_host": parsed.hostname,
        "http_proxy_port": parsed.port,
        "proxy_type": proxy_type,
    }
    if parsed.username or parsed.password:
        kwargs["http_proxy_auth"] = (parsed.username or "", parsed.password or "")
    return kwargs


def build_local_httpx_kwargs() -> dict[str, Any]:
    """Ensures CLI-to-localhost HTTP calls never fail on system SOCKS/HTTP proxy variables."""
    return {"trust_env": False}
