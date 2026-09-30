"""Tests for the .svm manifest format."""

from __future__ import annotations

import json
import struct

import pytest
from conftest import HEADER, chunked_entry, plain_entry, sha1

from steamverify import manifest as manifest_mod
from steamverify.manifest import Chunk, FileEntry, Manifest, ManifestError


def test_round_trip_preserves_everything(manifest_file):
    data = bytes(range(256)) * 2
    entries = [
        plain_entry("a.txt", b"alpha"),
        chunked_entry("b.bin", data, chunk_size=100),
        FileEntry(path="empty.txt", size=0, sha1=sha1(b"")),
    ]
    loaded = manifest_file(entries)

    assert loaded.file_count == 3
    assert loaded.chunk_count == len(entries[1].chunks)
    assert loaded.total_bytes == sum(e.size for e in entries)

    by_path = {e.path: e for e in loaded.files}
    assert by_path["a.txt"].sha1 == sha1(b"alpha")
    assert not by_path["a.txt"].is_chunked
    assert by_path["b.bin"].size == len(data)
    assert by_path["b.bin"].chunk_count == len(entries[1].chunks)
    assert [c.offset for c in by_path["b.bin"].chunks] == [c.offset for c in entries[1].chunks]
    assert by_path["b.bin"].chunks[0].sha1 == entries[1].chunks[0].sha1


def test_lookup_is_case_and_separator_insensitive(manifest_file):
    loaded = manifest_file([plain_entry("Content/Sub/File.TXT", b"x")])
    assert loaded.get("content/sub/file.txt") is not None
    assert loaded.get(r"Content\Sub\File.TXT") is not None
    assert "CONTENT/SUB/FILE.TXT" in loaded
    assert loaded.get("nope.txt") is None


def test_header_metadata_is_exposed(manifest_file):
    loaded = manifest_file([plain_entry("a", b"a")])
    assert loaded.slug == "fixture"
    assert loaded.game == "Fixture Game"
    assert loaded.build_id == "42"
    assert loaded.depots == ["100"]
    assert "Fixture Game" in loaded.describe()


def test_hash_table_is_interned_for_repeated_chunk_digests(tmp_path):
    """A file made of identical chunks must not store the digest repeatedly."""
    block = b"x" * 64
    data = block * 40
    entry = chunked_entry("repeat.bin", data, chunk_size=64)
    assert len({c.sha1 for c in entry.chunks}) == 1

    path = tmp_path / "repeat.svm"
    header = manifest_mod.write(path, dict(HEADER), [entry])
    # 40 chunks, one unique digest + one whole-file digest = 2 table entries
    assert header["hash_count"] == 2
    assert header["chunk_count"] == 40

    loaded = manifest_mod.load(path)
    assert len({c.sha1 for c in loaded.files[0].chunks}) == 1


def test_missing_file_raises(tmp_path):
    with pytest.raises(ManifestError, match="not found"):
        manifest_mod.load(tmp_path / "nope.svm")


def test_bad_magic_is_rejected(tmp_path):
    path = tmp_path / "bad.svm"
    path.write_bytes(b"NOPE" + b"\x00" * 64)
    with pytest.raises(ManifestError, match="not an .svm manifest"):
        manifest_mod.load(path)


def test_unsupported_version_is_rejected(manifest_file, tmp_path):
    loaded = manifest_file([plain_entry("a", b"a")])
    raw = bytearray(loaded.path.read_bytes())
    struct.pack_into("<I", raw, len(manifest_mod.MAGIC), 99)
    path = tmp_path / "future.svm"
    path.write_bytes(bytes(raw))
    with pytest.raises(ManifestError, match="unsupported manifest format version 99"):
        manifest_mod.load(path)


def test_truncated_file_is_rejected(manifest_file, tmp_path):
    loaded = manifest_file([plain_entry("a", b"alpha"), plain_entry("b", b"beta")])
    raw = loaded.path.read_bytes()
    path = tmp_path / "truncated.svm"
    path.write_bytes(raw[:len(raw) // 2])
    with pytest.raises(ManifestError, match="truncated manifest"):
        manifest_mod.load(path)


def test_digest_pinning_detects_edits(manifest_file):
    loaded = manifest_file([plain_entry("a", b"a")])
    good = loaded.sha256
    assert manifest_mod.load(loaded.path, verify_digest=good).sha256 == good
    with pytest.raises(ManifestError, match="SHA-256"):
        manifest_mod.load(loaded.path, verify_digest="0" * 64)


def test_header_file_count_mismatch_is_rejected(manifest_file, tmp_path):
    """A tampered header must not silently change the reported file count."""
    loaded = manifest_file([plain_entry("a", b"a")])
    raw = bytearray(loaded.path.read_bytes())
    # header length sits right after magic + version
    header_len_offset = len(manifest_mod.MAGIC) + 4
    header_len = struct.unpack_from("<I", raw, header_len_offset)[0]
    header = json.loads(raw[header_len_offset + 4:header_len_offset + 4 + header_len])
    header["file_count"] = 999
    encoded = json.dumps(header, separators=(",", ":")).encode()
    rebuilt = bytearray()
    rebuilt += manifest_mod.MAGIC
    rebuilt += struct.pack("<I", 1)
    rebuilt += struct.pack("<I", len(encoded))
    rebuilt += encoded
    rebuilt += raw[header_len_offset + 4 + header_len:]
    path = tmp_path / "tampered.svm"
    path.write_bytes(bytes(rebuilt))
    with pytest.raises(ManifestError, match="header claims 999 files"):
        manifest_mod.load(path)


def test_validate_coverage_accepts_good_manifest(manifest_file):
    loaded = manifest_file(
        [plain_entry("a", b"a"), chunked_entry("b", b"y" * 200, chunk_size=64)]
    )
    assert manifest_mod.validate_coverage(loaded) == []


def test_validate_coverage_flags_size_mismatch():
    entry = FileEntry(
        path="bad.bin",
        size=999,
        sha1=sha1(b""),
        chunks=(Chunk(offset=0, length=10, sha1=sha1(b"0123456789")),),
    )
    problems = manifest_mod.validate_coverage(Manifest(header=dict(HEADER), files=[entry]))
    assert len(problems) == 1
    assert "chunks total 10 bytes but file size is 999" in problems[0]


def test_validate_coverage_flags_duplicate_offsets():
    entry = FileEntry(
        path="dup.bin",
        size=20,
        sha1=sha1(b""),
        chunks=(
            Chunk(offset=0, length=10, sha1=sha1(b"a" * 10)),
            Chunk(offset=0, length=10, sha1=sha1(b"b" * 10)),
        ),
    )
    problems = manifest_mod.validate_coverage(Manifest(header=dict(HEADER), files=[entry]))
    assert any("duplicate chunk offset" in p for p in problems), problems


def test_validate_coverage_flags_zero_length_chunk():
    entry = FileEntry(
        path="zero.bin",
        size=0,
        sha1=sha1(b""),
        chunks=(Chunk(offset=0, length=0, sha1=sha1(b"")),),
    )
    problems = manifest_mod.validate_coverage(Manifest(header=dict(HEADER), files=[entry]))
    assert any("non-positive chunk length" in p for p in problems)


def test_validate_coverage_flags_missing_whole_file_digest():
    entry = FileEntry(path="nodigest.bin", size=5, sha1="")
    problems = manifest_mod.validate_coverage(Manifest(header=dict(HEADER), files=[entry]))
    assert any("missing whole-file digest" in p for p in problems)


def test_write_json_dump(manifest_file, tmp_path):
    loaded = manifest_file([plain_entry("a", b"a"), chunked_entry("b", b"y" * 100, 64)])
    out = tmp_path / "dump.json"
    manifest_mod.write_json(out, loaded)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert len(payload["files"]) == 2
    assert payload["files"][1]["chunks"]
