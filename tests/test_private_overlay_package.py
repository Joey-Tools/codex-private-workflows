from __future__ import annotations

import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


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


class PrivateOverlayPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="codex-private-overlay.")
        self.root = Path(self.tmpdir.name)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def run_quietly(self, callback, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return callback(*args, **kwargs)

    def build_private_package(self) -> Path:
        dist_dir = self.root / "dist"
        subprocess.run(
            [
                sys.executable,
                str(PACKAGE_SCRIPT),
                "--repo-root",
                str(REPO_ROOT),
                "--manifest",
                "personal_codex/private-sync-manifest.json",
                "--sha",
                PRIVATE_SHA,
                "--output-dir",
                str(dist_dir),
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        return dist_dir / f"personal-codex-{PRIVATE_SHA}.tar.gz"

    def test_private_manifest_packages_overlay_targets(self) -> None:
        archive_path = self.build_private_package()
        extract_root = self.root / "extract"
        release_root = MODULE.safe_extract_archive(archive_path, extract_root)
        entries = MODULE.validate_release_tree(release_root)
        targets = {entry.target.as_posix(): entry for entry in entries}

        self.assertTrue(all(entry.owner == "private" for entry in entries))
        self.assertIn("AGENTS.md", targets)
        self.assertIn("skills/cisco-trackers-lookup", targets)
        self.assertIn("skills/remote-host-context", targets)
        self.assertIn("skills/apple-notes-work-report", targets)
        self.assertNotIn("bin/codex-personal-sync", targets)

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


if __name__ == "__main__":
    unittest.main()
