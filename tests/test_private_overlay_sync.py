from __future__ import annotations

import base64
import dataclasses
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
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync_private_overlay_sources.py"
SOURCE_LOCK_SCRIPT = REPO_ROOT / "scripts" / "private_overlay_source_lock.py"
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
LEGACY_REVIEW_SOURCE_PIN = (
    "c8df0f5d17e93a7b22d5fe5294baf9884ab2ba51",
    "e4081b640384cd885783637fa5aad8d21d4499d5",
)
FINAL_REVIEW_REQUIRED_ADDITIONS = frozenset(
    {
        Path("references/github-codex-terminal-carriers-v1.json"),
        Path("tests/test_github_recovery_contracts.py"),
        Path("tests/test_github_terminal_carriers.py"),
        Path("tests/test_local_codex_lane_contracts.py"),
        Path("tests/test_trusted_mac_gate_manifest.py"),
        Path(
            "scripts/independent_codex_pr_review/"
            "tests/internal_supervisor_child_fixture.py"
        ),
    }
)


def _is_legacy_review_source_pin(
    source_lock: object,
) -> bool:
    if not isinstance(source_lock, dict):
        return False
    sources = source_lock.get("sources")
    if not isinstance(sources, list):
        return False
    matches = [
        source
        for source in sources
        if isinstance(source, dict) and source.get("name") == "codex-review-workflows"
    ]
    if len(matches) != 1:
        return False
    source = matches[0]
    if source.get("repository") != "Joey-Tools/codex-review-workflows":
        return False
    return (source.get("sha"), source.get("tree")) == LEGACY_REVIEW_SOURCE_PIN


def _legacy_live_review_overlay_missing_allowance(
    source_lock: object,
) -> frozenset[Path]:
    if not _is_legacy_review_source_pin(source_lock):
        return frozenset()
    return FINAL_REVIEW_REQUIRED_ADDITIONS


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SYNC_MODULE = load_module("sync_private_overlay_sources", SYNC_SCRIPT)
SOURCE_LOCK_MODULE = load_module(
    "private_overlay_source_lock_sync_tests", SOURCE_LOCK_SCRIPT
)
RELEASE_MODULE = load_module("private_overlay_release", RELEASE_SCRIPT)
RUNTIME_MODULE = load_module("codex_personal_sync_private_overlay_sync", RUNTIME_SCRIPT)


def _final_personal_agents_text() -> str:
    data = (REPO_ROOT / SYNC_MODULE.PERSONAL_AGENTS_TARGET).read_bytes()
    return SYNC_MODULE._migrated_personal_agents_bytes(data).decode("utf-8")


def _synthetic_complete_checkout_receipt(source_root: Path, pins: tuple[object, ...]):
    def file_state(path: Path):
        return SimpleNamespace(
            path=path,
            object_identity=(1, 5, stat.S_IFREG),
            access_policy=(os.getuid(), 0o644, 1),
            size=1,
            sha256="f" * 64,
        )

    return SimpleNamespace(
        source_root=source_root,
        source_root_binding=(1, 1, os.getuid(), 0o700),
        pins=pins,
        checkouts=tuple(
            SimpleNamespace(
                name=pin.name,
                repository=pin.repository,
                checkout=source_root / pin.name,
                checkout_binding=(1, 2, os.getuid(), 0o700),
                git_directory=source_root / pin.name / ".git",
                git_directory_binding=(1, 3, os.getuid(), 0o700),
                objects_directory=source_root / pin.name / ".git" / "objects",
                objects_directory_binding=(1, 4, os.getuid(), 0o700),
                head=pin.sha,
                tree=pin.tree,
                head_file=file_state(source_root / pin.name / ".git" / "HEAD"),
                local_config_file=file_state(
                    source_root / pin.name / ".git" / "config"
                ),
                shallow=False,
                bare=False,
                detached_head=True,
                clean_worktree_and_index=True,
                promisor_or_partial_clone_absent=True,
                alternates_absent=True,
                grafts_absent=True,
                replace_refs_absent=True,
                sparse_checkout_absent=True,
                unsafe_config_absent=True,
                tracked_modes_and_index_flags_safe=True,
                object_closure_complete=True,
                strict_fsck_complete=True,
                safety_contract="private-overlay-complete-checkout-safety-v1",
            )
            for pin in pins
        ),
        safety_contract="private-overlay-complete-checkout-safety-v1",
    )


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
        base_release_sha: str | None = (
            RELEASE_MODULE.REQUIRED_PUBLIC_BASE_RELEASE_SHA
        ),
    ):
        manifest_data = SimpleNamespace(
            entries=[mock.Mock(owner="private", target=Path("skills/private"))],
            base_release_repo=base_release_repo,
            base_release_sha=base_release_sha,
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
        self.assertTrue(
            all(not destination.parent.exists() for destination in destinations)
        )
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
            manifest_path = release_root / "personal_codex" / "sync-manifest.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "owner": "private",
                        "base_release": {"repo": "Joey-Tools/codex-toolbox"},
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

    def test_verify_package_requires_exact_public_base_release_sha(self) -> None:
        sha = "7" * 40
        dist = self.root / "dist"
        dist.mkdir()

        for base_release_sha in (None, "f" * 40):
            with self.subTest(base_release_sha=base_release_sha):
                expectation = self._private_release_expectation(
                    base_release_sha=base_release_sha,
                )
                with (
                    mock.patch.object(
                        RELEASE_MODULE,
                        "_load_sync_module",
                        return_value=RUNTIME_MODULE,
                    ),
                    mock.patch.object(
                        RUNTIME_MODULE,
                        "verify_and_extract_archive",
                        return_value=(
                            self.root / f"personal-codex-{sha}",
                            expectation,
                        ),
                    ),
                    self.assertRaisesRegex(
                        RELEASE_MODULE.ReleaseError,
                        "exact public base release SHA",
                    ),
                ):
                    RELEASE_MODULE.verify_package(self.repo_root, sha, dist)

    def test_release_verifier_base_identity_matches_private_manifest(self) -> None:
        manifest = json.loads(
            (REPO_ROOT / "personal_codex" / "private-sync-manifest.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            manifest["base_release"],
            {
                "repo": RELEASE_MODULE.REQUIRED_PUBLIC_BASE_RELEASE_REPO,
                "sha": RELEASE_MODULE.REQUIRED_PUBLIC_BASE_RELEASE_SHA,
            },
        )

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
                side_effect=RELEASE_MODULE.ReleaseError("primary verification failure"),
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
                    case_root = self.root / f"replace-{replacement_kind}-{asset_role}"
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
            "checksum file" if asset_role == "checksum" else "compressed archive"
        )
        real_open_bounded_regular_file = RUNTIME_MODULE._open_bounded_regular_file

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

    @contextlib.contextmanager
    def _valid_installed_regular_file_overlay_receipt(self, name: str):
        target = self._create_regular_file_overlay_target(name)
        with self._regular_file_overlay_staging_directory(target) as scope:
            stack, staging, binding = (
                self._prepare_scoped_regular_file_overlay_candidate(scope)
            )
            with stack:
                result = SYNC_MODULE._replace_target_with_regular_file_overlays(
                    target,
                    staging,
                    (binding,),
                    staging_scope=scope,
                )
                yield result.installed_receipt

    def _trace_personal_agents_migration_order(
        self,
        *,
        agents: Path,
        legacy: bytes,
        legacy_digest: str,
        receipt_name: str,
        final_file_label: str,
        receipt_label: str,
        scope_operation: str,
        rename_endpoint: str,
    ) -> list[str]:
        events: list[str] = []
        file_event = f"file:{final_file_label}"
        receipt_event = f"receipt:{receipt_label}"
        scope_event = f"scope:{scope_operation}"
        rename_event = f"rename:{scope_operation}"
        real_assert_file = SYNC_MODULE._assert_bound_plain_file
        real_assert_installed = (
            SYNC_MODULE._assert_installed_regular_file_overlay_receipt
        )
        real_assert_scope = SYNC_MODULE._assert_regular_file_overlay_scope_binding
        real_rename = SYNC_MODULE._rename_regular_file_overlay_noreplace

        def record_file(*args, **kwargs):
            result = real_assert_file(*args, **kwargs)
            if kwargs.get("label") == final_file_label:
                events.append(file_event)
            return result

        def record_receipt(receipt, *, label):
            result = real_assert_installed(receipt, label=label)
            if label == receipt_label:
                events.append(receipt_event)
            return result

        def record_scope(scope, *, operation):
            if operation == scope_operation:
                events.append(scope_event)
            return real_assert_scope(scope, operation=operation)

        def record_rename(*args, **kwargs):
            relevant = (
                args[2] == agents.name
                if rename_endpoint == "source"
                else args[4] == agents.name
            )
            if relevant:
                events.append(rename_event)
            return real_rename(*args, **kwargs)

        self.assertIn(rename_endpoint, {"source", "target"})
        with (
            self._valid_installed_regular_file_overlay_receipt(
                receipt_name
            ) as installed_receipt,
            contextlib.ExitStack() as stack,
        ):
            repo_binding = SYNC_MODULE._pin_regular_file_overlay_directory(
                stack,
                self.repo_root,
                label="repository root",
            )
            with (
                mock.patch.object(
                    SYNC_MODULE,
                    "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
                    legacy_digest,
                ),
                mock.patch.object(
                    SYNC_MODULE,
                    "_assert_bound_plain_file",
                    side_effect=record_file,
                ),
                mock.patch.object(
                    SYNC_MODULE,
                    "_assert_installed_regular_file_overlay_receipt",
                    side_effect=record_receipt,
                ),
                mock.patch.object(
                    SYNC_MODULE,
                    "_assert_regular_file_overlay_scope_binding",
                    side_effect=record_scope,
                ),
                mock.patch.object(
                    SYNC_MODULE,
                    "_rename_regular_file_overlay_noreplace",
                    side_effect=record_rename,
                ),
            ):
                SYNC_MODULE._migrate_personal_agents_guidance(
                    repo_binding,
                    installed_receipt=installed_receipt,
                )

        agents.write_bytes(legacy)
        agents.chmod(0o644)
        return events

    def _assert_ordered_event_subsequence(
        self,
        events: list[str],
        expected: list[str],
    ) -> None:
        cursor = 0
        for event in expected:
            try:
                cursor = events.index(event, cursor) + 1
            except ValueError:
                self.fail(f"missing ordered event {event!r} in trace {events!r}")

    def _create_canonical_regular_file_overlay_rule(
        self,
        *,
        authoritative: bool = False,
        legacy_inventory: bool = False,
    ):
        if authoritative:
            rule = next(
                candidate
                for candidate in SYNC_MODULE.SYNC_RULES
                if candidate.target == SYNC_MODULE.CANONICAL_REVIEW_TARGET
            )
        else:
            rule = SYNC_MODULE.SyncRule(
                repo="canonical-repo",
                source=Path("skill"),
                target=SYNC_MODULE.CANONICAL_REVIEW_TARGET,
                regular_file_overlays=(
                    SYNC_MODULE.RegularFileOverlay(
                        source=Path("private/catalog.json"),
                        target=Path(
                            "scripts/review_runtime/synthetic-token-catalog.json"
                        ),
                    ),
                ),
            )
        source = self.source_root / rule.repo / rule.source
        inventory_profile = (
            SYNC_MODULE._CANONICAL_REVIEW_LEGACY_INVENTORY
            if legacy_inventory
            else SYNC_MODULE._CANONICAL_REVIEW_CURRENT_INVENTORY
        )
        for relative in inventory_profile.exact_files:
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("public\n", encoding="utf-8")
        if legacy_inventory:
            (source / "references/helper-contract.md").write_text(
                "Use external-review-playbook compatibility.\n",
                encoding="utf-8",
            )
        private_catalog = self.repo_root / rule.regular_file_overlays[0].source
        private_catalog.parent.mkdir(parents=True)
        private_catalog.write_text("private\n", encoding="utf-8")
        target = self.repo_root / SYNC_MODULE.CANONICAL_REVIEW_TARGET
        target.mkdir(parents=True)
        (target / "old-marker").write_text("old\n", encoding="utf-8")
        return rule, target

    def _synthetic_legacy_personal_agents(self) -> tuple[Path, bytes, str]:
        legacy_block = (
            SYNC_MODULE.PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_START
            + b" Synthetic legacy review detail.\n"
        )
        data = (
            b"# Personal Guidelines\n\n"
            + SYNC_MODULE.PERSONAL_AGENTS_LEGACY_CONSENT_LINE
            + legacy_block
            + SYNC_MODULE.PERSONAL_AGENTS_REVIEW_BLOCK_BOUNDARY
            + b"review evidence may span hosts.\n"
        )
        target = self.repo_root / SYNC_MODULE.PERSONAL_AGENTS_TARGET
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o644)
        return target, data, hashlib.sha256(legacy_block).hexdigest()

    def _migrate_personal_agents_after_first_read(self, action):
        agents, legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        real_read = SYNC_MODULE._read_regular_file_overlay_descriptor
        action_ran = False

        def read_then_act(descriptor, *, byte_limit):
            nonlocal action_ran
            data = real_read(descriptor, byte_limit=byte_limit)
            if not action_ran and data == legacy:
                action_ran = True
                action(agents, legacy)
            return data

        with contextlib.ExitStack() as stack:
            repo_binding = SYNC_MODULE._pin_regular_file_overlay_directory(
                stack,
                self.repo_root,
                label="repository root",
            )
            with (
                mock.patch.object(
                    SYNC_MODULE,
                    "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
                    legacy_digest,
                ),
                mock.patch.object(
                    SYNC_MODULE,
                    "_read_regular_file_overlay_descriptor",
                    side_effect=read_then_act,
                ),
            ):
                result = SYNC_MODULE._migrate_personal_agents_guidance(repo_binding)
        self.assertTrue(action_ran)
        return agents, legacy, result

    def _write_current_bug_triage_source(
        self,
        *,
        host_condition: str = (
            "if parsed.hostname.lower() not in DEFAULT_ALLOWED_HOSTS:"
        ),
        duplicate_recipes_scope: bool = False,
        prepended_script: str = "",
        appended_script: str = "",
        guard_decorator: str = "",
        guard_prelude: str = "",
        host_rejection_statement: str = (
            'raise ValueError("host not allowed: {}".format(parsed.hostname))'
        ),
        script_replacements: tuple[tuple[str, str], ...] = (),
    ):
        source = (
            self.source_root / "codex-debug-triage" / "skills" / "bug-triage-playbook"
        )
        scripts = source / "scripts"
        references = source / "references"
        agents = source / "agents"
        scripts.mkdir(parents=True)
        references.mkdir()
        agents.mkdir()

        (source / "SKILL.md").write_text(
            "---\n"
            "name: bug-triage-playbook\n"
            "description: Optionally transport and inspect allowlisted Jenkins-style HTTPS console, API, and ZIP artifacts with bounded authentication, redirects, output, extraction, and wall time. Use when a task has an exact remote artifact URL or a local ZIP and needs a public-safe probe, fetch, member listing, text view, or single-member extraction before diagnosis.\n"
            "---\n\n"
            "# Bounded Artifact Transport\n\n"
            "## Scope\n\n"
            "This optional public skill supplies one canonical artifact transport helper. It does not define a generic root-cause method, GitHub Actions triage, tracker lookup, remote process diagnosis, or private host policy. Use the relevant forge or tracker skill for those tasks, and use ordinary evidence-based reasoning after the requested artifact is available.\n\n"
            "The helper is `scripts/jenkins_artifact_probe.py`. Its public configuration is deliberately synthetic and fail-closed. A private installation may specialize fixed source constants through its own release process; callers cannot widen hosts, auth profiles, deadlines, or resource ceilings at runtime.\n",
            encoding="utf-8",
        )
        interface = (
            "interface:\n"
            '  display_name: "Bounded Artifact Transport"\n'
            '  short_description: "Load and route bounded Jenkins-style artifact transport."\n'
            '  default_prompt: "Use $bug-triage-playbook only to route an exact remote artifact URL or local ZIP through the bounded transport helper, then return the artifact evidence to the task\'s primary diagnostic workflow."\n'
        )
        (agents / "openai.yaml").write_text(interface, encoding="utf-8")
        recipes_scope = (
            "These recipes use the optional public helper as the canonical transport boundary. "
            "The public host and job names are synthetic. Run the installed helper directly; "
            "avoid wrapping authenticated calls in a broad shell command."
        )
        if duplicate_recipes_scope:
            recipes_scope = f"{recipes_scope}\n\n{recipes_scope}"
        (references / "jenkins-artifact-recipes.md").write_text(
            "# Bounded Jenkins-Style Artifact Recipes\n\n"
            f"{recipes_scope}\n\n"
            "```bash\n"
            "helper=example\n"
            'python3 "$helper" probe-url \\\n'
            "  'https://jenkins.example.com/job/example/42/api/json' \\\n"
            "  --auth-profile default\n"
            'python3 "$helper" show-url \\\n'
            "  'https://jenkins.example.com/job/example/42/consoleText' \\\n"
            "  --auth-profile default\n"
            'python3 "$helper" fetch-url \\\n'
            "  'https://jenkins.example.com/job/example/42/artifact/logs.zip' \\\n"
            "  --auth-profile default\n"
            "```\n",
            encoding="utf-8",
        )

        reviewed_helper = (
            REPO_ROOT
            / SYNC_MODULE.PRIVATE_BUG_TRIAGE_TARGET
            / "scripts"
            / "jenkins_artifact_probe.py"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            reviewed_helper.count(SYNC_MODULE.PRIVATE_BUG_TRIAGE_CONFIG_BLOCK),
            1,
        )
        helper = reviewed_helper.replace(
            SYNC_MODULE.PRIVATE_BUG_TRIAGE_CONFIG_BLOCK,
            SYNC_MODULE.PUBLIC_BUG_TRIAGE_CONFIG_BLOCK,
            1,
        )
        private_host_condition = "if parsed.hostname.lower() not in ALLOWED_HOSTS:"
        self.assertEqual(helper.count(private_host_condition), 1)
        helper = helper.replace(private_host_condition, host_condition, 1)
        reverse_private_transforms = (
            (
                SYNC_MODULE.PRIVATE_BUG_TRIAGE_BUILD_REMOTE_REQUEST_BLOCK,
                SYNC_MODULE.PUBLIC_BUG_TRIAGE_BUILD_REMOTE_REQUEST_BLOCK,
            ),
            (
                SYNC_MODULE.PRIVATE_BUG_TRIAGE_REDIRECT_REQUEST_CONSTRUCTION,
                SYNC_MODULE.PUBLIC_BUG_TRIAGE_REDIRECT_REQUEST_CONSTRUCTION,
            ),
            (
                SYNC_MODULE.PRIVATE_BUG_TRIAGE_BUILD_OPENER_BLOCK,
                SYNC_MODULE.PUBLIC_BUG_TRIAGE_BUILD_OPENER_BLOCK,
            ),
            (
                SYNC_MODULE.PRIVATE_BUG_TRIAGE_BLOCKABLE_SIGNALS_BLOCK,
                SYNC_MODULE.PUBLIC_BUG_TRIAGE_BLOCKABLE_SIGNALS_BLOCK,
            ),
        )
        for private, public in reverse_private_transforms:
            self.assertEqual(helper.count(private), 1)
            helper = helper.replace(private, public, 1)
        if prepended_script:
            helper = helper.replace(
                SYNC_MODULE.PUBLIC_BUG_TRIAGE_CONFIG_BLOCK,
                prepended_script + SYNC_MODULE.PUBLIC_BUG_TRIAGE_CONFIG_BLOCK,
                1,
            )
        guard_definition = (
            "def _ensure_allowed_url(url: str) -> urllib.parse.ParseResult:\n"
        )
        self.assertEqual(helper.count(guard_definition), 1)
        helper = helper.replace(
            guard_definition,
            guard_decorator + guard_definition,
            1,
        )
        guard_line = f"    {host_condition}\n"
        self.assertEqual(helper.count(guard_line), 1)
        if guard_prelude:
            helper = helper.replace(guard_line, guard_prelude + guard_line, 1)
        default_rejection = (
            '        raise ValueError("host not allowed: {}".format(parsed.hostname))\n'
        )
        self.assertEqual(helper.count(default_rejection), 1)
        helper = helper.replace(
            default_rejection,
            f"        {host_rejection_statement}\n",
            1,
        )
        for old, new in script_replacements:
            self.assertEqual(helper.count(old), 1)
            helper = helper.replace(old, new, 1)
        helper += appended_script
        (scripts / "jenkins_artifact_probe.py").write_text(helper, encoding="utf-8")
        rule = next(
            rule
            for rule in SYNC_MODULE.SYNC_RULES
            if rule.target == Path("personal_codex/skills/bug-triage-playbook")
        )
        return rule, source, interface

    def _locked_bug_triage_source(
        self,
        rule: SYNC_MODULE.SyncRule,
        source: Path,
        *,
        source_pin=None,
        migration_receipt=None,
        checkout_verification=None,
        root_object_id: str = "a" * 40,
    ) -> dict[tuple[str, Path], SYNC_MODULE._LockedRuleSource]:
        checkout = self.source_root / rule.repo
        entries = []
        blobs: dict[str, bytes] = {}
        for index, path in enumerate(sorted(source.rglob("*")), start=1):
            relative = path.relative_to(source)
            mode = stat.S_IMODE(path.stat().st_mode)
            object_id = f"{index:040x}"
            if path.is_dir():
                entries.append(
                    SimpleNamespace(
                        relative=relative,
                        kind="directory",
                        mode=mode,
                        object_id=object_id,
                    )
                )
                continue
            data = path.read_bytes()
            blobs[object_id] = data
            entries.append(
                SimpleNamespace(
                    relative=relative,
                    kind="file",
                    mode=mode,
                    object_id=object_id,
                )
            )

        def read_blob(locked_checkout: Path, object_id: str) -> bytes:
            self.assertEqual(locked_checkout, checkout)
            return blobs[object_id]

        if checkout_verification is None:
            checkout_verification = self._complete_checkout_verification(
                SimpleNamespace(
                    name=rule.repo,
                    repository=(
                        source_pin.repository
                        if source_pin is not None
                        else f"Joey-Tools/{rule.repo}"
                    ),
                    sha=(source_pin.revision if source_pin is not None else "a" * 40),
                    tree=(
                        source_pin.root_tree
                        if source_pin is not None
                        else root_object_id
                    ),
                )
            )
        return {
            (rule.repo, rule.source): SYNC_MODULE._LockedRuleSource(
                checkout=checkout,
                manifest=SimpleNamespace(
                    root_kind="tree",
                    root_object_id=root_object_id,
                    entries=tuple(entries),
                ),
                read_blob=read_blob,
                source_pin=source_pin,
                canonical_review_migration_receipt=migration_receipt,
                prewrite_checkout_verification=checkout_verification,
            )
        }

    def _complete_checkout_verification(self, *pins, source_root=None):
        checkout_root = self.source_root if source_root is None else source_root
        pin_records = tuple(
            (pin.name, pin.repository, pin.sha, pin.tree) for pin in pins
        )
        source_lock = SimpleNamespace(
            pins=tuple(pins),
            digest=hashlib.sha256(repr(pin_records).encode("utf-8")).hexdigest(),
        )

        def structured_receipt(*_args, **_kwargs):
            return _synthetic_complete_checkout_receipt(
                checkout_root,
                source_lock.pins,
            )

        source_lock_module = SimpleNamespace(
            load_source_lock=lambda _repo_root: source_lock,
            verify_checkouts=structured_receipt,
        )
        return SYNC_MODULE._verify_complete_checkouts(
            source_lock_module,
            checkout_root,
            source_lock,
            repo_root=self.repo_root,
        )

    def test_structured_checkout_receipt_rejects_forged_file_access_policy(
        self,
    ) -> None:
        pin = SimpleNamespace(
            name="example",
            repository="Joey-Tools/example",
            sha="a" * 40,
            tree="b" * 40,
        )
        receipt = _synthetic_complete_checkout_receipt(
            self.source_root,
            (pin,),
        )
        checkout = receipt.checkouts[0]
        file_state = checkout.head_file
        cases = {
            "wrong-uid": (os.getuid() + 1, 0o644, 1),
            "writable-mode": (os.getuid(), 0o666, 1),
            "multiple-links": (os.getuid(), 0o644, 2),
        }
        pin_records = ((pin.name, pin.repository, pin.sha, pin.tree),)

        for name, access_policy in cases.items():
            forged_file_state = SimpleNamespace(
                **{
                    **vars(file_state),
                    "access_policy": access_policy,
                }
            )
            forged_checkout = SimpleNamespace(
                **{
                    **vars(checkout),
                    "head_file": forged_file_state,
                }
            )
            forged_receipt = SimpleNamespace(
                **{
                    **vars(receipt),
                    "checkouts": (forged_checkout,),
                }
            )
            with self.subTest(name=name):
                self.assertFalse(
                    SYNC_MODULE._structured_checkout_receipt_is_complete(
                        forged_receipt,
                        source_root=self.source_root,
                        pins=pin_records,
                    )
                )

    def test_structured_checkout_receipt_rejects_nonprimitive_file_state(
        self,
    ) -> None:
        pin = SimpleNamespace(
            name="example",
            repository="Joey-Tools/example",
            sha="a" * 40,
            tree="b" * 40,
        )
        receipt = _synthetic_complete_checkout_receipt(
            self.source_root,
            (pin,),
        )
        checkout = receipt.checkouts[0]
        file_state = checkout.head_file
        cases = {
            "boolean-object-identity": {"object_identity": (1, 5, True)},
            "boolean-size": {"size": True},
            "nonhex-digest": {"sha256": "g" * 64},
        }
        pin_records = ((pin.name, pin.repository, pin.sha, pin.tree),)

        for name, replacement in cases.items():
            forged_file_state = SimpleNamespace(
                **{
                    **vars(file_state),
                    **replacement,
                }
            )
            forged_checkout = SimpleNamespace(
                **{
                    **vars(checkout),
                    "head_file": forged_file_state,
                }
            )
            forged_receipt = SimpleNamespace(
                **{
                    **vars(receipt),
                    "checkouts": (forged_checkout,),
                }
            )
            with self.subTest(name=name):
                self.assertFalse(
                    SYNC_MODULE._structured_checkout_receipt_is_complete(
                        forged_receipt,
                        source_root=self.source_root,
                        pins=pin_records,
                    )
                )

    def _locked_canonical_review_source(
        self,
        rule: SYNC_MODULE.SyncRule,
        source: Path,
        *,
        legacy: bool = False,
    ) -> dict[tuple[str, Path], SYNC_MODULE._LockedRuleSource]:
        policy = SYNC_MODULE.CANONICAL_REVIEW_MIGRATION_POLICY
        if legacy:
            source_pin = SYNC_MODULE._VerifiedLockedSourcePin(
                repository=policy.repository,
                revision=policy.legacy_revision,
                root_tree=policy.legacy_root_tree,
            )
            receipt = None
            root_object_id = "b" * 40
        else:
            source_pin = SYNC_MODULE._VerifiedLockedSourcePin(
                repository=policy.repository,
                revision="a" * 40,
                root_tree=policy.approved_root_tree,
            )
            root_object_id = policy.approved_review_subtree_tree
            receipt = SYNC_MODULE._CanonicalReviewMigrationReceipt(
                policy=policy,
                source_pin=source_pin,
                live_review_subtree_tree=root_object_id,
                activation_basis="exact-approved-root-tree",
            )
        checkout_verification = self._complete_checkout_verification(
            SimpleNamespace(
                name=rule.repo,
                repository=source_pin.repository,
                sha=source_pin.revision,
                tree=source_pin.root_tree,
            )
        )
        return self._locked_bug_triage_source(
            rule,
            source,
            source_pin=source_pin,
            migration_receipt=receipt,
            checkout_verification=checkout_verification,
            root_object_id=root_object_id,
        )

    def _fixture_git(self, repository: Path, *arguments: str) -> str:
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C",
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
        completed = subprocess.run(
            [
                "git",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "user.name=Private Overlay Test",
                "-c",
                "user.email=private-overlay-test@example.invalid",
                "-C",
                str(repository),
                *arguments,
            ],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            self.fail(
                f"fixture Git command failed ({arguments[0]}): "
                f"{completed.stderr.decode('utf-8', errors='replace')}"
            )
        return completed.stdout.decode("ascii").strip()

    def _fixture_git_bytes(self, repository: Path, *arguments: str) -> bytes:
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C",
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
        completed = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            self.fail(
                f"fixture Git command failed ({arguments[0]}): "
                f"{completed.stderr.decode('utf-8', errors='replace')}"
            )
        return completed.stdout

    def _canonical_migration_history(self):
        checkout = self.root / "canonical-migration-history"
        checkout.mkdir()
        self._fixture_git(checkout, "init", "--quiet")
        review_root = checkout / "skills" / "review-orchestration-playbook"
        review_root.mkdir(parents=True)
        (review_root / "SKILL.md").write_text("legacy\n", encoding="utf-8")
        (checkout / "repository.txt").write_text("common\n", encoding="utf-8")
        self._fixture_git(checkout, "add", ".")
        self._fixture_git(checkout, "commit", "--quiet", "-m", "common")
        common = self._fixture_git(checkout, "rev-parse", "HEAD")

        self._fixture_git(checkout, "switch", "--quiet", "-c", "candidate")
        (review_root / "SKILL.md").write_text("approved\n", encoding="utf-8")
        (checkout / "repository.txt").write_text("approved\n", encoding="utf-8")
        self._fixture_git(checkout, "add", ".")
        self._fixture_git(checkout, "commit", "--quiet", "-m", "candidate")
        candidate = self._fixture_git(checkout, "rev-parse", "HEAD")
        approved_root_tree = self._fixture_git(
            checkout,
            "rev-parse",
            f"{candidate}^{{tree}}",
        )
        approved_review_subtree_tree = self._fixture_git(
            checkout,
            "rev-parse",
            f"{candidate}:skills/review-orchestration-playbook",
        )

        self._fixture_git(checkout, "switch", "--quiet", "-c", "squash", common)
        (review_root / "SKILL.md").write_text("approved\n", encoding="utf-8")
        (checkout / "repository.txt").write_text("approved\n", encoding="utf-8")
        self._fixture_git(checkout, "add", ".")
        self._fixture_git(checkout, "commit", "--quiet", "-m", "squash")
        squash = self._fixture_git(checkout, "rev-parse", "HEAD")
        self.assertNotEqual(squash, candidate)
        self.assertEqual(
            self._fixture_git(checkout, "rev-parse", f"{squash}^{{tree}}"),
            approved_root_tree,
        )

        self._fixture_git(checkout, "switch", "--quiet", "-c", "future", common)
        (checkout / "future-a.txt").write_text("future a\n", encoding="utf-8")
        self._fixture_git(checkout, "add", ".")
        self._fixture_git(checkout, "commit", "--quiet", "-m", "future a")
        self._fixture_git(checkout, "switch", "--quiet", "-c", "side", squash)
        (checkout / "future-b.txt").write_text("future b\n", encoding="utf-8")
        self._fixture_git(checkout, "add", ".")
        self._fixture_git(checkout, "commit", "--quiet", "-m", "future b")
        self._fixture_git(checkout, "switch", "--quiet", "future")
        self._fixture_git(
            checkout,
            "merge",
            "--quiet",
            "--no-ff",
            "side",
            "-m",
            "future merge",
        )
        future_merge = self._fixture_git(checkout, "rev-parse", "HEAD")
        self.assertNotIn(
            squash,
            self._fixture_git(
                checkout,
                "rev-list",
                "--first-parent",
                future_merge,
            ).splitlines(),
        )
        self.assertIn(
            squash,
            self._fixture_git(
                checkout,
                "rev-list",
                f"{future_merge}^2",
            ).splitlines(),
        )
        self.assertEqual(
            self._fixture_git(
                checkout,
                "rev-parse",
                f"{future_merge}:skills/review-orchestration-playbook",
            ),
            approved_review_subtree_tree,
        )

        self._fixture_git(checkout, "switch", "--quiet", "-c", "fork", common)
        (review_root / "SKILL.md").write_text("approved\n", encoding="utf-8")
        (checkout / "repository.txt").write_text("fork\n", encoding="utf-8")
        self._fixture_git(checkout, "add", ".")
        self._fixture_git(checkout, "commit", "--quiet", "-m", "fork")
        fork = self._fixture_git(checkout, "rev-parse", "HEAD")

        self._fixture_git(
            checkout,
            "switch",
            "--quiet",
            "-c",
            "changed-subtree",
            squash,
        )
        (review_root / "SKILL.md").write_text("changed\n", encoding="utf-8")
        self._fixture_git(checkout, "add", ".")
        self._fixture_git(
            checkout,
            "commit",
            "--quiet",
            "-m",
            "changed subtree",
        )
        changed_subtree = self._fixture_git(checkout, "rev-parse", "HEAD")

        self._fixture_git(checkout, "switch", "--quiet", "-c", "moved-base", common)
        (checkout / "base-move.txt").write_text("moved base\n", encoding="utf-8")
        self._fixture_git(checkout, "add", ".")
        self._fixture_git(checkout, "commit", "--quiet", "-m", "moved base")
        (review_root / "SKILL.md").write_text("approved\n", encoding="utf-8")
        (checkout / "repository.txt").write_text("approved\n", encoding="utf-8")
        self._fixture_git(checkout, "add", ".")
        self._fixture_git(
            checkout,
            "commit",
            "--quiet",
            "-m",
            "base-move squash",
        )
        base_move_squash = self._fixture_git(checkout, "rev-parse", "HEAD")
        self.assertEqual(
            self._fixture_git(
                checkout,
                "rev-parse",
                f"{base_move_squash}:skills/review-orchestration-playbook",
            ),
            approved_review_subtree_tree,
        )
        self.assertNotEqual(
            self._fixture_git(
                checkout,
                "rev-parse",
                f"{base_move_squash}^{{tree}}",
            ),
            approved_root_tree,
        )

        policy = SYNC_MODULE.CanonicalReviewMigrationPolicy(
            repository="Joey-Tools/codex-review-workflows",
            reviewed_candidate_revision=candidate,
            reviewed_candidate_commit_payload_base64=base64.b64encode(
                self._fixture_git_bytes(
                    checkout,
                    "cat-file",
                    "commit",
                    candidate,
                )
            ).decode("ascii"),
            approved_root_tree=approved_root_tree,
            approved_review_subtree_tree=approved_review_subtree_tree,
            legacy_revision=common,
            legacy_root_tree=self._fixture_git(
                checkout,
                "rev-parse",
                f"{common}^{{tree}}",
            ),
        )
        canonical = SYNC_MODULE._CANONICAL_REVIEW_SYNC_RULE
        rule = SYNC_MODULE.SyncRule(
            repo=canonical.repo,
            source=canonical.source,
            target=canonical.target,
            replacements=canonical.replacements,
            text_extensions=canonical.text_extensions,
            exclude_names=canonical.exclude_names,
            forbidden_residuals=canonical.forbidden_residuals,
            regular_file_overlays=canonical.regular_file_overlays,
            replacement_excluded_paths=canonical.replacement_excluded_paths,
            canonical_review_migration_policy=policy,
        )
        return SimpleNamespace(
            checkout=checkout,
            rule=rule,
            policy=policy,
            candidate=candidate,
            squash=squash,
            future_merge=future_merge,
            fork=fork,
            changed_subtree=changed_subtree,
            base_move_squash=base_move_squash,
        )

    def _verified_migration_fixture_pin(self, history, revision: str):
        self._fixture_git(history.checkout, "switch", "--quiet", "--detach", revision)
        git_path = SOURCE_LOCK_MODULE._trusted_git_path()
        return SOURCE_LOCK_MODULE._verify_checkout(
            git_path,
            history.checkout,
            name="codex-review-workflows",
            repository=history.policy.repository,
            expected=None,
        )

    def _bind_migration_fixture(self, history, pin, *, complete: bool = True):
        subtree_tree = self._fixture_git(
            history.checkout,
            "rev-parse",
            f"{pin.sha}:{history.rule.source.as_posix()}",
        )
        manifest = SimpleNamespace(root_object_id=subtree_tree)
        verification = (
            self._complete_checkout_verification(
                SimpleNamespace(
                    name=history.checkout.name,
                    repository=pin.repository,
                    sha=pin.sha,
                    tree=pin.tree,
                ),
                source_root=history.checkout.parent,
            )
            if complete
            else True
        )
        with mock.patch.object(
            SYNC_MODULE,
            "_CANONICAL_REVIEW_SYNC_RULE",
            history.rule,
        ):
            return SYNC_MODULE._bind_canonical_review_migration_source(
                SOURCE_LOCK_MODULE,
                history.checkout,
                pin,
                manifest,
                history.rule,
                complete_checkout_verification=verification,
            )

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

    def test_private_ci_workflow_sync_rule_is_unique_and_byte_exact(self) -> None:
        canonical_source = Path(
            "skills/review-orchestration-playbook/tests/fixtures/ci/private.yml"
        )
        private_target = Path(".github/workflows/ci.yml")
        source_keys = [(rule.repo, rule.source) for rule in SYNC_MODULE.SYNC_RULES]
        targets = [rule.target for rule in SYNC_MODULE.SYNC_RULES]
        rules = [
            rule
            for rule in SYNC_MODULE.SYNC_RULES
            if rule.source == canonical_source or rule.target == private_target
        ]

        self.assertEqual(len(source_keys), len(set(source_keys)))
        self.assertEqual(len(targets), len(set(targets)))
        self.assertEqual(len(rules), 1)
        rule = rules[0]
        self.assertEqual(rule.repo, "codex-review-workflows")
        self.assertEqual(rule.source, canonical_source)
        self.assertEqual(rule.target, private_target)
        self.assertFalse(rule.replacements)
        self.assertFalse(rule.regular_file_overlays)

        payload = (
            b"name: Private CI\n"
            b"# Preserve canonical fixture bytes without text transforms.\n"
        )
        source = self.source_root / rule.repo / rule.source
        source.parent.mkdir(parents=True)
        source.write_bytes(payload)

        SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

        self.assertEqual((self.repo_root / rule.target).read_bytes(), payload)

    def test_toolbox_generated_surface_and_receipt_rules_are_complete(self) -> None:
        generated_paths = {
            Path("scripts/codex_personal_sync.py"),
            Path("tests/test_codex_personal_sync.py"),
            Path("schema/sync-manifest.schema.json"),
            Path("tests/test_personal_sync_reconciliation_safety.py"),
            Path("tests/test_release_retention.py"),
            Path("tests/test_scheduler_doctor.py"),
        }
        receipt_contract_paths = {
            Path("generated-sync-source-lock.json"),
            Path("scripts/verify_generated_sync_source_lock.py"),
            Path("tests/test_generated_sync_source_lock.py"),
        }
        toolbox_rules = {
            (rule.source, rule.target)
            for rule in SYNC_MODULE.SYNC_RULES
            if rule.repo == "codex-toolbox"
        }

        self.assertTrue(
            {
                (path, path) for path in generated_paths | receipt_contract_paths
            }.issubset(toolbox_rules)
        )

    def test_review_sync_preserves_private_ci_fixture_and_personalization(
        self,
    ) -> None:
        fixture_relative = Path("tests/fixtures/ci/private.yml")
        ci_rule = next(
            rule
            for rule in SYNC_MODULE.SYNC_RULES
            if rule.target == Path(".github/workflows/ci.yml")
        )
        review_rule = next(
            rule
            for rule in SYNC_MODULE.SYNC_RULES
            if rule.target == SYNC_MODULE.CANONICAL_REVIEW_TARGET
        )
        self.assertEqual(
            review_rule.replacement_excluded_paths,
            (fixture_relative,),
        )

        source = self.source_root / review_rule.repo / review_rule.source
        for relative in SYNC_MODULE._CANONICAL_REVIEW_CURRENT_INVENTORY.exact_files:
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("public\n", encoding="utf-8")
        (source / "SKILL.md").write_text(
            "Use this when the user asks.\n",
            encoding="utf-8",
        )
        fixture_payload = (
            b"name: Private CI\n"
            b"# Keep the user and user-specific profile bytes canonical.\n"
        )
        (source / fixture_relative).write_bytes(fixture_payload)

        private_catalog = self.repo_root / review_rule.regular_file_overlays[0].source
        private_catalog.parent.mkdir(parents=True)
        private_catalog.write_bytes(b'{"pool":"private"}\n')
        agents, legacy_agents, legacy_digest = self._synthetic_legacy_personal_agents()

        with mock.patch.object(
            SYNC_MODULE,
            "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
            legacy_digest,
        ):
            expected_agents = SYNC_MODULE._migrated_personal_agents_bytes(legacy_agents)
            SYNC_MODULE.sync_sources(
                self.repo_root,
                self.source_root,
                (review_rule,),
                locked_sources=self._locked_canonical_review_source(
                    review_rule,
                    source,
                ),
            )
            SYNC_MODULE.sync_sources(
                self.repo_root,
                self.source_root,
                (ci_rule,),
            )

        live_ci = self.repo_root / ci_rule.target
        nested_fixture = self.repo_root / review_rule.target / fixture_relative
        synced_skill = self.repo_root / review_rule.target / "SKILL.md"
        self.assertEqual(live_ci.read_bytes(), fixture_payload)
        self.assertEqual(nested_fixture.read_bytes(), fixture_payload)
        self.assertEqual(
            synced_skill.read_text(encoding="utf-8"),
            "Use this when Joey asks.\n",
        )
        self.assertEqual(agents.read_bytes(), expected_agents)

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

    def test_sync_removes_retired_skill_targets(self) -> None:
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
        for relative in SYNC_MODULE._CANONICAL_REVIEW_CURRENT_INVENTORY.exact_files:
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

        for relative in SYNC_MODULE._CANONICAL_REVIEW_CURRENT_INVENTORY.exact_files:
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

    def test_canonical_review_inventory_profiles_are_exact_and_disjoint(
        self,
    ) -> None:
        current = SYNC_MODULE._CANONICAL_REVIEW_CURRENT_INVENTORY
        legacy = SYNC_MODULE._CANONICAL_REVIEW_LEGACY_INVENTORY
        self.assertEqual(len(current.required_files), 151)
        self.assertEqual(len(current.exact_files), 156)
        self.assertEqual(len(current.independent_required_files), 77)
        self.assertEqual(len(legacy.required_files), 145)
        self.assertEqual(len(legacy.exact_files), 150)
        self.assertEqual(len(legacy.independent_required_files), 78)
        self.assertEqual(
            legacy.exact_files - current.exact_files,
            SYNC_MODULE._CANONICAL_REVIEW_LEGACY_ONLY_FILES,
        )
        self.assertEqual(
            current.exact_files - legacy.exact_files,
            SYNC_MODULE._CANONICAL_REVIEW_CURRENT_ONLY_FILES,
        )

        def build_target(name: str, profile) -> Path:
            target = self.repo_root / name
            for relative in profile.exact_files:
                path = target / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("canonical\n", encoding="utf-8")
            return target

        legacy_target = build_target("legacy-inventory", legacy)
        (legacy_target / "references/helper-contract.md").write_text(
            "Use external-review-playbook compatibility.\n",
            encoding="utf-8",
        )
        SYNC_MODULE._validate_canonical_review_target_contents(
            legacy_target,
            inventory_profile=legacy,
        )
        with self.assertRaisesRegex(SYNC_MODULE.SyncError, "exact tree inventory"):
            SYNC_MODULE._validate_canonical_review_target_contents(legacy_target)

        current_target = build_target("current-inventory", current)
        SYNC_MODULE._validate_canonical_review_target_contents(current_target)
        with self.assertRaisesRegex(SYNC_MODULE.SyncError, "exact tree inventory"):
            SYNC_MODULE._validate_canonical_review_target_contents(
                current_target,
                inventory_profile=legacy,
            )

        independent_root = legacy_target / SYNC_MODULE.INDEPENDENT_CODEX_REVIEW_ROOT
        unexpected = independent_root / "review_supervisor/unreviewed.py"
        unexpected.write_text("unexpected\n", encoding="utf-8")
        with self.assertRaisesRegex(SYNC_MODULE.SyncError, "exact tree inventory"):
            SYNC_MODULE._validate_canonical_review_target_contents(
                legacy_target,
                inventory_profile=legacy,
            )
        unexpected.unlink()
        missing = independent_root / "README.md"
        missing.unlink()
        with self.assertRaisesRegex(SYNC_MODULE.SyncError, "missing required file"):
            SYNC_MODULE._validate_canonical_review_target_contents(
                legacy_target,
                inventory_profile=legacy,
            )

    def test_canonical_review_target_requires_policy_runtime_and_tests(
        self,
    ) -> None:
        policy_required_files = (
            Path("references/canonical-claude-lane.md"),
            Path("references/claude-2.1.212-stream-schema.json"),
            Path("references/claude-stream-compatibility.json"),
            Path("references/claude-stream-schema.json"),
            Path("references/github-codex-evidence-authority.md"),
            Path("references/github-codex-terminal-carriers-v1.json"),
            Path("references/local-codex-lane.md"),
            Path("references/review-workspace.md"),
            Path("scripts/build_claude_keychain_broker_macos.sh"),
            Path("scripts/install_claude_keychain_broker_macos.sh"),
            Path("scripts/independent_codex_pr_review/review_supervisor/supervisor.py"),
            Path(
                "scripts/independent_codex_pr_review/"
                "tests/internal_supervisor_child_fixture.py"
            ),
            Path(
                "scripts/independent_codex_pr_review/"
                "tests/run_required_no_child_profile.py"
            ),
            Path("scripts/named_claude_preflight"),
            Path("scripts/named_lane_guard"),
            Path("scripts/review_runtime/claude_keychain_broker"),
            Path("scripts/review_runtime/claude_stream_contract.py"),
            Path("scripts/review_runtime/claude_version_policy.py"),
            Path("scripts/review_runtime/fd_exec.py"),
            Path("scripts/review_runtime/named_claude_preflight.py"),
            Path("scripts/review_runtime/named_lane.py"),
            Path("scripts/review_runtime/claude_refresh_lock.py"),
            Path("scripts/review_runtime/review_result.py"),
            Path("scripts/review_runtime/review_workspace.py"),
            Path("scripts/validate_claude_stream.py"),
            Path("tests/fixtures/compat/codex-review-gate.yml"),
            Path("tests/test_fd_exec.py"),
            Path("tests/test_github_recovery_contracts.py"),
            Path("tests/test_github_terminal_carriers.py"),
            Path("tests/test_local_codex_lane_contracts.py"),
            Path("tests/test_claude_refresh_lock.py"),
            Path("tests/test_named_claude_preflight.py"),
            Path("tests/test_named_lane.py"),
            Path("tests/test_review_result.py"),
            Path("tests/test_review_workspace.py"),
            Path("tests/test_installer.py"),
            Path("tests/test_trusted_mac_gate_manifest.py"),
            Path("tests/test_validate_claude_stream.py"),
        )
        self.assertTrue(
            set(policy_required_files).issubset(
                set(SYNC_MODULE.CANONICAL_REVIEW_REQUIRED_FILES)
            )
        )
        self.assertNotIn(
            Path("references/base-only-retarget-state-machine.json"),
            SYNC_MODULE.CANONICAL_REVIEW_REQUIRED_FILES,
        )
        for retired_public_surface in (
            Path("references/helper-contract.md"),
            Path("scripts/independent_codex_pr_review/README.md"),
            Path("scripts/independent_codex_pr_review/independent-codex-pr-review"),
        ):
            with self.subTest(retired_public_surface=retired_public_surface):
                self.assertNotIn(
                    retired_public_surface,
                    SYNC_MODULE.CANONICAL_REVIEW_REQUIRED_FILES,
                )
        complete_required_files = set(
            SYNC_MODULE._CANONICAL_REVIEW_CURRENT_INVENTORY.exact_files
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

    def test_independent_supervisor_sync_uses_exact_file_inventory(self) -> None:
        review_root = REPO_ROOT / SYNC_MODULE.CANONICAL_REVIEW_TARGET
        supervisor_root = review_root / SYNC_MODULE.INDEPENDENT_CODEX_REVIEW_ROOT
        source_lock = json.loads(
            (REPO_ROOT / "private-overlay-source-lock.json").read_text(encoding="utf-8")
        )
        transitional_missing = _legacy_live_review_overlay_missing_allowance(
            source_lock
        )
        retired_public_entrypoints = {
            Path("README.md"),
            Path("independent-codex-pr-review"),
        }
        internal_child_fixture = Path("tests/internal_supervisor_child_fixture.py")
        actual = {
            path.relative_to(supervisor_root)
            for path in supervisor_root.rglob("*")
            if path.is_file() and path.name != "__pycache__" and path.suffix != ".pyc"
        }
        self.assertTrue(
            retired_public_entrypoints.isdisjoint(
                SYNC_MODULE.INDEPENDENT_CODEX_REVIEW_REQUIRED_FILES
            )
        )
        required = set(SYNC_MODULE.INDEPENDENT_CODEX_REVIEW_REQUIRED_FILES)
        normalized_actual = actual - retired_public_entrypoints
        self.assertIn(internal_child_fixture, required)
        allowed_supervisor_missing = {
            relative.relative_to(SYNC_MODULE.INDEPENDENT_CODEX_REVIEW_ROOT)
            for relative in transitional_missing
            if relative.is_relative_to(SYNC_MODULE.INDEPENDENT_CODEX_REVIEW_ROOT)
        }
        self.assertTrue(
            (required - normalized_actual).issubset(allowed_supervisor_missing)
        )
        self.assertEqual(normalized_actual - required, set())
        live_missing = {
            relative
            for relative in FINAL_REVIEW_REQUIRED_ADDITIONS
            if not (review_root / relative).is_file()
        }
        self.assertTrue(live_missing.issubset(transitional_missing))
        self.assertIn(
            Path("review_supervisor/supervisor.py"),
            SYNC_MODULE.INDEPENDENT_CODEX_REVIEW_REQUIRED_FILES,
        )
        self.assertIn(
            Path("tests/test_supervisor.py"),
            SYNC_MODULE.INDEPENDENT_CODEX_REVIEW_REQUIRED_FILES,
        )

        target = self.repo_root / "canonical-review-exact-inventory"
        for relative in SYNC_MODULE._CANONICAL_REVIEW_CURRENT_INVENTORY.exact_files:
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("canonical\n", encoding="utf-8")
        unexpected = (
            target
            / SYNC_MODULE.INDEPENDENT_CODEX_REVIEW_ROOT
            / "review_supervisor/unreviewed.py"
        )
        unexpected.write_text("unexpected\n", encoding="utf-8")

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "exact tree inventory mismatch",
        ):
            SYNC_MODULE._validate_canonical_review_target_contents(target)

    def test_live_review_overlay_allowance_requires_exact_old_source_pin(
        self,
    ) -> None:
        old_source = {
            "name": "codex-review-workflows",
            "repository": "Joey-Tools/codex-review-workflows",
            "sha": LEGACY_REVIEW_SOURCE_PIN[0],
            "tree": LEGACY_REVIEW_SOURCE_PIN[1],
        }
        self.assertEqual(
            _legacy_live_review_overlay_missing_allowance({"sources": [old_source]}),
            FINAL_REVIEW_REQUIRED_ADDITIONS,
        )

        cases = {
            "new-sha": {**old_source, "sha": "a" * 40},
            "new-tree": {**old_source, "tree": "b" * 40},
            "wrong-repository": {
                **old_source,
                "repository": "Joey-Tools/other-review-workflows",
            },
            "wrong-name": {**old_source, "name": "other-review-workflows"},
        }
        for name, source in cases.items():
            with self.subTest(name=name):
                self.assertEqual(
                    _legacy_live_review_overlay_missing_allowance(
                        {"sources": [source]}
                    ),
                    frozenset(),
                )
        self.assertEqual(
            _legacy_live_review_overlay_missing_allowance(
                {"sources": [old_source, dict(old_source)]}
            ),
            frozenset(),
        )
        self.assertEqual(
            _legacy_live_review_overlay_missing_allowance(
                {
                    "sources": [
                        old_source,
                        {
                            **old_source,
                            "repository": "Joey-Tools/other-review-workflows",
                        },
                    ]
                }
            ),
            frozenset(),
        )

    def test_canonical_review_migration_policy_binds_candidate_commit_and_trees(
        self,
    ) -> None:
        policy = SYNC_MODULE.CANONICAL_REVIEW_MIGRATION_POLICY
        payload = SYNC_MODULE._validate_reviewed_candidate_commit_proof(policy)
        self.assertEqual(
            hashlib.sha1(
                f"commit {len(payload)}\0".encode("ascii") + payload,
                usedforsecurity=False,
            ).hexdigest(),
            policy.reviewed_candidate_revision,
        )
        self.assertEqual(
            policy.reviewed_candidate_revision,
            "b160b6fd0b3a0da4e25a74fbdb6bd3750c7a9bb2",
        )
        self.assertEqual(
            policy.approved_root_tree,
            "69475da88941082e2557ca875c82e4a0d38a173f",
        )
        self.assertEqual(
            policy.approved_review_subtree_tree,
            "7b08cb84a07c4a846d26ecde538c740e7772f9e7",
        )

    def test_canonical_review_migration_accepts_same_tree_squash(self) -> None:
        history = self._canonical_migration_history()
        pin = self._verified_migration_fixture_pin(history, history.squash)

        source_pin, receipt = self._bind_migration_fixture(history, pin)

        self.assertNotEqual(source_pin.revision, history.candidate)
        self.assertEqual(source_pin.root_tree, history.policy.approved_root_tree)
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.activation_basis, "exact-approved-root-tree")

    def test_canonical_review_migration_accepts_squash_after_candidate_prune(
        self,
    ) -> None:
        history = self._canonical_migration_history()
        self._fixture_git(history.checkout, "branch", "-D", "candidate")
        self._fixture_git(
            history.checkout,
            "reflog",
            "expire",
            "--expire=now",
            "--all",
        )
        self._fixture_git(history.checkout, "gc", "--prune=now")
        missing = subprocess.run(
            [
                "git",
                "-C",
                str(history.checkout),
                "cat-file",
                "-e",
                f"{history.candidate}^{{commit}}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        self.assertNotEqual(missing.returncode, 0)
        pin = self._verified_migration_fixture_pin(history, history.squash)

        source_pin, receipt = self._bind_migration_fixture(history, pin)

        self.assertEqual(source_pin.root_tree, history.policy.approved_root_tree)
        self.assertEqual(receipt.activation_basis, "exact-approved-root-tree")

        descendant_pin = self._verified_migration_fixture_pin(
            history,
            history.future_merge,
        )
        descendant_source, descendant_receipt = self._bind_migration_fixture(
            history,
            descendant_pin,
        )
        self.assertEqual(descendant_source.revision, history.future_merge)
        self.assertEqual(
            descendant_receipt.activation_basis,
            "bounded-approved-root-tree-ancestor",
        )

    def test_legacy_source_accepts_offline_commit_proof_without_approved_tree(
        self,
    ) -> None:
        history = self._canonical_migration_history()
        self._fixture_git(
            history.checkout,
            "switch",
            "--quiet",
            "--detach",
            history.policy.legacy_revision,
        )
        branches = self._fixture_git(
            history.checkout,
            "for-each-ref",
            "--format=%(refname:short)",
            "refs/heads",
        ).splitlines()
        for branch in branches:
            self._fixture_git(history.checkout, "branch", "-D", branch)
        self._fixture_git(
            history.checkout,
            "reflog",
            "expire",
            "--expire=now",
            "--all",
        )
        self._fixture_git(history.checkout, "gc", "--prune=now")
        missing_tree = subprocess.run(
            [
                "git",
                "-C",
                str(history.checkout),
                "cat-file",
                "-e",
                f"{history.policy.approved_root_tree}^{{tree}}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        self.assertNotEqual(missing_tree.returncode, 0)
        pin = self._verified_migration_fixture_pin(
            history,
            history.policy.legacy_revision,
        )

        source_pin, receipt = self._bind_migration_fixture(history, pin)

        self.assertEqual(source_pin.revision, history.policy.legacy_revision)
        self.assertIsNone(receipt)

    def test_legacy_source_rejects_tampered_offline_commit_proof(self) -> None:
        history = self._canonical_migration_history()
        pin = self._verified_migration_fixture_pin(
            history,
            history.policy.legacy_revision,
        )
        payload = base64.b64decode(
            history.policy.reviewed_candidate_commit_payload_base64,
            validate=True,
        )
        policy = dataclasses.replace(
            history.policy,
            reviewed_candidate_commit_payload_base64=base64.b64encode(
                payload + b"tampered"
            ).decode("ascii"),
        )
        altered = SimpleNamespace(
            **{
                **vars(history),
                "policy": policy,
                "rule": dataclasses.replace(
                    history.rule,
                    canonical_review_migration_policy=policy,
                ),
            }
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "commit proof differs",
        ):
            self._bind_migration_fixture(altered, pin)

    def test_nonlegacy_source_rejects_missing_approved_tree_object(self) -> None:
        history = self._canonical_migration_history()
        pin = self._verified_migration_fixture_pin(history, history.future_merge)
        tree_object = (
            history.checkout
            / ".git"
            / "objects"
            / history.policy.approved_root_tree[:2]
            / history.policy.approved_root_tree[2:]
        )
        self.assertTrue(tree_object.is_file())
        tree_object.unlink()

        with self.assertRaises(SOURCE_LOCK_MODULE.SourceLockError):
            self._bind_migration_fixture(history, pin)

    def test_canonical_review_migration_rejects_invalid_candidate_proofs(
        self,
    ) -> None:
        history = self._canonical_migration_history()
        pin = self._verified_migration_fixture_pin(history, history.squash)
        payload = base64.b64decode(
            history.policy.reviewed_candidate_commit_payload_base64,
            validate=True,
        )
        cases = {
            "missing": dataclasses.replace(
                history.policy,
                reviewed_candidate_commit_payload_base64="",
            ),
            "tampered": dataclasses.replace(
                history.policy,
                reviewed_candidate_commit_payload_base64=base64.b64encode(
                    payload + b"tampered"
                ).decode("ascii"),
            ),
            "wrong candidate": dataclasses.replace(
                history.policy,
                reviewed_candidate_revision="f" * 40,
            ),
            "wrong root": dataclasses.replace(
                history.policy,
                approved_root_tree="e" * 40,
            ),
            "wrong subtree": dataclasses.replace(
                history.policy,
                approved_review_subtree_tree="d" * 40,
            ),
        }
        for label, policy in cases.items():
            rule = dataclasses.replace(
                history.rule,
                canonical_review_migration_policy=policy,
            )
            altered = SimpleNamespace(
                **{**vars(history), "policy": policy, "rule": rule}
            )
            with self.subTest(label=label), self.assertRaises(SYNC_MODULE.SyncError):
                self._bind_migration_fixture(altered, pin)

    def test_canonical_review_migration_accepts_bounded_merge_descendant(
        self,
    ) -> None:
        history = self._canonical_migration_history()
        pin = self._verified_migration_fixture_pin(history, history.future_merge)
        self.assertNotEqual(pin.tree, history.policy.approved_root_tree)
        first_parent_history = self._fixture_git(
            history.checkout,
            "rev-list",
            "--first-parent",
            history.future_merge,
        ).splitlines()
        self.assertNotIn(history.squash, first_parent_history)
        self.assertIn(
            history.squash,
            self._fixture_git(
                history.checkout,
                "rev-list",
                f"{history.future_merge}^2",
            ).splitlines(),
        )

        source_pin, receipt = self._bind_migration_fixture(history, pin)

        self.assertEqual(source_pin.revision, history.future_merge)
        self.assertIsNotNone(receipt)
        self.assertEqual(
            receipt.activation_basis,
            "bounded-approved-root-tree-ancestor",
        )

    def test_canonical_review_migration_rejects_non_descendant(self) -> None:
        history = self._canonical_migration_history()
        pin = self._verified_migration_fixture_pin(history, history.fork)
        self.assertNotEqual(pin.tree, history.policy.approved_root_tree)

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "anchor-refresh-required",
        ):
            self._bind_migration_fixture(history, pin)

    def test_canonical_review_migration_base_move_requires_anchor_refresh(
        self,
    ) -> None:
        history = self._canonical_migration_history()
        pin = self._verified_migration_fixture_pin(
            history,
            history.base_move_squash,
        )
        self.assertNotEqual(pin.tree, history.policy.approved_root_tree)
        self.assertEqual(
            self._fixture_git(
                history.checkout,
                "rev-parse",
                f"{pin.sha}:{history.rule.source.as_posix()}",
            ),
            history.policy.approved_review_subtree_tree,
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "anchor-refresh-required",
        ):
            self._bind_migration_fixture(history, pin)

        refreshed_policy = SYNC_MODULE.CanonicalReviewMigrationPolicy(
            repository=history.policy.repository,
            reviewed_candidate_revision=pin.sha,
            reviewed_candidate_commit_payload_base64=base64.b64encode(
                self._fixture_git_bytes(
                    history.checkout,
                    "cat-file",
                    "commit",
                    pin.sha,
                )
            ).decode("ascii"),
            approved_root_tree=pin.tree,
            approved_review_subtree_tree=(history.policy.approved_review_subtree_tree),
            legacy_revision=history.policy.legacy_revision,
            legacy_root_tree=history.policy.legacy_root_tree,
        )
        refreshed_rule = SYNC_MODULE.SyncRule(
            repo=history.rule.repo,
            source=history.rule.source,
            target=history.rule.target,
            replacements=history.rule.replacements,
            text_extensions=history.rule.text_extensions,
            exclude_names=history.rule.exclude_names,
            forbidden_residuals=history.rule.forbidden_residuals,
            regular_file_overlays=history.rule.regular_file_overlays,
            replacement_excluded_paths=history.rule.replacement_excluded_paths,
            canonical_review_migration_policy=refreshed_policy,
        )
        refreshed = SimpleNamespace(
            **{
                **vars(history),
                "policy": refreshed_policy,
                "rule": refreshed_rule,
            }
        )

        source_pin, receipt = self._bind_migration_fixture(refreshed, pin)

        self.assertEqual(source_pin.root_tree, refreshed_policy.approved_root_tree)
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.activation_basis, "exact-approved-root-tree")

    def test_pruned_base_move_squash_reports_anchor_refresh_required(
        self,
    ) -> None:
        history = self._canonical_migration_history()
        self._fixture_git(
            history.checkout,
            "switch",
            "--quiet",
            "--detach",
            history.base_move_squash,
        )
        branches = self._fixture_git(
            history.checkout,
            "for-each-ref",
            "--format=%(refname:short)",
            "refs/heads",
        ).splitlines()
        for branch in branches:
            self._fixture_git(history.checkout, "branch", "-D", branch)
        self._fixture_git(
            history.checkout,
            "reflog",
            "expire",
            "--expire=now",
            "--all",
        )
        self._fixture_git(history.checkout, "gc", "--prune=now")
        missing_tree = subprocess.run(
            [
                "git",
                "-C",
                str(history.checkout),
                "cat-file",
                "-e",
                f"{history.policy.approved_root_tree}^{{tree}}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        self.assertNotEqual(missing_tree.returncode, 0)
        pin = self._verified_migration_fixture_pin(
            history,
            history.base_move_squash,
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "anchor-refresh-required",
        ):
            self._bind_migration_fixture(history, pin)

    def test_canonical_review_migration_rejects_changed_review_subtree(
        self,
    ) -> None:
        history = self._canonical_migration_history()
        pin = self._verified_migration_fixture_pin(history, history.changed_subtree)

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "live subtree is not approved",
        ):
            self._bind_migration_fixture(history, pin)

    def test_canonical_review_migration_rejects_wrong_locked_sha_or_tree(
        self,
    ) -> None:
        history = self._canonical_migration_history()
        actual = self._verified_migration_fixture_pin(history, history.squash)
        subtree_tree = self._fixture_git(
            history.checkout,
            "rev-parse",
            f"{actual.sha}:{history.rule.source.as_posix()}",
        )
        manifest = SimpleNamespace(root_object_id=subtree_tree)
        cases = {
            "revision": SimpleNamespace(
                repository=actual.repository,
                sha="f" * 40,
                tree=actual.tree,
            ),
            "root tree": SimpleNamespace(
                repository=actual.repository,
                sha=actual.sha,
                tree="e" * 40,
            ),
        }
        with mock.patch.object(
            SYNC_MODULE,
            "_CANONICAL_REVIEW_SYNC_RULE",
            history.rule,
        ):
            for label, pin in cases.items():
                with (
                    self.subTest(label=label),
                    self.assertRaisesRegex(
                        SYNC_MODULE.SyncError,
                        f"live {label} differs from the source lock",
                    ),
                ):
                    SYNC_MODULE._bind_canonical_review_migration_source(
                        SOURCE_LOCK_MODULE,
                        history.checkout,
                        pin,
                        manifest,
                        history.rule,
                        complete_checkout_verification=(
                            self._complete_checkout_verification(
                                SimpleNamespace(
                                    name=history.checkout.name,
                                    repository=pin.repository,
                                    sha=pin.sha,
                                    tree=pin.tree,
                                ),
                                source_root=history.checkout.parent,
                            )
                        ),
                    )

    def test_canonical_review_migration_requires_complete_checkout_proof(
        self,
    ) -> None:
        history = self._canonical_migration_history()
        pin = self._verified_migration_fixture_pin(history, history.squash)

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "locally complete checkout verification receipt",
        ):
            self._bind_migration_fixture(history, pin, complete=False)

    def test_canonical_review_migration_rejects_missing_ancestry_object(
        self,
    ) -> None:
        history = self._canonical_migration_history()
        pin = self._verified_migration_fixture_pin(history, history.future_merge)
        object_path = (
            history.checkout
            / ".git"
            / "objects"
            / history.squash[:2]
            / history.squash[2:]
        )
        self.assertTrue(object_path.is_file())
        object_path.unlink()

        with self.assertRaises(SOURCE_LOCK_MODULE.SourceLockError):
            self._bind_migration_fixture(history, pin)

    def test_review_sync_removes_stale_public_surfaces(self) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule()
        stale_surfaces = (
            target / SYNC_MODULE.INDEPENDENT_CODEX_REVIEW_ROOT / "README.md",
            target
            / SYNC_MODULE.INDEPENDENT_CODEX_REVIEW_ROOT
            / "independent-codex-pr-review",
            target / "references/helper-contract.md",
        )
        for stale_surface in stale_surfaces:
            stale_surface.parent.mkdir(parents=True, exist_ok=True)
            stale_surface.write_text("stale public surface\n", encoding="utf-8")

        SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

        for stale_surface in stale_surfaces:
            with self.subTest(stale_surface=stale_surface.name):
                self.assertFalse(stale_surface.exists())
        self.assertTrue(
            (
                target
                / SYNC_MODULE.INDEPENDENT_CODEX_REVIEW_ROOT
                / "review_supervisor/supervisor.py"
            ).is_file()
        )
        self.assertTrue(
            (
                target
                / SYNC_MODULE.INDEPENDENT_CODEX_REVIEW_ROOT
                / "tests/test_supervisor.py"
            ).is_file()
        )

    def test_approved_review_sync_migrates_personal_agents_after_tree(
        self,
    ) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule(
            authoritative=True
        )
        agents, legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        source = self.source_root / rule.repo / rule.source
        locked_sources = self._locked_canonical_review_source(rule, source)
        observed_installed_tree: list[bool] = []
        events: list[str] = []
        real_migrate = SYNC_MODULE._migrate_personal_agents_after_canonical_review_sync
        real_assert_installed = (
            SYNC_MODULE._assert_installed_regular_file_overlay_receipt
        )
        real_rename = SYNC_MODULE._rename_regular_file_overlay_noreplace

        def migrate_after_tree(
            repo_binding,
            migrated_rule,
            locked_source,
            installed_migration_receipt,
            personal_agents_plan,
        ):
            observed_installed_tree.append(
                not (target / "old-marker").exists()
                and (target / "SKILL.md").read_bytes() == b"public\n"
            )
            installed_receipt = installed_migration_receipt.installed_receipt
            os.fstat(installed_receipt.root_descriptor)
            os.fstat(installed_receipt.target_parent.descriptor)
            return real_migrate(
                repo_binding,
                migrated_rule,
                locked_source,
                installed_migration_receipt,
                personal_agents_plan,
            )

        def record_installed_validation(receipt, *, label):
            real_assert_installed(receipt, label=label)
            events.append(f"validate:{label}")

        def record_rename(*args, **kwargs):
            result = real_rename(*args, **kwargs)
            if args[4] == SYNC_MODULE.PERSONAL_AGENTS_TARGET.name:
                events.append("AGENTS publish")
            return result

        with (
            mock.patch.object(
                SYNC_MODULE,
                "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
                legacy_digest,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_migrate_personal_agents_after_canonical_review_sync",
                side_effect=migrate_after_tree,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_assert_installed_regular_file_overlay_receipt",
                side_effect=record_installed_validation,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_rename_regular_file_overlay_noreplace",
                side_effect=record_rename,
            ),
        ):
            expected = SYNC_MODULE._migrated_personal_agents_bytes(legacy)
            SYNC_MODULE.sync_sources(
                self.repo_root,
                self.source_root,
                (rule,),
                locked_sources=locked_sources,
            )

        self.assertEqual(observed_installed_tree, [True])
        install_validation = events.index("validate:post-install validation")
        agents_publish = events.index("AGENTS publish")
        post_agents_validation = events.index("validate:post-AGENTS publication")
        self.assertLess(install_validation, agents_publish)
        self.assertLess(agents_publish, post_agents_validation)
        self.assertEqual(agents.read_bytes(), expected)
        self.assertEqual(
            SYNC_MODULE._personal_agents_review_guidance_state(expected),
            "current",
        )

    def test_locked_authoritative_review_sync_migrates_personal_agents(
        self,
    ) -> None:
        rule, _target = self._create_canonical_regular_file_overlay_rule(
            authoritative=True
        )
        agents, legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        source = self.source_root / rule.repo / rule.source
        locked_sources = self._locked_canonical_review_source(rule, source)

        with mock.patch.object(
            SYNC_MODULE,
            "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
            legacy_digest,
        ):
            expected = SYNC_MODULE._migrated_personal_agents_bytes(legacy)
            SYNC_MODULE.sync_sources(
                self.repo_root,
                self.source_root,
                (rule,),
                locked_sources=locked_sources,
            )

        self.assertEqual(agents.read_bytes(), expected)

    def test_legacy_review_source_sync_keeps_legacy_personal_agents(self) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule(
            authoritative=True,
            legacy_inventory=True,
        )
        agents, legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        source = self.source_root / rule.repo / rule.source
        locked_sources = self._locked_canonical_review_source(
            rule,
            source,
            legacy=True,
        )

        with mock.patch.object(
            SYNC_MODULE,
            "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
            legacy_digest,
        ):
            SYNC_MODULE.sync_sources(
                self.repo_root,
                self.source_root,
                (rule,),
                locked_sources=locked_sources,
            )

        self.assertEqual(agents.read_bytes(), legacy)
        self.assertFalse((target / "old-marker").exists())
        self.assertEqual((target / "SKILL.md").read_bytes(), b"public\n")

    def test_legacy_review_source_rejects_nonlegacy_agents_before_any_write(
        self,
    ) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule(
            authoritative=True,
            legacy_inventory=True,
        )
        agents, legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        source = self.source_root / rule.repo / rule.source
        locked_sources = self._locked_canonical_review_source(
            rule,
            source,
            legacy=True,
        )

        with mock.patch.object(
            SYNC_MODULE,
            "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
            legacy_digest,
        ):
            current = SYNC_MODULE._migrated_personal_agents_bytes(legacy)
            cases = {
                "current": (
                    current,
                    "exact legacy canonical review source requires exact legacy",
                ),
                "mixed": (legacy + current, "exact legacy or migrated state"),
                "compact-current": (
                    current.replace(
                        SYNC_MODULE.PERSONAL_AGENTS_CURRENT_REVIEW_BLOCK,
                        SYNC_MODULE.PERSONAL_AGENTS_CURRENT_REVIEW_BLOCK.rstrip(b"\n"),
                        1,
                    ),
                    "exact legacy or migrated state",
                ),
                "legacy-byte-drift": (
                    legacy.replace(
                        b"Synthetic legacy review detail",
                        b"Synthetic legacy review detaiL",
                        1,
                    ),
                    "exact legacy or migrated state",
                ),
            }
            for name, (payload, error_pattern) in cases.items():
                agents.write_bytes(payload)
                agents.chmod(0o644)
                with (
                    self.subTest(name=name),
                    mock.patch.object(
                        SYNC_MODULE,
                        "_sync_sources_with_repo_binding",
                    ) as sync_impl,
                    self.assertRaisesRegex(
                        SYNC_MODULE.SyncError,
                        error_pattern,
                    ),
                ):
                    SYNC_MODULE.sync_sources(
                        self.repo_root,
                        self.source_root,
                        (rule,),
                        locked_sources=locked_sources,
                    )
                sync_impl.assert_not_called()
                self.assertTrue((target / "old-marker").is_file())

    def test_candidate_review_source_rejects_unknown_agents_before_any_write(
        self,
    ) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule(
            authoritative=True
        )
        agents, legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        source = self.source_root / rule.repo / rule.source
        locked_sources = self._locked_canonical_review_source(rule, source)

        with mock.patch.object(
            SYNC_MODULE,
            "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
            legacy_digest,
        ):
            current = SYNC_MODULE._migrated_personal_agents_bytes(legacy)
            cases = {
                "mixed": legacy + current,
                "legacy-byte-drift": legacy.replace(
                    b"Synthetic legacy review detail",
                    b"Synthetic legacy review detaiL",
                    1,
                ),
            }
            for name, payload in cases.items():
                agents.write_bytes(payload)
                agents.chmod(0o644)
                with (
                    self.subTest(name=name),
                    mock.patch.object(
                        SYNC_MODULE,
                        "_sync_sources_with_repo_binding",
                    ) as sync_impl,
                    self.assertRaisesRegex(
                        SYNC_MODULE.SyncError,
                        "exact legacy or migrated state",
                    ),
                ):
                    SYNC_MODULE.sync_sources(
                        self.repo_root,
                        self.source_root,
                        (rule,),
                        locked_sources=locked_sources,
                    )
                sync_impl.assert_not_called()
                self.assertTrue((target / "old-marker").is_file())

    def test_candidate_review_source_accepts_current_agents_without_migration(
        self,
    ) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule(
            authoritative=True
        )
        agents, legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        source = self.source_root / rule.repo / rule.source
        locked_sources = self._locked_canonical_review_source(rule, source)

        with mock.patch.object(
            SYNC_MODULE,
            "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
            legacy_digest,
        ):
            current = SYNC_MODULE._migrated_personal_agents_bytes(legacy)
            agents.write_bytes(current)
            initial_inode = agents.stat().st_ino
            real_assert_installed = (
                SYNC_MODULE._assert_canonical_review_installed_migration_receipt
            )
            installed_labels: list[str] = []

            def record_installed_gate(*args, label):
                result = real_assert_installed(*args, label=label)
                installed_labels.append(label)
                return result

            with (
                mock.patch.object(
                    SYNC_MODULE,
                    "_migrate_personal_agents_after_canonical_review_sync",
                ) as migrate,
                mock.patch.object(
                    SYNC_MODULE,
                    "_bind_canonical_review_installed_migration_receipt",
                    wraps=SYNC_MODULE._bind_canonical_review_installed_migration_receipt,
                ) as bind_installed,
                mock.patch.object(
                    SYNC_MODULE,
                    "_verify_current_personal_agents_after_canonical_review_sync",
                    wraps=SYNC_MODULE._verify_current_personal_agents_after_canonical_review_sync,
                ) as verify_current,
                mock.patch.object(
                    SYNC_MODULE,
                    "_assert_canonical_review_installed_migration_receipt",
                    side_effect=record_installed_gate,
                ),
            ):
                SYNC_MODULE.sync_sources(
                    self.repo_root,
                    self.source_root,
                    (rule,),
                    locked_sources=locked_sources,
                )

        migrate.assert_not_called()
        bind_installed.assert_called_once()
        verify_current.assert_called_once()
        self.assertIn("pre-AGENTS current no-op exact target", installed_labels)
        self.assertIn("post-AGENTS current no-op exact target", installed_labels)
        self.assertEqual(agents.read_bytes(), current)
        self.assertEqual(agents.stat().st_ino, initial_inode)
        self.assertFalse((target / "old-marker").exists())
        self.assertEqual((target / "SKILL.md").read_bytes(), b"public\n")

    def test_candidate_current_noop_fails_on_post_install_checkout_drift(
        self,
    ) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule(
            authoritative=True
        )
        agents, legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        source = self.source_root / rule.repo / rule.source
        locked_sources = self._locked_canonical_review_source(rule, source)
        real_replace = SYNC_MODULE._replace_target_with_regular_file_overlays
        real_revalidate = SYNC_MODULE._revalidate_complete_checkout_verification
        installed = False

        def replace_then_mark_installed(*args, **kwargs):
            nonlocal installed
            result = real_replace(*args, **kwargs)
            installed = True
            return result

        def reject_post_install_checkout(verification):
            if installed:
                raise SYNC_MODULE.SyncError("synthetic current-noop checkout drift")
            return real_revalidate(verification)

        with mock.patch.object(
            SYNC_MODULE,
            "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
            legacy_digest,
        ):
            current = SYNC_MODULE._migrated_personal_agents_bytes(legacy)
            agents.write_bytes(current)
            initial_inode = agents.stat().st_ino
            with (
                mock.patch.object(
                    SYNC_MODULE,
                    "_replace_target_with_regular_file_overlays",
                    side_effect=replace_then_mark_installed,
                ),
                mock.patch.object(
                    SYNC_MODULE,
                    "_revalidate_complete_checkout_verification",
                    side_effect=reject_post_install_checkout,
                ),
                mock.patch.object(
                    SYNC_MODULE,
                    "_bind_canonical_review_installed_migration_receipt",
                    wraps=SYNC_MODULE._bind_canonical_review_installed_migration_receipt,
                ) as bind_installed,
                mock.patch.object(
                    SYNC_MODULE,
                    "_migrate_personal_agents_after_canonical_review_sync",
                ) as migrate,
                self.assertRaisesRegex(
                    SYNC_MODULE.SyncError,
                    "synthetic current-noop checkout drift",
                ),
            ):
                SYNC_MODULE.sync_sources(
                    self.repo_root,
                    self.source_root,
                    (rule,),
                    locked_sources=locked_sources,
                )

        self.assertTrue(installed)
        bind_installed.assert_called_once()
        migrate.assert_not_called()
        self.assertEqual(agents.read_bytes(), current)
        self.assertEqual(agents.stat().st_ino, initial_inode)
        self.assertFalse((target / "old-marker").exists())
        self.assertEqual((target / "SKILL.md").read_bytes(), b"public\n")

    def test_candidate_current_noop_fails_on_installed_receipt_gate(self) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule(
            authoritative=True
        )
        agents, legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        source = self.source_root / rule.repo / rule.source
        locked_sources = self._locked_canonical_review_source(rule, source)
        real_assert_installed = (
            SYNC_MODULE._assert_canonical_review_installed_migration_receipt
        )

        def reject_current_noop_receipt(*args, label):
            if label == "pre-AGENTS current no-op exact target":
                raise SYNC_MODULE.SyncError(
                    "synthetic current-noop installed receipt failure"
                )
            return real_assert_installed(*args, label=label)

        with mock.patch.object(
            SYNC_MODULE,
            "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
            legacy_digest,
        ):
            current = SYNC_MODULE._migrated_personal_agents_bytes(legacy)
            agents.write_bytes(current)
            initial_inode = agents.stat().st_ino
            with (
                mock.patch.object(
                    SYNC_MODULE,
                    "_assert_canonical_review_installed_migration_receipt",
                    side_effect=reject_current_noop_receipt,
                ),
                mock.patch.object(
                    SYNC_MODULE,
                    "_revalidate_complete_checkout_verification",
                    wraps=SYNC_MODULE._revalidate_complete_checkout_verification,
                ) as revalidate,
                mock.patch.object(
                    SYNC_MODULE,
                    "_migrate_personal_agents_after_canonical_review_sync",
                ) as migrate,
                self.assertRaisesRegex(
                    SYNC_MODULE.SyncError,
                    "synthetic current-noop installed receipt failure",
                ),
            ):
                SYNC_MODULE.sync_sources(
                    self.repo_root,
                    self.source_root,
                    (rule,),
                    locked_sources=locked_sources,
                )

        self.assertGreaterEqual(revalidate.call_count, 3)
        migrate.assert_not_called()
        self.assertEqual(agents.read_bytes(), current)
        self.assertEqual(agents.stat().st_ino, initial_inode)
        self.assertFalse((target / "old-marker").exists())
        self.assertEqual((target / "SKILL.md").read_bytes(), b"public\n")

    def test_personal_agents_parent_missing_is_not_created_by_preflight(self) -> None:
        repo_root = self.root / "missing-personal-parent"
        repo_root.mkdir()
        with contextlib.ExitStack() as stack:
            repo_binding = SYNC_MODULE._pin_regular_file_overlay_directory(
                stack,
                repo_root,
                label="repository root",
            )
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "cannot pin regular-file overlay personal AGENTS parent",
            ):
                SYNC_MODULE._pin_personal_agents_file(stack, repo_binding)
        self.assertFalse(
            (repo_root / SYNC_MODULE.PERSONAL_AGENTS_TARGET.parent).exists()
        )

    def test_personal_agents_file_missing_is_not_created_by_preflight(self) -> None:
        repo_root = self.root / "missing-personal-file"
        personal_root = repo_root / SYNC_MODULE.PERSONAL_AGENTS_TARGET.parent
        personal_root.mkdir(parents=True)
        with contextlib.ExitStack() as stack:
            repo_binding = SYNC_MODULE._pin_regular_file_overlay_directory(
                stack,
                repo_root,
                label="repository root",
            )
            with self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "cannot inspect regular-file overlay personal AGENTS source",
            ):
                SYNC_MODULE._pin_personal_agents_file(stack, repo_binding)
        self.assertFalse((repo_root / SYNC_MODULE.PERSONAL_AGENTS_TARGET).exists())

    def test_personal_agents_parent_rejects_wrong_owner(self) -> None:
        agents, _legacy, _legacy_digest = self._synthetic_legacy_personal_agents()
        parent_inode = agents.parent.stat().st_ino
        real_fstat = SYNC_MODULE.os.fstat

        def wrong_owner_fstat(descriptor):
            metadata = real_fstat(descriptor)
            if stat.S_ISDIR(metadata.st_mode) and metadata.st_ino == parent_inode:
                return SimpleNamespace(
                    st_dev=metadata.st_dev,
                    st_ino=metadata.st_ino,
                    st_mode=metadata.st_mode,
                    st_uid=os.getuid() + 1,
                )
            return metadata

        with contextlib.ExitStack() as stack:
            repo_binding = SYNC_MODULE._pin_regular_file_overlay_directory(
                stack,
                self.repo_root,
                label="repository root",
            )
            with (
                mock.patch.object(
                    SYNC_MODULE.os,
                    "fstat",
                    side_effect=wrong_owner_fstat,
                ),
                self.assertRaisesRegex(
                    SYNC_MODULE.SyncError,
                    "directory must be owned by the current user",
                ),
            ):
                SYNC_MODULE._pin_personal_agents_file(stack, repo_binding)

    def test_personal_agents_parent_rejects_world_writable_mode_before_write(
        self,
    ) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule(
            authoritative=True
        )
        agents, _legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        source = self.source_root / rule.repo / rule.source
        locked_sources = self._locked_canonical_review_source(rule, source)
        original_mode = stat.S_IMODE(agents.parent.stat().st_mode)
        agents.parent.chmod(0o777)
        try:
            with (
                mock.patch.object(
                    SYNC_MODULE,
                    "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
                    legacy_digest,
                ),
                mock.patch.object(
                    SYNC_MODULE,
                    "_sync_sources_with_repo_binding",
                ) as sync_impl,
                self.assertRaisesRegex(
                    SYNC_MODULE.SyncError,
                    "directory must not be group or other writable",
                ),
            ):
                SYNC_MODULE.sync_sources(
                    self.repo_root,
                    self.source_root,
                    (rule,),
                    locked_sources=locked_sources,
                )
            sync_impl.assert_not_called()
            self.assertTrue((target / "old-marker").is_file())
        finally:
            agents.parent.chmod(original_mode)

    def test_personal_agents_parent_mode_drift_after_preflight_blocks_write(
        self,
    ) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule(
            authoritative=True
        )
        agents, _legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        source = self.source_root / rule.repo / rule.source
        locked_sources = self._locked_canonical_review_source(rule, source)
        original_mode = stat.S_IMODE(agents.parent.stat().st_mode)

        def drift_parent_mode(*_args, **_kwargs):
            agents.parent.chmod(original_mode | 0o022)

        try:
            with (
                mock.patch.object(
                    SYNC_MODULE,
                    "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
                    legacy_digest,
                ),
                mock.patch.object(
                    SYNC_MODULE,
                    "_require_retired_targets_absent",
                    side_effect=drift_parent_mode,
                ),
                mock.patch.object(
                    SYNC_MODULE,
                    "_create_external_prepared_regular_file_overlay_container",
                ) as prepare,
                self.assertRaisesRegex(
                    SYNC_MODULE.SyncError,
                    "directory binding changed|access policy changed",
                ),
            ):
                SYNC_MODULE.sync_sources(
                    self.repo_root,
                    self.source_root,
                    (rule,),
                    locked_sources=locked_sources,
                )
            prepare.assert_not_called()
            self.assertTrue((target / "old-marker").is_file())
        finally:
            agents.parent.chmod(original_mode)

    def test_personal_agents_parent_replacement_after_file_read_fails(self) -> None:
        agents, legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        detached_parent = agents.parent.with_name("personal_codex.detached")
        real_assert_file = SYNC_MODULE._assert_bound_plain_file
        replaced = False

        def assert_file_then_replace(*args, **kwargs):
            nonlocal replaced
            result = real_assert_file(*args, **kwargs)
            if not replaced:
                agents.parent.rename(detached_parent)
                agents.parent.mkdir(mode=0o755)
                agents.write_bytes(legacy)
                agents.chmod(0o644)
                replaced = True
            return result

        with (
            contextlib.ExitStack() as stack,
            mock.patch.object(
                SYNC_MODULE,
                "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
                legacy_digest,
            ),
        ):
            repo_binding = SYNC_MODULE._pin_regular_file_overlay_directory(
                stack,
                self.repo_root,
                label="repository root",
            )
            pinned = SYNC_MODULE._pin_personal_agents_file(stack, repo_binding)
            with (
                mock.patch.object(
                    SYNC_MODULE,
                    "_assert_bound_plain_file",
                    side_effect=assert_file_then_replace,
                ),
                self.assertRaisesRegex(
                    SYNC_MODULE.SyncError,
                    "post-file-read personal AGENTS parent directory binding changed",
                ),
            ):
                SYNC_MODULE._assert_pinned_personal_agents_file(
                    repo_binding,
                    pinned,
                    label="replacement race",
                )

        self.assertTrue(replaced)
        self.assertEqual(
            (detached_parent / agents.name).read_bytes(),
            legacy,
        )
        self.assertEqual(agents.read_bytes(), legacy)

    def test_personal_agents_parent_mode_drift_after_file_read_fails(self) -> None:
        agents, _legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        original_mode = stat.S_IMODE(agents.parent.stat().st_mode)
        real_assert_file = SYNC_MODULE._assert_bound_plain_file
        drifted = False

        def assert_file_then_drift_mode(*args, **kwargs):
            nonlocal drifted
            result = real_assert_file(*args, **kwargs)
            if not drifted:
                agents.parent.chmod(original_mode | 0o022)
                drifted = True
            return result

        try:
            with (
                contextlib.ExitStack() as stack,
                mock.patch.object(
                    SYNC_MODULE,
                    "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
                    legacy_digest,
                ),
            ):
                repo_binding = SYNC_MODULE._pin_regular_file_overlay_directory(
                    stack,
                    self.repo_root,
                    label="repository root",
                )
                pinned = SYNC_MODULE._pin_personal_agents_file(stack, repo_binding)
                with (
                    mock.patch.object(
                        SYNC_MODULE,
                        "_assert_bound_plain_file",
                        side_effect=assert_file_then_drift_mode,
                    ),
                    self.assertRaisesRegex(
                        SYNC_MODULE.SyncError,
                        "post-file-read personal AGENTS parent directory binding changed",
                    ),
                ):
                    SYNC_MODULE._assert_pinned_personal_agents_file(
                        repo_binding,
                        pinned,
                        label="mode race",
                    )
        finally:
            agents.parent.chmod(original_mode)

        self.assertTrue(drifted)

    def _assert_legacy_noop_post_install_agents_mutation_fails(
        self,
        mutate,
        *,
        error_pattern: str,
    ) -> tuple[Path, bytes]:
        rule, target = self._create_canonical_regular_file_overlay_rule(
            authoritative=True,
            legacy_inventory=True,
        )
        agents, legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        source = self.source_root / rule.repo / rule.source
        locked_sources = self._locked_canonical_review_source(
            rule,
            source,
            legacy=True,
        )
        real_replace = SYNC_MODULE._replace_target_with_regular_file_overlays
        mutation_ran = False

        def replace_then_mutate(*args, **kwargs):
            nonlocal mutation_ran
            result = real_replace(*args, **kwargs)
            mutate(agents, legacy)
            mutation_ran = True
            return result

        with (
            mock.patch.object(
                SYNC_MODULE,
                "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
                legacy_digest,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_replace_target_with_regular_file_overlays",
                side_effect=replace_then_mutate,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_migrate_personal_agents_after_canonical_review_sync",
            ) as migrate,
            self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                error_pattern,
            ) as raised,
        ):
            SYNC_MODULE.sync_sources(
                self.repo_root,
                self.source_root,
                (rule,),
                locked_sources=locked_sources,
            )

        self.assertTrue(mutation_ran)
        migrate.assert_not_called()
        self.assertFalse((target / "old-marker").exists())
        self.assertEqual((target / "SKILL.md").read_bytes(), b"public\n")
        self.assertIn("external prepared tree retained at", str(raised.exception))
        recovery_root = self.repo_root / SYNC_MODULE.REGULAR_FILE_OVERLAY_RECOVERY_ROOT
        self.assertIn(
            b"old\n",
            [path.read_bytes() for path in recovery_root.rglob("*") if path.is_file()],
        )
        return agents, legacy

    def test_legacy_noop_rejects_post_preflight_content_change(self) -> None:
        agents, legacy = self._assert_legacy_noop_post_install_agents_mutation_fails(
            lambda target, payload: target.write_bytes(
                payload.replace(b"Synthetic", b"synthetiC", 1)
            ),
            error_pattern="bytes or access policy differ",
        )
        self.assertNotEqual(agents.read_bytes(), legacy)

    def test_legacy_noop_rejects_post_preflight_object_replacement(self) -> None:
        moved: list[Path] = []

        def replace_agents(target: Path, _payload: bytes) -> None:
            prior = target.with_name("AGENTS.preflight.md")
            target.rename(prior)
            target.write_bytes(b"concurrent replacement\n")
            target.chmod(0o644)
            moved.append(prior)

        agents, legacy = self._assert_legacy_noop_post_install_agents_mutation_fails(
            replace_agents,
            error_pattern="binding changed",
        )
        self.assertEqual(agents.read_bytes(), b"concurrent replacement\n")
        self.assertEqual(moved[0].read_bytes(), legacy)

    def test_legacy_noop_rejects_post_preflight_mode_change(self) -> None:
        agents, _legacy = self._assert_legacy_noop_post_install_agents_mutation_fails(
            lambda target, _payload: target.chmod(0o600),
            error_pattern="access policy changed",
        )
        self.assertEqual(stat.S_IMODE(agents.stat().st_mode), 0o600)

    def test_legacy_noop_rejects_post_preflight_link_policy_change(self) -> None:
        alias: list[Path] = []

        def add_alias(target: Path, _payload: bytes) -> None:
            linked = target.with_name("AGENTS.alias.md")
            os.link(target, linked)
            alias.append(linked)

        agents, legacy = self._assert_legacy_noop_post_install_agents_mutation_fails(
            add_alias,
            error_pattern="access policy changed",
        )
        self.assertEqual(agents.read_bytes(), legacy)
        self.assertEqual(alias[0].read_bytes(), legacy)

    def test_canonical_review_inventory_selector_fails_closed_on_drift(
        self,
    ) -> None:
        rule, _target = self._create_canonical_regular_file_overlay_rule(
            authoritative=True
        )
        source = self.source_root / rule.repo / rule.source
        key = (rule.repo, rule.source)
        current = self._locked_canonical_review_source(rule, source)[key]
        legacy = self._locked_canonical_review_source(
            rule,
            source,
            legacy=True,
        )[key]
        self.assertIs(
            SYNC_MODULE._select_canonical_review_inventory_profile(rule, current),
            SYNC_MODULE._CANONICAL_REVIEW_CURRENT_INVENTORY,
        )
        self.assertIs(
            SYNC_MODULE._select_canonical_review_inventory_profile(rule, legacy),
            SYNC_MODULE._CANONICAL_REVIEW_LEGACY_INVENTORY,
        )

        missing_receipt = dataclasses.replace(
            current,
            canonical_review_migration_receipt=None,
        )
        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "migration receipt does not match",
        ):
            SYNC_MODULE._select_canonical_review_inventory_profile(
                rule,
                missing_receipt,
            )

        legacy_with_receipt = dataclasses.replace(
            legacy,
            canonical_review_migration_receipt=(
                current.canonical_review_migration_receipt
            ),
        )
        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "legacy canonical review source has a migration receipt",
        ):
            SYNC_MODULE._select_canonical_review_inventory_profile(
                rule,
                legacy_with_receipt,
            )

    def test_unlocked_authoritative_review_sync_fails_before_write(self) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule(
            authoritative=True
        )
        agents, legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        prior_source = self.source_root / "prior-repo" / "prior-source"
        prior_source.mkdir(parents=True)
        (prior_source / "marker.txt").write_text("prior\n", encoding="utf-8")
        prior_rule = SYNC_MODULE.SyncRule(
            repo="prior-repo",
            source=Path("prior-source"),
            target=Path("prior-target"),
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "requires a verified source pin",
        ):
            SYNC_MODULE.sync_sources(
                self.repo_root,
                self.source_root,
                (prior_rule, rule),
            )

        self.assertEqual(agents.read_bytes(), legacy)
        self.assertFalse((self.repo_root / prior_rule.target).exists())
        self.assertTrue((target / "old-marker").is_file())

    def test_unknown_locked_review_source_fails_before_write(self) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule(
            authoritative=True
        )
        agents, legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        source = self.source_root / rule.repo / rule.source
        source_pin = SYNC_MODULE._VerifiedLockedSourcePin(
            repository=SYNC_MODULE.CANONICAL_REVIEW_MIGRATION_POLICY.repository,
            revision="d" * 40,
            root_tree="c" * 40,
        )
        locked_sources = self._locked_bug_triage_source(
            rule,
            source,
            source_pin=source_pin,
            checkout_verification=self._complete_checkout_verification(
                SimpleNamespace(
                    name=rule.repo,
                    repository=source_pin.repository,
                    sha=source_pin.revision,
                    tree=source_pin.root_tree,
                )
            ),
            root_object_id=(
                SYNC_MODULE.CANONICAL_REVIEW_MIGRATION_POLICY.approved_review_subtree_tree
            ),
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "migration receipt does not match its source",
        ):
            SYNC_MODULE.sync_sources(
                self.repo_root,
                self.source_root,
                (rule,),
                locked_sources=locked_sources,
            )

        self.assertEqual(agents.read_bytes(), legacy)
        self.assertTrue((target / "old-marker").is_file())

    def test_personal_agents_migration_accepts_timestamp_only_churn(self) -> None:
        def materialize_timestamp(agents, _legacy):
            metadata = agents.stat()
            os.utime(
                agents,
                ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000_000),
            )

        agents, _legacy, result = self._migrate_personal_agents_after_first_read(
            materialize_timestamp
        )

        self.assertIsNotNone(result)
        self.assertTrue(result.is_dir())
        self.assertEqual(
            SYNC_MODULE._personal_agents_review_guidance_state(agents.read_bytes()),
            "current",
        )

    def _assert_post_install_tree_mutation_blocks_agents_migration(
        self,
        mutate,
    ) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule(
            authoritative=True
        )
        agents, legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        source = self.source_root / rule.repo / rule.source
        locked_sources = self._locked_canonical_review_source(rule, source)
        real_replace = SYNC_MODULE._replace_target_with_regular_file_overlays
        mutation_ran = False

        def replace_then_mutate(*args, **kwargs):
            nonlocal mutation_ran
            result = real_replace(*args, **kwargs)
            mutate(target)
            mutation_ran = True
            return result

        with (
            mock.patch.object(
                SYNC_MODULE,
                "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
                legacy_digest,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_replace_target_with_regular_file_overlays",
                side_effect=replace_then_mutate,
            ),
            self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "installed.*(binding|manifest).*changed",
            ),
        ):
            SYNC_MODULE.sync_sources(
                self.repo_root,
                self.source_root,
                (rule,),
                locked_sources=locked_sources,
            )

        self.assertTrue(mutation_ran)
        self.assertEqual(agents.read_bytes(), legacy)

    def test_installed_tree_root_replacement_before_migration_keeps_agents(
        self,
    ) -> None:
        def replace_root(target):
            detached = target.with_name(f"{target.name}.detached")
            target.rename(detached)
            shutil.copytree(detached, target)

        self._assert_post_install_tree_mutation_blocks_agents_migration(replace_root)

    def test_installed_tree_content_mutation_before_migration_keeps_agents(
        self,
    ) -> None:
        self._assert_post_install_tree_mutation_blocks_agents_migration(
            lambda target: (target / "SKILL.md").write_bytes(b"mutated\n"),
        )

    def test_installed_tree_receipt_must_match_exact_candidate_manifest(
        self,
    ) -> None:
        rule, _target = self._create_canonical_regular_file_overlay_rule(
            authoritative=True
        )
        agents, legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        source = self.source_root / rule.repo / rule.source
        locked_sources = self._locked_canonical_review_source(rule, source)
        real_replace = SYNC_MODULE._replace_target_with_regular_file_overlays

        def replace_with_forged_manifest(*args, **kwargs):
            result = real_replace(*args, **kwargs)
            forged_manifest = SYNC_MODULE._RegularFileOverlayTreeManifest(
                root_identity=result.installed_receipt.manifest.root_identity,
                entries=(),
                total_bytes=0,
            )
            return dataclasses.replace(
                result,
                installed_receipt=dataclasses.replace(
                    result.installed_receipt,
                    manifest=forged_manifest,
                ),
            )

        with (
            mock.patch.object(
                SYNC_MODULE,
                "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
                legacy_digest,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_replace_target_with_regular_file_overlays",
                side_effect=replace_with_forged_manifest,
            ),
            self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "installed manifest differs from candidate",
            ),
        ):
            SYNC_MODULE.sync_sources(
                self.repo_root,
                self.source_root,
                (rule,),
                locked_sources=locked_sources,
            )

        self.assertEqual(agents.read_bytes(), legacy)

    def test_final_prepublish_tree_drift_blocks_compact_agents_rename(
        self,
    ) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule(
            authoritative=True
        )
        agents, legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        source = self.source_root / rule.repo / rule.source
        locked_sources = self._locked_canonical_review_source(rule, source)
        real_register = SYNC_MODULE._register_regular_file_overlay_retained_entry
        mutation_ran = False

        def register_then_mutate(scope, name, entry):
            nonlocal mutation_ran
            result = real_register(scope, name, entry)
            if not mutation_ran and scope.target_parent.path == agents.parent:
                skill = target / "SKILL.md"
                before = skill.stat()
                skill.write_bytes(b"same-inode drift before compact publish\n")
                after = skill.stat()
                self.assertEqual(
                    (before.st_dev, before.st_ino),
                    (after.st_dev, after.st_ino),
                )
                mutation_ran = True
            return result

        with (
            mock.patch.object(
                SYNC_MODULE,
                "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
                legacy_digest,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_register_regular_file_overlay_retained_entry",
                side_effect=register_then_mutate,
            ),
            self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "final pre-AGENTS publication.*manifest changed",
            ),
        ):
            SYNC_MODULE.sync_sources(
                self.repo_root,
                self.source_root,
                (rule,),
                locked_sources=locked_sources,
            )

        self.assertTrue(mutation_ran)
        self.assertFalse(agents.exists())
        recovery_root = self.repo_root / SYNC_MODULE.REGULAR_FILE_OVERLAY_RECOVERY_ROOT
        self.assertIn(
            legacy,
            [path.read_bytes() for path in recovery_root.rglob("*") if path.is_file()],
        )

    def test_bound_plain_file_accepts_materialization_timestamp_hint(self) -> None:
        agents, legacy, _legacy_digest = self._synthetic_legacy_personal_agents()
        real_hint = SYNC_MODULE._overlay_file_timestamp_hint
        real_read = SYNC_MODULE._read_regular_file_overlay_descriptor
        hint_calls = 0
        read_calls = 0

        def materialized_hint(metadata):
            nonlocal hint_calls
            hint_calls += 1
            mtime_ns, ctime_ns = real_hint(metadata)
            if hint_calls <= 2:
                return mtime_ns, ctime_ns
            return mtime_ns, ctime_ns + 1

        def counted_read(descriptor, *, byte_limit):
            nonlocal read_calls
            read_calls += 1
            return real_read(descriptor, byte_limit=byte_limit)

        with contextlib.ExitStack() as stack:
            parent = SYNC_MODULE._pin_regular_file_overlay_directory(
                stack,
                agents.parent,
                label="personal AGENTS parent",
            )
            pinned = SYNC_MODULE._pin_regular_file_overlay_entry(
                stack,
                parent.descriptor,
                agents.name,
                label="personal AGENTS source",
            )
            with (
                mock.patch.object(
                    SYNC_MODULE,
                    "_overlay_file_timestamp_hint",
                    side_effect=materialized_hint,
                ),
                mock.patch.object(
                    SYNC_MODULE,
                    "_read_regular_file_overlay_descriptor",
                    side_effect=counted_read,
                ),
            ):
                snapshot = SYNC_MODULE._read_bound_plain_file_semantically(
                    parent.descriptor,
                    agents.name,
                    pinned,
                    byte_limit=len(legacy),
                    label="personal AGENTS source",
                )

        self.assertEqual(snapshot.data, legacy)
        self.assertEqual(read_calls, 2)

    def test_bound_plain_file_caps_persistent_timestamp_revalidation(self) -> None:
        agents, legacy, _legacy_digest = self._synthetic_legacy_personal_agents()
        real_hint = SYNC_MODULE._overlay_file_timestamp_hint
        real_read = SYNC_MODULE._read_regular_file_overlay_descriptor
        hint_calls = 0
        read_calls = 0

        def unstable_hint(metadata):
            nonlocal hint_calls
            hint_calls += 1
            mtime_ns, ctime_ns = real_hint(metadata)
            return mtime_ns, ctime_ns + hint_calls

        def counted_read(descriptor, *, byte_limit):
            nonlocal read_calls
            read_calls += 1
            return real_read(descriptor, byte_limit=byte_limit)

        with contextlib.ExitStack() as stack:
            parent = SYNC_MODULE._pin_regular_file_overlay_directory(
                stack,
                agents.parent,
                label="personal AGENTS parent",
            )
            pinned = SYNC_MODULE._pin_regular_file_overlay_entry(
                stack,
                parent.descriptor,
                agents.name,
                label="personal AGENTS source",
            )
            with (
                mock.patch.object(
                    SYNC_MODULE,
                    "_overlay_file_timestamp_hint",
                    side_effect=unstable_hint,
                ),
                mock.patch.object(
                    SYNC_MODULE,
                    "_read_regular_file_overlay_descriptor",
                    side_effect=counted_read,
                ),
                self.assertRaisesRegex(
                    SYNC_MODULE.SyncError,
                    "timestamp revalidation did not stabilize",
                ) as raised,
            ):
                SYNC_MODULE._read_bound_plain_file_semantically(
                    parent.descriptor,
                    agents.name,
                    pinned,
                    byte_limit=len(legacy),
                    label="personal AGENTS source",
                )

        self.assertEqual(read_calls, 2)
        self.assertNotIn("content changed", str(raised.exception))

    def test_personal_agents_migration_rejects_content_drift(self) -> None:
        def change_content(agents, legacy):
            drifted = legacy.replace(b"Synthetic", b"synthetiC", 1)
            self.assertEqual(len(drifted), len(legacy))
            agents.write_bytes(drifted)
            metadata = agents.stat()
            os.utime(
                agents,
                ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000_000),
            )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "content changed during timestamp revalidation",
        ):
            self._migrate_personal_agents_after_first_read(change_content)

    def test_personal_agents_migration_rejects_object_replacement(self) -> None:
        def replace_object(agents, legacy):
            agents.replace(agents.with_name("AGENTS.replaced.md"))
            agents.write_bytes(legacy)
            agents.chmod(0o644)

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "binding changed: object identity mismatch",
        ):
            self._migrate_personal_agents_after_first_read(replace_object)

    def test_personal_agents_migration_rejects_mode_drift(self) -> None:
        def change_mode(agents, _legacy):
            agents.chmod(0o600)

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "access policy changed",
        ):
            self._migrate_personal_agents_after_first_read(change_mode)

    def test_personal_agents_migration_rejects_link_count_drift(self) -> None:
        def add_hard_link(agents, _legacy):
            os.link(agents, agents.with_name("AGENTS.alias.md"))

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "access policy changed",
        ):
            self._migrate_personal_agents_after_first_read(add_hard_link)

    def test_personal_agents_migration_rejects_ownership_drift(self) -> None:
        real_fstat = SYNC_MODULE.os.fstat
        drift_owner = False

        def mark_owner_drift(_agents, _legacy):
            nonlocal drift_owner
            drift_owner = True

        def owner_drifting_fstat(descriptor):
            metadata = real_fstat(descriptor)
            if not drift_owner or not stat.S_ISREG(metadata.st_mode):
                return metadata
            return SimpleNamespace(
                st_dev=metadata.st_dev,
                st_ino=metadata.st_ino,
                st_mode=metadata.st_mode,
                st_nlink=metadata.st_nlink,
                st_uid=metadata.st_uid + 1,
                st_size=metadata.st_size,
                st_mtime_ns=metadata.st_mtime_ns,
                st_ctime_ns=metadata.st_ctime_ns,
            )

        with (
            mock.patch.object(
                SYNC_MODULE.os,
                "fstat",
                side_effect=owner_drifting_fstat,
            ),
            self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "access policy changed",
            ),
        ):
            self._migrate_personal_agents_after_first_read(mark_owner_drift)

    def test_personal_agents_migration_classifies_missing_path(self) -> None:
        def remove_path(agents, _legacy):
            agents.unlink()

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "pathname is missing during revalidation",
        ):
            self._migrate_personal_agents_after_first_read(remove_path)

    def test_personal_agents_migration_classifies_unreadable_path(self) -> None:
        agents, _legacy, _legacy_digest = self._synthetic_legacy_personal_agents()
        parent_descriptor = os.open(agents.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with (
                mock.patch.object(
                    SYNC_MODULE.os,
                    "stat",
                    side_effect=PermissionError(
                        errno.EACCES,
                        "synthetic unreadable path",
                    ),
                ),
                self.assertRaisesRegex(
                    SYNC_MODULE.SyncError,
                    "pathname is unreadable during revalidation",
                ),
            ):
                SYNC_MODULE._stat_bound_plain_file_name(
                    parent_descriptor,
                    agents.name,
                    label="personal AGENTS source",
                )
        finally:
            os.close(parent_descriptor)

    def test_personal_agents_migration_classifies_path_revalidation_failure(
        self,
    ) -> None:
        agents, _legacy, _legacy_digest = self._synthetic_legacy_personal_agents()
        parent_descriptor = os.open(agents.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with (
                mock.patch.object(
                    SYNC_MODULE.os,
                    "stat",
                    side_effect=OSError(
                        errno.EIO,
                        "synthetic revalidation failure",
                    ),
                ),
                self.assertRaisesRegex(
                    SYNC_MODULE.SyncError,
                    "pathname revalidation failed",
                ),
            ):
                SYNC_MODULE._stat_bound_plain_file_name(
                    parent_descriptor,
                    agents.name,
                    label="personal AGENTS source",
                )
        finally:
            os.close(parent_descriptor)

    def test_current_personal_agents_migration_is_inode_stable(self) -> None:
        rule, _target = self._create_canonical_regular_file_overlay_rule(
            authoritative=True
        )
        agents, legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        source = self.source_root / rule.repo / rule.source
        locked_sources = self._locked_canonical_review_source(rule, source)

        with mock.patch.object(
            SYNC_MODULE,
            "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
            legacy_digest,
        ):
            SYNC_MODULE.sync_sources(
                self.repo_root,
                self.source_root,
                (rule,),
                locked_sources=locked_sources,
            )
            migrated = agents.read_bytes()
            migrated_inode = agents.stat().st_ino
            SYNC_MODULE.sync_sources(
                self.repo_root,
                self.source_root,
                (rule,),
                locked_sources=locked_sources,
            )

        self.assertEqual(agents.read_bytes(), migrated)
        self.assertEqual(agents.stat().st_ino, migrated_inode)

    def test_personal_agents_unknown_states_fail_closed(self) -> None:
        _agents, legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        with mock.patch.object(
            SYNC_MODULE,
            "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
            legacy_digest,
        ):
            current = SYNC_MODULE._migrated_personal_agents_bytes(legacy)
            cases = {
                "legacy-drift": legacy.replace(
                    b"Synthetic legacy review detail",
                    b"Synthetic legacy review detaiL",
                    1,
                ),
                "half-migrated": legacy.replace(
                    SYNC_MODULE.PERSONAL_AGENTS_LEGACY_CONSENT_LINE,
                    SYNC_MODULE.PERSONAL_AGENTS_CURRENT_CONSENT_LINE,
                    1,
                ),
                "legacy-consent-suffix-drift": legacy.replace(
                    SYNC_MODULE.PERSONAL_AGENTS_LEGACY_CONSENT_LINE,
                    SYNC_MODULE.PERSONAL_AGENTS_LEGACY_CONSENT_LINE.rstrip(b"\n")
                    + b" Extra authorization.\n",
                    1,
                ),
                "legacy-consent-prefix-drift": legacy.replace(
                    SYNC_MODULE.PERSONAL_AGENTS_LEGACY_CONSENT_LINE,
                    b"Extra authorization. "
                    + SYNC_MODULE.PERSONAL_AGENTS_LEGACY_CONSENT_LINE,
                    1,
                ),
                "current-consent-suffix-drift": current.replace(
                    SYNC_MODULE.PERSONAL_AGENTS_CURRENT_CONSENT_LINE,
                    SYNC_MODULE.PERSONAL_AGENTS_CURRENT_CONSENT_LINE.rstrip(b"\n")
                    + b" Extra authorization.\n",
                    1,
                ),
                "current-consent-prefix-drift": current.replace(
                    SYNC_MODULE.PERSONAL_AGENTS_CURRENT_CONSENT_LINE,
                    b"Extra authorization. "
                    + SYNC_MODULE.PERSONAL_AGENTS_CURRENT_CONSENT_LINE,
                    1,
                ),
                "legacy-consent-reordered": legacy.replace(
                    SYNC_MODULE.PERSONAL_AGENTS_LEGACY_CONSENT_LINE,
                    b"",
                    1,
                )
                + SYNC_MODULE.PERSONAL_AGENTS_LEGACY_CONSENT_LINE,
                "legacy-block-prefix-drift": legacy.replace(
                    SYNC_MODULE.PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_START,
                    b"Extra policy. "
                    + SYNC_MODULE.PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_START,
                    1,
                ),
                "current-block-prefix-drift": current.replace(
                    SYNC_MODULE.PERSONAL_AGENTS_CURRENT_REVIEW_BLOCK,
                    b"Extra policy. "
                    + SYNC_MODULE.PERSONAL_AGENTS_CURRENT_REVIEW_BLOCK,
                    1,
                ),
                "mixed": legacy + current,
                "duplicate-current": current.replace(
                    SYNC_MODULE.PERSONAL_AGENTS_REVIEW_BLOCK_BOUNDARY,
                    SYNC_MODULE.PERSONAL_AGENTS_CURRENT_REVIEW_BLOCK
                    + SYNC_MODULE.PERSONAL_AGENTS_REVIEW_BLOCK_BOUNDARY,
                    1,
                ),
            }
            for name, data in cases.items():
                with (
                    self.subTest(name=name),
                    self.assertRaisesRegex(
                        SYNC_MODULE.SyncError,
                        "exact legacy or migrated state",
                    ),
                ):
                    SYNC_MODULE._migrated_personal_agents_bytes(data)

    def test_personal_agents_drift_fails_before_new_review_tree_is_installed(
        self,
    ) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule(
            authoritative=True
        )
        agents, legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        drifted = legacy.replace(
            b"Synthetic legacy review detail",
            b"Synthetic legacy review detaiL",
            1,
        )
        agents.write_bytes(drifted)
        source = self.source_root / rule.repo / rule.source
        locked_sources = self._locked_canonical_review_source(rule, source)

        with (
            mock.patch.object(
                SYNC_MODULE,
                "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
                legacy_digest,
            ),
            self.assertRaisesRegex(
                SYNC_MODULE.SyncError,
                "exact legacy or migrated state",
            ),
        ):
            SYNC_MODULE.sync_sources(
                self.repo_root,
                self.source_root,
                (rule,),
                locked_sources=locked_sources,
            )

        self.assertEqual(agents.read_bytes(), drifted)
        self.assertTrue((target / "old-marker").is_file())
        self.assertFalse((target / "SKILL.md").exists())

    def test_personal_agents_migration_does_not_overwrite_rebound_target(
        self,
    ) -> None:
        agents, legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        moved = agents.with_name("AGENTS.old.md")
        replacement = b"concurrent replacement\n"
        real_rename = SYNC_MODULE._rename_regular_file_overlay_noreplace
        rebound = False

        def rebind_inside_publish(*args, **kwargs):
            nonlocal rebound
            if not rebound:
                agents.rename(moved)
                agents.write_bytes(replacement)
                agents.chmod(0o644)
                rebound = True
            return real_rename(*args, **kwargs)

        with contextlib.ExitStack() as stack:
            repo_binding = SYNC_MODULE._pin_regular_file_overlay_directory(
                stack,
                self.repo_root,
                label="repository root",
            )
            with (
                mock.patch.object(
                    SYNC_MODULE,
                    "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
                    legacy_digest,
                ),
                mock.patch.object(
                    SYNC_MODULE,
                    "_rename_regular_file_overlay_noreplace",
                    side_effect=rebind_inside_publish,
                ),
                self.assertRaisesRegex(
                    SYNC_MODULE.SyncError,
                    "binding changed",
                ),
            ):
                SYNC_MODULE._migrate_personal_agents_guidance(repo_binding)

        self.assertTrue(rebound)
        self.assertFalse(agents.exists())
        self.assertEqual(moved.read_bytes(), legacy)
        recovery_root = self.repo_root / SYNC_MODULE.REGULAR_FILE_OVERLAY_RECOVERY_ROOT
        retained_payloads = [
            path.read_bytes() for path in recovery_root.rglob("*") if path.is_file()
        ]
        self.assertIn(replacement, retained_payloads)

    def test_personal_agents_parent_replacement_before_retention_rename_fails(
        self,
    ) -> None:
        agents, legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        detached_parent = agents.parent.with_name("personal_codex.detached")
        replacement = b"concurrent parent replacement\n"
        final_file_label = "personal AGENTS pre-publish source"
        receipt_label = "immediate pre-AGENTS publication"
        scope_operation = "personal AGENTS prior-state retention"
        file_event = f"file:{final_file_label}"
        receipt_event = f"receipt:{receipt_label}"
        scope_event = f"scope:{scope_operation}"
        rename_event = f"rename:{scope_operation}"
        with mock.patch.object(
            SYNC_MODULE,
            "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
            legacy_digest,
        ):
            expected = SYNC_MODULE._migrated_personal_agents_bytes(legacy)
        control_events = self._trace_personal_agents_migration_order(
            agents=agents,
            legacy=legacy,
            legacy_digest=legacy_digest,
            receipt_name="personal-agents-retention-order-receipt",
            final_file_label=final_file_label,
            receipt_label=receipt_label,
            scope_operation=scope_operation,
            rename_endpoint="source",
        )
        self._assert_ordered_event_subsequence(
            control_events,
            [file_event, receipt_event, scope_event, rename_event],
        )
        events: list[str] = []
        real_assert_file = SYNC_MODULE._assert_bound_plain_file
        real_assert_installed = (
            SYNC_MODULE._assert_installed_regular_file_overlay_receipt
        )
        real_assert_scope = SYNC_MODULE._assert_regular_file_overlay_scope_binding
        real_rename = SYNC_MODULE._rename_regular_file_overlay_noreplace
        replaced = False

        def record_file(*args, **kwargs):
            result = real_assert_file(*args, **kwargs)
            if kwargs.get("label") == final_file_label:
                events.append(file_event)
            return result

        def replace_parent_after_receipt(receipt, *, label):
            nonlocal replaced
            result = real_assert_installed(receipt, label=label)
            if label == receipt_label:
                events.append(receipt_event)
            if label == receipt_label and not replaced:
                agents.parent.rename(detached_parent)
                agents.parent.mkdir(mode=0o755)
                agents.write_bytes(replacement)
                agents.chmod(0o644)
                replaced = True
            return result

        def record_scope(scope, *, operation):
            if operation == scope_operation:
                events.append(scope_event)
            return real_assert_scope(scope, operation=operation)

        def record_rename(*args, **kwargs):
            if args[2] == agents.name:
                events.append(rename_event)
            return real_rename(*args, **kwargs)

        with (
            self._valid_installed_regular_file_overlay_receipt(
                "personal-agents-retention-receipt"
            ) as installed_receipt,
            contextlib.ExitStack() as stack,
        ):
            repo_binding = SYNC_MODULE._pin_regular_file_overlay_directory(
                stack,
                self.repo_root,
                label="repository root",
            )
            with (
                mock.patch.object(
                    SYNC_MODULE,
                    "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
                    legacy_digest,
                ),
                mock.patch.object(
                    SYNC_MODULE,
                    "_assert_bound_plain_file",
                    side_effect=record_file,
                ),
                mock.patch.object(
                    SYNC_MODULE,
                    "_assert_installed_regular_file_overlay_receipt",
                    side_effect=replace_parent_after_receipt,
                ),
                mock.patch.object(
                    SYNC_MODULE,
                    "_assert_regular_file_overlay_scope_binding",
                    side_effect=record_scope,
                ),
                mock.patch.object(
                    SYNC_MODULE,
                    "_rename_regular_file_overlay_noreplace",
                    side_effect=record_rename,
                ) as rename_mock,
                self.assertRaisesRegex(
                    SYNC_MODULE.SyncError,
                    "target parent lineage changed before personal AGENTS "
                    "prior-state retention",
                ),
            ):
                SYNC_MODULE._migrate_personal_agents_guidance(
                    repo_binding,
                    installed_receipt=installed_receipt,
                )

        self.assertTrue(replaced)
        rename_mock.assert_not_called()
        self._assert_ordered_event_subsequence(
            events,
            [file_event, receipt_event, scope_event],
        )
        self.assertNotIn(rename_event, events)
        self.assertEqual((detached_parent / agents.name).read_bytes(), legacy)
        self.assertEqual(agents.read_bytes(), replacement)
        recovery_root = self.repo_root / SYNC_MODULE.REGULAR_FILE_OVERLAY_RECOVERY_ROOT
        retained_payloads = [
            path.read_bytes() for path in recovery_root.rglob("*") if path.is_file()
        ]
        self.assertIn(expected, retained_payloads)

    def test_personal_agents_parent_mode_drift_before_publish_rename_fails(
        self,
    ) -> None:
        agents, legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        final_file_label = "personal AGENTS migration candidate before publication"
        receipt_label = "final pre-AGENTS publication"
        scope_operation = "personal AGENTS current-state publication"
        file_event = f"file:{final_file_label}"
        receipt_event = f"receipt:{receipt_label}"
        scope_event = f"scope:{scope_operation}"
        rename_event = f"rename:{scope_operation}"
        with mock.patch.object(
            SYNC_MODULE,
            "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
            legacy_digest,
        ):
            expected = SYNC_MODULE._migrated_personal_agents_bytes(legacy)
        original_mode = stat.S_IMODE(agents.parent.stat().st_mode)
        control_events = self._trace_personal_agents_migration_order(
            agents=agents,
            legacy=legacy,
            legacy_digest=legacy_digest,
            receipt_name="personal-agents-publication-order-receipt",
            final_file_label=final_file_label,
            receipt_label=receipt_label,
            scope_operation=scope_operation,
            rename_endpoint="target",
        )
        self._assert_ordered_event_subsequence(
            control_events,
            [file_event, receipt_event, scope_event, rename_event],
        )
        events: list[str] = []
        real_assert_file = SYNC_MODULE._assert_bound_plain_file
        real_assert_installed = (
            SYNC_MODULE._assert_installed_regular_file_overlay_receipt
        )
        real_assert_scope = SYNC_MODULE._assert_regular_file_overlay_scope_binding
        real_rename = SYNC_MODULE._rename_regular_file_overlay_noreplace
        drifted = False

        def record_file(*args, **kwargs):
            result = real_assert_file(*args, **kwargs)
            if kwargs.get("label") == final_file_label:
                events.append(file_event)
            return result

        def drift_parent_after_receipt(receipt, *, label):
            nonlocal drifted
            result = real_assert_installed(receipt, label=label)
            if label == receipt_label:
                events.append(receipt_event)
            if label == receipt_label and not drifted:
                agents.parent.chmod(original_mode | 0o022)
                drifted = True
            return result

        def record_scope(scope, *, operation):
            if operation == scope_operation:
                events.append(scope_event)
            return real_assert_scope(scope, operation=operation)

        def record_rename(*args, **kwargs):
            if args[4] == agents.name:
                events.append(rename_event)
            return real_rename(*args, **kwargs)

        try:
            with (
                self._valid_installed_regular_file_overlay_receipt(
                    "personal-agents-publication-receipt"
                ) as installed_receipt,
                contextlib.ExitStack() as stack,
            ):
                repo_binding = SYNC_MODULE._pin_regular_file_overlay_directory(
                    stack,
                    self.repo_root,
                    label="repository root",
                )
                with (
                    mock.patch.object(
                        SYNC_MODULE,
                        "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
                        legacy_digest,
                    ),
                    mock.patch.object(
                        SYNC_MODULE,
                        "_assert_bound_plain_file",
                        side_effect=record_file,
                    ),
                    mock.patch.object(
                        SYNC_MODULE,
                        "_assert_installed_regular_file_overlay_receipt",
                        side_effect=drift_parent_after_receipt,
                    ),
                    mock.patch.object(
                        SYNC_MODULE,
                        "_assert_regular_file_overlay_scope_binding",
                        side_effect=record_scope,
                    ),
                    mock.patch.object(
                        SYNC_MODULE,
                        "_rename_regular_file_overlay_noreplace",
                        side_effect=record_rename,
                    ) as rename_mock,
                    self.assertRaisesRegex(
                        SYNC_MODULE.SyncError,
                        "target parent lineage changed before personal AGENTS "
                        "current-state publication",
                    ),
                ):
                    SYNC_MODULE._migrate_personal_agents_guidance(
                        repo_binding,
                        installed_receipt=installed_receipt,
                    )
        finally:
            agents.parent.chmod(original_mode)

        self.assertTrue(drifted)
        self.assertEqual(rename_mock.call_count, 1)
        self._assert_ordered_event_subsequence(
            events,
            [file_event, receipt_event, scope_event],
        )
        self.assertNotIn(rename_event, events)
        self.assertFalse(agents.exists())
        recovery_root = self.repo_root / SYNC_MODULE.REGULAR_FILE_OVERLAY_RECOVERY_ROOT
        retained_payloads = [
            path.read_bytes() for path in recovery_root.rglob("*") if path.is_file()
        ]
        self.assertIn(legacy, retained_payloads)
        self.assertIn(expected, retained_payloads)

    def test_current_personal_agents_noop_revalidates_after_classification(
        self,
    ) -> None:
        agents, legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        replacement = b"concurrent current-state replacement\n"
        moved = agents.with_name("AGENTS.current.md")

        with contextlib.ExitStack() as stack:
            repo_binding = SYNC_MODULE._pin_regular_file_overlay_directory(
                stack,
                self.repo_root,
                label="repository root",
            )
            with mock.patch.object(
                SYNC_MODULE,
                "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
                legacy_digest,
            ):
                SYNC_MODULE._migrate_personal_agents_guidance(repo_binding)

        real_migrate_bytes = SYNC_MODULE._migrated_personal_agents_bytes
        replaced = False

        def replace_after_classification(data):
            nonlocal replaced
            result = real_migrate_bytes(data)
            if not replaced:
                agents.rename(moved)
                agents.write_bytes(replacement)
                agents.chmod(0o644)
                replaced = True
            return result

        with contextlib.ExitStack() as stack:
            repo_binding = SYNC_MODULE._pin_regular_file_overlay_directory(
                stack,
                self.repo_root,
                label="repository root",
            )
            with (
                mock.patch.object(
                    SYNC_MODULE,
                    "_migrated_personal_agents_bytes",
                    side_effect=replace_after_classification,
                ),
                self.assertRaisesRegex(SYNC_MODULE.SyncError, "binding changed"),
            ):
                SYNC_MODULE._migrate_personal_agents_guidance(repo_binding)

        self.assertTrue(replaced)
        self.assertEqual(agents.read_bytes(), replacement)
        self.assertEqual(
            SYNC_MODULE._personal_agents_review_guidance_state(moved.read_bytes()),
            "current",
        )

    def test_personal_agents_publish_failure_is_retryable(self) -> None:
        agents, legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        real_rename = SYNC_MODULE._rename_regular_file_overlay_noreplace
        failed = False

        def fail_first_publish(*args, **kwargs):
            nonlocal failed
            if not failed:
                failed = True
                raise OSError("simulated publish failure")
            return real_rename(*args, **kwargs)

        with contextlib.ExitStack() as stack:
            repo_binding = SYNC_MODULE._pin_regular_file_overlay_directory(
                stack,
                self.repo_root,
                label="repository root",
            )
            with (
                mock.patch.object(
                    SYNC_MODULE,
                    "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
                    legacy_digest,
                ),
                mock.patch.object(
                    SYNC_MODULE,
                    "_rename_regular_file_overlay_noreplace",
                    side_effect=fail_first_publish,
                ),
            ):
                expected = SYNC_MODULE._migrated_personal_agents_bytes(legacy)
                with self.assertRaisesRegex(
                    SYNC_MODULE.SyncError,
                    "simulated publish failure",
                ):
                    SYNC_MODULE._migrate_personal_agents_guidance(repo_binding)
                self.assertEqual(agents.read_bytes(), legacy)
                self.assertTrue(
                    SYNC_MODULE._migrate_personal_agents_guidance(repo_binding)
                )

        self.assertTrue(failed)
        self.assertEqual(agents.read_bytes(), expected)

    def test_canonical_validation_failure_does_not_migrate_personal_agents(
        self,
    ) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule(
            authoritative=True
        )
        agents, legacy, legacy_digest = self._synthetic_legacy_personal_agents()
        missing = self.source_root / rule.repo / rule.source / "SKILL.md"
        missing.unlink()
        source = self.source_root / rule.repo / rule.source
        locked_sources = self._locked_canonical_review_source(rule, source)

        with (
            mock.patch.object(
                SYNC_MODULE,
                "PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256",
                legacy_digest,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_migrate_personal_agents_after_canonical_review_sync",
            ) as migrate,
            self.assertRaisesRegex(SYNC_MODULE.SyncError, "missing required file"),
        ):
            SYNC_MODULE.sync_sources(
                self.repo_root,
                self.source_root,
                (rule,),
                locked_sources=locked_sources,
            )

        migrate.assert_not_called()
        self.assertEqual(agents.read_bytes(), legacy)
        self.assertTrue((target / "old-marker").is_file())

    def test_unrelated_sync_does_not_migrate_personal_agents(self) -> None:
        agents, legacy, _legacy_digest = self._synthetic_legacy_personal_agents()
        source = self.source_root / "example-repo" / "skill" / "SKILL.md"
        source.parent.mkdir(parents=True)
        source.write_text("example\n", encoding="utf-8")
        rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/example"),
        )

        with mock.patch.object(
            SYNC_MODULE,
            "_migrate_personal_agents_after_canonical_review_sync",
        ) as migrate:
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

        migrate.assert_not_called()
        self.assertEqual(agents.read_bytes(), legacy)

    def test_sync_rejects_ignored_upstream_independent_supervisor_file(
        self,
    ) -> None:
        rule, target = self._create_canonical_regular_file_overlay_rule()
        unexpected = (
            self.source_root
            / rule.repo
            / rule.source
            / SYNC_MODULE.INDEPENDENT_CODEX_REVIEW_ROOT
            / ".github"
            / "unreviewed.yml"
        )
        unexpected.parent.mkdir()
        unexpected.write_text("unreviewed\n", encoding="utf-8")

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "raw exact tree inventory mismatch.*unexpected=.github",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

        self.assertEqual(
            (target / "old-marker").read_text(encoding="utf-8"),
            "old\n",
        )

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

    def test_change_delivery_sync_rule_builds_current_private_variant(
        self,
    ) -> None:
        source = (
            self.source_root
            / "codex-review-workflows"
            / "skills"
            / "change-delivery-workflow"
            / "SKILL.md"
        )
        source.parent.mkdir(parents=True)
        source.write_text(
            "---\n"
            "name: change-delivery-workflow\n"
            'description: "Run a local delivery gate for non-trivial repo '
            "changes: implement, build, test, update docs, form the landing "
            "commit, review its frozen exact head, then accept it. Use when "
            "wrapping up local work, probing local gate readiness, or starting "
            'a full workflow before PR readiness."\n'
            "---\n\n"
            "Ask the user before expanding scope.\n",
            encoding="utf-8",
        )
        rule = next(
            rule
            for rule in SYNC_MODULE.SYNC_RULES
            if rule.target == Path("personal_codex/skills/change-delivery-workflow")
        )

        self.assertEqual(
            rule.replacements[0],
            SYNC_MODULE.Replacement(
                "Run a local delivery gate",
                "Run Joey's local delivery gate",
                required_count=1,
            ),
        )

        SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

        target = self.repo_root / rule.target / "SKILL.md"
        self.assertEqual(
            target.read_text(encoding="utf-8"),
            "---\n"
            "name: change-delivery-workflow\n"
            "description: \"Run Joey's local delivery gate for non-trivial repo "
            "changes: implement, build, test, update docs, form the landing "
            "commit, review its frozen exact head, then accept it. Use when "
            "wrapping up local work, probing local gate readiness, or starting "
            'a full workflow before PR readiness."\n'
            "---\n\n"
            "Ask Joey before expanding scope.\n",
        )

    def test_bug_triage_sync_rule_builds_current_private_transport_variant(
        self,
    ) -> None:
        rule, _source, interface = self._write_current_bug_triage_source()
        target = self.repo_root / rule.target
        stale_reference = target / "references" / "triage-report.md"
        stale_reference.parent.mkdir(parents=True)
        stale_reference.write_text(
            "retired generic triage template\n", encoding="utf-8"
        )

        SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

        self.assertFalse(stale_reference.exists())
        self.assertEqual(
            (target / "agents/openai.yaml").read_text(encoding="utf-8"),
            interface,
        )
        skill = (target / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn(
            "description: Transport and inspect allowlisted Cisco Jenkins HTTPS",
            skill,
        )
        self.assertIn(
            "This private skill supplies one canonical artifact transport helper",
            skill,
        )
        self.assertIn(
            "[$cisco-trackers-lookup](../cisco-trackers-lookup/SKILL.md)",
            skill,
        )
        self.assertIn(
            "Its private configuration is fixed and fail-closed by this release process",
            skill,
        )
        self.assertNotIn("private host policy", skill)

        recipes = (target / "references/jenkins-artifact-recipes.md").read_text(
            encoding="utf-8"
        )
        self.assertEqual(recipes.count("engci-private-sjc.cisco.com"), 3)
        self.assertEqual(recipes.count("--auth-profile wme_jenkins_jobs_artifact"), 3)
        self.assertIn(
            "The allowed host is fixed by the private release, and job names are examples",
            recipes,
        )

        module_name = f"synced_bug_triage_probe_{id(self)}"
        probe = load_module(
            module_name,
            target / "scripts/jenkins_artifact_probe.py",
        )
        self.addCleanup(sys.modules.pop, module_name, None)
        self.assertEqual(
            probe.ALLOWED_HOSTS,
            frozenset({"engci-private-sjc.cisco.com"}),
        )
        self.assertFalse(hasattr(probe, "DEFAULT_ALLOWED_HOSTS"))
        self.assertEqual(
            probe.AUTH_PROFILES,
            {
                "jenkins_mbpm2_codex": (
                    "Jenkins_mbpM2_codex_username",
                    "Jenkins_mbpM2_codex_token",
                ),
                "jenkins_webex_teams": (
                    "Jenkins_webex_teams_username",
                    "Jenkins_webex_teams_token",
                ),
                "wme_jenkins_jobs_artifact": (
                    "wme_jenkins_jobs_artifact_user",
                    "wme_jenkins_jobs_artifact_token",
                ),
            },
        )
        probe._ensure_allowed_url("https://ENGCI-PRIVATE-SJC.CISCO.COM/job/example")
        with self.assertRaisesRegex(ValueError, "host not allowed"):
            probe._ensure_allowed_url("https://jenkins.example.com/job/example")

        generated_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(target.rglob("*"))
            if path.is_file() and path.suffix in {".md", ".py", ".yaml"}
        )
        for residual in rule.forbidden_residuals:
            with self.subTest(residual=residual):
                self.assertNotIn(residual, generated_text)

        specific_replacements = rule.replacements[
            : -len(SYNC_MODULE.COMMON_JOEY_TEXT_REPLACEMENTS)
        ]
        self.assertTrue(specific_replacements)
        self.assertTrue(
            all(
                replacement.required and replacement.required_count is not None
                for replacement in specific_replacements
            )
        )
        obsolete_anchors = {
            "tracker issue metadata or forge PR/commit metadata",
            "fetch that tracker metadata first with a tracker-specific lookup skill",
            "if parsed.hostname not in _allowed_hosts():",
        }
        self.assertTrue(
            obsolete_anchors.isdisjoint(
                replacement.old for replacement in specific_replacements
            )
        )

    def test_generated_bug_triage_builds_basic_auth_from_exact_profiles(
        self,
    ) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source()
        target = self.repo_root / rule.target
        SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))
        module_name = f"synced_bug_triage_auth_{id(self)}"
        probe = load_module(
            module_name,
            target / "scripts/jenkins_artifact_probe.py",
        )
        self.addCleanup(sys.modules.pop, module_name, None)

        allowed_url = "https://engci-private-sjc.cisco.com/job/example"
        for profile, (user_env, token_env) in probe.AUTH_PROFILES.items():
            with self.subTest(profile=profile):
                user = f"{profile}-user"
                token = f"{profile}-token"
                values = {user_env: user, token_env: token}
                with mock.patch.object(
                    probe.os,
                    "getenv",
                    side_effect=values.get,
                ) as getenv:
                    request, auth_state = probe._build_remote_request(
                        allowed_url,
                        method="GET",
                        auth_profile=profile,
                    )

                self.assertEqual(
                    getenv.call_args_list,
                    [mock.call(user_env), mock.call(token_env)],
                )
                self.assertEqual(auth_state, "present")
                self.assertEqual(request.full_url, allowed_url)
                expected = base64.b64encode(f"{user}:{token}".encode("utf-8")).decode(
                    "ascii"
                )
                self.assertEqual(
                    request.get_header("Authorization"),
                    f"Basic {expected}",
                )

    def test_generated_bug_triage_rejects_disallowed_auth_before_io(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source()
        target = self.repo_root / rule.target
        SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))
        module_name = f"synced_bug_triage_rejection_{id(self)}"
        probe = load_module(
            module_name,
            target / "scripts/jenkins_artifact_probe.py",
        )
        self.addCleanup(sys.modules.pop, module_name, None)
        args = SimpleNamespace(
            url="https://attacker.example.com/job/example",
            method="GET",
            auth_profile="wme_jenkins_jobs_artifact",
        )

        diagnostics = io.StringIO()
        with (
            contextlib.redirect_stderr(diagnostics),
            mock.patch.object(probe.os, "getenv") as getenv,
            mock.patch.object(probe.urllib.request, "Request") as constructor,
            mock.patch.object(probe, "_open_remote") as open_remote,
        ):
            result = probe.cmd_probe_url(args)

        self.assertEqual(result, 2)
        self.assertIn("host not allowed", diagnostics.getvalue())
        getenv.assert_not_called()
        constructor.assert_not_called()
        open_remote.assert_not_called()

    def test_generated_bug_triage_rejects_cross_origin_auth_redirect(
        self,
    ) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source()
        target = self.repo_root / rule.target
        SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))
        module_name = f"synced_bug_triage_redirect_{id(self)}"
        probe = load_module(
            module_name,
            target / "scripts/jenkins_artifact_probe.py",
        )
        self.addCleanup(sys.modules.pop, module_name, None)
        profile = "wme_jenkins_jobs_artifact"
        user_env, token_env = probe.AUTH_PROFILES[profile]
        with mock.patch.object(
            probe.os,
            "getenv",
            side_effect={user_env: "user", token_env: "token"}.get,
        ):
            request, _auth_state = probe._build_remote_request(
                "https://engci-private-sjc.cisco.com/job/example",
                method="GET",
                auth_profile=profile,
            )
        handler = probe.SameOriginRedirectHandler(request.full_url, 5)

        with mock.patch.object(probe.urllib.request, "Request") as constructor:
            with self.assertRaises(probe.UnsafeRedirectError):
                handler.redirect_request(
                    request,
                    io.BytesIO(),
                    302,
                    "Found",
                    {},
                    "https://attacker.example.com/steal",
                )
        constructor.assert_not_called()

    def test_generated_bug_triage_disables_environment_proxy_routing(
        self,
    ) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source()
        target = self.repo_root / rule.target
        SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))
        module_name = f"synced_bug_triage_proxy_{id(self)}"
        probe = load_module(
            module_name,
            target / "scripts/jenkins_artifact_probe.py",
        )
        self.addCleanup(sys.modules.pop, module_name, None)

        allowed_url = "https://engci-private-sjc.cisco.com/job/example"
        hostile_proxy = "http://fixture-user:fixture-token@proxy.invalid:8080"
        request = probe.urllib.request.Request(allowed_url)
        with (
            mock.patch.dict(
                probe.os.environ,
                {
                    "HTTPS_PROXY": hostile_proxy,
                    "https_proxy": hostile_proxy,
                },
                clear=True,
            ),
            mock.patch.object(probe.urllib.request, "getproxies") as getproxies,
        ):
            opener = probe._build_opener(allowed_url, 5)

        proxy_handlers = [
            handler
            for handler in opener.handlers
            if isinstance(handler, probe.urllib.request.ProxyHandler)
        ]
        getproxies.assert_not_called()
        self.assertTrue(all(not handler.proxies for handler in proxy_handlers))

        response = SimpleNamespace(
            close=mock.Mock(),
            geturl=lambda: allowed_url,
        )
        with (
            mock.patch.object(opener, "open", return_value=response) as open_call,
            mock.patch.object(probe, "_build_opener", return_value=opener),
            probe._open_remote(
                request,
                socket_timeout=3.0,
                max_redirects=5,
            ) as actual_response,
        ):
            self.assertIs(actual_response, response)

        open_call.assert_called_once_with(request, timeout=3.0)
        response.close.assert_called_once_with()
        self.assertEqual(request.full_url, allowed_url)
        self.assertNotIn(
            "proxy-authorization",
            {header.casefold() for header, _value in request.header_items()},
        )

    def test_generated_bug_triage_uses_direct_unmaskable_signals(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source()
        target = self.repo_root / rule.target
        SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))
        helper = target / "scripts/jenkins_artifact_probe.py"
        module_name = f"synced_bug_triage_signals_{id(self)}"
        probe = load_module(module_name, helper)
        self.addCleanup(sys.modules.pop, module_name, None)

        blocked = probe._blockable_signals()
        self.assertNotIn(probe.signal.SIGKILL, blocked)
        self.assertNotIn(probe.signal.SIGSTOP, blocked)
        helper_text = helper.read_text(encoding="utf-8")
        self.assertIn("blocked.discard(signal.SIGKILL)", helper_text)
        self.assertIn("blocked.discard(signal.SIGSTOP)", helper_text)
        self.assertNotIn("getattr(signal, name, None)", helper_text)

    def test_bug_triage_sync_rule_rejects_missing_current_host_anchor(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            host_condition="if parsed.hostname not in DEFAULT_ALLOWED_HOSTS:"
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbidden residual 'DEFAULT_ALLOWED_HOSTS'",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rule_rejects_duplicate_current_recipes_anchor(
        self,
    ) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            duplicate_recipes_scope=True
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            r"optional public helper.*\(2 != 1\)",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_appended_allowed_hosts_reassignment(
        self,
    ) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script=('\nALLOWED_HOSTS = frozenset({"attacker.example.com"})\n')
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            r"exactly one module-level direct assignment to ALLOWED_HOSTS \(2 != 1\)",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_appended_auth_profiles_reassignment(
        self,
    ) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script='\nAUTH_PROFILES = {"extra": ("USER", "TOKEN")}\n'
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            r"exactly one module-level direct assignment to AUTH_PROFILES \(2 != 1\)",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_appended_allowed_hosts_method_mutation(
        self,
    ) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script='\nALLOWED_HOSTS.add("attacker.example.com")\n'
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids method mutation",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_appended_auth_profiles_subscript_mutation(
        self,
    ) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script=(
                '\nAUTH_PROFILES["extra"] = ("EXTRA_USER", "EXTRA_TOKEN")\n'
            )
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids attribute or subscript mutation",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_container_alias_policy_load(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script=(
                "\npolicy_box = [AUTH_PROFILES]\n"
                'policy_box[0]["extra"] = ("EXTRA_USER", "EXTRA_TOKEN")\n'
            )
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids unexpected policy loads",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_frozenset_shadow(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            prepended_script=(
                'frozenset = lambda values: set(values) | {"attacker.example.com"}\n\n'
            )
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids shadowing frozenset",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_sorted_shadow(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            prepended_script="sorted = lambda values: values\n\n"
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids shadowing sorted",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_dotted_policy_import(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script="\nimport AUTH_PROFILES.payload\n"
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids imported policy names",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_dynamic_builtin_alias(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script="\nrunner = exec\n"
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids dynamic builtin reference exec",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_dynamic_setattr_name(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script=(
                "\npolicy_name = ''.join(('AUTH', '_PROFILES'))\n"
                "setattr(sys.modules[__name__], policy_name, {})\n"
            )
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "requires literal reflection attributes",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_allowed_url_guard_rebinding(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script=("\n_ensure_allowed_url = urllib.parse.urlparse\n")
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids rebinding _ensure_allowed_url",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_allowed_url_guard_deletion(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script="\ndel _ensure_allowed_url\n"
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids rebinding _ensure_allowed_url",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_allowed_url_code_replacement(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script=(
                "\ndef bypass(url):\n"
                "    return urllib.parse.urlparse(url)\n"
                "\n_ensure_allowed_url.__code__ = bypass.__code__\n"
            )
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids attribute or subscript mutation",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_allowed_url_guard_alias(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script=(
                "\ndef bypass(url):\n"
                "    return urllib.parse.urlparse(url)\n"
                "\nguard = _ensure_allowed_url\n"
                "guard.__code__ = bypass.__code__\n"
            )
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids unapproved _ensure_allowed_url references",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_allowed_url_setattr(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script=(
                "\ndef bypass(url):\n"
                "    return urllib.parse.urlparse(url)\n"
                "\nsetattr(_ensure_allowed_url, '__code__', bypass.__code__)\n"
            )
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids unapproved _ensure_allowed_url references",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_allowed_url_guard_decorator(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            guard_decorator="@lambda function: urllib.parse.urlparse\n"
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids _ensure_allowed_url decorators",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_non_rejecting_host_guard(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            host_rejection_statement="pass"
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "ALLOWED_HOSTS guard must directly raise",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_return_before_host_guard(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            guard_prelude="    return parsed\n"
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "guard must precede the sole direct parsed return",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_decoy_parsed_before_host_guard(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            guard_prelude=(
                "    parsed = urllib.parse.urlparse("
                '"https://engci-private-sjc.cisco.com/")\n'
            )
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids parsed/url rebinding",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_swallowed_request_guard(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            script_replacements=(
                (
                    "    _ensure_allowed_url(url)\n",
                    "    try:\n"
                    "        _ensure_allowed_url(url)\n"
                    "    except ValueError:\n"
                    "        pass\n",
                ),
            )
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "required replacement count mismatch",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_decoy_redirect_guard(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            script_replacements=(
                (
                    "            parsed = _ensure_allowed_url(target)\n",
                    "            parsed = _ensure_allowed_url(\n"
                    '                "https://engci-private-sjc.cisco.com/"\n'
                    "            )\n",
                ),
            )
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "reviewed helper payload digest mismatch",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_operator_attrgetter_reflection(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            prepended_script=(
                "import operator\n"
                "namespace = operator.attrgetter('__built' + 'ins__')(lambda: None)\n"
                "original_frozenset = namespace['froze' + 'nset']\n"
                "namespace['froze' + 'nset'] = lambda values: original_frozenset(\n"
                "    set(values) | {'attacker.example.com'}\n"
                ")\n\n"
            ),
            appended_script=("\nnamespace['froze' + 'nset'] = original_frozenset\n"),
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "reviewed helper payload digest mismatch",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_unreviewed_helper_comment(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script="\n# Unreviewed helper drift.\n"
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "reviewed helper payload digest mismatch",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_reviewed_manifest_rejects_executable_drift(self) -> None:
        helper = (
            REPO_ROOT
            / SYNC_MODULE.PRIVATE_BUG_TRIAGE_TARGET
            / "scripts"
            / "jenkins_artifact_probe.py"
        ).read_bytes()
        directory_entry = SimpleNamespace(
            relative_parts=("scripts",),
            kind="directory",
        )

        reviewed_manifest = SimpleNamespace(
            entries=(
                directory_entry,
                SimpleNamespace(
                    relative_parts=("scripts", "jenkins_artifact_probe.py"),
                    kind="file",
                    sha256=hashlib.sha256(helper).hexdigest(),
                ),
            )
        )
        SYNC_MODULE._validate_private_bug_triage_reviewed_manifest(
            reviewed_manifest,
            SYNC_MODULE.PRIVATE_BUG_TRIAGE_TARGET,
            surface="reviewed fixture",
        )

        drifts = {
            "getenvb": b'\nextra = os.getenvb(b"EXTRA_SECRET")\n',
            "system": b'\nos.system("curl https://attacker.example")\n',
            "alternate-base64": b"\nextra = base64.urlsafe_b64encode(b'x')\n",
            "header-reader": (
                b"\ndef _single_line_header(headers, name):\n    return headers\n"
            ),
        }
        for label, suffix in drifts.items():
            with self.subTest(label=label):
                drifted_manifest = SimpleNamespace(
                    entries=(
                        directory_entry,
                        SimpleNamespace(
                            relative_parts=(
                                "scripts",
                                "jenkins_artifact_probe.py",
                            ),
                            kind="file",
                            sha256=hashlib.sha256(helper + suffix).hexdigest(),
                        ),
                    )
                )
                with self.assertRaisesRegex(
                    SYNC_MODULE.SyncError,
                    "reviewed helper payload digest mismatch at drifted fixture",
                ):
                    SYNC_MODULE._validate_private_bug_triage_reviewed_manifest(
                        drifted_manifest,
                        SYNC_MODULE.PRIVATE_BUG_TRIAGE_TARGET,
                        surface="drifted fixture",
                    )

    def test_bug_triage_reviewed_manifest_rejects_extra_script_entry(self) -> None:
        manifest = SimpleNamespace(
            entries=(
                SimpleNamespace(
                    relative_parts=("scripts",),
                    kind="directory",
                ),
                SimpleNamespace(
                    relative_parts=("scripts", "jenkins_artifact_probe.py"),
                    kind="file",
                    sha256=SYNC_MODULE.PRIVATE_BUG_TRIAGE_REVIEWED_HELPER_SHA256,
                ),
                SimpleNamespace(
                    relative_parts=("scripts", "payload.py"),
                    kind="file",
                    sha256=hashlib.sha256(b"unreviewed\n").hexdigest(),
                ),
            )
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "reviewed scripts inventory differs at inventory fixture",
        ):
            SYNC_MODULE._validate_private_bug_triage_reviewed_manifest(
                manifest,
                SYNC_MODULE.PRIVATE_BUG_TRIAGE_TARGET,
                surface="inventory fixture",
            )

    def test_bug_triage_sync_rejects_unreviewed_script_entry(self) -> None:
        rule, source, _interface = self._write_current_bug_triage_source()
        (source / "scripts" / "payload.py").write_text(
            "raise RuntimeError('unexpected import surface')\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "scripts directory contains unreviewed entries: payload.py",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_locked_sync_accepts_reviewed_helper(self) -> None:
        rule, source, _interface = self._write_current_bug_triage_source()

        SYNC_MODULE.sync_sources(
            self.repo_root,
            self.source_root,
            (rule,),
            locked_sources=self._locked_bug_triage_source(rule, source),
        )

        helper = self.repo_root / rule.target / "scripts/jenkins_artifact_probe.py"
        self.assertEqual(
            hashlib.sha256(helper.read_bytes()).hexdigest(),
            SYNC_MODULE.PRIVATE_BUG_TRIAGE_REVIEWED_HELPER_SHA256,
        )

    def test_bug_triage_unlocked_sync_uses_bound_tree_install(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source()

        with mock.patch.object(
            SYNC_MODULE,
            "_replace_target",
            side_effect=AssertionError("plain install must not handle bug triage"),
        ) as plain_replace:
            SYNC_MODULE.sync_sources(
                self.repo_root,
                self.source_root,
                (rule,),
            )

        plain_replace.assert_not_called()
        helper = self.repo_root / rule.target / "scripts/jenkins_artifact_probe.py"
        self.assertEqual(
            hashlib.sha256(helper.read_bytes()).hexdigest(),
            SYNC_MODULE.PRIVATE_BUG_TRIAGE_REVIEWED_HELPER_SHA256,
        )

    def test_bug_triage_locked_sync_rejects_unreviewed_helper(self) -> None:
        rule, source, _interface = self._write_current_bug_triage_source(
            appended_script="\n# Unreviewed locked-source helper drift.\n"
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "reviewed helper payload digest mismatch",
        ):
            SYNC_MODULE.sync_sources(
                self.repo_root,
                self.source_root,
                (rule,),
                locked_sources=self._locked_bug_triage_source(rule, source),
            )

    def test_bug_triage_sync_rejects_frame_global_policy_mutation(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script=(
                "\nnamespace = sys._getframe().f_globals\n"
                "namespace['AUTH_' + 'PROFILES'] = {}\n"
            )
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids indirect builtin namespace/reflection access",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_frame_builtin_policy_mutation(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script=(
                "\nimport inspect\n"
                "namespace = inspect.currentframe().f_builtins\n"
                "namespace['froze' + 'nset'] = set\n"
            )
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids indirect builtin namespace/reflection access",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_function_builtin_policy_mutation(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            prepended_script=(
                "namespace = (lambda: None).__builtins__\n"
                "original_frozenset = namespace['froze' + 'nset']\n"
                "namespace['froze' + 'nset'] = lambda values: original_frozenset(\n"
                "    set(values) | {'attacker.example.com'}\n"
                ")\n\n"
            ),
            appended_script=("\nnamespace['froze' + 'nset'] = original_frozenset\n"),
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids indirect builtin namespace/reflection access",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_imported_sys_frame_reflection(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script=(
                "\nfrom sys import _getframe as frame\n"
                "namespace = frame().f_globals\n"
                "namespace['AUTH_' + 'PROFILES'] = {}\n"
            )
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids importing sys frame reflection",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_wildcard_import(self) -> None:
        rule, source, _interface = self._write_current_bug_triage_source(
            appended_script="\nfrom payload import *\n"
        )
        (source / "scripts" / "payload.py").write_text(
            "ALLOWED_HOSTS = frozenset({'attacker.example.com'})\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids wildcard imports",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_imported_reflection_alias(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script=(
                "\nfrom builtins import setattr as mutate\n"
                "policy_name = 'ALLOWED_' + 'HOSTS'\n"
                "mutate(sys.modules[__name__], policy_name, frozenset())\n"
            )
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids importing from builtins",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_qualified_reflection_builtin(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script=(
                "\npolicy_name = 'ALLOWED_' + 'HOSTS'\n"
                "sys.modules['builtins'].setattr(\n"
                "    sys.modules[__name__], policy_name, frozenset()\n"
                ")\n"
            )
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids qualified dynamic/reflection builtin calls",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_direct_reflection_builtin_alias(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script=(
                "\nmutate = setattr\n"
                "policy_name = 'ALLOWED_' + 'HOSTS'\n"
                "mutate(sys.modules[__name__], policy_name, frozenset())\n"
            )
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids unapproved reflection builtin reference setattr",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_getattr_builtin_acquisition(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script=(
                "\nbuiltin_module = sys.modules['builtins']\n"
                "mutate = getattr(builtin_module, 'setattr')\n"
                "policy_name = 'ALLOWED_' + 'HOSTS'\n"
                "mutate(sys.modules[__name__], policy_name, frozenset())\n"
            )
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids reflection namespace/builtin acquisition",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_builtin_namespace_subscript(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script=(
                "\nmutate = __builtins__['setattr']\n"
                "policy_name = 'ALLOWED_' + 'HOSTS'\n"
                "mutate(sys.modules[__name__], policy_name, frozenset())\n"
            )
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids builtin namespace reference __builtins__",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_reflection_attribute_alias(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script=(
                "\nmutate = sys.modules['builtins'].setattr\n"
                "policy_name = 'ALLOWED_' + 'HOSTS'\n"
                "mutate(sys.modules[__name__], policy_name, frozenset())\n"
            )
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids reflection builtin attribute loads",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_indirect_reflection_namespace(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script=(
                "\nnamespace = (lambda: None).__globals__\n"
                "mutate = namespace['__builtins__']['setattr']\n"
                "policy_name = 'ALLOWED_' + 'HOSTS'\n"
                "mutate(sys.modules[__name__], policy_name, frozenset())\n"
            )
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids indirect builtin namespace/reflection access",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_literal_reflection_namespace_chain(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script=(
                "\nregistry = getattr(sys, 'modules')\n"
                "namespace = getattr(registry[__name__], '__dict__')\n"
                "namespace['AUTH_' + 'PROFILES'] = {}\n"
            )
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids reflection namespace/builtin acquisition",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_imported_builtins_namespace(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            prepended_script=(
                "from builtins import __dict__ as builtin_ns\n"
                "builtin_ns['frozenset'] = lambda values: "
                "set(values) | {'attacker.example.com'}\n\n"
            )
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids importing from builtins",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_imported_sys_module_registry(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script=(
                "\nfrom sys import modules as registry\n"
                "namespace = registry['builtins'].__dict__\n"
                "namespace['AUTH_' + 'PROFILES'] = {}\n"
            )
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids importing the sys module registry",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_aliased_sys_module_registry(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script=(
                "\nimport sys as system_runtime\n"
                "registry = system_runtime.modules\n"
                "namespace = registry['builtins'].__dict__\n"
                "namespace['AUTH_' + 'PROFILES'] = {}\n"
            )
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids indirect builtin namespace/reflection access",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_bug_triage_sync_rejects_pattern_capture_rebinding(self) -> None:
        rule, _source, _interface = self._write_current_bug_triage_source(
            appended_script=(
                "\nmatch object():\n    case _ as AUTH_PROFILES:\n        pass\n"
            )
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "forbids pattern capture",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

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
                    "description: Maintain repository project journals and their optional local tooling.",
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
        readme = source.parent / "README.md"
        readme.write_text(
            "Project journal adoption and migration contract.\n",
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
        self.assertIn(
            "description: Maintain Joey repo project journals and their optional local tooling.",
            text,
        )
        self.assertIn("Find Joey repos recently touched by Codex sessions.", text)
        self.assertIn("Use this when converting existing Joey repos.", text)
        self.assertIn("Do not batch-install hooks across Joey repos.", text)
        self.assertNotIn("Maintain repository project journals", text)
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
        synced_readme = target.parent / "README.md"
        self.assertEqual(
            synced_readme.read_text(encoding="utf-8"),
            "Project journal adoption and migration contract.\n",
        )

    def test_project_journal_sync_rule_rejects_frontmatter_drift(self) -> None:
        source = self.source_root / "codex-project-journal" / "SKILL.md"
        source.parent.mkdir(parents=True)
        source.write_text(
            "\n".join(
                [
                    "description: Archive repository project journals.",
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
        references = source.parent / "references"
        references.mkdir()
        (references / "wording-history.md").write_text(
            "Previous frontmatter: description: Maintain repository project journals.\n",
            encoding="utf-8",
        )
        rule = next(
            rule
            for rule in SYNC_MODULE.SYNC_RULES
            if rule.target == Path("personal_codex/skills/project-journal")
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "description: Maintain repository project journals",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

    def test_project_journal_sync_rule_rejects_duplicate_frontmatter_anchor(
        self,
    ) -> None:
        source = self.source_root / "codex-project-journal" / "SKILL.md"
        source.parent.mkdir(parents=True)
        source.write_text(
            "\n".join(
                [
                    "description: Maintain repository project journals.",
                    "description: Maintain repository project journals again.",
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

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "required replacement count mismatch",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

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

    def test_replacement_excluded_paths_reject_unsafe_and_ambiguous_rules(
        self,
    ) -> None:
        replacement = SYNC_MODULE.Replacement(
            "public",
            "private",
            required=False,
        )
        path_scoped_replacement = SYNC_MODULE.Replacement(
            "public",
            "private",
            path=Path("private.yml"),
        )
        cases = (
            ("require replacements", (), (Path("private.yml"),)),
            ("unsafe", (replacement,), (Path("."),)),
            (
                "duplicate",
                (replacement,),
                (Path("private.yml"), Path("private.yml")),
            ),
            (
                "conflicts with path-scoped",
                (path_scoped_replacement,),
                (Path("private.yml"),),
            ),
        )

        for error, replacements, excluded_paths in cases:
            with self.subTest(error=error):
                rule = SYNC_MODULE.SyncRule(
                    repo="example-repo",
                    source=Path("skill"),
                    target=Path("personal_codex/skills/example"),
                    replacements=replacements,
                    replacement_excluded_paths=excluded_paths,
                )
                with self.assertRaisesRegex(SYNC_MODULE.SyncError, error):
                    SYNC_MODULE.sync_sources(
                        self.repo_root,
                        self.source_root,
                        (rule,),
                    )

    def test_replacement_excluded_path_must_name_a_text_candidate(self) -> None:
        source = self.source_root / "example-repo" / "skill"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("public\n", encoding="utf-8")
        rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/example"),
            replacements=(
                SYNC_MODULE.Replacement("public", "private", required=False),
            ),
            replacement_excluded_paths=(Path("missing.yml"),),
        )

        with self.assertRaisesRegex(
            SYNC_MODULE.SyncError,
            "missing or not a text candidate",
        ):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

        self.assertFalse((self.repo_root / rule.target).exists())

    def test_replacement_exclusion_does_not_bypass_forbidden_residuals(
        self,
    ) -> None:
        source = self.source_root / "example-repo" / "skill"
        source.mkdir(parents=True)
        (source / "private.yml").write_text("public-token\n", encoding="utf-8")
        rule = SYNC_MODULE.SyncRule(
            repo="example-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/example"),
            replacements=(
                SYNC_MODULE.Replacement("public", "private", required=False),
            ),
            forbidden_residuals=("public-token",),
            replacement_excluded_paths=(Path("private.yml"),),
        )

        with self.assertRaisesRegex(SYNC_MODULE.SyncError, "forbidden residual"):
            SYNC_MODULE.sync_sources(self.repo_root, self.source_root, (rule,))

        self.assertFalse((self.repo_root / rule.target).exists())

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

    def _assert_plain_source_copy_race_fails_closed(
        self,
        *,
        directory_rule: bool,
    ) -> None:
        repository = "plain-directory-race" if directory_rule else "plain-file-race"
        source_repository = self.source_root / repository
        source_repository.mkdir(parents=True)
        if directory_rule:
            source = source_repository / "skill"
            source.mkdir()
            payload = source / "payload.txt"
            target_relative = Path("synced/plain-directory-race")
            target = self.repo_root / target_relative
            target.mkdir(parents=True)
            (target / "old-marker.txt").write_bytes(b"old target\n")
        else:
            source = source_repository / "payload.txt"
            payload = source
            target_relative = Path("synced/plain-file-race.txt")
            target = self.repo_root / target_relative
            target.parent.mkdir(parents=True)
            target.write_bytes(b"old target\n")
        trusted = b"trusted source bytes\n"
        injected = b"injected source bytes\n"
        payload.write_bytes(trusted)
        payload.chmod(0o644)
        rule = SYNC_MODULE.SyncRule(
            repo=repository,
            source=source.relative_to(source_repository),
            target=target_relative,
        )
        locked_blob_id = "b" * 40
        locked_manifest = SimpleNamespace(
            root_kind="tree" if directory_rule else "file",
            root_mode=0o040000 if directory_rule else 0o644,
            root_object_id="a" * 40 if directory_rule else locked_blob_id,
            entries=(
                SimpleNamespace(
                    relative=Path("payload.txt"),
                    kind="file",
                    mode=0o644,
                    object_id=locked_blob_id,
                ),
            )
            if directory_rule
            else (),
        )
        source_lock = SimpleNamespace(
            pins=(
                SimpleNamespace(
                    name=repository,
                    repository=f"Joey-Tools/{repository}",
                    sha="c" * 40,
                    tree="d" * 40,
                ),
            ),
            digest="e" * 64,
        )
        source_lock_error = type("SourceLockError", (RuntimeError,), {})
        verification_count = 0

        def verify_locked_source(_root, _source_lock, **_kwargs):
            nonlocal verification_count
            verification_count += 1
            self.assertEqual(payload.read_bytes(), trusted)
            return _synthetic_complete_checkout_receipt(
                _root,
                _source_lock.pins,
            )

        def load_locked_source_manifest(
            checkout,
            commit,
            locked_path,
            *,
            exclude_names,
            exclude_suffixes,
        ):
            self.assertEqual(checkout, source_repository)
            self.assertEqual(commit, "c" * 40)
            self.assertEqual(locked_path, rule.source)
            self.assertIsInstance(exclude_names, tuple)
            self.assertIsInstance(exclude_suffixes, tuple)
            return locked_manifest

        def read_locked_source_blob(checkout, object_id):
            self.assertEqual(checkout, source_repository)
            self.assertEqual(object_id, locked_blob_id)
            return trusted

        source_lock_module = SimpleNamespace(
            SourceLockError=source_lock_error,
            load_source_lock=mock.Mock(return_value=source_lock),
            load_locked_source_manifest=mock.Mock(
                side_effect=load_locked_source_manifest,
            ),
            read_locked_source_blob=mock.Mock(
                side_effect=read_locked_source_blob,
            ),
            validate_base_release_binding=mock.Mock(),
            validate_generated_provenance=mock.Mock(),
            verify_checkouts=mock.Mock(side_effect=verify_locked_source),
        )
        real_sync_sources = SYNC_MODULE.sync_sources
        copy_triggered = False

        def sync_only_test_rule(repo_root, source_root, *, locked_sources):
            return real_sync_sources(
                repo_root,
                source_root,
                (rule,),
                locked_sources=locked_sources,
            )

        if directory_rule:
            real_copy = SYNC_MODULE.shutil.copytree

            def copy_with_transient_source(source_path, destination, *args, **kwargs):
                nonlocal copy_triggered
                if Path(source_path) == source:
                    self.assertFalse(copy_triggered)
                    copy_triggered = True
                    payload.write_bytes(injected)
                    try:
                        return real_copy(source_path, destination, *args, **kwargs)
                    finally:
                        payload.write_bytes(trusted)

        else:
            real_copy = SYNC_MODULE.shutil.copy2

            def copy_with_transient_source(source_path, destination, *args, **kwargs):
                nonlocal copy_triggered
                if Path(source_path) == source:
                    self.assertFalse(copy_triggered)
                    copy_triggered = True
                    payload.write_bytes(injected)
                    try:
                        return real_copy(source_path, destination, *args, **kwargs)
                    finally:
                        payload.write_bytes(trusted)
                return real_copy(source_path, destination, *args, **kwargs)

        copy_name = "copytree" if directory_rule else "copy2"
        errors = io.StringIO()
        with (
            mock.patch.object(
                SYNC_MODULE,
                "_load_source_lock_module",
                return_value=source_lock_module,
            ),
            mock.patch.object(SYNC_MODULE, "SYNC_RULES", (rule,)),
            mock.patch.object(
                SYNC_MODULE,
                "sync_sources",
                side_effect=sync_only_test_rule,
            ),
            mock.patch.object(
                SYNC_MODULE.shutil,
                copy_name,
                side_effect=copy_with_transient_source,
            ),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(errors),
        ):
            result = SYNC_MODULE.main(
                [
                    "--repo-root",
                    str(self.repo_root),
                    "--source-root",
                    str(self.source_root),
                ]
            )

        self.assertFalse(copy_triggered)
        self.assertEqual(result, 0, errors.getvalue())
        self.assertEqual(payload.read_bytes(), trusted)
        self.assertEqual(verification_count, 4)
        if directory_rule:
            self.assertEqual(
                sorted(path.name for path in target.iterdir()),
                ["payload.txt"],
            )
            self.assertEqual(
                (target / "payload.txt").read_bytes(),
                trusted,
            )
        else:
            self.assertEqual(target.read_bytes(), trusted)
        for installed in target.rglob("*") if target.is_dir() else (target,):
            if installed.is_file():
                self.assertNotIn(injected, installed.read_bytes())

    def test_plain_file_sync_ignores_transient_legacy_copy_injection(
        self,
    ) -> None:
        self._assert_plain_source_copy_race_fails_closed(directory_rule=False)

    def test_plain_directory_sync_ignores_transient_legacy_copy_injection(
        self,
    ) -> None:
        self._assert_plain_source_copy_race_fails_closed(directory_rule=True)

    def _assert_prewrite_checkout_mutation_blocks_sync(
        self,
        mutation_kind: str,
    ) -> None:
        checkout = self.source_root / "prewrite-verification-repo"
        checkout.mkdir(parents=True)
        self._fixture_git(checkout, "init", "--quiet")
        source = checkout / "skill"
        source.mkdir()
        (source / "payload.txt").write_text("trusted\n", encoding="utf-8")
        self._fixture_git(checkout, "add", ".")
        self._fixture_git(checkout, "commit", "--quiet", "-m", "trusted")
        self._fixture_git(checkout, "switch", "--quiet", "--detach")
        git_path = SOURCE_LOCK_MODULE._trusted_git_path()
        pin = SOURCE_LOCK_MODULE._verify_checkout(
            git_path,
            checkout,
            name=checkout.name,
            repository="Joey-Tools/prewrite-verification-repo",
            expected=None,
        )
        source_lock = SimpleNamespace(pins=(pin,), digest="a" * 64)
        rule = SYNC_MODULE.SyncRule(
            repo=pin.name,
            source=Path("skill"),
            target=Path("personal_codex/skills/prewrite-verification"),
        )
        target = self.repo_root / rule.target
        target.mkdir(parents=True)
        sentinel = target / "sentinel.txt"
        sentinel.write_bytes(b"old target\n")
        mutation_ran = False

        replacement = self.root / "prewrite-verification-replacement"
        if mutation_kind == "checkout-replacement":
            replacement.mkdir()
            self._fixture_git(replacement, "init", "--quiet")
            replacement_source = replacement / "skill"
            replacement_source.mkdir()
            (replacement_source / "payload.txt").write_text(
                "replacement\n",
                encoding="utf-8",
            )
            self._fixture_git(replacement, "add", ".")
            self._fixture_git(
                replacement,
                "commit",
                "--quiet",
                "-m",
                "replacement",
            )
            self._fixture_git(replacement, "switch", "--quiet", "--detach")

        def verify_checkouts(_source_root, _source_lock, **_kwargs):
            self.assertIs(_source_lock, source_lock)
            return SOURCE_LOCK_MODULE.verify_checkouts(
                _source_root,
                _source_lock,
            )

        def load_manifest(*args, **kwargs):
            nonlocal mutation_ran
            manifest = SOURCE_LOCK_MODULE.load_locked_source_manifest(
                *args,
                **kwargs,
            )
            self.assertFalse(mutation_ran)
            if mutation_kind == "object-deletion":
                object_path = checkout / ".git" / "objects" / pin.sha[:2] / pin.sha[2:]
                self.assertTrue(object_path.is_file())
                object_path.unlink()
            elif mutation_kind == "checkout-replacement":
                retained = checkout.with_name(f"{checkout.name}.retained")
                checkout.rename(retained)
                replacement.rename(checkout)
            elif mutation_kind == "config-mutation":
                config = checkout / ".git" / "config"
                before = config.stat()
                config.write_bytes(
                    config.read_bytes() + b"\n[test-receipt]\n\tvalue = stable\n"
                )
                after = config.stat()
                self.assertEqual(
                    (before.st_dev, before.st_ino), (after.st_dev, after.st_ino)
                )
            mutation_ran = True
            return manifest

        def load_live_source_lock(_repo_root):
            if mutation_ran and mutation_kind == "source-lock-mutation":
                return SimpleNamespace(
                    pins=source_lock.pins,
                    digest="b" * 64,
                )
            return source_lock

        source_lock_module = SimpleNamespace(
            SourceLockError=SOURCE_LOCK_MODULE.SourceLockError,
            load_source_lock=mock.Mock(side_effect=load_live_source_lock),
            validate_base_release_binding=mock.Mock(),
            validate_generated_provenance=mock.Mock(),
            verify_checkouts=mock.Mock(side_effect=verify_checkouts),
            load_locked_source_manifest=mock.Mock(side_effect=load_manifest),
            read_locked_source_blob=SOURCE_LOCK_MODULE.read_locked_source_blob,
        )
        errors = io.StringIO()
        with (
            mock.patch.object(
                SYNC_MODULE,
                "_load_source_lock_module",
                return_value=source_lock_module,
            ),
            mock.patch.object(SYNC_MODULE, "SYNC_RULES", (rule,)),
            mock.patch.object(SYNC_MODULE, "sync_sources") as sync_sources,
            contextlib.redirect_stderr(errors),
        ):
            result = SYNC_MODULE.main(
                [
                    "--repo-root",
                    str(self.repo_root),
                    "--source-root",
                    str(self.source_root),
                ]
            )

        self.assertTrue(mutation_ran)
        self.assertEqual(result, 1, errors.getvalue())
        self.assertEqual(
            source_lock_module.verify_checkouts.call_count,
            1 if mutation_kind == "source-lock-mutation" else 2,
        )
        sync_sources.assert_not_called()
        self.assertEqual(sentinel.read_bytes(), b"old target\n")

    def test_prewrite_verification_rejects_deleted_object_before_sync(self) -> None:
        self._assert_prewrite_checkout_mutation_blocks_sync("object-deletion")

    def test_prewrite_verification_rejects_replaced_checkout_before_sync(
        self,
    ) -> None:
        self._assert_prewrite_checkout_mutation_blocks_sync("checkout-replacement")

    def test_prewrite_verification_rejects_same_inode_config_mutation_before_sync(
        self,
    ) -> None:
        self._assert_prewrite_checkout_mutation_blocks_sync("config-mutation")

    def test_prewrite_verification_rejects_source_lock_mutation_before_sync(
        self,
    ) -> None:
        self._assert_prewrite_checkout_mutation_blocks_sync("source-lock-mutation")

    def test_sync_main_reports_repo_recovery_and_external_retention(self) -> None:
        repo_recovery = self.repo_root / ".codex-tmp/private-overlay-recovery/run"
        external_retained = self.external_prepared_parent / ".skill.prepared.example"
        output = io.StringIO()
        events: list[str] = []
        source_names = tuple(
            dict.fromkeys(rule.repo for rule in SYNC_MODULE.SYNC_RULES)
        )
        policy = SYNC_MODULE.CANONICAL_REVIEW_MIGRATION_POLICY
        source_lock = SimpleNamespace(
            pins=tuple(
                SimpleNamespace(
                    name=name,
                    repository=(
                        policy.repository
                        if name == "codex-review-workflows"
                        else f"Joey-Tools/{name}"
                    ),
                    sha=f"{index + 1:040x}",
                    tree=(
                        policy.approved_root_tree
                        if name == "codex-review-workflows"
                        else f"{index + 101:040x}"
                    ),
                )
                for index, name in enumerate(source_names)
            ),
            digest="f" * 64,
        )
        source_lock_error = type("SourceLockError", (RuntimeError,), {})

        def record_verification(source_root, locked_source, **_kwargs):
            events.append("verify")
            return _synthetic_complete_checkout_receipt(
                source_root,
                locked_source.pins,
            )

        def record_manifest(*_args, **_kwargs):
            events.append("manifest")
            return object()

        source_lock_module = SimpleNamespace(
            SourceLockError=source_lock_error,
            load_source_lock=mock.Mock(return_value=source_lock),
            validate_base_release_binding=mock.Mock(),
            validate_generated_provenance=mock.Mock(),
            verify_checkouts=mock.Mock(side_effect=record_verification),
            load_locked_source_manifest=mock.Mock(side_effect=record_manifest),
            read_locked_source_blob=mock.Mock(),
        )
        migration_pin = next(
            pin for pin in source_lock.pins if pin.name == "codex-review-workflows"
        )
        migration_source_pin = SYNC_MODULE._VerifiedLockedSourcePin(
            repository=policy.repository,
            revision=migration_pin.sha,
            root_tree=policy.approved_root_tree,
        )
        migration_receipt = SYNC_MODULE._CanonicalReviewMigrationReceipt(
            policy=policy,
            source_pin=migration_source_pin,
            live_review_subtree_tree=policy.approved_review_subtree_tree,
            activation_basis="exact-approved-root-tree",
        )

        def record_receipt(*_args, **_kwargs):
            events.append("receipt")
            return migration_source_pin, migration_receipt

        def record_sync(*_args, **_kwargs):
            events.append("sync")
            return repo_recovery, external_retained

        with (
            mock.patch.object(
                SYNC_MODULE,
                "_load_source_lock_module",
                return_value=source_lock_module,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "sync_sources",
                side_effect=record_sync,
            ),
            mock.patch.object(
                SYNC_MODULE,
                "_bind_canonical_review_migration_source",
                side_effect=record_receipt,
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
        self.assertEqual(source_lock_module.load_source_lock.call_count, 7)
        self.assertTrue(
            all(
                call.args == (self.repo_root,)
                for call in source_lock_module.load_source_lock.call_args_list
            )
        )
        source_lock_module.validate_base_release_binding.assert_called_once_with(
            self.repo_root,
            source_lock,
        )
        self.assertEqual(source_lock_module.verify_checkouts.call_count, 3)
        verification_events = [
            index for index, event in enumerate(events) if event == "verify"
        ]
        self.assertEqual(len(verification_events), 3)
        manifest_or_receipt_events = [
            index
            for index, event in enumerate(events)
            if event in {"manifest", "receipt"}
        ]
        self.assertLess(verification_events[0], min(manifest_or_receipt_events))
        self.assertGreater(
            verification_events[1],
            max(manifest_or_receipt_events),
        )
        self.assertLess(verification_events[1], events.index("sync"))
        self.assertLess(events.index("sync"), verification_events[2])
        self.assertEqual(
            source_lock_module.validate_generated_provenance.call_args_list,
            [
                mock.call(
                    self.repo_root,
                    source_lock,
                    toolbox_checkout=self.source_root / "codex-toolbox",
                    require_private_receipt=False,
                ),
                mock.call(
                    self.repo_root,
                    source_lock,
                    toolbox_checkout=self.source_root / "codex-toolbox",
                ),
            ],
        )

    def test_sync_main_preserves_lexical_source_root_for_preflight(self) -> None:
        source_link = self.root / "source-link"
        source_link.symlink_to(self.source_root, target_is_directory=True)
        source_lock = SimpleNamespace(
            pins=(
                SimpleNamespace(
                    name="codex-toolbox",
                    repository="Joey-Tools/codex-toolbox",
                    sha="a" * 40,
                    tree="b" * 40,
                ),
            ),
            digest="c" * 64,
        )
        source_lock_error = type("SourceLockError", (RuntimeError,), {})

        def reject_symlink(path, _source_lock, **_kwargs):
            self.assertEqual(path, source_link)
            self.assertTrue(path.is_symlink())
            raise source_lock_error("source root must be a non-symlink directory")

        source_lock_module = SimpleNamespace(
            SourceLockError=source_lock_error,
            load_source_lock=mock.Mock(return_value=source_lock),
            validate_base_release_binding=mock.Mock(),
            validate_generated_provenance=mock.Mock(),
            verify_checkouts=mock.Mock(side_effect=reject_symlink),
        )

        with (
            mock.patch.object(
                SYNC_MODULE,
                "_load_source_lock_module",
                return_value=source_lock_module,
            ),
            mock.patch.object(SYNC_MODULE, "sync_sources") as sync_sources,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            result = SYNC_MODULE.main(
                [
                    "--repo-root",
                    str(self.repo_root),
                    "--source-root",
                    str(source_link),
                ]
            )

        self.assertEqual(result, 1)
        sync_sources.assert_not_called()

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

    def test_plain_staging_normalizes_git_style_public_modes(self) -> None:
        source = self.root / "mode-source"
        source.mkdir(mode=0o700)
        regular = source / "regular.txt"
        executable = source / "run-tool"
        nested = source / "nested"
        nested.mkdir(mode=0o700)
        regular.write_text("regular\n", encoding="utf-8")
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        regular.chmod(0o600)
        executable.chmod(0o700)
        staging = self.root / "mode-staging"

        SYNC_MODULE._copy_source_to_staging(source, staging)

        self.assertEqual(stat.S_IMODE(staging.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((staging / "nested").stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((staging / "regular.txt").stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE((staging / "run-tool").stat().st_mode), 0o755)

    def test_secure_replacements_enforce_scoped_exact_counts(self) -> None:
        cases = (
            (
                "cross-file-bait",
                "description: Archive repository project journals.\n",
                "Previous frontmatter: description: Maintain repository project journals.\n",
            ),
            (
                "duplicate-anchor",
                "description: Maintain repository project journals.\n"
                "description: Maintain repository project journals again.\n",
                "",
            ),
        )
        for name, skill_text, reference_text in cases:
            with self.subTest(name=name):
                repo = f"secure-{name}-repo"
                source = self.source_root / repo / "skill"
                source.mkdir(parents=True)
                (source / "SKILL.md").write_text(skill_text, encoding="utf-8")
                (source / "reference.md").write_text(
                    reference_text,
                    encoding="utf-8",
                )
                (source / "catalog.json").write_bytes(b"public\n")
                private = self.repo_root / "private" / f"{name}.json"
                private.parent.mkdir(exist_ok=True)
                private.write_bytes(b"private\n")
                target = Path("personal_codex/skills") / name
                rule = SYNC_MODULE.SyncRule(
                    repo=repo,
                    source=Path("skill"),
                    target=target,
                    replacements=(
                        SYNC_MODULE.Replacement(
                            "description: Maintain repository project journals",
                            "description: Maintain Joey repo project journals",
                            path=Path("SKILL.md"),
                            required_count=1,
                        ),
                    ),
                    regular_file_overlays=(
                        SYNC_MODULE.RegularFileOverlay(
                            source=Path("private") / f"{name}.json",
                            target=Path("catalog.json"),
                        ),
                    ),
                )

                with self.assertRaisesRegex(
                    SYNC_MODULE.SyncError,
                    "required replacement count mismatch",
                ):
                    SYNC_MODULE.sync_sources(
                        self.repo_root,
                        self.source_root,
                        (rule,),
                    )

                self.assertFalse((self.repo_root / target).exists())

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
        observed_profiles = []
        prepared_secure: Path | None = None
        real_public_copy = (
            SYNC_MODULE._copy_regular_file_overlay_public_source_to_prepared
        )
        real_cleanup = SYNC_MODULE._remove_retired_targets
        real_load = SYNC_MODULE._load_regular_file_overlay_data
        real_validate = SYNC_MODULE._validate_no_retired_review_references

        def record_public_prepare(
            source,
            staging,
            *,
            prepared_root,
            rule,
            locked_source=None,
            inventory_profile=SYNC_MODULE._CANONICAL_REVIEW_CURRENT_INVENTORY,
        ):
            nonlocal prepared_secure
            observed_profiles.append(inventory_profile)
            result = real_public_copy(
                source,
                staging,
                prepared_root=prepared_root,
                rule=rule,
                locked_source=locked_source,
                inventory_profile=inventory_profile,
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
            observed_profiles,
            [SYNC_MODULE._CANONICAL_REVIEW_CURRENT_INVENTORY],
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
        observed_profiles = []
        real_validate = (
            SYNC_MODULE._validate_regular_file_overlay_required_manifest_paths
        )
        real_pin = SYNC_MODULE._pin_regular_file_overlay_targets
        real_manifest_assert = SYNC_MODULE._assert_regular_file_overlay_tree_manifest
        real_rename = SYNC_MODULE._rename_regular_file_overlay_noreplace

        def record_validation(
            manifest,
            policy_target,
            *,
            inventory_profile=SYNC_MODULE._CANONICAL_REVIEW_CURRENT_INVENTORY,
            surface,
        ):
            observed_profiles.append(inventory_profile)
            events.append(
                "staging-validation"
                if surface == "staged target"
                else "external-validation"
            )
            return real_validate(
                manifest,
                policy_target,
                inventory_profile=inventory_profile,
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
            observed_profiles,
            [
                SYNC_MODULE._CANONICAL_REVIEW_CURRENT_INVENTORY,
                SYNC_MODULE._CANONICAL_REVIEW_CURRENT_INVENTORY,
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

        def fail_staging_validation(
            manifest,
            policy_target,
            *,
            inventory_profile=SYNC_MODULE._CANONICAL_REVIEW_CURRENT_INVENTORY,
            surface,
        ):
            nonlocal validations
            validations += 1
            real_validate(
                manifest,
                policy_target,
                inventory_profile=inventory_profile,
                surface=surface,
            )
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

        def validate_with_decoy(
            data,
            relative,
            policy_target,
            *,
            inventory_profile=SYNC_MODULE._CANONICAL_REVIEW_CURRENT_INVENTORY,
            surface,
        ):
            nonlocal swapped_during_validation
            if surface == "staged target" and relative == Path("README.md"):
                recovery_root = (
                    self.repo_root / SYNC_MODULE.REGULAR_FILE_OVERLAY_RECOVERY_ROOT
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
                        inventory_profile=inventory_profile,
                        surface=surface,
                    )
                finally:
                    shutil.rmtree(candidate)
                    saved.rename(candidate)
            return real_validate(
                data,
                relative,
                policy_target,
                inventory_profile=inventory_profile,
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

        def validate_with_decoy(
            data,
            relative,
            policy_target,
            *,
            inventory_profile=SYNC_MODULE._CANONICAL_REVIEW_CURRENT_INVENTORY,
            surface,
        ):
            nonlocal swapped_during_validation
            if surface == "prepared public source" and relative == Path("SKILL.md"):
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
                        inventory_profile=inventory_profile,
                        surface=surface,
                    )
                finally:
                    shutil.rmtree(source)
                    saved.rename(source)
            return real_validate(
                data,
                relative,
                policy_target,
                inventory_profile=inventory_profile,
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
            (set(SYNC_MODULE.os.supports_follow_symlinks) - {real_stat}) | {stat_mock}
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
            (set(SYNC_MODULE.os.supports_follow_symlinks) - {real_stat}) | {stat_mock}
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

        source = self.source_root / "descriptor-depth-repo" / "skill"
        source.mkdir(parents=True)
        (source / "catalog.json").write_bytes(b"public\n")
        private_catalog = self.repo_root / "private/catalog.json"
        private_catalog.parent.mkdir()
        private_catalog.write_bytes(b"private\n")
        rule = SYNC_MODULE.SyncRule(
            repo="descriptor-depth-repo",
            source=Path("skill"),
            target=Path("personal_codex/skills/descriptor-depth"),
            regular_file_overlays=(
                SYNC_MODULE.RegularFileOverlay(
                    source=Path("private/catalog.json"),
                    target=Path("catalog.json"),
                ),
            ),
        )
        target = self.repo_root / rule.target
        target.mkdir(parents=True)
        (target / "old-marker").write_bytes(b"old\n")
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

        def inject_unproven_entry(
            source,
            prepared,
            *,
            prepared_root,
            rule,
            locked_source=None,
            inventory_profile=SYNC_MODULE._CANONICAL_REVIEW_CURRENT_INVENTORY,
        ):
            nonlocal retained_container, marker
            self.assertIsNone(locked_source)
            self.assertIs(
                inventory_profile,
                SYNC_MODULE._CANONICAL_REVIEW_CURRENT_INVENTORY,
            )
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
                    "injected public-copy failure.*external prepared tree retained at",
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
            f"regular-file overlay recovery scope may be retained at {retained[0]}",
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

    def test_review_sync_rule_keeps_personalization_and_private_catalog(
        self,
    ) -> None:
        rule = next(
            rule
            for rule in SYNC_MODULE.SYNC_RULES
            if rule.target == SYNC_MODULE.CANONICAL_REVIEW_TARGET
        )
        private_replacements = rule.replacements[
            : -len(SYNC_MODULE.COMMON_JOEY_TEXT_REPLACEMENTS)
        ]
        self.assertEqual(private_replacements, ())
        self.assertFalse(
            any(
                replacement.path
                in {
                    Path("references/github-pr-probes.md"),
                    Path("tests/test_contracts.py"),
                }
                for replacement in rule.replacements
            )
        )
        self.assertEqual(
            rule.replacements[-len(SYNC_MODULE.COMMON_JOEY_TEXT_REPLACEMENTS) :],
            SYNC_MODULE.COMMON_JOEY_TEXT_REPLACEMENTS,
        )
        self.assertEqual(
            rule.replacement_excluded_paths,
            (Path("tests/fixtures/ci/private.yml"),),
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

    def test_live_private_ci_workflow_matches_synced_fixture_bytes(self) -> None:
        workflow = REPO_ROOT / ".github" / "workflows" / "ci.yml"
        fixture = (
            REPO_ROOT
            / "personal_codex"
            / "skills"
            / "review-orchestration-playbook"
            / "tests"
            / "fixtures"
            / "ci"
            / "private.yml"
        )

        self.assertEqual(workflow.read_bytes(), fixture.read_bytes())

    def test_scheduled_workflow_tracks_generated_github_files(self) -> None:
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "scheduled-sync-release.yml"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "git status --porcelain -- .github scripts tests schema "
            "personal_codex .agents generated-sync-source-lock.json",
            workflow,
        )
        self.assertIn(
            "git add .github scripts tests schema personal_codex .agents "
            "generated-sync-source-lock.json",
            workflow,
        )
        self.assertNotIn(
            "git status --porcelain -- scripts tests personal_codex .agents",
            workflow,
        )
        self.assertNotIn(
            "git add scripts tests personal_codex .agents",
            workflow,
        )

    def test_toolbox_generated_receipt_matches_private_mirror(self) -> None:
        receipt = json.loads(
            (REPO_ROOT / "generated-sync-source-lock.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            receipt["canonical_commit"],
            "7803eebe63782f5539c22e1b7f0d7a7ec587ac3f",
        )
        self.assertEqual(receipt["mirror"], "toolbox")
        self.assertEqual(
            receipt["mirror_repository"],
            "Joey-Tools/codex-toolbox",
        )
        expected_paths = {
            "scripts/codex_personal_sync.py",
            "tests/test_codex_personal_sync.py",
            "schema/sync-manifest.schema.json",
            "tests/test_personal_sync_reconciliation_safety.py",
            "tests/test_release_retention.py",
            "tests/test_scheduler_doctor.py",
        }
        self.assertEqual(
            {entry["target_path"] for entry in receipt["files"]},
            expected_paths,
        )
        for entry in receipt["files"]:
            target = REPO_ROOT / entry["target_path"]
            self.assertTrue(target.is_file())
            self.assertEqual(
                f"{stat.S_IMODE(target.stat().st_mode):04o}",
                entry["mode"],
            )
            self.assertEqual(
                hashlib.sha256(target.read_bytes()).hexdigest(),
                entry["sha256"],
            )
        for relative in (
            "scripts/verify_generated_sync_source_lock.py",
            "tests/test_generated_sync_source_lock.py",
        ):
            self.assertTrue((REPO_ROOT / relative).is_file())

    def test_synced_bug_triage_transport_uses_fixed_private_policy(self) -> None:
        skill_root = REPO_ROOT / "personal_codex/skills/bug-triage-playbook"
        skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        recipes = (skill_root / "references/jenkins-artifact-recipes.md").read_text(
            encoding="utf-8"
        )
        helper = (skill_root / "scripts/jenkins_artifact_probe.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("This private skill supplies", skill)
        self.assertIn(
            "private configuration is fixed and fail-closed by this release process",
            skill,
        )
        self.assertIn("engci-private-sjc.cisco.com", recipes)
        self.assertIn("--auth-profile wme_jenkins_jobs_artifact", recipes)
        self.assertIn(
            'ALLOWED_HOSTS = frozenset({"engci-private-sjc.cisco.com"})',
            helper,
        )
        self.assertIn('"jenkins_mbpm2_codex"', helper)
        self.assertIn('"jenkins_webex_teams"', helper)
        self.assertIn('"wme_jenkins_jobs_artifact"', helper)
        for residual in (
            "jenkins.example.com",
            "JENKINS_ARTIFACT_USER",
            "JENKINS_ARTIFACT_TOKEN",
            "--auth-profile default",
            "DEFAULT_ALLOWED_HOSTS",
        ):
            with self.subTest(residual=residual):
                self.assertNotIn(residual, skill)
                self.assertNotIn(residual, recipes)
                self.assertNotIn(residual, helper)

    def test_python_workflows_disable_bytecode_before_runtime_imports(self) -> None:
        def assert_bytecode_guard_contract(workflow: str) -> None:
            preamble, separator, jobs = workflow.partition("\njobs:\n")
            self.assertEqual(separator, "\njobs:\n")
            self.assertIn(
                '\nenv:\n  PYTHONDONTWRITEBYTECODE: "1"\n',
                preamble,
            )
            self.assertEqual(preamble.count("PYTHONDONTWRITEBYTECODE"), 1)

            job_lines = jobs.splitlines()
            for line_index, line in enumerate(job_lines):
                if "PYTHONDONTWRITEBYTECODE" not in line:
                    continue
                self.assertEqual(line.count("PYTHONDONTWRITEBYTECODE"), 1)
                self.assertRegex(
                    line.strip(),
                    r"\bPYTHONDONTWRITEBYTECODE=1\s*\\$",
                )
                command_start = line_index
                while command_start > 0 and job_lines[
                    command_start - 1
                ].rstrip().endswith("\\"):
                    command_start -= 1
                command_context = "\n".join(job_lines[command_start : line_index + 1])
                variable_index = command_context.rfind("PYTHONDONTWRITEBYTECODE")
                env_index = command_context.rfind("/usr/bin/env -i", 0, variable_index)
                self.assertGreaterEqual(env_index, 0)
                env_arguments = command_context[
                    env_index + len("/usr/bin/env -i") : variable_index
                ]
                self.assertNotRegex(env_arguments, r"[;&|]")
                try:
                    argument_tokens = shlex.split(env_arguments.replace("\\\n", " "))
                except ValueError as error:
                    self.fail(f"invalid env -i argument quoting: {error}")
                for argument in argument_tokens:
                    self.assertRegex(argument, r"^[A-Z_][A-Z0-9_]*=.+$")

        workflow_paths = (
            REPO_ROOT / ".github" / "workflows" / "ci.yml",
            REPO_ROOT / ".github" / "workflows" / "release.yml",
            REPO_ROOT / ".github" / "workflows" / "scheduled-sync-release.yml",
        )
        workflow_cases = [
            (workflow_path.name, workflow_path.read_text(encoding="utf-8"))
            for workflow_path in workflow_paths
        ]
        workflow_cases.append(
            (
                "scrubbed-child-multiline-environment",
                r"""name: Synthetic

env:
  PYTHONDONTWRITEBYTECODE: "1"

jobs:
  test:
    steps:
      - run: |
          /usr/bin/env -i \
            HOME=/var/empty \
            PYTHONDONTWRITEBYTECODE=1 \
            python3 -I -B -S test.py
""",
            )
        )
        workflow_cases.append(
            (
                "scrubbed-child-inline-environment",
                r"""name: Synthetic

env:
  PYTHONDONTWRITEBYTECODE: "1"

jobs:
  test:
    steps:
      - run: |
          /usr/bin/env -i PYTHONDONTWRITEBYTECODE=1 \
            python3 -I -B -S test.py
""",
            )
        )

        for workflow_name, workflow in workflow_cases:
            with self.subTest(workflow=workflow_name):
                assert_bytecode_guard_contract(workflow)

        invalid_job_bodies = {
            "job-env-override": """  test:
    env:
      PYTHONDONTWRITEBYTECODE: "0"
""",
            "unisolated-command-override": r"""  test:
    steps:
      - run: |
          PYTHONDONTWRITEBYTECODE=1 \
            python3 -I -B -S test.py
""",
            "separated-from-scrubbed-command": r"""  test:
    steps:
      - run: |
          /usr/bin/env -i true; PYTHONDONTWRITEBYTECODE=1 \
            python3 -I -B -S test.py
""",
            "duplicate-on-admitted-line": r"""  test:
    steps:
      - run: |
          /usr/bin/env -i PYTHONDONTWRITEBYTECODE=0 PYTHONDONTWRITEBYTECODE=1 \
            python3 -I -B -S test.py
""",
            "duplicate-across-separated-continuations": r"""  test:
    steps:
      - run: |
          /usr/bin/env -i \
            PYTHONDONTWRITEBYTECODE=1 \
            true; PYTHONDONTWRITEBYTECODE=1 \
            python3 -I -B -S test.py
""",
        }
        for case_name, jobs in invalid_job_bodies.items():
            with self.subTest(rejected=case_name):
                workflow = (
                    """name: Synthetic

env:
  PYTHONDONTWRITEBYTECODE: "1"

jobs:
"""
                    + jobs
                )
                with self.assertRaises(AssertionError):
                    assert_bytecode_guard_contract(workflow)

    def test_release_workflows_use_event_appropriate_runners(self) -> None:
        workflows = {
            "scheduled sync-release": (
                REPO_ROOT / ".github" / "workflows" / "scheduled-sync-release.yml",
                "sync-release",
                "ubuntu-latest",
            ),
            "release build": (
                REPO_ROOT / ".github" / "workflows" / "release.yml",
                "release",
                "ubuntu-latest",
            ),
            "release publish": (
                REPO_ROOT / ".github" / "workflows" / "release.yml",
                "publish",
                "ubuntu-latest",
            ),
        }

        for label, (path, job_name, expected_runner) in workflows.items():
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
                self.assertEqual(runners, [expected_runner])

    def test_release_workflow_keeps_pr_validation_release_specific(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        release_job = re.search(
            r"(?ms)^  release:\n(?P<body>.*?)(?=^  [-a-zA-Z0-9_]+:\n|\Z)",
            workflow,
        )
        self.assertIsNotNone(release_job)
        release_body = release_job.group("body")

        self.assertIn("    name: Build private overlay release\n", release_body)
        self.assertIn(
            "  group: private-overlay-release-${{ github.repository }}-${{ github.ref }}",
            workflow,
        )
        self.assertIn(
            "  cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
            workflow,
        )

        def step_body(step_name: str) -> str:
            step = re.search(
                rf"(?ms)^      - name: {re.escape(step_name)}\n"
                r"(?P<body>.*?)(?=^      - name: |\Z)",
                release_body,
            )
            self.assertIsNotNone(step, step_name)
            return step.group("body")

        for duplicate_step in (
            "Check helper syntax without bytecode",
            "Run tests",
            "Verify canonical review workflow",
        ):
            with self.subTest(skipped_on_pull_request=duplicate_step):
                self.assertRegex(
                    step_body(duplicate_step),
                    r"(?m)^        if: github\.event_name != 'pull_request'$",
                )

        for release_specific_step in (
            "Validate sync manifest changes",
            "Build release package",
            "Verify release package",
        ):
            with self.subTest(retained_on_pull_request=release_specific_step):
                self.assertNotIn(
                    "github.event_name != 'pull_request'",
                    step_body(release_specific_step),
                )

        manifest_step = step_body("Validate sync manifest changes")
        self.assertIn(
            '--release-repo "$GITHUB_REPOSITORY"',
            manifest_step,
        )
        self.assertNotIn("--base-ref", manifest_step)

        self.assertRegex(
            step_body("Require source-only Python tree"),
            r"(?m)^        if: always\(\)$",
        )

    def test_full_canonical_suite_jobs_use_python_313_with_bounded_timeout(
        self,
    ) -> None:
        scheduled = (
            REPO_ROOT / ".github" / "workflows" / "scheduled-sync-release.yml"
        ).read_text(encoding="utf-8")
        release = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )

        scheduled_job = re.search(
            r"(?ms)^  sync-release:\n(?P<body>.*?)(?=^  [-a-zA-Z0-9_]+:\n|\Z)",
            scheduled,
        )
        release_job = re.search(
            r"(?ms)^  release:\n(?P<body>.*?)(?=^  [-a-zA-Z0-9_]+:\n|\Z)",
            release,
        )
        publish_job = re.search(
            r"(?ms)^  publish:\n(?P<body>.*?)(?=^  [-a-zA-Z0-9_]+:\n|\Z)",
            release,
        )
        self.assertIsNotNone(scheduled_job)
        self.assertIsNotNone(release_job)
        self.assertIsNotNone(publish_job)

        scheduled_body = scheduled_job.group("body")
        release_body = release_job.group("body")
        publish_body = publish_job.group("body")
        self.assertIn("timeout-minutes: 30", scheduled_body)
        self.assertNotIn("timeout-minutes: 15", scheduled_body)
        self.assertIn("timeout-minutes: 30", release_body)
        self.assertNotIn("timeout-minutes: 15", release_body)
        self.assertIn('python-version: "3.13"', scheduled_body)
        self.assertNotIn('python-version: "3.x"', scheduled_body)
        self.assertIn('python-version: "3.13"', release_body)
        self.assertNotIn('python-version: "3.x"', release_body)
        self.assertIn('python-version: "3.13"', publish_body)
        self.assertNotIn('python-version: "3.x"', publish_body)

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

    def test_active_workflows_do_not_run_retired_waited_delivery_tests(
        self,
    ) -> None:
        workflow_paths = (
            REPO_ROOT / ".github" / "workflows" / "ci.yml",
            REPO_ROOT / ".github" / "workflows" / "release.yml",
            REPO_ROOT / ".github" / "workflows" / "scheduled-sync-release.yml",
            REPO_ROOT
            / "personal_codex"
            / "skills"
            / "review-orchestration-playbook"
            / "tests"
            / "fixtures"
            / "ci"
            / "private.yml",
        )

        for workflow_path in workflow_paths:
            with self.subTest(workflow=workflow_path.name):
                workflow = workflow_path.read_text(encoding="utf-8")
                self.assertNotIn("Verify waited-delivery review contract", workflow)
                self.assertNotIn(
                    "personal_codex/skills/waited-delivery/tests",
                    workflow,
                )

    def test_ci_validates_review_helper_on_minimum_python_across_platforms(
        self,
    ) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        independent_job_match = re.search(
            r"(?ms)^  independent_supervisor_tests:\n"
            r"(?P<body>.*?)(?=^  [-a-zA-Z0-9_]+:\n|\Z)",
            workflow,
        )
        self.assertIsNotNone(independent_job_match)
        independent_job = independent_job_match.group("body")

        self.assertEqual(
            independent_job.count("\n    timeout-minutes: 20\n"),
            1,
        )
        deterministic_step = """      - name: Run deterministic independent supervisor tests
        timeout-minutes: 10
        working-directory: personal_codex/skills/review-orchestration-playbook/scripts/independent_codex_pr_review
        env:
          CODEX_REVIEW_TEST_RUNTIME_PARENT: ${{ runner.temp }}
        run: |
          python3 -m tests.run_required_deterministic_supervisor
"""
        setup_latest_step = """      - uses: actions/setup-python@v5
        id: setup_latest_python
        if: always()
        timeout-minutes: 2
        with:
          python-version: "3.x"
"""
        reconciliation_step = """      - name: Run platform reconciliation safety tests (Python 3.x)
        if: ${{ always() && steps.setup_latest_python.outcome == 'success' }}
        timeout-minutes: 2
        run: python3 -m unittest tests.test_personal_sync_reconciliation_safety
"""
        broker_step = """      - name: Require hosted-runner byte reproduction
        if: always()
        timeout-minutes: 2
        env:
          DEVELOPER_DIR: /Applications/Xcode_26.6.app/Contents/Developer
        run: |
          /bin/bash \\
            personal_codex/skills/review-orchestration-playbook/scripts/build_claude_keychain_broker_macos.sh \\
            --check
"""
        for step in (
            deterministic_step,
            setup_latest_step,
            reconciliation_step,
            broker_step,
        ):
            self.assertEqual(independent_job.count(step), 1)
        self.assertEqual(
            independent_job.count("\n        timeout-minutes: 10\n"),
            1,
        )
        self.assertEqual(
            independent_job.count("\n        timeout-minutes: 2\n"),
            3,
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
        self.assertNotIn("\n  broker_reproducibility:\n", workflow)
        self.assertNotIn("\n  platform-safety:\n", workflow)
        self.assertIn("\n  independent_supervisor_tests:\n", workflow)
        self.assertIn("Require hosted-runner byte reproduction", workflow)
        self.assertEqual(
            workflow.count("Run platform reconciliation safety tests (Python 3.x)"),
            1,
        )
        self.assertEqual(
            workflow.count(
                "if: ${{ always() && steps.setup_latest_python.outcome == 'success' }}"
            ),
            1,
        )
        self.assertEqual(workflow.count("    runs-on: ubuntu-slim\n"), 2)
        self.assertIn(
            "needs:\n      - python-39-compatibility\n    strategy:",
            workflow,
        )
        self.assertIn("\n  test:\n", workflow)
        self.assertIn("\n    name: test\n", workflow)
        self.assertIn("if: ${{ always() }}", workflow)
        self.assertIn(
            "needs:\n"
            "      - platform_tests\n"
            "      - python-39-compatibility\n"
            "      - independent_supervisor_tests\n"
            "      - readonly_install_supervisor_tests",
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
            "INDEPENDENT_SUPERVISOR_RESULT: "
            "${{ needs.independent_supervisor_tests.result }}",
            workflow,
        )
        self.assertIn(
            "READONLY_INSTALL_SUPERVISOR_RESULT: "
            "${{ needs.readonly_install_supervisor_tests.result }}",
            workflow,
        )
        self.assertIn('test "$PLATFORM_TESTS_RESULT" = "success"', workflow)
        self.assertIn('test "$PYTHON_39_RESULT" = "success"', workflow)
        self.assertIn('test "$INDEPENDENT_SUPERVISOR_RESULT" = "success"', workflow)
        self.assertIn(
            'test "$READONLY_INSTALL_SUPERVISOR_RESULT" = "success"', workflow
        )

    def test_python_workflows_disable_implicit_bytecode(self) -> None:
        workflow_paths = (
            REPO_ROOT / ".github" / "workflows" / "ci.yml",
            REPO_ROOT / ".github" / "workflows" / "release.yml",
            REPO_ROOT / ".github" / "workflows" / "scheduled-sync-release.yml",
        )

        for workflow_path in workflow_paths:
            with self.subTest(workflow=workflow_path.name):
                workflow = workflow_path.read_text(encoding="utf-8")
                workflow_preamble, separator, _jobs = workflow.partition("\njobs:\n")
                self.assertEqual(separator, "\njobs:\n")
                self.assertIn(
                    '\nenv:\n  PYTHONDONTWRITEBYTECODE: "1"\n',
                    workflow_preamble,
                )
                self.assertNotIn("python3 -m py_compile", workflow)
                self.assertNotIn("python3 -m compileall", workflow)
                self.assertIn("Require source-only Python tree", workflow)

        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("python3 -B -c 'import pathlib, sys;", readme)
        self.assertIn(
            "PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests",
            readme,
        )
        self.assertNotIn("python3 -m py_compile", readme)

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
        all_manifest_targets = {link["target"] for link in manifest["links"]}
        sync_targets = {str(rule.target) for rule in SYNC_MODULE.SYNC_RULES}
        retired_targets = {str(path) for path in SYNC_MODULE.RETIRED_TARGETS}
        removed_by_target = {link["target"]: link for link in manifest["removed_links"]}

        self.assertEqual(
            manifest_sources - private_only_sources, manifest_sources & sync_targets
        )
        self.assertTrue(manifest_sources.isdisjoint(retired_targets))
        self.assertTrue(sync_targets.isdisjoint(retired_targets))
        self.assertIn("personal_codex/skills/bounded-command-output", manifest_sources)
        self.assertIn("skills/bounded-command-output", manifest_targets)
        self.assertIn("personal_codex/skills/bounded-command-output", sync_targets)
        self.assertNotIn(
            "personal_codex/skills/codex-session-retrospective", manifest_sources
        )
        self.assertIn(
            "personal_codex/skills/synthetic-token-fixtures", manifest_sources
        )
        self.assertIn("skills/synthetic-token-fixtures", manifest_targets)
        self.assertIn("personal_codex/skills/synthetic-token-fixtures", sync_targets)
        self.assertNotIn("skills/apple-notes-db-guardrails", all_manifest_targets)
        self.assertNotIn("skills/apple-notes-work-report", all_manifest_targets)
        self.assertNotIn("skills/codex-rules-hygiene", all_manifest_targets)
        self.assertNotIn("skills/codex-session-retrospective", all_manifest_targets)
        self.assertNotIn("skills/waited-delivery", all_manifest_targets)
        self.assertIn("personal_codex/skills/codex-rules-hygiene", retired_targets)
        self.assertIn(
            "personal_codex/skills/codex-session-retrospective", retired_targets
        )
        self.assertNotIn(
            "personal_codex/skills/codex-session-retrospective", sync_targets
        )
        self.assertIn("personal_codex/skills/waited-delivery", retired_targets)
        for target in (
            "skills/apple-notes-db-guardrails",
            "skills/apple-notes-work-report",
            "skills/codex-rules-hygiene",
            "skills/codex-session-retrospective",
            "skills/waited-delivery",
        ):
            with self.subTest(non_legacy_tombstone=target):
                self.assertFalse(removed_by_target[target].get("legacy", False))

        retrospective_root = (
            REPO_ROOT / "personal_codex/skills/codex-session-retrospective"
        )
        self.assertFalse(retrospective_root.exists())
        for automation in (
            "personal_codex/automations/daily-session-retrospective/automation.toml",
            "personal_codex/automations/weekly-session-retrospective/automation.toml",
        ):
            with self.subTest(retired_automation=automation):
                self.assertNotIn(automation, manifest["reference_only"])
                self.assertFalse((REPO_ROOT / automation).exists())

    def test_personal_agents_routes_local_only_skills_explicitly(self) -> None:
        agents = (REPO_ROOT / "personal_codex" / "AGENTS.md").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "For Apple Notes tasks, start from the `codex-workspace` repo-local",
            agents,
        )
        self.assertIn(
            "do not rely on a global copy under `~/.codex/skills`",
            agents,
        )
        self.assertNotIn("$codex-rules-hygiene", agents)
        self.assertNotIn("$codex-session-retrospective", agents)

        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("private session retrospective automation routing", readme)

    def test_personal_agents_state_matches_review_source_transition(self) -> None:
        source_lock = json.loads(
            (REPO_ROOT / "private-overlay-source-lock.json").read_text(encoding="utf-8")
        )
        actual = SYNC_MODULE._personal_agents_review_guidance_state(
            (REPO_ROOT / SYNC_MODULE.PERSONAL_AGENTS_TARGET).read_bytes()
        )
        expected = "legacy" if _is_legacy_review_source_pin(source_lock) else "current"
        self.assertEqual(actual, expected)

    def test_personal_agents_delegate_workspace_contract_to_review_skill(self) -> None:
        agents = _final_personal_agents_text()
        source_lock = json.loads(
            (REPO_ROOT / "private-overlay-source-lock.json").read_text(encoding="utf-8")
        )
        review_source = next(
            source
            for source in source_lock["sources"]
            if source["name"] == "codex-review-workflows"
        )
        self.assertEqual(
            review_source["repository"],
            "Joey-Tools/codex-review-workflows",
        )
        self.assertIn("clean-workspace preparation", agents)
        self.assertIn("The skill owns adapter selection", agents)
        for retired_detail in (
            "Materialization contract activation",
            "validate-worktree --help",
            "parent_graph_sha256",
            "local_config_sha256",
            "9a90db95cebe2d66c669e2991a8ede62f66563aa",
            "fc2b38bd3001ff1784b3283d3822782b85e48755",
        ):
            with self.subTest(retired_detail=retired_detail):
                self.assertNotIn(retired_detail, agents)

    def test_personal_agents_scopes_bug_triage_to_artifact_transport(self) -> None:
        agents = (REPO_ROOT / "personal_codex" / "AGENTS.md").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "Use `$bug-triage-playbook` only to route an exact remote artifact URL or local ZIP",
            agents,
        )
        self.assertIn(
            "ordinary regression and root-cause analysis on evidence-based reasoning",
            agents,
        )
        self.assertNotIn(
            "Use `$bug-triage-playbook` for log-driven debugging, regression analysis",
            agents,
        )

    def test_tracker_handoff_matches_transport_only_bug_triage_contract(
        self,
    ) -> None:
        bug_triage = REPO_ROOT / "personal_codex/skills/bug-triage-playbook"
        bug_skill = (bug_triage / "SKILL.md").read_text(encoding="utf-8")
        bug_interface = (bug_triage / "agents/openai.yaml").read_text(encoding="utf-8")
        agents = (REPO_ROOT / "personal_codex/AGENTS.md").read_text(encoding="utf-8")
        tracker = REPO_ROOT / "personal_codex/skills/cisco-trackers-lookup"
        tracker_skill = (tracker / "SKILL.md").read_text(encoding="utf-8")
        tracker_workflow = (tracker / "references/workflow.md").read_text(
            encoding="utf-8"
        )

        SYNC_MODULE._validate_private_bug_triage_target_contents(bug_triage)
        self.assertFalse((bug_triage / "references/triage-report.md").exists())
        self.assertIn(
            "exact remote artifact URL or a local ZIP",
            bug_skill,
        )
        self.assertIn(
            "only to route an exact remote artifact URL or local ZIP",
            bug_interface,
        )
        self.assertIn(
            "only to route an exact remote artifact URL or local ZIP",
            agents,
        )
        self.assertNotIn("root-cause hypotheses", bug_interface)

        self.assertIn(
            "acquired from an exact Jenkins URL or inspected in a local ZIP",
            tracker_skill,
        )
        self.assertIn(
            "reserve `bug-triage-playbook` for the bounded transport step above",
            tracker_skill,
        )
        self.assertIn(
            "bounded acquisition from an exact Jenkins URL or inspection of a local ZIP",
            tracker_workflow,
        )
        self.assertIn(
            "Crash-log interpretation, code-level hypotheses, and root-cause ranking remain ordinary evidence-based diagnosis",
            tracker_workflow,
        )
        for stale_claim in (
            "switch to [$bug-triage-playbook]",
            "that remains `bug-triage-playbook`",
            "top-level owner",
            "generic owner",
            "bug-triage-playbook](../../bug-triage-playbook/SKILL.md).\nThis skill is for tracker metadata",
        ):
            with self.subTest(stale_claim=stale_claim):
                self.assertNotIn(stale_claim, tracker_skill)
                self.assertNotIn(stale_claim, tracker_workflow)

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
        self.assertIn(
            "omit `timeout_ms` to use the `30000` millisecond default", agents
        )
        self.assertIn("supported `10000`–`3600000` millisecond range", agents)
        self.assertIn("`30000`–`60000` for ordinary or reviewer polling", agents)
        self.assertIn("longer single waits are valid", agents)

    def test_agents_guidance_uses_canonical_named_review_policy(self) -> None:
        agents = _final_personal_agents_text()

        for anchor in (
            "Use `$review-orchestration-playbook` as the only entrypoint",
            "Single uses one fresh-context local Codex review session",
            "double adds actual Claude Code",
            "triple adds current-head GitHub Codex",
            "The skill owns adapter selection",
            "do not duplicate those contracts here",
            "contemporaneous consent for scoped review egress to that shape",
            "including tracked repository secrets",
            "excludes runtime secrets and credentials",
            "A bare named-review request is report-only",
            "scoped exact `@codex review` producer operation",
            "single-owner, single-flight recovery after ambiguous delivery",
            "repeating that exact POST for the same logical request",
            "never authorizes a second logical request",
            "GitHub Actions rerun, dispatch, or reconciliation requires both",
            "repository-predeclared exact idempotent or reentrant contract",
            "frozen scope and exact inputs",
            "separate current-task delivery or readiness authorization",
            "never authorizes a different workflow, input, scope, destination",
        ):
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, agents)

        for retired in (
            "Materialization contract activation",
            "canonical Claude Code compatibility range",
            "github-codex-evidence-authority.md",
            "thumbs-up-clean",
            "terminal-payload",
            "`isolated_review`",
            "mandatory independent-codex-pr-review",
            "required `independent-codex-pr-review`",
            "$external-review-playbook",
            "$copilot-review-playbook",
            "is the only mutation implied by bare triple",
        ):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, agents)

    def test_agents_guidance_leaves_skill_repo_gate_to_scoped_guidance(self) -> None:
        agents = _final_personal_agents_text()
        self.assertNotIn("skill-repo-codex-gate", agents)
        for repository in (
            "codex-toolbox",
            "codex-debug-triage",
            "codex-review-workflows",
            "codex-workflow-hygiene",
            "codex-project-journal",
            "codex-apple-notes-toolkit",
            "codex-private-workflows",
        ):
            with self.subTest(repository=repository):
                self.assertNotIn(f"`{repository}`", agents)

    def test_codex_review_gate_is_compatibility_status_only(self) -> None:
        workflow_path = REPO_ROOT / ".github" / "workflows" / "codex-review-gate.yml"
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

    def test_scheduled_workflow_freezes_and_revalidates_five_sources(self) -> None:
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "scheduled-sync-release.yml"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "python3 scripts/private_overlay_source_lock.py emit-github-outputs",
            workflow,
        )
        self.assertIn(
            "ref: ${{ steps.source-lock.outputs.codex_toolbox_sha }}",
            workflow,
        )
        self.assertEqual(workflow.count("fetch-depth: 0"), 6)
        self.assertIn("Detach dynamic source checkouts", workflow)
        self.assertEqual(
            workflow.count("checkout --detach --no-recurse-submodules HEAD"), 1
        )
        self.assertIn("refresh-non-toolbox-pins", workflow)
        self.assertEqual(workflow.count("verify-checkouts"), 2)
        refresh = workflow.index("- name: Refresh non-toolbox source pins")
        preflight = workflow.index("- name: Verify source checkouts before sync")
        sync = workflow.index("- name: Sync private overlay sources")
        postflight = workflow.index("- name: Verify source checkouts after sync")
        self.assertLess(refresh, preflight)
        self.assertLess(preflight, sync)
        self.assertLess(sync, postflight)
        self.assertIn("private-overlay-source-lock.json)", workflow)
        self.assertIn("private-overlay-source-lock.json\n", workflow)
        self.assertIn("steps.refreshed-lock.outputs.source_lock_sha256", workflow)
        for name, _repository in (
            ("codex_toolbox", "Joey-Tools/codex-toolbox"),
            ("codex_debug_triage", "Joey-Tools/codex-debug-triage"),
            ("codex_review_workflows", "Joey-Tools/codex-review-workflows"),
            ("codex_workflow_hygiene", "Joey-Tools/codex-workflow-hygiene"),
            ("codex_project_journal", "Joey-Tools/codex-project-journal"),
        ):
            with self.subTest(source=name):
                self.assertIn(f"steps.refreshed-lock.outputs.{name}_sha", workflow)
                self.assertIn(f"steps.refreshed-lock.outputs.{name}_tree", workflow)

    def test_source_lock_inventory_matches_every_sync_rule_repository(self) -> None:
        source_lock = SOURCE_LOCK_MODULE.load_source_lock(REPO_ROOT)
        locked_repositories = tuple(pin.name for pin in source_lock.pins)
        rule_repositories = tuple(
            dict.fromkeys(rule.repo for rule in SYNC_MODULE.SYNC_RULES)
        )

        self.assertEqual(len(locked_repositories), len(rule_repositories))
        self.assertEqual(frozenset(locked_repositories), frozenset(rule_repositories))
        self.assertIn("codex-workflow-hygiene", locked_repositories)
        workflow_hygiene_targets = {
            str(rule.target)
            for rule in SYNC_MODULE.SYNC_RULES
            if rule.repo == "codex-workflow-hygiene"
        }
        self.assertEqual(
            workflow_hygiene_targets,
            {
                "personal_codex/skills/bounded-command-output",
                "personal_codex/skills/codex-session-mining",
                "personal_codex/skills/joey-skill-authoring",
            },
        )

    @unittest.skipUnless(sys.platform == "darwin", "macOS-only Git trust root")
    def test_macos_source_lock_binds_actual_command_line_tools_git(self) -> None:
        trusted = SOURCE_LOCK_MODULE._trusted_git_path()

        self.assertEqual(
            trusted.path,
            Path("/Library/Developer/CommandLineTools/usr/bin/git"),
        )
        self.assertNotEqual(trusted.path, Path("/usr/bin/git"))
        SOURCE_LOCK_MODULE._revalidate_trusted_git(trusted)

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
        self.assertIn("fine-grained PAT or GitHub App token", readme)
        self.assertIn("`Workflows: write`", readme)
        self.assertIn("classic PAT must include the `workflow` scope", readme)
        self.assertIn("codex-automation", readme)

    def test_readme_documents_canonical_source_trust_boundary(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        normalized_readme = re.sub(r"\s+", " ", readme)

        self.assertIn("`private-overlay-source-lock.json`", normalized_readme)
        self.assertIn("exact immutable base release SHA", normalized_readme)
        self.assertIn("frozen into the candidate lock", normalized_readme)
        self.assertIn("verified against the candidate lock", normalized_readme)
        self.assertIn("exact review-workflow SHA and tree", normalized_readme)
        self.assertIn(
            "The current generated PR workflow declares only `contents: read` and "
            "contains no `secrets.*` references",
            normalized_readme,
        )

        ci_workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(
            encoding="utf-8"
        )
        private_fixture = (
            REPO_ROOT
            / "personal_codex"
            / "skills"
            / "review-orchestration-playbook"
            / "tests"
            / "fixtures"
            / "ci"
            / "private.yml"
        ).read_text(
            encoding="utf-8",
        )
        self.assertEqual(ci_workflow, private_fixture)
        preamble, separator, _jobs = ci_workflow.partition("\njobs:\n")
        self.assertEqual(separator, "\njobs:\n")
        self.assertIn("permissions:\n  contents: read", preamble)
        self.assertNotIn("secrets.", ci_workflow)

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
            "Load frozen source identities",
            "Check out codex-toolbox",
            "Check out codex-debug-triage",
            "Check out codex-review-workflows",
            "Check out codex-workflow-hygiene",
            "Check out codex-project-journal",
            "Detach dynamic source checkouts",
            "Refresh non-toolbox source pins",
            "Verify source checkouts before sync",
            "Sync private overlay sources",
            "Verify source checkouts after sync",
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
        self.assertNotIn("codex-waited-delivery", workflow)
        self.assertNotIn("personal_codex/skills/waited-delivery", workflow)
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

    def test_release_workflow_gates_release_on_controller_compatibility(
        self,
    ) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )

        def job_body(job_name: str) -> str:
            job = re.search(
                rf"(?ms)^  {re.escape(job_name)}:\n"
                r"(?P<body>.*?)(?=^  [-a-zA-Z0-9_]+:\n|\Z)",
                workflow,
            )
            self.assertIsNotNone(job, job_name)
            return job.group("body")

        controller_jobs = {
            "controller_python_39": ("ubuntu-latest", 'python-version: "3.9"'),
            "controller_macos": ("macos-latest", 'python-version: "3.13"'),
        }
        for job_name, (runner, python_version) in controller_jobs.items():
            with self.subTest(job=job_name):
                body = job_body(job_name)
                self.assertIn(f"    runs-on: {runner}\n", body)
                self.assertIn(python_version, body)
                self.assertIn(
                    "personal_codex/bin/codex-private-macos-sync",
                    body,
                )
                self.assertIn(
                    "python3 -m unittest tests.test_private_macos_sync_controller",
                    body,
                )
                self.assertRegex(
                    body,
                    r"(?ms)- name: Require source-only Python tree\n"
                    r"        if: always\(\)",
                )

        release_body = job_body("release")
        self.assertIn("    name: Build private overlay release\n", release_body)
        self.assertIn(
            "    needs:\n      - controller_python_39\n      - controller_macos\n",
            release_body,
        )
        self.assertIn("always() &&", release_body)
        self.assertIn(
            "PYTHON_39_RESULT: ${{ needs.controller_python_39.result }}",
            release_body,
        )
        self.assertIn(
            "MACOS_CONTROLLER_RESULT: ${{ needs.controller_macos.result }}",
            release_body,
        )
        self.assertIn('test "$PYTHON_39_RESULT" = "success"', release_body)
        self.assertIn('test "$MACOS_CONTROLLER_RESULT" = "success"', release_body)
        self.assertIn("    needs: release\n", job_body("publish"))


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
            "tag_name": (f"personal-codex-20260522-100000-{sha[:tag_suffix_length]}"),
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
            name.lower(): value for name, value in capability_request.header_items()
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
            self.assertFalse(RELEASE_MODULE.release_complete("owner/repo", "a" * 40))

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
        incomplete["tag_name"] = f"personal-codex-20260522-100001-{sha[:7]}"
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
                    self.assertFalse(RELEASE_MODULE.release_complete("owner/repo", sha))

    def test_multiple_incomplete_published_releases_fail_closed(self) -> None:
        sha = "a" * 40
        candidates = [
            self._release_candidate(sha, release_id=10, assets=[]),
            self._release_candidate(sha, release_id=20, assets=[]),
        ]
        candidates[1]["tag_name"] = f"personal-codex-20260522-100001-{sha[:7]}"
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
            RELEASE_MODULE.create_or_find_release("owner/repo", sha, expected_names)
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
        candidates[1]["tag_name"] = f"personal-codex-20260522-100001-{sha[:8]}"

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
            _selected, _uploaded_names, done = RELEASE_MODULE.create_or_find_release(
                "owner/repo",
                sha,
                RELEASE_MODULE._expected_asset_names(sha),
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
        drafts[1]["tag_name"] = f"personal-codex-20260522-100001-{sha[:7]}"

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
            selected, _uploaded_names, done = RELEASE_MODULE.create_or_find_release(
                "owner/repo",
                sha,
                RELEASE_MODULE._expected_asset_names(sha),
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
            selected, _uploaded_names, done = RELEASE_MODULE.create_or_find_release(
                "owner/repo",
                sha,
                RELEASE_MODULE._expected_asset_names(sha),
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
        candidate["tag_name"] = f"personal-codex-20260522-100000-{wrong_prefix}"
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
                    json.dumps({"name": asset_name, "state": "uploaded"}).encode(
                        "utf-8"
                    )
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
                    json.dumps({"name": asset_name, "state": "uploaded"}).encode(
                        "utf-8"
                    )
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
        self.assertFalse(any(request["method"] == "PATCH" for request in requests))
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
                    json.dumps({"name": asset_name, "state": "uploaded"}).encode(
                        "utf-8"
                    )
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
                    json.dumps({"name": asset_name, "state": "uploaded"}).encode(
                        "utf-8"
                    )
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
                    return json.dumps({"name": self.name, "state": "uploaded"}).encode(
                        "utf-8"
                    )

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
                    json.dumps({"name": asset_name, "state": "uploaded"}).encode(
                        "utf-8"
                    )
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
                    self.assertFalse(RELEASE_MODULE.release_complete("owner/repo", sha))

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
