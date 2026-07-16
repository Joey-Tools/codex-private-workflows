#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import binascii
import collections
import dataclasses
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import selectors
import shlex
import socket
import subprocess
import stat
import sys
import time
from collections.abc import Iterable
from typing import Any

DATE_FORMAT = "%Y/%m/%d"
MAX_SESSION_META_LIMIT = 500
MAX_SESSION_META_DATE_COUNT = 31
MAX_FETCH_ROLLOUT_BYTES = 16 * 1024 * 1024
MIN_ROLLOUT_CHUNK_BYTES = 64 * 1024
DEFAULT_ROLLOUT_CHUNK_BYTES = 1024 * 1024
MAX_ROLLOUT_CHUNK_BYTES = 2 * 1024 * 1024
MAX_FETCH_ROLLOUT_CHUNK_BYTES = 2 * 1024 * 1024
MAX_CHUNKED_ROLLOUT_SUMMARY_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_SOURCE_IDENTITY_TOKEN_CHARS = 256
MAX_REMOTE_METADATA_STDOUT_BYTES = 64 * 1024
MAX_REMOTE_STDERR_BYTES = 64 * 1024
REMOTE_CHUNKED_SUMMARY_FRAME_OVERHEAD_BYTES = 64 * 1024
MAX_ROLLOUT_SUMMARY_LIMIT = 200
MAX_ROLLOUT_SUMMARY_SCAN_BYTES = 2 * 1024 * 1024
MAX_ROLLOUT_SUMMARY_LINE_BYTES = 1024 * 1024
MAX_ROLLOUT_SUMMARY_TAIL_RECORDS = 50
MAX_ROLLOUT_SUMMARY_TEXT_CHARS = 1200
MAX_SESSION_META_SCAN_BYTES = 256 * 1024
REMOTE_PREFLIGHT_TIMEOUT_SECONDS = 15
REMOTE_COMMAND_TIMEOUT_SECONDS = 60
TASK_OUTPUT_RELATIVE_DIR = pathlib.Path(".codex-tmp/remote-host-context")
ACTIVE_ROLLOUT_RELATIVE_RE = re.compile(
    r"^sessions/\d{4}/\d{2}/\d{2}/rollout-[^/]+\.jsonl$"
)
ARCHIVED_ROLLOUT_RELATIVE_RE = re.compile(
    r"^archived_sessions/(?:\d{4}/\d{2}/\d{2}/)?rollout-[^/]+\.jsonl$"
)
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
WRAPPER_END_MARKERS = ("</INSTRUCTIONS>", "</environment_context>", "</skill>", "</subagent_notification>", "</turn_aborted>")
AUTOMATION_PROMPT_PATTERN_TEXTS = (
    r"^Run the (?:daily|weekly) Codex session retrospective\b",
    r"^Run a read-only (?:daily|weekly) retrospective over Joey's Codex session activity\b",
    r"^Run inside the dedicated worktree provisioned for this automation\b",
    r"^Use \$codex-session-retrospective to run\b",
    r"^Use the installed codex-session-retrospective workflow\b",
)
AUTOMATION_PROMPT_PATTERNS = tuple(re.compile(pattern, re.I) for pattern in AUTOMATION_PROMPT_PATTERN_TEXTS)
AUTOMATION_PROMPT_MARKERS = (
    "Run a read-only daily retrospective over Joey's Codex session activity.",
    "Run a read-only weekly retrospective over Joey's Codex session activity.",
    "Evidence scope must match $remote-host-context's default host policy",
    "Use the automation's configured model and reasoning effort",
    "When reconstructing the real user task from rollouts, ignore injected wrapper content",
    "Write task-local artifacts under .codex-local/session-retrospective/runs/",
)
SUMMARY_SIGNAL_MARKERS = ("error:", "approval", "could not run", "you missed", "assumed", "secret")
REMOTE_SESSION_META_BEGIN = "__REMOTE_CODEX_PROBE_SESSION_META_BEGIN__"
REMOTE_SESSION_META_END = "__REMOTE_CODEX_PROBE_SESSION_META_END__"
SESSION_META_LIMIT_TRUNCATED_REASON = "session_meta_limit_truncated"
REMOTE_FETCH_ROLLOUT_BEGIN = "__REMOTE_CODEX_PROBE_FETCH_ROLLOUT_BEGIN__"
REMOTE_FETCH_ROLLOUT_END = "__REMOTE_CODEX_PROBE_FETCH_ROLLOUT_END__"
REMOTE_FETCH_ROLLOUT_CHUNK_BEGIN = "__REMOTE_CODEX_PROBE_FETCH_ROLLOUT_CHUNK_BEGIN__"
REMOTE_FETCH_ROLLOUT_CHUNK_END = "__REMOTE_CODEX_PROBE_FETCH_ROLLOUT_CHUNK_END__"
REMOTE_ROLLOUT_STAT_BEGIN = "__REMOTE_CODEX_PROBE_ROLLOUT_STAT_BEGIN__"
REMOTE_ROLLOUT_STAT_END = "__REMOTE_CODEX_PROBE_ROLLOUT_STAT_END__"
REMOTE_ROLLOUT_SUMMARY_BEGIN = "__REMOTE_CODEX_PROBE_ROLLOUT_SUMMARY_BEGIN__"
REMOTE_ROLLOUT_SUMMARY_END = "__REMOTE_CODEX_PROBE_ROLLOUT_SUMMARY_END__"
REMOTE_CHUNKED_ROLLOUT_SUMMARY_BEGIN = "__REMOTE_CODEX_PROBE_CHUNKED_ROLLOUT_SUMMARY_BEGIN__"
REMOTE_CHUNKED_ROLLOUT_SUMMARY_END = "__REMOTE_CODEX_PROBE_CHUNKED_ROLLOUT_SUMMARY_END__"

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
class RolloutIdentity:
    size: int
    device: int
    inode: int
    mtime_ns: int
    ctime_ns: int


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
    root = workspace_root.resolve() if workspace_root is not None else pathlib.Path.cwd().resolve()
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
    workspace = workspace_root.resolve() if workspace_root is not None else pathlib.Path.cwd().resolve()
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
        if _path_is_relative_to(raw_path.resolve(strict=False), root.resolve(strict=False)):
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
    return _safe_relative_path(codex_root, rollout_relative_path, expect_regular_file=True)


def _safe_directory_path(
    codex_root: pathlib.Path, relative_path: pathlib.PurePosixPath
) -> pathlib.Path:
    return _safe_relative_path(codex_root, relative_path, expect_directory=True)


def _rollout_identity_from_stat(stat_result: os.stat_result) -> RolloutIdentity:
    if not stat.S_ISREG(stat_result.st_mode):
        raise ValueError("rollout path is not a regular file")
    return RolloutIdentity(
        size=stat_result.st_size,
        device=stat_result.st_dev,
        inode=stat_result.st_ino,
        mtime_ns=stat_result.st_mtime_ns,
        ctime_ns=stat_result.st_ctime_ns,
    )


def _rollout_identity_token(identity: RolloutIdentity) -> str:
    return (
        f"v1:{identity.size}:{identity.device}:{identity.inode}:"
        f"{identity.mtime_ns}:{identity.ctime_ns}"
    )


def _parse_rollout_identity_token(value: str) -> RolloutIdentity:
    if not value or len(value) > MAX_SOURCE_IDENTITY_TOKEN_CHARS:
        raise ValueError("invalid --expected-source-identity")
    parts = value.split(":")
    if len(parts) != 6 or parts[0] != "v1":
        raise ValueError("invalid --expected-source-identity")
    try:
        numbers = [int(part) for part in parts[1:]]
    except ValueError as error:
        raise ValueError("invalid --expected-source-identity") from error
    if any(number < 0 for number in numbers):
        raise ValueError("invalid --expected-source-identity")
    identity = RolloutIdentity(*numbers)
    if _rollout_identity_token(identity) != value:
        raise ValueError("invalid --expected-source-identity")
    return identity


def _parse_expected_rollout_identity(
    token: str,
    expected_source_bytes: int,
) -> RolloutIdentity:
    if expected_source_bytes < 0:
        raise ValueError("--expected-source-bytes must be non-negative")
    identity = _parse_rollout_identity_token(token)
    if identity.size != expected_source_bytes:
        raise ValueError(
            "--expected-source-bytes must match --expected-source-identity: "
            f"{expected_source_bytes} != {identity.size}"
        )
    return identity


def _expected_rollout_identity_from_args(
    args: argparse.Namespace,
    *,
    required: bool,
) -> RolloutIdentity | None:
    token = getattr(args, "expected_source_identity", None)
    expected_source_bytes = getattr(args, "expected_source_bytes", None)
    if token is None and expected_source_bytes is None:
        if required:
            raise ValueError(
                "--expected-source-identity and --expected-source-bytes are required"
            )
        return None
    if token is None or expected_source_bytes is None:
        raise ValueError(
            "--expected-source-identity and --expected-source-bytes must be provided together"
        )
    return _parse_expected_rollout_identity(token, expected_source_bytes)


def _assert_rollout_identity(
    actual: RolloutIdentity,
    expected: RolloutIdentity,
    *,
    phase: str,
) -> None:
    if actual != expected:
        raise ValueError(f"rollout identity changed {phase}")


def _rollout_path_identity(target: pathlib.Path) -> RolloutIdentity:
    return _rollout_identity_from_stat(target.lstat())


def _assert_rollout_path_identity(
    target: pathlib.Path,
    expected: RolloutIdentity,
    *,
    phase: str,
) -> None:
    try:
        actual = _rollout_path_identity(target)
    except (FileNotFoundError, ValueError) as error:
        raise ValueError(f"rollout identity changed {phase}") from error
    _assert_rollout_identity(actual, expected, phase=phase)


def _validate_source_read_budget(
    identity: RolloutIdentity,
    authorized_source_bytes: int | None,
) -> bool:
    if authorized_source_bytes is not None:
        if authorized_source_bytes < 0:
            raise ValueError("--authorized-source-bytes must be non-negative")
        if authorized_source_bytes != identity.size:
            raise ValueError(
                "--authorized-source-bytes must equal expected source size: "
                f"{authorized_source_bytes} != {identity.size}"
            )
    if identity.size > MAX_FETCH_ROLLOUT_BYTES:
        if authorized_source_bytes != identity.size:
            raise ValueError(
                "rollout exceeds automatic full-reconstruction limit: "
                f"{identity.size} bytes > {MAX_FETCH_ROLLOUT_BYTES}; exact "
                f"--authorized-source-bytes {identity.size} is required"
            )
        return True
    return authorized_source_bytes == identity.size


def _rollout_identity_record(identity: RolloutIdentity) -> dict[str, Any]:
    automatic_allowed = identity.size <= MAX_FETCH_ROLLOUT_BYTES
    return {
        "kind": "rollout_stat",
        "source_bytes": identity.size,
        "source_identity": _rollout_identity_token(identity),
        "source_dev": identity.device,
        "source_inode": identity.inode,
        "source_mtime_ns": identity.mtime_ns,
        "source_ctime_ns": identity.ctime_ns,
        "full_fetch_limit_bytes": MAX_FETCH_ROLLOUT_BYTES,
        "automatic_full_reconstruction_allowed": automatic_allowed,
        "full_reconstruction_allowed": automatic_allowed,
    }


def _rollout_identity_from_record(record: dict[str, Any]) -> RolloutIdentity:
    identity = _parse_rollout_identity_token(str(record.get("source_identity", "")))
    expected_fields = {
        "source_bytes": identity.size,
        "source_dev": identity.device,
        "source_inode": identity.inode,
        "source_mtime_ns": identity.mtime_ns,
        "source_ctime_ns": identity.ctime_ns,
    }
    for key, value in expected_fields.items():
        try:
            actual = int(record[key])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"rollout stat record has invalid {key}") from error
        if actual != value:
            raise ValueError(f"rollout stat record has mismatched {key}")
    return identity


def _stat_local_rollout_identity(
    codex_root: pathlib.Path,
    rollout_relative_path: pathlib.PurePosixPath,
) -> RolloutIdentity:
    target = _safe_rollout_path(codex_root, rollout_relative_path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(target), flags)
    try:
        identity = _rollout_identity_from_stat(os.fstat(fd))
        _assert_rollout_path_identity(target, identity, phase="during metadata stat")
        return identity
    finally:
        os.close(fd)


def _open_local_rollout_text(
    codex_root: pathlib.Path,
    rollout_relative_path: pathlib.PurePosixPath,
    *,
    expected_identity: RolloutIdentity | None = None,
):
    target = _safe_rollout_path(codex_root, rollout_relative_path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(target), flags)
    try:
        identity = _rollout_identity_from_stat(os.fstat(fd))
        if expected_identity is not None:
            _assert_rollout_identity(identity, expected_identity, phase="before read")
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
    expected_identity: RolloutIdentity,
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
        identity = _rollout_identity_from_stat(os.fstat(fd))
        _assert_rollout_identity(identity, expected_identity, phase="before read")
        if byte_end > identity.size:
            raise ValueError(f"--byte-end exceeds rollout size: {byte_end} > {identity.size}")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            handle.seek(byte_start)
            data = handle.read(length)
            _assert_rollout_identity(
                _rollout_identity_from_stat(os.fstat(handle.fileno())),
                expected_identity,
                phase="after read",
            )
            _assert_rollout_path_identity(
                target,
                expected_identity,
                phase="after read",
            )
            if len(data) != length:
                raise ValueError(
                    f"rollout chunk read was truncated: {len(data)} bytes != {length}"
                )
            return data
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
    raise FileExistsError(f"could not create private temporary output for {output}") from last_error


def _flat_archived_rollout_matches_date(
    rollout_path: pathlib.Path, date_value: dt.date
) -> bool:
    return rollout_path.name.startswith(f"rollout-{date_value.strftime('%Y-%m-%d')}")


def _is_raw_rollout_file(path: pathlib.Path) -> bool:
    return path.name.startswith("rollout-") and not path.name.startswith("rollout-summary")


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


class _HashingRolloutReader:
    def __init__(self, handle: Any) -> None:
        self.handle = handle
        self.bytes_read = 0
        self._hasher = hashlib.sha256()

    def readline(self, size: int = -1) -> Any:
        data = self.handle.readline(size)
        raw_bytes = data.encode("utf-8", "surrogatepass") if isinstance(data, str) else bytes(data)
        self._hasher.update(raw_bytes)
        self.bytes_read += len(raw_bytes)
        return data

    def fileno(self) -> int:
        return self.handle.fileno()

    def hexdigest(self) -> str:
        return self._hasher.hexdigest()


def _iter_rollout_chunks(
    handle: Any,
    *,
    chunk_bytes: int,
    source_bytes: int | None = None,
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
        remaining = None if source_bytes is None else source_bytes - offset
        if remaining is not None and remaining <= 0:
            chunk = flush()
            if chunk is not None:
                yield chunk
            return
        current_read_limit = read_limit if remaining is None else min(read_limit, remaining)
        raw_line = handle.readline(current_read_limit)
        if not raw_line:
            chunk = flush()
            if chunk is not None:
                yield chunk
            return
        raw_bytes, line = _raw_line_parts(raw_line)
        line_start = offset
        record_no += 1
        at_source_end = source_bytes is not None and (
            line_start + len(raw_bytes) >= source_bytes
        )
        line_truncated = (
            not at_source_end
            and len(raw_line) == current_read_limit
            and not _raw_line_endswith_newline(raw_line)
        )
        if len(raw_bytes) > chunk_bytes or line_truncated:
            if lines:
                chunk = flush()
                if chunk is not None:
                    yield chunk

            total_bytes = len(raw_bytes)
            while line_truncated:
                remaining = (
                    None
                    if source_bytes is None
                    else source_bytes - (line_start + total_bytes)
                )
                if remaining is not None and remaining <= 0:
                    break
                current_read_limit = (
                    read_limit if remaining is None else min(read_limit, remaining)
                )
                segment = handle.readline(current_read_limit)
                if not segment:
                    break
                segment_bytes, _ = _raw_line_parts(segment)
                total_bytes += len(segment_bytes)
                at_source_end = source_bytes is not None and (
                    line_start + total_bytes >= source_bytes
                )
                line_truncated = (
                    not at_source_end
                    and len(segment) == current_read_limit
                    and not _raw_line_endswith_newline(segment)
                )

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


def _parse_kv_lines(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key] = value
    return result


def _run_subprocess_text(
    argv: list[str], *, input_text: str | None = None, timeout_seconds: int | None = None
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
        raise RuntimeError(
            f"command timed out after {timeout_seconds}s"
        ) from exc


def _run_subprocess_text_bounded(
    argv: list[str],
    *,
    input_text: str | None = None,
    timeout_seconds: int,
    max_stdout_bytes: int,
    max_stderr_bytes: int,
) -> subprocess.CompletedProcess[str]:
    if max_stdout_bytes < 1 or max_stderr_bytes < 1:
        raise ValueError("bounded subprocess output limits must be positive")
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"command not found: {argv[0]}") from exc

    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    input_bytes = (input_text or "").encode("utf-8")
    input_offset = 0
    stdout = bytearray()
    stderr = bytearray()
    selector = selectors.DefaultSelector()
    for stream, events, label in (
        (process.stdout, selectors.EVENT_READ, "stdout"),
        (process.stderr, selectors.EVENT_READ, "stderr"),
    ):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, events, label)
    if input_bytes:
        os.set_blocking(process.stdin.fileno(), False)
        selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
    else:
        process.stdin.close()

    deadline = time.monotonic() + timeout_seconds
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"command timed out after {timeout_seconds}s")
            for key, _ in selector.select(min(0.25, remaining)):
                stream = key.fileobj
                if key.data == "stdin":
                    try:
                        written = os.write(
                            stream.fileno(), input_bytes[input_offset : input_offset + 65536]
                        )
                    except BrokenPipeError:
                        written = 0
                        input_offset = len(input_bytes)
                    else:
                        input_offset += written
                    if input_offset >= len(input_bytes):
                        selector.unregister(stream)
                        stream.close()
                    continue

                try:
                    chunk = os.read(stream.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                buffer = stdout if key.data == "stdout" else stderr
                limit = (
                    max_stdout_bytes if key.data == "stdout" else max_stderr_bytes
                )
                if len(buffer) + len(chunk) > limit:
                    raise RuntimeError(
                        f"command {key.data} exceeded {limit}-byte capture limit"
                    )
                buffer.extend(chunk)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(f"command timed out after {timeout_seconds}s")
        returncode = process.wait(timeout=remaining)
    except subprocess.TimeoutExpired as exc:
        if process.poll() is None:
            process.kill()
        process.wait()
        raise RuntimeError(f"command timed out after {timeout_seconds}s") from exc
    except Exception:
        if process.poll() is None:
            process.kill()
        process.wait()
        raise
    finally:
        selector.close()
        for stream in (process.stdin, process.stdout, process.stderr):
            if not stream.closed:
                stream.close()

    return subprocess.CompletedProcess(
        argv,
        returncode,
        stdout.decode("utf-8", "replace"),
        stderr.decode("utf-8", "replace"),
    )


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
        message = result.stderr.strip() or result.stdout.strip() or "ssh preflight failed"
        raise RuntimeError(message)
    row = _parse_kv_lines(result.stdout)
    row["host"] = alias
    return row


def _remote_python_script(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return f"""
import base64
import collections
import hashlib
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
MIN_ROLLOUT_CHUNK_BYTES = int(CONFIG.get("min_rollout_chunk_bytes", 0))
MAX_ROLLOUT_CHUNK_BYTES = int(CONFIG.get("max_rollout_chunk_bytes", 0))
MAX_CHUNKED_ROLLOUT_SUMMARY_OUTPUT_BYTES = int(CONFIG.get("max_chunked_summary_output_bytes", 0))
EXPECTED_SOURCE_IDENTITY_TOKEN = str(CONFIG.get("expected_source_identity", ""))
EXPECTED_SOURCE_BYTES = int(CONFIG.get("expected_source_bytes", -1))
AUTHORIZED_SOURCE_BYTES_RAW = CONFIG.get("authorized_source_bytes")
AUTHORIZED_SOURCE_BYTES = None if AUTHORIZED_SOURCE_BYTES_RAW is None else int(AUTHORIZED_SOURCE_BYTES_RAW)
OUTPUT_HOST = str(CONFIG.get("output_host", ""))
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
ROLLOUT_STAT_BEGIN = {REMOTE_ROLLOUT_STAT_BEGIN!r}
ROLLOUT_STAT_END = {REMOTE_ROLLOUT_STAT_END!r}
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


def rollout_identity_from_stat(stat_result):
    if not stat.S_ISREG(stat_result.st_mode):
        raise ValueError("rollout path is not a regular file")
    return {{
        "size": int(stat_result.st_size),
        "device": int(stat_result.st_dev),
        "inode": int(stat_result.st_ino),
        "mtime_ns": int(stat_result.st_mtime_ns),
        "ctime_ns": int(stat_result.st_ctime_ns),
    }}


def rollout_identity_token(identity):
    return "v1:" + ":".join(str(identity[key]) for key in ("size", "device", "inode", "mtime_ns", "ctime_ns"))


def parse_rollout_identity_token(value):
    if not value or len(value) > {MAX_SOURCE_IDENTITY_TOKEN_CHARS}:
        raise ValueError("invalid expected source identity")
    parts = value.split(":")
    if len(parts) != 6 or parts[0] != "v1":
        raise ValueError("invalid expected source identity")
    try:
        numbers = [int(part) for part in parts[1:]]
    except ValueError as error:
        raise ValueError("invalid expected source identity") from error
    if any(number < 0 for number in numbers):
        raise ValueError("invalid expected source identity")
    identity = dict(zip(("size", "device", "inode", "mtime_ns", "ctime_ns"), numbers))
    if rollout_identity_token(identity) != value:
        raise ValueError("invalid expected source identity")
    return identity


def parse_expected_rollout_identity():
    if EXPECTED_SOURCE_BYTES < 0:
        raise ValueError("expected source bytes must be non-negative")
    identity = parse_rollout_identity_token(EXPECTED_SOURCE_IDENTITY_TOKEN)
    if identity["size"] != EXPECTED_SOURCE_BYTES:
        raise ValueError("expected source bytes must match expected source identity")
    return identity


def assert_rollout_identity(actual, expected, phase):
    if actual != expected:
        raise ValueError("rollout identity changed " + phase)


def rollout_path_identity(target):
    return rollout_identity_from_stat(target.lstat())


def assert_rollout_path_identity(target, expected, phase):
    try:
        actual = rollout_path_identity(target)
    except (FileNotFoundError, ValueError) as error:
        raise ValueError("rollout identity changed " + phase) from error
    assert_rollout_identity(actual, expected, phase)


def validate_source_read_budget(identity):
    if AUTHORIZED_SOURCE_BYTES is not None:
        if AUTHORIZED_SOURCE_BYTES < 0:
            raise ValueError("authorized source bytes must be non-negative")
        if AUTHORIZED_SOURCE_BYTES != identity["size"]:
            raise ValueError("authorized source bytes must equal expected source size")
    if identity["size"] > MAX_FETCH_ROLLOUT_BYTES:
        if AUTHORIZED_SOURCE_BYTES != identity["size"]:
            raise ValueError(
                "rollout exceeds automatic full-reconstruction limit: "
                + str(identity["size"])
                + " bytes > "
                + str(MAX_FETCH_ROLLOUT_BYTES)
                + "; exact authorized source bytes required"
            )
        return True
    return AUTHORIZED_SOURCE_BYTES == identity["size"]


def rollout_identity_record(identity):
    automatic_allowed = identity["size"] <= MAX_FETCH_ROLLOUT_BYTES
    return {{
        "kind": "rollout_stat",
        "source_bytes": identity["size"],
        "source_identity": rollout_identity_token(identity),
        "source_dev": identity["device"],
        "source_inode": identity["inode"],
        "source_mtime_ns": identity["mtime_ns"],
        "source_ctime_ns": identity["ctime_ns"],
        "full_fetch_limit_bytes": MAX_FETCH_ROLLOUT_BYTES,
        "automatic_full_reconstruction_allowed": automatic_allowed,
        "full_reconstruction_allowed": automatic_allowed,
    }}


def stat_rollout_identity(target):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(target), flags)
    try:
        identity = rollout_identity_from_stat(os.fstat(fd))
        assert_rollout_path_identity(target, identity, "during metadata stat")
        return identity
    finally:
        os.close(fd)


def open_rollout_text(target, expected_identity=None):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(target), flags)
    try:
        identity = rollout_identity_from_stat(os.fstat(fd))
        if expected_identity is not None:
            assert_rollout_identity(identity, expected_identity, "before read")
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


def read_rollout_byte_range(target, byte_start, byte_end, max_bytes, expected_identity):
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
        identity = rollout_identity_from_stat(os.fstat(fd))
        assert_rollout_identity(identity, expected_identity, "before read")
        if byte_end > identity["size"]:
            raise ValueError("byte end exceeds rollout size: " + str(byte_end) + " > " + str(identity["size"]))
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            handle.seek(byte_start)
            data = handle.read(length)
            assert_rollout_identity(
                rollout_identity_from_stat(os.fstat(handle.fileno())),
                expected_identity,
                "after read",
            )
            assert_rollout_path_identity(target, expected_identity, "after read")
            if len(data) != length:
                raise ValueError("rollout chunk read was truncated")
            return data
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


class HashingRolloutReader:
    def __init__(self, handle):
        self.handle = handle
        self.bytes_read = 0
        self.hasher = hashlib.sha256()

    def readline(self, size=-1):
        data = self.handle.readline(size)
        raw_bytes = data.encode("utf-8", "surrogatepass") if isinstance(data, str) else bytes(data)
        self.hasher.update(raw_bytes)
        self.bytes_read += len(raw_bytes)
        return data

    def fileno(self):
        return self.handle.fileno()

    def hexdigest(self):
        return self.hasher.hexdigest()


def iter_rollout_chunks(handle, chunk_bytes, source_bytes=None):
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
        remaining = None if source_bytes is None else source_bytes - offset
        if remaining is not None and remaining <= 0:
            chunk = flush()
            if chunk is not None:
                yield chunk
            return
        current_read_limit = read_limit if remaining is None else min(read_limit, remaining)
        raw_line = handle.readline(current_read_limit)
        if not raw_line:
            chunk = flush()
            if chunk is not None:
                yield chunk
            return
        raw_bytes, line = raw_line_parts(raw_line)
        line_start = offset
        record_no += 1
        at_source_end = source_bytes is not None and line_start + len(raw_bytes) >= source_bytes
        line_truncated = (
            not at_source_end
            and len(raw_line) == current_read_limit
            and not raw_line_endswith_newline(raw_line)
        )
        if len(raw_bytes) > chunk_bytes or line_truncated:
            if lines:
                chunk = flush()
                if chunk is not None:
                    yield chunk

            total_bytes = len(raw_bytes)
            while line_truncated:
                remaining = None if source_bytes is None else source_bytes - (line_start + total_bytes)
                if remaining is not None and remaining <= 0:
                    break
                current_read_limit = read_limit if remaining is None else min(read_limit, remaining)
                segment = handle.readline(current_read_limit)
                if not segment:
                    break
                segment_bytes, _ = raw_line_parts(segment)
                total_bytes += len(segment_bytes)
                at_source_end = source_bytes is not None and line_start + total_bytes >= source_bytes
                line_truncated = (
                    not at_source_end
                    and len(segment) == current_read_limit
                    and not raw_line_endswith_newline(segment)
                )

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


def chunk_meta_record(chunk, records, source_identity, chunk_bytes):
    reason_codes = chunk_reason_codes(chunk, records)
    redacted_or_signal_only_records = sum(1 for record in records if summary_record_has_signal(record))
    raw_fetch_recommended = (
        bool(chunk["oversized_record"])
        or "no_structured_evidence" in reason_codes
        or redacted_or_signal_only_records > 0
    )
    automatic_allowed = source_identity["size"] <= MAX_FETCH_ROLLOUT_BYTES
    meta = {{
        "kind": "chunk_meta",
        "line": chunk["record_start"],
        "source_bytes": source_identity["size"],
        "source_identity": rollout_identity_token(source_identity),
        "full_fetch_limit_bytes": MAX_FETCH_ROLLOUT_BYTES,
        "automatic_full_reconstruction_allowed": automatic_allowed,
        "full_reconstruction_allowed": automatic_allowed or AUTHORIZED_SOURCE_BYTES == source_identity["size"],
        "authorized_source_bytes": AUTHORIZED_SOURCE_BYTES,
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


def serialized_summary_line(record, rel):
    payload = dict(record)
    payload["host"] = OUTPUT_HOST
    payload["rollout"] = rel.as_posix()
    line = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return line, len(line.encode("utf-8")) + 1


def rollout_summary_meta_record(source_identity, source_sha256):
    record = rollout_identity_record(source_identity)
    record.update({{
        "kind": "rollout_meta",
        "source_sha256": source_sha256,
        "authorized_source_bytes": AUTHORIZED_SOURCE_BYTES,
        "full_reconstruction_allowed": (
            source_identity["size"] <= MAX_FETCH_ROLLOUT_BYTES
            or AUTHORIZED_SOURCE_BYTES == source_identity["size"]
        ),
        "min_chunk_bytes": MIN_ROLLOUT_CHUNK_BYTES,
        "chunk_summary_output_limit_bytes": MAX_CHUNKED_ROLLOUT_SUMMARY_OUTPUT_BYTES,
    }})
    return record


def stat_rollout():
    rel = pathlib.PurePosixPath(str(CONFIG["rollout"]))
    normalized = rel.as_posix()
    print(ROLLOUT_STAT_BEGIN)
    if not (
        ACTIVE_ROLLOUT_RELATIVE_RE.fullmatch(normalized)
        or ARCHIVED_ROLLOUT_RELATIVE_RE.fullmatch(normalized)
    ):
        print(json.dumps({{"ok": False, "error": "invalid rollout path"}}, separators=(",", ":"), sort_keys=True))
        print(ROLLOUT_STAT_END)
        return
    try:
        target = safe_rollout_path(rel)
        identity = stat_rollout_identity(target)
        if EXPECTED_SOURCE_IDENTITY_TOKEN or EXPECTED_SOURCE_BYTES >= 0:
            if not EXPECTED_SOURCE_IDENTITY_TOKEN or EXPECTED_SOURCE_BYTES < 0:
                raise ValueError("expected source bytes and identity must be provided together")
            assert_rollout_identity(
                identity,
                parse_expected_rollout_identity(),
                "during final metadata verification",
            )
    except FileNotFoundError:
        print(json.dumps({{"ok": False, "error": "rollout not found"}}, separators=(",", ":"), sort_keys=True))
        print(ROLLOUT_STAT_END)
        return
    except OSError:
        print(json.dumps({{"ok": False, "error": "rollout unreadable"}}, separators=(",", ":"), sort_keys=True))
        print(ROLLOUT_STAT_END)
        return
    except ValueError as error:
        print(json.dumps({{"ok": False, "error": str(error)}}, separators=(",", ":"), sort_keys=True))
        print(ROLLOUT_STAT_END)
        return
    record = rollout_identity_record(identity)
    record["host"] = OUTPUT_HOST
    record["rollout"] = rel.as_posix()
    print(json.dumps({{"ok": True}}, separators=(",", ":"), sort_keys=True))
    print(json.dumps(record, separators=(",", ":"), sort_keys=True))
    print(ROLLOUT_STAT_END)


def summarize_rollout_chunks():
    rel = pathlib.PurePosixPath(str(CONFIG["rollout"]))
    normalized = rel.as_posix()
    print(CHUNKED_ROLLOUT_SUMMARY_BEGIN)
    if SUMMARY_MAX_TEXT_CHARS < 40 or SUMMARY_MAX_TEXT_CHARS > SUMMARY_MAX_TEXT_CHARS_LIMIT:
        print(json.dumps({{"ok": False, "error": "summary max text chars out of range"}}, separators=(",", ":"), sort_keys=True))
        print(CHUNKED_ROLLOUT_SUMMARY_END)
        return
    if CHUNK_BYTES < MIN_ROLLOUT_CHUNK_BYTES or CHUNK_BYTES > MAX_ROLLOUT_CHUNK_BYTES:
        print(json.dumps({{"ok": False, "error": "chunk bytes out of range"}}, separators=(",", ":"), sort_keys=True))
        print(CHUNKED_ROLLOUT_SUMMARY_END)
        return
    if MAX_CHUNKED_ROLLOUT_SUMMARY_OUTPUT_BYTES < 1:
        print(json.dumps({{"ok": False, "error": "chunked summary output limit out of range"}}, separators=(",", ":"), sort_keys=True))
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
        expected_identity = parse_expected_rollout_identity()
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
        serialized_records = []
        serialized_bytes = 0
        with open_rollout_text(target, expected_identity) as handle:
            validate_source_read_budget(expected_identity)
            hashing_reader = HashingRolloutReader(handle)
            chunks = iter_rollout_chunks(
                hashing_reader,
                CHUNK_BYTES,
                source_bytes=expected_identity["size"],
            )
            for chunk in chunks:
                records = summarize_records(chunk["lines"], line_offset=int(chunk["record_start"]) - 1)
                common = chunk_common_fields(chunk)
                line, line_bytes = serialized_summary_line(
                    chunk_meta_record(chunk, records, expected_identity, CHUNK_BYTES),
                    rel,
                )
                if serialized_bytes + line_bytes > MAX_CHUNKED_ROLLOUT_SUMMARY_OUTPUT_BYTES:
                    raise ValueError("chunked summary output too large")
                serialized_records.append(line)
                serialized_bytes += line_bytes
                for record in records:
                    payload = dict(record)
                    payload.update(common)
                    line, line_bytes = serialized_summary_line(payload, rel)
                    if serialized_bytes + line_bytes > MAX_CHUNKED_ROLLOUT_SUMMARY_OUTPUT_BYTES:
                        raise ValueError("chunked summary output too large")
                    serialized_records.append(line)
                    serialized_bytes += line_bytes
            assert_rollout_identity(
                rollout_identity_from_stat(os.fstat(handle.fileno())),
                expected_identity,
                "after summary scan",
            )
            assert_rollout_path_identity(target, expected_identity, "after summary scan")
            if hashing_reader.bytes_read != expected_identity["size"]:
                raise ValueError("chunked summary scan did not cover expected source bytes")
            source_sha256 = hashing_reader.hexdigest()
        meta_line, meta_bytes = serialized_summary_line(
            rollout_summary_meta_record(expected_identity, source_sha256),
            rel,
        )
        if meta_bytes + serialized_bytes > MAX_CHUNKED_ROLLOUT_SUMMARY_OUTPUT_BYTES:
            raise ValueError("chunked summary output too large")
    except OSError:
        print(json.dumps({{"ok": False, "error": "rollout unreadable"}}, separators=(",", ":"), sort_keys=True))
        print(CHUNKED_ROLLOUT_SUMMARY_END)
        return
    except ValueError as error:
        print(json.dumps({{"ok": False, "error": str(error)}}, separators=(",", ":"), sort_keys=True))
        print(CHUNKED_ROLLOUT_SUMMARY_END)
        return
    print(json.dumps({{
        "ok": True,
        "source_bytes": expected_identity["size"],
        "source_identity": rollout_identity_token(expected_identity),
        "source_sha256": source_sha256,
        "summary_output_bytes": meta_bytes + serialized_bytes,
    }}, separators=(",", ":"), sort_keys=True))
    print(meta_line)
    for line in serialized_records:
        print(line)
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
        expected_identity = parse_expected_rollout_identity()
        validate_source_read_budget(expected_identity)
        target = safe_rollout_path(rel)
        data = read_rollout_byte_range(
            target,
            FETCH_CHUNK_BYTE_START,
            FETCH_CHUNK_BYTE_END,
            MAX_FETCH_ROLLOUT_CHUNK_BYTES,
            expected_identity,
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
    print(json.dumps({{
        "ok": True,
        "bytes": len(data),
        "source_bytes": expected_identity["size"],
        "source_identity": rollout_identity_token(expected_identity),
    }}, separators=(",", ":"), sort_keys=True))
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
elif CONFIG["mode"] == "rollout-stat":
    stat_rollout()
elif CONFIG["mode"] == "rollout-summary":
    summarize_rollout()
elif CONFIG["mode"] == "chunked-rollout-summary":
    summarize_rollout_chunks()
else:
    raise SystemExit("unknown mode: " + str(CONFIG["mode"]))
""".lstrip()


def _remote_python_argv(alias: str) -> list[str]:
    ssh_target = HOSTS[alias]["ssh_target"]
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        ssh_target,
        "python3",
        "-",
    ]


def _run_remote_python(alias: str, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return _run_subprocess_text(
        _remote_python_argv(alias),
        input_text=_remote_python_script(payload),
        timeout_seconds=REMOTE_COMMAND_TIMEOUT_SECONDS,
    )


def _run_remote_python_bounded(
    alias: str,
    payload: dict[str, object],
    *,
    max_stdout_bytes: int,
) -> subprocess.CompletedProcess[str]:
    return _run_subprocess_text_bounded(
        _remote_python_argv(alias),
        input_text=_remote_python_script(payload),
        timeout_seconds=REMOTE_COMMAND_TIMEOUT_SECONDS,
        max_stdout_bytes=max_stdout_bytes,
        max_stderr_bytes=MAX_REMOTE_STDERR_BYTES,
    )


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
            flat_archived_dir = _safe_directory_path(resolved_root, pathlib.PurePosixPath("archived_sessions"))
            rollout_paths.extend(
                rollout_path
                for rollout_path in sorted_rollout_paths(flat_archived_dir)
                if _is_raw_rollout_file(rollout_path) and _flat_archived_rollout_matches_date(rollout_path, date_value)
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
            rollout_relative_key = _session_meta_rollout_dedupe_key(rollout_relative_path)
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


def _fetch_local_rollout(codex_root: pathlib.Path, rollout_relative_path: pathlib.PurePosixPath) -> bytes:
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
    expected_identity: RolloutIdentity,
) -> bytes:
    return _read_local_rollout_byte_range(
        codex_root,
        rollout_relative_path,
        byte_start=byte_start,
        byte_end=byte_end,
        max_bytes=MAX_FETCH_ROLLOUT_CHUNK_BYTES,
        expected_identity=expected_identity,
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
    expected_source_identity: str | None = None,
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
    if expected_source_identity is not None:
        if str(header.get("source_identity", "")) != expected_source_identity:
            raise ValueError(
                f"remote {command} output on host {host} had a mismatched source identity"
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
        raise ValueError(
            f"rollout too large: {len(data)} bytes > {max_bytes}"
        )
    return data


def _extract_framed_rollout_summary_records(
    text: str,
    *,
    begin_marker: str,
    end_marker: str,
    host: str,
    command: str,
    max_serialized_bytes: int | None = None,
    expected_source_identity: str | None = None,
    expected_source_bytes: int | None = None,
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

    record_lines = [line for line in payload_lines[1:] if line.strip()]
    serialized_bytes = sum(len(line.encode("utf-8")) + 1 for line in record_lines)
    if max_serialized_bytes is not None:
        if serialized_bytes > max_serialized_bytes:
            raise ValueError(
                f"remote {command} output on host {host} exceeded "
                f"{max_serialized_bytes} serialized bytes"
            )
        try:
            reported_bytes = int(header["summary_output_bytes"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"remote {command} output on host {host} had an invalid output size"
            ) from exc
        if reported_bytes != serialized_bytes:
            raise ValueError(
                f"remote {command} output on host {host} had a mismatched output size"
            )
    if expected_source_identity is not None:
        if str(header.get("source_identity", "")) != expected_source_identity:
            raise ValueError(
                f"remote {command} output on host {host} had a mismatched source identity"
            )
    if expected_source_bytes is not None:
        try:
            reported_source_bytes = int(header["source_bytes"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"remote {command} output on host {host} had an invalid source size"
            ) from exc
        if reported_source_bytes != expected_source_bytes:
            raise ValueError(
                f"remote {command} output on host {host} had a mismatched source size"
            )
    source_sha256 = ""
    if expected_source_identity is not None:
        source_sha256 = str(header.get("source_sha256", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
            raise ValueError(
                f"remote {command} output on host {host} had an invalid source digest"
            )

    records: list[dict[str, Any]] = []
    for line in record_lines:
        item = _json_line_to_dict(line, host=host)
        records.append(item)
    if expected_source_identity is not None:
        rollout_meta = [
            record for record in records if record.get("kind") == "rollout_meta"
        ]
        if len(rollout_meta) != 1:
            raise ValueError(
                f"remote {command} output on host {host} must contain one rollout_meta record"
            )
        meta = rollout_meta[0]
        if (
            str(meta.get("source_identity", "")) != expected_source_identity
            or int(meta.get("source_bytes", -1)) != expected_source_bytes
            or str(meta.get("source_sha256", "")) != source_sha256
        ):
            raise ValueError(
                f"remote {command} output on host {host} had mismatched rollout metadata"
            )
        for record in records:
            if record.get("kind") != "chunk_meta":
                continue
            if (
                str(record.get("source_identity", "")) != expected_source_identity
                or int(record.get("source_bytes", -1)) != expected_source_bytes
            ):
                raise ValueError(
                    f"remote {command} output on host {host} had mismatched chunk metadata"
                )
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


def _session_meta_error_from_item(item: dict[str, Any]) -> SessionMetaRolloutError | None:
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
                message = result.stderr.strip() or result.stdout.strip() or "remote fetch-rollout failed"
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


def cmd_rollout_stat(args: argparse.Namespace) -> int:
    try:
        hosts = _resolve_hosts([args.host])
        alias = hosts[0]
        rollout_relative_path = _resolve_rollout_relative_path(args.rollout)
        expected_identity = _expected_rollout_identity_from_args(args, required=False)
    except ValueError as error:
        return _error(str(error))

    try:
        if HOSTS[alias]["kind"] == "local":
            identity = _stat_local_rollout_identity(
                _local_codex_root(), rollout_relative_path
            )
            if expected_identity is not None:
                _assert_rollout_identity(
                    identity,
                    expected_identity,
                    phase="during final metadata verification",
                )
            record = _rollout_identity_record(identity)
            record["host"] = alias
            record["rollout"] = rollout_relative_path.as_posix()
        else:
            payload = {
                "mode": "rollout-stat",
                "rollout": rollout_relative_path.as_posix(),
                "codex_root": HOSTS[alias]["codex_root"],
                "max_fetch_rollout_bytes": MAX_FETCH_ROLLOUT_BYTES,
                "expected_source_identity": (
                    _rollout_identity_token(expected_identity)
                    if expected_identity is not None
                    else ""
                ),
                "expected_source_bytes": (
                    expected_identity.size if expected_identity is not None else -1
                ),
                "output_host": alias,
            }
            try:
                result = _run_remote_python_bounded(
                    alias,
                    payload,
                    max_stdout_bytes=MAX_REMOTE_METADATA_STDOUT_BYTES,
                )
            except RuntimeError as error:
                print(f"host={alias}", file=sys.stderr)
                print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
                print(f"error={error}", file=sys.stderr)
                return 1
            if result.returncode != 0:
                message = (
                    result.stderr.strip()
                    or result.stdout.strip()
                    or "remote rollout-stat failed"
                )
                print(f"host={alias}", file=sys.stderr)
                print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
                print(f"error={message}", file=sys.stderr)
                return 1
            records = _extract_framed_rollout_summary_records(
                result.stdout,
                begin_marker=REMOTE_ROLLOUT_STAT_BEGIN,
                end_marker=REMOTE_ROLLOUT_STAT_END,
                host=alias,
                command="rollout-stat",
            )
            if len(records) != 1 or records[0].get("kind") != "rollout_stat":
                raise ValueError(
                    f"remote rollout-stat output on host {alias} must contain one rollout_stat record"
                )
            record = records[0]
            identity = _rollout_identity_from_record(record)
            if expected_identity is not None:
                _assert_rollout_identity(
                    identity,
                    expected_identity,
                    phase="during final metadata verification",
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
    except ValueError as error:
        print(f"host={alias}", file=sys.stderr)
        print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
        print(f"error={error}", file=sys.stderr)
        return 1

    print(json.dumps(record, separators=(",", ":"), sort_keys=True))
    return 0


def cmd_fetch_rollout_chunk(args: argparse.Namespace) -> int:
    try:
        hosts = _resolve_hosts([args.host])
        alias = hosts[0]
        rollout_relative_path = _resolve_rollout_relative_path(args.rollout)
        output = _resolve_output_path(args.output)
        expected_identity = _expected_rollout_identity_from_args(args, required=True)
        assert expected_identity is not None
        authorized_source_bytes = getattr(args, "authorized_source_bytes", None)
        _validate_source_read_budget(expected_identity, authorized_source_bytes)
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
                expected_identity=expected_identity,
            )
        else:
            payload = {
                "mode": "fetch-rollout-chunk",
                "rollout": rollout_relative_path.as_posix(),
                "codex_root": HOSTS[alias]["codex_root"],
                "byte_start": args.byte_start,
                "byte_end": args.byte_end,
                "max_fetch_rollout_bytes": MAX_FETCH_ROLLOUT_BYTES,
                "max_fetch_rollout_chunk_bytes": MAX_FETCH_ROLLOUT_CHUNK_BYTES,
                "expected_source_identity": _rollout_identity_token(
                    expected_identity
                ),
                "expected_source_bytes": expected_identity.size,
                "authorized_source_bytes": authorized_source_bytes,
            }
            try:
                result = _run_remote_python(alias, payload)
            except RuntimeError as error:
                print(f"host={alias}", file=sys.stderr)
                print(f"rollout={rollout_relative_path.as_posix()}", file=sys.stderr)
                print(f"error={error}", file=sys.stderr)
                return 1
            if result.returncode != 0:
                message = result.stderr.strip() or result.stdout.strip() or "remote fetch-rollout-chunk failed"
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
                    expected_source_identity=_rollout_identity_token(
                        expected_identity
                    ),
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
    print(f"source_identity={_rollout_identity_token(expected_identity)}")
    print(f"source_bytes={expected_identity.size}")
    print(f"output={output}")
    print(f"bytes={len(data)}")
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
                if candidate and not any(candidate.startswith(prefix) for prefix in WRAPPER_PREFIXES):
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
    if re.search(r"(?:exit(?:ed)?(?: with)? code [1-9]\d*|failed|traceback|error:|permission denied)", text, re.I):
        signals.append("error:")
    if re.search(r"(?:approval|require_escalated|sandbox|\bauth(?:entication|orization|[-_ ]?gated)?\b|credential|permission denied|TCC)", text, re.I):
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
    if record is None or str(record.get("kind", "")) in {"session_meta", "scan_meta", "chunk_meta"}:
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
    normalized = _normalize_summary_text(_safe_summary_text(kind, signal_text), max_text_chars=max_text_chars)
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
            text_value = str(record.get("_match_text") or record.get("text", "")).casefold()
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
        if str(record.get("kind", "")) not in {"session_meta", "scan_meta", "chunk_meta"}
    ]
    codes: list[str] = []
    if chunk.oversized_record:
        codes.append("oversized_record")
    if not evidence_records:
        codes.append("no_structured_evidence")
    if not any(record.get("kind") == "user_message" for record in evidence_records):
        codes.append("missing_meaningful_user_message")
    if not any(record.get("kind") in {"assistant_message", "task_complete"} for record in evidence_records):
        codes.append("missing_final_summary")
    if any(_summary_record_has_signal(record) for record in evidence_records):
        codes.append("signal_or_redaction_present")
    return codes


def _chunk_meta_record(
    *,
    chunk: RolloutChunk,
    records: list[dict[str, Any]],
    source_identity: RolloutIdentity,
    chunk_bytes: int,
    authorized_source_bytes: int | None,
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
    automatic_allowed = source_identity.size <= MAX_FETCH_ROLLOUT_BYTES
    meta = {
        "kind": "chunk_meta",
        "line": chunk.record_start,
        "source_bytes": source_identity.size,
        "source_identity": _rollout_identity_token(source_identity),
        "full_fetch_limit_bytes": MAX_FETCH_ROLLOUT_BYTES,
        "automatic_full_reconstruction_allowed": automatic_allowed,
        "full_reconstruction_allowed": (
            automatic_allowed or authorized_source_bytes == source_identity.size
        ),
        "authorized_source_bytes": authorized_source_bytes,
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


def _append_bounded_summary_record(
    output: list[dict[str, Any]],
    record: dict[str, Any],
    serialized_bytes: int,
) -> int:
    encoded_bytes = len(
        json.dumps(record, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ) + 1
    updated_bytes = serialized_bytes + encoded_bytes
    if updated_bytes > MAX_CHUNKED_ROLLOUT_SUMMARY_OUTPUT_BYTES:
        raise ValueError(
            "chunked summary output too large: serialized JSONL exceeds "
            f"{MAX_CHUNKED_ROLLOUT_SUMMARY_OUTPUT_BYTES} bytes"
        )
    output.append(record)
    return updated_bytes


def _rollout_summary_meta_record(
    *,
    source_identity: RolloutIdentity,
    source_sha256: str,
    authorized_source_bytes: int | None,
) -> dict[str, Any]:
    record = _rollout_identity_record(source_identity)
    record.update(
        {
            "kind": "rollout_meta",
            "source_sha256": source_sha256,
            "authorized_source_bytes": authorized_source_bytes,
            "full_reconstruction_allowed": (
                source_identity.size <= MAX_FETCH_ROLLOUT_BYTES
                or authorized_source_bytes == source_identity.size
            ),
            "min_chunk_bytes": MIN_ROLLOUT_CHUNK_BYTES,
            "chunk_summary_output_limit_bytes": (
                MAX_CHUNKED_ROLLOUT_SUMMARY_OUTPUT_BYTES
            ),
        }
    )
    return record


def _chunked_rollout_summary_records(
    *,
    codex_root: pathlib.Path,
    rollout_relative_path: pathlib.PurePosixPath,
    chunk_bytes: int,
    keywords: list[str],
    limit_per_chunk: int,
    tail_records: int,
    max_text_chars: int,
    host: str,
    expected_identity: RolloutIdentity,
    authorized_source_bytes: int | None,
) -> list[dict[str, Any]]:
    if chunk_bytes < MIN_ROLLOUT_CHUNK_BYTES or chunk_bytes > MAX_ROLLOUT_CHUNK_BYTES:
        raise ValueError(
            f"--chunk-bytes must stay between {MIN_ROLLOUT_CHUNK_BYTES} "
            f"and {MAX_ROLLOUT_CHUNK_BYTES}"
        )
    target = _safe_rollout_path(codex_root, rollout_relative_path)
    _validate_source_read_budget(expected_identity, authorized_source_bytes)
    output: list[dict[str, Any]] = []
    serialized_bytes = 0
    with _open_local_rollout_text(
        codex_root,
        rollout_relative_path,
        expected_identity=expected_identity,
    ) as handle:
        hashing_reader = _HashingRolloutReader(handle)
        for chunk in _iter_rollout_chunks(
            hashing_reader,
            chunk_bytes=chunk_bytes,
            source_bytes=expected_identity.size,
        ):
            records = _summarize_rollout_records(
                lines=chunk.lines,
                keywords=keywords,
                limit=limit_per_chunk,
                tail_records=tail_records,
                max_text_chars=max_text_chars,
                line_offset=chunk.record_start - 1,
            )
            common = _chunk_common_fields(chunk)
            meta = _chunk_meta_record(
                chunk=chunk,
                records=records,
                source_identity=expected_identity,
                chunk_bytes=chunk_bytes,
                authorized_source_bytes=authorized_source_bytes,
            )
            meta["host"] = host
            meta["rollout"] = rollout_relative_path.as_posix()
            serialized_bytes = _append_bounded_summary_record(
                output,
                meta,
                serialized_bytes,
            )
            for record in records:
                item = dict(record)
                item.update(common)
                item["host"] = host
                item["rollout"] = rollout_relative_path.as_posix()
                serialized_bytes = _append_bounded_summary_record(
                    output,
                    item,
                    serialized_bytes,
                )
        _assert_rollout_identity(
            _rollout_identity_from_stat(os.fstat(handle.fileno())),
            expected_identity,
            phase="after summary scan",
        )
        _assert_rollout_path_identity(
            target,
            expected_identity,
            phase="after summary scan",
        )
        if hashing_reader.bytes_read != expected_identity.size:
            raise ValueError(
                "chunked summary scan did not cover expected source bytes: "
                f"{hashing_reader.bytes_read} != {expected_identity.size}"
            )
        rollout_meta = _rollout_summary_meta_record(
            source_identity=expected_identity,
            source_sha256=hashing_reader.hexdigest(),
            authorized_source_bytes=authorized_source_bytes,
        )
        rollout_meta["host"] = host
        rollout_meta["rollout"] = rollout_relative_path.as_posix()
        meta_output: list[dict[str, Any]] = []
        meta_bytes = _append_bounded_summary_record(meta_output, rollout_meta, 0)
        if meta_bytes + serialized_bytes > MAX_CHUNKED_ROLLOUT_SUMMARY_OUTPUT_BYTES:
            raise ValueError(
                "chunked summary output too large: serialized JSONL exceeds "
                f"{MAX_CHUNKED_ROLLOUT_SUMMARY_OUTPUT_BYTES} bytes"
            )
        output.insert(0, rollout_meta)
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
        if args.tail_records < 0 or args.tail_records > MAX_ROLLOUT_SUMMARY_TAIL_RECORDS:
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
            source_bytes = _safe_rollout_path(codex_root, rollout_relative_path).stat().st_size
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
        expected_identity = _expected_rollout_identity_from_args(args, required=True)
        assert expected_identity is not None
        authorized_source_bytes = getattr(args, "authorized_source_bytes", None)
        _validate_source_read_budget(expected_identity, authorized_source_bytes)
        if (
            args.chunk_bytes < MIN_ROLLOUT_CHUNK_BYTES
            or args.chunk_bytes > MAX_ROLLOUT_CHUNK_BYTES
        ):
            raise ValueError(
                f"--chunk-bytes must stay between {MIN_ROLLOUT_CHUNK_BYTES} "
                f"and {MAX_ROLLOUT_CHUNK_BYTES}"
            )
        if args.limit_per_chunk < 1 or args.limit_per_chunk > MAX_ROLLOUT_SUMMARY_LIMIT:
            raise ValueError(
                f"--limit-per-chunk must stay between 1 and {MAX_ROLLOUT_SUMMARY_LIMIT}"
            )
        if args.tail_records < 0 or args.tail_records > MAX_ROLLOUT_SUMMARY_TAIL_RECORDS:
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
                host=alias,
                expected_identity=expected_identity,
                authorized_source_bytes=authorized_source_bytes,
            )
        else:
            payload = {
                "mode": "chunked-rollout-summary",
                "rollout": rollout_relative_path.as_posix(),
                "codex_root": HOSTS[alias]["codex_root"],
                "max_fetch_rollout_bytes": MAX_FETCH_ROLLOUT_BYTES,
                "max_fetch_rollout_chunk_bytes": MAX_FETCH_ROLLOUT_CHUNK_BYTES,
                "min_rollout_chunk_bytes": MIN_ROLLOUT_CHUNK_BYTES,
                "max_rollout_chunk_bytes": MAX_ROLLOUT_CHUNK_BYTES,
                "max_chunked_summary_output_bytes": (
                    MAX_CHUNKED_ROLLOUT_SUMMARY_OUTPUT_BYTES
                ),
                "expected_source_identity": _rollout_identity_token(
                    expected_identity
                ),
                "expected_source_bytes": expected_identity.size,
                "authorized_source_bytes": authorized_source_bytes,
                "output_host": alias,
                "summary_keywords": list(args.keyword),
                "summary_limit": args.limit_per_chunk,
                "summary_tail_records": args.tail_records,
                "summary_max_text_chars": args.max_text_chars,
                "chunk_bytes": args.chunk_bytes,
            }
            try:
                result = _run_remote_python_bounded(
                    alias,
                    payload,
                    max_stdout_bytes=(
                        MAX_CHUNKED_ROLLOUT_SUMMARY_OUTPUT_BYTES
                        + REMOTE_CHUNKED_SUMMARY_FRAME_OVERHEAD_BYTES
                    ),
                )
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
                    max_serialized_bytes=MAX_CHUNKED_ROLLOUT_SUMMARY_OUTPUT_BYTES,
                    expected_source_identity=_rollout_identity_token(
                        expected_identity
                    ),
                    expected_source_bytes=expected_identity.size,
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

    rollout_stat = subparsers.add_parser(
        "rollout-stat",
        help="Return bounded rollout identity metadata before or after an identity-bound read.",
    )
    rollout_stat.add_argument("--host", required=True)
    rollout_stat.add_argument(
        "--rollout",
        required=True,
        help="Relative rollout path under the remote Codex root (sessions/... or archived_sessions/...).",
    )
    rollout_stat.add_argument("--expected-source-bytes", type=int)
    rollout_stat.add_argument("--expected-source-identity")
    rollout_stat.set_defaults(func=cmd_rollout_stat)

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
    fetch_rollout_chunk.add_argument("--expected-source-bytes", type=int, required=True)
    fetch_rollout_chunk.add_argument("--expected-source-identity", required=True)
    fetch_rollout_chunk.add_argument("--authorized-source-bytes", type=int)
    fetch_rollout_chunk.add_argument(
        "--output",
        required=True,
        help="Output path must resolve under .codex-tmp/remote-host-context/ or /tmp.",
    )
    fetch_rollout_chunk.set_defaults(func=cmd_fetch_rollout_chunk)

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
    chunked_rollout_summary.add_argument("--chunk-bytes", type=int, default=DEFAULT_ROLLOUT_CHUNK_BYTES)
    chunked_rollout_summary.add_argument(
        "--expected-source-bytes", type=int, required=True
    )
    chunked_rollout_summary.add_argument("--expected-source-identity", required=True)
    chunked_rollout_summary.add_argument("--authorized-source-bytes", type=int)
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
