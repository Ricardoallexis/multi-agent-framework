# Offline routing result comparison

`compare.py` compares already recorded A/B/C results. It imports no application clients and never executes a model, provider, evaluator, or rubric. All times are milliseconds; token counts and calls are non-negative integers. No monetary conversion or rate estimation is performed.

## Record schema

Each JSON object, JSONL line, or CSV row represents one case on one route. Required columns:

| Field | Meaning |
|---|---|
| `case_id`, `route` | Stable case identifier and arm/routing label; one row per pair |
| `input_version`, `input_hash` | Frozen input identity |
| `rubric_version`, `rubric_hash` | Frozen rubric/reference identity |
| `config_hash`, `surface` | Comparable harness/configuration identity and execution surface |
| `accepted`, `critical_error` | Boolean adjudication already recorded; critical errors reject a case by evaluator convention |

Optional text: `evaluated_at` (source evaluation date/time), `requested`, `effective`, `observed` (requested/effective/observed route or model labels), `cost_unit` (explicit unit, for example `USD`). Optional numeric values: `calls`, `input_tokens`, `output_tokens`, `cost`, `duration_ms`, `rework`, and `escalations`. Omitted, empty CSV cells, or JSON `null` mean unknown; numeric zero is a measured zero. Cost requires a `cost_unit`. Calls/tokens/rework/escalations must be integers. Values must be finite and non-negative. Duplicate `(case_id, route)` rows and malformed values are errors with row context.

For example, JSON is an array of objects (or `{"records": [...]}`); JSONL is one object per line; CSV uses the field names above as its header. The implementation normalizes imports into a stable case/route sort order.

## Comparison rules

Route aggregates include every supplied case, including failed cases and retries represented by `calls` and total attempt cost. Every numeric aggregate reports a known subtotal/count and unknown count. Cost per accepted case divides total route cost, including failed attempts, by accepted cases; it is unknown if any attempt cost is unknown, units are mixed, or cost is not reported. A zero accepted denominator is undefined.

Paired case metrics require matching input version/hash, rubric version/hash, config hash, and surface. A failed match is excluded with a visible reason. Quality metrics can remain comparable when cost is unknown; each metric reports its own denominator and unknown/excluded count. Monetary deltas additionally require known cost and the same explicit unit within each case. Deltas from different cases are grouped by unit; when more than one unit is present, the combined monetary delta is marked incomparable and only per-unit numerators/denominators are returned. Delta means right route minus left route; numerator/denominator are emitted so downstream users can calculate rates without losing the evidence.

## Public API and CLI

Import `load_records(path)`, `summarize(records)`, `compare_results(path)`, `export_report(report, output, format=None)`, or `main(argv=None)` from `tools.evals.routing.compare` (namespace-package import from the repository root). The CLI prints the full deterministic JSON report, or exports it:

```powershell
python tools/evals/routing/compare.py results.jsonl --output report.json
python tools/evals/routing/compare.py results.csv --output normalized.jsonl
python tools/evals/routing/compare.py results.json --output normalized.csv
```

JSON output contains normalized records, per-route aggregates, and paired comparisons. JSONL/CSV exports contain normalized records for round-tripping. Inputs and outputs are local files supplied by the caller; keep private inputs/outputs outside version control. No model or provider call is made.


