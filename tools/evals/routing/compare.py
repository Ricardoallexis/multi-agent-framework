"""Offline comparison of previously recorded routing evaluation results.

This module only reads and compares supplied records. It never runs a model.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "CompareError",
    "load_records",
    "summarize",
    "compare_results",
    "export_report",
    "main",
]

_REQUIRED = (
    "case_id", "route", "input_version", "input_hash", "rubric_version",
    "rubric_hash", "config_hash", "surface", "accepted", "critical_error",
)
_NULLABLE_NUMBERS = (
    "calls", "input_tokens", "output_tokens", "cost", "duration_ms",
    "rework", "escalations",
)
_TEXT_FIELDS = (
    "case_id", "route", "input_version", "input_hash", "rubric_version",
    "rubric_hash", "config_hash", "surface", "evaluated_at", "requested", "effective", "observed",
    "cost_unit", "category",
)
_CONTEXT_EVENT_FIELDS = frozenset({
    "event_type", "step_id", "attempt", "level", "ratio", "warning_at", "threshold",
    "tokens_before", "tokens_after", "summary_ref",
})
_CONTEXT_EVENT_TYPES = frozenset({"context_warning", "context_compacted"})
_BOOL_FIELDS = ("accepted", "critical_error")


class CompareError(ValueError):
    """An input record is malformed or contains a duplicate observation."""


def _number(value: Any, field: str, row: int) -> int | float | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError as exc:
            raise CompareError(f"row {row}: {field} must be a non-negative number or null") from exc
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CompareError(f"row {row}: {field} must be a non-negative number or null")
    if not math.isfinite(value) or value < 0:
        raise CompareError(f"row {row}: {field} must be finite and non-negative")
    if field in ("calls", "input_tokens", "output_tokens", "rework", "escalations"):
        if not float(value).is_integer():
            raise CompareError(f"row {row}: {field} must be an integer")
        return int(value)
    return value


def _normalize(raw: Mapping[str, Any], row: int) -> dict[str, Any]:
    missing = [key for key in _REQUIRED if key not in raw or raw[key] is None or raw[key] == ""]
    if missing:
        raise CompareError(f"row {row}: missing required fields: {', '.join(missing)}")
    record: dict[str, Any] = {}
    for key in _TEXT_FIELDS:
        value = raw.get(key)
        if value is not None and not isinstance(value, str):
            raise CompareError(f"row {row}: {key} must be text or null")
        if key in _REQUIRED and not value:
            raise CompareError(f"row {row}: {key} must be non-empty text")
        record[key] = value or None
    for key in _BOOL_FIELDS:
        value = raw[key]
        if isinstance(value, bool):
            record[key] = value
        elif isinstance(value, str) and value.lower() in ("true", "false"):
            record[key] = value.lower() == "true"
        else:
            raise CompareError(f"row {row}: {key} must be true or false")
    for key in _NULLABLE_NUMBERS:
        record[key] = _number(raw.get(key), key, row)
    if "context_events" in raw and raw["context_events"] not in (None, ""):
        events = raw["context_events"]
        if isinstance(events, str):
            try:
                events = json.loads(events)
            except json.JSONDecodeError as exc:
                raise CompareError(f"row {row}: context_events must be a JSON array") from exc
        if not isinstance(events, list):
            raise CompareError(f"row {row}: context_events must be a JSON array")
        normalized_events = []
        for event in events:
            if (not isinstance(event, Mapping) or not isinstance(event.get("event_type"), str)
                    or event["event_type"] not in _CONTEXT_EVENT_TYPES):
                raise CompareError(f"row {row}: context_events contains an invalid event")
            normalized_event = {key: event[key] for key in _CONTEXT_EVENT_FIELDS if key in event}
            if not isinstance(normalized_event.get("event_type"), str):
                raise CompareError(f"row {row}: context event type must be text")
            normalized_events.append(normalized_event)
        record["context_events"] = normalized_events
    if record["critical_error"] and record["accepted"]:
        raise CompareError(f"row {row}: a critical error cannot be accepted")
    if record["cost"] is not None and not record["cost_unit"]:
        raise CompareError(f"row {row}: cost_unit is required when cost is known")
    return record


def _read_json(path: Path) -> list[Mapping[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise CompareError(f"cannot read {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CompareError(f"invalid JSON: {exc}") from exc
    if isinstance(data, dict):
        data = data.get("records")
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise CompareError("JSON input must be an array of records or an object with a records array")
    return data


def load_records(path: str | Path) -> list[dict[str, Any]]:
    """Load JSON, JSONL, or CSV records and validate the public schema."""
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".json":
        raw_rows = _read_json(source)
    elif suffix == ".jsonl":
        try:
            lines = source.read_text(encoding="utf-8-sig").splitlines()
        except OSError as exc:
            raise CompareError(f"cannot read {source}: {exc}") from exc
        raw_rows = []
        for line_no, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CompareError(f"line {line_no}: invalid JSON: {exc}") from exc
            if not isinstance(value, dict):
                raise CompareError(f"line {line_no}: each JSONL line must be an object")
            raw_rows.append(value)
    elif suffix == ".csv":
        try:
            with source.open(newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames is None:
                    raise CompareError("CSV input has no header")
                raw_rows = list(reader)
        except OSError as exc:
            raise CompareError(f"cannot read {source}: {exc}") from exc
    else:
        raise CompareError("input extension must be .json, .jsonl, or .csv")

    records = [_normalize(row, index) for index, row in enumerate(raw_rows, 1)]
    seen: set[tuple[str, str]] = set()
    for index, record in enumerate(records, 1):
        key = (record["case_id"], record["route"])
        if key in seen:
            raise CompareError(f"row {index}: duplicate case_id/route {key!r}")
        seen.add(key)
    return sorted(records, key=lambda item: (item["case_id"], item["route"]))


def _metric(values: Iterable[int | float | None]) -> dict[str, Any]:
    items = list(values)
    present = [value for value in items if value is not None]
    return {
        "known_total": sum(present),
        "known_count": len(present),
        "unknown_count": sum(value is None for value in items),
    }


def _route_summary(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    accepted = sum(row["accepted"] for row in records)
    summary: dict[str, Any] = {
        "route": records[0]["route"],
        "attempted_cases": len(records),
        "accepted": {"numerator": accepted, "denominator": len(records)},
        "failed": {"numerator": len(records) - accepted, "denominator": len(records)},
        "critical_errors": {"numerator": sum(row["critical_error"] for row in records), "denominator": len(records)},
    }
    for field in _NULLABLE_NUMBERS:
        summary[field] = _metric(row[field] for row in records)
    costs = [row["cost"] for row in records]
    units = {row["cost_unit"] for row in records if row["cost"] is not None}
    all_costs_known = all(value is not None for value in costs)

    if len(units) > 1:
        summary["cost_total"] = {"status": "incomparable_units", "units": sorted(units)}
        summary["cost_per_accepted_case"] = {"status": "incomparable_units", "units": sorted(units)}
    elif not all_costs_known:
        summary["cost_total"] = {"status": "unknown", "known_subtotal": sum(value for value in costs if value is not None), "unknown_count": sum(value is None for value in costs), "unit": next(iter(units), None)}
        summary["cost_per_accepted_case"] = {"status": "unknown", "reason": "at least one attempt cost is unknown; failures are included in the numerator", "unit": next(iter(units), None)}
    elif accepted == 0:
        total = sum(costs)  # all known
        summary["cost_total"] = {"status": "known", "value": total, "unit": next(iter(units), None)}
        summary["cost_per_accepted_case"] = {"status": "undefined", "reason": "no accepted cases", "unit": next(iter(units), None)}
    else:
        total = sum(costs)  # includes accepted and failed attempts
        summary["cost_total"] = {"status": "known", "value": total, "unit": next(iter(units), None)}
        summary["cost_per_accepted_case"] = {"status": "known", "numerator": total, "denominator": accepted, "value": total / accepted, "unit": next(iter(units), None)}
    return summary


def _pair_summaries(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_case: dict[str, dict[str, Mapping[str, Any]]] = {}
    for row in records:
        by_case.setdefault(row["case_id"], {})[row["route"]] = row
    routes = sorted({row["route"] for row in records})
    pairs: list[dict[str, Any]] = []
    for left_index, left_route in enumerate(routes):
        for right_route in routes[left_index + 1:]:
            metrics: dict[str, Any] = {}
            comparable_cases = 0
            excluded: dict[str, int] = {}
            deltas: dict[str, list[float]] = {name: [] for name in (*_NULLABLE_NUMBERS, "accepted", "critical_error")}
            for case_id in sorted(by_case):
                group = by_case[case_id]
                if left_route not in group or right_route not in group:
                    excluded["route_missing"] = excluded.get("route_missing", 0) + 1
                    continue
                left, right = group[left_route], group[right_route]
                reason = None
                for field in ("input_version", "input_hash", "rubric_version", "rubric_hash", "config_hash"):
                    if left[field] != right[field]:
                        reason = f"{field}_mismatch"
                        break
                if reason is None and left["surface"] != right["surface"]:
                    reason = "surface_mismatch"
                if reason:
                    excluded[reason] = excluded.get(reason, 0) + 1
                    continue
                comparable_cases += 1
                for name in (*_NULLABLE_NUMBERS, "accepted", "critical_error"):
                    a, b = left[name], right[name]
                    if a is None or b is None:
                        excluded[f"{name}_unknown"] = excluded.get(f"{name}_unknown", 0) + 1
                    else:
                        deltas[name].append((float(b) - float(a)))
            for name, values in deltas.items():
                metrics[name] = {"numerator": sum(values), "denominator": len(values), "unknown_or_excluded": excluded.get(f"{name}_unknown", 0)}
            # Keep monetary deltas grouped by unit across the entire comparison.
            cost_deltas: dict[str, list[float]] = {}
            cost_reasons: dict[str, int] = {}
            for case_id in sorted(by_case):
                group = by_case[case_id]
                if left_route not in group or right_route not in group:
                    continue
                a, b = group[left_route], group[right_route]
                if any(a[field] != b[field] for field in ("input_version", "input_hash", "rubric_version", "rubric_hash", "config_hash", "surface")):
                    continue
                if a["cost"] is None or b["cost"] is None:
                    cost_reasons["cost_unknown"] = cost_reasons.get("cost_unknown", 0) + 1
                elif not a["cost_unit"] or not b["cost_unit"]:
                    cost_reasons["cost_unit_unknown"] = cost_reasons.get("cost_unit_unknown", 0) + 1
                elif a["cost_unit"] != b["cost_unit"]:
                    cost_reasons["cost_unit_mismatch"] = cost_reasons.get("cost_unit_mismatch", 0) + 1
                else:
                    unit = a["cost_unit"]
                    cost_deltas.setdefault(unit, []).append(float(b["cost"]) - float(a["cost"]))
            by_unit = {
                unit: {"numerator": sum(values), "denominator": len(values)}
                for unit, values in sorted(cost_deltas.items())
            }
            if len(by_unit) > 1:
                metrics["cost"] = {
                    "status": "incomparable_units",
                    "units": list(by_unit),
                    "by_unit": by_unit,
                    "unknown_or_excluded": cost_reasons,
                }
            elif by_unit:
                unit, totals = next(iter(by_unit.items()))
                metrics["cost"] = {
                    "status": "comparable",
                    "unit": unit,
                    **totals,
                    "unknown_or_excluded": cost_reasons,
                }
            else:
                metrics["cost"] = {
                    "status": "unknown",
                    "numerator": 0,
                    "denominator": 0,
                    "unknown_or_excluded": cost_reasons,
                }
            pairs.append({"left_route": left_route, "right_route": right_route, "comparable_cases": comparable_cases, "excluded_cases": excluded, "delta_convention": "right minus left", "metrics": metrics})
    return pairs


def summarize(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build deterministic per-route aggregates and paired comparisons."""
    normalized = sorted((_normalize(row, index) for index, row in enumerate(records, 1)), key=lambda item: (item["case_id"], item["route"]))
    seen: set[tuple[str, str]] = set()
    for row in normalized:
        key = (row["case_id"], row["route"])
        if key in seen:
            raise CompareError(f"duplicate case_id/route {key!r}")
        seen.add(key)
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for row in normalized:
        groups.setdefault(row["route"], []).append(row)
    return {
        "schema_version": 1,
        "records": normalized,
        "routes": [_route_summary(groups[key]) for key in sorted(groups)],
        "paired_comparisons": _pair_summaries(normalized),
    }


def compare_results(path: str | Path) -> dict[str, Any]:
    """Load an input file and produce a JSON-serializable comparison report."""
    return summarize(load_records(path))


def export_report(report: Mapping[str, Any], output: str | Path, format: str | None = None) -> None:
    """Export a full JSON report or normalized records as JSONL/CSV."""
    destination = Path(output)
    kind = (format or destination.suffix.lstrip(".")).lower()
    if kind == "json":
        with destination.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    elif kind in ("jsonl", "csv"):
        records = report.get("records", [])
        fields = list(_TEXT_FIELDS) + list(_BOOL_FIELDS) + list(_NULLABLE_NUMBERS)
        if any("context_events" in row for row in records):
            fields.append("context_events")
        if kind == "jsonl":
            content = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in records)
            with destination.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
        else:
            with destination.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
                writer.writeheader()
                for row in records:
                    writer.writerow({
                        key: ("" if row.get(key) is None else
                              json.dumps(row[key], ensure_ascii=False, sort_keys=True)
                              if key == "context_events" else row.get(key))
                        for key in fields
                    })
    else:
        raise CompareError("output format must be json, jsonl, or csv")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare pre-existing routing evaluation results offline.")
    parser.add_argument("input", help=".json, .jsonl, or .csv result records")
    parser.add_argument("-o", "--output", help="write report/normalized records to this file")
    parser.add_argument("--format", choices=("json", "jsonl", "csv"), help="output format (default: inferred from output extension)")
    args = parser.parse_args(argv)
    try:
        report = compare_results(args.input)
        if args.output:
            export_report(report, args.output, args.format)
        else:
            sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    except (CompareError, OSError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())







