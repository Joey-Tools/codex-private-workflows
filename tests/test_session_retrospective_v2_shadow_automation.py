from __future__ import annotations

import ast
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import hashlib
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import tomllib
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
AUTOMATIONS_ROOT = REPO_ROOT / "personal_codex" / "automations"
MANIFEST_PATH = REPO_ROOT / "personal_codex" / "private-sync-manifest.json"
PACKAGE_SCRIPT = REPO_ROOT / "scripts" / "build_personal_codex_package.py"
SHADOW_ID = "session-retrospective-v2-shadow"
SHADOW_RELATIVE_PATH = (
    "personal_codex/automations/session-retrospective-v2-shadow/automation.toml"
)
SHADOW_PATH = REPO_ROOT / SHADOW_RELATIVE_PATH
RUNNER_RELATIVE_PATH = (
    "personal_codex/skills/remote-host-context/scripts/"
    "session_retrospective_v2_shadow_runner.py"
)
RUNNER_PATH = REPO_ROOT / RUNNER_RELATIVE_PATH
STABLE_CWD = "/Users/hoteng/Program/GitHub/Joey-Tools/codex-workspace"
INSTALLED_V2_CLI = (
    "/Users/hoteng/.codex/skills/codex-session-retrospective/scripts/"
    "session_retrospective_v2.py"
)
PRESERVED_AUTOMATION_HASHES = {
    "daily-skill-friction": (
        "2e5ecf3b66e8d806ba36c4a8ac793d9151855c9768afa398127850eacc27785e"
    ),
    "daily-work-report-draft": (
        "42a5b3f1f03d70fb11b1d4912a673eb38d668a0029ac5fe29536044d7a13cc9a"
    ),
}
RETROSPECTIVE_TEMPLATE_IDS = (
    "daily-session-retrospective",
    "weekly-session-retrospective",
)

RUNNER_SPEC = importlib.util.spec_from_file_location(
    "session_retrospective_v2_shadow_runner_tests",
    RUNNER_PATH,
)
RUNNER = importlib.util.module_from_spec(RUNNER_SPEC)
assert RUNNER_SPEC is not None
assert RUNNER_SPEC.loader is not None
sys.modules[RUNNER_SPEC.name] = RUNNER
RUNNER_SPEC.loader.exec_module(RUNNER)


def load_automation(path: Path) -> dict[str, object]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def protocol_ref(prefix: str, label: str) -> str:
    return prefix + hashlib.sha256(label.encode("ascii")).hexdigest()


def source_status_result(
    invocation_dir: Path,
    *,
    host: str,
    lease_ref: str = "lease_ref_v2:source",
) -> dict[str, object]:
    identity_path = invocation_dir / "identity-v2.json"
    run_dir = invocation_dir / "run"
    output_path = invocation_dir / "source-transport.jsonl"
    host_ref = protocol_ref("host_ref_v2:", host)
    run_ref = protocol_ref("run_ref_v2:", "source-run")
    window = {
        "start": "2026-07-13T00:00:00Z",
        "end": "2026-07-14T00:00:00Z",
    }
    transport_command = [
        sys.executable,
        "-I",
        str(RUNNER.TRANSPORT_PATH),
        "session-shards",
        "--host",
        host,
        "--emit",
        "descriptors",
        "--rollout",
        "sessions/2026/07/14/rollout-shadow.jsonl",
    ]
    accept_command = [
        sys.executable,
        str(invocation_dir / "coordinator.py"),
        "accept-source",
        "--run-dir",
        str(run_dir),
        "--lease-ref",
        lease_ref,
        "--transport-stream-file",
        str(output_path),
        "--identity-path",
        str(identity_path),
        "--require-existing-identity",
    ]
    lease = {
        "authentication_tag": ("source_transport_lease_auth_v2:" + "a" * 64),
        "command_argv": transport_command,
        "cursor_time": None,
        "frame_byte_limit": 1024,
        "host": host,
        "host_ref": host_ref,
        "job_ref": protocol_ref("job_ref_v2:", f"{host}:job"),
        "lease_ref": lease_ref,
        "process_nonce": protocol_ref("process_nonce_v2:", f"{host}:nonce"),
        "record_limit": 100,
        "run_ref": run_ref,
        "schema": RUNNER.SOURCE_TRANSPORT_LEASE_SCHEMA,
        "session_selector_commitment": None,
        "session_target": None,
        "source_byte_limit": 1024,
        "source_cursor": None,
        "source_kind": "history",
        "transport_program_commitment": (
            "sha256:" + hashlib.sha256(RUNNER.TRANSPORT_PATH.read_bytes()).hexdigest()
        ),
        "window": window,
    }
    action = {
        "category": "source",
        "coordinator_cwd_contract": "run_directory",
        "host": host,
        "host_ref": host_ref,
        "job_kind": "source_catalog",
        "job_ref": lease["job_ref"],
        "lease_ref": lease_ref,
        "native_coordinator_actions": [
            {
                "action": "capture-source-transport",
                "command": transport_command,
                "stdout_path": str(output_path),
            },
            {"action": "accept-source", "command": accept_command},
        ],
        "native_subagent_instruction": (
            "Capture source_transport_command stdout at source_transport_output, "
            "then run the accept-source coordinator action."
        ),
        "source_contract": "bounded_metadata_jsonl_v2",
        "source_kind": "history",
        "source_transport_command": transport_command,
        "source_transport_output": str(output_path),
        "stage": "source_catalog",
        "status": "runnable",
        "transport_contract": RUNNER.SOURCE_TRANSPORT_LEASE_SCHEMA,
        "transport_lease": lease,
        "window": window,
    }
    return {
        "command": "status",
        "error": None,
        "exit_code": 0,
        "ok": True,
        "result": {
            "active_source_leases": [action],
            "checkpoint_revision": 1,
            "run_ref": run_ref,
            "schema_version": 2,
            "shadow": True,
        },
        "schema": RUNNER.CLI_RESULT_SCHEMA,
    }


def accept_source_arguments(
    invocation_dir: Path,
    *,
    lease_ref: str = "lease_ref_v2:source",
) -> list[str]:
    return [
        "accept-source",
        "--run-dir",
        str(invocation_dir / "run"),
        "--lease-ref",
        lease_ref,
        "--transport-stream-file",
        str(invocation_dir / "source-transport.jsonl"),
        "--identity-path",
        str(invocation_dir / "identity-v2.json"),
        "--require-existing-identity",
    ]


def successful_capture_executor(
    command: tuple[str, ...],
    output: object,
    _invocation_dir: Path,
    max_output_bytes: int,
) -> subprocess.CompletedProcess[bytes]:
    payload = b'{"kind":"test-transport"}\n'
    assert len(payload) < max_output_bytes
    output.write(payload)  # type: ignore[attr-defined]
    return subprocess.CompletedProcess(command, 0)


def daily_partial_status_fixture(
    *,
    transport: object,
    receipt: dict[str, object],
    coordinator_identity_key: bytes,
    partial_run_ref: str,
) -> tuple[dict[str, object], dict[str, object]]:
    host = str(receipt["host"])
    source_kind = str(receipt["source_kind"])
    host_refs = {
        name: protocol_ref("host_ref_v2:", name) for name in RUNNER.CANONICAL_HOSTS
    }
    coordinator_key_id = transport._session_shards_coordinator_identity_key_id(  # type: ignore[attr-defined]
        coordinator_identity_key
    )
    coverage_receipt = {
        "authentication_tag": protocol_ref("shadow_coverage_auth_v2:", partial_run_ref),
        "backfill_of": None,
        "checkpoint_revision": 7,
        "configuration_root": hashlib.sha256(b"configuration").hexdigest(),
        "controlled_gap_receipt_ref": receipt["holdout_ref"],
        "configured_host_refs": sorted(host_refs.values()),
        "covered_host_refs": sorted(
            value for name, value in host_refs.items() if name != host
        ),
        "export_bundle_digest": hashlib.sha256(b"partial-export").hexdigest(),
        "gap_host_refs": [host_refs[host]],
        "identity_key_id": coordinator_key_id,
        "mode": "daily",
        "model_era": "test-model",
        "partial": True,
        "policy_commitment": protocol_ref("shadow_policy_commitment_v2:", "partial"),
        "policy_era": "test-policy",
        "production_configuration_ref": protocol_ref(
            "configuration_ref_v2:", "production"
        ),
        "receipt_ref": protocol_ref("shadow_coverage_receipt_v2:", partial_run_ref),
        "run_ref": partial_run_ref,
        "schema": RUNNER.COORDINATOR_COVERAGE_SCHEMA,
        "source_evidence_commitment": protocol_ref(
            "shadow_source_evidence_v2:", "partial"
        ),
        "source_receipt_refs": [
            protocol_ref("source_transport_receipt_v2:", "partial")
        ],
        "source_snapshot_refs": [protocol_ref("source_snapshot_v2:", "partial")],
        "source_units": {
            "consumed_candidate": 1,
            "expected": 1,
            "explicit_gap": 0,
            "structurally_excluded": 0,
        },
        "specification_digest": hashlib.sha256(b"specification").hexdigest(),
        "version_commitment": protocol_ref("shadow_version_commitment_v2:", "version"),
        "window_end": receipt["window_end"],
        "window_start": receipt["window_start"],
    }
    held_cell = {
        "lease_ref": receipt["source_lease_ref"],
        "snapshot_ref": None,
        "status": "gap",
        "transport_receipt_ref": None,
    }
    hosts = {
        name: {
            "cells": {source_kind: held_cell} if name == host else {},
            "host_ref": host_refs[name],
            "status": "gap" if name == host else "complete",
        }
        for name in RUNNER.CANONICAL_HOSTS
    }
    status = {
        "command": "status",
        "error": None,
        "exit_code": 0,
        "ok": True,
        "result": {
            "accepted_source_manifests": [],
            "active_source_leases": [],
            "checkpoint_revision": 8,
            "coverage": {"hosts": hosts, "status": "partial"},
            "gaps": [
                {
                    "host": host,
                    "host_ref": host_refs[host],
                    "lease_ref": receipt["source_lease_ref"],
                    "reason": receipt["reason"],
                    "receipt_ref": receipt["holdout_ref"],
                    "source_kind": source_kind,
                }
            ],
            "identity_key_id": coordinator_key_id,
            "lineage": {"backfill_of": None},
            "mode": "daily",
            "publication": {"coverage_receipt": coverage_receipt},
            "run_ref": partial_run_ref,
            "schema_version": 2,
            "shadow": True,
            "stage": "export",
            "window": {
                "end": receipt["window_end"],
                "start": receipt["window_start"],
            },
        },
        "schema": RUNNER.CLI_RESULT_SCHEMA,
    }
    return status, coverage_receipt


class SessionRetrospectiveV2ShadowAutomationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.shadow = load_automation(SHADOW_PATH)
        cls.prompt = str(cls.shadow["prompt"])
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    def test_identity_schedule_and_runtime_convention_are_unique(self) -> None:
        automation_paths = sorted(AUTOMATIONS_ROOT.glob("*/automation.toml"))
        automations = [(path, load_automation(path)) for path in automation_paths]
        ids = [str(automation["id"]) for _path, automation in automations]

        self.assertEqual(1, Counter(ids)[SHADOW_ID])
        self.assertTrue(all(count == 1 for count in Counter(ids).values()))
        other_schedules = {
            str(automation["rrule"])
            for path, automation in automations
            if path != SHADOW_PATH
        }
        self.assertNotIn(self.shadow["rrule"], other_schedules)
        self.assertEqual(1, self.shadow["version"])
        self.assertEqual("cron", self.shadow["kind"])
        self.assertEqual("Session Retrospective v2 Shadow", self.shadow["name"])
        self.assertEqual("ACTIVE", self.shadow["status"])
        self.assertEqual(
            "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU;BYHOUR=5;BYMINUTE=10",
            self.shadow["rrule"],
        )
        self.assertEqual("gpt-5.6-sol", self.shadow["model"])
        self.assertEqual("xhigh", self.shadow["reasoning_effort"])
        self.assertEqual("worktree", self.shadow["execution_environment"])
        self.assertEqual([STABLE_CWD], self.shadow["cwds"])

    def test_unrelated_production_automation_templates_are_unchanged(self) -> None:
        for automation_id, expected_hash in PRESERVED_AUTOMATION_HASHES.items():
            with self.subTest(automation_id=automation_id):
                path = AUTOMATIONS_ROOT / automation_id / "automation.toml"
                actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(expected_hash, actual_hash)

    def test_retrospective_templates_define_reference_only_cutover(self) -> None:
        linked_sources = {entry["source"] for entry in self.manifest["links"]}
        linked_targets = {entry["target"] for entry in self.manifest["links"]}
        for automation_id in RETROSPECTIVE_TEMPLATE_IDS:
            with self.subTest(automation_id=automation_id):
                relative_path = (
                    f"personal_codex/automations/{automation_id}/automation.toml"
                )
                path = REPO_ROOT / relative_path
                raw_template = path.read_text(encoding="utf-8")
                automation = load_automation(path)
                prompt = str(automation["prompt"])
                live_path = f"~/.codex/automations/{automation_id}/automation.toml"

                self.assertEqual(automation_id, automation["id"])
                self.assertEqual(
                    1,
                    self.manifest["reference_only"].count(relative_path),
                )
                self.assertNotIn(relative_path, linked_sources)
                self.assertNotIn(relative_path, linked_targets)
                self.assertIn("Reference-only release template", raw_template)
                self.assertIn("not evidence", raw_template)
                self.assertIn(live_path, raw_template)
                self.assertIn("exact first registration", raw_template)
                self.assertIn("verified in-place update", raw_template)
                self.assertIn(
                    "path/type/parse/ID mismatch blocks cutover", raw_template
                )
                self.assertIn(
                    INSTALLED_V2_CLI,
                    prompt,
                )
                self.assertIn("suppress a production source", prompt)
                self.assertNotIn("scripts/session_retrospective.py", prompt)
                self.assertIn(
                    "Do not use the v1 entry point, a source-checkout fallback",
                    prompt,
                )

    def test_manifest_keeps_shadow_automation_reference_only(self) -> None:
        self.assertEqual(
            1,
            self.manifest["reference_only"].count(SHADOW_RELATIVE_PATH),
        )
        linked_sources = {entry["source"] for entry in self.manifest["links"]}
        linked_targets = {entry["target"] for entry in self.manifest["links"]}
        self.assertNotIn(SHADOW_RELATIVE_PATH, linked_sources)
        self.assertNotIn(SHADOW_RELATIVE_PATH, linked_targets)

    def test_private_package_contains_reference_only_shadow_toml(self) -> None:
        package_sha = "a" * 40
        with tempfile.TemporaryDirectory(prefix="shadow-automation-package.") as raw:
            output_dir = Path(raw)
            subprocess.run(
                [
                    sys.executable,
                    str(PACKAGE_SCRIPT),
                    "--repo-root",
                    str(REPO_ROOT),
                    "--manifest",
                    "personal_codex/private-sync-manifest.json",
                    "--sha",
                    package_sha,
                    "--output-dir",
                    str(output_dir),
                ],
                check=True,
                text=True,
                capture_output=True,
            )
            archive_path = output_dir / f"personal-codex-{package_sha}.tar.gz"
            with tarfile.open(archive_path, "r:gz") as archive:
                shadow_member = archive.getmember(
                    f"personal-codex-{package_sha}/{SHADOW_RELATIVE_PATH}"
                )
                shadow_file = archive.extractfile(shadow_member)
                self.assertIsNotNone(shadow_file)
                packaged_shadow = tomllib.loads(
                    shadow_file.read().decode("utf-8")  # type: ignore[union-attr]
                )
                packaged_templates = {}
                for automation_id in RETROSPECTIVE_TEMPLATE_IDS:
                    relative_path = (
                        f"personal_codex/automations/{automation_id}/automation.toml"
                    )
                    member = archive.getmember(
                        f"personal-codex-{package_sha}/{relative_path}"
                    )
                    template_file = archive.extractfile(member)
                    self.assertIsNotNone(template_file)
                    packaged_templates[automation_id] = tomllib.loads(
                        template_file.read().decode(  # type: ignore[union-attr]
                            "utf-8"
                        )
                    )
                runner_member = archive.getmember(
                    f"personal-codex-{package_sha}/{RUNNER_RELATIVE_PATH}"
                )
                self.assertEqual(0o755, runner_member.mode)
                manifest_member = archive.getmember(
                    f"personal-codex-{package_sha}/personal_codex/sync-manifest.json"
                )
                manifest_file = archive.extractfile(manifest_member)
                self.assertIsNotNone(manifest_file)
                packaged_manifest = json.loads(
                    manifest_file.read().decode("utf-8")  # type: ignore[union-attr]
                )

        self.assertEqual(SHADOW_ID, packaged_shadow["id"])
        self.assertEqual(
            set(RETROSPECTIVE_TEMPLATE_IDS),
            {
                automation_id
                for automation_id, template in packaged_templates.items()
                if template["id"] == automation_id
            },
        )
        self.assertIn(SHADOW_RELATIVE_PATH, packaged_manifest["reference_only"])
        self.assertNotIn(
            SHADOW_RELATIVE_PATH,
            {entry["source"] for entry in packaged_manifest["links"]},
        )

    def test_runner_rejects_unsafe_actions_before_executor_invocation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="shadow-runner-policy.") as raw:
            shadow_root = Path(raw) / "shadow"
            requested_invocation = shadow_root / "invocation"
            invocation_dir, _ = RUNNER._prepare_invocation_directory(
                requested_invocation,
                shadow_root=shadow_root,
            )
            outside = Path(raw) / "outside"
            status = [
                "status",
                "--identity-path",
                str(invocation_dir / "identity-v2.json"),
                "--require-existing-identity",
                "--run-dir",
                str(invocation_dir / "run"),
            ]
            cases = (
                (["publish"], "not allowlisted"),
                ([*status, "--provider-state", str(outside)], "not allowed"),
                ([*status, "--output", str(outside)], "not allowed"),
                ([*status, "--run-d", str(invocation_dir / "run")], "not allowed"),
                ([*status, "-r", str(invocation_dir / "run")], "alias"),
                ([*status, "--claim-ref=value"], "inline option"),
                ([*status, "--run-dir", str(invocation_dir / "other")], "duplicate"),
                ([*status, "positional"], "positional"),
                ([*status, "--claim-ttl-seconds", "nan"], "positive decimal"),
            )
            for arguments, expected_error in cases:
                with self.subTest(arguments=arguments):
                    with self.assertRaisesRegex(
                        RUNNER.ShadowPolicyError,
                        expected_error,
                    ):
                        RUNNER.validate_coordinator_command(
                            arguments,
                            invocation_dir=invocation_dir,
                            host=None,
                        )

            inside_history = invocation_dir / "simulation-history"
            inside_history.mkdir(mode=0o700)
            RUNNER.validate_coordinator_command(
                [
                    "finalize",
                    "--identity-path",
                    str(invocation_dir / "identity-v2.json"),
                    "--require-existing-identity",
                    "--run-dir",
                    str(invocation_dir / "run"),
                    "--history-repo",
                    str(inside_history),
                    "--history-target-ref",
                    RUNNER.SHADOW_HISTORY_TARGET_REF,
                    "--phase",
                    "commit",
                    "--shadow",
                ],
                invocation_dir=invocation_dir,
                host=None,
            )

            with self.assertRaises(SystemExit):
                RUNNER.build_parser().parse_args(
                    ["run", "--invocation-d", str(invocation_dir)]
                )

    def test_runner_help_and_identity_bootstrap_enable_a_fresh_doctor(self) -> None:
        with tempfile.TemporaryDirectory(prefix="shadow-runner-bootstrap.") as raw:
            shadow_root = Path(raw) / "shadow"
            invocation_dir, _ = RUNNER._prepare_invocation_directory(
                shadow_root / "invocation",
                shadow_root=shadow_root,
            )
            identity_path = invocation_dir / "identity-v2.json"
            simulation_history = invocation_dir / "simulation-history"
            simulation_history.mkdir(mode=0o700)
            observed: list[str] = []

            def executor(
                _coordinator: Path,
                arguments: tuple[str, ...],
                _invocation_dir: Path,
            ) -> subprocess.CompletedProcess[str]:
                observed.append(arguments[0])
                if arguments[0] == "identity":
                    identity_path.write_text("{}", encoding="utf-8")
                    identity_path.chmod(0o600)
                return subprocess.CompletedProcess(arguments, 0)

            help_result = RUNNER.run_guarded_coordinator(
                ("help",),
                invocation_dir=invocation_dir,
                shadow_root=shadow_root,
                coordinator_path=Path(sys.executable),
                executor=executor,
            )
            identity_result = RUNNER.run_guarded_coordinator(
                (
                    "identity",
                    "--create-identity",
                    "--identity-path",
                    str(identity_path),
                    "--shadow",
                ),
                invocation_dir=invocation_dir,
                shadow_root=shadow_root,
                coordinator_path=Path(sys.executable),
                executor=executor,
            )
            doctor_result = RUNNER.run_guarded_coordinator(
                (
                    "doctor",
                    "--identity-path",
                    str(identity_path),
                    "--require-existing-identity",
                    "--history-repo",
                    str(simulation_history),
                    "--history-target-ref",
                    RUNNER.SHADOW_HISTORY_TARGET_REF,
                    "--run-config",
                    str(invocation_dir / "run-config.json"),
                    "--shadow",
                ),
                invocation_dir=invocation_dir,
                shadow_root=shadow_root,
                coordinator_path=Path(sys.executable),
                executor=executor,
            )
            capture_environment = RUNNER._source_capture_environment(invocation_dir)
            with self.assertRaisesRegex(
                RUNNER.ShadowPolicyError,
                "identity already exists",
            ):
                RUNNER.validate_coordinator_command(
                    (
                        "identity",
                        "--create-identity",
                        "--identity-path",
                        str(identity_path),
                        "--shadow",
                    ),
                    invocation_dir=invocation_dir,
                    host=None,
                )
            identity_mode = identity_path.stat().st_mode & 0o777
            help_text = RUNNER.build_parser().format_help()

        self.assertEqual(0, help_result.returncode)
        self.assertEqual(0, identity_result.returncode)
        self.assertEqual(0, doctor_result.returncode)
        self.assertEqual(["help", "identity", "doctor"], observed)
        self.assertEqual(0o600, identity_mode)
        self.assertEqual(
            str(invocation_dir),
            capture_environment["CODEX_SESSION_SHARDS_SHADOW_ROOT"],
        )
        self.assertIn(INSTALLED_V2_CLI, help_text)

    def test_runner_requires_the_latest_closed_weekly_window(self) -> None:
        now_utc = dt.datetime(2026, 7, 16, 12, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory(prefix="shadow-runner-weekly-window.") as raw:
            shadow_root = Path(raw) / "shadow"
            invocation_dir, _ = RUNNER._prepare_invocation_directory(
                shadow_root / "invocation",
                shadow_root=shadow_root,
            )
            simulation_history = invocation_dir / "simulation-history"
            simulation_history.mkdir(mode=0o700)

            def arguments(start: str, end: str) -> list[str]:
                result = [
                    "start",
                    "--identity-path",
                    str(invocation_dir / "identity-v2.json"),
                    "--require-existing-identity",
                    "--history-repo",
                    str(simulation_history),
                    "--history-target-ref",
                    RUNNER.SHADOW_HISTORY_TARGET_REF,
                    "--run-config",
                    str(invocation_dir / "run-config.json"),
                    "--run-dir",
                    str(invocation_dir / "weekly-run"),
                    "--mode",
                    "weekly",
                    "--start",
                    start,
                    "--end",
                    end,
                ]
                for host in RUNNER.CANONICAL_HOST_ORDER:
                    result.extend(("--host", host))
                result.append("--shadow")
                return result

            self.assertEqual(
                ("start", None),
                RUNNER.validate_coordinator_command(
                    arguments(
                        "2026-07-09T00:00:00Z",
                        "2026-07-16T00:00:00Z",
                    ),
                    host=None,
                    invocation_dir=invocation_dir,
                    now_utc=now_utc,
                ),
            )
            for start, end in (
                ("2026-07-02T00:00:00Z", "2026-07-09T00:00:00Z"),
                ("2026-07-10T00:00:00Z", "2026-07-17T00:00:00Z"),
            ):
                with self.subTest(start=start, end=end):
                    with self.assertRaisesRegex(
                        RUNNER.ShadowPolicyError,
                        "latest closed UTC seven-day window",
                    ):
                        RUNNER.validate_coordinator_command(
                            arguments(start, end),
                            host=None,
                            invocation_dir=invocation_dir,
                            now_utc=now_utc,
                        )

    def test_runner_rejects_simulation_history_writes_after_execution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="shadow-runner-history-write.") as raw:
            shadow_root = Path(raw) / "shadow"
            invocation_dir, _ = RUNNER._prepare_invocation_directory(
                shadow_root / "invocation",
                shadow_root=shadow_root,
            )
            simulation_history = invocation_dir / "simulation-history"
            simulation_history.mkdir(mode=0o700)
            marker = simulation_history / "unexpected"

            def executor(
                _coordinator: Path,
                arguments: tuple[str, ...],
                _invocation_dir: Path,
            ) -> subprocess.CompletedProcess[str]:
                marker.write_text("not allowed", encoding="ascii")
                return subprocess.CompletedProcess(arguments, 0)

            with self.assertRaisesRegex(
                RUNNER.ShadowPolicyError,
                "simulation history must remain empty",
            ):
                RUNNER.run_guarded_coordinator(
                    ("help",),
                    invocation_dir=invocation_dir,
                    shadow_root=shadow_root,
                    coordinator_path=Path(sys.executable),
                    executor=executor,
                )

            self.assertTrue(marker.is_file())

    def test_runner_rejects_simulation_history_writes_from_source_status(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="shadow-runner-status-write.") as raw:
            shadow_root = Path(raw) / "shadow"
            invocation_dir, _ = RUNNER._prepare_invocation_directory(
                shadow_root / "invocation",
                shadow_root=shadow_root,
            )
            simulation_history = invocation_dir / "simulation-history"
            simulation_history.mkdir(mode=0o700)
            marker = simulation_history / "unexpected"
            arguments = accept_source_arguments(invocation_dir)
            status = source_status_result(invocation_dir, host="miku-bot-dev")

            def status_query(
                _coordinator: Path,
                _arguments: tuple[str, ...],
                _invocation_dir: Path,
            ) -> dict[str, object]:
                marker.write_text("not allowed", encoding="ascii")
                return status

            with self.assertRaisesRegex(
                RUNNER.ShadowPolicyError,
                "simulation history must remain empty",
            ):
                RUNNER.run_guarded_coordinator(
                    arguments,
                    invocation_dir=invocation_dir,
                    host="miku-bot-dev",
                    shadow_root=shadow_root,
                    coordinator_path=Path(sys.executable),
                    executor=lambda *_args: self.fail(
                        "source acceptance must not follow a dirty status"
                    ),
                    capture_executor=lambda *_args: self.fail(
                        "source capture must not follow a dirty status"
                    ),
                    status_query=status_query,
                )

            self.assertTrue(marker.is_file())

    def test_status_for_run_rejects_simulation_history_writes(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="shadow-runner-internal-status-write."
        ) as raw:
            shadow_root = Path(raw) / "shadow"
            invocation_dir, _ = RUNNER._prepare_invocation_directory(
                shadow_root / "invocation",
                shadow_root=shadow_root,
            )
            simulation_history = invocation_dir / "simulation-history"
            simulation_history.mkdir(mode=0o700)
            marker = simulation_history / "unexpected"

            def status_query(
                _coordinator: Path,
                _arguments: tuple[str, ...],
                _invocation_dir: Path,
            ) -> dict[str, object]:
                marker.write_text("not allowed", encoding="ascii")
                return {}

            with self.assertRaisesRegex(
                RUNNER.ShadowPolicyError,
                "simulation history must remain empty",
            ):
                RUNNER._status_for_run(
                    coordinator_path=Path(sys.executable),
                    coordinator_identity_path=invocation_dir / "identity-v2.json",
                    run_dir=invocation_dir / "run",
                    invocation_dir=invocation_dir,
                    status_query=status_query,
                )

            self.assertTrue(marker.is_file())

    def test_runner_starts_verified_daily_partial_successor_pair(self) -> None:
        now_utc = dt.datetime(2026, 7, 15, 12, tzinfo=dt.timezone.utc)
        window_start = "2026-07-14T00:00:00Z"
        window_end = "2026-07-15T00:00:00Z"
        with tempfile.TemporaryDirectory(prefix="shadow-runner-daily-pair.") as raw:
            shadow_root = Path(raw) / "shadow"
            invocation_dir, _ = RUNNER._prepare_invocation_directory(
                shadow_root / "invocation",
                shadow_root=shadow_root,
            )
            coordinator_identity_path = invocation_dir / "identity-v2.json"
            run_config = invocation_dir / "run-config.json"
            for path in (coordinator_identity_path, run_config):
                path.write_text("{}", encoding="ascii")
                path.chmod(0o600)
            transport = RUNNER._load_transport_module()
            holdout_identity_path = invocation_dir / "holdout-identity"
            holdout_identity_key = transport._create_session_shards_shadow_identity(
                holdout_identity_path
            )
            partial_run_dir = invocation_dir / "partial-run"
            backfill_run_dir = invocation_dir / "backfill-run"
            observed: list[tuple[str, ...]] = []

            def executor(
                _coordinator: Path,
                arguments: tuple[str, ...],
                _invocation_dir: Path,
            ) -> subprocess.CompletedProcess[str]:
                observed.append(tuple(arguments))
                run_dir = Path(arguments[arguments.index("--run-dir") + 1])
                run_dir.mkdir(mode=0o700)
                return subprocess.CompletedProcess(arguments, 0)

            partial_result = RUNNER.start_daily_shadow_pair(
                invocation_dir=invocation_dir,
                holdout_host="hoteng-srv-01",
                holdout_identity_path=holdout_identity_path,
                coordinator_identity_path=coordinator_identity_path,
                run_config=run_config,
                partial_run_dir=partial_run_dir,
                backfill_run_dir=backfill_run_dir,
                window_start=window_start,
                window_end=window_end,
                shadow_root=shadow_root,
                coordinator_path=Path(sys.executable),
                transport_module=transport,
                executor=executor,
                now_utc=now_utc,
            )
            manifest_path = invocation_dir / RUNNER.DAILY_PAIR_FILENAME
            partial_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            partial_arguments = observed[0]
            partial_hosts = [
                partial_arguments[index + 1]
                for index, value in enumerate(partial_arguments)
                if value == "--host"
            ]

            self.assertEqual(0, partial_result.returncode)
            self.assertEqual(set(RUNNER.CANONICAL_HOSTS), set(partial_hosts))
            self.assertEqual(len(RUNNER.CANONICAL_HOSTS), len(partial_hosts))
            self.assertIn("--allow-partial", partial_arguments)
            self.assertIn("--shadow", partial_arguments)
            self.assertNotIn("--holdout-host", partial_arguments)
            self.assertNotIn("--backfill-of", partial_arguments)
            self.assertEqual("partial_started", partial_manifest["state"])
            self.assertFalse(partial_manifest["production_source_suppressed"])
            self.assertEqual(
                RUNNER.DAILY_PAIR_HOLDOUT_MECHANISM,
                partial_manifest["holdout_mechanism"],
            )
            self.assertEqual(
                transport._session_shards_holdout_identity_key_id(holdout_identity_key),
                partial_manifest["holdout_identity_key_id"],
            )
            self.assertEqual(0o600, manifest_path.stat().st_mode & 0o777)

            receipt = transport._session_shards_holdout_receipt(
                identity_key=holdout_identity_key,
                host="hoteng-srv-01",
                window_start=window_start,
                window_end=window_end,
                source_kind="codex_session_history",
                source_lease_ref="source-lease:daily-pair:partial",
                now_utc=now_utc,
            )
            receipt_path = invocation_dir / "holdout-receipt.json"
            receipt_path.write_text(
                json.dumps(receipt, separators=(",", ":"), sort_keys=True),
                encoding="utf-8",
            )
            receipt_path.chmod(0o600)
            partial_run_ref = protocol_ref("run_ref_v2:", "daily-pair-partial")

            key_path = (
                holdout_identity_path
                / transport.SESSION_SHARDS_HOLDOUT_IDENTITY_KEY_FILE
            )
            key_path.unlink()
            holdout_identity_path.rmdir()
            replacement_identity_key = transport._create_session_shards_shadow_identity(
                holdout_identity_path
            )
            replacement_receipt = transport._session_shards_holdout_receipt(
                identity_key=replacement_identity_key,
                host="hoteng-srv-01",
                window_start=window_start,
                window_end=window_end,
                source_kind="codex_session_history",
                source_lease_ref="source-lease:daily-pair:partial",
                now_utc=now_utc,
            )
            replacement_receipt_path = invocation_dir / "replacement-receipt.json"
            replacement_receipt_path.write_text(
                json.dumps(
                    replacement_receipt,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            replacement_receipt_path.chmod(0o600)
            with self.assertRaisesRegex(
                RUNNER.ShadowPolicyError,
                "holdout identity changed",
            ):
                RUNNER.start_daily_shadow_pair_successor(
                    invocation_dir=invocation_dir,
                    receipt_path=replacement_receipt_path,
                    holdout_identity_path=holdout_identity_path,
                    coordinator_identity_path=coordinator_identity_path,
                    partial_run_ref=partial_run_ref,
                    shadow_root=shadow_root,
                    coordinator_path=Path(sys.executable),
                    transport_module=transport,
                    executor=executor,
                    status_query=lambda *_args: self.fail(
                        "replacement identity must fail before status"
                    ),
                    now_utc=now_utc,
                )
            self.assertFalse(backfill_run_dir.exists())
            self.assertEqual(1, len(observed))

            key_path.unlink()
            holdout_identity_path.rmdir()
            holdout_identity_path.mkdir(mode=0o700)
            key_path.write_bytes(holdout_identity_key)
            key_path.chmod(0o600)
            status, coverage_receipt = daily_partial_status_fixture(
                transport=transport,
                receipt=receipt,
                coordinator_identity_key=b"c" * 32,
                partial_run_ref=partial_run_ref,
            )
            current_status = json.loads(json.dumps(status))

            def status_query(
                _coordinator: Path,
                _arguments: tuple[str, ...],
                _invocation_dir: Path,
            ) -> dict[str, object]:
                return current_status

            def verify_coverage(
                _coordinator: Path,
                _identity: Path,
                value: dict[str, object],
                _invocation_dir: Path,
            ) -> dict[str, object]:
                self.assertEqual(coverage_receipt, value)
                return dict(value)

            del current_status["result"]["coverage"]["hosts"]["local"]
            with self.assertRaisesRegex(
                RUNNER.ShadowPolicyError,
                "terminal partial predecessor",
            ):
                RUNNER.start_daily_shadow_pair_successor(
                    invocation_dir=invocation_dir,
                    receipt_path=receipt_path,
                    holdout_identity_path=holdout_identity_path,
                    coordinator_identity_path=coordinator_identity_path,
                    partial_run_ref=partial_run_ref,
                    shadow_root=shadow_root,
                    coordinator_path=Path(sys.executable),
                    transport_module=transport,
                    executor=executor,
                    status_query=status_query,
                    coverage_verifier=verify_coverage,
                    now_utc=now_utc,
                )
            self.assertFalse(backfill_run_dir.exists())
            self.assertEqual(1, len(observed))
            self.assertEqual(
                "partial_started",
                json.loads(manifest_path.read_text(encoding="utf-8"))["state"],
            )

            current_status = json.loads(json.dumps(status))
            backfill_result = RUNNER.start_daily_shadow_pair_successor(
                invocation_dir=invocation_dir,
                receipt_path=receipt_path,
                holdout_identity_path=holdout_identity_path,
                coordinator_identity_path=coordinator_identity_path,
                partial_run_ref=partial_run_ref,
                shadow_root=shadow_root,
                coordinator_path=Path(sys.executable),
                transport_module=transport,
                executor=executor,
                status_query=status_query,
                coverage_verifier=verify_coverage,
                now_utc=now_utc,
            )
            backfill_arguments = observed[1]
            backfill_hosts = [
                backfill_arguments[index + 1]
                for index, value in enumerate(backfill_arguments)
                if value == "--host"
            ]
            final_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(0, backfill_result.returncode)
            self.assertEqual(["hoteng-srv-01"], backfill_hosts)
            self.assertNotIn("--allow-partial", backfill_arguments)
            self.assertIn("--shadow", backfill_arguments)
            self.assertEqual(
                partial_run_ref,
                backfill_arguments[backfill_arguments.index("--backfill-of") + 1],
            )
            self.assertEqual(
                str(receipt_path),
                backfill_arguments[
                    backfill_arguments.index("--controlled-gap-receipt") + 1
                ],
            )
            self.assertEqual("backfill_started", final_manifest["state"])
            self.assertEqual(partial_run_ref, final_manifest["partial_run_ref"])
            self.assertEqual(
                receipt["holdout_ref"],
                final_manifest["controlled_gap_receipt_ref"],
            )
            self.assertFalse(final_manifest["production_source_suppressed"])

            with self.assertRaisesRegex(
                RUNNER.ShadowPolicyError,
                "requires one started partial",
            ):
                RUNNER.start_daily_shadow_pair_successor(
                    invocation_dir=invocation_dir,
                    receipt_path=receipt_path,
                    holdout_identity_path=holdout_identity_path,
                    coordinator_identity_path=coordinator_identity_path,
                    partial_run_ref=partial_run_ref,
                    shadow_root=shadow_root,
                    coordinator_path=Path(sys.executable),
                    transport_module=transport,
                    executor=executor,
                    status_query=status_query,
                    coverage_verifier=verify_coverage,
                    now_utc=now_utc,
                )
            self.assertEqual(2, len(observed))

    @unittest.skipUnless(
        sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file(),
        "requires the macOS sandbox used by the automation host",
    )
    def test_runner_sandbox_blocks_outside_read_write_and_network(self) -> None:
        sandbox_probe = subprocess.run(
            [
                "/usr/bin/sandbox-exec",
                "-p",
                "(version 1)\n(allow default)",
                "/usr/bin/true",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if sandbox_probe.returncode != 0:
            self.skipTest("the enclosing test sandbox blocks nested sandbox-exec")
        with tempfile.TemporaryDirectory(prefix="shadow-runner-sandbox.") as raw:
            root = Path(raw)
            shadow_root = root / "shadow"
            invocation_dir, _ = RUNNER._prepare_invocation_directory(
                shadow_root / "invocation",
                shadow_root=shadow_root,
            )
            outside = root / "retained-production-state.json"
            outside.write_text("production-secret", encoding="utf-8")
            outside_write = root / "provider-state.json"
            result_path = invocation_dir / "sandbox-result"
            coordinator_source = f"""\
from pathlib import Path
import socket

failures = []
try:
    Path({str(outside)!r}).read_text(encoding="utf-8")
    failures.append("read")
except OSError:
    pass
try:
    Path({str(outside_write)!r}).write_text("published", encoding="utf-8")
    failures.append("write")
except OSError:
    pass
try:
    socket.create_connection(("127.0.0.1", 9), timeout=0.1)
    failures.append("network")
except OSError:
    pass
Path({str(result_path)!r}).write_text(",".join(failures), encoding="utf-8")
raise SystemExit(0 if not failures else 74)
"""
            coordinator = root / "coordinator.py"
            coordinator.write_text(coordinator_source, encoding="utf-8")
            denied = RUNNER._run_sandboxed(
                coordinator_path=coordinator,
                arguments=("status",),
                invocation_dir=invocation_dir,
                capture_output=True,
            )
            self.assertEqual(0, denied.returncode, denied.stderr)
            self.assertTrue(result_path.is_file(), denied.stderr)
            result_text = result_path.read_text(encoding="utf-8")

        self.assertEqual("", result_text)
        self.assertFalse(outside_write.exists())

    def test_runner_uses_python_39_compatible_process_group_creation(self) -> None:
        module = ast.parse(RUNNER_PATH.read_text(encoding="utf-8"))
        popen_calls = [
            node
            for node in ast.walk(module)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "Popen"
        ]

        self.assertEqual(2, len(popen_calls))
        self.assertFalse(
            any(
                keyword.arg == "process_group"
                for call in popen_calls
                for keyword in call.keywords
            )
        )
        for call in popen_calls:
            start_new_session = next(
                (
                    keyword.value
                    for keyword in call.keywords
                    if keyword.arg == "start_new_session"
                ),
                None,
            )
            self.assertIsInstance(start_new_session, ast.Constant)
            self.assertIs(True, start_new_session.value)

    def test_runner_supervisor_times_out_and_cleans_process_group(self) -> None:
        with tempfile.TemporaryDirectory(prefix="shadow-runner-timeout.") as raw:
            root = Path(raw)
            pid_path = root / "coordinator.pid"
            script = (
                "import os, pathlib, time; "
                f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpgrp())); "
                "time.sleep(60)"
            )
            started = time.monotonic()
            with self.assertRaisesRegex(
                RUNNER.ShadowPolicyError,
                "coordinator action timed out",
            ):
                RUNNER._run_supervised_process(
                    (sys.executable, "-c", script),
                    cwd=root,
                    environment=os.environ,
                    capture_output=False,
                    timeout_seconds=0.2,
                )
            if pid_path.exists():
                process_group = int(pid_path.read_text(encoding="utf-8"))
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    try:
                        os.killpg(process_group, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.05)
                else:
                    self.fail("timed-out coordinator process group was retained")

        self.assertLess(time.monotonic() - started, 5)

    def test_source_capture_uses_output_idle_timeout(self) -> None:
        class ProgressProcess:
            def __init__(self, output: object, writes: int) -> None:
                self.output = output
                self.writes = writes
                self.polls = 0
                self.returncode: int | None = None

            def poll(self) -> int | None:
                if self.returncode is not None:
                    return self.returncode
                if self.polls < self.writes:
                    self.output.write(b"x")  # type: ignore[attr-defined]
                    self.output.flush()  # type: ignore[attr-defined]
                    self.polls += 1
                    return None
                self.returncode = 0
                return self.returncode

        class StalledProcess:
            returncode: int | None = None

            @staticmethod
            def poll() -> None:
                return None

        class SidecarProgressProcess:
            def __init__(self, progress_path: Path, writes: int) -> None:
                self.progress_path = progress_path
                self.writes = writes
                self.polls = 0
                self.returncode: int | None = None

            def poll(self) -> int | None:
                if self.returncode is not None:
                    return self.returncode
                if self.polls < self.writes:
                    with self.progress_path.open("ab", buffering=0) as progress:
                        progress.write(b".")
                    self.polls += 1
                    return None
                self.returncode = 0
                return self.returncode

        with tempfile.TemporaryDirectory(prefix="shadow-capture-idle.") as raw:
            root = Path(raw)
            with tempfile.NamedTemporaryFile(dir=root) as output:
                progressing = ProgressProcess(output, writes=6)
                started = time.monotonic()
                with (
                    mock.patch.object(RUNNER.sys, "platform", "darwin"),
                    mock.patch.object(
                        RUNNER,
                        "SANDBOX_EXEC_PATH",
                        Path(sys.executable),
                    ),
                    mock.patch.object(
                        RUNNER.subprocess,
                        "Popen",
                        return_value=progressing,
                    ),
                    mock.patch.object(
                        RUNNER,
                        "SOURCE_CAPTURE_IDLE_TIMEOUT_SECONDS",
                        0.04,
                    ),
                    mock.patch.object(RUNNER, "SOURCE_CAPTURE_POLL_SECONDS", 0.01),
                    mock.patch.object(
                        RUNNER, "_process_group_exists", return_value=False
                    ),
                    mock.patch.object(RUNNER, "_terminate_process_group"),
                ):
                    result = RUNNER._sandboxed_capture_executor(
                        (sys.executable, "-I", str(RUNNER.TRANSPORT_PATH)),
                        output,
                        root,
                        1024,
                    )
                self.assertGreater(time.monotonic() - started, 0.04)
                self.assertEqual(0, result.returncode)

            with tempfile.NamedTemporaryFile(dir=root) as output:
                progress_paths: list[Path] = []

                def sidecar_progress_popen(
                    _command: object,
                    **kwargs: object,
                ) -> SidecarProgressProcess:
                    environment = kwargs["env"]
                    assert isinstance(environment, dict)
                    progress_path = Path(
                        environment[RUNNER.SOURCE_CAPTURE_PROGRESS_PATH_ENV]
                    )
                    progress_paths.append(progress_path)
                    return SidecarProgressProcess(progress_path, writes=6)

                started = time.monotonic()
                with (
                    mock.patch.object(RUNNER.sys, "platform", "darwin"),
                    mock.patch.object(
                        RUNNER,
                        "SANDBOX_EXEC_PATH",
                        Path(sys.executable),
                    ),
                    mock.patch.object(
                        RUNNER.subprocess,
                        "Popen",
                        side_effect=sidecar_progress_popen,
                    ),
                    mock.patch.object(
                        RUNNER,
                        "SOURCE_CAPTURE_IDLE_TIMEOUT_SECONDS",
                        0.04,
                    ),
                    mock.patch.object(RUNNER, "SOURCE_CAPTURE_POLL_SECONDS", 0.01),
                    mock.patch.object(
                        RUNNER, "_process_group_exists", return_value=False
                    ),
                    mock.patch.object(RUNNER, "_terminate_process_group"),
                ):
                    result = RUNNER._sandboxed_capture_executor(
                        (sys.executable, "-I", str(RUNNER.TRANSPORT_PATH)),
                        output,
                        root,
                        1024,
                    )
                self.assertGreater(time.monotonic() - started, 0.04)
                self.assertEqual(0, result.returncode)
                self.assertEqual(1, len(progress_paths))
                self.assertFalse(progress_paths[0].exists())

            with tempfile.NamedTemporaryFile(dir=root) as output:
                progressing_forever = ProgressProcess(output, writes=10_000)
                with (
                    mock.patch.object(RUNNER.sys, "platform", "darwin"),
                    mock.patch.object(
                        RUNNER,
                        "SANDBOX_EXEC_PATH",
                        Path(sys.executable),
                    ),
                    mock.patch.object(
                        RUNNER.subprocess,
                        "Popen",
                        return_value=progressing_forever,
                    ),
                    mock.patch.object(
                        RUNNER,
                        "SOURCE_CAPTURE_IDLE_TIMEOUT_SECONDS",
                        0.04,
                    ),
                    mock.patch.object(
                        RUNNER,
                        "_source_capture_wall_timeout_seconds",
                        return_value=0.05,
                    ),
                    mock.patch.object(RUNNER, "SOURCE_CAPTURE_POLL_SECONDS", 0.01),
                    mock.patch.object(RUNNER, "_terminate_process_group"),
                ):
                    with self.assertRaisesRegex(
                        RUNNER.ShadowPolicyError,
                        "wall-clock timed out",
                    ):
                        RUNNER._sandboxed_capture_executor(
                            (sys.executable, "-I", str(RUNNER.TRANSPORT_PATH)),
                            output,
                            root,
                            1024,
                        )

            with tempfile.NamedTemporaryFile(dir=root) as output:
                with (
                    mock.patch.object(RUNNER.sys, "platform", "darwin"),
                    mock.patch.object(
                        RUNNER,
                        "SANDBOX_EXEC_PATH",
                        Path(sys.executable),
                    ),
                    mock.patch.object(
                        RUNNER.subprocess,
                        "Popen",
                        return_value=StalledProcess(),
                    ),
                    mock.patch.object(
                        RUNNER,
                        "SOURCE_CAPTURE_IDLE_TIMEOUT_SECONDS",
                        0.04,
                    ),
                    mock.patch.object(RUNNER, "SOURCE_CAPTURE_POLL_SECONDS", 0.01),
                    mock.patch.object(RUNNER, "_terminate_process_group"),
                ):
                    with self.assertRaisesRegex(
                        RUNNER.ShadowPolicyError,
                        "idle timed out",
                    ):
                        RUNNER._sandboxed_capture_executor(
                            (sys.executable, "-I", str(RUNNER.TRANSPORT_PATH)),
                            output,
                            root,
                            1024,
                        )

    def test_runner_serializes_capture_and_accept_for_each_host(
        self,
    ) -> None:
        active = 0
        peak_active = 0
        counter_lock = threading.Lock()
        start_barrier = threading.Barrier(2)

        def serialized_executor(
            _coordinator: Path,
            arguments: tuple[str, ...],
            _invocation_dir: Path,
        ) -> subprocess.CompletedProcess[str]:
            nonlocal active, peak_active
            with counter_lock:
                active += 1
                peak_active = max(peak_active, active)
            time.sleep(0.08)
            with counter_lock:
                active -= 1
            return subprocess.CompletedProcess(arguments, 0)

        def serialized_capture_executor(
            command: tuple[str, ...],
            output: object,
            _invocation_dir: Path,
            _max_output_bytes: int,
        ) -> subprocess.CompletedProcess[bytes]:
            nonlocal active, peak_active
            with counter_lock:
                active += 1
                peak_active = max(peak_active, active)
            output.write(b'{"kind":"test-transport"}\n')  # type: ignore[attr-defined]
            time.sleep(0.08)
            with counter_lock:
                active -= 1
            return subprocess.CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory(prefix="shadow-runner-lock.") as raw:
            shadow_root = Path(raw) / "shadow"
            invocation_dir, _ = RUNNER._prepare_invocation_directory(
                shadow_root / "invocation",
                shadow_root=shadow_root,
            )
            arguments = accept_source_arguments(invocation_dir)
            status = source_status_result(
                invocation_dir,
                host="miku-bot-dev",
            )

            def status_query(
                _coordinator: Path,
                _arguments: tuple[str, ...],
                _invocation_dir: Path,
            ) -> dict[str, object]:
                return status

            def same_host_run() -> int:
                start_barrier.wait(timeout=5)
                return RUNNER.run_guarded_coordinator(
                    arguments,
                    invocation_dir=invocation_dir,
                    host="miku-bot-dev",
                    shadow_root=shadow_root,
                    coordinator_path=Path(sys.executable),
                    executor=serialized_executor,
                    capture_executor=serialized_capture_executor,
                    status_query=status_query,
                ).returncode

            with ThreadPoolExecutor(max_workers=2) as executor:
                returncodes = list(
                    executor.map(lambda _index: same_host_run(), range(2))
                )

        self.assertEqual([0, 0], returncodes)
        self.assertEqual(1, peak_active)

    def test_runner_keeps_different_host_captures_concurrent(self) -> None:
        active = 0
        peak_active = 0
        counter_lock = threading.Lock()
        capture_barrier = threading.Barrier(2)

        with tempfile.TemporaryDirectory(prefix="shadow-runner-host-parallel.") as raw:
            shadow_root = Path(raw) / "shadow"
            invocation_dirs = {
                host: RUNNER._prepare_invocation_directory(
                    shadow_root / host,
                    shadow_root=shadow_root,
                )[0]
                for host in ("miku-bot-dev", "hoteng-srv-01")
            }

            def run_host(host: str) -> int:
                nonlocal active, peak_active
                invocation_dir = invocation_dirs[host]
                arguments = accept_source_arguments(invocation_dir)
                status = source_status_result(invocation_dir, host=host)

                def status_query(
                    _coordinator: Path,
                    _arguments: tuple[str, ...],
                    _invocation_dir: Path,
                ) -> dict[str, object]:
                    return status

                def capture_executor(
                    command: tuple[str, ...],
                    output: object,
                    _invocation_dir: Path,
                    _max_output_bytes: int,
                ) -> subprocess.CompletedProcess[bytes]:
                    nonlocal active, peak_active
                    with counter_lock:
                        active += 1
                        peak_active = max(peak_active, active)
                    output.write(b'{"kind":"test-transport"}\n')  # type: ignore[attr-defined]
                    capture_barrier.wait(timeout=5)
                    with counter_lock:
                        active -= 1
                    return subprocess.CompletedProcess(command, 0)

                return RUNNER.run_guarded_coordinator(
                    arguments,
                    invocation_dir=invocation_dir,
                    host=host,
                    shadow_root=shadow_root,
                    coordinator_path=Path(sys.executable),
                    executor=lambda _path, argv, _cwd: subprocess.CompletedProcess(
                        argv,
                        0,
                    ),
                    capture_executor=capture_executor,
                    status_query=status_query,
                ).returncode

            with ThreadPoolExecutor(max_workers=2) as executor:
                returncodes = list(
                    executor.map(run_host, ("miku-bot-dev", "hoteng-srv-01"))
                )

        self.assertEqual([0, 0], returncodes)
        self.assertEqual(2, peak_active)

    def test_runner_rejects_transport_without_python_isolated_mode(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="shadow-runner-capture-isolated."
        ) as raw:
            shadow_root = Path(raw) / "shadow"
            invocation_dir, _ = RUNNER._prepare_invocation_directory(
                shadow_root / "invocation",
                shadow_root=shadow_root,
            )
            status = source_status_result(invocation_dir, host="miku-bot-dev")
            action = status["result"]["active_source_leases"][0]
            action["source_transport_command"].pop(1)

            with self.assertRaisesRegex(
                RUNNER.ShadowPolicyError,
                "Python isolated mode",
            ):
                RUNNER._validated_source_transport_command(
                    action,
                    host="miku-bot-dev",
                    invocation_dir=invocation_dir,
                )

    def test_runner_rejects_non_transport_capture_program(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="shadow-runner-capture-command."
        ) as raw:
            shadow_root = Path(raw) / "shadow"
            invocation_dir, _ = RUNNER._prepare_invocation_directory(
                shadow_root / "invocation",
                shadow_root=shadow_root,
            )
            status = source_status_result(invocation_dir, host="miku-bot-dev")
            action = status["result"]["active_source_leases"][0]
            unexpected = invocation_dir / "unexpected-transport.py"
            unexpected.write_text("raise SystemExit(0)\n", encoding="utf-8")
            action["source_transport_command"] = [
                sys.executable,
                "-I",
                str(unexpected),
                "session-shards",
                "--host",
                "miku-bot-dev",
            ]

            with self.assertRaisesRegex(
                RUNNER.ShadowPolicyError,
                "installed remote-host helper",
            ):
                RUNNER._validated_source_transport_command(
                    action,
                    host="miku-bot-dev",
                    invocation_dir=invocation_dir,
                )

    def test_runner_rejects_drifted_transport_program_commitment(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="shadow-runner-program-commitment."
        ) as raw:
            shadow_root = Path(raw) / "shadow"
            invocation_dir, _ = RUNNER._prepare_invocation_directory(
                shadow_root / "invocation",
                shadow_root=shadow_root,
            )
            arguments = accept_source_arguments(invocation_dir)
            status = source_status_result(invocation_dir, host="miku-bot-dev")
            action = status["result"]["active_source_leases"][0]
            action["transport_lease"]["transport_program_commitment"] = (
                "sha256:" + "b" * 64
            )
            capture_called = False

            def capture_executor(
                command: tuple[str, ...],
                output: object,
                _invocation_dir: Path,
                _max_output_bytes: int,
            ) -> subprocess.CompletedProcess[bytes]:
                nonlocal capture_called
                capture_called = True
                output.write(b"unexpected\n")  # type: ignore[attr-defined]
                return subprocess.CompletedProcess(command, 0)

            with self.assertRaisesRegex(
                RUNNER.ShadowPolicyError,
                "authenticated commitment",
            ):
                RUNNER.run_guarded_coordinator(
                    arguments,
                    invocation_dir=invocation_dir,
                    host="miku-bot-dev",
                    shadow_root=shadow_root,
                    coordinator_path=Path(sys.executable),
                    executor=lambda _path, argv, _cwd: subprocess.CompletedProcess(
                        argv,
                        0,
                    ),
                    capture_executor=capture_executor,
                    status_query=lambda *_args: status,
                )
            snapshots = list(invocation_dir.glob(".remote-codex-probe-*.py"))

        self.assertFalse(capture_called)
        self.assertEqual([], snapshots)

    def test_runner_atomically_captures_then_accepts_and_cleans_stream(self) -> None:
        with tempfile.TemporaryDirectory(prefix="shadow-runner-capture.") as raw:
            shadow_root = Path(raw) / "shadow"
            invocation_dir, _ = RUNNER._prepare_invocation_directory(
                shadow_root / "invocation",
                shadow_root=shadow_root,
            )
            arguments = accept_source_arguments(invocation_dir)
            status = source_status_result(invocation_dir, host="miku-bot-dev")
            output_path = invocation_dir / "source-transport.jsonl"
            order: list[str] = []

            def status_query(
                _coordinator: Path,
                _arguments: tuple[str, ...],
                _invocation_dir: Path,
            ) -> dict[str, object]:
                return status

            def capture_executor(
                command: tuple[str, ...],
                output: object,
                _invocation_dir: Path,
                max_output_bytes: int,
            ) -> subprocess.CompletedProcess[bytes]:
                self.assertFalse(output_path.exists())
                payload = b'{"kind":"fresh-transport"}\n'
                self.assertLess(len(payload), max_output_bytes)
                script_index = 2 if command[1] == "-I" else 1
                snapshot_path = Path(command[script_index])
                self.assertNotEqual(
                    RUNNER.TRANSPORT_PATH.resolve(),
                    snapshot_path.resolve(),
                )
                self.assertEqual(
                    RUNNER.TRANSPORT_PATH.read_bytes(),
                    snapshot_path.read_bytes(),
                )
                self.assertEqual(0o600, snapshot_path.stat().st_mode & 0o777)
                output.write(payload)  # type: ignore[attr-defined]
                order.append("capture")
                return subprocess.CompletedProcess(command, 0)

            def accept_executor(
                _coordinator: Path,
                argv: tuple[str, ...],
                _invocation_dir: Path,
            ) -> subprocess.CompletedProcess[str]:
                order.append("accept")
                self.assertEqual(
                    b'{"kind":"fresh-transport"}\n',
                    output_path.read_bytes(),
                )
                self.assertEqual(0o600, output_path.stat().st_mode & 0o777)
                return subprocess.CompletedProcess(argv, 0)

            result = RUNNER.run_guarded_coordinator(
                arguments,
                invocation_dir=invocation_dir,
                host="miku-bot-dev",
                shadow_root=shadow_root,
                coordinator_path=Path(sys.executable),
                executor=accept_executor,
                capture_executor=capture_executor,
                status_query=status_query,
            )
            self.assertFalse(output_path.exists())
            snapshots = list(invocation_dir.glob(".remote-codex-probe-*.py"))

        self.assertEqual(0, result.returncode)
        self.assertEqual(["capture", "accept"], order)
        self.assertEqual([], snapshots)

    def test_runner_capture_failure_never_calls_accept_or_leaves_partial_stream(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="shadow-runner-capture-failure."
        ) as raw:
            shadow_root = Path(raw) / "shadow"
            invocation_dir, _ = RUNNER._prepare_invocation_directory(
                shadow_root / "invocation",
                shadow_root=shadow_root,
            )
            arguments = accept_source_arguments(invocation_dir)
            status = source_status_result(invocation_dir, host="miku-bot-dev")
            output_path = invocation_dir / "source-transport.jsonl"
            output_path.write_bytes(b"stale")
            output_path.chmod(0o600)
            accept_called = False

            def status_query(
                _coordinator: Path,
                _arguments: tuple[str, ...],
                _invocation_dir: Path,
            ) -> dict[str, object]:
                return status

            def failed_capture(
                command: tuple[str, ...],
                output: object,
                _invocation_dir: Path,
                _max_output_bytes: int,
            ) -> subprocess.CompletedProcess[bytes]:
                self.assertFalse(output_path.exists())
                output.write(b"partial")  # type: ignore[attr-defined]
                return subprocess.CompletedProcess(command, 9)

            def forbidden_accept(
                _coordinator: Path,
                argv: tuple[str, ...],
                _invocation_dir: Path,
            ) -> subprocess.CompletedProcess[str]:
                nonlocal accept_called
                accept_called = True
                return subprocess.CompletedProcess(argv, 0)

            with self.assertRaisesRegex(
                RUNNER.ShadowPolicyError,
                "capture failed",
            ):
                RUNNER.run_guarded_coordinator(
                    arguments,
                    invocation_dir=invocation_dir,
                    host="miku-bot-dev",
                    shadow_root=shadow_root,
                    coordinator_path=Path(sys.executable),
                    executor=forbidden_accept,
                    capture_executor=failed_capture,
                    status_query=status_query,
                )
            temporary_files = list(
                invocation_dir.glob(".source-transport.jsonl.capture-*.tmp")
            )
            self.assertFalse(output_path.exists())

        self.assertFalse(accept_called)
        self.assertEqual([], temporary_files)

    def test_runner_uses_authenticated_host_for_mismatch_mutex(self) -> None:
        entered = threading.Event()
        with tempfile.TemporaryDirectory(prefix="shadow-runner-host-mismatch.") as raw:
            shadow_root = Path(raw) / "shadow"
            invocation_dir, _ = RUNNER._prepare_invocation_directory(
                shadow_root / "invocation",
                shadow_root=shadow_root,
            )
            arguments = accept_source_arguments(invocation_dir)
            status = source_status_result(invocation_dir, host="miku-bot-dev")

            def status_query(
                _coordinator: Path,
                _arguments: tuple[str, ...],
                _invocation_dir: Path,
            ) -> dict[str, object]:
                return status

            def slow_executor(
                _coordinator: Path,
                arguments: tuple[str, ...],
                _invocation_dir: Path,
            ) -> subprocess.CompletedProcess[str]:
                entered.set()
                time.sleep(0.12)
                return subprocess.CompletedProcess(arguments, 0)

            with ThreadPoolExecutor(max_workers=2) as executor:
                owner = executor.submit(
                    RUNNER.run_guarded_coordinator,
                    arguments,
                    invocation_dir=invocation_dir,
                    host="miku-bot-dev",
                    shadow_root=shadow_root,
                    coordinator_path=Path(sys.executable),
                    executor=slow_executor,
                    capture_executor=successful_capture_executor,
                    status_query=status_query,
                )
                self.assertTrue(entered.wait(timeout=5))
                started = time.monotonic()
                mismatch = executor.submit(
                    RUNNER.run_guarded_coordinator,
                    arguments,
                    invocation_dir=invocation_dir,
                    host="hoteng-srv-01",
                    shadow_root=shadow_root,
                    coordinator_path=Path(sys.executable),
                    executor=slow_executor,
                    capture_executor=successful_capture_executor,
                    status_query=status_query,
                )
                self.assertEqual(0, owner.result(timeout=5).returncode)
                with self.assertRaisesRegex(
                    RUNNER.ShadowPolicyError,
                    "caller host does not match",
                ):
                    mismatch.result(timeout=5)
                elapsed = time.monotonic() - started

        self.assertGreaterEqual(elapsed, 0.08)

    def test_coordinator_verification_runs_in_sandboxed_subprocess(self) -> None:
        with tempfile.TemporaryDirectory(prefix="shadow-verifier.") as raw:
            shadow_root = Path(raw) / "shadow"
            invocation_dir, _ = RUNNER._prepare_invocation_directory(
                shadow_root / "invocation",
                shadow_root=shadow_root,
            )
            scripts_root = Path(raw) / "coordinator"
            package_root = scripts_root / "retrospective_v2"
            package_root.mkdir(parents=True)
            coordinator_path = scripts_root / "session_retrospective_v2.py"
            coordinator_path.write_text("raise SystemExit(0)\n", encoding="utf-8")
            (package_root / "__init__.py").write_text("", encoding="utf-8")
            (package_root / "identity.py").write_text(
                'raise RuntimeError("must stay sandboxed")\n',
                encoding="utf-8",
            )
            coordinator_identity_path = invocation_dir / "identity-v2.json"
            coordinator_identity_path.write_text("{}", encoding="utf-8")
            coordinator_identity_path.chmod(0o600)
            receipt = {"receipt_ref": "shadow_coverage_receipt_v2:" + "a" * 64}
            observed: list[tuple[str, ...]] = []

            def verifier_process(
                command: tuple[str, ...],
                **_kwargs: object,
            ) -> subprocess.CompletedProcess[str]:
                observed.append(command)
                operation = command[7]
                if operation == "load-identity":
                    result = {
                        "operation": operation,
                        "schema": RUNNER.COORDINATOR_VERIFIER_SCHEMA,
                        "secret_hex": "c" * 64,
                    }
                else:
                    self.assertEqual("verify-coverage", operation)
                    self.assertEqual(receipt, json.loads(command[10]))
                    result = {
                        "operation": operation,
                        "schema": RUNNER.COORDINATOR_VERIFIER_SCHEMA,
                        "verified": receipt,
                    }
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps(result, separators=(",", ":")) + "\n",
                    "",
                )

            with (
                mock.patch.object(RUNNER.sys, "platform", "darwin"),
                mock.patch.object(
                    RUNNER,
                    "SANDBOX_EXEC_PATH",
                    Path(sys.executable),
                ),
                mock.patch.object(
                    RUNNER,
                    "_run_supervised_process",
                    side_effect=verifier_process,
                ),
            ):
                identity_key = RUNNER._load_coordinator_identity_key(
                    coordinator_path,
                    coordinator_identity_path,
                    invocation_dir,
                )
                verified = RUNNER._verify_coordinator_coverage_receipt(
                    coordinator_path,
                    coordinator_identity_path,
                    receipt,
                    invocation_dir,
                )

        self.assertEqual(b"\xcc" * 32, identity_key)
        self.assertEqual(receipt, verified)
        self.assertEqual(2, len(observed))
        for command in observed:
            self.assertEqual(("-I", "-c"), command[4:6])
            self.assertIn("(deny network*)", command[2])
            self.assertIn(str(invocation_dir), command[2])

    def test_runner_records_backfill_through_the_atomic_transport_ledger(
        self,
    ) -> None:
        now_utc = dt.datetime(2026, 7, 14, 12, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory(prefix="shadow-runner-backfill.") as raw:
            shadow_root = Path(raw) / "shadow"
            requested_invocation = shadow_root / "invocation"
            invocation_dir, _resolved_root = RUNNER._prepare_invocation_directory(
                requested_invocation,
                shadow_root=shadow_root,
            )
            transport = RUNNER._load_transport_module()
            holdout_identity_path = invocation_dir / "holdout-identity"
            holdout_identity_key = transport._create_session_shards_shadow_identity(
                holdout_identity_path
            )
            coordinator_identity_key = b"c" * 32
            coordinator_identity_path = invocation_dir / "identity-v2.json"
            coordinator_identity_path.write_text("{}", encoding="ascii")
            coordinator_identity_path.chmod(0o600)
            receipt = transport._session_shards_holdout_receipt(
                identity_key=holdout_identity_key,
                host="hoteng-srv-01",
                window_start="2026-07-13T00:00:00Z",
                window_end="2026-07-14T00:00:00Z",
                source_kind="codex_session_history",
                source_lease_ref="source-lease:runner:partial",
                now_utc=now_utc,
            )
            receipt_path = invocation_dir / "holdout-receipt.json"
            receipt_path.write_text(
                json.dumps(receipt, separators=(",", ":"), sort_keys=True),
                encoding="utf-8",
            )
            receipt_path.chmod(0o600)
            partial_run_dir = invocation_dir / "partial-run"
            backfill_run_dir = invocation_dir / "backfill-run"
            partial_run_dir.mkdir(mode=0o700)
            backfill_run_dir.mkdir(mode=0o700)
            partial_run_ref = protocol_ref("run_ref_v2:", "partial")
            backfill_run_ref = protocol_ref("run_ref_v2:", "backfill")
            coordinator_key_id = transport._session_shards_coordinator_identity_key_id(
                coordinator_identity_key
            )
            host_ref = protocol_ref("host_ref_v2:", "hoteng-srv-01")
            snapshot_ref = protocol_ref("source_snapshot_v2:", "backfill")
            source_receipt_ref = protocol_ref(
                "source_transport_receipt_v2:", "backfill"
            )
            configuration_root = hashlib.sha256(b"configuration").hexdigest()
            controlled_gap_ref = receipt["holdout_ref"]

            def coverage(*, partial: bool) -> dict[str, object]:
                run_ref = partial_run_ref if partial else backfill_run_ref
                configured = (
                    sorted(
                        protocol_ref("host_ref_v2:", host)
                        for host in RUNNER.CANONICAL_HOSTS
                    )
                    if partial
                    else [host_ref]
                )
                return {
                    "authentication_tag": protocol_ref(
                        "shadow_coverage_auth_v2:", run_ref
                    ),
                    "backfill_of": None if partial else partial_run_ref,
                    "checkpoint_revision": 7,
                    "configuration_root": configuration_root,
                    "controlled_gap_receipt_ref": controlled_gap_ref,
                    "configured_host_refs": configured,
                    "covered_host_refs": (
                        [item for item in configured if item != host_ref]
                        if partial
                        else [host_ref]
                    ),
                    "export_bundle_digest": hashlib.sha256(
                        run_ref.encode()
                    ).hexdigest(),
                    "gap_host_refs": [host_ref] if partial else [],
                    "identity_key_id": coordinator_key_id,
                    "mode": "daily",
                    "model_era": "test-model",
                    "partial": partial,
                    "policy_commitment": protocol_ref(
                        "shadow_policy_commitment_v2:", run_ref
                    ),
                    "policy_era": "test-policy",
                    "production_configuration_ref": protocol_ref(
                        "configuration_ref_v2:", "production"
                    ),
                    "receipt_ref": protocol_ref("shadow_coverage_receipt_v2:", run_ref),
                    "run_ref": run_ref,
                    "schema": RUNNER.COORDINATOR_COVERAGE_SCHEMA,
                    "source_evidence_commitment": protocol_ref(
                        "shadow_source_evidence_v2:", run_ref
                    ),
                    "source_receipt_refs": [
                        protocol_ref(
                            "source_transport_receipt_v2:",
                            "partial" if partial else "backfill",
                        )
                    ],
                    "source_snapshot_refs": [
                        protocol_ref(
                            "source_snapshot_v2:",
                            "partial" if partial else "backfill",
                        )
                    ],
                    "source_units": {
                        "consumed_candidate": 1,
                        "expected": 1,
                        "explicit_gap": 0,
                        "structurally_excluded": 0,
                    },
                    "specification_digest": hashlib.sha256(b"spec").hexdigest(),
                    "version_commitment": protocol_ref(
                        "shadow_version_commitment_v2:", "version"
                    ),
                    "window_end": receipt["window_end"],
                    "window_start": receipt["window_start"],
                }

            partial_coverage = coverage(partial=True)
            backfill_coverage = coverage(partial=False)

            def status(*, partial: bool) -> dict[str, object]:
                run_ref = partial_run_ref if partial else backfill_run_ref
                cell = {
                    "lease_ref": (
                        receipt["source_lease_ref"]
                        if partial
                        else "source-lease:runner:backfill"
                    ),
                    "snapshot_ref": None if partial else snapshot_ref,
                    "status": "gap" if partial else "complete",
                    "transport_receipt_ref": (None if partial else source_receipt_ref),
                }
                partial_hosts = {
                    configured_host: {
                        "cells": (
                            {receipt["source_kind"]: cell}
                            if configured_host == receipt["host"]
                            else {}
                        ),
                        "host_ref": protocol_ref("host_ref_v2:", configured_host),
                        "status": (
                            "gap" if configured_host == receipt["host"] else "complete"
                        ),
                    }
                    for configured_host in RUNNER.CANONICAL_HOSTS
                }
                result = {
                    "accepted_source_manifests": (
                        []
                        if partial
                        else [
                            {
                                "host_ref": host_ref,
                                "record_count": 1,
                                "snapshot_ref": snapshot_ref,
                                "source_kind": receipt["source_kind"],
                                "status": "complete",
                            }
                        ]
                    ),
                    "active_source_leases": [],
                    "checkpoint_revision": 8,
                    "coverage": {
                        "hosts": (
                            partial_hosts
                            if partial
                            else {
                                receipt["host"]: {
                                    "cells": {receipt["source_kind"]: cell},
                                    "host_ref": host_ref,
                                    "status": "complete",
                                }
                            }
                        ),
                        "status": "partial" if partial else "complete",
                    },
                    "gaps": (
                        []
                        if not partial
                        else [
                            {
                                "host": receipt["host"],
                                "host_ref": host_ref,
                                "lease_ref": receipt["source_lease_ref"],
                                "reason": receipt["reason"],
                                "receipt_ref": receipt["holdout_ref"],
                                "source_kind": receipt["source_kind"],
                            }
                        ]
                    ),
                    "identity_key_id": coordinator_key_id,
                    "lineage": {"backfill_of": None if partial else partial_run_ref},
                    "mode": "daily",
                    "publication": {
                        "coverage_receipt": (
                            partial_coverage if partial else backfill_coverage
                        )
                    },
                    "run_ref": run_ref,
                    "schema_version": 2,
                    "shadow": True,
                    "stage": "export",
                    "window": {
                        "start": receipt["window_start"],
                        "end": receipt["window_end"],
                    },
                }
                return {
                    "command": "status",
                    "error": None,
                    "exit_code": 0,
                    "ok": True,
                    "result": result,
                    "schema": RUNNER.CLI_RESULT_SCHEMA,
                }

            statuses = {
                partial_run_dir: status(partial=True),
                backfill_run_dir: status(partial=False),
            }
            expected_coverages = {
                partial_coverage["receipt_ref"]: partial_coverage,
                backfill_coverage["receipt_ref"]: backfill_coverage,
            }

            def status_query(
                _coordinator: Path,
                argv: tuple[str, ...],
                _invocation_dir: Path,
            ) -> dict[str, object]:
                run_dir = Path(argv[argv.index("--run-dir") + 1])
                return statuses[run_dir]

            def verify_coverage(
                _coordinator: Path,
                _identity: Path,
                value: dict[str, object],
                _invocation: Path,
            ) -> dict[str, object]:
                expected = expected_coverages.get(value.get("receipt_ref"))
                if expected != value:
                    raise RUNNER.ShadowPolicyError("coverage authentication failed")
                return dict(value)

            arguments = {
                "invocation_dir": invocation_dir,
                "receipt_path": receipt_path,
                "holdout_identity_path": holdout_identity_path,
                "coordinator_identity_path": coordinator_identity_path,
                "partial_run_dir": partial_run_dir,
                "backfill_run_dir": backfill_run_dir,
                "shadow_root": shadow_root,
                "coordinator_path": Path(sys.executable),
                "transport_module": transport,
                "status_query": status_query,
                "coverage_verifier": verify_coverage,
                "coordinator_identity_loader": (
                    lambda _coordinator,
                    _identity,
                    _invocation: coordinator_identity_key
                ),
                "now_utc": now_utc,
            }

            synthetic_partial = json.loads(json.dumps(statuses[partial_run_dir]))
            synthetic_partial["result"]["gaps"][0]["receipt_ref"] = (
                "session_shards_holdout_v1:" + "f" * 64
            )
            statuses[partial_run_dir] = synthetic_partial
            with self.assertRaisesRegex(
                RUNNER.ShadowPolicyError,
                "complete real backfill",
            ):
                RUNNER.record_backfill_replacement(**arguments)
            ledger_path = shadow_root.resolve() / "campaign-ledger.sqlite3"
            self.assertFalse(ledger_path.exists())

            synthetic_partial = status(partial=True)
            synthetic_partial["result"]["gaps"].append(
                dict(synthetic_partial["result"]["gaps"][0])
            )
            statuses[partial_run_dir] = synthetic_partial
            with self.assertRaisesRegex(
                RUNNER.ShadowPolicyError,
                "complete real backfill",
            ):
                RUNNER.record_backfill_replacement(**arguments)
            self.assertFalse(ledger_path.exists())

            statuses[partial_run_dir] = status(partial=True)
            for field, mismatched_value in (
                ("mode", "weekly"),
                ("window_start", "1999-01-01T00:00:00Z"),
                ("window_end", "1999-01-02T00:00:00Z"),
            ):
                with self.subTest(coverage_binding=field):
                    mismatched = json.loads(json.dumps(status(partial=False)))
                    mismatched_coverage = mismatched["result"]["publication"][
                        "coverage_receipt"
                    ]
                    mismatched_coverage[field] = mismatched_value
                    expected_coverages[mismatched_coverage["receipt_ref"]] = (
                        mismatched_coverage
                    )
                    statuses[backfill_run_dir] = mismatched
                    with self.assertRaisesRegex(
                        RUNNER.ShadowPolicyError,
                        "current for this shadow run",
                    ):
                        RUNNER.record_backfill_replacement(**arguments)
                    self.assertFalse(ledger_path.exists())
            expected_coverages[backfill_coverage["receipt_ref"]] = backfill_coverage
            statuses[backfill_run_dir] = status(partial=False)

            synthetic = json.loads(json.dumps(statuses[backfill_run_dir]))
            synthetic["result"]["accepted_source_manifests"] = []
            statuses[backfill_run_dir] = synthetic
            with self.assertRaisesRegex(
                RUNNER.ShadowPolicyError,
                "no unique accepted session-shards",
            ):
                RUNNER.record_backfill_replacement(**arguments)
            self.assertFalse(ledger_path.exists())

            extra_cell = status(partial=False)
            extra_host = extra_cell["result"]["coverage"]["hosts"][receipt["host"]]
            extra_host["cells"]["history"] = dict(
                extra_host["cells"][receipt["source_kind"]]
            )
            statuses[backfill_run_dir] = extra_cell
            with self.assertRaisesRegex(
                RUNNER.ShadowPolicyError,
                "complete real backfill",
            ):
                RUNNER.record_backfill_replacement(**arguments)
            self.assertFalse(ledger_path.exists())

            extra_cell_field = status(partial=False)
            extra_cell_field["result"]["coverage"]["hosts"][receipt["host"]]["cells"][
                receipt["source_kind"]
            ]["unexpected_binding"] = "untrusted"
            statuses[backfill_run_dir] = extra_cell_field
            with self.assertRaisesRegex(
                RUNNER.ShadowPolicyError,
                "complete real backfill",
            ):
                RUNNER.record_backfill_replacement(**arguments)
            self.assertFalse(ledger_path.exists())

            extra_manifest = status(partial=False)
            extra_manifest_value = dict(
                extra_manifest["result"]["accepted_source_manifests"][0]
            )
            extra_manifest_value["source_kind"] = "history"
            extra_manifest_value["snapshot_ref"] = protocol_ref(
                "source_snapshot_v2:", "extra-manifest"
            )
            extra_manifest["result"]["accepted_source_manifests"].append(
                extra_manifest_value
            )
            statuses[backfill_run_dir] = extra_manifest
            with self.assertRaisesRegex(
                RUNNER.ShadowPolicyError,
                "no unique accepted session-shards",
            ):
                RUNNER.record_backfill_replacement(**arguments)
            self.assertFalse(ledger_path.exists())

            for field, extra_ref in (
                (
                    "source_snapshot_refs",
                    protocol_ref("source_snapshot_v2:", "extra"),
                ),
                (
                    "source_receipt_refs",
                    protocol_ref("source_transport_receipt_v2:", "extra"),
                ),
            ):
                with self.subTest(extra_coverage_ref=field):
                    extra_evidence = json.loads(json.dumps(status(partial=False)))
                    extra_coverage = extra_evidence["result"]["publication"][
                        "coverage_receipt"
                    ]
                    extra_coverage[field].append(extra_ref)
                    expected_coverages[extra_coverage["receipt_ref"]] = extra_coverage
                    statuses[backfill_run_dir] = extra_evidence
                    with self.assertRaisesRegex(
                        RUNNER.ShadowPolicyError,
                        "complete real backfill",
                    ):
                        RUNNER.record_backfill_replacement(**arguments)
                    self.assertFalse(ledger_path.exists())

            extra_units = json.loads(json.dumps(status(partial=False)))
            extra_units_coverage = extra_units["result"]["publication"][
                "coverage_receipt"
            ]
            extra_units_coverage["source_units"]["consumed_candidate"] = 2
            extra_units_coverage["source_units"]["expected"] = 2
            expected_coverages[extra_units_coverage["receipt_ref"]] = (
                extra_units_coverage
            )
            statuses[backfill_run_dir] = extra_units
            with self.assertRaisesRegex(
                RUNNER.ShadowPolicyError,
                "complete real backfill",
            ):
                RUNNER.record_backfill_replacement(**arguments)
            self.assertFalse(ledger_path.exists())
            expected_coverages[backfill_coverage["receipt_ref"]] = backfill_coverage

            contradictory = status(partial=False)
            contradictory_host = contradictory["result"]["coverage"]["hosts"][
                receipt["host"]
            ]
            contradictory_host["status"] = "no_activity"
            statuses[backfill_run_dir] = contradictory
            with self.assertRaisesRegex(
                RUNNER.ShadowPolicyError,
                "complete real backfill",
            ):
                RUNNER.record_backfill_replacement(**arguments)
            self.assertFalse(ledger_path.exists())

            nonempty_absence = status(partial=False)
            absence_host = nonempty_absence["result"]["coverage"]["hosts"][
                receipt["host"]
            ]
            absence_host["status"] = "no_activity"
            absence_host["cells"][receipt["source_kind"]]["status"] = "verified_absent"
            absence_manifest = nonempty_absence["result"]["accepted_source_manifests"][
                0
            ]
            absence_manifest["status"] = "verified_absent"
            statuses[backfill_run_dir] = nonempty_absence
            with self.assertRaisesRegex(
                RUNNER.ShadowPolicyError,
                "zero records",
            ):
                RUNNER.record_backfill_replacement(**arguments)
            self.assertFalse(ledger_path.exists())

            statuses[backfill_run_dir] = status(partial=False)
            accepted_ref = RUNNER.record_backfill_replacement(**arguments)
            with self.assertRaisesRegex(ValueError, "replay rejected"):
                RUNNER.record_backfill_replacement(**arguments)
            ledger_mode = ledger_path.stat().st_mode & 0o777

        self.assertEqual(str(receipt["holdout_ref"]), accepted_ref)
        self.assertEqual(0o600, ledger_mode)

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX flock processes")
    def test_runner_host_mutex_serializes_separate_processes(self) -> None:
        context = multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory(prefix="shadow-runner-process-lock.") as raw:
            shadow_root = Path(raw) / "shadow"
            shadow_root.mkdir(mode=0o700)
            events = context.Queue()

            def worker() -> None:
                with RUNNER.host_mutex(shadow_root, "miku-bot-dev"):
                    events.put(("enter", time.monotonic_ns()))
                    time.sleep(0.1)
                    events.put(("exit", time.monotonic_ns()))

            processes = [context.Process(target=worker) for _index in range(2)]
            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=5)
                self.assertEqual(0, process.exitcode)
            observed = sorted(
                (events.get(timeout=2) for _index in range(4)),
                key=lambda item: item[1],
            )
            events.close()
            events.join_thread()

        self.assertEqual(["enter", "exit", "enter", "exit"], [x[0] for x in observed])

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX flock processes")
    def test_runner_cross_process_host_mismatch_uses_actual_host_lock(self) -> None:
        context = multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory(
            prefix="shadow-runner-process-mismatch."
        ) as raw:
            shadow_root = Path(raw) / "shadow"
            invocation_dir, _ = RUNNER._prepare_invocation_directory(
                shadow_root / "invocation",
                shadow_root=shadow_root,
            )
            arguments = accept_source_arguments(invocation_dir)
            status = source_status_result(invocation_dir, host="miku-bot-dev")
            events = context.Queue()

            def status_query(
                _coordinator: Path,
                _arguments: tuple[str, ...],
                _invocation_dir: Path,
            ) -> dict[str, object]:
                return status

            def owner_worker() -> None:
                def executor(
                    _coordinator: Path,
                    argv: tuple[str, ...],
                    _invocation_dir: Path,
                ) -> subprocess.CompletedProcess[str]:
                    events.put("enter")
                    time.sleep(0.15)
                    events.put("exit")
                    return subprocess.CompletedProcess(argv, 0)

                RUNNER.run_guarded_coordinator(
                    arguments,
                    invocation_dir=invocation_dir,
                    host="miku-bot-dev",
                    shadow_root=shadow_root,
                    coordinator_path=Path(sys.executable),
                    executor=executor,
                    capture_executor=successful_capture_executor,
                    status_query=status_query,
                )

            def mismatch_worker() -> None:
                try:
                    RUNNER.run_guarded_coordinator(
                        arguments,
                        invocation_dir=invocation_dir,
                        host="hoteng-srv-01",
                        shadow_root=shadow_root,
                        coordinator_path=Path(sys.executable),
                        executor=lambda *_args: subprocess.CompletedProcess([], 99),
                        capture_executor=successful_capture_executor,
                        status_query=status_query,
                    )
                except RUNNER.ShadowPolicyError:
                    events.put("mismatch")

            owner = context.Process(target=owner_worker)
            owner.start()
            self.assertEqual("enter", events.get(timeout=5))
            mismatch = context.Process(target=mismatch_worker)
            mismatch.start()
            owner.join(timeout=5)
            mismatch.join(timeout=5)
            self.assertEqual(0, owner.exitcode)
            self.assertEqual(0, mismatch.exitcode)
            remaining = [events.get(timeout=5), events.get(timeout=5)]
            events.close()
            events.join_thread()

        self.assertEqual(["exit", "mismatch"], remaining)

    def test_prompt_requires_installed_v2_and_remote_session_shards(self) -> None:
        required = (
            "$HOME/.codex/skills/codex-session-retrospective/scripts/"
            "session_retrospective_v2.py",
            'schema == "cli_result_v2"',
            "result.schema_version == 2",
            'provenance.versions.engine == "2.0"',
            "provenance.configuration_root",
            "$remote-host-context",
            "$HOME/.codex/skills/remote-host-context/scripts/remote_codex_probe.py",
            "`session-shards` is the only evidence transport",
            "local, miku-bot-dev, and hoteng-srv-01",
            "native `session-shards`/`accept-source` bridge contract",
            "status -> accept-source/accept-agent-result -> advance",
            "exact status-provided transport contract",
            "Do not execute or pipe `session-shards` as a separate source driver",
            "Never invoke bundled `execute-source`",
            "remote-agent SSH",
            "native ephemeral subagents at the maximum concurrency",
        )
        for text in required:
            with self.subTest(text=text):
                self.assertIn(text, self.prompt)

        self.assertNotIn("session_retrospective.py", self.prompt)
        self.assertNotIn("scan-daily", self.prompt)
        self.assertNotIn("weekly-dry-run", self.prompt)
        self.assertNotIn("export-retained", self.prompt)
        self.assertNotIn("advance-state", self.prompt)
        self.assertNotIn("codex-session-retrospective-history", self.prompt)
        self.assertNotIn("execute-source ->", self.prompt)
        self.assertNotIn("remote-agent SSH is allowed", self.prompt)

    def test_prompt_is_fail_closed_and_nonpublishing(self) -> None:
        required = (
            "This automation is always non-publishing",
            "$HOME/.codex/skills/remote-host-context/scripts/session_retrospective_v2_shadow_runner.py",
            "The runner, not this prompt, is the enforcement authority",
            "status-authenticated transport-program SHA-256 commitment",
            "owner-only snapshot of exactly those bytes",
            "atomically publish it to the authenticated stream path",
            "never pre-capture or replace that stream outside the runner",
            "unavailable pre-execution write sandbox",
            "Every finalize invocation must include `--shadow`",
            "must not include `--provider-state`",
            "formal send, formal history write, history commit",
            "cursor or head advancement",
            "provider-state path",
            "v1 state mutation",
            "production automation update",
            "reference-only Daily and Weekly release templates are not evidence",
            "cannot perform a first registration or a verified in-place update",
            "Never read or write any production retrospective history/state",
            "Do not initialize it as a Git repository",
            "no such ref may be created",
            "coordinator's no-argument `help` action through the runner",
            "explicit absolute run-local coordinator identity file",
            "`identity --create-identity --identity-path <path> --shadow`",
            "separate source holdout identity",
            "`--create-shadow-identity`",
            "`--require-existing-shadow-identity`",
            "Never accept an implicit/default identity",
            'final `kind == "shadow"`',
            "`state_advanced == false`",
            "`provider.attempt_present == false`",
            "unchanged pre/post host cursor values",
            "remain at stage `export` after shadow completion",
            "rechecks that it remains empty after every coordinator action",
        )
        for text in required:
            with self.subTest(text=text):
                self.assertIn(text, self.prompt)

        self.assertIn(".codex-local/session-retrospective-v2-shadow/", self.prompt)
        self.assertIn("directories with mode 0700", self.prompt)
        self.assertIn("files with mode 0600", self.prompt)

    def test_prompt_runs_acceptance_scenarios_and_accumulates_receipts(self) -> None:
        required = (
            "latest closed UTC seven-day window",
            "Daily partial shadow",
            "Daily missing-host backfill shadow",
            "`start-daily-pair`",
            "`start-daily-pair-successor`",
            "`--holdout-identity-path`",
            "persist that identity's key ID before starting the partial",
            "replacement blocks the pair",
            "`production_source_suppressed == false`",
            "never generate the gap by omitting a configured host",
            "starts the direct successor",
            "For exactly that host and exactly one status-issued source lease",
            "Exactly one controlled holdout is allowed in the invocation",
            "`--qualification-mode shadow`",
            "`--controlled-missing-host`",
            "`shadow_qualification_controlled_missing_host`",
            "`content_free == true`",
            "`source_observed == false`",
            "`transport_attempted == false`",
            "`backfill_required == true`",
            "`--backfill-of`",
            "identify the authenticated `holdout_ref` it replaces",
            "Never emit or accept another holdout for backfill",
            "runner's `record-backfill` action",
            "commit in one persistent transaction",
            "same configuration root, identity key ID, and window",
            "exactly `manifest.json`, `coverage.json`, `episodes.jsonl`",
            "Write one canonical local receipt per valid scenario",
            "Accumulate only distinct valid receipts for the current configuration root",
            "Repeated scheduled or manual invocations provide the two required Weekly runs",
            "one invocation must not manufacture two Weekly receipts",
            "Never claim that calibration, the shadow gate, cutover, or production readiness passed automatically",
        )
        for text in required:
            with self.subTest(text=text):
                self.assertIn(text, self.prompt)


if __name__ == "__main__":
    unittest.main()
