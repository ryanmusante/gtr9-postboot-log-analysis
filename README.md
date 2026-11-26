# gtr9-postboot-log-analysis

**Version 7.33.0** · [Changelog](CHANGELOG.md)

GTR9 Pro post-boot log analysis for CachyOS. `build_report.py` turns a `cachyos-bugreport.log` and a [ry-verify](https://github.com/ryanmusante/ry-verify) JSONL log into a PDF report. The report leads with the findings, their evidence, and the commands they call for, then covers system health, the boot, dmesg, the journal, and the ry-verify results. The script holds analysis rules, never results.

## Quick Start

> [!WARNING]
> The PDF carries both inputs as attachments, with identifiers unmasked — pass `--no-attach` for a PDF that goes to someone else. `cachyos-bugreport.sh` must run as root and then offers to upload its log; answer no.

```fish
sudo pacman -S --needed python-reportlab python-matplotlib python-svglib python-pillow ttf-ibm-plex
chmod +x build_report.py
sudo cachyos-bugreport.sh
~/ry-verify/ry-verify.fish --verify
./build_report.py --bugreport cachyos-bugreport.log --verify (path sort ~/ry-install/logs/*/verify-*.jsonl)[-1]
```

Run the steps in the directory that holds `build_report.py`. The PDF lands in the current directory as `post-boot-log-analysis-<capture date>.pdf`; a later run on a capture from the same day overwrites it. If no PDF lands, the exit code and its message say why; see [Exit Codes](#exit-codes).

- `cachyos-bugreport.sh` writes `cachyos-bugreport.log` to the current directory.
- `~/ry-verify/ry-verify.fish` is where ry-verify's README clones the script when run from the home directory, and ry-verify logs to `~/ry-install/logs/<date>/`.
- `uv run build_report.py …` (pacman: `uv`) installs the Python dependencies in place of the pacman packages and needs no executable bit. The fonts still come from `ttf-ibm-plex`.

## Requirements

| Requirement | Detail |
|---|---|
| OS | CachyOS or Arch Linux with its own packages (Python 3.14, fish 4.9) runs Quick Start and [Verify](#verify) as written; Arch lacks `cachyos-bugreport.sh`, which only CachyOS's `cachyos-settings` ships |
| Python | 3.12 or newer; 3.12, 3.13, and 3.14 as tested, which build the same PDF — byte for byte where they link the same zlib |
| Libraries | at least ReportLab 5.0, matplotlib 3.11, svglib 2.2, and Pillow 12; ReportLab 5.1.0 and 5.1.2, matplotlib 3.11.2, svglib 2.3.2 and 2.4.0, and Pillow 13.0.0 as tested |
| Fonts | the 9 IBM Plex TTF files named at the top of the script — `ttf-ibm-plex` on CachyOS and Arch, `fonts-ibm-plex` on Debian |
| Inputs | `cachyos-bugreport.sh` from cachyos-settings 1:1.4.1-1 as tested, and the layout and redactions it writes in 1.4.2 to 1.4.5; ry-verify 7.265.0 to 7.302.0 as tested |
| Section 3 commands | fish, `sudo`, and systemd's `systemctl`, `journalctl`, and `coredumpctl`; the Wi-Fi P2P task also runs NetworkManager's `nmcli`, and the jack2 task `pacman` |
| Lint | ruff 0.17.5 to 0.17.6 and ty 0.0.101 to 0.0.111 as tested |

## Usage

The PDF's path, `--help` (`-h`), and `--version` are the only output on stdout; messages, the `--check` report, and `--verbose` go to stderr.

- `--bugreport PATH` — required; the log `cachyos-bugreport.sh` writes
- `--verify PATH` — required; the JSONL log of a ry-verify `--verify` run (`verify-*.jsonl`); a `--check` log is refused
- `--out PATH` — the output PDF; default `./post-boot-log-analysis-<capture date>.pdf`, dated by the ry-verify start when the bug report states no capture date, else `undated`; under `--brief`, `-brief` comes before `.pdf`
- `--brief` — the cover and Sections 1 to 3 only, about 6 pages, for paper
- `--json PATH` — also write the findings as a [JSON Summary](#json-summary)
- `--compare PATH` — compare with an earlier run's [JSON Summary](#json-summary)
- `--no-attach` — leave the two inputs out of the PDF
- `--fonts DIR` — the directory with the 9 IBM Plex TTF files; the default search covers `/usr/share/fonts/TTF`, `/usr/share/fonts/truetype/ibm-plex`, and `$XDG_DATA_HOME/fonts`
- `--check` — run the preflight, parse both inputs, run the parser cross-checks, and stop
- `--verbose` — report the setup (the font directory and the Python and library versions), the parse, the analysis, the charts and their warnings, each layout pass, and each file written
- `--version`, `--help` — print the version or the usage; both work without the Python dependencies

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | built, or the inputs parsed under `--check` — a differing cross-check is reported, not an error |
| `1` | build or check failed — an input in the wrong format (a ry-verify log of another mode among them), a `--compare` file that is no summary, an unsettled layout, an unresolved page reference, a figure svglib cannot convert whole, or an output that cannot be written |
| `2` | usage error — a missing `--bugreport` or `--verify`; an unknown option; `--out` or `--json` naming a directory, anything else that is not a regular file, or one of the inputs; `--check` with `--brief`, `--json`, or `--compare`; an invalid `SOURCE_DATE_EPOCH` (see [Environment Overrides](#environment-overrides)) |
| `3` | preflight failed — a Python module, a font file, or an input file is missing or unreadable, an input is no regular file, or a font file is no TrueType font |
| `130` | interrupted by Ctrl-C, `SIGTERM`, or `SIGHUP` — nothing is written, unless the PDF had already replaced its target, which the message names |

When the JSON summary fails after the PDF has replaced its target, the exit `1` message names the PDF. A signal that arrives while Python itself starts, before the script runs, is Python's to handle.

## Environment Overrides

Two variables change a build; no `matplotlibrc` applies.

- `SOURCE_DATE_EPOCH` — dates the PDF in place of the capture time (else the ry-verify start); a whole number of seconds up to 253402300799 (9999-12-31), and any other value exits `2`
- `XDG_DATA_HOME` — its `fonts` directory joins the default font search; `~/.local/share/fonts` when unset or relative

## The Report

The report has a cover, a contents page, 8 sections, and 2 appendices.

| Part | Content |
|---|---|
| Cover | the verdict; counts of findings by severity, watch items, dmesg lines and journal entries, and ry-verify `FAIL`, `WARN`, and `GEN_FAIL`; tiles for the error classes, boot to root mount, unit failures, GPU memory, and temperatures; the uptime strip; the action checklist |
| Contents | sections and figures with their pages, a reading guide, and the severity levels |
| 1 Summary | key facts and the register of findings that call for action |
| 2 Findings | a card for each bug-report finding that calls for action, then the watch items and settings |
| 3 Actions | fish commands for each action, one line each |
| 4 System health | the ry-verify result, the hardware checks, and the CPU scheduler, each with its result, detail, and source; the lines each error class matched in dmesg and in each journal boot |
| 5 Boot | the boot chart and boot activity by subsystem, the milestones and captures with the step between them, and the longest pauses in dmesg during the boot's burst |
| 6 dmesg | each subsystem's lines per boot phase and after the boot, its lines with failure keywords, and the rules they match; the chart and the messages after the boot; the sources with failure keywords; repeated warnings |
| 7 Journal | an overview per boot, the timeline, entries by process, repeated warnings, the messages new in the current boot, and the unit failures systemd logged |
| 8 ry-verify | results by section, failures and warnings grouped by section with ry-verify's hints, and notes |
| Appendix A | the `INFO` findings, each with what it means |
| Appendix B | both inputs with their full SHA256, the parser cross-checks, and the method |

**The cover** — the uptime strip marks the boot phases, the first line of each bug-report finding that calls for action, the ry-verify run, and the bug-report capture. The action checklist gives each action the findings it answers and its completion test.

**Every page** — the footer names the generator, the capture date, and the first 8 hex digits of each input's SHA256. Past the cover, the running header names the machine, the `HIGH`, `MED`, and `LOW` counts, and the section.

**The file** — the PDF opens with its bookmarks shown, names the machine and capture date in its title and the project as its author, and states its language.

## JSON Summary

`--json PATH` writes the run as JSON in ASCII, schema 1:

- `schema`, `generator`, `day`, and `captured`
- `machine` and `kernel`
- `inputs` — each input's file name, bytes, and SHA256
- `ry_verify` — version, profile, mode, the result in the report's words, the exit as text, `exit_code` as a number or null, and the totals
- `findings` — ID, rule key, severity, title, first line, and the evidence lines counted per log and journal boot; a ry-verify log from another machine, installation, or boot is the one finding under the key `verify-pairing`, and a paired run that did not finish adds one under `verify-unfinished`
- `watch` — the watch items and settings
- `actions` — IDs, titles, the findings each answers, and the completion tests

`--compare PATH` reads an earlier summary's `schema`, `day`, `generator`, and `machine`, and each finding's `id`, `key`, `severity`, and `title`. Findings match by `key`; ry-verify findings match by status, phase, and section.

- The register gains a Change column: new, worse, better, or same. A finding that was `INFO` is new.
- A line under the register names the findings resolved since: gone, or lowered to `INFO`.
- The verdict counts the new and the resolved findings.
- A summary from another machine is named as such.
- When this run's ry-verify log comes from another boot or machine, the earlier run's ry-verify findings are counted as not compared.

## How It Works

### Parsing

**Bug report** — the log is split at the separator lines `cachyos-bugreport.sh` writes, and its lines are numbered as `rg -n` numbers them. The capture time comes from the `date` line in the C locale or any English locale glibc ships; in another language it is left unstated.

**dmesg lines** — a line without a timestamp continues the message above it. One before the first timestamp continues none: it is counted in the cross-checks and named wherever dmesg lines are counted.

**Journal entries** — an entry carries no year, so each takes the latest year that puts it at most a day after the capture time (the ry-verify start when that is unstated). Journal lines the parser cannot read (another locale's month names, say) are counted in the cross-checks and named wherever journal entries are counted.

**ry-verify log** — the parser reads the header, footer, phases, sections, and every `OK`, `INFO`, `WARN`, `FAIL`, and `ERR` record. As ry-verify counts them, `ERR` is a `FAIL`, and a generator failure is a `GEN_FAIL`. Phase banners, the notice a signal logs, and the `ERR` lines ry-verify does not count are no results, and the hardware-mismatch warning is no finding.

**Totals** — the run's totals come from a footer that counts every record (a finished run's, or an interrupted one's from ry-verify 7.257.0), else from the combined record, else from the phase records plus the records of a phase without one.

**Result** — the ry-verify result reads in the words of ry-install's verdict: `PREFLIGHT` for exit `3`; `FAIL` for a counted `FAIL` or `GEN_FAIL`, and for a failing exit the counts do not explain; else `PASS-WITH-WARNINGS` or `PASS`. An interrupted run reads interrupted, and a log without a footer or an exit code reads unknown; in a paired log either is a `LOW` finding, as the checks past the log's last record did not run, and the rerun answers it.

### Pairing

The pairing checks (see [Cross-Checks](#cross-checks)) tell whether the ry-verify log describes the captured boot. When one differs, the log comes from another machine, installation, or boot: its `FAIL`, `GEN_FAIL`, and `WARN` records make no findings, its `OK` records lower none, and none is cited. One `LOW` finding takes their place, titled by the first difference. The finding's action, first on the checklist, is the ry-verify rerun alone, with a note to capture the bug report again after a reboot.

### Rules

Each rule names a message class: where it appears, the patterns that recognize it, its severity, what it means, and what to do.

- The first matching rule claims a line.
- A rule with context patterns also claims the other lines of the same event, within 3 lines after a line it claims and from the same source.
- A rule that cites ry-verify quotes its last record about the same setting, whatever its status.
- A ry-verify `OK` record that shows a finding's mitigation lowers it to `INFO` and is cited — unless a `FAIL`, `GEN_FAIL`, or `WARN` record about the mitigation contradicts it, the record and the sub-heading above it leave out a device the evidence comes from, or the evidence names none.
- A rule limited to the shutdown window matches the previous boot's entries within 15 s of its last one, when those hold a line only a stop sequence logs.

### Counting

**Subsystems** — a dmesg line belongs to the subsystem its wording names (a DRM function tag, an I/O error, an OOM kill, a kernel splat), else to the one its prefix names, else to Other; an indented line belongs to the message above it. A journal entry belongs to the process that logged it.

**Sources** — kernel splats, OOM kills, machine checks, block-layer errors, file-system messages that name their device, and lines with a DRM function tag (`[drm:…]`) have sources of their own. Any other line's source is the text before its first colon and space when that is at most 40 characters and 3 words (such as `usb 1-4`), else its first word. The source table lists 10 sources: those of findings that call for action first, then those no rule explains, then explained ones.

**Failure keywords** — 53, matched case-insensitively as whole words:

```text
fail · failed · failure · failures · error · warn · warning · unable
cannot · can't · could not · couldn't · not supported · unsupported
not found · no such · denied · invalid · timeout · timed out · abort
aborted · aborting · crash · crashed · panic · oops · bug · taint · taints
tainting · call trace · segfault · fault · faults · io_page_fault · killed
refused · reset · hang · lockup · stall · stalls · corrupt · corrupted
corruption · mismatch · deprecated · unknown · lacking · kaput
critical temperature · hardware protection
```

**Counts** — counts are of messages: a continuation belongs to its entry. Lines and entries with a failure keyword are counted per log, boot, subsystem, source, and process, those a `NOTE` rule claims aside.

**Repeats** — repeated warnings group messages whose text differs only in numbers and hex values after the first word; each table lists the 10 most frequent. The messages after the boot group the same way and list the first 10 in order of their first line. The boot comparison lists the 10 most frequent messages of the current boot that the previous boot did not log.

### Boot Timeline

**Boot phases** — the kernel runs until it starts init, the initrd until the switch to the root file system, and userspace until the boot's burst of dmesg lines ends; a phase dmesg does not bound is left out.

**Burst** — the burst ends at the first 30 s pause after the last milestone, in a window of 600 s from the kernel start or from a later start of init, root mount, or root switch. Without a pause, the burst is cut at the window's last line, and userspace lasts at least its span (≥).

**Lost boot** — a ring buffer whose first line comes more than a second after the kernel start has lost the boot: the boot charts, phases, milestones, pauses, and the lines after the boot are left out, and Section 5 says where dmesg starts.

**Kernel start** — each kernel message logged once in both dmesg and the current journal bounds the start to the second the journal stamps, and the estimate is the middle of the window they share. A message printed while no journald ran bounds it from above only, and messages after the first suspend are left out.

### Cross-Checks

Appendix B.2 and `--check` report:

- ry-verify's per-phase counts against its result records, its combined totals against its footer, its records against a footer that counts them all, and a finished run's exit code against its counts
- every dmesg line as a message, a continuation, or an unread line, and every journal line as an entry, a continuation, a marker, or an unread line
- the kernel messages paired between dmesg and the journal, which must agree on the kernel start
- the 7 sections `cachyos-bugreport.sh` writes

The pairing checks apply where both logs state the fact:

- the CPU vendor ry-verify detected (the mismatch warning, else `HARDWARE_MATCH`, logged from 7.251.0) against inxi's
- its root file system against the bug report's
- its kernel release (`KERNEL_RELEASE`, logged from 7.265.0) against the bug report's
- the kernel command line tokens it checked against the bug report's `cmdline:` line
- its start against the kernel start

### Quotes and Actions

**Quotes** — quotes keep their line numbers: BR is a line of the bug report, VJ a line of the ry-verify log, blank lines counted. A message logged once is quoted as logged; repeats are quoted as their template.

**Actions** — every `HIGH`, `MED`, and `LOW` finding is answered by an action. A failed unit is looked up in its own boot and manager (`journalctl -b -1`, `--user-unit`) by its name as logged, with a masked identifier as `*`; kernel errors are looked up in the warning-level kernel journal.

**ry-verify rerun** — the rerun calls the script by the absolute path its log's header records, else `~/ry-verify/ry-verify.fish`. The rerun follows `sudo -v` when sudo stopped the run or its cache lapsed, or `sudo true` when the stop says `sudo -v` cached nothing. Above a paired log's rerun stand up to 6 of ry-verify's own suggestions and notes, and the checks a lapsed sudo cache skipped; a hint is cited with the record ry-verify logged it under. A record or hint that names a command, after `Run:`, `Install missing:`, a dash, a parenthesis, or a semicolon, is a suggestion; any other hint is a note.

**Tasks** — a finding no failure action answers gets its rule's task: for a unit that failed under a redacted name, the Wi-Fi P2P device, a HID joystick, or jack2. An action without commands points at its finding's card.

### Figures and Missing Data

**Figures** — 5, in grayscale: the boot chart, boot activity by subsystem, dmesg after the boot, the journal timeline (on a log scale once a bar holds 20), and ry-verify results by section. The boot chart covers the burst and the captures up to twice its length, within 600 s of the kernel start; a capture later than the chart or before the kernel start appears in the timeline table only.

**Missing data** — a log section without content is one sentence, which counts any lines that could not be read; a journal whose lines could not be read adds its overview table. A journal boot of which no line could be read counts as not read, never as zero. A figure without data is left out, and its section says so.

## Privacy and Reliability

**Masking** — identifiers in quotes and in ry-verify section names are replaced by placeholders such as `[root UUID]` and `[serial]`: UUIDs, machine and USB4 domain IDs, serials, MAC, IPv4, and IPv6 addresses, home directories, and email addresses. A fact keeps a four-part version such as a firmware's `1.20.3.4`; a quote masks the same string as an IPv4 address. The attachments are not masked — see [Quick Start](#quick-start).

**Control characters** — ANSI escapes are removed. Other control characters (tab and line feed aside), the Unicode line and paragraph separators, invisible format characters, and the private-use characters U+E000 to U+E002 show as U+FFFD, as do the bytes of a file name that are not UTF-8.

**Limits** — what the report reads and shows has fixed bounds:

- The rules read the first 4,096 characters of a dmesg line, a journal entry, or an inxi line; the boot milestones and the dmesg, inxi, and scheduler facts keep to the same limit.
- A quote stops at 400 characters, as does a fact the cover or the health table states; a ry-verify version, profile, or mode stops at 40, and a section name at 60.
- A card quotes up to 8 lines, and a Lines cell lists up to 40 line ranges per log and counts the rest.
- The ry-verify failures table lists 20 rows per section, and the notes and unit-failure tables 40 rows; a last row counts the rest. In both ry-verify tables, 3 or more records of one section that share a status and the text after or before their first colon and space are one row. A failure's row ends with the hints ry-verify logged under it, which the notes leave out.
- A chart row draws at most 2,000 ticks, and a figure 6,000.

**Outputs** — the PDF and the JSON summary are written to scratch files beside their targets, then renamed over them; each gets the permissions a new file gets there.

**Reproducible** — the same inputs under the same file names, with the same fonts, library versions, and zlib, give a byte-identical PDF and JSON summary in any directory and under any output name.

## Adding Rules

Add an entry to `RULES`: key, title, area, severity (`HIGH`, `MED`, `LOW`, `INFO`, `WATCH`, `SETTING`, or `NOTE`), streams, patterns, and explanation (an empty one leaves the card's row out), plus an action unless none is needed. Optional fields:

- `window="shutdown"` — limit the rule to the previous boot's shutdown window
- `idents` — limit the rule to certain processes
- `mitigated_by` — let a ry-verify record mark the finding mitigated; with `covers="ids"`, that record must name each device by its USB vendor and product IDs
- `context` — claim the other lines of one event
- `lead` — claim such a line only while the last line claimed is the rule's own match
- `cites` — quote ry-verify's record about the same setting
- `task`, `done` — the checklist action and its completion test, for a `HIGH`, `MED`, or `LOW` rule that no failure action covers

A function under the rule's key in `RULE_COMMANDS` builds a task's commands from the finding's evidence.

## Verify

```fish
sudo pacman -S --needed poppler ruff ty
set -l vj (path sort ~/ry-install/logs/*/verify-*.jsonl)[-1]
./build_report.py --bugreport cachyos-bugreport.log --verify $vj --check
./build_report.py --bugreport cachyos-bugreport.log --verify $vj --out /tmp/a.pdf
./build_report.py --bugreport cachyos-bugreport.log --verify $vj --out /tmp/b.pdf; and cmp /tmp/a.pdf /tmp/b.pdf; and echo reproducible
pdffonts /tmp/a.pdf
pdfdetach -list /tmp/a.pdf
ruff check build_report.py; and ruff format --check build_report.py; and ty check build_report.py
```

Every `pdffonts` row shows `yes` in its `emb` column, and `pdfdetach` lists the two inputs. `ruff` reads `ruff.toml`; `ty` needs no settings.

## Troubleshooting

**Script not executable** — fish answers `exists but is not an executable file` when a download or a copy dropped the executable bit; `chmod +x build_report.py` restores it.

**Log lookup with `ls -t` fails** — CachyOS's fish config makes `ls` an alias for `eza`, which reads `-t` as `--time`; `path sort` picks the newest ry-verify log by its timestamped name.

**Unresolved imports under `ty`** — `ty` resolves imports in the system Python, so it needs the pacman packages of Quick Start; after the `uv run` route alone, it reports them unresolved.

## Contributing

Questions and bug reports: [GitHub issues](https://github.com/ryanmusante/gtr9-postboot-log-analysis/issues). Single-host scope — open an issue before a PR.

## License

MIT — see [LICENSE](LICENSE).
