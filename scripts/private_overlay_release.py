#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen
import re


API_ROOT = "https://api.github.com"
UPLOAD_ROOT = "https://uploads.github.com"
RELEASE_TAG_PREFIX = "personal-codex-"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_VERIFICATION_CLEANUP_ENTRIES = 20_000
MAX_VERIFICATION_CLEANUP_DEPTH = 70


class ReleaseError(RuntimeError):
    pass


def _github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ReleaseError("GITHUB_TOKEN is required")
    return token


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    token: str | None = None,
) -> Any:
    data = None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token or _github_token()}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=30) as response:
        body = response.read()
        if not body:
            return {}
        return json.loads(body.decode("utf-8"))


def parse_timestamp(raw: str) -> dt.datetime:
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def recent_successful_runs(
    *,
    repo: str,
    workflow: str,
    current_run_id: str,
    now: dt.datetime,
    cooldown_seconds: int,
    event: str,
) -> list[dict[str, Any]]:
    data = request_json(
        f"{API_ROOT}/repos/{repo}/actions/workflows/{workflow}/runs"
        "?status=success&per_page=20"
    )
    runs = data.get("workflow_runs")
    if not isinstance(runs, list):
        raise ReleaseError("workflow runs API returned an unexpected payload")
    recent: list[dict[str, Any]] = []
    cutoff = now - dt.timedelta(seconds=cooldown_seconds)
    for run in runs:
        if not isinstance(run, dict):
            continue
        if str(run.get("id")) == str(current_run_id):
            continue
        created_at_raw = run.get("created_at")
        if not isinstance(created_at_raw, str):
            continue
        created_at = parse_timestamp(created_at_raw)
        if created_at < cutoff:
            continue
        if event == "schedule" and run.get("event") == "schedule":
            continue
        recent.append(run)
    return recent


def _release_asset_names(release: dict[str, Any]) -> set[str]:
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        return set()
    return {
        asset["name"]
        for asset in assets
        if isinstance(asset, dict)
        and "name" in asset
        and asset.get("state") == "uploaded"
    }


def _incomplete_release_assets(
    release: dict[str, Any],
    expected_asset_names: set[str],
) -> list[dict[str, Any]]:
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        return []
    return [
        asset
        for asset in assets
        if isinstance(asset, dict)
        and asset.get("name") in expected_asset_names
        and asset.get("state") != "uploaded"
    ]


def _release_has_complete_assets(release: dict[str, Any]) -> bool:
    sha = release.get("target_commitish")
    tag_name = release.get("tag_name", "")
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        return False
    if not isinstance(tag_name, str) or not tag_name.startswith(RELEASE_TAG_PREFIX):
        return False
    expected_asset_names = {
        f"personal-codex-{sha}.tar.gz",
        f"personal-codex-{sha}.sha256",
    }
    return expected_asset_names <= _release_asset_names(release)


def _release_source_event(release: dict[str, Any]) -> str | None:
    body = release.get("body")
    if not isinstance(body, str):
        return None
    for line in body.splitlines():
        if line.startswith("source_event="):
            return line.partition("=")[2].strip() or None
    return None


def _release_body(sha: str, source_event: str) -> str:
    return f"Private Codex overlay release for {sha}.\n\nsource_event={source_event}"


def recent_complete_releases(
    *,
    repo: str,
    now: dt.datetime,
    cooldown_seconds: int,
    event: str,
) -> list[dict[str, Any]]:
    cutoff = now - dt.timedelta(seconds=cooldown_seconds)
    recent: list[dict[str, Any]] = []
    for release in iter_releases(repo):
        if not isinstance(release, dict) or release.get("draft", False):
            continue
        published_at_raw = release.get("published_at") or release.get("created_at")
        if not isinstance(published_at_raw, str):
            continue
        if parse_timestamp(published_at_raw) < cutoff:
            continue
        if event == "schedule" and _release_source_event(release) == "schedule":
            continue
        if _release_has_complete_assets(release):
            recent.append(release)
    return recent


def should_run(
    *,
    repo: str,
    workflow: str,
    current_run_id: str,
    event: str,
    force: bool,
    cooldown_seconds: int,
    now: dt.datetime | None = None,
) -> tuple[bool, str]:
    if force:
        return True, "force=true"
    now = now or dt.datetime.now(dt.timezone.utc)
    recent = recent_complete_releases(
        repo=repo,
        now=now,
        cooldown_seconds=cooldown_seconds,
        event=event,
    )
    if not recent:
        return True, "no recent complete release in cooldown window"
    latest = recent[0]
    return (
        False,
        "cooldown active after complete release "
        f"{latest.get('tag_name', 'unknown')} at "
        f"{latest.get('published_at') or latest.get('created_at', 'unknown')}",
    )


def _load_sync_module(repo_root: Path):
    script_path = repo_root / "scripts" / "codex_personal_sync.py"
    spec = importlib.util.spec_from_file_location("codex_personal_sync", script_path)
    if spec is None or spec.loader is None:
        raise ReleaseError(f"failed to load {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _open_verification_workspace(
    workspace: Path,
) -> tuple[int, int, tuple[int, int]]:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    parent_fd = -1
    workspace_fd = -1
    try:
        parent_fd = os.open(workspace.parent, flags)
        workspace_fd = os.open(workspace.name, flags, dir_fd=parent_fd)
        opened_metadata = os.fstat(workspace_fd)
        path_metadata = os.stat(
            workspace.name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
    except OSError as error:
        if workspace_fd >= 0:
            os.close(workspace_fd)
        if parent_fd >= 0:
            os.close(parent_fd)
        raise ReleaseError(
            f"failed to bind verification workspace {workspace}: {error}"
        ) from error
    identity = (opened_metadata.st_dev, opened_metadata.st_ino)
    if (
        not stat.S_ISDIR(opened_metadata.st_mode)
        or not stat.S_ISDIR(path_metadata.st_mode)
        or (path_metadata.st_dev, path_metadata.st_ino) != identity
    ):
        os.close(workspace_fd)
        os.close(parent_fd)
        raise ReleaseError(
            f"verification workspace changed while binding: {workspace}"
        )
    return parent_fd, workspace_fd, identity


def _rename_noreplace_at(
    parent_fd: int,
    source_name: str,
    destination_name: str,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    source = os.fsencode(source_name)
    destination = os.fsencode(destination_name)
    if sys.platform == "darwin":
        rename_function = libc.renameatx_np
        rename_function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename_function.restype = ctypes.c_int
        result = rename_function(
            parent_fd,
            source,
            parent_fd,
            destination,
            0x00000004,
        )
    elif sys.platform.startswith("linux"):
        try:
            rename_function = libc.renameat2
        except AttributeError as error:
            raise ReleaseError(
                "renameat2 is required for safe verification cleanup"
            ) from error
        rename_function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename_function.restype = ctypes.c_int
        result = rename_function(
            parent_fd,
            source,
            parent_fd,
            destination,
            0x00000001,
        )
    else:
        raise ReleaseError(
            f"safe verification cleanup is unsupported on {sys.platform}"
        )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(
            error_number,
            os.strerror(error_number),
            f"{source_name} -> {destination_name}",
        )


def _cleanup_isolation_names():
    for _attempt in range(128):
        yield (
            ".codex-private-overlay-cleanup-"
            f"{os.getpid()}-{os.urandom(8).hex()}"
        )
    raise ReleaseError("failed to allocate verification cleanup name")


def _isolate_verification_workspace_entry(
    parent_fd: int,
    name: str,
    expected: os.stat_result,
) -> str:
    # This cleanup defends against accidental or untrusted path replacement,
    # including symlink swaps, before the no-replace isolation step. A
    # malicious process running as the same uid can observe and rewrite the
    # user's private temporary directory after isolation; that process already
    # has equivalent authority over the checkout and release artifacts and is
    # outside this helper's threat model.
    isolated_name: str | None = None
    for candidate in _cleanup_isolation_names():
        try:
            _rename_noreplace_at(parent_fd, name, candidate)
        except FileExistsError:
            continue
        isolated_name = candidate
        break
    assert isolated_name is not None
    current = os.stat(
        isolated_name,
        dir_fd=parent_fd,
        follow_symlinks=False,
    )
    if (
        (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino)
        or stat.S_IFMT(current.st_mode) != stat.S_IFMT(expected.st_mode)
    ):
        raise ReleaseError(
            "verification workspace entry changed during isolation; "
            f"preserved as {isolated_name}"
        )
    return isolated_name


def _remove_verification_workspace_entry(
    parent_fd: int,
    name: str,
    budget: list[int],
    *,
    depth: int,
) -> None:
    if depth > MAX_VERIFICATION_CLEANUP_DEPTH:
        raise ReleaseError("verification workspace cleanup exceeds depth limit")
    if budget[0] <= 0:
        raise ReleaseError("verification workspace cleanup exceeds entry limit")
    budget[0] -= 1
    metadata = os.stat(
        name,
        dir_fd=parent_fd,
        follow_symlinks=False,
    )
    isolated_name = _isolate_verification_workspace_entry(
        parent_fd,
        name,
        metadata,
    )
    if stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        os.unlink(isolated_name, dir_fd=parent_fd)
        return
    if not stat.S_ISDIR(metadata.st_mode):
        raise ReleaseError(
            "verification workspace contains an unsupported entry; "
            f"preserved as {isolated_name}"
        )
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    child_fd = os.open(isolated_name, flags, dir_fd=parent_fd)
    try:
        opened_metadata = os.fstat(child_fd)
        expected_identity = (metadata.st_dev, metadata.st_ino)
        if (
            not stat.S_ISDIR(opened_metadata.st_mode)
            or (opened_metadata.st_dev, opened_metadata.st_ino)
            != expected_identity
        ):
            raise ReleaseError(
                "verification workspace entry changed after isolation; "
                f"preserved as {isolated_name}"
            )
        for child_name in os.listdir(child_fd):
            _remove_verification_workspace_entry(
                child_fd,
                child_name,
                budget,
                depth=depth + 1,
            )
        if os.listdir(child_fd):
            raise ReleaseError(
                "verification workspace directory gained an entry; "
                f"preserved as {isolated_name}"
            )
        path_metadata = os.stat(
            isolated_name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISDIR(path_metadata.st_mode)
            or (path_metadata.st_dev, path_metadata.st_ino)
            != expected_identity
        ):
            raise ReleaseError(
                "verification workspace entry changed before deletion; "
                f"preserved as {isolated_name}"
            )
        os.rmdir(isolated_name, dir_fd=parent_fd)
    finally:
        active_error = sys.exc_info()[0] is not None
        try:
            os.close(child_fd)
        except OSError:
            if not active_error:
                raise


def _cleanup_verification_workspace(
    workspace: Path,
    parent_fd: int,
    workspace_fd: int,
    expected_identity: tuple[int, int],
) -> None:
    try:
        opened_metadata = os.fstat(workspace_fd)
        path_metadata = os.stat(
            workspace.name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
    except OSError as error:
        raise ReleaseError(
            f"failed to inspect verification workspace {workspace}: {error}"
        ) from error
    if (
        not stat.S_ISDIR(opened_metadata.st_mode)
        or (opened_metadata.st_dev, opened_metadata.st_ino) != expected_identity
        or not stat.S_ISDIR(path_metadata.st_mode)
        or (path_metadata.st_dev, path_metadata.st_ino) != expected_identity
    ):
        raise ReleaseError(
            f"verification workspace changed; refusing cleanup: {workspace}"
        )
    try:
        isolated_name = _isolate_verification_workspace_entry(
            parent_fd,
            workspace.name,
            path_metadata,
        )
        budget = [MAX_VERIFICATION_CLEANUP_ENTRIES]
        for name in os.listdir(workspace_fd):
            _remove_verification_workspace_entry(
                workspace_fd,
                name,
                budget,
                depth=0,
            )
        if os.listdir(workspace_fd):
            raise ReleaseError(
                "verification workspace gained an entry; "
                f"preserved as {isolated_name}"
            )
        opened_metadata = os.fstat(workspace_fd)
        path_metadata = os.stat(
            isolated_name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        if (
            (opened_metadata.st_dev, opened_metadata.st_ino) != expected_identity
            or not stat.S_ISDIR(path_metadata.st_mode)
            or (path_metadata.st_dev, path_metadata.st_ino) != expected_identity
        ):
            raise ReleaseError(
                "verification workspace changed before deletion; "
                f"preserved as {isolated_name}"
            )
        os.rmdir(isolated_name, dir_fd=parent_fd)
    except OSError as error:
        raise ReleaseError(
            f"failed to clean verification workspace {workspace}: {error}"
        ) from error


def verify_package(repo_root: Path, sha: str, dist: Path) -> None:
    module = _load_sync_module(repo_root)
    archive_path = dist / f"personal-codex-{sha}.tar.gz"
    checksum_path = dist / f"personal-codex-{sha}.sha256"
    workspace = Path(tempfile.mkdtemp(prefix="codex-private-overlay-verify."))
    created_metadata = os.lstat(workspace)
    try:
        parent_fd, workspace_fd, workspace_identity = (
            _open_verification_workspace(workspace)
        )
    except BaseException:
        try:
            current = os.lstat(workspace)
            if (
                stat.S_ISDIR(current.st_mode)
                and (current.st_dev, current.st_ino)
                == (created_metadata.st_dev, created_metadata.st_ino)
            ):
                os.rmdir(workspace)
        except OSError:
            pass
        raise
    try:
        release_root, _release_expectation = module.verify_and_extract_archive(
            archive_path,
            checksum_path,
            workspace / "extract",
        )
        entries = module.validate_release_tree(release_root)
        targets = {entry.target.as_posix() for entry in entries}
        manifest = json.loads(
            (release_root / "personal_codex" / "sync-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        if not entries or any(entry.owner != "private" for entry in entries):
            raise ReleaseError("release manifest must contain only private-owned entries")
        if manifest.get("base_release", {}).get("repo") != "Joey-Tools/codex-toolbox":
            raise ReleaseError("release manifest must declare the public base release repo")
        if "bin/codex-personal-sync" in targets:
            raise ReleaseError("private overlay must not publish the public sync runner")
    finally:
        active_error = sys.exc_info()[0] is not None
        cleanup_error: BaseException | None = None
        try:
            _cleanup_verification_workspace(
                workspace,
                parent_fd,
                workspace_fd,
                workspace_identity,
            )
        except BaseException as error:
            cleanup_error = error
        finally:
            for descriptor, label in (
                (workspace_fd, "workspace"),
                (parent_fd, "workspace parent"),
            ):
                try:
                    os.close(descriptor)
                except OSError as error:
                    if cleanup_error is None:
                        cleanup_error = ReleaseError(
                            f"failed to close verification {label} "
                            f"{workspace}: {error}"
                        )
        if cleanup_error is not None:
            if isinstance(cleanup_error, ReleaseError):
                normalized_cleanup_error = cleanup_error
            else:
                normalized_cleanup_error = ReleaseError(
                    "verification workspace cleanup failed: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
            if active_error:
                print(
                    f"warning: {normalized_cleanup_error}",
                    file=sys.stderr,
                )
            elif normalized_cleanup_error is cleanup_error:
                raise normalized_cleanup_error
            else:
                raise normalized_cleanup_error from cleanup_error


def iter_releases(repo: str):
    page = 1
    while True:
        releases = request_json(f"{API_ROOT}/repos/{repo}/releases?per_page=100&page={page}")
        if not releases:
            break
        if not isinstance(releases, list):
            raise ReleaseError("releases API returned an unexpected payload")
        yield from releases
        page += 1


def create_or_find_release(
    repo: str,
    sha: str,
    asset_names: set[str],
    *,
    source_event: str = "unknown",
) -> tuple[dict[str, Any], set[str], bool]:
    for candidate in iter_releases(repo):
        tag_name = candidate.get("tag_name", "")
        uploaded_asset_names = _release_asset_names(candidate)
        if candidate.get("target_commitish") != sha or not tag_name.startswith(RELEASE_TAG_PREFIX):
            continue
        if asset_names <= uploaded_asset_names:
            if candidate.get("draft", False):
                request_json(
                    f"{API_ROOT}/repos/{repo}/releases/{candidate['id']}",
                    method="PATCH",
                    payload={"body": _release_body(sha, source_event), "draft": False},
                )
                print(f"Published existing draft release: {candidate['tag_name']}")
            else:
                print(f"Release already exists: {candidate['tag_name']}")
            return candidate, uploaded_asset_names, True
        if candidate.get("draft", False):
            if _release_source_event(candidate) != source_event:
                candidate = request_json(
                    f"{API_ROOT}/repos/{repo}/releases/{candidate['id']}",
                    method="PATCH",
                    payload={"body": _release_body(sha, source_event)},
                )
                if not isinstance(candidate, dict):
                    raise ReleaseError("release update API returned an unexpected payload")
            return candidate, uploaded_asset_names, False
        return candidate, uploaded_asset_names, False

    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    tag = f"{RELEASE_TAG_PREFIX}{timestamp}-{sha[:7]}"
    release = request_json(
        f"{API_ROOT}/repos/{repo}/releases",
        method="POST",
        payload={
            "tag_name": tag,
            "target_commitish": sha,
            "name": tag,
            "body": _release_body(sha, source_event),
            "draft": True,
            "prerelease": False,
        },
    )
    if not isinstance(release, dict):
        raise ReleaseError("release creation API returned an unexpected payload")
    return release, set(), False


def release_complete(repo: str, sha: str) -> bool:
    for candidate in iter_releases(repo):
        if candidate.get("target_commitish") != sha:
            continue
        if _release_has_complete_assets(candidate) and not candidate.get("draft", False):
            return True
    return False


def publish_release(repo: str, sha: str, dist: Path, *, source_event: str = "unknown") -> None:
    assets = [
        dist / f"personal-codex-{sha}.tar.gz",
        dist / f"personal-codex-{sha}.sha256",
    ]
    for asset in assets:
        if not asset.is_file():
            raise ReleaseError(f"release asset is missing: {asset}")
    expected_asset_names = {asset.name for asset in assets}
    release, uploaded_asset_names, done = create_or_find_release(
        repo,
        sha,
        expected_asset_names,
        source_event=source_event,
    )
    if done:
        return

    for stale_asset in _incomplete_release_assets(release, expected_asset_names):
        asset_id = stale_asset.get("id")
        asset_name = stale_asset.get("name", "unknown")
        if not isinstance(asset_id, int):
            raise ReleaseError(f"incomplete release asset has no numeric id: {asset_name}")
        request_json(
            f"{API_ROOT}/repos/{repo}/releases/assets/{asset_id}",
            method="DELETE",
        )
        print(f"Deleted incomplete asset: {asset_name}")

    content_types = {
        ".gz": "application/gzip",
        ".sha256": "text/plain",
    }
    for asset in assets:
        if asset.name in uploaded_asset_names:
            print(f"Asset already exists: {asset.name}")
            continue
        suffix = ".sha256" if asset.name.endswith(".sha256") else asset.suffix
        url = (
            f"{UPLOAD_ROOT}/repos/{repo}/releases/{release['id']}/assets"
            f"?name={quote(asset.name)}"
        )
        request = Request(
            url,
            data=asset.read_bytes(),
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {_github_token()}",
                "Content-Type": content_types[suffix],
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="POST",
        )
        with urlopen(request, timeout=30) as response:
            uploaded = json.loads(response.read().decode("utf-8"))
        print(f"Uploaded {uploaded['name']}")
        uploaded_asset_names.add(uploaded["name"])

    if not expected_asset_names <= uploaded_asset_names:
        raise ReleaseError(f"uploaded asset mismatch: {sorted(uploaded_asset_names)}")
    request_json(
        f"{API_ROOT}/repos/{repo}/releases/{release['id']}",
        method="PATCH",
        payload={"draft": False},
    )
    print(f"Published {release['tag_name']}")


def _write_github_output(run: bool, reason: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as file:
        file.write(f"run={'true' if run else 'false'}\n")
        file.write(f"reason={reason}\n")


def _write_release_complete_output(complete: bool) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as file:
        file.write(f"complete={'true' if complete else 'false'}\n")


def command_should_run(args: argparse.Namespace) -> int:
    run, reason = should_run(
        repo=args.repo,
        workflow=args.workflow,
        current_run_id=args.current_run_id,
        event=args.event,
        force=args.force,
        cooldown_seconds=args.cooldown_hours * 60 * 60,
    )
    _write_github_output(run, reason)
    print(f"run={'true' if run else 'false'}")
    print(f"reason={reason}")
    return 0


def command_verify_package(args: argparse.Namespace) -> int:
    verify_package(Path(args.repo_root).resolve(), args.sha, Path(args.dist))
    return 0


def command_publish(args: argparse.Namespace) -> int:
    publish_release(args.repo, args.sha, Path(args.dist), source_event=args.source_event)
    return 0


def command_release_complete(args: argparse.Namespace) -> int:
    complete = release_complete(args.repo, args.sha)
    _write_release_complete_output(complete)
    print(f"complete={'true' if complete else 'false'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Private overlay release workflow helpers.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    should_run_parser = subparsers.add_parser("should-run")
    should_run_parser.add_argument("--repo", required=True)
    should_run_parser.add_argument("--workflow", required=True)
    should_run_parser.add_argument("--current-run-id", required=True)
    should_run_parser.add_argument("--event", required=True)
    should_run_parser.add_argument("--force", action="store_true")
    should_run_parser.add_argument("--cooldown-hours", type=int, default=8)
    should_run_parser.set_defaults(func=command_should_run)

    verify_parser = subparsers.add_parser("verify-package")
    verify_parser.add_argument("--repo-root", default=".")
    verify_parser.add_argument("--sha", required=True)
    verify_parser.add_argument("--dist", default="dist")
    verify_parser.set_defaults(func=command_verify_package)

    publish_parser = subparsers.add_parser("publish")
    publish_parser.add_argument("--repo", required=True)
    publish_parser.add_argument("--sha", required=True)
    publish_parser.add_argument("--dist", default="dist")
    publish_parser.add_argument("--source-event", default=os.environ.get("GITHUB_EVENT_NAME", "unknown"))
    publish_parser.set_defaults(func=command_publish)

    release_complete_parser = subparsers.add_parser("release-complete")
    release_complete_parser.add_argument("--repo", required=True)
    release_complete_parser.add_argument("--sha", required=True)
    release_complete_parser.set_defaults(func=command_release_complete)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ReleaseError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
