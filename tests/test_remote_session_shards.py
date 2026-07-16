from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import tracemalloc
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    REPO_ROOT
    / "personal_codex/skills/remote-host-context/scripts/remote_codex_probe.py"
)
SKILL_PATH = REPO_ROOT / "personal_codex/skills/remote-host-context/SKILL.md"
REFERENCE_PATH = (
    REPO_ROOT
    / "personal_codex/skills/remote-host-context/references/session-shards-v1.md"
)
SPEC = importlib.util.spec_from_file_location(
    "remote_codex_probe_session_shards", SCRIPT_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def write_rollout(codex_root: Path, data: bytes) -> str:
    relative = "sessions/2026/07/14/rollout-2026-07-14T10-00-00-shards.jsonl"
    path = codex_root / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    return relative


def command_args(
    rollout: str,
    *,
    host: str = "local",
    emit: str = "descriptors",
    byte_start: int = 0,
    byte_end: int | None = None,
    shard_bytes: int = 512,
    max_shards: int = 64,
    source_token: str | None = None,
    resume_cursor: str | None = None,
    record_processing_budget_bytes: int = (
        MODULE.DEFAULT_SESSION_RECORD_PROCESSING_BUDGET_BYTES
    ),
) -> argparse.Namespace:
    return argparse.Namespace(
        host=host,
        rollout=rollout,
        emit=emit,
        byte_start=byte_start,
        byte_end=byte_end,
        shard_bytes=shard_bytes,
        max_shards=max_shards,
        source_token=source_token,
        resume_cursor=resume_cursor,
        record_processing_budget_bytes=record_processing_budget_bytes,
    )


def holdout_command_args(
    identity_path: Path,
    *,
    host: str = "hoteng-srv-01",
    qualification_mode: str = "shadow",
    controlled_missing_host: bool = True,
    create_identity: bool = True,
    window_start: str = "2026-07-13T00:00:00Z",
    window_end: str = "2026-07-14T00:00:00Z",
    source_kind: str = "codex_session_history",
    source_lease_ref: str = "source-lease:daily-partial:hoteng-srv-01:1",
) -> argparse.Namespace:
    argv = [
        "session-shards",
        "--host",
        host,
        "--emit",
        "holdout-receipt",
        "--qualification-mode",
        qualification_mode,
        "--window-start",
        window_start,
        "--window-end",
        window_end,
        "--source-kind",
        source_kind,
        "--source-lease-ref",
        source_lease_ref,
        "--shadow-identity-path",
        str(identity_path),
    ]
    if controlled_missing_host:
        argv.append("--controlled-missing-host")
    argv.append(
        "--create-shadow-identity"
        if create_identity
        else "--require-existing-shadow-identity"
    )
    return MODULE.build_parser().parse_args(argv)


def run_local(
    codex_root: Path,
    args: argparse.Namespace,
) -> tuple[int, list[dict[str, object]], str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with (
        mock.patch.object(MODULE, "_local_codex_root", return_value=codex_root),
        redirect_stdout(stdout),
        redirect_stderr(stderr),
    ):
        returncode = MODULE.cmd_session_shards(args)
    frames = [json.loads(line) for line in stdout.getvalue().splitlines()]
    return returncode, frames, stderr.getvalue()


def frame_of_kind(frames: list[dict[str, object]], kind: str) -> dict[str, object]:
    return next(frame for frame in frames if frame["kind"] == kind)


def protocol_ref(prefix: str, label: str) -> str:
    return prefix + hashlib.sha256(label.encode("ascii")).hexdigest()


def authenticated_backfill_result(
    *,
    holdout_identity_key: bytes,
    coordinator_identity_key: bytes,
    receipt: dict[str, object],
    label: str,
    now_utc: dt.datetime,
    backfill_run_ref: str | None = None,
    source_outcome: str = "complete",
    source_transport_receipt_ref: str | None = None,
) -> dict[str, object]:
    partial_run_ref = protocol_ref("run_ref_v2:", f"{label}:partial")
    return MODULE._session_shards_backfill_result(
        holdout_identity_key=holdout_identity_key,
        coordinator_identity_key=coordinator_identity_key,
        holdout_ref=str(receipt["holdout_ref"]),
        host=str(receipt["host"]),
        host_ref=protocol_ref("host_ref_v2:", f"{label}:host"),
        window_start=str(receipt["window_start"]),
        window_end=str(receipt["window_end"]),
        source_kind=str(receipt["source_kind"]),
        partial_source_lease_ref=str(receipt["source_lease_ref"]),
        backfill_source_lease_ref=f"source-lease:backfill:{label}",
        partial_run_ref=partial_run_ref,
        backfill_run_ref=(
            backfill_run_ref or protocol_ref("run_ref_v2:", f"{label}:backfill")
        ),
        backfill_of_run_ref=partial_run_ref,
        partial_configuration_root=hashlib.sha256(b"configuration").hexdigest(),
        backfill_configuration_root=hashlib.sha256(b"configuration").hexdigest(),
        coordinator_identity_key_id=(
            MODULE._session_shards_coordinator_identity_key_id(coordinator_identity_key)
        ),
        source_outcome=source_outcome,
        source_snapshot_ref=protocol_ref("source_snapshot_v2:", f"{label}:snapshot"),
        source_transport_receipt_ref=(
            source_transport_receipt_ref
            or protocol_ref(
                "source_transport_receipt_v2:", f"{label}:transport-receipt"
            )
        ),
        evidence_digest=protocol_ref("shadow_source_evidence_v2:", f"{label}:evidence"),
        terminal_completion_ref=protocol_ref(
            "shadow_coverage_receipt_v2:", f"{label}:coverage"
        ),
        terminal_completion_authentication_tag=protocol_ref(
            "shadow_coverage_auth_v2:", f"{label}:coverage-auth"
        ),
        terminal_completion_revision=7,
        status_checkpoint_revision=8,
        now_utc=now_utc,
    )


def reassemble_fragments(frames: list[dict[str, object]]) -> bytes:
    fragments = [frame for frame in frames if frame["kind"] == "record_fragment"]
    assert fragments
    fragments.sort(key=lambda frame: int(frame["fragment_index"]))
    return b"".join(
        base64.b64decode(str(frame["fragment_b64"]), validate=True)
        for frame in fragments
    )


def remote_request(
    rollout: str,
    *,
    emit: str = "descriptors",
    byte_start: int = 0,
    byte_end: int | None = None,
    shard_bytes: int = 512,
    max_shards: int = 64,
    source_token: str | None = None,
    resume_cursor: str | None = None,
    record_processing_budget_bytes: int = (
        MODULE.DEFAULT_SESSION_RECORD_PROCESSING_BUDGET_BYTES
    ),
) -> dict[str, object]:
    return {
        "emit": emit,
        "rollout": rollout,
        "codex_root": "/home/hoteng/.codex",
        "byte_start": byte_start,
        "byte_end": byte_end,
        "shard_bytes": shard_bytes,
        "max_shards": max_shards,
        "source_token": source_token,
        "resume_cursor": resume_cursor,
        "record_processing_budget_bytes": record_processing_budget_bytes,
    }


def remote_output(frames: list[dict[str, object]], *, include_end: bool = True) -> str:
    lines = [
        MODULE.REMOTE_SESSION_SHARDS_BEGIN,
        *(
            json.dumps(
                {
                    key: value
                    for key, value in frame.items()
                    if key not in {"host", "rollout"}
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            for frame in frames
        ),
    ]
    if include_end:
        lines.append(MODULE.REMOTE_SESSION_SHARDS_END)
    lines.append("")
    return "\n".join(lines)


def refresh_record_accounting_commitment(
    frames: list[dict[str, object]],
) -> None:
    hasher = hashlib.sha256()
    for frame in frames:
        if frame.get("kind") in {"record", "record_fragment", "gap"}:
            hasher.update(MODULE._session_shards_accounting_bytes(frame))
    terminal = frame_of_kind(frames, "stream_end")
    proof = dict(terminal["conservation_proof"])
    proof["accounting_commitment"] = "sha256:" + hasher.hexdigest()
    terminal["conservation_proof"] = proof


def empty_descriptor_frames(
    request: dict[str, object],
) -> list[dict[str, object]]:
    source_token = request.get("source_token") or (
        MODULE.SESSION_SHARDS_SOURCE_TOKEN_PREFIX + "0" * 64
    )
    request_binding = MODULE._session_shards_request_binding(
        rollout=str(request["rollout"]),
        mode=str(request["emit"]),
        source_token=(
            None
            if request.get("source_token") is None
            else str(request["source_token"])
        ),
        byte_start=int(request["byte_start"]),
        byte_end=(
            None if request.get("byte_end") is None else int(request["byte_end"])
        ),
        shard_bytes=int(request["shard_bytes"]),
        max_shards=int(request["max_shards"]),
        record_processing_budget_bytes=int(request["record_processing_budget_bytes"]),
        resume_cursor=(
            None
            if request.get("resume_cursor") is None
            else str(request["resume_cursor"])
        ),
    )
    stream_meta = {
        "kind": "stream_meta",
        "schema": MODULE.SESSION_SHARDS_SCHEMA,
        "mode": "descriptors",
        "source_token": source_token,
        "request_rollout": request["rollout"],
        "request_source_token": request.get("source_token"),
        "request_resume_cursor": request.get("resume_cursor"),
        "request_binding": request_binding,
        "source_bytes": 0,
        "byte_start": 0,
        "byte_end": None,
        "record_start": 0,
        "shard_bytes": request["shard_bytes"],
        "max_shards": request["max_shards"],
        "record_processing_budget_bytes": request["record_processing_budget_bytes"],
        "fixed_memory_envelope_bytes": (
            MODULE.MIN_SESSION_RECORD_PROCESSING_BUDGET_BYTES
        ),
        "hard_record_processing_ceiling_bytes": (
            MODULE.HARD_SESSION_RECORD_PROCESSING_CEILING_BYTES
        ),
        "hard_record_scan_ceiling_bytes": (
            MODULE.HARD_SESSION_RECORD_SCAN_CEILING_BYTES
        ),
        "record_fragment_bytes": MODULE.SESSION_SHARDS_RECORD_FRAGMENT_BYTES,
        "json_nesting_depth_limit": MODULE.SESSION_SHARDS_MAX_JSON_NESTING_DEPTH,
        "max_remote_frame_chars": MODULE.MAX_SESSION_SHARDS_FRAME_CHARS,
        "protocol_features": list(MODULE.SESSION_SHARDS_PROTOCOL_FEATURES),
    }
    terminal = {
        "kind": "stream_end",
        "schema": MODULE.SESSION_SHARDS_SCHEMA,
        "mode": "descriptors",
        "source_token": source_token,
        "request_binding": request_binding,
        "complete": True,
        "reason": "eof",
        "emitted_shards": 0,
        "byte_start": 0,
        "byte_end": 0,
        "record_start": 0,
        "record_end": 0,
        "next_byte_start": None,
        "next_record_start": None,
        "next_resume_cursor": None,
        "accounted_byte_count": 0,
        "accounted_record_count": 0,
    }
    return [stream_meta, terminal]


class FakePopen:
    def __init__(self, output: str | bytes, returncode: int) -> None:
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(
            output.encode("utf-8") if isinstance(output, str) else output
        )
        self._returncode = returncode
        self._waited = False

    def poll(self) -> int | None:
        return self._returncode if self._waited else None

    def wait(self, timeout: int | None = None) -> int:
        del timeout
        self._waited = True
        return self._returncode

    def kill(self) -> None:
        self._waited = True


class SessionShardsLocalTests(unittest.TestCase):
    def test_utf8_offsets_are_source_bytes_not_characters(self) -> None:
        first = (
            json.dumps(
                {"type": "message", "text": "你好"},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        second = b'{"type":"event","ok":true}\n'
        data = first + second
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, data)
            rc, descriptors, error = run_local(
                codex_root,
                command_args(rollout, shard_bytes=len(first)),
            )
            token = frame_of_kind(descriptors, "stream_meta")["source_token"]
            rc_records, records, records_error = run_local(
                codex_root,
                command_args(
                    rollout,
                    emit="records",
                    byte_end=len(data),
                    shard_bytes=len(first),
                    source_token=str(token),
                ),
            )

        self.assertEqual((rc, error), (0, ""))
        self.assertEqual((rc_records, records_error), (0, ""))
        record_frames = [frame for frame in records if frame["kind"] == "record"]
        self.assertEqual(record_frames[0]["byte_end"], len(first))
        self.assertEqual(record_frames[1]["byte_start"], len(first))
        decoded = base64.b64decode(record_frames[0]["record_b64"])
        self.assertEqual(decoded, first)
        self.assertEqual(json.loads(decoded)["text"], "你好")

    def test_final_record_without_newline_is_complete(self) -> None:
        first = b'{"n":1}\n'
        second = b'{"n":2}'
        data = first + second
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, data)
            rc, descriptors, error = run_local(codex_root, command_args(rollout))
            token = frame_of_kind(descriptors, "stream_meta")["source_token"]
            rc_records, records, records_error = run_local(
                codex_root,
                command_args(
                    rollout,
                    emit="records",
                    byte_end=len(data),
                    source_token=str(token),
                ),
            )

        self.assertEqual((rc, error), (0, ""))
        self.assertEqual((rc_records, records_error), (0, ""))
        terminal = frame_of_kind(records, "stream_end")
        self.assertTrue(terminal["complete"])
        self.assertEqual(terminal["byte_end"], len(data))
        self.assertEqual(terminal["record_end"], 2)

    def test_crlf_delimiter_survives_scan_chunk_boundary(self) -> None:
        prefix = b'{"text":"'
        suffix = b'"}'
        padding = (
            MODULE.SESSION_SHARDS_RECORD_SCAN_CHUNK_BYTES
            - len(prefix)
            - len(suffix)
            - 1
        )
        data = prefix + b"x" * padding + suffix + b"\r\n"
        self.assertEqual(
            data[MODULE.SESSION_SHARDS_RECORD_SCAN_CHUNK_BYTES - 1 :],
            b"\r\n",
        )
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, data)
            rc, descriptors, error = run_local(
                codex_root, command_args(rollout, shard_bytes=512)
            )
            token = frame_of_kind(descriptors, "stream_meta")["source_token"]
            rc_records, records, records_error = run_local(
                codex_root,
                command_args(
                    rollout,
                    emit="records",
                    byte_end=len(data),
                    shard_bytes=512,
                    source_token=str(token),
                ),
            )

        self.assertEqual((rc, error), (0, ""))
        self.assertEqual((rc_records, records_error), (0, ""))
        fragment = frame_of_kind(records, "record_fragment")
        self.assertEqual(fragment["delimiter_bytes"], 2)
        self.assertEqual(reassemble_fragments(records), data)

    def test_descriptor_pagination_resumes_with_source_token(self) -> None:
        lines = [b'{"n":1}\n', b'{"n":2}\n', b'{"n":3}\n']
        data = b"".join(lines)
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, data)
            rc_first, first_page, first_error = run_local(
                codex_root,
                command_args(rollout, shard_bytes=len(lines[0]), max_shards=2),
            )
            first_terminal = frame_of_kind(first_page, "stream_end")
            token = frame_of_kind(first_page, "stream_meta")["source_token"]
            resume_cursor = first_terminal["next_resume_cursor"]
            rc_second, second_page, second_error = run_local(
                codex_root,
                command_args(
                    rollout,
                    byte_start=int(first_terminal["next_byte_start"]),
                    shard_bytes=len(lines[0]),
                    max_shards=2,
                    source_token=str(token),
                    resume_cursor=str(resume_cursor),
                ),
            )

        self.assertEqual((rc_first, first_error), (0, ""))
        self.assertEqual((rc_second, second_error), (0, ""))
        self.assertFalse(first_terminal["complete"])
        self.assertEqual(first_terminal["reason"], "max_shards")
        self.assertEqual(
            first_terminal["accounted_byte_count"],
            len(lines[0]) + len(lines[1]),
        )
        self.assertEqual(first_terminal["accounted_record_count"], 2)
        self.assertEqual(first_terminal["next_byte_start"], first_terminal["byte_end"])
        self.assertIsInstance(resume_cursor, str)
        self.assertEqual(
            first_terminal["next_record_start"], first_terminal["record_end"]
        )
        second_terminal = frame_of_kind(second_page, "stream_end")
        self.assertTrue(second_terminal["complete"])
        self.assertEqual(second_terminal["accounted_byte_count"], len(lines[2]))
        self.assertEqual(second_terminal["accounted_record_count"], 1)
        shards = [
            frame for frame in first_page + second_page if frame["kind"] == "shard"
        ]
        self.assertEqual(
            [(item["byte_start"], item["byte_end"]) for item in shards],
            [
                (0, len(lines[0])),
                (len(lines[0]), len(lines[0]) + len(lines[1])),
                (len(lines[0]) + len(lines[1]), len(data)),
            ],
        )
        self.assertEqual(
            [(item["record_start"], item["record_end"]) for item in shards],
            [(0, 1), (1, 2), (2, 3)],
        )

    def test_max_shards_boundary_does_not_read_huge_next_record(self) -> None:
        first = b'{"n":1}\n'
        second = (
            b'{"text":"'
            + b"x" * (2 * MODULE.SESSION_SHARDS_RECORD_SCAN_CHUNK_BYTES)
            + b'"}\n'
        )
        bytes_read = 0
        real_open = MODULE._open_session_shard_source

        class CountingHandle:
            def __init__(self, handle: object) -> None:
                self.handle = handle

            def __enter__(self) -> CountingHandle:
                return self

            def __exit__(self, *args: object) -> None:
                del args
                self.handle.close()

            def __getattr__(self, name: str) -> object:
                return getattr(self.handle, name)

            def read(self, size: int = -1) -> bytes:
                nonlocal bytes_read
                value = self.handle.read(size)
                bytes_read += len(value)
                return value

            def readline(self, size: int = -1) -> bytes:
                nonlocal bytes_read
                value = self.handle.readline(size)
                bytes_read += len(value)
                return value

        def counted_open(*args: object, **kwargs: object) -> CountingHandle:
            return CountingHandle(real_open(*args, **kwargs))

        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, first + second)
            with (
                mock.patch.object(
                    MODULE,
                    "_open_session_shard_source",
                    side_effect=counted_open,
                ),
                mock.patch.object(
                    MODULE.tempfile,
                    "SpooledTemporaryFile",
                    side_effect=AssertionError("session-shards must not spool"),
                ) as spool,
            ):
                rc, frames, error = run_local(
                    codex_root,
                    command_args(
                        rollout,
                        shard_bytes=64,
                        max_shards=1,
                    ),
                )

        self.assertEqual((rc, error), (0, ""))
        terminal = frame_of_kind(frames, "stream_end")
        self.assertEqual(terminal["reason"], "max_shards")
        self.assertEqual(terminal["next_byte_start"], len(first))
        self.assertEqual(bytes_read, len(first))
        spool.assert_not_called()

    def test_high_page_resume_cursor_does_not_rescan_the_prefix(self) -> None:
        lines = [f'{{"n":"{index:04d}"}}\n'.encode() for index in range(1_100)]
        self.assertEqual(len({len(line) for line in lines}), 1)
        shard_bytes = len(lines[0])
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, b"".join(lines))
            rc_first, first_page, first_error = run_local(
                codex_root,
                command_args(
                    rollout,
                    shard_bytes=shard_bytes,
                    max_shards=MODULE.MAX_SESSION_SHARDS_PER_PAGE,
                ),
            )
            terminal = frame_of_kind(first_page, "stream_end")
            token = frame_of_kind(first_page, "stream_meta")["source_token"]
            with mock.patch.object(
                MODULE,
                "_session_shards_record_index_at_offset",
                side_effect=AssertionError("prefix scan must not run"),
            ) as prefix_scan:
                rc_second, second_page, second_error = run_local(
                    codex_root,
                    command_args(
                        rollout,
                        byte_start=int(terminal["next_byte_start"]),
                        shard_bytes=shard_bytes,
                        max_shards=1,
                        source_token=str(token),
                        resume_cursor=str(terminal["next_resume_cursor"]),
                    ),
                )

        self.assertEqual((rc_first, first_error), (0, ""))
        self.assertEqual((rc_second, second_error), (0, ""))
        self.assertEqual(terminal["next_record_start"], 1_024)
        prefix_scan.assert_not_called()
        shard = frame_of_kind(second_page, "shard")
        self.assertEqual((shard["record_start"], shard["record_end"]), (1_024, 1_025))

    def test_resume_cursor_rejects_forgery_offset_mismatch_and_stale_source(
        self,
    ) -> None:
        lines = [b'{"n":1}\n', b'{"n":2}\n', b'{"n":3}\n']
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, b"".join(lines))
            rc, first_page, error = run_local(
                codex_root,
                command_args(rollout, shard_bytes=len(lines[0]), max_shards=1),
            )
            terminal = frame_of_kind(first_page, "stream_end")
            token = str(frame_of_kind(first_page, "stream_meta")["source_token"])
            cursor = str(terminal["next_resume_cursor"])
            forged = cursor[:-1] + ("0" if cursor[-1] != "0" else "1")
            forged_rc, forged_frames, forged_error = run_local(
                codex_root,
                command_args(
                    rollout,
                    byte_start=int(terminal["next_byte_start"]),
                    shard_bytes=len(lines[0]),
                    source_token=token,
                    resume_cursor=forged,
                ),
            )
            mismatch_rc, mismatch_frames, mismatch_error = run_local(
                codex_root,
                command_args(
                    rollout,
                    byte_start=int(terminal["next_byte_start"]) + len(lines[1]),
                    shard_bytes=len(lines[0]),
                    source_token=token,
                    resume_cursor=cursor,
                ),
            )
            (codex_root / rollout).write_bytes(b"".join(lines) + b'{"n":4}\n')
            stale_rc, stale_frames, stale_error = run_local(
                codex_root,
                command_args(
                    rollout,
                    byte_start=int(terminal["next_byte_start"]),
                    shard_bytes=len(lines[0]),
                    source_token=token,
                    resume_cursor=cursor,
                ),
            )

        self.assertEqual((rc, error), (0, ""))
        self.assertEqual((forged_rc, forged_frames), (1, []))
        self.assertIn("invalid session-shards resume cursor", forged_error)
        self.assertEqual((mismatch_rc, mismatch_frames), (1, []))
        self.assertIn("does not match --byte-start", mismatch_error)
        self.assertEqual((stale_rc, stale_frames), (1, []))
        self.assertIn("source token does not match current rollout", stale_error)

    def test_stale_source_token_is_rejected_before_any_frame(self) -> None:
        data = b'{"n":1}\n'
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, data)
            rc, descriptors, error = run_local(codex_root, command_args(rollout))
            token = frame_of_kind(descriptors, "stream_meta")["source_token"]
            (codex_root / rollout).write_bytes(data + b'{"n":2}\n')
            stale_rc, stale_frames, stale_error = run_local(
                codex_root,
                command_args(
                    rollout,
                    emit="records",
                    byte_end=len(data),
                    source_token=str(token),
                ),
            )

        self.assertEqual((rc, error), (0, ""))
        self.assertEqual(stale_rc, 1)
        self.assertEqual(stale_frames, [])
        self.assertIn("source token does not match current rollout", stale_error)

    def test_invalid_json_is_a_content_free_gap(self) -> None:
        valid = b'{"n":1}\n'
        invalid = b"{not-json}\n"
        non_object = b"[]\n"
        nonstandard_constant = b'{"n":NaN}\n'
        data = valid + invalid + non_object + nonstandard_constant
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, data)
            rc, descriptors, error = run_local(codex_root, command_args(rollout))
            token = frame_of_kind(descriptors, "stream_meta")["source_token"]
            rc_records, records, records_error = run_local(
                codex_root,
                command_args(
                    rollout,
                    emit="records",
                    byte_end=len(data),
                    source_token=str(token),
                ),
            )

        self.assertEqual((rc, error), (0, ""))
        self.assertEqual((rc_records, records_error), (0, ""))
        gaps = [frame for frame in records if frame["kind"] == "gap"]
        self.assertEqual([gap["reason"] for gap in gaps], ["invalid_json"] * 3)
        for gap in gaps:
            self.assertTrue(
                {"record", "record_b64", "payload", "raw", "text"}.isdisjoint(gap),
                gap,
            )

    def test_multibyte_record_over_raw_shard_limit_reassembles_exactly(self) -> None:
        data = (
            json.dumps(
                {"type": "message", "text": "\u4f60\u597d\U0001f642" * 70_000},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        self.assertGreater(len(data), MODULE.MAX_SESSION_SHARD_BYTES)
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, data)
            rc, descriptors, error = run_local(
                codex_root,
                command_args(
                    rollout,
                    shard_bytes=MODULE.MAX_SESSION_SHARD_BYTES,
                ),
            )
            token = frame_of_kind(descriptors, "stream_meta")["source_token"]
            rc_records, records, records_error = run_local(
                codex_root,
                command_args(
                    rollout,
                    emit="records",
                    byte_end=len(data),
                    shard_bytes=MODULE.MAX_SESSION_SHARD_BYTES,
                    source_token=str(token),
                ),
            )

        self.assertEqual((rc, error), (0, ""))
        descriptor = frame_of_kind(descriptors, "shard")
        self.assertEqual(descriptor["status"], "ready")
        self.assertTrue(descriptor["oversized_record"])
        self.assertEqual(descriptor["record_transport"], "base64_fragments")
        self.assertEqual(
            (descriptor["byte_start"], descriptor["byte_end"]),
            (0, len(data)),
        )
        self.assertEqual((rc_records, records_error), (0, ""))
        fragments = [frame for frame in records if frame["kind"] == "record_fragment"]
        self.assertGreater(len(fragments), 2)
        self.assertEqual(
            [int(frame["fragment_index"]) for frame in fragments],
            list(range(len(fragments))),
        )
        self.assertEqual(
            {int(frame["fragment_count"]) for frame in fragments},
            {len(fragments)},
        )
        self.assertEqual(
            [(frame["byte_start"], frame["byte_end"]) for frame in fragments],
            [
                (
                    index * MODULE.SESSION_SHARDS_RECORD_FRAGMENT_BYTES,
                    min(
                        (index + 1) * MODULE.SESSION_SHARDS_RECORD_FRAGMENT_BYTES,
                        len(data),
                    ),
                )
                for index in range(len(fragments))
            ],
        )
        self.assertEqual(
            {
                (
                    frame["record_byte_start"],
                    frame["record_byte_end"],
                    frame["record_start"],
                    frame["record_end"],
                    frame["source_token"],
                )
                for frame in fragments
            },
            {(0, len(data), 0, 1, token)},
        )
        self.assertEqual(reassemble_fragments(records), data)
        terminal = frame_of_kind(records, "stream_end")
        proof = terminal["conservation_proof"]
        self.assertIsInstance(proof, dict)
        self.assertEqual(proof["byte_count"], len(data))
        self.assertEqual(proof["accounted_byte_count"], len(data))
        self.assertEqual(proof["record_count"], 1)
        self.assertEqual(proof["accounted_record_count"], 1)

    def test_local_validator_rejects_unmarked_oversized_ready_descriptor(
        self,
    ) -> None:
        data = b'{"text":"' + b"x" * 128 + b'"}\n'
        shard_bytes = 64
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, data)
            rc, frames, error = run_local(
                codex_root,
                command_args(rollout, shard_bytes=shard_bytes),
            )

        self.assertEqual((rc, error), (0, ""))
        transport_frames = [
            {
                key: value
                for key, value in frame.items()
                if key not in {"host", "rollout"}
            }
            for frame in frames
        ]
        meta = frame_of_kind(transport_frames, "stream_meta")
        descriptor = frame_of_kind(transport_frames, "shard")
        request = remote_request(rollout, shard_bytes=shard_bytes)

        valid = MODULE._RemoteSessionShardsValidator(request=request)
        valid.accept(meta)
        valid.accept(descriptor)

        unmarked = dict(descriptor)
        for field in MODULE._SESSION_SHARDS_OVERSIZED_DESCRIPTOR_FIELDS:
            unmarked.pop(field)
        adversarial = MODULE._RemoteSessionShardsValidator(request=request)
        adversarial.accept(meta)
        with self.assertRaisesRegex(
            RuntimeError,
            "exceeds shard_bytes.*without the oversized record contract",
        ):
            adversarial.accept(unmarked)

    def test_large_record_uses_source_offsets_with_bounded_peak_memory(self) -> None:
        data = b'{"text":"' + b"x" * (1024 * 1024) + b'"}\n'
        budget = MODULE.MIN_SESSION_RECORD_PROCESSING_BUDGET_BYTES
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, data)
            with (
                MODULE._open_session_shard_source(
                    codex_root,
                    MODULE.pathlib.PurePosixPath(rollout),
                ) as handle,
                mock.patch.object(
                    MODULE.tempfile,
                    "SpooledTemporaryFile",
                    side_effect=AssertionError("session-shards must not spool"),
                ) as spool,
            ):
                records = iter(
                    MODULE._iter_session_shard_records(
                        handle,
                        byte_start=0,
                        byte_end=len(data),
                        record_start=0,
                        record_processing_budget_bytes=budget,
                    )
                )
                tracemalloc.start()
                try:
                    record = next(records)
                    _, peak_bytes = tracemalloc.get_traced_memory()
                finally:
                    tracemalloc.stop()
                self.assertIs(record.source_handle, handle)
                self.assertLess(peak_bytes, budget)
                self.assertEqual(
                    record.record_commitment,
                    MODULE._session_shards_content_commitment(data),
                )
                frames = list(
                    MODULE._iter_session_record_transport_frames(
                        record,
                        shard_bytes=MODULE.MAX_SESSION_SHARD_BYTES,
                        source_token=MODULE.SESSION_SHARDS_SOURCE_TOKEN_PREFIX
                        + "a" * 64,
                        request_binding=MODULE.SESSION_SHARDS_REQUEST_BINDING_PREFIX
                        + "b" * 64,
                    )
                )
                transported = b"".join(
                    base64.b64decode(frame["fragment_b64"], validate=True)
                    for frame in frames
                )
                self.assertEqual(data, transported)
                spool.assert_not_called()
                records.close()

    def test_first_record_without_newline_stops_at_hard_scan_ceiling(self) -> None:
        hard_scan_ceiling = 1024

        class CountingBytesIO(io.BytesIO):
            def __init__(self, value: bytes) -> None:
                super().__init__(value)
                self.bytes_returned = 0

            def readline(self, size: int = -1) -> bytes:
                value = super().readline(size)
                self.bytes_returned += len(value)
                return value

        handle = CountingBytesIO(b"x" * (hard_scan_ceiling * 16))
        with mock.patch.object(
            MODULE,
            "HARD_SESSION_RECORD_SCAN_CEILING_BYTES",
            hard_scan_ceiling,
        ):
            records = iter(
                MODULE._iter_session_shard_records(
                    handle,
                    byte_start=0,
                    byte_end=hard_scan_ceiling * 16,
                    record_start=0,
                    record_processing_budget_bytes=(
                        MODULE.DEFAULT_SESSION_RECORD_PROCESSING_BUDGET_BYTES
                    ),
                )
            )
            with self.assertRaisesRegex(
                ValueError,
                "record scan exceeded the hard byte ceiling",
            ):
                next(records)
            records.close()

        self.assertEqual(hard_scan_ceiling, handle.bytes_returned)
        self.assertEqual(hard_scan_ceiling, handle.tell())

    def test_fixed_memory_envelope_covers_stream_frame_serialization(self) -> None:
        def measured_peak(data: bytes) -> int:
            with tempfile.TemporaryDirectory() as raw:
                codex_root = Path(raw) / ".codex"
                rollout = write_rollout(codex_root, data)
                with MODULE._open_session_shard_source(
                    codex_root,
                    MODULE.pathlib.PurePosixPath(rollout),
                ) as handle:
                    identity = MODULE._session_shards_source_identity(
                        os.fstat(handle.fileno())
                    )
                token = MODULE._session_shards_source_token(identity)
                tracemalloc.start()
                try:
                    for frame in MODULE._iter_local_session_shard_frames(
                        codex_root=codex_root,
                        rollout_relative_path=MODULE.pathlib.PurePosixPath(rollout),
                        emit="records",
                        byte_start=0,
                        byte_end=len(data),
                        shard_bytes=MODULE.MAX_SESSION_SHARD_BYTES,
                        max_shards=64,
                        source_token=token,
                        record_processing_budget_bytes=(
                            MODULE.MIN_SESSION_RECORD_PROCESSING_BUDGET_BYTES
                        ),
                    ):
                        encoded = json.dumps(
                            frame,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                        self.assertLessEqual(
                            len(encoded), MODULE.MAX_SESSION_SHARDS_FRAME_CHARS
                        )
                        del encoded
                    _, peak_bytes = tracemalloc.get_traced_memory()
                finally:
                    tracemalloc.stop()
            return peak_bytes

        inline_overhead = len(b'{"text":""}\n')
        normal = (
            b'{"text":"'
            + b"x" * (MODULE.MAX_SESSION_SHARD_BYTES - inline_overhead)
            + b'"}\n'
        )
        fragmented = b'{"text":"' + b"x" * (1024 * 1024) + b'"}\n'
        self.assertEqual(len(normal), MODULE.MAX_SESSION_SHARD_BYTES)
        for name, data in (("normal", normal), ("fragmented", fragmented)):
            with self.subTest(name=name):
                peak_bytes = measured_peak(data)
                self.assertLess(
                    peak_bytes,
                    MODULE.MIN_SESSION_RECORD_PROCESSING_BUDGET_BYTES,
                    f"peak={peak_bytes}",
                )

    def test_processing_budget_gap_is_explicit_content_free_and_conserved(
        self,
    ) -> None:
        budget = MODULE.MIN_SESSION_RECORD_PROCESSING_BUDGET_BYTES
        over_budget = b'{"text":"' + b"x" * budget + b'"}\n'
        following = b'{"n":2}\n'
        data = over_budget + following
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, data)
            rc, descriptors, error = run_local(
                codex_root,
                command_args(
                    rollout,
                    shard_bytes=32,
                    max_shards=1,
                    record_processing_budget_bytes=budget,
                ),
            )
            descriptor_terminal = frame_of_kind(descriptors, "stream_end")
            token = frame_of_kind(descriptors, "stream_meta")["source_token"]
            rc_records, records, records_error = run_local(
                codex_root,
                command_args(
                    rollout,
                    emit="records",
                    byte_end=len(data),
                    shard_bytes=32,
                    source_token=str(token),
                    record_processing_budget_bytes=budget,
                ),
            )

        self.assertEqual((rc, error), (0, ""))
        descriptor = frame_of_kind(descriptors, "shard")
        self.assertEqual(descriptor["status"], "gap")
        self.assertEqual(descriptor["gap_reason"], "record_processing_budget_exceeded")
        self.assertEqual(descriptor["byte_count"], len(over_budget))
        self.assertEqual(descriptor["record_processing_budget_bytes"], budget)
        self.assertEqual(descriptor["processing_ceiling_kind"], "record_bytes")
        self.assertEqual(descriptor["processing_ceiling_limit"], budget)
        self.assertEqual(descriptor["processing_ceiling_observed"], len(over_budget))
        self.assertEqual(
            descriptor["hard_record_processing_ceiling_bytes"],
            MODULE.HARD_SESSION_RECORD_PROCESSING_CEILING_BYTES,
        )
        self.assertFalse(descriptor_terminal["complete"])
        self.assertEqual(descriptor_terminal["next_byte_start"], len(over_budget))
        self.assertEqual((rc_records, records_error), (0, ""))
        gap = frame_of_kind(records, "gap")
        self.assertEqual(gap["reason"], "record_processing_budget_exceeded")
        self.assertEqual(gap["byte_count"], len(over_budget))
        self.assertEqual(gap["record_processing_budget_bytes"], budget)
        self.assertEqual(gap["processing_ceiling_kind"], "record_bytes")
        self.assertEqual(gap["processing_ceiling_limit"], budget)
        self.assertEqual(gap["processing_ceiling_observed"], len(over_budget))
        self.assertTrue(
            {
                "record",
                "record_b64",
                "fragment_b64",
                "payload",
                "raw",
                "text",
                "record_commitment",
                "fragment_commitment",
            }.isdisjoint(gap)
        )
        normal = frame_of_kind(records, "record")
        self.assertEqual(base64.b64decode(normal["record_b64"]), following)
        terminal = frame_of_kind(records, "stream_end")
        self.assertEqual(terminal["emitted_gap_bytes"], len(over_budget))
        self.assertEqual(terminal["emitted_record_bytes"], len(following))
        proof = terminal["conservation_proof"]
        self.assertEqual(proof["byte_count"], len(data))
        self.assertEqual(proof["accounted_byte_count"], len(data))

    def test_valid_json_over_nesting_ceiling_is_an_explicit_processing_gap(
        self,
    ) -> None:
        depth = MODULE.SESSION_SHARDS_MAX_JSON_NESTING_DEPTH + 1
        data = b'{"value":' + b"[" * depth + b"0" + b"]" * depth + b"}\n"
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, data)
            rc, descriptors, error = run_local(codex_root, command_args(rollout))
            token = str(frame_of_kind(descriptors, "stream_meta")["source_token"])
            rc_records, records, records_error = run_local(
                codex_root,
                command_args(
                    rollout,
                    emit="records",
                    byte_end=len(data),
                    source_token=token,
                ),
            )

        self.assertEqual((rc, error), (0, ""))
        self.assertEqual((rc_records, records_error), (0, ""))
        descriptor = frame_of_kind(descriptors, "shard")
        gap = frame_of_kind(records, "gap")
        for frame in (descriptor, gap):
            self.assertEqual(
                frame.get("gap_reason", frame.get("reason")),
                "record_processing_budget_exceeded",
            )
            self.assertEqual(frame["byte_count"], len(data))
            self.assertEqual(frame["processing_ceiling_kind"], "json_nesting_depth")
            self.assertEqual(
                frame["processing_ceiling_limit"],
                MODULE.SESSION_SHARDS_MAX_JSON_NESTING_DEPTH,
            )
            self.assertEqual(
                frame["processing_ceiling_observed"],
                MODULE.SESSION_SHARDS_MAX_JSON_NESTING_DEPTH + 1,
            )
        self.assertTrue(
            {"record_b64", "fragment_b64", "payload", "raw", "text"}.isdisjoint(gap)
        )

    def test_records_range_limit_is_inclusive_and_boundary_aligned(self) -> None:
        range_limit = 2048
        data = b"x" * (range_limit - 1) + b"\n"
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, data)
            with mock.patch.object(
                MODULE,
                "MAX_SESSION_SHARDS_RANGE_BYTES",
                range_limit,
            ):
                rc, descriptors, error = run_local(
                    codex_root,
                    command_args(
                        rollout,
                        shard_bytes=MODULE.MAX_SESSION_SHARD_BYTES,
                    ),
                )
                token = frame_of_kind(descriptors, "stream_meta")["source_token"]
                exact_rc, exact_frames, exact_error = run_local(
                    codex_root,
                    command_args(
                        rollout,
                        emit="records",
                        byte_end=len(data),
                        shard_bytes=MODULE.MAX_SESSION_SHARD_BYTES,
                        source_token=str(token),
                    ),
                )
                over_rc, over_frames, over_error = run_local(
                    codex_root,
                    command_args(
                        rollout,
                        emit="records",
                        byte_end=range_limit + 1,
                        source_token=str(token),
                    ),
                )
                unaligned_rc, unaligned_frames, unaligned_error = run_local(
                    codex_root,
                    command_args(
                        rollout,
                        emit="records",
                        byte_start=1,
                        byte_end=len(data),
                        shard_bytes=MODULE.MAX_SESSION_SHARD_BYTES,
                        source_token=str(token),
                    ),
                )

        self.assertEqual((rc, error), (0, ""))
        self.assertEqual((exact_rc, exact_error), (0, ""))
        self.assertTrue(frame_of_kind(exact_frames, "stream_end")["complete"])
        self.assertEqual(frame_of_kind(exact_frames, "gap")["byte_count"], len(data))
        self.assertEqual((over_rc, over_frames), (2, []))
        self.assertIn("record range too large", over_error)
        self.assertEqual((unaligned_rc, unaligned_frames), (1, []))
        self.assertIn("JSONL record boundary", unaligned_error)

    def test_fstat_change_prevents_terminal_completion(self) -> None:
        data = b'{"n":1}\n'
        first_identity = (1, 2, 3, len(data), 4, 5)
        changed_identity = (1, 2, 3, len(data) + 1, 6, 7)
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, data)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.object(MODULE, "_local_codex_root", return_value=codex_root),
                mock.patch.object(
                    MODULE,
                    "_session_shards_source_identity",
                    side_effect=[first_identity, changed_identity],
                ),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                rc = MODULE.cmd_session_shards(command_args(rollout))

        frames = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(rc, 1)
        self.assertNotIn("stream_end", [frame["kind"] for frame in frames])
        self.assertIn("source changed during session-shards read", stderr.getvalue())

    def test_symlink_and_non_regular_rollouts_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            codex_root = root / ".codex"
            rollout = "sessions/2026/07/14/rollout-unsafe.jsonl"
            target = codex_root / rollout
            target.parent.mkdir(parents=True)
            outside = root / "outside.jsonl"
            outside.write_bytes(b'{"n":1}\n')
            target.symlink_to(outside)
            symlink_rc, symlink_frames, symlink_error = run_local(
                codex_root, command_args(rollout)
            )
            target.unlink()
            target.mkdir()
            directory_rc, directory_frames, directory_error = run_local(
                codex_root, command_args(rollout)
            )

        self.assertEqual((symlink_rc, symlink_frames), (1, []))
        self.assertIn("symlink", symlink_error)
        self.assertEqual((directory_rc, directory_frames), (1, []))
        self.assertIn("not a regular file", directory_error)

    @unittest.skipUnless(
        hasattr(os, "mkfifo") and hasattr(os, "O_NONBLOCK"),
        "FIFO nonblocking open is unavailable",
    )
    def test_openat_final_fifo_swap_is_rejected_without_blocking(self) -> None:
        errors: list[BaseException] = []
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, b'{"n":1}\n')
            relative = MODULE.pathlib.PurePosixPath(rollout)
            target = codex_root / rollout
            pinned = target.with_name(target.name + ".pinned")

            def swap_before_final_open(index: int, _part: str, _dirfd: int) -> None:
                if index != len(relative.parts) - 2:
                    return
                target.rename(pinned)
                os.mkfifo(target, mode=0o600)

            def open_source() -> None:
                try:
                    with MODULE._open_session_shard_source(codex_root, relative):
                        pass
                except BaseException as exc:
                    errors.append(exc)

            thread = threading.Thread(target=open_source, daemon=True)
            with mock.patch.object(
                MODULE,
                "_SESSION_SHARDS_OPEN_COMPONENT_HOOK",
                side_effect=swap_before_final_open,
                create=True,
            ):
                thread.start()
                thread.join(timeout=1)
                blocked = thread.is_alive()
                if blocked:
                    unblock_fd = os.open(target, os.O_RDWR | os.O_NONBLOCK)
                    os.close(unblock_fd)
                    thread.join(timeout=1)

            self.assertFalse(blocked, "opening the final FIFO blocked before fstat")
            self.assertFalse(thread.is_alive(), "FIFO open worker did not terminate")

        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ValueError)
        self.assertIn("not a regular file", str(errors[0]))

    def test_openat_traversal_survives_ancestor_name_swap(self) -> None:
        safe_data = b'{"source":"safe"}\n'
        unsafe_data = b'{"source":"outside"}\n'
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            codex_root = root / ".codex"
            rollout = write_rollout(codex_root, safe_data)
            sessions = codex_root / "sessions"
            pinned_sessions = codex_root / "sessions-pinned"
            outside_sessions = root / "outside-sessions"
            outside_rollout = outside_sessions / Path(rollout).relative_to("sessions")
            outside_rollout.parent.mkdir(parents=True)
            outside_rollout.write_bytes(unsafe_data)

            def swap_after_sessions_open(index: int, part: str, dirfd: int) -> None:
                del dirfd
                if index == 0 and part == "sessions":
                    sessions.rename(pinned_sessions)
                    sessions.symlink_to(outside_sessions, target_is_directory=True)

            with mock.patch.object(
                MODULE,
                "_SESSION_SHARDS_OPEN_COMPONENT_HOOK",
                side_effect=swap_after_sessions_open,
                create=True,
            ):
                with MODULE._open_session_shard_source(
                    codex_root,
                    MODULE.pathlib.PurePosixPath(rollout),
                ) as handle:
                    opened_data = handle.read()

            self.assertEqual((codex_root / rollout).read_bytes(), unsafe_data)

        self.assertEqual(opened_data, safe_data)

    def test_openat_traversal_fails_closed_without_portable_primitives(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, b'{"n":1}\n')
            with (
                mock.patch.object(MODULE.os, "supports_dir_fd", frozenset()),
                self.assertRaisesRegex(RuntimeError, "secure openat.*unsupported"),
            ):
                MODULE._open_session_shard_source(
                    codex_root,
                    MODULE.pathlib.PurePosixPath(rollout),
                )

    def test_openat_traversal_rejects_a_symlink_codex_root(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            real_codex_root = root / ".codex-real"
            rollout = write_rollout(real_codex_root, b'{"n":1}\n')
            linked_codex_root = root / ".codex"
            linked_codex_root.symlink_to(real_codex_root, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "Codex root.*real directory"):
                MODULE._open_session_shard_source(
                    linked_codex_root,
                    MODULE.pathlib.PurePosixPath(rollout),
                )


class SessionShardsHoldoutReceiptTests(unittest.TestCase):
    def test_explicit_runner_shadow_root_is_authoritative(self) -> None:
        with tempfile.TemporaryDirectory(prefix="session-shards-root.") as raw:
            root = Path(raw)
            root.chmod(0o700)
            invocation_dir = root / "invocation"
            invocation_dir.mkdir(mode=0o700)
            inside = invocation_dir / "identity"
            outside = root / "outside-identity"
            with mock.patch.dict(
                os.environ,
                {"CODEX_SESSION_SHARDS_SHADOW_ROOT": str(invocation_dir)},
            ):
                self.assertEqual(
                    inside.resolve(strict=False),
                    MODULE._resolve_session_shards_shadow_identity_path(
                        str(inside),
                        creating=True,
                    ),
                )
                with self.assertRaisesRegex(ValueError, "run-local shadow root"):
                    MODULE._resolve_session_shards_shadow_identity_path(
                        str(outside),
                        creating=True,
                    )

    def test_holdout_receipt_is_authenticated_terminal_and_content_free(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="session-shards-holdout.") as raw:
            root = Path(raw)
            root.chmod(0o700)
            identity_path = root / "identity"
            with mock.patch.object(
                MODULE,
                "_iter_remote_session_shard_frames",
                side_effect=AssertionError("holdout must not start remote transport"),
            ):
                returncode, frames, stderr = run_local(
                    root,
                    holdout_command_args(identity_path),
                )

            self.assertEqual(0, returncode, stderr)
            self.assertEqual(1, len(frames))
            receipt = frames[0]
            self.assertEqual(
                MODULE._SESSION_SHARDS_HOLDOUT_RECEIPT_FIELDS,
                set(receipt),
            )
            self.assertEqual("transport_receipt", receipt["kind"])
            self.assertEqual(
                MODULE.SESSION_SHARDS_HOLDOUT_REASON,
                receipt["reason"],
            )
            self.assertEqual(
                frozenset({MODULE.SESSION_SHARDS_HOLDOUT_REASON}),
                MODULE.SESSION_SHARDS_HOLDOUT_SAFE_REASONS,
            )
            self.assertIs(receipt["terminal"], True)
            self.assertIs(receipt["content_free"], True)
            self.assertIs(receipt["source_observed"], False)
            self.assertIs(receipt["transport_attempted"], False)
            self.assertIs(receipt["backfill_required"], True)
            self.assertEqual(0o700, stat.S_IMODE(identity_path.stat().st_mode))
            key_path = identity_path / MODULE.SESSION_SHARDS_HOLDOUT_IDENTITY_KEY_FILE
            self.assertEqual(0o600, stat.S_IMODE(key_path.stat().st_mode))

            forbidden_fields = {
                "fragment_b64",
                "payload",
                "raw",
                "record",
                "record_b64",
                "rollout",
                "source_token",
                "text",
            }
            self.assertTrue(forbidden_fields.isdisjoint(receipt))
            for false_reason in (
                "no_activity",
                "invalid_json",
                "timeout",
                "authentication_failed",
                "record_processing_budget_exceeded",
            ):
                self.assertNotEqual(false_reason, receipt["reason"])

            identity_key = MODULE._read_session_shards_shadow_identity_key(
                identity_path
            )
            verified_ref = MODULE._verify_session_shards_holdout_receipt(
                receipt,
                identity_key=identity_key,
                expected_host="hoteng-srv-01",
                expected_window_start="2026-07-13T00:00:00Z",
                expected_window_end="2026-07-14T00:00:00Z",
                expected_source_kind="codex_session_history",
                expected_source_lease_ref=(
                    "source-lease:daily-partial:hoteng-srv-01:1"
                ),
            )
            self.assertEqual(receipt["holdout_ref"], verified_ref)

    def test_holdout_receipt_rejects_cross_binding_use_and_tampering(self) -> None:
        identity_key = b"h" * MODULE.SESSION_SHARDS_HOLDOUT_IDENTITY_KEY_BYTES
        receipt = MODULE._session_shards_holdout_receipt(
            identity_key=identity_key,
            host="hoteng-srv-01",
            window_start="2026-07-13T00:00:00Z",
            window_end="2026-07-14T00:00:00Z",
            source_kind="codex_session_history",
            source_lease_ref="source-lease:daily-partial:hoteng-srv-01:1",
        )
        replacement_receipt = MODULE._session_shards_holdout_receipt(
            identity_key=b"r" * MODULE.SESSION_SHARDS_HOLDOUT_IDENTITY_KEY_BYTES,
            host="hoteng-srv-01",
            window_start="2026-07-13T00:00:00Z",
            window_end="2026-07-14T00:00:00Z",
            source_kind="codex_session_history",
            source_lease_ref="source-lease:daily-partial:hoteng-srv-01:1",
        )
        self.assertNotEqual(
            receipt["identity_key_id"], replacement_receipt["identity_key_id"]
        )
        self.assertNotEqual(receipt["holdout_ref"], replacement_receipt["holdout_ref"])
        expected = {
            "expected_host": "hoteng-srv-01",
            "expected_window_start": "2026-07-13T00:00:00Z",
            "expected_window_end": "2026-07-14T00:00:00Z",
            "expected_source_kind": "codex_session_history",
            "expected_source_lease_ref": ("source-lease:daily-partial:hoteng-srv-01:1"),
        }
        mismatches = {
            "expected_host": "miku-bot-dev",
            "expected_window_start": "2026-07-12T00:00:00Z",
            "expected_window_end": "2026-07-15T00:00:00Z",
            "expected_source_kind": "other_source_kind",
            "expected_source_lease_ref": ("source-lease:daily-partial:hoteng-srv-01:2"),
        }
        for field, mismatch in mismatches.items():
            verification = dict(expected)
            verification[field] = mismatch
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "expected source lease"):
                    MODULE._verify_session_shards_holdout_receipt(
                        receipt,
                        identity_key=identity_key,
                        **verification,
                    )

        tampered = dict(receipt)
        authentication_tag = str(tampered["authentication_tag"])
        replacement = "1" if authentication_tag.endswith("0") else "0"
        tampered["authentication_tag"] = authentication_tag[:-1] + replacement
        with self.assertRaisesRegex(ValueError, "authentication failed"):
            MODULE._verify_session_shards_holdout_receipt(
                tampered,
                identity_key=identity_key,
                **expected,
            )

    def test_holdout_window_rejects_current_unclosed_and_future_utc_days(
        self,
    ) -> None:
        now_utc = dt.datetime(
            2026,
            7,
            14,
            12,
            30,
            tzinfo=dt.timezone.utc,
        )
        self.assertEqual(
            (
                dt.datetime(2026, 7, 13, tzinfo=dt.timezone.utc),
                dt.datetime(2026, 7, 14, tzinfo=dt.timezone.utc),
            ),
            MODULE._session_shards_holdout_daily_window(
                "2026-07-13T00:00:00Z",
                "2026-07-14T00:00:00Z",
                now_utc=now_utc,
            ),
        )
        for window_start, window_end in (
            ("2026-07-14T00:00:00Z", "2026-07-15T00:00:00Z"),
            ("2026-07-15T00:00:00Z", "2026-07-16T00:00:00Z"),
        ):
            with self.subTest(window_end=window_end):
                with self.assertRaisesRegex(ValueError, "latest closed UTC day"):
                    MODULE._session_shards_holdout_daily_window(
                        window_start,
                        window_end,
                        now_utc=now_utc,
                    )

    def test_holdout_cli_checks_the_injected_utc_clock_before_identity_creation(
        self,
    ) -> None:
        now_utc = dt.datetime(2026, 7, 14, 23, 59, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory(prefix="session-shards-holdout.") as raw:
            root = Path(raw)
            root.chmod(0o700)
            for name, window_start, window_end in (
                (
                    "current-day",
                    "2026-07-14T00:00:00Z",
                    "2026-07-15T00:00:00Z",
                ),
                (
                    "future-day",
                    "2026-07-15T00:00:00Z",
                    "2026-07-16T00:00:00Z",
                ),
            ):
                identity_path = root / name
                with (
                    self.subTest(name=name),
                    mock.patch.object(
                        MODULE,
                        "_session_shards_now_utc",
                        return_value=now_utc,
                    ),
                ):
                    returncode, frames, stderr = run_local(
                        root,
                        holdout_command_args(
                            identity_path,
                            window_start=window_start,
                            window_end=window_end,
                        ),
                    )
                    self.assertEqual(2, returncode)
                    self.assertEqual([], frames)
                    self.assertIn("latest closed UTC day", stderr)
                    self.assertFalse(identity_path.exists())

    def test_holdout_consumption_and_backfill_replacement_are_atomic(self) -> None:
        holdout_identity_key = b"h" * MODULE.SESSION_SHARDS_HOLDOUT_IDENTITY_KEY_BYTES
        coordinator_identity_key = (
            b"c" * MODULE.SESSION_SHARDS_COORDINATOR_IDENTITY_KEY_BYTES
        )
        now_utc = dt.datetime(2026, 7, 14, 12, tzinfo=dt.timezone.utc)
        common = {
            "identity_key": holdout_identity_key,
            "host": "hoteng-srv-01",
            "window_start": "2026-07-13T00:00:00Z",
            "window_end": "2026-07-14T00:00:00Z",
            "source_kind": "codex_session_history",
        }
        first_receipt = MODULE._session_shards_holdout_receipt(
            **common,
            source_lease_ref="source-lease:partial:first",
            now_utc=now_utc,
        )
        second_receipt = MODULE._session_shards_holdout_receipt(
            **common,
            source_lease_ref="source-lease:partial:second",
            now_utc=now_utc,
        )

        with tempfile.TemporaryDirectory(prefix="session-shards-ledger.") as raw:
            root = Path(raw)
            root.chmod(0o700)
            ledger_path = root / "campaign.sqlite3"

            def consume(
                receipt: dict[str, object],
                label: str,
                *,
                backfill_run_ref: str | None = None,
                source_transport_receipt_ref: str | None = None,
            ) -> str:
                result = authenticated_backfill_result(
                    holdout_identity_key=holdout_identity_key,
                    coordinator_identity_key=coordinator_identity_key,
                    receipt=receipt,
                    label=label,
                    backfill_run_ref=backfill_run_ref,
                    source_transport_receipt_ref=source_transport_receipt_ref,
                    now_utc=now_utc,
                )
                return MODULE._consume_session_shards_holdout_for_backfill(
                    ledger_path=ledger_path,
                    receipt=receipt,
                    holdout_identity_key=holdout_identity_key,
                    coordinator_identity_key=coordinator_identity_key,
                    backfill_result=result,
                    now_utc=now_utc,
                )

            shared_backfill_run_ref = protocol_ref("run_ref_v2:", "shared-backfill-run")
            first_ref = consume(
                first_receipt,
                "first",
                backfill_run_ref=shared_backfill_run_ref,
            )
            with self.assertRaisesRegex(ValueError, "already recorded"):
                consume(
                    second_receipt,
                    "second-conflict",
                    backfill_run_ref=shared_backfill_run_ref,
                )
            first_transport_receipt_ref = protocol_ref(
                "source_transport_receipt_v2:", "first:transport-receipt"
            )
            with self.assertRaisesRegex(
                ValueError,
                "source transport receipt replay rejected",
            ):
                consume(
                    second_receipt,
                    "second-transport-replay",
                    source_transport_receipt_ref=first_transport_receipt_ref,
                )
            second_ref = consume(second_receipt, "second")

            connection = sqlite3.connect(ledger_path)
            try:
                consumption_count = connection.execute(
                    "SELECT COUNT(*) FROM holdout_consumptions"
                ).fetchone()
                replacement_count = connection.execute(
                    "SELECT COUNT(*) FROM backfill_replacements"
                ).fetchone()
                transport_consumption_count = connection.execute(
                    "SELECT COUNT(*) FROM source_transport_consumptions"
                ).fetchone()
            finally:
                connection.close()

            self.assertEqual(str(first_receipt["holdout_ref"]), first_ref)
            self.assertEqual(str(second_receipt["holdout_ref"]), second_ref)
            self.assertEqual((2,), consumption_count)
            self.assertEqual((2,), replacement_count)
            self.assertEqual((2,), transport_consumption_count)
            self.assertEqual(0o600, stat.S_IMODE(ledger_path.stat().st_mode))

    def test_concurrent_holdout_replay_accepts_exactly_once(self) -> None:
        holdout_identity_key = b"h" * MODULE.SESSION_SHARDS_HOLDOUT_IDENTITY_KEY_BYTES
        coordinator_identity_key = (
            b"c" * MODULE.SESSION_SHARDS_COORDINATOR_IDENTITY_KEY_BYTES
        )
        now_utc = dt.datetime(2026, 7, 14, 12, tzinfo=dt.timezone.utc)
        receipt = MODULE._session_shards_holdout_receipt(
            identity_key=holdout_identity_key,
            host="hoteng-srv-01",
            window_start="2026-07-13T00:00:00Z",
            window_end="2026-07-14T00:00:00Z",
            source_kind="codex_session_history",
            source_lease_ref="source-lease:partial:concurrent",
            now_utc=now_utc,
        )
        backfill_result = authenticated_backfill_result(
            holdout_identity_key=holdout_identity_key,
            coordinator_identity_key=coordinator_identity_key,
            receipt=receipt,
            label="concurrent",
            source_outcome="no_activity",
            now_utc=now_utc,
        )
        barrier = threading.Barrier(2)

        with tempfile.TemporaryDirectory(prefix="session-shards-ledger.") as raw:
            root = Path(raw)
            root.chmod(0o700)
            ledger_path = root / "campaign.sqlite3"

            def consume_once() -> str:
                barrier.wait(timeout=5)
                try:
                    return MODULE._consume_session_shards_holdout_for_backfill(
                        ledger_path=ledger_path,
                        receipt=receipt,
                        holdout_identity_key=holdout_identity_key,
                        coordinator_identity_key=coordinator_identity_key,
                        backfill_result=backfill_result,
                        now_utc=now_utc,
                    )
                except ValueError as exc:
                    return f"error:{exc}"

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _index: consume_once(), range(2)))

            connection = sqlite3.connect(ledger_path)
            try:
                rows = connection.execute(
                    """
                    SELECT c.holdout_ref, r.backfill_run_ref
                    FROM holdout_consumptions AS c
                    JOIN backfill_replacements AS r USING (holdout_ref)
                    """
                ).fetchall()
            finally:
                connection.close()

        self.assertEqual(1, results.count(str(receipt["holdout_ref"])))
        self.assertEqual(
            1,
            sum("replay rejected" in result for result in results),
        )
        self.assertEqual(
            [
                (
                    str(receipt["holdout_ref"]),
                    backfill_result["backfill_run_ref"],
                )
            ],
            rows,
        )

    def test_backfill_authentication_fails_before_ledger_creation(self) -> None:
        holdout_identity_key = b"h" * 32
        coordinator_identity_key = b"c" * 32
        now_utc = dt.datetime(2026, 7, 14, 12, tzinfo=dt.timezone.utc)
        receipt = MODULE._session_shards_holdout_receipt(
            identity_key=holdout_identity_key,
            host="hoteng-srv-01",
            window_start="2026-07-13T00:00:00Z",
            window_end="2026-07-14T00:00:00Z",
            source_kind="codex_session_history",
            source_lease_ref="source-lease:partial:authenticated",
            now_utc=now_utc,
        )
        valid = authenticated_backfill_result(
            holdout_identity_key=holdout_identity_key,
            coordinator_identity_key=coordinator_identity_key,
            receipt=receipt,
            label="authenticated",
            now_utc=now_utc,
        )
        fake_digest = dict(valid)
        fake_digest["evidence_digest"] = "shadow_source_evidence_v2:" + "f" * 64
        synthetic_outcome = dict(valid)
        synthetic_outcome["source_outcome"] = "no_activity"
        stale = authenticated_backfill_result(
            holdout_identity_key=holdout_identity_key,
            coordinator_identity_key=coordinator_identity_key,
            receipt=receipt,
            label="stale",
            now_utc=now_utc - dt.timedelta(seconds=301),
        )

        with tempfile.TemporaryDirectory(prefix="session-shards-auth-fail.") as raw:
            root = Path(raw)
            root.chmod(0o700)
            cases = (
                (
                    "fake-digest",
                    fake_digest,
                    coordinator_identity_key,
                    "authentication|binding",
                ),
                (
                    "synthetic-outcome",
                    synthetic_outcome,
                    coordinator_identity_key,
                    "authentication|binding",
                ),
                ("stale", stale, coordinator_identity_key, "stale"),
                ("wrong-coordinator", valid, b"x" * 32, "coordinator identity"),
            )
            for name, result, coordinator_key, error in cases:
                ledger_path = root / f"{name}.sqlite3"
                with (
                    self.subTest(name=name),
                    self.assertRaisesRegex(ValueError, error),
                ):
                    MODULE._consume_session_shards_holdout_for_backfill(
                        ledger_path=ledger_path,
                        receipt=receipt,
                        holdout_identity_key=holdout_identity_key,
                        coordinator_identity_key=coordinator_key,
                        backfill_result=result,
                        now_utc=now_utc,
                    )
                self.assertFalse(ledger_path.exists())

    def test_holdout_receipt_is_rejected_outside_explicit_shadow_qualification(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="session-shards-holdout.") as raw:
            root = Path(raw)
            root.chmod(0o700)
            named_rollout = holdout_command_args(root / "rollout-identity")
            named_rollout.rollout = "sessions/2026/07/13/rollout-not-allowed.jsonl"
            wrong_emit = holdout_command_args(root / "wrong-emit-identity")
            wrong_emit.emit = "descriptors"
            wrong_emit.rollout = "sessions/2026/07/13/rollout-example.jsonl"
            cases = (
                (
                    holdout_command_args(
                        root / "production-identity",
                        qualification_mode="production",
                    ),
                    "unavailable in production",
                ),
                (
                    holdout_command_args(
                        root / "implicit-identity",
                        controlled_missing_host=False,
                    ),
                    "requires --controlled-missing-host",
                ),
                (
                    holdout_command_args(root / "local-identity", host="local"),
                    "only for a remote host",
                ),
                (
                    holdout_command_args(
                        root / "weekly-identity",
                        window_start="2026-07-07T00:00:00Z",
                        window_end="2026-07-14T00:00:00Z",
                    ),
                    "one exact closed UTC day",
                ),
                (named_rollout, "must not name a rollout"),
                (wrong_emit, "require --emit holdout-receipt"),
            )
            for args, expected_error in cases:
                with self.subTest(expected_error=expected_error):
                    returncode, frames, stderr = run_local(root, args)
                    self.assertEqual(2, returncode)
                    self.assertEqual([], frames)
                    self.assertIn(expected_error, stderr)
                    self.assertFalse(Path(args.shadow_identity_path).exists())

    def test_existing_shadow_identity_is_reused_only_when_owner_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="session-shards-holdout.") as raw:
            root = Path(raw)
            root.chmod(0o700)
            identity_path = root / "identity"
            first_code, first_frames, first_stderr = run_local(
                root,
                holdout_command_args(identity_path),
            )
            second_code, second_frames, second_stderr = run_local(
                root,
                holdout_command_args(
                    identity_path,
                    create_identity=False,
                    source_lease_ref=("source-lease:next-partial:hoteng-srv-01:2"),
                ),
            )

            self.assertEqual(0, first_code, first_stderr)
            self.assertEqual(0, second_code, second_stderr)
            self.assertEqual(
                first_frames[0]["identity_key_id"],
                second_frames[0]["identity_key_id"],
            )
            self.assertNotEqual(
                first_frames[0]["holdout_ref"],
                second_frames[0]["holdout_ref"],
            )

            identity_path.chmod(0o755)
            rejected_code, rejected_frames, rejected_stderr = run_local(
                root,
                holdout_command_args(
                    identity_path,
                    create_identity=False,
                    source_lease_ref=("source-lease:next-partial:hoteng-srv-01:3"),
                ),
            )
            self.assertEqual(2, rejected_code)
            self.assertEqual([], rejected_frames)
            self.assertIn("mode 0700", rejected_stderr)

    def test_existing_shadow_identity_rejects_a_symlink_key(self) -> None:
        with tempfile.TemporaryDirectory(prefix="session-shards-holdout.") as raw:
            root = Path(raw)
            root.chmod(0o700)
            identity_path = root / "identity"
            identity_path.mkdir(mode=0o700)
            external_key = root / "external.key"
            external_key.write_bytes(
                b"k" * MODULE.SESSION_SHARDS_HOLDOUT_IDENTITY_KEY_BYTES
            )
            external_key.chmod(0o600)
            key_path = identity_path / MODULE.SESSION_SHARDS_HOLDOUT_IDENTITY_KEY_FILE
            key_path.symlink_to(external_key)

            returncode, frames, stderr = run_local(
                root,
                holdout_command_args(identity_path, create_identity=False),
            )

            self.assertEqual(2, returncode)
            self.assertEqual([], frames)
            self.assertIn("missing or unsafe", stderr)


class SessionShardsRemoteTests(unittest.TestCase):
    def test_remote_receiver_idle_timeout_resets_on_output_progress(self) -> None:
        request = remote_request(
            "sessions/2026/07/14/rollout-a.jsonl",
            max_shards=2,
        )
        output_lines = remote_output(empty_descriptor_frames(request)).splitlines()
        output_lines[1] += " " * (128 * 1024)
        output = "\n".join(output_lines) + "\n"

        progressing = FakePopen(output, 0)
        with tempfile.TemporaryDirectory(prefix="session-shards-progress.") as raw:
            progress_root = Path(raw)
            progress_root.chmod(0o700)
            progress_path = progress_root / "capture.progress"
            progress_path.touch(mode=0o600)
            progress_path.chmod(0o600)
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "CODEX_SESSION_SHARDS_SHADOW_ROOT": str(progress_root),
                        MODULE.SESSION_SHARDS_CAPTURE_PROGRESS_PATH_ENV: str(
                            progress_path
                        ),
                    },
                ),
                mock.patch.object(
                    MODULE.subprocess,
                    "Popen",
                    return_value=progressing,
                ) as popen,
            ):
                frames = list(
                    MODULE._iter_remote_session_shard_frames(
                        "miku-bot-dev",
                        request,
                    )
                )
            self.assertGreater(progress_path.stat().st_size, 1)
        self.assertEqual("stream_end", frames[-1]["kind"])
        self.assertEqual(
            ["python3", "-I", "-B", "-"],
            popen.call_args.args[0][-4:],
        )

        now = 0.0

        def monotonic() -> float:
            return now

        idle_state = MODULE._RemoteSessionShardsIdleState(
            10.0,
            monotonic=monotonic,
        )
        now = 9.0
        self.assertFalse(idle_state.expired())
        idle_state.mark_progress()
        now = 18.0
        self.assertFalse(idle_state.expired())
        now = 19.0
        self.assertTrue(idle_state.expired())

    def test_remote_receiver_idle_watchdog_kills_only_after_deadline(self) -> None:
        now = 0.0

        def monotonic() -> float:
            return now

        idle_state = MODULE._RemoteSessionShardsIdleState(
            10.0,
            monotonic=monotonic,
        )

        class AdvancingStop:
            observed_timeout: float | None = None

            def wait(self, timeout: float) -> bool:
                nonlocal now
                self.observed_timeout = timeout
                now = 10.0
                return False

        class KillableProcess:
            killed = False

            @staticmethod
            def poll() -> None:
                return None

            def kill(self) -> None:
                self.killed = True

        stop = AdvancingStop()
        process = KillableProcess()
        timed_out = threading.Event()

        MODULE._watch_remote_session_shards_idle(
            process,
            stop,
            timed_out,
            idle_state,
        )

        self.assertEqual(1.0, stop.observed_timeout)
        self.assertTrue(timed_out.is_set())
        self.assertTrue(process.killed)

    def test_remote_receiver_rejects_unbounded_gap_descriptors(self) -> None:
        data = b"not-json\n"
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, data)
            rc, frames, error = run_local(codex_root, command_args(rollout))

        self.assertEqual((rc, error), (0, ""))
        stream_meta = {
            key: value
            for key, value in frame_of_kind(frames, "stream_meta").items()
            if key not in {"host", "rollout"}
        }
        gap = {
            key: value
            for key, value in frame_of_kind(frames, "shard").items()
            if key not in {"host", "rollout"}
        }
        request = remote_request(rollout)

        record_count_gap = dict(gap)
        record_count_gap["record_count"] = 2
        record_count_gap["record_end"] = int(record_count_gap["record_start"]) + 2
        validator = MODULE._RemoteSessionShardsValidator(request=request)
        validator.accept(dict(stream_meta))
        with self.assertRaisesRegex(RuntimeError, "gap descriptor range"):
            validator.accept(record_count_gap)

        invalid_over_budget = MODULE._RemoteSessionShardsValidator(request=request)
        invalid_over_budget.accept(dict(stream_meta))
        assert invalid_over_budget.stream_meta is not None
        invalid_over_budget.stream_meta["record_processing_budget_bytes"] = (
            len(data) - 1
        )
        with self.assertRaisesRegex(RuntimeError, "exceeds its byte ceiling"):
            invalid_over_budget.accept(dict(gap))

        processing_over_scan = MODULE._RemoteSessionShardsValidator(request=request)
        processing_over_scan.accept(dict(stream_meta))
        assert processing_over_scan.stream_meta is not None
        processing_over_scan.stream_meta["record_processing_budget_bytes"] = 1
        processing_over_scan.stream_meta["hard_record_scan_ceiling_bytes"] = (
            len(data) - 1
        )
        processing_gap = dict(gap)
        processing_gap["gap_reason"] = "record_processing_budget_exceeded"
        processing_gap.update(
            {
                "hard_record_processing_ceiling_bytes": stream_meta[
                    "hard_record_processing_ceiling_bytes"
                ],
                "processing_ceiling_kind": "record_bytes",
                "processing_ceiling_limit": 1,
                "processing_ceiling_observed": len(data),
                "record_processing_budget_bytes": 1,
            }
        )
        with self.assertRaisesRegex(RuntimeError, "exceeds its byte ceiling"):
            processing_over_scan.accept(processing_gap)

    def test_remote_program_compiles_and_has_no_child_transport(self) -> None:
        script = MODULE._remote_session_shards_script(
            {
                "emit": "descriptors",
                "rollout": "sessions/2026/07/14/rollout-a.jsonl",
                "codex_root": "/home/hoteng/.codex",
                "byte_start": 0,
                "byte_end": None,
                "shard_bytes": 512,
                "max_shards": 2,
                "source_token": None,
            }
        )

        compile(script, "<session-shards>", "exec")
        self.assertNotIn("subprocess", script)
        self.assertNotIn("ssh", script)
        self.assertNotIn("SpooledTemporaryFile", script)
        self.assertNotIn("import tempfile", script)

    def test_remote_parser_rejects_deep_frames_before_json_loads(self) -> None:
        request = remote_request(
            "sessions/2026/07/14/rollout-a.jsonl",
            max_shards=2,
        )
        depth = MODULE.SESSION_SHARDS_MAX_JSON_NESTING_DEPTH
        nested_values = {
            "arrays": "[" * depth + "0" + "]" * depth,
            "objects": '{"value":' * depth + "0" + "}" * depth,
        }
        self.assertLessEqual(
            4 * MODULE.MAX_SESSION_SHARDS_FRAME_CHARS,
            MODULE.MIN_SESSION_RECORD_PROCESSING_BUDGET_BYTES,
        )
        real_json_loads = MODULE.json.loads
        for name, nested_value in nested_values.items():
            with self.subTest(nesting=name):
                output = "\n".join(
                    [
                        MODULE.REMOTE_SESSION_SHARDS_BEGIN,
                        '{"kind":' + nested_value + "}",
                        MODULE.REMOTE_SESSION_SHARDS_END,
                        "",
                    ]
                )
                fake = FakePopen(output, 0)
                with (
                    mock.patch.object(MODULE.subprocess, "Popen", return_value=fake),
                    mock.patch.object(
                        MODULE.json,
                        "loads",
                        wraps=real_json_loads,
                    ) as json_loads,
                ):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "invalid JSON frame",
                    ):
                        list(
                            MODULE._iter_remote_session_shard_frames(
                                "miku-bot-dev",
                                request,
                            )
                        )
                json_loads.assert_not_called()

    def test_remote_parser_rejects_duplicate_json_object_fields(self) -> None:
        request = remote_request(
            "sessions/2026/07/14/rollout-a.jsonl",
            max_shards=2,
        )
        output = "\n".join(
            [
                MODULE.REMOTE_SESSION_SHARDS_BEGIN,
                '{"kind":"stream_meta","kind":"stream_end"}',
                MODULE.REMOTE_SESSION_SHARDS_END,
                "",
            ]
        )
        fake = FakePopen(output, 0)
        with mock.patch.object(MODULE.subprocess, "Popen", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, "invalid JSON frame"):
                list(
                    MODULE._iter_remote_session_shard_frames(
                        "miku-bot-dev",
                        request,
                    )
                )

    def test_remote_parser_normalizes_json_loads_recursion_error(self) -> None:
        request = remote_request(
            "sessions/2026/07/14/rollout-a.jsonl",
            max_shards=2,
        )
        output = "\n".join(
            [
                MODULE.REMOTE_SESSION_SHARDS_BEGIN,
                '{"kind":"stream_meta"}',
                MODULE.REMOTE_SESSION_SHARDS_END,
                "",
            ]
        )
        fake = FakePopen(output, 0)
        with (
            mock.patch.object(MODULE.subprocess, "Popen", return_value=fake),
            mock.patch.object(
                MODULE.json,
                "loads",
                side_effect=RecursionError("synthetic decoder recursion"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "invalid JSON frame"):
                list(
                    MODULE._iter_remote_session_shard_frames(
                        "miku-bot-dev",
                        request,
                    )
                )

    def test_remote_program_runs_as_a_streaming_standalone_helper(self) -> None:
        data = b'{"text":"remote"}\n'
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, data)
            script = MODULE._remote_session_shards_script(
                {
                    "emit": "descriptors",
                    "rollout": rollout,
                    "codex_root": str(codex_root),
                    "byte_start": 0,
                    "byte_end": None,
                    "shard_bytes": 512,
                    "max_shards": 2,
                    "source_token": None,
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
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], MODULE.REMOTE_SESSION_SHARDS_BEGIN)
        self.assertEqual(lines[-1], MODULE.REMOTE_SESSION_SHARDS_END)
        frames = [json.loads(line) for line in lines[1:-1]]
        self.assertEqual(
            [frame["kind"] for frame in frames],
            [
                "stream_meta",
                "shard",
                "stream_end",
            ],
        )

    def test_remote_program_stops_a_first_record_over_the_hard_scan_ceiling(
        self,
    ) -> None:
        hard_scan_ceiling = 1024
        data = b"x" * (hard_scan_ceiling * 16)
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, data)
            with mock.patch.object(
                MODULE,
                "HARD_SESSION_RECORD_SCAN_CEILING_BYTES",
                hard_scan_ceiling,
            ):
                script = MODULE._remote_session_shards_script(
                    {
                        "emit": "descriptors",
                        "rollout": rollout,
                        "codex_root": str(codex_root),
                        "byte_start": 0,
                        "byte_end": None,
                        "shard_bytes": 512,
                        "max_shards": 2,
                        "source_token": None,
                    }
                )
            result = subprocess.run(
                [sys.executable, "-"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )

        self.assertEqual(1, result.returncode)
        self.assertIn(MODULE.REMOTE_SESSION_SHARDS_BEGIN, result.stdout)
        self.assertNotIn(MODULE.REMOTE_SESSION_SHARDS_END, result.stdout)
        self.assertIn(
            f"hard byte ceiling of {hard_scan_ceiling} bytes",
            result.stderr,
        )

    def test_remote_program_streams_oversized_record_in_bounded_frames(
        self,
    ) -> None:
        data = (
            json.dumps(
                {"type": "message", "text": "\u4f60\u597d\U0001f642" * 70_000},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, data)
            rc, descriptors, error = run_local(
                codex_root,
                command_args(
                    rollout,
                    shard_bytes=MODULE.MAX_SESSION_SHARD_BYTES,
                ),
            )
            token = frame_of_kind(descriptors, "stream_meta")["source_token"]
            script = MODULE._remote_session_shards_script(
                {
                    "emit": "records",
                    "rollout": rollout,
                    "codex_root": str(codex_root),
                    "byte_start": 0,
                    "byte_end": len(data),
                    "shard_bytes": MODULE.MAX_SESSION_SHARD_BYTES,
                    "max_shards": 2,
                    "source_token": token,
                    "record_processing_budget_bytes": (
                        MODULE.DEFAULT_SESSION_RECORD_PROCESSING_BUDGET_BYTES
                    ),
                }
            )
            result = subprocess.run(
                [sys.executable, "-"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual((rc, error), (0, ""))
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], MODULE.REMOTE_SESSION_SHARDS_BEGIN)
        self.assertEqual(lines[-1], MODULE.REMOTE_SESSION_SHARDS_END)
        frame_lines = lines[1:-1]
        self.assertTrue(frame_lines)
        self.assertLessEqual(
            max(len(line) for line in frame_lines),
            MODULE.MAX_SESSION_SHARDS_FRAME_CHARS,
        )
        minimum_full_record_b64_chars = 4 * ((MODULE.MAX_SESSION_SHARD_BYTES + 2) // 3)
        self.assertGreater(
            MODULE.MAX_SESSION_SHARDS_FRAME_CHARS,
            minimum_full_record_b64_chars,
        )
        frames = [json.loads(line) for line in frame_lines]
        fragments = [frame for frame in frames if frame["kind"] == "record_fragment"]
        self.assertGreater(len(fragments), 2)
        self.assertTrue(
            all(
                len(base64.b64decode(frame["fragment_b64"], validate=True))
                <= MODULE.SESSION_SHARDS_RECORD_FRAGMENT_BYTES
                for frame in fragments
            )
        )
        self.assertEqual(reassemble_fragments(frames), data)
        terminal = frame_of_kind(frames, "stream_end")
        self.assertEqual(terminal["emitted_fragments"], len(fragments))
        self.assertEqual(terminal["emitted_fragment_bytes"], len(data))
        self.assertEqual(
            terminal["conservation_proof"]["accounted_byte_count"],
            len(data),
        )

    def test_remote_receiver_rejects_missing_fragment_without_completion(
        self,
    ) -> None:
        data = b'{"text":"' + b"x" * 300_000 + b'"}\n'
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, data)
            rc, descriptors, error = run_local(
                codex_root, command_args(rollout, shard_bytes=64)
            )
            token = frame_of_kind(descriptors, "stream_meta")["source_token"]
            rc_records, records, records_error = run_local(
                codex_root,
                command_args(
                    rollout,
                    emit="records",
                    byte_end=len(data),
                    shard_bytes=64,
                    source_token=str(token),
                ),
            )

        self.assertEqual((rc, error), (0, ""))
        self.assertEqual((rc_records, records_error), (0, ""))
        fragment_indexes = [
            index
            for index, frame in enumerate(records)
            if frame["kind"] == "record_fragment"
        ]
        self.assertGreater(len(fragment_indexes), 1)
        missing_last_fragment = [
            {
                key: value
                for key, value in frame.items()
                if key not in {"host", "rollout"}
            }
            for index, frame in enumerate(records)
            if index != fragment_indexes[-1]
        ]
        output = "\n".join(
            [
                MODULE.REMOTE_SESSION_SHARDS_BEGIN,
                *(
                    json.dumps(frame, separators=(",", ":"), sort_keys=True)
                    for frame in missing_last_fragment
                ),
                MODULE.REMOTE_SESSION_SHARDS_END,
                "",
            ]
        )
        fake = FakePopen(output, 0)
        with mock.patch.object(MODULE.subprocess, "Popen", return_value=fake):
            with self.assertRaisesRegex(
                RuntimeError,
                "ended inside a fragmented record",
            ):
                list(
                    MODULE._iter_remote_session_shard_frames(
                        "miku-bot-dev",
                        remote_request(
                            rollout,
                            emit="records",
                            byte_end=len(data),
                            shard_bytes=64,
                            max_shards=64,
                            source_token=str(token),
                        ),
                    )
                )

    def test_remote_receiver_rejects_impossible_direct_record_frames(self) -> None:
        data = b'{"lf":1}\n{"crlf":2}\r\n{"final":3}'
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, data)
            rc, descriptors, error = run_local(codex_root, command_args(rollout))
            token = str(frame_of_kind(descriptors, "stream_meta")["source_token"])
            rc_records, records, records_error = run_local(
                codex_root,
                command_args(
                    rollout,
                    emit="records",
                    byte_end=len(data),
                    source_token=token,
                ),
            )
        self.assertEqual((rc, error), (0, ""))
        self.assertEqual((rc_records, records_error), (0, ""))
        request = remote_request(
            rollout,
            emit="records",
            byte_end=len(data),
            source_token=token,
        )
        record_indexes = [
            index for index, frame in enumerate(records) if frame["kind"] == "record"
        ]
        self.assertEqual(
            [1, 2, 0], [records[index]["delimiter_bytes"] for index in record_indexes]
        )

        cases: list[tuple[str, list[dict[str, object]]]] = []
        for label, index, delimiter in (
            ("lf-as-none", record_indexes[0], 0),
            ("lf-as-crlf", record_indexes[0], 2),
            ("crlf-as-lf", record_indexes[1], 1),
            ("final-as-lf", record_indexes[2], 1),
        ):
            mutated = [dict(frame) for frame in records]
            mutated[index]["delimiter_bytes"] = delimiter
            refresh_record_accounting_commitment(mutated)
            cases.append((label, mutated))

        zero = [dict(frame) for frame in records]
        first = zero[record_indexes[0]]
        first["byte_end"] = first["byte_start"]
        first["byte_count"] = 0
        first["delimiter_bytes"] = 0
        first["record_b64"] = ""
        first["record_commitment"] = "sha256:" + hashlib.sha256(b"").hexdigest()
        refresh_record_accounting_commitment(zero)
        cases.append(("zero-byte", zero))

        changed_payload = [dict(frame) for frame in records]
        first = changed_payload[record_indexes[0]]
        payload = base64.b64decode(str(first["record_b64"]), validate=True)[:-1] + b"x"
        first["record_b64"] = base64.b64encode(payload).decode("ascii")
        first["record_commitment"] = "sha256:" + hashlib.sha256(payload).hexdigest()
        refresh_record_accounting_commitment(changed_payload)
        cases.append(("recommitted-payload", changed_payload))

        for label, mutated in cases:
            with self.subTest(label=label):
                fake = FakePopen(remote_output(mutated), 0)
                with mock.patch.object(MODULE.subprocess, "Popen", return_value=fake):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "delimiter|coordinates|byte_end",
                    ):
                        list(
                            MODULE._iter_remote_session_shard_frames(
                                "miku-bot-dev",
                                request,
                            )
                        )

    def test_remote_receiver_rejects_recommitted_fragment_delimiter_mismatch(
        self,
    ) -> None:
        fragment_bytes = MODULE.SESSION_SHARDS_RECORD_FRAGMENT_BYTES
        prefix = b'{"text":"'
        suffix = b'"}\r\n'
        data = prefix + b"x" * (fragment_bytes + 1 - len(prefix) - len(suffix)) + suffix
        self.assertEqual(fragment_bytes + 1, len(data))
        self.assertEqual(b"\r\n", data[fragment_bytes - 1 : fragment_bytes + 1])
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, data)
            rc, descriptors, error = run_local(
                codex_root, command_args(rollout, shard_bytes=64)
            )
            token = str(frame_of_kind(descriptors, "stream_meta")["source_token"])
            rc_records, records, records_error = run_local(
                codex_root,
                command_args(
                    rollout,
                    emit="records",
                    byte_end=len(data),
                    shard_bytes=64,
                    source_token=token,
                ),
            )
        self.assertEqual((rc, error), (0, ""))
        self.assertEqual((rc_records, records_error), (0, ""))
        request = remote_request(
            rollout,
            emit="records",
            byte_end=len(data),
            shard_bytes=64,
            source_token=token,
        )
        mutated = [dict(frame) for frame in records]
        fragment_indexes = [
            index
            for index, frame in enumerate(mutated)
            if frame["kind"] == "record_fragment"
        ]
        for index in fragment_indexes:
            mutated[index]["delimiter_bytes"] = 1
        refresh_record_accounting_commitment(mutated)

        fake = FakePopen(remote_output(mutated), 0)
        with mock.patch.object(MODULE.subprocess, "Popen", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, "delimiter"):
                list(
                    MODULE._iter_remote_session_shard_frames(
                        "miku-bot-dev",
                        request,
                    )
                )

        recommitted = [dict(frame) for frame in records]
        last = recommitted[fragment_indexes[-1]]
        payload = base64.b64decode(str(last["fragment_b64"]), validate=True)
        payload = payload[:-1] + b"x"
        last["fragment_b64"] = base64.b64encode(payload).decode("ascii")
        last["fragment_commitment"] = "sha256:" + hashlib.sha256(payload).hexdigest()
        whole = b"".join(
            base64.b64decode(str(recommitted[index]["fragment_b64"]), validate=True)
            for index in fragment_indexes
        )
        whole_commitment = "sha256:" + hashlib.sha256(whole).hexdigest()
        for index in fragment_indexes:
            recommitted[index]["record_commitment"] = whole_commitment
        refresh_record_accounting_commitment(recommitted)
        fake = FakePopen(remote_output(recommitted), 0)
        with mock.patch.object(MODULE.subprocess, "Popen", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, "delimiter"):
                list(
                    MODULE._iter_remote_session_shard_frames(
                        "miku-bot-dev",
                        request,
                    )
                )

    def test_remote_receiver_preserves_paginated_descriptor_cursor(self) -> None:
        lines = [b'{"n":1}\n', b'{"n":2}\n', b'{"n":3}\n']
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, b"".join(lines))
            rc, local_frames, error = run_local(
                codex_root,
                command_args(
                    rollout,
                    shard_bytes=len(lines[0]),
                    max_shards=2,
                ),
            )

        self.assertEqual((rc, error), (0, ""))
        remote_frames = [
            {
                key: value
                for key, value in frame.items()
                if key not in {"host", "rollout"}
            }
            for frame in local_frames
        ]
        output = "\n".join(
            [
                MODULE.REMOTE_SESSION_SHARDS_BEGIN,
                *(
                    json.dumps(frame, separators=(",", ":"), sort_keys=True)
                    for frame in remote_frames
                ),
                MODULE.REMOTE_SESSION_SHARDS_END,
                "",
            ]
        )
        fake = FakePopen(output, 0)
        with mock.patch.object(MODULE.subprocess, "Popen", return_value=fake):
            received = list(
                MODULE._iter_remote_session_shard_frames(
                    "miku-bot-dev",
                    remote_request(
                        rollout,
                        shard_bytes=len(lines[0]),
                        max_shards=2,
                    ),
                )
            )

        terminal = frame_of_kind(received, "stream_end")
        self.assertFalse(terminal["complete"])
        self.assertEqual(terminal["next_byte_start"], len(lines[0] + lines[1]))
        self.assertEqual(terminal["next_record_start"], 2)
        self.assertTrue(
            str(terminal["next_resume_cursor"]).startswith(
                MODULE.SESSION_SHARDS_RESUME_CURSOR_PREFIX
            )
        )

    def test_remote_receiver_rejects_unmarked_oversized_ready_descriptor(
        self,
    ) -> None:
        data = b'{"text":"' + b"x" * 128 + b'"}\n'
        shard_bytes = 64
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, data)
            rc, frames, error = run_local(
                codex_root,
                command_args(rollout, shard_bytes=shard_bytes),
            )

        self.assertEqual((rc, error), (0, ""))
        descriptor_index = next(
            index for index, frame in enumerate(frames) if frame["kind"] == "shard"
        )
        adversarial = [dict(frame) for frame in frames]
        for field in MODULE._SESSION_SHARDS_OVERSIZED_DESCRIPTOR_FIELDS:
            adversarial[descriptor_index].pop(field)
        request = remote_request(rollout, shard_bytes=shard_bytes)
        fake = FakePopen(remote_output(adversarial), 0)

        with mock.patch.object(MODULE.subprocess, "Popen", return_value=fake):
            receiver = MODULE._iter_remote_session_shard_frames(
                "miku-bot-dev",
                request,
            )
            self.assertEqual(next(receiver)["kind"], "stream_meta")
            with self.assertRaisesRegex(
                RuntimeError,
                "exceeds shard_bytes.*without the oversized record contract",
            ):
                next(receiver)

    def test_remote_receiver_rejects_misbinding_descriptor_cursors(self) -> None:
        lines = [b'{"n":1}\n', b'{"n":2}\n', b'{"n":3}\n']
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, b"".join(lines))
            rc, frames, error = run_local(
                codex_root,
                command_args(
                    rollout,
                    shard_bytes=len(lines[0]),
                    max_shards=2,
                ),
            )

        self.assertEqual((rc, error), (0, ""))
        request = remote_request(
            rollout,
            shard_bytes=len(lines[0]),
            max_shards=2,
        )
        shard_indexes = [
            index for index, frame in enumerate(frames) if frame["kind"] == "shard"
        ]
        terminal_index = next(
            index for index, frame in enumerate(frames) if frame["kind"] == "stream_end"
        )

        bad_shard_frames = [dict(frame) for frame in frames]
        bad_shard_frames[shard_indexes[0]]["resume_cursor"] = frames[shard_indexes[1]][
            "resume_cursor"
        ]
        fake_shard = FakePopen(remote_output(bad_shard_frames), 0)
        with mock.patch.object(MODULE.subprocess, "Popen", return_value=fake_shard):
            receiver = MODULE._iter_remote_session_shard_frames(
                "miku-bot-dev",
                request,
            )
            self.assertEqual(next(receiver)["kind"], "stream_meta")
            with self.assertRaisesRegex(RuntimeError, "cursor coordinates"):
                next(receiver)

        bad_terminal_frames = [dict(frame) for frame in frames]
        bad_terminal_frames[terminal_index]["next_resume_cursor"] = frames[
            shard_indexes[0]
        ]["resume_cursor"]
        fake_terminal = FakePopen(remote_output(bad_terminal_frames), 0)
        with mock.patch.object(MODULE.subprocess, "Popen", return_value=fake_terminal):
            with self.assertRaisesRegex(RuntimeError, "cursor coordinates"):
                list(
                    MODULE._iter_remote_session_shard_frames(
                        "miku-bot-dev",
                        request,
                    )
                )

    def test_remote_receiver_binds_stream_meta_to_the_exact_request(self) -> None:
        data = b'{"n":1}\n'
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, data)
            rc, descriptors, error = run_local(codex_root, command_args(rollout))
            token = str(frame_of_kind(descriptors, "stream_meta")["source_token"])
            rc_records, records, records_error = run_local(
                codex_root,
                command_args(
                    rollout,
                    emit="records",
                    byte_end=len(data),
                    source_token=token,
                ),
            )

        self.assertEqual((rc, error), (0, ""))
        self.assertEqual((rc_records, records_error), (0, ""))
        request = remote_request(
            rollout,
            emit="records",
            byte_end=len(data),
            source_token=token,
        )
        request_mutations = {
            "mode": {"emit": "descriptors"},
            "rollout": {"rollout": "sessions/2026/07/14/rollout-other.jsonl"},
            "source_token": {
                "source_token": MODULE.SESSION_SHARDS_SOURCE_TOKEN_PREFIX + "f" * 64
            },
            "byte_start": {"byte_start": 1},
            "byte_end": {"byte_end": len(data) - 1},
            "shard_bytes": {"shard_bytes": 513},
            "max_shards": {"max_shards": 63},
            "processing_budget": {
                "record_processing_budget_bytes": (
                    MODULE.DEFAULT_SESSION_RECORD_PROCESSING_BUDGET_BYTES + 1
                )
            },
            "resume_cursor": {
                "resume_cursor": MODULE.SESSION_SHARDS_RESUME_CURSOR_PREFIX + "forged"
            },
        }
        output = remote_output(records)
        for name, mutation in request_mutations.items():
            with self.subTest(name=name):
                mismatched_request = {**request, **mutation}
                fake = FakePopen(output, 0)
                with mock.patch.object(MODULE.subprocess, "Popen", return_value=fake):
                    receiver = MODULE._iter_remote_session_shard_frames(
                        "miku-bot-dev",
                        mismatched_request,
                    )
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "does not match|invalid resume cursor|not bound",
                    ):
                        next(receiver)

        for name, field, value in (
            ("schema", "schema", "session-shards-v999"),
            ("record_start", "record_start", 1),
            (
                "fragment_limit",
                "record_fragment_bytes",
                MODULE.SESSION_SHARDS_RECORD_FRAGMENT_BYTES + 1,
            ),
            ("source_size", "source_bytes", 0),
            (
                "response_source_token",
                "source_token",
                MODULE.SESSION_SHARDS_SOURCE_TOKEN_PREFIX + "e" * 64,
            ),
            ("request_binding", "request_binding", "session_shards_request_v1:bad"),
        ):
            with self.subTest(name=name):
                mismatched_frames = [dict(frame) for frame in records]
                mismatched_frames[0][field] = value
                fake = FakePopen(remote_output(mismatched_frames), 0)
                with mock.patch.object(MODULE.subprocess, "Popen", return_value=fake):
                    receiver = MODULE._iter_remote_session_shard_frames(
                        "miku-bot-dev",
                        request,
                    )
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "does not match|does not cover",
                    ):
                        next(receiver)

        missing_none_field_frames = [dict(frame) for frame in records]
        missing_none_field_frames[0].pop("request_resume_cursor")
        fake = FakePopen(remote_output(missing_none_field_frames), 0)
        with mock.patch.object(MODULE.subprocess, "Popen", return_value=fake):
            receiver = MODULE._iter_remote_session_shard_frames(
                "miku-bot-dev",
                request,
            )
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                next(receiver)

    def test_remote_receiver_enforces_closed_schemas_for_all_frame_variants(
        self,
    ) -> None:
        small = b'{"n":1}\n'
        oversized = b'{"text":"' + b"x" * 128 + b'"}\n'
        invalid = b"not-json\n"
        depth = MODULE.SESSION_SHARDS_MAX_JSON_NESTING_DEPTH
        over_depth = b'{"deep":' + b"[" * depth + b"0" + b"]" * depth + b"}\n"
        data = small + oversized + invalid + over_depth

        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, data)
            rc_descriptors, descriptors, descriptor_error = run_local(
                codex_root,
                command_args(
                    rollout,
                    shard_bytes=64,
                    max_shards=64,
                ),
            )
            token = str(frame_of_kind(descriptors, "stream_meta")["source_token"])
            rc_records, records, record_error = run_local(
                codex_root,
                command_args(
                    rollout,
                    emit="records",
                    byte_end=len(data),
                    shard_bytes=64,
                    max_shards=64,
                    source_token=token,
                ),
            )
            rc_max, max_page, max_error = run_local(
                codex_root,
                command_args(
                    rollout,
                    shard_bytes=64,
                    max_shards=1,
                ),
            )

        self.assertEqual((rc_descriptors, descriptor_error), (0, ""))
        self.assertEqual((rc_records, record_error), (0, ""))
        self.assertEqual((rc_max, max_error), (0, ""))
        descriptor_request = remote_request(
            rollout,
            shard_bytes=64,
            max_shards=64,
        )
        record_request = remote_request(
            rollout,
            emit="records",
            byte_end=len(data),
            shard_bytes=64,
            max_shards=64,
            source_token=token,
        )
        max_request = remote_request(
            rollout,
            shard_bytes=64,
            max_shards=1,
        )

        def matching_index(
            frames: list[dict[str, object]],
            **expected: object,
        ) -> int:
            return next(
                index
                for index, frame in enumerate(frames)
                if all(frame.get(key) == value for key, value in expected.items())
            )

        cases = [
            (
                "stream_meta",
                descriptors,
                descriptor_request,
                matching_index(descriptors, kind="stream_meta"),
                "unexpected_field",
            ),
            (
                "ready_descriptor",
                descriptors,
                descriptor_request,
                next(
                    index
                    for index, frame in enumerate(descriptors)
                    if frame.get("kind") == "shard"
                    and frame.get("status") == "ready"
                    and "oversized_record" not in frame
                ),
                "unexpected_field",
            ),
            (
                "oversized_descriptor",
                descriptors,
                descriptor_request,
                matching_index(
                    descriptors,
                    kind="shard",
                    status="ready",
                    oversized_record=True,
                ),
                "unexpected_field",
            ),
            (
                "invalid_gap_descriptor",
                descriptors,
                descriptor_request,
                matching_index(
                    descriptors,
                    kind="shard",
                    status="gap",
                    gap_reason="invalid_json",
                ),
                "record_b64",
            ),
            (
                "processing_gap_descriptor",
                descriptors,
                descriptor_request,
                matching_index(
                    descriptors,
                    kind="shard",
                    status="gap",
                    gap_reason="record_processing_budget_exceeded",
                ),
                "record_b64",
            ),
            (
                "eof_terminal",
                descriptors,
                descriptor_request,
                matching_index(descriptors, kind="stream_end", reason="eof"),
                "unexpected_field",
            ),
            (
                "record",
                records,
                record_request,
                matching_index(records, kind="record"),
                "unexpected_field",
            ),
            (
                "record_fragment",
                records,
                record_request,
                matching_index(records, kind="record_fragment"),
                "unexpected_field",
            ),
            (
                "invalid_gap",
                records,
                record_request,
                matching_index(records, kind="gap", reason="invalid_json"),
                "record_b64",
            ),
            (
                "processing_gap",
                records,
                record_request,
                matching_index(
                    records,
                    kind="gap",
                    reason="record_processing_budget_exceeded",
                ),
                "record_b64",
            ),
            (
                "range_terminal",
                records,
                record_request,
                matching_index(
                    records,
                    kind="stream_end",
                    reason="range_complete",
                ),
                "unexpected_field",
            ),
            (
                "max_shards_terminal",
                max_page,
                max_request,
                matching_index(
                    max_page,
                    kind="stream_end",
                    reason="max_shards",
                ),
                "unexpected_field",
            ),
        ]
        for label, frames, request, index, extra_field in cases:
            with self.subTest(frame=label):
                mutated = [dict(frame) for frame in frames]
                mutated[index][extra_field] = "must be rejected"
                fake = FakePopen(remote_output(mutated), 0)
                with mock.patch.object(
                    MODULE.subprocess,
                    "Popen",
                    return_value=fake,
                ):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "closed field schema",
                    ):
                        list(
                            MODULE._iter_remote_session_shard_frames(
                                "miku-bot-dev",
                                request,
                            )
                        )

        proof_frames = [dict(frame) for frame in records]
        proof_terminal_index = matching_index(
            proof_frames,
            kind="stream_end",
            reason="range_complete",
        )
        proof = dict(proof_frames[proof_terminal_index]["conservation_proof"])
        proof["unexpected_field"] = "must be rejected"
        proof_frames[proof_terminal_index]["conservation_proof"] = proof
        fake_proof = FakePopen(remote_output(proof_frames), 0)
        with mock.patch.object(
            MODULE.subprocess,
            "Popen",
            return_value=fake_proof,
        ):
            with self.assertRaisesRegex(RuntimeError, "closed field schema"):
                list(
                    MODULE._iter_remote_session_shard_frames(
                        "miku-bot-dev",
                        record_request,
                    )
                )

        reason_cases = [
            (
                "descriptor_gap",
                descriptors,
                descriptor_request,
                matching_index(
                    descriptors,
                    kind="shard",
                    gap_reason="invalid_json",
                ),
                "gap_reason",
                "future_gap",
            ),
            (
                "record_gap",
                records,
                record_request,
                matching_index(records, kind="gap", reason="invalid_json"),
                "reason",
                "future_gap",
            ),
            (
                "descriptor_terminal",
                descriptors,
                descriptor_request,
                matching_index(descriptors, kind="stream_end", reason="eof"),
                "reason",
                "range_complete",
            ),
            (
                "record_terminal",
                records,
                record_request,
                matching_index(
                    records,
                    kind="stream_end",
                    reason="range_complete",
                ),
                "reason",
                "eof",
            ),
        ]
        for label, frames, request, index, field, value in reason_cases:
            with self.subTest(reason=label):
                mutated = [dict(frame) for frame in frames]
                mutated[index][field] = value
                fake = FakePopen(remote_output(mutated), 0)
                with mock.patch.object(
                    MODULE.subprocess,
                    "Popen",
                    return_value=fake,
                ):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "closed-schema reason|range_complete",
                    ):
                        list(
                            MODULE._iter_remote_session_shard_frames(
                                "miku-bot-dev",
                                request,
                            )
                        )

    def test_remote_receiver_rejects_boolean_terminal_integers(self) -> None:
        first = b'{"n":1}\n'
        data = first + b'{"n":2}\n'
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, data)
            rc_descriptors, descriptors, descriptor_error = run_local(
                codex_root,
                command_args(
                    rollout,
                    shard_bytes=len(first),
                    max_shards=1,
                ),
            )
            token = str(frame_of_kind(descriptors, "stream_meta")["source_token"])
            rc_records, records, record_error = run_local(
                codex_root,
                command_args(
                    rollout,
                    emit="records",
                    byte_end=len(first),
                    shard_bytes=len(first),
                    max_shards=1,
                    source_token=token,
                ),
            )

        self.assertEqual((rc_descriptors, descriptor_error), (0, ""))
        self.assertEqual((rc_records, record_error), (0, ""))
        descriptor_request = remote_request(
            rollout,
            shard_bytes=len(first),
            max_shards=1,
        )
        record_request = remote_request(
            rollout,
            emit="records",
            byte_end=len(first),
            shard_bytes=len(first),
            max_shards=1,
            source_token=token,
        )
        descriptor_terminal_index = next(
            index
            for index, frame in enumerate(descriptors)
            if frame.get("kind") == "stream_end"
        )
        record_terminal_index = next(
            index
            for index, frame in enumerate(records)
            if frame.get("kind") == "stream_end"
        )
        cases = (
            (
                "descriptor emitted count",
                descriptors,
                descriptor_request,
                descriptor_terminal_index,
                None,
                "emitted_shards",
            ),
            (
                "descriptor accounting count",
                descriptors,
                descriptor_request,
                descriptor_terminal_index,
                None,
                "accounted_record_count",
            ),
            (
                "descriptor continuation",
                descriptors,
                descriptor_request,
                descriptor_terminal_index,
                None,
                "next_record_start",
            ),
            (
                "record emitted count",
                records,
                record_request,
                record_terminal_index,
                None,
                "emitted_records",
            ),
            (
                "proof record count",
                records,
                record_request,
                record_terminal_index,
                "conservation_proof",
                "record_count",
            ),
            (
                "proof accounting count",
                records,
                record_request,
                record_terminal_index,
                "conservation_proof",
                "accounted_record_count",
            ),
        )
        for label, frames, request, index, nested_key, field in cases:
            with self.subTest(field=label):
                mutated = [dict(frame) for frame in frames]
                target = mutated[index]
                if nested_key is not None:
                    nested = dict(target[nested_key])
                    target[nested_key] = nested
                    target = nested
                self.assertEqual(1, target[field])
                target[field] = True
                fake = FakePopen(remote_output(mutated), 0)
                with mock.patch.object(
                    MODULE.subprocess,
                    "Popen",
                    return_value=fake,
                ):
                    with self.assertRaisesRegex(RuntimeError, f"invalid {field}"):
                        list(
                            MODULE._iter_remote_session_shard_frames(
                                "miku-bot-dev",
                                request,
                            )
                        )

    def test_remote_receiver_checks_every_data_and_terminal_binding(self) -> None:
        data = b'{"n":1}\n'
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            rollout = write_rollout(codex_root, data)
            rc, descriptors, error = run_local(codex_root, command_args(rollout))
            token = str(frame_of_kind(descriptors, "stream_meta")["source_token"])
            rc_records, records, records_error = run_local(
                codex_root,
                command_args(
                    rollout,
                    emit="records",
                    byte_end=len(data),
                    source_token=token,
                ),
            )

        self.assertEqual((rc, error), (0, ""))
        self.assertEqual((rc_records, records_error), (0, ""))
        request = remote_request(
            rollout,
            emit="records",
            byte_end=len(data),
            source_token=token,
        )
        record_index = next(
            index for index, frame in enumerate(records) if frame["kind"] == "record"
        )
        terminal_index = next(
            index
            for index, frame in enumerate(records)
            if frame["kind"] == "stream_end"
        )

        for name, field, value in (
            ("schema", "schema", "session-shards-v999"),
            ("mode", "mode", "descriptors"),
            (
                "source_token",
                "source_token",
                MODULE.SESSION_SHARDS_SOURCE_TOKEN_PREFIX + "d" * 64,
            ),
            ("request_binding", "request_binding", "wrong"),
        ):
            with self.subTest(frame="record", mismatch=name):
                bad_record_frames = [dict(frame) for frame in records]
                bad_record_frames[record_index][field] = value
                fake_record = FakePopen(remote_output(bad_record_frames), 0)
                with mock.patch.object(
                    MODULE.subprocess,
                    "Popen",
                    return_value=fake_record,
                ):
                    receiver = MODULE._iter_remote_session_shard_frames(
                        "miku-bot-dev",
                        request,
                    )
                    self.assertEqual(next(receiver)["kind"], "stream_meta")
                    with self.assertRaisesRegex(RuntimeError, "request binding"):
                        next(receiver)

        for name, field, value in (
            ("schema", "schema", "session-shards-v999"),
            ("mode", "mode", "descriptors"),
            (
                "source_token",
                "source_token",
                MODULE.SESSION_SHARDS_SOURCE_TOKEN_PREFIX + "c" * 64,
            ),
            ("request_binding", "request_binding", "wrong"),
        ):
            with self.subTest(frame="terminal", mismatch=name):
                bad_terminal_frames = [dict(frame) for frame in records]
                bad_terminal_frames[terminal_index][field] = value
                fake_terminal = FakePopen(remote_output(bad_terminal_frames), 0)
                with mock.patch.object(
                    MODULE.subprocess,
                    "Popen",
                    return_value=fake_terminal,
                ):
                    with self.assertRaisesRegex(RuntimeError, "request binding"):
                        list(
                            MODULE._iter_remote_session_shard_frames(
                                "miku-bot-dev",
                                request,
                            )
                        )

    def test_remote_program_preserves_root_rollout_support(self) -> None:
        data = b'{"text":"root"}\n'
        with tempfile.TemporaryDirectory() as raw:
            codex_root = Path(raw) / ".codex"
            codex_root.mkdir(parents=True)
            rollout = "rollout-2026-07-14T10-00-00-root.jsonl"
            (codex_root / rollout).write_bytes(data)
            script = MODULE._remote_session_shards_script(
                {
                    "emit": "descriptors",
                    "rollout": rollout,
                    "codex_root": str(codex_root),
                    "byte_start": 0,
                    "byte_end": None,
                    "shard_bytes": 512,
                    "max_shards": 2,
                    "source_token": None,
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
        frames = [json.loads(line) for line in result.stdout.splitlines()[1:-1]]
        self.assertTrue(frame_of_kind(frames, "stream_end")["complete"])

    def test_missing_end_marker_withholds_terminal_frame(self) -> None:
        request = remote_request(
            "sessions/2026/07/14/rollout-a.jsonl",
            max_shards=2,
        )
        output = remote_output(empty_descriptor_frames(request), include_end=False)
        fake = FakePopen(output, 0)
        with mock.patch.object(MODULE.subprocess, "Popen", return_value=fake):
            frames = MODULE._iter_remote_session_shard_frames(
                "miku-bot-dev",
                request,
            )
            self.assertEqual(next(frames)["kind"], "stream_meta")
            with self.assertRaisesRegex(RuntimeError, "truncated before end marker"):
                next(frames)

    def test_missing_stream_end_cannot_be_treated_as_completion(self) -> None:
        request = remote_request(
            "sessions/2026/07/14/rollout-a.jsonl",
            max_shards=2,
        )
        output = remote_output(empty_descriptor_frames(request)[:1])
        fake = FakePopen(output, 0)
        with mock.patch.object(MODULE.subprocess, "Popen", return_value=fake):
            frames = MODULE._iter_remote_session_shard_frames(
                "miku-bot-dev",
                request,
            )
            self.assertEqual(next(frames)["kind"], "stream_meta")
            with self.assertRaisesRegex(RuntimeError, "truncated before stream_end"):
                next(frames)

    def test_data_after_end_marker_is_rejected(self) -> None:
        request = remote_request(
            "sessions/2026/07/14/rollout-a.jsonl",
            max_shards=2,
        )
        frames = empty_descriptor_frames(request)
        output = "\n".join(
            [
                MODULE.REMOTE_SESSION_SHARDS_BEGIN,
                *(json.dumps(frame, separators=(",", ":")) for frame in frames),
                MODULE.REMOTE_SESSION_SHARDS_END,
                '{"kind":"shard"}',
                "",
            ]
        )
        fake = FakePopen(output, 0)
        with mock.patch.object(MODULE.subprocess, "Popen", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, "data after the end marker"):
                list(
                    MODULE._iter_remote_session_shard_frames(
                        "miku-bot-dev",
                        request,
                    )
                )

    def test_ssh_failure_is_reported_without_complete_output(self) -> None:
        fake = FakePopen("ssh: connect to host failed\n", 255)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(MODULE.subprocess, "Popen", return_value=fake),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            rc = MODULE.cmd_session_shards(
                command_args(
                    "sessions/2026/07/14/rollout-a.jsonl",
                    host="miku-bot-dev",
                )
            )

        self.assertEqual(rc, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("host=miku-bot-dev", stderr.getvalue())
        self.assertIn("ssh: connect to host failed", stderr.getvalue())

    def test_remote_diagnostic_flood_keeps_only_one_bounded_message(self) -> None:
        diagnostic_lines = [
            f"diagnostic-{index:04d}:" + "界" * 400 for index in range(2_000)
        ]
        fake = FakePopen("\n".join([*diagnostic_lines, ""]), 255)
        request = remote_request("sessions/2026/07/14/rollout-a.jsonl")
        with mock.patch.object(MODULE.subprocess, "Popen", return_value=fake):
            with self.assertRaises(RuntimeError) as caught:
                list(
                    MODULE._iter_remote_session_shard_frames(
                        "miku-bot-dev",
                        request,
                    )
                )

        message = str(caught.exception)
        self.assertTrue(message.startswith("diagnostic-1999:"), message)
        self.assertNotIn("diagnostic-0000:", message)
        self.assertLessEqual(
            len(message.encode("utf-8")),
            MODULE.MAX_REMOTE_SESSION_SHARDS_DIAGNOSTIC_BYTES,
        )


class SessionShardsCompatibilityTests(unittest.TestCase):
    def test_isolated_review_contract_is_filtered_as_wrapper_noise(self) -> None:
        wrapper = (
            "Persistent isolated code-review contract:\n"
            "Check sandbox, credentials, privacy, and verification."
        )

        self.assertIn(
            "Persistent isolated code-review contract:",
            MODULE.WRAPPER_PREFIXES,
        )
        self.assertEqual("", MODULE._meaningful_prompt_text(wrapper))
        self.assertIsNone(
            MODULE._build_summary_record(
                kind="user_message",
                text=wrapper,
                line_no=1,
                timestamp="2026-07-16T00:00:00Z",
                max_text_chars=1200,
            )
        )

    def test_existing_v1_command_defaults_and_remote_program_stay_available(
        self,
    ) -> None:
        parser = MODULE.build_parser()
        args = parser.parse_args(
            [
                "rollout-summary",
                "--host",
                "local",
                "--rollout",
                "sessions/2026/05/26/rollout-a.jsonl",
            ]
        )
        shard_args = parser.parse_args(
            [
                "session-shards",
                "--host",
                "local",
                "--rollout",
                "sessions/2026/05/26/rollout-a.jsonl",
            ]
        )
        old_remote_script = MODULE._remote_python_script(
            {
                "mode": "fetch-rollout",
                "rollout": "sessions/2026/05/26/rollout-a.jsonl",
                "codex_root": "/home/hoteng/.codex",
                "max_fetch_rollout_bytes": 8,
            }
        )

        self.assertIs(args.func, MODULE.cmd_rollout_summary)
        self.assertEqual(args.limit, 40)
        self.assertEqual(args.tail_records, 8)
        self.assertEqual(shard_args.emit, "descriptors")
        self.assertIsNone(shard_args.resume_cursor)
        self.assertEqual("production", shard_args.qualification_mode)
        self.assertFalse(shard_args.controlled_missing_host)
        self.assertIsNone(shard_args.shadow_identity_path)
        self.assertFalse(shard_args.create_shadow_identity)
        self.assertFalse(shard_args.require_existing_shadow_identity)
        self.assertEqual(shard_args.shard_bytes, MODULE.DEFAULT_SESSION_SHARD_BYTES)
        self.assertEqual(
            shard_args.record_processing_budget_bytes,
            MODULE.DEFAULT_SESSION_RECORD_PROCESSING_BUDGET_BYTES,
        )
        compile(old_remote_script, "<fetch-rollout>", "exec")

    def test_private_command_parsers_keep_existing_defaults(self) -> None:
        parser = MODULE.build_parser()
        rollout = "sessions/2026/05/26/rollout-a.jsonl"
        cases = (
            (
                ["preflight", "--host", "local"],
                MODULE.cmd_preflight,
                {"host": ["local"]},
            ),
            (
                ["session-meta", "--host", "local"],
                MODULE.cmd_session_meta,
                {
                    "host": ["local"],
                    "date": [],
                    "from_date": None,
                    "to_date": None,
                    "limit": 200,
                },
            ),
            (
                [
                    "fetch-rollout",
                    "--host",
                    "local",
                    "--rollout",
                    rollout,
                    "--output",
                    "/tmp/rollout.jsonl",
                ],
                MODULE.cmd_fetch_rollout,
                {"host": "local", "rollout": rollout},
            ),
            (
                [
                    "fetch-rollout-chunk",
                    "--host",
                    "local",
                    "--rollout",
                    rollout,
                    "--byte-start",
                    "0",
                    "--byte-end",
                    "8",
                    "--output",
                    "/tmp/rollout.chunk",
                ],
                MODULE.cmd_fetch_rollout_chunk,
                {"byte_start": 0, "byte_end": 8},
            ),
            (
                [
                    "rollout-summary",
                    "--host",
                    "local",
                    "--rollout",
                    rollout,
                ],
                MODULE.cmd_rollout_summary,
                {
                    "keyword": [],
                    "limit": 40,
                    "tail_records": 8,
                    "max_text_chars": 400,
                },
            ),
            (
                [
                    "chunked-rollout-summary",
                    "--host",
                    "local",
                    "--rollout",
                    rollout,
                ],
                MODULE.cmd_chunked_rollout_summary,
                {
                    "keyword": [],
                    "chunk_bytes": MODULE.DEFAULT_ROLLOUT_CHUNK_BYTES,
                    "limit_per_chunk": 40,
                    "tail_records": 8,
                    "max_text_chars": 400,
                },
            ),
        )

        for argv, expected_func, expected_values in cases:
            with self.subTest(command=argv[0]):
                args = parser.parse_args(argv)
                self.assertIs(args.func, expected_func)
                for name, expected in expected_values.items():
                    self.assertEqual(getattr(args, name), expected)

    def test_root_rollout_support_is_session_shards_only(self) -> None:
        rollout = "rollout-2026-07-14T10-00-00-root.jsonl"

        with self.assertRaisesRegex(ValueError, "rollout path must match"):
            MODULE._resolve_rollout_relative_path(rollout)
        self.assertEqual(
            MODULE._resolve_session_shards_rollout_relative_path(rollout).as_posix(),
            rollout,
        )

    def test_session_shards_rejects_summary_rollout_names(self) -> None:
        for rollout in (
            "sessions/2026/07/14/rollout-summary-example.jsonl",
            "archived_sessions/rollout-summary-example.jsonl",
            "rollout-summary-example.jsonl",
        ):
            with self.subTest(rollout=rollout):
                with self.assertRaisesRegex(ValueError, "rollout path must match"):
                    MODULE._resolve_session_shards_rollout_relative_path(rollout)

    def test_docs_define_unique_v2_transport_and_closed_protocol(self) -> None:
        skill = SKILL_PATH.read_text(encoding="utf-8")
        reference = REFERENCE_PATH.read_text(encoding="utf-8")

        self.assertIn(
            "`session-shards` is the only SSH/transport primitive for Session "
            "Retrospective v2",
            skill,
        )
        for phrase in (
            "closed field schema",
            "next descriptor",
            "64 KiB",
            "4 MiB",
            "record_processing_budget_exceeded",
            "session-shards-shadow-holdout-v1",
            "shadow_qualification_controlled_missing_host",
            "source lease ref is a one-time challenge",
            "carry the authenticated `holdout_ref`",
            "holdout cannot satisfy backfill",
            "`bootstrap-daily-holdout-identity`",
            "exact closed holdout argv",
            "supervisor cleanup failure",
            "`start-daily-pair-successor`",
            "direct-successor run",
            "production_source_suppressed: false",
        ):
            self.assertIn(phrase, reference)

        for phrase in (
            "Only an explicitly requested shadow Daily partial/backfill qualification",
            "never as `no_activity`",
            "replace the authenticated `holdout_ref`",
            "exact installed v2 coordinator path",
            "bootstrap-daily-holdout-identity",
            "every present authenticated binding",
            "supervisor cleanup failure",
            "start-daily-pair-successor",
        ):
            self.assertIn(phrase, skill)


if __name__ == "__main__":
    unittest.main()
