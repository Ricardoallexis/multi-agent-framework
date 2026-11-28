# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Print a Markdown comparison of numerical benchmark fields."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def numbers(value, prefix: str = "") -> dict[str, float]:
    found = {}
    if isinstance(value, dict):
        for key, child in value.items():
            found.update(numbers(child, f"{prefix}.{key}" if prefix else key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            label = (f"tasks_n={child['tasks_n']}" if isinstance(child, dict) and "tasks_n" in child
                     else f"size_kb={child['size_kb']}" if isinstance(child, dict) and "size_kb" in child
                     else f"threads={child['threads']}" if isinstance(child, dict) and "threads" in child
                     else f"bundles={child['bundles']}" if isinstance(child, dict) and "bundles" in child
                     else str(index))
            found.update(numbers(child, f"{prefix}[{label}]"))
    elif type(value) in (int, float):
        found[prefix] = float(value)
    return found


def compare(before: dict, after: dict) -> str:
    old, new = numbers(before), numbers(after)
    lines = ["| Metric | Before | After | Ratio |", "| --- | ---: | ---: | ---: |"]
    for key in sorted(old.keys() | new.keys()):
        if key.startswith("derived.") or key.startswith("polling_vs_push.expected_mean_delay_"):
            continue
        a, b = old.get(key), new.get(key)
        ratio = f"{b / a:.2f}x" if a not in (None, 0) and b is not None else "-"
        before_text = f"{a:g}" if a is not None else "-"
        after_text = f"{b:g}" if b is not None else "-"
        lines.append(f"| {key} | {before_text} | {after_text} | {ratio} |")
    old_dir, new_dir = before.get("work_dir", "not recorded"), after.get("work_dir", "not recorded")
    if old_dir != new_dir:
        lines += ["", f"Warning: work directories differ ({old_dir} vs {new_dir}); "
                  "timings that write to disk are not comparable."]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    args = parser.parse_args()
    print(compare(json.loads(args.before.read_text(encoding="utf-8")),
                  json.loads(args.after.read_text(encoding="utf-8"))))


if __name__ == "__main__":
    main()
