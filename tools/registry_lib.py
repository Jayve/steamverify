"""Shared helpers for tools that write ``manifests/registry.json``.

Kept separate so ``build_manifest.py``, ``build_from_directory.py`` and
``add_game.py`` all agree on the registry schema.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from steamverify.manifest import Manifest  # noqa: E402

REGISTRY_FILENAME = "registry.json"
SCHEMA_VERSION = 1


def load_registry(manifests_dir: Path) -> dict:
    path = manifests_dir / REGISTRY_FILENAME
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    return {"version": SCHEMA_VERSION, "games": {}}


def save_registry(manifests_dir: Path, data: dict) -> Path:
    """Write ``registry.json`` with stable, platform-independent bytes.

    Newlines are pinned to ``\\n`` so regenerating the registry on Windows and
    on Linux produces identical files, and therefore identical diffs.
    """
    path = manifests_dir / REGISTRY_FILENAME
    data["version"] = SCHEMA_VERSION
    data["generated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def upsert_manifest(
    manifests_dir: Path,
    *,
    slug: str,
    name: str,
    app_id: str = "",
    platform: str = "windows",
    store_url: str = "",
    vendor: str = "",
    manifest_path: Path,
    loaded: Manifest,
    replay: str = "",
    default_dirs: dict[str, str] | None = None,
    aliases: list[str] | None = None,
    make_current: bool = True,
) -> tuple[Path, dict]:
    """Insert or update one game and one of its manifests.

    Returns ``(registry_path, game_entry)``.
    """
    data = load_registry(manifests_dir)
    games = data.setdefault("games", {})
    entry = games.setdefault(slug, {})

    alias_set = set(entry.get("aliases", []))
    alias_set.update(aliases or [])
    if app_id:
        alias_set.add(str(app_id))

    entry.update(
        {
            "slug": slug,
            "name": name or entry.get("name", slug),
            "directory": entry.get("directory", slug),
            "app_id": str(app_id or entry.get("app_id", "")),
            "platform": platform or entry.get("platform", "windows"),
            "vendor": vendor or entry.get("vendor", ""),
            "store_url": store_url or entry.get("store_url", ""),
            "aliases": sorted(a for a in alias_set if a),
        }
    )
    if default_dirs:
        merged = dict(entry.get("default_dirs", {}))
        merged.update(default_dirs)
        entry["default_dirs"] = merged
    if replay:
        entry["replay"] = replay

    records = {r.get("file"): dict(r) for r in entry.get("manifests", []) if r.get("file")}
    records[manifest_path.name] = {
        "file": manifest_path.name,
        "sha256": loaded.sha256,
        "build_id": str(loaded.header.get("build_id", "")),
        "app_id": str(loaded.header.get("app_id", app_id)),
        "platform": str(loaded.header.get("platform", platform)),
        "file_count": loaded.file_count,
        "chunk_count": loaded.chunk_count,
        "total_bytes": loaded.total_bytes,
        "generated": str(loaded.header.get("generated", "")),
        "note": str(loaded.header.get("notes", "")),
    }
    entry["manifests"] = sorted(
        records.values(), key=lambda r: str(r.get("build_id", ""))
    )

    # Only one build is "current": the one just added, unless told otherwise.
    if make_current or not entry.get("current_build"):
        entry["current_build"] = str(loaded.header.get("build_id", ""))

    return save_registry(manifests_dir, data), entry


def prune_missing_files(manifests_dir: Path) -> list[str]:
    """Drop registry records whose manifest file no longer exists."""
    data = load_registry(manifests_dir)
    removed: list[str] = []
    for slug, entry in data.get("games", {}).items():
        directory = manifests_dir / entry.get("directory", slug)
        kept = []
        for record in entry.get("manifests", []):
            if (directory / record.get("file", "")).is_file():
                kept.append(record)
            else:
                removed.append(f"{slug}/{record.get('file')}")
        entry["manifests"] = kept
        if kept and entry.get("current_build") not in {r.get("build_id") for r in kept}:
            entry["current_build"] = str(kept[-1].get("build_id", ""))
    save_registry(manifests_dir, data)
    return removed
