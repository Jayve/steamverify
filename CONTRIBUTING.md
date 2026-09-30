# Contributing to steamverify

Thanks for helping. There are three useful ways to contribute, in rough order
of value: **new game manifests**, **bug reports from other platforms**, and
**code**.

---

## 1. Adding a game manifest

Manifests are data, not code. The scanner has no game-specific logic.

### Build it

On a machine with the game installed through Steam:

```bash
python tools/build_manifest.py \
    --app-id 292030 \
    --slug witcher3 \
    --name "The Witcher 3: Wild Hunt" \
    --vendor "CD Projekt Red" \
    --store-url "https://store.steampowered.com/app/292030/" \
    --out manifests
```

Add `--verbose` to see per-depot progress. The script:

1. reads `steamapps/appmanifest_<appid>.acf` to learn which depots and which
   build are installed;
2. reads each depot's binary manifest from `<steam>/depotcache/`;
3. merges them into `manifests/<slug>/<slug>-<build>.svm`;
4. registers it in `manifests/registry.json`;
5. validates chunk coverage and refuses to write a broken file.

Useful flags: `--default-dir windows=<folder name>` (auto-discovery hint),
`--alias <name>` (extra CLI aliases), `--dry-run`, `--no-write`.

### Requirements before opening a PR

- [ ] **Source from a pristine install.** Freshly downloaded, never modded,
      never repacked. A manifest built from an already-modified copy will mark
      legitimate files as foreign and vice versa.
- [ ] **`steamverify doctor` reports no problems.**
- [ ] **The byte total reconciles.** The script derives it from the depot
      manifests; confirm it is plausible against the game's store page or
      `SizeOnDisk` in the ACF. A large gap means a depot could not be read.
- [ ] **A note in the PR** with the game name, app id, build id, platform and
      how the install was obtained.

### If a depot manifest is missing

Steam only caches manifests for depots it has actually installed and updated.
If the script warns about skipped depots:

- make sure the game has been installed **and updated at least once** by the
  Steam client on this machine;
- check `<steam>/depotcache/` for `<depotid>_*.manifest`;
- for language depots, change the game's language in Steam properties, let it
  download, then re-run. Files for a language you never installed cannot be
  hashed from local data.

Never fabricate a hash. A manifest with guessed entries is worse than no
manifest.

### Non-Steam builds

For GOG, Epic, portable or preserved copies, hash a directory you trust:

```bash
python tools/build_from_directory.py \
    --dir "D:/Games/SomeGame" --slug somegame --name "Some Game" \
    --version "1.2.3" --out manifests
```

Note in the PR that the manifest is directory-derived, not Steam-derived.
`--chunk-size` optionally splits files into fixed chunks so future scans can
localise damage, at the cost of a larger manifest.

### Manually registering a manifest

```bash
python tools/add_game.py --manifest manifests/somegame/somegame-1.2.3.svm \
    --slug somegame --name "Some Game" --app-id 1234
python tools/add_game.py --list
python tools/add_game.py --prune      # drop records whose file is gone
```

---

## 2. Reporting a bug

Please include:

- `steamverify --version` and your OS;
- the **exact command** you ran;
- the output of `steamverify verify ... --json` if the scan itself is wrong;
- `steamverify doctor` and `steamverify list` output if a bundled manifest is
  involved;
- for a parsing error, which game and which store.

**Never paste your Steam credentials** — nothing here needs them. Redact
absolute paths if you prefer; only the failing relative path matters.

## 3. Code

### Layout

```
src/steamverify/
    manifest.py     .svm format: parse, write, validate
    acf.py          Valve KeyValues parser + AppManifest view
    depot.py        Steam's binary depot manifest (protobuf) parser
    steam.py        locate Steam roots, libraries and apps
    registry.py     bundled-game registry
    scanner.py      the scan engine (game-agnostic)
    reporter.py     text / JSON / CSV output
    cli.py          argument parsing and commands
tools/              manifest builders (not part of the installed package)
tests/              pytest suite; no game install required
manifests/          bundled game data + registry.json
```

Keep `scanner.py` free of game-specific knowledge. Anything that knows about
a particular title belongs in `manifests/`.

### Development

```bash
git clone https://github.com/Jayve/steamverify.git
cd steamverify
python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q
ruff check src tools tests
```

The suite must pass with no game installed — it synthesises Steam manifests
byte by byte so that the parser is tested against the wire format rather than
against a fixture file that could drift.

### Guidelines

- **No runtime dependencies.** Standard library only. This is a tool people
  run on a machine that is already misbehaving; asking them to resolve a
  dependency tree first is a bad trade.
- **Fail loudly.** A parse that might be wrong must raise, not guess. See the
  deprecation of the "field 5 is a symlink target" assumption in `depot.py`
  for what happens when a parser guesses.
- **Document the surprising parts.** Steam's formats contain real traps
  (depot ids live in filenames; chunk offsets are not what they look like;
  directories masquerade as 64-byte files). If you spend an hour on one,
  write it down in the module docstring.
- **Add a test with every behavioural change**, especially a regression test
  named after the bug.
- **Keep reports readable.** A user should learn what is wrong and what to do
  next within the first screen.
- Match the existing style: type hints, `from __future__ import annotations`,
  no bare `except`.

### Changing the manifest format

Read [docs/manifest-format.md](docs/manifest-format.md) first. Bumping
`FORMAT_VERSION` is a breaking change for older clients — they will refuse to
read the file, which is the intended behaviour. Never reinterpret bytes under
an existing version number.

## Commit and PR conventions

- One logical change per commit; imperative subject line
  (`fix: locate depot manifests in every library`, not `fixes`).
- Say **why** in the body, not just what.
- PRs that add a game manifest should include the regenerated
  `manifests/registry.json`.

## Code of conduct

Be decent. Disagreement about a parser is fine; dismissing people is not.
