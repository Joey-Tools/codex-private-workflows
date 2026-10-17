from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "personal_codex" / "bin" / "codex-node-system-ca"
loader = importlib.machinery.SourceFileLoader("node_system_ca_tests", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
MODULE = importlib.util.module_from_spec(spec)
sys.modules[loader.name] = MODULE
loader.exec_module(MODULE)


class FakeLaunchctl:
    def __init__(self, uid=9417):
        self.uid = uid
        self.home = None
        self.loaded = False
        self.enabled = False
        self.value = None
        self.calls = []
        self.mutations = []
        self.fail_bootstrap = False
        self.query_error = None
        self.gui = True
        self.manager = "Aqua"
        self.manager_uid = uid

    def __call__(self, argv, **kwargs):
        args = tuple(argv[1:])
        self.calls.append((args, kwargs.get("timeout")))
        op = args[0]
        if op == "managername":
            return self.result(0, out=self.manager + "\n")
        if op == "manageruid":
            return self.result(0, out=str(self.manager_uid) + "\n")
        if op == "print" and args[1] == "gui/%d" % self.uid:
            return self.result(0 if self.gui else 113, err="no gui domain")
        if op == "print":
            if self.query_error:
                return self.result(5, err=self.query_error)
            missing = 'Bad request.\nCould not find service "%s" in domain for user gui: %d' % (MODULE.LABEL, self.uid)
            return self.result(0) if self.loaded else self.result(113, err=missing)
        if op == "getenv":
            return self.result(0, out=(self.value or "") + ("\n" if self.value else ""))
        self.mutations.append(args)
        if op == "setenv":
            self.value = args[2]
        elif op == "unsetenv":
            self.value = None
        elif op == "enable":
            self.enabled = True
        elif op == "disable":
            self.enabled = False
        elif op == "bootstrap":
            if self.fail_bootstrap:
                return self.result(5, err="bootstrap rejected")
            self.loaded = True
        elif op == "bootout":
            self.loaded = False
        return self.result(0)

    @staticmethod
    def result(code, out="", err=""):
        return SimpleNamespace(returncode=code, stdout=out, stderr=err)


class NodeSystemCaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "user-home"
        self.home.mkdir()
        self.fake = FakeLaunchctl(uid=6382)
        self.fake.home = self.home
        self.patches = [
            mock.patch.object(MODULE.sys, "platform", "darwin"),
            mock.patch.object(MODULE.os, "getuid", return_value=self.fake.uid),
            mock.patch.object(MODULE.pwd, "getpwuid", return_value=SimpleNamespace(pw_dir=str(self.home))),
            mock.patch.object(MODULE, "LAUNCHCTL", "/fake/launchctl"),
            mock.patch.object(MODULE.subprocess, "run", side_effect=self.fake),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    @property
    def plist(self):
        return MODULE.plist_path(self.home)

    def test_dry_run_has_no_files_or_launchctl_mutations(self):
        result = MODULE.execute("install", dry_run=True)
        uninstall_result = MODULE.execute("uninstall", dry_run=True)
        self.assertFalse(self.plist.exists())
        self.assertEqual([], self.fake.mutations)
        self.assertIn("bootstrap LaunchAgent", result["planned_actions"])
        self.assertEqual("dry-run", uninstall_result["status"])
        self.assertNotIn("set GUI environment NODE_USE_SYSTEM_CA=1", uninstall_result["planned_actions"])
        self.assertNotIn("enable " + MODULE.LABEL, uninstall_result["planned_actions"])
        self.assertIn("preserve GUI environment value", uninstall_result["planned_actions"])
        self.assertIn("no managed plist or loaded service to remove", uninstall_result["planned_actions"])
        self.assertFalse(self.plist.exists())
        self.assertTrue(all(timeout == MODULE.TIMEOUT_SECONDS for _, timeout in self.fake.calls))

    def test_identity_and_home_are_derived_from_current_account(self):
        result = MODULE.execute("install")
        self.assertEqual(6382, result["uid"])
        self.assertEqual(str(self.home), result["home"])
        self.assertIn(("enable", "gui/6382/" + MODULE.LABEL), self.fake.mutations)
        payload = plistlib.loads(self.plist.read_bytes())
        self.assertEqual(MODULE.expected_plist(), payload)
        self.assertEqual("Aqua", payload["LimitLoadToSessionType"])

    def test_matching_manual_plist_is_adopted_and_repeat_skips_bootstrap(self):
        manual = dict(MODULE.expected_plist())
        self.plist.parent.mkdir(parents=True)
        self.plist.write_bytes(plistlib.dumps(manual, sort_keys=False))
        inode = self.plist.stat().st_ino
        MODULE.execute("install")
        self.assertEqual(inode, self.plist.stat().st_ino)
        self.assertTrue(any(call[0] == "bootstrap" for call in self.fake.mutations))
        self.fake.mutations.clear()
        result = MODULE.execute("install")
        self.assertIn("already loaded", result["status"])
        self.assertFalse(any(call[0] == "bootstrap" for call in self.fake.mutations))

    def test_foreign_plist_and_symlink_are_rejected(self):
        self.plist.parent.mkdir(parents=True)
        self.plist.write_bytes(plistlib.dumps({"Label": "someone.else"}))
        with self.assertRaisesRegex(MODULE.HelperError, "refusing to manage"):
            MODULE.execute("install")
        self.plist.unlink()
        target = self.plist.parent / "target.plist"
        target.write_bytes(plistlib.dumps(MODULE.expected_plist()))
        self.plist.symlink_to(target)
        with self.assertRaisesRegex(MODULE.HelperError, "foreign object"):
            MODULE.execute("uninstall")
        self.assertTrue(target.exists())
        self.assertEqual([], self.fake.mutations)

    def test_background_caller_is_rejected_even_when_gui_domain_exists(self):
        self.fake.manager = "Background"
        with self.assertRaisesRegex(MODULE.HelperError, "caller must be in an Aqua"):
            MODULE.execute("install")
        self.assertTrue(self.fake.gui)
        self.assertFalse(self.plist.exists())
        self.assertEqual([], self.fake.mutations)

    def test_status_reports_background_caller_even_when_gui_domain_exists(self):
        self.fake.manager = "Background"
        result = MODULE.execute("status")
        self.assertFalse(result["gui_context"])
        self.assertFalse(result["configured"])
        self.assertIn("Aqua GUI session", result["status"])
        self.assertIsNone(result["service_loaded"])

    def test_uninstall_refuses_loaded_orphan_without_managed_plist(self):
        self.fake.loaded = True
        with self.assertRaisesRegex(MODULE.HelperError, "orphaned loaded service"):
            MODULE.execute("uninstall")
        self.assertEqual([], self.fake.mutations)

    def test_aqua_context_must_match_current_account(self):
        self.fake.manager_uid = self.fake.uid + 1
        with self.assertRaisesRegex(MODULE.HelperError, "current account"):
            MODULE.execute("install")
        self.assertFalse(self.plist.exists())
        self.assertEqual([], self.fake.mutations)
        self.assertFalse(self.plist.exists())

    def test_uninstall_without_plist_clears_only_the_helper_value(self):
        self.fake.value = "1"
        result = MODULE.execute("uninstall")
        self.assertIsNone(self.fake.value)
        self.assertFalse(any(call[0] in ("disable", "bootout") for call in self.fake.mutations))
        self.assertIn("no managed plist", result["status"])

    def test_bootstrap_failure_keeps_managed_partial_state_and_can_retry(self):
        self.fake.fail_bootstrap = True
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(1, MODULE.main(["install"]))
        payload = json.loads(output.getvalue())
        self.assertTrue(self.plist.exists())
        self.assertEqual("1", self.fake.value)
        self.assertIn("bootstrap rejected", payload["status"])
        self.assertTrue(payload["partial_state"]["plist_installed"])
        self.fake.fail_bootstrap = False
        result = MODULE.execute("install")
        self.assertTrue(result["ok"])
        self.assertTrue(self.fake.loaded)

    def test_reinstall_reenables_disabled_service(self):
        MODULE.execute("install")
        self.fake.loaded = False
        self.fake.enabled = False
        self.fake.mutations.clear()
        MODULE.execute("install")
        self.assertTrue(self.fake.enabled)
        self.assertIn(("enable", "gui/6382/" + MODULE.LABEL), self.fake.mutations)
        self.assertTrue(any(call[0] == "bootstrap" for call in self.fake.mutations))

    def test_uninstall_clears_our_value_and_removes_only_managed_plist(self):
        MODULE.execute("install")
        MODULE.execute("uninstall")
        self.assertFalse(self.plist.exists())
        self.assertFalse(self.fake.loaded)
        self.assertIsNone(self.fake.value)
        self.assertIn(("disable", "gui/6382/" + MODULE.LABEL), self.fake.mutations)
        self.assertIn(("unsetenv", MODULE.VARIABLE), self.fake.mutations)

    def test_uninstall_preserves_custom_gui_value(self):
        MODULE.execute("install")
        self.fake.value = "custom"
        MODULE.execute("uninstall")
        self.assertEqual("custom", self.fake.value)
        self.assertFalse(any(call[0] == "unsetenv" for call in self.fake.mutations))

    def test_unknown_service_query_error_is_not_treated_as_absence(self):
        self.fake.query_error = "Input/output error"
        with self.assertRaisesRegex(MODULE.HelperError, "cannot determine"):
            MODULE.execute("install", dry_run=True)

    def test_dry_run_does_not_hide_a_later_query_failure(self):
        with mock.patch.object(
            MODULE, "service_loaded",
            side_effect=[False, MODULE.HelperError("service query became unavailable")],
        ):
            result = MODULE.execute("install", dry_run=True)
        self.assertFalse(result["ok"])
        self.assertIn("became unavailable", result["status"])
        self.assertEqual([], self.fake.mutations)
        self.assertFalse(self.plist.exists())

    def test_uninstall_does_not_hide_a_final_query_failure(self):
        MODULE.execute("install")
        with mock.patch.object(
            MODULE, "service_loaded",
            side_effect=[True, MODULE.HelperError("final service query unavailable")],
        ):
            result = MODULE.execute("uninstall")
        self.assertFalse(result["ok"])
        self.assertIn("query unavailable", result["status"])

    def test_known_missing_service_is_absence(self):
        self.assertFalse(MODULE.service_loaded(self.fake.uid))

    def test_missing_service_diagnostic_must_match_label_and_gui_uid(self):
        with mock.patch.object(
            MODULE,
            "launchctl",
            return_value=(113, "", 'Bad request.\nCould not find service "other.label" in domain for user gui: 6382'),
        ):
            with self.assertRaisesRegex(MODULE.HelperError, "cannot determine"):
                MODULE.service_loaded(self.fake.uid)

    def test_plist_metadata_churn_is_accepted_but_inode_replacement_is_rejected(self):
        self.plist.parent.mkdir(parents=True)
        self.plist.write_bytes(plistlib.dumps(MODULE.expected_plist()))
        original_lstat = Path.lstat
        count = 0

        def with_metadata_churn(path):
            nonlocal count
            result = original_lstat(path)
            if path == self.plist:
                count += 1
                if count == 2:
                    return SimpleNamespace(
                        st_dev=result.st_dev,
                        st_ino=result.st_ino,
                        st_mode=result.st_mode,
                        st_mtime_ns=result.st_mtime_ns + 1,
                    )
            return result

        with mock.patch.object(Path, "lstat", with_metadata_churn):
            self.assertEqual((True, "managed"), MODULE.managed_file(self.plist))

        real_fstat = os.fstat

        def replacement(fd):
            result = real_fstat(fd)
            return SimpleNamespace(st_dev=result.st_dev, st_ino=result.st_ino + 1)

        with mock.patch.object(MODULE.os, "fstat", replacement):
            owned, reason = MODULE.managed_file(self.plist)
        self.assertFalse(owned)
        self.assertIn("replaced during inspection", reason)

    def test_status_is_json_ready_for_current_gui_and_explains_state(self):
        result = MODULE.execute("status")
        self.assertTrue(result["ok"])
        self.assertEqual("not configured: managed plist missing; LaunchAgent not loaded; GUI environment NODE_USE_SYSTEM_CA is not 1", result["status"])
        self.assertFalse(result["configured"])
        self.assertFalse(result["plist_installed"])
        self.assertFalse(result["service_loaded"])
        self.assertIsNone(result["gui_environment_value"])


if __name__ == "__main__":
    unittest.main()
