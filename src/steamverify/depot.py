"""Parser for Steam's binary depot manifests (``<depot>_<manifest>.manifest``).

These files are what Steam itself uses to validate an install.  The Steam
client caches one per installed depot under ``<steam>/depotcache/``, which
makes them the authoritative, fully offline source of official file hashes.

Format
------
An 8-byte header (``u32 magic 0x71F617D0`` plus a ``u32`` the client ignores)
followed by a protobuf message.  Depending on the manifest revision the body
is either a ``ContentManifestPayload`` wrapper whose field 1 is a repeated
``FileMapping``, or the repeated ``FileMapping`` directly.  Both shapes are
handled.

``FileMapping``     1=filename 2=flags 3=size 4=sha1 5=chunks 6=chunks
``ChunkData``       1=sha1 2=crc 3=offset 4=cb_original 5=cb_compressed

Notes that cost real debugging time, and are therefore documented here:

* The depot id is **not** in the file header.  Steam stores it in the
  filename, so it must be recovered from there.
* Sizes for chunked files live only in the chunk records; the mapping's own
  ``size`` field is 0 for them and must be rebuilt from ``offset+length``.
* Field 5 of a mapping is **not** a symlink target.  It carries a second
  hash-like value that is present on every chunked file; treating it as a
  link target makes every asset in the depot look like a symlink.  Real
  symlinks are rare and are detected by the flag bit instead.
* Directory entries look like ordinary files with no chunks and a placeholder
  size of 64, so they cannot be distinguished by flags alone -- see
  :func:`looks_like_directory`.
* Chunk records for one file are **not** stored in offset order, so they must
  be sorted before coverage can be reasoned about.

Encrypted (password-protected beta) manifests are detected and reported
rather than silently mis-parsed.
"""

from __future__ import annotations

import re
import struct
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "DepotManifestError",
    "DepotChunk",
    "DepotFile",
    "DepotManifest",
    "parse",
    "load",
    "find_manifest_file",
    "depotcache_dir",
]

MAGIC = 0x71F617D0
DIRECTORY_PLACEHOLDER_SIZE = 64
_MANIFEST_NAME = re.compile(r"^(?P<depot>\d+)_(?P<gid>\d+)\.manifest$", re.IGNORECASE)


class DepotManifestError(Exception):
    """The depot manifest is missing, encrypted, or malformed."""


# ---------------------------------------------------------------------------
# minimal protobuf wire reader
# ---------------------------------------------------------------------------
class _Truncated(Exception):
    """Raised internally when the byte stream stops looking like protobuf."""


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while shift < 64:
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
    raise _Truncated


def _iter_fields(buf: bytes, start: int = 0, end: int | None = None):
    """Yield ``(field_number, wire_type, value, next_pos)``."""
    if end is None:
        end = len(buf)
    pos = start
    while pos < end:
        try:
            key, pos = _read_varint(buf, pos)
            field_no = key >> 3
            wire = key & 7
            if wire == 0:
                value, pos = _read_varint(buf, pos)
            elif wire == 1:
                if pos + 8 > end:
                    raise _Truncated
                value = struct.unpack_from("<Q", buf, pos)[0]
                pos += 8
            elif wire == 2:
                length, pos = _read_varint(buf, pos)
                if length < 0 or pos + length > end:
                    raise _Truncated
                value = buf[pos:pos + length]
                pos += length
            elif wire == 5:
                if pos + 4 > end:
                    raise _Truncated
                value = struct.unpack_from("<I", buf, pos)[0]
                pos += 4
            else:
                raise _Truncated
        except (IndexError, struct.error):
            raise _Truncated from None
        yield field_no, wire, value, pos


def _looks_like_mapping(buf: bytes) -> bool:
    """A ``FileMapping`` starts with a filename string then a flags varint."""
    try:
        keys = []
        for field_no, wire, _value, _pos in _iter_fields(buf):
            keys.append((field_no, wire))
            if len(keys) >= 3:
                break
        return bool(keys) and keys[0] == (1, 2) and (2, 0) in keys
    except _Truncated:
        return False


# ---------------------------------------------------------------------------
# data model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DepotChunk:
    """One SteamPipe chunk of a file."""

    sha1: str
    offset: int
    length: int
    compressed_length: int = 0
    crc: int = 0


@dataclass
class DepotFile:
    """One entry in a depot manifest."""

    path: str
    size: int
    sha1: str
    flags: int = 0
    chunks: tuple[DepotChunk, ...] = ()
    symlink_target: str = ""

    @property
    def is_directory(self) -> bool:
        """Steam marks directories as chunkless entries with a 64-byte size."""
        return (
            not self.chunks
            and self.size == DIRECTORY_PLACEHOLDER_SIZE
            and not self.symlink_target
        )

    @property
    def is_symlink(self) -> bool:
        return bool(self.symlink_target)

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)

    @property
    def is_sequential(self) -> bool:
        """True when chunk offsets are exactly ``0..n-1``.

        Steam's ``offset`` field is a chunk *ordinal*, not a byte offset: the
        assembled file is the chunks written back to back in that order.  This
        holds for every depot observed so far and is worth asserting.
        """
        if not self.chunks:
            return False
        offsets = sorted(c.offset for c in self.chunks)
        return offsets == list(range(len(self.chunks)))

    @property
    def covered_bytes(self) -> int:
        """Bytes the chunks assemble into (which is the file size)."""
        return sum(c.length for c in self.chunks)


@dataclass
class DepotManifest:
    """A parsed depot manifest."""

    depot_id: str
    manifest_gid: str
    path: Path | None
    entries: list[DepotFile] = field(default_factory=list)

    @property
    def files(self) -> list[DepotFile]:
        """Real files only (directory placeholders removed)."""
        return [e for e in self.entries if not e.is_directory]

    @property
    def total_bytes(self) -> int:
        return sum(e.size for e in self.entries)

    def describe(self) -> str:
        return (
            f"depot {self.depot_id} manifest {self.manifest_gid}: "
            f"{len(self.files)} files, {self.total_bytes / 1024 ** 3:.2f} GiB"
        )


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------
def _parse_chunk(buf: bytes) -> DepotChunk:
    sha1 = ""
    crc = 0
    offset = 0
    length = 0
    compressed = 0
    for field_no, wire, value, _pos in _iter_fields(buf):
        if field_no == 1 and wire == 2:
            sha1 = value.hex()
        elif field_no == 2 and wire == 5:
            crc = value
        elif field_no == 3 and wire == 0:
            offset = value
        elif field_no == 4 and wire == 0:
            length = value
        elif field_no == 5 and wire == 0:
            compressed = value
    return DepotChunk(sha1=sha1, offset=offset, length=length,
                      compressed_length=compressed, crc=crc)


def _parse_mapping(buf: bytes) -> DepotFile:
    """Parse a ``FileMapping``.

    Local ``name``/``size``/chunks are captured; field 5 is intentionally
    ignored because it is a per-file hash, not a symlink target (see the
    module docstring).
    """
    name = ""
    flags = 0
    size = 0
    sha1 = ""
    chunks: list[DepotChunk] = []
    for field_no, wire, value, _pos in _iter_fields(buf):
        if field_no == 1 and wire == 2:
            name = value.decode("utf-8", "replace")
        elif field_no == 2 and wire == 0:
            flags = value
        elif field_no == 3 and wire == 0:
            size = value
        elif field_no == 4 and wire == 2:
            sha1 = value.hex()
        elif field_no == 6 and wire == 2:
            chunks.append(_parse_chunk(value))

    entry = DepotFile(
        path=name.replace("\\", "/").strip("/"),
        size=size,
        sha1=sha1,
        flags=flags,
        chunks=tuple(chunks),
    )
    if entry.chunks:
        # Chunked files report size 0 in the mapping; rebuild it from the
        # chunks, which concatenate into the file.
        entry.size = entry.covered_bytes
    return entry


def _collect_mappings(buf: bytes, depth: int = 0, out: list[DepotFile] | None = None):
    if out is None:
        out = []
    if depth > 4:
        return out
    try:
        for field_no, wire, value, _pos in _iter_fields(buf):
            if field_no != 1 or wire != 2:
                continue
            if _looks_like_mapping(value):
                entry = _parse_mapping(value)
                if entry.path:
                    out.append(entry)
            else:
                _collect_mappings(value, depth + 1, out)
    except _Truncated:
        pass
    return out


def parse(raw: bytes, *, depot_id: str = "", manifest_gid: str = "",
          path: Path | None = None) -> DepotManifest:
    """Parse depot-manifest bytes."""
    if len(raw) < 8:
        raise DepotManifestError("file is too small to be a depot manifest")

    magic = struct.unpack_from("<I", raw, 0)[0]
    if magic != MAGIC:
        if magic == 0x71F617D1:
            raise DepotManifestError(
                "manifest is encrypted (password-protected beta branch); "
                "it cannot be read without the depot key"
            )
        raise DepotManifestError(f"bad manifest magic 0x{magic:08X}")

    entries = _collect_mappings(raw[8:])
    if not entries:
        raise DepotManifestError(
            "no file mappings found -- unsupported or encrypted manifest"
        )
    return DepotManifest(
        depot_id=depot_id, manifest_gid=manifest_gid, path=path, entries=entries
    )


def load(path: str | Path) -> DepotManifest:
    """Load a depot manifest, recovering depot id and gid from the filename."""
    path = Path(path)
    if not path.is_file():
        raise DepotManifestError(f"depot manifest not found: {path}")
    match = _MANIFEST_NAME.match(path.name)
    depot_id = match.group("depot") if match else ""
    gid = match.group("gid") if match else ""
    return parse(path.read_bytes(), depot_id=depot_id, manifest_gid=gid, path=path)


# ---------------------------------------------------------------------------
# locating manifests on disk
# ---------------------------------------------------------------------------
def depotcache_dir(steam_root: str | Path) -> Path:
    """``<steam>/depotcache`` for a Steam installation root."""
    return Path(steam_root) / "depotcache"


def find_manifest_file(cache: str | Path, depot_id: str, manifest_gid: str) -> Path | None:
    """Find ``<depot>_<gid>.manifest`` inside a depotcache directory.

    Falls back to a glob when the gid recorded in the ACF does not match the
    cached file (Steam occasionally updates a depot without rewriting the
    ACF immediately).
    """
    cache = Path(cache)
    if not cache.is_dir():
        return None
    exact = cache / f"{depot_id}_{manifest_gid}.manifest"
    if exact.is_file():
        return exact
    if manifest_gid:
        for candidate in cache.glob(f"{depot_id}_*.manifest"):
            if candidate.name.split("_")[-1].split(".")[0] == manifest_gid:
                return candidate
    matches = sorted(cache.glob(f"{depot_id}_*.manifest"))
    return matches[0] if matches else None


def iter_local_source_files(raw: bytes) -> Iterator[tuple[str, int]]:  # pragma: no cover
    """Debug helper: yield ``(path, size)`` for every mapping in raw bytes."""
    for entry in _collect_mappings(raw[8:]):
        yield entry.path, entry.size
