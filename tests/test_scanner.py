"""Tests for the scanner: classification of official, modified, extra, missing."""

from __future__ import annotations

from conftest import sha1

from steamverify import scanner
from steamverify.manifest import FileEntry


def scan_tree(root, manifest, **kwargs):
    options = scanner.ScanOptions(**kwargs)
    return scanner.scan(root, manifest, options)


def test_clean_tree_is_clean(game_tree):
    root, manifest = game_tree
    result = scan_tree(root, manifest)
    assert result.is_clean
    assert result.game_data_intact
    assert len(result.verified) == 3
    assert result.modified == []
    assert result.extra == []
    assert result.missing == []
    assert result.verified_bytes == sum(e.size for e in manifest.files)


def test_extra_file_is_detected(game_tree):
    root, manifest = game_tree
    (root / "trainer.exe").write_bytes(b"not official")
    result = scan_tree(root, manifest)
    assert not result.is_clean
    assert result.game_data_intact  # official data is untouched
    assert [r.path for r in result.extra] == ["trainer.exe"]
    assert result.extra[0].size == len(b"not official")
    assert result.counts() == {"verified": 3, "modified": 0, "stub": 0,
                               "extra": 1, "missing": 0}


def test_extra_grouping(game_tree):
    root, manifest = game_tree
    for name in ("tools/a.exe", "tools/b.exe", "tools/deep/c.exe", "mods/m.bin"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 10)
    result = scan_tree(root, manifest)
    groups = {g: (c, b) for g, c, b in result.extra_groups()}
    assert groups["tools"][0] == 3
    assert groups["mods"][0] == 1


def test_modified_plain_file(game_tree):
    root, manifest = game_tree
    (root / "plain.txt").write_bytes(b"tampered content\n")
    result = scan_tree(root, manifest)
    assert len(result.modified) == 1
    item = result.modified[0]
    assert item.path == "plain.txt"
    assert item.category == scanner.MODIFIED
    assert item.expected_sha1 != item.sha1
    assert not result.game_data_intact


def test_modified_size_reports_both_sizes(game_tree):
    root, manifest = game_tree
    (root / "plain.txt").write_bytes(b"short")
    result = scan_tree(root, manifest)
    item = result.modified[0]
    assert "size differs" in item.reason
    assert str(item.expected_size) in item.reason
    assert item.size == len(b"short")


def test_modified_chunked_file_localises_damage(game_tree):
    root, manifest = game_tree
    original = (root / "archive.pak").read_bytes()
    damaged = bytearray(original)
    damaged[70:80] = b"\xff" * 10  # inside the second 64-byte chunk
    (root / "archive.pak").write_bytes(bytes(damaged))
    result = scan_tree(root, manifest)
    item = result.modified[0]
    assert item.path == "archive.pak"
    assert item.chunks_total == len(manifest.get("archive.pak").chunks)
    assert item.chunks_bad == 1
    assert item.bad_offsets == (64,)
    assert "1 of" in item.reason


def test_missing_file_is_detected(game_tree):
    root, manifest = game_tree
    (root / "sub" / "nested.bin").unlink()
    result = scan_tree(root, manifest)
    assert [e.path for e in result.missing] == ["sub/nested.bin"]
    assert not result.game_data_intact


def test_zero_byte_placeholder_is_a_stub_not_modified(tmp_path):
    """DLC tombstones: official entry has a hash, the local file is empty."""
    root = tmp_path / "game"
    root.mkdir()
    (root / "marker.tombstone").write_bytes(b"")
    manifest = _manifest([FileEntry(path="marker.tombstone", size=0,
                                    sha1=sha1(b"identifier"))])
    result = scan_tree(root, manifest)
    assert len(result.stubs) == 1
    assert result.modified == []
    assert result.missing == []
    # A stub is not evidence that game data was altered...
    assert result.game_data_intact
    # ...but it does mean the tree is not a byte-perfect match.
    assert not result.is_clean
    assert "placeholder" in result.stubs[0].reason


def test_size_only_mode_skips_hashing(game_tree):
    root, manifest = game_tree
    (root / "plain.txt").write_bytes(b"tampered content\n")  # same length? no
    result = scan_tree(root, manifest, verify_hashes=False)
    # content differs but size matches only for same-length edits
    assert all(r.size_only for r in result.verified)


def test_size_only_still_catches_size_changes(game_tree):
    root, manifest = game_tree
    (root / "plain.txt").write_bytes(b"x")
    result = scan_tree(root, manifest, verify_hashes=False)
    assert len(result.modified) == 1


def test_ignore_patterns(game_tree):
    root, manifest = game_tree
    (root / "mods").mkdir()
    (root / "mods" / "mod1.bin").write_bytes(b"m")
    (root / "notes.txt").write_bytes(b"n")
    result = scan_tree(root, manifest, ignore=["mods/*", "notes.txt"],
                       use_default_ignores=False)
    assert result.extra == []
    assert sorted(result.ignored) == ["mods/mod1.bin", "notes.txt"]


def test_default_ignores_exclude_logs_and_tool_output(game_tree):
    root, manifest = game_tree
    (root / "crash.log").write_bytes(b"log")
    (root / ".steamverify").mkdir()
    (root / ".steamverify" / "report.json").write_bytes(b"{}")
    result = scan_tree(root, manifest, use_default_ignores=True)
    assert result.extra == []
    assert len(result.ignored) == 2


def test_default_ignores_can_be_disabled(game_tree):
    root, manifest = game_tree
    (root / "crash.log").write_bytes(b"log")
    result = scan_tree(root, manifest, use_default_ignores=False)
    assert [r.path for r in result.extra] == ["crash.log"]


def test_walk_files_reports_relative_posix_paths(game_tree):
    root, _ = game_tree
    found = {rel for rel, _full, _st in scanner.walk_files(root)}
    assert found == {"plain.txt", "archive.pak", "sub/nested.bin"}


def test_scan_missing_directory_raises(tmp_path, game_tree):
    _, manifest = game_tree
    try:
        scan_tree(tmp_path / "nope", manifest)
    except FileNotFoundError as exc:
        assert "game directory not found" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected FileNotFoundError")


def test_results_are_sorted(game_tree):
    root, manifest = game_tree
    for name in ("zzz.txt", "aaa.txt", "mmm.txt"):
        (root / name).write_bytes(b"e")
    result = scan_tree(root, manifest)
    paths = [r.path for r in result.extra]
    assert paths == sorted(paths)


def test_extra_groups_sorted_by_size(game_tree):
    root, manifest = game_tree
    (root / "small").mkdir()
    (root / "small" / "a").write_bytes(b"a")
    (root / "big").mkdir()
    (root / "big" / "a").write_bytes(b"a" * 5000)
    result = scan_tree(root, manifest)
    assert [g for g, _c, _b in result.extra_groups()] == ["big", "small"]


def test_hash_file_matches_known_digest(tmp_path):
    path = tmp_path / "x.bin"
    path.write_bytes(b"abc")
    assert scanner.hash_file(path) == sha1(b"abc")


def _manifest(entries):
    from steamverify.manifest import Manifest

    return Manifest(header={"slug": "t", "platform": "windows", "source": "test"},
                    files=list(entries))
