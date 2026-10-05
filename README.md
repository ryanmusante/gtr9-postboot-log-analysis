# gtr9-postboot-log-analysis

**Version 6.0.0** · [Changelog](CHANGELOG.md)

Analyses a `cachyos-bugreport.log` and a ry-verify JSONL log and writes a print-edition PDF: findings with their evidence, system health, identifiers to redact before posting, coverage of every journal entry, ry-verify results, and the commands each finding calls for. Every number, line reference, table row, figure, and finding comes from the two inputs on each run; the script holds only analysis rules.

## Quick Start

```fish
sudo pacman -S --needed python-reportlab python-matplotlib python-svglib python-pillow ttf-ibm-plex
sudo cachyos-bugreport.sh
~/ry-install/ry-verify.fish --verify
./build_report.py --bugreport cachyos-bugreport.log --verify (ls -t ~/ry-install/logs/*/verify-*.jsonl | head -n 1)
```

`cachyos-bugreport.sh` must run as root, writes `cachyos-bugreport.log` to the current directory, and then offers to upload it; answer no. The PDF lands in the current directory as `post-boot-log-analysis-<capture date>.pdf`, and its absolute path is printed on stdout. Instead of the Python packages, `uv run build_report.py …` installs the dependencies listed at the top of the script; the fonts still come from `ttf-ibm-plex`.

## Inputs

- `--bugreport PATH` — required; the log `cachyos-bugreport.sh` (CachyOS-Settings) writes, with its header, inxi, dmesg, both journal boots, and package list
- `--verify PATH` — required; a ry-verify JSONL log, `verify-*.jsonl` or the `report-*.jsonl` that `--report` writes, under `~/ry-install/logs/<date>/`

## Usage

- `--out PATH` — output PDF; default `./post-boot-log-analysis-<capture date>.pdf`
- `--fonts DIR` — directory with the 9 IBM Plex TTF files; default search `/usr/share/fonts/TTF`, `/usr/share/fonts/truetype/ibm-plex`, `~/.local/share/fonts`
- `--check` — run the preflight and parse both inputs, then stop
- `--verbose` — report the parse, the analysis, each layout pass, and the result on stderr
- `--version`, `--help` — version and usage; both work before the Python dependencies are loaded

## Exit Codes

- `0` — built, or the inputs parsed under `--check`
- `1` — build failed: an input in the wrong format, an unsettled layout, an unresolved page reference, or an unwritable output path
- `2` — usage error: a missing `--bugreport` or `--verify`, an unknown option, `--out` naming a directory, or a `SOURCE_DATE_EPOCH` that is not a whole number
- `3` — preflight failed: a Python module, a font file, or an input file is missing or unreadable
- `130` — interrupted with Ctrl-C; nothing is written

## The Report

- **Cover** — verdict, counts (findings by severity, watch items, identifier lines, unclassified lines, ry-verify FAIL and WARN), health tiles, and document control with both inputs' SHA256.
- **Sections** — summary with the findings register and actions; inputs and method; system health with the boot timeline and ry-verify results by section; one card per finding; identifiers; watch items and settings; coverage; ry-verify details; actions with commands and a checklist.
- **Appendices** — the inxi block as captured, masked, and the rule catalogue.
- **Evidence** — quotes keep their line numbers (BR for the bug report, VJ for the ry-verify log), and identifiers in them are replaced by placeholders such as `[root UUID]` and `[serial]`.

## How It Works

- **Parsing** — the bug report is split at the separator lines `cachyos-bugreport.sh` writes; dmesg, both journal boots, inxi, and the package list are read line by line. The ry-verify log yields its header, footer, phases, sections, and every OK, INFO, WARN, and FAIL record.
- **Rules** — each rule names a message class: where it appears, the patterns that recognise it, its severity, what it means, and what to do. The first matching rule claims a line. A ry-verify OK record that shows a finding's mitigation lowers it to INFO and is cited.
- **Coverage** — every journal entry is attributed to a rule or listed as unclassified; dmesg lines that match the failure keywords but no rule are listed too.
- **Cross-checks** — ry-verify's per-phase counts are reconciled with its own result records, and its combined totals with its footer.
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

- Python 3.12 or newer; tested on 3.14.7
- ReportLab 5.0.1, matplotlib 3.11.2, svglib 2.2.0, and Pillow 12.3.0 as tested; the script requires at least ReportLab 5.0, matplotlib 3.11, svglib 2.2, and Pillow 12
- IBM Plex 6.4.0 TTF files (`ttf-ibm-plex`)
- Inputs from `cachyos-bugreport.sh` (CachyOS-Settings, script of 2026-10-04 as tested) and ry-verify 7.226.0 as tested
- To lint: ruff 0.16 and ty 0.0.84 as tested

## License

MIT — see [LICENSE](LICENSE).
