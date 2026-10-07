# gtr9-postboot-log-analysis

**Version 7.0.0** · [Changelog](CHANGELOG.md)

Analyzes a `cachyos-bugreport.log` and a ry-verify JSONL log and writes a print-edition PDF: findings with their evidence, system health, identifiers to redact before posting, coverage of every journal entry, ry-verify results, and the commands each finding calls for. Every number, line reference, table row, figure, and finding comes from the two inputs on each run; the script holds only analysis rules.

## Quick Start

```fish
sudo pacman -S --needed python-reportlab python-matplotlib python-svglib python-pillow ttf-ibm-plex
sudo cachyos-bugreport.sh
~/ry-install/ry-verify.fish --verify
./build_report.py --bugreport cachyos-bugreport.log --verify (ls -t ~/ry-install/logs/*/verify-*.jsonl | head -n 1)
```

`cachyos-bugreport.sh` must run as root, writes `cachyos-bugreport.log` to the current directory, and then offers to upload it; answer no. The PDF lands in the current directory as `post-boot-log-analysis-<capture date>.pdf`, a later run on the same capture overwrites it, and its absolute path is printed on stdout. Instead of the Python packages, `uv run build_report.py …` installs the dependencies listed at the top of the script; the fonts still come from `ttf-ibm-plex`.

## Inputs

- `--bugreport PATH` — required; the log `cachyos-bugreport.sh` (CachyOS-Settings) writes, with its header, inxi, dmesg, both journal boots, and package list
- `--verify PATH` — required; a ry-verify JSONL log, `verify-*.jsonl` or the `report-*.jsonl` that `--report` writes, under `~/ry-install/logs/<date>/`

## Usage

- `--out PATH` — output PDF; default `./post-boot-log-analysis-<capture date>.pdf`
- `--fonts DIR` — directory with the 9 IBM Plex TTF files; default search `/usr/share/fonts/TTF`, `/usr/share/fonts/truetype/ibm-plex`, `~/.local/share/fonts`
- `--check` — run the preflight, parse both inputs, and run the parser cross-checks, then stop; the summary counts journal lines the parser could not read and names any cross-check that differs
- `--verbose` — report the parse, the analysis, each layout pass, and the result on stderr
- `--version`, `--help` — version and usage; both work before the Python dependencies are loaded

## Exit Codes

- `0` — built, or the inputs parsed under `--check` (a differing cross-check is reported, not an error)
- `1` — build failed: an input in the wrong format, an unsettled layout, an unresolved page reference, or an unwritable output path
- `2` — usage error: a missing `--bugreport` or `--verify`, an unknown option, `--out` naming a directory, or a `SOURCE_DATE_EPOCH` that is not a whole number
- `3` — preflight failed: a Python module, a font file, or an input file is missing or unreadable
- `130` — interrupted with Ctrl-C; nothing is written

## The Report

- **Cover** — verdict, counts (findings by severity, watch items, identifier lines, unclassified lines, ry-verify FAIL and WARN), health tiles, and document control with both inputs' SHA256. The ry-verify totals come from the log's footer, or without one from its combined record, its phase records, or its result records.
- **Sections** — summary with the findings register and actions; inputs and method; system health with the boot timeline and ry-verify results by section; one card per finding; identifiers; watch items and settings; coverage; ry-verify details; actions with fish commands and a checklist.
- **Appendices** — A, the environment snapshot: the inxi block as captured, masked and with wrapped lines joined; B, the analysis rules.
- **Evidence** — quotes keep their line numbers (BR for the bug report, VJ for the ry-verify log), identifiers in them are replaced by placeholders such as `[root UUID]` and `[serial]`, control characters show as U+FFFD, and a quote stops at 400 characters.

## How It Works

- **Parsing** — the bug report is split at the separator lines `cachyos-bugreport.sh` writes; dmesg, both journal boots, inxi, and the package list are read line by line. inxi output in IRC form, which inxi prints when its stdin is not a terminal, is read like terminal output. Journal entries carry no year: they take the capture year, or the year before when a boot crossed New Year; journal lines the parser cannot read (another locale's month names, say) are counted in the cross-checks table. The ry-verify log yields its header, footer, phases, sections, and every OK, INFO, WARN, FAIL, and ERR record; ERR counts as FAIL, as in ry-verify. Both inputs are read as UTF-8, with or without a byte order mark; blank lines in the JSONL log are skipped.
- **Rules** — each rule names a message class: where it appears, the patterns that recognize it, its severity, what it means, and what to do. The first matching rule claims a line. A ry-verify OK record that shows a finding's mitigation lowers it to INFO and is cited.
- **Coverage** — every journal entry is attributed to a rule or listed as unclassified; dmesg lines that match the failure keywords but no rule are listed too.
- **Cross-checks** — ry-verify's per-phase counts are reconciled with its own result records, its combined totals with its footer, and every journal line is accounted for as an entry, a continuation, a marker, or an unread line.
- **Figures** — the boot timeline covers the ring buffer's first 600 s; a ry-verify run or a capture later than that is named in the caption instead of stretching the chart.
- **Reproducible** — the same inputs, fonts, and library versions give a byte-identical PDF, dated by the capture time unless `SOURCE_DATE_EPOCH` is set.

## Adding Rules

Add an entry to `RULES`: key, title, area, severity (HIGH, MED, LOW, INFO, WATCH, SETTING, or NOTE), streams, patterns, explanation, and action. Optional fields limit a rule to the previous boot's shutdown window, to certain processes, to hardware named in the bug report, or let a ry-verify record mark it mitigated. The unclassified lines in Section 7.4 show what no rule covers yet.

## Verify

```fish
./build_report.py --bugreport cachyos-bugreport.log --verify verify.jsonl --check
./build_report.py --bugreport cachyos-bugreport.log --verify verify.jsonl --out /tmp/a.pdf
./build_report.py --bugreport cachyos-bugreport.log --verify verify.jsonl --out /tmp/b.pdf; and cmp /tmp/a.pdf /tmp/b.pdf; and echo reproducible
pdffonts /tmp/a.pdf
ruff check build_report.py; and ruff format --check build_report.py; and ty check build_report.py
```

Every `pdffonts` row reads `emb yes`. `pdffonts` comes from `poppler`; `ruff` reads `ruff.toml`, and `ty` needs no settings.

## Files

- `build_report.py` — parsing, rules, analysis, figures, layout, and command line in one script
- `ruff.toml` — lint and format settings
- `CHANGELOG.md`, `LICENSE`

## Requirements

- Python 3.12 or newer; tested on 3.12, 3.13, and 3.14, which build byte-identical PDFs
- ReportLab 5.0.1, matplotlib 3.11.2, svglib 2.2.0 or 2.3.0, and Pillow 12.3.0 as tested; the script metadata requires at least ReportLab 5.0, matplotlib 3.11, svglib 2.2, and Pillow 12
- IBM Plex TTF files, the 9 named at the top of the script; `ttf-ibm-plex` on Arch, `fonts-ibm-plex` on Debian
- Inputs from `cachyos-bugreport.sh` (CachyOS-Settings, script of 2026-10-04 as tested) and ry-verify 7.226.0 as tested
- To run the Section 9 commands: fish and ripgrep with PCRE2 (`rg -P`), as Arch's `ripgrep` package builds it
- To lint: ruff 0.16 and ty 0.0.85 as tested

## License

MIT — see [LICENSE](LICENSE).
