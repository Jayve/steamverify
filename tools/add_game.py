#!/usr/bin/env python3
"""Register an existing ``.svm`` manifest in ``manifests/registry.json``.

``build_manifest.py`` does this automatically; use this when you copied a
manifest in from elsewhere, want to hand-edit metadata, or need to prune
records whose files were deleted.

Examples
--------
::

    # register a manifest, making it the default build
    python tools/add_game.py --manifest manifests/somegame/somegame-123.svm \\
        --slug somegame --name "Some Game" --app-id 1234

    # list what is registered
    python tools/add_game.py --list

    # drop records whose manifest file is gone
    python tools/add_game.py --prune
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from registry_lib import load_registry, prune_missing_files, upsert_manifest  # noqa: E402

from steamverify.manifest import ManifestError  # noqa: E402
from steamverify.manifest import load as load_manifest  # noqa: E402


def cmd_list(manifests_dir: Path, as_json: bool) -> int:
    data = load_registry(manifests_dir)
    games = data.get("games", {})
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    if not games:
        print("registry is empty")
        return 0
    for slug, entry in sorted(games.items()):
        print(f"{entry.get('name', slug)}  [{slug}]")
        if entry.get("app_id"):
            print(f"    app id     {entry['app_id']}")
        print(f"    current    {entry.get('current_build') or '(none)'}")
        for record in entry.get("manifests", []):
            mark = "*" if record.get("build_id") == entry.get("current_build") else " "
            print(
                f"  {mark} {record.get('build_id', '?'):<12} "
                f"{record.get('file_count', 0):>6,} files  "
                f"{record.get('total_bytes', 0) / 1024 ** 3:>7.2f} GiB  "
                f"{record.get('file')}"
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", help="path to the .svm manifest to register")
    parser.add_argument("--slug")
    parser.add_argument("--name")
    parser.add_argument("--app-id", default="")
    parser.add_argument("--vendor", default="")
    parser.add_argument("--store-url", default="")
    parser.add_argument("--platform", default="windows",
                        choices=["windows", "linux", "macos"])
    parser.add_argument("--alias", action="append", metavar="NAME")
    parser.add_argument("--default-dir", action="append", metavar="PLATFORM=PATH")
    parser.add_argument("--keep-current", action="store_true",
                        help="do not make this the default build")
    parser.add_argument("--list", action="store_true", help="show the registry")
    parser.add_argument("--prune", action="store_true",
                        help="remove records whose manifest file is missing")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", default=str(ROOT / "manifests"))
    args = parser.parse_args(argv)

    manifests_dir = Path(args.out)

    if args.list:
        return cmd_list(manifests_dir, args.json)

    if args.prune:
        removed = prune_missing_files(manifests_dir)
        if args.json:
            print(json.dumps({"removed": removed}, indent=2))
        else:
            print(f"removed {len(removed)} stale record(s)")
            for item in removed:
                print(f"  - {item}")
        return 0

    if not args.manifest:
        parser.error("--manifest is required (or use --list / --prune)")

    manifest_path = Path(args.manifest)
    try:
        loaded = load_manifest(manifest_path)
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    slug = args.slug or loaded.slug or loaded.game.lower().replace(" ", "-")
    # keep the manifest inside manifests/<slug>/ so relative paths stay valid
    target_dir = manifests_dir / slug
    target_dir.mkdir(parents=True, exist_ok=True)
    if manifest_path.parent.resolve() != target_dir.resolve():
        destination = target_dir / manifest_path.name
        if destination.resolve() != manifest_path.resolve():
            destination.write_bytes(manifest_path.read_bytes())
            manifest_path = destination
            loaded = load_manifest(manifest_path)
        else:
            manifest_path = destination

    default_dirs: dict[str, str] = {}
    for item in args.default_dir or []:
        if "=" not in item:
            parser.error(f"--default-dir expects PLATFORM=PATH, got {item!r}")
        platform, _, path = item.partition("=")
        default_dirs[platform.strip()] = path.strip()

    registry_path, entry = upsert_manifest(
        manifests_dir=manifests_dir,
        slug=slug,
        name=args.name or loaded.game,
        app_id=args.app_id or loaded.app_id,
        platform=args.platform or loaded.platform,
        store_url=args.store_url,
        vendor=args.vendor,
        manifest_path=manifest_path,
        loaded=loaded,
        replay=loaded.header.get("replay", ""),
        default_dirs=default_dirs,
        aliases=list(args.alias or []),
        make_current=not args.keep_current,
    )

    if args.json:
        print(json.dumps({"registry": str(registry_path), "game": entry},
                         ensure_ascii=False, indent=2))
    else:
        print(f"registered {slug} build {loaded.build_id or '?'} in {registry_path}")
        print(f"  manifest  {manifest_path}")
        print(f"  sha256    {loaded.sha256}")
        print(f"  files     {loaded.file_count:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
