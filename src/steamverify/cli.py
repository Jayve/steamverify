"""Command line interface."""

from __future__ import annotations

import argparse
import contextlib
import sys
import time
from pathlib import Path

from . import __version__
from .manifest import Manifest, ManifestError
from .registry import GameEntry, Registry, RegistryError
from .reporter import _exit_code, render_json, render_text, write_csv
from .scanner import ScanOptions, scan
from .steam import SteamLibrary, find_installations

EXIT_OK = 0
EXIT_EXTRA = 1
EXIT_MODIFIED = 2
EXIT_ERROR = 3


class CliError(Exception):
    """A problem the user can act on."""


# ---------------------------------------------------------------------------
# path resolution
# ---------------------------------------------------------------------------
def _resolve_game(registry: Registry, args) -> tuple[GameEntry | None, Manifest]:
    """Work out which manifest to use, from --game or --manifest."""
    if args.manifest:
        path = Path(args.manifest)
        return None, Manifest_load(path)
    if not args.game:
        raise CliError("specify a game with --game (see `steamverify list`)")
    game = registry.get(args.game)
    manifest = game.load(getattr(args, "build", None))
    return game, manifest


def Manifest_load(path: Path) -> Manifest:
    try:
        from .manifest import load as load_manifest

        return load_manifest(path)
    except ManifestError as exc:
        raise CliError(str(exc)) from exc


def _ensure_root(args, game: GameEntry | None, manifest: Manifest) -> tuple[Path, str]:
    """Return ``(root, label)`` for the game directory."""
    if args.dir:
        root = Path(args.dir).expanduser()
        if not root.is_dir():
            raise CliError(f"directory does not exist: {root}")
        return root, "user supplied (--dir)"

    app_id = game.app_id if game and game.app_id else manifest.app_id
    matches = find_installations(app_id, platform=manifest.platform or None)
    if matches:
        best = matches[0]
        candidates = [m for m in matches if manifest.build_id and m.build_id == manifest.build_id]
        if candidates:
            best = candidates[0]
        label = f"{best.library.label}"
        if best.build_id:
            label += f", build {best.build_id}"
        return best.path, label

    # fall back to the conventional install directory inside any library
    conventional = game.default_directory() if game else ""
    if conventional:
        for library in SteamLibrary.discover():
            candidate = library.app_dir(app_id, conventional)
            if candidate.is_dir():
                return candidate, f"{library.label} (guessed path)"
    raise CliError(
        "could not find the game directory automatically.\n"
        "Pass --dir \"<path to game folder>\" (the folder containing the game's exe)."
    )


# ---------------------------------------------------------------------------
# progress
# ---------------------------------------------------------------------------
def _make_progress(stream, enabled: bool):
    if not enabled:
        return None
    state = {"last": 0.0}

    def progress(phase: str, done: int, total: int, detail: str):
        now = time.monotonic()
        if phase == "walk":
            stream.write(f"\r  scanning {detail} ...")
            stream.flush()
            return
        if now - state["last"] < 0.1 and done != total:
            return
        state["last"] = now
        if total:
            percent = done * 100 / total
            bar_width = 24
            filled = int(bar_width * done / total)
            bar = "#" * filled + "." * (bar_width - filled)
            stream.write(f"\r  [{bar}] {percent:5.1f}%  {done:,}/{total:,} files")
        else:
            stream.write(f"\r  hashing {done:,} files")
        stream.flush()

    return progress


def _finish_progress(stream, enabled: bool) -> None:
    if enabled:
        stream.write("\r" + " " * 78 + "\r")
        stream.flush()


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def cmd_list(args) -> int:
    registry = Registry.load(args.registry)
    out = sys.stdout
    if args.json:
        import json

        payload = {
            "registry": str(registry.path),
            "generated": registry.generated,
            "games": [game.to_dict() for game in registry.games],
        }
        out.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return EXIT_OK

    out.write(f"Bundled games ({len(registry)}), registry: {registry.path}\n\n")
    for game in registry.games:
        out.write(f"  {game.name}\n")
        bits = [f"slug={game.slug}"]
        if game.app_id:
            bits.append(f"app={game.app_id}")
        if game.platform:
            bits.append(game.platform)
        out.write(f"      {'  '.join(bits)}\n")
        for record in game.manifests:
            marker = "*" if record.build_id == game.current_build else " "
            out.write(
                f"    {marker} build {record.build_id or '?':<12} "
                f"{record.file_count:>6,} files  "
                f"{record.total_bytes / 1024 ** 3:>7.2f} GiB  "
                f"{record.file}\n"
            )
        if game.aliases:
            out.write(f"      aliases: {', '.join(game.aliases)}\n")
        out.write("\n")
    out.write("  * = build used by default\n")
    out.write("\nVerify a game with:  steamverify verify --game <slug>\n")
    return EXIT_OK


def cmd_games(args) -> int:
    return cmd_list(args)


def cmd_info(args) -> int:
    registry = Registry.load(args.registry)
    _, manifest = _resolve_game(registry, args)
    out = sys.stdout

    if args.json:
        import json

        out.write(json.dumps(
            {
                "header": manifest.header,
                "manifest_sha256": manifest.sha256,
                "source": str(manifest.source or ""),
            },
            ensure_ascii=False, indent=2) + "\n")
        return EXIT_OK

    out.write(f"{manifest.game}\n")
    out.write(f"  app id         {manifest.app_id}\n")
    out.write(f"  build          {manifest.build_id or '(unknown)'}\n")
    out.write(f"  platform       {manifest.platform}\n")
    out.write(f"  source         {manifest.source}\n")
    out.write(f"  manifest file  {manifest.path}\n")
    out.write(f"  manifest sha256 {manifest.sha256}\n")
    out.write(f"  files          {manifest.file_count:,}\n")
    out.write(f"  chunks         {manifest.chunk_count:,}\n")
    out.write(f"  total size     {manifest.total_bytes / 1024 ** 3:.2f} GiB\n")
    if manifest.depots:
        out.write(f"  depots         {', '.join(manifest.depots)}\n")
    if manifest.header.get("generated"):
        out.write(f"  generated      {manifest.header['generated']}\n")
    if manifest.header.get("replay"):
        out.write(f"  regenerate     {manifest.header['replay']}\n")
    if manifest.header.get("notes"):
        out.write(f"  notes          {manifest.header['notes']}\n")

    if args.details:
        out.write("\nLargest files:\n")
        for entry in sorted(manifest.files, key=lambda e: -e.size)[:args.details]:
            kind = f"{entry.chunk_count} chunks" if entry.is_chunked else "plain"
            out.write(f"  {entry.size / 1024 ** 2:>10.2f} MiB  {kind:<12} {entry.path}\n")
    return EXIT_OK


def cmd_verify(args) -> int:
    registry = Registry.load(args.registry)
    game, manifest = _resolve_game(registry, args)
    root, label = _ensure_root(args, game, manifest)

    build_hint = ""
    if game and manifest.build_id and game.current_build \
            and manifest.build_id != game.current_build:
        build_hint = (
            f"note: this manifest is for build {manifest.build_id}, but the registry's "
            f"current build is {game.current_build}\n"
        )

    options = ScanOptions(
        workers=args.workers,
        verify_hashes=not args.no_hash,
        follow_symlinks=args.follow_symlinks,
        ignore=list(args.ignore or []),
        use_default_ignores=not args.no_default_ignores,
    )
    show_progress = not args.quiet and not args.json and sys.stdout.isatty()
    options.progress = _make_progress(sys.stdout, show_progress)

    try:
        result = scan(root, manifest, options)
    finally:
        _finish_progress(sys.stdout, show_progress)

    if build_hint and not args.json:
        sys.stderr.write(build_hint)

    if args.json:
        sys.stdout.write(render_json(result, indent=None if args.compact else 2) + "\n")
    else:
        sys.stdout.write(
            render_text(
                result,
                colour=args.color,
                max_list=args.max_list,
                verbose=args.verbose,
                root_label=f"{root}   [{label}]",
            )
        )

    if args.csv:
        path = write_csv(result, args.csv)
        if not args.json:
            sys.stdout.write(f"\nCSV written to {path}\n")

    if args.fail_on and args.fail_on != "any":
        if args.fail_on == "modified" and not (result.modified or result.missing):
            return EXIT_OK
        if args.fail_on == "missing" and not result.missing:
            return EXIT_OK

    return _exit_code(result)


def cmd_steam(args) -> int:
    """Inspect the local Steam installation (libraries and tracked apps)."""
    libraries = SteamLibrary.discover(extra_roots=args.library or [])
    if not libraries:
        sys.stderr.write("no Steam libraries found\n")
        return EXIT_ERROR
    for library in libraries:
        sys.stdout.write(f"{library.label}\n")
        sys.stdout.write(f"  root      {library.root}\n")
        if library.library_path:
            sys.stdout.write(f"  library   {library.library_path}\n")
        sys.stdout.write(f"  depotcache {'yes' if library.depotcache.is_dir() else 'no'}\n")
        apps = library.apps()
        sys.stdout.write(f"  apps      {len(apps)}\n")
        if args.verbose:
            for app in apps:
                sys.stdout.write(
                    f"    {app.app_id:>8}  build {app.build_id or '?':<10} {app.name}\n"
                )
    return EXIT_OK


def cmd_digest(args) -> int:
    """Print the SHA-256 of a manifest -- handy for registry entries."""
    for target in args.files:
        manifest = Manifest_load(Path(target))
        sys.stdout.write(
            f"{manifest.path}\n"
            f"  sha256 {manifest.sha256}\n"
            f"  {manifest.describe()}\n"
        )
    return EXIT_OK


def cmd_doctor(args) -> int:
    """Check that the bundled manifests are present and unmodified."""
    from . import manifest as manifest_mod

    problems: list[str] = []
    try:
        registry = Registry.load(args.registry)
    except RegistryError as exc:
        sys.stderr.write(f"registry error: {exc}\n")
        return EXIT_ERROR

    sys.stdout.write(f"registry        {registry.path}\n")
    sys.stdout.write(f"games           {len(registry)}\n")
    checked = 0
    for game in registry.games:
        for record in game.manifests:
            checked += 1
            path = game.directory_path / record.file
            if not path.is_file():
                problems.append(f"{game.slug}: missing {record.file}")
                continue
            try:
                loaded = manifest_mod.load(path, verify_digest=record.sha256)
            except ManifestError as exc:
                problems.append(f"{game.slug}/{record.file}: {exc}")
                continue
            coverage = manifest_mod.validate_coverage(loaded)
            if coverage:
                problems.append(
                    f"{game.slug}/{record.file}: {len(coverage)} chunk-coverage "
                    f"problem(s), first: {coverage[0]}"
                )
    sys.stdout.write(f"manifests       {checked} checked\n")
    if problems:
        sys.stdout.write("\nproblems:\n")
        for problem in problems:
            sys.stdout.write(f"  - {problem}\n")
        return EXIT_ERROR
    sys.stdout.write("all bundled manifests are present, intact and well-formed\n")
    return EXIT_OK


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="steamverify",
        description=(
            "Verify a Steam game installation against a bundled official hash "
            "manifest, and list files that are not part of the official build."
        ),
        epilog=(
            "exit codes: 0 clean, 1 extra files present, "
            "2 modified or missing official files, 3 errors"
        ),
    )
    parser.add_argument("--version", action="version",
                        version=f"steamverify {__version__}")
    parser.add_argument("--registry", default=None,
                        help="path to registry.json (default: bundled manifests)")
    sub = parser.add_subparsers(dest="command")

    def add_selection(target):
        target.add_argument("-g", "--game", help="bundled game slug, alias, or app id")
        target.add_argument("-m", "--manifest", help="path to a .svm manifest file")
        target.add_argument("--build", help="manifest build id (default: current)")

    lister = sub.add_parser("list", aliases=["games"], help="list bundled games")
    lister.add_argument("--json", action="store_true", help="machine-readable output")
    lister.set_defaults(func=cmd_list)

    info = sub.add_parser("info", help="show manifest details")
    add_selection(info)
    info.add_argument("--json", action="store_true")
    info.add_argument("--details", type=int, default=0, metavar="N",
                      help="also list the N largest files")
    info.set_defaults(func=cmd_info)

    verify = sub.add_parser("verify", help="scan a game directory")
    add_selection(verify)
    verify.add_argument("-d", "--dir", help="game directory (default: auto-detect)")
    verify.add_argument("--json", action="store_true", help="emit JSON")
    verify.add_argument("--compact", action="store_true", help="single-line JSON")
    verify.add_argument("--csv", metavar="PATH", help="also write a per-file CSV")
    verify.add_argument("--no-hash", action="store_true",
                        help="size-only check, much faster but weaker")
    verify.add_argument("--workers", type=int, default=0,
                        help="hashing threads (default: auto)")
    verify.add_argument("--ignore", action="append", metavar="GLOB",
                        help="skip matching paths (repeatable), e.g. 'mods/*'")
    verify.add_argument("--no-default-ignores", action="store_true",
                        help="also report the paths ignored by default "
                             "(logs, crash dumps, .steamverify/*)")
    verify.add_argument("--follow-symlinks", action="store_true")
    verify.add_argument("--max-list", type=int, default=25,
                        help="rows shown per section (default 25)")
    verify.add_argument("-v", "--verbose", action="store_true",
                        help="list every extra file")
    verify.add_argument("-q", "--quiet", action="store_true", help="no progress bar")
    verify.add_argument("--color", choices=["auto", "always", "never"], default="auto")
    verify.add_argument("--fail-on", choices=["any", "modified", "missing"],
                        default="any",
                        help="relax the exit code for CI (default: any difference)")
    verify.set_defaults(func=cmd_verify)

    steam = sub.add_parser("steam", help="show local Steam libraries")
    steam.add_argument("--library", action="append", metavar="PATH",
                       help="additional Steam root to inspect")
    steam.add_argument("-v", "--verbose", action="store_true")
    steam.set_defaults(func=cmd_steam)

    doctor = sub.add_parser("doctor", help="validate the bundled manifests")
    doctor.set_defaults(func=cmd_doctor)

    digest = sub.add_parser("digest", help="print manifest digests")
    digest.add_argument("files", nargs="+", metavar="MANIFEST")
    digest.set_defaults(func=cmd_digest)

    return parser


def _configure_output() -> None:
    """Ask for UTF-8 on stdout/stderr so non-ASCII paths render correctly.

    A Latin-1 console would otherwise mangle or crash on paths that are not
    pure ASCII.  Failures are ignored -- the text is still usable.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _configure_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_OK
    try:
        return args.func(args)
    except (CliError, RegistryError, ManifestError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return EXIT_ERROR
    except FileNotFoundError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return EXIT_ERROR
    except BrokenPipeError:  # pragma: no cover - piping into head
        return EXIT_OK
    except KeyboardInterrupt:  # pragma: no cover - interactive
        sys.stderr.write("\ninterrupted\n")
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
