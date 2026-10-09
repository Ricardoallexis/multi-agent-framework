from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.evals.routing.compare import CompareError, load_records, summarize, export_report


def row(case: str, route: str, **changes):
    value = {
        "case_id": case, "route": route, "input_version": "v1", "input_hash": "in-1",
        "rubric_version": "r1", "rubric_hash": "rub-1", "config_hash": "cfg-1",
        "surface": "fake-offline", "evaluated_at": "2026-10-09T10:00:00Z", "accepted": True, "critical_error": False,
        "requested": "model-requested", "effective": "model-effective", "observed": None,
        "calls": 1, "input_tokens": 10, "output_tokens": 4,
        "cost": None, "cost_unit": None, "duration_ms": 20,
        "rework": 0, "escalations": 0,
    }
    value.update(changes)
    return value


class RoutingCompareTests(unittest.TestCase):
    def test_unknowns_and_cost_include_failed_attempts(self):
        records = [
            row("1", "A", accepted=False, calls=2, cost=0.2, cost_unit="USD"),
            row("1", "B", cost=None),
            row("2", "A", calls=1, cost=0.3, cost_unit="USD"),
            row("2", "B", cost=None),
        ]
        report = summarize(records)
        routes = {item["route"]: item for item in report["routes"]}
        a = routes["A"]
        self.assertEqual(a["calls"], {"known_total": 3, "known_count": 2, "unknown_count": 0})
        self.assertEqual(a["cost_total"], {"status": "known", "value": 0.5, "unit": "USD"})
        self.assertEqual(a["cost_per_accepted_case"]["value"], 0.5)
        b = routes["B"]
        self.assertEqual(b["cost_total"]["status"], "unknown")
        self.assertEqual(b["cost_total"]["unknown_count"], 2)
        pair = report["paired_comparisons"][0]
        self.assertEqual(pair["comparable_cases"], 2)
        self.assertEqual(pair["metrics"]["accepted"]["denominator"], 2)
        self.assertEqual(pair["metrics"]["cost"]["denominator"], 0)

    def test_paired_cost_deltas_are_never_combined_across_currency_units(self):
        records = [
            row("usd-case", "A", cost=1, cost_unit="USD"),
            row("usd-case", "B", cost=2, cost_unit="USD"),
            row("mxn-case", "A", cost=100, cost_unit="MXN"),
            row("mxn-case", "B", cost=0, cost_unit="MXN"),
        ]
        cost = summarize(records)["paired_comparisons"][0]["metrics"]["cost"]
        self.assertEqual(cost["status"], "incomparable_units")
        self.assertEqual(cost["units"], ["MXN", "USD"])
        self.assertEqual(cost["by_unit"]["USD"], {"numerator": 1.0, "denominator": 1})
        self.assertEqual(cost["by_unit"]["MXN"], {"numerator": -100.0, "denominator": 1})
        self.assertNotIn("numerator", cost)

    def test_configuration_mismatch_excludes_pair_but_keeps_route_quality(self):
        report = summarize([row("1", "A"), row("1", "B", rubric_hash="changed")])
        pair = report["paired_comparisons"][0]
        self.assertEqual(pair["comparable_cases"], 0)
        self.assertEqual(pair["excluded_cases"], {"rubric_hash_mismatch": 1})
        self.assertEqual(report["routes"][0]["accepted"]["denominator"], 1)

    def test_json_csv_roundtrip_and_duplicate_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "data.json"
            source.write_text(json.dumps([row("1", "A", cost=0, cost_unit="USD")]), encoding="utf-8")
            records = load_records(source)
            csv_path = folder / "data.csv"
            export_report(summarize(records), csv_path)
            self.assertEqual(load_records(csv_path), records)
        with self.assertRaisesRegex(CompareError, "duplicate case_id/route"):
            summarize([row("1", "A"), row("1", "A")])

    def test_cli_from_real_consumer_exports_deterministic_report(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "synthetic.jsonl"
            source.write_text("\n".join(json.dumps(item) for item in [row("2", "B", input_tokens=None), row("1", "A", cost=0.1, cost_unit="USD")]) + "\n", encoding="utf-8")
            output = folder / "report.json"
            command = [sys.executable, "tools/evals/routing/compare.py", str(source), "--output", str(output)]
            first = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(first.returncode, 0, first.stderr)
            first_bytes = output.read_bytes()
            second = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(output.read_bytes(), first_bytes)
            exported = json.loads(first_bytes)
            self.assertEqual([item["case_id"] for item in exported["records"]], ["1", "2"])
            routes = {item["route"]: item for item in exported["routes"]}
            self.assertEqual(routes["B"]["input_tokens"]["unknown_count"], 1)

    def test_critical_error_cannot_be_accepted(self):
        with self.assertRaisesRegex(CompareError, "critical error cannot be accepted"):
            summarize([row("x", "A", critical_error=True)])

    def test_csv_numeric_validation_and_required_cost_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "bad.csv"
            fields = list(row("x", "A"))
            with source.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow(row("x", "A", calls=1.5))
            with self.assertRaisesRegex(CompareError, "calls must be an integer"):
                load_records(source)
        with self.assertRaisesRegex(CompareError, "cost_unit is required"):
            summarize([row("x", "A", cost=1)])


if __name__ == "__main__":
    unittest.main()






