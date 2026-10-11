Changes for gtr9-postboot-log-analysis
======================================

Newest first. Versioning is MAJOR.MINOR.PATCH.

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
