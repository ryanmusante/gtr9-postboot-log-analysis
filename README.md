# gtr9-postboot-log-analysis

**Version 7.18.0** · [Changelog](CHANGELOG.md)

GTR9 Pro post-boot log analysis for CachyOS. `build_report.py` builds a PDF report from a `cachyos-bugreport.log` and a [ry-verify](https://github.com/ryanmusante/ry-verify) JSONL log — the findings with their evidence and the commands they call for, system health, the boot, dmesg, the journal, and the ry-verify results. Every finding, quote, count, and figure comes from the two inputs on each run; the script holds analysis rules, never results.

## Quick Start

> [!WARNING]
> The PDF carries both inputs as attachments, byte for byte, identifiers unmasked — pass `--no-attach` for a PDF that goes to someone else. `cachyos-bugreport.sh` must run as root and then offers to upload its log; answer no.

```fish
sudo pacman -S --needed python-reportlab python-matplotlib python-svglib python-pillow ttf-ibm-plex
chmod +x build_report.py
sudo cachyos-bugreport.sh
~/ry-verify/ry-verify.fish --verify
./build_report.py --bugreport cachyos-bugreport.log --verify (path sort ~/ry-install/logs/*/verify-*.jsonl)[-1]
```

Run the steps in the directory that holds `build_report.py`. The PDF lands in the current directory as `post-boot-log-analysis-<capture date>.pdf`, which a later run on a same-day capture overwrites; its absolute path is printed on stdout. Any other outcome is one of the [Exit Codes](#exit-codes).

`cachyos-bugreport.sh` writes `cachyos-bugreport.log` to the current directory. `~/ry-verify/ry-verify.fish` is where the clone command in ry-verify's README, run from the home directory, puts the script; ry-verify logs to `~/ry-install/logs/<date>/`, and `path sort` picks the newest log by its timestamped name. `uv run build_report.py …` (pacman: `uv`) installs the Python dependencies listed at the top of the script in place of the pacman packages and needs no executable bit; the fonts still come from `ttf-ibm-plex`.

## Requirements

| Requirement | Detail |
|---|---|
| OS | Arch Linux with its own packages (Python 3.14, fish 4.9) runs Quick Start and Verify as written, all but `cachyos-bugreport.sh`, which CachyOS's `cachyos-settings` ships |
| Python | 3.12 or newer; 3.12, 3.13, and 3.14 as tested, which build the same PDF, byte for byte where they link the same zlib (zlib-ng compresses it to other bytes) |
| Libraries | ReportLab 5.1.0, matplotlib 3.11.2, svglib 2.3.2 and 2.4.0, and Pillow 13.0.0 as tested; the script metadata requires at least ReportLab 5.0, matplotlib 3.11, svglib 2.2, and Pillow 12, which on Python 3.12 build the same PDF |
| Fonts | the 9 IBM Plex TTF files named at the top of the script — `ttf-ibm-plex` on Arch, `fonts-ibm-plex` on Debian |
| Inputs | `cachyos-bugreport.sh` from cachyos-settings 1:1.4.1-1 as tested, and the layout and redactions of 1.4.2 to 1.4.4 and of the CachyOS-Settings tree after them, as the script writes them; ry-verify 7.265.0 as tested, and the records of 7.266.0, as its source writes them |
| Section 3 commands | fish, and systemd's `systemctl`, `journalctl`, and `coredumpctl`; the Wi-Fi P2P task also runs NetworkManager's `nmcli`, and the jack2 task `pacman` |
| Lint | ruff 0.17.5 and ty 0.0.101 to 0.0.104 as tested |

## Usage

`--bugreport` and `--verify` are required. The PDF's path, `--help` (`-h`), and `--version` are the only stdout output — messages, the `--check` report, and `--verbose` go to stderr.

- `--bugreport PATH` — required; the log `cachyos-bugreport.sh` writes, with its header, inxi, scheduler, dmesg, both journal boots, and package list
- `--verify PATH` — required; the JSONL log of a ry-verify `--verify` run (`verify-*.jsonl`) under `~/ry-install/logs/<date>/`; a `--check` log holds no results and is refused
- `--out PATH` — output PDF; default `./post-boot-log-analysis-<capture date>.pdf` (the ry-verify start date when the bug report states none, else `undated`), with `-brief` before `.pdf` under `--brief`
- `--brief` — the cover and Sections 1 to 3 only, about 6 pages for paper; a reference to a later section names the full report, and the PDF's title says brief
- `--json PATH` — also write the findings as a [JSON Summary](#json-summary), replaced in the same step as the PDF
- `--compare PATH` — an earlier run's JSON summary: the register marks each finding new, worse, better, or same, and a line under it names those resolved since — see [JSON Summary](#json-summary)
- `--no-attach` — leave the two inputs out of the PDF, which otherwise carries them as file attachments (`pdfdetach -list`)
- `--fonts DIR` — directory with the 9 IBM Plex TTF files; default search `/usr/share/fonts/TTF`, `/usr/share/fonts/truetype/ibm-plex`, `$XDG_DATA_HOME/fonts`
- `--check` — run the preflight, parse both inputs, and run the parser cross-checks, then stop; its report counts journal lines the parser could not read and names any cross-check that differs
- `--verbose` — report the setup (the font directory and the Python and library versions), then the parse, the analysis, the charts and their matplotlib or svglib warnings, each layout pass, and each file written
- `--version`, `--help` — version and usage; both work before the Python dependencies are loaded

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | built, or the inputs parsed under `--check` — a differing cross-check is reported, not an error |
| `1` | build or check failed — an input in the wrong format (a ry-verify log of another mode among them), a `--compare` file that is no summary, an unsettled layout, an unresolved page reference, a figure svglib cannot convert whole, or an output that cannot be written: a directory that takes no new file, a default PDF name that names an input, a directory, or no regular file, or a `--json` path that is the PDF |
| `2` | usage error — a missing `--bugreport` or `--verify`; an unknown option; `--out` or `--json` naming a directory, anything else that is not a regular file, or one of the inputs (also through a link); `--check` with `--brief`, `--json`, or `--compare`; or an invalid `SOURCE_DATE_EPOCH` (see [Environment Overrides](#environment-overrides)) |
| `3` | preflight failed — a Python module, a font file, or an input file is missing or unreadable, an input is no regular file (a pipe or a device), or a font file is no TrueType font; the message names why |
| `130` | interrupted with Ctrl-C, `SIGTERM`, or `SIGHUP` — nothing is written, unless the PDF had already replaced its target, which the message names |

When the JSON summary fails after the PDF has replaced its target, the exit `1` message names the PDF. A signal after the run is over leaves its exit code. A signal while Python itself starts, before the script runs, is Python's to handle: Ctrl-C prints `KeyboardInterrupt`, and `SIGTERM` or `SIGHUP` ends the process.

## Environment Overrides

Two variables change a build. No `matplotlibrc` applies: the charts start from matplotlib's defaults.

- `SOURCE_DATE_EPOCH` — dates the PDF in place of the capture time (else the ry-verify start); a whole number of seconds up to 253402300799 (9999-12-31), and any other value exits `2`
- `XDG_DATA_HOME` — its `fonts` directory joins the default font search; `~/.local/share/fonts` when unset or relative

## JSON Summary

`--json PATH` writes the run as JSON in ASCII, other characters escaped, schema 1: `schema`, `generator`, `day` and `captured`, `machine`, `kernel`, the `inputs` with their file names, bytes, and SHA256, `ry_verify` (version, profile, mode, result in the report's words, exit as text, `exit_code` as a number or null, totals), the `findings` (ID, rule key, severity, title, first line, and the evidence lines counted per log and journal boot; a ry-verify log from another machine, installation, or boot is the one finding under the key `verify-pairing`), the `watch` items and settings, and the `actions` with their IDs, titles, the findings they answer, and completion tests.

`--compare PATH` reads an earlier summary's `schema`, `day`, `generator`, and `machine`, and each finding's `id`, `key`, `severity`, and `title`. IDs are renumbered each run, so findings match by `key`, ry-verify findings by status, phase, and section. The register gains a Change column — new, worse, better, or same; a finding that was `INFO` is new — and a line under it names the findings resolved since (gone, or lowered to `INFO`) by title and earlier ID; the verdict counts the new and the resolved findings. A summary from another machine is named as such. While this run's ry-verify log comes from another boot or machine, the earlier run's ry-verify findings are counted as not compared, not as resolved.

## The Report

A cover, a contents page, 8 sections, and 2 appendices; `--brief` stops after Section 3.

| Part | Content |
|---|---|
| Cover | the verdict; counts of findings by severity, watch items, dmesg lines and journal entries, and ry-verify `FAIL`, `WARN`, and `GEN_FAIL`; tiles for the error classes, boot to root mount, unit failures, GPU memory, and temperatures, as far as the inputs state them; the uptime strip, in seconds since kernel start on a log scale: the boot phases, where each bug-report finding that calls for action first shows, the ry-verify run (a diamond), and the bug report (a square); the action checklist with the findings each action answers and its completion test |
| Contents | sections and figures with their pages, a reading guide, and the severity levels |
| 1 Summary | key facts and the register of findings that call for action |
| 2 Findings | a card for each bug-report finding that calls for action, then the watch items and settings |
| 3 Actions | fish commands for each action, one line each |
| 4 System health | the ry-verify result, the hardware checks, and the CPU scheduler the bug report's scheduler section names, each with its result, detail, and source; the lines each error class matched in dmesg and in each journal boot |
| 5 Boot | the boot chart and boot activity by subsystem, the milestones and captures with the step between them, and the longest pauses in dmesg during the boot's burst |
| 6 dmesg | each subsystem's lines per boot phase and after the boot, its lines with failure keywords, and the rules they match; the chart and the messages after the boot; the sources with failure keywords; repeated warnings |
| 7 Journal | an overview per boot, the timeline, entries by process, repeated warnings, the messages new in the current boot, and the unit failures systemd logged |
| 8 ry-verify | results by section, failures and warnings grouped by section, and notes |
| Appendix A | the `INFO` findings, each with what it means |
| Appendix B | both inputs with their full SHA256, the parser cross-checks, and the method |

**Every page** — the footer names the generator, the capture date, and the first 8 hex digits of each input's SHA256; past the cover, the running header names the machine, the `HIGH`, `MED`, and `LOW` counts, and the section. Severity marks stand beside the levels in the counts, the register, and the contents: a filled square for `HIGH`, a half-filled one for `MED`, an open one for `LOW`.

**The file** — the PDF opens with its bookmarks shown, names the machine and capture date in its title, states its language, and carries both inputs as compressed attachments unless `--no-attach` leaves them out.

## How It Works

**Bug report** — split at the separator lines `cachyos-bugreport.sh` writes, each section known by its title in any case; lines are numbered at newline characters only, as `rg -n` numbers them. The capture time comes from the `date` line in the C locale or any English locale glibc ships; in another language it is left unstated. Journal entries carry no year: each takes the latest year that puts it at most a day after the capture time (the ry-verify start when that is unstated). Journal lines the parser cannot read (another locale's month names, say) are counted in the cross-checks and named wherever journal entries are counted.

**ry-verify log** — the header, footer, phases, sections, and every `OK`, `INFO`, `WARN`, `FAIL`, and `ERR` record. As ry-verify counts them, `ERR` is a `FAIL`, and a generator failure, logged as `FAIL`, is a `GEN_FAIL`; phase banners, the notice a signal logs, and the `ERR` lines ry-verify does not count are no results, and the hardware-mismatch warning is no finding. The totals come from a footer that counts every record — a finished run's, or an interrupted one's from ry-verify 7.257.0 — else from the combined record, else from the phase records plus the records of a phase without one. The result reads in the words of ry-install's verdict: `PREFLIGHT` for exit `3`; `FAIL` for a counted `FAIL` or `GEN_FAIL`, and for a failing exit the counts do not explain; else `PASS-WITH-WARNINGS` or `PASS`. An interrupted run reads interrupted, and a log without a footer or an exit code unknown.

**Pairing** — the pairing checks (Cross-checks, below) tell whether the ry-verify log describes the captured boot. When one differs, the log comes from another machine, installation, or boot and says nothing of the captured one: its `FAIL`, `GEN_FAIL`, and `WARN` records make no findings, its `OK` records lower none, and none is cited. One `LOW` finding takes their place, titled by the first difference; its action, first on the checklist, is the ry-verify rerun alone, with a note to capture the bug report again after a reboot. The run is placed on no chart and not on the uptime strip.

**Rules** — each rule names a message class: where it appears, the patterns that recognize it, its severity, what it means, and what to do; the first matching rule claims a line. A rule with context patterns also claims the other lines of the same event, within 3 lines after a line it claims and from the same source (the ring-reset lines after a GPU timeout). A rule that cites ry-verify quotes its last record about the same setting, whatever its status. A ry-verify `OK` record that shows a finding's mitigation lowers it to `INFO` and is cited, unless a `FAIL`, `GEN_FAIL`, or `WARN` record about the mitigation contradicts it, the record and the sub-heading above it leave out a device the evidence comes from, or the evidence names none. A rule limited to the shutdown window matches the previous boot's entries within 15 s of its last one when those hold a line only a stop sequence logs.

**Subsystems and sources** — a dmesg line belongs to the subsystem its wording names (a DRM error, an I/O error, an OOM kill, a kernel splat), else to the one its prefix names (a driver, facility, or device), else to Other; an indented line belongs to the message above it. A line's source is the text before its first colon and space when that is at most 40 characters and 3 words (a driver and its device, such as `usb 1-4`), else its first word. The source table lists 10 sources: those of findings that call for action first, then those no rule explains, then explained ones. A journal entry belongs to the process that logged it.

**Failure keywords** — 53, matched case-insensitively as whole words: `fail` · `failed` · `failure` · `failures` · `error` · `warn` · `warning` · `unable` · `cannot` · `can't` · `could not` · `couldn't` · `not supported` · `unsupported` · `not found` · `no such` · `denied` · `invalid` · `timeout` · `timed out` · `abort` · `aborted` · `aborting` · `crash` · `crashed` · `panic` · `oops` · `bug` · `taint` · `taints` · `tainting` · `call trace` · `segfault` · `fault` · `faults` · `io_page_fault` · `killed` · `refused` · `reset` · `hang` · `lockup` · `stall` · `stalls` · `corrupt` · `corrupted` · `corruption` · `mismatch` · `deprecated` · `unknown` · `lacking` · `kaput` · `critical temperature` · `hardware protection`.

**Counts** — counts are of messages: a continuation belongs to its entry. Lines and entries with a failure keyword are counted per log, boot, subsystem, source, and process, those a `NOTE` rule claims aside. Repeated warnings group messages whose text differs only in numbers and hex values after the first word; each table lists the 10 most frequent. The messages after the boot group the same way, the first 10 in order of their first line; the boot comparison lists the 10 most frequent messages of the current boot that the previous boot did not log.

**Boot** — the kernel runs until it starts init (`/init` when an initramfs holds it), the initrd until the switch to the root file system, and userspace until the boot's burst of dmesg lines ends; a phase dmesg does not bound is left out. The switch is systemd's `Switching root` line where dmesg holds it, else the stop of the initrd's journald (`Received SIGTERM`) when systemd's start banner follows it. The burst ends at the first 30 s pause after the last milestone, in a window of 600 s from the kernel start or from a later start of init, root mount, or root switch; without a pause, the burst is cut at the window's last line and userspace lasts at least its span (≥). A ring buffer whose first line comes more than a second after the kernel start has lost the boot: the boot charts, phases, milestones, pauses, and the lines after the boot are left out, and Section 5 says where dmesg starts.

**Kernel start** — each kernel message logged once in both dmesg and the current journal bounds the start to the second the journal stamps, and the estimate is the middle of the window they share. A message printed while no journald ran bounds it from above only, and messages after the first suspend are left out, as dmesg time stops while the system sleeps.

**Cross-checks** — in Appendix B.2 and under `--check`: ry-verify's per-phase counts against its result records, its combined totals against its footer, its records against a footer that counts them all, and a finished run's exit code against its counts; every journal line as an entry, a continuation, a marker, or an unread line; the kernel messages paired between dmesg and the journal, which must agree on the kernel start; and the 7 sections `cachyos-bugreport.sh` writes. The pairing checks apply where both logs state the fact: the CPU vendor ry-verify detected (the mismatch warning, else `HARDWARE_MATCH`, logged from 7.251.0) against inxi's, its root file system against the bug report's, its kernel release (`KERNEL_RELEASE`, logged from 7.265.0) against the bug report's, the kernel command line tokens it checked against the bug report's `cmdline:` line, and its start against the kernel start, as a run from before the captured boot describes an earlier one.

**Quotes** — quotes keep their line numbers: BR, a line of the bug report; VJ, a line of the ry-verify log, blank lines counted. A quote from wrapped inxi output shows the line its number names. A message logged once is quoted as logged, repeats as their template.

**Actions** — every `HIGH`, `MED`, and `LOW` finding is answered by an action. A failed unit is looked up in its own boot and manager (`journalctl -b -1`, `--user-unit`) by its name as logged, a masked identifier as `*`; kernel errors are looked up in the warning-level kernel journal with monotonic times. The ry-verify rerun calls the script by the absolute path its log's header records, else `~/ry-verify/ry-verify.fish`, after `sudo -v` when sudo stopped the run or its cache lapsed. Above a paired log's rerun stand up to 6 of ry-verify's own suggestions in log order and the checks a lapsed sudo cache skipped. A finding no failure action answers gets its rule's task — for a unit that failed under a redacted name, the Wi-Fi P2P device, a HID joystick, or jack2 — and an action without commands points at its finding's card.

**Figures** — 5, in grayscale: the boot chart (dmesg lines per bar, with the longest silence before the root mount, a band of the boot phases, the milestones, and the captures); boot activity by subsystem (one tick per line, aligned under the boot chart); dmesg after the boot (one tick per line, one row per subsystem, with a dashed line at each suspend); the journal timeline (entries per bar for each boot, on a log scale once a bar holds 20, above one row of ticks per finding or message class); and ry-verify results by section (the static phase above the runtime phase, on one scale). The boot chart covers the burst and the captures up to twice its length, within 600 s of the kernel start; a capture later than the chart or before the kernel start appears in the timeline table only.

**Missing data** — a log section without content is one sentence, and a journal whose lines could not be read adds its overview table; a journal boot of which no line could be read counts as not read, never as zero. A figure without data is left out and its section says so.

## Privacy and Reliability

**Masking** — identifiers in quotes and in ry-verify section names (UUIDs, machine and USB4 domain IDs, serials, MAC, IPv4, and IPv6 addresses, home directories, email addresses) are replaced by placeholders such as `[root UUID]` and `[serial]`. A fact keeps a version of four numbers (a firmware's `1.20.3.4`), which a quote masks as an IPv4 address. The attachments are not masked — see [Quick Start](#quick-start).

**Control characters** — ANSI escapes are removed; other control characters (tab and line feed aside), the Unicode line and paragraph separators, invisible format characters, and the private-use characters U+E000 to U+E002, which the report's own markup uses, show as U+FFFD, as do the bytes of a file name that are not UTF-8.

**Limits** — the rules read the first 4,096 characters of a dmesg line, a journal entry, or an inxi line, four times the kernel's longest record; the boot milestones and the dmesg, inxi, and scheduler facts keep to the same limit. A quote stops at 400 characters, as does a fact the cover or the health table states; a ry-verify version, profile, or mode stops at 40 and a section name at 60. A card quotes up to 8 lines, and a Lines cell lists up to 40 line ranges per log and counts the rest. The ry-verify failures table lists 20 rows per section, the notes and unit-failure tables 40 rows, and a last row counts the rest; in the failures table, 3 or more records of one section that share a status and the text after or before their first `: ` are one row. A chart row draws at most 2,000 ticks and a figure 6,000; past that, ticks that fall on one place of the axis are drawn once.

**Outputs** — the PDF and the JSON summary are written to scratch files beside their targets, created exclusively under unpredictable names, then renamed over them; each gets the permissions a new file gets there, from the umask or the directory's default ACL.

**Reproducible** — the same inputs under the same file names, with the same fonts, library versions, and zlib, give a byte-identical PDF and JSON summary in any directory and under any output name; the PDF is dated by the capture time (else the ry-verify start) unless `SOURCE_DATE_EPOCH` is set.

## Adding Rules

Add an entry to `RULES`: key, title, area, severity (`HIGH`, `MED`, `LOW`, `INFO`, `WATCH`, `SETTING`, or `NOTE`), streams, patterns, and explanation (an empty one leaves the card's row out), plus an action unless none is needed. Optional fields:

- `window="shutdown"` — limit the rule to the previous boot's shutdown window
- `idents` — limit the rule to certain processes
- `requires` — limit the rule to hardware named in the bug report
- `mitigated_by` — let a ry-verify record mark the finding mitigated; with `covers="ids"`, that record must name each device by its USB vendor and product IDs
- `context` — claim the other lines of one event
- `lead` — claim such a line only while the last line claimed is the rule's own match
- `cites` — quote ry-verify's record about the same setting
- `task`, `done` — the checklist action and its completion test, for a `HIGH`, `MED`, or `LOW` rule that no failure action covers

A function under the rule's key in `RULE_COMMANDS` builds a task's commands from the finding's evidence; a finding whose rule names no task still gets an action, which points at its card.

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

**Unresolved imports under `ty`** — `ty` resolves imports in the system Python, so it needs the pacman packages of Quick Start; after the `uv run` route alone it reports them unresolved.

## Contributing

Questions and bug reports: [GitHub issues](https://github.com/ryanmusante/gtr9-postboot-log-analysis/issues). Single-host scope — open an issue before a PR.

## License

MIT — see [LICENSE](LICENSE).
