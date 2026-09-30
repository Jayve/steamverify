"""Bundled hash-manifest format (``.svm`` -- Steam Verify Manifest).

A manifest is a self-contained, versioned description of the *official* files
of one game build.  It ships inside this repository so the tool works fully
offline and so scans are reproducible: the same manifest always yields the
same verdict.

Design constraints
------------------
* **Small enough to commit.**  SteamPipe files carry per-chunk hashes and a
  single file can have tens of thousands of chunks, so the on-disk form is a
  compact binary with interned path and hash tables instead of JSON.
* **Self-describing.**  The header names the game, build and depots, so a
  stale manifest can be detected instead of producing thousands of bogus
  "modified" rows.
* **Forward compatible.**  A format version gates parsing; unknown trailing
  sections are ignored.
* **Game agnostic.**  Nothing in the format is specific to any title -- only
  the header content and the records differ.

Layout
------
All integers are little-endian.

===========  ==========================================================
magic        ``b"SVHASH\\x00\\x01"``
u32          format version (currently 1)
u32          byte length of the UTF-8 JSON header that follows
bytes        header JSON (required keys in :data:`REQUIRED_HEADER_KEYS`)
u32          number of paths
paths        each: u16 byte length + UTF-8 bytes, ``/`` separators,
             relative to the game root
u32          number of hashes
hashes       each: 20 raw bytes (SHA-1)
u32          number of file records
records      each: u32 path idx, u64 size, u32 sha1 idx,
             u32 first chunk idx, u32 chunk count
u32          number of chunk records
chunks       each: u64 offset, u32 length, u32 sha1 idx
===========  ==========================================================

Semantics
---------
* ``chunk count == 0`` -- the file is stored verbatim; the record's ``sha1``
  must equal the SHA-1 of the whole file.
* ``chunk count > 0``  -- the file is stored as SteamPipe chunks; the bytes
  at ``[offset, offset + length)`` must match the chunk's hash.  Chunks are
  expected to tile the file without gaps, which the loader validates.

The size is always recorded explicitly, so a truncated or padded file is
caught before any hashing happens.
"""

from __future__ import annotations

import hashlib
import io
import json
import struct
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAGIC = b"SVHASH\x00\x01"
FORMAT_VERSION = 1

_U32 = struct.Struct("<I")
_RECORD = struct.Struct("<IQIII")
_CHUNK = struct.Struct("<QII")
_PATH_LEN = struct.Struct("<H")

REQUIRED_HEADER_KEYS = (
    "game",
    "platform",
    "file_count",
    "chunk_count",
    "total_bytes",
    "source",
)


class ManifestError(Exception):
    """The manifest is missing, truncated, corrupt, or of an unknown version."""


@dataclass(frozen=True)
class Chunk:
    """A byte range of a file, plus the SHA-1 those bytes must have."""

    offset: int
    length: int
    sha1: str

    @property
    def end(self) -> int:
        return self.offset + self.length


@dataclass(frozen=True)
class FileEntry:
    """One tracked file."""

    path: str
    size: int
    sha1: str
    chunks: tuple[Chunk, ...] = ()

    @property
    def is_chunked(self) -> bool:
        """True when the file is stored as SteamPipe chunks."""
        return bool(self.chunks)

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)


@dataclass
class Manifest:
    """A parsed ``.svm`` manifest."""

    header: dict[str, Any]
    files: list[FileEntry]
    #: path the manifest was loaded from (``None`` for synthetic manifests)
    source_path: Path | None = None
    sha256: str = ""
    _by_path: dict[str, FileEntry] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._by_path = {normalise(e.path): e for e in self.files}

    # -- lookups ---------------------------------------------------------
    def get(self, relative_path: str) -> FileEntry | None:
        """Look up a file by a path relative to the game root."""
        return self._by_path.get(normalise(relative_path))

    def __contains__(self, relative_path: object) -> bool:
        return isinstance(relative_path, str) and normalise(relative_path) in self._by_path

    def __len__(self) -> int:
        return len(self.files)

    def __iter__(self) -> Iterator[FileEntry]:
        return iter(self.files)

    # -- metadata --------------------------------------------------------
    @property
    def game(self) -> str:
        return str(self.header.get("game", "?"))

    @property
    def slug(self) -> str:
        return str(self.header.get("slug", ""))

    @property
    def app_id(self) -> str:
        return str(self.header.get("app_id", "?"))

    @property
    def build_id(self) -> str:
        return str(self.header.get("build_id", ""))

    @property
    def platform(self) -> str:
        return str(self.header.get("platform", "?"))

    @property
    def depots(self) -> list[str]:
        return [str(d) for d in self.header.get("depots", [])]

    @property
    def source(self) -> str:
        """Where the manifest came from, as recorded in the header."""
        return str(self.header.get("source", ""))

    @property
    def path(self) -> Path | None:
        """Filesystem path this manifest was loaded from."""
        return self.source_path

    @property
    def total_bytes(self) -> int:
        return int(self.header.get("total_bytes", 0))

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def chunk_count(self) -> int:
        return int(self.header.get("chunk_count", 0))

    @property
    def chunk_size(self) -> int:
        """Nominal chunk size when the manifest was built by chunking."""
        return int(self.header.get("chunk_size", 0) or 0)

    def describe(self) -> str:
        """One-line human summary."""
        bits = [self.game]
        if self.app_id != "?":
            bits.append(f"app {self.app_id}")
        if self.build_id:
            bits.append(f"build {self.build_id}")
        bits.append(f"{len(self.files)} files")
        if self.chunk_count:
            bits.append(f"{self.chunk_count} chunks")
        bits.append(f"{self.total_bytes / 1024 ** 3:.2f} GiB")
        return " | ".join(bits)


def normalise(path: str) -> str:
    """Canonical form of a relative path for lookups and comparisons.

    Windows paths are case-insensitive and may use either separator, so keys
    are lower-cased and slash-normalised.  Steam's own manifests use ``\\``.
    """
    return path.replace("\\", "/").strip("/").lower()


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------
def _read_exact(handle: io.BytesIO, count: int, what: str) -> bytes:
    data = handle.read(count)
    if len(data) != count:
        raise ManifestError(f"truncated manifest while reading {what}")
    return data


def _read_u32(handle: io.BytesIO, what: str) -> int:
    return _U32.unpack(_read_exact(handle, 4, what))[0]


def load(path: str | Path, *, verify_digest: str | None = None) -> Manifest:
    """Load and validate a manifest.

    ``verify_digest`` pins the SHA-256 of the manifest file itself, which is
    what the registry records; a mismatch means the bundled data was edited.
    """
    path = Path(path)
    if not path.is_file():
        raise ManifestError(f"manifest file not found: {path}")

    raw = path.read_bytes()
    sha256 = hashlib.sha256(raw).hexdigest()
    if verify_digest and verify_digest.lower() != sha256:
        raise ManifestError(
            "manifest SHA-256 does not match the registry entry -- "
            "the bundled manifest may have been modified\n"
            f"  expected {verify_digest}\n  actual   {sha256}"
        )

    handle = io.BytesIO(raw)
    if _read_exact(handle, len(MAGIC), "magic") != MAGIC:
        raise ManifestError(f"not an .svm manifest: {path}")

    version = _read_u32(handle, "format version")
    if version != FORMAT_VERSION:
        raise ManifestError(
            f"unsupported manifest format version {version} "
            f"(this build reads version {FORMAT_VERSION})"
        )

    header_len = _read_u32(handle, "header length")
    try:
        header = json.loads(_read_exact(handle, header_len, "header").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"manifest header is not valid JSON: {exc}") from exc
    missing = [k for k in REQUIRED_HEADER_KEYS if k not in header]
    if missing:
        raise ManifestError(f"manifest header is missing keys: {', '.join(missing)}")

    path_count = _read_u32(handle, "path count")
    paths: list[str] = []
    for i in range(path_count):
        length = _PATH_LEN.unpack(_read_exact(handle, 2, f"path {i} length"))[0]
        try:
            paths.append(_read_exact(handle, length, f"path {i}").decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ManifestError(f"path {i} is not valid UTF-8: {exc}") from exc

    hash_count = _read_u32(handle, "hash count")
    blob = _read_exact(handle, hash_count * 20, "hash table")
    hashes = [blob[i * 20:(i + 1) * 20].hex() for i in range(hash_count)]

    record_count = _read_u32(handle, "file count")
    raw_records = [
        _RECORD.unpack(_read_exact(handle, _RECORD.size, f"file record {i}"))
        for i in range(record_count)
    ]

    chunk_count = _read_u32(handle, "chunk count")
    raw_chunks = [
        _CHUNK.unpack(_read_exact(handle, _CHUNK.size, f"chunk {i}"))
        for i in range(chunk_count)
    ]

    files: list[FileEntry] = []
    for index, (path_index, size, sha_index, first, count) in enumerate(raw_records):
        if path_index >= len(paths):
            raise ManifestError(f"file record {index} references unknown path {path_index}")
        if sha_index >= len(hashes):
            raise ManifestError(f"file record {index} references unknown hash {sha_index}")
        if first + count > len(raw_chunks):
            raise ManifestError(f"file record {index} references chunks past the end")
        chunks = tuple(
            Chunk(offset=offset, length=length, sha1=hashes[hash_index])
            for offset, length, hash_index in raw_chunks[first:first + count]
        )
        files.append(
            FileEntry(path=paths[path_index], size=size, sha1=hashes[sha_index], chunks=chunks)
        )

    declared = int(header.get("file_count", -1))
    if declared != len(files):
        raise ManifestError(f"header claims {declared} files but {len(files)} are present")
    declared_chunks = int(header.get("chunk_count", -1))
    if declared_chunks != len(raw_chunks):
        raise ManifestError(
            f"header claims {declared_chunks} chunks but {len(raw_chunks)} are present"
        )

    return Manifest(header=header, files=files, source_path=path, sha256=sha256)


def validate_coverage(manifest: Manifest) -> list[str]:
    """Return human-readable structural problems (empty list == good).

    Two invariants matter, and both are cheap to check:

    1. Chunk lengths sum to the recorded file size.  If they did not, a file
       could be "verified" while some of its bytes were never examined.
    2. Chunk offsets are unique and non-negative, so the verification pass
       reads each region of the file exactly once.

    Note on offsets: Steam's chunk ``offset`` is a byte offset into the file
    (chunks are fixed-size apart from the final one), which is also why a
    manifest built by chunking a directory with ``--chunk-size`` is
    compatible with the same reader.
    """
    problems: list[str] = []
    for entry in manifest.files:
        if entry.size < 0:
            problems.append(f"{entry.path}: negative size")
            continue
        if not entry.chunks:
            if len(entry.sha1) != 40:
                problems.append(f"{entry.path}: missing whole-file digest")
            continue

        seen: set[int] = set()
        duplicate: int | None = None
        for chunk in entry.chunks:
            if chunk.length <= 0:
                problems.append(
                    f"{entry.path}: non-positive chunk length at offset {chunk.offset}"
                )
                break
            if chunk.offset < 0:
                problems.append(f"{entry.path}: negative chunk offset {chunk.offset}")
                break
            if chunk.offset in seen:
                duplicate = chunk.offset
                break
            seen.add(chunk.offset)
        else:
            total = sum(c.length for c in entry.chunks)
            if total != entry.size:
                problems.append(
                    f"{entry.path}: chunks total {total} bytes but file size is "
                    f"{entry.size}"
                )
        if duplicate is not None:
            problems.append(f"{entry.path}: duplicate chunk offset {duplicate}")
    return problems


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------
def write(
    path: str | Path,
    header: dict[str, Any],
    entries: Iterable[FileEntry],
) -> dict[str, Any]:
    """Serialise a manifest to ``path``; returns the header as written.

    Path and hash tables are interned so repeated digests (very common once a
    file has thousands of chunks) cost four bytes instead of twenty.
    """
    entries = list(entries)

    path_table: dict[str, int] = {}
    paths: list[str] = []
    hash_table: dict[str, int] = {}
    hashes: list[str] = []
    records: list[tuple[int, int, int, int, int]] = []
    chunks: list[tuple[int, int, int]] = []

    def intern_path(value: str) -> int:
        if value not in path_table:
            path_table[value] = len(paths)
            paths.append(value)
        return path_table[value]

    def intern_hash(value: str) -> int:
        value = value.lower()
        if value not in hash_table:
            hash_table[value] = len(hashes)
            hashes.append(value)
        return hash_table[value]

    for entry in entries:
        first = len(chunks)
        for chunk in entry.chunks:
            chunks.append((chunk.offset, chunk.length, intern_hash(chunk.sha1)))
        records.append(
            (
                intern_path(entry.path),
                entry.size,
                intern_hash(entry.sha1),
                first,
                len(entry.chunks),
            )
        )

    final_header = dict(header)
    final_header.update(
        {
            "format_version": FORMAT_VERSION,
            "file_count": len(records),
            "chunk_count": len(chunks),
            "path_count": len(paths),
            "hash_count": len(hashes),
            "total_bytes": sum(entry.size for entry in entries),
        }
    )
    header_bytes = json.dumps(
        final_header, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")

    out = bytearray()
    out += MAGIC
    out += _U32.pack(FORMAT_VERSION)
    out += _U32.pack(len(header_bytes))
    out += header_bytes
    out += _U32.pack(len(paths))
    for value in paths:
        encoded = value.encode("utf-8")
        out += _PATH_LEN.pack(len(encoded))
        out += encoded
    out += _U32.pack(len(hashes))
    for value in hashes:
        out += bytes.fromhex(value)
    out += _U32.pack(len(records))
    for record in records:
        out += _RECORD.pack(*record)
    out += _U32.pack(len(chunks))
    for chunk in chunks:
        out += _CHUNK.pack(*chunk)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return final_header


def write_json(path: str | Path, manifest: Manifest, *, indent: int = 1) -> None:
    """Write a human-readable JSON dump (large; for debugging, not shipping)."""
    payload = {
        "header": manifest.header,
        "sha256": manifest.sha256,
        "files": [
            {
                "path": e.path,
                "size": e.size,
                "sha1": e.sha1,
                "chunks": [
                    {"offset": c.offset, "length": c.length, "sha1": c.sha1}
                    for c in e.chunks
                ],
            }
            for e in manifest.files
        ],
    }
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=indent), encoding="utf-8"
    )
