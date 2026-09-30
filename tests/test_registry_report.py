"""Tests for the game registry and the reporter/output layer."""

from __future__ import annotations

import json

import pytest
from conftest import HEADER, plain_entry, sha1

from steamverify import manifest as manifest_mod
from steamverify import registry as registry_mod
from steamverify import reporter
from steamverify.manifest import FileEntry


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------
def test_registry_lists_games(manifests_dir):
    reg = registry_mod.Registry.load(manifests_dir / "registry.json")
    assert len(reg) == 2
    assert [g.slug for g in reg] == ["gameone", "gametwo"]


def test_registry_lookup_by_slug_alias_appid_and_name(manifests_dir):
    reg = registry_mod.Registry.load(manifests_dir / "registry.json")
    assert reg.get("gameone").app_id == "100"
    assert reg.get("ONE").slug == "gameone"          # alias, case-insensitive
    assert reg.get("100").slug == "gameone"          # app id
    assert reg.get("Game Two").slug == "gametwo"     # display name
    assert reg.find("nope") is None
    with pytest.raises(registry_mod.RegistryError, match="unknown game"):
        reg.get("nope")


def test_registry_selects_current_build(manifests_dir):
    reg = registry_mod.Registry.load(manifests_dir / "registry.json")
    game = reg.get("gameone")
    assert game.record().build_id == "200"
    assert game.record("100").build_id == "100"
    assert game.record("current").build_id == "200"
    loaded = game.load()
    assert loaded.file_count == 2


def test_registry_unknown_build_lists_alternatives(manifests_dir):
    reg = registry_mod.Registry.load(manifests_dir / "registry.json")
    with pytest.raises(registry_mod.RegistryError, match="no manifest for build"):
        reg.get("gameone").record("999")


def test_registry_detects_tampered_manifest(manifests_dir):
    reg = registry_mod.Registry.load(manifests_dir / "registry.json")
    path = manifests_dir / "gameone" / "gameone-200.svm"
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 0xFF
    path.write_bytes(bytes(raw))
    problems = reg.verify_all()
    assert any("SHA-256 mismatch" in p for p in problems)
    with pytest.raises(manifest_mod.ManifestError, match="SHA-256"):
        reg.get("gameone").load()


def test_registry_verify_all_is_clean_for_good_tree(manifests_dir):
    reg = registry_mod.Registry.load(manifests_dir / "registry.json")
    assert reg.verify_all() == []


def test_registry_missing_manifest_is_reported(manifests_dir):
    (manifests_dir / "gametwo" / "gametwo-1.svm").unlink()
    reg = registry_mod.Registry.load(manifests_dir / "registry.json")
    problems = reg.verify_all()
    assert any("missing manifest file" in p for p in problems)


def test_registry_default_directory(manifests_dir):
    reg = registry_mod.Registry.load(manifests_dir / "registry.json")
    assert reg.get("gameone").default_directory("windows") == "GameOneFolder"
    assert reg.get("gametwo").default_directory("windows") == ""


def test_registry_missing_file_raises(tmp_path):
    with pytest.raises(registry_mod.RegistryError, match="registry not found"):
        registry_mod.Registry.load(tmp_path / "nope.json")


def test_registry_invalid_json_raises(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(registry_mod.RegistryError, match="not valid JSON"):
        registry_mod.Registry.load(path)


def test_registry_empty_games_raises(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text('{"version": 1, "games": {}}', encoding="utf-8")
    with pytest.raises(registry_mod.RegistryError, match="lists no games"):
        registry_mod.Registry.load(path)


# ---------------------------------------------------------------------------
# reporter
# ---------------------------------------------------------------------------
def _scan(tmp_path, specs, mutate=None):
    """Build a tree, a manifest, and scan the two against each other.

    ``specs`` is a list of ``(path, content_bytes)`` pairs so the on-disk tree
    is byte-identical to the manifest and the "clean" case is genuinely clean.
    """
    from steamverify import scanner

    root = tmp_path / "tree"
    root.mkdir(exist_ok=True)
    entries = []
    for path, content in specs:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        entries.append(plain_entry(path, content))
    if mutate:
        mutate(root)
    manifest = manifest_mod.Manifest(header=dict(HEADER), files=entries)
    return scanner.scan(root, manifest)


def test_text_report_lists_extra_paths(tmp_path):
    """Every extra must be named by a path a user can act on."""

    def mutate(root):
        for name in ("tools/a.exe", "tools/deep/b.exe", "mods/mod.ini"):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"third party")
        (root / "trainer.exe").write_bytes(b"t")

    result = _scan(tmp_path, [("a.txt", b"aaa")], mutate)
    text = reporter.render_text(result, colour="never")
    section = text.split("Extra files by location", 1)[1].split("\n\n", 1)[0]
    # a folder holding nothing official is named once, with a trailing slash
    assert "tools/" in section
    assert "tools/a.exe" not in section
    # a file outside such a folder is named individually
    assert "trainer.exe" in section
    # the explanation is shown when nothing was left out
    assert "trailing /" in text


def test_text_report_keeps_long_paths_whole(tmp_path):
    """Truncating a path would make it unactionable, so it is never cut."""
    long_folder = "/".join(f"part{index}" for index in range(12))
    deep = f"sub/{long_folder}/stray.exe"

    def mutate(root):
        path = root / deep
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    # an official file shares the deepest folder, so the folder cannot be
    # collapsed and the extra has to be named by its full path
    result = _scan(tmp_path, [(f"sub/{long_folder}/nested.bin", b"n")], mutate)
    assert [row[0] for row in result.extra_groups()] == [deep]
    assert deep in reporter.render_text(result, colour="never")


def test_text_report_shows_every_extra_path_with_verbose(tmp_path):
    def mutate(root):
        for index in range(40):
            path = root / f"tools/file{index:02d}.exe"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")

    result = _scan(tmp_path, [("a.txt", b"aaa")], mutate)
    quiet = reporter.render_text(result, colour="never", max_list=5)
    verbose = reporter.render_text(result, colour="never", max_list=5, verbose=True)
    # the folder is one row, so nothing was left out and no hint is printed
    assert "more" not in quiet
    assert "tools/" in quiet
    # -v names every file inside it, up to the display limit
    assert "tools/file00.exe" in verbose
    assert "trailing /" not in verbose


def test_max_list_shows_fewer_rows_and_says_how_many(tmp_path):
    def mutate(root):
        for name in ("mods/mod.ini", "media/movie.bik"):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"third party")
        (root / "trainer.exe").write_bytes(b"t")

    result = _scan(tmp_path, [("a.txt", b"aaa")], mutate)
    assert len(result.extra_groups()) == 3
    section = reporter.render_text(result, colour="never", max_list=1).split(
        "Extra files by location", 1
    )[1]
    assert "2 more" in section
    # the hint points at the machine-readable output for the full list
    assert "--json" in section


def test_text_report_clean(tmp_path):
    result = _scan(tmp_path, [("a.txt", b"aaa")])
    text = reporter.render_text(result, colour="never")
    assert "RESULT: clean" in text
    assert "Exit code: 0" in text


def test_text_report_flags_extra_and_modified(tmp_path):
    def mutate(root):
        (root / "a.txt").write_bytes(b"zzz")
        (root / "tool.exe").write_bytes(b"t")

    result = _scan(tmp_path, [("a.txt", b"aaa"), ("b.txt", b"bb")], mutate)
    text = reporter.render_text(result, colour="never")
    assert "modified" in text
    assert "RESULT: NOT clean" in text
    assert "Exit code: 2" in text
    assert "tool.exe" in text


def test_text_report_stub_only(tmp_path):
    from steamverify import scanner

    root = tmp_path / "tree"
    root.mkdir()
    (root / "marker.tombstone").write_bytes(b"")
    manifest = manifest_mod.Manifest(
        header=dict(HEADER),
        files=[FileEntry(path="marker.tombstone", size=0, sha1=sha1(b"id"))],
    )
    result = scanner.scan(root, manifest)
    text = reporter.render_text(result, colour="never")
    assert "content-free DLC marker" in text
    assert "Placeholder entries" in text


def test_colour_can_be_disabled_and_forced(tmp_path):
    result = _scan(tmp_path, [("a.txt", b"a")])
    assert "\x1b[" not in reporter.render_text(result, colour="never")
    assert "\x1b[" in reporter.render_text(result, colour="always")


def test_json_report_is_valid_and_complete(tmp_path):
    def mutate(root):
        (root / "a.txt").write_bytes(b"bbb")
        (root / "extra.bin").write_bytes(b"12345")

    result = _scan(tmp_path, [("a.txt", b"aaa")], mutate)
    payload = json.loads(reporter.render_json(result))
    assert payload["summary"]["modified_files"] == 1
    assert payload["summary"]["extra_files"] == 1
    assert payload["summary"]["clean"] is False
    assert payload["modified"][0]["path"] == "a.txt"
    assert payload["extra"][0]["path"] == "extra.bin"
    assert payload["extra_groups"] == [
        {"group": "extra.bin", "files": 1, "bytes": 5, "folder": False}
    ]


def test_csv_output_has_all_rows(tmp_path):
    def mutate(root):
        (root / "a.txt").write_bytes(b"bbb")
        (root / "extra.bin").write_bytes(b"1")

    result = _scan(tmp_path, [("a.txt", b"aaa")], mutate)
    path = reporter.write_csv(result, tmp_path / "out" / "report.csv")
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].startswith("status,path,size")
    body = "\n".join(lines[1:])
    assert "a.txt" in body
    assert "extra.bin" in body


def test_csv_includes_missing_rows(tmp_path):
    def mutate(root):
        (root / "a.txt").unlink()

    result = _scan(tmp_path, [("a.txt", b"aaa")], mutate)
    path = reporter.write_csv(result, tmp_path / "report.csv")
    # utf-8-sig: strip the BOM before comparing
    body = path.read_text(encoding="utf-8-sig")
    assert "missing,a.txt" in body


def test_format_bytes_units():
    assert reporter.format_bytes(0) == "0 B"
    assert reporter.format_bytes(512) == "512 B"
    assert reporter.format_bytes(2048) == "2.00 KiB"
    assert reporter.format_bytes(5 * 1024 ** 2) == "5.00 MiB"
    assert reporter.format_bytes(3 * 1024 ** 3) == "3.00 GiB"
    assert reporter.format_bytes(-2048) == "-2.00 KiB"


def test_summary_dict_shape(tmp_path):
    result = _scan(tmp_path, [("a.txt", b"a")])
    summary = reporter.summary_dict(result)
    for key in ("game", "app_id", "build_id", "manifest_sha256", "verified_files",
                "modified_files", "stub_files", "extra_files", "missing_files",
                "clean", "game_data_intact"):
        assert key in summary
    assert summary["clean"] is True
