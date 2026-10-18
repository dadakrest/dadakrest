"""AI Data Center: the company overview.

    python3 tools/pdf/build.py ai_data_center
    python3 tools/pdf/ai_data_center.py

Describes the business and the software it runs on. Every count, total and
average is computed from the bundled data when the document is built; the
prose around the figures is the only hand-written part.
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    ILLUSTRATIVE_NOTICE,
    REPO,
    document,
    esc,
    money,
    records,
    render,
    seeded_center,
    table,
)

from datacenter import DATA_CENTER_NAME, DATA_DIR, loader, vectors  # noqa: E402
from datacenter.cli import build_parser  # noqa: E402
from datacenter.config import BUCKET_SPACE, DATABASES  # noqa: E402
from datacenter.core import DataCenter  # noqa: E402
from datacenter.plans import FEE_TABLES, cost_all  # noqa: E402
from datacenter.portal import DEFAULT_HOST, DEFAULT_PORT, NAV  # noqa: E402

NAME = "ai-data-center"
TITLE = "AI Data Center"

#: A GPU with this status is installed in a host. Every other status (spare,
#: out for repair, retired) is a card on the shelving, not in a rack.
INSTALLED = "in-service"

#: How the prose names a card that is not installed, by its status.
SHELF_STATUS = {"spare": "spare", "repair": "out for repair", "retired": "retired"}

DASH = '<span class="muted">&ndash;</span>'

# The architecture figure is drawn at its printed size: the text column is
# 7 in (504 pt) wide and one SVG unit is one point, so a font size in the
# figure is its size on paper. Nothing in it is set smaller than FIG_MIN_TYPE.
FIG_WIDTH = 504.0
FIG_MIN_TYPE = 7.0
FIG_SANS = "Liberation Sans, Arial, Helvetica, sans-serif"
FIG_MONO = "Liberation Mono, DejaVu Sans Mono, monospace"
FIG_INK = "#17201f"
FIG_MUTED = "#56635f"
FIG_STYLES = {
    #        fill       stroke     title
    "input": ("#E4ECF4", "#5C7C9E", "#1E3A55"),
    "facade": ("#F7EEDC", "#B08D4B", "#5A431A"),
    "embedder": ("#FBF6EA", "#B08D4B", "#5A431A"),
    "group": ("#F6F9F6", "#9DB5A2", "#24482C"),
    "database": ("#E5F0E7", "#5F8F6A", "#24482C"),
    "audit": ("#ECE7F3", "#7B6CA3", "#3E3160"),
    "output": ("#EFEFEF", "#7A7A7A", "#2B2B2B"),
}
FIG_ARROW = "#4A4A4A"
FIG_AUDIT = "#7B6CA3"

#: How many times a government fee charged each `period` falls due in a year.
#: One-time fees are counted separately, so they recur zero times.
TIMES_PER_YEAR = {"one-time": 0, "annual": 1, "quarterly": 4, "monthly": 12}

#: Display names for the fee tables a plan draws on, in FEE_TABLES order.
FEE_TABLE_LABELS = {
    "government_fees": "Government",
    "service_fees": "Service",
    "employment_fees": "Employment",
}

NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven",
                "eight", "nine", "ten", "eleven", "twelve")

EXTRA_CSS = """
/* A data table's totals row belongs after its last row only. Chromium repeats a
   tfoot on every page a table spans, which would print "Total" under a partial
   list. The page wrapper's own tfoot (table.page) must keep repeating. */
table:not(.page) > tfoot { display: table-row-group; }
.cols { display: flex; gap: 16pt; align-items: flex-start; }
.cols > div { flex: 1 1 0; min-width: 0; }
.cols td { padding-top: 2.5pt; padding-bottom: 2.5pt; }
.keep { break-inside: avoid; }
.glance { flex-wrap: nowrap; gap: 6pt; }
.glance .stat { flex: 1 1 0; min-width: 0; padding: 6pt 7pt; }
.glance .stat span { display: block; line-height: 1.3; }
p code, li code { white-space: nowrap; }
.nowrap { white-space: nowrap; }
pre.cli { font-family: "Liberation Mono", "DejaVu Sans Mono", monospace; font-size: 8.5pt;
          line-height: 1.45; background: var(--tint); border-radius: 3pt; padding: 7pt 10pt;
          margin: 4pt 0 10pt; white-space: pre-wrap; break-inside: avoid; }
td code { font-size: 8.5pt; }
td .sub { display: block; color: var(--muted); font-size: 8.5pt; }
.section-notice { margin-top: 2pt; }
figure.arch svg { display: block; width: 100%; height: auto; }
/* A long table may run on to the next page, but its heading, introduction
   and first rows stay together, and the header row repeats. */
.lead-rows > p { break-after: avoid; }
.lead-rows tbody tr:nth-child(2), .lead-rows tbody tr:nth-child(3) { break-before: avoid; }
"""


# -- small helpers ----------------------------------------------------------------

def words(n: int) -> str:
    """Small counts as words, as prose writes them; larger ones as digits."""
    return NUMBER_WORDS[n] if 0 <= n < len(NUMBER_WORDS) else f"{n:,}"


def times_word(n: int) -> str:
    """1 -> 'once', 2 -> 'twice', 4 -> 'four times'."""
    return {1: "once", 2: "twice"}.get(n, f"{words(n)} times")


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def join_and(items: list[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def long_date(value: str) -> str:
    """'2026-06-01' or '2026-06-01T04:05:00Z' as 'June 1, 2026'."""
    day = datetime.fromisoformat(value.replace("Z", "+00:00")).date() if "T" in value \
        else date.fromisoformat(value)
    return f"{day:%B %-d, %Y}"


def number(value: Any, label: str) -> float:
    """A numeric field that must be present. Zero is a value, not a gap."""
    if value is None:
        raise ValueError(f"missing number: {label}")
    return float(value)


def stat(value: str, label: str) -> str:
    return f'<div class="stat"><b>{value}</b><span>{label}</span></div>'


def first_seen(values: list[str]) -> list[str]:
    """Distinct values in the order they first appear."""
    return list(dict.fromkeys(values))


def money_or_dash(values: list[float]) -> str:
    """The sum of some fees, or a dash when there were none to add up.

    Tests the list, not the sum: a real $0.00 fee still prints as $0.00."""
    return DASH if not values else money(sum(values))


def requires_python() -> str:
    """The minimum Python version, read from pyproject.toml."""
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'requires-python\s*=\s*"\s*>=\s*([0-9.]+)\s*"', text)
    if not match:
        raise ValueError("pyproject.toml does not state requires-python as >=X.Y")
    return match.group(1)


# -- sections ---------------------------------------------------------------------

def at_a_glance(services, gpus, docs, plans, gov) -> str:
    in_service = sum(1 for g in gpus if g.get("status") == "in-service")
    kb_docs = sum(1 for d in docs if str(d.get("source", "")).startswith("kb/"))
    categories = len(first_seen([s["category"] for s in services]))
    return (
        '<div class="grid glance">'
        + stat(esc(len(DATABASES)), "databases")
        + stat(esc(len(services)), f"services in {esc(categories)} categories")
        + stat(esc(len(gpus)), f"GPUs, {esc(in_service)} in service")
        + stat(esc(kb_docs), "knowledge-base articles")
        + stat(esc(len(plans)), "costed business plans")
        + stat(esc(len(gov)), "government fees")
        + "</div>"
    )


def section_business(services: list[dict[str, Any]]) -> str:
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for service in services:
        by_category[service["category"]].append(service)
    order = first_seen([s["category"] for s in services])

    rows = []
    for category in order:
        items = by_category[category]
        names = "; ".join(esc(s["service"]) for s in items)
        units = ", ".join(esc(u) for u in first_seen([s["unit"] for s in items]))
        rows.append([f"<b>{esc(category.capitalize())}</b>", esc(len(items)), names, units])

    return f"""
<h2>1. What the business does</h2>
<p>{esc(DATA_CENTER_NAME)} is an AI data center services company. Clients rent GPU capacity
to train and serve models, send inference traffic to hosted models, keep data in tiered
storage, place their own hardware in its racks, and buy support from its staff. The rate
card, the <code>service_fees</code> dataset, lists {esc(len(services))} services in
{esc(words(len(order)))} categories:</p>
<div class="keep">
{table(["Category", "Services", "What is sold", "Billed per"], rows, numeric={1},
       widths=["14%", "10%", "55%", "21%"],
       footer=["Total", esc(len(services)), "", ""])}
</div>
<p>The company runs its own operations on the software this document describes: one
program that keeps {esc(words(len(DATABASES)))} databases, for
{esc(join_and([spec.title.lower() for spec in DATABASES]))}.</p>
"""


def section_databases(status: dict[str, Any]) -> str:
    rows_by_key = {db["key"]: db.get("rows", {}) for db in status["databases"]}
    rows = []
    total_rows = 0
    for index, spec in enumerate(DATABASES, start=1):
        counts = rows_by_key.get(spec.key, {})
        tables = []
        for name in spec.tables:
            count = counts.get(name)
            if count is None:
                tables.append(f"<code>{esc(name)}</code>")
            else:
                total_rows += count
                tables.append(f"<code>{esc(name)}</code> ({esc(f'{count:,}')})")
        rows.append([
            esc(index),
            f"<b>{esc(spec.title)}</b><span class=\"sub\"><code>{esc(spec.filename)}</code></span>",
            esc(spec.purpose),
            "<br>".join(tables),
        ])
    return f"""
<h2>2. The {esc(words(len(DATABASES)))} databases</h2>
<p>Each database is its own SQLite file under the storage root, so one domain can be backed
up, inspected or replaced without touching the others. The order below is the order they are
provisioned in. The numbers in brackets are the rows each table holds after
<code>python -m datacenter seed</code> fills an empty root, which loads the bundled data and
runs the jobs that loading queues. The audit count grows with every later command.</p>
{table(["#", "Database", "Purpose", "Tables (rows)"], rows, numeric={0},
       widths=["5%", "22%", "41%", "32%"],
       footer=["", "Total", "", f"{esc(f'{total_rows:,}')} rows"])}
"""


# -- the architecture figure --------------------------------------------------------

def text_width(value: str, size: float, *, bold: bool = False, mono: bool = False) -> float:
    """A generous estimate of a line's printed width in points.

    Liberation Mono advances 0.6 em per character. Liberation Sans has Arial's
    metrics; its advance widths are grouped into classes here, each rounded up
    to the widest member, so a line that passes this check fits on paper.
    """
    if mono:
        return len(value) * 0.6 * size
    em = 0.0
    for ch in value:
        if ch in " .,:;'!|()[]/\\-ijlftrI·":
            em += 0.39 if bold else 0.34
        elif ch in "mwMW%@":
            em += 0.95
        elif ch.isupper() or ch in "&→":
            em += 0.78
        else:
            em += 0.62 if bold else 0.57
    return em * size


def wrap(value: str, size: float, room: float, *, bold: bool = False) -> list[str]:
    """Break a sentence into lines no wider than `room` points."""
    lines: list[str] = []
    for word in value.split():
        if lines and text_width(f"{lines[-1]} {word}", size, bold=bold) <= room:
            lines[-1] += f" {word}"
        else:
            if text_width(word, size, bold=bold) > room:
                raise ValueError(f"figure: {word!r} is wider than its box")
            lines.append(word)
    return lines


def bucket_space() -> tuple[str, str]:
    """The embedder's bucket space as (plain text, SVG markup with an exponent)."""
    exponent = (BUCKET_SPACE + 1).bit_length() - 1
    sup = f'2<tspan dy="-2.6" font-size="{FIG_MIN_TYPE:g}">{exponent}</tspan>'
    if BUCKET_SPACE == 2**exponent - 1:
        return f"2{exponent} − 1", sup + '<tspan dy="2.6"> &#8722; 1</tspan>'
    if BUCKET_SPACE == 2**exponent:
        return f"2{exponent}", sup + '<tspan dy="2.6"></tspan>'
    return f"{BUCKET_SPACE:,}", esc(f"{BUCKET_SPACE:,}")


class Figure:
    """An SVG drawn in points, refusing any text that is too small or too wide."""

    def __init__(self) -> None:
        self.items: list[str] = []

    def box(self, x: float, y: float, w: float, h: float, style: str, *,
            dashed: bool = False, radius: float = 4.0) -> None:
        fill, stroke, _ = FIG_STYLES[style]
        dash = ' stroke-dasharray="3 2"' if dashed else ""
        self.items.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                          f'rx="{radius:g}" fill="{fill}" stroke="{stroke}" '
                          f'stroke-width="0.8"{dash}/>')

    def text(self, x: float, y: float, value: str, *, size: float = 7.5, bold: bool = False,
             mono: bool = False, fill: str = FIG_INK, anchor: str = "start",
             room: float | None = None, markup: str | None = None) -> None:
        """One line of text. `markup`, when given, is drawn in place of the
        escaped value; the value is still what is measured."""
        if size < FIG_MIN_TYPE:
            raise ValueError(f"figure: {value!r} is set at {size}pt, below {FIG_MIN_TYPE}pt")
        if room is not None and text_width(value, size, bold=bold, mono=mono) > room:
            raise ValueError(f"figure: {value!r} does not fit in {room:.0f}pt")
        family = FIG_MONO if mono else FIG_SANS
        weight = ' font-weight="700"' if bold else ""
        self.items.append(f'<text x="{x:.1f}" y="{y:.1f}" font-family="{family}" '
                          f'font-size="{size:g}"{weight} fill="{fill}" text-anchor="{anchor}">'
                          f'{markup if markup is not None else esc(value)}</text>')

    def arrow(self, *points: tuple[float, float], color: str = FIG_ARROW) -> None:
        path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(points))
        self.items.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="0.9" '
                          f'stroke-linejoin="round" marker-end="url(#arch-arrow)"/>')

    def line(self, *points: tuple[float, float], color: str = FIG_ARROW) -> None:
        path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(points))
        self.items.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="0.9"/>')

    def dot(self, x: float, y: float) -> None:
        self.items.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.6" fill="{FIG_ARROW}"/>')

    def card(self, x: float, y: float, w: float, style: str, title: str, *,
             right: str = "", mono: str = "", lines: list[str] | None = None,
             markup: dict[int, str] | None = None, dashed: bool = False,
             title_size: float = 8.5) -> float:
        """A titled box: an optional file name on the right of the title, an
        optional monospace line, then lines of text. Returns its bottom edge."""
        pad, lead = 6.0, 9.4
        lines = lines or []
        height = 12.5 + (10.0 if mono else 0.0) + lead * len(lines) + 5.5
        self.box(x, y, w, height, style, dashed=dashed)
        room = w - 2 * pad
        title_room = room - (text_width(right, 7.0, mono=True) + 6 if right else 0)
        self.text(x + pad, y + 12, title, size=title_size, bold=True,
                  fill=FIG_STYLES[style][2], room=title_room)
        if right:
            self.text(x + w - pad, y + 12, right, size=7.0, mono=True, fill=FIG_MUTED,
                      anchor="end")
        baseline = y + 12
        if mono:
            baseline += 10.0
            self.text(x + pad, baseline, mono, size=7.5, mono=True, fill=FIG_MUTED, room=room)
        for index, line in enumerate(lines):
            baseline += lead
            self.text(x + pad, baseline, line, room=room,
                      markup=(markup or {}).get(index))
        return y + height

    def svg(self, height: float, label: str) -> str:
        defs = ('<defs><marker id="arch-arrow" markerUnits="userSpaceOnUse" markerWidth="5.5" '
                'markerHeight="4.4" refX="5.5" refY="2.2" orient="auto">'
                f'<path d="M0,0 L5.5,2.2 L0,4.4 Z" fill="{FIG_ARROW}"/></marker></defs>')
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {FIG_WIDTH:g} {height:.1f}" '
                f'width="{FIG_WIDTH:g}pt" height="{height:.1f}pt" role="img" '
                f'aria-label="{esc(label)}">{defs}{"".join(self.items)}</svg>')


def architecture_svg(command_count: int) -> str:
    """How data gets in, where it lives and what comes out, drawn from the code.

    The database boxes come from datacenter.config, the input files from the
    loader, the embedder's name and bucket space from datacenter.vectors, and
    every DataCenter method the figure names is checked to exist.
    """
    named = ("provision", "add_document", "search", "run_next_job", "log", "backup",
             "status", "recent_events", "verify")
    missing = [name for name in named if not callable(getattr(DataCenter, name, None))]
    if missing:
        raise ValueError(f"the figure names DataCenter methods that are gone: {missing}")

    count = len(DATABASES)
    fig = Figure()
    top, gap = 16.0, 13.0
    # A 1 pt margin all round keeps the outer strokes inside the drawing.
    ax, aw = 1.0, 123.0          # inputs
    bx, bw = 154.0, 176.0        # the DataCenter class
    cx = 358.0                   # the databases
    cw = FIG_WIDTH - 1.0 - cx
    bus = 344.0

    for x, label in ((ax, "INPUTS"), (bx, "FACADE"), (cx, "STORAGE")):
        fig.text(x, 9, label, size=7, bold=True, fill=FIG_MUTED)

    # Inputs: data files, the loader, the command line.
    kinds = [name.removesuffix(".json") for name in loader.FILES]
    folders = [kind for kind in kinds if (DATA_DIR / kind).is_dir()]
    data_lines = wrap(f"{words(len(kinds)).capitalize()} kinds, one JSON array each: "
                      f"{', '.join(kinds)}.", 7.5, aw - 12)
    if folders:
        data_lines += wrap("Split into part files: "
                           + ", ".join(f"{kind}/" for kind in folders) + ".", 7.5, aw - 12)
    data_bottom = fig.card(ax, top, aw, "input", "Data files (JSON)",
                           mono=f"{DATA_DIR.parent.name}/{DATA_DIR.name}/", lines=data_lines)
    loader_top = data_bottom + gap
    fig.arrow((ax + aw / 2, data_bottom), (ax + aw / 2, loader_top))
    loader_bottom = fig.card(
        ax, loader_top, aw, "input", "Loader", right="loader.py",
        lines=wrap("validate() checks every file before anything is written; load_all() "
                   "then upserts, so a refill never duplicates.", 7.5, aw - 12))
    cli_top = loader_bottom + gap
    cli_bottom = fig.card(
        ax, cli_top, aw, "input", "CLI / Python API", mono="python -m datacenter",
        lines=wrap(f"{command_count} commands, listed in section 8; or DataCenter(root) "
                   "from Python.", 7.5, aw - 12))

    # The DataCenter class, with the embedder it calls.
    rows = [
        ("provision", f"create {count} files, apply schemas"),
        ("writes", "upsert on natural keys"),
        ("search", "rank by IDF-weighted cosine"),
        ("jobs", "claim, run, record each run"),
        ("audit", "log() after every write"),
        ("backup", f"copy all {count} files + a manifest"),
    ]
    rows_top = top + 32.0
    embed_lines = [
        "words → BLAKE2b hash → bucket",
        "bucket space: {space}",
        "stored: sparse map, bucket → weight",
        "weight 1 + log(count), unit length",
        "query: terms weighted by BM25 IDF",
        "score: cosine over shared buckets",
    ]
    space_text, space_markup = bucket_space()
    embed_top = rows_top + 11.0 * (len(rows) - 1) + 12.0
    embed_height = 12.5 + 10.0 + 9.4 * len(embed_lines) + 5.5
    facade_bottom = embed_top + embed_height + 8.0
    fig.box(bx, top, bw, facade_bottom - top, "facade")
    fig.text(bx + 8, top + 14, "DataCenter", size=10, bold=True, fill=FIG_STYLES["facade"][2],
             room=bw - 16 - text_width("datacenter/core.py", 7.5, mono=True) - 6)
    fig.text(bx + bw - 8, top + 14, "datacenter/core.py", size=7.5, mono=True, fill=FIG_MUTED,
             anchor="end")
    fig.line((bx + 8, top + 20.5), (bx + bw - 8, top + 20.5), color=FIG_STYLES["facade"][1])
    for index, (label, what) in enumerate(rows):
        y = rows_top + 11.0 * index
        purple = label == "audit"
        fig.text(bx + 8, y, label, bold=True, room=44,
                 fill=FIG_AUDIT if purple else FIG_STYLES["facade"][2])
        fig.text(bx + 54, y, what, room=bw - 54 - 8, fill=FIG_AUDIT if purple else FIG_INK)
    fig.card(bx + 8, embed_top, bw - 16, "embedder", "Local hashing embedder", dashed=True,
             mono=f"{vectors.LOCAL_MODEL_NAME} · vectors.py",
             lines=[line.format(space=space_text) for line in embed_lines],
             markup={1: "bucket space: " + space_markup})

    # Inputs call the class.
    # An arrow leaves the middle of its box; when that is below the class's
    # box, it turns up in the gap between the columns to meet it.
    for source_top, source_bottom in ((loader_top, loader_bottom), (cli_top, cli_bottom)):
        if source_top > facade_bottom - 12:
            raise ValueError("figure: an input box no longer lines up with the DataCenter box")
        y = (source_top + source_bottom) / 2
        target = min(y, facade_bottom - 12)
        if target == y:
            fig.arrow((ax + aw, y), (bx, y))
        else:
            elbow = (ax + aw + bx) / 2
            fig.arrow((ax + aw, y), (elbow, y), (elbow, target), (bx, target))

    # The databases, one box each, on a bus from the class.
    db_top = top + 30.0
    y = db_top
    centres = []
    for spec in DATABASES:
        style = "audit" if spec.key == "audit" else "database"
        tables = wrap(" · ".join(spec.tables), 7.5, cw - 14 - 12)
        bottom = fig.card(cx + 7, y, cw - 14, style, spec.title, right=spec.filename,
                          lines=tables, title_size=8.0)
        centres.append((y + bottom) / 2)
        y = bottom + 4.0
    group_bottom = y + 3.0
    # Drawn under the database boxes: the group goes first in the SVG.
    group = Figure()
    group.box(cx, top, cw, group_bottom - top, "group", dashed=True, radius=6)
    group.text(cx + 7, top + 11, f"{words(count).capitalize()} SQLite databases", size=8,
               bold=True, fill=FIG_STYLES["group"][2], room=cw - 14)
    group.text(cx + 7, top + 21, "one file each in the storage root", fill=FIG_MUTED,
               room=cw - 14)
    fig.items[:0] = group.items
    joint = min(max((centres[0] + centres[-1]) / 2, top + 26), facade_bottom - 10)
    fig.line((bx + bw, joint), (bus, joint))
    fig.line((bus, min(joint, centres[0])), (bus, max(joint, centres[-1])))
    fig.dot(bus, joint)
    for centre in centres:
        fig.arrow((bus, centre), (cx + 7, centre))

    # Outputs.
    out_top = max(facade_bottom, group_bottom, cli_bottom) + 26.0
    fig.text(bx, out_top - 5, "OUTPUTS", size=7, bold=True, fill=FIG_MUTED)
    results_bottom = fig.card(bx, out_top, bw, "output", "Search, status and events", lines=[
        "search: top hits with their scores",
        "status: health, size, rows per table",
        "events: the most recent audit trail",
        "verify: SQLite integrity check",
    ])
    fig.arrow((bx + bw / 2, facade_bottom), (bx + bw / 2, out_top))
    backups_bottom = fig.card(cx, out_top, cw, "output", "Backups", lines=wrap(
        f"A timestamped folder holding a copy of each of the {count} files and a "
        "manifest.json.", 7.5, cw - 12))
    fig.arrow((cx + cw / 2, group_bottom), (cx + cw / 2, out_top))
    fig.text(cx + cw / 2 + 5, (group_bottom + out_top) / 2 + 2.5, "backup()", size=7, mono=True,
             fill=FIG_MUTED)

    # Legend, under the inputs.
    fig.arrow((ax, out_top + 4), (ax + 20, out_top + 4))
    fig.text(ax + 26, out_top + 6.5, "data flow", fill=FIG_MUTED, room=aw - 26)
    fill, stroke, _ = FIG_STYLES["audit"]
    fig.items.append(f'<rect x="{ax:.1f}" y="{out_top + 12:.1f}" width="20" height="8" rx="2" '
                     f'fill="{fill}" stroke="{stroke}" stroke-width="0.8"/>')
    fig.text(ax + 26, out_top + 19, "audit trail of every write", fill=FIG_AUDIT, room=aw - 26)

    height = max(results_bottom, backups_bottom) + 2.0
    return fig.svg(height, "Architecture: inputs, the DataCenter class, the "
                   f"{words(count)} databases and the outputs")


def section_architecture(command_count: int) -> str:
    return f"""
<div class="keep">
<h2>3. Architecture</h2>
<p>The command line and the Python API both call one class, <code>DataCenter</code>, which
owns the connections to all {esc(words(len(DATABASES)))} databases. Data files are validated
in full before anything is written, then loaded through that class. Documents are embedded
for search as they are stored, and every write is also recorded in the audit database.
Backups copy all {esc(words(len(DATABASES)))} files together with a manifest.</p>
<figure class="arch">
  {architecture_svg(command_count)}
  <figcaption>Figure 1. How data gets in, where it lives and what comes out. Inputs on the
  left; the <code>DataCenter</code> class in the middle, with the embedder it calls when a
  document is stored or a query is run; the {esc(words(len(DATABASES)))} database files in the
  dashed box; outputs below. Purple marks the audit trail.</figcaption>
</figure>
</div>
"""


def section_facility(gpus, power, incidents) -> str:
    # GPU inventory
    model_counts = Counter(g["model"] for g in gpus)
    vram: dict[str, float] = defaultdict(float)
    for g in gpus:
        vram[g["model"]] += number(g.get("vram_gb"), f"{g.get('asset_tag')} vram_gb")
    model_rows = [
        [esc(model), esc(count), esc(f"{vram[model]:,.0f}")]
        for model, count in sorted(model_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    status_counts = Counter(g["status"] for g in gpus)
    status_rows = [
        [esc(status), esc(count)]
        for status, count in sorted(status_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    total_vram = sum(vram.values())
    # Only an installed card sits in a host and a rack. The others are kept on
    # shelving, whose `host` field names a bench or cabinet, not a server.
    installed = [g for g in gpus if g.get("status") == INSTALLED]
    shelved = [g for g in gpus if g.get("status") != INSTALLED]
    hosts = sorted({g["host"] for g in installed})
    racks = sorted({g["rack"] for g in installed})
    shelving = sorted({g["rack"] for g in shelved})
    if set(shelving) & set(racks):
        raise ValueError(f"GPUs that are not {INSTALLED} sit in working racks "
                         f"{sorted(set(shelving) & set(racks))}; update the inventory text")
    where = (f"{len(installed):,} installed in {plural(len(hosts), 'host')} "
             f"across {plural(len(racks), 'rack')} ({join_and(racks)})")
    if shelved:
        by_status = Counter(g["status"] for g in shelved)
        kinds = [f"{n:,} {SHELF_STATUS.get(status, status)}"
                 for status, n in sorted(by_status.items(), key=lambda kv: (-kv[1], kv[0]))]
        where += (f", and {len(shelved):,} on the {join_and(shelving)} shelving "
                  f"({join_and(kinds)})")

    # Power and cooling
    dates = sorted(p["date"] for p in power)
    power_racks = sorted({p["rack"] for p in power})
    avg_kw = statistics.fmean(number(p.get("avg_kw"), f"{p['date']} avg_kw") for p in power)
    peaks = [number(p.get("peak_kw"), f"{p['date']} peak_kw") for p in power]
    peak_kw = max(peaks)
    peak_days = sum(1 for value in peaks if value == peak_kw)
    pues = [number(p.get("pue"), f"{p['date']} pue") for p in power]
    inlet = statistics.fmean(number(p.get("inlet_temp_c"), "inlet_temp_c") for p in power)
    outlet = statistics.fmean(number(p.get("outlet_temp_c"), "outlet_temp_c") for p in power)

    # Incidents
    minutes = [number(i.get("minutes_to_resolve"), f"{i['incident_id']} minutes") for i in incidents]
    median_minutes = statistics.median(minutes)
    impact = sum(1 for i in incidents if i.get("client_impact") is True)
    opened = sorted(i["opened_at"] for i in incidents)
    sev_rows = []
    for severity in sorted({i["severity"] for i in incidents}):
        group = [i for i in incidents if i["severity"] == severity]
        group_minutes = [number(i.get("minutes_to_resolve"), "minutes") for i in group]
        sev_rows.append([
            esc(severity),
            esc(len(group)),
            esc(sum(1 for i in group if i.get("client_impact") is True)),
            esc(f"{statistics.median(group_minutes):,.0f}"),
        ])
    category_counts = Counter(i["category"] for i in incidents)
    cat_rows = []
    for category, count in sorted(category_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        group_minutes = [number(i.get("minutes_to_resolve"), "minutes")
                         for i in incidents if i["category"] == category]
        cat_rows.append([esc(category), esc(count), esc(f"{statistics.median(group_minutes):,.0f}")])

    return f"""
<div class="keep">
<h2>4. Facility snapshot</h2>
<h3>GPU inventory</h3>
<p>The <code>gpu_inventory</code> dataset lists {esc(plural(len(gpus), "GPU"))} with
{esc(f"{total_vram:,.0f}")} GB of GPU memory in total: {esc(where)}.</p>
<div class="cols">
  <div>{table(["Model", "GPUs", "Memory (GB)"], model_rows, numeric={1, 2},
              footer=["Total", esc(len(gpus)), esc(f"{total_vram:,.0f}")])}</div>
  <div>{table(["Status", "GPUs"], status_rows, numeric={1},
              footer=["Total", esc(len(gpus))])}</div>
</div>
</div>

<div class="keep">
<h3>Power and cooling</h3>
<p>The <code>power_and_cooling_daily</code> dataset holds
{esc(plural(len(power), "daily reading"))} for rack{"s" if len(power_racks) != 1 else ""}
{esc(join_and(power_racks))}, {esc(long_date(dates[0]))} to {esc(long_date(dates[-1]))}.
Averages are daily means; PUE ranged from {esc(f"{min(pues):.3f}")} to
{esc(f"{max(pues):.3f}")}, and the peak was reached on {esc(plural(peak_days, "day"))}.</p>
<div class="grid">
  {stat(esc(f"{avg_kw:,.2f} kW"), "average draw")}
  {stat(esc(f"{peak_kw:,.2f} kW"), "peak draw")}
  {stat(esc(f"{statistics.fmean(pues):.3f}"), "average PUE")}
  {stat(esc(f"{inlet:.1f} / {outlet:.1f} °C"), "average inlet / outlet air")}
</div>
</div>

<div class="keep">
<h3>Incidents</h3>
<p>The <code>incidents</code> dataset records {esc(plural(len(incidents), "incident"))}
opened from {esc(long_date(opened[0]))} to {esc(long_date(opened[-1]))}; {esc(impact)}
affected clients. The median time to resolve was {esc(f"{median_minutes:,.0f}")} minutes and
the longest {esc(f"{max(minutes):,.0f}")}. Sev1 is the most severe.</p>
<div class="cols">
  <div>{table(["Severity", "Incidents", "Client impact", "Median min."], sev_rows,
              numeric={1, 2, 3},
              footer=["Total", esc(len(incidents)), esc(impact), esc(f"{median_minutes:,.0f}")])}</div>
  <div>{table(["Category", "Incidents", "Median min."], cat_rows, numeric={1, 2})}</div>
</div>
</div>
"""


def section_knowledge_base(docs: list[dict[str, Any]], status: dict[str, Any]) -> str:
    docs = sorted(docs, key=lambda d: d["id"])
    sources = first_seen([d.get("source", "") for d in docs])
    kb_sources = [s for s in sources if s.startswith("kb/")]
    other_sources = [s for s in sources if not s.startswith("kb/")]
    rows = []
    kb_total = 0
    for source in kb_sources:
        titles = [d["title"] for d in docs if d.get("source") == source]
        kb_total += len(titles)
        examples = "; ".join(esc(t) for t in titles[:3])
        rows.append([f"<code>{esc(source)}</code>", esc(len(titles)), examples])
    other_count = sum(1 for d in docs if d.get("source") in other_sources)

    rows_by_key = {db["key"]: db.get("rows", {}) for db in status["databases"]}
    embeddings = rows_by_key.get("embeddings", {}).get("embeddings")
    if embeddings is None:
        index_note = ""
    elif embeddings == len(docs):
        index_note = (f" The embeddings database holds {esc(embeddings)} vectors, one for "
                      "every stored document, so all of them are reachable by search.")
    else:
        index_note = (f" The embeddings database holds {esc(embeddings)} vectors for "
                      f"{esc(len(docs))} stored documents.")

    other_note = ""
    if other_count:
        other_note = (f" The other {esc(plural(other_count, 'document'))} come from "
                      + join_and([f"<code>{esc(s)}</code>" for s in other_sources]) + ".")

    return f"""
<h2>5. Knowledge base</h2>
<p>The documents database holds {esc(plural(len(docs), "document"))}. Of these,
{esc(kb_total)} form the knowledge base, under {esc(words(len(kb_sources)))} sources: what staff and clients
need to know about AI, how the hall is run, the rules that govern data and models, and the
terms of employment.{other_note}{index_note}</p>
{table(["Source", "Documents", "Examples"], rows, numeric={1}, widths=["20%", "12%", "68%"],
       footer=["Knowledge base", esc(kb_total), ""])}
<p>Search with <code>python -m datacenter search "your question"</code> or the Search page
of the portal.</p>
"""


def section_plans(plans: list[dict[str, Any]]) -> str:
    tables = list(FEE_TABLES.values())
    rows = []
    line_total = 0
    for plan in plans:
        line_total += len(plan["lines"])
        cells = [f"<code>{esc(plan['plan'])}</code>", esc(len(plan["lines"])), esc(plan["months"])]
        for name in tables:
            subtotal = plan["subtotals"].get(name)
            cells.append(DASH if subtotal is None else money(subtotal))
        cells += [f"<b>{money(plan['total_usd'])}</b>", money(plan["monthly_average_usd"])]
        rows.append(cells)
    columns = (["Plan", "Lines", "Months"] + [FEE_TABLE_LABELS.get(t, t) for t in tables]
               + ["Total", "Per month"])
    numeric = set(range(1, len(columns)))
    return f"""
<h2>6. Business plans</h2>
<p>The <code>business_plans</code> dataset holds {esc(plural(line_total, "line"))} in
{esc(words(len(plans)))} plans. Each line names a fee code, and
<code>datacenter.plans</code> costs it against the fee table that code belongs to:</p>
<ul>
  <li><b>Government</b> fees are charged each time they fall due within the plan's months:
  once, monthly, quarterly or annually.</li>
  <li><b>Service</b> fees are the unit price times the units, with the rate card's minimum
  enforced. Per-month units are charged for every month of the line.</li>
  <li><b>Employment</b> costs are a twelfth of base salary, grossed up by the employer tax
  rate, plus monthly benefits, for every person and month, plus any recruitment fee once per
  person.</li>
</ul>
<div class="keep">
{table(columns, rows, numeric=numeric,
       widths=["22%", "7%", "8%", "12%", "12%", "13%", "13%", "13%"])}
<p class="small muted">A dash means the plan has no lines in that fee table. &ldquo;Per
month&rdquo; is the total divided by the plan's longest line. The plans are alternatives, not parts of one
budget, so they are not added together.</p>
</div>
"""


def section_government(gov: list[dict[str, Any]]) -> str:
    def summarise(key: str) -> tuple[list[list[str]], list[str]]:
        # Each group keeps the amounts themselves, not running sums, so a group
        # with no fee of a kind (a dash) stays distinct from one whose fees of
        # that kind add up to $0.00, such as a registration that costs nothing.
        groups: dict[str, dict[str, list[float]]] = {}
        order = first_seen([f[key] for f in gov])
        for fee in gov:
            period = str(fee.get("period", "")).strip().lower()
            if period not in TIMES_PER_YEAR:
                raise ValueError(f"{fee['fee_code']}: unknown period {period!r}")
            amount = number(fee.get("amount_usd"), f"{fee['fee_code']} amount_usd")
            group = groups.setdefault(fee[key], {"fees": [], "once": [], "yearly": []})
            group["fees"].append(amount)
            if period == "one-time":
                group["once"].append(amount)
            else:
                group["yearly"].append(amount * TIMES_PER_YEAR[period])
        rows = []
        for name in order:
            g = groups[name]
            rows.append([esc(name.capitalize() if key == "category" else name),
                         esc(len(g["fees"])), money_or_dash(g["once"]),
                         money_or_dash(g["yearly"]), money(sum(g["once"]) + sum(g["yearly"]))])
        once = [v for g in groups.values() for v in g["once"]]
        yearly = [v for g in groups.values() for v in g["yearly"]]
        footer = ["Total", esc(len(gov)), money_or_dash(once), money_or_dash(yearly),
                  money(sum(once) + sum(yearly))]
        return rows, footer

    by_category, cat_footer = summarise("category")
    by_jurisdiction, jur_footer = summarise("jurisdiction")
    authorities = first_seen([f["authority"] for f in gov])
    jurisdictions = first_seen([f["jurisdiction"] for f in gov])
    columns = ["", "Fees", "One-time", "Recurring per year", "First year"]
    widths = ["28%", "10%", "20%", "22%", "20%"]

    zero_fees = [f for f in gov if f.get("amount_usd") is not None and float(f["amount_usd"]) == 0]
    zero_note = "".join(
        f"<p>{esc(f['name'])} ({esc(f['fee_code'])}) costs {money(f['amount_usd'])} and is "
        f"still counted in the tables above, because it must be filed."
        + (f" The fee table notes: &ldquo;{esc(f['notes'])}&rdquo;" if f.get("notes") else "")
        + "</p>"
        for f in zero_fees
    )

    return f"""
<h2>7. Government fees summary</h2>
<div class="notice section-notice">{esc(ILLUSTRATIVE_NOTICE)}</div>
<p>The <code>government_fees</code> dataset lists {esc(plural(len(gov), "fee"))} charged by
{esc(plural(len(authorities), "authority", "authorities"))} in
{esc(words(len(jurisdictions)))} jurisdictions: {esc(join_and(jurisdictions))}. Fees are
charged once or on a schedule, so the tables keep the two apart. &ldquo;Recurring per
year&rdquo; counts annual fees {esc(times_word(TIMES_PER_YEAR["annual"]))}, quarterly fees
{esc(times_word(TIMES_PER_YEAR["quarterly"]))} and monthly fees
{esc(times_word(TIMES_PER_YEAR["monthly"]))}; &ldquo;First year&rdquo; adds the one-time fees
to that.</p>
<p class="small muted">A dash means the category or jurisdiction has no fees of that kind.</p>
<div class="keep">
<h3>By category</h3>
{table(["Category"] + columns[1:], by_category, numeric={1, 2, 3, 4}, widths=widths,
       footer=cat_footer)}
</div>
<div class="keep">
<h3>By jurisdiction</h3>
{table(["Jurisdiction"] + columns[1:], by_jurisdiction, numeric={1, 2, 3, 4}, widths=widths,
       footer=jur_footer)}
</div>
{zero_note}
"""


def cli_commands() -> list[list[str]]:
    """Every subcommand with its usage line and help text, read from the parser."""
    parser = build_parser()
    rows = []
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for choice in action._choices_actions:
            usage = " ".join(action.choices[choice.dest].format_usage().split())
            usage = usage.removeprefix(f"usage: {parser.prog} ").replace(" [-h]", "")
            help_text = choice.help or ""
            # Break lines only between whole arguments, never inside "[--opt VALUE]".
            units = re.findall(r"\[[^\]]*\]|\S+", usage)
            usage_html = " ".join(f'<span class="nowrap">{esc(unit)}</span>' for unit in units)
            rows.append([f"<code>{usage_html}</code>",
                         esc(help_text[:1].upper() + help_text[1:]) + "."])
    if not rows:
        raise ValueError("the CLI parser has no subcommands")
    return rows


def section_running(commands: list[list[str]]) -> str:
    portal_url = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"
    pages = join_and([label for _, label in NAV])
    quick_start = "\n".join([
        "python -m datacenter init      # create the databases",
        "python -m datacenter seed      # load the bundled data files",
        "python -m datacenter status    # health, size and row counts",
        "python -m datacenter plans     # business plans with their totals",
        f"python -m datacenter portal    # read-only portal on {portal_url}",
    ])
    return f"""
<div class="keep">
<h2>8. How to run it</h2>
<p>The software is one Python package, <code>datacenter</code>, with no third-party
dependencies. It needs Python {esc(requires_python())} or newer. Run these from the
repository root:</p>
<pre class="cli">{esc(quick_start)}</pre>
</div>
<p>The databases are written to a <code>storage</code> folder in the current directory. Pass
<code>--root DIR</code> before the command, or set the <code>SDDU13_ROOT</code> environment
variable, to keep them somewhere else. <code>seed</code> is a refill, so running it again
updates records instead of duplicating them. This document is rebuilt from the same data
with <code>python3 tools/pdf/build.py ai_data_center</code>.</p>

<div class="keep">
<h3>The portal</h3>
<p><code>python -m datacenter portal</code> serves the data center at
<code>{esc(portal_url)}</code> with {esc(words(len(NAV)))} pages: {esc(pages)}. It has two
fixed limits:</p>
<ul>
  <li><b>Read-only.</b> It answers GET requests only and has no write path. Changes go
  through the command line or the Python API, where the audit trail records who made
  them.</li>
  <li><b>Local by default.</b> It binds <code>{esc(DEFAULT_HOST)}</code>. It has no
  authentication, so it refuses any other <code>--host</code> unless
  <code>--allow-remote</code> is also passed.</li>
</ul>
</div>

<div class="lead-rows">
<h3>Every command</h3>
<p>Each line below follows <code>python -m datacenter</code>.</p>
{table(["Usage", "What it does"], commands, widths=["50%", "50%"])}
</div>
"""


# -- build -------------------------------------------------------------------------

def build() -> Path:
    with seeded_center() as center:
        services = records(center, "service_fees")
        gov = records(center, "government_fees")
        gpus = records(center, "gpu_inventory")
        power = records(center, "power_and_cooling_daily")
        incidents = records(center, "incidents")
        docs = center.list_documents()
        plans = cost_all(center)
        # `python -m datacenter seed` runs the jobs that loading queues before it
        # returns. Do the same, so the row counts match what a reader sees after
        # seeding an empty root themselves.
        while center.run_next_job() is not None:
            pass
        status = center.status()

        for name, rows in (("service_fees", services), ("government_fees", gov),
                           ("gpu_inventory", gpus), ("power_and_cooling_daily", power),
                           ("incidents", incidents)):
            if not rows:
                raise ValueError(f"dataset {name} is empty")

        commands = cli_commands()
        body = "".join([
            f"<style>{EXTRA_CSS}</style>",
            at_a_glance(services, gpus, docs, plans, gov),
            section_business(services),
            section_databases(status),
            section_architecture(len(commands)),
            section_facility(gpus, power, incidents),
            section_knowledge_base(docs, status),
            section_plans(plans),
            section_government(gov),
            section_running(commands),
        ])

    html_text = document(
        TITLE,
        body,
        eyebrow="Company overview",
        subtitle="What the business sells, the software it runs on, and what its data says "
                 "about the facility, the plans and the fees.",
        notice=ILLUSTRATIVE_NOTICE,
    )
    return render(NAME, html_text)


if __name__ == "__main__":
    print(build())
