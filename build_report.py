#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["reportlab>=5.0", "matplotlib>=3.11", "svglib>=2.2", "pillow>=12"]
# ///
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Ryan Musante
"""Analyze a cachyos-bugreport.log and a ry-verify JSONL log and build a print-edition PDF report.

Every count, line reference, table row, figure, and finding comes from the two inputs on each run;
the script carries only analysis rules (message patterns and what they mean), never results.
Sections: SETUP, INPUT, RULES, ANALYSIS, FIGURES, LAYOUT, BUILD.
Exit codes: 0 built or check passed, 1 build failed, 2 usage, 3 preflight failed, 130 interrupted.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import itertools
import json
import os
import re
import signal
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO
from xml.sax.saxutils import escape

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from types import ModuleType

    from matplotlib.axes import Axes
    from matplotlib.figure import Figure
    from reportlab.pdfgen.canvas import Canvas

# ── SETUP ─────────────────────────────────────────────────────────────
# Version, exit codes, fonts, command line, preflight, and the shared build state.
__version__ = "7.0.0"
EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_PREFLIGHT, EXIT_INTERRUPT = 0, 1, 2, 3, 130  # 130 = 128 + SIGINT, as shells report


def user_font_dir() -> Path:
    """Return ~/.local/share/fonts; without a known home directory, a path that no file can match."""
    try:
        return Path("~/.local/share/fonts").expanduser()
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
# reportlab is imported at start-up; the preflight covers the modules loaded later
MODULES = {"matplotlib": "python-matplotlib", "svglib": "python-svglib", "PIL": "python-pillow"}
MAX_PASSES = 6  # layout passes allowed before the page references must have settled
HEADING_TOP_BAND = 70.0  # pt below the frame top within which a section heading opens its page


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser; its epilog lists the exit codes from the EXIT_* constants."""
    parser = argparse.ArgumentParser(
        prog="build_report.py",
        description="Analyze a cachyos-bugreport.log and a ry-verify JSONL log into a print-edition PDF.",
        epilog=(
            f"Exit codes: {EXIT_OK} built or check passed, {EXIT_FAIL} build failed, "
            f"{EXIT_USAGE} usage, {EXIT_PREFLIGHT} preflight failed, {EXIT_INTERRUPT} interrupted."
        ),
    )
    parser.add_argument("--bugreport", type=Path, required=True, help="cachyos-bugreport.log from cachyos-bugreport.sh")
    parser.add_argument(
        "--verify", type=Path, required=True, help="ry-verify JSONL log (verify-*.jsonl or report-*.jsonl)"
    )
    parser.add_argument("--out", type=Path, help="output PDF (default: ./post-boot-log-analysis-<capture date>.pdf)")
    parser.add_argument(
        "--fonts", type=Path, help="directory with the IBM Plex TTF files (default: system font directories)"
    )
    parser.add_argument("--check", action="store_true", help="run the preflight and parse the inputs; build nothing")
    parser.add_argument(
        "--verbose", action="store_true", help="report parsing, analysis, layout passes, and the result"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


# Ctrl-C during start-up ends the process quietly (nothing is written yet); main() restores KeyboardInterrupt.
signal.signal(signal.SIGINT, signal.SIG_DFL)
# Parse before importing ReportLab, so --help, --version, and usage errors work without the dependencies.
_ARGS = build_parser().parse_args() if __name__ == "__main__" else None

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
    from reportlab.pdfbase.ttfonts import TTFont
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
    print(f"build_report.py: cannot import {exc.name or 'reportlab'} (pacman: python-reportlab)", file=sys.stderr)
    sys.exit(EXIT_PREFLIGHT)


class PreflightError(Exception):
    """A missing module, font, or input; maps to exit code 3."""


class InputError(Exception):
    """An input that is present but not in the expected format; maps to exit code 1."""


def preflight(fonts: Path | None, inputs: Sequence[Path]) -> Path:
    """Return the font directory, or raise PreflightError naming what is missing."""
    missing = [f"{name} (pacman: {pkg})" for name, pkg in MODULES.items() if importlib.util.find_spec(name) is None]
    if missing:
        msg = "missing Python modules: " + ", ".join(missing)
        raise PreflightError(msg)
    need = [f"{stem}.ttf" for stem in FONT_FILES.values()]
    dirs = (fonts,) if fonts else FONT_DIRS
    font_dir = next((d for d in dirs if all((d / f).is_file() for f in need)), None)
    if font_dir is None:
        searched = ", ".join(str(d) for d in dirs)
        msg = f"IBM Plex TTF files not found in {searched} (pacman: ttf-ibm-plex, or pass --fonts)"
        raise PreflightError(msg)
    unreadable = [str(p) for p in inputs if not (p.is_file() and os.access(p, os.R_OK))]
    if unreadable:
        msg = "cannot read input: " + ", ".join(unreadable)
        raise PreflightError(msg)
    return font_dir


@dataclass
class BuildState:
    """Mutable state shared by the layout passes, the flowables, and the page callbacks."""

    ref: dict[str, int] = field(default_factory=dict)  # anchor pages from the previous pass
    anchors: dict[str, int] = field(default_factory=dict)  # anchor pages seen in this pass
    h1pos: dict[int, tuple[str, bool]] = field(default_factory=dict)  # page -> (heading, opens the page)
    page_section: dict[int, str] = field(default_factory=dict)  # page -> running-header text
    unresolved: set[str] = field(default_factory=set)  # page references not known in this pass
    figures: list[tuple[int, str]] = field(default_factory=list)  # (number, title) in reading order, this pass
    fig_ref: list[tuple[int, str]] = field(default_factory=list)  # the same from the previous pass, for the contents
    model: Model | None = None
    tables: int = 0
    total: int = 0
    chart_dir: Path = field(default_factory=Path)
    font_dir: Path = field(default_factory=Path)
    mpl: ModuleType | None = None


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
    """One journal entry at warning level or above."""

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
    inxi: list[tuple[int, str]]
    dmesg: list[DmesgEntry]
    journal: list[JournalEntry]
    journal_unparsed: list[int]  # journal-section lines that are neither entries, continuations, nor markers
    packages: list[tuple[int, str, str, str]]  # (line, repository, name, version)

    def section_lines(self, name: str) -> list[tuple[int, str]]:
        """Return (line number, text) for every line of a section."""
        return section_slice(self.lines, self.sections, name)


@dataclass
class VerifyItem:
    """One ry-verify result record."""

    no: int
    phase: str  # "static" or "runtime"
    section: str
    status: str  # OK, INFO, WARN, FAIL, ERR
    text: str

    @property
    def counted(self) -> str:
        """Return the status column the record counts under: ERR records count as FAIL."""
        return "FAIL" if self.status == "ERR" else self.status


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
    texts: list[tuple[int, str]]  # (record number, every string field joined) for every record, header included

    @property
    def started(self) -> dt.datetime | None:
        """Return the header timestamp."""
        return parse_iso(self.header.get("ts", ""))

    @property
    def finished(self) -> dt.datetime | None:
        """Return the footer timestamp."""
        return parse_iso(self.footer.get("ts", ""))

    @property
    def totals(self) -> dict[str, int]:
        """Return the run's ok, fail, warn, and gen_fail counts from the best source the log holds.

        The footer comes first; without one, the VERIFY_RESULT_COMBINED record, then the phase records summed,
        then the result records counted (which cannot see generator failures).
        """
        keys = ("ok", "fail", "warn", "gen_fail")
        if self.footer:
            return {k: int(self.footer.get("pass" if k == "ok" else k, 0)) for k in keys}
        if self.combined:
            return {k: self.combined.get(k, 0) for k in keys}
        if self.phase_results:
            return {k: sum(r.get(k, 0) for r in self.phase_results.values()) for k in keys}
        return {k: sum(1 for i in self.items if i.counted.lower() == k) for k in keys}

    @property
    def status(self) -> tuple[str, str]:
        """Return the run's verdict and exit text: (PASS, exit 0), (FAIL, exit N), or (unknown, no footer)."""
        if not self.footer:
            return "unknown", "no footer"
        code = self.footer.get("exit_code")
        return ("PASS" if code == 0 else "FAIL"), f"exit {code}"


def parse_iso(text: str) -> dt.datetime | None:
    """Return a datetime from ry-verify's ISO stamp (YYYY-MM-DDTHH:MM:SS.fff±hhmm), or None."""
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


def parse_capture_date(text: str) -> dt.datetime | None:
    """Return the capture time from the report's `date` line (C or en_US locale), or None."""
    cleaned = " ".join(w for w in text.split() if not re.fullmatch(r"[A-Z]{3,5}|[+-]\d{4}|UTC[+-]?\d*", w))
    formats = ("%a %b %d %H:%M:%S %Y", "%a %b %d %I:%M:%S %p %Y", "%a %d %b %Y %H:%M:%S", "%a %d %b %Y %I:%M:%S %p")
    for layout_ in formats:
        try:
            return dt.datetime.strptime(cleaned, layout_)  # the zone name is dropped above
        except ValueError:
            continue
    return None


JOURNAL_MARKERS = ("-- ", "No previous boot log available")  # journalctl notices and the capture script's own


def parse_journal(
    lines: Iterable[tuple[int, str]], boot: str, captured: dt.datetime | None
) -> tuple[list[JournalEntry], list[int]]:
    """Return the journal entries of one boot and the lines that parsed as nothing.

    Continuation lines join the entry above them; journalctl markers are skipped. Journal lines carry no year:
    entries take the capture year, or the year before when their month is later than the capture month (a boot
    that crossed New Year). Times are local wall-clock, as the journal prints them.
    """
    entries: list[JournalEntry] = []
    unparsed: list[int] = []
    now = captured or dt.datetime.now().astimezone().replace(tzinfo=None)  # local wall-clock, like the journal
    for n, line in lines:
        m = JOURNAL_LINE.match(line)
        if m and m.group("mon") in MONTHS:
            hh, mm, ss = (int(x) for x in m.group("time").split(":"))
            month = MONTHS[m.group("mon")]
            year = now.year - 1 if month > now.month else now.year
            when = dt.datetime(year, month, int(m.group("day")), hh, mm, ss)  # journal time, no zone
            entries.append(JournalEntry(n, boot, when, m.group("ident"), m.group("pid") or "", m.group("msg")))
        elif entries and line.startswith((" ", "\t")) and line.strip():
            entries[-1].text += " " + line.strip()
        elif line.strip() and not line.startswith(JOURNAL_MARKERS):
            unparsed.append(n)
    return entries, unparsed


IRC_KEY = re.compile(r"\x03\d{1,2}(?:,\d{1,2})?([^\x03]*)\x03")  # inxi's IRC key: colour code, key text, reset
IRC_CODE = re.compile(r"\x03(?:\d{1,2}(?:,\d{1,2})?)?|[\x02\x0f\x16\x1d\x1f]")  # any other IRC formatting byte


def normalize_inxi(text: str) -> str:
    """Return one inxi output line in its terminal form.

    inxi prints IRC formatting when its stdin is not a terminal (cachyos-bugreport.sh run from a launcher or with
    redirected input): keys become colour runs without their colons. They get the colons back; codes are dropped.
    """
    text = IRC_KEY.sub(lambda m: m.group(1) if m.group(1).endswith(":") else f"{m.group(1)}:", text)
    return IRC_CODE.sub("", text)


def inxi_logical_lines(lines: Iterable[tuple[int, str]]) -> list[tuple[int, str]]:
    """Return inxi output with its wrapped continuation lines (indented 4 or more) joined to the entry above.

    inxi wraps at the terminal width wherever a word ends, so a line may end on a key such as `cache:` whose
    value continues below.
    """
    out: list[tuple[int, str]] = []
    for n, raw in lines:
        text = normalize_inxi(raw).rstrip()
        if not text.strip():
            continue
        if out and text.startswith("    "):
            out[-1] = (out[-1][0], out[-1][1] + " " + text.strip())
        else:
            out.append((n, text))
    return out


def parse_bugreport(path: Path) -> BugReport:
    """Parse cachyos-bugreport.log; raise InputError when it does not look like one."""
    raw = path.read_bytes()
    lines = raw.decode("utf-8-sig", errors="replace").splitlines()
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
    journal, unparsed = parse_journal(section_slice(lines, sections, "journal-current"), "current", captured)
    previous, unparsed_prev = parse_journal(section_slice(lines, sections, "journal-previous"), "previous", captured)
    journal += previous
    unparsed += unparsed_prev
    packages = []
    for n, line in section_slice(lines, sections, "packages"):
        m = re.match(r"^(?P<repo>[\w.-]+)/(?P<name>\S+) (?P<ver>\S+)", line)
        if m:
            packages.append((n, m.group("repo"), m.group("name"), m.group("ver")))
    inxi = inxi_logical_lines(section_slice(lines, sections, "inxi"))
    return BugReport(
        path,
        raw,
        lines,
        sections,
        head.get("Date", ""),
        captured,
        head.get("uname", ""),
        head.get("cmdline", ""),
        inxi,
        dmesg,
        journal,
        unparsed,
        packages,
    )


RESULT_LINE = re.compile(
    r"^(OK|INFO|WARN|FAIL|ERR):\s+(.*)$"
)  # ERR is ry-verify's fatal-check level; it counts as FAIL
COUNTS = re.compile(r"\b(ok|fail|warn|gen_fail)=(\d+)")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Return the JSON objects of a JSONL file, skipping blank lines; raise InputError on anything else."""
    records: list[dict[str, Any]] = []
    for n, line in enumerate(path.read_bytes().decode("utf-8-sig", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            msg = f"{path.name}: line {n} is not JSON ({exc.msg})"
            raise InputError(msg) from exc
        if not isinstance(rec, dict):
            msg = f"{path.name}: line {n} is not a JSON object"
            raise InputError(msg)
        records.append(rec)
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
            self.last_phase, self.section = m.group(1).lower(), "GENERAL"
            return True
        if m := re.match(r"^ECHO: ([A-Z][A-Z0-9 /&-]+)$", data):
            self.section = m.group(1)
            return True
        return False


def parse_verify(path: Path) -> VerifyLog:
    """Parse a ry-verify JSONL log; raise InputError when it is not one."""
    raw = path.read_bytes()
    records = read_jsonl(path)
    header = next((r for r in records if r.get("event") == "header"), None)
    if header is None or "version" not in header:
        msg = f"{path.name}: not a ry-verify log (no header record)"
        raise InputError(msg)
    foot = next((r for r in reversed(records) if r.get("event") == "footer"), {})
    items, phase_results, combined = [], {}, {}
    texts = [(n, " ".join(record_strings(rec))) for n, rec in enumerate(records, 1)]
    at = VerifyCursor()
    for n, rec in enumerate(records, 1):
        data = str(rec.get("data", "")) if rec.get("event") == "log" else ""
        if not data:
            continue
        if at.advance(data):
            continue
        if data.startswith("VERIFY_RESULT_COMBINED:"):
            combined = {k: int(v) for k, v in COUNTS.findall(data)}
        elif data.startswith("VERIFY_RESULT:"):
            phase_results[at.last_phase] = {k: int(v) for k, v in COUNTS.findall(data)}
        elif (m := RESULT_LINE.match(data)) and at.phase and at.section != "VERIFICATION SUMMARY":
            items.append(VerifyItem(n, at.phase, at.section, m.group(1), m.group(2).strip()))
    return VerifyLog(path, raw, records, header, foot, items, phase_results, combined, texts)


def record_strings(value: object) -> list[str]:
    """Return every string inside a JSON value: the identifier scan reads all fields, not only `data`."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in record_strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in record_strings(v)]
    return []


# ── RULES ─────────────────────────────────────────────────────────────
# Message patterns and what they mean. Rules carry knowledge, never results: a rule only reaches the
# report when its pattern matches a line of the inputs, and every count it shows is taken from them.
SEVERITY_RANK = {"HIGH": 0, "MED": 1, "LOW": 2, "INFO": 3, "WATCH": 4, "SETTING": 5, "NOTE": 6}
FINDING_LEVELS = ("HIGH", "MED", "LOW", "INFO")
SHUTDOWN_WINDOW = 15  # seconds before the previous boot's last journal entry that count as shutdown
KEYWORDS = (
    "fail", "failed", "failure", "error", "warn", "warning", "unable", "cannot", "can't", "could not", "couldn't",
    "not supported", "unsupported", "not found", "no such", "denied", "invalid", "timeout", "timed out", "abort",
    "crash", "crashed", "panic", "oops", "bug", "taint", "call trace", "segfault", "killed", "refused", "reset",
    "hang", "corrupt", "mismatch", "deprecated", "unknown", "lacking", "kaput",
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


R = Rule
RULES = (
    R("kernel-splat", "Kernel oops, BUG, or warning splat", "Kernel", "HIGH", ("dmesg", "journal"),
      (r"\bOops\b", r"\bBUG: ", r"kernel BUG at ", r"general protection fault", r"WARNING: CPU: \d+ PID: \d+",
       r"Call Trace:"),
      "The kernel reached an unexpected state and printed a splat; the lines after the first match name the "
      "module and function involved.",
      "Read the whole splat with `journalctl -k -b` and report it upstream with the module it names."),
    R("kernel-taint", "Kernel taint flag set", "Kernel", "MED", ("dmesg", "journal"), (r"\bTainted: (?:[A-Z]|\[)",),
      "A taint flag marks the running kernel as modified or degraded (an out-of-tree or unsigned module, or an "
      "earlier oops); upstream developers ask for reproductions on an untainted kernel.",
      "Read `/proc/sys/kernel/tainted` and identify the module that set the flag."),
    R("gpu-hang", "GPU hang or reset", "GPU / amdgpu", "HIGH", ("dmesg", "journal"),
      (r"amdgpu.*(?:GPU reset|ring \S+ timeout|job timed out|GPU recovery)",),
      "amdgpu detected a stuck engine and reset the GPU; applications using it may have lost their contexts.",
      "Note what was running at that time and check the amdgpu lines around the reset."),
    R("storage-errors", "Storage or file-system errors", "Storage", "HIGH", ("dmesg", "journal"),
      (r"I/O error", r"EXT4-fs error", r"BTRFS (?:error|critical)", r"XFS .*(?:corruption|metadata I/O error)",
       r"nvme\d+: (?:controller is down|resetting controller)"),
      "The kernel reported failed I/O or file-system damage; data on the affected device may be at risk.",
      "Check the device with `smartctl -a` and run a file-system check from a live system."),
    R("oom-kill", "Out-of-memory kill", "Memory", "MED", ("dmesg", "journal"),
      (r"Out of memory: Killed process", r"oom-kill:"),
      "The kernel ran out of memory and killed a process to recover.",
      "Find the killed process in the evidence and the memory consumer that caused it."),
    R("core-dump", "A process crashed and dumped core", "Processes", "MED", ("journal",),
      (r"Process \d+ \(.+\) of user \d+ (?:dumped core|terminated abnormally)",),
      "systemd-coredump recorded a crashed program; the evidence names it.",
      "Inspect it with `coredumpctl list` and `coredumpctl info <PID>`."),
    R("shutdown-noise", "Shutdown-only noise", "Session / shutdown", "INFO", ("journal",),
      (r"Failed to enqueue SYSTEMD_(?:USER_)?WANTS job", r"Transaction for .* is destructive", r"PipeWire remote error",
       r"context kaput", r"dispatcher: .*failed", r"crashed \(signal (?:1|15)\)", r"Authentication error: .*crashed",
       r"Failed with result '", r"No object for name", r"Could not activate remote peer"),
      "These lines come from the last seconds of the previous boot, while services stopped for the reboot: "
      "helpers ended by SIGHUP or SIGTERM are reported as crashes, udev events cannot add jobs to a shutdown "
      "transaction, D-Bus activations are refused, and PipeWire clients lose their server.",
      window="shutdown"),
    R("redacted-unit", "A unit whose name the redactor replaced failed", "Services", "INFO", ("journal",),
      (r"<email-address-redacted>: Failed with result",),
      "systemd reported a failed unit, but cachyos-bugreport.sh replaced its name: unit names containing @ look "
      "like email addresses to its filter. Template and D-Bus-activated units are affected.",
      "Name it with `journalctl -b -o cat -p warning | rg 'Failed with result'` (add `--user` for user units)."),
    R("kwallet-off", "KWallet services fail because KWallet is disabled", "Session / KWallet", "INFO", ("journal",),
      (r"Lacking a socket, pipe", r"kwallet.*Failed with result"),
      "With the KDE wallet disabled, ksecretd and the KWallet secret portal exit on start and systemd records the "
      "D-Bus-activated unit as failed; nothing else depends on them.",
      "None unless secret storage is wanted."),
    R("kde-startup", "KDE, portal, and D-Bus startup noise", "Desktop / KDE, portal, D-Bus", "INFO", ("journal",),
      (r"Activation request for '[^']+' failed", r"Service file '[^']+' is not named after the D-Bus name",
       r"Failed to register with host portal", r"[Cc]harge thresholds? .*not supported|chargethreshold",
       r"Failed enumerating MM objects", r"no kernel backlight interface", r"\.qml:\d+", r"[Dd]eprecated",
       r"gtk\.portal|Lockdown", r"UPower", r"@DEFAULT_SOURCE@", r"[Ss]creencast", r"[Ss]creen configuration"),
      "Plasma, its portals, and D-Bus print these on working desktops: portal host registration for programs "
      "without desktop files, legacy D-Bus service file names, absent ModemManager or UPower owners, power "
      "management probing hardware the machine lacks, and QML deprecation warnings.",
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
      "bolt's table of USB4/Thunderbolt host interfaces lacks these IDs, so it treats the host UUID as unstable, the "
      "safe default; nothing changes without devices that need bolt authorization."),
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
      "zswap set up its compressed pool. When the command line disables zswap, a later write to its `enabled` "
      "parameter does this (CachyOS's zram udev rule writes it); zswap stays as configured."),
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
       r"No LPM exit latency info found", r"Could not reserve \[mem", r"Fan Speeds \(rpm\): N/A"),
      "Firmware keeps PCIe error reporting (AER) instead of granting it to the OS, so link errors are not reported; "
      "no fan tachometer is exposed; USB link power management is off for controllers without exit-latency data.",
      "None; a clean ring is not proof of a clean PCIe link."),
    R("inxi-labels", "inxi mislabels the CPU or GPU generation", "Tooling / inxi", "INFO", ("inxi",),
      (r"arch: Zen 6", r"code: Phoenix"),
      "inxi's model tables label Strix Halo as Zen 6 and its GPU as Phoenix; the hardware is Zen 5 with an RDNA 3.5 "
      "GPU.", "None; note it when sharing inxi output.", requires=r"(?i)ryzen ai max"),
    R("egistec", "Fingerprint reader without a driver", "Input / fingerprint", "INFO", ("dmesg",),
      (r"idVendor=1c7a, idProduct=0577",),
      "libfprint lists the EgisTec EH577 (1c7a:0577) as unsupported, so the sensor enumerates but no driver binds.",
      "None unless fingerprint login is wanted."),
    R("hid-joystick", "Keyboard receiver exposes a HID joystick interface", "Input / HID", "LOW", ("dmesg",),
      (r"Joystick \[Keychron",),
      "The receiver presents a joystick interface that SDL and Proton games can take for a game controller.",
      "Add the receiver's VID/PID pairs to SDL_GAMECONTROLLER_IGNORE_DEVICES for the session.",
      mitigated_by=r"SDL_GAMECONTROLLER_IGNORE_DEVICES"),
    R("usb-volume", "USB audio device reports a tiny hardware volume range", "Audio / USB", "LOW", ("dmesg",),
      (r"Unlikely small volume range",),
      "The device's hardware volume control spans so little that the slider has almost no effect.",
      "Enable WirePlumber's soft mixer (api.alsa.soft-mixer) for the card.", mitigated_by=r"(?i)soft.?mixer"),
    R("jack-server", "A JACK server runs beside PipeWire", "Audio / JACK", "LOW", ("inxi",),
      (r"Server-\d+: JACK", r"\bjackd\b"),
      "jack2 provides libjack, so JACK clients bypass PipeWire.",
      "Replace jack2 with pipewire-jack.", mitigated_by=r"pipewire-jack"),
    R("wifi-tx-cap", "Wi-Fi TX power capped by the access point", "Network / Wi-Fi", "WATCH", ("dmesg",),
      (r"Limiting TX power to \d+",),
      "The access point advertises a transmit-power limit and the driver applies it.",
      "Watch for a lower cap after router or firmware changes."),
    R("link-down", "Wired network ports down", "Network / Ethernet", "WATCH", ("dmesg",),
      (r"\b(?:eth|en)\w*: (?:NIC )?Link is Down",),
      "These ports have no cable or link partner.", "Watch for a cabled port that stays down."),
    R("secure-boot", "Secure Boot state", "Boot / Secure Boot", "SETTING", ("dmesg",),
      (r"Secure boot (?:disabled|enabled)",), "The firmware's Secure Boot state as the kernel reports it."),
    R("ipv6-off", "IPv6 administratively disabled", "Network / IPv6", "SETTING", ("dmesg",),
      (r"IPv6: Loaded, but administratively disabled",), "ipv6.disable=1 on the kernel command line."),
    R("cstate-cap", "CPU idle states limited", "CPU / idle", "SETTING", ("dmesg",),
      (r"processor limited to max C-state",), "processor.max_cstate on the kernel command line."),
    R("cmdline", "Kernel command line", "Boot", "NOTE", ("dmesg",), (r"^(?:Kernel )?[Cc]ommand line:",),
      "The command line the kernel booted with."),
    R("memmap", "Firmware memory map, ACPI table reservations", "Boot / firmware", "NOTE", ("dmesg",),
      (r"BIOS-e820:", r"^e820: ", r"^ACPI: .*(?:table|Reserving)", r"reserve setup_data"),
      "Boilerplate printed on every boot."),
    R("pnp-reserve", "PNP0C02 resource reservations", "Boot / ACPI", "NOTE", ("dmesg",),
      (r"system 00:\w+: \[(?:mem|io) .*\] (?:has been|could not be) reserved", r"PNP0C02"),
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
      ((r"\bamdgpu\b.*(?:Direct firmware load for .* failed|not supported|is not available|runtime pm is manually "
       r"disabled)"),),
      "Optional firmware or features absent on this GPU; boilerplate."),
    R("unmet-conditions", "systemd unmet conditions", "Boot / systemd", "NOTE", ("dmesg",),
      ((r"was skipped because (?:no trigger condition checks were met|of an unmet condition check|all trigger "
       r"condition checks failed)"),),
      "Units skipped by design on this system (no TPM measurement, no hibernation, and similar)."),
    R("audit", "Audit subsystem", "Kernel / audit", "NOTE", ("dmesg",), (r"\baudit[:(]",),
      "Boilerplate printed on every boot."),
    R("reset-reason", "Previous reset reason", "Boot", "NOTE", ("dmesg",),
      (r"(?i)reset reason|wrote 0x\w+ to reset control register",),
      "How the previous boot ended, as the platform recorded it."),
    R("unit-failed", "systemd units failed", "Services", "LOW", ("journal",),
      (r"Failed with result '", r"Failed to start "),
      "systemd reports units that ended in failure; the evidence names each unit.",
      "Inspect each unit with `systemctl status` and `journalctl -b -u <unit>`."),
)  # fmt: skip
del R
COMPILED = {r.key: [re.compile(p) for p in r.patterns] for r in RULES}
IDENT_RES = {r.key: re.compile(r.idents) for r in RULES if r.idents}

# Identifier classes: what makes a log traceable to one machine or person.
UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
IDENTIFIERS = (
    ("Root UUID, root=UUID= form", re.compile(rf"root=UUID={UUID}"), "root=UUID=[root UUID]"),
    ("DMI system UUID", re.compile(rf"\buuid: {UUID}"), "uuid: [DMI UUID]"),
    ("USB4 domain ID", re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-domain"), "<USB4 domain id>-domain"),
    ("Other UUID", re.compile(rf"\b{UUID}\b"), "[UUID]"),
    ("USB serial numbers", re.compile(r"SerialNumber:\s*(?![<\[])\S+"), "SerialNumber: [serial]"),
    ("Bluetooth address, underscore form", re.compile(r"\b(?:[0-9A-Fa-f]{2}_){5}[0-9A-Fa-f]{2}\b"), "[BT MAC]"),
    ("MAC address, colon form", re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b"), "[MAC]"),
    ("Home directory", re.compile(r"/home/(?!<)[A-Za-z0-9._-]+"), "/home/<user>"),
    (
        "IPv4 address",
        re.compile(r"(?<!v: )(?<![\w.:/-])(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\w.])"),
        "[IPv4]",
    ),
    ("Email address", re.compile(r"[\w.%+-]+@[\w-]+\.(?!service\b|socket\b|timer\b|target\b|mount\b|slice\b|scope\b)"
                                 r"[A-Za-z]{2,}\b"), "[email]"),
)  # fmt: skip


QUOTE_LIMIT = 400  # characters of a log line quoted in the report; the rest is cut with an ellipsis
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")  # CSI sequences a program may have written into a log
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # C0 controls other than tab and newline


def mask(text: str) -> str:
    """Return text with every identifier replaced by a placeholder, for quoting in the report."""
    for _name, pattern, placeholder in IDENTIFIERS:
        text = pattern.sub(placeholder, text)
    return text


def sanitize(text: str) -> str:
    """Return text masked, with ANSI escapes removed and other control characters shown as U+FFFD."""
    return CONTROL.sub("\ufffd", ANSI_ESCAPE.sub("", mask(text)))


def quote(text: str) -> str:
    """Return a log line sanitized and cut to QUOTE_LIMIT characters."""
    text = sanitize(text)
    return text if len(text) <= QUOTE_LIMIT else text[: QUOTE_LIMIT - 1] + "…"


def clip(text: str, limit: int) -> str:
    """Return text cut at a word boundary to at most limit characters, with an ellipsis when cut."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"


# ── ANALYSIS ──────────────────────────────────────────────────────────
# Turns the parsed inputs into findings, health checks, coverage, identifiers, timeline, and actions.
@dataclass
class Match:
    """One input line attributed to a finding."""

    stream: str  # dmesg, journal, inxi, verify
    no: int  # bug-report line or JSONL record
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

    def count(self, stream: str, boot: str = "") -> int:
        """Return the evidence lines from one stream (and, for the journal, one boot)."""
        return sum(1 for m in self.matches if m.stream == stream and (not boot or m.boot == boot))

    def first_line(self) -> str:
        """Return the first evidence line reference, e.g. 'BR 12' or 'VJ 7'."""
        br = [m.no for m in self.matches if m.stream != "verify"]
        vj = [m.no for m in self.matches if m.stream == "verify"]
        return f"BR {min(br)}" if br else (f"VJ {min(vj)}" if vj else "")

    def lines(self) -> str:
        """Return the evidence line references as ranges, e.g. 'BR 12, 40 to 41 · VJ 7'."""
        br = sorted({m.no for m in self.matches if m.stream != "verify"})
        vj = sorted({m.no for m in self.matches if m.stream == "verify"})
        parts = [f"BR {ranges(br)}"] if br else []
        return " · ".join([*parts, f"VJ {ranges(vj)}"] if vj else parts)


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
    unclassified: list[Match]
    identifiers: list[tuple[str, list[int], list[int]]]  # (class, bug-report lines, JSONL records)
    milestones: dict[str, float]
    quiet_gap: tuple[float, float] | None
    kernel_start: tuple[dt.datetime, float] | None  # (estimate, half-width in seconds)
    verify_sections: list[tuple[str, str, dict[str, int]]]
    facts: dict[str, str]
    health: list[tuple[str, str, str]]
    keyword_lines: dict[str, int]
    actions: list[Action]

    @property
    def identifier_lines(self) -> tuple[int, int]:
        """Return how many distinct bug-report lines and ry-verify records carry any identifier."""
        br = {n for _, b, _ in self.identifiers for n in b}
        vj = {n for _, _, v in self.identifiers for n in v}
        return len(br), len(vj)


def ranges(numbers: Sequence[int]) -> str:
    """Return sorted numbers as compact ranges: 1, 3 to 5, 9 reads 1, 3-5, 9 (the dash is an en dash)."""
    out, streak = [], []
    for n in numbers:
        if streak and n == streak[-1] + 1:
            streak.append(n)
            continue
        if streak:
            out.append(f"{streak[0]}–{streak[-1]}" if len(streak) > 1 else str(streak[0]))
        streak = [n]
    if streak:
        out.append(f"{streak[0]}–{streak[-1]}" if len(streak) > 1 else str(streak[0]))
    return ", ".join(out)


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


def attribute(br: BugReport, rules: Sequence[Rule]) -> tuple[dict[str, Finding], list[Match]]:
    """Attribute every journal entry, every dmesg line a rule knows, and inxi lines to rules."""
    found: dict[str, Finding] = {}
    unclassified: list[Match] = []

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
        elif KEYWORD_RE.search(e.text):
            unclassified.append(Match("dmesg", e.no, quote(f"[{e.t:12.6f}] {e.text}")))
    previous = [e for e in br.journal if e.boot == "previous"]
    last_prev = max((e.when for e in previous), default=None)
    for e in br.journal:
        shutdown = bool(last_prev and e.boot == "previous" and (last_prev - e.when).total_seconds() <= SHUTDOWN_WINDOW)
        rule = match_rule(rules, "journal", e.text, e.ident, shutdown=shutdown)
        if rule is None and e.ident == "kernel":  # kernel warnings repeat in the journal; dmesg rules know them
            rule = match_rule(rules, "dmesg", e.text)
        match = Match("journal", e.no, quote(e.line), e.boot, e.when)
        if rule:
            add(rule, match)
        else:
            unclassified.append(match)
    for n, line in br.inxi:
        rule = match_rule(rules, "inxi", line)
        if rule:
            add(rule, Match("inxi", n, quote(line.strip())))
    return found, unclassified


def apply_mitigations(found: dict[str, Finding], vj: VerifyLog) -> None:
    """Lower a finding to INFO when ry-verify reports its mitigation in place, citing the record."""
    for rule in RULES:
        f = found.get(rule.key)
        if not f or not rule.mitigated_by or SEVERITY_RANK[f.severity] > SEVERITY_RANK["LOW"]:
            continue
        hit = next((i for i in vj.items if i.status == "OK" and re.search(rule.mitigated_by, i.text)), None)
        if hit:
            f.severity = "INFO"
            f.notes.append(f"ry-verify reports the mitigation in place (VJ {hit.no}: {clip(quote(hit.text), 90)}).")


def verify_findings(vj: VerifyLog) -> list[Finding]:
    """Group ry-verify FAIL and WARN records by section into findings."""
    groups: dict[tuple[str, str, str], list[VerifyItem]] = {}
    for item in vj.items:
        if item.counted in ("FAIL", "WARN"):
            groups.setdefault((item.counted, item.phase, item.section), []).append(item)
    out = []
    for (status, phase, section), items in groups.items():
        sev = "MED" if status == "FAIL" else "LOW"
        title = f"ry-verify {status}: {section_title(section)} ({phase})"
        f = Finding(
            f"verify-{status}-{phase}-{section}",
            title,
            "ry-verify / " + section_title(section),
            sev,
            f"ry-verify {vj.header.get('version', '')} checks the managed configuration; these {phase} checks "
            f"reported {status}.",
            "Fix each listed item, then run `~/ry-install/ry-verify.fish --verify` again.",
        )
        f.matches = [Match("verify", i.no, quote(f"{i.status}: {i.text}")) for i in items]
        out.append(f)
    return out


def section_title(section: str) -> str:
    """Return a ry-verify section name in sentence case (WIFI STATE -> Wi-Fi state)."""
    return section.capitalize().replace("Wifi", "Wi-Fi").replace("Preamble", "Before the checks")


def find_identifiers(br: BugReport, vj: VerifyLog) -> list[tuple[str, list[int], list[int]]]:
    """Return each identifier class with the bug-report lines and JSONL records that carry it."""
    root = re.search(rf"root=UUID=({UUID})", br.cmdline)
    classes = list(IDENTIFIERS)
    if root:
        classes.insert(1, ("Root UUID, bare", re.compile(rf"(?<!root=UUID=){re.escape(root.group(1))}"), ""))
    out = []
    for name, pattern, _ in classes:
        br_lines = [n for n, line in enumerate(br.lines, 1) if pattern.search(line)]
        vj_lines = [n for n, text in vj.texts if pattern.search(text)]
        if name == "Other UUID":
            known = re.compile(rf"root=UUID={UUID}|uuid: {UUID}" + (f"|{re.escape(root.group(1))}" if root else ""))
            br_lines = [
                n
                for n in br_lines
                if not known.search(br.lines[n - 1]) or pattern.search(known.sub("", br.lines[n - 1]))
            ]
            vj_lines = [n for n, d in vj.texts if n in vj_lines and pattern.search(known.sub("", d))]
        if br_lines or vj_lines:
            out.append((name, br_lines, vj_lines))
    return out


def privacy_finding(ids: Sequence[tuple[str, list[int], list[int]]]) -> Finding | None:
    """Return the LOW finding for identifiers in the logs, or None when there are none."""
    if not ids:
        return None
    br = sorted({n for _, b, _ in ids for n in b})
    vj = sorted({n for _, _, v in ids for n in v})
    f = Finding(
        "identifiers",
        "Logs carry identifiers the bug-report redactor misses",
        "Privacy / log sharing",
        "LOW",
        "cachyos-bugreport.sh redacts the hostname, user name, home directory, IPv4 and colon-form MAC "
        "addresses, and email-shaped strings. UUIDs, USB serial numbers, USB4 domain IDs, underscore-form "
        "Bluetooth addresses, and anything in the ry-verify log stay readable; they tie posted logs to this "
        "machine.",
        "Post only copies with these identifiers removed (Section 9).",
    )
    f.matches = [Match("bugreport", n, "") for n in br] + [Match("verify", n, "") for n in vj]
    f.notes.append(f"{len(br)} bug-report lines and {len(vj)} ry-verify records carry identifiers (Section 5).")
    return f


MILESTONES = (
    ("initrd starts", r"Run /init as init process"),
    ("switch-root", r"systemd\[1\]: Switching root"),
    (
        "root mounted",
        (
            r"EXT4-fs \([^)]+\): mounted filesystem|XFS \([^)]+\): Ending clean mount|BTRFS info .*: "
            r"(?:first mount|enabling ssd)|F2FS-fs \([^)]+\): Mounted"
        ),
    ),
    ("Wi-Fi associated", r"\bwl\w+: associated"),
)


def timeline(br: BugReport) -> tuple[dict[str, float], tuple[float, float] | None]:
    """Return boot milestones (seconds since kernel start) and the longest quiet gap before the root mount."""
    marks: dict[str, float] = {}
    root = re.search(rf"root=UUID=({UUID})", br.cmdline)
    for name, pattern in MILESTONES:
        if name == "root mounted" and root:
            hit = next((e.t for e in br.dmesg if re.search(pattern, e.text) and root.group(1) in e.text), None)
            if hit is not None:
                marks[name] = hit
                continue
        hit = next((e.t for e in br.dmesg if re.search(pattern, e.text)), None)
        if hit is not None:
            marks[name] = hit
    limit = marks.get("root mounted", marks.get("switch-root", 0.0))
    times = [e.t for e in br.dmesg if e.t <= limit]
    gaps = [(b - a, a, b) for a, b in itertools.pairwise(times)]
    best = max(gaps, default=None)
    quiet = (best[1], best[2]) if best and best[0] >= 1.0 else None
    return marks, quiet


def kernel_start(br: BugReport) -> tuple[dt.datetime, float] | None:
    """Estimate the wall-clock kernel start from journal kernel entries that also appear in dmesg."""
    offsets = {}
    for e in br.dmesg:
        offsets.setdefault(e.text, e.t)
    lo, hi = None, None
    for e in br.journal:
        if e.boot != "current" or e.ident != "kernel" or e.text not in offsets:
            continue
        start = e.when - dt.timedelta(seconds=offsets[e.text])
        lo = max(lo, start) if lo else start
        end = start + dt.timedelta(seconds=1)
        hi = min(hi, end) if hi else end
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
        counts = order.setdefault((item.phase, item.section), {"OK": 0, "INFO": 0, "WARN": 0, "FAIL": 0})
        counts[item.counted] += 1
    return [(phase, section, counts) for (phase, section), counts in order.items()]


INXI_FACTS = (  # (key, inxi block, pattern, template); patterns run on the block's logical lines joined by newlines
    ("machine", "Machine", r"System:\s*(.+?)\s+product:\s*(.+?)\s+(?:v:|serial:)", "{0} {1}"),
    ("firmware", "Machine", r"UEFI:\s*(.+?)\s+v:\s*(\S+)\s+date:\s*(\S+)", "{0} {1} ({2})"),
    ("cpu", "CPU", r"\bmodel:\s*(.+?)\s+bits:", "{0}"),
    ("kernel", "System", r"Kernel:\s*(\S+)", "{0}"),
    ("distro", "System", r"Distro:\s*(.+?)(?:\s+base:.*)?$", "{0}"),
    ("desktop", "System", r"Desktop:\s*(.+?)\s+v:\s*(\S+)", "{0} {1}"),
    ("gpu", "Graphics", r"Device-1:\s*(.+?)\s+driver:", "{0}"),
    ("mesa", "Graphics", r"\bmesa v:\s*(\S+)", "{0}"),
    ("pipewire", "Audio", r"PipeWire v:\s*(\S+)", "{0}"),
    ("memory", "Info", r"Memory:\s*total:\s*([\d.]+ \w+)(?:.*?available:\s*([\d.]+ \w+))?", "{0} total, {1} available"),
    ("swap", "Swap", r"type:\s*zram\s+size:\s*([\d.]+ \w+)", "zram {0}"),
    ("temperatures", "Sensors", r"System Temperatures:\s*(.+)$", "{0}"),
    ("fans", "Sensors", r"Fan Speeds \(rpm\):\s*(.+)$", "{0}"),
    ("drive", "Drives", r"ID-1:\s*/dev/(\w+)\s.*?model:\s*(\S+)", "{0} {1}"),
    ("smart", "Drives", r"health:\s*(\w+)", "{0}"),
    ("drive-temp", "Drives", r"\btemp:\s*([\d.]+ C)", "{0}"),
    ("written", "Drives", r"written:\s*([\d.]+ \w+)", "{0}"),
)


def inxi_blocks(br: BugReport) -> dict[str, str]:
    """Return inxi's top-level blocks (System, Machine, CPU, …) as newline-joined logical lines."""
    blocks: dict[str, list[str]] = {}
    name = ""
    for _, line in br.inxi:
        if re.match(r"^[A-Z][A-Za-z]+:\s*$", line):
            name = line.strip(": ")
            blocks.setdefault(name, [])
        elif name:
            blocks[name].append(line.strip())
    return {k: "\n".join(v) for k, v in blocks.items()}


def inxi_facts(br: BugReport) -> dict[str, str]:
    """Return the facts inxi reports, keyed by name; absent facts are left out."""
    blocks = inxi_blocks(br)
    facts = {}
    for key, block, pattern, template in INXI_FACTS:
        m = re.search(pattern, blocks.get(block, ""), re.MULTILINE)
        if m:
            groups = [g or "?" for g in m.groups()]
            text = mask(template.format(*groups)).replace(", ? available", "")
            facts[key] = re.sub(r"\s+N/A\b", "", text) if key in ("machine", "drive") else text
    if vuln := vulnerability_counts(blocks.get("CPU", "")):
        facts["vulnerabilities"] = vuln
    return facts


def vulnerability_counts(cpu_block: str) -> str:
    """Return inxi's CPU vulnerability rows as counts, or "" when the block has none.

    inxi prints one `Type: <name> status: …` or `Type: <name> mitigation: …` row per sysfs entry; the rows share a
    logical line once wrapped, so each value ends at the next Type.
    """
    rows = re.findall(r"Type:\s*\S+\s+(status|mitigation):\s*(.+?)(?=\s+Type:\s|$)", cpu_block, re.MULTILINE)
    if not rows:
        return ""
    counts = {"not affected": 0, "mitigated": 0, "vulnerable": 0, "unknown": 0}
    for kind, text in rows:
        if kind == "mitigation":
            counts["mitigated"] += 1
        elif text.startswith("Not affected"):
            counts["not affected"] += 1
        elif text.startswith("Vulnerable"):
            counts["vulnerable"] += 1
        else:
            counts["unknown"] += 1
    return ", ".join(f"{n} {k}" for k, n in counts.items() if n or k != "unknown")


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
        ("secure-boot", r"Secure boot (disabled|enabled)"),
    )
    for key, pattern in probes:
        m = re.search(pattern, text)
        if m:
            out[key] = " / ".join(m.groups())
    out["smu"] = "initialized" if re.search(r"SMU is initialized", text) else ""
    return {k: v for k, v in out.items() if v}


def verify_health(m: Model) -> list[tuple[str, str, str]]:
    """Return the ry-verify and journal health rows."""
    vj, totals = m.vj, m.vj.totals
    static, runtime = vj.phase_results.get("static", {}), vj.phase_results.get("runtime", {})
    info = sum(1 for i in vj.items if i.status == "INFO")
    verdict_, exit_ = vj.status
    evidence = (
        f"{totals['ok']} OK = {static.get('ok', 0)} static + {runtime.get('ok', 0)} runtime; {totals['fail']} FAIL, "
        f"{totals['warn']} WARN, {totals['gen_fail']} GEN_FAIL; {info} INFO"
    )
    rows = [(f"ry-verify {vj.header.get('version', '')}", f"{verdict_}, {exit_}", evidence)]
    splats = [x for x in m.findings if x.key in ("kernel-splat", "kernel-taint")]
    ring = "No taint, oops, or splat" if not splats else f"{len(splats)} splat findings"
    rows.append(
        ("Kernel ring buffer", ring, f"{len(m.br.dmesg)} lines; {m.keyword_lines.get('dmesg', 0)} keyword lines")
    )
    failed = unit_failures(m)
    rows.append(
        ("Unit failures, current boot", "no journal entries", "journal section empty")
        if failed is None
        else ("Unit failures, current boot", str(failed), "journal, warning and above")
    )
    return rows


def unit_failures(m: Model) -> int | None:
    """Return the current boot's failed-unit entries, or None when its journal section holds no entries."""
    current = [e for e in m.br.journal if e.boot == "current"]
    if not current:
        return None
    return sum(1 for e in current if re.search(r"Failed with result '|Failed to start ", e.text))


def boot_health(m: Model) -> list[tuple[str, str, str]]:
    """Return the boot-timing, GPU memory, and firmware health rows."""
    d, rows = m.facts, []
    if "root mounted" in m.milestones:
        gap = f"; {m.quiet_gap[1] - m.quiet_gap[0]:.2f} s without output first" if m.quiet_gap else ""
        rows.append(("Boot to root mount", f"{m.milestones['root mounted']:.2f} s", f"dmesg{gap}"))
    if "Wi-Fi associated" in m.milestones:
        rows.append(("Wi-Fi associated", f"{m.milestones['Wi-Fi associated']:.2f} s", "dmesg"))
    if "vram" in d:
        gtt = f"GTT {int(d['gtt']):,} MiB" if "gtt" in d else "GTT not logged"
        rows.append(("GPU memory", f"VRAM {int(d['vram']):,} MiB", gtt))
    labels = (("smu", "SMU {}"), ("dmub", "DMUB {}"), ("vcn", "VCN ENC/DEC {}"))
    fw = [label.format(d[key]) for key, label in labels if key in d]
    if fw:
        rows.append(("amdgpu firmware", fw[0], "; ".join(fw[1:]) or "dmesg"))
    return rows


def hardware_health(m: Model) -> list[tuple[str, str, str]]:
    """Return the CPU, sensor, swap, Secure Boot, and drive health rows."""
    f, rows = m.facts, []
    for key, label in (
        ("microcode", "CPU microcode"),
        ("vulnerabilities", "CPU vulnerabilities"),
        ("temperatures", "Temperatures"),
        ("fans", "Fan speeds"),
        ("swap", "Swap"),
        ("secure-boot", "Secure Boot"),
    ):
        if key in f:
            rows.append((label, f[key], "dmesg" if key in ("microcode", "secure-boot") else "inxi"))
    if "drive" in f:
        smart = " · ".join(x for x in (f.get("smart", ""), f.get("drive-temp", ""), f.get("written", "")) if x)
        rows.append((f"Drive {f['drive']}", smart or "no SMART data", "inxi"))
    return rows


def health_rows(m: Model) -> list[tuple[str, str, str]]:
    """Return (check, result, evidence) rows for the system-health table, from facts the inputs state."""
    return [*verify_health(m), *boot_health(m), *hardware_health(m)]


def keyword_lines(br: BugReport) -> dict[str, int]:
    """Count the lines matching the failure keyword set, per report section."""
    out: dict[str, int] = {}
    for name in br.sections:
        key = "journal" if name.startswith("journal") else name
        out[key] = out.get(key, 0) + sum(1 for _, line in br.section_lines(name) if KEYWORD_RE.search(line))
    return out


FISH_SAFE = re.compile(r"[\w@%+=:,./-]+")  # characters fish reads literally in a bare word


def fish_quote(text: str) -> str:
    """Return text as one fish word: bare when safe, else single-quoted with backslashes and quotes escaped."""
    if FISH_SAFE.fullmatch(text):
        return text
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


# The rg check for each identifier class: the same matches as IDENTIFIERS, written for ripgrep (-P is PCRE2).
REDACTION_CHECKS = (
    (("Root UUID, root=UUID= form", "Root UUID, bare", "DMI system UUID", "Other UUID"),
     r"'\b[[:xdigit:]]{8}(-[[:xdigit:]]{4}){3}-[[:xdigit:]]{12}\b'"),
    (("USB4 domain ID",), r"'\b[[:xdigit:]]{8}-[[:xdigit:]]{4}-domain'"),
    (("USB serial numbers",), r"'SerialNumber:\s*[^<\[\s]'"),
    (("Bluetooth address, underscore form", "MAC address, colon form"),
     r"'\b([[:xdigit:]]{2}_){5}[[:xdigit:]]{2}\b|\b([[:xdigit:]]{2}:){5}[[:xdigit:]]{2}\b'"),
    (("Home directory",), r"'/home/[A-Za-z0-9._-]'"),
    (("IPv4 address",),
     r"-P '(?<!v: )(?<![\w.:/-])(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\w.])'"),
    (("Email address",),
     r"-P '[\w.%+-]+@[\w-]+\.(?!service\b|socket\b|timer\b|target\b|mount\b|slice\b|scope\b)[A-Za-z]{2,}\b'"),
)  # fmt: skip


def redaction_action(m: Model) -> Action | None:
    """Return the redaction action when the logs carry identifiers; the commands are fish.

    One rg check runs per identifier class found, over both public copies, so the checks cover Section 5 exactly.
    """
    if not m.identifiers:
        return None
    bug, ver = fish_quote(m.br.path.name), fish_quote(m.vj.path.name)
    pub_bug = fish_quote(f"{m.br.path.stem}-public{m.br.path.suffix}")
    pub_ver = fish_quote(f"{m.vj.path.stem}-public{m.vj.path.suffix}")
    found = {name for name, _, _ in m.identifiers}
    cmds = [
        f"set -l pub {pub_bug} {pub_ver}",
        f"cp {bug} $pub[1]",
        f"cp {ver} $pub[2]",
        "# edit both copies: remove every line class listed in Section 5, then:",
        *[f"rg -c {args} $pub" for classes, args in REDACTION_CHECKS if found.intersection(classes)],
    ]
    total = sum(m.identifier_lines)
    return Action(
        "",
        "Redact the identifiers before posting",
        f"{total} identifier lines across both logs",
        cmds,
        "Every rg check prints nothing (rg exits 1)",
    )


def failure_actions(m: Model) -> list[Action]:
    """Return the actions for ry-verify failures, kernel errors, and failed units."""
    acts = []
    fails = [f for f in m.findings if f.key.startswith("verify-")]
    if fails:
        acts.append(
            Action(
                "",
                "Fix the failing ry-verify checks",
                f"{len(fails)} ry-verify sections with FAIL or WARN",
                ["~/ry-install/ry-verify.fish --verify"],
                "ry-verify exits 0 with no FAIL or WARN",
            )
        )
    if any(f.key in ("kernel-splat", "kernel-taint", "gpu-hang", "storage-errors") for f in m.findings):
        acts.append(
            Action(
                "",
                "Investigate the kernel errors",
                "HIGH or MED kernel findings in Section 4",
                ["journalctl -k -b -p err", "journalctl -k -b -1 -p err"],
                "The cause is identified",
            )
        )
    units = sorted(
        {
            re.sub(r"^.*?: (.+?): Failed with result.*$", r"\1", mt.text)
            for f in m.findings
            if f.key == "unit-failed"
            for mt in f.matches
            if "Failed with result" in mt.text
        }
    )
    if units:
        acts.append(
            Action(
                "",
                "Inspect the failed units",
                f"{len(units)} units ended in failure",
                ["systemctl --failed", *[f"journalctl -b -u {fish_quote(u)}" for u in units[:6]]],
                "No unit stays failed",
            )
        )
    return acts


def plan_actions(m: Model) -> list[Action]:
    """Return the actions the findings call for, each with commands and a completion test."""
    acts = [a for a in (redaction_action(m),) if a] + failure_actions(m)
    if m.unclassified:
        acts.append(
            Action(
                "",
                "Review the unclassified lines",
                f"{len(m.unclassified)} lines match no rule (Section 7.4)",
                [f"rg -n -i 'warn|error|fail' {fish_quote(m.br.path.name)}"],
                "Each line is explained or a rule is added",
            )
        )
    for n, a in enumerate(acts, 1):
        a.aid = f"A-{n}"
    return acts


def analyze(br: BugReport, vj: VerifyLog) -> Model:
    """Run every analysis step and return the model the report is built from."""
    rules = applicable_rules(br)
    found, unclassified = attribute(br, rules)
    apply_mitigations(found, vj)
    findings = [f for f in found.values() if f.severity in FINDING_LEVELS] + verify_findings(vj)
    ids = find_identifiers(br, vj)
    pf = privacy_finding(ids)
    if pf:
        firsts = {}
        for name, b_lines, _v in ids:
            if b_lines:
                firsts.setdefault(b_lines[0], name)
        pf.matches = [
            Match("bugreport", n, quote(br.lines[n - 1].strip()) if n in firsts else "") for _, b, _ in ids for n in b
        ]
        pf.matches += [Match("verify", n, "") for _, _, v in ids for n in v]
        findings.append(pf)
    findings.sort(key=lambda f: (SEVERITY_RANK[f.severity], min((x.no for x in f.matches), default=0), f.key))
    counters: dict[str, int] = {}
    for f in findings:
        letter = f.severity[0]
        counters[letter] = counters.get(letter, 0) + 1
        f.fid = f"{letter}-{counters[letter]}"
    others = sorted(
        (f for f in found.values() if f.severity not in FINDING_LEVELS), key=lambda f: SEVERITY_RANK[f.severity]
    )
    marks, quiet = timeline(br)
    facts = {**inxi_facts(br), **dmesg_facts(br)}
    if "kernel" not in facts and (release := re.match(r"Linux \S+ (\S+)", br.uname)):
        facts["kernel"] = release.group(1)  # the report header's uname line stands in for a missing inxi block
    m = Model(
        br,
        vj,
        findings,
        others,
        unclassified,
        ids,
        marks,
        quiet,
        kernel_start(br),
        verify_sections(vj),
        facts,
        [],
        keyword_lines(br),
        [],
    )
    m.health = health_rows(m)
    m.actions = plan_actions(m)
    return m


# ── FIGURES ───────────────────────────────────────────────────────────
# Vector charts from the model: matplotlib with text as paths, embedded through svglib; grayscale only.
C_INK, C_DARK, C_MID, C_LIGHT, C_PALE = "#1d1d1d", "#3c3c3c", "#8a8a8a", "#c9c9c9", "#ececec"
CW = 7.1  # chart width in inches: the 512 pt text frame
FIG_MAX_HEIGHT = 8.0  # inches; a row-scaled chart stops growing here so it fits a page with its caption


def fig_height(base: float, per_row: float, rows: int) -> float:
    """Return a row-scaled figure height in inches, capped at FIG_MAX_HEIGHT."""
    return min(base + per_row * rows, FIG_MAX_HEIGHT)


MPL_FONTS = ("IBMPlexSans-Regular", "IBMPlexSans-SemiBold", "IBMPlexSans-Medium", "IBMPlexSansCondensed-Regular")
MPL_STYLE: dict[str, Any] = {
    "font.family": "IBM Plex Sans", "font.size": 7.6, "svg.fonttype": "path", "svg.hashsalt": "gtr9-postboot",
    "axes.linewidth": 0.6, "axes.edgecolor": "#3a3a3a", "xtick.major.width": 0.5, "ytick.major.width": 0,
    "xtick.major.size": 2.5, "axes.labelcolor": "#222", "xtick.color": "#333", "ytick.color": "#222",
    "axes.titlesize": 8.2, "axes.titleweight": "semibold", "axes.titlelocation": "left", "legend.frameon": False,
    "legend.fontsize": 7.2,
}  # fmt: skip
STATUS_STYLE: dict[str, dict[str, Any]] = {
    "OK": {"color": C_LIGHT},
    "INFO": {"facecolor": "white", "edgecolor": C_INK, "lw": 0.6},
    "WARN": {"color": C_MID},
    "FAIL": {"color": C_INK},
}
SEVERITY_STYLE: dict[str, dict[str, Any]] = {
    "HIGH": {"color": C_INK},
    "MED": {"color": C_INK},
    "LOW": {"color": C_DARK},
    "INFO": {"color": C_MID},
    "WATCH": {"facecolor": "white", "edgecolor": C_INK, "lw": 0.8, "ls": (0, (2, 1.5))},
    "SETTING": {"facecolor": "white", "edgecolor": C_INK, "lw": 0.8},
    "NOTE": {"color": C_LIGHT},
    "UNCLASSIFIED": {"facecolor": C_PALE, "edgecolor": C_INK, "lw": 0.6},
}


def _mpl() -> ModuleType:
    """Import matplotlib on first use (after the preflight), register the fonts, and apply the house style."""
    if STATE.mpl is not None:
        return STATE.mpl
    import matplotlib as mpl

    mpl.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    for stem in MPL_FONTS:
        font_manager.fontManager.addfont(str(STATE.font_dir / f"{stem}.ttf"))
    for key, value in MPL_STYLE.items():
        plt.rcParams[key] = value  # ty: ignore[invalid-assignment]  # stubs type values per key
    STATE.mpl = plt
    return plt


def clean(ax: Axes, *, grid: bool = True) -> None:
    """Hide the top, right, and left spines and add a light vertical grid behind the bars."""
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(b=False)
    if grid:
        ax.xaxis.grid(visible=True, color="#dcdcdc", lw=0.5)
        ax.set_axisbelow(b=True)


def save(fig: Figure, name: str) -> str:
    """Write one figure as SVG into the build's chart directory, close it, and return its name."""
    fig.savefig(
        STATE.chart_dir / f"{name}.svg", format="svg", bbox_inches="tight", pad_inches=0.04, metadata={"Date": None}
    )
    _mpl().close(fig)
    return name


def capture_points(m: Model) -> dict[str, float]:
    """Return the capture times as seconds since kernel start, when the kernel start is known."""
    if not m.kernel_start:
        return {}
    start = m.kernel_start[0]
    out = {}
    for label, when in (("ry-verify starts", m.vj.started), ("ry-verify ends", m.vj.finished)):
        if when:
            out[label] = (when.replace(tzinfo=None) - start).total_seconds()
    if m.br.captured:
        out["bug report"] = (m.br.captured - start).total_seconds()
    return {k: v for k, v in out.items() if 0 <= v <= 3600}


BOOT_SPAN = 600.0  # seconds of the ring buffer the boot timeline covers at most


def boot_axis(m: Model) -> tuple[float, dict[str, float]] | None:
    """Return the boot chart's x range and the capture points inside it, or None without early dmesg lines.

    The range follows the boot activity and any capture within BOOT_SPAN; later captures are named in the caption.
    """
    early = [e.t for e in m.br.dmesg if e.t < BOOT_SPAN]
    if not early:
        return None
    captures = capture_points(m)
    inside = [v for v in captures.values() if v <= BOOT_SPAN]
    xmax = min(max([*early, *m.milestones.values(), *inside]) * 1.06, BOOT_SPAN)
    return xmax, {k: v for k, v in captures.items() if v <= xmax}


def capture_marks(points: dict[str, float]) -> dict[str, float]:
    """Return the chart labels for the capture points: one mark per capture, the ry-verify run at its middle."""
    marks = {}
    if "ry-verify starts" in points and "ry-verify ends" in points:
        a, b = points["ry-verify starts"], points["ry-verify ends"]
        marks[f"ry-verify {a:.1f}–{b:.1f} s"] = (a + b) / 2
    elif "ry-verify starts" in points:
        marks[f"ry-verify {points['ry-verify starts']:.1f} s"] = points["ry-verify starts"]
    if "bug report" in points:
        marks[f"bug report {points['bug report']:.1f} s"] = points["bug report"]
    return marks


def fig_boot(m: Model) -> str | None:
    """Draw the boot timeline: dmesg lines per 0.5 s, the quiet gap, milestones, and capture points."""
    axis = boot_axis(m)
    if axis is None:
        return None  # the ring buffer no longer holds the boot; there is no timeline to draw
    plt = _mpl()
    xmax, captures = axis
    bins = int(xmax / 0.5) + 1
    counts = [0] * bins
    for e in m.br.dmesg:
        if e.t < xmax:
            counts[int(e.t / 0.5)] += 1
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(CW, 2.9), sharex=True, gridspec_kw={"height_ratios": [1.4, 1.1], "hspace": 0.12}
    )
    ax1.bar([i * 0.5 + 0.25 for i in range(bins)], counts, width=0.42, color=C_DARK)
    ax1.set_yscale("log")
    top = max([*counts, 1]) * 1.8
    ax1.set_ylim(0.8, top)  # a fixed floor keeps one-line bins visible and the gap label inside the axes
    ax1.set_ylabel("dmesg lines\nper 0.5 s", fontsize=7)
    if m.quiet_gap:
        a, b = m.quiet_gap
        ax1.axvspan(a, b, color=C_LIGHT, zorder=0)
        ax1.text((a + b) / 2, (0.8 * top) ** 0.5, f"no output\n{b - a:.2f} s", ha="center", va="center", fontsize=6.4)
    boot_marks = {f"{k}\n{v:.2f} s": v for k, v in m.milestones.items() if v <= xmax}
    for row, marks, marker in ((1, boot_marks, "v"), (0, capture_marks(captures), "s")):
        last, level = -1e9, 0
        for x, label in sorted((v, k) for k, v in marks.items()):
            level = 1 - level if x - last < xmax * 0.09 else 0
            last = x
            ax2.plot([x], [row], marker=marker, ms=5, color=C_INK, ls="none")
            y = row + 0.25 + 1.35 * level if row else row - 0.3 - 0.75 * level
            ax2.text(x, y, label, ha="center", va="bottom" if row else "top", fontsize=6.2)
    if "ry-verify starts" in captures and "ry-verify ends" in captures:
        ax2.plot(
            [captures["ry-verify starts"], captures["ry-verify ends"]], [0, 0], color=C_INK, lw=3, solid_capstyle="butt"
        )
    ax2.set_yticks([1, 0] if captures else [1])
    ax2.set_yticklabels(["Boot", "Captures"] if captures else ["Boot"])
    ax2.set_ylim(-2.0, 3.4)
    ax2.set_xlim(0, xmax)
    ax2.set_xlabel("Seconds since kernel start", fontsize=7.2)
    for ax in (ax1, ax2):
        clean(ax, grid=ax is ax1)
        ax.tick_params(axis="y", length=0)
    return save(fig, "boot")


def fig_verify(m: Model) -> str | None:
    """Draw ry-verify results per section, one panel per phase, stacked by status."""
    phases = [p for p in ("static", "runtime") if any(s[0] == p for s in m.verify_sections)]
    if not phases:
        return None
    plt = _mpl()
    from matplotlib.patches import Patch

    fig, axs = plt.subplots(
        1,
        len(phases),
        figsize=(CW, fig_height(0.4, 0.24, max(sum(1 for s in m.verify_sections if s[0] == p) for p in phases))),
        squeeze=False,
        gridspec_kw={"wspace": 0.62},
    )
    for ax, phase in zip(axs[0], phases, strict=True):
        rows = [s for s in m.verify_sections if s[0] == phase][::-1]
        names = [section_title(s[1]) for s in rows]
        left = [0] * len(rows)
        for status in ("OK", "INFO", "WARN", "FAIL"):
            vals = [s[2][status] for s in rows]
            ax.barh(names, vals, left=left, height=0.62, **STATUS_STYLE[status])
            left = [a + b for a, b in zip(left, vals, strict=True)]
        for i, s in enumerate(rows):
            c = s[2]
            label = f"{c['OK']}" + "".join(f" + {c[k]} {k}" for k in ("INFO", "WARN", "FAIL") if c[k])
            ax.text(left[i] + 0.6, i, label, va="center", fontsize=6.4)
        totals = {k: sum(s[2][k] for s in rows) for k in ("OK", "INFO", "WARN", "FAIL")}
        ax.set_title(f"{phase.capitalize()}: " + ", ".join(f"{v} {k}" for k, v in totals.items() if v))
        ax.set_xlim(0, max(left, default=1) * 1.45 + 1)
        clean(ax)
        ax.tick_params(axis="y", length=0)
    handles = [Patch(label=k, **STATUS_STYLE[k]) for k in ("OK", "INFO", "WARN", "FAIL")]
    fig.legend(handles=handles, loc="lower center", ncol=4, bbox_to_anchor=(0.5, -0.2))
    return save(fig, "verify")


def fig_areas(m: Model) -> str | None:
    """Draw findings per area beside the journal entries they explain (both boots)."""
    if not m.findings:
        return None
    plt = _mpl()
    areas: dict[str, list[Finding]] = {}
    for f in m.findings:
        areas.setdefault(f.area.split(" / ")[0], []).append(f)
    rows = sorted(areas.items(), key=lambda kv: (-sum(f.count("journal") for f in kv[1]), -len(kv[1]), kv[0]))[::-1]
    fig, axs = plt.subplots(
        1,
        2,
        figsize=(CW, fig_height(0.5, 0.2, len(rows))),
        sharey=True,
        gridspec_kw={"width_ratios": [1, 1.25], "wspace": 0.08},
    )
    names = [r[0] for r in rows]
    counts = [len(r[1]) for r in rows]
    journal = [sum(f.count("journal") for f in r[1]) for r in rows]
    axs[0].barh(names, counts, color=C_MID, height=0.6)
    for i, r in enumerate(rows):
        ids = [f.fid for f in r[1]]
        label = ", ".join(ids) if len(ids) <= 4 else f"{', '.join(ids[:3])} … +{len(ids) - 3}"
        axs[0].text(counts[i] + 0.08, i, label, va="center", fontsize=6.2)
    axs[0].set_xlim(0, max(counts) * 2.2 + 1)
    axs[0].set_title(f"Findings ({sum(counts)})")
    axs[1].barh(names, journal, color=C_DARK, height=0.6)
    for i, v in enumerate(journal):
        axs[1].text(
            v + max([*journal, 1]) * 0.01 + 0.3,
            i,
            str(v) if v else "— not in the journal",
            va="center",
            fontsize=6.2,
            color=C_INK if v else C_MID,
        )
    axs[1].set_xlim(0, max([*journal, 1]) * 1.25 + 1)
    axs[1].set_title(f"Journal entries, both boots ({sum(journal)})")
    for ax in axs:
        clean(ax)
        ax.tick_params(axis="y", length=0)
    return save(fig, "areas")


def fig_identifiers(m: Model) -> str | None:
    """Draw identifier lines per class, bug report and ry-verify log stacked."""
    if not m.identifiers:
        return None
    plt = _mpl()
    rows = sorted(m.identifiers, key=lambda r: len(r[1]) + len(r[2]))
    fig, ax = plt.subplots(figsize=(CW, fig_height(0.5, 0.22, len(rows))))
    names = [r[0] for r in rows]
    br = [len(r[1]) for r in rows]
    vj = [len(r[2]) for r in rows]
    ax.barh(names, br, color=C_DARK, height=0.6, label=f"{m.br.path.name} ({sum(br)})")
    ax.barh(
        names, vj, left=br, color="white", edgecolor=C_INK, lw=0.7, height=0.6, label=f"{m.vj.path.name} ({sum(vj)})"
    )
    for i, (b, v) in enumerate(zip(br, vj, strict=True)):
        ax.text(b + v + 0.3, i, str(b + v), va="center", fontsize=6.6)
    ax.set_xlim(0, max(b + v for b, v in zip(br, vj, strict=True)) * 1.2 + 1)
    ax.set_xlabel("Lines to redact before posting", fontsize=7.2)
    clean(ax)
    ax.tick_params(axis="y", length=0)
    ax.legend(loc="lower right")
    return save(fig, "identifiers")


def family_rows(m: Model) -> list[tuple[str, int, str]]:
    """Return (family, dmesg lines, severity) for every rule with dmesg evidence, plus the unclassified lines."""
    rows = [(f.title, f.count("dmesg"), f.severity) for f in [*m.findings, *m.others] if f.count("dmesg")]
    rows.sort(key=lambda r: (-r[1], r[0]))
    unclassified = sum(1 for u in m.unclassified if u.stream == "dmesg")
    if unclassified:
        rows.append(("Unclassified keyword lines", unclassified, "UNCLASSIFIED"))
    return rows


def fig_families(m: Model) -> str | None:
    """Draw dmesg notice lines per family, styled by disposition."""
    rows = family_rows(m)[::-1]
    if not rows:
        return None
    plt = _mpl()
    from matplotlib.patches import Patch

    fig, ax = plt.subplots(figsize=(CW, fig_height(0.5, 0.17, len(rows))))
    for i, (_name, value, sev) in enumerate(rows):
        ax.barh(i, value, height=0.62, **SEVERITY_STYLE[sev])
        ax.text(value + 0.6, i, str(value), va="center", fontsize=6.4)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r[0] for r in rows])
    ax.set_xlim(0, max(r[1] for r in rows) * 1.15 + 1)
    ax.set_xlabel("dmesg lines", fontsize=7.2)
    clean(ax)
    ax.tick_params(axis="y", length=0)
    present = [s for s in SEVERITY_STYLE if any(r[2] == s for r in rows)]
    ax.legend(handles=[Patch(label=s.lower(), **SEVERITY_STYLE[s]) for s in present], loc="lower right", ncol=2)
    return save(fig, "families")


def journal_rows(m: Model) -> list[tuple[str, int, int]]:
    """Return (label, current-boot entries, previous-boot entries) for every finding with journal evidence."""
    rows = [
        (f.fid or f.title, f.count("journal", "current"), f.count("journal", "previous"))
        for f in [*m.findings, *m.others]
        if f.count("journal")
    ]
    cur = sum(1 for u in m.unclassified if u.stream == "journal" and u.boot == "current")
    prev = sum(1 for u in m.unclassified if u.stream == "journal" and u.boot == "previous")
    if cur or prev:
        rows.append(("Unclassified", cur, prev))
    return rows


def fig_journal(m: Model) -> str | None:
    """Draw journal entries per finding, current boot beside previous boot."""
    rows = journal_rows(m)
    if not rows:
        return None
    plt = _mpl()
    fig, axs = plt.subplots(
        1, 2, figsize=(CW, fig_height(0.6, 0.17, len(rows))), sharey=True, gridspec_kw={"wspace": 0.1}
    )
    y = list(range(len(rows)))[::-1]
    for ax, idx, title in ((axs[0], 1, "Current boot"), (axs[1], 2, "Previous boot")):
        vals = [r[idx] for r in rows]
        ax.barh(y, vals, height=0.6, color=C_DARK if idx == 1 else C_MID)
        for v, n in zip(y, vals, strict=True):
            if n:
                ax.text(n + 0.4, v, str(n), va="center", fontsize=6.2)
        ax.set_title(f"{title} ({sum(vals)})")
        ax.set_xlim(0, max([r[1] for r in rows] + [r[2] for r in rows]) * 1.18 + 1)
        ax.set_xlabel("Journal entries", fontsize=7.2)
        clean(ax)
        ax.tick_params(axis="y", length=0)
    axs[0].set_yticks(y)
    axs[0].set_yticklabels([r[0] for r in rows])
    return save(fig, "journal")


def fig_prevboot(m: Model) -> str | None:
    """Draw the previous boot's journal entries over wall-clock time, one row per finding."""
    prev = [
        (f.fid or f.title, [x.when for x in f.matches if x.boot == "previous" and x.when])
        for f in [*m.findings, *m.others]
    ]
    prev = [(k, t) for k, t in prev if t]
    unc = [u.when for u in m.unclassified if u.boot == "previous" and u.when]
    if unc:
        prev.append(("Unclassified", unc))
    if not prev:
        return None
    plt = _mpl()
    import matplotlib.dates as mdates

    fig, ax = plt.subplots(figsize=(CW, fig_height(0.6, 0.17, len(prev))))
    for i, (_label, times) in enumerate(prev[::-1]):
        ax.plot(times, [i] * len(times), marker="|", ms=7, mew=1.2, ls="none", color=C_INK)
    ax.set_yticks(range(len(prev)))
    ax.set_yticklabels([f"{k} ({len(t)})" for k, t in prev[::-1]])
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    first = min(min(t) for _, t in prev)
    ax.set_xlabel(f"Previous boot, {first:%Y-%m-%d}; one tick per journal entry", fontsize=7.2)
    clean(ax)
    ax.tick_params(axis="y", length=0)
    return save(fig, "prevboot")


# ── LAYOUT ────────────────────────────────────────────────────────────
# ReportLab platypus: styles, references, flowables, sections, page templates.
INK, MUTE, RULE, LIGHT, ZEBRA, DARK, MID = (
    HexColor(x) for x in ("#1b1b1b", "#5a5a5a", "#a3a3a3", "#e8e8e8", "#f4f4f4", "#2d2d2d", "#8a8a8a")
)
PW, PH = letter
LM = RM = 50  # pt, left and right page margins
TOPM, BOTM = 58, 54  # pt, top and bottom page margins (the running header and footer sit inside them)
FW = PW - LM - RM
EVIDENCE_LINES = 8  # evidence lines quoted per card; the Lines row lists them all
CODE_SIZE = 7.3  # monospace size of command blocks
CODE_MIN_SIZE = 5.6  # smallest size a command block shrinks to before a line wraps
HOLD_ROWS = 2  # data rows a page split leaves on each side of a table; fewer, and the table moves whole


def style(name: str, parent: ParagraphStyle | None = None, **kw: object) -> ParagraphStyle:
    """Return a paragraph style: the parent's settings, else the house defaults (Plex 9/12.8, ink), plus kw."""
    if parent is not None:
        return ParagraphStyle(name, parent=parent, **kw)
    base: dict[str, object] = {"fontName": "Plex", "fontSize": 9, "leading": 12.8, "textColor": INK}
    base["bulletFontName"] = "Plex"
    base.update(kw)
    return ParagraphStyle(name, **base)


sty_body = style("body", spaceAfter=5.5)
sty_bullet = style("bullet", leftIndent=12, bulletIndent=2, spaceAfter=3.2)
sty_h1 = style("h1", fontName="Plex-SB", fontSize=15.5, leading=19, spaceBefore=2, spaceAfter=9, keepWithNext=1)
sty_h2 = style("h2", fontName="Plex-SB", fontSize=10.6, leading=14, spaceBefore=11, spaceAfter=4.5, keepWithNext=1)
sty_table_title = style("ttl", fontName="Plex-SB", fontSize=8.1, leading=10.8)
sty_caption = style("cap", fontName="Plex-It", fontSize=7.5, leading=10, textColor=MUTE, spaceBefore=3, spaceAfter=11)
sty_th = style("th", fontName="PlexC-SB", fontSize=7.6, leading=9.5)
sty_td = style("td", fontName="PlexC", fontSize=7.8, leading=9.9)
sty_td_right = style("tdr", parent=sty_td, alignment=TA_RIGHT)
sty_td_mono = style("tdm", fontName="PlexM", fontSize=6.9, leading=9.1)
sty_code = style("code", fontName="PlexM", fontSize=CODE_SIZE, leading=10.6)
sty_label = style("lab", fontName="PlexC-SB", fontSize=7.4, leading=9.6, textColor=MUTE)
sty_card_value = style("cv", parent=sty_td, fontSize=8.0, leading=10.6)


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


def pref(key: str) -> str:
    """Return a linked "p. N" reference to an anchor."""
    return f'<a href="#{key}" color="#1b1b1b">p.\u00a0{page_of(key)}</a>'


REF_KEYS = re.compile(r"\{p:((?:sec|sub|card|fig|tab)-[\w.-]+)\}")  # the anchors the report defines


def fmt(text: str, st: ParagraphStyle | None = None) -> str:
    """Escape text for a Paragraph and apply the house markup: `code`, **bold**, and {p:key} page links.

    Only the report's own anchor keys become links, so a quoted log line cannot reference a missing target.
    """
    out = escape(text)
    mono_size = (getattr(st, "fontSize", 9) if st else 9) - 0.7
    out = re.sub(r"`([^`]+)`", lambda m: f'<font name="PlexM" size="{mono_size:.1f}">{m.group(1)}</font>', out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", out)
    return REF_KEYS.sub(lambda m: pref(m.group(1)), out)


def para(text: str, st: ParagraphStyle = sty_body) -> Paragraph:
    """Return a Paragraph from house-markup text."""
    return Paragraph(fmt(text, st), st)


def bullet_paragraphs(texts: Iterable[str]) -> list[Flowable]:
    """Return bulleted body paragraphs."""
    return [Paragraph(fmt(t), sty_bullet, bulletText="•") for t in texts]


STRUCT = (
    ("sec-1", 0, "1", "Summary"),
    ("sub-1.1", 1, "1.1", "Key facts"),
    ("sub-1.2", 1, "1.2", "Findings register"),
    ("sub-1.3", 1, "1.3", "Actions"),
    ("sec-2", 0, "2", "Inputs and method"),
    ("sub-2.1", 1, "2.1", "Inputs"),
    ("sub-2.2", 1, "2.2", "Capture window"),
    ("sub-2.3", 1, "2.3", "Method"),
    ("sub-2.4", 1, "2.4", "Severity scale"),
    ("sec-3", 0, "3", "System health"),
    ("sec-4", 0, "4", "Findings"),
    ("sec-5", 0, "5", "Identifiers"),
    ("sec-6", 0, "6", "Watch items and settings"),
    ("sec-7", 0, "7", "Coverage"),
    ("sub-7.1", 1, "7.1", "Streams and cross-checks"),
    ("sub-7.2", 1, "7.2", "dmesg notice families"),
    ("sub-7.3", 1, "7.3", "Journal attribution"),
    ("sub-7.4", 1, "7.4", "Unclassified lines"),
    ("sec-8", 0, "8", "ry-verify"),
    ("sub-8.1", 1, "8.1", "Sections"),
    ("sub-8.2", 1, "8.2", "Notes, warnings, and failures"),
    ("sec-9", 0, "9", "Actions"),
    ("sec-A", 0, "A", "Environment snapshot"),
    ("sec-B", 0, "B", "Analysis rules"),
)
SD = {key: (level, number, title) for key, level, number, title in STRUCT}


class HPara(Paragraph):
    """Numbered section heading that records its page, bookmark, and outline entry."""

    def __init__(self, key: str) -> None:
        """Compose the heading for a STRUCT key."""
        level, number, title = SD[key]
        self.key, self.level, self.toc = key, level, f"{number}  {title}"
        num = (
            f'<font name="Plex-Md" color="#8a8a8a">{number}</font>\u2002'
            if level == 0
            else f'<font color="#5a5a5a">{number}</font>\u2002'
        )
        super().__init__(f"{num}{escape(title)}", sty_h1 if level == 0 else sty_h2)

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
        ("LINEBELOW", (0, head + 1), (-1, -1), 0.25, RULE),
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
Cell = tuple[int, int]


class HouseTable(Table):
    """A Table whose page splits leave at least HOLD_ROWS data rows on each side; otherwise it moves whole.

    ReportLab builds the parts of a split table with this class but without rowSplitRange, so the default is set
    here from the repeated rows, and it holds on every continuation page.
    """

    def __init__(self, data: Sequence[Sequence[object]], **kw: object) -> None:
        """Pass everything to Table, with rowSplitRange derived from the repeated rows unless given."""
        repeat = kw.get("repeatRows", 0)  # a count, or the indexes of the rows to repeat
        held = max(repeat) + 1 if isinstance(repeat, (tuple, list)) else repeat if isinstance(repeat, int) else 0
        kw.setdefault("rowSplitRange", (held + HOLD_ROWS, -HOLD_ROWS))
        super().__init__(data, **kw)


class HeadingGroup(KeepTogether):
    """The keepWithNext group: headings stay with what follows, but a table may split under them.

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


def cell(value: object, column: int, mono: Sequence[int], right: Sequence[int]) -> Flowable:
    """Return a table cell: flowables pass through; text becomes a Paragraph in the column's style."""
    if isinstance(value, Flowable):
        return value
    if column in mono:
        return Paragraph(escape(str(value)), sty_td_mono)
    return Paragraph(fmt(str(value), sty_td), sty_td_right if column in right else sty_td)


def table(
    head: Sequence[str],
    rows: Sequence[Sequence[object]],
    widths: Sequence[float],
    *,
    title: str = "",
    mono: Sequence[int] = (),
    right: Sequence[int] = (),
    bold_last: bool = False,
    spans: Sequence[tuple[Cell, Cell]] = (),
) -> list[Flowable]:
    """Return a house table (optional numbered title, ruled zebra grid, repeated header) and a spacer.

    The title is the grid's first row, so it never ends a page alone; a page split repeats the header and leaves
    at least HOLD_ROWS data rows on each side, else the table moves whole. Spans are given against the header row.
    Raise ValueError when the column widths do not fill the text frame or a row has the wrong number of cells.
    """
    label = title or "untitled table"
    if len(widths) != len(head) or abs(sum(widths) - FW) > 0.5:
        msg = f"{label}: {len(widths)} widths summing to {sum(widths):g} pt for {len(head)} columns, frame {FW:g} pt"
        raise ValueError(msg)
    if any(len(row) != len(head) for row in rows):
        msg = f"{label}: every row needs {len(head)} cells"
        raise ValueError(msg)
    if not rows:
        rows = [["—", *[""] * (len(head) - 1)]]
    data: list[list[object]] = [[Paragraph(fmt(h, sty_th), sty_th) for h in head]]
    data += [[cell(value, j, mono, right) for j, value in enumerate(row)] for row in rows]
    first = 0  # the header row
    commands: list[tuple[object, ...]] = []
    if title:
        STATE.tables += 1
        caption = Paragraph(f'<font color="#5a5a5a">Table {STATE.tables}</font>\u2002{escape(title)}', sty_table_title)
        data.insert(0, [[Anchor(f"tab-{STATE.tables}"), caption], *[""] * (len(head) - 1)])
        first = 1
        commands += TITLE_ROW_STYLE
    commands += table_style(first)
    commands += [
        ("SPAN", (c0, r0 + first if r0 >= 0 else r0), (c1, r1 + first if r1 >= 0 else r1))
        for (c0, r0), (c1, r1) in spans
    ]
    if bold_last:
        commands.append(("LINEABOVE", (0, -1), (-1, -1), 0.6, INK))
    grid = HouseTable(data, colWidths=list(widths), repeatRows=(first,), hAlign="LEFT", spaceBefore=7 if title else 0)
    grid.setStyle(TableStyle(commands))
    return [grid, Spacer(1, 6)]


CHIP_COLORS = {
    "HIGH": (INK, colors.white, INK),
    "MED": (DARK, colors.white, DARK),
    "LOW": (colors.white, INK, INK),
    "INFO": (LIGHT, INK, MID),
    "MITIGATED": (colors.white, MUTE, MID),
}


def chip_width(text: str) -> float:
    """Return a chip's width: its label in PlexC SemiBold 6.8 plus 9 pt of padding."""
    return stringWidth(text, "PlexC-SB", 6.8) + 9


def chip(text: str) -> Table:
    """Return a small bordered label (severity or MITIGATED) as a one-cell table."""
    fill, ink, border = CHIP_COLORS[text]
    label = Paragraph(
        f'<font name="PlexC-SB" size="6.8" color="{ink.hexval()}">{text}</font>',
        style("chip", alignment=TA_CENTER, leading=8),
    )
    box = Table([[label]], colWidths=[chip_width(text)], rowHeights=[11])
    box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), fill),
                ("BOX", (0, 0), (-1, -1), 0.7, border),
                *ZERO_PAD[:3],
                ("TOPPADDING", (0, 0), (-1, -1), 1.4),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return box


def card(f: Finding) -> KeepTogether:
    """Return a finding card: ID, title, chips, meta line, evidence, lines, counts, explanation, notes, action."""
    labels = [f.severity, *(["MITIGATED"] if any("mitigation" in n for n in f.notes) else [])]
    widths = [chip_width(t) + 4 for t in labels]
    chips = Table([[chip(t) for t in labels]], colWidths=widths, hAlign="RIGHT")
    chips.setStyle(
        TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2), *ZERO_PAD[2:]])
    )
    idp = Paragraph(f'<font name="PlexM-SB" size="11">{f.fid}</font>', style("cid", leading=13))
    tp = Paragraph(f'<font name="Plex-SB" size="9.4">{escape(f.title)}</font>', style("ctt", leading=12))
    header = Table([[idp, tp, chips]], colWidths=[46, FW - 46 - sum(widths) - 14, sum(widths) + 6])
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), *ZERO_PAD]))
    meta = escape(f"{f.severity} · {f.area} · Register, ") + pref("sub-1.2")
    quotes = [m.text for m in f.matches if m.text]
    shown = [Paragraph(escape(q), sty_td_mono) for q in quotes[:EVIDENCE_LINES]]
    if len(quotes) > EVIDENCE_LINES:
        shown.append(Paragraph(f"… and {len(quotes) - EVIDENCE_LINES} more (Lines row)", sty_label))
    counts = ", ".join(
        f"{n} {label}"
        for n, label in (
            (f.count("journal", "current"), "current-boot journal"),
            (f.count("journal", "previous"), "previous-boot journal"),
            (f.count("dmesg"), "dmesg"),
            (f.count("inxi"), "inxi"),
            (f.count("verify"), "ry-verify"),
        )
        if n
    )
    data: list[list[object]] = [
        [header, ""],
        [Paragraph(meta, style("meta", fontName="PlexC", fontSize=7.4, leading=9.4, textColor=MUTE)), ""],
    ]
    if shown:
        data.append([Paragraph("Evidence", sty_label), shown])
    data.append([Paragraph("Lines", sty_label), Paragraph(escape(f.lines()), style("ln", parent=sty_td))])
    if counts:
        data.append([Paragraph("Counts", sty_label), Paragraph(counts, style("cn", parent=sty_td))])
    rows = [("Explanation", f.explanation), *(("Note", n) for n in f.notes), ("Action", f.action)]
    data += [[Paragraph(k, sty_label), Paragraph(fmt(v, sty_td), sty_card_value)] for k, v in rows]
    body = Table(data, colWidths=[62, FW - 62])
    body.setStyle(TableStyle(card_style(f.severity, has_evidence=bool(shown))))
    return KeepTogether([Anchor(f"card-{f.fid}", f"{f.fid}  {f.title}", 1), body, Spacer(1, 9)])


def card_style(severity: str, *, has_evidence: bool) -> list[tuple[object, ...]]:
    """Return a card's table commands: header fill by severity, evidence shading, rules, padding."""
    commands: list[tuple[object, ...]] = [
        ("SPAN", (0, 0), (-1, 0)),
        ("SPAN", (0, 1), (-1, 1)),
        ("BOX", (0, 0), (-1, -1), 1.0 if severity in ("HIGH", "MED") else 0.7, INK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT if severity == "INFO" else colors.white),
        ("LINEBELOW", (0, 0), (-1, 0), 0.7, INK),
        ("LINEBELOW", (0, 1), (-1, -2), 0.25, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.4),
        ("TOPPADDING", (0, 0), (-1, 0), 4.5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4.5),
    ]
    if has_evidence:
        commands.append(("BACKGROUND", (1, 2), (1, 2), ZEBRA))
    if severity in ("HIGH", "MED", "LOW"):
        commands.append(("LINEBEFORE", (0, 0), (0, -1), 3.2 if severity != "LOW" else 1.6, INK))
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
    head = f'<font name="Plex-SBIt" color="#3a3a3a">Figure {num}</font>\u2002'
    return [
        KeepTogether(
            [Anchor(f"fig-{num}"), Spacer(1, 2), drawing, Paragraph(head + fmt(caption, sty_caption), sty_caption)]
        )
    ]


def code_block(label: str, lines: Sequence[str]) -> list[Flowable]:
    """Return a labeled command block, one monospace line per command, then a spacer.

    The font shrinks (down to CODE_MIN_SIZE) so the longest command fits the frame on one line.
    """
    head = Paragraph(
        f'<font name="PlexC-SB" size="6.8" color="#ffffff">{escape(label)}</font>', style("cbl", leading=8.5)
    )
    longest = max((stringWidth(line, "PlexM", CODE_SIZE) for line in lines), default=0)
    size = CODE_SIZE if longest <= FW - 16 else max(CODE_MIN_SIZE, CODE_SIZE * (FW - 16) / longest)
    code = style("code-fit", parent=sty_code, fontSize=size, leading=size * 1.45)
    block = Table([[head], *[[Paragraph(escape(line), code)] for line in lines]], colWidths=[FW])
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
    return [block, Spacer(1, 4)]


def sha16(raw: bytes) -> str:
    """Return the first 16 hex digits of a SHA256."""
    return hashlib.sha256(raw).hexdigest()[:16]


def severity_counts(m: Model) -> dict[str, int]:
    """Return the number of findings per severity."""
    return {s: sum(1 for f in m.findings if f.severity == s) for s in FINDING_LEVELS}


def verdict(m: Model) -> tuple[str, str]:
    """Return the cover verdict: a head line and a summary sentence, both from the model."""
    c = severity_counts(m)
    if c["HIGH"] or c["MED"]:
        n = c["HIGH"] + c["MED"]
        head = (
            "Attention — "
            + " and ".join(f"{c[k]} {k}" for k in ("HIGH", "MED") if c[k])
            + f" finding{'s' if n != 1 else ''}."
        )
    elif c["LOW"]:
        head = f"Healthy, with {c['LOW']} LOW finding{'s' if c['LOW'] != 1 else ''} to act on."
    else:
        head = "Healthy — no HIGH, MED, or LOW findings."
    t, exit_ = m.vj.totals, m.vj.status[1]
    splats = any(f.key in ("kernel-splat", "kernel-taint") for f in m.findings)
    body = (
        f"{len(m.findings)} findings: " + ", ".join(f"{v} {k}" for k, v in c.items()) + ". "
        f"ry-verify {m.vj.header.get('version', '')} reports {t['ok']} OK, {t['fail']} FAIL, {t['warn']} WARN, "
        f"and {t['gen_fail']} GEN_FAIL ({exit_}); "
        f"the kernel ring {'has a splat or taint' if splats else 'shows no taint, oops, or splat'}; "
        f"{len(m.unclassified)} log lines match no rule."
    )
    return head, body


def verdict_panel(m: Model) -> Table:
    """Return the cover's verdict box: a black bar beside the verdict head and summary."""
    head, text = verdict(m)
    summary = style("vb", fontSize=8.6, leading=11.8)
    body = [
        Paragraph('<font name="PlexC-SB" size="7" color="#5a5a5a">VERDICT</font>', style("vk", leading=9)),
        Paragraph(fmt(head), style("vh", fontName="Plex-SB", fontSize=12.5, leading=16, spaceAfter=2)),
        Paragraph(fmt(text, summary), summary),
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
    """Return the cover's count strip: findings, signals, and ry-verify results."""
    c = severity_counts(m)
    groups = (
        ("FINDINGS", [("HIGH", c["HIGH"]), ("MED", c["MED"]), ("LOW", c["LOW"]), ("INFO", c["INFO"])]),
        (
            "SIGNALS",
            [
                ("WATCH", sum(1 for f in m.others if f.severity == "WATCH")),
                ("ID LINES", sum(m.identifier_lines)),
                ("UNCLASSIFIED", len(m.unclassified)),
            ],
        ),
        ("RY-VERIFY", [("FAIL", m.vj.totals["fail"]), ("WARN", m.vj.totals["warn"])]),
    )
    items = [i for _, g in groups for i in g]

    def text(markup: str, name: str, leading: float) -> Paragraph:
        """Return a centered paragraph."""
        return Paragraph(markup, style(name, alignment=TA_CENTER, leading=leading))

    row0: list[object] = []
    for name, g in groups:
        row0 += [text(f'<font name="Plex-SB" size="7.2">{name}</font>', "kg", 9), *[""] * (len(g) - 1)]
    data = [
        row0,
        [text(f'<font name="Plex-SB" size="17">{v}</font>', "kb", 19) for _, v in items],
        [text(f'<font name="PlexC-SB" size="6.4" color="#5a5a5a">{k}</font>', "ks", 8) for k, _ in items],
    ]
    strip = Table(data, colWidths=[FW / len(items)] * len(items), rowHeights=[13, 22, 12])
    commands: list[tuple[object, ...]] = [
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, RULE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]
    col = 0
    for i, (_, g) in enumerate(groups):
        end = col + len(g) - 1
        commands += [("SPAN", (col, 0), (end, 0)), ("BOX", (col, 0), (end, -1), 0.9, INK)]
        if i == 1:
            commands.append(("BACKGROUND", (col, 0), (end, -1), HexColor("#ececec")))
        col = end + 1
    strip.setStyle(TableStyle(commands))
    return strip


def tile_rows(m: Model) -> list[tuple[str, str, str]]:
    """Return up to six (label, value, detail) tiles for the cover, each short enough for its box."""
    t, d = m.vj.totals, m.facts
    verdict_, exit_ = m.vj.status
    tiles = [
        (
            "ry-verify",
            f"{verdict_}, {exit_}" if m.vj.footer else exit_,
            f"{t['ok']} OK · {t['fail']} FAIL · {t['warn']} WARN",
        )
    ]
    splats = [x for x in m.findings if x.key in ("kernel-splat", "kernel-taint")]
    tiles.append(
        (
            "kernel ring",
            "Clean" if not splats else "Splat",
            f"{len(m.br.dmesg)} lines · {m.keyword_lines.get('dmesg', 0)} keyword",
        )
    )
    if "root mounted" in m.milestones:
        quiet = f"{m.quiet_gap[1] - m.quiet_gap[0]:.2f} s quiet first" if m.quiet_gap else "dmesg"
        tiles.append(("boot to root mount", f"{m.milestones['root mounted']:.2f} s", quiet))
    failed = unit_failures(m)
    tiles.append(
        ("unit failures", "—", "journal section empty")
        if failed is None
        else ("unit failures", str(failed), "current boot, journal")
    )
    if "vram" in d:
        tiles.append(
            ("GPU memory", f"{int(d['vram']):,} MiB", f"VRAM · GTT {int(d['gtt']):,} MiB" if "gtt" in d else "VRAM")
        )
    temps = re.findall(r"(cpu|mobo|gpu)\S*:(?: amdgpu temp:)? ([\d.]+ C)", d.get("temperatures", ""))
    if temps:
        tiles.append(("temperatures", f"{temps[0][0]} {temps[0][1]}", " · ".join(f"{k} {v}" for k, v in temps[1:3])))
    return tiles[:6]


def health_tiles(m: Model) -> Table:
    """Return the cover's health tiles."""
    cells = [
        [
            Paragraph(
                f'<font name="PlexC-SB" size="6.4" color="#5a5a5a">{escape(c.upper())}</font>', style("hk", leading=8)
            ),
            Paragraph(f'<font name="Plex-SB" size="10.5">{escape(r)}</font>', style("hv", leading=13)),
            Paragraph(
                f'<font name="PlexC" size="6.6" color="#3a3a3a">{escape(clip(e, 44))}</font>', style("hs", leading=8.2)
            ),
        ]
        for c, r, e in tile_rows(m)
    ]
    if not cells:
        return Table([[""]])
    tiles = Table([cells], colWidths=[FW / len(cells)] * len(cells))
    tiles.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 0), (-1, 0), 2.0, INK),
                ("LINEBEFORE", (1, 0), (-1, -1), 0.4, RULE),
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
    rows = [
        (
            "Bug report",
            f"`{m.br.path.name}` · {len(m.br.raw):,} bytes · {len(m.br.lines):,} lines · SHA256 {sha16(m.br.raw)}…",
        ),
        (
            "ry-verify log",
            f"`{m.vj.path.name}` · {len(m.vj.raw):,} bytes · {len(m.vj.records):,} records · SHA256 {sha16(m.vj.raw)}…",
        ),
        ("Captured", m.br.date_text or "not stated in the bug report"),
        ("Generated by", f"build_report.py {__version__}; every value in this report comes from the two inputs"),
        ("Masking", "Identifiers in quoted lines are replaced by placeholders such as [root UUID] and [serial]"),
    ]
    key = style("dk", fontName="PlexC-SB", fontSize=7.6, leading=9.8)
    table_ = Table(
        [[Paragraph(f"<b>{k}</b>", key), Paragraph(fmt(v, sty_td), style("dv", parent=sty_td))] for k, v in rows],
        colWidths=[80, FW - 80],
    )
    table_.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 0), (-1, 0), 0.8, INK),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2.2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.4),
            ]
        )
    )
    return table_


def machine_line(m: Model) -> str:
    """Return the cover's machine line from inxi and the ry-verify header."""
    f = m.facts
    parts = [
        f.get("machine", "Unknown machine"),
        f.get("distro", ""),
        f"Linux {f['kernel']}" if "kernel" in f else "",
        f"ry-verify {m.vj.header.get('version', '')}",
    ]
    return " · ".join(p for p in parts if p)


def toc_sections() -> Table:
    """Return the sections column of the contents page, with page numbers."""
    rows = []
    for key, level, number, title in STRUCT:
        if level == 0:
            st = style("tc0", fontName="Plex-SB", fontSize=8.8, leading=11.4)
        else:
            st = style("tc1", fontSize=8.2, leading=10.6, leftIndent=16)
        link = f'<a href="#{key}" color="#1b1b1b">'
        page = Paragraph(f"{link}{page_of(key)}</a>", style("tp", parent=st, leftIndent=0, alignment=TA_RIGHT))
        rows.append([Paragraph(f"{link}{number}\u2002{escape(title)}</a>", st), page])
    sections = Table(rows, colWidths=[FW * 0.62 - 40, 40], hAlign="LEFT")
    rules = [("LINEABOVE", (0, i), (-1, i), 0.3, RULE) for i, s in enumerate(STRUCT) if s[1] == 0 and i]
    padding = [("TOPPADDING", (0, 0), (-1, -1), 1.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.2)]
    sections.setStyle(TableStyle([*ZERO_PAD[:2], *padding, *rules]))
    return sections


READING_GUIDE = (
    "**IDs** — H, M, L, and I mark HIGH, MED, LOW, and INFO findings, numbered by severity and first line.",
    "**Lines** — BR is a line of the bug report, VJ a record of the ry-verify log; quoted lines are masked.",
    (
        "**Coverage** — every journal entry and every dmesg line a rule knows is attributed; anything else "
        "that matches the failure keywords is listed in Section 7.4."
    ),
    "**Actions** — Section 9 holds the commands, one per line, ready to type in fish.",
)


def toc_side() -> list[Flowable]:
    """Return the side column of the contents page: the figure list and the reading guide."""
    figs = [
        [
            Paragraph(
                f'<a href="#fig-{n}" color="#1b1b1b"><font color="#5a5a5a">Figure {n}</font>\u2002{escape(t)}</a>',
                style("tf", fontSize=7.6, leading=9.8),
            ),
            Paragraph(
                f'<a href="#fig-{n}" color="#1b1b1b">{page_of(f"fig-{n}")}</a>',
                style("tfp", fontSize=7.6, leading=9.8, alignment=TA_RIGHT),
            ),
        ]
        for n, t in STATE.fig_ref
    ]
    head = style("fh", fontName="Plex-SB", fontSize=9.5, leading=12, spaceAfter=4)
    side: list[Flowable] = [Paragraph("Figures", head)]
    if figs:
        fig_list = Table(figs, colWidths=[FW * 0.38 - 30, 22])
        padding = [("TOPPADDING", (0, 0), (-1, -1), 1.3), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.3)]
        fig_list.setStyle(TableStyle([*ZERO_PAD[:2], *padding, ("LINEBELOW", (0, 0), (-1, -2), 0.25, RULE)]))
        side.append(fig_list)
    guide = style("g", fontSize=7.6, leading=10.2, spaceAfter=4)
    side += [Spacer(1, 14), Paragraph("How to read this report", style("gh", parent=head))]
    return side + [Paragraph(fmt(g, guide), guide) for g in READING_GUIDE]


def toc() -> Table:
    """Return the contents page: sections with pages beside the figure list and the reading guide."""
    outer = Table([[toc_sections(), toc_side()]], colWidths=[FW * 0.62, FW * 0.38])
    outer.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (0, 0), 18),
                ("LINEBEFORE", (1, 0), (1, 0), 0.4, RULE),
                ("LEFTPADDING", (1, 0), (1, 0), 14),
            ]
        )
    )
    return outer


def _story_cover(m: Model) -> list[Flowable]:
    """Return the cover page and the contents page."""
    mute = style("cs", fontSize=10.2, leading=13.5, textColor=MUTE)
    return [
        Spacer(1, 16),
        Paragraph("Post-Boot Log Analysis", style("ct", fontName="Plex-SB", fontSize=25, leading=29, spaceAfter=6)),
        para(machine_line(m), mute),
        para(
            f"Captured {m.br.date_text or 'at an unstated time'} · print edition",
            style("cs2", parent=mute, spaceAfter=12),
        ),
        verdict_panel(m),
        Spacer(1, 10),
        kpi_strip(m),
        para(
            "Findings: Section 4. Identifiers: Section 5. Watch items and settings are not counted as findings.",
            style("kn", fontName="Plex-It", fontSize=7.4, leading=10, textColor=MUTE, spaceBefore=3, spaceAfter=12),
        ),
        health_tiles(m),
        Spacer(1, 12),
        Paragraph("Document control", style("dch", fontName="Plex-SB", fontSize=8.4, leading=11, spaceAfter=3)),
        doc_control(m),
        NextPageTemplate("body"),
        PageBreak(),
        Paragraph("Contents", style("cth", fontName="Plex-SB", fontSize=15.5, leading=19, spaceAfter=10)),
        toc(),
        PageBreak(),
    ]


def key_facts(m: Model) -> list[str]:
    """Return the key-fact bullets, each built from the inputs."""
    f, t = m.facts, m.vj.totals
    cur = sum(1 for e in m.br.journal if e.boot == "current")
    prev = [e for e in m.br.journal if e.boot == "previous"]
    hw = ", ".join(x for x in (f.get("machine"), f.get("cpu"), f.get("gpu")) if x)
    out = [f"Hardware: {hw}." if hw else "Hardware: inxi block not found."]
    software = [
        f"kernel {f['kernel']}" if "kernel" in f else "",
        f.get("desktop", ""),
        f"Mesa {f['mesa']}" if "mesa" in f else "",
        f"PipeWire {f['pipewire']}" if "pipewire" in f else "",
    ]
    named = [s for s in software if s]
    out.append(f"Software: {', '.join(named)}." if named else "Software: not stated in the bug report.")
    out.append(
        f"ry-verify {m.vj.header.get('version', '?')} (profile {m.vj.header.get('profile', '?')}, mode "
        f"{m.vj.header.get('mode', '?')}): {t['ok']} OK, {t['fail']} FAIL, {t['warn']} WARN, {m.vj.status[1]}."
    )
    span = f" ({prev[0].when:%H:%M:%S}–{prev[-1].when:%H:%M:%S})" if prev else ""
    out.append(
        f"Logs: {len(m.br.dmesg):,} dmesg lines; {cur} journal entries in the current boot and {len(prev)} in the "
        f"previous boot{span}; {len(m.unclassified)} lines match no rule."
    )
    if "root mounted" in m.milestones:
        gap = f", after {m.quiet_gap[1] - m.quiet_gap[0]:.2f} s without output" if m.quiet_gap else ""
        wifi = (
            f"; Wi-Fi associated at {m.milestones['Wi-Fi associated']:.2f} s"
            if "Wi-Fi associated" in m.milestones
            else ""
        )
        out.append(f"Boot: root mounted at {m.milestones['root mounted']:.2f} s{gap}{wifi}.")
    if m.identifiers:
        b, v = m.identifier_lines
        out.append(f"Identifiers: {b} bug-report lines and {v} ry-verify records (Section 5).")
    return out


def _story_sec1(m: Model) -> list[Flowable]:
    """Return Section 1: key facts, the findings register, and the actions."""
    out: list[Flowable] = [HPara("sec-1"), HPara("sub-1.1"), *bullet_paragraphs(key_facts(m)), HPara("sub-1.2")]
    rows = [
        [
            f.fid,
            f.severity,
            f.title,
            f.area,
            f.first_line(),
            f.count("journal", "current"),
            f.count("journal", "previous"),
            f"{{p:card-{f.fid}}}",
        ]
        for f in m.findings
    ]
    head = ["ID", "Sev.", "Finding", "Area", "First line", "Cur.", "Prev.", "Page"]
    out += table(head, rows, [30, 32, 160, 108, 56, 28, 28, 70], title="Findings register", right=(5, 6))
    out.append(HPara("sub-1.3"))
    if m.actions:
        rows = [[a.aid, a.title, a.why, "{p:sec-9}"] for a in m.actions]
        out += table(["ID", "Action", "Why", "Page"], rows, [32, 170, 250, 60], title="Actions")
    else:
        out.append(para("The findings call for no action."))
    return out


def inputs_table(m: Model) -> list[Flowable]:
    """Return the inputs table: sizes, line counts, content, and full SHA256 of both files."""
    br_secs = ", ".join(n for n in SECTION_TITLES.values() if n in m.br.sections)
    phases = ", ".join(p for p in ("static", "runtime") if any(i.phase == p for i in m.vj.items))
    h = m.vj.header
    rows: list[list[object]] = [
        [f"`{m.br.path.name}`", f"{len(m.br.raw):,}", f"{len(m.br.lines):,}", f"Sections found: {br_secs}"],
        [Paragraph("SHA256", sty_label), Paragraph(hashlib.sha256(m.br.raw).hexdigest(), sty_td_mono), "", ""],
        [
            f"`{m.vj.path.name}`",
            f"{len(m.vj.raw):,}",
            f"{len(m.vj.records):,}",
            (
                f"ry-verify {h.get('version', '?')}, profile {h.get('profile', '?')}, mode {h.get('mode', '?')}; "
                f"phases: {phases or 'none'}"
            ),
        ],
        [Paragraph("SHA256", sty_label), Paragraph(hashlib.sha256(m.vj.raw).hexdigest(), sty_td_mono), "", ""],
    ]
    spans = [((1, 2), (3, 2)), ((1, 4), (3, 4))]
    return table(
        ["File", "Bytes", "Lines", "Content"], rows, [172, 46, 38, 256], title="Inputs", right=(1, 2), spans=spans
    )


def method_bullets(m: Model) -> list[Flowable]:
    """Return the method bullets; the keyword table follows them, so its number is the next one."""
    return bullet_paragraphs(
        (
            (
                f"The bug report is split at its separator lines into {len(m.br.sections)} sections; dmesg, "
                "both journal boots, inxi, and the package list are parsed line by line, and every quote keeps "
                "its line number (BR)."
            ),
            (
                f"{len(RULES)} rules recognize known message classes; the first matching rule claims a line. "
                "Every journal entry is attributed or listed as unclassified, and dmesg lines matching the "
                f"failure keywords (Table {STATE.tables + 1}) but no rule are listed too."
            ),
            (
                f"Journal entries of the previous boot within {SHUTDOWN_WINDOW} s of its last entry count as shutdown "
                "noise."
            ),
            (
                "A ry-verify OK record that shows a finding's mitigation in place lowers that finding to INFO and is "
                "cited."
            ),
            "Severity follows the observed impact on this host, not the log level.",
        )
    )


def keyword_table() -> list[Flowable]:
    """Return the failure keyword set as a five-column grid."""
    kw = list(KEYWORDS)
    grid = [[f"`{w}`" for w in kw[i : i + 5]] + [""] * (5 - len(kw[i : i + 5])) for i in range(0, len(kw), 5)]
    title = f"Failure keyword set ({len(kw)} terms, case-insensitive)"
    return table(["Terms", "", "", "", ""], grid, [FW / 5] * 5, title=title, spans=[((0, 0), (-1, 0))])


def _story_sec2(m: Model) -> list[Flowable]:
    """Return Section 2: inputs, capture window, method, and severity scale."""
    out: list[Flowable] = [CondPageBreak(240), HPara("sec-2"), HPara("sub-2.1"), *inputs_table(m), HPara("sub-2.2")]
    out += table(
        ["Event", "Wall clock", "Since kernel start"], capture_rows(m), [196, 176, 140], title="Capture window"
    )
    out += [HPara("sub-2.3"), *method_bullets(m), *keyword_table(), HPara("sub-2.4")]
    return out + table(["Level", "Meaning"], SEVERITY_SCALE, [78, 434], title="Severity scale")


SEVERITY_SCALE = (
    ("HIGH", "Data loss, crashes, hardware at risk, or a broken function."),
    ("MED", "Degraded function, a failed ry-verify check, or a kernel taint."),
    ("LOW", "Limited or conditional impact; the action is optional or quick."),
    ("INFO", "Explained and harmless; no action required."),
    ("WATCH", "A limit kept under observation; not counted as a finding."),
    ("SETTING", "A configuration choice visible in the logs; not counted."),
    ("NOTE", "Boilerplate printed on every boot; counted in the coverage tables only."),
)


def capture_rows(m: Model) -> list[list[str]]:
    """Return the capture-window rows: previous boot, kernel start, ry-verify run, and bug report."""
    rows = []
    prev = [e for e in m.br.journal if e.boot == "previous"]
    if prev:
        rows.append(
            ["Previous boot (journal span)", f"{prev[0].when:%Y-%m-%d %H:%M:%S} → {prev[-1].when:%H:%M:%S}", "—"]
        )
    if m.kernel_start:
        rows.append(
            [
                "Kernel start (estimate)",
                f"{m.kernel_start[0]:%Y-%m-%d %H:%M:%S.%f}"[:-4] + f" (±{m.kernel_start[1]:.2f} s)",
                "0 s",
            ]
        )
    points = capture_points(m)
    if m.vj.started:
        since = f"{points['ry-verify starts']:.1f} s" if "ry-verify starts" in points else "—"
        rows.append(["ry-verify run starts", f"{m.vj.started:%Y-%m-%d %H:%M:%S %z}", since])
    if m.vj.finished:
        since = f"{points['ry-verify ends']:.1f} s" if "ry-verify ends" in points else "—"
        rows.append(["ry-verify run ends", f"{m.vj.finished:%Y-%m-%d %H:%M:%S %z}", since])
    if m.br.captured:
        since = f"{points['bug report']:.1f} s" if "bug report" in points else "—"
        rows.append(["Bug report captured", m.br.date_text, since])
    return rows


def _story_sec3(m: Model) -> list[Flowable]:
    """Return Section 3: health checks, the boot timeline, and ry-verify results by section."""
    out: list[Flowable] = [CondPageBreak(260), HPara("sec-3")]
    out += table(["Check", "Result", "Evidence"], m.health, [130, 160, 222], title="System health checks")
    gap = (
        f" The shaded stretch is {m.quiet_gap[1] - m.quiet_gap[0]:.2f} s without output before the root mount."
        if m.quiet_gap
        else ""
    )
    cap = f" Capture points use the kernel-start estimate (±{m.kernel_start[1]:.2f} s)." if m.kernel_start else ""
    axis = boot_axis(m)
    late = {k: v for k, v in capture_points(m).items() if axis and v > axis[0]}
    if late:
        cap += " Beyond the chart: " + ", ".join(f"{k} {v:,.1f} s" for k, v in late.items()) + "."
    out += svgfig(
        STATE_FIGS.get("boot"),
        "Boot timeline",
        f"dmesg lines per 0.5 s on a log scale, with the milestones the kernel logged.{gap}{cap}",
    )
    out += svgfig(
        STATE_FIGS.get("verify"),
        "ry-verify results by section",
        "Records per section and status, static phase left and runtime right; summary lines are excluded.",
    )
    return out


def _story_sec4(m: Model) -> list[Flowable]:
    """Return Section 4: findings by area and one card per finding."""
    out: list[Flowable] = [PageBreak(), HPara("sec-4")]
    if not m.findings:
        return [*out, para("No rule produced a finding.")]
    c = severity_counts(m)
    out.append(
        para(
            f"{len(m.findings)} findings, ordered by severity and first line: "
            + ", ".join(f"{v} {k}" for k, v in c.items())
            + "."
        )
    )
    out += svgfig(
        STATE_FIGS.get("areas"),
        "Findings by area",
        "Findings per area beside the journal entries they explain in both boots.",
    )
    return out + [card(f) for f in m.findings]


def _story_sec5(m: Model) -> list[Flowable]:
    """Return Section 5: identifier classes with their lines, and the chart."""
    out: list[Flowable] = [CondPageBreak(260), HPara("sec-5")]
    if not m.identifiers:
        return [*out, para("Neither input carries an identifier the patterns recognize.")]
    out.append(
        para(
            "Lines that tie the logs to this machine or a paired device. The report itself masks them; remove "
            "them from copies before posting (Section 9)."
        )
    )
    rows: list[list[object]] = [
        [
            name,
            " · ".join(x for x in (f"BR {ranges(b)}" if b else "", f"VJ {ranges(v)}" if v else "") if x),
            len(b) + len(v),
        ]
        for name, b, v in m.identifiers
    ]
    b_all, v_all = m.identifier_lines
    rows.append(["Lines with any identifier", f"BR {b_all} · VJ {v_all}", b_all + v_all])
    out += table(
        ["Identifier class", "Lines", "Count"],
        rows,
        [172, 292, 48],
        title="Identifier classes",
        right=(2,),
        bold_last=True,
    )
    return out + svgfig(
        STATE_FIGS.get("identifiers"),
        "Identifier lines by class",
        "Lines to redact per class, bug report and ry-verify log stacked.",
    )


def _story_sec6(m: Model) -> list[Flowable]:
    """Return Section 6: watch items and settings visible in the logs."""
    out: list[Flowable] = [CondPageBreak(240), HPara("sec-6")]
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
    return out + table(
        ["Kind", "Item", "Evidence", "Lines", "Meaning"],
        rows,
        [52, 100, 150, 70, 140],
        title="Watch items and settings",
        mono=(2,),
    )


def _story_sec7(m: Model) -> list[Flowable]:
    """Return Section 7: streams, cross-checks, notice families, journal attribution, and unclassified lines."""
    out: list[Flowable] = [PageBreak(), HPara("sec-7"), HPara("sub-7.1")]
    out += table(
        ["Stream", "Size", "Attributed", "Unclassified"],
        stream_rows(m),
        [150, 90, 150, 122],
        title="Coverage by stream",
    )
    out += table(["Cross-check", "Result"], cross_checks(m), [372, 140], title="Parser cross-checks")
    out.append(HPara("sub-7.2"))
    fams = [[name, n, sev.lower()] for name, n, sev in family_rows(m)]
    out += table(["Family", "Lines", "Disposition"], fams, [300, 60, 152], title="dmesg lines by family", right=(1,))
    out += svgfig(
        STATE_FIGS.get("families"),
        "dmesg lines by family",
        "Lines per rule family, styled by disposition; unclassified keyword lines last.",
    )
    out.append(HPara("sub-7.3"))
    rows: list[list[object]] = [[label, cur, prev] for label, cur, prev in journal_rows(m)]
    cur_all = sum(1 for e in m.br.journal if e.boot == "current")
    prev_all = sum(1 for e in m.br.journal if e.boot == "previous")
    rows.append(["All journal entries", cur_all, prev_all])
    out += table(
        ["Finding", "Current boot", "Previous boot"],
        rows,
        [312, 100, 100],
        title="Journal attribution",
        right=(1, 2),
        bold_last=True,
    )
    out += svgfig(
        STATE_FIGS.get("journal"),
        "Journal entries per finding",
        "Entries per finding, current boot beside previous boot.",
    )
    out += svgfig(
        STATE_FIGS.get("prevboot"),
        "Previous boot timeline",
        "One tick per journal entry of the previous boot, per finding.",
    )
    out.append(HPara("sub-7.4"))
    if not m.unclassified:
        return [*out, para("None: every journal entry and every keyword-matching dmesg line was attributed.")]
    rows = [[u.stream + (f" ({u.boot})" if u.boot else ""), f"BR {u.no}", u.text] for u in m.unclassified]
    return out + table(["Stream", "Line", "Text"], rows, [76, 46, 390], title="Unclassified lines", mono=(2,))


def stream_rows(m: Model) -> list[list[object]]:
    """Return per-stream sizes and how many lines rules attributed."""
    found = [*m.findings, *m.others]
    rows: list[list[object]] = []
    for label, stream, boot, total in (
        ("dmesg", "dmesg", "", len(m.br.dmesg)),
        ("Journal, current boot", "journal", "current", sum(1 for e in m.br.journal if e.boot == "current")),
        ("Journal, previous boot", "journal", "previous", sum(1 for e in m.br.journal if e.boot == "previous")),
        ("inxi", "inxi", "", len(m.br.inxi)),
    ):
        attributed = sum(f.count(stream, boot) for f in found)
        unc = sum(1 for u in m.unclassified if u.stream == stream and (not boot or u.boot == boot))
        rows.append([label, f"{total:,} lines", attributed, unc if stream != "inxi" else "—"])
    rows.append(["ry-verify records", f"{len(m.vj.records):,} records", f"{len(m.vj.items)} results", "—"])
    rows.append(["Installed packages", f"{len(m.br.packages)} packages", "versions only", "—"])
    return rows


def cross_checks(m: Model) -> list[list[str]]:
    """Return checks of the parse against the inputs' own totals."""
    out = []
    for phase in ("static", "runtime"):
        res = m.vj.phase_results.get(phase)
        if res is None:
            continue
        counts = {k: sum(s[2][k] for s in m.verify_sections if s[0] == phase) for k in ("OK", "WARN", "FAIL")}
        ok = (counts["OK"], counts["WARN"], counts["FAIL"]) == (res.get("ok"), res.get("warn"), res.get("fail"))
        out.append(
            [
                (
                    f"ry-verify {phase} records match its VERIFY_RESULT ({res.get('ok')} OK, {res.get('warn')} WARN, "
                    f"{res.get('fail')} FAIL)"
                ),
                "match" if ok else f"differs: {counts}",
            ]
        )
    if m.vj.combined and m.vj.footer:
        pairs = (("ok", "pass"), ("fail", "fail"), ("warn", "warn"), ("gen_fail", "gen_fail"))
        same = all(m.vj.combined.get(c, 0) == m.vj.footer.get(f, 0) for c, f in pairs)
        out.append(
            ["ry-verify combined totals match the footer (OK, FAIL, WARN, GEN_FAIL)", "match" if same else "differs"]
        )
    journal = sum(f.count("journal") for f in [*m.findings, *m.others]) + sum(
        1 for u in m.unclassified if u.stream == "journal"
    )
    out.append(
        [
            f"Every journal entry is attributed or listed ({len(m.br.journal)} entries)",
            "match" if journal == len(m.br.journal) else f"differs: {journal}",
        ]
    )
    unparsed = m.br.journal_unparsed
    out.append(
        [
            "Journal lines read as entries, continuations, or markers",
            "match" if not unparsed else f"differs: {len(unparsed)} unread (BR {clip(ranges(unparsed), 36)})",
        ]
    )
    out.append(["Sections found in the bug report", f"{len(m.br.sections)} of {len(SECTION_TITLES)}"])
    return out


def _story_sec8(m: Model) -> list[Flowable]:
    """Return Section 8: ry-verify section counts and its non-OK records."""
    out: list[Flowable] = [CondPageBreak(260), HPara("sec-8"), HPara("sub-8.1")]
    rows: list[list[object]] = [
        [p, section_title(s), c["OK"], c["INFO"], c["WARN"], c["FAIL"]] for p, s, c in m.verify_sections
    ]
    out += table(
        ["Phase", "Section", "OK", "INFO", "WARN", "FAIL"],
        rows,
        [60, 212, 60, 60, 60, 60],
        title="ry-verify sections",
        right=(2, 3, 4, 5),
    )
    out.append(HPara("sub-8.2"))
    items = [[f"VJ {i.no}", section_title(i.section), i.status, quote(i.text)] for i in m.vj.items if i.status != "OK"]
    return out + table(
        ["Record", "Section", "Status", "Text"],
        items,
        [50, 110, 44, 308],
        title="ry-verify notes, warnings, and failures",
        mono=(3,),
    )


def _story_sec9(m: Model) -> list[Flowable]:
    """Return Section 9: commands for each action, then a checklist."""
    out: list[Flowable] = [PageBreak(), HPara("sec-9")]
    if not m.actions:
        return [*out, para("The findings call for no action.")]
    out.append(para("Commands for each action, one per line; tick the checklist as each completion test passes."))
    out.extend(
        KeepTogether(
            [
                para(f"**{a.aid}** — {a.title}: {a.why}."),
                *code_block(f"{a.aid} COMMANDS", a.commands),
                para(f"Done when: {a.done_when}."),
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


def _story_appendices(m: Model) -> list[Flowable]:
    """Return Appendix A (inxi as captured, masked) and Appendix B (the analysis rules)."""
    out: list[Flowable] = [CondPageBreak(300), HPara("sec-A")]
    rows = [[f"BR {n}", sanitize(line)] for n, line in m.br.inxi]
    out += table(["Line", "inxi output"], rows, [50, 462], title="Environment snapshot (inxi -Farz, masked)", mono=(1,))
    out += [CondPageBreak(200), HPara("sec-B")]
    rules = [[r.key, r.severity, r.area, ", ".join(r.streams), len(r.patterns)] for r in RULES]
    return out + table(
        ["Rule", "Severity", "Area", "Streams", "Patterns"],
        rules,
        [96, 52, 196, 112, 56],
        title="Analysis rules",
        right=(4,),
    )


STATE_FIGS: dict[str, str | None] = {}


def story(m: Model) -> list[Flowable]:
    """Return all flowables in reading order; figure and table numbers restart each pass."""
    STATE.figures.clear()
    STATE.tables = 0
    parts = (
        _story_cover,
        _story_sec1,
        _story_sec2,
        _story_sec3,
        _story_sec4,
        _story_sec5,
        _story_sec6,
        _story_sec7,
        _story_sec8,
        _story_sec9,
        _story_appendices,
    )
    return [flowable for part in parts for flowable in part(m)]


class Doc(BaseDocTemplate):
    """US Letter document with cover and body page templates and the report metadata."""

    def __init__(self, filename: str, m: Model) -> None:
        """Set the metadata, frames, and page templates."""
        when = capture_day(m)
        super().__init__(
            filename, pagesize=letter, leftMargin=LM, rightMargin=RM, topMargin=TOPM, bottomMargin=BOTM,
            title=f"Post-Boot Log Analysis ({when}), print edition",
            author="build_report.py",
            subject=f"Findings from {m.br.path.name} and {m.vj.path.name}",
            keywords="CachyOS, cachyos-bugreport, ry-verify, dmesg, journal, log analysis, print edition",
            creator=f"build_report.py {__version__} (ReportLab)",
            keepTogetherClass=HeadingGroup,  # keepWithNext groups that let a table split under its heading
        )  # fmt: skip
        pad = {"leftPadding": 0, "rightPadding": 0, "topPadding": 0, "bottomPadding": 0}
        body = Frame(LM, BOTM, FW, PH - TOPM - BOTM, id="f", **pad)
        cover = Frame(LM, BOTM, FW, PH - 44 - BOTM, id="fc", **pad)
        self.addPageTemplates(
            [PageTemplate("cover", [cover], onPage=on_cover), PageTemplate("body", [body], onPage=on_body)]
        )

    # ReportLab calls this hook by name after placing each flowable.
    def afterFlowable(self, flowable: Flowable) -> None:  # noqa: N802
        """Note the page of each level-0 heading and whether it opens the page."""
        frame = self.frame
        if isinstance(flowable, HPara) and flowable.level == 0 and frame is not None:
            top = frame._y1 + frame._height  # frame position is only exposed as attributes
            heading = f"{SD[flowable.key][1]}  {SD[flowable.key][2]}"
            STATE.h1pos.setdefault(self.page, (heading, (top - frame._y) < HEADING_TOP_BAND))


def capture_day(m: Model) -> str:
    """Return the capture date (bug report first, then ry-verify) as YYYY-MM-DD."""
    if m.br.captured:
        return f"{m.br.captured:%Y-%m-%d}"
    return f"{m.vj.started:%Y-%m-%d}" if m.vj.started else "undated"


def footer(canvas: Canvas, doc: BaseDocTemplate) -> None:
    """Draw the footer rule with generator and capture date, edition, and page X of Y."""
    m = model()
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(LM, 40, PW - RM, 40)
    canvas.setFont("Plex", 7)
    canvas.setFillColor(MUTE)
    canvas.drawString(LM, 29, f"build_report.py {__version__} · captured {capture_day(m)}")
    canvas.drawCentredString(PW / 2, 29, "Print edition")
    canvas.drawRightString(PW - RM, 29, f"Page {doc.page} of {STATE.total or '?'}")


def on_cover(canvas: Canvas, doc: BaseDocTemplate) -> None:
    """Decorate the cover: black band with the edition and capture date, plus the footer."""
    canvas.saveState()
    canvas.setFillColor(INK)
    canvas.rect(0, PH - 30, PW, 30, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Plex-SB", 7.8)
    canvas.drawString(LM, PH - 19, "POST-BOOT LOG ANALYSIS · PRINT EDITION")
    canvas.drawRightString(PW - RM, PH - 19, f"CAPTURED {capture_day(model())}")
    footer(canvas, doc)
    canvas.restoreState()


def on_body(canvas: Canvas, doc: BaseDocTemplate) -> None:
    """Decorate a body page: running header with the current section, plus the footer."""
    canvas.saveState()
    canvas.setFont("Plex", 7)
    canvas.setFillColor(MUTE)
    canvas.drawString(LM, PH - 34, f"Post-Boot Log Analysis · {model().facts.get('machine', 'unknown machine')}")
    canvas.setFont("Plex-SB", 7)
    canvas.setFillColor(INK)
    canvas.drawRightString(PW - RM, PH - 34, STATE.page_section.get(doc.page, "Contents"))
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(LM, PH - 40, PW - RM, PH - 40)
    footer(canvas, doc)
    canvas.restoreState()


# ── BUILD ─────────────────────────────────────────────────────────────
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
    """Return the running-header text per page: the section at the top of each page."""
    sections, current = {}, "Contents"
    for page in range(1, total + 1):
        if page in STATE.h1pos and STATE.h1pos[page][1]:
            current = STATE.h1pos[page][0]
        sections[page] = current
        if page in STATE.h1pos and not STATE.h1pos[page][1]:
            current = STATE.h1pos[page][0]
    return sections


def render_figures(m: Model) -> int:
    """Render every figure the model supports; return how many were drawn."""
    STATE_FIGS.clear()
    for key, draw in (
        ("boot", fig_boot),
        ("verify", fig_verify),
        ("areas", fig_areas),
        ("identifiers", fig_identifiers),
        ("families", fig_families),
        ("journal", fig_journal),
        ("prevboot", fig_prevboot),
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


def source_epoch(m: Model) -> int:
    """Return the embedded PDF date: the capture time, else the ry-verify start, else 0.

    The bug report's date line names its zone by abbreviation only, so the capture time takes the ry-verify
    log's UTC offset when both fall on the same day, and UTC otherwise.
    """
    if m.br.captured:
        zone = m.vj.started.tzinfo if m.vj.started and m.vj.started.date() == m.br.captured.date() else dt.UTC
        return int(m.br.captured.replace(tzinfo=zone).timestamp())
    return int(m.vj.started.timestamp()) if m.vj.started else 0


def build(args: argparse.Namespace, font_dir: Path, *, verbose: bool = False) -> Path:
    """Parse and analyze the inputs, render the figures, lay out the report, and write the PDF atomically."""

    def log(message: str) -> None:
        """Report a build step on stderr when verbose; stop logging if stderr's reader goes away."""
        nonlocal verbose
        if verbose:
            try:
                print(f"build_report.py: {message}", file=sys.stderr)
            except BrokenPipeError:
                verbose = False
                silence(sys.stderr)

    br, vj = parse_bugreport(args.bugreport), parse_verify(args.verify)
    log(
        f"bug report: {len(br.lines)} lines, {len(br.dmesg)} dmesg, {len(br.journal)} journal, "
        f"{len(br.journal_unparsed)} journal lines unread; ry-verify: {len(vj.records)} records"
    )
    m = analyze(br, vj)
    STATE.model = m
    ids = sum(m.identifier_lines)
    log(
        f"analysis: {len(m.findings)} findings, {len(m.others)} other matches, {len(m.unclassified)} unclassified, "
        f"{ids} identifier lines, {len(m.actions)} actions"
    )
    os.environ.setdefault("SOURCE_DATE_EPOCH", str(source_epoch(m)))
    STATE.font_dir = font_dir
    register_fonts(font_dir)
    out = (args.out or Path(f"post-boot-log-analysis-{capture_day(m)}.pdf")).resolve()
    if not out.parent.is_dir() or not os.access(out.parent, os.W_OK):
        msg = f"cannot write to {out.parent}"
        raise OSError(msg)
    tmp = out.with_name(f".{out.name}.tmp-{os.getpid()}")
    try:
        with tempfile.TemporaryDirectory(prefix="postboot-report-") as tmp_dir:
            STATE.chart_dir = Path(tmp_dir)
            log(f"{render_figures(m)} figures rendered")
            layout(tmp, m, log)
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    log(f"wrote {out} ({STATE.total} pages, {out.stat().st_size} bytes)")
    return out


def silence(stream: TextIO) -> None:
    """Point a closed pipe's file descriptor at /dev/null so later writes and the final flush succeed."""
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, stream.fileno())
    os.close(devnull)


def main(argv: list[str] | None = None) -> int:
    """Validate the arguments and the environment, then run; Ctrl-C at any point exits 130."""
    parser = build_parser()
    args = _ARGS if argv is None and _ARGS is not None else parser.parse_args(argv)
    if args.out is not None and args.out.is_dir():
        parser.error(f"--out names a directory: {args.out}")
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch is not None and not epoch.strip().isdigit():
        parser.error(f"SOURCE_DATE_EPOCH must be a whole number of seconds, not {epoch!r}")
    signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        return run(args)
    except KeyboardInterrupt:
        print("build_report.py: interrupted; nothing written", file=sys.stderr)
        return EXIT_INTERRUPT


def check(args: argparse.Namespace) -> int:
    """Parse both inputs and run the parser cross-checks; report on stderr and return EXIT_OK."""
    br, vj = parse_bugreport(args.bugreport), parse_verify(args.verify)
    checks = [row for row in cross_checks(analyze(br, vj)) if row[1] == "match" or row[1].startswith("differs")]
    differing = [row for row in checks if row[1] != "match"]
    unread = f", {len(br.journal_unparsed)} journal lines unread" if br.journal_unparsed else ""
    print(
        f"build_report.py: inputs ok ({len(br.sections)} bug-report sections, {len(br.dmesg)} dmesg lines, "
        f"{len(br.journal)} journal entries{unread}; ry-verify {vj.header.get('version')} with {len(vj.items)} "
        f"results; cross-checks: {len(checks) - len(differing)} of {len(checks)} match)",
        file=sys.stderr,
    )
    for name, result in differing:
        print(f"build_report.py: cross-check: {name}: {result}", file=sys.stderr)
    return EXIT_OK


def run(args: argparse.Namespace) -> int:
    """Run the preflight, then the check or the build; the written path goes to stdout."""
    try:
        font_dir = preflight(args.fonts, (args.bugreport, args.verify))
    except PreflightError as exc:
        print(f"build_report.py: {exc}", file=sys.stderr)
        return EXIT_PREFLIGHT
    try:
        if args.check:
            return check(args)
        out = build(args, font_dir, verbose=args.verbose)
    except (OSError, ValueError, RuntimeError, LayoutError, InputError) as exc:
        print(f"build_report.py: {'check' if args.check else 'build'} failed: {exc}", file=sys.stderr)
        return EXIT_FAIL
    try:
        print(out, flush=True)
    except BrokenPipeError:  # the PDF is written; a closed stdout only loses the path line
        silence(sys.stdout)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
