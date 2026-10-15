#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["reportlab>=5.0", "matplotlib>=3.11", "svglib>=2.2", "pillow>=12"]
# ///
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Ryan Musante
"""Analyze a cachyos-bugreport.log and a ry-verify JSONL log and build a PDF report.

Every finding, quote, line reference, and figure comes from the two inputs on each run;
the script carries analysis rules (message patterns and what they mean), never results.
Sections: SETUP, INPUT, RULES, ANALYSIS, FIGURES, LAYOUT, BUILD.
Exit codes: 0 built (--check: inputs parsed), 1 input or build failed, 2 usage, 3 preflight failed, 130 interrupted.
"""

from __future__ import annotations

import contextlib
import os
import signal

if __name__ == "__main__":

    def _quit(*_: object) -> None:
        """Exit 130 on Ctrl-C, SIGTERM, or SIGHUP before main() installs its handlers: nothing is written yet."""
        with contextlib.suppress(OSError):  # stderr may be closed
            os.write(2, b"build_report.py: interrupted; nothing written\n")
        os._exit(130)  # no teardown, where a C extension caught half loaded could crash

    for _name in ("SIGINT", "SIGTERM", "SIGHUP"):
        if hasattr(signal, _name):
            signal.signal(getattr(signal, _name), _quit)

import argparse
import datetime as dt
import hashlib
import importlib.util
import itertools
import json
import re
import struct
import sys
import tempfile
import textwrap
import warnings
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable, Mapping, Sequence
    from types import ModuleType

    from matplotlib.axes import Axes
    from matplotlib.dates import AutoDateLocator, ConciseDateFormatter
    from matplotlib.figure import Figure
    from reportlab.pdfgen.canvas import Canvas

# ── SETUP ─────────────────────────────────────────────────────────────
# Version, exit codes, fonts, command line, preflight, and the shared build state.
__version__ = "7.5.0"
EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_PREFLIGHT, EXIT_INTERRUPT = 0, 1, 2, 3, 130  # 130 = 128 + SIGINT, as shells report


def user_font_dir() -> Path:
    """Return $XDG_DATA_HOME/fonts (default ~/.local/share/fonts); without a home directory, a path no file matches."""
    try:
        return Path(os.environ.get("XDG_DATA_HOME") or "~/.local/share").expanduser() / "fonts"
    except RuntimeError:
        return Path("/nonexistent/.local/share/fonts")


FONT_DIRS = (Path("/usr/share/fonts/TTF"), Path("/usr/share/fonts/truetype/ibm-plex"), user_font_dir())
FONT_FILES = {
    "Plex": "IBMPlexSans-Regular",
    "Plex-It": "IBMPlexSans-Italic",
    "Plex-Md": "IBMPlexSans-Medium",
    "Plex-SB": "IBMPlexSans-SemiBold",
    "Plex-SBIt": "IBMPlexSans-SemiBoldItalic",
    "PlexC": "IBMPlexSansCondensed-Regular",
    "PlexC-SB": "IBMPlexSansCondensed-SemiBold",
    "PlexM": "IBMPlexMono-Regular",
    "PlexM-SB": "IBMPlexMono-SemiBold",
}
# reportlab, and Pillow through it, load at start-up; the preflight covers the modules loaded later
MODULES = {"matplotlib": "python-matplotlib", "svglib": "python-svglib", "PIL": "python-pillow"}
MAX_PASSES = 6  # layout passes allowed before the page references must have settled
EPOCH_MAX = 253402300799  # 9999-12-31 23:59:59 UTC, the last second a PDF date can hold
HEADING_TOP_BAND = 70.0  # pt below the frame top within which a section heading opens its page


def out_path(text: str) -> Path:
    """Return the --out value as a path; one that ends in a slash names a directory, a usage error."""
    if text.endswith("/"):
        msg = f"names a directory: {text}"
        raise argparse.ArgumentTypeError(msg)
    return Path(text)


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser; its epilog lists the exit codes from the EXIT_* constants."""
    parser = argparse.ArgumentParser(
        prog="build_report.py",
        description="Analyze a cachyos-bugreport.log and a ry-verify JSONL log into a PDF report.",
        epilog=(
            f"Exit codes: {EXIT_OK} built (--check: inputs parsed), {EXIT_FAIL} input or build failed, "
            f"{EXIT_USAGE} usage, {EXIT_PREFLIGHT} preflight failed, {EXIT_INTERRUPT} interrupted."
        ),
    )
    parser.add_argument("--bugreport", type=Path, required=True, help="cachyos-bugreport.log from cachyos-bugreport.sh")
    parser.add_argument(
        "--verify", type=Path, required=True, help="ry-verify JSONL log (verify-*.jsonl or report-*.jsonl)"
    )
    parser.add_argument(
        "--out", type=out_path, help="output PDF (default: ./post-boot-log-analysis-<capture date>.pdf)"
    )
    parser.add_argument(
        "--fonts", type=Path, help="directory with the IBM Plex TTF files (default: system and user font directories)"
    )
    parser.add_argument("--check", action="store_true", help="parse the inputs and run the cross-checks; build nothing")
    parser.add_argument(
        "--verbose", action="store_true", help="report parsing, analysis, layout passes, and the result"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


_ARGS: argparse.Namespace | None = None
if __name__ == "__main__":
    # Parse before importing ReportLab, so --help, --version, and usage errors work without the dependencies.
    _ARGS = build_parser().parse_args()

try:
    from reportlab import rl_config
    from reportlab.graphics import shapes as rl_shapes
    from reportlab.lib import colors
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.pdfmetrics import registerFontFamily, stringWidth
    from reportlab.pdfbase.ttfonts import TTFError, TTFont
    from reportlab.platypus import (
        BaseDocTemplate,
        CondPageBreak,
        Flowable,
        Frame,
        KeepTogether,
        NextPageTemplate,
        PageBreak,
        PageTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )
    from reportlab.platypus import tables as rl_tables
    from reportlab.platypus.doctemplate import LayoutError
except ImportError as exc:
    missing = exc.name or "reportlab"
    package = MODULES.get(missing.partition(".")[0], "python-reportlab")
    print(f"build_report.py: cannot import {missing} (pacman: {package})", file=sys.stderr)
    sys.exit(EXIT_PREFLIGHT)


class PreflightError(Exception):
    """A missing module, font, or input; maps to exit code 3."""


class InputError(Exception):
    """An input that is present but not in the expected format; maps to exit code 1."""


def readable(path: Path) -> bool:
    """Return whether path is a file this process can read; a directory it cannot enter counts as no."""
    try:
        return path.is_file() and os.access(path, os.R_OK)
    except OSError:
        return False


def preflight(fonts: Path | None, inputs: Sequence[Path]) -> Path:
    """Return the font directory, or raise PreflightError naming what is missing, unreadable, or no TrueType font."""
    missing = [f"{name} (pacman: {pkg})" for name, pkg in MODULES.items() if importlib.util.find_spec(name) is None]
    if missing:
        msg = "missing Python modules: " + ", ".join(missing)
        raise PreflightError(msg)
    need = [f"{stem}.ttf" for stem in FONT_FILES.values()]
    dirs = (fonts,) if fonts else FONT_DIRS
    font_dir = next((d for d in dirs if all(readable(d / f) for f in need)), None)
    if font_dir is None:
        searched = ", ".join(str(d) for d in dirs)
        msg = f"readable IBM Plex TTF files not found in {searched} (pacman: ttf-ibm-plex, or pass --fonts)"
        raise PreflightError(msg)
    for stem in FONT_FILES.values():
        path = font_dir / f"{stem}.ttf"
        try:
            TTFont(stem, str(path))  # parses the file; the build registers the faces
        except (TTFError, struct.error, ValueError, LookupError) as exc:
            msg = f"not a TrueType font: {path} ({exc})"
            raise PreflightError(msg) from exc
    unreadable = [str(p) for p in inputs if not readable(p)]
    if unreadable:
        msg = "cannot read input: " + ", ".join(unreadable)
        raise PreflightError(msg)
    return font_dir


@dataclass
class BuildState:
    """Mutable state shared by the layout passes, the flowables, and the page callbacks."""

    ref: dict[str, int] = field(default_factory=dict)  # anchor pages from the previous pass
    anchors: dict[str, int] = field(default_factory=dict)  # anchor pages seen in this pass
    h1pos: dict[int, list[tuple[str, bool]]] = field(default_factory=dict)  # page -> [(heading, opens the page)]
    page_section: dict[int, str] = field(default_factory=dict)  # page -> running-header text
    unresolved: set[str] = field(default_factory=set)  # page references not known in this pass
    figures: list[tuple[int, str]] = field(default_factory=list)  # (number, title) in reading order, this pass
    fig_ref: list[tuple[int, str]] = field(default_factory=list)  # the same from the previous pass, for the contents
    model: Model | None = None
    tables: int = 0
    total: int = 0
    chart_dir: Path = field(default_factory=Path)
    font_dir: Path = field(default_factory=Path)
    root_uuid: str = ""  # the root file system's UUID from the kernel command line, which mask() names
    mpl: ModuleType | None = None

    def reset(self) -> None:
        """Forget the previous build's model, pages, anchors, and figures; keep matplotlib, which loads once."""
        self.__init__(mpl=self.mpl)


STATE = BuildState()


# ── INPUT ─────────────────────────────────────────────────────────────
# Parsers for the two capture formats: cachyos-bugreport.sh output and ry-verify JSONL.
SEPARATOR = re.compile(r"^(?:_{44}|-{44})$")  # the bare 44-character rules cachyos-bugreport.sh writes
SECTION_TITLES = {
    "Start of CachyOS bug report log file": "header",
    "Getting Hardware Information": "inxi",
    "Getting Scheduler information": "sched",
    "dmesg": "dmesg",
    "journalctl of current boot": "journal-current",
    "journalctl of previous boot": "journal-previous",
    "Installed packages": "packages",
}
DMESG_LINE = re.compile(r"^\[\s*(?P<t>\d+\.\d+)\]\s?(?P<msg>.*)$")
JOURNAL_LINE = re.compile(
    r"^(?P<mon>[A-Z][a-z]{2}) (?P<day>[ \d]\d) (?P<time>\d{2}:\d{2}:\d{2}) (?P<host>\S+) "
    r"(?P<ident>[^\s\[:]+)(?:\[(?P<pid>\d+)\])?: (?P<msg>.*)$"
)
MONTHS = {
    m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)
}


@dataclass
class DmesgEntry:
    """One kernel ring-buffer message: report line, seconds since kernel start, text."""

    no: int
    t: float
    text: str


@dataclass
class JournalEntry:
    """One journal entry of the capture's `journalctl -p 4..1`: warning through alert."""

    no: int
    boot: str  # "current" or "previous"
    when: dt.datetime
    ident: str
    pid: str
    text: str

    @property
    def line(self) -> str:
        """Return the entry as quoted in the report: time, process, message."""
        proc = f"{self.ident}[{self.pid}]" if self.pid else self.ident
        return f"{self.when:%H:%M:%S} {proc}: {self.text}"


@dataclass
class BugReport:
    """The parsed cachyos-bugreport.log."""

    path: Path
    raw: bytes
    lines: list[str]
    sections: dict[str, tuple[int, int]]  # name -> (first line, last line), 1-based
    date_text: str
    captured: dt.datetime | None
    uname: str
    cmdline: str
    inxi: list[tuple[int, str]]  # logical lines: (first line number, wrapped parts joined)
    inxi_parts: dict[int, list[tuple[int, str]]]  # per logical line, by its first line: (line number, part as joined)
    dmesg: list[DmesgEntry]
    journal: list[JournalEntry]
    journal_unparsed: list[int]  # journal-section lines that are neither entries, continuations, nor markers
    packages: list[int]  # lines of the package list that name a repository/package and a version


@dataclass
class VerifyItem:
    """One ry-verify result record."""

    no: int
    phase: str  # "preamble", "static", or "runtime"
    section: str
    status: str  # OK, INFO, WARN, FAIL, ERR
    text: str

    @property
    def counted(self) -> str:
        """Return the status the record counts under, as ry-verify does: ERR as FAIL, a generator failure as GEN_FAIL.

        ry-verify logs a failed generator as a FAIL record but counts it as GEN_FAIL, not FAIL.
        """
        if self.status == "ERR":
            return "FAIL"
        return "GEN_FAIL" if self.status == "FAIL" and GENERATOR_FAILED.search(self.text) else self.status


@dataclass
class VerifyLog:
    """The parsed ry-verify JSONL log."""

    path: Path
    raw: bytes
    records: list[dict[str, Any]]
    header: dict[str, Any]
    footer: dict[str, Any]
    items: list[VerifyItem]
    phase_results: dict[str, dict[str, int]]  # phase -> counts from its VERIFY_RESULT record
    combined: dict[str, int]

    def meta(self, key: str) -> str:
        """Return a header field for display: sanitized, or "?" when the log does not state it."""
        value = self.header.get(key)
        return "?" if value is None else sanitize(str(value))

    @property
    def started(self) -> dt.datetime | None:
        """Return the header timestamp."""
        return parse_iso(self.header.get("ts", ""))

    @property
    def finished(self) -> dt.datetime | None:
        """Return the footer timestamp."""
        return parse_iso(self.footer.get("ts", ""))

    @property
    def interrupted(self) -> bool:
        """Return True when the footer says the run was interrupted (its counts then cover one phase only)."""
        return self.footer.get("interrupted") is True

    @property
    def totals(self) -> dict[str, int]:
        """Return the run's ok, fail, warn, and gen_fail counts from the best source the log holds.

        A finished run's footer comes first; without one, the VERIFY_RESULT_COMBINED record. Otherwise the phase
        records are summed, and a phase without one (the phase an interruption cut short) adds its result records,
        which cannot see generator failures.
        """
        keys = ("ok", "fail", "warn", "gen_fail")
        if self.footer and not self.interrupted:
            return {k: self.footer.get("pass" if k == "ok" else k, 0) for k in keys}  # integers, checked on parse
        if self.combined:
            return {k: self.combined.get(k, 0) for k in keys}
        out = {k: sum(r.get(k, 0) for r in self.phase_results.values()) for k in keys}
        for item in self.items:
            if item.phase != "preamble" and item.phase not in self.phase_results and item.counted.lower() in out:
                out[item.counted.lower()] += 1
        return out

    @property
    def status(self) -> tuple[str, str]:
        """Return the verdict and exit text: PASS or FAIL with exit N, interrupted, or unknown (no footer or exit)."""
        if not self.footer:
            return "unknown", "no footer"
        code = self.footer.get("exit_code")
        if code is None:
            return "unknown", "no exit code"
        if self.interrupted:
            return "interrupted", f"exit {code}"
        return ("PASS" if code == 0 else "FAIL"), f"exit {code}"

    @property
    def stopped(self) -> VerifyItem | None:
        """Return the first ERR record when ry-verify stopped before any check ran (only ERR results), else None."""
        checks = [i for i in self.items if i.phase != "preamble"]
        if checks and all(i.status == "ERR" for i in checks):
            return checks[0]
        return None

    @property
    def phase_stops(self) -> list[VerifyItem]:
        """Return the first record of each phase that logged only ERR results, when another phase ran its checks.

        ry-verify restores the static totals when its runtime phase stops for want of sudo, so the totals leave out
        such a phase's records.
        """
        if self.stopped:
            return []
        phases = [[i for i in self.items if i.phase == p] for p in ("static", "runtime")]
        return [items[0] for items in phases if items and all(i.status == "ERR" for i in items)]

    @property
    def hints(self) -> list[VerifyItem]:
        """Return the INFO records in which ry-verify names its own fix (Run: …, Install missing: …)."""
        return [i for i in self.items if i.status == "INFO" and re.search(r"\b(?:Run|Install missing): \S", i.text)]

    def outside_totals(self) -> list[str]:
        """Return the preamble FAIL and WARN records the totals leave out, as ["2 WARN"], judged by the totals.

        ry-verify resets its counters once its checks start, so a finished run's totals omit what the preamble
        logged; a run that stopped before its checks keeps it.
        """
        out = []
        for status in ("FAIL", "WARN"):
            before = sum(1 for i in self.items if i.phase == "preamble" and i.counted == status)
            every = sum(1 for i in self.items if i.counted == status)
            if before and self.totals[status.lower()] == every - before:
                out.append(f"{before} {status}")
        return out


def parse_iso(text: object) -> dt.datetime | None:
    """Return a datetime from ry-verify's ISO stamp (YYYY-MM-DDTHH:MM:SS.fff±hhmm), or None for anything else."""
    if not isinstance(text, str):
        return None
    try:
        return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%f%z")
    except ValueError:
        return None


def parse_sections(lines: Sequence[str]) -> dict[str, tuple[int, int]]:
    """Split the bug report at its separator lines and name each block by its first text line."""
    blocks, start = [], 1
    for n, line in enumerate(lines, 1):
        if SEPARATOR.match(line):
            blocks.append((start, n - 1))
            start = n + 1
    blocks.append((start, len(lines)))
    sections = {}
    for first, last in blocks:
        title_no = next((n for n in range(first, last + 1) if lines[n - 1].strip()), None)
        if title_no is None:
            continue
        title = lines[title_no - 1].strip()
        name = next((v for k, v in SECTION_TITLES.items() if title.startswith(k)), None)
        if name and name not in sections:
            sections[name] = (title_no + 1, last) if name != "header" else (first, last)
    return sections


def section_slice(lines: Sequence[str], sections: dict[str, tuple[int, int]], name: str) -> list[tuple[int, str]]:
    """Return (line number, text) for every line of a named report section."""
    first, last = sections.get(name, (1, 0))
    return [(n, lines[n - 1]) for n in range(first, last + 1)]


CAPTURE_FORMATS = (  # glibc's `date` layouts in the C and English locales, once the zone and commas are dropped
    "%a %b %d %H:%M:%S %Y",  # C, POSIX
    "%a %b %d %I:%M:%S %p %Y",  # en_US
    "%a %d %b %H:%M:%S %Y",  # en_GB, en_AG, en_SC, en_ZM
    "%a %d %b %Y %H:%M:%S",  # en_AU, en_BW, en_IE, en_IL, en_NG, en_NZ, en_ZA, en_ZW
    "%a %d %b %Y %I:%M:%S %p",  # en_CA, en_SG
    "%A %d %B %Y %I:%M:%S %p",  # en_IN, en_PH
    "%A %B %d %Y %p%I:%M:%S",  # en_HK
    "%Y-%m-%dT%H:%M:%S",  # en_DK, en_SE
)
ZONE_WORD = re.compile(r"ChST|[A-Z]{3,5}|[+-]\d{2}(?::?\d{2})?|UTC[+-]?\d*")  # BST, AEDT, ChST, +04, +0545, -03:00


def parse_capture_date(text: str) -> dt.datetime | None:
    """Return the capture time from the report's `date` line, or None when no English layout fits.

    The zone is dropped, as a word or as an offset after the time: journal times are local wall-clock too.
    """
    text = re.sub(r"(?<=:\d\d)[+-]\d\d(?::?\d\d)?\b", "", text.replace(",", " "))
    cleaned = " ".join(w for w in text.split() if not ZONE_WORD.fullmatch(w))
    for layout_ in CAPTURE_FORMATS:
        try:
            return dt.datetime.strptime(cleaned, layout_)
        except ValueError:
            continue
    return None


JOURNAL_MARKERS = ("-- ", "No previous boot log available")  # journalctl notices and the capture script's own


def journal_time(m: re.Match[str], reference: dt.datetime) -> dt.datetime | None:
    """Return a journal line's time, or None for a date or time no calendar has.

    Journal lines carry no year: the time takes the reference's, or the year before for a later month (a boot
    across New Year) or for a February 29 the reference year lacks.
    """
    hh, mm, ss = (int(x) for x in m.group("time").split(":"))
    month, day = MONTHS[m.group("mon")], int(m.group("day"))
    year = reference.year - 1 if month > reference.month else reference.year
    for y in (year, year - 1):
        try:
            return dt.datetime(y, month, day, hh, mm, ss)  # journal time, no zone
        except ValueError:
            continue
    return None


def parse_journal(
    lines: Iterable[tuple[int, str]], boot: str, reference: dt.datetime
) -> tuple[list[JournalEntry], list[int]]:
    """Return the journal entries of one boot and the lines that parsed as nothing.

    Continuation lines join the entry parsed just above them; journalctl markers are skipped. journal_time() dates
    each entry from the reference. Times are local wall-clock, as the journal prints them.
    """
    entries: list[JournalEntry] = []
    unparsed: list[int] = []
    last: JournalEntry | None = None
    for n, line in lines:
        m = JOURNAL_LINE.match(line)
        when = journal_time(m, reference) if m and m.group("mon") in MONTHS else None
        if m and when:
            last = JournalEntry(n, boot, when, m.group("ident"), m.group("pid") or "", m.group("msg"))
            entries.append(last)
        elif last and line.startswith((" ", "\t")) and line.strip():
            last.text += " " + line.strip()
        elif line.strip() and not line.startswith(JOURNAL_MARKERS):
            unparsed.append(n)
            last = None  # a continuation below an unread line belongs to it, not to the entry before
    return entries, unparsed


IRC_KEY_COLOR = re.compile(r"\x03(\d{1,2})(?=[A-Za-z])")  # the color code of inxi's first IRC key, as in "\x0312Kernel"
IRC_PLAIN = re.compile(r"[\x02\x0f\x16\x1d\x1f]")  # IRC bold, reset, reverse, italic, underline


def normalize_inxi(text: str, key: str) -> str:
    """Return one inxi output line in its terminal form.

    inxi prints IRC formatting when its stdin is not a terminal (cachyos-bugreport.sh run from a launcher or with
    redirected input): keys become runs in the key color without their colons, values follow a reset byte. Keys get
    their colons back and the codes go. The key color followed by a letter opens a key; followed by one or two digits
    and a reset, it is a numeric key, as in per-core speeds (`1: 2100`) and repository lists. A value that starts with
    digits after a reset keeps them: a wrapped `5002`, or `128 GiB` under key color 12.
    """
    if key:
        opens = rf"\x03{key}(?:,\d{{1,2}})?(?=[A-Za-z])"
        keyed = re.compile(rf"{opens}([^\x03]*?)(\s*)\x03")
        text = keyed.sub(lambda m: m[1] + ("" if m[1].endswith(":") else ":") + m[2], text)
        text = re.sub(rf"\x03{key}(\d{{1,2}})( ?)\x03", r"\1:\2", text)
        text = re.sub(rf"{opens}|\x03", "", text)
    return IRC_PLAIN.sub("", text)


def inxi_logical_lines(
    lines: Iterable[tuple[int, str]],
) -> tuple[list[tuple[int, str]], dict[int, list[tuple[int, str]]]]:
    """Return inxi output with its wrapped continuation lines (indented 4 or more) joined to the entry above.

    inxi wraps at the terminal width wherever a word ends, so a line may end on a key such as `cache:` whose
    value continues below. The second value holds each logical line's parts, (line number, text as joined), by its
    first line number; the parts are joined by single spaces.
    """
    lines = list(lines)
    found = next((m for _, raw in lines if (m := IRC_KEY_COLOR.search(raw))), None)
    key = found.group(1) if found else ""
    out: list[tuple[int, str]] = []
    parts: dict[int, list[tuple[int, str]]] = {}
    for n, raw in lines:
        text = normalize_inxi(raw, key).rstrip()
        if not text.strip():
            continue
        if out and text.startswith("    "):
            out[-1] = (out[-1][0], out[-1][1] + " " + text.strip())
            parts[out[-1][0]].append((n, text.strip()))
        else:
            out.append((n, text))
            parts[n] = [(n, text)]
    return out, parts


def text_lines(raw: bytes) -> list[str]:
    """Return UTF-8 text (byte order mark optional) split at newlines only, numbered as rg -n and editors number it.

    str.splitlines() also breaks at form feeds, vertical tabs, U+0085, U+2028, and other separators a log line can
    hold, which would shift every later line number.
    """
    lines = raw.decode("utf-8-sig", errors="replace").split("\n")
    if lines[-1] == "":
        lines.pop()
    return [line.removesuffix("\r") for line in lines]


def parse_bugreport(path: Path, fallback: dt.datetime | None = None) -> BugReport:
    """Parse cachyos-bugreport.log; raise InputError when it does not look like one.

    Journal years come from the capture time, else from fallback (the ry-verify start), else from 1970.
    """
    raw = path.read_bytes()
    lines = text_lines(raw)
    sections = parse_sections(lines)
    if "header" not in sections or "dmesg" not in sections:
        msg = f"{path.name}: not a cachyos-bugreport.log (no report header or dmesg section)"
        raise InputError(msg)
    first, last = sections["header"]
    head = dict(ln.split(": ", 1) for ln in lines[first - 1 : last] if ": " in ln)
    captured = parse_capture_date(head.get("Date", ""))
    dmesg: list[DmesgEntry] = []
    for n, line in section_slice(lines, sections, "dmesg"):
        m = DMESG_LINE.match(line)
        if m:
            dmesg.append(DmesgEntry(n, float(m.group("t")), m.group("msg")))
        elif dmesg and line.strip():
            dmesg[-1].text += " " + line.strip()
    reference = captured or fallback or dt.datetime(1970, 12, 31)  # no month is later, so none goes to 1969
    journal, unparsed = parse_journal(section_slice(lines, sections, "journal-current"), "current", reference)
    previous, unparsed_prev = parse_journal(section_slice(lines, sections, "journal-previous"), "previous", reference)
    journal += previous
    unparsed += unparsed_prev
    packages = [n for n, line in section_slice(lines, sections, "packages") if re.match(r"^\S+/\S+ \S", line)]
    inxi, inxi_parts = inxi_logical_lines(section_slice(lines, sections, "inxi"))
    return BugReport(
        path,
        raw,
        lines,
        sections,
        sanitize(head.get("Date", "")),
        captured,
        head.get("uname", ""),
        head.get("cmdline", ""),
        inxi,
        inxi_parts,
        dmesg,
        journal,
        unparsed,
        packages,
    )


# ERR is ry-verify's fatal-check level; it counts as FAIL
RESULT_LINE = re.compile(r"^(OK|INFO|WARN|FAIL|ERR):\s+(.*)$")
GENERATOR_FAILED = re.compile(r": generator failed\b")  # a FAIL record that ry-verify counts as GEN_FAIL
PHASE_BANNER = re.compile(r"^INFO: (?:Static|Runtime) verification \(")  # a phase heading logged as INFO, no result
# ry-verify's signal handler logs this through _log, which counts nothing: no result either
SIGNAL_NOTICE = re.compile(r"^WARN: Caught SIG[A-Z]+ — cleaning up")
COUNTS = re.compile(r"\b(ok|fail|warn|gen_fail)=(\d{1,9})\b")
FOOTER_NUMBERS = ("pass", "fail", "warn", "gen_fail", "exit_code")  # footer fields the report reads as integers


def read_jsonl(raw: bytes, name: str) -> list[tuple[int, dict[str, Any]]]:
    """Return (line number, object) for each line of a JSONL file, skipping blank lines; raise InputError otherwise."""
    records: list[tuple[int, dict[str, Any]]] = []
    for n, line in enumerate(text_lines(raw), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except (ValueError, RecursionError) as exc:  # JSONDecodeError, an over-long integer, or too deep a nesting
            msg = f"{name}: line {n} is not JSON ({getattr(exc, 'msg', exc)})"
            raise InputError(msg) from exc
        if not isinstance(rec, dict):
            msg = f"{name}: line {n} is not a JSON object"
            raise InputError(msg)
        records.append((n, rec))
    return records


@dataclass
class VerifyCursor:
    """Where the ry-verify log stands while its records are read: phase, section, and the last phase started."""

    phase: str = "preamble"  # static, runtime, preamble, or "" between phases
    last_phase: str = "static"  # the phase a VERIFY_RESULT record belongs to
    section: str = "PREAMBLE"

    def advance(self, data: str) -> bool:
        """Apply a marker or section record; return True when the record was one."""
        if m := re.match(r"^=== (STATIC|RUNTIME) VERIFICATION (START|END) ===$", data):
            self.phase = m.group(1).lower() if m.group(2) == "START" else ""
            self.last_phase, self.section = m.group(1).lower(), "BEFORE THE FIRST SECTION"
            return True
        if m := re.match(r"^ECHO: ([A-Z][A-Z0-9 /&-]+)$", data):
            self.section = m.group(1)
            return True
        return False


def parse_verify(path: Path) -> VerifyLog:
    """Parse a ry-verify JSONL log; raise InputError when it is not one. Records are numbered by file line."""
    raw = path.read_bytes()
    numbered = read_jsonl(raw, path.name)
    records = [rec for _, rec in numbered]
    header = next((r for r in records if r.get("event") == "header"), None)
    if header is None or "version" not in header:
        msg = f"{path.name}: not a ry-verify log (no header record with a version)"
        raise InputError(msg)
    foot = next((r for r in reversed(records) if r.get("event") == "footer"), {})
    for key in FOOTER_NUMBERS:
        if key in foot and (isinstance(foot[key], bool) or not isinstance(foot[key], int)):
            msg = f"{path.name}: footer field {key} is not a whole number ({foot[key]!r})"
            raise InputError(msg)
    items, phase_results, combined = [], {}, {}
    at = VerifyCursor()
    for n, rec in numbered:
        data = str(rec.get("data", "")) if rec.get("event") == "log" else ""
        if not data:
            continue
        if at.advance(data):
            continue
        if data.startswith("VERIFY_RESULT_COMBINED:"):
            combined = {k: int(v) for k, v in COUNTS.findall(data)}
        elif data.startswith("VERIFY_RESULT:"):
            phase_results[at.last_phase] = {k: int(v) for k, v in COUNTS.findall(data)}
        elif (
            (m := RESULT_LINE.match(data))
            and at.phase
            and at.section != "VERIFICATION SUMMARY"
            and not PHASE_BANNER.match(data)
            and not SIGNAL_NOTICE.match(data)
        ):
            items.append(VerifyItem(n, at.phase, at.section, m.group(1), m.group(2).strip()))
    return VerifyLog(path, raw, records, header, foot, items, phase_results, combined)


# ── RULES ─────────────────────────────────────────────────────────────
# Message patterns and what they mean. Rules carry knowledge, never results: a rule only reaches the
# report when its pattern matches a line of the inputs, and every count it shows is taken from them.
SEVERITY_RANK = {"HIGH": 0, "MED": 1, "LOW": 2, "INFO": 3, "WATCH": 4, "SETTING": 5, "NOTE": 6}
FINDING_LEVELS = ("HIGH", "MED", "LOW", "INFO")
# A stop sequence logs its last lines within seconds; a longer window would reach into the session before it.
SHUTDOWN_WINDOW = 15  # seconds before the previous boot's last journal entry that count as shutdown
# Lines only a stop sequence logs. The capture keeps warnings alone, so a shutdown that logs none leaves the boot's
# last warning in the session before it; the window counts as the shutdown only when it holds one of these.
SHUTDOWN_MARK = re.compile(
    r"Failed to enqueue SYSTEMD_(?:USER_)?WANTS job|Transaction for .* is destructive|PipeWire remote error"
    r"|context kaput|crashed \(signal (?:1|15)\)|Authentication error: .*crashed"
)
# Words that mark a line as a possible failure; "tainted" stays out, as an untainted kernel's splat header reads
# "Not tainted" (a tainted kernel's "Tainted:" line is the kernel-taint rule's).
KEYWORDS = (
    "fail", "failed", "failure", "failures", "error", "warn", "warning", "unable", "cannot", "can't", "could not",
    "couldn't", "not supported", "unsupported", "not found", "no such", "denied", "invalid", "timeout", "timed out",
    "abort", "aborted", "aborting", "crash", "crashed", "panic", "oops", "bug", "taint", "taints", "tainting",
    "call trace", "segfault", "fault", "faults", "io_page_fault", "killed", "refused", "reset", "hang", "lockup",
    "stall", "stalls", "corrupt", "corrupted", "corruption", "mismatch", "deprecated", "unknown", "lacking", "kaput",
    "critical temperature", "hardware protection",
)  # fmt: skip
KEYWORD_RE = re.compile(r"(?i)\b(?:" + "|".join(re.escape(k) for k in KEYWORDS) + r")\b")


@dataclass(frozen=True)
class Rule:
    """A message class: where it appears, how to recognize it, and what it means."""

    key: str
    title: str
    area: str
    severity: str
    streams: tuple[str, ...]
    patterns: tuple[str, ...]
    explanation: str
    action: str = "None"
    window: str = ""  # "shutdown": only in the previous boot's last SHUTDOWN_WINDOW seconds
    idents: str = ""  # journal: the process name must match this pattern
    requires: str = ""  # the rule applies only when this pattern occurs somewhere in the bug report
    mitigated_by: str = ""  # a ry-verify OK record matching this lowers the severity to INFO
    covers: str = ""  # a mitigation that lists devices: (vendor, product) IDs it must name as 0xVVVV/0xPPPP


RESET_REASON = r"Previous system reset reason \[0x[0-9a-f]+\]: "  # amd.c prints one line per reason bit (6.16+)
# The reasons a reboot or power-off the system asked for leaves; keyboard reset is the reboot=k path.
ROUTINE_RESET = (
    r"software (?:wrote 0x[0-9A-F]+ to reset control register|issued PCI reset)|ACPI power state transition"
    r"|keyboard reset pin"
)
R = Rule
RULES = (
    # The five kernel error classes read dmesg only: attribute() routes the journal's kernel entries to dmesg rules,
    # so a program that prints "Oops" or "I/O error" is not taken for the kernel.
    R("kernel-splat", "Kernel oops, BUG, or warning splat", "Kernel", "HIGH", ("dmesg",),
      (r"\bOops\b", r"\bBUG: ", r"kernel BUG at ", r"^general protection fault",
       r"^WARNING: (?:CPU: \d+ PID: \d+ |.*, CPU#\d+: )", r"^UBSAN: ", r"INFO: task .+ blocked for more than",
       r"detected hard LOCKUP", r"detected (?:expedited )?stalls? on CPU", r"\birq \d+: nobody cared"),
      "The kernel reached an unexpected state and printed a splat; the lines after the first match name the "
      "module and function involved.",
      "Read the whole splat with `journalctl -k -b` (`-b -1` for the previous boot) and report it upstream with the "
      "module it names."),
    R("kernel-taint", "Kernel taint flag set", "Kernel", "MED", ("dmesg",),
      (r"\bTainted: (?:[A-Z]|\[)", r"taints kernel\.", r"tainting kernel", r": kernel tainted\.", r"inheriting taint",
       r"due to kernel taint"),
      "A taint flag marks the kernel as modified or degraded (an out-of-tree or unsigned module, an earlier oops); "
      "upstream wants reproductions on an untainted kernel.",
      "Read `/proc/sys/kernel/tainted` and identify the module that set the flag."),
    R("gpu-hang", "GPU hang or reset", "GPU / amdgpu", "HIGH", ("dmesg",),
      (r"amdgpu.*(?:GPU reset|ring \S+ timeout|job timed out|GPU recovery)",),
      "amdgpu detected a stuck engine and reset the GPU; applications using it may have lost their contexts.",
      "Note what was running at that time and check the amdgpu lines around the reset."),
    R("storage-errors", "Storage or file-system errors", "Storage", "HIGH", ("dmesg",),
      (r"I/O error", (r"\b(?:critical (?:medium|target|nexus|space allocation)|recoverable transport|protection"
       r"|timeout) error, dev "), r"EXT4-fs error", r"BTRFS (?:error|critical)", r"XFS .*[Cc]orruption",
       r"nvme\d+: (?:controller is down|resetting controller)", r"nvme\d+: I/O (?:tag )?\d+ .*\bQID \d+ timeout, "),
      "The kernel reported failed I/O or file-system damage; data on the affected device may be at risk.",
      "Check the device with `smartctl -a` and run a file-system check from a live system."),
    R("oom-kill", "Out-of-memory kill", "Memory", "MED", ("dmesg",),
      (r"(?i)out of memory: Killed process", r"oom-kill:"),
      "System memory, or a memory cgroup's limit (MemoryMax=), ran out and the kernel killed a process to recover.",
      "Find the killed process in the evidence and the memory consumer that caused it."),
    R("reset-abnormal", "Previous boot ended in an abnormal reset", "Boot / platform", "MED", ("dmesg",),
      (RESET_REASON + r"(?!" + ROUTINE_RESET + ")",),
      "The previous boot ended in neither a software reboot nor an ACPI power-off: a thermal trip, a held power "
      "button, a reset pin, a watchdog, or a hardware error ended it.",
      "Read the end of the previous boot with `journalctl -b -1 -e`; check cooling after a thermal trip and look for "
      "hardware errors after a watchdog or sync-flood reset."),
    R("core-dump", "A process crashed and dumped core", "Processes", "MED", ("journal",),
      (r"Process \d+ \(.+\) of user \d+ (?:dumped core|terminated abnormally)",),
      "systemd-coredump recorded a crashed program; the evidence names it.",
      "Inspect it with `coredumpctl list` and `coredumpctl info <PID>`."),
    R("shutdown-noise", "Shutdown-only noise", "Session / shutdown", "INFO", ("journal",),
      (r"Failed to enqueue SYSTEMD_(?:USER_)?WANTS job", r"Transaction for .* is destructive", r"PipeWire remote error",
       r"context kaput", r"dispatcher: .*failed", r"crashed \(signal (?:1|15)\)", r"Authentication error: .*crashed",
       r"Failed with result '", r"No object for name", r"Could not activate remote peer"),
      "Logged as services stopped for the reboot: SIGHUP and SIGTERM exits read as crashes, udev jobs meet the "
      "shutdown transaction, D-Bus refuses activations, and PipeWire clients lose their server.",
      window="shutdown"),
    R("redacted-dbus-unit", "A D-Bus-activated unit failed under a redacted name", "Services", "INFO",
      ("journal",), (r"dbus-:<email-address-redacted>: Failed with result",),
      "cachyos-bugreport.sh redacts the name, which reads as an email address; on-demand services fail this way "
      "when they exit on start, as KWallet's do with it off.",
      "Name it with `journalctl -b -o cat -p warning -g 'Failed with result'` (`-b -1` for the previous boot, "
      "`--user` for user units).", idents=r"systemd"),
    R("redacted-unit", "A unit failed under a redacted name", "Services", "LOW", ("journal",),
      (r"<email-address-redacted>: Failed with result", r"Failed to start <email-address-redacted>"),
      "A unit failed under a name cachyos-bugreport.sh redacted: template units such as getty@tty1.service read "
      "as email addresses.",
      "Name it with `journalctl -b -o cat -p warning -g 'Failed with result'` (`-b -1` for the previous boot, "
      "`--user` for user units), then inspect it with `systemctl status`.", idents=r"systemd"),
    R("kwallet-off", "ksecretd started without the PAM login handoff", "Session / KWallet", "INFO", ("journal",),
      (r"Lacking a socket, pipe",),
      "The login passed ksecretd no PAM socket, so the wallet stays locked; with the wallet disabled, ksecretd "
      "and its portal exit on start and fail.",
      "None when the wallet is disabled; otherwise check that the login manager's PAM stack runs pam_kwallet5."),
    R("kde-startup", "KDE, portal, and D-Bus startup noise", "Desktop / KDE, portal, D-Bus", "INFO", ("journal",),
      (r"Activation request for '[^']+' failed", r"Service file '[^']+' is not named after the D-Bus name",
       r"Failed to register with host portal", r"[Cc]harge thresholds? .*not supported|chargethreshold",
       r"Failed enumerating MM objects", r"no kernel backlight interface", r"\.qml:\d+", r"[Dd]eprecated",
       r"gtk\.portal|Lockdown", r"UPower", r"@DEFAULT_SOURCE@", r"[Ss]creencast", r"[Ss]creen configuration"),
      "Plasma, its portals, and D-Bus log these on working desktops: portal registration, legacy service names, "
      "absent services, hardware probes, screen notices, and QML warnings.",
      idents=r"plasmashell|kwin_wayland|ksmserver|kded6|org_kde_powerdevil|dbus-broker-launch|xdg-desktop-portal"
      r".*|plasmalogin|startplasma.*|kactivitymanagerd|powerdevil|baloo.*|kscreen.*|polkit-kde.*"),
    R("greeter-helper", "Greeter authentication helper exits with status 255", "Login / plasmalogin", "INFO",
      ("journal",), (r"plasmalogin-helper exited with 255",),
      "The greeter's helper exits with 255 when the greeter stops after a successful hand-over to the session."),
    R("kwin-commit", "KWin atomic commit refused at the session switch", "Desktop / KWin", "INFO", ("journal",),
      (r"atomic commit failed: Permission denied",),
      "The outgoing compositor loses DRM master during the greeter-to-session switch and logs the refused commit.",
      "None unless a login glitch appears."),
    R("kwin-killer", "KWin discards an unfinished kill prompt", "Desktop / KWin", "INFO", ("journal",),
      (r"kwin_killer_helper.*still running",),
      "KWin starts its killer helper when a closing window stops answering and discards it once the window goes "
      "away: an application hung briefly on close.", "None unless it recurs."),
    R("unity-launcher", "Task manager finds no desktop file for a launcher entry", "Desktop / task manager", "INFO",
      ("journal",), (r"Failed to find service for Unity Launcher",),
      "Plasma's task manager maps Unity LauncherEntry updates to desktop files and drops updates naming files "
      "that do not exist."),
    R("bt-audio", "Bluetooth audio device connects and disconnects", "Bluetooth / BlueZ, pulseaudio-qt", "INFO",
      ("journal",),
      (r"load_remote_sep\(\) Unable to load LastUsed", r"ext_io_disconnected\(\) Unable to get io data",
       r"No object for name"),
      "bluetoothd skips a cached audio endpoint the device no longer offers, or a profile connection closes before "
      "it is read; each node change makes pulseaudio-qt look up nodes that are gone."),
    R("bolt-nhi", "bolt does not recognize the USB4 host interfaces", "USB4 / bolt", "INFO", ("journal",),
      (r"unknown NHI PCI id",),
      "bolt lacks these USB4 host IDs and treats the host UUID as unstable, the safe default; it matters only for "
      "devices that need bolt authorization."),
    R("wpa-multicast", "wpa_supplicant multicast RX registration unsupported", "Network / wpa_supplicant", "INFO",
      ("journal",), (r"multicast RX registrations are not supported",),
      "nl80211 refuses multicast management-frame registrations when the driver does not advertise them; "
      "wpa_supplicant logs it and continues."),
    R("wext", "A program uses legacy wireless extensions", "Network / cfg80211", "INFO", ("dmesg", "journal"),
      (r"uses wireless extensions which will stop working",),
      "The kernel warns once per boot when a program queries Wi-Fi through the legacy wireless-extensions ioctls, "
      "which Wi-Fi 7 multi-link devices refuse; connectivity is unaffected."),
    R("nm-p2p", "NetworkManager warns on the Wi-Fi P2P device", "Network / NetworkManager", "LOW", ("journal",),
      (r"p2p-dev-\w+.*(?:forwarding|No such file or directory)",),
      "NetworkManager configures a P2P device that has no kernel network device and logs the failure.",
      "Unmanage `type:wifi-p2p` devices in a NetworkManager conf.d drop-in.", mitigated_by=r"(?i)p2p"),
    R("zswap-pool", "zswap pool initialized", "Memory / zswap", "INFO", ("dmesg",), (r"zswap: loaded using pool",),
      "A write to zswap's `enabled` parameter (CachyOS's zram udev rule) set up the pool; zswap stays as the "
      "command line configures it."),
    R("wq-name", "Workqueue name truncated", "Kernel", "INFO", ("dmesg",), (r"workqueue: name exceeds WQ_NAME_LEN",),
      "A driver names a workqueue longer than the kernel's limit, and the kernel truncates it once."),
    R("bt-esco", "Bluetooth controller lacks enhanced synchronous connections", "Bluetooth / btusb", "INFO",
      ("dmesg",), (r"Enhanced Setup Synchronous Connection command is advertised, but not supported",),
      "btusb marks the command broken for this controller; voice links use the legacy setup and A2DP audio is "
      "unaffected."),
    R("acp-machine", "Audio co-processor finds no machine driver", "Audio / ACP", "INFO", ("dmesg",),
      (r"No matching ASoC machine driver found",),
      "No ACP machine description matches the board, so audio runs through HDA and USB devices instead."),
    R("nvme-notice", "NVMe UUID and SGL notices", "Storage / NVMe", "INFO", ("dmesg",),
      (r"passthrough uses implicit buffer lengths", r"No UUID available providing old NGUID"),
      "Informational notices for a controller without SGL support and a namespace without a UUID."),
    R("tdx", "TDX message on a CPU without TDX", "Kernel / TDX", "INFO", ("dmesg",),
      (r"TDX not supported by the host platform",),
      "The kernel's Intel TDX host code logs this whenever the CPU lacks TDX host support."),
    R("platform-gaps", "Platform visibility gaps (AER, fan speed, USB LPM)", "Platform / firmware", "INFO",
      ("dmesg", "inxi"),
      (r"_OSC: platform does not support \[[^\]]*AER", r"We don't know the algorithms for LPM",
       r"No LPM exit latency info found", r"Fan Speeds \(rpm\): N/A"),
      "Firmware keeps PCIe error reporting (AER), so link errors go unreported; no fan tachometer is exposed; USB "
      "LPM is off where exit latencies are unknown.",
      "None; a clean ring is not proof of a clean PCIe link."),
    R("egistec", "Fingerprint reader without a driver", "Input / fingerprint", "INFO", ("dmesg",),
      (r"idVendor=1c7a, idProduct=0577",),
      "libfprint lists the EgisTec EH577 (1c7a:0577) as unsupported, so the sensor enumerates but no driver binds.",
      "None unless fingerprint login is wanted."),
    R("hid-joystick", "A Keychron keyboard or receiver exposes a HID joystick interface", "Input / HID", "LOW",
      ("dmesg",), (r"Joystick \[Keychron",),
      "The device presents a joystick interface that SDL and Proton games can take for a game controller.",
      "Add the device's VID/PID pairs to SDL_GAMECONTROLLER_IGNORE_DEVICES for the session.",
      mitigated_by=r"SDL_GAMECONTROLLER_IGNORE_DEVICES", covers=r"\b0003:([0-9A-Fa-f]{4}):([0-9A-Fa-f]{4})\."),
    R("usb-volume", "USB audio device reports a tiny hardware volume range", "Audio / USB", "LOW", ("dmesg",),
      (r"Unlikely small volume range",),
      "The device's hardware volume control spans so little that the slider has almost no effect.",
      "Enable WirePlumber's soft mixer (api.alsa.soft-mixer) for the card.", mitigated_by=r"(?i)soft.?mixer"),
    R("jack-server", "jack2 is installed beside PipeWire", "Audio / JACK", "LOW", ("inxi",), (r"Server-\d+: JACK",),
      "jack2 provides libjack, so JACK clients bypass PipeWire.",
      "Replace jack2 with pipewire-jack.", mitigated_by=r"pipewire-jack"),
    R("wifi-tx-cap", "Wi-Fi TX power capped by the access point", "Network / Wi-Fi", "WATCH", ("dmesg",),
      (r"Limiting TX power to \d+",),
      "The access point advertises a transmit-power limit and the driver applies it.",
      "Watch for a lower cap after router or firmware changes."),
    R("link-down", "Wired network link down", "Network / Ethernet", "WATCH", ("dmesg",),
      (r"\b(?:eth|en)\w*: (?:NIC )?Link is Down",),
      "A port that later logs `Link is Up` was negotiating; one that never does has no cable or link partner.",
      "Watch for a cabled port that stays down."),
    R("ipv6-off", "IPv6 administratively disabled", "Network / IPv6", "SETTING", ("dmesg",),
      (r"IPv6: Loaded, but administratively disabled",), "ipv6.disable=1 on the kernel command line."),
    R("cstate-cap", "CPU idle states limited", "CPU / idle", "SETTING", ("dmesg",),
      (r"processor limited to max C-state",), "processor.max_cstate on the kernel command line."),
    R("cmdline", "Kernel command line", "Boot", "NOTE", ("dmesg",),
      (r"^(?:Kernel )?[Cc]ommand line:", r"^Unknown kernel command line parameters "),
      "The command line the kernel booted with, and the parameters it passes on to user space."),
    R("memmap", "Firmware memory map, ACPI table reservations", "Boot / firmware", "NOTE", ("dmesg",),
      (r"BIOS-e820:", r"^e820: ", r"^ACPI: .*(?:table|Reserving)", r"reserve setup_data"),
      "Boilerplate printed on every boot."),
    R("pnp-reserve", "PNP0C02 resource reservations", "Boot / ACPI", "NOTE", ("dmesg",),
      (r"system 00:\w+: \[(?:mem|io) .*\] (?:has been|could not be) reserved", r"PNP0C02", r"Could not reserve \[mem"),
      "Boilerplate printed on every boot."),
    R("pci-crs", "PCI host bridge windows from ACPI", "Boot / PCI", "NOTE", ("dmesg",),
      (r'host bridge windows from ACPI; if necessary, use "pci=(?:nocrs|use_crs)"',),
      "Printed on every x86 boot with ACPI; the closing request to report a bug is part of the standard message."),
    R("mitigations", "CPU vulnerability mitigations", "CPU / security", "NOTE", ("dmesg",),
      ((r"(?i)\b(?:spectre|meltdown|mmio stale data|retbleed|speculative store bypass)\b.*"
       r"(?:mitigation|vulnerable|not affected|disabled)"),
       r"\b(?:MDS|TAA|SRBDS|GDS|SRSO|RFDS|ITS|TSA|VMSCAPE|L1TF|SSB|BHI):", r"(?i)\bmitigations?: "),
      "Posture lines printed on every boot; inxi's Vulnerabilities block summarizes them."),
    R("xhci-quirks", "xHCI quirk masks", "USB", "NOTE", ("dmesg",), (r"xhci_hcd .*(?:hcc params|quirks)",),
      "Boilerplate printed on every boot."),
    R("amdgpu-optional", "amdgpu optional features", "GPU / amdgpu", "NOTE", ("dmesg",),
      ((r"\bamdgpu\b(?!.*(?:\*ERROR\*|in kfd)).*(?:not supported|is not available|runtime pm is manually disabled)"),
       r'Optional firmware "[^"]+" was not found'),
      "Optional firmware or features absent on this GPU; boilerplate. A failed direct firmware load is required "
      "firmware and is not boilerplate."),
    R("unmet-conditions", "systemd unmet conditions", "Boot / systemd", "NOTE", ("dmesg",),
      ((r"was skipped because (?:no trigger condition checks were met|of an unmet condition check|all trigger "
       r"condition checks failed)"),),
      "Units skipped by design on this system (no TPM measurement, no hibernation, and similar)."),
    R("audit", "Audit subsystem", "Kernel / audit", "NOTE", ("dmesg",),
      (r"\baudit[:(](?!.*(?:DENIED|denied|res=failed))",),  # a denial or a failed result is no boilerplate
      "Boilerplate printed on every boot."),
    R("reset-reason", "Previous reset reason: a reboot or power-off", "Boot / platform", "NOTE", ("dmesg",),
      (RESET_REASON + r"(?:" + ROUTINE_RESET + ")",),
      "How the previous boot ended, as the platform recorded it: a reset the system asked for."),
    R("secure-boot", "Secure Boot state", "Boot / Secure Boot", "NOTE", ("dmesg",),
      (r"Secure boot (?:disabled|enabled|could not be determined)",), "The firmware's Secure Boot state."),
    R("call-trace", "Kernel call traces", "Kernel", "NOTE", ("dmesg",), (r"Call Trace:",),
      "Stack traces the kernel prints below a splat, an OOM report, or a hung-task report; the line that heads "
      "each one names the event."),
    R("unit-failed", "A systemd unit failed", "Services", "LOW", ("journal",),
      (r"Failed with result '", r"Failed to start "),
      "",
      "Check `systemctl --failed` and read each unit's journal in the boot it failed in; {s:sec-3} lists the "
      "commands.",
      idents=r"systemd"),
)  # fmt: skip
del R
COMPILED = {r.key: [re.compile(p) for p in r.patterns] for r in RULES}
IDENT_RES = {r.key: re.compile(r.idents) for r in RULES if r.idents}


ERROR_RULES = ("kernel-splat", "kernel-taint", "gpu-hang", "storage-errors", "oom-kill", "reset-abnormal", "core-dump")
KERNEL_ERRORS = ERROR_RULES[:5]  # the error classes journalctl -k shows; resets and core dumps have their own actions
# Identifiers that make a quote traceable to one machine or person, with the placeholder mask() puts in their place.
# Hex digits, not word boundaries, delimit the hex forms, so an identifier stays found after an underscore
# (dev_AC_80_…) or an escape sequence's final letter.
HEX_BEFORE, HEX_AFTER = r"(?<![0-9A-Fa-f])", r"(?![0-9A-Fa-f])"
UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
NIL_UUID = r"0{8}-0{4}-[0-9]0{3}-[0-9a-fA-F]0{3}-0{12}"  # the nil UUID, or a placeholder with only version and variant
UNIT_DASH = r"\\x2d"  # systemd escapes "-" in unit names, as in dev-disk-by\x2duuid-0f8e3a2c\x2d1b4d…
IDENTIFIERS = (
    (re.compile(rf"root=UUID=(?!{NIL_UUID}){UUID}"), "root=UUID=[root UUID]"),  # the root file system, root=UUID=
    (re.compile(rf"\buuid: (?!{NIL_UUID}){UUID}"), "uuid: [DMI UUID]"),  # the DMI system UUID
    (re.compile(rf"{HEX_BEFORE}[0-9a-fA-F]{{8}}-[0-9a-fA-F]{{4}}-domain"), "<USB4 domain ID>-domain"),
    (re.compile(rf"{HEX_BEFORE}(?!{NIL_UUID}){UUID}{HEX_AFTER}"), "[UUID]"),  # any other UUID
    (re.compile(rf"(?:(?<={UNIT_DASH})|{HEX_BEFORE})[0-9a-fA-F]{{8}}(?:{UNIT_DASH}[0-9a-fA-F]{{4}}){{3}}{UNIT_DASH}"
                rf"[0-9a-fA-F]{{12}}{HEX_AFTER}"), "[UUID]"),  # a UUID inside a unit name
    (re.compile(r"(?<=/log/journal/)[0-9a-f]{32}\b"), "[machine ID]"),  # /var/log/journal and /run/log/journal
    # USB serial numbers; a root hub's serial is its PCI address, as in SerialNumber: 0000:c5:00.0
    (re.compile(r"SerialNumber: (?![<\[]|[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$).+"), "SerialNumber: [serial]"),
    (re.compile(rf"{HEX_BEFORE}(?:[0-9A-Fa-f]{{2}}_){{5}}[0-9A-Fa-f]{{2}}{HEX_AFTER}"), "[BT MAC]"),  # underscore form
    (re.compile(rf"{HEX_BEFORE}(?:[0-9A-Fa-f]{{2}}:){{5}}[0-9A-Fa-f]{{2}}{HEX_AFTER}"), "[MAC]"),  # colon form
    (re.compile(r"/home/(?!<)[A-Za-z0-9._-]+"), "/home/<user>"),
    (re.compile(r"(?<!v: )(?<![\w.:/-])(?:25[0-5]|2[0-4]\d|1?\d?\d)"
                r"(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?!\w|\.\d)"), "[IPv4]"),
    (re.compile(r"(?<![\w.%+-])[\w.%+-]+@[\w-]+\."
                r"(?!(?:service|socket|timer|target|mount|slice|scope)\b)[A-Za-z]{2,}\b"), "[email]"),
)  # fmt: skip


# About five lines of monospace; past that, a line is a data dump (a hex buffer, a long command line).
QUOTE_LIMIT = 400  # characters of a log line quoted in the report; the rest is cut with an ellipsis
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")  # CSI sequences a program may have written into a log
# C0 and C1 controls other than tab and newline, and the Unicode line and paragraph separators
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029]")


def mask(text: str) -> str:
    """Return text with every identifier replaced by a placeholder, for quoting in the report.

    The root file system's UUID reads [root UUID] wherever it appears, also as a unit name spells it.
    """
    if STATE.root_uuid:
        escaped = STATE.root_uuid.replace("-", r"\x2d")  # as a unit name spells it
        text = text.replace(STATE.root_uuid, "[root UUID]").replace(escaped, "[root UUID]")
    for pattern, placeholder in IDENTIFIERS:
        text = pattern.sub(placeholder, text)
    return text


def sanitize(text: str) -> str:
    """Return text with ANSI escapes removed, then masked, with other control characters shown as U+FFFD."""
    return CONTROL.sub("\ufffd", mask(ANSI_ESCAPE.sub("", text)))


def quote(text: str) -> str:
    """Return a log line sanitized and cut to QUOTE_LIMIT characters."""
    text = sanitize(text)
    return text if len(text) <= QUOTE_LIMIT else text[: QUOTE_LIMIT - 1] + "…"


def clip(text: str, limit: int) -> str:
    """Return text cut to at most limit characters, with an ellipsis when cut.

    The cut falls at a word boundary unless that keeps under half the limit, as a long path would.
    """
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    word = (cut if text[limit - 1] == " " else cut.rsplit(" ", 1)[0]).rstrip(",;:")
    return (word if len(word) >= limit // 2 else cut) + "…"


QUOTE_CLIP = 160  # characters of a ry-verify record quoted in running text or a command comment: two lines at most
COMMENT_WIDTH = 110  # characters per comment line in a command block; longer lines would shrink its type


def comment(text: str) -> list[str]:
    """Return text as fish comment lines of at most COMMENT_WIDTH characters, broken between words."""
    return textwrap.wrap(text, COMMENT_WIDTH, initial_indent="# ", subsequent_indent="# ", break_on_hyphens=False)


# ── ANALYSIS ──────────────────────────────────────────────────────────
# Turns the parsed inputs into findings, health checks, log statistics, the boot timeline, and actions.
@dataclass
class Match:
    """One input line attributed to a finding."""

    stream: str  # dmesg, journal, inxi, or verify
    no: int  # bug-report line or JSONL line
    text: str  # masked quote
    boot: str = ""
    when: dt.datetime | None = None


@dataclass
class Finding:
    """A finding, watch item, setting, or boilerplate family with its evidence."""

    key: str
    title: str
    area: str
    severity: str
    explanation: str
    action: str
    matches: list[Match] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    fid: str = ""
    anchor: str = ""  # where the register links when the finding has no card: its first ry-verify row

    def count(self, stream: str, boot: str = "") -> int:
        """Return how many evidence lines come from one stream (and, for the journal, one boot)."""
        return sum(1 for m in self.matches if m.stream == stream and (not boot or m.boot == boot))

    def first_line(self) -> str:
        """Return the first evidence line reference, e.g. 'BR 12' or 'VJ 7'."""
        br = [m.no for m in self.matches if m.stream != "verify"]
        vj = [m.no for m in self.matches if m.stream == "verify"]
        return f"BR {min(br)}" if br else (f"VJ {min(vj)}" if vj else "")

    def lines(self) -> str:
        """Return the evidence line references as ranges, e.g. 'BR 12, 40-41 · VJ 7' (the dash is an en dash).

        Each stream lists its first LINE_RUNS ranges and counts the rest, so a table row stays shorter than a page.
        """
        br = sorted({m.no for m in self.matches if m.stream != "verify"})
        vj = sorted({m.no for m in self.matches if m.stream == "verify"})
        parts = [f"BR {ranges(br, LINE_RUNS)}"] if br else []
        return f"{NBSP}· ".join([*parts, f"VJ {ranges(vj, LINE_RUNS)}"] if vj else parts)  # no line opens with ·


@dataclass
class Action:
    """An action the findings call for, with its commands and completion test."""

    aid: str
    title: str
    why: str
    commands: list[str]
    done_when: str


@dataclass
class Model:
    """Everything the report shows, derived from the two inputs."""

    br: BugReport
    vj: VerifyLog
    findings: list[Finding]
    others: list[Finding]  # WATCH, SETTING, NOTE
    milestones: dict[str, float]
    quiet_gap: tuple[float, float] | None
    kernel_start: tuple[dt.datetime, float] | None  # (estimate, half-width in seconds)
    verify_sections: list[tuple[str, str, dict[str, int]]]
    facts: dict[str, str]
    health: list[tuple[str, str, str, str]]
    keyword_lines: dict[str, int]
    actions: list[Action]


def consecutive_runs(numbers: Iterable[int]) -> list[tuple[int, int]]:
    """Return the distinct numbers as (first, last) runs of consecutive values, in ascending order."""
    out: list[tuple[int, int]] = []
    for n in sorted(set(numbers)):
        if out and n == out[-1][1] + 1:
            out[-1] = (out[-1][0], n)
        else:
            out.append((n, n))
    return out


LINE_RUNS = 40  # line ranges a table cell lists per stream: about eight lines in a card's column, never a page


def ranges(numbers: Iterable[int], limit: int = 0) -> str:
    """Return numbers as compact ranges: 1, 3 to 5, 9 reads 1, 3-5, 9 (the dash is an en dash).

    With a limit, only the first that many ranges are listed and the rest counted: limit 2 gives 1, 3-5 and 1 more
    range.
    """
    runs = consecutive_runs(numbers)
    shown = runs[:limit] if limit else runs
    text = ", ".join(f"{a}–{b}" if b > a else str(a) for a, b in shown)
    rest = len(runs) - len(shown)
    return f"{text} and {plural(rest, 'more range')}" if rest else text


def plural(n: int, noun: str, nouns: str = "") -> str:
    """Return a count with its noun in the right number: 1 line, 2 lines; nouns gives an irregular plural."""
    return f"{n:,} {noun if n == 1 else nouns or noun + 's'}"


def no_journal(m: Model) -> str:
    """Return why the report holds no journal entries: none logged, or lines the parser could not read."""
    unread = len(m.br.journal_unparsed)
    return f"{plural(unread, 'journal line')} could not be read" if unread else "no journal entries"


def error_findings(m: Model) -> list[Finding]:
    """Return the findings of the error classes in ERROR_RULES, in register order."""
    return [f for f in m.findings if f.key in ERROR_RULES]


def applicable_rules(br: BugReport) -> list[Rule]:
    """Return the rules whose `requires` pattern (if any) occurs in the bug report."""
    text = "\n".join(br.lines)
    return [r for r in RULES if not r.requires or re.search(r.requires, text)]


def match_rule(
    rules: Sequence[Rule], stream: str, text: str, ident: str = "", *, shutdown: bool = False
) -> Rule | None:
    """Return the first rule matching a line of a stream, honoring process and shutdown-window limits."""
    for rule in rules:
        if stream not in rule.streams or (rule.window == "shutdown" and not shutdown):
            continue
        if rule.idents and not IDENT_RES[rule.key].fullmatch(ident):
            continue
        if any(p.search(text) for p in COMPILED[rule.key]):
            return rule
    return None


def attribute(br: BugReport, rules: Sequence[Rule]) -> dict[str, Finding]:
    """Attribute the dmesg lines, journal entries, and inxi lines a rule knows to that rule's finding."""
    found: dict[str, Finding] = {}

    def add(rule: Rule, match: Match) -> None:
        """Add one evidence line to its rule's finding."""
        f = found.setdefault(
            rule.key, Finding(rule.key, rule.title, rule.area, rule.severity, rule.explanation, rule.action)
        )
        f.matches.append(match)

    for e in br.dmesg:
        rule = match_rule(rules, "dmesg", e.text)
        if rule:
            add(rule, Match("dmesg", e.no, quote(f"[{e.t:12.6f}] {e.text}")))
    previous = [e for e in br.journal if e.boot == "previous"]
    last_prev = max((e.when for e in previous), default=None)
    window = [e for e in previous if last_prev and (last_prev - e.when).total_seconds() <= SHUTDOWN_WINDOW]
    tail = {e.no for e in window} if any(SHUTDOWN_MARK.search(e.text) for e in window) else set()
    for e in br.journal:
        rule = match_rule(rules, "journal", e.text, e.ident, shutdown=e.no in tail)
        if rule is None and e.ident == "kernel":  # kernel warnings repeat in the journal; dmesg rules know them
            rule = match_rule(rules, "dmesg", e.text)
        if rule:
            add(rule, Match("journal", e.no, quote(e.line), e.boot, e.when))
    for n, line in br.inxi:
        rule = match_rule(rules, "inxi", line)
        if rule:
            at, text = inxi_line_at(br, n, line, rule)
            add(rule, Match("inxi", at, quote(text)))
    return found


def inxi_line_at(br: BugReport, start: int, joined: str, rule: Rule) -> tuple[int, str]:
    """Return the line of a joined inxi line where the rule matches, as (line number, text stripped).

    A quote then shows the line its number names, as everywhere else in the report.
    """
    hit = next((h for p in COMPILED[rule.key] if (h := p.search(joined))), None)
    end = 0
    for n, part in br.inxi_parts.get(start, []):
        end += len(part) + 1  # the part and the space that joins the next one
        if hit is None or hit.start() < end:
            return n, part.strip()
    return start, joined.strip()


def apply_mitigations(found: dict[str, Finding], vj: VerifyLog) -> None:
    """Lower a finding to INFO when ry-verify reports its mitigation in place, citing the record.

    Any FAIL, GEN_FAIL, or WARN record about the mitigation keeps the finding as it is (a file in place that the
    running system has not applied, say), as does a record that leaves out a device the evidence names; otherwise the
    last OK record is cited, as runtime checks follow static ones.
    """
    for rule in RULES:
        f = found.get(rule.key)
        if not f or not rule.mitigated_by or SEVERITY_RANK[f.severity] > SEVERITY_RANK["LOW"]:
            continue
        related = [i for i in vj.items if re.search(rule.mitigated_by, i.text)]
        if any(i.counted in ("FAIL", "GEN_FAIL", "WARN") for i in related):
            continue
        hit = next((i for i in reversed(related) if i.status == "OK"), None)
        if hit and rule.covers:
            named = {f"0x{v}/0x{p}".lower() for mt in f.matches for v, p in re.findall(rule.covers, mt.text)}
            hit = hit if all(d in hit.text.lower() for d in named) else None
        if hit:
            f.severity = "INFO"
            f.notes.append(
                f"ry-verify reports the mitigation in place (VJ {hit.no}: {clip(quote(hit.text), QUOTE_CLIP)})."
            )


def hardware_mismatch(vj: VerifyLog) -> VerifyItem | None:
    """Return the preamble record in which ry-verify reports that its profile targets other hardware, if any."""
    return next((i for i in vj.items if i.phase == "preamble" and "Hardware mismatch" in i.text), None)


def actionable(vj: VerifyLog) -> list[VerifyItem]:
    """Return the records that count as FAIL, GEN_FAIL, or WARN, which a fix can clear, the hardware mismatch aside."""
    mismatch = hardware_mismatch(vj)
    return [i for i in vj.items if i.counted in ("FAIL", "GEN_FAIL", "WARN") and i is not mismatch]


def generator_failures(vj: VerifyLog) -> tuple[int, int]:
    """Return ry-verify's generator failures: those its records show, and those it counted beyond them."""
    shown = sum(1 for i in actionable(vj) if i.counted == "GEN_FAIL")
    return shown, max(vj.totals["gen_fail"] - shown, 0)


CPU_VENDORS = (
    ("AMD", re.compile(r"\b(?:AMD|Ryzen|EPYC|Athlon|Threadripper)\b", re.IGNORECASE)),
    ("Intel", re.compile(r"\b(?:Intel|Xeon|Celeron|Pentium)\b|\bCore\(TM\)", re.IGNORECASE)),
)


def cpu_vendor(cpu: str) -> str:
    """Return the vendor a CPU model string names, AMD or Intel, or "" when it names neither."""
    return next((name for name, pattern in CPU_VENDORS if pattern.search(cpu)), "")


def other_machine(vj: VerifyLog, cpu: str) -> tuple[VerifyItem, str, str] | None:
    """Return the hardware-mismatch record with its CPU vendor and the bug report's when the two vendors differ.

    ry-verify names the CPU it detected when its profile targets other hardware; a vendor other than the one in the
    bug report's inxi block shows that the two logs come from different machines.
    """
    item = hardware_mismatch(vj)
    detected = re.search(r"\bdetected: (.+?)(?:\s+—\s|$)", item.text) if item else None
    if item is None or detected is None:
        return None
    theirs, ours = cpu_vendor(detected.group(1)), cpu_vendor(cpu)
    return (item, theirs, ours) if theirs and ours and theirs != ours else None


def verify_findings(vj: VerifyLog) -> list[Finding]:
    """Group ry-verify FAIL, GEN_FAIL, and WARN records by section into findings; the hardware mismatch is not one."""
    groups: dict[tuple[str, str, str], list[VerifyItem]] = {}
    for item in actionable(vj):
        groups.setdefault((item.counted, item.phase, item.section), []).append(item)
    out = []
    for (status, phase, section), items in groups.items():
        sev = "LOW" if status == "WARN" else "MED"
        title = f"ry-verify {status}: {section_title(section)} ({phase})"
        what = (
            f"it could not generate the expected content for these {phase} checks (GEN_FAIL)"
            if status == "GEN_FAIL"
            else f"these {phase} checks reported {status}"
        )
        f = Finding(
            f"verify-{status}-{phase}-{section}",
            title,
            "ry-verify / " + section_title(section),
            sev,
            f"ry-verify {lit(vj.meta('version'))} checks the managed configuration; {what}.",
            "Fix each listed item, then run `~/ry-install/ry-verify.fish --verify` again.",
        )
        f.matches = [Match("verify", i.no, quote(f"{i.status}: {i.text}")) for i in items]
        f.anchor = verify_anchor(phase, section)
        out.append(f)
    return out


def section_title(section: str) -> str:
    """Return a ry-verify section name in sentence case (WIFI STATE -> Wi-Fi state)."""
    return section.capitalize().replace("Wifi", "Wi-Fi").replace("Preamble", "Before the checks")


def verify_anchor(phase: str, name: str) -> str:
    """Return the link target of a ry-verify section's first failure or warning, such as ryv-runtime-wifi-state."""
    return f"ryv-{phase}-{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}"


def root_uuid(br: BugReport) -> str:
    """Return the root file system's UUID from the kernel command line, or "" without one (or with the nil UUID)."""
    root = re.search(rf"root=UUID=(?!{NIL_UUID})({UUID})", br.cmdline)
    return root.group(1) if root else ""


def register_order(f: Finding) -> tuple[int, int, int, str]:
    """Return a finding's place in the register: severity, then the first line its First line column shows."""
    br = [x.no for x in f.matches if x.stream != "verify"]
    vj = [x.no for x in f.matches if x.stream == "verify"]
    return (
        (SEVERITY_RANK[f.severity], 0, min(br), f.key)
        if br
        else (SEVERITY_RANK[f.severity], 1, min(vj, default=0), f.key)
    )


MILESTONES = (
    ("initrd starts", r"Run /init as init process"),
    ("init starts", r"Run (?!/init )\S+ as init process"),  # without an initramfs: /sbin/init, say
    ("Switching root", r"systemd\[1\]: Switching root"),
    (
        "Root mounted",
        (
            r"EXT4-fs \([^)]+\): mounted filesystem|XFS \([^)]+\): Ending clean mount|BTRFS info .*: "
            r"(?:first mount|enabling ssd)|F2FS-fs \([^)]+\): Mounted"
        ),
    ),
    ("Wi-Fi associated", r"\bwl\w+: associated"),
)


QUIET_MIN = 1.0  # s; a shorter stretch without output before the root mount is no quiet gap (pauses: PAUSE_MIN)
BOOT_FIRST = 1.0  # s; a ring buffer whose first line comes later has lost the start of the boot


def holds_boot(br: BugReport) -> bool:
    """Return whether dmesg still holds the boot: its first line comes within BOOT_FIRST of the kernel start."""
    return bool(br.dmesg) and br.dmesg[0].t <= BOOT_FIRST


def timeline(br: BugReport) -> tuple[dict[str, float], tuple[float, float] | None]:
    """Return boot milestones (seconds since kernel start) and the longest quiet gap up to the root mount.

    The root mount is the mount that names the root UUID; failing that, the first mount up to switch-root that names
    no other UUID. The gap ends at the root mount, else at switch-root, and lasts QUIET_MIN at least. A ring buffer
    that has lost the boot (holds_boot()) yields neither.
    """
    marks: dict[str, float] = {}
    if not holds_boot(br):
        return marks, None
    root = root_uuid(br)
    for name, pattern in MILESTONES:
        hits = [e for e in br.dmesg if re.search(pattern, e.text)]
        if name == "Root mounted":
            own = [e for e in hits if root and root in e.text]
            switch = marks.get("Switching root", float("inf"))
            hits = own or [e for e in hits if e.t <= switch and not (root and re.search(UUID, e.text))]
        if hits:
            marks[name] = hits[0].t
    limit = marks.get("Root mounted", marks.get("Switching root", 0.0))
    times = [e.t for e in br.dmesg if e.t <= limit]
    gaps = [(b - a, a, b) for a, b in itertools.pairwise(times)]
    best = max(gaps, default=None)
    quiet = (best[1], best[2]) if best and best[0] >= QUIET_MIN else None
    return marks, quiet


JOURNALD_LINE = re.compile(r"systemd-journald\[\d+\]: ")  # journald's own first line: it reads kmsg from here on
SUSPEND_LINE = re.compile(r"PM: suspend entry")  # dmesg time stops while the system sleeps


def kernel_pairs(br: BugReport) -> list[tuple[float, dt.datetime, bool]]:
    """Return the kernel messages logged once in dmesg and once in the current journal, before the first suspend.

    Each gives its dmesg time, its journal time, and whether it bounds the kernel start from below as well: journald
    stamps a kernel message when it reads it, so one printed before journald's first line in dmesg carries a later
    time. Messages after the first suspend are left out, as dmesg time stops while the system sleeps.
    """
    kernel = [e for e in br.journal if e.boot == "current" and e.ident == "kernel"]
    in_dmesg, in_journal = Counter(e.text for e in br.dmesg), Counter(e.text for e in kernel)
    offsets = {e.text: e.t for e in br.dmesg if in_dmesg[e.text] == 1}
    reads = next((e.t for e in br.dmesg if JOURNALD_LINE.match(e.text)), None)
    sleeps = next((e.t for e in br.dmesg if SUSPEND_LINE.match(e.text)), None)
    pairs = []
    for e in kernel:
        t = offsets.get(e.text)
        if in_journal[e.text] != 1 or t is None or (sleeps is not None and t >= sleeps):
            continue
        pairs.append((t, e.when, reads is None or t >= reads))
    return pairs


def kernel_bounds(pairs: Sequence[tuple[float, dt.datetime, bool]]) -> tuple[dt.datetime | None, dt.datetime | None]:
    """Return the earliest and latest kernel start the pairs allow; None for a side no pair bounds.

    Each pair bounds the start to one second, as the journal stamps whole seconds.
    """
    starts = [(when - dt.timedelta(seconds=t), below) for t, when, below in pairs]
    lo = max((start for start, below in starts if below), default=None)
    hi = min((start + dt.timedelta(seconds=1) for start, _ in starts), default=None)
    return lo, hi


def kernel_start(br: BugReport) -> tuple[dt.datetime, float] | None:
    """Estimate the wall-clock kernel start from kernel messages found once in dmesg and once in the journal.

    The estimate is the middle of the window every pair allows, with half its width; a message printed more than once
    cannot be paired and is skipped.
    """
    lo, hi = kernel_bounds(kernel_pairs(br))
    if lo is None or hi is None or hi < lo:
        return None
    half = (hi - lo).total_seconds() / 2
    return lo + dt.timedelta(seconds=half), half


def verify_sections(vj: VerifyLog) -> list[tuple[str, str, dict[str, int]]]:
    """Return per-section status counts in log order, phase by phase."""
    order: dict[tuple[str, str], dict[str, int]] = {}
    for item in vj.items:
        if item.phase == "preamble":
            continue
        counts = order.setdefault((item.phase, item.section), dict.fromkeys(STATUS_ORDER, 0))
        counts[item.counted] += 1
    return [(phase, section, counts) for (phase, section), counts in order.items()]


INXI_FACTS = (  # (key, inxi block, pattern, template); patterns run on the block's logical lines joined by newlines
    ("machine", "Machine", r"System:\s*(.+?)\s+product:\s*(.+?)\s+(?:v:|serial:)", "{0} {1}"),
    (  # inxi 3.3 prints "Firmware: UEFI vendor: … v: … date: …", older releases "UEFI: … v: … date: …"
        "firmware",
        "Machine",
        r"\b(UEFI-\[Legacy\]|UEFI|BIOS)(?:\s+vendor)?:\s*(.+?)\s+v:\s*(.+?)(?:\s+rev:\s*\S+)?\s+date:\s*(\S+)",
        "{0}: {1} {2} ({3})",
    ),
    ("cpu", "CPU", r"\bmodel:\s*(.+?)\s+bits:", "{0}"),
    ("kernel", "System", r"Kernel:\s*(\S+)", "{0}"),
    ("distro", "System", r"Distro:\s*(.+?)(?:\s+base:.*)?$", "{0}"),
    ("desktop", "System", r"Desktop:\s*(.+?)\s+v:\s*(\S+)", "{0} {1}"),
    ("gpu", "Graphics", r"Device-1:\s*(.+?)\s+(?:vendor|driver):", "{0}"),  # -x adds the board vendor before driver
    ("mesa", "Graphics", r"\bmesa v:\s*(\S+)", "{0}"),
    ("pipewire", "Audio", r"PipeWire v:\s*(\S+)", "{0}"),
    ("memory", "Info", r"Memory:\s*total:\s*([\d.]+ \w+)(?:.*?available:\s*([\d.]+ \w+))?", "{0} total, {1} available"),
    ("swap", "Swap", r"type:\s*zram\s+size:\s*([\d.]+ \w+)", "zram {0}"),
    ("temperatures", "Sensors", r"System Temperatures:\s*(.+)$", "{0}"),
    ("fans", "Sensors", r"Fan Speeds \(rpm\):\s*(.+)$", "{0}"),
    ("drive", "Drive-1", r"ID-1:\s*/dev/(\w+)\s.*?model:\s*(.+?)(?=\s+(?:drive vendor|drive model|family|size):|$)",
     "{0} {1}"),
    ("smart", "Drive-1", r"health:\s*(\w+)", "{0}"),
    ("drive-temp", "Drive-1", r"\btemp:\s*([\d.]+ C)", "{0}"),
    ("written", "Drive-1", r"written(?:-units)?:\s*(?:[\d,]+\s+\[)?([\d.]+ \w+)", "{0} written"),  # NVMe: units [size]
)  # fmt: skip


def inxi_blocks(br: BugReport) -> dict[str, str]:
    """Return inxi's top-level blocks (System, Machine, CPU, …) as newline-joined logical lines.

    Drive-1 holds the Drives block's first drive, from its ID-1 line up to the next drive, with its SMART lines.
    """
    blocks: dict[str, list[str]] = {}
    name = ""
    for _, line in br.inxi:
        if re.match(r"^[A-Z][A-Za-z]+:\s*$", line):
            name = line.strip(": ")
            blocks.setdefault(name, [])
        elif name:
            blocks[name].append(line.strip())
    out = {k: "\n".join(v) for k, v in blocks.items()}
    if first := re.search(r"^ID-1:.*?(?=^ID-\d+:|\Z)", out.get("Drives", ""), re.MULTILINE | re.DOTALL):
        out["Drive-1"] = first.group(0)
    return out


def inxi_facts(br: BugReport) -> dict[str, str]:
    """Return the facts inxi reports, keyed by name; absent facts are left out. Firmware dates read as ISO dates."""
    blocks = inxi_blocks(br)
    facts = {}
    for key, block, pattern, template in INXI_FACTS:
        m = re.search(pattern, blocks.get(block, ""), re.MULTILINE)
        if m:
            groups = [g or "?" for g in m.groups()]
            text = mask(template.format(*groups)).replace(", ? available", "")
            if key == "firmware":
                text = re.sub(r"\((\d{2})/(\d{2})/(\d{4})\)$", r"(\3-\1-\2)", text)  # inxi prints DMI's MM/DD/YYYY
            facts[key] = re.sub(r"\s+N/A\b", "", text) if key in ("machine", "drive") else text
    if vuln := vulnerability_counts(blocks.get("CPU", "")):
        facts["vulnerabilities"] = vuln
    return facts


def vulnerability_counts(cpu_block: str) -> str:
    """Return inxi's CPU vulnerability rows as counts, or "" when the block has none.

    inxi prints one `Type: <name> status: …` or `Type: <name> mitigation: …` row per sysfs entry, and labels a value
    that does not open with "Mitigation:" a status, so a KVM: prefix is read past. A mitigation that leaves a part
    vulnerable (spectre_v2 with BHI: Vulnerable, say) counts as partly vulnerable and is named.
    """
    rows = re.findall(r"Type:\s*(\S+)\s+(status|mitigation):\s*(.+?)(?=\s+Type:\s|$)", cpu_block, re.MULTILINE)
    if not rows:
        return ""
    counts = {"not affected": 0, "mitigated": 0, "partly vulnerable": 0, "vulnerable": 0, "unknown": 0}
    partly = []
    for name, kind, value in rows:
        text = value.removeprefix("KVM: ")
        if kind == "mitigation" or text.startswith("Mitigation"):
            weak = re.findall(r"([\w-]+):? [Vv]ulnerable\b", text)  # BHI: Vulnerable, SMT vulnerable
            if weak or re.search(r"(?i)\bvulnerable\b", text):
                counts["partly vulnerable"] += 1
                partly.append(f"{name}: {', '.join(weak)}" if weak else name)
            else:
                counts["mitigated"] += 1
        elif text.startswith("Not affected"):
            counts["not affected"] += 1
        elif text.startswith(("Vulnerable", "Processor vulnerable")):
            counts["vulnerable"] += 1
        else:
            counts["unknown"] += 1
    named = {"partly vulnerable": f" ({'; '.join(partly)})"} if partly else {}
    return ", ".join(
        f"{n} {k}{named.get(k, '')}" for k, n in counts.items() if n or k in ("not affected", "mitigated", "vulnerable")
    )


def dmesg_facts(br: BugReport) -> dict[str, str]:
    """Return firmware, memory, and boot facts the kernel ring buffer states."""
    text = "\n".join(e.text for e in br.dmesg)
    out = {}
    probes = (
        ("microcode", r"microcode: (?:updated early|Current revision):? (?:0x[0-9a-f]+ -> )?(0x[0-9a-f]+)"),
        ("vram", r"(\d+)M of VRAM memory ready"),
        ("gtt", r"(\d+)M of GTT memory ready"),
        ("dmub", r"DMUB firmware.*version[=:]\s*(0x[0-9a-fA-F]+)"),
        ("vcn", r"VCN firmware Version ENC: ([\d.]+) DEC: (\d+)"),
        ("secure-boot", r"Secure boot (disabled|enabled|could not be determined)"),
    )
    for key, pattern in probes:
        m = re.search(pattern, text)
        if m:
            out[key] = " / ".join(m.groups())
    out["smu"] = "initialized" if re.search(r"SMU is initialized", text) else ""
    return {k: v for k, v in out.items() if v}


def verify_summary(vj: VerifyLog) -> str:
    """Return ry-verify's totals as one line, or where a run that never reached its checks stopped.

    OK splits by phase when both phase records agree with it; GEN_FAIL appears when counted; the preamble records the
    totals leave out and a phase that stopped before its checks follow.
    """
    if stop := vj.stopped:
        return f"stopped before its checks; VJ {stop.no}: {clip(quote(stop.text), QUOTE_CLIP)}"
    totals = vj.totals
    static, runtime = vj.phase_results.get("static", {}), vj.phase_results.get("runtime", {})
    by_phase = (static.get("ok", 0), runtime.get("ok", 0))
    split = ""
    if static and runtime and sum(by_phase) == totals["ok"]:
        split = f" ({by_phase[0]:,} static, {by_phase[1]:,} runtime)"
    gen = f", {totals['gen_fail']:,} GEN_FAIL" if totals["gen_fail"] else ""
    info = sum(1 for i in vj.items if i.status == "INFO")
    outside = vj.outside_totals()
    stops = "".join(
        f"; the {i.phase} phase stopped before its checks (VJ {i.no}: {clip(quote(i.text), QUOTE_CLIP)})"
        for i in vj.phase_stops
    )
    return (
        f"{totals['ok']:,} OK{split}; {totals['fail']:,} FAIL, {totals['warn']:,} WARN{gen}; {info:,} INFO"
        + (f"; {', '.join(outside)} in the preamble, outside the totals" if outside else "")
        + stops
    )


def verify_health(m: Model) -> list[tuple[str, str, str, str]]:
    """Return the ry-verify, error-class, kernel-ring, and unit-failure health rows: check, result, detail, source."""
    vj = m.vj
    verdict_, exit_ = vj.status
    errors = error_findings(m)
    keyword = plural(m.keyword_lines.get("dmesg", 0), "line")
    in_dmesg = sum(1 for r in RULES if r.key in ERROR_RULES and "dmesg" in r.streams)
    classes = f"{len(ERROR_RULES)} classes checked" if m.br.journal else f"{in_dmesg} classes; {no_journal(m)}"
    rows = [
        (f"ry-verify {vj.meta('version')}", f"{verdict_}, {exit_}", verify_summary(vj), "ry-verify log"),
        ("Error classes", ", ".join(f.fid for f in errors) or "None seen", classes,
         "dmesg, journal" if m.br.journal else "dmesg"),
        ("Kernel ring buffer", plural(len(m.br.dmesg), "line"), f"{keyword} with failure keywords", "dmesg"),
    ]  # fmt: skip
    failed = unit_failures(m)
    if failed is None:
        present = sum(1 for s in ("journal-current", "journal-previous") if s in m.br.sections)
        empty = (
            ("journal sections empty" if present > 1 else "journal section empty") if present else "no journal section"
        )
        rows.append(
            ("Unit failures", "not read", no_journal(m), "journal")
            if m.br.journal_unparsed
            else ("Unit failures", "no journal entries", empty, "journal")
        )
    else:
        current = sum(1 for boot, _ in failed if boot == "current")
        owners = ", ".join(sorted(dict.fromkeys(fid for _, fid in failed if fid), key=id_order))
        result = f"{current:,} current boot, {len(failed) - current:,} previous"
        unread = no_journal(m) if m.br.journal_unparsed else ""
        rows.append(("Unit failures", result, "; ".join(x for x in (owners, unread) if x), "journal"))
    return rows


def unit_failures(m: Model) -> list[tuple[str, str]] | None:
    """Return (boot, covering finding ID) per unit failure in the journal, or None when it holds no entries.

    systemd logs one "Failed with result" line per failure; the "Failed to start" line beside it is not counted.
    """
    if not m.br.journal:
        return None
    owner = {mt.no: f.fid for f in m.findings for mt in f.matches if mt.stream == "journal"}
    lines = [e for e in m.br.journal if e.ident == "systemd" and UNIT_RESULT.match(e.text)]
    return [(e.boot, owner.get(e.no, "")) for e in lines]


def gpu_health(m: Model) -> list[tuple[str, str, str, str]]:
    """Return the GPU memory and amdgpu firmware health rows; the boot timing is in the timeline table."""
    d, rows = m.facts, []
    if "vram" in d:
        gtt = f"GTT {int(d['gtt']):,} MiB" if "gtt" in d else "GTT not logged"
        rows.append(("GPU memory", f"VRAM {int(d['vram']):,} MiB", gtt, "dmesg"))
    labels = (("smu", "SMU {}"), ("dmub", "DMUB {}"), ("vcn", "VCN ENC/DEC {}"))
    fw = [label.format(d[key]) for key, label in labels if key in d]
    if fw:
        rows.append(("amdgpu firmware", fw[0], ", ".join(fw[1:]), "dmesg"))
    return rows


def sensor_temps(text: str) -> list[str]:
    """Return inxi's CPU, board, and GPU temperatures as "cpu 49.6 C", in the order inxi prints them."""
    found = re.findall(r"\b(cpu|mobo|gpu)\S*:(?:\s+\S+\s+temp:)?\s+([\d.]+ C)\b", text)
    return [f"{name} {value}" for name, value in found]


def hardware_health(m: Model) -> list[tuple[str, str, str, str]]:
    """Return the firmware, CPU, memory, sensor, and drive health rows."""
    f, rows = m.facts, []
    for key, label in (
        ("firmware", "Firmware"),
        ("secure-boot", "Secure Boot"),
        ("microcode", "CPU microcode"),
        ("vulnerabilities", "CPU vulnerabilities"),
        ("memory", "Memory"),
        ("swap", "Swap"),
        ("temperatures", "Temperatures"),
        ("fans", "Fan speeds"),
    ):
        if key in f and f[key] != "N/A":  # inxi's N/A states nothing to check
            value = f[key]
            if key == "temperatures":
                value = ", ".join(sensor_temps(value)) or value
            rows.append((label, value, "", "dmesg" if key in ("microcode", "secure-boot") else "inxi"))
    if "drive" in f:
        detail = " · ".join(x for x in (f.get("drive-temp", ""), f.get("written", "")) if x)
        rows.append((f"Drive {f['drive']}", f.get("smart", "") or "no SMART data", detail, "inxi"))
    return rows


def health_rows(m: Model) -> list[tuple[str, str, str, str]]:
    """Return (check, result, detail, source) rows for the system-health table, from facts the inputs state."""
    return [*verify_health(m), *gpu_health(m), *hardware_health(m)]


def keyword_lines(br: BugReport, boilerplate: set[int]) -> dict[str, int]:
    """Count the dmesg lines and the journal entries of each boot holding a failure keyword, leaving out NOTE lines.

    Lines count as the report counts them: a dmesg or journal continuation belongs to its entry.
    """
    found = {
        "dmesg": [(e.no, e.text) for e in br.dmesg],
        "journal-current": [(e.no, e.text) for e in br.journal if e.boot == "current"],
        "journal-previous": [(e.no, e.text) for e in br.journal if e.boot == "previous"],
    }
    return {k: sum(1 for n, text in v if n not in boilerplate and KEYWORD_RE.search(text)) for k, v in found.items()}


# dmesg subsystems: the prefix a kernel message starts with names the driver, facility, or device that printed it.
# The first subsystem whose pattern matches the whole prefix, in lowercase, claims the line; a line whose prefix no
# pattern matches, or that has none, counts as Other.
SUBSYSTEMS = (
    ("GPU", ("amdgpu", "drm", "radeon", "i915", "xe", "nouveau", r"nvidia\w*", "simpledrm", "efifb", "fbcon",
             "vgaarb")),
    ("USB and input", (r"usb\w*", "usb-storage", "hub", "xhci_hcd", r"[eo]hci\w*", r"hid\w*", r"hid-[\w-]+", "input",
                       r"i2c_hid\w*", "i8042", "psmouse", "atkbd", r"serio\d*", "uas", r"typec\w*", r"ucsi\w*",
                       "thunderbolt")),
    ("Storage", (r"nvme\d*(?:n\d+)?", r"ata\d*", "scsi", "sd", "sr", "block", "ext4-fs", "xfs", "btrfs", "f2fs-fs",
                 "fat-fs", "squashfs", r"zram\d*", r"dm-\d+", "device-mapper", r"md\d*", r"loop\d*", "virtio_blk",
                 r"[sv]d[a-z]+", "fuse", "blk_update_request", "print_req_error")),
    ("Network", (r"r81\d\d", "igc", "igb", "e1000e?", r"mt7\w+", "iwlwifi", r"ath\d+k\w*", r"rtw\w+", "brcmfmac",
                 "cfg80211", "mac80211", r"wlan\d+", r"wl[opsx]\w+", r"en[opsx]\w+", r"eth\d+", "net", "ipv6",
                 "netlabel", r"nf_\w+", r"xt_\w+", "bridge", "tun", "wireguard", "virtio_net", "tcp", r"tcp_\w+",
                 "udp", "udp-lite", "ip", "mptcp", "table-perturb")),
    ("Bluetooth", ("bluetooth", r"bt\w+", r"hci\d+")),
    ("ACPI and platform", ("acpi", "dmi", "secure", "pnp", "system", "platform", r"thermal\w*", "button", "battery",
                           "ac", "amd_pmc", "amd_pmf", r"sp5100[-_]tco", "efi", r"tpm\w*", "ccp", "rtc_cmos",
                           r"i2c_designware\w*")),
    ("Audio", (r"snd\w*", r"acp(?!i)\w*", r"soundwire\w*", r"sof-audio\w*")),
    ("PCI and IOMMU", (r"pci\w*", "pci-dma", "pcieport", "acpiphp", "amd-vi", "amd_iommu", "iommu", "dmar",
                       "virtio-pci")),
    ("CPU and memory", ("x86", r"cpu\d*", "smpboot", "smp", "microcode", r"spectre v\d", "speculative", "mitigations",
                        "mds", "taa", "mmio", "srbds", "gds", "its", "rfds", "bhi", "ssb", "l1tf", "tsa", "transient",
                        "vmscape", "retbleed", "rcu", "clocksource", "tsc", "apic", "ioapic", "x2apic", "e820",
                        "bios-e820", "bios-provided", "memory", "numa", "percpu", "pcpu-alloc", "setup_percpu",
                        "hugetlb", r"kvm[\w-]*", "hypervisor", "amd[-_]pstate", "intel_pstate", "acpi_cpufreq",
                        "cpufreq", "cpuidle", "zswap",
                        "mce", r"edac\w*", "perf", "performance", "rapl", "virt", "pv", "calibrating", "mtrr", "nx",
                        "using", "last", "zone", "dma", "dma32", "normal", "device", "on", "node", "node_data",
                        "initmem", "early", "movable", "fallback", "built", "policy", "mem", "slub", "last_pfn",
                        "ramdisk", "faking", "dentry", "inode-cache", "mount-cache", "mountpoint-cache", "workingset",
                        "free", "demotion")),
    ("systemd and audit", (r"systemd(?:-[\w-]+)?", "audit", r"kauditd\w*")),
    ("Kernel core", ("linux", "command", "kernel", "booting", "console", "printk", "random", "ftrace", "lsm",
                     "landlock", "selinux", "apparmor", "yama", "bpf", "key", "io", "vfs", "devtmpfs", "pid_max",
                     "signal", "timer", "posixtimers", "dynamic", "nr_irqs", "run", "write", "freeing",
                     "workqueue", "clk", "sched_clock", "integrity", "ima", "evm", "deferred_init", "pps_core", "ptp",
                     "clockevents", "hrtimer", "watchdog", "nmi", "trace", "rodata_test", "futex", "unpacking",
                     "pvm-pv")),
)  # fmt: skip
SUBSYSTEM_RES = tuple((name, re.compile("|".join(patterns))) for name, patterns in SUBSYSTEMS)
OTHER = "Other"
# Messages whose first word names no subsystem, known by their wording: the error classes and kernel splats
SPLAT_LINE = re.compile(
    r"(?:BUG|Oops|WARNING|UBSAN):|general protection fault|Call Trace:|RIP: |Code: |Modules linked in:"
    r"|Hardware name: |---\[ end trace|INFO: task .+ blocked|-+\[ cut here \]|CPU: \d+ (?:UID: \d+ )?PID: "
    r"|Tainted: \[|(?:R[A-D]X|R[SD]I|R[SB]P|R(?:0[89]|1[0-5])|[CDEFGS]S|CR[0-4]|DR[0-7]|PKRU): "
    r"|task:\S+ +state:|\"echo 0 > "
)  # a kernel splat's lines, matched from their start: banner, task, registers, trace


DMESG_WORDING = (
    (re.compile(r"\[drm:"), "GPU"),  # [drm:amdgpu_job_timedout [amdgpu]] *ERROR* …
    (re.compile(r"(?:Buffer )?I/O error\b|[\w ]+ error, dev \S+, sector "), "Storage"),  # the block layer's errors
    (re.compile(r"Out of memory|oom-kill:|oom_reaper:|Memory cgroup out of memory|\S+ invoked oom-killer:"
                r"|(?:\{\d+\})?\[Hardware Error\]"), "CPU and memory"),  # and machine-check reports
    (re.compile(r"No NUMA configuration found|\.\.\.\. node +#\d+, CPUs:|software IO TLB|IPI shorthand broadcast"
                r"|page_reporting|\S+ \(\d+\): drop_caches: "), "CPU and memory"),  # memory set-up and cache drops
    (re.compile(r"Initiali[sz]e system trusted keyrings|Asymmetric key parser|Loading compiled-in X\.509"
                r"|registered taskstats|Serial: 8250|\S+: ttyS\d+ at "), "Kernel core"),  # keys, accounting, serial
    (re.compile(r"Mirror/redirect action on|u32 classifier|Actions configured|Initializing XFRM netlink socket"),
     "Network"),  # traffic control and IPsec set-up
    (SPLAT_LINE, "Kernel core"),  # a splat and its lines: banner, task, registers
)  # fmt: skip
DMESG_PREFIXES = (
    re.compile(r"\[(mem) "),  # [mem 0x…-0x…] available for PCI devices: the memory map
    re.compile(r"\[(\w+)\]"),  # [drm] …
    re.compile(r"platform (acp)_"),  # platform acp_asoc_acp70.0: …, the audio co-processor's machine device
    re.compile(r"([\w.-]+?)(?:/[\w.-]+)?(?:\[\d+\])?:\s"),  # ACPI: …, x86/fpu: …, systemd[1]: …
    re.compile(r"([\w.-]+) [\w.:/-]+(?: [\w.-]+)?:\s"),  # amdgpu 0000:c5:00.0: …, usb 1-1: …, r8169 … enp193s0: …
    re.compile(r"([\w-]+) \([^)]*\):\s"),  # EXT4-fs (nvme0n1p2): …
    re.compile(r"(Spectre V\d|Speculative)\b[\w ]*?\s?:"),  # CPU mitigation lines, a space before the colon
    re.compile(r"([\w.-]+)"),  # else the first word: ACPI BIOS Warning …, TSC deadline timer …
)


def subsystem(text: str) -> str:
    """Return the subsystem a dmesg message belongs to by its wording or the prefix it starts with, or Other."""
    if known := next((name for pattern, name in DMESG_WORDING if pattern.match(text)), None):
        return known
    hit = next((m for pattern in DMESG_PREFIXES if (m := pattern.match(text))), None)
    prefix = hit.group(1).lower() if hit else ""
    return next((name for name, pattern in SUBSYSTEM_RES if pattern.fullmatch(prefix)), OTHER)


def subsystem_lines(br: BugReport) -> dict[str, list[DmesgEntry]]:
    """Return the dmesg lines per subsystem in order of their first line (by time, then line), Other last.

    An indented line continues the message above it (a memory-zone table, RCU settings) and joins its subsystem.
    """
    groups: dict[str, list[DmesgEntry]] = {}
    name = OTHER
    for e in br.dmesg:
        name = name if e.text[:1].isspace() else subsystem(e.text)
        groups.setdefault(name, []).append(e)
    order = sorted(groups, key=lambda k: (k == OTHER, min(e.t for e in groups[k]), groups[k][0].no))
    return {k: groups[k] for k in order}


def journal_processes(br: BugReport) -> list[tuple[str, int, int]]:
    """Return (process, current-boot entries, previous-boot entries) per journal process, most entries first."""
    counts: dict[str, list[int]] = {}
    for e in br.journal:
        counts.setdefault(e.ident, [0, 0])[e.boot == "previous"] += 1
    return sorted(((k, c, p) for k, (c, p) in counts.items()), key=lambda r: (-(r[1] + r[2]), r[0]))


FISH_SAFE = re.compile(r"[\w@%+=:,./-]+")  # characters fish reads literally in a bare word


def fish_quote(text: str) -> str:
    """Return text as one fish word: bare when safe, else single-quoted with backslashes and quotes escaped."""
    if FISH_SAFE.fullmatch(text):
        return text
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


# The unit a systemd failure names, in "<unit>: Failed with result '…'" or "Failed to start <unit> - <description>";
# PID 1 is the system manager
FAILED_UNIT = re.compile(r"\bsystemd\[(\d+)\]: (?:(.+?): Failed with result '|Failed to start (\S+\.[a-z]+) - )")
UNIT_RESULT = re.compile(r"(.+?): Failed with result '([^']+)'")  # systemd's own message, after the unit name


CORE_PID = re.compile(r"\bProcess (\d+) \(")  # systemd-coredump names the crashed process by PID
MAX_UNIT_COMMANDS = 6  # journalctl commands per failed unit, coredumpctl info per dump; the rest are counted


def failed_units(m: Model) -> list[tuple[str, str, bool]]:
    """Return (unit, boot, user manager) for each failure the unit-failed finding quotes, current boot first.

    A "Failed to start" line names its unit too, for a failure whose result line another rule claims.
    """
    found = {
        (hit.group(2) or hit.group(3), mt.boot, hit.group(1) != "1")
        for f in m.findings
        if f.key == "unit-failed"
        for mt in f.matches
        if (hit := FAILED_UNIT.search(mt.text))
    }
    return sorted(found, key=lambda u: (u[1] != "current", u[2], u[0]))


def unit_commands(units: Sequence[tuple[str, str, bool]]) -> list[str]:
    """Return systemctl --failed (no unit stays failed), then journalctl for each unit in its boot and manager."""
    cmds = ["systemctl --failed", *(["systemctl --user --failed"] if any(u for _, _, u in units) else [])]
    for name, boot, user in units[:MAX_UNIT_COMMANDS]:
        which = "-b -1" if boot == "previous" else "-b"
        cmds.append(f"journalctl {which} {'--user-unit' if user else '-u'} {fish_quote(name)}")
    if len(units) > MAX_UNIT_COMMANDS:
        cmds.append(f"# {len(units) - MAX_UNIT_COMMANDS} more: {section_ref('sub-7.6')}")
    return cmds


def verify_action(m: Model) -> Action | None:
    """Return the action for ry-verify's failures and warnings: its own suggested fixes as comments, then a rerun."""
    fails = [f for f in m.findings if f.key.startswith("verify-")]
    shown, more = generator_failures(m.vj)
    if not fails and not more:
        return None
    sections = {(i.phase, i.section) for i in actionable(m.vj)}
    stop = m.vj.stopped
    cmds = comment(f"ry-verify stopped (VJ {stop.no}): {clip(quote(stop.text), QUOTE_CLIP)}") if stop else []
    for hint in m.vj.hints:
        cmds += comment(f"ry-verify suggests (VJ {hint.no}): {clip(quote(hint.text), QUOTE_CLIP)}")
    cmds.append("~/ry-install/ry-verify.fish --verify")
    kinds = "FAIL, GEN_FAIL, or WARN" if shown else "FAIL or WARN"
    why = f"{plural(len(fails), 'finding')} in {plural(len(sections), 'ry-verify section')} with {kinds}"
    if more:  # generator failures ry-verify counted without a record that shows them
        failed = f"{plural(more, 'more generator failure' if shown else 'generator failure')} (GEN_FAIL)"
        why = f"{why}; ry-verify counted {failed}" if fails else f"ry-verify logged {failed}"
    done = f"ry-verify exits 0 and logs no {'FAIL, WARN, or GEN_FAIL' if shown or more else 'FAIL or WARN'}"
    if hardware_mismatch(m.vj):
        done += ", the hardware-mismatch warning aside"
    return Action("", "Fix the failing ry-verify checks", why, cmds, done)


def failure_actions(m: Model) -> list[Action]:
    """Return the actions for ry-verify failures, kernel errors, an abnormal reset, core dumps, and failed units."""
    acts = [a for a in (verify_action(m),) if a]
    kernel = [f for f in m.findings if f.key in KERNEL_ERRORS]
    if kernel:
        boots = {mt.boot for f in kernel for mt in f.matches}  # "" for dmesg lines, which are the current boot
        cmds = [
            f"journalctl -k {which} -p warning -o short-monotonic"
            for which, wanted in (("-b", bool(boots - {"previous"})), ("-b -1", "previous" in boots))
            if wanted
        ]
        why = f"{', '.join(f.fid for f in kernel)} in {section_ref('sub-2.1')}"
        acts.append(Action("", "Investigate the kernel errors", why, cmds, "The cause is identified"))
    reset = [f for f in m.findings if f.key == "reset-abnormal"]
    if reset:
        why = f"{reset[0].fid} in {section_ref('sub-2.1')}"
        acts.append(
            Action("", "Find why the previous boot ended", why, ["journalctl -b -1 -e"], "The cause is identified")
        )
    dumps = sorted(
        {int(pid) for f in m.findings if f.key == "core-dump" for mt in f.matches for pid in CORE_PID.findall(mt.text)}
    )
    if dumps:
        cmds = ["coredumpctl list", *[f"coredumpctl info {pid}" for pid in dumps[:MAX_UNIT_COMMANDS]]]
        if len(dumps) > MAX_UNIT_COMMANDS:
            cmds.append(f"# {len(dumps) - MAX_UNIT_COMMANDS} more: coredumpctl list names them all")
        why = f"{plural(len(dumps), 'process', 'processes')} dumped core"
        acts.append(Action("", "Inspect the core dumps", why, cmds, "The crashing program is identified"))
    units = failed_units(m)
    if units:
        names, boots = list(dict.fromkeys(u[0] for u in units)), {u[1] for u in units}
        where = "both boots" if len(boots) > 1 else f"the {boots.pop()} boot"
        failed = f"`{lit(names[0])}` failed" if len(names) == 1 else f"{plural(len(names), 'unit')} failed"
        why = f"{failed} in {where}"
        title = "Inspect the failed unit" if len(names) == 1 else "Inspect the failed units"
        acts.append(Action("", title, why, unit_commands(units), "No unit stays failed"))
    elif unnamed := [f for f in m.findings if f.key == "unit-failed"]:  # its lines name no unit
        why = f"{unnamed[0].fid} in {section_ref('sub-2.1')}"
        acts.append(Action("", "Inspect the failed units", why, unit_commands([]), "No unit stays failed"))
    return acts


def plan_actions(m: Model) -> list[Action]:
    """Return the actions the findings call for, each with commands and a completion test."""
    acts = failure_actions(m)
    for n, a in enumerate(acts, 1):
        a.aid = f"A-{n}"
    return acts


def analyze(br: BugReport, vj: VerifyLog) -> Model:
    """Run every analysis step and return the model the report is built from."""
    STATE.root_uuid = root_uuid(br)
    facts = {**inxi_facts(br), **dmesg_facts(br)}
    if "kernel" not in facts and (release := re.match(r"Linux \S+ (\S+)", br.uname)):
        facts["kernel"] = release.group(1)  # the report header's uname line stands in for a missing inxi block
    facts = {k: sanitize(v) for k, v in facts.items()}  # shown on the cover and in every running header
    rules = applicable_rules(br)
    found = attribute(br, rules)
    if not other_machine(vj, facts.get("cpu", "")):  # another machine's records say nothing about this one
        apply_mitigations(found, vj)
    findings = [f for f in found.values() if f.severity in FINDING_LEVELS] + verify_findings(vj)
    findings.sort(key=register_order)
    counters: dict[str, int] = {}
    for f in findings:
        letter = f.severity[0]
        counters[letter] = counters.get(letter, 0) + 1
        f.fid = f"{letter}-{counters[letter]}"
    others = sorted(
        (f for f in found.values() if f.severity not in FINDING_LEVELS), key=lambda f: SEVERITY_RANK[f.severity]
    )
    marks, quiet = timeline(br)
    m = Model(
        br,
        vj,
        findings,
        others,
        marks,
        quiet,
        kernel_start(br),
        verify_sections(vj),
        facts,
        [],
        keyword_lines(br, {mt.no for f in others if f.severity == "NOTE" for mt in f.matches}),
        [],
    )
    m.health = health_rows(m)
    m.actions = plan_actions(m)
    return m


# ── FIGURES ───────────────────────────────────────────────────────────
# Vector charts from the model: matplotlib with text as paths, embedded through svglib; grayscale only. svglib draws
# no SVG patterns, so styles differ by gray level and outline, never by hatching.
C_INK, C_DARK, C_MID, C_LIGHT = "#1d1d1d", "#3c3c3c", "#8a8a8a", "#c9c9c9"
CW = 7.1  # chart width in inches: the 512 pt text frame
FIG_MAX_HEIGHT = 8.0  # inches; a row-scaled chart stops growing here so it fits a page with its caption


def fig_height(base: float, per_row: float, rows: int) -> float:
    """Return a row-scaled figure height in inches, capped at FIG_MAX_HEIGHT."""
    return min(base + per_row * rows, FIG_MAX_HEIGHT)


MPL_FONTS = ("IBMPlexSans-Regular", "IBMPlexSans-SemiBold")  # the faces the charts use: labels and titles
MPL_STYLE: dict[str, Any] = {
    "font.family": "IBM Plex Sans", "font.size": 7.6, "svg.fonttype": "path", "svg.hashsalt": "gtr9-postboot",
    "axes.linewidth": 0.6, "axes.edgecolor": "#3a3a3a", "xtick.major.width": 0.5, "ytick.major.width": 0,
    "xtick.major.size": 2.5, "axes.labelcolor": "#222", "xtick.color": "#333", "ytick.color": "#222",
    "axes.titlesize": 8.2, "axes.titleweight": "semibold", "axes.titlelocation": "left", "legend.frameon": False,
    "legend.fontsize": 7.2,
}  # fmt: skip
STATUS_ORDER = ("OK", "FAIL", "GEN_FAIL", "WARN", "INFO")  # as the text names them and the ry-verify chart stacks them
STATUS_STYLE: dict[str, dict[str, Any]] = {
    "OK": {"color": C_LIGHT},
    "INFO": {"facecolor": "white", "edgecolor": C_INK, "lw": 0.6},
    "WARN": {"color": C_MID},
    "GEN_FAIL": {"color": "#555555"},
    "FAIL": {"color": C_INK},
}
SEVERITY_STYLE: dict[str, dict[str, Any]] = {  # darker for more severe; watch items, settings outlined; NOTE lightest
    "HIGH": {"color": C_INK},
    "MED": {"color": "#4a4a4a"},
    "LOW": {"color": "#777777"},
    "INFO": {"color": "#a4a4a4"},
    "WATCH": {"facecolor": "white", "edgecolor": C_INK, "lw": 0.8, "ls": (0, (3, 1.5))},
    "SETTING": {"facecolor": "white", "edgecolor": C_INK, "lw": 0.8, "ls": (0, (0.8, 1.2))},
    "NOTE": {"color": "#d4d4d4"},
}


def _mpl() -> ModuleType:
    """Import matplotlib on first use (after the preflight), register the fonts, and apply the house style.

    The SVG backend measures text as the saved figures draw it, unhinted. The fonts from the font directory replace
    any installed copy of their families, which matplotlib would otherwise find first.
    """
    if STATE.mpl is not None:
        return STATE.mpl
    import matplotlib as mpl

    mpl.use("svg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    fm = font_manager.fontManager
    known = len(fm.ttflist)
    for stem in MPL_FONTS:
        fm.addfont(str(STATE.font_dir / f"{stem}.ttf"))
    ours = fm.ttflist[known:]
    names = {font.name for font in ours}
    fm.ttflist = [font for font in fm.ttflist[:known] if font.name not in names] + ours
    for key, value in MPL_STYLE.items():
        plt.rcParams[key] = value  # ty: ignore[invalid-assignment]  # stubs type values per key
    STATE.mpl = plt
    return plt


def clean(ax: Axes, *, grid: bool = True) -> None:
    """Hide the top, right, and left spines, the y ticks, and add a light vertical grid behind the bars."""
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(b=False)
    ax.tick_params(axis="y", which="both", length=0)  # a log axis has minor ticks too
    if grid:
        ax.xaxis.grid(visible=True, color="#dcdcdc", lw=0.5)
        ax.set_axisbelow(b=True)


def value_label(ax: Axes, x: float, y: float, text: str, *, boxed: bool = True) -> None:
    """Write a bar's value 3 pt past its end, whatever the axis scale, boxed in white over the grid lines.

    Paired bars sit too close for a box, which would cover part of the label beside it.
    """
    ax.annotate(
        text, (x, y), xytext=(3, 0), textcoords="offset points", va="center", fontsize=6.4 if boxed else 6.0,
        bbox={"boxstyle": "square,pad=0.1", "facecolor": "white", "edgecolor": "none"} if boxed else None,
    )  # fmt: skip


def status_text(counts: dict[str, int]) -> str:
    """Return status counts in report order without the zeros, as 10 OK, 3 FAIL, 1 WARN, 4 INFO."""
    return ", ".join(f"{counts[k]:,} {k}" for k in STATUS_ORDER if counts.get(k)) or "no records"


FIG_PAD = 0.04  # inches of white around a saved figure


def save(fig: Figure, name: str, *, full_width: bool = False) -> str:
    """Write one figure as SVG into the build's chart directory, close it, and return its name.

    A figure is cropped to what it draws; a full-width figure keeps its whole width, so axes aligned across figures
    stay aligned on the page.
    """
    box: Any = "tight"
    if full_width:
        from matplotlib.transforms import Bbox

        tight = fig.get_tightbbox()
        box = Bbox.from_extents(0, tight.y0 - FIG_PAD, fig.get_figwidth(), tight.y1 + FIG_PAD)
    fig.savefig(
        STATE.chart_dir / f"{name}.svg", format="svg", bbox_inches=box, pad_inches=FIG_PAD, metadata={"Date": None}
    )
    _mpl().close(fig)
    return name


def align_x(figs: Sequence[Figure]) -> None:
    """Give every axes of the figures one left and one right edge, so time charts on one scale line up.

    Each figure is laid out on its own first; the widest margins then hold for all, the right edge moving in further
    while a label would reach past a figure's edge. The layout is then frozen, so saving keeps the edges.
    """
    for fig in figs:
        fig.draw_without_rendering()  # runs the figure's constrained layout
        fig.set_layout_engine("none")
    boxes = [ax.get_position() for fig in figs for ax in fig.axes]
    left, right = max(b.x0 for b in boxes), min(b.x1 for b in boxes)
    for _ in range(8):
        for fig in figs:
            for ax in fig.axes:
                box = ax.get_position()
                ax.set_position((left, box.y0, right - left, box.height))
        spill = max(fig.get_tightbbox().x1 + FIG_PAD - fig.get_figwidth() for fig in figs)  # inches
        if spill <= 0:
            break
        right -= (spill + 0.01) / CW


def capture_points(m: Model) -> dict[str, float]:
    """Return the capture times as seconds since kernel start, when the kernel start is known.

    A time before the kernel start (the clock set back since) is left out.
    """
    if not m.kernel_start:
        return {}
    start = m.kernel_start[0]
    out = {}
    for label, when in (("ry-verify starts", m.vj.started), ("ry-verify ends", m.vj.finished)):
        if when:
            out[label] = (when.replace(tzinfo=None) - start).total_seconds()
    if m.br.captured:
        out["bug report"] = (m.br.captured - start).total_seconds()
    return {k: v for k, v in out.items() if v >= 0}


BOOT_SPAN = 600.0  # seconds of the ring buffer the boot chart covers at most
BOOT_MIN_SPAN = 1.0  # s, the least it covers, so lines all stamped near 0 s still draw a chart
BOOT_PAUSE = 30.0  # s; the boot's dmesg burst ends at the first pause this long after the last milestone
BOOT_BARS = 120  # most bars the boot chart draws
BAR_WIDTHS = (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0)  # s, the bar widths the boot chart picks from
PAUSE_MIN = 0.5  # s; a shorter stretch without dmesg output during the boot is no pause the pause table lists
PAUSE_QUOTE = 90  # characters of each line the pause table quotes: two lines in its column
PAUSES = 5  # longest pauses the pause table lists


def boot_bin(xmax: float) -> float:
    """Return the boot chart's bar width: the narrowest that draws at most BOOT_BARS bars up to xmax."""
    return next((w for w in BAR_WIDTHS if xmax / w <= BOOT_BARS), BAR_WIDTHS[-1])


def quiet_span(m: Model, xmax: float) -> tuple[float, float] | None:
    """Return the stretch of the boot chart shaded for the quiet gap: the bars wholly inside it, up to xmax.

    The lines that bound the gap fall into the bars at either end, which stay unshaded, so no drawn bar lies on the
    shading; a gap within one or two bars shades nothing. Bars are counted in whole microseconds, as draw_boot does.
    """
    if not m.quiet_gap:
        return None
    step = round(boot_bin(xmax) * 1e6)
    a = (round(m.quiet_gap[0] * 1e6) // step + 1) * step / 1e6
    b = min(round(m.quiet_gap[1] * 1e6) // step * step / 1e6, xmax)
    return (a, b) if b > a else None


def burst_end(m: Model) -> tuple[float, bool] | None:
    """Return when the boot's burst of dmesg lines ends and whether BOOT_SPAN cut it; None once dmesg lost the boot.

    The burst ends at the first pause of BOOT_PAUSE after the last milestone, and lasts BOOT_MIN_SPAN at least; when no
    such pause comes before BOOT_SPAN, it ends at the last line before BOOT_SPAN, cut.
    """
    if not holds_boot(m.br):
        return None
    marks = [v for v in m.milestones.values() if v < BOOT_SPAN]
    times = sorted(e.t for e in m.br.dmesg)
    end, cut = times[0], False
    for t in times[1:]:
        if t - end >= BOOT_PAUSE and end >= max(marks, default=0.0):
            break  # what follows the pause is the running system, not the boot
        if t >= BOOT_SPAN:
            cut = True  # the boot chart's window ends before a pause does
            break
        end = t
    return max([end, *marks, BOOT_MIN_SPAN]), cut


def boot_burst(m: Model) -> float | None:
    """Return when the boot's burst of dmesg lines ends, or None when dmesg no longer holds the boot."""
    burst = burst_end(m)
    return None if burst is None else burst[0]


def boot_phases(m: Model) -> list[tuple[str, float, float]]:
    """Return the boot phases dmesg bounds, as (name, start, end) in seconds since kernel start.

    The kernel runs until it starts init (/init when an initramfs holds it), the initrd until the switch to the root
    file system, and userspace until the boot's burst of dmesg lines ends; a phase dmesg does not bound is left out.
    A switch of the root file system shows an initrd whatever its init is called (rdinit= names another).
    """
    marks = m.milestones
    init = marks.get("initrd starts", marks.get("init starts"))
    initrd = "initrd starts" in marks or "Switching root" in marks
    spans = (
        ("Kernel", 0.0, init),
        ("initrd", init if initrd else None, marks.get("Switching root")),
        ("Userspace", marks.get("Switching root") if initrd else init, boot_burst(m)),
    )
    return [(name, a, b) for name, a, b in spans if a is not None and b is not None and b > a]


def boot_axis(m: Model) -> tuple[float, dict[str, float]] | None:
    """Return the boot chart's x range and the capture points it draws, or None when dmesg no longer holds the boot.

    The range covers the boot's burst of dmesg lines and the captures up to twice its length; later captures
    appear in the timeline table only. A ry-verify run that starts on the chart keeps its end, drawn at the edge.
    """
    burst = boot_burst(m)
    if burst is None:
        return None
    points = capture_points(m)
    captures = {k: v for k, v in points.items() if v <= min(2 * burst, BOOT_SPAN)}
    xmax = min(max([burst, *captures.values()]) * 1.06, BOOT_SPAN)
    if "ry-verify starts" in captures and "ry-verify ends" in points:
        captures["ry-verify ends"] = points["ry-verify ends"]
    return xmax, captures


def capture_marks(points: dict[str, float], xmax: float) -> dict[str, float]:
    """Return the chart labels for the capture points: one mark per capture, the ry-verify run at its middle.

    A run that outlasts the chart is marked at the middle of the part drawn.
    """
    marks = {}
    if "ry-verify starts" in points and "ry-verify ends" in points:
        a, b = points["ry-verify starts"], points["ry-verify ends"]
        marks[f"ry-verify {a:.1f}–{b:.1f} s"] = (a + min(b, xmax)) / 2
    elif "ry-verify starts" in points:
        marks[f"ry-verify from {points['ry-verify starts']:.1f} s"] = points["ry-verify starts"]
    if "bug report" in points:
        marks[f"bug report {points['bug report']:.1f} s"] = points["bug report"]
    return marks


def gap_seconds(a: float, b: float) -> float:
    """Return the time from a to b as the report prints both: rounded to hundredths first, so the three agree."""
    return round(b, 2) - round(a, 2)


MILESTONE_GAP = 0.12  # share of the axis two milestone labels need between them on one level (about 0.65 in)
CAPTURE_GAP = 0.16  # the same for the wider capture labels


def label_levels(points: dict[str, float], gap: float) -> dict[str, int]:
    """Return a stacking level per label, left to right: the lowest level whose last label lies at least gap away."""
    last: list[float] = []
    out = {}
    for label, x in sorted(points.items(), key=lambda kv: (kv[1], kv[0])):
        level = next((i for i, prev in enumerate(last) if x - prev >= gap), len(last))
        if level == len(last):
            last.append(x)
        else:
            last[level] = x
        out[label] = level
    return out


PHASE_STYLE = {"Kernel": C_INK, "initrd": C_MID, "Userspace": C_LIGHT}  # one gray per boot phase, darkest first


Lane = tuple[dict[str, float], str, dict[str, int]]  # a lane's labels at their x, its marker, each label's level


def draw_boot(m: Model) -> Figure | None:
    """Draw the boot chart: dmesg lines per bar width with the quiet gap, the boot phases, milestones, and captures."""
    axis = boot_axis(m)
    if axis is None:
        return None  # the ring buffer no longer holds the boot; there is no chart to draw
    plt = _mpl()
    xmax, captures = axis
    width = boot_bin(xmax)
    step = round(width * 1e6)  # whole microseconds, as dmesg stamps lines, so no line falls into the wrong bar
    counts = [0] * (round(xmax * 1e6) // step + 1)
    for e in m.br.dmesg:
        if e.t < xmax:
            counts[round(e.t * 1e6) // step] += 1
    phases = boot_phases(m)
    lanes: dict[int, Lane] = {}  # row 1, the milestones, labeled above; row 0, the captures, labeled below
    if boot := {f"{k}\n{v:.2f} s": v for k, v in m.milestones.items() if v <= xmax}:
        lanes[1] = (boot, "v", label_levels(boot, xmax * MILESTONE_GAP))
    if marks := capture_marks(captures, xmax):
        lanes[0] = (marks, "s", label_levels(marks, xmax * CAPTURE_GAP))
    ratios, height = [1.6], 1.9
    if phases:
        ratios.append(0.3)
        height += 0.3
    low = high = 0.0
    if lanes:
        up = max(lanes[1][2].values()) + 1 if 1 in lanes else 0  # label levels above the milestones
        down = max(lanes[0][2].values()) + 1 if 0 in lanes else 0  # and below the captures
        low = -0.55 - 0.75 * down if down else 0.4
        high = 1.45 + 1.35 * up if up else 0.6
        ratios.append(0.3 * (high - low))
        height += 0.22 * (high - low) - 0.15
    fig, axs = plt.subplots(
        len(ratios), 1, figsize=(CW, height), sharex=True, squeeze=False, height_ratios=ratios, layout="constrained"
    )
    axes = [row[0] for row in axs]
    boot_bars(axes[0], m, xmax, counts, width)
    clean(axes[0])
    if phases:
        phase_band(fig, axes[1], phases)
    if lanes:
        boot_lanes(axes[-1], lanes, captures)
        axes[-1].set_ylim(low, high)
    axes[-1].set_xlim(0, xmax)
    axes[-1].set_xlabel("Seconds since kernel start", fontsize=7.2)
    return fig


def phase_band(fig: Figure, band: Axes, phases: Sequence[tuple[str, float, float]]) -> None:
    """Draw the boot phases as one band of grays, with a legend that names each phase and its duration."""
    from matplotlib.patches import Patch

    for name, a, b in phases:
        band.barh(0, b - a, left=a, height=0.62, color=PHASE_STYLE[name], edgecolor="white", lw=0.8)
    band.set_ylim(-0.5, 0.5)
    band.set_yticks([0], ["Phases"])
    band.spines["bottom"].set_visible(b=False)
    band.tick_params(axis="x", length=0)
    clean(band, grid=False)
    handles = [Patch(color=PHASE_STYLE[n], label=f"{n} {gap_seconds(a, b):.2f} s") for n, a, b in phases]
    fig.legend(handles=handles, loc="outside lower center", ncol=len(handles))


def boot_lanes(ax: Axes, lanes: Mapping[int, Lane], captures: dict[str, float]) -> None:
    """Draw the milestone lane and the capture lane, each label at its stacking level, the ry-verify run as a bar.

    A lane without labels is left out; a run that outlasts the chart ends at its edge.
    """
    for row, (marks, marker, levels) in lanes.items():
        for label, x in marks.items():
            ax.plot([x], [row], marker=marker, ms=5, color=C_INK, ls="none", clip_on=False)
            y = row + 0.25 + 1.35 * levels[label] if row else row - 0.3 - 0.75 * levels[label]
            ax.text(x, y, label, ha="center", va="bottom" if row else "top", fontsize=6.2)
    if "ry-verify starts" in captures and "ry-verify ends" in captures:
        span = [captures["ry-verify starts"], captures["ry-verify ends"]]
        ax.plot(span, [0, 0], color=C_INK, lw=3, solid_capstyle="butt")
    rows = sorted(lanes, reverse=True)
    ax.set_yticks(rows, ["Boot" if row else "Captures" for row in rows])
    clean(ax, grid=False)


def boot_bars(ax1: Axes, m: Model, xmax: float, counts: Sequence[int], width: float) -> None:
    """Draw the boot chart's bars on a log scale, with the quiet gap shaded and named when it is wide enough."""
    from matplotlib.ticker import NullFormatter

    ax1.bar([(i + 0.5) * width for i in range(len(counts))], counts, width=width * 0.84, color=C_DARK)
    ax1.set_yscale("log")
    ax1.yaxis.set_minor_formatter(NullFormatter())  # a range under a decade would label its minor ticks too
    top = max([*counts, 1]) * 1.8
    ax1.set_ylim(0.8, top)  # a fixed floor keeps one-line bars visible and the gap label inside the axes
    ax1.set_ylabel(f"dmesg lines\nper {width:g} s", fontsize=7)
    if m.quiet_gap and (span := quiet_span(m, xmax)):
        a, b = span
        ax1.axvspan(a, b, color=C_LIGHT, zorder=0)
        if b - a >= 0.1 * xmax:  # a narrower stretch is named in the caption only
            label = f"no output\n{gap_seconds(*m.quiet_gap):.2f} s"
            ax1.text((a + b) / 2, (0.8 * top) ** 0.5, label, ha="center", va="center", fontsize=6.4, clip_on=True)


def fig_verify(m: Model) -> str | None:
    """Draw ry-verify results per section, one panel per phase on a shared scale, stacked by status."""
    phases = [p for p in ("static", "runtime") if any(s[0] == p for s in m.verify_sections)]
    if not phases:
        return None
    plt = _mpl()
    from matplotlib.patches import Patch
    from matplotlib.ticker import MaxNLocator

    per_phase = [[s for s in m.verify_sections if s[0] == p][::-1] for p in phases]
    fig, axs = plt.subplots(
        len(phases), 1, figsize=(CW, fig_height(0.95, 0.19, sum(len(rows) for rows in per_phase))), sharex=True,
        squeeze=False, height_ratios=[len(rows) + 1.2 for rows in per_phase], layout="constrained",
    )  # fmt: skip
    peak = max(sum(s[2].values()) for s in m.verify_sections)
    for (ax,), phase, rows in zip(axs, phases, per_phase, strict=True):
        left = [0] * len(rows)
        for status in STATUS_ORDER:
            vals = [s[2][status] for s in rows]
            drawn = [i for i, v in enumerate(vals) if v]  # a zero-width bar would still draw its edge as a hairline
            ax.barh(drawn, [vals[i] for i in drawn], left=[left[i] for i in drawn], height=0.62, **STATUS_STYLE[status])
            left = [a + b for a, b in zip(left, vals, strict=True)]
        for i, s in enumerate(rows):
            value_label(ax, left[i], i, status_text(s[2]))
        ax.set_yticks(range(len(rows)), [chart_text(section_title(s[1])) for s in rows])
        ax.set_ylim(-0.6, len(rows) - 0.4)
        totals = {k: sum(s[2][k] for s in rows) for k in STATUS_ORDER}
        ax.set_title(f"{phase.capitalize()} phase: {status_text(totals)}")
        clean(ax)
    axs[0][0].set_xlim(0, peak * 1.3 + 1)  # room for the value labels on the shared scale
    axs[0][0].xaxis.set_major_locator(MaxNLocator(integer=True))
    axs[-1][0].set_xlabel("Records", fontsize=7.2)
    present = [k for k in STATUS_ORDER if any(s[2][k] for s in m.verify_sections)]  # the legend keys those drawn
    handles = [Patch(label=k, **STATUS_STYLE[k]) for k in present]
    fig.legend(handles=handles, loc="outside lower center", ncol=len(handles))
    return save(fig, "verify")


def family_rows(m: Model) -> list[tuple[str, int, str]]:
    """Return (family, dmesg lines, disposition) rows: rules with dmesg evidence by count, then the rest.

    Equal counts sort by disposition, then by finding number, then by title; the boilerplate (NOTE) families share
    one row, last.
    """
    found = [f for f in [*m.findings, *m.others] if f.count("dmesg")]
    ranked = sorted(
        (f for f in found if f.severity != "NOTE"),
        key=lambda f: (-f.count("dmesg"), SEVERITY_RANK[f.severity], int(f.fid.partition("-")[2] or 0), f.title),
    )
    rows = [(f"{f.fid}  {f.title}" if f.fid else f.title, f.count("dmesg"), f.severity) for f in ranked]
    notes = [f for f in found if f.severity == "NOTE"]
    if notes:
        label = f"Boilerplate, {plural(len(notes), 'family', 'families')}"
        rows.append((label, sum(f.count("dmesg") for f in notes), "NOTE"))
    return rows


def fig_families(m: Model) -> str | None:
    """Draw dmesg lines per family, styled by disposition."""
    rows = family_rows(m)[::-1]
    if not rows:
        return None
    plt = _mpl()
    from matplotlib.patches import Patch
    from matplotlib.ticker import MaxNLocator

    fig, ax = plt.subplots(figsize=(CW, fig_height(0.75, 0.17, len(rows))), layout="constrained")
    for i, (_name, value, sev) in enumerate(rows):
        ax.barh(i, value, height=0.62, **SEVERITY_STYLE[sev])
        value_label(ax, value, i, f"{value:,}")
    ax.set_yticks(range(len(rows)), [r[0] for r in rows])
    ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.set_xlim(0, max(r[1] for r in rows) * 1.12 + 1)
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_xlabel("dmesg lines", fontsize=7.2)
    clean(ax)
    present = [s for s in SEVERITY_STYLE if any(r[2] == s for r in rows)]
    handles = [Patch(label=s, **SEVERITY_STYLE[s]) for s in present]
    fig.legend(handles=handles, loc="outside lower center", ncol=len(handles))
    return save(fig, "families")


def draw_subsystems(m: Model) -> Figure | None:
    """Draw dmesg activity per subsystem over the boot: one tick per line on a span from the first line to the last."""
    axis = boot_axis(m)
    if axis is None:
        return None
    xmax = axis[0]
    rows = [(name, [e.t for e in lines if e.t < xmax]) for name, lines in subsystem_lines(m.br).items()]
    rows = [(name, times) for name, times in rows if times][::-1]  # the first subsystem on top
    if not rows:
        return None
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(CW, fig_height(0.7, 0.2, len(rows))), layout="constrained")
    for y, (_name, times) in enumerate(rows):
        ax.barh(y, max(times) - min(times), left=min(times), height=0.56, color=C_LIGHT, lw=0)
        ax.plot(times, [y] * len(times), marker="|", ms=6.5, mew=0.7, ls="none", color=C_INK)
    ax.set_yticks(range(len(rows)), [f"{name} ({len(times):,})" for name, times in rows])
    ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.set_xlim(0, xmax)
    ax.set_xlabel("Seconds since kernel start", fontsize=7.2)
    clean(ax)
    return fig


TIME_UNITS = ((7200.0, 3600.0, "Hours"), (300.0, 60.0, "Minutes"), (0.0, 1.0, "Seconds"))  # from, divisor, unit


def after_lines(m: Model) -> list[DmesgEntry]:
    """Return the dmesg lines after the boot's burst; none when dmesg no longer holds the boot."""
    burst = boot_burst(m)
    return [] if burst is None else [e for e in m.br.dmesg if e.t > burst]


def time_unit(span: float) -> tuple[float, str]:
    """Return the unit of a time axis that spans this many seconds, as (seconds per unit, name)."""
    return next((div, name) for start, div, name in TIME_UNITS if span >= start)


def after_span(m: Model) -> tuple[float, float] | None:
    """Return the after-boot chart's span, from the burst's end to the last line, or None when it has no line."""
    lines, burst = after_lines(m), boot_burst(m)
    if not lines or burst is None:
        return None
    return burst, max(e.t for e in lines)


def after_suspends(m: Model) -> list[float]:
    """Return when the system went to sleep after the boot's burst, by dmesg time, which stops while it sleeps."""
    burst = boot_burst(m)
    return [] if burst is None else sorted(e.t for e in m.br.dmesg if e.t > burst and SUSPEND_LINE.match(e.text))


def fig_after(m: Model) -> str | None:
    """Draw the dmesg lines after the boot: one tick per line, one row per subsystem, by time since kernel start.

    A dashed line marks each suspend, as dmesg time stops while the system sleeps.
    """
    span = after_span(m)
    if span is None:
        return None
    plt = _mpl()
    div, unit = time_unit(span[1] - span[0])
    after = {e.no for e in after_lines(m)}
    rows = [(name, [e.t / div for e in group if e.no in after]) for name, group in subsystem_lines(m.br).items()]
    rows = [(name, times) for name, times in rows if times][::-1]  # the first subsystem on top
    fig, ax = plt.subplots(figsize=(CW, fig_height(0.7, 0.2, len(rows))), layout="constrained")
    for t in after_suspends(m):
        ax.axvline(t / div, color=C_DARK, lw=0.8, ls=(0, (3, 2)), zorder=1)
    for y, (_name, times) in enumerate(rows):
        ax.plot(times, [y] * len(times), marker="|", ms=7, mew=1.0, ls="none", color=C_INK)
    ax.set_yticks(range(len(rows)), [f"{name} ({len(times):,})" for name, times in rows])
    ax.set_ylim(-0.6, len(rows) - 0.4)
    lo, hi = span[0] / div, span[1] / div
    ax.set_xlim(lo, hi + max((hi - lo) * 0.03, 0.5 / div))
    ax.ticklabel_format(axis="x", style="plain", useOffset=False)  # a late, short span keeps whole times
    ax.set_xlabel(f"{unit} since kernel start", fontsize=7.2)
    clean(ax)
    return save(fig, "after")


def boot_figures(m: Model) -> dict[str, str | None]:
    """Draw the boot chart and the boot activity by subsystem, with their time axes aligned."""
    figs = {"boot": draw_boot(m), "subsystems": draw_subsystems(m)}
    drawn = [fig for fig in figs.values() if fig is not None]
    if len(drawn) > 1:
        align_x(drawn)
    return {key: save(fig, key, full_width=len(drawn) > 1) if fig else None for key, fig in figs.items()}


PROCESS_BARS = 12  # bars the process chart draws at most: past this many processes, the busiest 11 and one Other bar
PROCESS_LABEL = 40  # characters of a process name a bar label shows; a longer name keeps its start and its end
PROCESS_ROWS = 20  # journal processes the process table lists; one more row counts the rest


def chart_text(text: str, limit: int = 0) -> str:
    """Return input text for a matplotlib label: masked, with each dollar sign escaped so it never opens math text.

    With a limit, a longer text keeps its start and its end around an ellipsis, as the end of a path names it.
    """
    text = quote(text)
    if limit and len(text) > limit:
        text = text[: limit // 2 - 1] + "…" + text[len(text) - (limit - limit // 2) :]
    return text.replace("$", r"\$")


def process_bars(m: Model) -> list[tuple[str, int, int]]:
    """Return the process chart's bars, most entries first.

    Up to PROCESS_BARS processes get a bar each; past that, the PROCESS_BARS - 1 busiest get one and an Other bar
    sums the rest.
    """
    procs = journal_processes(m.br)
    if len(procs) <= PROCESS_BARS:
        return [(chart_text(name, PROCESS_LABEL), cur, prev) for name, cur, prev in procs]
    rest = procs[PROCESS_BARS - 1 :]
    other = (f"{OTHER} ({len(rest)})", sum(r[1] for r in rest), sum(r[2] for r in rest))
    return [*((chart_text(name, PROCESS_LABEL), cur, prev) for name, cur, prev in procs[: PROCESS_BARS - 1]), other]


BOOT_STYLE: dict[str, dict[str, Any]] = {"current": {"color": C_DARK}, "previous": {"color": C_LIGHT}}


def draw_paired(rows: Sequence[tuple[str, int, int]], xlabel: str) -> Figure:
    """Draw one bar per boot and row, the current boot's above the previous boot's, the first row on top.

    A boot without entries in any row draws no bars and no legend entry.
    """
    plt = _mpl()
    from matplotlib.patches import Patch
    from matplotlib.ticker import MaxNLocator

    rows = list(rows)[::-1]  # barh draws upward
    boots = [b for b, i in (("current", 1), ("previous", 2)) if any(r[i] for r in rows)]
    height = 0.66 / len(boots)
    fig, ax = plt.subplots(figsize=(CW, fig_height(0.75, 0.1 + 0.07 * len(boots), len(rows))), layout="constrained")
    for k, boot in enumerate(boots):
        i = 1 if boot == "current" else 2
        offset = ((len(boots) - 1) / 2 - k) * height  # the current boot's bar on top
        for y, row in enumerate(rows):
            if row[i]:
                ax.barh(y + offset, row[i], height=height * 0.88, **BOOT_STYLE[boot])
                value_label(ax, row[i], y + offset, f"{row[i]:,}", boxed=len(boots) == 1)
    ax.set_yticks(range(len(rows)), [r[0] for r in rows])
    ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.set_xlim(0, max(max(r[1], r[2]) for r in rows) * 1.14 + 1)
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_xlabel(xlabel, fontsize=7.2)
    clean(ax)
    if len(boots) > 1:
        handles = [Patch(label=f"{b.capitalize()} boot", **BOOT_STYLE[b]) for b in boots]
        fig.legend(handles=handles, loc="outside lower center", ncol=len(handles))
    return fig


def fig_processes(m: Model) -> str | None:
    """Draw journal entries per process, the current boot's bar above the previous boot's."""
    rows = process_bars(m)
    return save(draw_paired(rows, "Journal entries"), "processes") if rows else None


BY_FINDING_LABEL = 64  # characters of a finding's ID and title a bar label shows; a longer title ends in an ellipsis


def by_finding_rows(m: Model) -> list[tuple[str, int, int]]:
    """Return the by-finding chart's bars: entries per boot of each finding or message class with journal entries.

    Rows keep register order and show the ID and title.
    """
    out = []
    for f in [*m.findings, *m.others]:
        cur, prev = f.count("journal", "current"), f.count("journal", "previous")
        if cur or prev:
            out.append((chart_text(clip(f"{f.fid}  {f.title}" if f.fid else f.title, BY_FINDING_LABEL)), cur, prev))
    return out


def fig_by_finding(m: Model) -> str | None:
    """Draw journal entries per finding or message class, the current boot above the previous; both boots needed."""
    rows = by_finding_rows(m)
    if not (any(r[1] for r in rows) and any(r[2] for r in rows)):
        return None  # the chart compares the boots; a single boot's counts are the timeline's rows
    return save(draw_paired(rows, "Journal entries"), "by-finding")


def journal_timeline_rows(m: Model) -> list[tuple[str, list[dt.datetime], list[dt.datetime]]]:
    """Return (label, current-boot times, previous-boot times) per finding or message class with journal entries."""
    rows = []
    for f in [*m.findings, *m.others]:
        cur = [x.when for x in f.matches if x.stream == "journal" and x.boot == "current" and x.when]
        prev = [x.when for x in f.matches if x.stream == "journal" and x.boot == "previous" and x.when]
        if cur or prev:
            rows.append((f.fid or f.title, cur, prev))
    return rows


JOURNAL_BARS = 60  # most bars a journal timeline panel draws
JOURNAL_LOG = 20  # entries in the fullest bar from which the activity bars take a log scale; below, a linear one
JOURNAL_WIDTHS = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400)  # s


def journal_bin(span: float) -> int:
    """Return the journal timeline's bar width in seconds: the narrowest that draws at most JOURNAL_BARS bars."""
    return next((w for w in JOURNAL_WIDTHS if span / w < JOURNAL_BARS), JOURNAL_WIDTHS[-1])


def width_text(seconds: float) -> str:
    """Return a bar width as a caption names it: 5 s, 2 min, or 1 h."""
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds / 3600:g} h"
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds / 60:g} min"
    return f"{seconds:g} s"


def journal_boots(m: Model) -> list[str]:
    """Return the boots the journal timeline draws a panel for: those with entries, current first."""
    return [boot for boot in ("current", "previous") if any(e.boot == boot for e in m.br.journal)]


def journal_times(m: Model, boot: str) -> tuple[list[dt.datetime], dt.datetime | None]:
    """Return a boot's journal entry times and its panel's clock: the kernel start, or None for the wall clock."""
    start = m.kernel_start[0] if boot == "current" and m.kernel_start else None
    return [e.when for e in m.br.journal if e.boot == boot], start


def journal_unit(m: Model, boot: str) -> tuple[float, str] | None:
    """Return the unit of a boot's panel timed from the kernel start, by its entries' span; None on the wall clock."""
    times, start = journal_times(m, boot)
    if start is None or not times:
        return None
    seconds = [(t - start).total_seconds() for t in times]
    return time_unit(max(seconds) - min(seconds))


def journal_bars(times: Sequence[dt.datetime], start: dt.datetime | None) -> tuple[list[tuple[Any, int]], int]:
    """Return a panel's bars, (center, entries) on the panel's axis, and their width in seconds.

    The axis counts seconds from the kernel start when start is given, else the wall clock, where bars begin on
    whole multiples of their width since midnight.
    """
    base = start or min(times).replace(hour=0, minute=0, second=0, microsecond=0)
    seconds = [(t - base).total_seconds() for t in times]
    width = journal_bin(max(seconds) - min(seconds))
    counts = Counter(int(s // width) for s in seconds)
    if start:
        return [((k + 0.5) * width, n) for k, n in sorted(counts.items())], width
    return [(base + dt.timedelta(seconds=(k + 0.5) * width), n) for k, n in sorted(counts.items())], width


def journal_axis(ax: Axes, xs: Sequence[Any], width: int, unit: tuple[float, str] | None) -> None:
    """Label a journal timeline panel's time axis; a span under two bars widens to one bar either side.

    A panel timed from the kernel start counts in unit, as (seconds per unit, name); without one, it reads the wall
    clock. width is the bar width in seconds.
    """
    import matplotlib.dates as mdates

    if unit:
        div, name = unit
        if max(xs) - min(xs) < 2 * width / div:
            ax.set_xlim(min(xs) - width / div, max(xs) + width / div)
        ax.ticklabel_format(axis="x", style="plain", useOffset=False)  # a long uptime keeps whole times
        ax.set_xlabel(f"{name} since kernel start", fontsize=7.2)
        return
    loc = mdates.AutoDateLocator(minticks=3, maxticks=7)
    ax.xaxis.set_major_locator(loc)
    ax.xaxis.set_major_formatter(day_formatter(loc))
    pad = dt.timedelta(seconds=max(width, 60))
    if max(xs) - min(xs) < 2 * pad:
        ax.set_xlim(min(xs) - pad, max(xs) + pad)
    ax.set_xlabel("Wall clock (local)", fontsize=7.2)


def day_formatter(loc: AutoDateLocator) -> ConciseDateFormatter:
    """Return the wall-clock tick formatter: ConciseDateFormatter in the house formats, with midnight dated.

    On hour ticks ConciseDateFormatter labels midnight with its date; on minute and second ticks it would print 00:00
    and leave the offset to name the last tick's day only, so midnight is labeled with its date there too.
    """
    import matplotlib.dates as mdates

    class DayFormatter(mdates.ConciseDateFormatter):
        """ConciseDateFormatter that labels a midnight with its date on minute and second ticks as well."""

        def format_ticks(self, values: Any) -> list[str]:  # noqa: ANN401  # the stubs type values as ArrayLike
            """Return the tick labels, a midnight that would read as a time reading as its date."""
            labels = super().format_ticks(values)
            whens = [mdates.num2date(v) for v in values]
            return [
                f"{w:%m-%d}" if ":" in label and (w.hour, w.minute, w.second, w.microsecond) == (0, 0, 0, 0) else label
                for w, label in zip(whens, labels, strict=True)
            ]

    return DayFormatter(
        loc,
        formats=["%Y", "%Y-%m", "%m-%d", "%H:%M", "%H:%M", "%H:%M:%S"],
        zero_formats=["", "%Y", "%Y-%m", "%m-%d", "%H:%M", "%H:%M"],
        offset_formats=["", "%Y", "%Y-%m", "%Y-%m-%d", "%Y-%m-%d", "%Y-%m-%d %H:%M"],
    )


def fig_journal(m: Model) -> str | None:
    """Draw the journal timeline per boot: entries per bar on a log scale, then one tick per matched entry and finding.

    The current boot is timed from the kernel start when it is estimated, else by the wall clock; the previous boot
    by the wall clock.
    """
    boots = journal_boots(m)
    if not boots:
        return None
    plt = _mpl()
    from matplotlib.ticker import MaxNLocator, NullFormatter

    rows = journal_timeline_rows(m)
    ratios = [1.0, 0.17 * len(rows) + 0.25] if rows else [1.0]
    height = min(1.5 + (0.17 * len(rows) + 0.3 if rows else 0.0), FIG_MAX_HEIGHT)
    fig, axs = plt.subplots(
        len(ratios), len(boots), figsize=(CW, height), sharex="col", sharey="row", squeeze=False,
        height_ratios=ratios, layout="constrained",
    )  # fmt: skip
    y = list(range(len(rows)))[::-1]
    peak = 1
    for col, boot in enumerate(boots):
        times, start = journal_times(m, boot)
        bars, width = journal_bars(times, start)
        unit = journal_unit(m, boot)
        scale = unit[0] if unit else 86400  # the panel's unit, or days on a date axis
        xs = [x / scale for x, _ in bars] if unit else [x for x, _ in bars]
        hist = axs[0][col]
        hist.bar(xs, [n for _, n in bars], width=width / scale * 0.84, color=C_DARK)
        hist.set_title(f"{boot.capitalize()} boot ({plural(len(times), 'entry', 'entries')})")
        peak = max([peak, *(n for _, n in bars)])
        clean(hist)
        if rows:
            ticks = axs[1][col]
            for level, row in zip(y, rows, strict=True):
                if marks := (row[1] if boot == "current" else row[2]):
                    at = [(t - start).total_seconds() / scale for t in marks] if start else marks
                    ticks.plot(at, [level] * len(at), marker="|", ms=7, mew=1.2, ls="none", color=C_INK)
            clean(ticks)
        journal_axis(axs[-1][col], xs, width, unit)
    hist = axs[0][0]
    if peak >= JOURNAL_LOG:
        hist.set_yscale("log")
        hist.yaxis.set_minor_formatter(NullFormatter())  # a range under a decade would label its minor ticks too
        hist.set_ylim(0.8, peak * 1.8)
    else:
        hist.yaxis.set_major_locator(MaxNLocator(nbins=3, integer=True))
        hist.set_ylim(0, peak * 1.15)
    hist.set_ylabel("Entries per bar", fontsize=7)
    if rows:
        axs[1][0].set_yticks(y, [r[0] for r in rows])
        axs[1][0].set_ylim(-0.6, len(rows) - 0.4)  # room for the ticks of the first and last rows
    return save(fig, "journal")


# ── LAYOUT ────────────────────────────────────────────────────────────
# ReportLab platypus: styles, references, flowables, sections, page templates.
PALETTE = {  # the report's grays, darkest first: markup takes the hex strings, drawing code the colors below
    "ink": "#1b1b1b", "dark": "#2d2d2d", "soft": "#3a3a3a", "mute": "#5a5a5a", "faint": "#8a8a8a",
    "rule": "#a3a3a3", "light": "#e8e8e8", "zebra": "#f4f4f4",
}  # fmt: skip
INK, DARK, SOFT, MUTE, RULE, LIGHT, ZEBRA = (
    HexColor(PALETTE[k]) for k in ("ink", "dark", "soft", "mute", "rule", "light", "zebra")
)
HAIR = 0.25  # pt, the weight of every separator rule
PW, PH = letter
LM = RM = 50  # pt, left and right page margins
TOPM, BOTM = 58, 54  # pt, top and bottom page margins (the running header and footer sit inside them)
FW = PW - LM - RM
EVIDENCE_LINES = 8  # evidence lines quoted per card; the Lines row lists them all
CODE_SIZE = 7.3  # monospace size of command blocks
CODE_MIN_SIZE = 6.0  # smallest size a command block shrinks to before a line wraps
MONO_ADVANCE = 0.6  # em; every IBM Plex Mono glyph is 600 units wide
HOLD_ROWS = 2  # data rows a page split leaves on each side of a table; fewer, and the table moves whole
WHOLE_TABLE = 220.0  # pt; a table no taller than this moves to the next page whole instead of splitting
SECTION_ROOM = 160.0  # pt a section or appendix needs left on a page to start there
COVER_TOP = 44  # pt from the page top to the cover's frame: the 30 pt band and 14 pt of white
UNKNOWN_MACHINE = "Unknown machine"  # the cover's and the running header's name for a machine inxi does not name
NBSP, EN_SPACE = "\u00a0", "\u2002"  # no-break space, en space; a \N{} name would load unicodedata at compile time


def style(name: str, parent: ParagraphStyle | None = None, **kw: object) -> ParagraphStyle:
    """Return a paragraph style: the parent's settings, else the house defaults (Plex 9/12.8, ink), plus kw."""
    if parent is not None:
        return ParagraphStyle(name, parent=parent, **kw)
    base: dict[str, object] = {
        "fontName": "Plex", "fontSize": 9, "leading": 12.8, "textColor": INK, "bulletFontName": "Plex",
        "bulletFontSize": 9, "allowWidows": 0,
    }  # fmt: skip
    base.update(kw)
    return ParagraphStyle(name, **base)


sty_body = style("body", spaceAfter=5.5)
sty_lead = style("lead", parent=sty_body, keepWithNext=1)  # introduces what follows
sty_bullet = style("bullet", leftIndent=12, bulletIndent=2, spaceAfter=3.2)
sty_h1 = style("h1", fontName="Plex-SB", fontSize=15.5, leading=19, spaceBefore=16, spaceAfter=9, keepWithNext=1)
sty_h2 = style("h2", fontName="Plex-SB", fontSize=10.6, leading=14, spaceBefore=11, spaceAfter=4.5, keepWithNext=1)
sty_table_title = style("ttl", fontName="Plex-SB", fontSize=8.1, leading=10.8)
sty_caption = style("cap", fontName="Plex-It", fontSize=7.5, leading=10, textColor=MUTE, spaceBefore=3, spaceAfter=11)
sty_th = style("th", fontName="PlexC-SB", fontSize=7.6, leading=9.5)
sty_th_right = style("th-right", parent=sty_th, alignment=TA_RIGHT)
sty_td = style("td", fontName="PlexC", fontSize=7.8, leading=9.9)
sty_td_right = style("tdr", parent=sty_td, alignment=TA_RIGHT)
TD_MONO = 6.9  # pt, the monospace size of table cells and card evidence
sty_td_mono = style("tdm", fontName="PlexM", fontSize=TD_MONO, leading=9.1)
sty_code = style("code", fontName="PlexM", fontSize=CODE_SIZE, leading=10.6)
sty_label = style("lab", fontName="PlexC-SB", fontSize=7.4, leading=9.6, textColor=MUTE)
sty_card_value = style("cv", parent=sty_td, fontSize=8.0, leading=10.6)
sty_card_meta = style("meta", fontName="PlexC", fontSize=7.4, leading=9.4, textColor=MUTE)
sty_card_id = style("cid", fontName="PlexM-SB", fontSize=11, leading=13)
sty_card_title = style("ctt", fontName="Plex-SB", fontSize=9.4, leading=12)
sty_chip = style("chip", fontName="PlexC-SB", fontSize=6.8, leading=8, alignment=TA_CENTER)
sty_code_label = style("cbl", fontName="PlexC-SB", fontSize=6.8, leading=8.5, textColor=colors.white)
sty_info_meaning = style("im", parent=sty_td, fontSize=7.3, leading=9.4, textColor=MUTE)
sty_cover_title = style("ct", fontName="Plex-SB", fontSize=25, leading=29, spaceAfter=6)
sty_cover_line = style("cs", fontSize=10.2, leading=13.5, textColor=MUTE)
sty_cover_date = style("cs2", parent=sty_cover_line, spaceAfter=12)
sty_cover_head = style("dch", fontName="Plex-SB", fontSize=8.4, leading=11, spaceAfter=3)
sty_cover_label = style("cl", fontName="PlexC-SB", fontSize=6.6, leading=8.6, textColor=MUTE)  # the cover's labels
sty_verdict_head = style("vh", fontName="Plex-SB", fontSize=12.5, leading=16, spaceAfter=2)
sty_verdict = style("vb", fontSize=8.6, leading=11.8)
sty_kpi_group = style("kg", parent=sty_cover_label, textColor=INK, alignment=TA_CENTER)
sty_kpi_value = style("kb", fontName="Plex-SB", fontSize=17, leading=19, alignment=TA_CENTER)
sty_kpi_label = style("ks", parent=sty_cover_label, alignment=TA_CENTER)
sty_tile_value = style("hv", fontName="Plex-SB", fontSize=10.5, leading=13)
sty_tile_detail = style("hs", fontName="PlexC", fontSize=6.6, leading=8.2, textColor=SOFT)
sty_doc_key = style("dk", fontName="PlexC-SB", fontSize=7.6, leading=9.8)
sty_contents = style("cth", fontName="Plex-SB", fontSize=15.5, leading=19, spaceAfter=10)
sty_toc0 = style("tc0", fontName="Plex-SB", fontSize=8.8, leading=11.4)
sty_toc1 = style("tc1", fontSize=8.2, leading=10.6, leftIndent=16)
sty_toc0_page = style("tp0", parent=sty_toc0, alignment=TA_RIGHT)
sty_toc1_page = style("tp1", parent=sty_toc1, leftIndent=0, alignment=TA_RIGHT)
sty_side_head = style("fh", fontName="Plex-SB", fontSize=9.5, leading=12, spaceAfter=4)
sty_fig_entry = style("tf", fontSize=7.6, leading=9.8)
sty_fig_page = style("tfp", parent=sty_fig_entry, alignment=TA_RIGHT)
sty_guide = style("g", fontSize=7.6, leading=10.2, spaceAfter=4)
sty_level = style("sl", fontName="PlexC-SB", fontSize=7.4, leading=10.2)
sty_level_meaning = style("sm", parent=sty_guide, spaceAfter=0)


def model() -> Model:
    """Return the model of the current build."""
    if STATE.model is None:
        msg = "no model"
        raise RuntimeError(msg)
    return STATE.model


def page_of(key: str) -> str:
    """Return an anchor's page from the previous pass; until it is known, '?' (recorded as unresolved)."""
    if key in STATE.ref:
        return str(STATE.ref[key])
    STATE.unresolved.add(key)
    return "?"


def link(key: str, text: str) -> str:
    """Return markup linking text to an anchor."""
    return f'<a href="#{key}" color="{PALETTE["ink"]}">{text}</a>'


def pref(key: str) -> str:
    """Return a linked "p. N" reference to an anchor."""
    return link(key, f"p.{NBSP}{page_of(key)}")


REF_KEYS = re.compile(r"\{p:((?:sec|sub|card|fig|tab|ryv|act)-[\w.-]+)\}")  # the anchors the report defines
SEC_KEYS = re.compile(r"\{s:([\w.-]+)\}")  # section names in rule and guide text; an unknown key raises
LITERAL = {ord(c): chr(0xE000 + i) for i, c in enumerate("`*{")}  # private-use stand-ins for the markup characters
UNLITERAL = {0xE000 + i: c for i, c in enumerate("`*{")}


def escape(text: str) -> str:
    """Return text with &, <, and > escaped for Paragraph markup, as xml.sax.saxutils does without its slow import."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def lit(text: object) -> str:
    """Return input data for house-markup text: fmt() prints its backticks, asterisks, and braces as written."""
    return str(text).translate(LITERAL)


def esc(text: str) -> str:
    """Escape text for a Paragraph; U+FFFD switches to Plex Sans, as the condensed faces have no glyph for it."""
    mark = "\ufffd"  # the replacement character
    return escape(text).replace(mark, f'<font name="Plex">{mark}</font>')


def fmt(text: str, st: ParagraphStyle | None = None) -> str:
    """Escape text for a Paragraph and apply the house markup: `code`, **bold**, {p:key} links, {s:key} names.

    Only the report's own anchor and section keys expand, and input data passes through lit() first, so a file
    name, an inxi value, or a ry-verify field never takes markup.
    """
    out = esc(text)
    mono_size = (getattr(st, "fontSize", 9) if st else 9) - 0.7
    out = re.sub(r"`([^`]+)`", lambda m: f'<font name="PlexM" size="{mono_size:.1f}">{m.group(1)}</font>', out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", out)
    out = SEC_KEYS.sub(lambda m: section_ref(m.group(1)), out)  # an unknown key raises
    return REF_KEYS.sub(lambda m: pref(m.group(1)), out).translate(UNLITERAL)


def mono_markup(text: str, width: float, size: float) -> str:
    """Return text as monospace Paragraph markup broken into lines that fit width, keeping runs of spaces.

    A line breaks at its last space that fits, else after a slash or another separator, else at the width; a
    Paragraph alone would collapse the spaces and break a long path anywhere.
    """
    limit = max(8, int((width - 0.5) / (size * MONO_ADVANCE)))
    lines = []
    while len(text) > limit:
        cut = text.rfind(" ", len(text) - len(text.lstrip(" ")) + 1, limit + 1)
        if cut > 0:
            lines.append(text[:cut])
            text = text[cut + 1 :]
            continue
        cut = max(text.rfind(c, 0, limit) for c in "/,;:=&") + 1
        cut = cut if cut > limit // 3 else limit
        lines.append(text[:cut])
        text = text[cut:]
    lines.append(text)

    def keep(line: str) -> str:
        """Escape a line; a run of spaces turns into no-break spaces but its last, and leading spaces all."""
        line = re.sub(r"^ +", lambda m: NBSP * len(m.group(0)), escape(line))
        return re.sub(r" {2,}", lambda m: NBSP * (len(m.group(0)) - 1) + " ", line)

    return "<br/>".join(keep(line) for line in lines)


UNIT_SPACE = re.compile(r"(?<=\d) (?=(?:ms|s|min|h)\b)")  # the space between a number and its time unit


def prose(text: str) -> str:
    """Return house-markup prose with each number bound to its time unit by a no-break space, as 30 s."""
    return UNIT_SPACE.sub(NBSP, text)


def para(text: str, st: ParagraphStyle = sty_body) -> Paragraph:
    """Return a Paragraph from house-markup text."""
    return Paragraph(fmt(prose(text), st), st)


def bullet_paragraphs(texts: Iterable[str]) -> list[Flowable]:
    """Return bulleted body paragraphs from house-markup texts."""
    return [Paragraph(fmt(prose(t)), sty_bullet, bulletText="•") for t in texts]


STRUCT = (
    ("sec-1", 0, "1", "Summary"),
    ("sub-1.1", 1, "1.1", "Key facts"),
    ("sub-1.2", 1, "1.2", "Findings register"),
    ("sub-1.3", 1, "1.3", "Actions"),
    ("sec-2", 0, "2", "Findings"),
    ("sub-2.1", 1, "2.1", "Action needed"),
    ("sub-2.2", 1, "2.2", "Watch items and settings"),
    ("sec-3", 0, "3", "Actions"),
    ("sec-4", 0, "4", "System health"),
    ("sub-4.1", 1, "4.1", "Checks"),
    ("sub-4.2", 1, "4.2", "Error classes"),
    ("sec-5", 0, "5", "Boot"),
    ("sub-5.1", 1, "5.1", "Boot chart"),
    ("sub-5.2", 1, "5.2", "Phases and milestones"),
    ("sub-5.3", 1, "5.3", "Pauses"),
    ("sec-6", 0, "6", "dmesg"),
    ("sub-6.1", 1, "6.1", "Overview"),
    ("sub-6.2", 1, "6.2", "By subsystem and source"),
    ("sub-6.3", 1, "6.3", "Repeated warnings"),
    ("sub-6.4", 1, "6.4", "By rule family"),
    ("sec-7", 0, "7", "Journal"),
    ("sub-7.1", 1, "7.1", "Overview"),
    ("sub-7.2", 1, "7.2", "Timeline"),
    ("sub-7.3", 1, "7.3", "By process"),
    ("sub-7.4", 1, "7.4", "Repeated warnings"),
    ("sub-7.5", 1, "7.5", "Boot comparison"),
    ("sub-7.6", 1, "7.6", "Unit failures"),
    ("sec-8", 0, "8", "ry-verify"),
    ("sub-8.1", 1, "8.1", "Results by section"),
    ("sub-8.2", 1, "8.2", "Failures and warnings"),
    ("sub-8.3", 1, "8.3", "Notes"),
    ("sec-A", 0, "A", "Explained messages"),
    ("sec-B", 0, "B", "Inputs and method"),
    ("sub-B.1", 1, "B.1", "Inputs"),
    ("sub-B.2", 1, "B.2", "Parser cross-checks"),
    ("sub-B.3", 1, "B.3", "Method"),
)
SD = {key: (level, number, title) for key, level, number, title in STRUCT}


def hidden(m: Model) -> set[str]:
    """Return the STRUCT entries a report leaves out: a log section's subsections when the log holds nothing.

    dmesg without lines and a journal without entries each get a sentence under the section heading instead.
    """
    out = set()
    if not m.br.dmesg:
        out |= {key for key, *_ in STRUCT if key.startswith("sub-6.")}
    if not m.br.journal:
        out |= {key for key, *_ in STRUCT if key.startswith("sub-7.")}
    return out


def section_ref(key: str) -> str:
    """Return how running text names a STRUCT entry, Section 2.2 or Appendix B.2; raise ValueError for another key."""
    if key not in SD:
        msg = f"no section {key}"
        raise ValueError(msg)
    number = SD[key][1]
    return f"Appendix {number}" if number[0].isalpha() else f"Section {number}"


class HPara(Paragraph):
    """Numbered section heading that records its page, bookmark, and outline entry."""

    def __init__(self, key: str) -> None:
        """Compose the heading for a STRUCT key."""
        level, number, title = SD[key]
        self.key, self.level, self.toc = key, level, f"{number}  {title}"
        face = ' name="Plex-Md"' if level == 0 else ""
        color = PALETTE["faint"] if level == 0 else PALETTE["mute"]
        super().__init__(
            f'<font{face} color="{color}">{number}</font>{EN_SPACE}{escape(title)}', sty_h1 if level == 0 else sty_h2
        )

    def draw(self) -> None:
        """Record the page, add the bookmark and outline entry, and draw; level-0 headings get a rule."""
        canvas = self.canv
        STATE.anchors[self.key] = canvas.getPageNumber()
        canvas.bookmarkHorizontal(self.key, 0, self.height + 6)  # a link lands just above the heading
        canvas.addOutlineEntry(self.toc, self.key, level=self.level, closed=False)
        super().draw()
        if self.level == 0:
            canvas.setStrokeColor(INK)
            canvas.setLineWidth(1.1)
            canvas.line(0, -3.5, FW, -3.5)


class Anchor(Flowable):
    """Zero-size flowable marking a link target, optionally with an outline entry."""

    def __init__(self, key: str, outline: str | None = None, level: int = 1) -> None:
        """Store the target and, when an outline text is given, its outline entry and level."""
        super().__init__()
        self.key, self.outline, self.level = key, outline, level
        self.width = self.height = 0

    def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:  # noqa: ARG002, N803
        """Take no space."""
        return 0, 0

    def draw(self) -> None:
        """Record the page and add the bookmark, plus the outline entry when one is set."""
        canvas = self.canv
        STATE.anchors[self.key] = canvas.getPageNumber()
        canvas.bookmarkHorizontal(self.key, 0, 4)  # a link lands here, not at the top of the page
        if self.outline:
            canvas.addOutlineEntry(self.outline, self.key, level=self.level, closed=True)


def table_style(head: int) -> list[tuple[object, ...]]:
    """Return the house grid commands: ruled header at row `head` (0, or 1 under a title row), zebra rows below."""
    return [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEABOVE", (0, head), (-1, head), 1.0, INK),
        ("LINEBELOW", (0, head), (-1, head), 0.6, INK),
        ("LINEBELOW", (0, head + 1), (-1, -1), HAIR, RULE),
        ("BACKGROUND", (0, head), (-1, head), LIGHT),
        ("ROWBACKGROUNDS", (0, head + 1), (-1, -1), [colors.white, ZEBRA]),
        ("LEFTPADDING", (0, head), (-1, -1), 3.6),
        ("RIGHTPADDING", (0, head), (-1, -1), 3.6),
        ("TOPPADDING", (0, head), (-1, -1), 2.6),
        ("BOTTOMPADDING", (0, head), (-1, -1), 2.9),
        ("LINEBELOW", (0, -1), (-1, -1), 0.8, INK),
    ]


TITLE_ROW_STYLE = (
    ("SPAN", (0, 0), (-1, 0)),
    ("LEFTPADDING", (0, 0), (-1, 0), 0),
    ("RIGHTPADDING", (0, 0), (-1, 0), 0),
    ("TOPPADDING", (0, 0), (-1, 0), 0),
    ("BOTTOMPADDING", (0, 0), (-1, 0), 3.5),
)
ZERO_PAD = (
    ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ("TOPPADDING", (0, 0), (-1, -1), 0),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
)
CELL_PAD = 7.2  # pt, a house table cell's left and right padding together
Cell = tuple[int, int]


class HouseTable(Table):
    """A Table that moves whole when short, and otherwise splits leaving at least HOLD_ROWS data rows per side.

    ReportLab builds the parts without the split range, so a part that splits again would ignore HOLD_ROWS; the
    range is set from the table's own repeated rows before every split instead. A grouped table builds its second
    part itself, so a page that opens inside a group repeats that group's heading.
    """

    rest: Callable[[int], Flowable] | None = None  # a grouped table's data rows from an index on, as a new table
    body = 1  # rows above the first data row: the header, and the title row when there is one

    def split(self, availWidth: float, availHeight: float) -> list[Flowable]:  # noqa: N803
        """Return the parts, or none for a table short enough to move to the next page whole."""
        if self.wrap(availWidth, availHeight)[1] <= WHOLE_TABLE:
            return []
        repeat = self.repeatRows  # a count, or the indexes of the rows to repeat
        held = max(repeat) + 1 if isinstance(repeat, (tuple, list)) else repeat
        self._rowSplitRange = (held + HOLD_ROWS, -HOLD_ROWS)
        parts = super().split(availWidth, availHeight)
        if len(parts) == 2 and self.rest is not None and isinstance(parts[0], Table):
            parts[1] = self.rest(len(parts[0]._cellvalues) - self.body)
        return parts


class HeadingGroup(KeepTogether):
    """The keepWithNext group: headings and lead paragraphs stay with what follows; a table may split under them.

    KeepTogether moves a group whole to the next page whenever it does not fit, which leaves most of a page
    blank before a long table. This group flows instead when its last flowable is a HouseTable that can split
    in the space left under the headings.
    """

    def split(self, aW: float, aH: float) -> list[Flowable]:  # noqa: N803
        """Return the content to flow, or defer to KeepTogether."""
        if (aW, aH) != getattr(self, "_wrapInfo", None):
            self.wrap(aW, aH)
        last = self._content[-1]
        if aH < self._H and isinstance(last, HouseTable):
            room = aH - (self._H - last.wrap(aW, aH)[1]) - 2  # below the headings and the table's space before
            if room > 0 and last.split(aW, room):
                return list(self._content)
        return super().split(aW, aH)


def cell(value: object, column: int, mono_cols: Sequence[int], right: Sequence[int], width: float) -> object:
    """Return a table cell: flowables and lists of them pass through; text becomes a Paragraph in the column style.

    Monospace columns keep their spacing and break long lines at spaces and slashes (mono_markup()); other text
    takes the house markup.
    """
    if isinstance(value, (Flowable, list)):
        return value
    if column in mono_cols:
        return Paragraph(mono_markup(str(value), width - CELL_PAD, TD_MONO), sty_td_mono)
    return Paragraph(fmt(str(value), sty_td), sty_td_right if column in right else sty_td)


def table(
    head: Sequence[str],
    rows: Sequence[Sequence[object]],
    widths: Sequence[float],
    *,
    title: str = "",
    mono: Sequence[int] = (),
    right: Sequence[int] = (),
    spans: Sequence[tuple[Cell, Cell]] = (),
    groups: Mapping[int, str] | None = None,
) -> list[Flowable]:
    """Return a house table: an optional numbered title, a ruled zebra grid, and a repeated header.

    The title is the grid's first row, so it never ends a page alone; a page split repeats the header and leaves
    at least HOLD_ROWS data rows on each side, else the table moves whole. Spans are given against the header row.
    Each data row index in groups is a heading that spans the table and stays with the row below it; a group's last
    two rows stay together, and a page that opens inside a group repeats its heading markup, marked (cont.). Raise
    ValueError when the column widths do not fill the text frame or a row has the wrong number of cells.
    """
    groups = groups or {}
    label = title or "untitled table"
    if len(widths) != len(head) or abs(sum(widths) - FW) > 0.5:
        msg = f"{label}: {len(widths)} widths summing to {sum(widths):g} pt for {len(head)} columns, frame {FW:g} pt"
        raise ValueError(msg)
    if any(len(row) != len(head) for row in rows):
        msg = f"{label}: every row needs {len(head)} cells"
        raise ValueError(msg)
    if not rows:
        rows = [["—", *[""] * (len(head) - 1)]]
    data: list[list[object]] = [
        [Paragraph(fmt(h, sty_th), sty_th_right if j in right else sty_th) for j, h in enumerate(head)]
    ]
    data += [[cell(value, j, mono, right, widths[j]) for j, value in enumerate(row)] for row in rows]
    first = 0  # the header row
    commands: list[tuple[object, ...]] = []
    if title:
        STATE.tables += 1
        number = f'<font color="{PALETTE["mute"]}">Table {STATE.tables}</font>{EN_SPACE}'
        caption = Paragraph(number + escape(title), sty_table_title)
        data.insert(0, [[Anchor(f"tab-{STATE.tables}"), caption], *[""] * (len(head) - 1)])
        first = 1
        commands += TITLE_ROW_STYLE
    commands += table_style(first)
    commands += [
        ("SPAN", (c0, r0 + first if r0 >= 0 else r0), (c1, r1 + first if r1 >= 0 else r1))
        for (c0, r0), (c1, r1) in spans
    ]
    heads = sorted(groups)
    for i, r in enumerate(heads):
        row = r + first + 1
        end = (heads[i + 1] if i + 1 < len(heads) else len(rows)) + first  # the group's last row
        commands += [
            ("SPAN", (0, row), (-1, row)),
            ("BACKGROUND", (0, row), (-1, row), LIGHT),
            ("LINEABOVE", (0, row), (-1, row), 0.6, INK),
            ("NOSPLIT", (0, row), (-1, min(row + 1, len(data) - 1))),
        ]
        if end - 1 > row:  # so a page never opens with the group's last record alone
            commands.append(("NOSPLIT", (0, end - 1), (-1, end)))
    grid = HouseTable(
        data, colWidths=list(widths), repeatRows=(first,), hAlign="LEFT", spaceBefore=7 if title else 0, spaceAfter=6
    )
    grid.setStyle(TableStyle(commands))
    if groups:
        grid.rest, grid.body = continuation(head, rows, widths, groups, mono, right), first + 1
    return [grid]


def continuation(
    head: Sequence[str],
    rows: Sequence[Sequence[object]],
    widths: Sequence[float],
    groups: Mapping[int, str],
    mono: Sequence[int],
    right: Sequence[int],
) -> Callable[[int], Flowable]:
    """Return the builder of a grouped table's second part: its data rows from index k on, as an untitled table.

    A part that opens inside a group starts with that group's heading, marked (cont.).
    """

    def rest(k: int) -> Flowable:
        """Return the data rows from k on, under their group's heading when k falls inside a group."""
        above = max((g for g in groups if g < k), default=None)
        lead: list[list[object]] = []
        moved = {g - k: text for g, text in groups.items() if g >= k}
        if above is not None and k not in groups:
            lead = [[Paragraph(f"{groups[above]} (cont.)", sty_td), *[""] * (len(head) - 1)]]
            moved = {0: groups[above]} | {g + 1: text for g, text in moved.items()}
        return table(head, [*lead, *rows[k:]], widths, mono=mono, right=right, groups=moved)[0]

    return rest


CHIP_COLORS = {"HIGH": (INK, colors.white, INK), "MED": (MUTE, colors.white, MUTE), "LOW": (colors.white, INK, INK)}


def chip_width(text: str) -> float:
    """Return a chip's width: its label in PlexC SemiBold 6.8 plus 9 pt of padding."""
    return stringWidth(text, "PlexC-SB", 6.8) + 9


def chip(text: str) -> Table:
    """Return a severity label as a small filled or bordered one-cell table."""
    fill, ink, border = CHIP_COLORS[text]
    box = Table([[Paragraph(f'<font color="{ink.hexval()}">{text}</font>', sty_chip)]], colWidths=[chip_width(text)],
                rowHeights=[11])  # fmt: skip
    box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), fill),
                ("BOX", (0, 0), (-1, -1), 0.7, border),
                *ZERO_PAD[:2],
                ("TOPPADDING", (0, 0), (-1, -1), 1.4),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return box


def card(f: Finding) -> KeepTogether:
    """Return a finding card: ID, title, and severity chip; area and a register link; then the rows.

    The rows are the evidence (up to EVIDENCE_LINES quotes), the lines with their counts, the explanation when
    the rule has one, and the action.
    """
    cw = chip_width(f.severity)
    header = Table(
        [[Paragraph(f.fid, sty_card_id), Paragraph(escape(f.title), sty_card_title), chip(f.severity)]],
        colWidths=[46, FW - 10 - 46 - cw, cw],  # the card's padding leaves FW - 10 for the spanned header
    )
    header.setStyle(
        TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), *ZERO_PAD, ("RIGHTPADDING", (1, 0), (1, 0), 8)])
    )
    meta = escape(f"{f.area} · Register, ") + pref("sub-1.2")
    quotes = [m.text for m in f.matches if m.text]
    shown = [Paragraph(mono_markup(q, FW - 72, TD_MONO), sty_td_mono) for q in quotes[:EVIDENCE_LINES]]
    if len(quotes) > EVIDENCE_LINES:
        shown.append(Paragraph(f"… and {len(quotes) - EVIDENCE_LINES:,} more (Lines row)", sty_label))
    counts = ", ".join(
        plural(n, *noun)
        for n, noun in (
            (f.count("journal", "current"), ("current-boot journal entry", "current-boot journal entries")),
            (f.count("journal", "previous"), ("previous-boot journal entry", "previous-boot journal entries")),
            (f.count("dmesg"), ("dmesg line",)),
            (f.count("inxi"), ("inxi line",)),
            (f.count("verify"), ("ry-verify record",)),
        )
        if n
    )
    lines = escape(f.lines()) + (f' <font color="{PALETTE["mute"]}">({counts})</font>' if counts else "")
    data: list[list[object]] = [[header, ""], [Paragraph(meta, sty_card_meta), ""]]
    if shown:
        data.append([Paragraph("Evidence", sty_label), shown])
    data.append([Paragraph("Lines", sty_label), Paragraph(lines, sty_td)])
    rows = [("Explanation", fmt(f.explanation, sty_card_value))] if f.explanation else []  # rule text: house markup
    rows.append(("Action", fmt(f.action, sty_card_value)))
    data += [[Paragraph(k, sty_label), Paragraph(v, sty_card_value)] for k, v in rows]
    body = Table(data, colWidths=[62, FW - 62], spaceAfter=9)
    body.setStyle(TableStyle(card_style(f.severity, has_evidence=bool(shown))))
    return KeepTogether([Anchor(f"card-{f.fid}", f"{f.fid}  {f.title}", 2), body])


def card_style(severity: str, *, has_evidence: bool) -> list[tuple[object, ...]]:
    """Return a card's table commands: box and left bar weighted by severity, rules, padding, evidence shading."""
    heavy = severity in ("HIGH", "MED")
    commands: list[tuple[object, ...]] = [
        ("SPAN", (0, 0), (-1, 0)),
        ("SPAN", (0, 1), (-1, 1)),
        ("BOX", (0, 0), (-1, -1), 1.0 if heavy else 0.7, INK),
        ("LINEBEFORE", (0, 0), (0, -1), 3.2 if heavy else 1.6, INK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.7, INK),
        ("LINEBELOW", (0, 1), (-1, -2), HAIR, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.4),
        ("TOPPADDING", (0, 0), (-1, 0), 4.5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4.5),
    ]
    if has_evidence:
        commands.append(("BACKGROUND", (1, 2), (1, 2), ZEBRA))
    return commands


def svgfig(name: str | None, title: str, caption: str) -> list[Flowable]:
    """Return a numbered figure from a rendered SVG (scaled to the frame width) with its caption, or nothing."""
    if name is None:
        return []
    from svglib.svglib import svg2rlg

    drawing = svg2rlg(str(STATE.chart_dir / f"{name}.svg"))
    if drawing is None:
        msg = f"unreadable figure {name}.svg"
        raise RuntimeError(msg)
    scale = FW / drawing.width
    drawing.width, drawing.height = drawing.width * scale, drawing.height * scale
    drawing.scale(scale, scale)
    num = len(STATE.figures) + 1
    STATE.figures.append((num, title))
    head = f'<font name="Plex-SBIt" color="{PALETTE["soft"]}">Figure {num}</font>{EN_SPACE}'
    text = Paragraph(head + fmt(prose(caption), sty_caption), sty_caption)
    return [KeepTogether([Anchor(f"fig-{num}"), Spacer(1, 2), drawing, text])]


def code_block(label: str, lines: Sequence[str]) -> list[Flowable]:
    """Return a labeled command block, one monospace line per command.

    The font shrinks (down to CODE_MIN_SIZE) so the longest command fits the frame on one line; a longer command
    wraps where mono_markup() breaks it.
    """
    longest = max((stringWidth(line, "PlexM", CODE_SIZE) for line in lines), default=0)
    size = CODE_SIZE if longest <= FW - 17 else max(CODE_MIN_SIZE, CODE_SIZE * (FW - 17) / longest)
    code = style("code-fit", parent=sty_code, fontSize=size, leading=size * 1.45)
    rows = [
        [Paragraph(escape(label), sty_code_label)],
        *[[Paragraph(mono_markup(line, FW - 16, size), code)] for line in lines],
    ]
    block = Table(rows, colWidths=[FW], spaceAfter=4)
    block.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), DARK),
                ("BACKGROUND", (0, 1), (-1, -1), ZEBRA),
                ("BOX", (0, 0), (-1, -1), 0.6, INK),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, 0), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 2.5),
                ("TOPPADDING", (0, 1), (-1, -1), 1.6),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 1.6),
                ("BOTTOMPADDING", (0, -1), (-1, -1), 5),
                ("TOPPADDING", (0, 1), (-1, 1), 5),
            ]
        )
    )
    return [block]


def sha16(raw: bytes) -> str:
    """Return the first 16 hex digits of a SHA256."""
    return hashlib.sha256(raw).hexdigest()[:16]


def severity_counts(m: Model) -> dict[str, int]:
    """Return the number of findings per severity."""
    return {s: sum(1 for f in m.findings if f.severity == s) for s in FINDING_LEVELS}


def verify_run_text(m: Model) -> str:
    """Return ry-verify's result as one house-markup sentence: its totals, or where it stopped before its checks.

    GEN_FAIL appears when ry-verify counted one; the preamble records the totals leave out are the health table's.
    """
    vj = m.vj
    name = f"ry-verify {lit(vj.meta('version'))}"
    if stop := vj.stopped:
        return f"{name} stopped before its checks (VJ {stop.no}: {lit(clip(quote(stop.text), QUOTE_CLIP))})"
    t = vj.totals
    counts = f"{t['ok']:,} OK, {t['fail']:,} FAIL, {t['warn']:,} WARN" + (
        f", {t['gen_fail']:,} GEN_FAIL" if t["gen_fail"] else ""
    )
    extra = "".join(f"; the {i.phase} phase stopped before its checks, VJ {i.no}" for i in vj.phase_stops)
    return f"{name}: {counts} ({'interrupted, ' if vj.interrupted else ''}{vj.status[1]}{extra})"


def verdict(m: Model) -> tuple[str, str]:
    """Return the cover verdict: a head line and a summary, both from the model."""
    c, (shown, gen) = severity_counts(m), generator_failures(m.vj)  # a GEN_FAIL record is a finding already
    if c["HIGH"] or c["MED"] or gen:
        n, parts = c["HIGH"] + c["MED"], []
        if n:
            levels = " and ".join(f"{c[k]} {k}" for k in ("HIGH", "MED") if c[k])
            parts.append(f"{levels} finding{'s' if n != 1 else ''}")
        if gen:
            parts.append(f"ry-verify logged {gen:,} {'more ' if shown else ''}GEN_FAIL")
        head = f"Attention — {'; '.join(parts)}."
    elif c["LOW"]:
        head = f"Healthy, with {c['LOW']} LOW finding{'s' if c['LOW'] != 1 else ''} to act on."
    else:
        head = "Healthy — no HIGH, MED, or LOW findings."
    acting = sum(c[k] for k in ("HIGH", "MED", "LOW"))
    split = ", ".join(f"{c[k]} {k}" for k in ("HIGH", "MED", "LOW") if c[k])
    need = f"{plural(acting, 'finding')} {'needs' if acting == 1 else 'need'} action ({split})" if acting else ""
    info = f"{plural(c['INFO'], 'INFO finding')} explained" if c["INFO"] else ""
    findings = "; ".join(x for x in (need, info) if x) or "No findings"
    mismatch, foreign = hardware_mismatch(m.vj), other_machine(m.vj, m.facts.get("cpu", ""))
    if foreign:
        item, theirs, ours = foreign
        target = (
            f" Its log comes from another machine (VJ {item.no}: {theirs} CPU, not {ours}), so it lowers no finding."
        )
    else:
        target = (
            f" Its {lit(m.vj.meta('profile'))} profile targets other hardware (VJ {mismatch.no})." if mismatch else ""
        )
    errors = error_findings(m)
    if errors:
        error = f"Error classes seen: {', '.join(f.fid for f in errors)}"
    elif m.br.journal:
        error = "No error class seen"
    else:
        error = f"No error class seen in dmesg; {no_journal(m)}"
    return head, f"{findings}. {verify_run_text(m)}.{target} {error}."


def verdict_panel(m: Model) -> Table:
    """Return the cover's verdict box: a black bar beside the verdict head and summary."""
    head, text = verdict(m)
    body = [
        Paragraph("VERDICT", sty_cover_label),
        Paragraph(fmt(head), sty_verdict_head),
        Paragraph(fmt(text, sty_verdict), sty_verdict),
    ]
    panel = Table([["", body]], colWidths=[6, FW - 6])
    panel.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, 0), INK),
                ("BACKGROUND", (1, 0), (1, 0), ZEBRA),
                ("LEFTPADDING", (1, 0), (1, 0), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return panel


def kpi_strip(m: Model) -> Table:
    """Return the cover's count strip: findings, watch items, dmesg lines and journal entries, ry-verify results."""
    c, t = severity_counts(m), m.vj.totals
    groups = (
        ("FINDINGS", [("HIGH", c["HIGH"]), ("MED", c["MED"]), ("LOW", c["LOW"]), ("INFO", c["INFO"])]),
        ("WATCH", [("ITEMS", sum(1 for f in m.others if f.severity == "WATCH"))]),
        ("LOGS", [("DMESG", len(m.br.dmesg)), ("JOURNAL", len(m.br.journal))]),
        ("RY-VERIFY", [("FAIL", t["fail"]), ("WARN", t["warn"]), ("GEN_FAIL", t["gen_fail"])]),
    )
    items = [i for _, g in groups for i in g]
    row0: list[object] = []
    for name, g in groups:
        row0 += [Paragraph(name, sty_kpi_group), *[""] * (len(g) - 1)]
    data = [
        row0,
        [Paragraph(f"{v:,}", sty_kpi_value) for _, v in items],
        [Paragraph(k, sty_kpi_label) for k, _ in items],
    ]
    strip = Table(data, colWidths=[FW / len(items)] * len(items), rowHeights=[13, 22, 12])
    commands: list[tuple[object, ...]] = [
        ("LINEBELOW", (0, 0), (-1, 0), HAIR, RULE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]
    col = 0
    for i, (_, g) in enumerate(groups):
        end = col + len(g) - 1
        commands += [("SPAN", (col, 0), (end, 0)), ("BOX", (col, 0), (end, -1), 0.9, INK)]
        if i == 1:
            commands.append(("BACKGROUND", (col, 0), (end, -1), LIGHT))
        col = end + 1
    strip.setStyle(TableStyle(commands))
    return strip


def tile_rows(m: Model) -> list[tuple[str, str, str]]:
    """Return the cover's system tiles (label, value, detail), each short enough for its box."""
    d, errors = m.facts, error_findings(m)
    in_dmesg = sum(1 for r in RULES if r.key in ERROR_RULES and "dmesg" in r.streams)
    checked = f"{len(ERROR_RULES)} classes checked" if m.br.journal else f"{in_dmesg} classes, dmesg only"
    tiles = [
        (
            "error classes",
            plural(len(errors), "finding") if errors else "None seen",
            ", ".join(f.fid for f in errors) if errors else checked,
        )
    ]
    if "Root mounted" in m.milestones:
        quiet = f"{gap_seconds(*m.quiet_gap):.2f} s without output first" if m.quiet_gap else "dmesg"
        tiles.append(("boot to root mount", f"{m.milestones['Root mounted']:.2f} s", quiet))
    failed = unit_failures(m)
    if failed is None:
        tiles.append(("unit failures", "—", no_journal(m)))
    else:
        current = [fid for boot, fid in failed if boot == "current"]
        owners = ", ".join(sorted(dict.fromkeys(fid for fid in current if fid), key=id_order))
        tiles.append(("unit failures", f"{len(current):,}", f"current boot · {owners}" if owners else "current boot"))
    if "vram" in d:
        tiles.append(
            ("GPU memory", f"{int(d['vram']):,} MiB", f"VRAM · GTT {int(d['gtt']):,} MiB" if "gtt" in d else "VRAM")
        )
    temps = sensor_temps(d.get("temperatures", ""))
    if temps:
        tiles.append(("temperatures", temps[0], " · ".join(temps[1:3])))
    return tiles


def health_tiles(m: Model) -> Table:
    """Return the cover's health tiles; there are always at least the error-class and unit-failure tiles."""
    cells = [
        [
            Paragraph(escape(c.upper()), sty_cover_label),
            Paragraph(escape(r), sty_tile_value),
            Paragraph(esc(clip(e, 44)), sty_tile_detail),
        ]
        for c, r, e in tile_rows(m)
    ]
    tiles = Table([cells], colWidths=[FW / len(cells)] * len(cells))
    tiles.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 0), (-1, 0), 2.0, INK),
                ("LINEBEFORE", (1, 0), (-1, -1), HAIR, RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return tiles


def doc_control(m: Model) -> Table:
    """Return the cover's document-control table."""
    br_size = f"{plural(len(m.br.raw), 'byte')} · {plural(len(m.br.lines), 'line')}"
    vj_size = f"{plural(len(m.vj.raw), 'byte')} · {plural(len(m.vj.records), 'record')}"
    rows = [
        ("Bug report", f"`{lit(m.br.path.name)}` · {br_size} · SHA256 {sha16(m.br.raw)}…"),
        ("ry-verify log", f"`{lit(m.vj.path.name)}` · {vj_size} · SHA256 {sha16(m.vj.raw)}…"),
        ("Captured", lit(capture_text(m)) or "not stated in the bug report"),
        ("Generated by", f"build_report.py {__version__}"),
    ]
    table_ = Table(
        [[Paragraph(k, sty_doc_key), Paragraph(fmt(v, sty_td), sty_td)] for k, v in rows], colWidths=[80, FW - 80]
    )
    table_.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 0), (-1, 0), 0.8, INK),
                ("LINEBELOW", (0, 0), (-1, -1), HAIR, RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2.2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.4),
            ]
        )
    )
    return table_


def machine_line(m: Model) -> str:
    """Return the cover's machine line from inxi and the ry-verify header, as house-markup text."""
    f = m.facts
    parts = [
        f.get("machine", UNKNOWN_MACHINE),
        f.get("distro", ""),
        f"Linux {f['kernel']}" if "kernel" in f else "",
        f"ry-verify {m.vj.meta('version')}",
    ]
    return " · ".join(lit(p) for p in parts if p)


TOC_W = FW * 0.62  # pt, the sections column of the contents page; the side column takes the rest
TOC_GAP = 16  # pt between each column and the rule that divides them


def toc_sections(m: Model) -> Table:
    """Return the sections column of the contents page, with page numbers."""
    shown = [entry for entry in STRUCT if entry[0] not in hidden(m)]
    rows = []
    for key, level, number, title in shown:
        st, page = (sty_toc0, sty_toc0_page) if level == 0 else (sty_toc1, sty_toc1_page)
        rows.append(
            [Paragraph(link(key, f"{number}{EN_SPACE}{escape(title)}"), st), Paragraph(link(key, page_of(key)), page)]
        )
    sections = Table(rows, colWidths=[TOC_W - TOC_GAP - 40, 40])
    rules = [("LINEABOVE", (0, i), (-1, i), HAIR, RULE) for i, s in enumerate(shown) if s[1] == 0 and i]
    padding = [("TOPPADDING", (0, 0), (-1, -1), 1.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.2)]
    sections.setStyle(TableStyle([*ZERO_PAD[:2], *padding, *rules]))
    return sections


READING_GUIDE = (
    "**IDs** — H, M, L, I: HIGH, MED, LOW, and INFO findings, numbered by severity and first line.",
    "**Lines** — BR: a bug report line; VJ: a ry-verify log line; quotes are masked.",
    "**Cur., Prev.** — a finding's journal entries in the current and the previous boot.",
)
SEVERITY_SCALE = (
    ("HIGH", "Data loss, crashes, hardware at risk, or a broken function."),
    ("MED", "Degraded function, a failed ry-verify check, or a kernel taint."),
    ("LOW", "Limited or conditional impact; a quick action clears it."),
    ("INFO", "Explained and harmless."),
    ("WATCH", "A limit or link state to watch; not a finding."),
    ("SETTING", "A configuration choice the logs show; not a finding."),
    ("NOTE", "Boilerplate, or a call trace under a finding's event; counted in {s:sec-6} only."),
)


def toc_side() -> list[Flowable]:
    """Return the side column of the contents page: the figure list, the reading guide, and the severity levels."""
    width = FW - TOC_W - TOC_GAP
    side: list[Flowable] = []
    if STATE.fig_ref:
        figs = [
            [
                Paragraph(link(f"fig-{n}", f'<font color="{PALETTE["mute"]}">Figure {n}</font>{EN_SPACE}{escape(t)}'),
                          sty_fig_entry),
                Paragraph(link(f"fig-{n}", page_of(f"fig-{n}")), sty_fig_page),
            ]
            for n, t in STATE.fig_ref
        ]  # fmt: skip
        fig_list = Table(figs, colWidths=[width - 22, 22])
        padding = [("TOPPADDING", (0, 0), (-1, -1), 1.3), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.3)]
        fig_list.setStyle(TableStyle([*ZERO_PAD[:2], *padding, ("LINEBELOW", (0, 0), (-1, -2), HAIR, RULE)]))
        side += [Paragraph("Figures", sty_side_head), fig_list, Spacer(1, 14)]
    side.append(Paragraph("How to read this report", sty_side_head))
    side += [Paragraph(fmt(g, sty_guide), sty_guide) for g in READING_GUIDE]
    scale = Table(
        [[Paragraph(k, sty_level), Paragraph(fmt(v, sty_level_meaning), sty_level_meaning)] for k, v in SEVERITY_SCALE],
        colWidths=[44, width - 44],
    )
    padding = [("TOPPADDING", (0, 0), (-1, -1), 1.3), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.3)]
    scale.setStyle(
        TableStyle(
            [*ZERO_PAD[:2], *padding, ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -2), HAIR, RULE)]
        )
    )
    return [*side, Spacer(1, 10), Paragraph("Severity levels", sty_side_head), scale]


def toc(m: Model) -> Table:
    """Return the contents page: sections with pages beside the figure list, the reading guide, and the levels."""
    outer = Table([[toc_sections(m), toc_side()]], colWidths=[TOC_W, FW - TOC_W])
    outer.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                *ZERO_PAD,
                ("RIGHTPADDING", (0, 0), (0, 0), TOC_GAP),
                ("LEFTPADDING", (1, 0), (1, 0), TOC_GAP),
                ("LINEBEFORE", (1, 0), (1, 0), HAIR, RULE),
            ]
        )
    )
    return outer


def _story_cover(m: Model) -> list[Flowable]:
    """Return the cover page and the contents page."""
    captured = capture_text(m)
    return [
        Spacer(1, 16),
        Paragraph("Post-Boot Log Analysis", sty_cover_title),
        para(machine_line(m), sty_cover_line),
        para(f"Captured {lit(captured)}" if captured else "Capture time not stated", sty_cover_date),
        verdict_panel(m),
        Spacer(1, 10),
        kpi_strip(m),
        Spacer(1, 14),
        health_tiles(m),
        Spacer(1, 12),
        Paragraph("Document control", sty_cover_head),
        doc_control(m),
        NextPageTemplate("body"),
        PageBreak(),
        Paragraph("Contents", sty_contents),
        toc(m),
        PageBreak(),
    ]


def journal_span(entries: Sequence[JournalEntry]) -> str:
    """Return the wall-clock span of journal entries: one time for one entry, the end's date only when it differs."""
    first, last = min(e.when for e in entries), max(e.when for e in entries)
    if first == last:
        return f"{first:%Y-%m-%d %H:%M:%S}"
    end = f"{last:%H:%M:%S}" if last.date() == first.date() else f"{last:%Y-%m-%d %H:%M:%S}"
    return f"{first:%Y-%m-%d %H:%M:%S} – {end}"


def key_facts(m: Model) -> list[str]:
    """Return the key-fact bullets as house-markup text, each built from the inputs."""
    f = m.facts
    gpu = re.sub(r"^Advanced Micro Devices \[AMD(?:/ATI)?\]", "AMD", f.get("gpu", ""))  # inxi's PCI vendor name
    hardware = [f.get("machine", ""), f"CPU {f['cpu']}" if "cpu" in f else "", f"GPU {gpu}" if gpu else ""]
    hw = "; ".join(lit(x) for x in hardware if x)
    out = [f"Hardware: {hw}." if hw else "Hardware: inxi block not found."]
    software = [
        f"kernel {f['kernel']}" if "kernel" in f else "",
        f.get("desktop", ""),
        f"Mesa {f['mesa']}" if "mesa" in f else "",
        f"PipeWire {f['pipewire']}" if "pipewire" in f else "",
    ]
    named = [lit(s) for s in software if s]
    out.append(f"Software: {', '.join(named)}." if named else "Software: not stated in the bug report.")
    out.append(verify_fact(m))
    cur = sum(1 for e in m.br.journal if e.boot == "current")
    prev = [e for e in m.br.journal if e.boot == "previous"]
    if m.br.journal:
        span = f" ({journal_span(prev)})" if prev else ""
        journal = (
            f"{plural(cur, 'journal entry', 'journal entries')} in the current boot, {len(prev):,} in the previous"
            f"{span}" + (f"; {no_journal(m)}" if m.br.journal_unparsed else "")
        )
    else:
        journal = no_journal(m)
    out.append(f"Logs: {plural(len(m.br.dmesg), 'dmesg line')}; {journal}.")
    early = [(name.lower(), a, b) for name, a, b in boot_phases(m) if name != "Userspace"]
    phases = ", ".join(f"{name} {gap_seconds(a, b):.2f} s" for name, a, b in early)
    boot = [phases] if phases else []
    if "Root mounted" in m.milestones:
        gap = f" after {gap_seconds(*m.quiet_gap):.2f} s without output" if m.quiet_gap else ""
        boot.append(f"root mounted at {m.milestones['Root mounted']:.2f} s{gap}")
    if (wifi := m.milestones.get("Wi-Fi associated")) is not None:
        boot.append(f"Wi-Fi associated at {wifi:.2f} s")
    if boot:
        out.append(f"Boot: {'; '.join(boot)}.")
    return out


def verify_fact(m: Model) -> str:
    """Return the ry-verify key fact: version, profile, and mode, the result, and what the totals leave out."""
    vj = m.vj
    verdict_, exit_ = vj.status
    result = f"stopped before its checks (VJ {vj.stopped.no})" if vj.stopped else f"result {verdict_} ({exit_})"
    result += "".join(f"; its {i.phase} phase stopped before its checks (VJ {i.no})" for i in vj.phase_stops)
    ryv = (
        f"ry-verify {lit(vj.meta('version'))}: profile {lit(vj.meta('profile'))}, mode {lit(vj.meta('mode'))}; {result}"
    )
    if foreign := other_machine(vj, m.facts.get("cpu", "")):
        item, theirs, ours = foreign
        ryv += f"; run on another machine (VJ {item.no}: {theirs} CPU, not {ours})"
    preamble = [i for i in vj.items if i.phase == "preamble" and i.counted in ("FAIL", "WARN")]
    if preamble:
        logged = Counter(i.status for i in preamble)
        counts = ", ".join(f"{logged[s]} {s}" for s in ("FAIL", "ERR", "WARN") if logged[s])
        refs = ", ".join(f"VJ {i.no}" for i in preamble)
        ryv += f"; preamble: {counts} ({refs}, {{s:sub-8.2}})"
    return f"{ryv}."


def jcounts(f: Finding) -> tuple[object, object]:
    """Return a finding's journal entries in the current and the previous boot, or dashes when it has none."""
    cur, prev = f.count("journal", "current"), f.count("journal", "previous")
    return (f"{cur:,}", f"{prev:,}") if cur or prev else ("—", "—")


def _story_summary(m: Model) -> list[Flowable]:
    """Return Section 1: key facts, the register of findings that call for action, and the actions."""
    out: list[Flowable] = [HPara("sec-1"), HPara("sub-1.1"), *bullet_paragraphs(key_facts(m)), HPara("sub-1.2")]
    acting = [f for f in m.findings if f.severity != "INFO"]
    if acting:
        journal = any(f.count("journal") for f in acting)  # without journal evidence, Cur. and Prev. are left out
        rows = [
            [f.fid, f.severity, lit(f.title), f.first_line(), *(jcounts(f) if journal else ()),
             f"{{p:{f.anchor or 'card-' + f.fid}}}"]
            for f in acting
        ]  # fmt: skip
        head = ["ID", "Sev.", "Finding", "First line", *(["Cur.", "Prev."] if journal else []), "Page"]
        widths = [30, 32, 290, 60, 30, 30, 40] if journal else [30, 32, 350, 60, 40]
        right = (4, 5, 6) if journal else (4,)
        out += table(head, rows, widths, title="Findings that call for action", right=right)
    else:
        out.append(para("No finding calls for action."))
    info = sum(1 for f in m.findings if f.severity == "INFO")
    if info:
        out.append(para(f"{{s:sec-A}} explains {plural(info, 'INFO finding')}."))
    out.append(HPara("sub-1.3"))
    if not m.actions:
        return [*out, para("The findings call for no action.")]
    rows = [[a.aid, a.title, a.why, f"{{p:act-{a.aid}}}"] for a in m.actions]
    return out + table(["ID", "Action", "Why", "Page"], rows, [32, 170, 270, 40], title="Actions", right=(3,))


def inputs_table(m: Model) -> list[Flowable]:
    """Return the inputs table: sizes, line counts, content, and full SHA256 of both files."""
    br_secs = ", ".join(n for n in SECTION_TITLES.values() if n in m.br.sections)
    phases = ", ".join(p for p in ("static", "runtime") if any(i.phase == p for i in m.vj.items))
    vj = m.vj
    rows: list[list[object]] = [
        [
            f"`{lit(m.br.path.name)}`",
            f"{len(m.br.raw):,}",
            f"{len(m.br.lines):,}",
            f"Sections found: {br_secs}; {plural(len(m.br.packages), 'package')} listed",
        ],
        [Paragraph("SHA256", sty_label), Paragraph(hashlib.sha256(m.br.raw).hexdigest(), sty_td_mono), "", ""],
        [
            f"`{lit(m.vj.path.name)}`",
            f"{len(m.vj.raw):,}",
            f"{len(text_lines(m.vj.raw)):,}",
            (
                f"{plural(len(vj.records), 'record')}; ry-verify {lit(vj.meta('version'))}, profile "
                f"{lit(vj.meta('profile'))}, mode {lit(vj.meta('mode'))}; phases: {phases or 'none'}"
            ),
        ],
        [Paragraph("SHA256", sty_label), Paragraph(hashlib.sha256(m.vj.raw).hexdigest(), sty_td_mono), "", ""],
    ]
    spans = [((1, 2), (3, 2)), ((1, 4), (3, 4))]
    return table(
        ["File", "Bytes", "Lines", "Content"], rows, [172, 46, 38, 256], title="Inputs", right=(1, 2), spans=spans
    )


def method_bullets() -> list[Flowable]:
    """Return the method bullets and the failure keyword set."""
    terms = f"{NBSP}· ".join(f"`{k.replace(' ', NBSP)}`" for k in KEYWORDS)  # breaks only after a dot or keyword
    count = f"{len(KEYWORDS)}, case-insensitive; a line a NOTE rule claims is not counted"
    return [
        *bullet_paragraphs(
            (
                "The bug report splits at its separator lines; quotes keep line numbers; counts are of messages.",
                f"{len(RULES)} rules recognize message classes; the first matching rule claims a line.",
                (
                    "A dmesg line belongs to the subsystem its prefix or wording names, an indented line to the "
                    "message above; a journal entry belongs to its process."
                ),
                (
                    "Boot phases: the kernel until it starts init, the initrd until the root switch, userspace until "
                    f"the dmesg burst ends at its first {BOOT_PAUSE:.0f} s pause after the last milestone."
                ),
                (
                    "ry-verify FAIL and GEN_FAIL records become MED findings and WARN records LOW ones, one per "
                    "phase, section, and status; ERR counts as FAIL and a generator failure as GEN_FAIL; the "
                    "hardware-mismatch warning is no finding."
                ),
                (
                    f"The previous boot's last {SHUTDOWN_WINDOW} s of journal entries also meet the shutdown-noise "
                    "patterns when they hold a line only a stop sequence logs."
                ),
                (
                    "A ry-verify OK record showing a mitigation lowers its finding to INFO, unless another record "
                    "contradicts it, it omits a device the evidence names, or the log names a CPU of another vendor."
                ),
                (
                    "Each kernel message logged once in both dmesg and the current journal bounds the kernel start to "
                    "one second; the estimate is the middle of the window they share, ± half its width."
                ),
                (
                    "Repeated warnings group messages that match but for their numbers and hex values after the first "
                    "word, which names the driver or device."
                ),
                "Severity follows the observed impact on this host, not the log level.",
            )
        ),
        KeepTogether(para(f"Failure keywords ({count}): {terms}.")),  # moves whole rather than split at the end
    ]


def timeline_rows(m: Model) -> list[list[str]]:
    """Return the boot and capture timeline rows in time order; times are local, as each source logs them.

    With a kernel-start estimate every row sorts by its time since the kernel start; without one, the previous boot
    comes first, then the milestones, then the captures.
    """
    start = m.kernel_start[0] if m.kernel_start else None
    points = capture_points(m)
    events: list[tuple[tuple[float, float], list[str]]] = []

    def order(group: int, when: dt.datetime | None, since: float | None = None) -> tuple[float, float]:
        """Return a row's sort key: its time since the kernel start when known, else its group and time."""
        if since is None and start and when:
            since = (when - start).total_seconds()
        if start and since is not None:
            return (0, since)
        return (group, since if since is not None else when.timestamp() if when else 0)

    prev = [e for e in m.br.journal if e.boot == "previous"]
    if prev:
        first = min(e.when for e in prev)
        events.append((order(0, first), ["Previous boot, journal", journal_span(prev), "—"]))
    if m.kernel_start:
        when, half = m.kernel_start
        shown = f"{when + dt.timedelta(microseconds=5000):%Y-%m-%d %H:%M:%S.%f}"[:-4]  # rounded to hundredths
        events.append(((0, 0.0), ["Kernel start (estimate)", f"{shown} ±{half:.2f} s", "0.00 s"]))
    events += [(order(1, None, t), [name, "—", f"{t:.2f} s"]) for name, t in m.milestones.items()]
    begin, end = (w.replace(tzinfo=None) if w else None for w in (m.vj.started, m.vj.finished))
    if begin:
        a, b = points.get("ry-verify starts"), points.get("ry-verify ends")
        if end:
            day = f"{end:%H:%M:%S}" if end.date() == begin.date() else f"{end:%Y-%m-%d %H:%M:%S}"
            span = f"{begin:%Y-%m-%d %H:%M:%S}" + (f" – {day}" if f"{end:%F %T}" != f"{begin:%F %T}" else "")
            since = f"≈{a:,.1f}–{b:,.1f} s" if a is not None and b is not None else "—"
            events.append((order(2, begin), ["ry-verify run", span, since]))
        else:
            since = f"≈{a:,.1f} s" if a is not None else "—"
            events.append((order(2, begin), ["ry-verify run starts", f"{begin:%Y-%m-%d %H:%M:%S}", since]))
    if m.br.captured:
        t = points.get("bug report")
        since = f"≈{t:,.1f} s" if t is not None else "—"
        events.append((order(2, m.br.captured), ["Bug report captured", f"{m.br.captured:%Y-%m-%d %H:%M:%S}", since]))
    return with_steps([row for _, row in sorted(events, key=lambda e: e[0])])


def with_steps(rows: list[list[str]]) -> list[list[str]]:
    """Return timeline rows with a step: the time since the row above, where both are timed from the kernel start.

    A range (the ry-verify run) is stepped to at its start and from at its end, or from its start by a row that falls
    inside it; a step from or to an estimated time is estimated.
    """
    out: list[list[str]] = []
    last: list[float] = []
    estimated = False
    for row in rows:
        times = [float(x.replace(",", "")) for x in re.findall(r"[\d,]+\.\d+", row[2])]
        approx = row[2].startswith("≈")
        step = "—"
        if times and (before := [t for t in last if t <= times[0]]):
            gap = times[0] - max(before)
            step = f"≈+{gap:,.1f} s" if approx or estimated else f"+{gap:,.2f} s"
        if times:
            last, estimated = times, approx
        out.append([*row, step])
    return out


def phase_rows(m: Model) -> list[list[str]]:
    """Return the boot phases table: each phase with its span, its duration, and the dmesg lines logged in it.

    A phase holds the lines from its start up to the next phase's; userspace, which ends on the burst's last line,
    holds that line too.
    """
    rows = []
    for name, a, b in boot_phases(m):
        lines = sum(1 for e in m.br.dmesg if a <= e.t < b or (name == "Userspace" and e.t == b))
        rows.append([name, f"{a:.2f}–{b:.2f} s", f"{gap_seconds(a, b):.2f} s", f"{lines:,}"])
    return rows


def phase_subsystem_rows(m: Model) -> tuple[list[str], list[list[object]]]:
    """Return the boot phases' names and one row per subsystem: its dmesg lines in each phase and during the boot.

    A phase holds the lines from its start up to the next phase's, userspace the burst's last line too, as in the
    phases table; During boot counts every line up to the burst's end, as the dmesg overview does, so it exceeds
    the phases' sum when dmesg bounds only some of them. Without a phase, there is no table.
    """
    phases, burst = boot_phases(m), boot_burst(m)
    if not phases or burst is None:
        return [], []
    rows: list[list[object]] = []
    for name, lines in subsystem_lines(m.br).items():
        counts = [sum(1 for e in lines if a <= e.t < b or (p == "Userspace" and e.t == b)) for p, a, b in phases]
        if during := sum(1 for e in lines if e.t <= burst):
            rows.append([name, *(f"{n:,}" for n in counts), f"{during:,}"])
    return [p for p, _, _ in phases], rows


def pause_rows(m: Model) -> list[list[object]]:
    """Return the longest pauses in dmesg during the boot's burst, in time order, with the lines around each."""
    burst = boot_burst(m)
    if burst is None:
        return []
    lines = [e for e in m.br.dmesg if e.t <= burst]
    gaps = [(b.t - a.t, a, b) for a, b in itertools.pairwise(lines) if b.t - a.t >= PAUSE_MIN]
    longest = sorted(gaps, key=lambda g: (-g[0], g[1].t))[:PAUSES]
    return [
        [
            f"{gap_seconds(a.t, b.t):.2f} s",
            f"{a.t:.2f}–{b.t:.2f} s",
            f"BR {a.no}: {clip(quote(a.text), PAUSE_QUOTE)}",
            f"BR {b.no}: {clip(quote(b.text), PAUSE_QUOTE)}",
        ]
        for _, a, b in sorted(longest, key=lambda g: g[1].t)
    ]


def subsystems_caption(m: Model) -> str:
    """Return the subsystem chart's caption: what a row, its bar, and a tick are, and the dmesg lines left out."""
    axis = boot_axis(m)
    drawn = [k for k, lines in subsystem_lines(m.br).items() if axis and any(e.t < axis[0] for e in lines)]
    text = (
        "One tick per dmesg line; a bar spans each subsystem's first to last line, the lines drawn beside its name; "
        f"rows follow the first line{', Other last' if OTHER in drawn else ''}."
    )
    outside = sum(1 for e in m.br.dmesg if axis and e.t >= axis[0])
    if outside:
        verb = "falls" if outside == 1 else "fall"
        text += f" {plural(outside, 'later line')} {verb} outside the chart; {section_ref('sub-6.2')} counts all."
    return text


def boot_caption(m: Model) -> str:
    """Return the boot chart's caption, naming what the chart shows and what it leaves to the timeline table."""
    axis = boot_axis(m)
    if axis is None:
        return ""
    xmax, drawn = axis
    parts = ["the boot phases"] if boot_phases(m) else []
    if any(v <= xmax for v in m.milestones.values()):
        parts.append("the milestones in dmesg")
    marks = f", with {' and '.join(parts)}" if parts else ""
    text = f"dmesg lines per {boot_bin(xmax):g} s, log scale{marks}."
    if m.quiet_gap:
        limit = "the root mount" if "Root mounted" in m.milestones else "switch-root"
        gap = f"{gap_seconds(*m.quiet_gap):.2f} s"
        if quiet_span(m, xmax):
            text += f" Shading: {gap} without output before {limit}."
        else:
            text += f" No output for {gap} before {limit}."
    if drawn and m.kernel_start:
        text += f" Capture points use the kernel-start estimate (±{m.kernel_start[1]:.2f} s)."
    known = [k for k, when in (("ry-verify starts", m.vj.started), ("bug report", m.br.captured)) if when]
    if left := [k for k in known if k not in drawn]:
        timed = capture_points(m)  # a capture timed before the kernel start is left out of it
        early = m.kernel_start is not None and any(k not in timed for k in left)
        late = any(k in timed for k in left)
        if early:
            which = "Captures from before this boot" + (" and later ones" if late else "")
        else:
            which = "Later captures" if drawn else "The captures"
        text += f" {which} appear in {section_ref('sub-5.2')} only."
    outside = sum(1 for e in m.br.dmesg if e.t >= xmax)
    if outside:
        text += f" {plural(outside, 'later dmesg line')} {'falls' if outside == 1 else 'fall'} outside the chart."
    return text


def _story_health(m: Model) -> list[Flowable]:
    """Return Section 4: the system health checks, then the lines each error class matched in each log."""
    rows = [[lit(check_), lit(result), lit(detail), lit(source)] for check_, result, detail, source in m.health]
    lead = "Lines per error class and log; 0 means checked and clean, and a dash means not read or empty."
    return [
        CondPageBreak(SECTION_ROOM),
        HPara("sec-4"),
        HPara("sub-4.1"),
        *table(["Check", "Result", "Detail", "Source"], rows, [118, 160, 176, 58], title="System health checks"),
        HPara("sub-4.2"),
        para(lead, sty_lead),
        *table(["Error class", "dmesg", "Current boot", "Previous boot", "Finding"], error_class_rows(m),
               [212, 70, 80, 80, 70], title="Error classes by log", right=(1, 2, 3)),
    ]  # fmt: skip


def error_class_rows(m: Model) -> list[list[object]]:
    """Return one row per error class: the lines it matched in dmesg and in each journal boot, and its finding.

    A dash marks a log the class does not read (core dumps are journal-only), an empty dmesg, or a journal boot without
    entries.
    """
    found = {f.key: f for f in m.findings}
    boots = [b for b in ("current", "previous") if any(e.boot == b for e in m.br.journal)]
    rows: list[list[object]] = []
    for rule in (r for r in RULES if r.key in ERROR_RULES):
        f = found.get(rule.key)
        dmesg = f"{f.count('dmesg') if f else 0:,}" if "dmesg" in rule.streams and m.br.dmesg else "—"
        journal = [f"{f.count('journal', b) if f else 0:,}" if b in boots else "—" for b in ("current", "previous")]
        rows.append([rule.title, dmesg, *journal, f.fid if f else "—"])
    return rows


def info_rows(m: Model) -> list[list[object]]:
    """Return one row per INFO finding: ID, message class with its meaning and notes, lines, journal counts."""
    rows: list[list[object]] = []
    for f in m.findings:
        if f.severity != "INFO":
            continue
        notes = "".join(f'<br/><font name="Plex-It">{esc(n)}</font>' for n in f.notes)  # PlexC has no italic
        text = [
            Paragraph(f"<b>{escape(f.title)}</b>", sty_td),
            Paragraph(fmt(f.explanation, sty_info_meaning) + notes, sty_info_meaning),
        ]
        rows.append([f.fid, text, f.lines(), *jcounts(f)])
    return rows


def no_boot(m: Model) -> str:
    """Return why the report shows no boot: no dmesg line at all, or a ring buffer that has lost the boot."""
    if not m.br.dmesg:
        return "The bug report holds no dmesg line"
    return f"dmesg no longer holds the boot: its first line comes {m.br.dmesg[0].t:.2f} s after the kernel start"


def _story_boot(m: Model) -> list[Flowable]:
    """Return Section 5: the boot chart, phases by subsystem, milestones and captures, and the longest pauses."""
    out: list[Flowable] = [CondPageBreak(SECTION_ROOM), HPara("sec-5"), HPara("sub-5.1")]
    out += svgfig(STATE_FIGS.get("boot"), "Boot chart", boot_caption(m)) or [para(f"{no_boot(m)}; no chart is drawn.")]
    out += svgfig(STATE_FIGS.get("subsystems"), "Boot activity by subsystem", subsystems_caption(m))
    out.append(HPara("sub-5.2"))
    if rows := phase_rows(m):
        out += table(["Phase", "From – to", "Duration", "dmesg lines"], rows, [128, 152, 116, 116],
                     title="Boot phases", right=(2, 3))  # fmt: skip
    else:
        out.append(para("dmesg holds no milestone that bounds a boot phase."))
    names, rows = phase_subsystem_rows(m)
    if rows:
        cols = len(names) + 1
        out += table(["Subsystem", *names, "During boot"], rows, [152, *[(FW - 152) / cols] * cols],
                     title="dmesg lines by subsystem and phase", right=tuple(range(1, cols + 1)))  # fmt: skip
    out += table(
        ["Event", "Wall clock (local)", "Since kernel start", "Step"],
        timeline_rows(m),
        [160, 196, 82, 74],
        title="Boot and capture timeline",
        right=(2, 3),
    )
    out.append(HPara("sub-5.3"))
    if pauses := pause_rows(m):
        out += table(
            ["Pause", "From – to", "Last line before", "First line after"],
            pauses,
            [46, 76, 195, 195],
            title="Longest dmesg pauses during boot, in time order",
            mono=(2, 3),
        )
    elif boot_axis(m):
        out.append(para(f"No pause in dmesg during the boot reaches {PAUSE_MIN:.2f} s."))
    else:
        out.append(para(f"{no_boot(m)}; no pause is measured."))
    return out


def _story_findings(m: Model) -> list[Flowable]:
    """Return Section 2: cards for the log findings that call for action, then the watch items and settings."""
    out: list[Flowable] = [CondPageBreak(SECTION_ROOM), HPara("sec-2"), HPara("sub-2.1")]
    logged = [f for f in m.findings if f.severity != "INFO" and not f.key.startswith("verify-")]
    checks = sum(1 for f in m.findings if f.key.startswith("verify-"))
    where = f"; the ry-verify findings are in {section_ref('sub-8.2')}" if checks else ""
    if logged:
        out += [para(f"Bug-report findings, by severity and first line{where}.", sty_lead)]
        out += [card(f) for f in logged]
    else:
        out.append(para(f"No bug-report finding calls for action{where}."))
    out.append(HPara("sub-2.2"))
    shown = [f for f in m.others if f.severity in ("WATCH", "SETTING")]
    if not shown:
        return [*out, para("No watch item or setting appears in the logs.")]
    rows = [
        [
            f.severity,
            f.title,
            f.matches[0].text if f.matches else "",
            f.lines(),
            f"{f.explanation} {f.action if f.action != 'None' else ''}".strip(),
        ]
        for f in shown
    ]
    head = ["Kind", "Item", "Evidence", "Lines", "Meaning"]
    return out + table(head, rows, [52, 100, 150, 70, 140], title="Watch items and settings", mono=(2,))


def _story_explained(m: Model) -> list[Flowable]:
    """Return Appendix A: the INFO findings, messages a rule explains as expected on this host."""
    out: list[Flowable] = [CondPageBreak(SECTION_ROOM), HPara("sec-A")]
    if not (rows := info_rows(m)):
        return [*out, para("No finding is INFO.")]
    head = ["ID", "Message class and meaning", "Lines", "Cur.", "Prev."]
    return out + table(head, rows, [34, 290, 116, 36, 36], title="Explained messages", right=(3, 4))


def failure_rows(m: Model) -> tuple[list[list[object]], dict[int, str]]:
    """Return the records counted as FAIL, GEN_FAIL, or WARN, under a heading row per phase and section, in log order.

    The second value maps each heading row to its markup; each heading anchors its section's finding.
    """
    groups: dict[tuple[str, str], list[VerifyItem]] = {}
    for i in m.vj.items:
        if i.counted in ("FAIL", "GEN_FAIL", "WARN"):
            groups.setdefault((i.phase, i.section), []).append(i)
    rows: list[list[object]] = []
    heads: dict[int, str] = {}
    kinds = (("FAIL", "FAIL"), ("FAIL", "GEN_FAIL"), ("ERR", "FAIL"), ("WARN", "WARN"))  # (as logged, as counted)
    for (phase, section), items in groups.items():
        logged = Counter((i.status, i.counted) for i in items)
        counts = ", ".join(
            f"{logged[k]:,} {k[0]}" + (f" counted as {k[1]}" if k[0] != k[1] else "") for k in kinds if logged[k]
        )
        heading = f"<b>{escape(section_title(section))}</b> · {phase} · {counts}"
        heads[len(rows)] = heading
        rows.append([[Anchor(verify_anchor(phase, section)), Paragraph(heading, sty_td)], "", ""])
        rows += [[f"VJ {i.no}", i.status, quote(i.text)] for i in items]
    return rows, heads


def verify_caption(m: Model) -> str:
    """Return the ry-verify chart's caption, naming the phases drawn and the preamble records left out."""
    phases = [p for p in ("static", "runtime") if any(s[0] == p for s in m.verify_sections)]
    if not phases:
        return ""  # no figure to caption
    where = "static phase above, runtime below, on one scale" if len(phases) > 1 else f"{phases[0]} phase only"
    before = Counter(i.counted for i in m.vj.items if i.phase == "preamble")
    listed = {"FAIL": "sub-8.2", "GEN_FAIL": "sub-8.2", "WARN": "sub-8.2", "INFO": "sub-8.3"}  # OK records: no list
    refs = {s: f" ({section_ref(listed[s])})" if s in listed else "" for s in before}
    parts = [f"{before[s]:,} {s}{refs[s]}" for s in STATUS_ORDER if before[s]]
    left_out = ""
    if parts:
        joined = parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"
        left_out = f" Not drawn: {joined} from the preamble."
    return f"Records per section and status, {where}.{left_out}"


def _story_verify(m: Model) -> list[Flowable]:
    """Return Section 8: ry-verify results by section, its failures and warnings, and its notes."""
    out: list[Flowable] = [CondPageBreak(SECTION_ROOM), HPara("sec-8"), HPara("sub-8.1")]
    out += svgfig(STATE_FIGS.get("verify"), "ry-verify results by section", verify_caption(m)) or [
        para("The log holds no result records from a verification phase.")
    ]
    out.append(HPara("sub-8.2"))
    rows, heads = failure_rows(m)
    if rows:
        out += table(
            ["Record", "Status", "Text"],
            rows,
            [50, 44, 418],
            title="ry-verify failures and warnings, by section",
            mono=(2,),
            groups=heads,
        )
    else:
        out.append(para("No ry-verify check reported FAIL or WARN."))
    out.append(HPara("sub-8.3"))
    notes = [[f"VJ {i.no}", lit(section_title(i.section)), quote(i.text)] for i in m.vj.items if i.status == "INFO"]
    if not notes:
        return [*out, para("ry-verify logged no INFO record.")]
    return out + table(["Record", "Section", "Text"], notes, [50, 110, 352], title="ry-verify notes", mono=(2,))


def journal_caption(m: Model) -> str:
    """Return the journal timeline's caption: each panel's bar width and clock, and the tick rows a rule matches."""
    boots = journal_boots(m)
    if not boots:
        return ""  # no figure to caption
    panels = {boot: journal_bars(*journal_times(m, boot)) for boot in boots}
    unit = journal_unit(m, "current")
    clock = {"current": f"{unit[1].lower()} since the kernel start" if unit else "wall clock", "previous": "wall clock"}
    widths = " and per ".join(f"{width_text(w)} in the {boot} boot ({clock[boot]})" for boot, (_, w) in panels.items())
    peak = max(n for bars, _ in panels.values() for _, n in bars)
    text = f"Journal entries per {widths}{', log scale' if peak >= JOURNAL_LOG else ''}"
    rows = journal_timeline_rows(m)
    return text + ("; below, one tick per rule-matched entry, one row per finding or class." if rows else ".")


def by_finding_caption(m: Model) -> str:
    """Return the by-finding chart's caption."""
    others = " or message class" if any(f.count("journal") for f in m.others) else ""
    return f"Journal entries per finding{others}, the current boot above the previous."


def by_finding_absent(m: Model) -> str:
    """Return why the by-finding chart is not drawn: no matched entry, or matched entries in one boot only."""
    rows = by_finding_rows(m)
    if not rows:
        return "No journal entry matches a rule; no chart is drawn."
    boot = "current" if any(r[1] for r in rows) else "previous"
    return f"Only the {boot} boot has entries a rule matches; {section_ref('sub-7.2')} times them."


def after_caption(m: Model) -> str:
    """Return the after-boot chart's caption: what a tick and a row are, from when, in which unit, and the suspends."""
    span, burst = after_span(m), burst_end(m)
    if span is None or burst is None:
        return ""  # no figure to caption
    end, cut = burst
    unit = time_unit(span[1] - span[0])[1].lower()
    if cut:
        since = f"after {end:.2f} s, where the boot chart's {BOOT_SPAN:g} s window cut the boot's burst"
    else:
        since = f"after the boot's burst ended at {end:.2f} s"
    text = f"One tick per dmesg line {since}, one row per subsystem; {unit} since the kernel start"
    if sleeps := after_suspends(m):
        marks = "a dashed line marks a suspend" if len(sleeps) == 1 else f"dashed lines mark {len(sleeps):,} suspends"
        text += f"; {marks}, during which dmesg time stops"
    return text + "."


def after_absent(m: Model) -> str:
    """Return why the after-boot chart is not drawn: dmesg has lost the boot, or no line follows its burst."""
    if boot_burst(m) is None:
        return "dmesg no longer holds the boot, so no line can be placed after it."
    return "No dmesg line follows the boot's burst."


def families_caption(m: Model) -> str:
    """Return the rule-family chart's caption, naming only the kinds of rows drawn."""
    kinds = {sev for _, _, sev in family_rows(m)}
    notes = "; the NOTE families share one bar" if "NOTE" in kinds else ""
    return f"dmesg lines per rule family, styled by disposition{notes}."


def processes_caption(m: Model) -> str:
    """Return the process chart's caption: the boots drawn and the processes folded into Other."""
    procs, rows = journal_processes(m.br), process_bars(m)
    boots = [b for b, i in (("current", 1), ("previous", 2)) if any(r[i] for r in rows)]
    which = "the current boot above the previous" if len(boots) > 1 else f"in the {boots[0]} boot only" if boots else ""
    folded = len(procs) - (PROCESS_BARS - 1) if len(procs) > PROCESS_BARS else 0
    other = (
        f"; past the {PROCESS_BARS - 1} busiest, {plural(folded, 'process', 'processes')} share the {OTHER} bar"
        if folded
        else ""
    )
    return f"Journal entries per process, {which}{other}."


def _story_dmesg(m: Model) -> list[Flowable]:
    """Return Section 6: the overview and after-boot lines, subsystems and sources, repeats, and rule families."""
    if not m.br.dmesg:
        return [CondPageBreak(SECTION_ROOM), HPara("sec-6"), para("The bug report holds no dmesg line.")]
    out: list[Flowable] = [CondPageBreak(SECTION_ROOM), HPara("sec-6"), HPara("sub-6.1")]
    head = ["Lines", "Span", "During boot", "After boot", "Subsystems", "Rule matches", "Keyword lines"]
    out += table(head, [dmesg_overview_row(m)], [52, 140, 62, 58, 62, 66, 72], title="dmesg overview",
                 right=(0, 2, 3, 4, 5, 6))  # fmt: skip
    out += svgfig(STATE_FIGS.get("after"), "dmesg after the boot", after_caption(m)) or [para(after_absent(m))]
    rows, lines, total = after_messages(m)
    if rows:
        out.append(para(after_lead(lines, total), sty_lead))
        head = ["Message", "Lines", "First", "Last", "Matched by"]
        out += table(head, rows, [272, 40, 56, 56, 88], title="dmesg messages after the boot", mono=(0,),
                     right=(1, 2, 3))  # fmt: skip
    out.append(HPara("sub-6.2"))
    head = ["Subsystem", "Lines", "First", "Last", "Keyword lines", "Matched by"]
    out += table(head, subsystem_rows(m), [112, 50, 58, 58, 70, 164], title="dmesg lines by subsystem",
                 right=(1, 2, 3, 4))  # fmt: skip
    rows, total = source_rows(m)
    if rows:
        if total > SOURCES:
            lead = f"{total:,} sources log dmesg lines with failure keywords; the table lists {SOURCES}, those of "
            out.append(para(f"{lead}findings that call for action first and explained ones last.", sty_lead))
        head = ["Source", "Subsystem", "Keyword lines", "Lines", "First", "Last", "Matched by"]
        out += table(head, rows, [138, 88, 60, 36, 58, 58, 74], title="dmesg sources with failure keywords",
                     mono=(0,), right=(2, 3, 4, 5))  # fmt: skip
    else:
        out.append(para("No dmesg source logs a failure keyword, boilerplate aside."))
    out.append(HPara("sub-6.3"))
    rows, total = dmesg_repeats(m)
    if rows:
        noun = "dmesg message with a failure keyword"
        out.append(para(repeats_lead(total, noun, noun.replace("message", "messages")), sty_lead))
        head = ["Message", "Lines", "First", "Last", "Matched by"]
        out += table(head, rows, [272, 40, 56, 56, 88], title="Repeated dmesg warnings", mono=(0,),
                     right=(1, 2, 3))  # fmt: skip
    else:
        out.append(para("No dmesg line with a failure keyword repeats, boilerplate aside."))
    out.append(HPara("sub-6.4"))
    return out + (
        svgfig(STATE_FIGS.get("families"), "dmesg lines by rule family", families_caption(m))
        or [para("No dmesg line matches a rule.")]
    )


def _story_journal(m: Model) -> list[Flowable]:
    """Return Section 7: the overview, timeline, processes, repeated messages, boot comparison, unit failures.

    Without journal entries the section holds a sentence, and the overview when journal lines could not be read.
    """
    out: list[Flowable] = [CondPageBreak(SECTION_ROOM), HPara("sec-7")]
    if rows := journal_overview_rows(m):
        head = ["Boot", "Span (local)", "Entries", "Processes", "Rule matches", "Keyword entries", "Unit failures"]
        overview = table(head, rows, [52, 154, 82, 52, 56, 60, 56], title="Journal overview", right=(2, 3, 4, 5, 6))
    else:
        overview = []
    if not m.br.journal:
        return [*out, para(f"{no_journal(m)[0].upper()}{no_journal(m)[1:]}."), *overview]
    out += [HPara("sub-7.1"), *overview, HPara("sub-7.2")]
    out += svgfig(STATE_FIGS.get("journal"), "Journal timeline", journal_caption(m))
    out.append(HPara("sub-7.3"))
    out += svgfig(STATE_FIGS.get("processes"), "Journal entries by process", processes_caption(m))
    head = ["Process", "Current", "Previous", "Keyword entries", "Matched by"]
    out += table(head, process_rows(m), [150, 50, 50, 62, 200], title="Journal entries by process", right=(1, 2, 3),
                 mono=(0,))  # fmt: skip
    out.append(HPara("sub-7.4"))
    rows, total = journal_repeats(m)
    if rows:
        out.append(para(repeats_lead(total, "journal message", "journal messages"), sty_lead))
        head = ["Message", "Current", "Previous", "Matched by"]
        out += table(head, rows, [318, 50, 50, 94], title="Repeated journal warnings", mono=(0,), right=(1, 2))
    else:
        out.append(para("No journal entry repeats in either boot."))
    out.append(HPara("sub-7.5"))
    out += svgfig(STATE_FIGS.get("by-finding"), "Journal entries by finding", by_finding_caption(m)) or [
        para(by_finding_absent(m))
    ]
    if (new := new_messages(m)) is not None:
        rows, total = new
        if rows:
            most = f"; the table lists the {REPEATS} most frequent" if total > REPEATS else ""
            verb = "does" if total == 1 else "do"
            lead = (
                f"{plural(total, 'message')} of the current boot {verb} not appear in the previous one, numbers and "
                f"hex values aside{most}."
            )
            out.append(para(lead, sty_lead))
            out += table(["Message", "Entries", "First", "Matched by"], rows, [318, 50, 50, 94],
                         title="Journal messages new in the current boot", mono=(0,), right=(1,))  # fmt: skip
        else:
            out.append(para("Every message of the current boot also appears in the previous one, numbers aside."))
    out.append(HPara("sub-7.6"))
    if not (rows := unit_failure_rows(m)):
        return [*out, para("systemd logged no unit failure in either boot.")]
    head = ["Unit", "Manager", "Boot", "Result", "Line", "Finding"]
    return out + table(head, rows, [196, 52, 54, 86, 58, 66], title="Unit failures", mono=(0,))


def dmesg_overview_row(m: Model) -> list[object]:
    """Return the dmesg overview: lines, span, lines during and after the boot, subsystems, rule and keyword lines."""
    d = m.br.dmesg
    burst = boot_burst(m)
    during = sum(1 for e in d if e.t <= burst) if burst is not None else None
    named = [k for k in subsystem_lines(m.br) if k != OTHER]
    return [
        f"{len(d):,}",
        f"{min(e.t for e in d):.2f}–{max(e.t for e in d):.2f} s since kernel start",
        f"{during:,}" if during is not None else "—",
        f"{len(d) - during:,}" if during is not None else "—",
        f"{len(named):,}",
        f"{sum(f.count('dmesg') for f in [*m.findings, *m.others]):,}",
        f"{m.keyword_lines.get('dmesg', 0):,}",
    ]


def journal_overview_rows(m: Model) -> list[list[object]]:
    """Return one row per boot with journal content, none when neither boot has any.

    A row gives the span, the entries (and unread lines), processes, rule matches, entries with failure keywords
    (NOTE entries aside), and unit failures; a boot of which no line could be read counts nothing.
    """
    found = [*m.findings, *m.others]
    rows: list[list[object]] = []
    for boot in ("current", "previous"):
        entries = [e for e in m.br.journal if e.boot == boot]
        first, last = m.br.sections.get(f"journal-{boot}", (1, 0))
        unread = sum(1 for n in m.br.journal_unparsed if first <= n <= last)
        if not entries and not unread:
            continue
        size = f"{len(entries):,}" + (f"; {plural(unread, 'line')} unread" if unread else "")
        counts = (
            len({e.ident for e in entries}),
            sum(f.count("journal", boot) for f in found),
            m.keyword_lines.get(f"journal-{boot}", 0),
            sum(1 for e in entries if e.ident == "systemd" and UNIT_RESULT.match(e.text)),
        )
        span = journal_span(entries) if entries else "—"
        rows.append([boot.capitalize(), span, size, *(f"{n:,}" if entries else "—" for n in counts)])
    return rows


def id_order(label: str) -> tuple[int, int]:
    """Return the sort key of a finding ID (H-1 before I-10) or a rule kind (WATCH, SETTING, NOTE), kinds last."""
    if label in SEVERITY_RANK:
        return (10 + SEVERITY_RANK[label], 0)
    letter, _, number = label.partition("-")
    return ("HMLI".find(letter), int(number) if number.isdigit() else 0)


TEMPLATE_VALUE = re.compile(r"\b(?:0x[0-9a-fA-F]+|[0-9a-fA-F]*\d[0-9a-fA-F]*|[0-9a-fA-F]{8,})\b|\d+")  # numbers
REPEATS = 10  # repeated messages a repeats table lists, the most frequent first


def message_template(text: str) -> str:
    """Return a message as quoted, with each number and hex value as #, so repeats with other values group together.

    The first word, the driver or device name (r8169, ata1.00), stays as logged when it holds a letter; a number
    inside a word counts too, so CPU3 reads CPU# and 22s reads #s.
    """
    quoted = quote(text)
    head, sep, rest = quoted.partition(" ")
    if not (sep and re.search(r"[A-Za-z]", head)):
        head, sep, rest = "", "", quoted
    return head + sep + re.sub(r"#{2,}", "#", TEMPLATE_VALUE.sub("#", rest))  # CPU#3 reads CPU#, not CPU##


def shown_message(text: str, count: int) -> str:
    """Return how a messages table shows a group: one message quoted as logged, repeats as their template."""
    return message_template(text) if count > 1 else quote(text)


def dmesg_repeats(m: Model) -> tuple[list[list[object]], int]:
    """Return the most frequent dmesg messages with a failure keyword logged more than once, and how many repeat.

    Boilerplate (NOTE) lines are left out, and so are lines without a keyword, as a device per line enumerates. A row
    gives the message as a template, its lines, the first and last time, and the rules its lines match.
    """
    notes = {mt.no for f in m.others if f.severity == "NOTE" for mt in f.matches}
    owners = evidence_owners(m, "dmesg")
    groups: dict[str, list[DmesgEntry]] = {}
    for e in m.br.dmesg:
        if e.no not in notes and KEYWORD_RE.search(e.text):
            groups.setdefault(message_template(e.text), []).append(e)
    repeated = sorted((g for g in groups.values() if len(g) > 1), key=lambda g: (-len(g), g[0].no))
    rows: list[list[object]] = []
    for lines in repeated[:REPEATS]:
        ids = sorted(dict.fromkeys(i for e in lines for i in owners.get(e.no, [])), key=id_order)
        span = [f"{lines[0].t:.2f} s", f"{lines[-1].t:.2f} s"]
        rows.append([message_template(lines[0].text), f"{len(lines):,}", *span, ", ".join(ids) or "—"])
    return rows, len(repeated)


def after_messages(m: Model) -> tuple[list[list[object]], int, int]:
    """Return the first messages after the boot's burst, the lines after it, and how many messages they hold.

    Lines group as in the repeats tables; messages come in order of their first line, and a row gives the message, its
    lines, the first and last time, and the rules its lines match.
    """
    owners = evidence_owners(m, "dmesg")
    lines = sorted(after_lines(m), key=lambda e: (e.t, e.no))
    groups: dict[str, list[DmesgEntry]] = {}
    for e in lines:
        groups.setdefault(message_template(e.text), []).append(e)
    rows: list[list[object]] = []
    for group in list(groups.values())[:REPEATS]:
        ids = sorted(dict.fromkeys(i for e in group for i in owners.get(e.no, [])), key=id_order)
        span = [f"{group[0].t:.2f} s", f"{group[-1].t:.2f} s"]
        rows.append([shown_message(group[0].text, len(group)), f"{len(group):,}", *span, ", ".join(ids) or "—"])
    return rows, len(lines), len(groups)


def after_lead(lines: int, total: int) -> str:
    """Return the sentence above the after-boot messages: the lines, their messages, and what the table lists."""
    verb = "follows" if lines == 1 else "follow"
    held = f": {plural(total, 'message')}, numbers and hex values aside" if total < lines else ""
    most = f"; the table lists the {REPEATS} that come first" if total > REPEATS else ""
    return f"{plural(lines, 'dmesg line')} {verb} the boot's burst{held}{most}."


SOURCE_MAX = 40  # characters of a dmesg prefix that can name its source; a longer one is message text
SOURCES = 10  # sources the source table lists, the most keyword lines first


SOURCE_WORDING = (
    (re.compile(r"^(?:Out of memory|oom-kill:|oom_reaper:|Memory cgroup out of memory|\S+ invoked oom-killer:)"),
     "OOM killer"),
    (re.compile(r"^(?:\{\d+\})?\[Hardware Error\]"), "machine check"),
    (re.compile(r"I/O error,? (?:on )?dev ([\w-]+)"), "block {}"),  # the block layer names the device
    (re.compile(r"^([\w-]+) (?:error|warning|info|notice|critical) \(device ([\w-]+)\)"), "{} ({})"),  # BTRFS, ext4
    (re.compile(r"^\[drm:\S+ \[(\w+)\]\]"), "{} [drm]"),  # [drm:amdgpu_job_timedout [amdgpu]]
)  # fmt: skip


def dmesg_source(text: str) -> str:
    """Return what a dmesg line names as its source.

    Kernel splats, OOM kills, machine checks, block-layer and file-system errors, and DRM function tags have sources
    of their own; any other line's source is the prefix before its first ": " when that is short (a driver and its
    device, usb 1-4, an interface, or a subsystem, "(bug)" aside), else its first word.
    """
    if SPLAT_LINE.match(text):
        return "kernel splat"
    for pattern, name in SOURCE_WORDING:
        if hit := pattern.search(text):
            return name.format(*hit.groups())
    head, sep, _ = text.partition(": ")
    head = head.strip().removesuffix(" (bug)")
    if sep and 0 < len(head) <= SOURCE_MAX and head.count(" ") <= 2:
        return head
    return clip(text.split(" ", 1)[0], SOURCE_MAX)


def source_rows(m: Model) -> tuple[list[list[object]], int]:
    """Return the dmesg sources with failure-keyword lines, in table order, and how many there are.

    Sources whose keyword lines a finding that calls for action names come first, then those no rule explains, then
    explained ones; within each, the most keyword lines first. An indented line belongs to the source above, as to
    its subsystem; NOTE lines are left out, as in the keyword counts. A row gives the source, its subsystem, its
    keyword lines and all its lines, their first and last time, and the rules its lines match.
    """
    notes = {mt.no for f in m.others if f.severity == "NOTE" for mt in f.matches}
    owners = evidence_owners(m, "dmesg")
    subsystems = {e.no: name for name, lines in subsystem_lines(m.br).items() for e in lines}
    groups: dict[str, list[DmesgEntry]] = {}
    source = ""
    for e in m.br.dmesg:
        if not (e.text[:1].isspace() and source):
            source = dmesg_source(e.text.strip())
        groups.setdefault(source, []).append(e)
    keyword = {s: [e for e in lines if e.no not in notes and KEYWORD_RE.search(e.text)] for s, lines in groups.items()}

    def rank(s: str) -> tuple[int, int, int]:
        """Return a source's sort key: its tier, then the most keyword lines, then its first line."""
        ids = [i for e in keyword[s] for i in owners.get(e.no, [])]
        if any(i[:2] in ("H-", "M-", "L-") for i in ids):
            tier = 0  # a finding that calls for action
        elif any(not owners.get(e.no) for e in keyword[s]):
            tier = 1  # a keyword line no rule explains
        else:
            tier = 2  # explained, watched, or set
        return tier, -len(keyword[s]), groups[s][0].no

    flagged = sorted((s for s in groups if keyword[s]), key=rank)
    rows: list[list[object]] = []
    for s in flagged[:SOURCES]:
        lines = groups[s]
        span = [f"{min(e.t for e in lines):.2f} s", f"{max(e.t for e in lines):.2f} s"]
        ids = sorted(dict.fromkeys(i for e in lines for i in owners.get(e.no, [])), key=id_order)
        subsystem_ = subsystems.get(lines[0].no, OTHER)
        rows.append([quote(s), subsystem_, f"{len(keyword[s]):,}", f"{len(lines):,}", *span, ", ".join(ids) or "—"])
    return rows, len(flagged)


def new_messages(m: Model) -> tuple[list[list[object]], int] | None:
    """Return the current boot's journal messages the previous boot never logged, and how many there are.

    Messages compare by process and template, as in the repeats table, the most entries first; None unless both
    boots have entries. A row gives the message, its entries, the first one's time, and the rules they match.
    """
    previous = {(e.ident, message_template(e.text)) for e in m.br.journal if e.boot == "previous"}
    current = [e for e in m.br.journal if e.boot == "current"]
    if not previous or not current:
        return None
    owners = evidence_owners(m, "journal")
    groups: dict[tuple[str, str], list[JournalEntry]] = {}
    for e in current:
        if (key := (e.ident, message_template(e.text))) not in previous:
            groups.setdefault(key, []).append(e)
    ranked = sorted(groups.values(), key=lambda g: (-len(g), g[0].no))
    rows: list[list[object]] = []
    for entries in ranked[:REPEATS]:
        ids = sorted(dict.fromkeys(i for e in entries for i in owners.get(e.no, [])), key=id_order)
        message = f"{quote(entries[0].ident)}: {shown_message(entries[0].text, len(entries))}"
        rows.append([message, f"{len(entries):,}", f"{entries[0].when:%H:%M:%S}", ", ".join(ids) or "—"])
    return rows, len(ranked)


def journal_repeats(m: Model) -> tuple[list[list[object]], int]:
    """Return the most frequent journal messages logged more than once over both boots, and how many repeat.

    Messages group by process and template; a row gives both, the entries per boot, and the rules they match.
    """
    owners = evidence_owners(m, "journal")
    groups: dict[tuple[str, str], list[JournalEntry]] = {}
    for e in m.br.journal:
        groups.setdefault((e.ident, message_template(e.text)), []).append(e)
    repeated = sorted((g for g in groups.values() if len(g) > 1), key=lambda g: (-len(g), g[0].no))
    rows: list[list[object]] = []
    for entries in repeated[:REPEATS]:
        cur = sum(1 for e in entries if e.boot == "current")
        ids = sorted(dict.fromkeys(i for e in entries for i in owners.get(e.no, [])), key=id_order)
        message = f"{quote(entries[0].ident)}: {message_template(entries[0].text)}"
        rows.append([message, f"{cur:,}", f"{len(entries) - cur:,}", ", ".join(ids) or "—"])
    return rows, len(repeated)


def repeats_lead(total: int, noun: str, nouns: str) -> str:
    """Return the sentence above a repeats table: how many messages repeat, and whether the table lists them all."""
    most = f"; the table lists the {REPEATS} most frequent" if total > REPEATS else ""
    return f"{plural(total, noun, nouns)} {'repeats' if total == 1 else 'repeat'}, numbers and hex values aside{most}."


def evidence_owners(m: Model, stream: str) -> dict[int, list[str]]:
    """Return, per line of a stream, the IDs of the findings it is evidence for, then the kinds of the other rules."""
    owners: dict[int, list[str]] = {}
    for f in [*m.findings, *m.others]:
        for mt in f.matches:
            if mt.stream == stream:
                owners.setdefault(mt.no, []).append(f.fid or f.severity)
    return owners


def subsystem_rows(m: Model) -> list[list[object]]:
    """Return one row per dmesg subsystem: lines, first and last time, failure-keyword lines, and the rules matched.

    The rules are named by finding ID, and by kind (WATCH, SETTING, NOTE) for the others.
    """
    owners = evidence_owners(m, "dmesg")
    notes = {mt.no for f in m.others if f.severity == "NOTE" for mt in f.matches}
    rows: list[list[object]] = []
    for name, lines in subsystem_lines(m.br).items():
        ids = list(dict.fromkeys(i for e in lines for i in owners.get(e.no, [])))
        ids.sort(key=id_order)
        keyword = sum(1 for e in lines if e.no not in notes and KEYWORD_RE.search(e.text))
        first, last = min(e.t for e in lines), max(e.t for e in lines)
        rows.append([name, f"{len(lines):,}", f"{first:.2f} s", f"{last:.2f} s", f"{keyword:,}", ", ".join(ids) or "—"])
    return rows


def process_rows(m: Model) -> list[list[object]]:
    """Return the journal processes with their entries per boot, entries with failure keywords, and matching rules.

    Processes come most entries first; past PROCESS_ROWS, one last row counts the rest. Keyword entries leave out
    NOTE lines, as the overview counts them.
    """
    owners = evidence_owners(m, "journal")
    notes = {mt.no for f in m.others if f.severity == "NOTE" for mt in f.matches}
    by_process: dict[str, list[str]] = {}
    keyword: Counter[str] = Counter()
    for e in m.br.journal:
        by_process.setdefault(e.ident, []).extend(owners.get(e.no, []))
        keyword[e.ident] += e.no not in notes and bool(KEYWORD_RE.search(e.text))
    procs = journal_processes(m.br)
    rows: list[list[object]] = []
    for name, cur, prev in procs[:PROCESS_ROWS]:
        ids = sorted(dict.fromkeys(by_process.get(name, [])), key=id_order)
        rows.append([quote(name), f"{cur:,}", f"{prev:,}", f"{keyword[name]:,}", ", ".join(ids) or "—"])
    rest = procs[PROCESS_ROWS:]
    if rest:
        cur, prev = sum(r[1] for r in rest), sum(r[2] for r in rest)
        ids = sorted(dict.fromkeys(i for name, _, _ in rest for i in by_process.get(name, [])), key=id_order)
        label = Paragraph(escape(plural(len(rest), "more process", "more processes")), sty_td)
        hits = sum(keyword[name] for name, _, _ in rest)
        rows.append([label, f"{cur:,}", f"{prev:,}", f"{hits:,}", ", ".join(ids) or "—"])
    return rows


def unit_failure_rows(m: Model) -> list[list[object]]:
    """Return one row per unit failure systemd logged: unit, manager, boot, result, line, and finding."""
    owners = evidence_owners(m, "journal")
    rows: list[list[object]] = []
    for e in m.br.journal:
        hit = UNIT_RESULT.match(e.text) if e.ident == "systemd" else None
        if hit:
            owner = ", ".join(owners.get(e.no, [])) or "—"
            manager = "system" if e.pid == "1" else "user"
            rows.append([quote(hit.group(1)), manager, e.boot, lit(quote(hit.group(2))), f"BR {e.no}", owner])
    return rows


RANGES_CLIP = 36  # characters of the unread lines' ranges a cross-check cites: one line of its Result column


def cross_checks(m: Model) -> list[list[str]]:
    """Return checks of the parse against the inputs' own totals."""
    out = []
    for phase in ("static", "runtime"):
        res = m.vj.phase_results.get(phase)
        if res is None:
            continue
        sections = [s[2] for s in m.verify_sections if s[0] == phase]
        found = {k: sum(s[k] for s in sections) for k in ("OK", "FAIL", "WARN", "GEN_FAIL")}
        stated = {k: res.get(k.lower(), 0) for k in found}
        keys = [k for k in found if k != "GEN_FAIL" or found[k] or stated[k]]  # GEN_FAIL where either side has one
        said, gave = (", ".join(f"{d[k]:,} {k}" for k in keys) for d in (stated, found))
        result = "match" if found == stated else f"differs: records give {gave}"
        out.append([f"ry-verify {phase} records match its VERIFY_RESULT ({said})", result])
    if m.vj.combined and m.vj.footer:
        pairs = (("ok", "pass"), ("fail", "fail"), ("warn", "warn"), ("gen_fail", "gen_fail"))
        same = all(m.vj.combined.get(c, 0) == m.vj.footer.get(f, 0) for c, f in pairs)
        out.append(
            ["ry-verify combined totals match the footer (OK, FAIL, WARN, GEN_FAIL)", "match" if same else "differs"]
        )
    unparsed = m.br.journal_unparsed
    read = "match"
    if unparsed:
        read = f"differs: {len(unparsed):,} unread (BR {clip(ranges(unparsed), RANGES_CLIP)})"
    out.append(["Journal lines read as entries, continuations, or markers", read])
    if pairs := kernel_pairs(m.br):
        lo, hi = kernel_bounds(pairs)
        if lo is None or hi is None:
            agree = "no pair bounds it from below"
        else:
            agree = "match" if lo <= hi else "differs: no time fits every pair"
        label = f"dmesg and journal times of {plural(len(pairs), 'kernel message')} agree on the kernel start"
        out.append([label, agree])
    out.append(["Sections found in the bug report", f"{len(m.br.sections)} of {len(SECTION_TITLES)}"])
    return out


def _story_actions(m: Model) -> list[Flowable]:
    """Return Section 3: commands for each action, then the checklist with each completion test."""
    out: list[Flowable] = [CondPageBreak(SECTION_ROOM), HPara("sec-3")]
    if not m.actions:
        return [*out, para("The findings call for no action.")]
    out.append(para("Fish commands for each action, one per line; tick the checklist as each test passes.", sty_lead))
    out.extend(
        KeepTogether(
            [
                Anchor(f"act-{a.aid}"),
                para(f"**{a.aid}** — {a.title}: {a.why}."),
                *code_block(f"{a.aid} COMMANDS", a.commands),
            ]
        )
        for a in m.actions
    )

    def box() -> Table:
        """Return an empty tick box."""
        return Table([[""]], colWidths=[9], rowHeights=[9], style=[("BOX", (0, 0), (-1, -1), 0.9, INK)])

    rows = [[box(), a.aid, a.title, a.done_when, ""] for a in m.actions]
    return out + table(
        ["", "ID", "Action", "Done when", "Date / initials"], rows, [18, 34, 190, 190, 80], title="Checklist"
    )


def _story_appendix(m: Model) -> list[Flowable]:
    """Return Appendix B: the inputs, the parser cross-checks, and the method."""
    out: list[Flowable] = [CondPageBreak(SECTION_ROOM), HPara("sec-B"), HPara("sub-B.1"), *inputs_table(m)]
    checks = table(["Cross-check", "Result"], cross_checks(m), [372, 140], title="Parser cross-checks")
    out += [HPara("sub-B.2"), *checks]
    return [*out, HPara("sub-B.3"), *method_bullets()]


STATE_FIGS: dict[str, str | None] = {}


def story(m: Model) -> list[Flowable]:
    """Return all flowables in reading order; figure and table numbers restart each pass."""
    STATE.figures.clear()
    STATE.tables = 0
    parts = (
        _story_cover,
        _story_summary,
        _story_findings,
        _story_actions,
        _story_health,
        _story_boot,
        _story_dmesg,
        _story_journal,
        _story_verify,
        _story_explained,
        _story_appendix,
    )
    return [flowable for part in parts for flowable in part(m)]


class Doc(BaseDocTemplate):
    """US Letter document with cover and body page templates and the report metadata."""

    def __init__(self, filename: str, m: Model) -> None:
        """Set the metadata, frames, and page templates."""
        super().__init__(
            filename, pagesize=letter, leftMargin=LM, rightMargin=RM, topMargin=TOPM, bottomMargin=BOTM,
            title=f"Post-Boot Log Analysis ({capture_day(m)})",
            author="build_report.py",
            subject=f"Findings from {m.br.path.name} and {m.vj.path.name}",
            keywords="CachyOS, cachyos-bugreport, ry-verify, dmesg, journal, log analysis",
            creator=f"build_report.py {__version__} (ReportLab)",
            keepTogetherClass=HeadingGroup,  # keepWithNext groups that let a table split under its heading
        )  # fmt: skip
        pad = {"leftPadding": 0, "rightPadding": 0, "topPadding": 0, "bottomPadding": 0}
        body = Frame(LM, BOTM, FW, PH - TOPM - BOTM, id="f", **pad)
        cover = Frame(LM, BOTM, FW, PH - COVER_TOP - BOTM, id="fc", **pad)
        self.addPageTemplates(
            [PageTemplate("cover", [cover], onPage=on_cover), PageTemplate("body", [body], onPage=on_body)]
        )

    # ReportLab calls these hooks by name: the first before placing a flowable, the second after.
    def handle_keepWithNext(self, flowables: list[Flowable]) -> None:  # noqa: N802
        """Group keepWithNext flowables with the next one; a KeepTogether next joins the group by its content.

        ReportLab ends the group before a KeepTogether, so a heading or lead paragraph above a figure, a card, or
        an action block could end a page alone.
        """
        i = 0
        while i < len(flowables) and flowables[i].getKeepWithNext():
            i += 1
        nxt = flowables[i] if 0 < i < len(flowables) else None
        if isinstance(nxt, KeepTogether) and not isinstance(nxt, HeadingGroup):
            for f in flowables[:i]:
                f.__dict__["keepWithNext"] = 0  # as ReportLab's grouping does, so a split group never regroups
            flowables[: i + 1] = [HeadingGroup([*flowables[:i], *nxt._content])]
            return
        super().handle_keepWithNext(flowables)

    def afterFlowable(self, flowable: Flowable) -> None:  # noqa: N802
        """Note the page of each level-0 heading and whether it opens the page."""
        frame = self.frame
        if isinstance(flowable, HPara) and flowable.level == 0 and frame is not None:
            top = frame._y1 + frame._height  # frame position is only exposed as attributes
            heading = f"{SD[flowable.key][1]}{EN_SPACE}{SD[flowable.key][2]}"  # as the headings set it
            STATE.h1pos.setdefault(self.page, []).append((heading, (top - frame._y) < HEADING_TOP_BAND))


def capture_day(m: Model) -> str:
    """Return the capture date (bug report first, then ry-verify) as YYYY-MM-DD, for the file name and title."""
    if m.br.captured:
        return f"{m.br.captured:%Y-%m-%d}"
    return f"{m.vj.started:%Y-%m-%d}" if m.vj.started else "undated"


def capture_label(m: Model) -> str:
    """Return the date the page furniture shows, with what it dates: captured, or the ry-verify run, or neither."""
    if m.br.captured:
        return f"captured {m.br.captured:%Y-%m-%d}"
    return f"ry-verify run {m.vj.started:%Y-%m-%d}" if m.vj.started else "capture date not stated"


def capture_text(m: Model) -> str:
    """Return the capture time as YYYY-MM-DD HH:MM:SS plus the zone the date line names, else the line as written."""
    if not m.br.captured:
        return m.br.date_text
    zone = next((w for w in m.br.date_text.replace(",", " ").split() if ZONE_WORD.fullmatch(w)), "")
    offset = re.search(r"(?<=:\d\d)[+-]\d\d(?::?\d\d)?\b", m.br.date_text)
    zone = zone or (offset.group(0) if offset else "")
    return f"{m.br.captured:%Y-%m-%d %H:%M:%S}" + (f" {zone}" if zone else "")


def footer(canvas: Canvas, doc: BaseDocTemplate) -> None:
    """Draw the footer rule with the generator and capture date, and page X of Y."""
    m = model()
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(HAIR)
    canvas.line(LM, 40, PW - RM, 40)
    canvas.setFont("Plex", 7)
    canvas.setFillColor(MUTE)
    canvas.drawString(LM, 29, f"build_report.py {__version__} · {capture_label(m)}")
    canvas.drawRightString(PW - RM, 29, f"Page {doc.page} of {STATE.total or '?'}")


def on_cover(canvas: Canvas, doc: BaseDocTemplate) -> None:
    """Decorate the cover: black band with the report name and capture date, plus the footer."""
    canvas.saveState()
    canvas.setFillColor(INK)
    canvas.rect(0, PH - 30, PW, 30, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Plex-SB", 7.8)
    canvas.drawString(LM, PH - 19, "POST-BOOT LOG ANALYSIS")
    canvas.drawRightString(PW - RM, PH - 19, capture_label(model()).upper())
    footer(canvas, doc)
    canvas.restoreState()


def on_body(canvas: Canvas, doc: BaseDocTemplate) -> None:
    """Decorate a body page: running header with the current section, plus the footer."""
    canvas.saveState()
    canvas.setFont("Plex", 7)
    canvas.setFillColor(MUTE)
    canvas.drawString(LM, PH - 34, f"Post-Boot Log Analysis · {model().facts.get('machine', UNKNOWN_MACHINE)}")
    canvas.setFont("Plex-SB", 7)
    canvas.setFillColor(INK)
    canvas.drawRightString(PW - RM, PH - 34, STATE.page_section.get(doc.page, "Contents"))
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(HAIR)
    canvas.line(LM, PH - 40, PW - RM, PH - 40)
    footer(canvas, doc)
    canvas.restoreState()


# ── BUILD ─────────────────────────────────────────────────────────────
# Fonts, figure rendering, layout passes, the PDF date, the atomic write, and the command line.
def register_fonts(font_dir: Path) -> None:
    """Register the IBM Plex faces; make them the defaults for the page, plain table cells, and drawings."""
    for name, stem in FONT_FILES.items():
        pdfmetrics.registerFont(TTFont(name, str(font_dir / f"{stem}.ttf")))
    registerFontFamily("Plex", normal="Plex", bold="Plex-SB", italic="Plex-It", boldItalic="Plex-SBIt")
    registerFontFamily("PlexC", normal="PlexC", bold="PlexC-SB", italic="PlexC", boldItalic="PlexC-SB")
    registerFontFamily("PlexM", normal="PlexM", bold="PlexM-SB", italic="PlexM", boldItalic="PlexM-SB")
    vars(rl_config).update(canvas_basefontname="Plex", invariant=1)
    rl_tables.CellStyle.fontname = "PlexC"
    rl_shapes.STATE_DEFAULTS["fontName"] = "Plex"


def page_sections(total: int) -> dict[int, str]:
    """Return the running-header text per page: the section in force at the top of each page."""
    sections, current = {}, "Contents"
    for page in range(1, total + 1):
        heads = STATE.h1pos.get(page, [])
        if heads and heads[0][1]:
            current = heads[0][0]
        sections[page] = current
        if heads:
            current = heads[-1][0]
    return sections


def render_figures(m: Model) -> int:
    """Render every figure the model supports; return how many were drawn."""
    STATE_FIGS.clear()
    STATE_FIGS.update(boot_figures(m))
    for key, draw in (
        ("after", fig_after),
        ("families", fig_families),
        ("journal", fig_journal),
        ("processes", fig_processes),
        ("by-finding", fig_by_finding),
        ("verify", fig_verify),
    ):
        STATE_FIGS[key] = draw(m)
    return sum(1 for v in STATE_FIGS.values() if v)


def layout(path: Path, m: Model, log: Callable[[str], None]) -> None:
    """Lay the story out until anchors, page count, and headers repeat; raise on drift or unresolved references."""
    for n in range(1, MAX_PASSES + 1):
        STATE.anchors.clear()
        STATE.h1pos.clear()
        STATE.unresolved.clear()
        doc = Doc(str(path), m)
        doc.build(story(m))
        total = doc.page
        sections = page_sections(total)
        stable = (
            STATE.anchors == STATE.ref
            and total == STATE.total
            and sections == STATE.page_section
            and STATE.figures == STATE.fig_ref
        )
        STATE.ref, STATE.total, STATE.page_section, STATE.fig_ref = (
            dict(STATE.anchors),
            total,
            sections,
            list(STATE.figures),
        )
        log(f"pass {n}: pages={total} anchors={len(STATE.anchors)} stable={stable}")
        if stable and n > 1:
            break
    else:
        msg = f"layout did not settle after {MAX_PASSES} passes"
        raise RuntimeError(msg)
    if STATE.unresolved:
        msg = "unresolved page references: " + ", ".join(sorted(STATE.unresolved))
        raise RuntimeError(msg)


def stated_zone(date_text: str) -> dt.tzinfo | None:
    """Return the zone the bug report's date line states as an offset or as UTC/GMT; None for an abbreviation."""
    offset = re.search(r"(?:(?<=:\d\d)|(?<=\s)|^)([+-])(\d\d)(?::?(\d\d))?(?=\s|$)", date_text)
    if offset:
        sign = -1 if offset.group(1) == "-" else 1
        return dt.timezone(sign * dt.timedelta(hours=int(offset.group(2)), minutes=int(offset.group(3) or 0)))
    return dt.UTC if re.search(r"\b(?:UTC|GMT)\b(?![+-])", date_text) else None


def source_epoch(m: Model) -> int:
    """Return the embedded PDF date: the capture time, else the ry-verify start, else 0.

    The capture time takes the zone its date line states as an offset or as UTC. An abbreviation such as PDT names
    no offset, so the ry-verify log's offset stands in when both fall on the same day, and UTC otherwise.
    """
    if m.br.captured:
        zone = stated_zone(m.br.date_text)
        if zone is None:
            same_day = m.vj.started and m.vj.started.date() == m.br.captured.date()
            zone = m.vj.started.tzinfo if same_day and m.vj.started else dt.UTC
        return int(m.br.captured.replace(tzinfo=zone).timestamp())
    return int(m.vj.started.timestamp()) if m.vj.started else 0


def say(message: str) -> bool:
    """Print a line on stderr; return False when stderr cannot be written, after pointing it at /dev/null."""
    if sys.stderr is None:  # started with stderr closed, where print would fall back to stdout
        return False
    try:
        print(f"build_report.py: {message}", file=sys.stderr)
    except OSError:
        silence(sys.stderr)
        return False
    return True


def build(args: argparse.Namespace, font_dir: Path, *, verbose: bool = False) -> Path:
    """Parse and analyze the inputs, render the figures, lay out the report, and write the PDF atomically."""

    def log(message: str) -> None:
        """Report a build step on stderr when verbose; stop logging if stderr's reader goes away."""
        nonlocal verbose
        if verbose:
            verbose = say(message)

    STATE.reset()
    vj = parse_verify(args.verify)
    br = parse_bugreport(args.bugreport, vj.started.replace(tzinfo=None) if vj.started else None)
    log(
        f"bug report: {plural(len(br.lines), 'line')}, {len(br.dmesg):,} dmesg, {len(br.journal):,} journal, "
        f"{plural(len(br.journal_unparsed), 'journal line')} unread; ry-verify: {plural(len(vj.records), 'record')}"
    )
    m = analyze(br, vj)
    STATE.model = m
    others = plural(len(m.others), "other message class", "other message classes")
    log(f"analysis: {plural(len(m.findings), 'finding')}, {others}, {plural(len(m.actions), 'action')}")
    STATE.font_dir = font_dir
    register_fonts(font_dir)
    out = (args.out or Path(f"post-boot-log-analysis-{capture_day(m)}.pdf")).resolve()
    if not out.parent.is_dir() or not os.access(out.parent, os.W_OK):
        msg = f"cannot write to {out.parent}"
        raise OSError(msg)
    tmp = out.with_name(f".build_report-{os.getpid()}.tmp")  # short, so any output name the system allows fits
    epoch = os.environ.get("SOURCE_DATE_EPOCH")  # ReportLab dates the PDF from it; set for this build only
    if epoch is None:
        os.environ["SOURCE_DATE_EPOCH"] = str(source_epoch(m))
    try:
        with tempfile.TemporaryDirectory(prefix="postboot-report-") as tmp_dir:
            STATE.chart_dir = Path(tmp_dir)
            with warnings.catch_warnings(record=True) as caught:  # matplotlib warns about tick choices, say
                warnings.simplefilter("always")
                drawn = render_figures(m)
            for warning in caught:
                log(f"figure warning: {warning.message}")
            log(f"{plural(drawn, 'figure')} rendered")
            layout(tmp, m, log)
        with interrupts_deferred():  # the PDF replaces its target and is recorded as written, or neither happens
            if INTERRUPTED:  # an interrupt raised inside a callback that Python swallowed
                raise KeyboardInterrupt
            tmp.replace(out)
            WRITTEN.append(out)
    finally:
        tmp.unlink(missing_ok=True)
        if epoch is None:
            os.environ.pop("SOURCE_DATE_EPOCH", None)
    log(f"wrote {out} ({STATE.total} pages, {out.stat().st_size:,} bytes)")
    return out


def silence(stream: TextIO) -> None:
    """Point a closed pipe's file descriptor at /dev/null so later writes and the final flush succeed."""
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, stream.fileno())
    os.close(devnull)


INTERRUPTED: list[int] = []  # the signals received; build() checks before the PDF replaces its target
DEFERRING: list[bool] = []  # set while the PDF replaces its target, so an interrupt waits for that step to end
WRITTEN: list[Path] = []  # the PDF once it has replaced its target, which a later interrupt leaves in place
SIGNALS = tuple(getattr(signal, n) for n in ("SIGINT", "SIGTERM", "SIGHUP") if hasattr(signal, n))


@contextlib.contextmanager
def interrupts_deferred() -> Generator[None]:
    """Run the block with interrupts deferred: one that arrives meanwhile raises KeyboardInterrupt when it ends.

    Blocking the signals is not enough: the kernel may hand a signal sent to the process to another thread, such as
    a math library's worker, and Python then runs the handler in the main thread at once.
    """
    DEFERRING.append(True)
    try:
        yield
    finally:
        DEFERRING.pop()
    if INTERRUPTED:
        raise KeyboardInterrupt


def interrupt(signum: int, _frame: object) -> None:
    """Record the signal and raise KeyboardInterrupt, so the build cleans up and exits 130.

    While the PDF replaces its target, the raise waits for that step to end. The record outlasts an interrupt Python
    swallows (raised in a weakref callback) or turns into another error (raised while a C extension loads).
    """
    INTERRUPTED.append(signum)
    if not DEFERRING:
        raise KeyboardInterrupt


def unraisable(info: Any) -> None:  # noqa: ANN401  # sys.UnraisableHookArgs is not importable
    """Report an error Python could not raise, unless it came after an interrupt, which interrupt() recorded."""
    if not INTERRUPTED:
        sys.__unraisablehook__(info)


def main(argv: list[str] | None = None) -> int:
    """Validate the arguments and the environment, then run; Ctrl-C, SIGTERM, or SIGHUP from here on exits 130."""
    parser = build_parser()
    args = _ARGS if argv is None and _ARGS is not None else parser.parse_args(argv)
    if args.out is not None and (kind := out_kind(args.out)):
        parser.error(f"--out names {kind}: {args.out}")
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch is not None and not (re.fullmatch(r"\s*\d{1,20}\s*", epoch, re.ASCII) and int(epoch) <= EPOCH_MAX):
        parser.error(f"SOURCE_DATE_EPOCH must be a whole number of seconds up to {EPOCH_MAX}, not {clip(epoch, 40)!r}")
    for signum in SIGNALS:
        signal.signal(signum, interrupt)
    sys.unraisablehook = unraisable
    try:
        code = run(args)
    except KeyboardInterrupt:
        code = EXIT_INTERRUPT
    except Exception:
        if not INTERRUPTED:
            raise
        code = EXIT_INTERRUPT  # an interrupt that surfaced as another error
    if INTERRUPTED or code == EXIT_INTERRUPT:
        say(f"interrupted after writing {WRITTEN[0]}" if WRITTEN else "interrupted; nothing written")
        return EXIT_INTERRUPT
    return code


def out_kind(path: Path) -> str:
    """Return what --out names when the PDF cannot replace it: a directory, or no regular file (a pipe, a device).

    A path the system cannot reach returns "", and the build reports why; Python before 3.14 raises for one here.
    """
    try:
        if path.is_dir():
            return "a directory"
        return "no regular file" if path.exists() and not path.is_file() else ""
    except OSError:
        return ""


def check(args: argparse.Namespace) -> int:
    """Parse both inputs and run the parser cross-checks; report on stderr and return EXIT_OK."""
    vj = parse_verify(args.verify)
    br = parse_bugreport(args.bugreport, vj.started.replace(tzinfo=None) if vj.started else None)
    checks = [row for row in cross_checks(analyze(br, vj)) if row[1] == "match" or row[1].startswith("differs")]
    differing = [row for row in checks if row[1] != "match"]
    unread = f", {plural(len(br.journal_unparsed), 'journal line')} unread" if br.journal_unparsed else ""
    counts = (
        f"{len(br.sections)} bug-report sections, {plural(len(br.dmesg), 'dmesg line')}, "
        f"{plural(len(br.journal), 'journal entry', 'journal entries')}{unread}"
    )
    say(
        f"inputs ok ({counts}; ry-verify {vj.meta('version')} with {plural(len(vj.items), 'result')}; "
        f"cross-checks: {len(checks) - len(differing)} of {len(checks)} match)"
    )
    for name, result in differing:
        say(f"cross-check: {name}: {result}")
    return EXIT_OK


def run(args: argparse.Namespace) -> int:
    """Run the preflight, then the check or the build; the written path goes to stdout."""
    try:
        font_dir = preflight(args.fonts, (args.bugreport, args.verify))
    except PreflightError as exc:
        say(str(exc))
        return EXIT_PREFLIGHT
    try:
        if args.check:
            return check(args)
        out = build(args, font_dir, verbose=args.verbose)
    except (OSError, ValueError, RuntimeError, LayoutError, InputError) as exc:
        if INTERRUPTED:  # an error the interrupt caused; main() reports the interrupt
            return EXIT_INTERRUPT
        say(f"{'check' if args.check else 'build'} failed: {exc}")
        return EXIT_FAIL
    try:
        print(out, flush=True)
    except BrokenPipeError:  # the PDF is written; a closed stdout only loses the path line
        silence(sys.stdout)
    return EXIT_OK


if __name__ == "__main__":
    status = main()
    if status == EXIT_INTERRUPT:
        # The build is over and its files are gone; skip the interpreter's teardown, where a C extension that the
        # interrupt caught half loaded can crash and turn exit 130 into a segmentation fault.
        os._exit(status)
    sys.exit(status)
