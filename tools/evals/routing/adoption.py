"""Adoption criterion for the economic routing profile (ECO-T07; ECO-T01 contract, section 8).

Applies the approved criterion to comparator records (``compare.py``, RUT-T08) per corpus category and for
all cases together, and reports a verdict with the numbers behind it. It reads records only: no model,
provider or evaluator is called, and nothing is activated. The verdict is evidence for the user, who decides
whether the profile becomes a default.

The candidate route (the economic profile) is **adopted** for a group of cases only when all of these hold,
measured on the cases both routes ran with the same frozen input, rubric, configuration and surface:

1. at least ``min_cases`` paired cases (20 per category);
2. cost per accepted run strictly lower than the baseline's (``legacy``), failed attempts included;
3. acceptance rate not worse than the baseline's by more than ``max_acceptance_drop_points`` (2 points);
4. no new critical errors: no case where only the candidate has one.

The verdict of a group is ``not_adopt`` as soon as a criterion measurably fails (a cost that is not lower,
including a tie, a quality drop beyond the margin, or a new critical error), ``insufficient_data`` when it
cannot be decided (too few cases, or a cost that is unknown, undefined or in incomparable units) and
``adopt`` otherwise. A failing criterion on fewer than ``min_cases`` cases does not decide ``not_adopt``: the
group stays ``insufficient_data`` and lists the criteria already failing. The overall verdict is ``not_adopt``
if any group (or all cases together) is ``not_adopt``, ``insufficient_data`` if any category is, or if cases
have no category, and ``adopt`` only when every category and the pooled cases adopt.

Usage::

    python tools/evals/routing/adoption.py records.jsonl --candidate economic --output verdict.md
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    from tools.evals.routing import compare
except ImportError:  # run as a script: the repository root is not on the path yet
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from tools.evals.routing import compare

__all__ = [
    "ADOPT", "NOT_ADOPT", "INSUFFICIENT", "AdoptionError", "evaluate", "evaluate_file", "render_markdown",
    "export_verdict", "main",
]

ADOPT, NOT_ADOPT, INSUFFICIENT = "adopt", "not_adopt", "insufficient_data"
DEFAULT_BASELINE = "legacy"
DEFAULT_MIN_CASES = 20
DEFAULT_MAX_ACCEPTANCE_DROP_POINTS = 2
UNCATEGORIZED = "(uncategorized)"
_PAIRING_FIELDS = ("input_version", "input_hash", "rubric_version", "rubric_hash", "config_hash", "surface", "category")
_LABELS = {ADOPT: "adopt", NOT_ADOPT: "not adopt", INSUFFICIENT: "insufficient data"}


class AdoptionError(ValueError):
    """The records or the options cannot produce a verdict."""


def _reason(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def _pick_routes(rows: Sequence[Mapping[str, Any]], baseline: str, candidate: str | None) -> tuple[str, str]:
    routes = sorted({row["route"] for row in rows})
    if baseline not in routes:
        raise AdoptionError(f"baseline route {baseline!r} has no records (routes found: {', '.join(routes) or 'none'})")
    if candidate is None:
        others = [route for route in routes if route != baseline]
        if len(others) != 1:
            raise AdoptionError("name the candidate route (--candidate): the records have "
                                f"{len(others)} routes besides the baseline ({', '.join(others) or 'none'})")
        candidate = others[0]
    if candidate == baseline:
        raise AdoptionError("the candidate route must differ from the baseline route")
    if candidate not in routes:
        raise AdoptionError(f"candidate route {candidate!r} has no records (routes found: {', '.join(routes)})")
    return baseline, candidate


def _pair_cases(rows: Sequence[Mapping[str, Any]], baseline: str, candidate: str):
    """Cases both routes ran under the same identity, plus how many were left out and why."""
    by_case: dict[str, dict[str, Mapping[str, Any]]] = {}
    for row in rows:
        by_case.setdefault(row["case_id"], {})[row["route"]] = row
    pairs: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    excluded: dict[str, int] = {}
    for case_id in sorted(by_case):
        group = by_case[case_id]
        if baseline not in group or candidate not in group:
            excluded["route_missing"] = excluded.get("route_missing", 0) + 1
            continue
        left, right = group[baseline], group[candidate]
        mismatch = next((f"{field}_mismatch" for field in _PAIRING_FIELDS if left[field] != right[field]), None)
        if mismatch:
            excluded[mismatch] = excluded.get(mismatch, 0) + 1
            continue
        pairs.append((left, right))
    return pairs, excluded


def _digest(route: Mapping[str, Any]) -> dict[str, Any]:
    accepted = route["accepted"]
    per_case = route["cost_per_accepted_case"]
    return {
        "cases": route["attempted_cases"],
        "accepted": accepted["numerator"],
        "acceptance_rate": accepted["numerator"] / accepted["denominator"] if accepted["denominator"] else None,
        "critical_errors": route["critical_errors"]["numerator"],
        "cost_total": route["cost_total"],
        "cost_per_accepted": per_case,
        **{name: route[name] for name in ("calls", "escalations", "rework", "input_tokens", "output_tokens", "duration_ms")},
    }


def _cost_check(base: Mapping[str, Any], cand: Mapping[str, Any]) -> dict[str, Any]:
    """Cost per accepted run: lower is a pass, equal or higher a fail, anything not comparable is unknown."""
    result: dict[str, Any] = {"status": "unknown", "reason": None, "baseline": None, "candidate": None, "unit": None}
    statuses = {base["status"], cand["status"]}
    if "incomparable_units" in statuses:
        result["reason"] = "cost_units_incomparable"
    elif "unknown" in statuses:
        which = [name for name, item in (("baseline", base), ("candidate", cand)) if item["status"] == "unknown"]
        result["reason"] = "cost_unknown"
        result["detail"] = f"cost of {' and '.join(which)} has at least one unknown attempt cost"
    elif "undefined" in statuses:
        which = [name for name, item in (("baseline", base), ("candidate", cand)) if item["status"] == "undefined"]
        result["reason"] = "cost_undefined"
        result["detail"] = f"no accepted case in {' and '.join(which)}"
    elif base["unit"] != cand["unit"]:
        result["reason"] = "cost_units_incomparable"
        result["detail"] = f"{base['unit']} against {cand['unit']}"
    else:
        low, high = base["value"], cand["value"]
        result.update(baseline=low, candidate=high, unit=base["unit"], delta=high - low,
                      saving_pct=(100 * (low - high) / low) if low else None)
        if math.isclose(low, high, rel_tol=1e-9, abs_tol=1e-12):
            result.update(status="fail", reason="cost_tie")
        elif high < low:
            result.update(status="pass", reason="cost_lower")
        else:
            result.update(status="fail", reason="cost_higher")
    return result


def _evaluate_group(pairs: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]], baseline: str, candidate: str,
                    min_cases: int, max_drop: Fraction) -> dict[str, Any]:
    count = len(pairs)
    group: dict[str, Any] = {"paired_cases": count, "required_cases": min_cases}
    if count == 0:
        group.update(verdict=INSUFFICIENT, reasons=[_reason("insufficient_cases", f"0 paired cases, {min_cases} required")],
                     checks=None, baseline=None, candidate=None, deltas=None)
        return group

    report = compare.summarize([row for pair in pairs for row in pair])
    routes = {item["route"]: item for item in report["routes"]}
    base, cand = _digest(routes[baseline]), _digest(routes[candidate])
    new_critical = sum(1 for left, right in pairs if right["critical_error"] and not left["critical_error"])
    fixed_critical = sum(1 for left, right in pairs if left["critical_error"] and not right["critical_error"])
    drop = (base["accepted"] - cand["accepted"]) * 100          # accepted-case difference in points times `count`
    drop_points = float(Fraction(drop, count))
    checks = {
        "cost": _cost_check(base["cost_per_accepted"], cand["cost_per_accepted"]),
        "acceptance": {
            "status": "pass" if drop <= max_drop * count else "fail",
            "reason": "acceptance_within_margin" if drop <= max_drop * count else "acceptance_regression",
            "baseline": base["acceptance_rate"], "candidate": cand["acceptance_rate"],
            "drop_points": drop_points, "max_drop_points": float(max_drop),
        },
        "critical_errors": {
            "status": "pass" if new_critical == 0 else "fail",
            "reason": "no_new_critical_errors" if new_critical == 0 else "new_critical_errors",
            "new_cases": new_critical, "fixed_cases": fixed_critical,
            "baseline": base["critical_errors"], "candidate": cand["critical_errors"],
        },
    }
    failing = [name for name, check in checks.items() if check["status"] == "fail"]
    unknown = [name for name, check in checks.items() if check["status"] == "unknown"]
    reasons: list[dict[str, str]] = []
    if count < min_cases:
        verdict = INSUFFICIENT
        reasons.append(_reason("insufficient_cases", f"{count} paired cases, {min_cases} required"))
        if failing:
            group["preliminary_failing_checks"] = failing
    elif failing:
        verdict = NOT_ADOPT
    elif unknown:
        verdict = INSUFFICIENT
    else:
        verdict = ADOPT
    for name in failing + unknown:                  # always shown, so a short group still tells what it already shows
        check = checks[name]
        reasons.append(_reason(check["reason"], _check_detail(name, check)))
    if verdict == ADOPT:
        reasons.append(_reason("criterion_met", "cost per accepted run lower, acceptance within margin, no new critical errors"))
    group.update(verdict=verdict, reasons=reasons, checks=checks, baseline=base, candidate=cand,
                 deltas={"acceptance_points": -drop_points, "new_critical_cases": new_critical,
                         "fixed_critical_cases": fixed_critical, "cost_per_accepted": checks["cost"].get("delta")})
    return group


def _check_detail(name: str, check: Mapping[str, Any]) -> str:
    if name == "cost":
        if check["status"] == "unknown":
            return check.get("detail") or check["reason"]
        return f"cost per accepted {check['baseline']:.6g} -> {check['candidate']:.6g} {check['unit']}"
    if name == "acceptance":
        return f"acceptance dropped {check['drop_points']:.2f} points (margin {check['max_drop_points']:g})"
    return f"{check['new_cases']} case(s) with a critical error only in the candidate"


def evaluate(records: Iterable[Mapping[str, Any]], *, baseline: str = DEFAULT_BASELINE, candidate: str | None = None,
             min_cases: int = DEFAULT_MIN_CASES, max_acceptance_drop_points: float | str = DEFAULT_MAX_ACCEPTANCE_DROP_POINTS,
             categories: Sequence[str] = ()) -> dict[str, Any]:
    """The adoption verdict for comparator ``records``: per category, pooled and overall.

    ``categories`` lists the categories of the corpus that must be covered; one with no paired case is
    reported with 0 cases and keeps the overall verdict at ``insufficient_data``.
    """
    if isinstance(min_cases, bool) or not isinstance(min_cases, int) or min_cases < 1:
        raise AdoptionError("min_cases must be a positive integer")
    try:
        max_drop = Fraction(str(max_acceptance_drop_points))
    except (ValueError, ZeroDivisionError) as exc:
        raise AdoptionError("max_acceptance_drop_points must be a number") from exc
    if max_drop < 0:
        raise AdoptionError("max_acceptance_drop_points must not be negative")
    try:
        rows = compare.summarize(list(records))["records"]
    except compare.CompareError as exc:
        raise AdoptionError(str(exc)) from exc
    if not rows:
        raise AdoptionError("no records to evaluate")
    baseline, candidate = _pick_routes(rows, baseline, candidate)
    pairs, excluded = _pair_cases(rows, baseline, candidate)

    by_category: dict[str, list] = {}
    for pair in pairs:
        by_category.setdefault(pair[0]["category"] or UNCATEGORIZED, []).append(pair)
    names = sorted(set(by_category) | {name for name in categories if name != UNCATEGORIZED})
    groups = [{"category": name, **_evaluate_group(by_category.get(name, []), baseline, candidate, min_cases, max_drop)}
              for name in names]
    pooled = {"category": "(all cases)", **_evaluate_group(pairs, baseline, candidate, min_cases, max_drop)}

    counts = {verdict: sum(1 for group in groups if group["verdict"] == verdict) for verdict in (ADOPT, NOT_ADOPT, INSUFFICIENT)}
    reasons: list[dict[str, str]] = []
    if not pairs:
        verdict = INSUFFICIENT
        reasons.append(_reason("no_paired_cases", "no case ran on both routes under the same input, rubric, configuration and surface"))
    else:
        failing = [group for group in groups if group["verdict"] == NOT_ADOPT]
        reasons += [_reason(f"category_{group['category']}", "not adopt: " + "; ".join(item["detail"] for item in group["reasons"]))
                    for group in failing]
        if pooled["verdict"] == NOT_ADOPT:
            reasons.append(_reason("pooled_not_adopt", "all cases together: " + "; ".join(item["detail"] for item in pooled["reasons"])))
        if failing or pooled["verdict"] == NOT_ADOPT:
            verdict = NOT_ADOPT
        else:
            short = [group for group in groups if group["verdict"] == INSUFFICIENT]
            reasons += [_reason(f"category_{group['category']}", "insufficient data: " + "; ".join(item["detail"] for item in group["reasons"]))
                        for group in short]
            if UNCATEGORIZED in by_category:
                reasons.append(_reason("cases_without_category", f"{len(by_category[UNCATEGORIZED])} paired case(s) carry no category"))
            if pooled["verdict"] == INSUFFICIENT and not short:
                reasons.append(_reason("pooled_insufficient", "all cases together: " + "; ".join(item["detail"] for item in pooled["reasons"])))
            verdict = INSUFFICIENT if reasons else ADOPT
            if verdict == ADOPT:
                reasons.append(_reason("criterion_met", "every category and all cases together meet the criterion"))
    return {
        "schema_version": 1,
        "criterion": {
            "source": "ECO-T01 contract, section 8",
            "baseline_route": baseline, "candidate_route": candidate,
            "min_cases_per_category": min_cases,
            "max_acceptance_drop_points": float(max_drop),
            "cost_rule": "cost per accepted run strictly lower than the baseline's, failed attempts included",
            "critical_rule": "no case with a critical error only in the candidate",
            "expected_categories": sorted(set(categories)),
        },
        "overall": {"verdict": verdict, "reasons": reasons, "categories": counts, "pooled": pooled},
        "categories": groups,
        "paired_cases": len(pairs),
        "excluded_cases": excluded,
    }


def evaluate_file(path: str | Path, **options: Any) -> dict[str, Any]:
    """Evaluate a comparator input: records as JSON/JSONL/CSV, or a ``compare.py`` JSON report."""
    try:
        return evaluate(compare.load_records(path), **options)
    except compare.CompareError as exc:
        raise AdoptionError(str(exc)) from exc


def _cell(value: float | None, percent: bool = False) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%" if percent else f"{value:.6g}"


def _row(group: Mapping[str, Any]) -> str:
    base, cand, checks = group.get("baseline"), group.get("candidate"), group.get("checks")
    if not base:
        return f"| {group['category']} | 0 | {_LABELS[group['verdict']]} | n/a | n/a | n/a | n/a |"
    cost = checks["cost"]
    if cost["status"] == "unknown":
        cost_text = f"unknown ({cost['reason']})"
    else:
        cost_text = f"{_cell(cost['baseline'])} -> {_cell(cost['candidate'])} {cost['unit']}"
    escalations = cand["escalations"]
    escal = f"{escalations['known_total']}" + (f" (+{escalations['unknown_count']} unknown)" if escalations["unknown_count"] else "")
    return (f"| {group['category']} | {group['paired_cases']} | {_LABELS[group['verdict']]} "
            f"| {_cell(base['acceptance_rate'], True)} -> {_cell(cand['acceptance_rate'], True)} "
            f"| {cost_text} | {checks['critical_errors']['new_cases']} | {escal} |")


def render_markdown(verdict: Mapping[str, Any]) -> str:
    """A readable report of ``evaluate``'s result."""
    criterion, overall = verdict["criterion"], verdict["overall"]
    lines = [
        "# Economic profile adoption verdict", "",
        f"**Verdict: {_LABELS[overall['verdict']]}** - candidate `{criterion['candidate_route']}` against baseline "
        f"`{criterion['baseline_route']}`, {verdict['paired_cases']} paired case(s).", "",
        "Criterion (ECO-T01 contract, section 8): at least "
        f"{criterion['min_cases_per_category']} paired cases per category; cost per accepted run lower than the baseline's "
        f"(failed attempts included); acceptance rate not worse by more than {criterion['max_acceptance_drop_points']:g} points; "
        "no new critical errors.", "",
        "| Category | Cases | Verdict | Acceptance (baseline -> candidate) | Cost per accepted run | New critical | Candidate escalations |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        *[_row(group) for group in (*verdict["categories"], overall["pooled"])], "",
    ]
    if overall["reasons"]:
        lines += ["## Reasons", "", *[f"- `{item['code']}`: {item['detail']}" for item in overall["reasons"]], ""]
    for group in (*verdict["categories"], overall["pooled"]):
        if group["verdict"] != ADOPT and group["reasons"]:
            lines += [f"### {group['category']}", "",
                      *[f"- `{item['code']}`: {item['detail']}" for item in group["reasons"]]]
            if group.get("preliminary_failing_checks"):
                lines.append(f"- already failing on the cases so far: {', '.join(group['preliminary_failing_checks'])}")
            lines.append("")
    if verdict["excluded_cases"]:
        lines += ["## Cases left out of the comparison", "",
                  *[f"- {reason}: {count}" for reason, count in sorted(verdict["excluded_cases"].items())], ""]
    lines += ["This report informs the decision; it activates nothing. Enabling the profile by default is the user's decision.", ""]
    return "\n".join(lines)


def export_verdict(verdict: Mapping[str, Any], output: str | Path, format: str | None = None) -> None:
    """Write the verdict as JSON (``.json``) or Markdown (``.md``)."""
    destination = Path(output)
    kind = (format or destination.suffix.lstrip(".")).lower()
    if kind == "json":
        text = json.dumps(verdict, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    elif kind in ("md", "markdown"):
        text = render_markdown(verdict)
    else:
        raise AdoptionError("verdict format must be json or md")
    with destination.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply the economic-profile adoption criterion to comparator records. "
                                                 "Reads only; the verdict is evidence, not an activation.")
    parser.add_argument("input", help="comparator records (.json, .jsonl, .csv) or a compare.py JSON report")
    parser.add_argument("--baseline", default=DEFAULT_BASELINE, help="current route label (default: legacy)")
    parser.add_argument("--candidate", help="economic route label (default: the only other route)")
    parser.add_argument("--min-cases", type=int, default=DEFAULT_MIN_CASES, help="paired cases required per category (default: 20)")
    parser.add_argument("--max-acceptance-drop-points", default=str(DEFAULT_MAX_ACCEPTANCE_DROP_POINTS),
                        help="largest acceptable acceptance-rate drop in percentage points (default: 2)")
    parser.add_argument("--category", action="append", default=[], help="a corpus category that must be covered (repeatable)")
    parser.add_argument("-o", "--output", help="write the verdict here (.json or .md); default: JSON on stdout")
    parser.add_argument("--format", choices=("json", "md"), help="output format (default: inferred from the extension)")
    args = parser.parse_args(argv)
    try:
        verdict = evaluate_file(args.input, baseline=args.baseline, candidate=args.candidate, min_cases=args.min_cases,
                                max_acceptance_drop_points=args.max_acceptance_drop_points, categories=args.category)
        if args.output:
            export_verdict(verdict, args.output, args.format)
        elif args.format == "md":
            sys.stdout.write(render_markdown(verdict))
        else:
            sys.stdout.write(json.dumps(verdict, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    except (AdoptionError, OSError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
