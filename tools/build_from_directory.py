#!/usr/bin/env python3
"""Build a ``.svm`` manifest by hashing a directory you already trust.

Use this when Steam's depot manifests are unavailable or when the game is not
from Steam (GOG, Epic, a portable copy, a preserved archive).  Point it at a
*known-good* install and it records what "official" means for that build.

.. warning::
    A manifest is only as trustworthy as the directory it was built from.
    Build from a freshly downloaded, never-modded copy, and never from the
    machine you are trying to diagnose.

Two chunking modes are supported:

``--chunk-size`` (default ``0`` = whole-file hashes)
    ``0`` hashes each file in one piece.  Any non-zero value splits files into
    fixed-size chunks so a partially damaged file can be localised.  If you
    are regenerating a manifest for a game whose files Steam stores as
    SteamPipe archives, prefer ``tools/build_manifest.py`` instead -- that
    reproduces Steam's own chunk boundaries exactly, which this cannot.

Example
-------
::

    python tools/build_from_directory.py --dir "D:/Games/SomeGame" \\
        --slug somegame --name "Some Game" --out manifests
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from steamverify import manifest as manifest_mod  # noqa: E402
from steamverify.manifest import Chunk, FileEntry  # noqa: E402

READ_BLOCK = 8 << 20


class BuildError(Exception):
    """Something the user can fix."""


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "game"


def _sha1_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def hash_entry(path: Path, relative: str, chunk_size: int) -> FileEntry | None:
    """Hash one file, returning a manifest entry (or ``None`` if unreadable)."""
    try:
        size = path.stat().st_size
    except OSError:
        return None

    if chunk_size <= 0:
        digest = hashlib.sha1()
        try:
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(READ_BLOCK), b""):
                    digest.update(block)
        except OSError:
            return None
        return FileEntry(path=relative, size=size, sha1=digest.hexdigest())

    chunks: list[Chunk] = []
    try:
        with path.open("rb") as handle:
            offset = 0
            while True:
                block = handle.read(chunk_size)
                if not block:
                    break
                chunks.append(Chunk(offset=offset, length=len(block), sha1=_sha1_bytes(block)))
                offset += len(block)
    except OSError:
        return None

    if not chunks:
        return FileEntry(path=relative, size=0, sha1=_sha1_bytes(b""))
    return FileEntry(path=relative, size=size, sha1="", chunks=tuple(chunks))


def collect(root: Path, chunk_size: int, ignore: list[str], workers: int):
    """Hash every file under ``root`` in parallel."""
    targets: list[tuple[Path, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        current = Path(dirpath)
        for name in filenames:
            full = current / name
            if full.is_symlink():
                continue
            try:
                relative = full.relative_to(root).as_posix()
            except ValueError:
                continue
            if any(fnmatch.fnmatch(relative.lower(), pattern.lower()) for pattern in ignore):
                continue
            targets.append((full, relative))

    entries: list[FileEntry] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for entry in pool.map(lambda t: hash_entry(t[0], t[1], chunk_size), targets):
            if entry is not None:
                entries.append(entry)
    entries.sort(key=lambda e: e.path.lower())
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", required=True, help="directory to hash")
    parser.add_argument("--slug", help="short id (default: derived from --name)")
    parser.add_argument("--name", help="display name")
    parser.add_argument("--app-id", default="", help="optional store app id")
    parser.add_argument("--platform", default="windows",
                        choices=["windows", "linux", "macos"])
    parser.add_argument("--build-id", default="", help="label for this build")
    parser.add_argument("--version", default="", help="game version label")
    parser.add_argument("--chunk-size", type=int, default=0,
                        help="chunk size in bytes (0 = whole-file hashes)")
    parser.add_argument("--ignore", action="append", metavar="GLOB",
                        help="skip matching paths (repeatable)")
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--out", default=str(ROOT / "manifests"))
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.dir).expanduser()
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2

    workers = args.workers or min(32, (os.cpu_count() or 4) * 2)
    started = time.time()
    entries = collect(root, args.chunk_size, list(args.ignore or []), workers)
    if not entries:
        print("error: no files found", file=sys.stderr)
        return 1

    name = args.name or root.name
    slug = args.slug or _slugify(name)
    build = args.build_id or args.version or "local"
    total_bytes = sum(e.size for e in entries)
    chunk_count = sum(len(e.chunks) for e in entries)

    header = {
        "slug": slug,
        "game": name,
        "app_id": str(args.app_id),
        "build_id": build,
        "platform": args.platform,
        "version": args.version,
        "source": "hashed from directory",
        "chunk_size": args.chunk_size,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "generator": "steamverify/tools/build_from_directory.py",
        "notes": f"hashed from {root}",
    }

    target_dir = Path(args.out) / slug
    target = target_dir / f"{slug}-{build}.svm"

    summary = {
        "slug": slug,
        "manifest": str(target),
        "files": len(entries),
        "chunks": chunk_count,
        "bytes": total_bytes,
        "chunk_size": args.chunk_size,
        "seconds": round(time.time() - started, 1),
        "dry_run": bool(args.dry_run),
    }

    if not args.dry_run:
        header_written = manifest_mod.write(target, header, entries)
        loaded = manifest_mod.load(target)
        problems = manifest_mod.validate_coverage(loaded)
        if problems:
            print(f"error: validation failed: {problems[0]}", file=sys.stderr)
            return 1
        summary["sha256"] = loaded.sha256
        summary["header"] = header_written

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"slug      {slug}")
        print(f"manifest  {target}")
        print(f"files     {len(entries):,}")
        print(f"chunks    {chunk_count:,}")
        print(f"size      {total_bytes / 1024 ** 3:.2f} GiB")
        print(f"chunking  {'whole-file' if args.chunk_size <= 0 else str(args.chunk_size) + ' bytes'}")
        print(f"took      {summary['seconds']}s")
        if args.dry_run:
            print("(dry run -- nothing written)")
        elif not args.no_write:
            print("\nAdd it to manifests/registry.json (tools/add_game.py) to use it "
                  "via --game.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
