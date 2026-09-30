"""Tests for Steam's binary depot-manifest parser.

The fixtures synthesise the protobuf wire format byte by byte, which pins the
field layout independently of the parser implementation.
"""

from __future__ import annotations

import struct

import pytest

from steamverify import depot as depot_mod


# ---------------------------------------------------------------------------
# protobuf encoding helpers
# ---------------------------------------------------------------------------
def varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def tag(field: int, wire: int) -> bytes:
    return varint((field << 3) | wire)


def ld(field: int, payload: bytes) -> bytes:
    """Length-delimited field."""
    return tag(field, 2) + varint(len(payload)) + payload


def vint(field: int, value: int) -> bytes:
    return tag(field, 0) + varint(value)


def fixed32(field: int, value: int) -> bytes:
    return tag(field, 5) + struct.pack("<I", value)


def chunk(sha1: bytes, crc: int, offset: int, length: int, compressed: int) -> bytes:
    return (
        ld(1, sha1)
        + fixed32(2, crc)
        + vint(3, offset)
        + vint(4, length)
        + vint(5, compressed)
    )


def mapping(name: str, flags: int = 0, size: int = 0, sha1: bytes = b"",
            chunks: list[bytes] | None = None, extra_hash: bytes | None = None) -> bytes:
    """A serialised ``FileMapping``, already wrapped in its field-1 envelope."""
    fields = ld(1, name.encode("utf-8")) + vint(2, flags) + vint(3, size)
    if sha1:
        fields += ld(4, sha1)
    if extra_hash:
        # field 5 carries a second hash on real manifests; it must be ignored
        fields += ld(5, extra_hash)
    for item in chunks or []:
        fields += ld(6, item)
    return ld(1, fields)


def header() -> bytes:
    """The 8-byte prefix real manifests carry: magic plus a version word."""
    return struct.pack("<II", depot_mod.MAGIC, 0)


def manifest_bytes(*mappings: bytes, flat: bool = True) -> bytes:
    """Assemble a manifest body.

    ``flat=True`` reproduces the shape real manifests use: a repeated field 1
    at the top level, each carrying one ``FileMapping``.  ``flat=False`` nests
    them inside a single payload field, which older revisions use.
    """
    return header() + b"".join(mappings)


def wrapped_manifest(*mappings: bytes) -> bytes:
    """A manifest whose mappings sit one level deeper, inside a payload field."""
    payload = b"".join(ld(1, m) for m in mappings)
    return header() + ld(1, payload)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------
def test_parse_flat_manifest():
    raw = manifest_bytes(
        mapping("readme.txt", size=11, sha1=b"\x01" * 20),
        mapping("data.bin", size=0, sha1=b"\x02" * 20,
                extra_hash=b"\xbb" * 20,
                chunks=[chunk(b"\x03" * 20, 1234, 0, 100, 60),
                        chunk(b"\x04" * 20, 5678, 1, 50, 40)]),
    )
    parsed = depot_mod.parse(raw, depot_id="292031", manifest_gid="123")

    assert parsed.depot_id == "292031"
    assert parsed.manifest_gid == "123"
    readme, data = parsed.entries

    assert readme.path == "readme.txt"
    assert readme.size == 11
    assert readme.sha1 == "01" * 20
    assert readme.chunk_count == 0
    assert not readme.is_directory
    assert not readme.is_symlink

    assert data.path == "data.bin"
    # field 5 must NOT be treated as a symlink target
    assert not data.is_symlink
    assert data.chunk_count == 2
    # size is rebuilt from the chunks
    assert data.size == 150
    assert data.covered_bytes == 150
    assert data.chunks[0].offset == 0
    assert data.chunks[0].length == 100
    assert data.chunks[0].compressed_length == 60
    assert data.chunks[0].crc == 1234
    assert data.chunks[1].offset == 1
    assert data.is_sequential


def test_parse_wrapped_manifest():
    raw = wrapped_manifest(mapping("a/b.txt", size=3, sha1=b"\x05" * 20))
    parsed = depot_mod.parse(raw)
    assert [e.path for e in parsed.entries] == ["a/b.txt"]


def test_backslashes_are_normalised():
    raw = manifest_bytes(mapping("content\\content0\\x.w3strings", size=1,
                                 sha1=b"\x06" * 20))
    parsed = depot_mod.parse(raw)
    assert parsed.entries[0].path == "content/content0/x.w3strings"


def test_directory_placeholders_are_detected():
    raw = manifest_bytes(
        mapping("content", size=64, sha1=b"\x07" * 20),
        mapping("big.bin", size=1024, sha1=b"\x08" * 20),
        mapping("empty.txt", size=0, sha1=b"\x09" * 20),
    )
    parsed = depot_mod.parse(raw)
    by_path = {e.path: e for e in parsed.entries}

    # chunkless entry with the 64-byte placeholder marker => directory
    assert by_path["content"].is_directory
    # real files are kept regardless of size, including zero-byte ones
    assert not by_path["big.bin"].is_directory
    assert not by_path["empty.txt"].is_directory
    assert by_path["empty.txt"].chunk_count == 0
    assert [e.path for e in parsed.files] == ["big.bin", "empty.txt"]


def test_depot_id_and_gid_come_from_the_filename(tmp_path):
    path = tmp_path / "292031_2922676153265187497.manifest"
    path.write_bytes(manifest_bytes(mapping("a", size=1, sha1=b"\x0a" * 20)))
    loaded = depot_mod.load(path)
    assert loaded.depot_id == "292031"
    assert loaded.manifest_gid == "2922676153265187497"
    assert "depot 292031" in loaded.describe()


def test_encrypted_manifest_is_reported():
    raw = struct.pack("<II", 0x71F617D1, 0) + b"\x00" * 32
    with pytest.raises(depot_mod.DepotManifestError, match="encrypted"):
        depot_mod.parse(raw)


def test_bad_magic_is_rejected():
    with pytest.raises(depot_mod.DepotManifestError, match="bad manifest magic"):
        depot_mod.parse(struct.pack("<II", 0x00010203, 0) + b"\x00" * 32)


def test_too_small_is_rejected():
    with pytest.raises(depot_mod.DepotManifestError, match="too small"):
        depot_mod.parse(b"\x01\x02")


def test_empty_payload_is_rejected():
    with pytest.raises(depot_mod.DepotManifestError, match="no file mappings"):
        depot_mod.parse(header() + b"\x08\x01")


def test_truncated_chunk_stream_does_not_crash():
    """A cut-off manifest must yield what it can, not raise."""
    raw = manifest_bytes(
        mapping("good", size=1, sha1=b"\x0b" * 20),
        mapping("cut", size=2, sha1=b"\x0c" * 20),
    )
    parsed = depot_mod.parse(raw[:len(raw) - 3])
    assert [e.path for e in parsed.entries] == ["good"]


def test_total_bytes_and_files_properties():
    raw = manifest_bytes(
        mapping("dir", size=64, sha1=b"\x0d" * 20),
        mapping("a", size=100, sha1=b"\x0e" * 20),
        mapping("b", size=200, sha1=b"\x0f" * 20),
    )
    parsed = depot_mod.parse(raw)
    assert len(parsed.files) == 2
    assert parsed.total_bytes == 64 + 100 + 200


def test_find_manifest_file_exact_and_fallback(tmp_path):
    cache = tmp_path / "depotcache"
    cache.mkdir()
    exact = cache / "292031_111.manifest"
    exact.write_bytes(b"x")
    other = cache / "292031_222.manifest"
    other.write_bytes(b"y")

    assert depot_mod.find_manifest_file(cache, "292031", "111") == exact
    # gid not cached -> fall back to another manifest for the same depot
    assert depot_mod.find_manifest_file(cache, "292031", "999") in (exact, other)
    assert depot_mod.find_manifest_file(cache, "555", "1") is None
    assert depot_mod.find_manifest_file(tmp_path / "missing", "1", "1") is None


def test_non_sequential_offsets_are_reported():
    raw = manifest_bytes(
        mapping("weird.bin", size=0, sha1=b"\x10" * 20,
                chunks=[chunk(b"\x11" * 20, 0, 0, 10, 5),
                        chunk(b"\x12" * 20, 0, 7, 10, 5)]),
    )
    parsed = depot_mod.parse(raw)
    assert not parsed.entries[0].is_sequential
    assert parsed.entries[0].covered_bytes == 20
