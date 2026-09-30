"""Locate Steam installations, libraries, and installed apps.

Finding the game directory automatically makes ``steamverify verify --game X``
work without arguments on a normal machine.  Everything here is read-only.

Typical layout::

    <steam root>/
        steamapps/
            libraryfolders.vdf      # other libraries
            appmanifest_<appid>.acf
            common/<installdir>/    # the game
        depotcache/                 # per-depot manifests (official hashes)

On Windows the Steam root additionally comes from the registry, which is what
makes multi-drive library setups discoverable without hard-coded paths.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import acf

__all__ = ["SteamApp", "SteamLibrary", "find_app", "find_installations", "steam_roots"]


# ---------------------------------------------------------------------------
# root discovery
# ---------------------------------------------------------------------------
def _windows_roots() -> list[Path]:
    if not sys.platform.startswith("win"):
        return []
    roots: list[Path] = []
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover
        return []
    candidates = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ]
    for hive, key, value_name in candidates:
        try:
            with winreg.OpenKey(hive, key) as handle:
                value, _ = winreg.QueryValueEx(handle, value_name)
        except OSError:
            continue
        if value:
            roots.append(Path(str(value)))
    return roots


def _common_roots() -> list[Path]:
    home = Path.home()
    if sys.platform == "darwin":
        return [home / "Library/Application Support/Steam"]
    if sys.platform.startswith("win"):
        return [Path("C:/Program Files (x86)/Steam"), Path("C:/Program Files/Steam")]
    return [
        home / ".steam/steam",
        home / ".local/share/Steam",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    ]


def steam_roots(extra: list[str] | None = None) -> list[Path]:
    """Candidate Steam installation roots, de-duplicated, existing ones first."""
    candidates: list[Path] = []
    env = os.environ.get("STEAM_ROOT")
    if env:
        candidates.append(Path(env))
    candidates.extend(_windows_roots())
    candidates.extend(_common_roots())
    for value in extra or []:
        candidates.append(Path(value))

    seen: set[str] = set()
    ordered: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(resolved)
    return [root for root in ordered if root.is_dir()]


# ---------------------------------------------------------------------------
# apps and libraries
# ---------------------------------------------------------------------------
@dataclass
class SteamApp:
    """One installed app, read from its ACF file."""

    app_id: str
    name: str
    install_dir: str
    build_id: str
    state_flags: int
    size_on_disk: int
    library: SteamLibrary
    manifest: acf.AppManifest

    @property
    def path(self) -> Path:
        return self.library.app_dir(self.app_id, self.install_dir)

    @property
    def is_installed(self) -> bool:
        return bool(self.state_flags & 4)

    def describe(self) -> str:
        return f"{self.name} (app {self.app_id}, build {self.build_id or '?'})"


@dataclass
class SteamLibrary:
    """A Steam library folder (``.../steamapps``)."""

    root: Path
    steamapps: Path
    library_path: Path | None = None
    _apps: list[SteamApp] | None = field(default=None, repr=False)

    @property
    def label(self) -> str:
        return str(self.library_path or self.root)

    @property
    def depotcache(self) -> Path:
        return self.root / "depotcache"

    @property
    def appmanifest_dir(self) -> Path:
        return self.steamapps

    def app_dir(self, app_id: str, install_dir: str = "") -> Path:
        common = self.steamapps / "common"
        if install_dir:
            return common / install_dir
        # fall back to whatever directory starts with the app id
        if common.is_dir():
            for child in common.iterdir():
                if child.name.startswith(f"{app_id} "):
                    return child
        return common / app_id

    def apps(self) -> list[SteamApp]:
        if self._apps is not None:
            return self._apps
        found: list[SteamApp] = []
        for path in sorted(self.steamapps.glob("appmanifest_*.acf")):
            try:
                manifest = acf.AppManifest.load(path)
            except (acf.KeyValuesError, OSError):
                continue
            if not manifest.app_id:
                continue
            found.append(
                SteamApp(
                    app_id=manifest.app_id,
                    name=manifest.name,
                    install_dir=manifest.install_dir,
                    build_id=manifest.build_id,
                    state_flags=manifest.state_flags,
                    size_on_disk=manifest.size_on_disk,
                    library=self,
                    manifest=manifest,
                )
            )
        found.sort(key=lambda app: app.name.lower())
        self._apps = found
        return found

    def app(self, app_id: str) -> SteamApp | None:
        wanted = str(app_id)
        for app in self.apps():
            if app.app_id == wanted:
                return app
        return None

    def library_folders(self) -> list[Path]:
        """Paths listed in ``libraryfolders.vdf`` (including this library)."""
        vdf = self.steamapps / "libraryfolders.vdf"
        paths: list[Path] = []
        if vdf.is_file():
            try:
                tree = acf.parse_file(vdf)
            except (acf.KeyValuesError, OSError):
                tree = {}
            folders = tree.get("libraryfolders") or tree.get("LibraryFolders") or {}
            if isinstance(folders, dict):
                for key, value in folders.items():
                    if isinstance(value, dict):
                        candidate = value.get("path")
                    elif key.isdigit():
                        candidate = value
                    else:
                        continue
                    if isinstance(candidate, list):
                        candidate = candidate[0] if candidate else None
                    if isinstance(candidate, str) and candidate:
                        paths.append(Path(candidate))
        paths.append(self.root)
        return paths

    # -- discovery -------------------------------------------------------
    @classmethod
    def discover(cls, extra_roots: list[str] | None = None) -> list[SteamLibrary]:
        """All Steam libraries across all detected Steam installations."""
        libraries: list[SteamLibrary] = []
        seen: set[str] = set()

        def add(root: Path) -> None:
            root = root.resolve() if root.exists() else root
            steamapps = root / "steamapps"
            if not steamapps.is_dir():
                nested = root / "steamapps"
                if not nested.is_dir():
                    return
            key = str(steamapps).lower()
            if key in seen:
                return
            seen.add(key)
            library = cls(root=root, steamapps=steamapps)
            if root != (steamapps.parent):
                library.library_path = root
            libraries.append(library)

        for root in steam_roots(extra_roots):
            add(root)
            primary = cls(root=root, steamapps=root / "steamapps")
            for folder in primary.library_folders():
                if folder.is_dir():
                    add(folder)
        return libraries


# ---------------------------------------------------------------------------
# convenience
# ---------------------------------------------------------------------------
def find_installations(
    app_id: str,
    *,
    platform: str | None = None,
    extra_roots: list[str] | None = None,
) -> list[SteamApp]:
    """Every installed copy of ``app_id``, Steam's own installation first."""
    results: list[SteamApp] = []
    for library in SteamLibrary.discover(extra_roots):
        app = library.app(app_id)
        if app and app.path.is_dir():
            results.append(app)
    results.sort(key=lambda app: (not app.is_installed, str(app.path).lower()))
    return results


def find_app(app_id: str, extra_roots: list[str] | None = None) -> SteamApp | None:
    matches = find_installations(app_id, extra_roots=extra_roots)
    return matches[0] if matches else None
