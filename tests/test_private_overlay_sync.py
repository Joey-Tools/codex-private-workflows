from __future__ import annotations

import base64
import datetime as dt
import contextlib
import errno
import hashlib
import hmac
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync_private_overlay_sources.py"
RELEASE_SCRIPT = REPO_ROOT / "scripts" / "private_overlay_release.py"
RUNTIME_SCRIPT = REPO_ROOT / "scripts" / "codex_personal_sync.py"
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
RUNTIME_MODULE = load_module("codex_personal_sync_private_overlay_sync", RUNTIME_SCRIPT)

# isolated_review synthetic-token IDs: access-a and access-b.
GITHUB_TOKEN_FIXTURE = "codex_synth_v1_access_a"
IMMUTABLE_RELEASES_READ_TOKEN_FIXTURE = "codex_synth_v1_access_b"


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
        self.external_prepared_parent = self.root / "external-prepared"
        self.external_prepared_parent.mkdir(mode=0o700)
        self.external_prepared_parent_patcher = mock.patch.object(
            SYNC_MODULE,
            "_external_prepared_regular_file_overlay_parent_path",
            return_value=self.external_prepared_parent,
        )
        self.external_prepared_parent_patcher.start()

    def tearDown(self) -> None:
        self.external_prepared_parent_patcher.stop()
        self.tmpdir.cleanup()

    @staticmethod
    def _private_release_expectation(
        *,
        base_release_repo: str = "Joey-Tools/codex-toolbox",
    ):
        manifest_data = SimpleNamespace(
            entries=[mock.Mock(owner="private", target=Path("skills/private"))],
            base_release_repo=base_release_repo,
        )
        return (({}, manifest_data, "digest"), (1, 2))

    def test_verify_package_uses_bound_read_and_temporary_workspaces(self) -> None:
        sha = "1" * 40
        dist = self.root / "dist"
        existing_extract = dist / "extract"
        existing_extract.mkdir(parents=True)
        sentinel = existing_extract / "sentinel.txt"
        sentinel.write_text("keep\n", encoding="utf-8")
        destinations: list[Path] = []
        archive_workspaces: list[object] = []
        read_workspaces: list[object] = []
        expectation = self._private_release_expectation()

        def verify_and_extract(
            archive_path: Path,
            checksum_path: Path,
            destination: Path,
            *,
            workspace,
            read_workspace,
        ):
            self.assertEqual(
                archive_path,
                dist / f"personal-codex-{sha}.tar.gz",
            )
            self.assertEqual(
                checksum_path,
                dist / f"personal-codex-{sha}.sha256",
            )
            self.assertEqual(workspace.path, destination.parent)
            self.assertEqual(
                read_workspace.path,
                Path(os.path.abspath(dist)),
            )
            self.assertNotEqual(workspace.fd, read_workspace.fd)
            for capability in (workspace, read_workspace):
                metadata = os.fstat(capability.fd)
                self.assertTrue(stat.S_ISDIR(metadata.st_mode))
                self.assertEqual(
                    (metadata.st_dev, metadata.st_ino),
                    capability.identity,
                )
            destinations.append(destination)
            archive_workspaces.append(workspace)
            read_workspaces.append(read_workspace)
            return destination / f"personal-codex-{sha}", expectation

        with (
            mock.patch.object(
                RELEASE_MODULE,
                "_load_sync_module",
                return_value=RUNTIME_MODULE,
            ),
            mock.patch.object(
                RUNTIME_MODULE,
                "verify_and_extract_archive",
                side_effect=verify_and_extract,
            ),
        ):
            RELEASE_MODULE.verify_package(self.repo_root, sha, dist)
            RELEASE_MODULE.verify_package(self.repo_root, sha, dist)

        self.assertEqual(len(destinations), 2)
        self.assertNotEqual(destinations[0], destinations[1])
        self.assertTrue(all(not destination.parent.exists() for destination in destinations))
        for capability in [*archive_workspaces, *read_workspaces]:
            with self.assertRaises(OSError) as closed:
                os.fstat(capability.fd)
            self.assertEqual(closed.exception.errno, errno.EBADF)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")

    def test_verify_package_uses_bound_manifest_expectation_not_release_path(
        self,
    ) -> None:
        sha = "6" * 40
        dist = self.root / "dist"
        dist.mkdir()
        expectation = self._private_release_expectation(
            base_release_repo="Attacker/alternate-base",
        )

        def verify_and_extract(
            _archive_path: Path,
            _checksum_path: Path,
            destination: Path,
            *,
            workspace,
            read_workspace,
        ):
            self.assertEqual(workspace.path, destination.parent)
            self.assertEqual(read_workspace.path, Path(os.path.abspath(dist)))
            release_root = destination / f"personal-codex-{sha}"
            manifest_path = (
                release_root / "personal_codex" / "sync-manifest.json"
            )
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "owner": "private",
                        "base_release": {
                            "repo": "Joey-Tools/codex-toolbox"
                        },
                    }
                ),
                encoding="utf-8",
            )
            return release_root, expectation

        with (
            mock.patch.object(
                RELEASE_MODULE,
                "_load_sync_module",
                return_value=RUNTIME_MODULE,
            ),
            mock.patch.object(
                RUNTIME_MODULE,
                "verify_and_extract_archive",
                side_effect=verify_and_extract,
            ),
            mock.patch.object(
                RUNTIME_MODULE,
                "validate_release_tree",
                side_effect=AssertionError("release path must not be reopened"),
            ) as validate_release_tree,
            self.assertRaisesRegex(
                RELEASE_MODULE.ReleaseError,
                "declare the public base release repo",
            ),
        ):
            RELEASE_MODULE.verify_package(self.repo_root, sha, dist)

        validate_release_tree.assert_not_called()

    def test_verify_package_cleanup_error_does_not_mask_primary_error(
        self,
    ) -> None:
        sha = "2" * 40
        dist = self.root / "dist"
        dist.mkdir()
        stderr = io.StringIO()

        with (
            mock.patch.object(
                RELEASE_MODULE,
                "_load_sync_module",
                return_value=RUNTIME_MODULE,
            ),
            mock.patch.object(
                RUNTIME_MODULE,
                "verify_and_extract_archive",
                side_effect=RELEASE_MODULE.ReleaseError(
                    "primary verification failure"
                ),
            ),
            mock.patch.object(
                RUNTIME_MODULE,
                "_cleanup_bound_temporary_archive_workspace",
                side_effect=RUNTIME_MODULE.SyncError("cleanup failure"),
            ),
            contextlib.redirect_stderr(stderr),
            self.assertRaisesRegex(
                RELEASE_MODULE.ReleaseError,
                "primary verification failure",
            ),
        ):
            RELEASE_MODULE.verify_package(self.repo_root, sha, dist)

        self.assertIn("warning: cleanup failure", stderr.getvalue())

    def test_verify_package_rejects_bound_dist_or_ancestor_replacement(
        self,
    ) -> None:
        sha = "5" * 40
        for replacement_kind in ("dist", "ancestor"):
            for asset_role in ("checksum", "archive"):
                with self.subTest(
                    replacement_kind=replacement_kind,
                    asset_role=asset_role,
                ):
                    case_root = (
                        self.root
                        / f"replace-{replacement_kind}-{asset_role}"
                    )
                    self._assert_verify_package_rejects_bound_replacement(
                        case_root,
                        sha,
                        replacement_kind,
                        asset_role,
                    )

    def _assert_verify_package_rejects_bound_replacement(
        self,
        case_root: Path,
        sha: str,
        replacement_kind: str,
        asset_role: str,
    ) -> None:
        ancestor = case_root / "ancestor"
        dist = ancestor / "dist"
        dist.mkdir(parents=True)
        moved = case_root / f"moved-{replacement_kind}"
        replacement_sentinel = dist / "replacement.txt"
        archive_path = dist / f"personal-codex-{sha}.tar.gz"
        checksum_path = dist / f"personal-codex-{sha}.sha256"
        archive_payload = b"archive payload"
        archive_path.write_bytes(archive_payload)
        checksum_path.write_text(
            f"{RUNTIME_MODULE.hashlib.sha256(archive_payload).hexdigest()}  "
            f"{archive_path.name}\n",
            encoding="utf-8",
        )
        captured_read_workspace: list[object] = []
        replaced = False
        trigger_description = (
            "checksum file"
            if asset_role == "checksum"
            else "compressed archive"
        )
        real_open_bounded_regular_file = (
            RUNTIME_MODULE._open_bounded_regular_file
        )

        def open_after_replacement(
            path: Path,
            *,
            maximum_bytes: int,
            description: str,
            workspace=None,
        ):
            nonlocal replaced
            if not replaced and description == trigger_description:
                replaced = True
                captured_read_workspace.append(workspace)
                if replacement_kind == "dist":
                    dist.rename(moved)
                    dist.mkdir()
                else:
                    ancestor.rename(moved)
                    dist.mkdir(parents=True)
                replacement_sentinel.write_text(
                    "replacement\n",
                    encoding="utf-8",
                )
            return real_open_bounded_regular_file(
                path,
                maximum_bytes=maximum_bytes,
                description=description,
                workspace=workspace,
            )

        with (
            mock.patch.object(
                RELEASE_MODULE,
                "_load_sync_module",
                return_value=RUNTIME_MODULE,
            ),
            mock.patch.object(
                RUNTIME_MODULE,
                "_open_bounded_regular_file",
                side_effect=open_after_replacement,
            ),
            self.assertRaisesRegex(
                RUNTIME_MODULE.SyncError,
                "archive workspace binding changed",
            ),
        ):
            RELEASE_MODULE.verify_package(self.repo_root, sha, dist)

        self.assertTrue(replaced)
        self.assertEqual(len(captured_read_workspace), 1)
        with self.assertRaises(OSError) as closed:
            os.fstat(captured_read_workspace[0].fd)
        self.assertEqual(closed.exception.errno, errno.EBADF)
        self.assertTrue(moved.is_dir())
        self.assertEqual(
            replacement_sentinel.read_text(encoding="utf-8"),
            "replacement\n",
        )

    def test_verify_package_dist_close_failure_preserves_primary(self) -> None:
        sha = "6" * 40
        dist = self.root / "dist-close-primary"
        dist.mkdir()
        captured_fd = -1
        close_failed = False
        real_close = RUNTIME_MODULE.os.close
        stderr = io.StringIO()

        def fail_verification(
            _archive_path: Path,
            _checksum_path: Path,
            _destination: Path,
            *,
            workspace,
            read_workspace,
        ):
            nonlocal captured_fd
            self.assertNotEqual(workspace.fd, read_workspace.fd)
            captured_fd = read_workspace.fd
            raise RELEASE_MODULE.ReleaseError("primary verification failure")

        def fail_dist_close(file_descriptor: int) -> None:
            nonlocal close_failed
            if file_descriptor == captured_fd and not close_failed:
                close_failed = True
                raise OSError("simulated dist close failure")
            real_close(file_descriptor)

        try:
            with (
                mock.patch.object(
                    RELEASE_MODULE,
                    "_load_sync_module",
                    return_value=RUNTIME_MODULE,
                ),
                mock.patch.object(
                    RUNTIME_MODULE,
                    "verify_and_extract_archive",
                    side_effect=fail_verification,
                ),
                mock.patch.object(
                    RUNTIME_MODULE.os,
                    "close",
                    side_effect=fail_dist_close,
                ),
                contextlib.redirect_stderr(stderr),
                self.assertRaisesRegex(
                    RELEASE_MODULE.ReleaseError,
                    "primary verification failure",
                ),
            ):
                RELEASE_MODULE.verify_package(self.repo_root, sha, dist)
        finally:
            if captured_fd >= 0:
                real_close(captured_fd)

        self.assertTrue(close_failed)
        self.assertIn(
            "warning: failed to close archive workspace",
            stderr.getvalue(),
        )

    def test_verify_package_rejects_symlinked_archive_or_checksum_asset(
        self,
    ) -> None:
        sha = "7" * 40
        archive_name = f"personal-codex-{sha}.tar.gz"
        checksum_name = f"personal-codex-{sha}.sha256"
        archive_payload = b"not-a-tar-archive"

        for role in ("archive", "checksum"):
            with self.subTest(role=role):
                case_root = self.root / f"symlinked-{role}"
                dist = case_root / "dist"
                outside = case_root / "outside"
                dist.mkdir(parents=True)
                outside.mkdir()
                archive_path = dist / archive_name
                checksum_path = dist / checksum_name
                digest = RUNTIME_MODULE.hashlib.sha256(archive_payload).hexdigest()
                archive_path.write_bytes(archive_payload)
                checksum_path.write_text(
                    f"{digest}  {archive_name}\n",
                    encoding="utf-8",
                )
                unsafe_path = archive_path if role == "archive" else checksum_path
                unsafe_path.unlink()
                outside_path = outside / unsafe_path.name
                if role == "archive":
                    outside_path.write_bytes(archive_payload)
                else:
                    outside_path.write_text(
                        f"{digest}  {archive_name}\n",
                        encoding="utf-8",
                    )
                unsafe_path.symlink_to(outside_path)

                with (
                    mock.patch.object(
                        RELEASE_MODULE,
                        "_load_sync_module",
                        return_value=RUNTIME_MODULE,
                    ),
                    self.assertRaisesRegex(
                        RUNTIME_MODULE.SyncError,
                        "unsafe|non-regular",
                    ),
                ):
                    RELEASE_MODULE.verify_package(self.repo_root, sha, dist)

    @contextlib.contextmanager
    def _regular_file_overlay_staging_directory(self, target: Path):
        with contextlib.ExitStack() as stack:
            repo_binding = SYNC_MODULE._pin_regular_file_overlay_directory(
                stack,
                self.repo_root,
                label="repository root",
            )
            with SYNC_MODULE._regular_file_overlay_staging_directory(
                repo_binding,
                target.relative_to(self.repo_root),
            ) as scope:
                yield scope

    def _prepare_held_regular_file_overlay_target(
        self,
        name: str,
    ):
        target = self._create_regular_file_overlay_target(name)
        staging_parent = self.repo_root / f"{name}-staging"
        staging_parent.mkdir(mode=0o700)
        staging = staging_parent / "candidate"
        staging.mkdir()
        (staging / "catalog.json").write_text("private\n", encoding="utf-8")
        stack = contextlib.ExitStack()
        try:
            staging_root = SYNC_MODULE._pin_regular_file_overlay_directory(
                stack,
                staging,
                label="staged target",
            )
            manifest = SYNC_MODULE._capture_regular_file_overlay_tree_manifest(
                staging_root.descriptor,
                label="test staged target",
            )
            bindings = SYNC_MODULE._pin_regular_file_overlay_targets(
                stack,
                staging,
                staging_root,
                {Path("catalog.json"): b"private\n"},
                manifest,
            )
        except BaseException:
            stack.close()
            raise
        self.assertEqual(len(bindings), 1)
        return stack, target, staging, bindings[0]

    def _create_regular_file_overlay_target(self, name: str) -> Path:
        target = self.repo_root / f"{name}-installed"
        target.mkdir()
        (target / "catalog.json").write_text("public\n", encoding="utf-8")
        return target

    def _regular_file_overlay_manifest_entry_for_file(
        self,
        path: Path,
    ) -> SYNC_MODULE._RegularFileOverlayTreeEntry:
        metadata = path.stat()
        data = path.read_bytes()
        return SYNC_MODULE._RegularFileOverlayTreeEntry(
            relative_parts=(path.name,),
            kind="file",
            identity=SYNC_MODULE._overlay_file_identity(metadata),
            size=len(data),
            sha256=SYNC_MODULE.hashlib.sha256(data).hexdigest(),
        )

    def _prepare_scoped_regular_file_overlay_candidate(
        self,
        scope,
        *,
        extra_files: dict[Path, bytes] | None = None,
    ):
        staging = scope.path / "candidate"
        staging.mkdir()
        (staging / "catalog.json").write_text("private\n", encoding="utf-8")
        for relative, data in (extra_files or {}).items():
            path = staging / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        stack = contextlib.ExitStack()
        try:
            staging_root = SYNC_MODULE._pin_regular_file_overlay_child_directory(
                stack,
                scope.container,
                staging.name,
                path=staging,
                label="staged target",
            )
            manifest = SYNC_MODULE._capture_regular_file_overlay_tree_manifest(
                staging_root.descriptor,
                label="test staged target",
            )
            bindings = SYNC_MODULE._pin_regular_file_overlay_targets(
                stack,
                staging,
                staging_root,
                {Path("catalog.json"): b"private\n"},
                manifest,
            )
        except BaseException:
            stack.close()
            raise
        self.assertEqual(len(bindings), 1)
        return stack, staging, bindings[0]

    def _create_canonical_regular_file_overlay_rule(self):
        source = self.source_root / "canonical-repo" / "skill"
        for relative in SYNC_MODULE.CANONICAL_REVIEW_REQUIRED_FILES:
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("public\n", encoding="utf-8")
        private_catalog = self.repo_root / "private" / "catalog.json"
        private_catalog.parent.mkdir()
        private_catalog.write_text("private\n", encoding="utf-8")
        target = self.repo_root / SYNC_MODULE.CANONICAL_REVIEW_TARGET
        target.mkdir(parents=True)
        (target / "old-marker").write_text("old\n", encoding="utf-8")
        rule = SYNC_MODULE.SyncRule(
            repo="canonical-repo",
            source=Path("skill"),
            target=SYNC_MODULE.CANONICAL_REVIEW_TARGET,
            regular_file_overlays=(
                SYNC_MODULE.RegularFileOverlay(
                    source=Path("private/catalog.json"),
                    target=Path("scripts/review_runtime/synthetic-token-catalog.json"),
                ),
            ),
        )
        return rule, target

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

    def test_validator_sync_rule_replaces_legacy_mutable_release_identity(
        self,
    ) -> None:
        rule = next(
            rule
            for rule in SYNC_MODULE.SYNC_RULES
            if rule.source == Path("scripts/validate_sync_manifest_changes.py")
        )
        source = self.source_root / rule.repo / rule.source
        source.parent.mkdir(parents=True)
        source.write_text(
            'default="personal_codex/public-sync-manifest.json"\n'
            f"{SYNC_MODULE.PUBLIC_LEGACY_MUTABLE_RELEASE_BLOCK}\n",
            encoding="utf-8",
        )

        SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

        target_payload = (self.repo_root / rule.target).read_text(encoding="utf-8")
        self.assertIn(
            'default="personal_codex/private-sync-manifest.json"',
            target_payload,
        )
        self.assertIn(
            SYNC_MODULE.PRIVATE_LEGACY_MUTABLE_RELEASE_BLOCK,
            target_payload,
        )
        self.assertNotIn(
            SYNC_MODULE.PUBLIC_LEGACY_MUTABLE_RELEASE_BLOCK,
            target_payload,
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

    def test_canonical_review_target_requires_policy_runtime_and_tests(
        self,
    ) -> None:
        policy_required_files = (
            Path("references/base-only-retarget-state-machine.json"),
            Path("references/canonical-claude-lane.md"),
            Path("references/claude-2.1.212-stream-schema.json"),
            Path("references/claude-stream-compatibility.json"),
            Path("scripts/named_claude_preflight"),
            Path("scripts/review_runtime/claude_stream_contract.py"),
            Path("scripts/review_runtime/claude_version_policy.py"),
            Path("scripts/review_runtime/fd_exec.py"),
            Path("scripts/review_runtime/named_claude_preflight.py"),
            Path("scripts/review_runtime/claude_refresh_lock.py"),
            Path("scripts/validate_claude_stream.py"),
            Path("tests/fixtures/compat/codex-review-gate.yml"),
            Path("tests/test_fd_exec.py"),
            Path("tests/test_claude_refresh_lock.py"),
            Path("tests/test_named_claude_preflight.py"),
            Path("tests/test_validate_claude_stream.py"),
        )
        self.assertTrue(
            set(policy_required_files).issubset(
                set(SYNC_MODULE.CANONICAL_REVIEW_REQUIRED_FILES)
            )
        )
        complete_required_files = set(
            SYNC_MODULE.CANONICAL_REVIEW_REQUIRED_FILES
        ) | set(policy_required_files)

        for missing in policy_required_files:
            with self.subTest(missing=missing):
                target = self.repo_root / f"canonical-review-{missing.name}"
                for relative in complete_required_files:
                    if relative == missing:
                        continue
                    path = target / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("canonical\n", encoding="utf-8")

                with self.assertRaisesRegex(
                    SYNC_MODULE.SyncError,
                    re.escape(f"missing required file: {missing}"),
                ):
                    SYNC_MODULE._validate_canonical_review_target_contents(target)

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

        with (
            mock.patch.object(
                SYNC_MODULE,
                "_create_prepared_regular_file_overlay_value",
                wraps=SYNC_MODULE._create_prepared_regular_file_overlay_value,
            ) as private_create_mock,
            mock.patch.object(
                SYNC_MODULE,
                "_copy_prepared_regular_file_overlay_file",
                wraps=SYNC_MODULE._copy_prepared_regular_file_overlay_file,
            ) as public_copy_mock,
            mock.patch.object(
                SYNC_MODULE,
                "_rename_regular_file_overlay_noreplace",
                wraps=SYNC_MODULE._rename_regular_file_overlay_noreplace,
            ) as rename_mock,
        ):
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
        self.assertEqual(len(recovery_paths), 2)
        repo_recoveries = [
            path for path in recovery_paths if path.is_relative_to(self.repo_root)
        ]
        external_prepared = [
            path for path in recovery_paths if not path.is_relative_to(self.repo_root)
        ]
        self.assertEqual(len(repo_recoveries), 1)
        self.assertTrue(
            repo_recoveries[0].is_relative_to(self.repo_root / ".codex-tmp")
        )
        retained = list(repo_recoveries[0].iterdir())
        self.assertEqual(retained, [])
        self.assertEqual(len(external_prepared), 1)
        self.assertEqual(stat.S_IMODE(external_prepared[0].stat().st_mode), 0o700)
        retained_public_root = external_prepared[0] / target.name
        self.assertEqual(
            (retained_public_root / "catalog.json").read_bytes(),
            b'{"owner":"Joey","pool":"public"}\n',
        )
        for retained_file in retained_public_root.rglob("*"):
            if retained_file.is_file():
                self.assertNotEqual(retained_file.read_bytes(), expected)
        self.assertEqual(rename_mock.call_count, 1)
        private_create_mock.assert_called_once()
        self.assertEqual(private_create_mock.call_args.args[0], expected)
        self.assertEqual(private_create_mock.call_args.args[2], "catalog.json")
        self.assertNotIn(
            "catalog.json",
            [call.args[2] for call in public_copy_mock.call_args_list],
        )

    def test_sync_main_reports_repo_recovery_and_external_retention(self) -> None:
        repo_recovery = self.repo_root / ".codex-tmp/private-overlay-recovery/run"
        external_retained = self.external_prepared_parent / ".skill.prepared.example"
        output = io.StringIO()

        with (
            mock.patch.object(
                SYNC_MODULE,
                "sync_sources",
                return_value=(repo_recovery, external_retained),
            ),
            contextlib.redirect_stdout(output),
        ):
            result = SYNC_MODULE.main(
                [
                    "--repo-root",
                    str(self.repo_root),
                    "--source-root",
                    str(self.source_root),
                ]
            )

        self.assertEqual(result, 0)
        self.assertEqual(
            output.getvalue().splitlines(),
            [
                "regular-file overlay recovery: "
                ".codex-tmp/private-overlay-recovery/run",
                f"external prepared tree retained: {external_retained}",
            ],
        )

    def test_secure_replacements_bypass_plain_path_helpers(self) -> None:
        secure_source = self.source_root / "secure-replacement-repo" / "skill"
        secure_source.mkdir(parents=True)
        (secure_source / "SKILL.md").write_text("replace-old\n", encoding="utf-8")
        (secure_source / "catalog.json").write_bytes(b"public\n")
        private = self.repo_root / "private/catalog.json"
        private.parent.mkdir()
        private.write_bytes(b"private\n")
        secure_target = Path("personal_codex/skills/secure-replacement")
        secure_rule = SYNC_MODULE.SyncRule(
            repo="secure-replacement-repo",
            source=Path("skill"),
            target=secure_target,
            replacements=(SYNC_MODULE.Replacement("replace-old", "replace-new"),),
            forbidden_residuals=("replace-old",),
            regular_file_overlays=(
                SYNC_MODULE.RegularFileOverlay(
                    source=Path("private/catalog.json"),
                    target=Path("catalog.json"),
                ),
            ),
        )

        with (
            mock.patch.object(
                SYNC_MODULE,
                "_apply_rule_replacements",
                side_effect=AssertionError("secure replacement used path helper"),
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_reject_forbidden_residuals",
                side_effect=AssertionError("secure residual used path helper"),
            ),
        ):
            SYNC_MODULE.sync_sources(
                self.repo_root,
                self.source_root,
                (secure_rule,),
            )

        self.assertEqual(
            (self.repo_root / secure_target / "SKILL.md").read_text(encoding="utf-8"),
            "replace-new\n",
        )

        plain_source = self.source_root / "plain-replacement-repo" / "skill"
        plain_source.mkdir(parents=True)
        (plain_source / "SKILL.md").write_text("replace-old\n", encoding="utf-8")
        plain_target = Path("personal_codex/skills/plain-replacement")
        plain_rule = SYNC_MODULE.SyncRule(
            repo="plain-replacement-repo",
            source=Path("skill"),
            target=plain_target,
            replacements=(SYNC_MODULE.Replacement("replace-old", "replace-new"),),
            forbidden_residuals=("replace-old",),
        )

        with (
            mock.patch.object(
                SYNC_MODULE,
                "_apply_rule_replacements",
                wraps=SYNC_MODULE._apply_rule_replacements,
            ) as replacement_mock,
            mock.patch.object(
                SYNC_MODULE,
                "_reject_forbidden_residuals",
                wraps=SYNC_MODULE._reject_forbidden_residuals,
            ) as residual_mock,
        ):
            SYNC_MODULE.sync_sources(
                self.repo_root,
                self.source_root,
                (plain_rule,),
            )

        replacement_mock.assert_called_once()
        residual_mock.assert_called_once()
        self.assertEqual(
            (self.repo_root / plain_target / "SKILL.md").read_text(encoding="utf-8"),
            "replace-new\n",
        )

    def test_regular_file_overlay_repo_swap_after_source_read_blocks_write(
        self,
    ) -> None:
        source = self.source_root / "example-repo" / "skill"
        source.mkdir(parents=True)
        (source / "catalog.json").write_text("public\n", encoding="utf-8")
        private_catalog = self.repo_root / "private-overrides" / "catalog.json"
        private_catalog.parent.mkdir(parents=True)
        private_catalog.write_text("private\n", encoding="utf-8")
        target = self.repo_root / "personal_codex" / "skills" / "example"
        target.mkdir(parents=True)
        (target / "catalog.json").write_text("installed\n", encoding="utf-8")
        replacement_root = self.root / "replacement-repository"
        replacement_target = replacement_root / "personal_codex" / "skills" / "example"
        replacement_target.mkdir(parents=True)
        (replacement_target / "catalog.json").write_text(
            "replacement-installed\n",
            encoding="utf-8",
        )
        saved_root = self.root / "original-repository"
        rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/example"),
            regular_file_overlays=(
                SYNC_MODULE.RegularFileOverlay(
                    source=Path("private-overrides/catalog.json"),
                    target=Path("catalog.json"),
                ),
            ),
        )
        real_read = SYNC_MODULE._read_regular_file_overlay_source

        def read_then_swap(*args, **kwargs):
            data = real_read(*args, **kwargs)
            self.repo_root.rename(saved_root)
            replacement_root.rename(self.repo_root)
            return data

        with (
            mock.patch.object(
                SYNC_MODULE,
                "_read_regular_file_overlay_source",
                side_effect=read_then_swap,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_rename_regular_file_overlay_noreplace",
                wraps=SYNC_MODULE._rename_regular_file_overlay_noreplace,
            ) as rename_mock,
        ):
            with self.assertRaises(SYNC_MODULE.SyncError) as raised:
                SYNC_MODULE.sync_sources(
                    self.repo_root,
                    self.source_root,
                    (rule,),
                )

        self.assertRegex(str(raised.exception), "repository root.*binding changed")
        self.assertEqual(rename_mock.call_count, 0)
        self.assertEqual(
            (saved_root / rule.target / "catalog.json").read_bytes(),
            b"installed\n",
        )
        self.assertEqual(
            (self.repo_root / rule.target / "catalog.json").read_bytes(),
            b"replacement-installed\n",
        )
        self.assertFalse(
            (saved_root / SYNC_MODULE.REGULAR_FILE_OVERLAY_RECOVERY_ROOT).exists()
        )

    def test_regular_file_overlay_swapped_repo_fails_before_scope_mutation(
        self,
    ) -> None:
        target = self.repo_root / "personal_codex" / "skills" / "example"
        target.mkdir(parents=True)
        (target / "catalog.json").write_text("installed\n", encoding="utf-8")
        replacement_root = self.root / "replacement-before-scope"
        replacement_root.mkdir()
        saved_root = self.root / "original-before-scope"

        with contextlib.ExitStack() as stack:
            repo_binding = SYNC_MODULE._pin_regular_file_overlay_directory(
                stack,
                self.repo_root,
                label="repository root",
            )
            self.repo_root.rename(saved_root)
            replacement_root.rename(self.repo_root)
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "repository root.*binding changed",
            ):
                with SYNC_MODULE._regular_file_overlay_staging_directory(
                    repo_binding,
                    Path("personal_codex/skills/example"),
                ):
                    self.fail("repository swap must fail before staging")

        self.assertFalse(
            (self.repo_root / SYNC_MODULE.REGULAR_FILE_OVERLAY_RECOVERY_ROOT).exists()
        )
        self.assertFalse(
            (saved_root / SYNC_MODULE.REGULAR_FILE_OVERLAY_RECOVERY_ROOT).exists()
        )

    def test_regular_file_overlay_repo_swap_after_scope_blocks_candidate_copy(
        self,
    ) -> None:
        source = self.source_root / "example-repo" / "skill"
        source.mkdir(parents=True)
        (source / "catalog.json").write_text("public\n", encoding="utf-8")
        private_catalog = self.repo_root / "private-overrides" / "catalog.json"
        private_catalog.parent.mkdir(parents=True)
        private_catalog.write_text("private\n", encoding="utf-8")
        target = self.repo_root / "personal_codex" / "skills" / "example"
        target.mkdir(parents=True)
        (target / "catalog.json").write_text("installed\n", encoding="utf-8")
        replacement_root = self.root / "replacement-after-scope"
        replacement_target = replacement_root / "personal_codex" / "skills" / "example"
        replacement_target.mkdir(parents=True)
        (replacement_target / "catalog.json").write_text(
            "replacement-installed\n",
            encoding="utf-8",
        )
        saved_root = self.root / "original-after-scope"
        rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/example"),
            regular_file_overlays=(
                SYNC_MODULE.RegularFileOverlay(
                    source=Path("private-overrides/catalog.json"),
                    target=Path("catalog.json"),
                ),
            ),
        )
        real_copy = SYNC_MODULE._copy_prepared_regular_file_overlay_staging

        def swap_then_copy(*args, **kwargs):
            self.repo_root.rename(saved_root)
            replacement_root.rename(self.repo_root)
            return real_copy(*args, **kwargs)

        with (
            mock.patch.object(
                SYNC_MODULE,
                "_copy_prepared_regular_file_overlay_staging",
                side_effect=swap_then_copy,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_rename_regular_file_overlay_noreplace",
                wraps=SYNC_MODULE._rename_regular_file_overlay_noreplace,
            ) as rename_mock,
        ):
            with self.assertRaises(SYNC_MODULE.SyncError) as raised:
                SYNC_MODULE.sync_sources(
                    self.repo_root,
                    self.source_root,
                    (rule,),
                )

        self.assertRegex(str(raised.exception), "repository root.*binding changed")
        self.assertIn("pathname binding is unknown", str(raised.exception))
        self.assertIn("last-known path", str(raised.exception))
        self.assertIn("is untrusted", str(raised.exception))
        self.assertEqual(rename_mock.call_count, 0)
        self.assertEqual(
            (saved_root / rule.target / "catalog.json").read_bytes(),
            b"installed\n",
        )
        self.assertEqual(
            (self.repo_root / rule.target / "catalog.json").read_bytes(),
            b"replacement-installed\n",
        )
        recovery_root = saved_root / SYNC_MODULE.REGULAR_FILE_OVERLAY_RECOVERY_ROOT
        scopes = list(recovery_root.iterdir())
        self.assertEqual(len(scopes), 1)
        self.assertEqual(list(scopes[0].iterdir()), [])

    def test_regular_file_overlay_target_parent_rebind_blocks_live_mutation(
        self,
    ) -> None:
        target_parent = self.repo_root / "target-parent"
        target = target_parent / "example"
        target.mkdir(parents=True)
        (target / "catalog.json").write_text("public\n", encoding="utf-8")
        saved_parent = self.repo_root / "saved-target-parent"

        with mock.patch.object(
            SYNC_MODULE,
            "_rename_regular_file_overlay_noreplace",
            wraps=SYNC_MODULE._rename_regular_file_overlay_noreplace,
        ) as rename_mock:
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "target parent.*changed",
            ):
                with self._regular_file_overlay_staging_directory(target) as scope:
                    stack, staging, binding = (
                        self._prepare_scoped_regular_file_overlay_candidate(scope)
                    )
                    with stack:
                        target_parent.rename(saved_parent)
                        replacement = target_parent / "example"
                        replacement.mkdir(parents=True)
                        (replacement / "catalog.json").write_text(
                            "replacement\n",
                            encoding="utf-8",
                        )
                        SYNC_MODULE._replace_target_with_regular_file_overlays(
                            target,
                            staging,
                            (binding,),
                            staging_scope=scope,
                        )

        self.assertEqual(rename_mock.call_count, 0)
        self.assertEqual(
            (saved_parent / "example/catalog.json").read_bytes(),
            b"public\n",
        )
        self.assertEqual((target / "catalog.json").read_bytes(), b"replacement\n")

    def test_regular_file_overlay_scope_rebind_blocks_candidate_copy(
        self,
    ) -> None:
        target = self._create_regular_file_overlay_target("scope-rebind")
        source_manifest = (
            SYNC_MODULE._capture_regular_file_overlay_tree_manifest_at_path(
                target,
                label="test prepared source",
            )
        )
        saved_scope: Path | None = None
        replacement_scope: Path | None = None

        with mock.patch.object(
            SYNC_MODULE,
            "_rename_regular_file_overlay_noreplace",
            wraps=SYNC_MODULE._rename_regular_file_overlay_noreplace,
        ) as rename_mock:
            with self.assertRaises(SYNC_MODULE.SyncError) as raised:
                with self._regular_file_overlay_staging_directory(target) as scope:
                    saved_scope = scope.path.with_name(f"{scope.path.name}-saved")
                    replacement_scope = scope.path
                    scope.path.rename(saved_scope)
                    scope.path.mkdir(mode=0o700)
                    (scope.path / "replacement").write_text(
                        "replacement\n",
                        encoding="utf-8",
                    )
                    with contextlib.ExitStack() as stack:
                        SYNC_MODULE._copy_prepared_regular_file_overlay_staging(
                            stack,
                            target,
                            scope.path / "candidate",
                            staging_scope=scope,
                            policy_target=Path("test/candidate"),
                            overlay_data={Path("catalog.json"): b"private\n"},
                            expected_source_manifest=source_manifest,
                        )

        self.assertIn("scope lineage changed", str(raised.exception))
        self.assertIn("pathname binding is unknown", str(raised.exception))
        self.assertIn("is untrusted", str(raised.exception))
        self.assertEqual(rename_mock.call_count, 0)
        self.assertEqual((target / "catalog.json").read_bytes(), b"public\n")
        self.assertIsNotNone(saved_scope)
        self.assertIsNotNone(replacement_scope)
        self.assertEqual(list(saved_scope.iterdir()), [])
        self.assertEqual(
            (replacement_scope / "replacement").read_bytes(),
            b"replacement\n",
        )

    def test_regular_file_overlay_candidate_install_preserves_rebound_target(
        self,
    ) -> None:
        source = self.source_root / "example-repo" / "skill"
        source.mkdir(parents=True)
        (source / "catalog.json").write_text("public\n", encoding="utf-8")
        private_catalog = self.repo_root / "private-overrides" / "catalog.json"
        private_catalog.parent.mkdir(parents=True)
        private_catalog.write_text("private\n", encoding="utf-8")
        target = self.repo_root / "personal_codex" / "skills" / "example"
        target.mkdir(parents=True)
        (target / "catalog.json").write_text("installed\n", encoding="utf-8")
        rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/example"),
            regular_file_overlays=(
                SYNC_MODULE.RegularFileOverlay(
                    source=Path("private-overrides/catalog.json"),
                    target=Path("catalog.json"),
                ),
            ),
        )
        real_rename = SYNC_MODULE._rename_regular_file_overlay_noreplace
        calls = 0

        def rebind_target_before_candidate_install(*args):
            nonlocal calls
            calls += 1
            if calls != 2:
                return real_rename(*args)
            target_parent_descriptor = args[3]
            target_name = args[4]
            descriptor = os.open(
                target_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=target_parent_descriptor,
            )
            try:
                os.write(descriptor, b"unknown\n")
            finally:
                os.close(descriptor)
            return real_rename(*args)

        with mock.patch.object(
            SYNC_MODULE,
            "_rename_regular_file_overlay_noreplace",
            side_effect=rebind_target_before_candidate_install,
        ):
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "candidate retained in recovery scope",
            ):
                SYNC_MODULE.sync_sources(
                    self.repo_root,
                    self.source_root,
                    (rule,),
                )

        self.assertEqual(calls, 2)
        self.assertEqual(target.read_bytes(), b"unknown\n")
        recovery_root = self.repo_root / SYNC_MODULE.REGULAR_FILE_OVERLAY_RECOVERY_ROOT
        scopes = list(recovery_root.iterdir())
        self.assertEqual(len(scopes), 1)
        backups = list(
            scopes[0].glob(f"{SYNC_MODULE.REGULAR_FILE_OVERLAY_BACKUP_PREFIX}*")
        )
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "catalog.json").read_bytes(), b"installed\n")
        self.assertEqual(
            (scopes[0] / target.name / "catalog.json").read_bytes(),
            b"private\n",
        )

    def test_regular_file_overlay_backup_move_preserves_rebound_source(self) -> None:
        source = self.source_root / "example-repo" / "skill"
        source.mkdir(parents=True)
        (source / "catalog.json").write_text("public\n", encoding="utf-8")
        private_catalog = self.repo_root / "private-overrides" / "catalog.json"
        private_catalog.parent.mkdir(parents=True)
        private_catalog.write_text("private\n", encoding="utf-8")
        target = self.repo_root / "personal_codex" / "skills" / "example"
        target.mkdir(parents=True)
        (target / "catalog.json").write_text("installed\n", encoding="utf-8")
        rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/example"),
            regular_file_overlays=(
                SYNC_MODULE.RegularFileOverlay(
                    source=Path("private-overrides/catalog.json"),
                    target=Path("catalog.json"),
                ),
            ),
        )
        real_rename = SYNC_MODULE._rename_regular_file_overlay_noreplace
        saved_original_name = "attacker-saved-original"
        calls = 0

        def rebind_source_before_retention(*args):
            nonlocal calls
            calls += 1
            if calls != 1:
                return real_rename(*args)
            source_parent_descriptor = args[1]
            source_name = args[2]
            os.rename(
                source_name,
                saved_original_name,
                src_dir_fd=source_parent_descriptor,
                dst_dir_fd=source_parent_descriptor,
            )
            descriptor = os.open(
                source_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=source_parent_descriptor,
            )
            try:
                os.write(descriptor, b"unknown\n")
            finally:
                os.close(descriptor)
            return real_rename(*args)

        with mock.patch.object(
            SYNC_MODULE,
            "_rename_regular_file_overlay_noreplace",
            side_effect=rebind_source_before_retention,
        ):
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "prior target binding is unknown",
            ) as raised:
                SYNC_MODULE.sync_sources(
                    self.repo_root,
                    self.source_root,
                    (rule,),
                )

        self.assertEqual(calls, 1)
        message = str(raised.exception)
        self.assertIn("original transaction error:", message)
        self.assertIn("moved prior target backup binding changed", message)
        self.assertIn("only the candidate root identity matched", message)
        self.assertIn("exact contents are unverified", message)
        self.assertIn("must be treated as untrusted", message)
        self.assertFalse(target.exists())
        self.assertEqual(
            (target.parent / saved_original_name / "catalog.json").read_bytes(),
            b"installed\n",
        )
        recovery_root = self.repo_root / SYNC_MODULE.REGULAR_FILE_OVERLAY_RECOVERY_ROOT
        scopes = list(recovery_root.iterdir())
        self.assertEqual(len(scopes), 1)
        backups = list(
            scopes[0].glob(f"{SYNC_MODULE.REGULAR_FILE_OVERLAY_BACKUP_PREFIX}*")
        )
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b"unknown\n")
        self.assertEqual(
            (scopes[0] / target.name / "catalog.json").read_bytes(),
            b"private\n",
        )

    def test_regular_file_overlay_rebound_recovery_blocks_candidate_install(
        self,
    ) -> None:
        source = self.source_root / "example-repo" / "skill"
        source.mkdir(parents=True)
        (source / "catalog.json").write_text("public\n", encoding="utf-8")
        private_catalog = self.repo_root / "private-overrides" / "catalog.json"
        private_catalog.parent.mkdir(parents=True)
        private_catalog.write_text("private\n", encoding="utf-8")
        target = self.repo_root / "personal_codex" / "skills" / "example"
        target.mkdir(parents=True)
        (target / "catalog.json").write_text("installed\n", encoding="utf-8")
        rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/example"),
            regular_file_overlays=(
                SYNC_MODULE.RegularFileOverlay(
                    source=Path("private-overrides/catalog.json"),
                    target=Path("catalog.json"),
                ),
            ),
        )
        real_register = SYNC_MODULE._register_regular_file_overlay_retained_entry
        saved_name: str | None = None

        def register_then_rebind(scope, name, entry):
            nonlocal saved_name
            real_register(scope, name, entry)
            if not name.startswith(SYNC_MODULE.REGULAR_FILE_OVERLAY_BACKUP_PREFIX):
                return
            saved_name = f"{name}-saved"
            os.rename(
                name,
                saved_name,
                src_dir_fd=scope.container.descriptor,
                dst_dir_fd=scope.container.descriptor,
            )
            descriptor = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=scope.container.descriptor,
            )
            try:
                os.write(descriptor, b"unknown\n")
            finally:
                os.close(descriptor)

        with (
            mock.patch.object(
                SYNC_MODULE,
                "_register_regular_file_overlay_retained_entry",
                side_effect=register_then_rebind,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_rename_regular_file_overlay_noreplace",
                wraps=SYNC_MODULE._rename_regular_file_overlay_noreplace,
            ) as rename_mock,
        ):
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "prior target binding is unknown",
            ):
                SYNC_MODULE.sync_sources(
                    self.repo_root,
                    self.source_root,
                    (rule,),
                )

        self.assertEqual(rename_mock.call_count, 1)
        self.assertFalse(target.exists())
        self.assertIsNotNone(saved_name)
        recovery_root = self.repo_root / SYNC_MODULE.REGULAR_FILE_OVERLAY_RECOVERY_ROOT
        scopes = list(recovery_root.iterdir())
        self.assertEqual(len(scopes), 1)
        self.assertEqual(
            (scopes[0] / saved_name / "catalog.json").read_bytes(),
            b"installed\n",
        )
        rebound = [
            path
            for path in scopes[0].iterdir()
            if path.name.startswith(SYNC_MODULE.REGULAR_FILE_OVERLAY_BACKUP_PREFIX)
            and path.name != saved_name
        ]
        self.assertEqual(len(rebound), 1)
        self.assertEqual(rebound[0].read_bytes(), b"unknown\n")
        self.assertEqual(
            (scopes[0] / target.name / "catalog.json").read_bytes(),
            b"private\n",
        )

    def test_regular_file_overlay_reserves_backup_capacity_before_mutation(
        self,
    ) -> None:
        source = self.source_root / "example-repo" / "skill"
        source.mkdir(parents=True)
        (source / "catalog.json").write_text("public\n", encoding="utf-8")
        private_catalog = self.repo_root / "private-overrides" / "catalog.json"
        private_catalog.parent.mkdir(parents=True)
        private_catalog.write_text("private\n", encoding="utf-8")
        target = self.repo_root / "personal_codex" / "skills" / "example"
        target.mkdir(parents=True)
        (target / "catalog.json").write_text("installed\n", encoding="utf-8")
        target_inode = target.stat().st_ino
        rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/example"),
            regular_file_overlays=(
                SYNC_MODULE.RegularFileOverlay(
                    source=Path("private-overrides/catalog.json"),
                    target=Path("catalog.json"),
                ),
            ),
        )

        with (
            mock.patch.object(
                SYNC_MODULE,
                "MAX_REGULAR_FILE_OVERLAY_RETAINED_ENTRIES",
                0,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_rename_regular_file_overlay_noreplace",
                wraps=SYNC_MODULE._rename_regular_file_overlay_noreplace,
            ) as rename_mock,
        ):
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "retained entry limit would be exceeded",
            ):
                SYNC_MODULE.sync_sources(
                    self.repo_root,
                    self.source_root,
                    (rule,),
                )

        self.assertEqual(rename_mock.call_count, 0)
        self.assertEqual(target.stat().st_ino, target_inode)
        self.assertEqual((target / "catalog.json").read_bytes(), b"installed\n")

    def test_regular_file_overlay_unknown_scope_entry_blocks_candidate_install(
        self,
    ) -> None:
        target = self._create_regular_file_overlay_target("unknown-scope-entry")
        real_scope_guard = SYNC_MODULE._assert_regular_file_overlay_scope_binding
        injected = False

        def inject_unknown_entry(scope, *, operation):
            nonlocal injected
            real_scope_guard(scope, operation=operation)
            if operation != "final candidate install" or injected:
                return
            descriptor = os.open(
                "unknown-entry",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=scope.container.descriptor,
            )
            try:
                os.write(descriptor, b"unknown\n")
            finally:
                os.close(descriptor)
            injected = True

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "candidate retained in recovery scope",
        ):
            with self._regular_file_overlay_staging_directory(target) as scope:
                scope_path = scope.path
                stack, staging, binding = (
                    self._prepare_scoped_regular_file_overlay_candidate(scope)
                )
                with (
                    stack,
                    mock.patch.object(
                        SYNC_MODULE,
                        "_assert_regular_file_overlay_scope_binding",
                        side_effect=inject_unknown_entry,
                    ),
                    mock.patch.object(
                        SYNC_MODULE,
                        "_rename_regular_file_overlay_noreplace",
                        wraps=SYNC_MODULE._rename_regular_file_overlay_noreplace,
                    ) as rename_mock,
                ):
                    SYNC_MODULE._replace_target_with_regular_file_overlays(
                        target,
                        staging,
                        (binding,),
                        staging_scope=scope,
                    )

        self.assertTrue(injected)
        self.assertEqual(rename_mock.call_count, 1)
        self.assertFalse(target.exists())
        self.assertEqual(
            (scope_path / "candidate/catalog.json").read_bytes(),
            b"private\n",
        )
        self.assertEqual((scope_path / "unknown-entry").read_bytes(), b"unknown\n")
        backups = list(
            scope_path.glob(f"{SYNC_MODULE.REGULAR_FILE_OVERLAY_BACKUP_PREFIX}*")
        )
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "catalog.json").read_bytes(), b"public\n")

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

    def test_regular_file_overlay_rejects_multiple_secure_rules_before_mutation(
        self,
    ) -> None:
        rules: list[SYNC_MODULE.SyncRule] = []
        for index in range(2):
            source = self.source_root / f"repo-{index}" / "skill"
            source.mkdir(parents=True)
            (source / "catalog.json").write_text("public\n", encoding="utf-8")
            private = self.repo_root / "private" / f"catalog-{index}.json"
            private.parent.mkdir(parents=True, exist_ok=True)
            private.write_text("private\n", encoding="utf-8")
            rules.append(
                SYNC_MODULE.SyncRule(
                    repo=f"repo-{index}",
                    source=Path("skill"),
                    target=Path(f"personal_codex/skills/example-{index}"),
                    regular_file_overlays=(
                        SYNC_MODULE.RegularFileOverlay(
                            source=Path(f"private/catalog-{index}.json"),
                            target=Path("catalog.json"),
                        ),
                    ),
                )
            )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "exactly one secure rule",
        ):
            SYNC_MODULE.sync_sources(
                self.repo_root,
                self.source_root,
                tuple(rules),
            )

        self.assertFalse((self.repo_root / ".codex-tmp").exists())
        for rule in rules:
            self.assertFalse((self.repo_root / rule.target).exists())

    def test_production_sync_rules_define_one_secure_rule(self) -> None:
        secure_rules = [
            rule for rule in SYNC_MODULE.SYNC_RULES if rule.regular_file_overlays
        ]
        self.assertEqual(len(secure_rules), 1)
        self.assertEqual(
            secure_rules[0].target,
            SYNC_MODULE.CANONICAL_REVIEW_TARGET,
        )

    def test_plain_sync_and_retired_cleanup_precede_private_overlay_read(
        self,
    ) -> None:
        plain_source = self.source_root / "plain-repo" / "skill"
        plain_source.mkdir(parents=True)
        (plain_source / "SKILL.md").write_text("plain\n", encoding="utf-8")
        secure_source = self.source_root / "secure-repo" / "skill"
        secure_source.mkdir(parents=True)
        (secure_source / "catalog.json").write_text("public\n", encoding="utf-8")
        private_catalog = self.repo_root / "private" / "catalog.json"
        private_catalog.parent.mkdir()
        private_catalog.write_text("private\n", encoding="utf-8")
        retired_target = self.repo_root / SYNC_MODULE.RETIRED_TARGETS[0]
        retired_target.mkdir(parents=True)
        (retired_target / "stale").write_text("stale\n", encoding="utf-8")
        plain_target = Path("personal_codex/skills/plain")
        secure_target = Path("personal_codex/skills/secure")
        plain_rule = SYNC_MODULE.SyncRule(
            repo="plain-repo",
            source=Path("skill"),
            target=plain_target,
        )
        secure_rule = SYNC_MODULE.SyncRule(
            repo="secure-repo",
            source=Path("skill"),
            target=secure_target,
            regular_file_overlays=(
                SYNC_MODULE.RegularFileOverlay(
                    source=Path("private/catalog.json"),
                    target=Path("catalog.json"),
                ),
            ),
        )
        events: list[str] = []
        prepared_secure: Path | None = None
        real_public_copy = (
            SYNC_MODULE._copy_regular_file_overlay_public_source_to_prepared
        )
        real_cleanup = SYNC_MODULE._remove_retired_targets
        real_load = SYNC_MODULE._load_regular_file_overlay_data
        real_validate = SYNC_MODULE._validate_no_retired_review_references

        def record_public_prepare(source, staging, *, prepared_root, rule):
            nonlocal prepared_secure
            result = real_public_copy(
                source,
                staging,
                prepared_root=prepared_root,
                rule=rule,
            )
            if source == secure_source:
                prepared_secure = staging
                events.append("public-prepare")
                self.assertFalse(staging.is_relative_to(self.repo_root))
                self.assertEqual(
                    (staging / "catalog.json").read_text(encoding="utf-8"),
                    "public\n",
                )
            return result

        def record_cleanup(repo_root):
            events.append("cleanup")
            return real_cleanup(repo_root)

        def record_validation(repo_root, *, excluded_targets=()):
            events.append("precommit-validation")
            self.assertFalse((repo_root / secure_target).exists())
            return real_validate(
                repo_root,
                excluded_targets=excluded_targets,
            )

        def record_private_read(repo_root, rule, *, repo_binding):
            events.append("private-read")
            self.assertIsNotNone(prepared_secure)
            self.assertEqual(
                (prepared_secure / "catalog.json").read_text(encoding="utf-8"),
                "public\n",
            )
            self.assertEqual(
                (repo_root / plain_target / "SKILL.md").read_text(encoding="utf-8"),
                "plain\n",
            )
            self.assertFalse(retired_target.exists())
            return real_load(repo_root, rule, repo_binding=repo_binding)

        with (
            mock.patch.object(
                SYNC_MODULE,
                "_copy_regular_file_overlay_public_source_to_prepared",
                side_effect=record_public_prepare,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_remove_retired_targets",
                side_effect=record_cleanup,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_validate_no_retired_review_references",
                side_effect=record_validation,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_load_regular_file_overlay_data",
                side_effect=record_private_read,
            ),
        ):
            SYNC_MODULE.sync_sources(
                self.repo_root,
                self.source_root,
                (secure_rule, plain_rule),
            )

        self.assertEqual(
            events,
            [
                "cleanup",
                "precommit-validation",
                "public-prepare",
                "private-read",
            ],
        )
        self.assertEqual(
            (self.repo_root / secure_target / "catalog.json").read_text(
                encoding="utf-8"
            ),
            "private\n",
        )

    def test_canonical_secure_validation_and_retention_precede_live_commit(
        self,
    ) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule()
        events: list[str] = []
        real_validate = (
            SYNC_MODULE._validate_regular_file_overlay_required_manifest_paths
        )
        real_pin = SYNC_MODULE._pin_regular_file_overlay_targets
        real_manifest_assert = SYNC_MODULE._assert_regular_file_overlay_tree_manifest
        real_rename = SYNC_MODULE._rename_regular_file_overlay_noreplace

        def record_validation(manifest, policy_target, *, surface):
            events.append(
                "staging-validation"
                if surface == "staged target"
                else "external-validation"
            )
            return real_validate(
                manifest,
                policy_target,
                surface=surface,
            )

        def record_pin(*args, **kwargs):
            events.append("private-pin")
            return real_pin(*args, **kwargs)

        def record_manifest_assert(*args, **kwargs):
            if kwargs.get("label") == "retained external prepared source":
                events.append("external-retention-validation")
            return real_manifest_assert(*args, **kwargs)

        def record_rename(*args, **kwargs):
            events.append("live-rename")
            return real_rename(*args, **kwargs)

        with (
            mock.patch.object(
                SYNC_MODULE,
                "_validate_regular_file_overlay_required_manifest_paths",
                side_effect=record_validation,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_pin_regular_file_overlay_targets",
                side_effect=record_pin,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_assert_regular_file_overlay_tree_manifest",
                side_effect=record_manifest_assert,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_rename_regular_file_overlay_noreplace",
                side_effect=record_rename,
            ),
        ):
            SYNC_MODULE.sync_sources(
                self.repo_root,
                self.source_root,
                (rule,),
            )

        self.assertEqual(
            events,
            [
                "external-validation",
                "staging-validation",
                "private-pin",
                "external-retention-validation",
                "live-rename",
                "live-rename",
            ],
        )
        self.assertEqual(
            (
                target / "scripts/review_runtime/synthetic-token-catalog.json"
            ).read_bytes(),
            b"private\n",
        )

    def test_canonical_staging_validation_failure_precedes_live_mutation(
        self,
    ) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule()
        target_inode = target.stat().st_ino
        real_validate = (
            SYNC_MODULE._validate_regular_file_overlay_required_manifest_paths
        )
        validations = 0

        def fail_staging_validation(manifest, policy_target, *, surface):
            nonlocal validations
            validations += 1
            real_validate(manifest, policy_target, surface=surface)
            if surface == "staged target":
                raise SYNC_MODULE.SyncError("injected staging validation failure")

        with (
            mock.patch.object(
                SYNC_MODULE,
                "_validate_regular_file_overlay_required_manifest_paths",
                side_effect=fail_staging_validation,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_rename_regular_file_overlay_noreplace",
                wraps=SYNC_MODULE._rename_regular_file_overlay_noreplace,
            ) as rename_mock,
        ):
            with self.assertRaises(SYNC_MODULE.SyncError) as raised:
                SYNC_MODULE.sync_sources(
                    self.repo_root,
                    self.source_root,
                    (rule,),
                )

        self.assertEqual(validations, 2)
        recovery_root = self.repo_root / SYNC_MODULE.REGULAR_FILE_OVERLAY_RECOVERY_ROOT
        scopes = list(recovery_root.iterdir())
        self.assertEqual(len(scopes), 1)
        self.assertIn("injected staging validation failure", str(raised.exception))
        self.assertIn(str(scopes[0]), str(raised.exception))
        self.assertTrue((scopes[0] / target.name).is_dir())
        self.assertEqual(rename_mock.call_count, 0)
        self.assertEqual(target.stat().st_ino, target_inode)
        self.assertEqual((target / "old-marker").read_bytes(), b"old\n")

    def test_canonical_staging_validation_cannot_admit_late_injection(
        self,
    ) -> None:
        source = self.source_root / "staged-policy-repo" / "skill"
        source.mkdir(parents=True)
        (source / "README.md").write_text("clean\n", encoding="utf-8")
        private = self.repo_root / "private/README.md"
        private.parent.mkdir()
        bad_reference = SYNC_MODULE.RETIRED_REVIEW_REFERENCES[0]
        private.write_text(f"{bad_reference}\n", encoding="utf-8")
        target = self.repo_root / "personal_codex/skills/staged-policy"
        target.mkdir(parents=True)
        (target / "old-marker").write_bytes(b"old\n")
        target_inode = target.stat().st_ino
        rule = SYNC_MODULE.SyncRule(
            repo="staged-policy-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/staged-policy"),
            regular_file_overlays=(
                SYNC_MODULE.RegularFileOverlay(
                    source=Path("private/README.md"),
                    target=Path("README.md"),
                ),
            ),
        )
        real_validate = SYNC_MODULE._validate_regular_file_overlay_policy_bytes
        swapped_during_validation = False

        def validate_with_decoy(data, relative, policy_target, *, surface):
            nonlocal swapped_during_validation
            if surface == "staged target" and relative == Path("README.md"):
                recovery_root = (
                    self.repo_root
                    / SYNC_MODULE.REGULAR_FILE_OVERLAY_RECOVERY_ROOT
                )
                scopes = list(recovery_root.iterdir())
                self.assertEqual(len(scopes), 1)
                candidate = scopes[0] / target.name
                saved = scopes[0] / f".{target.name}.expected"
                candidate.rename(saved)
                candidate.mkdir(mode=0o700)
                (candidate / "README.md").write_text(
                    "clean decoy\n",
                    encoding="utf-8",
                )
                swapped_during_validation = True
                try:
                    return real_validate(
                        data,
                        relative,
                        policy_target,
                        surface=surface,
                    )
                finally:
                    shutil.rmtree(candidate)
                    saved.rename(candidate)
            return real_validate(
                data,
                relative,
                policy_target,
                surface=surface,
            )

        with (
            mock.patch.object(
                SYNC_MODULE,
                "_validate_regular_file_overlay_policy_bytes",
                side_effect=validate_with_decoy,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_rename_regular_file_overlay_noreplace",
                wraps=SYNC_MODULE._rename_regular_file_overlay_noreplace,
            ) as rename_mock,
        ):
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "retains retired reference",
            ):
                SYNC_MODULE.sync_sources(
                    self.repo_root,
                    self.source_root,
                    (rule,),
                )

        self.assertTrue(swapped_during_validation)
        self.assertEqual(rename_mock.call_count, 0)
        self.assertEqual(target.stat().st_ino, target_inode)
        self.assertEqual((target / "old-marker").read_bytes(), b"old\n")

    def test_external_validation_cannot_admit_late_injection(self) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule()
        source = self.source_root / "canonical-repo" / "skill"
        bad_reference = SYNC_MODULE.RETIRED_REVIEW_REFERENCES[0]
        (source / "SKILL.md").write_text(
            f"{bad_reference}\n",
            encoding="utf-8",
        )
        target_inode = target.stat().st_ino
        real_validate = SYNC_MODULE._validate_regular_file_overlay_policy_bytes
        swapped_during_validation = False

        def validate_with_decoy(data, relative, policy_target, *, surface):
            nonlocal swapped_during_validation
            if (
                surface == "prepared public source"
                and relative == Path("SKILL.md")
            ):
                saved = source.with_name(f".{source.name}.expected")
                source.rename(saved)
                shutil.copytree(saved, source)
                (source / "SKILL.md").write_text(
                    "clean decoy\n",
                    encoding="utf-8",
                )
                swapped_during_validation = True
                try:
                    return real_validate(
                        data,
                        relative,
                        policy_target,
                        surface=surface,
                    )
                finally:
                    shutil.rmtree(source)
                    saved.rename(source)
            return real_validate(
                data,
                relative,
                policy_target,
                surface=surface,
            )

        with mock.patch.object(
            SYNC_MODULE,
            "_validate_regular_file_overlay_policy_bytes",
            side_effect=validate_with_decoy,
        ):
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "retains retired reference",
            ):
                SYNC_MODULE.sync_sources(
                    self.repo_root,
                    self.source_root,
                    (rule,),
                )

        self.assertTrue(swapped_during_validation)
        self.assertEqual(target.stat().st_ino, target_inode)
        self.assertEqual((target / "old-marker").read_bytes(), b"old\n")

    def test_secure_public_prepare_rejects_transient_file_rebind(self) -> None:
        source = self.source_root / "public-rebind-repo" / "skill"
        source.mkdir(parents=True)
        (source / "catalog.json").write_bytes(b"public\n")
        payload = source / "payload.py"
        payload.write_bytes(b"trusted\n")
        private = self.repo_root / "private/catalog.json"
        private.parent.mkdir()
        private.write_bytes(b"private\n")
        target = self.repo_root / "personal_codex/skills/public-rebind"
        target.mkdir(parents=True)
        (target / "marker").write_bytes(b"old\n")
        target_inode = target.stat().st_ino
        rule = SYNC_MODULE.SyncRule(
            repo="public-rebind-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/public-rebind"),
            regular_file_overlays=(
                SYNC_MODULE.RegularFileOverlay(
                    source=Path("private/catalog.json"),
                    target=Path("catalog.json"),
                ),
            ),
        )
        saved = source.parent / ".payload.py.expected"
        swapped = False
        swap_performed = False
        restored_during_copy = False
        real_capture = SYNC_MODULE._capture_regular_file_overlay_tree_manifest
        real_stat = SYNC_MODULE.os.stat

        def capture_then_swap(*args, label, **kwargs):
            nonlocal swapped, swap_performed
            manifest = real_capture(*args, label=label, **kwargs)
            if label == "initial public source" and not swapped:
                payload.rename(saved)
                payload.write_bytes(b"malicious\n")
                swapped = True
                swap_performed = True
            return manifest

        def stat_then_restore(path, *args, **kwargs):
            nonlocal swapped, restored_during_copy
            metadata = real_stat(path, *args, **kwargs)
            if swapped and path == payload.name and kwargs.get("dir_fd") is not None:
                payload.unlink()
                saved.rename(payload)
                swapped = False
                restored_during_copy = True
            return metadata

        stat_mock = mock.Mock(side_effect=stat_then_restore)
        supports_dir_fd = frozenset(
            (set(SYNC_MODULE.os.supports_dir_fd) - {real_stat}) | {stat_mock}
        )
        supports_follow_symlinks = frozenset(
            (set(SYNC_MODULE.os.supports_follow_symlinks) - {real_stat})
            | {stat_mock}
        )

        try:
            with (
                mock.patch.object(
                    SYNC_MODULE,
                    "_capture_regular_file_overlay_tree_manifest",
                    side_effect=capture_then_swap,
                ),
                mock.patch.object(
                    SYNC_MODULE.os,
                    "stat",
                    stat_mock,
                ),
                mock.patch.object(
                    SYNC_MODULE.os,
                    "supports_dir_fd",
                    supports_dir_fd,
                ),
                mock.patch.object(
                    SYNC_MODULE.os,
                    "supports_follow_symlinks",
                    supports_follow_symlinks,
                ),
            ):
                with self.assertRaisesRegex(
                    SYNC_MODULE.SyncError,
                    "public source file binding changed",
                ):
                    SYNC_MODULE.sync_sources(
                        self.repo_root,
                        self.source_root,
                        (rule,),
                    )
        finally:
            if saved.exists():
                payload.unlink(missing_ok=True)
                saved.rename(payload)

        self.assertTrue(swap_performed)
        self.assertTrue(restored_during_copy)
        self.assertFalse(swapped)
        self.assertEqual(payload.read_bytes(), b"trusted\n")
        self.assertEqual(target.stat().st_ino, target_inode)
        self.assertEqual((target / "marker").read_bytes(), b"old\n")

    def test_secure_public_prepare_rejects_transient_entry_add_and_remove(
        self,
    ) -> None:
        source = self.source_root / "public-entry-race-repo" / "skill"
        source.mkdir(parents=True)
        (source / "catalog.json").write_bytes(b"public\n")
        (source / "payload.py").write_bytes(b"trusted\n")
        private = self.repo_root / "private/catalog.json"
        private.parent.mkdir()
        private.write_bytes(b"private\n")
        target = self.repo_root / "personal_codex/skills/public-entry-race"
        target.mkdir(parents=True)
        (target / "marker").write_bytes(b"old\n")
        target_inode = target.stat().st_ino
        rule = SYNC_MODULE.SyncRule(
            repo="public-entry-race-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/public-entry-race"),
            regular_file_overlays=(
                SYNC_MODULE.RegularFileOverlay(
                    source=Path("private/catalog.json"),
                    target=Path("catalog.json"),
                ),
            ),
        )
        transient = source / "transient.py"
        real_capture = SYNC_MODULE._capture_regular_file_overlay_tree_manifest
        real_names = SYNC_MODULE._bounded_regular_file_overlay_tree_names
        added = False
        removed_before_detection = False

        def capture_then_add(*args, label, **kwargs):
            nonlocal added
            manifest = real_capture(*args, label=label, **kwargs)
            if label == "initial public source" and not added:
                transient.write_bytes(b"transient\n")
                added = True
            return manifest

        def names_then_remove(*args, maximum, label):
            nonlocal removed_before_detection
            names = real_names(*args, maximum=maximum, label=label)
            if label == "public source" and transient.name in names:
                transient.unlink()
                removed_before_detection = True
            return names

        with (
            mock.patch.object(
                SYNC_MODULE,
                "_capture_regular_file_overlay_tree_manifest",
                side_effect=capture_then_add,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_bounded_regular_file_overlay_tree_names",
                side_effect=names_then_remove,
            ),
        ):
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "cannot inspect regular-file overlay public source entry transient.py",
            ):
                SYNC_MODULE.sync_sources(
                    self.repo_root,
                    self.source_root,
                    (rule,),
                )

        self.assertTrue(added)
        self.assertTrue(removed_before_detection)
        self.assertFalse(transient.exists())
        self.assertEqual(target.stat().st_ino, target_inode)
        self.assertEqual((target / "marker").read_bytes(), b"old\n")

    def test_prepared_copy_rejects_transient_file_rebind_and_restore(self) -> None:
        source = self.source_root / "prepared-file-rebind-repo" / "skill"
        source.mkdir(parents=True)
        (source / "catalog.json").write_bytes(b"public\n")
        (source / "payload.py").write_bytes(b"trusted\n")
        private = self.repo_root / "private/catalog.json"
        private.parent.mkdir()
        private.write_bytes(b"private\n")
        target = self.repo_root / "personal_codex/skills/prepared-file-rebind"
        target.mkdir(parents=True)
        (target / "marker").write_bytes(b"old\n")
        target_inode = target.stat().st_ino
        rule = SYNC_MODULE.SyncRule(
            repo="prepared-file-rebind-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/prepared-file-rebind"),
            regular_file_overlays=(
                SYNC_MODULE.RegularFileOverlay(
                    source=Path("private/catalog.json"),
                    target=Path("catalog.json"),
                ),
            ),
        )
        real_copy = SYNC_MODULE._copy_prepared_regular_file_overlay_staging
        real_stat = SYNC_MODULE.os.stat
        inside_copy = False
        swapped = False
        swap_performed = False
        restored_during_copy = False
        catalog_stat_calls = 0
        swapped_root: Path | None = None
        saved: Path | None = None

        def swap_catalog(root: Path) -> None:
            nonlocal swapped, swap_performed, swapped_root, saved
            if swapped:
                return
            catalog = root / "catalog.json"
            saved = root.parent / f".{root.name}.catalog.expected"
            catalog.rename(saved)
            catalog.write_bytes(b"malicious\n")
            swapped_root = root
            swapped = True
            swap_performed = True

        def restore_catalog() -> None:
            nonlocal swapped, restored_during_copy
            if not swapped or swapped_root is None or saved is None:
                return
            (swapped_root / "catalog.json").unlink()
            saved.rename(swapped_root / "catalog.json")
            swapped = False
            restored_during_copy = True

        def copy_with_window(*args, **kwargs):
            nonlocal inside_copy
            inside_copy = True
            try:
                swap_catalog(Path(args[1]))
                return real_copy(*args, **kwargs)
            finally:
                inside_copy = False

        def stat_then_restore(path, *args, **kwargs):
            nonlocal catalog_stat_calls
            metadata = real_stat(path, *args, **kwargs)
            if inside_copy and swapped and path == "catalog.json":
                catalog_stat_calls += 1
                if catalog_stat_calls == 1:
                    restore_catalog()
            return metadata

        stat_mock = mock.Mock(side_effect=stat_then_restore)
        supports_dir_fd = frozenset(
            (set(SYNC_MODULE.os.supports_dir_fd) - {real_stat}) | {stat_mock}
        )
        supports_follow_symlinks = frozenset(
            (set(SYNC_MODULE.os.supports_follow_symlinks) - {real_stat})
            | {stat_mock}
        )

        with (
            mock.patch.object(
                SYNC_MODULE,
                "_copy_prepared_regular_file_overlay_staging",
                side_effect=copy_with_window,
            ),
            mock.patch.object(
                SYNC_MODULE.os,
                "stat",
                stat_mock,
            ),
            mock.patch.object(
                SYNC_MODULE.os,
                "supports_dir_fd",
                supports_dir_fd,
            ),
            mock.patch.object(
                SYNC_MODULE.os,
                "supports_follow_symlinks",
                supports_follow_symlinks,
            ),
        ):
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "prepared overlay source changed while opening",
            ):
                SYNC_MODULE.sync_sources(
                    self.repo_root,
                    self.source_root,
                    (rule,),
                )

        self.assertTrue(swap_performed)
        self.assertTrue(restored_during_copy)
        self.assertFalse(swapped)
        self.assertEqual(target.stat().st_ino, target_inode)
        self.assertEqual((target / "marker").read_bytes(), b"old\n")

    def test_prepared_copy_rejects_transient_root_rebind_and_restore(self) -> None:
        source = self.source_root / "prepared-root-rebind-repo" / "skill"
        source.mkdir(parents=True)
        (source / "catalog.json").write_bytes(b"public\n")
        (source / "payload.py").write_bytes(b"trusted\n")
        private = self.repo_root / "private/catalog.json"
        private.parent.mkdir()
        private.write_bytes(b"private\n")
        target = self.repo_root / "personal_codex/skills/prepared-root-rebind"
        target.mkdir(parents=True)
        (target / "marker").write_bytes(b"old\n")
        target_inode = target.stat().st_ino
        rule = SYNC_MODULE.SyncRule(
            repo="prepared-root-rebind-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/prepared-root-rebind"),
            regular_file_overlays=(
                SYNC_MODULE.RegularFileOverlay(
                    source=Path("private/catalog.json"),
                    target=Path("catalog.json"),
                ),
            ),
        )
        real_copy = SYNC_MODULE._copy_prepared_regular_file_overlay_staging
        real_assert_directory = (
            SYNC_MODULE._assert_regular_file_overlay_directory_binding
        )
        inside_copy = False
        source_binding_checks = 0
        swapped = False
        swap_performed = False
        restored_during_copy = False
        swapped_root: Path | None = None
        saved_root: Path | None = None

        def swap_root(root: Path) -> None:
            nonlocal swapped, swap_performed, swapped_root, saved_root
            if swapped:
                return
            saved_root = root.with_name(f".{root.name}.expected")
            root.rename(saved_root)
            root.mkdir(mode=0o700)
            (root / "catalog.json").write_bytes(b"public\n")
            (root / "payload.py").write_bytes(b"malicious\n")
            swapped_root = root
            swapped = True
            swap_performed = True

        def restore_root() -> None:
            nonlocal swapped, restored_during_copy
            if not swapped or swapped_root is None or saved_root is None:
                return
            shutil.rmtree(swapped_root)
            saved_root.rename(swapped_root)
            swapped = False
            restored_during_copy = True

        def copy_with_window(*args, **kwargs):
            nonlocal inside_copy
            inside_copy = True
            try:
                return real_copy(*args, **kwargs)
            finally:
                inside_copy = False

        def assert_with_transient_root(pinned, *, label):
            nonlocal source_binding_checks
            if inside_copy and label == "validated external prepared source":
                source_binding_checks += 1
                if source_binding_checks == 2:
                    restore_root()
                result = real_assert_directory(pinned, label=label)
                if source_binding_checks == 1:
                    swap_root(pinned.path)
                return result
            return real_assert_directory(pinned, label=label)

        with (
            mock.patch.object(
                SYNC_MODULE,
                "_copy_prepared_regular_file_overlay_staging",
                side_effect=copy_with_window,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_assert_regular_file_overlay_directory_binding",
                side_effect=assert_with_transient_root,
            ),
        ):
            SYNC_MODULE.sync_sources(
                self.repo_root,
                self.source_root,
                (rule,),
            )

        self.assertTrue(swap_performed)
        self.assertTrue(restored_during_copy)
        self.assertFalse(swapped)
        self.assertNotEqual(target.stat().st_ino, target_inode)
        self.assertFalse((target / "marker").exists())
        self.assertEqual((target / "payload.py").read_bytes(), b"trusted\n")
        self.assertEqual((target / "catalog.json").read_bytes(), b"private\n")

    def test_secure_public_prepare_bounds_entries_before_repo_candidate(self) -> None:
        source = self.source_root / "bounded-entry-repo" / "skill"
        source.mkdir(parents=True)
        for index in range(3):
            (source / f"ignored-{index}.pyc").write_bytes(b"ignored\n")
        (source / "catalog.json").write_bytes(b"public\n")
        private = self.repo_root / "private/catalog.json"
        private.parent.mkdir()
        private.write_bytes(b"private\n")
        target = self.repo_root / "personal_codex/skills/bounded-entry"
        target.mkdir(parents=True)
        (target / "marker").write_bytes(b"old\n")
        target_inode = target.stat().st_ino
        rule = SYNC_MODULE.SyncRule(
            repo="bounded-entry-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/bounded-entry"),
            regular_file_overlays=(
                SYNC_MODULE.RegularFileOverlay(
                    source=Path("private/catalog.json"),
                    target=Path("catalog.json"),
                ),
            ),
        )

        with (
            mock.patch.object(
                SYNC_MODULE,
                "MAX_REGULAR_FILE_OVERLAY_TREE_ENTRIES",
                2,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_copy_prepared_regular_file_overlay_staging",
                side_effect=AssertionError("repo candidate copy must not start"),
            ),
        ):
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "bounded entry capacity",
            ):
                SYNC_MODULE.sync_sources(
                    self.repo_root,
                    self.source_root,
                    (rule,),
                )

        self.assertEqual(target.stat().st_ino, target_inode)
        self.assertEqual((target / "marker").read_bytes(), b"old\n")

    def test_secure_public_prepare_bounds_bytes_before_read(self) -> None:
        source = self.source_root / "bounded-byte-repo" / "skill"
        source.mkdir(parents=True)
        (source / "catalog.json").write_bytes(b"12345")
        private = self.repo_root / "private/catalog.json"
        private.parent.mkdir()
        private.write_bytes(b"private\n")
        target = self.repo_root / "personal_codex/skills/bounded-byte"
        target.mkdir(parents=True)
        (target / "marker").write_bytes(b"old\n")
        target_inode = target.stat().st_ino
        rule = SYNC_MODULE.SyncRule(
            repo="bounded-byte-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/bounded-byte"),
            regular_file_overlays=(
                SYNC_MODULE.RegularFileOverlay(
                    source=Path("private/catalog.json"),
                    target=Path("catalog.json"),
                ),
            ),
        )

        with (
            mock.patch.object(
                SYNC_MODULE,
                "MAX_REGULAR_FILE_OVERLAY_TREE_BYTES",
                4,
            ),
            mock.patch.object(
                SYNC_MODULE.os,
                "read",
                side_effect=AssertionError("oversized public source must not be read"),
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_copy_prepared_regular_file_overlay_staging",
                side_effect=AssertionError("repo candidate copy must not start"),
            ),
        ):
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "public source tree exceeds 4 bytes",
            ):
                SYNC_MODULE.sync_sources(
                    self.repo_root,
                    self.source_root,
                    (rule,),
                )

        self.assertEqual(target.stat().st_ino, target_inode)
        self.assertEqual((target / "marker").read_bytes(), b"old\n")

    def test_secure_copy_bounds_descriptors_by_tree_depth(self) -> None:
        try:
            import resource
        except ImportError:
            self.skipTest("resource limits are unavailable")

        descriptor_root = next(
            (
                path
                for path in (Path("/dev/fd"), Path("/proc/self/fd"))
                if path.is_dir()
            ),
            None,
        )
        if descriptor_root is None:
            self.skipTest("open descriptor inventory is unavailable")
        open_descriptors = len(list(descriptor_root.iterdir()))
        old_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
        soft_target = max(64, open_descriptors + 24)
        if soft_target > 128:
            self.skipTest("process already holds too many descriptors")
        hard_limit = old_limit[1]
        if hard_limit != resource.RLIM_INFINITY and hard_limit < soft_target:
            self.skipTest("hard descriptor limit is too low for the regression test")
        if old_limit[0] != resource.RLIM_INFINITY and old_limit[0] < soft_target:
            soft_target = old_limit[0]
        if soft_target <= open_descriptors + 16:
            self.skipTest("soft descriptor limit has insufficient test headroom")

        rule, target = self._create_canonical_regular_file_overlay_rule()
        source = self.source_root / rule.repo / rule.source
        for index in range(soft_target):
            sibling = source / f"wide-{index:03d}"
            sibling.mkdir()
            (sibling / "fixture.txt").write_bytes(b"fixture\n")

        resource.setrlimit(resource.RLIMIT_NOFILE, (soft_target, hard_limit))
        try:
            retained_paths = SYNC_MODULE.sync_sources(
                self.repo_root,
                self.source_root,
                (rule,),
            )
        finally:
            resource.setrlimit(resource.RLIMIT_NOFILE, old_limit)

        self.assertTrue(target.is_dir())
        self.assertEqual(len(retained_paths), 2)
        self.assertTrue((target / "wide-000/fixture.txt").is_file())

    def test_external_prepared_retention_validation_failure_precedes_live_mutation(
        self,
    ) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule()
        target_inode = target.stat().st_ino
        real_manifest_assert = SYNC_MODULE._assert_regular_file_overlay_tree_manifest

        def fail_retention_validation(*args, **kwargs):
            if kwargs.get("label") == "retained external prepared source":
                raise SYNC_MODULE.SyncError("injected retention validation failure")
            return real_manifest_assert(*args, **kwargs)

        with (
            mock.patch.object(
                SYNC_MODULE,
                "_assert_regular_file_overlay_tree_manifest",
                side_effect=fail_retention_validation,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_rename_regular_file_overlay_noreplace",
                wraps=SYNC_MODULE._rename_regular_file_overlay_noreplace,
            ) as rename_mock,
        ):
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "injected retention validation failure.*"
                "recovery scope retained for inspection.*"
                "external prepared tree retained at",
            ):
                SYNC_MODULE.sync_sources(
                    self.repo_root,
                    self.source_root,
                    (rule,),
                )

        self.assertEqual(rename_mock.call_count, 0)
        retained = list(self.external_prepared_parent.iterdir())
        self.assertEqual(len(retained), 1)
        self.assertTrue((retained[0] / target.name).is_dir())
        self.assertEqual(target.stat().st_ino, target_inode)
        self.assertEqual((target / "old-marker").read_bytes(), b"old\n")

    def test_external_prepared_partial_tree_is_retained_without_cleanup_authority(
        self,
    ) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule()
        target_inode = target.stat().st_ino
        retained_container: Path | None = None
        marker: Path | None = None

        def inject_unproven_entry(source, prepared, *, prepared_root, rule):
            nonlocal retained_container, marker
            retained_container = prepared.parent
            marker = prepared / "unproven-marker"
            marker.write_bytes(b"must-survive\n")
            raise SYNC_MODULE.SyncError("injected public-copy failure")

        try:
            with (
                mock.patch.object(
                    SYNC_MODULE,
                    "_copy_regular_file_overlay_public_source_to_prepared",
                    side_effect=inject_unproven_entry,
                ),
                mock.patch.object(
                    SYNC_MODULE,
                    "_rename_regular_file_overlay_noreplace",
                    wraps=SYNC_MODULE._rename_regular_file_overlay_noreplace,
                ) as rename_mock,
            ):
                with self.assertRaisesRegex(
                    SYNC_MODULE.SyncError,
                    "injected public-copy failure.*"
                    "external prepared tree retained at",
                ):
                    SYNC_MODULE.sync_sources(
                        self.repo_root,
                        self.source_root,
                        (rule,),
                    )

            self.assertIsNotNone(marker)
            self.assertEqual(marker.read_bytes(), b"must-survive\n")
            self.assertEqual(rename_mock.call_count, 0)
            self.assertEqual(target.stat().st_ino, target_inode)
            self.assertEqual((target / "old-marker").read_bytes(), b"old\n")
        finally:
            if retained_container is not None and retained_container.exists():
                shutil.rmtree(retained_container)

    def test_external_prepared_initial_manifest_rejects_injected_entry(self) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule()
        target_inode = target.stat().st_ino
        real_pin = SYNC_MODULE._pin_regular_file_overlay_child_directory
        retained_container: Path | None = None
        marker: Path | None = None

        def pin_then_inject(stack, parent, name, *, path, label):
            nonlocal retained_container, marker
            pinned = real_pin(
                stack,
                parent,
                name,
                path=path,
                label=label,
            )
            if label == "prepared public root":
                retained_container = path.parent
                marker = path / "pre-manifest-marker"
                marker.write_bytes(b"must-survive\n")
            return pinned

        try:
            with (
                mock.patch.object(
                    SYNC_MODULE,
                    "_pin_regular_file_overlay_child_directory",
                    side_effect=pin_then_inject,
                ),
                mock.patch.object(
                    SYNC_MODULE,
                    "_rename_regular_file_overlay_noreplace",
                    wraps=SYNC_MODULE._rename_regular_file_overlay_noreplace,
                ) as rename_mock,
            ):
                with self.assertRaisesRegex(
                    SYNC_MODULE.SyncError,
                    "initial external prepared root is not empty.*"
                    "external prepared tree retained at",
                ):
                    SYNC_MODULE.sync_sources(
                        self.repo_root,
                        self.source_root,
                        (rule,),
                    )

            self.assertIsNotNone(marker)
            self.assertEqual(marker.read_bytes(), b"must-survive\n")
            self.assertEqual(rename_mock.call_count, 0)
            self.assertEqual(target.stat().st_ino, target_inode)
            self.assertEqual((target / "old-marker").read_bytes(), b"old\n")
        finally:
            if retained_container is not None and retained_container.exists():
                shutil.rmtree(retained_container)

    def test_external_prepared_container_symlink_rebind_is_not_followed(self) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule()
        target_inode = target.stat().st_ino
        decoy = self.root / "external-prepared-decoy"
        decoy.mkdir(mode=0o700)
        (decoy / "do-not-delete").write_bytes(b"decoy\n")
        real_pin = SYNC_MODULE._pin_regular_file_overlay_child_directory
        retained_container: Path | None = None

        def rebind_before_pin(stack, parent, name, *, path, label):
            nonlocal retained_container
            if label != "external prepared container":
                return real_pin(
                    stack,
                    parent,
                    name,
                    path=path,
                    label=label,
                )
            retained_container = path
            saved = path.with_name(f".{path.name}.created")
            path.rename(saved)
            path.symlink_to(decoy, target_is_directory=True)
            try:
                return real_pin(
                    stack,
                    parent,
                    name,
                    path=path,
                    label=label,
                )
            finally:
                path.unlink()
                saved.rename(path)

        try:
            with (
                mock.patch.object(
                    SYNC_MODULE,
                    "_pin_regular_file_overlay_child_directory",
                    side_effect=rebind_before_pin,
                ),
                mock.patch.object(
                    SYNC_MODULE,
                    "_rename_regular_file_overlay_noreplace",
                    wraps=SYNC_MODULE._rename_regular_file_overlay_noreplace,
                ) as rename_mock,
            ):
                with self.assertRaisesRegex(
                    SYNC_MODULE.SyncError,
                    "cannot pin regular-file overlay external prepared container.*"
                    "external prepared tree retained at",
                ):
                    SYNC_MODULE.sync_sources(
                        self.repo_root,
                        self.source_root,
                        (rule,),
                    )

            self.assertEqual((decoy / "do-not-delete").read_bytes(), b"decoy\n")
            self.assertEqual(rename_mock.call_count, 0)
            self.assertEqual(target.stat().st_ino, target_inode)
            self.assertEqual((target / "old-marker").read_bytes(), b"old\n")
        finally:
            if retained_container is not None and retained_container.exists():
                shutil.rmtree(retained_container)

    def test_external_prepared_root_pin_failure_reports_retained_path(self) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule()
        target_inode = target.stat().st_ino
        real_pin = SYNC_MODULE._pin_regular_file_overlay_child_directory
        retained_container: Path | None = None

        def fail_root_pin(stack, parent, name, *, path, label):
            nonlocal retained_container
            if label == "prepared public root":
                retained_container = path.parent
                raise SYNC_MODULE.SyncError("injected prepared-root pin failure")
            return real_pin(
                stack,
                parent,
                name,
                path=path,
                label=label,
            )

        try:
            with (
                mock.patch.object(
                    SYNC_MODULE,
                    "_pin_regular_file_overlay_child_directory",
                    side_effect=fail_root_pin,
                ),
                mock.patch.object(
                    SYNC_MODULE,
                    "_rename_regular_file_overlay_noreplace",
                    wraps=SYNC_MODULE._rename_regular_file_overlay_noreplace,
                ) as rename_mock,
            ):
                with self.assertRaisesRegex(
                    SYNC_MODULE.SyncError,
                    "injected prepared-root pin failure.*"
                    "external prepared tree retained at",
                ):
                    SYNC_MODULE.sync_sources(
                        self.repo_root,
                        self.source_root,
                        (rule,),
                    )

            self.assertIsNotNone(retained_container)
            self.assertTrue((retained_container / target.name).is_dir())
            self.assertEqual(rename_mock.call_count, 0)
            self.assertEqual(target.stat().st_ino, target_inode)
            self.assertEqual((target / "old-marker").read_bytes(), b"old\n")
        finally:
            if retained_container is not None and retained_container.exists():
                shutil.rmtree(retained_container)

    def test_external_prepared_interrupt_reports_path_without_add_note(self) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule()
        target_inode = target.stat().st_ino
        errors = io.StringIO()

        with (
            mock.patch.object(
                SYNC_MODULE,
                "_copy_regular_file_overlay_public_source_to_prepared",
                side_effect=KeyboardInterrupt,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_base_exception_note_method",
                return_value=None,
            ),
            contextlib.redirect_stderr(errors),
        ):
            with self.assertRaises(KeyboardInterrupt):
                SYNC_MODULE.sync_sources(
                    self.repo_root,
                    self.source_root,
                    (rule,),
                )

        retained = list(self.external_prepared_parent.iterdir())
        self.assertEqual(len(retained), 1)
        self.assertIn(
            f"external prepared tree retained at {retained[0]}",
            errors.getvalue(),
        )
        self.assertEqual(target.stat().st_ino, target_inode)
        self.assertEqual((target / "old-marker").read_bytes(), b"old\n")

    def test_external_post_mkdir_interrupt_reports_possible_retained_path(self) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule()
        target_inode = target.stat().st_ino
        real_mkdir = SYNC_MODULE.os.mkdir
        errors = io.StringIO()

        def create_then_interrupt(path, mode=0o777, *, dir_fd=None):
            real_mkdir(path, mode, dir_fd=dir_fd)
            if str(path).startswith(f".{target.name}.prepared."):
                raise KeyboardInterrupt

        with (
            mock.patch.object(
                SYNC_MODULE.os,
                "mkdir",
                side_effect=create_then_interrupt,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_base_exception_note_method",
                return_value=None,
            ),
            contextlib.redirect_stderr(errors),
        ):
            with self.assertRaises(KeyboardInterrupt):
                SYNC_MODULE.sync_sources(
                    self.repo_root,
                    self.source_root,
                    (rule,),
                )

        retained = list(self.external_prepared_parent.iterdir())
        self.assertEqual(len(retained), 1)
        self.assertIn(
            f"external prepared tree may be retained at {retained[0]}",
            errors.getvalue(),
        )
        self.assertEqual(target.stat().st_ino, target_inode)
        self.assertEqual((target / "old-marker").read_bytes(), b"old\n")

    def test_external_prepared_retention_validation_rejects_root_rebind(
        self,
    ) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule()
        target_inode = target.stat().st_ino
        real_manifest_assert = SYNC_MODULE._assert_regular_file_overlay_tree_manifest
        rebound = False
        decoy_survived = False

        def rebind_retained_root(parent_descriptor, name, manifest, *, label):
            nonlocal rebound, decoy_survived
            if label != "retained external prepared source":
                return real_manifest_assert(
                    parent_descriptor,
                    name,
                    manifest,
                    label=label,
                )
            container = next(self.external_prepared_parent.iterdir())
            visible = container / name
            saved = container / f".{name}.expected"
            visible.rename(saved)
            visible.mkdir(mode=0o700)
            marker = visible / "do-not-delete"
            marker.write_bytes(b"decoy\n")
            rebound = True
            try:
                return real_manifest_assert(
                    parent_descriptor,
                    name,
                    manifest,
                    label=label,
                )
            finally:
                decoy_survived = marker.read_bytes() == b"decoy\n"
                shutil.rmtree(visible)
                saved.rename(visible)

        with (
            mock.patch.object(
                SYNC_MODULE,
                "_assert_regular_file_overlay_tree_manifest",
                side_effect=rebind_retained_root,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_rename_regular_file_overlay_noreplace",
                wraps=SYNC_MODULE._rename_regular_file_overlay_noreplace,
            ) as rename_mock,
        ):
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "retained external prepared source tree root binding changed.*"
                "recovery scope retained for inspection.*"
                "external prepared tree retained at",
            ):
                SYNC_MODULE.sync_sources(
                    self.repo_root,
                    self.source_root,
                    (rule,),
                )

        self.assertTrue(rebound)
        self.assertTrue(decoy_survived)
        self.assertEqual(rename_mock.call_count, 0)
        self.assertEqual(target.stat().st_ino, target_inode)
        self.assertEqual((target / "old-marker").read_bytes(), b"old\n")

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

    def test_regular_file_overlay_rejects_symlink_source(self) -> None:
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

    def test_regular_file_overlay_rejects_hard_linked_source(self) -> None:
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

    def test_regular_file_overlay_source_read_rejects_append_at_initial_size_plus_one(
        self,
    ) -> None:
        source = self.repo_root / "private" / "catalog.json"
        source.parent.mkdir()
        source.write_bytes(b"private\n")
        real_read = SYNC_MODULE.os.read
        requested_sizes: list[int] = []
        appended = False

        def append_after_first_read(descriptor, size):
            nonlocal appended
            requested_sizes.append(size)
            data = real_read(descriptor, size)
            if not appended:
                with source.open("ab") as stream:
                    stream.write(b"x")
                appended = True
            return data

        with mock.patch.object(
            SYNC_MODULE.os,
            "read",
            side_effect=append_after_first_read,
        ):
            with self.assertRaisesRegex(SYNC_MODULE.SyncError, "changed while reading"):
                SYNC_MODULE._read_regular_file_overlay_source(
                    self.repo_root,
                    Path("private/catalog.json"),
                )

        self.assertEqual(requested_sizes, [len(b"private\n") + 1, 1])

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
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "File exists",
            ):
                SYNC_MODULE._rename_regular_file_overlay_noreplace(
                    primitive,
                    1,
                    "source",
                    2,
                    "target",
                )
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
        target = self.repo_root / "bounded-readback"
        calls: list[int] = []

        def appending_read(_descriptor, size):
            calls.append(size)
            if len(calls) > 1:
                raise AssertionError("target read-back exceeded its byte budget")
            return b"x" * size

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "private regular-file overlay target verification failed",
        ):
            with self._regular_file_overlay_staging_directory(target) as scope:
                with contextlib.ExitStack() as stack:
                    staging = SYNC_MODULE._pin_or_create_regular_file_overlay_directory(
                        stack,
                        scope.container,
                        "candidate",
                        path=scope.path / "candidate",
                        label="staged target",
                        private=True,
                    )
                    with mock.patch.object(
                        SYNC_MODULE.os,
                        "read",
                        side_effect=appending_read,
                    ):
                        SYNC_MODULE._create_prepared_regular_file_overlay_value(
                            b"private\n",
                            staging,
                            "catalog.json",
                            relative=Path("catalog.json"),
                            staging_scope=scope,
                            manifest_builder=SYNC_MODULE._RegularFileOverlayManifestBuilder(),
                        )

        self.assertEqual(calls, [len(b"private\n") + 1])
        self.assertEqual(
            (scope.path / "candidate/catalog.json").read_bytes(),
            b"private\n",
        )

    def test_regular_file_overlay_prepared_copy_rejects_append_at_initial_size_plus_one(
        self,
    ) -> None:
        source = self.root / "prepared-copy-source.txt"
        source.write_bytes(b"prepared\n")
        target = self.repo_root / "prepared-copy-target"
        real_read = SYNC_MODULE.os.read
        requested_sizes: list[int] = []
        appended = False

        def append_after_first_read(descriptor, size):
            nonlocal appended
            requested_sizes.append(size)
            data = real_read(descriptor, size)
            if not appended:
                with source.open("ab") as stream:
                    stream.write(b"x")
                appended = True
            return data

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "prepared overlay source grew while copying",
        ):
            with self._regular_file_overlay_staging_directory(target) as scope:
                with contextlib.ExitStack() as stack:
                    source_parent = SYNC_MODULE._pin_regular_file_overlay_directory(
                        stack,
                        source.parent,
                        label="prepared source parent",
                    )
                    destination = (
                        SYNC_MODULE._pin_or_create_regular_file_overlay_directory(
                            stack,
                            scope.container,
                            "candidate",
                            path=scope.path / "candidate",
                            label="staged target",
                            private=True,
                        )
                    )
                    expected = self._regular_file_overlay_manifest_entry_for_file(
                        source
                    )
                    with mock.patch.object(
                        SYNC_MODULE.os,
                        "read",
                        side_effect=append_after_first_read,
                    ):
                        SYNC_MODULE._copy_prepared_regular_file_overlay_file(
                            source_parent,
                            source.name,
                            destination,
                            "copied.txt",
                            relative=Path("copied.txt"),
                            expected=expected,
                            policy_target=Path("test/candidate"),
                            staging_scope=scope,
                            copy_budget=SYNC_MODULE._RegularFileOverlayCopyBudget(),
                            manifest_builder=SYNC_MODULE._RegularFileOverlayManifestBuilder(),
                        )

        self.assertEqual(requested_sizes, [len(b"prepared\n") + 1, 1])
        self.assertFalse((scope.path / "candidate/copied.txt").exists())

    def test_regular_file_overlay_prepared_copy_fifo_swap_cannot_block(self) -> None:
        source = self.root / "prepared-copy-fifo-source.txt"
        source.write_bytes(b"prepared\n")
        target = self.repo_root / "prepared-copy-fifo-target"
        real_open = SYNC_MODULE.os.open
        swapped = False

        def swap_source_then_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if path == source.name and not swapped:
                source.unlink()
                os.mkfifo(source)
                swapped = True
                self.assertTrue(flags & os.O_NONBLOCK)
            return real_open(path, flags, *args, **kwargs)

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "prepared overlay source changed while opening",
        ):
            with self._regular_file_overlay_staging_directory(target) as scope:
                with contextlib.ExitStack() as stack:
                    source_parent = SYNC_MODULE._pin_regular_file_overlay_directory(
                        stack,
                        source.parent,
                        label="prepared source parent",
                    )
                    destination = (
                        SYNC_MODULE._pin_or_create_regular_file_overlay_directory(
                            stack,
                            scope.container,
                            "candidate",
                            path=scope.path / "candidate",
                            label="staged target",
                            private=True,
                        )
                    )
                    expected = self._regular_file_overlay_manifest_entry_for_file(
                        source
                    )
                    with mock.patch.object(
                        SYNC_MODULE.os,
                        "open",
                        side_effect=swap_source_then_open,
                    ):
                        SYNC_MODULE._copy_prepared_regular_file_overlay_file(
                            source_parent,
                            source.name,
                            destination,
                            "copied.txt",
                            relative=Path("copied.txt"),
                            expected=expected,
                            policy_target=Path("test/candidate"),
                            staging_scope=scope,
                            copy_budget=SYNC_MODULE._RegularFileOverlayCopyBudget(),
                            manifest_builder=SYNC_MODULE._RegularFileOverlayManifestBuilder(),
                        )

        self.assertTrue(swapped)
        self.assertTrue(stat.S_ISFIFO(source.lstat().st_mode))
        self.assertFalse((scope.path / "candidate/copied.txt").exists())

    def test_regular_file_overlay_prepared_copy_rejects_tree_byte_limit_before_read(
        self,
    ) -> None:
        source = self.root / "oversized-prepared-copy-source.txt"
        source.touch()
        os.truncate(source, SYNC_MODULE.MAX_REGULAR_FILE_OVERLAY_TREE_BYTES + 1)
        target = self.repo_root / "oversized-prepared-copy-target"

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "prepared target tree exceeds",
        ):
            with self._regular_file_overlay_staging_directory(target) as scope:
                with contextlib.ExitStack() as stack:
                    source_parent = SYNC_MODULE._pin_regular_file_overlay_directory(
                        stack,
                        source.parent,
                        label="prepared source parent",
                    )
                    destination = (
                        SYNC_MODULE._pin_or_create_regular_file_overlay_directory(
                            stack,
                            scope.container,
                            "candidate",
                            path=scope.path / "candidate",
                            label="staged target",
                            private=True,
                        )
                    )
                    metadata = source.stat()
                    expected = SYNC_MODULE._RegularFileOverlayTreeEntry(
                        relative_parts=(source.name,),
                        kind="file",
                        identity=SYNC_MODULE._overlay_file_identity(metadata),
                        size=metadata.st_size,
                        sha256="0" * 64,
                    )
                    with mock.patch.object(
                        SYNC_MODULE.os,
                        "read",
                        side_effect=AssertionError("oversized source must not be read"),
                    ):
                        SYNC_MODULE._copy_prepared_regular_file_overlay_file(
                            source_parent,
                            source.name,
                            destination,
                            "copied.txt",
                            relative=Path("copied.txt"),
                            expected=expected,
                            policy_target=Path("test/candidate"),
                            staging_scope=scope,
                            copy_budget=SYNC_MODULE._RegularFileOverlayCopyBudget(),
                            manifest_builder=SYNC_MODULE._RegularFileOverlayManifestBuilder(),
                        )

        self.assertFalse((scope.path / "candidate/copied.txt").exists())

    def test_regular_file_overlay_prepared_scan_bounds_ignored_entries(self) -> None:
        source = self.root / "bounded-prepared-scan"
        source.mkdir()
        for index in range(3):
            (source / f"ignored-{index}.pyc").write_bytes(b"ignored\n")
        target = self.repo_root / "bounded-prepared-scan-target"
        budget = SYNC_MODULE._RegularFileOverlayCopyBudget()

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "bounded entry capacity",
        ):
            with self._regular_file_overlay_staging_directory(target) as scope:
                with contextlib.ExitStack() as stack:
                    source_root = SYNC_MODULE._pin_regular_file_overlay_directory(
                        stack,
                        source,
                        label="prepared source root",
                    )
                    destination = (
                        SYNC_MODULE._pin_or_create_regular_file_overlay_directory(
                            stack,
                            scope.container,
                            "candidate",
                            path=scope.path / "candidate",
                            label="staged target",
                            private=True,
                        )
                    )
                    with mock.patch.object(
                        SYNC_MODULE,
                        "MAX_REGULAR_FILE_OVERLAY_TREE_ENTRIES",
                        2,
                    ):
                        SYNC_MODULE._copy_prepared_regular_file_overlay_directory(
                            stack,
                            source_root,
                            destination,
                            staging_scope=scope,
                            relative=Path(),
                            policy_target=Path("test/candidate"),
                            expected_entries={},
                            visited_entries=set(),
                            overlay_data={},
                            applied_overlays=set(),
                            copy_budget=budget,
                            manifest_builder=SYNC_MODULE._RegularFileOverlayManifestBuilder(),
                        )

        self.assertEqual(budget.scanned_entries, 0)
        self.assertEqual(budget.entries, 0)
        self.assertEqual(list((scope.path / "candidate").iterdir()), [])

    def test_regular_file_overlay_manifest_shares_entry_budget_across_depth(
        self,
    ) -> None:
        source = self.root / "bounded-manifest-scan"
        (source / "nested").mkdir(parents=True)
        (source / "sibling.txt").write_bytes(b"sibling\n")
        (source / "nested" / "child.txt").write_bytes(b"child\n")
        descriptor = SYNC_MODULE._open_regular_file_overlay_root(
            source,
            label="bounded manifest",
        )
        try:
            with mock.patch.object(
                SYNC_MODULE,
                "MAX_REGULAR_FILE_OVERLAY_TREE_ENTRIES",
                2,
            ):
                with self.assertRaisesRegex(
                    SYNC_MODULE.SyncError,
                    "bounded entry capacity",
                ):
                    SYNC_MODULE._capture_regular_file_overlay_tree_manifest(
                        descriptor,
                        label="bounded manifest",
                    )
        finally:
            os.close(descriptor)

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

    def test_regular_file_overlay_rejects_staged_file_mutation_before_install(
        self,
    ) -> None:
        target = self._create_regular_file_overlay_target("late-file-mutation")
        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "staged target.*(binding changed|verification failed)",
        ):
            with self._regular_file_overlay_staging_directory(target) as scope:
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

    def test_regular_file_overlay_rejects_whole_tree_mutation_before_install(
        self,
    ) -> None:
        for mutation in ("add", "modify", "remove"):
            with self.subTest(mutation=mutation):
                target = self._create_regular_file_overlay_target(
                    f"whole-tree-{mutation}"
                )
                with self.assertRaisesRegex(
                    SYNC_MODULE.SyncError,
                    "exact tree manifest changed",
                ):
                    with self._regular_file_overlay_staging_directory(target) as scope:
                        stack, staging, binding = (
                            self._prepare_scoped_regular_file_overlay_candidate(
                                scope,
                                extra_files={Path("fixtures/value.txt"): b"safe\n"},
                            )
                        )
                        with stack:
                            if mutation == "add":
                                (staging / "fixtures/unexpected.txt").write_bytes(
                                    b"added\n"
                                )
                            elif mutation == "modify":
                                (staging / "fixtures/value.txt").write_bytes(
                                    b"changed\n"
                                )
                            else:
                                (staging / "fixtures/value.txt").unlink()
                            SYNC_MODULE._replace_target_with_regular_file_overlays(
                                target,
                                staging,
                                (binding,),
                                staging_scope=scope,
                            )

                self.assertEqual((target / "catalog.json").read_bytes(), b"public\n")

    def test_regular_file_overlay_rejects_whole_tree_mutation_after_install(
        self,
    ) -> None:
        target = self._create_regular_file_overlay_target("whole-tree-post-install")
        real_rename = SYNC_MODULE._rename_regular_file_overlay_noreplace
        rename_calls = 0

        def mutate_after_install(*args):
            nonlocal rename_calls
            real_rename(*args)
            rename_calls += 1
            if rename_calls == 2:
                (target / "fixtures/value.txt").write_bytes(b"changed\n")

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "installed candidate left live",
        ) as raised:
            with self._regular_file_overlay_staging_directory(target) as scope:
                stack, staging, binding = (
                    self._prepare_scoped_regular_file_overlay_candidate(
                        scope,
                        extra_files={Path("fixtures/value.txt"): b"safe\n"},
                    )
                )
                with (
                    stack,
                    mock.patch.object(
                        SYNC_MODULE,
                        "_rename_regular_file_overlay_noreplace",
                        side_effect=mutate_after_install,
                    ),
                ):
                    SYNC_MODULE._replace_target_with_regular_file_overlays(
                        target,
                        staging,
                        (binding,),
                        staging_scope=scope,
                    )

        self.assertEqual(rename_calls, 2)
        message = str(raised.exception)
        self.assertIn("original transaction error:", message)
        self.assertIn("exact tree manifest changed", message)
        self.assertIn("only the candidate root identity matched", message)
        self.assertIn("exact contents are unverified", message)
        self.assertIn("must be treated as untrusted", message)
        self.assertIn("prior target root identity retained at", message)
        self.assertIn("contents are unverified", message)
        self.assertNotIn("pinned candidate", message)
        self.assertNotIn("verified prior target", message)
        self.assertEqual(
            (target / "fixtures/value.txt").read_bytes(),
            b"changed\n",
        )

    def test_regular_file_overlay_rejects_staging_root_replacement_before_install(
        self,
    ) -> None:
        target = self._create_regular_file_overlay_target("late-root-replacement")
        saved = self.root / "held-original-staging-root"
        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "staged target root binding changed",
        ):
            with self._regular_file_overlay_staging_directory(target) as scope:
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

    def test_regular_file_overlay_retains_post_install_mutation_and_prior_target(
        self,
    ) -> None:
        target = self._create_regular_file_overlay_target("post-install-mutation")
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
            "installed candidate left live",
        ):
            with self._regular_file_overlay_staging_directory(target) as scope:
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
        self.assertEqual((target / "catalog.json").read_bytes(), b"mutated\n")
        self.assertFalse((scope.path / "candidate").exists())
        recovery = list(
            scope.path.glob(f"{SYNC_MODULE.REGULAR_FILE_OVERLAY_BACKUP_PREFIX}*")
        )
        self.assertEqual(len(recovery), 1)
        self.assertEqual((recovery[0] / "catalog.json").read_bytes(), b"public\n")

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
            "candidate retained in recovery scope",
        ):
            with self._regular_file_overlay_staging_directory(target) as scope:
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

    def test_regular_file_overlay_source_rebind_fails_forward_without_restore(
        self,
    ) -> None:
        target = self._create_regular_file_overlay_target("source-rebind")
        real_rename = SYNC_MODULE._rename_regular_file_overlay_noreplace
        calls = 0

        def rebind_candidate_source(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                source_parent_descriptor = args[1]
                source_name = args[2]
                os.rename(
                    source_name,
                    f"{source_name}-saved",
                    src_dir_fd=source_parent_descriptor,
                    dst_dir_fd=source_parent_descriptor,
                )
                os.mkdir(source_name, 0o700, dir_fd=source_parent_descriptor)
                unknown_descriptor = os.open(
                    source_name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=source_parent_descriptor,
                )
                try:
                    file_descriptor = os.open(
                        "catalog.json",
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=unknown_descriptor,
                    )
                    try:
                        os.write(file_descriptor, b"unknown\n")
                    finally:
                        os.close(file_descriptor)
                finally:
                    os.close(unknown_descriptor)
            return real_rename(*args)

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "candidate binding is ambiguous.*untrusted live target",
        ):
            with self._regular_file_overlay_staging_directory(target) as scope:
                scope_path = scope.path
                stack, staging, binding = (
                    self._prepare_scoped_regular_file_overlay_candidate(scope)
                )
                with (
                    stack,
                    mock.patch.object(
                        SYNC_MODULE,
                        "_rename_regular_file_overlay_noreplace",
                        side_effect=rebind_candidate_source,
                    ),
                ):
                    SYNC_MODULE._replace_target_with_regular_file_overlays(
                        target,
                        staging,
                        (binding,),
                        staging_scope=scope,
                    )

        self.assertEqual(calls, 2)
        self.assertEqual((target / "catalog.json").read_bytes(), b"unknown\n")
        self.assertEqual(
            (scope_path / "candidate-saved/catalog.json").read_bytes(),
            b"private\n",
        )
        backups = list(
            scope_path.glob(f"{SYNC_MODULE.REGULAR_FILE_OVERLAY_BACKUP_PREFIX}*")
        )
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "catalog.json").read_bytes(), b"public\n")

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
            with self._regular_file_overlay_staging_directory(target) as scope:
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
            "candidate retained in recovery scope",
        ):
            with self._regular_file_overlay_staging_directory(target) as scope:
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

    def test_regular_file_overlay_recovery_error_reports_transaction_error(
        self,
    ) -> None:
        target = self._create_regular_file_overlay_target("recovery-error-detail")
        real_register = SYNC_MODULE._register_regular_file_overlay_retained_entry
        rebound = False

        def register_then_rebind(scope, name, entry):
            nonlocal rebound
            real_register(scope, name, entry)
            if not name.startswith(SYNC_MODULE.REGULAR_FILE_OVERLAY_BACKUP_PREFIX):
                return
            os.rename(
                name,
                f"{name}-saved",
                src_dir_fd=scope.container.descriptor,
                dst_dir_fd=scope.container.descriptor,
            )
            descriptor = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=scope.container.descriptor,
            )
            try:
                os.write(descriptor, b"unknown\n")
            finally:
                os.close(descriptor)
            rebound = True

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "prior target binding is unknown",
        ) as raised:
            with self._regular_file_overlay_staging_directory(target) as scope:
                stack, staging, binding = (
                    self._prepare_scoped_regular_file_overlay_candidate(scope)
                )
                with (
                    stack,
                    mock.patch.object(
                        SYNC_MODULE,
                        "_register_regular_file_overlay_retained_entry",
                        side_effect=register_then_rebind,
                    ),
                ):
                    SYNC_MODULE._replace_target_with_regular_file_overlays(
                        target,
                        staging,
                        (binding,),
                        staging_scope=scope,
                    )

        message = str(raised.exception)
        self.assertTrue(rebound)
        self.assertIn("original transaction error:", message)
        self.assertIn("retained recovery entry binding changed", message)
        self.assertIn("only the candidate root identity matched", message)
        self.assertIn("exact contents are unverified", message)
        self.assertIn("must be treated as untrusted", message)

    def test_regular_file_overlay_noreplace_capability_fails_before_target_mutation(
        self,
    ) -> None:
        target = self._create_regular_file_overlay_target("missing-noreplace")
        old_target_inode = target.stat().st_ino
        scope_path: Path | None = None
        with self.assertRaises(SYNC_MODULE.SyncError) as raised:
            with self._regular_file_overlay_staging_directory(target) as scope:
                scope_path = scope.path
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

        self.assertIn("noreplace unavailable", str(raised.exception))
        self.assertIsNotNone(scope_path)
        self.assertIn(str(scope_path), str(raised.exception))
        self.assertTrue((scope_path / "candidate").is_dir())
        self.assertEqual(target.stat().st_ino, old_target_inode)
        self.assertEqual((target / "catalog.json").read_bytes(), b"public\n")

    def test_regular_file_overlay_plain_exception_reports_recovery_scope(
        self,
    ) -> None:
        target = self._create_regular_file_overlay_target("plain-exception")
        scope_path: Path | None = None

        with self.assertRaises(SYNC_MODULE.SyncError) as raised:
            with self._regular_file_overlay_staging_directory(target) as scope:
                scope_path = scope.path
                (scope.path / "candidate").mkdir()
                raise ValueError("injected non-sync failure")

        self.assertIsInstance(raised.exception.__cause__, ValueError)
        self.assertIn("ValueError: injected non-sync failure", str(raised.exception))
        self.assertIsNotNone(scope_path)
        self.assertIn(str(scope_path), str(raised.exception))
        self.assertTrue((scope_path / "candidate").is_dir())
        self.assertEqual((target / "catalog.json").read_bytes(), b"public\n")

    def test_regular_file_overlay_preserves_root_bound_recovery_without_path_cleanup(
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
            with self._regular_file_overlay_staging_directory(target) as scope:
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
                "root-bound recovery backup",
                "retained recovery entry",
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
            with self._regular_file_overlay_staging_directory(target):
                self.fail("bounded recovery root must fail before staging")

        self.assertEqual(target.stat().st_ino, original_inode)
        self.assertEqual((target / "catalog.json").read_bytes(), b"public\n")

    def test_regular_file_overlay_completed_scope_only_closes_capabilities(
        self,
    ) -> None:
        target = self.repo_root / "completed-scope"
        target.mkdir()
        (target / "catalog.json").write_text("public\n", encoding="utf-8")
        real_assert_directory = (
            SYNC_MODULE._assert_regular_file_overlay_directory_binding
        )
        real_assert_scope = SYNC_MODULE._assert_regular_file_overlay_scope_binding
        real_assert_retained = SYNC_MODULE._assert_regular_file_overlay_retained_entries
        real_assert_entry = SYNC_MODULE._assert_regular_file_overlay_entry_binding
        committed = False

        def reject_directory_validation(*args, **kwargs):
            if committed:
                raise AssertionError("completed scope performed post-commit validation")
            return real_assert_directory(*args, **kwargs)

        def reject_scope_validation(*args, **kwargs):
            if committed:
                raise AssertionError("completed scope performed post-commit validation")
            return real_assert_scope(*args, **kwargs)

        def reject_retained_validation(*args, **kwargs):
            if committed:
                raise AssertionError("completed scope performed post-commit validation")
            return real_assert_retained(*args, **kwargs)

        def reject_entry_validation(*args, **kwargs):
            if committed:
                raise AssertionError("completed scope performed post-commit validation")
            return real_assert_entry(*args, **kwargs)

        with (
            mock.patch.object(
                SYNC_MODULE,
                "_assert_regular_file_overlay_directory_binding",
                side_effect=reject_directory_validation,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_assert_regular_file_overlay_scope_binding",
                side_effect=reject_scope_validation,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_assert_regular_file_overlay_retained_entries",
                side_effect=reject_retained_validation,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_assert_regular_file_overlay_entry_binding",
                side_effect=reject_entry_validation,
            ),
        ):
            with self._regular_file_overlay_staging_directory(target) as scope:
                scope_path = scope.path
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
                committed = True

        self.assertTrue(committed)
        self.assertEqual((target / "catalog.json").read_bytes(), b"private\n")
        recovery = list(
            scope_path.glob(f"{SYNC_MODULE.REGULAR_FILE_OVERLAY_BACKUP_PREFIX}*")
        )
        self.assertEqual(len(recovery), 1)
        self.assertEqual((recovery[0] / "catalog.json").read_bytes(), b"public\n")

    def test_regular_file_overlay_keyboard_interrupt_retains_prior_target(
        self,
    ) -> None:
        target = self.repo_root / "interrupt-installed"
        target.mkdir()
        (target / "catalog.json").write_text("public\n", encoding="utf-8")
        real_rename = SYNC_MODULE._rename_regular_file_overlay_noreplace
        calls = 0

        def interrupt_after_backup(*args):
            nonlocal calls
            real_rename(*args)
            calls += 1
            if calls == 1:
                raise KeyboardInterrupt

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "candidate retained in recovery scope",
        ):
            with self._regular_file_overlay_staging_directory(target) as scope:
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

        self.assertEqual(calls, 1)
        self.assertFalse(target.exists())
        self.assertTrue(scope_path.is_dir())
        self.assertEqual(
            (scope_path / "candidate" / "catalog.json").read_bytes(),
            b"private\n",
        )
        recovery = list(
            scope_path.glob(f"{SYNC_MODULE.REGULAR_FILE_OVERLAY_BACKUP_PREFIX}*")
        )
        self.assertEqual(len(recovery), 1)
        self.assertEqual((recovery[0] / "catalog.json").read_bytes(), b"public\n")

    def test_recovery_interrupt_reports_path_without_add_note(self) -> None:
        target = self.repo_root / "interrupt-reporting"
        errors = io.StringIO()
        scope_path: Path | None = None

        with (
            mock.patch.object(
                SYNC_MODULE,
                "_base_exception_note_method",
                return_value=None,
            ),
            contextlib.redirect_stderr(errors),
        ):
            with self.assertRaises(KeyboardInterrupt):
                with self._regular_file_overlay_staging_directory(target) as scope:
                    scope_path = scope.path
                    raise KeyboardInterrupt

        self.assertIsNotNone(scope_path)
        self.assertIn(
            f"recovery scope retained for inspection at {scope_path}",
            errors.getvalue(),
        )

    def test_recovery_post_mkdir_interrupt_reports_possible_retained_path(
        self,
    ) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule()
        target_inode = target.stat().st_ino
        real_mkdir = SYNC_MODULE.os.mkdir
        errors = io.StringIO()

        def create_then_interrupt(path, mode=0o777, *, dir_fd=None):
            real_mkdir(path, mode, dir_fd=dir_fd)
            if str(path).startswith(
                SYNC_MODULE.REGULAR_FILE_OVERLAY_RECOVERY_SCOPE_PREFIX
            ):
                raise KeyboardInterrupt

        mkdir_mock = mock.Mock(side_effect=create_then_interrupt)
        supported_dir_fd = frozenset(
            (set(SYNC_MODULE.os.supports_dir_fd) - {real_mkdir}) | {mkdir_mock}
        )
        with (
            mock.patch.object(SYNC_MODULE.os, "mkdir", mkdir_mock),
            mock.patch.object(
                SYNC_MODULE.os,
                "supports_dir_fd",
                supported_dir_fd,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_base_exception_note_method",
                return_value=None,
            ),
            contextlib.redirect_stderr(errors),
        ):
            with self.assertRaises(KeyboardInterrupt):
                SYNC_MODULE.sync_sources(
                    self.repo_root,
                    self.source_root,
                    (rule,),
                )

        recovery_root = self.repo_root / SYNC_MODULE.REGULAR_FILE_OVERLAY_RECOVERY_ROOT
        retained = list(recovery_root.iterdir())
        self.assertEqual(len(retained), 1)
        self.assertIn(
            "regular-file overlay recovery scope may be retained at "
            f"{retained[0]}",
            errors.getvalue(),
        )
        self.assertIn("external prepared tree retained at", errors.getvalue())
        self.assertEqual(target.stat().st_ino, target_inode)
        self.assertEqual((target / "old-marker").read_bytes(), b"old\n")

    def test_regular_file_overlay_final_rename_interrupt_retains_both_trees(
        self,
    ) -> None:
        target = self._create_regular_file_overlay_target("final-interrupt")
        real_rename = SYNC_MODULE._rename_regular_file_overlay_noreplace
        calls = 0

        def interrupt_after_final_rename(*args):
            nonlocal calls
            real_rename(*args)
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "installed candidate left live",
        ):
            with self._regular_file_overlay_staging_directory(target) as scope:
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

        self.assertEqual(calls, 2)
        self.assertEqual((target / "catalog.json").read_bytes(), b"private\n")
        self.assertFalse((scope_path / "candidate").exists())
        recovery = list(
            scope_path.glob(f"{SYNC_MODULE.REGULAR_FILE_OVERLAY_BACKUP_PREFIX}*")
        )
        self.assertEqual(len(recovery), 1)
        self.assertEqual((recovery[0] / "catalog.json").read_bytes(), b"public\n")

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
        self.assertEqual(rule.replacements, SYNC_MODULE.COMMON_JOEY_TEXT_REPLACEMENTS)
        obsolete_layout_replacements = {
            "REPO_ROOT = SKILL_ROOT.parents[1]",
            "(REPO_ROOT / relative).exists()",
            'with (REPO_ROOT / "agents/reviewer.toml").open("rb") as handle:',
        }
        self.assertTrue(
            obsolete_layout_replacements.isdisjoint(
                replacement.old for replacement in rule.replacements
            )
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

        private_catalog = REPO_ROOT / rule.regular_file_overlays[0].source
        private_catalog_stat = private_catalog.stat()
        self.assertEqual(private_catalog_stat.st_uid, os.getuid())
        self.assertFalse(private_catalog_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
        copied_private_catalog = self.repo_root / rule.regular_file_overlays[0].source
        copied_private_catalog.parent.mkdir(parents=True)
        shutil.copy2(private_catalog, copied_private_catalog)
        source = self.source_root / "example-repo" / "skill"
        source.mkdir(parents=True)
        (source / "catalog.json").write_bytes(b'{"pool":"public"}\n')
        test_rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/example"),
            regular_file_overlays=(
                SYNC_MODULE.RegularFileOverlay(
                    rule.regular_file_overlays[0].source,
                    Path("catalog.json"),
                ),
            ),
        )

        SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (test_rule,))

        target = self.repo_root / test_rule.target / "catalog.json"
        self.assertTrue(
            hmac.compare_digest(
                target.read_bytes(), copied_private_catalog.read_bytes()
            ),
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
        self.assertEqual(catalog.pool_version, "joey-private-v3")
        expected_authoring = {
            "access-a": ("access", "active"),
            "access-b": ("access", "active"),
            "access-c": ("access", "active"),
            "access-d": ("access", "active"),
            "access-e": ("access", "active"),
            "access-f": ("access", "active"),
            "access-g": ("access", "active"),
            "access-h": ("access", "active"),
            "access-i": ("access", "active"),
            "access-j": ("access", "active"),
            "access-expired": ("access", "expired"),
            "refresh-a": ("refresh", "active"),
            "refresh-b": ("refresh", "active"),
            "refresh-c": ("refresh", "active"),
            "refresh-d": ("refresh", "active"),
            "refresh-e": ("refresh", "active"),
            "refresh-f": ("refresh", "active"),
            "refresh-g": ("refresh", "active"),
            "refresh-h": ("refresh", "active"),
            "refresh-i": ("refresh", "active"),
            "refresh-j": ("refresh", "active"),
            "refresh-consumed": ("refresh", "consumed"),
            "id-a": ("id", "active"),
            "id-b": ("id", "active"),
            "id-c": ("id", "active"),
            "id-d": ("id", "active"),
            "id-e": ("id", "active"),
            "id-f": ("id", "active"),
            "id-g": ("id", "active"),
            "id-h": ("id", "active"),
            "id-i": ("id", "active"),
            "id-j": ("id", "active"),
            "api-key-a": ("api-key", "active"),
            "api-key-b": ("api-key", "active"),
            "api-key-c": ("api-key", "active"),
            "api-key-d": ("api-key", "active"),
            "api-key-e": ("api-key", "active"),
            "api-key-f": ("api-key", "active"),
            "api-key-g": ("api-key", "active"),
            "api-key-h": ("api-key", "active"),
            "api-key-i": ("api-key", "active"),
            "api-key-j": ("api-key", "active"),
            "bearer-a": ("bearer", "active"),
            "bearer-b": ("bearer", "active"),
            "bearer-c": ("bearer", "active"),
            "bearer-d": ("bearer", "active"),
            "bearer-e": ("bearer", "active"),
            "bearer-f": ("bearer", "active"),
            "bearer-g": ("bearer", "active"),
            "bearer-h": ("bearer", "active"),
            "bearer-i": ("bearer", "active"),
            "bearer-j": ("bearer", "active"),
        }
        expected_authoring_digests = {
            "access-a": "58daf468f4bf8efe2ae8dc70cc7f560986849e7ae12d5f37b6ff384173660949",
            "access-b": "2bb253074303e17640f50112e193b6785528316cb247aad010282d7fc72af278",
            "access-c": "aa43601b7e30e87c6f57ec4283a94014567f696f32b7873671a9a2cdd773a5ab",
            "access-d": "2162095cf7d35031b884dcc300ef3aaf7c09352c1d9a348cd28f7f3ad7ff044d",
            "access-e": "720a902d084068eafe495f605452134fd0defff08eea204f01fa1e273df7c646",
            "access-f": "f8f0b57889215532cff8b649c3bd8bba8d06bf8f392f9255aeae5ecbac3ac4ba",
            "access-g": "b6d5e218e2cfccb2217a3d8674e7711358583256472e25cf161bb8648647c584",
            "access-h": "dd79d8d0914e388424c2f843707a3ea41f6d193d09a733596a40c8d73ad31b55",
            "access-i": "03797e71bea3b550a352204d13018ab0093c086695881afea2cd4740c401b093",
            "access-j": "fdc7d8a6505b39d1ef058b0fa2d452e4256e94d0f9cffd6d437b5a7276089890",
            "access-expired": "bce04e6a1f6bc2c3359fe4132bd290863ba7fd03559842c4b0b9daa7b5663ab4",
            "refresh-a": "c28443d3517b1a1c7f838da8ae2c422c6cb9eca041679faebb2ecf2e8105e2cd",
            "refresh-b": "7f1fc893d30288dc8a8c31e81e3c104d1a00fb5a63cb4f8c78edfa5eb9f393e7",
            "refresh-c": "dea6d071dabff935154073ee2f59435222721a036e35dc4f3e394e4ce65064ac",
            "refresh-d": "b3d45b50277aa9f400545ea3fae9bf7ca45da116a387eda670988ba7cc16cd02",
            "refresh-e": "e9d48667654b7131f78dae7075a29170dd9e5089129fa3aa55163f03e550bdd3",
            "refresh-f": "6a999990e79fbccf2850b9185cfcf54c8f576ac8a9ff667a64d2e8b5fdd66c3b",
            "refresh-g": "7a1ab87487cde10c5c8fd17814b63c9bb5e1af095cc69ff27ac6c095e0d1f2e9",
            "refresh-h": "ba9da76205e1563fa0ea62255e45dea651937696123abb785bc60b3be6043f7c",
            "refresh-i": "8d35343032c4d236f99d7246e5c8da2442a04d52458b6899d8f6937457c2c23d",
            "refresh-j": "e32b08223b82cd7a146fefcb519f71b6b9808526d4ff390bc6443bdf538466d1",
            "refresh-consumed": "b0ba4734994dcb74e17a490c4e1cf8182ebb4a3ab9ffa8a239087a80b9d163f2",
            "id-a": "e56c3e8a834e46c7a6de2292ab026d113bf76d496c20eb5f926fbbe031351be8",
            "id-b": "635e5d26d428b4d6114e5aeb248f11315755ebe14f847ea3963941326569c293",
            "id-c": "f689afe1f0fc0683444787e0c4ab8a6ff2ef9925daee77a0bff49a0d50b8fe4f",
            "id-d": "1f1ea0c0c2878c5de74f13762cd5fb461d43bee7f4e057be856ee79caad66cc5",
            "id-e": "dbe516024dabb63129ed059750787fc3cc6e1bcda364128d50c41799e7e9a818",
            "id-f": "6f8156a1387a92b7b5b0a2415ad9fea7c00c864d85fb0100f42acfa61c4acc84",
            "id-g": "85bd04369c79b12cc572d33fdaf04e4ba7414dd88d487630cc6aa4e7848386c4",
            "id-h": "a3ec371ba33225f4d61b165302d7205e8a7c3f58c71ff51bcaab05792477c93f",
            "id-i": "036f27489928cf9bdde445dc27b4bf27aa02482064f0c9629283b6628ba414ed",
            "id-j": "bb71120a63735f02f282e9f2415caab024b4b432487250e9d903fa6cc83b96c7",
            "api-key-a": "0ac4cac80da9258c6db057fcf2f82c450c128631e6c306c82923eb2388955e38",
            "api-key-b": "f009beb73c74ce7f05999de6a934859a694b4c12e6d0c5152fd9c291ac22eb21",
            "api-key-c": "018db485def2985d26ea493b6ac1b64deb8de9b3a54d06cc3c89d5cea5b73d89",
            "api-key-d": "b7b237db49573ed8a01b8f16f4b27816a872ae6a78d40897fcee71d002aca33d",
            "api-key-e": "c8bfedea80a6cbb863329c4e6cbc62272ecce89c78940996ab986e5a2905cee9",
            "api-key-f": "55e507b5bc14d0ac4f7129daf81ce320176292bd0d72810a2a74a8eacbbdebd1",
            "api-key-g": "1a9dd681083e77fc5e5c3344e8011af9701eb5f03703cb59815ec82523acc03f",
            "api-key-h": "427a0b56b6cce5d300c8516edb74ef119225c2d683a11ccf58a669ee181024fb",
            "api-key-i": "d4549c7e7e2ae566b2c1e142eac813d9d66f870119cbb5e11f81a7cc2b2d9e39",
            "api-key-j": "141e9876cf7943ca50cc78e1ff1199f81b32bbbb83427bce1ebc2e5407963404",
            "bearer-a": "6baba51bd42263562f0fb352b1d180fedf4609528935a9437c7144517f48bd15",
            "bearer-b": "34f7af189914506e0866489d47e99c5a6206145ac156306af91277ebd196e9d1",
            "bearer-c": "09778c7dedcfdb984e10c30c5e5c780c8ed9cd8a6436b1aa283775cb88a727db",
            "bearer-d": "47f6198ab7ea4b1941a5af546d72a62c9af86322f223a2222a6bd9f0c3baba93",
            "bearer-e": "c57eb087ad16c58d7a003e743c596ac3153c218f828464eda207b717bae94b38",
            "bearer-f": "27b46cbb78ada99912c2b491ce083c182adec13880d32d8a3cbe742329fd01b2",
            "bearer-g": "0623cae7b884afc0e4e89e3388753a3ccdd546d781634a166735811bdac24af6",
            "bearer-h": "8e88b08419028de5c77ce863ba34efd29e3ed0115d28d50fe447b44bc0535f7b",
            "bearer-i": "7d48190dcc67b129d31376125480089349e90af8e776cf65be1e487acdcded14",
            "bearer-j": "5678bbb010e60d4c82b279fd13436ea9f72d61e6f2bd665eaae5810eba721801",
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
        portable_id = "portable-codex-runtime-master-generic-fixtures-v1"
        self.assertEqual(set(exemptions), {pat_id, portable_id})
        pat = exemptions[pat_id]
        portable = exemptions[portable_id]
        self.assertEqual(pat.repository, "Joey-Tools/codex-workflow-hygiene")
        self.assertEqual(portable.repository, "cha-op/portable-codex-runtime")
        self.assertEqual(
            pat.verified_master_tip, "95befb966cd93e0161ecb45099c124eac56cb52f"
        )
        self.assertEqual(
            portable.verified_master_tip,
            "83542fa2a29661c1422c108887bc13cb5bddd7eb",
        )
        self.assertEqual(len(pat.values), 1)
        self.assertEqual(len(portable.values), 16)
        self.assertEqual(sum(token.source_occurrences for token in pat.values), 1)
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
        self.assertEqual(
            {token.rule for token in portable.values},
            {"generic-secret-assignment"},
        )
        self.assertEqual(
            sum(len(exemption.values) for exemption in catalog.legacy_exemptions),
            17,
        )
        self.assertEqual(
            sum(
                token.source_occurrences
                for exemption in catalog.legacy_exemptions
                for token in exemption.values
            ),
            38,
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

    def test_release_workflows_use_vm_backed_runners(self) -> None:
        workflows = {
            "scheduled sync-release": (
                REPO_ROOT / ".github" / "workflows" / "scheduled-sync-release.yml",
                "sync-release",
            ),
            "release build": (
                REPO_ROOT / ".github" / "workflows" / "release.yml",
                "release",
            ),
            "release publish": (
                REPO_ROOT / ".github" / "workflows" / "release.yml",
                "publish",
            ),
        }

        for label, (path, job_name) in workflows.items():
            with self.subTest(job=label):
                workflow = path.read_text(encoding="utf-8")
                job = re.search(
                    rf"(?ms)^  {re.escape(job_name)}:\n(?P<body>.*?)(?=^  [-a-zA-Z0-9_]+:\n|\Z)",
                    workflow,
                )
                self.assertIsNotNone(job)
                runners = re.findall(
                    r"(?m)^    runs-on: *([^\n]+?) *$",
                    job.group("body"),
                )
                self.assertEqual(runners, ["ubuntu-latest"])

    def test_release_publish_steps_use_separate_immutable_releases_token(
        self,
    ) -> None:
        workflow_paths = (
            REPO_ROOT / ".github" / "workflows" / "release.yml",
            REPO_ROOT / ".github" / "workflows" / "scheduled-sync-release.yml",
        )
        immutable_token_env = (
            "IMMUTABLE_RELEASES_READ_TOKEN: "
            "${{ secrets.IMMUTABLE_RELEASES_READ_TOKEN }}"
        )

        for workflow_path in workflow_paths:
            with self.subTest(workflow=workflow_path.name):
                workflow = workflow_path.read_text(encoding="utf-8")
                publish_step = re.search(
                    r"(?ms)^      - name: Publish GitHub release\n"
                    r"(?P<body>.*?)(?=^      - name: |\Z)",
                    workflow,
                )
                self.assertIsNotNone(publish_step)
                publish_body = publish_step.group("body")
                self.assertIn("GITHUB_TOKEN: ${{ github.token }}", publish_body)
                self.assertIn(immutable_token_env, publish_body)
                self.assertIn("private_overlay_release.py publish", publish_body)
                self.assertEqual(workflow.count(immutable_token_env), 1)

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
        self.assertIn("\n  python-39-compatibility:\n", workflow)
        self.assertIn("Run Python 3.9 compatibility regressions", workflow)
        self.assertIn("\n  platform-safety:\n", workflow)
        self.assertIn("Run platform reconciliation safety tests", workflow)
        self.assertIn(
            "needs:\n      - python-39-compatibility\n      - platform-safety",
            workflow,
        )
        self.assertIn("\n  test:\n", workflow)
        self.assertIn("\n    name: test\n", workflow)
        self.assertIn("if: ${{ always() }}", workflow)
        self.assertIn(
            "needs:\n"
            "      - platform_tests\n"
            "      - python-39-compatibility\n"
            "      - platform-safety",
            workflow,
        )
        self.assertIn(
            "PLATFORM_TESTS_RESULT: ${{ needs.platform_tests.result }}",
            workflow,
        )
        self.assertIn(
            "PYTHON_39_RESULT: ${{ needs.python-39-compatibility.result }}",
            workflow,
        )
        self.assertIn(
            "PLATFORM_SAFETY_RESULT: ${{ needs.platform-safety.result }}",
            workflow,
        )
        self.assertIn('test "$PLATFORM_TESTS_RESULT" = "success"', workflow)
        self.assertIn('test "$PYTHON_39_RESULT" = "success"', workflow)
        self.assertIn('test "$PLATFORM_SAFETY_RESULT" = "success"', workflow)

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

    def test_agents_guidance_documents_wait_agent_timeout_contract(self) -> None:
        agents = (REPO_ROOT / "personal_codex" / "AGENTS.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("polling with `wait_agent`", agents)
        self.assertIn("omit `timeout_ms` to use the `30000` millisecond default", agents)
        self.assertIn("supported `10000`–`3600000` millisecond range", agents)
        self.assertIn("`30000`–`60000` for ordinary or reviewer polling", agents)
        self.assertIn("longer single waits are valid", agents)

    def test_agents_guidance_uses_canonical_named_review_policy(self) -> None:
        agents = (REPO_ROOT / "personal_codex" / "AGENTS.md").read_text(
            encoding="utf-8"
        )

        for anchor in (
            "A named single review is exactly one clear/fresh-context Codex `reviewer` agent",
            "repo-local playbook from the frozen review head",
            "never prebuild or inject the full diff into its prompt",
            "A named double review is that single-review agent plus an actual Anthropic Claude Code process",
            "Named double adds actual Claude Code",
            "The canonical Claude Code compatibility range is `>=2.1.211,<3.0.0`",
            "Claude Code `2.1.212` is the audited stream-schema baseline, not a global pin",
            "outside-workspace read exclusion as prompt/model scope",
            "Native `allowRead` is not a global host-read whitelist",
            "global `denyWrite` and critical-sensitive-root `denyRead`",
            "do not attest the final merged sandbox or managed permission arrays",
            "require `skills/review-orchestration-playbook/scripts/validate_claude_stream.py`",
            "A named triple review is the named double review plus a complete terminal provider-authored GitHub Codex findings payload",
            "every operating identity in `{hoteng, hoteng_cisco}` is unsupported",
            'user.login == "chatgpt-codex-connector[bot]"',
            "If GitHub Codex is unavailable because there is no PR or the integration/host/identity is unsupported, report `effective double`; never claim triple",
            "PR readiness adds CI, conversation, and branch/base gates, but no retired extra Codex gates",
            "never count a supplied-diff helper as a named lane",
            "possible cache or tool-result artifacts",
        ):
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, agents)

        for retired in (
            "mandatory independent-codex-pr-review",
            "required `independent-codex-pr-review`",
            "$external-review-playbook",
            "$copilot-review-playbook",
        ):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, agents)

    def test_codex_review_gate_is_compatibility_status_only(self) -> None:
        workflow_path = (
            REPO_ROOT / ".github" / "workflows" / "codex-review-gate.yml"
        )
        canonical_fixture = (
            REPO_ROOT
            / "personal_codex"
            / "skills"
            / "review-orchestration-playbook"
            / "tests"
            / "fixtures"
            / "compat"
            / "codex-review-gate.yml"
        )
        self.assertEqual(workflow_path.read_bytes(), canonical_fixture.read_bytes())

        workflow = workflow_path.read_text(encoding="utf-8")
        for anchor in (
            "name: Codex Review Gate Compatibility Status",
            "name: codex/review-gate compatibility publisher",
            "context=codex/review-gate",
            "Compatibility only; no reviewer or review lane.",
            "permissions: {}",
            "workflow_dispatch:",
        ):
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, workflow)

        for retired in (
            "JoeyTeng/codex-review-gate-action",
            "Gate on Codex review",
            "issue_comment:",
            "pull_request_review:",
            "schedule:",
            "CODEX_REVIEW_GATE_EVENT_MODE",
            "@codex review",
        ):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, workflow)

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

    def test_readme_documents_immutable_releases_token_permissions(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        normalized_readme = re.sub(r"\s+", " ", readme)

        self.assertIn("IMMUTABLE_RELEASES_READ_TOKEN", normalized_readme)
        self.assertIn("fine-grained personal access token", normalized_readme)
        self.assertIn("GitHub App installation access token", normalized_readme)
        self.assertIn("Administration (read)", normalized_readme)
        self.assertIn(
            "continue to use the workflow `GITHUB_TOKEN`",
            normalized_readme,
        )

    def test_scheduled_workflow_only_repairs_unchanged_incomplete_release(self) -> None:
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "scheduled-sync-release.yml"
        ).read_text(encoding="utf-8")
        cooldown_guard = "steps.cooldown.outputs.run == 'true'"
        release_guard = (
            "steps.current-release.outputs.complete == 'false' && "
            "steps.changes.outputs.changed != 'true'"
        )

        self.assertIn(
            'if [ "${{ steps.current-release.outputs.complete }}" = "false" ]; then\n'
            '            echo "run=false" >> "$GITHUB_OUTPUT"\n'
            '            echo "reason=current release is incomplete; skipping source sync and publishing current SHA" >> "$GITHUB_OUTPUT"\n'
            "            exit 0\n"
            "          fi",
            workflow,
        )
        for step_name in (
            "Check out codex-toolbox",
            "Check out codex-debug-triage",
            "Check out codex-review-workflows",
            "Check out codex-workflow-hygiene",
            "Check out codex-project-journal",
            "Check out codex-waited-delivery",
            "Sync private overlay sources",
            "Detect sync changes",
        ):
            with self.subTest(cooldown_step=step_name):
                self.assertRegex(
                    workflow,
                    rf"- name: {re.escape(step_name)}\n\s+if: {re.escape(cooldown_guard)}\n",
                )
        self.assertRegex(
            workflow,
            r"- name: Open synced overlay pull request\n"
            r"\s+if: steps\.changes\.outputs\.changed == 'true'\n",
        )
        for step_name in (
            "Validate release history before repair",
            "Revalidate release checkout",
            "Build release package",
            "Verify release package",
            "Publish GitHub release",
            "Validate repaired release history",
        ):
            with self.subTest(step=step_name):
                self.assertRegex(
                    workflow,
                    rf"- name: {re.escape(step_name)}\n\s+if: {re.escape(release_guard)}\n",
                )
        self.assertEqual(workflow.count(f"if: {release_guard}"), 6)
        self.assertIn('actual_sha="$(git rev-parse HEAD)"', workflow)
        self.assertIn("git status --porcelain=v1 --untracked-files=all", workflow)
        cache_redirect = (
            'echo "PYTHONPYCACHEPREFIX=$RUNNER_TEMP/python-cache" >> "$GITHUB_ENV"'
        )
        self.assertIn(cache_redirect, workflow)
        self.assertLess(workflow.index(cache_redirect), workflow.index("python3 "))
        self.assertLess(
            workflow.index("- name: Validate release history before repair"),
            workflow.index("- name: Revalidate release checkout"),
        )
        self.assertLess(
            workflow.index("- name: Check current release"),
            workflow.index("- name: Check cooldown"),
        )
        self.assertLess(
            workflow.index("- name: Check cooldown"),
            workflow.index("- name: Sync private overlay sources"),
        )
        self.assertLess(
            workflow.index("- name: Sync private overlay sources"),
            workflow.index("- name: Detect sync changes"),
        )
        self.assertLess(
            workflow.index("- name: Detect sync changes"),
            workflow.index("- name: Open synced overlay pull request"),
        )
        self.assertLess(
            workflow.index("- name: Open synced overlay pull request"),
            workflow.index("- name: Revalidate release checkout"),
        )
        self.assertLess(
            workflow.index("- name: Revalidate release checkout"),
            workflow.index("- name: Build release package"),
        )
        self.assertLess(
            workflow.index("- name: Publish GitHub release"),
            workflow.index("- name: Validate repaired release history"),
        )
        self.assertNotIn("steps.commit.outputs.sha", workflow)

    def test_scheduled_workflow_repairs_draft_current_release_assets_before_strict_validation(
        self,
    ) -> None:
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "scheduled-sync-release.yml"
        ).read_text(encoding="utf-8")
        sha = "a" * 40
        archive = {
            "id": 11,
            "name": f"personal-codex-{sha}.tar.gz",
            "state": "uploaded",
        }
        checksum = {
            "id": 12,
            "name": f"personal-codex-{sha}.sha256",
            "state": "uploaded",
        }
        release = {
            "id": 10,
            "tag_name": f"personal-codex-20260522-100000-{sha[:7]}",
            "target_commitish": sha,
            "draft": True,
            "prerelease": False,
            "assets": [],
        }
        cases = {
            "missing-asset": [archive],
            "non-uploaded-asset": [archive, dict(checksum, state="starter")],
            "other-sha-non-uploaded-asset": [
                archive,
                checksum,
                {
                    "id": 13,
                    "name": f"personal-codex-{'b' * 40}.sha256",
                    "state": "starter",
                },
            ],
        }
        for name, assets in cases.items():
            with (
                self.subTest(case=name),
                mock.patch.object(
                    RELEASE_MODULE,
                    "iter_releases",
                    return_value=iter([dict(release, assets=assets)]),
                ),
            ):
                self.assertFalse(RELEASE_MODULE.release_complete("owner/repo", sha))

        preflight_start = workflow.index(
            "- name: Validate release history before repair"
        )
        publish_start = workflow.index("- name: Publish GitHub release")
        final_validation_start = workflow.index(
            "- name: Validate repaired release history"
        )
        preflight = workflow[preflight_start:publish_start]
        final_validation = workflow[final_validation_start:]

        self.assertIn("--repair-incomplete-head-release", preflight)
        self.assertIn('--release-repo "$GITHUB_REPOSITORY"', preflight)
        self.assertNotIn("--repair-incomplete-head-release", final_validation)
        self.assertIn('--release-repo "$GITHUB_REPOSITORY"', final_validation)
        self.assertLess(preflight_start, publish_start)
        self.assertLess(publish_start, final_validation_start)

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
    @staticmethod
    def _release_asset(
        asset_id: int,
        name: str,
        data: bytes,
        *,
        state: str = "uploaded",
    ) -> dict[str, object]:
        return {
            "id": asset_id,
            "name": name,
            "state": state,
            "size": len(data),
            "digest": f"sha256:{hashlib.sha256(data).hexdigest()}",
        }

    @staticmethod
    def _release_candidate(
        sha: str,
        *,
        release_id: int = 10,
        draft: bool = False,
        prerelease: bool = False,
        assets: list[dict[str, object]] | None = None,
        tag_suffix_length: int = 7,
    ) -> dict[str, object]:
        if assets is None:
            assets = [
                PrivateOverlayReleaseTests._release_asset(
                    release_id * 10 + 1,
                    f"personal-codex-{sha}.tar.gz",
                    b"archive",
                ),
                PrivateOverlayReleaseTests._release_asset(
                    release_id * 10 + 2,
                    f"personal-codex-{sha}.sha256",
                    b"checksum\n",
                ),
            ]
        return {
            "id": release_id,
            "tag_name": (
                "personal-codex-20260522-100000-"
                f"{sha[:tag_suffix_length]}"
            ),
            "target_commitish": sha,
            "draft": draft,
            "prerelease": prerelease,
            "immutable": not draft,
            "assets": assets,
        }

    def test_immutable_releases_preflight_uses_separate_token_and_api_version(
        self,
    ) -> None:
        requests = []

        def fake_urlopen(request, timeout=30):
            requests.append(request)
            response = (
                {"enabled": True, "enforced_by_owner": False}
                if request.full_url.endswith("/immutable-releases")
                else {"id": 10}
            )
            return io.BytesIO(json.dumps(response).encode("utf-8"))

        with (
            mock.patch.dict(
                os.environ,
                {
                    "GITHUB_TOKEN": GITHUB_TOKEN_FIXTURE,
                    "IMMUTABLE_RELEASES_READ_TOKEN": (
                        IMMUTABLE_RELEASES_READ_TOKEN_FIXTURE
                    ),
                },
                clear=True,
            ),
            mock.patch.object(
                RELEASE_MODULE,
                "urlopen",
                side_effect=fake_urlopen,
            ),
        ):
            RELEASE_MODULE._require_immutable_releases_enabled("owner/repo")
            RELEASE_MODULE.request_json(
                "https://api.github.com/repos/owner/repo/releases/10"
            )

        self.assertEqual(len(requests), 2)
        capability_request, release_request = requests
        capability_headers = {
            name.lower(): value
            for name, value in capability_request.header_items()
        }
        release_headers = {
            name.lower(): value for name, value in release_request.header_items()
        }
        self.assertEqual(capability_request.get_method(), "GET")
        self.assertEqual(
            capability_request.full_url,
            "https://api.github.com/repos/owner/repo/immutable-releases",
        )
        self.assertEqual(
            capability_headers["x-github-api-version"],
            "2026-03-10",
        )
        self.assertEqual(
            capability_headers["authorization"],
            f"Bearer {IMMUTABLE_RELEASES_READ_TOKEN_FIXTURE}",
        )
        self.assertEqual(
            capability_headers["accept"],
            "application/vnd.github+json",
        )
        self.assertEqual(release_request.get_method(), "GET")
        self.assertEqual(
            release_headers["x-github-api-version"],
            RELEASE_MODULE.DEFAULT_GITHUB_API_VERSION,
        )
        self.assertEqual(
            release_headers["authorization"],
            f"Bearer {GITHUB_TOKEN_FIXTURE}",
        )

    def test_immutable_releases_preflight_fails_before_release_mutation(
        self,
    ) -> None:
        cases = {
            "disabled": b'{"enabled": false}',
            "malformed": b"[]",
            "not-found": None,
        }
        sha = "a" * 40
        with tempfile.TemporaryDirectory(
            prefix="private-overlay-release."
        ) as temp_dir_raw:
            dist = Path(temp_dir_raw)
            (dist / f"personal-codex-{sha}.tar.gz").write_bytes(b"archive")
            (dist / f"personal-codex-{sha}.sha256").write_bytes(b"checksum\n")
            draft = self._release_candidate(sha, draft=True)

            for candidate_name, candidates in {
                "new-draft": [],
                "existing-draft": [draft],
            }.items():
                for case_name, response_body in cases.items():
                    with self.subTest(
                        candidate=candidate_name,
                        case=case_name,
                    ):
                        requests = []

                        def fake_urlopen(request, timeout=30):
                            requests.append(request)
                            if response_body is None:
                                raise RELEASE_MODULE.HTTPError(
                                    request.full_url,
                                    404,
                                    "Not Found",
                                    None,
                                    None,
                                )
                            return io.BytesIO(response_body)

                        with (
                            mock.patch.dict(
                                os.environ,
                                {
                                    "GITHUB_TOKEN": GITHUB_TOKEN_FIXTURE,
                                    "IMMUTABLE_RELEASES_READ_TOKEN": (
                                        IMMUTABLE_RELEASES_READ_TOKEN_FIXTURE
                                    ),
                                },
                                clear=True,
                            ),
                            mock.patch.object(
                                RELEASE_MODULE,
                                "iter_releases",
                                return_value=iter(candidates),
                            ),
                            mock.patch.object(
                                RELEASE_MODULE,
                                "urlopen",
                                side_effect=fake_urlopen,
                            ),
                            contextlib.redirect_stdout(io.StringIO()),
                            self.assertRaises(RELEASE_MODULE.ReleaseError),
                        ):
                            RELEASE_MODULE.publish_release(
                                "owner/repo",
                                sha,
                                dist,
                            )

                        self.assertEqual(len(requests), 1)
                        request = requests[0]
                        headers = {
                            header.lower(): value
                            for header, value in request.header_items()
                        }
                        self.assertEqual(request.get_method(), "GET")
                        self.assertEqual(
                            request.full_url,
                            "https://api.github.com/repos/owner/repo/immutable-releases",
                        )
                        self.assertEqual(
                            headers["x-github-api-version"],
                            "2026-03-10",
                        )
                        self.assertEqual(
                            headers["authorization"],
                            f"Bearer {IMMUTABLE_RELEASES_READ_TOKEN_FIXTURE}",
                        )

    def test_missing_immutable_releases_token_fails_before_mutation(self) -> None:
        sha = "a" * 40
        with tempfile.TemporaryDirectory(
            prefix="private-overlay-release."
        ) as temp_dir_raw:
            dist = Path(temp_dir_raw)
            (dist / f"personal-codex-{sha}.tar.gz").write_bytes(b"archive")
            (dist / f"personal-codex-{sha}.sha256").write_bytes(b"checksum\n")
            draft = self._release_candidate(sha, draft=True)

            for token_name, read_token in {
                "absent": None,
                "empty": "",
                "whitespace": "   ",
            }.items():
                environment = {"GITHUB_TOKEN": GITHUB_TOKEN_FIXTURE}
                if read_token is not None:
                    environment["IMMUTABLE_RELEASES_READ_TOKEN"] = read_token
                for candidate_name, candidates in {
                    "new-draft": [],
                    "existing-draft": [draft],
                }.items():
                    with self.subTest(token=token_name, candidate=candidate_name):
                        with (
                            mock.patch.dict(
                                os.environ,
                                environment,
                                clear=True,
                            ),
                            mock.patch.object(
                                RELEASE_MODULE,
                                "iter_releases",
                                return_value=iter(candidates),
                            ),
                            mock.patch.object(
                                RELEASE_MODULE,
                                "request_json",
                            ) as request_json,
                            mock.patch.object(
                                RELEASE_MODULE,
                                "urlopen",
                            ) as urlopen,
                            contextlib.redirect_stdout(io.StringIO()),
                            self.assertRaisesRegex(
                                RELEASE_MODULE.ReleaseError,
                                "IMMUTABLE_RELEASES_READ_TOKEN is required",
                            ),
                        ):
                            RELEASE_MODULE.publish_release("owner/repo", sha, dist)

                    request_json.assert_not_called()
                    urlopen.assert_not_called()

    def test_release_complete_is_read_only_when_no_candidate_exists(self) -> None:
        with (
            mock.patch.object(
                RELEASE_MODULE,
                "iter_releases",
                return_value=iter([]),
            ),
            mock.patch.object(RELEASE_MODULE, "request_json") as request_json,
        ):
            self.assertFalse(
                RELEASE_MODULE.release_complete("owner/repo", "a" * 40)
            )

        request_json.assert_not_called()

    def test_unique_incomplete_published_release_wins_over_complete_release(
        self,
    ) -> None:
        sha = "a" * 40
        complete = self._release_candidate(sha, release_id=10)
        incomplete = self._release_candidate(
            sha,
            release_id=20,
            assets=[
                {
                    "id": 201,
                    "name": f"personal-codex-{sha}.tar.gz",
                    "state": "uploaded",
                }
            ],
        )
        incomplete["tag_name"] = (
            f"personal-codex-20260522-100001-{sha[:7]}"
        )
        expected_names = RELEASE_MODULE._expected_asset_names(sha)

        for candidates in ([complete, incomplete], [incomplete, complete]):
            with self.subTest(order=[candidate["id"] for candidate in candidates]):
                with mock.patch.object(
                    RELEASE_MODULE,
                    "iter_releases",
                    return_value=iter(candidates),
                ):
                    selected, _uploaded_names, done = (
                        RELEASE_MODULE.create_or_find_release(
                            "owner/repo",
                            sha,
                            expected_names,
                        )
                    )
                self.assertIs(selected, incomplete)
                self.assertFalse(done)

                with mock.patch.object(
                    RELEASE_MODULE,
                    "iter_releases",
                    return_value=iter(candidates),
                ):
                    self.assertFalse(
                        RELEASE_MODULE.release_complete("owner/repo", sha)
                    )

    def test_multiple_incomplete_published_releases_fail_closed(self) -> None:
        sha = "a" * 40
        candidates = [
            self._release_candidate(sha, release_id=10, assets=[]),
            self._release_candidate(sha, release_id=20, assets=[]),
        ]
        candidates[1]["tag_name"] = (
            f"personal-codex-20260522-100001-{sha[:7]}"
        )
        expected_names = RELEASE_MODULE._expected_asset_names(sha)

        with (
            mock.patch.object(
                RELEASE_MODULE,
                "iter_releases",
                return_value=iter(candidates),
            ),
            mock.patch.object(RELEASE_MODULE, "request_json") as request_json,
            self.assertRaisesRegex(
                RELEASE_MODULE.ReleaseError,
                "multiple incomplete",
            ),
        ):
            RELEASE_MODULE.create_or_find_release(
                "owner/repo", sha, expected_names
            )
        request_json.assert_not_called()

        with (
            mock.patch.object(
                RELEASE_MODULE,
                "iter_releases",
                return_value=iter(candidates),
            ),
            mock.patch.object(RELEASE_MODULE, "request_json") as request_json,
        ):
            self.assertFalse(RELEASE_MODULE.release_complete("owner/repo", sha))
            request_json.assert_not_called()

    def test_multiple_complete_published_releases_are_already_done(self) -> None:
        sha = "a" * 40
        candidates = [
            self._release_candidate(sha, release_id=10),
            self._release_candidate(sha, release_id=20),
        ]
        candidates[1]["tag_name"] = (
            f"personal-codex-20260522-100001-{sha[:8]}"
        )

        with mock.patch.object(
            RELEASE_MODULE,
            "iter_releases",
            return_value=iter(candidates),
        ):
            self.assertTrue(RELEASE_MODULE.release_complete("owner/repo", sha))

        with mock.patch.object(
            RELEASE_MODULE,
            "iter_releases",
            return_value=iter(candidates),
        ):
            _selected, _uploaded_names, done = (
                RELEASE_MODULE.create_or_find_release(
                    "owner/repo",
                    sha,
                    RELEASE_MODULE._expected_asset_names(sha),
                )
            )
        self.assertTrue(done)

    def test_immutable_complete_release_accepts_symbolic_target_commitish(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="private-overlay-release."
        ) as temp_dir_raw:
            dist = Path(temp_dir_raw)
            sha = "a" * 40
            (dist / f"personal-codex-{sha}.tar.gz").write_bytes(b"archive")
            (dist / f"personal-codex-{sha}.sha256").write_bytes(b"checksum\n")
            base_release = self._release_candidate(sha)
            missing_target = dict(base_release)
            missing_target.pop("target_commitish")
            cases = {
                "branch": dict(base_release, target_commitish="master"),
                "missing": missing_target,
                "non-sha": dict(base_release, target_commitish="not-a-full-sha"),
            }

            for name, release in cases.items():
                with self.subTest(case=name):
                    with mock.patch.object(
                        RELEASE_MODULE,
                        "iter_releases",
                        return_value=iter([release]),
                    ):
                        self.assertTrue(
                            RELEASE_MODULE.release_complete("owner/repo", sha)
                        )

                    with (
                        mock.patch.object(
                            RELEASE_MODULE,
                            "iter_releases",
                            return_value=iter([release]),
                        ),
                        mock.patch.object(
                            RELEASE_MODULE,
                            "request_json",
                        ) as request_json,
                    ):
                        selected, _uploaded_names, done = (
                            RELEASE_MODULE.create_or_find_release(
                                "owner/repo",
                                sha,
                                RELEASE_MODULE._expected_asset_names(sha),
                            )
                        )

                    self.assertIs(selected, release)
                    self.assertTrue(done)
                    request_json.assert_not_called()

                    with (
                        mock.patch.object(
                            RELEASE_MODULE,
                            "iter_releases",
                            return_value=iter([release]),
                        ),
                        mock.patch.object(
                            RELEASE_MODULE,
                            "request_json",
                        ) as request_json,
                        mock.patch.object(
                            RELEASE_MODULE,
                            "urlopen",
                        ) as urlopen,
                        mock.patch.object(
                            RELEASE_MODULE,
                            "_github_token",
                        ) as github_token,
                        contextlib.redirect_stdout(io.StringIO()),
                    ):
                        RELEASE_MODULE.publish_release("owner/repo", sha, dist)

                    request_json.assert_not_called()
                    urlopen.assert_not_called()
                    github_token.assert_not_called()

    def test_complete_release_rejects_different_full_target_before_create(
        self,
    ) -> None:
        sha = "a" * 40
        release = self._release_candidate(sha)
        release["target_commitish"] = "b" * 40
        expected_names = RELEASE_MODULE._expected_asset_names(sha)

        with (
            mock.patch.object(
                RELEASE_MODULE,
                "iter_releases",
                return_value=iter([release]),
            ),
            mock.patch.object(
                RELEASE_MODULE,
                "request_json",
            ) as request_json,
            self.assertRaisesRegex(
                RELEASE_MODULE.ReleaseError,
                "target commitish does not match",
            ),
        ):
            RELEASE_MODULE.create_or_find_release(
                "owner/repo",
                sha,
                expected_names,
            )

        request_json.assert_not_called()

        with (
            mock.patch.object(
                RELEASE_MODULE,
                "iter_releases",
                return_value=iter([release]),
            ),
            self.assertRaisesRegex(
                RELEASE_MODULE.ReleaseError,
                "target commitish does not match",
            ),
        ):
            RELEASE_MODULE.release_complete("owner/repo", sha)

    def test_multiple_drafts_are_ambiguous_only_for_publish_selection(self) -> None:
        sha = "a" * 40
        drafts = [
            self._release_candidate(sha, release_id=10, draft=True),
            self._release_candidate(sha, release_id=20, draft=True),
        ]
        drafts[1]["tag_name"] = (
            f"personal-codex-20260522-100001-{sha[:7]}"
        )

        with (
            mock.patch.object(
                RELEASE_MODULE,
                "iter_releases",
                return_value=iter(drafts),
            ),
            self.assertRaisesRegex(RELEASE_MODULE.ReleaseError, "multiple draft"),
        ):
            RELEASE_MODULE.create_or_find_release(
                "owner/repo",
                sha,
                RELEASE_MODULE._expected_asset_names(sha),
            )

        with mock.patch.object(
            RELEASE_MODULE,
            "iter_releases",
            return_value=iter(drafts),
        ):
            self.assertFalse(RELEASE_MODULE.release_complete("owner/repo", sha))

    def test_existing_complete_release_takes_precedence_over_draft(self) -> None:
        sha = "a" * 40
        complete = self._release_candidate(sha, release_id=10)
        draft = self._release_candidate(sha, release_id=20, draft=True)
        draft["tag_name"] = f"personal-codex-20260522-100001-{sha[:7]}"
        candidates = [draft, complete]

        with mock.patch.object(
            RELEASE_MODULE,
            "iter_releases",
            return_value=iter(candidates),
        ):
            selected, _uploaded_names, done = (
                RELEASE_MODULE.create_or_find_release(
                    "owner/repo",
                    sha,
                    RELEASE_MODULE._expected_asset_names(sha),
                )
            )
        self.assertIs(selected, complete)
        self.assertTrue(done)

        with mock.patch.object(
            RELEASE_MODULE,
            "iter_releases",
            return_value=iter(candidates),
        ):
            self.assertTrue(RELEASE_MODULE.release_complete("owner/repo", sha))

    def test_prerelease_candidates_do_not_anchor_or_satisfy_release(self) -> None:
        sha = "a" * 40
        prerelease = self._release_candidate(
            sha,
            prerelease=True,
        )
        prerelease.update(
            {
                "published_at": "2026-05-22T11:00:00Z",
                "body": "source_event=workflow_dispatch",
            }
        )

        with mock.patch.object(
            RELEASE_MODULE,
            "iter_releases",
            return_value=iter([prerelease]),
        ):
            self.assertEqual(
                RELEASE_MODULE.recent_complete_releases(
                    repo="owner/repo",
                    now=dt.datetime(
                        2026,
                        5,
                        22,
                        12,
                        0,
                        tzinfo=dt.timezone.utc,
                    ),
                    cooldown_seconds=8 * 60 * 60,
                    event="workflow_dispatch",
                ),
                [],
            )

        with mock.patch.object(
            RELEASE_MODULE,
            "iter_releases",
            return_value=iter([prerelease]),
        ):
            self.assertFalse(RELEASE_MODULE.release_complete("owner/repo", sha))

        created: list[dict[str, object]] = []

        def create_release(
            url: str,
            *,
            method="GET",
            payload=None,
            token=None,
            api_version=RELEASE_MODULE.DEFAULT_GITHUB_API_VERSION,
        ):
            if url.endswith("/immutable-releases"):
                self.assertEqual(method, "GET")
                self.assertEqual(
                    token,
                    IMMUTABLE_RELEASES_READ_TOKEN_FIXTURE,
                )
                self.assertEqual(
                    api_version,
                    RELEASE_MODULE.IMMUTABLE_RELEASES_API_VERSION,
                )
                return {"enabled": True, "enforced_by_owner": False}
            self.assertEqual(method, "POST")
            self.assertIsNone(token)
            self.assertEqual(
                api_version,
                RELEASE_MODULE.DEFAULT_GITHUB_API_VERSION,
            )
            response = dict(payload)
            response.update({"id": 20, "assets": []})
            created.append(response)
            return response

        with (
            mock.patch.object(
                RELEASE_MODULE,
                "iter_releases",
                return_value=iter([prerelease]),
            ),
            mock.patch.object(
                RELEASE_MODULE,
                "request_json",
                side_effect=create_release,
            ),
            mock.patch.object(
                RELEASE_MODULE,
                "_immutable_releases_read_token",
                return_value=IMMUTABLE_RELEASES_READ_TOKEN_FIXTURE,
            ),
        ):
            selected, _uploaded_names, done = (
                RELEASE_MODULE.create_or_find_release(
                    "owner/repo",
                    sha,
                    RELEASE_MODULE._expected_asset_names(sha),
                )
            )

        self.assertFalse(done)
        self.assertIs(selected, created[0])
        self.assertFalse(selected["prerelease"])
        self.assertTrue(selected["draft"])

    def test_matching_release_identity_and_flags_are_strict(self) -> None:
        sha = "a" * 40
        expected_names = RELEASE_MODULE._expected_asset_names(sha)
        cases = {
            "missing-prerelease": {"prerelease": None},
            "invalid-draft": {"draft": 0},
            "invalid-id": {"id": 0},
            "missing-assets": {"assets": None},
        }

        for name, changes in cases.items():
            with self.subTest(case=name):
                candidate = self._release_candidate(sha)
                candidate.update(changes)
                with (
                    mock.patch.object(
                        RELEASE_MODULE,
                        "iter_releases",
                        return_value=iter([candidate]),
                    ),
                    mock.patch.object(
                        RELEASE_MODULE,
                        "request_json",
                    ) as request_json,
                    self.assertRaises(RELEASE_MODULE.ReleaseError),
                ):
                    RELEASE_MODULE.create_or_find_release(
                        "owner/repo",
                        sha,
                        expected_names,
                    )
                request_json.assert_not_called()

    def test_release_tags_accept_sha_prefixes_from_seven_to_forty(self) -> None:
        sha = "0123456789abcdef" * 2 + "01234567"
        for prefix_length in (7, 8, 40):
            with self.subTest(prefix_length=prefix_length):
                candidate = self._release_candidate(
                    sha,
                    tag_suffix_length=prefix_length,
                )
                with mock.patch.object(
                    RELEASE_MODULE,
                    "iter_releases",
                    return_value=iter([candidate]),
                ):
                    _selected, _uploaded_names, done = (
                        RELEASE_MODULE.create_or_find_release(
                            "owner/repo",
                            sha,
                            RELEASE_MODULE._expected_asset_names(sha),
                        )
                    )
                self.assertTrue(done)

        wrong_prefix = f"{sha[:7]}f"
        candidate = self._release_candidate(sha)
        candidate["tag_name"] = (
            f"personal-codex-20260522-100000-{wrong_prefix}"
        )
        with (
            mock.patch.object(
                RELEASE_MODULE,
                "iter_releases",
                return_value=iter([candidate]),
            ),
            self.assertRaisesRegex(RELEASE_MODULE.ReleaseError, "invalid tag"),
        ):
            RELEASE_MODULE.create_or_find_release(
                "owner/repo",
                sha,
                RELEASE_MODULE._expected_asset_names(sha),
            )

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
                "prerelease": False,
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
                "prerelease": False,
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
                "prerelease": False,
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
                "prerelease": False,
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
                "prerelease": False,
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
        for release_id, release in enumerate(
            (releases[0], releases[1], releases[4]),
            start=10,
        ):
            release_sha = str(release["target_commitish"])
            release.update(
                {
                    "id": release_id,
                    "immutable": True,
                    "assets": [
                        self._release_asset(
                            release_id * 10 + 1,
                            f"personal-codex-{release_sha}.tar.gz",
                            b"archive",
                        ),
                        self._release_asset(
                            release_id * 10 + 2,
                            f"personal-codex-{release_sha}.sha256",
                            b"checksum\n",
                        ),
                    ],
                }
            )
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
                "prerelease": False,
                "immutable": True,
                "assets": [
                    self._release_asset(
                        11,
                        f"personal-codex-{sha}.tar.gz",
                        b"archive",
                    ),
                    self._release_asset(
                        12,
                        f"personal-codex-{sha}.sha256",
                        b"checksum\n",
                    ),
                ],
            }
            with (
                mock.patch.object(
                    RELEASE_MODULE,
                    "iter_releases",
                    return_value=iter([release]),
                ),
                mock.patch.object(RELEASE_MODULE, "request_json") as request_json,
                mock.patch.object(RELEASE_MODULE, "urlopen") as urlopen,
                mock.patch.object(
                    RELEASE_MODULE,
                    "_github_token",
                ) as github_token,
                mock.patch.object(
                    RELEASE_MODULE,
                    "_immutable_releases_read_token",
                ) as immutable_releases_read_token,
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    RELEASE_MODULE.publish_release("owner/repo", sha, dist)

            request_json.assert_not_called()
            urlopen.assert_not_called()
            github_token.assert_not_called()
            immutable_releases_read_token.assert_not_called()

    def test_publish_reuse_rejects_unbound_release_metadata_without_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="private-overlay-release."
        ) as temp_dir_raw:
            dist = Path(temp_dir_raw)
            sha = "a" * 40
            archive_name = f"personal-codex-{sha}.tar.gz"
            checksum_name = f"personal-codex-{sha}.sha256"
            (dist / archive_name).write_bytes(b"archive")
            (dist / checksum_name).write_bytes(b"checksum\n")
            release = self._release_candidate(sha)
            release_without_immutable = dict(release)
            release_without_immutable.pop("immutable")
            cases = {
                "mutable": dict(release, immutable=False),
                "missing-immutable": release_without_immutable,
                "invalid-id": dict(
                    release,
                    assets=[
                        dict(release["assets"][0], id=0),
                        release["assets"][1],
                    ],
                ),
                "duplicate-id": dict(
                    release,
                    assets=[
                        release["assets"][0],
                        dict(
                            release["assets"][1],
                            id=release["assets"][0]["id"],
                        ),
                    ],
                ),
                "wrong-size": dict(
                    release,
                    assets=[
                        dict(release["assets"][0], size=999),
                        release["assets"][1],
                    ],
                ),
                "wrong-digest": dict(
                    release,
                    assets=[
                        dict(
                            release["assets"][0],
                            digest=f"sha256:{'b' * 64}",
                        ),
                        release["assets"][1],
                    ],
                ),
                "uppercase-digest": dict(
                    release,
                    assets=[
                        dict(
                            release["assets"][0],
                            digest=str(release["assets"][0]["digest"]).upper(),
                        ),
                        release["assets"][1],
                    ],
                ),
                "non-object-asset": dict(
                    release,
                    assets=[*release["assets"], "invalid"],
                ),
            }

            for name, candidate in cases.items():
                with self.subTest(case=name):
                    with (
                        mock.patch.object(
                            RELEASE_MODULE,
                            "iter_releases",
                            return_value=iter([candidate]),
                        ),
                        mock.patch.object(
                            RELEASE_MODULE,
                            "request_json",
                        ) as request_json,
                        mock.patch.object(
                            RELEASE_MODULE,
                            "urlopen",
                        ) as urlopen,
                        mock.patch.object(
                            RELEASE_MODULE,
                            "_github_token",
                        ) as github_token,
                        contextlib.redirect_stdout(io.StringIO()),
                        self.assertRaises(RELEASE_MODULE.ReleaseError),
                    ):
                        RELEASE_MODULE.publish_release(
                            "owner/repo",
                            sha,
                            dist,
                        )

                    request_json.assert_not_called()
                    urlopen.assert_not_called()
                    github_token.assert_not_called()

    def test_publish_reads_bounded_assets_before_remote_mutation(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="private-overlay-release."
        ) as temp_dir_raw:
            dist = Path(temp_dir_raw)
            sha = "a" * 40
            (dist / f"personal-codex-{sha}.tar.gz").write_bytes(b"12345")
            (dist / f"personal-codex-{sha}.sha256").write_bytes(b"ok")
            with (
                mock.patch.object(
                    RELEASE_MODULE,
                    "MAX_RELEASE_ASSET_BYTES",
                    4,
                ),
                mock.patch.object(
                    RELEASE_MODULE,
                    "iter_releases",
                ) as iter_releases,
                self.assertRaisesRegex(
                    RELEASE_MODULE.ReleaseError,
                    "release asset exceeds 4 bytes",
                ),
            ):
                RELEASE_MODULE.publish_release("owner/repo", sha, dist)

            iter_releases.assert_not_called()

    def test_other_sha_pending_asset_keeps_uploaded_pair_eligible_for_repair(
        self,
    ) -> None:
        sha = "a" * 40
        other_sha = "b" * 40
        archive_name = f"personal-codex-{sha}.tar.gz"
        checksum_name = f"personal-codex-{sha}.sha256"
        release = {
            "id": 10,
            "tag_name": f"personal-codex-20260522-100000-{sha[:7]}",
            "target_commitish": sha,
            "draft": True,
            "prerelease": False,
            "assets": [
                {"id": 11, "name": archive_name, "state": "uploaded"},
                {"id": 12, "name": checksum_name, "state": "uploaded"},
                {
                    "id": 13,
                    "name": f"personal-codex-{other_sha}.sha256",
                    "state": "new",
                },
            ],
        }

        with mock.patch.object(
            RELEASE_MODULE,
            "iter_releases",
            return_value=iter([release]),
        ):
            candidate, uploaded_asset_names, done = (
                RELEASE_MODULE.create_or_find_release(
                    "owner/repo",
                    sha,
                    {archive_name, checksum_name},
                )
            )

        self.assertIs(candidate, release)
        self.assertEqual(uploaded_asset_names, {archive_name, checksum_name})
        self.assertFalse(done)

    def test_unexpected_or_duplicate_uploaded_assets_remain_repairable(
        self,
    ) -> None:
        sha = "a" * 40
        other_sha = "b" * 40
        archive_name = f"personal-codex-{sha}.tar.gz"
        checksum_name = f"personal-codex-{sha}.sha256"
        cases = {
            "unexpected": [
                {"id": 11, "name": archive_name, "state": "uploaded"},
                {"id": 12, "name": checksum_name, "state": "uploaded"},
                {
                    "id": 13,
                    "name": f"personal-codex-{other_sha}.tar.gz",
                    "state": "uploaded",
                },
            ],
            "duplicate": [
                {"id": 11, "name": archive_name, "state": "uploaded"},
                {"id": 12, "name": checksum_name, "state": "uploaded"},
                {"id": 13, "name": archive_name, "state": "uploaded"},
            ],
        }

        for name, assets in cases.items():
            with self.subTest(case=name):
                release = {
                    "id": 10,
                    "tag_name": f"personal-codex-20260522-100000-{sha[:7]}",
                    "target_commitish": sha,
                    "draft": True,
                    "prerelease": False,
                    "assets": assets,
                }
                with mock.patch.object(
                    RELEASE_MODULE,
                    "iter_releases",
                    return_value=iter([release]),
                ):
                    candidate, _uploaded_asset_names, done = (
                        RELEASE_MODULE.create_or_find_release(
                            "owner/repo",
                            sha,
                            {archive_name, checksum_name},
                        )
                    )

                self.assertIs(candidate, release)
                self.assertFalse(done)

    def test_create_find_rejects_invalid_matching_tag_before_mutation(self) -> None:
        sha = "a" * 40
        archive_name = f"personal-codex-{sha}.tar.gz"
        checksum_name = f"personal-codex-{sha}.sha256"
        cases = {
            "malformed": "personal-codex-not-a-release-tag",
            "wrong-suffix": "personal-codex-20260522-100000-bbbbbbb",
        }

        for name, tag_name in cases.items():
            with self.subTest(case=name):
                release = {
                    "id": 10,
                    "tag_name": tag_name,
                    "target_commitish": sha,
                    "draft": False,
                    "prerelease": False,
                    "assets": [
                        {"id": 11, "name": archive_name, "state": "uploaded"},
                        {"id": 12, "name": checksum_name, "state": "uploaded"},
                    ],
                }
                with (
                    mock.patch.object(
                        RELEASE_MODULE,
                        "iter_releases",
                        return_value=iter([release]),
                    ),
                    mock.patch.object(
                        RELEASE_MODULE,
                        "request_json",
                    ) as request_json,
                    self.assertRaisesRegex(
                        RELEASE_MODULE.ReleaseError,
                        "invalid tag",
                    ),
                ):
                    RELEASE_MODULE.create_or_find_release(
                        "owner/repo",
                        sha,
                        {archive_name, checksum_name},
                    )

                request_json.assert_not_called()

    def test_publish_existing_exact_pair_draft_reuploads_before_publish(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="private-overlay-release."
        ) as temp_dir_raw:
            dist = Path(temp_dir_raw)
            sha = "a" * 40
            archive_name = f"personal-codex-{sha}.tar.gz"
            checksum_name = f"personal-codex-{sha}.sha256"
            (dist / archive_name).write_bytes(b"archive")
            (dist / checksum_name).write_text("checksum\n", encoding="utf-8")
            release = {
                "id": 10,
                "tag_name": f"personal-codex-20260522-100000-{sha[:7]}",
                "target_commitish": sha,
                "body": "source_event=schedule",
                "draft": True,
                "prerelease": False,
                "assets": [
                    {"id": 11, "name": archive_name, "state": "uploaded"},
                    {"id": 12, "name": checksum_name, "state": "uploaded"},
                    {"id": 13, "name": "release-notes.txt", "state": "uploaded"},
                ],
            }
            requests: list[dict[str, object]] = []
            uploads: list[tuple[str, str, bytes]] = []
            events: list[str] = []
            published = False

            def fake_request_json(
                url: str,
                *,
                method: str = "GET",
                payload=None,
                token=None,
                api_version=RELEASE_MODULE.DEFAULT_GITHUB_API_VERSION,
            ):
                nonlocal published
                requests.append(
                    {
                        "url": url,
                        "method": method,
                        "payload": payload,
                        "token": token,
                        "api_version": api_version,
                    }
                )
                if url.endswith("/immutable-releases"):
                    events.append("GET:immutable-releases")
                    return {"enabled": True, "enforced_by_owner": False}
                if method == "DELETE":
                    events.append(f"DELETE:{url.rsplit('/', 1)[-1]}")
                else:
                    events.append(method)
                if method == "GET":
                    return dict(
                        release,
                        draft=not published,
                        immutable=published,
                        assets=[
                            self._release_asset(
                                21,
                                archive_name,
                                b"archive",
                            ),
                            self._release_asset(
                                22,
                                checksum_name,
                                b"checksum\n",
                            ),
                            release["assets"][2],
                        ],
                    )
                if method == "PATCH":
                    published = True
                return {"untrusted": True}

            def fake_urlopen(request, timeout=30):
                asset_name = request.full_url.rpartition("?name=")[2]
                uploads.append((request.get_method(), asset_name, request.data))
                events.append(f"{request.get_method()}:{asset_name}")
                if asset_name == archive_name:
                    (dist / checksum_name).write_bytes(b"changed after snapshot")
                return io.BytesIO(
                    json.dumps(
                        {"name": asset_name, "state": "uploaded"}
                    ).encode("utf-8")
                )

            with (
                mock.patch.object(
                    RELEASE_MODULE,
                    "iter_releases",
                    return_value=iter([release]),
                ),
                mock.patch.object(
                    RELEASE_MODULE,
                    "request_json",
                    side_effect=fake_request_json,
                ),
                mock.patch.object(
                    RELEASE_MODULE,
                    "urlopen",
                    side_effect=fake_urlopen,
                ),
                mock.patch.object(
                    RELEASE_MODULE,
                    "_github_token",
                    return_value=GITHUB_TOKEN_FIXTURE,
                ),
                mock.patch.object(
                    RELEASE_MODULE,
                    "_immutable_releases_read_token",
                    return_value=IMMUTABLE_RELEASES_READ_TOKEN_FIXTURE,
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                RELEASE_MODULE.publish_release(
                    "owner/repo",
                    sha,
                    dist,
                    source_event="workflow_dispatch",
                )

        self.assertEqual(
            [request["method"] for request in requests],
            ["GET", "DELETE", "DELETE", "GET", "GET", "PATCH", "GET"],
        )
        self.assertEqual(
            events,
            [
                "GET:immutable-releases",
                "DELETE:11",
                "DELETE:12",
                f"POST:{archive_name}",
                f"POST:{checksum_name}",
                "GET",
                "GET:immutable-releases",
                "PATCH",
                "GET",
            ],
        )
        self.assertEqual(
            {
                str(request["url"]).rsplit("/", 1)[-1]
                for request in requests
                if request["method"] == "DELETE"
            },
            {"11", "12"},
        )
        self.assertEqual(
            uploads,
            [
                ("POST", archive_name, b"archive"),
                ("POST", checksum_name, b"checksum\n"),
            ],
        )
        self.assertEqual(
            requests[5]["payload"],
            {
                "body": f"Private Codex overlay release for {sha}.\n\nsource_event=workflow_dispatch",
                "draft": False,
            },
        )
        self.assertEqual(
            requests[0]["api_version"],
            RELEASE_MODULE.IMMUTABLE_RELEASES_API_VERSION,
        )
        self.assertEqual(
            requests[4]["api_version"],
            RELEASE_MODULE.IMMUTABLE_RELEASES_API_VERSION,
        )
        self.assertEqual(
            [requests[index]["token"] for index in (0, 4)],
            [
                IMMUTABLE_RELEASES_READ_TOKEN_FIXTURE,
                IMMUTABLE_RELEASES_READ_TOKEN_FIXTURE,
            ],
        )
        self.assertTrue(
            all(
                request["token"] is None
                for index, request in enumerate(requests)
                if index not in (0, 4)
            )
        )

    def test_publish_rechecks_capability_before_patch(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="private-overlay-release."
        ) as temp_dir_raw:
            dist = Path(temp_dir_raw)
            sha = "a" * 40
            archive_name = f"personal-codex-{sha}.tar.gz"
            checksum_name = f"personal-codex-{sha}.sha256"
            (dist / archive_name).write_bytes(b"archive")
            (dist / checksum_name).write_bytes(b"checksum\n")
            draft = self._release_candidate(sha, draft=True)
            requests: list[dict[str, object]] = []
            capability_checks = 0

            def fake_request_json(
                url: str,
                *,
                method: str = "GET",
                payload=None,
                token=None,
                api_version=RELEASE_MODULE.DEFAULT_GITHUB_API_VERSION,
            ):
                nonlocal capability_checks
                requests.append(
                    {
                        "url": url,
                        "method": method,
                        "token": token,
                        "api_version": api_version,
                    }
                )
                if url.endswith("/immutable-releases"):
                    capability_checks += 1
                    return {"enabled": capability_checks == 1}
                if method == "GET":
                    return draft
                return {}

            def fake_urlopen(request, timeout=30):
                asset_name = request.full_url.rpartition("?name=")[2]
                return io.BytesIO(
                    json.dumps(
                        {"name": asset_name, "state": "uploaded"}
                    ).encode("utf-8")
                )

            with (
                mock.patch.object(
                    RELEASE_MODULE,
                    "iter_releases",
                    return_value=iter([draft]),
                ),
                mock.patch.object(
                    RELEASE_MODULE,
                    "request_json",
                    side_effect=fake_request_json,
                ),
                mock.patch.object(
                    RELEASE_MODULE,
                    "urlopen",
                    side_effect=fake_urlopen,
                ) as urlopen,
                mock.patch.object(
                    RELEASE_MODULE,
                    "_github_token",
                    return_value=GITHUB_TOKEN_FIXTURE,
                ),
                mock.patch.object(
                    RELEASE_MODULE,
                    "_immutable_releases_read_token",
                    return_value=IMMUTABLE_RELEASES_READ_TOKEN_FIXTURE,
                ),
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaisesRegex(
                    RELEASE_MODULE.ReleaseError,
                    "immutable releases are not enabled",
                ),
            ):
                RELEASE_MODULE.publish_release("owner/repo", sha, dist)

        self.assertEqual(
            [request["method"] for request in requests],
            ["GET", "DELETE", "DELETE", "GET", "GET"],
        )
        self.assertEqual(
            [
                request["token"]
                for request in requests
                if str(request["url"]).endswith("/immutable-releases")
            ],
            [
                IMMUTABLE_RELEASES_READ_TOKEN_FIXTURE,
                IMMUTABLE_RELEASES_READ_TOKEN_FIXTURE,
            ],
        )
        self.assertFalse(
            any(request["method"] == "PATCH" for request in requests)
        )
        self.assertEqual(urlopen.call_count, 2)

    def test_publish_existing_draft_rejects_flag_drift_before_patch(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="private-overlay-release."
        ) as temp_dir_raw:
            dist = Path(temp_dir_raw)
            sha = "a" * 40
            archive_name = f"personal-codex-{sha}.tar.gz"
            checksum_name = f"personal-codex-{sha}.sha256"
            (dist / archive_name).write_bytes(b"archive")
            (dist / checksum_name).write_text("checksum\n", encoding="utf-8")
            draft = self._release_candidate(sha, draft=True)
            requests: list[str] = []

            def fake_request_json(
                url: str, *, method: str = "GET", payload=None, token=None
            ):
                requests.append(method)
                if method == "GET":
                    return dict(draft, draft=False)
                return {}

            def fake_urlopen(request, timeout=30):
                asset_name = request.full_url.rpartition("?name=")[2]
                return io.BytesIO(
                    json.dumps(
                        {"name": asset_name, "state": "uploaded"}
                    ).encode("utf-8")
                )

            with (
                mock.patch.object(
                    RELEASE_MODULE,
                    "iter_releases",
                    return_value=iter([draft]),
                ),
                mock.patch.object(
                    RELEASE_MODULE,
                    "_require_immutable_releases_enabled",
                ),
                mock.patch.object(
                    RELEASE_MODULE,
                    "request_json",
                    side_effect=fake_request_json,
                ),
                mock.patch.object(
                    RELEASE_MODULE,
                    "urlopen",
                    side_effect=fake_urlopen,
                ) as urlopen,
                mock.patch.object(
                    RELEASE_MODULE,
                    "_github_token",
                    return_value="token",
                ),
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaisesRegex(
                    RELEASE_MODULE.ReleaseError,
                    "draft flag changed",
                ),
            ):
                RELEASE_MODULE.publish_release("owner/repo", sha, dist)

        self.assertEqual(requests, ["DELETE", "DELETE", "GET"])
        self.assertEqual(urlopen.call_count, 2)

    def test_publish_existing_draft_binds_pre_patch_get_to_local_content(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="private-overlay-release."
        ) as temp_dir_raw:
            dist = Path(temp_dir_raw)
            sha = "a" * 40
            archive_name = f"personal-codex-{sha}.tar.gz"
            checksum_name = f"personal-codex-{sha}.sha256"
            (dist / archive_name).write_bytes(b"archive")
            (dist / checksum_name).write_bytes(b"checksum\n")
            draft = self._release_candidate(sha, draft=True)
            requests: list[str] = []
            wrong_assets = [
                dict(draft["assets"][0], digest=f"sha256:{'b' * 64}"),
                draft["assets"][1],
            ]

            def fake_request_json(
                url: str, *, method: str = "GET", payload=None, token=None
            ):
                requests.append(method)
                if method == "GET":
                    return dict(draft, assets=wrong_assets)
                return {}

            def fake_urlopen(request, timeout=30):
                asset_name = request.full_url.rpartition("?name=")[2]
                return io.BytesIO(
                    json.dumps(
                        {"name": asset_name, "state": "uploaded"}
                    ).encode("utf-8")
                )

            with (
                mock.patch.object(
                    RELEASE_MODULE,
                    "iter_releases",
                    return_value=iter([draft]),
                ),
                mock.patch.object(
                    RELEASE_MODULE,
                    "_require_immutable_releases_enabled",
                ),
                mock.patch.object(
                    RELEASE_MODULE,
                    "request_json",
                    side_effect=fake_request_json,
                ),
                mock.patch.object(
                    RELEASE_MODULE,
                    "urlopen",
                    side_effect=fake_urlopen,
                ),
                mock.patch.object(
                    RELEASE_MODULE,
                    "_github_token",
                    return_value="token",
                ),
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaisesRegex(
                    RELEASE_MODULE.ReleaseError,
                    "digest mismatch",
                ),
            ):
                RELEASE_MODULE.publish_release("owner/repo", sha, dist)

        self.assertEqual(requests, ["DELETE", "DELETE", "GET"])
        self.assertNotIn("PATCH", requests)

    def test_publish_existing_draft_rejects_post_publish_drift(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="private-overlay-release."
        ) as temp_dir_raw:
            dist = Path(temp_dir_raw)
            sha = "a" * 40
            archive_name = f"personal-codex-{sha}.tar.gz"
            checksum_name = f"personal-codex-{sha}.sha256"
            (dist / archive_name).write_bytes(b"archive")
            (dist / checksum_name).write_text("checksum\n", encoding="utf-8")
            draft = self._release_candidate(sha, draft=True)
            wrong_digest_assets = [
                dict(draft["assets"][0], digest=f"sha256:{'b' * 64}"),
                draft["assets"][1],
            ]
            wrong_size_assets = [
                dict(draft["assets"][0], size=999),
                draft["assets"][1],
            ]
            drift_cases = {
                "id": dict(draft, id=99, draft=False),
                "tag": dict(
                    draft,
                    tag_name=f"personal-codex-20260522-100001-{sha[:7]}",
                    draft=False,
                ),
                "target": dict(draft, target_commitish="b" * 40, draft=False),
                "draft": draft,
                "prerelease": dict(draft, draft=False, prerelease=True),
                "immutable": dict(draft, draft=False, immutable=False),
                "digest": dict(
                    draft,
                    draft=False,
                    immutable=True,
                    assets=wrong_digest_assets,
                ),
                "size": dict(
                    draft,
                    draft=False,
                    immutable=True,
                    assets=wrong_size_assets,
                ),
                "assets": dict(
                    draft,
                    draft=False,
                    assets=[
                        *draft["assets"],
                        {
                            "id": 999,
                            "name": f"personal-codex-{'b' * 40}.sha256",
                            "state": "uploaded",
                        },
                    ],
                ),
            }

            for name, published in drift_cases.items():
                with self.subTest(case=name):
                    requests: list[str] = []
                    get_count = 0

                    def fake_request_json(
                        url: str,
                        *,
                        method: str = "GET",
                        payload=None,
                        token=None,
                        api_version=RELEASE_MODULE.DEFAULT_GITHUB_API_VERSION,
                    ):
                        nonlocal get_count
                        requests.append(method)
                        if url.endswith("/immutable-releases"):
                            return {"enabled": True, "enforced_by_owner": False}
                        if method == "GET":
                            get_count += 1
                            return draft if get_count == 1 else published
                        return {"untrusted": True}

                    def fake_urlopen(request, timeout=30):
                        asset_name = request.full_url.rpartition("?name=")[2]
                        return io.BytesIO(
                            json.dumps(
                                {"name": asset_name, "state": "uploaded"}
                            ).encode("utf-8")
                        )

                    with (
                        mock.patch.object(
                            RELEASE_MODULE,
                            "iter_releases",
                            return_value=iter([draft]),
                        ),
                        mock.patch.object(
                            RELEASE_MODULE,
                            "request_json",
                            side_effect=fake_request_json,
                        ),
                        mock.patch.object(
                            RELEASE_MODULE,
                            "urlopen",
                            side_effect=fake_urlopen,
                        ) as urlopen,
                        mock.patch.object(
                            RELEASE_MODULE,
                            "_github_token",
                            return_value=GITHUB_TOKEN_FIXTURE,
                        ),
                        mock.patch.object(
                            RELEASE_MODULE,
                            "_immutable_releases_read_token",
                            return_value=IMMUTABLE_RELEASES_READ_TOKEN_FIXTURE,
                        ),
                        contextlib.redirect_stdout(io.StringIO()),
                        self.assertRaises(RELEASE_MODULE.ReleaseError),
                    ):
                        RELEASE_MODULE.publish_release("owner/repo", sha, dist)

                    self.assertEqual(
                        requests,
                        [
                            "GET",
                            "DELETE",
                            "DELETE",
                            "GET",
                            "GET",
                            "PATCH",
                            "GET",
                        ],
                    )
                    self.assertEqual(urlopen.call_count, 2)

    def test_incomplete_published_release_requires_operator_resolution(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="private-overlay-release."
        ) as temp_dir_raw:
            dist = Path(temp_dir_raw)
            sha = "a" * 40
            archive_name = f"personal-codex-{sha}.tar.gz"
            checksum_name = f"personal-codex-{sha}.sha256"
            (dist / archive_name).write_bytes(b"archive")
            (dist / checksum_name).write_text("checksum\n", encoding="utf-8")
            base_release = self._release_candidate(
                sha,
                assets=[
                    {
                        "id": 11,
                        "name": archive_name,
                        "state": "starter",
                    }
                ],
            )
            missing_immutable = dict(base_release)
            missing_immutable.pop("immutable")
            cases = {
                "immutable": base_release,
                "mutable": dict(base_release, immutable=False),
                "missing": missing_immutable,
                "non-boolean": dict(base_release, immutable="false"),
            }

            for name, release in cases.items():
                with self.subTest(case=name):
                    with (
                        mock.patch.object(
                            RELEASE_MODULE,
                            "iter_releases",
                            return_value=iter([release]),
                        ),
                        mock.patch.object(
                            RELEASE_MODULE,
                            "request_json",
                        ) as request_json,
                        mock.patch.object(
                            RELEASE_MODULE,
                            "urlopen",
                        ) as urlopen,
                        mock.patch.object(
                            RELEASE_MODULE,
                            "_github_token",
                        ) as github_token,
                        contextlib.redirect_stdout(io.StringIO()),
                        self.assertRaisesRegex(
                            RELEASE_MODULE.ReleaseError,
                            "requires operator resolution or recreation",
                        ),
                    ):
                        RELEASE_MODULE.publish_release("owner/repo", sha, dist)

                    request_json.assert_not_called()
                    urlopen.assert_not_called()
                    github_token.assert_not_called()

    def test_publish_deletes_incomplete_assets_before_reupload(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="private-overlay-release."
        ) as temp_dir_raw:
            dist = Path(temp_dir_raw)
            sha = "a" * 40
            other_sha = "b" * 40
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
                "prerelease": False,
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
                    {
                        "id": 13,
                        "name": f"personal-codex-{other_sha}.tar.gz",
                        "state": "new",
                    },
                ],
            }
            requests: list[dict[str, object]] = []
            uploads: list[str] = []
            published = False

            def fake_request_json(
                url: str, *, method: str = "GET", payload=None, token=None
            ):
                nonlocal published
                requests.append({"url": url, "method": method, "payload": payload})
                if method == "GET" and url.endswith("/releases/10"):
                    return {
                        "id": 10,
                        "tag_name": release["tag_name"],
                        "target_commitish": sha,
                        "draft": not published,
                        "prerelease": False,
                        "immutable": published,
                        "assets": [
                            self._release_asset(
                                21,
                                f"personal-codex-{sha}.tar.gz",
                                b"archive",
                            ),
                            self._release_asset(
                                22,
                                f"personal-codex-{sha}.sha256",
                                b"checksum\n",
                            ),
                        ],
                    }
                if method == "PATCH":
                    published = True
                return {}

            class FakeResponse:
                def __init__(self, name: str) -> None:
                    self.name = name

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def read(self) -> bytes:
                    return json.dumps(
                        {"name": self.name, "state": "uploaded"}
                    ).encode("utf-8")

            def fake_urlopen(request, timeout=30):
                uploads.append(request.full_url)
                return FakeResponse(request.full_url.rpartition("?name=")[2])

            with (
                mock.patch.object(
                    RELEASE_MODULE,
                    "iter_releases",
                    return_value=iter([release]),
                ),
                mock.patch.object(
                    RELEASE_MODULE,
                    "_require_immutable_releases_enabled",
                ),
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

        self.assertEqual(
            {
                str(request["url"]).rsplit("/", 1)[-1]
                for request in requests
                if request["method"] == "DELETE"
            },
            {"11", "12", "13"},
        )
        self.assertEqual(len(uploads), 2)
        self.assertIn(f"personal-codex-{sha}.tar.gz", uploads[0])
        self.assertIn(f"personal-codex-{sha}.sha256", uploads[1])

    def test_exact_pair_draft_validates_asset_ids_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="private-overlay-release."
        ) as temp_dir_raw:
            dist = Path(temp_dir_raw)
            sha = "a" * 40
            archive_name = f"personal-codex-{sha}.tar.gz"
            checksum_name = f"personal-codex-{sha}.sha256"
            (dist / archive_name).write_bytes(b"archive")
            (dist / checksum_name).write_text("checksum\n", encoding="utf-8")
            cases = {
                "missing": (
                    [
                        {"id": 11, "name": archive_name, "state": "uploaded"},
                        {"name": checksum_name, "state": "uploaded"},
                    ],
                    "positive integer id",
                ),
                "invalid": (
                    [
                        {"id": 11, "name": archive_name, "state": "uploaded"},
                        {"id": 0, "name": checksum_name, "state": "uploaded"},
                    ],
                    "positive integer id",
                ),
                "boolean": (
                    [
                        {"id": 11, "name": archive_name, "state": "uploaded"},
                        {"id": True, "name": checksum_name, "state": "uploaded"},
                    ],
                    "positive integer id",
                ),
                "duplicate": (
                    [
                        {"id": 11, "name": archive_name, "state": "uploaded"},
                        {"id": 11, "name": checksum_name, "state": "uploaded"},
                    ],
                    "reuse id 11",
                ),
            }

            for name, (assets, error_pattern) in cases.items():
                with self.subTest(case=name):
                    release = {
                        "id": 10,
                        "tag_name": f"personal-codex-20260522-100000-{sha[:7]}",
                        "target_commitish": sha,
                        "draft": True,
                        "prerelease": False,
                        "assets": assets,
                    }
                    with (
                        mock.patch.object(
                            RELEASE_MODULE,
                            "iter_releases",
                            return_value=iter([release]),
                        ),
                        mock.patch.object(
                            RELEASE_MODULE,
                            "request_json",
                        ) as request_json,
                        mock.patch.object(
                            RELEASE_MODULE,
                            "urlopen",
                        ) as urlopen,
                        self.assertRaisesRegex(
                            RELEASE_MODULE.ReleaseError,
                            error_pattern,
                        ),
                    ):
                        RELEASE_MODULE.publish_release("owner/repo", sha, dist)

                    request_json.assert_not_called()
                    urlopen.assert_not_called()

    def test_publish_rejects_invalid_release_metadata_before_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="private-overlay-release."
        ) as temp_dir_raw:
            dist = Path(temp_dir_raw)
            sha = "a" * 40
            archive_name = f"personal-codex-{sha}.tar.gz"
            checksum_name = f"personal-codex-{sha}.sha256"
            (dist / archive_name).write_bytes(b"archive")
            (dist / checksum_name).write_text("checksum\n", encoding="utf-8")
            cases = {
                "release-id-missing": {"id": None},
                "release-id": {"id": 0},
                "release-id-boolean": {"id": True},
                "draft-flag": {"draft": 1},
                "prerelease-flag": {"prerelease": None},
                "draft-prerelease": {"prerelease": True},
            }

            for name, changes in cases.items():
                with self.subTest(case=name):
                    release = self._release_candidate(sha, draft=True)
                    release.update(changes)
                    with (
                        mock.patch.object(
                            RELEASE_MODULE,
                            "iter_releases",
                            return_value=iter([release]),
                        ),
                        mock.patch.object(
                            RELEASE_MODULE,
                            "_require_immutable_releases_enabled",
                        ) as immutable_releases_preflight,
                        mock.patch.object(
                            RELEASE_MODULE,
                            "request_json",
                        ) as request_json,
                        mock.patch.object(
                            RELEASE_MODULE,
                            "urlopen",
                        ) as urlopen,
                        self.assertRaises(RELEASE_MODULE.ReleaseError),
                    ):
                        RELEASE_MODULE.publish_release("owner/repo", sha, dist)

                    request_json.assert_not_called()
                    urlopen.assert_not_called()
                    immutable_releases_preflight.assert_not_called()

    def test_upload_response_must_match_expected_uploaded_asset(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="private-overlay-release."
        ) as temp_dir_raw:
            dist = Path(temp_dir_raw)
            sha = "a" * 40
            archive_name = f"personal-codex-{sha}.tar.gz"
            checksum_name = f"personal-codex-{sha}.sha256"
            (dist / archive_name).write_bytes(b"archive")
            (dist / checksum_name).write_text("checksum\n", encoding="utf-8")
            release = {
                "id": 10,
                "tag_name": f"personal-codex-20260522-100000-{sha[:7]}",
                "target_commitish": sha,
                "draft": True,
                "prerelease": False,
                "assets": [],
            }
            responses = {
                "wrong-name": {"name": checksum_name, "state": "uploaded"},
                "wrong-state": {"name": archive_name, "state": "new"},
            }

            for name, response_payload in responses.items():
                with self.subTest(case=name):
                    with (
                        mock.patch.object(
                            RELEASE_MODULE,
                            "iter_releases",
                            return_value=iter([release]),
                        ),
                        mock.patch.object(
                            RELEASE_MODULE,
                            "_require_immutable_releases_enabled",
                        ),
                        mock.patch.object(
                            RELEASE_MODULE,
                            "request_json",
                        ) as request_json,
                        mock.patch.object(
                            RELEASE_MODULE,
                            "urlopen",
                            return_value=io.BytesIO(
                                json.dumps(response_payload).encode("utf-8")
                            ),
                        ),
                        mock.patch.object(
                            RELEASE_MODULE,
                            "_github_token",
                            return_value="token",
                        ),
                        self.assertRaisesRegex(
                            RELEASE_MODULE.ReleaseError,
                            "unexpected payload",
                        ),
                    ):
                        RELEASE_MODULE.publish_release("owner/repo", sha, dist)

                    request_json.assert_not_called()

    def test_final_release_get_rejects_mixed_asset_state(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="private-overlay-release."
        ) as temp_dir_raw:
            dist = Path(temp_dir_raw)
            sha = "a" * 40
            other_sha = "b" * 40
            archive_name = f"personal-codex-{sha}.tar.gz"
            checksum_name = f"personal-codex-{sha}.sha256"
            (dist / archive_name).write_bytes(b"archive")
            (dist / checksum_name).write_text("checksum\n", encoding="utf-8")
            release = {
                "id": 10,
                "tag_name": f"personal-codex-20260522-100000-{sha[:7]}",
                "target_commitish": sha,
                "draft": True,
                "prerelease": False,
                "assets": [
                    {"id": 11, "name": archive_name, "state": "uploaded"},
                    {"id": 12, "name": checksum_name, "state": "starter"},
                ],
            }
            requests: list[dict[str, object]] = []

            def fake_request_json(
                url: str, *, method: str = "GET", payload=None, token=None
            ):
                requests.append({"url": url, "method": method, "payload": payload})
                if method == "GET":
                    return {
                        "id": 10,
                        "tag_name": release["tag_name"],
                        "target_commitish": sha,
                        "draft": True,
                        "prerelease": False,
                        "assets": [
                            {"id": 21, "name": archive_name, "state": "uploaded"},
                            {"id": 22, "name": checksum_name, "state": "uploaded"},
                            {
                                "id": 23,
                                "name": f"personal-codex-{other_sha}.tar.gz",
                                "state": "new",
                            },
                        ],
                    }
                return {}

            def fake_urlopen(request, timeout=30):
                asset_name = request.full_url.rpartition("?name=")[2]
                return io.BytesIO(
                    json.dumps(
                        {"name": asset_name, "state": "uploaded"}
                    ).encode("utf-8")
                )

            with (
                mock.patch.object(
                    RELEASE_MODULE,
                    "iter_releases",
                    return_value=iter([release]),
                ),
                mock.patch.object(
                    RELEASE_MODULE,
                    "_require_immutable_releases_enabled",
                ),
                mock.patch.object(
                    RELEASE_MODULE,
                    "request_json",
                    side_effect=fake_request_json,
                ),
                mock.patch.object(
                    RELEASE_MODULE,
                    "urlopen",
                    side_effect=fake_urlopen,
                ),
                mock.patch.object(
                    RELEASE_MODULE,
                    "_github_token",
                    return_value="token",
                ),
                self.assertRaisesRegex(
                    RELEASE_MODULE.ReleaseError,
                    "after upload.*not exact",
                ),
            ):
                RELEASE_MODULE.publish_release("owner/repo", sha, dist)

        self.assertFalse(any(request["method"] == "PATCH" for request in requests))

    def test_final_release_get_requires_immutable_identity(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="private-overlay-release."
        ) as temp_dir_raw:
            dist = Path(temp_dir_raw)
            sha = "a" * 40
            archive_name = f"personal-codex-{sha}.tar.gz"
            checksum_name = f"personal-codex-{sha}.sha256"
            tag_name = f"personal-codex-20260522-100000-{sha[:7]}"
            (dist / archive_name).write_bytes(b"archive")
            (dist / checksum_name).write_text("checksum\n", encoding="utf-8")
            release = {
                "id": 10,
                "tag_name": tag_name,
                "target_commitish": sha,
                "draft": True,
                "prerelease": False,
                "assets": [
                    {"id": 11, "name": archive_name, "state": "uploaded"},
                    {"id": 12, "name": checksum_name, "state": "starter"},
                ],
            }
            final_release = {
                "id": 10,
                "tag_name": tag_name,
                "target_commitish": sha,
                "draft": True,
                "prerelease": False,
                "assets": [
                    {"id": 21, "name": archive_name, "state": "uploaded"},
                    {"id": 22, "name": checksum_name, "state": "uploaded"},
                ],
            }
            cases = {
                "id": dict(final_release, id=99),
                "tag": dict(
                    final_release,
                    tag_name=f"personal-codex-20260522-100001-{sha[:7]}",
                ),
                "target": dict(final_release, target_commitish="b" * 40),
            }

            for name, refreshed in cases.items():
                with self.subTest(field=name):
                    requests: list[dict[str, object]] = []

                    def fake_request_json(
                        url: str,
                        *,
                        method: str = "GET",
                        payload=None,
                        token=None,
                    ):
                        requests.append(
                            {"url": url, "method": method, "payload": payload}
                        )
                        return refreshed if method == "GET" else {}

                    def fake_urlopen(request, timeout=30):
                        asset_name = request.full_url.rpartition("?name=")[2]
                        return io.BytesIO(
                            json.dumps(
                                {"name": asset_name, "state": "uploaded"}
                            ).encode("utf-8")
                        )

                    with (
                        mock.patch.object(
                            RELEASE_MODULE,
                            "iter_releases",
                            return_value=iter([release]),
                        ),
                        mock.patch.object(
                            RELEASE_MODULE,
                            "_require_immutable_releases_enabled",
                        ),
                        mock.patch.object(
                            RELEASE_MODULE,
                            "request_json",
                            side_effect=fake_request_json,
                        ),
                        mock.patch.object(
                            RELEASE_MODULE,
                            "urlopen",
                            side_effect=fake_urlopen,
                        ),
                        mock.patch.object(
                            RELEASE_MODULE,
                            "_github_token",
                            return_value="token",
                        ),
                        contextlib.redirect_stdout(io.StringIO()),
                        self.assertRaisesRegex(
                            RELEASE_MODULE.ReleaseError,
                            "identity changed",
                        ),
                    ):
                        RELEASE_MODULE.publish_release("owner/repo", sha, dist)

                    self.assertFalse(
                        any(request["method"] == "PATCH" for request in requests)
                    )

    def test_release_complete_requires_published_assets(self) -> None:
        sha = "a" * 40
        other_sha = "b" * 40
        complete_release = self._release_candidate(sha)
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
        pending_pair_release = dict(
            complete_release,
            assets=[
                {"name": f"personal-codex-{sha}.tar.gz", "state": "new"},
                {"name": f"personal-codex-{sha}.sha256", "state": "new"},
            ],
        )
        pending_extra_release = dict(
            complete_release,
            assets=[
                {"name": f"personal-codex-{sha}.tar.gz", "state": "uploaded"},
                {"name": f"personal-codex-{sha}.sha256", "state": "uploaded"},
                {"name": f"personal-codex-{sha}.sha256", "state": "new"},
            ],
        )
        other_sha_pending_extra_release = dict(
            complete_release,
            assets=[
                {"name": f"personal-codex-{sha}.tar.gz", "state": "uploaded"},
                {"name": f"personal-codex-{sha}.sha256", "state": "uploaded"},
                {
                    "name": f"personal-codex-{other_sha}.tar.gz",
                    "state": "new",
                },
            ],
        )
        malformed_tag_release = dict(
            complete_release,
            tag_name="personal-codex-not-a-release-tag",
        )
        wrong_tag_suffix_release = dict(
            complete_release,
            tag_name="personal-codex-20260522-100000-bbbbbbb",
        )

        with mock.patch.object(
            RELEASE_MODULE, "iter_releases", return_value=iter([complete_release])
        ):
            self.assertTrue(RELEASE_MODULE.release_complete("owner/repo", sha))

        valid_remote_only_digest = dict(
            complete_release,
            assets=[
                dict(
                    complete_release["assets"][0],
                    digest=f"sha256:{'b' * 64}",
                ),
                complete_release["assets"][1],
            ],
        )
        with mock.patch.object(
            RELEASE_MODULE,
            "iter_releases",
            return_value=iter([valid_remote_only_digest]),
        ):
            self.assertTrue(RELEASE_MODULE.release_complete("owner/repo", sha))

        invalid_remote_metadata = {
            "mutable": dict(complete_release, immutable=False),
            "missing-immutable": {
                key: value
                for key, value in complete_release.items()
                if key != "immutable"
            },
            "asset-id": dict(
                complete_release,
                assets=[
                    dict(complete_release["assets"][0], id=True),
                    complete_release["assets"][1],
                ],
            ),
            "asset-size": dict(
                complete_release,
                assets=[
                    dict(complete_release["assets"][0], size=-1),
                    complete_release["assets"][1],
                ],
            ),
            "asset-digest": dict(
                complete_release,
                assets=[
                    dict(complete_release["assets"][0], digest="sha256:BAD"),
                    complete_release["assets"][1],
                ],
            ),
            "non-object-asset": dict(
                complete_release,
                assets=[*complete_release["assets"], "invalid"],
            ),
        }
        for name, release in invalid_remote_metadata.items():
            with self.subTest(remote_metadata=name):
                with mock.patch.object(
                    RELEASE_MODULE,
                    "iter_releases",
                    return_value=iter([release]),
                ):
                    self.assertFalse(
                        RELEASE_MODULE.release_complete("owner/repo", sha)
                    )

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
        with mock.patch.object(
            RELEASE_MODULE,
            "iter_releases",
            return_value=iter([pending_pair_release]),
        ):
            self.assertFalse(RELEASE_MODULE.release_complete("owner/repo", sha))
        with mock.patch.object(
            RELEASE_MODULE,
            "iter_releases",
            return_value=iter([pending_extra_release]),
        ):
            self.assertFalse(RELEASE_MODULE.release_complete("owner/repo", sha))
        with mock.patch.object(
            RELEASE_MODULE,
            "iter_releases",
            return_value=iter([other_sha_pending_extra_release]),
        ):
            self.assertFalse(RELEASE_MODULE.release_complete("owner/repo", sha))
        for release in (malformed_tag_release, wrong_tag_suffix_release):
            with (
                mock.patch.object(
                    RELEASE_MODULE,
                    "iter_releases",
                    return_value=iter([release]),
                ),
                self.assertRaisesRegex(
                    RELEASE_MODULE.ReleaseError,
                    "invalid tag",
                ),
            ):
                RELEASE_MODULE.release_complete("owner/repo", sha)


if __name__ == "__main__":
    unittest.main()
