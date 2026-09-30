"""Regenerate docs/examples/ from a real install. Not shipped with the package."""

import re
import subprocess
import sys
from pathlib import Path

# This script lives in tools/, so the repository root is one level up. Getting
# this wrong silently writes the samples into tools/docs/examples/ and leaves
# the committed ones stale -- hence the guard in main().
ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "docs" / "examples"
PY = sys.executable

REAL_ROOT = re.compile(r"[A-Za-z]:\\[^\s\]]*The Witcher 3")
VERIFY = ["verify", "--game", "witcher3", "--quiet", "--max-list", "5"]
# Ignore the third-party bundles plus the markers the game rewrites while
# running (metadata.store.stamp and friends), so the samples stay reproducible
# no matter whether the game was launched recently.
IGNORES: list[str] = []
for pattern in ("tools/*", "tools", "_steam_audit/*", "*.md", "*.stamp", "metadata.store"):
    IGNORES += ["--ignore", pattern]


def run(args: list[str], *, lang: str | None = None) -> str:
    argv = [PY, "-m", "steamverify"]
    if lang:
        argv += ["--lang", lang]
    proc = subprocess.run([*argv, *args], cwd=ROOT, capture_output=True)
    # A child process on Windows emits CRLF; the samples must be LF so that the
    # READMEs, which are checked in as LF, match them line for line.
    return proc.stdout.decode("utf-8").replace("\r\n", "\n")


def scrub(text: str) -> str:
    text = REAL_ROOT.sub("<GAME_DIR>", text)
    text = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", "<UTC>", text)
    text = re.sub(r"\(\d+\.\d+s\)", "(<elapsed>)", text)
    return text.replace("\r\n", "\n").replace("\r", "\n")


HEADER_EN = (
    "# Generated from a real 69 GiB install, then scrubbed: the directory is\n"
    "# replaced with <GAME_DIR>, timestamps with <UTC>, durations with <elapsed>.\n"
    "# Everything else is verbatim tool output; ANSI colour is absent because the\n"
    "# output was captured from a pipe rather than a terminal.\n"
)
HEADER_ZH = (
    "# 取自真实的 69 GiB 安装，仅做脱敏：目录替换为 <GAME_DIR>，时间替换为 <UTC>，\n"
    "# 耗时替换为 <elapsed>。其余内容均为工具原样输出（因写入管道而非终端，故无颜色）。\n"
)

SAMPLES = {
    "report-full.txt": (
        "# steamverify verify --game witcher3 --max-list 5\n#\n" + HEADER_EN,
        lambda: scrub(run(VERIFY)),
    ),
    "report-full.zh-CN.txt": (
        "# steamverify --lang zh verify --game witcher3 --max-list 5\n#\n" + HEADER_ZH,
        lambda: scrub(run(VERIFY, lang="zh")),
    ),
    "report-clean.txt": (
        "# steamverify verify --game witcher3 \\\n"
        "#     --ignore 'tools/*' --ignore '_steam_audit/*' --ignore '*.md' \\\n"
        "#     --ignore '*.stamp' --ignore 'metadata.store'\n#\n"
        "# Official data intact, with the known third-party files and the\n"
        "# markers the game rewrites at runtime ignored.\n#\n" + HEADER_EN,
        lambda: scrub(run([*VERIFY, *IGNORES])),
    ),
    "report-clean.zh-CN.txt": (
        "# steamverify --lang zh verify --game witcher3 \\\n"
        "#     --ignore 'tools/*' --ignore '_steam_audit/*' --ignore '*.md' \\\n"
        "#     --ignore '*.stamp' --ignore 'metadata.store'\n#\n"
        "# 官方数据完好，且已知的第三方文件与游戏运行时改写的标记文件均被 --ignore 排除。\n#\n"
        + HEADER_ZH,
        lambda: scrub(run([*VERIFY, *IGNORES], lang="zh")),
    ),
}

def validate(name: str, body: str) -> None:
    """Refuse to write a sample that is empty, mangled, or in the wrong language.

    A truncated capture or a mangled encoding would otherwise be committed and
    then quoted in the README, which is worse than having no sample at all.
    """
    if len(body) < 400:
        raise SystemExit(f"{name}: output looks truncated ({len(body)} chars)")
    if "\ufffd" in body:
        raise SystemExit(f"{name}: output contains replacement characters (encoding loss)")
    if "steamverify  v" not in body:
        raise SystemExit(f"{name}: missing the version banner; not real tool output")
    if "Summary" not in body and "\u6c47\u603b" not in body:
        raise SystemExit(f"{name}: missing the summary section")
    if name.endswith("zh-CN.txt"):
        if "\u6c47\u603b" not in body or "Summary" in body:
            raise SystemExit(f"{name}: expected Chinese output but got something else")
    elif "\u6c47\u603b" in body:
        raise SystemExit(f"{name}: expected English output but got Chinese")


def path_relative(target: Path) -> str:
    """Path shown in progress output, relative to the repository root."""
    try:
        return str(target.relative_to(ROOT))
    except ValueError:  # pragma: no cover - defensive
        return str(target)


def write_sample(target: Path, text: str) -> None:
    """Write with LF endings on every platform.

    ``Path.write_text(newline=...)`` is not honoured consistently across the
    Python versions this project supports, and on Windows a default text-mode
    write turns ``\\n`` into ``\\r\\n`` -- which would break the line-for-line
    comparison against the LF READMEs.
    """
    with target.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


for name, (header, produce) in SAMPLES.items():
    body = produce()
    validate(name, body)
    target = EXAMPLES / name
    EXAMPLES.mkdir(parents=True, exist_ok=True)
    if target.parent.resolve() != EXAMPLES.resolve():  # pragma: no cover - defensive
        raise SystemExit(f"refusing to write outside {EXAMPLES}: {target}")
    write_sample(target, header + "\n" + body)
    data = target.read_bytes()
    if b"\r" in data:
        raise SystemExit(f"{name}: wrote CR characters; samples must be LF-only")
    print(f"wrote {path_relative(target)} "
          f"({len(data)} bytes, {data.count(10)} lines)")
