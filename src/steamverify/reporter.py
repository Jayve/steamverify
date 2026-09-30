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
from .i18n import get_translator
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


def display_width(text: str) -> int:
    """Terminal columns a string occupies.

    East Asian characters occupy two columns, so ``str.ljust`` misaligns
    Chinese labels in a monospaced terminal.  Anything ambiguous counts as one
    column, which is what Windows Terminal and modern Linux terminals do.
    """
    width = 0
    for char in text:
        code = ord(char)
        if (
            0x1100 <= code <= 0x115F          # Hangul Jamo
            or 0x2E80 <= code <= 0xA4CF       # CJK radicals .. Yi
            or 0xAC00 <= code <= 0xD7A3       # Hangul syllables
            or 0xF900 <= code <= 0xFAFF       # CJK compatibility ideographs
            or 0xFE30 <= code <= 0xFE6F       # CJK compatibility forms
            or 0xFF00 <= code <= 0xFF60       # fullwidth forms
            or 0xFFE0 <= code <= 0xFFE6       # fullwidth signs
            or 0x1F300 <= code <= 0x1FAFF     # emoji
            or 0x20000 <= code <= 0x3FFFD     # CJK extensions
        ):
            width += 2
        elif 0x0300 <= code <= 0x036F or code == 0x200D:  # combining marks, ZWJ
            continue
        else:
            width += 1
    return width


def pad(text: str, width: int) -> str:
    """Left-align ``text`` in ``width`` terminal columns."""
    return text + " " * max(0, width - display_width(text))


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


def write_csv(result: ScanResult, path: str | Path, *, language: str | None = None) -> Path:
    """Write every local file with its verdict to a CSV file.

    Column headers stay English so spreadsheets and scripts are unaffected by
    ``--lang``; only the free-text ``reason`` column is localised.
    """
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
                    item.reason(language),
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
    language: str | None = None,
) -> str:
    """Build the human-readable report, localised to ``language``."""
    tr = get_translator(language)
    c = _Palette(_should_colour(colour))
    manifest = result.manifest
    out = io.StringIO()
    line = "-" * 72

    def emit(text: str = "") -> None:
        out.write(text + "\n")

    def field(label: str, value: str, width: int) -> None:
        emit(f"  {pad(label, width)} {value}")

    emit(c("steamverify", "bold") + c(f"  v{__version__}", "dim"))
    emit(c(line, "dim"))

    header_rows: list[tuple[str, str]] = [
        (tr("report.label.game"), c(manifest.game, "bold")),
    ]
    if manifest.app_id != "?":
        header_rows.append((tr("report.label.app_id"), manifest.app_id))
    if manifest.build_id:
        header_rows.append((tr("report.label.build"), manifest.build_id))
    header_rows.append(
        (
            tr("report.label.manifest"),
            tr(
                "report.manifest_summary",
                files=format_count(manifest.file_count),
                chunks=format_count(manifest.chunk_count),
                size=format_bytes(manifest.total_bytes),
            ),
        )
    )
    if manifest.sha256:
        header_rows.append(
            (tr("report.label.manifest_id"), f"sha256:{manifest.sha256[:16]}")
        )
    header_rows.append((tr("report.label.directory"), str(root_label or result.root)))
    header_rows.append(
        (
            tr("report.label.scanned"),
            f"{result.started_utc}  ({result.elapsed_seconds:.1f}s)",
        )
    )
    width = max(display_width(label) for label, _ in header_rows)
    for label, value in header_rows:
        field(label, value, width)
    if result.size_only:
        emit(c(f"  {tr('report.size_only')}", "yellow"))
    emit(c(line, "dim"))
    emit()

    counts = result.counts()
    emit(c(tr("report.summary"), "bold"))
    rows = [
        (VERIFIED, counts[VERIFIED], tr("report.row.verified"), "green"),
        (MODIFIED, counts[MODIFIED], tr("report.row.modified"), "red"),
        (STUB, counts[STUB], tr("report.row.stub"), "cyan"),
        (EXTRA, counts[EXTRA], tr("report.row.extra"), "yellow"),
        (MISSING, counts[MISSING], tr("report.row.missing"), "red"),
    ]
    name_width = max(display_width(tr(f"report.status.{key}")) for key, *_ in rows)
    for key, value, description, style in rows:
        name = tr(f"report.status.{key}")
        marker = c("#", style) if value else c(".", "dim")
        text = c(f"{value:>7,}", style if value else "dim")
        emit(f"  {marker} {pad(name, name_width)}  {text}   {c(description, 'dim')}")
    for key, value, description, style in (
        ("ignored", len(result.ignored), tr("report.row.ignored"), "dim"),
        ("errors", len(result.errors), tr("report.row.errors"), "red"),
    ):
        if not value:
            continue
        name = tr(f"report.status.{key}")
        emit(f"  {c('-', style)} {pad(name, name_width)}  "
             f"{c(f'{value:>7,}', style)}   {c(description, 'dim')}")
    emit()
    emit("  " + tr(
        "report.verified_total",
        files=format_count(len(result.verified)),
        size=format_bytes(result.verified_bytes),
    ))
    if result.extra:
        emit("  " + tr(
            "report.extra_total",
            files=format_count(len(result.extra)),
            size=format_bytes(result.extra_bytes),
        ))

    # -- verdict ---------------------------------------------------------
    emit()
    if result.is_clean:
        emit(c("  " + tr("report.result.clean"), "green", "bold"))
    elif result.modified or result.missing:
        emit(c("  " + tr("report.result.not_clean"), "red", "bold"))
    elif result.stubs:
        emit(c("  " + tr("report.result.stub_only"), "cyan", "bold"))
    else:
        emit(c("  " + tr("report.result.extra_only"), "yellow", "bold"))

    # -- extra groups ----------------------------------------------------
    groups = result.extra_groups()
    if groups:
        emit()
        emit(c(tr("report.section.extra_groups"), "bold"))
        for group, count, size in groups[:max_list]:
            files = tr("report.files_column", count=count)
            emit(f"  {files}  {format_bytes(size):>12}   {group}")
        if len(groups) > max_list:
            emit(c("  " + tr("report.more_hint", count=len(groups) - max_list), "dim"))

    # -- stubs -----------------------------------------------------------
    if result.stubs:
        emit()
        emit(c(tr("report.section.stubs"), "bold"))
        for item in result.stubs[:max_list]:
            emit(f"  {c('S', 'cyan')} {item.path}")
        if len(result.stubs) > max_list:
            emit(c("  " + tr("report.more", count=len(result.stubs) - max_list), "dim"))
        emit(c("  " + tr("report.stub_note"), "dim"))

    # -- modified --------------------------------------------------------
    if result.modified:
        emit()
        emit(c(tr("report.section.modified"), "bold"))
        for item in result.modified[:max_list]:
            emit(f"  {c('M', 'red')} {item.path}")
            emit(c(f"      {item.reason(language)}", "dim"))
            if item.bad_offsets:
                shown = ", ".join(f"{off:,}" for off in item.bad_offsets[:4])
                emit(c("      " + tr("report.bad_chunks", offsets=shown), "dim"))
        if len(result.modified) > max_list:
            emit(c("  " + tr("report.more_hint", count=len(result.modified) - max_list),
                   "dim"))

    # -- missing ---------------------------------------------------------
    if result.missing:
        emit()
        emit(c(tr("report.section.missing"), "bold"))
        for entry in result.missing[:max_list]:
            emit(f"  {c('-', 'red')} {entry.path}  {format_bytes(entry.size)}")
        if len(result.missing) > max_list:
            emit(c("  " + tr("report.more", count=len(result.missing) - max_list), "dim"))

    # -- extras detail ---------------------------------------------------
    if verbose and result.extra:
        emit()
        emit(c(tr("report.section.extras"), "bold"))
        for item in result.extra:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(item.mtime)) if item.mtime else ""
            emit(f"  {format_bytes(item.size):>12}  {when:<17} {item.path}")

    # -- errors ----------------------------------------------------------
    if result.errors:
        emit()
        emit(c(tr("report.section.errors"), "bold"))
        for message in result.errors[:max_list]:
            emit(f"  {c('!', 'red')} {message}")

    # -- next steps ------------------------------------------------------
    emit()
    emit(c(tr("report.section.next"), "bold"))
    if result.is_clean:
        emit("  " + tr("report.next.clean"))
    else:
        step = 1
        if result.modified or result.missing:
            emit(f"  {step}. " + tr("report.next.steam_verify"))
            emit("     " + tr("report.next.steam_verify2"))
            emit("     " + tr("report.next.still_differ"))
            step += 1
        if result.extra:
            emit(f"  {step}. " + tr("report.next.extra_intro"))
            emit("     " + tr("report.next.extra_intro2"))
            emit("     " + tr("report.next.extra_outro"))
    emit()
    emit(c("  " + tr("report.exit_code", code=_exit_code(result)), "dim"))
    return out.getvalue()


def _exit_code(result: ScanResult) -> int:
    if result.errors:
        return 3
    if result.modified or result.stubs or result.missing:
        return 2
    if result.extra:
        return 1
    return 0
