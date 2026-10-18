"""Fee Schedule: every fee the business pays and charges.

    python3 tools/pdf/build.py fee_schedule
    python3 tools/pdf/fee_schedule.py

Four sections, each computed from the bundled data when the document is built:

1. Government fees, grouped by category. Each recurring fee is converted to a
   twelve-month cost and the recurring annual cost is totalled.
2. The service rate card, grouped by category.
3. Employment costs, with a fully-loaded annual cost for each role.
4. Every business plan, costed line by line by datacenter.plans.cost_plan().

Before anything is written, the annual government costs and the fully-loaded
employment formula are checked against what datacenter.plans charges for the
same fees in the business plans. The build fails if the two disagree, so the
document and the plan costing cannot drift apart.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    ILLUSTRATIVE_NOTICE,
    document,
    esc,
    money,
    records,
    render,
    seeded_center,
    table,
)
from datacenter.plans import FEE_TABLES, cost_plan, plan_names  # noqa: E402

NAME = "fee-schedule"
TITLE = "Fee Schedule"

#: How many times a recurring government fee falls due in twelve months.
#: A one-time fee has no annual figure and is shown as "one-time".
PER_YEAR = {"monthly": 12, "quarterly": 4, "annual": 1}
ONE_TIME = "one-time"

#: The role whose plan line proves the fully-loaded formula matches the plans.
WITNESS_ROLE = "EMP-001"

#: Plan lines are shown in the same order as the sections of this document.
TABLE_ORDER = [FEE_TABLES["GOV"], FEE_TABLES["SVC"], FEE_TABLES["EMP"]]
TABLE_LABELS = {
    FEE_TABLES["GOV"]: "Government fees",
    FEE_TABLES["SVC"]: "Services",
    FEE_TABLES["EMP"]: "Employment",
}

#: How many rows of a plan's table stay on the page with the plan's heading.
#: The rest may run on to the next page, under a repeated header row.
LEAD_ROWS = 3

EXTRA_CSS = """
<style>
/* A totals row belongs after a table's last row only; Chromium would otherwise
   repeat a tfoot on every page the table spans. The page wrapper keeps its own. */
table:not(.page) > tfoot { display: table-row-group; }
.keep { break-inside: avoid; }
/* A plan's heading, its summary line and its first rows stay together; a
   subtotal or the plan total never starts a page on its own. */
.planmeta { break-after: avoid; }
tr.stick, tr.subtotal, table.plan > tfoot { break-before: avoid; }
.code, .nw { white-space: nowrap; }
.stats5 .stat { flex: 1 1 auto; }
.stats5 .stat span { white-space: nowrap; }
.wraphead th.num { white-space: normal; }
.note { color: var(--muted); font-size: 8pt; line-height: 1.35; margin-top: 1pt; }
.lead { margin-bottom: 10pt; }
.count { font-weight: 400; color: var(--muted); font-size: 9pt; }
.planmeta { font-size: 9pt; color: var(--muted); margin: 0 0 2pt; }
tr.subtotal td { font-weight: 700; border-top: 0.75pt solid var(--muted); }
.formula { font-size: 9pt; }
</style>
"""


class FeeScheduleError(RuntimeError):
    """The data cannot be shown without guessing, or disagrees with the plans."""


# -- small helpers ----------------------------------------------------------------

def need(row: dict[str, Any], key: str) -> Any:
    """A field that must be present. Missing is an error, never a silent zero."""
    value = row.get(key)
    if value is None:
        raise FeeScheduleError(f"{row.get('fee_code', '?')}: no {key}")
    return value


def number(row: dict[str, Any], key: str) -> float:
    return float(need(row, key))


def text(row: dict[str, Any], key: str) -> str:
    value = need(row, key)
    if str(value).strip() == "":
        raise FeeScheduleError(f"{row.get('fee_code', '?')}: empty {key}")
    return str(value)


def capitalised(value: str) -> str:
    return value[:1].upper() + value[1:]


def plural(count: int, word: str, words: str = "") -> str:
    return f"{count} {word if count == 1 else (words or word + 's')}"


def units(value: float) -> str:
    return f"{value:,g}"


def percent(value: float) -> str:
    return f"{value:g}%"


def code(value: Any) -> str:
    """A fee code, kept on one line."""
    return f'<span class="code">{esc(value)}</span>'


DASH = '<span class="muted">&ndash;</span>'


def money_columns(*columns: list[float]) -> list[list[str]]:
    """Amounts for the money columns of one table: whole dollars when every
    value in every column is whole, otherwise cents throughout, so neither a
    column nor a row mixes the two."""
    whole = all(float(value).is_integer() for values in columns for value in values)
    return [[money(value, cents=not whole) for value in values] for values in columns]


def money_or_dash(value: float | None) -> str:
    """A sum, or a dash when there was nothing to add up (None, never 0)."""
    return DASH if value is None else money(value)


def with_note(name: str, note: Any) -> str:
    """A name, with its note in small type underneath when there is one."""
    cell = esc(name)
    if note is not None and str(note).strip():
        cell += f'<div class="note">{esc(note)}</div>'
    return cell


def grouped(rows: list[dict[str, Any]], key: str) -> list[tuple[str, list[dict[str, Any]]]]:
    """Rows grouped by `key`, groups in order of first appearance, rows by fee_code."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(text(row, key), []).append(row)
    return [(name, sorted(items, key=lambda r: text(r, "fee_code"))) for name, items in groups.items()]


def cents(value: float) -> float:
    return round(value, 2)


# -- the rules this document states ---------------------------------------------

def period_of(fee: dict[str, Any]) -> str:
    period = text(fee, "period").strip().lower()
    if period != ONE_TIME and period not in PER_YEAR:
        raise FeeScheduleError(f"{fee['fee_code']}: unknown period {period!r}")
    return period


def annual_cost(fee: dict[str, Any]) -> float | None:
    """Twelve months of a recurring government fee; None for a one-time fee."""
    period = period_of(fee)
    if period == ONE_TIME:
        return None
    return number(fee, "amount_usd") * PER_YEAR[period]


def fully_loaded(role: dict[str, Any]) -> float:
    """base x (1 + tax% / 100) + benefits x 12. Recruitment is not included."""
    base = number(role, "base_salary_usd")
    tax = number(role, "employer_tax_pct")
    benefits = number(role, "benefits_usd_month")
    return base * (1.0 + tax / 100.0) + benefits * 12


# -- cross-checks against datacenter.plans ----------------------------------------

def check_against_plans(gov: list[dict[str, Any]], roles: list[dict[str, Any]],
                        plans: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fail unless the plan costing agrees with the figures this document prints.

    Returns the plan and line that witness the fully-loaded formula for
    WITNESS_ROLE, so the document can show the check it passed.
    """
    gov_by_code = {text(fee, "fee_code"): fee for fee in gov}
    role_by_code = {text(role, "fee_code"): role for role in roles}

    witness: tuple[dict[str, Any], dict[str, Any]] | None = None
    for plan in plans:
        line_sum = cents(sum(line["amount_usd"] for line in plan["lines"]))
        subtotal_sum = cents(sum(plan["subtotals"].values()))
        if line_sum != plan["total_usd"] or subtotal_sum != plan["total_usd"]:
            raise FeeScheduleError(
                f"plan {plan['plan']}: lines sum to {line_sum:.2f} and subtotals to "
                f"{subtotal_sum:.2f}, but the total is {plan['total_usd']:.2f}")

        for line in plan["lines"]:
            code = line["fee_code"]
            if line["months"] != 12:
                continue  # the annual figures below describe twelve months only
            quantity = line["quantity"]
            if code in role_by_code:
                role = role_by_code[code]
                expected = cents(quantity * (fully_loaded(role) + number(role, "recruitment_fee_usd")))
            elif code in gov_by_code:
                fee = gov_by_code[code]
                yearly = annual_cost(fee)
                expected = cents(quantity * (number(fee, "amount_usd") if yearly is None else yearly))
            else:
                continue  # service lines are priced by units; nothing annual to compare
            if line["amount_usd"] != expected:
                raise FeeScheduleError(
                    f"plan {plan['plan']}, line {line['line']!r} ({code}): datacenter.plans charges "
                    f"{line['amount_usd']:.2f} over 12 months, this document's rule gives {expected:.2f}")
            if witness is None and code == WITNESS_ROLE:
                witness = (plan, line)

    if witness is None:
        raise FeeScheduleError(
            f"no business plan has a 12-month line for {WITNESS_ROLE}; "
            "the fully-loaded formula cannot be cross-checked")
    plan, line = witness
    if line["quantity"] != 1:
        raise FeeScheduleError(f"{WITNESS_ROLE} line in {plan['plan']} has quantity "
                               f"{line['quantity']:g}, not 1; the check needs one person")
    role = role_by_code[WITNESS_ROLE]
    expected = cents(fully_loaded(role)) + cents(number(role, "recruitment_fee_usd"))
    if line["amount_usd"] != cents(expected):
        raise FeeScheduleError(
            f"fully-loaded formula disagrees with datacenter.plans for {WITNESS_ROLE} in "
            f"{plan['plan']}: plan line {line['amount_usd']:.2f}, this document {expected:.2f}")
    return plan, line


# -- sections ---------------------------------------------------------------------

def intro(gov: list[dict[str, Any]], services: list[dict[str, Any]], roles: list[dict[str, Any]],
          plans: list[dict[str, Any]], recurring_total: float) -> str:
    stats = [
        (str(len(gov)), "government fees"),
        (money(recurring_total), "recurring fees a year"),
        (str(len(services)), "service rates"),
        (str(len(roles)), "employment roles"),
        (str(len(plans)), "business plans"),
    ]
    tiles = "".join(f"<div class=\"stat\"><b>{value}</b><span>{esc(label)}</span></div>"
                    for value, label in stats)
    return (
        '<p class="lead">Every fee in the data center\'s fee tables: what the business pays to '
        "government authorities, what it charges clients for services, what each role costs to "
        "employ, and what each business plan costs once those fees are applied. Each figure is "
        "read from the data, or computed from it, when the document is built.</p>"
        f'<div class="grid stats5">{tiles}</div>'
    )


def government_section(gov: list[dict[str, Any]]) -> tuple[str, float]:
    groups = grouped(gov, "category")
    recurring = [fee for fee in gov if annual_cost(fee) is not None]
    one_time = [fee for fee in gov if annual_cost(fee) is None]
    recurring_total = sum(annual_cost(fee) for fee in recurring)
    one_time_total = sum(number(fee, "amount_usd") for fee in one_time)
    authorities = {text(fee, "authority") for fee in gov}
    jurisdictions = {text(fee, "jurisdiction") for fee in gov}
    free = [fee for fee in gov if number(fee, "amount_usd") == 0]

    periods = ", ".join(f"{period} x{times}" for period, times in PER_YEAR.items())
    lead = [
        '<h2>1. Government fees</h2>',
        f"<p>The business pays {plural(len(gov), 'government fee')} to "
        f"{plural(len(authorities), 'authority', 'authorities')} in "
        f"{plural(len(jurisdictions), 'jurisdiction')}. "
        f"{plural(len(recurring), 'fee')} {'recurs and costs' if len(recurring) == 1 else 'recur and cost'} "
        f"{money(recurring_total)} a year; {plural(len(one_time), 'fee')} "
        f"{'is paid once and totals' if len(one_time) == 1 else 'are paid once and total'} "
        f"{money(one_time_total)}.</p>",
    ]
    rule = (f"<p>The annual cost column is twelve months of a recurring fee: {esc(periods)}. "
            "A one-time fee is paid once, so it is marked one-time and left out of the annual "
            "total.")
    if free:
        codes = ", ".join(esc(text(fee, "fee_code")) for fee in free)
        rule += (f" {codes} {'is' if len(free) == 1 else 'are'} listed at {money(0)}: "
                 f"{'a real entry' if len(free) == 1 else 'real entries'} with no charge, "
                 "not missing data.")
    lead.append(rule + "</p>")

    parts = []
    for index, (category, fees) in enumerate(groups):
        rows = []
        for fee in fees:
            yearly = annual_cost(fee)
            rows.append([
                code(text(fee, "fee_code")),
                with_note(text(fee, "name"), fee.get("notes")),
                esc(text(fee, "jurisdiction")),
                esc(text(fee, "authority")),
                f'<span class="nw">{esc(period_of(fee))}</span>',
                money(number(fee, "amount_usd")),
                '<span class="muted">one-time</span>' if yearly is None else money(yearly),
            ])
        parts.append(
            '<div class="keep">' + ("".join(lead) if index == 0 else "")
            + f'<h3>{esc(capitalised(category))} '
            f'<span class="count">{plural(len(fees), "fee")}</span></h3>'
            + table(["Code", "Fee", "Jurisdiction", "Authority", "Period", "Amount", "Annual cost"],
                    rows, numeric={5, 6},
                    widths=["9%", "26%", "13%", "19%", "10%", "11%", "12%"])
            + "</div>")

    summary_rows = []
    for category, fees in groups:
        once = [number(fee, "amount_usd") for fee in fees if annual_cost(fee) is None]
        yearly = [annual_cost(fee) for fee in fees if annual_cost(fee) is not None]
        summary_rows.append([esc(capitalised(category)), str(len(fees)),
                             money_or_dash(sum(once) if once else None),
                             money_or_dash(sum(yearly) if yearly else None)])
    parts.append(
        '<div class="keep"><h3>Totals by category</h3>'
        '<p class="small muted">A dash means the category has no fees of that kind.</p>'
        + table(
            ["Category", "Fees", "One-time total", "Recurring annual cost"],
            summary_rows,
            numeric={1, 2, 3},
            footer=["All categories", str(len(gov)), money(one_time_total), money(recurring_total)],
            widths=["40%", "14%", "23%", "23%"],
        ) + "</div>")
    return "".join(parts), recurring_total


def services_section(services: list[dict[str, Any]]) -> str:
    groups = grouped(services, "category")
    lead = (
        '<h2>2. Service rate card</h2>'
        f"<p>{plural(len(services), 'service')} in {plural(len(groups), 'category', 'categories')}, "
        "priced per unit. A charge is never for fewer than the minimum units shown. Units ending in "
        "-month are billed for every month of service, year units for every started year, and all "
        "other units on the quantity used.</p>"
    )
    parts = []
    for index, (category, rates) in enumerate(groups):
        rows = [[
            code(text(rate, "fee_code")),
            esc(text(rate, "service")),
            esc(text(rate, "unit")),
            money(number(rate, "unit_price_usd")),
            units(number(rate, "minimum_units")),
            esc(rate.get("notes")),
        ] for rate in rates]
        parts.append(
            '<div class="keep">' + (lead if index == 0 else "")
            + f'<h3>{esc(capitalised(category))} '
            f'<span class="count">{plural(len(rates), "service")}</span></h3>'
            + table(["Code", "Service", "Unit", "Unit price", "Min. units", "Notes"],
                    rows, numeric={3, 4},
                    widths=["9%", "25%", "15%", "11%", "9%", "31%"])
            + "</div>")
    return "".join(parts)


def employment_section(roles: list[dict[str, Any]], witness: tuple[dict[str, Any], dict[str, Any]]) -> str:
    ordered = sorted(roles, key=lambda r: text(r, "fee_code"))
    base, benefits, hiring, loaded = money_columns(
        [number(role, "base_salary_usd") for role in ordered],
        [number(role, "benefits_usd_month") for role in ordered],
        [number(role, "recruitment_fee_usd") for role in ordered],
        [cents(fully_loaded(role)) for role in ordered],
    )
    rows = [[
        code(text(role, "fee_code")),
        with_note(text(role, "role"), role.get("notes")),
        f'<span class="nw">{esc(text(role, "employment_type"))}</span>',
        base[index],
        percent(number(role, "employer_tax_pct")),
        benefits[index],
        hiring[index],
        loaded[index],
    ] for index, role in enumerate(ordered)]
    types: dict[str, int] = {}
    for role in ordered:
        kind = text(role, "employment_type")
        types[kind] = types.get(kind, 0) + 1
    mix = ", ".join(f"{count} {esc(kind)}" for kind, count in types.items())

    plan, line = witness
    role = next(r for r in ordered if r["fee_code"] == WITNESS_ROLE)
    recruitment = number(role, "recruitment_fee_usd")
    return (
        '<h2>3. Employment costs</h2>'
        f"<p>{plural(len(roles), 'role')} ({mix}). The fully-loaded annual cost is what one person "
        "in the role costs the business for twelve months:</p>"
        '<p class="formula"><b>Fully-loaded annual cost</b> = base salary &times; '
        "(1 + employer tax % &divide; 100) + monthly benefits &times; 12</p>"
        + '<div class="wraphead">'
        + table(["Code", "Role", "Type", "Base salary", "Employer tax", "Monthly benefits",
                 "Recruitment fee", "Fully-loaded annual"],
                rows, numeric={3, 4, 5, 6, 7},
                widths=["9%", "27%", "10%", "11%", "8%", "9%", "11%", "15%"])
        + "</div>"
        + '<div class="keep formula">'
        "<p>Recruitment fees are not part of the fully-loaded annual cost: they are paid once for "
        "each hire, not every year. The business plans in section 4 add the recruitment fee once "
        "for each person hired.</p>"
        f"<p>Check against the plan costing: the {esc(plan['plan'])} plan costs its "
        f"{esc(line['line'])} line ({esc(WITNESS_ROLE)}, one person, {line['months']} months) at "
        f"{money(line['amount_usd'])}, which is the fully-loaded annual cost of "
        f"{money(cents(fully_loaded(role)))} plus the recruitment fee of {money(recruitment)}.</p>"
        "</div>"
    )


def plan_table(plan: dict[str, Any]) -> str:
    """One plan's lines, with a subtotal after each fee table and the plan total."""
    widths = ["9%", "27%", "30%", "20%", "14%"]
    head = ("<colgroup>" + "".join(f'<col style="width:{w}">' for w in widths) + "</colgroup>"
            "<thead><tr><th>Code</th><th>Line</th><th>Fee</th><th>Basis</th>"
            '<th class="num">Amount</th></tr></thead>')
    # (row class, cells) for every row, so the first LEAD_ROWS can be marked
    # to stay with the plan's heading.
    body: list[tuple[str, str]] = []
    for fee_table in TABLE_ORDER:
        lines = sorted((line for line in plan["lines"] if line["table"] == fee_table),
                       key=lambda line: line["fee_code"])
        if not lines:
            continue
        for line in lines:
            body.append((
                "",
                f"<td>{code(line['fee_code'])}</td>"
                f"<td>{with_note(line['line'], line.get('notes'))}</td>"
                f"<td>{esc(line['description'])}</td><td>{esc(line['basis'])}</td>"
                f'<td class="num">{money(line["amount_usd"])}</td>'))
        body.append((
            "subtotal",
            f'<td colspan="4">{esc(TABLE_LABELS[fee_table])} subtotal</td>'
            f'<td class="num">{money(plan["subtotals"][fee_table])}</td>'))
    unknown = {line["table"] for line in plan["lines"]} - set(TABLE_ORDER)
    if unknown:
        raise FeeScheduleError(f"plan {plan['plan']}: lines from unexpected tables {sorted(unknown)}")
    rows = []
    for index, (kind, cells) in enumerate(body):
        classes = [kind] if kind else []
        if 0 < index < LEAD_ROWS:
            classes.append("stick")
        attr = f' class="{" ".join(classes)}"' if classes else ""
        rows.append(f"<tr{attr}>{cells}</tr>")
    foot = (f'<tfoot><tr><td colspan="4">Plan total</td>'
            f'<td class="num">{money(plan["total_usd"])}</td></tr></tfoot>')
    return f'<table class="plan">{head}<tbody>{"".join(rows)}</tbody>{foot}</table>'


def plans_section(plans: list[dict[str, Any]]) -> str:
    summary_rows = []
    for plan in plans:
        cells = [esc(plan["plan"]), str(plan["months"]), str(len(plan["lines"]))]
        for fee_table in TABLE_ORDER:
            cells.append(money_or_dash(plan["subtotals"].get(fee_table)))
        cells.append(money(plan["total_usd"]))
        summary_rows.append(cells)

    parts = [
        '<div class="keep"><h2>4. Business plans</h2>'
        f"<p>{plural(len(plans), 'plan')} from the business plans data set, each costed line by line "
        "against the fee tables above. A government fee is charged each time it falls due within "
        "the plan's months. A service is charged for the units billed, never fewer than its "
        "minimum. A role is charged its monthly fully-loaded cost for each person and month, plus "
        "one recruitment fee per person. A dash means the plan has no lines from that table.</p>"
        "<h3>Plans at a glance</h3>" + table(
            ["Plan", "Months", "Lines", "Government", "Services", "Employment", "Total"],
            summary_rows, numeric={1, 2, 3, 4, 5, 6},
            widths=["22%", "9%", "8%", "15%", "15%", "15%", "16%"],
        ) + "</div>",
    ]
    # A plan is not kept whole: a long one would leave most of a page empty.
    # Its heading, summary line and first rows travel together (see EXTRA_CSS).
    for plan in plans:
        parts.append(
            f'<div class="plan"><h3>{esc(plan["plan"])}</h3>'
            f'<p class="planmeta">{plural(len(plan["lines"]), "line")} over '
            f'{plural(plan["months"], "month")} &middot; total {money(plan["total_usd"])} &middot; '
            f'monthly average {money(plan["monthly_average_usd"])}</p>'
            + plan_table(plan) + "</div>")
    return "".join(parts)


# -- build ------------------------------------------------------------------------

def build() -> Path:
    with seeded_center() as center:
        gov = records(center, FEE_TABLES["GOV"])
        services = records(center, FEE_TABLES["SVC"])
        roles = records(center, FEE_TABLES["EMP"])
        plans = [cost_plan(center, name) for name in plan_names(center)]

    for name, rows in (("government fees", gov), ("service fees", services),
                       ("employment fees", roles), ("business plans", plans)):
        if not rows:
            raise FeeScheduleError(f"no {name} in the data; refusing to print an empty schedule")

    witness = check_against_plans(gov, roles, plans)
    government_html, recurring_total = government_section(gov)
    body = (
        EXTRA_CSS
        + intro(gov, services, roles, plans, recurring_total)
        + government_html
        + services_section(services)
        + employment_section(roles, witness)
        + plans_section(plans)
    )
    html_text = document(
        TITLE, body,
        eyebrow="Fees",
        subtitle="Government fees, service rates, employment costs and costed business plans",
        notice=ILLUSTRATIVE_NOTICE,
    )
    return render(NAME, html_text)


if __name__ == "__main__":
    print(build())
