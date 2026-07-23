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


def configured_int_max_str_digits() -> int:
    getter = getattr(sys, "get_int_max_str_digits", None)
    return int(getter()) if callable(getter) else 0


def pathological_json_lines() -> list[str]:
    integer_digit_limit = configured_int_max_str_digits()
    oversized_integer_digits = max(5000, integer_digit_limit + 1)
    nesting_depth = 10_000
    return [
        "9" * oversized_integer_digits,
        "[" * nesting_depth + "0" + "]" * nesting_depth,
    ]


def embedded_probe_namespace(payload: dict[str, object]) -> dict[str, object]:
    script = MODULE._remote_python_script(payload)
    definitions = script.split('\nif CONFIG["mode"] ==', 1)[0]
    namespace: dict[str, object] = {
        "__name__": "embedded_remote_codex_probe_descriptor_tests"
    }
    exec(
        compile(definitions, "<embedded-remote-codex-probe>", "exec"),
        namespace,
    )
    return namespace


def embedded_session_meta_records(namespace: dict[str, object]) -> list[dict[str, object]]:
    output = io.StringIO()
    with redirect_stdout(output):
        try:
            namespace["iter_session_meta"]()
        except SystemExit as error:
            if error.code not in (None, 0):
                raise
    return [
        json.loads(line)
        for line in MODULE._extract_framed_lines(
            output.getvalue(),
            begin_marker=MODULE.REMOTE_SESSION_META_BEGIN,
            end_marker=MODULE.REMOTE_SESSION_META_END,
            host="embedded",
            command="session-meta",
        )
    ]


def candidate_mutating_stat(
    real_stat,
    target_name: str,
    mutate,
    *,
    before_first_stat: bool,
):
    matching_calls = 0

    def mutating_stat(path, *args, **kwargs):
        nonlocal matching_calls
        is_candidate_stat = (
            path == target_name
            and kwargs.get("dir_fd") is not None
            and kwargs.get("follow_symlinks") is False
        )
        if not is_candidate_stat:
            return real_stat(path, *args, **kwargs)
        matching_calls += 1
        if matching_calls == 1 and before_first_stat:
            mutate()
        result = real_stat(path, *args, **kwargs)
        if matching_calls == 1 and not before_first_stat:
            mutate()
        return result

    return mutating_stat


def poisoned_entry_scandir(real_scandir, target_name: str):
    class EntryProxy:
        def __init__(self, entry) -> None:
            self._entry = entry
            self.name = entry.name

        def inode(self):
            raise AssertionError("DirEntry.inode must not bind rollout identity")

        def stat(self, *, follow_symlinks: bool = True):
            raise AssertionError("DirEntry.stat must not bind rollout identity")

        def __getattr__(self, name: str):
            return getattr(self._entry, name)

    class ScandirProxy:
        def __init__(self, iterator) -> None:
            self._iterator = iterator

        def __enter__(self):
            entries = self._iterator.__enter__()
            return (
                EntryProxy(entry) if entry.name == target_name else entry
                for entry in entries
            )

        def __exit__(self, *args: object):
            return self._iterator.__exit__(*args)

    def poisoned_scandir(path):
        return ScandirProxy(real_scandir(path))

    return poisoned_scandir


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
        self.assertIn("`--limit` bounds result rows only", skill)
        self.assertIn("independent fixed safety cap of 501", skill)
        self.assertIn("`session_meta_candidate_limit_truncated`", skill)
        self.assertIn("strict UTF-8", skill)
        self.assertIn("non-object JSON", skill)
        self.assertIn("non-object payload schemas", skill)
        self.assertIn("oversized integer literals", skill)
        self.assertIn("excessively nested JSON", skill)
        self.assertIn("do not treat a higher `--limit` as the remedy", skill)
        self.assertIn("name-only discovery", skill)
        self.assertIn("fresh descriptor-relative no-follow stats", skill)
        self.assertIn("cached dirent inode", skill)
        self.assertIn("at most 501 consumed active candidates", skill)
        self.assertIn("Do no rollout-open or prefix-proof I/O", skill)
        self.assertIn("exact inventory identity", skill)
        self.assertIn("after the proof read", skill)
        self.assertIn("retryable coverage gap", skill)
        self.assertIn("portable metadata cannot distinguish", skill)
        self.assertIn("latest observed high-water mark", skill)
        self.assertIn("aligned verified-snapshot identity", skill)
        self.assertIn("immutable verified snapshot", skill)
        self.assertIn("parse the refreshed snapshot once", skill)
        self.assertIn("scan-truncated coverage error", skill)
        self.assertIn("high-water identity outpaces", skill)
        self.assertIn(
            "`archived_sessions/**` candidates bind a full descriptor identity",
            skill,
        )
        self.assertIn("raw expanded Codex-root entry with `lstat`", skill)
        self.assertIn("open that same configured path directly", skill)
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

    def test_skill_documents_lossless_terminal_tail_contract(self) -> None:
        skill = SKILL_PATH.read_text(encoding="utf-8")
        reference = HOSTS_REFERENCE_PATH.read_text(encoding="utf-8")

        self.assertIn("A direct whole-file `fetch-rollout` remains capped at 16 MiB", skill)
        self.assertIn(
            "automatic complete reconstruction through the validated chunk plan "
            "is capped at 128 MiB",
            skill,
        )
        self.assertIn("freezes the opened descriptor's initial EOF as `S0`", skill)
        self.assertIn("absolute-offset 4 MiB `pread` windows", skill)
        self.assertIn("scans at most 128 MiB", skill)
        self.assertIn(
            "fixed-`S0` read protocol with a bounded coordinate witness",
            skill,
        )
        self.assertIn("Append growth and prefix metadata overwrites", skill)
        self.assertIn("`mtime` and `ctime` changes alone are not failures", skill)
        self.assertIn("distinctive raw substring at its original absolute offset", skill)
        self.assertIn("without relocating it or retrying automatically", skill)
        self.assertIn("does not hash the prefix", skill)
        self.assertIn(
            "same-inode rewrite that deliberately places identical anchor bytes",
            skill,
        )
        self.assertIn("non-LF-terminated record at `S0` means `source_in_progress`", skill)
        self.assertIn("means `terminal_not_reached`", skill)
        self.assertIn("exact UTF-8 final message", skill)
        self.assertIn("Lossless Terminal Tail", reference)
        self.assertIn("no redaction, normalization, or added newline", reference)
        self.assertIn("moving the next cursor to the previous window's start", reference)
        self.assertIn("never recalculates the cursor from a later EOF", reference)
        self.assertIn("There is no automatic retry or relocation", reference)
        self.assertIn("There is no digest of the prefix or every scanned range", reference)
        self.assertIn(
            "does not claim resistance to that adversarial mutation",
            reference,
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
    def test_active_growth_between_inventory_and_consumption_is_retryable_gap(
        self,
    ) -> None:
        for scope in ("local", "embedded"):
            for mutation in ("append_grow", "rewrite_grow"):
                with self.subTest(
                    scope=scope,
                    mutation=mutation,
                ), tempfile.TemporaryDirectory() as temp_dir:
                    codex_root = Path(temp_dir) / ".codex"
                    rollout = (
                        codex_root
                        / "sessions/2026/05/26/"
                        "rollout-2026-05-26T10-00-00-inventory-growth.jsonl"
                    )
                    write_session_meta_rollout(
                        rollout,
                        "trusted-session",
                        "/trusted",
                        "trusted follow-up",
                    )
                    original = rollout.read_bytes()
                    original_stat = rollout.stat()
                    mutated = False

                    def grow_before_consumption() -> None:
                        nonlocal mutated
                        if mutated:
                            return
                        mutated = True
                        if mutation == "append_grow":
                            with rollout.open("ab") as handle:
                                handle.write(b"{}\n")
                        else:
                            rewritten = original.replace(
                                b"trusted-session",
                                b"forged--session",
                                1,
                            ) + b"{}\n"
                            with rollout.open("r+b") as handle:
                                handle.write(rewritten)
                                handle.truncate()

                    if scope == "local":
                        real_open = MODULE._open_pinned_rollout_text_from_parent_fd

                        def open_after_inventory(*args, **kwargs):
                            grow_before_consumption()
                            return real_open(*args, **kwargs)

                        patcher = mock.patch.object(
                            MODULE,
                            "_open_pinned_rollout_text_from_parent_fd",
                            side_effect=open_after_inventory,
                        )

                        def run_scan():
                            return MODULE._scan_session_meta_records(
                                codex_root=codex_root,
                                dates=[MODULE.dt.date(2026, 5, 26)],
                                limit=10,
                                host="local",
                            )

                    else:
                        namespace = embedded_probe_namespace(
                            {
                                "mode": "session-meta",
                                "dates": ["2026/05/26"],
                                "limit": 10,
                                "codex_root": str(codex_root),
                                "session_meta_scan_bytes": (
                                    MODULE.MAX_SESSION_META_SCAN_BYTES
                                ),
                            }
                        )
                        real_open = namespace["open_rollout_text"]

                        def open_after_inventory(*args, **kwargs):
                            grow_before_consumption()
                            return real_open(*args, **kwargs)

                        patcher = mock.patch.dict(
                            namespace,
                            {"open_rollout_text": open_after_inventory},
                        )

                        def run_scan():
                            return embedded_session_meta_records(namespace)

                    with patcher:
                        if scope == "local":
                            with self.assertRaises(
                                MODULE.SessionMetaRolloutError
                            ) as raised:
                                run_scan()
                            error = raised.exception.error
                        else:
                            records = run_scan()
                            self.assertEqual(len(records), 1)
                            error = str(records[0]["error"])

                    retry = run_scan()
                    if scope == "local":
                        retry_rows = retry.rows
                    else:
                        retry_rows = retry
                    final_stat = rollout.stat()
                    self.assertTrue(mutated)
                    self.assertEqual(
                        (final_stat.st_dev, final_stat.st_ino),
                        (original_stat.st_dev, original_stat.st_ino),
                    )
                    self.assertGreater(final_stat.st_size, original_stat.st_size)
                    self.assertIn("identity changed after enumeration", error)
                    self.assertEqual(
                        [
                            row["session_id"]
                            for row in retry_rows
                            if "session_id" in row
                        ],
                        [
                            "trusted-session"
                            if mutation == "append_grow"
                            else "forged--session"
                        ],
                    )

    def test_active_replacement_between_inventory_and_consumption_is_rejected(
        self,
    ) -> None:
        rollout_ref = (
            "sessions/2026/05/26/"
            "rollout-2026-05-26T10-00-00-inventory-replacement.jsonl"
        )
        for scope in ("local", "embedded"):
            with self.subTest(scope=scope), tempfile.TemporaryDirectory() as temp_dir:
                codex_root = Path(temp_dir) / ".codex"
                rollout = codex_root / rollout_ref
                write_session_meta_rollout(
                    rollout,
                    "trusted-session",
                    "/trusted",
                    "trusted follow-up",
                )
                replacement = rollout.with_suffix(".replacement")
                write_session_meta_rollout(
                    replacement,
                    "replacement-session",
                    "/replacement",
                    "replacement follow-up",
                )
                replaced = False

                def replace_before_consumption() -> None:
                    nonlocal replaced
                    if replaced:
                        return
                    replaced = True
                    os.replace(replacement, rollout)

                if scope == "local":
                    real_open = MODULE._open_pinned_rollout_text_from_parent_fd

                    def open_after_inventory(*args, **kwargs):
                        replace_before_consumption()
                        return real_open(*args, **kwargs)

                    patcher = mock.patch.object(
                        MODULE,
                        "_open_pinned_rollout_text_from_parent_fd",
                        side_effect=open_after_inventory,
                    )

                    def run_scan():
                        return MODULE._scan_session_meta_records(
                            codex_root=codex_root,
                            dates=[MODULE.dt.date(2026, 5, 26)],
                            limit=10,
                            host="local",
                        ).rows

                else:
                    namespace = embedded_probe_namespace(
                        {
                            "mode": "session-meta",
                            "dates": ["2026/05/26"],
                            "limit": 10,
                            "codex_root": str(codex_root),
                            "session_meta_scan_bytes": (
                                MODULE.MAX_SESSION_META_SCAN_BYTES
                            ),
                        }
                    )
                    real_open = namespace["open_rollout_text"]

                    def open_after_inventory(*args, **kwargs):
                        replace_before_consumption()
                        return real_open(*args, **kwargs)

                    patcher = mock.patch.dict(
                        namespace,
                        {"open_rollout_text": open_after_inventory},
                    )

                    def run_scan():
                        return embedded_session_meta_records(namespace)

                with patcher:
                    if scope == "local":
                        with self.assertRaises(
                            MODULE.SessionMetaRolloutError
                        ) as raised:
                            run_scan()
                        error = raised.exception.error
                        error_rollout = raised.exception.rollout
                    else:
                        rows = run_scan()
                        self.assertEqual(len(rows), 1)
                        error = str(rows[0]["error"])
                        error_rollout = rows[0].get("rollout")

                self.assertTrue(replaced)
                self.assertEqual(error, "rollout identity changed after enumeration")
                self.assertEqual(error_rollout, rollout_ref)

    def test_archive_replacement_between_inventory_and_consumption_is_rejected(
        self,
    ) -> None:
        rollout_ref = (
            "archived_sessions/2026/05/26/"
            "rollout-2026-05-26T10-00-00-inventory-replacement.jsonl"
        )
        for scope in ("local", "embedded"):
            with self.subTest(scope=scope), tempfile.TemporaryDirectory() as temp_dir:
                codex_root = Path(temp_dir) / ".codex"
                rollout = codex_root / rollout_ref
                write_session_meta_rollout(
                    rollout,
                    "trusted-session",
                    "/trusted",
                    "trusted follow-up",
                )
                replacement = rollout.with_suffix(".replacement")
                write_session_meta_rollout(
                    replacement,
                    "replacement-session",
                    "/replacement",
                    "replacement follow-up",
                )
                replaced = False

                def replace_before_consumption() -> None:
                    nonlocal replaced
                    if replaced:
                        return
                    replaced = True
                    os.replace(replacement, rollout)

                if scope == "local":
                    real_open = MODULE._open_pinned_rollout_text_from_parent_fd

                    def open_after_inventory(*args, **kwargs):
                        replace_before_consumption()
                        return real_open(*args, **kwargs)

                    patcher = mock.patch.object(
                        MODULE,
                        "_open_pinned_rollout_text_from_parent_fd",
                        side_effect=open_after_inventory,
                    )

                    def run_scan():
                        return MODULE._scan_session_meta_records(
                            codex_root=codex_root,
                            dates=[MODULE.dt.date(2026, 5, 26)],
                            limit=10,
                            host="local",
                        ).rows

                else:
                    namespace = embedded_probe_namespace(
                        {
                            "mode": "session-meta",
                            "dates": ["2026/05/26"],
                            "limit": 10,
                            "codex_root": str(codex_root),
                            "session_meta_scan_bytes": (
                                MODULE.MAX_SESSION_META_SCAN_BYTES
                            ),
                        }
                    )
                    real_open = namespace["open_rollout_text"]

                    def open_after_inventory(*args, **kwargs):
                        replace_before_consumption()
                        return real_open(*args, **kwargs)

                    patcher = mock.patch.dict(
                        namespace,
                        {"open_rollout_text": open_after_inventory},
                    )

                    def run_scan():
                        return embedded_session_meta_records(namespace)

                with patcher:
                    if scope == "local":
                        with self.assertRaises(
                            MODULE.SessionMetaRolloutError
                        ) as raised:
                            run_scan()
                        error = raised.exception.error
                        error_rollout = raised.exception.rollout
                    else:
                        rows = run_scan()
                        self.assertEqual(len(rows), 1)
                        error = str(rows[0]["error"])
                        error_rollout = rows[0].get("rollout")

                self.assertTrue(replaced)
                self.assertEqual(error, "rollout identity changed after enumeration")
                self.assertEqual(error_rollout, rollout_ref)

    def test_active_refresh_finds_first_session_meta_appended_after_parse(
        self,
    ) -> None:
        for scope in ("local", "embedded"):
            with self.subTest(scope=scope), tempfile.TemporaryDirectory() as temp_dir:
                codex_root = Path(temp_dir) / ".codex"
                rollout = (
                    codex_root
                    / "sessions/2026/05/26/"
                    "rollout-2026-05-26T10-00-00-late-meta.jsonl"
                )
                rollout.parent.mkdir(parents=True)
                rollout.write_bytes(b"{}\n")
                parse_calls = 0
                late_meta = (
                    json.dumps(
                        {
                            "type": "session_meta",
                            "payload": {
                                "id": "late-session",
                                "cwd": "/late",
                            },
                        },
                        separators=(",", ":"),
                    ).encode()
                    + b"\n"
                )

                if scope == "local":
                    real_parser = MODULE._parse_bounded_session_meta_prefix

                    def run_scan():
                        return MODULE._scan_session_meta_records(
                            codex_root=codex_root,
                            dates=[MODULE.dt.date(2026, 5, 26)],
                            limit=10,
                            host="local",
                        ).rows

                else:
                    namespace = embedded_probe_namespace(
                        {
                            "mode": "session-meta",
                            "dates": ["2026/05/26"],
                            "limit": 10,
                            "codex_root": str(codex_root),
                            "session_meta_scan_bytes": (
                                MODULE.MAX_SESSION_META_SCAN_BYTES
                            ),
                        }
                    )
                    real_parser = namespace["parse_bounded_session_meta_prefix"]

                    def run_scan():
                        return embedded_session_meta_records(namespace)

                def append_meta_after_first_parse(*args, **kwargs):
                    nonlocal parse_calls
                    result = real_parser(*args, **kwargs)
                    parse_calls += 1
                    if parse_calls == 1:
                        with rollout.open("ab") as handle:
                            handle.write(late_meta)
                    return result

                if scope == "local":
                    patcher = mock.patch.object(
                        MODULE,
                        "_parse_bounded_session_meta_prefix",
                        side_effect=append_meta_after_first_parse,
                    )
                else:
                    patcher = mock.patch.dict(
                        namespace,
                        {
                            "parse_bounded_session_meta_prefix": (
                                append_meta_after_first_parse
                            )
                        },
                    )

                with patcher:
                    records = run_scan()
                self.assertEqual(parse_calls, 2)
                self.assertEqual(
                    [record["session_id"] for record in records if "session_id" in record],
                    ["late-session"],
                )

    def test_active_repeated_growth_after_refresh_is_coverage_gap(self) -> None:
        for scope in ("local", "embedded"):
            with self.subTest(scope=scope), tempfile.TemporaryDirectory() as temp_dir:
                codex_root = Path(temp_dir) / ".codex"
                rollout_ref = (
                    "sessions/2026/05/26/"
                    "rollout-2026-05-26T10-00-00-repeated-growth.jsonl"
                )
                rollout = codex_root / rollout_ref
                rollout.parent.mkdir(parents=True)
                rollout.write_bytes(b"{}\n")
                parse_calls = 0
                late_meta = (
                    json.dumps(
                        {
                            "type": "session_meta",
                            "payload": {
                                "id": "too-late-session",
                                "cwd": "/late",
                            },
                        },
                        separators=(",", ":"),
                    ).encode()
                    + b"\n"
                )

                if scope == "local":
                    real_parser = MODULE._parse_bounded_session_meta_prefix

                    def run_scan():
                        return MODULE._scan_session_meta_records(
                            codex_root=codex_root,
                            dates=[MODULE.dt.date(2026, 5, 26)],
                            limit=10,
                            host="local",
                        )

                else:
                    namespace = embedded_probe_namespace(
                        {
                            "mode": "session-meta",
                            "dates": ["2026/05/26"],
                            "limit": 10,
                            "codex_root": str(codex_root),
                            "session_meta_scan_bytes": (
                                MODULE.MAX_SESSION_META_SCAN_BYTES
                            ),
                        }
                    )
                    real_parser = namespace["parse_bounded_session_meta_prefix"]

                    def run_scan():
                        return embedded_session_meta_records(namespace)

                def grow_after_each_parse(*args, **kwargs):
                    nonlocal parse_calls
                    result = real_parser(*args, **kwargs)
                    parse_calls += 1
                    with rollout.open("ab") as handle:
                        handle.write(b"{}\n" if parse_calls == 1 else late_meta)
                    return result

                if scope == "local":
                    patcher = mock.patch.object(
                        MODULE,
                        "_parse_bounded_session_meta_prefix",
                        side_effect=grow_after_each_parse,
                    )
                else:
                    patcher = mock.patch.dict(
                        namespace,
                        {
                            "parse_bounded_session_meta_prefix": (
                                grow_after_each_parse
                            )
                        },
                    )

                with patcher:
                    if scope == "local":
                        with self.assertRaises(
                            MODULE.SessionMetaRolloutError
                        ) as raised:
                            run_scan()
                        error = raised.exception.error
                        error_rollout = raised.exception.rollout
                    else:
                        records = run_scan()
                        self.assertEqual(len(records), 1)
                        error = str(records[0]["error"])
                        error_rollout = records[0].get("rollout")
                self.assertEqual(parse_calls, 2)
                self.assertEqual(error, MODULE.SESSION_META_SCAN_TRUNCATED_ERROR)
                self.assertEqual(error_rollout, rollout_ref)

    def test_active_late_checkpoint_growth_is_coverage_gap(self) -> None:
        for scope in ("local", "embedded"):
            with self.subTest(scope=scope), tempfile.TemporaryDirectory() as temp_dir:
                codex_root = Path(temp_dir) / ".codex"
                rollout_ref = (
                    "sessions/2026/05/26/"
                    "rollout-2026-05-26T10-00-00-late-checkpoint.jsonl"
                )
                rollout = codex_root / rollout_ref
                rollout.parent.mkdir(parents=True)
                rollout.write_bytes(b"{}\n")
                read_calls = 0
                late_meta = (
                    json.dumps(
                        {
                            "type": "session_meta",
                            "payload": {
                                "id": "late-window-session",
                                "cwd": "/late",
                            },
                        },
                        separators=(",", ":"),
                    ).encode()
                    + b"\n"
                )

                if scope == "local":
                    real_read = MODULE._read_rollout_prefix_proof

                    def run_scan():
                        return MODULE._scan_session_meta_records(
                            codex_root=codex_root,
                            dates=[MODULE.dt.date(2026, 5, 26)],
                            limit=10,
                            host="local",
                        )

                else:
                    namespace = embedded_probe_namespace(
                        {
                            "mode": "session-meta",
                            "dates": ["2026/05/26"],
                            "limit": 10,
                            "codex_root": str(codex_root),
                            "session_meta_scan_bytes": (
                                MODULE.MAX_SESSION_META_SCAN_BYTES
                            ),
                        }
                    )
                    real_read = namespace["read_rollout_prefix_proof"]

                    def run_scan():
                        return embedded_session_meta_records(namespace)

                def append_after_aligned_checkpoint_read(*args, **kwargs):
                    nonlocal read_calls
                    result = real_read(*args, **kwargs)
                    read_calls += 1
                    if read_calls == 5:
                        with rollout.open("ab") as handle:
                            handle.write(late_meta)
                    return result

                if scope == "local":
                    patcher = mock.patch.object(
                        MODULE,
                        "_read_rollout_prefix_proof",
                        side_effect=append_after_aligned_checkpoint_read,
                    )
                else:
                    patcher = mock.patch.dict(
                        namespace,
                        {
                            "read_rollout_prefix_proof": (
                                append_after_aligned_checkpoint_read
                            )
                        },
                    )

                with patcher:
                    if scope == "local":
                        with self.assertRaises(
                            MODULE.SessionMetaRolloutError
                        ) as raised:
                            run_scan()
                        error = raised.exception.error
                        error_rollout = raised.exception.rollout
                    else:
                        records = run_scan()
                        self.assertEqual(len(records), 1)
                        error = str(records[0]["error"])
                        error_rollout = records[0].get("rollout")
                self.assertEqual(read_calls, 6)
                self.assertEqual(error, MODULE.SESSION_META_SCAN_TRUNCATED_ERROR)
                self.assertEqual(error_rollout, rollout_ref)

    def test_session_meta_uses_scandir_entries_for_names_only(self) -> None:
        for scope in ("local", "embedded"):
            with self.subTest(scope=scope), tempfile.TemporaryDirectory() as temp_dir:
                codex_root = Path(temp_dir) / ".codex"
                rollout = (
                    codex_root
                    / "sessions/2026/05/26/"
                    "rollout-2026-05-26T10-00-00-name-only.jsonl"
                )
                write_session_meta_rollout(
                    rollout,
                    "trusted-session",
                    "/trusted",
                    "trusted follow-up",
                )
                if scope == "local":
                    target_os = MODULE.os

                    def run_scan():
                        return MODULE._scan_session_meta_records(
                            codex_root=codex_root,
                            dates=[MODULE.dt.date(2026, 5, 26)],
                            limit=10,
                            host="local",
                        ).rows

                else:
                    namespace = embedded_probe_namespace(
                        {
                            "mode": "session-meta",
                            "dates": ["2026/05/26"],
                            "limit": 10,
                            "codex_root": str(codex_root),
                            "session_meta_scan_bytes": (
                                MODULE.MAX_SESSION_META_SCAN_BYTES
                            ),
                        }
                    )
                    target_os = namespace["os"]

                    def run_scan():
                        return embedded_session_meta_records(namespace)

                patched_scandir = poisoned_entry_scandir(
                    target_os.scandir,
                    rollout.name,
                )
                with mock.patch.object(
                    target_os,
                    "scandir",
                    side_effect=patched_scandir,
                ):
                    rows = run_scan()

                self.assertEqual(
                    [row["session_id"] for row in rows if "session_id" in row],
                    ["trusted-session"],
                )

    @unittest.skipUnless(
        hasattr(os, "symlink") and hasattr(os, "O_NOFOLLOW"),
        "symlink rejection requires POSIX O_NOFOLLOW",
    )
    def test_candidate_identity_open_maps_symlink_to_precise_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            parent = Path(temp_dir)
            target = parent / "target.jsonl"
            target.write_text("{}\n", encoding="utf-8")
            candidate = parent / "rollout-2026-05-26T10-00-00-link.jsonl"
            candidate.symlink_to(target)
            parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                for scope in ("local", "embedded"):
                    if scope == "local":
                        capture = (
                            MODULE._capture_rollout_candidate_identity_from_parent_fd
                        )
                    else:
                        namespace = embedded_probe_namespace(
                            {
                                "mode": "session-meta",
                                "dates": [],
                                "limit": 10,
                                "codex_root": str(parent),
                                "session_meta_scan_bytes": (
                                    MODULE.MAX_SESSION_META_SCAN_BYTES
                                ),
                            }
                        )
                        capture = namespace[
                            "capture_rollout_candidate_identity_from_parent_fd"
                        ]
                    with self.subTest(scope=scope), self.assertRaisesRegex(
                        ValueError,
                        "rollout path is a symlink",
                    ):
                        capture(parent_fd, candidate.name)
            finally:
                os.close(parent_fd)

    def test_candidate_identity_enumeration_bounds_transient_rollout_fds(
        self,
    ) -> None:
        for scope in ("local", "embedded"):
            with self.subTest(scope=scope), tempfile.TemporaryDirectory() as temp_dir:
                codex_root = Path(temp_dir) / ".codex"
                rollout_names: set[str] = set()
                for index in range(3):
                    rollout = (
                        codex_root
                        / "sessions/2026/05/26/"
                        f"rollout-2026-05-26T10-00-0{index}-fd-bound.jsonl"
                    )
                    write_session_meta_rollout(
                        rollout,
                        f"trusted-{index}",
                        "/trusted",
                        "trusted follow-up",
                    )
                    rollout_names.add(rollout.name)
                rollout_parent = (
                    codex_root / "sessions" / "2026" / "05" / "26"
                )
                if scope == "local":
                    target_os = MODULE.os
                    capture = MODULE._capture_rollout_candidate_identity_from_parent_fd

                else:
                    namespace = embedded_probe_namespace(
                        {
                            "mode": "session-meta",
                            "dates": ["2026/05/26"],
                            "limit": 10,
                            "codex_root": str(codex_root),
                            "session_meta_scan_bytes": (
                                MODULE.MAX_SESSION_META_SCAN_BYTES
                            ),
                        }
                    )
                    target_os = namespace["os"]
                    capture = namespace[
                        "capture_rollout_candidate_identity_from_parent_fd"
                    ]

                real_open = target_os.open
                real_close = target_os.close
                active_rollout_fds: set[int] = set()
                peak_rollout_fds = 0

                def tracking_open(path, *args, **kwargs):
                    nonlocal peak_rollout_fds
                    fd = real_open(path, *args, **kwargs)
                    if path in rollout_names and kwargs.get("dir_fd") is not None:
                        active_rollout_fds.add(fd)
                        peak_rollout_fds = max(
                            peak_rollout_fds,
                            len(active_rollout_fds),
                        )
                    return fd

                def tracking_close(fd: int) -> None:
                    active_rollout_fds.discard(fd)
                    real_close(fd)

                parent_fd = os.open(rollout_parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    with (
                        mock.patch.object(
                            target_os,
                            "open",
                            side_effect=tracking_open,
                        ),
                        mock.patch.object(
                            target_os,
                            "close",
                            side_effect=tracking_close,
                        ),
                    ):
                        for rollout_name in sorted(rollout_names):
                            capture(parent_fd, rollout_name)
                finally:
                    os.close(parent_fd)

                self.assertEqual(peak_rollout_fds, 1)
                self.assertEqual(active_rollout_fds, set())

    def test_active_session_meta_enforces_append_only_policy(self) -> None:
        for scope in ("local", "embedded"):
            for phase in ("post_initial_checkpoint", "post_parse"):
                for mutation in ("append", "truncate", "rewrite", "rewrite_grow"):
                    with self.subTest(
                        scope=scope,
                        phase=phase,
                        mutation=mutation,
                    ), tempfile.TemporaryDirectory() as temp_dir:
                        codex_root = Path(temp_dir) / ".codex"
                        rollout_ref = (
                            "sessions/2026/05/26/"
                            "rollout-2026-05-26T10-00-00-append-policy.jsonl"
                        )
                        rollout = codex_root / rollout_ref
                        write_session_meta_rollout(
                            rollout,
                            "trusted-session",
                            "/trusted",
                            "trusted follow-up",
                        )
                        original_stat = rollout.stat()
                        original = rollout.read_bytes()
                        mutated = False

                        def mutate() -> None:
                            nonlocal mutated
                            if mutated:
                                return
                            mutated = True
                            if mutation == "append":
                                with rollout.open("ab") as handle:
                                    handle.write(b"{}\n")
                            elif mutation == "truncate":
                                with rollout.open("r+b") as handle:
                                    handle.truncate(max(1, original_stat.st_size // 2))
                            elif mutation == "rewrite":
                                with rollout.open("r+b") as handle:
                                    handle.write(b" " + original[1:])
                                    handle.truncate(len(original))
                                os.utime(
                                    rollout,
                                    ns=(
                                        original_stat.st_atime_ns,
                                        original_stat.st_mtime_ns + 1_000_000_000,
                                    ),
                                )
                            else:
                                with rollout.open("r+b") as handle:
                                    handle.write(b" " + original[1:] + b"{}\n")

                        if scope == "local":
                            real_capture = (
                                MODULE._capture_initial_append_only_rollout_checkpoint
                            )
                            real_parser = MODULE._parse_bounded_session_meta_prefix

                            def run_scan():
                                return MODULE._scan_session_meta_records(
                                    codex_root=codex_root,
                                    dates=[MODULE.dt.date(2026, 5, 26)],
                                    limit=10,
                                    host="local",
                                )

                        else:
                            namespace = embedded_probe_namespace(
                                {
                                    "mode": "session-meta",
                                    "dates": ["2026/05/26"],
                                    "limit": 10,
                                    "codex_root": str(codex_root),
                                    "session_meta_scan_bytes": (
                                        MODULE.MAX_SESSION_META_SCAN_BYTES
                                    ),
                                }
                            )
                            real_capture = namespace[
                                "capture_initial_append_only_rollout_checkpoint"
                            ]
                            real_parser = namespace[
                                "parse_bounded_session_meta_prefix"
                            ]

                            def run_scan():
                                return embedded_session_meta_records(namespace)

                        if phase == "post_initial_checkpoint":
                            def capture_then_mutate(*args, **kwargs):
                                proof = real_capture(*args, **kwargs)
                                mutate()
                                return proof

                            if scope == "local":
                                patcher = mock.patch.object(
                                    MODULE,
                                    "_capture_initial_append_only_rollout_checkpoint",
                                    side_effect=capture_then_mutate,
                                )
                            else:
                                patcher = mock.patch.dict(
                                    namespace,
                                    {
                                        "capture_initial_append_only_rollout_checkpoint": (
                                            capture_then_mutate
                                        )
                                    },
                                )
                        else:
                            def parse_then_mutate(*args, **kwargs):
                                result = real_parser(*args, **kwargs)
                                mutate()
                                return result

                            if scope == "local":
                                patcher = mock.patch.object(
                                    MODULE,
                                    "_parse_bounded_session_meta_prefix",
                                    side_effect=parse_then_mutate,
                                )
                            else:
                                patcher = mock.patch.dict(
                                    namespace,
                                    {
                                        "parse_bounded_session_meta_prefix": (
                                            parse_then_mutate
                                        )
                                    },
                                )

                        with patcher:
                            if scope == "local":
                                if mutation == "append":
                                    scan = run_scan()
                                    session_ids = [
                                        row["session_id"] for row in scan.rows
                                    ]
                                else:
                                    with self.assertRaises(
                                        MODULE.SessionMetaRolloutError
                                    ) as raised:
                                        run_scan()
                                    error = raised.exception.error
                            else:
                                records = run_scan()
                                if mutation == "append":
                                    session_ids = [
                                        str(record["session_id"])
                                        for record in records
                                        if "session_id" in record
                                    ]
                                else:
                                    self.assertEqual(len(records), 1)
                                    error = str(records[0]["error"])

                        final_stat = rollout.stat()
                        self.assertTrue(mutated)
                        self.assertEqual(
                            (final_stat.st_dev, final_stat.st_ino),
                            (original_stat.st_dev, original_stat.st_ino),
                        )
                        if mutation == "append":
                            self.assertEqual(session_ids, ["trusted-session"])
                        else:
                            self.assertIn("identity changed", error)

    def test_active_append_after_verified_checkpoint_uses_aligned_snapshot(
        self,
    ) -> None:
        for scope in ("local", "embedded"):
            with self.subTest(scope=scope), tempfile.TemporaryDirectory() as temp_dir:
                codex_root = Path(temp_dir) / ".codex"
                rollout_ref = (
                    "sessions/2026/05/26/"
                    "rollout-2026-05-26T10-00-00-late-append.jsonl"
                )
                rollout = codex_root / rollout_ref
                write_session_meta_rollout(
                    rollout,
                    "trusted-session",
                    "/trusted",
                    "trusted follow-up",
                )
                original_size = rollout.stat().st_size
                after_scan_attempts = 0
                after_scan_successes = 0
                mutated = False

                if scope == "local":
                    real_read = MODULE._read_rollout_prefix_proof

                    def run_scan():
                        return MODULE._scan_session_meta_records(
                            codex_root=codex_root,
                            dates=[MODULE.dt.date(2026, 5, 26)],
                            limit=10,
                            host="local",
                        )

                else:
                    namespace = embedded_probe_namespace(
                        {
                            "mode": "session-meta",
                            "dates": ["2026/05/26"],
                            "limit": 10,
                            "codex_root": str(codex_root),
                            "session_meta_scan_bytes": (
                                MODULE.MAX_SESSION_META_SCAN_BYTES
                            ),
                        }
                    )
                    real_read = namespace["read_rollout_prefix_proof"]

                    def run_scan():
                        return embedded_session_meta_records(namespace)

                def append_after_verified_read(*args, **kwargs):
                    nonlocal after_scan_attempts, after_scan_successes, mutated
                    after_scan = kwargs.get("phase") == "after session-meta scan"
                    if after_scan:
                        after_scan_attempts += 1
                    result = real_read(*args, **kwargs)
                    if after_scan:
                        after_scan_successes += 1
                    if after_scan and after_scan_successes == 2:
                        with rollout.open("ab") as handle:
                            handle.write(b"{}\n")
                        mutated = True
                    return result

                if scope == "local":
                    patcher = mock.patch.object(
                        MODULE,
                        "_read_rollout_prefix_proof",
                        side_effect=append_after_verified_read,
                    )
                else:
                    patcher = mock.patch.dict(
                        namespace,
                        {"read_rollout_prefix_proof": append_after_verified_read},
                    )

                with patcher:
                    result = run_scan()
                if scope == "local":
                    session_ids = [row["session_id"] for row in result.rows]
                else:
                    session_ids = [
                        str(record["session_id"])
                        for record in result
                        if "session_id" in record
                    ]
                self.assertTrue(mutated)
                self.assertEqual(after_scan_attempts, 3)
                self.assertEqual(after_scan_successes, 3)
                self.assertGreater(rollout.stat().st_size, original_size)
                self.assertEqual(session_ids, ["trusted-session"])

    def test_active_rewrite_grow_after_post_scan_final_proof_fails_closed(
        self,
    ) -> None:
        for scope in ("local", "embedded"):
            with self.subTest(scope=scope), tempfile.TemporaryDirectory() as temp_dir:
                codex_root = Path(temp_dir) / ".codex"
                rollout_ref = (
                    "sessions/2026/05/26/"
                    "rollout-2026-05-26T10-00-00-final-proof-rewrite.jsonl"
                )
                rollout = codex_root / rollout_ref
                write_session_meta_rollout(
                    rollout,
                    "trusted-session",
                    "/trusted",
                    "trusted follow-up",
                )
                original = rollout.read_bytes()
                original_stat = rollout.stat()
                rewritten = original.replace(
                    b"trusted-session",
                    b"forged--session",
                    1,
                ) + b"{}\n"
                after_scan_attempts = 0
                after_scan_successes = 0
                mutated = False

                if scope == "local":
                    real_read = MODULE._read_rollout_prefix_proof

                    def run_scan():
                        return MODULE._scan_session_meta_records(
                            codex_root=codex_root,
                            dates=[MODULE.dt.date(2026, 5, 26)],
                            limit=10,
                            host="local",
                        )

                else:
                    namespace = embedded_probe_namespace(
                        {
                            "mode": "session-meta",
                            "dates": ["2026/05/26"],
                            "limit": 10,
                            "codex_root": str(codex_root),
                            "session_meta_scan_bytes": (
                                MODULE.MAX_SESSION_META_SCAN_BYTES
                            ),
                        }
                    )
                    real_read = namespace["read_rollout_prefix_proof"]

                    def run_scan():
                        return embedded_session_meta_records(namespace)

                def rewrite_after_final_proof(*args, **kwargs):
                    nonlocal after_scan_attempts, after_scan_successes, mutated
                    after_scan = kwargs.get("phase") == "after session-meta scan"
                    if after_scan:
                        after_scan_attempts += 1
                    result = real_read(*args, **kwargs)
                    if after_scan:
                        after_scan_successes += 1
                    if after_scan and after_scan_successes == 2 and not mutated:
                        with rollout.open("r+b") as handle:
                            handle.write(rewritten)
                            handle.truncate()
                        mutated = True
                    return result

                if scope == "local":
                    patcher = mock.patch.object(
                        MODULE,
                        "_read_rollout_prefix_proof",
                        side_effect=rewrite_after_final_proof,
                    )
                else:
                    patcher = mock.patch.dict(
                        namespace,
                        {"read_rollout_prefix_proof": rewrite_after_final_proof},
                    )

                with patcher:
                    if scope == "local":
                        with self.assertRaises(
                            MODULE.SessionMetaRolloutError
                        ) as raised:
                            run_scan()
                        error = raised.exception.error
                    else:
                        records = run_scan()
                        self.assertEqual(len(records), 1)
                        error = str(records[0]["error"])
                    first_attempts = after_scan_attempts
                    first_successes = after_scan_successes
                    after_scan_attempts = 0
                    after_scan_successes = 0
                    retry = run_scan()

                retry_rows = retry.rows if scope == "local" else retry
                final_stat = rollout.stat()
                self.assertTrue(mutated)
                self.assertEqual(first_attempts, 3)
                self.assertEqual(first_successes, 2)
                self.assertEqual(after_scan_attempts, 2)
                self.assertEqual(after_scan_successes, 2)
                self.assertEqual(
                    (final_stat.st_dev, final_stat.st_ino),
                    (original_stat.st_dev, original_stat.st_ino),
                )
                self.assertGreater(final_stat.st_size, original_stat.st_size)
                self.assertIn("identity changed after session-meta scan", error)
                self.assertEqual(
                    [row["session_id"] for row in retry_rows if "session_id" in row],
                    ["forged--session"],
                )

    def test_active_growth_after_extra_post_scan_proof_fails_exact_check(
        self,
    ) -> None:
        for scope in ("local", "embedded"):
            with self.subTest(scope=scope), tempfile.TemporaryDirectory() as temp_dir:
                codex_root = Path(temp_dir) / ".codex"
                rollout = (
                    codex_root
                    / "sessions/2026/05/26/"
                    "rollout-2026-05-26T10-00-00-extra-proof-growth.jsonl"
                )
                write_session_meta_rollout(
                    rollout,
                    "trusted-session",
                    "/trusted",
                    "trusted follow-up",
                )
                original_stat = rollout.stat()
                after_scan_attempts = 0
                after_scan_successes = 0
                growth_events = 0

                if scope == "local":
                    real_read = MODULE._read_rollout_prefix_proof

                    def run_scan():
                        return MODULE._scan_session_meta_records(
                            codex_root=codex_root,
                            dates=[MODULE.dt.date(2026, 5, 26)],
                            limit=10,
                            host="local",
                        )

                else:
                    namespace = embedded_probe_namespace(
                        {
                            "mode": "session-meta",
                            "dates": ["2026/05/26"],
                            "limit": 10,
                            "codex_root": str(codex_root),
                            "session_meta_scan_bytes": (
                                MODULE.MAX_SESSION_META_SCAN_BYTES
                            ),
                        }
                    )
                    real_read = namespace["read_rollout_prefix_proof"]

                    def run_scan():
                        return embedded_session_meta_records(namespace)

                def grow_after_post_scan_proofs(*args, **kwargs):
                    nonlocal after_scan_attempts, after_scan_successes, growth_events
                    after_scan = kwargs.get("phase") == "after session-meta scan"
                    if after_scan:
                        after_scan_attempts += 1
                    result = real_read(*args, **kwargs)
                    if after_scan:
                        after_scan_successes += 1
                    if (
                        after_scan
                        and after_scan_successes in (2, 3)
                        and growth_events < 2
                    ):
                        with rollout.open("ab") as handle:
                            handle.write(b"{}\n")
                        growth_events += 1
                    return result

                if scope == "local":
                    patcher = mock.patch.object(
                        MODULE,
                        "_read_rollout_prefix_proof",
                        side_effect=grow_after_post_scan_proofs,
                    )
                else:
                    patcher = mock.patch.dict(
                        namespace,
                        {"read_rollout_prefix_proof": grow_after_post_scan_proofs},
                    )

                with patcher:
                    if scope == "local":
                        with self.assertRaises(
                            MODULE.SessionMetaRolloutError
                        ) as raised:
                            run_scan()
                        error = raised.exception.error
                    else:
                        records = run_scan()
                        self.assertEqual(len(records), 1)
                        error = str(records[0]["error"])
                    first_attempts = after_scan_attempts
                    first_successes = after_scan_successes
                    after_scan_attempts = 0
                    after_scan_successes = 0
                    retry = run_scan()

                retry_rows = retry.rows if scope == "local" else retry
                final_stat = rollout.stat()
                self.assertEqual(growth_events, 2)
                self.assertEqual(first_attempts, 3)
                self.assertEqual(first_successes, 3)
                self.assertEqual(after_scan_attempts, 2)
                self.assertEqual(after_scan_successes, 2)
                self.assertEqual(
                    (final_stat.st_dev, final_stat.st_ino),
                    (original_stat.st_dev, original_stat.st_ino),
                )
                self.assertEqual(final_stat.st_size, original_stat.st_size + 6)
                self.assertIn("identity changed after session-meta scan", error)
                self.assertEqual(
                    [row["session_id"] for row in retry_rows if "session_id" in row],
                    ["trusted-session"],
                )

    def test_active_growth_between_post_reproof_descriptor_and_path_fails(
        self,
    ) -> None:
        for scope in ("local", "embedded"):
            with self.subTest(scope=scope), tempfile.TemporaryDirectory() as temp_dir:
                codex_root = Path(temp_dir) / ".codex"
                rollout = (
                    codex_root
                    / "sessions/2026/05/26/"
                    "rollout-2026-05-26T10-00-00-reproof-path-gap.jsonl"
                )
                write_session_meta_rollout(
                    rollout,
                    "trusted-session",
                    "/trusted",
                    "trusted follow-up",
                )
                original_stat = rollout.stat()
                after_scan_attempts = 0
                after_scan_successes = 0
                safe_append_done = False
                path_gap_growth_done = False
                armed_reproof_fd: int | None = None

                if scope == "local":
                    target_os = MODULE.os
                    real_read = MODULE._read_rollout_prefix_proof

                    def run_scan():
                        return MODULE._scan_session_meta_records(
                            codex_root=codex_root,
                            dates=[MODULE.dt.date(2026, 5, 26)],
                            limit=10,
                            host="local",
                        )

                else:
                    namespace = embedded_probe_namespace(
                        {
                            "mode": "session-meta",
                            "dates": ["2026/05/26"],
                            "limit": 10,
                            "codex_root": str(codex_root),
                            "session_meta_scan_bytes": (
                                MODULE.MAX_SESSION_META_SCAN_BYTES
                            ),
                        }
                    )
                    target_os = namespace["os"]
                    real_read = namespace["read_rollout_prefix_proof"]

                    def run_scan():
                        return embedded_session_meta_records(namespace)

                real_fstat = target_os.fstat

                def append_and_arm_after_post_scan_proofs(*args, **kwargs):
                    nonlocal after_scan_attempts, after_scan_successes
                    nonlocal safe_append_done, armed_reproof_fd
                    after_scan = kwargs.get("phase") == "after session-meta scan"
                    if after_scan:
                        after_scan_attempts += 1
                    result = real_read(*args, **kwargs)
                    if after_scan:
                        after_scan_successes += 1
                    if (
                        after_scan
                        and after_scan_successes == 2
                        and not safe_append_done
                    ):
                        with rollout.open("ab") as handle:
                            handle.write(b"{}\n")
                        safe_append_done = True
                    elif (
                        after_scan
                        and after_scan_successes == 3
                        and safe_append_done
                        and not path_gap_growth_done
                    ):
                        armed_reproof_fd = int(args[0])
                    return result

                def grow_after_reproof_fstat(fd: int):
                    nonlocal armed_reproof_fd, path_gap_growth_done
                    result = real_fstat(fd)
                    if armed_reproof_fd == fd and not path_gap_growth_done:
                        with rollout.open("ab") as handle:
                            handle.write(b"{}\n")
                        path_gap_growth_done = True
                        armed_reproof_fd = None
                    return result

                if scope == "local":
                    proof_patcher = mock.patch.object(
                        MODULE,
                        "_read_rollout_prefix_proof",
                        side_effect=append_and_arm_after_post_scan_proofs,
                    )
                else:
                    proof_patcher = mock.patch.dict(
                        namespace,
                        {
                            "read_rollout_prefix_proof": (
                                append_and_arm_after_post_scan_proofs
                            )
                        },
                    )
                fstat_patcher = mock.patch.object(
                    target_os,
                    "fstat",
                    side_effect=grow_after_reproof_fstat,
                )

                with proof_patcher, fstat_patcher:
                    if scope == "local":
                        with self.assertRaises(
                            MODULE.SessionMetaRolloutError
                        ) as raised:
                            run_scan()
                        error = raised.exception.error
                    else:
                        records = run_scan()
                        self.assertEqual(len(records), 1)
                        error = str(records[0]["error"])
                    first_attempts = after_scan_attempts
                    first_successes = after_scan_successes
                    after_scan_attempts = 0
                    after_scan_successes = 0
                    retry = run_scan()

                retry_rows = retry.rows if scope == "local" else retry
                final_stat = rollout.stat()
                self.assertTrue(safe_append_done)
                self.assertTrue(path_gap_growth_done)
                self.assertIsNone(armed_reproof_fd)
                self.assertEqual(first_attempts, 3)
                self.assertEqual(first_successes, 3)
                self.assertEqual(after_scan_attempts, 2)
                self.assertEqual(after_scan_successes, 2)
                self.assertEqual(
                    (final_stat.st_dev, final_stat.st_ino),
                    (original_stat.st_dev, original_stat.st_ino),
                )
                self.assertEqual(final_stat.st_size, original_stat.st_size + 6)
                self.assertIn("identity changed after session-meta scan", error)
                self.assertEqual(
                    [row["session_id"] for row in retry_rows if "session_id" in row],
                    ["trusted-session"],
                )

    def test_late_append_high_water_rejects_intermediate_rollback(self) -> None:
        for scope in ("local", "embedded"):
            with self.subTest(scope=scope), tempfile.TemporaryDirectory() as temp_dir:
                codex_root = Path(temp_dir) / ".codex"
                rollout_ref = (
                    "sessions/2026/05/26/"
                    "rollout-2026-05-26T10-00-00-high-water.jsonl"
                )
                rollout = codex_root / rollout_ref
                write_session_meta_rollout(
                    rollout,
                    "trusted-session",
                    "/trusted",
                    "trusted follow-up",
                )
                original_size = rollout.stat().st_size
                read_calls = 0
                appended = False
                rolled_back = False

                if scope == "local":
                    real_read = MODULE._read_rollout_prefix_proof
                    real_parser = MODULE._parse_bounded_session_meta_prefix

                    def run_scan():
                        return MODULE._scan_session_meta_records(
                            codex_root=codex_root,
                            dates=[MODULE.dt.date(2026, 5, 26)],
                            limit=10,
                            host="local",
                        )

                else:
                    namespace = embedded_probe_namespace(
                        {
                            "mode": "session-meta",
                            "dates": ["2026/05/26"],
                            "limit": 10,
                            "codex_root": str(codex_root),
                            "session_meta_scan_bytes": (
                                MODULE.MAX_SESSION_META_SCAN_BYTES
                            ),
                        }
                    )
                    real_read = namespace["read_rollout_prefix_proof"]
                    real_parser = namespace["parse_bounded_session_meta_prefix"]

                    def run_scan():
                        return embedded_session_meta_records(namespace)

                def append_after_verified_read(*args, **kwargs):
                    nonlocal read_calls, appended
                    result = real_read(*args, **kwargs)
                    read_calls += 1
                    if read_calls == 3:
                        with rollout.open("ab") as handle:
                            handle.write(b"{}\n" * 20)
                        appended = True
                    return result

                def rollback_after_parse(*args, **kwargs):
                    nonlocal rolled_back
                    result = real_parser(*args, **kwargs)
                    with rollout.open("r+b") as handle:
                        handle.truncate(original_size + 1)
                    rolled_back = True
                    return result

                if scope == "local":
                    read_patcher = mock.patch.object(
                        MODULE,
                        "_read_rollout_prefix_proof",
                        side_effect=append_after_verified_read,
                    )
                    parser_patcher = mock.patch.object(
                        MODULE,
                        "_parse_bounded_session_meta_prefix",
                        side_effect=rollback_after_parse,
                    )
                else:
                    read_patcher = mock.patch.dict(
                        namespace,
                        {"read_rollout_prefix_proof": append_after_verified_read},
                    )
                    parser_patcher = mock.patch.dict(
                        namespace,
                        {"parse_bounded_session_meta_prefix": rollback_after_parse},
                    )

                with read_patcher, parser_patcher:
                    if scope == "local":
                        with self.assertRaises(
                            MODULE.SessionMetaRolloutError
                        ) as raised:
                            run_scan()
                        error = raised.exception.error
                    else:
                        records = run_scan()
                        self.assertEqual(len(records), 1)
                        error = str(records[0]["error"])

                self.assertTrue(appended)
                self.assertTrue(rolled_back)
                self.assertEqual(read_calls, 4)
                self.assertEqual(rollout.stat().st_size, original_size + 1)
                self.assertIn(
                    "rollout identity changed after session-meta scan",
                    error,
                )

    def test_active_initial_checkpoint_rejects_preproof_mutation(self) -> None:
        for scope in ("local", "embedded"):
            for mutation in (
                "append_grow",
                "rewrite_grow",
                "truncate",
                "same_size_rewrite",
            ):
                with self.subTest(
                    scope=scope,
                    mutation=mutation,
                ), tempfile.TemporaryDirectory() as temp_dir:
                    codex_root = Path(temp_dir) / ".codex"
                    rollout_ref = (
                        "sessions/2026/05/26/"
                        "rollout-2026-05-26T10-00-00-preproof-mutation.jsonl"
                    )
                    rollout = codex_root / rollout_ref
                    write_session_meta_rollout(
                        rollout,
                        "trusted-session",
                        "/trusted",
                        "trusted follow-up",
                    )
                    original = rollout.read_bytes()
                    original_stat = rollout.stat()
                    mutated = False

                    def mutate_before_initial_checkpoint() -> None:
                        nonlocal mutated
                        if mutated:
                            return
                        mutated = True
                        if mutation == "truncate":
                            with rollout.open("r+b") as handle:
                                handle.truncate(max(1, len(original) // 2))
                        elif mutation == "same_size_rewrite":
                            with rollout.open("r+b") as handle:
                                handle.write(b" " + original[1:])
                            os.utime(
                                rollout,
                                ns=(
                                    original_stat.st_atime_ns,
                                    original_stat.st_mtime_ns + 1_000_000_000,
                                ),
                            )
                        elif mutation == "append_grow":
                            with rollout.open("ab") as handle:
                                handle.write(b"{}\n")
                        else:
                            rewritten = original.replace(
                                b"trusted-session",
                                b"forged--session",
                                1,
                            ) + b"{}\n"
                            with rollout.open("r+b") as handle:
                                handle.write(rewritten)
                                handle.truncate()

                    if scope == "local":
                        real_capture = (
                            MODULE._capture_initial_append_only_rollout_checkpoint
                        )

                        def capture_after_mutation(*args, **kwargs):
                            mutate_before_initial_checkpoint()
                            return real_capture(*args, **kwargs)

                        patcher = mock.patch.object(
                            MODULE,
                            "_capture_initial_append_only_rollout_checkpoint",
                            side_effect=capture_after_mutation,
                        )

                        def run_scan():
                            return MODULE._scan_session_meta_records(
                                codex_root=codex_root,
                                dates=[MODULE.dt.date(2026, 5, 26)],
                                limit=10,
                                host="local",
                            )

                    else:
                        namespace = embedded_probe_namespace(
                            {
                                "mode": "session-meta",
                                "dates": ["2026/05/26"],
                                "limit": 10,
                                "codex_root": str(codex_root),
                                "session_meta_scan_bytes": (
                                    MODULE.MAX_SESSION_META_SCAN_BYTES
                                ),
                            }
                        )
                        real_capture = namespace[
                            "capture_initial_append_only_rollout_checkpoint"
                        ]

                        def capture_after_mutation(*args, **kwargs):
                            mutate_before_initial_checkpoint()
                            return real_capture(*args, **kwargs)

                        patcher = mock.patch.dict(
                            namespace,
                            {
                                "capture_initial_append_only_rollout_checkpoint": (
                                    capture_after_mutation
                                )
                            },
                        )

                        def run_scan():
                            return embedded_session_meta_records(namespace)

                    with patcher:
                        if scope == "local":
                            with self.assertRaises(
                                MODULE.SessionMetaRolloutError
                            ) as raised:
                                run_scan()
                            error = raised.exception.error
                            error_rollout = raised.exception.rollout
                        else:
                            records = run_scan()
                            self.assertEqual(len(records), 1)
                            error = str(records[0]["error"])
                            error_rollout = records[0].get("rollout")

                    final_stat = rollout.stat()
                    self.assertTrue(mutated)
                    self.assertEqual(
                        (final_stat.st_dev, final_stat.st_ino),
                        (original_stat.st_dev, original_stat.st_ino),
                    )
                    if mutation == "truncate":
                        self.assertLess(final_stat.st_size, original_stat.st_size)
                    elif mutation == "same_size_rewrite":
                        self.assertEqual(final_stat.st_size, original_stat.st_size)
                    else:
                        self.assertGreater(final_stat.st_size, original_stat.st_size)
                    self.assertEqual(error, "rollout identity changed during open")
                    self.assertEqual(error_rollout, rollout_ref)
                    if mutation in ("append_grow", "rewrite_grow"):
                        retry = run_scan()
                        retry_rows = retry.rows if scope == "local" else retry
                        self.assertEqual(
                            [
                                row["session_id"]
                                for row in retry_rows
                                if "session_id" in row
                            ],
                            [
                                "trusted-session"
                                if mutation == "append_grow"
                                else "forged--session"
                            ],
                        )

    def test_active_prefix_proof_capture_rejects_pre_anchor_growth(self) -> None:
        for scope in ("local", "embedded"):
            for mutation in ("append_grow", "rewrite_grow"):
                with self.subTest(
                    scope=scope,
                    mutation=mutation,
                ), tempfile.TemporaryDirectory() as temp_dir:
                    codex_root = Path(temp_dir) / ".codex"
                    rollout_ref = (
                        "sessions/2026/05/26/"
                        "rollout-2026-05-26T10-00-00-proof-growth.jsonl"
                    )
                    rollout = codex_root / rollout_ref
                    write_session_meta_rollout(
                        rollout,
                        "trusted-session",
                        "/trusted",
                        "trusted follow-up",
                    )
                    original = rollout.read_bytes()
                    original_stat = rollout.stat()
                    mutated = False

                    if scope == "local":
                        target_os = MODULE.os

                        def run_scan():
                            return MODULE._scan_session_meta_records(
                                codex_root=codex_root,
                                dates=[MODULE.dt.date(2026, 5, 26)],
                                limit=10,
                                host="local",
                            )

                    else:
                        namespace = embedded_probe_namespace(
                            {
                                "mode": "session-meta",
                                "dates": ["2026/05/26"],
                                "limit": 10,
                                "codex_root": str(codex_root),
                                "session_meta_scan_bytes": (
                                    MODULE.MAX_SESSION_META_SCAN_BYTES
                                ),
                            }
                        )
                        target_os = namespace["os"]

                        def run_scan():
                            return embedded_session_meta_records(namespace)

                    real_pread = target_os.pread

                    def grow_during_pread(
                        fd: int,
                        length: int,
                        offset: int,
                    ) -> bytes:
                        nonlocal mutated
                        data = real_pread(fd, length, offset)
                        if not mutated:
                            mutated = True
                            if mutation == "append_grow":
                                with rollout.open("ab") as handle:
                                    handle.write(b"{}\n")
                            else:
                                rewritten = original.replace(
                                    b"trusted-session",
                                    b"forged--session",
                                    1,
                                ) + b"{}\n"
                                with rollout.open("r+b") as handle:
                                    handle.write(rewritten)
                                    handle.truncate()
                        return data

                    with mock.patch.object(
                        target_os,
                        "pread",
                        side_effect=grow_during_pread,
                    ):
                        if scope == "local":
                            with self.assertRaises(
                                MODULE.SessionMetaRolloutError
                            ) as raised:
                                run_scan()
                            error = raised.exception.error
                        else:
                            records = run_scan()
                            self.assertEqual(len(records), 1)
                            error = str(records[0]["error"])

                    retry = run_scan()
                    retry_rows = retry.rows if scope == "local" else retry
                    final_stat = rollout.stat()
                    self.assertTrue(mutated)
                    self.assertEqual(
                        (final_stat.st_dev, final_stat.st_ino),
                        (original_stat.st_dev, original_stat.st_ino),
                    )
                    self.assertGreater(final_stat.st_size, original_stat.st_size)
                    self.assertIn("rollout identity changed during open", error)
                    self.assertEqual(
                        [
                            row["session_id"]
                            for row in retry_rows
                            if "session_id" in row
                        ],
                        [
                            "trusted-session"
                            if mutation == "append_grow"
                            else "forged--session"
                        ],
                    )

    def test_active_capture_rejects_growth_between_fstat_and_path_stat(
        self,
    ) -> None:
        for scope in ("local", "embedded"):
            with self.subTest(scope=scope), tempfile.TemporaryDirectory() as temp_dir:
                codex_root = Path(temp_dir) / ".codex"
                rollout = (
                    codex_root
                    / "sessions/2026/05/26/"
                    "rollout-2026-05-26T10-00-00-fstat-growth.jsonl"
                )
                write_session_meta_rollout(
                    rollout,
                    "trusted-session",
                    "/trusted",
                    "trusted follow-up",
                )
                mutated = False

                if scope == "local":
                    target_os = MODULE.os

                    def run_scan():
                        return MODULE._scan_session_meta_records(
                            codex_root=codex_root,
                            dates=[MODULE.dt.date(2026, 5, 26)],
                            limit=10,
                            host="local",
                        )

                else:
                    namespace = embedded_probe_namespace(
                        {
                            "mode": "session-meta",
                            "dates": ["2026/05/26"],
                            "limit": 10,
                            "codex_root": str(codex_root),
                            "session_meta_scan_bytes": (
                                MODULE.MAX_SESSION_META_SCAN_BYTES
                            ),
                        }
                    )
                    target_os = namespace["os"]

                    def run_scan():
                        return embedded_session_meta_records(namespace)

                real_fstat = target_os.fstat

                def grow_after_rollout_fstat(fd: int):
                    nonlocal mutated
                    result = real_fstat(fd)
                    if not mutated and MODULE.stat.S_ISREG(result.st_mode):
                        mutated = True
                        with rollout.open("ab") as handle:
                            handle.write(b"{}\n")
                    return result

                with mock.patch.object(
                    target_os,
                    "fstat",
                    side_effect=grow_after_rollout_fstat,
                ):
                    if scope == "local":
                        with self.assertRaises(
                            MODULE.SessionMetaRolloutError
                        ) as raised:
                            run_scan()
                        error = raised.exception.error
                    else:
                        records = run_scan()
                        self.assertEqual(len(records), 1)
                        error = str(records[0]["error"])
                self.assertTrue(mutated)
                self.assertIn("rollout identity changed during open", error)

    def test_active_post_proof_exact_recheck_rejects_growth(self) -> None:
        for scope in ("local", "embedded"):
            for mutation in ("append_grow", "rewrite_grow"):
                with self.subTest(
                    scope=scope,
                    mutation=mutation,
                ), tempfile.TemporaryDirectory() as temp_dir:
                    codex_root = Path(temp_dir) / ".codex"
                    rollout = (
                        codex_root
                        / "sessions/2026/05/26/"
                        "rollout-2026-05-26T10-00-00-post-proof-growth.jsonl"
                    )
                    write_session_meta_rollout(
                        rollout,
                        "trusted-session",
                        "/trusted",
                        "trusted follow-up",
                    )
                    original = rollout.read_bytes()
                    mutated = False

                    def mutate_after_proof() -> None:
                        nonlocal mutated
                        if mutated:
                            return
                        mutated = True
                        if mutation == "append_grow":
                            with rollout.open("ab") as handle:
                                handle.write(b"{}\n")
                        else:
                            rewritten = original.replace(
                                b"trusted-session",
                                b"forged--session",
                                1,
                            ) + b"{}\n"
                            with rollout.open("r+b") as handle:
                                handle.write(rewritten)
                                handle.truncate()

                    if scope == "local":
                        real_read = MODULE._read_rollout_prefix_proof

                        def run_scan():
                            return MODULE._scan_session_meta_records(
                                codex_root=codex_root,
                                dates=[MODULE.dt.date(2026, 5, 26)],
                                limit=10,
                                host="local",
                            )

                    else:
                        namespace = embedded_probe_namespace(
                            {
                                "mode": "session-meta",
                                "dates": ["2026/05/26"],
                                "limit": 10,
                                "codex_root": str(codex_root),
                                "session_meta_scan_bytes": (
                                    MODULE.MAX_SESSION_META_SCAN_BYTES
                                ),
                            }
                        )
                        real_read = namespace["read_rollout_prefix_proof"]

                        def run_scan():
                            return embedded_session_meta_records(namespace)

                    def read_then_mutate(*args, **kwargs):
                        result = real_read(*args, **kwargs)
                        mutate_after_proof()
                        return result

                    if scope == "local":
                        patcher = mock.patch.object(
                            MODULE,
                            "_read_rollout_prefix_proof",
                            side_effect=read_then_mutate,
                        )
                    else:
                        patcher = mock.patch.dict(
                            namespace,
                            {"read_rollout_prefix_proof": read_then_mutate},
                        )

                    with patcher:
                        if scope == "local":
                            with self.assertRaises(
                                MODULE.SessionMetaRolloutError
                            ) as raised:
                                run_scan()
                            error = raised.exception.error
                        else:
                            records = run_scan()
                            self.assertEqual(len(records), 1)
                            error = str(records[0]["error"])

                    retry = run_scan()
                    retry_rows = retry.rows if scope == "local" else retry
                    self.assertTrue(mutated)
                    self.assertIn("rollout identity changed during open", error)
                    self.assertEqual(
                        [
                            row["session_id"]
                            for row in retry_rows
                            if "session_id" in row
                        ],
                        [
                            "trusted-session"
                            if mutation == "append_grow"
                            else "forged--session"
                        ],
                    )

    def test_active_session_meta_parses_only_verified_snapshot(self) -> None:
        for scope in ("local", "embedded"):
            with self.subTest(scope=scope), tempfile.TemporaryDirectory() as temp_dir:
                codex_root = Path(temp_dir) / ".codex"
                rollout_ref = (
                    "sessions/2026/05/26/"
                    "rollout-2026-05-26T10-00-00-snapshot.jsonl"
                )
                rollout = codex_root / rollout_ref
                write_session_meta_rollout(
                    rollout,
                    "trusted-session",
                    "/trusted",
                    "trusted follow-up",
                )
                original = rollout.read_bytes()
                forged = original.replace(b"trusted-session", b"forged--session")
                self.assertEqual(len(forged), len(original))
                mutated = False
                restored = False

                if scope == "local":
                    real_parser = MODULE._parse_bounded_session_meta_prefix

                    def run_scan():
                        return MODULE._scan_session_meta_records(
                            codex_root=codex_root,
                            dates=[MODULE.dt.date(2026, 5, 26)],
                            limit=10,
                            host="local",
                        )

                else:
                    namespace = embedded_probe_namespace(
                        {
                            "mode": "session-meta",
                            "dates": ["2026/05/26"],
                            "limit": 10,
                            "codex_root": str(codex_root),
                            "session_meta_scan_bytes": (
                                MODULE.MAX_SESSION_META_SCAN_BYTES
                            ),
                        }
                    )
                    real_parser = namespace["parse_bounded_session_meta_prefix"]

                    def run_scan():
                        return embedded_session_meta_records(namespace)

                def transient_rewrite(prefix, source_size, start_offset=0):
                    nonlocal mutated, restored
                    with rollout.open("r+b") as handle:
                        handle.write(forged)
                        handle.truncate(len(forged))
                    mutated = True
                    result = real_parser(
                        prefix,
                        source_size=source_size,
                        start_offset=start_offset,
                    )
                    with rollout.open("r+b") as handle:
                        handle.write(original)
                        handle.truncate(len(original))
                    with rollout.open("ab") as handle:
                        handle.write(b"{}\n")
                    restored = True
                    return result

                if scope == "local":
                    patcher = mock.patch.object(
                        MODULE,
                        "_parse_bounded_session_meta_prefix",
                        side_effect=transient_rewrite,
                    )
                else:
                    patcher = mock.patch.dict(
                        namespace,
                        {"parse_bounded_session_meta_prefix": transient_rewrite},
                    )

                with patcher:
                    result = run_scan()
                if scope == "local":
                    session_ids = [row["session_id"] for row in result.rows]
                else:
                    session_ids = [
                        str(record["session_id"])
                        for record in result
                        if "session_id" in record
                    ]

                self.assertTrue(mutated)
                self.assertTrue(restored)
                self.assertEqual(session_ids, ["trusted-session"])
                self.assertGreater(rollout.stat().st_size, len(original))

    def test_active_candidate_safety_cap_does_not_follow_row_limit(self) -> None:
        for scope in ("local", "embedded"):
            for scenario in ("valid", "no_meta"):
                with self.subTest(
                    scope=scope,
                    scenario=scenario,
                ), tempfile.TemporaryDirectory() as temp_dir:
                    codex_root = Path(temp_dir) / ".codex"
                    rollout_refs = [
                        (
                            "sessions/2026/05/26/"
                            f"rollout-2026-05-26T10-00-0{index}-candidate.jsonl"
                        )
                        for index in range(3)
                    ]
                    for index, rollout_ref in enumerate(rollout_refs):
                        rollout = codex_root / rollout_ref
                        if scenario == "valid":
                            write_session_meta_rollout(
                                rollout,
                                f"candidate-{index}",
                                "/trusted",
                                "trusted follow-up",
                            )
                        else:
                            rollout.parent.mkdir(parents=True, exist_ok=True)
                            rollout.write_bytes(b"{}\n")
                    capture_names: list[str] = []
                    pread_requests: list[int] = []

                    if scope == "local":
                        target_os = MODULE.os
                        real_capture = (
                            MODULE._capture_initial_append_only_rollout_checkpoint
                        )

                        def run_scan():
                            return MODULE._scan_session_meta_records(
                                codex_root=codex_root,
                                dates=[MODULE.dt.date(2026, 5, 26)],
                                limit=1,
                                host="local",
                            )

                    else:
                        namespace = embedded_probe_namespace(
                            {
                                "mode": "session-meta",
                                "dates": ["2026/05/26"],
                                "limit": 1,
                                "codex_root": str(codex_root),
                                "session_meta_scan_bytes": (
                                    MODULE.MAX_SESSION_META_SCAN_BYTES
                                ),
                            }
                        )
                        target_os = namespace["os"]
                        real_capture = namespace[
                            "capture_initial_append_only_rollout_checkpoint"
                        ]

                        def run_scan():
                            return embedded_session_meta_records(namespace)

                    real_pread = target_os.pread

                    def tracking_capture(*args, **kwargs):
                        capture_names.append(str(args[2]))
                        return real_capture(*args, **kwargs)

                    def tracking_pread(fd: int, length: int, offset: int) -> bytes:
                        pread_requests.append(length)
                        return real_pread(fd, length, offset)

                    if scope == "local":
                        capture_patcher = mock.patch.object(
                            MODULE,
                            "_capture_initial_append_only_rollout_checkpoint",
                            side_effect=tracking_capture,
                        )
                    else:
                        capture_patcher = mock.patch.dict(
                            namespace,
                            {
                                "capture_initial_append_only_rollout_checkpoint": (
                                    tracking_capture
                                )
                            },
                        )

                    with capture_patcher, mock.patch.object(
                        target_os,
                        "pread",
                        side_effect=tracking_pread,
                    ):
                        result = run_scan()

                    expected_names = [
                        Path(rollout_refs[index]).name
                        for index in ((2, 1) if scenario == "valid" else (2, 1, 0))
                    ]
                    self.assertEqual(capture_names, expected_names)
                    self.assertTrue(pread_requests)
                    self.assertLessEqual(
                        max(pread_requests),
                        MODULE.SESSION_META_READ_CHUNK_BYTES,
                    )
                    if scope == "local":
                        self.assertEqual(result.truncated, scenario == "valid")
                        self.assertEqual(
                            result.truncation_reason,
                            (
                                MODULE.SESSION_META_LIMIT_TRUNCATED_REASON
                                if scenario == "valid"
                                else None
                            ),
                        )
                        session_ids = [row["session_id"] for row in result.rows]
                    else:
                        if scenario == "valid":
                            self.assertEqual(result[-1]["kind"], "truncation")
                            self.assertEqual(
                                result[-1]["reason"],
                                MODULE.SESSION_META_LIMIT_TRUNCATED_REASON,
                            )
                        else:
                            self.assertEqual(result, [])
                        session_ids = [
                            str(record["session_id"])
                            for record in result
                            if "session_id" in record
                        ]
                    self.assertEqual(
                        session_ids,
                        ["candidate-2"] if scenario == "valid" else [],
                    )

    def test_mixed_valid_and_no_meta_candidates_use_result_row_truncation(
        self,
    ) -> None:
        for scope in ("local", "embedded"):
            with self.subTest(scope=scope), tempfile.TemporaryDirectory() as temp_dir:
                codex_root = Path(temp_dir) / ".codex"
                session_dir = codex_root / "sessions/2026/05/26"
                oldest = session_dir / "rollout-2026-05-26T10-00-00-oldest.jsonl"
                middle = session_dir / "rollout-2026-05-26T10-00-01-no-meta.jsonl"
                newest = session_dir / "rollout-2026-05-26T10-00-02-newest.jsonl"
                write_session_meta_rollout(
                    oldest,
                    "oldest-session",
                    "/trusted",
                    "oldest follow-up",
                )
                middle.write_bytes(b"{}\n")
                write_session_meta_rollout(
                    newest,
                    "newest-session",
                    "/trusted",
                    "newest follow-up",
                )

                if scope == "local":
                    result = MODULE._scan_session_meta_records(
                        codex_root=codex_root,
                        dates=[MODULE.dt.date(2026, 5, 26)],
                        limit=1,
                        host="local",
                    )
                    self.assertTrue(result.truncated)
                    records = result.rows
                else:
                    namespace = embedded_probe_namespace(
                        {
                            "mode": "session-meta",
                            "dates": ["2026/05/26"],
                            "limit": 1,
                            "codex_root": str(codex_root),
                            "session_meta_scan_bytes": (
                                MODULE.MAX_SESSION_META_SCAN_BYTES
                            ),
                        }
                    )
                    records = embedded_session_meta_records(namespace)
                    self.assertEqual(records[-1]["kind"], "truncation")
                    self.assertEqual(
                        records[-1]["reason"],
                        MODULE.SESSION_META_LIMIT_TRUNCATED_REASON,
                    )

                self.assertEqual(
                    [
                        record["session_id"]
                        for record in records
                        if "session_id" in record
                    ],
                    ["newest-session"],
                )

    def test_append_only_policy_rejects_growth_followed_by_rollback(self) -> None:
        def regular_stat(size: int, timestamp_ns: int) -> argparse.Namespace:
            return argparse.Namespace(
                st_mode=MODULE.stat.S_IFREG | 0o600,
                st_size=size,
                st_dev=11,
                st_ino=22,
                st_mtime_ns=timestamp_ns,
                st_ctime_ns=timestamp_ns,
            )

        initial = regular_stat(100, 1)
        grown = regular_stat(200, 2)
        rolled_back = regular_stat(150, 3)

        for scope in ("local", "embedded"):
            if scope == "local":
                target_os = MODULE.os
                candidate = MODULE._rollout_candidate_identity_from_stat(initial)
                open_rollout = MODULE._open_pinned_regular_file_from_fd
                expected = MODULE._rollout_identity_from_stat(initial)
            else:
                namespace = embedded_probe_namespace(
                    {
                        "mode": "session-meta",
                        "dates": [],
                        "limit": 10,
                        "codex_root": "/tmp/unused",
                        "session_meta_scan_bytes": (
                            MODULE.MAX_SESSION_META_SCAN_BYTES
                        ),
                    }
                )
                target_os = namespace["os"]
                candidate = namespace["rollout_candidate_identity_from_stat"](
                    initial
                )
                open_rollout = namespace["open_pinned_regular_file_from_fd"]
                expected = namespace["rollout_identity_from_stat"](initial)

            with self.subTest(scope=scope, phase="during_open"), mock.patch.object(
                target_os,
                "stat",
                return_value=grown,
            ), mock.patch.object(
                target_os,
                "open",
                return_value=91,
            ), mock.patch.object(
                target_os,
                "fstat",
                return_value=rolled_back,
            ), mock.patch.object(
                target_os,
                "close",
            ) as close_fd, self.assertRaisesRegex(
                ValueError,
                "identity changed during open",
            ):
                open_rollout(
                    7,
                    "rollout.jsonl",
                    expected_identity=candidate,
                    allow_append=True,
                )
            close_fd.assert_called_once_with(91)

            with self.subTest(scope=scope, phase="after_scan"):
                if scope == "local":
                    handle = MODULE._PinnedRolloutHandle.__new__(
                        MODULE._PinnedRolloutHandle
                    )
                    handle._handle = mock.Mock()
                    handle._handle.fileno.return_value = 92
                    handle._parent_fd = 7
                    handle._name = "rollout.jsonl"
                    handle._prefix_proof = None

                    def assert_append_only() -> None:
                        handle.assert_append_only_identity(
                            expected,
                            phase="after session-meta scan",
                        )

                else:
                    handle_type = namespace["PinnedRolloutHandle"]
                    handle = handle_type.__new__(handle_type)
                    handle.handle = mock.Mock()
                    handle.handle.fileno.return_value = 92
                    handle.parent_fd = 7
                    handle.name = "rollout.jsonl"
                    handle.prefix_proof = None

                    def assert_append_only() -> None:
                        handle.assert_append_only_identity(
                            expected,
                            "after session-meta scan",
                        )

                with mock.patch.object(
                    target_os,
                    "fstat",
                    return_value=grown,
                ), mock.patch.object(
                    target_os,
                    "stat",
                    return_value=rolled_back,
                ), self.assertRaisesRegex(
                    ValueError,
                    "identity changed after session-meta scan",
                ):
                    assert_append_only()

    def test_append_only_policy_preserves_open_to_scan_handoff(self) -> None:
        for scope in ("local", "embedded"):
            with self.subTest(scope=scope), tempfile.TemporaryDirectory() as temp_dir:
                codex_root = Path(temp_dir) / ".codex"
                rollout_ref = (
                    "sessions/2026/05/26/"
                    "rollout-2026-05-26T10-00-00-handoff.jsonl"
                )
                rollout = codex_root / rollout_ref
                write_session_meta_rollout(
                    rollout,
                    "trusted-session",
                    "/trusted",
                    "trusted follow-up",
                )
                initial_stat = rollout.stat()
                grew = False
                rolled_back = False

                def grow() -> None:
                    nonlocal grew
                    if grew:
                        return
                    grew = True
                    with rollout.open("ab") as handle:
                        handle.write(b"x" * 256)

                def rollback() -> None:
                    nonlocal rolled_back
                    if rolled_back:
                        return
                    rolled_back = True
                    with rollout.open("r+b") as handle:
                        handle.truncate(initial_stat.st_size + 32)

                if scope == "local":
                    real_open = MODULE._open_pinned_rollout_text_from_parent_fd
                    real_capture = (
                        MODULE._capture_initial_append_only_rollout_checkpoint
                    )
                    real_checkpoint = MODULE._assert_append_only_rollout_checkpoint

                    def run_scan():
                        return MODULE._scan_session_meta_records(
                            codex_root=codex_root,
                            dates=[MODULE.dt.date(2026, 5, 26)],
                            limit=10,
                            host="local",
                        )

                else:
                    namespace = embedded_probe_namespace(
                        {
                            "mode": "session-meta",
                            "dates": ["2026/05/26"],
                            "limit": 10,
                            "codex_root": str(codex_root),
                            "session_meta_scan_bytes": (
                                MODULE.MAX_SESSION_META_SCAN_BYTES
                            ),
                        }
                    )
                    real_open = namespace[
                        "open_pinned_rollout_text_from_parent_fd"
                    ]
                    real_capture = namespace[
                        "capture_initial_append_only_rollout_checkpoint"
                    ]
                    real_checkpoint = namespace[
                        "assert_append_only_rollout_checkpoint"
                    ]

                    def run_scan():
                        return embedded_session_meta_records(namespace)

                def capture_then_grow(*args, **kwargs):
                    current, _snapshot, proof, _verified = real_capture(
                        *args, **kwargs
                    )
                    grow()
                    phase = kwargs.get("phase", args[3] if len(args) > 3 else "during open")
                    if scope == "local":
                        return real_checkpoint(
                            args[0],
                            args[1],
                            args[2],
                            current,
                            proof,
                            phase=phase,
                        )
                    return real_checkpoint(
                        args[0], args[1], args[2], current, proof, phase
                    )

                def open_then_rollback(*args, **kwargs):
                    handle = real_open(*args, **kwargs)
                    rollback()
                    return handle

                if scope == "local":
                    open_patcher = mock.patch.object(
                        MODULE,
                        "_open_pinned_rollout_text_from_parent_fd",
                        side_effect=open_then_rollback,
                    )
                    capture_patcher = mock.patch.object(
                        MODULE,
                        "_capture_initial_append_only_rollout_checkpoint",
                        side_effect=capture_then_grow,
                    )
                else:
                    open_patcher = mock.patch.dict(
                        namespace,
                        {
                            "open_pinned_rollout_text_from_parent_fd": (
                                open_then_rollback
                            )
                        },
                    )
                    capture_patcher = mock.patch.dict(
                        namespace,
                        {
                            "capture_initial_append_only_rollout_checkpoint": (
                                capture_then_grow
                            )
                        },
                    )

                with capture_patcher, open_patcher:
                    if scope == "local":
                        with self.assertRaises(
                            MODULE.SessionMetaRolloutError
                        ) as raised:
                            run_scan()
                        error = raised.exception.error
                    else:
                        records = run_scan()
                        self.assertEqual(len(records), 1)
                        error = str(records[0]["error"])

                final_stat = rollout.stat()
                self.assertTrue(grew)
                self.assertTrue(rolled_back)
                self.assertGreater(final_stat.st_size, initial_stat.st_size)
                self.assertIn("identity changed after session-meta scan", error)

    def test_archived_session_meta_rejects_same_inode_mutations(self) -> None:
        layouts = {
            "dated": (
                "archived_sessions/2026/05/26/"
                "rollout-2026-05-26T10-00-00-dated.jsonl",
                "append",
            ),
            "flat": (
                "archived_sessions/"
                "rollout-2026-05-26T10-00-00-flat.jsonl",
                "rewrite",
            ),
        }
        for scope in ("local", "embedded"):
            for layout, (rollout_ref, mutation) in layouts.items():
                for phase in ("post_read",):
                    with self.subTest(
                        scope=scope,
                        layout=layout,
                        phase=phase,
                        mutation=mutation,
                    ), tempfile.TemporaryDirectory() as temp_dir:
                        codex_root = Path(temp_dir) / ".codex"
                        rollout = codex_root / rollout_ref
                        write_session_meta_rollout(
                            rollout,
                            f"archived-{layout}",
                            "/trusted",
                            "trusted follow-up",
                        )
                        original = rollout.read_bytes()
                        original_stat = rollout.stat()
                        mutated = False

                        def mutate() -> None:
                            nonlocal mutated
                            if mutated:
                                return
                            mutated = True
                            if mutation == "append":
                                with rollout.open("ab") as handle:
                                    handle.write(b"{}\n")
                            else:
                                with rollout.open("r+b") as handle:
                                    handle.write(b" " + original[1:])
                                    handle.truncate(len(original))
                                os.utime(
                                    rollout,
                                    ns=(
                                        original_stat.st_atime_ns,
                                        original_stat.st_mtime_ns + 1_000_000_000,
                                    ),
                                )

                        if scope == "local":
                            real_open = (
                                MODULE._open_pinned_rollout_text_from_parent_fd
                            )
                            real_reader = MODULE._read_bounded_session_meta

                            def run_scan():
                                return MODULE._scan_session_meta_records(
                                    codex_root=codex_root,
                                    dates=[MODULE.dt.date(2026, 5, 26)],
                                    limit=10,
                                    host="local",
                                )

                        else:
                            namespace = embedded_probe_namespace(
                                {
                                    "mode": "session-meta",
                                    "dates": ["2026/05/26"],
                                    "limit": 10,
                                    "codex_root": str(codex_root),
                                    "session_meta_scan_bytes": (
                                        MODULE.MAX_SESSION_META_SCAN_BYTES
                                    ),
                                }
                            )
                            real_open = namespace["open_rollout_text"]
                            real_reader = namespace["read_bounded_session_meta"]

                            def run_scan():
                                return embedded_session_meta_records(namespace)

                        if phase == "before_open":
                            def mutate_then_open(*args, **kwargs):
                                mutate()
                                return real_open(*args, **kwargs)

                            if scope == "local":
                                patcher = mock.patch.object(
                                    MODULE,
                                    "_open_pinned_rollout_text_from_parent_fd",
                                    side_effect=mutate_then_open,
                                )
                            else:
                                patcher = mock.patch.dict(
                                    namespace,
                                    {"open_rollout_text": mutate_then_open},
                                )
                        else:
                            def read_then_mutate(*args, **kwargs):
                                result = real_reader(*args, **kwargs)
                                mutate()
                                return result

                            if scope == "local":
                                patcher = mock.patch.object(
                                    MODULE,
                                    "_read_bounded_session_meta",
                                    side_effect=read_then_mutate,
                                )
                            else:
                                patcher = mock.patch.dict(
                                    namespace,
                                    {"read_bounded_session_meta": read_then_mutate},
                                )

                        with patcher:
                            if scope == "local":
                                with self.assertRaises(
                                    MODULE.SessionMetaRolloutError
                                ) as raised:
                                    run_scan()
                                error = raised.exception.error
                            else:
                                records = run_scan()
                                self.assertEqual(len(records), 1)
                                error = str(records[0]["error"])

                        final_stat = rollout.stat()
                        self.assertTrue(mutated)
                        self.assertEqual(
                            (final_stat.st_dev, final_stat.st_ino),
                            (original_stat.st_dev, original_stat.st_ino),
                        )
                        self.assertIn("identity changed", error)

    def test_session_meta_bounds_directory_fds_across_31_dates(self) -> None:
        dates = [
            MODULE.dt.date(2026, 1, 1) + MODULE.dt.timedelta(days=offset)
            for offset in range(31)
        ]
        for scope in ("local", "embedded"):
            with self.subTest(scope=scope), tempfile.TemporaryDirectory() as temp_dir:
                codex_root = Path(temp_dir) / ".codex"
                for date_value in dates:
                    date_path = date_value.strftime("%Y/%m/%d")
                    (codex_root / "sessions" / date_path).mkdir(parents=True)
                    (codex_root / "archived_sessions" / date_path).mkdir(
                        parents=True
                    )

                if scope == "local":
                    target_os = MODULE.os

                    def run_scan():
                        return MODULE._scan_session_meta_records(
                            codex_root=codex_root,
                            dates=dates,
                            limit=10,
                            host="local",
                        )

                else:
                    namespace = embedded_probe_namespace(
                        {
                            "mode": "session-meta",
                            "dates": [
                                value.strftime("%Y/%m/%d") for value in dates
                            ],
                            "limit": 10,
                            "codex_root": str(codex_root),
                            "session_meta_scan_bytes": (
                                MODULE.MAX_SESSION_META_SCAN_BYTES
                            ),
                        }
                    )
                    target_os = namespace["os"]

                    def run_scan():
                        return embedded_session_meta_records(namespace)

                real_open = target_os.open
                real_dup = target_os.dup
                real_close = target_os.close
                directory_fds: set[int] = set()
                peak_directory_fds = 0

                def register_directory_fd(fd: int) -> int:
                    nonlocal peak_directory_fds
                    if len(directory_fds) >= 63:
                        real_close(fd)
                        raise OSError(24, "mock directory descriptor limit")
                    directory_fds.add(fd)
                    peak_directory_fds = max(
                        peak_directory_fds,
                        len(directory_fds),
                    )
                    return fd

                def tracking_open(
                    path: object,
                    flags: int,
                    mode: int = 0o777,
                    *,
                    dir_fd: int | None = None,
                ) -> int:
                    fd = real_open(path, flags, mode, dir_fd=dir_fd)
                    if flags & os.O_DIRECTORY:
                        return register_directory_fd(fd)
                    return fd

                def tracking_dup(fd: int) -> int:
                    duplicated = real_dup(fd)
                    if fd in directory_fds:
                        return register_directory_fd(duplicated)
                    return duplicated

                def tracking_close(fd: int) -> None:
                    directory_fds.discard(fd)
                    real_close(fd)

                with mock.patch.object(
                    target_os,
                    "open",
                    side_effect=tracking_open,
                ), mock.patch.object(
                    target_os,
                    "dup",
                    side_effect=tracking_dup,
                ), mock.patch.object(
                    target_os,
                    "close",
                    side_effect=tracking_close,
                ):
                    result = run_scan()

                if scope == "local":
                    self.assertEqual(result.rows, [])
                else:
                    self.assertEqual(result, [])
                self.assertLess(peak_directory_fds, 64)
                self.assertEqual(directory_fds, set())



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
        replacements = ("missing", "symlink", "directory")
        for replacement_kind in replacements:
            with (
                self.subTest(scope="local", replacement=replacement_kind),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                base = Path(temp_dir)
                codex_root = base / ".codex"
                codex_root.mkdir()
                moved_root = base / ".codex-pinned"
                external_root = base / "external-codex"
                external_root.mkdir()
                real_open = MODULE.os.open
                replaced = False

                def replace_root_before_open(path, *args, **kwargs):
                    nonlocal replaced
                    if (
                        str(path) == str(codex_root)
                        and kwargs.get("dir_fd") is None
                        and not replaced
                    ):
                        os.replace(codex_root, moved_root)
                        if replacement_kind == "symlink":
                            codex_root.symlink_to(external_root, target_is_directory=True)
                        elif replacement_kind == "directory":
                            os.replace(external_root, codex_root)
                        replaced = True
                    return real_open(path, *args, **kwargs)

                with (
                    mock.patch.object(
                        MODULE.os,
                        "open",
                        side_effect=replace_root_before_open,
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
                self.assertNotIn(secret_path, str(raised.exception))
                self.assertTrue(replaced)

            with (
                self.subTest(scope="embedded", replacement=replacement_kind),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                base = Path(temp_dir)
                codex_root = base / ".codex"
                codex_root.mkdir()
                moved_root = base / ".codex-pinned"
                external_root = base / "external-codex"
                external_root.mkdir()
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
                replacement = {
                    "missing": "",
                    "symlink": (
                        f"        ROOT.symlink_to(pathlib.Path({str(external_root)!r}), target_is_directory=True)\n"
                    ),
                    "directory": (
                        f"        os.replace({str(external_root)!r}, ROOT)\n"
                    ),
                }[replacement_kind]
                injection = (
                    "_real_open = os.open\n"
                    "_root_replaced = False\n"
                    "def injected_root_open(path, *args, **kwargs):\n"
                    "    global _root_replaced\n"
                    "    if str(path) == str(ROOT) and kwargs.get('dir_fd') is None and not _root_replaced:\n"
                    "        _root_replaced = True\n"
                    f"        os.replace(ROOT, pathlib.Path({str(moved_root)!r}))\n"
                    f"{replacement}"
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
            for rollout_fd in rollout_fds:
                with self.assertRaises(OSError):
                    os.fstat(rollout_fd)

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
                {"type": "response_item", "payload": "x" * io.DEFAULT_BUFFER_SIZE},
                separators=(",", ":"),
            ),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(codex_root, lines)
            active_path = codex_root / rollout
            archived_path = (
                codex_root
                / "archived_sessions/2026/05/26"
                / Path(rollout).name
            )
            archived_path.parent.mkdir(parents=True)
            os.replace(active_path, archived_path)
            rollout = archived_path.relative_to(codex_root).as_posix()
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

    def test_session_meta_rejects_invalid_utf8_locally_and_embedded(self) -> None:
        for invalid_field in ("id", "cwd"):
            with self.subTest(field=invalid_field), tempfile.TemporaryDirectory() as temp_dir:
                codex_root = Path(temp_dir) / ".codex"
                rollout = (
                    "sessions/2026/05/26/"
                    f"rollout-2026-05-26T10-00-00-invalid-{invalid_field}.jsonl"
                )
                rollout_path = codex_root / rollout
                rollout_path.parent.mkdir(parents=True)
                record = {
                    "type": "session_meta",
                    "payload": {"id": "VALID_ID", "cwd": "VALID_CWD"},
                }
                marker = f"VALID_{invalid_field.upper()}".encode("ascii")
                raw_line = json.dumps(record, separators=(",", ":")).encode("utf-8")
                raw_line = raw_line.replace(marker, b"\xff", 1) + b"\n"
                rollout_path.write_bytes(raw_line)

                embedded = embedded_probe_namespace(
                    {
                        "mode": "session-meta",
                        "dates": ["2026/05/26"],
                        "limit": 10,
                        "codex_root": str(codex_root),
                        "session_meta_scan_bytes": MODULE.MAX_SESSION_META_SCAN_BYTES,
                    }
                )
                for parser in (
                    MODULE._parse_bounded_session_meta_prefix,
                    embedded["parse_bounded_session_meta_prefix"],
                ):
                    with self.assertRaisesRegex(
                        ValueError,
                        f"^{MODULE.SESSION_META_INVALID_UTF8_ERROR}$",
                    ):
                        parser(raw_line, source_size=len(raw_line))

                with self.assertRaises(MODULE.SessionMetaRolloutError) as raised:
                    MODULE._scan_session_meta_records(
                        codex_root=codex_root,
                        dates=[MODULE.dt.date(2026, 5, 26)],
                        limit=10,
                        host="local",
                    )
                embedded_records = embedded_session_meta_records(embedded)

            self.assertEqual(
                raised.exception.error,
                MODULE.SESSION_META_INVALID_UTF8_ERROR,
            )
            self.assertEqual(raised.exception.rollout, rollout)
            self.assertEqual(
                embedded_records,
                [
                    {
                        "kind": "error",
                        "error": MODULE.SESSION_META_INVALID_UTF8_ERROR,
                        "rollout": rollout,
                    }
                ],
            )
            self.assertNotIn(rollout, str(raised.exception))

    def test_timestamp_and_session_meta_skip_non_object_schemas_locally_and_embedded(
        self,
    ) -> None:
        embedded = embedded_probe_namespace({"codex_root": "/unused"})
        pathological_lines = pathological_json_lines()
        if configured_int_max_str_digits() > 0:
            with self.assertRaises(ValueError) as oversized_integer_error:
                json.loads(pathological_lines[0])
            self.assertNotIsInstance(
                oversized_integer_error.exception,
                json.JSONDecodeError,
            )
        else:
            self.assertIsInstance(json.loads(pathological_lines[0]), int)
        timestamp_cases = (
            ("scalar", "0"),
            ("array", "[]"),
            ("null", "null"),
            ("oversized_integer", pathological_lines[0]),
            ("deep_nesting", pathological_lines[1]),
        )
        for case, line in timestamp_cases:
            with self.subTest(case=case):
                self.assertEqual(MODULE._timestamp_from_jsonl_line(line), "")
                self.assertEqual(embedded["timestamp_from_jsonl_line"](line), "")

        valid = json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": "later-valid", "cwd": "/repo"},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        prefix = b"".join(
            line.encode("utf-8") + b"\n"
            for line in [
                "0",
                "[]",
                "null",
                *pathological_lines,
                '{"type":"session_meta","payload":[]}',
            ]
        ) + valid + b"\n"
        expected = ("later-valid", "/repo", False)

        self.assertEqual(
            MODULE._parse_bounded_session_meta_prefix(
                prefix,
                source_size=len(prefix),
            ),
            expected,
        )
        self.assertEqual(
            embedded["parse_bounded_session_meta_prefix"](
                prefix,
                source_size=len(prefix),
            ),
            expected,
        )

    def test_pathological_json_fixture_supports_missing_int_digit_api(self) -> None:
        with mock.patch.object(
            sys,
            "get_int_max_str_digits",
            None,
            create=True,
        ):
            self.assertEqual(configured_int_max_str_digits(), 0)
            pathological_lines = pathological_json_lines()

        self.assertEqual(len(pathological_lines[0]), 5000)
        self.assertEqual(len(pathological_lines[1]), 20_001)

    def test_rollout_json_entrypoints_classify_mocked_recursion_errors(
        self,
    ) -> None:
        recursing_json = mock.Mock()
        recursing_json.loads.side_effect = RecursionError("mocked JSON recursion")
        recursing_json.dumps.side_effect = json.dumps

        local_scan_metadata: dict[str, int] = {}
        with mock.patch.object(MODULE, "json", recursing_json):
            self.assertEqual(MODULE._timestamp_from_jsonl_line("{}"), "")
            self.assertEqual(
                MODULE._parse_bounded_session_meta_prefix(
                    b"{}\n",
                    source_size=3,
                ),
                ("", "", False),
            )
            self.assertEqual(
                MODULE._summarize_rollout_records(
                    lines=["{}"],
                    keywords=[],
                    limit=20,
                    tail_records=0,
                    max_text_chars=80,
                    scan_metadata=local_scan_metadata,
                ),
                [],
            )
        self.assertEqual(local_scan_metadata["json_error_count"], 1)

        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(codex_root, ["{}"])
            embedded = embedded_probe_namespace(
                {
                    "mode": "rollout-summary",
                    "rollout": rollout,
                    "codex_root": str(codex_root),
                    "summary_keywords": [],
                    "summary_limit": 20,
                    "summary_scan_bytes": MODULE.MAX_ROLLOUT_SUMMARY_SCAN_BYTES,
                    "summary_line_bytes": MODULE.MAX_ROLLOUT_SUMMARY_LINE_BYTES,
                    "summary_tail_records": 0,
                    "summary_max_text_chars": 80,
                }
            )
            embedded["json"] = recursing_json
            self.assertEqual(embedded["timestamp_from_jsonl_line"]("{}"), "")
            self.assertEqual(
                embedded["parse_bounded_session_meta_prefix"](
                    b"{}\n",
                    source_size=3,
                ),
                ("", "", False),
            )
            embedded_scan_metadata: dict[str, int] = {}
            self.assertEqual(
                embedded["summarize_records"](
                    ["{}"],
                    scan_metadata=embedded_scan_metadata,
                ),
                [],
            )
            embedded_stdout = io.StringIO()
            with redirect_stdout(embedded_stdout):
                embedded["summarize_rollout"]()

        self.assertEqual(embedded_scan_metadata["json_error_count"], 1)
        embedded_records = MODULE._extract_framed_rollout_summary_records(
            embedded_stdout.getvalue(),
            begin_marker=MODULE.REMOTE_ROLLOUT_SUMMARY_BEGIN,
            end_marker=MODULE.REMOTE_ROLLOUT_SUMMARY_END,
            host="embedded",
            command="rollout-summary",
        )
        embedded_scan_meta = next(
            record for record in embedded_records if record["kind"] == "scan_meta"
        )
        self.assertEqual(embedded_scan_meta["json_error_count"], 1)

    def test_session_meta_rejects_current_entry_replacement_after_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(
                codex_root,
                ['{"type":"session_meta","payload":{"id":"trusted"}}'],
            )
            source_path = codex_root / rollout
            original_parser = MODULE._parse_bounded_session_meta_prefix

            def parse_then_replace(*args: object, **kwargs: object):
                result = original_parser(*args, **kwargs)
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
                    "_parse_bounded_session_meta_prefix",
                    side_effect=parse_then_replace,
                ),
                self.assertRaises(MODULE.SessionMetaRolloutError) as raised,
            ):
                MODULE._scan_session_meta_records(
                    codex_root=codex_root,
                    dates=[MODULE.dt.date(2026, 5, 26)],
                    limit=10,
                    host="local",
                )

        self.assertIn(
            "rollout identity changed after session-meta scan",
            raised.exception.error,
        )
        self.assertEqual(raised.exception.rollout, rollout)
        self.assertNotIn("external-sentinel", str(raised.exception))

    def test_rollout_summary_drops_oversized_bare_cr_suffix_locally_and_embedded(
        self,
    ) -> None:
        line_limit = 1024
        bare_cr_suffix = json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "bare-cr-suffix-must-not-escape",
                        }
                    ],
                },
            },
            separators=(",", ":"),
        ).encode("utf-8")
        following = json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "valid-record-after-oversized-line",
                        }
                    ],
                },
            },
            separators=(",", ":"),
        ).encode("utf-8")
        payload = (
            (b"x" * (64 * 1024))
            + b"\r"
            + bare_cr_suffix
            + b"\n"
            + following
            + b"\n"
        )

        with mock.patch.object(
            MODULE,
            "MAX_ROLLOUT_SUMMARY_LINE_BYTES",
            line_limit,
        ):
            local_lines = list(
                MODULE._bounded_text_lines(
                    io.BytesIO(payload),
                    len(payload),
                    len(payload),
                )
            )
        embedded = embedded_probe_namespace(
            {
                "mode": "rollout-summary",
                "rollout": "sessions/2026/05/26/rollout-bare-cr.jsonl",
                "codex_root": "/tmp/.codex",
                "summary_keywords": [],
                "summary_limit": 10,
                "summary_scan_bytes": len(payload),
                "summary_line_bytes": line_limit,
                "summary_tail_records": 0,
                "summary_max_text_chars": 200,
            }
        )
        embedded_lines = list(
            embedded["bounded_text_lines"](
                io.BytesIO(payload),
                len(payload),
                len(payload),
            )
        )

        expected = ["\n", following.decode("utf-8") + "\n"]
        self.assertEqual(local_lines, expected)
        self.assertEqual(embedded_lines, expected)
        for lines in (local_lines, embedded_lines):
            serialized = "".join(lines)
            self.assertNotIn("bare-cr-suffix-must-not-escape", serialized)
            self.assertIn("valid-record-after-oversized-line", serialized)

    def test_rollout_summary_line_boundaries_match_locally_and_embedded(
        self,
    ) -> None:
        record = json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": "boundary-session", "cwd": "/repo"},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        lf_record = record + b"\n"
        crlf_record = record + b"\r\n"
        capped_after_record = record + b"not-a-record\n"
        capped_after_lf = lf_record + b"not-a-record\n"
        cases = (
            ("lf", lf_record, len(lf_record), [lf_record.decode("utf-8")]),
            (
                "crlf",
                crlf_record,
                len(crlf_record),
                [crlf_record.decode("utf-8")],
            ),
            (
                "complete_lf_at_cap",
                capped_after_lf,
                len(lf_record),
                [lf_record.decode("utf-8")],
            ),
            (
                "parseable_prefix_at_cap",
                capped_after_record,
                len(record),
                [],
            ),
            (
                "true_eof_without_lf",
                record,
                len(record),
                [record.decode("utf-8")],
            ),
        )
        embedded = embedded_probe_namespace(
            {
                "mode": "rollout-summary",
                "rollout": "sessions/2026/05/26/rollout-boundaries.jsonl",
                "codex_root": "/tmp/.codex",
                "summary_keywords": [],
                "summary_limit": 10,
                "summary_scan_bytes": max(len(payload) for _, payload, _, _ in cases),
                "summary_line_bytes": 4096,
                "summary_tail_records": 0,
                "summary_max_text_chars": 200,
            }
        )

        for name, payload, scan_bytes, expected in cases:
            with self.subTest(name=name):
                local_lines = list(
                    MODULE._bounded_text_lines(
                        io.BytesIO(payload),
                        scan_bytes,
                        len(payload),
                    )
                )
                embedded_lines = list(
                    embedded["bounded_text_lines"](
                        io.BytesIO(payload),
                        scan_bytes,
                        len(payload),
                    )
                )
                self.assertEqual(local_lines, expected)
                self.assertEqual(embedded_lines, expected)

    def test_rollout_summary_size_fallback_is_bytesio_only_locally_and_embedded(
        self,
    ) -> None:
        record = json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": "fallback-session", "cwd": "/repo"},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        truncated_payload = record + b"\n"
        embedded = embedded_probe_namespace(
            {
                "mode": "rollout-summary",
                "rollout": "sessions/2026/05/26/rollout-fallback.jsonl",
                "codex_root": "/tmp/.codex",
                "summary_keywords": [],
                "summary_limit": 10,
                "summary_scan_bytes": len(truncated_payload),
                "summary_line_bytes": 4096,
                "summary_tail_records": 0,
                "summary_max_text_chars": 200,
            }
        )
        readers = (
            ("local", MODULE._bounded_text_lines),
            ("embedded", embedded["bounded_text_lines"]),
        )

        for implementation, reader in readers:
            with self.subTest(implementation=implementation, snapshot="truncated"):
                self.assertEqual(
                    list(reader(io.BytesIO(truncated_payload), len(record))),
                    [],
                )
            with self.subTest(implementation=implementation, snapshot="complete"):
                self.assertEqual(
                    list(reader(io.BytesIO(record), len(record))),
                    [record.decode("utf-8")],
                )
            with self.subTest(implementation=implementation, handle="non-bytesio"):
                handle = io.BufferedReader(io.BytesIO(truncated_payload))
                with self.assertRaisesRegex(
                    ValueError,
                    "rollout summary source size is required",
                ):
                    list(reader(handle, len(record)))

    def test_rollout_summary_rejects_nonzero_cursor_locally_and_embedded(
        self,
    ) -> None:
        prefix = b'{"ignored":true}\n'
        record = json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": "nonzero-session", "cwd": "/repo"},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        boundary_payload = prefix + record
        mid_record_offset = record.index(b'"payload"')
        embedded = embedded_probe_namespace(
            {
                "mode": "rollout-summary",
                "rollout": "sessions/2026/05/26/rollout-nonzero.jsonl",
                "codex_root": "/tmp/.codex",
                "summary_keywords": [],
                "summary_limit": 10,
                "summary_scan_bytes": len(boundary_payload),
                "summary_line_bytes": 4096,
                "summary_tail_records": 0,
                "summary_max_text_chars": 200,
            }
        )
        readers = (
            ("local", MODULE._bounded_text_lines),
            ("embedded", embedded["bounded_text_lines"]),
        )

        for implementation, reader in readers:
            for source_mode in ("explicit", "inferred"):
                for offset_kind, payload, start_offset in (
                    ("lf-boundary", boundary_payload, len(prefix)),
                    ("mid-record", record, mid_record_offset),
                ):
                    with self.subTest(
                        implementation=implementation,
                        source_mode=source_mode,
                        offset_kind=offset_kind,
                    ):
                        handle = io.BytesIO(payload)
                        handle.seek(start_offset)
                        args = [handle, len(payload)]
                        if source_mode == "explicit":
                            args.append(len(payload))
                        with self.assertRaisesRegex(
                            ValueError,
                            "rollout summary reader must start at byte 0",
                        ):
                            list(reader(*args))

    def test_rollout_summary_rejects_unavailable_or_invalid_start_offset_locally_and_embedded(
        self,
    ) -> None:
        class OffsetlessReader:
            def read(self, _size: int) -> bytes:
                return b""

        class InvalidOffsetReader(OffsetlessReader):
            def tell(self) -> int:
                return 2

        embedded = embedded_probe_namespace(
            {
                "mode": "rollout-summary",
                "rollout": "sessions/2026/05/26/rollout-offset.jsonl",
                "codex_root": "/tmp/.codex",
                "summary_keywords": [],
                "summary_limit": 10,
                "summary_scan_bytes": 1,
                "summary_line_bytes": 4096,
                "summary_tail_records": 0,
                "summary_max_text_chars": 200,
            }
        )
        readers = (
            ("local", MODULE._bounded_text_lines),
            ("embedded", embedded["bounded_text_lines"]),
        )

        for implementation, reader in readers:
            with self.subTest(implementation=implementation, offset="unavailable"):
                with self.assertRaisesRegex(
                    ValueError,
                    "rollout summary start offset is unavailable",
                ):
                    list(reader(OffsetlessReader(), 1, 1))
            with self.subTest(implementation=implementation, offset="invalid"):
                with self.assertRaisesRegex(
                    ValueError,
                    "rollout summary start offset is invalid",
                ):
                    list(reader(InvalidOffsetReader(), 1, 1))

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
                            max_bytes=MODULE.MAX_DIRECT_FETCH_ROLLOUT_BYTES,
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
                if str(path) == str(codex_root) and dir_fd is None and not swapped:
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
                    max_bytes=MODULE.MAX_DIRECT_FETCH_ROLLOUT_BYTES,
                )

        self.assertTrue(swapped)

    def test_rollout_root_symlink_swap_after_lstat_fails_closed(self) -> None:
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
                if str(path) == str(codex_root) and dir_fd is None and not swapped:
                    os.replace(codex_root, moved_root)
                    codex_root.symlink_to(external_root, target_is_directory=True)
                    swapped = True
                return real_open(path, flags, mode, dir_fd=dir_fd)

            with (
                mock.patch.object(
                    MODULE.os,
                    "open",
                    side_effect=swap_root_before_open,
                ),
                self.assertRaises((OSError, ValueError)),
            ):
                MODULE._read_local_rollout_bytes(
                    codex_root,
                    rollout_relative,
                    max_bytes=MODULE.MAX_DIRECT_FETCH_ROLLOUT_BYTES,
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
                    max_bytes=MODULE.MAX_DIRECT_FETCH_ROLLOUT_BYTES,
                )

        self.assertTrue(swapped)

    def test_session_meta_ancestor_disappearance_after_stat_fails_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            write_rollout(
                codex_root,
                ['{"type":"session_meta","payload":{"id":"trusted"}}'],
            )
            sessions = codex_root / "sessions"
            moved_sessions = codex_root / "sessions-vanished"
            real_open = MODULE.os.open
            vanished = False

            def vanish_sessions_before_open(
                path: object,
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                nonlocal vanished
                if path == "sessions" and dir_fd is not None and not vanished:
                    os.replace(sessions, moved_sessions)
                    vanished = True
                return real_open(path, flags, mode, dir_fd=dir_fd)

            with (
                mock.patch.object(
                    MODULE.os,
                    "open",
                    side_effect=vanish_sessions_before_open,
                ),
                self.assertRaises(MODULE.SessionMetaRolloutError) as raised,
            ):
                MODULE._scan_session_meta_records(
                    codex_root=codex_root,
                    dates=[MODULE.dt.date(2026, 5, 26)],
                    limit=10,
                    host="local",
                )

            self.assertTrue(vanished)
            self.assertEqual(raised.exception.error, "session directory unreadable")
            self.assertIsNone(raised.exception.rollout)
            os.replace(moved_sessions, sessions)

            script = MODULE._remote_python_script(
                {
                    "mode": "session-meta",
                    "dates": ["2026/05/26"],
                    "limit": 10,
                    "codex_root": str(codex_root),
                    "session_meta_scan_bytes": MODULE.MAX_SESSION_META_SCAN_BYTES,
                }
            )
            marker = (
                "                next_fd = os.open(part, directory_open_flags(), "
                "dir_fd=directory_fd)\n"
            )
            injection = (
                "                if part == 'sessions' and not globals().get('_ancestor_vanished', False):\n"
                "                    globals()['_ancestor_vanished'] = True\n"
                f"                    os.replace({str(sessions)!r}, {str(moved_sessions)!r})\n"
                + marker
            )
            self.assertEqual(script.count(marker), 1)
            script = script.replace(marker, injection, 1)
            embedded = subprocess.run(
                [sys.executable, "-"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(embedded.returncode, 0, embedded.stderr)
        embedded_records = [
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
            embedded_records,
            [{"kind": "error", "error": "session directory unreadable"}],
        )

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

    def test_rollout_unlink_between_stat_and_open_fails_closed_local_and_embedded(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(
                codex_root,
                ['{"type":"session_meta","payload":{"id":"trusted"}}'],
            )
            rollout_relative = MODULE._resolve_rollout_relative_path(rollout)
            rollout_path = codex_root / rollout
            real_open = MODULE.os.open
            unlinked = False

            def unlink_rollout_before_open(
                path: object,
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                nonlocal unlinked
                if path == rollout_path.name and dir_fd is not None and not unlinked:
                    rollout_path.unlink()
                    unlinked = True
                return real_open(path, flags, mode, dir_fd=dir_fd)

            with (
                mock.patch.object(
                    MODULE.os,
                    "open",
                    side_effect=unlink_rollout_before_open,
                ),
                self.assertRaisesRegex(ValueError, "rollout changed during open"),
            ):
                MODULE._read_local_rollout_bytes(
                    codex_root,
                    rollout_relative,
                    max_bytes=MODULE.MAX_DIRECT_FETCH_ROLLOUT_BYTES,
            )

            self.assertTrue(unlinked)
            rollout_path.write_text(
                '{"type":"session_meta","payload":{"id":"trusted"}}\n',
                encoding="utf-8",
            )
            script = MODULE._remote_python_script(
                {
                    "mode": "fetch-rollout",
                    "rollout": rollout,
                    "codex_root": str(codex_root),
                    "max_direct_fetch_rollout_bytes": (
                        MODULE.MAX_DIRECT_FETCH_ROLLOUT_BYTES
                    ),
                }
            )
            marker = (
                "    try:\n"
                "        fd = os.open(name, regular_file_open_flags(), dir_fd=parent_fd)\n"
            )
            injection = (
                "    try:\n"
                f"        if name == {rollout_path.name!r} and not globals().get('_rollout_unlinked', False):\n"
                "            globals()['_rollout_unlinked'] = True\n"
                f"            pathlib.Path({str(rollout_path)!r}).unlink()\n"
                "        fd = os.open(name, regular_file_open_flags(), dir_fd=parent_fd)\n"
            )
            self.assertEqual(script.count(marker), 3)
            script = script.replace(marker, injection, 1)
            embedded = subprocess.run(
                [sys.executable, "-"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(embedded.returncode, 0, embedded.stderr)
        payload_lines = MODULE._extract_framed_lines(
            embedded.stdout,
            begin_marker=MODULE.REMOTE_FETCH_ROLLOUT_BEGIN,
            end_marker=MODULE.REMOTE_FETCH_ROLLOUT_END,
            host="embedded",
            command="fetch-rollout",
        )
        self.assertEqual(
            [json.loads(line) for line in payload_lines],
            [{"ok": False, "error": "rollout changed during open"}],
        )
        self.assertNotIn(str(rollout_path), embedded.stdout)

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
                    max_bytes=MODULE.MAX_DIRECT_FETCH_ROLLOUT_BYTES,
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
                    "max_direct_fetch_rollout_bytes": (
                        MODULE.MAX_DIRECT_FETCH_ROLLOUT_BYTES
                    ),
                }
            )
            marker = (
                "    try:\n"
                "        fd = os.open(name, regular_file_open_flags(), dir_fd=parent_fd)\n"
            )
            injection = (
                "    try:\n"
                f"        if name == {rollout_path.name!r} and not globals().get('_fifo_swapped', False):\n"
                "            globals()['_fifo_swapped'] = True\n"
                f"            os.replace({str(rollout_path)!r}, {str(moved_rollout)!r})\n"
                f"            os.mkfifo({str(rollout_path)!r}, 0o600)\n"
                "        fd = os.open(name, regular_file_open_flags(), dir_fd=parent_fd)\n"
            )
            self.assertEqual(script.count(marker), 3)
            self.assertIn('getattr(os, "O_NONBLOCK", None)', script)
            self.assertIn("| nonblocking_flag", script)
            script = script.replace(marker, injection, 1)
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
                    "max_direct_fetch_rollout_bytes": (
                        MODULE.MAX_DIRECT_FETCH_ROLLOUT_BYTES
                    ),
                }
            )
            marker = (
                "            except FileNotFoundError as error:\n"
                "                raise ValueError(\"path ancestor changed during open\") from error\n"
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

    def test_session_meta_limit_precedes_next_row_output_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout_dir = codex_root / "sessions/2026/05/26"
            valid_rollout = rollout_dir / "rollout-2026-05-26T11-00-00-valid.jsonl"
            oversized_rollout = (
                rollout_dir / "rollout-2026-05-26T10-00-00-oversized.jsonl"
            )
            write_session_meta_rollout(valid_rollout, "within-limit", "/repo", "ok")
            write_session_meta_rollout(
                oversized_rollout,
                "beyond-limit",
                "x" * (MODULE.MAX_REMOTE_SESSION_META_SERIALIZED_ROW_BYTES + 1024),
                "must not be validated",
            )

            local_scan = MODULE._scan_session_meta_records(
                codex_root=codex_root,
                dates=[MODULE.dt.date(2026, 5, 26)],
                limit=1,
                host="local",
            )
            script = MODULE._remote_python_script(
                {
                    "mode": "session-meta",
                    "dates": ["2026/05/26"],
                    "limit": 1,
                    "codex_root": str(codex_root),
                    "session_meta_scan_bytes": MODULE.MAX_SESSION_META_SCAN_BYTES,
                }
            )
            embedded = subprocess.run(
                [sys.executable, "-"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(
            [row["session_id"] for row in local_scan.rows],
            ["within-limit"],
        )
        self.assertTrue(local_scan.truncated)
        self.assertEqual(embedded.returncode, 0, embedded.stderr)
        embedded_records = [
            json.loads(line)
            for line in MODULE._extract_framed_lines(
                embedded.stdout,
                begin_marker=MODULE.REMOTE_SESSION_META_BEGIN,
                end_marker=MODULE.REMOTE_SESSION_META_END,
                host="embedded",
                command="session-meta",
            )
        ]
        self.assertEqual(len(embedded_records), 2)
        self.assertEqual(embedded_records[0]["session_id"], "within-limit")
        self.assertEqual(
            embedded_records[1],
            {
                "kind": "truncation",
                "reason": MODULE.SESSION_META_LIMIT_TRUNCATED_REASON,
                "date": "2026/05/26",
                "limit": 1,
            },
        )
        self.assertNotIn(
            MODULE.SESSION_META_OUTPUT_ROW_TOO_LARGE_ERROR, embedded.stdout
        )
        self.assertNotIn("beyond-limit", embedded.stdout)

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
                "max_automatic_full_reconstruction_bytes": (
                    MODULE.MAX_AUTOMATIC_FULL_RECONSTRUCTION_BYTES
                ),
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
                "max_automatic_full_reconstruction_bytes": (
                    MODULE.MAX_AUTOMATIC_FULL_RECONSTRUCTION_BYTES
                ),
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
                "max_direct_fetch_rollout_bytes": (
                    MODULE.MAX_DIRECT_FETCH_ROLLOUT_BYTES
                ),
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
            '"full_fetch_limit_bytes": '
            "MAX_AUTOMATIC_FULL_RECONSTRUCTION_BYTES",
            chunked_script,
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
            payload["max_automatic_full_reconstruction_bytes"],
            MODULE.MAX_AUTOMATIC_FULL_RECONSTRUCTION_BYTES,
        )
        self.assertEqual(
            payload["max_fetch_rollout_chunk_bytes"],
            MODULE.MAX_FETCH_ROLLOUT_CHUNK_BYTES,
        )
        self.assertEqual(
            payload["max_fetch_range_plan_entries"],
            MODULE.MAX_FETCH_RANGE_PLAN_ENTRIES,
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
                    "max_automatic_full_reconstruction_bytes": (
                        MODULE.MAX_AUTOMATIC_FULL_RECONSTRUCTION_BYTES
                    ),
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
                    "max_automatic_full_reconstruction_bytes": (
                        MODULE.MAX_AUTOMATIC_FULL_RECONSTRUCTION_BYTES
                    ),
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
                record["full_fetch_limit_bytes"]
                == MODULE.MAX_AUTOMATIC_FULL_RECONSTRUCTION_BYTES
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
        with mock.patch.object(
            MODULE,
            "MAX_AUTOMATIC_FULL_RECONSTRUCTION_BYTES",
            64,
        ):
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

    def test_iter_rollout_chunks_bounds_physical_records_local_and_embedded(
        self,
    ) -> None:
        cap = MODULE.MAX_ROLLOUT_CHUNK_RECORDS
        data = b"\n" * (cap + 1)
        local_chunks = list(
            MODULE._iter_rollout_chunks(
                io.BytesIO(data),
                chunk_bytes=len(data),
                source_bytes=len(data),
            )
        )
        embedded = embedded_probe_namespace({"codex_root": "/unused"})
        embedded_chunks = list(
            embedded["iter_rollout_chunks"](
                io.BytesIO(data),
                len(data),
                len(data),
            )
        )

        self.assertEqual([len(chunk.lines) for chunk in local_chunks], [cap, 1])
        self.assertEqual(
            [len(chunk["lines"]) for chunk in embedded_chunks],
            [cap, 1],
        )
        self.assertEqual(
            [(chunk.byte_start, chunk.byte_end) for chunk in local_chunks],
            [(0, cap), (cap, cap + 1)],
        )
        self.assertEqual(
            [
                (chunk["byte_start"], chunk["byte_end"])
                for chunk in embedded_chunks
            ],
            [(0, cap), (cap, cap + 1)],
        )
        self.assertEqual(
            [(chunk.record_start, chunk.record_end) for chunk in local_chunks],
            [(1, cap), (cap + 1, cap + 1)],
        )

    def test_chunked_summary_json_errors_require_raw_fetch_local_and_embedded(
        self,
    ) -> None:
        user_line = json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Inspect this."}],
                },
            },
            separators=(",", ":"),
        )
        assistant_line = json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Completed."}],
                },
            },
            separators=(",", ":"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(
                codex_root,
                [user_line, "{malformed", assistant_line],
            )
            identity = rollout_identity(codex_root, rollout)
            with mock.patch.object(MODULE, "MIN_ROLLOUT_CHUNK_BYTES", 1):
                local_records = MODULE._chunked_rollout_summary_records(
                    codex_root=codex_root,
                    rollout_relative_path=MODULE._resolve_rollout_relative_path(
                        rollout
                    ),
                    chunk_bytes=identity.size,
                    keywords=[],
                    limit_per_chunk=20,
                    tail_records=4,
                    max_text_chars=200,
                    host="local",
                    expected_identity=identity,
                    authorized_source_bytes=None,
                )
            script = MODULE._remote_python_script(
                {
                    "mode": "chunked-rollout-summary",
                    "rollout": rollout,
                    "codex_root": str(codex_root),
                    "summary_keywords": [],
                    "summary_limit": 20,
                    "summary_tail_records": 4,
                    "summary_max_text_chars": 200,
                    "chunk_bytes": identity.size,
                    "max_automatic_full_reconstruction_bytes": (
                        MODULE.MAX_AUTOMATIC_FULL_RECONSTRUCTION_BYTES
                    ),
                    "max_fetch_rollout_chunk_bytes": (
                        MODULE.MAX_FETCH_ROLLOUT_CHUNK_BYTES
                    ),
                    "min_rollout_chunk_bytes": 1,
                    "max_rollout_chunk_bytes": identity.size,
                    "max_chunked_summary_output_bytes": (
                        MODULE.MAX_CHUNKED_ROLLOUT_SUMMARY_OUTPUT_BYTES
                    ),
                    "max_fetch_range_plan_entries": (
                        MODULE.MAX_FETCH_RANGE_PLAN_ENTRIES
                    ),
                    "expected_source_bytes": identity.size,
                    "expected_source_identity": MODULE._rollout_identity_token(
                        identity
                    ),
                    "authorized_source_bytes": None,
                    "output_host": "embedded",
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
        embedded_records = [
            json.loads(line)
            for line in MODULE._extract_framed_lines(
                result.stdout,
                begin_marker=MODULE.REMOTE_CHUNKED_ROLLOUT_SUMMARY_BEGIN,
                end_marker=MODULE.REMOTE_CHUNKED_ROLLOUT_SUMMARY_END,
                host="embedded",
                command="chunked-rollout-summary",
            )
            if "\"kind\"" in line
        ]
        for records in (local_records, embedded_records):
            chunk_meta = next(
                record for record in records if record["kind"] == "chunk_meta"
            )
            self.assertEqual(chunk_meta["decode_error_count"], 0)
            self.assertEqual(chunk_meta["json_error_count"], 1)
            self.assertEqual(chunk_meta["coverage_status"], "partial")
            self.assertTrue(chunk_meta["raw_fetch_recommended"])
            self.assertNotIn("utf8_decode_error", chunk_meta["reason_codes"])
            self.assertIn("json_parse_error", chunk_meta["reason_codes"])
            self.assertNotIn("no_structured_evidence", chunk_meta["reason_codes"])
            self.assertEqual(chunk_meta["records_emitted"], 2)
            self.assertEqual(chunk_meta["fetch_range_count"], 1)

    def test_chunked_summary_counts_non_object_schemas_and_keeps_later_evidence(
        self,
    ) -> None:
        user_timestamp = "2026-05-26T10:01:00Z"
        assistant_timestamp = "2026-05-26T10:02:00Z"
        user_line = json.dumps(
            {
                "timestamp": user_timestamp,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Inspect this."}],
                },
            },
            separators=(",", ":"),
        )
        assistant_line = json.dumps(
            {
                "timestamp": assistant_timestamp,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Completed."}],
                },
            },
            separators=(",", ":"),
        )
        malformed_schemas = [
            "0",
            "[]",
            "null",
            *pathological_json_lines(),
            '{"type":"response_item","payload":[]}',
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(
                codex_root,
                [*malformed_schemas, user_line, assistant_line],
            )
            identity = rollout_identity(codex_root, rollout)
            with mock.patch.object(MODULE, "MIN_ROLLOUT_CHUNK_BYTES", 1):
                local_records = MODULE._chunked_rollout_summary_records(
                    codex_root=codex_root,
                    rollout_relative_path=MODULE._resolve_rollout_relative_path(
                        rollout
                    ),
                    chunk_bytes=identity.size,
                    keywords=[],
                    limit_per_chunk=20,
                    tail_records=4,
                    max_text_chars=200,
                    host="local",
                    expected_identity=identity,
                    authorized_source_bytes=None,
                )
            script = MODULE._remote_python_script(
                {
                    "mode": "chunked-rollout-summary",
                    "rollout": rollout,
                    "codex_root": str(codex_root),
                    "summary_keywords": [],
                    "summary_limit": 20,
                    "summary_tail_records": 4,
                    "summary_max_text_chars": 200,
                    "chunk_bytes": identity.size,
                    "max_automatic_full_reconstruction_bytes": (
                        MODULE.MAX_AUTOMATIC_FULL_RECONSTRUCTION_BYTES
                    ),
                    "max_fetch_rollout_chunk_bytes": (
                        MODULE.MAX_FETCH_ROLLOUT_CHUNK_BYTES
                    ),
                    "min_rollout_chunk_bytes": 1,
                    "max_rollout_chunk_bytes": identity.size,
                    "max_chunked_summary_output_bytes": (
                        MODULE.MAX_CHUNKED_ROLLOUT_SUMMARY_OUTPUT_BYTES
                    ),
                    "max_fetch_range_plan_entries": (
                        MODULE.MAX_FETCH_RANGE_PLAN_ENTRIES
                    ),
                    "expected_source_bytes": identity.size,
                    "expected_source_identity": MODULE._rollout_identity_token(
                        identity
                    ),
                    "authorized_source_bytes": None,
                    "output_host": "embedded",
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
        embedded_records = [
            json.loads(line)
            for line in MODULE._extract_framed_lines(
                result.stdout,
                begin_marker=MODULE.REMOTE_CHUNKED_ROLLOUT_SUMMARY_BEGIN,
                end_marker=MODULE.REMOTE_CHUNKED_ROLLOUT_SUMMARY_END,
                host="embedded",
                command="chunked-rollout-summary",
            )
            if '"kind"' in line
        ]
        for records in (local_records, embedded_records):
            chunk_meta = next(
                record for record in records if record["kind"] == "chunk_meta"
            )
            self.assertEqual(chunk_meta["json_error_count"], len(malformed_schemas))
            self.assertEqual(chunk_meta["first_timestamp"], user_timestamp)
            self.assertEqual(chunk_meta["last_timestamp"], assistant_timestamp)
            self.assertEqual(chunk_meta["coverage_status"], "partial")
            self.assertTrue(chunk_meta["raw_fetch_recommended"])
            self.assertIn("json_parse_error", chunk_meta["reason_codes"])
            self.assertEqual(chunk_meta["records_emitted"], 2)
            self.assertEqual(
                [
                    record["kind"]
                    for record in records
                    if record["kind"] in {"user_message", "assistant_message"}
                ],
                ["user_message", "assistant_message"],
            )

    def test_chunked_summary_invalid_utf8_requires_raw_fetch_local_and_embedded(
        self,
    ) -> None:
        user_line = json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Inspect this."}],
                },
            },
            separators=(",", ":"),
        )
        corrupt_line = json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "corrupt INVALID byte"}
                    ],
                },
            },
            separators=(",", ":"),
        ).encode()
        corrupt_line = corrupt_line.replace(b"INVALID", b"\xff")
        assistant_line = json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Completed."}],
                },
            },
            separators=(",", ":"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(codex_root, [user_line, assistant_line])
            rollout_path = codex_root / rollout
            rollout_path.write_bytes(
                user_line.encode()
                + b"\n"
                + corrupt_line
                + b"\n"
                + assistant_line.encode()
                + b"\n"
            )
            identity = rollout_identity(codex_root, rollout)
            with mock.patch.object(MODULE, "MIN_ROLLOUT_CHUNK_BYTES", 1):
                local_records = MODULE._chunked_rollout_summary_records(
                    codex_root=codex_root,
                    rollout_relative_path=MODULE._resolve_rollout_relative_path(
                        rollout
                    ),
                    chunk_bytes=identity.size,
                    keywords=[],
                    limit_per_chunk=20,
                    tail_records=4,
                    max_text_chars=200,
                    host="local",
                    expected_identity=identity,
                    authorized_source_bytes=None,
                )
            script = MODULE._remote_python_script(
                {
                    "mode": "chunked-rollout-summary",
                    "rollout": rollout,
                    "codex_root": str(codex_root),
                    "summary_keywords": [],
                    "summary_limit": 20,
                    "summary_tail_records": 4,
                    "summary_max_text_chars": 200,
                    "chunk_bytes": identity.size,
                    "max_automatic_full_reconstruction_bytes": (
                        MODULE.MAX_AUTOMATIC_FULL_RECONSTRUCTION_BYTES
                    ),
                    "max_fetch_rollout_chunk_bytes": (
                        MODULE.MAX_FETCH_ROLLOUT_CHUNK_BYTES
                    ),
                    "min_rollout_chunk_bytes": 1,
                    "max_rollout_chunk_bytes": identity.size,
                    "max_chunked_summary_output_bytes": (
                        MODULE.MAX_CHUNKED_ROLLOUT_SUMMARY_OUTPUT_BYTES
                    ),
                    "max_fetch_range_plan_entries": (
                        MODULE.MAX_FETCH_RANGE_PLAN_ENTRIES
                    ),
                    "expected_source_bytes": identity.size,
                    "expected_source_identity": MODULE._rollout_identity_token(
                        identity
                    ),
                    "authorized_source_bytes": None,
                    "output_host": "embedded",
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
        embedded_records = [
            json.loads(line)
            for line in MODULE._extract_framed_lines(
                result.stdout,
                begin_marker=MODULE.REMOTE_CHUNKED_ROLLOUT_SUMMARY_BEGIN,
                end_marker=MODULE.REMOTE_CHUNKED_ROLLOUT_SUMMARY_END,
                host="embedded",
                command="chunked-rollout-summary",
            )
            if "\"kind\"" in line
        ]
        for records in (local_records, embedded_records):
            chunk_meta = next(
                record for record in records if record["kind"] == "chunk_meta"
            )
            self.assertEqual(chunk_meta["decode_error_count"], 1)
            self.assertEqual(chunk_meta["json_error_count"], 1)
            self.assertEqual(chunk_meta["coverage_status"], "partial")
            self.assertTrue(chunk_meta["raw_fetch_recommended"])
            self.assertIn("utf8_decode_error", chunk_meta["reason_codes"])
            self.assertIn("json_parse_error", chunk_meta["reason_codes"])
            self.assertNotIn("no_structured_evidence", chunk_meta["reason_codes"])
            self.assertEqual(chunk_meta["records_emitted"], 2)
            self.assertEqual(chunk_meta["fetch_range_count"], 1)
            self.assertNotIn("\ufffd", json.dumps(records, ensure_ascii=False))

    def test_chunked_summary_rejects_global_fetch_plan_local_and_embedded(
        self,
    ) -> None:
        error_text = "fetch range plan too large: 5 ranges > 4"
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(
                codex_root,
                ["{}"] * 5,
            )
            identity = rollout_identity(codex_root, rollout)
            expected_identity = identity_kwargs(identity)
            expected_identity["authorized_source_bytes"] = identity.size
            with (
                mock.patch.object(
                    MODULE,
                    "_local_codex_root",
                    return_value=codex_root,
                ),
                mock.patch.object(MODULE, "MIN_ROLLOUT_CHUNK_BYTES", 1),
                mock.patch.object(
                    MODULE,
                    "MAX_AUTOMATIC_FULL_RECONSTRUCTION_BYTES",
                    identity.size - 1,
                ),
                mock.patch.object(MODULE, "MAX_FETCH_RANGE_PLAN_ENTRIES", 4),
            ):
                local_stdout = io.StringIO()
                local_stderr = io.StringIO()
                with redirect_stdout(local_stdout), redirect_stderr(local_stderr):
                    local_rc = MODULE.cmd_chunked_rollout_summary(
                        argparse.Namespace(
                            host="local",
                            rollout=rollout,
                            keyword=[],
                            chunk_bytes=1,
                            limit_per_chunk=20,
                            tail_records=0,
                            max_text_chars=200,
                            **expected_identity,
                        )
                    )
            script = MODULE._remote_python_script(
                {
                    "mode": "chunked-rollout-summary",
                    "rollout": rollout,
                    "codex_root": str(codex_root),
                    "summary_keywords": [],
                    "summary_limit": 20,
                    "summary_tail_records": 0,
                    "summary_max_text_chars": 200,
                    "chunk_bytes": 1,
                    "max_automatic_full_reconstruction_bytes": identity.size - 1,
                    "max_fetch_rollout_chunk_bytes": identity.size,
                    "min_rollout_chunk_bytes": 1,
                    "max_rollout_chunk_bytes": identity.size,
                    "max_chunked_summary_output_bytes": (
                        MODULE.MAX_CHUNKED_ROLLOUT_SUMMARY_OUTPUT_BYTES
                    ),
                    "max_fetch_range_plan_entries": 4,
                    "expected_source_bytes": identity.size,
                    "expected_source_identity": MODULE._rollout_identity_token(
                        identity
                    ),
                    "authorized_source_bytes": identity.size,
                    "output_host": "embedded",
                }
            )
            result = subprocess.run(
                [sys.executable, "-"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(local_rc, 1)
        self.assertEqual(local_stdout.getvalue(), "")
        self.assertIn(f"error={error_text}", local_stderr.getvalue())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            MODULE._extract_framed_lines(
                result.stdout,
                begin_marker=MODULE.REMOTE_CHUNKED_ROLLOUT_SUMMARY_BEGIN,
                end_marker=MODULE.REMOTE_CHUNKED_ROLLOUT_SUMMARY_END,
                host="embedded",
                command="chunked-rollout-summary",
            ),
            [
                json.dumps(
                    {"ok": False, "error": error_text},
                    separators=(",", ":"),
                    sort_keys=True,
                )
            ],
        )

    def test_chunked_summary_counts_implicit_and_explicit_plan_entries(
        self,
    ) -> None:
        user_line = json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Inspect."}],
                },
            },
            separators=(",", ":"),
        )
        assistant_line = json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Done."}],
                },
            },
            separators=(",", ":"),
        )
        chunk_bytes = len((user_line + "\n" + assistant_line + "\n").encode())
        oversized_line = "x" * (chunk_bytes + 1)
        error_text = "fetch range plan too large: 3 ranges > 2"
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(
                codex_root,
                [user_line, assistant_line, oversized_line, oversized_line],
            )
            identity = rollout_identity(codex_root, rollout)
            with (
                mock.patch.object(MODULE, "MIN_ROLLOUT_CHUNK_BYTES", 1),
                mock.patch.object(MODULE, "MAX_FETCH_RANGE_PLAN_ENTRIES", 3),
            ):
                baseline = MODULE._chunked_rollout_summary_records(
                    codex_root=codex_root,
                    rollout_relative_path=MODULE._resolve_rollout_relative_path(
                        rollout
                    ),
                    chunk_bytes=chunk_bytes,
                    keywords=[],
                    limit_per_chunk=20,
                    tail_records=4,
                    max_text_chars=200,
                    host="local",
                    expected_identity=identity,
                    authorized_source_bytes=None,
                )
            baseline_meta = [
                record for record in baseline if record["kind"] == "chunk_meta"
            ]
            self.assertEqual(
                [record["coverage_status"] for record in baseline_meta],
                ["complete", "partial", "partial"],
            )
            self.assertNotIn("fetch_ranges", baseline_meta[0])
            self.assertEqual(
                [record["fetch_range_count"] for record in baseline_meta[1:]],
                [1, 1],
            )
            with (
                mock.patch.object(MODULE, "MIN_ROLLOUT_CHUNK_BYTES", 1),
                mock.patch.object(MODULE, "MAX_FETCH_RANGE_PLAN_ENTRIES", 2),
            ):
                with self.assertRaisesRegex(ValueError, error_text):
                    MODULE._chunked_rollout_summary_records(
                        codex_root=codex_root,
                        rollout_relative_path=(
                            MODULE._resolve_rollout_relative_path(rollout)
                        ),
                        chunk_bytes=chunk_bytes,
                        keywords=[],
                        limit_per_chunk=20,
                        tail_records=4,
                        max_text_chars=200,
                        host="local",
                        expected_identity=identity,
                        authorized_source_bytes=None,
                    )
            payload = {
                "mode": "chunked-rollout-summary",
                "rollout": rollout,
                "codex_root": str(codex_root),
                "summary_keywords": [],
                "summary_limit": 20,
                "summary_tail_records": 4,
                "summary_max_text_chars": 200,
                "chunk_bytes": chunk_bytes,
                "max_automatic_full_reconstruction_bytes": (
                    MODULE.MAX_AUTOMATIC_FULL_RECONSTRUCTION_BYTES
                ),
                "max_fetch_rollout_chunk_bytes": identity.size,
                "min_rollout_chunk_bytes": 1,
                "max_rollout_chunk_bytes": identity.size,
                "max_chunked_summary_output_bytes": (
                    MODULE.MAX_CHUNKED_ROLLOUT_SUMMARY_OUTPUT_BYTES
                ),
                "max_fetch_range_plan_entries": 2,
                "expected_source_bytes": identity.size,
                "expected_source_identity": MODULE._rollout_identity_token(identity),
                "authorized_source_bytes": None,
                "output_host": "embedded",
            }
            result = subprocess.run(
                [sys.executable, "-"],
                input=MODULE._remote_python_script(payload),
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            MODULE._extract_framed_lines(
                result.stdout,
                begin_marker=MODULE.REMOTE_CHUNKED_ROLLOUT_SUMMARY_BEGIN,
                end_marker=MODULE.REMOTE_CHUNKED_ROLLOUT_SUMMARY_END,
                host="embedded",
                command="chunked-rollout-summary",
            ),
            [
                json.dumps(
                    {"ok": False, "error": error_text},
                    separators=(",", ":"),
                    sort_keys=True,
                )
            ],
        )

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
            rollout_path = codex_root / rollout
            rollout_path.write_bytes(
                rollout_path.read_bytes()
                + b'{"type":"response_item","payload":"'
                + b"y" * 240
                + b'\xff"}\n'
                + b'"'
                + b"a" * 59
                + "é".encode()
                + b"b" * 100
                + b'"\n'
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
            script = MODULE._remote_python_script(
                {
                    "mode": "chunked-rollout-summary",
                    "rollout": rollout,
                    "codex_root": str(codex_root),
                    "summary_keywords": [],
                    "summary_limit": 20,
                    "summary_tail_records": 4,
                    "summary_max_text_chars": 200,
                    "chunk_bytes": 60,
                    "max_automatic_full_reconstruction_bytes": (
                        MODULE.MAX_AUTOMATIC_FULL_RECONSTRUCTION_BYTES
                    ),
                    "max_fetch_rollout_chunk_bytes": 80,
                    "min_rollout_chunk_bytes": 1,
                    "max_rollout_chunk_bytes": identity.size,
                    "max_chunked_summary_output_bytes": (
                        MODULE.MAX_CHUNKED_ROLLOUT_SUMMARY_OUTPUT_BYTES
                    ),
                    "max_fetch_range_plan_entries": (
                        MODULE.MAX_FETCH_RANGE_PLAN_ENTRIES
                    ),
                    "expected_source_bytes": identity.size,
                    "expected_source_identity": MODULE._rollout_identity_token(
                        identity
                    ),
                    "authorized_source_bytes": None,
                    "output_host": "embedded",
                }
            )
            embedded_result = subprocess.run(
                [sys.executable, "-"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(rc, 0)
        records = [json.loads(line) for line in buffer.getvalue().splitlines()]
        local_oversized = [
            record
            for record in records
            if record["kind"] == "chunk_meta"
            and "oversized_record" in record["reason_codes"]
        ]
        self.assertEqual(embedded_result.returncode, 0, embedded_result.stderr)
        embedded_records = [
            json.loads(line)
            for line in MODULE._extract_framed_lines(
                embedded_result.stdout,
                begin_marker=MODULE.REMOTE_CHUNKED_ROLLOUT_SUMMARY_BEGIN,
                end_marker=MODULE.REMOTE_CHUNKED_ROLLOUT_SUMMARY_END,
                host="embedded",
                command="chunked-rollout-summary",
            )
            if "\"kind\"" in line
        ]
        embedded_oversized = [
            record
            for record in embedded_records
            if record["kind"] == "chunk_meta"
            and "oversized_record" in record["reason_codes"]
        ]
        for oversized_records in (local_oversized, embedded_oversized):
            self.assertEqual(len(oversized_records), 3)
            valid_record, invalid_record, split_utf8_record = oversized_records
            for record in (valid_record, split_utf8_record):
                self.assertEqual(record["decode_error_count"], 0)
                self.assertEqual(record["json_error_count"], 0)
                self.assertNotIn("utf8_decode_error", record["reason_codes"])
                self.assertNotIn("json_parse_error", record["reason_codes"])
            self.assertEqual(invalid_record["decode_error_count"], 1)
            self.assertEqual(invalid_record["json_error_count"], 1)
            self.assertIn("utf8_decode_error", invalid_record["reason_codes"])
            self.assertIn("json_parse_error", invalid_record["reason_codes"])
            for oversized in oversized_records:
                self.assertTrue(oversized["raw_fetch_recommended"])
                self.assertGreater(oversized["fetch_range_count"], 1)
                self.assertEqual(
                    oversized["fetch_ranges"][0]["byte_start"],
                    oversized["byte_start"],
                )
                self.assertEqual(
                    oversized["fetch_ranges"][-1]["byte_end"],
                    oversized["byte_end"],
                )
                self.assertTrue(
                    all(
                        item["byte_end"] - item["byte_start"]
                        <= oversized["fetch_chunk_bytes"]
                        for item in oversized["fetch_ranges"]
                    )
                )

    def test_fetch_range_plan_rejects_huge_count_before_allocation(self) -> None:
        with mock.patch.object(MODULE, "MAX_FETCH_RANGE_PLAN_ENTRIES", 4):
            self.assertEqual(
                MODULE._fetch_ranges_for_byte_range(
                    byte_start=0,
                    byte_end=1,
                    max_bytes=1,
                    plan_entries_used=3,
                ),
                [{"range_index": 0, "byte_start": 0, "byte_end": 1}],
            )
            with self.assertRaisesRegex(
                ValueError,
                "fetch range plan too large: 5 ranges > 4",
            ):
                MODULE._fetch_ranges_for_byte_range(
                    byte_start=0,
                    byte_end=1,
                    max_bytes=1,
                    plan_entries_used=4,
                )
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
                    {MODULE.MAX_AUTOMATIC_FULL_RECONSTRUCTION_BYTES},
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
                self.assertLessEqual(
                    planned_bytes,
                    MODULE.MAX_AUTOMATIC_FULL_RECONSTRUCTION_BYTES,
                )

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
                mock.patch.object(
                    MODULE,
                    "MAX_AUTOMATIC_FULL_RECONSTRUCTION_BYTES",
                    identity.size - 1,
                ),
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
                            max_bytes=MODULE.MAX_DIRECT_FETCH_ROLLOUT_BYTES,
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

    def test_rollout_summary_counts_non_object_schemas_and_keeps_later_evidence(
        self,
    ) -> None:
        user_timestamp = "2026-05-26T10:01:00Z"
        assistant_timestamp = "2026-05-26T10:02:00Z"
        malformed_schemas = [
            "0",
            "[]",
            "null",
            *pathological_json_lines(),
            '{"type":"event_msg","payload":[]}',
        ]
        valid_lines = [
            json.dumps(
                {
                    "timestamp": user_timestamp,
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "Inspect this."}
                        ],
                    },
                },
                separators=(",", ":"),
            ),
            json.dumps(
                {
                    "timestamp": assistant_timestamp,
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "Completed."}
                        ],
                    },
                },
                separators=(",", ":"),
            ),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = write_rollout(
                codex_root,
                [*malformed_schemas, *valid_lines],
            )
            local_stdout = io.StringIO()
            local_stderr = io.StringIO()
            with (
                mock.patch.object(MODULE, "_local_codex_root", return_value=codex_root),
                redirect_stdout(local_stdout),
                redirect_stderr(local_stderr),
            ):
                local_rc = MODULE.cmd_rollout_summary(
                    argparse.Namespace(
                        host="local",
                        rollout=rollout,
                        keyword=[],
                        limit=20,
                        tail_records=4,
                        max_text_chars=200,
                    )
                )

            script = MODULE._remote_python_script(
                {
                    "mode": "rollout-summary",
                    "rollout": rollout,
                    "codex_root": str(codex_root),
                    "summary_keywords": [],
                    "summary_limit": 20,
                    "summary_scan_bytes": MODULE.MAX_ROLLOUT_SUMMARY_SCAN_BYTES,
                    "summary_line_bytes": MODULE.MAX_ROLLOUT_SUMMARY_LINE_BYTES,
                    "summary_tail_records": 4,
                    "summary_max_text_chars": 200,
                }
            )
            embedded = subprocess.run(
                [sys.executable, "-"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(local_rc, 0, local_stderr.getvalue())
        self.assertEqual(embedded.returncode, 0, embedded.stderr)
        local_records = [
            json.loads(line) for line in local_stdout.getvalue().splitlines()
        ]
        embedded_records = MODULE._extract_framed_rollout_summary_records(
            embedded.stdout,
            begin_marker=MODULE.REMOTE_ROLLOUT_SUMMARY_BEGIN,
            end_marker=MODULE.REMOTE_ROLLOUT_SUMMARY_END,
            host="embedded",
            command="rollout-summary",
        )
        for records in (local_records, embedded_records):
            scan_meta = next(
                record for record in records if record["kind"] == "scan_meta"
            )
            self.assertEqual(scan_meta["json_error_count"], len(malformed_schemas))
            evidence = [
                record
                for record in records
                if record["kind"] in {"user_message", "assistant_message"}
            ]
            self.assertEqual(
                [record["kind"] for record in evidence],
                ["user_message", "assistant_message"],
            )
            self.assertEqual(
                [record["timestamp"] for record in evidence],
                [user_timestamp, assistant_timestamp],
            )

    def test_local_and_embedded_rollout_summary_match_json_error_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout_ref = (
                "sessions/2026/05/26/"
                "rollout-2026-05-26T10-00-00-malformed.jsonl"
            )
            rollout = codex_root / rollout_ref
            rollout.parent.mkdir(parents=True, exist_ok=True)
            prefix = json.dumps(
                {
                    "type": "response_item",
                    "payload": {"type": "message", "role": "assistant"},
                }
            ).encode("utf-8")
            suffix = json.dumps(
                {
                    "type": "response_item",
                    "payload": {"type": "message", "role": "user"},
                }
            ).encode("utf-8")
            rollout.write_bytes(prefix + b"\r" + suffix + b"\n")

            local_stdout = io.StringIO()
            with mock.patch.object(
                MODULE,
                "_local_codex_root",
                return_value=codex_root,
            ), redirect_stdout(local_stdout):
                local_rc = MODULE.cmd_rollout_summary(
                    argparse.Namespace(
                        host="local",
                        rollout=rollout_ref,
                        keyword=[],
                        limit=20,
                        tail_records=0,
                        max_text_chars=80,
                    )
                )

            script = MODULE._remote_python_script(
                {
                    "mode": "rollout-summary",
                    "rollout": rollout_ref,
                    "codex_root": str(codex_root),
                    "summary_keywords": [],
                    "summary_limit": 20,
                    "summary_scan_bytes": MODULE.MAX_ROLLOUT_SUMMARY_SCAN_BYTES,
                    "summary_line_bytes": MODULE.MAX_ROLLOUT_SUMMARY_LINE_BYTES,
                    "summary_tail_records": 0,
                    "summary_max_text_chars": 80,
                }
            )
            embedded = subprocess.run(
                [sys.executable, "-"],
                input=script,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(local_rc, 0)
        self.assertEqual(embedded.returncode, 0, embedded.stderr)
        local_records = [
            json.loads(line) for line in local_stdout.getvalue().splitlines()
        ]
        embedded_records = MODULE._extract_framed_rollout_summary_records(
            embedded.stdout,
            begin_marker=MODULE.REMOTE_ROLLOUT_SUMMARY_BEGIN,
            end_marker=MODULE.REMOTE_ROLLOUT_SUMMARY_END,
            host="embedded",
            command="rollout-summary",
        )
        local_meta = next(
            record for record in local_records if record.get("kind") == "scan_meta"
        )
        embedded_meta = next(
            record
            for record in embedded_records
            if record.get("kind") == "scan_meta"
        )
        fields = (
            "json_error_count",
            "scan_bytes",
            "scan_truncated",
            "source_bytes",
        )
        self.assertEqual(
            {field: local_meta[field] for field in fields},
            {field: embedded_meta[field] for field in fields},
        )
        self.assertEqual(local_meta["json_error_count"], 1)

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


class RemoteCodexProbeTerminalTailTests(unittest.TestCase):
    @staticmethod
    def _jsonl_record(record: dict[str, object]) -> bytes:
        return (
            json.dumps(
                record,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )

    @classmethod
    def _task_complete(cls, message: str) -> bytes:
        return cls._jsonl_record(
            {
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "last_agent_message": message,
                },
            }
        )

    @classmethod
    def _event_user_message(cls, message: str) -> bytes:
        return cls._jsonl_record(
            {
                "type": "event_msg",
                "payload": {
                    "type": "user_message",
                    "message": message,
                },
            }
        )

    @classmethod
    def _response_user_message(cls, message: str) -> bytes:
        return cls._jsonl_record(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": message}],
                },
            }
        )

    @staticmethod
    def _write_rollout_bytes(codex_root: Path, data: bytes) -> str:
        rollout_dir = codex_root / "sessions/2026/07/23"
        rollout_dir.mkdir(parents=True)
        rollout = (
            rollout_dir
            / "rollout-2026-07-23T10-00-00-terminal-tail.jsonl"
        )
        rollout.write_bytes(data)
        return rollout.relative_to(codex_root).as_posix()

    @staticmethod
    def _remote_terminal_tail_header(
        *,
        status: str = "complete",
        message: bytes | None = b"remote exact bytes",
    ) -> dict[str, object]:
        return {
            "ok": True,
            "status": status,
            "bytes": len(message or b""),
            "source_bytes": 100,
            "observed_source_bytes": 100,
            "scan_start": 0,
            "scan_end": 100,
            "scanned_bytes": 100,
            "window_count": 1,
            "anchor_offset": 32,
            "anchor_length": 32,
            "append_observed": False,
            "terminal_record_offset": 64,
        }

    @staticmethod
    def _remote_terminal_tail_frame(
        header: dict[str, object],
        *,
        message: bytes | None = None,
    ) -> str:
        lines = [
            MODULE.REMOTE_TERMINAL_TAIL_BEGIN,
            json.dumps(header, separators=(",", ":"), sort_keys=True),
        ]
        if message:
            lines.append(MODULE.base64.b64encode(message).decode("ascii"))
        lines.extend([MODULE.REMOTE_TERMINAL_TAIL_END, ""])
        return "\n".join(lines)

    def _run_remote_terminal_tail_fixture(
        self,
        remote_result: subprocess.CompletedProcess[str] | Exception,
    ) -> tuple[int, str, str, bytes]:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            output_path = Path(temp_dir) / "terminal-result.txt"
            output_path.write_bytes(b"sentinel")
            stdout = io.StringIO()
            stderr = io.StringIO()
            if isinstance(remote_result, Exception):
                remote_mock = mock.patch.object(
                    MODULE,
                    "_run_remote_python_bounded",
                    side_effect=remote_result,
                )
            else:
                remote_mock = mock.patch.object(
                    MODULE,
                    "_run_remote_python_bounded",
                    return_value=remote_result,
                )
            with remote_mock, redirect_stdout(stdout), redirect_stderr(stderr):
                rc = MODULE.cmd_terminal_tail(
                    argparse.Namespace(
                        host="miku-bot-dev",
                        rollout=(
                            "sessions/2026/07/23/"
                            "rollout-2026-07-23T10-00-00-remote.jsonl"
                        ),
                        output=str(output_path),
                    )
                )
            output_bytes = output_path.read_bytes()
        return rc, stdout.getvalue(), stderr.getvalue(), output_bytes

    def test_rollout_budgets_split_direct_fetch_from_automatic_reconstruction(
        self,
    ) -> None:
        self.assertEqual(MODULE.MAX_DIRECT_FETCH_ROLLOUT_BYTES, 16 * 1024 * 1024)
        self.assertEqual(
            MODULE.MAX_AUTOMATIC_FULL_RECONSTRUCTION_BYTES,
            128 * 1024 * 1024,
        )
        self.assertEqual(MODULE.MAX_TERMINAL_TAIL_SCAN_BYTES, 128 * 1024 * 1024)
        self.assertEqual(
            MODULE.DEFAULT_TERMINAL_TAIL_WINDOW_BYTES,
            4 * 1024 * 1024,
        )
        self.assertEqual(MODULE.MAX_TERMINAL_TAIL_RECORD_BYTES, 16 * 1024 * 1024)
        self.assertEqual(
            MODULE.MAX_REMOTE_FETCH_ROLLOUT_STDOUT_BYTES,
            4 * ((MODULE.MAX_DIRECT_FETCH_ROLLOUT_BYTES + 2) // 3)
            + MODULE.REMOTE_FETCH_ROLLOUT_FRAME_OVERHEAD_BYTES,
        )

        identity = MODULE.RolloutIdentity(
            MODULE.MAX_DIRECT_FETCH_ROLLOUT_BYTES + 1,
            1,
            2,
            3,
            4,
        )
        self.assertFalse(MODULE._validate_source_read_budget(identity, None))
        record = MODULE._rollout_identity_record(identity)
        self.assertEqual(
            record["full_fetch_limit_bytes"],
            MODULE.MAX_AUTOMATIC_FULL_RECONSTRUCTION_BYTES,
        )
        self.assertEqual(
            record["automatic_full_reconstruction_limit_bytes"],
            MODULE.MAX_AUTOMATIC_FULL_RECONSTRUCTION_BYTES,
        )
        self.assertEqual(
            record["direct_fetch_limit_bytes"],
            MODULE.MAX_DIRECT_FETCH_ROLLOUT_BYTES,
        )
        self.assertEqual(
            record["terminal_tail_scan_limit_bytes"],
            MODULE.MAX_TERMINAL_TAIL_SCAN_BYTES,
        )
        self.assertTrue(record["automatic_full_reconstruction_allowed"])

    def test_direct_fetch_keeps_its_independent_small_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = self._write_rollout_bytes(codex_root, b"x" * 9)
            with mock.patch.object(
                MODULE,
                "MAX_DIRECT_FETCH_ROLLOUT_BYTES",
                8,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "(?:exceeds 8-byte limit|too large: 9 bytes > 8)",
                ):
                    MODULE._fetch_local_rollout(
                        codex_root,
                        MODULE._resolve_rollout_relative_path(rollout),
                    )

    def test_complete_tail_reads_multiple_absolute_windows_and_preserves_bytes(
        self,
    ) -> None:
        message = "  result\r\n雪\x00tail  "
        prefix = b"".join(
            self._jsonl_record({"type": "noise", "payload": {"index": index}})
            for index in range(8)
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = self._write_rollout_bytes(
                codex_root,
                prefix + self._task_complete(message),
            )
            source_bytes = (codex_root / rollout).stat().st_size
            pread_ranges: list[tuple[int, int]] = []
            real_pread_exact = MODULE._pread_exact

            def record_pread(fd: int, offset: int, length: int) -> bytes:
                pread_ranges.append((offset, length))
                return real_pread_exact(fd, offset, length)

            with mock.patch.object(
                MODULE,
                "_pread_exact",
                side_effect=record_pread,
            ):
                result = MODULE._read_terminal_tail(
                    codex_root,
                    MODULE._resolve_rollout_relative_path(rollout),
                    window_bytes=64,
                    max_scan_bytes=4096,
                    max_record_bytes=1024,
                )

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.message, message.encode("utf-8"))
        self.assertGreater(result.window_count, 1)
        self.assertEqual(result.scan_end, result.source_bytes)
        self.assertEqual(result.scanned_bytes, result.scan_end - result.scan_start)
        self.assertIsNotNone(result.anchor_offset)
        self.assertGreater(result.anchor_length, 0)
        window_offsets = [
            offset
            for offset, length in pread_ranges
            if length == 64 and (source_bytes - offset) % 64 == 0
        ]
        self.assertGreaterEqual(len(window_offsets), 2)
        self.assertEqual(
            window_offsets,
            [
                source_bytes - 64 * index
                for index in range(1, len(window_offsets) + 1)
            ],
        )

    def test_trailing_partial_record_takes_precedence_over_older_completion(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = self._write_rollout_bytes(
                codex_root,
                self._task_complete("stale result") + b'{"type":"event_msg"',
            )
            result = MODULE._read_terminal_tail(
                codex_root,
                MODULE._resolve_rollout_relative_path(rollout),
                window_bytes=64,
                max_scan_bytes=1024,
                max_record_bytes=512,
            )

        self.assertEqual(result.status, "source_in_progress")
        self.assertIsNone(result.message)

    def test_latest_complete_record_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = self._write_rollout_bytes(
                codex_root,
                self._task_complete("older")
                + self._jsonl_record({"type": "noise", "payload": "between"})
                + self._task_complete("newer"),
            )
            result = MODULE._read_terminal_tail(
                codex_root,
                MODULE._resolve_rollout_relative_path(rollout),
                window_bytes=64,
                max_scan_bytes=2048,
                max_record_bytes=1024,
            )

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.message, b"newer")

    def test_later_user_turn_makes_older_completion_non_terminal(self) -> None:
        later_turns = {
            "event_msg": self._event_user_message("continue"),
            "response_item": self._response_user_message("continue"),
        }
        for shape, later_turn in later_turns.items():
            with (
                self.subTest(shape=shape),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                codex_root = Path(temp_dir) / ".codex"
                rollout = self._write_rollout_bytes(
                    codex_root,
                    self._task_complete("stale result") + later_turn,
                )
                result = MODULE._read_terminal_tail(
                    codex_root,
                    MODULE._resolve_rollout_relative_path(rollout),
                    window_bytes=64,
                    max_scan_bytes=2048,
                    max_record_bytes=1024,
                )

            self.assertEqual(result.status, "terminal_not_reached")
            self.assertIsNone(result.message)

    def test_scan_budget_exhaustion_is_explicit(self) -> None:
        data = b"".join(
            self._jsonl_record(
                {"type": "noise", "payload": {"index": index, "text": "x" * 40}}
            )
            for index in range(12)
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = self._write_rollout_bytes(codex_root, data)
            result = MODULE._read_terminal_tail(
                codex_root,
                MODULE._resolve_rollout_relative_path(rollout),
                window_bytes=64,
                max_scan_bytes=128,
                max_record_bytes=1024,
            )

        self.assertEqual(result.status, "tail_window_insufficient")
        self.assertIsNone(result.message)
        self.assertEqual(result.scanned_bytes, 128)

    def test_complete_record_over_single_record_cap_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = self._write_rollout_bytes(
                codex_root,
                self._task_complete("x" * 256),
            )
            with self.assertRaisesRegex(
                ValueError,
                "record.*(?:too large|limit)|(?:too large|limit).*record",
            ):
                MODULE._read_terminal_tail(
                    codex_root,
                    MODULE._resolve_rollout_relative_path(rollout),
                    window_bytes=512,
                    max_scan_bytes=1024,
                    max_record_bytes=64,
                )

    def test_complete_malformed_jsonl_record_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = self._write_rollout_bytes(
                codex_root,
                self._task_complete("must not escape") + b"{not-json}\n",
            )
            with self.assertRaisesRegex(ValueError, "malformed|JSON"):
                MODULE._read_terminal_tail(
                    codex_root,
                    MODULE._resolve_rollout_relative_path(rollout),
                    window_bytes=64,
                    max_scan_bytes=1024,
                    max_record_bytes=512,
                )

    def test_nonfinite_json_constants_fail_closed_locally_and_embedded(
        self,
    ) -> None:
        for constant in ("NaN", "Infinity", "-Infinity"):
            with (
                self.subTest(constant=constant),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                codex_root = Path(temp_dir) / ".codex"
                record = (
                    b'{"type":"event_msg","payload":{"type":"task_complete",'
                    b'"last_agent_message":"must not escape"},"nonfinite":'
                    + constant.encode("ascii")
                    + b"}\n"
                )
                rollout = self._write_rollout_bytes(codex_root, record)
                relative_path = MODULE._resolve_rollout_relative_path(rollout)
                with self.assertRaisesRegex(ValueError, "malformed JSONL"):
                    MODULE._read_terminal_tail(
                        codex_root,
                        relative_path,
                        window_bytes=256,
                        max_scan_bytes=1024,
                        max_record_bytes=1024,
                    )

                script = MODULE._remote_python_script(
                    {
                        "mode": "terminal-tail",
                        "rollout": rollout,
                        "codex_root": str(codex_root),
                        "terminal_tail_window_bytes": 256,
                        "max_terminal_tail_scan_bytes": 1024,
                        "max_terminal_tail_record_bytes": 1024,
                        "max_terminal_tail_anchor_bytes": (
                            MODULE.MAX_TERMINAL_TAIL_ANCHOR_BYTES
                        ),
                        "min_terminal_tail_anchor_bytes": (
                            MODULE.MIN_TERMINAL_TAIL_ANCHOR_BYTES
                        ),
                    }
                )
                embedded = subprocess.run(
                    [sys.executable, "-"],
                    input=script,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            self.assertEqual(embedded.returncode, 0, embedded.stderr)
            lines = MODULE._extract_framed_lines(
                embedded.stdout,
                begin_marker=MODULE.REMOTE_TERMINAL_TAIL_BEGIN,
                end_marker=MODULE.REMOTE_TERMINAL_TAIL_END,
                host="embedded",
                command="terminal-tail",
            )
            self.assertEqual(len(lines), 1)
            header = json.loads(lines[0])
            self.assertFalse(header["ok"])
            self.assertIn("malformed JSONL", header["error"])

    def test_append_after_s0_does_not_move_frozen_tail_coordinates(self) -> None:
        message = "frozen result"
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = self._write_rollout_bytes(
                codex_root,
                self._jsonl_record({"type": "noise", "payload": "prefix"})
                + self._task_complete(message),
            )
            rollout_path = codex_root / rollout
            real_pread = MODULE.os.pread
            mutated = False

            def append_after_first_read(fd: int, size: int, offset: int) -> bytes:
                nonlocal mutated
                data = real_pread(fd, size, offset)
                if not mutated and size > 1:
                    mutated = True
                    with rollout_path.open("ab") as handle:
                        handle.write(self._event_user_message("later append"))
                return data

            with mock.patch.object(
                MODULE.os,
                "pread",
                side_effect=append_after_first_read,
            ):
                result = MODULE._read_terminal_tail(
                    codex_root,
                    MODULE._resolve_rollout_relative_path(rollout),
                    window_bytes=256,
                    max_scan_bytes=1024,
                    max_record_bytes=512,
                )

        self.assertTrue(mutated)
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.message, message.encode("utf-8"))
        self.assertTrue(result.append_observed)
        self.assertGreater(result.observed_source_bytes, result.source_bytes)

    def test_append_may_repeat_anchor_without_changing_frozen_coordinates(
        self,
    ) -> None:
        message = "frozen result " + "".join(
            f"{index:04x}-" for index in range(96)
        )
        source = (
            self._jsonl_record({"type": "noise", "payload": "prefix"})
            + self._task_complete(message)
        )
        window_bytes = 512
        window_start = max(0, len(source) - window_bytes)
        anchor = MODULE._select_terminal_tail_anchor(
            source[window_start:],
            window_start=window_start,
        )
        self.assertIsNotNone(anchor)
        assert anchor is not None
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = self._write_rollout_bytes(codex_root, source)
            rollout_path = codex_root / rollout
            real_pread = MODULE.os.pread
            mutated = False

            def append_duplicate_anchor(
                fd: int,
                size: int,
                offset: int,
            ) -> bytes:
                nonlocal mutated
                data = real_pread(fd, size, offset)
                if not mutated and size > 1:
                    mutated = True
                    with rollout_path.open("ab") as handle:
                        handle.write(anchor.data + b"\n")
                return data

            with mock.patch.object(
                MODULE.os,
                "pread",
                side_effect=append_duplicate_anchor,
            ):
                result = MODULE._read_terminal_tail(
                    codex_root,
                    MODULE._resolve_rollout_relative_path(rollout),
                    window_bytes=window_bytes,
                    max_scan_bytes=2048,
                    max_record_bytes=1024,
                )

        self.assertTrue(mutated)
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.message, message.encode("utf-8"))
        self.assertTrue(result.append_observed)

    def test_same_length_prefix_overwrite_does_not_invalidate_tail(self) -> None:
        message = "stable coordinates " + ("z" * 256)
        prefix = self._jsonl_record(
            {"type": "noise", "payload": {"text": "a" * 64}}
        )
        replacement_prefix = self._jsonl_record(
            {"type": "noise", "payload": {"text": "b" * 64}}
        )
        self.assertEqual(len(prefix), len(replacement_prefix))
        middle = b"".join(
            self._jsonl_record(
                {"type": "noise", "payload": {"index": index, "text": "m" * 32}}
            )
            for index in range(4)
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = self._write_rollout_bytes(
                codex_root,
                prefix + middle + self._task_complete(message),
            )
            rollout_path = codex_root / rollout
            real_pread = MODULE.os.pread
            mutated = False

            def overwrite_after_first_read(
                fd: int,
                size: int,
                offset: int,
            ) -> bytes:
                nonlocal mutated
                data = real_pread(fd, size, offset)
                if not mutated and size > 1:
                    mutated = True
                    with rollout_path.open("r+b") as handle:
                        handle.write(replacement_prefix)
                return data

            with mock.patch.object(
                MODULE.os,
                "pread",
                side_effect=overwrite_after_first_read,
            ):
                result = MODULE._read_terminal_tail(
                    codex_root,
                    MODULE._resolve_rollout_relative_path(rollout),
                    window_bytes=512,
                    max_scan_bytes=2048,
                    max_record_bytes=1024,
                )

        self.assertTrue(mutated)
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.message, message.encode("utf-8"))
        self.assertFalse(result.append_observed)

    def test_prefix_insertion_reports_anchor_moved_without_relocation(self) -> None:
        message = "must not relocate"
        original = (
            self._jsonl_record({"type": "noise", "payload": "prefix"})
            + self._task_complete(message)
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = self._write_rollout_bytes(codex_root, original)
            rollout_path = codex_root / rollout
            real_pread = MODULE.os.pread
            pread_calls = 0

            def insert_after_first_read(fd: int, size: int, offset: int) -> bytes:
                nonlocal pread_calls
                data = real_pread(fd, size, offset)
                if size > 1:
                    pread_calls += 1
                if pread_calls == 1 and size > 1:
                    with rollout_path.open("r+b") as handle:
                        handle.write(b"x" + original)
                return data

            with mock.patch.object(
                MODULE.os,
                "pread",
                side_effect=insert_after_first_read,
            ):
                result = MODULE._read_terminal_tail(
                    codex_root,
                    MODULE._resolve_rollout_relative_path(rollout),
                    window_bytes=256,
                    max_scan_bytes=1024,
                    max_record_bytes=512,
                )

        self.assertEqual(result.status, "anchor_moved")
        self.assertIsNone(result.message)
        self.assertGreaterEqual(pread_calls, 2)
        self.assertLessEqual(pread_calls, 3)

    def test_truncation_and_path_replacement_fail_closed(self) -> None:
        original = (
            self._jsonl_record({"type": "noise", "payload": "prefix"})
            + self._task_complete("result")
        )
        for mutation in ("truncate", "replace"):
            with (
                self.subTest(mutation=mutation),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                codex_root = Path(temp_dir) / ".codex"
                rollout = self._write_rollout_bytes(codex_root, original)
                rollout_path = codex_root / rollout
                real_pread = MODULE.os.pread
                mutated = False

                def mutate_after_first_window(
                    fd: int,
                    size: int,
                    offset: int,
                ) -> bytes:
                    nonlocal mutated
                    data = real_pread(fd, size, offset)
                    if not mutated and size > 1:
                        mutated = True
                        if mutation == "truncate":
                            with rollout_path.open("r+b") as handle:
                                handle.truncate(len(original) - 1)
                        else:
                            replacement = rollout_path.with_suffix(".replacement")
                            replacement.write_bytes(original)
                            os.replace(replacement, rollout_path)
                    return data

                with (
                    mock.patch.object(
                        MODULE.os,
                        "pread",
                        side_effect=mutate_after_first_window,
                    ),
                    self.assertRaisesRegex(
                        ValueError,
                        (
                            "truncated below frozen EOF"
                            if mutation == "truncate"
                            else "path replaced"
                        ),
                    ),
                ):
                    MODULE._read_terminal_tail(
                        codex_root,
                        MODULE._resolve_rollout_relative_path(rollout),
                        window_bytes=256,
                        max_scan_bytes=1024,
                        max_record_bytes=512,
                    )

                self.assertTrue(mutated)

    def test_local_and_embedded_terminal_tail_match(self) -> None:
        cases = {
            "complete": self._task_complete("remote parity"),
            "source_in_progress": (
                self._task_complete("stale") + b'{"type":"event_msg"'
            ),
            "terminal_not_reached": (
                self._task_complete("stale")
                + self._event_user_message("continue")
            ),
            "anchor_unavailable": b"{}\n",
        }
        for expected_status, data in cases.items():
            with (
                self.subTest(status=expected_status),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                codex_root = Path(temp_dir) / ".codex"
                rollout = self._write_rollout_bytes(codex_root, data)
                local = MODULE._read_terminal_tail(
                    codex_root,
                    MODULE._resolve_rollout_relative_path(rollout),
                    window_bytes=64,
                    max_scan_bytes=2048,
                    max_record_bytes=1024,
                )
                script = MODULE._remote_python_script(
                    {
                        "mode": "terminal-tail",
                        "rollout": rollout,
                        "codex_root": str(codex_root),
                        "terminal_tail_window_bytes": 64,
                        "max_terminal_tail_scan_bytes": 2048,
                        "max_terminal_tail_record_bytes": 1024,
                        "max_terminal_tail_anchor_bytes": (
                            MODULE.MAX_TERMINAL_TAIL_ANCHOR_BYTES
                        ),
                        "min_terminal_tail_anchor_bytes": (
                            MODULE.MIN_TERMINAL_TAIL_ANCHOR_BYTES
                        ),
                    }
                )
                embedded = subprocess.run(
                    [sys.executable, "-"],
                    input=script,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            self.assertEqual(embedded.returncode, 0, embedded.stderr)
            lines = MODULE._extract_framed_lines(
                embedded.stdout,
                begin_marker=MODULE.REMOTE_TERMINAL_TAIL_BEGIN,
                end_marker=MODULE.REMOTE_TERMINAL_TAIL_END,
                host="embedded",
                command="terminal-tail",
            )
            self.assertGreaterEqual(len(lines), 1)
            header = json.loads(lines[0])
            self.assertTrue(header["ok"])
            self.assertEqual(header["status"], expected_status)
            self.assertEqual(local.status, expected_status)
            fields = (
                "source_bytes",
                "observed_source_bytes",
                "scan_start",
                "scan_end",
                "scanned_bytes",
                "window_count",
                "anchor_offset",
                "anchor_length",
                "append_observed",
                "terminal_record_offset",
            )
            self.assertEqual(
                {field: getattr(local, field) for field in fields},
                {field: header[field] for field in fields},
            )
            if expected_status == "complete":
                self.assertEqual(len(lines), 2)
                self.assertEqual(
                    MODULE.base64.b64decode(lines[1], validate=True),
                    local.message,
                )
            else:
                self.assertEqual(len(lines), 1)
                self.assertIsNone(local.message)
            if expected_status == "anchor_unavailable":
                self.assertLess(
                    local.source_bytes - 1,
                    MODULE.MIN_TERMINAL_TAIL_ANCHOR_BYTES,
                )
                self.assertIsNone(local.anchor_offset)
                self.assertEqual(local.anchor_length, 0)

    def test_local_cli_writes_exact_message_without_echoing_it(self) -> None:
        message = "  exact\r\n终端\x00bytes  "
        expected = message.encode("utf-8")
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = self._write_rollout_bytes(
                codex_root,
                self._task_complete(message),
            )
            output_path = Path(temp_dir) / "terminal-result.txt"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.object(MODULE, "_local_codex_root", return_value=codex_root),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                rc = MODULE.cmd_terminal_tail(
                    argparse.Namespace(
                        host="local",
                        rollout=rollout,
                        output=str(output_path),
                    )
                )

            self.assertEqual(output_path.read_bytes(), expected)

        self.assertEqual(rc, 0, stderr.getvalue())
        self.assertNotIn(message, stdout.getvalue())
        self.assertNotIn(message, stderr.getvalue())
        self.assertIn("status=complete", stdout.getvalue())
        self.assertIn(f"bytes={len(expected)}", stdout.getvalue())

    def test_local_cli_nonterminal_status_does_not_touch_output(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            codex_root = Path(temp_dir) / ".codex"
            rollout = self._write_rollout_bytes(
                codex_root,
                self._task_complete("stale") + self._event_user_message("continue"),
            )
            output_path = Path(temp_dir) / "must-not-exist.txt"
            output_path.write_bytes(b"sentinel")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.object(MODULE, "_local_codex_root", return_value=codex_root),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                rc = MODULE.cmd_terminal_tail(
                    argparse.Namespace(
                        host="local",
                        rollout=rollout,
                        output=str(output_path),
                    )
                )

            self.assertEqual(output_path.read_bytes(), b"sentinel")

        self.assertEqual(rc, 1)
        self.assertIn("status=terminal_not_reached", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

    def test_remote_terminal_tail_uses_bounded_capture_and_preserves_output(
        self,
    ) -> None:
        for status, message, expected_rc in (
            ("complete", b"remote exact\nbytes", 0),
            ("terminal_not_reached", None, 1),
        ):
            with (
                self.subTest(status=status),
                tempfile.TemporaryDirectory(dir="/tmp") as temp_dir,
            ):
                header = {
                    "ok": True,
                    "status": status,
                    "bytes": len(message or b""),
                    "source_bytes": 100,
                    "observed_source_bytes": 100,
                    "scan_start": 0,
                    "scan_end": 100,
                    "scanned_bytes": 100,
                    "window_count": 1,
                    "anchor_offset": 32,
                    "anchor_length": 32,
                    "append_observed": False,
                    "terminal_record_offset": 64,
                }
                frame = [
                    MODULE.REMOTE_TERMINAL_TAIL_BEGIN,
                    json.dumps(header, separators=(",", ":"), sort_keys=True),
                ]
                if message is not None:
                    frame.append(MODULE.base64.b64encode(message).decode("ascii"))
                frame.extend([MODULE.REMOTE_TERMINAL_TAIL_END, ""])
                remote_result = subprocess.CompletedProcess(
                    args=["ssh"],
                    returncode=0,
                    stdout="\n".join(frame),
                    stderr="",
                )
                output_path = Path(temp_dir) / "terminal-result.txt"
                if message is None:
                    output_path.write_bytes(b"sentinel")
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    mock.patch.object(
                        MODULE,
                        "_run_remote_python_bounded",
                        return_value=remote_result,
                    ) as bounded_run,
                    redirect_stdout(stdout),
                    redirect_stderr(stderr),
                ):
                    rc = MODULE.cmd_terminal_tail(
                        argparse.Namespace(
                            host="miku-bot-dev",
                            rollout=(
                                "sessions/2026/07/23/"
                                "rollout-2026-07-23T10-00-00-remote.jsonl"
                            ),
                            output=str(output_path),
                        )
                    )

                self.assertEqual(rc, expected_rc, stderr.getvalue())
                bounded_run.assert_called_once()
                self.assertEqual(
                    bounded_run.call_args.kwargs["max_stdout_bytes"],
                    MODULE.MAX_REMOTE_TERMINAL_TAIL_STDOUT_BYTES,
                )
                payload = bounded_run.call_args.args[1]
                self.assertEqual(payload["mode"], "terminal-tail")
                self.assertEqual(
                    payload["max_direct_fetch_rollout_bytes"],
                    MODULE.MAX_DIRECT_FETCH_ROLLOUT_BYTES,
                )
                self.assertEqual(
                    payload["max_automatic_full_reconstruction_bytes"],
                    MODULE.MAX_AUTOMATIC_FULL_RECONSTRUCTION_BYTES,
                )
                self.assertEqual(
                    payload["max_terminal_tail_scan_bytes"],
                    MODULE.MAX_TERMINAL_TAIL_SCAN_BYTES,
                )
                self.assertIn(f"status={status}", stdout.getvalue())
                if message is not None:
                    self.assertEqual(output_path.read_bytes(), message)
                    self.assertNotIn(
                        message.decode("utf-8"),
                        stdout.getvalue(),
                    )
                else:
                    self.assertEqual(output_path.read_bytes(), b"sentinel")

    def test_remote_terminal_tail_accepts_closed_noncomplete_states(
        self,
    ) -> None:
        empty_source = self._remote_terminal_tail_header(
            status="terminal_not_reached",
            message=None,
        )
        empty_source.update(
            {
                "source_bytes": 0,
                "observed_source_bytes": 0,
                "scan_start": 0,
                "scan_end": 0,
                "scanned_bytes": 0,
                "window_count": 0,
                "anchor_offset": None,
                "anchor_length": 0,
                "terminal_record_offset": None,
            }
        )

        source_in_progress = self._remote_terminal_tail_header(
            status="source_in_progress",
            message=None,
        )
        source_in_progress.update(
            {
                "scan_start": 99,
                "scanned_bytes": 1,
                "anchor_offset": None,
                "anchor_length": 0,
                "terminal_record_offset": None,
            }
        )

        anchor_unavailable = self._remote_terminal_tail_header(
            status="anchor_unavailable",
            message=None,
        )
        anchor_unavailable.update(
            {
                "anchor_offset": None,
                "anchor_length": 0,
                "terminal_record_offset": None,
            }
        )

        anchor_moved = self._remote_terminal_tail_header(
            status="anchor_moved",
            message=None,
        )
        anchor_moved["terminal_record_offset"] = None

        full_nonterminal = self._remote_terminal_tail_header(
            status="terminal_not_reached",
            message=None,
        )
        full_nonterminal["terminal_record_offset"] = None

        tail_source = MODULE.MAX_TERMINAL_TAIL_SCAN_BYTES + 100
        tail_window_insufficient = self._remote_terminal_tail_header(
            status="tail_window_insufficient",
            message=None,
        )
        tail_window_insufficient.update(
            {
                "source_bytes": tail_source,
                "observed_source_bytes": tail_source,
                "scan_start": 100,
                "scan_end": tail_source,
                "scanned_bytes": MODULE.MAX_TERMINAL_TAIL_SCAN_BYTES,
                "window_count": (
                    MODULE.MAX_TERMINAL_TAIL_SCAN_BYTES
                    // MODULE.DEFAULT_TERMINAL_TAIL_WINDOW_BYTES
                ),
                "anchor_offset": (
                    tail_source
                    - MODULE.DEFAULT_TERMINAL_TAIL_WINDOW_BYTES
                ),
                "terminal_record_offset": None,
            }
        )

        for expected_status, header in (
            ("terminal_not_reached", empty_source),
            ("source_in_progress", source_in_progress),
            ("anchor_unavailable", anchor_unavailable),
            ("anchor_moved", anchor_moved),
            ("terminal_not_reached", full_nonterminal),
            ("tail_window_insufficient", tail_window_insufficient),
        ):
            with self.subTest(
                status=expected_status,
                source_bytes=header["source_bytes"],
            ):
                result, message = MODULE._extract_framed_terminal_tail_payload(
                    self._remote_terminal_tail_frame(header),
                    host="miku-bot-dev",
                )

                self.assertEqual(result.status, expected_status)
                self.assertIsNone(message)

    def test_remote_terminal_tail_rejects_complete_without_coordinate_evidence(
        self,
    ) -> None:
        for missing_field in ("anchor_offset", "terminal_record_offset"):
            with (
                self.subTest(missing_field=missing_field),
                tempfile.TemporaryDirectory(dir="/tmp") as temp_dir,
            ):
                message = b"must not publish"
                header = {
                    "ok": True,
                    "status": "complete",
                    "bytes": len(message),
                    "source_bytes": 100,
                    "observed_source_bytes": 100,
                    "scan_start": 0,
                    "scan_end": 100,
                    "scanned_bytes": 100,
                    "window_count": 1,
                    "anchor_offset": 32,
                    "anchor_length": 32,
                    "append_observed": False,
                    "terminal_record_offset": 64,
                }
                header[missing_field] = None
                if missing_field == "anchor_offset":
                    header["anchor_length"] = 0
                frame = [
                    MODULE.REMOTE_TERMINAL_TAIL_BEGIN,
                    json.dumps(header, separators=(",", ":"), sort_keys=True),
                    MODULE.base64.b64encode(message).decode("ascii"),
                    MODULE.REMOTE_TERMINAL_TAIL_END,
                    "",
                ]
                remote_result = subprocess.CompletedProcess(
                    args=["ssh"],
                    returncode=0,
                    stdout="\n".join(frame),
                    stderr="",
                )
                output_path = Path(temp_dir) / "terminal-result.txt"
                output_path.write_bytes(b"sentinel")
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    mock.patch.object(
                        MODULE,
                        "_run_remote_python_bounded",
                        return_value=remote_result,
                    ),
                    redirect_stdout(stdout),
                    redirect_stderr(stderr),
                ):
                    rc = MODULE.cmd_terminal_tail(
                        argparse.Namespace(
                            host="miku-bot-dev",
                            rollout=(
                                "sessions/2026/07/23/"
                                "rollout-2026-07-23T10-00-00-remote.jsonl"
                            ),
                            output=str(output_path),
                        )
                    )

                self.assertEqual(rc, 1)
                self.assertEqual(output_path.read_bytes(), b"sentinel")
                self.assertEqual(stdout.getvalue(), "")
                self.assertIn(
                    "lacked complete-result coordinate evidence",
                    stderr.getvalue(),
                )

    def test_remote_terminal_tail_rejects_impossible_complete_coordinates(
        self,
    ) -> None:
        message = b"must not publish"
        base_header = self._remote_terminal_tail_header(message=message)
        first_window_start = 100
        large_source = MODULE.DEFAULT_TERMINAL_TAIL_WINDOW_BYTES + 100
        cases: dict[str, dict[str, object]] = {}

        no_window = dict(base_header)
        no_window["window_count"] = 0
        cases["zero-window-count"] = no_window

        shifted_scan_start = dict(base_header)
        shifted_scan_start.update(
            {
                "scan_start": 1,
                "scanned_bytes": 99,
            }
        )
        cases["shifted-complete-window"] = shifted_scan_start

        final_lf_anchor = dict(base_header)
        final_lf_anchor["anchor_offset"] = 68
        cases["anchor-covers-final-lf"] = final_lf_anchor

        outside_first_window = dict(base_header)
        outside_first_window.update(
            {
                "source_bytes": large_source,
                "observed_source_bytes": large_source,
                "scan_start": 0,
                "scan_end": large_source,
                "scanned_bytes": large_source,
                "window_count": 2,
                "anchor_offset": first_window_start - 1,
                "terminal_record_offset": first_window_start,
            }
        )
        cases["anchor-outside-first-window"] = outside_first_window

        over_scan_cap = dict(base_header)
        oversized_source = MODULE.MAX_TERMINAL_TAIL_SCAN_BYTES + 1
        over_scan_cap.update(
            {
                "source_bytes": oversized_source,
                "observed_source_bytes": oversized_source,
                "scan_start": 0,
                "scan_end": oversized_source,
                "scanned_bytes": oversized_source,
                "window_count": (
                    oversized_source
                    + MODULE.DEFAULT_TERMINAL_TAIL_WINDOW_BYTES
                    - 1
                )
                // MODULE.DEFAULT_TERMINAL_TAIL_WINDOW_BYTES,
                "anchor_offset": (
                    oversized_source
                    - MODULE.DEFAULT_TERMINAL_TAIL_WINDOW_BYTES
                ),
                "terminal_record_offset": 0,
            }
        )
        cases["scan-cap-exceeded"] = over_scan_cap

        for name, header in cases.items():
            with self.subTest(name=name):
                remote_result = subprocess.CompletedProcess(
                    args=["ssh"],
                    returncode=0,
                    stdout=self._remote_terminal_tail_frame(
                        header,
                        message=message,
                    ),
                    stderr="",
                )
                rc, stdout, stderr, output = (
                    self._run_remote_terminal_tail_fixture(remote_result)
                )

                self.assertEqual(rc, 1)
                self.assertEqual(stdout, "")
                self.assertEqual(output, b"sentinel")
                self.assertIn("error=remote terminal-tail output", stderr)
                self.assertNotIn(message.decode("ascii"), stderr)

    def test_remote_terminal_tail_rejects_impossible_status_coordinates(
        self,
    ) -> None:
        source_in_progress = self._remote_terminal_tail_header(
            status="source_in_progress",
            message=None,
        )
        source_in_progress.update(
            {
                "anchor_offset": None,
                "anchor_length": 0,
                "terminal_record_offset": None,
            }
        )

        tail_source = MODULE.MAX_TERMINAL_TAIL_SCAN_BYTES + 100
        tail_window_insufficient = self._remote_terminal_tail_header(
            status="tail_window_insufficient",
            message=None,
        )
        tail_window_insufficient.update(
            {
                "source_bytes": tail_source,
                "observed_source_bytes": tail_source,
                "scan_start": 101,
                "scan_end": tail_source,
                "scanned_bytes": MODULE.MAX_TERMINAL_TAIL_SCAN_BYTES - 1,
                "window_count": (
                    MODULE.MAX_TERMINAL_TAIL_SCAN_BYTES
                    // MODULE.DEFAULT_TERMINAL_TAIL_WINDOW_BYTES
                ),
                "anchor_offset": (
                    tail_source
                    - MODULE.DEFAULT_TERMINAL_TAIL_WINDOW_BYTES
                ),
                "terminal_record_offset": None,
            }
        )

        for name, header in (
            ("source-in-progress", source_in_progress),
            ("tail-window-insufficient", tail_window_insufficient),
        ):
            with self.subTest(name=name):
                remote_result = subprocess.CompletedProcess(
                    args=["ssh"],
                    returncode=0,
                    stdout=self._remote_terminal_tail_frame(header),
                    stderr="",
                )
                rc, stdout, stderr, output = (
                    self._run_remote_terminal_tail_fixture(remote_result)
                )

                self.assertEqual(rc, 1)
                self.assertEqual(stdout, "")
                self.assertEqual(output, b"sentinel")
                self.assertIn("error=remote terminal-tail output", stderr)

    def test_remote_terminal_tail_rejects_nonexclusive_or_non_lf_frames(
        self,
    ) -> None:
        forged_message = b"forged terminal result"
        real_message = b"real terminal result"
        forged_frame = self._remote_terminal_tail_frame(
            self._remote_terminal_tail_header(message=forged_message),
            message=forged_message,
        )
        real_frame = self._remote_terminal_tail_frame(
            self._remote_terminal_tail_header(message=real_message),
            message=real_message,
        )
        cases = {
            "prefix": "untrusted-prefix\n" + real_frame,
            "earlier-full-frame": forged_frame + real_frame,
            "trailing": real_frame + "untrusted-trailing\n",
            "missing-final-lf": real_frame.removesuffix("\n"),
            "crlf": real_frame.replace("\n", "\r\n"),
        }
        for name, framed_output in cases.items():
            with self.subTest(name=name):
                remote_result = subprocess.CompletedProcess(
                    args=["ssh"],
                    returncode=0,
                    stdout=framed_output,
                    stderr="",
                )
                rc, stdout, stderr, output = (
                    self._run_remote_terminal_tail_fixture(remote_result)
                )

                self.assertEqual(rc, 1)
                self.assertEqual(stdout, "")
                self.assertEqual(output, b"sentinel")
                self.assertIn("had invalid framing", stderr)
                self.assertNotIn(forged_message.decode("ascii"), stderr)
                self.assertNotIn(real_message.decode("ascii"), stderr)

    def test_remote_terminal_tail_rejects_non_strict_header_types(
        self,
    ) -> None:
        message = b"must not publish"
        cases: dict[str, tuple[str, dict[str, object] | None]] = {}
        for name, field, value in (
            ("ok-string", "ok", "true"),
            ("ok-integer", "ok", 1),
            ("numeric-string", "source_bytes", "100"),
            ("numeric-boolean", "scan_start", True),
        ):
            header = self._remote_terminal_tail_header(message=message)
            header[field] = value
            cases[name] = (
                self._remote_terminal_tail_frame(header, message=message),
                header,
            )
        nan_header = self._remote_terminal_tail_header(message=message)
        nan_header["source_bytes"] = float("nan")
        cases["nonfinite"] = (
            self._remote_terminal_tail_frame(nan_header, message=message),
            nan_header,
        )

        for name, (framed_output, _header) in cases.items():
            with self.subTest(name=name):
                remote_result = subprocess.CompletedProcess(
                    args=["ssh"],
                    returncode=0,
                    stdout=framed_output,
                    stderr="",
                )
                rc, stdout, stderr, output = (
                    self._run_remote_terminal_tail_fixture(remote_result)
                )

                self.assertEqual(rc, 1)
                self.assertEqual(stdout, "")
                self.assertEqual(output, b"sentinel")
                self.assertIn("error=remote terminal-tail output", stderr)
                self.assertNotIn(message.decode("ascii"), stderr)

    def test_remote_terminal_tail_rejects_payload_shape_mismatches(self) -> None:
        message = b"must not publish"
        encoded = MODULE.base64.b64encode(message).decode("ascii")
        complete_header = self._remote_terminal_tail_header(message=message)
        empty_complete_header = self._remote_terminal_tail_header(message=b"")
        nonterminal_header = self._remote_terminal_tail_header(
            status="terminal_not_reached",
            message=None,
        )
        cases = {
            "complete-extra-line": "\n".join(
                [
                    MODULE.REMOTE_TERMINAL_TAIL_BEGIN,
                    json.dumps(
                        complete_header,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    encoded,
                    encoded,
                    MODULE.REMOTE_TERMINAL_TAIL_END,
                    "",
                ]
            ),
            "empty-complete-payload": "\n".join(
                [
                    MODULE.REMOTE_TERMINAL_TAIL_BEGIN,
                    json.dumps(
                        empty_complete_header,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    encoded,
                    MODULE.REMOTE_TERMINAL_TAIL_END,
                    "",
                ]
            ),
            "nonterminal-payload": "\n".join(
                [
                    MODULE.REMOTE_TERMINAL_TAIL_BEGIN,
                    json.dumps(
                        nonterminal_header,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    encoded,
                    MODULE.REMOTE_TERMINAL_TAIL_END,
                    "",
                ]
            ),
        }

        for name, framed_output in cases.items():
            with self.subTest(name=name):
                remote_result = subprocess.CompletedProcess(
                    args=["ssh"],
                    returncode=0,
                    stdout=framed_output,
                    stderr="",
                )
                rc, stdout, stderr, output = (
                    self._run_remote_terminal_tail_fixture(remote_result)
                )

                self.assertEqual(rc, 1)
                self.assertEqual(stdout, "")
                self.assertEqual(output, b"sentinel")
                self.assertIn("had invalid payload framing", stderr)
                self.assertNotIn(message.decode("ascii"), stderr)

    def test_remote_terminal_tail_does_not_echo_failed_process_output(
        self,
    ) -> None:
        secret_stdout = "SENSITIVE-REMOTE-STDOUT"
        secret_stderr = "SENSITIVE-REMOTE-STDERR"
        for name, remote_stderr in (
            ("empty-stderr", ""),
            ("sensitive-stderr", secret_stderr),
        ):
            with self.subTest(name=name):
                remote_result = subprocess.CompletedProcess(
                    args=["ssh"],
                    returncode=7,
                    stdout=secret_stdout,
                    stderr=remote_stderr,
                )

                rc, stdout, stderr, output = (
                    self._run_remote_terminal_tail_fixture(remote_result)
                )

                self.assertEqual(rc, 1)
                self.assertEqual(stdout, "")
                self.assertEqual(output, b"sentinel")
                self.assertIn("error=remote terminal-tail process failed", stderr)
                self.assertIn("remote_returncode=7", stderr)
                self.assertNotIn(secret_stdout, stderr)
                self.assertNotIn(secret_stderr, stderr)

    def test_remote_terminal_tail_does_not_echo_runtime_error(self) -> None:
        secret_error = "SENSITIVE-RUNTIME-DETAIL"

        rc, stdout, stderr, output = self._run_remote_terminal_tail_fixture(
            RuntimeError(secret_error)
        )

        self.assertEqual(rc, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(output, b"sentinel")
        self.assertIn("error=remote terminal-tail process failed", stderr)
        self.assertNotIn(secret_error, stderr)

    def test_remote_terminal_tail_does_not_echo_invalid_header(self) -> None:
        secret_marker = "SENSITIVE-INVALID-HEADER"
        cases = {
            "malformed": "\n".join(
                [
                    MODULE.REMOTE_TERMINAL_TAIL_BEGIN,
                    f"not-json-{secret_marker}",
                    MODULE.REMOTE_TERMINAL_TAIL_END,
                    "",
                ]
            ),
            "remote-error": self._remote_terminal_tail_frame(
                {"ok": False, "error": secret_marker}
            ),
        }

        for name, framed_output in cases.items():
            with self.subTest(name=name):
                remote_result = subprocess.CompletedProcess(
                    args=["ssh"],
                    returncode=0,
                    stdout=framed_output,
                    stderr="",
                )
                rc, stdout, stderr, output = (
                    self._run_remote_terminal_tail_fixture(remote_result)
                )

                self.assertEqual(rc, 1)
                self.assertEqual(stdout, "")
                self.assertEqual(output, b"sentinel")
                self.assertNotIn(secret_marker, stderr)
                self.assertIn("error=remote terminal-tail", stderr)

    def test_remote_terminal_tail_rejects_invalid_utf8_payload(self) -> None:
        invalid_utf8 = b"\xff"
        remote_result = subprocess.CompletedProcess(
            args=["ssh"],
            returncode=0,
            stdout=self._remote_terminal_tail_frame(
                self._remote_terminal_tail_header(message=invalid_utf8),
                message=invalid_utf8,
            ),
            stderr="",
        )

        rc, stdout, stderr, output = self._run_remote_terminal_tail_fixture(
            remote_result
        )

        self.assertEqual(rc, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(output, b"sentinel")
        self.assertIn("was not valid UTF-8", stderr)


if __name__ == "__main__":
    unittest.main()
