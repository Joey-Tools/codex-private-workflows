#!/usr/bin/env python3

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import signal
import stat
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Any, BinaryIO


AUTOMATION_ID = "session-retrospective-v2-shadow"
CANONICAL_HOST_ORDER = ("local", "miku-bot-dev", "hoteng-srv-01")
CANONICAL_HOSTS = frozenset(CANONICAL_HOST_ORDER)
ALLOWED_COORDINATOR_COMMANDS = frozenset(
    (
        "help",
        "identity",
        "doctor",
        "start",
        "status",
        "accept-source",
        "accept-agent-result",
        "advance",
        "export",
        "finalize",
    )
)
ALLOWED_FINALIZE_PHASES = frozenset(
    {
        "prepare",
        "stage",
        "seal",
        "close-compliance",
        "promote",
        "commit",
        "status",
    }
)
CONFIGURATION_ROOT_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+/@=-]{0,1023}$")
UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
CLI_RESULT_SCHEMA = "cli_result_v2"
SOURCE_TRANSPORT_LEASE_SCHEMA = "source_transport_lease_v2"
SOURCE_TRANSPORT_LEASE_AUTH_RE = re.compile(
    r"^source_transport_lease_auth_v2:[0-9a-f]{64}$"
)
TRANSPORT_PROGRAM_COMMITMENT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
RUN_REF_RE = re.compile(r"^run_ref_v2:[0-9a-f]{64}$")
HOST_REF_RE = re.compile(r"^host_ref_v2:[0-9a-f]{64}$")
SOURCE_SNAPSHOT_REF_RE = re.compile(r"^source_snapshot_v2:[0-9a-f]{64}$")
SOURCE_RECEIPT_REF_RE = re.compile(r"^source_transport_receipt_v2:[0-9a-f]{64}$")
SOURCE_EVIDENCE_RE = re.compile(r"^shadow_source_evidence_v2:[0-9a-f]{64}$")
HOLDOUT_REF_RE = re.compile(r"^session_shards_holdout_v1:[0-9a-f]{64}$")
HOLDOUT_IDENTITY_KEY_ID_RE = re.compile(r"^session_shards_holdout_key_v1:[0-9a-f]{64}$")
COORDINATOR_IDENTITY_RE = re.compile(r"^identity_key_v2:[0-9a-f]{64}$")
COORDINATOR_COVERAGE_SCHEMA = "shadow_coverage_receipt_v2"
SOURCE_CAPTURE_IDLE_TIMEOUT_SECONDS = 90
SOURCE_CAPTURE_POLL_SECONDS = 0.05
SOURCE_CAPTURE_MIN_WALL_TIMEOUT_SECONDS = 2 * 60
SOURCE_CAPTURE_MAX_WALL_TIMEOUT_SECONDS = 15 * 60
SOURCE_CAPTURE_MIN_THROUGHPUT_BYTES_PER_SECOND = 512 * 1024
SOURCE_CAPTURE_WALL_TIMEOUT_OVERHEAD_SECONDS = 60
SOURCE_CAPTURE_PROGRESS_PATH_ENV = "CODEX_SESSION_SHARDS_CAPTURE_PROGRESS_PATH"
MAX_SOURCE_CAPTURE_PROGRESS_BYTES = 1024 * 1024
COORDINATOR_STATUS_TIMEOUT_SECONDS = 30
COORDINATOR_ACTION_TIMEOUT_SECONDS = 5 * 60
MAX_COORDINATOR_STATUS_BYTES = 1024 * 1024
MAX_COORDINATOR_DIAGNOSTIC_BYTES = 64 * 1024
MIN_SOURCE_CAPTURE_BYTES = 4 * 1024 * 1024
MAX_SOURCE_CAPTURE_BYTES = 512 * 1024 * 1024
MAX_SOURCE_CAPTURE_ARGUMENTS = 128
MAX_SOURCE_CAPTURE_ARGUMENT_BYTES = 64 * 1024
MAX_TRANSPORT_PROGRAM_BYTES = 8 * 1024 * 1024
_PROCESS_GROUP_SUPERVISOR = (
    "import subprocess, sys\nraise SystemExit(subprocess.Popen(sys.argv[1:]).wait())\n"
)
_SOURCE_CAPTURE_OPTION_ARITY = {
    "--byte-end": 1,
    "--byte-start": 1,
    "--controlled-missing-host": 0,
    "--create-shadow-identity": 0,
    "--emit": 1,
    "--host": 1,
    "--max-shards": 1,
    "--qualification-mode": 1,
    "--record-processing-budget-bytes": 1,
    "--require-existing-shadow-identity": 0,
    "--resume-cursor": 1,
    "--rollout": 1,
    "--shadow-identity-path": 1,
    "--shard-bytes": 1,
    "--source-kind": 1,
    "--source-lease-ref": 1,
    "--source-token": 1,
    "--window-end": 1,
    "--window-start": 1,
}

WORKSPACE_ROOT = pathlib.Path("/Users/hoteng/Program/GitHub/Joey-Tools/codex-workspace")
SHADOW_ROOT = WORKSPACE_ROOT / ".codex-local/session-retrospective-v2-shadow"
COORDINATOR_PATH = (
    pathlib.Path.home()
    / ".codex/skills/codex-session-retrospective/scripts/session_retrospective_v2.py"
)
TRANSPORT_PATH = pathlib.Path(__file__).with_name("remote_codex_probe.py")
SANDBOX_EXEC_PATH = pathlib.Path("/usr/bin/sandbox-exec")
DAILY_PAIR_SCHEMA = "shadow_daily_pair_v2"
DAILY_PAIR_FILENAME = "daily-pair.json"
DAILY_PAIR_HOLDOUT_MECHANISM = "authenticated_status_lease_holdout"
DAILY_PAIR_STATES = frozenset(
    {"partial_starting", "partial_started", "backfill_starting", "backfill_started"}
)
_DAILY_PAIR_FIELDS = frozenset(
    {
        "automation_id",
        "backfill_run_dir",
        "controlled_gap_receipt_ref",
        "coordinator_identity_path",
        "coordinator_path",
        "holdout_host",
        "holdout_identity_key_id",
        "holdout_mechanism",
        "partial_run_dir",
        "partial_run_ref",
        "production_source_suppressed",
        "run_config",
        "schema",
        "shadow",
        "state",
        "window_end",
        "window_start",
    }
)

_THREAD_LOCKS: dict[pathlib.Path, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class ShadowPolicyError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class _OptionSpec:
    arity: int
    required: bool = False
    repeatable: bool = False
    kinds: tuple[str, ...] = ()
    choices: frozenset[str] | None = None


@dataclasses.dataclass(frozen=True)
class _ParsedCoordinatorCommand:
    command: str
    options: Mapping[str, tuple[tuple[str, ...], ...]]
    phase: str | None = None

    def one(self, option: str) -> str:
        values = self.options.get(option, ())
        if len(values) != 1 or len(values[0]) != 1:
            raise ShadowPolicyError(f"{option} must occur exactly once")
        return values[0][0]


@dataclasses.dataclass(frozen=True)
class _ValidatedSourceTransportCommand:
    argv: tuple[str, ...]
    script_index: int
    commitment: str


def _flag(*, required: bool = False) -> _OptionSpec:
    return _OptionSpec(arity=0, required=required)


def _value(
    kind: str = "token",
    *,
    required: bool = False,
    repeatable: bool = False,
    choices: frozenset[str] | None = None,
) -> _OptionSpec:
    return _OptionSpec(
        arity=1,
        required=required,
        repeatable=repeatable,
        kinds=(kind,),
        choices=choices,
    )


_IDENTITY_OPTIONS = {
    "--identity-path": _value("path", required=True),
    "--require-existing-identity": _flag(required=True),
}
_COMMAND_OPTION_SCHEMAS: dict[str, dict[str, _OptionSpec]] = {
    "help": {},
    "identity": {
        "--create-identity": _flag(required=True),
        "--identity-path": _value("path", required=True),
        "--shadow": _flag(required=True),
    },
    "doctor": {
        **_IDENTITY_OPTIONS,
        "--history-repo": _value("path", required=True),
        "--history-target-ref": _value(required=True),
        "--run-config": _value("path", required=True),
        "--shadow": _flag(required=True),
    },
    "start": {
        **_IDENTITY_OPTIONS,
        "--allow-partial": _flag(),
        "--backfill-of": _value(),
        "--controlled-gap-receipt": _value("path"),
        "--end": _value("timestamp", required=True),
        "--history-repo": _value("path", required=True),
        "--history-target-ref": _value(required=True),
        "--host": _value(
            required=True,
            repeatable=True,
            choices=CANONICAL_HOSTS,
        ),
        "--mode": _value(
            required=True,
            choices=frozenset({"daily", "weekly"}),
        ),
        "--run-config": _value("path", required=True),
        "--run-dir": _value("path", required=True),
        "--session-target": _value(),
        "--session-target-selector": _value(),
        "--shadow": _flag(required=True),
        "--start": _value("timestamp", required=True),
    },
    "status": {
        **_IDENTITY_OPTIONS,
        "--claim-attempt-ref": _value(),
        "--claim-job-ref": _value(),
        "--claim-ref": _value(),
        "--claim-ttl-seconds": _value("positive-integer"),
        "--dispatcher-ref": _value(),
        "--run-dir": _value("path", required=True),
    },
    "accept-source": {
        **_IDENTITY_OPTIONS,
        "--lease-ref": _value(required=True),
        "--run-dir": _value("path", required=True),
        "--transport-stream": _OptionSpec(
            arity=2,
            repeatable=True,
            kinds=("token", "path"),
        ),
        "--transport-stream-file": _value("path", required=True),
    },
    "accept-agent-result": {
        **_IDENTITY_OPTIONS,
        "--attempt-ref": _value(required=True),
        "--claim-ref": _value(required=True),
        "--job-ref": _value(required=True),
        "--result": _value("path", required=True),
        "--result-ref": _value(required=True),
        "--run-dir": _value("path", required=True),
    },
    "advance": {
        **_IDENTITY_OPTIONS,
        "--holdout-host": _value(choices=CANONICAL_HOSTS - {"local"}),
        "--holdout-reason": _value(choices=frozenset({"shadow_missing_host_holdout"})),
        "--run-dir": _value("path", required=True),
    },
    "export": {
        **_IDENTITY_OPTIONS,
        "--output": _value("path", required=True),
        "--retention-deadline": _value("timestamp"),
        "--run-dir": _value("path", required=True),
    },
    "finalize": {
        **_IDENTITY_OPTIONS,
        "--history-repo": _value("path", required=True),
        "--history-target-ref": _value(required=True),
        "--phase": _value(
            required=True,
            choices=ALLOWED_FINALIZE_PHASES,
        ),
        "--run-dir": _value("path", required=True),
        "--shadow": _flag(required=True),
    },
}
SHADOW_HISTORY_TARGET_REF = "refs/heads/session-retrospective-v2-shadow-simulation"


def _path_is_relative_to(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _normalize_system_alias_path(path: pathlib.Path) -> pathlib.Path:
    for alias_root in (pathlib.Path("/tmp"), pathlib.Path("/var")):
        if _path_is_relative_to(path, alias_root):
            return alias_root.resolve() / path.relative_to(alias_root)
    return path


def _reject_symlink_components(path: pathlib.Path) -> None:
    current = pathlib.Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            return
        if stat.S_ISLNK(mode):
            raise ShadowPolicyError(f"shadow path uses a symlink component: {current}")


def _validate_owner_only_directory(path: pathlib.Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ShadowPolicyError(f"owner-only directory does not exist: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ShadowPolicyError(f"shadow path must be a real directory: {path}")
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise ShadowPolicyError(
            f"shadow directory must be current-user mode 0700: {path}"
        )


def _ensure_owner_only_directory(path: pathlib.Path) -> None:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    _validate_owner_only_directory(path)


def _prepare_invocation_directory(
    invocation_dir: pathlib.Path,
    *,
    shadow_root: pathlib.Path,
) -> tuple[pathlib.Path, pathlib.Path]:
    if not invocation_dir.is_absolute() or not shadow_root.is_absolute():
        raise ShadowPolicyError("shadow paths must be absolute")
    if any(part == ".." for part in (*invocation_dir.parts, *shadow_root.parts)):
        raise ShadowPolicyError("shadow paths must not contain ..")

    invocation_dir = _normalize_system_alias_path(invocation_dir)
    shadow_root = _normalize_system_alias_path(shadow_root)

    _reject_symlink_components(shadow_root.parent)
    if not shadow_root.parent.is_dir():
        raise ShadowPolicyError("shadow root parent must already exist")
    _ensure_owner_only_directory(shadow_root)
    resolved_root = shadow_root.resolve(strict=True)

    _reject_symlink_components(invocation_dir)
    resolved_invocation = invocation_dir.resolve(strict=False)
    if resolved_invocation == resolved_root or not _path_is_relative_to(
        resolved_invocation, resolved_root
    ):
        raise ShadowPolicyError(
            "invocation directory must be a child of the shadow artifact root"
        )

    current = resolved_root
    for part in resolved_invocation.relative_to(resolved_root).parts:
        current /= part
        _ensure_owner_only_directory(current)
    return resolved_invocation, resolved_root


def _validate_path_argument(
    option: str,
    raw_value: str,
    *,
    invocation_dir: pathlib.Path,
) -> None:
    path = pathlib.Path(raw_value).expanduser()
    if not path.is_absolute() or any(part == ".." for part in path.parts):
        raise ShadowPolicyError(f"{option} must use an absolute path without ..")
    path = _normalize_system_alias_path(path)
    _reject_symlink_components(path)
    resolved = path.resolve(strict=False)
    if not _path_is_relative_to(resolved, invocation_dir):
        raise ShadowPolicyError(f"{option} must stay inside the invocation directory")


def _validate_option_value(
    option: str,
    value: str,
    *,
    kind: str,
    invocation_dir: pathlib.Path,
) -> None:
    if not value or "\x00" in value or len(value.encode("utf-8")) > 4096:
        raise ShadowPolicyError(f"{option} contains a malformed value")
    if value.startswith("-"):
        raise ShadowPolicyError(f"{option} value must not inject another option")
    if kind == "path":
        _validate_path_argument(option, value, invocation_dir=invocation_dir)
    elif kind == "token":
        if SAFE_TOKEN_RE.fullmatch(value) is None:
            raise ShadowPolicyError(f"{option} requires a bounded protocol token")
    elif kind == "timestamp":
        if UTC_TIMESTAMP_RE.fullmatch(value) is None:
            raise ShadowPolicyError(f"{option} requires a canonical UTC timestamp")
        try:
            dt.datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
        except ValueError as exc:
            raise ShadowPolicyError(
                f"{option} requires a canonical UTC timestamp"
            ) from exc
    elif kind == "positive-integer":
        if not value.isascii() or not value.isdecimal() or int(value) < 1:
            raise ShadowPolicyError(f"{option} requires a positive decimal integer")
    elif kind != "bounded":
        raise ShadowPolicyError(f"{option} uses an unsupported value schema")


def _parse_coordinator_command(
    arguments: Sequence[str],
    *,
    host: str | None,
    invocation_dir: pathlib.Path,
    now_utc: dt.datetime | None = None,
) -> _ParsedCoordinatorCommand:
    if not arguments:
        raise ShadowPolicyError("a coordinator command is required")
    if any(not isinstance(value, str) or "\x00" in value for value in arguments):
        raise ShadowPolicyError("coordinator arguments must be NUL-free strings")
    command = arguments[0]
    if command not in ALLOWED_COORDINATOR_COMMANDS:
        raise ShadowPolicyError(f"coordinator command is not allowlisted: {command}")
    if host is not None and host not in CANONICAL_HOSTS:
        raise ShadowPolicyError(f"host is not allowlisted: {host}")
    if host is not None and command != "accept-source":
        raise ShadowPolicyError(
            "an outer host assertion is valid only for accept-source"
        )

    schema = _COMMAND_OPTION_SCHEMAS[command]
    parsed: dict[str, list[tuple[str, ...]]] = {}
    index = 1
    while index < len(arguments):
        option = arguments[index]
        if not option.startswith("--") or option == "--":
            raise ShadowPolicyError(
                f"coordinator positional or alias argument is forbidden: {option}"
            )
        if "=" in option:
            raise ShadowPolicyError(
                f"coordinator inline option values are forbidden: {option}"
            )
        spec = schema.get(option)
        if spec is None:
            raise ShadowPolicyError(
                f"coordinator option is not allowed for {command}: {option}"
            )
        if not spec.repeatable and option in parsed:
            raise ShadowPolicyError(f"duplicate singleton coordinator option: {option}")
        value_end = index + 1 + spec.arity
        if value_end > len(arguments):
            raise ShadowPolicyError(f"{option} requires {spec.arity} value(s)")
        values = tuple(arguments[index + 1 : value_end])
        for value_index, value in enumerate(values):
            _validate_option_value(
                option,
                value,
                kind=spec.kinds[value_index],
                invocation_dir=invocation_dir,
            )
        if spec.choices is not None and values and values[0] not in spec.choices:
            raise ShadowPolicyError(
                f"{option} value is not allowed for {command}: {values[0]}"
            )
        if spec.repeatable and values in parsed.get(option, []):
            raise ShadowPolicyError(f"duplicate repeatable coordinator value: {option}")
        parsed.setdefault(option, []).append(values)
        index = value_end

    missing = sorted(
        option
        for option, spec in schema.items()
        if spec.required and option not in parsed
    )
    if missing:
        raise ShadowPolicyError(
            f"coordinator command is missing required options: {', '.join(missing)}"
        )
    frozen = {option: tuple(values) for option, values in parsed.items()}
    phase = frozen["--phase"][0][0] if "--phase" in frozen else None
    result = _ParsedCoordinatorCommand(command=command, options=frozen, phase=phase)
    _validate_command_relationships(
        result,
        invocation_dir=invocation_dir,
        now_utc=now_utc,
    )
    return result


def _assert_simulation_history_empty(
    parsed: _ParsedCoordinatorCommand,
    *,
    invocation_dir: pathlib.Path,
) -> None:
    expected_history = (invocation_dir / "simulation-history").resolve(strict=False)
    if parsed.command in {"doctor", "start", "finalize"}:
        history_repo = pathlib.Path(parsed.one("--history-repo")).resolve(strict=False)
        if history_repo != expected_history:
            raise ShadowPolicyError(
                "--history-repo must be the invocation simulation-history directory"
            )
    elif not expected_history.exists():
        return
    _validate_owner_only_directory(expected_history)
    if any(expected_history.iterdir()):
        raise ShadowPolicyError("shadow simulation history must remain empty")


def _validate_command_relationships(
    parsed: _ParsedCoordinatorCommand,
    *,
    invocation_dir: pathlib.Path,
    now_utc: dt.datetime | None = None,
) -> None:
    if parsed.command == "identity":
        identity_path = pathlib.Path(parsed.one("--identity-path")).resolve(
            strict=False
        )
        _validate_owner_only_directory(identity_path.parent)
        if identity_path.exists():
            raise ShadowPolicyError("coordinator identity already exists")

    if parsed.command in {"doctor", "start", "finalize"}:
        _assert_simulation_history_empty(parsed, invocation_dir=invocation_dir)
        if parsed.one("--history-target-ref") != SHADOW_HISTORY_TARGET_REF:
            raise ShadowPolicyError("shadow history target ref must use the inert ref")

    if parsed.command == "start":
        mode = parsed.one("--mode")
        start = dt.datetime.fromisoformat(
            parsed.one("--start").removesuffix("Z") + "+00:00"
        )
        end = dt.datetime.fromisoformat(
            parsed.one("--end").removesuffix("Z") + "+00:00"
        )
        expected_duration = dt.timedelta(days=1 if mode == "daily" else 7)
        if end - start != expected_duration:
            raise ShadowPolicyError(f"{mode} shadow window has an invalid duration")
        if mode == "weekly":
            current = now_utc or dt.datetime.now(dt.timezone.utc)
            if current.tzinfo is None:
                raise ShadowPolicyError("weekly shadow clock must be timezone-aware")
            expected_end = current.astimezone(dt.timezone.utc).replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
            if (
                end != expected_end
                or start != expected_end - dt.timedelta(days=7)
                or start.timetz() != dt.time(0, tzinfo=dt.timezone.utc)
                or end.timetz() != dt.time(0, tzinfo=dt.timezone.utc)
            ):
                raise ShadowPolicyError(
                    "weekly shadow must use the latest closed UTC seven-day window"
                )
        hosts = tuple(value[0] for value in parsed.options["--host"])
        backfill = "--backfill-of" in parsed.options
        controlled_gap = "--controlled-gap-receipt" in parsed.options
        partial = "--allow-partial" in parsed.options
        if backfill != controlled_gap:
            raise ShadowPolicyError(
                "daily backfill requires both lineage and controlled-gap receipt"
            )
        if mode == "weekly" and (partial or backfill or set(hosts) != CANONICAL_HOSTS):
            raise ShadowPolicyError("weekly shadow requires all canonical hosts")
        if mode == "daily" and backfill:
            if partial or len(hosts) != 1 or hosts[0] == "local":
                raise ShadowPolicyError(
                    "daily backfill requires exactly one canonical remote host"
                )
            if RUN_REF_RE.fullmatch(parsed.one("--backfill-of")) is None:
                raise ShadowPolicyError("--backfill-of must be an exact v2 run ref")
        elif mode == "daily" and set(hosts) != CANONICAL_HOSTS:
            raise ShadowPolicyError(
                "daily complete or partial shadow requires all canonical hosts"
            )

    if parsed.command == "status":
        required_claim = {
            "--claim-attempt-ref",
            "--claim-job-ref",
            "--dispatcher-ref",
        }
        present = required_claim & parsed.options.keys()
        if present and present != required_claim:
            raise ShadowPolicyError("status claim options must be complete")
        if "--claim-ttl-seconds" in parsed.options and not present:
            raise ShadowPolicyError("claim TTL is valid only for a status claim")

    if parsed.command == "advance" and (
        ("--holdout-host" in parsed.options) != ("--holdout-reason" in parsed.options)
    ):
        raise ShadowPolicyError("advance holdout options must be paired")

    if parsed.command == "accept-source":
        source_refs = [
            values[0] for values in parsed.options.get("--transport-stream", ())
        ]
        if len(source_refs) != len(set(source_refs)):
            raise ShadowPolicyError("accept-source transport refs must be unique")


def validate_coordinator_command(
    arguments: Sequence[str],
    *,
    host: str | None,
    invocation_dir: pathlib.Path,
    now_utc: dt.datetime | None = None,
) -> tuple[str, str | None]:
    parsed = _parse_coordinator_command(
        arguments,
        host=host,
        invocation_dir=invocation_dir,
        now_utc=now_utc,
    )
    return parsed.command, parsed.phase


def _thread_lock(path: pathlib.Path) -> threading.Lock:
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(path, threading.Lock())


@contextlib.contextmanager
def _shadow_mutex(
    shadow_root: pathlib.Path,
    *,
    lock_name: str,
    label: str,
) -> Iterator[None]:
    if re.fullmatch(r"[a-z0-9-]+", lock_name) is None:
        raise ShadowPolicyError("shadow lock name is invalid")
    locks_dir = shadow_root / "locks"
    _ensure_owner_only_directory(locks_dir)
    lock_path = locks_dir / f"{lock_name}.lock"
    local_lock = _thread_lock(lock_path)
    with local_lock:
        flags = os.O_RDWR | os.O_CREAT
        flags |= int(getattr(os, "O_CLOEXEC", 0))
        flags |= int(getattr(os, "O_NOFOLLOW", 0))
        descriptor = os.open(lock_path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise ShadowPolicyError(
                    f"{label} lock must be a single-link owner-only file"
                )
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


@contextlib.contextmanager
def host_mutex(shadow_root: pathlib.Path, host: str) -> Iterator[None]:
    if host not in CANONICAL_HOSTS:
        raise ShadowPolicyError(f"host is not allowlisted: {host}")
    with _shadow_mutex(shadow_root, lock_name=host, label="per-host"):
        yield


@contextlib.contextmanager
def daily_pair_mutex(shadow_root: pathlib.Path) -> Iterator[None]:
    with _shadow_mutex(shadow_root, lock_name="daily-pair", label="Daily pair"):
        yield


def _sandbox_profile(
    invocation_dir: pathlib.Path,
    coordinator_path: pathlib.Path,
) -> str:
    python_path = pathlib.Path(sys.executable).resolve(strict=True)
    read_literals = {
        pathlib.Path("/dev/null"),
        pathlib.Path("/dev/random"),
        pathlib.Path("/dev/urandom"),
        coordinator_path,
        python_path,
        TRANSPORT_PATH.resolve(strict=True),
    }
    read_subpaths = {
        invocation_dir,
        pathlib.Path("/Library/Apple"),
        pathlib.Path("/System"),
        pathlib.Path("/private/etc"),
        pathlib.Path("/usr/lib"),
        pathlib.Path("/usr/share"),
    }
    coordinator_package = coordinator_path.parent / "retrospective_v2"
    if coordinator_package.is_dir():
        read_subpaths.add(coordinator_package.resolve(strict=True))
    for key in ("base", "platbase", "installed_base", "installed_platbase"):
        value = sysconfig.get_config_var(key)
        if isinstance(value, str) and value:
            candidate = pathlib.Path(value).resolve(strict=False)
            if candidate.is_dir():
                read_subpaths.add(candidate)

    lines = [
        "(version 1)",
        "(deny default)",
        "(deny network*)",
        "(allow process-fork)",
        "(allow process-info*)",
        "(allow signal (target self))",
        "(allow sysctl-read)",
        "(allow mach-lookup)",
        f"(allow process-exec (literal {json.dumps(str(python_path))}))",
    ]
    lines.extend(
        f"(allow file-read* (literal {json.dumps(str(path))}))"
        for path in sorted(read_literals)
    )
    lines.extend(
        f"(allow file-read* (subpath {json.dumps(str(path))}))"
        for path in sorted(read_subpaths)
    )
    lines.extend(
        (
            f"(allow file-write* (subpath {json.dumps(str(invocation_dir))}))",
            '(allow file-write* (literal "/dev/null"))',
        )
    )
    return "\n".join(lines)


def _source_capture_sandbox_profile(invocation_dir: pathlib.Path) -> str:
    python_path = pathlib.Path(sys.executable).resolve(strict=True)
    ssh_path = pathlib.Path("/usr/bin/ssh")
    lines = [
        "(version 1)",
        "(deny default)",
        "(allow file-read*)",
        "(allow network-outbound)",
        "(allow process-fork)",
        "(allow process-info*)",
        "(allow signal (target self))",
        "(allow sysctl-read)",
        "(allow mach-lookup)",
        f"(allow process-exec (literal {json.dumps(str(python_path))}))",
    ]
    if ssh_path.is_file():
        lines.append(f"(allow process-exec (literal {json.dumps(str(ssh_path))}))")
    lines.extend(
        (
            f"(allow file-write* (subpath {json.dumps(str(invocation_dir))}))",
            '(allow file-write* (literal "/dev/null"))',
        )
    )
    return "\n".join(lines)


def _validate_coordinator_path(path: pathlib.Path) -> pathlib.Path:
    if not path.is_absolute():
        raise ShadowPolicyError("coordinator path must be absolute")
    resolved = path.resolve(strict=True)
    metadata = resolved.stat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid not in {0, os.getuid()}
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise ShadowPolicyError(
            "coordinator path must be a trusted single-link regular file"
        )
    return resolved


def _sandbox_environment(invocation_dir: pathlib.Path) -> dict[str, str]:
    directories = {
        "HOME": invocation_dir / "home",
        "TMPDIR": invocation_dir / "tmp",
        "XDG_CACHE_HOME": invocation_dir / "cache",
        "XDG_CONFIG_HOME": invocation_dir / "config",
        "XDG_STATE_HOME": invocation_dir / "state",
    }
    for path in directories.values():
        _ensure_owner_only_directory(path)
    return {
        **{key: str(path) for key, path in directories.items()},
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }


def _source_capture_environment(invocation_dir: pathlib.Path) -> dict[str, str]:
    temporary_directory = invocation_dir / "tmp"
    _ensure_owner_only_directory(temporary_directory)
    environment = {
        "CODEX_SESSION_SHARDS_SHADOW_ROOT": str(invocation_dir),
        "HOME": str(pathlib.Path.home()),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "TMPDIR": str(temporary_directory),
    }
    for key in ("CODEX_REMOTE_ROOT", "LOGNAME", "SSH_AUTH_SOCK", "USER"):
        value = os.environ.get(key)
        if value:
            environment[key] = value
    return environment


def _process_group_exists(process: subprocess.Popen[Any]) -> bool:
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return process.poll() is None
    return True


def _signal_process_group(
    process: subprocess.Popen[Any],
    signal_number: signal.Signals,
) -> None:
    try:
        os.killpg(process.pid, signal_number)
    except ProcessLookupError:
        return
    except PermissionError:
        if process.poll() is None:
            try:
                process.send_signal(signal_number)
            except ProcessLookupError:
                pass


def _supervised_command(command: Sequence[str]) -> tuple[str, ...]:
    return (
        str(pathlib.Path(sys.executable).resolve(strict=True)),
        "-I",
        "-c",
        _PROCESS_GROUP_SUPERVISOR,
        *command,
    )


def _terminate_process_group(process: subprocess.Popen[Any]) -> None:
    if _process_group_exists(process):
        _signal_process_group(process, signal.SIGTERM)
    deadline = time.monotonic() + 2
    while _process_group_exists(process) and time.monotonic() < deadline:
        time.sleep(SOURCE_CAPTURE_POLL_SECONDS)
    if _process_group_exists(process):
        _signal_process_group(process, signal.SIGKILL)
    if process.poll() is None:
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _run_supervised_process(
    command: Sequence[str],
    *,
    cwd: pathlib.Path,
    environment: Mapping[str, str],
    capture_output: bool,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    stdout_file: BinaryIO | None = None
    stderr_file: BinaryIO | None = None
    process: subprocess.Popen[bytes] | None = None
    try:
        if capture_output:
            stdout_file = tempfile.NamedTemporaryFile(
                mode="w+b",
                prefix=".coordinator-stdout-",
                suffix=".tmp",
                dir=cwd,
            )
            stderr_file = tempfile.NamedTemporaryFile(
                mode="w+b",
                prefix=".coordinator-stderr-",
                suffix=".tmp",
                dir=cwd,
            )
        try:
            process = subprocess.Popen(
                _supervised_command(command),
                cwd=cwd,
                env=dict(environment),
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
        except OSError as exc:
            raise ShadowPolicyError("coordinator process could not start") from exc

        deadline = time.monotonic() + timeout_seconds
        while process.poll() is None:
            if capture_output:
                assert stdout_file is not None
                assert stderr_file is not None
                if (
                    os.fstat(stdout_file.fileno()).st_size
                    > MAX_COORDINATOR_STATUS_BYTES
                ):
                    raise ShadowPolicyError(
                        "coordinator status exceeded its output byte limit"
                    )
                if (
                    os.fstat(stderr_file.fileno()).st_size
                    > MAX_COORDINATOR_DIAGNOSTIC_BYTES
                ):
                    raise ShadowPolicyError(
                        "coordinator status exceeded its diagnostic byte limit"
                    )
            if time.monotonic() >= deadline:
                label = "status" if capture_output else "action"
                raise ShadowPolicyError(f"coordinator {label} timed out")
            time.sleep(SOURCE_CAPTURE_POLL_SECONDS)

        if _process_group_exists(process):
            raise ShadowPolicyError("coordinator retained a descendant process")
        stdout_text: str | None = None
        stderr_text: str | None = None
        if capture_output:
            assert stdout_file is not None
            assert stderr_file is not None
            if os.fstat(stdout_file.fileno()).st_size > MAX_COORDINATOR_STATUS_BYTES:
                raise ShadowPolicyError(
                    "coordinator status exceeded its output byte limit"
                )
            if (
                os.fstat(stderr_file.fileno()).st_size
                > MAX_COORDINATOR_DIAGNOSTIC_BYTES
            ):
                raise ShadowPolicyError(
                    "coordinator status exceeded its diagnostic byte limit"
                )
            stdout_file.seek(0)
            stderr_file.seek(0)
            try:
                stdout_text = stdout_file.read().decode("utf-8", errors="strict")
                stderr_text = stderr_file.read().decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise ShadowPolicyError(
                    "coordinator status output is not valid UTF-8"
                ) from exc
        return subprocess.CompletedProcess(
            tuple(command),
            int(process.returncode or 0),
            stdout_text,
            stderr_text,
        )
    finally:
        if process is not None:
            _terminate_process_group(process)
        if stdout_file is not None:
            stdout_file.close()
        if stderr_file is not None:
            stderr_file.close()


def _run_sandboxed(
    coordinator_path: pathlib.Path,
    arguments: Sequence[str],
    invocation_dir: pathlib.Path,
    *,
    capture_output: bool,
) -> subprocess.CompletedProcess[str]:
    if sys.platform != "darwin" or not SANDBOX_EXEC_PATH.is_file():
        raise ShadowPolicyError(
            "the supported macOS pre-execution read/write/network sandbox is required"
        )
    python_path = pathlib.Path(sys.executable).resolve(strict=True)
    command = [
        str(SANDBOX_EXEC_PATH),
        "-p",
        _sandbox_profile(invocation_dir, coordinator_path),
        str(python_path),
        str(coordinator_path),
        *arguments,
    ]
    return _run_supervised_process(
        command,
        cwd=invocation_dir,
        environment=_sandbox_environment(invocation_dir),
        capture_output=capture_output,
        timeout_seconds=(
            COORDINATOR_STATUS_TIMEOUT_SECONDS
            if capture_output
            else COORDINATOR_ACTION_TIMEOUT_SECONDS
        ),
    )


def _sandboxed_executor(
    coordinator_path: pathlib.Path,
    arguments: Sequence[str],
    invocation_dir: pathlib.Path,
) -> subprocess.CompletedProcess[str]:
    return _run_sandboxed(
        coordinator_path,
        arguments,
        invocation_dir,
        capture_output=False,
    )


Executor = Callable[
    [pathlib.Path, Sequence[str], pathlib.Path], subprocess.CompletedProcess[str]
]
CaptureExecutor = Callable[
    [Sequence[str], BinaryIO, pathlib.Path, int],
    subprocess.CompletedProcess[bytes],
]
StatusQuery = Callable[[pathlib.Path, Sequence[str], pathlib.Path], dict[str, Any]]
_SOURCE_ACTION_FIELDS = frozenset(
    {
        "category",
        "coordinator_cwd_contract",
        "host",
        "host_ref",
        "job_kind",
        "job_ref",
        "lease_ref",
        "native_coordinator_actions",
        "native_subagent_instruction",
        "source_contract",
        "source_kind",
        "source_transport_command",
        "source_transport_output",
        "stage",
        "status",
        "transport_contract",
        "transport_lease",
        "window",
    }
)
_TRANSPORT_LEASE_FIELDS = frozenset(
    {
        "authentication_tag",
        "command_argv",
        "cursor_time",
        "frame_byte_limit",
        "host",
        "host_ref",
        "job_ref",
        "lease_ref",
        "process_nonce",
        "record_limit",
        "run_ref",
        "schema",
        "session_selector_commitment",
        "session_target",
        "source_byte_limit",
        "source_cursor",
        "source_kind",
        "transport_program_commitment",
        "window",
    }
)


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ShadowPolicyError("coordinator status contains duplicate JSON keys")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ShadowPolicyError(
        f"coordinator status contains invalid JSON constant: {value}"
    )


def _parse_coordinator_status_output(
    process: subprocess.CompletedProcess[str],
) -> dict[str, Any]:
    if process.returncode != 0:
        raise ShadowPolicyError("authenticated coordinator status query failed")
    encoded = process.stdout.encode("utf-8")
    if not encoded or len(encoded) > MAX_COORDINATOR_STATUS_BYTES:
        raise ShadowPolicyError("coordinator status output is empty or too large")
    lines = [line for line in process.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ShadowPolicyError("coordinator status must emit exactly one JSON object")
    try:
        value = json.loads(
            lines[0],
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except (TypeError, json.JSONDecodeError) as exc:
        raise ShadowPolicyError("coordinator status is not strict JSON") from exc
    if not isinstance(value, dict):
        raise ShadowPolicyError("coordinator status must contain one JSON object")
    return value


def _sandboxed_status_query(
    coordinator_path: pathlib.Path,
    arguments: Sequence[str],
    invocation_dir: pathlib.Path,
) -> dict[str, Any]:
    process = _run_sandboxed(
        coordinator_path,
        arguments,
        invocation_dir,
        capture_output=True,
    )
    return _parse_coordinator_status_output(process)


def _status_arguments(parsed: _ParsedCoordinatorCommand) -> tuple[str, ...]:
    return (
        "status",
        "--identity-path",
        parsed.one("--identity-path"),
        "--require-existing-identity",
        "--run-dir",
        parsed.one("--run-dir"),
    )


def _guarded_status_query(
    *,
    coordinator_path: pathlib.Path,
    arguments: tuple[str, ...],
    invocation_dir: pathlib.Path,
    status_query: StatusQuery,
) -> dict[str, Any]:
    parsed = _parse_coordinator_command(
        arguments,
        host=None,
        invocation_dir=invocation_dir,
    )
    try:
        return status_query(coordinator_path, arguments, invocation_dir)
    finally:
        _assert_simulation_history_empty(parsed, invocation_dir=invocation_dir)


def _status_result(value: dict[str, Any]) -> dict[str, Any]:
    if set(value) != {"command", "error", "exit_code", "ok", "result", "schema"}:
        raise ShadowPolicyError("coordinator status result violates its closed schema")
    if (
        value.get("schema") != CLI_RESULT_SCHEMA
        or value.get("command") != "status"
        or value.get("ok") is not True
        or value.get("exit_code") != 0
        or value.get("error") is not None
        or not isinstance(value.get("result"), dict)
    ):
        raise ShadowPolicyError("coordinator status result is not successful")
    result = value["result"]
    if (
        result.get("schema_version") != 2
        or result.get("shadow") is not True
        or not isinstance(result.get("checkpoint_revision"), int)
        or isinstance(result.get("checkpoint_revision"), bool)
        or result.get("checkpoint_revision") < 1
        or not isinstance(result.get("run_ref"), str)
        or RUN_REF_RE.fullmatch(result["run_ref"]) is None
    ):
        raise ShadowPolicyError(
            "coordinator status is not an authenticated v2 shadow run"
        )
    return result


def _authenticated_source_action(
    status_value: dict[str, Any],
    parsed: _ParsedCoordinatorCommand,
    *,
    invocation_dir: pathlib.Path,
) -> dict[str, Any]:
    result = _status_result(status_value)
    actions = result.get("active_source_leases")
    if not isinstance(actions, list):
        raise ShadowPolicyError("coordinator status lacks active source actions")
    lease_ref = parsed.one("--lease-ref")
    matches = [
        item
        for item in actions
        if isinstance(item, dict) and item.get("lease_ref") == lease_ref
    ]
    if len(matches) != 1:
        raise ShadowPolicyError(
            "source lease is not the unique current runnable action"
        )
    action = matches[0]
    if set(action) != _SOURCE_ACTION_FIELDS:
        raise ShadowPolicyError("source action violates its closed status schema")
    lease = action.get("transport_lease")
    if not isinstance(lease, dict) or set(lease) != _TRANSPORT_LEASE_FIELDS:
        raise ShadowPolicyError(
            "source action transport lease violates its closed schema"
        )
    host = action.get("host")
    host_ref = action.get("host_ref")
    command_argv = lease.get("command_argv")
    if (
        action.get("category") != "source"
        or action.get("status") != "runnable"
        or action.get("transport_contract") != SOURCE_TRANSPORT_LEASE_SCHEMA
        or lease.get("schema") != SOURCE_TRANSPORT_LEASE_SCHEMA
        or lease.get("lease_ref") != lease_ref
        or lease.get("run_ref") != result.get("run_ref")
        or lease.get("host") != host
        or lease.get("host_ref") != host_ref
        or lease.get("source_kind") != action.get("source_kind")
        or lease.get("window") != action.get("window")
        or host not in CANONICAL_HOSTS
        or not isinstance(host_ref, str)
        or HOST_REF_RE.fullmatch(host_ref) is None
        or not isinstance(lease.get("authentication_tag"), str)
        or SOURCE_TRANSPORT_LEASE_AUTH_RE.fullmatch(lease["authentication_tag"]) is None
        or not isinstance(lease.get("transport_program_commitment"), str)
        or TRANSPORT_PROGRAM_COMMITMENT_RE.fullmatch(
            lease["transport_program_commitment"]
        )
        is None
        or not isinstance(command_argv, list)
        or command_argv != action.get("source_transport_command")
        or any(
            isinstance(lease.get(key), bool)
            or not isinstance(lease.get(key), int)
            or lease[key] < 1
            for key in ("frame_byte_limit", "record_limit", "source_byte_limit")
        )
    ):
        raise ShadowPolicyError("source action does not match its authenticated lease")

    native_actions = action.get("native_coordinator_actions")
    if not isinstance(native_actions, list) or len(native_actions) != 2:
        raise ShadowPolicyError("source action invocation tree is incomplete")
    capture_actions = [
        item
        for item in native_actions
        if isinstance(item, dict) and item.get("action") == "capture-source-transport"
    ]
    accept_actions = [
        item
        for item in native_actions
        if isinstance(item, dict) and item.get("action") == "accept-source"
    ]
    if (
        len(capture_actions) != 1
        or set(capture_actions[0]) != {"action", "command", "stdout_path"}
        or capture_actions[0].get("command") != command_argv
        or capture_actions[0].get("stdout_path")
        != action.get("source_transport_output")
        or len(accept_actions) != 1
        or set(accept_actions[0]) != {"action", "command"}
    ):
        raise ShadowPolicyError("source action accept-source invocation is invalid")
    native_command = accept_actions[0].get("command")
    if not isinstance(native_command, list) or "accept-source" not in native_command:
        raise ShadowPolicyError("source action accept-source command is invalid")
    command_index = native_command.index("accept-source")
    native_parsed = _parse_coordinator_command(
        native_command[command_index:],
        host=None,
        invocation_dir=invocation_dir,
    )
    if parsed.options != native_parsed.options:
        raise ShadowPolicyError(
            "requested accept-source argv differs from the authenticated action"
        )
    if (
        action.get("source_transport_output") != parsed.one("--transport-stream-file")
        or action.get("coordinator_cwd_contract") != "run_directory"
        or action.get("native_subagent_instruction")
        != (
            "Capture source_transport_command stdout at source_transport_output, "
            "then run the accept-source coordinator action."
        )
    ):
        raise ShadowPolicyError("source action transport output path does not match")
    return action


def _validated_source_transport_command(
    action: Mapping[str, Any],
    *,
    host: str,
    invocation_dir: pathlib.Path,
) -> _ValidatedSourceTransportCommand:
    raw_command = action.get("source_transport_command")
    if not isinstance(raw_command, list) or not raw_command:
        raise ShadowPolicyError("source transport command is missing")
    if len(raw_command) > MAX_SOURCE_CAPTURE_ARGUMENTS or any(
        not isinstance(value, str) or not value or "\x00" in value
        for value in raw_command
    ):
        raise ShadowPolicyError("source transport command contains malformed argv")
    if (
        sum(len(value.encode("utf-8")) for value in raw_command)
        > MAX_SOURCE_CAPTURE_ARGUMENT_BYTES
    ):
        raise ShadowPolicyError("source transport command argv is too large")

    python_path = pathlib.Path(sys.executable).resolve(strict=True)
    if pathlib.Path(raw_command[0]).resolve(strict=False) != python_path:
        raise ShadowPolicyError(
            "source transport command must use the runner Python executable"
        )
    if len(raw_command) <= 1 or raw_command[1] != "-I":
        raise ShadowPolicyError(
            "source transport command must use Python isolated mode"
        )
    script_index = 2
    if len(raw_command) <= script_index + 1:
        raise ShadowPolicyError("source transport command is incomplete")
    try:
        script_path = pathlib.Path(raw_command[script_index]).resolve(strict=True)
    except OSError as exc:
        raise ShadowPolicyError(
            "source transport command helper is unavailable"
        ) from exc
    if script_path != TRANSPORT_PATH.resolve(strict=True):
        raise ShadowPolicyError(
            "source transport command must use the installed remote-host helper"
        )
    arguments = raw_command[script_index + 1 :]
    if arguments[:1] != ["session-shards"]:
        raise ShadowPolicyError(
            "source transport command must use the session-shards primitive"
        )
    parsed_options: dict[str, str | None] = {}
    index = 1
    while index < len(arguments):
        option = arguments[index]
        if "=" in option or option not in _SOURCE_CAPTURE_OPTION_ARITY:
            raise ShadowPolicyError(
                f"source transport command option is not allowlisted: {option}"
            )
        if option in parsed_options:
            raise ShadowPolicyError(
                f"source transport command option is duplicated: {option}"
            )
        arity = _SOURCE_CAPTURE_OPTION_ARITY[option]
        if index + arity >= len(arguments):
            raise ShadowPolicyError(
                f"source transport command option is missing a value: {option}"
            )
        value = arguments[index + 1] if arity else None
        parsed_options[option] = value
        index += 1 + arity
    if parsed_options.get("--host") != host:
        raise ShadowPolicyError(
            "source transport command host does not match the authenticated lease"
        )
    identity_path = parsed_options.get("--shadow-identity-path")
    if identity_path is not None:
        _validate_path_argument(
            "--shadow-identity-path",
            identity_path,
            invocation_dir=invocation_dir,
        )
    lease = action.get("transport_lease")
    commitment = (
        lease.get("transport_program_commitment")
        if isinstance(lease, Mapping)
        else None
    )
    if (
        not isinstance(commitment, str)
        or TRANSPORT_PROGRAM_COMMITMENT_RE.fullmatch(commitment) is None
    ):
        raise ShadowPolicyError("source transport program commitment is invalid")
    return _ValidatedSourceTransportCommand(
        argv=tuple(raw_command),
        script_index=script_index,
        commitment=commitment,
    )


def _materialize_transport_program(
    validated: _ValidatedSourceTransportCommand,
    *,
    invocation_dir: pathlib.Path,
) -> tuple[tuple[str, ...], pathlib.Path]:
    source_path = TRANSPORT_PATH.resolve(strict=True)
    source_flags = os.O_RDONLY | int(getattr(os, "O_CLOEXEC", 0))
    source_flags |= int(getattr(os, "O_NOFOLLOW", 0))
    try:
        source_descriptor = os.open(source_path, source_flags)
    except OSError as exc:
        raise ShadowPolicyError("source transport program could not be opened") from exc
    destination_descriptor = -1
    destination_path: pathlib.Path | None = None
    try:
        source_metadata = os.fstat(source_descriptor)
        if (
            not stat.S_ISREG(source_metadata.st_mode)
            or source_metadata.st_uid not in {0, os.getuid()}
            or source_metadata.st_nlink != 1
            or stat.S_IMODE(source_metadata.st_mode) & 0o022
            or source_metadata.st_size < 1
            or source_metadata.st_size > MAX_TRANSPORT_PROGRAM_BYTES
        ):
            raise ShadowPolicyError("source transport program is not trusted")
        destination_descriptor, destination_name = tempfile.mkstemp(
            prefix=".remote-codex-probe-",
            suffix=".py",
            dir=invocation_dir,
        )
        destination_path = pathlib.Path(destination_name)
        os.fchmod(destination_descriptor, 0o600)
        digest = hashlib.sha256()
        copied = 0
        with (
            os.fdopen(source_descriptor, "rb") as source,
            os.fdopen(destination_descriptor, "wb") as destination,
        ):
            source_descriptor = -1
            destination_descriptor = -1
            while chunk := source.read(64 * 1024):
                copied += len(chunk)
                if copied > MAX_TRANSPORT_PROGRAM_BYTES:
                    raise ShadowPolicyError("source transport program is too large")
                digest.update(chunk)
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
            destination_metadata = os.fstat(destination.fileno())
            if (
                not stat.S_ISREG(destination_metadata.st_mode)
                or destination_metadata.st_uid != os.getuid()
                or destination_metadata.st_nlink != 1
                or stat.S_IMODE(destination_metadata.st_mode) != 0o600
                or destination_metadata.st_size != copied
            ):
                raise ShadowPolicyError(
                    "source transport program snapshot is not owner-only"
                )
        actual_commitment = f"sha256:{digest.hexdigest()}"
        if actual_commitment != validated.commitment:
            raise ShadowPolicyError(
                "source transport program does not match its authenticated commitment"
            )
        argv = list(validated.argv)
        argv[validated.script_index] = str(destination_path.resolve(strict=True))
        return tuple(argv), destination_path
    except Exception:
        if destination_path is not None:
            destination_path.unlink(missing_ok=True)
        raise
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if destination_descriptor >= 0:
            os.close(destination_descriptor)


def _source_capture_byte_limit(action: Mapping[str, Any]) -> int:
    lease = action.get("transport_lease")
    if not isinstance(lease, Mapping):
        raise ShadowPolicyError("source transport lease is missing")
    source_bytes = int(lease["source_byte_limit"])
    record_limit = int(lease["record_limit"])
    frame_bytes = int(lease["frame_byte_limit"])
    estimated = source_bytes * 2 + record_limit * frame_bytes + 64 * 1024
    return min(MAX_SOURCE_CAPTURE_BYTES, max(MIN_SOURCE_CAPTURE_BYTES, estimated))


def _source_capture_wall_timeout_seconds(max_output_bytes: int) -> float:
    transfer_seconds = (
        max_output_bytes + SOURCE_CAPTURE_MIN_THROUGHPUT_BYTES_PER_SECOND - 1
    ) // SOURCE_CAPTURE_MIN_THROUGHPUT_BYTES_PER_SECOND
    derived_timeout = transfer_seconds + SOURCE_CAPTURE_WALL_TIMEOUT_OVERHEAD_SECONDS
    return float(
        min(
            SOURCE_CAPTURE_MAX_WALL_TIMEOUT_SECONDS,
            max(SOURCE_CAPTURE_MIN_WALL_TIMEOUT_SECONDS, derived_timeout),
        )
    )


def _sandboxed_capture_executor(
    command: Sequence[str],
    output: BinaryIO,
    invocation_dir: pathlib.Path,
    max_output_bytes: int,
) -> subprocess.CompletedProcess[bytes]:
    if sys.platform != "darwin" or not SANDBOX_EXEC_PATH.is_file():
        raise ShadowPolicyError(
            "the supported macOS source-capture write sandbox is required"
        )
    sandboxed_command = [
        str(SANDBOX_EXEC_PATH),
        "-p",
        _source_capture_sandbox_profile(invocation_dir),
        *command,
    ]
    try:
        progress_descriptor, progress_name = tempfile.mkstemp(
            prefix=".session-shards-progress.",
            dir=invocation_dir,
        )
    except OSError as exc:
        raise ShadowPolicyError(
            "source transport capture progress file could not be created"
        ) from exc
    progress_path = pathlib.Path(progress_name)
    try:
        os.fchmod(progress_descriptor, 0o600)
        environment = _source_capture_environment(invocation_dir)
        environment[SOURCE_CAPTURE_PROGRESS_PATH_ENV] = str(progress_path)
        try:
            process = subprocess.Popen(
                _supervised_command(sandboxed_command),
                cwd=invocation_dir,
                env=environment,
                stdout=output,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            raise ShadowPolicyError("source transport capture could not start") from exc

        last_output_size = 0
        last_progress_size = 0
        idle_deadline = time.monotonic() + SOURCE_CAPTURE_IDLE_TIMEOUT_SECONDS
        wall_deadline = time.monotonic() + _source_capture_wall_timeout_seconds(
            max_output_bytes
        )
        try:
            while process.poll() is None:
                output_size = os.fstat(output.fileno()).st_size
                progress_size = os.fstat(progress_descriptor).st_size
                if output_size > max_output_bytes:
                    _terminate_process_group(process)
                    raise ShadowPolicyError(
                        "source transport capture exceeded its byte limit"
                    )
                if (
                    progress_size < last_progress_size
                    or progress_size > MAX_SOURCE_CAPTURE_PROGRESS_BYTES
                ):
                    _terminate_process_group(process)
                    raise ShadowPolicyError(
                        "source transport capture progress file is invalid"
                    )
                now = time.monotonic()
                if output_size > last_output_size or progress_size > last_progress_size:
                    last_output_size = output_size
                    last_progress_size = progress_size
                    idle_deadline = now + SOURCE_CAPTURE_IDLE_TIMEOUT_SECONDS
                if now >= wall_deadline:
                    _terminate_process_group(process)
                    raise ShadowPolicyError(
                        "source transport capture wall-clock timed out"
                    )
                if now >= idle_deadline:
                    _terminate_process_group(process)
                    raise ShadowPolicyError("source transport capture idle timed out")
                time.sleep(SOURCE_CAPTURE_POLL_SECONDS)
            if time.monotonic() >= wall_deadline:
                raise ShadowPolicyError("source transport capture wall-clock timed out")
            if os.fstat(output.fileno()).st_size > max_output_bytes:
                raise ShadowPolicyError(
                    "source transport capture exceeded its byte limit"
                )
            final_progress_size = os.fstat(progress_descriptor).st_size
            if (
                final_progress_size < last_progress_size
                or final_progress_size > MAX_SOURCE_CAPTURE_PROGRESS_BYTES
            ):
                raise ShadowPolicyError(
                    "source transport capture progress file is invalid"
                )
            if _process_group_exists(process):
                _terminate_process_group(process)
                raise ShadowPolicyError(
                    "source transport capture retained a descendant process"
                )
        finally:
            _terminate_process_group(process)
        return subprocess.CompletedProcess(
            tuple(command),
            int(process.returncode or 0),
        )
    finally:
        os.close(progress_descriptor)
        progress_path.unlink(missing_ok=True)


def _capture_source_transport(
    action: Mapping[str, Any],
    *,
    host: str,
    invocation_dir: pathlib.Path,
    capture_executor: CaptureExecutor,
) -> pathlib.Path:
    validated_command = _validated_source_transport_command(
        action,
        host=host,
        invocation_dir=invocation_dir,
    )
    output_path = pathlib.Path(str(action["source_transport_output"]))
    _validate_path_argument(
        "source_transport_output",
        str(output_path),
        invocation_dir=invocation_dir,
    )
    output_path = output_path.resolve(strict=False)
    _validate_owner_only_directory(output_path.parent)
    try:
        output_path.unlink(missing_ok=True)
    except OSError as exc:
        raise ShadowPolicyError("source transport output could not be reset") from exc

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.capture-",
        suffix=".tmp",
        dir=output_path.parent,
    )
    temporary_path = pathlib.Path(temporary_name)
    program_path: pathlib.Path | None = None
    published = False
    try:
        command, program_path = _materialize_transport_program(
            validated_command,
            invocation_dir=invocation_dir,
        )
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w+b") as output:
            descriptor = -1
            result = capture_executor(
                command,
                output,
                invocation_dir,
                _source_capture_byte_limit(action),
            )
            if result.returncode != 0:
                raise ShadowPolicyError("source transport capture failed")
            output.flush()
            os.fsync(output.fileno())
            metadata = os.fstat(output.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_size < 1
            ):
                raise ShadowPolicyError(
                    "source transport capture is not a nonempty owner-only file"
                )
        os.replace(temporary_path, output_path)
        published = True
        metadata = output_path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise ShadowPolicyError(
                "source transport output failed atomic publication validation"
            )
        directory_flags = os.O_RDONLY | int(getattr(os, "O_DIRECTORY", 0))
        directory_flags |= int(getattr(os, "O_CLOEXEC", 0))
        directory_flags |= int(getattr(os, "O_NOFOLLOW", 0))
        directory_descriptor = os.open(output_path.parent, directory_flags)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        return output_path
    except Exception:
        if published:
            output_path.unlink(missing_ok=True)
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)
        if program_path is not None:
            program_path.unlink(missing_ok=True)


def run_guarded_coordinator(
    arguments: Sequence[str],
    *,
    invocation_dir: pathlib.Path,
    host: str | None = None,
    shadow_root: pathlib.Path = SHADOW_ROOT,
    coordinator_path: pathlib.Path = COORDINATOR_PATH,
    executor: Executor = _sandboxed_executor,
    capture_executor: CaptureExecutor = _sandboxed_capture_executor,
    status_query: StatusQuery = _sandboxed_status_query,
) -> subprocess.CompletedProcess[str]:
    invocation_dir, shadow_root = _prepare_invocation_directory(
        invocation_dir,
        shadow_root=shadow_root,
    )
    parsed = _parse_coordinator_command(
        arguments,
        host=host,
        invocation_dir=invocation_dir,
    )
    coordinator_path = _validate_coordinator_path(coordinator_path)
    if parsed.command == "identity":
        try:
            result = executor(coordinator_path, tuple(arguments), invocation_dir)
        finally:
            _assert_simulation_history_empty(parsed, invocation_dir=invocation_dir)
        if result.returncode == 0:
            _read_private_json(
                pathlib.Path(parsed.one("--identity-path")),
                root=invocation_dir,
            )
        return result
    if parsed.command != "accept-source":
        try:
            return executor(coordinator_path, tuple(arguments), invocation_dir)
        finally:
            _assert_simulation_history_empty(parsed, invocation_dir=invocation_dir)

    status_arguments = _status_arguments(parsed)
    first_action = _authenticated_source_action(
        _guarded_status_query(
            coordinator_path=coordinator_path,
            arguments=status_arguments,
            invocation_dir=invocation_dir,
            status_query=status_query,
        ),
        parsed,
        invocation_dir=invocation_dir,
    )
    actual_host = str(first_action["host"])
    with host_mutex(shadow_root, actual_host):
        current_action = _authenticated_source_action(
            _guarded_status_query(
                coordinator_path=coordinator_path,
                arguments=status_arguments,
                invocation_dir=invocation_dir,
                status_query=status_query,
            ),
            parsed,
            invocation_dir=invocation_dir,
        )
        if current_action != first_action:
            raise ShadowPolicyError(
                "source action changed while acquiring its host lock"
            )
        if host is not None and host != actual_host:
            raise ShadowPolicyError(
                "caller host does not match the authenticated source action host"
            )
        output_path = _capture_source_transport(
            current_action,
            host=actual_host,
            invocation_dir=invocation_dir,
            capture_executor=capture_executor,
        )
        try:
            return executor(coordinator_path, tuple(arguments), invocation_dir)
        finally:
            output_path.unlink(missing_ok=True)
            _assert_simulation_history_empty(parsed, invocation_dir=invocation_dir)


def _load_transport_module() -> Any:
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(
        "remote_codex_probe_shadow_transaction",
        TRANSPORT_PATH,
    )
    if spec is None or spec.loader is None:
        raise ShadowPolicyError("could not load the installed session-shards transport")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _read_private_json(path: pathlib.Path, *, root: pathlib.Path) -> dict[str, Any]:
    if not path.is_absolute():
        raise ShadowPolicyError("receipt path must be absolute")
    path = _normalize_system_alias_path(path)
    _reject_symlink_components(path)
    resolved = path.resolve(strict=True)
    if not _path_is_relative_to(resolved, root):
        raise ShadowPolicyError(
            "receipt path must stay inside the invocation directory"
        )
    metadata = resolved.stat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size > 32 * 1024
    ):
        raise ShadowPolicyError("receipt must be a bounded owner-only regular file")
    value = json.loads(
        resolved.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_json_keys,
        parse_constant=_reject_json_constant,
    )
    if not isinstance(value, dict):
        raise ShadowPolicyError("receipt must contain one JSON object")
    return value


_PRIVATE_JSON_MISSING = object()


def _write_private_json(
    path: pathlib.Path,
    value: Mapping[str, Any],
    *,
    root: pathlib.Path,
    expected_current: object = _PRIVATE_JSON_MISSING,
) -> None:
    if not path.is_absolute() or any(part == ".." for part in path.parts):
        raise ShadowPolicyError("private JSON path must be absolute without ..")
    path = _normalize_system_alias_path(path)
    _reject_symlink_components(path.parent)
    parent = path.parent.resolve(strict=True)
    if not _path_is_relative_to(parent, root):
        raise ShadowPolicyError("private JSON path must stay inside the invocation")
    _validate_owner_only_directory(parent)
    if expected_current is _PRIVATE_JSON_MISSING:
        if path.exists() or path.is_symlink():
            raise ShadowPolicyError("private JSON state already exists")
    else:
        current = _read_private_json(path, root=root)
        if current != expected_current:
            raise ShadowPolicyError("private JSON state changed concurrently")
    try:
        payload = json.dumps(
            dict(value),
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ShadowPolicyError("private JSON state is not closed JSON") from exc
    if len(payload) > 32 * 1024:
        raise ShadowPolicyError("private JSON state is too large")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=parent,
    )
    temporary_path = pathlib.Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        directory_flags = os.O_RDONLY | int(getattr(os, "O_DIRECTORY", 0))
        directory_descriptor = os.open(parent, directory_flags)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


def _parse_utc_timestamp(value: str, *, label: str) -> dt.datetime:
    if UTC_TIMESTAMP_RE.fullmatch(value) is None:
        raise ShadowPolicyError(f"{label} requires a canonical UTC timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ShadowPolicyError(f"{label} requires a canonical UTC timestamp") from exc
    return parsed


def _validate_daily_window(
    window_start: str,
    window_end: str,
) -> tuple[dt.datetime, dt.datetime]:
    start = _parse_utc_timestamp(window_start, label="Daily pair start")
    end = _parse_utc_timestamp(window_end, label="Daily pair end")
    if (
        end - start != dt.timedelta(days=1)
        or start.timetz() != dt.time(0, tzinfo=dt.timezone.utc)
        or end.timetz() != dt.time(0, tzinfo=dt.timezone.utc)
    ):
        raise ShadowPolicyError("Daily pair must use one exact closed UTC day")
    return start, end


def _validate_latest_closed_daily_window(
    window_start: str,
    window_end: str,
    *,
    now_utc: dt.datetime | None,
) -> None:
    start, end = _validate_daily_window(window_start, window_end)
    current = now_utc or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        raise ShadowPolicyError("Daily pair clock must be timezone-aware")
    current = current.astimezone(dt.timezone.utc)
    expected_end = current.replace(hour=0, minute=0, second=0, microsecond=0)
    if end != expected_end or start != expected_end - dt.timedelta(days=1):
        raise ShadowPolicyError("Daily pair must use the latest closed UTC day")


def _validate_daily_pair_manifest(
    value: Mapping[str, Any],
    *,
    invocation_dir: pathlib.Path,
) -> dict[str, Any]:
    if set(value) != _DAILY_PAIR_FIELDS:
        raise ShadowPolicyError("Daily pair state violates its closed schema")
    if (
        value.get("schema") != DAILY_PAIR_SCHEMA
        or value.get("automation_id") != AUTOMATION_ID
        or value.get("shadow") is not True
        or value.get("production_source_suppressed") is not False
        or value.get("holdout_mechanism") != DAILY_PAIR_HOLDOUT_MECHANISM
        or value.get("holdout_host") not in CANONICAL_HOSTS - {"local"}
        or not isinstance(value.get("holdout_identity_key_id"), str)
        or HOLDOUT_IDENTITY_KEY_ID_RE.fullmatch(value["holdout_identity_key_id"])
        is None
        or value.get("state") not in DAILY_PAIR_STATES
        or not isinstance(value.get("coordinator_path"), str)
    ):
        raise ShadowPolicyError("Daily pair state violates its shadow-only contract")
    coordinator_path = pathlib.Path(str(value["coordinator_path"])).expanduser()
    if not coordinator_path.is_absolute() or ".." in coordinator_path.parts:
        raise ShadowPolicyError("Daily pair coordinator path must be absolute")
    for field in (
        "coordinator_identity_path",
        "run_config",
        "partial_run_dir",
        "backfill_run_dir",
    ):
        raw_path = value.get(field)
        if not isinstance(raw_path, str):
            raise ShadowPolicyError(f"Daily pair {field} must be an absolute path")
        _validate_path_argument(field, raw_path, invocation_dir=invocation_dir)
    window_start = value.get("window_start")
    window_end = value.get("window_end")
    if not isinstance(window_start, str) or not isinstance(window_end, str):
        raise ShadowPolicyError("Daily pair window must use canonical timestamps")
    _validate_daily_window(window_start, window_end)

    partial_run_ref = value.get("partial_run_ref")
    controlled_gap_ref = value.get("controlled_gap_receipt_ref")
    state = value["state"]
    if state in {"partial_starting", "partial_started"}:
        if partial_run_ref is not None or controlled_gap_ref is not None:
            raise ShadowPolicyError("Daily pair partial state has premature lineage")
    elif (
        not isinstance(partial_run_ref, str)
        or RUN_REF_RE.fullmatch(partial_run_ref) is None
        or not isinstance(controlled_gap_ref, str)
        or HOLDOUT_REF_RE.fullmatch(controlled_gap_ref) is None
    ):
        raise ShadowPolicyError("Daily pair backfill state lacks exact lineage")
    return dict(value)


_COORDINATOR_COVERAGE_FIELDS = frozenset(
    {
        "authentication_tag",
        "backfill_of",
        "checkpoint_revision",
        "configuration_root",
        "controlled_gap_receipt_ref",
        "configured_host_refs",
        "covered_host_refs",
        "export_bundle_digest",
        "gap_host_refs",
        "identity_key_id",
        "mode",
        "model_era",
        "partial",
        "policy_commitment",
        "policy_era",
        "production_configuration_ref",
        "receipt_ref",
        "run_ref",
        "schema",
        "source_evidence_commitment",
        "source_receipt_refs",
        "source_snapshot_refs",
        "source_units",
        "specification_digest",
        "version_commitment",
        "window_end",
        "window_start",
    }
)
_SOURCE_STATUS_CELL_FIELDS = frozenset(
    {"lease_ref", "snapshot_ref", "status", "transport_receipt_ref"}
)
COORDINATOR_VERIFIER_SCHEMA = "coordinator_verifier_result_v1"
MAX_COORDINATOR_VERIFIER_PAYLOAD_BYTES = 64 * 1024
_COORDINATOR_VERIFIER_SCRIPT = r"""
import importlib
import json
import pathlib
import sys


def reject_constant(value):
    raise ValueError(f"non-standard JSON constant: {value}")


operation, scripts_root, identity_path, payload = sys.argv[1:]
scripts = pathlib.Path(scripts_root).resolve(strict=True)
sys.path.insert(0, str(scripts))
identity_module = importlib.import_module("retrospective_v2.identity")
identity = identity_module.load_identity_key(pathlib.Path(identity_path))
if operation == "load-identity":
    result = {
        "operation": operation,
        "schema": "coordinator_verifier_result_v1",
        "secret_hex": bytes(identity.secret).hex(),
    }
elif operation == "verify-coverage":
    authority_module = importlib.import_module("retrospective_v2.authority")
    receipt = json.loads(payload, parse_constant=reject_constant)
    verified = authority_module.verify_shadow_coverage_receipt(identity, receipt)
    result = {
        "operation": operation,
        "schema": "coordinator_verifier_result_v1",
        "verified": verified,
    }
else:
    raise ValueError("unsupported coordinator verifier operation")
print(json.dumps(result, allow_nan=False, separators=(",", ":"), sort_keys=True))
"""
CoverageVerifier = Callable[
    [pathlib.Path, pathlib.Path, Mapping[str, Any], pathlib.Path], dict[str, Any]
]
CoordinatorIdentityLoader = Callable[[pathlib.Path, pathlib.Path, pathlib.Path], bytes]


def _run_coordinator_verifier(
    *,
    operation: str,
    coordinator_path: pathlib.Path,
    coordinator_identity_path: pathlib.Path,
    invocation_dir: pathlib.Path,
    receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if sys.platform != "darwin" or not SANDBOX_EXEC_PATH.is_file():
        raise ShadowPolicyError(
            "the supported macOS coordinator verifier sandbox is required"
        )
    payload = ""
    if receipt is not None:
        try:
            payload = json.dumps(
                dict(receipt),
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError) as exc:
            raise ShadowPolicyError(
                "coordinator verifier payload is not closed JSON"
            ) from exc
    if len(payload.encode("utf-8")) > MAX_COORDINATOR_VERIFIER_PAYLOAD_BYTES:
        raise ShadowPolicyError("coordinator verifier payload is too large")
    python_path = pathlib.Path(sys.executable).resolve(strict=True)
    command = (
        str(SANDBOX_EXEC_PATH),
        "-p",
        _sandbox_profile(invocation_dir, coordinator_path),
        str(python_path),
        "-I",
        "-c",
        _COORDINATOR_VERIFIER_SCRIPT,
        operation,
        str(coordinator_path.parent.resolve(strict=True)),
        str(coordinator_identity_path),
        payload,
    )
    completed = _run_supervised_process(
        command,
        cwd=invocation_dir,
        environment=_sandbox_environment(invocation_dir),
        capture_output=True,
        timeout_seconds=COORDINATOR_STATUS_TIMEOUT_SECONDS,
    )
    if (
        completed.returncode != 0
        or completed.stdout is None
        or completed.stderr is None
        or completed.stderr != ""
        or len(completed.stdout.splitlines()) != 1
    ):
        raise ShadowPolicyError("coordinator verifier subprocess failed")
    try:
        result = json.loads(
            completed.stdout,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except (TypeError, ValueError) as exc:
        raise ShadowPolicyError("coordinator verifier returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise ShadowPolicyError("coordinator verifier returned invalid data")
    return result


def _load_coordinator_identity_key(
    coordinator_path: pathlib.Path,
    coordinator_identity_path: pathlib.Path,
    invocation_dir: pathlib.Path,
) -> bytes:
    result = _run_coordinator_verifier(
        operation="load-identity",
        coordinator_path=coordinator_path,
        coordinator_identity_path=coordinator_identity_path,
        invocation_dir=invocation_dir,
    )
    if set(result) != {"operation", "schema", "secret_hex"} or (
        result.get("operation") != "load-identity"
        or result.get("schema") != COORDINATOR_VERIFIER_SCHEMA
        or not isinstance(result.get("secret_hex"), str)
        or re.fullmatch(r"[0-9a-f]{64}", result["secret_hex"]) is None
    ):
        raise ShadowPolicyError("coordinator identity key has an invalid length")
    return bytes.fromhex(result["secret_hex"])


def _verify_coordinator_coverage_receipt(
    coordinator_path: pathlib.Path,
    coordinator_identity_path: pathlib.Path,
    receipt: Mapping[str, Any],
    invocation_dir: pathlib.Path,
) -> dict[str, Any]:
    result = _run_coordinator_verifier(
        operation="verify-coverage",
        coordinator_path=coordinator_path,
        coordinator_identity_path=coordinator_identity_path,
        invocation_dir=invocation_dir,
        receipt=receipt,
    )
    if (
        set(result) != {"operation", "schema", "verified"}
        or result.get("operation") != "verify-coverage"
        or result.get("schema") != COORDINATOR_VERIFIER_SCHEMA
        or not isinstance(result.get("verified"), dict)
    ):
        raise ShadowPolicyError("coordinator coverage verifier returned invalid data")
    return result["verified"]


def _status_for_run(
    *,
    coordinator_path: pathlib.Path,
    coordinator_identity_path: pathlib.Path,
    run_dir: pathlib.Path,
    invocation_dir: pathlib.Path,
    status_query: StatusQuery,
) -> dict[str, Any]:
    arguments = (
        "status",
        "--identity-path",
        str(coordinator_identity_path),
        "--require-existing-identity",
        "--run-dir",
        str(run_dir),
    )
    return _status_result(
        _guarded_status_query(
            coordinator_path=coordinator_path,
            arguments=arguments,
            invocation_dir=invocation_dir,
            status_query=status_query,
        )
    )


def _verified_coverage(
    status: Mapping[str, Any],
    *,
    coordinator_path: pathlib.Path,
    coordinator_identity_path: pathlib.Path,
    invocation_dir: pathlib.Path,
    coverage_verifier: CoverageVerifier,
) -> dict[str, Any]:
    publication = status.get("publication")
    raw_receipt = (
        publication.get("coverage_receipt")
        if isinstance(publication, Mapping)
        else None
    )
    if (
        not isinstance(raw_receipt, Mapping)
        or set(raw_receipt) != _COORDINATOR_COVERAGE_FIELDS
    ):
        raise ShadowPolicyError(
            "terminal shadow status lacks its closed coverage receipt"
        )
    verified = coverage_verifier(
        coordinator_path,
        coordinator_identity_path,
        raw_receipt,
        invocation_dir,
    )
    if verified != dict(raw_receipt):
        raise ShadowPolicyError("coverage verifier changed the coordinator receipt")
    status_window = status.get("window")
    if (
        verified.get("schema") != COORDINATOR_COVERAGE_SCHEMA
        or verified.get("run_ref") != status.get("run_ref")
        or verified.get("identity_key_id") != status.get("identity_key_id")
        or verified.get("mode") != status.get("mode")
        or not isinstance(status_window, Mapping)
        or set(status_window) != {"end", "start"}
        or verified.get("window_start") != status_window.get("start")
        or verified.get("window_end") != status_window.get("end")
        or not isinstance(verified.get("checkpoint_revision"), int)
        or isinstance(verified.get("checkpoint_revision"), bool)
        or verified["checkpoint_revision"] < 1
        or status.get("checkpoint_revision") < verified["checkpoint_revision"]
    ):
        raise ShadowPolicyError("coverage receipt is not current for this shadow run")
    return verified


def _status_source_cell(
    status: Mapping[str, Any],
    *,
    host: str,
    source_kind: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    coverage = status.get("coverage")
    hosts = coverage.get("hosts") if isinstance(coverage, Mapping) else None
    host_coverage = hosts.get(host) if isinstance(hosts, Mapping) else None
    cells = host_coverage.get("cells") if isinstance(host_coverage, Mapping) else None
    cell = cells.get(source_kind) if isinstance(cells, Mapping) else None
    if not isinstance(host_coverage, dict) or not isinstance(cell, dict):
        raise ShadowPolicyError("shadow status lacks the held-out source cell")
    return host_coverage, cell


def _verified_holdout_receipt(
    *,
    module: Any,
    receipt: Mapping[str, Any],
    holdout_identity_path: pathlib.Path,
    now_utc: dt.datetime | None,
) -> tuple[dict[str, Any], bytes]:
    closed_receipt = dict(receipt)
    identity_key = module._read_session_shards_shadow_identity_key(
        holdout_identity_path
    )
    receipt_bindings = (
        closed_receipt.get("host"),
        closed_receipt.get("window_start"),
        closed_receipt.get("window_end"),
        closed_receipt.get("source_kind"),
        closed_receipt.get("source_lease_ref"),
    )
    if not all(isinstance(value, str) for value in receipt_bindings):
        raise ShadowPolicyError("holdout receipt binding fields must be strings")
    module._verify_session_shards_holdout_receipt(
        closed_receipt,
        identity_key=identity_key,
        expected_host=receipt_bindings[0],
        expected_window_start=receipt_bindings[1],
        expected_window_end=receipt_bindings[2],
        expected_source_kind=receipt_bindings[3],
        expected_source_lease_ref=receipt_bindings[4],
        now_utc=now_utc,
    )
    if closed_receipt["host"] not in CANONICAL_HOSTS - {"local"}:
        raise ShadowPolicyError(
            "authenticated holdout does not name a canonical remote host"
        )
    return closed_receipt, identity_key


def _validate_daily_partial_successor(
    *,
    module: Any,
    receipt: Mapping[str, Any],
    status: Mapping[str, Any],
    coverage_receipt: Mapping[str, Any],
    expected_run_ref: str,
    window_start: str,
    window_end: str,
) -> None:
    host = receipt.get("host")
    source_kind = receipt.get("source_kind")
    source_lease_ref = receipt.get("source_lease_ref")
    holdout_ref = receipt.get("holdout_ref")
    if not all(
        isinstance(value, str)
        for value in (host, source_kind, source_lease_ref, holdout_ref)
    ):
        raise ShadowPolicyError("authenticated holdout lacks Daily pair bindings")
    host_coverage, source_cell = _status_source_cell(
        status,
        host=host,
        source_kind=source_kind,
    )
    host_ref = host_coverage.get("host_ref")
    coverage = status.get("coverage")
    hosts = coverage.get("hosts") if isinstance(coverage, Mapping) else None
    host_refs = (
        {
            name: value.get("host_ref")
            for name, value in hosts.items()
            if isinstance(name, str) and isinstance(value, Mapping)
        }
        if isinstance(hosts, Mapping)
        else {}
    )
    expected_gap = {
        "host": host,
        "host_ref": host_ref,
        "lease_ref": source_lease_ref,
        "reason": module.SESSION_SHARDS_HOLDOUT_REASON,
        "receipt_ref": holdout_ref,
        "source_kind": source_kind,
    }
    gaps = status.get("gaps")
    lineage = status.get("lineage")
    configuration_root = coverage_receipt.get("configuration_root")
    if (
        receipt.get("reason") != module.SESSION_SHARDS_HOLDOUT_REASON
        or receipt.get("window_start") != window_start
        or receipt.get("window_end") != window_end
        or status.get("run_ref") != expected_run_ref
        or status.get("stage") != "export"
        or status.get("mode") != "daily"
        or status.get("window") != {"start": window_start, "end": window_end}
        or not isinstance(coverage, Mapping)
        or coverage.get("status") != "partial"
        or not isinstance(hosts, Mapping)
        or set(hosts) != CANONICAL_HOSTS
        or set(host_refs) != CANONICAL_HOSTS
        or len(set(host_refs.values())) != len(CANONICAL_HOSTS)
        or any(
            not isinstance(value, str) or HOST_REF_RE.fullmatch(value) is None
            for value in host_refs.values()
        )
        or host_coverage.get("status") != "gap"
        or set(source_cell) != _SOURCE_STATUS_CELL_FIELDS
        or source_cell.get("lease_ref") != source_lease_ref
        or source_cell.get("snapshot_ref") is not None
        or source_cell.get("status") != "gap"
        or source_cell.get("transport_receipt_ref") is not None
        or any(
            not isinstance(hosts.get(name), Mapping)
            or hosts[name].get("status") not in {"complete", "no_activity"}
            for name in CANONICAL_HOSTS - {host}
        )
        or not isinstance(gaps, list)
        or gaps != [expected_gap]
        or status.get("active_source_leases") != []
        or not isinstance(lineage, Mapping)
        or lineage.get("backfill_of") is not None
        or coverage_receipt.get("partial") is not True
        or coverage_receipt.get("backfill_of") is not None
        or coverage_receipt.get("controlled_gap_receipt_ref") != holdout_ref
        or coverage_receipt.get("configured_host_refs") != sorted(host_refs.values())
        or coverage_receipt.get("covered_host_refs")
        != sorted(value for name, value in host_refs.items() if name != host)
        or coverage_receipt.get("gap_host_refs") != [host_ref]
        or not isinstance(configuration_root, str)
        or CONFIGURATION_ROOT_RE.fullmatch(configuration_root) is None
    ):
        raise ShadowPolicyError(
            "Daily backfill requires the authenticated terminal partial predecessor"
        )


def _daily_pair_start_arguments(
    manifest: Mapping[str, Any],
    *,
    invocation_dir: pathlib.Path,
    receipt_path: pathlib.Path | None = None,
) -> tuple[str, ...]:
    backfill = receipt_path is not None
    arguments = [
        "start",
        "--identity-path",
        str(manifest["coordinator_identity_path"]),
        "--require-existing-identity",
        "--history-repo",
        str(invocation_dir / "simulation-history"),
        "--history-target-ref",
        SHADOW_HISTORY_TARGET_REF,
        "--run-config",
        str(manifest["run_config"]),
        "--run-dir",
        str(manifest["backfill_run_dir"] if backfill else manifest["partial_run_dir"]),
        "--mode",
        "daily",
        "--start",
        str(manifest["window_start"]),
        "--end",
        str(manifest["window_end"]),
    ]
    if backfill:
        arguments.extend(
            (
                "--host",
                str(manifest["holdout_host"]),
                "--backfill-of",
                str(manifest["partial_run_ref"]),
                "--controlled-gap-receipt",
                str(receipt_path),
            )
        )
    else:
        for host in CANONICAL_HOST_ORDER:
            arguments.extend(("--host", host))
        arguments.append("--allow-partial")
    arguments.append("--shadow")
    return tuple(arguments)


def start_daily_shadow_pair(
    *,
    invocation_dir: pathlib.Path,
    holdout_host: str,
    holdout_identity_path: pathlib.Path,
    coordinator_identity_path: pathlib.Path,
    run_config: pathlib.Path,
    partial_run_dir: pathlib.Path,
    backfill_run_dir: pathlib.Path,
    window_start: str,
    window_end: str,
    shadow_root: pathlib.Path = SHADOW_ROOT,
    coordinator_path: pathlib.Path = COORDINATOR_PATH,
    transport_module: Any | None = None,
    executor: Executor = _sandboxed_executor,
    now_utc: dt.datetime | None = None,
) -> subprocess.CompletedProcess[str]:
    invocation_dir, shadow_root = _prepare_invocation_directory(
        invocation_dir,
        shadow_root=shadow_root,
    )
    if holdout_host not in CANONICAL_HOSTS - {"local"}:
        raise ShadowPolicyError(
            "Daily pair holdout must name one canonical remote host"
        )
    _validate_latest_closed_daily_window(
        window_start,
        window_end,
        now_utc=now_utc,
    )
    for option, path in (
        ("--holdout-identity-path", holdout_identity_path),
        ("--coordinator-identity-path", coordinator_identity_path),
        ("--run-config", run_config),
        ("--partial-run-dir", partial_run_dir),
        ("--backfill-run-dir", backfill_run_dir),
    ):
        _validate_path_argument(option, str(path), invocation_dir=invocation_dir)
    if partial_run_dir == backfill_run_dir:
        raise ShadowPolicyError(
            "Daily partial and backfill run directories must differ"
        )
    for run_dir in (partial_run_dir, backfill_run_dir):
        if run_dir.exists() or run_dir.is_symlink():
            raise ShadowPolicyError("Daily pair run directories must be unused")
    _read_private_json(coordinator_identity_path, root=invocation_dir)
    _read_private_json(run_config, root=invocation_dir)
    module = transport_module or _load_transport_module()
    holdout_identity_key = module._read_session_shards_shadow_identity_key(
        holdout_identity_path.resolve(strict=True)
    )
    holdout_identity_key_id = module._session_shards_holdout_identity_key_id(
        holdout_identity_key
    )
    declared_coordinator_path = coordinator_path
    coordinator_path = _validate_coordinator_path(declared_coordinator_path)
    simulation_history = invocation_dir / "simulation-history"
    _ensure_owner_only_directory(simulation_history)
    if any(simulation_history.iterdir()):
        raise ShadowPolicyError("shadow simulation history must remain empty")

    manifest_path = invocation_dir / DAILY_PAIR_FILENAME
    manifest = _validate_daily_pair_manifest(
        {
            "automation_id": AUTOMATION_ID,
            "backfill_run_dir": str(backfill_run_dir),
            "controlled_gap_receipt_ref": None,
            "coordinator_identity_path": str(coordinator_identity_path),
            "coordinator_path": str(declared_coordinator_path),
            "holdout_host": holdout_host,
            "holdout_identity_key_id": holdout_identity_key_id,
            "holdout_mechanism": DAILY_PAIR_HOLDOUT_MECHANISM,
            "partial_run_dir": str(partial_run_dir),
            "partial_run_ref": None,
            "production_source_suppressed": False,
            "run_config": str(run_config),
            "schema": DAILY_PAIR_SCHEMA,
            "shadow": True,
            "state": "partial_starting",
            "window_end": window_end,
            "window_start": window_start,
        },
        invocation_dir=invocation_dir,
    )

    with daily_pair_mutex(shadow_root):
        _write_private_json(manifest_path, manifest, root=invocation_dir)
        result = run_guarded_coordinator(
            _daily_pair_start_arguments(manifest, invocation_dir=invocation_dir),
            invocation_dir=invocation_dir,
            shadow_root=shadow_root,
            coordinator_path=coordinator_path,
            executor=executor,
        )
        if result.returncode == 0:
            _validate_owner_only_directory(partial_run_dir)
            started = {**manifest, "state": "partial_started"}
            _validate_daily_pair_manifest(started, invocation_dir=invocation_dir)
            _write_private_json(
                manifest_path,
                started,
                root=invocation_dir,
                expected_current=manifest,
            )
        return result


def start_daily_shadow_pair_successor(
    *,
    invocation_dir: pathlib.Path,
    receipt_path: pathlib.Path,
    holdout_identity_path: pathlib.Path,
    coordinator_identity_path: pathlib.Path,
    partial_run_ref: str,
    shadow_root: pathlib.Path = SHADOW_ROOT,
    coordinator_path: pathlib.Path = COORDINATOR_PATH,
    transport_module: Any | None = None,
    executor: Executor = _sandboxed_executor,
    status_query: StatusQuery = _sandboxed_status_query,
    coverage_verifier: CoverageVerifier = _verify_coordinator_coverage_receipt,
    now_utc: dt.datetime | None = None,
) -> subprocess.CompletedProcess[str]:
    invocation_dir, shadow_root = _prepare_invocation_directory(
        invocation_dir,
        shadow_root=shadow_root,
    )
    if RUN_REF_RE.fullmatch(partial_run_ref) is None:
        raise ShadowPolicyError("Daily pair successor requires an exact v2 run ref")
    for option, path in (
        ("--receipt", receipt_path),
        ("--holdout-identity-path", holdout_identity_path),
        ("--coordinator-identity-path", coordinator_identity_path),
    ):
        _validate_path_argument(option, str(path), invocation_dir=invocation_dir)
    manifest_path = invocation_dir / DAILY_PAIR_FILENAME
    manifest = _validate_daily_pair_manifest(
        _read_private_json(manifest_path, root=invocation_dir),
        invocation_dir=invocation_dir,
    )
    if manifest["state"] != "partial_started":
        raise ShadowPolicyError("Daily pair successor requires one started partial")
    if manifest["coordinator_identity_path"] != str(coordinator_identity_path):
        raise ShadowPolicyError("Daily pair coordinator identity changed")
    declared_coordinator_path = coordinator_path
    if manifest["coordinator_path"] != str(declared_coordinator_path):
        raise ShadowPolicyError("Daily pair coordinator path changed")
    coordinator_path = _validate_coordinator_path(declared_coordinator_path)
    _read_private_json(coordinator_identity_path, root=invocation_dir)
    _read_private_json(pathlib.Path(manifest["run_config"]), root=invocation_dir)
    receipt = _read_private_json(receipt_path, root=invocation_dir)
    module = transport_module or _load_transport_module()
    receipt, _identity_key = _verified_holdout_receipt(
        module=module,
        receipt=receipt,
        holdout_identity_path=holdout_identity_path.resolve(strict=True),
        now_utc=now_utc,
    )
    if receipt["identity_key_id"] != manifest["holdout_identity_key_id"]:
        raise ShadowPolicyError("Daily pair holdout identity changed")
    if (
        receipt["host"] != manifest["holdout_host"]
        or receipt["window_start"] != manifest["window_start"]
        or receipt["window_end"] != manifest["window_end"]
    ):
        raise ShadowPolicyError("Daily pair receipt does not match its planned holdout")

    partial_run_dir = pathlib.Path(manifest["partial_run_dir"]).resolve(strict=True)
    backfill_run_dir = pathlib.Path(manifest["backfill_run_dir"])
    _validate_owner_only_directory(partial_run_dir)
    if backfill_run_dir.exists() or backfill_run_dir.is_symlink():
        raise ShadowPolicyError("Daily backfill run directory must be unused")

    with (
        daily_pair_mutex(shadow_root),
        host_mutex(shadow_root, str(manifest["holdout_host"])),
    ):
        current = _validate_daily_pair_manifest(
            _read_private_json(manifest_path, root=invocation_dir),
            invocation_dir=invocation_dir,
        )
        if current != manifest:
            raise ShadowPolicyError("Daily pair state changed before its successor")
        partial_status = _status_for_run(
            coordinator_path=coordinator_path,
            coordinator_identity_path=coordinator_identity_path,
            run_dir=partial_run_dir,
            invocation_dir=invocation_dir,
            status_query=status_query,
        )
        partial_coverage = _verified_coverage(
            partial_status,
            coordinator_path=coordinator_path,
            coordinator_identity_path=coordinator_identity_path,
            invocation_dir=invocation_dir,
            coverage_verifier=coverage_verifier,
        )
        _validate_daily_partial_successor(
            module=module,
            receipt=receipt,
            status=partial_status,
            coverage_receipt=partial_coverage,
            expected_run_ref=partial_run_ref,
            window_start=str(manifest["window_start"]),
            window_end=str(manifest["window_end"]),
        )
        starting = {
            **manifest,
            "controlled_gap_receipt_ref": receipt["holdout_ref"],
            "partial_run_ref": partial_run_ref,
            "state": "backfill_starting",
        }
        _validate_daily_pair_manifest(starting, invocation_dir=invocation_dir)
        _write_private_json(
            manifest_path,
            starting,
            root=invocation_dir,
            expected_current=manifest,
        )
        result = run_guarded_coordinator(
            _daily_pair_start_arguments(
                starting,
                invocation_dir=invocation_dir,
                receipt_path=receipt_path,
            ),
            invocation_dir=invocation_dir,
            shadow_root=shadow_root,
            coordinator_path=coordinator_path,
            executor=executor,
        )
        if result.returncode == 0:
            _validate_owner_only_directory(backfill_run_dir)
            started = {**starting, "state": "backfill_started"}
            _validate_daily_pair_manifest(started, invocation_dir=invocation_dir)
            _write_private_json(
                manifest_path,
                started,
                root=invocation_dir,
                expected_current=starting,
            )
        return result


def _authenticated_backfill_result_from_status(
    *,
    module: Any,
    holdout_identity_key: bytes,
    coordinator_identity_key: bytes,
    receipt: dict[str, Any],
    partial_status: dict[str, Any],
    backfill_status: dict[str, Any],
    partial_coverage: dict[str, Any],
    backfill_coverage: dict[str, Any],
    now_utc: dt.datetime | None,
) -> dict[str, Any]:
    host = str(receipt["host"])
    source_kind = str(receipt["source_kind"])
    window_start = str(receipt["window_start"])
    window_end = str(receipt["window_end"])
    partial_lease_ref = str(receipt["source_lease_ref"])
    partial_host, partial_cell = _status_source_cell(
        partial_status,
        host=host,
        source_kind=source_kind,
    )
    backfill_host, backfill_cell = _status_source_cell(
        backfill_status,
        host=host,
        source_kind=source_kind,
    )
    host_ref = partial_host.get("host_ref")
    backfill_lease_ref = backfill_cell.get("lease_ref")
    snapshot_ref = backfill_cell.get("snapshot_ref")
    source_receipt_ref = backfill_cell.get("transport_receipt_ref")
    cell_status = backfill_cell.get("status")
    source_outcome = "no_activity" if cell_status == "verified_absent" else cell_status

    partial_run_ref = partial_status.get("run_ref")
    backfill_run_ref = backfill_status.get("run_ref")
    partial_configuration_root = partial_coverage.get("configuration_root")
    backfill_configuration_root = backfill_coverage.get("configuration_root")
    partial_window = partial_status.get("window")
    backfill_window = backfill_status.get("window")
    partial_hosts = partial_status.get("coverage", {}).get("hosts")
    backfill_hosts = backfill_status.get("coverage", {}).get("hosts")
    backfill_cells = backfill_host.get("cells")
    partial_host_refs = (
        {
            name: value.get("host_ref")
            for name, value in partial_hosts.items()
            if isinstance(name, str) and isinstance(value, Mapping)
        }
        if isinstance(partial_hosts, Mapping)
        else {}
    )
    partial_gaps = partial_status.get("gaps")
    partial_gap = (
        partial_gaps[0]
        if isinstance(partial_gaps, list)
        and len(partial_gaps) == 1
        and isinstance(partial_gaps[0], Mapping)
        else None
    )
    holdout_ref = receipt.get("holdout_ref")
    expected_gap_binding = {
        "host": host,
        "host_ref": host_ref,
        "lease_ref": partial_lease_ref,
        "reason": module.SESSION_SHARDS_HOLDOUT_REASON,
        "receipt_ref": holdout_ref,
        "source_kind": source_kind,
    }
    lineage = backfill_status.get("lineage")
    if (
        receipt.get("reason") != module.SESSION_SHARDS_HOLDOUT_REASON
        or partial_status.get("stage") != "export"
        or backfill_status.get("stage") != "export"
        or partial_status.get("mode") != "daily"
        or backfill_status.get("mode") != "daily"
        or partial_status.get("coverage", {}).get("status") != "partial"
        or backfill_status.get("coverage", {}).get("status") != "complete"
        or partial_window != {"start": window_start, "end": window_end}
        or backfill_window != partial_window
        or partial_host.get("status") != "gap"
        or set(partial_cell) != _SOURCE_STATUS_CELL_FIELDS
        or partial_cell.get("status") != "gap"
        or partial_cell.get("lease_ref") != partial_lease_ref
        or not isinstance(partial_hosts, Mapping)
        or set(partial_hosts) != CANONICAL_HOSTS
        or set(partial_host_refs) != CANONICAL_HOSTS
        or len(set(partial_host_refs.values())) != len(CANONICAL_HOSTS)
        or any(
            not isinstance(value, str) or HOST_REF_RE.fullmatch(value) is None
            for value in partial_host_refs.values()
        )
        or any(
            not isinstance(partial_hosts.get(name), Mapping)
            or partial_hosts[name].get("status") not in {"complete", "no_activity"}
            for name in CANONICAL_HOSTS - {host}
        )
        or partial_gap is None
        or any(
            partial_gap.get(key) != value for key, value in expected_gap_binding.items()
        )
        or not isinstance(backfill_hosts, Mapping)
        or set(backfill_hosts) != {host}
        or not isinstance(backfill_cells, Mapping)
        or set(backfill_cells) != {source_kind}
        or set(backfill_cell) != _SOURCE_STATUS_CELL_FIELDS
        or cell_status not in {"complete", "no_activity", "verified_absent"}
        or source_outcome not in {"complete", "no_activity"}
        or backfill_host.get("status") != source_outcome
        or not isinstance(lineage, Mapping)
        or lineage.get("backfill_of") != partial_run_ref
        or partial_coverage.get("partial") is not True
        or partial_coverage.get("backfill_of") is not None
        or partial_coverage.get("configured_host_refs")
        != sorted(partial_host_refs.values())
        or partial_coverage.get("covered_host_refs")
        != sorted(value for name, value in partial_host_refs.items() if name != host)
        or partial_coverage.get("gap_host_refs") != [host_ref]
        or backfill_coverage.get("partial") is not False
        or backfill_coverage.get("backfill_of") != partial_run_ref
        or backfill_coverage.get("configured_host_refs") != [host_ref]
        or backfill_coverage.get("covered_host_refs") != [host_ref]
        or backfill_coverage.get("gap_host_refs") != []
        or partial_coverage.get("controlled_gap_receipt_ref") != holdout_ref
        or backfill_coverage.get("controlled_gap_receipt_ref") != holdout_ref
        or not isinstance(partial_configuration_root, str)
        or CONFIGURATION_ROOT_RE.fullmatch(partial_configuration_root) is None
        or backfill_configuration_root != partial_configuration_root
        or not isinstance(backfill_run_ref, str)
        or RUN_REF_RE.fullmatch(backfill_run_ref) is None
        or not isinstance(partial_run_ref, str)
        or RUN_REF_RE.fullmatch(partial_run_ref) is None
        or backfill_run_ref == partial_run_ref
        or not isinstance(host_ref, str)
        or HOST_REF_RE.fullmatch(host_ref) is None
        or not isinstance(backfill_lease_ref, str)
        or backfill_lease_ref == partial_lease_ref
        or not isinstance(snapshot_ref, str)
        or SOURCE_SNAPSHOT_REF_RE.fullmatch(snapshot_ref) is None
        or not isinstance(source_receipt_ref, str)
        or SOURCE_RECEIPT_REF_RE.fullmatch(source_receipt_ref) is None
        or backfill_coverage.get("source_snapshot_refs") != [snapshot_ref]
        or backfill_coverage.get("source_receipt_refs") != [source_receipt_ref]
        or backfill_coverage.get("source_units")
        != {
            "consumed_candidate": 1,
            "expected": 1,
            "explicit_gap": 0,
            "structurally_excluded": 0,
        }
        or not isinstance(backfill_coverage.get("source_evidence_commitment"), str)
        or SOURCE_EVIDENCE_RE.fullmatch(backfill_coverage["source_evidence_commitment"])
        is None
        or partial_status.get("active_source_leases") != []
        or backfill_status.get("active_source_leases") != []
        or backfill_status.get("gaps") != []
    ):
        raise ShadowPolicyError(
            "coordinator status does not prove a complete real backfill"
        )

    manifests = backfill_status.get("accepted_source_manifests")
    matching_manifests = (
        [
            item
            for item in manifests
            if isinstance(item, Mapping)
            and item.get("host_ref") == host_ref
            and item.get("source_kind") == source_kind
            and item.get("snapshot_ref") == snapshot_ref
            and item.get("status") == cell_status
            and isinstance(item.get("record_count"), int)
            and not isinstance(item.get("record_count"), bool)
            and item.get("record_count") >= 0
        ]
        if isinstance(manifests, list)
        else []
    )
    if (
        not isinstance(manifests, list)
        or len(manifests) != 1
        or len(matching_manifests) != 1
    ):
        raise ShadowPolicyError(
            "backfill has no unique accepted session-shards source manifest"
        )
    if (
        source_outcome == "no_activity"
        and matching_manifests[0].get("record_count") != 0
    ):
        raise ShadowPolicyError("no-activity backfill manifest must have zero records")

    return module._session_shards_backfill_result(
        holdout_identity_key=holdout_identity_key,
        coordinator_identity_key=coordinator_identity_key,
        holdout_ref=str(receipt["holdout_ref"]),
        host=host,
        host_ref=host_ref,
        window_start=window_start,
        window_end=window_end,
        source_kind=source_kind,
        partial_source_lease_ref=partial_lease_ref,
        backfill_source_lease_ref=backfill_lease_ref,
        partial_run_ref=partial_run_ref,
        backfill_run_ref=backfill_run_ref,
        backfill_of_run_ref=partial_run_ref,
        partial_configuration_root=partial_configuration_root,
        backfill_configuration_root=backfill_configuration_root,
        coordinator_identity_key_id=str(backfill_coverage["identity_key_id"]),
        source_outcome=source_outcome,
        source_snapshot_ref=snapshot_ref,
        source_transport_receipt_ref=source_receipt_ref,
        evidence_digest=str(backfill_coverage["source_evidence_commitment"]),
        terminal_completion_ref=str(backfill_coverage["receipt_ref"]),
        terminal_completion_authentication_tag=str(
            backfill_coverage["authentication_tag"]
        ),
        terminal_completion_revision=int(backfill_coverage["checkpoint_revision"]),
        status_checkpoint_revision=int(backfill_status["checkpoint_revision"]),
        now_utc=now_utc,
    )


def record_backfill_replacement(
    *,
    invocation_dir: pathlib.Path,
    receipt_path: pathlib.Path,
    holdout_identity_path: pathlib.Path,
    coordinator_identity_path: pathlib.Path,
    partial_run_dir: pathlib.Path,
    backfill_run_dir: pathlib.Path,
    shadow_root: pathlib.Path = SHADOW_ROOT,
    coordinator_path: pathlib.Path = COORDINATOR_PATH,
    transport_module: Any | None = None,
    status_query: StatusQuery = _sandboxed_status_query,
    coverage_verifier: CoverageVerifier = _verify_coordinator_coverage_receipt,
    coordinator_identity_loader: CoordinatorIdentityLoader = (
        _load_coordinator_identity_key
    ),
    now_utc: dt.datetime | None = None,
) -> str:
    invocation_dir, shadow_root = _prepare_invocation_directory(
        invocation_dir,
        shadow_root=shadow_root,
    )
    for option, path in (
        ("--holdout-identity-path", holdout_identity_path),
        ("--coordinator-identity-path", coordinator_identity_path),
        ("--partial-run-dir", partial_run_dir),
        ("--backfill-run-dir", backfill_run_dir),
    ):
        _validate_path_argument(option, str(path), invocation_dir=invocation_dir)
    holdout_identity_path = holdout_identity_path.resolve(strict=True)
    coordinator_identity_path = coordinator_identity_path.resolve(strict=True)
    partial_run_dir = partial_run_dir.resolve(strict=True)
    backfill_run_dir = backfill_run_dir.resolve(strict=True)
    _read_private_json(coordinator_identity_path, root=invocation_dir)
    coordinator_path = _validate_coordinator_path(coordinator_path)
    receipt = _read_private_json(receipt_path, root=invocation_dir)
    ledger_path = shadow_root / "campaign-ledger.sqlite3"
    module = transport_module or _load_transport_module()
    receipt, holdout_identity_key = _verified_holdout_receipt(
        module=module,
        receipt=receipt,
        holdout_identity_path=holdout_identity_path,
        now_utc=now_utc,
    )
    coordinator_identity_key = coordinator_identity_loader(
        coordinator_path,
        coordinator_identity_path,
        invocation_dir,
    )
    host = str(receipt["host"])
    with host_mutex(shadow_root, host):
        partial_status = _status_for_run(
            coordinator_path=coordinator_path,
            coordinator_identity_path=coordinator_identity_path,
            run_dir=partial_run_dir,
            invocation_dir=invocation_dir,
            status_query=status_query,
        )
        backfill_status = _status_for_run(
            coordinator_path=coordinator_path,
            coordinator_identity_path=coordinator_identity_path,
            run_dir=backfill_run_dir,
            invocation_dir=invocation_dir,
            status_query=status_query,
        )
        partial_coverage = _verified_coverage(
            partial_status,
            coordinator_path=coordinator_path,
            coordinator_identity_path=coordinator_identity_path,
            invocation_dir=invocation_dir,
            coverage_verifier=coverage_verifier,
        )
        backfill_coverage = _verified_coverage(
            backfill_status,
            coordinator_path=coordinator_path,
            coordinator_identity_path=coordinator_identity_path,
            invocation_dir=invocation_dir,
            coverage_verifier=coverage_verifier,
        )
        backfill_result = _authenticated_backfill_result_from_status(
            module=module,
            holdout_identity_key=holdout_identity_key,
            coordinator_identity_key=coordinator_identity_key,
            receipt=receipt,
            partial_status=partial_status,
            backfill_status=backfill_status,
            partial_coverage=partial_coverage,
            backfill_coverage=backfill_coverage,
            now_utc=now_utc,
        )
        return str(
            module._consume_session_shards_holdout_for_backfill(
                ledger_path=ledger_path,
                receipt=receipt,
                holdout_identity_key=holdout_identity_key,
                coordinator_identity_key=coordinator_identity_key,
                backfill_result=backfill_result,
                now_utc=now_utc,
            )
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail-closed runner for Session Retrospective v2 shadow automation.",
        epilog=f"Installed Session Retrospective v2 CLI: {COORDINATOR_PATH}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    subparsers = parser.add_subparsers(dest="runner_action", required=True)

    run = subparsers.add_parser(
        "run", help="Run one allowlisted coordinator action.", allow_abbrev=False
    )
    run.add_argument("--invocation-dir", type=pathlib.Path, required=True)
    run.add_argument("--host", choices=sorted(CANONICAL_HOSTS))
    run.add_argument("coordinator_arguments", nargs=argparse.REMAINDER)

    pair = subparsers.add_parser(
        "start-daily-pair",
        help="Start a shadow Daily partial with every canonical source enabled.",
        allow_abbrev=False,
    )
    pair.add_argument("--invocation-dir", type=pathlib.Path, required=True)
    pair.add_argument(
        "--holdout-host",
        choices=sorted(CANONICAL_HOSTS - {"local"}),
        required=True,
    )
    pair.add_argument("--holdout-identity-path", type=pathlib.Path, required=True)
    pair.add_argument("--coordinator-identity-path", type=pathlib.Path, required=True)
    pair.add_argument("--run-config", type=pathlib.Path, required=True)
    pair.add_argument("--partial-run-dir", type=pathlib.Path, required=True)
    pair.add_argument("--backfill-run-dir", type=pathlib.Path, required=True)
    pair.add_argument("--start", required=True)
    pair.add_argument("--end", required=True)

    successor = subparsers.add_parser(
        "start-daily-pair-successor",
        help="Verify the terminal partial and start its exact shadow backfill.",
        allow_abbrev=False,
    )
    successor.add_argument("--invocation-dir", type=pathlib.Path, required=True)
    successor.add_argument("--receipt", type=pathlib.Path, required=True)
    successor.add_argument("--holdout-identity-path", type=pathlib.Path, required=True)
    successor.add_argument(
        "--coordinator-identity-path", type=pathlib.Path, required=True
    )
    successor.add_argument("--partial-run-ref", required=True)

    replace = subparsers.add_parser(
        "record-backfill",
        help="Atomically consume one holdout and record its real backfill.",
        allow_abbrev=False,
    )
    replace.add_argument("--invocation-dir", type=pathlib.Path, required=True)
    replace.add_argument("--receipt", type=pathlib.Path, required=True)
    replace.add_argument("--holdout-identity-path", type=pathlib.Path, required=True)
    replace.add_argument(
        "--coordinator-identity-path", type=pathlib.Path, required=True
    )
    replace.add_argument("--partial-run-dir", type=pathlib.Path, required=True)
    replace.add_argument("--backfill-run-dir", type=pathlib.Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.runner_action == "run":
            coordinator_arguments = list(args.coordinator_arguments)
            if coordinator_arguments[:1] == ["--"]:
                coordinator_arguments.pop(0)
            result = run_guarded_coordinator(
                coordinator_arguments,
                invocation_dir=args.invocation_dir,
                host=args.host,
            )
            return int(result.returncode)
        if args.runner_action == "start-daily-pair":
            result = start_daily_shadow_pair(
                invocation_dir=args.invocation_dir,
                holdout_host=args.holdout_host,
                holdout_identity_path=args.holdout_identity_path,
                coordinator_identity_path=args.coordinator_identity_path,
                run_config=args.run_config,
                partial_run_dir=args.partial_run_dir,
                backfill_run_dir=args.backfill_run_dir,
                window_start=args.start,
                window_end=args.end,
            )
            return int(result.returncode)
        if args.runner_action == "start-daily-pair-successor":
            result = start_daily_shadow_pair_successor(
                invocation_dir=args.invocation_dir,
                receipt_path=args.receipt,
                holdout_identity_path=args.holdout_identity_path,
                coordinator_identity_path=args.coordinator_identity_path,
                partial_run_ref=args.partial_run_ref,
            )
            return int(result.returncode)
        holdout_ref = record_backfill_replacement(
            invocation_dir=args.invocation_dir,
            receipt_path=args.receipt,
            holdout_identity_path=args.holdout_identity_path,
            coordinator_identity_path=args.coordinator_identity_path,
            partial_run_dir=args.partial_run_dir,
            backfill_run_dir=args.backfill_run_dir,
        )
        print(
            json.dumps(
                {
                    "automation_id": AUTOMATION_ID,
                    "holdout_ref": holdout_ref,
                    "result": "backfill_replacement_recorded",
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 0
    except (OSError, RuntimeError, ShadowPolicyError, ValueError) as exc:
        print(f"error={exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
