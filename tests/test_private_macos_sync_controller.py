from __future__ import annotations

import ctypes
import errno
import hashlib
import importlib.machinery
import importlib.util
import io
import fcntl
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "personal_codex" / "bin" / "codex-private-macos-sync"
INVENTORY = REPO_ROOT / "personal_codex" / "private-sync-hosts.json"


def load_module():
    name = "private_macos_sync_controller_tests"
    loader = importlib.machinery.SourceFileLoader(name, str(SCRIPT))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


MODULE = load_module()
PUBLIC_SHA = "1" * 40
NEXT_PUBLIC_SHA = "0" * 40
PRIVATE_SHA = "2" * 40
NEXT_PRIVATE_SHA = "3" * 40
FINAL_PRIVATE_SHA = "4" * 40
PUBLIC_TREE = "a" * 64
PRIVATE_TREE = "b" * 64
NEXT_PRIVATE_TREE = "c" * 64
FINAL_PRIVATE_TREE = "d" * 64
CANONICAL_SOURCE_SHA = "5" * 40
PUBLIC_SOURCE_TREE = "6" * 40
CANONICAL_RUNNER_BYTES = b"#!/usr/bin/env python3\n# trusted test runner\n"
CANONICAL_RUNNER_SHA256 = hashlib.sha256(CANONICAL_RUNNER_BYTES).hexdigest()
NEXT_CANONICAL_RUNNER_BYTES = b"#!/usr/bin/env python3\n# trusted next runner\n"


def decode_runtime_call(argv):
    """Return the semantic command while retaining raw argv separately."""
    raw_call = tuple(argv)
    if (
        len(raw_call) >= 6
        and raw_call[1:4] == MODULE.CANONICAL_PYTHON_FLAGS
        and raw_call[4].endswith("/scripts/codex_personal_sync.py")
    ):
        return (raw_call[4], *raw_call[5:])
    return raw_call


class FakeRuntime(MODULE.Runtime):
    def __init__(self, account, candidates=("controller",), gui=True):
        self._account = account
        self._candidates = tuple(candidates)
        self.gui = gui
        self.calls = []
        self.raw_calls = []
        self.before_spawn_presence = []
        self.ssh_results = []
        self.install_results = []
        self.run_scheduled_results = []
        self.ssh_factory = None
        self.install_factory = None
        self.run_scheduled_hook = None
        self.before_spawn_hook = None
        self.verify_hook = None
        self.verify_results = []
        self.notification_error = False
        self.notification_exception = None
        self.scheduler_state = None
        self.scheduler_enabled = True
        self.scheduler_daemon_classification = "enabled"
        self.scheduler_status_results = []
        self.identity_pair = {
            "public": {"sha": PUBLIC_SHA, "tree_sha256": PUBLIC_TREE},
            "private": {"sha": PRIVATE_SHA, "tree_sha256": PRIVATE_TREE},
        }
        self.machine_digest = "f" * 64

    def account(self):
        return self._account

    def host_candidates(self):
        return self._candidates

    def machine_identity_sha256(self):
        return self.machine_digest

    def run(self, argv, *, timeout, output_limit, before_spawn=None):
        raw_call = tuple(argv)
        self.raw_calls.append(raw_call)
        call = decode_runtime_call(raw_call)
        self.before_spawn_presence.append((call, before_spawn is not None))
        if before_spawn is not None:
            if self.before_spawn_hook is not None:
                self.before_spawn_hook(call)
            before_spawn()
        self.calls.append(call)
        if call[:2] == ("/bin/launchctl", "print"):
            return MODULE.CommandResult(0 if self.gui else 113, b"", b"")
        if call and call[0] == "/usr/bin/osascript":
            if self.notification_exception is not None:
                raise self.notification_exception
            if self.notification_error:
                raise MODULE.ControllerError("notification unavailable")
            return MODULE.CommandResult(0, b"", b"")
        if call and call[0] == "/usr/bin/ssh":
            if self.ssh_factory is not None:
                return self.ssh_factory(call)
            if self.ssh_results:
                return self.ssh_results.pop(0)
            raise AssertionError("unexpected SSH invocation")
        if len(call) >= 2 and call[1] == "release-identities":
            payload = {
                "version": 1,
                "mode": "private",
                "release_trees": self.identity_pair,
            }
            return MODULE.CommandResult(
                0,
                json.dumps(payload, sort_keys=True).encode("utf-8"),
                b"",
            )
        if len(call) >= 2 and call[1] == "status-scheduler":
            if self.scheduler_status_results:
                return self.scheduler_status_results.pop(0)
            return self.scheduler_status_result(call)
        if len(call) >= 2 and call[1] == "run-scheduled":
            if self.run_scheduled_results:
                result = self.run_scheduled_results.pop(0)
                if result.returncode != 0:
                    return result
            if self.run_scheduled_hook is not None:
                self.run_scheduled_hook()
            return MODULE.CommandResult(0, b"local sync\n", b"")
        if len(call) >= 2 and call[1] == "install-scheduler":
            if self.install_factory is not None:
                return self.install_factory(call)
            if self.install_results:
                result = self.install_results.pop(0)
                if result.returncode != 0:
                    return result
            self.scheduler_state = {
                "runner": call[call.index("--runner") + 1],
                "interval_minutes": call[call.index("--interval-minutes") + 1],
                "mode": call[call.index("--mode") + 1],
                "repo": call[call.index("--repo") + 1],
                "base_repo": call[call.index("--base-repo") + 1],
                "owner": call[call.index("--owner") + 1],
            }
            return MODULE.CommandResult(0, b"ok\n", b"")
        if len(call) >= 2 and call[1] in ("status", "verify-overlay"):
            if call[1] == "verify-overlay" and self.verify_hook is not None:
                self.verify_hook()
            if call[1] == "verify-overlay" and self.verify_results:
                return self.verify_results.pop(0)
            return MODULE.CommandResult(0, b"ok\n", b"")
        raise AssertionError(f"unexpected command: {call}")

    def scheduler_status_result(self, call):
        home = Path(call[call.index("--home") + 1])
        state = self.scheduler_state
        installed = state is not None
        runner = state.get("runner") if state is not None else None
        stable_runner = runner == str(home / "bin" / "codex-personal-sync")
        daemon_classification = (
            self.scheduler_daemon_classification if installed else "disabled"
        )
        enabled = bool(
            installed
            and self.scheduler_enabled
            and daemon_classification == "enabled"
        )
        failures = []
        if installed and not stable_runner:
            failures.append(
                {
                    "code": "scheduler-runner-drift",
                    "reason": "scheduler does not use the stable installed runner path",
                }
            )
        if installed and not enabled:
            failures.append(
                {
                    "code": "scheduler-daemon-disabled",
                    "reason": "scheduler daemon explicitly reports a disabled state",
                }
            )
        payload = {
            "platform": "macos",
            "installed": installed,
            "enabled": enabled,
            "config": [
                str(
                    home.parent
                    / "Library"
                    / "LaunchAgents"
                    / "io.github.joey-tools.codex-personal-sync.plist"
                )
            ],
            "interval_minutes": (
                int(state.get("interval_minutes", 30)) if state is not None else None
            ),
            "runner": runner,
            "stable_runner": stable_runner,
            "command": "run-scheduled" if installed else None,
            "mode": state.get("mode", "private") if state is not None else None,
            "repo": state.get("repo", MODULE.PRIVATE_REPO) if state is not None else None,
            "base_repo": (
                state.get("base_repo", MODULE.PUBLIC_REPO)
                if state is not None
                else None
            ),
            "private_repo": (
                state.get("repo", MODULE.PRIVATE_REPO)
                if state is not None and state.get("mode", "private") == "private"
                else None
            ),
            "owner": (
                state.get("owner", MODULE.PRIVATE_OWNER)
                if state is not None
                else None
            ),
            "migration_needed": False,
            "last_attempt": None,
            "recent_success": None,
            "current_release": (
                {
                    owner: identity["sha"]
                    for owner, identity in self.identity_pair.items()
                }
                if installed
                else {}
            ),
            "release_integrity": [],
            "quarantine_batches": 0,
            "quarantine_limit": 64,
            "mirror_quarantine": None,
            "failure_code": failures[0]["code"] if failures else None,
            "failure_reason": failures[0]["reason"] if failures else None,
            "daemon_query": {
                "classification": daemon_classification,
                "reason": (
                    None
                    if daemon_classification == "enabled"
                    else "scheduler daemon explicitly reports a disabled state"
                ),
            },
            "failures": failures,
        }
        return MODULE.CommandResult(
            0 if installed and stable_runner and not failures else 1,
            json.dumps(payload, sort_keys=True).encode("utf-8") + b"\n",
            b"",
        )

    def monotonic(self):
        return 100.0

    def sleep(self, seconds):
        raise AssertionError(f"unexpected sleep: {seconds}")


class DeterministicAclApi:
    def __init__(self, *, unsafe_passes=()):
        self.unsafe_passes = frozenset(unsafe_passes)
        self.pass_count = 0
        self._unsafe = False
        self._entry_returned = False
        self._nonowner_uuid = (ctypes.c_ubyte * 16)(*([0xFF] * 16))

    def acl_get_fd_np(self, _fd, _acl_type):
        self.pass_count += 1
        self._unsafe = self.pass_count in self.unsafe_passes
        self._entry_returned = False
        if self._unsafe:
            return 1
        ctypes.set_errno(errno.ENOENT)
        return None

    @staticmethod
    def acl_valid(_acl):
        return 0

    @staticmethod
    def mbr_uid_to_uuid(_uid, owner_uuid):
        for index in range(16):
            owner_uuid[index] = index
        return 0

    def acl_get_entry(self, _acl, _selector, entry_out):
        if self._unsafe and not self._entry_returned:
            self._entry_returned = True
            entry_out._obj.value = 1
            return 0
        ctypes.set_errno(errno.EINVAL)
        return -1

    @staticmethod
    def acl_get_tag_type(_entry, tag_out):
        tag_out._obj.value = MODULE.DARWIN_ACL_EXTENDED_ALLOW
        return 0

    def acl_get_qualifier(self, _entry):
        return ctypes.addressof(self._nonowner_uuid)

    @staticmethod
    def acl_free(_value):
        return 0


class AccessPolicyMetadata:
    def __init__(
        self,
        *,
        mode,
        uid,
        ctime_ns,
        nlink=1,
        dev=11,
        ino=29,
        gid=None,
    ):
        self.st_dev = dev
        self.st_ino = ino
        self.st_mode = mode
        self.st_uid = uid
        self.st_gid = os.getegid() if gid is None else gid
        self.st_nlink = nlink
        self.st_ctime_ns = ctime_ns


class PrivateMacosSyncControllerTests(unittest.TestCase):
    def setUp(self):
        self.resolver_patch = None
        self.temp = tempfile.TemporaryDirectory(
            prefix=".private-macos-sync.",
            dir=REPO_ROOT,
        )
        self.root = Path(self.temp.name).resolve()
        self.real_python_resolver = MODULE._resolved_current_python_executable
        self.test_python_executable = None
        if sys.platform.startswith("linux"):
            python_root = self.root / "python-runtime"
            python_root.mkdir(mode=0o700)
            self.test_python_executable = python_root / "python"
            shutil.copyfile(
                self.real_python_resolver(),
                self.test_python_executable,
            )
            self.test_python_executable.chmod(0o755)
            test_python_metadata = self.test_python_executable.stat()
            self.assertTrue(stat.S_ISREG(test_python_metadata.st_mode))
            self.assertFalse(self.test_python_executable.is_symlink())
            self.assertEqual(test_python_metadata.st_nlink, 1)
            self.resolver_patch = mock.patch.object(
                MODULE,
                "_resolved_current_python_executable",
                return_value=self.test_python_executable,
            )
            self.addCleanup(self.stop_resolver_patch)
            self.resolver_patch.start()
        self.account_home = self.root / "Users" / "hoteng"
        self.home = self.account_home / ".codex"
        self.home.mkdir(parents=True)
        self.uid = os.geteuid()
        self.account = MODULE.Account("hoteng", self.uid, self.account_home)
        self.controller_id = "controller"
        self.target_id = "headless"
        self.write_release(PRIVATE_SHA, self.inventory_data())
        self.switch_current(PRIVATE_SHA)

    def tearDown(self):
        self.stop_resolver_patch()
        self.temp.cleanup()

    def stop_resolver_patch(self):
        resolver_patch = self.resolver_patch
        self.resolver_patch = None
        if resolver_patch is not None:
            resolver_patch.stop()

    def inventory_data(
        self,
        *,
        controller_role="gui-controller",
        controller_id=None,
        target_controller=None,
        include_target=True,
        extra_hosts=None,
    ):
        controller_id = controller_id or self.controller_id
        target_controller = target_controller or controller_id
        hosts = [
            {
                "id": controller_id,
                "role": controller_role,
                "username": "hoteng",
                "uid": self.uid,
                "home": str(self.account_home),
            }
        ]
        if include_target:
            hosts.append(
                {
                    "id": self.target_id,
                    "role": "headless-managed",
                    "username": "hoteng",
                    "uid": self.uid,
                    "home": str(self.account_home),
                    "controller": target_controller,
                    "ssh_alias": "headless-ssh",
                }
            )
        hosts.extend(extra_hosts or [])
        return {"version": 1, "hosts": hosts}

    def write_release(
        self,
        sha,
        inventory,
        *,
        public_sha=PUBLIC_SHA,
        runner_bytes=CANONICAL_RUNNER_BYTES,
    ):
        release_root = (
            self.home
            / "personal-sync"
            / "overlays"
            / "private"
            / "releases"
            / sha
        )
        release = release_root / "personal_codex"
        release.mkdir(parents=True)
        path = release / "private-sync-hosts.json"
        path.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
        self.write_runner_attestation(
            self.home,
            release_root,
            public_sha=public_sha,
            runner_bytes=runner_bytes,
        )
        return path

    def write_runner_attestation(
        self,
        home,
        private_release_root,
        *,
        public_sha=PUBLIC_SHA,
        runner_bytes=CANONICAL_RUNNER_BYTES,
    ):
        runner_sha256 = hashlib.sha256(runner_bytes).hexdigest()
        generated = {
            "canonical_commit": CANONICAL_SOURCE_SHA,
            "canonical_repository": MODULE.CANONICAL_SOURCE_REPO,
            "file_set_digest": "7" * 64,
            "files": [
                {
                    "mode": MODULE.CANONICAL_RUNNER_MODE,
                    "sha256": runner_sha256,
                    "source_name": "engine",
                    "source_path": MODULE.CANONICAL_RUNNER_SOURCE,
                    "target_path": MODULE.CANONICAL_RUNNER_SOURCE,
                }
            ],
            "generator_contract_version": 2,
            "hash_algorithm": "sha256",
            "mapping_digest": "8" * 64,
            "mirror": "toolbox",
            "mirror_repository": MODULE.PUBLIC_REPO,
            "receipt_version": 1,
            "rules_contract_version": 1,
            "tree_digest": "9" * 64,
        }
        generated_payload = (
            json.dumps(generated, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        (private_release_root / MODULE.GENERATED_SOURCE_LOCK_PATH).write_bytes(
            generated_payload
        )
        source_lock = {
            "version": 1,
            "sources": [
                {
                    "name": "codex-toolbox",
                    "repository": MODULE.PUBLIC_REPO,
                    "sha": public_sha,
                    "tree": PUBLIC_SOURCE_TREE,
                }
            ],
            "toolbox_generated_provenance": {
                "repository": MODULE.CANONICAL_SOURCE_REPO,
                "sha": CANONICAL_SOURCE_SHA,
                "receipt_sha256": hashlib.sha256(generated_payload).hexdigest(),
            },
        }
        (private_release_root / MODULE.PRIVATE_SOURCE_LOCK_PATH).write_text(
            json.dumps(source_lock, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        private_manifest = {
            "version": 1,
            "owner": MODULE.PRIVATE_OWNER,
            "base_release": {
                "repo": MODULE.PUBLIC_REPO,
                "sha": public_sha,
            },
            "links": [],
            "removed_links": [],
            "reference_only": [
                MODULE.GENERATED_SOURCE_LOCK_PATH,
                "personal_codex/private-sync-hosts.json",
                MODULE.PRIVATE_SOURCE_LOCK_PATH,
            ],
        }
        personal_codex = private_release_root / "personal_codex"
        personal_codex.mkdir(parents=True, exist_ok=True)
        (personal_codex / "sync-manifest.json").write_text(
            json.dumps(private_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        public_release = home / "personal-sync" / "releases" / public_sha
        scripts = public_release / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        runner = scripts / "codex_personal_sync.py"
        if not runner.exists():
            runner.write_bytes(runner_bytes)
            runner.chmod(0o755)
        public_personal_codex = public_release / "personal_codex"
        public_personal_codex.mkdir(parents=True, exist_ok=True)
        public_manifest = {
            "version": 1,
            "links": [
                {
                    "source": MODULE.CANONICAL_RUNNER_SOURCE,
                    "target": MODULE.CANONICAL_RUNNER_TARGET,
                    "kind": "file",
                }
            ],
            "reference_only": [],
        }
        (public_personal_codex / "sync-manifest.json").write_text(
            json.dumps(public_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        public_current = home / "personal-sync" / "current"
        if not public_current.exists() and not public_current.is_symlink():
            public_current.symlink_to(f"releases/{public_sha}")
        bin_directory = home / "bin"
        bin_directory.mkdir(parents=True, exist_ok=True)
        live_runner = bin_directory / "codex-personal-sync"
        if not live_runner.exists() and not live_runner.is_symlink():
            live_runner.symlink_to(MODULE.CANONICAL_RUNNER_LIVE_TARGET)

    def switch_current(self, sha):
        current = (
            self.home / "personal-sync" / "overlays" / "private" / "current"
        )
        current.parent.mkdir(parents=True, exist_ok=True)
        replacement = current.with_name("current.next")
        if replacement.exists() or replacement.is_symlink():
            replacement.unlink()
        replacement.symlink_to(f"releases/{sha}")
        os.replace(replacement, current)

    def switch_public_current(self, sha, *, home=None):
        selected_home = home or self.home
        current = selected_home / "personal-sync" / "current"
        replacement = current.with_name("current.next")
        if replacement.exists() or replacement.is_symlink():
            replacement.unlink()
        replacement.symlink_to(f"releases/{sha}")
        os.replace(replacement, current)

    def private_release_root(self, sha=PRIVATE_SHA, *, home=None):
        return (
            (home or self.home)
            / "personal-sync"
            / "overlays"
            / "private"
            / "releases"
            / sha
        )

    def public_runner_path(self, public_sha=PUBLIC_SHA, *, home=None):
        return (
            (home or self.home)
            / "personal-sync"
            / "releases"
            / public_sha
            / "scripts"
            / "codex_personal_sync.py"
        )

    def rewrite_generated_lock(self, mutator, sha=PRIVATE_SHA):
        release_root = self.private_release_root(sha)
        generated_path = release_root / MODULE.GENERATED_SOURCE_LOCK_PATH
        generated = json.loads(generated_path.read_text(encoding="utf-8"))
        mutator(generated)
        generated_payload = (
            json.dumps(generated, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        generated_path.write_bytes(generated_payload)
        source_path = release_root / MODULE.PRIVATE_SOURCE_LOCK_PATH
        source = json.loads(source_path.read_text(encoding="utf-8"))
        source["toolbox_generated_provenance"]["receipt_sha256"] = (
            hashlib.sha256(generated_payload).hexdigest()
        )
        source_path.write_text(
            json.dumps(source, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def runtime(self, candidates=None, gui=True):
        return FakeRuntime(
            self.account,
            candidates=candidates or (self.controller_id,),
            gui=gui,
        )

    def remote_target_runtime(
        self,
        *,
        private_sha=PRIVATE_SHA,
        private_tree=PRIVATE_TREE,
        inventory=None,
    ):
        account_home = self.root / "RemoteUsers" / "hoteng"
        home = account_home / ".codex"
        home.mkdir(parents=True)
        remote_inventory = json.loads(
            json.dumps(inventory or self.inventory_data())
        )
        for host in remote_inventory["hosts"]:
            host["home"] = str(account_home)
        release = (
            home
            / "personal-sync"
            / "overlays"
            / "private"
            / "releases"
            / private_sha
            / "personal_codex"
        )
        release.mkdir(parents=True)
        (release / "private-sync-hosts.json").write_text(
            json.dumps(remote_inventory, indent=2) + "\n",
            encoding="utf-8",
        )
        self.write_runner_attestation(home, release.parent)
        current = home / "personal-sync" / "overlays" / "private" / "current"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.symlink_to(f"releases/{private_sha}")
        runtime = FakeRuntime(
            MODULE.Account("hoteng", self.uid, account_home),
            candidates=(self.target_id,),
            gui=False,
        )
        runtime.identity_pair = self.desired(private_sha, private_tree)
        MODULE.activate(
            runtime,
            home,
            requested_host_id=self.target_id,
            interval_minutes=30,
        )
        runtime.calls.clear()
        return runtime, home

    def activate_controller(self, runtime):
        return MODULE.activate(
            runtime,
            self.home,
            requested_host_id=self.controller_id,
            interval_minutes=30,
        )

    def current_snapshot(self):
        return MODULE._load_current_inventory(self.home, self.uid)

    def desired(self, private_sha=PRIVATE_SHA, private_tree=PRIVATE_TREE):
        return {
            "public": {"sha": PUBLIC_SHA, "tree_sha256": PUBLIC_TREE},
            "private": {"sha": private_sha, "tree_sha256": private_tree},
        }

    def physical_runner(self, home=None, public_sha=PUBLIC_SHA):
        return str(self.public_runner_path(public_sha, home=home))

    def expected_canonical_interpreter(self):
        if sys.platform == "darwin":
            return MODULE.DARWIN_CANONICAL_PYTHON.resolve(strict=True)
        if self.test_python_executable is not None:
            return self.test_python_executable
        return self.real_python_resolver()

    def success_receipt(self, desired=None, snapshot=None):
        desired = desired or self.desired()
        snapshot = snapshot or self.current_snapshot()
        controller = snapshot.inventory.hosts[self.controller_id]
        target = snapshot.inventory.hosts[self.target_id]
        return {
            "version": 1,
            "status": "verified",
            "controller_id": self.controller_id,
            "target_id": self.target_id,
            "scope_sha256": MODULE._scope_digest(controller, target),
            "inventory_release_sha": snapshot.release_sha,
            "inventory_sha256": snapshot.content_sha256,
            "release_trees": desired,
        }

    @staticmethod
    def json_result(payload, returncode=0):
        return MODULE.CommandResult(
            returncode,
            json.dumps(payload, sort_keys=True).encode("utf-8") + b"\n",
            b"",
        )

    def scheduler_payload(self, runtime):
        call = (
            str(self.home / "bin" / "codex-personal-sync"),
            "status-scheduler",
            "--home",
            str(self.home),
            "--platform",
            "macos",
            "--json",
            "--strict",
        )
        result = runtime.scheduler_status_result(call)
        return json.loads(result.stdout.decode("utf-8"))

    def state_path(self):
        return (
            self.home
            / MODULE.STATE_DIRECTORY_NAME
            / f"target-{self.target_id}.json"
        )

    def rewrite_target_controller(self, controller_id):
        path = self.state_path()
        state_value = json.loads(path.read_text(encoding="utf-8"))
        state_value["controller_id"] = controller_id
        path.write_bytes(MODULE._canonical_json_bytes(state_value))
        os.chmod(path, 0o600)
        return state_value

    def write_target_fence(
        self,
        target_id,
        *,
        controller_id=None,
        last_error="process-cleanup-inconclusive",
    ):
        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        state_directory.mkdir(mode=0o700, exist_ok=True)
        state_value = MODULE._empty_target_state(
            controller_id or self.controller_id,
            target_id,
            "0" * 64,
        )
        state_value["desired"] = self.desired()
        state_value["pending"] = True
        state_value["generation"] = 1
        state_value["last_error"] = last_error
        path = state_directory / f"target-{target_id}.json"
        path.write_bytes(MODULE._canonical_json_bytes(state_value))
        os.chmod(path, 0o600)
        return path

    def operation_state_path(self, operation):
        self.assertIn(operation, MODULE.HOST_MUTATION_OPERATIONS)
        return (
            self.home
            / MODULE.STATE_DIRECTORY_NAME
            / MODULE.HOST_MUTATION_STATE_NAME
        )

    def operation_state(self, operation):
        return json.loads(
            self.operation_state_path(operation).read_text(encoding="utf-8")
        )

    def remote_argv(self, *, home=None, desired=None):
        desired = desired or self.desired()
        return [
            "remote-apply",
            "--home",
            str(home or self.home),
            "--host-id",
            self.target_id,
            "--controller-id",
            self.controller_id,
            "--expected-public-sha",
            desired["public"]["sha"],
            "--expected-public-tree",
            desired["public"]["tree_sha256"],
            "--expected-private-sha",
            desired["private"]["sha"],
            "--expected-private-tree",
            desired["private"]["tree_sha256"],
        ]

    def invoke_remote_main(self, runtime, home, desired):
        class CaptureStdout:
            def __init__(self):
                self.buffer = io.BytesIO()

        stdout = CaptureStdout()
        stderr = io.StringIO()
        with (
            mock.patch("sys.stdout", new=stdout),
            mock.patch("sys.stderr", new=stderr),
        ):
            returncode = MODULE.main(
                self.remote_argv(home=home, desired=desired),
                runtime,
            )
        return returncode, stdout.buffer.getvalue(), stderr.getvalue()

    def write_activation_pending(self, runtime, home, status):
        path = (
            home
            / MODULE.STATE_DIRECTORY_NAME
            / MODULE.ACTIVATION_PENDING_FILE_NAME
        )
        if status == "pending":
            path.write_bytes(
                MODULE._canonical_json_bytes(
                    {
                        "version": MODULE.VERSION,
                        "status": status,
                        "receipt_sha256": "a" * 64,
                    }
                )
            )
            os.chmod(path, 0o600)
            return path

        account = runtime.account()
        receipt = json.loads(
            (
                home
                / MODULE.STATE_DIRECTORY_NAME
                / MODULE.ACTIVATION_FILE_NAME
            ).read_text(encoding="utf-8")
        )
        with MODULE._activation_lock(home, account.uid, runtime) as transaction:
            MODULE._begin_activation_publication(transaction, account, receipt)
            if status == "retryable":
                MODULE._mark_activation_retryable(transaction, account)
            elif status == "process-cleanup-inconclusive":
                MODULE._quarantine_activation_process_cleanup(
                    transaction,
                    account,
                )
            elif status != "in-flight":
                self.fail(f"unsupported activation sentinel status: {status}")
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8")),
            {
                "version": MODULE.VERSION,
                "status": status,
                "receipt_sha256": hashlib.sha256(
                    MODULE._canonical_json_bytes(receipt)
                ).hexdigest(),
            },
        )
        return path

    def notification_calls(self, runtime):
        return [call for call in runtime.calls if call[0] == "/usr/bin/osascript"]

    def ssh_calls(self, runtime):
        return [call for call in runtime.calls if call[0] == "/usr/bin/ssh"]

    def add_extended_acl(self, path, rule):
        before = stat.S_IMODE(path.lstat().st_mode)
        argv = ["/bin/chmod"]
        if path.is_symlink():
            argv.append("-h")
        argv.extend(("+a", rule, str(path)))
        subprocess.run(
            argv,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(stat.S_IMODE(path.lstat().st_mode), before)

    def clear_extended_acl(self, path):
        argv = ["/bin/chmod"]
        if path.is_symlink():
            argv.append("-h")
        argv.extend(("-N", str(path)))
        subprocess.run(
            argv,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def detach_state_directory(self):
        path = self.home / MODULE.STATE_DIRECTORY_NAME
        detached = path.with_name(f"{path.name}.detached")
        os.replace(path, detached)
        path.mkdir(mode=0o700)
        return path, detached

    def restore_state_directory(self, path, detached):
        replacement = path.with_name(f"{path.name}.replacement")
        os.replace(path, replacement)
        os.replace(detached, path)

    def status_snapshot_drift(self, kind):
        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        lock = state_directory / MODULE.HOST_MUTATION_LOCK_NAME
        if kind == "lock-replacement":
            replacement = lock.with_name(f"{lock.name}.status-replacement")

            def mutate():
                replacement.write_bytes(b"")
                os.chmod(replacement, 0o600)
                os.replace(replacement, lock)

            return mutate, lambda: None, "status lock identity or access policy"
        if kind == "lock-policy":
            original_mode = stat.S_IMODE(lock.stat().st_mode)

            def mutate():
                os.chmod(lock, original_mode | 0o040)

            def restore():
                os.chmod(lock, original_mode)

            return mutate, restore, "status lock identity or access policy"
        if kind == "state-replacement":
            detached = state_directory.with_name(
                f"{state_directory.name}.status-detached"
            )
            replacement = state_directory.with_name(
                f"{state_directory.name}.replacement"
            )

            def mutate():
                os.replace(state_directory, detached)
                state_directory.mkdir(mode=0o700)

            def restore():
                os.replace(state_directory, replacement)
                os.replace(detached, state_directory)
                replacement.rmdir()

            return mutate, restore, "state directory identity or access policy"
        if kind == "state-policy":
            original_mode = stat.S_IMODE(state_directory.stat().st_mode)

            def mutate():
                os.chmod(state_directory, original_mode | 0o040)

            def restore():
                os.chmod(state_directory, original_mode)

            return mutate, restore, "state directory identity or access policy"
        if kind == "home-replacement":
            detached = self.home.with_name(f"{self.home.name}.status-detached")
            replacement = self.home.with_name(f"{self.home.name}.status-replacement")
            original_mode = stat.S_IMODE(self.home.stat().st_mode)

            def mutate():
                os.replace(self.home, detached)
                self.home.mkdir(mode=original_mode)

            def restore():
                os.replace(self.home, replacement)
                os.replace(detached, self.home)
                replacement.rmdir()

            return mutate, restore, "state directory parent identity or access policy"
        if kind == "home-policy":
            original_mode = stat.S_IMODE(self.home.stat().st_mode)

            def mutate():
                os.chmod(self.home, original_mode | 0o020)

            def restore():
                os.chmod(self.home, original_mode)

            return mutate, restore, "state directory parent identity or access policy"
        raise AssertionError(f"unsupported status snapshot drift: {kind}")

    def replace_state_file_with_same_payload(self, name, payload):
        path = self.home / MODULE.STATE_DIRECTORY_NAME / name
        replacement = path.with_name(f".{path.name}.attacker-replacement")
        replacement.write_bytes(payload)
        os.chmod(replacement, 0o600)
        os.replace(replacement, path)

    def replace_lock_after_one_wait(self, lock, runtime):
        original_flock = MODULE.fcntl.flock
        attempts = 0

        def interleave(fd, operation):
            nonlocal attempts
            if operation == MODULE.fcntl.LOCK_EX | MODULE.fcntl.LOCK_NB:
                attempts += 1
                if attempts == 1:
                    raise BlockingIOError
                result = original_flock(fd, operation)
                replacement = lock.with_name(f"{lock.name}.replacement")
                replacement.write_bytes(b"")
                os.chmod(replacement, 0o600)
                os.replace(replacement, lock)
                return result
            return original_flock(fd, operation)

        runtime.sleep = lambda _seconds: None
        return interleave, lambda: attempts

    def test_checked_inventory_uses_single_direction_controller_graph(self):
        raw = json.loads(INVENTORY.read_text(encoding="utf-8"))
        controller = next(host for host in raw["hosts"] if host["role"] == "gui-controller")
        target = next(host for host in raw["hosts"] if host["role"] == "headless-managed")
        self.assertNotIn("targets", controller)
        parsed = MODULE._parse_inventory(INVENTORY.read_bytes())
        self.assertEqual(
            parsed.hosts[controller["id"]].targets,
            (target["id"],),
        )

    def test_checked_inventory_exact_migration_authority(self):
        raw = json.loads(INVENTORY.read_text(encoding="utf-8"))
        self.assertEqual(
            raw,
            {
                "version": 1,
                "hosts": [
                    {
                        "id": "HOTENG-M-NCQ2",
                        "role": "gui-controller",
                        "username": "hoteng",
                        "uid": 501,
                        "home": "/Users/hoteng",
                    },
                    {
                        "id": "BL-mac-mini-m4-hoteng",
                        "role": "headless-managed",
                        "username": "hoteng",
                        "uid": 502,
                        "home": "/Users/hoteng",
                        "controller": "HOTENG-M-NCQ2",
                        "ssh_alias": "BL-mac-mini-m4-hoteng",
                    },
                ],
            },
        )

    def test_inventory_rejects_controller_targets_field(self):
        raw = self.inventory_data()
        raw["hosts"][0]["targets"] = [self.target_id]
        with self.assertRaisesRegex(MODULE.ControllerError, "unknown=targets"):
            MODULE._parse_inventory(json.dumps(raw).encode("utf-8"))

    def test_inventory_rejects_standalone_targets_field(self):
        raw = self.inventory_data(controller_role="gui-standalone", include_target=False)
        raw["hosts"][0]["targets"] = []
        with self.assertRaisesRegex(MODULE.ControllerError, "unknown=targets"):
            MODULE._parse_inventory(json.dumps(raw).encode("utf-8"))

    def test_inventory_rejects_portable_host_and_alias_collisions(self):
        duplicate_host = self.inventory_data(
            extra_hosts=[
                {
                    "id": "Controller",
                    "role": "gui-standalone",
                    "username": "hoteng",
                    "uid": self.uid,
                    "home": str(self.account_home),
                }
            ]
        )
        duplicate_alias = self.inventory_data(
            extra_hosts=[
                {
                    "id": "second-headless",
                    "role": "headless-managed",
                    "username": "hoteng",
                    "uid": self.uid,
                    "home": str(self.account_home),
                    "controller": self.controller_id,
                    "ssh_alias": "HEADLESS-SSH",
                }
            ]
        )
        for label, raw, pattern in (
            ("host", duplicate_host, "portable duplicate inventory host ids"),
            ("alias", duplicate_alias, "portable duplicate inventory ssh aliases"),
        ):
            with self.subTest(label=label):
                with self.assertRaisesRegex(MODULE.ControllerError, pattern):
                    MODULE._parse_inventory(json.dumps(raw).encode("utf-8"))

    def test_inventory_strict_validation_rejects_malformed_shapes(self):
        cases = {
            "duplicate": b'{"version":1,"version":1,"hosts":[]}',
            "unknown": b'{"version":1,"hosts":[],"extra":true}',
            "type": b'{"version":true,"hosts":[]}',
            "path": json.dumps(
                {
                    "version": 1,
                    "hosts": [
                        {
                            "id": "gui",
                            "role": "gui-standalone",
                            "username": "hoteng",
                            "uid": self.uid,
                            "home": "relative/home",
                        }
                    ],
                }
            ).encode(),
            "role": json.dumps(
                {
                    "version": 1,
                    "hosts": [
                        {
                            "id": "gui",
                            "role": "auto-controller",
                            "username": "hoteng",
                            "uid": self.uid,
                            "home": str(self.account_home),
                        }
                    ],
                }
            ).encode(),
            "graph": json.dumps(
                {
                    "version": 1,
                    "hosts": [
                        {
                            "id": "headless",
                            "role": "headless-managed",
                            "username": "hoteng",
                            "uid": self.uid,
                            "home": str(self.account_home),
                            "controller": "missing",
                            "ssh_alias": "headless",
                        }
                    ],
                }
            ).encode(),
        }
        for label, payload in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(MODULE.ControllerError):
                    MODULE._parse_inventory(payload)

    def test_inventory_precedence_keeps_explicit_headless_role(self):
        self.write_release(
            NEXT_PRIVATE_SHA,
            self.inventory_data(
                controller_id="other-controller",
                target_controller="other-controller",
            ),
        )
        self.switch_current(NEXT_PRIVATE_SHA)
        runtime = self.runtime(candidates=(self.target_id,), gui=True)

        effective = MODULE.activate(
            runtime,
            self.home,
            requested_host_id=self.target_id,
            interval_minutes=30,
        )

        self.assertEqual(effective.entry.role, "headless-managed")
        self.assertFalse(any(len(call) > 1 and call[1] == "install-scheduler" for call in runtime.calls))

    def test_inventory_candidate_precedence_ignores_requested_unlisted_alias(self):
        runtime = self.runtime(
            candidates=("unlisted-alias", self.controller_id),
            gui=True,
        )

        effective = MODULE.activate(
            runtime,
            self.home,
            requested_host_id="unlisted-alias",
            interval_minutes=30,
        )

        self.assertEqual(effective.entry.host_id, self.controller_id)
        self.assertEqual(effective.entry.role, "gui-controller")
        self.assertEqual(effective.source, "inventory")

    def test_inventory_candidate_matching_is_case_insensitive(self):
        runtime = self.runtime(candidates=(self.controller_id.upper(),), gui=True)

        effective = MODULE.activate(
            runtime,
            self.home,
            requested_host_id=self.controller_id.upper(),
            interval_minutes=30,
        )

        self.assertEqual(effective.entry.host_id, self.controller_id)
        validated = MODULE._validate_activation(
            self.home,
            self.account,
            self.current_snapshot(),
            requested_host_id=self.controller_id.upper(),
            runtime=runtime,
        )
        self.assertEqual(validated.entry.host_id, self.controller_id)
        preflight = MODULE._preflight_activation_scope(
            self.home,
            self.account,
            self.current_snapshot(),
            requested_host_id=self.controller_id.upper(),
        )
        self.assertEqual(preflight.entry.host_id, self.controller_id)

    def test_implicit_candidate_receipt_validation_is_case_insensitive(self):
        host_id = "Standalone-Host"
        runtime = self.runtime(candidates=(host_id,), gui=True)
        effective = MODULE.activate(
            runtime,
            self.home,
            requested_host_id=host_id,
            interval_minutes=30,
        )
        self.assertEqual(effective.entry.host_id, host_id)

        validation_runtime = self.runtime(
            candidates=(host_id.casefold(),),
            gui=True,
        )
        validated = MODULE._validate_activation(
            self.home,
            self.account,
            self.current_snapshot(),
            requested_host_id=host_id.casefold(),
            runtime=validation_runtime,
        )
        self.assertEqual(validated.entry.host_id, host_id)
        preflight = MODULE._preflight_activation_scope(
            self.home,
            self.account,
            self.current_snapshot(),
            requested_host_id=host_id.casefold(),
        )
        self.assertEqual(preflight.entry.host_id, host_id)

    def test_multiple_inventory_candidates_are_ambiguous(self):
        second_id = "controller-alias"
        self.write_release(
            NEXT_PRIVATE_SHA,
            self.inventory_data(
                extra_hosts=[
                    {
                        "id": second_id,
                        "role": "gui-standalone",
                        "username": "hoteng",
                        "uid": self.uid,
                        "home": str(self.account_home),
                    }
                ],
            ),
        )
        self.switch_current(NEXT_PRIVATE_SHA)
        runtime = self.runtime(
            candidates=(self.controller_id, second_id),
            gui=True,
        )

        with self.assertRaisesRegex(
            MODULE.ControllerError,
            "multiple inventory-listed ids",
        ):
            MODULE.activate(
                runtime,
                self.home,
                requested_host_id=self.controller_id,
                interval_minutes=30,
            )

        self.assertFalse(
            any(
                len(call) > 1 and call[1] == "install-scheduler"
                for call in runtime.calls
            )
        )

    def test_unlisted_gui_defaults_to_standalone_and_uses_private_runner(self):
        runtime = self.runtime(candidates=("unlisted-gui",), gui=True)

        effective = MODULE.activate(
            runtime,
            self.home,
            requested_host_id="unlisted-gui",
            interval_minutes=45,
        )

        self.assertEqual(effective.entry.role, "gui-standalone")
        install = next(call for call in runtime.calls if len(call) > 1 and call[1] == "install-scheduler")
        runner_index = install.index("--runner") + 1
        self.assertEqual(
            install[runner_index],
            str(self.home / "bin" / "codex-private-macos-sync"),
        )
        self.assertEqual(install[install.index("--interval-minutes") + 1], "45")

    def test_private_runner_precheck_failure_prevents_activation_mutation(self):
        for label, returncode in (("wrong", 1), ("missing", 127)):
            with self.subTest(label=label):
                runtime = self.runtime()
                runtime.verify_results = [
                    MODULE.CommandResult(returncode, b"", label.encode("utf-8")),
                ]

                with self.assertRaisesRegex(
                    MODULE.ControllerError,
                    "private runner verification failed",
                ):
                    self.activate_controller(runtime)

                self.assertFalse(
                    (self.home / MODULE.STATE_DIRECTORY_NAME).exists()
                )
                self.assertFalse(
                    any(
                        len(call) > 1 and call[1] == "install-scheduler"
                        for call in runtime.calls
                    )
                )

    def test_private_runner_postcheck_failure_keeps_activation_in_flight(self):
        runtime = self.runtime()
        runtime.verify_results = [
            MODULE.CommandResult(0, b"ok\n", b""),
            MODULE.CommandResult(1, b"", b"wrong private runner"),
        ]

        with self.assertRaisesRegex(
            MODULE.ControllerError,
            "private runner verification failed",
        ):
            self.activate_controller(runtime)

        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        self.assertFalse((state_directory / MODULE.ACTIVATION_FILE_NAME).exists())
        pending = json.loads(
            (state_directory / MODULE.ACTIVATION_PENDING_FILE_NAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(pending["status"], "in-flight")
        self.assertEqual(
            len(
                [
                    call
                    for call in runtime.calls
                    if len(call) > 1 and call[1] == "verify-overlay"
                ]
            ),
            2,
        )
        command_verbs = [
            call[1]
            for call in runtime.calls
            if len(call) > 1
            and call[0] == self.physical_runner()
        ]
        self.assertLess(
            command_verbs.index("install-scheduler"),
            command_verbs.index("status-scheduler"),
        )
        self.assertLess(
            command_verbs.index("status-scheduler"),
            len(command_verbs) - 1 - command_verbs[::-1].index("verify-overlay"),
        )

    def test_private_runner_verifier_uses_exact_argv_and_fence_sandwich(self):
        runtime = self.runtime()
        fence_events = []

        MODULE._verify_private_runner(
            runtime,
            self.home,
            before_spawn=lambda: fence_events.append("revalidate"),
        )

        self.assertEqual(
            runtime.calls,
            [
                (
                    self.physical_runner(),
                    "verify-overlay",
                    "--home",
                    str(self.home),
                    "--owner",
                    MODULE.PRIVATE_OWNER,
                )
            ],
        )
        expected_interpreter = self.expected_canonical_interpreter()
        self.assertEqual(
            runtime.raw_calls,
            [
                (
                    str(expected_interpreter),
                    *MODULE.CANONICAL_PYTHON_FLAGS,
                    self.physical_runner(),
                    "verify-overlay",
                    "--home",
                    str(self.home),
                    "--owner",
                    MODULE.PRIVATE_OWNER,
                )
            ],
        )
        self.assertEqual(fence_events, ["revalidate"] * 4)

        runtime.calls.clear()
        runtime.verify_results = [MODULE.CommandResult(1, b"", b"wrong runner")]
        fence_events.clear()
        with self.assertRaisesRegex(
            MODULE.ControllerError,
            "private runner verification failed",
        ):
            MODULE._verify_private_runner(
                runtime,
                self.home,
                before_spawn=lambda: fence_events.append("revalidate"),
            )
        self.assertEqual(fence_events, ["revalidate"] * 4)

    def test_private_manifest_packages_independent_runner_attestation_chain(self):
        manifest = json.loads(
            (
                REPO_ROOT / "personal_codex" / "private-sync-manifest.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            manifest["base_release"],
            {
                "repo": "Joey-Tools/codex-toolbox",
                "sha": "598671d0972193bf74f2b076227a269ebacf87b3",
            },
        )
        for path in (
            MODULE.GENERATED_SOURCE_LOCK_PATH,
            MODULE.PRIVATE_SOURCE_LOCK_PATH,
        ):
            self.assertEqual(manifest["reference_only"].count(path), 1)

    def test_private_package_contains_runner_attestation_chain(self):
        output = self.root / "package"
        completed = subprocess.run(
            (
                sys.executable,
                str(REPO_ROOT / "scripts" / "build_personal_codex_package.py"),
                "--repo-root",
                str(REPO_ROOT),
                "--sha",
                PRIVATE_SHA,
                "--output-dir",
                str(output),
                "--manifest",
                "personal_codex/private-sync-manifest.json",
            ),
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        archive_path = output / f"personal-codex-{PRIVATE_SHA}.tar.gz"
        with tarfile.open(archive_path, "r:gz") as archive:
            names = archive.getnames()
        for path in (
            MODULE.GENERATED_SOURCE_LOCK_PATH,
            MODULE.PRIVATE_SOURCE_LOCK_PATH,
        ):
            self.assertEqual(
                sum(name.endswith(f"/{path}") for name in names),
                1,
            )

    def test_runner_attestation_rejects_manifest_and_lock_tamper_before_child(self):
        release_root = self.private_release_root()
        manifest_path = release_root / "personal_codex" / "sync-manifest.json"
        source_path = release_root / MODULE.PRIVATE_SOURCE_LOCK_PATH

        def restore():
            self.write_runner_attestation(self.home, release_root)

        cases = []

        def mismatch_base():
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["base_release"]["sha"] = "f" * 40
            manifest_path.write_text(
                json.dumps(payload, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        cases.append(("base", mismatch_base, "toolbox source does not match"))

        def mismatch_receipt():
            payload = json.loads(source_path.read_text(encoding="utf-8"))
            payload["toolbox_generated_provenance"]["receipt_sha256"] = "f" * 64
            source_path.write_text(
                json.dumps(payload, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        cases.append(("receipt", mismatch_receipt, "does not bind"))

        def wrong_engine_mode():
            self.rewrite_generated_lock(
                lambda payload: payload["files"][0].__setitem__("mode", "0644")
            )

        cases.append(("engine-mode", wrong_engine_mode, "engine is invalid"))

        def duplicate_engine():
            self.rewrite_generated_lock(
                lambda payload: payload["files"].append(dict(payload["files"][0]))
            )

        cases.append(("engine-duplicate", duplicate_engine, "one exact canonical engine"))

        for label, mutate, error in cases:
            with self.subTest(case=label):
                restore()
                mutate()
                runtime = self.runtime()
                with self.assertRaisesRegex(MODULE.ControllerError, error):
                    MODULE._verify_private_runner(runtime, self.home)
                self.assertEqual(runtime.calls, [])
        restore()

    def test_nonmutating_commands_reject_public_live_state_drift(self):
        public_manifest = (
            self.public_runner_path().parents[1]
            / "personal_codex"
            / "sync-manifest.json"
        )
        live = self.home / "bin" / "codex-personal-sync"

        def restore():
            self.switch_public_current(PUBLIC_SHA)
            if live.exists() or live.is_symlink():
                live.unlink()
            live.symlink_to(MODULE.CANONICAL_RUNNER_LIVE_TARGET)
            self.write_runner_attestation(self.home, self.private_release_root())

        def wrong_current():
            self.switch_public_current(NEXT_PUBLIC_SHA)

        def wrong_live_link():
            live.unlink()
            live.symlink_to("../wrong/runner")

        def wrong_public_manifest():
            payload = json.loads(public_manifest.read_text(encoding="utf-8"))
            payload["links"][0]["kind"] = "directory"
            public_manifest.write_text(
                json.dumps(payload, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        for label, mutate in (
            ("current", wrong_current),
            ("live-link", wrong_live_link),
            ("manifest", wrong_public_manifest),
        ):
            with self.subTest(case=label):
                restore()
                mutate()
                runtime = self.runtime()
                with self.assertRaises(MODULE.ControllerError):
                    MODULE._verify_private_runner(runtime, self.home)
                self.assertEqual(runtime.calls, [])
        restore()

    def test_runner_replacement_same_bytes_before_spawn_blocks_child(self):
        runtime = self.runtime()
        runner = self.public_runner_path()
        replaced = False

        def replace_runner(call):
            nonlocal replaced
            if len(call) > 1 and call[1] == "verify-overlay" and not replaced:
                replaced = True
                replacement = runner.with_name("codex_personal_sync.py.next")
                replacement.write_bytes(runner.read_bytes())
                replacement.chmod(0o755)
                os.replace(replacement, runner)

        runtime.before_spawn_hook = replace_runner
        with self.assertRaisesRegex(
            MODULE.ControllerError,
            "identity or access policy changed|object was replaced",
        ):
            MODULE._verify_private_runner(runtime, self.home)

        self.assertTrue(replaced)
        self.assertEqual(runtime.calls, [])

    def test_runner_content_and_access_policy_drift_block_child(self):
        for case in ("content", "mode"):
            with self.subTest(case=case):
                self.write_runner_attestation(self.home, self.private_release_root())
                runner = self.public_runner_path()
                runner.write_bytes(CANONICAL_RUNNER_BYTES)
                runner.chmod(0o755)
                runtime = self.runtime()
                changed = False

                def mutate_runner(call):
                    nonlocal changed
                    if len(call) > 1 and call[1] == "verify-overlay" and not changed:
                        changed = True
                        if case == "content":
                            runner.write_bytes(b"#!/bin/sh\nexit 9\n")
                            runner.chmod(0o755)
                        else:
                            runner.chmod(0o775)

                runtime.before_spawn_hook = mutate_runner
                with self.assertRaises(MODULE.ControllerError):
                    MODULE._verify_private_runner(runtime, self.home)
                self.assertTrue(changed)
                self.assertEqual(runtime.calls, [])
        runner.write_bytes(CANONICAL_RUNNER_BYTES)
        runner.chmod(0o755)

    def test_runner_ancestor_policy_drift_before_spawn_blocks_child(self):
        runtime = self.runtime()
        ancestor = self.public_runner_path().parents[1]
        original_mode = stat.S_IMODE(ancestor.stat().st_mode)
        changed = False

        def chmod_ancestor(call):
            nonlocal changed
            if len(call) > 1 and call[1] == "verify-overlay" and not changed:
                changed = True
                ancestor.chmod(original_mode | 0o020)

        runtime.before_spawn_hook = chmod_ancestor
        try:
            with self.assertRaisesRegex(MODULE.ControllerError, "access policy changed"):
                MODULE._verify_private_runner(runtime, self.home)
        finally:
            ancestor.chmod(original_mode)

        self.assertTrue(changed)
        self.assertEqual(runtime.calls, [])

    def test_runner_descriptor_gid_drift_before_spawn_blocks_child(self):
        runtime = self.runtime()
        runner = self.public_runner_path()
        runner_metadata = runner.stat()
        target_identity = (runner_metadata.st_dev, runner_metadata.st_ino)
        changed_gid = runner_metadata.st_gid + 1
        original_fstat = MODULE.os.fstat
        armed = False

        def fstat_probe(fd):
            result = original_fstat(fd)
            if armed and (result.st_dev, result.st_ino) == target_identity:
                return AccessPolicyMetadata(
                    mode=result.st_mode,
                    uid=result.st_uid,
                    gid=changed_gid,
                    ctime_ns=result.st_ctime_ns,
                    nlink=result.st_nlink,
                    dev=result.st_dev,
                    ino=result.st_ino,
                )
            return result

        def arm_gid_drift(call):
            nonlocal armed
            if len(call) > 1 and call[1] == "verify-overlay":
                armed = True

        runtime.before_spawn_hook = arm_gid_drift
        with mock.patch.object(MODULE.os, "fstat", side_effect=fstat_probe):
            with self.assertRaisesRegex(
                MODULE.ControllerError,
                "identity or access policy changed",
            ):
                MODULE._verify_private_runner(runtime, self.home)

        self.assertTrue(armed)
        self.assertEqual(len(runtime.raw_calls), 1)
        self.assertEqual(runtime.calls, [])

    def test_runner_descriptor_gid_drift_after_child_blocks_result(self):
        runtime = self.runtime()
        runner = self.public_runner_path()
        runner_metadata = runner.stat()
        target_identity = (runner_metadata.st_dev, runner_metadata.st_ino)
        changed_gid = runner_metadata.st_gid + 1
        original_fstat = MODULE.os.fstat
        armed = False

        def fstat_probe(fd):
            result = original_fstat(fd)
            if armed and (result.st_dev, result.st_ino) == target_identity:
                return AccessPolicyMetadata(
                    mode=result.st_mode,
                    uid=result.st_uid,
                    gid=changed_gid,
                    ctime_ns=result.st_ctime_ns,
                    nlink=result.st_nlink,
                    dev=result.st_dev,
                    ino=result.st_ino,
                )
            return result

        def arm_gid_drift():
            nonlocal armed
            armed = True

        runtime.verify_hook = arm_gid_drift
        with mock.patch.object(MODULE.os, "fstat", side_effect=fstat_probe):
            with self.assertRaisesRegex(
                MODULE.ControllerError,
                "identity or access policy changed",
            ):
                MODULE._verify_private_runner(runtime, self.home)

        self.assertTrue(armed)
        self.assertEqual(
            [call[1] for call in runtime.calls],
            ["verify-overlay"],
        )

    def test_account_home_policy_drift_before_spawn_blocks_child(self):
        runtime = self.runtime()
        original_mode = stat.S_IMODE(self.account_home.stat().st_mode)
        changed = False

        def chmod_account_home(call):
            nonlocal changed
            if len(call) > 1 and call[1] == "verify-overlay" and not changed:
                changed = True
                self.account_home.chmod(original_mode | 0o020)

        runtime.before_spawn_hook = chmod_account_home
        try:
            with self.assertRaisesRegex(
                MODULE.ControllerError,
                "identity or access policy changed",
            ):
                MODULE._verify_private_runner(runtime, self.home)
        finally:
            self.account_home.chmod(original_mode)

        self.assertTrue(changed)
        self.assertEqual(runtime.calls, [])

    def test_account_home_replacement_before_spawn_blocks_child(self):
        runtime = self.runtime()
        retained = self.account_home.with_name("hoteng.retained")
        replaced = False

        def replace_account_home(call):
            nonlocal replaced
            if len(call) > 1 and call[1] == "verify-overlay" and not replaced:
                replaced = True
                self.account_home.rename(retained)
                self.account_home.mkdir(mode=0o700)

        runtime.before_spawn_hook = replace_account_home
        try:
            with self.assertRaisesRegex(
                MODULE.ControllerError,
                "directory object was replaced",
            ):
                MODULE._verify_private_runner(runtime, self.home)
        finally:
            if self.account_home.exists():
                self.account_home.rmdir()
            if retained.exists():
                retained.rename(self.account_home)

        self.assertTrue(replaced)
        self.assertEqual(runtime.calls, [])

    def test_interpreter_ancestor_acl_drift_before_spawn_blocks_child(self):
        runtime = self.runtime()
        interpreter = self.expected_canonical_interpreter()
        ancestor = interpreter.parent
        target_identity = (ancestor.stat().st_dev, ancestor.stat().st_ino)
        original = MODULE._validate_fd_access_policy
        armed = False

        def validate(
            fd,
            uid,
            label,
            *,
            error_type=MODULE.ControllerError,
        ):
            metadata = os.fstat(fd)
            if armed and (metadata.st_dev, metadata.st_ino) == target_identity:
                raise error_type(
                    f"{label} extended ACL policy grants a non-owner ALLOW entry"
                )
            return original(
                fd,
                uid,
                label,
                error_type=error_type,
            )

        def arm_acl(call):
            nonlocal armed
            if len(call) > 1 and call[1] == "verify-overlay":
                armed = True

        runtime.before_spawn_hook = arm_acl
        with mock.patch.object(
            MODULE,
            "_validate_fd_access_policy",
            side_effect=validate,
        ):
            with self.assertRaisesRegex(MODULE.ControllerError, "non-owner ALLOW"):
                MODULE._verify_private_runner(runtime, self.home)

        self.assertEqual(runtime.calls, [])

    def test_non_darwin_real_python_resolver_selects_safe_sys_executable(self):
        safe_root = self.root / "default-python-runtime"
        safe_root.mkdir(mode=0o700)
        interpreter = safe_root / "python"
        shutil.copyfile(self.real_python_resolver(), interpreter)
        interpreter.chmod(0o755)

        with (
            mock.patch.object(MODULE.sys, "platform", "linux"),
            mock.patch.object(MODULE.sys, "executable", str(interpreter)),
            mock.patch.object(
                MODULE,
                "_resolved_current_python_executable",
                self.real_python_resolver,
            ),
        ):
            self.assertEqual(
                MODULE._resolved_current_python_executable(),
                interpreter,
            )
            with MODULE._canonical_runner_binding(
                self.home,
                self.uid,
                require_live_state=True,
            ) as binding:
                self.assertEqual(binding.interpreter_file.path, interpreter)

        metadata = interpreter.stat()
        self.assertTrue(stat.S_ISREG(metadata.st_mode))
        self.assertFalse(interpreter.is_symlink())
        self.assertEqual(metadata.st_nlink, 1)

    def test_linux_fixture_selects_safe_copied_interpreter(self):
        if not sys.platform.startswith("linux"):
            self.skipTest("Linux controller compatibility fixture is platform-specific")
        self.assertIsNotNone(self.test_python_executable)
        self.test_python_executable.relative_to(self.root)
        self.assertEqual(
            MODULE._resolved_current_python_executable(),
            self.test_python_executable,
        )
        with MODULE._canonical_runner_binding(
            self.home,
            self.uid,
            require_live_state=True,
        ) as binding:
            self.assertEqual(
                binding.interpreter_file.path,
                self.test_python_executable,
            )

    def test_non_darwin_unsafe_current_interpreter_ancestor_blocks_child(self):
        runtime = self.runtime()
        unsafe_prefix = self.root / "opt"
        interpreter = (
            unsafe_prefix
            / "hostedtoolcache"
            / "Python"
            / "3.9.25"
            / "x64"
            / "bin"
            / "python3.9"
        )
        interpreter.parent.mkdir(parents=True)
        interpreter.write_bytes(b"test hosted interpreter\n")
        interpreter.chmod(0o755)
        unsafe_prefix.chmod(0o775)

        with (
            mock.patch.object(MODULE.sys, "platform", "linux"),
            mock.patch.object(MODULE.sys, "executable", str(interpreter)),
            mock.patch.object(
                MODULE,
                "_resolved_current_python_executable",
                self.real_python_resolver,
            ),
            self.assertRaisesRegex(
                MODULE.ControllerError,
                "canonical runner directory access policy is unsafe",
            ),
        ):
            MODULE._verify_private_runner(runtime, self.home)

        self.assertEqual(runtime.raw_calls, [])
        self.assertEqual(runtime.calls, [])

    def test_interpreter_entry_resolution_drift_before_spawn_blocks_child(self):
        if sys.platform != "darwin":
            self.skipTest("Darwin CLT interpreter entry is platform-specific")
        runtime = self.runtime()
        entry_parent = MODULE.DARWIN_CANONICAL_PYTHON.parent
        entry_parent_identity = (
            entry_parent.stat().st_dev,
            entry_parent.stat().st_ino,
        )
        original_stat = MODULE.os.stat
        armed = False

        def stat_probe(path, *args, **kwargs):
            result = original_stat(path, *args, **kwargs)
            dir_fd = kwargs.get("dir_fd")
            if (
                armed
                and path == MODULE.DARWIN_CANONICAL_PYTHON.name
                and kwargs.get("follow_symlinks") is True
                and dir_fd is not None
            ):
                parent = os.fstat(dir_fd)
                if (parent.st_dev, parent.st_ino) == entry_parent_identity:
                    return AccessPolicyMetadata(
                        mode=stat.S_IFREG | 0o755,
                        uid=0,
                        ctime_ns=result.st_ctime_ns,
                        dev=result.st_dev,
                        ino=result.st_ino + 1,
                        gid=result.st_gid,
                    )
            return result

        def arm_resolution_drift(call):
            nonlocal armed
            if len(call) > 1 and call[1] == "verify-overlay":
                armed = True

        runtime.before_spawn_hook = arm_resolution_drift
        with mock.patch.object(MODULE.os, "stat", side_effect=stat_probe):
            with self.assertRaisesRegex(
                MODULE.ControllerError,
                "does not resolve to the attested object",
            ):
                MODULE._verify_private_runner(runtime, self.home)

        self.assertEqual(runtime.calls, [])

    def test_canonical_interpreter_binding_and_minimum_version(self):
        expected_interpreter = self.expected_canonical_interpreter()
        with MODULE._canonical_runner_binding(
            self.home,
            self.uid,
            require_live_state=True,
        ) as binding:
            self.assertEqual(binding.interpreter_file.path, expected_interpreter)
            if sys.platform == "darwin":
                self.assertIsNotNone(binding.interpreter_entry)
                self.assertEqual(
                    binding.interpreter_entry.path,
                    MODULE.DARWIN_CANONICAL_PYTHON,
                )
                self.assertEqual(
                    binding.interpreter_file.access_policy,
                    (
                        0,
                        "expected-owner-and-no-nonowner-allow-v1",
                    ),
                )

        completed = subprocess.run(
            (
                str(expected_interpreter),
                "-c",
                "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')",
            ),
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        selected_version = tuple(
            int(part) for part in completed.stdout.strip().split(".")
        )
        self.assertGreaterEqual(selected_version, MODULE.MINIMUM_CANONICAL_PYTHON)

    def test_missing_darwin_clt_python_fails_before_child(self):
        if sys.platform != "darwin":
            self.skipTest("Darwin CLT interpreter entry is platform-specific")
        runtime = self.runtime()
        missing = self.root / "missing-clt" / "usr" / "bin" / "python3"
        missing.parent.mkdir(parents=True)

        with mock.patch.object(MODULE, "DARWIN_CANONICAL_PYTHON", missing):
            with self.assertRaisesRegex(
                MODULE.ControllerError,
                "Command Line Tools Python entry is missing or unreadable",
            ):
                MODULE._verify_private_runner(runtime, self.home)

        self.assertEqual(runtime.calls, [])

    def test_canonical_gate_preserves_durable_fence_error_priority(self):
        events = []
        fence_calls = 0

        class DriftingBinding:
            def revalidate(self):
                events.append("runner")
                raise MODULE.ControllerError("injected runner drift")

            def revalidate_held(self):
                self.revalidate()

        def fence():
            nonlocal fence_calls
            fence_calls += 1
            events.append("fence")
            if fence_calls == 2:
                raise MODULE.StatePublicationError("injected durable fence drift")

        with self.assertRaisesRegex(
            MODULE.StatePublicationError,
            "durable fence drift",
        ) as raised:
            MODULE._revalidate_canonical_gate(
                DriftingBinding(),
                fence,
                held_only=False,
            )

        self.assertEqual(events, ["fence", "runner", "fence", "runner"])
        self.assertIsInstance(raised.exception.__cause__, MODULE.ControllerError)
        self.assertNotIsInstance(
            raised.exception.__cause__,
            MODULE.StatePublicationError,
        )

    def test_post_gate_durable_fence_error_outranks_runtime_error(self):
        runtime = self.runtime()
        fence_calls = 0

        def fence():
            nonlocal fence_calls
            fence_calls += 1
            if fence_calls == 3:
                raise MODULE.StatePublicationError("injected post-run fence drift")

        def fail_after_spawn(
            _argv,
            *,
            timeout,
            output_limit,
            before_spawn=None,
        ):
            self.assertGreater(timeout, 0)
            self.assertGreater(output_limit, 0)
            self.assertIsNotNone(before_spawn)
            before_spawn()
            raise MODULE.ControllerError("injected runtime failure")

        with mock.patch.object(runtime, "run", side_effect=fail_after_spawn):
            with self.assertRaisesRegex(
                MODULE.StatePublicationError,
                "post-run fence drift",
            ) as raised:
                MODULE._verify_private_runner(
                    runtime,
                    self.home,
                    before_spawn=fence,
                )

        self.assertEqual(fence_calls, 4)
        self.assertIsInstance(raised.exception.__cause__, MODULE.ControllerError)
        self.assertNotIsInstance(
            raised.exception.__cause__,
            MODULE.StatePublicationError,
        )

    def test_control_flow_error_outranks_post_gate_durable_fence_error(self):
        cases = (
            MODULE._ManagedProcessSignal(MODULE.signal.SIGTERM),
            KeyboardInterrupt("injected keyboard interrupt"),
        )
        for body_error in cases:
            with self.subTest(error=type(body_error).__name__):
                runtime = self.runtime()
                fence_calls = 0

                def fence():
                    nonlocal fence_calls
                    fence_calls += 1
                    if fence_calls == 3:
                        raise MODULE.StatePublicationError(
                            "injected post-run fence drift"
                        )

                def fail_after_spawn(
                    _argv,
                    *,
                    timeout,
                    output_limit,
                    before_spawn=None,
                ):
                    self.assertGreater(timeout, 0)
                    self.assertGreater(output_limit, 0)
                    self.assertIsNotNone(before_spawn)
                    before_spawn()
                    raise body_error

                with mock.patch.object(
                    runtime,
                    "run",
                    side_effect=fail_after_spawn,
                ):
                    with self.assertRaises(type(body_error)) as raised:
                        MODULE._verify_private_runner(
                            runtime,
                            self.home,
                            before_spawn=fence,
                        )

                self.assertIs(raised.exception, body_error)
                self.assertEqual(fence_calls, 4)
                self.assertIsInstance(
                    raised.exception.__cause__,
                    MODULE.StatePublicationError,
                )

    def test_darwin_canonical_python_target_contract_rejects_bad_targets(self):
        versions = MODULE.DARWIN_CANONICAL_PYTHON_VERSIONS
        cases = (
            (
                "absolute-entry",
                "/tmp/python3",
                versions / "3.9" / "bin" / "python3.9",
                "must be relative",
            ),
            (
                "escaped-target",
                "../../unexpected/python3",
                self.root / "python3.9",
                "escapes",
            ),
            (
                "old-version",
                "../../Library/Frameworks/Python3.framework/Versions/3.8/bin/python3",
                versions / "3.8" / "bin" / "python3.8",
                "older than",
            ),
            (
                "mismatched-name",
                "../../Library/Frameworks/Python3.framework/Versions/3.9/bin/python3",
                versions / "3.9" / "bin" / "python3.8",
                "version is invalid",
            ),
        )
        for label, entry_target, resolved, error in cases:
            with self.subTest(case=label):
                with self.assertRaisesRegex(MODULE.ControllerError, error):
                    MODULE._validate_darwin_canonical_python_target(
                        entry_target,
                        resolved,
                    )

    def test_runner_ancestor_deterministic_acl_drift_blocks_child(self):
        runtime = self.runtime()
        ancestor = self.public_runner_path().parents[1]
        target_identity = (ancestor.stat().st_dev, ancestor.stat().st_ino)
        original = MODULE._validate_fd_access_policy
        armed = False

        def validate(
            fd,
            uid,
            label,
            *,
            error_type=MODULE.ControllerError,
        ):
            metadata = os.fstat(fd)
            if armed and (metadata.st_dev, metadata.st_ino) == target_identity:
                raise error_type(
                    f"{label} extended ACL policy grants a non-owner ALLOW entry"
                )
            return original(
                fd,
                uid,
                label,
                error_type=error_type,
            )

        def arm_acl(call):
            nonlocal armed
            if len(call) > 1 and call[1] == "verify-overlay":
                armed = True

        runtime.before_spawn_hook = arm_acl
        with mock.patch.object(
            MODULE,
            "_validate_fd_access_policy",
            side_effect=validate,
        ):
            with self.assertRaisesRegex(MODULE.ControllerError, "non-owner ALLOW"):
                MODULE._verify_private_runner(runtime, self.home)

        self.assertEqual(runtime.calls, [])

    def test_runner_ancestor_child_churn_is_benign(self):
        runtime = self.runtime()
        ancestor = self.public_runner_path().parents[1]
        churned = False

        def add_child(call):
            nonlocal churned
            if len(call) > 1 and call[1] == "verify-overlay" and not churned:
                churned = True
                (ancestor / "benign-child").write_text("benign\n", encoding="utf-8")

        runtime.before_spawn_hook = add_child
        MODULE._verify_private_runner(runtime, self.home)

        self.assertTrue(churned)
        self.assertEqual(len(runtime.calls), 1)

    def test_mutating_sync_can_repair_live_link_then_requires_full_rebind(self):
        live = self.home / "bin" / "codex-personal-sync"
        live.unlink()
        live.symlink_to("../wrong/runner")
        runtime = self.runtime()

        def repair():
            live.unlink()
            live.symlink_to(MODULE.CANONICAL_RUNNER_LIVE_TARGET)

        runtime.run_scheduled_hook = repair
        MODULE._run_local_sync(runtime, self.home)

        self.assertEqual([call[1] for call in runtime.calls], ["run-scheduled"])
        self.assertEqual(runtime.calls[0][0], self.physical_runner())

    def test_mutating_sync_rebind_failure_stops_before_release_identities(self):
        runtime = self.runtime(candidates=(self.target_id,), gui=False)
        MODULE.activate(
            runtime,
            self.home,
            requested_host_id=self.target_id,
            interval_minutes=30,
        )
        runtime.calls.clear()
        self.write_release(NEXT_PRIVATE_SHA, self.inventory_data())
        self.rewrite_generated_lock(
            lambda payload: payload["files"][0].__setitem__("sha256", "f" * 64),
            NEXT_PRIVATE_SHA,
        )

        def install_untrusted_next():
            self.switch_current(NEXT_PRIVATE_SHA)
            runtime.identity_pair = self.desired(NEXT_PRIVATE_SHA, NEXT_PRIVATE_TREE)

        runtime.run_scheduled_hook = install_untrusted_next
        with self.assertRaisesRegex(MODULE.ControllerError, "runner hash"):
            MODULE.remote_apply(
                runtime,
                self.home,
                host_id=self.target_id,
                controller_id=self.controller_id,
                expected=self.desired(NEXT_PRIVATE_SHA, NEXT_PRIVATE_TREE),
            )

        self.assertEqual(
            [call[1] for call in runtime.calls if len(call) > 1],
            ["run-scheduled"],
        )
        self.assertEqual(self.operation_state("remote-apply")["status"], "retryable")

    def test_mutating_sync_revalidates_held_old_runner_before_identities(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        runner = self.public_runner_path()

        def tamper_held_runner():
            runner.write_bytes(b"#!/bin/sh\nexit 9\n")
            runner.chmod(0o755)

        runtime.run_scheduled_hook = tamper_held_runner
        try:
            with self.assertRaisesRegex(MODULE.ControllerError, "content changed"):
                MODULE.controller_run(runtime, self.home, strict=False)
        finally:
            runner.write_bytes(CANONICAL_RUNNER_BYTES)
            runner.chmod(0o755)

        self.assertEqual(
            [call[1] for call in runtime.calls if len(call) > 1],
            ["run-scheduled"],
        )
        self.assertEqual(self.ssh_calls(runtime), [])
        self.assertEqual(
            self.operation_state("controller-run")["status"],
            "retryable",
        )

    def test_upgrade_uses_new_physical_runner_before_identities_and_fanout(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        runtime.before_spawn_presence.clear()
        self.write_release(
            NEXT_PRIVATE_SHA,
            self.inventory_data(),
            public_sha=NEXT_PUBLIC_SHA,
            runner_bytes=NEXT_CANONICAL_RUNNER_BYTES,
        )
        desired = {
            "public": {"sha": NEXT_PUBLIC_SHA, "tree_sha256": PUBLIC_TREE},
            "private": {
                "sha": NEXT_PRIVATE_SHA,
                "tree_sha256": NEXT_PRIVATE_TREE,
            },
        }

        def install_next_pair():
            self.switch_current(NEXT_PRIVATE_SHA)
            self.switch_public_current(NEXT_PUBLIC_SHA)
            runtime.identity_pair = desired

        runtime.run_scheduled_hook = install_next_pair
        runtime.ssh_factory = lambda _call: self.json_result(
            self.success_receipt(desired=desired)
        )

        self.assertTrue(MODULE.controller_run(runtime, self.home, strict=False))

        canonical = [call for call in runtime.calls if len(call) > 1 and call[0] != "/usr/bin/ssh"]
        self.assertEqual(canonical[0][1], "run-scheduled")
        self.assertEqual(canonical[0][0], self.physical_runner())
        identities = [call for call in canonical if call[1] == "release-identities"]
        self.assertTrue(identities)
        self.assertTrue(
            all(call[0] == self.physical_runner(public_sha=NEXT_PUBLIC_SHA) for call in identities)
        )
        self.assertTrue(
            all(present for call, present in runtime.before_spawn_presence if len(call) > 1)
        )

    def test_activation_receipt_binds_scheduler_interval_by_role(self):
        gui_runtime = self.runtime()
        effective = MODULE.activate(
            gui_runtime,
            self.home,
            requested_host_id=self.controller_id,
            interval_minutes=45,
        )
        receipt_path = (
            self.home / MODULE.STATE_DIRECTORY_NAME / MODULE.ACTIVATION_FILE_NAME
        )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["interval_minutes"], 45)
        self.assertEqual(effective.interval_minutes, 45)

        headless_root = self.root / "headless-account"
        headless_home = headless_root / ".codex"
        headless_home.mkdir(parents=True)
        headless_account = MODULE.Account("hoteng", self.uid, headless_root)
        original_account = self.account
        original_account_home = self.account_home
        original_home = self.home
        try:
            self.account = headless_account
            self.account_home = headless_root
            self.home = headless_home
            self.write_release(
                PRIVATE_SHA,
                self.inventory_data(),
            )
            self.switch_current(PRIVATE_SHA)
            headless_runtime = self.runtime(
                candidates=(self.target_id,),
                gui=False,
            )
            headless_effective = MODULE.activate(
                headless_runtime,
                self.home,
                requested_host_id=self.target_id,
                interval_minutes=99,
            )
            headless_receipt = json.loads(
                (
                    self.home
                    / MODULE.STATE_DIRECTORY_NAME
                    / MODULE.ACTIVATION_FILE_NAME
                ).read_text(encoding="utf-8")
            )
            self.assertIsNone(headless_receipt["interval_minutes"])
            self.assertIsNone(headless_effective.interval_minutes)
        finally:
            self.account = original_account
            self.account_home = original_account_home
            self.home = original_home

    def test_activation_receipt_rejects_invalid_role_interval_shapes(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        receipt_path = (
            self.home / MODULE.STATE_DIRECTORY_NAME / MODULE.ACTIVATION_FILE_NAME
        )
        gui_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        for invalid in (True, 0, 7 * 24 * 60 + 1, None):
            with self.subTest(role="gui-controller", interval=invalid):
                candidate = dict(gui_receipt)
                candidate["interval_minutes"] = invalid
                with self.assertRaisesRegex(
                    MODULE.ControllerError,
                    "GUI activation scheduler interval is invalid",
                ):
                    MODULE._parse_activation(
                        MODULE._canonical_json_bytes(candidate)
                    )

        snapshot = self.current_snapshot()
        headless = snapshot.inventory.hosts[self.target_id]
        headless_payload = MODULE._activation_payload(
            MODULE.EffectiveEntry(headless, "inventory"),
            self.account,
            self.home,
            runtime.machine_identity_sha256(),
            None,
        )
        headless_payload["interval_minutes"] = 30
        with self.assertRaisesRegex(
            MODULE.ControllerError,
            "headless activation scheduler interval must be null",
        ):
            MODULE._parse_activation(
                MODULE._canonical_json_bytes(headless_payload)
            )

    def test_activation_rejects_scheduler_interval_mismatch(self):
        runtime = self.runtime()

        def install_wrong_interval(call):
            runtime.scheduler_state = {
                "runner": call[call.index("--runner") + 1],
                "interval_minutes": "45",
                "mode": call[call.index("--mode") + 1],
                "repo": call[call.index("--repo") + 1],
                "base_repo": call[call.index("--base-repo") + 1],
                "owner": call[call.index("--owner") + 1],
            }
            return MODULE.CommandResult(0, b"ok\n", b"")

        runtime.install_factory = install_wrong_interval
        with self.assertRaisesRegex(
            MODULE.ControllerError,
            "scheduler-interval-mismatch",
        ):
            self.activate_controller(runtime)

        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        self.assertFalse((state_directory / MODULE.ACTIVATION_FILE_NAME).exists())
        pending = json.loads(
            (state_directory / MODULE.ACTIVATION_PENDING_FILE_NAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(pending["status"], "in-flight")
        self.assertEqual(
            len(
                [
                    call
                    for call in runtime.calls
                    if len(call) > 1 and call[1] == "verify-overlay"
                ]
            ),
            1,
        )

    def test_unlisted_headless_requires_explicit_role_activation(self):
        runtime = self.runtime(candidates=("unlisted-headless",), gui=False)
        with self.assertRaisesRegex(MODULE.ControllerError, "role-activation-required"):
            MODULE.activate(
                runtime,
                self.home,
                requested_host_id="unlisted-headless",
                interval_minutes=30,
            )
        self.assertFalse(
            (
                self.home
                / MODULE.STATE_DIRECTORY_NAME
                / MODULE.ACTIVATION_FILE_NAME
            ).exists()
        )

    def test_explicit_gui_role_requires_live_aqua_before_receipt_or_scheduler(self):
        runtime = self.runtime(gui=False)
        with self.assertRaisesRegex(MODULE.ControllerError, "gui-session-required"):
            self.activate_controller(runtime)
        self.assertFalse(
            (
                self.home
                / MODULE.STATE_DIRECTORY_NAME
                / MODULE.ACTIVATION_FILE_NAME
            ).exists()
        )
        self.assertFalse(
            any(
                len(call) > 1 and call[1] == "install-scheduler"
                for call in runtime.calls
            )
        )

    def test_controller_activation_uses_private_wrapper_aqua_runner(self):
        runtime = self.runtime()
        effective = self.activate_controller(runtime)

        self.assertEqual(effective.entry.role, "gui-controller")
        install = next(call for call in runtime.calls if len(call) > 1 and call[1] == "install-scheduler")
        self.assertEqual(install[0], self.physical_runner())
        self.assertEqual(install[install.index("--platform") + 1], "macos")
        self.assertEqual(
            install[install.index("--runner") + 1],
            str(self.home / "bin" / "codex-private-macos-sync"),
        )
        receipt = self.home / MODULE.STATE_DIRECTORY_NAME / MODULE.ACTIVATION_FILE_NAME
        self.assertEqual(stat.S_IMODE(receipt.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(receipt.parent.stat().st_mode), 0o700)
        install_calls = [
            call
            for call in runtime.calls
            if len(call) > 1 and call[1] == "install-scheduler"
        ]
        self.assertEqual(len(install_calls), 1)
        self.assertNotIn("--no-enable", install_calls[0])

    def test_gui_activation_install_failure_does_not_publish_receipt(self):
        runtime = self.runtime()
        runtime.install_results = [
            MODULE.CommandResult(1, b"", b"enable failed"),
        ]

        with self.assertRaisesRegex(MODULE.ControllerError, "activation failed"):
            self.activate_controller(runtime)

        receipt = self.home / MODULE.STATE_DIRECTORY_NAME / MODULE.ACTIVATION_FILE_NAME
        self.assertFalse(receipt.exists())
        pending = (
            self.home
            / MODULE.STATE_DIRECTORY_NAME
            / MODULE.ACTIVATION_PENDING_FILE_NAME
        )
        self.assertTrue(pending.exists())

    def test_gui_activation_requires_post_install_scheduler_commit_proof(self):
        runtime = self.runtime()
        runtime.install_factory = lambda _call: MODULE.CommandResult(0, b"ok\n", b"")

        with self.assertRaisesRegex(
            MODULE.ControllerError,
            "scheduler activation could not be proved committed",
        ):
            self.activate_controller(runtime)

        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        self.assertFalse((state_directory / MODULE.ACTIVATION_FILE_NAME).exists())
        pending = json.loads(
            (state_directory / MODULE.ACTIVATION_PENDING_FILE_NAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(pending["status"], "in-flight")
        self.assertEqual(
            len(
                [
                    call
                    for call in runtime.calls
                    if len(call) > 1 and call[1] == "install-scheduler"
                ]
            ),
            1,
        )

    def test_activation_audits_orphan_blockers_after_scheduler_proof(self):
        inserted = False
        attack_enabled = False

        class PostSchedulerOrphanRuntime(FakeRuntime):
            def run(
                inner_self,
                argv,
                *,
                timeout,
                output_limit,
                before_spawn=None,
            ):
                nonlocal inserted
                result = super().run(
                    argv,
                    timeout=timeout,
                    output_limit=output_limit,
                    before_spawn=before_spawn,
                )
                call = decode_runtime_call(argv)
                if (
                    attack_enabled
                    and len(call) > 1
                    and call[1] == "status-scheduler"
                    and not inserted
                ):
                    self.write_target_fence("retired-target")
                    inserted = True
                return result

        runtime = PostSchedulerOrphanRuntime(
            self.account,
            candidates=(self.controller_id,),
            gui=True,
        )
        self.activate_controller(runtime)
        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        receipt = state_directory / MODULE.ACTIVATION_FILE_NAME
        old_receipt = receipt.read_bytes()
        runtime.calls.clear()
        attack_enabled = True

        with mock.patch.object(
            MODULE,
            "_publish_activation",
            wraps=MODULE._publish_activation,
        ) as publish_activation:
            with self.assertRaisesRegex(
                MODULE.ControllerError,
                "target is quarantined",
            ):
                self.activate_controller(runtime)
        publish_activation.assert_not_called()

        self.assertTrue(inserted)
        self.assertEqual(receipt.read_bytes(), old_receipt)
        pending = json.loads(
            (state_directory / MODULE.ACTIVATION_PENDING_FILE_NAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(pending["status"], "in-flight")
        self.assertEqual(
            len(
                [
                    call
                    for call in runtime.calls
                    if len(call) > 1 and call[1] == "install-scheduler"
                ]
            ),
            1,
        )

    def test_headless_activation_requires_scheduler_absence(self):
        runtime = self.runtime(candidates=(self.target_id,), gui=False)
        runtime.scheduler_state = {
            "runner": str(self.home / "bin" / "codex-private-macos-sync"),
            "interval_minutes": "30",
            "mode": "private",
            "repo": MODULE.PRIVATE_REPO,
            "base_repo": MODULE.PUBLIC_REPO,
            "owner": MODULE.PRIVATE_OWNER,
        }

        with self.assertRaisesRegex(
            MODULE.ControllerError,
            "scheduler activation could not be proved committed",
        ):
            MODULE.activate(
                runtime,
                self.home,
                requested_host_id=self.target_id,
                interval_minutes=30,
            )

        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        self.assertFalse((state_directory / MODULE.ACTIVATION_FILE_NAME).exists())
        pending = json.loads(
            (state_directory / MODULE.ACTIVATION_PENDING_FILE_NAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(pending["status"], "in-flight")
        self.assertFalse(
            any(
                len(call) > 1 and call[1] == "install-scheduler"
                for call in runtime.calls
            )
        )

    def test_activation_audits_blocking_orphan_before_alias_rebind(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        old_receipt = (
            self.home
            / MODULE.STATE_DIRECTORY_NAME
            / MODULE.ACTIVATION_FILE_NAME
        ).read_bytes()
        self.write_target_fence(self.target_id)
        replacement_id = "replacement-headless"
        replacement_inventory = self.inventory_data(
            include_target=False,
            extra_hosts=[
                {
                    "id": replacement_id,
                    "role": "headless-managed",
                    "username": "hoteng",
                    "uid": self.uid,
                    "home": str(self.account_home),
                    "controller": self.controller_id,
                    "ssh_alias": "headless-ssh",
                }
            ],
        )
        self.write_release(NEXT_PRIVATE_SHA, replacement_inventory)
        self.switch_current(NEXT_PRIVATE_SHA)
        runtime.calls.clear()

        with self.assertRaisesRegex(
            MODULE.ControllerError,
            "process-cleanup-inconclusive: target is quarantined",
        ):
            self.activate_controller(runtime)

        self.assertFalse(
            any(
                len(call) > 1 and call[1] == "install-scheduler"
                for call in runtime.calls
            )
        )
        receipt = (
            self.home
            / MODULE.STATE_DIRECTORY_NAME
            / MODULE.ACTIVATION_FILE_NAME
        )
        self.assertEqual(receipt.read_bytes(), old_receipt)

    def test_activation_allows_retryable_orphan_target_state(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        self.write_target_fence(
            "retired-target",
            last_error="process-retryable",
        )
        runtime.calls.clear()

        effective = self.activate_controller(runtime)

        self.assertEqual(effective.entry.role, "gui-controller")
        self.assertEqual(
            len(
                [
                    call
                    for call in runtime.calls
                    if len(call) > 1 and call[1] == "install-scheduler"
                ]
            ),
            1,
        )

    def test_activation_rebinds_safe_target_state_controller_before_installer(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        runtime.ssh_results = [self.json_result(self.success_receipt())]
        self.assertTrue(MODULE.controller_run(runtime, self.home, strict=False))
        old_state = json.loads(self.state_path().read_text(encoding="utf-8"))

        next_controller_id = "replacement-controller"
        self.write_release(
            NEXT_PRIVATE_SHA,
            self.inventory_data(
                controller_id=next_controller_id,
                target_controller=next_controller_id,
            ),
        )
        self.switch_current(NEXT_PRIVATE_SHA)
        next_runtime = self.runtime(
            candidates=(next_controller_id,),
            gui=True,
        )
        observed = None

        def observe_rebind_before_installer(call):
            nonlocal observed
            if len(call) > 1 and call[1] == "install-scheduler":
                observed = json.loads(
                    self.state_path().read_text(encoding="utf-8")
                )

        next_runtime.before_spawn_hook = observe_rebind_before_installer
        effective = MODULE.activate(
            next_runtime,
            self.home,
            requested_host_id=next_controller_id,
            interval_minutes=30,
        )

        self.assertEqual(effective.entry.host_id, next_controller_id)
        self.assertIsNotNone(observed)
        assert observed is not None
        self.assertEqual(observed["controller_id"], next_controller_id)
        self.assertTrue(observed["pending"])
        self.assertEqual(observed["last_error"], "local-scope-changed")
        self.assertEqual(observed["desired"], old_state["desired"])
        self.assertEqual(observed["confirmed"], old_state["confirmed"])
        self.assertEqual(observed["generation"], old_state["generation"] + 1)
        self.assertEqual(
            json.loads(self.state_path().read_text(encoding="utf-8")),
            observed,
        )

    def test_target_controller_rebind_cas_race_fails_closed(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        runtime.ssh_results = [self.json_result(self.success_receipt())]
        self.assertTrue(MODULE.controller_run(runtime, self.home, strict=False))
        old_state = json.loads(self.state_path().read_text(encoding="utf-8"))

        next_controller_id = "replacement-controller"
        self.write_release(
            NEXT_PRIVATE_SHA,
            self.inventory_data(
                controller_id=next_controller_id,
                target_controller=next_controller_id,
            ),
        )
        self.switch_current(NEXT_PRIVATE_SHA)
        next_runtime = self.runtime(
            candidates=(next_controller_id,),
            gui=True,
        )
        original_publish = MODULE._atomic_publish
        concurrent = dict(old_state)
        concurrent["generation"] = old_state["generation"] + 7
        concurrent_payload = MODULE._canonical_json_bytes(concurrent)
        raced = False

        def race_target_rebind(directory_fd, name, payload, uid, expected):
            nonlocal raced
            parsed = json.loads(payload.decode("utf-8"))
            if (
                name == f"target-{self.target_id}.json"
                and parsed.get("controller_id") == next_controller_id
                and parsed.get("last_error") == "local-scope-changed"
                and not raced
            ):
                raced = True
                self.state_path().write_bytes(concurrent_payload)
                os.chmod(self.state_path(), 0o600)
            return original_publish(
                directory_fd,
                name,
                payload,
                uid,
                expected,
            )

        with mock.patch.object(
            MODULE,
            "_atomic_publish",
            side_effect=race_target_rebind,
        ):
            with self.assertRaisesRegex(
                MODULE.StatePublicationError,
                "compare-and-swap failed",
            ):
                MODULE.activate(
                    next_runtime,
                    self.home,
                    requested_host_id=next_controller_id,
                    interval_minutes=30,
                )

        self.assertTrue(raced)
        self.assertEqual(self.state_path().read_bytes(), concurrent_payload)
        self.assertFalse(
            any(
                len(call) > 1 and call[1] == "install-scheduler"
                for call in next_runtime.calls
            )
        )
        pending = json.loads(
            (
                self.home
                / MODULE.STATE_DIRECTORY_NAME
                / MODULE.ACTIVATION_PENDING_FILE_NAME
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(pending["status"], "in-flight")

    def test_status_reports_stale_target_controller_as_scope_changed(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        runtime.ssh_results = [self.json_result(self.success_receipt())]
        self.assertTrue(MODULE.controller_run(runtime, self.home, strict=False))
        self.rewrite_target_controller("retired-controller")
        stale_payload = self.state_path().read_bytes()

        payload, readable = MODULE._status_payload(runtime, self.home)

        self.assertTrue(readable)
        self.assertFalse(payload["operational_error"])
        self.assertTrue(payload["degraded"])
        self.assertEqual(payload["targets"][0]["reason"], "scope-changed")
        self.assertEqual(self.state_path().read_bytes(), stale_payload)

        pending_stale = json.loads(stale_payload.decode("utf-8"))
        pending_stale["pending"] = True
        pending_stale["last_error"] = "process-retryable"
        pending_payload = MODULE._canonical_json_bytes(pending_stale)
        self.state_path().write_bytes(pending_payload)
        os.chmod(self.state_path(), 0o600)
        payload, readable = MODULE._status_payload(runtime, self.home)
        self.assertTrue(readable)
        self.assertFalse(payload["operational_error"])
        self.assertEqual(payload["targets"][0]["reason"], "scope-changed")
        self.assertEqual(self.state_path().read_bytes(), pending_payload)

        for blocker, expected_reason in (
            (None, "legacy-pending"),
            ("process-in-flight", "process-in-flight"),
            (
                "process-cleanup-inconclusive",
                "process-cleanup-inconclusive",
            ),
        ):
            with self.subTest(blocker=blocker):
                blocked_stale = dict(pending_stale)
                blocked_stale["last_error"] = blocker
                blocked_payload = MODULE._canonical_json_bytes(blocked_stale)
                self.state_path().write_bytes(blocked_payload)
                os.chmod(self.state_path(), 0o600)
                payload, readable = MODULE._status_payload(runtime, self.home)
                self.assertTrue(readable)
                self.assertFalse(payload["operational_error"])
                self.assertEqual(
                    payload["targets"][0]["reason"],
                    expected_reason,
                )
                self.assertEqual(self.state_path().read_bytes(), blocked_payload)

    def test_controller_run_and_force_rebind_safe_target_controller(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        runtime.ssh_results = [self.json_result(self.success_receipt())]
        self.assertTrue(MODULE.controller_run(runtime, self.home, strict=False))

        self.rewrite_target_controller("retired-controller")
        runtime.calls.clear()
        runtime.ssh_results = [self.json_result(self.success_receipt())]
        self.assertTrue(MODULE.controller_run(runtime, self.home, strict=False))
        self.assertEqual(len(self.ssh_calls(runtime)), 1)
        rebound = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertEqual(rebound["controller_id"], self.controller_id)
        self.assertFalse(rebound["pending"])
        self.assertIsNone(rebound["last_error"])

        self.rewrite_target_controller("retired-controller")
        runtime.calls.clear()
        runtime.ssh_results = [self.json_result(self.success_receipt())]
        self.assertTrue(
            MODULE.sync_target(
                runtime,
                self.home,
                target_id=self.target_id,
                force=True,
                strict=False,
            )
        )
        self.assertEqual(len(self.ssh_calls(runtime)), 1)
        force_rebound = json.loads(
            self.state_path().read_text(encoding="utf-8")
        )
        self.assertEqual(force_rebound["controller_id"], self.controller_id)
        self.assertFalse(force_rebound["pending"])
        self.assertIsNone(force_rebound["last_error"])

    def test_activation_fails_on_malformed_or_concurrently_changing_orphan_state(self):
        for case in ("malformed", "name-set-drift"):
            with self.subTest(case=case):
                runtime = self.runtime()
                self.activate_controller(runtime)
                runtime.calls.clear()
                if case == "malformed":
                    path = (
                        self.home
                        / MODULE.STATE_DIRECTORY_NAME
                        / "target-malformed.json"
                    )
                    path.write_text("{}\n", encoding="utf-8")
                    os.chmod(path, 0o600)
                    context = mock.patch.object(
                        MODULE,
                        "_read_bound_state_file",
                        wraps=MODULE._read_bound_state_file,
                    )
                    expected = MODULE.ControllerError
                else:
                    self.write_target_fence(
                        "retired-target",
                        last_error="process-retryable",
                    )
                    original_read = MODULE._read_bound_state_file
                    inserted = False

                    def insert_during_read(
                        directory_fd,
                        name,
                        uid,
                        *,
                        missing_ok,
                    ):
                        nonlocal inserted
                        result = original_read(
                            directory_fd,
                            name,
                            uid,
                            missing_ok=missing_ok,
                        )
                        if name == "target-retired-target.json" and not inserted:
                            inserted = True
                            self.write_target_fence(
                                "new-orphan",
                                last_error="process-retryable",
                            )
                        return result

                    context = mock.patch.object(
                        MODULE,
                        "_read_bound_state_file",
                        side_effect=insert_during_read,
                    )
                    expected = MODULE.StatePublicationError

                with context:
                    with self.assertRaises(expected):
                        self.activate_controller(runtime)
                self.assertFalse(
                    any(
                        len(call) > 1 and call[1] == "install-scheduler"
                        for call in runtime.calls
                    )
                )

                # Each subtest needs an independent state tree because both
                # failures deliberately leave evidence behind.
                if case == "malformed":
                    self.tearDown()
                    self.assertIs(
                        MODULE._resolved_current_python_executable,
                        self.real_python_resolver,
                    )
                    self.setUp()

    def test_activation_process_cleanup_quarantine_blocks_installer_retry(self):
        runtime = self.runtime()
        installer_calls = []

        def cleanup_inconclusive(call):
            installer_calls.append(call)
            raise MODULE.ProcessCleanupInconclusiveError(
                "installer process-group cleanup was inconclusive"
            )

        runtime.install_factory = cleanup_inconclusive
        with self.assertRaises(MODULE.ProcessCleanupInconclusiveError):
            self.activate_controller(runtime)

        pending = (
            self.home
            / MODULE.STATE_DIRECTORY_NAME
            / MODULE.ACTIVATION_PENDING_FILE_NAME
        )
        self.assertEqual(
            json.loads(pending.read_text(encoding="utf-8"))["status"],
            "process-cleanup-inconclusive",
        )
        status, readable = MODULE._status_payload(runtime, self.home)
        self.assertTrue(readable)
        self.assertFalse(status["operational_error"])
        self.assertTrue(status["degraded"])
        self.assertEqual(
            status["activation"]["status"],
            "process-cleanup-inconclusive",
        )
        self.assertEqual(
            status["host_mutation"]["status"],
            "process-cleanup-inconclusive",
        )
        with mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(
                MODULE.main(
                    ["status", "--home", str(self.home), "--json", "--strict"],
                    runtime,
                ),
                2,
            )
        with self.assertRaisesRegex(
            MODULE.ControllerError,
            "host mutation is blocked by a durable process fence",
        ):
            self.activate_controller(runtime)
        self.assertEqual(len(installer_calls), 1)

    def test_activation_quarantine_publication_failure_keeps_in_flight_fence(self):
        runtime = self.runtime()
        installer_calls = []

        def cleanup_inconclusive(call):
            installer_calls.append(call)
            raise MODULE.ProcessCleanupInconclusiveError(
                "installer process-group cleanup was inconclusive"
            )

        runtime.install_factory = cleanup_inconclusive
        with mock.patch.object(
            MODULE,
            "_quarantine_activation_process_cleanup",
            side_effect=MODULE.StatePublicationError(
                "injected activation quarantine publication failure"
            ),
        ):
            with self.assertRaisesRegex(
                MODULE.ProcessCleanupInconclusiveError,
                "cleanup was inconclusive.*quarantine publication failed",
            ) as raised:
                self.activate_controller(runtime)
        self.assertIsInstance(raised.exception.__cause__, MODULE.StatePublicationError)

        pending = (
            self.home
            / MODULE.STATE_DIRECTORY_NAME
            / MODULE.ACTIVATION_PENDING_FILE_NAME
        )
        self.assertEqual(
            json.loads(pending.read_text(encoding="utf-8"))["status"],
            "in-flight",
        )
        self.assertEqual(
            self.operation_state("activation")["status"],
            "process-cleanup-inconclusive",
        )
        with self.assertRaisesRegex(
            MODULE.ProcessCleanupInconclusiveError,
            "durable process fence from activation",
        ):
            self.activate_controller(runtime)
        self.assertEqual(len(installer_calls), 1)

    def test_activation_quarantine_io_error_preserves_cleanup_priority(self):
        runtime = self.runtime()

        def cleanup_inconclusive(_call):
            raise MODULE.ProcessCleanupInconclusiveError(
                "installer process-group cleanup was inconclusive"
            )

        runtime.install_factory = cleanup_inconclusive
        with mock.patch.object(
            MODULE,
            "_quarantine_activation_process_cleanup",
            side_effect=OSError("injected quarantine I/O failure"),
        ):
            with self.assertRaisesRegex(
                MODULE.ProcessCleanupInconclusiveError,
                "cleanup was inconclusive.*quarantine publication failed",
            ) as raised:
                self.activate_controller(runtime)

        self.assertIsInstance(raised.exception.__cause__, OSError)
        pending = (
            self.home
            / MODULE.STATE_DIRECTORY_NAME
            / MODULE.ACTIVATION_PENDING_FILE_NAME
        )
        self.assertEqual(
            json.loads(pending.read_text(encoding="utf-8"))["status"],
            "in-flight",
        )
        self.assertEqual(
            self.operation_state("activation")["status"],
            "process-cleanup-inconclusive",
        )

    def test_activation_quarantine_signal_preserves_cleanup_priority(self):
        runtime = self.runtime()

        def cleanup_inconclusive(_call):
            raise MODULE.ProcessCleanupInconclusiveError(
                "installer process-group cleanup was inconclusive"
            )

        runtime.install_factory = cleanup_inconclusive
        quarantine_signal = MODULE._ManagedProcessSignal(MODULE.signal.SIGTERM)
        with mock.patch.object(
            MODULE,
            "_quarantine_activation_process_cleanup",
            side_effect=quarantine_signal,
        ):
            with self.assertRaises(
                MODULE.ProcessCleanupInconclusiveError,
            ) as raised:
                self.activate_controller(runtime)

        self.assertIs(raised.exception.__cause__, quarantine_signal)
        self.assertEqual(
            self.operation_state("activation")["status"],
            "process-cleanup-inconclusive",
        )

    def test_legacy_activation_pending_migrates_fail_closed(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        pending = (
            self.home
            / MODULE.STATE_DIRECTORY_NAME
            / MODULE.ACTIVATION_PENDING_FILE_NAME
        )
        pending.write_text(
            json.dumps(
                {
                    "version": 1,
                    "status": "pending",
                    "receipt_sha256": "a" * 64,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.chmod(pending, 0o600)

        with self.assertRaisesRegex(MODULE.ControllerError, "legacy-pending"):
            self.activate_controller(runtime)

        self.assertFalse(
            any(
                len(call) > 1 and call[1] == "install-scheduler"
                for call in runtime.calls
            )
        )

    def test_managed_signal_maps_to_exit_only_after_activation_state_and_unlock(self):
        runtime = self.runtime()

        def interrupt(_call):
            raise MODULE._ManagedProcessSignal(MODULE.signal.SIGTERM)

        runtime.install_factory = interrupt
        exit_code = MODULE.main(
            [
                "activate",
                "--home",
                str(self.home),
                "--host-id",
                self.controller_id,
            ],
            runtime,
        )

        self.assertEqual(exit_code, 128 + MODULE.signal.SIGTERM)
        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        pending = json.loads(
            (state_directory / MODULE.ACTIVATION_PENDING_FILE_NAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(pending["status"], "retryable")
        lock_fd = os.open(state_directory / "activation.lock", os.O_RDWR)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

    def test_gui_reactivation_install_failure_leaves_previous_receipt(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        receipt = self.home / MODULE.STATE_DIRECTORY_NAME / MODULE.ACTIVATION_FILE_NAME
        previous = receipt.read_bytes()
        runtime.machine_digest = "e" * 64
        runtime.install_results = [
            MODULE.CommandResult(1, b"", b"enable failed"),
        ]

        with self.assertRaisesRegex(MODULE.ControllerError, "activation failed"):
            MODULE.activate(
                runtime,
                self.home,
                requested_host_id=self.controller_id,
                interval_minutes=45,
            )

        self.assertEqual(receipt.read_bytes(), previous)
        pending = (
            self.home
            / MODULE.STATE_DIRECTORY_NAME
            / MODULE.ACTIVATION_PENDING_FILE_NAME
        )
        self.assertTrue(pending.exists())
        with self.assertRaisesRegex(
            MODULE.ControllerError,
            "activation publication is pending",
        ):
            MODULE._validate_activation(
                self.home,
                self.account,
                self.current_snapshot(),
                runtime=runtime,
            )

    def test_gui_activation_installs_before_publishing_receipt(self):
        events = []
        receipt = self.home / MODULE.STATE_DIRECTORY_NAME / MODULE.ACTIVATION_FILE_NAME
        pending = (
            self.home
            / MODULE.STATE_DIRECTORY_NAME
            / MODULE.ACTIVATION_PENDING_FILE_NAME
        )

        class OrderingRuntime(FakeRuntime):
            def run(
                inner_self,
                argv,
                *,
                timeout,
                output_limit,
                before_spawn=None,
            ):
                call = decode_runtime_call(argv)
                if len(call) >= 2 and call[1] == "install-scheduler":
                    events.append("install")
                    self.assertFalse(receipt.exists())
                    self.assertTrue(pending.exists())
                    self.assertEqual(
                        json.loads(pending.read_text(encoding="utf-8"))["status"],
                        "in-flight",
                    )
                elif len(call) >= 2 and call[1] == "status-scheduler":
                    events.append("status")
                return super().run(
                    argv,
                    timeout=timeout,
                    output_limit=output_limit,
                    before_spawn=before_spawn,
                )

        runtime = OrderingRuntime(
            self.account,
            candidates=(self.controller_id,),
            gui=True,
        )
        original_publish = MODULE._publish_activation

        def observed_publish(*args, **kwargs):
            events.append("receipt")
            return original_publish(*args, **kwargs)

        with mock.patch.object(
            MODULE,
            "_publish_activation",
            side_effect=observed_publish,
        ):
            self.activate_controller(runtime)

        self.assertEqual(events, ["install", "status", "receipt"])
        self.assertTrue(receipt.exists())
        self.assertFalse(pending.exists())

    def test_old_receipt_is_rejected_while_reactivation_installs(self):
        initial_runtime = self.runtime()
        self.activate_controller(initial_runtime)
        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        receipt = state_directory / MODULE.ACTIVATION_FILE_NAME
        pending = state_directory / MODULE.ACTIVATION_PENDING_FILE_NAME
        old_receipt = receipt.read_bytes()
        observed = {}

        class InstallObservationRuntime(FakeRuntime):
            def run(
                inner_self,
                argv,
                *,
                timeout,
                output_limit,
                before_spawn=None,
            ):
                call = decode_runtime_call(argv)
                if len(call) >= 2 and call[1] == "install-scheduler":
                    observed["pending"] = pending.exists()
                    observed["pending_status"] = json.loads(
                        pending.read_text(encoding="utf-8")
                    )["status"]
                    observed["old_receipt"] = receipt.read_bytes() == old_receipt
                    with self.assertRaisesRegex(
                        MODULE.ControllerError,
                        "process-in-flight",
                    ):
                        MODULE._validate_activation(
                            self.home,
                            self.account,
                            self.current_snapshot(),
                            runtime=inner_self,
                        )
                    observed["consumer_rejected"] = True
                return super().run(
                    argv,
                    timeout=timeout,
                    output_limit=output_limit,
                    before_spawn=before_spawn,
                )

        runtime = InstallObservationRuntime(
            self.account,
            candidates=(self.controller_id,),
            gui=True,
        )
        self.activate_controller(runtime)

        self.assertEqual(
            observed,
            {
                "pending": True,
                "pending_status": "in-flight",
                "old_receipt": True,
                "consumer_rejected": True,
            },
        )
        self.assertFalse(pending.exists())

    def test_gui_activation_receipt_publication_failure_is_fail_closed(self):
        runtime = self.runtime()
        receipt = self.home / MODULE.STATE_DIRECTORY_NAME / MODULE.ACTIVATION_FILE_NAME

        with mock.patch.object(
            MODULE,
            "_publish_activation",
            side_effect=MODULE.StatePublicationError("publication failed"),
        ):
            with self.assertRaisesRegex(
                MODULE.StatePublicationError,
                "publication failed",
            ):
                self.activate_controller(runtime)

        self.assertIsNotNone(runtime.scheduler_state)
        self.assertFalse(receipt.exists())
        with self.assertRaisesRegex(
            MODULE.ControllerError,
            "activation publication is pending",
        ):
            MODULE._validate_activation(
                self.home,
                self.account,
                self.current_snapshot(),
                runtime=runtime,
            )

    def assert_failed_reactivation_is_pending_and_retry_recovers(self, runtime):
        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        receipt = state_directory / MODULE.ACTIVATION_FILE_NAME
        pending = state_directory / MODULE.ACTIVATION_PENDING_FILE_NAME

        self.assertTrue(receipt.exists())
        self.assertTrue(pending.exists())
        self.assertEqual(stat.S_IMODE(pending.stat().st_mode), 0o600)
        visible_receipt = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(visible_receipt["machine_identity_sha256"], "e" * 64)
        with self.assertRaisesRegex(
            MODULE.ControllerError,
            "activation publication is pending",
        ):
            MODULE._validate_activation(
                self.home,
                self.account,
                self.current_snapshot(),
                runtime=runtime,
            )
        with mock.patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(MODULE.main(self.scheduled_argv(), runtime), 1)
        self.assertFalse(
            any(
                len(call) > 1 and call[1] == "run-scheduled"
                for call in runtime.calls
            )
        )
        self.assertEqual(len(self.ssh_calls(runtime)), 0)

        self.activate_controller(runtime)

        self.assertFalse(pending.exists())
        validated = MODULE._validate_activation(
            self.home,
            self.account,
            self.current_snapshot(),
            runtime=runtime,
        )
        self.assertEqual(validated.entry.role, "gui-controller")
        install_calls = [
            call
            for call in runtime.calls
            if len(call) > 1 and call[1] == "install-scheduler"
        ]
        self.assertEqual(len(install_calls), 3)

    def test_receipt_directory_fsync_failure_stays_pending_until_retry(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.machine_digest = "e" * 64
        original_publish = MODULE._atomic_publish
        real_durable_sync = MODULE._durable_sync
        publishing_receipt = False
        directory_fsync_count = 0

        def observed_publish(directory_fd, name, payload, uid, expected):
            nonlocal publishing_receipt
            publishing_receipt = name == MODULE.ACTIVATION_FILE_NAME
            try:
                return original_publish(directory_fd, name, payload, uid, expected)
            finally:
                publishing_receipt = False

        def fail_receipt_directory_fsync(fd):
            nonlocal directory_fsync_count
            if publishing_receipt and stat.S_ISDIR(os.fstat(fd).st_mode):
                directory_fsync_count += 1
                raise OSError("injected receipt directory fsync failure")
            return real_durable_sync(fd)

        with mock.patch.object(MODULE, "_atomic_publish", side_effect=observed_publish):
            with mock.patch.object(
                MODULE,
                "_durable_sync",
                side_effect=fail_receipt_directory_fsync,
            ):
                with self.assertRaisesRegex(
                    MODULE.StatePublicationError,
                    "failed to publish state activation.json",
                ):
                    self.activate_controller(runtime)

        self.assertEqual(directory_fsync_count, 1)
        self.assert_failed_reactivation_is_pending_and_retry_recovers(runtime)

    def test_receipt_final_reread_failure_stays_pending_until_retry(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.machine_digest = "e" * 64
        original_read = MODULE._read_bound_state_file

        def fail_receipt_final_reread(directory_fd, name, uid, *, missing_ok):
            if name == MODULE.ACTIVATION_FILE_NAME and not missing_ok:
                raise MODULE.StatePublicationError(
                    "injected receipt final reread failure"
                )
            return original_read(
                directory_fd,
                name,
                uid,
                missing_ok=missing_ok,
            )

        with mock.patch.object(
            MODULE,
            "_read_bound_state_file",
            side_effect=fail_receipt_final_reread,
        ):
            with self.assertRaisesRegex(
                MODULE.StatePublicationError,
                "injected receipt final reread failure",
            ):
                self.activate_controller(runtime)

        self.assert_failed_reactivation_is_pending_and_retry_recovers(runtime)

    def assert_committed_activation_survives_stdout_failure(self, stdout):
        runtime = self.runtime()
        argv = (
            "activate",
            "--home",
            str(self.home),
            "--host-id",
            self.controller_id,
        )
        with mock.patch("sys.stdout", new=stdout):
            self.assertEqual(MODULE.main(argv, runtime), 0)
            self.assertIsInstance(MODULE.sys.stdout, MODULE._CommittedStdoutSink)

        pending = (
            self.home
            / MODULE.STATE_DIRECTORY_NAME
            / MODULE.ACTIVATION_PENDING_FILE_NAME
        )
        self.assertFalse(pending.exists())
        validated = MODULE._validate_activation(
            self.home,
            self.account,
            self.current_snapshot(),
            runtime=runtime,
        )
        self.assertEqual(validated.entry.role, "gui-controller")

    def test_committed_activation_survives_immediate_stdout_failure(self):
        class WriteFailure:
            def write(self, _value):
                raise BrokenPipeError("injected immediate stdout failure")

            def flush(self):
                raise AssertionError("flush must not use the failed stream")

        self.assert_committed_activation_survives_stdout_failure(WriteFailure())

    def test_committed_activation_survives_buffered_flush_failure(self):
        class FlushFailure:
            def __init__(self):
                self.messages = []

            def write(self, value):
                self.messages.append(value)
                return len(value)

            def flush(self):
                raise OSError("injected buffered stdout flush failure")

        stdout = FlushFailure()
        self.assert_committed_activation_survives_stdout_failure(stdout)
        self.assertEqual(len(stdout.messages), 1)

    def test_committed_activation_survives_stdout_keyboard_interrupt(self):
        class InterruptedStdout:
            def write(self, _value):
                raise KeyboardInterrupt("injected committed-report interrupt")

            def flush(self):
                raise AssertionError("flush must not use the interrupted stream")

        self.assert_committed_activation_survives_stdout_failure(InterruptedStdout())

    def test_activation_transaction_lock_serializes_install_and_receipt_publication(self):
        first_install_entered = threading.Event()
        release_first_install = threading.Event()
        second_install_entered = threading.Event()

        class InterleavingRuntime(FakeRuntime):
            def __init__(self, account):
                super().__init__(account, candidates=("controller",), gui=True)
                self.install_count = 0
                self.install_count_lock = threading.Lock()
                self.install_order = []

            def monotonic(self):
                return time.monotonic()

            def sleep(self, seconds):
                time.sleep(seconds)

            def run(
                self,
                argv,
                *,
                timeout,
                output_limit,
                before_spawn=None,
            ):
                call = decode_runtime_call(argv)
                if len(call) >= 2 and call[1] == "install-scheduler":
                    if before_spawn is not None:
                        before_spawn()
                    with self.install_count_lock:
                        self.install_count += 1
                        install_number = self.install_count
                    self.calls.append(call)
                    self.install_order.append(f"start-{install_number}")
                    if install_number == 1:
                        first_install_entered.set()
                        if not release_first_install.wait(5.0):
                            raise AssertionError("timed out releasing first activation")
                        self.install_order.append("end-1-failed")
                        return MODULE.CommandResult(1, b"", b"enable failed")
                    second_install_entered.set()
                    self.scheduler_state = {
                        "runner": call[call.index("--runner") + 1],
                        "interval_minutes": call[
                            call.index("--interval-minutes") + 1
                        ],
                        "mode": call[call.index("--mode") + 1],
                    }
                    self.install_order.append("end-2-success")
                    return MODULE.CommandResult(0, b"ok\n", b"")
                return super().run(
                    argv,
                    timeout=timeout,
                    output_limit=output_limit,
                    before_spawn=before_spawn,
                )

        runtime = InterleavingRuntime(self.account)
        outcomes = []

        def run_activation(name):
            try:
                result = MODULE.activate(
                    runtime,
                    self.home,
                    requested_host_id=self.controller_id,
                    interval_minutes=30,
                )
                outcomes.append((name, "success", result.entry.role))
            except MODULE.ControllerError as error:
                outcomes.append((name, "error", str(error)))

        first = threading.Thread(target=run_activation, args=("first",))
        second = threading.Thread(target=run_activation, args=("second",))
        first.start()
        self.assertTrue(first_install_entered.wait(2.0))
        second.start()
        entered_while_first_held_lock = second_install_entered.wait(0.2)
        release_first_install.set()
        first.join(5.0)
        second.join(5.0)

        self.assertFalse(entered_while_first_held_lock)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(
            runtime.install_order,
            ["start-1", "end-1-failed", "start-2", "end-2-success"],
        )
        self.assertEqual(
            sorted((name, status) for name, status, _detail in outcomes),
            [("first", "error"), ("second", "success")],
        )
        validated = MODULE._validate_activation(
            self.home,
            self.account,
            self.current_snapshot(),
            runtime=runtime,
        )
        self.assertEqual(validated.entry.role, "gui-controller")
        self.assertEqual(runtime.scheduler_state["mode"], "private")
        activation_lock = self.home / MODULE.STATE_DIRECTORY_NAME / "activation.lock"
        lock_metadata = activation_lock.stat()
        self.assertTrue(stat.S_ISREG(lock_metadata.st_mode))
        self.assertEqual(lock_metadata.st_uid, self.uid)
        self.assertEqual(stat.S_IMODE(lock_metadata.st_mode), 0o600)

    def test_activation_lock_order_is_host_then_activation(self):
        runtime = self.runtime()
        events = []
        original_host_lock = MODULE._host_mutation_lock
        original_activation_lock = MODULE._activation_lock

        @MODULE.contextmanager
        def observed_host_lock(*args, **kwargs):
            events.append("host-enter")
            with original_host_lock(*args, **kwargs) as transaction:
                yield transaction
            events.append("host-exit")

        @MODULE.contextmanager
        def observed_activation_lock(*args, **kwargs):
            events.append("activation-enter")
            with original_activation_lock(*args, **kwargs) as transaction:
                yield transaction
            events.append("activation-exit")

        with mock.patch.object(
            MODULE,
            "_host_mutation_lock",
            side_effect=observed_host_lock,
        ):
            with mock.patch.object(
                MODULE,
                "_activation_lock",
                side_effect=observed_activation_lock,
            ):
                self.activate_controller(runtime)

        self.assertEqual(
            events,
            ["host-enter", "activation-enter", "activation-exit", "host-exit"],
        )

    def test_host_lock_serializes_activation_behind_complete_controller_fanout(self):
        ssh_entered = threading.Event()
        release_ssh = threading.Event()
        installer_entered = threading.Event()

        class ConcurrentRuntime(FakeRuntime):
            def monotonic(self):
                return time.monotonic()

            def sleep(self, seconds):
                time.sleep(seconds)

        runtime = ConcurrentRuntime(
            self.account,
            candidates=(self.controller_id,),
            gui=True,
        )
        self.activate_controller(runtime)
        success = self.json_result(self.success_receipt())
        runtime.calls.clear()

        def pause_ssh(_call):
            ssh_entered.set()
            if not release_ssh.wait(3.0):
                raise AssertionError("timed out releasing controller SSH")
            return success

        def observe_installer(_call):
            installer_entered.set()
            return MODULE.CommandResult(0, b"ok\n", b"")

        runtime.ssh_factory = pause_ssh
        runtime.install_factory = observe_installer
        outcomes = []

        def run_controller():
            try:
                outcomes.append(("controller", MODULE.controller_run(
                    runtime,
                    self.home,
                    strict=False,
                )))
            except BaseException as error:
                outcomes.append(("controller-error", error))

        def run_activation():
            try:
                result = self.activate_controller(runtime)
                outcomes.append(("activation", result.entry.role))
            except BaseException as error:
                outcomes.append(("activation-error", error))

        controller_thread = threading.Thread(target=run_controller)
        activation_thread = threading.Thread(target=run_activation)
        controller_thread.start()
        self.assertTrue(ssh_entered.wait(2.0))
        activation_thread.start()
        self.assertFalse(installer_entered.wait(0.2))
        release_ssh.set()
        controller_thread.join(5.0)
        activation_thread.join(5.0)

        self.assertFalse(controller_thread.is_alive())
        self.assertFalse(activation_thread.is_alive())
        self.assertTrue(installer_entered.is_set())
        self.assertEqual(
            sorted(outcomes, key=lambda item: item[0]),
            [
                ("activation", "gui-controller"),
                ("controller", True),
            ],
        )

    def test_activation_receipt_binds_hashed_machine_identity(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        receipt_path = (
            self.home / MODULE.STATE_DIRECTORY_NAME / MODULE.ACTIVATION_FILE_NAME
        )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["machine_identity_sha256"], "f" * 64)
        self.assertNotIn("IOPlatformUUID", receipt_path.read_text(encoding="utf-8"))

        runtime.machine_digest = "e" * 64
        with self.assertRaisesRegex(MODULE.ControllerError, "role-activation-required"):
            MODULE._validate_activation(
                self.home,
                self.account,
                self.current_snapshot(),
                runtime=runtime,
            )

    def test_system_runtime_hashes_platform_uuid_without_returning_raw_value(self):
        runtime = MODULE.SystemRuntime()
        raw_uuid = b"12345678-1234-1234-1234-123456789ABC"
        output = b'    "IOPlatformUUID" = "' + raw_uuid + b'"\n'
        with mock.patch.object(
            runtime,
            "run",
            return_value=MODULE.CommandResult(0, output, b""),
        ):
            digest = runtime.machine_identity_sha256()
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertNotEqual(digest, raw_uuid.decode("ascii"))

    def test_activation_receipt_rejects_changed_controller_target_scope(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        extra_target = {
            "id": "new-headless",
            "role": "headless-managed",
            "username": "hoteng",
            "uid": self.uid,
            "home": str(self.account_home),
            "controller": self.controller_id,
            "ssh_alias": "new-headless-ssh",
        }
        changed = self.inventory_data(
            extra_hosts=[extra_target],
        )
        self.write_release(NEXT_PRIVATE_SHA, changed)
        self.switch_current(NEXT_PRIVATE_SHA)

        with self.assertRaisesRegex(MODULE.ControllerError, "role-activation-required"):
            MODULE._validate_activation(
                self.home,
                self.account,
                self.current_snapshot(),
                runtime=runtime,
            )

    def test_pending_is_published_before_exact_single_ssh_invocation(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        observed = {}

        def ssh_result(call):
            state = json.loads(self.state_path().read_text(encoding="utf-8"))
            observed["pending"] = state["pending"]
            observed["last_error"] = state["last_error"]
            observed["argv"] = call
            return self.json_result(self.success_receipt())

        runtime.ssh_factory = ssh_result
        self.assertTrue(MODULE.controller_run(runtime, self.home, strict=False))

        self.assertTrue(observed["pending"])
        self.assertEqual(observed["last_error"], "process-in-flight")
        self.assertEqual(len(self.ssh_calls(runtime)), 1)
        expected_prefix = (
            "/usr/bin/ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ConnectionAttempts=4",
            "-o",
            "ForwardAgent=no",
            "-o",
            "ClearAllForwardings=yes",
            "-o",
            "RequestTTY=no",
            "-T",
            "headless-ssh",
        )
        self.assertEqual(observed["argv"][:-1], expected_prefix)
        self.assertNotIn("\n", observed["argv"][-1])
        self.assertIn("remote-apply", shlex_split(observed["argv"][-1]))
        local_index = next(
            index
            for index, call in enumerate(runtime.calls)
            if len(call) > 1 and call[1] == "run-scheduled"
        )
        identity_index = next(
            index
            for index, call in enumerate(runtime.calls)
            if len(call) > 1 and call[1] == "release-identities"
        )
        ssh_index = next(
            index for index, call in enumerate(runtime.calls) if call[0] == "/usr/bin/ssh"
        )
        self.assertLess(local_index, identity_index)
        self.assertLess(identity_index, ssh_index)

    def test_local_sync_failure_is_operational_and_never_starts_target(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        runtime.run_scheduled_results = [
            MODULE.CommandResult(1, b"", b"local failed")
        ]
        with mock.patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(MODULE.main(self.scheduled_argv(), runtime), 1)
        self.assertEqual(len(self.ssh_calls(runtime)), 0)
        self.assertFalse(self.state_path().exists())
        self.assertEqual(
            self.operation_state("controller-run")["status"],
            "retryable",
        )

    def test_controller_operation_fence_precedes_mutating_local_sync(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        observed = []

        def local_sync():
            observed.append(("local-sync", self.operation_state("controller-run")))

        runtime.run_scheduled_hook = local_sync

        def ssh_sync(_call):
            observed.append(("ssh", self.operation_state("controller-run")))
            return self.json_result(self.success_receipt())

        runtime.ssh_factory = ssh_sync

        self.assertTrue(MODULE.controller_run(runtime, self.home, strict=False))

        self.assertEqual(
            [name for name, _state in observed],
            ["local-sync", "ssh"],
        )
        self.assertTrue(
            all(state["status"] == "in-flight" for _name, state in observed)
        )
        self.assertTrue(
            all(
                state["operation"] == "controller-run"
                and state["host_id"] == self.controller_id
                and state["controller_id"] == self.controller_id
                for _name, state in observed
            )
        )
        self.assertEqual(
            self.operation_state("controller-run")["status"],
            "retryable",
        )

    def test_controller_quarantine_publication_failure_retains_blocking_in_flight(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()

        def cleanup_inconclusive():
            raise MODULE.ProcessCleanupInconclusiveError(
                "local sync process cleanup was inconclusive"
            )

        original_transition = MODULE._transition_operation_fence

        def fail_quarantine(*args, **kwargs):
            status = args[-1]
            if status == "process-cleanup-inconclusive":
                raise MODULE.StatePublicationError(
                    "injected operation quarantine publication failure"
                )
            return original_transition(*args, **kwargs)

        runtime.run_scheduled_hook = cleanup_inconclusive
        with mock.patch.object(
            MODULE,
            "_transition_operation_fence",
            side_effect=fail_quarantine,
        ):
            with self.assertRaisesRegex(
                MODULE.ProcessCleanupInconclusiveError,
                "quarantine publication failed",
            ):
                MODULE.controller_run(runtime, self.home, strict=False)

        self.assertEqual(
            self.operation_state("controller-run")["status"],
            "in-flight",
        )
        calls_after_failure = len(runtime.calls)
        with self.assertRaisesRegex(
            MODULE.ProcessCleanupInconclusiveError,
            "durable process fence",
        ):
            MODULE.controller_run(runtime, self.home, strict=False)
        with self.assertRaisesRegex(
            MODULE.ProcessCleanupInconclusiveError,
            "durable process fence",
        ):
            MODULE.sync_target(
                runtime,
                self.home,
                target_id=self.target_id,
                force=True,
                strict=False,
            )
        self.assertEqual(len(runtime.calls), calls_after_failure)

    def test_post_ssh_publication_failure_keeps_target_in_flight(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        runtime.ssh_results = [self.json_result(self.success_receipt())]
        original_publish = MODULE._publish_target_state
        publications = 0

        def fail_final_publication(*args, **kwargs):
            nonlocal publications
            publications += 1
            if publications == 2:
                raise MODULE.StatePublicationError(
                    "injected post-SSH state publication failure"
                )
            return original_publish(*args, **kwargs)

        with mock.patch.object(
            MODULE,
            "_publish_target_state",
            side_effect=fail_final_publication,
        ):
            with self.assertRaisesRegex(
                MODULE.StatePublicationError,
                "post-SSH state publication failure",
            ):
                MODULE.controller_run(runtime, self.home, strict=False)

        self.assertEqual(
            json.loads(self.state_path().read_text(encoding="utf-8"))["last_error"],
            "process-in-flight",
        )
        ssh_count = len(self.ssh_calls(runtime))
        with self.assertRaisesRegex(MODULE.ControllerError, "process-in-flight"):
            MODULE.sync_target(
                runtime,
                self.home,
                target_id=self.target_id,
                force=True,
                strict=False,
            )
        self.assertEqual(len(self.ssh_calls(runtime)), ssh_count)

    def test_state_compare_and_swap_rejects_concurrent_content(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        original = MODULE._read_bound_state_file
        reads = {"target": 0}
        concurrent = b"concurrent-state\n"

        def racing_read(directory_fd, name, uid, *, missing_ok):
            if name == f"target-{self.target_id}.json":
                reads["target"] += 1
                if reads["target"] == 3:
                    self.state_path().write_bytes(concurrent)
                    os.chmod(self.state_path(), 0o600)
            return original(
                directory_fd,
                name,
                uid,
                missing_ok=missing_ok,
            )

        with mock.patch.object(
            MODULE,
            "_read_bound_state_file",
            side_effect=racing_read,
        ):
            with self.assertRaisesRegex(
                MODULE.StatePublicationError,
                "compare-and-swap failed at publication",
            ):
                MODULE.controller_run(runtime, self.home, strict=False)

        self.assertEqual(self.state_path().read_bytes(), concurrent)
        self.assertEqual(len(self.ssh_calls(runtime)), 0)

    def test_failure_retries_unchanged_release_next_cycle_then_success_skips(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        runtime.ssh_results = [
            MODULE.CommandResult(255, b"", b"connection failed"),
            self.json_result(self.success_receipt()),
        ]

        self.assertTrue(MODULE.controller_run(runtime, self.home, strict=False))
        failed = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertTrue(failed["pending"])
        self.assertEqual(failed["last_error"], "ssh-uncertain")
        self.assertTrue(MODULE.controller_run(runtime, self.home, strict=False))
        healthy = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertFalse(healthy["pending"])
        self.assertEqual(healthy["confirmed"], self.desired())
        self.assertTrue(MODULE.controller_run(runtime, self.home, strict=False))
        self.assertEqual(len(self.ssh_calls(runtime)), 2)

    def test_ssh_spawn_error_persists_uncertain_and_auto_exit_stays_zero(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()

        def spawn_error(_call):
            raise MODULE.ControllerError("ssh spawn failed")

        runtime.ssh_factory = spawn_error
        self.assertEqual(MODULE.main(self.scheduled_argv(), runtime), 0)
        state = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertTrue(state["pending"])
        self.assertEqual(state["last_error"], "ssh-uncertain")
        self.assertEqual(len(self.ssh_calls(runtime)), 1)

    def test_ssh_cleanup_quarantine_blocks_force_and_does_not_claim_retry(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        runtime.ssh_results = [self.json_result(self.success_receipt())]
        MODULE.controller_run(runtime, self.home, strict=False)

        self.write_release(NEXT_PRIVATE_SHA, self.inventory_data())
        self.switch_current(NEXT_PRIVATE_SHA)
        runtime.identity_pair = self.desired(NEXT_PRIVATE_SHA, NEXT_PRIVATE_TREE)
        ssh_attempts = []

        def cleanup_inconclusive(call):
            ssh_attempts.append(call)
            raise MODULE.ProcessCleanupInconclusiveError(
                "SSH process-group cleanup was inconclusive"
            )

        runtime.ssh_factory = cleanup_inconclusive
        with mock.patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(MODULE.main(self.scheduled_argv(), runtime), 1)
        state = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertEqual(state["last_error"], "process-cleanup-inconclusive")
        notifications = self.notification_calls(runtime)
        self.assertEqual(len(notifications), 1)
        self.assertIn(
            "manual recovery requires proving process absence",
            notifications[0][-2],
        )
        self.assertNotIn("will retry", notifications[0][-2])

        with mock.patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(
                MODULE.main(
                    [
                        "sync-target",
                        "--home",
                        str(self.home),
                        "--target-id",
                        self.target_id,
                        "--force",
                    ],
                    runtime,
                ),
                1,
            )
        self.assertEqual(len(ssh_attempts), 1)

    def test_sync_target_cleanup_inconclusive_keeps_target_and_host_blockers(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()

        def cleanup_inconclusive(_call):
            raise MODULE.ProcessCleanupInconclusiveError(
                "SSH process-group cleanup was inconclusive"
            )

        runtime.ssh_factory = cleanup_inconclusive
        with self.assertRaises(MODULE.ProcessCleanupInconclusiveError):
            MODULE.sync_target(
                runtime,
                self.home,
                target_id=self.target_id,
                force=True,
                strict=False,
            )

        target_state = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertEqual(
            target_state["last_error"],
            "process-cleanup-inconclusive",
        )
        self.assertEqual(
            self.operation_state("sync-target")["status"],
            "process-cleanup-inconclusive",
        )
        with self.assertRaisesRegex(
            MODULE.ProcessCleanupInconclusiveError,
            "durable process fence from sync-target",
        ):
            self.activate_controller(runtime)

    def test_ssh_quarantine_publication_failure_keeps_in_flight_fence(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        ssh_attempts = []

        def cleanup_inconclusive(call):
            ssh_attempts.append(call)
            raise MODULE.ProcessCleanupInconclusiveError(
                "SSH process-group cleanup was inconclusive"
            )

        original_mark = MODULE._mark_pending_failure

        def fail_quarantine(*args, **kwargs):
            reason = args[-2]
            if reason == "process-cleanup-inconclusive":
                raise MODULE.StatePublicationError(
                    "injected target quarantine publication failure"
                )
            return original_mark(*args, **kwargs)

        runtime.ssh_factory = cleanup_inconclusive
        with mock.patch.object(
            MODULE,
            "_mark_pending_failure",
            side_effect=fail_quarantine,
        ):
            with self.assertRaisesRegex(
                MODULE.ProcessCleanupInconclusiveError,
                "could not be quarantined",
            ):
                MODULE.controller_run(runtime, self.home, strict=False)

        state = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertEqual(state["last_error"], "process-in-flight")
        self.assertEqual(
            self.operation_state("controller-run")["status"],
            "process-cleanup-inconclusive",
        )
        with self.assertRaisesRegex(MODULE.ControllerError, "durable process fence"):
            MODULE.controller_run(runtime, self.home, strict=False)
        with self.assertRaisesRegex(MODULE.ControllerError, "durable process fence"):
            MODULE.sync_target(
                runtime,
                self.home,
                target_id=self.target_id,
                force=True,
                strict=False,
            )
        self.assertEqual(len(ssh_attempts), 1)

    def test_legacy_target_pending_migrates_fail_closed_even_with_force(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        snapshot = self.current_snapshot()
        state = {
            "version": 1,
            "controller_id": self.controller_id,
            "target_id": self.target_id,
            "scope_sha256": MODULE._scope_digest(
                snapshot.inventory.hosts[self.controller_id],
                snapshot.inventory.hosts[self.target_id],
            ),
            "desired": self.desired(),
            "confirmed": None,
            "pending": True,
            "generation": 1,
            "last_error": None,
        }
        self.state_path().write_text(
            json.dumps(state, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(self.state_path(), 0o600)

        with self.assertRaisesRegex(MODULE.ControllerError, "legacy-pending"):
            MODULE.controller_run(runtime, self.home, strict=False)
        with self.assertRaisesRegex(MODULE.ControllerError, "legacy-pending"):
            MODULE.sync_target(
                runtime,
                self.home,
                target_id=self.target_id,
                force=True,
                strict=False,
            )
        self.assertEqual(len(self.ssh_calls(runtime)), 0)

    def test_controller_validation_cleanup_inconclusive_is_not_scope_drift(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        original_validate = MODULE._validate_activation
        validations = 0

        def fail_second_validation(*args, **kwargs):
            nonlocal validations
            validations += 1
            if validations == 2:
                raise MODULE.ProcessCleanupInconclusiveError(
                    "validation process cleanup was inconclusive"
                )
            return original_validate(*args, **kwargs)

        with mock.patch.object(
            MODULE,
            "_validate_activation",
            side_effect=fail_second_validation,
        ):
            with self.assertRaises(MODULE.ProcessCleanupInconclusiveError):
                MODULE.controller_run(runtime, self.home, strict=False)

        self.assertFalse(self.state_path().exists())
        self.assertEqual(len(self.ssh_calls(runtime)), 0)
        self.assertEqual(len(self.notification_calls(runtime)), 0)
        self.assertEqual(
            self.operation_state("controller-run")["status"],
            "process-cleanup-inconclusive",
        )

    def test_notification_cleanup_inconclusive_propagates(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.ssh_results = [self.json_result(self.success_receipt())]
        MODULE.controller_run(runtime, self.home, strict=False)
        runtime.calls.clear()

        self.write_release(NEXT_PRIVATE_SHA, self.inventory_data())
        self.switch_current(NEXT_PRIVATE_SHA)
        runtime.identity_pair = self.desired(NEXT_PRIVATE_SHA, NEXT_PRIVATE_TREE)
        runtime.ssh_results = [MODULE.CommandResult(255, b"", b"uncertain")]
        runtime.notification_exception = MODULE.ProcessCleanupInconclusiveError(
            "notification process cleanup was inconclusive"
        )

        with self.assertRaises(MODULE.ProcessCleanupInconclusiveError):
            MODULE.controller_run(runtime, self.home, strict=False)

        state = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertEqual(state["last_error"], "ssh-uncertain")
        self.assertEqual(len(self.notification_calls(runtime)), 1)
        self.assertEqual(
            self.operation_state("controller-run")["status"],
            "process-cleanup-inconclusive",
        )

    def test_quarantine_cleanup_priority_survives_notification_signal(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.ssh_results = [self.json_result(self.success_receipt())]
        MODULE.controller_run(runtime, self.home, strict=False)
        runtime.calls.clear()

        self.write_release(NEXT_PRIVATE_SHA, self.inventory_data())
        self.switch_current(NEXT_PRIVATE_SHA)
        runtime.identity_pair = self.desired(NEXT_PRIVATE_SHA, NEXT_PRIVATE_TREE)
        runtime.ssh_results = [
            MODULE.CommandResult(
                MODULE.REMOTE_PROCESS_CLEANUP_EXIT,
                b"truncated cleanup receipt",
                b"remote cleanup diagnostics",
            )
        ]
        runtime.notification_exception = MODULE._ManagedProcessSignal(
            MODULE.signal.SIGTERM
        )

        with self.assertRaisesRegex(
            MODULE.ProcessCleanupInconclusiveError,
            "remote target process cleanup was inconclusive",
        ) as raised:
            MODULE.controller_run(runtime, self.home, strict=False)

        self.assertIsInstance(raised.exception.__cause__, MODULE._ManagedProcessSignal)
        self.assertEqual(
            json.loads(self.state_path().read_text(encoding="utf-8"))["last_error"],
            "process-cleanup-inconclusive",
        )
        self.assertEqual(
            self.operation_state("controller-run")["status"],
            "process-cleanup-inconclusive",
        )

    def test_managed_signal_maps_to_exit_only_after_target_pending_and_unlock(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()

        def interrupt(_call):
            raise MODULE._ManagedProcessSignal(MODULE.signal.SIGTERM)

        runtime.ssh_factory = interrupt
        exit_code = MODULE.main(self.scheduled_argv(), runtime)

        self.assertEqual(exit_code, 128 + MODULE.signal.SIGTERM)
        state = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertTrue(state["pending"])
        self.assertEqual(state["last_error"], "process-retryable")
        self.assertEqual(
            self.operation_state("controller-run")["status"],
            "retryable",
        )
        lock_path = (
            self.home
            / MODULE.STATE_DIRECTORY_NAME
            / f"target-{self.target_id}.lock"
        )
        lock_fd = os.open(lock_path, os.O_RDWR)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

    def test_transition_notifications_only_fire_on_health_edges(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        runtime.ssh_results = [self.json_result(self.success_receipt())]
        MODULE.controller_run(runtime, self.home, strict=False)
        self.assertEqual(len(self.notification_calls(runtime)), 0)

        self.write_release(NEXT_PRIVATE_SHA, self.inventory_data())
        self.switch_current(NEXT_PRIVATE_SHA)
        runtime.identity_pair = self.desired(NEXT_PRIVATE_SHA, NEXT_PRIVATE_TREE)
        runtime.ssh_results = [
            MODULE.CommandResult(255, b"", b"uncertain"),
            self.json_result(
                self.success_receipt(
                    self.desired(NEXT_PRIVATE_SHA, NEXT_PRIVATE_TREE),
                    self.current_snapshot(),
                )
            ),
        ]
        MODULE.controller_run(runtime, self.home, strict=False)
        self.assertEqual(len(self.notification_calls(runtime)), 1)
        MODULE.controller_run(runtime, self.home, strict=False)
        self.assertEqual(len(self.notification_calls(runtime)), 2)
        MODULE.controller_run(runtime, self.home, strict=False)
        self.assertEqual(len(self.notification_calls(runtime)), 2)

    def test_notification_failure_is_nonfatal_and_does_not_change_pending_state(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        runtime.ssh_results = [self.json_result(self.success_receipt())]
        MODULE.controller_run(runtime, self.home, strict=False)

        self.write_release(NEXT_PRIVATE_SHA, self.inventory_data())
        self.switch_current(NEXT_PRIVATE_SHA)
        runtime.identity_pair = self.desired(NEXT_PRIVATE_SHA, NEXT_PRIVATE_TREE)
        runtime.notification_error = True
        runtime.ssh_results = [MODULE.CommandResult(255, b"", b"uncertain")]

        self.assertTrue(MODULE.controller_run(runtime, self.home, strict=False))
        state = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertTrue(state["pending"])
        self.assertEqual(state["last_error"], "ssh-uncertain")
        self.assertEqual(len(self.notification_calls(runtime)), 1)

    def test_remote_receipt_mismatch_is_persisted_and_strict_status_degrades(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        receipt = self.success_receipt()
        receipt["release_trees"]["private"]["tree_sha256"] = "e" * 64
        runtime.ssh_results = [self.json_result(receipt)]

        self.assertTrue(MODULE.controller_run(runtime, self.home, strict=False))
        state = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertTrue(state["pending"])
        self.assertEqual(state["last_error"], "remote-receipt-invalid")
        payload, readable = MODULE._status_payload(runtime, self.home)
        self.assertTrue(readable)
        self.assertTrue(payload["degraded"])
        self.assertFalse(payload["healthy"])

    def test_remote_cleanup_exit_quarantines_target_and_host_mutation(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        receipt = MODULE._remote_cleanup_receipt(
            self.controller_id,
            self.target_id,
            self.desired(),
        )
        runtime.ssh_results = [
            self.json_result(
                receipt,
                returncode=MODULE.REMOTE_PROCESS_CLEANUP_EXIT,
            )
        ]

        with self.assertRaisesRegex(
            MODULE.ProcessCleanupInconclusiveError,
            "remote target process cleanup was inconclusive",
        ):
            MODULE.controller_run(runtime, self.home, strict=False)

        state = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertEqual(state["last_error"], "process-cleanup-inconclusive")
        self.assertEqual(
            self.operation_state("controller-run")["status"],
            "process-cleanup-inconclusive",
        )
        ssh_count = len(self.ssh_calls(runtime))
        with self.assertRaisesRegex(
            MODULE.ProcessCleanupInconclusiveError,
            "durable process fence from controller-run",
        ):
            MODULE.sync_target(
                runtime,
                self.home,
                target_id=self.target_id,
                force=True,
                strict=False,
            )
        self.assertEqual(len(self.ssh_calls(runtime)), ssh_count)

    def test_remote_cleanup_exit_stops_remaining_controller_fanout(self):
        second_target_id = "z-headless"
        second_target = {
            "id": second_target_id,
            "role": "headless-managed",
            "username": "hoteng",
            "uid": self.uid,
            "home": str(self.account_home),
            "controller": self.controller_id,
            "ssh_alias": "z-headless-ssh",
        }
        self.write_release(
            NEXT_PRIVATE_SHA,
            self.inventory_data(extra_hosts=[second_target]),
        )
        self.switch_current(NEXT_PRIVATE_SHA)
        runtime = self.runtime()
        runtime.identity_pair = self.desired(NEXT_PRIVATE_SHA, NEXT_PRIVATE_TREE)
        self.activate_controller(runtime)
        runtime.calls.clear()
        attempts = []
        cleanup_receipt = MODULE._remote_cleanup_receipt(
            self.controller_id,
            self.target_id,
            self.desired(NEXT_PRIVATE_SHA, NEXT_PRIVATE_TREE),
        )

        def first_target_quarantines(call):
            attempts.append(call)
            if len(attempts) > 1:
                self.fail("controller continued fanout after a cleanup quarantine")
            return self.json_result(
                cleanup_receipt,
                returncode=MODULE.REMOTE_PROCESS_CLEANUP_EXIT,
            )

        runtime.ssh_factory = first_target_quarantines
        with self.assertRaises(MODULE.ProcessCleanupInconclusiveError):
            MODULE.controller_run(runtime, self.home, strict=False)

        self.assertEqual(len(attempts), 1)
        self.assertIn("headless-ssh", attempts[0])
        self.assertEqual(
            self.operation_state("controller-run")["status"],
            "process-cleanup-inconclusive",
        )

    def test_remote_cleanup_exit_quarantine_publish_failure_keeps_both_blockers(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        runtime.ssh_results = [
            MODULE.CommandResult(
                MODULE.REMOTE_PROCESS_CLEANUP_EXIT,
                b"truncated cleanup receipt",
                b"remote cleanup diagnostics",
            )
        ]
        original_mark = MODULE._mark_pending_failure

        def fail_cleanup_quarantine(*args, **kwargs):
            if args[-2] == "process-cleanup-inconclusive":
                raise MODULE.StatePublicationError(
                    "injected remote cleanup quarantine publication failure"
                )
            return original_mark(*args, **kwargs)

        with mock.patch.object(
            MODULE,
            "_mark_pending_failure",
            side_effect=fail_cleanup_quarantine,
        ):
            with self.assertRaisesRegex(
                MODULE.ProcessCleanupInconclusiveError,
                "target in-flight fence could not be quarantined",
            ):
                MODULE.controller_run(runtime, self.home, strict=False)

        self.assertEqual(
            json.loads(self.state_path().read_text(encoding="utf-8"))["last_error"],
            "process-in-flight",
        )
        self.assertEqual(
            self.operation_state("controller-run")["status"],
            "process-cleanup-inconclusive",
        )
        ssh_count = len(self.ssh_calls(runtime))
        with self.assertRaisesRegex(
            MODULE.ProcessCleanupInconclusiveError,
            "durable process fence from controller-run",
        ):
            MODULE.controller_run(runtime, self.home, strict=False)
        self.assertEqual(len(self.ssh_calls(runtime)), ssh_count)

    def test_malformed_remote_cleanup_exit_still_quarantines_target(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        runtime.ssh_results = [
            MODULE.CommandResult(
                MODULE.REMOTE_PROCESS_CLEANUP_EXIT,
                b"remote banner\n{not-json}",
                b"unexpected remote stderr",
            )
        ]

        with self.assertRaises(MODULE.ProcessCleanupInconclusiveError):
            MODULE.controller_run(runtime, self.home, strict=False)

        state = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertEqual(state["last_error"], "process-cleanup-inconclusive")
        self.assertNotEqual(state["last_error"], "remote-receipt-invalid")
        self.assertEqual(
            self.operation_state("controller-run")["status"],
            "process-cleanup-inconclusive",
        )

    def _assert_remote_cleanup_flags_quarantine(self, **flags):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        runtime.ssh_results = [
            MODULE.CommandResult(
                MODULE.REMOTE_PROCESS_CLEANUP_EXIT,
                b"truncated cleanup receipt",
                b"remote transport diagnostics",
                **flags,
            )
        ]

        with self.assertRaises(MODULE.ProcessCleanupInconclusiveError):
            MODULE.controller_run(runtime, self.home, strict=False)

        state = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertEqual(state["last_error"], "process-cleanup-inconclusive")
        self.assertEqual(
            self.operation_state("controller-run")["status"],
            "process-cleanup-inconclusive",
        )

    def test_remote_cleanup_exit_overrides_output_overflow_classification(self):
        self._assert_remote_cleanup_flags_quarantine(output_overflow=True)

    def test_remote_cleanup_exit_overrides_timeout_classification(self):
        self._assert_remote_cleanup_flags_quarantine(timed_out=True)

    def test_status_strict_binds_exact_canonical_aqua_scheduler_contract(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        runtime.ssh_results = [self.json_result(self.success_receipt())]
        MODULE.controller_run(runtime, self.home, strict=False)
        runtime.calls.clear()

        payload, readable = MODULE._status_payload(runtime, self.home)

        self.assertTrue(readable)
        self.assertTrue(payload["scheduler"]["healthy"])
        self.assertEqual(
            payload["scheduler"]["compatibility_exception"],
            "canonical-private-runner-drift",
        )
        self.assertIn(
            (
                self.physical_runner(),
                "status-scheduler",
                "--home",
                str(self.home),
                "--platform",
                "macos",
                "--json",
                "--strict",
            ),
            runtime.calls,
        )

    def test_status_main_exit_codes_distinguish_operational_and_degraded(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.scheduler_state["interval_minutes"] = "45"

        def invoke(*extra):
            stdout = io.StringIO()
            with mock.patch("sys.stdout", new=stdout):
                exit_code = MODULE.main(
                    ["status", "--home", str(self.home), "--json", *extra],
                    runtime,
                )
            return exit_code, json.loads(stdout.getvalue())

        exit_code, payload = invoke()
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["degraded"])
        self.assertFalse(payload["operational_error"])

        exit_code, payload = invoke("--strict")
        self.assertEqual(exit_code, 2)
        self.assertTrue(payload["degraded"])
        self.assertFalse(payload["operational_error"])

        lock = (
            self.home
            / MODULE.STATE_DIRECTORY_NAME
            / MODULE.HOST_MUTATION_LOCK_NAME
        )
        lock.unlink()
        exit_code, payload = invoke()
        self.assertEqual(exit_code, 1)
        self.assertTrue(payload["operational_error"])
        self.assertIn("existing host mutation lock", payload["reason"])

        exit_code, payload = invoke("--strict")
        self.assertEqual(exit_code, 1)
        self.assertTrue(payload["operational_error"])
        self.assertIn("existing host mutation lock", payload["reason"])

    def test_status_snapshot_entry_revalidation_rejects_true_drift_before_reads(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        status_ident = threading.get_ident()
        drift_kinds = (
            "home-replacement",
            "home-policy",
            "state-replacement",
            "state-policy",
            "lock-policy",
            "lock-replacement",
        )
        for kind in drift_kinds:
            with self.subTest(kind=kind):
                runtime.calls.clear()
                mutate, restore, expected_reason = self.status_snapshot_drift(kind)
                original_flock = MODULE.fcntl.flock
                injected = False

                def acquire_then_mutate(fd, operation):
                    nonlocal injected
                    result = original_flock(fd, operation)
                    if (
                        threading.get_ident() == status_ident
                        and operation == MODULE.fcntl.LOCK_SH | MODULE.fcntl.LOCK_NB
                        and not injected
                    ):
                        injected = True
                        mutate()
                    return result

                payload_reader = mock.Mock(
                    return_value=({"marker": "must-be-discarded"}, True)
                )
                try:
                    with (
                        mock.patch.object(
                            MODULE.fcntl,
                            "flock",
                            side_effect=acquire_then_mutate,
                        ),
                        mock.patch.object(
                            MODULE,
                            "_status_payload_locked",
                            payload_reader,
                        ),
                    ):
                        payload, readable = MODULE._status_payload(runtime, self.home)
                finally:
                    if injected:
                        restore()

                self.assertTrue(injected)
                payload_reader.assert_not_called()
                self.assertFalse(readable)
                self.assertTrue(payload["operational_error"])
                self.assertNotIn("marker", payload)
                self.assertIn(expected_reason, payload["reason"])
                self.assertEqual(runtime.calls, [])

    def test_status_snapshot_exit_revalidation_discards_payload_on_true_drift(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        drift_kinds = (
            "home-replacement",
            "home-policy",
            "state-replacement",
            "state-policy",
            "lock-policy",
            "lock-replacement",
        )
        for kind in drift_kinds:
            with self.subTest(kind=kind):
                runtime.calls.clear()
                mutate, restore, expected_reason = self.status_snapshot_drift(kind)
                injected = False

                def payload_then_mutate(_runtime, _home, _account):
                    nonlocal injected
                    mutate()
                    injected = True
                    return {"marker": "must-be-discarded"}, True

                payload_reader = mock.Mock(side_effect=payload_then_mutate)
                try:
                    with mock.patch.object(
                        MODULE,
                        "_status_payload_locked",
                        payload_reader,
                    ):
                        payload, readable = MODULE._status_payload(runtime, self.home)
                finally:
                    if injected:
                        restore()

                self.assertTrue(injected)
                payload_reader.assert_called_once()
                self.assertFalse(readable)
                self.assertTrue(payload["operational_error"])
                self.assertNotIn("marker", payload)
                self.assertIn(expected_reason, payload["reason"])
                self.assertEqual(runtime.calls, [])

    def test_status_snapshot_accepts_benign_home_and_state_child_churn(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        home_child = self.home / "benign-status-home-child"
        state_child = state_directory / "benign-status-state-child"

        def payload_after_child_churn(_runtime, _home, _account):
            home_child.mkdir()
            state_child.mkdir()
            return {"marker": "coherent-snapshot"}, True

        try:
            with mock.patch.object(
                MODULE,
                "_status_payload_locked",
                side_effect=payload_after_child_churn,
            ):
                payload, readable = MODULE._status_payload(runtime, self.home)
        finally:
            if state_child.exists():
                state_child.rmdir()
            if home_child.exists():
                home_child.rmdir()

        self.assertTrue(readable)
        self.assertEqual(payload["marker"], "coherent-snapshot")
        self.assertEqual(runtime.calls, [])

    def test_status_snapshot_holds_shared_lock_from_first_read_through_prefix(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        writer_runtime = self.runtime()
        writer_runtime.monotonic = time.monotonic
        writer_runtime.sleep = time.sleep
        writer_attempted = threading.Event()
        writer_acquired = threading.Event()
        installer_started = threading.Event()
        writer_done = threading.Event()
        writer_errors = []
        writer_thread = None
        writer_ident = None
        status_ident = threading.get_ident()
        events = []
        original_flock = MODULE.fcntl.flock
        original_host_state = MODULE._read_host_mutation_state
        original_inventory = MODULE._load_current_inventory
        original_activation = MODULE._status_activation_snapshot

        def observed_flock(fd, operation):
            if (
                threading.get_ident() == status_ident
                and operation == MODULE.fcntl.LOCK_SH | MODULE.fcntl.LOCK_NB
            ):
                result = original_flock(fd, operation)
                events.append("status-shared")
                return result
            if (
                threading.get_ident() == status_ident
                and operation == MODULE.fcntl.LOCK_UN
            ):
                events.append("status-unlock")
                return original_flock(fd, operation)
            if (
                threading.get_ident() == writer_ident
                and operation == MODULE.fcntl.LOCK_EX | MODULE.fcntl.LOCK_NB
            ):
                events.append("writer-attempted")
                writer_attempted.set()
                result = original_flock(fd, operation)
                events.append("writer-acquired")
                writer_acquired.set()
                return result
            return original_flock(fd, operation)

        def observe_writer_spawn(call):
            if len(call) > 1 and call[1] == "install-scheduler":
                installer_started.set()

        writer_runtime.before_spawn_hook = observe_writer_spawn

        def activate_writer():
            nonlocal writer_ident
            writer_ident = threading.get_ident()
            try:
                self.activate_controller(writer_runtime)
            except BaseException as error:
                writer_errors.append(error)
            finally:
                writer_done.set()

        def assert_prefix_is_shared(label):
            self.assertTrue(writer_attempted.is_set())
            self.assertFalse(writer_acquired.is_set())
            self.assertFalse(installer_started.is_set())
            events.append(label)

        def read_host_state(home, account):
            nonlocal writer_thread
            self.assertIsNone(writer_thread)
            writer_thread = threading.Thread(target=activate_writer)
            writer_thread.start()
            self.assertTrue(writer_attempted.wait(2.0))
            time.sleep(0.05)
            assert_prefix_is_shared("host-state")
            return original_host_state(home, account)

        def load_inventory(home, uid):
            if threading.get_ident() == status_ident:
                assert_prefix_is_shared("inventory")
            return original_inventory(home, uid)

        def status_activation(runtime_arg, home, account, snapshot):
            if threading.get_ident() == status_ident:
                assert_prefix_is_shared("activation")
            return original_activation(runtime_arg, home, account, snapshot)

        try:
            with (
                mock.patch.object(
                    MODULE.fcntl,
                    "flock",
                    side_effect=observed_flock,
                ),
                mock.patch.object(
                    MODULE,
                    "_read_host_mutation_state",
                    side_effect=read_host_state,
                ),
                mock.patch.object(
                    MODULE,
                    "_load_current_inventory",
                    side_effect=load_inventory,
                ),
                mock.patch.object(
                    MODULE,
                    "_status_activation_snapshot",
                    side_effect=status_activation,
                ),
            ):
                payload, readable = MODULE._status_payload(runtime, self.home)
                if writer_thread is not None:
                    writer_thread.join(3.0)
        finally:
            if writer_thread is not None and writer_thread.is_alive():
                writer_thread.join(3.0)

        self.assertTrue(readable)
        self.assertFalse(payload["operational_error"])
        self.assertTrue(payload["activation"]["healthy"])
        self.assertTrue(payload["scheduler"]["healthy"])
        self.assertTrue(writer_done.is_set())
        self.assertEqual(writer_errors, [])
        self.assertTrue(writer_acquired.is_set())
        self.assertTrue(installer_started.is_set())
        shared_index = events.index("status-shared")
        unlock_index = events.index("status-unlock")
        acquired_index = events.index("writer-acquired")
        for label in ("host-state", "inventory", "activation"):
            self.assertLess(shared_index, events.index(label))
            self.assertLess(events.index(label), unlock_index)
        self.assertLess(unlock_index, acquired_index)

    def test_status_snapshot_blocks_activation_writer_until_scheduler_read_finishes(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        receipt = (
            self.home
            / MODULE.STATE_DIRECTORY_NAME
            / MODULE.ACTIVATION_FILE_NAME
        )
        receipt_identity = receipt.stat().st_ino
        writer_runtime = self.runtime()
        writer_runtime.monotonic = time.monotonic
        writer_runtime.sleep = time.sleep
        writer_attempted = threading.Event()
        writer_acquired = threading.Event()
        installer_started = threading.Event()
        writer_done = threading.Event()
        writer_errors = []
        writer_thread = None
        writer_ident = None
        original_flock = MODULE.fcntl.flock

        def observed_flock(fd, operation):
            if (
                threading.get_ident() == writer_ident
                and operation == MODULE.fcntl.LOCK_EX | MODULE.fcntl.LOCK_NB
            ):
                writer_attempted.set()
                result = original_flock(fd, operation)
                writer_acquired.set()
                return result
            return original_flock(fd, operation)

        def observe_writer_spawn(call):
            if len(call) > 1 and call[1] == "install-scheduler":
                installer_started.set()

        writer_runtime.before_spawn_hook = observe_writer_spawn

        def activate_writer():
            nonlocal writer_ident
            writer_ident = threading.get_ident()
            try:
                self.activate_controller(writer_runtime)
            except BaseException as error:
                writer_errors.append(error)
            finally:
                writer_done.set()

        def start_writer_during_scheduler(call):
            nonlocal writer_thread
            if (
                len(call) > 1
                and call[1] == "status-scheduler"
                and writer_thread is None
            ):
                writer_thread = threading.Thread(target=activate_writer)
                writer_thread.start()
                self.assertTrue(writer_attempted.wait(2.0))
                time.sleep(0.05)
                self.assertFalse(writer_acquired.is_set())
                self.assertFalse(installer_started.is_set())
                self.assertEqual(receipt.stat().st_ino, receipt_identity)

        runtime.before_spawn_hook = start_writer_during_scheduler
        try:
            with mock.patch.object(
                MODULE.fcntl,
                "flock",
                side_effect=observed_flock,
            ):
                payload, readable = MODULE._status_payload(runtime, self.home)
                if writer_thread is not None:
                    writer_thread.join(3.0)
        finally:
            if writer_thread is not None and writer_thread.is_alive():
                writer_thread.join(3.0)

        self.assertTrue(readable)
        self.assertTrue(payload["scheduler"]["healthy"])
        self.assertTrue(writer_done.is_set())
        self.assertEqual(writer_errors, [])
        self.assertTrue(writer_acquired.is_set())
        self.assertTrue(installer_started.is_set())
        self.assertNotEqual(receipt.stat().st_ino, receipt_identity)

    def test_status_snapshot_blocks_sync_writer_after_target_read(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        runtime.ssh_results = [self.json_result(self.success_receipt())]
        self.assertTrue(MODULE.controller_run(runtime, self.home, strict=False))
        runtime.calls.clear()
        initial_target_payload = self.state_path().read_bytes()
        writer_runtime = self.runtime()
        writer_runtime.monotonic = time.monotonic
        writer_runtime.sleep = time.sleep
        writer_runtime.ssh_results = [self.json_result(self.success_receipt())]
        writer_attempted = threading.Event()
        writer_acquired = threading.Event()
        writer_done = threading.Event()
        writer_errors = []
        writer_thread = None
        writer_ident = None
        status_ident = threading.get_ident()
        original_flock = MODULE.fcntl.flock
        original_read = MODULE._read_bound_state_file

        def observed_flock(fd, operation):
            if (
                threading.get_ident() == writer_ident
                and operation == MODULE.fcntl.LOCK_EX | MODULE.fcntl.LOCK_NB
            ):
                writer_attempted.set()
                result = original_flock(fd, operation)
                writer_acquired.set()
                return result
            return original_flock(fd, operation)

        def sync_writer():
            nonlocal writer_ident
            writer_ident = threading.get_ident()
            try:
                MODULE.sync_target(
                    writer_runtime,
                    self.home,
                    target_id=self.target_id,
                    force=True,
                    strict=False,
                )
            except BaseException as error:
                writer_errors.append(error)
            finally:
                writer_done.set()

        def read_and_start_writer(directory_fd, name, uid, *, missing_ok):
            nonlocal writer_thread
            result = original_read(
                directory_fd,
                name,
                uid,
                missing_ok=missing_ok,
            )
            if (
                threading.get_ident() == status_ident
                and name == f"target-{self.target_id}.json"
                and writer_thread is None
            ):
                writer_thread = threading.Thread(target=sync_writer)
                writer_thread.start()
                self.assertTrue(writer_attempted.wait(2.0))
                time.sleep(0.05)
                self.assertFalse(writer_acquired.is_set())
                self.assertEqual(self.ssh_calls(writer_runtime), [])
                self.assertEqual(self.state_path().read_bytes(), initial_target_payload)
            return result

        try:
            with mock.patch.object(
                MODULE.fcntl,
                "flock",
                side_effect=observed_flock,
            ):
                with mock.patch.object(
                    MODULE,
                    "_read_bound_state_file",
                    side_effect=read_and_start_writer,
                ):
                    payload, readable = MODULE._status_payload(runtime, self.home)
                if writer_thread is not None:
                    writer_thread.join(3.0)
        finally:
            if writer_thread is not None and writer_thread.is_alive():
                writer_thread.join(3.0)

        self.assertTrue(readable)
        self.assertTrue(payload["targets"][0]["healthy"])
        self.assertTrue(writer_done.is_set())
        self.assertEqual(writer_errors, [])
        self.assertTrue(writer_acquired.is_set())
        self.assertEqual(len(self.ssh_calls(writer_runtime)), 1)

    def test_status_strict_reports_activation_and_target_blockers_as_degraded(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        receipt = state_directory / MODULE.ACTIVATION_FILE_NAME
        pending = state_directory / MODULE.ACTIVATION_PENDING_FILE_NAME
        pending.write_bytes(
            MODULE._canonical_json_bytes(
                {
                    "version": 1,
                    "status": "in-flight",
                    "receipt_sha256": MODULE.hashlib.sha256(
                        receipt.read_bytes()
                    ).hexdigest(),
                }
            )
        )
        os.chmod(pending, 0o600)

        payload, readable = MODULE._status_payload(runtime, self.home)

        self.assertTrue(readable)
        self.assertFalse(payload["operational_error"])
        self.assertEqual(payload["activation"]["status"], "in-flight")
        with mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(
                MODULE.main(
                    ["status", "--home", str(self.home), "--json", "--strict"],
                    runtime,
                ),
                2,
            )

        pending.unlink()
        self.write_target_fence(
            self.target_id,
            last_error="process-in-flight",
        )
        payload, readable = MODULE._status_payload(runtime, self.home)

        self.assertTrue(readable)
        self.assertFalse(payload["operational_error"])
        self.assertEqual(payload["targets"][0]["reason"], "process-in-flight")
        with mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(
                MODULE.main(
                    ["status", "--home", str(self.home), "--json", "--strict"],
                    runtime,
                ),
                2,
            )

    def test_status_missing_snapshot_lock_is_operational_and_read_only(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        lock = state_directory / MODULE.HOST_MUTATION_LOCK_NAME
        lock.unlink()
        names_before = sorted(path.name for path in state_directory.iterdir())

        payload, readable = MODULE._status_payload(runtime, self.home)

        self.assertFalse(readable)
        self.assertTrue(payload["operational_error"])
        self.assertIn("existing host mutation lock", payload["reason"])
        self.assertFalse(lock.exists())
        self.assertEqual(
            sorted(path.name for path in state_directory.iterdir()),
            names_before,
        )
        with mock.patch("sys.stdout", new=io.StringIO()):
            self.assertNotEqual(
                MODULE.main(
                    ["status", "--home", str(self.home), "--json", "--strict"],
                    runtime,
                ),
                0,
            )
        self.assertFalse(lock.exists())
        self.assertEqual(
            sorted(path.name for path in state_directory.iterdir()),
            names_before,
        )

    def test_status_without_state_directory_does_not_create_state(self):
        runtime = self.runtime()
        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        self.assertFalse(state_directory.exists())

        payload, readable = MODULE._status_payload(runtime, self.home)

        self.assertFalse(readable)
        self.assertTrue(payload["operational_error"])
        self.assertFalse(state_directory.exists())
        with mock.patch("sys.stdout", new=io.StringIO()):
            self.assertNotEqual(
                MODULE.main(
                    ["status", "--home", str(self.home), "--json", "--strict"],
                    runtime,
                ),
                0,
            )
        self.assertFalse(state_directory.exists())

    def test_status_snapshot_lock_timeout_is_operational(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.monotonic = mock.Mock(side_effect=(0.0, 11.0))
        original_flock = MODULE.fcntl.flock

        def block_shared_lock(fd, operation):
            if operation == MODULE.fcntl.LOCK_SH | MODULE.fcntl.LOCK_NB:
                raise BlockingIOError("injected writer lock")
            return original_flock(fd, operation)

        with mock.patch.object(
            MODULE.fcntl,
            "flock",
            side_effect=block_shared_lock,
        ):
            payload, readable = MODULE._status_payload(runtime, self.home)

        self.assertFalse(readable)
        self.assertTrue(payload["operational_error"])
        self.assertEqual(
            payload["reason"],
            "timed out acquiring host mutation status lock",
        )

    def test_status_snapshot_rejects_unsafe_lock_mode_and_link_count(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        lock = state_directory / MODULE.HOST_MUTATION_LOCK_NAME

        os.chmod(lock, 0o640)
        payload, readable = MODULE._status_payload(runtime, self.home)
        self.assertFalse(readable)
        self.assertTrue(payload["operational_error"])
        self.assertIn("status lock identity or access policy", payload["reason"])

        os.chmod(lock, 0o600)
        extra_link = state_directory / "host-mutation.lock.extra-link"
        os.link(lock, extra_link)
        try:
            payload, readable = MODULE._status_payload(runtime, self.home)
            self.assertFalse(readable)
            self.assertTrue(payload["operational_error"])
            self.assertIn(
                "status lock identity or access policy",
                payload["reason"],
            )
        finally:
            extra_link.unlink()

    def test_status_final_snapshot_revalidation_rejects_lock_replacement(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        lock = state_directory / MODULE.HOST_MUTATION_LOCK_NAME
        original_identity = lock.stat().st_ino
        replaced = False

        def replace_lock_during_scheduler(call):
            nonlocal replaced
            if len(call) > 1 and call[1] == "status-scheduler" and not replaced:
                replacement = state_directory / "host-mutation.lock.next"
                replacement.write_bytes(b"")
                os.chmod(replacement, 0o600)
                os.replace(replacement, lock)
                replaced = True

        runtime.before_spawn_hook = replace_lock_during_scheduler

        payload, readable = MODULE._status_payload(runtime, self.home)

        self.assertTrue(replaced)
        self.assertNotEqual(lock.stat().st_ino, original_identity)
        self.assertFalse(readable)
        self.assertTrue(payload["operational_error"])
        self.assertIn("status lock identity", payload["reason"])

    def test_status_rejects_unverified_private_runner(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        runtime.verify_results = [
            MODULE.CommandResult(1, b"", b"private runner missing"),
        ]

        payload, readable = MODULE._status_payload(runtime, self.home)

        self.assertFalse(readable)
        self.assertTrue(payload["degraded"])
        self.assertTrue(payload["operational_error"])
        self.assertIn("private runner verification failed", payload["reason"])
        self.assertEqual(
            len(
                [
                    call
                    for call in runtime.calls
                    if len(call) > 1 and call[1] == "verify-overlay"
                ]
            ),
            1,
        )

    def test_status_rejects_scheduler_interval_drift(self):
        runtime = self.runtime()
        MODULE.activate(
            runtime,
            self.home,
            requested_host_id=self.controller_id,
            interval_minutes=45,
        )
        runtime.scheduler_state["interval_minutes"] = "30"

        payload, readable = MODULE._status_payload(runtime, self.home)

        self.assertTrue(readable)
        self.assertTrue(payload["degraded"])
        self.assertFalse(payload["operational_error"])
        self.assertEqual(
            payload["scheduler"]["reason"],
            "scheduler-interval-mismatch",
        )
        self.assertEqual(payload["scheduler"]["expected_interval_minutes"], 45)
        self.assertEqual(payload["scheduler"]["interval_minutes"], 30)
        with mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(
                MODULE.main(
                    ["status", "--home", str(self.home), "--json", "--strict"],
                    runtime,
                ),
                2,
            )

    def test_status_reports_blocking_operation_fence_as_readable_degraded(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.ssh_results = [self.json_result(self.success_receipt())]
        MODULE.controller_run(runtime, self.home, strict=False)
        path = self.operation_state_path("controller-run")
        state = self.operation_state("controller-run")
        state["status"] = "in-flight"
        state["generation"] += 1
        path.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(path, 0o600)

        payload, readable = MODULE._status_payload(runtime, self.home)

        self.assertTrue(readable)
        self.assertFalse(payload["operational_error"])
        self.assertTrue(payload["degraded"])
        self.assertEqual(payload["host_mutation"]["status"], "in-flight")
        self.assertEqual(
            payload["host_mutation"]["reason"],
            "host-mutation-in-flight",
        )
        with mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(
                MODULE.main(
                    ["status", "--home", str(self.home), "--json", "--strict"],
                    runtime,
                ),
                2,
            )

    def test_status_retains_aggregate_host_state_when_scheduler_is_unreadable(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        path = self.operation_state_path("controller-run")
        state = self.operation_state("activation")
        state["status"] = "process-cleanup-inconclusive"
        state["generation"] += 1
        path.write_bytes(MODULE._canonical_json_bytes(state))
        os.chmod(path, 0o600)
        runtime.scheduler_status_results = [
            MODULE.CommandResult(0, b"{not-json}\n", b""),
        ]

        payload, readable = MODULE._status_payload(runtime, self.home)

        self.assertFalse(readable)
        self.assertTrue(payload["operational_error"])
        self.assertIn("invalid canonical scheduler status JSON", payload["reason"])
        self.assertEqual(runtime.scheduler_status_results, [])
        self.assertEqual(
            len(
                [
                    call
                    for call in runtime.calls
                    if len(call) > 1 and call[1] == "status-scheduler"
                ]
            ),
            1,
        )
        self.assertEqual(
            payload["host_mutation"]["status"],
            "process-cleanup-inconclusive",
        )
        self.assertEqual(
            payload["host_mutation"]["reason"],
            "host-mutation-process-cleanup-inconclusive",
        )

    def test_status_retains_aggregate_host_state_on_scheduler_io_error(self):
        runtime = self.runtime(candidates=("standalone",), gui=True)
        MODULE.activate(
            runtime,
            self.home,
            requested_host_id="standalone",
            interval_minutes=30,
        )
        runtime.calls.clear()
        original_run = runtime.run

        def fail_scheduler(argv, *, timeout, output_limit, before_spawn=None):
            call = decode_runtime_call(argv)
            if len(call) > 1 and call[1] == "status-scheduler":
                raise OSError("injected scheduler transport failure")
            return original_run(
                argv,
                timeout=timeout,
                output_limit=output_limit,
                before_spawn=before_spawn,
            )

        with mock.patch.object(runtime, "run", side_effect=fail_scheduler):
            payload, readable = MODULE._status_payload(runtime, self.home)

        self.assertFalse(readable)
        self.assertTrue(payload["operational_error"])
        self.assertEqual(payload["reason"], "injected scheduler transport failure")
        self.assertEqual(payload["host_mutation"]["status"], "retryable")
        self.assertEqual(payload["host_mutation"]["operation"], "activation")

    def test_host_mutation_blocker_is_global_across_role_operations(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        path = self.operation_state_path("remote-apply")
        state = self.operation_state("activation")
        state.update(
            {
                "operation": "remote-apply",
                "host_id": self.target_id,
                "controller_id": self.controller_id,
                "scope_sha256": "9" * 64,
                "status": "in-flight",
                "generation": state["generation"] + 1,
            }
        )
        path.write_bytes(MODULE._canonical_json_bytes(state))
        os.chmod(path, 0o600)
        runtime.calls.clear()

        with self.assertRaisesRegex(
            MODULE.ProcessCleanupInconclusiveError,
            "durable process fence from remote-apply",
        ):
            MODULE.controller_run(runtime, self.home, strict=False)
        with self.assertRaisesRegex(
            MODULE.ProcessCleanupInconclusiveError,
            "durable process fence from remote-apply",
        ):
            MODULE.sync_target(
                runtime,
                self.home,
                target_id=self.target_id,
                force=True,
                strict=False,
            )
        with self.assertRaisesRegex(
            MODULE.ProcessCleanupInconclusiveError,
            "durable process fence from remote-apply",
        ):
            self.activate_controller(runtime)

        self.assertFalse(
            any(
                len(call) > 1
                and call[1] in ("run-scheduled", "install-scheduler")
                for call in runtime.calls
            )
        )
        self.assertEqual(len(self.ssh_calls(runtime)), 0)
        payload, readable = MODULE._status_payload(runtime, self.home)
        self.assertTrue(readable)
        self.assertTrue(payload["degraded"])
        self.assertEqual(payload["host_mutation"]["operation"], "remote-apply")
        self.assertFalse(payload["host_mutation"]["binding_current"])

    def test_remote_apply_blocker_prevents_standalone_local_sync(self):
        runtime = self.runtime(candidates=("standalone",), gui=True)
        MODULE.activate(
            runtime,
            self.home,
            requested_host_id="standalone",
            interval_minutes=30,
        )
        path = self.operation_state_path("remote-apply")
        state = self.operation_state("activation")
        state.update(
            {
                "operation": "remote-apply",
                "host_id": self.target_id,
                "controller_id": self.controller_id,
                "scope_sha256": "8" * 64,
                "status": "process-cleanup-inconclusive",
                "generation": state["generation"] + 1,
            }
        )
        path.write_bytes(MODULE._canonical_json_bytes(state))
        os.chmod(path, 0o600)
        runtime.calls.clear()

        with self.assertRaisesRegex(
            MODULE.ProcessCleanupInconclusiveError,
            "durable process fence from remote-apply",
        ):
            MODULE.controller_run(runtime, self.home, strict=False)

        self.assertFalse(
            any(
                len(call) > 1 and call[1] == "run-scheduled"
                for call in runtime.calls
            )
        )

    def test_controller_blocker_prevents_headless_remote_apply(self):
        runtime = self.runtime(candidates=(self.target_id,), gui=False)
        MODULE.activate(
            runtime,
            self.home,
            requested_host_id=self.target_id,
            interval_minutes=30,
        )
        path = self.operation_state_path("controller-run")
        state = self.operation_state("activation")
        state.update(
            {
                "operation": "controller-run",
                "host_id": self.controller_id,
                "controller_id": self.controller_id,
                "scope_sha256": "7" * 64,
                "status": "in-flight",
                "generation": state["generation"] + 1,
            }
        )
        path.write_bytes(MODULE._canonical_json_bytes(state))
        os.chmod(path, 0o600)
        runtime.calls.clear()

        with self.assertRaises(
            MODULE.RemoteProcessCleanupInconclusiveError,
        ) as raised:
            MODULE.remote_apply(
                runtime,
                self.home,
                host_id=self.target_id,
                controller_id=self.controller_id,
                expected=self.desired(),
            )
        self.assertIsInstance(
            raised.exception.__cause__,
            MODULE.ProcessCleanupInconclusiveError,
        )
        self.assertIn(
            "durable process fence from controller-run",
            str(raised.exception.__cause__),
        )

        self.assertFalse(
            any(
                len(call) > 1 and call[1] == "run-scheduled"
                for call in runtime.calls
            )
        )

    def test_standalone_attempt_rebinds_stale_retryable_host_state(self):
        runtime = self.runtime(candidates=("standalone",), gui=True)
        MODULE.activate(
            runtime,
            self.home,
            requested_host_id="standalone",
            interval_minutes=30,
        )
        path = self.operation_state_path("remote-apply")
        state = self.operation_state("activation")
        stale_generation = state["generation"] + 40
        state.update(
            {
                "operation": "remote-apply",
                "host_id": self.target_id,
                "controller_id": self.controller_id,
                "scope_sha256": "6" * 64,
                "status": "retryable",
                "generation": stale_generation,
            }
        )
        path.write_bytes(MODULE._canonical_json_bytes(state))
        os.chmod(path, 0o600)
        observed = []

        def observe_rebound_state():
            observed.append(self.operation_state("standalone-run"))

        runtime.run_scheduled_hook = observe_rebound_state
        self.assertTrue(MODULE.controller_run(runtime, self.home, strict=False))

        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]["operation"], "standalone-run")
        self.assertEqual(observed[0]["host_id"], "standalone")
        self.assertEqual(observed[0]["controller_id"], "standalone")
        self.assertEqual(observed[0]["status"], "in-flight")
        self.assertEqual(observed[0]["generation"], stale_generation + 1)
        final_state = self.operation_state("standalone-run")
        self.assertEqual(final_state["operation"], "standalone-run")
        self.assertEqual(final_state["status"], "retryable")
        self.assertEqual(final_state["generation"], stale_generation + 2)
        self.assertEqual(len(self.ssh_calls(runtime)), 0)

    def test_status_rejects_unsafe_and_malformed_operation_fence(self):
        for label in ("unsafe", "malformed"):
            with self.subTest(label=label):
                runtime = self.runtime()
                self.activate_controller(runtime)
                runtime.ssh_results = [self.json_result(self.success_receipt())]
                MODULE.controller_run(runtime, self.home, strict=False)
                path = self.operation_state_path("controller-run")
                original_payload = path.read_bytes()
                try:
                    if label == "unsafe":
                        os.chmod(path, 0o644)
                    else:
                        path.write_text("{}\n", encoding="utf-8")
                        os.chmod(path, 0o600)

                    payload, readable = MODULE._status_payload(runtime, self.home)

                    self.assertFalse(readable)
                    self.assertTrue(payload["operational_error"])
                    with mock.patch("sys.stdout", new=io.StringIO()):
                        self.assertEqual(
                            MODULE.main(
                                [
                                    "status",
                                    "--home",
                                    str(self.home),
                                    "--json",
                                    "--strict",
                                ],
                                runtime,
                            ),
                            1,
                        )
                finally:
                    path.write_bytes(original_payload)
                    os.chmod(path, 0o600)

    def test_unsupported_role_specific_operation_artifacts_fail_closed(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME

        for name in sorted(MODULE.UNSUPPORTED_OPERATION_FILES):
            with self.subTest(name=name):
                path = state_directory / name
                path.write_bytes(b"legacy artifact\n")
                os.chmod(path, 0o600)
                runtime.calls.clear()
                try:
                    with self.assertRaisesRegex(
                        MODULE.UnsupportedOperationArtifactError,
                        "manual cleanup",
                    ):
                        MODULE.controller_run(runtime, self.home, strict=False)
                    self.assertFalse(
                        any(
                            len(call) > 1 and call[1] == "run-scheduled"
                            for call in runtime.calls
                        )
                    )
                    self.assertEqual(len(self.ssh_calls(runtime)), 0)

                    payload, readable = MODULE._status_payload(runtime, self.home)
                    self.assertFalse(readable)
                    self.assertTrue(payload["operational_error"])

                    runtime.calls.clear()
                    with self.assertRaisesRegex(
                        MODULE.UnsupportedOperationArtifactError,
                        "manual cleanup",
                    ):
                        self.activate_controller(runtime)
                    self.assertFalse(
                        any(
                            len(call) > 1 and call[1] == "install-scheduler"
                            for call in runtime.calls
                        )
                    )
                finally:
                    path.unlink()

    def test_unreadable_unsupported_operation_artifact_uses_specific_error(self):
        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        state_directory.mkdir(mode=0o700)
        directory_fd = os.open(state_directory, os.O_RDONLY)
        first_name = sorted(MODULE.UNSUPPORTED_OPERATION_FILES)[0]
        original_stat = MODULE.os.stat

        def fail_unsupported(name, *args, **kwargs):
            if name == first_name:
                raise PermissionError(errno.EACCES, "injected unreadable artifact")
            return original_stat(name, *args, **kwargs)

        try:
            with mock.patch.object(MODULE.os, "stat", side_effect=fail_unsupported):
                with self.assertRaisesRegex(
                    MODULE.UnsupportedOperationArtifactError,
                    "unsupported operation artifact is unreadable",
                ):
                    MODULE._reject_unsupported_operation_files(directory_fd)
        finally:
            os.close(directory_fd)

    def test_status_strict_rejects_missing_disabled_and_wrong_runner_scheduler(self):
        cases = (
            ("missing", None, True, "enabled", "scheduler-not-installed"),
            (
                "disabled",
                "private",
                False,
                "disabled",
                "scheduler-daemon-disabled",
            ),
            (
                "wrong-runner",
                "wrong",
                True,
                "enabled",
                "scheduler-runner-mismatch",
            ),
        )
        for label, state_kind, enabled, classification, reason in cases:
            with self.subTest(label=label):
                runtime = self.runtime()
                self.activate_controller(runtime)
                runtime.ssh_results = [self.json_result(self.success_receipt())]
                MODULE.controller_run(runtime, self.home, strict=False)
                if state_kind is None:
                    runtime.scheduler_state = None
                elif state_kind == "wrong":
                    runtime.scheduler_state["runner"] = str(
                        self.home / "bin" / "unexpected-runner"
                    )
                runtime.scheduler_enabled = enabled
                runtime.scheduler_daemon_classification = classification

                payload, readable = MODULE._status_payload(runtime, self.home)

                self.assertTrue(readable)
                self.assertTrue(payload["degraded"])
                self.assertFalse(payload["operational_error"])
                self.assertEqual(payload["scheduler"]["reason"], reason)
                with mock.patch("sys.stdout", new=io.StringIO()):
                    self.assertEqual(
                        MODULE.main(
                            [
                                "status",
                                "--home",
                                str(self.home),
                                "--json",
                                "--strict",
                            ],
                            runtime,
                        ),
                        2,
                    )

    def test_controller_scheduler_compatibility_rejects_any_extra_failure(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.ssh_results = [self.json_result(self.success_receipt())]
        MODULE.controller_run(runtime, self.home, strict=False)
        report = self.scheduler_payload(runtime)
        report["failures"].append(
            {
                "code": "scheduler-daemon-unavailable",
                "reason": "additional scheduler uncertainty",
            }
        )
        runtime.scheduler_status_results = [self.json_result(report, returncode=1)]

        payload, readable = MODULE._status_payload(runtime, self.home)

        self.assertTrue(readable)
        self.assertFalse(payload["scheduler"]["healthy"])
        self.assertEqual(
            payload["scheduler"]["reason"],
            "scheduler-status-not-clean",
        )

    def test_controller_scheduler_rejects_migration_needed(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.ssh_results = [self.json_result(self.success_receipt())]
        MODULE.controller_run(runtime, self.home, strict=False)
        report = self.scheduler_payload(runtime)
        report["migration_needed"] = True
        runtime.scheduler_status_results = [self.json_result(report, returncode=1)]

        payload, readable = MODULE._status_payload(runtime, self.home)

        self.assertTrue(readable)
        self.assertFalse(payload["scheduler"]["healthy"])
        self.assertEqual(
            payload["scheduler"]["reason"],
            "scheduler-config-mismatch",
        )

    def test_standalone_and_headless_scheduler_roles_fail_closed(self):
        standalone = self.runtime(candidates=("standalone",), gui=True)
        MODULE.activate(
            standalone,
            self.home,
            requested_host_id="standalone",
            interval_minutes=30,
        )
        payload, readable = MODULE._status_payload(standalone, self.home)
        self.assertTrue(readable)
        self.assertTrue(payload["scheduler"]["healthy"])
        self.assertTrue(payload["host_mutation"]["healthy"])
        self.assertEqual(payload["host_mutation"]["status"], "retryable")
        self.assertEqual(payload["host_mutation"]["operation"], "activation")
        self.assertFalse(payload["host_mutation"]["binding_current"])
        self.assertEqual(
            payload["scheduler"]["runner"],
            str(self.home / "bin" / "codex-private-macos-sync"),
        )
        self.assertEqual(
            payload["scheduler"]["compatibility_exception"],
            "canonical-private-runner-drift",
        )
        standalone.scheduler_state["runner"] = str(
            self.home / "bin" / "codex-personal-sync"
        )
        payload, readable = MODULE._status_payload(standalone, self.home)
        self.assertTrue(readable)
        self.assertFalse(payload["scheduler"]["healthy"])
        self.assertEqual(
            payload["scheduler"]["reason"],
            "scheduler-runner-mismatch",
        )

        headless = self.runtime(candidates=(self.target_id,), gui=False)
        MODULE.activate(
            headless,
            self.home,
            requested_host_id=self.target_id,
            interval_minutes=30,
        )
        payload, readable = MODULE._status_payload(headless, self.home)
        self.assertTrue(readable)
        self.assertTrue(payload["scheduler"]["healthy"])
        self.assertTrue(payload["host_mutation"]["healthy"])
        self.assertEqual(payload["host_mutation"]["status"], "retryable")
        headless.scheduler_state = {
            "runner": str(self.home / "bin" / "codex-personal-sync"),
            "interval_minutes": "30",
            "mode": "private",
            "repo": MODULE.PRIVATE_REPO,
            "base_repo": MODULE.PUBLIC_REPO,
            "owner": MODULE.PRIVATE_OWNER,
        }
        payload, readable = MODULE._status_payload(headless, self.home)
        self.assertTrue(readable)
        self.assertFalse(payload["scheduler"]["healthy"])
        self.assertEqual(
            payload["scheduler"]["reason"],
            "headless-scheduler-present-or-uncertain",
        )

    def test_remote_receipt_strictly_binds_host_scope_inventory_and_shape(self):
        snapshot = self.current_snapshot()
        controller = snapshot.inventory.hosts[self.controller_id]
        target = snapshot.inventory.hosts[self.target_id]
        scope = MODULE._scope_digest(controller, target)
        baseline = self.success_receipt()
        mutations = []
        for field, value in (
            ("controller_id", "wrong-controller"),
            ("target_id", "wrong-target"),
            ("scope_sha256", "e" * 64),
            ("inventory_release_sha", "9" * 40),
            ("inventory_sha256", "9" * 64),
        ):
            changed = json.loads(json.dumps(baseline))
            changed[field] = value
            mutations.append((field, json.dumps(changed).encode("utf-8")))
        extra = json.loads(json.dumps(baseline))
        extra["extra"] = True
        mutations.append(("extra", json.dumps(extra).encode("utf-8")))
        valid_payload = json.dumps(baseline, sort_keys=True).encode("utf-8")
        mutations.append(("banner", b"remote banner\n" + valid_payload))
        duplicate = valid_payload.replace(
            b'{"controller_id"',
            b'{"version":1,"controller_id"',
            1,
        )
        mutations.append(("duplicate", duplicate))

        for label, payload in mutations:
            with self.subTest(label=label):
                with self.assertRaises(MODULE.ControllerError):
                    MODULE._parse_remote_receipt(
                        payload,
                        controller,
                        target,
                        self.desired(),
                        scope,
                        snapshot,
                    )

    def test_remote_cleanup_receipt_strictly_binds_request_and_shape(self):
        snapshot = self.current_snapshot()
        controller = snapshot.inventory.hosts[self.controller_id]
        target = snapshot.inventory.hosts[self.target_id]
        baseline = MODULE._remote_cleanup_receipt(
            self.controller_id,
            self.target_id,
            self.desired(),
        )
        MODULE._parse_remote_cleanup_receipt(
            json.dumps(baseline).encode("utf-8"),
            controller,
            target,
            self.desired(),
        )
        mutations = []
        wrong_request = dict(baseline)
        wrong_request["request_sha256"] = "e" * 64
        mutations.append(json.dumps(wrong_request).encode("utf-8"))
        extra = dict(baseline)
        extra["extra"] = True
        mutations.append(json.dumps(extra).encode("utf-8"))
        valid = json.dumps(baseline, sort_keys=True).encode("utf-8")
        mutations.append(b"remote banner\n" + valid)
        mutations.append(
            valid.replace(
                b'{"controller_id"',
                b'{"version":1,"controller_id"',
                1,
            )
        )
        for payload in mutations:
            with self.subTest(payload=payload[:32]):
                with self.assertRaises(MODULE.ControllerError):
                    MODULE._parse_remote_cleanup_receipt(
                        payload,
                        controller,
                        target,
                        self.desired(),
                    )

    def test_status_reports_release_unconfirmed_after_out_of_band_update(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        runtime.ssh_results = [self.json_result(self.success_receipt())]
        MODULE.controller_run(runtime, self.home, strict=False)

        self.write_release(NEXT_PRIVATE_SHA, self.inventory_data())
        self.switch_current(NEXT_PRIVATE_SHA)
        runtime.identity_pair = self.desired(NEXT_PRIVATE_SHA, NEXT_PRIVATE_TREE)
        payload, readable = MODULE._status_payload(runtime, self.home)

        self.assertTrue(readable)
        self.assertTrue(payload["degraded"])
        self.assertEqual(payload["targets"][0]["reason"], "release-unconfirmed")

    def test_local_current_inventory_change_after_ssh_refuses_ack(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        self.write_release(NEXT_PRIVATE_SHA, self.inventory_data())

        def ssh_result(_call):
            receipt = self.success_receipt()
            self.switch_current(NEXT_PRIVATE_SHA)
            runtime.identity_pair = self.desired(NEXT_PRIVATE_SHA, NEXT_PRIVATE_TREE)
            return self.json_result(receipt)

        runtime.ssh_factory = ssh_result
        self.assertTrue(MODULE.controller_run(runtime, self.home, strict=False))
        state = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertTrue(state["pending"])
        self.assertEqual(state["last_error"], "local-scope-changed")

    def test_role_change_after_local_sync_marks_existing_target_pending(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        runtime.ssh_results = [self.json_result(self.success_receipt())]
        MODULE.controller_run(runtime, self.home, strict=False)
        runtime.calls.clear()

        extra_target = {
            "id": "new-headless",
            "role": "headless-managed",
            "username": "hoteng",
            "uid": self.uid,
            "home": str(self.account_home),
            "controller": self.controller_id,
            "ssh_alias": "new-headless-ssh",
        }
        self.write_release(
            NEXT_PRIVATE_SHA,
            self.inventory_data(extra_hosts=[extra_target]),
        )

        def update_scope():
            self.switch_current(NEXT_PRIVATE_SHA)
            runtime.identity_pair = self.desired(
                NEXT_PRIVATE_SHA, NEXT_PRIVATE_TREE
            )

        runtime.run_scheduled_hook = update_scope
        with self.assertRaisesRegex(MODULE.ControllerError, "role-activation-required"):
            MODULE.controller_run(runtime, self.home, strict=False)

        state = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertTrue(state["pending"])
        self.assertEqual(state["last_error"], "local-scope-changed")
        self.assertEqual(len(self.ssh_calls(runtime)), 0)
        self.assertEqual(len(self.notification_calls(runtime)), 1)

    def test_malformed_receipt_and_state_fail_closed(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        receipt = self.home / MODULE.STATE_DIRECTORY_NAME / MODULE.ACTIVATION_FILE_NAME
        receipt.write_text('{"version":1,"version":1}\n', encoding="utf-8")
        os.chmod(receipt, 0o600)
        payload, readable = MODULE._status_payload(runtime, self.home)
        self.assertFalse(readable)
        self.assertTrue(payload["operational_error"])

        self.activate_controller(runtime)
        state = self.state_path()
        state.write_text("{}\n", encoding="utf-8")
        os.chmod(state, 0o600)
        payload, readable = MODULE._status_payload(runtime, self.home)
        self.assertFalse(readable)
        self.assertTrue(payload["operational_error"])

    def test_state_symlink_and_access_policy_are_rejected(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        outside = self.root / "outside.json"
        outside.write_text("{}\n", encoding="utf-8")
        self.state_path().symlink_to(outside)
        payload, readable = MODULE._status_payload(runtime, self.home)
        self.assertFalse(readable)
        self.assertTrue(payload["operational_error"])

    def test_receipt_and_target_state_mode_drift_fail_closed(self):
        for case in ("receipt", "target-state"):
            with self.subTest(case=case):
                runtime = self.runtime()
                self.activate_controller(runtime)
                if case == "target-state":
                    runtime.ssh_results = [
                        self.json_result(self.success_receipt())
                    ]
                    MODULE.controller_run(runtime, self.home, strict=False)
                    path = self.state_path()
                else:
                    path = (
                        self.home
                        / MODULE.STATE_DIRECTORY_NAME
                        / MODULE.ACTIVATION_FILE_NAME
                    )
                os.chmod(path, 0o644)
                payload, readable = MODULE._status_payload(runtime, self.home)
                self.assertFalse(readable)
                self.assertTrue(payload["operational_error"])
                os.chmod(path, 0o600)

    def test_access_policy_no_drift_uses_one_acl_pass(self):
        stable = AccessPolicyMetadata(
            mode=stat.S_IFREG | 0o600,
            uid=self.uid,
            ctime_ns=101,
        )
        api = DeterministicAclApi()
        with mock.patch.object(MODULE.sys, "platform", "darwin"):
            with mock.patch.object(MODULE, "_darwin_acl_api", return_value=api):
                with mock.patch.object(
                    MODULE.os,
                    "fstat",
                    side_effect=(stable, stable),
                ) as fstat_call:
                    binding = MODULE._validate_fd_access_policy(
                        17,
                        self.uid,
                        "stable access-policy probe",
                    )
        self.assertEqual(
            binding,
            (self.uid, "expected-owner-and-no-nonowner-allow-v1"),
        )
        self.assertEqual(api.pass_count, 1)
        self.assertEqual(fstat_call.call_count, 2)

    def test_metadata_identity_binds_gid_but_ignores_benign_directory_churn(self):
        initial = AccessPolicyMetadata(
            mode=stat.S_IFDIR | 0o700,
            uid=self.uid,
            gid=41,
            ctime_ns=101,
            nlink=2,
        )
        gid_drift = AccessPolicyMetadata(
            mode=initial.st_mode,
            uid=initial.st_uid,
            gid=43,
            ctime_ns=initial.st_ctime_ns,
            nlink=initial.st_nlink,
            dev=initial.st_dev,
            ino=initial.st_ino,
        )
        benign_churn = AccessPolicyMetadata(
            mode=initial.st_mode,
            uid=initial.st_uid,
            gid=initial.st_gid,
            ctime_ns=initial.st_ctime_ns + 1,
            nlink=initial.st_nlink + 1,
            dev=initial.st_dev,
            ino=initial.st_ino,
        )

        self.assertNotEqual(
            MODULE._metadata_identity(initial),
            MODULE._metadata_identity(gid_drift),
        )
        self.assertEqual(
            MODULE._metadata_identity(initial),
            MODULE._metadata_identity(benign_churn),
        )

    def test_access_policy_stable_unsafe_acl_is_rejected_without_retry(self):
        stable = AccessPolicyMetadata(
            mode=stat.S_IFREG | 0o600,
            uid=self.uid,
            ctime_ns=103,
        )
        api = DeterministicAclApi(unsafe_passes=(1,))
        with mock.patch.object(MODULE.sys, "platform", "darwin"):
            with mock.patch.object(MODULE, "_darwin_acl_api", return_value=api):
                with mock.patch.object(
                    MODULE.os,
                    "fstat",
                    side_effect=(stable, stable),
                ) as fstat_call:
                    with self.assertRaisesRegex(
                        MODULE.ControllerError,
                        "non-owner ALLOW",
                    ):
                        MODULE._validate_fd_access_policy(
                            17,
                            self.uid,
                            "unsafe access-policy probe",
                        )
        self.assertEqual(api.pass_count, 1)
        self.assertEqual(fstat_call.call_count, 2)

    def test_access_policy_stable_unsafe_nlink_is_rejected_without_retry(self):
        stable = AccessPolicyMetadata(
            mode=stat.S_IFREG | 0o600,
            uid=self.uid,
            ctime_ns=105,
            nlink=2,
        )
        api = DeterministicAclApi()
        with mock.patch.object(MODULE.sys, "platform", "darwin"):
            with mock.patch.object(MODULE, "_darwin_acl_api", return_value=api):
                with mock.patch.object(
                    MODULE.os,
                    "fstat",
                    side_effect=(stable, stable),
                ) as fstat_call:
                    with self.assertRaisesRegex(
                        MODULE.ControllerError,
                        "unsafe regular-file link count",
                    ):
                        MODULE._validate_fd_access_policy(
                            17,
                            self.uid,
                            "unsafe nlink probe",
                        )
        self.assertEqual(api.pass_count, 1)
        self.assertEqual(fstat_call.call_count, 2)

    def test_access_policy_first_drift_retries_then_rejects_unsafe_policy(self):
        regular_mode = stat.S_IFREG | 0o600
        cases = (
            (
                "mode",
                AccessPolicyMetadata(
                    mode=regular_mode | 0o020,
                    uid=self.uid,
                    ctime_ns=109,
                ),
                DeterministicAclApi(),
                "access policy changed during validation",
            ),
            (
                "uid",
                AccessPolicyMetadata(
                    mode=regular_mode,
                    uid=self.uid + 1,
                    ctime_ns=109,
                ),
                DeterministicAclApi(),
                "access policy changed during validation",
            ),
            (
                "nlink",
                AccessPolicyMetadata(
                    mode=regular_mode,
                    uid=self.uid,
                    ctime_ns=109,
                    nlink=2,
                ),
                DeterministicAclApi(),
                "access policy changed during validation",
            ),
            (
                "acl",
                AccessPolicyMetadata(
                    mode=regular_mode,
                    uid=self.uid,
                    ctime_ns=109,
                ),
                DeterministicAclApi(unsafe_passes=(2,)),
                "non-owner ALLOW",
            ),
        )
        for case, changed, api, expected_error in cases:
            with self.subTest(case=case):
                initial = AccessPolicyMetadata(
                    mode=regular_mode,
                    uid=self.uid,
                    ctime_ns=107,
                )
                with mock.patch.object(MODULE.sys, "platform", "darwin"):
                    with mock.patch.object(
                        MODULE,
                        "_darwin_acl_api",
                        return_value=api,
                    ):
                        with mock.patch.object(
                            MODULE.os,
                            "fstat",
                            side_effect=(initial, changed, changed, changed),
                        ) as fstat_call:
                            with self.assertRaisesRegex(
                                MODULE.ControllerError,
                                expected_error,
                            ):
                                MODULE._validate_fd_access_policy(
                                    17,
                                    self.uid,
                                    f"{case} drift probe",
                                )
                self.assertEqual(api.pass_count, 2)
                self.assertEqual(fstat_call.call_count, 4)

    def test_access_policy_benign_first_ctime_drift_then_stable_is_accepted(self):
        before = AccessPolicyMetadata(
            mode=stat.S_IFDIR | 0o700,
            uid=self.uid,
            ctime_ns=113,
            nlink=2,
        )
        stable = AccessPolicyMetadata(
            mode=stat.S_IFDIR | 0o700,
            uid=self.uid,
            ctime_ns=127,
            nlink=2,
        )
        api = DeterministicAclApi()
        with mock.patch.object(MODULE.sys, "platform", "darwin"):
            with mock.patch.object(MODULE, "_darwin_acl_api", return_value=api):
                with mock.patch.object(
                    MODULE.os,
                    "fstat",
                    side_effect=(before, stable, stable, stable),
                ) as fstat_call:
                    binding = MODULE._validate_fd_access_policy(
                        17,
                        self.uid,
                        "benign ctime drift probe",
                    )
        self.assertEqual(
            binding,
            (self.uid, "expected-owner-and-no-nonowner-allow-v1"),
        )
        self.assertEqual(api.pass_count, 2)
        self.assertEqual(fstat_call.call_count, 4)

    def test_access_policy_second_drift_fails_closed_after_bounded_retry(self):
        samples = tuple(
            AccessPolicyMetadata(
                mode=stat.S_IFREG | 0o600,
                uid=self.uid,
                ctime_ns=ctime_ns,
            )
            for ctime_ns in (131, 137, 137, 139)
        )
        api = DeterministicAclApi()
        with mock.patch.object(MODULE.sys, "platform", "darwin"):
            with mock.patch.object(MODULE, "_darwin_acl_api", return_value=api):
                with mock.patch.object(
                    MODULE.os,
                    "fstat",
                    side_effect=samples,
                ) as fstat_call:
                    with self.assertRaisesRegex(
                        MODULE.StatePublicationError,
                        "descriptor changed during validation",
                    ):
                        MODULE._validate_fd_access_policy(
                            17,
                            self.uid,
                            "repeated drift probe",
                            error_type=MODULE.StatePublicationError,
                        )
        self.assertEqual(api.pass_count, 2)
        self.assertEqual(fstat_call.call_count, 4)

    def test_darwin_acl_enumeration_uses_first_then_repeated_next(self):
        selectors = []

        class FakeAclApi:
            @staticmethod
            def acl_get_fd_np(_fd, _acl_type):
                return 1

            @staticmethod
            def acl_valid(_acl):
                return 0

            @staticmethod
            def mbr_uid_to_uuid(_uid, owner_uuid):
                for index in range(16):
                    owner_uuid[index] = index
                return 0

            @staticmethod
            def acl_get_entry(_acl, selector, entry_out):
                selectors.append(selector)
                if len(selectors) <= 2:
                    entry_out._obj.value = len(selectors)
                    return 0
                ctypes.set_errno(errno.EINVAL)
                return -1

            @staticmethod
            def acl_get_tag_type(_entry, tag_out):
                tag_out._obj.value = MODULE.DARWIN_ACL_EXTENDED_DENY
                return 0

            @staticmethod
            def acl_get_qualifier(_entry):
                raise AssertionError("DENY entries have no qualifier lookup")

            @staticmethod
            def acl_free(_value):
                return 0

        descriptor = os.open(self.home, os.O_RDONLY)
        try:
            with mock.patch.object(MODULE.sys, "platform", "darwin"):
                with mock.patch.object(
                    MODULE,
                    "_darwin_acl_api",
                    return_value=FakeAclApi(),
                ):
                    binding = MODULE._validate_fd_access_policy(
                        descriptor,
                        self.uid,
                        "selector probe",
                    )
        finally:
            os.close(descriptor)

        self.assertEqual(
            selectors,
            [
                MODULE.DARWIN_ACL_FIRST_ENTRY,
                MODULE.DARWIN_ACL_NEXT_ENTRY,
                MODULE.DARWIN_ACL_NEXT_ENTRY,
            ],
        )
        self.assertEqual(
            binding,
            (self.uid, "expected-owner-and-no-nonowner-allow-v1"),
        )

    @unittest.skipUnless(sys.platform == "darwin", "requires Darwin extended ACLs")
    def test_darwin_deny_and_exact_owner_allow_acls_are_safe(self):
        username = MODULE.pwd.getpwuid(self.uid).pw_name
        for rule in (
            "everyone deny delete",
            f"user:{username} allow read",
        ):
            with self.subTest(rule=rule):
                self.add_extended_acl(self.home, rule)
                try:
                    snapshot = self.current_snapshot()
                    self.assertEqual(snapshot.release_sha, PRIVATE_SHA)
                finally:
                    self.clear_extended_acl(self.home)

    @unittest.skipUnless(sys.platform == "darwin", "requires Darwin extended ACLs")
    def test_darwin_safe_multi_entry_acl_is_accepted(self):
        username = MODULE.pwd.getpwuid(self.uid).pw_name
        for rule in (
            "everyone deny delete",
            f"user:{username} allow read",
        ):
            self.add_extended_acl(self.home, rule)
        try:
            snapshot = self.current_snapshot()
            self.assertEqual(snapshot.release_sha, PRIVATE_SHA)
        finally:
            self.clear_extended_acl(self.home)

    @unittest.skipUnless(sys.platform == "darwin", "requires Darwin extended ACLs")
    def test_darwin_unsafe_later_acl_entry_is_rejected(self):
        for rule in (
            "everyone deny delete",
            "everyone allow read",
        ):
            self.add_extended_acl(self.home, rule)
        try:
            with self.assertRaisesRegex(
                MODULE.ControllerError,
                "non-owner ALLOW",
            ):
                self.current_snapshot()
        finally:
            self.clear_extended_acl(self.home)

    @unittest.skipUnless(sys.platform == "darwin", "requires Darwin extended ACLs")
    def test_darwin_safe_acl_churn_does_not_count_as_mutation(self):
        inventory = (
            self.home
            / "personal-sync"
            / "overlays"
            / "private"
            / "releases"
            / PRIVATE_SHA
            / "personal_codex"
            / "private-sync-hosts.json"
        )
        original = MODULE._read_fd_twice
        changed = False

        def add_safe_deny(fd, limit, label):
            nonlocal changed
            payload = original(fd, limit, label)
            if label == "private host inventory" and not changed:
                changed = True
                self.add_extended_acl(inventory, "everyone deny delete")
            return payload

        try:
            with mock.patch.object(MODULE, "_read_fd_twice", side_effect=add_safe_deny):
                snapshot = self.current_snapshot()
            self.assertTrue(changed)
            self.assertEqual(snapshot.release_sha, PRIVATE_SHA)
        finally:
            if changed:
                self.clear_extended_acl(inventory)

    @unittest.skipUnless(sys.platform == "darwin", "requires Darwin extended ACLs")
    def test_darwin_inventory_chain_and_current_reject_nonowner_allow(self):
        overlay = self.home / "personal-sync" / "overlays" / "private"
        paths = (
            self.home,
            overlay,
            overlay / "current",
            overlay / "releases" / PRIVATE_SHA,
            overlay
            / "releases"
            / PRIVATE_SHA
            / "personal_codex"
            / "private-sync-hosts.json",
        )
        for path in paths:
            with self.subTest(path=path):
                self.add_extended_acl(path, "everyone allow read")
                try:
                    with self.assertRaisesRegex(
                        MODULE.ControllerError,
                        "non-owner ALLOW",
                    ):
                        self.current_snapshot()
                finally:
                    self.clear_extended_acl(path)

    @unittest.skipUnless(sys.platform == "darwin", "requires Darwin extended ACLs")
    def test_darwin_acl_only_inventory_drift_fails_closed(self):
        inventory = (
            self.home
            / "personal-sync"
            / "overlays"
            / "private"
            / "releases"
            / PRIVATE_SHA
            / "personal_codex"
            / "private-sync-hosts.json"
        )
        original = MODULE._read_fd_twice
        changed = False

        def add_unsafe_allow(fd, limit, label):
            nonlocal changed
            payload = original(fd, limit, label)
            if label == "private host inventory" and not changed:
                changed = True
                self.add_extended_acl(inventory, "everyone allow read")
            return payload

        try:
            with mock.patch.object(MODULE, "_read_fd_twice", side_effect=add_unsafe_allow):
                with self.assertRaisesRegex(
                    MODULE.ControllerError,
                    "non-owner ALLOW",
                ):
                    self.current_snapshot()
            self.assertTrue(changed)
        finally:
            if changed:
                self.clear_extended_acl(inventory)

    @unittest.skipUnless(sys.platform == "darwin", "requires Darwin extended ACLs")
    def test_darwin_state_and_operation_lock_acl_block_before_child(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        receipt = state_directory / MODULE.ACTIVATION_FILE_NAME
        operation_lock = state_directory / MODULE.HOST_MUTATION_LOCK_NAME
        inventory = (
            self.home
            / "personal-sync"
            / "overlays"
            / "private"
            / "releases"
            / PRIVATE_SHA
            / "personal_codex"
            / "private-sync-hosts.json"
        )
        operation_lock.touch(mode=0o600)
        os.chmod(operation_lock, 0o600)
        for path in (state_directory, receipt, operation_lock, inventory):
            with self.subTest(path=path):
                self.add_extended_acl(path, "everyone allow read")
                runtime.calls.clear()
                try:
                    with self.assertRaisesRegex(
                        MODULE.ControllerError,
                        "non-owner ALLOW",
                    ):
                        MODULE.controller_run(runtime, self.home, strict=False)
                    self.assertEqual(runtime.calls, [])
                finally:
                    self.clear_extended_acl(path)

    @unittest.skipUnless(sys.platform == "darwin", "requires Darwin extended ACLs")
    def test_darwin_all_lock_kinds_reject_nonowner_allow(self):
        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        state_directory.mkdir(mode=0o700)
        cases = (
            (
                "activation",
                "activation.lock",
                lambda runtime: MODULE._activation_lock(
                    self.home,
                    self.uid,
                    runtime,
                ),
            ),
            (
                "target",
                f"target-{self.target_id}.lock",
                lambda runtime: MODULE._target_lock(
                    self.home,
                    self.uid,
                    self.target_id,
                    runtime,
                ),
            ),
            (
                "host-writer",
                MODULE.HOST_MUTATION_LOCK_NAME,
                lambda runtime: MODULE._host_mutation_lock(
                    self.home,
                    self.uid,
                    runtime,
                ),
            ),
            (
                "host-status",
                MODULE.HOST_MUTATION_LOCK_NAME,
                lambda runtime: MODULE._status_snapshot_lock(
                    self.home,
                    self.uid,
                    runtime,
                ),
            ),
        )
        for label, name, context_factory in cases:
            with self.subTest(label=label, name=name):
                lock = state_directory / name
                lock.touch(mode=0o600)
                os.chmod(lock, 0o600)
                self.add_extended_acl(lock, "everyone allow read")
                try:
                    with self.assertRaisesRegex(
                        MODULE.StatePublicationError,
                        "non-owner ALLOW",
                    ):
                        with context_factory(self.runtime()):
                            self.fail("unsafe lock was acquired")
                finally:
                    self.clear_extended_acl(lock)

    def test_non_darwin_acl_check_does_not_resolve_darwin_symbols(self):
        descriptor = os.open(self.home, os.O_RDONLY)
        try:
            with mock.patch.object(MODULE.sys, "platform", "linux"):
                with mock.patch.object(MODULE.ctypes, "CDLL") as loader:
                    binding = MODULE._validate_fd_access_policy(
                        descriptor,
                        self.uid,
                        "non-Darwin probe",
                    )
        finally:
            os.close(descriptor)
        self.assertEqual(
            binding,
            (self.uid, "expected-owner-and-no-nonowner-allow-v1"),
        )
        loader.assert_not_called()

    def test_darwin_acl_api_resolution_failure_is_unverifiable(self):
        descriptor = os.open(self.home, os.O_RDONLY)
        try:
            with mock.patch.object(MODULE.sys, "platform", "darwin"):
                with mock.patch.object(MODULE, "_DARWIN_ACL_API", None):
                    with mock.patch.object(
                        MODULE.ctypes,
                        "CDLL",
                        side_effect=OSError("unavailable"),
                    ):
                        with self.assertRaisesRegex(
                            MODULE.ControllerError,
                            "could not be verified",
                        ):
                            MODULE._validate_fd_access_policy(
                                descriptor,
                                self.uid,
                                "Darwin API probe",
                            )
        finally:
            os.close(descriptor)

    def test_temporary_state_acl_is_checked_before_first_write(self):
        original = MODULE._validate_fd_access_policy

        def reject_temporary(fd, uid, label, **kwargs):
            if label == "temporary state":
                raise MODULE.StatePublicationError(
                    "temporary state extended ACL policy grants a non-owner ALLOW entry"
                )
            return original(fd, uid, label, **kwargs)

        with MODULE._open_state_directory(self.home, self.uid, create=True) as (
            _path,
            directory_fd,
        ):
            with mock.patch.object(
                MODULE,
                "_validate_fd_access_policy",
                side_effect=reject_temporary,
            ):
                with mock.patch.object(MODULE.os, "write", wraps=os.write) as write:
                    with self.assertRaisesRegex(
                        MODULE.StatePublicationError,
                        "non-owner ALLOW",
                    ):
                        MODULE._atomic_publish(
                            directory_fd,
                            "test.json",
                            b"{}\n",
                            self.uid,
                            MODULE.FileSnapshot(False),
                        )
                    write.assert_not_called()

    def test_atomic_publication_normalizes_bound_read_policy_failure(self):
        with MODULE._open_state_directory(self.home, self.uid, create=True) as (
            _path,
            directory_fd,
        ):
            with mock.patch.object(
                MODULE,
                "_read_bound_state_file",
                side_effect=MODULE.ControllerError("injected unsafe ACL"),
            ):
                with self.assertRaisesRegex(
                    MODULE.StatePublicationError,
                    "publication binding is unsafe",
                ):
                    MODULE._atomic_publish(
                        directory_fd,
                        "test.json",
                        b"{}\n",
                        self.uid,
                        MODULE.FileSnapshot(False),
                    )

    def test_durable_sync_uses_exact_platform_primitive_without_fallback(self):
        with mock.patch.object(MODULE.sys, "platform", "darwin"):
            with mock.patch.object(
                MODULE.fcntl,
                "F_FULLFSYNC",
                12345,
                create=True,
            ):
                with mock.patch.object(MODULE.fcntl, "fcntl") as fullsync:
                    with mock.patch.object(MODULE.os, "fsync") as fsync:
                        MODULE._durable_sync(17)
                        fullsync.assert_called_once_with(17, 12345)
                        fsync.assert_not_called()

            with mock.patch.object(
                MODULE.fcntl,
                "F_FULLFSYNC",
                None,
                create=True,
            ):
                with mock.patch.object(MODULE.fcntl, "fcntl") as fullsync:
                    with mock.patch.object(MODULE.os, "fsync") as fsync:
                        with self.assertRaisesRegex(
                            MODULE.StatePublicationError,
                            "requires Darwin F_FULLFSYNC",
                        ):
                            MODULE._durable_sync(18)
                        fullsync.assert_not_called()
                        fsync.assert_not_called()

            with mock.patch.object(
                MODULE.fcntl,
                "F_FULLFSYNC",
                12345,
                create=True,
            ):
                with mock.patch.object(
                    MODULE.fcntl,
                    "fcntl",
                    side_effect=OSError("injected fullsync failure"),
                ):
                    with mock.patch.object(MODULE.os, "fsync") as fsync:
                        with self.assertRaisesRegex(
                            MODULE.StatePublicationError,
                            "durable sync failed",
                        ):
                            MODULE._durable_sync(19)
                        fsync.assert_not_called()

        with mock.patch.object(MODULE.sys, "platform", "linux"):
            with mock.patch.object(MODULE.fcntl, "fcntl") as fullsync:
                with mock.patch.object(MODULE.os, "fsync") as fsync:
                    MODULE._durable_sync(20)
                    fsync.assert_called_once_with(20)
                    fullsync.assert_not_called()

    def test_atomic_publication_fullsync_order_and_pre_rename_failure(self):
        with MODULE._open_state_directory(self.home, self.uid, create=True) as (
            _path,
            directory_fd,
        ):
            events = []
            real_replace = MODULE.os.replace

            def record_sync(fd):
                events.append(
                    "directory-sync"
                    if stat.S_ISDIR(os.fstat(fd).st_mode)
                    else "temporary-sync"
                )

            def record_replace(*args, **kwargs):
                events.append("replace")
                return real_replace(*args, **kwargs)

            with mock.patch.object(MODULE, "_durable_sync", side_effect=record_sync):
                with mock.patch.object(
                    MODULE.os,
                    "replace",
                    side_effect=record_replace,
                ):
                    MODULE._atomic_publish(
                        directory_fd,
                        "ordered.json",
                        b"{}\n",
                        self.uid,
                        MODULE.FileSnapshot(False),
                    )
            self.assertEqual(
                events,
                ["temporary-sync", "replace", "directory-sync"],
            )

            with mock.patch.object(
                MODULE,
                "_durable_sync",
                side_effect=MODULE.StatePublicationError(
                    "injected temporary fullsync failure"
                ),
            ):
                with mock.patch.object(MODULE.os, "replace") as replace_state:
                    with self.assertRaisesRegex(
                        MODULE.StatePublicationError,
                        "temporary fullsync failure",
                    ):
                        MODULE._atomic_publish(
                            directory_fd,
                            "never-renamed.json",
                            b"{}\n",
                            self.uid,
                            MODULE.FileSnapshot(False),
                        )
                    replace_state.assert_not_called()

    def test_atomic_publication_rejects_same_payload_inode_swap_in_rename_window(self):
        payload = b'{"status":"in-flight"}\n'
        with MODULE._open_state_directory(self.home, self.uid, create=True) as (
            state_directory,
            directory_fd,
        ):
            real_replace = MODULE.os.replace
            replaced = False

            def replace_then_swap(*args, **kwargs):
                nonlocal replaced
                result = real_replace(*args, **kwargs)
                if args[1] == "identity-bound.json" and not replaced:
                    replaced = True
                    attacker = state_directory / ".identity-bound.attacker"
                    attacker.write_bytes(payload)
                    os.chmod(attacker, 0o600)
                    real_replace(attacker, state_directory / "identity-bound.json")
                return result

            with mock.patch.object(
                MODULE.os,
                "replace",
                side_effect=replace_then_swap,
            ):
                with self.assertRaisesRegex(
                    MODULE.StatePublicationError,
                    "failed exact revalidation",
                ):
                    MODULE._atomic_publish(
                        directory_fd,
                        "identity-bound.json",
                        payload,
                        self.uid,
                        MODULE.FileSnapshot(False),
                    )

        self.assertTrue(replaced)

    def test_atomic_publication_rejects_replaced_temporary_pathname(self):
        payload = b'{"status":"in-flight"}\n'
        with MODULE._open_state_directory(self.home, self.uid, create=True) as (
            state_directory,
            directory_fd,
        ):
            real_stat = MODULE.os.stat
            replaced = False

            def swap_at_named_temporary_check(path, *args, **kwargs):
                nonlocal replaced
                if (
                    isinstance(path, str)
                    and path.startswith(".pathname-bound.")
                    and path.endswith(".tmp")
                    and kwargs.get("dir_fd") == directory_fd
                    and not replaced
                ):
                    attacker = state_directory / ".pathname-bound.attacker"
                    attacker.write_bytes(payload)
                    os.chmod(attacker, 0o600)
                    os.replace(attacker, state_directory / path)
                    replaced = True
                return real_stat(path, *args, **kwargs)

            with mock.patch.object(
                MODULE.os,
                "stat",
                side_effect=swap_at_named_temporary_check,
            ):
                with self.assertRaisesRegex(
                    MODULE.StatePublicationError,
                    "pathname no longer names",
                ):
                    MODULE._atomic_publish(
                        directory_fd,
                        "pathname-bound.json",
                        payload,
                        self.uid,
                        MODULE.FileSnapshot(False),
                    )

        self.assertTrue(replaced)

    def test_atomic_publication_ignores_temporary_close_error_after_commit(self):
        payload = b'{"status":"committed"}\n'
        with MODULE._open_state_directory(self.home, self.uid, create=True) as (
            _state_directory,
            directory_fd,
        ):
            real_sync = MODULE._durable_sync
            real_close = MODULE.os.close
            temporary_fd = None
            injected = False

            def capture_temporary_fd(fd):
                nonlocal temporary_fd
                if not stat.S_ISDIR(os.fstat(fd).st_mode):
                    temporary_fd = fd
                real_sync(fd)

            def fail_temporary_close(fd):
                nonlocal injected
                if fd == temporary_fd and not injected:
                    injected = True
                    raise OSError("injected temporary close failure")
                return real_close(fd)

            try:
                with mock.patch.object(
                    MODULE,
                    "_durable_sync",
                    side_effect=capture_temporary_fd,
                ), mock.patch.object(
                    MODULE.os,
                    "close",
                    side_effect=fail_temporary_close,
                ):
                    published = MODULE._atomic_publish(
                        directory_fd,
                        "close-bound.json",
                        payload,
                        self.uid,
                        MODULE.FileSnapshot(False),
                    )
            finally:
                if temporary_fd is not None:
                    try:
                        real_close(temporary_fd)
                    except OSError:
                        pass

        self.assertTrue(injected)
        self.assertTrue(published.exists)
        self.assertEqual(published.payload, payload)

    def test_fifo_target_audit_fails_without_blocking_or_spawning(self):
        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        state_directory.mkdir(mode=0o700)
        fifo_name = "target-orphan-fifo.json"
        fifo_path = state_directory / fifo_name
        os.mkfifo(fifo_path, 0o600)
        real_open = MODULE.os.open
        observed_nonblocking_open = False

        def require_nonblocking_open(path, flags, *args, **kwargs):
            nonlocal observed_nonblocking_open
            if path == fifo_name:
                self.assertTrue(flags & MODULE.os.O_NONBLOCK)
                observed_nonblocking_open = True
            return real_open(path, flags, *args, **kwargs)

        runtime = self.runtime()
        with mock.patch.object(
            MODULE.os,
            "open",
            side_effect=require_nonblocking_open,
        ):
            with self.assertRaisesRegex(
                MODULE.ControllerError,
                "access policy is unsafe",
            ):
                self.activate_controller(runtime)

        self.assertTrue(observed_nonblocking_open)
        self.assertFalse(
            any(
                len(call) > 1 and call[1] == "install-scheduler"
                for call in runtime.calls
            )
        )

    def test_existing_state_directory_retries_child_then_parent_sync(self):
        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        state_directory.mkdir(mode=0o700)
        state_identity = (state_directory.stat().st_dev, state_directory.stat().st_ino)
        home_identity = (self.home.stat().st_dev, self.home.stat().st_ino)
        events = []
        failed_parent_once = False

        def sync_with_one_parent_failure(fd):
            nonlocal failed_parent_once
            metadata = os.fstat(fd)
            identity = (metadata.st_dev, metadata.st_ino)
            if identity == state_identity:
                events.append("child")
            elif identity == home_identity:
                events.append("parent")
                if not failed_parent_once:
                    failed_parent_once = True
                    raise MODULE.StatePublicationError(
                        "injected parent directory fullsync failure"
                    )
            else:
                self.fail("unexpected durable-sync descriptor")

        with mock.patch.object(
            MODULE,
            "_durable_sync",
            side_effect=sync_with_one_parent_failure,
        ):
            with self.assertRaisesRegex(
                MODULE.StatePublicationError,
                "parent directory fullsync failure",
            ):
                with MODULE._open_state_directory_descriptor(
                    self.home,
                    self.uid,
                    create=True,
                ):
                    self.fail("state directory was exposed before parent sync")
            with MODULE._open_state_directory_descriptor(
                self.home,
                self.uid,
                create=True,
            ) as (_path, _fd, _policy, _revalidate_home):
                pass

        self.assertEqual(events, ["child", "parent", "child", "parent"])

    def test_activation_state_enumeration_enforces_all_bounds(self):
        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        state_directory.mkdir(mode=0o700)
        (state_directory / "a").write_bytes(b"")
        (state_directory / "long-name").write_bytes(b"")

        with MODULE._open_state_directory_descriptor(
            self.home,
            self.uid,
            create=False,
        ) as (_path, directory_fd, _policy, _revalidate_home):
            with mock.patch.object(MODULE, "MAX_STATE_DIRECTORY_ENTRIES", 1):
                with self.assertRaisesRegex(
                    MODULE.StatePublicationError,
                    "entry count",
                ):
                    MODULE._bounded_state_directory_names(directory_fd)
            with mock.patch.object(MODULE, "MAX_STATE_DIRECTORY_NAME_BYTES", 1):
                with self.assertRaisesRegex(
                    MODULE.StatePublicationError,
                    "entry name exceeds",
                ):
                    MODULE._bounded_state_directory_names(directory_fd)
            total_name_bytes = len(os.fsencode("a")) + len(os.fsencode("long-name"))
            with mock.patch.object(
                MODULE,
                "MAX_STATE_DIRECTORY_NAME_BYTES",
                len(os.fsencode("long-name")),
            ), mock.patch.object(
                MODULE,
                "MAX_STATE_DIRECTORY_NAMES_BYTES",
                total_name_bytes - 1,
            ):
                with self.assertRaisesRegex(
                    MODULE.StatePublicationError,
                    "entry names exceed",
                ):
                    MODULE._bounded_state_directory_names(directory_fd)
            with mock.patch.object(
                MODULE,
                "MAX_STATE_DIRECTORY_NAME_BYTES",
                len(os.fsencode("long-name")),
            ), mock.patch.object(
                MODULE,
                "MAX_STATE_DIRECTORY_NAMES_BYTES",
                total_name_bytes,
            ):
                self.assertEqual(
                    MODULE._bounded_state_directory_names(directory_fd),
                    ("a", "long-name"),
                )

        self.write_target_fence(
            "bounded-one",
            last_error="process-retryable",
        )
        self.write_target_fence(
            "bounded-two",
            last_error="process-retryable",
        )
        with MODULE._open_state_directory_descriptor(
            self.home,
            self.uid,
            create=False,
        ) as (_path, directory_fd, _policy, _revalidate_home):
            with mock.patch.object(MODULE, "MAX_TARGET_STATE_FILES", 1):
                with self.assertRaisesRegex(
                    MODULE.StatePublicationError,
                    "target state file count",
                ):
                    MODULE._audit_activation_state_directory(
                        directory_fd,
                        self.uid,
                        lambda: None,
                    )
            target_payload_bytes = sum(
                len(
                    (
                        state_directory
                        / f"target-{target_id}.json"
                    ).read_bytes()
                )
                for target_id in ("bounded-one", "bounded-two")
            )
            largest_target_payload = max(
                len(
                    (
                        state_directory
                        / f"target-{target_id}.json"
                    ).read_bytes()
                )
                for target_id in ("bounded-one", "bounded-two")
            )
            self.assertLess(largest_target_payload, target_payload_bytes)
            with mock.patch.object(
                MODULE,
                "MAX_TARGET_STATE_AUDIT_BYTES",
                target_payload_bytes - 1,
            ):
                with self.assertRaisesRegex(
                    MODULE.StatePublicationError,
                    "target state payloads",
                ):
                    MODULE._audit_activation_state_directory(
                        directory_fd,
                        self.uid,
                        lambda: None,
                    )
            with mock.patch.object(
                MODULE,
                "MAX_TARGET_STATE_AUDIT_BYTES",
                target_payload_bytes,
            ):
                MODULE._audit_activation_state_directory(
                    directory_fd,
                    self.uid,
                    lambda: None,
                )

    def test_activation_revalidates_visible_fence_before_installer(self):
        runtime = self.runtime()
        original = MODULE._atomic_publish
        detached_paths = None

        def detach_after_pending(directory_fd, name, payload, uid, expected):
            nonlocal detached_paths
            published = original(directory_fd, name, payload, uid, expected)
            if (
                name == MODULE.ACTIVATION_PENDING_FILE_NAME
                and json.loads(payload.decode("utf-8"))["status"] == "in-flight"
                and detached_paths is None
            ):
                detached_paths = self.detach_state_directory()
            return published

        try:
            with mock.patch.object(
                MODULE,
                "_atomic_publish",
                side_effect=detach_after_pending,
            ):
                with self.assertRaises(MODULE.ProcessCleanupInconclusiveError):
                    self.activate_controller(runtime)
            self.assertIsNotNone(detached_paths)
            self.assertFalse(
                any(len(call) > 1 and call[1] == "install-scheduler" for call in runtime.calls)
            )
        finally:
            if detached_paths is not None:
                self.restore_state_directory(*detached_paths)
        pending = json.loads(
            (
                self.home
                / MODULE.STATE_DIRECTORY_NAME
                / MODULE.ACTIVATION_PENDING_FILE_NAME
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(pending["status"], "in-flight")

    def test_target_revalidates_visible_fence_before_ssh(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        original = MODULE._atomic_publish
        detached_paths = None

        def detach_after_target_fence(directory_fd, name, payload, uid, expected):
            nonlocal detached_paths
            published = original(directory_fd, name, payload, uid, expected)
            parsed = json.loads(payload.decode("utf-8"))
            if (
                name == f"target-{self.target_id}.json"
                and parsed.get("last_error") == "process-in-flight"
                and detached_paths is None
            ):
                detached_paths = self.detach_state_directory()
            return published

        try:
            with mock.patch.object(
                MODULE,
                "_atomic_publish",
                side_effect=detach_after_target_fence,
            ):
                with self.assertRaises(MODULE.ProcessCleanupInconclusiveError):
                    MODULE.controller_run(runtime, self.home, strict=False)
            self.assertIsNotNone(detached_paths)
            self.assertEqual(len(self.ssh_calls(runtime)), 0)
        finally:
            if detached_paths is not None:
                self.restore_state_directory(*detached_paths)
        self.assertEqual(
            json.loads(self.state_path().read_text(encoding="utf-8"))["last_error"],
            "process-in-flight",
        )

    def test_operation_revalidates_visible_fence_before_local_sync(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        original = MODULE._atomic_publish
        detached_paths = None

        def detach_after_operation_fence(directory_fd, name, payload, uid, expected):
            nonlocal detached_paths
            published = original(directory_fd, name, payload, uid, expected)
            parsed = json.loads(payload.decode("utf-8"))
            if (
                name == MODULE.HOST_MUTATION_STATE_NAME
                and parsed.get("status") == "in-flight"
                and detached_paths is None
            ):
                detached_paths = self.detach_state_directory()
            return published

        try:
            with mock.patch.object(
                MODULE,
                "_atomic_publish",
                side_effect=detach_after_operation_fence,
            ):
                with self.assertRaises(MODULE.ProcessCleanupInconclusiveError):
                    MODULE.controller_run(runtime, self.home, strict=False)
            self.assertIsNotNone(detached_paths)
            self.assertFalse(
                any(len(call) > 1 and call[1] == "run-scheduled" for call in runtime.calls)
            )
            self.assertEqual(len(self.ssh_calls(runtime)), 0)
        finally:
            if detached_paths is not None:
                self.restore_state_directory(*detached_paths)
        self.assertEqual(
            self.operation_state("controller-run")["status"],
            "in-flight",
        )

    def test_activation_pending_same_payload_new_inode_blocks_installer(self):
        runtime = self.runtime()
        replaced = False

        def replace_immediately_before_spawn(call):
            nonlocal replaced
            if (
                len(call) > 1
                and call[1] == "install-scheduler"
                and not replaced
            ):
                replaced = True
                name = MODULE.ACTIVATION_PENDING_FILE_NAME
                payload = (
                    self.home / MODULE.STATE_DIRECTORY_NAME / name
                ).read_bytes()
                self.replace_state_file_with_same_payload(name, payload)

        runtime.before_spawn_hook = replace_immediately_before_spawn
        with self.assertRaisesRegex(
            MODULE.StatePublicationError,
            "object was replaced",
        ):
            self.activate_controller(runtime)

        self.assertTrue(replaced)
        self.assertFalse(
            any(
                len(call) > 1 and call[1] == "install-scheduler"
                for call in runtime.calls
            )
        )

    def test_target_same_payload_new_inode_blocks_ssh(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        replaced = False

        def replace_immediately_before_spawn(call):
            nonlocal replaced
            if (
                call
                and call[0] == "/usr/bin/ssh"
                and not replaced
            ):
                replaced = True
                name = f"target-{self.target_id}.json"
                payload = (
                    self.home / MODULE.STATE_DIRECTORY_NAME / name
                ).read_bytes()
                self.replace_state_file_with_same_payload(name, payload)

        runtime.before_spawn_hook = replace_immediately_before_spawn
        with self.assertRaises(MODULE.StatePublicationError):
            MODULE.controller_run(runtime, self.home, strict=False)

        self.assertTrue(replaced)
        self.assertEqual(len(self.ssh_calls(runtime)), 0)

    def test_host_mutation_same_payload_new_inode_blocks_local_sync(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        replaced = False

        def replace_immediately_before_spawn(call):
            nonlocal replaced
            if (
                len(call) > 1
                and call[1] == "run-scheduled"
                and not replaced
            ):
                replaced = True
                name = MODULE.HOST_MUTATION_STATE_NAME
                payload = (
                    self.home / MODULE.STATE_DIRECTORY_NAME / name
                ).read_bytes()
                self.replace_state_file_with_same_payload(name, payload)

        runtime.before_spawn_hook = replace_immediately_before_spawn
        with self.assertRaises(MODULE.ProcessCleanupInconclusiveError):
            MODULE.controller_run(runtime, self.home, strict=False)

        self.assertTrue(replaced)
        self.assertFalse(
            any(
                len(call) > 1 and call[1] == "run-scheduled"
                for call in runtime.calls
            )
        )
        self.assertEqual(len(self.ssh_calls(runtime)), 0)

    def test_activation_pending_unlink_immediately_before_spawn_blocks_installer(self):
        runtime = self.runtime()
        unlinked = False

        def unlink_immediately_before_spawn(call):
            nonlocal unlinked
            if len(call) > 1 and call[1] == "install-scheduler" and not unlinked:
                unlinked = True
                (
                    self.home
                    / MODULE.STATE_DIRECTORY_NAME
                    / MODULE.ACTIVATION_PENDING_FILE_NAME
                ).unlink()

        runtime.before_spawn_hook = unlink_immediately_before_spawn
        with self.assertRaisesRegex(
            MODULE.StatePublicationError,
            "named fence is missing",
        ):
            self.activate_controller(runtime)

        self.assertTrue(unlinked)
        self.assertFalse(
            any(
                len(call) > 1 and call[1] == "install-scheduler"
                for call in runtime.calls
            )
        )

    def test_target_mode_drift_immediately_before_spawn_blocks_ssh(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        changed = False

        def chmod_immediately_before_spawn(call):
            nonlocal changed
            if call and call[0] == "/usr/bin/ssh" and not changed:
                changed = True
                os.chmod(self.state_path(), 0o644)

        runtime.before_spawn_hook = chmod_immediately_before_spawn
        with self.assertRaises(MODULE.StatePublicationError):
            MODULE.controller_run(runtime, self.home, strict=False)

        self.assertTrue(changed)
        self.assertEqual(len(self.ssh_calls(runtime)), 0)

    def test_host_payload_drift_immediately_before_spawn_blocks_local_sync(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        changed = False

        def rewrite_immediately_before_spawn(call):
            nonlocal changed
            if len(call) > 1 and call[1] == "run-scheduled" and not changed:
                changed = True
                self.operation_state_path("controller-run").write_bytes(b"{}\n")

        runtime.before_spawn_hook = rewrite_immediately_before_spawn
        with self.assertRaises(MODULE.ProcessCleanupInconclusiveError):
            MODULE.controller_run(runtime, self.home, strict=False)

        self.assertTrue(changed)
        self.assertFalse(
            any(
                len(call) > 1 and call[1] == "run-scheduled"
                for call in runtime.calls
            )
        )

    def test_home_policy_drift_before_installer_blocks_spawn(self):
        runtime = self.runtime()
        original_mode = stat.S_IMODE(self.home.stat().st_mode)
        changed = False

        def chmod_home_before_installer(call):
            nonlocal changed
            if len(call) > 1 and call[1] == "install-scheduler" and not changed:
                changed = True
                os.chmod(self.home, original_mode | 0o020)

        runtime.before_spawn_hook = chmod_home_before_installer
        try:
            with self.assertRaises(MODULE.ProcessCleanupInconclusiveError):
                self.activate_controller(runtime)
        finally:
            os.chmod(self.home, original_mode)

        self.assertTrue(changed)
        self.assertFalse(
            any(
                len(call) > 1 and call[1] == "install-scheduler"
                for call in runtime.calls
            )
        )
        pending = json.loads(
            (
                self.home
                / MODULE.STATE_DIRECTORY_NAME
                / MODULE.ACTIVATION_PENDING_FILE_NAME
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(pending["status"], "in-flight")

    def test_home_policy_drift_before_local_sync_blocks_spawn(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        original_mode = stat.S_IMODE(self.home.stat().st_mode)
        changed = False

        def chmod_home_before_local_sync(call):
            nonlocal changed
            if len(call) > 1 and call[1] == "run-scheduled" and not changed:
                changed = True
                os.chmod(self.home, original_mode | 0o020)

        runtime.before_spawn_hook = chmod_home_before_local_sync
        try:
            with self.assertRaises(MODULE.ProcessCleanupInconclusiveError):
                MODULE.controller_run(runtime, self.home, strict=False)
        finally:
            os.chmod(self.home, original_mode)

        self.assertTrue(changed)
        self.assertFalse(
            any(
                len(call) > 1 and call[1] == "run-scheduled"
                for call in runtime.calls
            )
        )
        self.assertEqual(
            self.operation_state("controller-run")["status"],
            "in-flight",
        )

    def test_home_policy_drift_before_ssh_blocks_spawn(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        original_mode = stat.S_IMODE(self.home.stat().st_mode)
        changed = False

        def chmod_home_before_ssh(call):
            nonlocal changed
            if call and call[0] == "/usr/bin/ssh" and not changed:
                changed = True
                os.chmod(self.home, original_mode | 0o020)

        runtime.before_spawn_hook = chmod_home_before_ssh
        try:
            with self.assertRaises(MODULE.ProcessCleanupInconclusiveError):
                MODULE.controller_run(runtime, self.home, strict=False)
        finally:
            os.chmod(self.home, original_mode)

        self.assertTrue(changed)
        self.assertEqual(self.ssh_calls(runtime), [])
        self.assertEqual(
            json.loads(self.state_path().read_text(encoding="utf-8"))[
                "last_error"
            ],
            "process-in-flight",
        )

    def test_home_policy_drift_before_remote_sync_blocks_spawn(self):
        runtime = self.runtime(candidates=(self.target_id,), gui=False)
        MODULE.activate(
            runtime,
            self.home,
            requested_host_id=self.target_id,
            interval_minutes=30,
        )
        runtime.calls.clear()
        original_mode = stat.S_IMODE(self.home.stat().st_mode)
        changed = False

        def chmod_home_before_remote_sync(call):
            nonlocal changed
            if len(call) > 1 and call[1] == "run-scheduled" and not changed:
                changed = True
                os.chmod(self.home, original_mode | 0o020)

        runtime.before_spawn_hook = chmod_home_before_remote_sync
        try:
            with self.assertRaises(MODULE.RemoteProcessCleanupInconclusiveError):
                MODULE.remote_apply(
                    runtime,
                    self.home,
                    host_id=self.target_id,
                    controller_id=self.controller_id,
                    expected=self.desired(),
                )
        finally:
            os.chmod(self.home, original_mode)

        self.assertTrue(changed)
        self.assertFalse(
            any(
                len(call) > 1 and call[1] == "run-scheduled"
                for call in runtime.calls
            )
        )
        self.assertEqual(
            self.operation_state("remote-apply")["status"],
            "in-flight",
        )

    def test_home_child_entry_churn_before_local_sync_is_accepted(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        runtime.ssh_results = [self.json_result(self.success_receipt())]
        churned = False

        def create_benign_child_before_local_sync(call):
            nonlocal churned
            if len(call) > 1 and call[1] == "run-scheduled" and not churned:
                churned = True
                (self.home / "benign-child-churn").write_text(
                    "safe metadata churn\n",
                    encoding="utf-8",
                )

        runtime.before_spawn_hook = create_benign_child_before_local_sync

        self.assertTrue(MODULE.controller_run(runtime, self.home, strict=False))
        self.assertTrue(churned)
        self.assertEqual(len(self.ssh_calls(runtime)), 1)

    def test_home_revalidation_rejects_expected_uid_drift(self):
        with MODULE._open_home_directory_descriptor(
            self.home,
            self.uid,
        ) as (home_fd, revalidate_home):
            current = os.fstat(home_fd)
            wrong_owner = AccessPolicyMetadata(
                mode=current.st_mode,
                uid=self.uid + 1,
                ctime_ns=current.st_ctime_ns,
                nlink=current.st_nlink,
                dev=current.st_dev,
                ino=current.st_ino,
                gid=current.st_gid,
            )
            with mock.patch.object(
                MODULE.os,
                "fstat",
                return_value=wrong_owner,
            ):
                with self.assertRaisesRegex(
                    MODULE.StatePublicationError,
                    "identity or access policy changed",
                ):
                    revalidate_home()

    def test_home_revalidation_rejects_nonowner_allow_acl(self):
        api = DeterministicAclApi(unsafe_passes=(3,))
        with mock.patch.object(MODULE.sys, "platform", "darwin"):
            with mock.patch.object(MODULE, "_darwin_acl_api", return_value=api):
                with MODULE._open_home_directory_descriptor(
                    self.home,
                    self.uid,
                ) as (_home_fd, revalidate_home):
                    with self.assertRaisesRegex(
                        MODULE.StatePublicationError,
                        "non-owner ALLOW",
                    ):
                        revalidate_home()
        self.assertEqual(api.pass_count, 3)

    @unittest.skipUnless(sys.platform == "darwin", "requires Darwin extended ACLs")
    def test_host_acl_drift_immediately_before_spawn_blocks_local_sync(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        state_path = self.operation_state_path("controller-run")
        changed = False

        def add_acl_immediately_before_spawn(call):
            nonlocal changed
            if len(call) > 1 and call[1] == "run-scheduled" and not changed:
                changed = True
                self.add_extended_acl(state_path, "everyone allow read")

        runtime.before_spawn_hook = add_acl_immediately_before_spawn
        try:
            with self.assertRaises(MODULE.ProcessCleanupInconclusiveError):
                MODULE.controller_run(runtime, self.home, strict=False)
            self.assertTrue(changed)
            self.assertFalse(
                any(
                    len(call) > 1 and call[1] == "run-scheduled"
                    for call in runtime.calls
                )
            )
        finally:
            if changed:
                self.clear_extended_acl(state_path)

    def test_named_state_fence_revalidation_has_exact_sandwich_order(self):
        events = []
        counts = {"one": 0, "two": 0}

        def revalidate(label):
            counts[label] += 1
            phase = "pre" if counts[label] == 1 else "post"
            events.append(f"{phase}({label})")

        snapshots = {
            "one.json": MODULE.FileSnapshot(True, b"one", (1, 11)),
            "two.json": MODULE.FileSnapshot(True, b"two", (1, 12)),
        }
        fences = tuple(
            MODULE.NamedStateFence(
                directory_fd=17,
                name=name,
                uid=self.uid,
                snapshot=snapshot,
                payload=snapshot.payload,
                revalidate=lambda label=label: revalidate(label),
                label=label,
            )
            for label, name, snapshot in (
                ("one", "one.json", snapshots["one.json"]),
                ("two", "two.json", snapshots["two.json"]),
            )
        )

        def read_named(_directory_fd, name, _uid, *, missing_ok):
            self.assertTrue(missing_ok)
            events.append(f"read({name.removesuffix('.json')})")
            return snapshots[name]

        with mock.patch.object(
            MODULE,
            "_read_bound_state_file",
            side_effect=read_named,
        ):
            MODULE._revalidate_named_state_fences(fences)

        self.assertEqual(
            events,
            [
                "pre(one)",
                "pre(two)",
                "read(one)",
                "read(two)",
                "post(two)",
                "post(one)",
            ],
        )

    def test_operation_lock_revalidates_state_directory_after_acquire(self):
        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        state_directory.mkdir(mode=0o700)
        original_flock = MODULE.fcntl.flock
        detached_paths = None
        entered = False

        def detach_after_acquire(fd, operation):
            nonlocal detached_paths
            result = original_flock(fd, operation)
            if (
                operation == MODULE.fcntl.LOCK_EX | MODULE.fcntl.LOCK_NB
                and detached_paths is None
            ):
                detached_paths = self.detach_state_directory()
            return result

        try:
            with mock.patch.object(
                MODULE.fcntl,
                "flock",
                side_effect=detach_after_acquire,
            ):
                with self.assertRaises(MODULE.StatePublicationError):
                    with MODULE._host_mutation_lock(
                        self.home,
                        self.uid,
                        self.runtime(),
                    ):
                        entered = True
            self.assertFalse(entered)
            self.assertIsNotNone(detached_paths)
        finally:
            if detached_paths is not None:
                self.restore_state_directory(*detached_paths)

    def test_activation_lock_rejects_named_replacement_after_wait(self):
        runtime = self.runtime()
        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        state_directory.mkdir(mode=0o700)
        lock = state_directory / "activation.lock"
        lock.write_bytes(b"")
        os.chmod(lock, 0o600)
        fence = state_directory / MODULE.ACTIVATION_PENDING_FILE_NAME
        fence_payload = MODULE._canonical_json_bytes(
            {
                "version": 1,
                "status": "in-flight",
                "receipt_sha256": "a" * 64,
            }
        )
        fence.write_bytes(fence_payload)
        os.chmod(fence, 0o600)
        interleave, attempts = self.replace_lock_after_one_wait(lock, runtime)

        with mock.patch.object(MODULE.fcntl, "flock", side_effect=interleave):
            with self.assertRaisesRegex(
                MODULE.StatePublicationError,
                "activation lock",
            ):
                with MODULE._activation_lock(self.home, self.uid, runtime):
                    self.fail("replaced activation lock was acquired")

        self.assertEqual(attempts(), 2)
        self.assertEqual(fence.read_bytes(), fence_payload)
        self.assertEqual(runtime.calls, [])

    def test_target_lock_rejects_named_replacement_after_wait(self):
        runtime = self.runtime()
        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        state_directory.mkdir(mode=0o700)
        lock = state_directory / f"target-{self.target_id}.lock"
        lock.write_bytes(b"")
        os.chmod(lock, 0o600)
        fence = state_directory / f"target-{self.target_id}.json"
        state_value = MODULE._empty_target_state(
            self.controller_id,
            self.target_id,
            "0" * 64,
        )
        state_value["desired"] = self.desired()
        state_value["pending"] = True
        state_value["generation"] = 1
        state_value["last_error"] = "process-in-flight"
        fence_payload = MODULE._canonical_json_bytes(state_value)
        fence.write_bytes(fence_payload)
        os.chmod(fence, 0o600)
        interleave, attempts = self.replace_lock_after_one_wait(lock, runtime)
        entered = False

        with mock.patch.object(MODULE.fcntl, "flock", side_effect=interleave):
            with self.assertRaisesRegex(MODULE.StatePublicationError, "target lock"):
                with MODULE._target_lock(
                    self.home,
                    self.uid,
                    self.target_id,
                    runtime,
                ):
                    entered = True

        self.assertFalse(entered)
        self.assertEqual(attempts(), 2)
        self.assertEqual(fence.read_bytes(), fence_payload)
        self.assertEqual(runtime.calls, [])
        self.assertEqual(len(self.ssh_calls(runtime)), 0)

    def test_operation_lock_rejects_named_replacement_after_wait(self):
        runtime = self.runtime()
        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        state_directory.mkdir(mode=0o700)
        lock = state_directory / MODULE.HOST_MUTATION_LOCK_NAME
        lock.write_bytes(b"")
        os.chmod(lock, 0o600)
        fence = state_directory / MODULE.HOST_MUTATION_STATE_NAME
        fence_payload = MODULE._canonical_json_bytes(
            {
                "version": 1,
                "operation": "controller-run",
                "host_id": self.controller_id,
                "controller_id": self.controller_id,
                "scope_sha256": "0" * 64,
                "status": "in-flight",
                "generation": 1,
            }
        )
        fence.write_bytes(fence_payload)
        os.chmod(fence, 0o600)
        interleave, attempts = self.replace_lock_after_one_wait(lock, runtime)
        entered = False

        with mock.patch.object(MODULE.fcntl, "flock", side_effect=interleave):
            with self.assertRaisesRegex(MODULE.StatePublicationError, "host mutation lock"):
                with MODULE._host_mutation_lock(
                    self.home,
                    self.uid,
                    runtime,
                ):
                    entered = True

        self.assertFalse(entered)
        self.assertEqual(attempts(), 2)
        self.assertEqual(fence.read_bytes(), fence_payload)
        self.assertEqual(runtime.calls, [])

    def test_managed_signal_survives_lock_finalizer_revalidation_failure(self):
        runtime = self.runtime()
        state_directory = self.home / MODULE.STATE_DIRECTORY_NAME
        state_directory.mkdir(mode=0o700)
        lock = state_directory / f"target-{self.target_id}.lock"
        lock.write_bytes(b"")
        os.chmod(lock, 0o600)
        fence = state_directory / f"target-{self.target_id}.json"
        fence_payload = b'{"fence":"preserved"}\n'
        fence.write_bytes(fence_payload)
        os.chmod(fence, 0o600)

        with self.assertRaises(MODULE._ManagedProcessSignal) as raised:
            with MODULE._target_lock(
                self.home,
                self.uid,
                self.target_id,
                runtime,
            ):
                replacement = lock.with_name(f"{lock.name}.signal-replacement")
                replacement.write_bytes(b"")
                os.chmod(replacement, 0o600)
                os.replace(replacement, lock)
                raise MODULE._ManagedProcessSignal(MODULE.signal.SIGTERM)

        self.assertEqual(raised.exception.signum, MODULE.signal.SIGTERM)
        self.assertIsInstance(raised.exception.__cause__, MODULE.StatePublicationError)
        self.assertEqual(fence.read_bytes(), fence_payload)
        self.assertEqual(runtime.calls, [])

    def test_operation_drift_after_local_sync_keeps_fence_and_pce(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        detached_paths = None

        def detach_after_local_sync():
            nonlocal detached_paths
            detached_paths = self.detach_state_directory()

        runtime.run_scheduled_hook = detach_after_local_sync
        try:
            with self.assertRaises(MODULE.ProcessCleanupInconclusiveError):
                MODULE.controller_run(runtime, self.home, strict=False)
            self.assertIsNotNone(detached_paths)
            self.assertEqual(len(self.ssh_calls(runtime)), 0)
        finally:
            if detached_paths is not None:
                self.restore_state_directory(*detached_paths)
        self.assertEqual(
            self.operation_state("controller-run")["status"],
            "in-flight",
        )

    def test_target_drift_after_ssh_keeps_in_flight_state(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        detached_paths = None

        def detach_after_ssh(_call):
            nonlocal detached_paths
            detached_paths = self.detach_state_directory()
            return self.json_result(self.success_receipt())

        runtime.ssh_factory = detach_after_ssh
        try:
            with self.assertRaises(MODULE.ProcessCleanupInconclusiveError):
                MODULE.controller_run(runtime, self.home, strict=False)
            self.assertIsNotNone(detached_paths)
            self.assertEqual(len(self.ssh_calls(runtime)), 1)
        finally:
            if detached_paths is not None:
                self.restore_state_directory(*detached_paths)
        self.assertEqual(
            json.loads(self.state_path().read_text(encoding="utf-8"))["last_error"],
            "process-in-flight",
        )

    def test_unsafe_existing_lock_fails_closed_before_ssh(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        lock = (
            self.home
            / MODULE.STATE_DIRECTORY_NAME
            / f"target-{self.target_id}.lock"
        )
        lock.write_text("", encoding="utf-8")
        os.chmod(lock, 0o666)
        runtime.calls.clear()

        with self.assertRaisesRegex(MODULE.StatePublicationError, "access policy"):
            MODULE.controller_run(runtime, self.home, strict=False)

        self.assertEqual(len(self.ssh_calls(runtime)), 0)

    def test_remote_apply_revalidates_current_inventory_before_ack(self):
        runtime = self.runtime(candidates=(self.target_id,), gui=False)
        MODULE.activate(
            runtime,
            self.home,
            requested_host_id=self.target_id,
            interval_minutes=30,
        )
        runtime.calls.clear()
        self.write_release(NEXT_PRIVATE_SHA, self.inventory_data())
        self.write_release(FINAL_PRIVATE_SHA, self.inventory_data())

        def install_next():
            self.switch_current(NEXT_PRIVATE_SHA)
            runtime.identity_pair = self.desired(NEXT_PRIVATE_SHA, NEXT_PRIVATE_TREE)

        runtime.run_scheduled_hook = install_next

        def change_before_ack():
            self.switch_current(FINAL_PRIVATE_SHA)
            runtime.identity_pair = self.desired(FINAL_PRIVATE_SHA, FINAL_PRIVATE_TREE)

        runtime.verify_hook = change_before_ack
        with self.assertRaisesRegex(MODULE.ControllerError, "changed before acknowledgement"):
            MODULE.remote_apply(
                runtime,
                self.home,
                host_id=self.target_id,
                controller_id=self.controller_id,
                expected=self.desired(NEXT_PRIVATE_SHA, NEXT_PRIVATE_TREE),
            )
        self.assertEqual(
            self.operation_state("remote-apply")["status"],
            "retryable",
        )

    def test_remote_apply_returns_exact_verified_receipt(self):
        runtime = self.runtime(candidates=(self.target_id,), gui=False)
        MODULE.activate(
            runtime,
            self.home,
            requested_host_id=self.target_id,
            interval_minutes=30,
        )
        runtime.calls.clear()

        receipt = MODULE.remote_apply(
            runtime,
            self.home,
            host_id=self.target_id,
            controller_id=self.controller_id,
            expected=self.desired(),
        )

        self.assertEqual(receipt, self.success_receipt())
        canonical_calls = [
            call
            for call in runtime.calls
            if len(call) > 1 and call[0] == self.physical_runner()
        ]
        self.assertEqual(
            [call[1] for call in canonical_calls],
            [
                "run-scheduled",
                "release-identities",
                "status",
                "status",
                "verify-overlay",
                "release-identities",
            ],
        )
        self.assertIn("--strict", canonical_calls[2])
        self.assertEqual(
            canonical_calls[2][canonical_calls[2].index("--owner") + 1],
            "public",
        )
        self.assertIn("--strict", canonical_calls[3])
        self.assertEqual(
            canonical_calls[3][canonical_calls[3].index("--owner") + 1],
            "private",
        )
        self.assertEqual(canonical_calls[4][1], "verify-overlay")

    def test_remote_operation_fence_precedes_mutating_local_sync(self):
        runtime = self.runtime(candidates=(self.target_id,), gui=False)
        MODULE.activate(
            runtime,
            self.home,
            requested_host_id=self.target_id,
            interval_minutes=30,
        )
        runtime.calls.clear()
        observed = []

        def local_sync():
            observed.append(("local-sync", self.operation_state("remote-apply")))

        runtime.run_scheduled_hook = local_sync

        receipt = MODULE.remote_apply(
            runtime,
            self.home,
            host_id=self.target_id,
            controller_id=self.controller_id,
            expected=self.desired(),
        )

        self.assertEqual(receipt, self.success_receipt())
        self.assertEqual([name for name, _state in observed], ["local-sync"])
        self.assertTrue(
            all(state["status"] == "in-flight" for _name, state in observed)
        )
        self.assertEqual(
            self.operation_state("remote-apply")["status"],
            "retryable",
        )

    def test_remote_cleanup_fence_returns_exact_75_and_blocks_next_call(self):
        runtime = self.runtime(candidates=(self.target_id,), gui=False)
        MODULE.activate(
            runtime,
            self.home,
            requested_host_id=self.target_id,
            interval_minutes=30,
        )
        runtime.calls.clear()

        def cleanup_inconclusive():
            raise MODULE.ProcessCleanupInconclusiveError(
                "remote local sync cleanup was inconclusive"
            )

        runtime.run_scheduled_hook = cleanup_inconclusive

        class CaptureStdout:
            def __init__(self):
                self.buffer = io.BytesIO()

        captured = CaptureStdout()
        with mock.patch("sys.stdout", new=captured):
            self.assertEqual(
                MODULE.main(self.remote_argv(), runtime),
                MODULE.REMOTE_PROCESS_CLEANUP_EXIT,
            )

        expected_receipt = MODULE._remote_cleanup_receipt(
            self.controller_id,
            self.target_id,
            self.desired(),
        )
        self.assertEqual(
            json.loads(captured.buffer.getvalue().decode("utf-8")),
            expected_receipt,
        )
        self.assertEqual(
            self.operation_state("remote-apply")["status"],
            "process-cleanup-inconclusive",
        )
        payload, readable = MODULE._status_payload(runtime, self.home)
        self.assertTrue(readable)
        self.assertTrue(payload["degraded"])
        self.assertEqual(
            payload["host_mutation"]["status"],
            "process-cleanup-inconclusive",
        )
        calls_after_failure = len(runtime.calls)

        class BrokenBuffer:
            def write(self, _payload):
                raise BrokenPipeError("closed")

            def flush(self):
                raise BrokenPipeError("closed")

        class BrokenStdout:
            buffer = BrokenBuffer()

        with mock.patch("sys.stdout", new=BrokenStdout()):
            self.assertEqual(
                MODULE.main(self.remote_argv(), runtime),
                MODULE.REMOTE_PROCESS_CLEANUP_EXIT,
            )
        self.assertEqual(len(runtime.calls), calls_after_failure)

        class InterruptedBuffer:
            def write(self, _payload):
                raise KeyboardInterrupt("injected cleanup-report interrupt")

            def flush(self):
                raise AssertionError("flush must not use the interrupted stream")

        class InterruptedStdout:
            buffer = InterruptedBuffer()

        with mock.patch("sys.stdout", new=InterruptedStdout()):
            self.assertEqual(
                MODULE.main(self.remote_argv(), runtime),
                MODULE.REMOTE_PROCESS_CLEANUP_EXIT,
            )
        self.assertEqual(len(runtime.calls), calls_after_failure)

    def test_remote_legacy_artifact_returns_75_and_stops_controller_fanout(self):
        second_target_id = "z-headless"
        second_target = {
            "id": second_target_id,
            "role": "headless-managed",
            "username": "hoteng",
            "uid": self.uid,
            "home": str(self.account_home),
            "controller": self.controller_id,
            "ssh_alias": "z-headless-ssh",
        }
        inventory = self.inventory_data(extra_hosts=[second_target])
        self.write_release(NEXT_PRIVATE_SHA, inventory)
        self.switch_current(NEXT_PRIVATE_SHA)
        desired = self.desired(NEXT_PRIVATE_SHA, NEXT_PRIVATE_TREE)
        controller_runtime = self.runtime()
        controller_runtime.identity_pair = desired
        self.activate_controller(controller_runtime)
        controller_runtime.calls.clear()

        remote_runtime, remote_home = self.remote_target_runtime(
            private_sha=NEXT_PRIVATE_SHA,
            private_tree=NEXT_PRIVATE_TREE,
            inventory=inventory,
        )
        artifact = (
            remote_home
            / MODULE.STATE_DIRECTORY_NAME
            / "operation-remote-apply.json"
        )
        self.assertIn(artifact.name, MODULE.UNSUPPORTED_OPERATION_FILES)
        artifact.write_bytes(b"legacy remote process artifact\n")
        os.chmod(artifact, 0o600)

        returncode, stdout, stderr = self.invoke_remote_main(
            remote_runtime,
            remote_home,
            desired,
        )

        self.assertEqual(returncode, MODULE.REMOTE_PROCESS_CLEANUP_EXIT)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout.decode("utf-8")),
            MODULE._remote_cleanup_receipt(
                self.controller_id,
                self.target_id,
                desired,
            ),
        )
        self.assertFalse(
            any(
                len(call) > 1 and call[1] == "run-scheduled"
                for call in remote_runtime.calls
            )
        )

        attempts = []

        def remote_cleanup_result(call):
            attempts.append(call)
            if len(attempts) > 1:
                self.fail("controller continued fanout after remote cleanup exit")
            return MODULE.CommandResult(returncode, stdout, stderr.encode("utf-8"))

        controller_runtime.ssh_factory = remote_cleanup_result
        with self.assertRaises(MODULE.ProcessCleanupInconclusiveError):
            MODULE.controller_run(controller_runtime, self.home, strict=False)

        self.assertEqual(len(attempts), 1)
        self.assertIn("headless-ssh", attempts[0])
        target_state = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertEqual(
            target_state["last_error"],
            "process-cleanup-inconclusive",
        )
        self.assertEqual(
            self.operation_state("controller-run")["status"],
            "process-cleanup-inconclusive",
        )

    def _assert_remote_activation_process_fence_quarantines_fanout(self, status):
        second_target_id = "z-headless"
        inventory = self.inventory_data(
            extra_hosts=[
                {
                    "id": second_target_id,
                    "role": "headless-managed",
                    "username": "hoteng",
                    "uid": self.uid,
                    "home": str(self.account_home),
                    "controller": self.controller_id,
                    "ssh_alias": "z-headless-ssh",
                }
            ]
        )
        self.write_release(NEXT_PRIVATE_SHA, inventory)
        self.switch_current(NEXT_PRIVATE_SHA)
        desired = self.desired(NEXT_PRIVATE_SHA, NEXT_PRIVATE_TREE)
        controller_runtime = self.runtime()
        controller_runtime.identity_pair = desired
        self.activate_controller(controller_runtime)
        controller_runtime.calls.clear()

        remote_runtime, remote_home = self.remote_target_runtime(
            private_sha=NEXT_PRIVATE_SHA,
            private_tree=NEXT_PRIVATE_TREE,
            inventory=inventory,
        )
        self.write_activation_pending(remote_runtime, remote_home, status)

        returncode, stdout, stderr = self.invoke_remote_main(
            remote_runtime,
            remote_home,
            desired,
        )

        expected_receipt = MODULE._remote_cleanup_receipt(
            self.controller_id,
            self.target_id,
            desired,
        )
        self.assertEqual(returncode, MODULE.REMOTE_PROCESS_CLEANUP_EXIT)
        self.assertEqual(stdout, MODULE._canonical_json_bytes(expected_receipt))
        self.assertEqual(stderr, "")
        self.assertFalse(
            any(
                len(call) > 1 and call[1] == "run-scheduled"
                for call in remote_runtime.calls
            )
        )

        attempts = []

        def first_target_quarantines(call):
            attempts.append(call)
            if len(attempts) > 1:
                self.fail("controller continued fanout after activation process fence")
            return MODULE.CommandResult(returncode, stdout, b"")

        controller_runtime.ssh_factory = first_target_quarantines
        with self.assertRaises(MODULE.ProcessCleanupInconclusiveError):
            MODULE.controller_run(controller_runtime, self.home, strict=False)

        self.assertEqual(len(attempts), 1)
        self.assertIn("headless-ssh", attempts[0])
        self.assertEqual(
            json.loads(self.state_path().read_text(encoding="utf-8"))["last_error"],
            "process-cleanup-inconclusive",
        )
        self.assertEqual(
            self.operation_state("controller-run")["status"],
            "process-cleanup-inconclusive",
        )

    def test_remote_legacy_activation_pending_returns_75_and_stops_fanout(self):
        self._assert_remote_activation_process_fence_quarantines_fanout("pending")

    def test_remote_activation_in_flight_returns_75_and_stops_fanout(self):
        self._assert_remote_activation_process_fence_quarantines_fanout("in-flight")

    def test_remote_activation_cleanup_quarantine_returns_75_and_stops_fanout(self):
        self._assert_remote_activation_process_fence_quarantines_fanout(
            "process-cleanup-inconclusive"
        )

    def test_remote_retryable_activation_stays_exit1_and_fanout_continues(self):
        second_target_id = "z-headless"
        inventory = self.inventory_data(
            extra_hosts=[
                {
                    "id": second_target_id,
                    "role": "headless-managed",
                    "username": "hoteng",
                    "uid": self.uid,
                    "home": str(self.account_home),
                    "controller": self.controller_id,
                    "ssh_alias": "z-headless-ssh",
                }
            ]
        )
        self.write_release(NEXT_PRIVATE_SHA, inventory)
        self.switch_current(NEXT_PRIVATE_SHA)
        desired = self.desired(NEXT_PRIVATE_SHA, NEXT_PRIVATE_TREE)
        controller_runtime = self.runtime()
        controller_runtime.identity_pair = desired
        self.activate_controller(controller_runtime)
        controller_runtime.calls.clear()

        remote_runtime, remote_home = self.remote_target_runtime(
            private_sha=NEXT_PRIVATE_SHA,
            private_tree=NEXT_PRIVATE_TREE,
            inventory=inventory,
        )
        remote_state_path = (
            remote_home
            / MODULE.STATE_DIRECTORY_NAME
            / MODULE.HOST_MUTATION_STATE_NAME
        )
        self.assertEqual(
            json.loads(remote_state_path.read_text(encoding="utf-8"))["status"],
            "retryable",
        )
        self.write_activation_pending(
            remote_runtime,
            remote_home,
            "retryable",
        )

        returncode, stdout, stderr = self.invoke_remote_main(
            remote_runtime,
            remote_home,
            desired,
        )

        self.assertEqual(returncode, 1)
        self.assertEqual(stdout, b"")
        self.assertIn("role-activation-required", stderr)
        self.assertEqual(
            json.loads(remote_state_path.read_text(encoding="utf-8"))["status"],
            "retryable",
        )
        self.assertFalse(
            any(
                len(call) > 1 and call[1] == "run-scheduled"
                for call in remote_runtime.calls
            )
        )

        attempts = []

        def retryable_remote_failure(call):
            attempts.append(call)
            return MODULE.CommandResult(returncode, stdout, stderr.encode("utf-8"))

        controller_runtime.ssh_factory = retryable_remote_failure
        self.assertTrue(
            MODULE.controller_run(controller_runtime, self.home, strict=False)
        )

        self.assertEqual(len(attempts), 2)
        self.assertIn("headless-ssh", attempts[0])
        self.assertIn("z-headless-ssh", attempts[1])
        for target_id in (self.target_id, second_target_id):
            target_state = json.loads(
                (
                    self.home
                    / MODULE.STATE_DIRECTORY_NAME
                    / f"target-{target_id}.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(target_state["last_error"], "remote-failed")
        self.assertEqual(
            self.operation_state("controller-run")["status"],
            "retryable",
        )

    def test_remote_activation_fence_race_after_preflight_returns_75(self):
        runtime, remote_home = self.remote_target_runtime()
        original_validate = MODULE._validate_activation
        injected = False

        def block_inner_validation(*args, **kwargs):
            nonlocal injected
            if not injected:
                injected = True
                self.write_activation_pending(runtime, remote_home, "in-flight")
            return original_validate(*args, **kwargs)

        with mock.patch.object(
            MODULE,
            "_validate_activation",
            side_effect=block_inner_validation,
        ):
            returncode, stdout, stderr = self.invoke_remote_main(
                runtime,
                remote_home,
                self.desired(),
            )

        self.assertTrue(injected)
        self.assertEqual(returncode, MODULE.REMOTE_PROCESS_CLEANUP_EXIT)
        self.assertEqual(
            stdout,
            MODULE._canonical_json_bytes(
                MODULE._remote_cleanup_receipt(
                    self.controller_id,
                    self.target_id,
                    self.desired(),
                )
            ),
        )
        self.assertEqual(stderr, "")
        self.assertFalse(
            any(
                len(call) > 1 and call[1] == "run-scheduled"
                for call in runtime.calls
            )
        )

    def test_ordinary_remote_host_lock_failure_stays_exit1_and_remote_failed(self):
        controller_runtime = self.runtime()
        self.activate_controller(controller_runtime)
        controller_runtime.calls.clear()
        remote_runtime, remote_home = self.remote_target_runtime()
        lock_path = (
            remote_home
            / MODULE.STATE_DIRECTORY_NAME
            / MODULE.HOST_MUTATION_LOCK_NAME
        )
        os.chmod(lock_path, 0o666)

        returncode, stdout, stderr = self.invoke_remote_main(
            remote_runtime,
            remote_home,
            self.desired(),
        )

        self.assertEqual(returncode, 1)
        self.assertEqual(stdout, b"")
        self.assertIn("host mutation lock identity or access policy", stderr)
        controller_runtime.ssh_results = [
            MODULE.CommandResult(returncode, stdout, stderr.encode("utf-8"))
        ]

        self.assertTrue(
            MODULE.controller_run(controller_runtime, self.home, strict=False)
        )
        target_state = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertEqual(target_state["last_error"], "remote-failed")
        self.assertEqual(
            self.operation_state("controller-run")["status"],
            "retryable",
        )

    def test_remote_terminal_fence_publication_failure_retains_in_flight(self):
        runtime = self.runtime(candidates=(self.target_id,), gui=False)
        MODULE.activate(
            runtime,
            self.home,
            requested_host_id=self.target_id,
            interval_minutes=30,
        )
        runtime.calls.clear()
        original_transition = MODULE._transition_operation_fence

        def fail_retryable(*args, **kwargs):
            state_value = args[-2]
            status = args[-1]
            if (
                state_value["operation"] == "remote-apply"
                and status == "retryable"
            ):
                raise MODULE.StatePublicationError(
                    "injected remote terminal publication failure"
                )
            return original_transition(*args, **kwargs)

        with mock.patch.object(
            MODULE,
            "_transition_operation_fence",
            side_effect=fail_retryable,
        ):
            with self.assertRaises(MODULE.RemoteProcessCleanupInconclusiveError):
                MODULE.remote_apply(
                    runtime,
                    self.home,
                    host_id=self.target_id,
                    controller_id=self.controller_id,
                    expected=self.desired(),
                )

        self.assertEqual(
            self.operation_state("remote-apply")["status"],
            "in-flight",
        )
        calls_after_failure = len(runtime.calls)
        with self.assertRaises(MODULE.RemoteProcessCleanupInconclusiveError):
            MODULE.remote_apply(
                runtime,
                self.home,
                host_id=self.target_id,
                controller_id=self.controller_id,
                expected=self.desired(),
            )
        self.assertEqual(len(runtime.calls), calls_after_failure)

    def test_remote_quarantine_publication_failure_retains_in_flight(self):
        runtime = self.runtime(candidates=(self.target_id,), gui=False)
        MODULE.activate(
            runtime,
            self.home,
            requested_host_id=self.target_id,
            interval_minutes=30,
        )
        runtime.calls.clear()

        def cleanup_inconclusive():
            raise MODULE.ProcessCleanupInconclusiveError(
                "remote local sync cleanup was inconclusive"
            )

        original_transition = MODULE._transition_operation_fence

        def fail_quarantine(*args, **kwargs):
            state_value = args[-2]
            status = args[-1]
            if (
                state_value["operation"] == "remote-apply"
                and status == "process-cleanup-inconclusive"
            ):
                raise MODULE.StatePublicationError(
                    "injected remote quarantine publication failure"
                )
            return original_transition(*args, **kwargs)

        runtime.run_scheduled_hook = cleanup_inconclusive
        with mock.patch.object(
            MODULE,
            "_transition_operation_fence",
            side_effect=fail_quarantine,
        ):
            with self.assertRaises(MODULE.RemoteProcessCleanupInconclusiveError):
                MODULE.remote_apply(
                    runtime,
                    self.home,
                    host_id=self.target_id,
                    controller_id=self.controller_id,
                    expected=self.desired(),
                )

        self.assertEqual(
            self.operation_state("remote-apply")["status"],
            "in-flight",
        )
        calls_after_failure = len(runtime.calls)
        with self.assertRaises(MODULE.RemoteProcessCleanupInconclusiveError):
            MODULE.remote_apply(
                runtime,
                self.home,
                host_id=self.target_id,
                controller_id=self.controller_id,
                expected=self.desired(),
            )
        self.assertEqual(len(runtime.calls), calls_after_failure)

    def scheduled_argv(self, *extra):
        return [
            "run-scheduled",
            "--mode",
            "private",
            "--repo",
            MODULE.PRIVATE_REPO,
            "--base-repo",
            MODULE.PUBLIC_REPO,
            "--owner",
            MODULE.PRIVATE_OWNER,
            "--home",
            str(self.home),
            *extra,
        ]

    def test_standalone_scheduled_run_validates_then_syncs_once_without_ssh(self):
        runtime = self.runtime(candidates=("standalone",), gui=True)
        MODULE.activate(
            runtime,
            self.home,
            requested_host_id="standalone",
            interval_minutes=30,
        )
        runtime.calls.clear()
        validation_complete = False
        original_validate = MODULE._validate_activation

        def validate(*args, **kwargs):
            nonlocal validation_complete
            effective = original_validate(*args, **kwargs)
            validation_complete = True
            return effective

        def local_sync():
            self.assertTrue(validation_complete)

        runtime.run_scheduled_hook = local_sync
        with mock.patch.object(MODULE, "_validate_activation", side_effect=validate):
            self.assertEqual(MODULE.main(self.scheduled_argv(), runtime), 0)

        canonical_syncs = [
            call
            for call in runtime.calls
            if len(call) > 1 and call[1] == "run-scheduled"
        ]
        self.assertEqual(len(canonical_syncs), 1)
        self.assertEqual(canonical_syncs[0][0], self.physical_runner())
        self.assertEqual(self.ssh_calls(runtime), [])

    def test_headless_scheduled_run_rejects_before_local_sync_or_ssh(self):
        runtime = self.runtime(candidates=(self.target_id,), gui=False)
        MODULE.activate(
            runtime,
            self.home,
            requested_host_id=self.target_id,
            interval_minutes=30,
        )
        runtime.calls.clear()

        with mock.patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(MODULE.main(self.scheduled_argv(), runtime), 1)

        self.assertFalse(
            any(
                len(call) > 1 and call[1] == "run-scheduled"
                for call in runtime.calls
            )
        )
        self.assertEqual(self.ssh_calls(runtime), [])

    def test_standalone_pending_and_old_receipt_pending_block_before_sync(self):
        runtime = self.runtime(candidates=("standalone",), gui=True)
        MODULE.activate(
            runtime,
            self.home,
            requested_host_id="standalone",
            interval_minutes=30,
        )
        pending_path = (
            self.home
            / MODULE.STATE_DIRECTORY_NAME
            / MODULE.ACTIVATION_PENDING_FILE_NAME
        )

        def write_pending(status):
            pending_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "status": status,
                        "receipt_sha256": "a" * 64,
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            os.chmod(pending_path, 0o600)

        write_pending("pending")
        runtime.calls.clear()
        with mock.patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(MODULE.main(self.scheduled_argv(), runtime), 1)
        self.assertFalse(
            any(
                len(call) > 1 and call[1] == "run-scheduled"
                for call in runtime.calls
            )
        )

        pending_path.unlink()
        explicit_standalone = {
            "id": "standalone",
            "role": "gui-standalone",
            "username": "hoteng",
            "uid": self.uid,
            "home": str(self.account_home),
        }
        self.write_release(
            NEXT_PRIVATE_SHA,
            self.inventory_data(extra_hosts=[explicit_standalone]),
        )
        self.switch_current(NEXT_PRIVATE_SHA)
        write_pending("retryable")
        runtime.calls.clear()
        with mock.patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(MODULE.main(self.scheduled_argv(), runtime), 1)
        self.assertFalse(
            any(
                len(call) > 1 and call[1] == "run-scheduled"
                for call in runtime.calls
            )
        )
        self.assertEqual(self.ssh_calls(runtime), [])

    def test_main_exit_codes_for_success_auto_degraded_and_strict_degraded(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        runtime.ssh_results = [self.json_result(self.success_receipt())]
        self.assertEqual(MODULE.main(self.scheduled_argv(), runtime), 0)

        self.write_release(NEXT_PRIVATE_SHA, self.inventory_data())
        self.switch_current(NEXT_PRIVATE_SHA)
        runtime.identity_pair = self.desired(NEXT_PRIVATE_SHA, NEXT_PRIVATE_TREE)
        runtime.ssh_results = [
            MODULE.CommandResult(255, b"", b"uncertain"),
            MODULE.CommandResult(255, b"", b"uncertain"),
        ]
        self.assertEqual(MODULE.main(self.scheduled_argv(), runtime), 0)
        self.assertEqual(MODULE.main(self.scheduled_argv("--strict"), runtime), 2)
        with mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(
                MODULE.main(
                    ["status", "--home", str(self.home), "--json", "--strict"],
                    runtime,
                ),
                2,
            )

    def test_main_manual_target_strict_degraded_is_two_without_local_sync(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        runtime.ssh_results = [MODULE.CommandResult(255, b"", b"uncertain")]

        exit_code = MODULE.main(
            [
                "sync-target",
                "--home",
                str(self.home),
                "--target-id",
                self.target_id,
                "--strict",
            ],
            runtime,
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(len(self.ssh_calls(runtime)), 1)
        self.assertFalse(
            any(len(call) > 1 and call[1] == "run-scheduled" for call in runtime.calls)
        )

    def test_manual_force_controls_healthy_target_skip(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        runtime.calls.clear()
        runtime.ssh_results = [self.json_result(self.success_receipt())]
        MODULE.controller_run(runtime, self.home, strict=False)
        runtime.calls.clear()

        self.assertTrue(
            MODULE.sync_target(
                runtime,
                self.home,
                target_id=self.target_id,
                force=False,
                strict=True,
            )
        )
        self.assertEqual(len(self.ssh_calls(runtime)), 0)

        runtime.ssh_results = [self.json_result(self.success_receipt())]
        self.assertTrue(
            MODULE.sync_target(
                runtime,
                self.home,
                target_id=self.target_id,
                force=True,
                strict=True,
            )
        )
        self.assertEqual(len(self.ssh_calls(runtime)), 1)

    def test_main_operational_receipt_error_is_one(self):
        runtime = self.runtime()
        self.activate_controller(runtime)
        receipt = self.home / MODULE.STATE_DIRECTORY_NAME / MODULE.ACTIVATION_FILE_NAME
        receipt.write_text("{}\n", encoding="utf-8")
        os.chmod(receipt, 0o600)
        with mock.patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(MODULE.main(self.scheduled_argv(), runtime), 1)


class _SupervisorFakeStream:
    _next_fd = 500

    def __init__(self, name):
        self.name = name
        self.closed = False
        self.fd = self._next_fd
        type(self)._next_fd += 1

    def fileno(self):
        return self.fd

    def close(self):
        self.closed = True


class _SupervisorFakeProcess:
    def __init__(self, events=None, returncode=-MODULE.signal.SIGKILL):
        self.pid = 424242
        self.stdout = _SupervisorFakeStream("stdout")
        self.stderr = _SupervisorFakeStream("stderr")
        self.returncode = None
        self.events = events if events is not None else []
        self.final_returncode = returncode

    def poll(self):
        raise AssertionError("poll must not reap the process-group leader")

    def wait(self, *, timeout):
        self.events.append(("wait", timeout))
        self.returncode = self.final_returncode
        return self.returncode


class _SupervisorKey:
    def __init__(self, fileobj, data):
        self.fileobj = fileobj
        self.data = data


class _SupervisorFakeSelector:
    def __init__(self, *, fail_register_at=None, selections=None):
        self.fail_register_at = fail_register_at
        self.selections = list(selections or [])
        self.keys = []
        self.register_calls = 0
        self.closed = False

    def register(self, stream, _events, data):
        self.register_calls += 1
        if self.register_calls == self.fail_register_at:
            raise OSError("injected selector registration failure")
        key = _SupervisorKey(stream, data)
        self.keys.append(key)
        return key

    def unregister(self, stream):
        self.keys = [key for key in self.keys if key.fileobj is not stream]

    def get_map(self):
        return {index: key for index, key in enumerate(self.keys)}

    def select(self, _timeout):
        if self.selections:
            selection = self.selections.pop(0)
            if selection in ("stdout", "stderr"):
                return [
                    (next(key for key in self.keys if key.data == selection), 1)
                ]
            return selection
        return []

    def close(self):
        self.closed = True


class ProcessSupervisorTests(unittest.TestCase):
    @staticmethod
    def complete_receipt(returncode=-MODULE.signal.SIGKILL):
        return MODULE._ProcessCleanupReceipt(
            returncode=returncode,
            group_absent=True,
            pipes_drained=True,
            child_reaped=True,
        )

    def test_system_runtime_forwards_before_spawn_callback(self):
        callback = mock.Mock()
        expected = MODULE.CommandResult(0, b"ok\n", b"")
        with mock.patch.object(
            MODULE,
            "_run_bounded_process",
            return_value=expected,
        ) as bounded:
            actual = MODULE.SystemRuntime().run(
                ("/test/command", "argument"),
                timeout=7.0,
                output_limit=8192,
                before_spawn=callback,
            )

        self.assertIs(actual, expected)
        bounded.assert_called_once_with(
            ("/test/command", "argument"),
            timeout=7.0,
            output_limit=8192,
            before_spawn=callback,
        )

    def test_waitid_observer_saves_exit_status_before_post_probe_cancel(self):
        process = _SupervisorFakeProcess(returncode=23)
        observer = MODULE._ProcessStatusObserver()
        result = mock.Mock(
            si_pid=process.pid,
            si_code=17,
            si_status=23,
        )
        cancellations = []

        def cancel():
            cancellations.append((observer.exit_observed, observer.returncode))
            if len(cancellations) == 2:
                raise MODULE._ManagedProcessSignal(MODULE.signal.SIGTERM)

        with (
            mock.patch.object(MODULE, "_waitable_sigchld_failure", return_value=None),
            mock.patch.object(MODULE, "_waitid_available", return_value=True),
            mock.patch.object(MODULE.os, "waitid", return_value=result, create=True),
            mock.patch.object(MODULE.os, "P_PID", 1, create=True),
            mock.patch.object(MODULE.os, "WEXITED", 2, create=True),
            mock.patch.object(MODULE.os, "WNOHANG", 4, create=True),
            mock.patch.object(MODULE.os, "WNOWAIT", 8, create=True),
            mock.patch.object(MODULE.os, "CLD_EXITED", 17, create=True),
            self.assertRaises(MODULE._ManagedProcessSignal) as raised,
        ):
            MODULE._observe_process_exit_without_reaping(
                process,
                observer,
                float("inf"),
                cancel=cancel,
            )

        self.assertEqual(raised.exception.signum, MODULE.signal.SIGTERM)
        self.assertEqual(cancellations, [(False, None), (True, 23)])
        self.assertTrue(observer.exit_observed)
        self.assertEqual(observer.returncode, 23)

    def test_kqueue_observer_saves_exit_state_before_post_probe_cancel(self):
        process = _SupervisorFakeProcess()
        event = mock.Mock(
            flags=0,
            data=0,
            ident=process.pid,
            filter=-5,
            fflags=0x80000000,
        )

        class Queue:
            @staticmethod
            def control(_changes, _max_events, _timeout):
                return [event]

        observer = MODULE._ProcessStatusObserver(queue=Queue())
        cancellations = []

        def cancel():
            cancellations.append((observer.exit_observed, observer.returncode))
            if len(cancellations) == 2:
                raise MODULE._ManagedProcessSignal(MODULE.signal.SIGINT)

        with (
            mock.patch.object(MODULE, "_waitable_sigchld_failure", return_value=None),
            mock.patch.object(MODULE, "_waitid_available", return_value=False),
            mock.patch.object(MODULE.select, "KQ_EV_ERROR", 0x4000, create=True),
            mock.patch.object(MODULE.select, "KQ_FILTER_PROC", -5, create=True),
            mock.patch.object(MODULE.select, "KQ_NOTE_EXIT", 0x80000000, create=True),
            self.assertRaises(MODULE._ManagedProcessSignal) as raised,
        ):
            MODULE._observe_process_exit_without_reaping(
                process,
                observer,
                float("inf"),
                cancel=cancel,
            )

        self.assertEqual(raised.exception.signum, MODULE.signal.SIGINT)
        self.assertEqual(cancellations, [(False, None), (True, None)])
        self.assertTrue(observer.exit_observed)
        self.assertIsNone(observer.returncode)

    def test_cleanup_orders_term_kill_group_absence_before_reap(self):
        events = []
        process = _SupervisorFakeProcess(events, returncode=-MODULE.signal.SIGTERM)
        selector = _SupervisorFakeSelector()
        observer = MODULE._ProcessStatusObserver()
        ownership = MODULE._ProcessOwnership(state="group-owned")

        def signal_group(_process, signum, _ownership):
            events.append(("signal", signum))
            return MODULE._ProcessSignalResult(target_existed=True)

        def observe(_process, observed, _deadline):
            events.append(("status", None))
            observed.exit_observed = True
            observed.returncode = -MODULE.signal.SIGTERM
            return observed.returncode

        with (
            mock.patch.object(MODULE.sys, "platform", "linux"),
            mock.patch.object(
                MODULE, "_register_cleanup_streams", return_value=[]
            ),
            mock.patch.object(
                MODULE,
                "_bound_process_group_exists",
                side_effect=lambda *_args, **_kwargs: (True, None),
            ),
            mock.patch.object(
                MODULE,
                "_drain_process_pipes",
                side_effect=lambda *_args, **_kwargs: (
                    events.append(("drain", None)) or True,
                    [],
                ),
            ),
            mock.patch.object(
                MODULE,
                "_wait_for_process_group_absence",
                side_effect=[(True, None), (False, None)],
            ) as wait_for_absence,
            mock.patch.object(
                MODULE, "_observe_process_exit_without_reaping", side_effect=observe
            ),
            mock.patch.object(MODULE, "_signal_process_group", side_effect=signal_group),
            mock.patch.object(MODULE.os, "killpg", side_effect=ProcessLookupError),
        ):
            receipt = MODULE._terminate_process(
                process,
                selector,
                observer,
                ownership,
                {"stdout", "stderr"},
            )

        self.assertTrue(receipt.complete, receipt.issues)
        self.assertEqual(ownership.state, "released")
        self.assertEqual(
            [event for event in events if event[0] == "signal"],
            [
                ("signal", MODULE.signal.SIGTERM),
                ("signal", MODULE.signal.SIGKILL),
            ],
        )
        self.assertEqual(wait_for_absence.call_count, 2)
        wait_index = next(index for index, event in enumerate(events) if event[0] == "wait")
        self.assertLess(
            max(index for index, event in enumerate(events) if event[0] == "signal"),
            wait_index,
        )

    def test_cleanup_residual_group_is_inconclusive_and_never_reaps(self):
        process = _SupervisorFakeProcess()
        selector = _SupervisorFakeSelector()
        observer = MODULE._ProcessStatusObserver(
            exit_observed=True,
            returncode=-MODULE.signal.SIGKILL,
        )
        ownership = MODULE._ProcessOwnership(state="group-owned")
        with (
            mock.patch.object(MODULE, "_register_cleanup_streams", return_value=[]),
            mock.patch.object(
                MODULE, "_bound_process_group_exists", return_value=(True, None)
            ),
            mock.patch.object(
                MODULE, "_drain_process_pipes", return_value=(True, [])
            ),
            mock.patch.object(
                MODULE,
                "_wait_for_process_group_absence",
                side_effect=[(True, None), (True, None)],
            ),
            mock.patch.object(
                MODULE,
                "_signal_process_group",
                return_value=MODULE._ProcessSignalResult(target_existed=True),
            ),
        ):
            receipt = MODULE._terminate_process(
                process,
                selector,
                observer,
                ownership,
                {"stdout", "stderr"},
            )

        self.assertFalse(receipt.complete)
        self.assertFalse(receipt.group_absent)
        self.assertIsNone(process.returncode)
        self.assertEqual(ownership.state, "group-owned")

    def test_linux_empty_term_scan_still_kills_owned_group_before_reap(self):
        events = []
        process = _SupervisorFakeProcess(events, returncode=-MODULE.signal.SIGTERM)
        selector = _SupervisorFakeSelector()
        observer = MODULE._ProcessStatusObserver(
            exit_observed=True,
            returncode=-MODULE.signal.SIGTERM,
        )
        ownership = MODULE._ProcessOwnership(state="group-owned")

        def signal_group(_process, signum, _ownership):
            events.append(("signal", signum))
            return MODULE._ProcessSignalResult(
                target_existed=signum != MODULE.signal.SIGKILL
            )

        with (
            mock.patch.object(MODULE.sys, "platform", "linux"),
            mock.patch.object(MODULE, "_register_cleanup_streams", return_value=[]),
            mock.patch.object(
                MODULE, "_bound_process_group_exists", return_value=(True, None)
            ),
            mock.patch.object(MODULE, "_drain_process_pipes", return_value=(True, [])),
            mock.patch.object(
                MODULE,
                "_wait_for_process_group_absence",
                side_effect=[(False, None), (False, None)],
            ),
            mock.patch.object(MODULE, "_signal_process_group", side_effect=signal_group),
            mock.patch.object(MODULE.os, "killpg", side_effect=ProcessLookupError),
        ):
            receipt = MODULE._terminate_process(
                process,
                selector,
                observer,
                ownership,
                {"stdout", "stderr"},
            )

        self.assertTrue(receipt.complete, receipt.issues)
        self.assertEqual(
            [event for event in events if event[0] == "signal"],
            [
                ("signal", MODULE.signal.SIGTERM),
                ("signal", MODULE.signal.SIGKILL),
            ],
        )
        kill_index = events.index(("signal", MODULE.signal.SIGKILL))
        wait_index = next(
            index for index, event in enumerate(events) if event[0] == "wait"
        )
        self.assertLess(kill_index, wait_index)

    def test_observer_failure_still_reaps_and_remains_inconclusive(self):
        process = _SupervisorFakeProcess()
        selector = _SupervisorFakeSelector()
        observer = MODULE._ProcessStatusObserver()
        ownership = MODULE._ProcessOwnership(state="group-owned")
        with (
            mock.patch.object(MODULE, "_waitid_available", return_value=False),
            mock.patch.object(
                MODULE,
                "_register_process_status_observer",
                side_effect=OSError("observer unavailable"),
            ),
            mock.patch.object(MODULE, "_register_cleanup_streams", return_value=[]),
            mock.patch.object(
                MODULE, "_bound_process_group_exists", return_value=(True, None)
            ),
            mock.patch.object(
                MODULE, "_drain_process_pipes", return_value=(True, [])
            ),
            mock.patch.object(
                MODULE,
                "_wait_for_process_group_absence",
                side_effect=[(True, None), (True, None)],
            ),
            mock.patch.object(
                MODULE,
                "_observe_process_exit_without_reaping",
                side_effect=MODULE.ControllerError("observer unavailable"),
            ),
            mock.patch.object(
                MODULE,
                "_signal_process_group",
                return_value=MODULE._ProcessSignalResult(target_existed=True),
            ),
            mock.patch.object(MODULE.os, "killpg", side_effect=ProcessLookupError),
        ):
            receipt = MODULE._terminate_process(
                process,
                selector,
                observer,
                ownership,
                {"stdout", "stderr"},
            )

        self.assertTrue(receipt.child_reaped)
        self.assertTrue(receipt.group_absent)
        self.assertFalse(receipt.complete)
        self.assertEqual(ownership.state, "released")

    def test_selector_setup_failure_runs_owned_cleanup_and_closes_all_resources(self):
        process = _SupervisorFakeProcess()
        selector = _SupervisorFakeSelector(fail_register_at=2)
        receipt = self.complete_receipt()
        with (
            mock.patch.object(MODULE, "_require_process_supervision_support"),
            mock.patch.object(MODULE.selectors, "DefaultSelector", return_value=selector),
            mock.patch.object(MODULE.subprocess, "Popen", return_value=process),
            mock.patch.object(
                MODULE,
                "_register_process_status_observer",
                side_effect=lambda _process, observer: observer,
            ),
            mock.patch.object(MODULE.os, "set_blocking"),
            mock.patch.object(MODULE, "_terminate_process", return_value=receipt) as cleanup,
            self.assertRaisesRegex(OSError, "selector registration failure"),
        ):
            MODULE._run_bounded_process(("/test/command",), timeout=1, output_limit=8)

        cleanup.assert_called_once()
        self.assertTrue(selector.closed)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

    def test_latched_signal_before_popen_prevents_spawn(self):
        events = []
        process = _SupervisorFakeProcess(events)
        selector = _SupervisorFakeSelector()

        class LatchedSignal:
            pending = MODULE.signal.SIGTERM

            def install(self):
                events.append(("install", None))

            def raise_if_pending(self):
                raise MODULE._ManagedProcessSignal(self.pending)

            def restore(self):
                events.append(("restore", None))
                return []

        def popen(*_args, **_kwargs):
            events.append(("popen", None))
            return process

        def cleanup(*_args, **_kwargs):
            events.append(("cleanup", None))
            return self.complete_receipt()

        with (
            mock.patch.object(MODULE, "_require_process_supervision_support"),
            mock.patch.object(MODULE, "_ProcessSignalLatch", return_value=LatchedSignal()),
            mock.patch.object(MODULE.selectors, "DefaultSelector", return_value=selector),
            mock.patch.object(MODULE.subprocess, "Popen", side_effect=popen) as spawn,
            mock.patch.object(
                MODULE, "_terminate_process", side_effect=cleanup
            ) as terminate,
            self.assertRaises(MODULE._ManagedProcessSignal) as raised,
        ):
            MODULE._run_bounded_process(("/test/command",), timeout=1, output_limit=8)

        self.assertEqual(raised.exception.signum, MODULE.signal.SIGTERM)
        spawn.assert_not_called()
        terminate.assert_not_called()
        self.assertEqual(events, [("install", None), ("restore", None)])
        self.assertTrue(selector.closed)

    def test_before_spawn_callback_is_last_fallible_gate_before_popen(self):
        events = []
        selector = _SupervisorFakeSelector()

        def before_spawn():
            events.append("fence")

        def fail_popen(*_args, **_kwargs):
            events.append("popen")
            raise OSError("injected spawn failure")

        with (
            mock.patch.object(MODULE, "_require_process_supervision_support"),
            mock.patch.object(
                MODULE.selectors,
                "DefaultSelector",
                return_value=selector,
            ),
            mock.patch.object(MODULE.subprocess, "Popen", side_effect=fail_popen),
            self.assertRaisesRegex(MODULE.ControllerError, "failed to start command"),
        ):
            MODULE._run_bounded_process(
                ("/test/command",),
                timeout=1,
                output_limit=8,
                before_spawn=before_spawn,
            )

        self.assertEqual(events, ["fence", "popen"])
        self.assertTrue(selector.closed)

    def test_before_spawn_callback_failure_prevents_popen(self):
        selector = _SupervisorFakeSelector()
        callback_calls = 0

        def fail_fence():
            nonlocal callback_calls
            callback_calls += 1
            raise MODULE.StatePublicationError("injected named fence failure")

        with (
            mock.patch.object(MODULE, "_require_process_supervision_support"),
            mock.patch.object(
                MODULE.selectors,
                "DefaultSelector",
                return_value=selector,
            ),
            mock.patch.object(MODULE.subprocess, "Popen") as spawn,
            self.assertRaisesRegex(
                MODULE.StatePublicationError,
                "named fence failure",
            ),
        ):
            MODULE._run_bounded_process(
                ("/test/command",),
                timeout=1,
                output_limit=8,
                before_spawn=fail_fence,
            )

        self.assertEqual(callback_calls, 1)
        spawn.assert_not_called()
        self.assertTrue(selector.closed)

    def test_post_eof_normal_wait_passes_cancel_and_cleans_before_reraise(self):
        process = _SupervisorFakeProcess(returncode=0)
        selector = _SupervisorFakeSelector(selections=["stdout", "stderr"])

        class PostEofSignal:
            pending = None

            @staticmethod
            def install():
                return None

            def raise_if_pending(self):
                if self.pending is not None:
                    raise MODULE._ManagedProcessSignal(self.pending)

            @staticmethod
            def restore():
                return []

        latch = PostEofSignal()
        observed_state = []

        def observe(_process, observer, _deadline, *, cancel=None):
            self.assertIsNotNone(cancel)
            observer.exit_observed = True
            observer.returncode = 0
            observed_state.append((observer.exit_observed, observer.returncode))
            latch.pending = MODULE.signal.SIGTERM
            cancel()
            raise AssertionError("cancellation did not raise")

        def cleanup(_process, _selector, observer, ownership, eof, **_kwargs):
            self.assertTrue(observer.exit_observed)
            self.assertEqual(observer.returncode, 0)
            self.assertEqual(eof, {"stdout", "stderr"})
            ownership.transfer_to_reap(0)
            ownership.release()
            return self.complete_receipt(0)

        with (
            mock.patch.object(MODULE, "_require_process_supervision_support"),
            mock.patch.object(MODULE, "_ProcessSignalLatch", return_value=latch),
            mock.patch.object(MODULE.selectors, "DefaultSelector", return_value=selector),
            mock.patch.object(MODULE.subprocess, "Popen", return_value=process),
            mock.patch.object(
                MODULE,
                "_register_process_status_observer",
                side_effect=lambda _process, observer: observer,
            ),
            mock.patch.object(MODULE.os, "set_blocking"),
            mock.patch.object(MODULE.os, "read", return_value=b""),
            mock.patch.object(
                MODULE,
                "_observe_process_exit_without_reaping",
                side_effect=observe,
            ) as wait_for_exit,
            mock.patch.object(MODULE, "_terminate_process", side_effect=cleanup) as terminate,
            self.assertRaises(MODULE._ManagedProcessSignal) as raised,
        ):
            MODULE._run_bounded_process(
                ("/test/command",),
                timeout=1,
                output_limit=8,
            )

        self.assertEqual(raised.exception.signum, MODULE.signal.SIGTERM)
        self.assertEqual(observed_state, [(True, 0)])
        self.assertEqual(wait_for_exit.call_count, 1)
        terminate.assert_called_once()
        self.assertTrue(selector.closed)

    def test_real_pipe_eof_signal_cancels_status_wait_and_completes_cleanup(self):
        entered_post_eof_wait = threading.Event()
        sender_errors = []
        original_observe = MODULE._observe_process_exit_without_reaping

        def observe(*args, **kwargs):
            if kwargs.get("cancel") is not None:
                entered_post_eof_wait.set()
            return original_observe(*args, **kwargs)

        def send_signal():
            if not entered_post_eof_wait.wait(5):
                sender_errors.append("post-EOF status wait was not entered")
                return
            os.kill(os.getpid(), MODULE.signal.SIGTERM)

        sender = threading.Thread(target=send_signal, daemon=True)
        sender.start()
        started = time.monotonic()
        try:
            with (
                mock.patch.object(
                    MODULE,
                    "_observe_process_exit_without_reaping",
                    side_effect=observe,
                ),
                self.assertRaises(MODULE._ManagedProcessSignal) as raised,
            ):
                MODULE._run_bounded_process(
                    (
                        sys.executable,
                        "-c",
                        "import os,time; os.close(1); os.close(2); time.sleep(30)",
                    ),
                    timeout=10,
                    output_limit=1024,
                )
        finally:
            sender.join(5)

        self.assertEqual(sender_errors, [])
        self.assertFalse(sender.is_alive())
        self.assertEqual(raised.exception.signum, MODULE.signal.SIGTERM)
        self.assertLess(time.monotonic() - started, 5)

    def test_output_overflow_keeps_exact_bound_then_cleans(self):
        process = _SupervisorFakeProcess(returncode=-MODULE.signal.SIGTERM)
        selector = _SupervisorFakeSelector(selections=["stdout"])
        with (
            mock.patch.object(MODULE, "_require_process_supervision_support"),
            mock.patch.object(MODULE.selectors, "DefaultSelector", return_value=selector),
            mock.patch.object(MODULE.subprocess, "Popen", return_value=process),
            mock.patch.object(
                MODULE,
                "_register_process_status_observer",
                side_effect=lambda _process, observer: observer,
            ),
            mock.patch.object(MODULE.os, "set_blocking"),
            mock.patch.object(MODULE.os, "read", return_value=b"12345"),
            mock.patch.object(
                MODULE,
                "_terminate_process",
                return_value=self.complete_receipt(-MODULE.signal.SIGTERM),
            ) as cleanup,
        ):
            result = MODULE._run_bounded_process(
                ("/test/command",), timeout=1, output_limit=4
            )

        self.assertEqual(result.stdout, b"1234")
        self.assertTrue(result.output_overflow)
        self.assertFalse(result.timed_out)
        cleanup.assert_called_once()

    def test_selector_keyboard_interrupt_cleans_then_reraises_same_object(self):
        process = _SupervisorFakeProcess()
        selector = _SupervisorFakeSelector()
        interrupt = KeyboardInterrupt("injected selector interrupt")
        receipt = self.complete_receipt()
        with (
            mock.patch.object(MODULE, "_require_process_supervision_support"),
            mock.patch.object(MODULE.selectors, "DefaultSelector", return_value=selector),
            mock.patch.object(MODULE.subprocess, "Popen", return_value=process),
            mock.patch.object(
                MODULE,
                "_register_process_status_observer",
                side_effect=lambda _process, observer: observer,
            ),
            mock.patch.object(MODULE.os, "set_blocking"),
            mock.patch.object(selector, "select", side_effect=interrupt),
            mock.patch.object(MODULE, "_terminate_process", return_value=receipt) as cleanup,
            self.assertRaises(KeyboardInterrupt) as raised,
        ):
            MODULE._run_bounded_process(("/test/command",), timeout=1, output_limit=8)

        self.assertIs(raised.exception, interrupt)
        cleanup.assert_called_once()
        self.assertTrue(selector.closed)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

    @staticmethod
    def darwin_census_result(pids=(), *, byte_count=None, error_number=0):
        def census(proc_type, type_info, buffer, buffer_size):
            if proc_type != MODULE.DARWIN_PROC_PGRP_ONLY:
                raise AssertionError("unexpected Darwin process-list type")
            if type_info != 424242:
                raise AssertionError("unexpected Darwin process-group identity")
            for index, pid in enumerate(pids):
                buffer[index] = pid
            returned = (
                len(pids) * MODULE.ctypes.sizeof(MODULE.ctypes.c_int)
                if byte_count is None
                else byte_count(buffer_size)
                if callable(byte_count)
                else byte_count
            )
            return returned, error_number

        return census

    def darwin_eperm_probe(self, census):
        process = _SupervisorFakeProcess()
        with (
            mock.patch.object(MODULE.sys, "platform", "darwin"),
            mock.patch.object(MODULE, "_waitable_sigchld_failure", return_value=None),
            mock.patch.object(
                MODULE.os,
                "killpg",
                side_effect=PermissionError(MODULE.errno.EPERM, "denied"),
            ),
            mock.patch.object(MODULE, "_call_darwin_proc_listpids", side_effect=census),
        ):
            return MODULE._bound_process_group_exists(
                process,
                leader_exited=True,
                deadline=float("inf"),
            )

    def test_darwin_eperm_leader_only_census_is_quiescent(self):
        exists, error = self.darwin_eperm_probe(
            self.darwin_census_result((424242,))
        )

        self.assertFalse(exists)
        self.assertIsNone(error)

    def test_darwin_eperm_census_with_descendant_remains_present(self):
        exists, error = self.darwin_eperm_probe(
            self.darwin_census_result((424242, 424243))
        )

        self.assertTrue(exists)
        self.assertIsNone(error)

    def test_darwin_eperm_census_failures_are_inconclusive(self):
        census_calls = 0

        def changing_census(proc_type, type_info, buffer, buffer_size):
            nonlocal census_calls
            census_calls += 1
            pids = (424242,) if census_calls == 1 else (424242, 424243)
            return self.darwin_census_result(pids)(
                proc_type, type_info, buffer, buffer_size
            )

        cases = (
            ("error", self.darwin_census_result(error_number=MODULE.errno.EIO)),
            (
                "truncated",
                self.darwin_census_result(
                    (424242,), byte_count=lambda buffer_size: buffer_size
                ),
            ),
            ("identity", self.darwin_census_result((424243,))),
            ("changed", changing_census),
        )
        for name, census in cases:
            with self.subTest(name=name):
                census_calls = 0
                exists, error = self.darwin_eperm_probe(census)
                self.assertTrue(exists)
                self.assertIsNotNone(error)

    def test_darwin_eperm_census_error_keeps_cleanup_inconclusive_and_unreaped(self):
        process = _SupervisorFakeProcess()
        selector = _SupervisorFakeSelector()
        observer = MODULE._ProcessStatusObserver(
            exit_observed=True,
            returncode=-MODULE.signal.SIGKILL,
        )
        ownership = MODULE._ProcessOwnership(state="group-owned")
        census = self.darwin_census_result(error_number=MODULE.errno.EIO)
        with (
            mock.patch.object(MODULE.sys, "platform", "darwin"),
            mock.patch.object(MODULE, "_waitable_sigchld_failure", return_value=None),
            mock.patch.object(
                MODULE.os,
                "killpg",
                side_effect=PermissionError(MODULE.errno.EPERM, "denied"),
            ),
            mock.patch.object(MODULE, "_call_darwin_proc_listpids", side_effect=census),
            mock.patch.object(MODULE, "_register_cleanup_streams", return_value=[]),
            mock.patch.object(MODULE, "_drain_process_pipes", return_value=(True, [])),
            mock.patch.object(
                MODULE,
                "_signal_process_group",
                return_value=MODULE._ProcessSignalResult(target_existed=True),
            ),
        ):
            receipt = MODULE._terminate_process(
                process,
                selector,
                observer,
                ownership,
                {"stdout", "stderr"},
            )

        self.assertFalse(receipt.complete)
        self.assertFalse(receipt.group_absent)
        self.assertFalse(receipt.child_reaped)
        self.assertEqual(ownership.state, "group-owned")
        self.assertTrue(
            any("Darwin process-group census failed" in issue for issue in receipt.issues)
        )

    def test_linux_post_reap_zero_probe_requires_esrch(self):
        cases = (
            ("esrch", ProcessLookupError(), True),
            ("present", None, False),
            (
                "eperm",
                PermissionError(MODULE.errno.EPERM, "denied"),
                False,
            ),
        )
        for name, outcome, expected_complete in cases:
            with self.subTest(name=name):
                events = []
                process = _SupervisorFakeProcess(
                    events, returncode=-MODULE.signal.SIGTERM
                )
                selector = _SupervisorFakeSelector()
                observer = MODULE._ProcessStatusObserver(
                    exit_observed=True,
                    returncode=-MODULE.signal.SIGTERM,
                )
                ownership = MODULE._ProcessOwnership(state="group-owned")

                def post_reap_probe(pgid, signum):
                    events.append(("post-reap-probe", signum))
                    self.assertEqual(pgid, process.pid)
                    self.assertEqual(signum, 0)
                    self.assertIsNotNone(process.returncode)
                    if outcome is not None:
                        raise outcome

                with (
                    mock.patch.object(MODULE.sys, "platform", "linux"),
                    mock.patch.object(
                        MODULE, "_register_cleanup_streams", return_value=[]
                    ),
                    mock.patch.object(
                        MODULE,
                        "_bound_process_group_exists",
                        return_value=(True, None),
                    ),
                    mock.patch.object(
                        MODULE, "_drain_process_pipes", return_value=(True, [])
                    ),
                    mock.patch.object(
                        MODULE,
                        "_wait_for_process_group_absence",
                        side_effect=[(False, None), (False, None)],
                    ),
                    mock.patch.object(
                        MODULE,
                        "_signal_process_group",
                        return_value=MODULE._ProcessSignalResult(target_existed=True),
                    ),
                    mock.patch.object(
                        MODULE.os, "killpg", side_effect=post_reap_probe
                    ),
                ):
                    receipt = MODULE._terminate_process(
                        process,
                        selector,
                        observer,
                        ownership,
                        {"stdout", "stderr"},
                    )

                self.assertEqual(receipt.complete, expected_complete, receipt.issues)
                self.assertEqual(receipt.group_absent, expected_complete)
                self.assertTrue(receipt.child_reaped)
                self.assertEqual(ownership.state, "released")
                self.assertEqual(
                    [event for event in events if event[0] == "post-reap-probe"],
                    [("post-reap-probe", 0)],
                )

    def test_linux_waitid_identity_loss_blocks_destructive_group_signals(self):
        cases = (
            (
                "before-term",
                [ChildProcessError(MODULE.errno.ECHILD, "lost before TERM")],
                [],
            ),
            (
                "before-kill",
                [None, ChildProcessError(MODULE.errno.ECHILD, "lost before KILL")],
                [MODULE.signal.SIGTERM],
            ),
        )
        for name, waitid_results, expected_signals in cases:
            with self.subTest(name=name):
                process = _SupervisorFakeProcess(
                    returncode=-MODULE.signal.SIGTERM
                )
                selector = _SupervisorFakeSelector()
                observer = MODULE._ProcessStatusObserver(
                    exit_observed=True,
                    returncode=-MODULE.signal.SIGTERM,
                )
                ownership = MODULE._ProcessOwnership(state="group-owned")
                destructive_signals = []

                def killpg(pgid, signum):
                    self.assertEqual(pgid, process.pid)
                    if signum == 0:
                        raise ProcessLookupError
                    destructive_signals.append(signum)

                with (
                    mock.patch.object(MODULE.sys, "platform", "linux"),
                    mock.patch.object(MODULE, "_waitid_available", return_value=True),
                    mock.patch.object(
                        MODULE, "_waitable_sigchld_failure", return_value=None
                    ),
                    mock.patch.object(
                        MODULE.os,
                        "waitid",
                        side_effect=waitid_results,
                        create=True,
                    ) as waitid,
                    mock.patch.object(MODULE.os, "P_PID", 1, create=True),
                    mock.patch.object(MODULE.os, "WEXITED", 2, create=True),
                    mock.patch.object(MODULE.os, "WNOHANG", 4, create=True),
                    mock.patch.object(MODULE.os, "WNOWAIT", 8, create=True),
                    mock.patch.object(MODULE.os, "killpg", side_effect=killpg),
                    mock.patch.object(
                        MODULE, "_register_cleanup_streams", return_value=[]
                    ),
                    mock.patch.object(
                        MODULE,
                        "_bound_process_group_exists",
                        return_value=(True, None),
                    ),
                    mock.patch.object(
                        MODULE, "_drain_process_pipes", return_value=(True, [])
                    ),
                    mock.patch.object(
                        MODULE,
                        "_wait_for_process_group_absence",
                        side_effect=[(False, None), (False, None)],
                    ),
                ):
                    receipt = MODULE._terminate_process(
                        process,
                        selector,
                        observer,
                        ownership,
                        {"stdout", "stderr"},
                    )

                self.assertEqual(destructive_signals, expected_signals)
                self.assertEqual(waitid.call_count, len(waitid_results))
                self.assertTrue(ownership.identity_lost)
                self.assertIsNotNone(ownership.identity_lost_reason)
                self.assertFalse(receipt.complete)
                self.assertTrue(receipt.child_reaped)
                self.assertTrue(
                    any("identity" in issue for issue in receipt.issues),
                    receipt.issues,
                )


def shlex_split(value):
    import shlex

    return shlex.split(value)


if __name__ == "__main__":
    unittest.main()
