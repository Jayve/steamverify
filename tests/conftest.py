"""Shared pytest fixtures: synthetic manifests and game trees.

Nothing here needs a real game install, so the suite runs anywhere.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from steamverify import manifest as manifest_mod  # noqa: E402
from steamverify.manifest import Chunk, FileEntry  # noqa: E402

CHUNK = 64  # small chunk size keeps fixtures readable


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def chunked_entry(path: str, data: bytes, chunk_size: int = CHUNK) -> FileEntry:
    """Build an entry whose chunks tile ``data`` exactly (SteamPipe style)."""
    chunks = []
    for offset in range(0, len(data), chunk_size):
        piece = data[offset:offset + chunk_size]
        chunks.append(Chunk(offset=offset, length=len(piece), sha1=sha1(piece)))
    return FileEntry(path=path, size=len(data), sha1=sha1(data), chunks=tuple(chunks))


def plain_entry(path: str, data: bytes) -> FileEntry:
    return FileEntry(path=path, size=len(data), sha1=sha1(data))


HEADER = {
    "slug": "fixture",
    "game": "Fixture Game",
    "app_id": "1",
    "build_id": "42",
    "platform": "windows",
    "depots": ["100"],
    "source": "unit test",
}


@pytest.fixture
def game_tree(tmp_path: Path):
    """A tiny 'game' directory plus a matching manifest.

    Layout::

        game/plain.txt          content
        game/archive.pak        chunked content
        game/sub/nested.bin     content
    """
    root = tmp_path / "game"
    (root / "sub").mkdir(parents=True)

    plain = b"hello world\n" * 4
    archive = bytes(range(256)) * 3
    nested = b"nested!"

    (root / "plain.txt").write_bytes(plain)
    (root / "archive.pak").write_bytes(archive)
    (root / "sub" / "nested.bin").write_bytes(nested)

    entries = [
        plain_entry("plain.txt", plain),
        chunked_entry("archive.pak", archive),
        plain_entry("sub/nested.bin", nested),
    ]
    manifest = manifest_mod.Manifest(header=dict(HEADER), files=entries)
    return root, manifest


@pytest.fixture
def manifest_file(tmp_path: Path):
    """A manifest written to disk, with its digest."""

    def _write(entries, header: dict | None = None, name: str = "fixture-42.svm"):
        path = tmp_path / name
        manifest_mod.write(path, header or dict(HEADER), entries)
        loaded = manifest_mod.load(path)
        return loaded

    return _write


@pytest.fixture
def keyvalues_acf(tmp_path: Path):
    """A realistic appmanifest_*.acf for parser tests."""
    path = tmp_path / "appmanifest_292030.acf"
    path.write_text(
        '"AppState"\n'
        "{\n"
        '\t"appid"\t\t"292030"\n'
        '\t"name"\t\t"The Witcher 3: Wild Hunt"\n'
        '\t"StateFlags"\t\t"4"\n'
        '\t"installdir"\t\t"The Witcher 3"\n'
        '\t"buildid"\t\t"25575366"\n'
        '\t"SizeOnDisk"\t\t"74290293403"\n'
        '\t"LastUpdated"\t\t"1790778455"\n'
        '\t"InstalledDepots"\n'
        "\t{\n"
        '\t\t"292031"\n'
        "\t\t{\n"
        '\t\t\t"manifest"\t\t"2922676153265187497"\n'
        '\t\t\t"size"\t\t"58759089902"\n'
        "\t\t}\n"
        '\t\t"292042"\n'
        "\t\t{\n"
        '\t\t\t"manifest"\t\t"2032392750977723237"\n'
        '\t\t\t"size"\t\t"90832336"\n'
        "\t\t}\n"
        '\t\t"378649"\n'
        "\t\t{\n"
        '\t\t\t"manifest"\t\t"3257423189031698049"\n'
        '\t\t\t"size"\t\t"0"\n'
        '\t\t\t"dlcappid"\t\t"378649"\n'
        "\t\t}\n"
        "\t}\n"
        '\t"MountedConfig"\n'
        "\t{\n"
        '\t\t"language"\t\t"schinese"\n'
        "\t}\n"
        "}\n",
        encoding="utf-8",
    )
    return path
