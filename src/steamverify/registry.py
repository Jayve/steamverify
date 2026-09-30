"""Registry of bundled game manifests.

Each supported game gets a directory under ``manifests/<slug>/`` holding one
or more ``.svm`` manifests plus a ``manifest.json`` describing them.  Adding a
game is a data-only change: generate a manifest and drop it in.

The registry also records a replay command, so anyone can regenerate the exact
same manifest from their own Steam client and confirm the bundled hashes were
not tampered with.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import manifest as manifest_mod

__all__ = ["ManifestRecord", "GameEntry", "Registry", "RegistryError", "default_manifest_dir"]

REGISTRY_FILENAME = "registry.json"
_DEFAULT_PLATFORM = "windows"


class RegistryError(Exception):
    """The registry file or a referenced manifest is missing or invalid."""


def default_manifest_dir() -> Path:
    """Locate ``manifests/`` both from a source checkout and an installed wheel."""
    override = _env_override()
    if override:
        return override

    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "manifests"
        if (candidate / REGISTRY_FILENAME).is_file():
            return candidate
    packaged = here.parent / "data" / "manifests"
    if (packaged / REGISTRY_FILENAME).is_file():
        return packaged
    return here.parents[2] / "manifests"


def _env_override() -> Path | None:
    import os

    value = os.environ.get("STEAMVERIFY_MANIFESTS")
    return Path(value) if value else None


@dataclass(frozen=True)
class ManifestRecord:
    """One manifest build of a game."""

    file: str
    sha256: str = ""
    build_id: str = ""
    app_id: str = ""
    platform: str = ""
    file_count: int = 0
    chunk_count: int = 0
    total_bytes: int = 0
    generated: str = ""
    note: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> ManifestRecord:
        return cls(
            file=str(data["file"]),
            sha256=str(data.get("sha256", "")),
            build_id=str(data.get("build_id", "")),
            app_id=str(data.get("app_id", "")),
            platform=str(data.get("platform", "")),
            file_count=int(data.get("file_count", 0)),
            chunk_count=int(data.get("chunk_count", 0)),
            total_bytes=int(data.get("total_bytes", 0)),
            generated=str(data.get("generated", "")),
            note=str(data.get("note", "")),
        )

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "sha256": self.sha256,
            "build_id": self.build_id,
            "app_id": self.app_id,
            "platform": self.platform,
            "file_count": self.file_count,
            "chunk_count": self.chunk_count,
            "total_bytes": self.total_bytes,
            "generated": self.generated,
            "note": self.note,
        }

    @property
    def size_text(self) -> str:
        return f"{self.total_bytes / 1024 ** 3:.2f} GiB"


@dataclass
class GameEntry:
    """A game that ships with one or more manifests."""

    slug: str
    name: str
    directory: str
    app_id: str = ""
    platform: str = _DEFAULT_PLATFORM
    aliases: list[str] = field(default_factory=list)
    default_dirs: dict[str, str] = field(default_factory=dict)
    store_url: str = ""
    vendor: str = ""
    replay: str = ""
    current_build: str = ""
    manifests: list[ManifestRecord] = field(default_factory=list)
    registry_path: Path | None = None

    @classmethod
    def from_dict(cls, slug: str, data: dict, registry_path: Path | None = None) -> GameEntry:
        return cls(
            slug=str(data.get("slug", slug)),
            name=str(data.get("name", slug)),
            directory=str(data.get("directory", slug)),
            app_id=str(data.get("app_id", "")),
            platform=str(data.get("platform", _DEFAULT_PLATFORM)),
            aliases=[str(a) for a in data.get("aliases", [])],
            default_dirs={str(k): str(v) for k, v in data.get("default_dirs", {}).items()},
            store_url=str(data.get("store_url", "")),
            vendor=str(data.get("vendor", "")),
            replay=str(data.get("replay", "")),
            current_build=str(data.get("current_build", "")),
            manifests=[ManifestRecord.from_dict(m) for m in data.get("manifests", [])],
            registry_path=registry_path,
        )

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "name": self.name,
            "directory": self.directory,
            "app_id": self.app_id,
            "platform": self.platform,
            "aliases": self.aliases,
            "default_dirs": self.default_dirs,
            "store_url": self.store_url,
            "vendor": self.vendor,
            "replay": self.replay,
            "current_build": self.current_build,
            "manifests": [m.to_dict() for m in self.manifests],
        }

    # -- manifest resolution --------------------------------------------
    @property
    def directory_path(self) -> Path:
        if self.registry_path is None:  # pragma: no cover - defensive
            raise RegistryError("game entry has no registry path")
        return self.registry_path.parent / self.directory

    def manifest_path(self, build: str | None = None) -> Path:
        """Path to the requested manifest (default: the current build)."""
        record = self.record(build)
        return self.directory_path / record.file

    def record(self, build: str | None = None) -> ManifestRecord:
        if not self.manifests:
            raise RegistryError(f"game '{self.slug}' has no manifests")
        if build in (None, "", "current", "latest"):
            for record in self.manifests:
                if record.build_id and record.build_id == self.current_build:
                    return record
            return self.manifests[0]
        for record in self.manifests:
            if record.build_id == build or record.file == build or record.file.startswith(build):
                return record
        available = ", ".join(r.build_id or r.file for r in self.manifests)
        raise RegistryError(
            f"game '{self.slug}' has no manifest for build '{build}' (available: {available})"
        )

    def load(self, build: str | None = None, *, verify: bool = True) -> manifest_mod.Manifest:
        """Load the manifest, checking it against the recorded SHA-256."""
        record = self.record(build)
        path = self.directory_path / record.file
        return manifest_mod.load(path, verify_digest=record.sha256 if verify else None)

    def default_directory(self, platform: str | None = None) -> str:
        """Conventional install directory for a platform, if known."""
        platform = platform or _current_platform()
        if platform in self.default_dirs:
            return self.default_dirs[platform]
        return self.default_dirs.get(_DEFAULT_PLATFORM, "")


def _current_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


@dataclass
class Registry:
    """All bundled games."""

    path: Path
    version: int = 1
    generated: str = ""
    games: list[GameEntry] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path | None = None) -> Registry:
        path = Path(path) if path else default_manifest_dir() / REGISTRY_FILENAME
        if not path.is_file():
            raise RegistryError(
                f"registry not found: {path}\n"
                "set STEAMVERIFY_MANIFESTS to a manifests directory, or run "
                "from a source checkout"
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RegistryError(f"registry is not valid JSON: {exc}") from exc

        games = [
            GameEntry.from_dict(slug, entry, path)
            for slug, entry in sorted((data.get("games") or {}).items())
        ]
        if not games:
            raise RegistryError(f"registry lists no games: {path}")
        return cls(
            path=path,
            version=int(data.get("version", 1)),
            generated=str(data.get("generated", "")),
            games=games,
        )

    def __iter__(self):
        return iter(self.games)

    def __len__(self) -> int:
        return len(self.games)

    def get(self, slug: str) -> GameEntry:
        """Resolve a slug, alias, app id, or case-insensitive name."""
        wanted = str(slug).strip().lower()
        for game in self.games:
            if wanted in (game.slug.lower(), game.app_id.lower(), game.name.lower()):
                return game
            if any(wanted == alias.lower() for alias in game.aliases):
                return game
        known = ", ".join(game.slug for game in self.games)
        raise RegistryError(f"unknown game '{slug}'; bundled games: {known}")

    def find(self, slug: str) -> GameEntry | None:
        try:
            return self.get(slug)
        except RegistryError:
            return None

    def verify_all(self) -> list[str]:
        """Check every manifest against its recorded digest.

        Returns a list of problem descriptions (empty means all good).
        """
        problems: list[str] = []
        for game in self.games:
            for record in game.manifests:
                path = game.directory_path / record.file
                if not path.is_file():
                    problems.append(f"{game.slug}: missing manifest file {record.file}")
                    continue
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if record.sha256 and digest.lower() != record.sha256.lower():
                    problems.append(
                        f"{game.slug}/{record.file}: SHA-256 mismatch "
                        f"(registry {record.sha256[:16]}..., actual {digest[:16]}...)"
                    )
        return problems
