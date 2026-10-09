from __future__ import annotations

import collections
import importlib.util
import multiprocessing
import pathlib
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNNER_PATH = REPO_ROOT / "scripts" / "run_unittest_shard.py"
SPEC = importlib.util.spec_from_file_location("run_unittest_shard", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)
GROUP_RUNNER_PATH = REPO_ROOT / "scripts" / "run_release_test_group.py"
GROUP_SPEC = importlib.util.spec_from_file_location("run_release_test_group", GROUP_RUNNER_PATH)
assert GROUP_SPEC is not None and GROUP_SPEC.loader is not None
GROUP_RUNNER = importlib.util.module_from_spec(GROUP_SPEC)
GROUP_SPEC.loader.exec_module(GROUP_RUNNER)


class StubTest(unittest.TestCase):
    def __init__(self, test_id: str) -> None:
        super().__init__("runTest")
        self._test_id = test_id

    def id(self) -> str:
        return self._test_id


class UnittestShardRunnerTests(unittest.TestCase):
    def test_round_robin_shards_cover_each_test_once(self) -> None:
        tests = [StubTest(f"case-{number:02d}") for number in range(12)]
        shards = [RUNNER.select_shard(tests, index, 4) for index in range(4)]
        selected_ids = [test.id() for shard in shards for test in shard]

        self.assertEqual(len(selected_ids), len(tests))
        self.assertEqual(len(set(selected_ids)), len(tests))
        self.assertEqual(set(selected_ids), {test.id() for test in tests})
        self.assertTrue(all(len(shard) == 3 for shard in shards))

    def test_selection_is_independent_of_input_order(self) -> None:
        tests = [StubTest(f"case-{number:02d}") for number in range(8)]

        forward = [test.id() for test in RUNNER.select_shard(tests, 1, 2)]
        reverse = [
            test.id()
            for test in RUNNER.select_shard(list(reversed(tests)), 1, 2)
        ]

        self.assertEqual(forward, reverse)

    def test_spawn_can_import_the_sharded_module(self) -> None:
        with tempfile.TemporaryDirectory(prefix="unittest-shard-test-") as directory:
            module_path = pathlib.Path(directory) / "spawn_module.py"
            module_path.write_text(
                "import multiprocessing\n"
                "import unittest\n"
                "\n"
                "def worker(queue):\n"
                "    queue.put('spawn-ok')\n"
                "\n"
                "class SpawnTest(unittest.TestCase):\n"
                "    def test_spawn_worker(self):\n"
                "        context = multiprocessing.get_context('spawn')\n"
                "        queue = context.Queue()\n"
                "        process = context.Process(target=worker, args=(queue,))\n"
                "        process.start()\n"
                "        process.join(10)\n"
                "        self.assertEqual(process.exitcode, 0)\n"
                "        self.assertEqual(queue.get(timeout=2), 'spawn-ok')\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(RUNNER_PATH),
                    str(module_path),
                    "--shard-index",
                    "0",
                    "--shard-count",
                    "1",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Running shard 1/1: 1 of 1 tests", completed.stdout)


class ReleaseTestGroupRunnerTests(unittest.TestCase):
    def test_groups_preserve_every_discovered_test_and_module(self) -> None:
        cases = []
        for name in ("test_named_lane", "test_review_workspace", "test_new_a", "test_new_b", "test_new_c"):
            test_class = type(name, (unittest.TestCase,), {"__module__": name})
            cases.extend(test_class("runTest") for _ in range(3))
        groups = GROUP_RUNNER.group_tests(cases, 4)
        self.assertEqual(
            collections.Counter(id(test) for group in groups for test in group),
            collections.Counter(map(id, cases)),
        )
        module_groups = collections.defaultdict(set)
        for index, group in enumerate(groups):
            for test in group:
                module_groups[type(test).__module__].add(index)
            self.assertEqual(group, [test for test in cases if test in group])
        self.assertTrue(all(len(indices) == 1 for indices in module_groups.values()))
        self.assertEqual({type(test).__module__ for test in groups[0]}, {"test_named_lane"})
        reversed_groups = GROUP_RUNNER.group_tests(list(reversed(cases)), 4)
        self.assertEqual(
            [{id(test) for test in group} for group in groups],
            [{id(test) for test in group} for group in reversed_groups],
        )

    def _fixture_runner(self, root: pathlib.Path, index: int, count: int):
        script = root / "scripts" / GROUP_RUNNER_PATH.name
        script.parent.mkdir(exist_ok=True)
        script.write_bytes(GROUP_RUNNER_PATH.read_bytes())
        return subprocess.run(
            [sys.executable, "-B", str(script), "overlay", "--group-index", str(index), "--group-count", str(count)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_cli_groups_cover_new_modules_and_preserve_spawn_and_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            test_root = root / "tests"
            test_root.mkdir()
            marker = root / "executed.txt"
            for name in ("alpha", "beta", "new_module"):
                (test_root / f"test_{name}.py").write_text(
                    "import multiprocessing, pathlib, unittest\n"
                    f"MARKER = pathlib.Path({str(marker)!r})\n"
                    "def record(value):\n"
                    "    with MARKER.open('a') as stream: stream.write(value + '\\n')\n"
                    "def setUpModule(): record(__name__ + ':setup')\n"
                    "def tearDownModule(): record(__name__ + ':teardown')\n"
                    "class FixtureTests(unittest.TestCase):\n"
                    "    def test_spawn(self):\n"
                    "        process = multiprocessing.get_context('spawn').Process(target=record, args=(__name__ + ':spawn',))\n"
                    "        process.start(); process.join(10)\n"
                    "        self.assertEqual(process.exitcode, 0)\n"
                    "    def test_other(self): record(__name__ + ':other')\n"
                    + ("from tests.test_beta import record as companion_record\n" if name == "alpha" else "")
                )
            for index in range(2):
                completed = self._fixture_runner(root, index, 2)
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            expected = {
                f"test_{name}:{operation}"
                for name in ("alpha", "beta", "new_module")
                for operation in ("setup", "teardown", "spawn", "other")
            }
            actual = marker.read_text().splitlines()
            self.assertEqual(set(actual), expected)
            self.assertEqual(len(actual), len(expected))

    def test_cli_rejects_discovery_error_empty_group_and_failing_test(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            test_root = root / "tests"
            test_root.mkdir()
            module = test_root / "test_case.py"
            module.write_text("raise RuntimeError('broken discovery')\n")
            for index in range(2):
                completed = self._fixture_runner(root, index, 2)
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("broken discovery", completed.stderr)
            module.write_text("import unittest\nclass Failing(unittest.TestCase):\n    def test_failure(self): self.fail('fixture failure')\n")
            failed = self._fixture_runner(root, 0, 1)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("fixture failure", failed.stderr)
            empty = self._fixture_runner(root, 1, 2)
            self.assertNotEqual(empty.returncode, 0)
            self.assertIn("selected no tests", empty.stderr)
            for index, count in ((0, 0), (-1, 2), (2, 2)):
                invalid = self._fixture_runner(root, index, count)
                self.assertEqual(invalid.returncode, 2)
