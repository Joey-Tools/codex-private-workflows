from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
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
    def test_wrapper_path_is_anchored_to_source_tree(self) -> None:
        self.assertEqual(
            MODULE._profile_wrapper_path(SCRIPT),
            REPO_ROOT / "personal_codex" / "bin" / "gh-hoteng",
        )

    def test_wrapper_path_resolves_installed_skill_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            overlay_root = (
                root
                / "personal-sync/overlays/private/releases/release-id/personal_codex"
            )
            source_skill = overlay_root / "skills" / "cisco-trackers-lookup"
            source_script = source_skill / "scripts" / "cisco_ghe_probe.py"
            source_script.parent.mkdir(parents=True)
            source_script.touch()
            wrapper = overlay_root / "bin" / "gh-hoteng"
            wrapper.parent.mkdir()
            wrapper.touch()

            installed_skills = root / "home/.codex/skills"
            installed_skills.mkdir(parents=True)
            (installed_skills / "cisco-trackers-lookup").symlink_to(
                source_skill,
                target_is_directory=True,
            )

            self.assertEqual(
                MODULE._profile_wrapper_path(
                    installed_skills
                    / "cisco-trackers-lookup/scripts/cisco_ghe_probe.py"
                ),
                wrapper.resolve(),
            )

    def test_transport_uses_overlay_wrapper_and_cisco_host(self) -> None:
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
                "GH_PATH": "wrong/path",
                "GH_REPO": "wrong/repository",
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
        self.assertEqual(
            argv,
            [
                str(REPO_ROOT / "personal_codex" / "bin" / "gh-hoteng"),
                "api",
                "user",
            ],
        )
        self.assertNotEqual(Path(argv[0]).name, "gh")
        self.assertEqual(env["GH_HOST"], "sqbu-github.cisco.com")
        for key in MODULE.GH_INHERITED_ENVIRONMENT_KEYS:
            with self.subTest(key=key):
                self.assertNotIn(key, env)


if __name__ == "__main__":
    unittest.main()
