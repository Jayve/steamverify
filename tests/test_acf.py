"""Tests for the ACF (Valve KeyValues) parser and Steam library discovery."""

from __future__ import annotations

import pytest

from steamverify import acf


def test_parse_scalars_and_blocks(keyvalues_acf):
    manifest = acf.AppManifest.load(keyvalues_acf)
    assert manifest.app_id == "292030"
    assert manifest.name == "The Witcher 3: Wild Hunt"
    assert manifest.install_dir == "The Witcher 3"
    assert manifest.build_id == "25575366"
    assert manifest.size_on_disk == 74290293403
    assert manifest.last_updated == 1790778455
    assert manifest.language == "schinese"
    assert manifest.is_fully_installed


def test_installed_depots_and_manifests(keyvalues_acf):
    manifest = acf.AppManifest.load(keyvalues_acf)
    assert manifest.installed_depots == ["292031", "292042", "378649"]
    assert manifest.depot_manifest_gid("292031") == "2922676153265187497"
    assert manifest.depot_size("292031") == 58759089902
    assert manifest.depot_dlc_app_id("378649") == "378649"
    assert manifest.depot_manifest_gid("999") == ""
    assert manifest.depot("999") is None


def test_parse_escapes_and_comments():
    tree = acf.parse(
        '// leading comment\n"root"\n{\n'
        '  "quoted"  "a \\"b\\" c"\n'
        '  "newline" "a\\nb"\n'
        '  "backslash" "a\\\\b"\n'
        "}\n"
    )
    assert tree["root"]["quoted"] == 'a "b" c'
    assert tree["root"]["newline"] == "a\nb"
    assert tree["root"]["backslash"] == "a\\b"


def test_repeated_keys_become_a_list():
    tree = acf.parse('"r"\n{\n  "k" "1"\n  "k" "2"\n}\n')
    assert tree["r"]["k"] == ["1", "2"]


def test_bare_tokens_are_accepted():
    tree = acf.parse("root\n{\n  flag 1\n}\n")
    assert tree["root"]["flag"] == "1"


@pytest.mark.parametrize(
    "text, message",
    [
        ('"a" "b"\n"c"\n', "has no value"),
        ('"a"\n{\n', "unbalanced opening brace"),
        ('"a" "b"\n}\n', "unbalanced closing brace"),
        ('"a" "unterminated\n', "unterminated quoted string"),
        ('"a" "b" "c" { }\n}\n', "unbalanced"),
    ],
)
def test_malformed_input_is_rejected(text, message):
    with pytest.raises(acf.KeyValuesError, match=message):
        acf.parse(text)


def test_missing_appstate_block_is_rejected():
    with pytest.raises(acf.KeyValuesError, match="no top-level 'AppState'"):
        acf.AppManifest({"Something": {}})


def test_missing_file_raises(tmp_path):
    with pytest.raises(acf.KeyValuesError, match="file not found"):
        acf.parse_file(tmp_path / "nope.acf")


def test_describe_mentions_name_and_build(keyvalues_acf):
    text = acf.AppManifest.load(keyvalues_acf).describe()
    assert "The Witcher 3" in text
    assert "25575366" in text
    assert "schinese" in text


def test_state_flags_not_installed(tmp_path):
    path = tmp_path / "appmanifest_1.acf"
    path.write_text('"AppState"\n{\n "appid" "1"\n "StateFlags" "2"\n}\n', encoding="utf-8")
    manifest = acf.AppManifest.load(path)
    assert not manifest.is_fully_installed


# ---------------------------------------------------------------------------
# steam library discovery
# ---------------------------------------------------------------------------
def test_library_folders_parsing(tmp_path):
    from steamverify.steam import SteamLibrary

    root = tmp_path / "Steam"
    (root / "steamapps").mkdir(parents=True)
    other = tmp_path / "OtherLib"
    (other / "steamapps").mkdir(parents=True)
    root_path = str(root).replace("\\", "\\\\")
    other_path = str(other).replace("\\", "\\\\")
    (root / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n'
        f'\t"0"\n\t{{\n\t\t"path"\t\t"{root_path}"\n\t}}\n'
        f'\t"1"\n\t{{\n\t\t"path"\t\t"{other_path}"\n\t}}\n'
        "}\n",
        encoding="utf-8",
    )
    library = SteamLibrary(root=root, steamapps=root / "steamapps")
    folders = library.library_folders()
    assert root in folders
    assert other in folders


def test_app_dir_uses_install_dir(tmp_path):
    from steamverify.steam import SteamLibrary

    root = tmp_path / "Steam"
    (root / "steamapps" / "common" / "The Witcher 3").mkdir(parents=True)
    library = SteamLibrary(root=root, steamapps=root / "steamapps")
    assert library.app_dir("292030", "The Witcher 3") == \
        root / "steamapps" / "common" / "The Witcher 3"


def test_library_apps_reads_acf_files(tmp_path, keyvalues_acf):
    from steamverify.steam import SteamLibrary

    root = tmp_path / "Steam"
    apps_dir = root / "steamapps"
    apps_dir.mkdir(parents=True)
    (apps_dir / "appmanifest_292030.acf").write_bytes(keyvalues_acf.read_bytes())
    (apps_dir / "appmanifest_broken.acf").write_text("not valid {", encoding="utf-8")
    library = SteamLibrary(root=root, steamapps=apps_dir)

    apps = library.apps()
    assert [a.app_id for a in apps] == ["292030"]
    app = apps[0]
    assert app.name == "The Witcher 3: Wild Hunt"
    assert app.build_id == "25575366"
    assert app.is_installed
    assert library.app("292030") is app
    assert library.app("1") is None


def test_steam_roots_prefers_env(tmp_path, monkeypatch):
    from steamverify import steam

    fake = tmp_path / "FakeSteam"
    fake.mkdir()
    monkeypatch.setenv("STEAM_ROOT", str(fake))
    roots = steam.steam_roots()
    assert roots and roots[0] == fake.resolve()
