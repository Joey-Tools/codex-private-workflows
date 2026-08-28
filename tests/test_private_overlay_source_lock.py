from __future__ import annotations

import contextlib
from dataclasses import replace
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "private_overlay_source_lock.py"
SPEC = importlib.util.spec_from_file_location(
    "private_overlay_source_lock_tests_subject",
    SCRIPT_PATH,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("failed to load private overlay source-lock verifier")
SOURCE_LOCK = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SOURCE_LOCK
SPEC.loader.exec_module(SOURCE_LOCK)


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=False) + "\n").encode("utf-8")


def _copy_generated_managed_tree(destination_root: Path) -> None:
    receipt = json.loads((REPO_ROOT / SOURCE_LOCK.GENERATED_RECEIPT_PATH).read_bytes())
    for entry in receipt["files"]:
        relative_path = Path(entry["target_path"])
        destination = destination_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / relative_path, destination)
        destination.chmod(int(entry["mode"], 8))


class SourceLockContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix="private-overlay-source-lock-contract."
        )
        self.root = Path(self.temporary_directory.name).resolve()
        (self.root / "scripts").mkdir()
        (self.root / "personal_codex").mkdir()
        for relative_path in (
            SOURCE_LOCK.LOCK_PATH,
            SOURCE_LOCK.PRIVATE_MANIFEST_PATH,
            SOURCE_LOCK.PRIVATE_RELEASE_SCRIPT_PATH,
            Path("generated-sync-source-lock.json"),
        ):
            destination = self.root / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO_ROOT / relative_path, destination)
        (self.root / SOURCE_LOCK.GENERATED_RECEIPT_PATH).chmod(0o644)
        _copy_generated_managed_tree(self.root)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _read_lock_payload(self) -> dict:
        return json.loads((self.root / SOURCE_LOCK.LOCK_PATH).read_bytes())

    def _write_lock_payload(self, payload: dict, *, canonical: bool = True) -> None:
        encoded = (
            _canonical_json(payload)
            if canonical
            else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        )
        (self.root / SOURCE_LOCK.LOCK_PATH).write_bytes(encoded)

    def test_current_lock_is_canonical_and_binds_all_base_release_surfaces(
        self,
    ) -> None:
        source_lock = SOURCE_LOCK.load_source_lock(REPO_ROOT)

        SOURCE_LOCK.validate_base_release_binding(REPO_ROOT, source_lock)

        self.assertEqual(
            tuple((pin.name, pin.repository) for pin in source_lock.pins),
            SOURCE_LOCK.EXPECTED_SOURCES,
        )
        raw = (REPO_ROOT / SOURCE_LOCK.LOCK_PATH).read_bytes()
        self.assertEqual(source_lock.digest, hashlib.sha256(raw).hexdigest())
        self.assertEqual(
            source_lock.pins[0].sha,
            "255372d2b0dd96f39faf1e52a9168ca2aa7ece69",
        )

    def test_rejects_unexpected_root_and_entry_fields(self) -> None:
        payload = self._read_lock_payload()
        payload["unexpected"] = True
        self._write_lock_payload(payload)
        with self.assertRaisesRegex(
            SOURCE_LOCK.SourceLockError,
            "root fields",
        ):
            SOURCE_LOCK.load_source_lock(self.root)

        payload = json.loads((REPO_ROOT / SOURCE_LOCK.LOCK_PATH).read_bytes())
        payload["sources"][0]["unexpected"] = True
        self._write_lock_payload(payload)
        with self.assertRaisesRegex(
            SOURCE_LOCK.SourceLockError,
            "entry 0 fields",
        ):
            SOURCE_LOCK.load_source_lock(self.root)

    def test_rejects_source_order_drift(self) -> None:
        payload = self._read_lock_payload()
        payload["sources"][0], payload["sources"][1] = (
            payload["sources"][1],
            payload["sources"][0],
        )
        self._write_lock_payload(payload)

        with self.assertRaisesRegex(SOURCE_LOCK.SourceLockError, "wrong name"):
            SOURCE_LOCK.load_source_lock(self.root)

    def test_rejects_noncanonical_json(self) -> None:
        payload = self._read_lock_payload()
        self._write_lock_payload(payload, canonical=False)

        with self.assertRaisesRegex(SOURCE_LOCK.SourceLockError, "canonical JSON"):
            SOURCE_LOCK.load_source_lock(self.root)

    def test_rejects_private_manifest_base_release_mismatch(self) -> None:
        source_lock = SOURCE_LOCK.load_source_lock(self.root)
        manifest_path = self.root / SOURCE_LOCK.PRIVATE_MANIFEST_PATH
        manifest = json.loads(manifest_path.read_bytes())
        manifest["base_release"]["sha"] = "1" * 40
        manifest_path.write_bytes(_canonical_json(manifest))

        with self.assertRaisesRegex(
            SOURCE_LOCK.SourceLockError,
            "base identity differ",
        ):
            SOURCE_LOCK.validate_base_release_binding(self.root, source_lock)

    def test_rejects_release_verifier_base_release_mismatch(self) -> None:
        source_lock = SOURCE_LOCK.load_source_lock(self.root)
        release_path = self.root / SOURCE_LOCK.PRIVATE_RELEASE_SCRIPT_PATH
        release_source = release_path.read_text(encoding="utf-8")
        release_source = release_source.replace(
            source_lock.pins[0].sha,
            "2" * 40,
            1,
        )
        release_path.write_text(release_source, encoding="utf-8")

        with self.assertRaisesRegex(
            SOURCE_LOCK.SourceLockError,
            "base identity differ",
        ):
            SOURCE_LOCK.validate_base_release_binding(self.root, source_lock)

    def test_emit_github_outputs_contains_each_pin_and_lock_digest(self) -> None:
        source_lock = SOURCE_LOCK.load_source_lock(REPO_ROOT)
        captured = io.StringIO()

        with contextlib.redirect_stdout(captured):
            SOURCE_LOCK.emit_github_outputs(source_lock)

        self.assertEqual(
            captured.getvalue().splitlines(),
            [
                line
                for pin in source_lock.pins
                for line in (
                    f"{pin.name.replace('-', '_')}_sha={pin.sha}",
                    f"{pin.name.replace('-', '_')}_tree={pin.tree}",
                )
            ]
            + [f"source_lock_sha256={source_lock.digest}"],
        )

    def test_cli_rejects_generated_receipt_byte_mismatch(self) -> None:
        receipt_path = self.root / "generated-sync-source-lock.json"
        receipt_path.write_bytes(receipt_path.read_bytes() + b"\n")
        captured = io.StringIO()

        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(captured),
        ):
            result = SOURCE_LOCK.main(
                ["emit-github-outputs", "--repo-root", os.fspath(self.root)]
            )

        self.assertEqual(result, 1)
        self.assertIn("receipt", captured.getvalue())

    def test_cli_rejects_generated_receipt_repository_mismatch(self) -> None:
        receipt_path = self.root / "generated-sync-source-lock.json"
        receipt = json.loads(receipt_path.read_bytes())
        receipt["canonical_repository"] = "Attacker/alternate-source"
        receipt_bytes = _canonical_json(receipt)
        receipt_path.write_bytes(receipt_bytes)
        lock_payload = self._read_lock_payload()
        lock_payload["toolbox_generated_provenance"]["receipt_sha256"] = hashlib.sha256(
            receipt_bytes
        ).hexdigest()
        self._write_lock_payload(lock_payload)
        captured = io.StringIO()

        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(captured),
        ):
            result = SOURCE_LOCK.main(
                ["emit-github-outputs", "--repo-root", os.fspath(self.root)]
            )

        self.assertEqual(result, 1)
        self.assertIn("provenance", captured.getvalue())

    def test_cli_rejects_generated_receipt_commit_mismatch(self) -> None:
        receipt_path = self.root / "generated-sync-source-lock.json"
        receipt = json.loads(receipt_path.read_bytes())
        receipt["canonical_commit"] = "3" * 40
        receipt_bytes = _canonical_json(receipt)
        receipt_path.write_bytes(receipt_bytes)
        lock_payload = self._read_lock_payload()
        lock_payload["toolbox_generated_provenance"]["receipt_sha256"] = hashlib.sha256(
            receipt_bytes
        ).hexdigest()
        self._write_lock_payload(lock_payload)
        captured = io.StringIO()

        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(captured),
        ):
            result = SOURCE_LOCK.main(
                ["emit-github-outputs", "--repo-root", os.fspath(self.root)]
            )

        self.assertEqual(result, 1)
        self.assertIn("provenance", captured.getvalue())

    def test_rejects_private_generated_managed_file_mode_mismatch(self) -> None:
        source_lock = SOURCE_LOCK.load_source_lock(self.root)
        receipt = json.loads(
            (self.root / SOURCE_LOCK.GENERATED_RECEIPT_PATH).read_bytes()
        )
        managed_path = self.root / receipt["files"][0]["target_path"]
        managed_path.chmod(0o600)

        with self.assertRaisesRegex(
            SOURCE_LOCK.SourceLockError,
            "managed path mode differs",
        ):
            SOURCE_LOCK.validate_generated_provenance(self.root, source_lock)


class CheckoutVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix="private-overlay-source-lock-checkouts."
        )
        self.root = Path(self.temporary_directory.name).resolve()
        self.source_root = self.root / "source"
        self.source_root.mkdir(mode=0o700)
        git = shutil.which("git")
        if git is None:
            self.fail("Git is required for source-lock tests")
        self.git_path = Path(git).resolve()
        self.git_environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
        }
        self.git_environment.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_TERMINAL_PROMPT": "0",
                "LC_ALL": "C",
            }
        )
        self.provenance = SOURCE_LOCK.load_source_lock(
            REPO_ROOT
        ).toolbox_generated_provenance
        pins = []
        for index, (name, repository) in enumerate(SOURCE_LOCK.EXPECTED_SOURCES):
            checkout = self.source_root / name
            checkout.mkdir(mode=0o700)
            self._git(checkout, "init", "--quiet", "--initial-branch=main")
            # Git 2.54 may detach automatic maintenance after a write command,
            # leaving a fixture checkout active when TemporaryDirectory cleanup
            # begins. Keep these short-lived repositories free of background
            # writers instead of weakening teardown validation with retries.
            self._git(checkout, "config", "maintenance.auto", "false")
            self._git(checkout, "config", "gc.auto", "0")
            self._git(checkout, "config", "user.name", "Source Lock Test")
            self._git(checkout, "config", "user.email", "source-lock@example.invalid")
            self._git(checkout, "config", "commit.gpgsign", "false")
            (checkout / "tracked.txt").write_text(
                f"source lock fixture {index}\n",
                encoding="utf-8",
            )
            (checkout / "tracked.txt").chmod(0o644)
            if index == 0:
                shutil.copyfile(
                    REPO_ROOT / SOURCE_LOCK.GENERATED_RECEIPT_PATH,
                    checkout / SOURCE_LOCK.GENERATED_RECEIPT_PATH,
                )
                (checkout / SOURCE_LOCK.GENERATED_RECEIPT_PATH).chmod(0o644)
                _copy_generated_managed_tree(checkout)
            self._git(checkout, "add", ".")
            self._git(checkout, "commit", "--quiet", "-m", "Create fixture")
            sha = self._git_stdout(checkout, "rev-parse", "HEAD")
            tree = self._git_stdout(checkout, "rev-parse", "HEAD^{tree}")
            self._git(checkout, "checkout", "--quiet", "--detach", sha)
            pins.append(
                SOURCE_LOCK.SourcePin(
                    name=name,
                    repository=repository,
                    sha=sha,
                    tree=tree,
                )
            )
        self.pins = pins

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @property
    def source_lock(self):
        return SOURCE_LOCK.SourceLock(
            pins=tuple(self.pins),
            toolbox_generated_provenance=self.provenance,
            digest="0" * 64,
        )

    def _git(self, checkout: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
        completed = subprocess.run(
            [os.fspath(self.git_path), "-C", os.fspath(checkout), *args],
            env=self.git_environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stderr.decode("utf-8", errors="replace"),
        )
        return completed

    def _git_stdout(self, checkout: Path, *args: str) -> str:
        return self._git(checkout, *args).stdout.decode("ascii").strip()

    def _checkout(self, index: int = 0) -> Path:
        return self.source_root / self.pins[index].name

    def _refresh_pin_from_checkout(self, index: int = 0) -> None:
        checkout = self._checkout(index)
        self.pins[index] = replace(
            self.pins[index],
            sha=self._git_stdout(checkout, "rev-parse", "HEAD"),
            tree=self._git_stdout(checkout, "rev-parse", "HEAD^{tree}"),
        )

    def _homebrew_git_fixture(self, *, executable_mode: int = 0o755) -> Path:
        prefix = self.root / "homebrew"
        bin_directory = prefix / "bin"
        cellar_directory = prefix / "Cellar" / "git" / "2.54.0" / "bin"
        bin_directory.mkdir(parents=True, mode=0o775)
        cellar_directory.mkdir(parents=True, mode=0o755)
        (prefix / "Cellar").chmod(0o775)
        bin_directory.chmod(0o775)
        executable = cellar_directory / "git"
        shutil.copyfile(self.git_path, executable)
        executable.chmod(executable_mode)
        entry = bin_directory / "git"
        try:
            entry.symlink_to("../Cellar/git/2.54.0/bin/git")
        except OSError as error:
            self.skipTest(f"platform cannot create the Git symlink fixture: {error}")
        return entry

    def test_accepts_five_complete_clean_detached_checkouts(self) -> None:
        SOURCE_LOCK.verify_checkouts(self.source_root, self.source_lock)

    def test_accepts_safe_checkout_control_file_access_policy(self) -> None:
        git_directory = self._checkout() / ".git"
        (git_directory / "HEAD").chmod(0o600)
        (git_directory / "config").chmod(0o644)

        receipt = SOURCE_LOCK.verify_checkouts(self.source_root, self.source_lock)

        self.assertEqual(receipt.checkouts[0].head_file.access_policy[1:], (0o600, 1))
        self.assertEqual(
            receipt.checkouts[0].local_config_file.access_policy[1:],
            (0o644, 1),
        )

    def test_rejects_writable_checkout_control_file_access_policy(self) -> None:
        git_directory = self._checkout() / ".git"
        for name, label in (("HEAD", "detached HEAD"), ("config", "local config")):
            target = git_directory / name
            original_mode = stat.S_IMODE(target.stat().st_mode)
            for unsafe_mode in (0o666, 0o620):
                with self.subTest(name=name, mode=oct(unsafe_mode)):
                    target.chmod(unsafe_mode)
                    try:
                        with self.assertRaisesRegex(
                            SOURCE_LOCK.SourceLockError,
                            rf"{label} receipt checkout control file access policy "
                            r"is unsafe; requires no group- or world-writable bits",
                        ):
                            SOURCE_LOCK.verify_checkouts(
                                self.source_root,
                                self.source_lock,
                            )
                    finally:
                        target.chmod(original_mode)

    def test_rejects_multi_link_checkout_control_file_access_policy(self) -> None:
        git_directory = self._checkout() / ".git"
        for name, label in (("HEAD", "detached HEAD"), ("config", "local config")):
            target = git_directory / name
            alias = self.root / f"{name.lower()}-control-file-alias"
            try:
                os.link(target, alias)
            except OSError as error:
                self.skipTest(f"platform cannot create a hard-link fixture: {error}")
            try:
                with self.subTest(name=name):
                    with self.assertRaisesRegex(
                        SOURCE_LOCK.SourceLockError,
                        rf"{label} receipt checkout control file access policy "
                        r"is unsafe; requires exactly one hard link",
                    ):
                        SOURCE_LOCK.verify_checkouts(
                            self.source_root,
                            self.source_lock,
                        )
            finally:
                alias.unlink()

    def test_rejects_fifo_checkout_control_file_without_a_writer(self) -> None:
        root_binding = SOURCE_LOCK._directory_binding(
            self.source_root,
            label="FIFO fixture source root",
        )
        verified_source = (tuple(self.pins), root_binding)
        real_open = os.open
        for name, label in (("HEAD", "detached HEAD"), ("config", "local config")):
            target = self._checkout() / ".git" / name
            regular = target.with_name(f"{name}.regular-fixture")
            target.rename(regular)
            try:
                os.mkfifo(target, mode=0o600)
            except OSError as error:
                regular.rename(target)
                self.skipTest(f"platform cannot create a FIFO fixture: {error}")
            opened_fifo = False

            def guarded_open(path, flags, *args, **kwargs):
                nonlocal opened_fifo
                if Path(path) == target:
                    self.assertTrue(flags & os.O_NONBLOCK)
                    opened_fifo = True
                return real_open(path, flags, *args, **kwargs)

            started = time.monotonic()
            try:
                with (
                    mock.patch.object(
                        SOURCE_LOCK,
                        "_verify_source_root",
                        return_value=verified_source,
                    ),
                    mock.patch.object(
                        SOURCE_LOCK.os,
                        "open",
                        side_effect=guarded_open,
                    ),
                    self.assertRaisesRegex(
                        SOURCE_LOCK.SourceLockError,
                        rf"{label} receipt is not a regular file",
                    ),
                ):
                    SOURCE_LOCK.verify_checkouts(
                        self.source_root,
                        self.source_lock,
                    )
                self.assertTrue(opened_fifo)
                self.assertLess(time.monotonic() - started, 5.0)
            finally:
                target.unlink()
                regular.rename(target)

    def test_bounded_read_rejects_in_flight_mode_policy_drift(self) -> None:
        target = self.root / "mode-drift-control-file"
        target.write_bytes(b"control\n")
        real_fstat = os.fstat
        for initial_mode, final_mode in ((0o600, 0o620), (0o620, 0o600)):
            with self.subTest(initial=oct(initial_mode), final=oct(final_mode)):
                target.chmod(initial_mode)
                calls = 0

                def mutate_after_first_fstat(descriptor):
                    nonlocal calls
                    metadata = real_fstat(descriptor)
                    calls += 1
                    if calls == 1:
                        target.chmod(final_mode)
                    return metadata

                with (
                    mock.patch.object(
                        SOURCE_LOCK.os,
                        "fstat",
                        side_effect=mutate_after_first_fstat,
                    ),
                    self.assertRaisesRegex(
                        SOURCE_LOCK.SourceLockError,
                        "identity, content size, or policy changed",
                    ),
                ):
                    SOURCE_LOCK._read_bounded_regular(
                        target,
                        label="mode drift control file",
                    )
                self.assertEqual(calls, 2)
        target.chmod(0o600)

    def test_bounded_read_rejects_in_flight_link_policy_drift(self) -> None:
        target = self.root / "link-drift-control-file"
        alias = self.root / "link-drift-control-file-alias"
        target.write_bytes(b"control\n")
        target.chmod(0o600)
        real_fstat = os.fstat
        for initial_links, final_links in ((1, 2), (2, 1)):
            with self.subTest(initial=initial_links, final=final_links):
                if initial_links == 2:
                    os.link(target, alias)
                calls = 0

                def mutate_after_first_fstat(descriptor):
                    nonlocal calls
                    metadata = real_fstat(descriptor)
                    calls += 1
                    if calls == 1:
                        if final_links == 2:
                            os.link(target, alias)
                        else:
                            alias.unlink()
                    return metadata

                try:
                    with (
                        mock.patch.object(
                            SOURCE_LOCK.os,
                            "fstat",
                            side_effect=mutate_after_first_fstat,
                        ),
                        self.assertRaisesRegex(
                            SOURCE_LOCK.SourceLockError,
                            "identity, content size, or policy changed",
                        ),
                    ):
                        SOURCE_LOCK._read_bounded_regular(
                            target,
                            label="link drift control file",
                        )
                    self.assertEqual(calls, 2)
                finally:
                    if alias.exists():
                        alias.unlink()

    def test_rejects_safe_checkout_control_file_drift_between_receipts(
        self,
    ) -> None:
        git_directory = self._checkout() / ".git"
        original_capture = SOURCE_LOCK._capture_complete_checkout_verification_receipt
        mutations = (
            (
                "HEAD mode",
                git_directory / "HEAD",
                lambda target, payload, mode: target.chmod(
                    0o600 if mode != 0o600 else 0o644
                ),
            ),
            (
                "config content",
                git_directory / "config",
                lambda target, payload, mode: target.write_bytes(
                    payload + b"\n# receipt drift fixture\n"
                ),
            ),
        )
        for case, target, mutate in mutations:
            with self.subTest(case=case):
                original_payload = target.read_bytes()
                original_mode = stat.S_IMODE(target.stat().st_mode)
                captures = 0

                def capture_then_mutate(*args, **kwargs):
                    nonlocal captures
                    receipt = original_capture(*args, **kwargs)
                    captures += 1
                    if captures == 1:
                        mutate(target, original_payload, original_mode)
                    return receipt

                try:
                    with (
                        mock.patch.object(
                            SOURCE_LOCK,
                            "_capture_complete_checkout_verification_receipt",
                            side_effect=capture_then_mutate,
                        ),
                        self.assertRaisesRegex(
                            SOURCE_LOCK.SourceLockError,
                            "structured receipt changed during verification",
                        ),
                    ):
                        SOURCE_LOCK.verify_checkouts(
                            self.source_root,
                            self.source_lock,
                        )
                    self.assertEqual(captures, 2)
                finally:
                    target.write_bytes(original_payload)
                    target.chmod(original_mode)

    def test_fixture_disables_background_git_maintenance(self) -> None:
        for checkout in (self._checkout(index) for index in range(len(self.pins))):
            self.assertEqual(
                self._git_stdout(
                    checkout,
                    "config",
                    "--local",
                    "--type=bool",
                    "--get",
                    "maintenance.auto",
                ),
                "false",
            )
            self.assertEqual(
                self._git_stdout(
                    checkout,
                    "config",
                    "--local",
                    "--type=int",
                    "--get",
                    "gc.auto",
                ),
                "0",
            )

    def test_macos_uses_fixed_system_git_instead_of_homebrew_symlink(self) -> None:
        entry = self._homebrew_git_fixture()
        self.assertEqual(
            SOURCE_LOCK.MACOS_GIT_PATH,
            Path("/Library/Developer/CommandLineTools/usr/bin/git"),
        )
        self.assertNotEqual(SOURCE_LOCK.MACOS_GIT_PATH, Path("/usr/bin/git"))
        actual_system_git = (
            SOURCE_LOCK.MACOS_GIT_PATH
            if sys.platform == "darwin"
            else Path("/usr/bin/git")
        )
        hostile_environment = {
            "DEVELOPER_DIR": os.fspath(self.root / "developer"),
            "SDKROOT": os.fspath(self.root / "sdk"),
            "TOOLCHAINS": "attacker.toolchain",
        }

        with (
            mock.patch.object(SOURCE_LOCK.sys, "platform", "darwin"),
            mock.patch.object(SOURCE_LOCK, "MACOS_GIT_PATH", actual_system_git),
            mock.patch.object(SOURCE_LOCK.shutil, "which", return_value=str(entry)),
            mock.patch.dict(SOURCE_LOCK.os.environ, hostile_environment),
        ):
            trusted = SOURCE_LOCK._trusted_git_path()
            self.assertEqual(trusted.path, actual_system_git)
            SOURCE_LOCK.verify_checkouts(self.source_root, self.source_lock)

    def test_git_environment_is_closed_against_tool_and_loader_injection(
        self,
    ) -> None:
        hostile_environment = {
            "DEVELOPER_DIR": "/attacker/developer",
            "SDKROOT": "/attacker/sdk",
            "TOOLCHAINS": "attacker.toolchain",
            "DYLD_INSERT_LIBRARIES": "/attacker/darwin.dylib",
            "DYLD_LIBRARY_PATH": "/attacker/darwin-libraries",
            "LD_PRELOAD": "/attacker/linux.so",
            "LD_LIBRARY_PATH": "/attacker/linux-libraries",
            "GIT_EXEC_PATH": "/attacker/git-core",
            "HOME": "/attacker/home",
            "PATH": "/attacker/bin",
            "UNRELATED_PARENT_VALUE": "must-not-propagate",
        }

        with mock.patch.dict(
            SOURCE_LOCK.os.environ,
            hostile_environment,
            clear=True,
        ):
            self.assertEqual(
                SOURCE_LOCK._git_environment(),
                {
                    "PATH": "/usr/bin:/bin",
                    "LANG": "C",
                    "LC_ALL": "C",
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_NO_REPLACE_OBJECTS": "1",
                    "GIT_NO_LAZY_FETCH": "1",
                    "GIT_OPTIONAL_LOCKS": "0",
                    "GIT_TERMINAL_PROMPT": "0",
                },
            )

    def test_non_macos_rejects_homebrew_symlink_instead_of_broadening_policy(
        self,
    ) -> None:
        entry = self._homebrew_git_fixture()

        with (
            mock.patch.object(SOURCE_LOCK.sys, "platform", "linux"),
            mock.patch.object(SOURCE_LOCK.shutil, "which", return_value=str(entry)),
            self.assertRaisesRegex(
                SOURCE_LOCK.SourceLockError,
                "Git executable ancestor is group- or world-writable",
            ),
        ):
            SOURCE_LOCK._trusted_git_path()

    def test_rejects_git_under_world_writable_ancestor(self) -> None:
        entry = self._homebrew_git_fixture()
        (self.root / "homebrew" / "Cellar").chmod(0o777)

        with self.assertRaisesRegex(
            SOURCE_LOCK.SourceLockError,
            "Git executable ancestor is group- or world-writable",
        ):
            SOURCE_LOCK._bind_trusted_git_path(
                entry.parent.parent / "Cellar" / "git" / "2.54.0" / "bin" / "git",
                require_root_owner=False,
            )

    def test_rejects_bound_git_executable_replacement(self) -> None:
        # tempfile.gettempdir() may be /tmp (01777), which the production
        # full-path trust policy must reject. Use a cleanup-scoped directory
        # under the already trusted repository instead of the real account home.
        with tempfile.TemporaryDirectory(
            prefix=".private-overlay-source-lock-trusted-git.",
            dir=REPO_ROOT,
        ) as trusted_parent_name:
            trusted_parent = Path(trusted_parent_name).resolve()
            trusted_parent.chmod(0o700)
            executable = trusted_parent / "git"
            shutil.copyfile(self.git_path, executable)
            executable.chmod(0o755)

            trusted = SOURCE_LOCK._bind_trusted_git_path(
                executable,
                require_root_owner=False,
            )
            replacement = trusted.path.with_name("git-replacement")
            shutil.copyfile(self.git_path, replacement)
            replacement.chmod(0o755)
            os.replace(replacement, trusted.path)

            with self.assertRaisesRegex(
                SOURCE_LOCK.SourceLockError,
                "Git executable identity or access policy changed",
            ):
                SOURCE_LOCK._revalidate_trusted_git(trusted)

    def test_locked_file_manifest_and_blob_are_exact(self) -> None:
        checkout = self._checkout(1)
        pin = self.pins[1]
        expected_object = self._git_stdout(
            checkout,
            "rev-parse",
            f"{pin.sha}:tracked.txt",
        )

        manifest = SOURCE_LOCK.load_locked_source_manifest(
            checkout,
            pin.sha,
            Path("tracked.txt"),
        )

        self.assertEqual(manifest.root_kind, "file")
        self.assertEqual(manifest.root_mode, 0o644)
        self.assertEqual(manifest.root_object_id, expected_object)
        self.assertEqual(manifest.entries, ())
        self.assertEqual(
            SOURCE_LOCK.read_locked_source_blob(
                checkout,
                manifest.root_object_id,
            ),
            b"source lock fixture 1\n",
        )

    def test_locked_directory_manifest_inventory_honors_exclusions(self) -> None:
        checkout = self._checkout(1)
        files = {
            Path("skill/keep.txt"): b"keep root\n",
            Path("skill/nested/keep.py"): b"keep nested\n",
            Path("skill/nested/drop.tmp"): b"excluded suffix\n",
            Path("skill/excluded-name/secret.txt"): b"excluded name\n",
        }
        for relative, payload in files.items():
            path = checkout / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            path.chmod(0o644)
        self._git(checkout, "add", "skill")
        self._git(checkout, "commit", "--quiet", "-m", "Add tree fixture")
        commit = self._git_stdout(checkout, "rev-parse", "HEAD")
        expected_root = self._git_stdout(checkout, "rev-parse", "HEAD:skill")

        manifest = SOURCE_LOCK.load_locked_source_manifest(
            checkout,
            commit,
            Path("skill"),
            exclude_names=("excluded-name",),
            exclude_suffixes=(".tmp",),
        )

        self.assertEqual(manifest.root_kind, "tree")
        self.assertEqual(manifest.root_mode, 0o040000)
        self.assertEqual(manifest.root_object_id, expected_root)
        entries = {entry.relative: entry for entry in manifest.entries}
        self.assertEqual(
            set(entries),
            {
                Path("keep.txt"),
                Path("nested"),
                Path("nested/keep.py"),
            },
        )
        self.assertEqual(
            (
                entries[Path("nested")].kind,
                entries[Path("nested")].mode,
            ),
            ("directory", 0o755),
        )
        for relative, payload in (
            (Path("keep.txt"), b"keep root\n"),
            (Path("nested/keep.py"), b"keep nested\n"),
        ):
            entry = entries[relative]
            self.assertEqual((entry.kind, entry.mode), ("file", 0o644))
            self.assertEqual(
                entry.object_id,
                self._git_stdout(checkout, "rev-parse", f"HEAD:skill/{relative}"),
            )
            self.assertEqual(
                SOURCE_LOCK.read_locked_source_blob(checkout, entry.object_id),
                payload,
            )

    def test_locked_directory_manifest_rejects_symlink_entry(self) -> None:
        checkout = self._checkout(1)
        source = checkout / "symlink-tree"
        source.mkdir()
        target = source / "target.txt"
        target.write_bytes(b"target\n")
        target.chmod(0o644)
        try:
            (source / "link.txt").symlink_to("target.txt")
        except OSError as error:
            self.skipTest(f"platform cannot create symlink fixture: {error}")
        self._git(checkout, "add", "symlink-tree")
        self._git(checkout, "commit", "--quiet", "-m", "Add symlink fixture")
        commit = self._git_stdout(checkout, "rev-parse", "HEAD")

        with self.assertRaisesRegex(
            SOURCE_LOCK.SourceLockError,
            "unsupported tracked object",
        ):
            SOURCE_LOCK.load_locked_source_manifest(
                checkout,
                commit,
                Path("symlink-tree"),
            )

    def test_refresh_keeps_toolbox_pin_and_updates_four_dynamic_pins(self) -> None:
        repository_root = self.root / "private-repository"
        repository_root.mkdir(mode=0o700)
        (repository_root / "personal_codex").mkdir(mode=0o700)
        (repository_root / "scripts").mkdir(mode=0o700)
        initial_lock = self.source_lock
        initial_payload = {
            "version": 1,
            "sources": [
                {
                    "name": pin.name,
                    "repository": pin.repository,
                    "sha": pin.sha,
                    "tree": pin.tree,
                }
                for pin in initial_lock.pins
            ],
            "toolbox_generated_provenance": {
                "repository": self.provenance.repository,
                "sha": self.provenance.sha,
                "receipt_sha256": self.provenance.receipt_sha256,
            },
        }
        (repository_root / SOURCE_LOCK.LOCK_PATH).write_bytes(
            _canonical_json(initial_payload)
        )
        (repository_root / SOURCE_LOCK.PRIVATE_MANIFEST_PATH).write_bytes(
            _canonical_json(
                {
                    "base_release": {
                        "repo": initial_lock.pins[0].repository,
                        "sha": initial_lock.pins[0].sha,
                    }
                }
            )
        )
        (repository_root / SOURCE_LOCK.PRIVATE_RELEASE_SCRIPT_PATH).write_text(
            "REQUIRED_PUBLIC_BASE_RELEASE_REPO = "
            f"{initial_lock.pins[0].repository!r}\n"
            "REQUIRED_PUBLIC_BASE_RELEASE_SHA = "
            f"{initial_lock.pins[0].sha!r}\n",
            encoding="utf-8",
        )
        shutil.copyfile(
            REPO_ROOT / SOURCE_LOCK.GENERATED_RECEIPT_PATH,
            repository_root / SOURCE_LOCK.GENERATED_RECEIPT_PATH,
        )
        (repository_root / SOURCE_LOCK.GENERATED_RECEIPT_PATH).chmod(0o644)
        _copy_generated_managed_tree(repository_root)

        expected_pins = [initial_lock.pins[0]]
        for index, old_pin in enumerate(initial_lock.pins[1:], start=1):
            checkout = self._checkout(index)
            (checkout / "tracked.txt").write_text(
                f"refreshed source lock fixture {index}\n",
                encoding="utf-8",
            )
            self._git(checkout, "add", "tracked.txt")
            self._git(checkout, "commit", "--quiet", "-m", "Refresh fixture")
            expected_pins.append(
                replace(
                    old_pin,
                    sha=self._git_stdout(checkout, "rev-parse", "HEAD"),
                    tree=self._git_stdout(checkout, "rev-parse", "HEAD^{tree}"),
                )
            )

        refreshed = SOURCE_LOCK.refresh_non_toolbox_pins(
            repository_root,
            self.source_root,
            initial_lock,
        )

        self.assertEqual(refreshed.pins[0], initial_lock.pins[0])
        self.assertEqual(refreshed.pins, tuple(expected_pins))
        self.assertEqual(
            refreshed.toolbox_generated_provenance,
            initial_lock.toolbox_generated_provenance,
        )
        expected_payload = {
            "version": 1,
            "sources": [
                {
                    "name": pin.name,
                    "repository": pin.repository,
                    "sha": pin.sha,
                    "tree": pin.tree,
                }
                for pin in expected_pins
            ],
            "toolbox_generated_provenance": {
                "repository": self.provenance.repository,
                "sha": self.provenance.sha,
                "receipt_sha256": self.provenance.receipt_sha256,
            },
        }
        raw = (repository_root / SOURCE_LOCK.LOCK_PATH).read_bytes()
        self.assertEqual(raw, _canonical_json(expected_payload))
        self.assertEqual(refreshed.digest, hashlib.sha256(raw).hexdigest())

    def test_rejects_wrong_head(self) -> None:
        checkout = self._checkout()
        (checkout / "tracked.txt").write_text("new content\n", encoding="utf-8")
        self._git(checkout, "add", "tracked.txt")
        self._git(checkout, "commit", "--quiet", "-m", "Advance fixture")

        with self.assertRaisesRegex(SOURCE_LOCK.SourceLockError, "identity differs"):
            SOURCE_LOCK.verify_checkouts(self.source_root, self.source_lock)

    def test_rejects_wrong_tree_pin(self) -> None:
        self.pins[0] = replace(self.pins[0], tree="f" * 40)

        with self.assertRaisesRegex(SOURCE_LOCK.SourceLockError, "identity differs"):
            SOURCE_LOCK.verify_checkouts(self.source_root, self.source_lock)

    def test_rejects_attached_head(self) -> None:
        checkout = self._checkout()
        self._git(checkout, "switch", "--quiet", "main")

        with self.assertRaisesRegex(SOURCE_LOCK.SourceLockError, "not detached"):
            SOURCE_LOCK.verify_checkouts(self.source_root, self.source_lock)

    def test_rejects_dirty_and_untracked_checkout(self) -> None:
        checkout = self._checkout()
        (checkout / "untracked.txt").write_text("untracked\n", encoding="utf-8")

        with self.assertRaisesRegex(SOURCE_LOCK.SourceLockError, "dirty or untracked"):
            SOURCE_LOCK.verify_checkouts(self.source_root, self.source_lock)

    def test_rejects_shallow_checkout(self) -> None:
        checkout = self._checkout()
        git_directory = Path(self._git_stdout(checkout, "rev-parse", "--git-dir"))
        if not git_directory.is_absolute():
            git_directory = checkout / git_directory
        (git_directory / "shallow").write_text(
            f"{self.pins[0].sha}\n",
            encoding="ascii",
        )

        with self.assertRaisesRegex(SOURCE_LOCK.SourceLockError, "shallow"):
            SOURCE_LOCK.verify_checkouts(self.source_root, self.source_lock)

    def test_rejects_promisor_checkout(self) -> None:
        checkout = self._checkout()
        self._git(checkout, "config", "remote.origin.promisor", "true")

        with self.assertRaisesRegex(SOURCE_LOCK.SourceLockError, "promisor"):
            SOURCE_LOCK.verify_checkouts(self.source_root, self.source_lock)

    def test_rejects_replace_refs(self) -> None:
        checkout = self._checkout()
        replacement = self._git_stdout(
            checkout,
            "commit-tree",
            self.pins[0].tree,
            "-m",
            "Replacement fixture",
        )
        self._git(checkout, "replace", self.pins[0].sha, replacement)

        with self.assertRaisesRegex(SOURCE_LOCK.SourceLockError, "replace refs"):
            SOURCE_LOCK.verify_checkouts(self.source_root, self.source_lock)

    def test_rejects_sparse_checkout_configuration(self) -> None:
        checkout = self._checkout()
        self._git(checkout, "config", "core.sparseCheckout", "true")

        with self.assertRaisesRegex(SOURCE_LOCK.SourceLockError, "sparse checkout"):
            SOURCE_LOCK.verify_checkouts(self.source_root, self.source_lock)

    def test_rejects_assume_unchanged_hidden_modification(self) -> None:
        checkout = self._checkout()
        self._git(checkout, "update-index", "--assume-unchanged", "tracked.txt")
        (checkout / "tracked.txt").write_text(
            "hidden modified content\n",
            encoding="utf-8",
        )
        self.assertEqual(
            self._git(checkout, "status", "--porcelain=v1").stdout,
            b"",
        )

        with self.assertRaisesRegex(
            SOURCE_LOCK.SourceLockError,
            "non-default index flags",
        ):
            SOURCE_LOCK.verify_checkouts(self.source_root, self.source_lock)

    def test_rejects_fsmonitor_valid_index_flag_when_supported(self) -> None:
        checkout = self._checkout()
        self._git(checkout, "update-index", "--fsmonitor-valid", "tracked.txt")
        record = self._git(
            checkout,
            "ls-files",
            "-f",
            "--",
            "tracked.txt",
        ).stdout
        if not record or record[:1] == b"H":
            self.skipTest(
                "Git did not persist an observable fsmonitor-valid index flag"
            )

        with self.assertRaisesRegex(
            SOURCE_LOCK.SourceLockError,
            "non-default index flags",
        ):
            SOURCE_LOCK.verify_checkouts(self.source_root, self.source_lock)

    def test_rejects_git_clean_narrow_physical_mode(self) -> None:
        checkout = self._checkout()
        (checkout / "tracked.txt").chmod(0o600)
        self.assertEqual(
            self._git(checkout, "status", "--porcelain=v1").stdout,
            b"",
        )

        with self.assertRaisesRegex(
            SOURCE_LOCK.SourceLockError,
            "physical mode differs",
        ):
            SOURCE_LOCK.verify_checkouts(self.source_root, self.source_lock)

    def test_rejects_ignored_file_in_tracked_directory(self) -> None:
        checkout = self._checkout()
        (checkout / ".gitignore").write_text("ignored.cache\n", encoding="utf-8")
        (checkout / ".gitignore").chmod(0o644)
        self._git(checkout, "add", ".gitignore")
        self._git(checkout, "commit", "--quiet", "-m", "Ignore fixture output")
        self._refresh_pin_from_checkout()
        (checkout / "ignored.cache").write_text("ignored\n", encoding="utf-8")
        self.assertEqual(
            self._git(
                checkout,
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ).stdout,
            b"",
        )

        with self.assertRaisesRegex(
            SOURCE_LOCK.SourceLockError,
            "dirty or untracked",
        ):
            SOURCE_LOCK.verify_checkouts(self.source_root, self.source_lock)

    def test_rejects_local_filter_and_diff_executable_config(self) -> None:
        checkout = self._checkout()
        for key in ("filter.fixture.clean", "diff.fixture.command"):
            with self.subTest(key=key):
                self._git(checkout, "config", key, "/usr/bin/false")
                with self.assertRaisesRegex(
                    SOURCE_LOCK.SourceLockError,
                    "external filter or diff config",
                ):
                    SOURCE_LOCK.verify_checkouts(
                        self.source_root,
                        self.source_lock,
                    )
                self._git(checkout, "config", "--unset-all", key)

    def test_rejects_local_core_worktree_redirect(self) -> None:
        checkout = self._checkout()
        sibling = self.root / "sibling-worktree"
        sibling.mkdir(mode=0o700)
        self._git(checkout, "config", "core.worktree", os.fspath(sibling))

        with self.assertRaisesRegex(
            SOURCE_LOCK.SourceLockError,
            "external filter or diff config|Git worktree differs",
        ):
            SOURCE_LOCK.verify_checkouts(self.source_root, self.source_lock)

    def test_rejects_gitlink_index_entry_without_submodule_checkout(self) -> None:
        checkout = self._checkout()
        self._git(
            checkout,
            "update-index",
            "--add",
            "--cacheinfo",
            "160000",
            self.pins[0].sha,
            "gitlink-fixture",
        )

        with self.assertRaisesRegex(
            SOURCE_LOCK.SourceLockError,
            "gitlink|unsupported tracked mode",
        ):
            SOURCE_LOCK.verify_checkouts(self.source_root, self.source_lock)

    def test_rejects_core_symlinks_false_regular_file_materialization(self) -> None:
        checkout = self._checkout()
        link = checkout / "tracked-link"
        try:
            link.symlink_to("tracked.txt")
        except OSError as error:
            self.skipTest(f"platform cannot create the symlink fixture: {error}")
        self._git(checkout, "add", "tracked-link")
        self._git(checkout, "commit", "--quiet", "-m", "Add symlink fixture")
        self._refresh_pin_from_checkout()
        self._git(checkout, "config", "core.symlinks", "false")
        link.unlink()
        link.write_text("tracked.txt", encoding="utf-8")
        link.chmod(0o644)
        if self._git(checkout, "status", "--porcelain=v1").stdout:
            self.skipTest(
                "Git did not accept regular-file materialization for a symlink blob"
            )

        with self.assertRaisesRegex(
            SOURCE_LOCK.SourceLockError,
            "tracked source symlink|symlink kind differs",
        ):
            SOURCE_LOCK.verify_checkouts(self.source_root, self.source_lock)

    def test_rejects_alternate_object_store(self) -> None:
        checkout = self._checkout()
        alternate = self.root / "alternate"
        alternate.mkdir(mode=0o700)
        self._git(alternate, "init", "--quiet", "--initial-branch=main")
        alternate_objects = alternate / ".git" / "objects"
        info = checkout / ".git" / "objects" / "info"
        info.mkdir(parents=True, exist_ok=True)
        (info / "alternates").write_text(
            f"{alternate_objects}\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(SOURCE_LOCK.SourceLockError, "alternate"):
            SOURCE_LOCK.verify_checkouts(self.source_root, self.source_lock)

    def test_rejects_symlink_source_root(self) -> None:
        link = self.root / "source-link"
        link.symlink_to(self.source_root, target_is_directory=True)

        with self.assertRaisesRegex(SOURCE_LOCK.SourceLockError, "source root"):
            SOURCE_LOCK.verify_checkouts(link, self.source_lock)

    def test_cli_preserves_lexical_source_root_for_symlink_rejection(self) -> None:
        link = self.root / "source-link"
        link.symlink_to(self.source_root, target_is_directory=True)
        captured = io.StringIO()

        with (
            mock.patch.object(
                SOURCE_LOCK,
                "load_source_lock",
                return_value=self.source_lock,
            ),
            mock.patch.object(SOURCE_LOCK, "validate_base_release_binding"),
            mock.patch.object(SOURCE_LOCK, "validate_generated_provenance"),
            contextlib.redirect_stderr(captured),
        ):
            result = SOURCE_LOCK.main(
                [
                    "verify-checkouts",
                    "--repo-root",
                    os.fspath(REPO_ROOT),
                    "--source-root",
                    os.fspath(link),
                ]
            )

        self.assertEqual(result, 1)
        self.assertIn("source root", captured.getvalue())

    def test_rejects_symlink_checkout(self) -> None:
        checkout = self._checkout()
        real_checkout = self.root / "relocated-checkout"
        checkout.rename(real_checkout)
        checkout.symlink_to(real_checkout, target_is_directory=True)

        with self.assertRaisesRegex(
            SOURCE_LOCK.SourceLockError,
            "non-symlink directory",
        ):
            SOURCE_LOCK.verify_checkouts(self.source_root, self.source_lock)


if __name__ == "__main__":
    unittest.main()
