Changes for gtr9-postboot-log-analysis
======================================

Newest first. Versioning is MAJOR.MINOR.PATCH.

7.0.0
-----

  - ry-verify: OK, FAIL, WARN, and GEN_FAIL totals come from the footer, else
    the combined record, the phase records, or the result records themselves
  - ry-verify: a log without a footer reads "no footer" on the cover, in the
    key facts, and in the health table instead of exit "?" with zero counts
  - cli: --check also runs the parser cross-checks and names any that differ
  - facts: the kernel release falls back to the header's uname line when the
    inxi block is missing
  - figures: row-scaled charts stop growing at 8 in, so a long rule roster
    cannot push a figure past its page
  - figures: the boot timeline keeps a busy short boot and its gap label
    inside the axes; Figure 3 shortens long finding lists
  - parsing: inxi output in IRC form (no terminal on stdin) reads like
    terminal output; a wrapped inxi line joins even after a trailing key
  - privacy: every field of every ry-verify record is scanned, so the home
    directory in the header's argv is counted and masked
  - rules: the x86 PCI host-bridge notice is boilerplate, not an
    unclassified "report a bug" line
  - report: unit failures read "no journal entries" when the journal is
    empty; software inxi does not report is left out instead of "?"
  - actions: one rg check per identifier class in Section 5, IPv4 and
    email included; file and unit names are quoted for fish, not POSIX sh
  - cli: Ctrl-C during start-up exits 130 without a Python traceback


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
