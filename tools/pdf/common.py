"""Shared pieces for the PDF documents in docs/pdf/.

Every document is built from the data center's own data, so the numbers in a
PDF are the numbers the code holds: `seeded_center()` loads the bundled data
files into a throwaway root and hands back a DataCenter to read from.

Documents are written as HTML and printed by headless Chromium, which gives
real typography and tables with nothing to install. The one exception is the
fillable employment application, which needs reportlab for its form fields.
"""

from __future__ import annotations

import contextlib
import html
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "docs" / "pdf"
SRC_DIR = REPO / "tools" / "pdf" / "build"

sys.path.insert(0, str(REPO))

from datacenter import DATA_CENTER_NAME, DATA_DIR, VERSION, DataCenter, load_all  # noqa: E402

CHROMIUM_CANDIDATES = (
    os.environ.get("CHROMIUM", ""),
    "/opt/pw-browsers/chromium",
    "chromium",
    "chromium-browser",
    "google-chrome",
)

#: Every document carries an issue date. Fixed rather than "today" so that
#: rebuilding a document that has not changed produces the same file.
ISSUED = date(2026, 10, 18)

TEMPLATE_NOTICE = (
    "Template for review. This document is a starting point, not legal advice. "
    "Have it reviewed by a licensed attorney in your jurisdiction before you sign or rely on it."
)

ILLUSTRATIVE_NOTICE = (
    "Figures are illustrative. Jurisdictions and amounts are invented for the sample "
    "data set and are not legal, tax or payroll advice."
)


# -- data -----------------------------------------------------------------------

@contextlib.contextmanager
def seeded_center() -> Iterator[DataCenter]:
    """A DataCenter loaded with the bundled data, in a temporary root."""
    with tempfile.TemporaryDirectory() as tmp:
        center = DataCenter(root=Path(tmp) / "storage")
        try:
            center.provision()
            load_all(center, DATA_DIR)
            yield center
        finally:
            center.close()


def records(center: DataCenter, dataset: str) -> list[dict[str, Any]]:
    return [record["payload"] for record in center.list_records(dataset)]


# -- formatting -----------------------------------------------------------------

def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def money(value: Any, *, cents: bool = True) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return esc(value)
    return f"${amount:,.2f}" if cents else f"${amount:,.0f}"


def table(columns: list[str], rows: list[list[str]], *, numeric: set[int] = frozenset(),
          footer: list[str] | None = None, widths: list[str] | None = None) -> str:
    """An HTML table. Cells are trusted HTML: escape values before passing them."""
    colgroup = ""
    if widths:
        colgroup = "<colgroup>" + "".join(f'<col style="width:{w}">' for w in widths) + "</colgroup>"
    head = "".join(
        f'<th class="num">{esc(c)}</th>' if i in numeric else f"<th>{esc(c)}</th>"
        for i, c in enumerate(columns)
    )
    body = "".join(
        "<tr>" + "".join(
            f'<td class="num">{cell}</td>' if i in numeric else f"<td>{cell}</td>"
            for i, cell in enumerate(row)
        ) + "</tr>"
        for row in rows
    )
    foot = ""
    if footer:
        foot = "<tfoot><tr>" + "".join(
            f'<td class="num">{cell}</td>' if i in numeric else f"<td>{cell}</td>"
            for i, cell in enumerate(footer)
        ) + "</tr></tfoot>"
    return f"<table>{colgroup}<thead><tr>{head}</tr></thead><tbody>{body}</tbody>{foot}</table>"


def blank(width: str = "100%") -> str:
    """A line to write on, for templates and forms."""
    return f'<span class="blank" style="width:{width}"></span>'


# -- page -----------------------------------------------------------------------

PRINT_CSS = """
@page { size: Letter; margin: 0.75in 0.75in 0.6in 0.75in; }
:root { --ink:#17201f; --muted:#56635f; --rule:#cfd6d4; --accent:#0f5f68; --tint:#eaf2f2; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; color: var(--ink); font: 10.5pt/1.5 "Liberation Sans", Arial, Helvetica, sans-serif; }
h1 { font: 700 22pt/1.15 "Liberation Sans", Arial, sans-serif; margin: 0 0 6pt; letter-spacing: -0.01em; }
h2 { font: 700 13pt/1.25 "Liberation Sans", Arial, sans-serif; color: var(--accent);
     margin: 20pt 0 6pt; padding-bottom: 3pt; border-bottom: 1.2pt solid var(--accent);
     break-after: avoid; }
h3 { font: 700 11pt/1.3 "Liberation Sans", Arial, sans-serif; margin: 12pt 0 4pt; break-after: avoid; }
p { margin: 0 0 7pt; orphans: 3; widows: 3; }
ul, ol { margin: 0 0 8pt; padding-left: 18pt; }
li { margin: 0 0 2pt; }
a { color: var(--accent); text-decoration: none; }
code, .mono { font-family: "Liberation Mono", "DejaVu Sans Mono", monospace; font-size: 9pt; }
.cover { border-bottom: 2pt solid var(--accent); padding-bottom: 12pt; margin-bottom: 14pt; }
.eyebrow { font: 700 8.5pt/1 "Liberation Sans", Arial, sans-serif; letter-spacing: 0.12em;
           text-transform: uppercase; color: var(--accent); margin: 0 0 8pt; }
.subtitle { font-size: 12pt; color: var(--muted); margin: 0; }
.meta { font-size: 9pt; color: var(--muted); margin-top: 8pt; }
.notice { background: var(--tint); border-left: 3pt solid var(--accent); padding: 7pt 10pt;
          font-size: 9pt; color: var(--ink); margin: 0 0 12pt; break-inside: avoid; }
table { width: 100%; border-collapse: collapse; margin: 4pt 0 10pt; font-size: 9pt; }
thead { display: table-header-group; }
tr { break-inside: avoid; }
th { text-align: left; font-weight: 700; color: var(--muted); border-bottom: 1pt solid var(--ink);
     padding: 4pt 6pt; vertical-align: bottom; }
td { padding: 3.5pt 6pt; border-bottom: 0.5pt solid var(--rule); vertical-align: top; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
tfoot td { font-weight: 700; border-top: 1pt solid var(--ink); border-bottom: none; }
.grid { display: flex; flex-wrap: wrap; gap: 8pt; margin: 6pt 0 10pt; }
.stat { flex: 1 1 120pt; border: 0.75pt solid var(--rule); border-radius: 4pt; padding: 7pt 9pt; }
.stat b { display: block; font-size: 15pt; line-height: 1.2; }
.stat span { font-size: 8.5pt; color: var(--muted); }
.clause { break-inside: avoid-page; }
.blank { display: inline-block; border-bottom: 0.75pt solid var(--ink); height: 13pt;
         vertical-align: bottom; }
.sign { display: flex; gap: 24pt; margin-top: 18pt; break-inside: avoid; }
.sign > div { flex: 1; }
.sign .line { border-bottom: 0.75pt solid var(--ink); height: 26pt; }
.sign .label { font-size: 8.5pt; color: var(--muted); padding-top: 2pt; }
.page-break { break-before: page; }
figure { margin: 6pt 0 12pt; break-inside: avoid; }
figure img { width: 100%; border: 0.75pt solid var(--rule); }
figcaption { font-size: 8.5pt; color: var(--muted); margin-top: 3pt; }
/* A footer on every page. Chromium repeats a fixed element on each printed
   page only inside the page box, where text would run underneath it, so the
   whole document sits in a one-cell table whose tfoot -- repeated on every page
   -- reserves the room, and the fixed footer is drawn over that room. */
table.page { width: 100%; border-collapse: collapse; margin: 0; font-size: inherit; }
table.page > tbody > tr, table.page > tfoot > tr { break-inside: auto; }
table.page > tbody > tr > td, table.page > tfoot > tr > td { padding: 0; border: 0; }
.footer-space { height: 0.42in; }
.footer { position: fixed; bottom: 0; left: 0; right: 0; height: 0.3in; font-size: 7.5pt;
          color: var(--muted); border-top: 0.5pt solid var(--rule); padding-top: 4pt;
          display: flex; justify-content: space-between; background: #fff; }
.muted { color: var(--muted); }
.small { font-size: 9pt; }
"""


def document(title: str, body: str, *, eyebrow: str, subtitle: str = "",
             notice: str = "", footer_note: str = "") -> str:
    """A complete printable HTML document in the shared style."""
    notice_html = f'<div class="notice">{esc(notice)}</div>' if notice else ""
    footer = esc(footer_note or f"{DATA_CENTER_NAME} — {title}")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{esc(title)}</title>
<style>{PRINT_CSS}</style></head><body>
<div class="footer"><span>{footer}</span><span>Issued {ISSUED:%B %-d, %Y} · v{esc(VERSION)}</span></div>
<table class="page"><tfoot><tr><td><div class="footer-space"></div></td></tr></tfoot>
<tbody><tr><td>
<header class="cover">
  <p class="eyebrow">{esc(eyebrow)}</p>
  <h1>{esc(title)}</h1>
  {f'<p class="subtitle">{esc(subtitle)}</p>' if subtitle else ''}
  <p class="meta">{esc(DATA_CENTER_NAME)}</p>
</header>
{notice_html}
{body}
</td></tr></tbody></table>
</body></html>"""


# -- rendering ------------------------------------------------------------------

def chromium() -> str:
    for candidate in CHROMIUM_CANDIDATES:
        if candidate and (Path(candidate).exists() or shutil.which(candidate)):
            return candidate
    raise RuntimeError("no Chromium found; set CHROMIUM to its path")


def render(name: str, html_text: str) -> Path:
    """Write the HTML source and print it to docs/pdf/<name>.pdf."""
    SRC_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    source = SRC_DIR / f"{name}.html"
    source.write_text(html_text, encoding="utf-8")
    target = OUT_DIR / f"{name}.pdf"
    subprocess.run(
        [chromium(), "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
         "--run-all-compositor-stages-before-draw", "--virtual-time-budget=5000",
         f"--print-to-pdf={target}", source.as_uri()],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
    )
    if not target.exists() or target.stat().st_size < 1000:
        raise RuntimeError(f"Chromium did not produce {target}")
    return target
