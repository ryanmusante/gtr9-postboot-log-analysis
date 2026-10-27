Changes for gtr9-postboot-log-analysis
======================================

Newest first. Versioning is MAJOR.MINOR.PATCH.


7.8.1
-----

  - report: the cover counts share one type size at which the widest fits
    its column; a four-digit count no longer wraps over its label
  - report: temperatures carry a degree sign; a word too wide for its
    Appendix A column breaks after a separator, not mid-name
  - report: the method says ERR is a FAIL only where ry-verify counts it


7.8.0
-----

  - report: the dmesg lines by rule family chart and Section 6.4 are gone;
    the 6.1 lead counts the lines a NOTE rule matches
  - actions: the ry-verify rerun falls back to ~/ry-verify/ry-verify.fish,
    where ry-verify's README clones it, not ~/ry-install/ry-verify.fish
  - rules: usb-volume loses its soft-mixer mitigation, which no ry-verify
    record has named since 7.229.0; mitigations name devices by USB ID
  - ry-verify: ERR lines it logs without counting them (continuations,
    the boot-path note) are no results, so the cross-checks agree
  - code: comments are single lines and the year-fallback docstring says
    1972; status, pairs, and a generator's _ no longer reuse names
  - docs: README runs ~/ry-verify/ry-verify.fish and names ry-verify
    7.256.0, ReportLab 5.1.0, ruff 0.17.3, and ty 0.0.92 as tested


7.7.0
-----

  - report: the cover's document-control table is gone; Appendix B.1 gives
    the inputs and their SHA256, the footer and PDF metadata the generator
  - report: Section 1.3, now Action checklist, merges the actions table and
    the checklist; Section 3 keeps each action's commands
  - report: the verdict no longer repeats the tiles' note on a journal
    that was not read when no error class was seen
  - dmesg: one table in 6.1 replaces the boot phases table of 5.2, the
    dmesg overview, and the subsystem table of 6.2, now By source
  - dmesg: it counts each subsystem's lines per boot phase and after the
    boot; a lead gives the lines' span and how many a rule matches
  - dmesg: a subsystem's first and last line show once dmesg has lost the
    boot; otherwise the boot and after-boot charts show when it logs
  - boot: 5.2 is Milestones and captures; when dmesg has lost the boot,
    5.1 alone says where it starts
  - report: times since the kernel start use thousands separators in the
    dmesg tables, pauses, milestones, and chart labels too
  - report: a ry-verify run whose ends round to the same tenth of a
    second shows one time in the timeline table and the boot chart
  - actions: a script path with whitespace other than a space (a tab, a
    line break) reruns ry-verify as ~/ry-install/ry-verify.fish
  - code: one formatter prints seconds; helpers build Matched by cells and
    NOTE line sets; the rendered charts live in the build state
  - code: docstrings follow ry-verify 7.254.0's counting of stopped,
    finished, and interrupted runs
  - docs: README lists the report by section and splits How It Works by
    input, subsystem, count, and boot


7.6.0
-----

  - figures: journal entries by process and by finding are no longer
    drawn; tables count them, and message classes keep their timeline rows
  - boot: the boot phases table folds into the subsystem table: each phase
    heads its column with its span, and a last row totals the lines
  - boot: phase durations, >= for a cut userspace, move to the boot
    chart's legend; During boot shows only when phases leave lines out
  - report: system health keeps the ry-verify result and the hardware
    checks; error classes, dmesg, and unit failures have their own tables
  - report: the error-class lead names journal lines that could not be
    read, as the health rows that said so are gone
  - report: the verdict no longer repeats the error classes its tile names
  - actions: the ry-verify rerun names the script by the absolute path in
    the log's header, a home directory as ~, else ~/ry-install/ry-verify.fish
  - code: ty 0.0.90 passes; four functions over 50 lines are split, and a
    local no longer shadows run()
  - docs: README follows the new layout and names the versions tested;
    ruff.toml lists DTZ901 among the rules left out of select


7.5.1
-----

  - actions: a failed unit is looked up by its logged name, masked
    identifiers as * patterns, and every command fits one line
  - cli: --out may not name an input; the path prints as its bytes; a
    signal after the write or the run ends cleanly, never a traceback
  - journal: an entry logged just after the capture across a month or
    year end takes the right year
  - rules: a ry-verify mitigation lowers a USB device's finding only when
    the record names that device; gpu-hang covers soft recovery
  - masking: IPv6 addresses, invisible format characters, ry-verify
    section names, and file names that are not UTF-8
  - boot: milestones past 600 s extend the burst; phases keep their
    measured spans, and a cut userspace reads as a lower bound
  - boot: the chart names later milestones, states a cut burst, draws
    the ry-verify run as a bar, and shows short phases on long axes
  - report: a journal boot that could not be read counts as not read
  - report: the verdict adds only what its head line and tiles omit
  - report: the quiet gap reads as the longest silence, with its span
  - report: templates keep placeholders; counts and section numbers
    stay with their labels; six tables have clearer titles
  - report: long ry-verify and unit-failure tables list their first
    rows and count the rest; crowded charts draw fewer labels
  - parsing: continuation lines join in linear time; absurd dmesg
    stamps, dates, and zone offsets no longer stop a build
  - ry-verify: a footer count outside 0 to 999,999,999 is an input error
  - docs: README follows the new behavior


7.5.0
-----

  - boot: a table counts each subsystem's dmesg lines in each boot phase
  - dmesg: a table lists the sources with failure keywords (a device such
    as usb 1-4, a subsystem), findings that call for action first
  - journal: a boot comparison lists the current boot's messages that the
    previous boot did not log, numbers aside, the 10 most frequent
  - report: system health gives each check's result, detail, and source
    in columns; ry-verify's totals name GEN_FAIL only when counted
  - report: a message logged once is quoted as logged; only repeats read
    as their template
  - report: 6.2 is now By subsystem and source, 7.5 Boot comparison
  - text: the verdict, key facts, reading guide, severity levels, leads,
    captions, Method, and rule explanations are shorter
  - docs: README describes the new tables and the source of a dmesg line


7.4.0
-----

  - report: the explained (INFO) messages move to Appendix A, after the
    logs; Inputs and method become Appendix B
  - report: System health splits into its checks and an Error classes
    table: the lines each class matched in dmesg and in each journal boot
  - report: the verdict names GEN_FAIL only when ry-verify counted one and
    leaves the preamble detail to the health table
  - report: a health fact inxi gives as N/A is left out; the error-class
    row points at its table instead of listing the classes
  - report: the parser cross-checks test that dmesg and the journal agree
    on the kernel start, and Method says how it is estimated
  - dmesg: a chart of the lines after the boot, one tick per line and row
    per subsystem, in seconds, minutes, or hours by the span shown
  - dmesg: the chart marks each suspend and names a burst the 600 s window
    cut; when the chart is absent, the section says why
  - dmesg: a table quotes the messages after the boot in order of their
    first line, numbers and hex values aside, the first 10
  - dmesg: a table of repeated warnings: lines with a failure keyword that
    repeat with other numbers, boilerplate aside, the 10 most frequent
  - dmesg: common boot lines (keyrings, taskstats, serial ports, NUMA,
    XFRM, traffic control, cache drops) leave Other for their subsystems
  - journal: a table of repeated warnings per process, with entries per
    boot and the rules they match
  - journal: the process table counts entries with failure keywords
  - journal: the current boot's timeline counts in seconds, minutes, or
    hours by its span; a long uptime no longer reads as an offset
  - cli: an interrupt while the PDF replaces its target waits for the
    step to end, so the message names the PDF as written
  - layout: a paragraph never leaves its last line alone atop a page, and
    a number in prose or a caption stays on the line of its time unit
  - rules: the inxi mislabel rule goes with the inxi dump it explained;
    the jack2 explanation no longer narrates inxi
  - text: rule explanations, watch-item meanings, captions, and Method say
    the same in fewer words; the key facts name the bug report, not inxi
  - docs: README describes the new order, repeated warnings, the kernel
    start, and the eight figures


7.3.0
-----

  - report: sections run Summary, Findings, Actions, System health, Boot,
    dmesg, Journal, ry-verify, then Appendix A Inputs and method
  - report: watch items and settings join the findings, and the health
    checks get a section of their own
  - report: the inxi dump appendix and the Log streams table are gone; the
    inxi block still feeds the key facts, the tiles, and the health checks
  - report: a log section without content is one sentence; its empty
    subsections leave the section and the contents
  - report: the boot key fact gives the kernel and initrd phases; the
    verdict drops the error-class list the health table holds
  - report: a timeline row inside the ry-verify run steps from its start,
    so no step reads negative
  - report: a unit failure's result is printed as logged, never as markup
  - boot: the boot chart draws a band of the phases: the kernel until init,
    the initrd until the switch to the root file system, then userspace
  - boot: a phases table gives each phase's span, duration, and dmesg lines;
    init without an initramfs ends the kernel phase too
  - boot: a switch of the root file system shows an initrd whatever its
    init is called; only userspace counts the line at its end
  - boot: dmesg that lost the boot draws no boot chart and says where it
    starts; a report without dmesg says that instead
  - figures: a ry-verify run past the boot chart reaches its edge, a
    capture before the boot is named so, and no lane is drawn empty
  - figures: paired value labels no longer overlap, long process names are
    cut in the middle, and minute ticks date their midnight
  - dmesg: an overview table counts lines during and after the boot,
    subsystems, rule matches, and lines with failure keywords
  - dmesg: a splat's banner, task, taint, and register lines count under
    Kernel core, hardware errors under CPU and memory
  - journal: an overview table per boot gives the span, entries, processes,
    rule matches, entries with failure keywords, and unit failures
  - journal: the timeline draws entries per bar above the matched ticks,
    on a log scale once a bar holds 20
  - journal: a new chart compares each finding's entries in the two boots,
    drawn when both boots have entries a rule matches
  - journal: a boot of which no line could be read shows dashes, and the
    key facts and the health table name the unread lines
  - ry-verify: a generator failure, logged as FAIL, counts as GEN_FAIL in
    the chart, the tables, the cross-checks, and a finding of its own
  - ry-verify: a runtime phase stopped for want of sudo is named beside
    the totals that leave it out
  - rules: a mitigation that lists devices lowers a finding only when it
    names every device the evidence shows
  - rules: the shutdown window holds only with a line that only a stop
    sequence logs, so a quiet shutdown keeps the session's failures
  - actions: a unit named only by "Failed to start" gets its journalctl
    command; extra core dumps point at coredumpctl list
  - parsing: inxi's numeric keys in IRC form, per-core speeds and
    repository lists, read as in terminal output
  - text: the reading guide keeps IDs, lines, and the boot columns; Method
    is shorter and leaves inxi parsing to the README
  - text: the captions of the subsystem, process, and ry-verify charts say
    what is drawn and where the rest is listed
  - cli: --out must name a regular file or a new one, and a path Python
    cannot stat fails the build instead of raising a traceback
  - cli: font files that do not parse fail the preflight with exit 3
  - cli: Ctrl-C, SIGTERM, and SIGHUP exit 130 from the script's first line;
    an interrupt after the PDF has replaced its target names it
  - cli: the temporary file has a short name, so any output name fits;
    SOURCE_DATE_EPOCH takes at most 20 digits
  - code: table() loses the hold option with its last caller, and ruff's
    max-args returns to 8
  - docs: README describes the new order, the boot phases, and the seven
    figures


7.2.1
-----

  - report: a Lines cell lists at most 40 line ranges per log and counts
    the rest, so a message repeated thousands of times still builds
  - report: GEN_FAIL records count in the verdict, the cover counts, the
    ry-verify action, and its completion test
  - report: the cover groups its counts as FINDINGS, WATCH, LOGS, and
    RY-VERIFY; its ry-verify line says interrupted, as the key facts do
  - report: Section 5.5 says when no journal entry matches a rule; 5.1
    counts unread journal lines beside the entries
  - report: the pause table stays inside the boot's burst of dmesg lines;
    a pause or quiet gap is the difference of its printed ends
  - report: the timeline step after the ry-verify run counts from its end
    and stays an estimate; a capture hours later keeps its kernel time
  - report: counts are of messages, a continuation with its entry and a
    wrapped inxi line once; Method says so and covers indented lines
  - report: "Matched by" replaces "Explained by", and Section 5.3 and
    Figure 4 read "dmesg by rule family"
  - report: the reading guide puts dmesg over time in Section 2.2; the LOW,
    WATCH, and NOTE legend lines match how the report uses them
  - report: captions and titles drop fragments, say "in time order" for
    the pauses, and mention Other or milestones only when drawn
  - report: the process table's last row lists its findings in the body
    font; a cross-check no longer differs over an absent warn= count
  - report: one failed unit gets "Inspect the failed unit"; unit-failure
    owners print in ID order; a clipped quote keeps a word ending at the limit
  - layout: a group's last record or an inxi block label no longer opens or
    ends a page alone; monospace text breaks after its indentation
  - layout: the failure-keyword list never starts a line with a dot; large
    overflow counts take thousands separators
  - figures: milestone labels closer than 12 percent of the axis stack;
    a boot timeline without milestones draws no empty lane
  - figures: the boot timeline labels whole decades only, draws a marker
    at the axis edge whole, and labels a run without an end "from"
  - figures: the journal timeline's seconds axis spans 2 s at least and
    keeps edge ticks inside; section names cannot open math text
  - analysis: the kernel-start estimate pairs a message journald read late
    as an upper bound only, and skips messages after a suspend
  - analysis: dmesg errors without a prefix take their subsystem from the
    wording: DRM errors, I/O errors, OOM kills, and kernel splats
  - analysis: acpiphp, acpi_cpufreq, zswap, CPU mitigations, sp5100-tco,
    usb-storage, ACP devices, and partition lines sort to their subsystems
  - analysis: subsystems that start at the same time follow their first
    line; keyword counts agree between Sections 5.1 and 5.2
  - rules: storage errors add the block layer's medium, transport, and
    timeout errors and NVMe I/O timeouts that abort
  - rules: the ksecretd message no longer claims a disabled wallet; KDE
    startup noise names what it covers
  - rules: the OOM explanation covers memory cgroup limits; an undetermined
    Secure Boot state is recognized
  - rules: amdgpu errors and kfd notices no longer pass as optional-feature
    boilerplate, so their failure keywords count
  - rules: failure keywords add aborted, aborting, critical temperature,
    and hardware protection; unknown kernel parameters are boilerplate
  - rules: redacted units read "failed under a redacted name", and their
    actions filter with journalctl -g, so no command needs ripgrep
  - rules: the unit-failed card points to systemctl --failed and each
    unit's own boot, as Section 6 does; error lists say "kernel splat"
  - actions: past six failed units, the overflow comment points to
    Section 5.6, which lists every unit
  - privacy: the root UUID in an escaped unit name and machine IDs under
    /run/log/journal are masked; the USB4 placeholder spells ID in caps
  - parsing: ry-verify's "Caught SIG..." cleanup notice is no result; ANSI
    escapes with colon parameters are removed
  - parsing: an IRC inxi value starting with digits stays a value; package
    lines are counted under a redacted repository name
  - cli: an interrupt during a callback, a module load, or the teardown
    exits 130, writes nothing, and reports only the interrupt
  - cli: SIGINT takes its default action before the standard library
    loads, and Ctrl-C at launch no longer reads as a SyntaxError
  - cli: with stderr closed, nothing goes to stdout; a missing module
    names its own package
  - cli: --help says --check exits 0 once the inputs parse; --verbose
    counts message classes
  - code: comments and docstrings match the code; limits are named and
    explained; an unknown section key fails the build
  - code: dead code goes: the card's Note row, table totals, the bugreport
    count, the unused chart faces, and a section reader
  - docs: README installs the Verify block's tools, covers GEN_FAIL and the
    40-range cap, and drops the same-path condition for byte identity


7.2.0
-----

  - report: Section 5 Identifiers, the identifiers finding, and the
    redaction action are gone; quotes stay masked
  - report: unclassified lines are no longer listed, counted, or given an
    action; Section 6.4 and its sed commands are gone
  - report: Coverage becomes Section 5 Logs and Actions becomes Section 6;
    the cover counts dmesg lines and journal entries instead
  - report: the timeline table gives the step between events, and a new
    table lists the five longest pauses in dmesg during the boot
  - report: Section 5 adds dmesg lines by subsystem, journal entries by
    process, and every unit failure systemd logged in either boot
  - figures: new Figure 2 draws boot activity by subsystem, one tick per
    dmesg line, aligned under the boot timeline; later figures renumber
  - figures: new Figure 5 compares journal entries per process across the
    two boots; process names cannot open matplotlib math text
  - figures: the journal timeline draws the entries a rule explains
  - figures: charts use the IBM Plex files the report text uses, even where
    another copy of the family is installed
  - actions: no command names an input file, so sed is no longer needed
  - cross-checks: the journal attribution check leaves with the
    unclassified list
  - docs: README describes Section 5 Logs, the six figures, and the pause
    table; the identifier and unclassified-line texts are gone


7.1.0
-----

  - report: the verdict quotes where a ry-verify run stopped and names a
    profile run on other hardware or a log from another machine
  - report: ry-verify totals name the preamble WARN records they omit; an
    interrupted run reads "interrupted, exit 130" with complete totals
  - report: key facts give ry-verify's profile, mode, and result once,
    cite its preamble records by VJ number, and date the journal span
  - report: the register drops the Area column, and Cur. and Prev. when no
    finding has journal entries; IDs follow severity, then the first line
  - report: ry-verify failures sit under a heading row per section that a
    new page repeats; the parser cross-checks move to Appendix B.2
  - report: records before a phase's first section are named so, and the
    headings show ERR records as ERR, counted as FAIL
  - report: a journal the parser cannot read is named so, not empty, on
    the cover, in the key facts, and in the health table
  - report: a quote from wrapped inxi output cites the line it shows;
    Appendix A gives a wrapped line's range; the cover names placeholders
  - report: the unit-failure row counts both boots, the tile the current
    one, and each names the findings that cover them
  - report: a mitigation that leaves a part vulnerable (spectre_v2 BHI)
    counts as partly vulnerable and is named
  - report: the drive row gives the first drive's full model and its own
    SMART data; the GPU fact stops before an inxi vendor key
  - report: firmware dates read as ISO dates, the bare root UUID as
    [root UUID], and counts with thousands separators
  - report: fewer repeats: no identifier key fact or card note, no boot
    rows in the health table, one ry-verify run row in the timeline
  - report: the timeline table lists local times in time order and rounds
    the kernel start to hundredths
  - report: input file names, inxi values, and ry-verify header fields
    print as written; their markup characters cannot break a build
  - layout: no blank page before Section 7; its command blocks drop the
    Done-when lines the checklist repeats
  - layout: a section or appendix, Section 7 included, starts on the
    current page when 160 pt remain, so fewer pages end half empty
  - layout: monospace text keeps its spacing and wraps long paths after a
    slash; commands shrink further, so every rg check fits one line
  - layout: a card's chip aligns with the frame, its outline entry nests
    under 3.1, and a rule without an explanation leaves that row out
  - layout: one gray palette and hairline weight; MED chips differ from
    HIGH; notes in INFO rows are italic; inxi block labels are bold
  - figures: the boot timeline leaves late captures to the table, shades
    only the bars inside the quiet stretch, and stacks labels three deep
  - figures: ry-verify panels share one scale and stack statuses in the
    order their labels name them; zero segments and phase banners go
  - figures: dmesg families lead with the finding ID, rank ties by severity,
    share one bar for boilerplate, and grade severity by gray level
  - figures: the journal timeline dates any span in ISO form; one entry
    gets a minute either side; captions name only what is drawn
  - figures: Figures 1 and 4 use the layout engine, so their text prints at
    the size of the others'
  - rules: an abnormal previous reset (thermal trip, watchdog, sync flood)
    is a MED finding; a reboot or power-off stays boilerplate
  - rules: kernel splats add the new WARN format, UBSAN, hung tasks,
    lockups, RCU stalls, and unhandled IRQs, not user GP faults or traces
  - rules: the kernel error classes read dmesg only, so a program that
    logs "Oops" to the journal is not taken for the kernel
  - rules: taint at module load, XFS corruption, NVMe I/O timeouts, and
    cgroup OOM kills are recognized; failed amdgpu firmware is no boilerplate
  - rules: a redacted unit name is INFO only for D-Bus activation; the
    unit-failed rule reads systemd's own lines only
  - rules: a mitigation that a FAIL or WARN record contradicts leaves the
    finding as it was; the note cites the last OK record
  - rules: a ry-verify log naming another CPU vendor than the bug report
    lowers no finding; its hardware-mismatch warning is no finding
  - rules: the link-down, JACK, and HID joystick texts match what the lines
    show; Secure Boot moves to boilerplate, as the health table shows it
  - analysis: the root mount ignores mounts after switch-root and of other
    UUIDs; a ring buffer that lost the boot yields no milestones
  - analysis: the kernel-start estimate skips messages printed twice;
    failure keywords add inflected forms and skip boilerplate lines
  - ry-verify: the phase banners (INFO: Static verification, ...) are no
    longer counted as notes or results
  - privacy: the machine ID, Bluetooth addresses after dev_, and
    identifiers touching an escape sequence are found and masked
  - privacy: a root hub's PCI address and the nil UUID are not identifiers;
    a serial with spaces is masked whole; IPv4 may end a sentence
  - privacy: the email pattern stays fast on long runs of word characters
  - actions: kernel errors use journalctl -p warning -o short-monotonic,
    with -b -1 for previous-boot evidence; a reset has its own action
  - actions: the ry-verify action quotes its suggested fixes, or where it
    stopped, in wrapped comments, and counts findings and sections apart
  - actions: the unit action names the failed unit and its boot; the
    ry-verify test sets the hardware-mismatch warning aside
  - actions: unit actions start with systemctl --failed; a path in any
    home directory reads ~/; the redaction comment names the placeholders
  - actions: rg checks find what the report finds in the raw JSONL too;
    no check holds a space
  - parsing: VJ numbers are lines of the ry-verify log, blank lines
    counted; the log is read once; deep nesting is a wrong-format input
  - parsing: inxi in IRC form keeps a wrapped value's leading digits; a
    line below an unread journal line no longer joins an earlier entry
  - parsing: without a capture time, journal years follow the ry-verify
    start; February 29 in a common year takes the year before
  - parsing: the date line accepts ChST and offsets with a colon; the
    package count reads repository/package lines only
  - cli: an unreadable font is a preflight failure (exit 3); fonts are
    also searched in $XDG_DATA_HOME/fonts
  - cli: SIGTERM and SIGHUP exit 130 and clean up as Ctrl-C does; a closed
    stderr no longer changes an exit code
  - cli: SOURCE_DATE_EPOCH is set for one build only; a numeric zone on the
    date line dates the PDF; figure warnings go to the verbose log
  - cli: start-up skips the slow xml.sax import, so Ctrl-C at launch exits
    without a traceback
  - docs: README covers check failures under exit 1, VJ numbering, the
    redaction checks, the new figures and appendix, and the fallbacks


7.0.0
-----

  - report: restructured as Summary, System health, Findings, ry-verify,
    Identifiers, Coverage, and Actions, with appendices A (inxi) and B
  - report: cards only for bug-report findings that call for action; INFO
    findings share one table; ry-verify detail appears once, in Section 4
  - report: a boot and capture timeline table, a journal timeline figure,
    and an error-class health row and cover tile, oops to core dump
  - report: the contents page holds the reading guide and severity levels;
    the "print edition" label is gone from the cover and the metadata
  - report: the register links each finding to its card or ry-verify rows,
    and the actions table links each action to its command block
  - report: the cover tiles show the system alone; ry-verify stays in the
    verdict and the count strip
  - report: health rows keep results in the result column and lead the
    evidence column with the source; temperatures read "cpu 49.6 C"
  - report: the identifier total names its counts; numeric columns and
    their headers align right; capture times since boot read as estimates
  - report: unit failures read "no journal entries" when the journal is
    empty; software inxi does not report is left out instead of "?"
  - report: counts of one read in the singular; the privacy card counts its
    bug-report lines; firmware and memory join the health table
  - report: a unit failure counts once; journal sizes read in entries;
    notes take no markup
  - report: the ry-verify health row splits the OK count by phase only when
    the phase records add up to it, as they do not after an interrupted run
  - report: inxi facts, the date line, and ry-verify header fields are
    sanitized like quotes, so no escape sequence reaches the PDF or stderr
  - layout: a heading or lead paragraph stays with the figure, card, or
    command block below it; section headings get room above their rule
  - layout: a table split twice keeps two rows on each side (ReportLab 5
    shifts the hold once per command list); a short table moves whole
  - layout: running headers name the section at the top of each page;
    contents columns end clear of their rule and inside the frame
  - layout: U+FFFD in condensed text is drawn in Plex Sans, as the
    condensed faces have no glyph for it
  - figures: the boot timeline ends at the first 30 s pause after the last
    milestone, so later lines cannot squeeze the boot; the caption counts them
  - figures: boot timeline bars narrow with the span shown, 0.01 to 5 s;
    count axes tick at whole numbers; legends sit below the plot
  - figures: row-scaled charts stop growing at 8 in, so a long rule roster
    cannot push a figure past its page
  - figures: the boot timeline keeps a busy short boot and its gap label
    inside the axes
  - ry-verify: OK, FAIL, WARN, and GEN_FAIL totals come from the footer, else
    the combined record, the phase records, or the result records themselves
  - ry-verify: a log without a footer reads "no footer" in the verdict, the
    key facts, and the health table instead of exit "?" with zero counts
  - parsing: inxi output in IRC form (no terminal on stdin) reads like
    terminal output; a wrapped inxi line joins even after a trailing key
  - parsing: lines are numbered at newlines only, as rg -n numbers them; a
    form feed or U+2028 inside a line no longer shifts later numbers
  - parsing: the capture time reads in every English locale glibc ships,
    en_GB included, and with numeric zone names such as +04
  - parsing: a ry-verify footer count that is not a whole number is a
    wrong-format input (exit 1) instead of a Python traceback
  - facts: the kernel release falls back to the header's uname line when the
    inxi block is missing
  - facts: firmware and NVMe data written are read in inxi 3.3's layout
    (Firmware: UEFI vendor:, written-units:) as well as the older one
  - rules: the x86 PCI host-bridge notice is boilerplate, not an
    unclassified "report a bug" line
  - rules: audit records reporting a denial or a failed result are listed
    as unclassified instead of counted as boilerplate
  - privacy: every field of every ry-verify record is scanned, so the home
    directory in the header's argv is counted and masked
  - quotes: C1 controls and the Unicode line separators show as U+FFFD; a
    clipped mitigation note keeps the path it cites
  - actions: rg checks cover every identifier class in Section 5, IPv4 and
    email included; file and unit names are quoted for fish, not POSIX sh
  - actions: commands name the inputs by full path, so the Quick Start's
    ry-verify log in its dated logs folder is found from any directory
  - actions: a failed unit is looked up in its boot and manager, with
    -b -1 or --user-unit; unclassified lines are printed by number
  - actions: core dumps get coredumpctl commands; an OOM kill joins the
    kernel errors to investigate
  - cli: --check also runs the parser cross-checks and names any that differ
  - cli: Ctrl-C during start-up exits 130 without a Python traceback
  - cli: SOURCE_DATE_EPOCH past 9999-12-31 is a usage error; importing the
    script leaves the caller's Ctrl-C handling alone
  - cli: a second build in the same process starts from a clean state
  - docs: Quick Start runs chmod +x first and finds the newest ry-verify log
    with fish's path sort; ls -t fails where ls is CachyOS's eza alias
  - docs: the Verify block finds the newest ry-verify log the same way
    instead of naming a verify.jsonl that does not exist
  - docs: README describes the new sections, figures, and appendices; its
    section references follow the new numbering


6.1.0
-----

  - rules: kernel taint in its bracketed [W]=WARN form; "NIC Link is Down";
    amdgpu notices without a PCI address
  - rules: the mitigation note needs the upper-case acronym; lines that
    merely contain "its" or "tsa" are left to the other rules
  - analysis: CPU vulnerabilities read from inxi's CPU block, one count per
    Type row; identifier lines counted once per line, not once per class
  - parsing: journal lines the parser cannot read are counted and reported
    by --check, --verbose, and the cross-checks table
  - figures: the boot timeline keeps its scale when ry-verify or the capture
    ran after 600 s; such captures are named in the caption
  - quotes: ANSI escapes are removed and control characters show as U+FFFD;
    report markup in quoted text is inert, so no log line can break a build
  - actions: ry-verify commands name ~/ry-install/ry-verify.fish; the
    unclassified-line search is case-insensitive
  - cli: a failed --check is reported as a check failure; start-up no longer
    needs a resolvable home directory


6.0.0
-----

  - cli: --bugreport and --verify are required; --assets is gone; --check
    parses both inputs; the default output is named by the capture date
  - cli: an output directory that is missing or read-only fails the build
    before the layout runs; Ctrl-C leaves no temporary file behind
  - analysis: findings, health, identifiers, coverage, and actions come from
    the inputs on each run; rules carry patterns and meanings, never results
  - parsing: cachyos-bugreport.sh sections and ry-verify JSONL phases; ERR
    records count as FAIL; totals are reconciled with the result records
  - parsing: UTF-8 with or without a byte order mark; blank JSONL lines are
    skipped; journal entries from before New Year take the previous year
  - figures: all seven drawn from the inputs; the boot timeline marks the
    ry-verify run and the capture; assets/ and its images removed
  - privacy: identifiers counted per class and masked in every quote;
    version strings are not IPv4 addresses; quotes stop at 400 characters
  - actions: fish commands with file and unit names quoted; a long command
    shrinks to fit on one line
  - layout: long tables split across pages under a repeated header, never
    fewer than two rows on a side; a title or heading never ends a page alone


5.0.0
-----

  - layout: rewrite on IBM Plex: cover dashboard, contents, finding cards,
    running headers, and page references settled in a second pass
  - figures: 2, 5, 6, 7, and 10 added; 1, 4, and 8 redrawn as vector; all
    grayscale for black-and-white printing
  - content: revision 32; wording edited, values and evidence unchanged
  - checks: content cross-checks and table geometry run before layout; an
    unsettled layout or an unresolved page reference fails the build
  - cli: --out, --fonts, --assets, --check, and --verbose; exits 0-3 and 130;
    --help and --version work without the dependencies
  - build: replaces build_report.py 4.4.1, which built revision 28; output
    is byte-reproducible, dated by SOURCE_DATE_EPOCH or the issue date
