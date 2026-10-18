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

from datacenter import DATA_CENTER_NAME  # noqa: E402
from datacenter.cli import build_parser  # noqa: E402
from datacenter.config import DATABASES  # noqa: E402
from datacenter.plans import FEE_TABLES, cost_all  # noqa: E402
from datacenter.portal import DEFAULT_HOST, DEFAULT_PORT, NAV  # noqa: E402

NAME = "ai-data-center"
TITLE = "AI Data Center"
ARCHITECTURE_PNG = REPO / "docs" / "architecture.png"

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


def section_architecture() -> str:
    if not ARCHITECTURE_PNG.exists():
        raise FileNotFoundError(f"missing {ARCHITECTURE_PNG}")
    return f"""
<div class="keep">
<h2>3. Architecture</h2>
<p>The command line and the Python API both call one class, <code>DataCenter</code>, which
owns the connections to all {esc(words(len(DATABASES)))} databases. Data files are validated
in full before anything is written, then loaded through that class. Documents are embedded
for search as they are stored, and every write is also recorded in the audit database.
Backups copy all {esc(words(len(DATABASES)))} files together with a manifest.</p>
<figure>
  <img src="{esc(ARCHITECTURE_PNG.as_uri())}" alt="Architecture diagram">
  <figcaption>Figure 1. How data gets in, where it lives and what comes out. Inputs on the
  left, the <code>DataCenter</code> class in the middle, the {esc(words(len(DATABASES)))}
  database files in the dashed box, and search results, status and backups on the right.
  Purple arrows are the audit trail. The embedder box is out of date: an embedding is now
  stored as a sparse map of the hash buckets a text uses, not as a fixed-length
  vector.</figcaption>
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
    hosts = len({g["host"] for g in gpus})
    racks = sorted({g["rack"] for g in gpus})
    total_vram = sum(vram.values())

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
<p>The <code>gpu_inventory</code> dataset lists {esc(plural(len(gpus), "GPU"))} in
{esc(plural(hosts, "host"))} across {esc(plural(len(racks), "rack"))}
({esc(join_and(racks))}), with {esc(f"{total_vram:,.0f}")} GB of GPU memory in total.</p>
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
            cells.append("&mdash;" if subtotal is None else money(subtotal))
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
        groups: dict[str, dict[str, float]] = {}
        order = first_seen([f[key] for f in gov])
        for fee in gov:
            period = str(fee.get("period", "")).strip().lower()
            if period not in TIMES_PER_YEAR:
                raise ValueError(f"{fee['fee_code']}: unknown period {period!r}")
            amount = number(fee.get("amount_usd"), f"{fee['fee_code']} amount_usd")
            group = groups.setdefault(fee[key], {"count": 0, "once": 0.0, "yearly": 0.0})
            group["count"] += 1
            if period == "one-time":
                group["once"] += amount
            else:
                group["yearly"] += amount * TIMES_PER_YEAR[period]
        rows = []
        for name in order:
            g = groups[name]
            rows.append([esc(name.capitalize() if key == "category" else name),
                         esc(int(g["count"])), money(g["once"]), money(g["yearly"]),
                         money(g["once"] + g["yearly"])])
        once = sum(g["once"] for g in groups.values())
        yearly = sum(g["yearly"] for g in groups.values())
        footer = ["Total", esc(len(gov)), money(once), money(yearly), money(once + yearly)]
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


def section_running() -> str:
    portal_url = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"
    pages = join_and([label for _, label in NAV])
    quick_start = "\n".join([
        "python -m datacenter init      # create the databases",
        "python -m datacenter seed      # load the bundled data files",
        "python -m datacenter status    # health, size and row counts",
        "python -m datacenter plans     # business plans with their totals",
        f"python -m datacenter portal    # read-only portal on {portal_url}",
    ])
    commands = cli_commands()
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

<div class="keep">
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

        body = "".join([
            f"<style>{EXTRA_CSS}</style>",
            at_a_glance(services, gpus, docs, plans, gov),
            section_business(services),
            section_databases(status),
            section_architecture(),
            section_facility(gpus, power, incidents),
            section_knowledge_base(docs, status),
            section_plans(plans),
            section_government(gov),
            section_running(),
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
