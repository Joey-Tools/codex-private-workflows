from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
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

        compile(chunked_script, "<chunked-rollout-summary>", "exec")
        compile(fetch_script, "<fetch-rollout-chunk>", "exec")
        self.assertIn(
            '"full_fetch_limit_bytes": MAX_FETCH_ROLLOUT_BYTES', chunked_script
        )
        self.assertIn(
            '"full_reconstruction_allowed": automatic_allowed or AUTHORIZED_SOURCE_BYTES == source_identity["size"]',
            chunked_script,
        )
        self.assertIn("hashlib.sha256()", chunked_script)

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
        with mock.patch.object(MODULE, "_safe_rollout_path") as safe_path:
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
            safe_path.assert_not_called()

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
            real_fstat = MODULE.os.fstat
            calls = 0

            def fstat_then_append(fd: int) -> os.stat_result:
                nonlocal calls
                calls += 1
                result = real_fstat(fd)
                if calls == 2:
                    with source_path.open("ab") as handle:
                        handle.write(b"{}\n")
                return result

            with mock.patch.object(MODULE.os, "fstat", side_effect=fstat_then_append):
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
