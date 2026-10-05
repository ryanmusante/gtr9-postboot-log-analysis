Changes for gtr9-postboot-log-analysis
======================================

Newest first. Versioning is MAJOR.MINOR.PATCH.

6.0.0
-----

  - cli: --bugreport and --verify are required; --assets is gone; --check
    parses both inputs; the default output is named by the capture date
  - analysis: findings, health, identifiers, coverage, and actions come from
    the inputs on each run; rules carry patterns and meanings, never results
  - parsing: cachyos-bugreport.sh sections and ry-verify JSONL phases; the
    ry-verify totals are reconciled with its own result records
  - figures: all seven drawn from the inputs; assets/ and its images removed
  - privacy: identifiers counted per class and masked in every quote


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
