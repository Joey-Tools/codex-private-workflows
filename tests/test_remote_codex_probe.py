from __future__ import annotations

import argparse
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


def write_rollout(codex_root: Path, lines: list[str]) -> str:
    rollout_dir = codex_root / "sessions/2026/05/26"
    rollout_dir.mkdir(parents=True)
    rollout = rollout_dir / "rollout-2026-05-26T10-00-00-example.jsonl"
    rollout.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return "sessions/2026/05/26/rollout-2026-05-26T10-00-00-example.jsonl"


class RemoteHostContextDocumentationTests(unittest.TestCase):
    def test_skill_documents_repeatable_host_preflight_shape(self) -> None:
        skill = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn(
            "preflight --host local --host miku-bot-dev --host hoteng-srv-01 --host codex-hoteng-srv-01",
            skill,
        )
        self.assertIn("do not pass positional host names", skill)
        self.assertIn("plural `--hosts` flag", skill)

    def test_codex_hoteng_srv_uses_distinct_codex_home(self) -> None:
        host = MODULE.HOSTS["codex-hoteng-srv-01"]

        self.assertEqual(host["label"], "codex-hoteng-srv-01")
        self.assertEqual(host["ssh_target"], "codex-hoteng-srv-01")
        self.assertEqual(host["codex_root"], "/home/codex/.codex")


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
        chunked_script = MODULE._remote_python_script(
            {
                "mode": "chunked-rollout-summary",
                "rollout": "sessions/2026/05/26/rollout-a.jsonl",
                "codex_root": "/home/hoteng/.codex",
                "summary_keywords": ["permission"],
                "summary_limit": 10,
                "summary_tail_records": 4,
                "summary_max_text_chars": 200,
                "chunk_bytes": 1024,
                "max_fetch_rollout_chunk_bytes": MODULE.MAX_FETCH_ROLLOUT_CHUNK_BYTES,
            }
        )
        fetch_script = MODULE._remote_python_script(
            {
                "mode": "fetch-rollout-chunk",
                "rollout": "sessions/2026/05/26/rollout-a.jsonl",
                "codex_root": "/home/hoteng/.codex",
                "byte_start": 0,
                "byte_end": 120,
                "max_fetch_rollout_chunk_bytes": MODULE.MAX_FETCH_ROLLOUT_CHUNK_BYTES,
            }
        )

        compile(chunked_script, "<chunked-rollout-summary>", "exec")
        compile(fetch_script, "<fetch-rollout-chunk>", "exec")

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
            with mock.patch.object(MODULE, "_local_codex_root", return_value=codex_root):
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
                        )
                    )

        self.assertEqual(rc, 0)
        records = [json.loads(line) for line in buffer.getvalue().splitlines()]
        chunk_meta = [record for record in records if record["kind"] == "chunk_meta"]
        self.assertGreaterEqual(len(chunk_meta), 2)
        self.assertTrue(all(record["host"] == "local" for record in records))
        self.assertTrue(all(record["rollout"] == rollout for record in records))
        self.assertEqual(chunk_meta[0]["byte_start"], 0)
        self.assertTrue(all(record["byte_end"] > record["byte_start"] for record in chunk_meta))
        self.assertTrue(any(record["raw_fetch_recommended"] for record in chunk_meta))
        self.assertTrue(any(record["kind"] == "function_call_output" for record in records))

    def test_iter_rollout_chunks_never_reads_unbounded_oversized_line(self) -> None:
        data = b'{"timestamp":"2026-05-26T10:00:00Z","type":"response_item","payload":"' + b"x" * 200 + b'"}\n'
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
            with (
                mock.patch.object(MODULE, "_local_codex_root", return_value=codex_root),
                mock.patch.object(MODULE, "MAX_FETCH_ROLLOUT_CHUNK_BYTES", 80),
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
                        )
                    )

        self.assertEqual(rc, 0)
        records = [json.loads(line) for line in buffer.getvalue().splitlines()]
        oversized = next(
            record
            for record in records
            if record["kind"] == "chunk_meta" and "oversized_record" in record["reason_codes"]
        )
        self.assertTrue(oversized["raw_fetch_recommended"])
        self.assertGreater(oversized["fetch_range_count"], 1)
        self.assertEqual(oversized["fetch_ranges"][0]["byte_start"], oversized["byte_start"])
        self.assertEqual(oversized["fetch_ranges"][-1]["byte_end"], oversized["byte_end"])
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
            first_line_size = len(source_data.splitlines(keepends=True)[0])
            os.chdir(workspace)
            try:
                with mock.patch.object(MODULE, "_local_codex_root", return_value=codex_root):
                    buffer = io.StringIO()
                    with redirect_stdout(buffer):
                        rc = MODULE.cmd_fetch_rollout_chunk(
                            argparse.Namespace(
                                host="local",
                                rollout=rollout,
                                byte_start=0,
                                byte_end=first_line_size,
                                output="chunk.jsonl",
                            )
                        )
                output_path = workspace / ".codex-tmp/remote-host-context/chunk.jsonl"
                data = output_path.read_bytes()
            finally:
                os.chdir(original_cwd)

        self.assertEqual(rc, 0)
        self.assertEqual(data, source_data[:first_line_size])
        self.assertIn(f"bytes={first_line_size}", buffer.getvalue())

    def test_fetch_rollout_chunk_rejects_oversized_range_before_reading(self) -> None:
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
                    )
                )

        self.assertEqual(rc, 2)
        self.assertIn("chunk too large: 9 bytes > 8", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
