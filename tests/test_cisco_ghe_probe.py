from __future__ import annotations

import importlib.util
import io
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


class CiscoGheProbeAuthTests(unittest.TestCase):
    # synthetic-token-fixtures: access-a
    SELECTED_TOKEN = "codex_synth_v1_access_a"

    def test_transport_uses_global_account_token_and_cisco_host(self) -> None:
        token_lookup = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=f"{self.SELECTED_TOKEN}\n",
            stderr="",
        )
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
                "GH_TOKEN": "wrong-token",
                "GITHUB_TOKEN": "wrong-github-token",
                "GH_ENTERPRISE_TOKEN": "wrong-enterprise-token",
                "GITHUB_ENTERPRISE_TOKEN": "wrong-github-enterprise-token",
                "GH_CONFIG_DIR": "wrong/config",
                "GH_PATH": "wrong/path",
                "GH_REPO": "wrong/repository",
            },
            clear=False,
        ):
            with mock.patch.object(
                MODULE.subprocess,
                "run",
                side_effect=[token_lookup, completed],
            ) as run:
                rc, payload = MODULE._run_gh_json(["api", "user"])

        self.assertEqual(rc, 0)
        self.assertEqual(payload, {"login": "hoteng"})
        self.assertEqual(run.call_count, 2)
        token_argv = run.call_args_list[0].args[0]
        token_env = run.call_args_list[0].kwargs["env"]
        action_argv = run.call_args_list[1].args[0]
        action_env = run.call_args_list[1].kwargs["env"]
        self.assertEqual(
            token_argv,
            [
                "gh",
                "auth",
                "token",
                "--hostname",
                "sqbu-github.cisco.com",
                "--user",
                "hoteng",
            ],
        )
        self.assertEqual(
            action_argv,
            [
                "gh",
                "api",
                "user",
            ],
        )
        self.assertEqual(token_env["GH_PROMPT_DISABLED"], "1")
        self.assertEqual(action_env["GH_PROMPT_DISABLED"], "1")
        self.assertEqual(action_env["GH_HOST"], "sqbu-github.cisco.com")
        self.assertEqual(action_env["GH_ENTERPRISE_TOKEN"], self.SELECTED_TOKEN)
        for key in MODULE.GH_INHERITED_ENVIRONMENT_KEYS:
            with self.subTest(key=key):
                self.assertNotIn(key, token_env)
                if key in {"GH_HOST", "GH_ENTERPRISE_TOKEN"}:
                    self.assertIn(key, action_env)
                else:
                    self.assertNotIn(key, action_env)

    def test_failed_token_lookup_does_not_run_action(self) -> None:
        failed_lookup = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="selected account is unavailable",
        )
        stderr = io.StringIO()
        with mock.patch.object(
            MODULE.subprocess,
            "run",
            return_value=failed_lookup,
        ) as run, mock.patch("sys.stderr", stderr):
            rc, payload = MODULE._run_gh_json(["api", "user"])

        self.assertEqual(rc, 1)
        self.assertIsNone(payload)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(
            run.call_args.args[0],
            [
                "gh",
                "auth",
                "token",
                "--hostname",
                "sqbu-github.cisco.com",
                "--user",
                "hoteng",
            ],
        )
        self.assertIn("error=selected account is unavailable", stderr.getvalue())

    def test_empty_token_does_not_run_action(self) -> None:
        empty_lookup = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="\n",
            stderr="",
        )
        stderr = io.StringIO()
        with mock.patch.object(
            MODULE.subprocess,
            "run",
            return_value=empty_lookup,
        ) as run, mock.patch("sys.stderr", stderr):
            rc, payload = MODULE._run_gh_json(["api", "user"])

        self.assertEqual(rc, 1)
        self.assertIsNone(payload)
        self.assertEqual(run.call_count, 1)
        self.assertIn("error=gh auth token returned no token", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
