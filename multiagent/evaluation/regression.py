# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Pure comparison of two reports of the same evaluation set."""
from __future__ import annotations

from typing import Any

REGRESSED, IMPROVED, CHANGED, UNCHANGED = "regressed", "improved", "changed", "unchanged"


def _case_fingerprint(meta: dict[str, Any], case_id: str) -> dict[str, Any]:
    fingerprint = meta.get("fingerprint", {})
    target = fingerprint.get("target", {})
    source = target.get("sha256_by_case", {}).get(case_id) if "sha256_by_case" in target else target
    return {"source": source, "models": fingerprint.get("provider_models_by_case", {}).get(case_id)}


def fingerprint_changes(base: dict[str, Any], candidate: dict[str, Any]) -> dict[str, bool]:
    """What differs between two fingerprints: agent or prompt, workflow, models and providers, or the set."""
    old, new = base.get("target", {}), candidate.get("target", {})
    kinds = {old.get("kind"), new.get("kind")}
    return {
        "agent_or_prompt": bool(kinds & {"agent", "prompt"}) and old != new,
        "workflow": "workflow" in kinds and (len(kinds) > 1 or old.get("sha256_by_case") != new.get("sha256_by_case")),
        "models": base.get("provider_models_by_case") != candidate.get("provider_models_by_case"),
        "set_changed": base.get("set_sha256") != candidate.get("set_sha256"),
    }


def compare_reports(base: dict[str, Any], candidate: dict[str, Any], *, threshold: float = 0.0) -> dict[str, Any]:
    """Classify each case as regressed, improved, changed or unchanged.

    A case regresses when it passed and no longer does, or when its score drops by more than ``threshold``;
    ``improved`` is the opposite. ``changed`` means its check results or its fingerprint differ without
    crossing the threshold. Cases present in only one report are listed apart and do not count as regressions.
    """
    old = {case["id"]: case for case in base["cases"]}
    new = {case["id"]: case for case in candidate["cases"]}
    cases = []
    for case_id in (name for name in old if name in new):
        before, after = old[case_id], new[case_id]
        delta = round(after["score"] - before["score"], 4)
        results_before = {check["name"]: check["passed"] for check in before["checks"]}
        results_after = {check["name"]: check["passed"] for check in after["checks"]}
        changed_checks = sorted(name for name in results_before.keys() | results_after.keys()
                                if results_before.get(name) != results_after.get(name))
        if (before["passed"] and not after["passed"]) or -delta > threshold:
            status = REGRESSED
        elif (after["passed"] and not before["passed"]) or delta > threshold:
            status = IMPROVED
        elif changed_checks or (_case_fingerprint(base.get("meta", {}), case_id)
                                != _case_fingerprint(candidate.get("meta", {}), case_id)):
            status = CHANGED
        else:
            status = UNCHANGED
        cases.append({"id": case_id, "status": status, "base_passed": before["passed"],
                      "candidate_passed": after["passed"], "base_score": before["score"],
                      "candidate_score": after["score"], "delta": delta, "changed_checks": changed_checks})
    summary = {name: sum(case["status"] == name for case in cases)
               for name in (REGRESSED, IMPROVED, CHANGED, UNCHANGED)}
    return {
        "threshold": threshold,
        "regression": summary[REGRESSED] > 0,
        "score": {"base": base["score"], "candidate": candidate["score"],
                  "delta": round(candidate["score"] - base["score"], 4)},
        "summary": summary,
        "cases": cases,
        "only_in_base": [name for name in old if name not in new],
        "only_in_candidate": [name for name in new if name not in old],
        "fingerprint": fingerprint_changes(base.get("meta", {}).get("fingerprint", {}),
                                           candidate.get("meta", {}).get("fingerprint", {})),
    }
