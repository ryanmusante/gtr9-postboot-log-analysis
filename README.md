# gtr9-postboot-log-analysis

**Version 5.0.0** · [Changelog](CHANGELOG.md)

The GTR9 Pro post-boot log analysis — revision 32, covering the CachyOS captures of 2026-10-02 — as a 27-page print-edition PDF, with the script that builds it. `build_report.py` holds the report text and tables, draws seven vector figures with matplotlib, adds two raster figures from `assets/`, and lays out the pages with ReportLab.

## Quick Start

```fish
sudo pacman -S --needed python-reportlab python-matplotlib python-svglib python-pillow ttf-ibm-plex
chmod +x build_report.py
./build_report.py
```

The PDF is written to `gtr9-postboot-log-analysis-2026-10-02-print.pdf` in the current directory, and its absolute path is printed on stdout. Instead of the Python packages, `uv run build_report.py` installs the dependencies listed at the top of the script; the fonts still come from `ttf-ibm-plex`.

## Usage

- `--out PATH` — output PDF; default `./gtr9-postboot-log-analysis-2026-10-02-print.pdf`
- `--fonts DIR` — directory with the 9 IBM Plex TTF files; default search `/usr/share/fonts/TTF`, `/usr/share/fonts/truetype/ibm-plex`, `~/.local/share/fonts`
- `--assets DIR` — directory with `fig03_dmesg.png` and `fig09_prevboot.png`; default `assets/` beside the script
- `--check` — run the preflight only and build nothing
- `--verbose` — report the content checks, fonts, figures, each layout pass, and the result on stderr
- `--version`, `--help` — version and usage; both work before the Python dependencies are loaded

## Exit Codes

- `0` — built, or the preflight passed under `--check`
- `1` — build failed: a content cross-check, an unsettled layout, an unresolved page reference, or an unwritable output path
- `2` — usage error: an unknown option, `--out` naming a directory, or a `SOURCE_DATE_EPOCH` that is not a whole number
- `3` — preflight failed: a Python module, font file, or asset is missing; the message names the package
- `130` — interrupted with Ctrl-C; nothing is written

## Output

- **Pages** — 27 US Letter pages: cover dashboard, contents, Sections 1–11, and Appendices A and B.
- **Type** — IBM Plex Sans, Sans Condensed, and Mono; all 9 faces are embedded.
- **Print** — pure grayscale; figures tell series apart by gray level, outline, and marker shape.
- **Navigation** — 57 bookmarks and 138 internal links from the contents, page references, and finding cards.
- **Reproducible** — the same inputs, fonts, and library versions give a byte-identical PDF, dated by the issue date (2026-10-04) unless `SOURCE_DATE_EPOCH` is set.
- **Safe writes** — the PDF goes to a temporary file beside the target and is renamed into place; a failed or interrupted build leaves nothing behind.

## How It Works

- **Content** — report text, tables, finding cards, and captions are constants in the CONTENT section of `build_report.py`.
- **Checks** — before any layout, cross-checks compare the counts the report states more than once: register and cards, cover and tables, figures and tables, prose and tables. Every table must also fill the page width. Any mismatch fails the build.
- **Figures** — matplotlib draws seven charts with text as paths, and svglib embeds them as vector drawings; Figures 1, 5, and 8 are computed from the tables. Figures 3 and 9 are the revision-28 images in `assets/`, because their per-line data is not in the report.
- **Layout** — ReportLab repeats the layout until page references, contents, and running headers stop changing: two passes in practice, six at most.

## Editing

- Change text in the CONTENT section, then rebuild; page references and the contents follow.
- For a new revision, raise `REV` and set `ISSUED`, which is also the embedded PDF date.
- Figure data sits in the FIGURES section beside the function that draws it; a check names any table it no longer matches.

## Verify

```fish
./build_report.py --check
./build_report.py --out /tmp/a.pdf; and cmp /tmp/a.pdf gtr9-postboot-log-analysis-2026-10-02-print.pdf; and echo matches
pdfinfo /tmp/a.pdf
pdffonts /tmp/a.pdf
qpdf --check /tmp/a.pdf
ruff check build_report.py; and ruff format --check build_report.py; and ty check build_report.py
```

The `cmp` line matches only with the library versions listed under Requirements. `pdfinfo` reports 27 pages and revision 32 in the title, and every `pdffonts` row reads `emb yes`. `pdfinfo` and `pdffonts` come from `poppler`, `qpdf` from `qpdf`; `ruff` reads `ruff.toml`.

## Files

- `gtr9-postboot-log-analysis-2026-10-02-print.pdf` — the report built by this version, revision 32
- `build_report.py` — content, figures, layout, checks, and command line in one script
- `assets/fig03_dmesg.png`, `assets/fig09_prevboot.png` — Figures 3 and 9
- `ruff.toml` — lint and format settings
- `CHANGELOG.md`, `LICENSE`

## Requirements

- Python 3.12 or newer; tested on 3.14.7
- ReportLab 5.0.1, matplotlib 3.11.2, svglib 2.2.0, and Pillow 12.3.0 as tested; the script requires at least ReportLab 5.0, matplotlib 3.11, svglib 2.2, and Pillow 12
- IBM Plex 6.4.0 TTF files (`ttf-ibm-plex`)
- To lint: ruff 0.16 and ty 0.0.84 as tested

## License

MIT — see [LICENSE](LICENSE).
