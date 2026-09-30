"""Runtime internationalisation (English / 简体中文).

Design
------
* **English is the reference.** Every message is written in English first and
  lives in :data:`CATALOG`; a missing translation falls back to English rather
  than showing a raw key. A test asserts the two catalogs have identical key
  sets, so drift is caught in CI.
* **No global state.** ``get_translator()`` returns a translator; call
  ``set_language()`` once at start-up to choose the default for calls that do
  not pass one explicitly.
* **Machine output is never translated.** JSON keys and CSV column headers stay
  English so scripts and downstream tooling are unaffected by ``--lang``. Only
  prose shown to a human is localised.
* **Language is chosen in this order:** ``--lang`` → ``STEAMVERIFY_LANG`` →
  ``LC_ALL`` / ``LC_MESSAGES`` / ``LANG`` → English.

Adding a language means adding one dict to :data:`CATALOG` and one alias.
"""

from __future__ import annotations

import os
from typing import Any

__all__ = [
    "CATALOG",
    "LANGUAGES",
    "Translator",
    "get_translator",
    "set_language",
    "resolve_language",
    "available_languages",
    "DEFAULT_LANGUAGE",
]

DEFAULT_LANGUAGE = "en"

#: Canonical code -> (endonym, English name)
LANGUAGES: dict[str, tuple[str, str]] = {
    "en": ("English", "English"),
    "zh": ("简体中文", "Chinese (Simplified)"),
}

#: Aliases accepted from the command line and environment.
LANGUAGE_ALIASES: dict[str, str] = {
    "en": "en",
    "en-us": "en",
    "en-gb": "en",
    "english": "en",
    "zh": "zh",
    "zh-cn": "zh",
    "zh-hans": "zh",
    "zh-sg": "zh",
    "chs": "zh",
    "cn": "zh",
    "chinese": "zh",
    "simplified-chinese": "zh",
    "中文": "zh",
    "简体中文": "zh",
    "chinese (simplified)": "zh",
}


CATALOG: dict[str, dict[str, str]] = {
    # -----------------------------------------------------------------
    # English (reference catalog)
    # -----------------------------------------------------------------
    "en": {
        "lang.name": "English",
        # ---- CLI: commands and options ----
        "cli.description": (
            "Verify a Steam game installation against a bundled official hash "
            "manifest, and list files that are not part of the official build."
        ),
        "cli.epilog": (
            "exit codes: 0 clean, 1 extra files present, "
            "2 modified or missing official files, 3 errors"
        ),
        "cli.option.lang": "output language (default: auto-detect, else English)",
        "cli.option.registry": "path to registry.json (default: bundled manifests)",
        "cli.option.game": "bundled game slug, alias, or app id",
        "cli.option.manifest": "path to a .svm manifest file",
        "cli.option.build": "manifest build id (default: current)",
        "cli.option.json": "machine-readable output",
        "cli.option.dir": "game directory (default: auto-detect)",
        "cli.option.compact": "single-line JSON",
        "cli.option.csv": "also write a per-file CSV",
        "cli.option.no_hash": "size-only check, much faster but weaker",
        "cli.option.workers": "hashing threads (default: auto)",
        "cli.option.ignore": "skip matching paths (repeatable), e.g. 'mods/*'",
        "cli.option.no_default_ignores": (
            "also report the paths ignored by default "
            "(logs, crash dumps, .steamverify/*)"
        ),
        "cli.option.follow_symlinks": "descend into symlinked directories",
        "cli.option.max_list": "rows shown per section (default 25)",
        "cli.option.verbose": "also list each extra file with its size and time",
        "cli.option.quiet": "no progress bar",
        "cli.option.color": "colourise output",
        "cli.option.fail_on": "relax the exit code for CI (default: any difference)",
        "cli.option.details": "also list the N largest files",
        "cli.option.library": "additional Steam root to inspect",
        "cli.option.default_dir": "conventional install dir per platform (repeatable)",
        "cli.option.alias": "extra CLI aliases (repeatable)",
        "cli.cmd.list": "list bundled games",
        "cli.cmd.info": "show manifest details",
        "cli.cmd.verify": "scan a game directory",
        "cli.cmd.steam": "show local Steam libraries",
        "cli.cmd.doctor": "validate the bundled manifests",
        "cli.cmd.digest": "print manifest digests",
        "cli.help.game": "show this help and exit",
        "cli.help.version": "show the version and exit",
        "cli.need_command": "no command given",
        "cli.error": "error",
        "cli.interrupted": "interrupted",
        "cli.field.game": "game",
        "cli.field.app_id": "app id",
        "cli.field.build": "build",
        "cli.field.platform": "platform",
        "cli.field.source": "source",
        "cli.field.manifest_file": "manifest file",
        "cli.field.manifest_sha": "manifest sha256",
        "cli.field.files": "files",
        "cli.field.chunks": "chunks",
        "cli.field.total_size": "total size",
        "cli.field.depots": "depots",
        "cli.field.generated": "generated",
        "cli.field.regenerate": "regenerate",
        "cli.field.notes": "notes",
        "cli.largest_files": "Largest files:",
        "cli.aliases": "aliases",
        "cli.verify_hint": "Verify a game with:  steamverify verify --game <slug>",
        "cli.current_marker": "* = build used by default",
        "cli.bundled_games": "Bundled games ({count}), registry: {path}",
        "cli.no_steam_libraries": "no Steam libraries found",
        "cli.steam_root": "root",
        "cli.steam_library": "library",
        "cli.steam_depotcache": "depotcache",
        "cli.steam_apps": "apps",
        "cli.yes": "yes",
        "cli.no": "no",
        "cli.registry": "registry",
        "cli.games": "games",
        "cli.manifests_checked": "manifests",
        "cli.manifests_ok": (
            "all bundled manifests are present, intact and well-formed"
        ),
        "cli.manifests_problems": "problems:",
        "cli.ignored_skipped": "skipped by ignore rules",
        "cli.csv_written": "CSV written to {path}",
        "cli.dir_not_exist": "directory does not exist: {path}",
        "cli.specify_game": "specify a game with --game (see `steamverify list`)",
        "cli.build_hint": (
            "note: this manifest is for build {manifest_build}, but the "
            "registry's current build is {current_build}"
        ),
        # ---- discovery ----
        "discover.not_found": (
            "could not find the game directory automatically.\n"
            "Pass --dir \"<path to game folder>\" (the folder containing the "
            "game's exe)."
        ),
        "discover.user": "user supplied (--dir)",
        "discover.guessed": "guessed path",
        # ---- progress ----
        "progress.scanning": "scanning {detail} ...",
        "progress.hashing": "hashing {done} files",
        # ---- report: header ----
        "report.label.game": "game",
        "report.label.app_id": "app id",
        "report.label.build": "build",
        "report.label.manifest": "manifest",
        "report.label.manifest_id": "manifest id",
        "report.label.directory": "directory",
        "report.label.scanned": "scanned",
        "report.manifest_summary": "{files} files, {chunks} chunks, {size}",
        "report.size_only": "size-only (content hashes were NOT checked)",
        # ---- report: status column ----
        "report.status.verified": "verified",
        "report.status.modified": "modified",
        "report.status.stub": "stub",
        "report.status.extra": "extra",
        "report.status.missing": "missing",
        "report.status.ignored": "ignored",
        "report.status.errors": "errors",
        "report.files_column": "{count:>7,} files",
        # ---- report: summary ----
        "report.summary": "Summary",
        "report.row.verified": "files match the official manifest",
        "report.row.modified": "tracked files whose content differs",
        "report.row.stub": "content-free placeholder entries (DLC markers)",
        "report.row.extra": "files not present in any official manifest",
        "report.row.missing": "official files that are absent",
        "report.row.ignored": "skipped by ignore rules",
        "report.row.errors": "files that could not be read",
        "report.verified_total": (
            "{files} files / {size} verified against the official build"
        ),
        "report.extra_total": (
            "{files} extra files / {size} not in the official build"
        ),
        # ---- report: verdict ----
        "report.result.clean": (
            "RESULT: clean -- this install matches the official manifest."
        ),
        "report.result.not_clean": (
            "RESULT: NOT clean -- official game data differs from the manifest."
        ),
        "report.result.stub_only": (
            "RESULT: game data intact; only content-free DLC marker files differ."
        ),
        "report.result.extra_only": (
            "RESULT: official files intact, but extra files are present."
        ),
        # ---- report: sections ----
        "report.section.extra_groups": "Extra files by location",
        "report.section.stubs": "Placeholder entries (not a corruption signal)",
        "report.section.modified": "Modified files",
        "report.section.missing": "Missing files",
        "report.section.extras": "All extra files",
        "report.section.errors": "Errors",
        "report.section.next": "What to do",
        "report.more": "... {count} more",
        "report.more_hint": "... {count} more (use --json or --csv for the full list)",
        "report.extra_path_note": (
            "Paths are relative to the game directory; a trailing / marks a "
            "folder in which every file is extra. Hide the ones you keep on "
            "purpose with --ignore '<path>/*', or add -v for each file's size "
            "and time."
        ),
        "report.stub_note": (
            "These official entries carry an identifier hash but are "
            "legitimately empty on disk."
        ),
        "report.bad_chunks": "first bad chunk offsets: {offsets}",
        "report.exit_code": "Exit code: {code}",
        # ---- report: next steps ----
        "report.next.clean": (
            "Nothing. If Steam still reports problems, verify with Steam itself."
        ),
        "report.next.stub_only": (
            "Nothing to fix -- these placeholders are meant to be empty. The "
            "exit code is still non-zero because the tree is not a byte-exact "
            "match; use --fail-on missing to ignore them in CI."
        ),
        "report.next.steam_verify": (
            "In Steam: right-click the game > Properties > Installed Files"
        ),
        "report.next.steam_verify2": (
            "> 'Verify integrity of game files', then re-run this scan."
        ),
        "report.next.still_differ": (
            "Files that still differ are usually mods that overwrite game data."
        ),
        "report.next.extra_intro": (
            "Extra files are not part of the official build. Mods, trainers,"
        ),
        "report.next.extra_intro2": (
            "save games, crash dumps and repack leftovers show up here."
        ),
        "report.next.extra_outro": (
            "Delete what you do not want; the game itself is unaffected."
        ),
        # ---- scanner ----
        "scan.reason.placeholder": (
            "placeholder file: the official entry carries an identifier hash, "
            "but this file is legitimately empty/stubbed on disk"
        ),
        "scan.reason.size_differs": (
            "size differs (on disk {size} bytes, official {official} bytes)"
        ),
        "scan.reason.content_differs": "content differs",
        "scan.reason.chunks_differ": "{bad} of {total} chunks differ",
        "scan.reason.file_differs": "file content differs",
        "scan.reason.unreadable": "unreadable: {detail}",
        "scan.error.walk": "could not enumerate every file in the game directory",
        "scan.error.hash": "{path}: {detail}",
        "scan.error.interrupted": "scan interrupted by user",
        # ---- errors raised to the user ----
        "err.manifest_not_found": "manifest file not found: {path}",
        "err.manifest_not_svm": "not an .svm manifest: {path}",
        "err.manifest_digest": (
            "manifest SHA-256 does not match the registry entry -- "
            "the bundled manifest may have been modified\n"
            "  expected {expected}\n  actual   {actual}"
        ),
        "err.dir_not_found": "game directory not found: {path}",
        "err.file_not_found": "file not found: {path}",
        "err.unknown_game": "unknown game '{slug}'; bundled games: {known}",
        "err.no_manifests": "game '{slug}' has no manifests",
        "err.no_build": (
            "game '{slug}' has no manifest for build '{build}' (available: {available})"
        ),
    },
    # -----------------------------------------------------------------
    # 简体中文
    # -----------------------------------------------------------------
    "zh": {
        "lang.name": "简体中文",
        # ---- CLI: commands and options ----
        "cli.description": (
            "用内置的官方哈希清单校验 Steam 游戏安装，并列出所有不属于官方版本的文件。"
        ),
        "cli.epilog": (
            "退出码：0 干净，1 存在多余文件，2 官方文件被修改或缺失，3 出错"
        ),
        "cli.option.lang": "输出语言（默认：自动检测，否则英文）",
        "cli.option.registry": "registry.json 路径（默认：内置清单目录）",
        "cli.option.game": "内置游戏的 slug、别名或 app id",
        "cli.option.manifest": ".svm 清单文件路径",
        "cli.option.build": "清单对应的构建号（默认：当前）",
        "cli.option.json": "输出机器可读格式",
        "cli.option.dir": "游戏目录（默认：自动定位）",
        "cli.option.compact": "输出单行 JSON",
        "cli.option.csv": "同时写出逐文件 CSV",
        "cli.option.no_hash": "仅比对大小，速度快很多但强度较低",
        "cli.option.workers": "哈希线程数（默认：自动）",
        "cli.option.ignore": "跳过匹配的路径（可重复），例如 'mods/*'",
        "cli.option.no_default_ignores": (
            "连默认忽略的路径也一并报告（日志、崩溃转储、.steamverify/*）"
        ),
        "cli.option.follow_symlinks": "进入符号链接目录",
        "cli.option.max_list": "每节显示的行数（默认 25）",
        "cli.option.verbose": "同时列出每个多余文件的大小与时间",
        "cli.option.quiet": "不显示进度条",
        "cli.option.color": "彩色输出",
        "cli.option.fail_on": "为 CI 放宽退出码判定（默认：任何差异都算）",
        "cli.option.details": "同时列出最大的 N 个文件",
        "cli.option.library": "额外检查的 Steam 根目录",
        "cli.option.default_dir": "各平台的常规安装目录（可重复）",
        "cli.option.alias": "额外的命令行别名（可重复）",
        "cli.cmd.list": "列出内置游戏",
        "cli.cmd.info": "显示清单详情",
        "cli.cmd.verify": "扫描游戏目录",
        "cli.cmd.steam": "显示本机 Steam 库",
        "cli.cmd.doctor": "校验内置清单",
        "cli.cmd.digest": "打印清单摘要",
        "cli.help.game": "显示帮助并退出",
        "cli.help.version": "显示版本并退出",
        "cli.need_command": "未指定子命令",
        "cli.error": "错误",
        "cli.interrupted": "已中断",
        "cli.field.game": "游戏",
        "cli.field.app_id": "app id",
        "cli.field.build": "构建版本",
        "cli.field.platform": "平台",
        "cli.field.source": "来源",
        "cli.field.manifest_file": "清单文件",
        "cli.field.manifest_sha": "清单 sha256",
        "cli.field.files": "文件数",
        "cli.field.chunks": "分块数",
        "cli.field.total_size": "总大小",
        "cli.field.depots": "depot",
        "cli.field.generated": "生成时间",
        "cli.field.regenerate": "重新生成",
        "cli.field.notes": "备注",
        "cli.largest_files": "最大的文件：",
        "cli.aliases": "别名",
        "cli.verify_hint": "校验游戏：steamverify verify --game <slug>",
        "cli.current_marker": "* = 默认使用的构建",
        "cli.bundled_games": "内置游戏（{count} 个），注册表：{path}",
        "cli.no_steam_libraries": "未找到 Steam 库",
        "cli.steam_root": "根目录",
        "cli.steam_library": "库",
        "cli.steam_depotcache": "depotcache",
        "cli.steam_apps": "游戏数",
        "cli.yes": "是",
        "cli.no": "否",
        "cli.registry": "注册表",
        "cli.games": "游戏",
        "cli.manifests_checked": "已检查清单",
        "cli.manifests_ok": "全部内置清单均存在、完整且格式正确",
        "cli.manifests_problems": "问题：",
        "cli.ignored_skipped": "被忽略规则跳过",
        "cli.csv_written": "CSV 已写入 {path}",
        "cli.dir_not_exist": "目录不存在：{path}",
        "cli.specify_game": "请用 --game 指定游戏（见 `steamverify list`）",
        "cli.build_hint": (
            "提示：本清单对应构建 {manifest_build}，而注册表中的当前构建是 {current_build}"
        ),
        # ---- discovery ----
        "discover.not_found": (
            "无法自动定位游戏目录。\n"
            "请用 --dir \"<游戏文件夹路径>\" 指定（即包含游戏 exe 的那个文件夹）。"
        ),
        "discover.user": "用户指定（--dir）",
        "discover.guessed": "推测路径",
        # ---- progress ----
        "progress.scanning": "正在扫描 {detail} ...",
        "progress.hashing": "正在计算哈希 {done} 个文件",
        # ---- report: header ----
        "report.label.game": "游戏",
        "report.label.app_id": "app id",
        "report.label.build": "构建",
        "report.label.manifest": "清单",
        "report.label.manifest_id": "清单标识",
        "report.label.directory": "目录",
        "report.label.scanned": "扫描时间",
        "report.manifest_summary": "{files} 个文件，{chunks} 个分块，{size}",
        "report.size_only": "仅比对大小（未校验内容哈希）",
        # ---- report: status column ----
        "report.status.verified": "一致",
        "report.status.modified": "已修改",
        "report.status.stub": "占位",
        "report.status.extra": "多余",
        "report.status.missing": "缺失",
        "report.status.ignored": "已忽略",
        "report.status.errors": "错误",
        "report.files_column": "{count:>7,} 个文件",
        # ---- report: summary ----
        "report.summary": "汇总",
        "report.row.verified": "与官方清单一致的文件",
        "report.row.modified": "清单内但内容不同的文件",
        "report.row.stub": "无内容的占位条目（DLC 授权标记）",
        "report.row.extra": "不在任何官方清单中的文件",
        "report.row.missing": "缺失的官方文件",
        "report.row.ignored": "被忽略规则跳过",
        "report.row.errors": "无法读取的文件",
        "report.verified_total": "已按官方版本校验 {files} 个文件 / {size}",
        "report.extra_total": "多余文件 {files} 个 / {size}，不属于官方版本",
        # ---- report: verdict ----
        "report.result.clean": "结论：干净 —— 本安装与官方清单完全一致。",
        "report.result.not_clean": "结论：不干净 —— 官方游戏数据与清单不符。",
        "report.result.stub_only": (
            "结论：游戏数据完好；仅有无内容的 DLC 标记文件存在差异。"
        ),
        "report.result.extra_only": "结论：官方文件完好，但存在多余文件。",
        # ---- report: sections ----
        "report.section.extra_groups": "多余文件分布",
        "report.section.stubs": "占位条目（不是损坏信号）",
        "report.section.modified": "被修改的文件",
        "report.section.missing": "缺失的文件",
        "report.section.extras": "全部多余文件",
        "report.section.errors": "错误",
        "report.section.next": "处理建议",
        "report.more": "…… 另有 {count} 项",
        "report.more_hint": "…… 另有 {count} 项（用 --json 或 --csv 查看完整列表）",
        "report.extra_path_note": (
            "路径相对于游戏目录；结尾带 / 表示该文件夹内的文件全部都是多余的。"
            "想保留的可用 --ignore '<路径>/*' 忽略，加 -v 可查看每个文件的大小与时间。"
        ),
        "report.stub_note": "这些官方条目只带标识哈希，本地本就应为空文件。",
        "report.bad_chunks": "首个损坏分块偏移：{offsets}",
        "report.exit_code": "退出码：{code}",
        # ---- report: next steps ----
        "report.next.clean": "无需处理。若 Steam 仍报错，请用 Steam 自带的校验功能。",
        "report.next.stub_only": (
            "无需修复 —— 这些占位条目本就应为空。退出码仍非 0，因为目录并非"
            "逐字节完全一致；在 CI 中可用 --fail-on missing 忽略它们。"
        ),
        "report.next.steam_verify": "在 Steam 中：右键游戏 > 属性 > 已安装文件",
        "report.next.steam_verify2": "> “验证游戏文件的完整性”，然后重新运行本扫描。",
        "report.next.still_differ": "仍然不一致的文件，通常是覆盖了游戏数据的 MOD。",
        "report.next.extra_intro": "多余文件不属于官方版本。MOD、修改器、",
        "report.next.extra_intro2": "存档、崩溃转储和整合版残留都会出现在这里。",
        "report.next.extra_outro": "不需要的可以直接删除，不影响游戏本体。",
        # ---- scanner ----
        "scan.reason.placeholder": (
            "占位文件：官方条目只携带一个标识哈希，而本地文件本就应当为空"
        ),
        "scan.reason.size_differs": "大小不同（本地 {size} 字节，官方 {official} 字节）",
        "scan.reason.content_differs": "内容不同",
        "scan.reason.chunks_differ": "{total} 个分块中有 {bad} 个不同",
        "scan.reason.file_differs": "文件内容不同",
        "scan.reason.unreadable": "无法读取：{detail}",
        "scan.error.walk": "无法完整枚举游戏目录中的文件",
        "scan.error.hash": "{path}：{detail}",
        "scan.error.interrupted": "扫描被用户中断",
        # ---- errors raised to the user ----
        "err.manifest_not_found": "找不到清单文件：{path}",
        "err.manifest_not_svm": "不是 .svm 清单：{path}",
        "err.manifest_digest": (
            "清单 SHA-256 与注册表记录不符 —— 内置清单可能已被修改\n"
            "  期望 {expected}\n  实际 {actual}"
        ),
        "err.dir_not_found": "找不到游戏目录：{path}",
        "err.file_not_found": "找不到文件：{path}",
        "err.unknown_game": "未知游戏 '{slug}'；内置游戏：{known}",
        "err.no_manifests": "游戏 '{slug}' 没有任何清单",
        "err.no_build": "游戏 '{slug}' 没有构建 '{build}' 的清单（可用：{available}）",
    },
}


def available_languages() -> list[str]:
    """Canonical language codes, English first."""
    return [DEFAULT_LANGUAGE] + sorted(k for k in CATALOG if k != DEFAULT_LANGUAGE)


def normalise_language(value: str | None) -> str | None:
    """Map a user-supplied tag to a canonical code, or ``None`` if unknown."""
    if not value:
        return None
    key = value.strip().lower().replace("_", "-")
    if key in LANGUAGE_ALIASES:
        return LANGUAGE_ALIASES[key]
    # zh-Hans-CN -> zh-hans
    parts = key.split("-")
    for length in (2, 1):
        candidate = "-".join(parts[:length])
        if candidate in LANGUAGE_ALIASES:
            return LANGUAGE_ALIASES[candidate]
    return None


def _from_environment() -> str | None:
    for name in ("STEAMVERIFY_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(name)
        if not value:
            continue
        # POSIX locales look like zh_CN.UTF-8
        value = value.split(".")[0].split("@")[0]
        if value in ("C", "POSIX"):
            continue
        found = normalise_language(value)
        if found:
            return found
    return None


def resolve_language(explicit: str | None = None) -> str:
    """Pick a language: explicit argument, then environment, then English."""
    return normalise_language(explicit) or _from_environment() or DEFAULT_LANGUAGE


class Translator:
    """Looks up messages and formats them.

    ``tr("report.more_hint", count=12)`` returns the message with ``{count}``
    substituted. Unknown keys raise :class:`KeyError` rather than silently
    emitting a placeholder, so typos surface in tests instead of in a user's
    terminal.
    """

    def __init__(self, language: str = DEFAULT_LANGUAGE) -> None:
        self.language = normalise_language(language) or DEFAULT_LANGUAGE
        self._messages = CATALOG[DEFAULT_LANGUAGE]
        self._localised = CATALOG.get(self.language, {})

    @property
    def name(self) -> str:
        return LANGUAGES.get(self.language, (self.language, self.language))[0]

    def __call__(self, key: str, **kwargs: Any) -> str:
        return self.tr(key, **kwargs)

    def tr(self, key: str, **kwargs: Any) -> str:
        text = self._localised.get(key)
        if text is None:
            # Fall back to the reference catalog, then to a loud marker.
            text = self._messages.get(key)
        if text is None:
            raise KeyError(f"no message for key {key!r} in language {self.language!r}")
        if kwargs:
            try:
                return text.format(**kwargs)
            except (KeyError, IndexError) as exc:
                raise KeyError(
                    f"message {key!r} needs a value for {exc} "
                    f"(given: {sorted(kwargs)})"
                ) from exc
        return text

    def template(self, key: str) -> str:
        """Raw template, useful when the caller supplies values in stages."""
        return self._localised.get(key) or self._messages.get(key) or ""

    def keys(self) -> set[str]:
        return set(self._messages)

    def untranslated(self) -> list[str]:
        """Keys that exist in English but not in this language."""
        return sorted(set(self._messages) - set(self._localised))


#: Process-wide default, set once by the CLI.
_current: Translator = Translator(DEFAULT_LANGUAGE)


def get_translator(language: str | Translator | None = None) -> Translator:
    """Return a translator for ``language``.

    Accepts a language code, an existing :class:`Translator` (returned as-is,
    so callers may pass either), or ``None`` for the process-wide default.
    """
    if language is None:
        return _current
    if isinstance(language, Translator):
        return language
    return Translator(language)


def set_language(language: str | None = None) -> Translator:
    """Set the process-wide default language and return its translator."""
    global _current
    _current = Translator(resolve_language(language))
    return _current
