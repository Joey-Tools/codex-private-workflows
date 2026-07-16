from __future__ import annotations

import base64
import datetime as dt
import contextlib
import errno
import hmac
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync_private_overlay_sources.py"
RELEASE_SCRIPT = REPO_ROOT / "scripts" / "private_overlay_release.py"
REVIEW_RUNTIME_ROOT = (
    REPO_ROOT
    / "personal_codex"
    / "skills"
    / "review-orchestration-playbook"
    / "scripts"
    / "review_runtime"
)


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


def load_private_review_synthetic_tokens():
    package_name = "private_overlay_review_runtime"
    module_name = f"{package_name}.synthetic_tokens"
    module = sys.modules.get(module_name)
    if module is not None:
        return module
    package = sys.modules.get(package_name)
    if package is None:
        package_spec = importlib.util.spec_from_file_location(
            package_name,
            REVIEW_RUNTIME_ROOT / "__init__.py",
            submodule_search_locations=[str(REVIEW_RUNTIME_ROOT)],
        )
        assert package_spec is not None
        assert package_spec.loader is not None
        package = importlib.util.module_from_spec(package_spec)
        sys.modules[package_name] = package
        package_spec.loader.exec_module(package)
    try:
        return load_module(
            module_name,
            REVIEW_RUNTIME_ROOT / "synthetic_tokens.py",
        )
    except Exception:
        sys.modules.pop(module_name, None)
        raise


class PrivateOverlaySyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="private-overlay-sync.")
        self.root = Path(self.tmpdir.name).resolve()
        self.repo_root = self.root / "target"
        self.source_root = self.root / "source"
        self.repo_root.mkdir()
        self.source_root.mkdir()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _assert_no_regular_file_overlay_temporaries(
        self,
        *directories: Path,
    ) -> None:
        for directory in directories:
            self.assertFalse(
                any(
                    path.name.startswith(SYNC_MODULE.REGULAR_FILE_OVERLAY_TEMP_PREFIX)
                    for path in directory.iterdir()
                )
            )

    def _prepare_held_regular_file_overlay_target(
        self,
        name: str,
    ):
        target = self._create_regular_file_overlay_target(name)
        staging_parent = self.repo_root / f"{name}-staging"
        staging_parent.mkdir(mode=0o700)
        staging = staging_parent / "candidate"
        staging.mkdir()
        (staging / "catalog.json").write_text("public\n", encoding="utf-8")
        stack = contextlib.ExitStack()
        try:
            binding = SYNC_MODULE._write_regular_file_overlay_target(
                staging,
                Path("catalog.json"),
                b"private\n",
                binding_stack=stack,
            )
        except BaseException:
            stack.close()
            raise
        self.assertIsNotNone(binding)
        return stack, target, staging, binding

    def _create_regular_file_overlay_target(self, name: str) -> Path:
        target = self.repo_root / f"{name}-installed"
        target.mkdir()
        (target / "catalog.json").write_text("public\n", encoding="utf-8")
        return target

    def _prepare_scoped_regular_file_overlay_candidate(self, scope):
        staging = scope.path / "candidate"
        staging.mkdir()
        (staging / "catalog.json").write_text("public\n", encoding="utf-8")
        stack = contextlib.ExitStack()
        try:
            binding = SYNC_MODULE._write_regular_file_overlay_target(
                staging,
                Path("catalog.json"),
                b"private\n",
                binding_stack=stack,
            )
        except BaseException:
            stack.close()
            raise
        self.assertIsNotNone(binding)
        return stack, staging, binding

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
        self.assertEqual(
            target.read_text(encoding="utf-8"), "Use this when Joey asks.\n"
        )

    def test_sync_removes_retired_review_skill_targets(self) -> None:
        for relative in SYNC_MODULE.RETIRED_TARGETS:
            target = self.repo_root / relative
            target.mkdir(parents=True)
            (target / "SKILL.md").write_text("retired\n", encoding="utf-8")
        survivor = self.repo_root / "personal_codex" / "skills" / "survivor"
        survivor.mkdir(parents=True)
        (survivor / "SKILL.md").write_text("keep\n", encoding="utf-8")

        SYNC_MODULE.sync_sources(self.repo_root, self.source_root, ())

        for relative in SYNC_MODULE.RETIRED_TARGETS:
            self.assertFalse((self.repo_root / relative).exists())
        self.assertTrue((survivor / "SKILL.md").is_file())

    def test_invalid_canonical_staging_preserves_existing_and_retired_targets(
        self,
    ) -> None:
        for relative in SYNC_MODULE.RETIRED_TARGETS:
            retired = self.repo_root / relative
            retired.mkdir(parents=True)
            (retired / "SKILL.md").write_text("retired\n", encoding="utf-8")

        existing = self.repo_root / SYNC_MODULE.CANONICAL_REVIEW_TARGET
        for relative in SYNC_MODULE.CANONICAL_REVIEW_REQUIRED_FILES:
            path = existing / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("existing\n", encoding="utf-8")

        source = (
            self.source_root
            / "codex-review-workflows"
            / "skills"
            / "review-orchestration-playbook"
        )
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("incomplete\n", encoding="utf-8")
        rule = SYNC_MODULE.SyncRule(
            repo="codex-review-workflows",
            source=Path("skills/review-orchestration-playbook"),
            target=SYNC_MODULE.CANONICAL_REVIEW_TARGET,
        )

        with self.assertRaisesRegex(SYNC_MODULE.SyncError, "missing required file"):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

        self.assertEqual(
            (existing / "SKILL.md").read_text(encoding="utf-8"),
            "existing\n",
        )
        for relative in SYNC_MODULE.RETIRED_TARGETS:
            self.assertTrue((self.repo_root / relative / "SKILL.md").is_file())

    def test_sync_requires_self_contained_canonical_review_target(self) -> None:
        target = self.repo_root / SYNC_MODULE.CANONICAL_REVIEW_TARGET
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("canonical\n", encoding="utf-8")

        with self.assertRaisesRegex(SYNC_MODULE.SyncError, "missing required file"):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, ())

        for relative in SYNC_MODULE.CANONICAL_REVIEW_REQUIRED_FILES:
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("canonical\n", encoding="utf-8")
        (target / "SKILL.md").write_text(
            "Use $pr-readiness-review-workflow.\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SYNC_MODULE.SyncError, "retired reference"):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, ())

        (target / "SKILL.md").write_text("canonical\n", encoding="utf-8")
        SYNC_MODULE.sync_sources(self.repo_root, self.source_root, ())

    def test_sync_rejects_retired_review_reference_outside_canonical_target(
        self,
    ) -> None:
        agents = self.repo_root / "personal_codex" / "AGENTS.md"
        agents.parent.mkdir(parents=True)
        agents.write_text(
            "Use $external-review-playbook.\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "private overlay retains retired review reference",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, ())

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

    def test_synthetic_token_fixture_sync_rule_copies_templates(self) -> None:
        source = (
            self.source_root
            / "codex-review-workflows"
            / "skills"
            / "synthetic-token-fixtures"
        )
        agents = source / "agents"
        references = source / "references"
        agents.mkdir(parents=True)
        references.mkdir(parents=True)
        (source / "SKILL.md").write_text("synthetic fixture skill\n", encoding="utf-8")
        (agents / "openai.yaml").write_text(
            "interface:\n  display_name: Synthetic Token Fixtures\n",
            encoding="utf-8",
        )
        (references / "fixture-templates.md").write_text(
            "<SYNTHETIC_ACCESS_TOKEN>\n",
            encoding="utf-8",
        )
        rule = next(
            rule
            for rule in SYNC_MODULE.SYNC_RULES
            if rule.target == Path("personal_codex/skills/synthetic-token-fixtures")
        )

        SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

        target = self.repo_root / rule.target
        self.assertEqual(
            (target / "SKILL.md").read_text(encoding="utf-8"),
            "synthetic fixture skill\n",
        )
        self.assertEqual(
            (target / "agents/openai.yaml").read_text(encoding="utf-8"),
            "interface:\n  display_name: Synthetic Token Fixtures\n",
        )
        self.assertEqual(
            (target / "references/fixture-templates.md").read_text(encoding="utf-8"),
            "<SYNTHETIC_ACCESS_TOKEN>\n",
        )

    def test_session_mining_sync_rule_builds_remote_host_private_variant(self) -> None:
        source = (
            self.source_root
            / "codex-workflow-hygiene"
            / "skills"
            / "codex-session-mining"
        )
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
            self.repo_root
            / "personal_codex"
            / "skills"
            / "codex-session-mining"
            / "SKILL.md"
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
        self.assertIn("`$remote-host-context`'s default evidence scope", synced_skill)
        self.assertIn("Remote access belongs to `remote-host-context`", synced_skill)
        self.assertNotIn("environment-specific remote evidence workflow", synced_skill)
        self.assertIn("$remote-host-context", synced_reference)
        self.assertIn("default evidence scope", synced_reference)

    def test_session_retrospective_sync_rule_adds_private_default_hosts(self) -> None:
        source = (
            self.source_root
            / "codex-workflow-hygiene"
            / "skills"
            / "codex-session-retrospective"
        )
        scripts = source / "scripts"
        scripts.mkdir(parents=True)
        (source / "SKILL.md").write_text(
            "- Default host scope follows `$remote-host-context`: local machine, `miku-bot-dev`, and `hoteng-srv-01`.\n"
            "Retained host labels are restricted to `local`, the two default remote hosts, and `custom_source`.\n",
            encoding="utf-8",
        )
        (scripts / "session_retrospective.py").write_text(
            'DEFAULT_REMOTE_HOSTS = ("miku-bot-dev", "hoteng-srv-01")\n'
            'help="Source in HOST=PATH form. Defaults to local=~/.codex plus materialized miku-bot-dev and hoteng-srv-01 sources."\n',
            encoding="utf-8",
        )
        (scripts / "remote_codex_probe.py").write_text(
            "HOSTS = {\n"
            '    "local": {"kind": "local", "label": "local", "codex_root": "~/.codex"},\n'
            '    "miku-bot-dev": {\n'
            '        "kind": "ssh",\n'
            '        "label": "miku-bot-dev",\n'
            '        "ssh_target": "miku-bot-dev",\n'
            '        "codex_root": "/home/hoteng/.codex",\n'
            "    },\n"
            '    "hoteng-srv-01": {\n'
            '        "kind": "ssh",\n'
            '        "label": "hoteng-srv-01",\n'
            '        "ssh_target": "hoteng-srv-01",\n'
            '        "codex_root": "/home/hoteng/.codex",\n'
            "    },\n"
            "}\n",
            encoding="utf-8",
        )
        rule = next(
            rule
            for rule in SYNC_MODULE.SYNC_RULES
            if rule.target == Path("personal_codex/skills/codex-session-retrospective")
        )

        SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

        target = self.repo_root / rule.target
        synced_skill = (target / "SKILL.md").read_text(encoding="utf-8")
        synced_script = (target / "scripts/session_retrospective.py").read_text(
            encoding="utf-8"
        )
        synced_probe = (target / "scripts/remote_codex_probe.py").read_text(
            encoding="utf-8"
        )
        for host in ("BL-mac-mini-m4-hoteng", "codex-hoteng-srv-01"):
            self.assertIn(host, synced_skill)
            self.assertIn(host, synced_script)
            self.assertIn(host, synced_probe)
        self.assertIn("the four default remote hosts", synced_skill)
        self.assertIn('"codex_root": "/Users/hoteng/.codex"', synced_probe)
        self.assertIn('"codex_root": "/home/codex/.codex"', synced_probe)

    def test_session_mining_sync_rule_rejects_remote_host_residuals(self) -> None:
        source = (
            self.source_root
            / "codex-workflow-hygiene"
            / "skills"
            / "codex-session-mining"
        )
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
        source = (
            self.source_root
            / "codex-workflow-hygiene"
            / "skills"
            / "codex-skill-authoring"
        )
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
        (scripts / "codex_skill_validate.py").write_text(
            "#!/usr/bin/env python3\n", encoding="utf-8"
        )
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
            replacements=(
                SYNC_MODULE.Replacement("public placeholder", "private replacement"),
            ),
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
        (self.repo_root / "personal_codex").symlink_to(
            outside, target_is_directory=True
        )
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

        self.assertFalse(
            (self.repo_root / "personal_codex" / "skills" / "example").exists()
        )

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
            (
                self.repo_root / "personal_codex" / "skills" / "example" / "SKILL.md"
            ).is_file()
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

        self.assertFalse(
            (self.repo_root / "personal_codex" / "skills" / "example").exists()
        )

    def test_regular_file_overlay_replaces_exact_bytes_after_text_replacements(
        self,
    ) -> None:
        source = self.source_root / "example-repo" / "skill"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(
            "Use this when the user asks.\n", encoding="utf-8"
        )
        (source / "catalog.json").write_text(
            '{"owner":"the user","pool":"public"}\n',
            encoding="utf-8",
        )
        private_catalog = self.repo_root / "private-overrides" / "catalog.json"
        private_catalog.parent.mkdir(parents=True)
        expected = b'{"owner":"the user","pool":"private","bytes":"\\u2603"}\n'
        private_catalog.write_bytes(expected)
        private_catalog.chmod(0o600)
        rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/example"),
            replacements=SYNC_MODULE.COMMON_JOEY_TEXT_REPLACEMENTS,
            regular_file_overlays=(
                SYNC_MODULE.RegularFileOverlay(
                    source=Path("private-overrides/catalog.json"),
                    target=Path("catalog.json"),
                ),
            ),
        )

        recovery_paths = SYNC_MODULE.sync_sources(
            self.repo_root,
            self.source_root,
            (rule,),
        )

        target = self.repo_root / "personal_codex" / "skills" / "example"
        self.assertEqual((target / "catalog.json").read_bytes(), expected)
        self.assertEqual(private_catalog.read_bytes(), expected)
        self.assertEqual(stat.S_IMODE(private_catalog.stat().st_mode), 0o600)
        self.assertEqual(
            stat.S_IMODE((target / "catalog.json").stat().st_mode),
            SYNC_MODULE.REGULAR_FILE_OVERLAY_TARGET_MODE,
        )
        self.assertEqual(
            (target / "SKILL.md").read_text(encoding="utf-8"),
            "Use this when Joey asks.\n",
        )
        self.assertEqual(len(recovery_paths), 1)
        self.assertTrue(recovery_paths[0].is_relative_to(self.repo_root / ".codex-tmp"))
        self.assertEqual(list(recovery_paths[0].iterdir()), [])

    def test_regular_file_overlay_rejects_unsafe_paths(self) -> None:
        source = self.source_root / "example-repo" / "skill"
        source.mkdir(parents=True)
        (source / "catalog.json").write_text("public\n", encoding="utf-8")
        private_catalog = self.repo_root / "private-overrides" / "catalog.json"
        private_catalog.parent.mkdir(parents=True)
        private_catalog.write_text("private\n", encoding="utf-8")

        cases = (
            (Path("/private/catalog.json"), Path("catalog.json"), "source"),
            (Path("../private/catalog.json"), Path("catalog.json"), "source"),
            (Path("private-overrides/catalog.json"), Path("/catalog.json"), "target"),
            (Path("private-overrides/catalog.json"), Path("../catalog.json"), "target"),
        )
        for overlay_source, overlay_target, field in cases:
            with self.subTest(source=overlay_source, target=overlay_target):
                rule = SYNC_MODULE.SyncRule(
                    repo="example-repo",
                    source=Path("skill"),
                    target=Path("personal_codex/skills/example"),
                    regular_file_overlays=(
                        SYNC_MODULE.RegularFileOverlay(overlay_source, overlay_target),
                    ),
                )
                with self.assertRaisesRegex(
                    SYNC_MODULE.SyncError,
                    f"unsafe regular-file overlay {field}",
                ):
                    SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_regular_file_overlay_rejects_duplicate_output_target(self) -> None:
        source = self.source_root / "example-repo" / "skill"
        source.mkdir(parents=True)
        (source / "catalog.json").write_text("public\n", encoding="utf-8")
        rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/example"),
            regular_file_overlays=(
                SYNC_MODULE.RegularFileOverlay(Path("private/a"), Path("catalog.json")),
                SYNC_MODULE.RegularFileOverlay(Path("private/b"), Path("catalog.json")),
            ),
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError, "duplicate regular-file overlay target"
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_regular_file_overlay_requires_existing_regular_source_and_target(
        self,
    ) -> None:
        source = self.source_root / "example-repo" / "skill"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("public\n", encoding="utf-8")
        target = self.repo_root / "personal_codex" / "skills" / "example" / "SKILL.md"
        target.parent.mkdir(parents=True)
        target.write_text("existing\n", encoding="utf-8")

        missing_source_rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/example"),
            regular_file_overlays=(
                SYNC_MODULE.RegularFileOverlay(
                    Path("private/missing.json"), Path("SKILL.md")
                ),
            ),
        )
        with self.assertRaisesRegex(SYNC_MODULE.SyncError, "overlay source missing"):
            SYNC_MODULE.sync_sources(
                self.repo_root,
                self.source_root,
                (missing_source_rule,),
            )
        self.assertEqual(target.read_text(encoding="utf-8"), "existing\n")

        private_catalog = self.repo_root / "private" / "catalog.json"
        private_catalog.parent.mkdir()
        private_catalog.write_text("private\n", encoding="utf-8")
        missing_target_rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/example"),
            regular_file_overlays=(
                SYNC_MODULE.RegularFileOverlay(
                    Path("private/catalog.json"),
                    Path("catalog.json"),
                ),
            ),
        )
        with self.assertRaisesRegex(SYNC_MODULE.SyncError, "overlay target missing"):
            SYNC_MODULE.sync_sources(
                self.repo_root,
                self.source_root,
                (missing_target_rule,),
            )
        self.assertEqual(target.read_text(encoding="utf-8"), "existing\n")

    def test_regular_file_overlay_rejects_source_and_target_type_drift(self) -> None:
        source = self.source_root / "example-repo" / "skill"
        source.mkdir(parents=True)
        (source / "catalog.json").mkdir()
        private_catalog = self.repo_root / "private" / "catalog.json"
        private_catalog.mkdir(parents=True)
        rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/example"),
            regular_file_overlays=(
                SYNC_MODULE.RegularFileOverlay(
                    Path("private/catalog.json"),
                    Path("catalog.json"),
                ),
            ),
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError, "source is not a regular file"
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

        private_catalog.rmdir()
        private_catalog.write_text("private\n", encoding="utf-8")
        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError, "target is not a regular file"
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_regular_file_overlay_rejects_symlink_source_and_target(self) -> None:
        outside = self.root / "outside.json"
        outside.write_text("private\n", encoding="utf-8")
        private_catalog = self.repo_root / "private" / "catalog.json"
        private_catalog.parent.mkdir()
        private_catalog.symlink_to(outside)

        source = self.source_root / "example-repo" / "skill"
        source.mkdir(parents=True)
        (source / "catalog.json").write_text("public\n", encoding="utf-8")
        rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/example"),
            regular_file_overlays=(
                SYNC_MODULE.RegularFileOverlay(
                    Path("private/catalog.json"),
                    Path("catalog.json"),
                ),
            ),
        )

        with self.assertRaisesRegex(SYNC_MODULE.SyncError, "overlay source symlink"):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

        staging = (self.repo_root / "staging").resolve()
        staging.mkdir()
        (staging / "catalog.json").symlink_to(outside)
        with self.assertRaisesRegex(SYNC_MODULE.SyncError, "target symlink"):
            SYNC_MODULE._write_regular_file_overlay_target(
                staging,
                Path("catalog.json"),
                b"private\n",
            )

    def test_regular_file_overlay_rejects_hard_linked_source_and_target(self) -> None:
        source = self.source_root / "example-repo" / "skill"
        source.mkdir(parents=True)
        (source / "catalog.json").write_text("public\n", encoding="utf-8")
        private_catalog = self.repo_root / "private" / "catalog.json"
        private_catalog.parent.mkdir()
        private_catalog.write_text("private\n", encoding="utf-8")
        os.link(private_catalog, private_catalog.with_name("catalog-alias.json"))
        rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/example"),
            regular_file_overlays=(
                SYNC_MODULE.RegularFileOverlay(
                    Path("private/catalog.json"),
                    Path("catalog.json"),
                ),
            ),
        )

        with self.assertRaisesRegex(SYNC_MODULE.SyncError, "exactly one hard link"):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

        staging = (self.repo_root / "staging-hard-link").resolve()
        staging.mkdir()
        target = staging / "catalog.json"
        target.write_text("public\n", encoding="utf-8")
        os.link(target, staging / "catalog-alias.json")
        with self.assertRaisesRegex(SYNC_MODULE.SyncError, "exactly one hard link"):
            SYNC_MODULE._write_regular_file_overlay_target(
                staging,
                Path("catalog.json"),
                b"private\n",
            )

    def test_regular_file_overlay_detects_source_identity_drift(self) -> None:
        source = self.repo_root / "private" / "catalog.json"
        source.parent.mkdir()
        source.write_text("private\n", encoding="utf-8")
        real_fstat = SYNC_MODULE.os.fstat
        regular_file_calls = 0

        def drifting_fstat(descriptor):
            nonlocal regular_file_calls
            metadata = real_fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                return metadata
            regular_file_calls += 1
            if regular_file_calls != 2:
                return metadata
            return SimpleNamespace(
                st_dev=metadata.st_dev,
                st_ino=metadata.st_ino,
                st_mode=metadata.st_mode,
                st_nlink=metadata.st_nlink,
                st_uid=metadata.st_uid,
                st_size=metadata.st_size,
                st_mtime_ns=metadata.st_mtime_ns + 1,
                st_ctime_ns=metadata.st_ctime_ns,
            )

        with mock.patch.object(SYNC_MODULE.os, "fstat", side_effect=drifting_fstat):
            with self.assertRaisesRegex(SYNC_MODULE.SyncError, "changed while reading"):
                SYNC_MODULE._read_regular_file_overlay_source(
                    self.repo_root,
                    Path("private/catalog.json"),
                )

    def test_regular_file_overlay_blocks_source_ancestor_swap_after_preflight(
        self,
    ) -> None:
        private = self.repo_root / "private"
        private.mkdir()
        (private / "catalog.json").write_text("original\n", encoding="utf-8")
        outside = self.root / "outside-source"
        outside.mkdir()
        outside_catalog = outside / "catalog.json"
        outside_catalog.write_text("outside\n", encoding="utf-8")
        saved = self.repo_root / "private-before-swap"
        real_ensure_safe_source = SYNC_MODULE._ensure_safe_source

        def swap_ancestor(source_root, source):
            real_ensure_safe_source(source_root, source)
            private.rename(saved)
            private.symlink_to(outside, target_is_directory=True)

        with mock.patch.object(
            SYNC_MODULE,
            "_ensure_safe_source",
            side_effect=swap_ancestor,
        ):
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "regular-file overlay source directory chain binding changed",
            ):
                SYNC_MODULE._read_regular_file_overlay_source(
                    self.repo_root,
                    Path("private/catalog.json"),
                )

        self.assertEqual(outside_catalog.read_text(encoding="utf-8"), "outside\n")

    def test_regular_file_overlay_blocks_target_ancestor_swap_after_preflight(
        self,
    ) -> None:
        staging = (self.repo_root / "staging-ancestor-swap").resolve()
        nested = staging / "nested"
        nested.mkdir(parents=True)
        (nested / "catalog.json").write_text("public\n", encoding="utf-8")
        outside = self.root / "outside-target"
        outside.mkdir()
        outside_catalog = outside / "catalog.json"
        outside_catalog.write_text("outside\n", encoding="utf-8")
        saved = staging / "nested-before-swap"
        real_ensure_safe_target = SYNC_MODULE._ensure_safe_target

        def swap_ancestor(repo_root, target):
            real_ensure_safe_target(repo_root, target)
            nested.rename(saved)
            nested.symlink_to(outside, target_is_directory=True)

        with mock.patch.object(
            SYNC_MODULE,
            "_ensure_safe_target",
            side_effect=swap_ancestor,
        ):
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "regular-file overlay target directory chain binding changed",
            ):
                SYNC_MODULE._write_regular_file_overlay_target(
                    staging,
                    Path("nested/catalog.json"),
                    b"private\n",
                )

        self.assertEqual(outside_catalog.read_text(encoding="utf-8"), "outside\n")

    def test_regular_file_overlay_blocks_source_descendant_swap_after_preflight(
        self,
    ) -> None:
        nested = self.repo_root / "private" / "nested"
        nested.mkdir(parents=True)
        (nested / "catalog.json").write_text("original\n", encoding="utf-8")
        replacement = self.root / "replacement-source-descendant"
        replacement.mkdir()
        (replacement / "catalog.json").write_text("replaced\n", encoding="utf-8")
        saved = nested.with_name("nested-before-swap")
        real_ensure_safe_source = SYNC_MODULE._ensure_safe_source

        def swap_descendant(source_root, source):
            real_ensure_safe_source(source_root, source)
            nested.rename(saved)
            replacement.rename(nested)

        with mock.patch.object(
            SYNC_MODULE,
            "_ensure_safe_source",
            side_effect=swap_descendant,
        ):
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "regular-file overlay source directory chain binding changed",
            ):
                SYNC_MODULE._read_regular_file_overlay_source(
                    self.repo_root,
                    Path("private/nested/catalog.json"),
                )

        self.assertEqual(
            (saved / "catalog.json").read_text(encoding="utf-8"),
            "original\n",
        )
        self.assertEqual(
            (nested / "catalog.json").read_text(encoding="utf-8"),
            "replaced\n",
        )

    def test_regular_file_overlay_blocks_target_descendant_swap_after_preflight(
        self,
    ) -> None:
        staging = (self.repo_root / "staging-descendant-preflight").resolve()
        nested = staging / "nested" / "inner"
        nested.mkdir(parents=True)
        (nested / "catalog.json").write_text("public\n", encoding="utf-8")
        replacement = self.root / "replacement-target-descendant"
        replacement.mkdir()
        (replacement / "catalog.json").write_text("replaced\n", encoding="utf-8")
        saved = nested.with_name("inner-before-swap")
        real_ensure_safe_target = SYNC_MODULE._ensure_safe_target

        def swap_descendant(repo_root, target):
            real_ensure_safe_target(repo_root, target)
            nested.rename(saved)
            replacement.rename(nested)

        with mock.patch.object(
            SYNC_MODULE,
            "_ensure_safe_target",
            side_effect=swap_descendant,
        ):
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "regular-file overlay target directory chain binding changed",
            ):
                SYNC_MODULE._write_regular_file_overlay_target(
                    staging,
                    Path("nested/inner/catalog.json"),
                    b"private\n",
                )

        self.assertEqual(
            (saved / "catalog.json").read_text(encoding="utf-8"),
            "public\n",
        )
        self.assertEqual(
            (nested / "catalog.json").read_text(encoding="utf-8"),
            "replaced\n",
        )
        self._assert_no_regular_file_overlay_temporaries(saved, nested)

    def test_regular_file_overlay_blocks_source_root_swap_after_preflight(
        self,
    ) -> None:
        private = self.repo_root / "private"
        private.mkdir()
        (private / "catalog.json").write_text("original\n", encoding="utf-8")
        outside_root = self.root / "outside-source-root"
        outside_private = outside_root / "private"
        outside_private.mkdir(parents=True)
        outside_catalog = outside_private / "catalog.json"
        outside_catalog.write_text("outside\n", encoding="utf-8")
        saved = self.root / "target-before-root-swap"
        real_ensure_safe_source = SYNC_MODULE._ensure_safe_source

        def swap_root(source_root, source):
            real_ensure_safe_source(source_root, source)
            self.repo_root.rename(saved)
            self.repo_root.symlink_to(outside_root, target_is_directory=True)

        with mock.patch.object(
            SYNC_MODULE,
            "_ensure_safe_source",
            side_effect=swap_root,
        ):
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "regular-file overlay source directory chain binding changed",
            ):
                SYNC_MODULE._read_regular_file_overlay_source(
                    self.repo_root,
                    Path("private/catalog.json"),
                )

        self.assertEqual(outside_catalog.read_text(encoding="utf-8"), "outside\n")

    def test_regular_file_overlay_blocks_target_root_swap_after_preflight(
        self,
    ) -> None:
        staging = self.repo_root / "staging-root-swap"
        staging.mkdir()
        (staging / "catalog.json").write_text("public\n", encoding="utf-8")
        outside_root = self.root / "outside-target-root"
        outside_root.mkdir()
        outside_catalog = outside_root / "catalog.json"
        outside_catalog.write_text("outside\n", encoding="utf-8")
        saved = self.repo_root / "staging-before-root-swap"
        real_ensure_safe_target = SYNC_MODULE._ensure_safe_target

        def swap_root(repo_root, target):
            real_ensure_safe_target(repo_root, target)
            staging.rename(saved)
            staging.symlink_to(outside_root, target_is_directory=True)

        with mock.patch.object(
            SYNC_MODULE,
            "_ensure_safe_target",
            side_effect=swap_root,
        ):
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "regular-file overlay target directory chain binding changed",
            ):
                SYNC_MODULE._write_regular_file_overlay_target(
                    staging,
                    Path("catalog.json"),
                    b"private\n",
                )

        self.assertEqual(outside_catalog.read_text(encoding="utf-8"), "outside\n")

    def test_regular_file_overlay_blocks_source_directory_root_swap_after_preflight(
        self,
    ) -> None:
        private = self.repo_root / "private"
        private.mkdir()
        (private / "catalog.json").write_text("original\n", encoding="utf-8")
        replacement_root = self.root / "replacement-source-root"
        replacement_private = replacement_root / "private"
        replacement_private.mkdir(parents=True)
        (replacement_private / "catalog.json").write_text(
            "replacement\n",
            encoding="utf-8",
        )
        saved = self.root / "target-before-directory-root-swap"
        real_ensure_safe_source = SYNC_MODULE._ensure_safe_source

        def swap_root(source_root, source):
            real_ensure_safe_source(source_root, source)
            self.repo_root.rename(saved)
            replacement_root.rename(self.repo_root)

        with mock.patch.object(
            SYNC_MODULE,
            "_ensure_safe_source",
            side_effect=swap_root,
        ):
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "regular-file overlay source directory chain binding changed",
            ):
                SYNC_MODULE._read_regular_file_overlay_source(
                    self.repo_root,
                    Path("private/catalog.json"),
                )

        self.assertEqual(
            (self.repo_root / "private" / "catalog.json").read_text(encoding="utf-8"),
            "replacement\n",
        )

    def test_regular_file_overlay_blocks_target_directory_root_swap_after_preflight(
        self,
    ) -> None:
        staging = self.repo_root / "staging-directory-root-swap"
        staging.mkdir()
        (staging / "catalog.json").write_text("public\n", encoding="utf-8")
        replacement_root = self.root / "replacement-target-root"
        replacement_root.mkdir()
        (replacement_root / "catalog.json").write_text(
            "replacement\n",
            encoding="utf-8",
        )
        saved = self.repo_root / "staging-before-directory-root-swap"
        real_ensure_safe_target = SYNC_MODULE._ensure_safe_target

        def swap_root(repo_root, target):
            real_ensure_safe_target(repo_root, target)
            staging.rename(saved)
            replacement_root.rename(staging)

        with mock.patch.object(
            SYNC_MODULE,
            "_ensure_safe_target",
            side_effect=swap_root,
        ):
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "regular-file overlay target directory chain binding changed",
            ):
                SYNC_MODULE._write_regular_file_overlay_target(
                    staging,
                    Path("catalog.json"),
                    b"private\n",
                )

        self.assertEqual(
            (staging / "catalog.json").read_text(encoding="utf-8"),
            "replacement\n",
        )

    def test_regular_file_overlay_detects_source_root_swap_after_binding_check(
        self,
    ) -> None:
        private = self.repo_root / "private"
        private.mkdir()
        (private / "catalog.json").write_text("original\n", encoding="utf-8")
        replacement_root = self.root / "late-replacement-source-root"
        replacement_private = replacement_root / "private"
        replacement_private.mkdir(parents=True)
        (replacement_private / "catalog.json").write_text(
            "replacement\n",
            encoding="utf-8",
        )
        saved = self.root / "target-before-late-root-swap"
        real_assert_binding = (
            SYNC_MODULE._assert_regular_file_overlay_directory_chain_binding
        )
        real_read = SYNC_MODULE.os.read
        calls = 0
        read_inodes: list[int] = []

        def swap_after_binding(chain, *, label):
            nonlocal calls
            real_assert_binding(chain, label=label)
            calls += 1
            if calls == 1:
                self.repo_root.rename(saved)
                replacement_root.rename(self.repo_root)

        def record_read(descriptor, size):
            metadata = SYNC_MODULE.os.fstat(descriptor)
            if stat.S_ISREG(metadata.st_mode):
                read_inodes.append(metadata.st_ino)
            return real_read(descriptor, size)

        with (
            mock.patch.object(
                SYNC_MODULE,
                "_assert_regular_file_overlay_directory_chain_binding",
                side_effect=swap_after_binding,
            ),
            mock.patch.object(SYNC_MODULE.os, "read", side_effect=record_read),
        ):
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "regular-file overlay source directory chain binding changed",
            ):
                SYNC_MODULE._read_regular_file_overlay_source(
                    self.repo_root,
                    Path("private/catalog.json"),
                )

        self.assertEqual(
            (self.repo_root / "private" / "catalog.json").read_text(encoding="utf-8"),
            "replacement\n",
        )
        self.assertTrue(read_inodes)
        original_inode = (saved / "private" / "catalog.json").stat().st_ino
        replacement_inode = (self.repo_root / "private" / "catalog.json").stat().st_ino
        self.assertEqual(set(read_inodes), {original_inode})
        self.assertNotEqual(original_inode, replacement_inode)

    def test_regular_file_overlay_detects_target_root_swap_after_binding_check(
        self,
    ) -> None:
        staging = self.repo_root / "staging-late-root-swap"
        staging.mkdir()
        (staging / "catalog.json").write_text("public\n", encoding="utf-8")
        replacement_root = self.root / "late-replacement-target-root"
        replacement_root.mkdir()
        (replacement_root / "catalog.json").write_text(
            "replacement\n",
            encoding="utf-8",
        )
        saved = self.repo_root / "staging-before-late-root-swap"
        real_assert_binding = (
            SYNC_MODULE._assert_regular_file_overlay_directory_chain_binding
        )
        calls = 0

        def swap_after_binding(chain, *, label):
            nonlocal calls
            real_assert_binding(chain, label=label)
            calls += 1
            if calls == 1:
                staging.rename(saved)
                replacement_root.rename(staging)

        with mock.patch.object(
            SYNC_MODULE,
            "_assert_regular_file_overlay_directory_chain_binding",
            side_effect=swap_after_binding,
        ):
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "regular-file overlay target directory chain binding changed",
            ):
                SYNC_MODULE._write_regular_file_overlay_target(
                    staging,
                    Path("catalog.json"),
                    b"private\n",
                )

        self.assertEqual(
            (staging / "catalog.json").read_text(encoding="utf-8"),
            "replacement\n",
        )
        self.assertEqual(
            (saved / "catalog.json").read_text(encoding="utf-8"),
            "public\n",
        )
        self._assert_no_regular_file_overlay_temporaries(saved)

    def test_regular_file_overlay_detects_source_descendant_swap_after_binding_check(
        self,
    ) -> None:
        nested = self.repo_root / "private" / "nested"
        nested.mkdir(parents=True)
        (nested / "catalog.json").write_text("original\n", encoding="utf-8")
        replacement = self.root / "late-replacement-source-descendant"
        replacement.mkdir()
        (replacement / "catalog.json").write_text("replaced\n", encoding="utf-8")
        saved = nested.with_name("nested-before-late-swap")
        real_assert_binding = (
            SYNC_MODULE._assert_regular_file_overlay_directory_chain_binding
        )
        real_read = SYNC_MODULE.os.read
        calls = 0
        read_inodes: list[int] = []

        def swap_after_binding(chain, *, label):
            nonlocal calls
            real_assert_binding(chain, label=label)
            calls += 1
            if calls == 1:
                nested.rename(saved)
                replacement.rename(nested)

        def record_read(descriptor, size):
            metadata = SYNC_MODULE.os.fstat(descriptor)
            if stat.S_ISREG(metadata.st_mode):
                read_inodes.append(metadata.st_ino)
            return real_read(descriptor, size)

        with (
            mock.patch.object(
                SYNC_MODULE,
                "_assert_regular_file_overlay_directory_chain_binding",
                side_effect=swap_after_binding,
            ),
            mock.patch.object(SYNC_MODULE.os, "read", side_effect=record_read),
        ):
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "regular-file overlay source directory chain binding changed",
            ):
                SYNC_MODULE._read_regular_file_overlay_source(
                    self.repo_root,
                    Path("private/nested/catalog.json"),
                )

        original_inode = (saved / "catalog.json").stat().st_ino
        replacement_inode = (nested / "catalog.json").stat().st_ino
        self.assertTrue(read_inodes)
        self.assertEqual(set(read_inodes), {original_inode})
        self.assertNotEqual(original_inode, replacement_inode)

    def test_regular_file_overlay_detects_target_descendant_swap_after_binding_check(
        self,
    ) -> None:
        staging = (self.repo_root / "staging-late-descendant-swap").resolve()
        nested = staging / "nested" / "inner"
        nested.mkdir(parents=True)
        (nested / "catalog.json").write_text("public\n", encoding="utf-8")
        replacement = self.root / "late-replacement-target-descendant"
        replacement.mkdir()
        (replacement / "catalog.json").write_text("replaced\n", encoding="utf-8")
        saved = nested.with_name("inner-before-late-swap")
        real_assert_binding = (
            SYNC_MODULE._assert_regular_file_overlay_directory_chain_binding
        )
        calls = 0

        def swap_after_binding(chain, *, label):
            nonlocal calls
            real_assert_binding(chain, label=label)
            calls += 1
            if calls == 1:
                nested.rename(saved)
                replacement.rename(nested)

        with mock.patch.object(
            SYNC_MODULE,
            "_assert_regular_file_overlay_directory_chain_binding",
            side_effect=swap_after_binding,
        ):
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "regular-file overlay target directory chain binding changed",
            ):
                SYNC_MODULE._write_regular_file_overlay_target(
                    staging,
                    Path("nested/inner/catalog.json"),
                    b"private\n",
                )

        self.assertEqual(
            (saved / "catalog.json").read_text(encoding="utf-8"),
            "public\n",
        )
        self.assertEqual(
            (nested / "catalog.json").read_text(encoding="utf-8"),
            "replaced\n",
        )
        self._assert_no_regular_file_overlay_temporaries(saved, nested)

    def test_regular_file_overlay_secure_open_requires_dir_fd_support(self) -> None:
        with mock.patch.object(SYNC_MODULE.os, "supports_dir_fd", set()):
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "secure regular-file overlay source path traversal is unavailable",
            ):
                SYNC_MODULE._open_regular_file_overlay_root(
                    self.repo_root,
                    label="source",
                )

    def test_regular_file_overlay_atomic_replace_requires_dir_fd_support(self) -> None:
        supported = set(SYNC_MODULE.os.supports_dir_fd)
        for missing in (SYNC_MODULE.os.rename, SYNC_MODULE.os.unlink):
            with self.subTest(missing=missing.__name__):
                staging = (
                    self.repo_root / f"staging-missing-{missing.__name__}"
                ).resolve()
                staging.mkdir()
                target = staging / "catalog.json"
                target.write_text("public\n", encoding="utf-8")
                with mock.patch.object(
                    SYNC_MODULE.os,
                    "supports_dir_fd",
                    supported - {missing},
                ):
                    with self.assertRaisesRegex(
                        SYNC_MODULE.SyncError,
                        "secure regular-file overlay atomic replace is unavailable",
                    ):
                        SYNC_MODULE._write_regular_file_overlay_target(
                            staging,
                            Path("catalog.json"),
                            b"private\n",
                        )
                self.assertEqual(target.read_text(encoding="utf-8"), "public\n")
                self._assert_no_regular_file_overlay_temporaries(staging)

    def test_regular_file_overlay_noreplace_primitive_platform_abi(self) -> None:
        expected_argtypes = (
            SYNC_MODULE.ctypes.c_int,
            SYNC_MODULE.ctypes.c_char_p,
            SYNC_MODULE.ctypes.c_int,
            SYNC_MODULE.ctypes.c_char_p,
            SYNC_MODULE.ctypes.c_uint,
        )
        for platform, symbol, flags in (
            ("darwin", "renameatx_np", 0x00000004),
            ("linux", "renameat2", 1),
        ):
            with self.subTest(platform=platform):
                function = mock.Mock(return_value=0)
                libc = SimpleNamespace(**{symbol: function})
                with (
                    mock.patch.object(SYNC_MODULE.sys, "platform", platform),
                    mock.patch.object(
                        SYNC_MODULE.ctypes,
                        "CDLL",
                        return_value=libc,
                    ) as cdll,
                ):
                    primitive = (
                        SYNC_MODULE._load_regular_file_overlay_noreplace_primitive()
                    )
                cdll.assert_called_once_with(None, use_errno=True)
                self.assertIs(primitive.function, function)
                self.assertEqual(primitive.flags, flags)
                self.assertEqual(function.argtypes, expected_argtypes)
                self.assertIs(function.restype, SYNC_MODULE.ctypes.c_int)

    def test_regular_file_overlay_noreplace_errno_mapping(self) -> None:
        primitive = SYNC_MODULE._RegularFileOverlayNoReplacePrimitive(
            function=mock.Mock(return_value=-1),
            flags=1,
        )
        with mock.patch.object(
            SYNC_MODULE.ctypes,
            "get_errno",
            return_value=errno.EEXIST,
        ):
            with self.assertRaises(
                SYNC_MODULE._RegularFileOverlayRenameError
            ) as raised:
                SYNC_MODULE._rename_regular_file_overlay_noreplace(
                    primitive,
                    1,
                    "source",
                    2,
                    "target",
                )
        self.assertEqual(raised.exception.error_number, errno.EEXIST)

        for unsupported in (errno.ENOSYS, errno.EINVAL):
            with self.subTest(unsupported=unsupported):
                with mock.patch.object(
                    SYNC_MODULE.ctypes,
                    "get_errno",
                    return_value=unsupported,
                ):
                    with self.assertRaisesRegex(
                        SYNC_MODULE.SyncError,
                        "no-replace rename is unavailable",
                    ):
                        SYNC_MODULE._rename_regular_file_overlay_noreplace(
                            primitive,
                            1,
                            "source",
                            2,
                            "target",
                        )

    def test_regular_file_overlay_entry_probe_errors_fail_closed(self) -> None:
        pinned_directory = SYNC_MODULE._PinnedRegularFileOverlayDirectory(
            path=Path("/protected"),
            descriptor=1,
            identity=(1, 2, stat.S_IFDIR | 0o700, os.getuid()),
        )
        backup = SYNC_MODULE._PinnedRegularFileOverlayEntry(
            name="backup",
            descriptor=2,
            identity=(1, 3, stat.S_IFDIR | 0o700, 2, os.getuid()),
        )
        for error_number in (errno.EIO, errno.EACCES):
            with self.subTest(error_number=error_number):
                with mock.patch.object(
                    SYNC_MODULE.os,
                    "stat",
                    side_effect=OSError(error_number, "probe failure"),
                ):
                    with self.assertRaisesRegex(
                        SYNC_MODULE.SyncError,
                        "cannot inspect regular-file overlay entry",
                    ):
                        SYNC_MODULE._regular_file_overlay_entry_exists(1, "entry")
                with mock.patch.object(
                    SYNC_MODULE.os,
                    "stat",
                    side_effect=OSError(error_number, "probe failure"),
                ):
                    with self.assertRaises(
                        SYNC_MODULE._RegularFileOverlayBackupRetentionError
                    ):
                        SYNC_MODULE._retain_regular_file_overlay_backup(
                            pinned_directory,
                            "backup",
                            backup,
                        )

    def test_regular_file_overlay_bounds_target_readback(self) -> None:
        staging = (self.repo_root / "staging-bounded-readback").resolve()
        staging.mkdir()
        target = staging / "catalog.json"
        target.write_text("public\n", encoding="utf-8")
        calls: list[int] = []

        def appending_read(_descriptor, size):
            calls.append(size)
            if len(calls) > 1:
                raise AssertionError("target read-back exceeded its byte budget")
            return b"x" * size

        with mock.patch.object(SYNC_MODULE.os, "read", side_effect=appending_read):
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "temporary byte verification failed",
            ):
                SYNC_MODULE._write_regular_file_overlay_target(
                    staging,
                    Path("catalog.json"),
                    b"private\n",
                )

        self.assertEqual(calls, [len(b"private\n") + 1])
        self.assertEqual(target.read_text(encoding="utf-8"), "public\n")
        self._assert_no_regular_file_overlay_temporaries(staging)

    def test_regular_file_overlay_visible_fifo_fails_without_blocking(self) -> None:
        stack, _target, staging, binding = (
            self._prepare_held_regular_file_overlay_target("visible-fifo")
        )
        with stack:
            visible = staging / "catalog.json"
            visible.unlink()
            os.mkfifo(visible)
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "is not a regular file",
            ):
                SYNC_MODULE._assert_regular_file_overlay_binding_at_visible_root(
                    staging,
                    binding,
                    label="fifo probe",
                )

    def test_regular_file_overlay_visible_open_requires_nonblocking_support(
        self,
    ) -> None:
        stack, _target, staging, binding = (
            self._prepare_held_regular_file_overlay_target("missing-nonblocking")
        )
        with stack:
            with mock.patch.object(SYNC_MODULE.os, "O_NONBLOCK", None):
                with self.assertRaisesRegex(
                    SYNC_MODULE.SyncError,
                    "nonblocking file open is unavailable",
                ):
                    SYNC_MODULE._assert_regular_file_overlay_binding_at_visible_root(
                        staging,
                        binding,
                        label="capability probe",
                    )

    def test_regular_file_overlay_atomically_replaces_preopened_old_target(
        self,
    ) -> None:
        staging = (self.repo_root / "staging-preopened-old-target").resolve()
        staging.mkdir()
        target = staging / "catalog.json"
        target.write_text("public\n", encoding="utf-8")
        old_descriptor = os.open(target, os.O_RDWR)
        old_identity = os.fstat(old_descriptor)
        real_rename = SYNC_MODULE.os.rename
        mutated = False

        def mutate_old_then_rename(
            source,
            destination,
            *,
            src_dir_fd=None,
            dst_dir_fd=None,
        ):
            nonlocal mutated
            os.pwrite(old_descriptor, b"attacker", 0)
            mutated = True
            real_rename(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )

        try:
            with mock.patch.object(
                SYNC_MODULE.os,
                "rename",
                side_effect=mutate_old_then_rename,
            ) as rename_mock:
                with mock.patch.object(
                    SYNC_MODULE.os,
                    "supports_dir_fd",
                    set(SYNC_MODULE.os.supports_dir_fd) | {rename_mock},
                ):
                    SYNC_MODULE._write_regular_file_overlay_target(
                        staging,
                        Path("catalog.json"),
                        b"private\n",
                    )
        finally:
            os.close(old_descriptor)

        self.assertTrue(mutated)
        self.assertEqual(target.read_bytes(), b"private\n")
        self.assertNotEqual(target.stat().st_ino, old_identity.st_ino)
        self.assertEqual(
            stat.S_IMODE(target.stat().st_mode),
            SYNC_MODULE.REGULAR_FILE_OVERLAY_TARGET_MODE,
        )
        self._assert_no_regular_file_overlay_temporaries(staging)

    def test_regular_file_overlay_rejects_staged_file_mutation_before_install(
        self,
    ) -> None:
        target = self._create_regular_file_overlay_target("late-file-mutation")
        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "staged target.*(binding changed|verification failed)",
        ):
            with SYNC_MODULE._regular_file_overlay_staging_directory(
                self.repo_root, target
            ) as scope:
                stack, staging, binding = (
                    self._prepare_scoped_regular_file_overlay_candidate(scope)
                )
                with stack:
                    SYNC_MODULE._assert_regular_file_overlay_binding_at_visible_root(
                        staging,
                        binding,
                        label="test validation",
                    )
                    (staging / "catalog.json").write_bytes(b"mutated\n")
                    SYNC_MODULE._replace_target_with_regular_file_overlays(
                        target,
                        staging,
                        (binding,),
                        staging_scope=scope,
                    )

        self.assertEqual((target / "catalog.json").read_bytes(), b"public\n")

    def test_regular_file_overlay_rejects_staging_root_replacement_before_install(
        self,
    ) -> None:
        target = self._create_regular_file_overlay_target("late-root-replacement")
        saved = self.root / "held-original-staging-root"
        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "staged target root binding changed",
        ):
            with SYNC_MODULE._regular_file_overlay_staging_directory(
                self.repo_root, target
            ) as scope:
                stack, staging, binding = (
                    self._prepare_scoped_regular_file_overlay_candidate(scope)
                )
                with stack:
                    SYNC_MODULE._assert_regular_file_overlay_binding_at_visible_root(
                        staging,
                        binding,
                        label="test validation",
                    )
                    staging.rename(saved)
                    staging.mkdir()
                    (staging / "catalog.json").write_bytes(b"private\n")
                    SYNC_MODULE._replace_target_with_regular_file_overlays(
                        target,
                        staging,
                        (binding,),
                        staging_scope=scope,
                    )

        self.assertEqual((target / "catalog.json").read_bytes(), b"public\n")

    def test_regular_file_overlay_rolls_back_post_install_mutation_before_backup_cleanup(
        self,
    ) -> None:
        target = self._create_regular_file_overlay_target("post-install-mutation")
        old_target_inode = target.stat().st_ino
        real_assert_binding = (
            SYNC_MODULE._assert_regular_file_overlay_binding_at_visible_root
        )
        mutated = False

        def mutate_installed(root, held_binding, *, label):
            nonlocal mutated
            if root == target and label == "installed target" and not mutated:
                (target / "catalog.json").write_bytes(b"mutated\n")
                mutated = True
            return real_assert_binding(root, held_binding, label=label)

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "installed target validation failed; prior target restored",
        ):
            with SYNC_MODULE._regular_file_overlay_staging_directory(
                self.repo_root, target
            ) as scope:
                stack, staging, binding = (
                    self._prepare_scoped_regular_file_overlay_candidate(scope)
                )
                with (
                    stack,
                    mock.patch.object(
                        SYNC_MODULE,
                        "_assert_regular_file_overlay_binding_at_visible_root",
                        side_effect=mutate_installed,
                    ),
                ):
                    SYNC_MODULE._replace_target_with_regular_file_overlays(
                        target,
                        staging,
                        (binding,),
                        staging_scope=scope,
                    )

        self.assertTrue(mutated)
        self.assertEqual(target.stat().st_ino, old_target_inode)
        self.assertEqual((target / "catalog.json").read_bytes(), b"public\n")

    def test_regular_file_overlay_noreplace_retains_backup_for_unknown_candidate(
        self,
    ) -> None:
        target = self._create_regular_file_overlay_target("unknown-candidate")
        real_rename = SYNC_MODULE._rename_regular_file_overlay_noreplace
        calls = 0
        scope_path: Path | None = None

        def insert_unknown_after_backup(*args):
            nonlocal calls
            real_rename(*args)
            calls += 1
            if calls == 1:
                target.mkdir()
                (target / "catalog.json").write_text(
                    "unknown\n",
                    encoding="utf-8",
                )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "candidate and prior target retained in recovery scope",
        ):
            with SYNC_MODULE._regular_file_overlay_staging_directory(
                self.repo_root, target
            ) as scope:
                scope_path = scope.path
                stack, staging, binding = (
                    self._prepare_scoped_regular_file_overlay_candidate(scope)
                )
                with (
                    stack,
                    mock.patch.object(
                        SYNC_MODULE,
                        "_rename_regular_file_overlay_noreplace",
                        side_effect=insert_unknown_after_backup,
                    ),
                ):
                    SYNC_MODULE._replace_target_with_regular_file_overlays(
                        target,
                        staging,
                        (binding,),
                        staging_scope=scope,
                    )

        self.assertEqual((target / "catalog.json").read_text(), "unknown\n")
        self.assertIsNotNone(scope_path)
        retained = list(scope_path.glob(".codex-private-overlay-backup-*"))
        self.assertEqual(len(retained), 1)
        self.assertEqual((retained[0] / "catalog.json").read_bytes(), b"public\n")
        self.assertEqual(
            (scope_path / "candidate/catalog.json").read_bytes(), b"private\n"
        )

    def test_regular_file_overlay_preserves_target_swapped_before_backup_move(
        self,
    ) -> None:
        target = self._create_regular_file_overlay_target("pre-backup-swap")
        saved_target = self.root / "pre-backup-swap-original"
        replacement = self.root / "pre-backup-swap-unknown"
        replacement.mkdir()
        (replacement / "catalog.json").write_text("unknown\n", encoding="utf-8")
        real_rename = SYNC_MODULE._rename_regular_file_overlay_noreplace
        swapped = False

        def swap_before_backup_move(*args):
            nonlocal swapped
            if not swapped:
                target.rename(saved_target)
                replacement.rename(target)
                swapped = True
            return real_rename(*args)

        with self.assertRaises(SYNC_MODULE.SyncError):
            with SYNC_MODULE._regular_file_overlay_staging_directory(
                self.repo_root, target
            ) as scope:
                scope_path = scope.path
                stack, staging, binding = (
                    self._prepare_scoped_regular_file_overlay_candidate(scope)
                )
                with (
                    stack,
                    mock.patch.object(
                        SYNC_MODULE,
                        "_rename_regular_file_overlay_noreplace",
                        side_effect=swap_before_backup_move,
                    ),
                ):
                    SYNC_MODULE._replace_target_with_regular_file_overlays(
                        target,
                        staging,
                        (binding,),
                        staging_scope=scope,
                    )

        self.assertTrue(swapped)
        self.assertEqual(
            (saved_target / "catalog.json").read_text(encoding="utf-8"),
            "public\n",
        )
        self.assertEqual(
            (scope_path / "candidate/catalog.json").read_bytes(), b"private\n"
        )
        unknown_backups = list(scope_path.glob(".codex-private-overlay-backup-*"))
        self.assertEqual(len(unknown_backups), 1)
        self.assertEqual(
            (unknown_backups[0] / "catalog.json").read_text(encoding="utf-8"),
            "unknown\n",
        )

    def test_regular_file_overlay_probe_error_preserves_staged_backup(self) -> None:
        target = self._create_regular_file_overlay_target("probe-error-recovery")
        real_rename = SYNC_MODULE._rename_regular_file_overlay_noreplace
        real_exists = SYNC_MODULE._regular_file_overlay_entry_exists
        backup_moved = False

        def fail_after_backup(*args):
            nonlocal backup_moved
            real_rename(*args)
            if not backup_moved:
                backup_moved = True
                raise SYNC_MODULE.SyncError("injected post-backup failure")

        def fail_target_probe(parent_descriptor, name):
            if backup_moved and name == target.name:
                raise SYNC_MODULE.SyncError("injected target probe error")
            return real_exists(parent_descriptor, name)

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "candidate and prior target retained in recovery scope",
        ):
            with SYNC_MODULE._regular_file_overlay_staging_directory(
                self.repo_root, target
            ) as scope:
                scope_path = scope.path
                stack, staging, binding = (
                    self._prepare_scoped_regular_file_overlay_candidate(scope)
                )
                with (
                    stack,
                    mock.patch.object(
                        SYNC_MODULE,
                        "_rename_regular_file_overlay_noreplace",
                        side_effect=fail_after_backup,
                    ),
                    mock.patch.object(
                        SYNC_MODULE,
                        "_regular_file_overlay_entry_exists",
                        side_effect=fail_target_probe,
                    ),
                ):
                    SYNC_MODULE._replace_target_with_regular_file_overlays(
                        target,
                        staging,
                        (binding,),
                        staging_scope=scope,
                    )

        retained = list(scope_path.glob(".codex-private-overlay-backup-*"))
        self.assertEqual(len(retained), 1)
        self.assertEqual((retained[0] / "catalog.json").read_bytes(), b"public\n")
        self.assertEqual(
            (scope_path / "candidate/catalog.json").read_bytes(), b"private\n"
        )

    def test_regular_file_overlay_noreplace_capability_fails_before_target_mutation(
        self,
    ) -> None:
        target = self._create_regular_file_overlay_target("missing-noreplace")
        old_target_inode = target.stat().st_ino
        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "noreplace unavailable",
        ):
            with SYNC_MODULE._regular_file_overlay_staging_directory(
                self.repo_root, target
            ) as scope:
                stack, staging, binding = (
                    self._prepare_scoped_regular_file_overlay_candidate(scope)
                )
                with (
                    stack,
                    mock.patch.object(
                        SYNC_MODULE,
                        "_load_regular_file_overlay_noreplace_primitive",
                        side_effect=SYNC_MODULE.SyncError("noreplace unavailable"),
                    ),
                ):
                    SYNC_MODULE._replace_target_with_regular_file_overlays(
                        target,
                        staging,
                        (binding,),
                        staging_scope=scope,
                    )

        self.assertEqual(target.stat().st_ino, old_target_inode)
        self.assertEqual((target / "catalog.json").read_bytes(), b"public\n")

    def test_regular_file_overlay_preserves_verified_recovery_without_path_cleanup(
        self,
    ) -> None:
        target = self._create_regular_file_overlay_target("pinned-recovery")
        labels: list[str] = []
        real_assert_entry = SYNC_MODULE._assert_regular_file_overlay_entry_binding

        def record_entry_binding(*args, label, **kwargs):
            labels.append(label)
            return real_assert_entry(*args, label=label, **kwargs)

        with mock.patch.object(
            SYNC_MODULE.shutil,
            "rmtree",
            side_effect=AssertionError("pathname cleanup must not run"),
        ):
            with SYNC_MODULE._regular_file_overlay_staging_directory(
                self.repo_root, target
            ) as scope:
                scope_path = scope.path
                stack, staging, binding = (
                    self._prepare_scoped_regular_file_overlay_candidate(scope)
                )
                with stack:
                    with mock.patch.object(
                        SYNC_MODULE,
                        "_assert_regular_file_overlay_entry_binding",
                        side_effect=record_entry_binding,
                    ):
                        SYNC_MODULE._replace_target_with_regular_file_overlays(
                            target,
                            staging,
                            (binding,),
                            staging_scope=scope,
                        )

        self.assertTrue(scope_path.is_dir())
        self.assertEqual((target / "catalog.json").read_bytes(), b"private\n")
        recovery = list(scope_path.glob(".codex-private-overlay-backup-*"))
        self.assertEqual(len(recovery), 1)
        self.assertEqual((recovery[0] / "catalog.json").read_bytes(), b"public\n")
        self.assertTrue(
            {
                "prior target before backup move",
                "moved prior target backup",
                "verified recovery backup",
                "recovery backup before handoff",
            }.issubset(labels)
        )

    def test_regular_file_overlay_recovery_root_is_git_ignored(self) -> None:
        ignored = {
            line.strip()
            for line in (REPO_ROOT / ".gitignore")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertIn(".codex-tmp/", ignored)

    def test_regular_file_overlay_recovery_root_has_bounded_entries(self) -> None:
        target = self._create_regular_file_overlay_target("bounded-recovery")
        recovery_root = self.repo_root / SYNC_MODULE.REGULAR_FILE_OVERLAY_RECOVERY_ROOT
        recovery_root.mkdir(parents=True, mode=0o700)
        for index in range(SYNC_MODULE.MAX_REGULAR_FILE_OVERLAY_RECOVERY_PATHS):
            (recovery_root / f"existing-{index:02d}").mkdir(mode=0o700)

        original_inode = target.stat().st_ino
        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "recovery root reached its bounded entry limit",
        ):
            with SYNC_MODULE._regular_file_overlay_staging_directory(
                self.repo_root,
                target,
            ):
                self.fail("bounded recovery root must fail before staging")

        self.assertEqual(target.stat().st_ino, original_inode)
        self.assertEqual((target / "catalog.json").read_bytes(), b"public\n")

    def test_regular_file_overlay_rebound_staging_is_preserved(self) -> None:
        target = self.repo_root / "rebound-cleanup-installed"
        target.mkdir()
        (target / "catalog.json").write_text("public\n", encoding="utf-8")
        saved = self.root / "rebound-original-container"
        replacement_path: Path | None = None

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "staging container.*binding changed",
        ):
            with SYNC_MODULE._regular_file_overlay_staging_directory(
                self.repo_root, target
            ) as scope:
                stack, staging, binding = (
                    self._prepare_scoped_regular_file_overlay_candidate(scope)
                )
                with stack:
                    SYNC_MODULE._replace_target_with_regular_file_overlays(
                        target,
                        staging,
                        (binding,),
                        staging_scope=scope,
                    )
                scope.path.rename(saved)
                scope.path.mkdir(mode=0o700)
                replacement_path = scope.path

        self.assertTrue(saved.is_dir())
        self.assertIsNotNone(replacement_path)
        self.assertTrue(replacement_path.is_dir())

    def test_regular_file_overlay_keyboard_interrupt_restores_prior_target(
        self,
    ) -> None:
        target = self.repo_root / "interrupt-installed"
        target.mkdir()
        (target / "catalog.json").write_text("public\n", encoding="utf-8")
        old_inode = target.stat().st_ino
        real_rename = SYNC_MODULE._rename_regular_file_overlay_noreplace
        calls = 0

        def interrupt_after_backup(*args):
            nonlocal calls
            real_rename(*args)
            calls += 1
            if calls == 1:
                raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            with SYNC_MODULE._regular_file_overlay_staging_directory(
                self.repo_root, target
            ) as scope:
                scope_path = scope.path
                stack, staging, binding = (
                    self._prepare_scoped_regular_file_overlay_candidate(scope)
                )
                with stack:
                    with mock.patch.object(
                        SYNC_MODULE,
                        "_rename_regular_file_overlay_noreplace",
                        side_effect=interrupt_after_backup,
                    ):
                        SYNC_MODULE._replace_target_with_regular_file_overlays(
                            target,
                            staging,
                            (binding,),
                            staging_scope=scope,
                        )

        self.assertEqual(target.stat().st_ino, old_inode)
        self.assertEqual((target / "catalog.json").read_bytes(), b"public\n")
        self.assertTrue(scope_path.is_dir())

    def test_regular_file_overlay_final_rename_interrupt_restores_prior_target(
        self,
    ) -> None:
        target = self._create_regular_file_overlay_target("final-interrupt")
        old_inode = target.stat().st_ino
        real_rename = SYNC_MODULE._rename_regular_file_overlay_noreplace
        calls = 0

        def interrupt_after_final_rename(*args):
            nonlocal calls
            real_rename(*args)
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            with SYNC_MODULE._regular_file_overlay_staging_directory(
                self.repo_root, target
            ) as scope:
                scope_path = scope.path
                stack, staging, binding = (
                    self._prepare_scoped_regular_file_overlay_candidate(scope)
                )
                with (
                    stack,
                    mock.patch.object(
                        SYNC_MODULE,
                        "_rename_regular_file_overlay_noreplace",
                        side_effect=interrupt_after_final_rename,
                    ),
                ):
                    SYNC_MODULE._replace_target_with_regular_file_overlays(
                        target,
                        staging,
                        (binding,),
                        staging_scope=scope,
                    )

        self.assertEqual(calls, 4)
        self.assertEqual(target.stat().st_ino, old_inode)
        self.assertEqual((target / "catalog.json").read_bytes(), b"public\n")
        self.assertEqual(
            (scope_path / "candidate/catalog.json").read_bytes(), b"private\n"
        )

    def test_regular_file_overlay_enforces_size_limit(self) -> None:
        source = self.source_root / "example-repo" / "skill"
        source.mkdir(parents=True)
        (source / "catalog.json").write_text("public\n", encoding="utf-8")
        private_catalog = self.repo_root / "private" / "catalog.json"
        private_catalog.parent.mkdir()
        private_catalog.write_bytes(
            b"x" * (SYNC_MODULE.MAX_REGULAR_FILE_OVERLAY_BYTES + 1)
        )
        rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/example"),
            regular_file_overlays=(
                SYNC_MODULE.RegularFileOverlay(
                    Path("private/catalog.json"),
                    Path("catalog.json"),
                ),
            ),
        )

        with self.assertRaisesRegex(SYNC_MODULE.SyncError, "exceeds 65536 bytes"):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_review_sync_rule_wholesale_replaces_catalog_bytes(self) -> None:
        rule = next(
            rule
            for rule in SYNC_MODULE.SYNC_RULES
            if rule.target == SYNC_MODULE.CANONICAL_REVIEW_TARGET
        )
        self.assertEqual(
            rule.regular_file_overlays,
            (
                SYNC_MODULE.RegularFileOverlay(
                    Path(
                        "personal_codex/private-overrides/"
                        "review-orchestration-playbook/synthetic-token-catalog.json"
                    ),
                    Path("scripts/review_runtime/synthetic-token-catalog.json"),
                ),
            ),
        )

        staging = (self.root / "review-staging").resolve()
        target = staging / rule.regular_file_overlays[0].target
        target.parent.mkdir(parents=True)
        target.write_bytes(b'{"pool":"public"}\n')

        SYNC_MODULE._apply_regular_file_overlays(REPO_ROOT, staging, rule)

        private_catalog = REPO_ROOT / rule.regular_file_overlays[0].source
        private_catalog_stat = private_catalog.stat()
        self.assertEqual(private_catalog_stat.st_uid, os.getuid())
        self.assertFalse(private_catalog_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
        self.assertTrue(
            hmac.compare_digest(target.read_bytes(), private_catalog.read_bytes()),
            "staged catalog differs from the private override source",
        )
        generated_catalog = (
            REPO_ROOT / rule.target / rule.regular_file_overlays[0].target
        )
        self.assertTrue(generated_catalog.is_file())
        self.assertEqual(
            stat.S_IMODE(target.stat().st_mode),
            SYNC_MODULE.REGULAR_FILE_OVERLAY_TARGET_MODE,
        )
        self.assertEqual(
            stat.S_IMODE(generated_catalog.stat().st_mode),
            SYNC_MODULE.REGULAR_FILE_OVERLAY_TARGET_MODE,
        )
        self.assertTrue(
            hmac.compare_digest(
                generated_catalog.read_bytes(),
                private_catalog.read_bytes(),
            ),
            "generated catalog differs from the private override source",
        )

    def test_private_synthetic_token_catalog_contract(self) -> None:
        catalog_path = (
            REPO_ROOT
            / "personal_codex"
            / "private-overrides"
            / "review-orchestration-playbook"
            / "synthetic-token-catalog.json"
        )
        catalog_bytes = catalog_path.read_bytes()
        raw_catalog = json.loads(catalog_bytes)
        parser = load_private_review_synthetic_tokens()
        self.assertEqual(parser.MAX_CATALOG_BYTES, 64 * 1024)
        self.assertEqual(
            SYNC_MODULE.MAX_REGULAR_FILE_OVERLAY_BYTES,
            parser.MAX_CATALOG_BYTES,
        )
        self.assertLessEqual(len(catalog_bytes), parser.MAX_CATALOG_BYTES)
        securely_read = parser._read_catalog_file(catalog_path)
        self.assertTrue(
            hmac.compare_digest(securely_read, catalog_bytes),
            "secure catalog read changed catalog bytes",
        )
        catalog = parser.parse_catalog_bytes(catalog_bytes)

        self.assertEqual(catalog.schema_version, 1)
        self.assertEqual(catalog.pool_version, "joey-private-v1")
        expected_authoring = {
            "access-a": ("access", "active"),
            "access-b": ("access", "active"),
            "access-expired": ("access", "expired"),
            "refresh-a": ("refresh", "active"),
            "refresh-b": ("refresh", "active"),
            "refresh-consumed": ("refresh", "consumed"),
            "id-a": ("id", "active"),
            "id-b": ("id", "active"),
            "api-key-a": ("api-key", "active"),
            "bearer-a": ("bearer", "active"),
        }
        expected_authoring_digests = {
            "access-a": "58daf468f4bf8efe2ae8dc70cc7f560986849e7ae12d5f37b6ff384173660949",
            "access-b": "2bb253074303e17640f50112e193b6785528316cb247aad010282d7fc72af278",
            "access-expired": "bce04e6a1f6bc2c3359fe4132bd290863ba7fd03559842c4b0b9daa7b5663ab4",
            "refresh-a": "c28443d3517b1a1c7f838da8ae2c422c6cb9eca041679faebb2ecf2e8105e2cd",
            "refresh-b": "7f1fc893d30288dc8a8c31e81e3c104d1a00fb5a63cb4f8c78edfa5eb9f393e7",
            "refresh-consumed": "b0ba4734994dcb74e17a490c4e1cf8182ebb4a3ab9ffa8a239087a80b9d163f2",
            "id-a": "e56c3e8a834e46c7a6de2292ab026d113bf76d496c20eb5f926fbbe031351be8",
            "id-b": "635e5d26d428b4d6114e5aeb248f11315755ebe14f847ea3963941326569c293",
            "api-key-a": "0ac4cac80da9258c6db057fcf2f82c450c128631e6c306c82923eb2388955e38",
            "bearer-a": "6baba51bd42263562f0fb352b1d180fedf4609528935a9437c7144517f48bd15",
        }
        authoring = {token.identifier: token for token in catalog.authoring_tokens}
        self.assertEqual(set(authoring), set(expected_authoring))
        self.assertEqual(
            {
                identifier: (token.role, token.state)
                for identifier, token in authoring.items()
            },
            expected_authoring,
        )
        self.assertEqual(
            {identifier: token.value_sha256 for identifier, token in authoring.items()},
            expected_authoring_digests,
        )
        self.assertEqual(
            {token.rule for token in catalog.authoring_tokens},
            {"generic-secret-assignment"},
        )

        exemptions = {
            exemption.identifier: exemption for exemption in catalog.legacy_exemptions
        }
        pat_id = "codex-workflow-hygiene-session-retrospective-github-pat-v1"
        jwt_id = "codex-workflow-hygiene-jwt"
        portable_id = "portable-codex-runtime-master-generic-fixtures-v1"
        self.assertEqual(set(exemptions), {pat_id, jwt_id, portable_id})
        pat = exemptions[pat_id]
        jwt = exemptions[jwt_id]
        portable = exemptions[portable_id]
        self.assertEqual(pat.repository, "Joey-Tools/codex-workflow-hygiene")
        self.assertEqual(jwt.repository, "Joey-Tools/codex-workflow-hygiene")
        self.assertEqual(portable.repository, "cha-op/portable-codex-runtime")
        self.assertEqual(
            pat.verified_master_tip, "95befb966cd93e0161ecb45099c124eac56cb52f"
        )
        self.assertEqual(
            jwt.verified_master_tip, "95befb966cd93e0161ecb45099c124eac56cb52f"
        )
        self.assertEqual(
            portable.verified_master_tip,
            "83542fa2a29661c1422c108887bc13cb5bddd7eb",
        )
        self.assertEqual(len(pat.values), 1)
        self.assertEqual(len(jwt.values), 1)
        self.assertEqual(len(portable.values), 16)
        self.assertEqual(sum(token.source_occurrences for token in pat.values), 1)
        self.assertEqual(
            [
                (token.identifier, token.rule, token.source_occurrences)
                for token in jwt.values
            ],
            [("session-retrospective-redaction-jwt", "jwt", 1)],
        )
        expected_portable_counts = {
            "portable-runtime-legacy-v1-001": 1,
            "portable-runtime-legacy-v1-002": 2,
            "portable-runtime-legacy-v1-003": 7,
            "portable-runtime-legacy-v1-004": 1,
            "portable-runtime-legacy-v1-007": 1,
            "portable-runtime-legacy-v1-012": 6,
            "portable-runtime-legacy-v1-013": 1,
            "portable-runtime-legacy-v1-015": 1,
            "portable-runtime-legacy-v1-016": 1,
            "portable-runtime-legacy-v1-017": 2,
            "portable-runtime-legacy-v1-019": 2,
            "portable-runtime-legacy-v1-020": 2,
            "portable-runtime-legacy-v1-021": 2,
            "portable-runtime-legacy-v1-022": 3,
            "portable-runtime-legacy-v1-023": 3,
            "portable-runtime-legacy-v1-025": 2,
        }
        actual_portable_counts = {
            token.identifier: token.source_occurrences for token in portable.values
        }
        self.assertEqual(actual_portable_counts, expected_portable_counts)
        self.assertEqual(sum(expected_portable_counts.values()), 37)
        self.assertTrue(
            {
                "portable-runtime-legacy-v1-005",
                "portable-runtime-legacy-v1-006",
                "portable-runtime-legacy-v1-008",
                "portable-runtime-legacy-v1-009",
                "portable-runtime-legacy-v1-010",
                "portable-runtime-legacy-v1-011",
                "portable-runtime-legacy-v1-014",
                "portable-runtime-legacy-v1-018",
                "portable-runtime-legacy-v1-024",
            }.isdisjoint(actual_portable_counts)
        )
        self.assertEqual({token.rule for token in pat.values}, {"github-token"})
        self.assertEqual({token.rule for token in jwt.values}, {"jwt"})
        self.assertEqual(
            {token.rule for token in portable.values},
            {"generic-secret-assignment"},
        )
        self.assertEqual(
            sum(len(exemption.values) for exemption in catalog.legacy_exemptions),
            18,
        )
        self.assertEqual(
            sum(
                token.source_occurrences
                for exemption in catalog.legacy_exemptions
                for token in exemption.values
            ),
            39,
        )

        raw_exemptions = {
            exemption["id"]: exemption for exemption in raw_catalog["legacy_exemptions"]
        }
        expected_value_fields = {
            "id",
            "rule",
            "value_base64",
            "containing_commit",
            "source_occurrences",
        }
        for exemption_id, raw_exemption in raw_exemptions.items():
            for index, raw_token in enumerate(raw_exemption["values"]):
                self.assertEqual(
                    set(raw_token),
                    expected_value_fields,
                    f"invalid legacy fields for {exemption_id} value index {index}",
                )

        all_identifiers = [token.identifier for token in catalog.authoring_tokens]
        all_identifiers.extend(exemptions)
        all_identifiers.extend(
            token.identifier
            for exemption in catalog.legacy_exemptions
            for token in exemption.values
        )
        self.assertEqual(len(all_identifiers), len(set(all_identifiers)))

        authoring_digests = {token.value_sha256 for token in catalog.authoring_tokens}
        legacy_tokens = [
            (exemption.identifier, token)
            for exemption in catalog.legacy_exemptions
            for token in exemption.values
        ]
        legacy_digests = {token.value_sha256 for _, token in legacy_tokens}
        self.assertEqual(len(legacy_digests), len(legacy_tokens))
        self.assertTrue(authoring_digests.isdisjoint(legacy_digests))
        for exemption_id, token in legacy_tokens:
            self.assertRegex(token.value_sha256, r"\A[0-9a-f]{64}\Z")
            self.assertGreater(token.value_length, 0)
            self.assertRegex(token.containing_commit, r"\A[0-9a-f]{40}\Z")
            self.assertGreater(
                token.source_occurrences,
                0,
                f"invalid source count for {exemption_id}/{token.identifier}",
            )

        exact_values = [
            ("authoring", token.identifier, token.value)
            for token in catalog.authoring_tokens
        ]
        exact_values.extend(
            (exemption_id, token.identifier, token.value)
            for exemption_id, token in legacy_tokens
        )
        overlaps: set[tuple[str, str]] = set()
        for index, (envelope, identifier, value) in enumerate(exact_values):
            for other_envelope, other_id, other_value in exact_values[index + 1 :]:
                if value in other_value or other_value in value:
                    pair = tuple(sorted((identifier, other_id)))
                    overlaps.add(pair)
                    self.assertEqual(
                        envelope,
                        other_envelope,
                        f"cross-envelope exact-value overlap for {pair}",
                    )
                    self.assertNotEqual(envelope, "authoring")
        self.assertEqual(
            overlaps,
            {
                ("portable-runtime-legacy-v1-003", "portable-runtime-legacy-v1-023"),
                ("portable-runtime-legacy-v1-012", "portable-runtime-legacy-v1-013"),
                ("portable-runtime-legacy-v1-012", "portable-runtime-legacy-v1-015"),
                ("portable-runtime-legacy-v1-012", "portable-runtime-legacy-v1-016"),
            },
        )

        storage_values = [
            (token.identifier, base64.b64encode(token.value))
            for _, token in legacy_tokens
        ]
        metadata = {
            catalog.pool_version,
            *(token.identifier for token in catalog.authoring_tokens),
            *(token.role for token in catalog.authoring_tokens),
            *(token.state for token in catalog.authoring_tokens),
            *(token.rule for token in catalog.authoring_tokens),
            *(token.value_sha256 for token in catalog.authoring_tokens),
            *(exemption.identifier for exemption in catalog.legacy_exemptions),
            *(exemption.repository for exemption in catalog.legacy_exemptions),
            *(exemption.verified_master_tip for exemption in catalog.legacy_exemptions),
            *(exemption.match for exemption in catalog.legacy_exemptions),
            *(token.identifier for _, token in legacy_tokens),
            *(token.rule for _, token in legacy_tokens),
            *(token.value_sha256 for _, token in legacy_tokens),
            *(token.containing_commit for _, token in legacy_tokens),
        }
        encoded_metadata = tuple(item.encode("ascii") for item in metadata)
        for identifier, storage_value in storage_values:
            self.assertFalse(
                any(storage_value in item for item in encoded_metadata),
                f"legacy storage encoding overlaps public metadata for {identifier}",
            )
            for _, other_id, raw_value in exact_values:
                self.assertFalse(
                    storage_value in raw_value or raw_value in storage_value,
                    "legacy storage encoding overlaps exact value for "
                    f"{identifier}/{other_id}",
                )
        for index, (identifier, storage_value) in enumerate(storage_values):
            for other_id, other_storage in storage_values[index + 1 :]:
                self.assertFalse(
                    storage_value in other_storage or other_storage in storage_value,
                    f"legacy storage encodings overlap for {identifier}/{other_id}",
                )

    def test_public_catalog_parser_rejects_global_conflicts_and_oversize_file(
        self,
    ) -> None:
        parser = load_private_review_synthetic_tokens()

        def fixture(
            *, authoring_value: str, legacy_value: str, legacy_id: str
        ) -> bytes:
            return (
                json.dumps(
                    {
                        "schema_version": 1,
                        "authoring_pool": {
                            "version": "private-test-v1",
                            "tokens": [
                                {
                                    "id": "author-a",
                                    "role": "access",
                                    "state": "active",
                                    "rule": "generic-secret-assignment",
                                    "value": authoring_value,
                                }
                            ],
                        },
                        "legacy_exemptions": [
                            {
                                "id": "legacy-envelope",
                                "repository": "Example/example",
                                "verified_master_tip": "1" * 40,
                                "match": "non-increasing-global-count",
                                "values": [
                                    {
                                        "id": legacy_id,
                                        "rule": "generic-secret-assignment",
                                        "value_base64": base64.b64encode(
                                            legacy_value.encode("ascii")
                                        ).decode("ascii"),
                                        "containing_commit": "1" * 40,
                                        "source_occurrences": 1,
                                    }
                                ],
                            }
                        ],
                    },
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")

        baseline_authoring = "synthetic_fixture_alpha_123"
        baseline_legacy = "legacy_fixture_bravo_456"
        storage_legacy = "legacy_storage_fixture_123"
        storage_authoring = base64.b64encode(storage_legacy.encode("ascii")).decode(
            "ascii"
        )
        cases = (
            (
                "duplicate-id",
                fixture(
                    authoring_value=baseline_authoring,
                    legacy_value=baseline_legacy,
                    legacy_id="author-a",
                ),
                "duplicate id",
            ),
            (
                "duplicate-value",
                fixture(
                    authoring_value=baseline_authoring,
                    legacy_value=baseline_authoring,
                    legacy_id="legacy-a",
                ),
                "duplicate value",
            ),
            (
                "substring-value",
                fixture(
                    authoring_value=baseline_authoring,
                    legacy_value=f"{baseline_authoring}_suffix",
                    legacy_id="legacy-a",
                ),
                "overlapping values",
            ),
            (
                "storage-value",
                fixture(
                    authoring_value=storage_authoring,
                    legacy_value=storage_legacy,
                    legacy_id="legacy-a",
                ),
                "storage encoding overlaps an exact value",
            ),
        )
        for label, payload, error_pattern in cases:
            with self.subTest(case=label):
                with self.assertRaisesRegex(parser.ReviewError, error_pattern):
                    parser.parse_catalog_bytes(payload)

        oversized = self.root / "oversized-catalog.json"
        oversized.write_bytes(b" " * (parser.MAX_CATALOG_BYTES + 1))
        oversized.chmod(0o600)
        with self.assertRaisesRegex(parser.ReviewError, "exceeds the size limit"):
            parser._read_catalog_file(oversized)

    def test_synthetic_token_skill_is_installed_and_routed(self) -> None:
        skill_target = Path("personal_codex/skills/synthetic-token-fixtures")
        rules = [rule for rule in SYNC_MODULE.SYNC_RULES if rule.target == skill_target]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].repo, "codex-review-workflows")
        self.assertEqual(rules[0].source, Path("skills/synthetic-token-fixtures"))
        self.assertFalse(rules[0].regular_file_overlays)

        manifest = json.loads(
            (REPO_ROOT / "personal_codex" / "private-sync-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        links = [
            link
            for link in manifest["links"]
            if link["target"] == "skills/synthetic-token-fixtures"
        ]
        self.assertEqual(
            links,
            [
                {
                    "source": "personal_codex/skills/synthetic-token-fixtures",
                    "target": "skills/synthetic-token-fixtures",
                    "kind": "skill",
                }
            ],
        )

        agents_lines = (
            (REPO_ROOT / "personal_codex" / "AGENTS.md")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        trigger = (
            "- Use `$synthetic-token-fixtures` when authoring or migrating "
            "credential-shaped source and test fixtures that must pass the review "
            "helper's exact synthetic-token policy."
        )
        self.assertEqual(agents_lines.count(trigger), 1)

    def test_scheduled_workflow_checks_out_all_sync_rule_repos(self) -> None:
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "scheduled-sync-release.yml"
        ).read_text(encoding="utf-8")
        checked_out_repos = set(
            re.findall(r"repository: Joey-Tools/([-a-z0-9]+)", workflow)
        )
        checked_out_paths = set(re.findall(r"path: \.source/([-a-z0-9]+)", workflow))
        sync_rule_repos = {rule.repo for rule in SYNC_MODULE.SYNC_RULES}

        self.assertEqual(checked_out_repos, sync_rule_repos)
        self.assertEqual(checked_out_paths, sync_rule_repos)

    def test_ci_validates_review_helper_on_minimum_python_across_platforms(
        self,
    ) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("\n  platform_tests:\n", workflow)
        self.assertIn("name: platform-tests (${{ matrix.os }})", workflow)
        self.assertIn("ubuntu-latest", workflow)
        self.assertIn("macos-latest", workflow)
        self.assertIn('python-version: "3.10"', workflow)
        self.assertIn("tomli==2.2.1", workflow)
        self.assertIn("review-orchestration-playbook/tests", workflow)
        self.assertIn(
            "-fsyntax-only personal_codex/skills/review-orchestration-playbook/"
            "scripts/review_runtime/claude_linux_launcher.c",
            workflow,
        )
        self.assertIn(
            "python3 -m unittest -v personal_codex/skills/"
            "review-orchestration-playbook/tests/test_claude_linux.py",
            workflow,
        )
        self.assertNotIn("when present", workflow)
        self.assertNotIn('if [[ -f "$launcher" ]]', workflow)
        self.assertIn("\n  test:\n", workflow)
        self.assertIn("\n    name: test\n", workflow)
        self.assertIn("if: ${{ always() }}", workflow)
        self.assertIn("needs: platform_tests", workflow)
        self.assertIn(
            "PLATFORM_TESTS_RESULT: ${{ needs.platform_tests.result }}",
            workflow,
        )
        self.assertIn('test "$PLATFORM_TESTS_RESULT" = "success"', workflow)

    def test_manifest_canonical_skills_are_backed_by_sync_rules(self) -> None:
        manifest = json.loads(
            (REPO_ROOT / "personal_codex" / "private-sync-manifest.json").read_text(
                encoding="utf-8"
            )
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
        retired_targets = {str(path) for path in SYNC_MODULE.RETIRED_TARGETS}

        self.assertEqual(
            manifest_sources - private_only_sources, manifest_sources & sync_targets
        )
        self.assertTrue(manifest_sources.isdisjoint(retired_targets))
        self.assertTrue(sync_targets.isdisjoint(retired_targets))
        self.assertIn("personal_codex/skills/bounded-command-output", manifest_sources)
        self.assertIn("skills/bounded-command-output", manifest_targets)
        self.assertIn("personal_codex/skills/bounded-command-output", sync_targets)
        self.assertIn(
            "personal_codex/skills/codex-session-retrospective", manifest_sources
        )
        self.assertIn("skills/codex-session-retrospective", manifest_targets)
        self.assertIn("personal_codex/skills/codex-session-retrospective", sync_targets)
        self.assertIn(
            "personal_codex/skills/synthetic-token-fixtures", manifest_sources
        )
        self.assertIn("skills/synthetic-token-fixtures", manifest_targets)
        self.assertIn("personal_codex/skills/synthetic-token-fixtures", sync_targets)

    def test_bounded_command_output_is_installed_and_routed(self) -> None:
        agents = (REPO_ROOT / "personal_codex" / "AGENTS.md").read_text(
            encoding="utf-8"
        )
        skill_root = REPO_ROOT / "personal_codex" / "skills" / "bounded-command-output"
        skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        interface = (skill_root / "agents" / "openai.yaml").read_text(encoding="utf-8")

        self.assertIn("Use `$bounded-command-output` before broad searches", agents)
        self.assertIn("apply it alongside the task's domain skill", agents)
        self.assertIn("spinner-heavy container builds", skill)
        self.assertIn("allow_implicit_invocation: true", interface)

    def test_scheduled_workflow_opens_pr_for_sync_changes(self) -> None:
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "scheduled-sync-release.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("pull-requests: write", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("PRIVATE_OVERLAY_SYNC_PR_TOKEN", workflow)
        self.assertIn(
            'git remote set-url origin "https://x-access-token:${SYNC_PR_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"',
            workflow,
        )
        self.assertIn("gh pr create", workflow)
        self.assertIn("gh pr edit", workflow)
        self.assertIn('label="codex-automation"', workflow)
        self.assertIn(
            'gh api --method GET "repos/$GITHUB_REPOSITORY/labels/$label"', workflow
        )
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
        self.assertIn(
            'pr_head_sha="$(gh pr view "$pr_url" --json headRefOid --jq \'.headRefOid\')"',
            workflow,
        )
        self.assertIn(
            'pr_head_ref="$(gh pr view "$pr_url" --json headRefName --jq \'.headRefName\')"',
            workflow,
        )
        self.assertIn(
            'pr_base_ref="$(gh pr view "$pr_url" --json baseRefName --jq \'.baseRefName\')"',
            workflow,
        )
        self.assertIn(
            'gh pr merge "$pr_url" --auto --squash --delete-branch --match-head-commit "$head_sha"',
            workflow,
        )
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
        self.assertNotIn(
            "git diff --cached --quiet FETCH_HEAD -- scripts personal_codex .agents",
            workflow,
        )
        self.assertIn(
            "already matches the full generated overlay tree and contains", workflow
        )

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

    def test_release_workflow_runs_required_pr_check_for_all_pull_requests(
        self,
    ) -> None:
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
        with mock.patch.object(
            RELEASE_MODULE, "recent_complete_releases", return_value=[]
        ):
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
                    {
                        "name": f"personal-codex-{complete_sha}.tar.gz",
                        "state": "uploaded",
                    },
                    {
                        "name": f"personal-codex-{complete_sha}.sha256",
                        "state": "uploaded",
                    },
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
                "assets": [
                    {
                        "name": f"personal-codex-{missing_sha}.tar.gz",
                        "state": "uploaded",
                    }
                ],
            },
            {
                "tag_name": f"personal-codex-20260522-110000-{scheduled_sha[:7]}",
                "target_commitish": scheduled_sha,
                "published_at": "2026-05-22T11:00:00Z",
                "body": "source_event=schedule",
                "draft": False,
                "assets": [
                    {
                        "name": f"personal-codex-{scheduled_sha}.tar.gz",
                        "state": "uploaded",
                    },
                    {
                        "name": f"personal-codex-{scheduled_sha}.sha256",
                        "state": "uploaded",
                    },
                ],
            },
        ]
        with mock.patch.object(
            RELEASE_MODULE, "iter_releases", return_value=iter(releases)
        ):
            recent = RELEASE_MODULE.recent_complete_releases(
                repo="owner/repo",
                now=now,
                cooldown_seconds=8 * 60 * 60,
                event="schedule",
            )

        self.assertEqual(
            [release["target_commitish"] for release in recent], [complete_sha]
        )

        with mock.patch.object(
            RELEASE_MODULE, "iter_releases", return_value=iter(releases)
        ):
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
        with tempfile.TemporaryDirectory(
            prefix="private-overlay-release."
        ) as temp_dir_raw:
            dist = Path(temp_dir_raw)
            sha = "a" * 40
            (dist / f"personal-codex-{sha}.tar.gz").write_bytes(b"archive")
            (dist / f"personal-codex-{sha}.sha256").write_text(
                "checksum\n", encoding="utf-8"
            )
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
            with mock.patch.object(
                RELEASE_MODULE, "iter_releases", return_value=iter([release])
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    RELEASE_MODULE.publish_release("owner/repo", sha, dist)

    def test_publish_existing_draft_updates_source_event(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="private-overlay-release."
        ) as temp_dir_raw:
            dist = Path(temp_dir_raw)
            sha = "a" * 40
            (dist / f"personal-codex-{sha}.tar.gz").write_bytes(b"archive")
            (dist / f"personal-codex-{sha}.sha256").write_text(
                "checksum\n", encoding="utf-8"
            )
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

            def fake_request_json(
                url: str, *, method: str = "GET", payload=None, token=None
            ):
                requests.append({"url": url, "method": method, "payload": payload})
                return dict(release, body=payload["body"], draft=payload["draft"])

            with mock.patch.object(
                RELEASE_MODULE, "iter_releases", return_value=iter([release])
            ):
                with mock.patch.object(
                    RELEASE_MODULE, "request_json", fake_request_json
                ):
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
        with tempfile.TemporaryDirectory(
            prefix="private-overlay-release."
        ) as temp_dir_raw:
            dist = Path(temp_dir_raw)
            sha = "a" * 40
            (dist / f"personal-codex-{sha}.tar.gz").write_bytes(b"archive")
            (dist / f"personal-codex-{sha}.sha256").write_text(
                "checksum\n", encoding="utf-8"
            )
            release = {
                "id": 10,
                "tag_name": f"personal-codex-20260522-100000-{sha[:7]}",
                "target_commitish": sha,
                "body": "source_event=workflow_dispatch",
                "draft": True,
                "assets": [
                    {
                        "id": 11,
                        "name": f"personal-codex-{sha}.tar.gz",
                        "state": "uploaded",
                    },
                    {
                        "id": 12,
                        "name": f"personal-codex-{sha}.sha256",
                        "state": "starter",
                    },
                ],
            }
            requests: list[dict[str, object]] = []
            uploads: list[str] = []

            def fake_request_json(
                url: str, *, method: str = "GET", payload=None, token=None
            ):
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

            with mock.patch.object(
                RELEASE_MODULE, "iter_releases", return_value=iter([release])
            ):
                with mock.patch.object(
                    RELEASE_MODULE, "request_json", fake_request_json
                ):
                    with mock.patch.object(RELEASE_MODULE, "urlopen", fake_urlopen):
                        with mock.patch.object(
                            RELEASE_MODULE, "_github_token", return_value="token"
                        ):
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

        with mock.patch.object(
            RELEASE_MODULE, "iter_releases", return_value=iter([complete_release])
        ):
            self.assertTrue(RELEASE_MODULE.release_complete("owner/repo", sha))
        with mock.patch.object(
            RELEASE_MODULE, "iter_releases", return_value=iter([draft_release])
        ):
            self.assertFalse(RELEASE_MODULE.release_complete("owner/repo", sha))
        with mock.patch.object(
            RELEASE_MODULE, "iter_releases", return_value=iter([missing_asset_release])
        ):
            self.assertFalse(RELEASE_MODULE.release_complete("owner/repo", sha))
        with mock.patch.object(
            RELEASE_MODULE,
            "iter_releases",
            return_value=iter([incomplete_asset_release]),
        ):
            self.assertFalse(RELEASE_MODULE.release_complete("owner/repo", sha))


if __name__ == "__main__":
    unittest.main()
