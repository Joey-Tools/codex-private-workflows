#!/usr/bin/env python3
"""Run one deterministic round-robin shard of a unittest module."""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import os
import pathlib
import sys
import tempfile
import types
import unittest


def _flatten_tests(suite: unittest.TestSuite) -> list[unittest.TestCase]:
    tests: list[unittest.TestCase] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            tests.extend(_flatten_tests(item))
        else:
            tests.append(item)
    return tests


def select_shard(
    tests: list[unittest.TestCase], shard_index: int, shard_count: int
) -> list[unittest.TestCase]:
    """Select a stable round-robin subset after sorting by unittest ID."""

    ordered = sorted(tests, key=lambda test: test.id())
    return [
        test
        for position, test in enumerate(ordered)
        if position % shard_count == shard_index
    ]


@contextlib.contextmanager
def _importable_module_path(module_path: pathlib.Path):
    """Expose the source through an importable path for multiprocessing spawn."""

    old_pythonpath = os.environ.get("PYTHONPATH")
    with tempfile.TemporaryDirectory(prefix="codex-unittest-shard-") as directory:
        import_root = pathlib.Path(directory)
        importable_path = import_root / f"codex_unittest_shard_{module_path.stem}.py"
        importable_path.symlink_to(module_path)
        sys.path.insert(0, directory)
        os.environ["PYTHONPATH"] = (
            directory
            if not old_pythonpath
            else os.pathsep.join((directory, old_pythonpath))
        )
        try:
            yield importable_path
        finally:
            sys.path.remove(directory)
            if old_pythonpath is None:
                os.environ.pop("PYTHONPATH", None)
            else:
                os.environ["PYTHONPATH"] = old_pythonpath


def _load_test_module(module_path: pathlib.Path) -> types.ModuleType:
    module_name = module_path.stem
    if not module_name.startswith("codex_unittest_shard_"):
        module_name = f"codex_unittest_shard_{module_name}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load unittest module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("module_path", type=pathlib.Path)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_args(sys.argv[1:] if argv is None else argv)
    module_path = arguments.module_path.resolve()
    shard_index = arguments.shard_index
    shard_count = arguments.shard_count
    if not module_path.is_file():
        raise SystemExit(f"unittest module does not exist: {module_path}")
    if shard_count < 1:
        raise SystemExit("--shard-count must be positive")
    if shard_index < 0 or shard_index >= shard_count:
        raise SystemExit("--shard-index must be within --shard-count")

    with _importable_module_path(module_path) as importable_path:
        module = _load_test_module(importable_path)
        all_tests = _flatten_tests(
            unittest.defaultTestLoader.loadTestsFromModule(module)
        )
        selected_tests = select_shard(all_tests, shard_index, shard_count)
        if not selected_tests:
            raise SystemExit(
                f"shard {shard_index} of {shard_count} selected no tests from "
                f"{module_path.name}"
            )

        print(
            f"Running shard {shard_index + 1}/{shard_count}: "
            f"{len(selected_tests)} of {len(all_tests)} tests from {module_path.name}"
        )
        result = unittest.TextTestRunner(verbosity=1).run(
            unittest.TestSuite(selected_tests)
        )
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
