# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.1] - 2026-10-01

### Added

- **Bilingual output: English and Simplified Chinese.** Select with `--lang`,
  the `STEAMVERIFY_LANG` variable, or let it follow the system locale
  (`LC_ALL`/`LC_MESSAGES`/`LANG`), falling back to English. The whole text
  report, progress output, `--help`, `list`, `info`, `doctor`, `steam` and all
  error messages are translated.
- Chinese documentation: [`docs/README.zh-CN.md`](docs/README.zh-CN.md),
  [`docs/CONTRIBUTING.zh-CN.md`](docs/CONTRIBUTING.zh-CN.md) and
  [`docs/manifest-format.zh-CN.md`](docs/manifest-format.zh-CN.md).

### Changed

- Scan results now carry an i18n **key** (`scan.reason.*`) plus its arguments
  instead of a pre-formatted English string, and `FileResult.reason` became a
  method that renders on demand: `item.reason("zh")`. The scan engine therefore
  has no language of its own.
- Report and CLI columns are aligned using terminal display width rather than
  `len()`, so Chinese labels line up in monospaced terminals.
- JSON output gained `reason_text` (English prose) next to the stable `reason`
  key, and `info --json` renamed `source` to `manifest_path` for clarity.

### Notes

- Machine-readable output stays English: JSON keys and values, CSV headers, the
  `status` column and exit codes are unaffected by `--lang`, so scripts and CI
  behave identically in both languages.
- A missing translation falls back to English rather than leaking a raw key, and
  the test suite asserts both catalogs have identical key sets and `{}`
  placeholders.

## [0.1.0] - 2026-09-30

First release.

### Added

- **Bundled manifest for The Witcher 3: Wild Hunt** (Steam app 292030, build
  25575366, Windows): 1,937 official files, 73,477 chunks, 69.19 GiB, merged
  from all 32 installed depots with per-file and per-chunk SHA-1.
- `steamverify verify` — scan a game directory against a manifest; classifies
  every file as `verified`, `modified`, `stub`, `extra` or `missing`, with
  progress, colour output and actionable next steps.
- `steamverify list` / `info` / `doctor` / `digest` / `steam` commands.
- Text, JSON and CSV reports; documented exit codes for CI.
- Automatic game discovery through Windows registry, macOS, Linux and
  Flatpak Steam roots, `libraryfolders.vdf`, and `STEAM_ROOT`.
- Size-only fast mode (`--no-hash`) and repeatable `--ignore GLOB` rules.
- SteamPipe-aware verification: chunked files are checked chunk by chunk so a
  corrupt multi-GB archive is localised instead of reported wholesale.
- `tools/build_manifest.py` — generate a manifest for any Steam game from the
  client's own depot caches, with no network access and no login.
- `tools/build_from_directory.py` — generate a manifest from any trusted
  directory, for non-Steam builds.
- `tools/add_game.py` — register, list and prune manifest records.
- `.svm` manifest format with interned path/hash tables, a version gate and
  structural validation on load (see `docs/manifest-format.md`).
- Test suite (85 tests) covering the manifest format, the KeyValues parser,
  the depot protobuf parser, the scan engine and the reporters. Runs without
  any game installed.
- GitHub Actions CI across Linux, Windows and macOS.

### Notes

- Library code uses the Python standard library only; there are no runtime
  dependencies.
- `stub` was introduced during development after zero-byte DLC ownership
  tombstones were initially reported as "modified", which is technically true
  but misleading. They are now their own category and do not affect the
  "game data intact" verdict.

[Unreleased]: https://github.com/Jayve/steamverify/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/Jayve/steamverify/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Jayve/steamverify/releases/tag/v0.1.0
