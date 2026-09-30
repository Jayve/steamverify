"""Render scan results as text, JSON or CSV.

Reports are written for humans first: the summary answers "is my install
clean?", then the offending files are listed with a reason and a suggested
next step.  JSON output is the machine-readable form used by CI.
"""

from __future__ import annotations

import csv
import io
import json
import os
import time
from pathlib import Path

from . import __version__
from .scanner import EXTRA, MISSING, MODIFIED, STUB, VERIFIED, ScanResult

__all__ = [
    "render_text",
    "render_json",
    "write_csv",
    "summary_dict",
    "format_bytes",
    "format_count",
]

ANSI = {
    "reset": "\x1b[0m",
    "bold": "\x1b[1m",
    "dim": "\x1b[2m",
    "red": "\x1b[31m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "blue": "\x1b[34m",
    "cyan": "\x1b[36m",
}


def format_bytes(value: int) -> str:
    """Human-readable byte count using binary units."""
    sign = "-" if value < 0 else ""
    value = abs(value)
    for unit, scale in (("GiB", 1024 ** 3), ("MiB", 1024 ** 2), ("KiB", 1024)):
        if value >= scale:
            return f"{sign}{value / scale:.2f} {unit}"
    return f"{sign}{value} B"


def format_count(value: int) -> str:
    return f"{value:,}"


class _Palette:
    """Colour helper that degrades to plain text."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, *styles: str) -> str:
        if not self.enabled or not styles:
            return text
        prefix = "".join(ANSI.get(style, "") for style in styles)
        return f"{prefix}{text}{ANSI['reset']}"


def _should_colour(mode: str) -> bool:
    if mode == "always":
        return True
    if mode == "never":
        return False
    if os.environ.get("NO_COLOR"):
        return False
    return os.isatty(1)


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------
def summary_dict(result: ScanResult) -> dict:
    """Compact, serialisable overview of a scan."""
    manifest = result.manifest
    return {
        "tool": "steamverify",
        "tool_version": __version__,
        "game": manifest.game,
        "slug": manifest.slug,
        "app_id": manifest.app_id,
        "build_id": manifest.build_id,
        "manifest_sha256": manifest.sha256,
        "manifest_source": manifest.source,
        "root": str(result.root),
        "scanned_utc": result.started_utc,
        "elapsed_seconds": round(result.elapsed_seconds, 3),
        "hash_verified": not result.size_only,
        "official_files": manifest.file_count,
        "official_bytes": manifest.total_bytes,
        "local_files": result.local_file_count,
        "verified_files": len(result.verified),
        "verified_bytes": result.verified_bytes,
        "modified_files": len(result.modified),
        "stub_files": len(result.stubs),
        "extra_files": len(result.extra),
        "extra_bytes": result.extra_bytes,
        "missing_files": len(result.missing),
        "ignored_entries": len(result.ignored),
        "errors": len(result.errors),
        "clean": result.is_clean,
        "game_data_intact": result.game_data_intact,
    }


def render_json(result: ScanResult, *, indent: int | None = 2) -> str:
    payload = {
        "summary": summary_dict(result),
        "modified": [r.as_dict() for r in result.modified],
        "stub": [r.as_dict() for r in result.stubs],
        "extra": [r.as_dict() for r in result.extra],
        "missing": [
            {"path": e.path, "size": e.size, "sha1": e.sha1} for e in result.missing
        ],
        "errors": result.errors,
        "ignored": result.ignored,
        "extra_groups": [
            {"group": group, "files": count, "bytes": size}
            for group, count, size in result.extra_groups()
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=indent)


def write_csv(result: ScanResult, path: str | Path) -> Path:
    """Write every local file with its verdict to a CSV file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["status", "path", "size", "expected_size", "size_delta",
             "mtime", "sha1", "expected_sha1", "reason"]
        )
        for item in sorted(
            result.verified + result.modified + result.stubs + result.extra,
            key=lambda r: r.path.lower(),
        ):
            writer.writerow(
                [
                    item.category,
                    item.path,
                    item.size,
                    item.expected_size or "",
                    item.size_delta if item.expected_size else "",
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(item.mtime))
                    if item.mtime else "",
                    item.sha1,
                    item.expected_sha1 if item.expected_sha1 != item.sha1 else "",
                    item.reason,
                ]
            )
        for entry in result.missing:
            writer.writerow(["missing", entry.path, "", entry.size, "", "", "",
                             entry.sha1, "tracked file is absent"])
    return path


# ---------------------------------------------------------------------------
# text
# ---------------------------------------------------------------------------
def render_text(
    result: ScanResult,
    *,
    colour: str = "auto",
    max_list: int = 25,
    verbose: bool = False,
    root_label: str | None = None,
) -> str:
    """Build the human-readable report."""
    c = _Palette(_should_colour(colour))
    manifest = result.manifest
    out = io.StringIO()
    line = "-" * 72

    def emit(text: str = "") -> None:
        out.write(text + "\n")

    emit(c("steamverify", "bold") + c(f"  v{__version__}", "dim"))
    emit(c(line, "dim"))
    emit(f"  game        {c(manifest.game, 'bold')}")
    if manifest.app_id != "?":
        emit(f"  app id      {manifest.app_id}")
    if manifest.build_id:
        emit(f"  build       {manifest.build_id}")
    emit(f"  manifest    {manifest.file_count:,} files, "
         f"{manifest.chunk_count:,} chunks, {format_bytes(manifest.total_bytes)}")
    if manifest.sha256:
        emit(f"  manifest id sha256:{manifest.sha256[:16]}")
    emit(f"  directory   {root_label or result.root}")
    emit(f"  scanned     {result.started_utc}  ({result.elapsed_seconds:.1f}s)")
    if result.size_only:
        emit(c("  mode        size-only (content hashes were NOT checked)", "yellow"))
    emit(c(line, "dim"))
    emit()

    counts = result.counts()
    emit(c("Summary", "bold"))
    rows = [
        ("verified", counts[VERIFIED], "files match the official manifest", "green"),
        ("modified", counts[MODIFIED], "tracked files whose content differs", "red"),
        ("stub", counts[STUB], "content-free placeholder entries (DLC markers)", "cyan"),
        ("extra", counts[EXTRA], "files not present in any official manifest", "yellow"),
        ("missing", counts[MISSING], "official files that are absent", "red"),
    ]
    width = max(len(name) for name, *_ in rows)
    for name, value, description, style in rows:
        marker = c("#", style) if value else c(".", "dim")
        text = c(f"{value:>7,}", style if value else "dim")
        emit(f"  {marker} {name:<{width}}  {text}   {c(description, 'dim')}")
    if result.ignored:
        emit(f"  {c('-', 'dim')} {'ignored':<{width}}  "
             f"{c(f'{len(result.ignored):>7,}', 'dim')}   "
             f"{c('skipped by ignore rules', 'dim')}")
    if result.errors:
        emit(f"  {c('!', 'red')} {'errors':<{width}}  "
             f"{c(f'{len(result.errors):>7,}', 'red')}   "
             f"{c('files that could not be read', 'dim')}")
    emit()
    emit(f"  {format_count(len(result.verified))} files / "
         f"{format_bytes(result.verified_bytes)} verified against the official build")
    if result.extra:
        emit(f"  {format_count(len(result.extra))} extra files / "
             f"{format_bytes(result.extra_bytes)} not in the official build")

    # -- verdict ---------------------------------------------------------
    emit()
    if result.is_clean:
        emit(c("  RESULT: clean -- this install matches the official manifest.", "green", "bold"))
    elif result.modified or result.missing:
        emit(c("  RESULT: NOT clean -- official game data differs from the manifest.",
               "red", "bold"))
    elif result.stubs:
        emit(c("  RESULT: game data intact; only content-free DLC marker files "
               "differ.", "cyan", "bold"))
    else:
        emit(c("  RESULT: official files intact, but extra files are present.",
               "yellow", "bold"))

    # -- extra groups ----------------------------------------------------
    groups = result.extra_groups()
    if groups:
        emit()
        emit(c("Extra files by location", "bold"))
        for group, count, size in groups[:max_list]:
            emit(f"  {count:>7,} files  {format_bytes(size):>12}   {group}")
        if len(groups) > max_list:
            emit(c(f"  ... {len(groups) - max_list} more groups "
                   f"(use --json or --csv for the full list)", "dim"))

    # -- stubs -----------------------------------------------------------
    if result.stubs:
        emit()
        emit(c("Placeholder entries (not a corruption signal)", "bold"))
        for item in result.stubs[:max_list]:
            emit(f"  {c('S', 'cyan')} {item.path}")
        if len(result.stubs) > max_list:
            emit(c(f"  ... {len(result.stubs) - max_list} more", "dim"))
        emit(c("  These official entries carry an identifier hash but are "
               "legitimately empty on disk.", "dim"))

    # -- modified --------------------------------------------------------
    if result.modified:
        emit()
        emit(c("Modified files", "bold"))
        for item in result.modified[:max_list]:
            emit(f"  {c('M', 'red')} {item.path}")
            emit(c(f"      {item.reason}", "dim"))
            if item.bad_offsets:
                shown = ", ".join(f"{off:,}" for off in item.bad_offsets[:4])
                emit(c(f"      first bad chunk offsets: {shown}", "dim"))
        if len(result.modified) > max_list:
            emit(c(f"  ... {len(result.modified) - max_list} more "
                   f"(use --json or --csv)", "dim"))

    # -- missing ---------------------------------------------------------
    if result.missing:
        emit()
        emit(c("Missing files", "bold"))
        for entry in result.missing[:max_list]:
            emit(f"  {c('-', 'red')} {entry.path}  {format_bytes(entry.size)}")
        if len(result.missing) > max_list:
            emit(c(f"  ... {len(result.missing) - max_list} more", "dim"))

    # -- extras detail ---------------------------------------------------
    if verbose and result.extra:
        emit()
        emit(c("All extra files", "bold"))
        for item in result.extra:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(item.mtime)) if item.mtime else ""
            emit(f"  {format_bytes(item.size):>12}  {when:<17} {item.path}")

    # -- errors ----------------------------------------------------------
    if result.errors:
        emit()
        emit(c("Errors", "bold"))
        for message in result.errors[:max_list]:
            emit(f"  {c('!', 'red')} {message}")

    # -- next steps ------------------------------------------------------
    emit()
    emit(c("What to do", "bold"))
    if result.is_clean:
        emit("  Nothing. If Steam still reports problems, verify with Steam itself.")
    else:
        if result.modified or result.missing:
            emit("  1. In Steam: right-click the game > Properties > Installed Files")
            emit("     > 'Verify integrity of game files', then re-run this scan.")
            emit("     Files that still differ are usually mods that overwrite game data.")
        if result.extra:
            emit(f"  {'2' if (result.modified or result.missing) else '1'}."
                 " Extra files are not part of the official build. Mods, trainers,")
            emit("     save games, crash dumps and repack leftovers show up here.")
            emit("     Delete what you do not want; the game itself is unaffected.")
    emit()
    emit(c("  Exit code: " + str(_exit_code(result)), "dim"))
    return out.getvalue()


def _exit_code(result: ScanResult) -> int:
    if result.errors:
        return 3
    if result.modified or result.stubs or result.missing:
        return 2
    if result.extra:
        return 1
    return 0
