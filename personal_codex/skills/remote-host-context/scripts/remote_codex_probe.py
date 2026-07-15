#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import binascii
import codecs
import collections
import dataclasses
import datetime as dt
import errno
import hashlib
import hmac
import inspect
import json
import os
import pathlib
import re
import shlex
import socket
import sqlite3
import subprocess
import stat
import sys
import tempfile
import threading
from collections.abc import Iterable
from typing import Any

DATE_FORMAT = "%Y/%m/%d"
MAX_SESSION_META_LIMIT = 500
MAX_SESSION_META_DATE_COUNT = 31
MAX_FETCH_ROLLOUT_BYTES = 16 * 1024 * 1024
DEFAULT_ROLLOUT_CHUNK_BYTES = 1024 * 1024
MAX_ROLLOUT_CHUNK_BYTES = 2 * 1024 * 1024
MAX_FETCH_ROLLOUT_CHUNK_BYTES = 2 * 1024 * 1024
MAX_ROLLOUT_SUMMARY_LIMIT = 200
MAX_ROLLOUT_SUMMARY_SCAN_BYTES = 2 * 1024 * 1024
MAX_ROLLOUT_SUMMARY_LINE_BYTES = 1024 * 1024
MAX_ROLLOUT_SUMMARY_TAIL_RECORDS = 50
MAX_ROLLOUT_SUMMARY_TEXT_CHARS = 1200
MAX_SESSION_META_SCAN_BYTES = 256 * 1024
DEFAULT_SESSION_SHARD_BYTES = 512 * 1024
MAX_SESSION_SHARD_BYTES = 512 * 1024
DEFAULT_SESSION_SHARDS_PER_PAGE = 64
MAX_SESSION_SHARDS_PER_PAGE = 1024
DEFAULT_SESSION_RECORD_PROCESSING_BUDGET_BYTES = 64 * 1024 * 1024
HARD_SESSION_RECORD_PROCESSING_CEILING_BYTES = 256 * 1024 * 1024
HARD_SESSION_RECORD_SCAN_CEILING_BYTES = 256 * 1024 * 1024
MIN_SESSION_RECORD_PROCESSING_BUDGET_BYTES = 4 * 1024 * 1024
MAX_SESSION_SHARDS_RANGE_BYTES = HARD_SESSION_RECORD_PROCESSING_CEILING_BYTES
SESSION_SHARDS_RECORD_FRAGMENT_BYTES = 256 * 1024
SESSION_SHARDS_RECORD_SCAN_CHUNK_BYTES = 64 * 1024
SESSION_SHARDS_RECORD_SPOOL_MEMORY_BYTES = 64 * 1024
SESSION_SHARDS_JSON_VALIDATION_CHUNK_BYTES = 64 * 1024
SESSION_SHARDS_MAX_JSON_NESTING_DEPTH = 512
SESSION_SHARDS_FRAME_METADATA_CHARS = 16 * 1024
MAX_SESSION_SHARDS_FRAME_CHARS = (
    4 * ((max(MAX_SESSION_SHARD_BYTES, SESSION_SHARDS_RECORD_FRAGMENT_BYTES) + 2) // 3)
    + SESSION_SHARDS_FRAME_METADATA_CHARS
)
REMOTE_PREFLIGHT_TIMEOUT_SECONDS = 15
REMOTE_COMMAND_TIMEOUT_SECONDS = 60
MAX_REMOTE_SESSION_SHARDS_DIAGNOSTIC_BYTES = 512
TASK_OUTPUT_RELATIVE_DIR = pathlib.Path(".codex-tmp/remote-host-context")
ACTIVE_ROLLOUT_RELATIVE_RE = re.compile(
    r"^sessions/\d{4}/\d{2}/\d{2}/rollout-[^/]+\.jsonl$"
)
ARCHIVED_ROLLOUT_RELATIVE_RE = re.compile(
    r"^archived_sessions/(?:\d{4}/\d{2}/\d{2}/)?rollout-[^/]+\.jsonl$"
)
SESSION_SHARDS_ACTIVE_ROLLOUT_RELATIVE_RE = re.compile(
    r"^sessions/\d{4}/\d{2}/\d{2}/rollout-(?!summary)[^/]+\.jsonl$"
)
SESSION_SHARDS_ARCHIVED_ROLLOUT_RELATIVE_RE = re.compile(
    r"^archived_sessions/(?:\d{4}/\d{2}/\d{2}/)?rollout-(?!summary)[^/]+\.jsonl$"
)
ROOT_ROLLOUT_RELATIVE_RE = re.compile(r"^rollout-(?!summary)[^/]+\.jsonl$")
PRIVATE_IPV4_SIGNAL_RE = re.compile(
    r"(?<![\d.])(?:10(?:\.\d{1,3}){3}|100\.(?:6[4-9]|[78]\d|9\d|1[01]\d|12[0-7])(?:\.\d{1,3}){2}|127(?:\.\d{1,3}){3}|169\.254(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}|192\.168(?:\.\d{1,3}){2})(?![\d.])"
)
PRIVATE_IPV6_SIGNAL_RE = re.compile(
    r"(?<![0-9A-Fa-f:])(?:::1|f[cd][0-9A-Fa-f]{0,2}(?::[0-9A-Fa-f]{0,4}){1,7}|fe[89abAB][0-9A-Fa-f]?(?::[0-9A-Fa-f]{0,4}){1,7})(?![0-9A-Fa-f:])",
    re.I,
)
INTERNAL_HOSTNAME_SIGNAL_RE = re.compile(
    r"\b(?:[A-Za-z0-9-]+\.)+(?:internal|corp|local|lan|example|invalid|test)\b",
    re.I,
)
WRAPPER_PREFIXES = (
    "# AGENTS.md instructions",
    "<skill>",
    "<environment_context>",
    "<subagent_notification>",
    "# Review findings:",
    "<turn_aborted>",
    "Persistent internal Codex readonly review contract:",
    "Review discipline:",
)
WRAPPER_END_MARKERS = (
    "</INSTRUCTIONS>",
    "</environment_context>",
    "</skill>",
    "</subagent_notification>",
    "</turn_aborted>",
)
AUTOMATION_PROMPT_PATTERN_TEXTS = (
    r"^Run the (?:daily|weekly) Codex session retrospective\b",
    r"^Run a read-only (?:daily|weekly) retrospective over Joey's Codex session activity\b",
    r"^Run inside the dedicated worktree provisioned for this automation\b",
    r"^Use \$codex-session-retrospective to run\b",
    r"^Use the installed codex-session-retrospective workflow\b",
)
AUTOMATION_PROMPT_PATTERNS = tuple(
    re.compile(pattern, re.I) for pattern in AUTOMATION_PROMPT_PATTERN_TEXTS
)
AUTOMATION_PROMPT_MARKERS = (
    "Run a read-only daily retrospective over Joey's Codex session activity.",
    "Run a read-only weekly retrospective over Joey's Codex session activity.",
    "Evidence scope must match $remote-host-context's default host policy",
    "Use the automation's configured model and reasoning effort",
    "When reconstructing the real user task from rollouts, ignore injected wrapper content",
    "Write task-local artifacts under .codex-local/session-retrospective/runs/",
)
SUMMARY_SIGNAL_MARKERS = (
    "error:",
    "approval",
    "could not run",
    "you missed",
    "assumed",
    "secret",
)
REMOTE_SESSION_META_BEGIN = "__REMOTE_CODEX_PROBE_SESSION_META_BEGIN__"
REMOTE_SESSION_META_END = "__REMOTE_CODEX_PROBE_SESSION_META_END__"
SESSION_META_LIMIT_TRUNCATED_REASON = "session_meta_limit_truncated"
REMOTE_FETCH_ROLLOUT_BEGIN = "__REMOTE_CODEX_PROBE_FETCH_ROLLOUT_BEGIN__"
REMOTE_FETCH_ROLLOUT_END = "__REMOTE_CODEX_PROBE_FETCH_ROLLOUT_END__"
REMOTE_FETCH_ROLLOUT_CHUNK_BEGIN = "__REMOTE_CODEX_PROBE_FETCH_ROLLOUT_CHUNK_BEGIN__"
REMOTE_FETCH_ROLLOUT_CHUNK_END = "__REMOTE_CODEX_PROBE_FETCH_ROLLOUT_CHUNK_END__"
REMOTE_ROLLOUT_SUMMARY_BEGIN = "__REMOTE_CODEX_PROBE_ROLLOUT_SUMMARY_BEGIN__"
REMOTE_ROLLOUT_SUMMARY_END = "__REMOTE_CODEX_PROBE_ROLLOUT_SUMMARY_END__"
REMOTE_CHUNKED_ROLLOUT_SUMMARY_BEGIN = (
    "__REMOTE_CODEX_PROBE_CHUNKED_ROLLOUT_SUMMARY_BEGIN__"
)
REMOTE_CHUNKED_ROLLOUT_SUMMARY_END = (
    "__REMOTE_CODEX_PROBE_CHUNKED_ROLLOUT_SUMMARY_END__"
)
REMOTE_SESSION_SHARDS_BEGIN = "__REMOTE_CODEX_PROBE_SESSION_SHARDS_BEGIN__"
REMOTE_SESSION_SHARDS_END = "__REMOTE_CODEX_PROBE_SESSION_SHARDS_END__"
SESSION_SHARDS_SCHEMA = "session-shards-v1"
SESSION_SHARDS_SOURCE_TOKEN_PREFIX = "session_shards_source_v1:"
SESSION_SHARDS_RESUME_CURSOR_PREFIX = "session_shards_resume_v1:"
SESSION_SHARDS_REQUEST_BINDING_PREFIX = "session_shards_request_v1:"
SESSION_SHARDS_HOLDOUT_SCHEMA = "session-shards-shadow-holdout-v1"
SESSION_SHARDS_HOLDOUT_REASON = "shadow_qualification_controlled_missing_host"
SESSION_SHARDS_HOLDOUT_SAFE_REASONS = frozenset({SESSION_SHARDS_HOLDOUT_REASON})
SESSION_SHARDS_HOLDOUT_RECEIPT_PREFIX = "session_shards_holdout_v1:"
SESSION_SHARDS_HOLDOUT_KEY_ID_PREFIX = "session_shards_holdout_key_v1:"
SESSION_SHARDS_HOLDOUT_AUTH_PREFIX = "session_shards_holdout_hmac_v1:"
SESSION_SHARDS_HOLDOUT_IDENTITY_KEY_FILE = "holdout-hmac-v1.key"
SESSION_SHARDS_HOLDOUT_IDENTITY_KEY_BYTES = 32
SESSION_SHARDS_HOLDOUT_AUTH_CONTEXT = b"session-shards-shadow-holdout-v1\0"
SESSION_SHARDS_BACKFILL_RESULT_SCHEMA = "session-shards-shadow-backfill-result-v1"
SESSION_SHARDS_BACKFILL_RESULT_KIND = "coordinator_backfill_result"
SESSION_SHARDS_BACKFILL_RESULT_REF_PREFIX = "session_shards_backfill_result_v1:"
SESSION_SHARDS_BACKFILL_RESULT_AUTH_PREFIX = "session_shards_backfill_auth_v1:"
SESSION_SHARDS_BACKFILL_RESULT_AUTH_CONTEXT = "session-shards-shadow-backfill-result-v1"
SESSION_SHARDS_COORDINATOR_IDENTITY_KEY_BYTES = 32
SESSION_SHARDS_COORDINATOR_IDENTITY_ROOT_DOMAIN = (
    b"codex-session-retrospective/identity/v2"
)
SESSION_SHARDS_BACKFILL_RESULT_MAX_AGE_SECONDS = 300
SESSION_SHARDS_BACKFILL_RESULT_FUTURE_SKEW_SECONDS = 5
SESSION_SHARDS_HOLDOUT_SOURCE_KIND_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
SESSION_SHARDS_HOLDOUT_LEASE_REF_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$"
)
SESSION_SHARDS_HOLDOUT_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T00:00:00Z$")
SESSION_SHARDS_CONFIGURATION_ROOT_RE = re.compile(r"^[0-9a-f]{64}$")
SESSION_SHARDS_RUN_REF_RE = re.compile(r"^run_ref_v2:[0-9a-f]{64}$")
SESSION_SHARDS_HOST_REF_RE = re.compile(r"^host_ref_v2:[0-9a-f]{64}$")
SESSION_SHARDS_SOURCE_SNAPSHOT_REF_RE = re.compile(r"^source_snapshot_v2:[0-9a-f]{64}$")
SESSION_SHARDS_SOURCE_RECEIPT_REF_RE = re.compile(
    r"^source_transport_receipt_v2:[0-9a-f]{64}$"
)
SESSION_SHARDS_SOURCE_EVIDENCE_RE = re.compile(
    r"^shadow_source_evidence_v2:[0-9a-f]{64}$"
)
SESSION_SHARDS_COVERAGE_RECEIPT_REF_RE = re.compile(
    r"^shadow_coverage_receipt_v2:[0-9a-f]{64}$"
)
SESSION_SHARDS_COVERAGE_AUTH_RE = re.compile(r"^shadow_coverage_auth_v2:[0-9a-f]{64}$")
SESSION_SHARDS_COORDINATOR_IDENTITY_RE = re.compile(r"^identity_key_v2:[0-9a-f]{64}$")
SESSION_SHARDS_ATTESTED_TIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$"
)
SESSION_SHARDS_HOLDOUT_LEDGER_SCHEMA_VERSION = 2
SESSION_SHARDS_PROTOCOL_FEATURES = (
    "hard_record_scan_ceiling_v1",
    "oversized_record_fragments_v1",
    "terminal_conservation_v1",
    "request_binding_v1",
    "resume_cursor_v1",
)
_SESSION_SHARDS_BINDING_FIELDS = frozenset(
    {
        "kind",
        "schema",
        "mode",
        "source_token",
        "request_binding",
    }
)
_SESSION_SHARDS_STREAM_META_FIELDS = _SESSION_SHARDS_BINDING_FIELDS | {
    "request_rollout",
    "request_source_token",
    "request_resume_cursor",
    "source_bytes",
    "byte_start",
    "byte_end",
    "record_start",
    "shard_bytes",
    "max_shards",
    "record_processing_budget_bytes",
    "fixed_memory_envelope_bytes",
    "hard_record_processing_ceiling_bytes",
    "hard_record_scan_ceiling_bytes",
    "record_fragment_bytes",
    "json_nesting_depth_limit",
    "max_remote_frame_chars",
    "protocol_features",
}
_SESSION_SHARDS_DESCRIPTOR_FIELDS = _SESSION_SHARDS_BINDING_FIELDS | {
    "status",
    "byte_start",
    "byte_end",
    "record_start",
    "record_end",
    "record_count",
    "page_shard_index",
    "resume_cursor",
}
_SESSION_SHARDS_OVERSIZED_DESCRIPTOR_FIELDS = frozenset(
    {
        "oversized_record",
        "record_transport",
        "record_fragment_bytes",
        "record_processing_budget_bytes",
    }
)
_SESSION_SHARDS_GAP_DESCRIPTOR_FIELDS = frozenset({"gap_reason", "byte_count"})
_SESSION_SHARDS_PROCESSING_CEILING_FIELDS = frozenset(
    {
        "record_processing_budget_bytes",
        "hard_record_processing_ceiling_bytes",
        "processing_ceiling_kind",
        "processing_ceiling_limit",
        "processing_ceiling_observed",
    }
)
_SESSION_SHARDS_RECORD_COORDINATE_FIELDS = frozenset(
    {
        "byte_start",
        "byte_end",
        "byte_count",
        "record_start",
        "record_end",
        "delimiter_bytes",
    }
)
_SESSION_SHARDS_RECORD_FIELDS = (
    _SESSION_SHARDS_BINDING_FIELDS
    | _SESSION_SHARDS_RECORD_COORDINATE_FIELDS
    | {"record_encoding", "record_b64", "record_commitment"}
)
_SESSION_SHARDS_FRAGMENT_FIELDS = (
    _SESSION_SHARDS_BINDING_FIELDS
    | _SESSION_SHARDS_RECORD_COORDINATE_FIELDS
    | {
        "record_byte_start",
        "record_byte_end",
        "record_byte_count",
        "fragment_index",
        "fragment_count",
        "record_encoding",
        "fragment_b64",
        "fragment_commitment",
        "record_commitment",
    }
)
_SESSION_SHARDS_GAP_FIELDS = (
    _SESSION_SHARDS_BINDING_FIELDS
    | _SESSION_SHARDS_RECORD_COORDINATE_FIELDS
    | {"reason"}
)
_SESSION_SHARDS_DESCRIPTOR_TERMINAL_FIELDS = _SESSION_SHARDS_BINDING_FIELDS | {
    "complete",
    "reason",
    "emitted_shards",
    "byte_start",
    "byte_end",
    "record_start",
    "record_end",
    "next_byte_start",
    "next_record_start",
    "next_resume_cursor",
    "accounted_byte_count",
    "accounted_record_count",
}
_SESSION_SHARDS_RECORD_TERMINAL_FIELDS = _SESSION_SHARDS_BINDING_FIELDS | {
    "complete",
    "reason",
    "emitted_records",
    "emitted_gaps",
    "emitted_fragments",
    "emitted_record_bytes",
    "emitted_gap_bytes",
    "emitted_fragment_bytes",
    "byte_start",
    "byte_end",
    "record_start",
    "record_end",
    "conservation_proof",
}
_SESSION_SHARDS_CONSERVATION_PROOF_FIELDS = frozenset(
    {
        "schema",
        "source_token",
        "request_binding",
        "byte_start",
        "byte_end",
        "byte_count",
        "accounted_byte_count",
        "record_start",
        "record_end",
        "record_count",
        "accounted_record_count",
        "accounting_commitment",
    }
)
_SESSION_SHARDS_HOLDOUT_RECEIPT_FIELDS = frozenset(
    {
        "kind",
        "schema",
        "terminal",
        "qualification_mode",
        "receipt_type",
        "reason",
        "host",
        "window_start",
        "window_end",
        "source_kind",
        "source_lease_ref",
        "content_free",
        "source_observed",
        "transport_attempted",
        "backfill_required",
        "identity_key_id",
        "holdout_ref",
        "authentication_tag",
    }
)
_SESSION_SHARDS_BACKFILL_RESULT_FIELDS = frozenset(
    {
        "attested_at_utc",
        "authentication_tag",
        "backfill_configuration_root",
        "backfill_holdout_used",
        "backfill_of_run_ref",
        "backfill_run_ref",
        "backfill_source_lease_ref",
        "completion_stage",
        "coordinator_identity_key_id",
        "evidence_digest",
        "holdout_ref",
        "host",
        "host_ref",
        "identity_key_id",
        "kind",
        "partial_configuration_root",
        "partial_run_ref",
        "partial_source_lease_ref",
        "result_ref",
        "schema",
        "source_kind",
        "source_outcome",
        "source_snapshot_ref",
        "source_transport_receipt_ref",
        "status_checkpoint_revision",
        "terminal",
        "terminal_completion_authentication_tag",
        "terminal_completion_ref",
        "terminal_completion_revision",
        "transport",
        "window_end",
        "window_start",
    }
)

HOSTS: dict[str, dict[str, str]] = {
    "local": {"kind": "local", "label": "local", "codex_root": "~/.codex"},
    "BL-mac-mini-m4-hoteng": {
        "kind": "ssh",
        "label": "BL-mac-mini-m4-hoteng",
        "ssh_target": "BL-mac-mini-m4-hoteng",
        "codex_root": "/Users/hoteng/.codex",
    },
    "miku-bot-dev": {
        "kind": "ssh",
        "label": "miku-bot-dev",
        "ssh_target": "miku-bot-dev",
        "codex_root": "/home/hoteng/.codex",
    },
    "miku-server-dev": {
        "kind": "ssh",
        "label": "miku-bot-dev",
        "ssh_target": "miku-bot-dev",
        "codex_root": "/home/hoteng/.codex",
    },
    "hoteng-srv-01": {
        "kind": "ssh",
        "label": "hoteng-srv-01",
        "ssh_target": "hoteng-srv-01",
        "codex_root": "/home/hoteng/.codex",
    },
    "codex-hoteng-srv-01": {
        "kind": "ssh",
        "label": "codex-hoteng-srv-01",
        "ssh_target": "codex-hoteng-srv-01",
        "codex_root": "/home/codex/.codex",
    },
}


@dataclasses.dataclass(frozen=True)
class SessionMetaScan:
    rows: list[dict[str, str]]
    truncated: bool


@dataclasses.dataclass(frozen=True)
class RolloutChunk:
    index: int
    byte_start: int
    byte_end: int
    record_start: int
    record_end: int
    first_timestamp: str
    last_timestamp: str
    oversized_record: bool
    lines: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class SessionShardRecord:
    byte_start: int
    byte_end: int
    record_index: int
    record_storage: Any | None
    record_commitment: str | None
    delimiter_bytes: int
    gap_reason: str | None
    processing_ceiling_kind: str | None
    processing_ceiling_limit: int | None
    processing_ceiling_observed: int | None


class _SessionShardsProcessingBudgetExceeded(ValueError):
    def __init__(self, *, kind: str, limit: int, observed: int) -> None:
        super().__init__(f"{kind} processing ceiling exceeded: {observed} > {limit}")
        self.kind = kind
        self.limit = limit
        self.observed = observed


class _IncrementalJSONObjectValidator:
    _WHITESPACE = frozenset(" \t\r\n")
    _HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
    _SIMPLE_ESCAPES = frozenset('"\\/bfnrt')
    _NUMBER_TERMINAL_STATES = frozenset(
        {"zero", "integer", "fraction", "exponent_digits"}
    )

    def __init__(self) -> None:
        self.stack: list[list[str]] = []
        self.root_started = False
        self.root_complete = False
        self.token_kind: str | None = None
        self.string_is_key = False
        self.string_escape = False
        self.unicode_escape_remaining = 0
        self.literal_remaining = ""
        self.number_state: str | None = None
        self.position = 0

    def _invalid(self, detail: str) -> None:
        raise ValueError(f"invalid JSON object at character {self.position}: {detail}")

    def _push_container(self, kind: str, state: str) -> None:
        if len(self.stack) >= SESSION_SHARDS_MAX_JSON_NESTING_DEPTH:
            raise _SessionShardsProcessingBudgetExceeded(
                kind="json_nesting_depth",
                limit=SESSION_SHARDS_MAX_JSON_NESTING_DEPTH,
                observed=len(self.stack) + 1,
            )
        self.stack.append([kind, state])

    def _complete_value(self) -> None:
        if not self.stack:
            self.root_complete = True
            return
        frame = self.stack[-1]
        if frame[1] != "value":
            self._invalid("value appeared in an invalid container state")
        frame[1] = "comma_or_end"

    def _close_container(self, kind: str) -> None:
        if not self.stack or self.stack[-1][0] != kind:
            self._invalid("mismatched container terminator")
        self.stack.pop()
        self._complete_value()

    def _start_string(self, *, is_key: bool) -> None:
        self.token_kind = "string"
        self.string_is_key = is_key
        self.string_escape = False
        self.unicode_escape_remaining = 0

    def _start_value(self, character: str) -> None:
        if character in self._WHITESPACE:
            return
        if character == "{":
            self._push_container("object", "key_or_end")
        elif character == "[":
            self._push_container("array", "value_or_end")
        elif character == '"':
            self._start_string(is_key=False)
        elif character in "tfn":
            self.token_kind = "literal"
            self.literal_remaining = {
                "t": "rue",
                "f": "alse",
                "n": "ull",
            }[character]
        elif character == "-":
            self.token_kind = "number"
            self.number_state = "sign"
        elif character == "0":
            self.token_kind = "number"
            self.number_state = "zero"
        elif "1" <= character <= "9":
            self.token_kind = "number"
            self.number_state = "integer"
        else:
            self._invalid("expected a JSON value")

    def _consume_string(self, character: str) -> None:
        if self.unicode_escape_remaining:
            if character not in self._HEX_DIGITS:
                self._invalid("invalid unicode escape")
            self.unicode_escape_remaining -= 1
            return
        if self.string_escape:
            self.string_escape = False
            if character == "u":
                self.unicode_escape_remaining = 4
            elif character not in self._SIMPLE_ESCAPES:
                self._invalid("invalid string escape")
            return
        if character == "\\":
            self.string_escape = True
            return
        if character == '"':
            self.token_kind = None
            if self.string_is_key:
                if (
                    not self.stack
                    or self.stack[-1][0] != "object"
                    or self.stack[-1][1] not in ("key", "key_or_end")
                ):
                    self._invalid("object key appeared in an invalid state")
                self.stack[-1][1] = "colon"
            else:
                self._complete_value()
            return
        if ord(character) < 0x20:
            self._invalid("unescaped control character in string")

    def _consume_literal(self, character: str) -> None:
        if not self.literal_remaining or character != self.literal_remaining[0]:
            self._invalid("invalid literal")
        self.literal_remaining = self.literal_remaining[1:]
        if not self.literal_remaining:
            self.token_kind = None
            self._complete_value()

    def _consume_number(self, character: str) -> bool:
        state = self.number_state
        if state == "sign":
            if character == "0":
                self.number_state = "zero"
            elif "1" <= character <= "9":
                self.number_state = "integer"
            else:
                self._invalid("minus must be followed by a digit")
            return True
        if state == "zero":
            if character == ".":
                self.number_state = "decimal_point"
                return True
            if character in "eE":
                self.number_state = "exponent"
                return True
            if "0" <= character <= "9":
                self._invalid("leading zero in number")
            return False
        if state == "integer":
            if "0" <= character <= "9":
                return True
            if character == ".":
                self.number_state = "decimal_point"
                return True
            if character in "eE":
                self.number_state = "exponent"
                return True
            return False
        if state == "decimal_point":
            if "0" <= character <= "9":
                self.number_state = "fraction"
                return True
            self._invalid("decimal point must be followed by a digit")
        if state == "fraction":
            if "0" <= character <= "9":
                return True
            if character in "eE":
                self.number_state = "exponent"
                return True
            return False
        if state == "exponent":
            if character in "+-":
                self.number_state = "exponent_sign"
            elif "0" <= character <= "9":
                self.number_state = "exponent_digits"
            else:
                self._invalid("exponent must contain a digit")
            return True
        if state == "exponent_sign":
            if not "0" <= character <= "9":
                self._invalid("exponent sign must be followed by a digit")
            self.number_state = "exponent_digits"
            return True
        if state == "exponent_digits":
            if "0" <= character <= "9":
                return True
            return False
        self._invalid("invalid number state")
        return False

    def _finish_number(self) -> None:
        if self.number_state not in self._NUMBER_TERMINAL_STATES:
            self._invalid("incomplete number")
        self.token_kind = None
        self.number_state = None
        self._complete_value()

    def _consume_structure(self, character: str) -> None:
        if self.root_complete:
            if character not in self._WHITESPACE:
                self._invalid("trailing data after root object")
            return
        if not self.root_started:
            if character in self._WHITESPACE or (
                self.position == 0 and character == "\ufeff"
            ):
                return
            if character != "{":
                self._invalid("JSONL record must be an object")
            self.root_started = True
            self._push_container("object", "key_or_end")
            return
        if not self.stack:
            self._invalid("unexpected data after root object")
        frame = self.stack[-1]
        kind, state = frame
        if kind == "object":
            if state in ("key", "key_or_end"):
                if character in self._WHITESPACE:
                    return
                if state == "key_or_end" and character == "}":
                    self._close_container("object")
                elif character == '"':
                    self._start_string(is_key=True)
                else:
                    self._invalid("expected an object key")
            elif state == "colon":
                if character in self._WHITESPACE:
                    return
                if character != ":":
                    self._invalid("expected colon after object key")
                frame[1] = "value"
            elif state == "value":
                self._start_value(character)
            elif state == "comma_or_end":
                if character in self._WHITESPACE:
                    return
                if character == ",":
                    frame[1] = "key"
                elif character == "}":
                    self._close_container("object")
                else:
                    self._invalid("expected comma or object terminator")
            else:
                self._invalid("invalid object parser state")
            return
        if kind == "array":
            if state in ("value", "value_or_end"):
                if character in self._WHITESPACE:
                    return
                if state == "value_or_end" and character == "]":
                    self._close_container("array")
                else:
                    frame[1] = "value"
                    self._start_value(character)
            elif state == "comma_or_end":
                if character in self._WHITESPACE:
                    return
                if character == ",":
                    frame[1] = "value"
                elif character == "]":
                    self._close_container("array")
                else:
                    self._invalid("expected comma or array terminator")
            else:
                self._invalid("invalid array parser state")
            return
        self._invalid("invalid container kind")

    def feed(self, text: str) -> None:
        for character in text:
            while True:
                if self.token_kind == "string":
                    self._consume_string(character)
                    break
                if self.token_kind == "literal":
                    self._consume_literal(character)
                    break
                if self.token_kind == "number":
                    if self._consume_number(character):
                        break
                    self._finish_number()
                    continue
                self._consume_structure(character)
                break
            self.position += 1

    def finish(self) -> None:
        if self.token_kind == "number":
            self._finish_number()
        elif self.token_kind is not None:
            self._invalid("incomplete JSON token")
        if not self.root_complete or self.stack:
            self._invalid("incomplete root object")


def _validate_session_shards_json_storage(storage: Any) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    validator = _IncrementalJSONObjectValidator()
    storage.seek(0)
    while True:
        chunk = storage.read(SESSION_SHARDS_JSON_VALIDATION_CHUNK_BYTES)
        if not chunk:
            break
        validator.feed(decoder.decode(chunk, final=False))
    validator.feed(decoder.decode(b"", final=True))
    validator.finish()
    storage.seek(0)


def _session_shards_payload_delimiter_bytes(payload_tail: bytes) -> int:
    if payload_tail.endswith(b"\r\n"):
        return 2
    if payload_tail.endswith(b"\n"):
        return 1
    return 0


@dataclasses.dataclass
class _RemoteSessionShardsValidator:
    request: dict[str, object]
    stream_meta: dict[str, Any] | None = None
    next_byte: int | None = None
    next_record: int | None = None
    emitted_shards: int = 0
    emitted_records: int = 0
    emitted_gaps: int = 0
    emitted_fragments: int = 0
    emitted_record_bytes: int = 0
    emitted_gap_bytes: int = 0
    emitted_fragment_bytes: int = 0
    accounting_hasher: Any = dataclasses.field(default_factory=hashlib.sha256)
    fragment_state: dict[str, Any] | None = None

    @staticmethod
    def _require_exact_fields(
        frame: dict[str, Any],
        expected_fields: frozenset[str],
        label: str,
    ) -> None:
        if set(frame) != expected_fields:
            raise RuntimeError(
                f"remote session-shards {label} does not match the closed field schema"
            )

    def _validate_descriptor_field_schema(self, frame: dict[str, Any]) -> None:
        status = frame.get("status")
        if status == "ready":
            oversized_fields_present = not (
                _SESSION_SHARDS_OVERSIZED_DESCRIPTOR_FIELDS.isdisjoint(frame)
            )
            expected_fields = _SESSION_SHARDS_DESCRIPTOR_FIELDS
            if oversized_fields_present:
                expected_fields |= _SESSION_SHARDS_OVERSIZED_DESCRIPTOR_FIELDS
            self._require_exact_fields(frame, expected_fields, "ready descriptor")
            return
        if status != "gap":
            raise RuntimeError(
                "remote session-shards descriptor has an unsupported closed-schema status"
            )
        reason = frame.get("gap_reason")
        expected_fields = (
            _SESSION_SHARDS_DESCRIPTOR_FIELDS | _SESSION_SHARDS_GAP_DESCRIPTOR_FIELDS
        )
        if reason == "record_processing_budget_exceeded":
            expected_fields |= _SESSION_SHARDS_PROCESSING_CEILING_FIELDS
        elif reason != "invalid_json":
            raise RuntimeError(
                "remote session-shards gap descriptor has an unsupported closed-schema reason"
            )
        self._require_exact_fields(frame, expected_fields, "gap descriptor")

    def _validate_gap_field_schema(self, frame: dict[str, Any]) -> None:
        reason = frame.get("reason")
        expected_fields = _SESSION_SHARDS_GAP_FIELDS
        if reason == "record_processing_budget_exceeded":
            expected_fields |= _SESSION_SHARDS_PROCESSING_CEILING_FIELDS
        elif reason != "invalid_json":
            raise RuntimeError(
                "remote session-shards gap frame has an unsupported closed-schema reason"
            )
        self._require_exact_fields(frame, expected_fields, "gap frame")

    def _validate_terminal_field_schema(self, terminal: dict[str, Any]) -> None:
        assert self.stream_meta is not None
        if self.stream_meta.get("mode") == "descriptors":
            if terminal.get("reason") not in ("eof", "max_shards"):
                raise RuntimeError(
                    "remote session-shards descriptor terminal has an unsupported closed-schema reason"
                )
            self._require_exact_fields(
                terminal,
                _SESSION_SHARDS_DESCRIPTOR_TERMINAL_FIELDS,
                "descriptor terminal",
            )
            return
        if terminal.get("reason") != "range_complete":
            raise RuntimeError(
                'remote session-shards record terminal reason must be "range_complete"'
            )
        self._require_exact_fields(
            terminal,
            _SESSION_SHARDS_RECORD_TERMINAL_FIELDS,
            "record terminal",
        )

    def _expected_record_start(self) -> int:
        cursor = self.request.get("resume_cursor")
        if cursor is None:
            return 0
        try:
            _, _, value = _session_shards_decode_resume_cursor(str(cursor))
        except ValueError as exc:
            raise RuntimeError(
                "remote session-shards request has an invalid resume cursor"
            ) from exc
        if value["source_token"] != self.request.get("source_token") or value[
            "byte_offset"
        ] != int(self.request.get("byte_start", 0)):
            raise RuntimeError(
                "remote session-shards request resume cursor is not bound to its coordinates"
            )
        return int(value["next_record_index"])

    @staticmethod
    def _validate_cursor_coordinates(
        cursor: object,
        *,
        source_token: object,
        byte_offset: int,
        record_index: int,
    ) -> None:
        if not isinstance(cursor, str):
            raise RuntimeError("remote session-shards cursor is invalid")
        try:
            _, _, value = _session_shards_decode_resume_cursor(cursor)
        except ValueError as exc:
            raise RuntimeError("remote session-shards cursor is invalid") from exc
        if (
            value["source_token"] != source_token
            or value["byte_offset"] != byte_offset
            or value["next_record_index"] != record_index
        ):
            raise RuntimeError(
                "remote session-shards cursor coordinates are inconsistent"
            )

    def _expected_request_binding(self) -> str:
        byte_end = self.request.get("byte_end")
        return _session_shards_request_binding(
            rollout=str(self.request["rollout"]),
            mode=str(self.request["emit"]),
            source_token=(
                None
                if self.request.get("source_token") is None
                else str(self.request["source_token"])
            ),
            byte_start=int(self.request.get("byte_start", 0)),
            byte_end=None if byte_end is None else int(byte_end),
            shard_bytes=int(self.request["shard_bytes"]),
            max_shards=int(self.request["max_shards"]),
            record_processing_budget_bytes=int(
                self.request.get(
                    "record_processing_budget_bytes",
                    DEFAULT_SESSION_RECORD_PROCESSING_BUDGET_BYTES,
                )
            ),
            resume_cursor=(
                None
                if self.request.get("resume_cursor") is None
                else str(self.request["resume_cursor"])
            ),
        )

    def _validate_stream_meta_request(self, frame: dict[str, Any]) -> None:
        byte_end = self.request.get("byte_end")
        expected_source_token = self.request.get("source_token")
        expected = {
            "schema": SESSION_SHARDS_SCHEMA,
            "mode": self.request.get("emit"),
            "request_rollout": self.request.get("rollout"),
            "request_source_token": expected_source_token,
            "request_resume_cursor": self.request.get("resume_cursor"),
            "request_binding": self._expected_request_binding(),
            "byte_start": int(self.request.get("byte_start", 0)),
            "byte_end": None if byte_end is None else int(byte_end),
            "record_start": self._expected_record_start(),
            "shard_bytes": int(self.request["shard_bytes"]),
            "max_shards": int(self.request["max_shards"]),
            "record_processing_budget_bytes": int(
                self.request.get(
                    "record_processing_budget_bytes",
                    DEFAULT_SESSION_RECORD_PROCESSING_BUDGET_BYTES,
                )
            ),
            "fixed_memory_envelope_bytes": (MIN_SESSION_RECORD_PROCESSING_BUDGET_BYTES),
            "hard_record_processing_ceiling_bytes": (
                HARD_SESSION_RECORD_PROCESSING_CEILING_BYTES
            ),
            "hard_record_scan_ceiling_bytes": (HARD_SESSION_RECORD_SCAN_CEILING_BYTES),
            "record_fragment_bytes": SESSION_SHARDS_RECORD_FRAGMENT_BYTES,
            "json_nesting_depth_limit": SESSION_SHARDS_MAX_JSON_NESTING_DEPTH,
            "max_remote_frame_chars": MAX_SESSION_SHARDS_FRAME_CHARS,
            "protocol_features": list(SESSION_SHARDS_PROTOCOL_FEATURES),
        }
        if any(
            key not in frame
            or type(frame[key]) is not type(value)
            or frame[key] != value
            for key, value in expected.items()
        ):
            raise RuntimeError(
                "remote session-shards stream_meta does not match the request"
            )
        response_source_token = frame.get("source_token")
        if (
            not isinstance(response_source_token, str)
            or re.fullmatch(
                re.escape(SESSION_SHARDS_SOURCE_TOKEN_PREFIX) + r"[0-9a-f]{64}",
                response_source_token,
            )
            is None
            or (
                expected_source_token is not None
                and response_source_token != expected_source_token
            )
        ):
            raise RuntimeError(
                "remote session-shards source token does not match the request"
            )
        source_bytes = self._integer(frame, "source_bytes")
        minimum_source_bytes = int(self.request.get("byte_start", 0))
        if byte_end is not None:
            minimum_source_bytes = max(minimum_source_bytes, int(byte_end))
        if source_bytes < minimum_source_bytes:
            raise RuntimeError(
                "remote session-shards source size does not cover the request"
            )

    def _validate_response_binding(self, frame: dict[str, Any]) -> None:
        if self.stream_meta is None:
            raise RuntimeError("remote session-shards missing stream_meta")
        if (
            frame.get("schema") != SESSION_SHARDS_SCHEMA
            or frame.get("mode") != self.stream_meta.get("mode")
            or frame.get("source_token") != self.stream_meta.get("source_token")
            or frame.get("request_binding") != self.stream_meta.get("request_binding")
        ):
            raise RuntimeError(
                "remote session-shards frame does not match the request binding"
            )

    @staticmethod
    def _integer(
        frame: dict[str, Any],
        key: str,
        *,
        minimum: int = 0,
    ) -> int:
        value = frame.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise RuntimeError(f"remote session-shards frame has invalid {key}")
        return value

    @staticmethod
    def _decode_payload(frame: dict[str, Any], key: str) -> bytes:
        if frame.get("record_encoding") != "base64":
            raise RuntimeError("remote session-shards record encoding must be base64")
        encoded = frame.get(key)
        if not isinstance(encoded, str):
            raise RuntimeError(f"remote session-shards frame has invalid {key}")
        try:
            return base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RuntimeError(
                f"remote session-shards frame has invalid {key}"
            ) from exc

    @staticmethod
    def _validate_commitment(payload: bytes, commitment: object) -> None:
        if commitment != _session_shards_content_commitment(payload):
            raise RuntimeError("remote session-shards payload commitment mismatch")

    def accept(self, frame: dict[str, Any]) -> None:
        kind = frame.get("kind")
        if kind == "stream_meta":
            if self.stream_meta is not None:
                raise RuntimeError(
                    "remote session-shards emitted duplicate stream_meta"
                )
            self._require_exact_fields(
                frame,
                _SESSION_SHARDS_STREAM_META_FIELDS,
                "stream_meta",
            )
            self._validate_stream_meta_request(frame)
            self.stream_meta = frame
            if frame.get("mode") not in ("descriptors", "records"):
                raise RuntimeError(
                    "remote session-shards stream_meta has an invalid mode"
                )
            self.next_byte = self._integer(frame, "byte_start")
            self.next_record = self._integer(frame, "record_start")
            if frame.get("mode") == "records":
                self._integer(frame, "byte_end")
            else:
                self._integer(frame, "source_bytes")
            return

        if self.stream_meta is None:
            raise RuntimeError("remote session-shards emitted data before stream_meta")
        if self.stream_meta.get("mode") != "records":
            if kind != "shard":
                raise RuntimeError(
                    "remote session-shards descriptor stream has an invalid frame"
                )
            self._accept_descriptor(frame)
            return
        if kind == "record":
            self._accept_record(frame)
        elif kind == "record_fragment":
            self._accept_fragment(frame)
        elif kind == "gap":
            self._accept_gap(frame)
        else:
            raise RuntimeError(
                "remote session-shards record stream has an invalid frame"
            )

    def _accept_descriptor(self, frame: dict[str, Any]) -> None:
        self._validate_descriptor_field_schema(frame)
        self._validate_response_binding(frame)
        if self.next_byte is None or self.next_record is None:
            raise RuntimeError(
                "remote session-shards descriptor accounting is uninitialized"
            )
        byte_start = self._integer(frame, "byte_start")
        byte_end = self._integer(frame, "byte_end", minimum=byte_start + 1)
        record_start = self._integer(frame, "record_start")
        record_end = self._integer(
            frame,
            "record_end",
            minimum=record_start + 1,
        )
        record_count = self._integer(frame, "record_count", minimum=1)
        page_shard_index = self._integer(frame, "page_shard_index")
        status = frame.get("status")
        resume_cursor = frame.get("resume_cursor")
        assert self.stream_meta is not None
        max_shards = self._integer(self.stream_meta, "max_shards", minimum=1)
        source_bytes = self._integer(self.stream_meta, "source_bytes")
        shard_bytes = self._integer(self.stream_meta, "shard_bytes", minimum=1)
        descriptor_bytes = byte_end - byte_start
        if (
            byte_start != self.next_byte
            or byte_end > source_bytes
            or record_start != self.next_record
            or record_count != record_end - record_start
            or page_shard_index != self.emitted_shards
            or page_shard_index >= max_shards
            or status not in ("ready", "gap")
        ):
            raise RuntimeError("remote session-shards descriptors are not contiguous")
        self._validate_cursor_coordinates(
            resume_cursor,
            source_token=frame.get("source_token"),
            byte_offset=byte_start,
            record_index=record_start,
        )
        if status == "gap":
            reason = frame.get("gap_reason")
            if frame.get("byte_count") != descriptor_bytes:
                raise RuntimeError(
                    "remote session-shards gap descriptor byte count mismatch"
                )
            if reason == "record_processing_budget_exceeded":
                self._validate_processing_gap_metadata(
                    frame,
                    byte_count=descriptor_bytes,
                )
        elif "oversized_record" in frame:
            processing_budget = self._integer(
                self.stream_meta,
                "record_processing_budget_bytes",
                minimum=shard_bytes,
            )
            if (
                frame.get("oversized_record") is not True
                or frame.get("record_transport") != "base64_fragments"
                or frame.get("record_fragment_bytes")
                != self.stream_meta.get("record_fragment_bytes")
                or frame.get("record_processing_budget_bytes") != processing_budget
                or descriptor_bytes <= shard_bytes
                or descriptor_bytes > processing_budget
                or record_count != 1
            ):
                raise RuntimeError(
                    "remote session-shards oversized descriptor is inconsistent"
                )
        elif descriptor_bytes > shard_bytes:
            raise RuntimeError(
                "remote session-shards ready descriptor exceeds shard_bytes "
                "without the oversized record contract"
            )
        self.next_byte = byte_end
        self.next_record = record_end
        self.emitted_shards += 1

    def _validate_source_token(self, frame: dict[str, Any]) -> None:
        self._validate_response_binding(frame)

    def _validate_whole_record_range(
        self,
        frame: dict[str, Any],
    ) -> tuple[int, int, int, int]:
        if self.next_byte is None or self.next_record is None:
            raise RuntimeError(
                "remote session-shards record accounting is uninitialized"
            )
        byte_start = self._integer(frame, "byte_start")
        byte_end = self._integer(frame, "byte_end", minimum=byte_start + 1)
        byte_count = self._integer(frame, "byte_count", minimum=1)
        record_start = self._integer(frame, "record_start")
        record_end = self._integer(frame, "record_end", minimum=record_start)
        delimiter_bytes = self._integer(frame, "delimiter_bytes")
        if (
            byte_start != self.next_byte
            or byte_count != byte_end - byte_start
            or record_start != self.next_record
            or record_end != record_start + 1
            or delimiter_bytes not in (0, 1, 2)
            or delimiter_bytes > byte_count
        ):
            raise RuntimeError(
                "remote session-shards record coordinates are not contiguous"
            )
        return byte_start, byte_end, byte_count, record_end

    def _validate_record_delimiter(
        self,
        payload_tail: bytes,
        *,
        delimiter_bytes: int,
        record_byte_end: int,
    ) -> None:
        actual_delimiter_bytes = _session_shards_payload_delimiter_bytes(payload_tail)
        assert self.stream_meta is not None
        source_bytes = self._integer(self.stream_meta, "source_bytes")
        if actual_delimiter_bytes != delimiter_bytes or (
            delimiter_bytes == 0 and record_byte_end != source_bytes
        ):
            raise RuntimeError(
                "remote session-shards record delimiter does not match its payload"
            )

    def _accept_record(self, frame: dict[str, Any]) -> None:
        if self.fragment_state is not None:
            raise RuntimeError("remote session-shards interrupted a fragmented record")
        self._require_exact_fields(
            frame,
            _SESSION_SHARDS_RECORD_FIELDS,
            "record frame",
        )
        self._validate_source_token(frame)
        _, byte_end, byte_count, record_end = self._validate_whole_record_range(frame)
        assert self.stream_meta is not None
        shard_bytes = self._integer(self.stream_meta, "shard_bytes", minimum=1)
        if byte_count > shard_bytes:
            raise RuntimeError(
                "remote session-shards oversized record was not fragmented"
            )
        payload = self._decode_payload(frame, "record_b64")
        if len(payload) != byte_count:
            raise RuntimeError("remote session-shards record byte count mismatch")
        self._validate_commitment(payload, frame.get("record_commitment"))
        self._validate_record_delimiter(
            payload[-2:],
            delimiter_bytes=self._integer(frame, "delimiter_bytes"),
            record_byte_end=byte_end,
        )
        self.accounting_hasher.update(_session_shards_accounting_bytes(frame))
        self.next_byte = byte_end
        self.next_record = record_end
        self.emitted_records += 1
        self.emitted_record_bytes += byte_count

    def _validate_processing_gap_metadata(
        self,
        frame: dict[str, Any],
        *,
        byte_count: int,
    ) -> None:
        budget = self._integer(frame, "record_processing_budget_bytes", minimum=1)
        hard_ceiling = self._integer(
            frame,
            "hard_record_processing_ceiling_bytes",
            minimum=budget,
        )
        processing_kind = frame.get("processing_ceiling_kind")
        processing_limit = self._integer(
            frame,
            "processing_ceiling_limit",
            minimum=1,
        )
        processing_observed = self._integer(
            frame,
            "processing_ceiling_observed",
            minimum=processing_limit + 1,
        )
        assert self.stream_meta is not None
        if (
            hard_ceiling < budget
            or budget != self.stream_meta.get("record_processing_budget_bytes")
            or hard_ceiling
            != self.stream_meta.get("hard_record_processing_ceiling_bytes")
        ):
            raise RuntimeError(
                "remote session-shards processing-budget gap is inconsistent"
            )
        if processing_kind == "record_bytes":
            valid_processing_ceiling = (
                processing_limit == budget
                and processing_observed == byte_count
                and byte_count > budget
            )
        elif processing_kind == "json_nesting_depth":
            valid_processing_ceiling = (
                processing_limit == self.stream_meta.get("json_nesting_depth_limit")
                and processing_observed == processing_limit + 1
                and byte_count <= budget
            )
        else:
            valid_processing_ceiling = False
        if not valid_processing_ceiling:
            raise RuntimeError(
                "remote session-shards processing-budget gap is inconsistent"
            )

    def _accept_gap(self, frame: dict[str, Any]) -> None:
        if self.fragment_state is not None:
            raise RuntimeError("remote session-shards interrupted a fragmented record")
        self._validate_gap_field_schema(frame)
        self._validate_source_token(frame)
        _, byte_end, byte_count, record_end = self._validate_whole_record_range(frame)
        reason = frame.get("reason")
        if reason == "record_processing_budget_exceeded":
            self._validate_processing_gap_metadata(frame, byte_count=byte_count)
        self.accounting_hasher.update(_session_shards_accounting_bytes(frame))
        self.next_byte = byte_end
        self.next_record = record_end
        self.emitted_gaps += 1
        self.emitted_gap_bytes += byte_count

    def _accept_fragment(self, frame: dict[str, Any]) -> None:
        self._require_exact_fields(
            frame,
            _SESSION_SHARDS_FRAGMENT_FIELDS,
            "record_fragment frame",
        )
        self._validate_source_token(frame)
        if self.next_byte is None or self.next_record is None:
            raise RuntimeError(
                "remote session-shards record accounting is uninitialized"
            )
        byte_start = self._integer(frame, "byte_start")
        byte_end = self._integer(frame, "byte_end", minimum=byte_start)
        byte_count = self._integer(frame, "byte_count", minimum=1)
        record_byte_start = self._integer(frame, "record_byte_start")
        record_byte_end = self._integer(
            frame,
            "record_byte_end",
            minimum=record_byte_start,
        )
        record_byte_count = self._integer(
            frame,
            "record_byte_count",
            minimum=1,
        )
        record_start = self._integer(frame, "record_start")
        record_end = self._integer(frame, "record_end", minimum=record_start)
        fragment_index = self._integer(frame, "fragment_index")
        fragment_count = self._integer(frame, "fragment_count", minimum=1)
        delimiter_bytes = self._integer(frame, "delimiter_bytes")
        assert self.stream_meta is not None
        shard_bytes = self._integer(self.stream_meta, "shard_bytes", minimum=1)
        fragment_bytes = self._integer(
            self.stream_meta,
            "record_fragment_bytes",
            minimum=1,
        )
        processing_budget = self._integer(
            self.stream_meta,
            "record_processing_budget_bytes",
            minimum=shard_bytes,
        )
        expected_fragment_count = (
            record_byte_count + fragment_bytes - 1
        ) // fragment_bytes
        expected_byte_start = record_byte_start + fragment_index * fragment_bytes
        expected_byte_end = min(
            expected_byte_start + fragment_bytes,
            record_byte_end,
        )
        if (
            byte_count != byte_end - byte_start
            or record_byte_count != record_byte_end - record_byte_start
            or record_byte_count <= shard_bytes
            or record_byte_count > processing_budget
            or record_start != self.next_record
            or record_end != record_start + 1
            or delimiter_bytes not in (0, 1, 2)
            or delimiter_bytes > record_byte_count
            or fragment_index >= fragment_count
            or fragment_count != expected_fragment_count
            or byte_start != expected_byte_start
            or byte_end != expected_byte_end
        ):
            raise RuntimeError(
                "remote session-shards fragment coordinates are inconsistent"
            )

        if fragment_index == 0:
            if self.fragment_state is not None or record_byte_start != self.next_byte:
                raise RuntimeError(
                    "remote session-shards fragment sequence did not start contiguously"
                )
            self.fragment_state = {
                "next_index": 0,
                "next_byte": record_byte_start,
                "record_byte_start": record_byte_start,
                "record_byte_end": record_byte_end,
                "record_byte_count": record_byte_count,
                "record_start": record_start,
                "record_end": record_end,
                "fragment_count": fragment_count,
                "delimiter_bytes": delimiter_bytes,
                "record_commitment": frame.get("record_commitment"),
                "record_hasher": hashlib.sha256(),
                "record_tail": b"",
            }
        state = self.fragment_state
        if state is None:
            raise RuntimeError(
                "remote session-shards fragment sequence is missing its first frame"
            )
        stable_fields = (
            "record_byte_start",
            "record_byte_end",
            "record_byte_count",
            "record_start",
            "record_end",
            "fragment_count",
            "delimiter_bytes",
            "record_commitment",
        )
        if any(frame.get(key) != state[key] for key in stable_fields) or (
            fragment_index != state["next_index"] or byte_start != state["next_byte"]
        ):
            raise RuntimeError(
                "remote session-shards fragment sequence is not stable and contiguous"
            )

        payload = self._decode_payload(frame, "fragment_b64")
        if len(payload) != byte_count:
            raise RuntimeError("remote session-shards fragment byte count mismatch")
        self._validate_commitment(payload, frame.get("fragment_commitment"))
        state["record_hasher"].update(payload)
        state["record_tail"] = (state["record_tail"] + payload)[-2:]
        state["next_index"] = fragment_index + 1
        state["next_byte"] = byte_end
        self.accounting_hasher.update(_session_shards_accounting_bytes(frame))
        self.emitted_fragments += 1
        self.emitted_fragment_bytes += byte_count

        if fragment_index + 1 == fragment_count:
            record_commitment = "sha256:" + state["record_hasher"].hexdigest()
            if (
                byte_end != record_byte_end
                or record_commitment != state["record_commitment"]
            ):
                raise RuntimeError(
                    "remote session-shards fragmented record failed reassembly"
                )
            self._validate_record_delimiter(
                state["record_tail"],
                delimiter_bytes=delimiter_bytes,
                record_byte_end=record_byte_end,
            )
            self.next_byte = record_byte_end
            self.next_record = record_end
            self.emitted_records += 1
            self.emitted_record_bytes += record_byte_count
            self.fragment_state = None

    def finish(self, terminal: dict[str, Any]) -> None:
        if self.stream_meta is None:
            raise RuntimeError("remote session-shards missing stream_meta")
        self._validate_terminal_field_schema(terminal)
        self._validate_response_binding(terminal)
        if terminal.get("mode") != self.stream_meta.get("mode"):
            raise RuntimeError("remote session-shards terminal mode mismatch")
        if self.stream_meta.get("mode") != "records":
            self._finish_descriptors(terminal)
            return
        if self.fragment_state is not None:
            raise RuntimeError("remote session-shards ended inside a fragmented record")
        if self.next_byte is None or self.next_record is None:
            raise RuntimeError(
                "remote session-shards record accounting is uninitialized"
            )
        byte_start = self._integer(terminal, "byte_start")
        byte_end = self._integer(terminal, "byte_end", minimum=byte_start)
        record_start = self._integer(terminal, "record_start")
        record_end = self._integer(
            terminal,
            "record_end",
            minimum=record_start,
        )
        expected_counts = {
            "emitted_records": self.emitted_records,
            "emitted_gaps": self.emitted_gaps,
            "emitted_fragments": self.emitted_fragments,
            "emitted_record_bytes": self.emitted_record_bytes,
            "emitted_gap_bytes": self.emitted_gap_bytes,
            "emitted_fragment_bytes": self.emitted_fragment_bytes,
        }
        if any(terminal.get(key) != value for key, value in expected_counts.items()):
            raise RuntimeError(
                "remote session-shards terminal counters do not conserve the stream"
            )
        if (
            terminal.get("complete") is not True
            or byte_start != self.stream_meta.get("byte_start")
            or byte_end != self.stream_meta.get("byte_end")
            or byte_end != self.next_byte
            or record_start != self.stream_meta.get("record_start")
            or record_end != self.next_record
            or self.emitted_record_bytes + self.emitted_gap_bytes
            != byte_end - byte_start
            or self.emitted_records + self.emitted_gaps != record_end - record_start
        ):
            raise RuntimeError(
                "remote session-shards terminal coordinates do not conserve the stream"
            )
        proof = terminal.get("conservation_proof")
        if not isinstance(proof, dict):
            raise RuntimeError(
                "remote session-shards terminal is missing its conservation proof"
            )
        self._require_exact_fields(
            proof,
            _SESSION_SHARDS_CONSERVATION_PROOF_FIELDS,
            "conservation proof",
        )
        expected_proof = {
            "schema": "session-shards-conservation-v1",
            "source_token": self.stream_meta.get("source_token"),
            "request_binding": self.stream_meta.get("request_binding"),
            "byte_start": byte_start,
            "byte_end": byte_end,
            "byte_count": byte_end - byte_start,
            "accounted_byte_count": (
                self.emitted_record_bytes + self.emitted_gap_bytes
            ),
            "record_start": record_start,
            "record_end": record_end,
            "record_count": record_end - record_start,
            "accounted_record_count": self.emitted_records + self.emitted_gaps,
            "accounting_commitment": ("sha256:" + self.accounting_hasher.hexdigest()),
        }
        if any(proof.get(key) != value for key, value in expected_proof.items()):
            raise RuntimeError(
                "remote session-shards terminal conservation proof mismatch"
            )

    def _finish_descriptors(self, terminal: dict[str, Any]) -> None:
        if (
            self.stream_meta is None
            or self.next_byte is None
            or self.next_record is None
        ):
            raise RuntimeError(
                "remote session-shards descriptor accounting is uninitialized"
            )
        byte_start = self._integer(terminal, "byte_start")
        byte_end = self._integer(terminal, "byte_end", minimum=byte_start)
        record_start = self._integer(terminal, "record_start")
        record_end = self._integer(
            terminal,
            "record_end",
            minimum=record_start,
        )
        complete = terminal.get("complete")
        max_shards = self._integer(self.stream_meta, "max_shards", minimum=1)
        if (
            not isinstance(complete, bool)
            or terminal.get("emitted_shards") != self.emitted_shards
            or byte_start != self.stream_meta.get("byte_start")
            or byte_end != self.next_byte
            or record_start != self.stream_meta.get("record_start")
            or record_end != self.next_record
            or terminal.get("accounted_byte_count") != byte_end - byte_start
            or terminal.get("accounted_record_count") != record_end - record_start
        ):
            raise RuntimeError(
                "remote session-shards descriptor terminal does not conserve the page"
            )
        if complete:
            if (
                terminal.get("reason") != "eof"
                or byte_end != self.stream_meta.get("source_bytes")
                or terminal.get("next_byte_start") is not None
                or terminal.get("next_record_start") is not None
                or terminal.get("next_resume_cursor") is not None
            ):
                raise RuntimeError(
                    "remote session-shards completed descriptor page is inconsistent"
                )
        elif (
            terminal.get("reason") != "max_shards"
            or self.emitted_shards != max_shards
            or terminal.get("next_byte_start") != byte_end
            or terminal.get("next_record_start") != record_end
            or not isinstance(terminal.get("next_resume_cursor"), str)
        ):
            raise RuntimeError(
                "remote session-shards paginated descriptor cursor is inconsistent"
            )
        if not complete:
            self._validate_cursor_coordinates(
                terminal.get("next_resume_cursor"),
                source_token=terminal.get("source_token"),
                byte_offset=byte_end,
                record_index=record_end,
            )


class SessionMetaRolloutError(ValueError):
    def __init__(self, error: str, *, rollout: str | None = None) -> None:
        super().__init__(error)
        self.error = error
        self.rollout = rollout


REMOTE_PREFLIGHT_SCRIPT = r"""
hostname_value="$(hostname 2>/dev/null || printf unknown)"
user_value="$(id -un 2>/dev/null || printf unknown)"
printf 'hostname=%s\n' "$hostname_value"
printf 'user=%s\n' "$user_value"
printf 'home=%s\n' "$HOME"
codex_root="${CODEX_REMOTE_ROOT:-$HOME/.codex}"
printf 'codex_root=%s\n' "$codex_root"
if [ -d "$codex_root" ]; then
  echo 'codex=present'
else
  echo 'codex=missing'
fi
if command -v rg >/dev/null 2>&1; then
  echo 'rg=present'
else
  echo 'rg=missing'
fi
if command -v python3 >/dev/null 2>&1; then
  echo 'python3=present'
else
  echo 'python3=missing'
fi
"""


def _error(message: str) -> int:
    print(f"error={message}", file=sys.stderr)
    return 2


def _parse_date(value: str) -> dt.date:
    try:
        return dt.datetime.strptime(value, DATE_FORMAT).date()
    except ValueError as exc:
        raise ValueError(f"invalid date: {value}; expected YYYY/MM/DD") from exc


def _iter_dates(start: dt.date, end: dt.date) -> list[dt.date]:
    if end < start:
        raise ValueError("--to must be on or after --from")
    current = start
    dates: list[dt.date] = []
    while current <= end:
        dates.append(current)
        current += dt.timedelta(days=1)
    if len(dates) > MAX_SESSION_META_DATE_COUNT:
        raise ValueError(
            f"date range must stay within {MAX_SESSION_META_DATE_COUNT} days"
        )
    return dates


def _resolve_dates(args: argparse.Namespace) -> list[dt.date]:
    explicit_dates = [_parse_date(value) for value in args.date]
    if explicit_dates and (args.from_date or args.to_date):
        raise ValueError("--date cannot be combined with --from/--to")
    if explicit_dates:
        unique_dates = sorted(dict.fromkeys(explicit_dates))
        if len(unique_dates) > MAX_SESSION_META_DATE_COUNT:
            raise ValueError(
                f"date selection must stay within {MAX_SESSION_META_DATE_COUNT} days"
            )
        return unique_dates
    if args.from_date or args.to_date:
        if not args.from_date or not args.to_date:
            raise ValueError("--from and --to must be provided together")
        return _iter_dates(_parse_date(args.from_date), _parse_date(args.to_date))
    raise ValueError("at least one --date or a --from/--to range is required")


def _resolve_hosts(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    hosts: list[str] = []
    for value in values:
        if value not in HOSTS:
            raise ValueError(f"host not allowed: {value}")
        canonical = str(HOSTS[value]["label"])
        if canonical not in HOSTS:
            raise ValueError(
                f"host table misconfigured: canonical host missing for {value}: {canonical}"
            )
        if canonical in seen:
            continue
        seen.add(canonical)
        hosts.append(canonical)
    if not hosts:
        raise ValueError("at least one --host is required")
    return hosts


def _local_codex_root() -> pathlib.Path:
    return pathlib.Path.home() / ".codex"


def _task_output_root(workspace_root: pathlib.Path | None = None) -> pathlib.Path:
    root = (
        workspace_root.resolve()
        if workspace_root is not None
        else pathlib.Path.cwd().resolve()
    )
    return root / TASK_OUTPUT_RELATIVE_DIR


def _reject_symlink_components(path: pathlib.Path) -> None:
    if not path.is_absolute():
        raise ValueError("output path must be absolute after normalization")
    current = pathlib.Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            current_stat = current.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(current_stat.st_mode):
            raise ValueError("output path uses a symlink component")
        if current != path and not stat.S_ISDIR(current_stat.st_mode):
            raise ValueError("output path ancestor is not a directory")


def _validate_output_path(candidate: pathlib.Path, root: pathlib.Path) -> pathlib.Path:
    resolved_root = root.resolve(strict=False)
    resolved_candidate = candidate.resolve(strict=False)
    if not _path_is_relative_to(resolved_candidate, resolved_root):
        raise ValueError(f"output path must stay under {resolved_root}")
    _reject_symlink_components(candidate)
    return resolved_candidate


def _resolve_output_path(
    output: str, *, workspace_root: pathlib.Path | None = None
) -> pathlib.Path:
    raw_path = pathlib.Path(output).expanduser()
    if any(part == ".." for part in raw_path.parts):
        raise ValueError("output path must not contain ..")
    workspace = (
        workspace_root.resolve()
        if workspace_root is not None
        else pathlib.Path.cwd().resolve()
    )
    task_output_root = _task_output_root(workspace_root)
    tmp_alias_root = pathlib.Path("/tmp")
    tmp_root = pathlib.Path("/tmp").resolve()
    if not raw_path.is_absolute():
        task_output_parts = TASK_OUTPUT_RELATIVE_DIR.parts
        if raw_path.parts[: len(task_output_parts)] == task_output_parts:
            return _validate_output_path(workspace / raw_path, task_output_root)
        return _validate_output_path(task_output_root / raw_path, task_output_root)
    if _path_is_relative_to(raw_path, tmp_alias_root):
        raw_path = tmp_root / raw_path.relative_to(tmp_alias_root)
    for root in (task_output_root, tmp_root):
        if _path_is_relative_to(
            raw_path.resolve(strict=False), root.resolve(strict=False)
        ):
            return _validate_output_path(raw_path, root)
    raise ValueError(
        f"output path must stay under {task_output_root.resolve(strict=False)} or {tmp_root}"
    )


def _resolve_rollout_relative_path(value: str) -> pathlib.PurePosixPath:
    candidate = pathlib.PurePosixPath(value)
    normalized = candidate.as_posix()
    if not (
        ACTIVE_ROLLOUT_RELATIVE_RE.fullmatch(normalized)
        or ARCHIVED_ROLLOUT_RELATIVE_RE.fullmatch(normalized)
    ):
        raise ValueError(
            "rollout path must match sessions/YYYY/MM/DD/rollout-*.jsonl or archived_sessions/rollout-*.jsonl"
        )
    return candidate


def _resolve_session_shards_rollout_relative_path(
    value: str,
) -> pathlib.PurePosixPath:
    candidate = pathlib.PurePosixPath(value)
    normalized = candidate.as_posix()
    if not (
        SESSION_SHARDS_ACTIVE_ROLLOUT_RELATIVE_RE.fullmatch(normalized)
        or SESSION_SHARDS_ARCHIVED_ROLLOUT_RELATIVE_RE.fullmatch(normalized)
        or ROOT_ROLLOUT_RELATIVE_RE.fullmatch(normalized)
    ):
        raise ValueError(
            "rollout path must match sessions/YYYY/MM/DD/rollout-*.jsonl, "
            "archived_sessions/rollout-*.jsonl, or rollout-*.jsonl"
        )
    return candidate


def _path_is_relative_to(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _resolve_safe_codex_root(codex_root: pathlib.Path) -> pathlib.Path:
    expanded_root = codex_root.expanduser()
    root_stat = expanded_root.lstat()
    if stat.S_ISLNK(root_stat.st_mode):
        raise ValueError("Codex root is a symlink")
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ValueError("Codex root is not a directory")
    return expanded_root.resolve(strict=True)


def _safe_relative_path(
    codex_root: pathlib.Path,
    relative_path: pathlib.PurePosixPath,
    *,
    expect_directory: bool = False,
    expect_regular_file: bool = False,
) -> pathlib.Path:
    root = _resolve_safe_codex_root(codex_root)
    target = root
    parts = relative_path.parts
    for index, part in enumerate(parts):
        if part in ("", ".", ".."):
            raise ValueError("path must stay under Codex root")
        target = target / part
        target_stat = target.lstat()
        if stat.S_ISLNK(target_stat.st_mode):
            raise ValueError("path uses a symlink ancestor")
        is_last = index == len(parts) - 1
        if not is_last:
            if not stat.S_ISDIR(target_stat.st_mode):
                raise ValueError("path ancestor is not a directory")
        elif expect_directory and not stat.S_ISDIR(target_stat.st_mode):
            raise ValueError("path is not a directory")
        elif expect_regular_file and not stat.S_ISREG(target_stat.st_mode):
            raise ValueError("rollout path is not a regular file")
    target_resolved = target.resolve(strict=True)
    if not _path_is_relative_to(target_resolved, root):
        raise ValueError("path escapes Codex root")
    return target_resolved


def _safe_rollout_path(
    codex_root: pathlib.Path, rollout_relative_path: pathlib.PurePosixPath
) -> pathlib.Path:
    return _safe_relative_path(
        codex_root, rollout_relative_path, expect_regular_file=True
    )


def _safe_directory_path(
    codex_root: pathlib.Path, relative_path: pathlib.PurePosixPath
) -> pathlib.Path:
    return _safe_relative_path(codex_root, relative_path, expect_directory=True)


def _open_local_rollout_text(
    codex_root: pathlib.Path, rollout_relative_path: pathlib.PurePosixPath
):
    target = _safe_rollout_path(codex_root, rollout_relative_path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(target), flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("rollout path is not a regular file")
        handle = os.fdopen(fd, "rb")
        fd = -1
        return handle
    finally:
        if fd != -1:
            os.close(fd)


def _read_local_rollout_bytes(
    codex_root: pathlib.Path,
    rollout_relative_path: pathlib.PurePosixPath,
    *,
    max_bytes: int,
) -> bytes:
    target = _safe_rollout_path(codex_root, rollout_relative_path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(target), flags)
    try:
        stat_result = os.fstat(fd)
        if not stat.S_ISREG(stat_result.st_mode):
            raise ValueError("rollout path is not a regular file")
        size = stat_result.st_size
        if max_bytes and size > max_bytes:
            raise ValueError(f"rollout too large: {size} bytes > {max_bytes}")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            return handle.read()
    finally:
        if fd != -1:
            os.close(fd)


def _read_local_rollout_byte_range(
    codex_root: pathlib.Path,
    rollout_relative_path: pathlib.PurePosixPath,
    *,
    byte_start: int,
    byte_end: int,
    max_bytes: int,
) -> bytes:
    if byte_start < 0:
        raise ValueError("--byte-start must be non-negative")
    if byte_end <= byte_start:
        raise ValueError("--byte-end must be greater than --byte-start")
    length = byte_end - byte_start
    if length > max_bytes:
        raise ValueError(f"chunk too large: {length} bytes > {max_bytes}")
    target = _safe_rollout_path(codex_root, rollout_relative_path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(target), flags)
    try:
        stat_result = os.fstat(fd)
        if not stat.S_ISREG(stat_result.st_mode):
            raise ValueError("rollout path is not a regular file")
        if byte_end > stat_result.st_size:
            raise ValueError(
                f"--byte-end exceeds rollout size: {byte_end} > {stat_result.st_size}"
            )
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            handle.seek(byte_start)
            return handle.read(length)
    finally:
        if fd != -1:
            os.close(fd)


def _write_private_bytes(output: pathlib.Path, data: bytes) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        target_stat = output.lstat()
    except FileNotFoundError:
        target_stat = None
    if target_stat is not None and not stat.S_ISREG(target_stat.st_mode):
        raise ValueError("output path exists and is not a regular file")

    last_error: FileExistsError | None = None
    for attempt in range(100):
        temp_path = output.with_name(f".{output.name}.tmp-{os.getpid()}-{attempt}")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(str(temp_path), flags, 0o600)
        except FileExistsError as error:
            last_error = error
            continue
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, output)
            os.chmod(output, 0o600)
            return
        except Exception:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
            raise
    raise FileExistsError(
        f"could not create private temporary output for {output}"
    ) from last_error


def _flat_archived_rollout_matches_date(
    rollout_path: pathlib.Path, date_value: dt.date
) -> bool:
    return rollout_path.name.startswith(f"rollout-{date_value.strftime('%Y-%m-%d')}")


def _is_raw_rollout_file(path: pathlib.Path) -> bool:
    return path.name.startswith("rollout-") and not path.name.startswith(
        "rollout-summary"
    )


def _session_meta_rollout_dedupe_key(relative_path: pathlib.PurePosixPath) -> str:
    parts = relative_path.parts
    if len(parts) >= 2 and parts[0] == "archived_sessions":
        return f"archived_sessions/{relative_path.name}"
    return relative_path.as_posix()


def _timestamp_from_jsonl_line(line: str) -> str:
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return ""
    timestamp = obj.get("timestamp")
    return str(timestamp) if isinstance(timestamp, str) else ""


def _raw_line_parts(raw_line: Any) -> tuple[bytes, str]:
    if isinstance(raw_line, str):
        return raw_line.encode("utf-8", "surrogatepass"), raw_line
    raw_bytes = bytes(raw_line)
    return raw_bytes, raw_bytes.decode("utf-8", "replace")


def _raw_line_endswith_newline(raw_line: Any) -> bool:
    if isinstance(raw_line, str):
        return raw_line.endswith("\n")
    return bytes(raw_line).endswith(b"\n")


def _iter_rollout_chunks(
    handle: Any,
    *,
    chunk_bytes: int,
) -> Iterable[RolloutChunk]:
    if chunk_bytes < 1:
        raise ValueError("--chunk-bytes must be positive")

    chunk_index = 0
    offset = 0
    record_no = 0
    lines: list[str] = []
    current_bytes = 0
    byte_start = 0
    record_start = 0
    first_timestamp = ""
    last_timestamp = ""
    oversized_record = False

    def flush() -> RolloutChunk | None:
        nonlocal chunk_index, lines, current_bytes, byte_start, record_start
        nonlocal first_timestamp, last_timestamp, oversized_record
        if not lines:
            return None
        chunk = RolloutChunk(
            index=chunk_index,
            byte_start=byte_start,
            byte_end=byte_start + current_bytes,
            record_start=record_start,
            record_end=record_start + len(lines) - 1,
            first_timestamp=first_timestamp,
            last_timestamp=last_timestamp,
            oversized_record=oversized_record,
            lines=tuple(lines),
        )
        chunk_index += 1
        lines = []
        current_bytes = 0
        byte_start = 0
        record_start = 0
        first_timestamp = ""
        last_timestamp = ""
        oversized_record = False
        return chunk

    read_limit = chunk_bytes + 1

    while True:
        raw_line = handle.readline(read_limit)
        if not raw_line:
            chunk = flush()
            if chunk is not None:
                yield chunk
            return
        raw_bytes, line = _raw_line_parts(raw_line)
        line_start = offset
        record_no += 1
        line_truncated = len(raw_line) == read_limit and not _raw_line_endswith_newline(
            raw_line
        )
        if len(raw_bytes) > chunk_bytes or line_truncated:
            if lines:
                chunk = flush()
                if chunk is not None:
                    yield chunk

            total_bytes = len(raw_bytes)
            while line_truncated:
                segment = handle.readline(read_limit)
                if not segment:
                    break
                segment_bytes, _ = _raw_line_parts(segment)
                total_bytes += len(segment_bytes)
                line_truncated = len(
                    segment
                ) == read_limit and not _raw_line_endswith_newline(segment)

            offset += total_bytes
            yield RolloutChunk(
                index=chunk_index,
                byte_start=line_start,
                byte_end=line_start + total_bytes,
                record_start=record_no,
                record_end=record_no,
                first_timestamp="",
                last_timestamp="",
                oversized_record=True,
                lines=("",),
            )
            chunk_index += 1
            continue

        offset += len(raw_bytes)

        if lines and current_bytes + len(raw_bytes) > chunk_bytes:
            chunk = flush()
            if chunk is not None:
                yield chunk

        if not lines:
            byte_start = line_start
            record_start = record_no

        timestamp = _timestamp_from_jsonl_line(line)
        if timestamp and not first_timestamp:
            first_timestamp = timestamp
        if timestamp:
            last_timestamp = timestamp
        oversized_record = oversized_record or len(raw_bytes) > chunk_bytes
        lines.append(line)
        current_bytes += len(raw_bytes)


def _fetch_ranges_for_byte_range(
    *,
    byte_start: int,
    byte_end: int,
    max_bytes: int,
) -> list[dict[str, int]]:
    if byte_start < 0:
        raise ValueError("byte_start must be non-negative")
    if byte_end <= byte_start:
        raise ValueError("byte_end must be greater than byte_start")
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    ranges: list[dict[str, int]] = []
    cursor = byte_start
    while cursor < byte_end:
        next_cursor = min(cursor + max_bytes, byte_end)
        ranges.append(
            {
                "range_index": len(ranges),
                "byte_start": cursor,
                "byte_end": next_cursor,
            }
        )
        cursor = next_cursor
    return ranges


def _session_shards_source_identity(stat_result: os.stat_result) -> tuple[int, ...]:
    return (
        int(stat_result.st_dev),
        int(stat_result.st_ino),
        int(stat_result.st_mode),
        int(stat_result.st_size),
        int(stat_result.st_mtime_ns),
        int(stat_result.st_ctime_ns),
    )


def _session_shards_source_identity_bytes(identity: tuple[int, ...]) -> bytes:
    return json.dumps(identity, separators=(",", ":")).encode("ascii")


def _session_shards_source_token(identity: tuple[int, ...]) -> str:
    encoded = _session_shards_source_identity_bytes(identity)
    return SESSION_SHARDS_SOURCE_TOKEN_PREFIX + hashlib.sha256(encoded).hexdigest()


def _session_shards_holdout_canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _session_shards_coordinator_frame(*parts: bytes) -> bytes:
    framed = bytearray()
    for part in parts:
        if not isinstance(part, bytes):
            raise TypeError("coordinator HMAC framing accepts bytes only")
        framed.extend(len(part).to_bytes(8, "big"))
        framed.extend(part)
    return bytes(framed)


def _validate_session_shards_coordinator_identity_key(identity_key: bytes) -> None:
    if (
        not isinstance(identity_key, bytes)
        or len(identity_key) != SESSION_SHARDS_COORDINATOR_IDENTITY_KEY_BYTES
    ):
        raise ValueError("coordinator identity key must be exactly 32 bytes")


def _session_shards_coordinator_identity_key_id(identity_key: bytes) -> str:
    _validate_session_shards_coordinator_identity_key(identity_key)
    digest = hmac.new(
        identity_key,
        _session_shards_coordinator_frame(
            SESSION_SHARDS_COORDINATOR_IDENTITY_ROOT_DOMAIN,
            b"key-id",
        ),
        hashlib.sha256,
    ).hexdigest()
    return f"identity_key_v2:{digest}"


def _session_shards_coordinator_digest(
    identity_key: bytes,
    *,
    domain: str,
    value: dict[str, Any],
) -> str:
    _validate_session_shards_coordinator_identity_key(identity_key)
    domain_bytes = domain.encode("ascii")
    subkey = hmac.new(
        identity_key,
        _session_shards_coordinator_frame(
            SESSION_SHARDS_COORDINATOR_IDENTITY_ROOT_DOMAIN,
            b"subkey",
            domain_bytes,
        ),
        hashlib.sha256,
    ).digest()
    return hmac.new(
        subkey,
        _session_shards_coordinator_frame(
            SESSION_SHARDS_COORDINATOR_IDENTITY_ROOT_DOMAIN,
            b"value",
            _session_shards_holdout_canonical_bytes(value),
        ),
        hashlib.sha256,
    ).hexdigest()


def _session_shards_now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _normalize_session_shards_now_utc(now_utc: dt.datetime | None) -> dt.datetime:
    value = _session_shards_now_utc() if now_utc is None else now_utc
    if not isinstance(value, dt.datetime) or value.tzinfo is None:
        raise ValueError("current UTC clock must be a timezone-aware datetime")
    return value.astimezone(dt.timezone.utc)


def _session_shards_holdout_daily_window(
    window_start: str,
    window_end: str,
    *,
    now_utc: dt.datetime | None = None,
) -> tuple[dt.datetime, dt.datetime]:
    if (
        SESSION_SHARDS_HOLDOUT_TIME_RE.fullmatch(window_start) is None
        or SESSION_SHARDS_HOLDOUT_TIME_RE.fullmatch(window_end) is None
    ):
        raise ValueError(
            "holdout window must use closed-day UTC boundaries as YYYY-MM-DDT00:00:00Z"
        )
    try:
        start = dt.datetime.strptime(window_start, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
        end = dt.datetime.strptime(window_end, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise ValueError("holdout window contains an invalid UTC date") from exc
    if end - start != dt.timedelta(days=1):
        raise ValueError("controlled holdout requires one exact closed UTC day")
    closed_through = _normalize_session_shards_now_utc(now_utc).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    if end > closed_through:
        raise ValueError(
            "controlled holdout window must end on or before the latest closed UTC day"
        )
    return start, end


def _validate_session_shards_holdout_source_kind(source_kind: str) -> None:
    if SESSION_SHARDS_HOLDOUT_SOURCE_KIND_RE.fullmatch(source_kind) is None:
        raise ValueError(
            "--source-kind must be a lowercase bounded protocol identifier"
        )


def _validate_session_shards_holdout_lease_ref(source_lease_ref: str) -> None:
    if SESSION_SHARDS_HOLDOUT_LEASE_REF_RE.fullmatch(source_lease_ref) is None:
        raise ValueError("--source-lease-ref must be a bounded opaque protocol ref")


def _validate_session_shards_holdout_host(host: str) -> None:
    canonical = _resolve_hosts([host])[0]
    if canonical != host or HOSTS[canonical]["kind"] != "ssh":
        raise ValueError("holdout receipt host must be one canonical remote host")


def _validate_session_shards_holdout_identity_key(identity_key: bytes) -> None:
    if (
        not isinstance(identity_key, bytes)
        or len(identity_key) != SESSION_SHARDS_HOLDOUT_IDENTITY_KEY_BYTES
    ):
        raise ValueError("holdout identity key must be exactly 32 bytes")


def _resolve_session_shards_shadow_identity_path(
    value: str,
    *,
    creating: bool,
) -> pathlib.Path:
    raw_path = pathlib.Path(value).expanduser()
    if not raw_path.is_absolute():
        raise ValueError("--shadow-identity-path must be absolute")
    if any(part == ".." for part in raw_path.parts):
        raise ValueError("--shadow-identity-path must not contain ..")

    system_alias_roots = (pathlib.Path("/tmp"), pathlib.Path("/var"))
    for alias_root in system_alias_roots:
        resolved_alias_root = alias_root.resolve()
        if _path_is_relative_to(raw_path, alias_root):
            raw_path = resolved_alias_root / raw_path.relative_to(alias_root)
            break
    _reject_symlink_components(raw_path)
    resolved = raw_path.resolve(strict=False)

    if creating:
        configured_root = os.environ.get("CODEX_SESSION_SHARDS_SHADOW_ROOT")
        if configured_root:
            shadow_root = pathlib.Path(configured_root).expanduser()
            if not shadow_root.is_absolute() or any(
                part == ".." for part in shadow_root.parts
            ):
                raise ValueError(
                    "CODEX_SESSION_SHARDS_SHADOW_ROOT must be an absolute safe path"
                )
            for alias_root in system_alias_roots:
                if _path_is_relative_to(shadow_root, alias_root):
                    shadow_root = alias_root.resolve() / shadow_root.relative_to(
                        alias_root
                    )
                    break
            _reject_symlink_components(shadow_root)
            shadow_root = shadow_root.resolve(strict=True)
            root_metadata = shadow_root.lstat()
            if (
                stat.S_ISLNK(root_metadata.st_mode)
                or not stat.S_ISDIR(root_metadata.st_mode)
                or root_metadata.st_uid != os.getuid()
                or stat.S_IMODE(root_metadata.st_mode) != 0o700
            ):
                raise ValueError(
                    "CODEX_SESSION_SHARDS_SHADOW_ROOT must be owner-only mode 0700"
                )
            allowed_roots = (shadow_root,)
        else:
            allowed_roots = (
                pathlib.Path.cwd().resolve()
                / ".codex-local/session-retrospective-v2-shadow",
                pathlib.Path("/tmp").resolve(),
                pathlib.Path(tempfile.gettempdir()).resolve(),
            )
        if not any(_path_is_relative_to(resolved, root) for root in allowed_roots):
            raise ValueError(
                "new shadow identity must stay under the run-local shadow root or /tmp"
            )
    return resolved


def _validate_session_shards_shadow_identity_directory(
    identity_path: pathlib.Path,
) -> None:
    try:
        identity_stat = identity_path.lstat()
    except FileNotFoundError as exc:
        raise ValueError("shadow identity does not exist") from exc
    if stat.S_ISLNK(identity_stat.st_mode) or not stat.S_ISDIR(identity_stat.st_mode):
        raise ValueError("shadow identity path must be a real directory")
    if identity_stat.st_uid != os.getuid():
        raise ValueError("shadow identity directory must be owned by the current user")
    if stat.S_IMODE(identity_stat.st_mode) != 0o700:
        raise ValueError("shadow identity directory must have mode 0700")


def _read_session_shards_shadow_identity_key(
    identity_path: pathlib.Path,
) -> bytes:
    _validate_session_shards_shadow_identity_directory(identity_path)
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("shadow identity no-follow open is unsupported")
    key_path = identity_path / SESSION_SHARDS_HOLDOUT_IDENTITY_KEY_FILE
    flags = os.O_RDONLY | os.O_NOFOLLOW
    flags |= int(getattr(os, "O_CLOEXEC", 0))
    try:
        descriptor = os.open(key_path, flags)
    except OSError as exc:
        raise ValueError("shadow identity key is missing or unsafe") from exc
    try:
        key_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(key_stat.st_mode)
            or key_stat.st_uid != os.getuid()
            or key_stat.st_nlink != 1
            or stat.S_IMODE(key_stat.st_mode) != 0o600
            or key_stat.st_size != SESSION_SHARDS_HOLDOUT_IDENTITY_KEY_BYTES
        ):
            raise ValueError(
                "shadow identity key must be a single-link owner-only 32-byte file"
            )
        key = os.read(descriptor, SESSION_SHARDS_HOLDOUT_IDENTITY_KEY_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(key) != SESSION_SHARDS_HOLDOUT_IDENTITY_KEY_BYTES:
        raise ValueError("shadow identity key has an invalid length")
    return key


def _create_session_shards_shadow_identity(
    identity_path: pathlib.Path,
) -> bytes:
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("shadow identity no-follow create is unsupported")
    try:
        parent_stat = identity_path.parent.lstat()
    except FileNotFoundError as exc:
        raise ValueError("shadow identity parent directory must already exist") from exc
    if (
        not stat.S_ISDIR(parent_stat.st_mode)
        or parent_stat.st_uid != os.getuid()
        or stat.S_IMODE(parent_stat.st_mode) != 0o700
    ):
        raise ValueError(
            "shadow identity parent directory must be current-user mode 0700"
        )
    try:
        os.mkdir(identity_path, 0o700)
    except FileExistsError as exc:
        raise ValueError(
            "shadow identity already exists; use --require-existing-shadow-identity"
        ) from exc
    except OSError as exc:
        raise ValueError("could not create shadow identity directory") from exc

    key_path = identity_path / SESSION_SHARDS_HOLDOUT_IDENTITY_KEY_FILE
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    flags |= int(getattr(os, "O_CLOEXEC", 0))
    descriptor: int | None = None
    try:
        descriptor = os.open(key_path, flags, 0o600)
        key = os.urandom(SESSION_SHARDS_HOLDOUT_IDENTITY_KEY_BYTES)
        written = 0
        while written < len(key):
            write_count = os.write(descriptor, key[written:])
            if write_count < 1:
                raise OSError("shadow identity key write made no progress")
            written += write_count
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
            descriptor = None
        try:
            key_path.unlink(missing_ok=True)
            identity_path.rmdir()
        except OSError:
            pass
        raise ValueError("could not create shadow identity key") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return _read_session_shards_shadow_identity_key(identity_path)


def _session_shards_holdout_identity_key_id(identity_key: bytes) -> str:
    _validate_session_shards_holdout_identity_key(identity_key)
    return (
        SESSION_SHARDS_HOLDOUT_KEY_ID_PREFIX
        + hashlib.sha256(
            b"session-shards-holdout-key-id-v1\0" + identity_key
        ).hexdigest()
    )


def _session_shards_holdout_core(
    *,
    host: str,
    window_start: str,
    window_end: str,
    source_kind: str,
    source_lease_ref: str,
) -> dict[str, Any]:
    return {
        "backfill_required": True,
        "content_free": True,
        "host": host,
        "kind": "transport_receipt",
        "qualification_mode": "shadow",
        "reason": SESSION_SHARDS_HOLDOUT_REASON,
        "receipt_type": "controlled_missing_host_holdout",
        "schema": SESSION_SHARDS_HOLDOUT_SCHEMA,
        "source_kind": source_kind,
        "source_lease_ref": source_lease_ref,
        "source_observed": False,
        "terminal": True,
        "transport_attempted": False,
        "window_end": window_end,
        "window_start": window_start,
    }


def _session_shards_holdout_receipt(
    *,
    identity_key: bytes,
    host: str,
    window_start: str,
    window_end: str,
    source_kind: str,
    source_lease_ref: str,
    now_utc: dt.datetime | None = None,
) -> dict[str, Any]:
    _validate_session_shards_holdout_identity_key(identity_key)
    _validate_session_shards_holdout_host(host)
    _session_shards_holdout_daily_window(
        window_start,
        window_end,
        now_utc=now_utc,
    )
    _validate_session_shards_holdout_source_kind(source_kind)
    _validate_session_shards_holdout_lease_ref(source_lease_ref)
    core = _session_shards_holdout_core(
        host=host,
        window_start=window_start,
        window_end=window_end,
        source_kind=source_kind,
        source_lease_ref=source_lease_ref,
    )
    holdout_ref = (
        SESSION_SHARDS_HOLDOUT_RECEIPT_PREFIX
        + hashlib.sha256(_session_shards_holdout_canonical_bytes(core)).hexdigest()
    )
    receipt = {
        **core,
        "holdout_ref": holdout_ref,
        "identity_key_id": _session_shards_holdout_identity_key_id(identity_key),
    }
    receipt["authentication_tag"] = (
        SESSION_SHARDS_HOLDOUT_AUTH_PREFIX
        + hmac.new(
            identity_key,
            SESSION_SHARDS_HOLDOUT_AUTH_CONTEXT
            + _session_shards_holdout_canonical_bytes(receipt),
            hashlib.sha256,
        ).hexdigest()
    )
    return receipt


def _verify_session_shards_holdout_receipt(
    receipt: dict[str, Any],
    *,
    identity_key: bytes,
    expected_host: str,
    expected_window_start: str,
    expected_window_end: str,
    expected_source_kind: str,
    expected_source_lease_ref: str,
    now_utc: dt.datetime | None = None,
) -> str:
    _validate_session_shards_holdout_identity_key(identity_key)
    if set(receipt) != _SESSION_SHARDS_HOLDOUT_RECEIPT_FIELDS:
        raise ValueError("holdout receipt does not match the closed field schema")
    expected_fixed = {
        "backfill_required": True,
        "content_free": True,
        "kind": "transport_receipt",
        "qualification_mode": "shadow",
        "reason": SESSION_SHARDS_HOLDOUT_REASON,
        "receipt_type": "controlled_missing_host_holdout",
        "schema": SESSION_SHARDS_HOLDOUT_SCHEMA,
        "source_observed": False,
        "terminal": True,
        "transport_attempted": False,
    }
    if any(
        type(receipt.get(key)) is not type(value) or receipt.get(key) != value
        for key, value in expected_fixed.items()
    ):
        raise ValueError("holdout receipt has an unsupported terminal state")

    host = receipt.get("host")
    window_start = receipt.get("window_start")
    window_end = receipt.get("window_end")
    source_kind = receipt.get("source_kind")
    source_lease_ref = receipt.get("source_lease_ref")
    if not all(
        isinstance(value, str)
        for value in (host, window_start, window_end, source_kind, source_lease_ref)
    ):
        raise ValueError("holdout receipt binding fields must be strings")
    assert isinstance(host, str)
    assert isinstance(window_start, str)
    assert isinstance(window_end, str)
    assert isinstance(source_kind, str)
    assert isinstance(source_lease_ref, str)
    _validate_session_shards_holdout_host(host)
    _session_shards_holdout_daily_window(
        window_start,
        window_end,
        now_utc=now_utc,
    )
    _validate_session_shards_holdout_source_kind(source_kind)
    _validate_session_shards_holdout_lease_ref(source_lease_ref)

    core = _session_shards_holdout_core(
        host=host,
        window_start=window_start,
        window_end=window_end,
        source_kind=source_kind,
        source_lease_ref=source_lease_ref,
    )
    expected_holdout_ref = (
        SESSION_SHARDS_HOLDOUT_RECEIPT_PREFIX
        + hashlib.sha256(_session_shards_holdout_canonical_bytes(core)).hexdigest()
    )
    if receipt.get("holdout_ref") != expected_holdout_ref:
        raise ValueError("holdout receipt ref does not match its binding")
    if receipt.get("identity_key_id") != _session_shards_holdout_identity_key_id(
        identity_key
    ):
        raise ValueError("holdout receipt identity key does not match")
    signed = dict(receipt)
    authentication_tag = signed.pop("authentication_tag")
    expected_tag = (
        SESSION_SHARDS_HOLDOUT_AUTH_PREFIX
        + hmac.new(
            identity_key,
            SESSION_SHARDS_HOLDOUT_AUTH_CONTEXT
            + _session_shards_holdout_canonical_bytes(signed),
            hashlib.sha256,
        ).hexdigest()
    )
    if not isinstance(authentication_tag, str) or not hmac.compare_digest(
        authentication_tag,
        expected_tag,
    ):
        raise ValueError("holdout receipt authentication failed")

    expected_bindings = {
        "host": expected_host,
        "window_start": expected_window_start,
        "window_end": expected_window_end,
        "source_kind": expected_source_kind,
        "source_lease_ref": expected_source_lease_ref,
    }
    if any(receipt.get(key) != value for key, value in expected_bindings.items()):
        raise ValueError("holdout receipt does not match the expected source lease")
    return expected_holdout_ref


def _session_shards_backfill_result(
    *,
    holdout_identity_key: bytes,
    coordinator_identity_key: bytes,
    holdout_ref: str,
    host: str,
    host_ref: str,
    window_start: str,
    window_end: str,
    source_kind: str,
    partial_source_lease_ref: str,
    backfill_source_lease_ref: str,
    partial_run_ref: str,
    backfill_run_ref: str,
    backfill_of_run_ref: str,
    partial_configuration_root: str,
    backfill_configuration_root: str,
    coordinator_identity_key_id: str,
    source_outcome: str,
    source_snapshot_ref: str,
    source_transport_receipt_ref: str,
    evidence_digest: str,
    terminal_completion_ref: str,
    terminal_completion_authentication_tag: str,
    terminal_completion_revision: int,
    status_checkpoint_revision: int,
    now_utc: dt.datetime | None = None,
) -> dict[str, Any]:
    _validate_session_shards_holdout_identity_key(holdout_identity_key)
    _validate_session_shards_coordinator_identity_key(coordinator_identity_key)
    expected_coordinator_key_id = _session_shards_coordinator_identity_key_id(
        coordinator_identity_key
    )
    if coordinator_identity_key_id != expected_coordinator_key_id:
        raise ValueError("backfill result coordinator identity does not match")
    attested_at_utc = _normalize_session_shards_now_utc(now_utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    core = {
        "attested_at_utc": attested_at_utc,
        "backfill_configuration_root": backfill_configuration_root,
        "backfill_holdout_used": False,
        "backfill_of_run_ref": backfill_of_run_ref,
        "backfill_run_ref": backfill_run_ref,
        "backfill_source_lease_ref": backfill_source_lease_ref,
        "completion_stage": "export",
        "coordinator_identity_key_id": coordinator_identity_key_id,
        "evidence_digest": evidence_digest,
        "holdout_ref": holdout_ref,
        "host": host,
        "host_ref": host_ref,
        "identity_key_id": _session_shards_holdout_identity_key_id(
            holdout_identity_key
        ),
        "kind": SESSION_SHARDS_BACKFILL_RESULT_KIND,
        "partial_configuration_root": partial_configuration_root,
        "partial_run_ref": partial_run_ref,
        "partial_source_lease_ref": partial_source_lease_ref,
        "schema": SESSION_SHARDS_BACKFILL_RESULT_SCHEMA,
        "source_kind": source_kind,
        "source_outcome": source_outcome,
        "source_snapshot_ref": source_snapshot_ref,
        "source_transport_receipt_ref": source_transport_receipt_ref,
        "status_checkpoint_revision": status_checkpoint_revision,
        "terminal": True,
        "terminal_completion_authentication_tag": (
            terminal_completion_authentication_tag
        ),
        "terminal_completion_ref": terminal_completion_ref,
        "terminal_completion_revision": terminal_completion_revision,
        "transport": "session-shards",
        "window_end": window_end,
        "window_start": window_start,
    }
    result_ref = (
        SESSION_SHARDS_BACKFILL_RESULT_REF_PREFIX
        + hashlib.sha256(_session_shards_holdout_canonical_bytes(core)).hexdigest()
    )
    result = {**core, "result_ref": result_ref}
    result["authentication_tag"] = (
        SESSION_SHARDS_BACKFILL_RESULT_AUTH_PREFIX
        + _session_shards_coordinator_digest(
            coordinator_identity_key,
            domain=SESSION_SHARDS_BACKFILL_RESULT_AUTH_CONTEXT,
            value=result,
        )
    )
    return result


def _verify_session_shards_backfill_result(
    result: dict[str, Any],
    *,
    receipt: dict[str, Any],
    holdout_identity_key: bytes,
    coordinator_identity_key: bytes,
    now_utc: dt.datetime | None = None,
) -> dict[str, Any]:
    _validate_session_shards_holdout_identity_key(holdout_identity_key)
    _validate_session_shards_coordinator_identity_key(coordinator_identity_key)
    if set(result) != _SESSION_SHARDS_BACKFILL_RESULT_FIELDS:
        raise ValueError(
            "backfill result does not match the closed authenticated schema"
        )
    fixed = {
        "backfill_holdout_used": False,
        "completion_stage": "export",
        "kind": SESSION_SHARDS_BACKFILL_RESULT_KIND,
        "schema": SESSION_SHARDS_BACKFILL_RESULT_SCHEMA,
        "terminal": True,
        "transport": "session-shards",
    }
    if any(
        type(result.get(key)) is not type(value) or result.get(key) != value
        for key, value in fixed.items()
    ):
        raise ValueError("backfill result is not a terminal session-shards result")

    string_fields = (
        "attested_at_utc",
        "backfill_configuration_root",
        "backfill_of_run_ref",
        "backfill_run_ref",
        "backfill_source_lease_ref",
        "coordinator_identity_key_id",
        "evidence_digest",
        "holdout_ref",
        "host",
        "host_ref",
        "identity_key_id",
        "partial_configuration_root",
        "partial_run_ref",
        "partial_source_lease_ref",
        "result_ref",
        "source_kind",
        "source_outcome",
        "source_snapshot_ref",
        "source_transport_receipt_ref",
        "terminal_completion_authentication_tag",
        "terminal_completion_ref",
        "window_end",
        "window_start",
    )
    if any(not isinstance(result.get(key), str) for key in string_fields):
        raise ValueError("backfill result binding fields must be strings")
    terminal_revision = result.get("terminal_completion_revision")
    status_revision = result.get("status_checkpoint_revision")
    if (
        isinstance(terminal_revision, bool)
        or not isinstance(terminal_revision, int)
        or terminal_revision < 1
        or isinstance(status_revision, bool)
        or not isinstance(status_revision, int)
        or status_revision < terminal_revision
    ):
        raise ValueError("backfill result completion revisions are invalid")

    partial_run_ref = str(result["partial_run_ref"])
    backfill_run_ref = str(result["backfill_run_ref"])
    if (
        SESSION_SHARDS_RUN_REF_RE.fullmatch(partial_run_ref) is None
        or SESSION_SHARDS_RUN_REF_RE.fullmatch(backfill_run_ref) is None
        or result["backfill_of_run_ref"] != partial_run_ref
        or backfill_run_ref == partial_run_ref
    ):
        raise ValueError("backfill result does not prove exact partial-run lineage")
    if (
        SESSION_SHARDS_CONFIGURATION_ROOT_RE.fullmatch(
            str(result["partial_configuration_root"])
        )
        is None
        or result["backfill_configuration_root"] != result["partial_configuration_root"]
    ):
        raise ValueError("backfill result configuration roots do not match")
    if (
        SESSION_SHARDS_HOST_REF_RE.fullmatch(str(result["host_ref"])) is None
        or SESSION_SHARDS_COORDINATOR_IDENTITY_RE.fullmatch(
            str(result["coordinator_identity_key_id"])
        )
        is None
        or SESSION_SHARDS_SOURCE_SNAPSHOT_REF_RE.fullmatch(
            str(result["source_snapshot_ref"])
        )
        is None
        or SESSION_SHARDS_SOURCE_RECEIPT_REF_RE.fullmatch(
            str(result["source_transport_receipt_ref"])
        )
        is None
        or SESSION_SHARDS_SOURCE_EVIDENCE_RE.fullmatch(str(result["evidence_digest"]))
        is None
        or SESSION_SHARDS_COVERAGE_RECEIPT_REF_RE.fullmatch(
            str(result["terminal_completion_ref"])
        )
        is None
        or SESSION_SHARDS_COVERAGE_AUTH_RE.fullmatch(
            str(result["terminal_completion_authentication_tag"])
        )
        is None
    ):
        raise ValueError("backfill result lacks authenticated session-shards evidence")
    _validate_session_shards_holdout_host(str(result["host"]))
    _session_shards_holdout_daily_window(
        str(result["window_start"]),
        str(result["window_end"]),
        now_utc=now_utc,
    )
    _validate_session_shards_holdout_source_kind(str(result["source_kind"]))
    _validate_session_shards_holdout_lease_ref(str(result["partial_source_lease_ref"]))
    _validate_session_shards_holdout_lease_ref(str(result["backfill_source_lease_ref"]))
    if result["partial_source_lease_ref"] == result["backfill_source_lease_ref"]:
        raise ValueError("backfill result reused the controlled holdout source lease")
    if result["source_outcome"] not in {"complete", "no_activity"}:
        raise ValueError("backfill result has no accepted terminal source outcome")

    expected_holdout_bindings = {
        "holdout_ref": receipt.get("holdout_ref"),
        "host": receipt.get("host"),
        "partial_source_lease_ref": receipt.get("source_lease_ref"),
        "source_kind": receipt.get("source_kind"),
        "window_end": receipt.get("window_end"),
        "window_start": receipt.get("window_start"),
    }
    if any(
        result.get(key) != value for key, value in expected_holdout_bindings.items()
    ):
        raise ValueError("backfill result does not match the authenticated holdout")
    expected_identity_key_id = _session_shards_holdout_identity_key_id(
        holdout_identity_key
    )
    if result["identity_key_id"] != expected_identity_key_id:
        raise ValueError("backfill result identity key does not match")
    if result[
        "coordinator_identity_key_id"
    ] != _session_shards_coordinator_identity_key_id(coordinator_identity_key):
        raise ValueError("backfill result coordinator identity key does not match")

    attested_at_utc = str(result["attested_at_utc"])
    if SESSION_SHARDS_ATTESTED_TIME_RE.fullmatch(attested_at_utc) is None:
        raise ValueError("backfill result attestation timestamp is invalid")
    try:
        attested_at = dt.datetime.strptime(
            attested_at_utc,
            "%Y-%m-%dT%H:%M:%S.%fZ",
        ).replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise ValueError("backfill result attestation timestamp is invalid") from exc
    current_utc = _normalize_session_shards_now_utc(now_utc)
    age_seconds = (current_utc - attested_at).total_seconds()
    if age_seconds < -SESSION_SHARDS_BACKFILL_RESULT_FUTURE_SKEW_SECONDS:
        raise ValueError("backfill result attestation is from the future")
    if age_seconds > SESSION_SHARDS_BACKFILL_RESULT_MAX_AGE_SECONDS:
        raise ValueError("backfill result attestation is stale")

    unsigned = dict(result)
    authentication_tag = unsigned.pop("authentication_tag")
    result_ref = unsigned.pop("result_ref")
    expected_result_ref = (
        SESSION_SHARDS_BACKFILL_RESULT_REF_PREFIX
        + hashlib.sha256(_session_shards_holdout_canonical_bytes(unsigned)).hexdigest()
    )
    if not isinstance(result_ref, str) or not hmac.compare_digest(
        result_ref,
        expected_result_ref,
    ):
        raise ValueError("backfill result ref does not match its binding")
    signed = {**unsigned, "result_ref": result_ref}
    expected_authentication_tag = (
        SESSION_SHARDS_BACKFILL_RESULT_AUTH_PREFIX
        + _session_shards_coordinator_digest(
            coordinator_identity_key,
            domain=SESSION_SHARDS_BACKFILL_RESULT_AUTH_CONTEXT,
            value=signed,
        )
    )
    if not isinstance(authentication_tag, str) or not hmac.compare_digest(
        authentication_tag,
        expected_authentication_tag,
    ):
        raise ValueError("backfill result authentication failed")
    return dict(result)


def _prepare_session_shards_holdout_ledger(ledger_path: pathlib.Path) -> pathlib.Path:
    if not ledger_path.is_absolute() or any(part == ".." for part in ledger_path.parts):
        raise ValueError("holdout ledger path must be absolute and must not contain ..")
    for alias_root in (pathlib.Path("/tmp"), pathlib.Path("/var")):
        if _path_is_relative_to(ledger_path, alias_root):
            ledger_path = alias_root.resolve() / ledger_path.relative_to(alias_root)
            break
    _reject_symlink_components(ledger_path)
    resolved = ledger_path.resolve(strict=False)
    try:
        parent_stat = resolved.parent.lstat()
    except FileNotFoundError as exc:
        raise ValueError("holdout ledger parent directory must already exist") from exc
    if (
        not stat.S_ISDIR(parent_stat.st_mode)
        or parent_stat.st_uid != os.getuid()
        or stat.S_IMODE(parent_stat.st_mode) != 0o700
    ):
        raise ValueError(
            "holdout ledger parent directory must be current-user mode 0700"
        )
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("holdout ledger no-follow open is unsupported")

    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
    flags |= int(getattr(os, "O_CLOEXEC", 0))
    created = False
    try:
        descriptor = os.open(resolved, flags | os.O_EXCL, 0o600)
        created = True
    except FileExistsError:
        descriptor = os.open(resolved, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if created:
            os.fchmod(descriptor, 0o600)
            metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise ValueError(
                "holdout ledger must be a single-link owner-only regular file"
            )
    finally:
        os.close(descriptor)
    return resolved


def _consume_session_shards_holdout_for_backfill(
    *,
    ledger_path: pathlib.Path,
    receipt: dict[str, Any],
    holdout_identity_key: bytes,
    coordinator_identity_key: bytes,
    backfill_result: dict[str, Any],
    now_utc: dt.datetime | None = None,
) -> str:
    current_utc = _normalize_session_shards_now_utc(now_utc)
    binding_fields = (
        receipt.get("host"),
        receipt.get("window_start"),
        receipt.get("window_end"),
        receipt.get("source_kind"),
        receipt.get("source_lease_ref"),
    )
    if not all(isinstance(value, str) for value in binding_fields):
        raise ValueError("holdout receipt binding fields must be strings")
    expected_host, expected_window_start, expected_window_end = binding_fields[:3]
    expected_source_kind, expected_source_lease_ref = binding_fields[3:]
    holdout_ref = _verify_session_shards_holdout_receipt(
        receipt,
        identity_key=holdout_identity_key,
        expected_host=expected_host,
        expected_window_start=expected_window_start,
        expected_window_end=expected_window_end,
        expected_source_kind=expected_source_kind,
        expected_source_lease_ref=expected_source_lease_ref,
        now_utc=current_utc,
    )
    verified_result = _verify_session_shards_backfill_result(
        backfill_result,
        receipt=receipt,
        holdout_identity_key=holdout_identity_key,
        coordinator_identity_key=coordinator_identity_key,
        now_utc=current_utc,
    )

    # Every authentication, freshness, lineage, and evidence check above must finish
    # before this function creates or locks the persistent ledger.
    ledger_path = _prepare_session_shards_holdout_ledger(ledger_path)
    receipt_digest = (
        "sha256:"
        + hashlib.sha256(_session_shards_holdout_canonical_bytes(receipt)).hexdigest()
    )
    consumed_at_utc = current_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    connection = sqlite3.connect(
        str(ledger_path),
        timeout=30.0,
        isolation_level=None,
    )
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ledger_metadata (
                schema_version INTEGER PRIMARY KEY CHECK (schema_version = 2)
            )
            """
        )
        schema_row = connection.execute(
            "SELECT schema_version FROM ledger_metadata"
        ).fetchone()
        if schema_row is None:
            connection.execute(
                "INSERT INTO ledger_metadata(schema_version) VALUES (?)",
                (SESSION_SHARDS_HOLDOUT_LEDGER_SCHEMA_VERSION,),
            )
        elif schema_row != (SESSION_SHARDS_HOLDOUT_LEDGER_SCHEMA_VERSION,):
            raise RuntimeError("holdout ledger schema version is unsupported")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS holdout_consumptions (
                holdout_ref TEXT PRIMARY KEY,
                receipt_digest TEXT NOT NULL,
                identity_key_id TEXT NOT NULL,
                host TEXT NOT NULL,
                window_start TEXT NOT NULL,
                window_end TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                source_lease_ref TEXT NOT NULL,
                consumed_at_utc TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS backfill_replacements (
                holdout_ref TEXT PRIMARY KEY
                    REFERENCES holdout_consumptions(holdout_ref),
                backfill_result_ref TEXT NOT NULL UNIQUE,
                backfill_run_ref TEXT NOT NULL UNIQUE,
                backfill_of_run_ref TEXT NOT NULL,
                configuration_root TEXT NOT NULL,
                evidence_digest TEXT NOT NULL,
                backfill_source_lease_ref TEXT NOT NULL UNIQUE,
                source_snapshot_ref TEXT NOT NULL,
                source_transport_receipt_ref TEXT NOT NULL,
                terminal_completion_ref TEXT NOT NULL UNIQUE,
                terminal_completion_authentication_tag TEXT NOT NULL,
                terminal_completion_revision INTEGER NOT NULL,
                status_checkpoint_revision INTEGER NOT NULL,
                attested_at_utc TEXT NOT NULL,
                source_outcome TEXT NOT NULL
                    CHECK (source_outcome IN ('complete', 'no_activity'))
            )
            """
        )
        connection.execute(
            """
            INSERT INTO holdout_consumptions(
                holdout_ref,
                receipt_digest,
                identity_key_id,
                host,
                window_start,
                window_end,
                source_kind,
                source_lease_ref,
                consumed_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                holdout_ref,
                receipt_digest,
                str(receipt["identity_key_id"]),
                expected_host,
                expected_window_start,
                expected_window_end,
                expected_source_kind,
                expected_source_lease_ref,
                consumed_at_utc,
            ),
        )
        connection.execute(
            """
            INSERT INTO backfill_replacements(
                holdout_ref,
                backfill_result_ref,
                backfill_run_ref,
                backfill_of_run_ref,
                configuration_root,
                evidence_digest,
                backfill_source_lease_ref,
                source_snapshot_ref,
                source_transport_receipt_ref,
                terminal_completion_ref,
                terminal_completion_authentication_tag,
                terminal_completion_revision,
                status_checkpoint_revision,
                attested_at_utc,
                source_outcome
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                holdout_ref,
                verified_result["result_ref"],
                verified_result["backfill_run_ref"],
                verified_result["backfill_of_run_ref"],
                verified_result["backfill_configuration_root"],
                verified_result["evidence_digest"],
                verified_result["backfill_source_lease_ref"],
                verified_result["source_snapshot_ref"],
                verified_result["source_transport_receipt_ref"],
                verified_result["terminal_completion_ref"],
                verified_result["terminal_completion_authentication_tag"],
                verified_result["terminal_completion_revision"],
                verified_result["status_checkpoint_revision"],
                verified_result["attested_at_utc"],
                verified_result["source_outcome"],
            ),
        )
        connection.commit()
    except sqlite3.IntegrityError as exc:
        connection.rollback()
        consumed = connection.execute(
            "SELECT 1 FROM holdout_consumptions WHERE holdout_ref = ?",
            (holdout_ref,),
        ).fetchone()
        if consumed is not None:
            raise ValueError("holdout receipt replay rejected") from exc
        raise ValueError("authenticated backfill result is already recorded") from exc
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return holdout_ref


def _session_shards_resume_cursor(
    source_identity: tuple[int, ...],
    source_token: str,
    *,
    byte_offset: int,
    next_record_index: int,
) -> str:
    payload = json.dumps(
        {
            "byte_offset": byte_offset,
            "next_record_index": next_record_index,
            "source_token": source_token,
        },
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    encoded_payload = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    key = hashlib.sha256(
        b"session-shards-resume-key-v1\0"
        + _session_shards_source_identity_bytes(source_identity)
    ).digest()
    signature = hmac.new(key, payload, hashlib.sha256).hexdigest()
    return SESSION_SHARDS_RESUME_CURSOR_PREFIX + encoded_payload + "." + signature


def _session_shards_decode_resume_cursor(
    cursor: str,
) -> tuple[bytes, str, dict[str, Any]]:
    if not isinstance(cursor, str) or not cursor.startswith(
        SESSION_SHARDS_RESUME_CURSOR_PREFIX
    ):
        raise ValueError("invalid session-shards resume cursor")
    encoded = cursor.removeprefix(SESSION_SHARDS_RESUME_CURSOR_PREFIX)
    if len(encoded) > 2048 or encoded.count(".") != 1:
        raise ValueError("invalid session-shards resume cursor")
    payload_text, signature = encoded.split(".", 1)
    try:
        payload = base64.b64decode(
            payload_text + "=" * (-len(payload_text) % 4),
            altchars=b"-_",
            validate=True,
        )
        value = json.loads(payload)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("invalid session-shards resume cursor") from exc
    if not isinstance(value, dict) or set(value) != {
        "byte_offset",
        "next_record_index",
        "source_token",
    }:
        raise ValueError("invalid session-shards resume cursor")
    byte_offset = value.get("byte_offset")
    next_record_index = value.get("next_record_index")
    source_token = value.get("source_token")
    if (
        isinstance(byte_offset, bool)
        or not isinstance(byte_offset, int)
        or byte_offset < 0
        or isinstance(next_record_index, bool)
        or not isinstance(next_record_index, int)
        or next_record_index < 0
        or not isinstance(source_token, str)
        or re.fullmatch(
            re.escape(SESSION_SHARDS_SOURCE_TOKEN_PREFIX) + r"[0-9a-f]{64}",
            source_token,
        )
        is None
        or re.fullmatch(r"[0-9a-f]{64}", signature) is None
    ):
        raise ValueError("invalid session-shards resume cursor")
    canonical_payload = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    if canonical_payload != payload:
        raise ValueError("invalid session-shards resume cursor")
    return payload, signature, value


def _session_shards_parse_resume_cursor(
    cursor: str,
    source_identity: tuple[int, ...],
    expected_source_token: str,
) -> tuple[int, int]:
    payload, signature, value = _session_shards_decode_resume_cursor(cursor)
    if value["source_token"] != expected_source_token:
        raise ValueError("invalid session-shards resume cursor")
    key = hashlib.sha256(
        b"session-shards-resume-key-v1\0"
        + _session_shards_source_identity_bytes(source_identity)
    ).digest()
    expected_signature = hmac.new(key, payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise ValueError("invalid session-shards resume cursor")
    return int(value["byte_offset"]), int(value["next_record_index"])


def _session_shards_request_binding(
    *,
    rollout: str,
    mode: str,
    source_token: str | None,
    byte_start: int,
    byte_end: int | None,
    shard_bytes: int,
    max_shards: int,
    record_processing_budget_bytes: int,
    resume_cursor: str | None,
) -> str:
    payload = json.dumps(
        {
            "byte_end": byte_end,
            "byte_start": byte_start,
            "max_shards": max_shards,
            "mode": mode,
            "record_processing_budget_bytes": record_processing_budget_bytes,
            "rollout": rollout,
            "resume_cursor": resume_cursor,
            "schema": SESSION_SHARDS_SCHEMA,
            "shard_bytes": shard_bytes,
            "source_token": source_token,
        },
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return SESSION_SHARDS_REQUEST_BINDING_PREFIX + hashlib.sha256(payload).hexdigest()


def _open_session_shard_source(
    codex_root: pathlib.Path,
    rollout_relative_path: pathlib.PurePosixPath,
) -> Any:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    supports_dir_fd = getattr(os, "supports_dir_fd", frozenset())
    if not nofollow or not directory or os.open not in supports_dir_fd:
        raise RuntimeError("session-shards secure openat traversal is unsupported")

    parts = rollout_relative_path.parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ValueError("rollout path must stay under Codex root")
    close_on_exec = getattr(os, "O_CLOEXEC", 0)
    nonblock = getattr(os, "O_NONBLOCK", 0)
    directory_flags = os.O_RDONLY | nofollow | directory | close_on_exec
    file_flags = os.O_RDONLY | nofollow | close_on_exec | nonblock
    try:
        current_fd = os.open(str(codex_root.expanduser()), directory_flags)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ValueError("Codex root must be a real directory") from exc
        raise
    file_fd = -1
    try:
        for index, part in enumerate(parts[:-1]):
            try:
                next_fd = os.open(part, directory_flags, dir_fd=current_fd)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise ValueError(
                        "rollout path uses a symlink or non-directory ancestor"
                    ) from exc
                raise
            os.close(current_fd)
            current_fd = next_fd
            hook = globals().get("_SESSION_SHARDS_OPEN_COMPONENT_HOOK")
            if callable(hook):
                hook(index, part, current_fd)
        try:
            file_fd = os.open(parts[-1], file_flags, dir_fd=current_fd)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise ValueError(
                    "rollout path uses a symlink or non-directory ancestor"
                ) from exc
            raise
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise ValueError("rollout path is not a regular file")
        handle = os.fdopen(file_fd, "rb")
        file_fd = -1
        return handle
    finally:
        os.close(current_fd)
        if file_fd != -1:
            os.close(file_fd)


def _session_shards_record_index_at_offset(
    handle: Any,
    *,
    byte_offset: int,
    source_bytes: int,
) -> int:
    handle.seek(0)
    remaining = byte_offset
    record_index = 0
    last_byte = b""
    while remaining:
        chunk = handle.read(min(64 * 1024, remaining))
        if not chunk:
            raise ValueError("rollout ended while locating the requested byte range")
        record_index += chunk.count(b"\n")
        last_byte = chunk[-1:]
        remaining -= len(chunk)
    if byte_offset == source_bytes and byte_offset and last_byte != b"\n":
        record_index += 1
    handle.seek(byte_offset)
    return record_index


def _validate_session_shards_boundary(
    handle: Any,
    *,
    byte_offset: int,
    source_bytes: int,
    option: str,
) -> None:
    if byte_offset < 0 or byte_offset > source_bytes:
        raise ValueError(f"{option} must stay between 0 and {source_bytes}")
    if byte_offset in (0, source_bytes):
        return
    handle.seek(byte_offset - 1)
    if handle.read(1) != b"\n":
        raise ValueError(f"{option} must be on a JSONL record boundary")


def _reject_nonstandard_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _reject_duplicate_json_object_fields(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object field: {key}")
        result[key] = value
    return result


def _iter_session_shard_records(
    handle: Any,
    *,
    byte_start: int,
    byte_end: int,
    record_start: int,
    record_processing_budget_bytes: int,
) -> Iterable[SessionShardRecord]:
    handle.seek(byte_start)
    byte_offset = byte_start
    record_index = record_start
    while byte_offset < byte_end:
        storage: Any | None = tempfile.SpooledTemporaryFile(
            max_size=SESSION_SHARDS_RECORD_SPOOL_MEMORY_BYTES,
            mode="w+b",
        )
        try:
            record_hasher = hashlib.sha256()
            total_bytes = 0
            over_processing_budget = False
            spool_permissions_verified = False
            final_segment = b""
            record_tail = b""
            while byte_offset + total_bytes < byte_end:
                remaining = byte_end - byte_offset - total_bytes
                hard_scan_remaining = (
                    HARD_SESSION_RECORD_SCAN_CEILING_BYTES - total_bytes
                )
                if hard_scan_remaining <= 0:
                    raise ValueError(
                        "JSONL record scan exceeded the hard byte ceiling of "
                        f"{HARD_SESSION_RECORD_SCAN_CEILING_BYTES} bytes"
                    )
                segment = handle.readline(
                    min(
                        SESSION_SHARDS_RECORD_SCAN_CHUNK_BYTES,
                        remaining,
                        hard_scan_remaining,
                    )
                )
                if not segment:
                    raise ValueError(
                        "rollout ended before the requested byte range was read"
                    )
                total_bytes += len(segment)
                final_segment = segment
                record_tail = (record_tail + segment)[-2:]
                if not over_processing_budget:
                    if total_bytes <= record_processing_budget_bytes:
                        assert storage is not None
                        storage.write(segment)
                        record_hasher.update(segment)
                        if (
                            total_bytes > SESSION_SHARDS_RECORD_SPOOL_MEMORY_BYTES
                            and not spool_permissions_verified
                        ):
                            spool_fd = storage.fileno()
                            os.fchmod(spool_fd, stat.S_IRUSR | stat.S_IWUSR)
                            if stat.S_IMODE(os.fstat(spool_fd).st_mode) & 0o077:
                                raise RuntimeError(
                                    "session-shards record spool is not owner-only"
                                )
                            spool_permissions_verified = True
                    else:
                        storage.close()
                        storage = None
                        over_processing_budget = True
                if segment.endswith(b"\n"):
                    break
                if (
                    total_bytes >= HARD_SESSION_RECORD_SCAN_CEILING_BYTES
                    and byte_offset + total_bytes < byte_end
                ):
                    raise ValueError(
                        "JSONL record scan exceeded the hard byte ceiling of "
                        f"{HARD_SESSION_RECORD_SCAN_CEILING_BYTES} bytes"
                    )

            if (
                not final_segment.endswith(b"\n")
                and byte_offset + total_bytes < byte_end
            ):
                raise ValueError("rollout ended inside a JSONL record")

            gap_reason: str | None = None
            record_commitment: str | None = None
            processing_ceiling_kind: str | None = None
            processing_ceiling_limit: int | None = None
            processing_ceiling_observed: int | None = None
            delimiter_bytes = (
                2 if record_tail == b"\r\n" else int(record_tail.endswith(b"\n"))
            )
            if over_processing_budget:
                gap_reason = "record_processing_budget_exceeded"
                processing_ceiling_kind = "record_bytes"
                processing_ceiling_limit = record_processing_budget_bytes
                processing_ceiling_observed = total_bytes
            else:
                assert storage is not None
                if storage.tell() != total_bytes:
                    raise RuntimeError("session-shards record spool lost source bytes")
                try:
                    _validate_session_shards_json_storage(storage)
                except _SessionShardsProcessingBudgetExceeded as exc:
                    gap_reason = "record_processing_budget_exceeded"
                    processing_ceiling_kind = exc.kind
                    processing_ceiling_limit = exc.limit
                    processing_ceiling_observed = exc.observed
                    storage.close()
                    storage = None
                except (UnicodeDecodeError, ValueError):
                    gap_reason = "invalid_json"
                    storage.close()
                    storage = None
                else:
                    record_commitment = "sha256:" + record_hasher.hexdigest()

            yield SessionShardRecord(
                byte_start=byte_offset,
                byte_end=byte_offset + total_bytes,
                record_index=record_index,
                record_storage=storage,
                record_commitment=record_commitment,
                delimiter_bytes=delimiter_bytes,
                gap_reason=gap_reason,
                processing_ceiling_kind=processing_ceiling_kind,
                processing_ceiling_limit=processing_ceiling_limit,
                processing_ceiling_observed=processing_ceiling_observed,
            )
            byte_offset += total_bytes
            record_index += 1
            handle.seek(byte_offset)
        finally:
            if storage is not None:
                storage.close()

    if byte_offset != byte_end:
        raise ValueError("requested byte range did not end on a JSONL record boundary")


def _session_shards_processing_gap_metadata(
    item: SessionShardRecord,
    record_processing_budget_bytes: int,
) -> dict[str, Any]:
    kind = item.processing_ceiling_kind
    limit = item.processing_ceiling_limit
    observed = item.processing_ceiling_observed
    byte_count = item.byte_end - item.byte_start
    if kind == "record_bytes":
        valid = (
            limit == record_processing_budget_bytes
            and observed == byte_count
            and byte_count > limit
        )
    elif kind == "json_nesting_depth":
        valid = (
            limit == SESSION_SHARDS_MAX_JSON_NESTING_DEPTH
            and observed == limit + 1
            and byte_count <= record_processing_budget_bytes
        )
    else:
        valid = False
    if not valid:
        raise RuntimeError("session-shards processing gap metadata is inconsistent")
    return {
        "record_processing_budget_bytes": record_processing_budget_bytes,
        "hard_record_processing_ceiling_bytes": (
            HARD_SESSION_RECORD_PROCESSING_CEILING_BYTES
        ),
        "processing_ceiling_kind": kind,
        "processing_ceiling_limit": limit,
        "processing_ceiling_observed": observed,
    }


def _iter_session_shard_descriptors(
    records: Iterable[SessionShardRecord],
    *,
    shard_bytes: int,
    max_shards: int,
    record_processing_budget_bytes: int,
) -> Iterable[dict[str, Any]]:
    def descriptor_for(item: SessionShardRecord) -> dict[str, Any]:
        if item.gap_reason is not None:
            descriptor = {
                "kind": "shard",
                "status": "gap",
                "gap_reason": item.gap_reason,
                "byte_start": item.byte_start,
                "byte_end": item.byte_end,
                "record_start": item.record_index,
                "record_end": item.record_index + 1,
                "record_count": 1,
                "byte_count": item.byte_end - item.byte_start,
            }
            if item.gap_reason == "record_processing_budget_exceeded":
                descriptor.update(
                    _session_shards_processing_gap_metadata(
                        item,
                        record_processing_budget_bytes,
                    )
                )
            return descriptor

        item_bytes = item.byte_end - item.byte_start
        descriptor = {
            "kind": "shard",
            "status": "ready",
            "byte_start": item.byte_start,
            "byte_end": item.byte_end,
            "record_start": item.record_index,
            "record_end": item.record_index + 1,
            "record_count": 1,
        }
        if item_bytes > shard_bytes:
            descriptor.update(
                {
                    "oversized_record": True,
                    "record_transport": "base64_fragments",
                    "record_fragment_bytes": SESSION_SHARDS_RECORD_FRAGMENT_BYTES,
                    "record_processing_budget_bytes": record_processing_budget_bytes,
                }
            )
        return descriptor

    record_iterator = iter(records)
    pending: SessionShardRecord | None = None
    try:
        for page_index in range(max_shards):
            if pending is None:
                try:
                    item = next(record_iterator)
                except StopIteration:
                    return
            else:
                item = pending
                pending = None

            current = descriptor_for(item)
            if (
                current["status"] != "ready"
                or "oversized_record" in current
                or page_index + 1 == max_shards
            ):
                yield current
                continue

            while True:
                try:
                    next_item = next(record_iterator)
                except StopIteration:
                    yield current
                    return
                if next_item.gap_reason is not None:
                    pending = next_item
                    yield current
                    break
                next_item_bytes = next_item.byte_end - next_item.byte_start
                current_bytes = current["byte_end"] - current["byte_start"]
                if current_bytes + next_item_bytes > shard_bytes:
                    pending = next_item
                    yield current
                    break
                current["byte_end"] = next_item.byte_end
                current["record_end"] = next_item.record_index + 1
                current["record_count"] += 1
    finally:
        close = getattr(record_iterator, "close", None)
        if callable(close):
            close()


def _session_shards_content_commitment(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _session_shards_accounting_bytes(frame: dict[str, Any]) -> bytes:
    kind = frame.get("kind")
    common_keys = (
        "kind",
        "schema",
        "mode",
        "source_token",
        "request_binding",
        "byte_start",
        "byte_end",
        "byte_count",
        "record_start",
        "record_end",
        "delimiter_bytes",
    )
    if kind == "record":
        keys = common_keys + ("record_encoding", "record_commitment")
    elif kind == "record_fragment":
        keys = common_keys + (
            "record_byte_start",
            "record_byte_end",
            "record_byte_count",
            "fragment_index",
            "fragment_count",
            "record_encoding",
            "fragment_commitment",
            "record_commitment",
        )
    elif kind == "gap":
        keys = common_keys + ("reason",)
    else:
        raise ValueError(f"unsupported session-shards accounting frame: {kind}")
    try:
        accounting = {key: frame[key] for key in keys}
    except KeyError as exc:
        raise ValueError(
            f"session-shards {kind} frame is missing {exc.args[0]}"
        ) from exc
    for optional_key in (
        "record_processing_budget_bytes",
        "hard_record_processing_ceiling_bytes",
        "processing_ceiling_kind",
        "processing_ceiling_limit",
        "processing_ceiling_observed",
    ):
        if optional_key in frame:
            accounting[optional_key] = frame[optional_key]
    return (
        json.dumps(
            accounting,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        + b"\n"
    )


def _iter_session_record_transport_frames(
    item: SessionShardRecord,
    *,
    shard_bytes: int,
    source_token: str,
    request_binding: str,
) -> Iterable[dict[str, Any]]:
    if item.record_storage is None or item.record_commitment is None:
        raise RuntimeError("valid session-shards record lost its payload")
    record_storage = item.record_storage
    record_byte_count = item.byte_end - item.byte_start
    common = {
        "schema": SESSION_SHARDS_SCHEMA,
        "mode": "records",
        "source_token": source_token,
        "request_binding": request_binding,
        "record_start": item.record_index,
        "record_end": item.record_index + 1,
        "delimiter_bytes": item.delimiter_bytes,
    }
    if record_byte_count <= shard_bytes:
        record_storage.seek(0)
        record_bytes = record_storage.read(record_byte_count)
        if len(record_bytes) != record_byte_count:
            raise RuntimeError("session-shards record spool ended unexpectedly")
        yield {
            "kind": "record",
            **common,
            "byte_start": item.byte_start,
            "byte_end": item.byte_end,
            "byte_count": len(record_bytes),
            "record_encoding": "base64",
            "record_b64": base64.b64encode(record_bytes).decode("ascii"),
            "record_commitment": item.record_commitment,
        }
        return

    fragment_count = (
        record_byte_count + SESSION_SHARDS_RECORD_FRAGMENT_BYTES - 1
    ) // SESSION_SHARDS_RECORD_FRAGMENT_BYTES
    for fragment_index in range(fragment_count):
        local_start = fragment_index * SESSION_SHARDS_RECORD_FRAGMENT_BYTES
        local_end = min(
            local_start + SESSION_SHARDS_RECORD_FRAGMENT_BYTES,
            record_byte_count,
        )
        record_storage.seek(local_start)
        fragment = record_storage.read(local_end - local_start)
        if len(fragment) != local_end - local_start:
            raise RuntimeError("session-shards record spool ended unexpectedly")
        yield {
            "kind": "record_fragment",
            **common,
            "byte_start": item.byte_start + local_start,
            "byte_end": item.byte_start + local_end,
            "byte_count": len(fragment),
            "record_byte_start": item.byte_start,
            "record_byte_end": item.byte_end,
            "record_byte_count": record_byte_count,
            "fragment_index": fragment_index,
            "fragment_count": fragment_count,
            "record_encoding": "base64",
            "fragment_b64": base64.b64encode(fragment).decode("ascii"),
            "fragment_commitment": _session_shards_content_commitment(fragment),
            "record_commitment": item.record_commitment,
        }


def _iter_local_session_shard_frames(
    *,
    codex_root: pathlib.Path,
    rollout_relative_path: pathlib.PurePosixPath,
    emit: str,
    byte_start: int,
    byte_end: int | None,
    shard_bytes: int,
    max_shards: int,
    source_token: str | None,
    resume_cursor: str | None = None,
    record_processing_budget_bytes: int = (
        DEFAULT_SESSION_RECORD_PROCESSING_BUDGET_BYTES
    ),
) -> Iterable[dict[str, Any]]:
    with _open_session_shard_source(codex_root, rollout_relative_path) as handle:
        source_stat = os.fstat(handle.fileno())
        source_identity = _session_shards_source_identity(source_stat)
        current_token = _session_shards_source_token(source_identity)
        source_bytes = int(source_stat.st_size)
        if (
            record_processing_budget_bytes
            < max(shard_bytes, MIN_SESSION_RECORD_PROCESSING_BUDGET_BYTES)
            or record_processing_budget_bytes
            > HARD_SESSION_RECORD_PROCESSING_CEILING_BYTES
        ):
            raise ValueError(
                "record processing budget must cover the fixed memory envelope "
                f"of {MIN_SESSION_RECORD_PROCESSING_BUDGET_BYTES} bytes and be at "
                f"most {HARD_SESSION_RECORD_PROCESSING_CEILING_BYTES}"
            )
        if source_token is not None and source_token != current_token:
            raise ValueError("source token does not match current rollout")

        _validate_session_shards_boundary(
            handle,
            byte_offset=byte_start,
            source_bytes=source_bytes,
            option="--byte-start",
        )
        if emit == "descriptors":
            if byte_end is not None:
                raise ValueError("--byte-end is only valid with --emit records")
            if byte_start and source_token is None:
                raise ValueError(
                    "--source-token is required when --byte-start is non-zero"
                )
            effective_end = source_bytes
        else:
            if byte_end is None:
                raise ValueError("--byte-end is required with --emit records")
            if source_token is None:
                raise ValueError("--source-token is required with --emit records")
            if byte_end <= byte_start:
                raise ValueError("--byte-end must be greater than --byte-start")
            if byte_end - byte_start > MAX_SESSION_SHARDS_RANGE_BYTES:
                raise ValueError(
                    f"record range too large: {byte_end - byte_start} bytes > {MAX_SESSION_SHARDS_RANGE_BYTES}"
                )
            _validate_session_shards_boundary(
                handle,
                byte_offset=byte_end,
                source_bytes=source_bytes,
                option="--byte-end",
            )
            effective_end = byte_end

        if resume_cursor is None:
            if byte_start:
                raise ValueError(
                    "--resume-cursor is required when --byte-start is non-zero"
                )
            record_start = 0
        else:
            cursor_byte_offset, record_start = _session_shards_parse_resume_cursor(
                resume_cursor,
                source_identity,
                current_token,
            )
            if cursor_byte_offset != byte_start:
                raise ValueError(
                    "resume cursor byte offset does not match --byte-start"
                )
        request_binding = _session_shards_request_binding(
            rollout=rollout_relative_path.as_posix(),
            mode=emit,
            source_token=source_token,
            byte_start=byte_start,
            byte_end=byte_end,
            shard_bytes=shard_bytes,
            max_shards=max_shards,
            record_processing_budget_bytes=record_processing_budget_bytes,
            resume_cursor=resume_cursor,
        )
        yield {
            "kind": "stream_meta",
            "schema": SESSION_SHARDS_SCHEMA,
            "mode": emit,
            "source_token": current_token,
            "request_rollout": rollout_relative_path.as_posix(),
            "request_source_token": source_token,
            "request_resume_cursor": resume_cursor,
            "request_binding": request_binding,
            "source_bytes": source_bytes,
            "byte_start": byte_start,
            "byte_end": effective_end if emit == "records" else None,
            "record_start": record_start,
            "shard_bytes": shard_bytes,
            "max_shards": max_shards,
            "record_processing_budget_bytes": record_processing_budget_bytes,
            "fixed_memory_envelope_bytes": (MIN_SESSION_RECORD_PROCESSING_BUDGET_BYTES),
            "hard_record_processing_ceiling_bytes": (
                HARD_SESSION_RECORD_PROCESSING_CEILING_BYTES
            ),
            "hard_record_scan_ceiling_bytes": (HARD_SESSION_RECORD_SCAN_CEILING_BYTES),
            "record_fragment_bytes": SESSION_SHARDS_RECORD_FRAGMENT_BYTES,
            "json_nesting_depth_limit": SESSION_SHARDS_MAX_JSON_NESTING_DEPTH,
            "max_remote_frame_chars": MAX_SESSION_SHARDS_FRAME_CHARS,
            "protocol_features": list(SESSION_SHARDS_PROTOCOL_FEATURES),
        }

        records = _iter_session_shard_records(
            handle,
            byte_start=byte_start,
            byte_end=effective_end,
            record_start=record_start,
            record_processing_budget_bytes=record_processing_budget_bytes,
        )
        if emit == "descriptors":
            descriptors = iter(
                _iter_session_shard_descriptors(
                    records,
                    shard_bytes=shard_bytes,
                    max_shards=max_shards,
                    record_processing_budget_bytes=(record_processing_budget_bytes),
                )
            )
            emitted = 0
            last_byte_end = byte_start
            last_record_end = record_start
            for page_index, descriptor in enumerate(descriptors):
                descriptor["page_shard_index"] = page_index
                descriptor["schema"] = SESSION_SHARDS_SCHEMA
                descriptor["mode"] = emit
                descriptor["source_token"] = current_token
                descriptor["request_binding"] = request_binding
                descriptor["resume_cursor"] = _session_shards_resume_cursor(
                    source_identity,
                    current_token,
                    byte_offset=int(descriptor["byte_start"]),
                    next_record_index=int(descriptor["record_start"]),
                )
                yield descriptor
                emitted += 1
                last_byte_end = int(descriptor["byte_end"])
                last_record_end = int(descriptor["record_end"])
            complete = last_byte_end == source_bytes
            if emitted < max_shards and not complete:
                raise RuntimeError(
                    "session-shards descriptors ended before the source was accounted"
                )
            terminal = {
                "kind": "stream_end",
                "schema": SESSION_SHARDS_SCHEMA,
                "mode": emit,
                "source_token": current_token,
                "request_binding": request_binding,
                "complete": complete,
                "reason": "eof" if complete else "max_shards",
                "emitted_shards": emitted,
                "byte_start": byte_start,
                "byte_end": last_byte_end,
                "record_start": record_start,
                "record_end": last_record_end,
                "next_byte_start": None if complete else last_byte_end,
                "next_record_start": None if complete else last_record_end,
                "next_resume_cursor": None
                if complete
                else _session_shards_resume_cursor(
                    source_identity,
                    current_token,
                    byte_offset=last_byte_end,
                    next_record_index=last_record_end,
                ),
                "accounted_byte_count": last_byte_end - byte_start,
                "accounted_record_count": last_record_end - record_start,
            }
        else:
            emitted_records = 0
            emitted_gaps = 0
            emitted_fragments = 0
            emitted_record_bytes = 0
            emitted_gap_bytes = 0
            emitted_fragment_bytes = 0
            accounting_hasher = hashlib.sha256()
            last_record_end = record_start
            for item in records:
                common = {
                    "schema": SESSION_SHARDS_SCHEMA,
                    "mode": emit,
                    "source_token": current_token,
                    "request_binding": request_binding,
                    "byte_start": item.byte_start,
                    "byte_end": item.byte_end,
                    "record_start": item.record_index,
                    "record_end": item.record_index + 1,
                }
                if item.gap_reason is None:
                    for frame in _iter_session_record_transport_frames(
                        item,
                        shard_bytes=shard_bytes,
                        source_token=current_token,
                        request_binding=request_binding,
                    ):
                        accounting_hasher.update(
                            _session_shards_accounting_bytes(frame)
                        )
                        if frame["kind"] == "record_fragment":
                            emitted_fragments += 1
                            emitted_fragment_bytes += int(frame["byte_count"])
                        yield frame
                    emitted_records += 1
                    emitted_record_bytes += item.byte_end - item.byte_start
                else:
                    frame = {
                        "kind": "gap",
                        **common,
                        "byte_count": item.byte_end - item.byte_start,
                        "delimiter_bytes": item.delimiter_bytes,
                        "reason": item.gap_reason,
                    }
                    if item.gap_reason == "record_processing_budget_exceeded":
                        frame.update(
                            _session_shards_processing_gap_metadata(
                                item,
                                record_processing_budget_bytes,
                            )
                        )
                    accounting_hasher.update(_session_shards_accounting_bytes(frame))
                    yield frame
                    emitted_gaps += 1
                    emitted_gap_bytes += item.byte_end - item.byte_start
                last_record_end = item.record_index + 1
            accounted_byte_count = emitted_record_bytes + emitted_gap_bytes
            expected_byte_count = effective_end - byte_start
            accounted_record_count = emitted_records + emitted_gaps
            expected_record_count = last_record_end - record_start
            if (
                accounted_byte_count != expected_byte_count
                or accounted_record_count != expected_record_count
            ):
                raise RuntimeError(
                    "session-shards record transport failed byte conservation"
                )
            terminal = {
                "kind": "stream_end",
                "schema": SESSION_SHARDS_SCHEMA,
                "mode": emit,
                "source_token": current_token,
                "request_binding": request_binding,
                "complete": True,
                "reason": "range_complete",
                "emitted_records": emitted_records,
                "emitted_gaps": emitted_gaps,
                "emitted_fragments": emitted_fragments,
                "emitted_record_bytes": emitted_record_bytes,
                "emitted_gap_bytes": emitted_gap_bytes,
                "emitted_fragment_bytes": emitted_fragment_bytes,
                "byte_start": byte_start,
                "byte_end": effective_end,
                "record_start": record_start,
                "record_end": last_record_end,
                "conservation_proof": {
                    "schema": "session-shards-conservation-v1",
                    "source_token": current_token,
                    "request_binding": request_binding,
                    "byte_start": byte_start,
                    "byte_end": effective_end,
                    "byte_count": expected_byte_count,
                    "accounted_byte_count": accounted_byte_count,
                    "record_start": record_start,
                    "record_end": last_record_end,
                    "record_count": expected_record_count,
                    "accounted_record_count": accounted_record_count,
                    "accounting_commitment": (
                        "sha256:" + accounting_hasher.hexdigest()
                    ),
                },
            }

        if (
            _session_shards_source_identity(os.fstat(handle.fileno()))
            != source_identity
        ):
            raise RuntimeError("source changed during session-shards read")
        yield terminal


def _parse_kv_lines(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key] = value
    return result


def _run_subprocess_text(
    argv: list[str],
    *,
    input_text: str | None = None,
    timeout_seconds: int | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"command not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        if timeout_seconds is None:
            raise RuntimeError("command timed out") from exc
        raise RuntimeError(f"command timed out after {timeout_seconds}s") from exc


def _local_preflight_row(alias: str) -> dict[str, str]:
    codex_root = _local_codex_root()
    return {
        "host": alias,
        "hostname": socket.gethostname(),
        "user": os.getenv("USER", ""),
        "home": str(pathlib.Path.home()),
        "codex": "present" if codex_root.is_dir() else "missing",
        "rg": "present" if _which("rg") else "missing",
        "python3": "present" if _which("python3") else "missing",
    }


def _which(binary: str) -> str | None:
    for directory in os.getenv("PATH", "").split(os.pathsep):
        if not directory:
            continue
        candidate = pathlib.Path(directory) / binary
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _remote_preflight_row(alias: str) -> dict[str, str]:
    ssh_target = HOSTS[alias]["ssh_target"]
    result = _run_subprocess_text(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            ssh_target,
            f"CODEX_REMOTE_ROOT={shlex.quote(HOSTS[alias]['codex_root'])} {REMOTE_PREFLIGHT_SCRIPT}",
        ],
        timeout_seconds=REMOTE_PREFLIGHT_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        message = (
            result.stderr.strip() or result.stdout.strip() or "ssh preflight failed"
        )
        raise RuntimeError(message)
    row = _parse_kv_lines(result.stdout)
    row["host"] = alias
    return row


def _remote_python_script(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return f"""
import base64
import collections
import json
import os
import pathlib
import re
import stat
import sys

CONFIG = json.loads({encoded!r})
DATE_STRINGS = CONFIG.get("dates", [])
LIMIT = int(CONFIG.get("limit", 0))
ROOT = pathlib.Path(CONFIG["codex_root"]).expanduser()
MAX_FETCH_ROLLOUT_BYTES = int(CONFIG.get("max_fetch_rollout_bytes", 0))
MAX_FETCH_ROLLOUT_CHUNK_BYTES = int(CONFIG.get("max_fetch_rollout_chunk_bytes", 0))
SESSION_META_SCAN_BYTES = int(CONFIG.get("session_meta_scan_bytes", 0))
SUMMARY_LIMIT = int(CONFIG.get("summary_limit", 0))
SUMMARY_SCAN_BYTES = int(CONFIG.get("summary_scan_bytes", 0))
SUMMARY_LINE_BYTES = int(CONFIG.get("summary_line_bytes", 0)) or {MAX_ROLLOUT_SUMMARY_LINE_BYTES}
SUMMARY_TAIL_RECORDS = int(CONFIG.get("summary_tail_records", 0))
SUMMARY_MAX_TEXT_CHARS = int(CONFIG.get("summary_max_text_chars", 0))
SUMMARY_MAX_TEXT_CHARS_LIMIT = {MAX_ROLLOUT_SUMMARY_TEXT_CHARS}
SUMMARY_KEYWORDS = [str(value) for value in CONFIG.get("summary_keywords", [])]
CHUNK_BYTES = int(CONFIG.get("chunk_bytes", 0))
FETCH_CHUNK_BYTE_START = int(CONFIG.get("byte_start", 0))
FETCH_CHUNK_BYTE_END = int(CONFIG.get("byte_end", 0))
ACTIVE_ROLLOUT_RELATIVE_RE = re.compile({ACTIVE_ROLLOUT_RELATIVE_RE.pattern!r})
ARCHIVED_ROLLOUT_RELATIVE_RE = re.compile({ARCHIVED_ROLLOUT_RELATIVE_RE.pattern!r})
PRIVATE_IPV4_SIGNAL_RE = re.compile({PRIVATE_IPV4_SIGNAL_RE.pattern!r})
PRIVATE_IPV6_SIGNAL_RE = re.compile({PRIVATE_IPV6_SIGNAL_RE.pattern!r}, re.I)
INTERNAL_HOSTNAME_SIGNAL_RE = re.compile({INTERNAL_HOSTNAME_SIGNAL_RE.pattern!r}, re.I)
WRAPPER_PREFIXES = {WRAPPER_PREFIXES!r}
WRAPPER_END_MARKERS = {WRAPPER_END_MARKERS!r}
AUTOMATION_PROMPT_PATTERN_TEXTS = {AUTOMATION_PROMPT_PATTERN_TEXTS!r}
AUTOMATION_PROMPT_PATTERNS = tuple(re.compile(pattern, re.I) for pattern in AUTOMATION_PROMPT_PATTERN_TEXTS)
AUTOMATION_PROMPT_MARKERS = {AUTOMATION_PROMPT_MARKERS!r}
SUMMARY_SIGNAL_MARKERS = {SUMMARY_SIGNAL_MARKERS!r}
SESSION_META_BEGIN = {REMOTE_SESSION_META_BEGIN!r}
SESSION_META_END = {REMOTE_SESSION_META_END!r}
SESSION_META_LIMIT_TRUNCATED_REASON = {SESSION_META_LIMIT_TRUNCATED_REASON!r}
FETCH_ROLLOUT_BEGIN = {REMOTE_FETCH_ROLLOUT_BEGIN!r}
FETCH_ROLLOUT_END = {REMOTE_FETCH_ROLLOUT_END!r}
FETCH_ROLLOUT_CHUNK_BEGIN = {REMOTE_FETCH_ROLLOUT_CHUNK_BEGIN!r}
FETCH_ROLLOUT_CHUNK_END = {REMOTE_FETCH_ROLLOUT_CHUNK_END!r}
ROLLOUT_SUMMARY_BEGIN = {REMOTE_ROLLOUT_SUMMARY_BEGIN!r}
ROLLOUT_SUMMARY_END = {REMOTE_ROLLOUT_SUMMARY_END!r}
CHUNKED_ROLLOUT_SUMMARY_BEGIN = {REMOTE_CHUNKED_ROLLOUT_SUMMARY_BEGIN!r}
CHUNKED_ROLLOUT_SUMMARY_END = {REMOTE_CHUNKED_ROLLOUT_SUMMARY_END!r}


def path_is_relative_to(path, root):
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def safe_codex_root():
    root_stat = ROOT.lstat()
    if stat.S_ISLNK(root_stat.st_mode):
        raise ValueError("Codex root is a symlink")
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ValueError("Codex root is not a directory")
    return ROOT.resolve(strict=True)


def safe_relative_path(rel, *, expect_directory=False, expect_regular_file=False):
    root = safe_codex_root()
    target = root
    parts = rel.parts
    for index, part in enumerate(parts):
        if part in ("", ".", ".."):
            raise ValueError("path must stay under Codex root")
        target = target / part
        target_stat = target.lstat()
        if stat.S_ISLNK(target_stat.st_mode):
            raise ValueError("path uses a symlink ancestor")
        is_last = index == len(parts) - 1
        if not is_last:
            if not stat.S_ISDIR(target_stat.st_mode):
                raise ValueError("path ancestor is not a directory")
        elif expect_directory and not stat.S_ISDIR(target_stat.st_mode):
            raise ValueError("path is not a directory")
        elif expect_regular_file and not stat.S_ISREG(target_stat.st_mode):
            raise ValueError("rollout path is not a regular file")
    target_resolved = target.resolve(strict=True)
    if not path_is_relative_to(target_resolved, root):
        raise ValueError("path escapes Codex root")
    return target_resolved


def safe_rollout_path(rel):
    return safe_relative_path(rel, expect_regular_file=True)


def safe_directory_path(rel):
    return safe_relative_path(rel, expect_directory=True)


def open_rollout_text(target):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(target), flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("rollout path is not a regular file")
        handle = os.fdopen(fd, "rb")
        fd = -1
        return handle
    finally:
        if fd != -1:
            os.close(fd)


def read_rollout_bytes(target, max_bytes):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(target), flags)
    try:
        stat_result = os.fstat(fd)
        if not stat.S_ISREG(stat_result.st_mode):
            raise ValueError("rollout path is not a regular file")
        size = stat_result.st_size
        if max_bytes and size > max_bytes:
            raise ValueError("rollout too large: " + str(size) + " bytes > " + str(max_bytes))
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            return size, handle.read()
    finally:
        if fd != -1:
            os.close(fd)


def read_rollout_byte_range(target, byte_start, byte_end, max_bytes):
    if byte_start < 0:
        raise ValueError("byte start must be non-negative")
    if byte_end <= byte_start:
        raise ValueError("byte end must be greater than byte start")
    length = byte_end - byte_start
    if max_bytes and length > max_bytes:
        raise ValueError("chunk too large: " + str(length) + " bytes > " + str(max_bytes))
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(target), flags)
    try:
        stat_result = os.fstat(fd)
        if not stat.S_ISREG(stat_result.st_mode):
            raise ValueError("rollout path is not a regular file")
        if byte_end > stat_result.st_size:
            raise ValueError("byte end exceeds rollout size: " + str(byte_end) + " > " + str(stat_result.st_size))
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            handle.seek(byte_start)
            return handle.read(length)
    finally:
        if fd != -1:
            os.close(fd)


def flat_archived_rollout_matches_date(rollout, date_text):
    return rollout.name.startswith("rollout-" + date_text.replace("/", "-"))


def is_raw_rollout_file(path):
    return path.name.startswith("rollout-") and not path.name.startswith("rollout-summary")


def session_meta_rollout_dedupe_key(rel):
    parts = rel.parts
    if len(parts) >= 2 and parts[0] == "archived_sessions":
        return "archived_sessions/" + rel.name
    return rel.as_posix()


def timestamp_from_jsonl_line(line):
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return ""
    value = obj.get("timestamp")
    return str(value) if isinstance(value, str) else ""


def raw_line_parts(raw_line):
    if isinstance(raw_line, str):
        return raw_line.encode("utf-8", "surrogatepass"), raw_line
    raw_bytes = bytes(raw_line)
    return raw_bytes, raw_bytes.decode("utf-8", "replace")


def raw_line_endswith_newline(raw_line):
    if isinstance(raw_line, str):
        return raw_line.endswith("\\n")
    return bytes(raw_line).endswith(b"\\n")


def iter_rollout_chunks(handle, chunk_bytes):
    if chunk_bytes < 1:
        raise ValueError("chunk bytes must be positive")
    chunk_index = 0
    offset = 0
    record_no = 0
    lines = []
    current_bytes = 0
    byte_start = 0
    record_start = 0
    first_timestamp = ""
    last_timestamp = ""
    oversized_record = False

    def flush():
        nonlocal chunk_index, lines, current_bytes, byte_start, record_start
        nonlocal first_timestamp, last_timestamp, oversized_record
        if not lines:
            return None
        chunk = {{
            "index": chunk_index,
            "byte_start": byte_start,
            "byte_end": byte_start + current_bytes,
            "record_start": record_start,
            "record_end": record_start + len(lines) - 1,
            "first_timestamp": first_timestamp,
            "last_timestamp": last_timestamp,
            "oversized_record": oversized_record,
            "lines": tuple(lines),
        }}
        chunk_index += 1
        lines = []
        current_bytes = 0
        byte_start = 0
        record_start = 0
        first_timestamp = ""
        last_timestamp = ""
        oversized_record = False
        return chunk

    read_limit = chunk_bytes + 1

    while True:
        raw_line = handle.readline(read_limit)
        if not raw_line:
            chunk = flush()
            if chunk is not None:
                yield chunk
            return
        raw_bytes, line = raw_line_parts(raw_line)
        line_start = offset
        record_no += 1
        line_truncated = len(raw_line) == read_limit and not raw_line_endswith_newline(raw_line)
        if len(raw_bytes) > chunk_bytes or line_truncated:
            if lines:
                chunk = flush()
                if chunk is not None:
                    yield chunk

            total_bytes = len(raw_bytes)
            while line_truncated:
                segment = handle.readline(read_limit)
                if not segment:
                    break
                segment_bytes, _ = raw_line_parts(segment)
                total_bytes += len(segment_bytes)
                line_truncated = len(segment) == read_limit and not raw_line_endswith_newline(segment)

            offset += total_bytes
            yield {{
                "index": chunk_index,
                "byte_start": line_start,
                "byte_end": line_start + total_bytes,
                "record_start": record_no,
                "record_end": record_no,
                "first_timestamp": "",
                "last_timestamp": "",
                "oversized_record": True,
                "lines": ("",),
            }}
            chunk_index += 1
            continue

        offset += len(raw_bytes)

        if lines and current_bytes + len(raw_bytes) > chunk_bytes:
            chunk = flush()
            if chunk is not None:
                yield chunk

        if not lines:
            byte_start = line_start
            record_start = record_no

        timestamp = timestamp_from_jsonl_line(line)
        if timestamp and not first_timestamp:
            first_timestamp = timestamp
        if timestamp:
            last_timestamp = timestamp
        oversized_record = oversized_record or len(raw_bytes) > chunk_bytes
        lines.append(line)
        current_bytes += len(raw_bytes)


def fetch_ranges_for_byte_range(byte_start, byte_end, max_bytes):
    if byte_start < 0:
        raise ValueError("byte_start must be non-negative")
    if byte_end <= byte_start:
        raise ValueError("byte_end must be greater than byte_start")
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    ranges = []
    cursor = byte_start
    while cursor < byte_end:
        next_cursor = min(cursor + max_bytes, byte_end)
        ranges.append({{
            "range_index": len(ranges),
            "byte_start": cursor,
            "byte_end": next_cursor,
        }})
        cursor = next_cursor
    return ranges


def normalize_text(text, max_chars):
    collapsed = " ".join(str(text).replace("\\r", "\\n").split())
    if max_chars and max_chars > 3 and len(collapsed) > max_chars:
        return collapsed[: max_chars - 3] + "..."
    return collapsed


def message_summary_from_payload(payload):
    role = str(payload.get("role", ""))
    if role not in ("assistant", "user"):
        return None, None
    parts = []
    for item in payload.get("content", []):
        if not isinstance(item, dict):
            continue
        if item.get("type") not in ("input_text", "output_text", "text"):
            continue
        text = item.get("text")
        if text:
            parts.append(str(text))
    if not parts:
        return None, None
    kind = "user_message" if role == "user" else "assistant_message"
    return kind, "\\n".join(parts)


def meaningful_prompt_text(text):
    stripped = str(text).strip()
    if not stripped:
        return ""
    if any(stripped.startswith(prefix) for prefix in WRAPPER_PREFIXES):
        for marker in WRAPPER_END_MARKERS:
            index = stripped.rfind(marker)
            if index >= 0:
                candidate = stripped[index + len(marker):].strip()
                if candidate and not any(candidate.startswith(prefix) for prefix in WRAPPER_PREFIXES):
                    return candidate
        return ""
    return stripped


def meaningful_user_message_text(text):
    stripped = meaningful_prompt_text(text)
    if not stripped:
        return ""
    if any(pattern.search(stripped) for pattern in AUTOMATION_PROMPT_PATTERNS):
        return ""
    marker_count = sum(1 for marker in AUTOMATION_PROMPT_MARKERS if marker in stripped)
    if marker_count >= 2:
        return ""
    return stripped


def summary_signal_text(kind, text):
    signals = []
    if re.search(r"(?:exit(?:ed)?(?: with)? code [1-9]\\d*|failed|traceback|error:|permission denied)", text, re.I):
        signals.append("error:")
    if re.search(r"(?:approval|require_escalated|sandbox|\\bauth(?:entication|orization|[-_ ]?gated)?\\b|credential|permission denied|TCC)", text, re.I):
        signals.append("approval")
    if re.search(r"(?:not run|did not run|unable to run|could not run|untested|未运行|无法运行)", text, re.I):
        signals.append("could not run")
    if re.search(r"(?:you missed|you forgot|wrong|incorrect|not what I asked|漏了|忘了|不对|错了)", text, re.I):
        signals.append("you missed")
    if re.search(r"(?:lost context|misunderstood|I misunderstood|assumption|assumed|上下文|误解)", text, re.I):
        signals.append("assumed")
    if PRIVATE_IPV4_SIGNAL_RE.search(text) or PRIVATE_IPV6_SIGNAL_RE.search(text) or INTERNAL_HOSTNAME_SIGNAL_RE.search(text):
        signals.append("secret")
    elif re.search(
        r"(?:\\b(secret|token|credential|password|private key|production|destructive|rm -rf|reset --hard|customer data|privacy|pii)\\b|"
        r"\\b(?:(?:sk|rk)[-_](?:proj[-_])?[A-Za-z0-9_-]{{16,}}|gh[pousr]_[A-Za-z0-9_]{{16,}}|github_pat_[A-Za-z0-9_]{{16,}})\\b|"
        r"\\bAKIA[0-9A-Z]{{16}}\\b|\\bBearer\\s+[A-Za-z0-9._~+/\\-]+=*|"
        r"\\b(?:authorization|password|passwd|pwd|credential|secret(?:[\\s_-]?key)?|token|api[\\s_-]?key|private[\\s_-]?key)\\s*[:=]\\s*['\\\"]?[^'\\\"\\s,;]+|"
        r"\\beyJ[A-Za-z0-9_-]{{10,}}\\.[A-Za-z0-9_-]{{10,}}\\.[A-Za-z0-9_-]{{10,}}\\b|"
        r"(?<![0-9a-fA-F])[0-9a-fA-F]{{64}}(?![0-9a-fA-F])|"
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{{2,}}|https?://[^\\s)>\\]\\\"']+|"
        r"\\b(?:ssh://[^\\s)>\\]\\\"']+|git@[A-Za-z0-9_.-]+:[^\\s)>\\]\\\"']+)|"
        r"(?<!\\w)(?:~|/(?:Users|home|root|private|tmp|var|etc|opt|Volumes|workspace|workspaces))/[^\\s,;:)>\\]\\\"']+|"
        r"\\b(?:customer|client|account|tenant|org|repo|repository)[_-]?(?:id|name)?\\s*[:=]\\s*['\\\"]?[A-Za-z0-9_.-]+|"
        r"客户|客户数据|凭据|凭证|密钥|生产|破坏性)",
        text,
        re.I,
    ):
        signals.append("secret")
    return " ".join(signals) if signals else kind.replace("_", " ") + " present"


def safe_summary_text(kind, text):
    return summary_signal_text(kind, str(text))


def event_user_message_text(payload):
    message = payload.get("message")
    if isinstance(message, str):
        return message.strip()
    if isinstance(message, dict):
        kind, text = message_summary_from_payload(message)
        if kind == "user_message" and text:
            return text.strip()
    text = payload.get("text")
    if isinstance(text, str):
        return text.strip()
    return ""


def summary_record(kind, text, *, line_no, timestamp, session_id=""):
    signal_text = text
    if kind == "user_message":
        signal_text = meaningful_user_message_text(text)
        if not signal_text:
            return None
    value = normalize_text(safe_summary_text(kind, signal_text), SUMMARY_MAX_TEXT_CHARS)
    if not value:
        return None
    record = {{"kind": kind, "line": line_no, "text": value, "timestamp": timestamp or ""}}
    if session_id:
        record["session_id"] = str(session_id)
    match_text = normalize_text(signal_text, SUMMARY_MAX_TEXT_CHARS)
    if match_text and match_text != value:
        record["_match_text"] = match_text
    return record


def summary_record_has_signal(record):
    if record is None or str(record.get("kind", "")) in ("session_meta", "scan_meta", "chunk_meta"):
        return False
    text = str(record.get("text", ""))
    return any(marker in text for marker in SUMMARY_SIGNAL_MARKERS)


def bounded_text_lines(handle, max_scan_bytes):
    scanned = 0
    buffer = bytearray()
    dropping_oversized_line = False
    chunk_bytes = 64 * 1024

    def line_ended(part):
        return part.endswith(b"\\n") or part.endswith(b"\\r")

    while True:
        if max_scan_bytes and scanned >= max_scan_bytes:
            if dropping_oversized_line:
                yield "\\n"
            elif buffer:
                yield bytes(buffer).decode("utf-8", "replace")
            return
        remaining = max_scan_bytes - scanned if max_scan_bytes else 0
        read_size = min(chunk_bytes, remaining) if remaining else chunk_bytes
        chunk = handle.read(read_size)
        if not chunk:
            if dropping_oversized_line:
                yield "\\n"
            elif buffer:
                yield bytes(buffer).decode("utf-8", "replace")
            return
        if isinstance(chunk, str):
            raw_bytes = chunk.encode("utf-8", "surrogatepass")
        else:
            raw_bytes = bytes(chunk)
        scanned += len(raw_bytes)
        for part in raw_bytes.splitlines(keepends=True):
            if dropping_oversized_line:
                if line_ended(part):
                    yield "\\n"
                    dropping_oversized_line = False
                continue
            if len(buffer) + len(part) > SUMMARY_LINE_BYTES:
                buffer.clear()
                dropping_oversized_line = True
                if line_ended(part):
                    yield "\\n"
                    dropping_oversized_line = False
                continue
            buffer.extend(part)
            if line_ended(part):
                yield bytes(buffer).decode("utf-8", "replace")
                buffer.clear()


def summarize_records(lines, line_offset=0):
    keywords = [value.casefold() for value in SUMMARY_KEYWORDS if value]
    matched = []
    matched_seen = set()
    signal_records = []
    signal_seen = set()
    tail = collections.deque(maxlen=SUMMARY_TAIL_RECORDS)
    session_meta_record = None
    last_assistant_record = None
    last_user_record = None
    last_task_complete_record = None

    for line_no, line in enumerate(lines, line_offset + 1):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        timestamp = str(obj.get("timestamp", ""))
        record = None
        record_type = str(obj.get("type", ""))
        if record_type == "session_meta" and session_meta_record is None:
            payload = obj.get("payload", {{}})
            record = summary_record(
                "session_meta",
                "session_id=" + str(payload.get("id", ""))
                + " cwd_present="
                + str(bool(payload.get("cwd", ""))).lower(),
                line_no=line_no,
                timestamp=timestamp,
                session_id=str(payload.get("id", "")),
            )
            session_meta_record = record
        elif record_type == "response_item":
            payload = obj.get("payload", {{}})
            payload_type = str(payload.get("type", ""))
            if payload_type == "message":
                kind, text = message_summary_from_payload(payload)
                if text:
                    record = summary_record(kind, text, line_no=line_no, timestamp=timestamp)
                    if kind == "assistant_message":
                        last_assistant_record = record
                    elif kind == "user_message" and record is not None:
                        last_user_record = record
            elif payload_type == "function_call_output":
                output = payload.get("output")
                if isinstance(output, str) and output.strip():
                    record = summary_record("function_call_output", output, line_no=line_no, timestamp=timestamp)
        elif record_type == "event_msg":
            payload = obj.get("payload", {{}})
            payload_type = str(payload.get("type", ""))
            if payload_type == "task_complete":
                text = payload.get("last_agent_message")
                if text:
                    record = summary_record("task_complete", text, line_no=line_no, timestamp=timestamp)
                    last_task_complete_record = record
            elif payload_type == "user_message":
                text = event_user_message_text(payload)
                if text:
                    record = summary_record("user_message", text, line_no=line_no, timestamp=timestamp)
                    if record is not None:
                        last_user_record = record

        if not record or record.get("kind") == "session_meta":
            continue

        if summary_record_has_signal(record):
            key = (str(record.get("kind", "")), int(record.get("line", 0)))
            if key not in signal_seen and (not SUMMARY_LIMIT or len(signal_records) < SUMMARY_LIMIT):
                signal_records.append(record)
                signal_seen.add(key)

        text_value = str(record.get("_match_text") or record.get("text", ""))
        if keywords and any(keyword in text_value.casefold() for keyword in keywords):
            key = (str(record.get("kind", "")), int(record.get("line", 0)))
            if key not in matched_seen and (not SUMMARY_LIMIT or len(matched) < SUMMARY_LIMIT):
                matched.append(record)
                matched_seen.add(key)
        if SUMMARY_TAIL_RECORDS:
            tail.append(record)

    emitted = set()
    output = []

    def append(record):
        if not record:
            return
        key = (str(record.get("kind", "")), int(record.get("line", 0)))
        if key in emitted:
            return
        payload = dict(record)
        payload.pop("_match_text", None)
        output.append(payload)
        emitted.add(key)

    append(session_meta_record)
    for record in signal_records:
        append(record)
    for record in matched:
        append(record)
    if not keywords:
        for record in tail:
            append(record)
    append(last_user_record)
    append(last_assistant_record)
    if last_assistant_record is None:
        append(last_task_complete_record)
    return output


def chunk_common_fields(chunk):
    return {{
        "chunk_index": chunk["index"],
        "byte_start": chunk["byte_start"],
        "byte_end": chunk["byte_end"],
        "record_start": chunk["record_start"],
        "record_end": chunk["record_end"],
        "first_timestamp": chunk["first_timestamp"],
        "last_timestamp": chunk["last_timestamp"],
        "record_count": len(chunk["lines"]),
    }}


def chunk_reason_codes(chunk, records):
    evidence_records = [
        record
        for record in records
        if str(record.get("kind", "")) not in ("session_meta", "scan_meta", "chunk_meta")
    ]
    codes = []
    if chunk["oversized_record"]:
        codes.append("oversized_record")
    if not evidence_records:
        codes.append("no_structured_evidence")
    if not any(record.get("kind") == "user_message" for record in evidence_records):
        codes.append("missing_meaningful_user_message")
    if not any(record.get("kind") in ("assistant_message", "task_complete") for record in evidence_records):
        codes.append("missing_final_summary")
    if any(summary_record_has_signal(record) for record in evidence_records):
        codes.append("signal_or_redaction_present")
    return codes


def chunk_meta_record(chunk, records, source_bytes, chunk_bytes):
    reason_codes = chunk_reason_codes(chunk, records)
    redacted_or_signal_only_records = sum(1 for record in records if summary_record_has_signal(record))
    raw_fetch_recommended = (
        bool(chunk["oversized_record"])
        or "no_structured_evidence" in reason_codes
        or redacted_or_signal_only_records > 0
    )
    meta = {{
        "kind": "chunk_meta",
        "line": chunk["record_start"],
        "source_bytes": source_bytes,
        "chunk_bytes": chunk_bytes,
        "coverage_status": "partial" if raw_fetch_recommended else "complete",
        "reason_codes": reason_codes,
        "records_emitted": len(records),
        "redacted_or_signal_only_records": redacted_or_signal_only_records,
        "raw_fetch_recommended": raw_fetch_recommended,
        "timestamp": chunk["first_timestamp"],
    }}
    meta.update(chunk_common_fields(chunk))
    if raw_fetch_recommended:
        fetch_ranges = fetch_ranges_for_byte_range(
            chunk["byte_start"],
            chunk["byte_end"],
            MAX_FETCH_ROLLOUT_CHUNK_BYTES,
        )
        meta["fetch_ranges"] = fetch_ranges
        meta["fetch_range_count"] = len(fetch_ranges)
        meta["fetch_chunk_bytes"] = MAX_FETCH_ROLLOUT_CHUNK_BYTES
    return meta


def summarize_rollout_chunks():
    rel = pathlib.PurePosixPath(str(CONFIG["rollout"]))
    normalized = rel.as_posix()
    print(CHUNKED_ROLLOUT_SUMMARY_BEGIN)
    if SUMMARY_MAX_TEXT_CHARS < 40 or SUMMARY_MAX_TEXT_CHARS > SUMMARY_MAX_TEXT_CHARS_LIMIT:
        print(json.dumps({{"ok": False, "error": "summary max text chars out of range"}}, separators=(",", ":"), sort_keys=True))
        print(CHUNKED_ROLLOUT_SUMMARY_END)
        return
    if CHUNK_BYTES < 1:
        print(json.dumps({{"ok": False, "error": "chunk bytes out of range"}}, separators=(",", ":"), sort_keys=True))
        print(CHUNKED_ROLLOUT_SUMMARY_END)
        return
    if not (
        ACTIVE_ROLLOUT_RELATIVE_RE.fullmatch(normalized)
        or ARCHIVED_ROLLOUT_RELATIVE_RE.fullmatch(normalized)
    ):
        print(json.dumps({{"ok": False, "error": "invalid rollout path"}}, separators=(",", ":"), sort_keys=True))
        print(CHUNKED_ROLLOUT_SUMMARY_END)
        return
    try:
        target = safe_rollout_path(rel)
    except FileNotFoundError:
        print(json.dumps({{"ok": False, "error": "rollout not found"}}, separators=(",", ":"), sort_keys=True))
        print(CHUNKED_ROLLOUT_SUMMARY_END)
        return
    except ValueError as error:
        print(json.dumps({{"ok": False, "error": str(error)}}, separators=(",", ":"), sort_keys=True))
        print(CHUNKED_ROLLOUT_SUMMARY_END)
        return
    try:
        source_bytes = target.stat().st_size
        handle = open_rollout_text(target)
    except OSError:
        print(json.dumps({{"ok": False, "error": "rollout unreadable"}}, separators=(",", ":"), sort_keys=True))
        print(CHUNKED_ROLLOUT_SUMMARY_END)
        return
    print(json.dumps({{"ok": True}}, separators=(",", ":"), sort_keys=True))
    with handle:
        try:
            chunks = iter_rollout_chunks(handle, CHUNK_BYTES)
            for chunk in chunks:
                records = summarize_records(chunk["lines"], line_offset=int(chunk["record_start"]) - 1)
                common = chunk_common_fields(chunk)
                print(json.dumps(chunk_meta_record(chunk, records, source_bytes, CHUNK_BYTES), separators=(",", ":"), sort_keys=True))
                for record in records:
                    payload = dict(record)
                    payload.update(common)
                    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
        except ValueError as error:
            print(json.dumps({{"kind": "error", "error": str(error)}}, separators=(",", ":"), sort_keys=True))
    print(CHUNKED_ROLLOUT_SUMMARY_END)


def summarize_rollout():
    rel = pathlib.PurePosixPath(str(CONFIG["rollout"]))
    normalized = rel.as_posix()
    print(ROLLOUT_SUMMARY_BEGIN)
    if SUMMARY_MAX_TEXT_CHARS < 40 or SUMMARY_MAX_TEXT_CHARS > SUMMARY_MAX_TEXT_CHARS_LIMIT:
        print(json.dumps({{"ok": False, "error": "summary max text chars out of range"}}, separators=(",", ":"), sort_keys=True))
        print(ROLLOUT_SUMMARY_END)
        return
    if not (
        ACTIVE_ROLLOUT_RELATIVE_RE.fullmatch(normalized)
        or ARCHIVED_ROLLOUT_RELATIVE_RE.fullmatch(normalized)
    ):
        print(json.dumps({{"ok": False, "error": "invalid rollout path"}}, separators=(",", ":"), sort_keys=True))
        print(ROLLOUT_SUMMARY_END)
        return
    try:
        target = safe_rollout_path(rel)
    except FileNotFoundError:
        print(json.dumps({{"ok": False, "error": "rollout not found"}}, separators=(",", ":"), sort_keys=True))
        print(ROLLOUT_SUMMARY_END)
        return
    except ValueError as error:
        print(json.dumps({{"ok": False, "error": str(error)}}, separators=(",", ":"), sort_keys=True))
        print(ROLLOUT_SUMMARY_END)
        return

    keywords = [value.casefold() for value in SUMMARY_KEYWORDS if value]
    matched = []
    matched_seen = set()
    signal_records = []
    signal_seen = set()
    tail = collections.deque(maxlen=SUMMARY_TAIL_RECORDS)
    session_meta_record = None
    last_assistant_record = None
    last_user_record = None
    last_task_complete_record = None

    try:
        target_size = target.stat().st_size
        handle = open_rollout_text(target)
    except OSError:
        print(json.dumps({{"ok": False, "error": "rollout unreadable"}}, separators=(",", ":"), sort_keys=True))
        print(ROLLOUT_SUMMARY_END)
        return
    with handle:
        for line_no, line in enumerate(bounded_text_lines(handle, SUMMARY_SCAN_BYTES), 1):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            timestamp = str(obj.get("timestamp", ""))
            record = None
            record_type = str(obj.get("type", ""))
            if record_type == "session_meta" and session_meta_record is None:
                payload = obj.get("payload", {{}})
                record = summary_record(
                    "session_meta",
                    "session_id=" + str(payload.get("id", ""))
                    + " cwd_present="
                    + str(bool(payload.get("cwd", ""))).lower(),
                    line_no=line_no,
                    timestamp=timestamp,
                    session_id=str(payload.get("id", "")),
                )
                session_meta_record = record
            elif record_type == "response_item":
                payload = obj.get("payload", {{}})
                payload_type = str(payload.get("type", ""))
                if payload_type == "message":
                    kind, text = message_summary_from_payload(payload)
                    if text:
                        record = summary_record(kind, text, line_no=line_no, timestamp=timestamp)
                        if kind == "assistant_message":
                            last_assistant_record = record
                        elif kind == "user_message" and record is not None:
                            last_user_record = record
                elif payload_type == "function_call_output":
                    output = payload.get("output")
                    if isinstance(output, str) and output.strip():
                        record = summary_record("function_call_output", output, line_no=line_no, timestamp=timestamp)
            elif record_type == "event_msg":
                payload = obj.get("payload", {{}})
                payload_type = str(payload.get("type", ""))
                if payload_type == "task_complete":
                    text = payload.get("last_agent_message")
                    if text:
                        record = summary_record("task_complete", text, line_no=line_no, timestamp=timestamp)
                        last_task_complete_record = record
                elif payload_type == "user_message":
                    text = event_user_message_text(payload)
                    if text:
                        record = summary_record("user_message", text, line_no=line_no, timestamp=timestamp)
                        if record is not None:
                            last_user_record = record

            if not record or record.get("kind") == "session_meta":
                continue

            if summary_record_has_signal(record):
                key = (str(record.get("kind", "")), int(record.get("line", 0)))
                if key not in signal_seen and (not SUMMARY_LIMIT or len(signal_records) < SUMMARY_LIMIT):
                    signal_records.append(record)
                    signal_seen.add(key)

            text_value = str(record.get("_match_text") or record.get("text", ""))
            if keywords and any(keyword in text_value.casefold() for keyword in keywords):
                key = (str(record.get("kind", "")), int(record.get("line", 0)))
                if key not in matched_seen and (not SUMMARY_LIMIT or len(matched) < SUMMARY_LIMIT):
                    matched.append(record)
                    matched_seen.add(key)
            if SUMMARY_TAIL_RECORDS:
                tail.append(record)

    print(json.dumps({{"ok": True}}, separators=(",", ":"), sort_keys=True))
    print(json.dumps(
        {{
            "kind": "scan_meta",
            "line": 0,
            "scan_bytes": SUMMARY_SCAN_BYTES,
            "scan_truncated": bool(SUMMARY_SCAN_BYTES and target_size > SUMMARY_SCAN_BYTES),
            "source_bytes": target_size,
            "text": "scan_truncated=" + str(bool(SUMMARY_SCAN_BYTES and target_size > SUMMARY_SCAN_BYTES)).lower()
                + " scan_bytes=" + str(SUMMARY_SCAN_BYTES)
                + " source_bytes=" + str(target_size),
            "timestamp": "",
        }},
        separators=(",", ":"),
        sort_keys=True,
    ))
    emitted = set()

    def emit(record):
        if not record:
            return
        key = (str(record.get("kind", "")), int(record.get("line", 0)))
        if key in emitted:
            return
        payload = dict(record)
        payload.pop("_match_text", None)
        print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
        emitted.add(key)

    emit(session_meta_record)
    for record in signal_records:
        emit(record)
    for record in matched:
        emit(record)
    if not keywords:
        for record in tail:
            emit(record)
    emit(last_user_record)
    emit(last_assistant_record)
    if last_assistant_record is None:
        emit(last_task_complete_record)
    print(ROLLOUT_SUMMARY_END)


def iter_session_meta():
    try:
        root = safe_codex_root()
    except FileNotFoundError:
        print(SESSION_META_BEGIN)
        print(SESSION_META_END)
        return
    print(SESSION_META_BEGIN)

    def session_directory_unreadable():
        print(json.dumps({{"kind": "error", "error": "session directory unreadable"}}, separators=(",", ":"), sort_keys=True))
        print(SESSION_META_END)
        raise SystemExit(0)

    def sorted_rollout_paths(directory):
        try:
            return sorted(directory.glob("rollout-*.jsonl"), reverse=True)
        except OSError:
            session_directory_unreadable()

    count = 0
    seen_session_ids = set()
    for date_text in reversed(DATE_STRINGS):
        rollout_paths = []
        for rel_dir in (pathlib.PurePosixPath("sessions") / date_text, pathlib.PurePosixPath("archived_sessions") / date_text):
            try:
                date_dir = safe_directory_path(rel_dir)
            except FileNotFoundError:
                continue
            except OSError:
                session_directory_unreadable()
            rollout_paths.extend(
                rollout
                for rollout in sorted_rollout_paths(date_dir)
                if is_raw_rollout_file(rollout)
            )
        try:
            flat_archived_dir = safe_directory_path(pathlib.PurePosixPath("archived_sessions"))
            rollout_paths.extend(
                rollout
                for rollout in sorted_rollout_paths(flat_archived_dir)
                if is_raw_rollout_file(rollout) and flat_archived_rollout_matches_date(rollout, date_text)
            )
        except FileNotFoundError:
            pass
        except OSError:
            session_directory_unreadable()
        seen_rollout_paths = set()
        for rollout in rollout_paths:
            rel = pathlib.PurePosixPath(rollout.relative_to(root).as_posix())
            rel_key = session_meta_rollout_dedupe_key(rel)
            if rel_key in seen_rollout_paths:
                continue
            seen_rollout_paths.add(rel_key)
            session_id = ""
            cwd = ""
            try:
                target = safe_rollout_path(rel)
            except FileNotFoundError:
                continue
            try:
                handle = open_rollout_text(target)
            except OSError:
                print(json.dumps({{"kind": "error", "error": "rollout unreadable", "rollout": rel.as_posix()}}, separators=(",", ":"), sort_keys=True))
                print(SESSION_META_END)
                return
            with handle:
                for line in bounded_text_lines(handle, SESSION_META_SCAN_BYTES):
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("type") != "session_meta":
                        continue
                    payload = obj.get("payload", {{}})
                    session_id = str(payload.get("id", ""))
                    cwd = str(payload.get("cwd", ""))
                    break
            if session_id:
                if session_id in seen_session_ids:
                    continue
                seen_session_ids.add(session_id)
                count += 1
                if LIMIT and count > LIMIT:
                    print(json.dumps({{"kind": "truncation", "reason": SESSION_META_LIMIT_TRUNCATED_REASON, "date": date_text, "limit": LIMIT}}, separators=(",", ":"), sort_keys=True))
                    print(SESSION_META_END)
                    return
                print(json.dumps({{"date": date_text, "session_id": session_id, "cwd": cwd, "rollout": rollout.relative_to(root).as_posix()}}, separators=(",", ":"), sort_keys=True))
    print(SESSION_META_END)


def fetch_rollout():
    rel = pathlib.PurePosixPath(str(CONFIG["rollout"]))
    normalized = rel.as_posix()
    print(FETCH_ROLLOUT_BEGIN)
    if not (
        ACTIVE_ROLLOUT_RELATIVE_RE.fullmatch(normalized)
        or ARCHIVED_ROLLOUT_RELATIVE_RE.fullmatch(normalized)
    ):
        print(json.dumps({{"ok": False, "error": "invalid rollout path"}}, separators=(",", ":"), sort_keys=True))
        print(FETCH_ROLLOUT_END)
        return
    try:
        target = safe_rollout_path(rel)
        size, data = read_rollout_bytes(target, MAX_FETCH_ROLLOUT_BYTES)
    except FileNotFoundError:
        print(json.dumps({{"ok": False, "error": "rollout not found"}}, separators=(",", ":"), sort_keys=True))
        print(FETCH_ROLLOUT_END)
        return
    except OSError:
        print(json.dumps({{"ok": False, "error": "rollout unreadable"}}, separators=(",", ":"), sort_keys=True))
        print(FETCH_ROLLOUT_END)
        return
    except ValueError as error:
        print(json.dumps({{"ok": False, "error": str(error)}}, separators=(",", ":"), sort_keys=True))
        print(FETCH_ROLLOUT_END)
        return
    payload = base64.b64encode(data).decode("ascii")
    print(json.dumps({{"ok": True, "bytes": size}}, separators=(",", ":"), sort_keys=True))
    print(payload)
    print(FETCH_ROLLOUT_END)


def fetch_rollout_chunk():
    rel = pathlib.PurePosixPath(str(CONFIG["rollout"]))
    normalized = rel.as_posix()
    print(FETCH_ROLLOUT_CHUNK_BEGIN)
    if not (
        ACTIVE_ROLLOUT_RELATIVE_RE.fullmatch(normalized)
        or ARCHIVED_ROLLOUT_RELATIVE_RE.fullmatch(normalized)
    ):
        print(json.dumps({{"ok": False, "error": "invalid rollout path"}}, separators=(",", ":"), sort_keys=True))
        print(FETCH_ROLLOUT_CHUNK_END)
        return
    try:
        target = safe_rollout_path(rel)
        data = read_rollout_byte_range(
            target,
            FETCH_CHUNK_BYTE_START,
            FETCH_CHUNK_BYTE_END,
            MAX_FETCH_ROLLOUT_CHUNK_BYTES,
        )
    except FileNotFoundError:
        print(json.dumps({{"ok": False, "error": "rollout not found"}}, separators=(",", ":"), sort_keys=True))
        print(FETCH_ROLLOUT_CHUNK_END)
        return
    except OSError:
        print(json.dumps({{"ok": False, "error": "rollout unreadable"}}, separators=(",", ":"), sort_keys=True))
        print(FETCH_ROLLOUT_CHUNK_END)
        return
    except ValueError as error:
        print(json.dumps({{"ok": False, "error": str(error)}}, separators=(",", ":"), sort_keys=True))
        print(FETCH_ROLLOUT_CHUNK_END)
        return
    payload = base64.b64encode(data).decode("ascii")
    print(json.dumps({{"ok": True, "bytes": len(data)}}, separators=(",", ":"), sort_keys=True))
    print(payload)
    print(FETCH_ROLLOUT_CHUNK_END)


if CONFIG["mode"] == "session-meta":
    try:
        iter_session_meta()
    except ValueError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
elif CONFIG["mode"] == "fetch-rollout":
    fetch_rollout()
elif CONFIG["mode"] == "fetch-rollout-chunk":
    fetch_rollout_chunk()
elif CONFIG["mode"] == "rollout-summary":
    summarize_rollout()
elif CONFIG["mode"] == "chunked-rollout-summary":
    summarize_rollout_chunks()
else:
    raise SystemExit("unknown mode: " + str(CONFIG["mode"]))
""".lstrip()


def _remote_session_shards_script(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    shared_source = "\n\n".join(
        inspect.getsource(item)
        for item in (
            SessionShardRecord,
            _SessionShardsProcessingBudgetExceeded,
            _IncrementalJSONObjectValidator,
            _validate_session_shards_json_storage,
            _path_is_relative_to,
            _resolve_safe_codex_root,
            _safe_relative_path,
            _safe_rollout_path,
            _session_shards_source_identity,
            _session_shards_source_identity_bytes,
            _session_shards_source_token,
            _session_shards_resume_cursor,
            _session_shards_decode_resume_cursor,
            _session_shards_parse_resume_cursor,
            _session_shards_request_binding,
            _open_session_shard_source,
            _session_shards_record_index_at_offset,
            _validate_session_shards_boundary,
            _reject_nonstandard_json_constant,
            _iter_session_shard_records,
            _session_shards_processing_gap_metadata,
            _iter_session_shard_descriptors,
            _session_shards_content_commitment,
            _session_shards_accounting_bytes,
            _iter_session_record_transport_frames,
            _iter_local_session_shard_frames,
        )
    )
    return f"""from __future__ import annotations

import dataclasses
import base64
import binascii
import codecs
import errno
import hashlib
import hmac
import json
import os
import pathlib
import re
import stat
import sys
import tempfile
from collections.abc import Iterable
from typing import Any

CONFIG = json.loads({encoded!r})
ACTIVE_ROLLOUT_RELATIVE_RE = re.compile({SESSION_SHARDS_ACTIVE_ROLLOUT_RELATIVE_RE.pattern!r})
ARCHIVED_ROLLOUT_RELATIVE_RE = re.compile({SESSION_SHARDS_ARCHIVED_ROLLOUT_RELATIVE_RE.pattern!r})
ROOT_ROLLOUT_RELATIVE_RE = re.compile({ROOT_ROLLOUT_RELATIVE_RE.pattern!r})
MAX_SESSION_SHARDS_RANGE_BYTES = {MAX_SESSION_SHARDS_RANGE_BYTES}
DEFAULT_SESSION_RECORD_PROCESSING_BUDGET_BYTES = {DEFAULT_SESSION_RECORD_PROCESSING_BUDGET_BYTES}
HARD_SESSION_RECORD_PROCESSING_CEILING_BYTES = {HARD_SESSION_RECORD_PROCESSING_CEILING_BYTES}
HARD_SESSION_RECORD_SCAN_CEILING_BYTES = {HARD_SESSION_RECORD_SCAN_CEILING_BYTES}
MIN_SESSION_RECORD_PROCESSING_BUDGET_BYTES = {MIN_SESSION_RECORD_PROCESSING_BUDGET_BYTES}
SESSION_SHARDS_RECORD_FRAGMENT_BYTES = {SESSION_SHARDS_RECORD_FRAGMENT_BYTES}
SESSION_SHARDS_RECORD_SCAN_CHUNK_BYTES = {SESSION_SHARDS_RECORD_SCAN_CHUNK_BYTES}
SESSION_SHARDS_RECORD_SPOOL_MEMORY_BYTES = {SESSION_SHARDS_RECORD_SPOOL_MEMORY_BYTES}
SESSION_SHARDS_JSON_VALIDATION_CHUNK_BYTES = {SESSION_SHARDS_JSON_VALIDATION_CHUNK_BYTES}
SESSION_SHARDS_MAX_JSON_NESTING_DEPTH = {SESSION_SHARDS_MAX_JSON_NESTING_DEPTH}
MAX_SESSION_SHARDS_FRAME_CHARS = {MAX_SESSION_SHARDS_FRAME_CHARS}
SESSION_SHARDS_SCHEMA = {SESSION_SHARDS_SCHEMA!r}
SESSION_SHARDS_SOURCE_TOKEN_PREFIX = {SESSION_SHARDS_SOURCE_TOKEN_PREFIX!r}
SESSION_SHARDS_RESUME_CURSOR_PREFIX = {SESSION_SHARDS_RESUME_CURSOR_PREFIX!r}
SESSION_SHARDS_REQUEST_BINDING_PREFIX = {SESSION_SHARDS_REQUEST_BINDING_PREFIX!r}
SESSION_SHARDS_PROTOCOL_FEATURES = {SESSION_SHARDS_PROTOCOL_FEATURES!r}
SESSION_SHARDS_BEGIN = {REMOTE_SESSION_SHARDS_BEGIN!r}
SESSION_SHARDS_END = {REMOTE_SESSION_SHARDS_END!r}

{shared_source}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    print(SESSION_SHARDS_BEGIN, flush=True)
    try:
        rollout = pathlib.PurePosixPath(str(CONFIG["rollout"]))
        normalized = rollout.as_posix()
        if not (
            ACTIVE_ROLLOUT_RELATIVE_RE.fullmatch(normalized)
            or ARCHIVED_ROLLOUT_RELATIVE_RE.fullmatch(normalized)
            or ROOT_ROLLOUT_RELATIVE_RE.fullmatch(normalized)
        ):
            raise ValueError("invalid rollout path")
        byte_end_value = CONFIG.get("byte_end")
        frames = _iter_local_session_shard_frames(
            codex_root=pathlib.Path(str(CONFIG["codex_root"])),
            rollout_relative_path=rollout,
            emit=str(CONFIG["emit"]),
            byte_start=int(CONFIG.get("byte_start", 0)),
            byte_end=None if byte_end_value is None else int(byte_end_value),
            shard_bytes=int(CONFIG["shard_bytes"]),
            max_shards=int(CONFIG["max_shards"]),
            source_token=(
                None
                if CONFIG.get("source_token") is None
                else str(CONFIG["source_token"])
            ),
            resume_cursor=(
                None
                if CONFIG.get("resume_cursor") is None
                else str(CONFIG["resume_cursor"])
            ),
            record_processing_budget_bytes=int(
                CONFIG.get(
                    "record_processing_budget_bytes",
                    DEFAULT_SESSION_RECORD_PROCESSING_BUDGET_BYTES,
                )
            ),
        )
        for frame in frames:
            encoded_frame = json.dumps(
                frame,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            if len(encoded_frame) > MAX_SESSION_SHARDS_FRAME_CHARS:
                raise RuntimeError(
                    "session-shards frame exceeded the bounded line limit"
                )
            print(encoded_frame, flush=True)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        print("error=" + str(error), file=sys.stderr, flush=True)
        raise SystemExit(1)
    print(SESSION_SHARDS_END, flush=True)


if __name__ == "__main__":
    main()
"""


def _run_remote_python(
    alias: str, payload: dict[str, object]
) -> subprocess.CompletedProcess[str]:
    ssh_target = HOSTS[alias]["ssh_target"]
    return _run_subprocess_text(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            ssh_target,
            "python3",
            "-",
        ],
        input_text=_remote_python_script(payload),
        timeout_seconds=REMOTE_COMMAND_TIMEOUT_SECONDS,
    )


def _bounded_remote_session_shards_diagnostic(value: str) -> str:
    encoded = value.encode("utf-8", errors="replace")
    return encoded[:MAX_REMOTE_SESSION_SHARDS_DIAGNOSTIC_BYTES].decode(
        "utf-8",
        errors="ignore",
    )


def _parse_remote_session_shards_frame(value: str) -> dict[str, Any]:
    if len(value) > MAX_SESSION_SHARDS_FRAME_CHARS:
        raise RuntimeError(
            "remote session-shards frame exceeded the bounded line limit"
        )

    validator = _IncrementalJSONObjectValidator()
    encoded_bytes = 0
    try:
        for offset in range(0, len(value), SESSION_SHARDS_JSON_VALIDATION_CHUNK_BYTES):
            chunk = value[offset : offset + SESSION_SHARDS_JSON_VALIDATION_CHUNK_BYTES]
            encoded_bytes += len(chunk.encode("utf-8", errors="strict"))
            if encoded_bytes > MIN_SESSION_RECORD_PROCESSING_BUDGET_BYTES:
                raise ValueError("remote frame exceeded the fixed memory envelope")
            validator.feed(chunk)
        validator.finish()
        item = json.loads(
            value,
            object_pairs_hook=_reject_duplicate_json_object_fields,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except (UnicodeEncodeError, ValueError, RecursionError) as exc:
        raise RuntimeError(
            "remote session-shards emitted an invalid JSON frame"
        ) from exc
    if not isinstance(item, dict):
        raise RuntimeError("remote session-shards frame must be a JSON object")
    return item


def _iter_remote_session_shard_frames(
    alias: str,
    payload: dict[str, object],
) -> Iterable[dict[str, Any]]:
    ssh_target = HOSTS[alias]["ssh_target"]
    argv = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        ssh_target,
        "python3",
        "-",
    ]
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"command not found: {argv[0]}") from exc

    timed_out = False

    def terminate_on_timeout() -> None:
        nonlocal timed_out
        if process.poll() is not None:
            return
        timed_out = True
        process.kill()

    timer = threading.Timer(REMOTE_COMMAND_TIMEOUT_SECONDS, terminate_on_timeout)
    timer.daemon = True
    timer.start()
    diagnostics: collections.deque[str] = collections.deque(maxlen=1)
    saw_begin = False
    saw_end = False
    terminal: dict[str, Any] | None = None
    validator = _RemoteSessionShardsValidator(request=payload)
    returncode: int | None = None
    try:
        if process.stdin is None or process.stdout is None:
            raise RuntimeError("remote session-shards pipe setup failed")
        try:
            process.stdin.write(_remote_session_shards_script(payload))
            process.stdin.close()
        except BrokenPipeError:
            pass

        while True:
            line = process.stdout.readline(MAX_SESSION_SHARDS_FRAME_CHARS + 2)
            if line == "":
                break
            value = line.rstrip("\r\n")
            if len(value) > MAX_SESSION_SHARDS_FRAME_CHARS:
                raise RuntimeError(
                    "remote session-shards frame exceeded the bounded line limit"
                )
            if not saw_begin:
                if value == REMOTE_SESSION_SHARDS_BEGIN:
                    saw_begin = True
                elif value:
                    diagnostics.append(_bounded_remote_session_shards_diagnostic(value))
                continue
            if saw_end:
                if value:
                    raise RuntimeError(
                        "remote session-shards emitted data after the end marker"
                    )
                continue
            if value == REMOTE_SESSION_SHARDS_END:
                saw_end = True
                continue
            if value.startswith("error="):
                diagnostics.append(_bounded_remote_session_shards_diagnostic(value))
                continue
            item = _parse_remote_session_shards_frame(value)
            if terminal is not None:
                raise RuntimeError(
                    "remote session-shards emitted a frame after stream_end"
                )
            if item.get("kind") == "stream_end":
                terminal = item
            else:
                validator.accept(item)
                yield item

        returncode = process.wait(timeout=5)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait()
        raise RuntimeError(
            "remote session-shards did not terminate after its stream closed"
        ) from exc
    finally:
        timer.cancel()
        timer.join(timeout=1)
        if process.poll() is None:
            process.kill()
            process.wait()
        if process.stdout is not None:
            process.stdout.close()

    if timed_out:
        raise RuntimeError(
            f"remote session-shards timed out after {REMOTE_COMMAND_TIMEOUT_SECONDS}s"
        )
    if returncode != 0:
        message = diagnostics[-1] if diagnostics else "remote session-shards failed"
        raise RuntimeError(message)
    if not saw_begin:
        raise RuntimeError("remote session-shards missing begin marker")
    if not saw_end:
        raise RuntimeError("remote session-shards stream truncated before end marker")
    if terminal is None:
        raise RuntimeError("remote session-shards stream truncated before stream_end")
    validator.finish(terminal)
    yield terminal


def _scan_session_meta_records(
    *,
    codex_root: pathlib.Path,
    dates: list[dt.date],
    limit: int,
    host: str,
) -> SessionMetaScan:
    try:
        resolved_root = _resolve_safe_codex_root(codex_root)
    except OSError:
        return SessionMetaScan(rows=[], truncated=False)
    rows: list[dict[str, str]] = []
    seen_session_ids: set[str] = set()

    def sorted_rollout_paths(directory: pathlib.Path) -> list[pathlib.Path]:
        try:
            return sorted(directory.glob("rollout-*.jsonl"), reverse=True)
        except OSError as exc:
            raise SessionMetaRolloutError("session directory unreadable") from exc

    for date_value in reversed(dates):
        date_text = date_value.strftime(DATE_FORMAT)
        rollout_paths: list[pathlib.Path] = []
        for relative_dir in (
            pathlib.PurePosixPath("sessions") / date_text,
            pathlib.PurePosixPath("archived_sessions") / date_text,
        ):
            try:
                date_dir = _safe_directory_path(resolved_root, relative_dir)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise SessionMetaRolloutError("session directory unreadable") from exc
            rollout_paths.extend(
                rollout_path
                for rollout_path in sorted_rollout_paths(date_dir)
                if _is_raw_rollout_file(rollout_path)
            )
        try:
            flat_archived_dir = _safe_directory_path(
                resolved_root, pathlib.PurePosixPath("archived_sessions")
            )
            rollout_paths.extend(
                rollout_path
                for rollout_path in sorted_rollout_paths(flat_archived_dir)
                if _is_raw_rollout_file(rollout_path)
                and _flat_archived_rollout_matches_date(rollout_path, date_value)
            )
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise SessionMetaRolloutError("session directory unreadable") from exc
        seen_rollout_paths: set[str] = set()
        for rollout_path in rollout_paths:
            rollout_relative_path = pathlib.PurePosixPath(
                rollout_path.relative_to(resolved_root).as_posix()
            )
            rollout_relative_key = _session_meta_rollout_dedupe_key(
                rollout_relative_path
            )
            if rollout_relative_key in seen_rollout_paths:
                continue
            seen_rollout_paths.add(rollout_relative_key)
            session_id = ""
            cwd = ""
            try:
                handle = _open_local_rollout_text(resolved_root, rollout_relative_path)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise SessionMetaRolloutError(
                    "rollout unreadable",
                    rollout=rollout_relative_path.as_posix(),
                ) from exc
            with handle:
                for line in _bounded_text_lines(handle, MAX_SESSION_META_SCAN_BYTES):
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("type") != "session_meta":
                        continue
                    payload = obj.get("payload", {})
                    session_id = str(payload.get("id", ""))
                    cwd = str(payload.get("cwd", ""))
                    break
            if not session_id:
                continue
            if session_id in seen_session_ids:
                continue
            seen_session_ids.add(session_id)
            rows.append(
                {
                    "host": host,
                    "date": date_value.strftime(DATE_FORMAT),
                    "session_id": session_id,
                    "cwd": cwd,
                    "rollout": rollout_path.relative_to(resolved_root).as_posix(),
                }
            )
            if limit and len(rows) > limit:
                return SessionMetaScan(rows=rows[:limit], truncated=True)
    return SessionMetaScan(rows=rows, truncated=False)


def _iter_session_meta_records(
    *,
    codex_root: pathlib.Path,
    dates: list[dt.date],
    limit: int,
    host: str,
) -> list[dict[str, str]]:
    return _scan_session_meta_records(
        codex_root=codex_root,
        dates=dates,
        limit=limit,
        host=host,
    ).rows


def _fetch_local_rollout(
    codex_root: pathlib.Path, rollout_relative_path: pathlib.PurePosixPath
) -> bytes:
    return _read_local_rollout_bytes(
        codex_root,
        rollout_relative_path,
        max_bytes=MAX_FETCH_ROLLOUT_BYTES,
    )


def _fetch_local_rollout_chunk(
    codex_root: pathlib.Path,
    rollout_relative_path: pathlib.PurePosixPath,
    *,
    byte_start: int,
    byte_end: int,
) -> bytes:
    return _read_local_rollout_byte_range(
        codex_root,
        rollout_relative_path,
        byte_start=byte_start,
        byte_end=byte_end,
        max_bytes=MAX_FETCH_ROLLOUT_CHUNK_BYTES,
    )


def _print_tsv(rows: list[dict[str, str]], columns: list[str]) -> None:
    print("\t".join(columns))
    for row in rows:
        print("\t".join(row.get(column, "") for column in columns))


def _sort_session_meta_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(
        rows,
        key=lambda row: (
            row.get("date", ""),
            row.get("rollout", ""),
            row.get("session_id", ""),
            row.get("host", ""),
        ),
        reverse=True,
    )


def _json_line_to_dict(line: str, *, host: str) -> dict[str, Any]:
    try:
        value = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"remote helper returned a non-JSON line for host {host}: {line!r}"
        ) from exc
    if not isinstance(value, dict):
        raise ValueError(
            f"remote helper returned a non-object JSON value for host {host}"
        )
    return value


def _extract_framed_lines(
    text: str,
    *,
    begin_marker: str,
    end_marker: str,
    host: str,
    command: str,
) -> list[str]:
    started = False
    payload_lines: list[str] = []
    for line in text.splitlines():
        if not started:
            if line == begin_marker:
                started = True
            continue
        if line == end_marker:
            return payload_lines
        payload_lines.append(line)
    raise ValueError(
        f"remote {command} output on host {host} was missing framed payload markers"
    )


def _extract_framed_fetch_rollout_payload(
    text: str,
    *,
    begin_marker: str,
    end_marker: str,
    host: str,
    command: str,
    max_bytes: int = MAX_FETCH_ROLLOUT_BYTES,
) -> bytes:
    payload_lines = _extract_framed_lines(
        text,
        begin_marker=begin_marker,
        end_marker=end_marker,
        host=host,
        command=command,
    )
    if not payload_lines:
        raise ValueError(
            f"remote {command} output on host {host} was missing payload header"
        )
    try:
        header = _json_line_to_dict(payload_lines[0], host=host)
    except ValueError as exc:
        raise ValueError(
            f"remote {command} output on host {host} had an invalid payload header"
        ) from exc
    if not bool(header.get("ok")):
        error = str(header.get("error", "")).strip() or "remote fetch failed"
        if error == "rollout not found":
            raise FileNotFoundError(error)
        raise ValueError(error)
    try:
        expected_bytes = int(header["bytes"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"remote {command} output on host {host} had an invalid payload size"
        ) from exc
    if expected_bytes < 0:
        raise ValueError(
            f"remote {command} output on host {host} had a negative payload size"
        )
    payload = "".join(line.strip() for line in payload_lines[1:] if line.strip())
    if not payload:
        if expected_bytes != 0:
            raise ValueError(
                f"remote {command} output on host {host} was truncated or mismatched its payload size"
            )
        return b""
    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(
            f"remote {command} output on host {host} contained invalid base64 payload"
        ) from exc
    if len(data) != expected_bytes:
        raise ValueError(
            f"remote {command} output on host {host} was truncated or mismatched its payload size"
        )
    if len(data) > max_bytes:
        raise ValueError(f"rollout too large: {len(data)} bytes > {max_bytes}")
    return data


def _extract_framed_rollout_summary_records(
    text: str,
    *,
    begin_marker: str,
    end_marker: str,
    host: str,
    command: str,
) -> list[dict[str, Any]]:
    payload_lines = _extract_framed_lines(
        text,
        begin_marker=begin_marker,
        end_marker=end_marker,
        host=host,
        command=command,
    )
    if not payload_lines:
        raise ValueError(
            f"remote {command} output on host {host} was missing payload header"
        )
    try:
        header = _json_line_to_dict(payload_lines[0], host=host)
    except ValueError as exc:
        raise ValueError(
            f"remote {command} output on host {host} had an invalid payload header"
        ) from exc
    if not bool(header.get("ok")):
        error = str(header.get("error", "")).strip() or "remote rollout summary failed"
        if error == "rollout not found":
            raise FileNotFoundError(error)
        raise ValueError(error)

    records: list[dict[str, Any]] = []
    for line in payload_lines[1:]:
        if not line.strip():
            continue
        item = _json_line_to_dict(line, host=host)
        records.append(item)
    return records


def _session_meta_row_from_item(item: dict[str, Any], *, host: str) -> dict[str, str]:
    required_keys = ("date", "session_id", "cwd", "rollout")
    missing = [key for key in required_keys if key not in item]
    if missing:
        raise ValueError(
            f"remote helper returned incomplete session-meta payload for host {host}: missing {', '.join(missing)}"
        )
    return {
        "host": host,
        "date": str(item["date"]),
        "session_id": str(item["session_id"]),
        "cwd": str(item["cwd"]),
        "rollout": str(item["rollout"]),
    }


def _is_session_meta_truncation_item(item: dict[str, Any]) -> bool:
    return (
        item.get("kind") == "truncation"
        and item.get("reason") == SESSION_META_LIMIT_TRUNCATED_REASON
    )


def _session_meta_error_from_item(
    item: dict[str, Any],
) -> SessionMetaRolloutError | None:
    if item.get("kind") != "error":
        return None
    error = str(item.get("error", "")).strip() or "remote session-meta failed"
    rollout = item.get("rollout")
    rollout_text = str(rollout) if isinstance(rollout, str) and rollout else None
    return SessionMetaRolloutError(error, rollout=rollout_text)


def _session_meta_limit_error(host: str, limit: int) -> int:
    print(f"host={host}", file=sys.stderr)
    print(
        f"error=session-meta result exceeded --limit={limit}; narrow the date/host scope or raise --limit up to {MAX_SESSION_META_LIMIT}",
        file=sys.stderr,
    )
    return 1


def cmd_preflight(args: argparse.Namespace) -> int:
    try:
        hosts = _resolve_hosts(args.host)
    except ValueError as error:
        return _error(str(error))

    rows: list[dict[str, str]] = []
    for alias in hosts:
        try:
            row = (
                _local_preflight_row(alias)
                if HOSTS[alias]["kind"] == "local"
                else _remote_preflight_row(alias)
            )
        except RuntimeError as error:
            print(f"host={alias}", file=sys.stderr)
            print(f"error={error}", file=sys.stderr)
            return 1
        row.setdefault("hostname", "")
        row.setdefault("user", "")
        row.setdefault("home", "")
        row.setdefault("codex", "missing")
        row.setdefault("rg", "missing")
        row.setdefault("python3", "missing")
        rows.append(row)

    _print_tsv(
        rows,
        ["host", "hostname", "user", "home", "codex", "rg", "python3"],
    )
    return 0


def cmd_session_meta(args: argparse.Namespace) -> int:
    try:
        hosts = _resolve_hosts(args.host)
        dates = _resolve_dates(args)
        if args.limit < 1 or args.limit > MAX_SESSION_META_LIMIT:
            raise ValueError(
                f"--limit must stay between 1 and {MAX_SESSION_META_LIMIT}"
            )
    except ValueError as error:
        return _error(str(error))

    rows: list[dict[str, str]] = []
    for alias in hosts:
        if HOSTS[alias]["kind"] == "local":
            try:
                scan = _scan_session_meta_records(
                    codex_root=_local_codex_root(),
                    dates=dates,
                    limit=args.limit,
                    host=alias,
                )
            except SessionMetaRolloutError as error:
                print(f"host={alias}", file=sys.stderr)
                if error.rollout:
                    print(f"rollout={error.rollout}", file=sys.stderr)
                print(f"error={error.error}", file=sys.stderr)
                return 1
            except ValueError as error:
                print(f"host={alias}", file=sys.stderr)
                print(f"error={error}", file=sys.stderr)
                return 1
            if scan.truncated:
                return _session_meta_limit_error(alias, args.limit)
            host_rows = scan.rows
        else:
            payload = {
                "mode": "session-meta",
                "dates": [date_value.strftime(DATE_FORMAT) for date_value in dates],
                "limit": args.limit,
                "codex_root": HOSTS[alias]["codex_root"],
                "session_meta_scan_bytes": MAX_SESSION_META_SCAN_BYTES,
            }
            try:
                result = _run_remote_python(alias, payload)
            except RuntimeError as error:
                print(f"host={alias}", file=sys.stderr)
                print(f"error={error}", file=sys.stderr)
                return 1
            if result.returncode != 0:
                print(f"host={alias}", file=sys.stderr)
                print("error=remote session-meta failed", file=sys.stderr)
                return 1
            host_rows = []
            try:
                payload_lines = _extract_framed_lines(
                    result.stdout,
                    begin_marker=REMOTE_SESSION_META_BEGIN,
                    end_marker=REMOTE_SESSION_META_END,
                    host=alias,
                    command="session-meta",
                )
            except ValueError as error:
                print(f"host={alias}", file=sys.stderr)
                print(f"error={error}", file=sys.stderr)
                return 1
            for line in payload_lines:
                if not line.strip():
                    continue
                try:
                    item = _json_line_to_dict(line, host=alias)
                except ValueError as error:
                    print(f"host={alias}", file=sys.stderr)
                    print(f"error={error}", file=sys.stderr)
                    return 1
                if _is_session_meta_truncation_item(item):
                    return _session_meta_limit_error(alias, args.limit)
                session_meta_error = _session_meta_error_from_item(item)
                if session_meta_error is not None:
                    print(f"host={alias}", file=sys.stderr)
                    if session_meta_error.rollout:
                        print(f"rollout={session_meta_error.rollout}", file=sys.stderr)
                    print(f"error={session_meta_error.error}", file=sys.stderr)
                    return 1
                try:
                    host_rows.append(_session_meta_row_from_item(item, host=alias))
                except ValueError as error:
                    print(f"host={alias}", file=sys.stderr)
                    print(f"error={error}", file=sys.stderr)
                    return 1
        rows.extend(host_rows)
        if len(rows) > args.limit:
            return _session_meta_limit_error("all", args.limit)
    rows = _sort_session_meta_rows(rows)

    _print_tsv(rows, ["host", "date", "session_id", "cwd", "rollout"])
    return 0


def cmd_fetch_rollout(args: argparse.Namespace) -> int:
    try:
        hosts = _resolve_hosts([args.host])
        alias = hosts[0]
        rollout_relative_path = _resolve_rollout_relative_path(args.rollout)
        output = _resolve_output_path(args.output)
    except ValueError as error:
        return _error(str(error))

    try:
        if HOSTS[alias]["kind"] == "local":
            data = _fetch_local_rollout(_local_codex_root(), rollout_relative_path)
        else:
            payload = {
                "mode": "fetch-rollout",
                "rollout": rollout_relative_path.as_posix(),
                "codex_root": HOSTS[alias]["codex_root"],
                "max_fetch_rollout_bytes": MAX_FETCH_ROLLOUT_BYTES,
            }
            try:
                result = _run_remote_python(alias, payload)
            except RuntimeError as error:
                print(f"host={alias}", file=sys.stderr)
                print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
                print(f"error={error}", file=sys.stderr)
                return 1
            if result.returncode != 0:
                message = (
                    result.stderr.strip()
                    or result.stdout.strip()
                    or "remote fetch-rollout failed"
                )
                print(f"host={alias}", file=sys.stderr)
                print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
                print(f"error={message}", file=sys.stderr)
                return 1
            try:
                data = _extract_framed_fetch_rollout_payload(
                    result.stdout,
                    begin_marker=REMOTE_FETCH_ROLLOUT_BEGIN,
                    end_marker=REMOTE_FETCH_ROLLOUT_END,
                    host=alias,
                    command="fetch-rollout",
                )
            except FileNotFoundError:
                print(f"host={alias}", file=sys.stderr)
                print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
                print("error=rollout not found", file=sys.stderr)
                return 1
            except ValueError as error:
                print(f"host={alias}", file=sys.stderr)
                print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
                print(f"error={error}", file=sys.stderr)
                return 1
    except FileNotFoundError:
        print(f"host={alias}", file=sys.stderr)
        print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
        print("error=rollout not found", file=sys.stderr)
        return 1
    except OSError:
        print(f"host={alias}", file=sys.stderr)
        print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
        print("error=rollout unreadable", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"host={alias}", file=sys.stderr)
        print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
        print(f"error={error}", file=sys.stderr)
        return 1

    try:
        _write_private_bytes(output, data)
    except (OSError, ValueError) as error:
        print(f"host={alias}", file=sys.stderr)
        print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
        print(f"error={error}", file=sys.stderr)
        return 1
    print(f"host={alias}")
    print(f"rollout={rollout_relative_path.as_posix()}")
    print(f"output={output}")
    print(f"bytes={len(data)}")
    return 0


def cmd_fetch_rollout_chunk(args: argparse.Namespace) -> int:
    try:
        hosts = _resolve_hosts([args.host])
        alias = hosts[0]
        rollout_relative_path = _resolve_rollout_relative_path(args.rollout)
        output = _resolve_output_path(args.output)
        if args.byte_start < 0:
            raise ValueError("--byte-start must be non-negative")
        if args.byte_end <= args.byte_start:
            raise ValueError("--byte-end must be greater than --byte-start")
        if args.byte_end - args.byte_start > MAX_FETCH_ROLLOUT_CHUNK_BYTES:
            raise ValueError(
                f"chunk too large: {args.byte_end - args.byte_start} bytes > {MAX_FETCH_ROLLOUT_CHUNK_BYTES}"
            )
    except ValueError as error:
        return _error(str(error))

    try:
        if HOSTS[alias]["kind"] == "local":
            data = _fetch_local_rollout_chunk(
                _local_codex_root(),
                rollout_relative_path,
                byte_start=args.byte_start,
                byte_end=args.byte_end,
            )
        else:
            payload = {
                "mode": "fetch-rollout-chunk",
                "rollout": rollout_relative_path.as_posix(),
                "codex_root": HOSTS[alias]["codex_root"],
                "byte_start": args.byte_start,
                "byte_end": args.byte_end,
                "max_fetch_rollout_chunk_bytes": MAX_FETCH_ROLLOUT_CHUNK_BYTES,
            }
            try:
                result = _run_remote_python(alias, payload)
            except RuntimeError as error:
                print(f"host={alias}", file=sys.stderr)
                print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
                print(f"error={error}", file=sys.stderr)
                return 1
            if result.returncode != 0:
                message = (
                    result.stderr.strip()
                    or result.stdout.strip()
                    or "remote fetch-rollout-chunk failed"
                )
                print(f"host={alias}", file=sys.stderr)
                print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
                print(f"error={message}", file=sys.stderr)
                return 1
            try:
                data = _extract_framed_fetch_rollout_payload(
                    result.stdout,
                    begin_marker=REMOTE_FETCH_ROLLOUT_CHUNK_BEGIN,
                    end_marker=REMOTE_FETCH_ROLLOUT_CHUNK_END,
                    host=alias,
                    command="fetch-rollout-chunk",
                    max_bytes=MAX_FETCH_ROLLOUT_CHUNK_BYTES,
                )
            except FileNotFoundError:
                print(f"host={alias}", file=sys.stderr)
                print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
                print("error=rollout not found", file=sys.stderr)
                return 1
            except ValueError as error:
                print(f"host={alias}", file=sys.stderr)
                print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
                print(f"error={error}", file=sys.stderr)
                return 1
    except FileNotFoundError:
        print(f"host={alias}", file=sys.stderr)
        print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
        print("error=rollout not found", file=sys.stderr)
        return 1
    except OSError:
        print(f"host={alias}", file=sys.stderr)
        print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
        print("error=rollout unreadable", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"host={alias}", file=sys.stderr)
        print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
        print(f"error={error}", file=sys.stderr)
        return 1

    try:
        _write_private_bytes(output, data)
    except (OSError, ValueError) as error:
        print(f"host={alias}", file=sys.stderr)
        print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
        print(f"error={error}", file=sys.stderr)
        return 1
    print(f"host={alias}")
    print(f"rollout={rollout_relative_path.as_posix()}")
    print(f"byte_start={args.byte_start}")
    print(f"byte_end={args.byte_end}")
    print(f"output={output}")
    print(f"bytes={len(data)}")
    return 0


def _cmd_session_shards_holdout_receipt(args: argparse.Namespace) -> int:
    try:
        hosts = _resolve_hosts([args.host])
        alias = hosts[0]
        if HOSTS[alias]["kind"] != "ssh":
            raise ValueError(
                "controlled missing-host holdout is allowed only for a remote host"
            )
        if getattr(args, "qualification_mode", "production") != "shadow":
            raise ValueError(
                "holdout receipt is unavailable in production qualification mode"
            )
        if getattr(args, "controlled_missing_host", False) is not True:
            raise ValueError("holdout receipt requires --controlled-missing-host")
        if getattr(args, "rollout", None):
            raise ValueError("holdout receipt must not name a rollout")
        if (
            getattr(args, "byte_start", 0) != 0
            or getattr(args, "byte_end", None) is not None
            or getattr(args, "source_token", None) is not None
            or getattr(args, "resume_cursor", None) is not None
        ):
            raise ValueError(
                "holdout receipt must not carry rollout byte or token coordinates"
            )

        window_start = getattr(args, "window_start", None)
        window_end = getattr(args, "window_end", None)
        source_kind = getattr(args, "source_kind", None)
        source_lease_ref = getattr(args, "source_lease_ref", None)
        identity_value = getattr(args, "shadow_identity_path", None)
        if not all(
            isinstance(value, str) and value
            for value in (
                window_start,
                window_end,
                source_kind,
                source_lease_ref,
                identity_value,
            )
        ):
            raise ValueError(
                "holdout receipt requires window, source kind, source lease ref, "
                "and shadow identity path"
            )
        assert isinstance(window_start, str)
        assert isinstance(window_end, str)
        assert isinstance(source_kind, str)
        assert isinstance(source_lease_ref, str)
        assert isinstance(identity_value, str)
        _session_shards_holdout_daily_window(window_start, window_end)
        _validate_session_shards_holdout_source_kind(source_kind)
        _validate_session_shards_holdout_lease_ref(source_lease_ref)

        create_identity = getattr(args, "create_shadow_identity", False) is True
        require_existing_identity = (
            getattr(args, "require_existing_shadow_identity", False) is True
        )
        if create_identity == require_existing_identity:
            raise ValueError(
                "choose exactly one of --create-shadow-identity or "
                "--require-existing-shadow-identity"
            )
        identity_path = _resolve_session_shards_shadow_identity_path(
            identity_value,
            creating=create_identity,
        )
        identity_key = (
            _create_session_shards_shadow_identity(identity_path)
            if create_identity
            else _read_session_shards_shadow_identity_key(identity_path)
        )
        receipt = _session_shards_holdout_receipt(
            identity_key=identity_key,
            host=alias,
            window_start=window_start,
            window_end=window_end,
            source_kind=source_kind,
            source_lease_ref=source_lease_ref,
        )
    except (OSError, ValueError) as error:
        return _error(str(error))

    print(
        json.dumps(
            receipt,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


def _session_shards_has_holdout_only_options(args: argparse.Namespace) -> bool:
    return (
        getattr(args, "qualification_mode", "production") != "production"
        or getattr(args, "controlled_missing_host", False) is True
        or getattr(args, "window_start", None) is not None
        or getattr(args, "window_end", None) is not None
        or getattr(args, "source_kind", None) is not None
        or getattr(args, "source_lease_ref", None) is not None
        or getattr(args, "shadow_identity_path", None) is not None
        or getattr(args, "create_shadow_identity", False) is True
        or getattr(args, "require_existing_shadow_identity", False) is True
    )


def cmd_session_shards(args: argparse.Namespace) -> int:
    if args.emit == "holdout-receipt":
        return _cmd_session_shards_holdout_receipt(args)
    try:
        if _session_shards_has_holdout_only_options(args):
            raise ValueError(
                "shadow holdout qualification options require --emit holdout-receipt"
            )
        hosts = _resolve_hosts([args.host])
        alias = hosts[0]
        if not getattr(args, "rollout", None):
            raise ValueError("--rollout is required for descriptor and record modes")
        rollout_relative_path = _resolve_session_shards_rollout_relative_path(
            args.rollout
        )
        record_processing_budget_bytes = getattr(
            args,
            "record_processing_budget_bytes",
            DEFAULT_SESSION_RECORD_PROCESSING_BUDGET_BYTES,
        )
        resume_cursor = getattr(args, "resume_cursor", None)
        if args.emit not in ("descriptors", "records"):
            raise ValueError("--emit must be descriptors, records, or holdout-receipt")
        if args.byte_start < 0:
            raise ValueError("--byte-start must be non-negative")
        if args.shard_bytes < 1 or args.shard_bytes > MAX_SESSION_SHARD_BYTES:
            raise ValueError(
                f"--shard-bytes must stay between 1 and {MAX_SESSION_SHARD_BYTES}"
            )
        if args.max_shards < 1 or args.max_shards > MAX_SESSION_SHARDS_PER_PAGE:
            raise ValueError(
                f"--max-shards must stay between 1 and {MAX_SESSION_SHARDS_PER_PAGE}"
            )
        if (
            isinstance(record_processing_budget_bytes, bool)
            or not isinstance(record_processing_budget_bytes, int)
            or record_processing_budget_bytes
            < max(args.shard_bytes, MIN_SESSION_RECORD_PROCESSING_BUDGET_BYTES)
            or record_processing_budget_bytes
            > HARD_SESSION_RECORD_PROCESSING_CEILING_BYTES
        ):
            raise ValueError(
                "--record-processing-budget-bytes must cover the fixed memory "
                f"envelope of {MIN_SESSION_RECORD_PROCESSING_BUDGET_BYTES} bytes "
                f"and at most {HARD_SESSION_RECORD_PROCESSING_CEILING_BYTES}"
            )
        if resume_cursor is not None and not args.source_token:
            raise ValueError("--source-token is required with --resume-cursor")
        if args.emit == "descriptors":
            if args.byte_end is not None:
                raise ValueError("--byte-end is only valid with --emit records")
            if args.byte_start and not args.source_token:
                raise ValueError(
                    "--source-token is required when --byte-start is non-zero"
                )
        else:
            if args.byte_end is None:
                raise ValueError("--byte-end is required with --emit records")
            if args.byte_end <= args.byte_start:
                raise ValueError("--byte-end must be greater than --byte-start")
            if args.byte_end - args.byte_start > MAX_SESSION_SHARDS_RANGE_BYTES:
                raise ValueError(
                    f"record range too large: {args.byte_end - args.byte_start} bytes > {MAX_SESSION_SHARDS_RANGE_BYTES}"
                )
            if not args.source_token:
                raise ValueError("--source-token is required with --emit records")
    except ValueError as error:
        return _error(str(error))

    payload: dict[str, object] = {
        "mode": "session-shards",
        "emit": args.emit,
        "rollout": rollout_relative_path.as_posix(),
        "codex_root": HOSTS[alias]["codex_root"],
        "byte_start": args.byte_start,
        "byte_end": args.byte_end,
        "shard_bytes": args.shard_bytes,
        "max_shards": args.max_shards,
        "source_token": args.source_token,
        "resume_cursor": resume_cursor,
        "record_processing_budget_bytes": record_processing_budget_bytes,
    }
    try:
        frames = (
            _iter_local_session_shard_frames(
                codex_root=_local_codex_root(),
                rollout_relative_path=rollout_relative_path,
                emit=args.emit,
                byte_start=args.byte_start,
                byte_end=args.byte_end,
                shard_bytes=args.shard_bytes,
                max_shards=args.max_shards,
                source_token=args.source_token,
                resume_cursor=resume_cursor,
                record_processing_budget_bytes=(record_processing_budget_bytes),
            )
            if HOSTS[alias]["kind"] == "local"
            else _iter_remote_session_shard_frames(alias, payload)
        )
        for frame in frames:
            item = dict(frame)
            item["host"] = alias
            item["rollout"] = rollout_relative_path.as_posix()
            print(
                json.dumps(
                    item,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                flush=True,
            )
    except FileNotFoundError:
        print(f"host={alias}", file=sys.stderr)
        print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
        print("error=rollout not found", file=sys.stderr)
        return 1
    except OSError:
        print(f"host={alias}", file=sys.stderr)
        print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
        print("error=rollout unreadable", file=sys.stderr)
        return 1
    except (RuntimeError, ValueError) as error:
        print(f"host={alias}", file=sys.stderr)
        print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
        print(f"error={error}", file=sys.stderr)
        return 1
    return 0


def _normalize_summary_text(value: str, *, max_text_chars: int) -> str:
    collapsed = " ".join(str(value).replace("\r", "\n").split())
    if max_text_chars > 3 and len(collapsed) > max_text_chars:
        return collapsed[: max_text_chars - 3] + "..."
    return collapsed


def _message_summary(payload: dict[str, Any]) -> tuple[str, str]:
    role = str(payload.get("role", ""))
    if role not in {"assistant", "user"}:
        return "", ""
    parts: list[str] = []
    for item in payload.get("content", []):
        if not isinstance(item, dict):
            continue
        if item.get("type") not in {"input_text", "output_text", "text"}:
            continue
        text = item.get("text")
        if text:
            parts.append(str(text))
    kind = "user_message" if role == "user" else "assistant_message"
    return kind, "\n".join(parts).strip()


def _meaningful_prompt_text(text: str) -> str:
    stripped = str(text).strip()
    if not stripped:
        return ""
    if any(stripped.startswith(prefix) for prefix in WRAPPER_PREFIXES):
        for marker in WRAPPER_END_MARKERS:
            index = stripped.rfind(marker)
            if index >= 0:
                candidate = stripped[index + len(marker) :].strip()
                if candidate and not any(
                    candidate.startswith(prefix) for prefix in WRAPPER_PREFIXES
                ):
                    return candidate
        return ""
    return stripped


def _meaningful_user_message_text(text: str) -> str:
    stripped = _meaningful_prompt_text(text)
    if not stripped:
        return ""
    if any(pattern.search(stripped) for pattern in AUTOMATION_PROMPT_PATTERNS):
        return ""
    marker_count = sum(1 for marker in AUTOMATION_PROMPT_MARKERS if marker in stripped)
    if marker_count >= 2:
        return ""
    return stripped


def _summary_signal_text(kind: str, text: str) -> str:
    signals: list[str] = []
    if re.search(
        r"(?:exit(?:ed)?(?: with)? code [1-9]\d*|failed|traceback|error:|permission denied)",
        text,
        re.I,
    ):
        signals.append("error:")
    if re.search(
        r"(?:approval|require_escalated|sandbox|\bauth(?:entication|orization|[-_ ]?gated)?\b|credential|permission denied|TCC)",
        text,
        re.I,
    ):
        signals.append("approval")
    if re.search(
        r"(?:not run|did not run|unable to run|could not run|untested|未运行|无法运行)",
        text,
        re.I,
    ):
        signals.append("could not run")
    if re.search(
        r"(?:you missed|you forgot|wrong|incorrect|not what I asked|漏了|忘了|不对|错了)",
        text,
        re.I,
    ):
        signals.append("you missed")
    if re.search(
        r"(?:lost context|misunderstood|I misunderstood|assumption|assumed|上下文|误解)",
        text,
        re.I,
    ):
        signals.append("assumed")
    if (
        PRIVATE_IPV4_SIGNAL_RE.search(text)
        or PRIVATE_IPV6_SIGNAL_RE.search(text)
        or INTERNAL_HOSTNAME_SIGNAL_RE.search(text)
    ):
        signals.append("secret")
    elif re.search(
        r"(?:\b(secret|token|credential|password|private key|production|destructive|rm -rf|reset --hard|customer data|privacy|pii)\b|"
        r"\b(?:(?:sk|rk)[-_](?:proj[-_])?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,})\b|"
        r"\bAKIA[0-9A-Z]{16}\b|\bBearer\s+[A-Za-z0-9._~+/\-]+=*|"
        r"\b(?:authorization|password|passwd|pwd|credential|secret(?:[\s_-]?key)?|token|api[\s_-]?key|private[\s_-]?key)\s*[:=]\s*['\"]?[^'\"\s,;]+|"
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b|"
        r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])|"
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|https?://[^\s)>\]\"']+|"
        r"\b(?:ssh://[^\s)>\]\"']+|git@[A-Za-z0-9_.-]+:[^\s)>\]\"']+)|"
        r"(?<!\w)(?:~|/(?:Users|home|root|private|tmp|var|etc|opt|Volumes|workspace|workspaces))/[^\s,;:)>\]\"']+|"
        r"\b(?:customer|client|account|tenant|org|repo|repository)[_-]?(?:id|name)?\s*[:=]\s*['\"]?[A-Za-z0-9_.-]+|"
        r"客户|客户数据|凭据|凭证|密钥|生产|破坏性)",
        text,
        re.I,
    ):
        signals.append("secret")
    return " ".join(signals) if signals else f"{kind.replace('_', ' ')} present"


def _safe_summary_text(kind: str, text: str) -> str:
    return _summary_signal_text(kind, text)


def _summary_record_has_signal(record: dict[str, Any] | None) -> bool:
    if record is None or str(record.get("kind", "")) in {
        "session_meta",
        "scan_meta",
        "chunk_meta",
    }:
        return False
    text = str(record.get("text", ""))
    return any(marker in text for marker in SUMMARY_SIGNAL_MARKERS)


def _event_user_message_text(payload: dict[str, Any]) -> str:
    message = payload.get("message")
    if isinstance(message, str):
        return message.strip()
    if isinstance(message, dict):
        kind, text = _message_summary(message)
        if kind == "user_message" and text:
            return text.strip()
    text = payload.get("text")
    if isinstance(text, str):
        return text.strip()
    return ""


def _build_summary_record(
    *,
    kind: str,
    text: str,
    line_no: int,
    timestamp: str,
    max_text_chars: int,
    session_id: str = "",
) -> dict[str, Any] | None:
    signal_text = text
    if kind == "user_message":
        signal_text = _meaningful_user_message_text(text)
        if not signal_text:
            return None
    normalized = _normalize_summary_text(
        _safe_summary_text(kind, signal_text), max_text_chars=max_text_chars
    )
    if not normalized:
        return None
    record = {
        "kind": kind,
        "line": line_no,
        "text": normalized,
        "timestamp": timestamp,
    }
    if session_id:
        record["session_id"] = session_id
    match_text = _normalize_summary_text(signal_text, max_text_chars=max_text_chars)
    if match_text and match_text != normalized:
        record["_match_text"] = match_text
    return record


def _bounded_text_lines(handle: Any, max_scan_bytes: int) -> Iterable[str]:
    scanned = 0
    buffer = bytearray()
    dropping_oversized_line = False
    chunk_bytes = 64 * 1024

    def line_ended(part: bytes) -> bool:
        return part.endswith(b"\n") or part.endswith(b"\r")

    while True:
        if max_scan_bytes and scanned >= max_scan_bytes:
            if dropping_oversized_line:
                yield "\n"
            elif buffer:
                yield bytes(buffer).decode("utf-8", "replace")
            return
        remaining = max_scan_bytes - scanned if max_scan_bytes else 0
        read_size = min(chunk_bytes, remaining) if remaining else chunk_bytes
        chunk = handle.read(read_size)
        if not chunk:
            if dropping_oversized_line:
                yield "\n"
            elif buffer:
                yield bytes(buffer).decode("utf-8", "replace")
            return
        if isinstance(chunk, str):
            raw_bytes = chunk.encode("utf-8", "surrogatepass")
        else:
            raw_bytes = bytes(chunk)
        scanned += len(raw_bytes)
        for part in raw_bytes.splitlines(keepends=True):
            if dropping_oversized_line:
                if line_ended(part):
                    yield "\n"
                    dropping_oversized_line = False
                continue
            if len(buffer) + len(part) > MAX_ROLLOUT_SUMMARY_LINE_BYTES:
                buffer.clear()
                dropping_oversized_line = True
                if line_ended(part):
                    yield "\n"
                    dropping_oversized_line = False
                continue
            buffer.extend(part)
            if line_ended(part):
                yield bytes(buffer).decode("utf-8", "replace")
                buffer.clear()


def _summarize_rollout_records(
    *,
    lines: Iterable[str],
    keywords: list[str],
    limit: int,
    tail_records: int,
    max_text_chars: int,
    line_offset: int = 0,
) -> list[dict[str, Any]]:
    search_keywords = [value.casefold() for value in keywords if value]
    matched: list[dict[str, Any]] = []
    matched_seen: set[tuple[str, int]] = set()
    signal_records: list[dict[str, Any]] = []
    signal_seen: set[tuple[str, int]] = set()
    tail: collections.deque[dict[str, Any]] = collections.deque(maxlen=tail_records)
    session_meta_record: dict[str, Any] | None = None
    last_assistant_record: dict[str, Any] | None = None
    last_user_record: dict[str, Any] | None = None
    last_task_complete_record: dict[str, Any] | None = None

    for line_no, line in enumerate(lines, line_offset + 1):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        timestamp = str(obj.get("timestamp", ""))
        record: dict[str, Any] | None = None
        record_type = str(obj.get("type", ""))

        if record_type == "session_meta" and session_meta_record is None:
            payload = obj.get("payload", {})
            session_meta_record = _build_summary_record(
                kind="session_meta",
                text=f"session_id={payload.get('id', '')} cwd_present={str(bool(payload.get('cwd', ''))).lower()}",
                line_no=line_no,
                timestamp=timestamp,
                max_text_chars=max_text_chars,
                session_id=str(payload.get("id", "")),
            )
            continue

        if record_type == "response_item":
            payload = obj.get("payload", {})
            payload_type = str(payload.get("type", ""))
            if payload_type == "message":
                kind, text = _message_summary(payload)
                if text:
                    record = _build_summary_record(
                        kind=kind,
                        text=text,
                        line_no=line_no,
                        timestamp=timestamp,
                        max_text_chars=max_text_chars,
                    )
                    if kind == "assistant_message":
                        last_assistant_record = record
                    elif kind == "user_message" and record is not None:
                        last_user_record = record
            elif payload_type == "function_call_output":
                output = payload.get("output")
                if isinstance(output, str) and output.strip():
                    record = _build_summary_record(
                        kind="function_call_output",
                        text=output,
                        line_no=line_no,
                        timestamp=timestamp,
                        max_text_chars=max_text_chars,
                    )
        elif record_type == "event_msg":
            payload = obj.get("payload", {})
            payload_type = str(payload.get("type", ""))
            if payload_type == "task_complete":
                text = payload.get("last_agent_message")
                if text:
                    record = _build_summary_record(
                        kind="task_complete",
                        text=str(text),
                        line_no=line_no,
                        timestamp=timestamp,
                        max_text_chars=max_text_chars,
                    )
                    last_task_complete_record = record
            elif payload_type == "user_message":
                text = _event_user_message_text(payload)
                if text:
                    record = _build_summary_record(
                        kind="user_message",
                        text=text,
                        line_no=line_no,
                        timestamp=timestamp,
                        max_text_chars=max_text_chars,
                    )
                    if record is not None:
                        last_user_record = record

        if record is None:
            continue

        if _summary_record_has_signal(record):
            key = (str(record.get("kind", "")), int(record.get("line", 0)))
            if key not in signal_seen and (limit <= 0 or len(signal_records) < limit):
                signal_records.append(record)
                signal_seen.add(key)

        if search_keywords:
            text_value = str(
                record.get("_match_text") or record.get("text", "")
            ).casefold()
            if any(keyword in text_value for keyword in search_keywords):
                key = (str(record.get("kind", "")), int(record.get("line", 0)))
                if key not in matched_seen and (limit <= 0 or len(matched) < limit):
                    matched.append(record)
                    matched_seen.add(key)

        if tail_records > 0:
            tail.append(record)

    emitted: set[tuple[str, int]] = set()
    result: list[dict[str, Any]] = []

    def append(record: dict[str, Any] | None) -> None:
        if record is None:
            return
        key = (str(record.get("kind", "")), int(record.get("line", 0)))
        if key in emitted:
            return
        emitted.add(key)
        safe_record = dict(record)
        safe_record.pop("_match_text", None)
        result.append(safe_record)

    append(session_meta_record)
    for record in signal_records:
        append(record)
    for record in matched:
        append(record)
    if not search_keywords:
        for record in tail:
            append(record)
    append(last_user_record)
    append(last_assistant_record)
    if last_assistant_record is None:
        append(last_task_complete_record)
    return result


def _rollout_summary_scan_meta(*, source_bytes: int, scan_bytes: int) -> dict[str, Any]:
    scan_truncated = bool(scan_bytes and source_bytes > scan_bytes)
    return {
        "kind": "scan_meta",
        "line": 0,
        "scan_bytes": scan_bytes,
        "scan_truncated": scan_truncated,
        "source_bytes": source_bytes,
        "text": f"scan_truncated={str(scan_truncated).lower()} scan_bytes={scan_bytes} source_bytes={source_bytes}",
        "timestamp": "",
    }


def _chunk_common_fields(chunk: RolloutChunk) -> dict[str, Any]:
    return {
        "chunk_index": chunk.index,
        "byte_start": chunk.byte_start,
        "byte_end": chunk.byte_end,
        "record_start": chunk.record_start,
        "record_end": chunk.record_end,
        "first_timestamp": chunk.first_timestamp,
        "last_timestamp": chunk.last_timestamp,
        "record_count": len(chunk.lines),
    }


def _chunk_reason_codes(
    chunk: RolloutChunk,
    records: list[dict[str, Any]],
) -> list[str]:
    evidence_records = [
        record
        for record in records
        if str(record.get("kind", ""))
        not in {"session_meta", "scan_meta", "chunk_meta"}
    ]
    codes: list[str] = []
    if chunk.oversized_record:
        codes.append("oversized_record")
    if not evidence_records:
        codes.append("no_structured_evidence")
    if not any(record.get("kind") == "user_message" for record in evidence_records):
        codes.append("missing_meaningful_user_message")
    if not any(
        record.get("kind") in {"assistant_message", "task_complete"}
        for record in evidence_records
    ):
        codes.append("missing_final_summary")
    if any(_summary_record_has_signal(record) for record in evidence_records):
        codes.append("signal_or_redaction_present")
    return codes


def _chunk_meta_record(
    *,
    chunk: RolloutChunk,
    records: list[dict[str, Any]],
    source_bytes: int,
    chunk_bytes: int,
) -> dict[str, Any]:
    reason_codes = _chunk_reason_codes(chunk, records)
    redacted_or_signal_only_records = sum(
        1 for record in records if _summary_record_has_signal(record)
    )
    raw_fetch_recommended = (
        chunk.oversized_record
        or "no_structured_evidence" in reason_codes
        or redacted_or_signal_only_records > 0
    )
    meta = {
        "kind": "chunk_meta",
        "line": chunk.record_start,
        "source_bytes": source_bytes,
        "chunk_bytes": chunk_bytes,
        "coverage_status": "partial" if raw_fetch_recommended else "complete",
        "reason_codes": reason_codes,
        "records_emitted": len(records),
        "redacted_or_signal_only_records": redacted_or_signal_only_records,
        "raw_fetch_recommended": raw_fetch_recommended,
        "timestamp": chunk.first_timestamp,
    }
    meta.update(_chunk_common_fields(chunk))
    if raw_fetch_recommended:
        fetch_ranges = _fetch_ranges_for_byte_range(
            byte_start=chunk.byte_start,
            byte_end=chunk.byte_end,
            max_bytes=MAX_FETCH_ROLLOUT_CHUNK_BYTES,
        )
        meta["fetch_ranges"] = fetch_ranges
        meta["fetch_range_count"] = len(fetch_ranges)
        meta["fetch_chunk_bytes"] = MAX_FETCH_ROLLOUT_CHUNK_BYTES
    return meta


def _chunked_rollout_summary_records(
    *,
    codex_root: pathlib.Path,
    rollout_relative_path: pathlib.PurePosixPath,
    chunk_bytes: int,
    keywords: list[str],
    limit_per_chunk: int,
    tail_records: int,
    max_text_chars: int,
) -> list[dict[str, Any]]:
    target = _safe_rollout_path(codex_root, rollout_relative_path)
    source_bytes = target.stat().st_size
    output: list[dict[str, Any]] = []
    with _open_local_rollout_text(codex_root, rollout_relative_path) as handle:
        for chunk in _iter_rollout_chunks(handle, chunk_bytes=chunk_bytes):
            records = _summarize_rollout_records(
                lines=chunk.lines,
                keywords=keywords,
                limit=limit_per_chunk,
                tail_records=tail_records,
                max_text_chars=max_text_chars,
                line_offset=chunk.record_start - 1,
            )
            common = _chunk_common_fields(chunk)
            output.append(
                _chunk_meta_record(
                    chunk=chunk,
                    records=records,
                    source_bytes=source_bytes,
                    chunk_bytes=chunk_bytes,
                )
            )
            for record in records:
                item = dict(record)
                item.update(common)
                output.append(item)
    return output


def cmd_rollout_summary(args: argparse.Namespace) -> int:
    try:
        hosts = _resolve_hosts([args.host])
        alias = hosts[0]
        rollout_relative_path = _resolve_rollout_relative_path(args.rollout)
        if args.limit < 1 or args.limit > MAX_ROLLOUT_SUMMARY_LIMIT:
            raise ValueError(
                f"--limit must stay between 1 and {MAX_ROLLOUT_SUMMARY_LIMIT}"
            )
        if (
            args.tail_records < 0
            or args.tail_records > MAX_ROLLOUT_SUMMARY_TAIL_RECORDS
        ):
            raise ValueError(
                f"--tail-records must stay between 0 and {MAX_ROLLOUT_SUMMARY_TAIL_RECORDS}"
            )
        if args.max_text_chars < 40:
            raise ValueError("--max-text-chars must be at least 40")
        if args.max_text_chars > MAX_ROLLOUT_SUMMARY_TEXT_CHARS:
            raise ValueError(
                f"--max-text-chars must stay at or below {MAX_ROLLOUT_SUMMARY_TEXT_CHARS}"
            )
    except ValueError as error:
        return _error(str(error))

    try:
        if HOSTS[alias]["kind"] == "local":
            codex_root = _local_codex_root()
            source_bytes = (
                _safe_rollout_path(codex_root, rollout_relative_path).stat().st_size
            )
            with _open_local_rollout_text(codex_root, rollout_relative_path) as handle:
                records = _summarize_rollout_records(
                    lines=_bounded_text_lines(handle, MAX_ROLLOUT_SUMMARY_SCAN_BYTES),
                    keywords=args.keyword,
                    limit=args.limit,
                    tail_records=args.tail_records,
                    max_text_chars=args.max_text_chars,
                )
            records.insert(
                0,
                _rollout_summary_scan_meta(
                    source_bytes=source_bytes,
                    scan_bytes=MAX_ROLLOUT_SUMMARY_SCAN_BYTES,
                ),
            )
        else:
            payload = {
                "mode": "rollout-summary",
                "rollout": rollout_relative_path.as_posix(),
                "codex_root": HOSTS[alias]["codex_root"],
                "session_meta_scan_bytes": MAX_SESSION_META_SCAN_BYTES,
                "summary_keywords": list(args.keyword),
                "summary_limit": args.limit,
                "summary_scan_bytes": MAX_ROLLOUT_SUMMARY_SCAN_BYTES,
                "summary_line_bytes": MAX_ROLLOUT_SUMMARY_LINE_BYTES,
                "summary_tail_records": args.tail_records,
                "summary_max_text_chars": args.max_text_chars,
            }
            try:
                result = _run_remote_python(alias, payload)
            except RuntimeError as error:
                print(f"host={alias}", file=sys.stderr)
                print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
                print(f"error={error}", file=sys.stderr)
                return 1
            if result.returncode != 0:
                message = (
                    result.stderr.strip()
                    or result.stdout.strip()
                    or "remote rollout-summary failed"
                )
                print(f"host={alias}", file=sys.stderr)
                print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
                print(f"error={message}", file=sys.stderr)
                return 1
            try:
                records = _extract_framed_rollout_summary_records(
                    result.stdout,
                    begin_marker=REMOTE_ROLLOUT_SUMMARY_BEGIN,
                    end_marker=REMOTE_ROLLOUT_SUMMARY_END,
                    host=alias,
                    command="rollout-summary",
                )
            except FileNotFoundError:
                print(f"host={alias}", file=sys.stderr)
                print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
                print("error=rollout not found", file=sys.stderr)
                return 1
            except ValueError as error:
                print(f"host={alias}", file=sys.stderr)
                print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
                print(f"error={error}", file=sys.stderr)
                return 1
    except FileNotFoundError:
        print(f"host={alias}", file=sys.stderr)
        print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
        print("error=rollout not found", file=sys.stderr)
        return 1
    except OSError:
        print(f"host={alias}", file=sys.stderr)
        print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
        print("error=rollout unreadable", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"host={alias}", file=sys.stderr)
        print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
        print(f"error={error}", file=sys.stderr)
        return 1

    for record in records:
        item = dict(record)
        item["host"] = alias
        item["rollout"] = rollout_relative_path.as_posix()
        print(json.dumps(item, separators=(",", ":"), sort_keys=True))
    return 0


def cmd_chunked_rollout_summary(args: argparse.Namespace) -> int:
    try:
        hosts = _resolve_hosts([args.host])
        alias = hosts[0]
        rollout_relative_path = _resolve_rollout_relative_path(args.rollout)
        if args.chunk_bytes < 1 or args.chunk_bytes > MAX_ROLLOUT_CHUNK_BYTES:
            raise ValueError(
                f"--chunk-bytes must stay between 1 and {MAX_ROLLOUT_CHUNK_BYTES}"
            )
        if args.limit_per_chunk < 1 or args.limit_per_chunk > MAX_ROLLOUT_SUMMARY_LIMIT:
            raise ValueError(
                f"--limit-per-chunk must stay between 1 and {MAX_ROLLOUT_SUMMARY_LIMIT}"
            )
        if (
            args.tail_records < 0
            or args.tail_records > MAX_ROLLOUT_SUMMARY_TAIL_RECORDS
        ):
            raise ValueError(
                f"--tail-records must stay between 0 and {MAX_ROLLOUT_SUMMARY_TAIL_RECORDS}"
            )
        if args.max_text_chars < 40:
            raise ValueError("--max-text-chars must be at least 40")
        if args.max_text_chars > MAX_ROLLOUT_SUMMARY_TEXT_CHARS:
            raise ValueError(
                f"--max-text-chars must stay at or below {MAX_ROLLOUT_SUMMARY_TEXT_CHARS}"
            )
    except ValueError as error:
        return _error(str(error))

    try:
        if HOSTS[alias]["kind"] == "local":
            records = _chunked_rollout_summary_records(
                codex_root=_local_codex_root(),
                rollout_relative_path=rollout_relative_path,
                chunk_bytes=args.chunk_bytes,
                keywords=args.keyword,
                limit_per_chunk=args.limit_per_chunk,
                tail_records=args.tail_records,
                max_text_chars=args.max_text_chars,
            )
        else:
            payload = {
                "mode": "chunked-rollout-summary",
                "rollout": rollout_relative_path.as_posix(),
                "codex_root": HOSTS[alias]["codex_root"],
                "max_fetch_rollout_chunk_bytes": MAX_FETCH_ROLLOUT_CHUNK_BYTES,
                "summary_keywords": list(args.keyword),
                "summary_limit": args.limit_per_chunk,
                "summary_tail_records": args.tail_records,
                "summary_max_text_chars": args.max_text_chars,
                "chunk_bytes": args.chunk_bytes,
            }
            try:
                result = _run_remote_python(alias, payload)
            except RuntimeError as error:
                print(f"host={alias}", file=sys.stderr)
                print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
                print(f"error={error}", file=sys.stderr)
                return 1
            if result.returncode != 0:
                message = (
                    result.stderr.strip()
                    or result.stdout.strip()
                    or "remote chunked-rollout-summary failed"
                )
                print(f"host={alias}", file=sys.stderr)
                print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
                print(f"error={message}", file=sys.stderr)
                return 1
            try:
                records = _extract_framed_rollout_summary_records(
                    result.stdout,
                    begin_marker=REMOTE_CHUNKED_ROLLOUT_SUMMARY_BEGIN,
                    end_marker=REMOTE_CHUNKED_ROLLOUT_SUMMARY_END,
                    host=alias,
                    command="chunked-rollout-summary",
                )
            except FileNotFoundError:
                print(f"host={alias}", file=sys.stderr)
                print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
                print("error=rollout not found", file=sys.stderr)
                return 1
            except ValueError as error:
                print(f"host={alias}", file=sys.stderr)
                print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
                print(f"error={error}", file=sys.stderr)
                return 1
    except FileNotFoundError:
        print(f"host={alias}", file=sys.stderr)
        print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
        print("error=rollout not found", file=sys.stderr)
        return 1
    except OSError:
        print(f"host={alias}", file=sys.stderr)
        print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
        print("error=rollout unreadable", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"host={alias}", file=sys.stderr)
        print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
        print(f"error={error}", file=sys.stderr)
        return 1

    for record in records:
        item = dict(record)
        item["host"] = alias
        item["rollout"] = rollout_relative_path.as_posix()
        print(json.dumps(item, separators=(",", ":"), sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read bounded Codex session evidence from Joey's default hosts without ad hoc SSH literals."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser(
        "preflight",
        help="Check reachability and bounded prerequisites on the allowed hosts.",
    )
    preflight.add_argument("--host", action="append", required=True)
    preflight.set_defaults(func=cmd_preflight)

    session_meta = subparsers.add_parser(
        "session-meta",
        help="List session ids, cwd, and rollout paths from bounded date trees.",
    )
    session_meta.add_argument("--host", action="append", required=True)
    session_meta.add_argument("--date", action="append", default=[])
    session_meta.add_argument("--from", dest="from_date")
    session_meta.add_argument("--to", dest="to_date")
    session_meta.add_argument("--limit", type=int, default=200)
    session_meta.set_defaults(func=cmd_session_meta)

    fetch_rollout = subparsers.add_parser(
        "fetch-rollout",
        help="Copy one validated rollout file from an allowed host to a local path.",
    )
    fetch_rollout.add_argument("--host", required=True)
    fetch_rollout.add_argument(
        "--rollout",
        required=True,
        help="Relative rollout path under the remote Codex root (sessions/... or archived_sessions/...).",
    )
    fetch_rollout.add_argument(
        "--output",
        required=True,
        help="Output path must resolve under .codex-tmp/remote-host-context/ or /tmp.",
    )
    fetch_rollout.set_defaults(func=cmd_fetch_rollout)

    fetch_rollout_chunk = subparsers.add_parser(
        "fetch-rollout-chunk",
        help="Copy one bounded byte-range chunk from a validated rollout file.",
    )
    fetch_rollout_chunk.add_argument("--host", required=True)
    fetch_rollout_chunk.add_argument(
        "--rollout",
        required=True,
        help="Relative rollout path under the remote Codex root (sessions/... or archived_sessions/...).",
    )
    fetch_rollout_chunk.add_argument("--byte-start", type=int, required=True)
    fetch_rollout_chunk.add_argument("--byte-end", type=int, required=True)
    fetch_rollout_chunk.add_argument(
        "--output",
        required=True,
        help="Output path must resolve under .codex-tmp/remote-host-context/ or /tmp.",
    )
    fetch_rollout_chunk.set_defaults(func=cmd_fetch_rollout_chunk)

    session_shards = subparsers.add_parser(
        "session-shards",
        help="Stream bounded rollout shard descriptors or an exact record range.",
    )
    session_shards.add_argument("--host", required=True)
    session_shards.add_argument(
        "--rollout",
        help=(
            "Relative rollout path under the Codex root (sessions/..., "
            "archived_sessions/..., or root rollout-*.jsonl); required except "
            "for an explicit shadow holdout receipt."
        ),
    )
    session_shards.add_argument(
        "--emit",
        choices=("descriptors", "records", "holdout-receipt"),
        default="descriptors",
    )
    session_shards.add_argument("--byte-start", type=int, default=0)
    session_shards.add_argument("--byte-end", type=int)
    session_shards.add_argument(
        "--shard-bytes",
        type=int,
        default=DEFAULT_SESSION_SHARD_BYTES,
    )
    session_shards.add_argument(
        "--max-shards",
        type=int,
        default=DEFAULT_SESSION_SHARDS_PER_PAGE,
    )
    session_shards.add_argument(
        "--record-processing-budget-bytes",
        type=int,
        default=DEFAULT_SESSION_RECORD_PROCESSING_BUDGET_BYTES,
        help=(
            "Hard per-record source-byte ceiling for owner-only spooling and "
            "incremental JSON validation before emitting a content-free "
            "record_processing_budget_exceeded gap."
        ),
    )
    session_shards.add_argument("--source-token")
    session_shards.add_argument("--resume-cursor")
    session_shards.add_argument(
        "--qualification-mode",
        choices=("production", "shadow"),
        default="production",
        help=(
            "Qualification boundary for terminal holdout receipts; normal "
            "descriptor and record transport remains production-safe."
        ),
    )
    session_shards.add_argument(
        "--controlled-missing-host",
        action="store_true",
        help=(
            "Explicitly authorize one content-free missing remote host only "
            "with --qualification-mode shadow and --emit holdout-receipt."
        ),
    )
    session_shards.add_argument("--window-start")
    session_shards.add_argument("--window-end")
    session_shards.add_argument("--source-kind")
    session_shards.add_argument("--source-lease-ref")
    session_shards.add_argument("--shadow-identity-path")
    shadow_identity_mode = session_shards.add_mutually_exclusive_group()
    shadow_identity_mode.add_argument(
        "--create-shadow-identity",
        action="store_true",
        help="Create a new run-local mode-0700 shadow identity directory.",
    )
    shadow_identity_mode.add_argument(
        "--require-existing-shadow-identity",
        action="store_true",
        help="Fail closed unless the named owner-only shadow identity exists.",
    )
    session_shards.set_defaults(func=cmd_session_shards)

    rollout_summary = subparsers.add_parser(
        "rollout-summary",
        help="Read a bounded redacted prefix summary from one rollout without copying the full file.",
    )
    rollout_summary.add_argument("--host", required=True)
    rollout_summary.add_argument(
        "--rollout",
        required=True,
        help="Relative rollout path under the remote Codex root (sessions/... or archived_sessions/...).",
    )
    rollout_summary.add_argument("--keyword", action="append", default=[])
    rollout_summary.add_argument("--limit", type=int, default=40)
    rollout_summary.add_argument("--tail-records", type=int, default=8)
    rollout_summary.add_argument("--max-text-chars", type=int, default=400)
    rollout_summary.set_defaults(func=cmd_rollout_summary)

    chunked_rollout_summary = subparsers.add_parser(
        "chunked-rollout-summary",
        help="Read chunked structured summaries across a whole rollout without copying all raw text.",
    )
    chunked_rollout_summary.add_argument("--host", required=True)
    chunked_rollout_summary.add_argument(
        "--rollout",
        required=True,
        help="Relative rollout path under the remote Codex root (sessions/... or archived_sessions/...).",
    )
    chunked_rollout_summary.add_argument("--keyword", action="append", default=[])
    chunked_rollout_summary.add_argument(
        "--chunk-bytes", type=int, default=DEFAULT_ROLLOUT_CHUNK_BYTES
    )
    chunked_rollout_summary.add_argument("--limit-per-chunk", type=int, default=40)
    chunked_rollout_summary.add_argument("--tail-records", type=int, default=8)
    chunked_rollout_summary.add_argument("--max-text-chars", type=int, default=400)
    chunked_rollout_summary.set_defaults(func=cmd_chunked_rollout_summary)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
