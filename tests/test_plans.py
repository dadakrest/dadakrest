"""Tests for costing business plans against the fee tables."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datacenter import DATA_DIR, DataCenter, load_all  # noqa: E402
from datacenter.cli import main as cli_main  # noqa: E402
from datacenter.plans import (  # noqa: E402
    FEE_TABLES,
    PlanError,
    _occurrences,
    _service_units,
    cost_all,
    cost_plan,
    plan_names,
)


class PlansTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.center = DataCenter(root=self.tmp / "storage")
        self.center.provision()

    def tearDown(self) -> None:
        self.center.close()
        self._tmp.cleanup()

    def fees(self, **tables: list[dict]) -> None:
        """Put rows into the fee tables and a plan beside them."""
        for name, records in tables.items():
            self.center.replace_records(name, records)


class TestPeriods(unittest.TestCase):
    def test_how_often_each_period_falls_due(self) -> None:
        cases = [
            ("one-time", 12, 1), ("one-time", 1, 1),
            ("monthly", 12, 12), ("monthly", 1, 1),
            ("quarterly", 12, 4), ("quarterly", 1, 1), ("quarterly", 4, 2),
            ("annual", 12, 1), ("annual", 13, 2), ("annual", 24, 2),
        ]
        for period, months, expected in cases:
            with self.subTest(period=period, months=months):
                self.assertEqual(_occurrences(period, months), expected)

    def test_an_unknown_period_is_refused(self) -> None:
        with self.assertRaises(PlanError):
            _occurrences("fortnightly", 12)

    def test_a_per_month_unit_bills_every_month(self) -> None:
        self.assertEqual(_service_units("rack-month", 2, 12), 24)
        self.assertEqual(_service_units("month", 1, 6), 6)
        self.assertEqual(_service_units("gpu-month", 3, 4), 12)

    def test_a_flat_unit_bills_the_quantity_as_stated(self) -> None:
        self.assertEqual(_service_units("tb", 24, 12), 24)
        self.assertEqual(_service_units("engagement", 1, 12), 1)
        self.assertEqual(_service_units("gpu-hour", 100, 12), 100)

    def test_a_yearly_unit_bills_every_started_year(self) -> None:
        self.assertEqual(_service_units("year", 1, 12), 1)
        self.assertEqual(_service_units("year", 1, 13), 2)


class TestCosting(PlansTestCase):
    def test_a_government_fee_is_charged_once_per_occurrence(self) -> None:
        self.fees(
            government_fees=[{"fee_code": "GOV-1", "name": "Quarterly return", "period": "quarterly",
                              "amount_usd": 45.0}],
            business_plans=[{"plan": "p", "line": "Returns", "fee_code": "GOV-1",
                             "quantity": 1, "months": 12}],
        )
        plan = cost_plan(self.center, "p")
        self.assertEqual(plan["total_usd"], 180.0)
        self.assertEqual(plan["lines"][0]["basis"], "quarterly x4")

    def test_a_service_fee_respects_its_minimum(self) -> None:
        self.fees(
            service_fees=[{"fee_code": "SVC-1", "service": "Fine-tuning", "unit": "gpu-hour",
                           "unit_price_usd": 5.0, "minimum_units": 8}],
            business_plans=[{"plan": "p", "line": "Tuning", "fee_code": "SVC-1",
                             "quantity": 3, "months": 1}],
        )
        self.assertEqual(cost_plan(self.center, "p")["total_usd"], 40.0)

    def test_employment_cost_grosses_up_and_adds_recruitment_once(self) -> None:
        self.fees(
            employment_fees=[{"fee_code": "EMP-1", "role": "Technician", "base_salary_usd": 120000.0,
                              "employer_tax_pct": 10.0, "benefits_usd_month": 500.0,
                              "recruitment_fee_usd": 4000.0}],
            business_plans=[{"plan": "p", "line": "Technicians", "fee_code": "EMP-1",
                             "quantity": 2, "months": 6}],
        )
        # (120000/12 * 1.10 + 500) = 11500 a month, two people for six months,
        # plus a recruitment fee for each.
        self.assertEqual(cost_plan(self.center, "p")["total_usd"], 11500.0 * 2 * 6 + 8000.0)

    def test_subtotals_split_by_fee_table_and_sum_to_the_total(self) -> None:
        self.fees(
            government_fees=[{"fee_code": "GOV-1", "name": "Licence", "period": "annual", "amount_usd": 100.0}],
            service_fees=[{"fee_code": "SVC-1", "service": "Rack", "unit": "rack-month",
                           "unit_price_usd": 10.0, "minimum_units": 1}],
            business_plans=[
                {"plan": "p", "line": "Licence", "fee_code": "GOV-1", "quantity": 1, "months": 12},
                {"plan": "p", "line": "Rack", "fee_code": "SVC-1", "quantity": 1, "months": 12},
            ],
        )
        plan = cost_plan(self.center, "p")
        self.assertEqual(plan["subtotals"], {"government_fees": 100.0, "service_fees": 120.0})
        self.assertEqual(plan["total_usd"], 220.0)
        self.assertEqual(plan["monthly_average_usd"], round(220.0 / 12, 2))


class TestRefusals(PlansTestCase):
    def test_an_unknown_plan_names_the_ones_that_exist(self) -> None:
        self.fees(business_plans=[{"plan": "real", "line": "x", "fee_code": "GOV-1",
                                   "quantity": 1, "months": 1}])
        with self.assertRaises(PlanError) as raised:
            cost_plan(self.center, "imaginary")
        self.assertIn("real", str(raised.exception))

    def test_a_line_pointing_at_no_fee_is_refused_not_costed_as_zero(self) -> None:
        self.fees(business_plans=[{"plan": "p", "line": "Ghost", "fee_code": "GOV-404",
                                   "quantity": 1, "months": 1}])
        with self.assertRaises(PlanError) as raised:
            cost_plan(self.center, "p")
        self.assertIn("GOV-404", str(raised.exception))

    def test_months_below_one_is_refused(self) -> None:
        self.fees(
            government_fees=[{"fee_code": "GOV-1", "name": "Licence", "period": "annual", "amount_usd": 10.0}],
            business_plans=[{"plan": "p", "line": "x", "fee_code": "GOV-1", "quantity": 1, "months": 0}],
        )
        with self.assertRaises(PlanError):
            cost_plan(self.center, "p")


class TestBundledPlans(PlansTestCase):
    """The plans that ship with the data center must all cost cleanly."""

    def setUp(self) -> None:
        super().setUp()
        load_all(self.center, DATA_DIR)

    def test_every_bundled_plan_costs_without_error(self) -> None:
        plans = cost_all(self.center)
        self.assertEqual(len(plans), 4)
        for plan in plans:
            with self.subTest(plan=plan["plan"]):
                self.assertGreater(plan["total_usd"], 0)
                self.assertEqual(
                    round(sum(row["amount_usd"] for row in plan["lines"]), 2), plan["total_usd"]
                )

    def test_every_fee_code_in_every_plan_resolves(self) -> None:
        known = {
            record["payload"]["fee_code"]
            for table in FEE_TABLES.values()
            for record in self.center.list_records(table)
        }
        used = {
            record["payload"]["fee_code"]
            for record in self.center.list_records("business_plans")
        }
        self.assertEqual(used - known, set())

    def test_the_fee_tables_are_all_populated(self) -> None:
        for table in FEE_TABLES.values():
            with self.subTest(table=table):
                self.assertTrue(self.center.list_records(table))

    def test_plan_names_are_sorted(self) -> None:
        names = plan_names(self.center)
        self.assertEqual(names, sorted(names))


class TestPlanCli(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = str(Path(self._tmp.name) / "storage")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--root", self.root, "seed"])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_cli(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli_main(["--root", self.root, *argv])
        return code, out.getvalue()

    def test_fees_lists_all_three_tables(self) -> None:
        code, out = self.run_cli("fees")
        self.assertEqual(code, 0)
        for table in FEE_TABLES.values():
            self.assertIn(table, out)

    def test_fees_can_show_one_table(self) -> None:
        code, out = self.run_cli("fees", "--table", "government")
        self.assertEqual(code, 0)
        self.assertIn("government_fees", out)
        self.assertNotIn("service_fees", out)

    def test_plans_lists_every_plan(self) -> None:
        code, out = self.run_cli("plans")
        self.assertEqual(code, 0)
        self.assertIn("starter-colocation", out)

    def test_plan_prints_a_costed_breakdown(self) -> None:
        code, out = self.run_cli("plan", "starter-colocation")
        self.assertEqual(code, 0)
        self.assertIn("TOTAL", out)
        self.assertIn("SVC-021", out)

    def test_plan_json_round_trips(self) -> None:
        import json

        code, out = self.run_cli("plan", "starter-colocation", "--json")
        self.assertEqual(code, 0)
        plan = json.loads(out)
        self.assertEqual(plan["plan"], "starter-colocation")
        self.assertTrue(plan["lines"])

    def test_an_unknown_plan_exits_non_zero(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            code, _ = self.run_cli("plan", "no-such-plan")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
