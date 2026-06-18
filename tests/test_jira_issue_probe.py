from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "personal_codex/skills/cisco-trackers-lookup/scripts/jira_issue_probe.py"
SPEC = importlib.util.spec_from_file_location("jira_issue_probe", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class JiraIssueProbeAuthTests(unittest.TestCase):
    def test_default_profile_uses_bearer_token(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "Jira_email": "person@example.test",
                "Jira_token": "token-value",
            },
        ):
            request, auth_state = MODULE._build_issue_request(
                "SPARK-786996",
                "summary",
                "jira_eng_gpk2_default",
            )

        self.assertEqual(auth_state, "bearer")
        self.assertEqual(request.headers["Authorization"], "Bearer token-value")
        self.assertNotIn("person@example.test", request.headers["Authorization"])
        self.assertEqual(
            request.full_url,
            "https://jira-eng-gpk2.cisco.com/jira/rest/api/2/issue/SPARK-786996?fields=summary",
        )

    def test_default_profile_requires_token_only(self) -> None:
        with mock.patch.dict(os.environ, {"Jira_email": "person@example.test"}, clear=True):
            with self.assertRaisesRegex(
                ValueError,
                "missing auth env for profile jira_eng_gpk2_default: expected Jira_token",
            ):
                MODULE._build_issue_request(
                    "SPARK-786996",
                    "summary",
                    "jira_eng_gpk2_default",
                )


if __name__ == "__main__":
    unittest.main()
