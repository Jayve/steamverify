"""Tests for the bilingual (English / Simplified Chinese) output layer."""

from __future__ import annotations

import json

import pytest
from conftest import HEADER, plain_entry, sha1

from steamverify import i18n, reporter
from steamverify import manifest as manifest_mod
from steamverify.i18n import CATALOG
from steamverify.manifest import FileEntry


# ---------------------------------------------------------------------------
# catalog integrity
# ---------------------------------------------------------------------------
def test_catalogs_have_identical_keys():
    """A missing translation would silently fall back to English mid-report."""
    reference = set(CATALOG[i18n.DEFAULT_LANGUAGE])
    for code, catalog in CATALOG.items():
        missing = sorted(reference - set(catalog))
        extra = sorted(set(catalog) - reference)
        assert not missing, f"{code} is missing {len(missing)} keys: {missing[:5]}"
        assert not extra, f"{code} has {len(extra)} unknown keys: {extra[:5]}"


def test_no_untranslated_keys_in_shipped_languages():
    for code in i18n.LANGUAGES:
        assert i18n.Translator(code).untranslated() == []


def test_translations_are_not_accidentally_english():
    """Catch copy-paste: zh strings that are just the English text."""
    zh = CATALOG["zh"]
    en = CATALOG["en"]
    identical = [
        key for key, value in zh.items()
        # allow pure format/identifier strings to match
        if value == en[key] and any(ch.isalpha() for ch in value)
        and not value.startswith(("{", "(", "sha256"))
        and key not in _INTENTIONALLY_SHARED
    ]
    assert identical == [], f"untranslated by copy-paste: {identical}"


#: Values that are the same in both languages on purpose.
_INTENTIONALLY_SHARED = {
    "lang.name",              # endonym, handled per language
    "cli.field.app_id",       # "app id" is used as-is in Chinese too
    "report.label.app_id",    # ditto for the report header
    "cli.steam_depotcache",   # a directory name
}


def test_placeholders_match_between_languages():
    """A translated string must keep the same format fields as the English."""
    import re

    pattern = re.compile(r"\{(\w+)\}")
    for code, catalog in CATALOG.items():
        for key, value in catalog.items():
            expected = set(pattern.findall(CATALOG["en"][key]))
            found = set(pattern.findall(value))
            assert found == expected, (
                f"{code}:{key} placeholders {sorted(found)} != {sorted(expected)}"
            )


def test_english_is_the_fallback_for_unknown_language():
    tr = i18n.Translator("de")
    assert tr.language == "en"
    assert tr("report.summary") == CATALOG["en"]["report.summary"]


# ---------------------------------------------------------------------------
# language resolution
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "value, expected",
    [
        ("en", "en"),
        ("EN", "en"),
        ("zh", "zh"),
        ("zh-CN", "zh"),
        ("zh_CN", "zh"),
        ("zh-Hans", "zh"),
        ("ZH-HANS-CN", "zh"),
        ("chs", "zh"),
        ("cn", "zh"),
        ("chinese", "zh"),
        ("简体中文", "zh"),
        ("中文", "zh"),
        ("english", "en"),
        ("nonsense", None),
        ("", None),
        (None, None),
    ],
)
def test_normalise_language(value, expected):
    assert i18n.normalise_language(value) == expected


def _clear_locale(monkeypatch):
    """Remove every locale variable that would outrank the one under test.

    ``LC_ALL`` outranks ``LANG`` per POSIX, and CI runners set their own values
    (GitHub's macOS image sets ``LC_ALL``), so a test that only sets ``LANG``
    would pass or fail depending on the machine.
    """
    for name in ("STEAMVERIFY_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)


def test_env_var_selects_language(monkeypatch):
    _clear_locale(monkeypatch)
    monkeypatch.setenv("STEAMVERIFY_LANG", "zh")
    assert i18n.resolve_language(None) == "zh"


def test_explicit_argument_beats_environment(monkeypatch):
    _clear_locale(monkeypatch)
    monkeypatch.setenv("STEAMVERIFY_LANG", "zh")
    assert i18n.resolve_language("en") == "en"


def test_posix_locale_is_honoured(monkeypatch):
    _clear_locale(monkeypatch)
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    assert i18n.resolve_language(None) == "zh"


def test_locale_with_language_and_territory(monkeypatch):
    _clear_locale(monkeypatch)
    monkeypatch.setenv("LC_ALL", "zh_CN.UTF-8")
    assert i18n.resolve_language(None) == "zh"


def test_lc_all_outranks_lang(monkeypatch):
    """POSIX precedence: LC_ALL wins, so an en_US LC_ALL means English."""
    _clear_locale(monkeypatch)
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    assert i18n.resolve_language(None) == "en"


def test_lc_messages_outranks_lang(monkeypatch):
    _clear_locale(monkeypatch)
    monkeypatch.setenv("LC_MESSAGES", "zh_CN.UTF-8")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert i18n.resolve_language(None) == "zh"


def test_c_locale_falls_back_to_english(monkeypatch):
    _clear_locale(monkeypatch)
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("LANG", "POSIX")
    assert i18n.resolve_language(None) == "en"


def test_unset_locale_falls_back_to_english(monkeypatch):
    _clear_locale(monkeypatch)
    assert i18n.resolve_language(None) == "en"


def test_steamverify_lang_wins_over_locale(monkeypatch):
    _clear_locale(monkeypatch)
    monkeypatch.setenv("STEAMVERIFY_LANG", "en")
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    assert i18n.resolve_language(None) == "en"


def test_set_language_updates_the_default():
    try:
        assert i18n.set_language("zh").language == "zh"
        assert i18n.get_translator().language == "zh"
        assert i18n.get_translator().tr("report.summary") == "汇总"
    finally:
        i18n.set_language("en")


def test_unknown_key_raises():
    with pytest.raises(KeyError, match="no message for key"):
        i18n.Translator("en").tr("report.does_not_exist")


def test_missing_format_argument_raises():
    with pytest.raises(KeyError, match="needs a value"):
        i18n.Translator("en").tr("report.more", wrong=1)


# ---------------------------------------------------------------------------
# reporter output
# ---------------------------------------------------------------------------
def _result(tmp_path, *, extra=True, modified=False):
    from steamverify import scanner

    root = tmp_path / "tree"
    root.mkdir()
    content = b"hello"
    (root / "a.txt").write_bytes(content)
    if modified:
        (root / "a.txt").write_bytes(b"HELLO")
    if extra:
        (root / "tool.exe").write_bytes(b"x")
    manifest = manifest_mod.Manifest(
        header={**HEADER, "game": "Fixture Game"},
        files=[plain_entry("a.txt", content)],
    )
    return scanner.scan(root, manifest)


def test_english_report(tmp_path):
    text = reporter.render_text(_result(tmp_path), colour="never", language="en")
    assert "Summary" in text
    assert "Extra files by location" in text
    assert "What to do" in text
    assert "RESULT:" in text
    assert "Exit code:" in text
    assert "汇总" not in text


def test_chinese_report(tmp_path):
    text = reporter.render_text(_result(tmp_path), colour="never", language="zh")
    assert "汇总" in text
    assert "多余文件分布" in text
    assert "处理建议" in text
    assert "结论：" in text
    assert "退出码：" in text
    # the file path itself must survive untranslated
    assert "tool.exe" in text


def test_chinese_report_renders_reasons(tmp_path):
    result = _result(tmp_path, extra=False, modified=True)
    text = reporter.render_text(result, colour="never", language="zh")
    assert "内容不同" in text
    english = reporter.render_text(result, colour="never", language="en")
    assert "content differs" in english


def test_chinese_report_for_clean_tree(tmp_path):
    text = reporter.render_text(
        _result(tmp_path, extra=False), colour="never", language="zh"
    )
    assert "结论：干净" in text
    assert "无需处理" in text


def test_report_respects_process_language(tmp_path):
    try:
        i18n.set_language("zh")
        text = reporter.render_text(_result(tmp_path), colour="never")
        assert "汇总" in text
    finally:
        i18n.set_language("en")


def test_reason_is_localised_independently_of_report(tmp_path):
    result = _result(tmp_path, extra=False, modified=True)
    item = result.modified[0]
    assert "content differs" in item.reason("en")
    assert "内容不同" in item.reason("zh")
    assert "内容不同" in item.reason(i18n.Translator("zh"))


def test_json_stays_machine_readable_in_both_languages(tmp_path):
    """Keys and status values must not change with the selected language."""
    result = _result(tmp_path, modified=True)
    rendered = []
    for language in ("en", "zh"):
        i18n.set_language(language)
        payload = json.loads(reporter.render_json(result))
        assert payload["summary"]["tool"] == "steamverify"
        assert set(payload["modified"][0]) >= {"path", "status", "reason"}
        assert payload["modified"][0]["status"] == "modified"
        # reason stays a stable English key so scripts are language-proof
        assert payload["modified"][0]["reason"] == "scan.reason.content_differs"
        assert "content differs" in payload["modified"][0]["reason_text"]
        rendered.append(reporter.render_json(result))
    # byte-identical regardless of process language
    assert rendered[0] == rendered[1]


def test_csv_headers_stay_english_but_reason_is_localised(tmp_path):
    result = _result(tmp_path, extra=False, modified=True)
    path_zh = reporter.write_csv(result, tmp_path / "zh.csv", language="zh")
    lines = path_zh.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].startswith("status,path,size")
    assert "内容不同" in path_zh.read_text(encoding="utf-8-sig")

    path_en = reporter.write_csv(result, tmp_path / "en.csv", language="en")
    assert "content differs" in path_en.read_text(encoding="utf-8-sig")


def test_stub_reason_is_localised(tmp_path):
    from steamverify import scanner

    root = tmp_path / "tree"
    root.mkdir()
    (root / "m.tombstone").write_bytes(b"")
    manifest = manifest_mod.Manifest(
        header=dict(HEADER),
        files=[FileEntry(path="m.tombstone", size=0, sha1=sha1(b"id"))],
    )
    result = scanner.scan(root, manifest)
    assert "占位文件" in result.stubs[0].reason("zh")
    assert "placeholder file" in result.stubs[0].reason("en")


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------
def test_cli_prescan_finds_lang():
    from steamverify.cli import _prescan_language

    assert _prescan_language(["--lang", "zh", "list"]) == "zh"
    assert _prescan_language(["verify", "--lang=zh"]) == "zh"
    assert _prescan_language(["-L", "zh"]) is None      # short form: argparse only
    assert _prescan_language(["list"]) is None
    assert _prescan_language(["--lang"]) is None        # dangling flag
    assert _prescan_language(None) is None


def test_cli_help_is_localised():
    from steamverify.cli import build_parser

    english = build_parser(i18n.Translator("en")).format_help()
    chinese = build_parser(i18n.Translator("zh")).format_help()
    assert "Verify a Steam game installation" in english
    assert "用内置的官方哈希清单校验" in chinese
    assert "--lang" in english and "--lang" in chinese


def test_cli_list_speaks_chinese(manifests_dir, capsys):
    from steamverify import cli

    code = cli.main(["--lang", "zh", "--registry",
                     str(manifests_dir / "registry.json"), "list"])
    assert code == 0
    out = capsys.readouterr().out
    assert "内置游戏" in out
    assert "别名" in out


def test_cli_info_speaks_chinese(manifests_dir, capsys):
    from steamverify import cli

    code = cli.main(["--lang", "zh", "--registry",
                     str(manifests_dir / "registry.json"),
                     "info", "--game", "gameone"])
    assert code == 0
    out = capsys.readouterr().out
    # the game's own name stays as-is; the labels around it are translated
    assert "Game One" in out
    assert "文件数" in out
    assert "清单文件" in out


def test_cli_digest_speaks_chinese(manifests_dir, capsys):
    from steamverify import cli

    code = cli.main(["--lang", "zh", "digest",
                     str(manifests_dir / "gameone" / "gameone-200.svm")])
    assert code == 0
    assert "sha256" in capsys.readouterr().out


def test_cli_doctor_speaks_chinese(manifests_dir, capsys):
    from steamverify import cli

    code = cli.main(["--lang", "zh", "--registry",
                     str(manifests_dir / "registry.json"), "doctor"])
    assert code == 0
    out = capsys.readouterr().out
    assert "全部内置清单均存在" in out


def test_cli_missing_dir_error_is_localised(tmp_path, capsys):
    from steamverify import cli

    code = cli.main([
        "--lang", "zh", "verify", "--dir", str(tmp_path / "nope"),
        "--manifest", str(_write_manifest(tmp_path)),
    ])
    assert code == 3
    assert "目录不存在" in capsys.readouterr().err


def test_cli_bad_manifest_error_is_localised(tmp_path, capsys):
    from steamverify import cli

    bad = tmp_path / "bad.svm"
    bad.write_bytes(b"NOPE" + b"\x00" * 64)
    code = cli.main(["--lang", "zh", "verify", "--manifest", str(bad),
                     "--dir", str(tmp_path)])
    assert code == 3
    err = capsys.readouterr().err
    assert "错误" in err


def test_cli_unknown_game_message(manifests_dir, capsys):
    from steamverify import cli

    code = cli.main(["--lang", "zh", "--registry",
                     str(manifests_dir / "registry.json"),
                     "verify", "--game", "nope", "--dir", str(manifests_dir)])
    assert code == 3
    assert "未知游戏" in capsys.readouterr().err


def _write_manifest(tmp_path):
    from conftest import plain_entry as _plain

    path = tmp_path / "fixture.svm"
    manifest_mod.write(path, {**HEADER, "game": "Fixture Game"},
                       [_plain("a.txt", b"a")])
    return path
