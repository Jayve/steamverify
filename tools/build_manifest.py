#!/usr/bin/env python3
"""Build a ``.svm`` manifest from Steam's own depot manifests.

This is the canonical way to add or update a bundled game.  It reads the
Steam client's local caches only -- no network access, no Steam login:

* ``<steam>/steamapps/appmanifest_<appid>.acf`` -- which depots and builds
  are installed;
* ``<steam>/depotcache/<depot>_<manifest>.manifest`` -- the official file
  list with per-file and per-chunk SHA-1 hashes.

Because the output is derived purely from Steam's own data, the resulting
hashes are exactly what ``Verify integrity of game files`` uses.

Example
-------
::

    python tools/build_manifest.py --app-id 292030 --slug witcher3 \\
        --name "The Witcher 3: Wild Hunt" --out manifests

Use ``--dry-run`` to inspect what would be written, ``--no-write`` to skip
updating ``registry.json``, and ``--json`` for machine-readable output.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from steamverify import depot as depot_mod  # noqa: E402
from steamverify import manifest as manifest_mod  # noqa: E402
from steamverify.acf import AppManifest, KeyValuesError  # noqa: E402
from steamverify.manifest import Chunk, FileEntry, ManifestError  # noqa: E402
from steamverify.manifest import load as load_manifest  # noqa: E402
from steamverify.steam import SteamLibrary, find_installations, steam_roots  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
class BuildError(Exception):
    """Something the user can fix."""


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "game"


@dataclass
class BuildStats:
    files: int = 0
    chunks: int = 0
    bytes: int = 0
    depots_used: list[str] = None  # type: ignore[assignment]
    depots_missing: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.depots_used = self.depots_used or []
        self.depots_missing = self.depots_missing or []


# ---------------------------------------------------------------------------
# core
# ---------------------------------------------------------------------------
def _load_acf_for(app_id: str, steam_root: str | None) -> tuple[AppManifest, list[Path]]:
    """Find the ACF for ``app_id``; returns the manifest and depotcache roots."""
    caches: list[Path] = []

    if steam_root:
        root = Path(steam_root)
        acf_path = root / "steamapps" / f"appmanifest_{app_id}.acf"
        if not acf_path.is_file():
            raise BuildError(f"no {acf_path.name} under {root}")
        if (root / "depotcache").is_dir():
            caches.append(root / "depotcache")
        for folder in SteamLibrary(root=root, steamapps=root / "steamapps").library_folders():
            if (folder / "depotcache").is_dir():
                caches.append(folder / "depotcache")
        return AppManifest.load(acf_path), caches

    installations = find_installations(app_id)
    if not installations:
        raise BuildError(
            f"app {app_id} is not installed in any Steam library.\n"
            "Point at the right Steam root with --steam-root, or install the game."
        )

    # Depot manifests live in the Steam client that downloaded them, which is
    # not necessarily the library holding the game, so gather every cache.
    for library in SteamLibrary.discover():
        if library.depotcache.is_dir():
            caches.append(library.depotcache)
    for root in steam_roots():
        candidate = root / "depotcache"
        if candidate.is_dir() and candidate not in caches:
            caches.append(candidate)

    return installations[0].manifest, caches


def build_from_steam(args) -> tuple[manifest_mod.Manifest, BuildStats, Path]:
    """Assemble entries for one app by reading Steam's caches."""
    acf_manifest, caches = _load_acf_for(args.app_id, args.steam_root)
    if not caches:
        raise BuildError(
            "no depotcache directory found.\n"
            "Steam writes depot manifests next to the Steam client root "
            "(<steam>/depotcache). Make sure the game has been installed or "
            "updated at least once with this Steam client."
        )

    depots = [
        (depot_id, acf_manifest.depot_manifest_gid(depot_id))
        for depot_id in acf_manifest.installed_depots
    ]

    entries: dict[str, FileEntry] = {}
    stats = BuildStats()

    for depot_id, gid in depots:
        source = None
        for cache in caches:
            source = depot_mod.find_manifest_file(cache, depot_id, gid)
            if source is not None:
                break
        if source is None:
            stats.depots_missing.append(depot_id)
            if args.verbose:
                print(f"  ! depot {depot_id} manifest not cached "
                      f"(gid {gid or '?'}) -- skipped", file=sys.stderr)
            continue

        try:
            parsed = depot_mod.load(source)
        except depot_mod.DepotManifestError as exc:
            stats.depots_missing.append(depot_id)
            print(f"  ! depot {depot_id}: {exc}", file=sys.stderr)
            continue

        stats.depots_used.append(depot_id)
        kept = 0
        for item in parsed.files:
            if not item.path or item.is_directory or item.is_symlink:
                continue
            chunks = tuple(
                Chunk(offset=c.offset, length=c.length, sha1=c.sha1)
                for c in item.chunks
                if c.sha1 and c.length > 0
            )
            entry = FileEntry(
                path=item.path, size=item.size, sha1=item.sha1, chunks=chunks
            )
            if not entry.sha1 and not entry.chunks:
                continue
            # first depot wins; later depots may re-declare shared paths
            entries.setdefault(entry.path.lower(), entry)
            kept += 1
        if args.verbose:
            print(f"  depot {depot_id:<8} {kept:>6} files  "
                  f"{parsed.total_bytes / 1024 ** 3:>7.2f} GiB  ({source.name})")

    if not entries:
        raise BuildError("no files could be collected -- check --steam-root")

    ordered = sorted(entries.values(), key=lambda e: e.path.lower())
    stats.files = len(ordered)
    stats.chunks = sum(len(e.chunks) for e in ordered)
    stats.bytes = sum(e.size for e in ordered)

    game_name = args.name or acf_manifest.name or f"app {args.app_id}"
    build_id = args.build_id or acf_manifest.build_id
    slug = args.slug or _slugify(game_name)
    target = Path(args.out) / slug

    replay = (
        f"python tools/build_manifest.py --app-id {args.app_id} --slug {slug} "
        f"--name \"{game_name}\" --out manifests"
    )
    header = {
        "slug": slug,
        "game": game_name,
        "app_id": str(args.app_id),
        "build_id": str(build_id),
        "platform": args.platform,
        "vendor": args.vendor or "",
        "language": acf_manifest.language,
        "depots": stats.depots_used,
        "source": "steam depot manifests",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "generator": "steamverify/tools/build_manifest.py",
        "replay": replay,
        "notes": args.notes or "",
    }

    filename = f"{slug}-{build_id or 'unknown'}.svm"
    target_path = target / filename

    if args.dry_run:
        print(f"  would write {target_path} "
              f"({stats.files:,} files, {stats.chunks:,} chunks, "
              f"{stats.bytes / 1024 ** 3:.2f} GiB)")
        placeholder = manifest_mod.Manifest(
            header=dict(header, file_count=stats.files, chunk_count=stats.chunks,
                        total_bytes=stats.bytes),
            files=ordered,
        )
        return placeholder, stats, target_path

    manifest_mod.write(target_path, header, ordered)
    loaded = load_manifest(target_path)
    problems = manifest_mod.validate_coverage(loaded)
    if problems:
        raise BuildError(
            f"generated manifest failed validation ({len(problems)} problems), "
            f"first: {problems[0]}"
        )
    return loaded, stats, target_path


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a .svm hash manifest from Steam's local caches.",
    )
    parser.add_argument("--app-id", required=True, help="Steam app id, e.g. 292030")
    parser.add_argument("--slug", help="short id used in paths and the CLI")
    parser.add_argument("--name", help="display name")
    parser.add_argument("--platform", default="windows",
                        choices=["windows", "linux", "macos"])
    parser.add_argument("--vendor", default="", help="publisher/store, e.g. CD Projekt Red")
    parser.add_argument("--store-url", default="", help="store page URL")
    parser.add_argument("--build-id", help="override the build id from the ACF")
    parser.add_argument("--notes", default="", help="free-form note stored in the header")
    parser.add_argument("--steam-root", help="Steam root (default: auto-detect)")
    parser.add_argument("--out", default=str(ROOT / "manifests"),
                        help="manifests directory (default: ./manifests)")
    parser.add_argument("--default-dir", action="append", metavar="PLATFORM=PATH",
                        help="conventional install dir per platform (repeatable)")
    parser.add_argument("--alias", action="append", metavar="NAME",
                        help="extra CLI aliases (repeatable)")
    parser.add_argument("--no-write", action="store_true",
                        help="build the manifest but leave registry.json alone")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would be written, write nothing")
    parser.add_argument("--json", action="store_true", help="machine-readable summary")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifests_dir = Path(args.out)
    try:
        loaded, stats, manifest_path = build_from_steam(args)
    except (BuildError, KeyValuesError, ManifestError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # --default-dir PLATFORM=PATH (repeatable)
    default_dirs: dict[str, str] = {}
    for item in args.default_dir or []:
        if "=" not in item:
            print(f"error: --default-dir expects PLATFORM=PATH, got {item!r}",
                  file=sys.stderr)
            return 2
        platform, _, path = item.partition("=")
        default_dirs[platform.strip()] = path.strip()

    slug = loaded.slug or args.slug or _slugify(args.name or args.app_id)
    aliases = list(args.alias or [])
    if args.app_id not in aliases:
        aliases.append(args.app_id)

    summary = {
        "slug": slug,
        "manifest": str(manifest_path),
        "sha256": loaded.sha256,
        "build_id": loaded.header.get("build_id", ""),
        "files": stats.files,
        "chunks": stats.chunks,
        "bytes": stats.bytes,
        "depots_used": stats.depots_used,
        "depots_missing": stats.depots_missing,
        "dry_run": bool(args.dry_run),
    }

    if not args.dry_run and not args.no_write:
        from registry_lib import upsert_manifest

        registry_path, _entry = upsert_manifest(
            manifests_dir=manifests_dir,
            slug=slug,
            name=loaded.game,
            app_id=args.app_id,
            platform=args.platform,
            store_url=args.store_url,
            vendor=args.vendor,
            manifest_path=manifest_path,
            loaded=loaded,
            replay=loaded.header.get("replay", ""),
            default_dirs=default_dirs,
            aliases=aliases,
        )
        summary["registry"] = str(registry_path)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"slug        {slug}")
        print(f"manifest    {manifest_path}")
        print(f"sha256      {loaded.sha256}")
        print(f"build       {loaded.header.get('build_id', '?')}")
        print(f"files       {stats.files:,}")
        print(f"chunks      {stats.chunks:,}")
        print(f"size        {stats.bytes / 1024 ** 3:.2f} GiB")
        print(f"depots      {len(stats.depots_used)} used"
              + (f", {len(stats.depots_missing)} missing" if stats.depots_missing else ""))
        if stats.depots_missing:
            print(f"missing     {', '.join(stats.depots_missing)}")
        if summary.get("registry"):
            print(f"registry    {summary['registry']}")
        if args.dry_run:
            print("(dry run -- nothing written)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
