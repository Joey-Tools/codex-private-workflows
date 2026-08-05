#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import secrets
import shutil
import stat
import subprocess
import sys
import time


LOCK_PATH = Path("private-overlay-source-lock.json")
PRIVATE_MANIFEST_PATH = Path("personal_codex/private-sync-manifest.json")
PRIVATE_RELEASE_SCRIPT_PATH = Path("scripts/private_overlay_release.py")
GENERATED_RECEIPT_PATH = Path("generated-sync-source-lock.json")
MAX_LOCK_BYTES = 64 * 1024
MAX_GIT_OUTPUT_BYTES = 32 * 1024 * 1024
MAX_MANAGED_FILE_BYTES = 32 * 1024 * 1024
MAX_LOCKED_SOURCE_ENTRIES = 100_000
GIT_TIMEOUT_SECONDS = 60
MACOS_GIT_PATH = Path("/Library/Developer/CommandLineTools/usr/bin/git")
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
EXPECTED_SOURCES = (
    ("codex-toolbox", "Joey-Tools/codex-toolbox"),
    ("codex-debug-triage", "Joey-Tools/codex-debug-triage"),
    ("codex-review-workflows", "Joey-Tools/codex-review-workflows"),
    ("codex-workflow-hygiene", "Joey-Tools/codex-workflow-hygiene"),
    ("codex-project-journal", "Joey-Tools/codex-project-journal"),
    ("codex-waited-delivery", "Joey-Tools/codex-waited-delivery"),
)
EXPECTED_SOURCE_FIELDS = frozenset({"name", "repository", "sha", "tree"})
EXPECTED_ROOT_FIELDS = frozenset({"version", "sources", "toolbox_generated_provenance"})
EXPECTED_PROVENANCE_FIELDS = frozenset({"repository", "sha", "receipt_sha256"})
EXPECTED_TOOLBOX_MANAGED_PATHS = (
    "scripts/codex_personal_sync.py",
    "tests/test_codex_personal_sync.py",
    "schema/sync-manifest.schema.json",
    "tests/test_personal_sync_reconciliation_safety.py",
    "tests/test_release_retention.py",
    "tests/test_scheduler_doctor.py",
)


class SourceLockError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourcePin:
    name: str
    repository: str
    sha: str
    tree: str


@dataclass(frozen=True)
class ToolboxGeneratedProvenance:
    repository: str
    sha: str
    receipt_sha256: str


@dataclass(frozen=True)
class SourceLock:
    pins: tuple[SourcePin, ...]
    toolbox_generated_provenance: ToolboxGeneratedProvenance
    digest: str


@dataclass(frozen=True)
class LockedSourceEntry:
    relative: Path
    kind: str
    mode: int
    object_id: str


@dataclass(frozen=True)
class LockedSourceManifest:
    root_kind: str
    root_mode: int
    root_object_id: str
    entries: tuple[LockedSourceEntry, ...]


@dataclass(frozen=True)
class _GitPathBinding:
    path: Path
    device: int
    inode: int
    owner: int
    group: int
    mode: int
    file_type: int


@dataclass(frozen=True)
class _TrustedGitExecutable:
    path: Path
    binding: _GitPathBinding
    chain: tuple[_GitPathBinding, ...]
    require_root_owner: bool


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SourceLockError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json_object(path: Path, *, maximum_bytes: int) -> tuple[dict, bytes]:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise SourceLockError(f"cannot inspect {path}: {error}") from error
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise SourceLockError(f"expected a regular non-symlink file: {path}")
    if metadata.st_size > maximum_bytes:
        raise SourceLockError(f"file exceeds the byte limit: {path}")
    try:
        payload = path.read_bytes()
        parsed = json.loads(payload, object_pairs_hook=_reject_duplicate_keys)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SourceLockError(f"cannot parse {path}: {error}") from error
    if not isinstance(parsed, dict):
        raise SourceLockError(f"expected a JSON object: {path}")
    return parsed, payload


def _require_sha(value: object, *, label: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise SourceLockError(f"{label} must be a lowercase full commit SHA")
    return value


def load_source_lock(repo_root: Path) -> SourceLock:
    path = repo_root / LOCK_PATH
    payload, raw = _load_json_object(path, maximum_bytes=MAX_LOCK_BYTES)
    if frozenset(payload) != EXPECTED_ROOT_FIELDS:
        raise SourceLockError("source lock root fields do not match the contract")
    if payload.get("version") != 1:
        raise SourceLockError("unsupported source lock version")
    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, list) or len(raw_sources) != len(EXPECTED_SOURCES):
        raise SourceLockError("source lock must contain the exact six-source inventory")
    pins: list[SourcePin] = []
    for index, ((expected_name, expected_repo), raw_pin) in enumerate(
        zip(EXPECTED_SOURCES, raw_sources, strict=True)
    ):
        if (
            not isinstance(raw_pin, dict)
            or frozenset(raw_pin) != EXPECTED_SOURCE_FIELDS
        ):
            raise SourceLockError(f"source lock entry {index} fields do not match")
        if raw_pin.get("name") != expected_name:
            raise SourceLockError(f"source lock entry {index} has the wrong name")
        if raw_pin.get("repository") != expected_repo:
            raise SourceLockError(f"source lock entry {index} has the wrong repository")
        pins.append(
            SourcePin(
                name=expected_name,
                repository=expected_repo,
                sha=_require_sha(raw_pin.get("sha"), label=f"{expected_name} sha"),
                tree=_require_sha(raw_pin.get("tree"), label=f"{expected_name} tree"),
            )
        )
    provenance = payload.get("toolbox_generated_provenance")
    if (
        not isinstance(provenance, dict)
        or frozenset(provenance) != EXPECTED_PROVENANCE_FIELDS
    ):
        raise SourceLockError("toolbox generated provenance fields do not match")
    if provenance.get("repository") != "Joey-Tools/codex-personal-sync":
        raise SourceLockError("toolbox generated provenance repository is invalid")
    provenance_sha = _require_sha(provenance.get("sha"), label="toolbox provenance sha")
    receipt_sha = provenance.get("receipt_sha256")
    if (
        not isinstance(receipt_sha, str)
        or re.fullmatch(r"[0-9a-f]{64}", receipt_sha) is None
    ):
        raise SourceLockError("toolbox provenance receipt digest is invalid")
    expected_raw = (
        json.dumps(payload, indent=2, sort_keys=False).encode("utf-8") + b"\n"
    )
    if raw != expected_raw:
        raise SourceLockError("source lock is not in canonical JSON form")
    return SourceLock(
        pins=tuple(pins),
        toolbox_generated_provenance=ToolboxGeneratedProvenance(
            repository=provenance["repository"],
            sha=provenance_sha,
            receipt_sha256=receipt_sha,
        ),
        digest=hashlib.sha256(raw).hexdigest(),
    )


def _release_constant(repo_root: Path, name: str) -> str:
    path = repo_root / PRIVATE_RELEASE_SCRIPT_PATH
    try:
        tree = ast.parse(path.read_bytes(), filename=str(path))
    except (OSError, SyntaxError) as error:
        raise SourceLockError(
            f"cannot parse private release policy: {error}"
        ) from error
    matches: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == name for target in targets
        ):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError) as error:
            raise SourceLockError(
                f"private release policy {name} is not literal"
            ) from error
        if not isinstance(value, str):
            raise SourceLockError(f"private release policy {name} is not a string")
        matches.append(value)
    if len(matches) != 1:
        raise SourceLockError(f"private release policy {name} must appear exactly once")
    return matches[0]


def validate_base_release_binding(repo_root: Path, source_lock: SourceLock) -> None:
    manifest, _raw = _load_json_object(
        repo_root / PRIVATE_MANIFEST_PATH,
        maximum_bytes=1024 * 1024,
    )
    base_release = manifest.get("base_release")
    if not isinstance(base_release, dict):
        raise SourceLockError("private manifest base_release is missing")
    toolbox = source_lock.pins[0]
    expected_repo = _release_constant(repo_root, "REQUIRED_PUBLIC_BASE_RELEASE_REPO")
    expected_sha = _release_constant(repo_root, "REQUIRED_PUBLIC_BASE_RELEASE_SHA")
    if (
        toolbox.name != "codex-toolbox"
        or toolbox.repository != base_release.get("repo")
        or toolbox.sha != base_release.get("sha")
        or toolbox.repository != expected_repo
        or toolbox.sha != expected_sha
    ):
        raise SourceLockError(
            "toolbox source lock, private manifest, and release verifier base identity differ"
        )


def _validate_generated_receipt(
    receipt_path: Path,
    source_lock: SourceLock,
    *,
    label: str,
) -> dict:
    try:
        receipt_metadata = receipt_path.lstat()
    except OSError as error:
        raise SourceLockError(f"cannot inspect {label}: {error}") from error
    if stat.S_IMODE(receipt_metadata.st_mode) != 0o644:
        raise SourceLockError(f"{label} mode must be 0644")
    receipt, raw = _load_json_object(
        receipt_path,
        maximum_bytes=1024 * 1024,
    )
    provenance = source_lock.toolbox_generated_provenance
    if hashlib.sha256(raw).hexdigest() != provenance.receipt_sha256:
        raise SourceLockError(f"{label} digest differs from toolbox provenance")
    if receipt.get("canonical_repository") != provenance.repository:
        raise SourceLockError(
            f"{label} canonical_repository differs from toolbox provenance"
        )
    if receipt.get("canonical_commit") != provenance.sha:
        raise SourceLockError(
            f"{label} canonical_commit differs from toolbox provenance"
        )
    if receipt.get("mirror_repository") != source_lock.pins[0].repository:
        raise SourceLockError(
            f"{label} mirror_repository differs from the locked toolbox repository"
        )
    return receipt


def _read_bounded_regular(path: Path, *, label: str) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY
    for required_flag in ("O_CLOEXEC", "O_NOFOLLOW"):
        if not hasattr(os, required_flag):
            raise SourceLockError(f"platform lacks required flag: {required_flag}")
        flags |= getattr(os, required_flag)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise SourceLockError(f"cannot open {label}: {error}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SourceLockError(f"{label} is not a regular file")
        if before.st_uid != os.geteuid():
            raise SourceLockError(f"{label} is not owned by the current user")
        if before.st_size > MAX_MANAGED_FILE_BYTES:
            raise SourceLockError(f"{label} exceeds the byte limit")
        chunks: list[bytes] = []
        remaining = before.st_size + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        protected_before = (
            before.st_dev,
            before.st_ino,
            before.st_uid,
            stat.S_IMODE(before.st_mode),
            before.st_size,
        )
        protected_after = (
            after.st_dev,
            after.st_ino,
            after.st_uid,
            stat.S_IMODE(after.st_mode),
            after.st_size,
        )
        if protected_after != protected_before or len(payload) != before.st_size:
            raise SourceLockError(f"{label} identity, content size, or policy changed")
        return payload, after
    finally:
        os.close(descriptor)


def _validate_receipt_managed_tree(
    root: Path,
    receipt: dict,
    *,
    label: str,
) -> None:
    files = receipt.get("files")
    if (
        not isinstance(files, list)
        or tuple(
            entry.get("target_path") if isinstance(entry, dict) else None
            for entry in files
        )
        != EXPECTED_TOOLBOX_MANAGED_PATHS
    ):
        raise SourceLockError(f"{label} receipt managed inventory differs")
    seen: set[str] = set()
    for index, entry in enumerate(files):
        if not isinstance(entry, dict):
            raise SourceLockError(f"{label} receipt file {index} is invalid")
        target = entry.get("target_path")
        expected_mode = entry.get("mode")
        expected_sha = entry.get("sha256")
        if (
            not isinstance(target, str)
            or not target
            or Path(target).is_absolute()
            or ".." in Path(target).parts
            or target in seen
        ):
            raise SourceLockError(f"{label} receipt target {index} is invalid")
        if (
            not isinstance(expected_mode, str)
            or re.fullmatch(r"0[0-7]{3}", expected_mode) is None
        ):
            raise SourceLockError(f"{label} receipt mode {index} is invalid")
        if (
            not isinstance(expected_sha, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected_sha) is None
        ):
            raise SourceLockError(f"{label} receipt digest {index} is invalid")
        seen.add(target)
        payload, metadata = _read_bounded_regular(
            root / target,
            label=f"{label} managed path {target}",
        )
        if stat.S_IMODE(metadata.st_mode) != int(expected_mode, 8):
            raise SourceLockError(f"{label} managed path mode differs: {target}")
        if hashlib.sha256(payload).hexdigest() != expected_sha:
            raise SourceLockError(f"{label} managed path digest differs: {target}")


def validate_generated_provenance(
    repo_root: Path,
    source_lock: SourceLock,
    *,
    toolbox_checkout: Path | None = None,
    require_private_receipt: bool = True,
) -> None:
    if require_private_receipt:
        private_receipt = _validate_generated_receipt(
            repo_root / GENERATED_RECEIPT_PATH,
            source_lock,
            label="private generated receipt",
        )
        _validate_receipt_managed_tree(
            repo_root,
            private_receipt,
            label="private generated receipt",
        )
    if toolbox_checkout is not None:
        toolbox_receipt = _validate_generated_receipt(
            toolbox_checkout / GENERATED_RECEIPT_PATH,
            source_lock,
            label="locked toolbox generated receipt",
        )
        _validate_receipt_managed_tree(
            toolbox_checkout,
            toolbox_receipt,
            label="locked toolbox generated receipt",
        )


def _git_environment() -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _git(
    git_path: _TrustedGitExecutable,
    checkout: Path,
    *args: str,
    expected_codes: tuple[int, ...] = (0,),
    discard_stdout: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    command = [
        str(git_path.path),
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-C",
        str(checkout),
        *args,
    ]
    _revalidate_trusted_git(git_path)
    try:
        process = subprocess.Popen(
            command,
            env=_git_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL if discard_stdout else subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as error:
        raise SourceLockError(f"Git command failed to start: {args[0]}") from error
    assert process.stderr is not None
    stdout = bytearray()
    stderr = bytearray()
    streams = [process.stderr]
    if process.stdout is not None:
        streams.append(process.stdout)
    counts = {stream.fileno(): 0 for stream in streams}
    selector = selectors.DefaultSelector()
    for stream in streams:
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ)
    deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
    failure: str | None = None
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = f"Git command timed out: {args[0]}"
                break
            for key, _events in selector.select(min(remaining, 0.1)):
                stream = key.fileobj
                try:
                    chunk = os.read(stream.fileno(), 16 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    continue
                descriptor = stream.fileno()
                counts[descriptor] += len(chunk)
                if counts[descriptor] > MAX_GIT_OUTPUT_BYTES:
                    label = "stdout" if stream is process.stdout else "stderr"
                    failure = f"Git {label} exceeded the byte limit: {args[0]}"
                    break
                if stream is process.stdout:
                    stdout.extend(chunk)
                else:
                    stderr.extend(chunk)
            if failure is not None:
                break
        if failure is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = f"Git command timed out: {args[0]}"
            else:
                try:
                    process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    failure = f"Git command timed out: {args[0]}"
        if failure is not None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
            raise SourceLockError(failure)
    finally:
        selector.close()
        if process.stdout is not None:
            process.stdout.close()
        process.stderr.close()
    completed = subprocess.CompletedProcess(
        command,
        process.returncode,
        bytes(stdout),
        bytes(stderr),
    )
    _revalidate_trusted_git(git_path)
    if completed.returncode not in expected_codes:
        detail = completed.stderr[:MAX_GIT_OUTPUT_BYTES].decode(
            "utf-8", errors="replace"
        )
        raise SourceLockError(
            f"Git command failed for {checkout.name}: {args[0]}: {detail.strip()}"
        )
    return completed


def _single_line(completed: subprocess.CompletedProcess[bytes], *, label: str) -> str:
    assert completed.stdout is not None
    try:
        lines = completed.stdout.decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise SourceLockError(f"{label} output is not ASCII") from error
    if len(lines) != 1:
        raise SourceLockError(f"{label} output must contain exactly one line")
    return lines[0]


def _git_component_binding(
    path: Path,
    *,
    final: bool,
    require_root_owner: bool,
) -> _GitPathBinding:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise SourceLockError(f"cannot inspect Git executable path: {error}") from error
    expected_type = stat.S_IFREG if final else stat.S_IFDIR
    if stat.S_IFMT(metadata.st_mode) != expected_type or stat.S_ISLNK(metadata.st_mode):
        label = "file" if final else "ancestor"
        raise SourceLockError(f"Git executable {label} has an unsafe type")
    owner = metadata.st_uid
    accepted_owners = {0} if require_root_owner else {0, os.geteuid()}
    if owner not in accepted_owners:
        raise SourceLockError("Git executable path has an untrusted owner")
    mode = stat.S_IMODE(metadata.st_mode)
    if mode & 0o022:
        label = "file" if final else "ancestor"
        raise SourceLockError(f"Git executable {label} is group- or world-writable")
    if final and mode & 0o111 == 0:
        raise SourceLockError("Git executable is not executable")
    return _GitPathBinding(
        path=path,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        owner=owner,
        group=metadata.st_gid,
        mode=mode,
        file_type=stat.S_IFMT(metadata.st_mode),
    )


def _revalidate_git_component(
    binding: _GitPathBinding,
    *,
    require_root_owner: bool,
) -> None:
    current = _git_component_binding(
        binding.path,
        final=binding.file_type == stat.S_IFREG,
        require_root_owner=require_root_owner,
    )
    if current != binding:
        raise SourceLockError("Git executable identity or access policy changed")


def _bind_trusted_git_path(
    candidate: Path,
    *,
    require_root_owner: bool,
) -> _TrustedGitExecutable:
    candidate = _absolute_lexical(candidate)
    chain: list[_GitPathBinding] = []
    current = Path(candidate.anchor)
    chain.append(
        _git_component_binding(
            current,
            final=False,
            require_root_owner=require_root_owner,
        )
    )
    for component in candidate.parts[1:-1]:
        current /= component
        chain.append(
            _git_component_binding(
                current,
                final=False,
                require_root_owner=require_root_owner,
            )
        )
    final_binding = _git_component_binding(
        candidate,
        final=True,
        require_root_owner=require_root_owner,
    )
    chain.append(final_binding)
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    for required_flag in ("O_CLOEXEC", "O_NOFOLLOW"):
        if not hasattr(os, required_flag):
            raise SourceLockError(f"platform lacks required flag: {required_flag}")
        flags |= getattr(os, required_flag)
    try:
        descriptor = os.open(candidate, flags)
    except OSError as error:
        raise SourceLockError(f"cannot open Git executable: {error}") from error
    try:
        opened = os.fstat(descriptor)
        opened_identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_uid,
            opened.st_gid,
            stat.S_IMODE(opened.st_mode),
        )
        expected_identity = (
            final_binding.device,
            final_binding.inode,
            final_binding.owner,
            final_binding.group,
            final_binding.mode,
        )
        if not stat.S_ISREG(opened.st_mode) or opened_identity != expected_identity:
            raise SourceLockError("Git executable identity or access policy changed")
        for binding in chain:
            _revalidate_git_component(
                binding,
                require_root_owner=require_root_owner,
            )
    finally:
        os.close(descriptor)
    return _TrustedGitExecutable(
        path=candidate,
        binding=final_binding,
        chain=tuple(chain),
        require_root_owner=require_root_owner,
    )


def _revalidate_trusted_git(git_path: _TrustedGitExecutable) -> None:
    for binding in git_path.chain:
        _revalidate_git_component(
            binding,
            require_root_owner=git_path.require_root_owner,
        )
    current = _git_component_binding(
        git_path.path,
        final=True,
        require_root_owner=git_path.require_root_owner,
    )
    if current != git_path.binding:
        raise SourceLockError("Git executable identity or access policy changed")


def _trusted_git_path() -> _TrustedGitExecutable:
    # On macOS, both PATH-selected Homebrew Git and /usr/bin/git are launch
    # selectors rather than a fixed actual executable: the latter is an xcrun
    # shim whose target can be changed by developer-tool environment variables.
    # Source-lock custody instead declares the root-owned Command Line Tools Git
    # as its fixed trust root and executes this exact non-symlink path under the
    # closed environment returned by _git_environment(). Its complete
    # root-owned, non-writable ancestor chain excludes an untrusted replacement
    # between the final identity check and exec.
    if sys.platform == "darwin":
        return _bind_trusted_git_path(
            MACOS_GIT_PATH,
            require_root_owner=True,
        )
    candidate = shutil.which("git")
    if candidate is None:
        raise SourceLockError("Git executable is unavailable")
    return _bind_trusted_git_path(
        Path(candidate),
        require_root_owner=False,
    )


def _directory_binding(path: Path, *, label: str) -> tuple[int, int, int, int]:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise SourceLockError(f"cannot inspect {label}: {error}") from error
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise SourceLockError(f"{label} must be a non-symlink directory")
    if metadata.st_uid != os.geteuid():
        raise SourceLockError(f"{label} must be owned by the current user")
    mode = stat.S_IMODE(metadata.st_mode)
    if mode & 0o022:
        raise SourceLockError(f"{label} must not be group- or world-writable")
    return (metadata.st_dev, metadata.st_ino, metadata.st_uid, mode)


def _revalidate_directory_binding(
    path: Path,
    binding: tuple[int, int, int, int],
    *,
    label: str,
) -> None:
    if _directory_binding(path, label=label) != binding:
        raise SourceLockError(f"{label} identity or access policy changed")


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _git_path_from_output(checkout: Path, output: str) -> Path:
    candidate = Path(output)
    if not candidate.is_absolute():
        candidate = checkout / candidate
    return _absolute_lexical(candidate)


def _reject_promisor_or_alternate_state(
    git_path: _TrustedGitExecutable,
    checkout: Path,
) -> None:
    partial_config = _git(
        git_path,
        checkout,
        "config",
        "--local",
        "--no-includes",
        "--get-regexp",
        r"^(extensions\.partial[Cc]lone|remote\..*\.(promisor|partial[Cc]lone[Ff]ilter))$",
        expected_codes=(0, 1),
    )
    if partial_config.returncode == 0 or partial_config.stdout:
        raise SourceLockError(f"source checkout is promisor-backed: {checkout.name}")
    sparse_config = _git(
        git_path,
        checkout,
        "config",
        "--local",
        "--no-includes",
        "--get-regexp",
        r"^core\.sparse[Cc]heckout([Cc]one)?$",
        expected_codes=(0, 1),
    )
    if sparse_config.returncode == 0 or sparse_config.stdout:
        raise SourceLockError(f"source checkout uses sparse checkout: {checkout.name}")
    include_config = _git(
        git_path,
        checkout,
        "config",
        "--local",
        "--no-includes",
        "--get-regexp",
        r"^(include\.path|include[Ii]f\..*\.path)$",
        expected_codes=(0, 1),
    )
    if include_config.returncode == 0 or include_config.stdout:
        raise SourceLockError(
            f"source checkout has local config includes: {checkout.name}"
        )
    executable_config = _git(
        git_path,
        checkout,
        "config",
        "--local",
        "--no-includes",
        "--get-regexp",
        r"^(filter\..*\.(clean|smudge|process|required)|diff\..*\.(command|textconv)|core\.(attributesfile|excludesfile|worktree)|extensions\.worktree[Cc]onfig)$",
        expected_codes=(0, 1),
    )
    if executable_config.returncode == 0 or executable_config.stdout:
        raise SourceLockError(
            f"source checkout has external filter or diff config: {checkout.name}"
        )
    replace_refs = _git(
        git_path,
        checkout,
        "for-each-ref",
        "--format=%(refname)",
        "refs/replace",
    )
    if replace_refs.stdout:
        raise SourceLockError(f"source checkout has replace refs: {checkout.name}")
    git_directory = _git_path_from_output(
        checkout,
        _single_line(
            _git(git_path, checkout, "rev-parse", "--absolute-git-dir"),
            label=f"{checkout.name} Git directory",
        ),
    )
    common_directory = _git_path_from_output(
        checkout,
        _single_line(
            _git(git_path, checkout, "rev-parse", "--git-common-dir"),
            label=f"{checkout.name} Git common directory",
        ),
    )
    expected_git_directory = checkout / ".git"
    if (
        git_directory != expected_git_directory
        or common_directory != expected_git_directory
    ):
        raise SourceLockError(
            f"source checkout must use its own ordinary Git directory: {checkout.name}"
        )
    top_level = _git_path_from_output(
        checkout,
        _single_line(
            _git(git_path, checkout, "rev-parse", "--show-toplevel"),
            label=f"{checkout.name} Git worktree",
        ),
    )
    if top_level != checkout:
        raise SourceLockError(
            f"source checkout Git worktree differs from its path: {checkout.name}"
        )
    _directory_binding(git_directory, label=f"{checkout.name} Git directory")
    objects_directory = git_directory / "objects"
    _directory_binding(objects_directory, label=f"{checkout.name} object directory")
    alternates = objects_directory / "info" / "alternates"
    try:
        alternates.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise SourceLockError(
            f"cannot inspect source checkout alternates: {checkout.name}: {error}"
        ) from error
    else:
        raise SourceLockError(f"source checkout uses alternates: {checkout.name}")
    grafts = git_directory / "info" / "grafts"
    try:
        grafts.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise SourceLockError(
            f"cannot inspect source checkout grafts: {checkout.name}: {error}"
        ) from error
    else:
        raise SourceLockError(f"source checkout uses legacy grafts: {checkout.name}")
    pack_directory = objects_directory / "pack"
    try:
        with os.scandir(pack_directory) as entries:
            for index, entry in enumerate(entries, start=1):
                if index > 4096:
                    raise SourceLockError(
                        f"source checkout pack inventory exceeds the limit: {checkout.name}"
                    )
                if entry.name.endswith(".promisor"):
                    raise SourceLockError(
                        f"source checkout has promisor objects: {checkout.name}"
                    )
    except FileNotFoundError:
        pass
    except OSError as error:
        raise SourceLockError(
            f"cannot inspect source checkout packs: {checkout.name}: {error}"
        ) from error


def _validate_tracked_modes_and_index_flags(
    git_path: _TrustedGitExecutable,
    checkout: Path,
) -> None:
    staged = _git(git_path, checkout, "ls-files", "--stage", "-z").stdout
    for flag in ("-v", "-f"):
        output = _git(git_path, checkout, "ls-files", flag, "-z").stdout
        for record in (item for item in output.split(b"\0") if item):
            if len(record) < 3 or record[1:2] != b" ":
                raise SourceLockError(
                    f"source checkout index flag output is invalid: {checkout.name}"
                )
            if record[:1] != b"H":
                raise SourceLockError(
                    f"source checkout has non-default index flags: {checkout.name}"
                )
    for record in (item for item in staged.split(b"\0") if item):
        try:
            header, raw_path = record.split(b"\t", 1)
            raw_mode, object_id, raw_stage = header.split(b" ", 2)
        except ValueError as error:
            raise SourceLockError(
                f"source checkout index inventory is invalid: {checkout.name}"
            ) from error
        if raw_stage != b"0":
            raise SourceLockError(
                f"source checkout has an unmerged index entry: {checkout.name}"
            )
        relative_path = Path(os.fsdecode(raw_path))
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise SourceLockError(
                f"source checkout index path is unsafe: {checkout.name}"
            )
        if raw_mode not in {b"100644", b"100755", b"120000"}:
            raise SourceLockError(
                "source checkout has an unsupported tracked object mode: "
                f"{checkout.name}:{relative_path}:{raw_mode.decode('ascii', errors='replace')}"
            )
        path = checkout / relative_path
        ancestor = path.parent
        while ancestor != checkout:
            _directory_binding(
                ancestor,
                label=f"{checkout.name} tracked path parent",
            )
            ancestor = ancestor.parent
        if raw_mode == b"120000":
            try:
                metadata = path.lstat()
                target = os.fsencode(os.readlink(path))
            except OSError as error:
                raise SourceLockError(
                    f"cannot inspect tracked source symlink in {checkout.name}: {error}"
                ) from error
            if not stat.S_ISLNK(metadata.st_mode):
                raise SourceLockError(
                    f"tracked source symlink kind differs: {checkout.name}:{relative_path}"
                )
            blob = _git(
                git_path,
                checkout,
                "cat-file",
                "blob",
                object_id.decode("ascii"),
            ).stdout
            if target != blob:
                raise SourceLockError(
                    f"tracked source symlink content differs: {checkout.name}:{relative_path}"
                )
            continue
        try:
            metadata = path.lstat()
        except OSError as error:
            raise SourceLockError(
                f"cannot inspect tracked source path in {checkout.name}: {error}"
            ) from error
        expected_mode = 0o755 if raw_mode == b"100755" else 0o644
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != expected_mode
            or metadata.st_uid != os.geteuid()
        ):
            raise SourceLockError(
                "tracked source path type, owner, or physical mode differs from "
                f"the Git declaration: {checkout.name}:{os.fsdecode(raw_path)}"
            )
        payload, _metadata = _read_bounded_regular(
            path,
            label=f"{checkout.name} tracked source path {relative_path}",
        )
        try:
            object_name = object_id.decode("ascii")
        except UnicodeDecodeError as error:
            raise SourceLockError(
                f"source checkout object id is not ASCII: {checkout.name}"
            ) from error
        blob = _git(git_path, checkout, "cat-file", "blob", object_name).stdout
        if payload != blob:
            raise SourceLockError(
                f"tracked source content differs from Git: {checkout.name}:{relative_path}"
            )


def _parse_locked_tree_record(
    record: bytes,
    *,
    label: str,
) -> tuple[bytes, bytes, str, bytes]:
    try:
        header, raw_path = record.split(b"\t", 1)
        raw_mode, raw_kind, raw_object_id = header.split(b" ", 2)
        object_id = raw_object_id.decode("ascii")
    except (UnicodeDecodeError, ValueError) as error:
        raise SourceLockError(f"{label} Git tree record is invalid") from error
    if not SHA_RE.fullmatch(object_id):
        raise SourceLockError(f"{label} Git tree object id is invalid")
    return raw_mode, raw_kind, object_id, raw_path


def load_locked_source_manifest(
    checkout: Path,
    commit: str,
    source: Path,
    *,
    exclude_names: tuple[str, ...] = (),
    exclude_suffixes: tuple[str, ...] = (),
) -> LockedSourceManifest:
    """Load an exact, bounded source inventory from the frozen Git commit."""

    if not SHA_RE.fullmatch(commit):
        raise SourceLockError("locked source commit is invalid")
    if source.is_absolute() or ".." in source.parts:
        raise SourceLockError(f"locked source path is unsafe: {source}")
    checkout = _absolute_lexical(checkout)
    _directory_binding(checkout, label="locked source checkout")
    git_path = _trusted_git_path()
    head = _single_line(
        _git(git_path, checkout, "rev-parse", "--verify", "HEAD^{commit}"),
        label="locked source HEAD",
    )
    if head != commit:
        raise SourceLockError(
            "locked source checkout HEAD differs from the source lock"
        )

    source_text = os.fspath(source)
    if source == Path("."):
        root_kind = "tree"
        root_mode = 0o040000
        root_object_id = _single_line(
            _git(git_path, checkout, "rev-parse", "--verify", f"{commit}^{{tree}}"),
            label="locked source root tree",
        )
        inventory = _git(
            git_path,
            checkout,
            "ls-tree",
            "-r",
            "-t",
            "-z",
            "--full-tree",
            commit,
        ).stdout
        raw_source_prefix = b""
    else:
        literal_pathspec = f":(literal){source_text}"
        root_records = tuple(
            record
            for record in _git(
                git_path,
                checkout,
                "ls-tree",
                "-z",
                commit,
                "--",
                literal_pathspec,
            ).stdout.split(b"\0")
            if record
        )
        if len(root_records) != 1:
            raise SourceLockError(f"locked source is missing or ambiguous: {source}")
        raw_mode, raw_kind, root_object_id, raw_path = _parse_locked_tree_record(
            root_records[0],
            label=f"locked source {source}",
        )
        if raw_path != os.fsencode(source_text):
            raise SourceLockError(f"locked source path differs from Git: {source}")
        if raw_mode == b"040000" and raw_kind == b"tree":
            root_kind = "tree"
            root_mode = 0o040000
            inventory = _git(
                git_path,
                checkout,
                "ls-tree",
                "-r",
                "-t",
                "-z",
                "--full-tree",
                commit,
                "--",
                literal_pathspec,
            ).stdout
            raw_source_prefix = raw_path + b"/"
        elif raw_mode in {b"100644", b"100755"} and raw_kind == b"blob":
            root_kind = "file"
            root_mode = 0o755 if raw_mode == b"100755" else 0o644
            inventory = b""
            raw_source_prefix = b""
        else:
            raise SourceLockError(
                f"locked source has unsupported Git mode or type: {source}"
            )

    ignored = frozenset(exclude_names)
    entries: list[LockedSourceEntry] = []
    seen: set[Path] = set()
    for index, record in enumerate(
        (item for item in inventory.split(b"\0") if item),
        start=1,
    ):
        if index > MAX_LOCKED_SOURCE_ENTRIES:
            raise SourceLockError("locked source inventory exceeds its entry limit")
        raw_mode, raw_kind, object_id, raw_path = _parse_locked_tree_record(
            record,
            label=f"locked source {source}",
        )
        if source != Path(".") and raw_path == os.fsencode(source_text):
            if raw_mode != b"040000" or raw_kind != b"tree":
                raise SourceLockError(f"locked source root entry is invalid: {source}")
            continue
        if raw_source_prefix and os.fsencode(source_text).startswith(raw_path + b"/"):
            if raw_mode != b"040000" or raw_kind != b"tree":
                raise SourceLockError(
                    f"locked source ancestor entry is invalid: {source}"
                )
            continue
        if raw_source_prefix and not raw_path.startswith(raw_source_prefix):
            raise SourceLockError(f"locked source tree escapes its root: {source}")
        raw_relative = (
            raw_path[len(raw_source_prefix) :] if raw_source_prefix else raw_path
        )
        relative = Path(os.fsdecode(raw_relative))
        if relative == Path(".") or relative.is_absolute() or ".." in relative.parts:
            raise SourceLockError(f"locked source inventory path is unsafe: {source}")
        if any(
            part in ignored or any(part.endswith(suffix) for suffix in exclude_suffixes)
            for part in relative.parts
        ):
            continue
        if relative in seen:
            raise SourceLockError(f"locked source inventory is ambiguous: {source}")
        seen.add(relative)
        if raw_mode == b"040000" and raw_kind == b"tree":
            kind = "directory"
            mode = 0o755
        elif raw_mode in {b"100644", b"100755"} and raw_kind == b"blob":
            kind = "file"
            mode = 0o755 if raw_mode == b"100755" else 0o644
        else:
            raise SourceLockError(
                "locked source contains an unsupported tracked object: "
                f"{source / relative}"
            )
        entries.append(
            LockedSourceEntry(
                relative=relative,
                kind=kind,
                mode=mode,
                object_id=object_id,
            )
        )
    return LockedSourceManifest(
        root_kind=root_kind,
        root_mode=root_mode,
        root_object_id=root_object_id,
        entries=tuple(entries),
    )


def read_locked_source_blob(checkout: Path, object_id: str) -> bytes:
    if not SHA_RE.fullmatch(object_id):
        raise SourceLockError("locked source blob object id is invalid")
    checkout = _absolute_lexical(checkout)
    return _git(
        _trusted_git_path(),
        checkout,
        "cat-file",
        "blob",
        object_id,
    ).stdout


def _verify_checkout(
    git_path: _TrustedGitExecutable,
    checkout: Path,
    *,
    name: str,
    repository: str,
    expected: SourcePin | None,
) -> SourcePin:
    checkout_binding = _directory_binding(checkout, label=f"{name} checkout")
    head = _single_line(
        _git(git_path, checkout, "rev-parse", "--verify", "HEAD^{commit}"),
        label=f"{name} HEAD",
    )
    tree = _single_line(
        _git(git_path, checkout, "rev-parse", "--verify", "HEAD^{tree}"),
        label=f"{name} tree",
    )
    if expected is not None and (head != expected.sha or tree != expected.tree):
        raise SourceLockError(f"source checkout identity differs from lock: {name}")
    shallow = _single_line(
        _git(git_path, checkout, "rev-parse", "--is-shallow-repository"),
        label=f"{name} shallow state",
    )
    if shallow != "false":
        raise SourceLockError(f"source checkout is shallow: {name}")
    bare = _single_line(
        _git(git_path, checkout, "rev-parse", "--is-bare-repository"),
        label=f"{name} bare state",
    )
    if bare != "false":
        raise SourceLockError(f"source checkout is bare: {name}")
    _reject_promisor_or_alternate_state(git_path, checkout)
    _validate_tracked_modes_and_index_flags(git_path, checkout)
    attached = _git(
        git_path,
        checkout,
        "symbolic-ref",
        "-q",
        "HEAD",
        expected_codes=(0, 1),
    )
    if attached.returncode == 0:
        raise SourceLockError(f"source checkout is not detached: {name}")
    if _git(
        git_path,
        checkout,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--ignored=matching",
        "--ignore-submodules=none",
    ).stdout:
        raise SourceLockError(f"source checkout is dirty or untracked: {name}")
    _git(
        git_path,
        checkout,
        "rev-list",
        "--objects",
        "--missing=error",
        "--quiet",
        head,
        discard_stdout=True,
    )
    _git(
        git_path,
        checkout,
        "fsck",
        "--full",
        "--strict",
        "--no-dangling",
        "--no-progress",
        discard_stdout=True,
    )
    _revalidate_directory_binding(
        checkout,
        checkout_binding,
        label=f"{name} checkout",
    )
    return SourcePin(name=name, repository=repository, sha=head, tree=tree)


def _verify_source_root(
    source_root: Path,
    source_lock: SourceLock,
    *,
    refresh_non_toolbox: bool,
) -> tuple[tuple[SourcePin, ...], tuple[int, int, int, int]]:
    source_root = _absolute_lexical(source_root)
    root_binding = _directory_binding(source_root, label="source root")
    git_path = _trusted_git_path()
    pins: list[SourcePin] = []
    for index, pin in enumerate(source_lock.pins):
        checkout = source_root / pin.name
        pins.append(
            _verify_checkout(
                git_path,
                checkout,
                name=pin.name,
                repository=pin.repository,
                expected=pin if index == 0 or not refresh_non_toolbox else None,
            )
        )
    _revalidate_directory_binding(source_root, root_binding, label="source root")
    return tuple(pins), root_binding


def verify_checkouts(
    source_root: Path,
    source_lock: SourceLock,
    *,
    repo_root: Path | None = None,
) -> None:
    source_root = _absolute_lexical(source_root)
    _verify_source_root(source_root, source_lock, refresh_non_toolbox=False)
    if repo_root is not None:
        validate_generated_provenance(
            repo_root,
            source_lock,
            toolbox_checkout=source_root / source_lock.pins[0].name,
        )


def _source_lock_bytes(
    pins: tuple[SourcePin, ...],
    provenance: ToolboxGeneratedProvenance,
) -> bytes:
    payload = {
        "version": 1,
        "sources": [
            {
                "name": pin.name,
                "repository": pin.repository,
                "sha": pin.sha,
                "tree": pin.tree,
            }
            for pin in pins
        ],
        "toolbox_generated_provenance": {
            "repository": provenance.repository,
            "sha": provenance.sha,
            "receipt_sha256": provenance.receipt_sha256,
        },
    }
    return json.dumps(payload, indent=2, sort_keys=False).encode("utf-8") + b"\n"


def _replace_source_lock(repo_root: Path, payload: bytes) -> None:
    repo_root = _absolute_lexical(repo_root)
    root_binding = _directory_binding(repo_root, label="repository root")
    target = repo_root / LOCK_PATH
    try:
        target_metadata = target.lstat()
    except OSError as error:
        raise SourceLockError(
            f"cannot inspect source lock before update: {error}"
        ) from error
    if not stat.S_ISREG(target_metadata.st_mode) or stat.S_ISLNK(
        target_metadata.st_mode
    ):
        raise SourceLockError("source lock must remain a regular non-symlink file")
    target_binding = (
        target_metadata.st_dev,
        target_metadata.st_ino,
        target_metadata.st_uid,
        stat.S_IMODE(target_metadata.st_mode),
    )
    temporary = (
        repo_root / f".{LOCK_PATH.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    for required_flag in ("O_CLOEXEC", "O_NOFOLLOW"):
        if not hasattr(os, required_flag):
            raise SourceLockError(f"platform lacks required flag: {required_flag}")
        flags |= getattr(os, required_flag)
    descriptor = -1
    try:
        descriptor = os.open(temporary, flags, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise SourceLockError("cannot write refreshed source lock")
            view = view[written:]
        os.fchmod(descriptor, target_binding[3])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        _revalidate_directory_binding(repo_root, root_binding, label="repository root")
        current = target.lstat()
        current_binding = (
            current.st_dev,
            current.st_ino,
            current.st_uid,
            stat.S_IMODE(current.st_mode),
        )
        if current_binding != target_binding or not stat.S_ISREG(current.st_mode):
            raise SourceLockError("source lock identity or access policy changed")
        os.replace(temporary, target)
    except OSError as error:
        raise SourceLockError(
            f"cannot atomically update source lock: {error}"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def refresh_non_toolbox_pins(
    repo_root: Path,
    source_root: Path,
    source_lock: SourceLock,
) -> SourceLock:
    source_root = _absolute_lexical(source_root)
    pins, _root_binding = _verify_source_root(
        source_root,
        source_lock,
        refresh_non_toolbox=True,
    )
    validate_generated_provenance(
        repo_root,
        source_lock,
        toolbox_checkout=source_root / source_lock.pins[0].name,
    )
    _replace_source_lock(
        repo_root,
        _source_lock_bytes(pins, source_lock.toolbox_generated_provenance),
    )
    refreshed = load_source_lock(repo_root)
    validate_base_release_binding(repo_root, refreshed)
    validate_generated_provenance(
        repo_root,
        refreshed,
        toolbox_checkout=source_root / refreshed.pins[0].name,
    )
    verify_checkouts(source_root, refreshed, repo_root=repo_root)
    return refreshed


def emit_github_outputs(source_lock: SourceLock) -> None:
    for pin in source_lock.pins:
        key = pin.name.replace("-", "_")
        print(f"{key}_sha={pin.sha}")
        print(f"{key}_tree={pin.tree}")
    print(f"source_lock_sha256={source_lock.digest}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate private overlay source pins."
    )
    parser.add_argument(
        "command",
        choices=(
            "emit-github-outputs",
            "refresh-non-toolbox-pins",
            "verify-checkouts",
        ),
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--source-root", default=".source")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = _absolute_lexical(Path(args.repo_root))
    source_root = _absolute_lexical(Path(args.source_root))
    try:
        source_lock = load_source_lock(repo_root)
        validate_base_release_binding(repo_root, source_lock)
        validate_generated_provenance(repo_root, source_lock)
        if args.command == "emit-github-outputs":
            emit_github_outputs(source_lock)
        elif args.command == "refresh-non-toolbox-pins":
            refreshed = refresh_non_toolbox_pins(repo_root, source_root, source_lock)
            emit_github_outputs(refreshed)
        else:
            verify_checkouts(source_root, source_lock, repo_root=repo_root)
    except SourceLockError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
