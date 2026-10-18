"""Costed business plans.

A plan is a set of lines in the `business_plans` dataset. Each line names a
`fee_code`, and this module is what connects that code to the fee it refers to
— a statutory fee in `government_fees`, a rate-card entry in `service_fees` or
a role in `employment_fees` — and works out what the line costs over the period
the plan covers.

The three fee tables price things in different shapes, so each has its own
rule:

government_fees
    `amount_usd` charged once, monthly, quarterly or annually. The line costs
    the amount times the number of times it falls due inside the plan's months.
service_fees
    `unit_price_usd` per unit, with `minimum_units` enforced. A per-month unit
    (`rack-month`, `gpu-month`, `month`, …) is charged for every month of the
    line; a `year` unit for every started year; anything else is a plain
    quantity the plan states in full.
employment_fees
    Monthly cost is a twelfth of `base_salary_usd`, grossed up by
    `employer_tax_pct` and with `benefits_usd_month` added. `recruitment_fee_usd`
    is charged once per person.

Amounts are rounded to cents only at the end, so a long plan does not drift.

The figures in the bundled data are invented for the sample data set. They are
not legal, tax or payroll advice.
"""

from __future__ import annotations

import math
from typing import Any

from .core import DataCenter

#: The dataset each fee_code prefix lives in.
FEE_TABLES: dict[str, str] = {
    "GOV": "government_fees",
    "SVC": "service_fees",
    "EMP": "employment_fees",
}

PLAN_DATASET = "business_plans"


class PlanError(ValueError):
    """Raised when a plan cannot be costed from the data as it stands."""


def _or_default(value: Any, default: Any) -> Any:
    """`value` unless it is missing. Written out rather than `value or default`
    because a plan line of 0 months is a mistake to refuse, not a 1 to assume."""
    return default if value is None else value


def _occurrences(period: str, months: int) -> int:
    """How many times something charged `period` falls due inside `months`."""
    period = (period or "").strip().lower()
    if period in ("one-time", "once", ""):
        return 1
    if period == "monthly":
        return months
    if period == "quarterly":
        return math.ceil(months / 3)
    if period == "annual":
        return math.ceil(months / 12)
    raise PlanError(f"unknown period {period!r}")


def _service_units(unit: str, quantity: float, months: int) -> float:
    """Total units billed for a rate-card line."""
    unit = (unit or "").strip().lower()
    if unit == "month" or unit.endswith("-month"):
        return quantity * months
    if unit == "year":
        return quantity * math.ceil(months / 12)
    return quantity


def _fee_index(center: DataCenter) -> dict[str, tuple[str, dict[str, Any]]]:
    """Every fee row in the three tables, keyed by its fee_code."""
    index: dict[str, tuple[str, dict[str, Any]]] = {}
    for table in FEE_TABLES.values():
        for record in center.list_records(table):
            payload = record["payload"]
            code = payload.get("fee_code")
            if code:
                index[code] = (table, payload)
    return index


def _cost_line(line: dict[str, Any], table: str, fee: dict[str, Any]) -> dict[str, Any]:
    """Cost one plan line against the fee it points at."""
    quantity = float(_or_default(line.get("quantity", 1), 1))
    months = int(_or_default(line.get("months", 1), 1))
    if months < 1:
        raise PlanError(f"{line.get('fee_code')}: months must be at least 1, not {months}")
    if quantity < 0:
        raise PlanError(f"{line.get('fee_code')}: quantity cannot be negative")

    if table == "government_fees":
        times = _occurrences(fee.get("period", "one-time"), months)
        amount = float(fee.get("amount_usd", 0)) * quantity * times
        basis = f"{fee.get('period')} x{times}"
        unit = fee.get("period", "")

    elif table == "service_fees":
        units = _service_units(fee.get("unit", ""), quantity, months)
        units = max(units, float(fee.get("minimum_units", 0) or 0))
        amount = float(fee.get("unit_price_usd", 0)) * units
        basis = f"{units:g} x {fee.get('unit')}"
        unit = fee.get("unit", "")

    elif table == "employment_fees":
        salary = float(fee.get("base_salary_usd", 0))
        gross_month = (salary / 12.0) * (1.0 + float(fee.get("employer_tax_pct", 0)) / 100.0)
        monthly = gross_month + float(fee.get("benefits_usd_month", 0))
        recruitment = float(fee.get("recruitment_fee_usd", 0)) * quantity
        amount = monthly * quantity * months + recruitment
        basis = f"{quantity:g} person x {months} months"
        unit = "person-month"

    else:  # pragma: no cover - FEE_TABLES is the only source of `table`
        raise PlanError(f"no rule for fee table {table!r}")

    return {
        "line": line.get("line", ""),
        "fee_code": line.get("fee_code", ""),
        "table": table,
        "description": fee.get("name") or fee.get("service") or fee.get("role", ""),
        "quantity": quantity,
        "months": months,
        "unit": unit,
        "basis": basis,
        "amount_usd": round(amount, 2),
        "notes": line.get("notes", ""),
    }


def plan_names(center: DataCenter) -> list[str]:
    """Every plan named in the business_plans dataset, in order."""
    names = {record["payload"].get("plan") for record in center.list_records(PLAN_DATASET)}
    return sorted(name for name in names if name)


def cost_plan(center: DataCenter, plan: str) -> dict[str, Any]:
    """Resolve one plan's lines against the fee tables and total them.

    Raises PlanError if the plan has no lines, or if a line names a fee_code
    that no fee table defines — a silent zero would be worse than a refusal.
    """
    lines = [
        record["payload"]
        for record in center.list_records(PLAN_DATASET)
        if record["payload"].get("plan") == plan
    ]
    if not lines:
        known = ", ".join(plan_names(center)) or "none"
        raise PlanError(f"no plan named {plan!r} (plans: {known})")

    index = _fee_index(center)
    missing = sorted({line.get("fee_code") for line in lines} - set(index))
    if missing:
        raise PlanError(f"{plan}: no fee defined for " + ", ".join(str(code) for code in missing))

    costed = [_cost_line(line, *index[line["fee_code"]]) for line in lines]
    costed.sort(key=lambda row: (row["table"], row["fee_code"]))

    by_table: dict[str, float] = {}
    for row in costed:
        by_table[row["table"]] = round(by_table.get(row["table"], 0.0) + row["amount_usd"], 2)

    months = max(int(_or_default(line.get("months", 1), 1)) for line in lines)
    total = round(sum(row["amount_usd"] for row in costed), 2)
    return {
        "plan": plan,
        "months": months,
        "lines": costed,
        "subtotals": by_table,
        "total_usd": total,
        "monthly_average_usd": round(total / months, 2) if months else total,
    }


def cost_all(center: DataCenter) -> list[dict[str, Any]]:
    """Cost every plan. Used by the portal's overview and `plans` command."""
    return [cost_plan(center, name) for name in plan_names(center)]
