"""The scan engine: compare a game directory against a manifest.

The engine is deliberately game-agnostic -- it only knows about a root
directory and a manifest.  Three verdicts matter to users:

* **verified** -- the file exists, has the recorded size, and its hash matches
* **modified** -- the file is tracked but its bytes or size differ
* **extra**    -- the file is not in the manifest at all (mods, trainers,
  repack leftovers, save games, crash dumps)
* **missing**  -- a tracked file is absent

SteamPipe files are verified chunk by chunk so a partially corrupt multi-GB
archive is localised instead of just reported as "differs".
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import time
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .i18n import get_translator
from .manifest import FileEntry, Manifest, normalise

__all__ = [
    "VERIFIED",
    "MODIFIED",
    "STUB",
    "EXTRA",
    "MISSING",
    "DEFAULT_IGNORES",
    "FileResult",
    "ScanResult",
    "ScanOptions",
    "scan",
    "hash_file",
    "verify_chunked",
    "walk_files",
    "extra_folders",
    "group_extras",
]

#: Paths that are never meaningful to flag as "extra": this tool's own output,
#: Steam's bookkeeping, and things the game writes while running.  Disable
#: with ``ScanOptions(use_default_ignores=False)`` / ``--no-default-ignores``.
DEFAULT_IGNORES = (
    ".steamverify/*",
    ".steamverify",
    "*.log",
    "*.dmp",
    "*.mdmp",
    "*.tmp",
    "*.bak",
    "*.orig",
    "*.rej",
    "*.pyc",
    "__pycache__/*",
    "Thumbs.db",
    "desktop.ini",
    ".DS_Store",
    "steam_appid.txt",
    "steam_settings/*",
    "ColdClientLoader.ini",
)

VERIFIED = "verified"
MODIFIED = "modified"
STUB = "stub"
EXTRA = "extra"
MISSING = "missing"

_READ_BLOCK = 8 << 20  # 8 MiB per read; chunk lookups are indexed in memory

ProgressFn = Callable[[str, int, int, str], None]


@dataclass(frozen=True)
class FileResult:
    """Outcome for a single local file."""

    path: str
    category: str
    size: int = 0
    expected_size: int = 0
    mtime: float = 0.0
    sha1: str = ""
    expected_sha1: str = ""
    #: i18n key describing the problem (see :mod:`steamverify.i18n`).  The scan
    #: engine stays language-agnostic; the reporter renders the prose.
    reason_key: str = ""
    reason_args: dict = field(default_factory=dict)
    chunks_total: int = 0
    chunks_bad: int = 0
    bad_offsets: tuple[int, ...] = ()
    #: hash was not recomputed (``--no-hash`` fast path)
    size_only: bool = False
    #: this record stands for a whole folder whose contents are all extra,
    #: rather than for a single file (see :meth:`ScanResult.extra_groups`)
    is_folder: bool = False

    @property
    def is_ok(self) -> bool:
        return self.category == VERIFIED

    @property
    def size_delta(self) -> int:
        return self.size - self.expected_size

    def reason(self, language: str | None = None) -> str:
        """Human-readable explanation, localised on demand."""
        if not self.reason_key:
            return ""
        return get_translator(language)(self.reason_key, **self.reason_args)

    def as_dict(self) -> dict:
        data = {
            "path": self.path,
            "status": self.category,
            "size": self.size,
        }
        if self.expected_size:
            data["expected_size"] = self.expected_size
            data["size_delta"] = self.size_delta
        if self.mtime:
            data["mtime"] = int(self.mtime)
        if self.sha1:
            data["sha1"] = self.sha1
        if self.expected_sha1 and self.expected_sha1 != self.sha1:
            data["expected_sha1"] = self.expected_sha1
        if self.reason_key:
            # Machine output keeps the stable English key plus English prose,
            # so JSON consumers never depend on --lang.
            data["reason"] = self.reason_key
            data["reason_text"] = self.reason("en")
        if self.chunks_total:
            data["chunks_total"] = self.chunks_total
            data["chunks_bad"] = self.chunks_bad
            if self.bad_offsets:
                data["bad_chunk_offsets"] = list(self.bad_offsets)
        if self.size_only:
            data["size_only"] = True
        return data


def group_extras(
    entries: Iterable[tuple[str, int]], depth: int = 1
) -> dict[str, list[int]]:
    """Bucket ``(path, bytes)`` entries by their leading path components.

    ``entries`` may be individual files or already-collapsed folders; either
    way the bucket key is the first ``depth`` components of the path.  Values
    are ``[files, bytes]`` accumulators, keyed exactly as they appear on disk.
    """
    buckets: dict[str, list[int]] = {}
    for path, size in entries:
        parts = path.replace("\\", "/").split("/")
        key = "/".join(parts[:depth]) if depth > 1 else parts[0]
        slot = buckets.setdefault(key, [0, 0])
        slot[0] += 1
        slot[1] += max(size, 0)
    return buckets


def extra_folders(
    extra: Iterable[FileResult], scanned: Iterable[str]
) -> list[tuple[str, int, int]]:
    """Folders whose entire contents are extra: ``(folder, count, bytes)``.

    A path is what a user needs in order to act on a finding, but a mod
    bundle can hold thousands of files in one directory -- so any folder in
    which *every* scanned file is extra is reported once, by its path, instead
    of being expanded.  A folder is only ever collapsed when the manifest has
    nothing under it at all, so nothing official can hide behind one.

    Only the outermost such folders are returned: ``tools`` is reported rather
    than ``tools`` plus every subdirectory below it.  The result is sorted by
    descending byte count, then by path, so the biggest offender leads.
    """
    # folder -> files found under it, and how many of those are extras
    totals: dict[str, int] = {}
    extras: dict[str, int] = {}
    sizes: dict[str, int] = {}
    children: dict[str, set[str]] = {}
    for path in scanned:
        parts = path.replace("\\", "/").split("/")[:-1]  # parent folders only
        for index in range(1, len(parts) + 1):
            folder = "/".join(parts[:index])
            totals[folder] = totals.get(folder, 0) + 1

    for item in extra:
        path = item.path.replace("\\", "/")
        parts = path.split("/")[:-1]
        for index in range(1, len(parts) + 1):
            folder = "/".join(parts[:index])
            extras[folder] = extras.get(folder, 0) + 1
            sizes[folder] = sizes.get(folder, 0) + max(item.size, 0)
            if index < len(parts):
                children.setdefault(folder, set()).add("/".join(parts[:index + 1]))

    # A candidate's own counts only describe what is directly inside it, so
    # empty subdirectories are checked against the candidates themselves.
    complete = {
        folder
        for folder, total in totals.items()
        if extras.get(folder, 0) == total
    }

    def covered(folder: str) -> bool:
        return all(child in complete for child in children.get(folder, ()))

    complete = {folder for folder in complete if covered(folder)}

    # Keep only the outermost folders; deeper candidates are implied by them.
    reported: list[str] = []
    for folder in sorted(complete, key=lambda name: (name.count("/"), name)):
        if any(folder.startswith(parent + "/") for parent in reported):
            continue
        reported.append(folder)

    return sorted(
        ((folder, extras[folder], sizes.get(folder, 0)) for folder in reported),
        key=lambda row: (-row[2], row[0].lower()),
    )


@dataclass
class ScanResult:
    """Everything a scan produced."""

    manifest: Manifest
    root: Path
    verified: list[FileResult] = field(default_factory=list)
    modified: list[FileResult] = field(default_factory=list)
    stubs: list[FileResult] = field(default_factory=list)
    extra: list[FileResult] = field(default_factory=list)
    missing: list[FileEntry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    started_utc: str = ""
    elapsed_seconds: float = 0.0
    size_only: bool = False
    #: memoised :meth:`extra_groups` result, filled on first use
    _extra_groups: list[tuple[str, int, int, bool]] | None = field(
        default=None, repr=False, compare=False
    )

    # -- aggregates ------------------------------------------------------
    @property
    def local_file_count(self) -> int:
        return len(self.verified) + len(self.modified) + len(self.stubs) + len(self.extra)

    @property
    def verified_bytes(self) -> int:
        return sum(r.size for r in self.verified)

    @property
    def extra_bytes(self) -> int:
        return sum(r.size for r in self.extra)

    @property
    def is_clean(self) -> bool:
        """True when the install matches the manifest exactly."""
        return not (self.modified or self.stubs or self.extra or self.missing or self.errors)

    @property
    def game_data_intact(self) -> bool:
        """True when nothing that should hold game data has changed.

        Content-free placeholder entries (see :data:`STUB`) do not count: they
        hold no data, so an empty stub is not evidence of tampering.
        """
        return not (self.modified or self.missing or self.errors)

    @property
    def unexpected(self) -> list[FileResult]:
        return [r for r in self.extra if not r.reason_key]

    def scanned_paths(self) -> Iterator[str]:
        """Every local path the scan classified, including the extras."""
        for item in self.verified:
            yield item.path
        for item in self.modified:
            yield item.path
        for item in self.stubs:
            yield item.path
        for item in self.extra:
            yield item.path

    def extra_folders(self) -> list[tuple[str, int, int]]:
        """Folders that hold nothing but extra files: ``(folder, count, bytes)``."""
        return extra_folders(self.extra, self.scanned_paths())

    def loose_extra_files(self) -> list[FileResult]:
        """Extras that are not inside a folder from :meth:`extra_folders`.

        These need naming individually: they sit next to official files, so no
        enclosing folder can be pointed at instead.
        """
        folders = [folder for folder, _count, _size in self.extra_folders()]
        loose = [
            item
            for item in self.extra
            if not any(
                item.path.replace("\\", "/").startswith(folder + "/")
                for folder in folders
            )
        ]
        return sorted(loose, key=lambda item: (-max(item.size, 0), item.path.lower()))

    def extra_groups(self, depth: int = 1) -> list[tuple[str, int, int, bool]]:
        """Every extra, keyed by the most specific path that can be acted on.

        Returns ``(path, files, bytes, is_folder)`` rows: one per folder whose
        contents are entirely extra, plus one per extra file that sits among
        official files.  ``depth > 1`` keeps the old bucketing behaviour, which
        groups by the leading components instead (:func:`group_extras`).

        Sorted by descending byte count, then by path.
        """
        if depth > 1:
            buckets = group_extras(
                ((item.path, item.size) for item in self.extra), depth
            )
            return sorted(
                ((key, value[0], value[1], False) for key, value in buckets.items()),
                key=lambda row: (-row[2], row[0].lower()),
            )

        # A scan is read-only once it is returned, so this is computed once:
        # both the text and the JSON report ask for it.
        if self._extra_groups is None:
            rows = [
                (folder, count, size, True)
                for folder, count, size in self.extra_folders()
            ]
            rows += [
                (item.path, 1, max(item.size, 0), False)
                for item in self.loose_extra_files()
            ]
            self._extra_groups = sorted(
                rows, key=lambda row: (-row[2], row[0].lower())
            )
        return self._extra_groups

    def counts(self) -> dict[str, int]:
        return {
            VERIFIED: len(self.verified),
            MODIFIED: len(self.modified),
            STUB: len(self.stubs),
            EXTRA: len(self.extra),
            MISSING: len(self.missing),
        }


@dataclass
class ScanOptions:
    """Knobs for :func:`scan`."""

    workers: int = 0
    verify_hashes: bool = True
    follow_symlinks: bool = False
    ignore: list[str] = field(default_factory=list)
    use_default_ignores: bool = True
    progress: ProgressFn | None = None
    progress_interval: float = 0.25


# ---------------------------------------------------------------------------
# hashing helpers
# ---------------------------------------------------------------------------
def _sha1_hex(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def hash_file(path: Path) -> str:
    """SHA-1 of a whole file, streamed."""
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(_READ_BLOCK), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_chunked(path: Path, entry: FileEntry, *, max_bad: int = 8) -> tuple[int, tuple[int, ...]]:
    """Verify a chunked file; returns ``(bad_chunk_count, sample_ordinals)``.

    Steam stores a chunked file as its chunks concatenated in ordinal order,
    so locally every chunk occupies a fixed-size slot.  We recompute each
    chunk's digest in place, which both validates content and localises damage
    to a specific chunk instead of reporting "this 31 GB file differs".

    ``file_digest`` is validated by the caller against the manifest; this
    function only reports chunk-level mismatches.
    """
    ordered = sorted(entry.chunks, key=lambda c: c.offset)
    bad = 0
    sample: list[int] = []
    covered = 0

    try:
        with open(path, "rb") as handle:
            for chunk in ordered:
                handle.seek(covered)
                remaining = chunk.length
                digest = hashlib.sha1()
                while remaining > 0:
                    block = handle.read(min(_READ_BLOCK, remaining))
                    if not block:
                        break
                    digest.update(block)
                    remaining -= len(block)
                read = chunk.length - remaining
                if read != chunk.length or digest.hexdigest() != chunk.sha1:
                    bad += 1
                    if len(sample) < max_bad:
                        sample.append(chunk.offset)
                covered += chunk.length
    except OSError:
        raise

    if covered != entry.size and not bad:
        bad = 1  # chunks did not account for the whole file
        if not sample:
            sample = [0]
    return bad, tuple(sample)


# ---------------------------------------------------------------------------
# directory walking
# ---------------------------------------------------------------------------
def walk_files(root: Path, *, follow_symlinks: bool = False) -> Iterator[tuple[str, Path, os.stat_result]]:
    """Yield ``(relative_path, absolute_path, stat)`` for every regular file.

    Symlinked directories are never descended into (Steam installs widely use
    junctions), and files that vanish mid-scan are skipped silently.  The
    relative path always uses ``/`` separators.
    """
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
        dirnames.sort()
        filenames.sort()
        current = Path(dirpath)
        for name in filenames:
            full = current / name
            is_link = full.is_symlink()
            try:
                stat = full.lstat() if is_link and not follow_symlinks else full.stat()
            except OSError:
                continue
            is_regular = stat.st_mode & 0o170000 == 0o100000
            if not is_regular and not is_link:
                continue
            try:
                relative = full.relative_to(root).as_posix()
            except ValueError:  # pragma: no cover - defensive
                continue
            yield relative, full, stat


def _matches_ignore(relative: str, patterns: Iterable[str]) -> bool:
    lowered = relative.lower()
    for pattern in patterns:
        pattern = pattern.lower().replace("\\", "/")
        if fnmatch.fnmatch(lowered, pattern) or fnmatch.fnmatch(lowered, pattern.rstrip("/") + "/*"):
            return True
    return False


# ---------------------------------------------------------------------------
# the scan itself
# ---------------------------------------------------------------------------
def scan(
    root: str | Path,
    manifest: Manifest,
    options: ScanOptions | None = None,
) -> ScanResult:
    """Compare the game directory at ``root`` against ``manifest``."""
    options = options or ScanOptions()
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"game directory not found: {root}")

    started = time.time()
    result = ScanResult(
        manifest=manifest,
        root=root,
        started_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        size_only=not options.verify_hashes,
    )

    if root.name.lower() == manifest.slug.lower() and manifest.header.get("directory"):
        pass  # normal case; kept explicit for readability

    ignores = list(options.ignore)
    if options.use_default_ignores:
        ignores.extend(DEFAULT_IGNORES)

    # 1. walk once, classifying by manifest membership
    pending: list[tuple[str, Path, os.stat_result, FileEntry | None]] = []
    for relative, full, stat in walk_files(root, follow_symlinks=options.follow_symlinks):
        if ignores and _matches_ignore(relative, ignores):
            result.ignored.append(relative)
            continue
        pending.append((relative, full, stat, manifest.get(relative)))

    total = len(pending)
    if options.progress:
        options.progress("walk", 0, total, f"{total} local files")

    # 2. hash in parallel
    def classify(item):
        relative, full, stat, entry = item
        return _classify(relative, full, stat, entry, options.verify_hashes)

    workers = options.workers or min(32, (os.cpu_count() or 4) * 2)
    done = 0
    last_report = 0.0
    seen: set[str] = set()

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for outcome in pool.map(classify, pending):
                done += 1
                if outcome is None:
                    result.errors.append("unreadable file encountered")
                    continue
                item, entry = outcome
                if entry is not None:
                    seen.add(normalise(entry.path))
                if item.category == VERIFIED:
                    result.verified.append(item)
                elif item.category == MODIFIED:
                    result.modified.append(item)
                elif item.category == STUB:
                    result.stubs.append(item)
                else:
                    result.extra.append(item)

                if options.progress:
                    now = time.monotonic()
                    if now - last_report >= options.progress_interval or done == total:
                        last_report = now
                        options.progress("hash", done, total, item.path)
    except KeyboardInterrupt:  # pragma: no cover - interactive
        result.errors.append("scan interrupted by user")

    # 3. anything tracked but never seen is missing
    for entry in manifest.files:
        if normalise(entry.path) not in seen:
            result.missing.append(entry)

    # stable ordering for reproducible reports
    result.verified.sort(key=lambda r: r.path.lower())
    result.modified.sort(key=lambda r: r.path.lower())
    result.stubs.sort(key=lambda r: r.path.lower())
    result.extra.sort(key=lambda r: r.path.lower())
    result.missing.sort(key=lambda e: e.path.lower())

    result.elapsed_seconds = time.time() - started
    return result


def _stub_reason_key() -> str:
    """i18n key explaining why a same-size, different-content file is a stub.

    Some Steam depots ship *content-free* marker files -- DLC ownership
    tombstones are the common case.  The depot manifest records a hash for
    them, but no Steam client holds a file with that content locally; they are
    present as zero-byte stubs.  Reporting those as "modified" would be
    technically true and practically misleading, so they get their own
    category and do not move a file into the "changed game data" bucket.
    """
    return "scan.reason.placeholder"


def _size_differs(size: int, expected: int) -> dict:
    return {"size": f"{size:,}", "official": f"{expected:,}"}


def _classify(
    relative: str,
    full: Path,
    stat: os.stat_result,
    entry: FileEntry | None,
    verify_hashes: bool,
):
    """Return ``(FileResult, matched_entry_or_None)``.

    Reasons are recorded as i18n keys so the engine has no language of its own.
    """
    size = stat.st_size
    mtime = stat.st_mtime

    if entry is None:
        return (
            FileResult(path=relative, category=EXTRA, size=size, mtime=mtime),
            None,
        )

    if entry.is_chunked:
        if size != entry.size:
            return (
                FileResult(
                    path=relative,
                    category=MODIFIED,
                    size=size,
                    expected_size=entry.size,
                    mtime=mtime,
                    reason_key="scan.reason.size_differs",
                    reason_args=_size_differs(size, entry.size),
                ),
                entry,
            )
        if not verify_hashes:
            return (
                FileResult(
                    path=relative,
                    category=VERIFIED,
                    size=size,
                    expected_size=entry.size,
                    mtime=mtime,
                    chunks_total=entry.chunk_count,
                    size_only=True,
                ),
                entry,
            )
        try:
            bad, sample = verify_chunked(full, entry)
        except OSError as exc:
            return (
                FileResult(
                    path=relative,
                    category=MODIFIED,
                    size=size,
                    expected_size=entry.size,
                    mtime=mtime,
                    reason_key="scan.reason.unreadable",
                    reason_args={"detail": exc.strerror or str(exc)},
                ),
                entry,
            )
        if bad:
            if bad < entry.chunk_count:
                reason_key = "scan.reason.chunks_differ"
                reason_args = {"bad": bad, "total": entry.chunk_count}
            else:
                reason_key = "scan.reason.file_differs"
                reason_args = {}
            return (
                FileResult(
                    path=relative,
                    category=MODIFIED,
                    size=size,
                    expected_size=entry.size,
                    mtime=mtime,
                    reason_key=reason_key,
                    reason_args=reason_args,
                    chunks_total=entry.chunk_count,
                    chunks_bad=bad,
                    bad_offsets=sample,
                ),
                entry,
            )
        return (
            FileResult(
                path=relative,
                category=VERIFIED,
                size=size,
                expected_size=entry.size,
                mtime=mtime,
                chunks_total=entry.chunk_count,
            ),
            entry,
        )

    # plain file
    if size != entry.size:
        return (
            FileResult(
                path=relative,
                category=MODIFIED,
                size=size,
                expected_size=entry.size,
                mtime=mtime,
                expected_sha1=entry.sha1,
                reason_key="scan.reason.size_differs",
                reason_args=_size_differs(size, entry.size),
            ),
            entry,
        )
    if not verify_hashes:
        return (
            FileResult(
                path=relative,
                category=VERIFIED,
                size=size,
                expected_size=entry.size,
                mtime=mtime,
                size_only=True,
            ),
            entry,
        )
    try:
        digest = hash_file(full)
    except OSError as exc:
        return (
            FileResult(
                path=relative,
                category=MODIFIED,
                size=size,
                expected_size=entry.size,
                mtime=mtime,
                reason_key="scan.reason.unreadable",
                reason_args={"detail": exc.strerror or str(exc)},
            ),
            entry,
        )
    if digest != entry.sha1:
        # A zero-byte file whose official size is also zero is a stub, not a
        # corrupted asset (see _stub_reason_key).
        if size == 0 and entry.size == 0:
            return (
                FileResult(
                    path=relative,
                    category=STUB,
                    size=size,
                    expected_size=entry.size,
                    mtime=mtime,
                    sha1=digest,
                    expected_sha1=entry.sha1,
                    reason_key=_stub_reason_key(),
                ),
                entry,
            )
        return (
            FileResult(
                path=relative,
                category=MODIFIED,
                size=size,
                expected_size=entry.size,
                mtime=mtime,
                sha1=digest,
                expected_sha1=entry.sha1,
                reason_key="scan.reason.content_differs",
            ),
            entry,
        )
    return (
        FileResult(
            path=relative,
            category=VERIFIED,
            size=size,
            expected_size=entry.size,
            mtime=mtime,
            sha1=digest,
        ),
        entry,
    )
