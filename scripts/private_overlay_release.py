#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
import re


API_ROOT = "https://api.github.com"
UPLOAD_ROOT = "https://uploads.github.com"
RELEASE_TAG_PREFIX = "personal-codex-"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RELEASE_TAG_RE = re.compile(
    r"personal-codex-[0-9]{8}-[0-9]{6}-(?P<sha_prefix>[0-9a-f]{7,40})"
)
PERSONAL_CODEX_ASSET_RE = re.compile(
    r"personal-codex-(?P<sha>[0-9a-f]{40})\.(?:tar\.gz|sha256)"
)
ASSET_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
MAX_RELEASE_ASSET_BYTES = 64 * 1024 * 1024
RELEASE_ASSET_READ_CHUNK_BYTES = 1024 * 1024
DEFAULT_GITHUB_API_VERSION = "2022-11-28"
IMMUTABLE_RELEASES_API_VERSION = "2026-03-10"


class ReleaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class LocalAssetContent:
    data: bytes
    size: int
    digest: str


def _github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ReleaseError("GITHUB_TOKEN is required")
    return token


def _immutable_releases_read_token() -> str:
    token = os.environ.get("IMMUTABLE_RELEASES_READ_TOKEN", "").strip()
    if not token:
        raise ReleaseError(
            "IMMUTABLE_RELEASES_READ_TOKEN is required for immutable releases "
            "Administration (read) capability checks"
        )
    return token


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    api_version: str = DEFAULT_GITHUB_API_VERSION,
) -> Any:
    data = None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token or _github_token()}",
        "X-GitHub-Api-Version": api_version,
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


def _require_immutable_releases_enabled(repo: str) -> None:
    try:
        settings = request_json(
            f"{API_ROOT}/repos/{repo}/immutable-releases",
            token=_immutable_releases_read_token(),
            api_version=IMMUTABLE_RELEASES_API_VERSION,
        )
    except HTTPError as error:
        raise ReleaseError(
            f"immutable releases preflight failed for {repo}: HTTP {error.code}"
        ) from error
    if not isinstance(settings, dict) or settings.get("enabled") is not True:
        raise ReleaseError(f"immutable releases are not enabled for repository {repo}")


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


def _release_assets(release: dict[str, Any]) -> list[dict[str, Any]]:
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        return []
    return [asset for asset in assets if isinstance(asset, dict)]


def _personal_release_publication_flags(
    release: dict[str, Any],
    tag_name: str,
) -> tuple[bool, bool]:
    draft = release.get("draft")
    prerelease = release.get("prerelease")
    if not isinstance(draft, bool) or not isinstance(prerelease, bool):
        raise ReleaseError(
            f"personal-codex release {tag_name} has invalid publication flags"
        )
    return draft, prerelease


def _positive_release_id(release: dict[str, Any], tag_name: str) -> int:
    release_id = release.get("id")
    if (
        not isinstance(release_id, int)
        or isinstance(release_id, bool)
        or release_id <= 0
    ):
        raise ReleaseError(
            f"personal-codex release {tag_name} has no positive integer id"
        )
    return release_id


def _is_personal_codex_asset_name(name: object) -> bool:
    return isinstance(name, str) and PERSONAL_CODEX_ASSET_RE.fullmatch(name) is not None


def _release_tag_matches_sha(tag_name: object, sha: str) -> bool:
    if not isinstance(tag_name, str):
        return False
    match = RELEASE_TAG_RE.fullmatch(tag_name)
    return match is not None and sha.startswith(match.group("sha_prefix"))


def _target_commitish_matches_sha(
    target_commitish: object,
    sha: str,
    *,
    allow_symbolic: bool,
) -> bool:
    if isinstance(target_commitish, str) and SHA_RE.fullmatch(target_commitish):
        return target_commitish == sha
    return allow_symbolic


def _expected_asset_names(sha: str) -> set[str]:
    return {
        f"personal-codex-{sha}.tar.gz",
        f"personal-codex-{sha}.sha256",
    }


def _read_local_asset_content(asset: Path) -> LocalAssetContent:
    content = bytearray()
    digest = hashlib.sha256()
    size = 0
    try:
        with asset.open("rb") as asset_file:
            while True:
                chunk = asset_file.read(RELEASE_ASSET_READ_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_RELEASE_ASSET_BYTES:
                    raise ReleaseError(
                        f"release asset exceeds {MAX_RELEASE_ASSET_BYTES} bytes: "
                        f"{asset}"
                    )
                content.extend(chunk)
                digest.update(chunk)
    except OSError as error:
        raise ReleaseError(f"failed to read release asset {asset}: {error}") from error
    return LocalAssetContent(
        data=bytes(content),
        size=size,
        digest=f"sha256:{digest.hexdigest()}",
    )


def _personal_codex_release_assets(release: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        asset
        for asset in _release_assets(release)
        if _is_personal_codex_asset_name(asset.get("name"))
    ]


def _has_exact_uploaded_assets(
    release: dict[str, Any],
    expected_asset_names: set[str],
) -> bool:
    matching_assets = _personal_codex_release_assets(release)
    return (
        len(matching_assets) == len(expected_asset_names)
        and {asset.get("name") for asset in matching_assets} == expected_asset_names
        and all(asset.get("state") == "uploaded" for asset in matching_assets)
    )


def _validated_uploaded_assets(
    release: dict[str, Any],
    expected_asset_names: set[str],
    *,
    context: str,
    expected_asset_content: dict[str, LocalAssetContent] | None = None,
) -> list[dict[str, Any]]:
    release_assets = release.get("assets")
    if not isinstance(release_assets, list):
        raise ReleaseError(f"{context} has no release asset array")

    matching_assets: list[dict[str, Any]] = []
    for asset in release_assets:
        if not isinstance(asset, dict):
            raise ReleaseError(f"{context} has a non-object release asset")
        if _is_personal_codex_asset_name(asset.get("name")):
            matching_assets.append(asset)

    matching_names = [asset["name"] for asset in matching_assets]
    matching_name_set = set(matching_names)
    if (
        len(matching_assets) != len(expected_asset_names)
        or len(matching_name_set) != len(matching_names)
        or matching_name_set != expected_asset_names
    ):
        raise ReleaseError(f"{context} release assets are not exact")

    seen_ids: set[int] = set()
    for asset in matching_assets:
        asset_name = asset["name"]
        if asset.get("state") != "uploaded":
            raise ReleaseError(f"{context} release asset is not uploaded: {asset_name}")
        asset_id = asset.get("id")
        if (
            not isinstance(asset_id, int)
            or isinstance(asset_id, bool)
            or asset_id <= 0
        ):
            raise ReleaseError(
                f"{context} release asset has no positive integer id: {asset_name}"
            )
        if asset_id in seen_ids:
            raise ReleaseError(f"{context} release assets reuse id {asset_id}")
        seen_ids.add(asset_id)

        asset_size = asset.get("size")
        if (
            not isinstance(asset_size, int)
            or isinstance(asset_size, bool)
            or asset_size < 0
        ):
            raise ReleaseError(
                f"{context} release asset has invalid size: {asset_name}"
            )
        asset_digest = asset.get("digest")
        if (
            not isinstance(asset_digest, str)
            or ASSET_DIGEST_RE.fullmatch(asset_digest) is None
        ):
            raise ReleaseError(
                f"{context} release asset has invalid digest: {asset_name}"
            )

        if expected_asset_content is None:
            continue
        expected_content = expected_asset_content[asset_name]
        if asset_size != expected_content.size:
            raise ReleaseError(f"{context} release asset size mismatch: {asset_name}")
        if asset_digest != expected_content.digest:
            raise ReleaseError(f"{context} release asset digest mismatch: {asset_name}")

    return matching_assets


def _validated_repair_assets(
    release: dict[str, Any],
) -> list[tuple[int, dict[str, Any]]]:
    validated: list[tuple[int, dict[str, Any]]] = []
    seen_ids: set[int] = set()
    for asset in _personal_codex_release_assets(release):
        asset_id = asset.get("id")
        asset_name = asset.get("name", "unknown")
        if not isinstance(asset_id, int) or isinstance(asset_id, bool) or asset_id <= 0:
            raise ReleaseError(
                f"matching release asset has no positive integer id: {asset_name}"
            )
        if asset_id in seen_ids:
            raise ReleaseError(f"matching release assets reuse id {asset_id}")
        seen_ids.add(asset_id)
        validated.append((asset_id, asset))
    return validated


def _release_has_complete_assets(release: dict[str, Any], sha: str) -> bool:
    if not _release_tag_matches_sha(release.get("tag_name"), sha):
        return False
    return _has_exact_uploaded_assets(release, _expected_asset_names(sha))


def _complete_release_asset_sha(release: dict[str, Any]) -> str | None:
    matching_assets = _personal_codex_release_assets(release)
    matching_shas = {
        match.group("sha")
        for asset in matching_assets
        if isinstance(asset.get("name"), str)
        and (match := PERSONAL_CODEX_ASSET_RE.fullmatch(asset["name"])) is not None
    }
    if len(matching_shas) != 1:
        return None
    sha = next(iter(matching_shas))
    return sha if _release_has_complete_assets(release, sha) else None


def _release_has_remote_integrity(
    release: dict[str, Any],
    sha: str | None = None,
) -> bool:
    sha = sha or _complete_release_asset_sha(release)
    tag_name = release.get("tag_name")
    if (
        not isinstance(sha, str)
        or SHA_RE.fullmatch(sha) is None
        or not isinstance(tag_name, str)
        or not _release_tag_matches_sha(tag_name, sha)
        or not _target_commitish_matches_sha(
            release.get("target_commitish"),
            sha,
            allow_symbolic=release.get("immutable") is True,
        )
    ):
        return False
    try:
        draft, prerelease = _personal_release_publication_flags(release, tag_name)
        _positive_release_id(release, tag_name)
        _validated_uploaded_assets(
            release,
            _expected_asset_names(sha),
            context=f"published release {tag_name}",
        )
    except ReleaseError:
        return False
    return not draft and not prerelease and release.get("immutable") is True


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
        if not isinstance(release, dict):
            continue
        tag_name = release.get("tag_name")
        if not isinstance(tag_name, str) or not tag_name.startswith(
            RELEASE_TAG_PREFIX
        ):
            continue
        draft, prerelease = _personal_release_publication_flags(release, tag_name)
        if draft or prerelease:
            continue
        published_at_raw = release.get("published_at") or release.get("created_at")
        if not isinstance(published_at_raw, str):
            continue
        if parse_timestamp(published_at_raw) < cutoff:
            continue
        if event == "schedule" and _release_source_event(release) == "schedule":
            continue
        if _release_has_remote_integrity(release):
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


def verify_package(repo_root: Path, sha: str, dist: Path) -> None:
    module = _load_sync_module(repo_root)
    archive_path = dist / f"personal-codex-{sha}.tar.gz"
    checksum_path = dist / f"personal-codex-{sha}.sha256"
    with module.bind_archive_workspace(dist) as read_workspace:
        with module.temporary_archive_workspace(
            prefix="codex-private-overlay-verify."
        ) as archive_workspace:
            workspace = archive_workspace.path
            _release_root, release_expectation = module.verify_and_extract_archive(
                archive_path,
                checksum_path,
                workspace / "extract",
                workspace=archive_workspace,
                read_workspace=read_workspace,
            )
            manifest_data = release_expectation[0][1]
            entries = manifest_data.entries
            targets = {entry.target.as_posix() for entry in entries}
            if not entries or any(entry.owner != "private" for entry in entries):
                raise ReleaseError(
                    "release manifest must contain only private-owned entries"
                )
            if manifest_data.base_release_repo != "Joey-Tools/codex-toolbox":
                raise ReleaseError(
                    "release manifest must declare the public base release repo"
                )
            if "bin/codex-personal-sync" in targets:
                raise ReleaseError(
                    "private overlay must not publish the public sync runner"
                )


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


def _matching_release_candidates(
    repo: str,
    sha: str,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    complete_candidates: list[dict[str, Any]] = []
    incomplete_candidates: list[dict[str, Any]] = []
    draft_candidates: list[dict[str, Any]] = []
    seen_release_ids: set[int] = set()
    seen_tag_names: set[str] = set()
    for candidate in iter_releases(repo):
        if not isinstance(candidate, dict):
            raise ReleaseError("releases API returned a non-object entry")
        tag_name = candidate.get("tag_name", "")
        if not isinstance(tag_name, str) or not tag_name.startswith(RELEASE_TAG_PREFIX):
            continue
        target_commitish = candidate.get("target_commitish")
        has_complete_assets = _release_has_complete_assets(candidate, sha)
        if target_commitish != sha and not has_complete_assets:
            continue
        draft, prerelease = _personal_release_publication_flags(candidate, tag_name)
        if draft and prerelease:
            raise ReleaseError(
                f"personal-codex release {tag_name} cannot be both draft and prerelease"
            )
        if prerelease:
            continue
        if not _release_tag_matches_sha(tag_name, sha):
            raise ReleaseError(
                f"matching release has an invalid tag for {sha}: {tag_name}"
            )
        if has_complete_assets and not _target_commitish_matches_sha(
            target_commitish,
            sha,
            allow_symbolic=not draft and candidate.get("immutable") is True,
        ):
            raise ReleaseError(
                f"matching complete release target commitish does not match {sha}: "
                f"{target_commitish!r}"
            )
        release_id = _positive_release_id(candidate, tag_name)
        if release_id in seen_release_ids:
            raise ReleaseError(
                f"matching releases reuse GitHub release id {release_id}"
            )
        if tag_name in seen_tag_names:
            raise ReleaseError(f"matching releases reuse tag name {tag_name}")
        seen_release_ids.add(release_id)
        seen_tag_names.add(tag_name)
        if not isinstance(candidate.get("assets"), list):
            raise ReleaseError(
                f"matching release {tag_name} has no release asset array"
            )
        if draft:
            draft_candidates.append(candidate)
        elif has_complete_assets:
            complete_candidates.append(candidate)
        else:
            incomplete_candidates.append(candidate)

    return complete_candidates, incomplete_candidates, draft_candidates


def _create_or_find_release(
    repo: str,
    sha: str,
    asset_names: set[str],
    *,
    source_event: str = "unknown",
    before_create: Callable[[], None],
) -> tuple[dict[str, Any], set[str], bool]:
    if asset_names != _expected_asset_names(sha):
        raise ReleaseError("release asset names do not match the target commit")
    (
        complete_candidates,
        incomplete_candidates,
        draft_candidates,
    ) = _matching_release_candidates(repo, sha)
    if len(incomplete_candidates) > 1:
        raise ReleaseError(
            f"multiple incomplete personal-codex releases match {sha}"
        )
    if incomplete_candidates:
        candidate = incomplete_candidates[0]
        uploaded_asset_names = {
            asset["name"]
            for asset in _release_assets(candidate)
            if isinstance(asset.get("name"), str)
            and asset.get("state") == "uploaded"
        }
        return candidate, uploaded_asset_names, False
    if complete_candidates:
        candidate = complete_candidates[0]
        uploaded_asset_names = {
            asset["name"]
            for asset in _release_assets(candidate)
            if isinstance(asset.get("name"), str)
            and asset.get("state") == "uploaded"
        }
        return candidate, uploaded_asset_names, True
    if len(draft_candidates) > 1:
        raise ReleaseError(
            f"multiple draft personal-codex releases match {sha}"
        )
    if draft_candidates:
        candidate = draft_candidates[0]
        uploaded_asset_names = {
            asset["name"]
            for asset in _release_assets(candidate)
            if isinstance(asset.get("name"), str)
            and asset.get("state") == "uploaded"
        }
        return candidate, uploaded_asset_names, False

    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    tag = f"{RELEASE_TAG_PREFIX}{timestamp}-{sha[:7]}"
    before_create()
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
    if (
        release.get("tag_name") != tag
        or release.get("target_commitish") != sha
    ):
        raise ReleaseError("release creation API returned a mismatched identity")
    draft, prerelease = _personal_release_publication_flags(release, tag)
    if not draft or prerelease:
        raise ReleaseError("release creation API returned invalid publication flags")
    _positive_release_id(release, tag)
    if not isinstance(release.get("assets"), list):
        raise ReleaseError("release creation API returned no release asset array")
    return release, set(), False


def create_or_find_release(
    repo: str,
    sha: str,
    asset_names: set[str],
    *,
    source_event: str = "unknown",
) -> tuple[dict[str, Any], set[str], bool]:
    return _create_or_find_release(
        repo,
        sha,
        asset_names,
        source_event=source_event,
        before_create=lambda: _require_immutable_releases_enabled(repo),
    )


def release_complete(repo: str, sha: str) -> bool:
    complete_candidates, incomplete_candidates, _draft_candidates = (
        _matching_release_candidates(repo, sha)
    )
    return (
        bool(complete_candidates)
        and not incomplete_candidates
        and all(
            _release_has_remote_integrity(candidate, sha)
            for candidate in complete_candidates
        )
    )


def _validated_release_snapshot(
    release: object,
    expected_identity: tuple[int, str, object],
    expected_asset_names: set[str],
    expected_asset_content: dict[str, LocalAssetContent],
    *,
    phase: str,
    require_published: bool,
    require_immutable: bool,
) -> tuple[dict[str, Any], bool]:
    if not isinstance(release, dict):
        raise ReleaseError(f"release lookup returned an unexpected payload {phase}")
    tag_name = release.get("tag_name")
    if not isinstance(tag_name, str):
        raise ReleaseError(f"release identity changed {phase}")
    identity = (
        release.get("id"),
        tag_name,
        release.get("target_commitish"),
    )
    if identity != expected_identity:
        raise ReleaseError(f"release identity changed {phase}")
    draft, prerelease = _personal_release_publication_flags(release, tag_name)
    if prerelease:
        raise ReleaseError(f"release became a prerelease {phase}")
    _validated_uploaded_assets(
        release,
        expected_asset_names,
        context=f"release {phase}",
        expected_asset_content=expected_asset_content,
    )
    if require_published and draft:
        raise ReleaseError(f"release remained a draft {phase}")
    if require_immutable and release.get("immutable") is not True:
        raise ReleaseError(f"published release is not immutable {phase}")
    return release, draft


def publish_release(repo: str, sha: str, dist: Path, *, source_event: str = "unknown") -> None:
    assets = [
        dist / f"personal-codex-{sha}.tar.gz",
        dist / f"personal-codex-{sha}.sha256",
    ]
    expected_asset_content = {
        asset.name: _read_local_asset_content(asset) for asset in assets
    }
    expected_asset_names = set(expected_asset_content)
    immutable_releases_verified = False

    def ensure_immutable_releases_enabled() -> None:
        nonlocal immutable_releases_verified
        if immutable_releases_verified:
            return
        _require_immutable_releases_enabled(repo)
        immutable_releases_verified = True

    release, _uploaded_asset_names, done = _create_or_find_release(
        repo,
        sha,
        expected_asset_names,
        source_event=source_event,
        before_create=ensure_immutable_releases_enabled,
    )

    tag_name = release.get("tag_name")
    if not isinstance(tag_name, str):
        raise ReleaseError("selected release has an invalid tag name")
    target_commitish = release.get("target_commitish")
    if not _release_tag_matches_sha(tag_name, sha) or not _target_commitish_matches_sha(
        target_commitish,
        sha,
        allow_symbolic=done and release.get("immutable") is True,
    ):
        raise ReleaseError("selected release has a mismatched identity")
    selected_draft, prerelease = _personal_release_publication_flags(
        release,
        tag_name,
    )
    if prerelease:
        raise ReleaseError("selected release is a prerelease")
    release_id = _positive_release_id(release, tag_name)
    if not isinstance(release.get("assets"), list):
        raise ReleaseError("selected release has no release asset array")
    release_identity = (release_id, tag_name, target_commitish)
    if done:
        _validated_release_snapshot(
            release,
            release_identity,
            expected_asset_names,
            expected_asset_content,
            phase="during published release reuse",
            require_published=True,
            require_immutable=True,
        )
        print(f"Release already exists: {tag_name}")
        return

    if not selected_draft:
        raise ReleaseError(
            "incomplete published release requires operator resolution or "
            f"recreation: {tag_name}"
        )
    repair_assets = _validated_repair_assets(release)
    ensure_immutable_releases_enabled()
    for asset_id, stale_asset in repair_assets:
        asset_name = stale_asset.get("name", "unknown")
        request_json(
            f"{API_ROOT}/repos/{repo}/releases/assets/{asset_id}",
            method="DELETE",
        )
        print(f"Deleted release asset for repair: {asset_name}")

    content_types = {
        ".gz": "application/gzip",
        ".sha256": "text/plain",
    }
    for asset in assets:
        asset_content = expected_asset_content[asset.name]
        suffix = ".sha256" if asset.name.endswith(".sha256") else asset.suffix
        url = (
            f"{UPLOAD_ROOT}/repos/{repo}/releases/{release_id}/assets"
            f"?name={quote(asset.name)}"
        )
        request = Request(
            url,
            data=asset_content.data,
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
        if (
            not isinstance(uploaded, dict)
            or uploaded.get("name") != asset.name
            or uploaded.get("state") != "uploaded"
        ):
            raise ReleaseError(
                f"release asset upload returned an unexpected payload for {asset.name}"
            )
        print(f"Uploaded {asset.name}")

    release_url = f"{API_ROOT}/repos/{repo}/releases/{release_id}"
    refreshed = request_json(release_url)
    refreshed, refreshed_draft = _validated_release_snapshot(
        refreshed,
        release_identity,
        expected_asset_names,
        expected_asset_content,
        phase="after upload",
        require_published=False,
        require_immutable=False,
    )
    if refreshed_draft != selected_draft:
        raise ReleaseError("release draft flag changed after upload")
    _require_immutable_releases_enabled(repo)
    request_json(
        release_url,
        method="PATCH",
        payload={"body": _release_body(sha, source_event), "draft": False},
    )
    published = request_json(release_url)
    _validated_release_snapshot(
        published,
        release_identity,
        expected_asset_names,
        expected_asset_content,
        phase="after publish",
        require_published=True,
        require_immutable=True,
    )
    print(f"Published {tag_name}")


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
