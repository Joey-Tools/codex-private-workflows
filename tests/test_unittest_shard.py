from __future__ import annotations

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
