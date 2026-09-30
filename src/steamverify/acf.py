"""Parser for Valve's KeyValues text format, used by ``appmanifest_<id>.acf``.

The format is a nested ``key value`` tree::

    "AppState"
    {
        "appid"      "292030"
        "InstalledDepots"
        {
            "292031"
            {
                "manifest"  "2922676153265187497"
                "size"      "58759089902"
            }
        }
    }

Only the parts this tool needs are modelled, but the parser is complete
enough for any ACF/VDF file (keys may repeat, blocks may nest arbitrarily,
``//`` comments and C-style escapes are honoured).
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["KeyValuesError", "parse", "parse_file", "AppManifest"]


class KeyValuesError(ValueError):
    """The input is not valid KeyValues text."""


_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "\\": "\\",
    '"': '"',
}


def _tokenize(text: str):
    """Yield (kind, value) where kind is one of ``str``, ``{`` or ``}``."""
    i = 0
    n = len(text)
    while i < n:
        char = text[i]
        if char in " \t\r\n":
            i += 1
            continue
        if char == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if char == "{":
            yield "{", "{"
            i += 1
            continue
        if char == "}":
            yield "}", "}"
            i += 1
            continue
        if char == '"':
            i += 1
            out = []
            while True:
                if i >= n:
                    raise KeyValuesError("unterminated quoted string")
                char = text[i]
                if char == "\\":
                    i += 1
                    if i >= n:
                        raise KeyValuesError("dangling escape at end of input")
                    out.append(_ESCAPES.get(text[i], text[i]))
                    i += 1
                    continue
                if char == '"':
                    i += 1
                    break
                out.append(char)
                i += 1
            yield "str", "".join(out)
            continue
        # bare token: read to whitespace or a brace
        start = i
        while i < n and text[i] not in ' \t\r\n"{}':
            i += 1
        if i == start:  # pragma: no cover - defensive
            raise KeyValuesError(f"unexpected character {char!r} at offset {i}")
        yield "str", text[start:i]


def parse(text: str) -> dict:
    """Parse KeyValues text into a nested dict.

    Repeated keys collapse into a list, so callers should use the helpers on
    :class:`AppManifest` rather than indexing blindly.
    """
    root: dict = {}
    stack: list[dict] = [root]
    pending_key: str | None = None

    for kind, value in _tokenize(text):
        if kind == "{":
            if pending_key is None:
                raise KeyValuesError("block without a key")
            child: dict = {}
            _insert(stack[-1], pending_key, child)
            stack.append(child)
            pending_key = None
        elif kind == "}":
            if len(stack) == 1:
                raise KeyValuesError("unbalanced closing brace")
            stack.pop()
            pending_key = None
        else:
            if pending_key is None:
                pending_key = value
            else:
                _insert(stack[-1], pending_key, value)
                pending_key = None

    if len(stack) != 1:
        raise KeyValuesError("unbalanced opening brace")
    if pending_key is not None:
        raise KeyValuesError(f"key {pending_key!r} has no value")
    return root


def _insert(target: dict, key: str, value) -> None:
    if key not in target:
        target[key] = value
        return
    existing = target[key]
    if isinstance(existing, list):
        existing.append(value)
    else:
        target[key] = [existing, value]


def parse_file(path: str | Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise KeyValuesError(f"file not found: {path}")
    return parse(path.read_text(encoding="utf-8", errors="replace"))


def _first(value):
    """Unwrap a possibly-repeated key back to a single value."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


class AppManifest:
    """Convenience view over a parsed ``appmanifest_<appid>.acf``."""

    def __init__(self, tree: dict, path: Path | None = None) -> None:
        self.path = path
        state = tree.get("AppState")
        if not isinstance(state, dict):
            raise KeyValuesError("ACF file has no top-level 'AppState' block")
        self.state = state

    @classmethod
    def load(cls, path: str | Path) -> AppManifest:
        path = Path(path)
        return cls(parse_file(path), path)

    # -- scalar fields ---------------------------------------------------
    def _get(self, key: str, default: str = "") -> str:
        value = _first(self.state.get(key))
        return default if value is None else str(value)

    @property
    def app_id(self) -> str:
        return self._get("appid")

    @property
    def name(self) -> str:
        return self._get("name")

    @property
    def install_dir(self) -> str:
        return self._get("installdir")

    @property
    def build_id(self) -> str:
        return self._get("buildid")

    @property
    def target_build_id(self) -> str:
        return self._get("TargetBuildID") or self._get("buildid")

    @property
    def size_on_disk(self) -> int:
        try:
            return int(self._get("SizeOnDisk", "0"))
        except ValueError:
            return 0

    @property
    def state_flags(self) -> int:
        try:
            return int(self._get("StateFlags", "0"))
        except ValueError:
            return 0

    @property
    def last_updated(self) -> int:
        try:
            return int(self._get("LastUpdated", "0"))
        except ValueError:
            return 0

    @property
    def language(self) -> str:
        mounted = self.state.get("MountedConfig")
        if isinstance(mounted, dict):
            value = _first(mounted.get("language"))
            if value:
                return str(value)
        user = self.state.get("UserConfig")
        if isinstance(user, dict):
            value = _first(user.get("language"))
            if value:
                return str(value)
        return ""

    @property
    def is_fully_installed(self) -> bool:
        """StateFlags bit 4 (value 4) means 'fully installed'."""
        return bool(self.state_flags & 4)

    # -- depots ----------------------------------------------------------
    @property
    def installed_depots(self) -> list[str]:
        """Depot ids in installation order, as listed in the ACF."""
        depots = self.state.get("InstalledDepots")
        if not isinstance(depots, dict):
            return []
        return list(depots.keys())

    def depot(self, depot_id: str) -> dict | None:
        depots = self.state.get("InstalledDepots")
        if not isinstance(depots, dict):
            return None
        entry = _first(depots.get(str(depot_id)))
        return entry if isinstance(entry, dict) else None

    def depot_manifest_gid(self, depot_id: str) -> str:
        entry = self.depot(depot_id)
        if not entry:
            return ""
        return str(_first(entry.get("manifest")) or "")

    def depot_size(self, depot_id: str) -> int:
        entry = self.depot(depot_id)
        if not entry:
            return 0
        try:
            return int(_first(entry.get("size")) or 0)
        except (TypeError, ValueError):
            return 0

    def depot_dlc_app_id(self, depot_id: str) -> str:
        entry = self.depot(depot_id)
        if not entry:
            return ""
        return str(_first(entry.get("dlcappid")) or "")

    def describe(self) -> str:
        bits = [self.name or f"app {self.app_id}", f"build {self.build_id or '?'}"]
        if self.language:
            bits.append(f"lang {self.language}")
        if self.size_on_disk:
            bits.append(f"{self.size_on_disk / 1024 ** 3:.2f} GiB on disk")
        return " | ".join(bits)
