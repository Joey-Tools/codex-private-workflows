from __future__ import annotations

import datetime as dt
import importlib.util
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync_private_overlay_sources.py"
RELEASE_SCRIPT = REPO_ROOT / "scripts" / "private_overlay_release.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SYNC_MODULE = load_module("sync_private_overlay_sources", SYNC_SCRIPT)
RELEASE_MODULE = load_module("private_overlay_release", RELEASE_SCRIPT)


class PrivateOverlaySyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="private-overlay-sync.")
        self.root = Path(self.tmpdir.name)
        self.repo_root = self.root / "target"
        self.source_root = self.root / "source"
        self.repo_root.mkdir()
        self.source_root.mkdir()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_sync_rule_copies_and_transforms_text(self) -> None:
        source = self.source_root / "example-repo" / "skill" / "SKILL.md"
        source.parent.mkdir(parents=True)
        source.write_text("Use this when the user asks.\n", encoding="utf-8")
        rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/example"),
            replacements=SYNC_MODULE.COMMON_JOEY_TEXT_REPLACEMENTS,
        )

        SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

        target = self.repo_root / "personal_codex" / "skills" / "example" / "SKILL.md"
        self.assertEqual(target.read_text(encoding="utf-8"), "Use this when Joey asks.\n")

    def test_sync_rule_rejects_symlink_sources(self) -> None:
        source = self.source_root / "example-repo" / "skill"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("---\nname: example\n---\n", encoding="utf-8")
        (source / "leak").symlink_to(Path.home())
        rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/example"),
        )

        with self.assertRaisesRegex(SYNC_MODULE.SyncError, "symlink"):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_required_replacement_must_match(self) -> None:
        source = self.source_root / "example-repo" / "skill" / "SKILL.md"
        source.parent.mkdir(parents=True)
        source.write_text("unchanged\n", encoding="utf-8")
        rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/example"),
            replacements=(SYNC_MODULE.Replacement("missing", "replacement"),),
        )

        with self.assertRaisesRegex(SYNC_MODULE.SyncError, "required replacement"):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))


class PrivateOverlayReleaseTests(unittest.TestCase):
    def test_force_bypasses_cooldown_lookup(self) -> None:
        with mock.patch.object(RELEASE_MODULE, "recent_successful_runs") as lookup:
            run, reason = RELEASE_MODULE.should_run(
                repo="owner/repo",
                workflow="scheduled-sync-release.yml",
                current_run_id="1",
                event="workflow_dispatch",
                force=True,
                cooldown_seconds=8 * 60 * 60,
            )

        self.assertTrue(run)
        self.assertEqual(reason, "force=true")
        lookup.assert_not_called()

    def test_manual_default_skips_when_recent_success_exists(self) -> None:
        with mock.patch.object(
            RELEASE_MODULE,
            "recent_successful_runs",
            return_value=[{"id": 2, "event": "workflow_dispatch", "created_at": "2026-05-22T10:00:00Z"}],
        ):
            run, reason = RELEASE_MODULE.should_run(
                repo="owner/repo",
                workflow="scheduled-sync-release.yml",
                current_run_id="1",
                event="workflow_dispatch",
                force=False,
                cooldown_seconds=8 * 60 * 60,
            )

        self.assertFalse(run)
        self.assertIn("cooldown active", reason)

    def test_schedule_ignores_recent_schedule_but_counts_manual(self) -> None:
        now = dt.datetime(2026, 5, 22, 12, 0, tzinfo=dt.timezone.utc)
        payload = {
            "workflow_runs": [
                {
                    "id": 2,
                    "event": "schedule",
                    "created_at": "2026-05-22T11:00:00Z",
                },
                {
                    "id": 3,
                    "event": "workflow_dispatch",
                    "created_at": "2026-05-22T10:30:00Z",
                },
            ]
        }
        with mock.patch.object(RELEASE_MODULE, "request_json", return_value=payload):
            recent = RELEASE_MODULE.recent_successful_runs(
                repo="owner/repo",
                workflow="scheduled-sync-release.yml",
                current_run_id="1",
                now=now,
                cooldown_seconds=8 * 60 * 60,
                event="schedule",
            )

        self.assertEqual([run["id"] for run in recent], [3])

    def test_publish_is_idempotent_when_release_assets_exist(self) -> None:
        with tempfile.TemporaryDirectory(prefix="private-overlay-release.") as temp_dir_raw:
            dist = Path(temp_dir_raw)
            sha = "a" * 40
            (dist / f"personal-codex-{sha}.tar.gz").write_bytes(b"archive")
            (dist / f"personal-codex-{sha}.sha256").write_text("checksum\n", encoding="utf-8")
            release = {
                "id": 10,
                "tag_name": f"personal-codex-20260522-100000-{sha[:7]}",
                "target_commitish": sha,
                "draft": False,
                "assets": [
                    {"name": f"personal-codex-{sha}.tar.gz"},
                    {"name": f"personal-codex-{sha}.sha256"},
                ],
            }
            with mock.patch.object(RELEASE_MODULE, "iter_releases", return_value=iter([release])):
                with contextlib.redirect_stdout(io.StringIO()):
                    RELEASE_MODULE.publish_release("owner/repo", sha, dist)


if __name__ == "__main__":
    unittest.main()
