from __future__ import annotations

import contextlib
import hashlib
import hmac
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import ast
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = REPO_ROOT / "scripts" / "codex_personal_sync.py"
PACKAGE_SCRIPT = REPO_ROOT / "scripts" / "build_personal_codex_package.py"
MANIFEST_VALIDATOR = REPO_ROOT / "scripts" / "validate_sync_manifest_changes.py"
SPEC = importlib.util.spec_from_file_location("codex_personal_sync", SYNC_SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


PUBLIC_SHA = "1" * 40
PRIVATE_SHA = "2" * 40


def automation_prompt(automation_id: str) -> str:
    path = (
        REPO_ROOT / "personal_codex" / "automations" / automation_id / "automation.toml"
    )
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("prompt = "):
            return ast.literal_eval(line.partition("=")[2].strip())
    raise AssertionError(f"missing prompt in {path}")


def write_public_base_fixture(root: Path) -> None:
    script_root = root / "scripts"
    script_root.mkdir(parents=True)
    (script_root / "codex_personal_sync.py").write_text(
        "#!/usr/bin/env python3\n",
        encoding="utf-8",
    )
    skill_root = (
        root
        / "personal_codex"
        / "skills"
        / "submodule-linked-worktrees"
    )
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: submodule-linked-worktrees\n---\n",
        encoding="utf-8",
    )
    manifest_root = root / "personal_codex"
    (manifest_root / "sync-manifest.json").write_text(
        """
{
  "version": 1,
  "owner": "public",
  "links": [
    {
      "source": "scripts/codex_personal_sync.py",
      "target": "bin/codex-personal-sync",
      "kind": "file"
    },
    {
      "source": "personal_codex/skills/submodule-linked-worktrees",
      "target": "skills/submodule-linked-worktrees",
      "kind": "skill"
    }
  ],
  "reference_only": []
}
""".strip()
        + "\n",
        encoding="utf-8",
    )


def write_scheduler_runner(home: Path) -> Path:
    runner = home / "bin" / "codex-personal-sync"
    runner.parent.mkdir(parents=True)
    runner.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    runner.chmod(0o755)
    return runner


class PrivateOverlayPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="codex-private-overlay.")
        self.root = Path(self.tmpdir.name)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def run_quietly(self, callback, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return callback(*args, **kwargs)

    def build_private_package(
        self,
        *,
        manifest: str | None = "personal_codex/private-sync-manifest.json",
        sha: str = PRIVATE_SHA,
        repo_root: Path = REPO_ROOT,
    ) -> Path:
        dist_dir = self.root / "dist"
        args = [
            sys.executable,
            str(PACKAGE_SCRIPT),
            "--repo-root",
            str(repo_root),
            "--sha",
            sha,
            "--output-dir",
            str(dist_dir),
        ]
        if manifest is not None:
            args.extend(["--manifest", manifest])
        completed = subprocess.run(
            args,
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stderr or completed.stdout,
        )
        return dist_dir / f"personal-codex-{sha}.tar.gz"

    def test_private_manifest_packages_overlay_targets(self) -> None:
        temporary_root = REPO_ROOT / ".codex-tmp"
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="private-overlay-recovery-package-test.",
            dir=temporary_root,
        ) as recovery:
            (Path(recovery) / "must-not-package").write_text(
                "recovery\n",
                encoding="utf-8",
            )
            archive_path = self.build_private_package()
        extract_root = self.root / "extract"
        release_root = MODULE.safe_extract_archive(archive_path, extract_root)
        entries = MODULE.validate_release_tree(release_root)
        targets = {entry.target.as_posix(): entry for entry in entries}
        manifest = json.loads(
            (release_root / "personal_codex" / "sync-manifest.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertTrue(all(entry.owner == "private" for entry in entries))
        self.assertEqual(
            manifest["base_release"]["repo"],
            "Joey-Tools/codex-toolbox",
        )
        self.assertIn("AGENTS.md", targets)
        self.assertIn("skills/agile-delivery-workflow", targets)
        self.assertIn("skills/cisco-trackers-lookup", targets)
        self.assertIn("skills/remote-host-context", targets)
        self.assertIn("skills/apple-notes-work-report", targets)
        self.assertNotIn("bin/codex-personal-sync", targets)
        self.assertEqual(
            manifest["removed_links"],
            [
                {
                    "id": "2026-07-15-move-submodule-linked-worktrees-to-public",
                    "source": "personal_codex/skills/submodule-linked-worktrees",
                    "target": "skills/submodule-linked-worktrees",
                    "kind": "skill",
                    "replacement_target": "skills/submodule-linked-worktrees",
                    "legacy": True,
                }
            ],
        )
        generated_catalog = (
            release_root
            / "personal_codex"
            / "skills"
            / "review-orchestration-playbook"
            / "scripts"
            / "review_runtime"
            / "synthetic-token-catalog.json"
        )
        private_catalog = (
            REPO_ROOT
            / "personal_codex"
            / "private-overrides"
            / "review-orchestration-playbook"
            / "synthetic-token-catalog.json"
        )
        self.assertTrue(generated_catalog.is_file())
        self.assertTrue(
            hmac.compare_digest(
                generated_catalog.read_bytes(),
                private_catalog.read_bytes(),
            ),
            "packaged generated catalog differs from the private override source",
        )
        with tarfile.open(archive_path, "r:gz") as archive:
            self.assertFalse(any(".codex-tmp" in name for name in archive.getnames()))
            self.assertFalse(
                any("private-overrides" in name for name in archive.getnames())
            )

    def test_manifest_validator_defaults_to_private_manifest(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(MANIFEST_VALIDATOR),
                "--repo-root",
                str(REPO_ROOT),
                "--base-ref",
                "HEAD",
            ],
            check=False,
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sync manifest change validation ok", result.stdout)

    def test_default_manifest_packages_private_overlay(self) -> None:
        archive_path = self.build_private_package(manifest=None)
        release_root = MODULE.safe_extract_archive(archive_path, self.root / "extract")
        entries = MODULE.validate_release_tree(release_root)

        self.assertTrue(all(entry.owner == "private" for entry in entries))
        self.assertFalse(
            any(
                entry.target.as_posix() == "bin/codex-personal-sync"
                for entry in entries
            )
        )

    def test_package_excludes_private_override_source(self) -> None:
        repo_root = self.root / "repo"
        review_skill = (
            repo_root
            / "personal_codex"
            / "skills"
            / "review-orchestration-playbook"
        )
        generated_catalog = (
            review_skill
            / "scripts"
            / "review_runtime"
            / "synthetic-token-catalog.json"
        )
        generated_catalog.parent.mkdir(parents=True)
        (review_skill / "SKILL.md").write_text(
            "---\nname: review-orchestration-playbook\n---\n",
            encoding="utf-8",
        )
        expected = b'{"pool":"private"}\n'
        generated_catalog.write_bytes(expected)
        override_catalog = (
            repo_root
            / "personal_codex"
            / "private-overrides"
            / "review-orchestration-playbook"
            / "synthetic-token-catalog.json"
        )
        override_catalog.parent.mkdir(parents=True)
        override_catalog.write_bytes(expected)
        synthetic_skill = (
            repo_root / "personal_codex" / "skills" / "synthetic-token-fixtures"
        )
        synthetic_skill.mkdir(parents=True)
        (synthetic_skill / "SKILL.md").write_text(
            "---\nname: synthetic-token-fixtures\n---\n",
            encoding="utf-8",
        )
        manifest_path = repo_root / "personal_codex" / "private-sync-manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "owner": "private",
                    "links": [
                        {
                            "source": "personal_codex/skills/review-orchestration-playbook",
                            "target": "skills/review-orchestration-playbook",
                            "kind": "skill",
                        },
                        {
                            "source": "personal_codex/skills/synthetic-token-fixtures",
                            "target": "skills/synthetic-token-fixtures",
                            "kind": "skill",
                        },
                    ],
                    "reference_only": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )

        archive_path = self.build_private_package(repo_root=repo_root)

        with tarfile.open(archive_path, "r:gz") as archive:
            names = archive.getnames()
            catalog_member = next(
                member
                for member in archive.getmembers()
                if member.name.endswith(
                    "personal_codex/skills/review-orchestration-playbook/"
                    "scripts/review_runtime/synthetic-token-catalog.json"
                )
            )
            extracted = archive.extractfile(catalog_member)
            assert extracted is not None
            self.assertEqual(extracted.read(), expected)
            self.assertTrue(
                any(
                    member.name.endswith(
                        "personal_codex/skills/synthetic-token-fixtures/SKILL.md"
                    )
                    for member in archive.getmembers()
                )
            )

        self.assertFalse(any("private-overrides" in name for name in names))

    def test_package_builder_rejects_nested_directory_symlinks(self) -> None:
        repo_root = self.root / "repo"
        source_root = repo_root / "personal_codex" / "skills" / "example"
        source_root.mkdir(parents=True)
        (source_root / "SKILL.md").write_text(
            "---\nname: example\n---\n", encoding="utf-8"
        )
        (source_root / "leak").symlink_to(Path.home())
        manifest_path = repo_root / "personal_codex" / "private-sync-manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "owner": "private",
                    "links": [
                        {
                            "source": "personal_codex/skills/example",
                            "target": "skills/example",
                            "kind": "skill",
                        }
                    ],
                    "reference_only": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                sys.executable,
                str(PACKAGE_SCRIPT),
                "--repo-root",
                str(repo_root),
                "--sha",
                PRIVATE_SHA,
                "--output-dir",
                str(self.root / "dist"),
            ],
            check=False,
            text=True,
            capture_output=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nested symlink", result.stderr)

    def test_package_builder_rejects_generated_manifest_sources(self) -> None:
        repo_root = self.root / "repo"
        cache_root = repo_root / "personal_codex" / "skills" / "example" / "__pycache__"
        cache_root.mkdir(parents=True)
        (cache_root / "generated.pyc").write_bytes(b"bytecode")
        manifest_path = repo_root / "personal_codex" / "private-sync-manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "owner": "private",
                    "links": [
                        {
                            "source": "personal_codex/skills/example/__pycache__",
                            "target": "skills/example/__pycache__",
                            "kind": "directory",
                        }
                    ],
                    "reference_only": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                sys.executable,
                str(PACKAGE_SCRIPT),
                "--repo-root",
                str(repo_root),
                "--sha",
                PRIVATE_SHA,
                "--output-dir",
                str(self.root / "dist"),
            ],
            check=False,
            text=True,
            capture_output=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing generated manifest source", result.stderr)

    def test_package_builder_rejects_generated_manifest_file_sources(self) -> None:
        repo_root = self.root / "repo"
        source_file = (
            repo_root / "personal_codex" / "skills" / "example" / "generated.pyc"
        )
        source_file.parent.mkdir(parents=True)
        source_file.write_bytes(b"bytecode")
        manifest_path = repo_root / "personal_codex" / "private-sync-manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "owner": "private",
                    "links": [
                        {
                            "source": "personal_codex/skills/example/generated.pyc",
                            "target": "skills/example/generated.pyc",
                            "kind": "file",
                        }
                    ],
                    "reference_only": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                sys.executable,
                str(PACKAGE_SCRIPT),
                "--repo-root",
                str(repo_root),
                "--sha",
                PRIVATE_SHA,
                "--output-dir",
                str(self.root / "dist"),
            ],
            check=False,
            text=True,
            capture_output=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing generated manifest source", result.stderr)

    def test_package_builder_rejects_symlink_inside_generated_directory(self) -> None:
        repo_root = self.root / "repo"
        source_root = repo_root / "personal_codex" / "skills" / "example"
        cache_root = source_root / "__pycache__"
        cache_root.mkdir(parents=True)
        (source_root / "SKILL.md").write_text(
            "---\nname: example\n---\n", encoding="utf-8"
        )
        (cache_root / "leak.pyc").symlink_to(Path.home())
        manifest_path = repo_root / "personal_codex" / "private-sync-manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "owner": "private",
                    "links": [
                        {
                            "source": "personal_codex/skills/example",
                            "target": "skills/example",
                            "kind": "skill",
                        }
                    ],
                    "reference_only": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                sys.executable,
                str(PACKAGE_SCRIPT),
                "--repo-root",
                str(repo_root),
                "--sha",
                PRIVATE_SHA,
                "--output-dir",
                str(self.root / "dist"),
            ],
            check=False,
            text=True,
            capture_output=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nested symlink", result.stderr)

    def test_package_builder_excludes_generated_bytecode(self) -> None:
        repo_root = self.root / "repo"
        source_root = repo_root / "personal_codex" / "skills" / "example"
        cache_root = source_root / "__pycache__"
        cache_root.mkdir(parents=True)
        fixture_asset = source_root / "assets" / "example.pyc" / "fixture.txt"
        fixture_asset.parent.mkdir(parents=True)
        (source_root / "SKILL.md").write_text(
            "---\nname: example\n---\n", encoding="utf-8"
        )
        (source_root / ".DS_Store").write_text("metadata\n", encoding="utf-8")
        (cache_root / "session_retrospective.cpython-314.pyc").write_bytes(b"bytecode")
        fixture_asset.write_text("not bytecode\n", encoding="utf-8")
        manifest_path = repo_root / "personal_codex" / "private-sync-manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "owner": "private",
                    "links": [
                        {
                            "source": "personal_codex/skills/example",
                            "target": "skills/example",
                            "kind": "skill",
                        }
                    ],
                    "reference_only": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )

        archive_path = self.build_private_package(repo_root=repo_root)
        with tarfile.open(archive_path, "r:gz") as archive:
            names = archive.getnames()

        self.assertTrue(any(name.endswith("/SKILL.md") for name in names))
        self.assertTrue(
            any(name.endswith("/assets/example.pyc/fixture.txt") for name in names)
        )
        self.assertFalse(any("__pycache__" in name for name in names))
        self.assertFalse(
            any(
                name.endswith("session_retrospective.cpython-314.pyc") for name in names
            )
        )
        self.assertFalse(any(name.endswith(".DS_Store") for name in names))

    def test_verify_checksum_enforces_input_size_limits(self) -> None:
        archive = self.root / f"personal-codex-{PRIVATE_SHA}.tar.gz"
        checksum = self.root / f"personal-codex-{PRIVATE_SHA}.sha256"
        archive.write_bytes(b"archive")
        digest = hashlib.sha256(b"archive").hexdigest()
        checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")

        with (
            self.subTest(limit="checksum"),
            mock.patch.object(MODULE, "MAX_ARCHIVE_CHECKSUM_BYTES", 1),
        ):
            with self.assertRaisesRegex(MODULE.SyncError, "checksum file exceeds"):
                MODULE.verify_checksum(archive, checksum)

        with (
            self.subTest(limit="compressed"),
            mock.patch.object(MODULE, "MAX_ARCHIVE_COMPRESSED_BYTES", 1),
        ):
            with self.assertRaisesRegex(MODULE.SyncError, "compressed archive exceeds"):
                MODULE.verify_checksum(archive, checksum)

    def test_verify_checksum_rejects_symlink_and_fifo_inputs(self) -> None:
        for role in ("archive", "checksum"):
            for kind in ("symlink", "fifo"):
                with self.subTest(role=role, kind=kind):
                    case_root = self.root / f"unsafe-{role}-{kind}"
                    case_root.mkdir()
                    archive = case_root / f"personal-codex-{PRIVATE_SHA}.tar.gz"
                    checksum = case_root / f"personal-codex-{PRIVATE_SHA}.sha256"
                    archive_payload = b"archive"
                    archive.write_bytes(archive_payload)
                    digest = hashlib.sha256(archive_payload).hexdigest()
                    checksum.write_text(
                        f"{digest}  {archive.name}\n",
                        encoding="utf-8",
                    )
                    unsafe_path = archive if role == "archive" else checksum
                    unsafe_path.unlink()
                    if kind == "symlink":
                        backing = case_root / f"{role}-backing"
                        if role == "archive":
                            backing.write_bytes(archive_payload)
                        else:
                            backing.write_text(
                                f"{digest}  {archive.name}\n",
                                encoding="utf-8",
                            )
                        unsafe_path.symlink_to(backing)
                    else:
                        os.mkfifo(unsafe_path)

                    with self.assertRaisesRegex(
                        MODULE.SyncError,
                        "unsafe|non-regular",
                    ):
                        MODULE.verify_checksum(archive, checksum)

    def test_verify_checksum_rejects_same_inode_archive_rewrite(self) -> None:
        archive = self.root / f"personal-codex-{PRIVATE_SHA}.tar.gz"
        checksum = self.root / f"personal-codex-{PRIVATE_SHA}.sha256"
        archive_payload = b"archive"
        archive.write_bytes(archive_payload)
        digest = hashlib.sha256(archive_payload).hexdigest()
        checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
        archive_metadata = archive.stat()
        archive_identity = (
            archive_metadata.st_dev,
            archive_metadata.st_ino,
            archive_metadata.st_size,
        )
        real_read = os.read
        rewritten = False

        def rewrite_after_archive_read(file_descriptor, size):
            nonlocal rewritten
            metadata = os.fstat(file_descriptor)
            is_archive = (metadata.st_dev, metadata.st_ino) == archive_identity[:2]
            payload = real_read(file_descriptor, min(size, 1) if is_archive else size)
            if is_archive and payload and not rewritten:
                rewritten = True
                writer_fd = os.open(archive, os.O_RDWR)
                try:
                    # Rewrite a byte that the bounded first read has not copied yet.
                    os.lseek(writer_fd, 1, os.SEEK_SET)
                    os.write(writer_fd, b"X")
                    os.fsync(writer_fd)
                finally:
                    os.close(writer_fd)
            return payload

        with mock.patch.object(MODULE.os, "read", rewrite_after_archive_read):
            with self.assertRaisesRegex(
                MODULE.SyncError,
                "compressed archive changed while reading|checksum mismatch",
            ):
                MODULE.verify_checksum(archive, checksum)

        self.assertTrue(rewritten)
        final_metadata = archive.stat()
        self.assertEqual(
            (final_metadata.st_dev, final_metadata.st_ino, final_metadata.st_size),
            archive_identity,
        )

    def test_download_extracts_verified_snapshot_after_archive_path_replacement(
        self,
    ) -> None:
        destination = self.root / "download"
        destination.mkdir()
        assets = MODULE.ReleaseAssets(
            tag_name="personal-codex-20260520-120000-2222222",
            sha=PRIVATE_SHA,
            archive_name=f"personal-codex-{PRIVATE_SHA}.tar.gz",
            checksum_name=f"personal-codex-{PRIVATE_SHA}.sha256",
            archive_id=1,
            archive_size=1,
            checksum_id=2,
            checksum_size=1,
        )
        archive_path = destination / assets.archive_name
        archive_path.write_bytes(self.build_private_package().read_bytes())
        checksum_path = destination / assets.checksum_name
        digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        checksum_path.write_text(
            f"{digest}  {archive_path.name}\n",
            encoding="utf-8",
        )
        malicious_archive = self.root / "replacement.tar.gz"
        malicious_archive.write_bytes(b"replacement")
        retained_archive = self.root / "verified.tar.gz"
        real_extract = MODULE._safe_extract_archive_snapshot

        def replace_archive_path(snapshot, extract_root):
            archive_path.rename(retained_archive)
            malicious_archive.rename(archive_path)
            return real_extract(snapshot, extract_root)

        with (
            mock.patch.object(MODULE, "find_latest_release", return_value={}),
            mock.patch.object(MODULE, "select_release_assets", return_value=assets),
            mock.patch.object(MODULE, "download_release_assets"),
            mock.patch.object(
                MODULE,
                "_safe_extract_archive_snapshot",
                side_effect=replace_archive_path,
            ),
        ):
            release = MODULE.download_and_extract_release("owner/repo", destination)

        self.assertTrue(
            (release.release_root / "personal_codex" / "sync-manifest.json").is_file()
        )
        self.assertIsNotNone(release.release_expectation)
        release_metadata = release.release_root.stat()
        self.assertEqual(
            release.release_expectation[1],
            (release_metadata.st_dev, release_metadata.st_ino),
        )
        self.assertRegex(release.release_expectation[0][2], r"^[0-9a-f]{64}$")
        self.assertEqual(archive_path.read_bytes(), b"replacement")
        self.assertTrue(retained_archive.is_file())

    def test_safe_extract_rejects_exact_duplicate_before_writing(self) -> None:
        archive_path = self.root / "duplicate.tar.gz"
        member_name = "personal-codex/payload.txt"
        with tarfile.open(archive_path, "w:gz") as archive:
            for _index in range(2):
                data = b"same"
                member = tarfile.TarInfo(member_name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))

        destination = self.root / "duplicate-extract"
        with self.assertRaisesRegex(MODULE.SyncError, "duplicate archive member path"):
            MODULE.safe_extract_archive(archive_path, destination)

        self.assertFalse(destination.exists())

    def test_safe_extract_enforces_member_resource_limits_before_writing(self) -> None:
        cases = (
            ("members", 2, (b"a", b"b")),
            ("member-bytes", 1, (b"long",)),
            ("total-bytes", 2, (b"abc", b"def")),
        )
        for name, member_count, payloads in cases:
            with self.subTest(limit=name):
                archive_path = self.root / f"limit-{name}.tar.gz"
                with tarfile.open(archive_path, "w:gz") as archive:
                    for index in range(member_count):
                        payload = payloads[index]
                        member = tarfile.TarInfo(f"root/file-{index}.txt")
                        member.size = len(payload)
                        archive.addfile(member, io.BytesIO(payload))
                destination = self.root / f"limit-{name}-extract"
                if name == "members":
                    patches = (mock.patch.object(MODULE, "MAX_ARCHIVE_MEMBERS", 1),)
                    expected = "member limit"
                elif name == "member-bytes":
                    patches = (
                        mock.patch.object(MODULE, "MAX_ARCHIVE_MEMBER_BYTES", 3),
                    )
                    expected = "member exceeds expanded byte limit"
                else:
                    patches = (
                        mock.patch.object(MODULE, "MAX_ARCHIVE_MEMBER_BYTES", 10),
                        mock.patch.object(MODULE, "MAX_ARCHIVE_EXPANDED_BYTES", 5),
                    )
                    expected = "total expanded byte limit"
                with contextlib.ExitStack() as stack:
                    for patcher in patches:
                        stack.enter_context(patcher)
                    with self.assertRaisesRegex(MODULE.SyncError, expected):
                        MODULE.safe_extract_archive(archive_path, destination)
                self.assertFalse(destination.exists())

    def test_safe_extract_counts_pax_metadata_against_expanded_limit(self) -> None:
        archive_path = self.root / "pax-metadata-limit.tar.gz"
        with tarfile.open(
            archive_path,
            "w:gz",
            format=tarfile.PAX_FORMAT,
        ) as archive:
            payload = b"x"
            member = tarfile.TarInfo("root/file.txt")
            member.size = len(payload)
            member.pax_headers = {"comment": "x" * 4096}
            archive.addfile(member, io.BytesIO(payload))
        destination = self.root / "pax-metadata-limit-extract"

        with mock.patch.object(MODULE, "MAX_ARCHIVE_EXPANDED_BYTES", 1024):
            with self.assertRaisesRegex(
                MODULE.SyncError,
                "total expanded byte limit",
            ):
                MODULE.safe_extract_archive(archive_path, destination)

        self.assertFalse(destination.exists())

    def test_safe_extract_counts_trailing_gzip_payload_against_expanded_limit(
        self,
    ) -> None:
        archive_path = self.build_private_package()
        archive_payload = archive_path.read_bytes()
        expanded_size = len(MODULE.gzip.decompress(archive_payload))
        archive_path.write_bytes(
            archive_payload + MODULE.gzip.compress(b"x" * 4096)
        )
        destination = self.root / "trailing-payload-extract"

        with mock.patch.object(
            MODULE,
            "MAX_ARCHIVE_EXPANDED_BYTES",
            expanded_size + 1024,
        ):
            with self.assertRaisesRegex(
                MODULE.SyncError,
                "total expanded byte limit",
            ):
                MODULE.safe_extract_archive(archive_path, destination)

        self.assertFalse(destination.exists())

    def test_safe_extract_rejects_same_inode_same_size_content_rewrite(self) -> None:
        archive_path = self.build_private_package()
        destination = self.root / "content-race-extract"
        real_identity = MODULE._release_tree_identity_from_directory_fd
        identity_calls = 0
        raced_path: Path | None = None
        original_identity: tuple[int, int] | None = None
        replacement_byte = b""

        def rewrite_after_identity(
            root_fd,
            display_root,
            *,
            require_sanitized_modes=False,
        ):
            nonlocal identity_calls, raced_path, original_identity, replacement_byte
            identity = real_identity(
                root_fd,
                display_root,
                require_sanitized_modes=require_sanitized_modes,
            )
            identity_calls += 1
            if identity_calls == 1:
                raced_path = display_root / "personal_codex" / "AGENTS.md"
                before = raced_path.stat()
                original_identity = before.st_ino, before.st_size
                file_descriptor = os.open(raced_path, os.O_RDWR)
                try:
                    original_byte = os.read(file_descriptor, 1)
                    replacement_byte = b"X" if original_byte != b"X" else b"Y"
                    os.lseek(file_descriptor, 0, os.SEEK_SET)
                    os.write(file_descriptor, replacement_byte)
                    os.fsync(file_descriptor)
                finally:
                    os.close(file_descriptor)
                after = raced_path.stat()
                self.assertEqual((after.st_ino, after.st_size), original_identity)
            return identity

        with mock.patch.object(
            MODULE,
            "_release_tree_identity_from_directory_fd",
            side_effect=rewrite_after_identity,
        ):
            with self.assertRaisesRegex(MODULE.SyncError, "file changed during validation"):
                MODULE.safe_extract_archive(archive_path, destination)

        self.assertEqual(identity_calls, 1)
        self.assertIsNotNone(raced_path)
        self.assertEqual(raced_path.read_bytes()[:1], replacement_byte)

    def test_safe_extract_rejects_normalized_member_aliases(self) -> None:
        for name, member_name in (
            ("empty", "personal-codex//payload.txt"),
            ("current", "personal-codex/./payload.txt"),
        ):
            with self.subTest(name=name):
                archive_path = self.root / f"unsafe-{name}.tar.gz"
                with tarfile.open(archive_path, "w:gz") as archive:
                    data = b"bad"
                    member = tarfile.TarInfo(member_name)
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))

                destination = self.root / f"unsafe-{name}-extract"
                with self.assertRaisesRegex(
                    MODULE.SyncError,
                    "unsafe archive member path",
                ):
                    MODULE.safe_extract_archive(archive_path, destination)
                self.assertFalse(destination.exists())

    def test_safe_extract_rejects_pax_path_with_embedded_nul_before_writing(
        self,
    ) -> None:
        archive_path = self.root / "nul-path.tar.gz"
        with tarfile.open(
            archive_path,
            "w:gz",
            format=tarfile.PAX_FORMAT,
        ) as archive:
            payload = b"payload"
            member = tarfile.TarInfo("placeholder.txt")
            member.pax_headers = {"path": "personal-codex/nul\0payload.txt"}
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
        destination = self.root / "nul-path-extract"

        with self.assertRaisesRegex(
            MODULE.SyncError,
            "unsafe archive member path: embedded NUL",
        ):
            MODULE.safe_extract_archive(archive_path, destination)

        self.assertFalse(destination.exists())

    def test_safe_extract_rejects_member_path_limits_before_writing(self) -> None:
        wide_component = "\N{LATIN SMALL LETTER E WITH ACUTE}" * 120
        cases = (
            (
                "path-bytes",
                "root/" + "/".join([wide_component] * 18),
                "path exceeds UTF-8 byte limit",
            ),
            (
                "component-bytes",
                "root/" + "\N{CJK UNIFIED IDEOGRAPH-754C}" * 86,
                "component exceeds UTF-8 byte limit",
            ),
            (
                "depth",
                "/".join(["root", *(["d"] * MODULE.MAX_ARCHIVE_MEMBER_PATH_DEPTH)]),
                "path exceeds depth limit",
            ),
        )
        for name, member_name, expected in cases:
            with self.subTest(limit=name):
                archive_path = self.root / f"path-limit-{name}.tar.gz"
                with tarfile.open(
                    archive_path,
                    "w:gz",
                    format=tarfile.PAX_FORMAT,
                ) as archive:
                    payload = b"payload"
                    member = tarfile.TarInfo(member_name)
                    member.size = len(payload)
                    archive.addfile(member, io.BytesIO(payload))
                destination = self.root / f"path-limit-{name}-extract"
                with contextlib.ExitStack() as stack:
                    if name == "depth":
                        stack.enter_context(
                            mock.patch.object(
                                MODULE,
                                "PurePosixPath",
                                side_effect=AssertionError(
                                    "path object created before depth validation"
                                ),
                            )
                        )
                    with self.assertRaisesRegex(MODULE.SyncError, expected):
                        MODULE.safe_extract_archive(archive_path, destination)
                self.assertFalse(destination.exists())

    def test_archive_member_path_limits_accept_boundaries_and_shared_prefixes(
        self,
    ) -> None:
        boundary_component = "\N{CJK UNIFIED IDEOGRAPH-754C}" * 85
        boundary_name = f"root/{boundary_component}"
        boundary_member = tarfile.TarInfo(boundary_name)
        with (
            mock.patch.object(
                MODULE,
                "MAX_ARCHIVE_MEMBER_PATH_BYTES",
                len(boundary_name.encode("utf-8")),
            ),
            mock.patch.object(
                MODULE,
                "MAX_ARCHIVE_MEMBER_COMPONENT_BYTES",
                len(boundary_component.encode("utf-8")),
            ),
            mock.patch.object(
                MODULE,
                "MAX_ARCHIVE_MEMBER_PATH_DEPTH",
                len(boundary_name.split("/")),
            ),
        ):
            MODULE._validate_tar_member(boundary_member)
            MODULE._validate_archive_member_paths([boundary_member])

        shared_prefix_members = []
        for index in range(512):
            member = tarfile.TarInfo(
                f"personal-codex/shared/prefix/file-{index:04d}.txt"
            )
            MODULE._validate_tar_member(member)
            shared_prefix_members.append(member)
        MODULE._validate_archive_member_paths(shared_prefix_members)
        self.assertEqual(len(shared_prefix_members), 512)

    def test_archive_member_paths_bound_implicit_directories(self) -> None:
        members = [
            tarfile.TarInfo("root/personal_codex/sync-manifest.json"),
            tarfile.TarInfo("root/a/b/c/file.txt"),
        ]

        with mock.patch.object(MODULE, "MAX_ARCHIVE_MEMBERS", 7):
            MODULE._validate_archive_member_paths(members)
        with (
            mock.patch.object(MODULE, "MAX_ARCHIVE_MEMBERS", 6),
            self.assertRaisesRegex(MODULE.SyncError, "path entry limit"),
        ):
            MODULE._validate_archive_member_paths(members)

    def test_safe_extract_rejects_implicit_directory_limit_before_writing(
        self,
    ) -> None:
        archive_path = self.root / "implicit-directory-limit.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            for member_name in (
                "root/personal_codex/sync-manifest.json",
                "root/a/b/c/file.txt",
            ):
                payload = b"x"
                member = tarfile.TarInfo(member_name)
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
        destination = self.root / "implicit-directory-limit-extract"

        with (
            mock.patch.object(MODULE, "MAX_ARCHIVE_MEMBERS", 6),
            mock.patch.object(
                MODULE,
                "_create_archive_destination",
                side_effect=AssertionError("destination must not be created"),
            ) as create_destination,
            self.assertRaisesRegex(MODULE.SyncError, "path entry limit"),
        ):
            MODULE.safe_extract_archive(archive_path, destination)

        create_destination.assert_not_called()
        self.assertFalse(destination.exists())

    def test_validate_tar_member_allows_one_directory_trailing_slash(self) -> None:
        member = tarfile.TarInfo("personal-codex/")
        member.type = tarfile.DIRTYPE

        MODULE._validate_tar_member(member)

        self.assertEqual(member.name, "personal-codex")

    def test_safe_extract_rejects_portable_path_conflicts_before_writing(
        self,
    ) -> None:
        cases = {
            "case-file": (
                "personal-codex/Foo.txt",
                "personal-codex/foo.txt",
            ),
            "unicode-file": (
                "personal-codex/caf\N{LATIN SMALL LETTER E WITH ACUTE}.txt",
                "personal-codex/cafe\N{COMBINING ACUTE ACCENT}.txt",
            ),
            "case-directory": (
                "personal-codex/Foo/one.txt",
                "personal-codex/foo/two.txt",
            ),
            "unicode-directory": (
                "personal-codex/caf\N{LATIN SMALL LETTER E WITH ACUTE}/one.txt",
                "personal-codex/cafe\N{COMBINING ACUTE ACCENT}/two.txt",
            ),
            "file-directory": (
                "personal-codex/Thing",
                "personal-codex/thing/child.txt",
            ),
        }
        for name, member_names in cases.items():
            with self.subTest(case=name):
                archive_path = self.root / f"portable-{name}.tar.gz"
                with tarfile.open(archive_path, "w:gz") as archive:
                    for member_name in member_names:
                        data = b"payload"
                        member = tarfile.TarInfo(member_name)
                        member.size = len(data)
                        archive.addfile(member, io.BytesIO(data))

                destination = self.root / f"portable-{name}-extract"
                with self.assertRaisesRegex(
                    MODULE.SyncError,
                    "portable archive member path conflict",
                ):
                    MODULE.safe_extract_archive(archive_path, destination)

                self.assertFalse(destination.exists())

    def test_safe_extract_rejects_preexisting_symlink_destination(self) -> None:
        archive_path = self.build_private_package()
        outside = self.root / "outside-extract"
        outside.mkdir()
        destination = self.root / "preexisting-extract"
        destination.symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(
            MODULE.SyncError,
            "pre-existing archive destination",
        ):
            MODULE.safe_extract_archive(archive_path, destination)

        self.assertTrue(destination.is_symlink())
        self.assertEqual(list(outside.iterdir()), [])

    def test_safe_extract_destination_swap_does_not_write_redirected_tree(self) -> None:
        archive_path = self.build_private_package()
        destination = self.root / "destination-swap-extract"
        moved_destination = self.root / "destination-swap-bound"
        redirected = self.root / "destination-swap-redirected"
        redirected.mkdir()
        sentinel = redirected / "sentinel.txt"
        sentinel.write_text("keep\n", encoding="utf-8")
        original_extract = MODULE._extract_archive_members

        def swap_destination(archive, destination_fd, members):
            destination.rename(moved_destination)
            destination.symlink_to(redirected, target_is_directory=True)
            return original_extract(archive, destination_fd, members)

        with mock.patch.object(
            MODULE,
            "_extract_archive_members",
            swap_destination,
        ):
            with self.assertRaisesRegex(MODULE.SyncError, "destination changed"):
                MODULE.safe_extract_archive(archive_path, destination)

        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")
        self.assertEqual(list(redirected.iterdir()), [sentinel])
        self.assertTrue(
            (
                moved_destination
                / f"personal-codex-{PRIVATE_SHA}"
                / "personal_codex"
                / "sync-manifest.json"
            ).is_file()
        )

    def test_safe_extract_parent_swap_does_not_create_in_redirected_parent(self) -> None:
        archive_path = self.build_private_package()
        parent = self.root / "parent-swap-parent"
        parent.mkdir()
        moved_parent = self.root / "parent-swap-bound"
        redirected = self.root / "parent-swap-redirected"
        redirected.mkdir()
        sentinel = redirected / "sentinel.txt"
        sentinel.write_text("keep\n", encoding="utf-8")
        destination = parent / "extract"
        original_create_directory = MODULE._create_archive_directory_at
        swapped = False

        def swap_parent(parent_fd, name):
            nonlocal swapped
            if not swapped:
                swapped = True
                parent.rename(moved_parent)
                parent.symlink_to(redirected, target_is_directory=True)
            return original_create_directory(parent_fd, name)

        with mock.patch.object(
            MODULE,
            "_create_archive_directory_at",
            swap_parent,
        ):
            with self.assertRaisesRegex(MODULE.SyncError, "parent changed"):
                MODULE.safe_extract_archive(archive_path, destination)

        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")
        self.assertEqual(list(redirected.iterdir()), [sentinel])
        self.assertTrue((moved_parent / "extract").is_dir())

    def test_safe_extract_preserves_concurrent_expected_leaves(self) -> None:
        archive_path = self.build_private_package()
        original_rename = MODULE._rename_noreplace_at
        for kind in ("regular", "symlink", "directory"):
            with self.subTest(kind=kind):
                destination = self.root / f"leaf-race-{kind}-extract"
                outside = self.root / f"leaf-race-{kind}-outside.txt"
                outside.write_text("outside\n", encoding="utf-8")
                inserted = False

                def insert_expected_leaf(
                    source_parent_fd,
                    source_name,
                    destination_parent_fd,
                    destination_name,
                ):
                    nonlocal inserted
                    if destination_name == "sync-manifest.json" and not inserted:
                        inserted = True
                        if kind == "regular":
                            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                            existing_fd = os.open(
                                destination_name,
                                flags,
                                0o600,
                                dir_fd=destination_parent_fd,
                            )
                            try:
                                os.write(existing_fd, b"concurrent\n")
                            finally:
                                os.close(existing_fd)
                        elif kind == "symlink":
                            os.symlink(
                                outside,
                                destination_name,
                                dir_fd=destination_parent_fd,
                            )
                        else:
                            os.mkdir(destination_name, dir_fd=destination_parent_fd)
                    return original_rename(
                        source_parent_fd,
                        source_name,
                        destination_parent_fd,
                        destination_name,
                    )

                with mock.patch.object(
                    MODULE,
                    "_rename_noreplace_at",
                    insert_expected_leaf,
                ):
                    with self.assertRaisesRegex(MODULE.SyncError, "entry already exists"):
                        MODULE.safe_extract_archive(archive_path, destination)

                existing = (
                    destination
                    / f"personal-codex-{PRIVATE_SHA}"
                    / "personal_codex"
                    / "sync-manifest.json"
                )
                self.assertTrue(inserted)
                self.assertEqual(outside.read_text(encoding="utf-8"), "outside\n")
                if kind == "regular":
                    self.assertEqual(
                        existing.read_text(encoding="utf-8"),
                        "concurrent\n",
                    )
                elif kind == "symlink":
                    self.assertTrue(existing.is_symlink())
                    self.assertEqual(os.readlink(existing), str(outside))
                else:
                    self.assertTrue(existing.is_dir())
                    self.assertEqual(list(existing.iterdir()), [])

    def test_safe_extract_does_not_use_extractall(self) -> None:
        archive_path = self.build_private_package()
        destination = self.root / "descriptor-extract"
        with mock.patch.object(
            tarfile.TarFile,
            "extractall",
            side_effect=AssertionError("extractall must not be used"),
        ) as extractall:
            release_root = MODULE.safe_extract_archive(archive_path, destination)

        extractall.assert_not_called()
        self.assertTrue(
            (release_root / "personal_codex" / "sync-manifest.json").is_file()
        )
        self.assertEqual(destination.stat().st_mode & 0o777, 0o700)

    def test_private_overlay_installs_over_public_base_and_verifies(self) -> None:
        public_release = self.root / "public-release"
        home = self.root / "home" / ".codex"
        write_public_base_fixture(public_release)
        private_release = MODULE.safe_extract_archive(
            self.build_private_package(),
            self.root / "private-extract",
        )

        self.run_quietly(
            MODULE.install_release_tree,
            public_release,
            home,
            PUBLIC_SHA,
            dry_run=False,
        )
        self.run_quietly(
            MODULE.install_release_tree,
            private_release,
            home,
            PRIVATE_SHA,
            dry_run=False,
        )

        self.assertTrue((home / "bin" / "codex-personal-sync").is_symlink())
        self.assertTrue((home / "AGENTS.md").is_symlink())
        self.assertTrue((home / "skills" / "cisco-trackers-lookup").is_symlink())
        self.run_quietly(MODULE.verify_overlay, home, "private")

    def test_install_private_downloads_public_base_and_overlay(self) -> None:
        public_release = self.root / "public-release"
        home = self.root / "home" / ".codex"
        write_public_base_fixture(public_release)
        private_release = MODULE.safe_extract_archive(
            self.build_private_package(),
            self.root / "private-extract",
        )
        downloads: list[tuple[str, str | None]] = []

        def fake_download(repo: str, destination: Path, *, sha: str | None = None):
            downloads.append((repo, sha))
            if repo == "Joey-Tools/codex-private-workflows":
                return MODULE.DownloadedRelease(
                    repo=repo,
                    assets=MODULE.ReleaseAssets(
                        tag_name="personal-codex-20260520-120000-2222222",
                        sha=PRIVATE_SHA,
                        archive_name=f"personal-codex-{PRIVATE_SHA}.tar.gz",
                        checksum_name=f"personal-codex-{PRIVATE_SHA}.sha256",
                        archive_id=1,
                        archive_size=1,
                        checksum_id=2,
                        checksum_size=1,
                    ),
                    release_root=private_release,
                )
            if repo == "Joey-Tools/codex-toolbox":
                return MODULE.DownloadedRelease(
                    repo=repo,
                    assets=MODULE.ReleaseAssets(
                        tag_name="personal-codex-20260520-120000-1111111",
                        sha=PUBLIC_SHA,
                        archive_name=f"personal-codex-{PUBLIC_SHA}.tar.gz",
                        checksum_name=f"personal-codex-{PUBLIC_SHA}.sha256",
                        archive_id=1,
                        archive_size=1,
                        checksum_id=2,
                        checksum_size=1,
                    ),
                    release_root=public_release,
                )
            raise AssertionError(f"unexpected repo: {repo}")

        with mock.patch.object(MODULE, "download_and_extract_release", fake_download):
            self.run_quietly(
                MODULE.install_private_from_github,
                "Joey-Tools/codex-private-workflows",
                home,
                base_repo="Fallback/base",
                owner="private",
                dry_run=False,
            )

        self.assertEqual(
            downloads,
            [
                ("Joey-Tools/codex-private-workflows", None),
                ("Joey-Tools/codex-toolbox", None),
            ],
        )
        self.assertTrue((home / "bin" / "codex-personal-sync").is_symlink())
        self.assertTrue((home / "AGENTS.md").is_symlink())
        self.run_quietly(MODULE.verify_overlay, home, "private")

    def test_install_private_uses_validated_base_release_spec(self) -> None:
        public_release = self.root / "public-release"
        home = self.root / "home" / ".codex"
        write_public_base_fixture(public_release)
        private_release = MODULE.safe_extract_archive(
            self.build_private_package(),
            self.root / "private-extract",
        )
        manifest_path = private_release / "personal_codex" / "sync-manifest.json"
        original_manifest = manifest_path.read_bytes()
        downloads: list[tuple[str, str | None]] = []
        overlay_validated = False
        real_validate = MODULE._validate_release_manifest_owner

        def validate_then_replace_manifest(
            release_root: Path,
            expected_owner: str,
            release_expectation: MODULE.ReleaseTreeExpectation | None = None,
        ):
            nonlocal overlay_validated
            manifest = real_validate(
                release_root,
                expected_owner,
                release_expectation,
            )
            if expected_owner == "private":
                payload = json.loads(original_manifest.decode("utf-8"))
                payload["base_release"] = {"repo": "Attacker/alternate-base"}
                manifest_path.write_text(
                    json.dumps(payload) + "\n",
                    encoding="utf-8",
                )
                overlay_validated = True
            return manifest

        def fake_download(repo: str, destination: Path, *, sha: str | None = None):
            downloads.append((repo, sha))
            if repo == "Joey-Tools/codex-private-workflows":
                return MODULE.DownloadedRelease(
                    repo=repo,
                    assets=MODULE.ReleaseAssets(
                        tag_name="personal-codex-20260520-120000-2222222",
                        sha=PRIVATE_SHA,
                        archive_name=f"personal-codex-{PRIVATE_SHA}.tar.gz",
                        checksum_name=f"personal-codex-{PRIVATE_SHA}.sha256",
                        archive_id=1,
                        archive_size=1,
                        checksum_id=2,
                        checksum_size=1,
                    ),
                    release_root=private_release,
                )
            if overlay_validated:
                manifest_path.write_bytes(original_manifest)
            if repo == "Joey-Tools/codex-toolbox":
                return MODULE.DownloadedRelease(
                    repo=repo,
                    assets=MODULE.ReleaseAssets(
                        tag_name="personal-codex-20260520-120000-1111111",
                        sha=PUBLIC_SHA,
                        archive_name=f"personal-codex-{PUBLIC_SHA}.tar.gz",
                        checksum_name=f"personal-codex-{PUBLIC_SHA}.sha256",
                        archive_id=1,
                        archive_size=1,
                        checksum_id=2,
                        checksum_size=1,
                    ),
                    release_root=public_release,
                )
            raise AssertionError(f"unexpected repo: {repo}")

        with (
            mock.patch.object(MODULE, "download_and_extract_release", fake_download),
            mock.patch.object(
                MODULE,
                "_validate_release_manifest_owner",
                side_effect=validate_then_replace_manifest,
            ),
        ):
            self.run_quietly(
                MODULE.install_private_from_github,
                "Joey-Tools/codex-private-workflows",
                home,
                base_repo="Fallback/base",
                owner="private",
                dry_run=False,
            )

        self.assertTrue(overlay_validated)
        self.assertEqual(
            downloads,
            [
                ("Joey-Tools/codex-private-workflows", None),
                ("Joey-Tools/codex-toolbox", None),
            ],
        )
        self.assertEqual(manifest_path.read_bytes(), original_manifest)

    def test_private_scheduler_invokes_private_install_entrypoint(self) -> None:
        home = self.root / "home" / ".codex"
        args = MODULE._scheduler_install_args(
            Path("/runner"),
            "Joey-Tools/codex-private-workflows",
            home,
            mode="private",
            base_repo="Joey-Tools/codex-toolbox",
            owner="private",
        )

        self.assertEqual(
            args,
            [
                "/runner",
                "install-private",
                "--repo",
                "Joey-Tools/codex-private-workflows",
                "--base-repo",
                "Joey-Tools/codex-toolbox",
                "--owner",
                "private",
                "--home",
                str(home),
            ],
        )

    def test_install_scheduler_no_enable_keeps_legacy_macos_plist(self) -> None:
        user_home = self.root / "home"
        home = user_home / ".codex"
        write_scheduler_runner(home)
        legacy_plist = (
            user_home
            / "Library"
            / "LaunchAgents"
            / f"{MODULE.LEGACY_LAUNCHD_LABELS[0]}.plist"
        )
        legacy_plist.parent.mkdir(parents=True)
        legacy_plist.write_text("legacy\n", encoding="utf-8")

        with mock.patch.object(MODULE.Path, "home", return_value=user_home):
            self.run_quietly(
                MODULE.install_scheduler,
                home,
                "owner/repo",
                60,
                "macos",
                None,
                dry_run=False,
                enable=False,
            )

        self.assertTrue(legacy_plist.exists())

    def test_uninstall_scheduler_no_disable_removes_legacy_macos_plist(self) -> None:
        user_home = self.root / "home"
        home = user_home / ".codex"
        legacy_plist = (
            user_home
            / "Library"
            / "LaunchAgents"
            / f"{MODULE.LEGACY_LAUNCHD_LABELS[0]}.plist"
        )
        legacy_plist.parent.mkdir(parents=True)
        legacy_plist.write_text("legacy\n", encoding="utf-8")

        with mock.patch.object(MODULE.Path, "home", return_value=user_home):
            self.run_quietly(
                MODULE.uninstall_scheduler,
                home,
                "macos",
                dry_run=False,
                disable=False,
            )

        self.assertFalse(legacy_plist.exists())

    def test_private_rollback_is_rejected(self) -> None:
        home = self.root / "home" / ".codex"

        with self.assertRaisesRegex(MODULE.SyncError, "only public releases"):
            self.run_quietly(MODULE.rollback, home, None, "private")


class PrivateAutomationPromptTests(unittest.TestCase):
    def test_daily_work_report_bounds_memory_reads(self) -> None:
        prompt = automation_prompt("daily-work-report-draft")

        self.assertIn("When reading this automation's memory", prompt)
        self.assertIn("do not dump the whole file or a fixed 200-line head", prompt)
        self.assertIn(
            "widen only when needed for the candidate-day calculation", prompt
        )

    def test_daily_skill_friction_bounds_memory_reads(self) -> None:
        prompt = automation_prompt("daily-skill-friction")

        self.assertIn("When reading this automation's memory", prompt)
        self.assertIn("structured parser or narrowly bounded extraction", prompt)
        self.assertIn("latest completed-run End timestamp", prompt)

    def test_daily_skill_friction_scans_active_and_archived_rollouts(self) -> None:
        prompt = automation_prompt("daily-skill-friction")

        self.assertIn("both `~/.codex/sessions` and `~/.codex/archived_sessions`", prompt)
        self.assertIn("dated `YYYY/MM/DD/rollout-*.jsonl` directories", prompt)
        self.assertIn("flat `archived_sessions/rollout-*.jsonl` layouts", prompt)
        self.assertIn(
            "rollout lifecycle identity and normalized content fingerprint",
            prompt,
        )
        self.assertIn(
            "In the final report, state the active, archived, and union candidate, "
            "parsed, and accepted counts, plus the cross-root duplicate groups, "
            "duplicate rollouts collapsed, and replayed-prefix record counts produced "
            "by the session corpus helper.",
            prompt,
        )
        self.assertIn(
            "do not discard later human follow-up turns in the same thread solely "
            "because the rollout began with the automation wrapper",
            prompt,
        )


if __name__ == "__main__":
    unittest.main()
