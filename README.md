# gtr9-postboot-log-analysis

**Version 7.2.1** · [Changelog](CHANGELOG.md)

Analyzes a `cachyos-bugreport.log` and a ry-verify JSONL log and writes a PDF report: findings with their evidence, system health and the boot timeline, ry-verify results, dmesg and journal statistics with charts, and the commands each finding calls for. Every finding, quote, line reference, and figure comes from the two inputs on each run; the script holds analysis rules, never results.

## Quick Start

```fish
sudo pacman -S --needed python-reportlab python-matplotlib python-svglib python-pillow ttf-ibm-plex
chmod +x build_report.py
sudo cachyos-bugreport.sh
~/ry-install/ry-verify.fish --verify
./build_report.py --bugreport cachyos-bugreport.log --verify (path sort ~/ry-install/logs/*/verify-*.jsonl)[-1]
```

Run the steps in the directory that holds `build_report.py`. `chmod +x` restores the executable bit that a download or a copy can drop; without it fish answers "exists but is not an executable file". `cachyos-bugreport.sh` must run as root, writes `cachyos-bugreport.log` to the current directory, and then offers to upload it; answer no. `path sort` picks the newest ry-verify log by its timestamped name; `ls -t` does not work there, because CachyOS's fish config makes `ls` an alias for `eza`, which reads `-t` as `--time`. The PDF lands in the current directory as `post-boot-log-analysis-<capture date>.pdf`, a later run on any capture from the same day overwrites it, and its absolute path is printed on stdout. Instead of the Python packages, `uv run build_report.py …` (pacman: `uv`) installs the dependencies listed at the top of the script and needs no executable bit; the fonts still come from `ttf-ibm-plex`.

## Inputs

- `--bugreport PATH` — required; the log `cachyos-bugreport.sh` (CachyOS-Settings) writes, with its header, inxi, dmesg, both journal boots, and package list
- `--verify PATH` — required; a ry-verify JSONL log, `verify-*.jsonl` or the `report-*.jsonl` that `--report` writes, under `~/ry-install/logs/<date>/`

## Usage

- `--out PATH` — output PDF; default `./post-boot-log-analysis-<capture date>.pdf`
- `--fonts DIR` — directory with the 9 IBM Plex TTF files; default search `/usr/share/fonts/TTF`, `/usr/share/fonts/truetype/ibm-plex`, `$XDG_DATA_HOME/fonts` (`~/.local/share/fonts` when unset)
- `--check` — run the preflight, parse both inputs, and run the parser cross-checks, then stop; the summary counts journal lines the parser could not read and names any cross-check that differs
- `--verbose` — report the parse, the analysis, each layout pass, and the result on stderr
- `--version`, `--help` — version and usage; both work before the Python dependencies are loaded

## Exit Codes

- `0` — built, or the inputs parsed under `--check` (a differing cross-check is reported, not an error)
- `1` — build or check failed: an input in the wrong format, an unsettled layout, an unresolved page reference, or an unwritable output path
- `2` — usage error: a missing `--bugreport` or `--verify`, an unknown option, `--out` naming a directory, or a `SOURCE_DATE_EPOCH` that is not a whole number of seconds up to 253402300799 (9999-12-31)
- `3` — preflight failed: a Python module, a font file, or an input file is missing or unreadable
- `130` — interrupted with Ctrl-C, `SIGTERM`, or `SIGHUP`; nothing is written

## The Report

- **Cover** — verdict, counts (findings by severity, watch items, dmesg lines and journal entries, ry-verify FAIL, WARN, and GEN_FAIL), system tiles (error classes, boot to root mount, unit failures, GPU memory, temperatures, as far as the inputs state them), and document control with each input's size and SHA256 prefix. The ry-verify totals come from a finished run's footer, else its combined record, else its phase records plus the result records of a phase without one, as after an interruption; preamble records the totals leave out are named.
- **Contents** — sections and figures with their pages, a reading guide, and the severity levels.
- **Sections** — 1 Summary: key facts, the register of findings that call for action, and the actions. 2 System health: checks, the boot timeline and boot activity by subsystem, a table of the milestones and captures with the step between them, the longest pauses in dmesg, watch items and settings. 3 Findings: a card for each finding from the bug report that calls for action, and one table of the INFO messages a rule explains. 4 ry-verify: results by section, failures and warnings grouped by section, notes. 5 Logs: the streams, dmesg by subsystem and by rule family, journal entries by process, the journal timeline, and the unit failures systemd logged. 6 Actions: fish commands for each action and a checklist.
- **Actions** — a failed unit is looked up in its own boot and manager (`journalctl -b -1`, `--user-unit`), kernel errors in the warning-level kernel journal with monotonic times, and ry-verify's own suggested fixes are quoted as comments above its rerun.
- **Appendices** — A Environment snapshot: the inxi block, masked, with wrapped lines joined under their line range; B Inputs and method: both inputs with their full SHA256, the parser cross-checks, and the method with the failure keywords.
- **Evidence** — quotes keep their line numbers (BR, a line of the bug report; VJ, a line of the ry-verify log, blank lines counted), and a quote from wrapped inxi output shows the line its number names; identifiers in them are replaced by placeholders such as `[root UUID]` and `[serial]`; ANSI escapes are removed, other control characters (tab aside) and the Unicode line and paragraph separators show as U+FFFD, and a quote stops at 400 characters. A card quotes up to 8 lines; a Lines cell lists up to 40 line ranges per log and counts the rest.

## How It Works

- **Parsing** — the bug report is split at the separator lines `cachyos-bugreport.sh` writes; dmesg, both journal boots, inxi, and the package list are read line by line, and lines are numbered at newline characters only, as `rg -n` numbers them. inxi output in IRC form, which inxi prints when its stdin is not a terminal, is read like terminal output. The capture time comes from the `date` line in the C locale or any English locale glibc ships; in another language it is left unstated. Journal entries carry no year: they take the capture year (the ry-verify start's when the capture time is unstated), or the year before when a boot crossed New Year; journal lines the parser cannot read (another locale's month names, say) are counted in the parser cross-checks, and the report names them where it would count journal entries. The ry-verify log yields its header, footer, phases, sections, and every OK, INFO, WARN, FAIL, and ERR record; ERR counts as FAIL, as in ry-verify, its phase banners and the notice it logs when a signal stops it are not results, its hardware-mismatch warning is listed but is no finding, and a footer count that is not a whole number is an input in the wrong format. Both inputs are read as UTF-8, with or without a byte order mark; blank lines in the JSONL log are skipped.
- **Rules** — each rule names a message class: where it appears, the patterns that recognize it, its severity, what it means, and what to do. The first matching rule claims a line. A ry-verify OK record that shows a finding's mitigation lowers it to INFO and is cited, unless a FAIL or WARN record about the same mitigation contradicts it or the ry-verify log names a CPU from another vendor than the bug report, as when the two logs come from different machines.
- **Logs** — a dmesg line belongs to the subsystem its prefix names (a driver, facility, or device; GPU, USB and input, storage, network, and so on) or, for an error without one, its wording (a DRM error, an I/O error, an OOM kill, a kernel splat); an indented line belongs to the message above it, and a line without either to Other. A journal entry belongs to the process that logged it. Counts are of messages: a continuation belongs to its entry, and a wrapped inxi line counts once. Messages with a failure keyword are counted per stream and subsystem, those a NOTE rule claims aside.
- **Cross-checks** — in Appendix B.2: ry-verify's per-phase counts are reconciled with its own result records, its combined totals with its footer, and every journal line is accounted for as an entry, a continuation, a marker, or an unread line.
- **Figures** — six, drawn from the inputs in grayscale: the boot timeline (dmesg lines per bar, 0.01 to 5 s wide by the span shown, with the quiet gap, the milestones, and the captures), boot activity by subsystem (one tick per dmesg line on a span from each subsystem's first line to its last, on the boot timeline's time axis and aligned under it), ry-verify results by section (the static phase above the runtime phase, on one scale), dmesg lines by rule family (the boilerplate families as one bar), journal entries by process (the current boot's bar above the previous boot's; at most 12 bars, so past 12 processes the 11 busiest and one Other bar), and the journal timeline (one tick per entry a rule matches, one row per finding or message class; the current boot timed from the kernel start when it can be estimated, the previous boot by the wall clock). The boot timeline covers the boot's burst of dmesg lines, which ends at the first 30 s pause after the last milestone, and the captures up to twice that time, within 600 s of the kernel start; its caption counts the dmesg lines it leaves out, and a later capture appears in the timeline table only. The pause table covers the burst alone. A figure without data is left out and its section says so.
- **Reproducible** — the same inputs under the same file names, with the same fonts and library versions, give a byte-identical PDF in any directory and under any output name, dated by the capture time (else the ry-verify start) unless `SOURCE_DATE_EPOCH` is set; a capture time whose date line names its zone only by abbreviation takes the ry-verify log's UTC offset when both fall on the same day.

## Adding Rules

Add an entry to `RULES`: key, title, area, severity (HIGH, MED, LOW, INFO, WATCH, SETTING, or NOTE), streams, patterns, and explanation (an empty one leaves the card's row out), plus an action unless none is needed. Optional fields limit a rule to the previous boot's shutdown window, to certain processes, to hardware named in the bug report, or let a ry-verify record mark it mitigated.

## Verify

```fish
sudo pacman -S --needed poppler ruff ty
set -l vj (path sort ~/ry-install/logs/*/verify-*.jsonl)[-1]
./build_report.py --bugreport cachyos-bugreport.log --verify $vj --check
./build_report.py --bugreport cachyos-bugreport.log --verify $vj --out /tmp/a.pdf
./build_report.py --bugreport cachyos-bugreport.log --verify $vj --out /tmp/b.pdf; and cmp /tmp/a.pdf /tmp/b.pdf; and echo reproducible
pdffonts /tmp/a.pdf
ruff check build_report.py; and ruff format --check build_report.py; and ty check build_report.py
```

Every `pdffonts` row reads `emb yes`. `ruff` reads `ruff.toml`, and `ty` needs no settings; `ty` resolves the imports in the system Python, so it needs the pacman packages of Quick Start; after the `uv run` route alone it reports them unresolved.

## Files

- `build_report.py` — parsing, rules, analysis, figures, layout, and command line in one script
- `ruff.toml` — lint and format settings
- `CHANGELOG.md`, `LICENSE`

## Requirements

- Python 3.12 or newer; tested on 3.12, 3.13, and 3.14, which build byte-identical PDFs
- ReportLab 5.0.1, matplotlib 3.11.2, svglib 2.3.0, and Pillow 12.3.0 as tested; the script metadata requires at least ReportLab 5.0, matplotlib 3.11, svglib 2.2, and Pillow 12
- IBM Plex TTF files, the 9 named at the top of the script; `ttf-ibm-plex` on Arch, `fonts-ibm-plex` on Debian
- Inputs from `cachyos-bugreport.sh` (CachyOS-Settings, script of 2026-10-04 as tested) and ry-verify 7.226.0 as tested
- To run the Section 6 commands: fish, and systemd's `systemctl`, `journalctl`, and `coredumpctl`
- To lint: ruff 0.16 and ty 0.0.85 as tested

## License

MIT — see [LICENSE](LICENSE).
