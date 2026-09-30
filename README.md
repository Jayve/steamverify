# steamverify

**Verify a Steam game installation against a bundled official hash manifest — and list every file that is not part of the official build.**

[English](README.md) · [简体中文](docs/README.zh-CN.md)

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
$ steamverify verify --game witcher3

steamverify  v0.1.0
------------------------------------------------------------------------
  game        The Witcher 3: Wild Hunt
  app id      292030
  build       25575366
  manifest    1,937 files, 73,477 chunks, 69.19 GiB
  scanned     2026-09-30T15:37:57Z  (69.0s)
------------------------------------------------------------------------

Summary
  # verified    1,915   files match the official manifest
  . modified        0   tracked files whose content differs
  # stub           22   content-free placeholder entries (DLC markers)
  # extra       4,968   files not present in any official manifest
  . missing         0   official files that are absent

  1,915 files / 69.19 GiB verified against the official build
  4,968 extra files / 761.11 MiB not in the official build

  RESULT: game data intact; only content-free DLC marker files differ.
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
```

### Exit codes

Useful for scripts and CI:

| Code | Meaning |
|-----:|---------|
| `0` | Clean — the install matches the manifest exactly |
| `1` | Official files intact, but extra files are present |
| `2` | Official files are modified, missing, or present only as stubs |
| `3` | Errors (unreadable files, bad manifest, no install found) |

`--fail-on modified` and `--fail-on missing` relax this when you only care
about a subset.

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

See [CONTRIBUTING.md](CONTRIBUTING.md) and
[docs/manifest-format.md](docs/manifest-format.md) for details.

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
    print(item.path, item.reason)
for group, count, size in result.extra_groups():
    print(f"{count:>6} files  {size / 1024**2:>9.1f} MiB  {group}")
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
