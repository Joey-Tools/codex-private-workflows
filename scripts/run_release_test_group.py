#!/usr/bin/env python3
"""Run one complete-module group from a release unittest discovery suite."""

from __future__ import annotations

import argparse
from collections import defaultdict
import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SUITE_ROOTS = {
    "overlay": REPO_ROOT / "tests",
    "review": REPO_ROOT / "personal_codex/skills/review-orchestration-playbook/tests",
}
# Approximate Ubuntu test seconds from PR #219 CI run 37949290538. These
# scheduling hints never filter discovery or determine whether a test runs.
MODULE_WEIGHTS = {
    "test_named_lane": 322,
    "test_review_workspace": 152,
    "test_workspace": 75,
    "test_providers": 63,
    "test_synthetic_tokens": 55,
    "test_state": 48,
    "test_personal_sync_reconciliation_safety": 75,
    "test_codex_personal_sync": 67,
    "test_private_overlay_package": 60,
    "test_pending_staging_cleanup": 54,
    "test_regular_agent_materialization": 29,
    "test_private_overlay_sync": 26,
}


def flatten_tests(suite: unittest.TestSuite) -> list[unittest.TestCase]:
    tests = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            tests.extend(flatten_tests(item))
        else:
            tests.append(item)
    return tests


def group_tests(
    tests: list[unittest.TestCase], group_count: int
) -> list[list[unittest.TestCase]]:
    if group_count < 1:
        raise ValueError("--group-count must be positive")
    modules = defaultdict(list)
    for test in tests:
        modules[type(test).__module__].append(test)

    def weight(module: str) -> int:
        return MODULE_WEIGHTS.get(module.rsplit(".", 1)[-1], 1)

    loads = [0] * group_count
    assignments = {}
    for module in sorted(modules, key=lambda name: (-weight(name), name)):
        index = min(range(group_count), key=lambda index: (loads[index], index))
        assignments[module] = index
        loads[index] += weight(module)

    groups = [[] for _ in range(group_count)]
    # Preserve discovery order and each module's class/module fixture lifecycle.
    for test in tests:
        groups[assignments[type(test).__module__]].append(test)
    return groups


def run_group(test_root: pathlib.Path, group_index: int, group_count: int) -> int:
    if group_count < 1 or not 0 <= group_index < group_count:
        raise ValueError("group index/count are out of range")
    loader = unittest.TestLoader()
    tests = flatten_tests(loader.discover(str(test_root), pattern="test_*.py"))
    if loader.errors:
        # A discovery error must fail every group, even outside its assignment.
        for error in loader.errors:
            print(error, file=sys.stderr)
        return 1
    selected = group_tests(tests, group_count)[group_index]
    if not selected:
        print(f"group {group_index}/{group_count} selected no tests", file=sys.stderr)
        return 1
    modules = sorted({type(test).__module__ for test in selected})
    print(
        f"Running group {group_index + 1}/{group_count}: "
        f"{len(selected)} of {len(tests)} discovered tests; modules: "
        + ", ".join(modules),
        flush=True,
    )
    result = unittest.TextTestRunner(verbosity=1).run(unittest.TestSuite(selected))
    return 0 if result.wasSuccessful() else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", choices=SUITE_ROOTS)
    parser.add_argument("--group-index", type=int, required=True)
    parser.add_argument("--group-count", type=int, required=True)
    args = parser.parse_args(argv)
    if args.group_count < 1 or not 0 <= args.group_index < args.group_count:
        parser.error("group index/count are out of range")
    # Match `python -m unittest` imports such as `tests.test_other_module`.
    sys.path.insert(0, str(REPO_ROOT))
    return run_group(SUITE_ROOTS[args.suite], args.group_index, args.group_count)


if __name__ == "__main__":
    raise SystemExit(main())
