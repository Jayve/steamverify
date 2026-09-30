#!/usr/bin/env python3
"""Verify that the sample output quoted in the READMEs is genuine.

Docs drift silently: a change to the report layout can leave the README
advertising output the tool no longer produces.  This script parses the README
code blocks and requires each one to be a **verbatim contiguous excerpt** of the
matching file in ``docs/examples/`` -- which in turn is generated from a real
install by ``tools/generate_examples.py``.

Run from the repository root::

    python tools/check_docs.py

Exit code 0 when every quoted sample matches, 1 otherwise.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: ``(label, README, sample, heading, skip)`` where ``skip`` counts fenced
#: blocks to step over after the heading before taking the one to compare.
#: The "clean run" sections put a bash command fence before the output fence.
#: The final entry covers the example at the top of the English README.
CASES = [
    ("README.md / report-full", "README.md",
     "docs/examples/report-full.txt", "### A normal run", 0),
    ("docs/README.zh-CN.md / report-full.zh-CN", "docs/README.zh-CN.md",
     "docs/examples/report-full.zh-CN.txt", "### 场景一", 0),
    ("README.md / report-clean", "README.md",
     "docs/examples/report-clean.txt", "### A clean run", 1),
    ("docs/README.zh-CN.md / report-clean.zh-CN", "docs/README.zh-CN.md",
     "docs/examples/report-clean.zh-CN.txt", "### 场景二", 1),
    ("README.md / top example", "README.md",
     "docs/examples/report-full.txt", "# steamverify", 0),
]


def blocks(path: Path) -> list[str]:
    """Fenced code blocks, tolerating a fence that ends the file."""
    return re.findall(r"```[^\n]*\n(.*?)(?:\n```|\Z)", path.read_text(encoding="utf-8"), re.S)


def sample_body(path: Path) -> list[str]:
    """Sample lines, with the leading '# ' commentary stripped."""
    raw = path.read_text(encoding="utf-8").splitlines()
    lines = [line for line in raw if not line.startswith("#")]
    return [line.rstrip() for line in "\n".join(lines).strip("\n").splitlines()]


def quote_lines(block: str) -> list[str]:
    """Comparable lines from a quoted block.

    Shell prompts are dropped: the samples are captured output and never
    contain one, while the READMEs show the command that produced them.  The
    prompt style varies (``$ cmd`` or ``$`` alone on a line).
    """
    lines = [line.rstrip() for line in block.strip("\n").splitlines()]
    cleaned: list[str] = []
    for line in lines:
        if line.startswith("$ "):
            continue                      # "$ command" -- not output
        if line.strip() == "$":
            continue                      # bare prompt line -- not output
        cleaned.append(line)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    return cleaned


def find(quote: list[str], sample: list[str]) -> int | None:
    """Index where ``quote`` appears as a contiguous run in ``sample``."""
    if not quote or len(quote) > len(sample):
        return None
    for start in range(len(sample) - len(quote) + 1):
        if sample[start:start + len(quote)] == quote:
            return start
    return None


def find_blocks(text: str) -> list[tuple[int, int, str]]:
    """Every fenced block as ``(start_offset, end_offset, content)``."""
    found: list[tuple[int, int, str]] = []
    index = 0
    while True:
        open_at = text.find("\n```", index)
        if open_at < 0:
            break
        body_at = text.find("\n", open_at + 1) + 1
        close_at = text.find("\n```", body_at)
        if close_at < 0:
            break
        found.append((open_at, close_at, text[body_at:close_at]))
        index = close_at + 1
    return found


def block_after_heading(text: str, heading: str, skip: int = 0) -> str | None:
    """A fenced block whose opening fence follows ``heading``.

    ``skip`` steps over earlier blocks in the same section (for example a bash
    command fence that precedes the output fence).  Composing on
    :func:`find_blocks` matters: searching for a closing fence from the heading
    reaches *inside* a block that started before it, which silently compares
    the wrong text.
    """
    at = text.find(heading)
    if at < 0:
        return None
    following = [content for start, _end, content in find_blocks(text) if start > at]
    if skip >= len(following):
        return None
    return following[skip]


def check(label: str, readme_name: str, sample_name: str, heading: str,
          skip: int = 0) -> bool:
    readme = ROOT / readme_name
    sample_path = ROOT / sample_name
    if not readme.is_file():
        print(f"  FAIL {label}: missing {readme_name}")
        return False
    if not sample_path.is_file():
        print(f"  FAIL {label}: missing {sample_name} "
              f"(run tools/generate_examples.py)")
        return False

    sample = sample_body(sample_path)
    block = block_after_heading(readme.read_text(encoding="utf-8"), heading, skip)
    if block is None:
        print(f"  FAIL {label}: no code block found under {heading!r}")
        return False

    quote = quote_lines(block)
    start = find(quote, sample)
    if start is not None:
        print(f"  OK   {label}: {len(quote)} lines verbatim (sample line {start + 1})")
        return True

    # report the longest matching prefix so the failure is actionable
    best, at = 0, 0
    for offset in range(len(sample)):
        matched = 0
        while (offset + matched < len(sample) and matched < len(quote)
               and sample[offset + matched] == quote[matched]):
            matched += 1
        if matched > best:
            best, at = matched, offset
    print(f"  FAIL {label}: not a verbatim excerpt of {sample_name}")
    print(f"       matched {best} line(s), then:")
    print(f"         quoted: {quote[best] if best < len(quote) else '<end>'!r}")
    print(f"         sample: {sample[at + best] if at + best < len(sample) else '<end>'!r}")
    print("       regenerate with: python tools/generate_examples.py")
    return False


def main() -> int:
    print(f"checking {len(CASES)} quoted sample(s) against docs/examples/")
    ok = True
    for label, readme_name, sample_name, heading, skip in CASES:
        ok &= check(label, readme_name, sample_name, heading, skip)
    print()
    print("all README samples verified verbatim" if ok
          else "docs are out of sync with the generated examples")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
