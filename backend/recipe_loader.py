"""Declarative TOML recipe loader with strict security validation."""

from __future__ import annotations

import logging
import re
import tomllib
from pathlib import Path

from backend.models import Accelerator, RecipeEndpoint, RecipeInput, RecipeSpec, RecipeStage

logger = logging.getLogger(__name__)
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")


def _parse_accelerator(raw: str) -> Accelerator:
    for acc in Accelerator:
        if acc.value == raw.upper().strip():
            return acc
    return Accelerator.L4


def load_recipe_dir(recipe_dir: Path) -> RecipeSpec:
    if recipe_dir.is_symlink():
        raise ValueError(f"Symlink recipe directory forbidden: {recipe_dir}")
    manifest = recipe_dir / "recipe.toml"
    if not manifest.is_file() or manifest.is_symlink():
        raise ValueError(f"Missing or invalid recipe.toml in {recipe_dir}")
    if manifest.stat().st_size > 65536:
        raise ValueError(f"recipe.toml exceeds 64 KiB limit: {manifest}")

    raw = tomllib.loads(manifest.read_text(encoding="utf-8"))
    rid = str(raw.get("id") or "").strip()
    if not ID_PATTERN.match(rid) or rid != recipe_dir.name:
        raise ValueError(f"Invalid recipe id '{rid}' (must match directory '{recipe_dir.name}')")

    script_rel = str((raw.get("run") or {}).get("script") or "worker.py").strip()
    script_file = (recipe_dir / script_rel).resolve()
    if not script_file.is_file() or script_file.is_symlink() or recipe_dir.resolve() not in script_file.parents:
        raise ValueError(f"Invalid worker script path '{script_rel}' in {rid}")

    prov = raw.get("provision") or {}
    inputs = [RecipeInput.model_validate(item) for item in (raw.get("inputs") or [])]
    stages = [RecipeStage.model_validate(item) for item in (raw.get("stages") or [])]

    endpoints: list[RecipeEndpoint] = []
    used_ports: set[int] = set()
    for ep_raw in raw.get("endpoints") or []:
        ep = RecipeEndpoint.model_validate(ep_raw)
        if ep.port == 8080:
            raise ValueError(f"Recipe '{rid}' cannot bind reserved Colab port 8080")
        if ep.port is not None:
            if ep.port in used_ports:
                raise ValueError(f"Duplicate endpoint port {ep.port} in '{rid}'")
            used_ports.add(ep.port)
        endpoints.append(ep)

    return RecipeSpec(
        id=rid,
        name=str(raw.get("name") or rid),
        description=str(raw.get("description") or ""),
        version=str(raw.get("version") or "1.0.0"),
        category=str(raw.get("category") or "video"),
        tags=[str(t) for t in (raw.get("tags") or [])],
        default_accelerator=_parse_accelerator(str(prov.get("accelerator") or "L4")),
        min_vram_mib=int(prov.get("min_vram_mib") or 0),
        high_mem=bool(prov.get("high_mem") or False),
        needs=[str(n) for n in (raw.get("needs") or [])],
        inputs=inputs,
        stages=stages,
        endpoints=endpoints,
        output_dir=str(raw.get("output_dir") or "/content/ComfyUI/output"),
        script_path=str(script_file),
    )


def discover_recipes(builtin_dir: Path, user_dir: Path) -> list[RecipeSpec]:
    found: dict[str, RecipeSpec] = {}
    for root, allow_override in ((builtin_dir, True), (user_dir, False)):
        if not root.exists():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith(".") or child.name == "__pycache__":
                continue
            try:
                spec = load_recipe_dir(child)
                if not allow_override and spec.id in found:
                    logger.warning("User recipe '%s' skipped; cannot override built-in recipe.", spec.id)
                    continue
                found[spec.id] = spec
            except Exception as exc:
                logger.warning("Skipping invalid recipe at %s: %s", child, exc)
    return list(found.values())
