from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
REUSABLE_HEADER = "name: Required CI\n\non:\n  workflow_call:\n\n"


class RequiredCIWorkflowTests(unittest.TestCase):
    def test_reusable_entry_preserves_both_required_scopes(self) -> None:
        ci = (WORKFLOW_DIR / "ci.yml").read_text(encoding="utf-8")
        release = (WORKFLOW_DIR / "release.yml").read_text(encoding="utf-8")
        reusable = (WORKFLOW_DIR / "required-ci.yml").read_text(encoding="utf-8")

        permissions = ci.index("permissions:\n")
        release_start = release.index("  release:\n")
        publish_start = release.index("\n  publish:\n")
        release_job = release[release_start:publish_start].rstrip("\n") + "\n"
        expected = REUSABLE_HEADER + ci[permissions:].rstrip("\n") + "\n\n" + release_job

        self.assertEqual(reusable, expected)

    def test_reusable_entry_is_read_only_and_excludes_publication(self) -> None:
        reusable = (WORKFLOW_DIR / "required-ci.yml").read_text(encoding="utf-8")
        header, _separator, _body = reusable.partition("permissions:\n")

        self.assertEqual(header, REUSABLE_HEADER)
        self.assertIn("permissions:\n  contents: read\n", reusable)
        self.assertIn("name: test\n", reusable)
        self.assertIn("name: Build private overlay release\n", reusable)
        self.assertNotIn("  publish:\n", reusable)
        self.assertNotIn("contents: write", reusable)
        self.assertNotIn("statuses: write", reusable)
        self.assertNotIn("${{ secrets.", reusable)


if __name__ == "__main__":
    unittest.main()
