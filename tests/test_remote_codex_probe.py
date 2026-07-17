from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    REPO_ROOT
    / "personal_codex/skills/remote-host-context/scripts/remote_codex_probe.py"
)
SPEC = importlib.util.spec_from_file_location("remote_codex_probe", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
SKILL_PATH = REPO_ROOT / "personal_codex/skills/remote-host-context/SKILL.md"
HOSTS_REFERENCE_PATH = (
    REPO_ROOT / "personal_codex/skills/remote-host-context/references/hosts.md"
)


def write_rollout(codex_root: Path, lines: list[str]) -> str:
    rollout_dir = codex_root / "sessions/2026/05/26"
    rollout_dir.mkdir(parents=True)
    rollout = rollout_dir / "rollout-2026-05-26T10-00-00-example.jsonl"
    rollout.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return "sessions/2026/05/26/rollout-2026-05-26T10-00-00-example.jsonl"


def write_session_meta_rollout(
    path: Path, session_id: str, cwd: str, followup: str
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": cwd},
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": followup}],
            },
        },
    ]
    path.write_text(
        "\n".join(json.dumps(record, separators=(",", ":")) for record in records)
        + "\n",
        encoding="utf-8",
    )


def rollout_identity(codex_root: Path, rollout: str) -> MODULE.RolloutIdentity:
    return MODULE._stat_local_rollout_identity(
        codex_root, MODULE._resolve_rollout_relative_path(rollout)
    )


def identity_kwargs(identity: MODULE.RolloutIdentity) -> dict[str, object]:
    return {
        "expected_source_bytes": identity.size,
        "expected_source_identity": MODULE._rollout_identity_token(identity),
        "authorized_source_bytes": None,
    }


class RemoteHostContextDocumentationTests(unittest.TestCase):
    def test_skill_documents_repeatable_host_preflight_shape(self) -> None:
        skill = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn(
            "preflight --host local --host BL-mac-mini-m4-hoteng "
            "--host miku-bot-dev --host hoteng-srv-01 "
            "--host codex-hoteng-srv-01",
            skill,
        )
        self.assertIn("do not pass positional host names", skill)
        self.assertIn("plural `--hosts` flag", skill)

    def test_skill_bounds_codex_thread_locator_reads(self) -> None:
        skill = SKILL_PATH.read_text(encoding="utf-8")
        reference = HOSTS_REFERENCE_PATH.read_text(encoding="utf-8")

        self.assertIn("cross-host Codex task/thread URL recovery", skill)
        self.assertIn("one exact thread per call", skill)
        self.assertIn("`turnLimit: 1`", skill)
        self.assertIn("`includeOutputs: false`", skill)
        self.assertIn("`maxOutputCharsPerItem` at most 400", skill)
        self.assertIn("do not call `read_thread` unless the service supports", skill)
        self.assertIn("server-side accepted item-type filtering", skill)
        self.assertIn("an item-count limit", skill)
        self.assertIn("a whole-response byte cap", skill)
        self.assertIn("32 KiB (32,768 bytes)", skill)
        self.assertIn("an upper bound, not a target", skill)
        self.assertIn("the service itself must emit", skill)
        self.assertIn("Caller-side projection or truncation after receipt", skill)
        self.assertIn("skip `read_thread` entirely", skill)
        self.assertIn(
            "Invoke `session-meta` directly only when the creation date is known",
            skill,
        )
        self.assertIn("If only an activity or updated date is known", skill)
        self.assertIn("derive the creation date", skill)
        self.assertIn("never substitute activity-date directories", skill)
        self.assertIn("accepts only complete LF-terminated JSONL records", skill)
        self.assertIn("a bare CR is incomplete", skill)
        self.assertIn("descriptor-relative directory enumeration is unreadable", skill)
        self.assertIn("`O_NOFOLLOW` and `O_NONBLOCK`", skill)
        self.assertIn("stat-to-open FIFO replacement", skill)
        self.assertIn("cap cuts through a record", skill)
        self.assertIn("locator and triage output only", skill)
        self.assertIn(
            "cannot prove that every later substantive human follow-up", skill
        )
        self.assertIn("iterate every `chunk_meta` row in byte order", skill)
        self.assertIn("fetch every listed `fetch_ranges[]` entry", skill)
        self.assertIn("complete exact JSONL record stream", skill)
        self.assertIn("must never decide omissions", skill)
        self.assertIn("Do not batch multiple thread reads", skill)
        self.assertIn(
            "[Codex Thread Locator Skim]"
            "(references/hosts.md#codex-thread-locator-skim)",
            skill,
        )
        self.assertIn("`read_thread` is forbidden unless", reference)
        self.assertIn("caller sets every one", reference)
        self.assertIn("Do not batch several thread reads", reference)
        self.assertIn("accepted item-type filtering", reference)
        self.assertIn("an item-count limit that bounds the entire result", reference)
        self.assertIn("permits no more than 12 message snippets", reference)
        self.assertIn("a whole-response byte cap", reference)
        self.assertIn("32 KiB (32,768 bytes)", reference)
        self.assertIn("19,200 raw UTF-8 bytes", reference)
        self.assertIn("13,568 bytes for metadata and encoding overhead", reference)
        self.assertIn("final encoded response would exceed 32,768 bytes", reference)
        self.assertIn("at most 12 user or agent message snippets", reference)
        self.assertIn("rendered on one line per snippet", reference)
        self.assertIn("stringify or serialize the raw result", reference)
        self.assertIn("before returning", reference)
        self.assertIn("created/updated timestamps", reference)
        self.assertIn(
            "do not bound item count or whole-response bytes",
            reference,
        )
        self.assertIn(
            "whole-response controls missing from the observed API", reference
        )
        self.assertIn("If any required server-side control is unavailable", reference)
        self.assertIn("do not call `read_thread` at all", reference)
        self.assertIn("When the creation date is known", reference)
        self.assertIn(
            "run `session-meta` only against that creation-date directory", reference
        )
        self.assertIn("When only an activity or updated date is known", reference)
        self.assertIn("derive the creation date", reference)
        self.assertIn("filter the bounded results by the exact session id", reference)
        self.assertIn(
            "bounded metadata-only exact-thread or session-index lookup", reference
        )
        self.assertIn("Never scan activity-date directories as a proxy", reference)
        self.assertIn("never widen `read_thread` to discover the date", reference)
        self.assertIn("`chunked-rollout-summary`", reference)
        self.assertIn(
            "a later wrapper or noise record can obscure a substantive follow-up",
            reference,
        )
        self.assertIn("sort every `chunk_meta` row by `byte_start`", reference)
        self.assertIn("consume all of them", reference)
        self.assertIn("start at byte 0", reference)
        self.assertIn("gap-free and non-overlapping", reference)
        self.assertIn("end at `source_bytes`", reference)
        self.assertIn("complete exact JSONL record stream", reference)
        self.assertIn("must never decide omissions", reference)
        self.assertIn("summary rows alone are not sufficient evidence", reference)
        self.assertIn("Before any chunk fetch", skill)
        self.assertIn("same `source_bytes` and `full_fetch_limit_bytes`", skill)
        self.assertIn("total planned bytes must equal `source_bytes`", skill)
        self.assertIn("16 MiB (16,777,216 bytes)", skill)
        self.assertIn("`full_reconstruction_allowed=true`", skill)
        self.assertIn("fetch nothing", skill)
        self.assertIn(
            "explicit authorization for that exact rollout and byte count", skill
        )
        self.assertIn("Never silently loop over an over-limit plan", skill)
        self.assertIn("before issuing the first chunk fetch", reference)
        self.assertIn(
            "sum of all planned range lengths equals `source_bytes`", reference
        )
        self.assertIn("issue zero chunk fetches", reference)
        self.assertIn("exact cumulative byte count", reference)
        self.assertIn("metadata-only `rollout-stat`", skill)
        self.assertIn("`--expected-source-bytes`", skill)
        self.assertIn("`--expected-source-identity`", skill)
        self.assertIn("`--authorized-source-bytes`", skill)
        self.assertIn("64 KiB through 2 MiB", skill)
        self.assertIn("4 MiB of final serialized JSONL", skill)
        self.assertIn("computes SHA-256 during that same allowed scan", skill)
        self.assertIn("bounded stdout and stderr readers", skill)
        self.assertIn("snapshot size plus one growth-detection byte", skill)
        self.assertIn("bounds the complete base64 frame before parsing it", skill)
        self.assertIn("source_bytes` from the same descriptor it scans", skill)
        self.assertIn("retains only a transient boolean", skill)
        self.assertIn("restart from byte 0 with a new `rollout-stat`", skill)
        self.assertIn("does not scan or hash its content", reference)
        self.assertIn("`rollout_meta.source_sha256`", reference)
        self.assertIn("run a final `rollout-stat`", reference)
        self.assertIn("discard all partial chunks", reference)
        self.assertNotIn("selected user-bearing chunk", skill)
        self.assertNotIn("relevant user-bearing chunk", reference)
        self.assertNotIn("candidate user-bearing chunk", reference)
        self.assertNotIn("creation or nearby activity date", reference)
        self.assertNotIn("use `read_thread` only as a bounded locator", skill)
        self.assertNotIn("use a callable thread reader only", reference)

    def test_bl_mac_mini_uses_hoteng_macos_home(self) -> None:
        host = MODULE.HOSTS["BL-mac-mini-m4-hoteng"]

        self.assertEqual(host["label"], "BL-mac-mini-m4-hoteng")
        self.assertEqual(host["ssh_target"], "BL-mac-mini-m4-hoteng")
        self.assertEqual(host["codex_root"], "/Users/hoteng/.codex")

    def test_codex_hoteng_srv_uses_distinct_codex_home(self) -> None:
        host = MODULE.HOSTS["codex-hoteng-srv-01"]

        self.assertEqual(host["label"], "codex-hoteng-srv-01")
        self.assertEqual(host["ssh_target"], "codex-hoteng-srv-01")
        self.assertEqual(host["codex_root"], "/home/codex/.codex")

    def test_default_host_shape_keeps_distinct_server_accounts(self) -> None:
        hosts = MODULE._resolve_hosts(
            [
                "local",
                "BL-mac-mini-m4-hoteng",
                "miku-bot-dev",
                "hoteng-srv-01",
                "codex-hoteng-srv-01",
            ]
        )

        self.assertEqual(
            hosts,
            [
                "local",
                "BL-mac-mini-m4-hoteng",
                "miku-bot-dev",
                "hoteng-srv-01",
                "codex-hoteng-srv-01",
            ],
        )


class SizeGuardedBytesIO(io.BytesIO):
    def __init__(self, data: bytes, *, max_readline_size: int) -> None:
        super().__init__(data)
        self.max_readline_size = max_readline_size
        self.readline_sizes: list[int] = []

    def readline(self, size: int = -1) -> bytes:
        self.readline_sizes.append(size)
        if size < 0 or size > self.max_readline_size:
            raise AssertionError(f"unbounded readline: {size}")
        return super().readline(size)


class RemoteCodexProbeChunkTests(unittest.TestCase):
    def test_session_meta_missing_codex_root_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            scan = MODULE._scan_session_meta_records(
                codex_root=Path(temp_dir) / "missing-codex-root",
                dates=[MODULE.dt.date(2026, 5, 26)],
                limit=10,
                host="local",
            )

        self.assertEqual(scan, MODULE.SessionMetaScan(rows=[], truncated=False))

    def test_session_meta_root_io_errors_fail_closed_without_path_leak(self) -> None:
        secret_root = "/sensitive/codex/root"
        errors = (
            PermissionError(13, "Permission denied", secret_root),
            OSError(5, "Input/output error", secret_root),
        )
        for root_error in errors:
            with self.subTest(error_type=type(root_error).__name__):
                with (
                    mock.patch.object(
                        MODULE,
                        "_open_pinned_codex_root",
                        side_effect=root_error,
                    ),
                    self.assertRaises(MODULE.SessionMetaRolloutError) as raised,
                ):
                    MODULE._scan_session_meta_records(
                        codex_root=Path(secret_root),
                        dates=[MODULE.dt.date(2026, 5, 26)],
                        limit=10,
                        host="local",
                    )

                self.assertEqual(raised.exception.error, "session directory unreadable")
                self.assertIsNone(raised.exception.rollout)
                self.assertNotIn(secret_root, str(raised.exception))

    def test_session_meta_root_post_lstat_failures_fail_closed_local_and_embedded(
        self,
    ) -> None:
        secret_path = "/sensitive/root-race"
        windows = ("resolve", "stat", "open", "resolve-runtime")
        for window in windows:
            with (
                self.subTest(scope="local", window=window),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                codex_root = Path(temp_dir) / ".codex"
                codex_root.mkdir()
                resolved_root = codex_root.resolve()
                real_resolve = MODULE.pathlib.Path.resolve
                real_stat = MODULE.os.stat
                real_open = MODULE.os.open

                def resolve_failure(path: Path, *args, **kwargs):
                    if path == codex_root:
                        if window == "resolve-runtime":
                            raise RuntimeError(f"symlink loop at {secret_path}")
                        raise FileNotFoundError(secret_path)
                    return real_resolve(path, *args, **kwargs)

                def stat_failure(path, *args, **kwargs):
                    if (
                        str(path) == str(resolved_root)
                        and kwargs.get("follow_symlinks") is False
                        and kwargs.get("dir_fd") is None
                    ):
                        raise FileNotFoundError(secret_path)
                    return real_stat(path, *args, **kwargs)

                def open_failure(path, *args, **kwargs):
                    if (
                        str(path) == str(resolved_root)
                        and kwargs.get("dir_fd") is None
                    ):
                        raise FileNotFoundError(secret_path)
                    return real_open(path, *args, **kwargs)

                if window in ("resolve", "resolve-runtime"):
                    patcher = mock.patch.object(
                        MODULE.pathlib.Path,
                        "resolve",
                        resolve_failure,
                    )
                elif window == "stat":
                    patcher = mock.patch.object(MODULE.os, "stat", stat_failure)
                else:
                    patcher = mock.patch.object(MODULE.os, "open", open_failure)

                with (
                    patcher,
                    self.assertRaises(MODULE.SessionMetaRolloutError) as raised,
                ):
                    MODULE._scan_session_meta_records(
                        codex_root=codex_root,
                        dates=[MODULE.dt.date(2026, 5, 26)],
                        limit=10,
                        host="local",
                    )

                self.assertEqual(raised.exception.error, "session directory unreadable")
                self.assertIsNone(raised.exception.rollout)
                self.assertNotIn(secret_path, str(raised.exception))

            with (
                self.subTest(scope="embedded", window=window),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                codex_root = Path(temp_dir) / ".codex"
                codex_root.mkdir()
                script = MODULE._remote_python_script(
                    {
                        "mode": "session-meta",
                        "dates": [],
                        "limit": 10,
                        "codex_root": str(codex_root),
                        "session_meta_scan_bytes": MODULE.MAX_SESSION_META_SCAN_BYTES,
                    }
                )
                marker = 'if CONFIG["mode"] == "session-meta":\n'
                if window in ("resolve", "resolve-runtime"):
                    error_type = "RuntimeError" if window == "resolve-runtime" else "FileNotFoundError"
                    injection = (
                        "_real_resolve = pathlib.Path.resolve\n"
                        "def injected_root_resolve(path, *args, **kwargs):\n"
                        "    if path == ROOT:\n"
                        f"        raise {error_type}({secret_path!r})\n"
                        "    return _real_resolve(path, *args, **kwargs)\n"
                        "pathlib.Path.resolve = injected_root_resolve\n\n"
                    )
                elif window == "stat":
                    injection = (
                        "_resolved_root_text = str(ROOT.resolve(strict=True))\n"
                        "_real_stat = os.stat\n"
                        "def injected_root_stat(path, *args, **kwargs):\n"
                        "    if str(path) == _resolved_root_text and kwargs.get('follow_symlinks') is False and kwargs.get('dir_fd') is None:\n"
                        f"        raise FileNotFoundError({secret_path!r})\n"
                        "    return _real_stat(path, *args, **kwargs)\n"
                        "os.stat = injected_root_stat\n\n"
                    )
                else:
                    injection = (
                        "_resolved_root_text = str(ROOT.resolve(strict=True))\n"
                        "_real_open = os.open\n"
                        "def injected_root_open(path, *args, **kwargs):\n"
                        "    if str(path) == _resolved_root_text and kwargs.get('dir_fd') is None:\n"
                        f"        raise FileNotFoundError({secret_path!r})\n"
                        "    return _real_open(path, *args, **kwargs)\n"
                        "os.open = injected_root_open\n\n"
                    )
                self.assertEqual(script.count(marker), 1)
                script = script.replace(marker, injection + marker)
                result = subprocess.run(
                    [sys.executable, "-"],
                    input=script,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            payload_lines = MODULE._extract_framed_lines(
                result.stdout,
                begin_marker=MODULE.REMOTE_SESSION_META_BEGIN,
                end_marker=MODULE.REMOTE_SESSION_META_END,
                host="embedded",
                command="session-meta",
            )
            self.assertEqual(
                [json.loads(line) for line in payload_lines],
                [{"kind": "error", "error": "session directory unreadable"}],
            )
            self.assertNotIn(secret_path, result.stdout)

    def test_session_meta_root_permission_error_uses_cli_error_channel(self) -> None:
        secret_root = "/sensitive/codex/root"
        output = io.StringIO()
        error_output = io.StringIO()
        with (
            mock.patch.object(
                MODULE,
                "_open_pinned_codex_root",
                side_effect=PermissionError(13, "Permission denied", secret_root),
            ),
            redirect_stdout(output),
            redirect_stderr(error_output),
        ):
            rc = MODULE.cmd_session_meta(
                argparse.Namespace(
                    host=["local"],
                    date=["2026/05/26"],
                    from_date=None,
                    to_date=None,
                    limit=10,
                )
            )

        self.assertEqual(rc, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(
            error_output.getvalue(),
            "host=local\nerror=session directory unreadable\n",
        )
        self.assertNotIn(secret_root, error_output.getvalue())

    def test_session_meta_scandir_errors_fail_closed_without_path_leak(self) -> None:
        secret_path = "/sensitive/session/directory"
        for scan_error in (
            PermissionError(13, "Permission denied", secret_path),
            OSError(5, "Input/output error", secret_path),
        ):
            with (
                self.subTest(error_type=type(scan_error).__name__),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                codex_root = Path(temp_dir) / ".codex"
                write_rollout(
                    codex_root,
                    ['{"type":"session_meta","payload":{"id":"trusted"}}'],
                )
                with (
                    mock.patch.object(MODULE.os, "scandir", side_effect=scan_error),
                    self.assertRaises(MODULE.SessionMetaRolloutError) as raised,
                ):
                    MODULE._scan_session_meta_records(
                        codex_root=codex_root,
                        dates=[MODULE.dt.date(2026, 5, 26)],
                        limit=10,
                        host="local",
                    )

                self.assertEqual(raised.exception.error, "session directory unreadable")
                self.assertIsNone(raised.exception.rollout)
                self.assertNotIn(secret_path, str(raised.exception))

    def test_session_meta_scandir_missing_after_open_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            write_rollout(
                codex_root,
                ['{"type":"session_meta","payload":{"id":"vanished"}}'],
            )
            with (
                mock.patch.object(
                    MODULE.os,
                    "scandir",
                    side_effect=FileNotFoundError("directory vanished"),
                ),
                self.assertRaises(MODULE.SessionMetaRolloutError) as raised,
            ):
                MODULE._scan_session_meta_records(
                    codex_root=codex_root,
                    dates=[MODULE.dt.date(2026, 5, 26)],
                    limit=10,
                    host="local",
                )

        self.assertEqual(raised.exception.error, "session directory unreadable")
        self.assertIsNone(raised.exception.rollout)
        self.assertNotIn("directory vanished", str(raised.exception))

    def test_embedded_session_meta_scandir_error_uses_path_neutral_frame(self) -> None:
        secret_path = "/sensitive/session/directory"
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            write_rollout(
                codex_root,
                ['{"type":"session_meta","payload":{"id":"trusted"}}'],
            )
            script = MODULE._remote_python_script(
                {
                    "mode": "session-meta",
                    "dates": ["2026/05/26"],
                    "limit": 10,
                    "codex_root": str(codex_root),
                    "session_meta_scan_bytes": MODULE.MAX_SESSION_META_SCAN_BYTES,
                }
            )
            marker = 'if CONFIG["mode"] == "session-meta":\n'
            injection = (
                "def injected_scandir_failure(*args, **kwargs):\n"
                f"    raise OSError(5, 'Input/output error', {secret_path!r})\n\n"
                "os.scandir = injected_scandir_failure\n\n"
            )
            self.assertEqual(script.count(marker), 1)
            script = script.replace(marker, injection + marker)
            result = subprocess.run(
                [sys.executable, "-"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload_lines = MODULE._extract_framed_lines(
            result.stdout,
            begin_marker=MODULE.REMOTE_SESSION_META_BEGIN,
            end_marker=MODULE.REMOTE_SESSION_META_END,
            host="embedded",
            command="session-meta",
        )
        self.assertEqual(
            [json.loads(line) for line in payload_lines],
            [{"kind": "error", "error": "session directory unreadable"}],
        )
        self.assertNotIn(secret_path, result.stdout)
        self.assertNotIn(secret_path, result.stderr)

    def test_embedded_session_meta_scandir_missing_after_open_fails_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            write_rollout(
                codex_root,
                ['{"type":"session_meta","payload":{"id":"vanished"}}'],
            )
            script = MODULE._remote_python_script(
                {
                    "mode": "session-meta",
                    "dates": ["2026/05/26"],
                    "limit": 10,
                    "codex_root": str(codex_root),
                    "session_meta_scan_bytes": MODULE.MAX_SESSION_META_SCAN_BYTES,
                }
            )
            marker = 'if CONFIG["mode"] == "session-meta":\n'
            injection = (
                "def injected_scandir_missing(*args, **kwargs):\n"
                "    raise FileNotFoundError('directory vanished')\n\n"
                "os.scandir = injected_scandir_missing\n\n"
            )
            self.assertEqual(script.count(marker), 1)
            script = script.replace(marker, injection + marker)
            result = subprocess.run(
                [sys.executable, "-"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload_lines = MODULE._extract_framed_lines(
            result.stdout,
            begin_marker=MODULE.REMOTE_SESSION_META_BEGIN,
            end_marker=MODULE.REMOTE_SESSION_META_END,
            host="embedded",
            command="session-meta",
        )
        self.assertEqual(
            [json.loads(line) for line in payload_lines],
            [{"kind": "error", "error": "session directory unreadable"}],
        )
        self.assertNotIn("directory vanished", result.stdout)
        self.assertNotIn("directory vanished", result.stderr)

    def test_session_meta_parent_dup_failure_closes_rollout_and_stays_framed(
        self,
    ) -> None:
        secret_error = "/sensitive/dup-failure"
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(
                codex_root,
                ['{"type":"session_meta","payload":{"id":"trusted"}}'],
            )
            rollout_name = Path(rollout).name
            real_open = MODULE.os.open
            real_dup = MODULE.os.dup
            dup_calls = 0
            rollout_fds: list[int] = []

            def tracking_open(path, *args, **kwargs):
                fd = real_open(path, *args, **kwargs)
                if path == rollout_name and kwargs.get("dir_fd") is not None:
                    rollout_fds.append(fd)
                return fd

            def fail_parent_dup(fd: int) -> int:
                nonlocal dup_calls
                dup_calls += 1
                if dup_calls == 2:
                    raise OSError(24, "Too many open files", secret_error)
                return real_dup(fd)

            with (
                mock.patch.object(MODULE.os, "open", tracking_open),
                mock.patch.object(MODULE.os, "dup", fail_parent_dup),
                self.assertRaises(MODULE.SessionMetaRolloutError) as raised,
            ):
                MODULE._scan_session_meta_records(
                    codex_root=codex_root,
                    dates=[MODULE.dt.date(2026, 5, 26)],
                    limit=10,
                    host="local",
                )

            self.assertEqual(raised.exception.error, "rollout unreadable")
            self.assertEqual(raised.exception.rollout, rollout)
            self.assertNotIn(secret_error, str(raised.exception))
            self.assertEqual(len(rollout_fds), 1)
            with self.assertRaises(OSError):
                os.fstat(rollout_fds[0])

            script = MODULE._remote_python_script(
                {
                    "mode": "session-meta",
                    "dates": ["2026/05/26"],
                    "limit": 10,
                    "codex_root": str(codex_root),
                    "session_meta_scan_bytes": MODULE.MAX_SESSION_META_SCAN_BYTES,
                }
            )
            marker = 'if CONFIG["mode"] == "session-meta":\n'
            injection = (
                "_real_dup = os.dup\n"
                "_dup_calls = 0\n"
                "def injected_parent_dup(fd):\n"
                "    global _dup_calls\n"
                "    _dup_calls += 1\n"
                "    if _dup_calls == 2:\n"
                f"        raise OSError(24, 'Too many open files', {secret_error!r})\n"
                "    return _real_dup(fd)\n"
                "os.dup = injected_parent_dup\n\n"
            )
            self.assertEqual(script.count(marker), 1)
            script = script.replace(marker, injection + marker)
            result = subprocess.run(
                [sys.executable, "-"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        payload_lines = MODULE._extract_framed_lines(
            result.stdout,
            begin_marker=MODULE.REMOTE_SESSION_META_BEGIN,
            end_marker=MODULE.REMOTE_SESSION_META_END,
            host="embedded",
            command="session-meta",
        )
        self.assertEqual(
            [json.loads(line) for line in payload_lines],
            [{"kind": "error", "error": "rollout unreadable", "rollout": rollout}],
        )
        self.assertNotIn(secret_error, result.stdout)

    def test_session_meta_cap_never_accepts_unterminated_json_prefix(self) -> None:
        session_meta = json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": "must-not-escape", "cwd": "/repo"},
            },
            separators=(",", ":"),
        )
        scan_bytes = len(session_meta.encode("utf-8"))
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            write_rollout(codex_root, [session_meta + " trailing-junk"])
            output = io.StringIO()
            error_output = io.StringIO()
            with (
                mock.patch.object(MODULE, "_local_codex_root", return_value=codex_root),
                mock.patch.object(MODULE, "MAX_SESSION_META_SCAN_BYTES", scan_bytes),
                redirect_stdout(output),
                redirect_stderr(error_output),
            ):
                rc = MODULE.cmd_session_meta(
                    argparse.Namespace(
                        host=["local"],
                        date=["2026/05/26"],
                        from_date=None,
                        to_date=None,
                        limit=10,
                    )
                )

            script = MODULE._remote_python_script(
                {
                    "mode": "session-meta",
                    "dates": ["2026/05/26"],
                    "limit": 10,
                    "codex_root": str(codex_root),
                    "session_meta_scan_bytes": scan_bytes,
                }
            )
            embedded = subprocess.run(
                [sys.executable, "-"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(rc, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn(MODULE.SESSION_META_SCAN_TRUNCATED_ERROR, error_output.getvalue())
        self.assertNotIn("must-not-escape", output.getvalue())
        self.assertEqual(embedded.returncode, 0, embedded.stderr)
        embedded_items = [
            json.loads(line)
            for line in MODULE._extract_framed_lines(
                embedded.stdout,
                begin_marker=MODULE.REMOTE_SESSION_META_BEGIN,
                end_marker=MODULE.REMOTE_SESSION_META_END,
                host="embedded",
                command="session-meta",
            )
        ]
        self.assertEqual(
            embedded_items[0]["error"], MODULE.SESSION_META_SCAN_TRUNCATED_ERROR
        )
        self.assertNotIn("must-not-escape", embedded.stdout)

    def test_session_meta_complete_line_and_newline_at_cap_is_accepted(self) -> None:
        session_meta = json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": "complete-at-cap", "cwd": "/repo"},
            },
            separators=(",", ":"),
        )
        line = (session_meta + "\n").encode("utf-8")
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(codex_root, [session_meta, "trailing"])
            with mock.patch.object(MODULE, "MAX_SESSION_META_SCAN_BYTES", len(line)):
                scan = MODULE._scan_session_meta_records(
                    codex_root=codex_root,
                    dates=[MODULE.dt.date(2026, 5, 26)],
                    limit=10,
                    host="local",
                )

        self.assertEqual(scan.rows[0]["session_id"], "complete-at-cap")
        self.assertEqual(scan.rows[0]["rollout"], rollout)

    def test_session_meta_raw_reads_never_exceed_scan_cap_local_and_embedded(
        self,
    ) -> None:
        lines = [
            '{"type":"other","payload":{}}',
            '{"type":"session_meta","payload":{"id":"bounded-raw-read","cwd":"/repo"}}',
            json.dumps(
                {"type": "response_item", "payload": "x" * 16_384},
                separators=(",", ":"),
            ),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(codex_root, lines)
            scan_bytes = sum(len(line.encode("utf-8")) + 1 for line in lines[:2])
            self.assertLess(scan_bytes, io.DEFAULT_BUFFER_SIZE)
            self.assertGreater((codex_root / rollout).stat().st_size, io.DEFAULT_BUFFER_SIZE)
            local_reads: list[tuple[int, int]] = []
            real_os_read = MODULE.os.read

            def guarded_local_os_read(file_descriptor: int, size: int) -> bytes:
                consumed = sum(returned for _requested, returned in local_reads)
                remaining = scan_bytes - consumed
                if size < 0 or size > remaining:
                    raise AssertionError(
                        f"os.read requested {size}, remaining cap is {remaining}"
                    )
                data = real_os_read(file_descriptor, size)
                local_reads.append((size, len(data)))
                return data

            with (
                mock.patch.object(
                    MODULE,
                    "MAX_SESSION_META_SCAN_BYTES",
                    scan_bytes,
                ),
                mock.patch.object(
                    MODULE.os,
                    "read",
                    guarded_local_os_read,
                ),
            ):
                scan = MODULE._scan_session_meta_records(
                    codex_root=codex_root,
                    dates=[MODULE.dt.date(2026, 5, 26)],
                    limit=10,
                    host="local",
                )

            self.assertEqual(scan.rows[0]["session_id"], "bounded-raw-read")
            self.assertEqual(sum(returned for _size, returned in local_reads), scan_bytes)
            self.assertTrue(local_reads)
            self.assertLessEqual(
                sum(requested for requested, _returned in local_reads),
                scan_bytes,
            )

            script = MODULE._remote_python_script(
                {
                    "mode": "session-meta",
                    "dates": ["2026/05/26"],
                    "limit": 10,
                    "codex_root": str(codex_root),
                    "session_meta_scan_bytes": scan_bytes,
                }
            )
            audit_path = Path(temp_dir) / "embedded-os-read-bytes.txt"
            marker = 'if CONFIG["mode"] == "session-meta":\n'
            injection = (
                "_real_os_read = os.read\n"
                "_embedded_os_read_bytes = 0\n"
                "def guarded_embedded_os_read(file_descriptor, size):\n"
                "    global _embedded_os_read_bytes\n"
                "    remaining = SESSION_META_SCAN_BYTES - _embedded_os_read_bytes\n"
                "    if size < 0 or size > remaining:\n"
                "        raise AssertionError('embedded os.read exceeded session-meta cap')\n"
                "    data = _real_os_read(file_descriptor, size)\n"
                "    _embedded_os_read_bytes += len(data)\n"
                f"    pathlib.Path({str(audit_path)!r}).write_text(str(_embedded_os_read_bytes), encoding='utf-8')\n"
                "    return data\n"
                "os.read = guarded_embedded_os_read\n\n"
            )
            self.assertEqual(script.count(marker), 1)
            self.assertIn("chunk = os.read(file_descriptor, remaining)", script)
            self.assertNotIn("raw_line = handle.readline(remaining)", script)
            script = script.replace(marker, injection + marker)
            result = subprocess.run(
                [sys.executable, "-"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
            )
            embedded_read_bytes = int(audit_path.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(embedded_read_bytes, scan_bytes)
        payload_lines = MODULE._extract_framed_lines(
            result.stdout,
            begin_marker=MODULE.REMOTE_SESSION_META_BEGIN,
            end_marker=MODULE.REMOTE_SESSION_META_END,
            host="embedded",
            command="session-meta",
        )
        self.assertEqual(
            [json.loads(line)["session_id"] for line in payload_lines],
            ["bounded-raw-read"],
        )

    def test_session_meta_rejects_bare_carriage_return_locally_and_embedded(
        self,
    ) -> None:
        session_meta = json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": "bare-cr-must-not-escape", "cwd": "/repo"},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        bare_cr_record = session_meta + b"\r"
        rollout = "sessions/2026/05/26/rollout-2026-05-26T10-00-00-bare-cr.jsonl"
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout_path = codex_root / rollout
            rollout_path.parent.mkdir(parents=True)
            rollout_path.write_bytes(bare_cr_record)
            output = io.StringIO()
            error_output = io.StringIO()
            with (
                mock.patch.object(MODULE, "_local_codex_root", return_value=codex_root),
                mock.patch.object(
                    MODULE,
                    "MAX_SESSION_META_SCAN_BYTES",
                    len(bare_cr_record),
                ),
                redirect_stdout(output),
                redirect_stderr(error_output),
            ):
                rc = MODULE.cmd_session_meta(
                    argparse.Namespace(
                        host=["local"],
                        date=["2026/05/26"],
                        from_date=None,
                        to_date=None,
                        limit=10,
                    )
                )

            script = MODULE._remote_python_script(
                {
                    "mode": "session-meta",
                    "dates": ["2026/05/26"],
                    "limit": 10,
                    "codex_root": str(codex_root),
                    "session_meta_scan_bytes": len(bare_cr_record),
                }
            )
            embedded = subprocess.run(
                [sys.executable, "-"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(rc, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn(MODULE.SESSION_META_SCAN_TRUNCATED_ERROR, error_output.getvalue())
        self.assertNotIn("bare-cr-must-not-escape", output.getvalue())
        self.assertEqual(embedded.returncode, 0, embedded.stderr)
        embedded_items = [
            json.loads(line)
            for line in MODULE._extract_framed_lines(
                embedded.stdout,
                begin_marker=MODULE.REMOTE_SESSION_META_BEGIN,
                end_marker=MODULE.REMOTE_SESSION_META_END,
                host="embedded",
                command="session-meta",
            )
        ]
        self.assertEqual(
            embedded_items,
            [
                {
                    "kind": "error",
                    "error": MODULE.SESSION_META_SCAN_TRUNCATED_ERROR,
                    "rollout": rollout,
                }
            ],
        )
        self.assertNotIn("bare-cr-must-not-escape", embedded.stdout)

    def test_session_meta_accepts_crlf_locally_and_embedded(self) -> None:
        session_meta = json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": "crlf-session", "cwd": "/repo"},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        crlf_record = session_meta + b"\r\n"
        rollout = "sessions/2026/05/26/rollout-2026-05-26T10-00-00-crlf.jsonl"
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout_path = codex_root / rollout
            rollout_path.parent.mkdir(parents=True)
            rollout_path.write_bytes(crlf_record)
            with mock.patch.object(
                MODULE,
                "MAX_SESSION_META_SCAN_BYTES",
                len(crlf_record),
            ):
                scan = MODULE._scan_session_meta_records(
                    codex_root=codex_root,
                    dates=[MODULE.dt.date(2026, 5, 26)],
                    limit=10,
                    host="local",
                )

            script = MODULE._remote_python_script(
                {
                    "mode": "session-meta",
                    "dates": ["2026/05/26"],
                    "limit": 10,
                    "codex_root": str(codex_root),
                    "session_meta_scan_bytes": len(crlf_record),
                }
            )
            embedded = subprocess.run(
                [sys.executable, "-"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(scan.rows[0]["session_id"], "crlf-session")
        self.assertEqual(scan.rows[0]["rollout"], rollout)
        self.assertEqual(embedded.returncode, 0, embedded.stderr)
        embedded_items = [
            json.loads(line)
            for line in MODULE._extract_framed_lines(
                embedded.stdout,
                begin_marker=MODULE.REMOTE_SESSION_META_BEGIN,
                end_marker=MODULE.REMOTE_SESSION_META_END,
                host="embedded",
                command="session-meta",
            )
        ]
        self.assertEqual(embedded_items[0]["session_id"], "crlf-session")
        self.assertEqual(embedded_items[0]["rollout"], rollout)

    def test_session_meta_rejects_current_entry_replacement_after_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(
                codex_root,
                ['{"type":"session_meta","payload":{"id":"trusted"}}'],
            )
            source_path = codex_root / rollout
            original_reader = MODULE._read_bounded_session_meta

            def read_then_replace(*args: object, **kwargs: object):
                result = original_reader(*args, **kwargs)
                replacement = source_path.with_suffix(".replacement")
                replacement.write_text(
                    '{"type":"session_meta","payload":{"id":"external-sentinel"}}\n',
                    encoding="utf-8",
                )
                os.replace(replacement, source_path)
                return result

            with (
                mock.patch.object(
                    MODULE,
                    "_read_bounded_session_meta",
                    side_effect=read_then_replace,
                ),
                self.assertRaises(MODULE.SessionMetaRolloutError) as raised,
            ):
                MODULE._scan_session_meta_records(
                    codex_root=codex_root,
                    dates=[MODULE.dt.date(2026, 5, 26)],
                    limit=10,
                    host="local",
                )

        self.assertEqual(raised.exception.error, "rollout unreadable")
        self.assertEqual(raised.exception.rollout, rollout)
        self.assertNotIn("external-sentinel", str(raised.exception))

    def test_private_output_rejects_parent_symlink_swap_after_resolution(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            root = Path(temp_dir).resolve()
            output_parent = root / "output"
            output_parent.mkdir()
            output = MODULE._resolve_output_path(str(output_parent / "chunk.jsonl"))
            moved_parent = root / "moved-output"
            escape_parent = root / "escape"
            escape_parent.mkdir()
            os.replace(output_parent, moved_parent)
            output_parent.symlink_to(escape_parent, target_is_directory=True)

            with self.assertRaises(OSError):
                MODULE._write_private_bytes(output, b"sensitive\n")

            self.assertFalse((escape_parent / output.name).exists())
            self.assertFalse((moved_parent / output.name).exists())

    def test_private_output_rename_stays_on_pinned_parent_descriptor(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            root = Path(temp_dir).resolve()
            output_parent = root / "output"
            output_parent.mkdir()
            output = MODULE._resolve_output_path(str(output_parent / "chunk.jsonl"))
            moved_parent = root / "moved-output"
            escape_parent = root / "escape"
            escape_parent.mkdir()
            real_replace = os.replace
            replace_dir_fds: list[tuple[int | None, int | None]] = []

            def swap_parent_then_replace(
                src: str,
                dst: str,
                *,
                src_dir_fd: int | None = None,
                dst_dir_fd: int | None = None,
            ) -> None:
                replace_dir_fds.append((src_dir_fd, dst_dir_fd))
                real_replace(output_parent, moved_parent)
                output_parent.symlink_to(escape_parent, target_is_directory=True)
                real_replace(
                    src,
                    dst,
                    src_dir_fd=src_dir_fd,
                    dst_dir_fd=dst_dir_fd,
                )

            with mock.patch.object(
                MODULE.os, "replace", side_effect=swap_parent_then_replace
            ):
                MODULE._write_private_bytes(output, b"sensitive\n")

            written_output = moved_parent / output.name
            self.assertEqual(written_output.read_bytes(), b"sensitive\n")
            self.assertEqual(written_output.stat().st_mode & 0o777, 0o600)
            self.assertFalse((escape_parent / output.name).exists())
            self.assertEqual(len(replace_dir_fds), 1)
            self.assertIsNotNone(replace_dir_fds[0][0])
            self.assertEqual(replace_dir_fds[0][0], replace_dir_fds[0][1])

    def test_rollout_readers_stay_on_pinned_ancestor_after_swap(self) -> None:
        operations = (
            "rollout-stat",
            "rollout-summary",
            "chunked-rollout-summary",
            "fetch-rollout",
            "fetch-rollout-chunk",
            "session-meta",
        )
        for operation in operations:
            with (
                self.subTest(operation=operation),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                base = Path(temp_dir)
                codex_root = base / ".codex"
                external_root = base / "external-codex"
                trusted_line = json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {"id": "trusted-session", "cwd": "/trusted"},
                    },
                    separators=(",", ":"),
                )
                sentinel_line = json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {"id": "external-sentinel", "cwd": "/external"},
                    },
                    separators=(",", ":"),
                )
                rollout = write_rollout(codex_root, [trusted_line])
                write_rollout(external_root, [sentinel_line])
                rollout_relative = MODULE._resolve_rollout_relative_path(rollout)
                trusted_data = (codex_root / rollout).read_bytes()
                identity = rollout_identity(codex_root, rollout)
                moved_sessions = codex_root / "sessions-pinned"
                external_sessions = external_root / "sessions"
                real_open = MODULE.os.open
                swapped = False

                def swap_after_sessions_open(
                    path: object,
                    flags: int,
                    mode: int = 0o777,
                    *,
                    dir_fd: int | None = None,
                ) -> int:
                    nonlocal swapped
                    fd = real_open(path, flags, mode, dir_fd=dir_fd)
                    if path == "sessions" and dir_fd is not None and not swapped:
                        os.replace(codex_root / "sessions", moved_sessions)
                        (codex_root / "sessions").symlink_to(
                            external_sessions,
                            target_is_directory=True,
                        )
                        swapped = True
                    return fd

                with mock.patch.object(
                    MODULE.os,
                    "open",
                    side_effect=swap_after_sessions_open,
                ):
                    if operation == "rollout-stat":
                        result = MODULE._stat_local_rollout_identity(
                            codex_root,
                            rollout_relative,
                        )
                        self.assertEqual(result, identity)
                    elif operation == "rollout-summary":
                        output = io.StringIO()
                        error_output = io.StringIO()
                        with (
                            mock.patch.object(
                                MODULE,
                                "_local_codex_root",
                                return_value=codex_root,
                            ),
                            redirect_stdout(output),
                            redirect_stderr(error_output),
                        ):
                            rc = MODULE.cmd_rollout_summary(
                                argparse.Namespace(
                                    host="local",
                                    rollout=rollout,
                                    keyword=[],
                                    limit=20,
                                    tail_records=4,
                                    max_text_chars=200,
                                )
                            )
                        self.assertEqual(rc, 0, error_output.getvalue())
                        self.assertIn("trusted-session", output.getvalue())
                        self.assertNotIn("external-sentinel", output.getvalue())
                    elif operation == "chunked-rollout-summary":
                        with mock.patch.object(MODULE, "MIN_ROLLOUT_CHUNK_BYTES", 1):
                            records = MODULE._chunked_rollout_summary_records(
                                codex_root=codex_root,
                                rollout_relative_path=rollout_relative,
                                chunk_bytes=identity.size,
                                keywords=[],
                                limit_per_chunk=20,
                                tail_records=0,
                                max_text_chars=200,
                                host="local",
                                expected_identity=identity,
                                authorized_source_bytes=None,
                            )
                        encoded = json.dumps(records)
                        self.assertIn("trusted-session", encoded)
                        self.assertNotIn("external-sentinel", encoded)
                    elif operation == "fetch-rollout":
                        data = MODULE._read_local_rollout_bytes(
                            codex_root,
                            rollout_relative,
                            max_bytes=MODULE.MAX_FETCH_ROLLOUT_BYTES,
                        )
                        self.assertEqual(data, trusted_data)
                    elif operation == "fetch-rollout-chunk":
                        data = MODULE._read_local_rollout_byte_range(
                            codex_root,
                            rollout_relative,
                            byte_start=0,
                            byte_end=len(trusted_data),
                            max_bytes=MODULE.MAX_FETCH_ROLLOUT_CHUNK_BYTES,
                            expected_identity=identity,
                        )
                        self.assertEqual(data, trusted_data)
                    else:
                        scan = MODULE._scan_session_meta_records(
                            codex_root=codex_root,
                            dates=[MODULE.dt.date(2026, 5, 26)],
                            limit=10,
                            host="local",
                        )
                        self.assertEqual(scan.rows[0]["session_id"], "trusted-session")
                        self.assertNotIn("external-sentinel", json.dumps(scan.rows))

                self.assertTrue(swapped)

    def test_rollout_root_swap_between_stat_and_open_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            codex_root = base / ".codex"
            external_root = base / "external-codex"
            rollout = write_rollout(
                codex_root,
                ['{"type":"session_meta","payload":{"id":"trusted"}}'],
            )
            write_rollout(
                external_root,
                ['{"type":"session_meta","payload":{"id":"external-sentinel"}}'],
            )
            rollout_relative = MODULE._resolve_rollout_relative_path(rollout)
            resolved_root = codex_root.resolve()
            moved_root = base / ".codex-pinned"
            real_open = MODULE.os.open
            swapped = False

            def swap_root_before_open(
                path: object,
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                nonlocal swapped
                if str(path) == str(resolved_root) and dir_fd is None and not swapped:
                    os.replace(codex_root, moved_root)
                    os.replace(external_root, codex_root)
                    swapped = True
                return real_open(path, flags, mode, dir_fd=dir_fd)

            with (
                mock.patch.object(MODULE.os, "open", side_effect=swap_root_before_open),
                self.assertRaisesRegex(ValueError, "Codex root changed during open"),
            ):
                MODULE._read_local_rollout_bytes(
                    codex_root,
                    rollout_relative,
                    max_bytes=MODULE.MAX_FETCH_ROLLOUT_BYTES,
                )

        self.assertTrue(swapped)

    def test_rollout_root_swap_between_lstat_and_resolve_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            codex_root = base / ".codex"
            external_root = base / "external-codex"
            rollout = write_rollout(
                codex_root,
                ['{"type":"session_meta","payload":{"id":"trusted"}}'],
            )
            write_rollout(
                external_root,
                ['{"type":"session_meta","payload":{"id":"external-sentinel"}}'],
            )
            rollout_relative = MODULE._resolve_rollout_relative_path(rollout)
            moved_root = base / ".codex-pinned"
            real_resolve = MODULE.pathlib.Path.resolve
            swapped = False

            def swap_root_before_resolve(path: Path, *args, **kwargs) -> Path:
                nonlocal swapped
                if path == codex_root and not swapped:
                    os.replace(codex_root, moved_root)
                    os.replace(external_root, codex_root)
                    swapped = True
                return real_resolve(path, *args, **kwargs)

            with (
                mock.patch.object(
                    MODULE.pathlib.Path,
                    "resolve",
                    swap_root_before_resolve,
                ),
                self.assertRaisesRegex(
                    ValueError,
                    "Codex root changed during resolution",
                ),
            ):
                MODULE._read_local_rollout_bytes(
                    codex_root,
                    rollout_relative,
                    max_bytes=MODULE.MAX_FETCH_ROLLOUT_BYTES,
                )

        self.assertTrue(swapped)

    def test_rollout_ancestor_swap_between_stat_and_open_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            codex_root = base / ".codex"
            external_root = base / "external-codex"
            rollout = write_rollout(
                codex_root,
                ['{"type":"session_meta","payload":{"id":"trusted"}}'],
            )
            write_rollout(
                external_root,
                ['{"type":"session_meta","payload":{"id":"external-sentinel"}}'],
            )
            rollout_relative = MODULE._resolve_rollout_relative_path(rollout)
            moved_sessions = codex_root / "sessions-pinned"
            real_open = MODULE.os.open
            swapped = False

            def swap_sessions_before_open(
                path: object,
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                nonlocal swapped
                if path == "sessions" and dir_fd is not None and not swapped:
                    os.replace(codex_root / "sessions", moved_sessions)
                    os.replace(external_root / "sessions", codex_root / "sessions")
                    swapped = True
                return real_open(path, flags, mode, dir_fd=dir_fd)

            with (
                mock.patch.object(
                    MODULE.os,
                    "open",
                    side_effect=swap_sessions_before_open,
                ),
                self.assertRaisesRegex(ValueError, "path ancestor changed during open"),
            ):
                MODULE._read_local_rollout_bytes(
                    codex_root,
                    rollout_relative,
                    max_bytes=MODULE.MAX_FETCH_ROLLOUT_BYTES,
                )

        self.assertTrue(swapped)

    def test_regular_file_open_flags_fail_closed_without_nonblock(self) -> None:
        with (
            mock.patch.object(MODULE.os, "O_NONBLOCK", None),
            self.assertRaisesRegex(OSError, "secure rollout reads require O_NONBLOCK"),
        ):
            MODULE._regular_file_open_flags()

    def test_relative_path_validator_rejects_absolute_local_and_embedded(
        self,
    ) -> None:
        absolute_path = MODULE.pathlib.PurePosixPath(
            "/tmp/rollout-absolute-must-not-open.jsonl"
        )
        with self.assertRaisesRegex(ValueError, "path must stay under Codex root"):
            MODULE._validate_relative_path_parts(absolute_path)

        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            codex_root.mkdir()
            script = MODULE._remote_python_script(
                {
                    "mode": "session-meta",
                    "dates": [],
                    "limit": 10,
                    "codex_root": str(codex_root),
                    "session_meta_scan_bytes": MODULE.MAX_SESSION_META_SCAN_BYTES,
                }
            )
            marker = 'if CONFIG["mode"] == "session-meta":\n'
            injection = (
                "try:\n"
                f"    validate_relative_path_parts(pathlib.PurePosixPath({absolute_path.as_posix()!r}))\n"
                "except ValueError as error:\n"
                "    if str(error) != 'path must stay under Codex root':\n"
                "        raise\n"
                "else:\n"
                "    raise AssertionError('absolute path passed embedded validator')\n\n"
            )
            self.assertEqual(script.count(marker), 1)
            self.assertIn("if rel.is_absolute():", script)
            script = script.replace(marker, injection + marker)
            result = subprocess.run(
                [sys.executable, "-"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        payload_lines = MODULE._extract_framed_lines(
            result.stdout,
            begin_marker=MODULE.REMOTE_SESSION_META_BEGIN,
            end_marker=MODULE.REMOTE_SESSION_META_END,
            host="embedded",
            command="session-meta",
        )
        self.assertEqual(payload_lines, [])

    @unittest.skipUnless(
        hasattr(os, "mkfifo") and hasattr(os, "O_NONBLOCK"),
        "FIFO nonblocking opens require POSIX mkfifo and O_NONBLOCK",
    )
    def test_rollout_fifo_swap_before_open_fails_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(
                codex_root,
                ['{"type":"session_meta","payload":{"id":"trusted"}}'],
            )
            rollout_relative = MODULE._resolve_rollout_relative_path(rollout)
            rollout_path = codex_root / rollout
            moved_rollout = rollout_path.with_suffix(".pinned")
            real_open = MODULE.os.open
            opened_flags: list[int] = []
            swapped = False

            def swap_rollout_for_fifo_before_open(
                path: object,
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                nonlocal swapped
                if path == rollout_path.name and dir_fd is not None and not swapped:
                    os.replace(rollout_path, moved_rollout)
                    os.mkfifo(rollout_path, 0o600)
                    opened_flags.append(flags)
                    swapped = True
                    if not flags & os.O_NONBLOCK:
                        raise AssertionError("final rollout open omitted O_NONBLOCK")
                return real_open(path, flags, mode, dir_fd=dir_fd)

            with (
                mock.patch.object(
                    MODULE.os,
                    "open",
                    side_effect=swap_rollout_for_fifo_before_open,
                ),
                self.assertRaisesRegex(
                    ValueError, "rollout path is not a regular file"
                ),
            ):
                MODULE._read_local_rollout_bytes(
                    codex_root,
                    rollout_relative,
                    max_bytes=MODULE.MAX_FETCH_ROLLOUT_BYTES,
                )

        self.assertTrue(swapped)
        self.assertEqual(len(opened_flags), 1)
        self.assertTrue(opened_flags[0] & os.O_NONBLOCK)

    @unittest.skipUnless(
        hasattr(os, "mkfifo") and hasattr(os, "O_NONBLOCK"),
        "FIFO nonblocking opens require POSIX mkfifo and O_NONBLOCK",
    )
    def test_embedded_fetch_fifo_swap_before_open_fails_without_blocking(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(
                codex_root,
                ['{"type":"session_meta","payload":{"id":"trusted"}}'],
            )
            rollout_path = codex_root / rollout
            moved_rollout = rollout_path.with_suffix(".pinned")
            script = MODULE._remote_python_script(
                {
                    "mode": "fetch-rollout",
                    "rollout": rollout,
                    "codex_root": str(codex_root),
                    "max_fetch_rollout_bytes": MODULE.MAX_FETCH_ROLLOUT_BYTES,
                }
            )
            marker = (
                "    fd = os.open(name, regular_file_open_flags(), dir_fd=parent_fd)\n"
            )
            injection = (
                f"    if name == {rollout_path.name!r} and not globals().get('_fifo_swapped', False):\n"
                "        globals()['_fifo_swapped'] = True\n"
                f"        os.replace({str(rollout_path)!r}, {str(moved_rollout)!r})\n"
                f"        os.mkfifo({str(rollout_path)!r}, 0o600)\n" + marker
            )
            self.assertEqual(script.count(marker), 1)
            self.assertIn('getattr(os, "O_NONBLOCK", None)', script)
            self.assertIn("| nonblocking_flag", script)
            script = script.replace(marker, injection)
            result = subprocess.run(
                [sys.executable, "-"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
                timeout=3,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload_lines = MODULE._extract_framed_lines(
            result.stdout,
            begin_marker=MODULE.REMOTE_FETCH_ROLLOUT_BEGIN,
            end_marker=MODULE.REMOTE_FETCH_ROLLOUT_END,
            host="embedded",
            command="fetch-rollout",
        )
        self.assertEqual(
            [json.loads(line) for line in payload_lines],
            [{"ok": False, "error": "rollout path is not a regular file"}],
        )
        self.assertNotIn("trusted", result.stdout)

    def test_pinned_root_tolerates_canonical_ancestor_symlink(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(
                codex_root,
                ['{"type":"session_meta","payload":{"id":"trusted"}}'],
            )
            alias_root = Path("/tmp") / Path(temp_dir).name / ".codex"
            identity = MODULE._stat_local_rollout_identity(
                alias_root,
                MODULE._resolve_rollout_relative_path(rollout),
            )

        self.assertGreater(identity.size, 0)

    def test_embedded_fetch_stays_on_pinned_ancestor_after_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            codex_root = base / ".codex"
            external_root = base / "external-codex"
            rollout = write_rollout(
                codex_root,
                ['{"type":"session_meta","payload":{"id":"trusted"}}'],
            )
            write_rollout(
                external_root,
                ['{"type":"session_meta","payload":{"id":"external-sentinel"}}'],
            )
            trusted_data = (codex_root / rollout).read_bytes()
            moved_sessions = codex_root / "sessions-pinned"
            external_sessions = external_root / "sessions"
            script = MODULE._remote_python_script(
                {
                    "mode": "fetch-rollout",
                    "rollout": rollout,
                    "codex_root": str(codex_root),
                    "max_fetch_rollout_bytes": MODULE.MAX_FETCH_ROLLOUT_BYTES,
                }
            )
            marker = (
                "            next_fd = os.open(part, directory_open_flags(), "
                "dir_fd=directory_fd)\n"
            )
            injection = marker + (
                "            if part == 'sessions' and not globals().get('_ancestor_swapped', False):\n"
                "                globals()['_ancestor_swapped'] = True\n"
                f"                os.replace({str(codex_root / 'sessions')!r}, {str(moved_sessions)!r})\n"
                f"                os.symlink({str(external_sessions)!r}, {str(codex_root / 'sessions')!r}, target_is_directory=True)\n"
            )
            self.assertEqual(script.count(marker), 1)
            script = script.replace(marker, injection)
            result = subprocess.run(
                [sys.executable, "-"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        data = MODULE._extract_framed_fetch_rollout_payload(
            result.stdout,
            begin_marker=MODULE.REMOTE_FETCH_ROLLOUT_BEGIN,
            end_marker=MODULE.REMOTE_FETCH_ROLLOUT_END,
            host="embedded",
            command="fetch-rollout",
        )
        self.assertEqual(data, trusted_data)
        self.assertNotIn("external-sentinel", data.decode("utf-8"))

    def test_session_meta_preserves_distinct_lifecycle_paths_with_same_session_id(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            basename = "rollout-2026-05-26T10-00-00-same.jsonl"
            active_relative = Path("sessions/2026/05/26") / basename
            dated_relative = Path("archived_sessions/2026/05/26") / basename
            flat_relative = Path("archived_sessions") / basename
            write_session_meta_rollout(
                codex_root / active_relative,
                "shared-session",
                "/active",
                "Active follow-up only.",
            )
            write_session_meta_rollout(
                codex_root / dated_relative,
                "shared-session",
                "/dated",
                "Dated archive follow-up only.",
            )
            write_session_meta_rollout(
                codex_root / flat_relative,
                "shared-session",
                "/flat",
                "Flat archive follow-up only.",
            )

            local_scan = MODULE._scan_session_meta_records(
                codex_root=codex_root,
                dates=[MODULE.dt.date(2026, 5, 26), MODULE.dt.date(2026, 5, 26)],
                limit=10,
                host="local",
            )
            script = MODULE._remote_python_script(
                {
                    "mode": "session-meta",
                    "dates": ["2026/05/26", "2026/05/26"],
                    "limit": 10,
                    "codex_root": str(codex_root),
                    "session_meta_scan_bytes": MODULE.MAX_SESSION_META_SCAN_BYTES,
                }
            )
            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertIn(
                "Active follow-up only.",
                (codex_root / active_relative).read_text(encoding="utf-8"),
            )
            self.assertIn(
                "Dated archive follow-up only.",
                (codex_root / dated_relative).read_text(encoding="utf-8"),
            )
            self.assertIn(
                "Flat archive follow-up only.",
                (codex_root / flat_relative).read_text(encoding="utf-8"),
            )

        self.assertFalse(local_scan.truncated)
        self.assertEqual(result.returncode, 0, result.stderr)
        embedded_rows = [
            json.loads(line)
            for line in MODULE._extract_framed_lines(
                result.stdout,
                begin_marker=MODULE.REMOTE_SESSION_META_BEGIN,
                end_marker=MODULE.REMOTE_SESSION_META_END,
                host="embedded",
                command="session-meta",
            )
        ]
        local_projection = {
            (row["session_id"], row["cwd"], row["rollout"]) for row in local_scan.rows
        }
        embedded_projection = {
            (row["session_id"], row["cwd"], row["rollout"]) for row in embedded_rows
        }
        expected = {
            ("shared-session", "/active", active_relative.as_posix()),
            ("shared-session", "/dated", dated_relative.as_posix()),
            ("shared-session", "/flat", flat_relative.as_posix()),
        }
        self.assertEqual(local_projection, expected)
        self.assertEqual(embedded_projection, expected)
        self.assertEqual(
            {
                MODULE._session_meta_rollout_dedupe_key(
                    MODULE.pathlib.PurePosixPath(relative.as_posix())
                )
                for relative in (active_relative, dated_relative, flat_relative)
            },
            {
                active_relative.as_posix(),
                dated_relative.as_posix(),
                flat_relative.as_posix(),
            },
        )

    def test_session_meta_fails_closed_when_scan_cap_leaves_unread_bytes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(
                codex_root,
                [
                    json.dumps(
                        {
                            "type": "session_meta",
                            "payload": {
                                "padding": "x" * 512,
                                "id": "oversized-session",
                                "cwd": "/repo",
                            },
                        },
                        separators=(",", ":"),
                    )
                ],
            )
            scan_bytes = 64
            with mock.patch.object(MODULE, "MAX_SESSION_META_SCAN_BYTES", scan_bytes):
                with self.assertRaises(MODULE.SessionMetaRolloutError) as raised:
                    MODULE._scan_session_meta_records(
                        codex_root=codex_root,
                        dates=[MODULE.dt.date(2026, 5, 26)],
                        limit=10,
                        host="local",
                    )

            script = MODULE._remote_python_script(
                {
                    "mode": "session-meta",
                    "dates": ["2026/05/26"],
                    "limit": 10,
                    "codex_root": str(codex_root),
                    "session_meta_scan_bytes": scan_bytes,
                }
            )
            result = subprocess.run(
                [sys.executable, "-"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(
            raised.exception.error, MODULE.SESSION_META_SCAN_TRUNCATED_ERROR
        )
        self.assertEqual(raised.exception.rollout, rollout)
        self.assertEqual(result.returncode, 0, result.stderr)
        embedded_records = [
            json.loads(line)
            for line in MODULE._extract_framed_lines(
                result.stdout,
                begin_marker=MODULE.REMOTE_SESSION_META_BEGIN,
                end_marker=MODULE.REMOTE_SESSION_META_END,
                host="embedded",
                command="session-meta",
            )
        ]
        self.assertEqual(
            embedded_records,
            [
                {
                    "kind": "error",
                    "error": MODULE.SESSION_META_SCAN_TRUNCATED_ERROR,
                    "rollout": rollout,
                }
            ],
        )

    def test_session_meta_before_scan_cap_allows_larger_rollout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            meta_line = json.dumps(
                {
                    "type": "session_meta",
                    "payload": {"id": "bounded-session", "cwd": "/repo"},
                },
                separators=(",", ":"),
            )
            rollout = write_rollout(
                codex_root,
                [
                    meta_line,
                    json.dumps(
                        {"type": "response_item", "payload": "x" * 512},
                        separators=(",", ":"),
                    ),
                ],
            )
            scan_bytes = len(meta_line.encode("utf-8")) + 1
            with mock.patch.object(MODULE, "MAX_SESSION_META_SCAN_BYTES", scan_bytes):
                local_scan = MODULE._scan_session_meta_records(
                    codex_root=codex_root,
                    dates=[MODULE.dt.date(2026, 5, 26)],
                    limit=10,
                    host="local",
                )

            script = MODULE._remote_python_script(
                {
                    "mode": "session-meta",
                    "dates": ["2026/05/26"],
                    "limit": 10,
                    "codex_root": str(codex_root),
                    "session_meta_scan_bytes": scan_bytes,
                }
            )
            result = subprocess.run(
                [sys.executable, "-"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(
            [row["session_id"] for row in local_scan.rows], ["bounded-session"]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        embedded_records = [
            json.loads(line)
            for line in MODULE._extract_framed_lines(
                result.stdout,
                begin_marker=MODULE.REMOTE_SESSION_META_BEGIN,
                end_marker=MODULE.REMOTE_SESSION_META_END,
                host="embedded",
                command="session-meta",
            )
        ]
        self.assertEqual(
            [row["session_id"] for row in embedded_records], ["bounded-session"]
        )
        self.assertEqual(embedded_records[0]["rollout"], rollout)

    def test_session_meta_bounds_serialized_rows_local_and_embedded(self) -> None:
        row_limit = MODULE.MAX_REMOTE_SESSION_META_SERIALIZED_ROW_BYTES
        for serialized_bytes in (row_limit, row_limit + 1):
            with (
                self.subTest(serialized_bytes=serialized_bytes),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                codex_root = Path(temp_dir) / ".codex"
                rollout = (
                    "sessions/2026/05/26/"
                    "rollout-2026-05-26T10-00-00-example.jsonl"
                )
                base_row = {
                    "date": "2026/05/26",
                    "session_id": "bounded-row",
                    "cwd": "",
                    "rollout": rollout,
                }
                base_bytes = len(
                    json.dumps(
                        base_row,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8")
                )
                cwd_size = serialized_bytes - base_bytes
                expected_row = {**base_row, "cwd": "x" * cwd_size}
                self.assertEqual(
                    len(
                        json.dumps(
                            expected_row,
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode("utf-8")
                    ),
                    serialized_bytes,
                )
                rollout = write_rollout(
                    codex_root,
                    [
                        json.dumps(
                            {
                                "type": "session_meta",
                                "payload": {
                                    "id": "bounded-row",
                                    "cwd": "x" * cwd_size,
                                },
                            },
                            separators=(",", ":"),
                        )
                    ],
                )
                if serialized_bytes == row_limit:
                    local_scan = MODULE._scan_session_meta_records(
                        codex_root=codex_root,
                        dates=[MODULE.dt.date(2026, 5, 26)],
                        limit=10,
                        host="local",
                    )
                else:
                    with self.assertRaises(
                        MODULE.SessionMetaRolloutError
                    ) as raised:
                        MODULE._scan_session_meta_records(
                            codex_root=codex_root,
                            dates=[MODULE.dt.date(2026, 5, 26)],
                            limit=10,
                            host="local",
                        )
                    self.assertEqual(
                        raised.exception.error,
                        MODULE.SESSION_META_OUTPUT_ROW_TOO_LARGE_ERROR,
                    )
                    self.assertIsNone(raised.exception.rollout)
                script = MODULE._remote_python_script(
                    {
                        "mode": "session-meta",
                        "dates": ["2026/05/26"],
                        "limit": 10,
                        "codex_root": str(codex_root),
                        "session_meta_scan_bytes": MODULE.MAX_SESSION_META_SCAN_BYTES,
                    }
                )
                result = subprocess.run(
                    [sys.executable, "-"],
                    input=script,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload_lines = MODULE._extract_framed_lines(
                result.stdout,
                begin_marker=MODULE.REMOTE_SESSION_META_BEGIN,
                end_marker=MODULE.REMOTE_SESSION_META_END,
                host="embedded",
                command="session-meta",
            )
            records = [json.loads(line) for line in payload_lines]
            if serialized_bytes == row_limit:
                self.assertEqual(
                    {key: local_scan.rows[0][key] for key in expected_row},
                    expected_row,
                )
                self.assertEqual(records, [expected_row])
            else:
                self.assertEqual(
                    records,
                    [
                        {
                            "kind": "error",
                            "error": MODULE.SESSION_META_OUTPUT_ROW_TOO_LARGE_ERROR,
                        }
                    ],
                )
                self.assertNotIn("x" * 1024, result.stdout)

    def test_remote_python_script_compiles_for_chunk_commands(self) -> None:
        identity = MODULE.RolloutIdentity(120, 1, 2, 3, 4)
        token = MODULE._rollout_identity_token(identity)
        chunked_script = MODULE._remote_python_script(
            {
                "mode": "chunked-rollout-summary",
                "rollout": "sessions/2026/05/26/rollout-a.jsonl",
                "codex_root": "/home/hoteng/.codex",
                "summary_keywords": ["permission"],
                "summary_limit": 10,
                "summary_tail_records": 4,
                "summary_max_text_chars": 200,
                "chunk_bytes": MODULE.MIN_ROLLOUT_CHUNK_BYTES,
                "max_fetch_rollout_bytes": MODULE.MAX_FETCH_ROLLOUT_BYTES,
                "max_fetch_rollout_chunk_bytes": MODULE.MAX_FETCH_ROLLOUT_CHUNK_BYTES,
                "min_rollout_chunk_bytes": MODULE.MIN_ROLLOUT_CHUNK_BYTES,
                "max_rollout_chunk_bytes": MODULE.MAX_ROLLOUT_CHUNK_BYTES,
                "max_chunked_summary_output_bytes": MODULE.MAX_CHUNKED_ROLLOUT_SUMMARY_OUTPUT_BYTES,
                "max_fetch_range_plan_entries": MODULE.MAX_FETCH_RANGE_PLAN_ENTRIES,
                "expected_source_bytes": identity.size,
                "expected_source_identity": token,
                "authorized_source_bytes": None,
                "output_host": "miku-bot-dev",
            }
        )
        fetch_script = MODULE._remote_python_script(
            {
                "mode": "fetch-rollout-chunk",
                "rollout": "sessions/2026/05/26/rollout-a.jsonl",
                "codex_root": "/home/hoteng/.codex",
                "byte_start": 0,
                "byte_end": 120,
                "max_fetch_rollout_bytes": MODULE.MAX_FETCH_ROLLOUT_BYTES,
                "max_fetch_rollout_chunk_bytes": MODULE.MAX_FETCH_ROLLOUT_CHUNK_BYTES,
                "expected_source_bytes": identity.size,
                "expected_source_identity": token,
                "authorized_source_bytes": None,
            }
        )
        full_fetch_script = MODULE._remote_python_script(
            {
                "mode": "fetch-rollout",
                "rollout": "sessions/2026/05/26/rollout-a.jsonl",
                "codex_root": "/home/hoteng/.codex",
                "max_fetch_rollout_bytes": MODULE.MAX_FETCH_ROLLOUT_BYTES,
            }
        )
        summary_script = MODULE._remote_python_script(
            {
                "mode": "rollout-summary",
                "rollout": "sessions/2026/05/26/rollout-a.jsonl",
                "codex_root": "/home/hoteng/.codex",
                "summary_keywords": ["distant needle"],
                "summary_limit": 10,
                "summary_scan_bytes": MODULE.MAX_ROLLOUT_SUMMARY_SCAN_BYTES,
                "summary_tail_records": 0,
                "summary_max_text_chars": 200,
            }
        )

        compile(chunked_script, "<chunked-rollout-summary>", "exec")
        compile(fetch_script, "<fetch-rollout-chunk>", "exec")
        compile(full_fetch_script, "<fetch-rollout>", "exec")
        compile(summary_script, "<rollout-summary>", "exec")
        self.assertIn(
            '"full_fetch_limit_bytes": MAX_FETCH_ROLLOUT_BYTES', chunked_script
        )
        self.assertIn(
            '"full_reconstruction_allowed": automatic_allowed or AUTHORIZED_SOURCE_BYTES == source_identity["size"]',
            chunked_script,
        )
        self.assertIn("hashlib.sha256()", chunked_script)
        self.assertIn("fetch range plan too large", chunked_script)
        self.assertIn('data = handle.read(identity["size"] + 1)', full_fetch_script)
        self.assertIn(
            'handle.assert_identity(identity, "after read")',
            full_fetch_script,
        )
        self.assertIn(
            "source_identity = rollout_identity_from_stat(os.fstat(handle.fileno()))",
            summary_script,
        )
        self.assertIn(
            'handle.assert_identity(source_identity, "after summary scan")',
            summary_script,
        )
        for script in (chunked_script, fetch_script, full_fetch_script, summary_script):
            self.assertIn("open_pinned_rollout_text", script)
            self.assertNotIn("assert_rollout_path_identity", script)
        self.assertNotIn("_match_text", summary_script)

    def test_remote_chunked_rollout_summary_passes_full_fetch_limit(self) -> None:
        identity = MODULE.RolloutIdentity(120, 1, 2, 3, 4)
        token = MODULE._rollout_identity_token(identity)
        source_sha256 = "0" * 64
        record_lines = [
            json.dumps(
                {
                    "kind": "rollout_meta",
                    "source_bytes": identity.size,
                    "source_identity": token,
                    "source_sha256": source_sha256,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            json.dumps(
                {
                    "kind": "chunk_meta",
                    "line": 1,
                    "source_bytes": identity.size,
                    "source_identity": token,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
        ]
        output_bytes = sum(len(line.encode("utf-8")) + 1 for line in record_lines)
        remote_result = mock.Mock(
            returncode=0,
            stderr="",
            stdout="\n".join(
                [
                    MODULE.REMOTE_CHUNKED_ROLLOUT_SUMMARY_BEGIN,
                    json.dumps(
                        {
                            "ok": True,
                            "source_bytes": identity.size,
                            "source_identity": token,
                            "source_sha256": source_sha256,
                            "summary_output_bytes": output_bytes,
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    *record_lines,
                    MODULE.REMOTE_CHUNKED_ROLLOUT_SUMMARY_END,
                    "",
                ]
            ),
        )
        with mock.patch.object(
            MODULE, "_run_remote_python_bounded", return_value=remote_result
        ) as run_remote:
            with redirect_stdout(io.StringIO()):
                rc = MODULE.cmd_chunked_rollout_summary(
                    argparse.Namespace(
                        host="miku-bot-dev",
                        rollout="sessions/2026/05/26/rollout-a.jsonl",
                        keyword=["permission"],
                        chunk_bytes=MODULE.MIN_ROLLOUT_CHUNK_BYTES,
                        limit_per_chunk=10,
                        tail_records=4,
                        max_text_chars=200,
                        **identity_kwargs(identity),
                    )
                )

        self.assertEqual(rc, 0)
        alias, payload = run_remote.call_args.args
        self.assertEqual(alias, "miku-bot-dev")
        self.assertEqual(
            payload["max_fetch_rollout_bytes"], MODULE.MAX_FETCH_ROLLOUT_BYTES
        )
        self.assertEqual(
            payload["max_fetch_rollout_chunk_bytes"],
            MODULE.MAX_FETCH_ROLLOUT_CHUNK_BYTES,
        )
        self.assertEqual(payload["expected_source_identity"], token)
        self.assertEqual(payload["expected_source_bytes"], identity.size)
        self.assertEqual(
            run_remote.call_args.kwargs["max_stdout_bytes"],
            MODULE.MAX_CHUNKED_ROLLOUT_SUMMARY_OUTPUT_BYTES
            + MODULE.REMOTE_CHUNKED_SUMMARY_FRAME_OVERHEAD_BYTES,
        )

    def test_embedded_chunk_summary_missing_at_actual_open_stays_not_found(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(
                codex_root,
                ['{"type":"session_meta","payload":{"id":"vanished"}}'],
            )
            identity = rollout_identity(codex_root, rollout)
            script = MODULE._remote_python_script(
                {
                    "mode": "chunked-rollout-summary",
                    "rollout": rollout,
                    "codex_root": str(codex_root),
                    "summary_keywords": [],
                    "summary_limit": 10,
                    "summary_tail_records": 0,
                    "summary_max_text_chars": 200,
                    "chunk_bytes": MODULE.MIN_ROLLOUT_CHUNK_BYTES,
                    "max_fetch_rollout_bytes": MODULE.MAX_FETCH_ROLLOUT_BYTES,
                    "max_fetch_rollout_chunk_bytes": MODULE.MAX_FETCH_ROLLOUT_CHUNK_BYTES,
                    "min_rollout_chunk_bytes": MODULE.MIN_ROLLOUT_CHUNK_BYTES,
                    "max_rollout_chunk_bytes": MODULE.MAX_ROLLOUT_CHUNK_BYTES,
                    "max_chunked_summary_output_bytes": MODULE.MAX_CHUNKED_ROLLOUT_SUMMARY_OUTPUT_BYTES,
                    "max_fetch_range_plan_entries": MODULE.MAX_FETCH_RANGE_PLAN_ENTRIES,
                    "expected_source_bytes": identity.size,
                    "expected_source_identity": MODULE._rollout_identity_token(
                        identity
                    ),
                    "authorized_source_bytes": None,
                    "output_host": "embedded",
                }
            )
            (codex_root / rollout).unlink()
            result = subprocess.run(
                [sys.executable, "-"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        payload_lines = MODULE._extract_framed_lines(
            result.stdout,
            begin_marker=MODULE.REMOTE_CHUNKED_ROLLOUT_SUMMARY_BEGIN,
            end_marker=MODULE.REMOTE_CHUNKED_ROLLOUT_SUMMARY_END,
            host="embedded",
            command="chunked-rollout-summary",
        )
        self.assertEqual(
            [json.loads(line) for line in payload_lines],
            [{"ok": False, "error": "rollout not found"}],
        )

    def test_embedded_remote_rejects_fetch_range_plan_before_allocation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(
                codex_root,
                [json.dumps({"message": "x" * 256}, separators=(",", ":"))],
            )
            identity = rollout_identity(codex_root, rollout)
            script = MODULE._remote_python_script(
                {
                    "mode": "chunked-rollout-summary",
                    "rollout": rollout,
                    "codex_root": str(codex_root),
                    "summary_keywords": [],
                    "summary_limit": 10,
                    "summary_tail_records": 0,
                    "summary_max_text_chars": 200,
                    "chunk_bytes": 64,
                    "max_fetch_rollout_bytes": MODULE.MAX_FETCH_ROLLOUT_BYTES,
                    "max_fetch_rollout_chunk_bytes": 16,
                    "min_rollout_chunk_bytes": 1,
                    "max_rollout_chunk_bytes": 1024,
                    "max_chunked_summary_output_bytes": MODULE.MAX_CHUNKED_ROLLOUT_SUMMARY_OUTPUT_BYTES,
                    "max_fetch_range_plan_entries": 1,
                    "expected_source_bytes": identity.size,
                    "expected_source_identity": MODULE._rollout_identity_token(
                        identity
                    ),
                    "authorized_source_bytes": None,
                    "output_host": "miku-bot-dev",
                }
            )

            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("fetch range plan too large", result.stdout)

    def test_chunked_rollout_summary_reads_all_chunks_with_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(
                codex_root,
                [
                    '{"timestamp":"2026-05-26T10:00:00Z","type":"session_meta","payload":{"id":"abc","cwd":"/repo"}}',
                    '{"timestamp":"2026-05-26T10:01:00Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"Please debug the runner outage"}]}}',
                    '{"timestamp":"2026-05-26T10:02:00Z","type":"response_item","payload":{"type":"function_call_output","output":"Command failed with permission denied"}}',
                    '{"timestamp":"2026-05-26T10:03:00Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"The runner service is restored."}]}}',
                ],
            )
            identity = rollout_identity(codex_root, rollout)
            source_bytes = identity.size
            with (
                mock.patch.object(MODULE, "_local_codex_root", return_value=codex_root),
                mock.patch.object(MODULE, "MIN_ROLLOUT_CHUNK_BYTES", 1),
            ):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    rc = MODULE.cmd_chunked_rollout_summary(
                        argparse.Namespace(
                            host="local",
                            rollout=rollout,
                            keyword=["permission"],
                            chunk_bytes=220,
                            limit_per_chunk=20,
                            tail_records=4,
                            max_text_chars=200,
                            **identity_kwargs(identity),
                        )
                    )

        self.assertEqual(rc, 0)
        records = [json.loads(line) for line in buffer.getvalue().splitlines()]
        chunk_meta = [record for record in records if record["kind"] == "chunk_meta"]
        self.assertGreaterEqual(len(chunk_meta), 2)
        self.assertTrue(all(record["host"] == "local" for record in records))
        self.assertTrue(all(record["rollout"] == rollout for record in records))
        self.assertEqual(chunk_meta[0]["byte_start"], 0)
        self.assertTrue(
            all(record["source_bytes"] == source_bytes for record in chunk_meta)
        )
        self.assertTrue(
            all(
                record["full_fetch_limit_bytes"] == MODULE.MAX_FETCH_ROLLOUT_BYTES
                for record in chunk_meta
            )
        )
        self.assertTrue(
            all(record["full_reconstruction_allowed"] for record in chunk_meta)
        )
        self.assertTrue(
            all(record["byte_end"] > record["byte_start"] for record in chunk_meta)
        )
        self.assertTrue(any(record["raw_fetch_recommended"] for record in chunk_meta))
        self.assertTrue(
            any(record["kind"] == "function_call_output" for record in records)
        )

    def test_chunk_meta_disallows_full_reconstruction_over_global_limit(self) -> None:
        chunk = MODULE.RolloutChunk(
            index=0,
            byte_start=0,
            byte_end=65,
            record_start=1,
            record_end=1,
            first_timestamp="",
            last_timestamp="",
            oversized_record=False,
            lines=("",),
        )
        with mock.patch.object(MODULE, "MAX_FETCH_ROLLOUT_BYTES", 64):
            meta = MODULE._chunk_meta_record(
                chunk=chunk,
                records=[],
                source_identity=MODULE.RolloutIdentity(65, 1, 2, 3, 4),
                chunk_bytes=64,
                authorized_source_bytes=None,
            )

        self.assertEqual(meta["source_bytes"], 65)
        self.assertEqual(meta["full_fetch_limit_bytes"], 64)
        self.assertFalse(meta["full_reconstruction_allowed"])

    def test_iter_rollout_chunks_never_reads_unbounded_oversized_line(self) -> None:
        data = (
            b'{"timestamp":"2026-05-26T10:00:00Z","type":"response_item","payload":"'
            + b"x" * 200
            + b'"}\n'
        )
        handle = SizeGuardedBytesIO(data, max_readline_size=17)

        chunks = list(MODULE._iter_rollout_chunks(handle, chunk_bytes=16))

        self.assertEqual(len(chunks), 1)
        self.assertTrue(chunks[0].oversized_record)
        self.assertEqual(chunks[0].byte_start, 0)
        self.assertEqual(chunks[0].byte_end, len(data))
        self.assertEqual(chunks[0].record_start, 1)
        self.assertEqual(chunks[0].record_end, 1)
        self.assertEqual(chunks[0].lines, ("",))
        self.assertTrue(all(size == 17 for size in handle.readline_sizes))

    def test_chunked_rollout_summary_splits_oversized_fetch_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(
                codex_root,
                [
                    json.dumps(
                        {
                            "timestamp": "2026-05-26T10:00:00Z",
                            "type": "response_item",
                            "payload": {
                                "type": "message",
                                "role": "user",
                                "content": [
                                    {
                                        "type": "input_text",
                                        "text": "x" * 240,
                                    }
                                ],
                            },
                        },
                        separators=(",", ":"),
                    )
                ],
            )
            identity = rollout_identity(codex_root, rollout)
            with (
                mock.patch.object(MODULE, "_local_codex_root", return_value=codex_root),
                mock.patch.object(MODULE, "MAX_FETCH_ROLLOUT_CHUNK_BYTES", 80),
                mock.patch.object(MODULE, "MIN_ROLLOUT_CHUNK_BYTES", 1),
            ):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    rc = MODULE.cmd_chunked_rollout_summary(
                        argparse.Namespace(
                            host="local",
                            rollout=rollout,
                            keyword=[],
                            chunk_bytes=60,
                            limit_per_chunk=20,
                            tail_records=4,
                            max_text_chars=200,
                            **identity_kwargs(identity),
                        )
                    )

        self.assertEqual(rc, 0)
        records = [json.loads(line) for line in buffer.getvalue().splitlines()]
        oversized = next(
            record
            for record in records
            if record["kind"] == "chunk_meta"
            and "oversized_record" in record["reason_codes"]
        )
        self.assertTrue(oversized["raw_fetch_recommended"])
        self.assertGreater(oversized["fetch_range_count"], 1)
        self.assertEqual(
            oversized["fetch_ranges"][0]["byte_start"], oversized["byte_start"]
        )
        self.assertEqual(
            oversized["fetch_ranges"][-1]["byte_end"], oversized["byte_end"]
        )
        self.assertTrue(
            all(
                item["byte_end"] - item["byte_start"] <= oversized["fetch_chunk_bytes"]
                for item in oversized["fetch_ranges"]
            )
        )

    def test_fetch_range_plan_rejects_huge_count_before_allocation(self) -> None:
        with mock.patch.object(MODULE, "MAX_FETCH_RANGE_PLAN_ENTRIES", 4):
            with self.assertRaisesRegex(
                ValueError,
                "fetch range plan too large: 1000000000000000000000000000000 ranges > 4",
            ):
                MODULE._fetch_ranges_for_byte_range(
                    byte_start=0,
                    byte_end=10**30,
                    max_bytes=1,
                )

    def test_fetch_rollout_chunk_writes_bounded_local_output(self) -> None:
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(
                codex_root,
                [
                    '{"type":"session_meta","payload":{"id":"abc"}}',
                    '{"type":"event_msg","payload":{"type":"task_complete","last_agent_message":"done"}}',
                ],
            )
            source_path = codex_root / rollout
            source_data = source_path.read_bytes()
            identity = rollout_identity(codex_root, rollout)
            first_line_size = len(source_data.splitlines(keepends=True)[0])
            os.chdir(workspace)
            try:
                with mock.patch.object(
                    MODULE, "_local_codex_root", return_value=codex_root
                ):
                    buffer = io.StringIO()
                    with redirect_stdout(buffer):
                        rc = MODULE.cmd_fetch_rollout_chunk(
                            argparse.Namespace(
                                host="local",
                                rollout=rollout,
                                byte_start=0,
                                byte_end=first_line_size,
                                output="chunk.jsonl",
                                **identity_kwargs(identity),
                            )
                        )
                output_path = workspace / ".codex-tmp/remote-host-context/chunk.jsonl"
                data = output_path.read_bytes()
            finally:
                os.chdir(original_cwd)

        self.assertEqual(rc, 0)
        self.assertEqual(data, source_data[:first_line_size])
        self.assertIn(f"bytes={first_line_size}", buffer.getvalue())

    def test_chunk_locator_then_exact_fetch_retains_multiple_substantive_followups(
        self,
    ) -> None:
        first_followup = "Please include archived sessions in the audit."
        second_followup = "Also verify later human follow-ups are not discarded."
        first_wrapper = "Synthetic wrapper bookkeeping after the user's request."
        second_wrapper = "Background artifact chatter after the user's request."

        def message_line(timestamp: str, role: str, text: str) -> str:
            content_type = "input_text" if role == "user" else "output_text"
            return json.dumps(
                {
                    "timestamp": timestamp,
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": role,
                        "content": [{"type": content_type, "text": text}],
                    },
                },
                separators=(",", ":"),
            )

        session_meta_line = json.dumps(
            {
                "timestamp": "2026-05-26T10:00:00Z",
                "type": "session_meta",
                "payload": {"id": "abc", "cwd": "/repo"},
            },
            separators=(",", ":"),
        )
        first_followup_line = message_line(
            "2026-05-26T10:02:00Z", "user", first_followup
        )
        first_wrapper_line = message_line("2026-05-26T10:03:00Z", "user", first_wrapper)
        second_followup_line = message_line(
            "2026-05-26T10:04:00Z", "user", second_followup
        )
        second_wrapper_line = message_line(
            "2026-05-26T10:05:00Z", "user", second_wrapper
        )
        chunk_bytes = max(
            len((first_followup_line + "\n" + first_wrapper_line + "\n").encode()),
            len((second_followup_line + "\n" + second_wrapper_line + "\n").encode()),
        )
        oversized_noise_line = json.dumps(
            {
                "timestamp": "2026-05-26T10:01:00Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "output": "noise:" + "x" * (chunk_bytes + 100),
                },
            },
            separators=(",", ":"),
        )
        rollout_lines = [
            session_meta_line,
            oversized_noise_line,
            first_followup_line,
            first_wrapper_line,
            second_followup_line,
            second_wrapper_line,
        ]
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(codex_root, rollout_lines)
            source_data = (codex_root / rollout).read_bytes()
            identity = rollout_identity(codex_root, rollout)
            with (
                mock.patch.object(MODULE, "_local_codex_root", return_value=codex_root),
                mock.patch.object(MODULE, "MIN_ROLLOUT_CHUNK_BYTES", 1),
            ):
                summary_buffer = io.StringIO()
                with redirect_stdout(summary_buffer):
                    summary_rc = MODULE.cmd_chunked_rollout_summary(
                        argparse.Namespace(
                            host="local",
                            rollout=rollout,
                            keyword=[],
                            chunk_bytes=chunk_bytes,
                            limit_per_chunk=20,
                            tail_records=0,
                            max_text_chars=200,
                            **identity_kwargs(identity),
                        )
                    )
                summary_records = [
                    json.loads(line) for line in summary_buffer.getvalue().splitlines()
                ]
                self.assertEqual(summary_rc, 0)
                chunk_meta_rows = [
                    record
                    for record in summary_records
                    if record["kind"] == "chunk_meta"
                ]
                rollout_meta = next(
                    record
                    for record in summary_records
                    if record["kind"] == "rollout_meta"
                )
                chunk_meta_rows.sort(key=lambda record: record["byte_start"])
                self.assertGreaterEqual(len(chunk_meta_rows), 2)
                self.assertTrue(
                    any(
                        record["record_start"] <= 3 and record["record_end"] >= 4
                        for record in chunk_meta_rows
                    )
                )
                self.assertTrue(
                    any(
                        record["record_start"] <= 5 and record["record_end"] >= 6
                        for record in chunk_meta_rows
                    )
                )
                summary_user_records = [
                    record
                    for record in summary_records
                    if record["kind"] == "user_message"
                ]
                summary_user_texts = [record["text"] for record in summary_user_records]
                summary_user_lines = [record["line"] for record in summary_user_records]
                expected_ranges = []
                for chunk_meta in chunk_meta_rows:
                    ranges = chunk_meta.get("fetch_ranges") or [
                        {
                            "byte_start": chunk_meta["byte_start"],
                            "byte_end": chunk_meta["byte_end"],
                        }
                    ]
                    expected_ranges.extend(
                        (item["byte_start"], item["byte_end"]) for item in ranges
                    )

                self.assertEqual(
                    {record["source_bytes"] for record in chunk_meta_rows},
                    {len(source_data)},
                )
                self.assertEqual(
                    {record["full_fetch_limit_bytes"] for record in chunk_meta_rows},
                    {MODULE.MAX_FETCH_ROLLOUT_BYTES},
                )
                self.assertTrue(
                    all(
                        record["full_reconstruction_allowed"]
                        for record in chunk_meta_rows
                    )
                )
                self.assertEqual(expected_ranges[0][0], 0)
                self.assertTrue(
                    all(
                        current_end == next_start
                        for (_, current_end), (next_start, _) in zip(
                            expected_ranges, expected_ranges[1:]
                        )
                    )
                )
                self.assertEqual(expected_ranges[-1][1], len(source_data))
                planned_bytes = sum(
                    byte_end - byte_start for byte_start, byte_end in expected_ranges
                )
                self.assertEqual(planned_bytes, len(source_data))
                self.assertLessEqual(planned_bytes, MODULE.MAX_FETCH_ROLLOUT_BYTES)

                os.chdir(workspace)
                try:
                    fetch_rcs = []
                    fetched_ranges = []
                    fetched_parts = []
                    for fetch_index, (byte_start, byte_end) in enumerate(
                        expected_ranges
                    ):
                        output_name = f"reconstruction/part-{fetch_index:03d}.jsonl"
                        with redirect_stdout(io.StringIO()):
                            fetch_rcs.append(
                                MODULE.cmd_fetch_rollout_chunk(
                                    argparse.Namespace(
                                        host="local",
                                        rollout=rollout,
                                        byte_start=byte_start,
                                        byte_end=byte_end,
                                        output=output_name,
                                        **identity_kwargs(identity),
                                    )
                                )
                            )
                        fetched_path = (
                            workspace / ".codex-tmp/remote-host-context" / output_name
                        )
                        fetched_ranges.append((byte_start, byte_end))
                        fetched_parts.append(fetched_path.read_bytes())
                    reconstructed_data = b"".join(fetched_parts)
                    fetched_records = [
                        json.loads(line)
                        for line in reconstructed_data.decode("utf-8").splitlines()
                    ]
                    with redirect_stdout(io.StringIO()):
                        final_stat_rc = MODULE.cmd_rollout_stat(
                            argparse.Namespace(
                                host="local",
                                rollout=rollout,
                                expected_source_bytes=identity.size,
                                expected_source_identity=(
                                    MODULE._rollout_identity_token(identity)
                                ),
                            )
                        )
                finally:
                    os.chdir(original_cwd)

        fetched_user_texts = [
            item["text"]
            for record in fetched_records
            if record.get("type") == "response_item"
            and record.get("payload", {}).get("type") == "message"
            and record.get("payload", {}).get("role") == "user"
            for item in record["payload"].get("content", [])
            if item.get("type") == "input_text"
        ]
        self.assertEqual(summary_user_lines, [4, 6])
        self.assertNotIn(first_followup, summary_user_texts)
        self.assertNotIn(second_followup, summary_user_texts)
        self.assertTrue(all(rc == 0 for rc in fetch_rcs))
        self.assertEqual(fetched_ranges, expected_ranges)
        self.assertEqual(reconstructed_data, source_data)
        self.assertEqual(
            hashlib.sha256(reconstructed_data).hexdigest(),
            rollout_meta["source_sha256"],
        )
        self.assertEqual(final_stat_rc, 0)
        self.assertIn(first_followup, fetched_user_texts)
        self.assertIn(second_followup, fetched_user_texts)
        self.assertLess(
            fetched_user_texts.index(first_followup),
            fetched_user_texts.index(second_followup),
        )

    def test_rollout_stat_preflight_and_final_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(
                codex_root,
                ['{"type":"session_meta","payload":{"id":"abc"}}'],
            )
            source_path = codex_root / rollout
            with mock.patch.object(
                MODULE, "_local_codex_root", return_value=codex_root
            ):
                preflight_output = io.StringIO()
                with redirect_stdout(preflight_output):
                    preflight_rc = MODULE.cmd_rollout_stat(
                        argparse.Namespace(
                            host="local",
                            rollout=rollout,
                            expected_source_bytes=None,
                            expected_source_identity=None,
                        )
                    )
                record = json.loads(preflight_output.getvalue())
                identity = MODULE._rollout_identity_from_record(record)
                with redirect_stdout(io.StringIO()):
                    final_rc = MODULE.cmd_rollout_stat(
                        argparse.Namespace(
                            host="local",
                            rollout=rollout,
                            expected_source_bytes=identity.size,
                            expected_source_identity=(
                                MODULE._rollout_identity_token(identity)
                            ),
                        )
                    )
                with source_path.open("ab") as handle:
                    handle.write(b"{}\n")
                final_error = io.StringIO()
                with redirect_stderr(final_error):
                    changed_rc = MODULE.cmd_rollout_stat(
                        argparse.Namespace(
                            host="local",
                            rollout=rollout,
                            expected_source_bytes=identity.size,
                            expected_source_identity=(
                                MODULE._rollout_identity_token(identity)
                            ),
                        )
                    )

        self.assertEqual(preflight_rc, 0)
        self.assertEqual(record["kind"], "rollout_stat")
        self.assertEqual(record["host"], "local")
        self.assertEqual(final_rc, 0)
        self.assertEqual(changed_rc, 1)
        self.assertIn("identity changed", final_error.getvalue())

    def test_overlimit_summary_refuses_before_scan_and_exact_auth_lifts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(
                codex_root,
                ['{"type":"session_meta","payload":{"id":"abc"}}'],
            )
            identity = rollout_identity(codex_root, rollout)
            kwargs = {
                "codex_root": codex_root,
                "rollout_relative_path": MODULE._resolve_rollout_relative_path(rollout),
                "chunk_bytes": identity.size,
                "keywords": [],
                "limit_per_chunk": 20,
                "tail_records": 0,
                "max_text_chars": 200,
                "host": "local",
                "expected_identity": identity,
            }
            with (
                mock.patch.object(MODULE, "MAX_FETCH_ROLLOUT_BYTES", identity.size - 1),
                mock.patch.object(MODULE, "MIN_ROLLOUT_CHUNK_BYTES", 1),
            ):
                with mock.patch.object(MODULE, "_iter_rollout_chunks") as iterator:
                    with self.assertRaisesRegex(
                        ValueError, "exact --authorized-source-bytes"
                    ):
                        MODULE._chunked_rollout_summary_records(
                            **kwargs,
                            authorized_source_bytes=None,
                        )
                    with self.assertRaisesRegex(
                        ValueError, "must equal expected source size"
                    ):
                        MODULE._chunked_rollout_summary_records(
                            **kwargs,
                            authorized_source_bytes=identity.size - 1,
                        )
                    iterator.assert_not_called()
                records = MODULE._chunked_rollout_summary_records(
                    **kwargs,
                    authorized_source_bytes=identity.size,
                )

        self.assertEqual(records[0]["kind"], "rollout_meta")
        self.assertEqual(records[0]["authorized_source_bytes"], identity.size)
        self.assertTrue(records[0]["full_reconstruction_allowed"])

    def test_chunk_summary_rejects_tiny_chunks_before_opening_rollout(self) -> None:
        identity = MODULE.RolloutIdentity(100, 1, 2, 3, 4)
        with mock.patch.object(MODULE, "_open_pinned_rollout_text") as pinned_open:
            with self.assertRaisesRegex(ValueError, "--chunk-bytes must stay between"):
                MODULE._chunked_rollout_summary_records(
                    codex_root=Path("/unused"),
                    rollout_relative_path=Path("sessions/2026/05/26/rollout-a.jsonl"),
                    chunk_bytes=MODULE.MIN_ROLLOUT_CHUNK_BYTES - 1,
                    keywords=[],
                    limit_per_chunk=20,
                    tail_records=0,
                    max_text_chars=200,
                    host="local",
                    expected_identity=identity,
                    authorized_source_bytes=None,
                )
            pinned_open.assert_not_called()

    def test_chunk_summary_output_cap_fails_without_partial_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(
                codex_root,
                ['{"type":"session_meta","payload":{"id":"abc"}}'],
            )
            identity = rollout_identity(codex_root, rollout)
            output = io.StringIO()
            error_output = io.StringIO()
            with (
                mock.patch.object(MODULE, "_local_codex_root", return_value=codex_root),
                mock.patch.object(MODULE, "MIN_ROLLOUT_CHUNK_BYTES", 1),
                mock.patch.object(
                    MODULE, "MAX_CHUNKED_ROLLOUT_SUMMARY_OUTPUT_BYTES", 64
                ),
                redirect_stdout(output),
                redirect_stderr(error_output),
            ):
                rc = MODULE.cmd_chunked_rollout_summary(
                    argparse.Namespace(
                        host="local",
                        rollout=rollout,
                        keyword=[],
                        chunk_bytes=identity.size,
                        limit_per_chunk=20,
                        tail_records=0,
                        max_text_chars=200,
                        **identity_kwargs(identity),
                    )
                )

        self.assertEqual(rc, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("chunked summary output too large", error_output.getvalue())

    def test_full_fetch_bounds_snapshot_read_and_rechecks_identity(self) -> None:
        for mutation in ("append", "replace"):
            with (
                self.subTest(mutation=mutation),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                codex_root = Path(temp_dir) / ".codex"
                rollout = write_rollout(
                    codex_root,
                    ['{"type":"session_meta","payload":{"id":"abc"}}'],
                )
                source_path = codex_root / rollout
                source_data = source_path.read_bytes()
                real_fdopen = MODULE.os.fdopen
                read_sizes: list[int] = []
                mutated = False

                class MutatingReadHandle:
                    def __init__(self, handle: object) -> None:
                        self.handle = handle

                    def __enter__(self) -> "MutatingReadHandle":
                        return self

                    def __exit__(self, *args: object) -> None:
                        self.handle.close()

                    def fileno(self) -> int:
                        return self.handle.fileno()

                    def close(self) -> None:
                        self.handle.close()

                    def read(self, size: int = -1) -> bytes:
                        nonlocal mutated
                        read_sizes.append(size)
                        if not mutated:
                            if mutation == "append":
                                with source_path.open("ab") as append_handle:
                                    append_handle.write(b"{}\n")
                            else:
                                replacement = source_path.with_suffix(".replacement")
                                replacement.write_bytes(source_data)
                                os.replace(replacement, source_path)
                            mutated = True
                        return self.handle.read(size)

                def fdopen_with_mutation(fd: int, mode: str) -> MutatingReadHandle:
                    return MutatingReadHandle(real_fdopen(fd, mode))

                with mock.patch.object(
                    MODULE.os,
                    "fdopen",
                    side_effect=fdopen_with_mutation,
                ):
                    with self.assertRaisesRegex(
                        ValueError, "identity changed after read"
                    ):
                        MODULE._read_local_rollout_bytes(
                            codex_root,
                            MODULE._resolve_rollout_relative_path(rollout),
                            max_bytes=MODULE.MAX_FETCH_ROLLOUT_BYTES,
                        )

                self.assertEqual(read_sizes, [len(source_data) + 1])

    def test_remote_full_fetch_uses_bounded_parent_capture(self) -> None:
        remote_output = (
            f"{MODULE.REMOTE_FETCH_ROLLOUT_BEGIN}\n"
            '{"bytes":3,"ok":true}\n'
            "YWJj\n"
            f"{MODULE.REMOTE_FETCH_ROLLOUT_END}\n"
        )
        remote_result = subprocess.CompletedProcess(
            args=["ssh"],
            returncode=0,
            stdout=remote_output,
            stderr="",
        )
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            output_path = Path(temp_dir) / "rollout.jsonl"
            with (
                mock.patch.object(
                    MODULE,
                    "_run_remote_python_bounded",
                    return_value=remote_result,
                ) as bounded_run,
                redirect_stdout(io.StringIO()),
            ):
                rc = MODULE.cmd_fetch_rollout(
                    argparse.Namespace(
                        host="miku-bot-dev",
                        rollout="sessions/2026/05/26/rollout-a.jsonl",
                        output=str(output_path),
                    )
                )

            self.assertEqual(output_path.read_bytes(), b"abc")

        self.assertEqual(rc, 0)
        bounded_run.assert_called_once()
        self.assertEqual(
            bounded_run.call_args.kwargs["max_stdout_bytes"],
            MODULE.MAX_REMOTE_FETCH_ROLLOUT_STDOUT_BYTES,
        )

    def test_remote_session_meta_uses_exact_bounded_parent_capture(self) -> None:
        remote_result = subprocess.CompletedProcess(
            args=["ssh"],
            returncode=0,
            stdout=(
                f"{MODULE.REMOTE_SESSION_META_BEGIN}\n"
                f"{MODULE.REMOTE_SESSION_META_END}\n"
            ),
            stderr="",
        )
        args = argparse.Namespace(
            host=["miku-bot-dev"],
            date=["2026/05/26"],
            from_date=None,
            to_date=None,
            limit=10,
        )
        with (
            mock.patch.object(
                MODULE,
                "_run_remote_python_bounded",
                return_value=remote_result,
            ) as bounded_run,
            redirect_stdout(io.StringIO()),
        ):
            rc = MODULE.cmd_session_meta(args)

        self.assertEqual(rc, 0)
        self.assertEqual(MODULE.MAX_REMOTE_SESSION_META_STDOUT_BYTES, 32_899_072)
        self.assertEqual(
            bounded_run.call_args.kwargs["max_stdout_bytes"],
            32_899_072,
        )
        self.assertFalse(hasattr(MODULE, "_run_remote_python"))

        with (
            mock.patch.object(
                MODULE,
                "_run_remote_python_bounded",
                side_effect=RuntimeError("stdout capture limit exceeded"),
            ),
            mock.patch.object(MODULE, "_extract_framed_lines") as parser,
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            rc = MODULE.cmd_session_meta(args)

        self.assertEqual(rc, 1)
        parser.assert_not_called()

    def test_remote_rollout_summary_uses_exact_bounded_parent_capture(self) -> None:
        remote_result = subprocess.CompletedProcess(
            args=["ssh"],
            returncode=0,
            stdout="unused framed output",
            stderr="",
        )
        args = argparse.Namespace(
            host="miku-bot-dev",
            rollout="sessions/2026/05/26/rollout-a.jsonl",
            keyword=[],
            limit=20,
            tail_records=4,
            max_text_chars=200,
        )
        with (
            mock.patch.object(
                MODULE,
                "_run_remote_python_bounded",
                return_value=remote_result,
            ) as bounded_run,
            mock.patch.object(
                MODULE,
                "_extract_framed_rollout_summary_records",
                return_value=[],
            ),
            redirect_stdout(io.StringIO()),
        ):
            rc = MODULE.cmd_rollout_summary(args)

        self.assertEqual(rc, 0)
        self.assertEqual(MODULE.MAX_REMOTE_ROLLOUT_SUMMARY_STDOUT_BYTES, 31_462_656)
        self.assertEqual(
            bounded_run.call_args.kwargs["max_stdout_bytes"],
            31_462_656,
        )

        with (
            mock.patch.object(
                MODULE,
                "_run_remote_python_bounded",
                side_effect=RuntimeError("stdout capture limit exceeded"),
            ),
            mock.patch.object(
                MODULE,
                "_extract_framed_rollout_summary_records",
            ) as parser,
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            rc = MODULE.cmd_rollout_summary(args)

        self.assertEqual(rc, 1)
        parser.assert_not_called()

    def test_remote_chunk_fetch_uses_exact_bounded_parent_capture(self) -> None:
        identity = MODULE.RolloutIdentity(3, 1, 2, 3, 4)
        identity_token = MODULE._rollout_identity_token(identity)
        remote_output = (
            f"{MODULE.REMOTE_FETCH_ROLLOUT_CHUNK_BEGIN}\n"
            + json.dumps(
                {
                    "bytes": 3,
                    "ok": True,
                    "source_bytes": 3,
                    "source_identity": identity_token,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\nYWJj\n"
            + f"{MODULE.REMOTE_FETCH_ROLLOUT_CHUNK_END}\n"
        )
        remote_result = subprocess.CompletedProcess(
            args=["ssh"],
            returncode=0,
            stdout=remote_output,
            stderr="",
        )
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            output_path = Path(temp_dir) / "chunk.jsonl"
            with (
                mock.patch.object(
                    MODULE,
                    "_run_remote_python_bounded",
                    return_value=remote_result,
                ) as bounded_run,
                redirect_stdout(io.StringIO()),
            ):
                rc = MODULE.cmd_fetch_rollout_chunk(
                    argparse.Namespace(
                        host="miku-bot-dev",
                        rollout="sessions/2026/05/26/rollout-a.jsonl",
                        byte_start=0,
                        byte_end=3,
                        output=str(output_path),
                        **identity_kwargs(identity),
                    )
                )

            self.assertEqual(output_path.read_bytes(), b"abc")

        self.assertEqual(rc, 0)
        bounded_run.assert_called_once()
        self.assertEqual(
            bounded_run.call_args.kwargs["max_stdout_bytes"],
            2_861_740,
        )
        self.assertEqual(
            MODULE.MAX_REMOTE_FETCH_ROLLOUT_CHUNK_STDOUT_BYTES,
            2_861_740,
        )

    def test_chunk_fetch_rejects_append_and_replacement_after_preflight(self) -> None:
        for mutation in ("append", "replace"):
            with (
                self.subTest(mutation=mutation),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                codex_root = Path(temp_dir) / ".codex"
                rollout = write_rollout(
                    codex_root,
                    ['{"type":"session_meta","payload":{"id":"abc"}}'],
                )
                source_path = codex_root / rollout
                source_data = source_path.read_bytes()
                identity = rollout_identity(codex_root, rollout)
                if mutation == "append":
                    with source_path.open("ab") as handle:
                        handle.write(b"{}\n")
                else:
                    replacement = source_path.with_suffix(".replacement")
                    replacement.write_bytes(source_data)
                    os.replace(replacement, source_path)

                with self.assertRaisesRegex(ValueError, "identity changed before read"):
                    MODULE._read_local_rollout_byte_range(
                        codex_root,
                        MODULE._resolve_rollout_relative_path(rollout),
                        byte_start=0,
                        byte_end=len(source_data),
                        max_bytes=MODULE.MAX_FETCH_ROLLOUT_CHUNK_BYTES,
                        expected_identity=identity,
                    )

    def test_chunk_fetch_rechecks_path_identity_after_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(
                codex_root,
                ['{"type":"session_meta","payload":{"id":"abc"}}'],
            )
            source_path = codex_root / rollout
            source_data = source_path.read_bytes()
            identity = rollout_identity(codex_root, rollout)
            real_assert_identity = MODULE._PinnedRolloutHandle.assert_identity

            def assert_identity_then_append(
                handle: MODULE._PinnedRolloutHandle,
                expected: MODULE.RolloutIdentity,
                *,
                phase: str,
            ) -> None:
                if phase == "after read":
                    with source_path.open("ab") as append_handle:
                        append_handle.write(b"{}\n")
                real_assert_identity(handle, expected, phase=phase)

            with mock.patch.object(
                MODULE._PinnedRolloutHandle,
                "assert_identity",
                new=assert_identity_then_append,
            ):
                with self.assertRaisesRegex(ValueError, "identity changed after read"):
                    MODULE._read_local_rollout_byte_range(
                        codex_root,
                        MODULE._resolve_rollout_relative_path(rollout),
                        byte_start=0,
                        byte_end=len(source_data),
                        max_bytes=MODULE.MAX_FETCH_ROLLOUT_CHUNK_BYTES,
                        expected_identity=identity,
                    )

    def test_chunk_summary_rejects_append_during_scan_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(
                codex_root,
                [
                    '{"type":"session_meta","payload":{"id":"abc"}}',
                    '{"type":"event_msg","payload":{"type":"task_complete","last_agent_message":"done"}}',
                ],
            )
            source_path = codex_root / rollout
            identity = rollout_identity(codex_root, rollout)
            original_iterator = MODULE._iter_rollout_chunks

            def mutating_iterator(*args: object, **kwargs: object):
                mutated = False
                for chunk in original_iterator(*args, **kwargs):
                    yield chunk
                    if not mutated:
                        with source_path.open("ab") as handle:
                            handle.write(b"{}\n")
                        mutated = True

            output = io.StringIO()
            error_output = io.StringIO()
            with (
                mock.patch.object(MODULE, "_local_codex_root", return_value=codex_root),
                mock.patch.object(MODULE, "MIN_ROLLOUT_CHUNK_BYTES", 1),
                mock.patch.object(
                    MODULE, "_iter_rollout_chunks", side_effect=mutating_iterator
                ),
                redirect_stdout(output),
                redirect_stderr(error_output),
            ):
                rc = MODULE.cmd_chunked_rollout_summary(
                    argparse.Namespace(
                        host="local",
                        rollout=rollout,
                        keyword=[],
                        chunk_bytes=identity.size,
                        limit_per_chunk=20,
                        tail_records=0,
                        max_text_chars=200,
                        **identity_kwargs(identity),
                    )
                )

        self.assertEqual(rc, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("identity changed after summary scan", error_output.getvalue())

    def test_embedded_rollout_summary_symlink_rejection_stays_framed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            codex_root = base / ".codex"
            outside = base / "outside-rollout.jsonl"
            outside.write_text(
                '{"type":"session_meta","payload":{"id":"must-not-escape"}}\n',
                encoding="utf-8",
            )
            rollout = "sessions/2026/05/26/rollout-2026-05-26T10-00-00-symlink.jsonl"
            rollout_path = codex_root / rollout
            rollout_path.parent.mkdir(parents=True)
            rollout_path.symlink_to(outside)
            script = MODULE._remote_python_script(
                {
                    "mode": "rollout-summary",
                    "rollout": rollout,
                    "codex_root": str(codex_root),
                    "summary_keywords": [],
                    "summary_limit": 10,
                    "summary_scan_bytes": MODULE.MAX_ROLLOUT_SUMMARY_SCAN_BYTES,
                    "summary_line_bytes": MODULE.MAX_ROLLOUT_SUMMARY_LINE_BYTES,
                    "summary_tail_records": 0,
                    "summary_max_text_chars": 200,
                }
            )
            result = subprocess.run(
                [sys.executable, "-"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        payload_lines = MODULE._extract_framed_lines(
            result.stdout,
            begin_marker=MODULE.REMOTE_ROLLOUT_SUMMARY_BEGIN,
            end_marker=MODULE.REMOTE_ROLLOUT_SUMMARY_END,
            host="embedded",
            command="rollout-summary",
        )
        self.assertEqual(
            [json.loads(line) for line in payload_lines],
            [{"ok": False, "error": "rollout path is a symlink"}],
        )
        self.assertNotIn("must-not-escape", result.stdout)
        self.assertNotIn("Traceback", result.stdout)

    def test_embedded_rollout_summary_budget_error_has_no_partial_records(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(
                codex_root,
                ['{"type":"session_meta","payload":{"id":"must-not-leak"}}'],
            )
            script = MODULE._remote_python_script(
                {
                    "mode": "rollout-summary",
                    "rollout": rollout,
                    "codex_root": str(codex_root),
                    "summary_keywords": [],
                    "summary_limit": 10,
                    "summary_scan_bytes": MODULE.MAX_ROLLOUT_SUMMARY_SCAN_BYTES,
                    "summary_line_bytes": MODULE.MAX_ROLLOUT_SUMMARY_LINE_BYTES,
                    "summary_tail_records": 0,
                    "summary_max_text_chars": 200,
                }
            )
            original = (
                "ROLLOUT_SUMMARY_SERIALIZED_BYTES = "
                f"{MODULE.MAX_REMOTE_ROLLOUT_SUMMARY_SERIALIZED_BYTES}"
            )
            self.assertEqual(script.count(original), 1)
            script = script.replace(
                original,
                "ROLLOUT_SUMMARY_SERIALIZED_BYTES = 1",
            )
            result = subprocess.run(
                [sys.executable, "-"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload_lines = MODULE._extract_framed_lines(
            result.stdout,
            begin_marker=MODULE.REMOTE_ROLLOUT_SUMMARY_BEGIN,
            end_marker=MODULE.REMOTE_ROLLOUT_SUMMARY_END,
            host="embedded",
            command="rollout-summary",
        )
        self.assertEqual(
            [json.loads(line) for line in payload_lines],
            [{"ok": False, "error": "rollout summary output too large"}],
        )
        self.assertNotIn("scan_meta", result.stdout)
        self.assertNotIn("must-not-leak", result.stdout)

    def test_rollout_summary_rejects_path_mutation_before_output(self) -> None:
        for mutation in ("append", "replace"):
            with (
                self.subTest(mutation=mutation),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                codex_root = Path(temp_dir) / ".codex"
                rollout = write_rollout(
                    codex_root,
                    [
                        '{"type":"session_meta","payload":{"id":"abc"}}',
                        '{"type":"event_msg","payload":{"type":"task_complete","last_agent_message":"done"}}',
                    ],
                )
                source_path = codex_root / rollout
                source_data = source_path.read_bytes()
                original_summary = MODULE._summarize_rollout_records

                def summarize_then_mutate(*args: object, **kwargs: object):
                    records = original_summary(*args, **kwargs)
                    if mutation == "append":
                        with source_path.open("ab") as handle:
                            handle.write(b"{}\n")
                    else:
                        replacement = source_path.with_suffix(".replacement")
                        replacement.write_bytes(source_data)
                        os.replace(replacement, source_path)
                    return records

                output = io.StringIO()
                error_output = io.StringIO()
                with (
                    mock.patch.object(
                        MODULE, "_local_codex_root", return_value=codex_root
                    ),
                    mock.patch.object(
                        MODULE,
                        "_summarize_rollout_records",
                        side_effect=summarize_then_mutate,
                    ),
                    redirect_stdout(output),
                    redirect_stderr(error_output),
                ):
                    rc = MODULE.cmd_rollout_summary(
                        argparse.Namespace(
                            host="local",
                            rollout=rollout,
                            keyword=[],
                            limit=20,
                            tail_records=4,
                            max_text_chars=200,
                        )
                    )

                self.assertEqual(rc, 1)
                self.assertEqual(output.getvalue(), "")
                self.assertIn(
                    "identity changed after summary scan", error_output.getvalue()
                )

    def test_keyword_match_uses_full_signal_without_retaining_raw_text(self) -> None:
        distant_keyword = "distant needle"
        long_text = "prefix " + ("x" * 256) + "   distant\nneedle"

        def message(text: str) -> str:
            return json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": text}],
                    },
                },
                separators=(",", ":"),
            )

        lines = [message(long_text), message("final ordinary message")]
        local_records = MODULE._summarize_rollout_records(
            lines=lines,
            keywords=[distant_keyword],
            limit=20,
            tail_records=0,
            max_text_chars=40,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(codex_root, lines)
            script = MODULE._remote_python_script(
                {
                    "mode": "rollout-summary",
                    "rollout": rollout,
                    "codex_root": str(codex_root),
                    "summary_keywords": [distant_keyword],
                    "summary_limit": 20,
                    "summary_scan_bytes": MODULE.MAX_ROLLOUT_SUMMARY_SCAN_BYTES,
                    "summary_line_bytes": MODULE.MAX_ROLLOUT_SUMMARY_LINE_BYTES,
                    "summary_tail_records": 0,
                    "summary_max_text_chars": 40,
                }
            )
            result = subprocess.run(
                [sys.executable, "-"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual([record["line"] for record in local_records], [1, 2])
        self.assertNotIn("_keyword_matched", json.dumps(local_records))
        self.assertNotIn(distant_keyword, json.dumps(local_records))
        self.assertEqual(result.returncode, 0, result.stderr)
        embedded_records = MODULE._extract_framed_rollout_summary_records(
            result.stdout,
            begin_marker=MODULE.REMOTE_ROLLOUT_SUMMARY_BEGIN,
            end_marker=MODULE.REMOTE_ROLLOUT_SUMMARY_END,
            host="embedded",
            command="rollout-summary",
        )
        embedded_user_records = [
            record
            for record in embedded_records
            if record.get("kind") == "user_message"
        ]
        self.assertEqual(
            [record["line"] for record in embedded_user_records],
            [1, 2],
        )
        self.assertNotIn("_keyword_matched", result.stdout)
        self.assertNotIn(distant_keyword, result.stdout)
        self.assertNotIn("x" * 64, result.stdout)

    def test_parent_bounded_reader_stops_noisy_stdout_and_stderr(self) -> None:
        for stream_name, fd in (("stdout", 1), ("stderr", 2)):
            with self.subTest(stream=stream_name):
                with self.assertRaisesRegex(
                    RuntimeError,
                    rf"command {stream_name} exceeded 1024-byte capture limit",
                ):
                    MODULE._run_subprocess_text_bounded(
                        [
                            sys.executable,
                            "-c",
                            f"import os; os.write({fd}, b'x' * 4096)",
                        ],
                        timeout_seconds=5,
                        max_stdout_bytes=1024,
                        max_stderr_bytes=1024,
                    )

    def test_parent_bounded_reader_reaps_child_after_closed_pipe_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pid_path = Path(temp_dir) / "child.pid"
            child_script = (
                "import os, pathlib, time; "
                f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid())); "
                "os.close(1); os.close(2); time.sleep(60)"
            )
            with self.assertRaisesRegex(RuntimeError, "command timed out after 1s"):
                MODULE._run_subprocess_text_bounded(
                    [sys.executable, "-c", child_script],
                    timeout_seconds=1,
                    max_stdout_bytes=1024,
                    max_stderr_bytes=1024,
                )

            child_pid = int(pid_path.read_text(encoding="utf-8"))
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)

    def test_fetch_rollout_chunk_rejects_oversized_range_before_reading(self) -> None:
        identity = MODULE.RolloutIdentity(9, 1, 2, 3, 4)
        buffer = io.StringIO()
        with mock.patch.object(MODULE, "MAX_FETCH_ROLLOUT_CHUNK_BYTES", 8):
            with redirect_stderr(buffer):
                rc = MODULE.cmd_fetch_rollout_chunk(
                    argparse.Namespace(
                        host="local",
                        rollout="sessions/2026/05/26/rollout-2026-05-26T10-00-00-example.jsonl",
                        byte_start=0,
                        byte_end=9,
                        output="chunk.jsonl",
                        **identity_kwargs(identity),
                    )
                )

        self.assertEqual(rc, 2)
        self.assertIn("chunk too large: 9 bytes > 8", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
