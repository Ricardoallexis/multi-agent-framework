"""Export routing comparator records from a framework database (ECO-T07).

Reads the runs, usage telemetry and run events of a labelled set of finished runs and writes one record per
case and route in the format of ``compare.py`` (RUT-T08). It only reads: the database is opened read-only,
no model, provider or evaluator is called, and nothing is activated.

Privacy: a record carries identifiers, hashes, counters, tokens, costs and model ids only. Prompts, run
requests, artifacts, event payloads and error messages are never copied. Only the whitelisted keys of a label
are read, so a corpus row with its input text can be passed as a label without leaking it.

Outcome rules, per run:

* ``accepted``: the run is ``completed`` (a human approved the last review), did not end by exhausting its
  budget and has no critical error. A label may set ``accepted: false`` (an evaluator rejected a run the
  framework completed) but can never accept a run that did not complete or that has a critical error.
* ``critical_error``: an adjudication the framework cannot make, so it comes from the label. A label without
  it is an error unless ``assume_no_critical_errors`` is set, which records ``false`` for those cases.
* ``calls``: provider calls recorded in the usage telemetry (failed attempts included, human steps not).
* ``input_tokens`` / ``output_tokens`` / ``duration_ms``: sums over those calls; unknown (null) when any call did
  not report the value. A run with no model call has measured zeros.
* ``cost`` / ``cost_unit``: sum of the estimated costs of those calls with their currency; unknown (null) when
  any call has no cost or the currencies differ. Never a 0 for an unknown cost.
* ``rework``: ``human_revision`` events (a human asked for changes). ``escalations``: ``model_escalated`` events.
* ``route``: the label, checked against the routing profile the run's events show (``legacy`` means none).
* ``input_hash`` and ``config_hash`` default to a hash of the run request and of the workflow/budget settings.

Usage::

    python tools/evals/routing/export_runs.py --db data/multiagent.db --labels labels.jsonl --output records.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

try:
    from tools.evals.routing import compare
except ImportError:  # run as a script: the repository root is not on the path yet
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from tools.evals.routing import compare

from multiagent.observability.usage import read_usage

__all__ = ["ExportError", "LEGACY_ROUTE", "load_labels", "export_records", "main"]

LEGACY_ROUTE = "legacy"
_TERMINAL = frozenset({"completed", "failed", "rejected", "cancelled"})
_MAX_LABEL_TEXT = 200
_REQUIRED_LABELS = ("run_id", "case_id", "route", "input_version", "rubric_version", "rubric_hash")
_OPTIONAL_TEXT_LABELS = ("category", "input_hash", "config_hash", "surface")
_BOOL_LABELS = ("accepted", "critical_error")
_CONTEXT_EVENT_TYPES = frozenset({"context_warning", "context_compacted"})
_CONTEXT_EVENT_FIELDS = (
    "step_id", "attempt", "level", "ratio", "warning_at", "threshold",
    "tokens_before", "tokens_after", "summary_ref",
)


class ExportError(ValueError):
    """A label, run or database cannot be exported without guessing."""


@contextmanager
def _read_only(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    path = Path(db_path)
    if not path.is_file():
        raise ExportError(f"database not found: {path}")
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


class _UsageSource:
    """Just enough of a store for ``read_usage`` over a read-only connection."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self.db = self

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        yield self._conn


def _check_text(value: Any, key: str, row: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExportError(f"label {row}: {key} must be non-empty text")
    if len(value) > _MAX_LABEL_TEXT:
        raise ExportError(f"label {row}: {key} is longer than {_MAX_LABEL_TEXT} characters (labels hold identifiers, not content)")
    return value.strip()


def _normalize_label(raw: Any, row: int) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ExportError(f"label {row}: each label must be an object")
    label: dict[str, Any] = {}
    for key in _REQUIRED_LABELS:
        if raw.get(key) in (None, ""):
            raise ExportError(f"label {row}: missing required field {key}")
        label[key] = _check_text(raw[key], key, row)
    for key in _OPTIONAL_TEXT_LABELS:
        if raw.get(key) not in (None, ""):
            label[key] = _check_text(raw[key], key, row)
    for key in _BOOL_LABELS:
        if key in raw and raw[key] is not None:
            if not isinstance(raw[key], bool):
                raise ExportError(f"label {row}: {key} must be true or false")
            label[key] = raw[key]
    return label


def load_labels(path: str | Path) -> list[dict[str, Any]]:
    """Load and validate the labels of a measurement (a JSON array, ``{"runs": [...]}`` or JSONL)."""
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ExportError(f"cannot read {source}: {exc}") from exc
    try:
        if source.suffix.lower() == ".jsonl":
            raw = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            raw = json.loads(text)
            if isinstance(raw, dict):
                raw = raw.get("runs")
    except json.JSONDecodeError as exc:
        raise ExportError(f"invalid JSON in {source}: {exc}") from exc
    if not isinstance(raw, list):
        raise ExportError("labels must be a JSON array, an object with a runs array, or JSONL")
    return [_normalize_label(item, index) for index, item in enumerate(raw, 1)]


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _profiles_seen(events: Sequence[Mapping[str, Any]]) -> set[str]:
    seen = set()
    for event in events:
        if event["event_type"] in ("routing_decision", "model_attempt"):
            profile = event["payload"].get("profile_id")
            if isinstance(profile, str) and profile:
                seen.add(profile)
    return seen


def _ordered_unique(values: Sequence[Any]) -> str | None:
    unique = list(dict.fromkeys(str(value) for value in values if value))
    return ",".join(unique) if unique else None


def _context_events(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the context-window facts needed to interpret run quality after compaction."""
    exported = []
    for event in events:
        event_type = event["event_type"]
        if event_type not in _CONTEXT_EVENT_TYPES:
            continue
        payload = event["payload"]
        exported.append({"event_type": event_type, **{
            key: payload[key] for key in _CONTEXT_EVENT_FIELDS if key in payload
        }})
    return exported


def _usage_totals(calls: list[dict[str, Any]], events: Sequence[Mapping[str, Any]], cost_unit: str) -> dict[str, Any]:
    """Calls, tokens, duration and cost of the provider calls; unknown stays ``None``."""
    if any(event["event_type"] == "telemetry_failed" for event in events):
        return {"calls": None, "input_tokens": None, "output_tokens": None, "duration_ms": None,
                "cost": None, "cost_unit": None}

    def total(field: str) -> int | None:
        values = [record[field] for record in calls]
        return None if any(value is None for value in values) else sum(values)

    amounts: list[tuple[float, str]] = []
    unknown = False
    for record in calls:
        basis = record.get("cost_basis") or {}
        if record["estimated_cost_usd"] is not None:
            amounts.append((record["estimated_cost_usd"], "USD"))
        elif (isinstance(basis.get("cost"), (int, float)) and not isinstance(basis.get("cost"), bool)
              and isinstance(basis.get("currency"), str) and basis["currency"]):
            amounts.append((basis["cost"], basis["currency"]))
        else:
            unknown = True
    currencies = {currency for _, currency in amounts}
    if not calls:
        cost, unit = 0, cost_unit
    elif unknown or len(currencies) != 1:
        cost, unit = None, None
    else:
        cost, unit = sum(amount for amount, _ in amounts), next(iter(currencies))
    return {"calls": len(calls), "input_tokens": total("tokens_in"), "output_tokens": total("tokens_out"),
            "duration_ms": total("duration_ms"), "cost": cost, "cost_unit": unit}


def _export_one(conn: sqlite3.Connection, source: _UsageSource, label: Mapping[str, Any], *, surface: str,
                cost_unit: str, assume_no_critical_errors: bool, check_route: bool) -> dict[str, Any]:
    run_id = label["run_id"]
    row = conn.execute(
        "SELECT status,workflow_id,max_llm_calls,max_run_seconds,brand_version,budget_exhausted,request_json "
        "FROM runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise ExportError(f"run not found: {run_id}")
    if row["status"] not in _TERMINAL:
        raise ExportError(f"run {run_id} is {row['status']}: only finished runs "
                          f"({', '.join(sorted(_TERMINAL))}) have an outcome to compare")
    events = [{"event_type": item["event_type"], "payload": json.loads(item["payload_json"] or "{}")}
              for item in conn.execute("SELECT event_type,payload_json FROM run_events WHERE run_id=? ORDER BY id", (run_id,))]

    route = label["route"]
    if check_route:
        profiles = _profiles_seen(events)
        expected = set() if route == LEGACY_ROUTE else {route}
        if profiles != expected:
            shown = ", ".join(sorted(profiles)) or "none"
            raise ExportError(f"run {run_id} is labelled route {route!r} but its events show routing profile(s): {shown}")

    completed = row["status"] == "completed" and not row["budget_exhausted"]
    if "critical_error" in label:
        critical = label["critical_error"]
    elif assume_no_critical_errors:
        critical = False
    else:
        raise ExportError(f"run {run_id} (case {label['case_id']}): the label has no critical_error adjudication "
                          "(set it, or assume none explicitly)")
    accepted = label.get("accepted", completed and not critical)     # by convention a critical error rejects the case
    if accepted and not completed:
        raise ExportError(f"run {run_id} did not complete (status {row['status']}"
                          f"{', budget exhausted' if row['budget_exhausted'] else ''}): it cannot be labelled accepted")
    if critical and accepted:
        raise ExportError(f"run {run_id}: a run with a critical error cannot be accepted")

    usage = [record for record in read_usage(source, run_id) if record["provider"] != "human"]  # human steps are not calls
    totals = _usage_totals(usage, events, cost_unit)
    requested = [event["payload"].get("selected_model") or event["payload"].get("preferred_model")
                 for event in events if event["event_type"] == "routing_decision"]
    request = json.loads(row["request_json"] or "{}")
    record = {
        "case_id": label["case_id"],
        "route": route,
        "category": label.get("category"),
        "input_version": label["input_version"],
        "input_hash": label.get("input_hash") or _hash(request),
        "rubric_version": label["rubric_version"],
        "rubric_hash": label["rubric_hash"],
        "config_hash": label.get("config_hash") or _hash({
            "workflow_id": row["workflow_id"], "max_llm_calls": row["max_llm_calls"],
            "max_run_seconds": row["max_run_seconds"], "brand_version": row["brand_version"]}),
        "surface": label.get("surface") or surface,
        "accepted": accepted,
        "critical_error": critical,
        "requested": _ordered_unique(requested),
        "effective": _ordered_unique([record["model"] for record in usage]),
        "rework": sum(1 for event in events if event["event_type"] == "human_revision"),
        "escalations": sum(1 for event in events if event["event_type"] == "model_escalated"),
        **totals,
    }
    context_events = _context_events(events)
    if context_events:
        record["context_events"] = context_events
    return record


def export_records(db_path: str | Path, labels: Sequence[Mapping[str, Any]], *, surface: str = "framework-run",
                   cost_unit: str = "USD", assume_no_critical_errors: bool = False,
                   check_route: bool = True) -> list[dict[str, Any]]:
    """Comparator records (validated and in the comparator's stable order) for the labelled runs.

    ``labels`` are validated with the same rules as ``load_labels``. ``cost_unit`` is only the unit given to
    the zero cost of a run that made no model call. Raises ``ExportError`` for an unknown or unfinished run,
    a route label that disagrees with the run's events, a missing ``critical_error`` adjudication (unless
    ``assume_no_critical_errors``), or an accepted label on a run that did not complete.
    """
    normalized = [_normalize_label(label, index) for index, label in enumerate(labels, 1)]
    seen: set[str] = set()
    for label in normalized:
        if label["run_id"] in seen:
            raise ExportError(f"run {label['run_id']} appears in more than one label")
        seen.add(label["run_id"])
    with _read_only(db_path) as conn:
        source = _UsageSource(conn)
        records = [_export_one(conn, source, label, surface=surface, cost_unit=cost_unit,
                               assume_no_critical_errors=assume_no_critical_errors, check_route=check_route)
                   for label in normalized]
    try:
        return compare.summarize(records)["records"]
    except compare.CompareError as exc:
        raise ExportError(str(exc)) from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export comparator records from a framework database. "
                                                 "Reads only; never calls a model or exports prompt content.")
    parser.add_argument("--db", required=True, help="framework SQLite database (opened read-only)")
    parser.add_argument("--labels", required=True, help="JSON or JSONL labels: run_id, case_id, route, input/rubric identity")
    parser.add_argument("-o", "--output", help="output file (.jsonl, .csv or .json); default: JSONL on stdout")
    parser.add_argument("--surface", default="framework-run", help="execution surface for labels without one")
    parser.add_argument("--cost-unit", default="USD", help="unit of the zero cost of a run with no model call")
    parser.add_argument("--assume-no-critical-errors", action="store_true",
                        help="record critical_error=false for labels that carry no adjudication")
    parser.add_argument("--no-route-check", action="store_true",
                        help="do not check the route label against the run's routing events")
    args = parser.parse_args(argv)
    try:
        records = export_records(args.db, load_labels(args.labels), surface=args.surface, cost_unit=args.cost_unit,
                                 assume_no_critical_errors=args.assume_no_critical_errors,
                                 check_route=not args.no_route_check)
        if args.output:
            compare.export_report({"records": records}, args.output)
        else:
            sys.stdout.write("".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in records))
    except (ExportError, compare.CompareError, OSError, sqlite3.Error) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
