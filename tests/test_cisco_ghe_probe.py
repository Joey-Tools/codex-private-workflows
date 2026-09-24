from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    REPO_ROOT / "personal_codex/skills/cisco-trackers-lookup/scripts/cisco_ghe_probe.py"
)
SPEC = importlib.util.spec_from_file_location("cisco_ghe_probe", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class CiscoGheProbeProfileTests(unittest.TestCase):
    def test_transport_uses_hoteng_wrapper_and_cisco_host(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"login":"hoteng"}',
            stderr="",
        )
        with mock.patch.dict(
            os.environ,
            {
                "GH_HOST": "wrong.example",
                "GH_CONFIG_DIR": "/tmp/wrong-profile",
                "GH_TOKEN": "wrong-token",
            },
            clear=False,
        ):
            with mock.patch.object(
                MODULE.subprocess,
                "run",
                return_value=completed,
            ) as run:
                rc, payload = MODULE._run_gh_json(["api", "user"])

        self.assertEqual(rc, 0)
        self.assertEqual(payload, {"login": "hoteng"})
        argv = run.call_args.args[0]
        env = run.call_args.kwargs["env"]
        self.assertEqual(argv, ["gh-hoteng", "api", "user"])
        self.assertNotEqual(argv[0], "gh")
        self.assertEqual(env["GH_HOST"], "sqbu-github.cisco.com")


if __name__ == "__main__":
    unittest.main()
