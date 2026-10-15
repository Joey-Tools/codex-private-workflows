#!/usr/bin/env python3
"""Transport exact generated tree content for scheduled sync tests.

The integrity boundary protects the candidate patch bytes and resulting Git tree.
Same-UID races in the workspace and candidate directory are not excluded.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import re
import selectors
import subprocess
import sys
import time


SCOPES = (
    ".github",
    "scripts",
    "tests",
    "schema",
    "personal_codex",
    ".agents",
    "generated-sync-source-lock.json",
    "private-overlay-source-lock.json",
)
PATCH_NAME = "candidate.patch"
MAX_PATCH_BYTES = 32 * 1024 * 1024
COMMAND_TIMEOUT_SECONDS = 60
SHA_PATTERN = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class CandidateError(Exception):
    """An invalid candidate or Git operation."""


def _git_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    if extra:
        environment.update(extra)
    return environment


def _git_argv(repo: pathlib.Path, *args: str) -> list[str]:
    return [
        "git",
        "--no-replace-objects",
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        "core.fsmonitor=false",
        "-C",
        str(repo),
        *args,
    ]


def _run_git(
    repo: pathlib.Path,
    *args: str,
    env: dict[str, str] | None = None,
    stdout: int | None = subprocess.PIPE,
) -> bytes:
    try:
        completed = subprocess.run(
            _git_argv(repo, *args),
            check=False,
            stdout=stdout,
            stderr=subprocess.PIPE,
            env=_git_env(env),
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CandidateError(f"git {' '.join(args[:2])} failed: {error}") from error
    if completed.returncode != 0:
        detail = (completed.stderr or b"").decode("utf-8", errors="replace")[-2000:]
        raise CandidateError(f"git {' '.join(args[:2])} failed: {detail.strip()}")
    return completed.stdout or b""


def _run_git_bounded_output(repo: pathlib.Path, *args: str, limit: int) -> bytes:
    """Capture stdout without ever retaining more than limit bytes."""
    try:
        process = subprocess.Popen(
            _git_argv(repo, *args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_git_env(),
        )
    except OSError as error:
        raise CandidateError(f"git {' '.join(args[:2])} failed: {error}") from error
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    output = bytearray()
    error_tail = bytearray()
    deadline = time.monotonic() + COMMAND_TIMEOUT_SECONDS
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CandidateError(f"git {' '.join(args[:2])} timed out")
            for key, _ in selector.select(min(remaining, 1.0)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                if key.data == "stdout":
                    if len(output) + len(chunk) > limit:
                        raise CandidateError(
                            f"candidate patch exceeds the {limit}-byte limit"
                        )
                    output.extend(chunk)
                else:
                    error_tail.extend(chunk)
                    if len(error_tail) > 8192:
                        del error_tail[:-8192]
        return_code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CandidateError(f"git {' '.join(args[:2])} failed: {error}") from error
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()
    if return_code != 0:
        detail = error_tail.decode("utf-8", errors="replace")
        raise CandidateError(f"git {' '.join(args[:2])} failed: {detail.strip()}")
    return bytes(output)


def _resolve_repo(repo_arg: str) -> pathlib.Path:
    repo = pathlib.Path(repo_arg).resolve(strict=True)
    top = _run_git(repo, "rev-parse", "--show-toplevel").decode().strip()
    return pathlib.Path(top).resolve(strict=True)


def _validate_sha(value: str, label: str) -> str:
    if not SHA_PATTERN.fullmatch(value):
        raise CandidateError(f"{label} must be a full lowercase Git SHA")
    return value


def _head(repo: pathlib.Path) -> str:
    return _run_git(repo, "rev-parse", "HEAD").decode().strip()


def _changed_tracked_paths(repo: pathlib.Path, base: str) -> list[str]:
    output = _run_git(repo, "diff", "--name-only", "-z", base, "--")
    return [os.fsdecode(item) for item in output.split(b"\0") if item]


def _in_scope(path: str) -> bool:
    return any(path == scope or path.startswith(scope + "/") for scope in SCOPES)


def _unlink_owned_index(
    path: pathlib.Path, identity: tuple[int, int] | None
) -> bool:
    if identity is None:
        return False
    try:
        current = path.lstat()
    except FileNotFoundError:
        return False
    if (
        not path.is_file()
        or path.is_symlink()
        or (current.st_dev, current.st_ino) != identity
    ):
        return False
    path.unlink()
    return True


def _write_candidate(repo: pathlib.Path, base: str, output_dir: pathlib.Path) -> tuple[str, str]:
    output_dir = output_dir.absolute()
    try:
        output_dir.relative_to(repo)
    except ValueError:
        pass
    else:
        raise CandidateError("candidate output directory must be outside the repository")
    try:
        output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError as error:
        raise CandidateError("candidate output directory must be fresh") from error

    temp_index = output_dir / "index"
    index_env = {"GIT_INDEX_FILE": str(temp_index)}
    owned_index_identity: tuple[int, int] | None = None
    try:
        output_dir.chmod(0o700)
        _run_git(repo, "read-tree", base, env=index_env)
        _run_git(repo, "add", "-A", "--", *SCOPES, env=index_env)
        tree = _run_git(repo, "write-tree", env=index_env).decode().strip()
        index_stat = temp_index.lstat()
        if not temp_index.is_file() or temp_index.is_symlink():
            raise CandidateError("temporary index is not a regular file")
        owned_index_identity = (index_stat.st_dev, index_stat.st_ino)
        patch = _run_git_bounded_output(
            repo,
            "diff",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-textconv",
            base,
            tree,
            "--",
            *SCOPES,
            limit=MAX_PATCH_BYTES,
        )
        patch_path = output_dir / PATCH_NAME
        patch_path.write_bytes(patch)
        patch_path.chmod(0o600)
        patch_sha = hashlib.sha256(patch).hexdigest()
        _unlink_owned_index(temp_index, owned_index_identity)
        return tree, patch_sha
    except Exception as error:
        raise CandidateError(
            f"{error}; candidate directory retained at {output_dir}"
        ) from error


def prepare(repo_arg: str, expected_base: str, output_arg: str) -> tuple[str, str, str]:
    expected_base = _validate_sha(expected_base, "expected base")
    repo = _resolve_repo(repo_arg)
    actual_base = _head(repo)
    if actual_base != expected_base:
        raise CandidateError(f"HEAD mismatch: expected {expected_base}, found {actual_base}")
    outside = [path for path in _changed_tracked_paths(repo, expected_base) if not _in_scope(path)]
    if outside:
        sample = ", ".join(outside[:10])
        raise CandidateError(f"tracked changes outside generated-sync scopes: {sample}")
    tree, patch_sha = _write_candidate(repo, expected_base, pathlib.Path(output_arg))
    return expected_base, tree, patch_sha


def _read_patch(candidate_dir: pathlib.Path, expected_patch_sha: str) -> bytes:
    candidate_dir = candidate_dir.resolve(strict=True)
    if not candidate_dir.is_dir():
        raise CandidateError("candidate directory is not a directory")
    patch_path = candidate_dir / PATCH_NAME
    if patch_path.is_symlink() or not patch_path.is_file():
        raise CandidateError("candidate.patch is missing or is not a regular file")
    with patch_path.open("rb") as patch_file:
        patch = patch_file.read(MAX_PATCH_BYTES + 1)
    if len(patch) > MAX_PATCH_BYTES:
        raise CandidateError(f"candidate patch exceeds the {MAX_PATCH_BYTES}-byte limit")
    actual_sha = hashlib.sha256(patch).hexdigest()
    if actual_sha != expected_patch_sha:
        raise CandidateError("candidate patch SHA-256 mismatch")
    return patch


def apply_candidate(
    repo_arg: str,
    candidate_arg: str,
    expected_base: str,
    expected_tree: str,
    expected_patch_sha: str,
) -> None:
    expected_base = _validate_sha(expected_base, "expected base")
    expected_tree = _validate_sha(expected_tree, "expected tree")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_patch_sha):
        raise CandidateError("patch SHA-256 must be 64 lowercase hexadecimal characters")
    repo = _resolve_repo(repo_arg)
    actual_base = _head(repo)
    if actual_base != expected_base:
        raise CandidateError(f"HEAD mismatch: expected {expected_base}, found {actual_base}")
    status = _run_git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise CandidateError("recipient checkout is not clean")
    patch = _read_patch(pathlib.Path(candidate_arg), expected_patch_sha)
    if patch:
        try:
            completed = subprocess.run(
                _git_argv(repo, "apply", "--index", "--binary"),
                input=patch,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_git_env(),
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CandidateError(f"git apply failed: {error}") from error
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace")[-2000:]
            raise CandidateError(f"git apply failed: {detail.strip()}")
    actual_tree = _run_git(repo, "write-tree").decode().strip()
    if actual_tree != expected_tree:
        raise CandidateError(f"tree mismatch: expected {expected_tree}, found {actual_tree}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--repo", required=True)
    prepare_parser.add_argument("--expected-base", required=True)
    prepare_parser.add_argument("--output-dir", required=True)
    apply_parser = commands.add_parser("apply")
    apply_parser.add_argument("--repo", required=True)
    apply_parser.add_argument("--candidate-dir", required=True)
    apply_parser.add_argument("--expected-base", required=True)
    apply_parser.add_argument("--expected-tree", required=True)
    apply_parser.add_argument("--patch-sha256", required=True)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "prepare":
            base, tree, patch_sha = prepare(
                arguments.repo, arguments.expected_base, arguments.output_dir
            )
            print(f"base_sha={base}")
            print(f"tree_sha={tree}")
            print(f"patch_sha256={patch_sha}")
        else:
            apply_candidate(
                arguments.repo,
                arguments.candidate_dir,
                arguments.expected_base,
                arguments.expected_tree,
                arguments.patch_sha256,
            )
    except CandidateError as error:
        print(f"scheduled sync candidate: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
