"""steamverify -- verify Steam game installs against bundled official hash manifests.

The tool answers two questions:

1. Are the official game files intact, or has something modified them?
2. Which files are present that are *not* part of any official build
   (mods, trainers, repack leftovers, crash dumps, save games)?

It works entirely offline using manifests generated from Steam's own depot
manifests, so a scan is reproducible and needs no network access.

Library usage::

    from steamverify import registry, scanner

    game = registry.Registry.load().get("witcher3")
    manifest = game.load()
    result = scanner.scan("/path/to/game", manifest)
    print(result.counts())
"""

from __future__ import annotations

__all__ = [
    "__version__",
    "manifest",
    "acf",
    "depot",
    "registry",
    "scanner",
    "reporter",
    "steam",
    "cli",
]

__version__ = "0.1.0"
