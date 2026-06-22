from __future__ import annotations

import datetime as dt
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import re
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

    def test_agile_delivery_sync_rule_builds_private_variant(self) -> None:
        source = (
            self.source_root
            / "codex-review-workflows"
            / "skills"
            / "agile-delivery-workflow"
            / "SKILL.md"
        )
        source.parent.mkdir(parents=True)
        source.write_text(
            "Use this when the user asks.\nState the core user-visible behavior.\n",
            encoding="utf-8",
        )
        rule = next(
            rule
            for rule in SYNC_MODULE.SYNC_RULES
            if rule.target == Path("personal_codex/skills/agile-delivery-workflow")
        )

        SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

        target = (
            self.repo_root
            / "personal_codex"
            / "skills"
            / "agile-delivery-workflow"
            / "SKILL.md"
        )
        self.assertEqual(
            target.read_text(encoding="utf-8"),
            "Use this when Joey asks.\nState the core Joey-visible behavior.\n",
        )

    def test_session_mining_sync_rule_builds_remote_host_private_variant(self) -> None:
        source = self.source_root / "codex-workflow-hygiene" / "skills" / "codex-session-mining"
        references = source / "references"
        references.mkdir(parents=True)
        (source / "SKILL.md").write_text(
            "description: pair with an environment-specific remote evidence workflow when remote-host evidence may matter.\n"
            "- If the task might depend on remote-host evidence, let an environment-specific remote evidence workflow materialize remote rollout candidates locally before concluding that local history is complete.\n"
            "- Do not recreate a second remote-access workflow here; this skill owns local extraction and interpretation after remote evidence is materialized.\n",
            encoding="utf-8",
        )
        (references / "workflow.md").write_text(
            "If the user is asking for a work summary, activity audit, or session recovery that may include remote hosts, use an environment-specific remote evidence workflow before concluding that the local `~/.codex` tree is complete.\n",
            encoding="utf-8",
        )
        rule = next(
            rule
            for rule in SYNC_MODULE.SYNC_RULES
            if rule.target == Path("personal_codex/skills/codex-session-mining")
        )

        SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

        synced_skill = (
            self.repo_root / "personal_codex" / "skills" / "codex-session-mining" / "SKILL.md"
        ).read_text(encoding="utf-8")
        synced_reference = (
            self.repo_root
            / "personal_codex"
            / "skills"
            / "codex-session-mining"
            / "references"
            / "workflow.md"
        ).read_text(encoding="utf-8")
        self.assertIn("pair with `$remote-host-context`", synced_skill)
        self.assertIn("miku-bot-dev", synced_skill)
        self.assertIn("hoteng-srv-01", synced_skill)
        self.assertNotIn("codex-hoteng-srv-01", synced_skill)
        self.assertIn("Remote access belongs to `remote-host-context`", synced_skill)
        self.assertNotIn("environment-specific remote evidence workflow", synced_skill)
        self.assertIn("$remote-host-context", synced_reference)

    def test_session_mining_sync_rule_rejects_remote_host_residuals(self) -> None:
        source = self.source_root / "codex-workflow-hygiene" / "skills" / "codex-session-mining"
        references = source / "references"
        references.mkdir(parents=True)
        (source / "SKILL.md").write_text(
            "description: pair with an environment-specific remote evidence workflow when remote-host evidence may matter.\n"
            "- If the task might depend on remote-host evidence, let an environment-specific remote evidence workflow materialize remote rollout candidates locally before concluding that local history is complete.\n"
            "- Do not recreate a second remote-access workflow here; this skill owns local extraction and interpretation after remote evidence is materialized.\n"
            "- A new environment-specific workflow note must not slip through.\n",
            encoding="utf-8",
        )
        (references / "workflow.md").write_text(
            "If the user is asking for a work summary, activity audit, or session recovery that may include remote hosts, use an environment-specific remote evidence workflow before concluding that the local `~/.codex` tree is complete.\n",
            encoding="utf-8",
        )
        rule = next(
            rule
            for rule in SYNC_MODULE.SYNC_RULES
            if rule.target == Path("personal_codex/skills/codex-session-mining")
        )

        with self.assertRaisesRegex(SYNC_MODULE.SyncError, "forbidden residual"):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_project_journal_sync_rule_matches_current_public_wording(self) -> None:
        source = self.source_root / "codex-project-journal" / "SKILL.md"
        source.parent.mkdir(parents=True)
        source.write_text(
            "\n".join(
                [
                    "Manage repository project journals.",
                    "For repositories, assume docs exist.",
                    "Find repositories recently touched by Codex sessions.",
                    "Use this when converting existing repositories.",
                    "Do not batch-install hooks across repositories.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        script = source.parent / "scripts" / "project_journal.py"
        script.parent.mkdir()
        script.write_text(
            '"""Manage cross-repo project journal indexes for Codex workflows."""\n',
            encoding="utf-8",
        )
        rule = next(
            rule
            for rule in SYNC_MODULE.SYNC_RULES
            if rule.target == Path("personal_codex/skills/project-journal")
        )

        SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

        target = (
            self.repo_root
            / "personal_codex"
            / "skills"
            / "project-journal"
            / "SKILL.md"
        )
        text = target.read_text(encoding="utf-8")
        self.assertIn("Manage Joey repo project journals.", text)
        self.assertIn("For Joey repos, assume docs exist.", text)
        self.assertIn("Find Joey repos recently touched by Codex sessions.", text)
        self.assertIn("Use this when converting existing Joey repos.", text)
        self.assertIn("Do not batch-install hooks across Joey repos.", text)
        self.assertNotIn("For repositories", text)
        synced_script = (
            self.repo_root
            / "personal_codex"
            / "skills"
            / "project-journal"
            / "scripts"
            / "project_journal.py"
        )
        self.assertIn(
            "Manage cross-repo project journal indexes for Joey's Codex workflows.",
            synced_script.read_text(encoding="utf-8"),
        )

    def test_skill_authoring_sync_rule_copies_validator_wrapper(self) -> None:
        source = self.source_root / "codex-workflow-hygiene" / "skills" / "codex-skill-authoring"
        scripts = source / "scripts"
        scripts.mkdir(parents=True)
        (source / "SKILL.md").write_text(
            "# Codex Skill Authoring\n"
            "Create concise concise Codex skills.\n"
            'Use "$HOME/.codex/skills/codex-skill-authoring/scripts/codex_skill_validate.py".\n'
            "Use this when the user asks.\n"
            "Avoid user-specific validator mirrors.\n",
            encoding="utf-8",
        )
        (scripts / "codex_skill_validate.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        rule = next(
            rule
            for rule in SYNC_MODULE.SYNC_RULES
            if rule.target == Path("personal_codex/skills/joey-skill-authoring")
        )

        SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

        target = self.repo_root / "personal_codex" / "skills" / "joey-skill-authoring"
        synced_skill = (target / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn(
            '"$HOME/.codex/skills/joey-skill-authoring/scripts/codex_skill_validate.py"',
            synced_skill,
        )
        self.assertIn("Use this when Joey asks.", synced_skill)
        self.assertIn("Joey-specific validator mirrors.", synced_skill)
        self.assertTrue((target / "scripts" / "codex_skill_validate.py").exists())

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

    def test_required_replacement_rejects_unmatched_new_text(self) -> None:
        source = self.source_root / "example-repo" / "skill" / "SKILL.md"
        source.parent.mkdir(parents=True)
        source.write_text("private replacement\n", encoding="utf-8")
        rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/example"),
            replacements=(SYNC_MODULE.Replacement("public placeholder", "private replacement"),),
        )

        with self.assertRaisesRegex(SYNC_MODULE.SyncError, "required replacement"):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_failed_replacement_leaves_existing_target_unchanged(self) -> None:
        source = self.source_root / "example-repo" / "skill" / "SKILL.md"
        source.parent.mkdir(parents=True)
        source.write_text("public content\n", encoding="utf-8")
        target = self.repo_root / "personal_codex" / "skills" / "example" / "SKILL.md"
        target.parent.mkdir(parents=True)
        target.write_text("private content\n", encoding="utf-8")
        rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/example"),
            replacements=(SYNC_MODULE.Replacement("missing", "replacement"),),
        )

        with self.assertRaisesRegex(SYNC_MODULE.SyncError, "required replacement"):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

        self.assertEqual(target.read_text(encoding="utf-8"), "private content\n")

    def test_sync_rejects_target_ancestor_symlink(self) -> None:
        source = self.source_root / "example-repo" / "skill" / "SKILL.md"
        source.parent.mkdir(parents=True)
        source.write_text("content\n", encoding="utf-8")
        outside = self.root / "outside"
        outside.mkdir()
        (self.repo_root / "personal_codex").symlink_to(outside, target_is_directory=True)
        rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/example"),
        )

        with self.assertRaisesRegex(SYNC_MODULE.SyncError, "ancestor symlink"):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

        self.assertFalse((outside / "skills").exists())

    def test_sync_rejects_source_ancestor_symlink(self) -> None:
        outside = self.root / "outside-source"
        outside_skill = outside / "example"
        outside_skill.mkdir(parents=True)
        (outside_skill / "SKILL.md").write_text("leaked content\n", encoding="utf-8")
        repo = self.source_root / "example-repo"
        repo.mkdir()
        (repo / "skills").symlink_to(outside, target_is_directory=True)
        rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skills/example"),
            target=Path("personal_codex/skills/example"),
        )

        with self.assertRaisesRegex(SYNC_MODULE.SyncError, "source ancestor symlink"):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

        self.assertFalse((self.repo_root / "personal_codex" / "skills" / "example").exists())

    def test_ignored_source_symlink_is_not_rejected(self) -> None:
        source = self.source_root / "example-repo" / "skill"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("content\n", encoding="utf-8")
        (source / ".github").mkdir()
        (source / ".github" / "leak").symlink_to(Path.home())
        rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/example"),
        )

        SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

        self.assertTrue(
            (self.repo_root / "personal_codex" / "skills" / "example" / "SKILL.md").is_file()
        )

    def test_forbidden_residuals_fail_sync(self) -> None:
        source = self.source_root / "example-repo" / "skill" / "SKILL.md"
        source.parent.mkdir(parents=True)
        source.write_text("public-token\n", encoding="utf-8")
        rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/example"),
            forbidden_residuals=("public-token",),
        )

        with self.assertRaisesRegex(SYNC_MODULE.SyncError, "forbidden residual"):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

        self.assertFalse((self.repo_root / "personal_codex" / "skills" / "example").exists())

    def test_scheduled_workflow_checks_out_all_sync_rule_repos(self) -> None:
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "scheduled-sync-release.yml"
        ).read_text(encoding="utf-8")
        checked_out_repos = set(re.findall(r"repository: Joey-Tools/([-a-z0-9]+)", workflow))
        checked_out_paths = set(re.findall(r"path: \.source/([-a-z0-9]+)", workflow))
        sync_rule_repos = {rule.repo for rule in SYNC_MODULE.SYNC_RULES}

        self.assertEqual(checked_out_repos, sync_rule_repos)
        self.assertEqual(checked_out_paths, sync_rule_repos)

    def test_manifest_canonical_skills_are_backed_by_sync_rules(self) -> None:
        manifest = json.loads(
            (REPO_ROOT / "personal_codex" / "private-sync-manifest.json").read_text(encoding="utf-8")
        )
        private_only_sources = {
            "personal_codex/skills/cisco-trackers-lookup",
            "personal_codex/skills/remote-host-context",
        }
        manifest_sources = {
            link["source"]
            for link in manifest["links"]
            if link["source"].startswith("personal_codex/skills/")
        }
        manifest_targets = {
            link["target"]
            for link in manifest["links"]
            if link["source"].startswith("personal_codex/skills/")
        }
        sync_targets = {str(rule.target) for rule in SYNC_MODULE.SYNC_RULES}

        self.assertEqual(manifest_sources - private_only_sources, manifest_sources & sync_targets)
        self.assertIn("personal_codex/skills/codex-session-retrospective", manifest_sources)
        self.assertIn("skills/codex-session-retrospective", manifest_targets)
        self.assertIn("personal_codex/skills/codex-session-retrospective", sync_targets)

    def test_scheduled_workflow_opens_pr_for_sync_changes(self) -> None:
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "scheduled-sync-release.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("pull-requests: write", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("PRIVATE_OVERLAY_SYNC_PR_TOKEN", workflow)
        self.assertIn('git remote set-url origin "https://x-access-token:${SYNC_PR_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"', workflow)
        self.assertIn("gh pr create", workflow)
        self.assertIn("gh pr edit", workflow)
        self.assertIn('label="codex-automation"', workflow)
        self.assertIn('gh api --method GET "repos/$GITHUB_REPOSITORY/labels/$label"', workflow)
        self.assertNotIn("gh label list --repo", workflow)
        self.assertIn('--label "$label"', workflow)
        self.assertIn('--add-label "$label"', workflow)
        self.assertIn('head="$owner:$branch"', workflow)
        self.assertIn('gh api --method GET "repos/$GITHUB_REPOSITORY/pulls"', workflow)
        self.assertNotIn('git push origin "HEAD:${GITHUB_REF_NAME}"', workflow)

    def test_scheduled_workflow_enables_auto_merge_for_generated_pr(self) -> None:
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "scheduled-sync-release.yml"
        ).read_text(encoding="utf-8")

        self.assertIn('head_sha="$(git rev-parse HEAD)"', workflow)
        self.assertIn('head_sha="$remote_sha"', workflow)
        self.assertIn('pr_head_sha="$(gh pr view "$pr_url" --json headRefOid --jq \'.headRefOid\')"', workflow)
        self.assertIn('pr_head_ref="$(gh pr view "$pr_url" --json headRefName --jq \'.headRefName\')"', workflow)
        self.assertIn('pr_base_ref="$(gh pr view "$pr_url" --json baseRefName --jq \'.baseRefName\')"', workflow)
        self.assertIn('gh pr merge "$pr_url" --auto --squash --delete-branch --match-head-commit "$head_sha"', workflow)
        self.assertIn('git diff --cached --quiet "$head_sha"', workflow)
        self.assertIn('git diff --quiet "$head_sha"', workflow)

    def test_scheduled_workflow_uses_exact_sync_branch_ref(self) -> None:
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "scheduled-sync-release.yml"
        ).read_text(encoding="utf-8")

        self.assertIn('remote_ref="refs/heads/$branch"', workflow)
        self.assertIn('awk -v ref="$remote_ref"', workflow)
        self.assertIn('git push --force-with-lease="$remote_ref:$remote_sha"', workflow)
        self.assertNotIn('git ls-remote --heads origin "$branch"', workflow)

    def test_scheduled_workflow_skips_unchanged_sync_branch(self) -> None:
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "scheduled-sync-release.yml"
        ).read_text(encoding="utf-8")

        self.assertIn('git merge-base --is-ancestor "$GITHUB_SHA" FETCH_HEAD', workflow)
        self.assertIn("git diff --cached --quiet FETCH_HEAD", workflow)
        self.assertNotIn("git diff --cached --quiet FETCH_HEAD -- scripts personal_codex .agents", workflow)
        self.assertIn("already matches the full generated overlay tree and contains", workflow)

    def test_readme_documents_sync_pr_token_permissions(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("PRIVATE_OVERLAY_SYNC_PR_TOKEN", readme)
        self.assertIn("contents, pull-request, and issues write access", readme)
        self.assertIn("codex-automation", readme)

    def test_scheduled_workflow_only_publishes_incomplete_current_release(self) -> None:
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "scheduled-sync-release.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("if: steps.current-release.outputs.complete == 'false'", workflow)
        self.assertNotIn("steps.commit.outputs.sha", workflow)

    def test_release_workflow_runs_required_pr_check_for_all_pull_requests(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("  pull_request:\n  push:", workflow)
        self.assertIn("    branches:\n      - master", workflow)
        self.assertIn('      - ".github/workflows/**"', workflow)


class PrivateOverlayReleaseTests(unittest.TestCase):
    def test_force_bypasses_cooldown_lookup(self) -> None:
        with mock.patch.object(RELEASE_MODULE, "recent_complete_releases") as lookup:
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

    def test_manual_default_skips_when_recent_complete_release_exists(self) -> None:
        with mock.patch.object(
            RELEASE_MODULE,
            "recent_complete_releases",
            return_value=[
                {
                    "tag_name": "personal-codex-20260522-100000-aaaaaaaa",
                    "published_at": "2026-05-22T10:00:00Z",
                    "body": "source_event=workflow_dispatch",
                }
            ],
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

    def test_noop_workflow_runs_do_not_anchor_cooldown(self) -> None:
        with mock.patch.object(RELEASE_MODULE, "recent_complete_releases", return_value=[]):
            run, reason = RELEASE_MODULE.should_run(
                repo="owner/repo",
                workflow="scheduled-sync-release.yml",
                current_run_id="1",
                event="schedule",
                force=False,
                cooldown_seconds=8 * 60 * 60,
            )

        self.assertTrue(run)
        self.assertIn("no recent complete release", reason)

    def test_recent_complete_releases_require_published_complete_assets(self) -> None:
        now = dt.datetime(2026, 5, 22, 12, 0, tzinfo=dt.timezone.utc)
        complete_sha = "a" * 40
        old_sha = "b" * 40
        draft_sha = "c" * 40
        missing_sha = "d" * 40
        scheduled_sha = "e" * 40
        releases = [
            {
                "tag_name": f"personal-codex-20260522-110000-{complete_sha[:7]}",
                "target_commitish": complete_sha,
                "published_at": "2026-05-22T11:00:00Z",
                "body": "source_event=workflow_dispatch",
                "draft": False,
                "assets": [
                    {"name": f"personal-codex-{complete_sha}.tar.gz", "state": "uploaded"},
                    {"name": f"personal-codex-{complete_sha}.sha256", "state": "uploaded"},
                ],
            },
            {
                "tag_name": f"personal-codex-20260522-010000-{old_sha[:7]}",
                "target_commitish": old_sha,
                "published_at": "2026-05-22T01:00:00Z",
                "body": "source_event=workflow_dispatch",
                "draft": False,
                "assets": [
                    {"name": f"personal-codex-{old_sha}.tar.gz", "state": "uploaded"},
                    {"name": f"personal-codex-{old_sha}.sha256", "state": "uploaded"},
                ],
            },
            {
                "tag_name": f"personal-codex-20260522-110000-{draft_sha[:7]}",
                "target_commitish": draft_sha,
                "published_at": "2026-05-22T11:00:00Z",
                "body": "source_event=workflow_dispatch",
                "draft": True,
                "assets": [
                    {"name": f"personal-codex-{draft_sha}.tar.gz", "state": "uploaded"},
                    {"name": f"personal-codex-{draft_sha}.sha256", "state": "uploaded"},
                ],
            },
            {
                "tag_name": f"personal-codex-20260522-110000-{missing_sha[:7]}",
                "target_commitish": missing_sha,
                "published_at": "2026-05-22T11:00:00Z",
                "body": "source_event=workflow_dispatch",
                "draft": False,
                "assets": [{"name": f"personal-codex-{missing_sha}.tar.gz", "state": "uploaded"}],
            },
            {
                "tag_name": f"personal-codex-20260522-110000-{scheduled_sha[:7]}",
                "target_commitish": scheduled_sha,
                "published_at": "2026-05-22T11:00:00Z",
                "body": "source_event=schedule",
                "draft": False,
                "assets": [
                    {"name": f"personal-codex-{scheduled_sha}.tar.gz", "state": "uploaded"},
                    {"name": f"personal-codex-{scheduled_sha}.sha256", "state": "uploaded"},
                ],
            },
        ]
        with mock.patch.object(RELEASE_MODULE, "iter_releases", return_value=iter(releases)):
            recent = RELEASE_MODULE.recent_complete_releases(
                repo="owner/repo",
                now=now,
                cooldown_seconds=8 * 60 * 60,
                event="schedule",
            )

        self.assertEqual([release["target_commitish"] for release in recent], [complete_sha])

        with mock.patch.object(RELEASE_MODULE, "iter_releases", return_value=iter(releases)):
            recent = RELEASE_MODULE.recent_complete_releases(
                repo="owner/repo",
                now=now,
                cooldown_seconds=8 * 60 * 60,
                event="workflow_dispatch",
            )

        self.assertEqual(
            [release["target_commitish"] for release in recent],
            [complete_sha, scheduled_sha],
        )

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
                    {"name": f"personal-codex-{sha}.tar.gz", "state": "uploaded"},
                    {"name": f"personal-codex-{sha}.sha256", "state": "uploaded"},
                ],
            }
            with mock.patch.object(RELEASE_MODULE, "iter_releases", return_value=iter([release])):
                with contextlib.redirect_stdout(io.StringIO()):
                    RELEASE_MODULE.publish_release("owner/repo", sha, dist)

    def test_publish_existing_draft_updates_source_event(self) -> None:
        with tempfile.TemporaryDirectory(prefix="private-overlay-release.") as temp_dir_raw:
            dist = Path(temp_dir_raw)
            sha = "a" * 40
            (dist / f"personal-codex-{sha}.tar.gz").write_bytes(b"archive")
            (dist / f"personal-codex-{sha}.sha256").write_text("checksum\n", encoding="utf-8")
            release = {
                "id": 10,
                "tag_name": f"personal-codex-20260522-100000-{sha[:7]}",
                "target_commitish": sha,
                "body": "source_event=schedule",
                "draft": True,
                "assets": [
                    {"name": f"personal-codex-{sha}.tar.gz", "state": "uploaded"},
                    {"name": f"personal-codex-{sha}.sha256", "state": "uploaded"},
                ],
            }
            requests: list[dict[str, object]] = []

            def fake_request_json(url: str, *, method: str = "GET", payload=None, token=None):
                requests.append({"url": url, "method": method, "payload": payload})
                return dict(release, body=payload["body"], draft=payload["draft"])

            with mock.patch.object(RELEASE_MODULE, "iter_releases", return_value=iter([release])):
                with mock.patch.object(RELEASE_MODULE, "request_json", fake_request_json):
                    with contextlib.redirect_stdout(io.StringIO()):
                        RELEASE_MODULE.publish_release(
                            "owner/repo",
                            sha,
                            dist,
                            source_event="workflow_dispatch",
                        )

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["method"], "PATCH")
        self.assertEqual(
            requests[0]["payload"],
            {
                "body": f"Private Codex overlay release for {sha}.\n\nsource_event=workflow_dispatch",
                "draft": False,
            },
        )

    def test_publish_deletes_incomplete_assets_before_reupload(self) -> None:
        with tempfile.TemporaryDirectory(prefix="private-overlay-release.") as temp_dir_raw:
            dist = Path(temp_dir_raw)
            sha = "a" * 40
            (dist / f"personal-codex-{sha}.tar.gz").write_bytes(b"archive")
            (dist / f"personal-codex-{sha}.sha256").write_text("checksum\n", encoding="utf-8")
            release = {
                "id": 10,
                "tag_name": f"personal-codex-20260522-100000-{sha[:7]}",
                "target_commitish": sha,
                "body": "source_event=workflow_dispatch",
                "draft": True,
                "assets": [
                    {"id": 11, "name": f"personal-codex-{sha}.tar.gz", "state": "uploaded"},
                    {"id": 12, "name": f"personal-codex-{sha}.sha256", "state": "starter"},
                ],
            }
            requests: list[dict[str, object]] = []
            uploads: list[str] = []

            def fake_request_json(url: str, *, method: str = "GET", payload=None, token=None):
                requests.append({"url": url, "method": method, "payload": payload})
                return {}

            class FakeResponse:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def read(self) -> bytes:
                    return b'{"name":"personal-codex-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.sha256"}'

            def fake_urlopen(request, timeout=30):
                uploads.append(request.full_url)
                return FakeResponse()

            with mock.patch.object(RELEASE_MODULE, "iter_releases", return_value=iter([release])):
                with mock.patch.object(RELEASE_MODULE, "request_json", fake_request_json):
                    with mock.patch.object(RELEASE_MODULE, "urlopen", fake_urlopen):
                        with mock.patch.object(RELEASE_MODULE, "_github_token", return_value="token"):
                            with contextlib.redirect_stdout(io.StringIO()):
                                RELEASE_MODULE.publish_release(
                                    "owner/repo",
                                    sha,
                                    dist,
                                    source_event="workflow_dispatch",
                                )

        self.assertTrue(
            any(
                request["method"] == "DELETE"
                and str(request["url"]).endswith("/releases/assets/12")
                for request in requests
            )
        )
        self.assertEqual(len(uploads), 1)
        self.assertIn(f"personal-codex-{sha}.sha256", uploads[0])

    def test_release_complete_requires_published_assets(self) -> None:
        sha = "a" * 40
        complete_release = {
            "tag_name": f"personal-codex-20260522-100000-{sha[:7]}",
            "target_commitish": sha,
            "draft": False,
            "assets": [
                {"name": f"personal-codex-{sha}.tar.gz", "state": "uploaded"},
                {"name": f"personal-codex-{sha}.sha256", "state": "uploaded"},
            ],
        }
        draft_release = dict(complete_release, draft=True)
        missing_asset_release = dict(
            complete_release,
            assets=[{"name": f"personal-codex-{sha}.tar.gz", "state": "uploaded"}],
        )
        incomplete_asset_release = dict(
            complete_release,
            assets=[
                {"name": f"personal-codex-{sha}.tar.gz", "state": "uploaded"},
                {"name": f"personal-codex-{sha}.sha256", "state": "starter"},
            ],
        )

        with mock.patch.object(RELEASE_MODULE, "iter_releases", return_value=iter([complete_release])):
            self.assertTrue(RELEASE_MODULE.release_complete("owner/repo", sha))
        with mock.patch.object(RELEASE_MODULE, "iter_releases", return_value=iter([draft_release])):
            self.assertFalse(RELEASE_MODULE.release_complete("owner/repo", sha))
        with mock.patch.object(RELEASE_MODULE, "iter_releases", return_value=iter([missing_asset_release])):
            self.assertFalse(RELEASE_MODULE.release_complete("owner/repo", sha))
        with mock.patch.object(RELEASE_MODULE, "iter_releases", return_value=iter([incomplete_asset_release])):
            self.assertFalse(RELEASE_MODULE.release_complete("owner/repo", sha))


if __name__ == "__main__":
    unittest.main()
