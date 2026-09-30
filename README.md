# steamverify

**Verify a Steam game installation against a bundled official hash manifest — and list every file that is not part of the official build.**

**English** · [简体中文](docs/README.zh-CN.md)

The tool itself speaks both languages — `--lang zh` for Chinese, or let it follow
your system locale. See [Output language](#output-language).

---

`steamverify` answers two questions that Steam's own "Verify integrity of game
files" does not:

1. **Are the official game files intact?** Every file is checked byte-for-byte,
   down to the individual SteamPipe chunk, against hashes taken from Steam's
   own depot manifests.
2. **What is in this folder that should not be?** Mods, trainers, cheat menus,
   repack leftovers, crash dumps, save games and third-party tool bundles are
   listed explicitly instead of being silently ignored.

It runs **fully offline**: the manifests ship in this repository, so a scan is
reproducible and needs no network access, no Steam login and no API keys.

```
steamverify  v0.1.1
------------------------------------------------------------------------
  game        The Witcher 3: Wild Hunt
  app id      292030
  build       25575366
  manifest    1,937 files, 73,477 chunks, 69.19 GiB
  manifest id sha256:cb2ddd355f0d42c5
  directory   <GAME_DIR>   [S:\SteamLibrary, build 25575366]
  scanned     <UTC>  (<elapsed>)
------------------------------------------------------------------------

Summary
  # verified    1,915   files match the official manifest
  . modified        0   tracked files whose content differs
  # stub           22   content-free placeholder entries (DLC markers)
  # extra       4,960   files not present in any official manifest
  . missing         0   official files that are absent
  - ignored       297   skipped by ignore rules

  1,915 files / 69.19 GiB verified against the official build
  4,960 extra files / 754.88 MiB not in the official build

  RESULT: game data intact; only content-free DLC marker files differ.

Extra files by location
    4,952 files    746.38 MiB   tools/
        3 files      7.25 MiB   dlc/bob/
        2 files      1.00 MiB   mods/mod0001/
        2 files    256.00 KiB   dlc/dlc10/
        1 files           8 B   content/notes.txt
  Paths are relative to the game directory; a trailing / marks a folder in which every file is extra. Hide the ones you keep on purpose with --ignore '<path>/*', or add -v for each file's size and time.

Placeholder entries (not a corruption signal)
  S dlc-tombstones/bob/bob.tombstone
  S dlc-tombstones/bob/bob_speech_cn.tombstone
  S dlc-tombstones/bob/bob_speech_en.tombstone
  S dlc-tombstones/dlc1/dlc1.tombstone
  S dlc-tombstones/dlc10/dlc10.tombstone
  ... 17 more
  These official entries carry an identifier hash but are legitimately empty on disk.

What to do
  1. Extra files are not part of the official build. Mods, trainers,
     save games, crash dumps and repack leftovers show up here.
     Delete what you do not want; the game itself is unaffected.

  Exit code: 2
```

---

## Why this exists

Steam can tell you that *something* is wrong. It cannot tell you **what** is
extra, because it only tracks files it installed itself. That gap matters when
you are trying to work out why a game behaves oddly, whether a download was
tampered with, or what a "repack" actually added to your disk.

This tool was built after exactly that investigation: a 70 GB install turned
out to have pristine official data plus **5,264 files (763 MiB) of third-party
tooling, performance-config overrides and a cheat-menu config** that Steam had
no idea about. Nothing else could have surfaced that split.

## Installation

Requires Python 3.9+. There are no runtime dependencies.

```bash
git clone https://github.com/Jayve/steamverify.git
cd steamverify
pip install -e .
steamverify --help
```

Or run it straight from the checkout with no install step:

```bash
python run.py verify --game witcher3
```

## Usage

```bash
# what games ship with a manifest?
steamverify list

# verify a game, finding it automatically
steamverify verify --game witcher3

# point at a specific directory (portable drives, copies, other libraries)
steamverify verify --game witcher3 --dir "D:/Games/The Witcher 3"

# see every extra file, not just the first 25
steamverify verify --game witcher3 -v

# machine-readable, for CI or your own tooling
steamverify verify --game witcher3 --json > report.json
steamverify verify --game witcher3 --csv report.csv

# quick pass: sizes only, no content hashing (seconds instead of minutes)
steamverify verify --game witcher3 --no-hash

# ignore your own mods so only real surprises stand out
steamverify verify --game witcher3 --ignore 'mods/*' --ignore 'dlc/*'

# check the bundled manifests themselves have not been tampered with
steamverify doctor

# inspect local Steam libraries
steamverify steam -v

# Chinese output (also: STEAMVERIFY_LANG=zh, or a zh_* system locale)
steamverify --lang zh verify --game witcher3
```

### Output language

The report is prose, so it is localised; the machine-readable parts are not.

| | English | 简体中文 |
|---|---|---|
| Select with | `--lang en` | `--lang zh` |
| Aliases accepted | `en`, `en-US`, `english` | `zh`, `zh-CN`, `zh-Hans`, `chs`, `cn`, `中文`, `简体中文` |
| Environment variable | `STEAMVERIFY_LANG=en` | `STEAMVERIFY_LANG=zh` |
| Auto-detected from | `LC_ALL` / `LC_MESSAGES` / `LANG` | same |

Resolution order: `--lang` → `STEAMVERIFY_LANG` → system locale → English.

What is translated:

* the whole text report, including section headings, the verdict, the reasons
  attached to each file and the "what to do" advice;
* progress output and `--help`;
* `list`, `info`, `doctor`, `steam` and error messages.

What is **never** translated, so scripts and CI stay unaffected by `--lang`:

* JSON keys and values, including `status` (`verified`/`extra`/…) and the
  `reason` field (a stable key such as `scan.reason.content_differs`, with the
  English wording alongside it in `reason_text`);
* CSV column headers and the `status` column;
* exit codes.

A missing translation falls back to English rather than leaking a raw key, and
CI asserts the two catalogs have identical key sets and placeholders.

## Example output

Every sample below is **real tool output** from a 69 GiB install, scrubbed only
by replacing the absolute directory with `<GAME_DIR>`, the timestamp with
`<UTC>` and the duration with `<elapsed>`. Full files:
[`docs/examples/`](docs/examples/).

### A normal run: official data intact, third-party files present

```
  game        The Witcher 3: Wild Hunt
  app id      292030
  build       25575366
  manifest    1,937 files, 73,477 chunks, 69.19 GiB
  manifest id sha256:cb2ddd355f0d42c5
  directory   <GAME_DIR>   [S:\SteamLibrary, build 25575366]
  scanned     <UTC>  (<elapsed>)
------------------------------------------------------------------------

Summary
  # verified    1,915   files match the official manifest
  . modified        0   tracked files whose content differs
  # stub           22   content-free placeholder entries (DLC markers)
  # extra       4,960   files not present in any official manifest
  . missing         0   official files that are absent
  - ignored       297   skipped by ignore rules

  1,915 files / 69.19 GiB verified against the official build
  4,960 extra files / 754.88 MiB not in the official build

  RESULT: game data intact; only content-free DLC marker files differ.

Extra files by location
    4,952 files    746.38 MiB   tools/
        3 files      7.25 MiB   dlc/bob/
        2 files      1.00 MiB   mods/mod0001/
        2 files    256.00 KiB   dlc/dlc10/
        1 files           8 B   content/notes.txt
  Paths are relative to the game directory; a trailing / marks a folder in which every file is extra. Hide the ones you keep on purpose with --ignore '<path>/*', or add -v for each file's size and time.

Placeholder entries (not a corruption signal)
  S dlc-tombstones/bob/bob.tombstone
  S dlc-tombstones/bob/bob_speech_cn.tombstone
  S dlc-tombstones/bob/bob_speech_en.tombstone
  S dlc-tombstones/dlc1/dlc1.tombstone
  S dlc-tombstones/dlc10/dlc10.tombstone
  ... 17 more
  These official entries carry an identifier hash but are legitimately empty on disk.

What to do
  1. Extra files are not part of the official build. Mods, trainers,
     save games, crash dumps and repack leftovers show up here.
     Delete what you do not want; the game itself is unaffected.

  Exit code: 2
```

Full output: [`examples/report-full.txt`](docs/examples/report-full.txt) ·
[中文](docs/examples/report-full.zh-CN.txt)

### A clean run, once you ignore what you added on purpose

```bash
steamverify verify --game witcher3 \
    --ignore 'tools/*' --ignore 'mods/*' --ignore 'dlc/bob/*' \
    --ignore 'dlc/dlc10/*' --ignore 'content/notes.txt' --ignore '*.md' \
    --ignore '*.stamp' --ignore 'metadata.store'
```

```
Summary
  # verified    1,915   files match the official manifest
  . modified        0   tracked files whose content differs
  # stub           22   content-free placeholder entries (DLC markers)
  . extra           0   files not present in any official manifest
  . missing         0   official files that are absent
  - ignored     5,259   skipped by ignore rules

  1,915 files / 69.19 GiB verified against the official build

  RESULT: game data intact; only content-free DLC marker files differ.

Placeholder entries (not a corruption signal)
  S dlc-tombstones/bob/bob.tombstone
  S dlc-tombstones/bob/bob_speech_cn.tombstone
  S dlc-tombstones/bob/bob_speech_en.tombstone
  S dlc-tombstones/dlc1/dlc1.tombstone
  S dlc-tombstones/dlc10/dlc10.tombstone
  ... 17 more
  These official entries carry an identifier hash but are legitimately empty on disk.

What to do
  Nothing to fix -- these placeholders are meant to be empty. The exit code is still non-zero because the tree is not a byte-exact match; use --fail-on missing to ignore them in CI.

  Exit code: 2
```

Full output: [`examples/report-clean.txt`](docs/examples/report-clean.txt) ·
[中文](docs/examples/report-clean.zh-CN.txt)

### Why the clean run still exits 2

Everything in that second sample is *correct*, yet the exit code is 2, not 0.
That is deliberate:

* `stub` means the official manifest lists an entry, but the local file is
  zero bytes. That is **not** a corruption signal -- but it does mean the tree
  is not byte-for-byte identical to the official build, so it is not reported
  as clean;
* use `--fail-on missing` (or `--fail-on modified`) when you do not want those
  to fail a CI job;
* to ask only "was any official game data altered?", read `game_data_intact`
  from the JSON report -- placeholders do not affect it.

The samples are regenerated from a real install with
[`tools/generate_examples.py`](tools/generate_examples.py), and CI runs
[`tools/check_docs.py`](tools/check_docs.py) to prove every block quoted here
still matches them verbatim.

### How extra paths are reported

A report is only useful if it tells you what to delete, so every extra is named
by a path relative to the game directory. The only thing that is ever collapsed
is a folder in which **every** file is extra:

| Shown | Means | Action |
|---|---|---|
| `tools/` (trailing slash) | every file under `tools/` is extra, and the official manifest has nothing there | the whole folder can go, or `--ignore 'tools/*'` to keep it |
| `content/notes.txt` | this one file is extra and sits among official files | delete the file, or `--ignore 'content/notes.txt'` |

A folder is never collapsed when a single official file lives in it, so nothing
that the manifest tracks can hide behind a directory row. Long paths are printed
in full rather than truncated -- a path you cannot copy is not actionable.
`-v` additionally lists every extra file with its size and modification time,
and `--json` / `--csv` always carry the per-file detail.

## How it works

1. **Manifests come from Steam itself.** The Steam client caches a binary
   depot manifest for every installed depot under `<steam>/depotcache/`. Those
   files contain the official path, size and SHA-1 of every file, plus the
   SHA-1 of every SteamPipe chunk. `tools/build_manifest.py` reads them and
   packs the result into a compact `.svm` file. **No hashes are invented.**

2. **The manifest is validated against Steam's bookkeeping.** The byte total
   reconstructed from the depot manifests must equal the `SizeOnDisk` recorded
   in `appmanifest_<appid>.acf`. A mismatch means the parse is wrong, and the
   build fails loudly instead of shipping bad data.

3. **Scanning classifies every file.** Sizes first, then content: whole-file
   SHA-1 for ordinary files, per-chunk SHA-1 for SteamPipe archives. Damage in
   a 31 GB `.cache` is reported as *which chunk*, not just "this file differs".

4. **Anything not in the manifest is extra.** That is the whole point — it is
   the category Steam itself cannot report.

### File categories

| Category | Meaning |
|---|---|
| `verified` | Size and content match the official manifest |
| `modified` | Tracked file whose size or content differs |
| `stub` | Official entry carries only an identifier hash; the local file is legitimately empty (DLC ownership tombstones) — **not** a corruption signal |
| `extra` | Not present in any official manifest |
| `missing` | Official file that is absent |

By default a few paths are ignored because they are never interesting: this
tool's own output, `*.log`, `*.dmp`, `__pycache__`, `steam_appid.txt` and
similar. Pass `--no-default-ignores` to see everything, or `--ignore GLOB` to
add your own.

## Adding a game

Adding support for another title is a data change, not a code change.

```bash
python tools/build_manifest.py \
    --app-id 292030 \
    --slug witcher3 \
    --name "The Witcher 3: Wild Hunt" \
    --vendor "CD Projekt Red" \
    --out manifests
```

The tool finds the ACF and depot manifests in your local Steam installation,
rebuilds the official file list, writes `manifests/<slug>/<slug>-<build>.svm`
and registers it in `manifests/registry.json`. Then open a pull request.

Requirements for a manifest to be accepted:

- Built from a **freshly installed, never-modded** copy;
- The depot count and byte total must reconcile with the ACF;
- `steamverify doctor` must report no problems.

If a game is not from Steam (GOG, Epic, a portable copy), hash a directory you
trust instead:

```bash
python tools/build_from_directory.py --dir "D:/Games/SomeGame" \
    --slug somegame --name "Some Game" --out manifests
```

See [CONTRIBUTING.md](CONTRIBUTING.md) ([中文](docs/CONTRIBUTING.zh-CN.md)) and the
[manifest format spec](docs/manifest-format.md)
([中文](docs/manifest-format.zh-CN.md)) for details.

## Bundled games

| Game | App ID | Build | Platform | Files | Size |
|---|---|---:|---|---:|---:|
| The Witcher 3: Wild Hunt | 292030 | 25575366 | windows | 1,937 | 69.19 GiB |

Community contributions for other games are very welcome — the scanner has no
game-specific code in it.

## Library use

```python
from steamverify import registry, scanner

game = registry.Registry.load().get("witcher3")
manifest = game.load()                      # verifies the manifest digest
result = scanner.scan("/path/to/game", manifest)

print(result.counts())
for item in result.modified:
    print(item.path, item.reason("en"))

# folders whose contents are all extra: (path, files, bytes)
for folder, count, size in result.extra_folders():
    print(f"{count:>6} files  {size / 1024**2:>9.1f} MiB  {folder}/")

# ...and the extras that sit among official files, which need naming one by one
for item in result.loose_extra_files():
    print(f"{item.size:>12,} B  {item.path}")

# both kinds at once, biggest first: (path, files, bytes, is_folder)
for path, count, size, is_folder in result.extra_groups():
    print(f"{count:>6} files  {size / 1024**2:>9.1f} MiB  {path}{'/' if is_folder else ''}")
```

The scan engine has no language of its own: it records reasons as i18n keys and
you render them on demand, so the same result can be printed in either language.

```python
from steamverify.i18n import get_translator

tr = get_translator("zh")
print(tr("report.result.clean"))
print(result.modified[0].reason(tr))
```

## Limitations, honestly

- **A manifest is only valid for one build.** After a game update the bundled
  manifest is stale; every file will look modified. Run `steamverify info` and
  compare `info`'s build id against the ACF, or rebuild the manifest. The tool
  warns when the ACF build and the manifest build disagree.
- **A clean scan is not a security guarantee.** It proves the files match a
  known-good build. It says nothing about what a *mod* does, and an attacker
  who controls your manifest source can lie to you. Compare the manifest's
  SHA-256 against a second source.
- **The `stub` heuristic is deliberately narrow** (a zero-byte local file whose
  official size is also zero). It will not hide real corruption.
- **Encrypted depot manifests** (password-protected beta branches) cannot be
  read and are reported rather than mis-parsed.
- Hashing 70 GB takes about a minute on an SSD and considerably longer on a
  spinning disk. Use `--no-hash` when you only need a file inventory.

## Legal

`steamverify` is MIT licensed and contains **no game code or game data** — only
file paths, byte sizes and SHA-1 hashes, which is the same information Steam
itself stores locally and publishes to every client. Game names and trademarks
belong to their respective owners. See [NOTICE](NOTICE).

## Contributing

Bug reports, new game manifests, and platform testing are all welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
