#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["reportlab>=5.0", "matplotlib>=3.11", "svglib>=2.2", "pillow>=12"]
# ///
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Ryan Musante
"""Build the print edition of the GTR9 Pro post-boot log analysis as a PDF.

Sections: SETUP (command line, preflight, build state), CONTENT (report text and
tables), FIGURES (vector charts), LAYOUT (pages), and BUILD (content checks, layout
passes, entry point). Two raster figures ship in assets/.
Exit codes: 0 built or check passed, 1 build failed, 2 usage, 3 preflight failed, 130 interrupted.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, TextIO, TypedDict
from xml.sax.saxutils import escape

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from types import ModuleType

    from matplotlib.axes import Axes
    from matplotlib.figure import Figure
    from reportlab.pdfgen.canvas import Canvas

# ── SETUP ─────────────────────────────────────────────────────────────
# Version, exit codes, paths, command line, preflight, and the shared build state.
__version__ = "5.0.0"
EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_PREFLIGHT, EXIT_INTERRUPT = 0, 1, 2, 3, 130
HERE = Path(__file__).resolve().parent
DEFAULT_OUT = Path("gtr9-postboot-log-analysis-2026-10-02-print.pdf")
FONT_DIRS = (
    Path("/usr/share/fonts/TTF"),
    Path("/usr/share/fonts/truetype/ibm-plex"),
    Path.home() / ".local/share/fonts",
)
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
ASSET_FILES = ("fig03_dmesg.png", "fig09_prevboot.png")
# reportlab is imported at start-up; the preflight covers the modules loaded later
MODULES = {
    "matplotlib": "python-matplotlib",
    "svglib": "python-svglib",
    "PIL": "python-pillow",
}
MAX_PASSES = 6  # layout passes allowed before the page references must have settled
HEADING_TOP_BAND = 70.0  # pt below the frame top within which a section heading opens its page


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser; its epilog lists the exit codes from the EXIT_* constants."""
    parser = argparse.ArgumentParser(
        prog="build_report.py",
        description="Build the GTR9 Pro post-boot log analysis print edition (PDF).",
        epilog=(
            f"Exit codes: {EXIT_OK} built or check passed, {EXIT_FAIL} build failed, "
            f"{EXIT_USAGE} usage, {EXIT_PREFLIGHT} preflight failed, {EXIT_INTERRUPT} interrupted."
        ),
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"output PDF (default: ./{DEFAULT_OUT})")
    parser.add_argument(
        "--fonts", type=Path, help="directory with the IBM Plex TTF files (default: system font directories)"
    )
    parser.add_argument(
        "--assets",
        type=Path,
        default=HERE / "assets",
        help="directory with the two raster figures (default: assets/ beside this script)",
    )
    parser.add_argument("--check", action="store_true", help="run the preflight only; build nothing")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="report the content checks, fonts, figures, each layout pass, and the result on stderr",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


# Parse before importing ReportLab, so --help, --version, and usage errors work without the dependencies.
_ARGS = build_parser().parse_args() if __name__ == "__main__" else None

try:
    from reportlab import rl_config
    from reportlab.graphics import shapes as rl_shapes
    from reportlab.lib import colors
    from reportlab.lib.colors import Color, HexColor
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.pdfmetrics import registerFontFamily, stringWidth
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        BaseDocTemplate,
        CondPageBreak,
        Flowable,
        Frame,
        Image,
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
    """A missing module, font, or asset; maps to exit code 3."""


def preflight(fonts: Path | None, assets: Path) -> tuple[Path, Path]:
    """Return the font and asset directories, or raise PreflightError naming what is missing."""
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
    gone = [f for f in ASSET_FILES if not (assets / f).is_file()]
    if gone:
        msg = f"missing assets in {assets}: {', '.join(gone)}"
        raise PreflightError(msg)
    return font_dir, assets


@dataclass
class BuildState:
    """Mutable state shared by the layout passes, the flowables, and the page callbacks."""

    ref: dict[str, int] = field(default_factory=dict)  # anchor pages from the previous pass
    anchors: dict[str, int] = field(default_factory=dict)  # anchor pages seen in this pass
    h1pos: dict[int, tuple[str, bool]] = field(default_factory=dict)  # page -> (heading, opens the page)
    page_section: dict[int, str] = field(default_factory=dict)  # page -> running-header text
    unresolved: set[str] = field(default_factory=set)  # page references not known in this pass
    total: int = 0
    chart_dir: Path = field(default_factory=Path)
    font_dir: Path = field(default_factory=Path)
    asset_dir: Path = field(default_factory=Path)
    mpl: ModuleType | None = None


STATE = BuildState()


class Card(TypedDict, total=False):
    """Content of a finding card; INFO and closed cards leave out the keys they do not use."""

    id: str
    title: str
    area: str
    cmds: str
    closed: str
    ev: list[str]
    lines: str
    rows: list[tuple[str, str]]
    bullets: list[str]


# ── CONTENT ───────────────────────────────────────────────────────────
# Report text, tables, cards, and captions; edit here, then rebuild. T1-T20, TA1, and TB1 are the report's tables.
REV = "32"
ISSUED = "2026-10-04"
CAP = "2026-10-02"
VERDICT_HEAD = "Healthy — no CRIT, HIGH, or MED findings."
VERDICT_BODY = (
    "Open: one LOW finding (L-3, identifiers in the new captures) and 20 INFO findings that need "
    "no action. "
    "Closed: five LOW findings, confirmed by these captures. ry-verify reports 299 OK, 0 FAIL, 0 "
    "WARN, and 0 GEN_FAIL; "
    "the fix-script `--verify` run exits 0; there is no taint, oops, or splat."
)
KPI_OPEN = [("CRIT", 0), ("HIGH", 0), ("MED", 0), ("LOW", 1), ("INFO", 20), ("WATCH", 2), ("UNKNOWNS", 7)]
KPI_CLOSED = [("LOW", 5), ("UNKNOWNS", 5), ("EVENTS", 1)]
HEALTH = [
    ("RY-VERIFY 7.219.0", "299 OK", "0 FAIL · 0 WARN · 0 GEN_FAIL"),
    ("BOOT TO ROOT MOUNT", "7.22 s", "+1.00 s against 2026-09-27"),
    ("FAILED UNITS", "0 / 0", "system / user"),
    ("KERNEL RING", "Clean", "no taint, oops, or splat"),
    ("NVME (P310 2 TB)", "PASSED", "42.9 °C · 8.92 TB written"),
    ("GPU FIRMWARE", "MES 0x92", "SMU up · DMUB 0x09005300"),
]
DOC_CONTROL = [
    ("Report", "GTR9 Pro Post-Boot Log Analysis (2026-10-02), print edition"),
    ("Revision", f"{REV}, issued {ISSUED}"),
    (
        "Captures",
        "cachyos-bugreport.log, ry-verify 7.219.0 JSONL, gtr9-postboot-fix 2.3.0 `--verify` log; 2026-10-02 18:20 PDT",
    ),
    ("Masking", "Identifiers in quoted lines are masked (L-3)"),
    (
        "This revision",
        (
            "Editorial cleanup and consistent typefaces in tables, cards, and the contents; findings, values, "
            "and evidence unchanged since revision 30."
        ),
    ),
]
GUIDE = [
    (
        "**IDs** — L-n is a LOW finding, I-n an INFO finding, O-n an open action. Every finding has a "
        "card with its evidence and the lines it cites."
    ),
    "**Line prefixes** — BR is cachyos-bugreport.log, VJ the ry-verify JSONL, FL the fix-script log (Appendix B).",
    (
        "**Status** — open items are in Sections 4–9 and 11, closed items in Section 10. Watch and "
        "by-design items are not counted as findings."
    ),
    (
        "**Commands** — Section 11 holds every command, one per line, ready to type in fish; its "
        "checklist tracks the open actions and unknowns."
    ),
    (
        "**Print** — figures encode series with gray levels, outlines, and marker shapes, so they read "
        "on a black-and-white printer."
    ),
]
EXEC_INTRO = [
    (
        "The captures were taken after the 18:19 reboot on 2026-10-02, with every fix from revisions "
        "1–19 in place. The 2026-09-27 captures are no longer on the host; Table 1 keeps their values."
    ),
    (
        "Open: L-3, because 65 lines of the new captures carry identifiers; 20 INFO findings that need "
        "no action; two watch items; and seven unknowns. Closed: L-1, L-2, L-4, L-5, and L-6, five "
        "unknowns, and one event."
    ),
]
KEY_FACTS = [
    (
        "All five fixes hold after a reboot: no NetworkManager P2P warning (L-6), JACK through pw-jack "
        "with no JACK server (L-2), the soft mixer on the POROSVOC card (L-5), the SDL ignore list in "
        "the session (L-1), and only the accepted KWallet portal failure (L-4)."
    ),
    (
        "The L-2 upgrade moved the kernel from 7.2.8-1 to 7.2.8-2-cachyos (clang 23.1.1) and Mesa from "
        "26.2.3 to 26.2.4; the ry-install profile still matches (17/17 checksums, 15/15 kernel tokens "
        "live)."
    ),
    (
        "Firmware: CPU microcode 0x0B700037 (early update), SMU initialized, DMUB 0x09005300, MES "
        "0x00000092, and MT7925 Wi-Fi and Bluetooth firmware built 2026-08-13."
    ),
    (
        "The previous boot ran 2 h 10 min and adds I-17 to I-20: a wireless-extensions warning, a "
        "window that hung on close, a Bluetooth audio device connecting and disconnecting, and Steam's "
        "driver probe in the task manager (Figure 9)."
    ),
    "Idle captures: ry-verify at ≈39 s, the fix script at ≈49 s, and the bug report at ≈55 s after kernel start.",
]
CHANGED_INTRO = (
    "Revision 19 values come from the 2026-09-27 captures. The early boot runs about 1 s later; "
    "0.8 s of that is the gap before the root mount (forced fsck, inferred; Section 9)."
)
T1 = (
    ["Item", "2026-09-27 (revision 19)", "2026-10-02", "Change"],
    [
        ["Kernel", "7.2.8-1-cachyos (clang 22.1.8)", "7.2.8-2-cachyos (clang 23.1.1)", "L-2 upgrade"],
        ["Mesa", "26.2.3", "26.2.4", "L-2 upgrade"],
        ["ry-verify 7.219.0", "299 OK; 0 FAIL, 0 WARN, 0 GEN_FAIL", "299 OK; every section count equal", "none"],
        ["JACK provider", "jack2 1.9.22 (jackd found by inxi)", "pipewire-jack (pw-jack plugin)", "L-2 closed"],
        ["NM P2P warning per boot", "1", "0 in both boots", "L-6 closed"],
        [
            "D-Bus user-unit failures",
            "1 current, 2 previous (names redacted)",
            "1 current (KWallet portal), 2 previous",
            "accepted, L-4 closed",
        ],
        ["Journal entries, current boot", "55", "53", "L-6 warning and I-11 framebuffer line gone"],
        ["Journal entries, previous boot", "87 (2 min 42 s boot)", "114 (2 h 10 min boot)", "longer session"],
        ["Root mounted", "6.22 s", "7.22 s", "+1.00 s"],
        ["No log output before the root mount", "3.06 s", "3.87 s", "+0.81 s, fsck inferred"],
        ["Wi-Fi associated", "13.34 s", "14.30 s", "+0.96 s"],
        ["Greeter / session hand-over", "≈10.4 / ≈17.4 s", "≈10.45 / ≈17.45 s", "none"],
        ["MES firmware", "not logged; 0x91 read on 2026-08-14", "0x00000092 (FL 2)", "unknown closed"],
        ["Failed units", "not captured", "0 system, 0 user (FL 2)", "unknown closed"],
        [
            "NVMe (Crucial P310 2 TB)",
            "39.9 °C; 8.52 TB written; 119 days",
            "42.9 °C; 8.92 TB; 124 days",
            "+0.40 TB in 5 days",
        ],
        ["VRAM / GTT", "32,768 / 48,091 MiB", "32,768 / 48,091 MiB", "none"],
    ],
)
ACTIONS_INTRO = (
    "No INFO or watch item needs action. Table 2 lists the three open actions; Section 11 has "
    "their commands and a checklist."
)
T2 = (
    ["#", "Action", "Why", "How", "Page"],
    [
        [
            "O-1",
            "Redact the 2026-10-02 captures before posting them (L-3)",
            "28 bug-report and 37 JSONL lines carry identifiers",
            "Redact by hand; check with Section 11.1",
            "{p:sub-11.1}",
        ],
        [
            "O-2",
            "Merge the seven .pacnew files listed by the 2026-10-02 upgrade",
            "House rule after every `-Syu`; skip if done since",
            "`sudo pacdiff`",
            "{p:sub-11.2}",
        ],
        [
            "O-3",
            "Hand L-1, L-2, L-5, and L-6 to ry-install 7.224.0",
            "The profile now carries all four; the old drop-ins duplicate them",
            "Deploy, delete two drop-ins, run ry-verify",
            "{p:sub-11.4}",
        ],
    ],
)
T3 = (
    ["File", "Bytes", "Lines", "Content"],
    [
        [
            "cachyos-bugreport.log",
            "147,662",
            "1,885",
            (
                "inxi, sched-ext state, full dmesg, journal of the current and previous boot (warning and "
                "above), package list"
            ),
            "5ee35ae048c94a328a49f45465d35ff3b2b587ab8d1aed335bde4dc3523dea92",
        ],
        [
            "verify-20261002-182017-0700-1942.jsonl",
            "51,436",
            "483",
            "ry-verify 7.219.0 static and runtime passes, profile gtr9_pro",
            "18d25f823a0778419e5026c4f37d61b895110b74d267ba34ec2f714389b82b41",
        ],
        [
            "20261002-182027-3922.log",
            "1,048",
            "11",
            "gtr9-postboot-fix 2.3.0 `--verify`: one record per step, exit status",
            "2160b2adc2d0f6077630d8f8e8f757bd0f0402a7eeca3abc3200432678f99440",
        ],
    ],
)
T4 = (
    ["Event", "Wall clock (PDT)", "Since kernel start"],
    [
        ["Previous boot (journal span)", "16:08:42 → 18:19:29", "ended by reboot at 18:19:28"],
        ["Current boot: kernel start", "≈18:19:38.05 (±0.16 s)", "0 s"],
        ["Greeter starts", "18:19:48", "≈10.45 s"],
        ["Greeter → user session hand-over", "18:19:55", "≈17.45 s"],
        ["ry-verify 7.219.0 run", "18:20:17.118 → 18:20:19.451", "≈39.06 → 41.40 s"],
        ["Fix script 2.3.0 `--verify`", "18:20:27.126 → 18:20:27.294", "≈49.07 → 49.24 s"],
        ["cachyos-bugreport capture", "18:20:33", "≈55.45 s"],
    ],
)
T4_NOTE = (
    "Kernel start is the intersection of the 1 s journal stamps of seven kernel messages with "
    "their dmesg offsets. Journal and bug-report times have 1 s resolution; offsets use the middle "
    "of the second (±0.5 s)."
)
METHOD = [
    (
        "Every JSONL record, every journal entry of both boots, and all 1,359 dmesg lines were read "
        "and classified. The 124 lines matching the failure keyword set (Table 5) and the 205 dmesg "
        "lines matching a wider notice pattern are each attributed (Section 8)."
    ),
    (
        "New messages were traced to their emitting code on 2026-10-02 (Linux v7.2, BlueZ, KWin, "
        "plasma-desktop, pulseaudio-qt, Chromium, CachyOS-Settings); unchanged messages keep revision "
        "19's 2026-09-29 checks. Quoted lines are verbatim apart from masked identifiers and dropped "
        "date and host prefixes; each cites its line (BR, VJ, FL: Appendix B)."
    ),
    (
        "Severity follows the observed impact on this host, not the log level. Commands are "
        "fish-compatible, one per line, and never wrapped."
    ),
]
KEYWORDS = [
    "fail",
    "failed",
    "failure",
    "error",
    "warn",
    "warning",
    "unable",
    "cannot",
    "can't",
    "could not",
    "couldn't",
    "not supported",
    "unsupported",
    "not found",
    "no such",
    "denied",
    "invalid",
    "timeout",
    "timed out",
    "abort",
    "crash",
    "crashed",
    "panic",
    "oops",
    "bug",
    "taint",
    "call trace",
    "segfault",
    "killed",
    "refused",
    "reset",
    "hang",
    "corrupt",
    "mismatch",
    "deprecated",
    "unknown",
    "lacking",
    "kaput",
]
T6 = (
    ["Level / status", "Meaning"],
    [
        ["CRIT", "Data loss, unbootable system, or active security exposure."],
        ["HIGH", "Broken function or hardware at risk."],
        ["MED", "Degraded function or performance with a visible impact."],
        ["LOW", "Limited, conditional, or hygiene impact; action is optional or quick."],
        ["INFO", "Explained and harmless; no action required."],
        ["OPEN", "Present in the 2026-10-02 captures or awaiting an action; Sections 4–9 and 11."],
        ["CLOSED", "Fixed and confirmed by the 2026-10-02 captures, answered, or a normal event; Section 10 only."],
        ["WATCH", "Explained limit kept under observation; open, not counted as a finding."],
        ["BY DESIGN", "Deliberate host choice surfacing in the logs; not counted."],
    ],
)
T7 = (
    ["Check", "Result", "Evidence"],
    [
        ["ry-verify 7.219.0", "PASS, exit 0", "299 OK = 213 static + 86 runtime; 11 INFO notes (Table 14)"],
        ["Fix script `--verify`", "exit 0", "7 steps ok; L-3 had no capture pair yet (FL 5)"],
        ["Managed config, cmdline", "17/17 checksums; 15/15 tokens live", "No kernel parser rejections (VJ 377)"],
        ["Kernel ring buffer", "No taint, oops, or splat", "1,359 lines; 13 keyword and 205 notice lines classified"],
        ["Failed units", "0 system, 0 user", "FL 2"],
        ["CPU vulnerabilities", "None unmitigated", "inxi: 14 not affected, 5 mitigated (BR 50–73)"],
        ["GPU memory", "VRAM 32,768 MiB", "GTT 48,091 MiB, the kernel default (BR 1384)"],
        ["amdgpu firmware", "SMU initialized", "DMUB 0x09005300; VCN ENC 1.24 / DEC 9; MES 0x00000092 (FL 2)"],
        [
            "CPU / GPU policy",
            "amd-pstate-epp, EPP performance; GPU DPM high",
            "Microcode 0x0B700037 (early); governor powersave; dynamic_epp disabled (VJ 392)",
        ],
        ["zswap", "Disabled (readback N)", "Pool initialized by the vendor udev rule (I-1)"],
        ["Temperatures at idle", "CPU 49.6 °C, GPU 46.0 °C", "Board 47.5 °C (BR 163); no fan RPM (I-13)"],
        [
            "NVMe (Crucial P310 2 TB)",
            "SMART PASSED, 42.9 °C",
            "8.92 TB written, +0.40 TB in 5 days; 124 days powered on",
        ],
        ["Wi-Fi (MT7925)", "Associated at 14.30 s", "AP-advertised TX cap 30 dBm (watch)"],
        ["Boot to root mount", "7.22 s", "3.87 s without log output first (Figure 3)"],
    ],
)
REGISTER_INTRO = (
    "The register lists the open LOW finding first, then the INFO findings, which need no action. "
    "Line gives the first evidence line (BR and VJ prefixes: Appendix B); Page gives the finding "
    "card. Closed findings are in Section 10."
)
T8 = [
    (
        "L-3",
        "LOW",
        "Logs carry identifiers the bug-report redactor misses",
        "Privacy / log sharing",
        "BR 8",
        "Redact the Table 9 identifiers before posting",
    ),
    ("I-1", "INFO", "zswap pool initialized although zswap.enabled=0", "Memory / zswap", "BR 1566", "None"),
    (
        "I-2",
        "INFO",
        "bolt does not recognize the Strix Halo USB4 NHIs",
        "USB4 / bolt",
        "BR 1674",
        "None; upstream bolt table entry",
    ),
    (
        "I-3",
        "INFO",
        "amdgpu workqueue name truncated",
        "Kernel / amdgpu DC",
        "BR 1416",
        "None; renamed in Linux 7.3-rc3",
    ),
    ("I-4", "INFO", "MediaTek Bluetooth eSCO quirk notice", "Bluetooth / btusb", "BR 1635", "None"),
    ("I-5", "INFO", "ACP70 finds no ASoC machine driver", "Audio / ACP", "BR 1598", "None"),
    ("I-6", "INFO", "NVMe UUID and SGL notices", "Storage / NVMe", "BR 1335", "None"),
    (
        "I-7",
        "INFO",
        "wpa_supplicant multicast RX registration unsupported",
        "Network / wpa_supplicant",
        "BR 1677",
        "None",
    ),
    ("I-8", "INFO", "TDX message on an AMD CPU", "Kernel / TDX", "BR 650", "None"),
    (
        "I-9",
        "INFO",
        "KDE, portal, and D-Bus startup noise",
        "Desktop / KDE, portal, D-Bus",
        "BR 1676",
        "None; skip the /usr and PAM workarounds",
    ),
    ("I-10", "INFO", "plasmalogin-helper exits with status 255", "Login / plasmalogin", "BR 1721", "None"),
    (
        "I-11",
        "INFO",
        "KWin warning at session hand-over",
        "Desktop / KWin",
        "BR 1684",
        "None unless a login glitch appears",
    ),
    ("I-12", "INFO", "Shutdown-only noise", "Session / shutdown", "BR 1808", "None"),
    (
        "I-13",
        "INFO",
        "Platform visibility gaps (AER, fan RPM) and boilerplate",
        "Platform / ACPI firmware",
        "BR 164",
        "None; note the AER blind spot",
    ),
    (
        "I-14",
        "INFO",
        "inxi mislabels the CPU and GPU generation",
        "Tooling / inxi",
        "BR 35",
        "None; note it when sharing the report",
    ),
    (
        "I-15",
        "INFO",
        "EgisTec EH577 fingerprint reader has no driver",
        "Input / fingerprint",
        "BR 1395",
        "None unless fingerprint login is wanted",
    ),
    ("I-16", "INFO", "Secure Boot disabled", "Boot / Secure Boot", "BR 360", "None; posture note"),
    (
        "I-17",
        "INFO",
        "Wireless-extensions warning from a Chromium-based process",
        "Network / cfg80211",
        "BR 1781",
        "None",
    ),
    ("I-18", "INFO", "KWin discards an unfinished kill prompt", "Desktop / KWin", "BR 1789", "None unless it recurs"),
    (
        "I-19",
        "INFO",
        "Bluetooth audio device connects and disconnects",
        "Bluetooth / BlueZ, pulseaudio-qt",
        "BR 1782",
        "None",
    ),
    (
        "I-20",
        "INFO",
        "Task manager finds no desktop file for gldriverquery",
        "Desktop / task manager",
        "BR 1779",
        "None",
    ),
]
L3: Card = {
    "id": "L-3",
    "title": "Logs carry identifiers the bug-report redactor misses",
    "area": "Privacy / log sharing",
    "cmds": "sub-11.1",
    "ev": [
        "cmdline root=UUID=[root UUID] (4 lines: header, inxi, dmesg ×2)",
        "dmesg systemd[1]: Expecting device /dev/disk/by-uuid/[root UUID]...",
        "dmesg EXT4-fs (nvme0n1p2): mounted filesystem [root UUID] r/w (and re-mounted)",
        "inxi uuid: [DMI UUID] Firmware: UEFI vendor: American",
        "dmesg usb 3-2: SerialNumber: [serial] (Logitech USB Receiver; 3-3 EgisTec EH577, 3-4 POROSVOC)",
        "journal boltd[837]: [<USB4 domain id>-domain0 ...] (4 lines)",
        'journal kded6[1313]: No object for name "bluez_output.[BT MAC].1" (3 lines)',
        "JSONL OK: root=UUID=[root UUID]: present (1 line)",
        "JSONL CHECK_FILE: /home/<user>/.config/environment.d/... (36 lines)",
    ],
    "lines": "BR 8, 17, 28, 306, 516, 1285, 1310, 1346, 1399, 1405, 1459, 1469, 1561, 1674–1675, 1731–1732, "
    "1787, 1791–1792 · VJ 66, 203, 347",
    "rows": [
        (
            "Cause",
            (
                "cachyos-bugreport.sh (CachyOS-Settings) redacts the hostname, user name, home directory, IPv4 "
                "addresses, colon-form MAC addresses, and email-shaped strings. It leaves the DMI system UUID, "
                "USB serial numbers, the root UUID, the USB4 domain ID, and underscore-form Bluetooth "
                "addresses (PipeWire node names). The JSONL embeds the root UUID and home paths."
            ),
        ),
        ("Impact", "Only on sharing: the values tie posted reports to this machine and to a paired Bluetooth device."),
        ("Fix", "Post only copies with the Table 9 identifiers removed; Section 11.1 checks a copy."),
        ("Status", "Open for the 2026-10-02 pair: 28 bug-report and 37 JSONL lines carry identifiers (Table 9)."),
    ],
}
T9 = [
    ("Root UUID, root=UUID= form (header, inxi, dmesg)", "BR 8, 17, 306, 516", "4"),
    ("Root UUID, bare (systemd device, EXT4 mount and re-mount)", "BR 1285, 1469, 1561", "3"),
    ("Root UUID in the JSONL", "VJ 66", "1"),
    ("DMI system UUID (inxi)", "BR 28", "1"),
    (
        "USB serial numbers (3 device-unique)",
        "BR 1125, 1133, 1146, 1154, 1167, 1175, 1188, 1196, 1310, 1346, 1399, 1405, 1459",
        "13",
    ),
    ("USB4 domain ID (bolt)", "BR 1674–1675, 1731–1732", "4"),
    ("Bluetooth address with underscores (pulseaudio-qt)", "BR 1787, 1791–1792", "3"),
    (
        "Home directory (JSONL)",
        (
            "VJ 203–205, 207, 209, 211, 213, 215, 217, 219, 221, 223, 225, 228–230, 232, 234, 236, 238, "
            "240, 242, 244, 246, 248, 250, 252, 254, 256, 258, 260, 262, 264, 266, 346–347"
        ),
        "36",
    ),
    ("Identifier lines in total", "BR 28 · VJ 37", "65"),
]
INFO_INTRO = (
    "No action is needed. I-1 to I-16 recur from 2026-09-27 with the same cause; I-17 to I-20 are "
    "new, from the longer previous boot."
)
EXPLANATION = "Explanation"
INFO: list[Card] = [
    {
        "id": "I-1",
        "ev": ["[ 7.991631] zswap: loaded using pool zstd", "ry-verify: OK: zswap.enabled: N"],
        "lines": "BR 1566 · VJ 398",
        "rows": [
            (
                EXPLANATION,
                (
                    "CachyOS's 30-zram.rules, which also sets vm.swappiness to 150, writes N to zswap's enabled "
                    "parameter when zram0 initializes. In Linux v7.2 that runtime write, with zswap still "
                    "uninitialized because of zswap.enabled=0, runs zswap_setup(), which prints this line, and "
                    "then stores N. zswap stays off (ry-verify readback N; inxi: zswap no), as ry-install intends "
                    "with zram as the swap path; the only cost is the pool allocation."
                ),
            )
        ],
    },
    {
        "id": "I-2",
        "ev": [
            (
                "18:19:47 boltd[837]: [<USB4 domain id>-domain0 ] udev: failed to determine if uid is stable: "
                "unknown NHI PCI id '0x158d'"
            ),
            (
                "18:19:47 boltd[837]: [<USB4 domain id>-domain1 ] udev: failed to determine if uid is stable: "
                "unknown NHI PCI id '0x158e'"
            ),
        ],
        "lines": "BR 1674–1675",
        "rows": [
            (
                EXPLANATION,
                (
                    "bolt keeps a table of known USB4/Thunderbolt native host interface (NHI) IDs to decide "
                    "whether a host UUID survives reboots. Its table (0.9.11) lacks these Strix Halo IDs, so it "
                    "treats the UUID as unstable — the safe default, as native USB4 host UUIDs can change at every "
                    "boot. There is no effect without USB4 or Thunderbolt peripherals that need bolt authorization."
                ),
            )
        ],
    },
    {
        "id": "I-3",
        "ev": ["[ 2.783176] workqueue: name exceeds WQ_NAME_LEN. Truncating to: hdmi_frl_status_polling_workque"],
        "lines": "BR 1416",
        "rows": [
            (
                EXPLANATION,
                (
                    "In v7.2, amdgpu_dm names a workqueue hdmi_frl_status_polling_workqueue, longer than the "
                    "workqueue name limit, so the kernel truncates it once. Linux 7.3-rc3 shortens the name to "
                    "hdmi_frl_status_polling_wq."
                ),
            )
        ],
    },
    {
        "id": "I-4",
        "ev": [
            (
                "[ 8.455021] Bluetooth: hci0: HCI Enhanced Setup Synchronous Connection command is advertised, "
                "but not supported."
            )
        ],
        "lines": "BR 1635",
        "rows": [
            (
                EXPLANATION,
                (
                    "btusb marks the Enhanced Setup Synchronous Connection command broken on every MediaTek "
                    "controller, and the Bluetooth core lists active quirks at setup. Voice links use the legacy "
                    "synchronous connection-oriented (SCO) setup instead; A2DP audio, which the SR-C20A soundbar "
                    "uses, is unaffected."
                ),
            )
        ],
    },
    {
        "id": "I-5",
        "ev": ["[ 8.106935] platform acp_asoc_acp70.0: warning: No matching ASoC machine driver found"],
        "lines": "BR 1598",
        "rows": [
            (
                EXPLANATION,
                (
                    "The Audio Co-Processor (ACP) driver logs this when no ACP machine description (DMIC or I2S "
                    "codec) matches the board. Audio runs through the HDA codec (ALC897) and USB devices."
                ),
            )
        ],
    },
    {
        "id": "I-6",
        "ev": [
            "[ 0.923433] nvme nvme0: passthrough uses implicit buffer lengths",
            "[ 18.614978] block nvme0n1: No UUID available providing old NGUID",
        ],
        "lines": "BR 1335, 1663",
        "rows": [
            (
                EXPLANATION,
                (
                    "The first is an informational notice for a controller without scatter-gather list (SGL) "
                    "support. The second is a one-time warning when userspace reads the namespace uuid attribute "
                    "and the drive exposes only a namespace globally unique identifier (NGUID)."
                ),
            )
        ],
    },
    {
        "id": "I-7",
        "ev": [
            (
                "18:19:48 wpa_supplicant[930]: wlan0: nl80211: kernel reports: multicast RX registrations are "
                "not supported"
            ),
            (
                "18:19:48 wpa_supplicant[930]: p2p-dev-wlan0: nl80211: kernel reports: multicast RX "
                "registrations are not supported"
            ),
        ],
        "lines": "BR 1677–1678",
        "rows": [
            (
                EXPLANATION,
                (
                    "nl80211 rejects multicast management-frame registrations with EOPNOTSUPP when the driver does "
                    "not advertise NL80211_EXT_FEATURE_MULTICAST_REGISTRATIONS; wpa_supplicant logs it and "
                    "continues. The P2P line remains because wpa_supplicant still creates that device, which "
                    "NetworkManager no longer manages (L-6, closed)."
                ),
            )
        ],
    },
    {
        "id": "I-8",
        "ev": ["[ 0.282216] virt/tdx: TDX not supported by the host platform"],
        "lines": "BR 650",
        "rows": [
            (
                EXPLANATION,
                (
                    "The kernel's Intel Trust Domain Extensions (TDX) host code logs this at error level whenever "
                    "the CPU lacks TDX host support — always the case on AMD."
                ),
            )
        ],
    },
    {
        "id": "I-9",
        "ev": [
            (
                "18:19:48 dbus-broker-launch[833]: Activation request for 'org.freedesktop.home1' failed: The "
                "systemd unit 'dbus-org.freedesktop.home1.service' could not be found."
            ),
            (
                "18:19:48 dbus-broker-launch[957]: Service file "
                "'/usr/share/dbus-1/services/org.kde.dolphin.FileManager1.service' is not named after the "
                "D-Bus name 'org.freedesktop.FileManager1'."
            ),
            (
                "18:19:56 ksmserver[1302]: Failed to register with host portal "
                'QDBusError("org.freedesktop.portal.Error.Failed", "Could not register app ID: App info not '
                "found for 'org.kde.ksmserver'\")"
            ),
            (
                "18:19:56 org_kde_powerdevil[1346]: org.kde.powerdevil.chargethresholdhelper.getthreshold "
                'failed "Charge thresholds are not supported by the kernel for this hardware"'
            ),
            (
                "18:19:56 kded6[1304]: Failed enumerating MM objects: "
                '"org.freedesktop.DBus.Error.NameHasNoOwner" "Could not activate remote peer '
                "'org.freedesktop.ModemManager1': activation request failed: unknown unit\""
            ),
        ],
        "lines": "BR 1676, 1679, 1690, 1693, 1701",
        "bullets": [
            (
                "Portal host-registration failures (10 programs) are common on Plasma and had no visible "
                "effect; the circulating workaround adds placeholder .desktop files under "
                "/usr/share/applications, which the no-edits-under-/usr rule excludes."
            ),
            (
                "dbus-broker flags three legacy service-file names; the greeter and the session each run a "
                "broker, so each notice appears twice per boot. The home1 request comes from pam_systemd_home "
                "while systemd-homed is unused; silencing it means editing a pambase-owned file."
            ),
            "ModemManager is not installed, so kded6 cannot enumerate modem objects.",
            (
                "powerdevil probes charge thresholds because the Logitech PRO X 2 exposes a HID++ battery "
                "(inxi: hidpp_battery_0), finds no kernel backlight (DDC/CI is off by design), and cannot "
                "check the screen configuration for button handling."
            ),
            (
                "Also benign: QML override and deprecated-signal warnings, the gtk.portal fallback for "
                "Lockdown, a UPower owner race at greeter start, @DEFAULT_SOURCE@ lookups before WirePlumber "
                "picks a default source, and a screencast request for a closed window (18:18:48, previous "
                "boot)."
            ),
        ],
        "rows": [],
    },
    {
        "id": "I-10",
        "ev": [
            "18:20:01 plasmalogin[920]: Auth: plasmalogin-helper exited with 255",
            "[prev] 16:09:05 plasmalogin[926]: Auth: plasmalogin-helper exited with 255",
        ],
        "lines": "BR 1721, 1778",
        "rows": [
            (
                EXPLANATION,
                (
                    "The greeter's authentication helper exits with 255 when the greeter stops after a successful "
                    "hand-over to the user session; the same message is widely reported on working systems."
                ),
            )
        ],
    },
    {
        "id": "I-11",
        "ev": [
            "18:19:55 kwin_wayland[959]: atomic commit failed: Permission denied",
            "[prev] 16:09:00 kwin_wayland[965]: atomic commit failed: Permission denied",
        ],
        "lines": "BR 1684, 1741",
        "rows": [
            (
                EXPLANATION,
                (
                    "The permission error accompanies each greeter-to-session switch, consistent with the outgoing "
                    "compositor losing DRM master. KDE bugs 521568 and 524540 report the same line at this "
                    "transition, on NVIDIA systems with visible symptoms; here the session starts normally. The "
                    "2026-09-27 framebuffer warning does not recur."
                ),
            )
        ],
    },
    {
        "id": "I-12",
        "ev": [
            (
                "[prev] 18:19:28 plasmalogin[926]: Authentication error: PLASMALOGIN::Auth::ERROR_INTERNAL "
                '"Process crashed"'
            ),
            (
                "[prev] 18:19:28 plasmalogin[926]: Auth: plasmalogin-helper (--socket "
                "/tmp/plasmalogin-auth-[id] --id 4 --start /usr/bin/startplasma-login-wayland --user "
                "plasmalogin --greeter) crashed (signal 15)"
            ),
            (
                "[prev] 18:19:28 systemd[1]: sys-devices-virtual-misc-rfkill.device: Failed to enqueue "
                "SYSTEMD_WANTS job, ignoring: Transaction for systemd-rfkill.socket/start is destructive "
                "(systemd-reboot.service has 'start' job queued, but 'stop' is included in transaction)."
            ),
            (
                "[prev] 18:19:28 systemd[1123]: "
                "sys-devices-pci0000:00-0000:00:08.1-0000:c6:00.6-sound-card0-controlC0.device: Failed to "
                "enqueue SYSTEMD_USER_WANTS job, ignoring: Transaction for sound.target/start is destructive "
                "(exit.target has 'start' job queued, but 'stop' is included in transaction)."
            ),
            "[prev] 18:19:28 plasmashell[1325]: PipeWire remote error: -2 target not found",
            "[prev] 18:19:28 kded6[1313]: context kaput",
            (
                "[prev] 18:19:29 NetworkManager[900]: <warn> [1790990369.0699] dispatcher: (10) failed (after "
                "0.001 sec): Could not activate remote peer 'org.freedesktop.nm_dispatcher': activation "
                "request failed: unit is invalid"
            ),
            "[prev] 18:19:29 systemd[1]: <email-address-redacted>: Failed with result 'exit-code'.",
        ],
        "lines": "BR 1808, 1811, 1817–1819, 1824, 1835, 1839",
        "rows": [
            (
                EXPLANATION,
                (
                    "All occur during the 18:19:28 reboot. The session and greeter helpers, ended by SIGHUP and "
                    "SIGTERM, are reported as crashes (signals 1 and 15). udev events cannot add jobs to the "
                    "shutdown transaction of the system or the user manager. D-Bus activations (bluez, obex, "
                    "UDisks2, NetworkManager dispatcher) are refused while units stop, which NetworkManager logs "
                    "as a failed dispatcher call. PipeWire clients lose their server: Plasma and KWin log remote "
                    "errors, and pulseaudio-qt logs “context kaput” and lookups of vanished nodes. One system unit "
                    "whose name contains @ exits with failure as it stops; the redactor replaced the name (Section "
                    "9)."
                ),
            )
        ],
    },
    {
        "id": "I-13",
        "ev": [
            "inxi: Fan Speeds (rpm): N/A",
            "[ 0.307147] acpi PNP0A08:00: _OSC: platform does not support [SHPCHotplug AER LTR DPC]",
            "[ 0.335423] acpi PNP0C02:01: Could not reserve [mem 0xfec00000-0xfec0ffff]",
            "[ 0.371843] usb usb2: We don't know the algorithms for LPM for this host, disabling LPM.",
            "[ 0.779023] usb 5-1: No LPM exit latency info found, disabling LPM.",
        ],
        "lines": "BR 164, 695, 941, 1128, 1270",
        "rows": [
            (
                EXPLANATION,
                (
                    "Firmware keeps Advanced Error Reporting (AER), Latency Tolerance Reporting (LTR), and "
                    "Downstream Port Containment (DPC) instead of granting them to the OS, and the BIOS has no "
                    "option that hands them over, so the kernel's AER driver cannot report PCIe link errors — a "
                    "clean ring is not proof of a clean link. No fan tachometer is exposed. The USB link power "
                    "management (LPM) lines — four host controllers, and usb 5-1, the USB-C display cable, without "
                    "exit-latency data — and the four reservation overlaps are standard; the ACPI core treats such "
                    "overlaps as usually harmless."
                ),
            )
        ],
    },
    {
        "id": "I-14",
        "ev": [
            "inxi: bits: 64 type: MT MCP arch: Zen 6 note: 6 level: v4 note: check built: 2026+",
            "inxi: process: TSMC n2/n3 (2,3nm) family: 0x1A (26) model-id: 0x70 (112)",
            "inxi: Radeon 8050S 8060S Graphics] driver: amdgpu v: kernel arch: RDNA-3",
            "inxi: code: Phoenix process: TSMC n4 (4nm) built: 2023+ pcie: gen: 4",
        ],
        "lines": "BR 35–36, 76–77",
        "rows": [
            (
                EXPLANATION,
                (
                    "inxi 3.3.41 maps AMD family 0x1A models from 0x50 up to Zen 6, so this model 0x70 CPU reads "
                    "as Zen 6, and it labels the GPU Phoenix with RDNA-3. The Ryzen AI Max+ 395 is Zen 5 (16C/32T) "
                    "with a gfx1151 Radeon 8060S, RDNA 3.5 (Strix Halo)."
                ),
            )
        ],
    },
    {
        "id": "I-15",
        "ev": [
            "[ 2.343811] usb 3-3: New USB device found, idVendor=1c7a, idProduct=0577, bcdDevice=10.41",
            "[ 2.347684] usb 3-3: Product: EgisTec EH577",
        ],
        "lines": "BR 1395, 1397",
        "rows": [
            (
                EXPLANATION,
                (
                    "The sensor (1c7a:0577) enumerates, but no driver binds: libfprint lists it as unsupported, "
                    "and the upstream support request was closed in 2023. The only working path in circulation, "
                    "the eh577-libfprint fork, runs EgisTec's Windows matching engine and replaces the packaged "
                    "libfprint, which does not suit a managed host."
                ),
            )
        ],
    },
    {
        "id": "I-16",
        "ev": ["[ 0.002960] Secure boot disabled"],
        "lines": "BR 360",
        "rows": [
            (EXPLANATION, "UEFI Secure Boot is off, the firmware default; nothing in either capture depends on it.")
        ],
    },
    {
        "id": "I-17",
        "ev": [
            (
                "[prev] 16:09:20 kernel: warning: `ThreadPoolForeg' uses wireless extensions which will stop "
                "working for Wi-Fi 7 hardware; use nl80211"
            )
        ],
        "lines": "BR 1781",
        "rows": [
            (
                EXPLANATION,
                (
                    "Linux v7.2 prints this once per boot (net/wireless/wext-core.c) for the first process that "
                    "uses the legacy wireless-extensions ioctls on a cfg80211 device; mt7925 sets "
                    "WIPHY_FLAG_SUPPORTS_MLO, so the call is refused. ThreadPoolForeg is Chromium's "
                    "ThreadPoolForegroundWorker thread cut to 15 characters; Chromium's network code uses "
                    "SIOCGIWNAME and SIOCGIWESSID to classify an interface as Wi-Fi and read its SSID. Steam, "
                    "whose web helper is Chromium-based, was starting four seconds earlier (I-20) and is the "
                    "likely source. That process cannot read the SSID this way; connectivity is unaffected."
                ),
            )
        ],
    },
    {
        "id": "I-18",
        "ev": [
            (
                "[prev] 16:27:40 kwin_wayland[1210]: QProcess: Destroyed while process "
                '("/usr/lib/kwin_killer_helper") is still running.'
            )
        ],
        "lines": "BR 1789",
        "rows": [
            (
                EXPLANATION,
                (
                    "KWin starts kwin_killer_helper when a window it asked to close stops answering pings, to "
                    "offer to terminate the application, and stops it when the window answers or goes away. Here "
                    "the window went away while the helper still ran, so Qt warned as KWin discarded it: an "
                    "application hung briefly on close at 16:27:40. The warning-level journal does not name it."
                ),
            )
        ],
    },
    {
        "id": "I-19",
        "ev": [
            (
                "[prev] 16:09:54 bluetoothd[840]: profiles/audio/a2dp.c:load_remote_sep() Unable to load "
                "LastUsed: rseid 2 not found"
            ),
            '[prev] 16:09:55 kded6[1313]: No object for name "bluez_output.[BT MAC].1" returning nullptr',
            (
                "[prev] 16:57:32 bluetoothd[840]: src/profile.c:ext_io_disconnected() Unable to get io data "
                "for Hands-Free Voice gateway: getpeername: Transport endpoint is not connected (107)"
            ),
            '[prev] 16:57:32 kded6[1313]: No object for name "@DEFAULT_SINK@" returning nullptr',
        ],
        "lines": "BR 1782, 1787, 1790, 1793",
        "rows": [
            (
                EXPLANATION,
                (
                    "At 16:09:54 bluetoothd found the device's cached LastUsed endpoint pair but not remote "
                    "endpoint 2 among those the device now offered, so it skipped the cached choice (BlueZ "
                    "a2dp.c); the device's audio node appeared a second later. At 16:57:32 the device closed its "
                    "Hands-Free Profile (HFP) connection before bluetoothd read the socket's peer address "
                    "(profile.c ext_io_disconnected; ENOTCONN, 107). Each node change makes pulseaudio-qt, used by "
                    "Plasma's audio applet and kded6, look up nodes that are gone: the device's sink and source, "
                    "the default sink and source, and the null sink. The device is not the SR-C20A that "
                    "reconnect-soundbar manages."
                ),
            )
        ],
    },
    {
        "id": "I-20",
        "ev": [
            '[prev] 16:09:16 plasmashell[1325]: Failed to find service for Unity Launcher "gldriverquery.desktop"',
            '[prev] 16:09:16 plasmashell[1325]: Failed to find service for Unity Launcher "gldriverquery.desktop"',
        ],
        "lines": "BR 1779–1780",
        "rows": [
            (
                EXPLANATION,
                (
                    "Plasma's task manager maps Unity LauncherEntry updates (badges, progress) to desktop files "
                    "(plasma-desktop smartlauncherbackend.cpp). At 16:09:16 two updates named "
                    "gldriverquery.desktop, which does not exist, so they were dropped. gldriverquery is the "
                    "OpenGL driver-query helper Steam runs at start-up."
                ),
            )
        ],
    },
]
WATCH_INTRO = (
    "Watch items are explained limits under observation: open, but not counted. By-design items "
    "are deliberate host choices and are not counted either. The normal reboot event is in Section "
    "10.4."
)
T10 = (
    ["Item", "Evidence", "Watch for", "Then"],
    [
        [
            "Wi-Fi TX power capped at 30 dBm",
            "wlan0: Limiting TX power to 30 (30 - 0) dBm as advertised by the AP (BR 1661)",
            "A lower cap after a router or firmware change, or Wi-Fi range or throughput complaints",
            "Check the router's country and transmit-power settings; the cap follows the AP.",
        ],
        [
            "10 GbE ports down",
            "enp193s0, enp197s0: Link is Down (BR 1645, 1647)",
            "Latency or jitter in online games over Wi-Fi, or a cabled port that stays down",
            "Connect one port to the router (tuning-audit item G-8) and confirm the link comes up.",
        ],
    ],
)
T11 = (
    ["Item", "Evidence", "Line", "Why"],
    [
        ["IPv6 disabled", "IPv6: Loaded, but administratively disabled", "BR 1217", "ipv6.disable=1"],
        [
            "CPU idle limited to C1",
            "ACPI: processor limited to max C-state 1",
            "BR 1108",
            "processor.max_cstate=1; firmware twin: Global C-state Control",
        ],
        ["Sleep states masked", "ry-verify: the five sleep targets inactive", "VJ 423", "Masked by ry-install"],
        [
            "swappiness 150, cache pressure 50, zram 93.93 GiB",
            "inxi Swap block",
            "BR 158",
            (
                "CachyOS-Settings: 30-zram.rules sets swappiness 150 when zram0 starts, "
                "70-cachyos-settings.conf cache pressure 50; not in ry-install's overrides (VJ 160–176)"
            ),
        ],
        ["KWallet disabled", "ksecretd: Lacking a socket, pipe: 0 env: 0", "BR 1717", "kdewallet off; see L-4, closed"],
        [
            "Monitor brightness control off",
            "no kernel backlight interface found; POWERDEVIL_NO_DDCUTIL=1",
            "BR 1692",
            "DDC/CI off by design (VJ 215)",
        ],
        [
            "32 GiB UMA carve-out; GTT at default",
            "32768M of VRAM memory ready · 48091M of GTT memory ready",
            "BR 1383",
            "Permanent choice since 2026-09-06; the small-carve-out recipe is not used",
        ],
        ["fsck forced every boot", "fsck.mode=force: active", "VJ 362", "fsck.mode=force; ≈3.9 s per boot, inferred"],
        [
            "NetworkManager logging and checks",
            "dispatcher LogLevelMax=notice · level=WARN · connectivity enabled=false",
            "VJ 123, 134, 136",
            "Fewer NM journal lines by design; no captive-portal detection",
        ],
    ],
)
COVERAGE_INTRO = "Every stream was read in full; Tables 12–15 and Figures 7–9 show the classification."
T12 = (
    ["Stream", "Size", "Method", "Result"],
    [
        ["inxi and scheduler blocks", "291 lines", "Read in full", "Facts; feeds L-3, I-13, I-14, and the L-2 check"],
        [
            "Kernel ring (dmesg)",
            "1,359 lines",
            "Read line by line",
            "0 unexplained; 205 notice lines in 20 families (Table 13)",
        ],
        [
            "Kernel splat patterns",
            "1,359 lines",
            "Tainted:, Oops, WARNING: CPU, BUG:, Call Trace, general protection fault, kernel taint",
            "0 matches",
        ],
        ["Journal, current boot", "53 entries", "Every entry attributed", "53 / 53 (Table 15)"],
        ["Journal, previous boot", "114 entries", "Every entry attributed", "114 / 114 (Table 15)"],
        ["Package list (cachyos-znver4)", "41 packages", "Read", "Versions only"],
        [
            "ry-verify JSONL",
            "483 records",
            "Every record read",
            "299 OK; 11 INFO notes (Table 14); 0 FAIL, WARN, GEN_FAIL",
        ],
        ["Fix-script log", "11 lines", "Every record read", "7 steps ok, L-3 none; exit 0"],
        [
            "Keyword sweep, whole report",
            "124 lines",
            "38-term set (Table 5)",
            "124 / 124: 109 journal, 13 dmesg, 2 report header",
        ],
    ],
)
T13 = (
    ["Notice family", "Lines", "First", "Disposition"],
    [
        ["Firmware memory map, ACPI table reservations", "80", "BR 307", "Boilerplate"],
        ["PNP0C02 resource reservations", "51", "BR 905", "I-13 for the four overlaps; the rest boilerplate"],
        ["CPU vulnerability mitigations", "10", "BR 568", "Posture: none unmitigated (Table 7)"],
        ["USB link power management", "5", "BR 1128", "I-13"],
        ["xHCI quirk masks", "4", "BR 1117", "Boilerplate"],
        ["amdgpu optional features", "7", "BR 1375", "Boilerplate: optional firmware absent; no runtime PM on an APU"],
        ["systemd unmet conditions", "18", "BR 1292", "Boilerplate: no measured boot, no hibernation"],
        ["Audit subsystem off", "3", "BR 636", "Boilerplate"],
        ["Kernel command line", "2", "BR 306", "By design: ry-install profile"],
        ["Kernel and PCI notices", "17", "BR 334", "Boilerplate"],
        ["Secure Boot state", "1", "BR 360", "I-16"],
        ["CPU idle and IPv6 policy", "2", "BR 1108", "By design (Table 11)"],
        ["Previous reset reason", "1", "BR 1219", "Closed event (Table 19)"],
        ["TDX probe", "1", "BR 650", "I-8"],
        ["Workqueue name truncated", "1", "BR 1416", "I-3"],
        ["ACP machine driver", "1", "BR 1598", "I-5"],
        ["USB mic volume range", "1", "BR 1628", "L-5 (closed)"],
        ["Bluetooth eSCO quirk", "1", "BR 1635", "I-4"],
        ["10 GbE links down", "2", "BR 1645", "Watch (Table 10)"],
        ["Wi-Fi TX power cap", "1", "BR 1661", "Watch (Table 10)"],
    ],
)
T14 = (
    ["VJ", "Note", "Reading"],
    [
        ["301", "No IgnorePkg set", "No package is held back from upgrades."],
        [
            "349",
            "no .ry.bak copies (no run has rewritten a boot file or fstab, or they were removed by hand)",
            "No pending backups of boot files or fstab.",
        ],
        ["382", "Checking cpu0 (representative)", "CPU policy checks read cpu0 as representative."],
        ["407", "No module_blacklist= entry in KERNEL_PARAMS", "No module blacklisted on the kernel command line."],
        ["432", "NM Wi-Fi radio: enabled", "Wi-Fi radio on."],
        [
            "434",
            "firewall posture: ufw=inactive nft_rules=5",
            "nftables ruleset active, input policy drop (VJ 195); ufw unused.",
        ],
        ["458", "root filesystem: ext4", "As expected."],
        [
            "470",
            "/boot/loader/loader.conf: skipped (vfat — unix perms synthesized from mount options)",
            "vfat has no Unix permissions; expected for /boot.",
        ],
        [
            "472",
            "1 file(s) skipped on boot partition (vfat or undetermined fstype — unix perms not verifiable)",
            "vfat has no Unix permissions; expected for /boot.",
        ],
        [
            "474",
            "/boot/loader: skipped (vfat — unix perms synthesized from mount options)",
            "vfat has no Unix permissions; expected for /boot.",
        ],
        [
            "476",
            "1 dir(s) skipped on boot partition (vfat or undetermined fstype — unix perms not verifiable)",
            "vfat has no Unix permissions; expected for /boot.",
        ],
    ],
)
ATTR_INTRO = "Shaded rows are closed findings whose lines still print as expected (L-4 accepted, L-5 kernel warning)."
T15 = [
    ("L-4", "D-Bus-activated user units exit with failure after login", "CLOSED", 2, 3),
    ("L-5", "USB microphone hardware gain spans under 1 dB", "CLOSED", 2, 2),
    ("L-6", "NetworkManager warns on the Wi-Fi P2P device at every boot", "CLOSED", 0, 0),
    ("I-2", "bolt does not recognize the Strix Halo USB4 NHIs", "OPEN", 2, 2),
    ("I-3", "amdgpu workqueue name truncated", "OPEN", 1, 1),
    ("I-4", "MediaTek Bluetooth eSCO quirk notice", "OPEN", 1, 1),
    ("I-5", "ACP70 finds no ASoC machine driver", "OPEN", 1, 1),
    ("I-6", "NVMe UUID and SGL notices", "OPEN", 1, 1),
    ("I-7", "wpa_supplicant multicast RX registration unsupported", "OPEN", 2, 2),
    ("I-8", "TDX message on an AMD CPU", "OPEN", 1, 1),
    ("I-9", "KDE, portal, and D-Bus startup noise", "OPEN", 38, 39),
    ("I-10", "plasmalogin-helper exits with status 255", "OPEN", 1, 1),
    ("I-11", "KWin warning at session hand-over", "OPEN", 1, 1),
    ("I-12", "Shutdown-only noise", "OPEN", 0, 32),
    ("I-17", "Wireless-extensions warning from a Chromium-based process", "OPEN", 0, 1),
    ("I-18", "KWin discards an unfinished kill prompt", "OPEN", 0, 1),
    ("I-19", "Bluetooth audio device connects and disconnects", "OPEN", 0, 23),
    ("I-20", "Task manager finds no desktop file for gldriverquery", "OPEN", 0, 2),
]
UNKNOWN_INTRO = (
    "What these captures cannot show, and how to close each item. The five unknowns of revision 19 "
    "that have since been answered are in Section 10.3."
)
T16 = (
    ["Item", "Why it matters", "How to close"],
    [
        [
            "Idle capture",
            (
                "Both capture sets are from idle, ≈39–55 s after kernel start: no GPU reset, thermal, or "
                "frametime data under load."
            ),
            (
                "Capture again after a gaming or LLM session; `ry-dashboard --log` records CPU and GPU "
                "temperature, clock, load, and power to CSV meanwhile."
            ),
        ],
        [
            "Native AER unavailable",
            "Firmware withholds AER (I-13), so PCIe link errors are not reported.",
            "Account for it when diagnosing PCIe devices.",
        ],
        [
            "Redacted shutdown unit",
            "A system unit with @ in its name failed as it stopped at 18:19:29 (I-12).",
            "Section 11.3, first command, while the 18:19 boot is current.",
        ],
        [
            "Unnamed D-Bus failure, previous boot",
            "The 16:10:00 failure (L-4) is unnamed; the fix script reads only the current boot.",
            "Section 11.3, second command; expected: KSplash.",
        ],
        [
            "Window that hung on close",
            "I-18 does not name the application.",
            "Only if it recurs: note what was closed.",
        ],
        [
            "Bluetooth device",
            "The I-19 device is not the SR-C20A; the capture shows only its address.",
            "Section 11.3, third command.",
        ],
        [
            "Gap before the root mount",
            "Grew from 3.06 s to 3.87 s; inferred to be the forced fsck, which neither capture times.",
            "Section 11.3, fourth command.",
        ],
    ],
)
CLOSED_INTRO = "Fixed and confirmed, answered, or normal; kept for the record. Nothing here needs action."
T17 = [
    (
        "L-4",
        "D-Bus-activated user units exit with failure after login",
        "2026-09-30",
        "FL 3: only the KWallet portal failed; 0 failed units",
        "none needed",
    ),
    (
        "L-1",
        "Keychron Link receiver exposes a HID joystick interface",
        "2026-10-02",
        "FL 4: both pairs in the session (session=active)",
        "ry-install 7.224.0",
    ),
    (
        "L-5",
        "USB microphone hardware gain spans under 1 dB",
        "2026-09-30",
        "FL 6: soft_mixer=true",
        "ry-install 7.224.0",
    ),
    (
        "L-6",
        "NetworkManager warns on the Wi-Fi P2P device at every boot",
        "2026-10-01",
        "FL 7: unmanaged, 0 warnings; no warning in either boot",
        "ry-install 7.224.0",
    ),
    (
        "L-2",
        "JACK clients bypassed PipeWire (jack2 provided libjack)",
        "2026-10-02",
        "FL 8: pipewire-jack; inxi lists pw-jack, no JACK server",
        "ry-install 7.224.0",
    ),
]
CLOSED: list[Card] = [
    {
        "id": "L-4",
        "closed": "2026-09-30",
        "area": "Session / systemd --user",
        "ev": [
            (
                "fix log: fix=L-4 state=ok failed=0 journal_failures=1 "
                "units=dbus-:1.2-org.freedesktop.impl.portal.desktop.kwallet@0.service other="
            ),
            "18:19:56 ksecretd[1572]: Lacking a socket, pipe: 0 env: 0",
            "18:19:56 systemd[1116]: dbus-:<email-address-redacted>: Failed with result 'exit-code'.",
            "[prev] 16:10:00 systemd[1123]: dbus-:<email-address-redacted>: Failed with result 'exit-code'.",
        ],
        "lines": "BR 1717–1718, 1788 · FL 3",
        "rows": [
            (
                "Was",
                (
                    "D-Bus-activated user units exited with failure after login; the bug report's email filter hid "
                    "their names."
                ),
            ),
            (
                "Fix in place",
                (
                    "None needed: kdewallet is disabled by design, so the KWallet portal and KSplash fail on "
                    "start; the script reads their names from the user journal. Accepted 2026-09-30."
                ),
            ),
            (
                "Confirmed",
                (
                    "One failure this boot, the KWallet portal, and no failed unit left (FL 3). The previous boot "
                    "shows the pair at 16:09:01 and 16:10:00; Section 9 checks the second name."
                ),
            ),
        ],
    },
    {
        "id": "L-1",
        "closed": "2026-10-02",
        "area": "Input / HID",
        "ev": [
            (
                "fix log: fix=L-1 state=ok pairs=0x3434/0x0e20,0x3434/0xd030 "
                "global=0x3434/0x0e20,0x3434/0xd030 file=ours session=active"
            ),
            (
                "[ 0.900672] hid-generic 0003:3434:D030.0004: input,hiddev97,hidraw3: USB HID v1.11 Joystick "
                "[Keychron Keychron Link ] on usb-0000:c8:00.0-1/input1"
            ),
        ],
        "lines": "BR 1328 · FL 4",
        "rows": [
            (
                "Was",
                (
                    "The Keychron Link receiver (3434:d030) exposes interface 1 as a HID joystick that Proton and "
                    "SDL games can take for a controller, which stops mouse input."
                ),
            ),
            (
                "Fix in place",
                (
                    "~/.config/environment.d/60-sdl-ignore.conf sets "
                    "SDL_GAMECONTROLLER_IGNORE_DEVICES=0x3434/0x0e20,0x3434/0xd030 for the session (2026-09-30)."
                ),
            ),
            (
                "Confirmed",
                (
                    "Both pairs reach the session (session=active, FL 4), so Steam inherits them. Steam Input must "
                    "be off per game, and the proton-cachyos sdlinput and wayland modes need "
                    "PROTON_NO_STEAMINPUT=0."
                ),
            ),
            (
                "Profile",
                (
                    "ry-install 7.224.0 sets the pair in ~/.config/environment.d/10-environment.conf, and "
                    "ry-verify checks it; delete 60-sdl-ignore.conf afterwards (O-3)."
                ),
            ),
        ],
    },
    {
        "id": "L-5",
        "closed": "2026-09-30",
        "area": "Audio / USB mic",
        "ev": [
            "fix log: fix=L-5 state=ok soft_mixer=true",
            "[ 8.200254] usb 3-4: Warning! Unlikely small volume range (=100), linear volume or custom curve?",
            "[ 8.200257] usb 3-4: [12] FU [Mic Capture Volume] ch = 1, val = 0/100/1",
        ],
        "lines": "BR 1628–1629 · FL 6",
        "rows": [
            (
                "Was",
                (
                    "The POROSVOC card (1d6b:a4a7) reports Mic Capture Volume as 0–100 in 1/256 dB, about 0.39 dB "
                    "in total."
                ),
            ),
            (
                "Fix in place",
                (
                    "WirePlumber rule 51-porosvoc-softmixer.conf sets api.alsa.soft-mixer = true for "
                    "alsa_card.usb-POROSVOC* (2026-09-30)."
                ),
            ),
            (
                "Confirmed",
                (
                    "soft_mixer=true (FL 6). The kernel warning still prints twice per boot because the driver "
                    "registers the hardware control; this is expected."
                ),
            ),
            (
                "Profile",
                (
                    "ry-install 7.224.0 manages this path (mode 0600, its own text), and ry-verify reads the "
                    "property through pactl (O-3)."
                ),
            ),
        ],
    },
    {
        "id": "L-6",
        "closed": "2026-10-01",
        "area": "Network / NetworkManager",
        "ev": ["fix log: fix=L-6 state=ok nm_state=unmanaged warnings=0"],
        "lines": "FL 7",
        "rows": [
            (
                "Was",
                (
                    "NetworkManager 1.54 set IPv4 forwarding on p2p-dev-wlan0, which has no kernel netdev, and "
                    "logged ENOENT at every boot."
                ),
            ),
            (
                "Fix in place",
                (
                    "/etc/NetworkManager/conf.d/90-no-p2p.conf unmanages type:wifi-p2p devices (2026-09-30); Wi-Fi "
                    "Direct and Miracast are off."
                ),
            ),
            (
                "Confirmed",
                (
                    "Unmanaged with 0 warnings (FL 7); no forwarding warning in either boot, against one per boot "
                    "on 2026-09-27."
                ),
            ),
            (
                "Profile",
                (
                    "ry-install 7.224.0 adds [device-no-p2p] to 99-cachyos-nm.conf, and ry-verify checks it; "
                    "delete 90-no-p2p.conf afterwards (O-3)."
                ),
            ),
        ],
    },
    {
        "id": "L-2",
        "closed": "2026-10-02",
        "area": "Audio / JACK",
        "ev": [
            "fix log: fix=L-2 state=ok installed=pipewire-jack",
            "inxi: Server-2: PipeWire v: 1.6.9 status: n/a (root, process) with:",
            "inxi: 3: pipewire-alsa type: plugin 4: pw-jack type: plugin",
        ],
        "lines": "BR 119, 121 · FL 8",
        "rows": [
            ("Was", "jack2 provided libjack, and inxi found a JACK server (jackd), so JACK clients bypassed PipeWire."),
            (
                "Fix in place",
                "pipewire-jack replaced jack2 1.9.22-2.1 on 2026-10-02 in a 14 s unattended upgrade (script 2.3.0).",
            ),
            ("Confirmed", "pipewire-jack installed (FL 8); inxi lists pw-jack under PipeWire and no JACK server."),
            (
                "Profile",
                "ry-install 7.224.0 declares pipewire-jack in PKGS_ADD; pacman's conflict keeps jack2 out (O-3).",
            ),
        ],
    },
]
T18 = (
    ["Unknown in revision 19", "Answer", "Evidence", "Closed"],
    [
        ["Failed-unit state not captured", "0 failed system units, 0 failed user units", "FL 2", "2026-10-02"],
        ["MES firmware not logged", "0x00000092, newer than the 0x91 reading of 2026-08-14", "FL 2", "2026-09-30"],
        [
            "L-2 not confirmed by pacman",
            "pacman listed jack2 1.9.22-2.1 alone; now pipewire-jack",
            "FL 8",
            "2026-10-01",
        ],
        ["Stray .ry.orig not visible", "The root sweep of /etc finds none", "FL 2, FL 9 (scope=root)", "2026-10-02"],
        ["L-1 session check pending", "The drop-in reached the session", "FL 4 (session=active)", "2026-10-02"],
    ],
)
T19 = (
    ["Event", "Evidence", "Line", "Status"],
    [
        [
            "Previous boot ended by reboot",
            "software wrote 0x6 to reset control register 0xCF9",
            "BR 1219",
            "Closed; normal 18:19:28 reboot (shutdown lines: I-12)",
        ]
    ],
)
IMPL_INTRO = (
    "When each step from revisions 1–19 was done, and with which script version. Step 4 covered "
    "the 2026-09-27 pair; L-3 is open for the new pair."
)
T20 = (
    ["Step", "ID", "Action", "Done", "Script", "Result"],
    [
        [
            "1",
            "BASE",
            "Baseline checks",
            "2026-09-30; root sweep 2026-10-02",
            "2.0.x, 2.3.0",
            "0 failed units, MES 0x00000092, no stray .ry.orig",
        ],
        ["2", "L-4", "Identify the failing D-Bus units", "2026-09-30", "2.0.1", "KWallet portal and KSplash, accepted"],
        ["3", "L-1", "Session-wide SDL drop-in", "2026-09-30", "2.0.x", "session=active on 2026-10-02"],
        [
            "4",
            "L-3",
            "-public copies of the 2026-09-27 pair",
            "2026-09-30",
            "2.0.x",
            "Superseded; the 2026-10-02 pair is open, Section 5",
        ],
        ["5", "L-5", "WirePlumber soft-mixer rule", "2026-09-30", "2.0.x", "soft_mixer=true"],
        ["6", "L-6", "NetworkManager P2P drop-in", "2026-09-30", "2.0.x", "0 warnings since the 2026-10-01 boot"],
        ["7", "L-2", "pipewire-jack inside a full -Syu", "2026-10-02", "2.3.0", "jack2 out in 14 s, third attempt"],
        ["—", "TIDY", "Delete leftovers of earlier runs", "2026-10-02", "2.3.0", "old=0, ry_orig=0"],
    ],
)
CHECKLIST = [
    ("O-1", "Redact the 2026-10-02 captures (Section 11.1)", "All six checks print nothing"),
    ("O-2", "Merge the .pacnew files (Section 11.2)", "pacdiff lists nothing left to merge"),
    ("O-3", "Hand over to ry-install 7.224.0 (Section 11.4)", "PASS; ry-verify shows no FAIL after a reboot"),
    ("U", "Close the open unknowns (Section 11.3)", "Unit name, D-Bus unit, device name, fsck timing noted"),
]
S111 = (
    (
        "Post only copies with the Table 9 identifiers removed, saved as bugreport-public.log and "
        "verify-public.jsonl. Check them from the directory that holds them:"
    ),
    "MANUAL CROSS-CHECK",
    [
        "rg -c 'uuid: [[:xdigit:]]{8}-|root=UUID=[[:xdigit:]]' bugreport-public.log verify-public.jsonl",
        "rg -c 'SerialNumber: [^\\[\\s]|[[:xdigit:]]{8}-[[:xdigit:]]{4}-domain' bugreport-public.log",
        "rg -c '([[:xdigit:]]{2}_){5}[[:xdigit:]]{2}' bugreport-public.log verify-public.jsonl",
        "rg -o -m 1 -r '$1' 'root=UUID=(\\S{36})' cachyos-bugreport.log | rg -c -F -f - bugreport-public.log",
        "rg -o -m 1 -r '$1' 'root=UUID=(\\S{36})' cachyos-bugreport.log | rg -c -F -f - verify-public.jsonl",
        'rg -c -F "$HOME" bugreport-public.log verify-public.jsonl',
    ],
    "Expected: no output from any of them; rg exits 1 when nothing matches.",
)
S112 = (
    None,
    "APPLY",
    ["sudo pacdiff"],
    "Expected: the seven files the 2026-10-02 upgrade listed; nothing to do if they were merged since.",
)
S113 = (
    None,
    "CHECK",
    [
        "journalctl -b -1 _PID=1 -p warning -o cat | rg 'Failed with result'",
        "journalctl --user -b -1 -o cat -u 'dbus-*'",
        "bluetoothctl devices",
        "journalctl -b -o short-monotonic -u systemd-fsck-root.service",
    ],
    (
        "Expected, while the 18:19 boot is current (-b -1 is relative): the templated unit's name "
        "(I-12); the 16:10:00 D-Bus failure, likely KSplash; paired Bluetooth devices by name (I-19); "
        "and the root fsck's start and finish, whose difference measures the gap in Table 1."
    ),
)
S114 = (
    (
        "After O-1 and O-2. ry-install 7.224.0 carries L-1 (ENV_VARS), L-2 (pipewire-jack in "
        "PKGS_ADD), L-5 (the same WirePlumber path, mode 0600), and L-6 ([device-no-p2p] in "
        "99-cachyos-nm.conf); ry-verify checks all four."
    ),
    "HAND OVER",
    [
        "cd ~/ry-install",
        "./ry-install.fish",
        "rm ~/.config/environment.d/60-sdl-ignore.conf",
        "sudo rm /etc/NetworkManager/conf.d/90-no-p2p.conf",
        "./ry-verify.fish --verify",
    ],
    (
        "Expected: ry-install ends PASS or PASS-WITH-WARNINGS; after the next boot and login, "
        "ry-verify reports no FAIL. The fix script is retired: running it again would undo the profile."
    ),
)
TA1 = [
    ("Machine", "Beelink GTR9 Pro (DMI: AZW GTR Pro); BIOS GTRPRPI1001C (2026-05-12); AGESA StrixHaloPI-FP11 1.0.0.1c"),
    ("CPU", "AMD Ryzen AI Max+ 395, 16C/32T; microcode 0x0B700037 (early update from 0x0B700034)"),
    ("GPU", "Radeon 8060S (1002:1586, gfx1151); VRAM 32,768 MiB; GTT 48,091 MiB"),
    ("Memory", "96 GiB outside the carve-out, 93.93 GiB usable; zram swap 93.93 GiB (zstd)"),
    ("Storage", "Crucial P310 2 TB (CT2000P310SSD8, firmware V8CR001); ext4 root (43.4% used), vfat /boot"),
    ("Network", "MediaTek MT7925 Wi-Fi 7 (mt7925e) and Bluetooth 5.4 (13d3:3604); 2 × Realtek RTL8127 10 GbE (r8169)"),
    ("Audio", "HDA ALC897; POROSVOC USB audio; AMD ACP (no machine driver); PipeWire 1.6.9 with pw-jack"),
    (
        "Input",
        (
            "Keychron K2 HE (3434:0e20), Keychron Link receiver (3434:d030), Logitech Lightspeed receiver "
            "(046d:c54d) with a PRO X 2 mouse, EgisTec EH577 reader (no driver)"
        ),
    ),
    ("Display", "BenQ PD3420Q, 3440 × 1440 at 60 Hz on DP-2 through an Anker USB-C to HDMI cable (usb 5-1)"),
    (
        "Kernel",
        (
            "7.2.8-2-cachyos (clang 23.1.1); sched-ext disabled; fallback linux-cachyos-lts 6.18.52; both "
            "newer than 6.18.4, the toolboxes' validated kernel"
        ),
    ),
    ("Userspace", "systemd 262 · KDE Plasma 6.7.5 (Wayland) · Mesa 26.2.4 · PipeWire 1.6.9 · inxi 3.3.41"),
    ("Profile", "ry-verify 7.219.0, profile gtr9_pro; gtr9-postboot-fix 2.3.0 at capture, retired since"),
]
TB1 = [
    ("A2DP", "Advanced Audio Distribution Profile (Bluetooth stereo audio)"),
    ("ACP", "Audio Co-Processor, the AMD audio block"),
    ("ACPI", "Advanced Configuration and Power Interface"),
    ("AER", "Advanced Error Reporting (PCI Express)"),
    ("AGESA", "AMD Generic Encapsulated Software Architecture, the platform firmware base"),
    ("AP", "Wi-Fi access point"),
    ("ASoC", "ALSA System on Chip, the Linux audio layer for on-chip audio"),
    ("BR", "Line number in cachyos-bugreport.log"),
    ("DC", "Display Core, the amdgpu display driver"),
    ("DDC/CI", "Display Data Channel Command Interface (monitor control)"),
    ("DMI", "Desktop Management Interface; the SMBIOS identity data"),
    ("DMIC", "Digital microphone"),
    ("DMUB", "AMD display microcontroller firmware"),
    ("DPC", "Downstream Port Containment (PCI Express)"),
    ("DPM", "Dynamic Power Management (amdgpu)"),
    ("DRM", "Direct Rendering Manager, the kernel graphics subsystem"),
    ("EPP", "Energy Performance Preference (amd-pstate)"),
    ("eSCO", "Enhanced Synchronous Connection-Oriented link (Bluetooth voice)"),
    ("FL", "Line number in the fix-script log"),
    ("GTT", "Graphics Translation Table; system memory the GPU can map"),
    ("HCI", "Host Controller Interface (Bluetooth)"),
    ("HDA", "High Definition Audio"),
    ("HFP", "Hands-Free Profile (Bluetooth voice)"),
    ("HID", "Human Interface Device (USB input class)"),
    ("I2S", "Inter-IC Sound, a serial audio bus"),
    ("JACK", "JACK Audio Connection Kit, a low-latency audio server and API"),
    ("JSONL", "JSON Lines: one JSON object per line"),
    ("L-n, I-n", "LOW and INFO finding identifiers"),
    ("LPM", "Link Power Management (USB)"),
    ("LTR", "Latency Tolerance Reporting (PCI Express)"),
    ("MES", "MicroEngine Scheduler, AMD GPU firmware that schedules queues"),
    ("MLO", "Multi-Link Operation (Wi-Fi 7)"),
    ("NGUID", "Namespace Globally Unique Identifier (NVMe)"),
    ("NHI", "Native Host Interface, the USB4 host controller function"),
    ("NM", "NetworkManager"),
    ("NVMe", "Non-Volatile Memory Express, the SSD interface"),
    ("O-n", "Open action identifiers (Table 2)"),
    ("P2P", "Peer-to-peer; here Wi-Fi Direct"),
    ("PAM", "Pluggable Authentication Modules"),
    ("PCIe", "PCI Express"),
    ("PDT", "Pacific Daylight Time (UTC−7)"),
    ("QML", "Qt Modeling Language, used by KDE interfaces"),
    ("RDNA", "AMD GPU architecture family"),
    ("SCO", "Synchronous Connection-Oriented link (Bluetooth voice)"),
    ("SDL", "Simple DirectMedia Layer, the input and media library many games use"),
    ("SGL", "Scatter-Gather List (NVMe data transfer)"),
    ("SMART", "Self-Monitoring, Analysis and Reporting Technology"),
    ("SMBIOS", "System Management BIOS, the firmware identity tables"),
    ("SMU", "System Management Unit, AMD power-management firmware"),
    ("SSID", "Service Set Identifier, a Wi-Fi network name"),
    ("TDX", "Intel Trust Domain Extensions"),
    ("TX", "Transmit"),
    ("UEFI", "Unified Extensible Firmware Interface"),
    ("UMA", "Unified Memory Architecture; here the fixed VRAM carve-out"),
    ("UUID", "Universally Unique Identifier"),
    ("VCN", "Video Core Next, the AMD video codec engine"),
    ("VJ", "Line number in the ry-verify JSONL"),
    ("VRAM", "Video memory; here the UMA carve-out"),
    ("wext", "Wireless Extensions, the legacy Wi-Fi ioctl interface"),
]
CAPTIONS = {
    1: "Open and closed items, 2026-10-02. Left: the open LOW finding, the INFO findings, the watch "
    "items, and the open unknowns. Right: closed LOW findings with their closing dates, the "
    "answered unknowns, and the normal reboot.",
    2: "Boot milestones, 2026-09-27 against 2026-10-02 (Table 1). The root mount and Wi-Fi "
    "association move about 1 s later; the shaded box is the stretch without log output before the "
    "root mount, inferred to be the forced fsck. Hollow markers: 2026-09-27; filled: 2026-10-02.",
    3: "Current boot. Top: dmesg lines per 0.5 s (log scale); the empty stretch is the 3.87 s without "
    "output before the root mount (forced fsck in the initrd, inferred). Bottom: phase edges from "
    "dmesg (systemd in the initrd at 0.79 s, after switch-root at 7.65 s) and the journal (greeter "
    "≈10.45 s, session ≈17.45 s); capture points from the JSONL, the fix-script log, and the bug "
    "report.",
    4: "ry-verify 7.219.0 results by section. Static phase left, runtime right; dark bars are OK "
    "checks, outlined bars INFO notes (Table 14). Section counts exclude each phase's summary "
    "lines and match the 2026-09-27 run.",
    5: "Open INFO findings by area (Table 8) against the journal entries they explain in both boots "
    "(Table 15). Desktop and shutdown noise make up 114 of the 158 entries; five areas surface "
    "only in dmesg or inxi.",
    6: "Identifier lines to redact before posting, by class (Table 9). Home-directory paths in the "
    "JSONL account for 36 of the 65 lines.",
    7: "dmesg notice lines by family (Table 13), shaded by disposition. Boilerplate dominates; every "
    "finding-linked family is one to five lines.",
    8: "Journal entries per finding (Table 15). Left: current boot, 2026-09-27 (light) against "
    "2026-10-02 (dark); L-6 drops to 0 and I-11 loses its framebuffer warning, and the rest recur "
    "at the same rate. Right: the 114 entries of the previous boot.",
    9: "Previous boot, 16:08:42–18:19:29: journal entries by finding over time. Startup notices "
    "cluster at 16:08–16:10; I-18 is at 16:27:40, I-19 at 16:09:54 and 16:57:32, and I-12 is the "
    "18:19:28 reboot.",
    10: "Fix history from Tables 17 and 20: when each fix was applied and when the captures confirmed "
    "it. L-3's earlier copies covered the 2026-09-27 pair; the 2026-10-02 pair is open.",
}


# ── FIGURES ───────────────────────────────────────────────────────────
# Vector charts: matplotlib with text as paths, embedded through svglib; grayscale only.
C_INK, C_DARK, C_MID, C_LIGHT, C_PALE = "#1d1d1d", "#3c3c3c", "#8a8a8a", "#c9c9c9", "#ececec"
CW = 7.1  # chart width in inches: the 512 pt text frame
MPL_FONTS = (
    "IBMPlexSans-Regular",
    "IBMPlexSans-SemiBold",
    "IBMPlexSans-Medium",
    "IBMPlexSansCondensed-Regular",
    "IBMPlexSansCondensed-SemiBold",
)
MPL_STYLE: dict[str, Any] = {
    "font.family": "IBM Plex Sans",
    "font.size": 7.6,
    "svg.fonttype": "path",
    "svg.hashsalt": "gtr9-postboot",
    "axes.linewidth": 0.6,
    "axes.edgecolor": "#3a3a3a",
    "xtick.major.width": 0.5,
    "ytick.major.width": 0,
    "xtick.major.size": 2.5,
    "axes.labelcolor": "#222",
    "xtick.color": "#333",
    "ytick.color": "#222",
    "axes.titlesize": 8.2,
    "axes.titleweight": "semibold",
    "axes.titlelocation": "left",
    "legend.frameon": False,
    "legend.fontsize": 7.2,
}


class Lane(NamedTuple):
    """One capture date in Figure 2: lane height, quiet gap, milestones, and approximate labels."""

    name: str
    y: int
    gap: tuple[float, float]
    root: float
    greet: float
    wifi: float
    sess: float
    greet_label: str
    sess_label: str


BOOT_LANES = (
    Lane("2026-09-27\n(revision 19)", 1, (6.22 - 3.06, 6.22), 6.22, 10.4, 13.34, 17.4, "≈10.4", "≈17.4"),
    Lane("2026-10-02", 0, (3.35, 7.22), 7.22, 10.45, 14.30, 17.45, "≈10.45", "≈17.45"),
)
BOOT_DELTAS = ((6.22, 7.22, 6.72, "+1.00 s"), (13.34, 14.30, 13.82, "+0.96 s"))  # from, to, label x, label
# Figure 4: (section, OK checks, INFO notes) as in the revision-28 figure
FIG_VERIFY_STATIC = (
    ("Boot configuration", 65, 0),
    ("System configuration", 48, 0),
    ("User configuration", 32, 0),
    ("Packages", 29, 1),
    ("Services", 11, 0),
    ("Syntax validation", 11, 0),
    ("Checksum verification", 17, 1),
)
FIG_VERIFY_RUNTIME = (
    ("Kernel cmdline", 17, 0),
    ("Hardware state", 11, 1),
    ("Module state", 9, 1),
    ("Service state", 19, 0),
    ("Wi-Fi state", 3, 2),
    ("Environment state", 24, 1),
    ("File permissions", 3, 4),
)
# Figure 6: (class, bug-report lines, JSONL lines), largest first
FIG_IDENT = (
    ("Home directory (JSONL)", 0, 36),
    ("USB serial numbers", 13, 0),
    ("Root UUID, root=UUID= form", 4, 0),
    ("USB4 domain ID", 4, 0),
    ("Root UUID, bare", 3, 0),
    ("Bluetooth address, underscore form", 3, 0),
    ("DMI system UUID", 1, 0),
    ("Root UUID in the JSONL", 0, 1),
)
# Figure 7: (Table 13 family, lines, disposition key, tag), largest first
FIG_FAMILIES = (
    ("Firmware memory map, ACPI table reservations", 80, "B", ""),
    ("PNP0C02 resource reservations", 51, "B", "4 overlaps: I-13"),
    ("systemd unmet conditions", 18, "B", ""),
    ("Kernel and PCI notices", 17, "B", ""),
    ("CPU vulnerability mitigations", 10, "P", "posture"),
    ("amdgpu optional features", 7, "B", ""),
    ("USB link power management", 5, "I", "I-13"),
    ("xHCI quirk masks", 4, "B", ""),
    ("Audit subsystem off", 3, "B", ""),
    ("Kernel command line", 2, "D", "by design"),
    ("CPU idle and IPv6 policy", 2, "D", "by design"),
    ("10 GbE links down", 2, "W", "watch"),
    ("Secure Boot state", 1, "I", "I-16"),
    ("Previous reset reason", 1, "C", "closed event"),
    ("TDX probe", 1, "I", "I-8"),
    ("Workqueue name truncated", 1, "I", "I-3"),
    ("ACP machine driver", 1, "I", "I-5"),
    ("USB mic volume range", 1, "C", "L-5, closed"),
    ("Bluetooth eSCO quirk", 1, "I", "I-4"),
    ("Wi-Fi TX power cap", 1, "W", "watch"),
)
FAMILY_STYLES: dict[str, dict[str, Any]] = {
    "B": {"color": C_LIGHT},
    "P": {"color": C_MID},
    "I": {"color": C_DARK},
    "D": {"facecolor": "white", "edgecolor": C_INK, "lw": 0.8},
    "W": {"facecolor": "white", "edgecolor": C_INK, "lw": 0.8, "ls": (0, (2, 1.5))},
    "C": {"facecolor": C_PALE, "edgecolor": C_INK, "lw": 0.6},
}
FAMILY_NAMES = {"B": "boilerplate", "P": "posture", "I": "INFO finding", "D": "by design", "W": "watch", "C": "closed"}
R19_EXTRA = {"L-6": 1, "I-11": 1}  # current-boot lines of 2026-09-27 gone by 2026-10-02 (Table 1)
# Figure 10: (finding, fix applied, closed or None, note)
FIG_FIXES = (
    ("L-4", "2026-09-30", "2026-09-30", "accepted"),
    ("L-1", "2026-09-30", "2026-10-02", "session=active"),
    ("L-5", "2026-09-30", "2026-09-30", "soft_mixer=true"),
    ("L-6", "2026-09-30", "2026-10-01", "0 warnings"),
    ("L-2", "2026-10-02", "2026-10-02", "pipewire-jack"),
    ("L-3", "2026-09-30", None, "new pair open (Section 5)"),
)


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
    for side in ("top", "right"):
        ax.spines[side].set_visible(b=False)
    ax.spines["left"].set_visible(b=False)
    if grid:
        ax.xaxis.grid(visible=True, color="#dcdcdc", lw=0.5)
        ax.set_axisbelow(b=True)


def save(fig: Figure, name: str) -> None:
    """Write one figure as SVG into the build's chart directory and close it."""
    fig.savefig(STATE.chart_dir / name, format="svg", bbox_inches="tight", pad_inches=0.04, metadata={"Date": None})
    _mpl().close(fig)


def fig_boot() -> None:
    """Draw Figure 2: boot milestones on both capture dates (Tables 1 and 4)."""
    plt = _mpl()
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch, Rectangle

    fig, ax = plt.subplots(figsize=(CW, 1.85))
    for lane in BOOT_LANES:
        y = lane.y
        ax.plot([0, 19.6], [y, y], color=C_LIGHT, lw=1.2, zorder=1)
        width = lane.gap[1] - lane.gap[0]
        ax.add_patch(
            Rectangle((lane.gap[0], y - 0.17), width, 0.34, facecolor=C_LIGHT, edgecolor=C_DARK, lw=0.6, zorder=2)
        )
        mid = (lane.gap[0] + lane.gap[1]) / 2
        ax.text(mid, y, f"no output {width:.2f} s", ha="center", va="center", fontsize=6.2, color=C_INK, zorder=3)
        marks = ((lane.root, "v", None), (lane.greet, "o", lane.greet_label))
        marks += ((lane.wifi, "s", None), (lane.sess, "D", lane.sess_label))
        for x, marker, label in marks:
            face = C_INK if y == 0 else "white"
            ax.plot([x], [y], marker=marker, ms=5.2, color=C_INK, mfc=face, mew=0.9, zorder=4, ls="none")
            ty, va = (y + 0.24, "bottom") if y else (y - 0.24, "top")
            ax.text(x, ty, label or f"{x:.2f}", ha="center", va=va, fontsize=6.6, color=C_INK)
        ax.text(-0.35, y, lane.name, ha="right", va="center", fontsize=7.2, color=C_INK, linespacing=1.05)
    for x, label in ((0.79, "systemd\nin initrd"), (7.65, "switch-\nroot")):
        ax.plot([x, x], [-0.16, 0.16], color=C_INK, lw=0.9, zorder=3)
        ax.text(x, -0.62, label, ha="center", va="top", fontsize=6.0, color=C_MID, linespacing=0.95)
    for start, end, label_x, label in BOOT_DELTAS:
        arrow = {"arrowstyle": "->", "lw": 0.7, "color": C_DARK}
        ax.annotate("", xy=(end, 0.5), xytext=(start, 0.5), arrowprops=arrow)
        ax.text(label_x, 0.55, label, ha="center", va="bottom", fontsize=6.4, color=C_DARK)
    ax.set_xlim(0, 19.6)
    ax.set_ylim(-1.05, 1.72)
    ax.set_yticks([])
    ax.set_xticks(range(0, 20, 2))
    ax.set_xlabel("Seconds since kernel start", fontsize=7.2)
    clean(ax)
    handles = [
        Line2D([], [], marker="v", ls="none", color=C_INK, ms=5, label="root mounted"),
        Line2D([], [], marker="o", ls="none", color=C_INK, ms=5, label="greeter starts"),
        Line2D([], [], marker="s", ls="none", color=C_INK, ms=5, label="Wi-Fi associated"),
        Line2D([], [], marker="D", ls="none", color=C_INK, ms=4.5, label="session hand-over"),
        Patch(facecolor=C_LIGHT, edgecolor=C_DARK, label="no log output (forced fsck, inferred)"),
    ]
    ax.legend(
        handles=handles, loc="upper center", bbox_to_anchor=(0.5, 1.2), ncol=5, handletextpad=0.4, columnspacing=1.2
    )
    save(fig, "fig02_boot.svg")


def fig_verify() -> None:
    """Draw Figure 4: ry-verify 7.219.0 OK checks and INFO notes per section, static and runtime."""
    plt = _mpl()
    from matplotlib.patches import Patch

    fig, axs = plt.subplots(1, 2, figsize=(CW, 2.05), gridspec_kw={"wspace": 0.62})
    panels = ((axs[0], FIG_VERIFY_STATIC, "Static"), (axs[1], FIG_VERIFY_RUNTIME, "Runtime"))
    for ax, data, phase in panels:
        names = [d[0] for d in data][::-1]
        ok = [d[1] for d in data][::-1]
        info = [d[2] for d in data][::-1]
        ax.barh(names, ok, color=C_DARK, height=0.62)
        ax.barh(names, info, left=ok, color="white", edgecolor=C_INK, lw=0.7, height=0.62)
        for i, (o, f) in enumerate(zip(ok, info, strict=True)):
            ax.text(o + f + 1.2, i, f"{o}" + (f" + {f}" if f else ""), va="center", fontsize=6.8)
        ax.set_xlim(0, 75)
        ax.set_title(f"{phase}: {sum(ok)} OK, {sum(info)} INFO")
        clean(ax)
        ax.tick_params(axis="y", length=0)
    handles = [Patch(color=C_DARK, label="OK checks"), Patch(facecolor="white", edgecolor=C_INK, label="INFO notes")]
    axs[1].legend(handles=handles, loc="lower right")
    save(fig, "fig04_verify.svg")


def area_rows() -> list[tuple[str, int, str, int]]:
    """Return (area, findings, IDs, journal entries in both boots) per area from Tables 8 and 15, busiest first."""
    journal = {r[0]: r[3] + r[4] for r in T15}
    areas: dict[str, list[str]] = {}
    for fid, sev, _finding, area, _line, _action in T8:
        if sev == "INFO":
            areas.setdefault(area.split(" / ")[0], []).append(fid)
    order = {name: i for i, name in enumerate(areas)}
    rows = [(name, len(ids), ", ".join(ids), sum(journal.get(i, 0) for i in ids)) for name, ids in areas.items()]
    return sorted(rows, key=lambda r: (-r[3], -r[1], order[r[0]]))


def fig_areas() -> None:
    """Draw Figure 5: open INFO findings per area beside the journal entries they explain."""
    plt = _mpl()
    rows = area_rows()[::-1]
    fig, axs = plt.subplots(
        1, 2, figsize=(CW, 2.75), sharey=True, gridspec_kw={"width_ratios": [1, 1.25], "wspace": 0.08}
    )
    names = [r[0] for r in rows]
    axs[0].barh(names, [r[1] for r in rows], color=C_MID, height=0.6)
    for i, r in enumerate(rows):
        axs[0].text(r[1] + 0.08, i, r[2], va="center", fontsize=6.4, color=C_INK)
    axs[0].set_xlim(0, 7.2)
    axs[0].set_xticks([0, 1, 2, 3, 4])
    axs[0].set_title(f"Open INFO findings ({sum(r[1] for r in rows)})")
    axs[1].barh(names, [r[3] for r in rows], color=C_DARK, height=0.6)
    for i, r in enumerate(rows):
        text, color = (str(r[3]), C_INK) if r[3] else ("— not in the journal", C_MID)
        axs[1].text(r[3] + 1, i, text, va="center", fontsize=6.4, color=color)
    axs[1].set_xlim(0, 100)
    axs[1].set_title(f"Journal entries, both boots ({sum(r[3] for r in rows)})")
    for ax in axs:
        clean(ax)
        ax.tick_params(axis="y", length=0)
    save(fig, "fig05_areas.svg")


def fig_ident() -> None:
    """Draw Figure 6: identifier lines to redact before posting, by class and source (Table 9)."""
    plt = _mpl()
    rows = FIG_IDENT[::-1]
    fig, ax = plt.subplots(figsize=(CW, 2.0))
    names = [r[0] for r in rows]
    br = [r[1] for r in rows]
    vj = [r[2] for r in rows]
    ax.barh(names, br, color=C_DARK, height=0.6, label="cachyos-bugreport.log (BR)")
    ax.barh(names, vj, left=br, color="white", edgecolor=C_INK, lw=0.7, height=0.6, label="ry-verify JSONL (VJ)")
    for i, (b, v) in enumerate(zip(br, vj, strict=True)):
        ax.text(b + v + 0.5, i, str(b + v), va="center", fontsize=6.8)
    ax.set_xlim(0, 42)
    ax.set_xlabel("Lines to redact before posting", fontsize=7.2)
    clean(ax)
    ax.tick_params(axis="y", length=0)
    title = f"{sum(br) + sum(vj)} lines: BR {sum(br)} · VJ {sum(vj)}"
    ax.legend(
        loc="lower right", title=title, title_fontproperties={"weight": "semibold", "size": 7.2}, alignment="left"
    )
    save(fig, "fig06_ident.svg")


def fig_families() -> None:
    """Draw Figure 7: dmesg notice lines per family, shaded by disposition (Table 13)."""
    plt = _mpl()
    from matplotlib.patches import Patch

    rows = FIG_FAMILIES[::-1]
    fig, ax = plt.subplots(figsize=(CW, 3.55))
    for i, (_name, value, key, tag) in enumerate(rows):
        ax.barh(i, value, height=0.62, **FAMILY_STYLES[key])
        ax.text(value + 0.8, i, f"{value}" + (f"   {tag}" if tag else ""), va="center", fontsize=6.6)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r[0] for r in rows])
    ax.set_xlim(0, 92)
    ax.set_xlabel("dmesg notice lines", fontsize=7.2)
    clean(ax)
    ax.tick_params(axis="y", length=0)
    handles = [Patch(label=FAMILY_NAMES[k], **FAMILY_STYLES[k]) for k in "BPIDWC"]
    ax.legend(handles=handles, loc="lower right", ncol=2)
    save(fig, "fig07_families.svg")


def journal_series() -> tuple[list[str], list[int], list[int], list[int]]:
    """Return finding IDs with current-boot (2026-09-27, 2026-10-02) and previous-boot counts from Table 15."""
    ids = [r[0] for r in T15]
    cur = [r[3] for r in T15]
    r19 = [c + R19_EXTRA.get(fid, 0) for fid, c in zip(ids, cur, strict=True)]
    prev = [r[4] for r in T15]
    return ids, r19, cur, prev


def fig_journal() -> None:
    """Draw Figure 8: journal entries per finding, current boot on both dates and the previous boot."""
    plt = _mpl()
    ids, r19, cur, prev = journal_series()
    fig, axs = plt.subplots(1, 2, figsize=(CW, 3.2), sharey=True, gridspec_kw={"wspace": 0.1})
    y = list(range(len(ids)))[::-1]
    h = 0.36
    axs[0].barh([v + h / 2 for v in y], r19, height=h, color=C_LIGHT, label=f"2026-09-27 ({sum(r19)})")
    axs[0].barh([v - h / 2 for v in y], cur, height=h, color=C_DARK, label=f"2026-10-02 ({sum(cur)})")
    for v, a, b in zip(y, r19, cur, strict=True):
        if a or b:
            text = f"{a} → {b}" if a != b else f"{b}"
            weight = "semibold" if a != b else "normal"
            axs[0].text(max(a, b) + 0.6, v, text, va="center", fontsize=6.4, fontweight=weight)
    axs[0].set_title("Current boot, both capture dates")
    axs[0].set_xlim(0, 46)
    axs[0].legend(loc="lower right")
    axs[1].barh(y, prev, height=0.6, color=C_MID)
    for v, p in zip(y, prev, strict=True):
        if p:
            axs[1].text(p + 0.6, v, str(p), va="center", fontsize=6.4)
    axs[1].set_title(f"Previous boot, 2026-10-02 ({sum(prev)})")
    axs[1].set_xlim(0, 46)
    axs[0].set_yticks(y)
    axs[0].set_yticklabels(ids)
    for ax in axs:
        clean(ax)
        ax.tick_params(axis="y", length=0)
    for ax in axs:
        ax.set_xlabel("Journal entries", fontsize=7.2)
    save(fig, "fig08_journal.svg")


def day(iso: str) -> dt.datetime:
    """Return midnight UTC of an ISO date."""
    return dt.datetime.fromisoformat(f"{iso}T00:00:00+00:00")


def fig_fixes() -> None:
    """Draw Figure 10: when each fix was applied and when the captures confirmed it (Tables 17 and 20)."""
    plt = _mpl()
    from matplotlib.lines import Line2D

    fig, ax = plt.subplots(figsize=(CW, 1.95))
    note_x = day("2026-10-03") + dt.timedelta(hours=8)
    open_x = day("2026-10-02") + dt.timedelta(hours=18.3)
    for i, (_fid, applied, closed, note) in enumerate(FIG_FIXES[::-1]):
        if closed:
            ax.plot([day(applied), day(closed)], [i, i], color=C_DARK, lw=1.4, zorder=2)
            ax.plot([day(applied)], [i], marker="o", ms=5, color=C_INK, zorder=3)
            ax.plot([day(closed)], [i], marker="s", ms=5.2, color=C_INK, zorder=4)
        else:
            ax.plot([day(applied)], [i], marker="o", ms=5, color=C_INK, zorder=3)
            ax.plot([day(applied), open_x], [i, i], color=C_DARK, lw=1.0, ls=(0, (2, 2)), zorder=2)
            ax.plot([open_x], [i], marker="D", ms=5, mfc="white", mec=C_INK, mew=0.9, zorder=4)
        ax.text(note_x, i, note, va="center", fontsize=6.6)
    captures = (
        (day("2026-09-27"), "2026-09-27 captures\n(revision 19)"),
        (day("2026-10-02") + dt.timedelta(hours=18.33), "2026-10-02 18:20\ncaptures"),
    )
    for x, label in captures:
        ax.axvline(x, color=C_MID, lw=0.7, ls=(0, (3, 2)), zorder=1)
        top = len(FIG_FIXES) - 0.35
        ax.text(x, top, label, ha="center", va="bottom", fontsize=6.2, color=C_DARK, linespacing=1.0)
    ax.set_yticks(range(len(FIG_FIXES)))
    ax.set_yticklabels([r[0] for r in FIG_FIXES[::-1]])
    ax.set_xlim(day("2026-09-26") + dt.timedelta(hours=12), day("2026-10-05") + dt.timedelta(hours=12))
    ax.set_ylim(-0.6, len(FIG_FIXES) + 0.5)
    ax.set_xticks([day(f"2026-09-{d}") for d in (27, 28, 29, 30)] + [day(f"2026-10-0{d}") for d in (1, 2, 3)])
    ax.set_xticklabels(["09-27", "09-28", "09-29", "09-30", "10-01", "10-02", "10-03"])
    clean(ax)
    ax.tick_params(axis="y", length=0)
    handles = [
        Line2D([], [], marker="o", ls="none", color=C_INK, ms=5, label="fix applied"),
        Line2D([], [], marker="s", ls="none", color=C_INK, ms=5, label="confirmed, closed"),
        Line2D([], [], marker="D", ls="none", mfc="white", mec=C_INK, ms=5, label="open"),
    ]
    ax.legend(handles=handles, loc="lower left", ncol=3, bbox_to_anchor=(0.0, -0.42))
    save(fig, "fig10_fixes.svg")


FIGURES = (fig_boot, fig_verify, fig_areas, fig_ident, fig_families, fig_journal, fig_fixes)


# ── LAYOUT ────────────────────────────────────────────────────────────
# ReportLab platypus: styles, flowables, sections, page templates.
INK, MUTE, RULE, LIGHT, ZEBRA, DARK, MID = (
    HexColor(x) for x in ("#1b1b1b", "#5a5a5a", "#a3a3a3", "#e8e8e8", "#f4f4f4", "#2d2d2d", "#8a8a8a")
)
PW, PH = letter
LM = RM = 50
TOPM, BOTM = 58, 54
FW = PW - LM - RM
FIGURE_TITLES = (
    "Open and closed items",
    "Boot milestones, both captures",
    "Current boot: dmesg, phases, captures",
    "ry-verify results by section",
    "Open INFO findings by area",
    "Identifier lines by class",
    "dmesg notice lines by family",
    "Journal entries per finding",
    "Previous boot timeline",
    "Fix history",
)


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
sty_table_title = style(
    "ttl", fontName="Plex-SB", fontSize=8.1, leading=10.8, spaceBefore=7, spaceAfter=3.5, keepWithNext=1
)
sty_caption = style("cap", fontName="Plex-It", fontSize=7.5, leading=10, textColor=MUTE, spaceBefore=3, spaceAfter=11)
sty_note = style("note", fontName="Plex-It", fontSize=7.4, leading=9.8, textColor=MUTE, spaceBefore=3, spaceAfter=8)
sty_th = style("th", fontName="PlexC-SB", fontSize=7.6, leading=9.5)
sty_td = style("td", fontName="PlexC", fontSize=7.8, leading=9.9)
sty_td_right = style("tdr", parent=sty_td, alignment=TA_RIGHT)
sty_td_mono = style("tdm", fontName="PlexM", fontSize=6.9, leading=9.1)
sty_code = style("code", fontName="PlexM", fontSize=7.3, leading=10.6)
sty_label = style("lab", fontName="PlexC-SB", fontSize=7.4, leading=9.6, textColor=MUTE)
sty_card_value = style("cv", parent=sty_td, fontSize=8.0, leading=10.6)


def page_of(key: str) -> str:
    """Return an anchor's page from the previous pass; until it is known, '?' (recorded as unresolved)."""
    if key in STATE.ref:
        return str(STATE.ref[key])
    STATE.unresolved.add(key)
    return "?"


def pref(key: str) -> str:
    """Return a linked "p. N" reference to an anchor."""
    return f'<a href="#{key}" color="#1b1b1b">p.\u00a0{page_of(key)}</a>'


def fmt(text: str, st: ParagraphStyle | None = None) -> str:
    """Escape text for a Paragraph and apply the house markup: `code`, **bold**, and {p:key} page links."""
    out = escape(text)
    mono_size = (getattr(st, "fontSize", 9) if st else 9) - 0.7
    out = re.sub(r"`([^`]+)`", lambda m: f'<font name="PlexM" size="{mono_size:.1f}">{m.group(1)}</font>', out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", out)
    return re.sub(r"\{p:([^}]+)\}", lambda m: pref(m.group(1)), out)


def para(text: str, st: ParagraphStyle = sty_body) -> Paragraph:
    """Return a Paragraph from house-markup text."""
    return Paragraph(fmt(text, st), st)


STRUCT = (
    ("sec-1", 0, "1", "Executive summary"),
    ("sub-1.1", 1, "1.1", "Key facts"),
    ("sub-1.2", 1, "1.2", "What changed since 2026-09-27"),
    ("sub-1.3", 1, "1.3", "Open actions"),
    ("sec-2", 0, "2", "Scope and method"),
    ("sub-2.1", 1, "2.1", "Inputs"),
    ("sub-2.2", 1, "2.2", "Capture window"),
    ("sub-2.3", 1, "2.3", "Method"),
    ("sub-2.4", 1, "2.4", "Severity and status"),
    ("sec-3", 0, "3", "System health"),
    ("sec-4", 0, "4", "Open findings register"),
    ("sec-5", 0, "5", "Open LOW finding"),
    ("sec-6", 0, "6", "Open INFO findings"),
    ("sec-7", 0, "7", "Open watch and by-design items"),
    ("sec-8", 0, "8", "Coverage and double-check"),
    ("sub-8.1", 1, "8.1", "Journal attribution by finding"),
    ("sec-9", 0, "9", "Open unknowns and risks"),
    ("sec-10", 0, "10", "Closed"),
    ("sub-10.1", 1, "10.1", "Closed findings register"),
    ("sub-10.2", 1, "10.2", "Closed finding cards"),
    ("sub-10.3", 1, "10.3", "Closed unknowns"),
    ("sub-10.4", 1, "10.4", "Closed events"),
    ("sub-10.5", 1, "10.5", "Implementation record"),
    ("sec-11", 0, "11", "Open actions"),
    ("sub-11.1", 1, "11.1", "Redact the captures before posting (O-1)"),
    ("sub-11.2", 1, "11.2", "Merge the .pacnew files (O-2)"),
    ("sub-11.3", 1, "11.3", "Close the open unknowns"),
    ("sub-11.4", 1, "11.4", "Hand over to ry-install 7.224.0 (O-3)"),
    ("sub-11.5", 1, "11.5", "Checklist"),
    ("sec-A", 0, "A", "Environment snapshot"),
    ("sec-B", 0, "B", "Abbreviations"),
)
SD = {key: (level, number, title) for key, level, number, title in STRUCT}


class HPara(Paragraph):
    """Numbered section heading that records its page, bookmark, and outline entry."""

    def __init__(self, key: str) -> None:
        """Compose the heading for a STRUCT key."""
        level, number, title = SD[key]
        self.key, self.level, self.toc = key, level, f"{number}  {title}"
        if level == 0:
            num = f'<font name="Plex-Md" color="#8a8a8a">{number}</font>\u2002'
        else:
            num = f'<font color="#5a5a5a">{number}</font>\u2002'
        super().__init__(f'<a name="{key}"/>{num}{escape(title)}', sty_h1 if level == 0 else sty_h2)

    def draw(self) -> None:
        """Record the page, add the bookmark and outline entry, and draw; level-0 headings get a rule."""
        canvas = self.canv
        STATE.anchors[self.key] = canvas.getPageNumber()
        canvas.bookmarkPage(self.key)
        canvas.addOutlineEntry(self.toc, self.key, level=self.level, closed=False)
        super().draw()
        if self.level == 0:
            canvas.setStrokeColor(INK)
            canvas.setLineWidth(1.1)
            canvas.line(0, -3.5, FW, -3.5)


class Anchor(Flowable):
    """Zero-size flowable marking a link target, optionally with an outline entry."""

    def __init__(self, key: str, outline: str | None = None, level: int = 1, *, keep_with_next: bool = False) -> None:
        """Store the target; keep_with_next binds the anchor to the flowable after it."""
        super().__init__()
        self.key, self.outline, self.level = key, outline, level
        self.width = self.height = 0
        if keep_with_next:
            self.keepWithNext = 1

    def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:  # noqa: ARG002, N803
        """Take no space."""
        return 0, 0

    def draw(self) -> None:
        """Record the page and add the bookmark, plus the outline entry when one is set."""
        canvas = self.canv
        STATE.anchors[self.key] = canvas.getPageNumber()
        canvas.bookmarkPage(self.key)
        if self.outline:
            canvas.addOutlineEntry(self.outline, self.key, level=self.level, closed=True)


TABLE_STYLE = (
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("LINEABOVE", (0, 0), (-1, 0), 1.0, INK),
    ("LINEBELOW", (0, 0), (-1, 0), 0.6, INK),
    ("LINEBELOW", (0, 1), (-1, -1), 0.25, RULE),
    ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ZEBRA]),
    ("LEFTPADDING", (0, 0), (-1, -1), 3.6),
    ("RIGHTPADDING", (0, 0), (-1, -1), 3.6),
    ("TOPPADDING", (0, 0), (-1, -1), 2.6),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 2.9),
    ("LINEBELOW", (0, -1), (-1, -1), 0.8, INK),
)
Cell = tuple[int, int]


def cell(value: object, column: int, mono: Sequence[int], right: Sequence[int]) -> Flowable:
    """Return a table cell: flowables pass through; text becomes a Paragraph in the column's style."""
    if isinstance(value, Flowable):
        return value
    if column in mono:
        return Paragraph(fmt(str(value), sty_td_mono), sty_td_mono)
    return Paragraph(fmt(str(value), sty_td), sty_td_right if column in right else sty_td)


def table(
    head: Sequence[str],
    rows: Sequence[Sequence[object]],
    widths: Sequence[float],
    *,
    num: int | str | None = None,
    title: str = "",
    mono: Sequence[int] = (),
    right: Sequence[int] = (),
    bold_last: bool = False,
    shade: Sequence[int] = (),
    spans: Sequence[tuple[Cell, Cell]] = (),
) -> list[Flowable]:
    """Return a house table (optional numbered title, ruled zebra grid, repeated header) and a spacer.

    Raise ValueError when the column widths do not fill the text frame or a row has the wrong number of cells.
    """
    label = f"Table {num}" if num is not None else "untitled table"
    if len(widths) != len(head) or abs(sum(widths) - FW) > 0.5:
        msg = f"{label}: {len(widths)} widths summing to {sum(widths):g} pt for {len(head)} columns, frame {FW:g} pt"
        raise ValueError(msg)
    if any(len(row) != len(head) for row in rows):
        msg = f"{label}: every row needs {len(head)} cells"
        raise ValueError(msg)
    data: list[list[Flowable]] = [[Paragraph(fmt(h, sty_th), sty_th) for h in head]]
    data += [[cell(value, j, mono, right) for j, value in enumerate(row)] for row in rows]
    grid = Table(data, colWidths=list(widths), repeatRows=1, hAlign="LEFT")
    commands: list[tuple[object, ...]] = list(TABLE_STYLE)
    commands += [("BACKGROUND", (0, r), (-1, r), LIGHT) for r in shade]
    commands += [("SPAN", *span) for span in spans]
    if bold_last:
        commands.append(("LINEABOVE", (0, -1), (-1, -1), 0.6, INK))
    grid.setStyle(TableStyle(commands))
    out: list[Flowable] = []
    if num is not None:
        out.append(Anchor(f"tab-{num}", keep_with_next=True))
        out.append(Paragraph(f'<font color="#5a5a5a">Table {num}</font>\u2002{escape(title)}', sty_table_title))
    return [*out, grid, Spacer(1, 6)]


CHIP_COLORS = {
    "LOW": (INK, colors.white, INK),
    "INFO": (LIGHT, INK, MID),
    "OPEN": (colors.white, INK, INK),
    "CLOSED": (DARK, colors.white, DARK),
}


def chip_width(text: str) -> float:
    """Return a chip's width: its label in PlexC SemiBold 6.8 plus 9 pt of padding."""
    return stringWidth(text, "PlexC-SB", 6.8) + 9


def chip(text: str) -> Table:
    """Return a small bordered label (LOW, INFO, OPEN, CLOSED) as a one-cell table."""
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
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 1.4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return box


ZERO_PAD = (
    ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ("TOPPADDING", (0, 0), (-1, -1), 0),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
)


def card_header(fid: str, title: str, kind: str) -> Table:
    """Return a card's header row: monospace ID, title, and severity and status chips."""
    dark = kind == "closed"
    fg = "#ffffff" if dark else "#1b1b1b"
    idp = Paragraph(
        f'<a name="card-{fid}"/><font name="PlexM-SB" size="11" color="{fg}">{fid}</font>', style("cid", leading=13)
    )
    tp = Paragraph(f'<font name="Plex-SB" size="9.4" color="{fg}">{escape(title)}</font>', style("ctt", leading=12))
    labels = ("INFO" if kind == "info" else "LOW", "CLOSED" if dark else "OPEN")
    widths = [chip_width(t) + 4 for t in labels]
    chips = Table([[chip(t) for t in labels]], colWidths=widths, hAlign="RIGHT")
    pad = [("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2), *ZERO_PAD[2:]]
    chips.setStyle(TableStyle(pad))
    header = Table([[idp, tp, chips]], colWidths=[46, FW - 46 - sum(widths) - 14, sum(widths) + 6])
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), *ZERO_PAD]))
    return header


def card(
    fid: str,
    title: str,
    kind: str,
    area: str,
    ev: Sequence[str],
    lines: str,
    rows: Sequence[tuple[str, str]],
    *,
    bullets: Sequence[str] = (),
    extra_meta: str = "",
    closed: str = "",
    outline_level: int = 1,
) -> KeepTogether:
    """Return a finding card: header, meta line, evidence, lines, explanation bullets, then labelled rows."""
    dark = kind == "closed"
    meta = ["INFO" if kind == "info" else "LOW", f"CLOSED {closed}" if dark else "OPEN", area]
    if extra_meta:
        meta.append(extra_meta)
    meta.append("Register, " + pref("sub-10.1" if dark else "sec-4"))
    meta_style = style("meta", fontName="PlexC", fontSize=7.4, leading=9.4, textColor=MUTE)
    data: list[list[object]] = [
        [card_header(fid, title, kind), ""],
        [Paragraph(" \u00b7 ".join(meta), meta_style), ""],
        [Paragraph("Evidence", sty_label), [Paragraph(escape(e), sty_td_mono) for e in ev]],
        [Paragraph("Lines", sty_label), Paragraph(escape(lines), style("ln", parent=sty_td, fontName="PlexC"))],
    ]
    if bullets:
        bullet_style = style("cb", parent=sty_td, fontSize=8.0, leading=10.6, leftIndent=8, bulletIndent=0)
        items = [Paragraph(fmt(b, sty_td), bullet_style, bulletText="•") for b in bullets]
        data.append([Paragraph(EXPLANATION, sty_label), items])
    data += [[Paragraph(k, sty_label), Paragraph(fmt(v, sty_td), sty_card_value)] for k, v in rows]
    body = Table(data, colWidths=[62, FW - 62])
    body.setStyle(TableStyle(card_style(kind)))
    return KeepTogether([Anchor(f"card-{fid}", f"{fid}  {title}", outline_level), body, Spacer(1, 9)])


def card_style(kind: str) -> list[tuple[object, ...]]:
    """Return a card's table commands: header fill by kind, evidence shading, rules, padding, LOW side bar."""
    dark = kind == "closed"
    commands: list[tuple[object, ...]] = [
        ("SPAN", (0, 0), (-1, 0)),
        ("SPAN", (0, 1), (-1, 1)),
        ("BOX", (0, 0), (-1, -1), 1.0 if kind == "low" else 0.7, INK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), DARK if dark else (colors.white if kind == "low" else LIGHT)),
        ("LINEBELOW", (0, 0), (-1, 0), 0.7, INK),
        ("BACKGROUND", (1, 2), (1, 2), ZEBRA),
        ("LINEBELOW", (0, 1), (-1, -2), 0.25, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.4),
        ("TOPPADDING", (0, 0), (-1, 0), 4.5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4.5),
    ]
    if kind == "low":
        commands.append(("LINEBEFORE", (0, 0), (0, -1), 3.2, INK))
    return commands


def figure_caption(num: int) -> Paragraph:
    """Return the caption paragraph of figure num."""
    return Paragraph(
        f'<font name="Plex-SBIt" color="#3a3a3a">Figure {num}</font>\u2002' + fmt(CAPTIONS[num], sty_caption),
        sty_caption,
    )


def svgfig(num: int, name: str) -> KeepTogether:
    """Return a rendered SVG figure, scaled to the frame width, with its anchor and caption."""
    from svglib.svglib import svg2rlg

    drawing = svg2rlg(str(STATE.chart_dir / f"{name}.svg"))
    if drawing is None:
        msg = f"unreadable figure {name}.svg"
        raise RuntimeError(msg)
    scale = FW / drawing.width
    drawing.width, drawing.height = drawing.width * scale, drawing.height * scale
    drawing.scale(scale, scale)
    return KeepTogether([Anchor(f"fig-{num}"), Spacer(1, 2), drawing, figure_caption(num)])


def rasterfig(num: int, name: str) -> KeepTogether:
    """Return a raster figure from the assets at frame width, keeping its aspect ratio, with its caption."""
    path = str(STATE.asset_dir / name)
    w, h = ImageReader(path).getSize()
    return KeepTogether(
        [Anchor(f"fig-{num}"), Spacer(1, 2), Image(path, width=FW, height=FW * h / w), figure_caption(num)]
    )


def code_block(label: str, lines: Sequence[str]) -> list[Flowable]:
    """Return a labelled command block: one monospace line per command, never wrapped, then a spacer."""
    head = Paragraph(
        f'<font name="PlexC-SB" size="6.8" color="#ffffff">{escape(label)}</font>', style("cbl", leading=8.5)
    )
    block = Table([[head], *[[Paragraph(escape(line), sty_code)] for line in lines]], colWidths=[FW])
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


BOARD_NEW = ("I-17", "I-18", "I-19", "I-20")  # INFO findings first seen in the 2026-10-02 previous boot
BOARD_WATCH = ("Wi-Fi TX cap 30 dBm", "10 GbE ports down")  # Table 10 items, shortened for the board


class Board(Flowable):
    """Figure 1: open and closed items as a status board (vector)."""

    def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:  # noqa: ARG002, N803
        """Take the frame width and 182 pt."""
        return FW, 182

    def box(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        *,
        fill: Color | None = None,
        lw: float = 0.8,
        dash: tuple[float, float] | None = None,
        stroke: Color = INK,
    ) -> None:
        """Draw a rectangle with an optional fill and dash."""
        canvas = self.canv
        canvas.setLineWidth(lw)
        canvas.setStrokeColor(stroke)
        if dash:
            canvas.setDash(*dash)
        else:
            canvas.setDash()
        if fill is not None:
            canvas.setFillColor(fill)
        canvas.rect(x, y, w, h, stroke=1, fill=1 if fill is not None else 0)
        canvas.setDash()

    def txt(
        self, x: float, y: float, s: str, font: str = "Plex", size: float = 7.4, color: Color = INK, align: str = "l"
    ) -> None:
        """Draw a string left-, center-, or right-aligned at (x, y)."""
        canvas = self.canv
        canvas.setFont(font, size)
        canvas.setFillColor(color)
        draw = {"l": canvas.drawString, "c": canvas.drawCentredString}.get(align, canvas.drawRightString)
        draw(x, y, s)

    def draw(self) -> None:
        """Draw the OPEN panel (L-3, INFO grid, watch items) and the CLOSED panel (fixed findings)."""
        self.draw_open(334)
        self.draw_closed(334 + 10)

    def draw_open(self, width: float) -> None:
        """Draw the OPEN panel: the LOW finding, the INFO grid, the watch items, and the open unknowns."""
        counts = dict(KPI_OPEN)
        info_ids = [r[0] for r in T8 if r[1] == "INFO"]
        self.box(0, 0, width, 182, lw=1.2)
        self.txt(10, 162, "OPEN", "Plex-SB", 13)
        summary = f"{counts['LOW']} LOW with an action · {counts['INFO']} INFO, no action · {counts['WATCH']} WATCH"
        self.txt(56, 164, summary, "Plex", 7.4, MUTE)
        self.box(10, 82, 90, 66, lw=2.4)
        self.txt(55, 122, L3["id"], "Plex-SB", 15, align="c")
        self.txt(55, 106, "LOW · redact the", "Plex", 7, align="c")
        self.txt(55, 96, f"{CAP} pair", "Plex", 7, align="c")
        cw, chh = 21.2, 21
        for i, fid in enumerate(info_ids):
            col, row = i % 10, i // 10
            x, y = 110 + col * cw, 127 - row * chh
            new = fid in BOARD_NEW
            self.box(x, y, cw, chh, lw=1.5 if new else 0.6)
            self.txt(x + cw / 2, y + 7.4, fid, "PlexC-SB" if new else "PlexC", 6.6, align="c")
        note = f"INFO findings; {BOARD_NEW[0]} to {BOARD_NEW[-1]} (bold) are new on {CAP}"
        self.txt(110, 74, note, "Plex", 6.6, MUTE)
        for x, label in zip((10, 132), BOARD_WATCH, strict=True):
            self.box(x, 14, 112, 44, lw=0.9, dash=(3, 2))
            self.txt(x + 56, 40, "WATCH", "Plex-SB", 7.6, align="c")
            self.txt(x + 56, 27, label, "Plex", 7, align="c")
        self.txt(256, 38, f"+ {counts['UNKNOWNS']} open unknowns", "Plex", 7.2)
        self.txt(256, 28, "Section 9", "Plex", 7.2, MUTE)

    def draw_closed(self, x0: float) -> None:
        """Draw the CLOSED panel from x0: one tile per closed finding (Table 17), then the answered unknowns."""
        counts = dict(KPI_CLOSED)
        self.box(x0, 0, FW - x0, 182, fill=HexColor("#ececec"), lw=1.2)
        self.txt(x0 + 10, 162, "CLOSED", "Plex-SB", 13)
        self.txt(x0 + 74, 164, "fixed and confirmed", "Plex", 7.4, MUTE)
        tiles = [(fid, f"accepted {day_[5:]}" if owner == "none needed" else day_) for fid, _, day_, _, owner in T17]
        tiles.append((f"{counts['UNKNOWNS']} unknowns", "answered"))
        tw, tg = 47, 6
        for i, (head, sub) in enumerate(tiles):
            last = i == len(tiles) - 1
            col, row = i % 3, i // 3
            x, y = x0 + 10 + col * (tw + tg), 98 - row * 52
            self.box(x, y, tw, 46, fill=MID if last else DARK, lw=0.6, stroke=DARK)
            self.txt(x + tw / 2, y + 24, head, "Plex-SB", 7.2 if last else 10.5, colors.white, "c")
            self.txt(x + tw / 2, y + 11, sub, "Plex", 6.1, colors.white, "c")
        events = counts["EVENTS"]
        self.txt(x0 + 10, 26, f"+ {events} normal event{'s' if events != 1 else ''}", "Plex", 7.2)
        self.txt(x0 + 10, 16, "(the 18:19:28 reboot)", "Plex", 7.2, MUTE)


def kpi_strip() -> Table:
    """Return the open and closed counts as a two-group strip."""
    opened, closed = KPI_OPEN, KPI_CLOSED
    items = (*opened, *closed)

    def text(markup: str, name: str, leading: float) -> Paragraph:
        """Return a centred paragraph."""
        return Paragraph(markup, style(name, alignment=TA_CENTER, leading=leading))

    data = [
        [
            text('<font name="Plex-SB" size="7.2">OPEN</font>', "kg", 9),
            *[""] * (len(opened) - 1),
            text('<font name="Plex-SB" size="7.2">CLOSED</font>', "kg", 9),
            *[""] * (len(closed) - 1),
        ],
        [text(f'<font name="Plex-SB" size="17">{v}</font>', "kb", 19) for _, v in items],
        [text(f'<font name="PlexC-SB" size="6.6" color="#5a5a5a">{k}</font>', "ks", 8) for k, _ in items],
    ]
    strip = Table(data, colWidths=[FW / len(items)] * len(items), rowHeights=[13, 22, 12])
    n = len(opened)
    strip.setStyle(
        TableStyle(
            [
                ("SPAN", (0, 0), (n - 1, 0)),
                ("SPAN", (n, 0), (-1, 0)),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, RULE),
                ("BOX", (0, 0), (n - 1, -1), 0.9, INK),
                ("BOX", (n, 0), (-1, -1), 0.9, INK),
                ("BACKGROUND", (n, 0), (-1, -1), HexColor("#ececec")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )
    return strip


def health_tiles() -> Table:
    """Return the six health tiles under the status board."""
    cells = [
        [
            Paragraph(f'<font name="PlexC-SB" size="6.4" color="#5a5a5a">{escape(k)}</font>', style("hk", leading=8)),
            Paragraph(f'<font name="Plex-SB" size="11.5">{escape(v)}</font>', style("hv", leading=14)),
            Paragraph(f'<font name="PlexC" size="6.7" color="#3a3a3a">{escape(s)}</font>', style("hs", leading=8.4)),
        ]
        for k, v, s in HEALTH
    ]
    tiles = Table([cells], colWidths=[FW / 6] * 6)
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


def toc_sections() -> Table:
    """Return the sections column of the contents page, with page numbers."""
    rows = []
    for key, level, number, title in STRUCT:
        if level == 0:
            st = style("tc0", fontName="Plex-SB", fontSize=8.8, leading=11.4)
        else:
            st = style("tc1", fontSize=8.2, leading=10.6, leftIndent=16)
        link = f'<a href="#{key}" color="#1b1b1b">'
        rows.append(
            [
                Paragraph(f"{link}{number}\u2002{escape(title)}</a>", st),
                Paragraph(f"{link}{page_of(key)}</a>", style("tp", parent=st, leftIndent=0, alignment=TA_RIGHT)),
            ]
        )
    sections = Table(rows, colWidths=[FW * 0.62 - 40, 40], hAlign="LEFT")
    commands: list[tuple[object, ...]] = [*ZERO_PAD[:2], ("TOPPADDING", (0, 0), (-1, -1), 1.2)]
    commands.append(("BOTTOMPADDING", (0, 0), (-1, -1), 1.2))
    commands += [("LINEABOVE", (0, i), (-1, i), 0.3, RULE) for i, s in enumerate(STRUCT) if s[1] == 0 and i]
    sections.setStyle(TableStyle(commands))
    return sections


def toc_side() -> list[Flowable]:
    """Return the side column of the contents page: the figure list and the reading guide."""
    figs = [
        [
            Paragraph(
                f'<a href="#fig-{i}" color="#1b1b1b"><font color="#5a5a5a">Figure {i}</font>\u2002{escape(x)}</a>',
                style("tf", fontSize=7.6, leading=9.8),
            ),
            Paragraph(
                f'<a href="#fig-{i}" color="#1b1b1b">{page_of(f"fig-{i}")}</a>',
                style("tfp", fontSize=7.6, leading=9.8, alignment=TA_RIGHT),
            ),
        ]
        for i, x in enumerate(FIGURE_TITLES, 1)
    ]
    fig_list = Table(figs, colWidths=[FW * 0.38 - 30, 22])
    padding = [*ZERO_PAD[:2], ("TOPPADDING", (0, 0), (-1, -1), 1.3), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.3)]
    fig_list.setStyle(TableStyle([*padding, ("LINEBELOW", (0, 0), (-1, -2), 0.25, RULE)]))
    head = style("fh", fontName="Plex-SB", fontSize=9.5, leading=12, spaceAfter=4)
    guide = [
        Paragraph(fmt(g, style("g", fontSize=7.6, leading=10.2)), style("g", fontSize=7.6, leading=10.2, spaceAfter=4))
        for g in GUIDE
    ]
    reading = Paragraph(
        "How to read this report", style("gh", fontName="Plex-SB", fontSize=9.5, leading=12, spaceAfter=4)
    )
    return [Paragraph("Figures", head), fig_list, Spacer(1, 14), reading, *guide]


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


def checklist() -> list[Flowable]:
    """Return the tick-box checklist for the open actions and unknowns (Section 11.5)."""

    def box() -> Table:
        """Return an empty tick box."""
        return Table([[""]], colWidths=[9], rowHeights=[9], style=[("BOX", (0, 0), (-1, -1), 0.9, INK)])

    rows = [
        [
            box(),
            Paragraph(f"<b>{a}</b>", sty_td),
            Paragraph(fmt(b, sty_td), sty_td),
            Paragraph(fmt(c, sty_td), sty_td),
            "",
        ]
        for a, b, c in CHECKLIST
    ]
    return table(["", "ID", "Action", "Done when", "Date / initials"], rows, [18, 28, 196, 190, 80])


def verdict_panel() -> Table:
    """Return the cover's verdict box: a black bar beside the verdict head and summary."""
    summary_style = style("vb", fontSize=8.6, leading=11.8)
    body = [
        Paragraph('<font name="PlexC-SB" size="7" color="#5a5a5a">VERDICT</font>', style("vk", leading=9)),
        Paragraph(fmt(VERDICT_HEAD), style("vh", fontName="Plex-SB", fontSize=12.5, leading=16, spaceAfter=2)),
        Paragraph(fmt(VERDICT_BODY, summary_style), summary_style),
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


def doc_control() -> Table:
    """Return the document-control table of the cover."""
    key_style = style("dk", fontName="PlexC-SB", fontSize=7.6, leading=9.8)
    rows = [
        [Paragraph(f"<b>{k}</b>", key_style), Paragraph(fmt(v, sty_td), style("dv", parent=sty_td))]
        for k, v in DOC_CONTROL
    ]
    control = Table(rows, colWidths=[80, FW - 80])
    control.setStyle(
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
    return control


def _story_cover() -> list[Flowable]:
    """Return the cover page and the contents page."""
    return [
        Spacer(1, 16),
        Paragraph(
            "GTR9 Pro Post-Boot Log Analysis", style("ct", fontName="Plex-SB", fontSize=25, leading=29, spaceAfter=6)
        ),
        para(
            "Beelink GTR9 Pro · CachyOS · Linux 7.2.8-2-cachyos · ry-verify 7.219.0",
            style("cs1", fontSize=10.2, leading=13.5, textColor=MUTE),
        ),
        para(
            f"Captures of 2026-10-02, 18:20 PDT · print edition, revision {REV}",
            style("cs2", fontSize=10.2, leading=13.5, textColor=MUTE, spaceAfter=12),
        ),
        verdict_panel(),
        Spacer(1, 10),
        kpi_strip(),
        para(
            "Open items: Sections 4–9 and 11. Closed items: Section 10. "
            "Watch and by-design items are not counted as findings.",
            style("kn", fontName="Plex-It", fontSize=7.4, leading=10, textColor=MUTE, spaceBefore=3, spaceAfter=9),
        ),
        KeepTogether([Anchor("fig-1"), Board(), figure_caption(1)]),
        health_tiles(),
        Spacer(1, 10),
        Paragraph("Document control", style("dch", fontName="Plex-SB", fontSize=8.4, leading=11, spaceAfter=3)),
        doc_control(),
        NextPageTemplate("body"),
        PageBreak(),
        Paragraph("Contents", style("cth", fontName="Plex-SB", fontSize=15.5, leading=19, spaceAfter=10)),
        toc(),
        PageBreak(),
    ]


def bullet_paragraphs(texts: Sequence[str]) -> list[Flowable]:
    """Return bulleted body paragraphs."""
    return [Paragraph(fmt(t), sty_bullet, bulletText="•") for t in texts]


def _story_sec1() -> list[Flowable]:
    """Return Section 1: executive summary, key facts, changes since 2026-09-27, and open actions."""
    out: list[Flowable] = [
        HPara("sec-1"),
        *[para(x) for x in EXEC_INTRO],
        HPara("sub-1.1"),
        *bullet_paragraphs(KEY_FACTS),
    ]
    out += [HPara("sub-1.2"), para(CHANGED_INTRO), *table(*T1, [120, 124, 128, 140], num=1, title="Before and after")]
    out += [svgfig(2, "fig02_boot"), HPara("sub-1.3"), para(ACTIONS_INTRO)]
    out += table(*T2, [26, 150, 150, 142, 44], num=2, title="Open actions")
    return out


def _story_sec2() -> list[Flowable]:
    """Return Section 2: inputs, capture window, method, keyword set, and severity scale."""
    out: list[Flowable] = [CondPageBreak(240), HPara("sec-2"), HPara("sub-2.1")]
    rows: list[list[object]] = []
    spans: list[tuple[Cell, Cell]] = []
    for name, size, n_lines, content, sha in T3[1]:
        rows.append([f"`{name}`", size, n_lines, content])
        rows.append([Paragraph("SHA256", sty_label), Paragraph(sha, sty_td_mono), "", ""])
        spans.append(((1, len(rows)), (3, len(rows))))
    out += table(T3[0], rows, [150, 46, 38, 278], num=3, title="Inputs", right=(1, 2), spans=spans)
    out += [HPara("sub-2.2"), *table(*T4, [196, 176, 140], num=4, title="Capture window"), para(T4_NOTE, sty_note)]
    out += [HPara("sub-2.3"), *bullet_paragraphs(METHOD)]
    ncol = 5
    terms = [[f"`{w}`" for w in KEYWORDS[i : i + ncol]] for i in range(0, len(KEYWORDS), ncol)]
    terms = [row + [""] * (ncol - len(row)) for row in terms]
    title = f"Failure keyword set ({len(KEYWORDS)} terms, case-insensitive, whole words)"
    out += table(
        ["Terms"] + [""] * (ncol - 1), terms, [FW / ncol] * ncol, num=5, title=title, spans=[((0, 0), (-1, 0))]
    )
    out += [HPara("sub-2.4"), *table(*T6, [78, 434], num=6, title="Severity and status")]
    return out


def _story_sec3() -> list[Flowable]:
    """Return Section 3: system health checks and Figures 3 and 4."""
    out: list[Flowable] = [CondPageBreak(260), HPara("sec-3")]
    out += table(*T7, [116, 166, 230], num=7, title="System health checks")
    return [*out, rasterfig(3, "fig03_dmesg.png"), svgfig(4, "fig04_verify")]


def _story_sec4() -> list[Flowable]:
    """Return Section 4: the open findings register and Figure 5."""
    rows = [
        [fid, sev, finding, area, line, action, f"{{p:card-{fid}}}"] for fid, sev, finding, area, line, action in T8
    ]
    head = ["ID", "Sev.", "Finding", "Area", "Line", "Action", "Page"]
    out: list[Flowable] = [PageBreak(), HPara("sec-4"), para(REGISTER_INTRO + " Every row is OPEN.")]
    out += table(head, rows, [30, 30, 162, 98, 46, 108, 38], num=8, title="Open findings register", shade=(1,))
    return [*out, svgfig(5, "fig05_areas")]


def _story_sec5() -> list[Flowable]:
    """Return Section 5: the L-3 card, Table 9, and Figure 6."""
    l3 = L3
    key = l3["cmds"]
    commands = f"Commands: Section {SD[key][1]}, {pref(key)}"
    out: list[Flowable] = [CondPageBreak(300), HPara("sec-5")]
    out.append(card(l3["id"], l3["title"], "low", l3["area"], l3["ev"], l3["lines"], l3["rows"], extra_meta=commands))
    out.append(para("Table 9 lists the identifier lines to remove before posting (Section 11.1)."))
    title = "Identifier classes in the 2026-10-02 captures"
    out += table(
        ["Identifier class", "Lines", "Count"], T9, [172, 292, 48], num=9, title=title, right=(2,), bold_last=True
    )
    return [*out, svgfig(6, "fig06_ident")]


def _story_sec6() -> list[Flowable]:
    """Return Section 6: the twenty INFO cards."""
    out: list[Flowable] = [PageBreak(), HPara("sec-6"), para(INFO_INTRO)]
    register = {r[0]: r for r in T8}
    for d in INFO:
        r = register[str(d["id"])]
        rows = [*d["rows"], ("Action", r[5])]
        out.append(card(r[0], r[2], "info", r[3], d["ev"], d["lines"], rows, bullets=d.get("bullets", ())))
    return out


def _story_sec7() -> list[Flowable]:
    """Return Section 7: watch and by-design items."""
    out: list[Flowable] = [CondPageBreak(260), HPara("sec-7"), para(WATCH_INTRO)]
    out += table(*T10, [92, 142, 136, 142], num=10, title="Watch items (open)")
    return out + table(*T11, [118, 150, 54, 190], num=11, title="By-design items")


def _story_sec8() -> list[Flowable]:
    """Return Section 8: coverage tables, Figures 7 to 9, and journal attribution."""
    out: list[Flowable] = [PageBreak(), HPara("sec-8"), para(COVERAGE_INTRO)]
    out += table(*T12, [112, 58, 150, 192], num=12, title="Coverage by stream")
    out += table(*T13, [178, 34, 48, 252], num=13, title="dmesg notice lines by family", right=(1,))
    out += [svgfig(7, "fig07_families"), *table(*T14, [26, 256, 230], num=14, title="ry-verify INFO notes")]
    out += [HPara("sub-8.1"), para(ATTR_INTRO)]
    rows: list[list[object]] = [[i, f, s, str(a), str(b)] for i, f, s, a, b in T15]
    rows.append(["Total", "All journal entries", "", str(sum(r[3] for r in T15)), str(sum(r[4] for r in T15))])
    head = ["ID", "Finding", "Status", "Current boot", "Previous boot"]
    title = "Journal attribution by finding"
    out += table(head, rows, [32, 300, 56, 62, 62], num=15, title=title, right=(3, 4), shade=(1, 2, 3), bold_last=True)
    return [*out, svgfig(8, "fig08_journal"), rasterfig(9, "fig09_prevboot.png")]


def _story_sec9() -> list[Flowable]:
    """Return Section 9: open unknowns and risks."""
    out: list[Flowable] = [CondPageBreak(240), HPara("sec-9"), para(UNKNOWN_INTRO)]
    return out + table(*T16, [112, 210, 190], num=16, title="Open unknowns")


def _story_sec10() -> list[Flowable]:
    """Return Section 10: closed register, Figure 10, closed cards, answered unknowns, events, and record."""
    out: list[Flowable] = [PageBreak(), HPara("sec-10"), para(CLOSED_INTRO), HPara("sub-10.1")]
    rows = [[i, f, cl, ev, ow, f"{{p:card-{i}}}"] for i, f, cl, ev, ow in T17]
    head = ["ID", "Finding", "Closed", "Evidence on 2026-10-02", "Owner", "Page"]
    out += table(head, rows, [30, 150, 54, 174, 66, 38], num=17, title="Closed findings register")
    out += [svgfig(10, "fig10_fixes"), HPara("sub-10.2")]
    titles = {r[0]: r[1] for r in T17}
    for d in CLOSED:
        fid = str(d["id"])
        out.append(
            card(
                fid,
                titles[fid],
                "closed",
                d["area"],
                d["ev"],
                d["lines"],
                d["rows"],
                closed=d["closed"],
                outline_level=2,
            )
        )
    out += [HPara("sub-10.3"), *table(*T18, [128, 196, 110, 78], num=18, title="Unknowns answered since revision 19")]
    out += [HPara("sub-10.4"), *table(*T19, [120, 172, 48, 172], num=19, title="Normal events in the logs")]
    out += [HPara("sub-10.5"), para(IMPL_INTRO)]
    return out + table(*T20, [28, 36, 142, 108, 54, 144], num=20, title="Implementation record")


def _story_sec11() -> list[Flowable]:
    """Return Section 11: commands for the open actions and unknowns, then the checklist."""
    intro = (
        "Commands for the three open actions and the open unknowns, one per line. "
        "Section 11.5 is a checklist to tick as each expected result appears."
    )
    out: list[Flowable] = [PageBreak(), HPara("sec-11"), para(intro)]
    for key, (lead, label, cmds, expected) in (
        ("sub-11.1", S111),
        ("sub-11.2", S112),
        ("sub-11.3", S113),
        ("sub-11.4", S114),
    ):
        group: list[Flowable] = [HPara(key), *([para(lead)] if lead else []), *code_block(label, cmds), para(expected)]
        out.append(KeepTogether(group))
    out.append(KeepTogether([HPara("sub-11.5"), *checklist()]))
    return out


def _story_appendices() -> list[Flowable]:
    """Return Appendix A (environment snapshot) and Appendix B (abbreviations in two columns)."""
    out: list[Flowable] = [CondPageBreak(320), HPara("sec-A")]
    out += table(["Item", "Value"], TA1, [70, 442], num="A-1", title="Environment snapshot")
    out += [CondPageBreak(200), HPara("sec-B")]
    half = (len(TB1) + 1) // 2
    left, right = TB1[:half], TB1[half:] + [("", "")] * (half - len(TB1[half:]))
    rows = [[a, b, c, d] for (a, b), (c, d) in zip(left, right, strict=True)]
    title = "Abbreviations and conventions"
    return out + table(["Term", "Meaning", "Term", "Meaning"], rows, [50, 206, 50, 206], num="B-1", title=title)


def story() -> list[Flowable]:
    """Return all flowables in reading order."""
    parts = (_story_cover, _story_sec1, _story_sec2, _story_sec3, _story_sec4, _story_sec5, _story_sec6)
    parts += (_story_sec7, _story_sec8, _story_sec9, _story_sec10, _story_sec11, _story_appendices)
    return [flowable for part in parts for flowable in part()]


class Doc(BaseDocTemplate):
    """US Letter document with cover and body page templates and the report metadata."""

    def __init__(self, filename: str) -> None:
        """Set the metadata, frames, and page templates."""
        super().__init__(
            filename,
            pagesize=letter,
            leftMargin=LM,
            rightMargin=RM,
            topMargin=TOPM,
            bottomMargin=BOTM,
            title=f"GTR9 Pro Post-Boot Log Analysis (2026-10-02), print edition, revision {REV}",
            author="GTR9 Pro log review",
            subject=(
                "Findings from cachyos-bugreport.log, the ry-verify 7.219.0 JSONL and the gtr9-postboot-fix log "
                "of 2026-10-02; open and closed items kept apart"
            ),
            keywords=(
                "CachyOS, GTR9 Pro, Strix Halo, ry-verify, gtr9-postboot-fix, dmesg, journal, log analysis, "
                "print edition"
            ),
            creator=f"build_report.py {__version__} (ReportLab)",
        )
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


def footer(canvas: Canvas, doc: BaseDocTemplate) -> None:
    """Draw the footer rule with revision and issue date, edition, and page X of Y."""
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(LM, 40, PW - RM, 40)
    canvas.setFont("Plex", 7)
    canvas.setFillColor(MUTE)
    canvas.drawString(LM, 29, f"Revision {REV} · issued {ISSUED} · captures of {CAP}")
    canvas.drawCentredString(PW / 2, 29, "Print edition")
    canvas.drawRightString(PW - RM, 29, f"Page {doc.page} of {STATE.total or '?'}")


def on_cover(canvas: Canvas, doc: BaseDocTemplate) -> None:
    """Decorate the cover: black band with edition and revision, plus the footer."""
    canvas.saveState()
    canvas.setFillColor(INK)
    canvas.rect(0, PH - 30, PW, 30, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Plex-SB", 7.8)
    canvas.drawString(LM, PH - 19, "SYSTEM LOG ANALYSIS REPORT · PRINT EDITION")
    canvas.drawRightString(PW - RM, PH - 19, f"REVISION {REV} · CAPTURES OF {CAP}")
    footer(canvas, doc)
    canvas.restoreState()


def on_body(canvas: Canvas, doc: BaseDocTemplate) -> None:
    """Decorate a body page: running header with the current section, plus the footer."""
    canvas.saveState()
    canvas.setFont("Plex", 7)
    canvas.setFillColor(MUTE)
    canvas.drawString(LM, PH - 34, "GTR9 Pro Post-Boot Log Analysis · captures of 2026-10-02")
    canvas.setFont("Plex-SB", 7)
    canvas.setFillColor(INK)
    canvas.drawRightString(PW - RM, PH - 34, STATE.page_section.get(doc.page, "Contents"))
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(LM, PH - 40, PW - RM, PH - 40)
    footer(canvas, doc)
    canvas.restoreState()


# ── BUILD ─────────────────────────────────────────────────────────────
ISSUED_EPOCH = int(dt.datetime.fromisoformat(f"{ISSUED}T00:00:00+00:00").timestamp())


def content_checks() -> dict[str, bool]:
    """Return the cross-checks between counts the report states in more than one place."""
    open_rows = [r for r in T8 if r[1] == "INFO"]
    kpi_open, kpi_closed = dict(KPI_OPEN), dict(KPI_CLOSED)
    t1 = {r[0]: r for r in T1[1]}
    br, vj = sum(r[1] for r in FIG_IDENT), sum(r[2] for r in FIG_IDENT)
    _ids, r19, cur, prev = journal_series()
    t17_closed = {r[0]: r[2] for r in T17}
    return {
        "INFO cards follow the register": [d["id"] for d in INFO] == [r[0] for r in open_rows],
        "closed cards follow Table 17": [d["id"] for d in CLOSED] == [r[0] for r in T17],
        "open counts match Tables 8, 10, and 16": (
            kpi_open["LOW"],
            kpi_open["INFO"],
            kpi_open["WATCH"],
            kpi_open["UNKNOWNS"],
        )
        == (sum(r[1] == "LOW" for r in T8), len(open_rows), len(T10[1]), len(T16[1])),
        "closed counts match Tables 17 to 19": (kpi_closed["LOW"], kpi_closed["UNKNOWNS"], kpi_closed["EVENTS"])
        == (len(T17), len(T18[1]), len(T19[1])),
        "Table 9 rows sum to its total": sum(int(r[2]) for r in T9[:-1]) == int(T9[-1][2]),
        "Figure 6 matches Table 9": sorted(b + v for _, b, v in FIG_IDENT) == sorted(int(r[2]) for r in T9[:-1])
        and T9[-1][1] == f"BR {br} · VJ {vj}",
        "Figure 7 matches Table 13": {(r[0], r[1]) for r in FIG_FAMILIES} == {(r[0], int(r[1])) for r in T13[1]},
        "Figure 8 matches Table 1": (str(sum(r19)), str(sum(cur))) == tuple(t1["Journal entries, current boot"][1:3])
        and t1["Journal entries, previous boot"][2].startswith(f"{sum(prev)} "),
        "Figure 10 matches Table 17": all(t17_closed.get(fid) == closed for fid, _, closed, _ in FIG_FIXES if closed),
        "Figure 1 matches the counts and Table 10": len(BOARD_WATCH) == len(T10[1])
        and set(BOARD_NEW) <= {r[0] for r in open_rows}
        and f"{BOARD_NEW[0]} to {BOARD_NEW[-1]}" in INFO_INTRO
        and len(open_rows) <= 20,  # the board grid holds two rows of ten
        "cover tiles match Tables 1 and 7": health_checks(t1),
        "prose counts match Tables 8 and 9": f"{br + vj} lines" in EXEC_INTRO[1]
        and f"{len(open_rows)} INFO findings" in VERDICT_BODY
        and f"{br} bug-report and {vj} JSONL lines" in T2[1][0][2]
        and f"{br} bug-report and {vj} JSONL lines" in dict(L3["rows"])["Status"],
        "Figure 4 matches Table 7": T7[1][0][2].startswith(
            f"{sum(r[1] for r in FIG_VERIFY_STATIC) + sum(r[1] for r in FIG_VERIFY_RUNTIME)} OK = "
            f"{sum(r[1] for r in FIG_VERIFY_STATIC)} static + {sum(r[1] for r in FIG_VERIFY_RUNTIME)} runtime; "
            f"{len(T14[1])} INFO notes"
        ),
        "abbreviations are sorted": [a.lower() for a, _ in TB1] == sorted(a.lower() for a, _ in TB1),
        "every figure has a caption and a title": sorted(CAPTIONS) == list(range(1, len(FIGURE_TITLES) + 1)),
    }


def health_checks(t1: Mapping[str, Sequence[str]]) -> bool:
    """Return True when the cover's health tiles restate Tables 1 and 7 correctly."""
    tile = {k: (v, sub) for k, v, sub in HEALTH}
    t7 = {r[0]: r for r in T7[1]}
    mes = re.search(r"MES (0x[0-9A-Fa-f]+)", t7["amdgpu firmware"][2])
    return all(
        (
            tile["RY-VERIFY 7.219.0"][0] in t7["ry-verify 7.219.0"][2],
            tile["BOOT TO ROOT MOUNT"][0] == t1["Root mounted"][2]
            and t1["Root mounted"][3] in tile["BOOT TO ROOT MOUNT"][1],
            re.findall(r"\d+", tile["FAILED UNITS"][0]) == re.findall(r"\d+", t7["Failed units"][1]),
            tile["KERNEL RING"][1].lower() == t7["Kernel ring buffer"][1].lower(),
            tile["NVME (P310 2 TB)"][0] in t7["NVMe (Crucial P310 2 TB)"][1]
            and all(
                part in " ".join(t7["NVMe (Crucial P310 2 TB)"][1:])
                for part in tile["NVME (P310 2 TB)"][1].split(" · ")
            ),
            mes is not None and int(tile["GPU FIRMWARE"][0].split()[-1], 16) == int(mes.group(1), 16),
            tile["GPU FIRMWARE"][1].split(" · ")[1] in t7["amdgpu firmware"][2],
        )
    )


def validate_content() -> int:
    """Run the content cross-checks; return how many ran, or raise ValueError naming every failure."""
    checks = content_checks()
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        msg = "content cross-check failed: " + "; ".join(failed)
        raise ValueError(msg)
    return len(checks)


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


def layout(path: Path, log: Callable[[str], None]) -> None:
    """Lay the story out until anchors, page count, and headers repeat; raise on drift or unresolved references."""
    for n in range(1, MAX_PASSES + 1):
        STATE.anchors.clear()
        STATE.h1pos.clear()
        STATE.unresolved.clear()
        doc = Doc(str(path))
        doc.build(story())
        total = doc.page
        sections = page_sections(total)
        stable = STATE.anchors == STATE.ref and total == STATE.total and sections == STATE.page_section
        STATE.ref, STATE.total, STATE.page_section = dict(STATE.anchors), total, sections
        log(f"pass {n}: pages={total} anchors={len(STATE.anchors)} stable={stable}")
        if stable and n > 1:
            break
    else:
        msg = f"layout did not settle after {MAX_PASSES} passes"
        raise RuntimeError(msg)
    if STATE.unresolved:
        msg = "unresolved page references: " + ", ".join(sorted(STATE.unresolved))
        raise RuntimeError(msg)


def build(out: Path, font_dir: Path, asset_dir: Path, *, verbose: bool = False) -> int:
    """Check the content, render the figures, lay out the report, and write the PDF atomically; return pages."""

    def log(message: str) -> None:
        """Report a build step on stderr when verbose; stop logging if stderr's reader goes away."""
        nonlocal verbose
        if verbose:
            try:
                print(f"build_report.py: {message}", file=sys.stderr)
            except BrokenPipeError:
                verbose = False
                silence(sys.stderr)

    log(f"{validate_content()} content checks passed")
    os.environ.setdefault("SOURCE_DATE_EPOCH", str(ISSUED_EPOCH))
    STATE.font_dir, STATE.asset_dir = font_dir, asset_dir
    register_fonts(font_dir)
    log(f"fonts {font_dir}; assets {asset_dir}")
    out = out.resolve()
    tmp = out.with_name(f".{out.name}.tmp-{os.getpid()}")
    try:
        with tempfile.TemporaryDirectory(prefix="gtr9-report-") as tmp_dir:
            STATE.chart_dir = Path(tmp_dir)
            for draw in FIGURES:
                draw()
            log(f"{len(FIGURES)} figures rendered")
            layout(tmp, log)
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    log(f"wrote {out} ({STATE.total} pages, {out.stat().st_size} bytes)")
    return STATE.total


def silence(stream: TextIO) -> None:
    """Point a closed pipe's file descriptor at /dev/null so later writes and the final flush succeed."""
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, stream.fileno())
    os.close(devnull)


def main(argv: list[str] | None = None) -> int:
    """Validate the arguments and the environment, then run; Ctrl-C at any point exits 130."""
    parser = build_parser()
    args = _ARGS if argv is None and _ARGS is not None else parser.parse_args(argv)
    if args.out.is_dir():
        parser.error(f"--out names a directory: {args.out}")
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch is not None and not epoch.strip().isdigit():
        parser.error(f"SOURCE_DATE_EPOCH must be a whole number of seconds, not {epoch!r}")
    try:
        return run(args)
    except KeyboardInterrupt:
        print("build_report.py: interrupted; nothing written", file=sys.stderr)
        return EXIT_INTERRUPT


def run(args: argparse.Namespace) -> int:
    """Run the preflight and the build; the written path goes to stdout."""
    try:
        font_dir, asset_dir = preflight(args.fonts, args.assets)
    except PreflightError as exc:
        print(f"build_report.py: {exc}", file=sys.stderr)
        return EXIT_PREFLIGHT
    if args.check:
        print(f"build_report.py: preflight ok (fonts {font_dir}, assets {asset_dir})", file=sys.stderr)
        return EXIT_OK
    try:
        build(args.out, font_dir, asset_dir, verbose=args.verbose)
    except (OSError, ValueError, RuntimeError, LayoutError) as exc:
        print(f"build_report.py: build failed: {exc}", file=sys.stderr)
        return EXIT_FAIL
    try:
        print(args.out.resolve(), flush=True)
    except BrokenPipeError:  # the PDF is written; a closed stdout only loses the path line
        silence(sys.stdout)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
