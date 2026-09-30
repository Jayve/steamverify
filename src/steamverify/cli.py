"""Command line interface.

Output language is resolved before the parser is built, so even ``--help`` is
localised: ``--lang`` → ``STEAMVERIFY_LANG`` → ``LC_ALL``/``LANG`` → English.
Machine-readable output (JSON keys, CSV headers, exit codes) is never
translated -- only prose shown to a person.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from pathlib import Path

from . import __version__
from .i18n import LANGUAGES, Translator, get_translator, set_language
from .manifest import Manifest, ManifestError
from .manifest import load as load_manifest
from .registry import GameEntry, Registry, RegistryError
from .reporter import _exit_code, display_width, pad, render_json, render_text, write_csv
from .scanner import ScanOptions, scan
from .steam import SteamLibrary, find_installations

EXIT_OK = 0
EXIT_EXTRA = 1
EXIT_MODIFIED = 2
EXIT_ERROR = 3


class CliError(Exception):
    """A problem the user can act on.

    ``translated`` marks messages that are already localised, so the top-level
    handler does not wrap them in a second, differently-worded prefix.
    """

    def __init__(self, message: str, *, translated: bool = False) -> None:
        super().__init__(message)
        self.translated = translated


# ---------------------------------------------------------------------------
# language selection
# ---------------------------------------------------------------------------
def _prescan_language(argv: list[str] | None) -> str | None:
    """Find ``--lang`` before argparse runs, so help text can be localised."""
    if not argv:
        return None
    for index, token in enumerate(argv):
        if token == "--lang" and index + 1 < len(argv):
            return argv[index + 1]
        if token.startswith("--lang="):
            return token.split("=", 1)[1]
    return None


# ---------------------------------------------------------------------------
# path resolution
# ---------------------------------------------------------------------------
def _resolve_game(registry: Registry, args) -> tuple[GameEntry | None, Manifest]:
    """Work out which manifest to use, from --game or --manifest."""
    tr = get_translator()
    if args.manifest:
        return None, _load_manifest(Path(args.manifest))
    if not args.game:
        raise CliError(tr("cli.specify_game"), translated=True)
    game = registry.get(args.game)
    manifest = game.load(getattr(args, "build", None))
    return game, manifest


def _load_manifest(path: Path) -> Manifest:
    try:
        return load_manifest(path)
    except ManifestError as exc:
        raise CliError(str(exc)) from exc


#: Backwards-compatible alias for the manifest loader used by the CLI.
Manifest_load = _load_manifest


def _ensure_root(args, game: GameEntry | None, manifest: Manifest) -> tuple[Path, str]:
    """Return ``(root, label)`` for the game directory."""
    tr = get_translator()
    if args.dir:
        root = Path(args.dir).expanduser()
        if not root.is_dir():
            raise CliError(tr("cli.dir_not_exist", path=root), translated=True)
        return root, tr("discover.user")

    app_id = game.app_id if game and game.app_id else manifest.app_id
    matches = find_installations(app_id, platform=manifest.platform or None)
    if matches:
        best = matches[0]
        candidates = [
            m for m in matches if manifest.build_id and m.build_id == manifest.build_id
        ]
        if candidates:
            best = candidates[0]
        label = best.library.label
        if best.build_id:
            label += f", build {best.build_id}"
        return best.path, label

    # fall back to the conventional install directory inside any library
    conventional = game.default_directory() if game else ""
    if conventional:
        for library in SteamLibrary.discover():
            candidate = library.app_dir(app_id, conventional)
            if candidate.is_dir():
                return candidate, f"{library.label} ({tr('discover.guessed')})"
    raise CliError(tr("discover.not_found"), translated=True)


# ---------------------------------------------------------------------------
# progress
# ---------------------------------------------------------------------------
def _make_progress(stream, enabled: bool):
    if not enabled:
        return None
    tr = get_translator()
    state = {"last": 0.0}

    def progress(phase: str, done: int, total: int, detail: str):
        now = time.monotonic()
        if phase == "walk":
            stream.write("\r  " + tr("progress.scanning", detail=detail))
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
            stream.write(f"\r  [{bar}] {percent:5.1f}%  {done:,}/{total:,}")
        else:
            stream.write("\r  " + tr("progress.hashing", done=f"{done:,}"))
        stream.flush()

    return progress


def _finish_progress(stream, enabled: bool) -> None:
    if enabled:
        stream.write("\r" + " " * 78 + "\r")
        stream.flush()


def _emit_fields(rows: list[tuple[str, str]]) -> None:
    """Print aligned ``label  value`` rows, CJK-aware."""
    if not rows:
        return
    width = max(display_width(label) for label, _ in rows)
    for label, value in rows:
        sys.stdout.write(f"  {pad(label, width)} {value}\n")


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def cmd_list(args) -> int:
    tr = get_translator()
    registry = Registry.load(args.registry)
    out = sys.stdout

    if args.json:
        payload = {
            "registry": str(registry.path),
            "generated": registry.generated,
            "games": [game.to_dict() for game in registry.games],
        }
        out.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return EXIT_OK

    out.write(
        tr("cli.bundled_games", count=len(registry), path=registry.path) + "\n\n"
    )
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
            out.write(f"      {tr('cli.aliases')}: {', '.join(game.aliases)}\n")
        out.write("\n")
    out.write(f"  {tr('cli.current_marker')}\n")
    out.write(f"\n  {tr('cli.verify_hint')}\n")
    return EXIT_OK


def cmd_games(args) -> int:
    return cmd_list(args)


def cmd_info(args) -> int:
    tr = get_translator()
    registry = Registry.load(args.registry)
    _, manifest = _resolve_game(registry, args)
    out = sys.stdout

    if args.json:
        out.write(
            json.dumps(
                {
                    "header": manifest.header,
                    "manifest_path": str(manifest.path or ""),
                    "manifest_sha256": manifest.sha256,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        return EXIT_OK

    out.write(f"{manifest.game}\n")
    rows = [
        (tr("cli.field.app_id"), manifest.app_id),
        (tr("cli.field.build"), manifest.build_id or "-"),
        (tr("cli.field.platform"), manifest.platform),
        (tr("cli.field.source"), manifest.source),
        (tr("cli.field.manifest_file"), str(manifest.path or "-")),
        (tr("cli.field.manifest_sha"), manifest.sha256),
        (tr("cli.field.files"), f"{manifest.file_count:,}"),
        (tr("cli.field.chunks"), f"{manifest.chunk_count:,}"),
        (tr("cli.field.total_size"), f"{manifest.total_bytes / 1024 ** 3:.2f} GiB"),
    ]
    if manifest.depots:
        rows.append((tr("cli.field.depots"), ", ".join(manifest.depots)))
    if manifest.header.get("generated"):
        rows.append((tr("cli.field.generated"), str(manifest.header["generated"])))
    if manifest.header.get("replay"):
        rows.append((tr("cli.field.regenerate"), str(manifest.header["replay"])))
    if manifest.header.get("notes"):
        rows.append((tr("cli.field.notes"), str(manifest.header["notes"])))
    _emit_fields(rows)

    if args.details:
        out.write(f"\n{tr('cli.largest_files')}\n")
        for entry in sorted(manifest.files, key=lambda e: -e.size)[: args.details]:
            kind = f"{entry.chunk_count} chunks" if entry.is_chunked else "plain"
            out.write(f"  {entry.size / 1024 ** 2:>10.2f} MiB  {kind:<12} {entry.path}\n")
    return EXIT_OK


def cmd_verify(args) -> int:
    tr = get_translator()
    registry = Registry.load(args.registry)
    game, manifest = _resolve_game(registry, args)
    root, label = _ensure_root(args, game, manifest)

    build_hint = ""
    if (
        game
        and manifest.build_id
        and game.current_build
        and manifest.build_id != game.current_build
    ):
        build_hint = (
            tr(
                "cli.build_hint",
                manifest_build=manifest.build_id,
                current_build=game.current_build,
            )
            + "\n"
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
                language=tr.language,
            )
        )

    if args.csv:
        path = write_csv(result, args.csv, language=tr.language)
        if not args.json:
            sys.stdout.write("\n" + tr("cli.csv_written", path=path) + "\n")

    if args.fail_on and args.fail_on != "any":
        if args.fail_on == "modified" and not (result.modified or result.missing):
            return EXIT_OK
        if args.fail_on == "missing" and not result.missing:
            return EXIT_OK

    return _exit_code(result)


def cmd_steam(args) -> int:
    """Inspect the local Steam installation (libraries and tracked apps)."""
    tr = get_translator()
    libraries = SteamLibrary.discover(extra_roots=args.library or [])
    if not libraries:
        sys.stderr.write(tr("cli.no_steam_libraries") + "\n")
        return EXIT_ERROR
    for library in libraries:
        sys.stdout.write(f"{library.label}\n")
        rows = [
            (tr("cli.steam_root"), str(library.root)),
            (tr("cli.steam_depotcache"),
             tr("cli.yes") if library.depotcache.is_dir() else tr("cli.no")),
            (tr("cli.steam_apps"), str(len(library.apps()))),
        ]
        if library.library_path:
            rows.insert(1, (tr("cli.steam_library"), str(library.library_path)))
        _emit_fields(rows)
        if args.verbose:
            for app in library.apps():
                sys.stdout.write(
                    f"    {app.app_id:>8}  build {app.build_id or '?':<10} {app.name}\n"
                )
    return EXIT_OK


def cmd_digest(args) -> int:
    """Print the SHA-256 of a manifest -- handy for registry entries."""
    for target in args.files:
        manifest = _load_manifest(Path(target))
        sys.stdout.write(f"{manifest.path}\n  sha256 {manifest.sha256}\n  {manifest.describe()}\n")
    return EXIT_OK


def cmd_doctor(args) -> int:
    """Check that the bundled manifests are present and unmodified."""
    from . import manifest as manifest_mod

    tr = get_translator()
    problems: list[str] = []
    try:
        registry = Registry.load(args.registry)
    except RegistryError as exc:
        sys.stderr.write(f"{tr('cli.error')}: {exc}\n")
        return EXIT_ERROR

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
    _emit_fields(
        [
            (tr("cli.registry"), str(registry.path)),
            (tr("cli.games"), str(len(registry))),
            (tr("cli.manifests_checked"), f"{checked}"),
        ]
    )
    if problems:
        sys.stdout.write(f"\n{tr('cli.manifests_problems')}\n")
        for problem in problems:
            sys.stdout.write(f"  - {problem}\n")
        return EXIT_ERROR
    sys.stdout.write(tr("cli.manifests_ok") + "\n")
    return EXIT_OK


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------
def build_parser(tr: Translator | None = None) -> argparse.ArgumentParser:
    tr = tr or get_translator()
    language_help = tr("cli.option.lang")
    choices = sorted(LANGUAGES)
    language_help += " (" + ", ".join(f"{code}={LANGUAGES[code][0]}" for code in choices) + ")"

    parser = argparse.ArgumentParser(
        prog="steamverify",
        description=tr("cli.description"),
        epilog=tr("cli.epilog"),
    )
    parser.add_argument("--version", action="version",
                        version=f"steamverify {__version__}")
    parser.add_argument("-L", "--lang", metavar="CODE", help=language_help)
    parser.add_argument("--registry", default=None, help=tr("cli.option.registry"))
    sub = parser.add_subparsers(dest="command")

    def add_selection(target):
        target.add_argument("-g", "--game", help=tr("cli.option.game"))
        target.add_argument("-m", "--manifest", help=tr("cli.option.manifest"))
        target.add_argument("--build", help=tr("cli.option.build"))

    lister = sub.add_parser("list", aliases=["games"], help=tr("cli.cmd.list"))
    lister.add_argument("--json", action="store_true", help=tr("cli.option.json"))
    lister.set_defaults(func=cmd_list)

    info = sub.add_parser("info", help=tr("cli.cmd.info"))
    add_selection(info)
    info.add_argument("--json", action="store_true", help=tr("cli.option.json"))
    info.add_argument("--details", type=int, default=0, metavar="N",
                      help=tr("cli.option.details"))
    info.set_defaults(func=cmd_info)

    verify = sub.add_parser("verify", help=tr("cli.cmd.verify"))
    add_selection(verify)
    verify.add_argument("-d", "--dir", help=tr("cli.option.dir"))
    verify.add_argument("--json", action="store_true", help=tr("cli.option.json"))
    verify.add_argument("--compact", action="store_true", help=tr("cli.option.compact"))
    verify.add_argument("--csv", metavar="PATH", help=tr("cli.option.csv"))
    verify.add_argument("--no-hash", action="store_true", help=tr("cli.option.no_hash"))
    verify.add_argument("--workers", type=int, default=0, help=tr("cli.option.workers"))
    verify.add_argument("--ignore", action="append", metavar="GLOB",
                        help=tr("cli.option.ignore"))
    verify.add_argument("--no-default-ignores", action="store_true",
                        help=tr("cli.option.no_default_ignores"))
    verify.add_argument("--follow-symlinks", action="store_true",
                        help=tr("cli.option.follow_symlinks"))
    verify.add_argument("--max-list", type=int, default=25,
                        help=tr("cli.option.max_list"))
    verify.add_argument("-v", "--verbose", action="store_true",
                        help=tr("cli.option.verbose"))
    verify.add_argument("-q", "--quiet", action="store_true", help=tr("cli.option.quiet"))
    verify.add_argument("--color", choices=["auto", "always", "never"], default="auto",
                        help=tr("cli.option.color"))
    verify.add_argument("--fail-on", choices=["any", "modified", "missing"],
                        default="any", help=tr("cli.option.fail_on"))
    verify.set_defaults(func=cmd_verify)

    steam = sub.add_parser("steam", help=tr("cli.cmd.steam"))
    steam.add_argument("--library", action="append", metavar="PATH",
                       help=tr("cli.option.library"))
    steam.add_argument("-v", "--verbose", action="store_true",
                       help=tr("cli.option.verbose"))
    steam.set_defaults(func=cmd_steam)

    doctor = sub.add_parser("doctor", help=tr("cli.cmd.doctor"))
    doctor.set_defaults(func=cmd_doctor)

    digest = sub.add_parser("digest", help=tr("cli.cmd.digest"))
    digest.add_argument("files", nargs="+", metavar="MANIFEST")
    digest.set_defaults(func=cmd_digest)

    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_output()
    argv = list(sys.argv[1:] if argv is None else argv)

    # Language must be known before the parser is built so --help is localised.
    tr = set_language(_prescan_language(argv))
    parser = build_parser(tr)
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_OK

    try:
        return args.func(args)
    except CliError as exc:
        if exc.translated:
            sys.stderr.write(f"{exc}\n")
        else:
            sys.stderr.write(f"{tr('cli.error')}: {exc}\n")
        return EXIT_ERROR
    except RegistryError as exc:
        # Raised from the registry layer, which does not know about languages.
        sys.stderr.write(f"{tr('cli.error')}: {_localise_registry_error(tr, exc)}\n")
        return EXIT_ERROR
    except ManifestError as exc:
        sys.stderr.write(f"{tr('cli.error')}: {exc}\n")
        return EXIT_ERROR
    except FileNotFoundError as exc:
        sys.stderr.write(f"{tr('cli.error')}: {exc.strerror or exc}\n")
        return EXIT_ERROR
    except BrokenPipeError:  # pragma: no cover - piping into head
        return EXIT_OK
    except KeyboardInterrupt:  # pragma: no cover - interactive
        sys.stderr.write(f"\n{tr('cli.interrupted')}\n")
        return 130


def _localise_registry_error(tr: Translator, exc: RegistryError) -> str:
    """Re-render the common registry failures in the selected language.

    The registry layer raises plain-English messages because it has no notion
    of a UI language; rather than push translations into data code, the
    messages it can raise are recognised here and reworded.
    """
    text = str(exc)
    if " is not valid JSON" in text:
        return text  # a parser detail: keep the file and the parser's words
    prefix = "unknown game '"
    if text.startswith(prefix):
        slug = text[len(prefix):].split("'", 1)[0]
        known = text.rsplit("bundled games:", 1)[-1].strip()
        return tr("err.unknown_game", slug=slug, known=known)
    if text.startswith("game '") and " has no manifests" in text:
        slug = text.split("'")[1]
        return tr("err.no_manifests", slug=slug)
    if " has no manifest for build " in text:
        slug, rest = text.split("'", 2)[1], text.split("'", 2)[2]
        build = rest.split("'")[1] if "'" in rest else "?"
        available = text.rsplit("available:", 1)[-1].strip().rstrip(")")
        return tr("err.no_build", slug=slug, build=build, available=available)
    return text


def _configure_output() -> None:
    """Ask for UTF-8 on stdout/stderr so non-ASCII paths render correctly.

    Required for the Chinese output as well as for non-ASCII file paths: a
    Latin-1 console would otherwise mangle or crash on them.  Failures are
    ignored -- the text is still usable.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
