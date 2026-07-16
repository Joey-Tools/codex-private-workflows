from __future__ import annotations

import contextlib
import hmac
import importlib.util
import io
import json
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
    manifest_root = root / "personal_codex"
    manifest_root.mkdir(parents=True)
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
        subprocess.run(args, check=True, text=True, capture_output=True)
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
        generated_catalog = (
            repo_root
            / "personal_codex"
            / "skills"
            / "review-orchestration-playbook"
            / "scripts"
            / "review_runtime"
            / "synthetic-token-catalog.json"
        )
        generated_catalog.parent.mkdir(parents=True)
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


if __name__ == "__main__":
    unittest.main()
