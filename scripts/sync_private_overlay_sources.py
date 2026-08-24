#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import base64
import contextlib
from collections.abc import Callable, Iterator
import ctypes
from dataclasses import dataclass, field
import errno
import hashlib
import importlib.util
import os
from pathlib import Path
import secrets
import shutil
import stat
import sys
import tempfile


SOURCE_LOCK_SCRIPT = Path(__file__).resolve().parent / "private_overlay_source_lock.py"


class SyncError(RuntimeError):
    pass


def _load_source_lock_module():
    specification = importlib.util.spec_from_file_location(
        "private_overlay_source_lock_for_sync",
        SOURCE_LOCK_SCRIPT,
    )
    if specification is None or specification.loader is None:
        raise SyncError("cannot load the private overlay source-lock verifier")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    try:
        specification.loader.exec_module(module)
    except Exception as error:
        sys.modules.pop(specification.name, None)
        raise SyncError(f"cannot load source-lock verifier: {error}") from error
    return module


def _base_exception_note_method(error: BaseException):
    return getattr(error, "add_note", None)


def _attach_base_exception_detail(error: BaseException, detail: str) -> None:
    """Preserve recovery detail on Python 3.10 and newer runtimes."""

    add_note = _base_exception_note_method(error)
    if callable(add_note):
        add_note(detail)
        return
    print(f"error detail: {detail}", file=sys.stderr)


class _RegularFileOverlayBackupRetentionError(SyncError):
    pass


@dataclass(frozen=True)
class Replacement:
    old: str
    new: str
    required: bool = True
    path: Path | None = None
    required_count: int | None = None


@dataclass(frozen=True)
class RegularFileOverlay:
    source: Path
    target: Path


@dataclass(frozen=True)
class CanonicalReviewMigrationPolicy:
    repository: str
    reviewed_candidate_revision: str
    reviewed_candidate_commit_payload_base64: str
    approved_root_tree: str
    approved_review_subtree_tree: str
    legacy_revision: str
    legacy_root_tree: str


CANONICAL_REVIEW_MIGRATION_POLICY = CanonicalReviewMigrationPolicy(
    repository="Joey-Tools/codex-review-workflows",
    reviewed_candidate_revision="cd5ccd2ddd2a0975db6c5286765d4aab838bc736",
    reviewed_candidate_commit_payload_base64=(
        "dHJlZSBhZWY0YmVmN2E0NWFkYWI3NjJhMWI2NzFkYTQ4ZmJjMmQxZjQ0MDY0CnBhcmVudCBhNjc4"
        "MmU2Y2VlYTZhYzFkNmQwMmUwYmQyMjlmMjJkNzU1MGZlNzY4CmF1dGhvciBKb2V5IFRlbmcgPGpv"
        "ZXkudGVuZy5kZXZAZ21haWwuY29tPiAxNzg3NjA4NjUzICswMTAwCmNvbW1pdHRlciBKb2V5IFRl"
        "bmcgPGpvZXkudGVuZy5kZXZAZ21haWwuY29tPiAxNzg3NjA4NjUzICswMTAwCmdwZ3NpZyAtLS0t"
        "LUJFR0lOIFBHUCBTSUdOQVRVUkUtLS0tLQogCiBpSFVFQUJZS0FCMFdJUVR2dThrVDlKcGZiZ3J3"
        "MGtqM0FrWVVQY0tQTWdVQ2FveStUUUFLQ1JEM0FrWVVQY0tQCiBNdmg5QVA5L3U2aGVGSU5SNEhQ"
        "RGI5ekRuVXNpWnFDZFp5by9pa0lrQXh1MkdvVFNpd0VBeEZJQWxJdGtnNTBxCiB4L0RrNi8yOGU0"
        "NjFIeVpybklYdkc0aitiWWd6Q0FrPQogPXB4WkIKIC0tLS0tRU5EIFBHUCBTSUdOQVRVUkUtLS0t"
        "LQoKQ2xvc2UgcmV2aWV3IGV2aWRlbmNlIGFuZCB0ZWFyZG93biBnYXBzCgpCaW5kIHRoZSBsb2Nh"
        "bCBDb2RleCBwcmVmaXggcmVjZWlwdCB0byB0aGUgdmFsaWRhdGVkIHdvcmtzcGFjZSBhbmQgc2Vs"
        "ZWN0ZWQgR2l0IGlkZW50aXR5LiBNYWtlIHZlcmlmaWVyIHRlYXJkb3duIHNpZ25hbC1zYWZlLCBw"
        "cmVzZXJ2ZSBtdWx0aS1mYWlsdXJlIGNhdXNhbGl0eSwgY2xvc2UgZXZlcnkgb3duZWQgcmVjb3Zl"
        "cnkgZGVzY3JpcHRvciwgYW5kIGNvdmVyIFB5dGhvbiAzLjEwIGRpYWdub3N0aWNzIHBsdXMgcmV0"
        "YWluZWQgcmVjb3ZlcnkgdG9tYnN0b25lcy4KCkNvLWF1dGhvcmVkLWJ5OiBDb2RleCAodG9vbD1D"
        "b2RleCBEZXNrdG9wOyBtb2RlbD1HUFQtNS42IFNvbCBVbHRyYSkgPGNvZGV4QG9wZW5haS5jb20+"
        "Cg=="
    ),
    approved_root_tree="aef4bef7a45adab762a1b671da48fbc2d1f44064",
    approved_review_subtree_tree="6dab70713244598e3aaaa132eb082211b348bcdf",
    legacy_revision="c8df0f5d17e93a7b22d5fe5294baf9884ab2ba51",
    legacy_root_tree="e4081b640384cd885783637fa5aad8d21d4499d5",
)
MAX_CANONICAL_REVIEW_MIGRATION_ANCESTRY_COMMITS = 4096
MAX_CANONICAL_REVIEW_COMMIT_PROOF_BYTES = 16 * 1024


@dataclass(frozen=True)
class SyncRule:
    repo: str
    source: Path
    target: Path
    replacements: tuple[Replacement, ...] = ()
    text_extensions: tuple[str, ...] = (".md", ".yaml", ".yml", ".py", ".toml", ".json")
    exclude_names: tuple[str, ...] = ()
    forbidden_residuals: tuple[str, ...] = ()
    regular_file_overlays: tuple[RegularFileOverlay, ...] = ()
    replacement_excluded_paths: tuple[Path, ...] = ()
    canonical_review_migration_policy: CanonicalReviewMigrationPolicy | None = None


@dataclass(frozen=True)
class _VerifiedLockedSourcePin:
    repository: str
    revision: str
    root_tree: str


_COMPLETE_CHECKOUT_VERIFICATION_SEAL = object()


@dataclass(frozen=True)
class _CompleteCheckoutVerification:
    source_root: Path
    repo_root: Path
    source_lock: object = field(repr=False, compare=False)
    source_lock_module: object = field(repr=False, compare=False)
    source_lock_digest: str
    pins: tuple[tuple[object, object, object, object], ...]
    checkout_receipt: object
    event: object = field(repr=False, compare=False)
    seal: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class _CanonicalReviewMigrationReceipt:
    policy: CanonicalReviewMigrationPolicy
    source_pin: _VerifiedLockedSourcePin
    live_review_subtree_tree: str
    activation_basis: str


_CANONICAL_REVIEW_INSTALLED_RECEIPT_SEAL = object()


@dataclass(frozen=True)
class _CanonicalReviewInstalledMigrationReceipt:
    migration: _CanonicalReviewMigrationReceipt
    expected_target: Path
    prepared_source_manifest: object
    expected_manifest: object
    installed_receipt: object = field(repr=False, compare=False)
    seal: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class _LockedRuleSource:
    checkout: Path
    manifest: object
    read_blob: Callable[[Path, str], bytes]
    source_pin: _VerifiedLockedSourcePin | None = None
    canonical_review_migration_receipt: _CanonicalReviewMigrationReceipt | None = None
    prewrite_checkout_verification: _CompleteCheckoutVerification | None = None


def _path(raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise SyncError(f"unsafe relative path in sync rule: {raw}")
    return path


COMMON_JOEY_TEXT_REPLACEMENTS = (
    Replacement("the user's", "Joey's", required=False),
    Replacement("The user's", "Joey's", required=False),
    Replacement("the user", "Joey", required=False),
    Replacement("The user", "Joey", required=False),
    Replacement("Joey request", "Joey's request", required=False),
    Replacement("user-specific", "Joey-specific", required=False),
    Replacement("User-Specific", "Joey-Specific", required=False),
)


PUBLIC_LEGACY_MUTABLE_RELEASE_BLOCK = """_LEGACY_MUTABLE_RELEASES = {
    "Joey-Tools/codex-toolbox": _LegacyMutableRelease(
        release_id=325822894,
        tag_name="personal-codex-20260520-100331-8de1857",
        sha="8de18571811128cef148d13f1d474718d7cfae17",
        archive_id=425088267,
        archive_size=16353,
        archive_digest=(
            "sha256:05df0bf973e3c67aedd03838c5116471"
            "314e7ce6c24d3435b0f8b7765624c9be"
        ),
        checksum_id=425088288,
        checksum_size=129,
        checksum_digest=(
            "sha256:c3ed6fe4d5df9178f471c80dd9e0bb56"
            "340b4e8c32731974ff4747e3797d4805"
        ),
    ),
}"""


PRIVATE_LEGACY_MUTABLE_RELEASE_BLOCK = """_LEGACY_MUTABLE_RELEASES = {
    "Joey-Tools/codex-private-workflows": _LegacyMutableRelease(
        release_id=325865586,
        tag_name="personal-codex-20260520-104847-4e5ca3f",
        sha="4e5ca3f1a377c5dfb572f35fc2bab8f38e885685",
        archive_id=425126036,
        archive_size=214354,
        archive_digest=(
            "sha256:ed831eac668a0ecd330ce5c168a50477"
            "927dd4eb8ed68b10b0f1fd90cf5399ef"
        ),
        checksum_id=425126043,
        checksum_size=129,
        checksum_digest=(
            "sha256:5b67e065429e3ca6f58186ab97488032"
            "c405edc0940da69f95875dbf2a50bed4"
        ),
    ),
}"""


PUBLIC_BUG_TRIAGE_DESCRIPTION = (
    "description: Optionally transport and inspect allowlisted Jenkins-style HTTPS "
    "console, API, and ZIP artifacts with bounded authentication, redirects, output, "
    "extraction, and wall time. Use when a task has an exact remote artifact URL or a "
    "local ZIP and needs a public-safe probe, fetch, member listing, text view, or "
    "single-member extraction before diagnosis."
)


PRIVATE_BUG_TRIAGE_DESCRIPTION = (
    "description: Transport and inspect allowlisted Cisco Jenkins HTTPS console, API, "
    "and ZIP artifacts with bounded authentication, redirects, output, extraction, "
    "and wall time. Use when Joey has an exact remote artifact URL or a local ZIP and "
    "needs a private fixed-profile probe, fetch, member listing, text view, or "
    "single-member extraction before diagnosis."
)


PUBLIC_BUG_TRIAGE_SCOPE = (
    "This optional public skill supplies one canonical artifact transport helper. It "
    "does not define a generic root-cause method, GitHub Actions triage, tracker "
    "lookup, remote process diagnosis, or private host policy. Use the relevant forge "
    "or tracker skill for those tasks, and use ordinary evidence-based reasoning after "
    "the requested artifact is available."
)


PRIVATE_BUG_TRIAGE_SCOPE = (
    "This private skill supplies one canonical artifact transport helper for Joey's "
    "fixed Cisco Jenkins policy. It does not define a generic root-cause method, GitHub "
    "Actions triage, tracker lookup, or remote process diagnosis. "
    "Use the relevant forge skill or "
    "[$cisco-trackers-lookup](../cisco-trackers-lookup/SKILL.md) for those tasks, and "
    "use ordinary evidence-based reasoning after the requested artifact is available."
)


PUBLIC_BUG_TRIAGE_CONFIGURATION = (
    "The helper is `scripts/jenkins_artifact_probe.py`. Its public configuration is "
    "deliberately synthetic and fail-closed. A private installation may specialize "
    "fixed source constants through its own release process; callers cannot widen "
    "hosts, auth profiles, deadlines, or resource ceilings at runtime."
)


PRIVATE_BUG_TRIAGE_CONFIGURATION = (
    "The helper is `scripts/jenkins_artifact_probe.py`. Its private configuration is "
    "fixed and fail-closed by this release process; callers cannot widen hosts, auth "
    "profiles, deadlines, or resource ceilings at runtime."
)


PUBLIC_BUG_TRIAGE_RECIPES_SCOPE = (
    "These recipes use the optional public helper as the canonical transport boundary. "
    "The public host and job names are synthetic. Run the installed helper directly; "
    "avoid wrapping authenticated calls in a broad shell command."
)


PRIVATE_BUG_TRIAGE_RECIPES_SCOPE = (
    "These recipes use the private helper as the canonical transport boundary. The "
    "allowed host is fixed by the private release, and job names are examples. Run the "
    "installed helper directly; avoid wrapping authenticated calls in a broad shell "
    "command."
)


PUBLIC_BUG_TRIAGE_CONFIG_BLOCK = """DEFAULT_ALLOWED_HOSTS = frozenset({"jenkins.example.com"})
AUTH_PROFILES = {
    "default": (
        "JENKINS_ARTIFACT_USER",
        "JENKINS_ARTIFACT_TOKEN",
    ),
}"""


PRIVATE_BUG_TRIAGE_CONFIG_BLOCK = """ALLOWED_HOSTS = frozenset({"engci-private-sjc.cisco.com"})
AUTH_PROFILES = {
    "jenkins_mbpm2_codex": (
        "Jenkins_mbpM2_codex_username",
        "Jenkins_mbpM2_codex_token",
    ),
    "jenkins_webex_teams": (
        "Jenkins_webex_teams_username",
        "Jenkins_webex_teams_token",
    ),
    "wme_jenkins_jobs_artifact": (
        "wme_jenkins_jobs_artifact_user",
        "wme_jenkins_jobs_artifact_token",
    ),
}"""


PUBLIC_BUG_TRIAGE_BUILD_REMOTE_REQUEST_BLOCK = """def _build_remote_request(
    url: str,
    *,
    method: str,
    auth_profile: Optional[str],
) -> Tuple[urllib.request.Request, str]:
    _ensure_allowed_url(url)
    request = urllib.request.Request(url, method=method)
    auth_state = _add_basic_auth(request, auth_profile)
    return request, auth_state"""


PRIVATE_BUG_TRIAGE_BUILD_REMOTE_REQUEST_BLOCK = """def _build_remote_request(
    url: str,
    *,
    method: str,
    auth_profile: Optional[str],
) -> Tuple[urllib.request.Request, str]:
    parsed = _ensure_allowed_url(url)
    request = urllib.request.Request(parsed.geturl(), method=method)
    auth_state = _add_basic_auth(request, auth_profile)
    return request, auth_state"""


PUBLIC_BUG_TRIAGE_REDIRECT_REQUEST_CONSTRUCTION = (
    "redirected = urllib.request.Request(target, method=method)"
)
PRIVATE_BUG_TRIAGE_REDIRECT_REQUEST_CONSTRUCTION = (
    "redirected = urllib.request.Request(parsed.geturl(), method=method)"
)


PUBLIC_BUG_TRIAGE_BUILD_OPENER_BLOCK = """def _build_opener(initial_url: str, max_redirects: int) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        SameOriginRedirectHandler(initial_url, max_redirects)
    )"""


PRIVATE_BUG_TRIAGE_BUILD_OPENER_BLOCK = """def _build_opener(initial_url: str, max_redirects: int) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        SameOriginRedirectHandler(initial_url, max_redirects),
    )"""


PUBLIC_BUG_TRIAGE_BLOCKABLE_SIGNALS_BLOCK = """def _blockable_signals() -> frozenset:
    blocked = set(signal.valid_signals())
    for name in ("SIGKILL", "SIGSTOP"):
        unmaskable = getattr(signal, name, None)
        if unmaskable is not None:
            blocked.discard(unmaskable)
    return frozenset(blocked)"""


PRIVATE_BUG_TRIAGE_BLOCKABLE_SIGNALS_BLOCK = """def _blockable_signals() -> frozenset:
    blocked = set(signal.valid_signals())
    blocked.discard(signal.SIGKILL)
    blocked.discard(signal.SIGSTOP)
    return frozenset(blocked)"""


PRIVATE_BUG_TRIAGE_TARGET = _path("personal_codex/skills/bug-triage-playbook")
PRIVATE_BUG_TRIAGE_ALLOWED_HOSTS = frozenset({"engci-private-sjc.cisco.com"})
PRIVATE_BUG_TRIAGE_AUTH_PROFILES = {
    "jenkins_mbpm2_codex": (
        "Jenkins_mbpM2_codex_username",
        "Jenkins_mbpM2_codex_token",
    ),
    "jenkins_webex_teams": (
        "Jenkins_webex_teams_username",
        "Jenkins_webex_teams_token",
    ),
    "wme_jenkins_jobs_artifact": (
        "wme_jenkins_jobs_artifact_user",
        "wme_jenkins_jobs_artifact_token",
    ),
}
PRIVATE_BUG_TRIAGE_REVIEWED_HELPER_SHA256 = (
    "643f914a7367d799d3837645cd7dc5a0a309e57cef8e26d595de6e250a1e0ea7"
)


def _rule(
    repo: str,
    source: str,
    target: str,
    replacements: tuple[Replacement, ...] = (),
    *,
    common_joey_text: bool = False,
    replacement_excluded_paths: tuple[str, ...] = (),
    exclude_names: tuple[str, ...] = (),
    forbidden_residuals: tuple[str, ...] = (),
    regular_file_overlays: tuple[RegularFileOverlay, ...] = (),
    canonical_review_migration_policy: CanonicalReviewMigrationPolicy | None = None,
) -> SyncRule:
    if common_joey_text:
        replacements = replacements + COMMON_JOEY_TEXT_REPLACEMENTS
    return SyncRule(
        repo=repo,
        source=_path(source),
        target=_path(target),
        replacements=replacements,
        replacement_excluded_paths=tuple(
            _path(path) for path in replacement_excluded_paths
        ),
        exclude_names=exclude_names,
        forbidden_residuals=forbidden_residuals,
        regular_file_overlays=regular_file_overlays,
        canonical_review_migration_policy=canonical_review_migration_policy,
    )


SYNC_RULES = (
    _rule(
        "codex-toolbox",
        "scripts/codex_personal_sync.py",
        "scripts/codex_personal_sync.py",
    ),
    _rule(
        "codex-toolbox",
        "tests/test_codex_personal_sync.py",
        "tests/test_codex_personal_sync.py",
    ),
    _rule(
        "codex-toolbox",
        "schema/sync-manifest.schema.json",
        "schema/sync-manifest.schema.json",
    ),
    _rule(
        "codex-toolbox",
        "scripts/build_personal_codex_package.py",
        "scripts/build_personal_codex_package.py",
        (
            Replacement(
                'DEFAULT_MANIFEST = Path("personal_codex/public-sync-manifest.json")',
                'DEFAULT_MANIFEST = Path("personal_codex/private-sync-manifest.json")',
            ),
        ),
    ),
    _rule(
        "codex-toolbox",
        "scripts/validate_sync_manifest_changes.py",
        "scripts/validate_sync_manifest_changes.py",
        (
            Replacement(
                'default="personal_codex/public-sync-manifest.json"',
                'default="personal_codex/private-sync-manifest.json"',
            ),
            Replacement(
                PUBLIC_LEGACY_MUTABLE_RELEASE_BLOCK,
                PRIVATE_LEGACY_MUTABLE_RELEASE_BLOCK,
            ),
        ),
    ),
    _rule(
        "codex-toolbox",
        "tests/test_sync_manifest_changes.py",
        "tests/test_sync_manifest_changes.py",
    ),
    _rule(
        "codex-toolbox",
        "tests/test_package_builder_safety.py",
        "tests/test_package_builder_safety.py",
    ),
    _rule(
        "codex-toolbox",
        "tests/test_release_manifest_baseline.py",
        "tests/test_release_manifest_baseline.py",
    ),
    _rule(
        "codex-toolbox",
        "tests/test_personal_sync_reconciliation_safety.py",
        "tests/test_personal_sync_reconciliation_safety.py",
    ),
    _rule(
        "codex-toolbox",
        "tests/test_release_retention.py",
        "tests/test_release_retention.py",
    ),
    _rule(
        "codex-toolbox",
        "tests/test_scheduler_doctor.py",
        "tests/test_scheduler_doctor.py",
    ),
    _rule(
        "codex-toolbox",
        "generated-sync-source-lock.json",
        "generated-sync-source-lock.json",
    ),
    _rule(
        "codex-toolbox",
        "scripts/verify_generated_sync_source_lock.py",
        "scripts/verify_generated_sync_source_lock.py",
    ),
    _rule(
        "codex-toolbox",
        "tests/test_generated_sync_source_lock.py",
        "tests/test_generated_sync_source_lock.py",
    ),
    _rule(
        "codex-review-workflows",
        "agents/reviewer.toml",
        "personal_codex/agents/reviewer.toml",
    ),
    _rule(
        "codex-review-workflows",
        "skills/agile-delivery-workflow",
        "personal_codex/skills/agile-delivery-workflow",
        (Replacement("user-visible", "Joey-visible", required=False),),
        common_joey_text=True,
    ),
    _rule(
        "codex-debug-triage",
        "skills/bug-triage-playbook",
        "personal_codex/skills/bug-triage-playbook",
        (
            Replacement(
                PUBLIC_BUG_TRIAGE_DESCRIPTION,
                PRIVATE_BUG_TRIAGE_DESCRIPTION,
                path=Path("SKILL.md"),
                required_count=1,
            ),
            Replacement(
                PUBLIC_BUG_TRIAGE_SCOPE,
                PRIVATE_BUG_TRIAGE_SCOPE,
                path=Path("SKILL.md"),
                required_count=1,
            ),
            Replacement(
                PUBLIC_BUG_TRIAGE_CONFIGURATION,
                PRIVATE_BUG_TRIAGE_CONFIGURATION,
                path=Path("SKILL.md"),
                required_count=1,
            ),
            Replacement(
                PUBLIC_BUG_TRIAGE_CONFIG_BLOCK,
                PRIVATE_BUG_TRIAGE_CONFIG_BLOCK,
                path=Path("scripts/jenkins_artifact_probe.py"),
                required_count=1,
            ),
            Replacement(
                "if parsed.hostname.lower() not in DEFAULT_ALLOWED_HOSTS:",
                "if parsed.hostname.lower() not in ALLOWED_HOSTS:",
                path=Path("scripts/jenkins_artifact_probe.py"),
                required_count=1,
            ),
            Replacement(
                PUBLIC_BUG_TRIAGE_BUILD_REMOTE_REQUEST_BLOCK,
                PRIVATE_BUG_TRIAGE_BUILD_REMOTE_REQUEST_BLOCK,
                path=Path("scripts/jenkins_artifact_probe.py"),
                required_count=1,
            ),
            Replacement(
                PUBLIC_BUG_TRIAGE_REDIRECT_REQUEST_CONSTRUCTION,
                PRIVATE_BUG_TRIAGE_REDIRECT_REQUEST_CONSTRUCTION,
                path=Path("scripts/jenkins_artifact_probe.py"),
                required_count=1,
            ),
            Replacement(
                PUBLIC_BUG_TRIAGE_BUILD_OPENER_BLOCK,
                PRIVATE_BUG_TRIAGE_BUILD_OPENER_BLOCK,
                path=Path("scripts/jenkins_artifact_probe.py"),
                required_count=1,
            ),
            Replacement(
                PUBLIC_BUG_TRIAGE_BLOCKABLE_SIGNALS_BLOCK,
                PRIVATE_BUG_TRIAGE_BLOCKABLE_SIGNALS_BLOCK,
                path=Path("scripts/jenkins_artifact_probe.py"),
                required_count=1,
            ),
            Replacement(
                PUBLIC_BUG_TRIAGE_RECIPES_SCOPE,
                PRIVATE_BUG_TRIAGE_RECIPES_SCOPE,
                path=Path("references/jenkins-artifact-recipes.md"),
                required_count=1,
            ),
            Replacement(
                "jenkins.example.com",
                "engci-private-sjc.cisco.com",
                path=Path("references/jenkins-artifact-recipes.md"),
                required_count=3,
            ),
            Replacement(
                "--auth-profile default",
                "--auth-profile wme_jenkins_jobs_artifact",
                path=Path("references/jenkins-artifact-recipes.md"),
                required_count=3,
            ),
        ),
        common_joey_text=True,
        forbidden_residuals=(
            "jenkins.example.com",
            "JENKINS_ARTIFACT_USER",
            "JENKINS_ARTIFACT_TOKEN",
            "--auth-profile default",
            "DEFAULT_ALLOWED_HOSTS",
            "_allowed_hosts",
            "optional public skill",
            "public-safe",
            "public configuration",
            "deliberately synthetic",
            "A private installation may specialize",
            "relevant forge or tracker skill",
            "optional public helper",
            "The public host",
            "private host policy",
        ),
    ),
    _rule(
        "codex-review-workflows",
        "skills/change-delivery-workflow",
        "personal_codex/skills/change-delivery-workflow",
        (
            Replacement(
                "Run a local pre-commit delivery gate",
                "Run Joey's local pre-commit delivery gate",
            ),
        ),
        common_joey_text=True,
    ),
    _rule(
        "codex-workflow-hygiene",
        "skills/bounded-command-output",
        "personal_codex/skills/bounded-command-output",
    ),
    _rule(
        "codex-workflow-hygiene",
        "skills/codex-session-mining",
        "personal_codex/skills/codex-session-mining",
        (
            Replacement(
                "pair with an environment-specific remote evidence workflow when remote-host evidence may matter.",
                "pair with `$remote-host-context` when remote-host evidence may matter.",
            ),
            Replacement(
                "If the task might depend on remote-host evidence, let an environment-specific remote evidence workflow materialize remote rollout candidates locally before concluding that local history is complete.",
                "If the task might depend on a host in `$remote-host-context`'s default evidence scope, use `$remote-host-context` before concluding the local machine is complete.\n"
                "- When remote-host coverage is needed, let `remote-host-context` own the remote access step. Use its helper to materialize remote rollout candidates locally, then continue the actual mining here.",
            ),
            Replacement(
                "If the task might depend on remote-host evidence",
                "If the task might depend on a host in `$remote-host-context`'s default evidence scope",
                required=False,
            ),
            Replacement(
                "use an environment-specific remote evidence workflow before concluding the local machine is complete.",
                "use `$remote-host-context` before concluding the local machine is complete.",
                required=False,
            ),
            Replacement(
                "let an environment-specific remote evidence workflow own the remote access step. Materialize remote rollout candidates locally",
                "let `remote-host-context` own the remote access step. Use its helper to materialize remote rollout candidates locally",
                required=False,
            ),
            Replacement(
                "Do not recreate a second remote-access workflow here; this skill owns local extraction and interpretation after remote evidence is materialized.",
                "Do not recreate a second remote-access workflow here. Remote access belongs to `remote-host-context`; this skill owns local extraction and interpretation after the evidence is available.",
            ),
            Replacement(
                "Remote access belongs to an environment-specific workflow",
                "Remote access belongs to `remote-host-context`",
                required=False,
            ),
            Replacement(
                "If the user is asking for a work summary, activity audit, or session recovery that may include remote hosts, use an environment-specific remote evidence workflow before concluding that the local `~/.codex` tree is complete.",
                "If the user is asking for a work summary, activity audit, or session recovery that may include a host in `$remote-host-context`'s default evidence scope, use `$remote-host-context` before concluding that the local `~/.codex` tree is complete.",
            ),
            Replacement(
                "remote hosts",
                "hosts in `$remote-host-context`'s default evidence scope",
                required=False,
            ),
        ),
        common_joey_text=True,
        forbidden_residuals=(
            "environment-specific remote evidence workflow",
            "environment-specific workflow",
        ),
    ),
    _rule(
        "codex-workflow-hygiene",
        "skills/codex-skill-authoring",
        "personal_codex/skills/joey-skill-authoring",
        (
            Replacement("codex-skill-authoring", "joey-skill-authoring"),
            Replacement("Codex Skill Authoring", "Joey Skill Authoring"),
            Replacement(
                "Create concise concise Codex skills.",
                "Create concise Joey-style Codex skills.",
            ),
        ),
        common_joey_text=True,
    ),
    _rule(
        "codex-project-journal",
        ".",
        "personal_codex/skills/project-journal",
        (
            Replacement(
                "description: Maintain repository project journals",
                "description: Maintain Joey repo project journals",
                path=Path("SKILL.md"),
                required_count=1,
            ),
            Replacement("repositories recently touched", "Joey repos recently touched"),
            Replacement("existing repositories", "existing Joey repos"),
            Replacement(
                "cross-repo project journal indexes for Codex workflows",
                "cross-repo project journal indexes for Joey's Codex workflows",
            ),
            Replacement(
                "Do not batch-install hooks across repositories",
                "Do not batch-install hooks across Joey repos",
            ),
        ),
        common_joey_text=True,
    ),
    _rule(
        "codex-review-workflows",
        "skills/review-orchestration-playbook/tests/fixtures/ci/private.yml",
        ".github/workflows/ci.yml",
    ),
    _rule(
        "codex-review-workflows",
        "skills/review-orchestration-playbook",
        "personal_codex/skills/review-orchestration-playbook",
        common_joey_text=True,
        replacement_excluded_paths=("tests/fixtures/ci/private.yml",),
        canonical_review_migration_policy=CANONICAL_REVIEW_MIGRATION_POLICY,
        regular_file_overlays=(
            RegularFileOverlay(
                Path(
                    "personal_codex/private-overrides/"
                    "review-orchestration-playbook/synthetic-token-catalog.json"
                ),
                Path("scripts/review_runtime/synthetic-token-catalog.json"),
            ),
        ),
    ),
    _rule(
        "codex-review-workflows",
        "skills/synthetic-token-fixtures",
        "personal_codex/skills/synthetic-token-fixtures",
        common_joey_text=True,
    ),
)


RETIRED_TARGETS = tuple(
    _path(path)
    for path in (
        "personal_codex/skills/copilot-review-playbook",
        "personal_codex/skills/external-review-playbook",
        "personal_codex/skills/pr-readiness-review-workflow",
        "personal_codex/skills/waited-delivery",
        "personal_codex/skills/codex-rules-hygiene",
        "personal_codex/skills/codex-session-retrospective",
    )
)

CANONICAL_REVIEW_TARGET = _path("personal_codex/skills/review-orchestration-playbook")
PERSONAL_AGENTS_TARGET = _path("personal_codex/AGENTS.md")
_CANONICAL_REVIEW_SYNC_RULE = next(
    rule for rule in SYNC_RULES if rule.target == CANONICAL_REVIEW_TARGET
)
PERSONAL_AGENTS_LEGACY_CONSENT_LINE = (
    b"- For Joey-requested Codex/GitHub PR or repo workflows, treat OpenAI Codex "
    b"services and GitHub-owned PR/review APIs as trusted destinations for scoped "
    b"repo/PR data: PR diffs, changed files, necessary nearby context, review "
    b"prompts/results, PR comments, statuses, and same-PR fix-loop reruns. This "
    b"standing consent excludes secrets, credentials, untracked private files, "
    b"unrelated repositories, broad workspace dumps, and non-Codex external "
    b"reviewers; approval justifications must still name the exact repo/PR and data "
    b"scope.\n"
)
PERSONAL_AGENTS_CURRENT_CONSENT_LINE = (
    b"- For Joey-requested Codex/GitHub PR or repo workflows, treat OpenAI Codex "
    b"services and GitHub-owned PR/review APIs as trusted destinations for scoped "
    b"repo/PR data: PR diffs, changed files, necessary nearby context, review "
    b"prompts/results, PR comments, statuses, and same-PR fix-loop reruns. This "
    b"standing consent excludes runtime secrets and credentials, untracked private "
    b"files, unrelated repositories, broad workspace dumps, and non-Codex external "
    b"reviewers; approval justifications must still name the exact repo/PR and data "
    b"scope.\n"
)
PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_START = (
    b"- For catalogued low-level-helper Claude local-login artifacts,"
)
PERSONAL_AGENTS_REVIEW_BLOCK_BOUNDARY = b"- Use `$remote-host-context` when "
PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256 = (
    "6d093c17f2bbcaef9a085937891f5e029044b10dace7e3e7972aebb819630a62"
)
PERSONAL_AGENTS_CURRENT_REVIEW_BLOCK = (
    b"- Use `$review-orchestration-playbook` as the only entrypoint for named "
    b"single, double, and triple review plus PR readiness. Single uses one "
    b"fresh-context local Codex review session, double adds actual Claude Code, "
    b"and triple adds current-head GitHub Codex. The skill owns adapter selection, "
    b"clean-workspace preparation, reviewer runtime checks, GitHub evidence and "
    b"recovery, and PR-readiness algorithms; do not duplicate those contracts "
    b"here.\n"
    b"- An unambiguous named single, double, or triple request is contemporaneous "
    b"consent for scoped review egress to that shape: OpenAI Codex for single, "
    b"Anthropic Claude Code additionally for double, and GitHub Codex on an exact "
    b"`github.com` PR additionally for triple. Reviewers may inspect the named "
    b"repository tracked diff, necessary tracked context, bounded derived evidence, "
    b"and review prompt/results, including tracked repository secrets. This excludes "
    b"untracked files, unrelated repositories, broad workspace or home-directory "
    b"content, credential discovery, GitHub Copilot, and substitute reviewers.\n"
    b"- A bare named-review request is report-only and does not authorize branch "
    b"creation or mutation, commits, push, PR creation/update or metadata changes, "
    b"merge, any GitHub Actions rerun, dispatch, or reconciliation, or unrelated "
    b"mutation. Bare triple authorizes only the scoped exact `@codex review` "
    b"producer operation on an already-existing eligible PR, including the skill's "
    b"single-owner, single-flight recovery after ambiguous delivery by repeating "
    b"that exact POST for the same logical request; it never authorizes a second "
    b"logical request. "
    b"Any GitHub Actions rerun, dispatch, or reconciliation requires both a "
    b"repository-predeclared exact idempotent or reentrant contract for the frozen "
    b"scope and exact inputs, and separate current-task delivery or readiness "
    b"authorization for that external mutation; it never authorizes a different "
    b"workflow, input, scope, destination, PR, repository, or unrelated action.\n"
)
INDEPENDENT_CODEX_REVIEW_ROOT = _path("scripts/independent_codex_pr_review")
INDEPENDENT_CODEX_REVIEW_REQUIRED_FILES = tuple(
    _path(path)
    for path in (
        ".gitignore",
        "ACCOUNT_LOCAL_RETENTION_V1",
        "review_supervisor/__init__.py",
        "review_supervisor/appserver_protocol.py",
        "review_supervisor/appserver_runtime.py",
        "review_supervisor/auth_carrier.py",
        "review_supervisor/auth_refresh.py",
        "review_supervisor/checkout.py",
        "review_supervisor/cli.py",
        "review_supervisor/codex_executable.py",
        "review_supervisor/constants.py",
        "review_supervisor/custody.py",
        "review_supervisor/direct_gate.py",
        "review_supervisor/errors.py",
        "review_supervisor/evidence.py",
        "review_supervisor/final_transport.py",
        "review_supervisor/frozen_source.py",
        "review_supervisor/gitraw.py",
        "review_supervisor/ledger.py",
        "review_supervisor/legacy_retention.py",
        "review_supervisor/lfs.py",
        "review_supervisor/logs.py",
        "review_supervisor/models.py",
        "review_supervisor/no_child_profile.py",
        "review_supervisor/process.py",
        "review_supervisor/prompt.py",
        "review_supervisor/recovery_cleanup.py",
        "review_supervisor/review_execution.py",
        "review_supervisor/runtime.py",
        "review_supervisor/secureio.py",
        "review_supervisor/settlement_state.py",
        "review_supervisor/signal_relay.py",
        "review_supervisor/supervisor.py",
        "review_supervisor/wire.py",
        "tests/__init__.py",
        "tests/async_fd_custody.py",
        "tests/internal_supervisor_child_fixture.py",
        "tests/readonly_child_isolation.sb",
        "tests/readonly_no_child_contract.py",
        "tests/run_hosted_no_child_fail_closed.py",
        "tests/run_readonly_no_child_supervisor.py",
        "tests/run_readonly_install_deterministic_supervisor.py",
        "tests/run_required_deterministic_supervisor.py",
        "tests/run_required_no_child_profile.py",
        "tests/support.py",
        "tests/synthetic_fixtures.py",
        "tests/test_appserver_protocol.py",
        "tests/test_appserver_runtime.py",
        "tests/test_async_fd_custody.py",
        "tests/test_auth_carrier.py",
        "tests/test_auth_refresh.py",
        "tests/test_checkout.py",
        "tests/test_cli.py",
        "tests/test_codex_executable.py",
        "tests/test_custody.py",
        "tests/test_direct_gate.py",
        "tests/test_evidence.py",
        "tests/test_frozen_source.py",
        "tests/test_git_checkout.py",
        "tests/test_ledger.py",
        "tests/test_lfs.py",
        "tests/test_logs.py",
        "tests/test_no_child_profile.py",
        "tests/test_prompt.py",
        "tests/test_readonly_install_runner.py",
        "tests/test_recovery_cleanup.py",
        "tests/test_review_execution.py",
        "tests/test_runtime_helpers.py",
        "tests/test_runtime_process.py",
        "tests/test_runtime_root_custody.py",
        "tests/test_secureio.py",
        "tests/test_settlement_state.py",
        "tests/test_signal_relay.py",
        "tests/test_supervisor.py",
        "tests/test_wire.py",
        "tests/trusted_mac_gate.py",
        "trusted_mac_gate_sources.index",
    )
)
INDEPENDENT_CODEX_REVIEW_REQUIRED_FILE_PARTS = frozenset(
    relative.parts for relative in INDEPENDENT_CODEX_REVIEW_REQUIRED_FILES
)
INDEPENDENT_CODEX_REVIEW_REQUIRED_DIRECTORY_PARTS = frozenset(
    parent.parts
    for relative in INDEPENDENT_CODEX_REVIEW_REQUIRED_FILES
    for parent in relative.parents
    if parent != Path(".")
)
CANONICAL_REVIEW_REQUIRED_FILES = tuple(
    _path(path)
    for path in (
        "SKILL.md",
        "agents/openai.yaml",
        "references/canonical-claude-lane.md",
        "references/cbth-agent-delivery.md",
        "references/claude-2.1.212-stream-schema.json",
        "references/claude-runtime-trust.md",
        "references/claude-stream-compatibility.json",
        "references/claude-stream-schema.json",
        "references/egress-consent.md",
        "references/github-codex-evidence-authority.md",
        "references/github-codex-terminal-carriers-v1.json",
        "references/github-pr-probes.md",
        "references/local-codex-lane.md",
        "references/pr-readiness.md",
        "references/review-workspace.md",
        "references/review-lane-contracts.md",
        "references/review-prompt-templates.md",
        "references/synthetic-token-fixtures.md",
        "scripts/build_claude_keychain_broker_macos.sh",
        "scripts/install_claude_keychain_broker_macos.sh",
        "scripts/isolated_review",
        "scripts/named_claude_preflight",
        "scripts/named_lane_guard",
        "scripts/validate_claude_stream.py",
        "scripts/review_runtime/__init__.py",
        "scripts/review_runtime/claude_capabilities.py",
        "scripts/review_runtime/claude_code_release.asc",
        "scripts/review_runtime/claude_keychain_broker",
        "scripts/review_runtime/claude_keychain_broker.c",
        "scripts/review_runtime/claude_linux.py",
        "scripts/review_runtime/claude_linux_launcher.c",
        "scripts/review_runtime/claude_provenance.py",
        "scripts/review_runtime/claude_refresh_lock.py",
        "scripts/review_runtime/claude_stream_contract.py",
        "scripts/review_runtime/claude_version_policy.py",
        "scripts/review_runtime/cleanup_worker.py",
        "scripts/review_runtime/cli.py",
        "scripts/review_runtime/common.py",
        "scripts/review_runtime/fd_exec.py",
        "scripts/review_runtime/named_claude_preflight.py",
        "scripts/review_runtime/named_lane.py",
        "scripts/review_runtime/prompt.py",
        "scripts/review_runtime/providers.py",
        "scripts/review_runtime/review_result.py",
        "scripts/review_runtime/review_workspace.py",
        "scripts/review_runtime/state.py",
        "scripts/review_runtime/synthetic-token-catalog.json",
        "scripts/review_runtime/synthetic_tokens.py",
        "scripts/review_runtime/workspace.py",
        "tests/test_claude_capabilities.py",
        "tests/test_claude_linux.py",
        "tests/test_claude_provenance.py",
        "tests/test_claude_refresh_lock.py",
        "tests/test_cli.py",
        "tests/test_common.py",
        "tests/test_contracts.py",
        "tests/test_installer.py",
        "tests/fixtures/ci/canonical.yml",
        "tests/fixtures/ci/private.yml",
        "tests/fixtures/compat/codex-review-gate.yml",
        "tests/test_fd_exec.py",
        "tests/test_github_recovery_contracts.py",
        "tests/test_github_terminal_carriers.py",
        "tests/test_local_codex_lane_contracts.py",
        "tests/test_named_claude_preflight.py",
        "tests/test_named_lane.py",
        "tests/test_providers.py",
        "tests/test_review_result.py",
        "tests/test_review_workspace.py",
        "tests/test_state.py",
        "tests/test_synthetic_tokens.py",
        "tests/test_trusted_mac_gate_manifest.py",
        "tests/test_validate_claude_stream.py",
        "tests/test_workspace.py",
    )
) + tuple(
    INDEPENDENT_CODEX_REVIEW_ROOT / relative
    for relative in INDEPENDENT_CODEX_REVIEW_REQUIRED_FILES
)
RETIRED_REVIEW_REFERENCES = (
    "pr-readiness-review-workflow",
    "external-review-playbook",
    "copilot-review-playbook",
)


EXCLUDED_NAMES = frozenset({".git", ".github", "__pycache__"})
EXCLUDED_SUFFIXES = (".pyc",)
MAX_REGULAR_FILE_OVERLAY_BYTES = 64 * 1024
REGULAR_FILE_OVERLAY_TARGET_MODE = 0o644
REGULAR_FILE_OVERLAY_TEMP_ATTEMPTS = 16
REGULAR_FILE_OVERLAY_BACKUP_PREFIX = ".codex-private-overlay-backup-"
REGULAR_FILE_OVERLAY_RECOVERY_ROOT = Path(".codex-tmp/private-overlay-recovery")
REGULAR_FILE_OVERLAY_RECOVERY_SCOPE_PREFIX = "sync-"
MAX_REGULAR_FILE_OVERLAY_RECOVERY_PATHS = 64
MAX_REGULAR_FILE_OVERLAY_RETAINED_ENTRIES = 64
MAX_REGULAR_FILE_OVERLAY_TREE_ENTRIES = 4 * 1024
MAX_REGULAR_FILE_OVERLAY_TREE_BYTES = 64 * 1024 * 1024
MAX_REGULAR_FILE_OVERLAY_TREE_DEPTH = 64


def _is_text_candidate(path: Path, extensions: tuple[str, ...]) -> bool:
    return path.suffix in extensions or path.name in {"SKILL.md", "README.md"}


def _is_ignored_name(name: str, ignored_names: frozenset[str]) -> bool:
    return name in ignored_names or any(
        name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES
    )


def _is_ignored_relative(path: Path, root: Path, ignored_names: frozenset[str]) -> bool:
    return any(
        _is_ignored_name(part, ignored_names) for part in path.relative_to(root).parts
    )


def _reject_unignored_symlinks(path: Path, ignored_names: frozenset[str]) -> None:
    if path.is_symlink():
        raise SyncError(f"refusing to sync symlink: {path}")
    if path.is_dir():
        for child in path.rglob("*"):
            if _is_ignored_relative(child, path, ignored_names):
                continue
            if child.is_symlink():
                raise SyncError(f"refusing to sync nested symlink: {child}")


def _normalize_public_staging_modes(staging: Path) -> None:
    def normalize(candidate: Path) -> None:
        metadata = candidate.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            candidate.chmod(0o755)
        elif stat.S_ISREG(metadata.st_mode):
            executable = bool(metadata.st_mode & 0o111)
            candidate.chmod(0o755 if executable else 0o644)
        else:
            raise SyncError(f"unsupported staged public source type: {candidate}")

    normalize(staging)
    if staging.is_dir():
        for candidate in staging.rglob("*"):
            normalize(candidate)


def _ensure_safe_target(repo_root: Path, target: Path) -> None:
    repo_root = repo_root.resolve()
    target = target.absolute()
    try:
        target.relative_to(repo_root)
    except ValueError as exc:
        raise SyncError(f"sync target escapes repository root: {target}") from exc

    ancestor = target.parent
    ancestors: list[Path] = []
    while ancestor != repo_root:
        ancestors.append(ancestor)
        if ancestor.parent == ancestor:
            raise SyncError(f"sync target escapes repository root: {target}")
        ancestor = ancestor.parent
    for path in reversed(ancestors):
        if path.is_symlink():
            raise SyncError(f"refusing sync target ancestor symlink: {path}")
    if target.is_symlink():
        raise SyncError(f"refusing sync target symlink: {target}")


def _ensure_safe_source(source_repo_root: Path, source: Path) -> None:
    source_repo_root_raw = source_repo_root.absolute()
    source = source.absolute()
    try:
        source.relative_to(source_repo_root_raw)
    except ValueError as exc:
        raise SyncError(
            f"sync source escapes source repository root: {source}"
        ) from exc

    if source_repo_root_raw.is_symlink():
        raise SyncError(
            f"refusing source repository root symlink: {source_repo_root_raw}"
        )
    ancestor = source
    ancestors: list[Path] = []
    while ancestor != source_repo_root_raw:
        ancestors.append(ancestor)
        if ancestor.parent == ancestor:
            raise SyncError(f"sync source escapes source repository root: {source}")
        ancestor = ancestor.parent
    for path in reversed(ancestors):
        if path.is_symlink():
            raise SyncError(f"refusing sync source ancestor symlink: {path}")

    source_repo_root_resolved = source_repo_root_raw.resolve(strict=True)
    source_resolved = source.resolve(strict=True)
    try:
        source_resolved.relative_to(source_repo_root_resolved)
    except ValueError as exc:
        raise SyncError(
            f"sync source resolves outside source repository root: {source}"
        ) from exc


def _copy_source_to_staging(
    source: Path, staging: Path, *, exclude_names: tuple[str, ...] = ()
) -> None:
    ignored_names = EXCLUDED_NAMES | frozenset(exclude_names)
    _reject_unignored_symlinks(source, ignored_names)
    if source.is_dir():
        shutil.copytree(
            source,
            staging,
            ignore=lambda _dir, names: [
                name for name in names if _is_ignored_name(name, ignored_names)
            ],
        )
        _normalize_public_staging_modes(staging)
        return
    if source.is_file():
        staging.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, staging)
        _normalize_public_staging_modes(staging)
        return
    raise SyncError(f"unsupported source type: {source}")


def _replace_target(target: Path, staging: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if target.exists():
        backup = Path(
            tempfile.mkdtemp(prefix=f".{target.name}.backup.", dir=target.parent)
        )
        backup.rmdir()
        target.rename(backup)
    try:
        staging.rename(target)
    except Exception:
        if backup is not None and backup.exists() and not target.exists():
            backup.rename(target)
        raise
    if backup is not None:
        if backup.is_dir():
            shutil.rmtree(backup)
        else:
            backup.unlink()


def _remove_retired_targets(repo_root: Path) -> None:
    for relative in RETIRED_TARGETS:
        target = repo_root / relative
        _ensure_safe_target(repo_root, target)
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()


def _require_retired_targets_absent(
    stack: contextlib.ExitStack,
    repo_binding: _PinnedRegularFileOverlayDirectory,
) -> None:
    for relative in RETIRED_TARGETS:
        chain = _pin_or_create_regular_file_overlay_descendant_chain(
            stack,
            repo_binding,
            relative.parent,
            label="retired target parent",
        )
        parent = chain[-1]
        if _regular_file_overlay_entry_exists(parent.descriptor, relative.name):
            raise SyncError(
                "locked sync requires retired target to be removed by an explicit "
                f"migration: {relative}"
            )


def _validate_canonical_review_exact_tree_inventories(
    file_parts: set[tuple[str, ...]],
    *,
    surface: str,
) -> None:
    prefix = INDEPENDENT_CODEX_REVIEW_ROOT.parts
    actual = {
        parts[len(prefix) :]
        for parts in file_parts
        if parts[: len(prefix)] == prefix and len(parts) > len(prefix)
    }
    expected = {relative.parts for relative in INDEPENDENT_CODEX_REVIEW_REQUIRED_FILES}
    if actual == expected:
        return

    missing = sorted("/".join(parts) for parts in expected - actual)
    unexpected = sorted("/".join(parts) for parts in actual - expected)
    raise SyncError(
        "canonical review exact tree inventory mismatch at "
        f"{surface}: missing={missing}; unexpected={unexpected}"
    )


def _validate_canonical_review_raw_tree_entry(
    relative_parts: tuple[str, ...],
    *,
    surface: str,
) -> None:
    prefix = INDEPENDENT_CODEX_REVIEW_ROOT.parts
    if relative_parts[: len(prefix)] != prefix or len(relative_parts) <= len(prefix):
        return
    independent_relative = relative_parts[len(prefix) :]
    if (
        independent_relative in INDEPENDENT_CODEX_REVIEW_REQUIRED_FILE_PARTS
        or independent_relative in INDEPENDENT_CODEX_REVIEW_REQUIRED_DIRECTORY_PARTS
    ):
        return
    unexpected = "/".join(independent_relative)
    raise SyncError(
        "canonical review raw exact tree inventory mismatch at "
        f"{surface}: unexpected={unexpected}"
    )


def _validate_canonical_review_target_contents(target: Path) -> None:
    if not target.exists():
        return
    for path in target.rglob("*"):
        _validate_canonical_review_raw_tree_entry(
            path.relative_to(target).parts,
            surface=str(target),
        )
    for relative in CANONICAL_REVIEW_REQUIRED_FILES:
        if not (target / relative).is_file():
            raise SyncError(
                f"canonical review target missing required file: {relative}"
            )
    file_parts = {
        path.relative_to(target).parts
        for path in target.rglob("*")
        if path.is_file() and not _is_ignored_relative(path, target, EXCLUDED_NAMES)
    }
    _validate_canonical_review_exact_tree_inventories(
        file_parts,
        surface=str(target),
    )
    for path in sorted(target.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        for reference in RETIRED_REVIEW_REFERENCES:
            if reference in text:
                raise SyncError(
                    "canonical review target retains retired reference "
                    f"{reference!r} in {path.relative_to(target)}"
                )


def _validate_canonical_review_target(repo_root: Path) -> None:
    _validate_canonical_review_target_contents(repo_root / CANONICAL_REVIEW_TARGET)


def _personal_agents_review_guidance_state(data: bytes) -> str:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SyncError("personal AGENTS guidance is not valid UTF-8") from exc

    legacy_consent_count = data.count(PERSONAL_AGENTS_LEGACY_CONSENT_LINE)
    current_consent_count = data.count(PERSONAL_AGENTS_CURRENT_CONSENT_LINE)
    legacy_start_count = data.count(PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_START)
    current_block_count = data.count(PERSONAL_AGENTS_CURRENT_REVIEW_BLOCK)
    boundary_count = data.count(PERSONAL_AGENTS_REVIEW_BLOCK_BOUNDARY)

    if (
        legacy_consent_count == 1
        and current_consent_count == 0
        and legacy_start_count == 1
        and current_block_count == 0
        and boundary_count == 1
    ):
        consent = data.index(PERSONAL_AGENTS_LEGACY_CONSENT_LINE)
        start = data.index(PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_START)
        boundary = data.index(PERSONAL_AGENTS_REVIEW_BLOCK_BOUNDARY)
        consent_starts_line = consent == 0 or data[consent - 1 : consent] == b"\n"
        block_starts_line = start == 0 or data[start - 1 : start] == b"\n"
        if consent_starts_line and block_starts_line and consent < start < boundary:
            legacy_block = data[start:boundary]
            if (
                hashlib.sha256(legacy_block).hexdigest()
                == PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_SHA256
            ):
                return "legacy"

    if (
        legacy_consent_count == 0
        and current_consent_count == 1
        and legacy_start_count == 0
        and current_block_count == 1
        and boundary_count == 1
    ):
        consent = data.index(PERSONAL_AGENTS_CURRENT_CONSENT_LINE)
        start = data.index(PERSONAL_AGENTS_CURRENT_REVIEW_BLOCK)
        boundary = data.index(PERSONAL_AGENTS_REVIEW_BLOCK_BOUNDARY)
        consent_starts_line = consent == 0 or data[consent - 1 : consent] == b"\n"
        block_starts_line = start == 0 or data[start - 1 : start] == b"\n"
        if (
            consent_starts_line
            and block_starts_line
            and consent < start
            and start + len(PERSONAL_AGENTS_CURRENT_REVIEW_BLOCK) == boundary
        ):
            return "current"

    raise SyncError(
        "personal AGENTS review guidance must be the exact legacy or migrated "
        "state; restore or reconcile personal_codex/AGENTS.md, then rerun sync"
    )


def _migrated_personal_agents_bytes(data: bytes) -> bytes:
    state = _personal_agents_review_guidance_state(data)
    if state == "current":
        return data

    start = data.index(PERSONAL_AGENTS_LEGACY_REVIEW_BLOCK_START)
    boundary = data.index(PERSONAL_AGENTS_REVIEW_BLOCK_BOUNDARY)
    migrated = data[:start] + PERSONAL_AGENTS_CURRENT_REVIEW_BLOCK + data[boundary:]
    migrated = migrated.replace(
        PERSONAL_AGENTS_LEGACY_CONSENT_LINE,
        PERSONAL_AGENTS_CURRENT_CONSENT_LINE,
        1,
    )
    if _personal_agents_review_guidance_state(migrated) != "current":
        raise SyncError("personal AGENTS review-guidance migration did not converge")
    return migrated


def _validate_no_retired_review_references(
    repo_root: Path,
    *,
    excluded_targets: tuple[Path, ...] = (),
) -> None:
    overlay_root = repo_root / "personal_codex"
    if not overlay_root.exists():
        return
    for path in sorted(overlay_root.rglob("*.md")):
        relative = path.relative_to(repo_root)
        if any(
            relative == excluded or excluded in relative.parents
            for excluded in excluded_targets
        ):
            continue
        text = path.read_text(encoding="utf-8")
        for reference in RETIRED_REVIEW_REFERENCES:
            if reference in text:
                raise SyncError(
                    "private overlay retains retired review reference "
                    f"{reference!r} in {relative}"
                )


def _apply_replacements(
    path: Path,
    relative: Path,
    replacements: tuple[Replacement, ...],
) -> dict[int, int]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {}
    changed = False
    found: dict[int, int] = {}
    for index, replacement in enumerate(replacements):
        if replacement.path is not None and replacement.path != relative:
            continue
        if replacement.old not in text:
            continue
        found[index] = text.count(replacement.old)
        text = text.replace(replacement.old, replacement.new)
        changed = True
    if changed:
        path.write_text(text, encoding="utf-8")
    return found


def _text_candidate_paths(target: Path, rule: SyncRule) -> list[Path]:
    paths = (
        [target]
        if target.is_file()
        else sorted(path for path in target.rglob("*") if path.is_file())
    )
    return [path for path in paths if _is_text_candidate(path, rule.text_extensions)]


def _validate_replacement_excluded_paths(rules: tuple[SyncRule, ...]) -> None:
    for rule in rules:
        excluded_paths = rule.replacement_excluded_paths
        if excluded_paths and not rule.replacements:
            raise SyncError(
                f"replacement-excluded paths require replacements: {rule.target}"
            )
        seen: set[Path] = set()
        path_scoped_replacements = {
            replacement.path
            for replacement in rule.replacements
            if replacement.path is not None
        }
        for relative in excluded_paths:
            if (
                not isinstance(relative, Path)
                or relative == Path(".")
                or relative.is_absolute()
                or ".." in relative.parts
            ):
                raise SyncError(
                    f"unsafe replacement-excluded path for {rule.target}: {relative}"
                )
            if relative in seen:
                raise SyncError(
                    f"duplicate replacement-excluded path for {rule.target}: {relative}"
                )
            if relative in path_scoped_replacements:
                raise SyncError(
                    "replacement-excluded path conflicts with path-scoped "
                    f"replacement for {rule.target}: {relative}"
                )
            seen.add(relative)


def _validate_replacement_excluded_candidates(
    rule: SyncRule,
    candidates: set[Path],
    *,
    surface: str,
) -> None:
    for relative in rule.replacement_excluded_paths:
        if relative not in candidates:
            raise SyncError(
                "replacement-excluded path is missing or not a text candidate at "
                f"{surface} for {rule.target}: {relative}"
            )


def _apply_rule_replacements(target: Path, rule: SyncRule) -> None:
    if not rule.replacements:
        return
    candidates = _text_candidate_paths(target, rule)
    relative_candidates = {
        path.relative_to(target) if target.is_dir() else Path(path.name)
        for path in candidates
    }
    _validate_replacement_excluded_candidates(
        rule,
        relative_candidates,
        surface="staged target",
    )
    found: dict[int, int] = {}
    for path in candidates:
        relative = path.relative_to(target) if target.is_dir() else Path(path.name)
        if relative in rule.replacement_excluded_paths:
            continue
        for index, count in _apply_replacements(
            path,
            relative,
            rule.replacements,
        ).items():
            found[index] = found.get(index, 0) + count
    _validate_replacement_counts(rule, found)


def _validate_replacement_counts(rule: SyncRule, found: dict[int, int]) -> None:
    for index, replacement in enumerate(rule.replacements):
        actual_count = found.get(index, 0)
        if replacement.required_count is not None:
            if actual_count != replacement.required_count:
                raise SyncError(
                    "required replacement count mismatch for "
                    f"{rule.target}: {replacement.old!r} "
                    f"({actual_count} != {replacement.required_count})"
                )
        elif replacement.required and actual_count == 0:
            raise SyncError(
                f"required replacement did not match for {rule.target}: {replacement.old!r}"
            )


def _reject_forbidden_residuals(target: Path, rule: SyncRule) -> None:
    if not rule.forbidden_residuals:
        return
    for path in _text_candidate_paths(target, rule):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for residual in rule.forbidden_residuals:
            if residual in text:
                raise SyncError(
                    f"forbidden residual {residual!r} remains in {path.relative_to(target)}"
                )


def _private_bug_triage_direct_assignments(
    tree: ast.Module,
) -> tuple[dict[str, ast.Assign], set[int]]:
    protected = {"ALLOWED_HOSTS", "AUTH_PROFILES"}
    assignments: dict[str, list[ast.Assign]] = {name: [] for name in protected}
    allowed_target_ids: set[int] = set()
    for statement in tree.body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            continue
        target = statement.targets[0]
        if not isinstance(target, ast.Name) or target.id not in protected:
            continue
        assignments[target.id].append(statement)
        allowed_target_ids.add(id(target))

    exact: dict[str, ast.Assign] = {}
    for name in sorted(protected):
        matches = assignments[name]
        if len(matches) != 1:
            raise SyncError(
                "private bug-triage policy requires exactly one module-level direct "
                f"assignment to {name} ({len(matches)} != 1)"
            )
        exact[name] = matches[0]
    return exact, allowed_target_ids


def _private_bug_triage_literal_string(node: ast.AST, *, label: str) -> str:
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        raise SyncError(f"private bug-triage {label} must be a string literal")
    return node.value


def _private_bug_triage_validate_policy_values(
    assignments: dict[str, ast.Assign],
) -> None:
    allowed_hosts_value = assignments["ALLOWED_HOSTS"].value
    if not (
        isinstance(allowed_hosts_value, ast.Call)
        and isinstance(allowed_hosts_value.func, ast.Name)
        and allowed_hosts_value.func.id == "frozenset"
        and len(allowed_hosts_value.args) == 1
        and not allowed_hosts_value.keywords
        and isinstance(allowed_hosts_value.args[0], ast.Set)
    ):
        raise SyncError(
            "private bug-triage ALLOWED_HOSTS must be one explicit frozenset literal"
        )
    host_nodes = allowed_hosts_value.args[0].elts
    hosts = frozenset(
        _private_bug_triage_literal_string(node, label="allowed host")
        for node in host_nodes
    )
    if len(host_nodes) != len(hosts) or hosts != PRIVATE_BUG_TRIAGE_ALLOWED_HOSTS:
        raise SyncError("private bug-triage ALLOWED_HOSTS policy does not match")

    auth_value = assignments["AUTH_PROFILES"].value
    if not isinstance(auth_value, ast.Dict) or len(auth_value.keys) != len(
        PRIVATE_BUG_TRIAGE_AUTH_PROFILES
    ):
        raise SyncError(
            "private bug-triage AUTH_PROFILES must be one exact dict literal"
        )
    profiles: dict[str, tuple[str, str]] = {}
    for key_node, value_node in zip(auth_value.keys, auth_value.values, strict=True):
        if key_node is None:
            raise SyncError(
                "private bug-triage AUTH_PROFILES cannot use dict unpacking"
            )
        key = _private_bug_triage_literal_string(key_node, label="auth profile name")
        if (
            not isinstance(value_node, ast.Tuple)
            or len(value_node.elts) != 2
            or any(isinstance(element, ast.Starred) for element in value_node.elts)
        ):
            raise SyncError(
                f"private bug-triage auth profile {key!r} must be a two-string tuple"
            )
        value = tuple(
            _private_bug_triage_literal_string(
                element,
                label=f"auth profile {key!r} environment name",
            )
            for element in value_node.elts
        )
        if key in profiles:
            raise SyncError(f"private bug-triage duplicate auth profile: {key}")
        profiles[key] = value
    if profiles != PRIVATE_BUG_TRIAGE_AUTH_PROFILES:
        raise SyncError("private bug-triage AUTH_PROFILES policy does not match")


def _private_bug_triage_references_name(
    node: ast.AST,
    names: frozenset[str],
) -> bool:
    return any(
        isinstance(candidate, ast.Name) and candidate.id in names
        for candidate in ast.walk(node)
    )


def _private_bug_triage_is_allowed_sorted_call(node: ast.Call) -> bool:
    return (
        isinstance(node.func, ast.Name)
        and node.func.id == "sorted"
        and len(node.args) == 1
        and not node.keywords
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "AUTH_PROFILES"
    )


def _private_bug_triage_is_allowed_dynamic_reflection_call(node: ast.Call) -> bool:
    return (
        isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) == 3
        and not node.keywords
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "signal"
        and isinstance(node.args[1], ast.Name)
        and node.args[1].id == "name"
        and isinstance(node.args[2], ast.Constant)
        and node.args[2].value is None
    )


def _private_bug_triage_is_allowed_direct_reflection_call(
    node: ast.Call,
    forbidden_reflection_attributes: frozenset[str],
) -> bool:
    if not isinstance(node.func, ast.Name) or node.func.id not in {
        "delattr",
        "getattr",
        "setattr",
    }:
        return False
    if _private_bug_triage_is_allowed_dynamic_reflection_call(node):
        return True
    return (
        len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
        and node.args[1].value not in forbidden_reflection_attributes
    )


def _private_bug_triage_validate_no_policy_mutation(
    tree: ast.Module,
    allowed_target_ids: set[int],
    allowed_definition_ids: set[int],
) -> None:
    protected = frozenset({"ALLOWED_HOSTS", "AUTH_PROFILES"})
    protected_bindings = protected | {"_ensure_allowed_url"}
    reserved = protected_bindings | {"frozenset", "sorted"}
    reserved_builtins = frozenset({"frozenset", "sorted"})
    forbidden_dynamic_calls = frozenset(
        {"__import__", "compile", "eval", "exec", "globals", "locals", "vars"}
    )
    bounded_reflection_calls = frozenset({"delattr", "getattr", "setattr"})
    indirect_reflection_attributes = frozenset(
        {
            "__delattr__",
            "__builtins__",
            "__dict__",
            "__getattribute__",
            "__globals__",
            "__setattr__",
            "_current_frames",
            "_getframe",
            "f_builtins",
            "f_globals",
            "f_locals",
        }
    )
    policy_sensitive_builtins = forbidden_dynamic_calls | bounded_reflection_calls
    forbidden_reflection_attributes = (
        policy_sensitive_builtins | indirect_reflection_attributes | {"modules"}
    )
    allowed_reflection_name_load_ids = {
        id(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _private_bug_triage_is_allowed_direct_reflection_call(
            node, forbidden_reflection_attributes
        )
    }
    allowed_guard_name_load_ids = {
        id(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_ensure_allowed_url"
    }
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and node.id in protected_bindings
            and isinstance(node.ctx, (ast.Store, ast.Del))
            and id(node) not in allowed_target_ids
        ):
            raise SyncError(f"private bug-triage policy forbids rebinding {node.id}")
        if (
            isinstance(node, ast.Name)
            and node.id in reserved_builtins
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ):
            raise SyncError(f"private bug-triage policy forbids shadowing {node.id}")
        if isinstance(node, ast.Attribute) and node.attr in reserved:
            raise SyncError("private bug-triage policy forbids reserved attributes")
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Load)
            and node.attr in bounded_reflection_calls
        ):
            raise SyncError(
                "private bug-triage policy forbids reflection builtin attribute loads"
            )
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Load)
            and (node.attr in indirect_reflection_attributes or node.attr == "modules")
        ):
            raise SyncError(
                "private bug-triage policy forbids indirect builtin namespace/reflection access"
            )
        if isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(
            node.ctx, (ast.Store, ast.Del)
        ):
            if _private_bug_triage_references_name(node, protected_bindings):
                raise SyncError(
                    "private bug-triage policy forbids attribute or subscript mutation"
                )
        if isinstance(node, ast.AugAssign) and _private_bug_triage_references_name(
            node.target, protected_bindings
        ):
            raise SyncError("private bug-triage policy forbids augmented mutation")
        if isinstance(node, (ast.Global, ast.Nonlocal)) and reserved.intersection(
            node.names
        ):
            raise SyncError(
                "private bug-triage policy forbids global/nonlocal policy names"
            )
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in reserved and id(node) not in allowed_definition_ids:
                raise SyncError(
                    f"private bug-triage policy forbids shadowing {node.name}"
                )
        if isinstance(node, ast.arg) and node.arg in reserved:
            raise SyncError(
                f"private bug-triage policy forbids argument shadowing {node.arg}"
            )
        if isinstance(node, ast.alias) and (
            node.name.split(".", 1)[0] in reserved or node.asname in reserved
        ):
            raise SyncError("private bug-triage policy forbids imported policy names")
        if isinstance(node, ast.Import) and any(
            alias.name.split(".", 1)[0] == "builtins" for alias in node.names
        ):
            raise SyncError(
                "private bug-triage policy forbids importing the builtins module"
            )
        if isinstance(node, ast.ImportFrom) and node.module == "builtins":
            raise SyncError("private bug-triage policy forbids importing from builtins")
        if isinstance(node, ast.ImportFrom) and any(
            alias.name == "*" for alias in node.names
        ):
            raise SyncError("private bug-triage policy forbids wildcard imports")
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "sys"
            and any(alias.name == "modules" for alias in node.names)
        ):
            raise SyncError(
                "private bug-triage policy forbids importing the sys module registry"
            )
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "sys"
            and any(
                alias.name in {"_current_frames", "_getframe"} for alias in node.names
            )
        ):
            raise SyncError(
                "private bug-triage policy forbids importing sys frame reflection"
            )
        if isinstance(node, ast.ExceptHandler) and node.name in reserved:
            raise SyncError(
                "private bug-triage policy forbids exception-name shadowing"
            )
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name in reserved:
            raise SyncError("private bug-triage policy forbids pattern capture")
        if isinstance(node, ast.MatchMapping) and node.rest in reserved:
            raise SyncError("private bug-triage policy forbids mapping-rest capture")
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and any(name in node.value for name in reserved)
        ):
            raise SyncError("private bug-triage policy forbids dynamic name lookup")
        if not isinstance(node, ast.Call):
            if (
                isinstance(node, ast.Name)
                and node.id in forbidden_dynamic_calls
                and isinstance(node.ctx, ast.Load)
            ):
                raise SyncError(
                    f"private bug-triage policy forbids dynamic builtin reference {node.id}"
                )
            if (
                isinstance(node, ast.Name)
                and node.id in bounded_reflection_calls
                and isinstance(node.ctx, ast.Load)
                and id(node) not in allowed_reflection_name_load_ids
            ):
                raise SyncError(
                    f"private bug-triage policy forbids unapproved reflection builtin reference {node.id}"
                )
            if (
                isinstance(node, ast.Name)
                and node.id in {"builtins", "__builtins__"}
                and isinstance(node.ctx, ast.Load)
            ):
                raise SyncError(
                    f"private bug-triage policy forbids builtin namespace reference {node.id}"
                )
            if (
                isinstance(node, ast.Name)
                and node.id == "_ensure_allowed_url"
                and isinstance(node.ctx, ast.Load)
                and id(node) not in allowed_guard_name_load_ids
            ):
                raise SyncError(
                    "private bug-triage policy forbids unapproved "
                    "_ensure_allowed_url references"
                )
            continue
        if isinstance(node.func, ast.Name) and node.func.id in forbidden_dynamic_calls:
            raise SyncError(
                f"private bug-triage policy forbids dynamic call {node.func.id}()"
            )
        if isinstance(node.func, ast.Attribute) and (
            node.func.attr in bounded_reflection_calls
            or (
                node.func.attr in forbidden_dynamic_calls
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"builtins", "__builtins__"}
            )
        ):
            raise SyncError(
                "private bug-triage policy forbids qualified dynamic/reflection builtin calls"
            )
        if (
            isinstance(node.func, ast.Name)
            and node.func.id in bounded_reflection_calls
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in forbidden_reflection_attributes
        ):
            raise SyncError(
                "private bug-triage policy forbids reflection namespace/builtin acquisition"
            )
        if (
            isinstance(node.func, ast.Name)
            and node.func.id in bounded_reflection_calls
            and (
                len(node.args) < 2
                or not isinstance(node.args[1], ast.Constant)
                or not isinstance(node.args[1].value, str)
            )
            and not _private_bug_triage_is_allowed_dynamic_reflection_call(node)
        ):
            raise SyncError(
                "private bug-triage policy requires literal reflection attributes"
            )
        if isinstance(node.func, ast.Attribute) and _private_bug_triage_references_name(
            node.func.value, protected_bindings
        ):
            raise SyncError("private bug-triage policy forbids method mutation")


def _private_bug_triage_is_host_membership_condition(node: ast.Compare) -> bool:
    if (
        len(node.ops) != 1
        or not isinstance(node.ops[0], ast.NotIn)
        or len(node.comparators) != 1
        or not isinstance(node.comparators[0], ast.Name)
        or node.comparators[0].id != "ALLOWED_HOSTS"
    ):
        return False
    call = node.left
    return (
        isinstance(call, ast.Call)
        and not call.args
        and not call.keywords
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "lower"
        and isinstance(call.func.value, ast.Attribute)
        and call.func.value.attr == "hostname"
        and isinstance(call.func.value.value, ast.Name)
        and call.func.value.value.id == "parsed"
    )


def _private_bug_triage_validate_url_guard(
    function: ast.FunctionDef,
) -> ast.Compare:
    if function.decorator_list:
        raise SyncError(
            "private bug-triage policy forbids _ensure_allowed_url decorators"
        )

    guards = [
        statement
        for statement in function.body
        if isinstance(statement, ast.If)
        and isinstance(statement.test, ast.Compare)
        and _private_bug_triage_is_host_membership_condition(statement.test)
    ]
    if len(guards) != 1:
        raise SyncError(
            "private bug-triage policy requires one direct lowercase "
            "ALLOWED_HOSTS guard"
        )
    guard = guards[0]

    parsed_assignments = [
        statement
        for statement in function.body
        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and statement.targets[0].id == "parsed"
            and isinstance(statement.value, ast.Call)
            and len(statement.value.args) == 1
            and not statement.value.keywords
            and isinstance(statement.value.args[0], ast.Name)
            and statement.value.args[0].id == "url"
            and isinstance(statement.value.func, ast.Attribute)
            and statement.value.func.attr == "urlparse"
            and isinstance(statement.value.func.value, ast.Attribute)
            and statement.value.func.value.attr == "parse"
            and isinstance(statement.value.func.value.value, ast.Name)
            and statement.value.func.value.value.id == "urllib"
        )
    ]
    if len(parsed_assignments) != 1:
        raise SyncError(
            "private bug-triage _ensure_allowed_url requires one direct "
            "parsed = urllib.parse.urlparse(url) assignment"
        )
    parsed_assignment = parsed_assignments[0]
    allowed_parsed_store_id = id(parsed_assignment.targets[0])
    rebound_inputs = [
        node
        for node in ast.walk(function)
        if (
            isinstance(node, ast.Name)
            and node.id in {"parsed", "url"}
            and isinstance(node.ctx, (ast.Store, ast.Del))
            and id(node) != allowed_parsed_store_id
        )
    ]
    if rebound_inputs or any(
        isinstance(node, (ast.Global, ast.Nonlocal))
        and {"parsed", "url"}.intersection(node.names)
        for node in ast.walk(function)
    ):
        raise SyncError(
            "private bug-triage _ensure_allowed_url forbids parsed/url rebinding"
        )
    if function.body.index(parsed_assignment) >= function.body.index(guard):
        raise SyncError(
            "private bug-triage URL parse assignment must precede the host guard"
        )
    if guard.orelse or len(guard.body) != 1 or not isinstance(guard.body[0], ast.Raise):
        raise SyncError("private bug-triage ALLOWED_HOSTS guard must directly raise")

    returns = [node for node in ast.walk(function) if isinstance(node, ast.Return)]
    if (
        len(returns) != 1
        or returns[0] not in function.body
        or function.body.index(guard) >= function.body.index(returns[0])
        or not isinstance(returns[0].value, ast.Name)
        or returns[0].value.id != "parsed"
    ):
        raise SyncError(
            "private bug-triage ALLOWED_HOSTS guard must precede the sole "
            "direct parsed return"
        )
    if any(isinstance(node, (ast.Yield, ast.YieldFrom)) for node in ast.walk(function)):
        raise SyncError("private bug-triage _ensure_allowed_url cannot be a generator")
    return guard.test


def _private_bug_triage_validate_policy_loads(
    tree: ast.Module,
    host_condition: ast.Compare,
) -> None:
    protected = frozenset({"ALLOWED_HOSTS", "AUTH_PROFILES"})
    allowed_load_ids = {id(host_condition.comparators[0])}

    auth_functions = [
        statement
        for statement in tree.body
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
        and statement.name == "_add_basic_auth"
    ]
    if len(auth_functions) != 1:
        raise SyncError(
            "private bug-triage policy requires exactly one _add_basic_auth"
        )
    auth_lookups = [
        node
        for node in ast.walk(auth_functions[0])
        if isinstance(node, ast.Subscript)
        and isinstance(node.ctx, ast.Load)
        and isinstance(node.value, ast.Name)
        and node.value.id == "AUTH_PROFILES"
        and isinstance(node.slice, ast.Name)
        and node.slice.id == "auth_profile"
    ]
    if len(auth_lookups) != 1:
        raise SyncError(
            "private bug-triage policy requires one AUTH_PROFILES[auth_profile] load"
        )
    allowed_load_ids.add(id(auth_lookups[0].value))

    sorted_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _private_bug_triage_is_allowed_sorted_call(node)
    ]
    if len(sorted_calls) != 3:
        raise SyncError(
            "private bug-triage policy requires exactly three sorted(AUTH_PROFILES) "
            f"loads ({len(sorted_calls)} != 3)"
        )
    allowed_load_ids.update(id(call.args[0]) for call in sorted_calls)

    unexpected = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id in protected
        and id(node) not in allowed_load_ids
    ]
    if unexpected:
        locations = ", ".join(
            f"{node.id}@{getattr(node, 'lineno', '?')}" for node in unexpected
        )
        raise SyncError(
            f"private bug-triage policy forbids unexpected policy loads: {locations}"
        )


def _validate_private_bug_triage_target_contents(target: Path) -> None:
    """Run structural diagnostics and reject bytes outside the reviewed helper."""

    script = target / "scripts/jenkins_artifact_probe.py"
    if not script.is_file():
        raise SyncError(f"private bug-triage target missing helper: {script}")
    try:
        payload = script.read_bytes()
        text = payload.decode("utf-8")
        tree = ast.parse(text, filename=str(script))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise SyncError(f"cannot parse private bug-triage helper: {exc}") from exc
    assignments, allowed_target_ids = _private_bug_triage_direct_assignments(tree)
    _private_bug_triage_validate_policy_values(assignments)

    functions = [
        statement
        for statement in tree.body
        if isinstance(statement, ast.FunctionDef)
        and statement.name == "_ensure_allowed_url"
    ]
    if len(functions) != 1:
        raise SyncError(
            "private bug-triage policy requires exactly one _ensure_allowed_url"
        )
    function = functions[0]
    _private_bug_triage_validate_no_policy_mutation(
        tree,
        allowed_target_ids,
        {id(function)},
    )
    host_condition = _private_bug_triage_validate_url_guard(function)
    _private_bug_triage_validate_policy_loads(tree, host_condition)

    # The AST checks above are non-authoritative structural diagnostics.  The
    # capability-closed admission boundary is the exact digest plus the
    # descriptor-bound scripts inventory validated from the captured manifest.
    helper_digest = hashlib.sha256(payload).hexdigest()
    if helper_digest != PRIVATE_BUG_TRIAGE_REVIEWED_HELPER_SHA256:
        raise SyncError(
            "private bug-triage reviewed helper payload digest mismatch "
            f"({helper_digest} != {PRIVATE_BUG_TRIAGE_REVIEWED_HELPER_SHA256})"
        )

    scripts_dir = script.parent
    unexpected_entries: list[str] = []
    for entry in scripts_dir.iterdir():
        if entry == script:
            continue
        if entry.name == "__pycache__" and entry.is_dir() and not entry.is_symlink():
            continue
        unexpected_entries.append(entry.name)
    if unexpected_entries:
        raise SyncError(
            "private bug-triage scripts directory contains unreviewed entries: "
            + ", ".join(sorted(unexpected_entries))
        )


def _require_overlay_relative_path(path: Path, *, field: str) -> None:
    if path == Path(".") or path.is_absolute() or ".." in path.parts:
        raise SyncError(f"unsafe regular-file overlay {field}: {path}")


def _validate_regular_file_overlay_targets(rules: tuple[SyncRule, ...]) -> None:
    targets: set[Path] = set()
    for rule in rules:
        for overlay in rule.regular_file_overlays:
            _require_overlay_relative_path(overlay.source, field="source")
            _require_overlay_relative_path(overlay.target, field="target")
            output_target = rule.target / overlay.target
            if output_target in targets:
                raise SyncError(
                    f"duplicate regular-file overlay target: {output_target}"
                )
            targets.add(output_target)


def _overlay_file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
    )


def _overlay_file_object_identity(
    metadata: os.stat_result,
) -> tuple[int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
    )


def _overlay_file_access_policy(
    metadata: os.stat_result,
) -> tuple[int, int, int]:
    return (
        metadata.st_uid,
        stat.S_IMODE(metadata.st_mode),
        metadata.st_nlink,
    )


def _overlay_plain_file_content_identity(data: bytes) -> tuple[int, str]:
    return len(data), hashlib.sha256(data).hexdigest()


def _overlay_file_timestamp_hint(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_mtime_ns, metadata.st_ctime_ns


def _overlay_file_content_identity(
    metadata: os.stat_result,
) -> tuple[int, int, int, int, int, int, int, int]:
    # Legacy copy and manifest callers still use timestamps as a change signal.
    # A caller may ignore that signal only when it performs a bounded semantic
    # reread of object identity, content, and access policy instead.
    return (
        *_overlay_file_identity(metadata),
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _validate_overlay_regular_file(
    metadata: os.stat_result,
    *,
    label: str,
    path: Path,
) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise SyncError(f"regular-file overlay {label} is not a regular file: {path}")
    if metadata.st_nlink != 1:
        raise SyncError(
            f"regular-file overlay {label} must have exactly one hard link: {path}"
        )
    if metadata.st_uid != os.getuid():
        raise SyncError(
            f"regular-file overlay {label} must be owned by the current user: {path}"
        )
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise SyncError(
            f"regular-file overlay {label} must not be group or other writable: {path}"
        )


def _regular_file_overlay_directory_flags(*, label: str) -> int:
    if (
        not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or os.open not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
        or os.stat not in os.supports_follow_symlinks
    ):
        raise SyncError(
            f"secure regular-file overlay {label} path traversal is unavailable"
        )
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _open_regular_file_overlay_root(
    root: Path,
    *,
    label: str,
) -> int:
    if not root.is_absolute() or root.anchor != os.sep:
        raise SyncError(f"regular-file overlay {label} root must be absolute: {root}")
    flags = _regular_file_overlay_directory_flags(label=label)
    descriptor: int | None = None
    try:
        descriptor = os.open(os.sep, flags)
        for component in root.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            previous_descriptor = descriptor
            descriptor = next_descriptor
            os.close(previous_descriptor)
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise SyncError(
            f"cannot securely open regular-file overlay {label} root: {root}: {exc}"
        ) from exc
    return descriptor


def _overlay_root_identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
    )


@dataclass(frozen=True)
class _PinnedRegularFileOverlayDirectoryChain:
    root: Path
    relative: Path
    name: str
    descriptors: tuple[int, ...]
    identities: tuple[tuple[int, int, int, int], ...]

    @property
    def parent_descriptor(self) -> int:
        return self.descriptors[-1]


@dataclass(frozen=True)
class _RegularFileOverlayTreeEntry:
    relative_parts: tuple[str, ...]
    kind: str
    identity: tuple[int, int, int, int, int]
    size: int
    sha256: str | None


@dataclass(frozen=True)
class _RegularFileOverlayTreeManifest:
    root_identity: tuple[int, int, int, int, int]
    entries: tuple[_RegularFileOverlayTreeEntry, ...]
    total_bytes: int


@dataclass
class _RegularFileOverlayManifestBuilder:
    entries: dict[tuple[str, ...], _RegularFileOverlayTreeEntry] = field(
        default_factory=dict
    )
    total_bytes: int = 0

    def _record(self, entry: _RegularFileOverlayTreeEntry) -> None:
        if not entry.relative_parts or entry.relative_parts in self.entries:
            raise SyncError("duplicate regular-file overlay manifest entry")
        self.entries[entry.relative_parts] = entry
        self.total_bytes += entry.size

    def record_directory(
        self,
        relative: Path,
        metadata: os.stat_result,
        *,
        label: str,
    ) -> None:
        _validate_regular_file_overlay_tree_directory(metadata, label=label)
        self._record(
            _RegularFileOverlayTreeEntry(
                relative_parts=relative.parts,
                kind="directory",
                identity=_overlay_file_identity(metadata),
                size=0,
                sha256=None,
            )
        )

    def record_file(
        self,
        relative: Path,
        metadata: os.stat_result,
        *,
        size: int,
        sha256: str,
        label: str,
    ) -> None:
        _validate_overlay_regular_file(metadata, label=label, path=relative)
        if metadata.st_size != size or len(sha256) != 64:
            raise SyncError(f"regular-file overlay {label} record is inconsistent")
        self._record(
            _RegularFileOverlayTreeEntry(
                relative_parts=relative.parts,
                kind="file",
                identity=_overlay_file_identity(metadata),
                size=size,
                sha256=sha256,
            )
        )

    def finish(
        self,
        root_metadata: os.stat_result,
        *,
        expected_entries: int,
        expected_bytes: int,
        label: str,
    ) -> _RegularFileOverlayTreeManifest:
        _validate_regular_file_overlay_tree_directory(root_metadata, label=label)
        if len(self.entries) != expected_entries or self.total_bytes != expected_bytes:
            raise SyncError(
                f"regular-file overlay {label} manifest builder is incomplete"
            )
        return _RegularFileOverlayTreeManifest(
            root_identity=_overlay_file_identity(root_metadata),
            entries=tuple(self.entries[path] for path in sorted(self.entries)),
            total_bytes=self.total_bytes,
        )


@dataclass
class _RegularFileOverlayCopyBudget:
    scanned_entries: int = 0
    entries: int = 0
    total_bytes: int = 0

    def reserve_scanned_entry(self, *, label: str) -> None:
        if self.scanned_entries >= MAX_REGULAR_FILE_OVERLAY_TREE_ENTRIES:
            raise SyncError(
                f"regular-file overlay {label} traversal exceeds "
                f"{MAX_REGULAR_FILE_OVERLAY_TREE_ENTRIES} entries"
            )
        self.scanned_entries += 1

    def reserve_entry(self, *, label: str) -> None:
        if self.entries >= MAX_REGULAR_FILE_OVERLAY_TREE_ENTRIES:
            raise SyncError(
                f"regular-file overlay {label} tree exceeds "
                f"{MAX_REGULAR_FILE_OVERLAY_TREE_ENTRIES} entries"
            )
        self.entries += 1

    def reserve_bytes(self, size: int, *, label: str) -> None:
        if size < 0 or self.total_bytes + size > MAX_REGULAR_FILE_OVERLAY_TREE_BYTES:
            raise SyncError(
                f"regular-file overlay {label} tree exceeds "
                f"{MAX_REGULAR_FILE_OVERLAY_TREE_BYTES} bytes"
            )
        self.total_bytes += size


@dataclass(frozen=True)
class _PinnedRegularFileOverlayTarget:
    chain: _PinnedRegularFileOverlayDirectoryChain
    file_descriptor: int
    expected_data: bytes
    expected_identity: tuple[int, int, int, int, int, int, int, int]
    tree_manifest: _RegularFileOverlayTreeManifest


@dataclass(frozen=True)
class _PreparedRegularFileOverlayCandidate:
    root: _PinnedRegularFileOverlayDirectory
    manifest: _RegularFileOverlayTreeManifest


def _regular_file_overlay_directory_identity(
    descriptor: int,
    *,
    label: str,
    path: Path,
) -> tuple[int, int, int, int]:
    try:
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise SyncError(
            f"cannot inspect regular-file overlay {label} directory: {path}: {exc}"
        ) from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise SyncError(f"regular-file overlay {label} path is not a directory: {path}")
    return _overlay_root_identity(metadata)


def _validate_regular_file_overlay_tree_directory(
    metadata: os.stat_result,
    *,
    label: str,
) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise SyncError(f"regular-file overlay {label} is not a directory")
    if metadata.st_uid != os.getuid():
        raise SyncError(
            f"regular-file overlay {label} directory must be owned by the current user"
        )
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise SyncError(
            f"regular-file overlay {label} directory must not be group or other writable"
        )


@contextlib.contextmanager
def _pin_regular_file_overlay_directory_chain(
    root: Path,
    relative: Path,
    *,
    label: str,
    root_binding: _PinnedRegularFileOverlayDirectory | None = None,
) -> Iterator[_PinnedRegularFileOverlayDirectoryChain]:
    _require_overlay_relative_path(relative, field=label)
    flags = _regular_file_overlay_directory_flags(label=label)
    with contextlib.ExitStack() as stack:
        if root_binding is None:
            root_descriptor = _open_regular_file_overlay_root(root, label=label)
        else:
            if root_binding.path != root:
                raise SyncError(
                    f"regular-file overlay {label} root capability mismatch: {root}"
                )
            _assert_regular_file_overlay_directory_binding(
                root_binding,
                label=label,
            )
            root_descriptor = os.dup(root_binding.descriptor)
        stack.callback(os.close, root_descriptor)
        root_identity = _regular_file_overlay_directory_identity(
            root_descriptor,
            label=label,
            path=root,
        )
        if root_binding is not None and root_identity != root_binding.identity:
            raise SyncError(
                f"regular-file overlay {label} root capability changed: {root}"
            )
        descriptors = [root_descriptor]
        identities = [root_identity]
        current = root_descriptor
        current_path = root
        try:
            for component in relative.parts[:-1]:
                current_path = current_path / component
                current = os.open(component, flags, dir_fd=current)
                stack.callback(os.close, current)
                descriptors.append(current)
                identities.append(
                    _regular_file_overlay_directory_identity(
                        current,
                        label=label,
                        path=current_path,
                    )
                )
        except FileNotFoundError as exc:
            raise SyncError(
                f"regular-file overlay {label} missing: {root / relative}"
            ) from exc
        except OSError as exc:
            raise SyncError(
                "cannot securely pin regular-file overlay "
                f"{label} directory chain: {relative}: {exc}"
            ) from exc
        yield _PinnedRegularFileOverlayDirectoryChain(
            root=root,
            relative=relative,
            name=relative.name,
            descriptors=tuple(descriptors),
            identities=tuple(identities),
        )


def _regular_file_overlay_directory_chain_changed(
    *,
    label: str,
    path: Path,
) -> SyncError:
    return SyncError(
        f"regular-file overlay {label} directory chain binding changed: {path}"
    )


def _assert_regular_file_overlay_directory_chain_binding(
    chain: _PinnedRegularFileOverlayDirectoryChain,
    *,
    label: str,
) -> None:
    flags = _regular_file_overlay_directory_flags(label=label)
    visible_descriptors: list[int] = []
    visible_path = chain.root
    try:
        try:
            visible = _open_regular_file_overlay_root(chain.root, label=label)
        except SyncError as exc:
            raise _regular_file_overlay_directory_chain_changed(
                label=label,
                path=visible_path,
            ) from exc
        visible_descriptors.append(visible)
        try:
            visible_identity = _regular_file_overlay_directory_identity(
                visible,
                label=label,
                path=visible_path,
            )
        except SyncError as exc:
            raise _regular_file_overlay_directory_chain_changed(
                label=label,
                path=visible_path,
            ) from exc
        if visible_identity != chain.identities[0]:
            raise _regular_file_overlay_directory_chain_changed(
                label=label,
                path=visible_path,
            )
        for index, component in enumerate(chain.relative.parts[:-1], start=1):
            visible_path = visible_path / component
            try:
                visible = os.open(component, flags, dir_fd=visible)
            except OSError as exc:
                raise _regular_file_overlay_directory_chain_changed(
                    label=label,
                    path=visible_path,
                ) from exc
            visible_descriptors.append(visible)
            try:
                visible_identity = _regular_file_overlay_directory_identity(
                    visible,
                    label=label,
                    path=visible_path,
                )
            except SyncError as exc:
                raise _regular_file_overlay_directory_chain_changed(
                    label=label,
                    path=visible_path,
                ) from exc
            if visible_identity != chain.identities[index]:
                raise _regular_file_overlay_directory_chain_changed(
                    label=label,
                    path=visible_path,
                )
    finally:
        for descriptor in reversed(visible_descriptors):
            os.close(descriptor)


def _stat_regular_file_overlay_entry(
    parent_descriptor: int,
    name: str,
    *,
    label: str,
    path: Path,
) -> os.stat_result:
    try:
        metadata = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError as exc:
        raise SyncError(f"regular-file overlay {label} missing: {path}") from exc
    except OSError as exc:
        raise SyncError(
            f"cannot inspect regular-file overlay {label}: {path}: {exc}"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise SyncError(f"refusing regular-file overlay {label} symlink: {path}")
    _validate_overlay_regular_file(metadata, label=label, path=path)
    return metadata


def _read_regular_file_overlay_descriptor(
    descriptor: int,
    *,
    byte_limit: int,
) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    remaining = byte_limit + 1
    while remaining:
        chunk = os.read(descriptor, min(remaining, 64 * 1024))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _hash_regular_file_overlay_descriptor(
    descriptor: int,
    *,
    initial_size: int,
    label: str,
) -> str:
    digest = hashlib.sha256()
    consumed = 0
    remaining = initial_size + 1
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            consumed += len(chunk)
            if consumed > initial_size:
                raise SyncError(f"regular-file overlay {label} grew while being read")
            digest.update(chunk)
            remaining -= len(chunk)
    except OSError as exc:
        raise SyncError(f"cannot read regular-file overlay {label}: {exc}") from exc
    if consumed != initial_size:
        raise SyncError(f"regular-file overlay {label} changed size while being read")
    return digest.hexdigest()


def _bounded_regular_file_overlay_tree_names(
    descriptor: int,
    *,
    maximum: int,
    label: str,
) -> list[str]:
    if os.scandir not in os.supports_fd:
        raise SyncError(
            f"secure regular-file overlay {label} bounded traversal is unavailable"
        )
    names: list[str] = []
    try:
        with os.scandir(descriptor) as iterator:
            for entry in iterator:
                names.append(entry.name)
                if len(names) > maximum:
                    raise SyncError(
                        f"regular-file overlay {label} tree exceeds its bounded "
                        "entry capacity"
                    )
    except OSError as exc:
        raise SyncError(
            f"cannot enumerate regular-file overlay {label} tree: {exc}"
        ) from exc
    return sorted(names)


def _capture_regular_file_overlay_tree_manifest(
    root_descriptor: int,
    *,
    label: str,
    ignored_names: frozenset[str] = frozenset(),
    raw_entry_validator: Callable[[tuple[str, ...]], None] | None = None,
) -> _RegularFileOverlayTreeManifest:
    if os.scandir not in os.supports_fd:
        raise SyncError(
            f"secure regular-file overlay {label} descriptor traversal is unavailable"
        )
    nonblocking = getattr(os, "O_NONBLOCK", None)
    if nonblocking is None:
        raise SyncError(
            f"secure regular-file overlay {label} nonblocking open is unavailable"
        )
    try:
        root_metadata = os.fstat(root_descriptor)
    except OSError as exc:
        raise SyncError(
            f"cannot inspect regular-file overlay {label} root: {exc}"
        ) from exc
    _validate_regular_file_overlay_tree_directory(
        root_metadata,
        label=f"{label} root",
    )

    entries: list[_RegularFileOverlayTreeEntry] = []
    total_bytes = 0
    scanned_entries = 0
    directory_flags = _regular_file_overlay_directory_flags(label=label)
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | nonblocking | getattr(os, "O_CLOEXEC", 0)

    def capture_directory(
        descriptor: int,
        relative_parts: tuple[str, ...],
        depth: int,
    ) -> None:
        nonlocal scanned_entries, total_bytes
        if depth > MAX_REGULAR_FILE_OVERLAY_TREE_DEPTH:
            raise SyncError(
                f"regular-file overlay {label} tree depth exceeds "
                f"{MAX_REGULAR_FILE_OVERLAY_TREE_DEPTH}"
            )
        initial_names = _bounded_regular_file_overlay_tree_names(
            descriptor,
            maximum=MAX_REGULAR_FILE_OVERLAY_TREE_ENTRIES - scanned_entries,
            label=label,
        )
        scanned_entries += len(initial_names)
        for name in initial_names:
            child_parts = (*relative_parts, name)
            if raw_entry_validator is not None:
                raw_entry_validator(child_parts)
            if _is_ignored_name(name, ignored_names):
                continue
            if len(entries) >= MAX_REGULAR_FILE_OVERLAY_TREE_ENTRIES:
                raise SyncError(
                    f"regular-file overlay {label} tree exceeds "
                    f"{MAX_REGULAR_FILE_OVERLAY_TREE_ENTRIES} entries"
                )
            child_label = "/".join(child_parts)
            try:
                named_before = os.stat(
                    name,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise SyncError(
                    f"cannot inspect regular-file overlay {label} entry "
                    f"{child_label}: {exc}"
                ) from exc
            if stat.S_ISLNK(named_before.st_mode):
                raise SyncError(
                    f"refusing regular-file overlay {label} tree symlink: {child_label}"
                )
            if stat.S_ISDIR(named_before.st_mode):
                try:
                    child_descriptor = os.open(
                        name,
                        directory_flags,
                        dir_fd=descriptor,
                    )
                except OSError as exc:
                    raise SyncError(
                        f"cannot open regular-file overlay {label} directory "
                        f"{child_label}: {exc}"
                    ) from exc
                try:
                    opened = os.fstat(child_descriptor)
                    _validate_regular_file_overlay_tree_directory(
                        opened,
                        label=f"{label} tree directory {child_label}",
                    )
                    identity = _overlay_file_identity(opened)
                    if identity != _overlay_file_identity(named_before):
                        raise SyncError(
                            f"regular-file overlay {label} directory binding "
                            f"changed: {child_label}"
                        )
                    entries.append(
                        _RegularFileOverlayTreeEntry(
                            relative_parts=child_parts,
                            kind="directory",
                            identity=identity,
                            size=0,
                            sha256=None,
                        )
                    )
                    capture_directory(child_descriptor, child_parts, depth + 1)
                    held_after = os.fstat(child_descriptor)
                    named_after = os.stat(
                        name,
                        dir_fd=descriptor,
                        follow_symlinks=False,
                    )
                    if (
                        _overlay_file_identity(held_after) != identity
                        or _overlay_file_identity(named_after) != identity
                    ):
                        raise SyncError(
                            f"regular-file overlay {label} directory changed "
                            f"while being traversed: {child_label}"
                        )
                finally:
                    os.close(child_descriptor)
                continue
            if not stat.S_ISREG(named_before.st_mode):
                raise SyncError(
                    f"unsupported regular-file overlay {label} tree entry: "
                    f"{child_label}"
                )
            try:
                file_descriptor = os.open(
                    name,
                    file_flags,
                    dir_fd=descriptor,
                )
            except OSError as exc:
                raise SyncError(
                    f"cannot open regular-file overlay {label} file "
                    f"{child_label}: {exc}"
                ) from exc
            try:
                opened = os.fstat(file_descriptor)
                _validate_overlay_regular_file(
                    opened,
                    label=f"{label} tree file",
                    path=Path(child_label),
                )
                if _overlay_file_content_identity(
                    opened
                ) != _overlay_file_content_identity(named_before):
                    raise SyncError(
                        f"regular-file overlay {label} file binding changed: "
                        f"{child_label}"
                    )
                if total_bytes + opened.st_size > MAX_REGULAR_FILE_OVERLAY_TREE_BYTES:
                    raise SyncError(
                        f"regular-file overlay {label} tree exceeds "
                        f"{MAX_REGULAR_FILE_OVERLAY_TREE_BYTES} bytes"
                    )
                digest = _hash_regular_file_overlay_descriptor(
                    file_descriptor,
                    initial_size=opened.st_size,
                    label=f"{label} tree file {child_label}",
                )
                held_after = os.fstat(file_descriptor)
                named_after = os.stat(
                    name,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
                expected_content_identity = _overlay_file_content_identity(opened)
                if (
                    _overlay_file_content_identity(held_after)
                    != expected_content_identity
                    or _overlay_file_content_identity(named_after)
                    != expected_content_identity
                ):
                    raise SyncError(
                        f"regular-file overlay {label} file changed while being "
                        f"read: {child_label}"
                    )
                entries.append(
                    _RegularFileOverlayTreeEntry(
                        relative_parts=child_parts,
                        kind="file",
                        identity=_overlay_file_identity(held_after),
                        size=held_after.st_size,
                        sha256=digest,
                    )
                )
                total_bytes += held_after.st_size
            finally:
                os.close(file_descriptor)
        final_names = _bounded_regular_file_overlay_tree_names(
            descriptor,
            maximum=len(initial_names),
            label=label,
        )
        if final_names != initial_names:
            raise SyncError(
                f"regular-file overlay {label} tree changed while being traversed"
            )

    try:
        capture_directory(root_descriptor, (), 0)
        root_after = os.fstat(root_descriptor)
    except OSError as exc:
        raise SyncError(
            f"cannot traverse regular-file overlay {label} tree: {exc}"
        ) from exc
    root_identity = _overlay_file_identity(root_metadata)
    if _overlay_file_identity(root_after) != root_identity:
        raise SyncError(f"regular-file overlay {label} root changed while traversing")
    return _RegularFileOverlayTreeManifest(
        root_identity=root_identity,
        entries=tuple(sorted(entries, key=lambda entry: entry.relative_parts)),
        total_bytes=total_bytes,
    )


def _assert_regular_file_overlay_tree_manifest(
    parent_descriptor: int,
    name: str,
    expected: _RegularFileOverlayTreeManifest,
    *,
    label: str,
) -> None:
    try:
        named_before = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        descriptor = os.open(
            name,
            _regular_file_overlay_directory_flags(label=label),
            dir_fd=parent_descriptor,
        )
    except OSError as exc:
        raise SyncError(
            f"cannot open regular-file overlay {label} tree: {exc}"
        ) from exc
    try:
        if (
            _overlay_file_identity(named_before) != expected.root_identity
            or _overlay_file_identity(os.fstat(descriptor)) != expected.root_identity
        ):
            raise SyncError(f"regular-file overlay {label} tree root binding changed")
        try:
            actual = _capture_regular_file_overlay_tree_manifest(
                descriptor,
                label=label,
            )
            named_after = os.stat(
                name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            held_after = os.fstat(descriptor)
        except OSError as exc:
            raise SyncError(
                f"cannot verify regular-file overlay {label} tree: {exc}"
            ) from exc
        if (
            actual != expected
            or _overlay_file_identity(named_after) != expected.root_identity
            or _overlay_file_identity(held_after) != expected.root_identity
        ):
            raise SyncError(f"regular-file overlay {label} exact tree manifest changed")
    finally:
        os.close(descriptor)


def _capture_regular_file_overlay_tree_manifest_at_path(
    root: Path,
    *,
    label: str,
) -> _RegularFileOverlayTreeManifest:
    with contextlib.ExitStack() as stack:
        pinned = _pin_regular_file_overlay_directory(stack, root, label=label)
        manifest = _capture_regular_file_overlay_tree_manifest(
            pinned.descriptor,
            label=label,
        )
        _assert_regular_file_overlay_directory_binding(pinned, label=label)
        return manifest


def _assert_regular_file_overlay_tree_manifest_at_path(
    root: Path,
    expected: _RegularFileOverlayTreeManifest,
    *,
    label: str,
) -> None:
    actual = _capture_regular_file_overlay_tree_manifest_at_path(
        root,
        label=label,
    )
    if actual != expected:
        raise SyncError(f"regular-file overlay {label} exact tree manifest changed")


def _write_regular_file_overlay_descriptor(descriptor: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(descriptor, data[offset:])
        if written <= 0:
            raise SyncError("short write for regular-file overlay temporary file")
        offset += written


def _read_regular_file_overlay_source(
    repo_root: Path,
    relative: Path,
    *,
    repo_binding: _PinnedRegularFileOverlayDirectory | None = None,
) -> bytes:
    source = repo_root / relative
    with _pin_regular_file_overlay_directory_chain(
        repo_root,
        relative,
        label="source",
        root_binding=repo_binding,
    ) as chain:
        if source.is_symlink():
            raise SyncError(f"refusing regular-file overlay source symlink: {source}")
        if not source.exists():
            raise SyncError(f"regular-file overlay source missing: {source}")
        _ensure_safe_source(repo_root, source)
        _assert_regular_file_overlay_directory_chain_binding(
            chain,
            label="source",
        )
        initial = _stat_regular_file_overlay_entry(
            chain.parent_descriptor,
            chain.name,
            label="source",
            path=source,
        )
        flags = (
            os.O_RDONLY
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            descriptor = os.open(
                chain.name,
                flags,
                dir_fd=chain.parent_descriptor,
            )
        except OSError as exc:
            raise SyncError(
                f"cannot open regular-file overlay source: {source}: {exc}"
            ) from exc
        try:
            before = os.fstat(descriptor)
            _validate_overlay_regular_file(before, label="source", path=source)
            if _overlay_file_content_identity(before) != _overlay_file_content_identity(
                initial
            ):
                raise SyncError(
                    f"regular-file overlay source changed before reading: {source}"
                )
            if before.st_size > MAX_REGULAR_FILE_OVERLAY_BYTES:
                raise SyncError(
                    "regular-file overlay source exceeds "
                    f"{MAX_REGULAR_FILE_OVERLAY_BYTES} bytes: {source}"
                )
            data = _read_regular_file_overlay_descriptor(
                descriptor,
                byte_limit=before.st_size,
            )
            after = os.fstat(descriptor)
        except OSError as exc:
            raise SyncError(
                f"cannot read regular-file overlay source: {source}: {exc}"
            ) from exc
        finally:
            os.close(descriptor)

        identity_before = _overlay_file_content_identity(before)
        identity_after = _overlay_file_content_identity(after)
        if (
            identity_before != identity_after
            or len(data) != before.st_size
            or len(data) != after.st_size
        ):
            raise SyncError(
                f"regular-file overlay source changed while reading: {source}"
            )
        final = _stat_regular_file_overlay_entry(
            chain.parent_descriptor,
            chain.name,
            label="source",
            path=source,
        )
        if _overlay_file_content_identity(final) != identity_after:
            raise SyncError(
                f"regular-file overlay source changed after reading: {source}"
            )
        _assert_regular_file_overlay_directory_chain_binding(
            chain,
            label="source",
        )
    if len(data) > MAX_REGULAR_FILE_OVERLAY_BYTES:
        raise SyncError(
            "regular-file overlay source exceeds "
            f"{MAX_REGULAR_FILE_OVERLAY_BYTES} bytes: {source}"
        )
    return data


def _load_regular_file_overlay_data(
    repo_root: Path,
    rule: SyncRule,
    *,
    repo_binding: _PinnedRegularFileOverlayDirectory,
) -> dict[Path, bytes]:
    loaded: dict[Path, bytes] = {}
    for overlay in rule.regular_file_overlays:
        loaded[overlay.target] = _read_regular_file_overlay_source(
            repo_root,
            overlay.source,
            repo_binding=repo_binding,
        )
    return loaded


def _pin_regular_file_overlay_targets(
    stack: contextlib.ExitStack,
    staging: Path,
    staging_root: _PinnedRegularFileOverlayDirectory,
    overlay_data: dict[Path, bytes],
    expected_tree_manifest: _RegularFileOverlayTreeManifest,
) -> tuple[_PinnedRegularFileOverlayTarget, ...]:
    bindings: list[_PinnedRegularFileOverlayTarget] = []
    if (
        _capture_regular_file_overlay_tree_manifest(
            staging_root.descriptor,
            label="staged target",
        )
        != expected_tree_manifest
    ):
        raise SyncError(
            "regular-file overlay staged target exact tree manifest changed "
            "before pinning private targets"
        )
    flags = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    for relative, expected_data in overlay_data.items():
        chain = stack.enter_context(
            _pin_regular_file_overlay_directory_chain(
                staging,
                relative,
                label="target",
                root_binding=staging_root,
            )
        )
        initial = _stat_regular_file_overlay_entry(
            chain.parent_descriptor,
            chain.name,
            label="target",
            path=relative,
        )
        try:
            descriptor = os.open(
                chain.name,
                flags,
                dir_fd=chain.parent_descriptor,
            )
        except OSError as exc:
            raise SyncError(
                f"cannot pin private regular-file overlay target: {relative}: {exc}"
            ) from exc
        stack.callback(os.close, descriptor)
        opened = os.fstat(descriptor)
        _validate_overlay_regular_file(opened, label="target", path=relative)
        data = _read_regular_file_overlay_descriptor(
            descriptor,
            byte_limit=len(expected_data),
        )
        final = os.fstat(descriptor)
        named = _stat_regular_file_overlay_entry(
            chain.parent_descriptor,
            chain.name,
            label="target",
            path=relative,
        )
        if (
            _overlay_file_identity(initial) != _overlay_file_identity(opened)
            or _overlay_file_content_identity(opened)
            != _overlay_file_content_identity(final)
            or _overlay_file_identity(named) != _overlay_file_identity(final)
            or data != expected_data
            or final.st_size != len(expected_data)
            or stat.S_IMODE(final.st_mode) != REGULAR_FILE_OVERLAY_TARGET_MODE
        ):
            raise SyncError(
                f"private regular-file overlay target binding changed: {relative}"
            )
        _assert_regular_file_overlay_directory_chain_binding(
            chain,
            label="target",
        )
        bindings.append(
            _PinnedRegularFileOverlayTarget(
                chain=chain,
                file_descriptor=descriptor,
                expected_data=expected_data,
                expected_identity=_overlay_file_content_identity(final),
                tree_manifest=expected_tree_manifest,
            )
        )
    if (
        _capture_regular_file_overlay_tree_manifest(
            staging_root.descriptor,
            label="staged target",
        )
        != expected_tree_manifest
    ):
        raise SyncError(
            "regular-file overlay staged target exact tree manifest changed "
            "while pinning private targets"
        )
    return tuple(bindings)


@dataclass(frozen=True)
class _RegularFileOverlayNoReplacePrimitive:
    function: Callable[..., int]
    flags: int


@dataclass(frozen=True)
class _PinnedRegularFileOverlayDirectory:
    path: Path
    descriptor: int
    identity: tuple[int, int, int, int]


@dataclass(frozen=True)
class _PinnedRegularFileOverlayEntry:
    name: str
    descriptor: int
    identity: tuple[int, int, int, int, int]


@dataclass
class _RegularFileOverlayStagingScope:
    path: Path
    repo_root: _PinnedRegularFileOverlayDirectory
    temporary_root: _PinnedRegularFileOverlayDirectory
    recovery_root: _PinnedRegularFileOverlayDirectory
    target_parent: _PinnedRegularFileOverlayDirectory
    target_parent_chain: tuple[_PinnedRegularFileOverlayDirectory, ...]
    container: _PinnedRegularFileOverlayDirectory
    resource_stack: contextlib.ExitStack
    retained_entries: dict[str, _PinnedRegularFileOverlayEntry] = field(
        default_factory=dict
    )
    completed: bool = False

    @property
    def recovery_path(self) -> Path:
        return self.path


@dataclass(frozen=True)
class _InstalledRegularFileOverlayReceipt:
    target: Path
    target_parent: _PinnedRegularFileOverlayDirectory
    root_descriptor: int
    root_identity: tuple[int, int, int, int]
    manifest: _RegularFileOverlayTreeManifest


@dataclass(frozen=True)
class _RegularFileOverlayInstallResult:
    recovery_path: Path | None
    installed_receipt: _InstalledRegularFileOverlayReceipt


def _assert_installed_regular_file_overlay_receipt(
    receipt: _InstalledRegularFileOverlayReceipt,
    *,
    label: str,
) -> None:
    if receipt.target.parent != receipt.target_parent.path:
        raise SyncError(
            f"regular-file overlay {label} installed receipt target disagrees"
        )
    _assert_regular_file_overlay_directory_binding(
        receipt.target_parent,
        label=f"{label} target parent",
    )
    held_identity = _regular_file_overlay_directory_identity(
        receipt.root_descriptor,
        label=label,
        path=receipt.target,
    )
    if held_identity != receipt.root_identity:
        raise SyncError(f"regular-file overlay {label} installed root identity changed")
    held_manifest = _capture_regular_file_overlay_tree_manifest(
        receipt.root_descriptor,
        label=f"{label} held installed target",
    )
    if held_manifest != receipt.manifest:
        raise SyncError(f"regular-file overlay {label} installed held manifest changed")
    if not _regular_file_overlay_named_root_matches(
        receipt.target_parent.descriptor,
        receipt.target.name,
        receipt.root_identity,
        label=label,
    ):
        raise SyncError(
            f"regular-file overlay {label} installed target root binding changed"
        )
    _assert_regular_file_overlay_tree_manifest(
        receipt.target_parent.descriptor,
        receipt.target.name,
        receipt.manifest,
        label=f"{label} live installed target",
    )
    if _regular_file_overlay_directory_identity(
        receipt.root_descriptor,
        label=label,
        path=receipt.target,
    ) != receipt.root_identity or not _regular_file_overlay_named_root_matches(
        receipt.target_parent.descriptor,
        receipt.target.name,
        receipt.root_identity,
        label=label,
    ):
        raise SyncError(
            f"regular-file overlay {label} installed target binding changed"
        )
    _assert_regular_file_overlay_directory_binding(
        receipt.target_parent,
        label=f"{label} target parent",
    )


def _bind_canonical_review_installed_migration_receipt(
    rule: SyncRule,
    locked_source: _LockedRuleSource,
    expected_target: Path,
    prepared_source_manifest: _RegularFileOverlayTreeManifest,
    expected_target_manifest: _RegularFileOverlayTreeManifest,
    installed_receipt: _InstalledRegularFileOverlayReceipt,
) -> _CanonicalReviewInstalledMigrationReceipt:
    if not _canonical_review_personal_agents_migration_required(
        rule,
        locked_source,
    ):
        raise SyncError("canonical review install is not migration-authorized")
    migration = locked_source.canonical_review_migration_receipt
    if migration is None:
        raise SyncError("canonical review migration receipt is missing at install")
    if (
        getattr(locked_source.manifest, "root_object_id", None)
        != migration.live_review_subtree_tree
    ):
        raise SyncError("canonical review locked manifest changed before install")
    if (
        installed_receipt.target != expected_target
        or expected_target.parts[-len(rule.target.parts) :] != rule.target.parts
    ):
        raise SyncError("canonical review installed target path differs")
    if installed_receipt.manifest != expected_target_manifest:
        raise SyncError("canonical review installed manifest differs from candidate")
    _assert_installed_regular_file_overlay_receipt(
        installed_receipt,
        label="canonical review install receipt binding",
    )
    return _CanonicalReviewInstalledMigrationReceipt(
        migration=migration,
        expected_target=expected_target,
        prepared_source_manifest=prepared_source_manifest,
        expected_manifest=expected_target_manifest,
        installed_receipt=installed_receipt,
        seal=_CANONICAL_REVIEW_INSTALLED_RECEIPT_SEAL,
    )


def _assert_canonical_review_installed_migration_receipt(
    receipt: _CanonicalReviewInstalledMigrationReceipt,
    rule: SyncRule,
    locked_source: _LockedRuleSource,
    *,
    label: str,
) -> None:
    if (
        type(receipt) is not _CanonicalReviewInstalledMigrationReceipt
        or receipt.seal is not _CANONICAL_REVIEW_INSTALLED_RECEIPT_SEAL
        or receipt.migration is not locked_source.canonical_review_migration_receipt
        or receipt.migration.policy != rule.canonical_review_migration_policy
        or receipt.expected_target.parts[-len(rule.target.parts) :] != rule.target.parts
        or receipt.installed_receipt.target != receipt.expected_target
        or receipt.installed_receipt.manifest != receipt.expected_manifest
        or not isinstance(
            receipt.prepared_source_manifest,
            _RegularFileOverlayTreeManifest,
        )
        or not isinstance(receipt.expected_manifest, _RegularFileOverlayTreeManifest)
    ):
        raise SyncError(
            f"canonical review {label} installed migration receipt is invalid"
        )
    _assert_installed_regular_file_overlay_receipt(
        receipt.installed_receipt,
        label=label,
    )


def _register_regular_file_overlay_retained_entry(
    scope: _RegularFileOverlayStagingScope,
    name: str,
    entry: _PinnedRegularFileOverlayEntry,
) -> None:
    if name in scope.retained_entries:
        raise SyncError("duplicate regular-file overlay retained entry")
    if len(scope.retained_entries) >= MAX_REGULAR_FILE_OVERLAY_RETAINED_ENTRIES:
        raise SyncError("regular-file overlay retained entry limit exceeded")
    _assert_regular_file_overlay_entry_binding(
        scope.container.descriptor,
        entry,
        label="retained recovery entry",
        name=name,
    )
    scope.retained_entries[name] = entry


def _assert_regular_file_overlay_retained_entries(
    scope: _RegularFileOverlayStagingScope,
    *,
    exact_names: set[str] | None = None,
) -> None:
    if exact_names is not None:
        actual_names = set(
            _bounded_regular_file_overlay_tree_names(
                scope.container.descriptor,
                maximum=len(exact_names),
                label="recovery scope",
            )
        )
        if actual_names != exact_names:
            raise SyncError("regular-file overlay recovery scope entries changed")
    for name, entry in scope.retained_entries.items():
        _assert_regular_file_overlay_entry_binding(
            scope.container.descriptor,
            entry,
            label="retained recovery entry",
            name=name,
        )


def _load_regular_file_overlay_noreplace_primitive() -> (
    _RegularFileOverlayNoReplacePrimitive
):
    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except OSError as exc:
        raise SyncError(
            "secure regular-file overlay no-replace rename is unavailable"
        ) from exc
    if sys.platform == "darwin":
        symbol = "renameatx_np"
        flags = 0x00000004
    elif sys.platform.startswith("linux"):
        symbol = "renameat2"
        flags = 1
    else:
        raise SyncError("secure regular-file overlay no-replace rename is unavailable")
    try:
        function = getattr(libc, symbol)
    except AttributeError as exc:
        raise SyncError(
            "secure regular-file overlay no-replace rename is unavailable"
        ) from exc
    function.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    function.restype = ctypes.c_int
    return _RegularFileOverlayNoReplacePrimitive(function=function, flags=flags)


def _rename_regular_file_overlay_noreplace(
    primitive: _RegularFileOverlayNoReplacePrimitive,
    source_parent_descriptor: int,
    source_name: str,
    target_parent_descriptor: int,
    target_name: str,
) -> None:
    ctypes.set_errno(0)
    result = primitive.function(
        source_parent_descriptor,
        os.fsencode(source_name),
        target_parent_descriptor,
        os.fsencode(target_name),
        primitive.flags,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    unsupported = {
        errno.EINVAL,
        errno.ENOSYS,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }
    if error_number in unsupported:
        raise SyncError("secure regular-file overlay no-replace rename is unavailable")
    raise SyncError(
        "cannot securely rename regular-file overlay entry without replacement: "
        f"{os.strerror(error_number)}"
    )


def _pin_regular_file_overlay_directory(
    stack: contextlib.ExitStack,
    path: Path,
    *,
    label: str,
) -> _PinnedRegularFileOverlayDirectory:
    descriptor = _open_regular_file_overlay_root(path, label=label)
    stack.callback(os.close, descriptor)
    identity = _regular_file_overlay_directory_identity(
        descriptor,
        label=label,
        path=path,
    )
    return _PinnedRegularFileOverlayDirectory(
        path=path,
        descriptor=descriptor,
        identity=identity,
    )


def _pin_or_create_regular_file_overlay_descendant_chain(
    stack: contextlib.ExitStack,
    root: _PinnedRegularFileOverlayDirectory,
    relative: Path,
    *,
    label: str,
) -> tuple[_PinnedRegularFileOverlayDirectory, ...]:
    if relative.is_absolute() or ".." in relative.parts:
        raise SyncError(f"unsafe regular-file overlay {label}: {relative}")
    _assert_regular_file_overlay_directory_binding(root, label="repository root")
    chain: list[_PinnedRegularFileOverlayDirectory] = [root]
    current = root
    current_path = root.path
    for component in relative.parts:
        current_path = current_path / component
        _assert_regular_file_overlay_directory_binding(
            root,
            label="repository root before target-parent creation",
        )
        _assert_regular_file_overlay_directory_binding(
            current,
            label="target parent before descendant creation",
        )
        try:
            os.mkdir(component, 0o755, dir_fd=current.descriptor)
        except FileExistsError:
            pass
        except OSError as exc:
            raise SyncError(
                f"cannot create regular-file overlay {label}: {current_path}: {exc}"
            ) from exc
        try:
            descriptor = os.open(
                component,
                _regular_file_overlay_directory_flags(label=label),
                dir_fd=current.descriptor,
            )
        except OSError as exc:
            raise SyncError(
                f"cannot pin regular-file overlay {label}: {current_path}: {exc}"
            ) from exc
        stack.callback(os.close, descriptor)
        metadata = os.fstat(descriptor)
        if metadata.st_uid != os.getuid() or metadata.st_mode & (
            stat.S_IWGRP | stat.S_IWOTH
        ):
            raise SyncError(
                f"regular-file overlay {label} has unsafe ownership or mode: "
                f"{current_path}"
            )
        pinned = _PinnedRegularFileOverlayDirectory(
            path=current_path,
            descriptor=descriptor,
            identity=_regular_file_overlay_directory_identity(
                descriptor,
                label=label,
                path=current_path,
            ),
        )
        if not _regular_file_overlay_named_root_matches(
            current.descriptor,
            component,
            pinned.identity,
            label=label,
        ):
            raise SyncError(
                f"regular-file overlay {label} binding changed: {current_path}"
            )
        chain.append(pinned)
        current = pinned
    _assert_regular_file_overlay_directory_binding(root, label="repository root")
    return tuple(chain)


def _assert_regular_file_overlay_scope_binding(
    scope: _RegularFileOverlayStagingScope,
    *,
    operation: str,
) -> None:
    _assert_regular_file_overlay_directory_binding(
        scope.repo_root,
        label="repository root",
    )
    lineage = (
        (scope.repo_root, scope.temporary_root, scope.temporary_root.path.name),
        (scope.temporary_root, scope.recovery_root, scope.recovery_root.path.name),
        (scope.recovery_root, scope.container, scope.container.path.name),
    )
    for parent, child, name in lineage:
        if parent is child:
            continue
        if not _regular_file_overlay_named_root_matches(
            parent.descriptor,
            name,
            child.identity,
            label=operation,
        ):
            raise SyncError(
                "regular-file overlay scope lineage changed before "
                f"{operation}: {child.path}"
            )
    for index in range(1, len(scope.target_parent_chain)):
        parent = scope.target_parent_chain[index - 1]
        child = scope.target_parent_chain[index]
        if not _regular_file_overlay_named_root_matches(
            parent.descriptor,
            child.path.name,
            child.identity,
            label=operation,
        ):
            raise SyncError(
                "regular-file overlay target parent lineage changed before "
                f"{operation}: {child.path}"
            )
    _assert_regular_file_overlay_directory_binding(
        scope.target_parent,
        label="target parent",
    )
    _assert_regular_file_overlay_retained_entries(scope)


def _assert_regular_file_overlay_directory_binding(
    pinned: _PinnedRegularFileOverlayDirectory,
    *,
    label: str,
) -> None:
    try:
        visible = _open_regular_file_overlay_root(pinned.path, label=label)
    except SyncError as exc:
        raise SyncError(
            f"regular-file overlay {label} directory binding changed: {pinned.path}"
        ) from exc
    try:
        identity = _regular_file_overlay_directory_identity(
            visible,
            label=label,
            path=pinned.path,
        )
    finally:
        os.close(visible)
    if identity != pinned.identity:
        raise SyncError(
            f"regular-file overlay {label} directory binding changed: {pinned.path}"
        )


def _pin_regular_file_overlay_entry(
    stack: contextlib.ExitStack,
    parent_descriptor: int,
    name: str,
    *,
    label: str,
) -> _PinnedRegularFileOverlayEntry:
    nonblocking = getattr(os, "O_NONBLOCK", None)
    if nonblocking is None:
        raise SyncError(
            f"secure regular-file overlay {label} nonblocking open is unavailable"
        )
    try:
        before = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except OSError as exc:
        raise SyncError(f"cannot inspect regular-file overlay {label}: {exc}") from exc
    if stat.S_ISLNK(before.st_mode) or not (
        stat.S_ISDIR(before.st_mode) or stat.S_ISREG(before.st_mode)
    ):
        raise SyncError(f"regular-file overlay {label} has an unsafe file type")
    if before.st_uid != os.getuid() or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise SyncError(f"regular-file overlay {label} has unsafe ownership or mode")
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    flags |= os.O_DIRECTORY if stat.S_ISDIR(before.st_mode) else nonblocking
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise SyncError(f"cannot pin regular-file overlay {label}: {exc}") from exc
    stack.callback(os.close, descriptor)
    identity = _overlay_file_identity(os.fstat(descriptor))
    if identity != _overlay_file_identity(before):
        raise SyncError(f"regular-file overlay {label} changed while being pinned")
    return _PinnedRegularFileOverlayEntry(
        name=name,
        descriptor=descriptor,
        identity=identity,
    )


def _assert_regular_file_overlay_entry_binding(
    parent_descriptor: int,
    pinned: _PinnedRegularFileOverlayEntry,
    *,
    label: str,
    name: str | None = None,
) -> None:
    visible_name = pinned.name if name is None else name
    try:
        visible = os.stat(
            visible_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        held = os.fstat(pinned.descriptor)
    except OSError as exc:
        raise SyncError(f"cannot verify regular-file overlay {label}: {exc}") from exc
    if (
        _overlay_file_identity(visible) != pinned.identity
        or _overlay_file_identity(held) != pinned.identity
    ):
        raise SyncError(f"regular-file overlay {label} binding changed")


def _regular_file_overlay_named_entry_matches(
    parent_descriptor: int,
    name: str,
    pinned: _PinnedRegularFileOverlayEntry,
) -> bool:
    try:
        visible = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        held = os.fstat(pinned.descriptor)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise SyncError(
            f"cannot inspect regular-file overlay entry {name!r}: {exc}"
        ) from exc
    return (
        _overlay_file_identity(visible) == pinned.identity
        and _overlay_file_identity(held) == pinned.identity
    )


def _open_regular_file_overlay_visible_file(
    stack: contextlib.ExitStack,
    root: Path,
    binding: _PinnedRegularFileOverlayTarget,
    *,
    label: str,
) -> int:
    nonblocking = getattr(os, "O_NONBLOCK", None)
    if nonblocking is None:
        raise SyncError(
            f"secure regular-file overlay {label} nonblocking file open is unavailable"
        )
    flags = _regular_file_overlay_directory_flags(label=label)
    descriptor = _open_regular_file_overlay_root(root, label=label)
    stack.callback(os.close, descriptor)
    identity = _regular_file_overlay_directory_identity(
        descriptor,
        label=label,
        path=root,
    )
    if identity != binding.chain.identities[0]:
        raise SyncError(f"regular-file overlay {label} root binding changed: {root}")
    visible_path = root
    for index, component in enumerate(binding.chain.relative.parts[:-1], start=1):
        visible_path = visible_path / component
        try:
            descriptor = os.open(component, flags, dir_fd=descriptor)
        except OSError as exc:
            raise SyncError(
                f"regular-file overlay {label} directory binding changed: {visible_path}"
            ) from exc
        stack.callback(os.close, descriptor)
        identity = _regular_file_overlay_directory_identity(
            descriptor,
            label=label,
            path=visible_path,
        )
        if identity != binding.chain.identities[index]:
            raise SyncError(
                f"regular-file overlay {label} directory binding changed: {visible_path}"
            )
    try:
        visible_file = os.open(
            binding.chain.name,
            os.O_RDONLY | os.O_NOFOLLOW | nonblocking | getattr(os, "O_CLOEXEC", 0),
            dir_fd=descriptor,
        )
    except OSError as exc:
        raise SyncError(
            f"cannot open regular-file overlay {label} file: "
            f"{root / binding.chain.relative}: {exc}"
        ) from exc
    stack.callback(os.close, visible_file)
    return visible_file


def _assert_regular_file_overlay_binding_at_visible_root(
    root: Path,
    binding: _PinnedRegularFileOverlayTarget,
    *,
    label: str,
) -> None:
    with contextlib.ExitStack() as stack:
        visible_file = _open_regular_file_overlay_visible_file(
            stack,
            root,
            binding,
            label=label,
        )
        visible_before = os.fstat(visible_file)
        pinned_before = os.fstat(binding.file_descriptor)
        _validate_overlay_regular_file(
            visible_before,
            label=label,
            path=root / binding.chain.relative,
        )
        if (
            _overlay_file_content_identity(visible_before) != binding.expected_identity
            or _overlay_file_content_identity(pinned_before)
            != binding.expected_identity
        ):
            raise SyncError(
                f"regular-file overlay {label} file binding changed: "
                f"{root / binding.chain.relative}"
            )
        visible_data = _read_regular_file_overlay_descriptor(
            visible_file,
            byte_limit=len(binding.expected_data),
        )
        pinned_data = _read_regular_file_overlay_descriptor(
            binding.file_descriptor,
            byte_limit=len(binding.expected_data),
        )
        visible_after = os.fstat(visible_file)
        pinned_after = os.fstat(binding.file_descriptor)
        if (
            visible_data != binding.expected_data
            or pinned_data != binding.expected_data
            or _overlay_file_content_identity(visible_after)
            != binding.expected_identity
            or _overlay_file_content_identity(pinned_after) != binding.expected_identity
            or stat.S_IMODE(visible_after.st_mode) != REGULAR_FILE_OVERLAY_TARGET_MODE
        ):
            raise SyncError(
                f"regular-file overlay {label} exact-byte verification failed: "
                f"{root / binding.chain.relative}"
            )
    with contextlib.ExitStack() as final_stack:
        visible_file = _open_regular_file_overlay_visible_file(
            final_stack,
            root,
            binding,
            label=label,
        )
        if (
            _overlay_file_content_identity(os.fstat(visible_file))
            != binding.expected_identity
        ):
            raise SyncError(
                f"regular-file overlay {label} final file binding changed: "
                f"{root / binding.chain.relative}"
            )


def _regular_file_overlay_named_root_matches(
    parent_descriptor: int,
    name: str,
    expected_identity: tuple[int, int, int, int],
    *,
    label: str,
) -> bool:
    flags = _regular_file_overlay_directory_flags(label=label)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError:
        return False
    try:
        return (
            _regular_file_overlay_directory_identity(
                descriptor,
                label=label,
                path=Path(name),
            )
            == expected_identity
        )
    finally:
        os.close(descriptor)


def _regular_file_overlay_entry_exists(parent_descriptor: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise SyncError(
            f"cannot inspect regular-file overlay entry {name!r}: {exc}"
        ) from exc
    return True


def _regular_file_overlay_absent_name(
    parent_descriptor: int,
    *,
    prefix: str,
) -> str:
    for _attempt in range(REGULAR_FILE_OVERLAY_TEMP_ATTEMPTS):
        name = f"{prefix}{secrets.token_hex(16)}"
        if not _regular_file_overlay_entry_exists(parent_descriptor, name):
            return name
    raise SyncError("cannot allocate a regular-file overlay backup name")


def _retain_regular_file_overlay_backup(
    staging_parent: _PinnedRegularFileOverlayDirectory,
    backup_name: str,
    backup: _PinnedRegularFileOverlayEntry,
) -> Path:
    try:
        _assert_regular_file_overlay_entry_binding(
            staging_parent.descriptor,
            backup,
            label="retained backup",
            name=backup_name,
        )
    except SyncError as exc:
        raise _RegularFileOverlayBackupRetentionError(
            "regular-file overlay backup recovery binding is unknown: "
            f"{staging_parent.path / backup_name}"
        ) from exc
    return staging_parent.path / backup_name


def _locate_regular_file_overlay_backup_or_retain(
    staging_parent: _PinnedRegularFileOverlayDirectory,
    backup_name: str | None,
    backup: _PinnedRegularFileOverlayEntry | None,
    target_parent: _PinnedRegularFileOverlayDirectory,
    target_name: str,
) -> Path | None:
    if backup_name is None or backup is None:
        return None
    try:
        if _regular_file_overlay_named_entry_matches(
            target_parent.descriptor, target_name, backup
        ):
            return None
        if _regular_file_overlay_named_entry_matches(
            staging_parent.descriptor, backup_name, backup
        ):
            # A portable pathname rename cannot conditionally move only the
            # pinned inode. Never restore through a basename that could have
            # rebound; retain the root-bound prior target for manual recovery.
            # This entry binding does not verify the prior tree contents.
            return _retain_regular_file_overlay_backup(
                staging_parent,
                backup_name,
                backup,
            )
    except SyncError:
        return _retain_regular_file_overlay_backup(
            staging_parent,
            backup_name,
            backup,
        )
    raise _RegularFileOverlayBackupRetentionError(
        "regular-file overlay prior target is not bound at its target or "
        f"recovery path: {staging_parent.path / backup_name}"
    )


def _replace_target_with_regular_file_overlays(
    target: Path,
    staging: Path,
    bindings: tuple[_PinnedRegularFileOverlayTarget, ...],
    *,
    staging_scope: _RegularFileOverlayStagingScope,
    candidate_root: _PinnedRegularFileOverlayDirectory | None = None,
    candidate_manifest: _RegularFileOverlayTreeManifest | None = None,
) -> _RegularFileOverlayInstallResult:
    primitive = _load_regular_file_overlay_noreplace_primitive()
    if bindings:
        expected_root_identity = bindings[0].chain.identities[0]
        expected_tree_manifest = bindings[0].tree_manifest
        if any(
            binding.chain.root != staging
            or binding.chain.identities[0] != expected_root_identity
            or binding.tree_manifest != expected_tree_manifest
            for binding in bindings
        ):
            raise SyncError("regular-file overlay staging bindings disagree")
        if (
            candidate_root is not None
            and candidate_root.identity != expected_root_identity
        ):
            raise SyncError("regular-file overlay candidate root binding disagrees")
        if (
            candidate_manifest is not None
            and candidate_manifest != expected_tree_manifest
        ):
            raise SyncError("regular-file overlay candidate manifest disagrees")
        expected_root_descriptor = (
            candidate_root.descriptor
            if candidate_root is not None
            else bindings[0].chain.descriptors[0]
        )
    elif candidate_root is not None and candidate_manifest is not None:
        if candidate_root.path != staging:
            raise SyncError("regular-file overlay candidate root path disagrees")
        expected_root_identity = candidate_root.identity
        expected_tree_manifest = candidate_manifest
        expected_root_descriptor = candidate_root.descriptor
    else:
        raise SyncError(
            "secure regular-file overlay install requires a bound candidate"
        )
    if (
        staging.parent != staging_scope.path
        or target.parent != staging_scope.target_parent.path
    ):
        raise SyncError("regular-file overlay staging scope mismatch")
    _assert_regular_file_overlay_scope_binding(
        staging_scope,
        operation="final install preparation",
    )

    with contextlib.ExitStack() as stack:
        staging_parent = staging_scope.container
        target_parent = staging_scope.target_parent
        _assert_regular_file_overlay_directory_binding(
            staging_parent,
            label="staging container",
        )
        _assert_regular_file_overlay_directory_binding(
            target_parent,
            label="target parent",
        )
        for binding in bindings:
            _assert_regular_file_overlay_binding_at_visible_root(
                staging,
                binding,
                label="staged target",
            )
        if not _regular_file_overlay_named_root_matches(
            staging_parent.descriptor,
            staging.name,
            expected_root_identity,
            label="staged target",
        ):
            raise SyncError(
                f"regular-file overlay staged target root binding changed: {staging}"
            )
        _assert_regular_file_overlay_tree_manifest(
            staging_parent.descriptor,
            staging.name,
            expected_tree_manifest,
            label="staged target",
        )
        _assert_regular_file_overlay_retained_entries(
            staging_scope,
            exact_names={staging.name},
        )

        backup_name: str | None = None
        backup: _PinnedRegularFileOverlayEntry | None = None
        if _regular_file_overlay_entry_exists(target_parent.descriptor, target.name):
            backup = _pin_regular_file_overlay_entry(
                stack,
                target_parent.descriptor,
                target.name,
                label="prior target",
            )
            backup_name = _regular_file_overlay_absent_name(
                staging_parent.descriptor,
                prefix=REGULAR_FILE_OVERLAY_BACKUP_PREFIX,
            )
            if (
                len(staging_scope.retained_entries) + 1
                > MAX_REGULAR_FILE_OVERLAY_RETAINED_ENTRIES
            ):
                raise SyncError(
                    "regular-file overlay retained entry limit would be exceeded"
                )

        try:
            if backup_name is not None and backup is not None:
                _assert_regular_file_overlay_scope_binding(
                    staging_scope,
                    operation="prior target backup move",
                )
                _assert_regular_file_overlay_retained_entries(
                    staging_scope,
                    exact_names={staging.name},
                )
                _assert_regular_file_overlay_entry_binding(
                    target_parent.descriptor,
                    backup,
                    label="prior target before backup move",
                )
                _rename_regular_file_overlay_noreplace(
                    primitive,
                    target_parent.descriptor,
                    target.name,
                    staging_parent.descriptor,
                    backup_name,
                )
                _assert_regular_file_overlay_entry_binding(
                    staging_parent.descriptor,
                    backup,
                    label="moved prior target backup",
                    name=backup_name,
                )
                _register_regular_file_overlay_retained_entry(
                    staging_scope,
                    backup_name,
                    backup,
                )
                held_resources = stack.pop_all()
                staging_scope.resource_stack.callback(held_resources.close)
            _assert_regular_file_overlay_directory_binding(
                staging_parent,
                label="staging container",
            )
            _assert_regular_file_overlay_directory_binding(
                target_parent,
                label="target parent",
            )
            for binding in bindings:
                _assert_regular_file_overlay_binding_at_visible_root(
                    staging,
                    binding,
                    label="staged target",
                )
            if not _regular_file_overlay_named_root_matches(
                staging_parent.descriptor,
                staging.name,
                expected_root_identity,
                label="staged target",
            ):
                raise SyncError(
                    f"regular-file overlay staged target root binding changed: {staging}"
                )
            _assert_regular_file_overlay_scope_binding(
                staging_scope,
                operation="final candidate install",
            )
            expected_preinstall_entries = {staging.name}
            if backup_name is not None:
                expected_preinstall_entries.add(backup_name)
            _assert_regular_file_overlay_retained_entries(
                staging_scope,
                exact_names=expected_preinstall_entries,
            )
            _assert_regular_file_overlay_tree_manifest(
                staging_parent.descriptor,
                staging.name,
                expected_tree_manifest,
                label="staged target",
            )
            # No portable rename primitive can atomically require the source
            # basename to still name the previously pinned source-entry inode.
            # The randomized 0700 recovery scope keeps other UIDs out; a
            # concurrent same-UID basename rebind is detected by the
            # installed-root and exact-byte checks below and fails forward
            # without restoring any mutable recovery basename.
            _rename_regular_file_overlay_noreplace(
                primitive,
                staging_parent.descriptor,
                staging.name,
                target_parent.descriptor,
                target.name,
            )
            if not _regular_file_overlay_named_root_matches(
                target_parent.descriptor,
                target.name,
                expected_root_identity,
                label="installed target",
            ):
                raise SyncError(
                    f"regular-file overlay installed target root binding changed: {target}"
                )
            _assert_regular_file_overlay_tree_manifest(
                target_parent.descriptor,
                target.name,
                expected_tree_manifest,
                label="installed target",
            )
            _assert_regular_file_overlay_directory_binding(
                staging_parent,
                label="staging container",
            )
            _assert_regular_file_overlay_directory_binding(
                target_parent,
                label="target parent",
            )
            if not _regular_file_overlay_named_root_matches(
                target_parent.descriptor,
                target.name,
                expected_root_identity,
                label="installed target",
            ):
                raise SyncError(
                    f"regular-file overlay installed target root binding changed: {target}"
                )
            for binding in bindings:
                _assert_regular_file_overlay_binding_at_visible_root(
                    target,
                    binding,
                    label="installed target",
                )
            expected_staging_entries = sorted(
                set(staging_scope.retained_entries)
                | ({backup_name} if backup_name is not None else set())
            )
            actual_staging_entries = _bounded_regular_file_overlay_tree_names(
                staging_parent.descriptor,
                maximum=len(expected_staging_entries),
                label="staging after install",
            )
            if actual_staging_entries != expected_staging_entries:
                raise SyncError(
                    "regular-file overlay staging gained an unknown entry after install"
                )
            if backup_name is not None and backup is not None:
                _assert_regular_file_overlay_entry_binding(
                    staging_parent.descriptor,
                    backup,
                    label="root-bound recovery backup",
                    name=backup_name,
                )
            _assert_regular_file_overlay_directory_binding(
                target_parent,
                label="target parent",
            )
            _assert_regular_file_overlay_tree_manifest(
                target_parent.descriptor,
                target.name,
                expected_tree_manifest,
                label="installed target",
            )
            _assert_regular_file_overlay_scope_binding(
                staging_scope,
                operation="final candidate validation",
            )
        except BaseException as transaction_error:
            # Recovery is deliberately forward-only. After either no-replace
            # rename, a mutable basename may have rebound even while the pinned
            # descriptors remain trustworthy. Inspect capabilities and retain
            # evidence, but never move a recovery basename back into the live
            # target or move an installed candidate back into staging.
            transaction_message = str(transaction_error)
            transaction_detail = type(transaction_error).__name__
            if transaction_message:
                transaction_detail += f": {transaction_message}"
            target_is_candidate = _regular_file_overlay_named_root_matches(
                target_parent.descriptor,
                target.name,
                expected_root_identity,
                label="candidate recovery target",
            )
            staging_is_candidate = _regular_file_overlay_named_root_matches(
                staging_parent.descriptor,
                staging.name,
                expected_root_identity,
                label="candidate recovery staging",
            )
            if target_is_candidate and not staging_is_candidate:
                candidate_detail = (
                    f"installed candidate left live at {target}; only the candidate "
                    "root identity matched; exact contents are unverified and must "
                    "be treated as untrusted"
                )
            elif staging_is_candidate and not target_is_candidate:
                candidate_detail = (
                    f"candidate retained in recovery scope {staging_scope.path}; "
                    "only the candidate root identity matched; exact contents are "
                    "unverified and must be treated as untrusted"
                )
            else:
                candidate_detail = (
                    "candidate binding is ambiguous between live target and "
                    f"recovery scope {staging_scope.path}; exact contents are "
                    "unverified and must be treated as untrusted"
                )

            retained: Path | None = None
            if backup_name is not None and backup is not None:
                try:
                    retained = _locate_regular_file_overlay_backup_or_retain(
                        staging_parent,
                        backup_name,
                        backup,
                        target_parent,
                        target.name,
                    )
                except _RegularFileOverlayBackupRetentionError as recovery_error:
                    raise SyncError(
                        "regular-file overlay transaction failed; original "
                        f"transaction error: {transaction_detail}; "
                        f"{candidate_detail}; prior target binding is unknown; "
                        f"inspect {staging_scope.path}: {recovery_error}"
                    ) from transaction_error
                if retained is None:
                    prior_detail = (
                        f"prior target root identity remains live at {target}; "
                        "contents are unverified"
                    )
                else:
                    prior_detail = (
                        f"prior target root identity retained at {retained}; "
                        "contents are unverified"
                    )
            else:
                prior_detail = "no prior target existed"

            try:
                target_exists = _regular_file_overlay_entry_exists(
                    target_parent.descriptor,
                    target.name,
                )
            except SyncError:
                live_detail = "live target state could not be inspected"
            else:
                if target_is_candidate:
                    live_detail = (
                        "live target matches only the candidate root identity; "
                        "exact contents are unverified and untrusted"
                    )
                elif retained is None and backup is not None:
                    live_detail = (
                        "live target matches only the prior-target root identity; "
                        "contents are unverified"
                    )
                elif target_exists:
                    live_detail = f"untrusted live target remains at {target}"
                else:
                    live_detail = "live target is absent"

            raise SyncError(
                "regular-file overlay transaction failed; original "
                f"transaction error: {transaction_detail}; "
                f"{candidate_detail}; {prior_detail}; {live_detail}"
            ) from transaction_error
        installed_receipt = _InstalledRegularFileOverlayReceipt(
            target=target,
            target_parent=target_parent,
            root_descriptor=expected_root_descriptor,
            root_identity=expected_root_identity,
            manifest=expected_tree_manifest,
        )
        _assert_installed_regular_file_overlay_receipt(
            installed_receipt,
            label="post-install validation",
        )
        staging_scope.completed = True
        return _RegularFileOverlayInstallResult(
            recovery_path=staging_scope.recovery_path,
            installed_receipt=installed_receipt,
        )


def _pin_or_create_regular_file_overlay_directory(
    stack: contextlib.ExitStack,
    parent: _PinnedRegularFileOverlayDirectory,
    name: str,
    *,
    path: Path,
    label: str,
    private: bool,
) -> _PinnedRegularFileOverlayDirectory:
    try:
        os.mkdir(name, 0o700, dir_fd=parent.descriptor)
    except FileExistsError:
        pass
    except OSError as exc:
        raise SyncError(f"cannot create regular-file overlay {label}: {exc}") from exc
    try:
        descriptor = os.open(
            name,
            _regular_file_overlay_directory_flags(label=label),
            dir_fd=parent.descriptor,
        )
    except OSError as exc:
        raise SyncError(f"cannot pin regular-file overlay {label}: {exc}") from exc
    stack.callback(os.close, descriptor)
    metadata = os.fstat(descriptor)
    if metadata.st_uid != os.getuid() or metadata.st_mode & (
        stat.S_IWGRP | stat.S_IWOTH
    ):
        raise SyncError(f"regular-file overlay {label} has unsafe ownership or mode")
    if private and stat.S_IMODE(metadata.st_mode) != 0o700:
        raise SyncError(f"regular-file overlay {label} must have mode 0700")
    pinned = _PinnedRegularFileOverlayDirectory(
        path=path,
        descriptor=descriptor,
        identity=_regular_file_overlay_directory_identity(
            descriptor,
            label=label,
            path=path,
        ),
    )
    if not _regular_file_overlay_named_root_matches(
        parent.descriptor,
        name,
        pinned.identity,
        label=label,
    ):
        raise SyncError(f"regular-file overlay {label} binding changed: {path}")
    return pinned


def _pin_regular_file_overlay_child_directory(
    stack: contextlib.ExitStack,
    parent: _PinnedRegularFileOverlayDirectory,
    name: str,
    *,
    path: Path,
    label: str,
) -> _PinnedRegularFileOverlayDirectory:
    try:
        descriptor = os.open(
            name,
            _regular_file_overlay_directory_flags(label=label),
            dir_fd=parent.descriptor,
        )
    except OSError as exc:
        raise SyncError(f"cannot pin regular-file overlay {label}: {exc}") from exc
    stack.callback(os.close, descriptor)
    pinned = _PinnedRegularFileOverlayDirectory(
        path=path,
        descriptor=descriptor,
        identity=_regular_file_overlay_directory_identity(
            descriptor,
            label=label,
            path=path,
        ),
    )
    if not _regular_file_overlay_named_root_matches(
        parent.descriptor,
        name,
        pinned.identity,
        label=label,
    ):
        raise SyncError(f"regular-file overlay {label} binding changed: {path}")
    return pinned


def _external_prepared_regular_file_overlay_parent_path() -> Path:
    return Path(tempfile.gettempdir()).resolve(strict=True)


def _create_external_prepared_regular_file_overlay_container(
    stack: contextlib.ExitStack,
    *,
    target_name: str,
) -> tuple[
    _PinnedRegularFileOverlayDirectory,
    _PinnedRegularFileOverlayDirectory,
]:
    temporary_root = _external_prepared_regular_file_overlay_parent_path()
    parent = _pin_regular_file_overlay_directory(
        stack,
        temporary_root,
        label="external prepared parent",
    )
    prefix = f".{target_name}.prepared."
    container_name: str | None = None
    for _attempt in range(REGULAR_FILE_OVERLAY_TEMP_ATTEMPTS):
        candidate_name = f"{prefix}{secrets.token_hex(16)}"
        _assert_regular_file_overlay_directory_binding(
            parent,
            label="external prepared parent before container creation",
        )
        try:
            os.mkdir(candidate_name, 0o700, dir_fd=parent.descriptor)
        except FileExistsError:
            continue
        except OSError as exc:
            raise SyncError(
                f"cannot create external prepared container: {exc}"
            ) from exc
        except BaseException as exc:
            detail = (
                "external prepared tree may be retained at "
                f"{parent.path / candidate_name}"
            )
            if isinstance(exc, Exception):
                raise SyncError(f"{type(exc).__name__}: {exc}; {detail}") from exc
            _attach_base_exception_detail(exc, detail)
            raise
        container_name = candidate_name
        break
    if container_name is None:
        raise SyncError("cannot allocate an external prepared container")

    container_path = parent.path / container_name
    try:
        created = os.stat(
            container_name,
            dir_fd=parent.descriptor,
            follow_symlinks=False,
        )
        _validate_regular_file_overlay_tree_directory(
            created,
            label="external prepared container",
        )
        if stat.S_IMODE(created.st_mode) != 0o700:
            raise SyncError("external prepared container must have mode 0700")
        container = _pin_regular_file_overlay_child_directory(
            stack,
            parent,
            container_name,
            path=container_path,
            label="external prepared container",
        )
        if _overlay_file_identity(
            os.fstat(container.descriptor)
        ) != _overlay_file_identity(created):
            raise SyncError(
                "external prepared container changed after descriptor-relative creation"
            )
    except BaseException as exc:
        detail = f"external prepared tree retained at {container_path}"
        if isinstance(exc, SyncError):
            raise SyncError(f"{exc}; {detail}") from exc
        if isinstance(exc, Exception):
            raise SyncError(f"{type(exc).__name__}: {exc}; {detail}") from exc
        _attach_base_exception_detail(exc, detail)
        raise
    return parent, container


def _regular_file_overlay_manifest_index(
    manifest: _RegularFileOverlayTreeManifest,
    *,
    label: str,
) -> dict[tuple[str, ...], _RegularFileOverlayTreeEntry]:
    if len(manifest.entries) > MAX_REGULAR_FILE_OVERLAY_TREE_ENTRIES:
        raise SyncError(
            f"regular-file overlay {label} manifest exceeds its entry capacity"
        )
    if (
        manifest.total_bytes < 0
        or manifest.total_bytes > MAX_REGULAR_FILE_OVERLAY_TREE_BYTES
    ):
        raise SyncError(
            f"regular-file overlay {label} manifest exceeds its byte capacity"
        )
    entries: dict[tuple[str, ...], _RegularFileOverlayTreeEntry] = {}
    for entry in manifest.entries:
        if not entry.relative_parts or entry.relative_parts in entries:
            raise SyncError(f"regular-file overlay {label} manifest is ambiguous")
        if entry.kind not in {"directory", "file"}:
            raise SyncError(f"regular-file overlay {label} manifest kind is invalid")
        if entry.kind == "directory" and (entry.size != 0 or entry.sha256 is not None):
            raise SyncError(
                f"regular-file overlay {label} directory manifest is invalid"
            )
        if entry.kind == "file" and (
            entry.size < 0 or entry.sha256 is None or len(entry.sha256) != 64
        ):
            raise SyncError(f"regular-file overlay {label} file manifest is invalid")
        entries[entry.relative_parts] = entry
    if sum(entry.size for entry in entries.values()) != manifest.total_bytes:
        raise SyncError(f"regular-file overlay {label} manifest size is inconsistent")
    return entries


def _require_regular_file_overlay_manifest_entry(
    entries: dict[tuple[str, ...], _RegularFileOverlayTreeEntry],
    relative: Path,
    *,
    kind: str,
    label: str,
) -> _RegularFileOverlayTreeEntry:
    entry = entries.get(relative.parts)
    if entry is None:
        raise SyncError(
            f"regular-file overlay {label} gained an unregistered entry: {relative}"
        )
    if entry.kind != kind:
        raise SyncError(f"regular-file overlay {label} entry type changed: {relative}")
    return entry


def _apply_regular_file_overlay_rule_to_bytes(
    data: bytes,
    relative: Path,
    rule: SyncRule,
    found_replacements: dict[int, int],
) -> bytes:
    if not _is_text_candidate(relative, rule.text_extensions):
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    if relative not in rule.replacement_excluded_paths:
        for index, replacement in enumerate(rule.replacements):
            if replacement.path is not None and replacement.path != relative:
                continue
            if replacement.old not in text:
                continue
            found_replacements[index] = found_replacements.get(index, 0) + text.count(
                replacement.old
            )
            text = text.replace(replacement.old, replacement.new)
    for residual in rule.forbidden_residuals:
        if residual in text:
            raise SyncError(f"forbidden residual {residual!r} remains in {relative}")
    return text.encode("utf-8")


def _validate_regular_file_overlay_policy_bytes(
    data: bytes,
    relative: Path,
    target: Path,
    *,
    surface: str,
) -> None:
    if relative.suffix != ".md":
        return
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SyncError(
            "regular-file overlay policy cannot decode UTF-8 markdown at "
            f"{surface} {target / relative}"
        ) from exc
    for reference in RETIRED_REVIEW_REFERENCES:
        if reference in text:
            raise SyncError(
                "regular-file overlay target retains retired reference "
                f"{reference!r} at {surface} {target / relative}"
            )


def _validate_regular_file_overlay_required_manifest_paths(
    manifest: _RegularFileOverlayTreeManifest,
    target: Path,
    *,
    surface: str,
) -> None:
    if target != CANONICAL_REVIEW_TARGET:
        return
    files = {entry.relative_parts for entry in manifest.entries if entry.kind == "file"}
    for relative in CANONICAL_REVIEW_REQUIRED_FILES:
        if relative.parts not in files:
            raise SyncError(
                "canonical review target missing required file at "
                f"{surface}: {relative}"
            )
    _validate_canonical_review_exact_tree_inventories(
        files,
        surface=surface,
    )


def _validate_private_bug_triage_reviewed_manifest(
    manifest: _RegularFileOverlayTreeManifest,
    target: Path,
    *,
    surface: str,
) -> None:
    """Enforce descriptor-bound executable inventory and exact-byte admission."""

    if target != PRIVATE_BUG_TRIAGE_TARGET:
        return

    scripts_entries = {
        entry.relative_parts: entry
        for entry in manifest.entries
        if entry.relative_parts[:1] == ("scripts",)
    }
    expected_scripts_entries = {
        ("scripts",): "directory",
        ("scripts", "jenkins_artifact_probe.py"): "file",
    }
    observed_kinds = {
        relative_parts: entry.kind for relative_parts, entry in scripts_entries.items()
    }
    if observed_kinds != expected_scripts_entries:
        raise SyncError(
            f"private bug-triage reviewed scripts inventory differs at {surface}"
        )

    helper = scripts_entries[("scripts", "jenkins_artifact_probe.py")]
    if helper.sha256 != PRIVATE_BUG_TRIAGE_REVIEWED_HELPER_SHA256:
        raise SyncError(
            "private bug-triage reviewed helper payload digest mismatch at "
            f"{surface} ({helper.sha256} != "
            f"{PRIVATE_BUG_TRIAGE_REVIEWED_HELPER_SHA256})"
        )


def _copy_regular_file_overlay_public_source_to_prepared(
    source: Path,
    prepared: Path,
    *,
    prepared_root: _PinnedRegularFileOverlayDirectory,
    rule: SyncRule,
    locked_source: _LockedRuleSource | None = None,
) -> _RegularFileOverlayTreeManifest:
    ignored_names = EXCLUDED_NAMES | frozenset(rule.exclude_names)
    raw_entry_validator: Callable[[tuple[str, ...]], None] | None = None
    if rule.target == CANONICAL_REVIEW_TARGET:

        def validate_raw_entry(relative_parts: tuple[str, ...]) -> None:
            _validate_canonical_review_raw_tree_entry(
                relative_parts,
                surface="public source",
            )

        raw_entry_validator = validate_raw_entry
    nonblocking = getattr(os, "O_NONBLOCK", None)
    if nonblocking is None:
        raise SyncError(
            "secure public-source regular-file overlay nonblocking open is unavailable"
        )
    if prepared_root.path != prepared:
        raise SyncError("bounded prepared public root mismatch")
    _assert_regular_file_overlay_directory_binding(
        prepared_root,
        label="prepared public root",
    )

    with contextlib.ExitStack() as stack:
        source_root = _pin_regular_file_overlay_directory(
            stack,
            source,
            label="public source root",
        )
        source_root_metadata = os.fstat(source_root.descriptor)
        _validate_regular_file_overlay_tree_directory(
            source_root_metadata,
            label="public source root",
        )
        source_manifest = _capture_regular_file_overlay_tree_manifest(
            source_root.descriptor,
            label="initial public source",
            ignored_names=ignored_names,
            raw_entry_validator=raw_entry_validator,
        )
        locked_entries: dict[tuple[str, ...], object] = {}
        if locked_source is not None:
            if getattr(locked_source.manifest, "root_kind", None) != "tree":
                raise SyncError("locked directory source manifest kind differs")
            locked_entries = {
                entry.relative.parts: entry
                for entry in getattr(locked_source.manifest, "entries", ())
            }
            physical_kinds = {
                entry.relative_parts: entry.kind for entry in source_manifest.entries
            }
            locked_kinds = {
                parts: getattr(entry, "kind", None)
                for parts, entry in locked_entries.items()
            }
            if physical_kinds != locked_kinds:
                raise SyncError(
                    "physical source inventory differs from the locked Git tree"
                )
        replacement_candidates = {
            Path(*entry.relative_parts)
            for entry in source_manifest.entries
            if entry.kind == "file"
            and _is_text_candidate(
                Path(*entry.relative_parts),
                rule.text_extensions,
            )
        }
        _validate_replacement_excluded_candidates(
            rule,
            replacement_candidates,
            surface="public source",
        )
        if (
            _overlay_file_identity(source_root_metadata)
            != source_manifest.root_identity
        ):
            raise SyncError("regular-file overlay public source root changed")
        expected_entries = _regular_file_overlay_manifest_index(
            source_manifest,
            label="initial public source",
        )
        visited_entries: set[tuple[str, ...]] = set()
        found_replacements: dict[int, int] = {}
        budget = _RegularFileOverlayCopyBudget()
        manifest_builder = _RegularFileOverlayManifestBuilder()
        source_file_flags = (
            os.O_RDONLY | os.O_NOFOLLOW | nonblocking | getattr(os, "O_CLOEXEC", 0)
        )
        destination_file_flags = (
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
        )

        def copy_directory(
            source_directory: _PinnedRegularFileOverlayDirectory,
            destination_directory: _PinnedRegularFileOverlayDirectory,
            relative: Path,
            depth: int,
        ) -> None:
            if depth > MAX_REGULAR_FILE_OVERLAY_TREE_DEPTH:
                raise SyncError(
                    "regular-file overlay public source tree depth exceeds "
                    f"{MAX_REGULAR_FILE_OVERLAY_TREE_DEPTH}"
                )
            names = _bounded_regular_file_overlay_tree_names(
                source_directory.descriptor,
                maximum=(
                    MAX_REGULAR_FILE_OVERLAY_TREE_ENTRIES - budget.scanned_entries
                ),
                label="public source",
            )
            budget.scanned_entries += len(names)
            for name in names:
                child_relative = relative / name
                if raw_entry_validator is not None:
                    raw_entry_validator(child_relative.parts)
                if _is_ignored_name(name, ignored_names):
                    continue
                if len(child_relative.parts) > MAX_REGULAR_FILE_OVERLAY_TREE_DEPTH:
                    raise SyncError(
                        "regular-file overlay public source tree depth exceeds "
                        f"{MAX_REGULAR_FILE_OVERLAY_TREE_DEPTH}"
                    )
                budget.reserve_entry(label="public source")
                try:
                    named_before = os.stat(
                        name,
                        dir_fd=source_directory.descriptor,
                        follow_symlinks=False,
                    )
                except OSError as exc:
                    raise SyncError(
                        "cannot inspect regular-file overlay public source entry "
                        f"{child_relative}: {exc}"
                    ) from exc
                if stat.S_ISLNK(named_before.st_mode):
                    raise SyncError(
                        "refusing regular-file overlay public source symlink: "
                        f"{child_relative}"
                    )
                if stat.S_ISDIR(named_before.st_mode):
                    expected = _require_regular_file_overlay_manifest_entry(
                        expected_entries,
                        child_relative,
                        kind="directory",
                        label="public source",
                    )
                    with contextlib.ExitStack() as child_stack:
                        source_child = _pin_regular_file_overlay_child_directory(
                            child_stack,
                            source_directory,
                            name,
                            path=source_directory.path / name,
                            label="public source directory",
                        )
                        source_opened = os.fstat(source_child.descriptor)
                        _validate_regular_file_overlay_tree_directory(
                            source_opened,
                            label=f"public source directory {child_relative}",
                        )
                        source_identity = _overlay_file_identity(source_opened)
                        if (
                            source_identity != expected.identity
                            or _overlay_file_identity(named_before) != expected.identity
                        ):
                            raise SyncError(
                                "regular-file overlay public source directory binding "
                                f"changed: {child_relative}"
                            )
                        visited_entries.add(child_relative.parts)
                        try:
                            os.mkdir(
                                name,
                                0o700,
                                dir_fd=destination_directory.descriptor,
                            )
                        except OSError as exc:
                            raise SyncError(
                                "cannot create bounded prepared public directory "
                                f"{child_relative}: {exc}"
                            ) from exc
                        destination_child = _pin_regular_file_overlay_child_directory(
                            child_stack,
                            destination_directory,
                            name,
                            path=destination_directory.path / name,
                            label="prepared public directory",
                        )
                        copy_directory(
                            source_child,
                            destination_child,
                            child_relative,
                            depth + 1,
                        )
                        try:
                            source_after = os.fstat(source_child.descriptor)
                            source_named_after = os.stat(
                                name,
                                dir_fd=source_directory.descriptor,
                                follow_symlinks=False,
                            )
                            os.fchmod(
                                destination_child.descriptor,
                                stat.S_IMODE(source_opened.st_mode),
                            )
                            destination_after = os.fstat(destination_child.descriptor)
                        except OSError as exc:
                            raise SyncError(
                                "cannot finalize bounded prepared public directory "
                                f"{child_relative}: {exc}"
                            ) from exc
                        if (
                            _overlay_file_identity(source_after) != expected.identity
                            or _overlay_file_identity(source_named_after)
                            != expected.identity
                        ):
                            raise SyncError(
                                "regular-file overlay public source directory changed "
                                f"while copying: {child_relative}"
                            )
                        manifest_builder.record_directory(
                            child_relative,
                            destination_after,
                            label="prepared public directory",
                        )
                    continue
                if not stat.S_ISREG(named_before.st_mode):
                    raise SyncError(
                        "unsupported regular-file overlay public source entry: "
                        f"{child_relative}"
                    )
                expected = _require_regular_file_overlay_manifest_entry(
                    expected_entries,
                    child_relative,
                    kind="file",
                    label="public source",
                )
                try:
                    source_descriptor = os.open(
                        name,
                        source_file_flags,
                        dir_fd=source_directory.descriptor,
                    )
                except OSError as exc:
                    raise SyncError(
                        "cannot open regular-file overlay public source file "
                        f"{child_relative}: {exc}"
                    ) from exc
                try:
                    source_opened = os.fstat(source_descriptor)
                    _validate_overlay_regular_file(
                        source_opened,
                        label="public source file",
                        path=child_relative,
                    )
                    expected_source_content_identity = _overlay_file_content_identity(
                        source_opened
                    )
                    if (
                        _overlay_file_identity(source_opened) != expected.identity
                        or source_opened.st_size != expected.size
                        or _overlay_file_identity(named_before) != expected.identity
                        or named_before.st_size != expected.size
                    ):
                        raise SyncError(
                            "regular-file overlay public source file binding changed: "
                            f"{child_relative}"
                        )
                    source_data = _read_regular_file_overlay_descriptor(
                        source_descriptor,
                        byte_limit=expected.size,
                    )
                    source_digest = hashlib.sha256(source_data).hexdigest()
                    try:
                        source_after = os.fstat(source_descriptor)
                        source_named_after = os.stat(
                            name,
                            dir_fd=source_directory.descriptor,
                            follow_symlinks=False,
                        )
                    except OSError as exc:
                        raise SyncError(
                            "cannot verify regular-file overlay public source file "
                            f"{child_relative}: {exc}"
                        ) from exc
                    if (
                        len(source_data) != expected.size
                        or source_digest != expected.sha256
                        or _overlay_file_content_identity(source_after)
                        != expected_source_content_identity
                        or _overlay_file_content_identity(source_named_after)
                        != expected_source_content_identity
                    ):
                        raise SyncError(
                            "regular-file overlay public source file changed while "
                            f"copying: {child_relative}"
                        )
                    if locked_source is not None:
                        locked_entry = locked_entries.get(child_relative.parts)
                        if (
                            locked_entry is None
                            or getattr(locked_entry, "kind", None) != "file"
                            or stat.S_IMODE(source_opened.st_mode)
                            != getattr(locked_entry, "mode", None)
                            or source_data
                            != locked_source.read_blob(
                                locked_source.checkout,
                                getattr(locked_entry, "object_id", ""),
                            )
                        ):
                            raise SyncError(
                                "physical source file differs from the locked Git "
                                f"blob: {child_relative}"
                            )
                    output_data = _apply_regular_file_overlay_rule_to_bytes(
                        source_data,
                        child_relative,
                        rule,
                        found_replacements,
                    )
                    _validate_regular_file_overlay_policy_bytes(
                        output_data,
                        child_relative,
                        rule.target,
                        surface="prepared public source",
                    )
                    budget.reserve_bytes(len(output_data), label="prepared public")
                    try:
                        destination_descriptor = os.open(
                            name,
                            destination_file_flags,
                            0o600,
                            dir_fd=destination_directory.descriptor,
                        )
                    except OSError as exc:
                        raise SyncError(
                            "cannot create bounded prepared public file "
                            f"{child_relative}: {exc}"
                        ) from exc
                    try:
                        _write_regular_file_overlay_descriptor(
                            destination_descriptor,
                            output_data,
                        )
                        os.fchmod(
                            destination_descriptor,
                            stat.S_IMODE(source_opened.st_mode),
                        )
                        destination_before = os.fstat(destination_descriptor)
                        destination_digest = _hash_regular_file_overlay_descriptor(
                            destination_descriptor,
                            initial_size=len(output_data),
                            label=f"prepared public file {child_relative}",
                        )
                        destination_after = os.fstat(destination_descriptor)
                    finally:
                        os.close(destination_descriptor)
                    if (
                        destination_before.st_size != len(output_data)
                        or _overlay_file_content_identity(destination_after)
                        != _overlay_file_content_identity(destination_before)
                        or destination_digest != hashlib.sha256(output_data).hexdigest()
                    ):
                        raise SyncError(
                            "regular-file overlay public source file changed while "
                            f"copying: {child_relative}"
                        )
                    manifest_builder.record_file(
                        child_relative,
                        destination_after,
                        size=len(output_data),
                        sha256=destination_digest,
                        label="prepared public file",
                    )
                    visited_entries.add(child_relative.parts)
                finally:
                    os.close(source_descriptor)
            final_names = _bounded_regular_file_overlay_tree_names(
                source_directory.descriptor,
                maximum=len(names),
                label="public source",
            )
            if final_names != names:
                raise SyncError(
                    "regular-file overlay public source tree changed while copying"
                )

        copy_directory(source_root, prepared_root, Path(), 0)
        missing_entries = set(expected_entries) - visited_entries
        extra_entries = visited_entries - set(expected_entries)
        if missing_entries or extra_entries:
            raise SyncError(
                "regular-file overlay public source manifest coverage changed"
            )
        _validate_replacement_counts(rule, found_replacements)
        try:
            source_root_after = os.fstat(source_root.descriptor)
            os.fchmod(
                prepared_root.descriptor,
                stat.S_IMODE(source_root_metadata.st_mode),
            )
            prepared_root_after = os.fstat(prepared_root.descriptor)
        except OSError as exc:
            raise SyncError(
                f"cannot finalize bounded prepared public tree: {exc}"
            ) from exc
        if _overlay_file_identity(source_root_after) != _overlay_file_identity(
            source_root_metadata
        ):
            raise SyncError(
                "regular-file overlay public source root changed while copying"
            )
        final_source_manifest = _capture_regular_file_overlay_tree_manifest(
            source_root.descriptor,
            label="final public source",
            ignored_names=ignored_names,
            raw_entry_validator=raw_entry_validator,
        )
        if final_source_manifest != source_manifest:
            raise SyncError(
                "regular-file overlay public source exact tree manifest changed "
                "while copying"
            )
        final_prepared_root = _PinnedRegularFileOverlayDirectory(
            path=prepared_root.path,
            descriptor=prepared_root.descriptor,
            identity=_overlay_root_identity(prepared_root_after),
        )
        manifest = manifest_builder.finish(
            prepared_root_after,
            expected_entries=budget.entries,
            expected_bytes=budget.total_bytes,
            label="prepared public root",
        )
        _validate_regular_file_overlay_required_manifest_paths(
            manifest,
            rule.target,
            surface="prepared public source",
        )
        if rule.target == PRIVATE_BUG_TRIAGE_TARGET:
            # Preserve structural diagnostics, while the descriptor-bound
            # manifest below remains the installation admission control.
            _validate_private_bug_triage_target_contents(prepared)
        # The manifest is captured from descriptor-bound file content and is
        # revalidated before install.  Comparing its helper digest protects
        # the reviewed byte content without reopening a pathname in the
        # production locked-source lane.
        _validate_private_bug_triage_reviewed_manifest(
            manifest,
            rule.target,
            surface="prepared public source",
        )
        if (
            _capture_regular_file_overlay_tree_manifest(
                prepared_root.descriptor,
                label="prepared public tree",
            )
            != manifest
        ):
            raise SyncError(
                "regular-file overlay prepared public exact tree manifest changed "
                "during bounded copy"
            )
        _assert_regular_file_overlay_directory_binding(
            source_root,
            label="public source root",
        )
        _assert_regular_file_overlay_directory_binding(
            final_prepared_root,
            label="prepared public root",
        )
        return manifest


def _read_expected_prepared_regular_file_overlay_file(
    source_parent: _PinnedRegularFileOverlayDirectory,
    source_name: str,
    *,
    relative: Path,
    expected: _RegularFileOverlayTreeEntry,
) -> tuple[bytes, os.stat_result]:
    source = source_parent.path / source_name
    try:
        source_before = os.stat(
            source_name,
            dir_fd=source_parent.descriptor,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise SyncError(
            f"cannot inspect prepared overlay source: {source}: {exc}"
        ) from exc
    if (
        expected.kind != "file"
        or not stat.S_ISREG(source_before.st_mode)
        or _overlay_file_identity(source_before) != expected.identity
        or source_before.st_size != expected.size
    ):
        raise SyncError(f"prepared overlay source changed while opening: {source}")
    nonblocking = getattr(os, "O_NONBLOCK", None)
    if nonblocking is None:
        raise SyncError(
            "secure prepared regular-file overlay nonblocking open is unavailable"
        )
    source_flags = (
        os.O_RDONLY | os.O_NOFOLLOW | nonblocking | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        source_descriptor = os.open(
            source_name,
            source_flags,
            dir_fd=source_parent.descriptor,
        )
    except OSError as exc:
        raise SyncError(
            f"cannot open prepared overlay source: {source}: {exc}"
        ) from exc
    try:
        opened_source = os.fstat(source_descriptor)
        opened_content_identity = _overlay_file_content_identity(opened_source)
        if (
            _overlay_file_identity(opened_source) != expected.identity
            or opened_source.st_size != expected.size
            or opened_content_identity != _overlay_file_content_identity(source_before)
        ):
            raise SyncError(f"prepared overlay source changed while opening: {source}")
        source_data = _read_regular_file_overlay_descriptor(
            source_descriptor,
            byte_limit=expected.size,
        )
        if len(source_data) > expected.size:
            raise SyncError(f"prepared overlay source grew while copying: {source}")
        source_digest = hashlib.sha256(source_data).hexdigest()
        try:
            source_after = os.fstat(source_descriptor)
            source_named_after = os.stat(
                source_name,
                dir_fd=source_parent.descriptor,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise SyncError(
                f"cannot verify prepared overlay source: {source}: {exc}"
            ) from exc
        if (
            len(source_data) != expected.size
            or source_digest != expected.sha256
            or _overlay_file_content_identity(source_after) != opened_content_identity
            or _overlay_file_content_identity(source_named_after)
            != opened_content_identity
        ):
            raise SyncError(f"prepared overlay source changed while copying: {source}")
        return source_data, source_after
    finally:
        os.close(source_descriptor)


def _copy_prepared_regular_file_overlay_file(
    source_parent: _PinnedRegularFileOverlayDirectory,
    source_name: str,
    destination_parent: _PinnedRegularFileOverlayDirectory,
    destination_name: str,
    *,
    relative: Path,
    expected: _RegularFileOverlayTreeEntry,
    policy_target: Path,
    staging_scope: _RegularFileOverlayStagingScope,
    copy_budget: _RegularFileOverlayCopyBudget,
    manifest_builder: _RegularFileOverlayManifestBuilder,
) -> None:
    copy_budget.reserve_bytes(expected.size, label="prepared target")
    source_data, source_metadata = _read_expected_prepared_regular_file_overlay_file(
        source_parent,
        source_name,
        relative=relative,
        expected=expected,
    )
    _validate_regular_file_overlay_policy_bytes(
        source_data,
        relative,
        policy_target,
        surface="staged target",
    )
    _assert_regular_file_overlay_scope_binding(
        staging_scope,
        operation="prepared file creation",
    )
    _assert_regular_file_overlay_directory_binding(
        destination_parent,
        label="prepared file parent",
    )
    destination_flags = (
        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        destination_descriptor = os.open(
            destination_name,
            destination_flags,
            0o600,
            dir_fd=destination_parent.descriptor,
        )
    except OSError as exc:
        raise SyncError(
            "cannot create prepared regular-file overlay target: "
            f"{destination_parent.path / destination_name}: {exc}"
        ) from exc
    try:
        _write_regular_file_overlay_descriptor(destination_descriptor, source_data)
        os.fchmod(destination_descriptor, stat.S_IMODE(source_metadata.st_mode))
        copied_before = os.fstat(destination_descriptor)
        copied_digest = _hash_regular_file_overlay_descriptor(
            destination_descriptor,
            initial_size=expected.size,
            label=f"prepared target file {relative}",
        )
        copied_after = os.fstat(destination_descriptor)
    finally:
        os.close(destination_descriptor)
    if (
        copied_before.st_size != expected.size
        or _overlay_file_content_identity(copied_after)
        != _overlay_file_content_identity(copied_before)
        or copied_digest != expected.sha256
    ):
        raise SyncError(
            "prepared overlay target changed while copying: "
            f"{destination_parent.path / destination_name}"
        )
    manifest_builder.record_file(
        relative,
        copied_after,
        size=expected.size,
        sha256=copied_digest,
        label="prepared target file",
    )
    _assert_regular_file_overlay_scope_binding(
        staging_scope,
        operation="prepared file validation",
    )


def _create_prepared_regular_file_overlay_value(
    data: bytes,
    destination_parent: _PinnedRegularFileOverlayDirectory,
    destination_name: str,
    *,
    relative: Path,
    staging_scope: _RegularFileOverlayStagingScope,
    manifest_builder: _RegularFileOverlayManifestBuilder,
) -> None:
    if len(data) > MAX_REGULAR_FILE_OVERLAY_BYTES:
        raise SyncError(
            "regular-file overlay target data exceeds "
            f"{MAX_REGULAR_FILE_OVERLAY_BYTES} bytes: {destination_name}"
        )
    _assert_regular_file_overlay_scope_binding(
        staging_scope,
        operation="private overlay target creation",
    )
    _assert_regular_file_overlay_directory_binding(
        destination_parent,
        label="private overlay target parent",
    )
    flags = (
        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(
            destination_name,
            flags,
            0o600,
            dir_fd=destination_parent.descriptor,
        )
    except OSError as exc:
        raise SyncError(
            "cannot create private regular-file overlay target: "
            f"{destination_parent.path / destination_name}: {exc}"
        ) from exc
    try:
        _write_regular_file_overlay_descriptor(descriptor, data)
        os.fchmod(descriptor, REGULAR_FILE_OVERLAY_TARGET_MODE)
        written = os.fstat(descriptor)
        written_data = _read_regular_file_overlay_descriptor(
            descriptor,
            byte_limit=len(data),
        )
        final = os.fstat(descriptor)
        _validate_overlay_regular_file(
            final,
            label="private target",
            path=destination_parent.path / destination_name,
        )
        if (
            written_data != data
            or written.st_size != len(data)
            or _overlay_file_content_identity(written)
            != _overlay_file_content_identity(final)
            or stat.S_IMODE(final.st_mode) != REGULAR_FILE_OVERLAY_TARGET_MODE
        ):
            raise SyncError(
                "private regular-file overlay target verification failed: "
                f"{destination_parent.path / destination_name}"
            )
        manifest_builder.record_file(
            relative,
            final,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            label="private target",
        )
    finally:
        os.close(descriptor)
    _assert_regular_file_overlay_scope_binding(
        staging_scope,
        operation="private overlay target validation",
    )


def _copy_prepared_regular_file_overlay_directory(
    stack: contextlib.ExitStack,
    source: _PinnedRegularFileOverlayDirectory,
    destination: _PinnedRegularFileOverlayDirectory,
    *,
    staging_scope: _RegularFileOverlayStagingScope,
    relative: Path,
    policy_target: Path,
    expected_entries: dict[tuple[str, ...], _RegularFileOverlayTreeEntry],
    visited_entries: set[tuple[str, ...]],
    overlay_data: dict[Path, bytes],
    applied_overlays: set[Path],
    copy_budget: _RegularFileOverlayCopyBudget,
    manifest_builder: _RegularFileOverlayManifestBuilder,
) -> None:
    child_names = _bounded_regular_file_overlay_tree_names(
        source.descriptor,
        maximum=(MAX_REGULAR_FILE_OVERLAY_TREE_ENTRIES - copy_budget.scanned_entries),
        label="prepared source",
    )
    copy_budget.scanned_entries += len(child_names)
    for child_name in child_names:
        child = source.path / child_name
        child_relative = relative / child_name
        if len(child_relative.parts) > MAX_REGULAR_FILE_OVERLAY_TREE_DEPTH:
            raise SyncError(
                "regular-file overlay prepared target tree depth exceeds "
                f"{MAX_REGULAR_FILE_OVERLAY_TREE_DEPTH}"
            )
        copy_budget.reserve_entry(label="prepared target")
        try:
            metadata = os.stat(
                child_name,
                dir_fd=source.descriptor,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise SyncError(
                f"cannot inspect prepared overlay source: {child}: {exc}"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise SyncError(f"refusing prepared overlay source symlink: {child}")
        if child_relative in overlay_data and not stat.S_ISREG(metadata.st_mode):
            raise SyncError(
                f"regular-file overlay target is not a regular file: {child_relative}"
            )
        if stat.S_ISDIR(metadata.st_mode):
            expected = _require_regular_file_overlay_manifest_entry(
                expected_entries,
                child_relative,
                kind="directory",
                label="prepared source",
            )
            with contextlib.ExitStack() as child_stack:
                source_child = _pin_regular_file_overlay_child_directory(
                    child_stack,
                    source,
                    child_name,
                    path=child,
                    label="prepared source directory",
                )
                source_opened = os.fstat(source_child.descriptor)
                if (
                    _overlay_file_identity(metadata) != expected.identity
                    or _overlay_file_identity(source_opened) != expected.identity
                ):
                    raise SyncError(
                        f"prepared overlay source directory changed: {child_relative}"
                    )
                visited_entries.add(child_relative.parts)
                _assert_regular_file_overlay_scope_binding(
                    staging_scope,
                    operation="prepared directory creation",
                )
                _assert_regular_file_overlay_directory_binding(
                    destination,
                    label="prepared directory parent",
                )
                try:
                    os.mkdir(child_name, 0o700, dir_fd=destination.descriptor)
                except OSError as exc:
                    raise SyncError(
                        "cannot create prepared regular-file overlay directory: "
                        f"{destination.path / child_name}: {exc}"
                    ) from exc
                pinned_child = _pin_regular_file_overlay_child_directory(
                    child_stack,
                    destination,
                    child_name,
                    path=destination.path / child_name,
                    label="prepared directory",
                )
                _copy_prepared_regular_file_overlay_directory(
                    child_stack,
                    source_child,
                    pinned_child,
                    staging_scope=staging_scope,
                    relative=child_relative,
                    policy_target=policy_target,
                    expected_entries=expected_entries,
                    visited_entries=visited_entries,
                    overlay_data=overlay_data,
                    applied_overlays=applied_overlays,
                    copy_budget=copy_budget,
                    manifest_builder=manifest_builder,
                )
                _assert_regular_file_overlay_scope_binding(
                    staging_scope,
                    operation="prepared directory mode update",
                )
                try:
                    source_after = os.fstat(source_child.descriptor)
                    source_named_after = os.stat(
                        child_name,
                        dir_fd=source.descriptor,
                        follow_symlinks=False,
                    )
                except OSError as exc:
                    raise SyncError(
                        "cannot verify prepared overlay source directory "
                        f"{child_relative}: {exc}"
                    ) from exc
                if (
                    _overlay_file_identity(source_after) != expected.identity
                    or _overlay_file_identity(source_named_after) != expected.identity
                ):
                    raise SyncError(
                        f"prepared overlay source directory changed: {child_relative}"
                    )
                os.fchmod(
                    pinned_child.descriptor,
                    stat.S_IMODE(source_opened.st_mode),
                )
                manifest_builder.record_directory(
                    child_relative,
                    os.fstat(pinned_child.descriptor),
                    label="prepared target directory",
                )
            continue
        if stat.S_ISREG(metadata.st_mode):
            expected = _require_regular_file_overlay_manifest_entry(
                expected_entries,
                child_relative,
                kind="file",
                label="prepared source",
            )
            if (
                _overlay_file_identity(metadata) != expected.identity
                or metadata.st_size != expected.size
            ):
                raise SyncError(
                    f"prepared overlay source changed while opening: {child}"
                )
            if child_relative in overlay_data:
                _read_expected_prepared_regular_file_overlay_file(
                    source,
                    child_name,
                    relative=child_relative,
                    expected=expected,
                )
                _validate_regular_file_overlay_policy_bytes(
                    overlay_data[child_relative],
                    child_relative,
                    policy_target,
                    surface="staged target",
                )
                copy_budget.reserve_bytes(
                    len(overlay_data[child_relative]),
                    label="prepared target",
                )
                _create_prepared_regular_file_overlay_value(
                    overlay_data[child_relative],
                    destination,
                    child_name,
                    relative=child_relative,
                    staging_scope=staging_scope,
                    manifest_builder=manifest_builder,
                )
                applied_overlays.add(child_relative)
            else:
                _copy_prepared_regular_file_overlay_file(
                    source,
                    child_name,
                    destination,
                    child_name,
                    relative=child_relative,
                    expected=expected,
                    policy_target=policy_target,
                    staging_scope=staging_scope,
                    copy_budget=copy_budget,
                    manifest_builder=manifest_builder,
                )
            visited_entries.add(child_relative.parts)
            continue
        raise SyncError(f"unsupported prepared overlay source type: {child}")
    final_names = _bounded_regular_file_overlay_tree_names(
        source.descriptor,
        maximum=len(child_names),
        label="prepared source",
    )
    if final_names != child_names:
        raise SyncError(
            "regular-file overlay prepared source tree changed while copying"
        )


def _copy_prepared_regular_file_overlay_staging(
    stack: contextlib.ExitStack,
    source: Path,
    staging: Path,
    *,
    source_root: _PinnedRegularFileOverlayDirectory | None = None,
    staging_scope: _RegularFileOverlayStagingScope,
    policy_target: Path,
    overlay_data: dict[Path, bytes],
    expected_source_manifest: _RegularFileOverlayTreeManifest,
) -> _PreparedRegularFileOverlayCandidate:
    if source_root is None:
        source_root = _pin_regular_file_overlay_directory(
            stack,
            source,
            label="validated external prepared source",
        )
    elif source_root.path != source:
        raise SyncError("validated external prepared source path mismatch")
    source_metadata = os.fstat(source_root.descriptor)
    if (
        _overlay_file_identity(source_metadata)
        != expected_source_manifest.root_identity
    ):
        raise SyncError(
            "regular-file overlay validated external prepared source root changed"
        )
    expected_entries = _regular_file_overlay_manifest_index(
        expected_source_manifest,
        label="validated external prepared source",
    )
    _assert_regular_file_overlay_directory_binding(
        source_root,
        label="validated external prepared source",
    )
    if staging.parent != staging_scope.path:
        raise SyncError("prepared regular-file overlay staging scope mismatch")
    _assert_regular_file_overlay_scope_binding(
        staging_scope,
        operation="prepared staging root creation",
    )
    try:
        os.mkdir(staging.name, 0o700, dir_fd=staging_scope.container.descriptor)
    except OSError as exc:
        raise SyncError(f"cannot create prepared overlay staging root: {exc}") from exc
    staging_root = _pin_regular_file_overlay_child_directory(
        stack,
        staging_scope.container,
        staging.name,
        path=staging,
        label="staged target",
    )
    applied_overlays: set[Path] = set()
    visited_entries: set[tuple[str, ...]] = set()
    copy_budget = _RegularFileOverlayCopyBudget()
    manifest_builder = _RegularFileOverlayManifestBuilder()
    _copy_prepared_regular_file_overlay_directory(
        stack,
        source_root,
        staging_root,
        staging_scope=staging_scope,
        relative=Path(),
        policy_target=policy_target,
        expected_entries=expected_entries,
        visited_entries=visited_entries,
        overlay_data=overlay_data,
        applied_overlays=applied_overlays,
        copy_budget=copy_budget,
        manifest_builder=manifest_builder,
    )
    if visited_entries != set(expected_entries):
        raise SyncError(
            "regular-file overlay prepared source manifest coverage changed"
        )
    if applied_overlays != set(overlay_data):
        missing = sorted(str(path) for path in set(overlay_data) - applied_overlays)
        raise SyncError(
            "regular-file overlay target missing from prepared public tree: "
            + ", ".join(missing)
        )
    _assert_regular_file_overlay_scope_binding(
        staging_scope,
        operation="prepared staging root mode update",
    )
    os.fchmod(staging_root.descriptor, stat.S_IMODE(source_metadata.st_mode))
    pinned_root = _PinnedRegularFileOverlayDirectory(
        path=staging_root.path,
        descriptor=staging_root.descriptor,
        identity=_regular_file_overlay_directory_identity(
            staging_root.descriptor,
            label="staged target",
            path=staging_root.path,
        ),
    )
    manifest = manifest_builder.finish(
        os.fstat(staging_root.descriptor),
        expected_entries=copy_budget.entries,
        expected_bytes=copy_budget.total_bytes,
        label="prepared target root",
    )
    _validate_regular_file_overlay_required_manifest_paths(
        manifest,
        policy_target,
        surface="staged target",
    )
    _validate_private_bug_triage_reviewed_manifest(
        manifest,
        policy_target,
        surface="staged target",
    )
    if (
        _capture_regular_file_overlay_tree_manifest(
            staging_root.descriptor,
            label="prepared target",
        )
        != manifest
    ):
        raise SyncError(
            "regular-file overlay prepared target exact tree manifest changed "
            "during construction"
        )
    if (
        _capture_regular_file_overlay_tree_manifest(
            source_root.descriptor,
            label="validated external prepared source",
        )
        != expected_source_manifest
    ):
        raise SyncError(
            "regular-file overlay validated external prepared source exact tree "
            "manifest changed"
        )
    _assert_regular_file_overlay_directory_binding(
        source_root,
        label="validated external prepared source",
    )
    return _PreparedRegularFileOverlayCandidate(
        root=pinned_root,
        manifest=manifest,
    )


def _regular_file_overlay_recovery_scope_detail(
    scope: _RegularFileOverlayStagingScope,
) -> str:
    try:
        _assert_regular_file_overlay_scope_binding(
            scope,
            operation="failure reporting",
        )
    except SyncError:
        return (
            "regular-file overlay recovery scope pathname binding is unknown; "
            f"last-known path {scope.path} is untrusted"
        )
    return (
        f"regular-file overlay recovery scope retained for inspection at {scope.path}"
    )


@contextlib.contextmanager
def _regular_file_overlay_staging_directory(
    repo_binding: _PinnedRegularFileOverlayDirectory,
    target_relative: Path,
) -> Iterator[_RegularFileOverlayStagingScope]:
    if os.mkdir not in os.supports_dir_fd or os.scandir not in os.supports_fd:
        raise SyncError(
            "secure regular-file overlay descriptor-relative recovery is unavailable"
        )
    _require_overlay_relative_path(target_relative, field="sync target")
    repo_root = repo_binding.path
    with contextlib.ExitStack() as stack:
        _assert_regular_file_overlay_directory_binding(
            repo_binding,
            label="repository root",
        )
        _assert_regular_file_overlay_directory_binding(
            repo_binding,
            label="repository root before temporary-root creation",
        )
        temporary_root = _pin_or_create_regular_file_overlay_directory(
            stack,
            repo_binding,
            REGULAR_FILE_OVERLAY_RECOVERY_ROOT.parts[0],
            path=repo_root / REGULAR_FILE_OVERLAY_RECOVERY_ROOT.parts[0],
            label="temporary root",
            private=False,
        )
        _assert_regular_file_overlay_directory_binding(
            repo_binding,
            label="repository root before recovery-root creation",
        )
        _assert_regular_file_overlay_directory_binding(
            temporary_root,
            label="temporary root before recovery-root creation",
        )
        recovery_root = _pin_or_create_regular_file_overlay_directory(
            stack,
            temporary_root,
            REGULAR_FILE_OVERLAY_RECOVERY_ROOT.parts[1],
            path=repo_root / REGULAR_FILE_OVERLAY_RECOVERY_ROOT,
            label="recovery root",
            private=True,
        )
        target_parent_chain = _pin_or_create_regular_file_overlay_descendant_chain(
            stack,
            repo_binding,
            target_relative.parent,
            label="target parent",
        )
        target_parent = target_parent_chain[-1]
        if (
            os.fstat(recovery_root.descriptor).st_dev
            != os.fstat(target_parent.descriptor).st_dev
        ):
            raise SyncError(
                "regular-file overlay recovery and target must share a filesystem"
            )
        existing_recoveries = _bounded_regular_file_overlay_tree_names(
            recovery_root.descriptor,
            maximum=MAX_REGULAR_FILE_OVERLAY_RECOVERY_PATHS,
            label="recovery root",
        )
        if len(existing_recoveries) >= MAX_REGULAR_FILE_OVERLAY_RECOVERY_PATHS:
            raise SyncError(
                "regular-file overlay recovery root reached its bounded entry limit"
            )
        container_name = _regular_file_overlay_absent_name(
            recovery_root.descriptor,
            prefix=REGULAR_FILE_OVERLAY_RECOVERY_SCOPE_PREFIX,
        )
        _assert_regular_file_overlay_directory_binding(
            repo_binding,
            label="repository root before staging-container creation",
        )
        _assert_regular_file_overlay_directory_binding(
            temporary_root,
            label="temporary root before staging-container creation",
        )
        _assert_regular_file_overlay_directory_binding(
            recovery_root,
            label="recovery root before staging-container creation",
        )
        for pinned_parent in target_parent_chain:
            _assert_regular_file_overlay_directory_binding(
                pinned_parent,
                label="target parent before staging-container creation",
            )
        container_path = recovery_root.path / container_name
        try:
            try:
                os.mkdir(container_name, 0o700, dir_fd=recovery_root.descriptor)
            except OSError as exc:
                raise SyncError(
                    f"cannot create regular-file overlay staging container: {exc}"
                ) from exc
            container_descriptor = os.open(
                container_name,
                _regular_file_overlay_directory_flags(label="staging container"),
                dir_fd=recovery_root.descriptor,
            )
            stack.callback(os.close, container_descriptor)
            container = _PinnedRegularFileOverlayDirectory(
                path=container_path,
                descriptor=container_descriptor,
                identity=_regular_file_overlay_directory_identity(
                    container_descriptor,
                    label="staging container",
                    path=container_path,
                ),
            )
            scope = _RegularFileOverlayStagingScope(
                path=container_path,
                repo_root=repo_binding,
                temporary_root=temporary_root,
                recovery_root=recovery_root,
                target_parent=target_parent,
                target_parent_chain=target_parent_chain,
                container=container,
                resource_stack=stack,
            )
            _assert_regular_file_overlay_scope_binding(
                scope,
                operation="staging scope creation",
            )
        except BaseException as primary_error:
            detail = (
                "regular-file overlay recovery scope may be retained at "
                f"{container_path}"
            )
            if isinstance(primary_error, SyncError):
                raise SyncError(f"{primary_error}; {detail}") from primary_error
            if isinstance(primary_error, Exception):
                raise SyncError(
                    f"{type(primary_error).__name__}: {primary_error}; {detail}"
                ) from primary_error
            _attach_base_exception_detail(primary_error, detail)
            raise
        try:
            yield scope
        except BaseException as primary_error:
            detail = _regular_file_overlay_recovery_scope_detail(scope)
            if isinstance(primary_error, SyncError) and detail not in str(
                primary_error
            ):
                raise SyncError(f"{primary_error}; {detail}") from primary_error
            if isinstance(primary_error, Exception):
                raise SyncError(
                    f"{type(primary_error).__name__}: {primary_error}; {detail}"
                ) from primary_error
            _attach_base_exception_detail(primary_error, detail)
            raise
        else:
            if not scope.completed:
                raise SyncError(_regular_file_overlay_recovery_scope_detail(scope))


def _write_all(descriptor: int, payload: bytes, *, label: str) -> None:
    view = memoryview(payload)
    written = 0
    while written < len(view):
        try:
            count = os.write(descriptor, view[written:])
        except OSError as exc:
            raise SyncError(f"cannot write {label}: {exc}") from exc
        if count <= 0:
            raise SyncError(f"cannot make progress while writing {label}")
        written += count


@dataclass(frozen=True)
class _BoundPlainFileSnapshot:
    data: bytes
    object_identity: tuple[int, int, int]
    access_policy: tuple[int, int, int]
    content_identity: tuple[int, str]
    initial_timestamp_hint: tuple[int, int]
    final_timestamp_hint: tuple[int, int]
    timestamp_changed: bool


def _pinned_plain_file_object_identity(
    pinned: _PinnedRegularFileOverlayEntry,
) -> tuple[int, int, int]:
    device, inode, mode, _link_count, _owner = pinned.identity
    return device, inode, stat.S_IFMT(mode)


def _pinned_plain_file_access_policy(
    pinned: _PinnedRegularFileOverlayEntry,
) -> tuple[int, int, int]:
    _device, _inode, mode, link_count, owner = pinned.identity
    return owner, stat.S_IMODE(mode), link_count


def _stat_bound_plain_file_name(
    parent_descriptor: int,
    name: str,
    *,
    label: str,
) -> os.stat_result:
    try:
        return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise SyncError(f"{label} pathname is missing during revalidation") from exc
    except PermissionError as exc:
        raise SyncError(f"{label} pathname is unreadable during revalidation") from exc
    except OSError as exc:
        raise SyncError(f"{label} pathname revalidation failed: {exc}") from exc


def _assert_bound_plain_file_metadata(
    metadata: os.stat_result,
    pinned: _PinnedRegularFileOverlayEntry,
    *,
    expected_size: int | None,
    label: str,
) -> None:
    if _overlay_file_object_identity(metadata) != _pinned_plain_file_object_identity(
        pinned
    ):
        raise SyncError(f"{label} binding changed: object identity mismatch")
    if _overlay_file_access_policy(metadata) != _pinned_plain_file_access_policy(
        pinned
    ):
        raise SyncError(f"{label} access policy changed")
    if expected_size is not None and metadata.st_size != expected_size:
        raise SyncError(f"{label} content size changed")
    _validate_overlay_regular_file(metadata, label=label, path=Path(pinned.name))


def _read_bound_plain_file_pass(
    parent_descriptor: int,
    name: str,
    pinned: _PinnedRegularFileOverlayEntry,
    *,
    byte_limit: int,
    label: str,
) -> _BoundPlainFileSnapshot:
    try:
        before = os.fstat(pinned.descriptor)
    except OSError as exc:
        raise SyncError(f"{label} descriptor revalidation failed: {exc}") from exc
    _assert_bound_plain_file_metadata(
        before,
        pinned,
        expected_size=None,
        label=label,
    )
    if before.st_size > byte_limit:
        raise SyncError(f"{label} exceeds its bounded content size")
    named_before = _stat_bound_plain_file_name(
        parent_descriptor,
        name,
        label=label,
    )
    _assert_bound_plain_file_metadata(
        named_before,
        pinned,
        expected_size=before.st_size,
        label=label,
    )
    try:
        data = _read_regular_file_overlay_descriptor(
            pinned.descriptor,
            byte_limit=byte_limit,
        )
        after = os.fstat(pinned.descriptor)
    except OSError as exc:
        raise SyncError(f"cannot read or revalidate {label}: {exc}") from exc
    named_after = _stat_bound_plain_file_name(
        parent_descriptor,
        name,
        label=label,
    )
    for metadata in (after, named_after):
        _assert_bound_plain_file_metadata(
            metadata,
            pinned,
            expected_size=before.st_size,
            label=label,
        )
    content_identity = _overlay_plain_file_content_identity(data)
    if content_identity[0] != before.st_size:
        raise SyncError(f"{label} content changed while being read")
    timestamp_hints = tuple(
        _overlay_file_timestamp_hint(metadata)
        for metadata in (before, named_before, after, named_after)
    )
    return _BoundPlainFileSnapshot(
        data=data,
        object_identity=_overlay_file_object_identity(after),
        access_policy=_overlay_file_access_policy(after),
        content_identity=content_identity,
        initial_timestamp_hint=timestamp_hints[0],
        final_timestamp_hint=timestamp_hints[-1],
        timestamp_changed=len(set(timestamp_hints)) != 1,
    )


def _read_bound_plain_file_semantically(
    parent_descriptor: int,
    name: str,
    pinned: _PinnedRegularFileOverlayEntry,
    *,
    byte_limit: int,
    label: str,
) -> _BoundPlainFileSnapshot:
    first = _read_bound_plain_file_pass(
        parent_descriptor,
        name,
        pinned,
        byte_limit=byte_limit,
        label=label,
    )
    if not first.timestamp_changed:
        return first
    second = _read_bound_plain_file_pass(
        parent_descriptor,
        name,
        pinned,
        byte_limit=byte_limit,
        label=label,
    )
    if second.data != first.data or second.content_identity != first.content_identity:
        raise SyncError(f"{label} content changed during timestamp revalidation")
    if (
        second.initial_timestamp_hint != first.final_timestamp_hint
        or second.timestamp_changed
    ):
        raise SyncError(f"{label} timestamp revalidation did not stabilize")
    return second


def _assert_bound_plain_file(
    parent_descriptor: int,
    name: str,
    pinned: _PinnedRegularFileOverlayEntry,
    expected: bytes,
    expected_mode: int,
    *,
    label: str,
) -> None:
    snapshot = _read_bound_plain_file_semantically(
        parent_descriptor,
        name,
        pinned,
        byte_limit=len(expected),
        label=label,
    )
    if (
        snapshot.data != expected
        or snapshot.content_identity != _overlay_plain_file_content_identity(expected)
        or snapshot.access_policy[1] != expected_mode
    ):
        raise SyncError(f"{label} bytes or access policy differ from locked output")


def _is_authoritative_canonical_review_rule(rule: SyncRule) -> bool:
    return rule == _CANONICAL_REVIEW_SYNC_RULE


def _source_lock_git_line(
    source_lock_module: object,
    checkout: Path,
    *arguments: str,
    label: str,
) -> str:
    git_path = source_lock_module._trusted_git_path()
    completed = source_lock_module._git(git_path, checkout, *arguments)
    return source_lock_module._single_line(completed, label=label)


def _require_full_git_object_id(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SyncError(f"{label} is not a lowercase full Git object ID")
    return value


def _validate_reviewed_candidate_commit_proof(
    policy: CanonicalReviewMigrationPolicy,
) -> bytes:
    revision = _require_full_git_object_id(
        policy.reviewed_candidate_revision,
        label="reviewed canonical candidate revision",
    )
    approved_root_tree = _require_full_git_object_id(
        policy.approved_root_tree,
        label="reviewed canonical candidate root tree",
    )
    _require_full_git_object_id(
        policy.approved_review_subtree_tree,
        label="reviewed canonical candidate review subtree tree",
    )
    encoded = policy.reviewed_candidate_commit_payload_base64
    if (
        not isinstance(encoded, str)
        or not encoded
        or len(encoded) > ((MAX_CANONICAL_REVIEW_COMMIT_PROOF_BYTES * 4 + 2) // 3)
    ):
        raise SyncError("reviewed canonical candidate commit proof is invalid")
    try:
        payload = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise SyncError(
            "reviewed canonical candidate commit proof is not strict Base64"
        ) from exc
    if not payload or len(payload) > MAX_CANONICAL_REVIEW_COMMIT_PROOF_BYTES:
        raise SyncError("reviewed canonical candidate commit proof exceeds its limit")
    object_bytes = f"commit {len(payload)}\0".encode("ascii") + payload
    object_id = hashlib.sha1(object_bytes, usedforsecurity=False).hexdigest()
    if object_id != revision:
        raise SyncError(
            "reviewed canonical candidate commit proof differs from its revision"
        )
    header, separator, _message = payload.partition(b"\n\n")
    if not separator:
        raise SyncError("reviewed canonical candidate commit proof is malformed")
    header_lines = header.splitlines()
    tree_lines = [line for line in header_lines if line.startswith(b"tree ")]
    if len(tree_lines) != 1 or tree_lines[0] != (
        b"tree " + approved_root_tree.encode("ascii")
    ):
        raise SyncError(
            "reviewed canonical candidate commit proof does not bind the approved root tree"
        )
    if header_lines[0] != tree_lines[0]:
        raise SyncError("reviewed canonical candidate commit tree header is malformed")
    return payload


def _validate_reviewed_candidate_tree_path_proof(
    source_lock_module: object,
    checkout: Path,
    rule: SyncRule,
    policy: CanonicalReviewMigrationPolicy,
    proof_revision: str,
) -> None:
    proved_subtree = _source_lock_git_line(
        source_lock_module,
        checkout,
        "rev-parse",
        "--verify",
        f"{proof_revision}:{rule.source.as_posix()}",
        label="reviewed canonical candidate proved subtree tree",
    )
    if proved_subtree != policy.approved_review_subtree_tree:
        raise SyncError(
            "reviewed canonical candidate root tree does not bind the approved review subtree"
        )


def _bounded_ancestor_root_trees(
    source_lock_module: object,
    checkout: Path,
    revision: str,
) -> tuple[tuple[str, str], ...]:
    git_path = source_lock_module._trusted_git_path()
    completed = source_lock_module._git(
        git_path,
        checkout,
        "log",
        "--no-show-signature",
        "--no-decorate",
        "--format=%H %T",
        f"--max-count={MAX_CANONICAL_REVIEW_MIGRATION_ANCESTRY_COMMITS + 1}",
        revision,
    )
    try:
        lines = completed.stdout.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise SyncError("canonical review ancestry output is not ASCII") from exc
    if len(lines) > MAX_CANONICAL_REVIEW_MIGRATION_ANCESTRY_COMMITS:
        raise SyncError("canonical review migration ancestry exceeds its commit limit")
    records: list[tuple[str, str]] = []
    for line in lines:
        fields = line.split(" ")
        if len(fields) != 2:
            raise SyncError("canonical review ancestry record is malformed")
        records.append(
            (
                _require_full_git_object_id(
                    fields[0],
                    label="canonical review ancestry revision",
                ),
                _require_full_git_object_id(
                    fields[1],
                    label="canonical review ancestry root tree",
                ),
            )
        )
    if not records or records[0][0] != revision:
        raise SyncError("canonical review ancestry does not start at the live revision")
    return tuple(records)


def _source_lock_pin_records(
    source_lock: object,
) -> tuple[tuple[object, object, object, object], ...]:
    pins = getattr(source_lock, "pins", None)
    if not isinstance(pins, tuple):
        raise SyncError("verified source lock has an invalid pin collection")
    return tuple(
        (
            getattr(pin, "name", None),
            getattr(pin, "repository", None),
            getattr(pin, "sha", None),
            getattr(pin, "tree", None),
        )
        for pin in pins
    )


def _structured_checkout_receipt_is_complete(
    receipt: object,
    *,
    source_root: Path,
    pins: tuple[tuple[object, object, object, object], ...],
) -> bool:
    checkouts = getattr(receipt, "checkouts", None)
    source_root_binding = getattr(receipt, "source_root_binding", None)
    if (
        not isinstance(checkouts, tuple)
        or len(checkouts) != len(pins)
        or not isinstance(source_root_binding, tuple)
        or len(source_root_binding) != 4
    ):
        return False
    required_true = (
        "detached_head",
        "clean_worktree_and_index",
        "promisor_or_partial_clone_absent",
        "alternates_absent",
        "grafts_absent",
        "replace_refs_absent",
        "sparse_checkout_absent",
        "unsafe_config_absent",
        "tracked_modes_and_index_flags_safe",
        "object_closure_complete",
        "strict_fsck_complete",
    )
    for checkout, pin in zip(checkouts, pins, strict=True):
        name, repository, head, tree = pin
        if (
            getattr(checkout, "name", None) != name
            or getattr(checkout, "repository", None) != repository
            or getattr(checkout, "checkout", None) != source_root / str(name)
            or getattr(checkout, "git_directory", None)
            != source_root / str(name) / ".git"
            or getattr(checkout, "objects_directory", None)
            != source_root / str(name) / ".git" / "objects"
            or getattr(checkout, "head", None) != head
            or getattr(checkout, "tree", None) != tree
            or getattr(checkout, "shallow", None) is not False
            or getattr(checkout, "bare", None) is not False
            or any(
                getattr(checkout, field, None) is not True for field in required_true
            )
            or getattr(checkout, "safety_contract", None)
            != "private-overlay-complete-checkout-safety-v1"
        ):
            return False
        for binding_name in (
            "checkout_binding",
            "git_directory_binding",
            "objects_directory_binding",
        ):
            binding = getattr(checkout, binding_name, None)
            if not isinstance(binding, tuple) or len(binding) != 4:
                return False
        expected_file_paths = {
            "head_file": source_root / str(name) / ".git" / "HEAD",
            "local_config_file": source_root / str(name) / ".git" / "config",
        }
        for file_state_name, expected_path in expected_file_paths.items():
            file_state = getattr(checkout, file_state_name, None)
            if (
                file_state is None
                or getattr(file_state, "path", None) != expected_path
                or not isinstance(getattr(file_state, "object_identity", None), tuple)
                or len(file_state.object_identity) != 3
                or not isinstance(getattr(file_state, "access_policy", None), tuple)
                or len(file_state.access_policy) != 3
                or not isinstance(getattr(file_state, "size", None), int)
                or not isinstance(getattr(file_state, "sha256", None), str)
                or len(file_state.sha256) != 64
            ):
                return False
    return True


def _verify_complete_checkouts(
    source_lock_module: object,
    source_root: Path,
    source_lock: object,
    *,
    repo_root: Path,
    validate_generated_provenance: bool = False,
) -> _CompleteCheckoutVerification:
    pins_before = _source_lock_pin_records(source_lock)
    source_lock_digest = getattr(source_lock, "digest", None)
    if (
        not isinstance(source_lock_digest, str)
        or len(source_lock_digest) != 64
        or any(character not in "0123456789abcdef" for character in source_lock_digest)
    ):
        raise SyncError("source-lock digest is invalid for checkout verification")
    live_lock_before = source_lock_module.load_source_lock(repo_root)
    if (
        getattr(live_lock_before, "digest", None) != source_lock_digest
        or _source_lock_pin_records(live_lock_before) != pins_before
    ):
        raise SyncError("live source lock differs before checkout verification")
    checkout_receipt = source_lock_module.verify_checkouts(source_root, source_lock)
    if checkout_receipt is None:
        raise SyncError("checkout verifier did not return a structured receipt")
    receipt_pins = tuple(
        (
            getattr(pin, "name", None),
            getattr(pin, "repository", None),
            getattr(pin, "sha", None),
            getattr(pin, "tree", None),
        )
        for pin in getattr(checkout_receipt, "pins", ())
    )
    if (
        getattr(checkout_receipt, "source_root", None) != source_root
        or getattr(checkout_receipt, "safety_contract", None)
        != "private-overlay-complete-checkout-safety-v1"
        or receipt_pins != pins_before
        or not _structured_checkout_receipt_is_complete(
            checkout_receipt,
            source_root=source_root,
            pins=pins_before,
        )
    ):
        raise SyncError("checkout verifier returned a mismatched structured receipt")
    if validate_generated_provenance:
        source_lock_module.validate_generated_provenance(
            repo_root,
            source_lock,
            toolbox_checkout=source_root / source_lock.pins[0].name,
        )
    live_lock_after = source_lock_module.load_source_lock(repo_root)
    pins_after = _source_lock_pin_records(source_lock)
    if (
        pins_after != pins_before
        or getattr(live_lock_after, "digest", None) != source_lock_digest
        or _source_lock_pin_records(live_lock_after) != pins_before
    ):
        raise SyncError("source-lock state changed during checkout verification")
    return _CompleteCheckoutVerification(
        source_root=source_root,
        repo_root=repo_root,
        source_lock=source_lock,
        source_lock_module=source_lock_module,
        source_lock_digest=source_lock_digest,
        pins=pins_after,
        checkout_receipt=checkout_receipt,
        event=object(),
        seal=_COMPLETE_CHECKOUT_VERIFICATION_SEAL,
    )


def _assert_complete_checkout_verification(
    verification: _CompleteCheckoutVerification | None,
    *,
    source_root: Path,
    repo_root: Path | None = None,
    source_lock: object | None = None,
) -> None:
    if (
        type(verification) is not _CompleteCheckoutVerification
        or verification.seal is not _COMPLETE_CHECKOUT_VERIFICATION_SEAL
        or verification.source_root != source_root
        or (repo_root is not None and verification.repo_root != repo_root)
        or not isinstance(verification.source_lock_digest, str)
        or len(verification.source_lock_digest) != 64
        or verification.checkout_receipt is None
        or (
            source_lock is not None
            and (
                verification.source_lock is not source_lock
                or verification.pins != _source_lock_pin_records(source_lock)
            )
        )
    ):
        raise SyncError("locally complete checkout verification receipt is invalid")


def _assert_matching_checkout_verification_scope(
    initial: _CompleteCheckoutVerification,
    prewrite: _CompleteCheckoutVerification,
) -> None:
    _assert_complete_checkout_verification(
        initial,
        source_root=initial.source_root,
        repo_root=initial.repo_root,
        source_lock=initial.source_lock,
    )
    _assert_complete_checkout_verification(
        prewrite,
        source_root=initial.source_root,
        repo_root=initial.repo_root,
        source_lock=initial.source_lock,
    )
    if (
        prewrite.pins != initial.pins
        or prewrite.source_lock_digest != initial.source_lock_digest
        or prewrite.checkout_receipt != initial.checkout_receipt
    ):
        raise SyncError(
            "source-lock or checkout structured state changed between verifications"
        )
    if prewrite.event is initial.event:
        raise SyncError("prewrite checkout verification was not refreshed")


def _revalidate_complete_checkout_verification(
    verification: _CompleteCheckoutVerification | None,
) -> None:
    if verification is None:
        raise SyncError("locally complete checkout verification receipt is missing")
    _assert_complete_checkout_verification(
        verification,
        source_root=verification.source_root,
        repo_root=verification.repo_root,
        source_lock=verification.source_lock,
    )
    refreshed = _verify_complete_checkouts(
        verification.source_lock_module,
        verification.source_root,
        verification.source_lock,
        repo_root=verification.repo_root,
    )
    _assert_matching_checkout_verification_scope(verification, refreshed)


def _assert_checkout_verification_covers_source(
    verification: _CompleteCheckoutVerification | None,
    checkout: Path,
    source_pin: _VerifiedLockedSourcePin,
) -> None:
    _assert_complete_checkout_verification(
        verification,
        source_root=checkout.parent,
        repo_root=verification.repo_root if verification is not None else None,
    )
    if verification is None:
        raise SyncError("locally complete checkout verification receipt is missing")
    matching = tuple(
        record
        for record in verification.pins
        if record[0] == checkout.name
        and record[1] == source_pin.repository
        and record[2] == source_pin.revision
        and record[3] == source_pin.root_tree
    )
    if len(matching) != 1:
        raise SyncError(
            "complete checkout verification does not cover the canonical source pin"
        )


def _bind_canonical_review_migration_source(
    source_lock_module: object,
    checkout: Path,
    pin: object,
    manifest: object,
    rule: SyncRule,
    *,
    complete_checkout_verification: _CompleteCheckoutVerification,
) -> tuple[_VerifiedLockedSourcePin, _CanonicalReviewMigrationReceipt | None]:
    policy = rule.canonical_review_migration_policy
    if policy is None or not _is_authoritative_canonical_review_rule(rule):
        raise SyncError("canonical review migration policy is not authoritative")
    _assert_complete_checkout_verification(
        complete_checkout_verification,
        source_root=checkout.parent,
    )
    repository = getattr(pin, "repository", None)
    revision = _require_full_git_object_id(
        getattr(pin, "sha", None),
        label="canonical review source revision",
    )
    root_tree = _require_full_git_object_id(
        getattr(pin, "tree", None),
        label="canonical review source root tree",
    )
    if repository != policy.repository:
        raise SyncError("canonical review source repository is not approved")
    _validate_reviewed_candidate_commit_proof(policy)
    actual_revision = _source_lock_git_line(
        source_lock_module,
        checkout,
        "rev-parse",
        "--verify",
        "HEAD^{commit}",
        label="canonical review live revision",
    )
    actual_root_tree = _source_lock_git_line(
        source_lock_module,
        checkout,
        "rev-parse",
        "--verify",
        "HEAD^{tree}",
        label="canonical review live root tree",
    )
    if actual_revision != revision:
        raise SyncError("canonical review live revision differs from the source lock")
    if actual_root_tree != root_tree:
        raise SyncError("canonical review live root tree differs from the source lock")
    actual_review_subtree_tree = _source_lock_git_line(
        source_lock_module,
        checkout,
        "rev-parse",
        "--verify",
        f"{revision}:{rule.source.as_posix()}",
        label="canonical review live subtree tree",
    )
    manifest_review_subtree_tree = _require_full_git_object_id(
        getattr(manifest, "root_object_id", None),
        label="canonical review locked manifest root tree",
    )
    if actual_review_subtree_tree != manifest_review_subtree_tree:
        raise SyncError(
            "canonical review live subtree differs from the locked manifest"
        )

    source_pin = _VerifiedLockedSourcePin(
        repository=repository,
        revision=actual_revision,
        root_tree=actual_root_tree,
    )
    _assert_checkout_verification_covers_source(
        complete_checkout_verification,
        checkout,
        source_pin,
    )
    if (
        source_pin.revision == policy.legacy_revision
        and source_pin.root_tree == policy.legacy_root_tree
    ):
        return source_pin, None
    if actual_review_subtree_tree != policy.approved_review_subtree_tree:
        raise SyncError("canonical review live subtree is not approved for migration")
    if source_pin.root_tree == policy.approved_root_tree:
        activation_basis = "exact-approved-root-tree"
        proof_revision = source_pin.revision
    else:
        ancestry = _bounded_ancestor_root_trees(
            source_lock_module,
            checkout,
            source_pin.revision,
        )
        approved_ancestors = tuple(
            ancestor_revision
            for ancestor_revision, ancestor_tree in ancestry
            if ancestor_tree == policy.approved_root_tree
        )
        if not approved_ancestors:
            raise SyncError(
                "canonical review migration anchor-refresh-required: live root "
                "is neither the approved root nor a bounded descendant that "
                "retains it"
            )
        activation_basis = "bounded-approved-root-tree-ancestor"
        proof_revision = approved_ancestors[0]
    _validate_reviewed_candidate_tree_path_proof(
        source_lock_module,
        checkout,
        rule,
        policy,
        proof_revision,
    )
    return source_pin, _CanonicalReviewMigrationReceipt(
        policy=policy,
        source_pin=source_pin,
        live_review_subtree_tree=actual_review_subtree_tree,
        activation_basis=activation_basis,
    )


def _canonical_review_personal_agents_migration_required(
    rule: SyncRule,
    locked_source: _LockedRuleSource | None,
) -> bool:
    if not _is_authoritative_canonical_review_rule(rule):
        return False
    policy = rule.canonical_review_migration_policy
    if policy is None or locked_source is None or locked_source.source_pin is None:
        raise SyncError(
            "authoritative canonical review sync requires a verified source pin"
        )
    source_pin = locked_source.source_pin
    if source_pin.repository != policy.repository:
        raise SyncError("canonical review migration source repository differs")
    _assert_checkout_verification_covers_source(
        locked_source.prewrite_checkout_verification,
        locked_source.checkout,
        source_pin,
    )
    receipt = locked_source.canonical_review_migration_receipt
    if (
        source_pin.revision == policy.legacy_revision
        and source_pin.root_tree == policy.legacy_root_tree
    ):
        if receipt is not None:
            raise SyncError("legacy canonical review source has a migration receipt")
        return False
    if (
        receipt is None
        or receipt.policy != policy
        or receipt.source_pin != source_pin
        or receipt.live_review_subtree_tree != policy.approved_review_subtree_tree
        or getattr(locked_source.manifest, "root_object_id", None)
        != receipt.live_review_subtree_tree
        or receipt.activation_basis
        not in {
            "exact-approved-root-tree",
            "bounded-approved-root-tree-ancestor",
        }
    ):
        raise SyncError("canonical review migration receipt does not match its source")
    return True


def _migrate_personal_agents_guidance(
    repo_binding: _PinnedRegularFileOverlayDirectory,
    *,
    installed_receipt: _InstalledRegularFileOverlayReceipt | None = None,
) -> Path | None:
    if installed_receipt is not None:
        _assert_installed_regular_file_overlay_receipt(
            installed_receipt,
            label="pre-AGENTS migration",
        )
    target = repo_binding.path / PERSONAL_AGENTS_TARGET
    with contextlib.ExitStack() as source_stack:
        target_parent_chain = _pin_or_create_regular_file_overlay_descendant_chain(
            source_stack,
            repo_binding,
            PERSONAL_AGENTS_TARGET.parent,
            label="personal AGENTS parent",
        )
        target_parent = target_parent_chain[-1]
        prior = _pin_regular_file_overlay_entry(
            source_stack,
            target_parent.descriptor,
            target.name,
            label="personal AGENTS source",
        )
        prior_snapshot = _read_bound_plain_file_semantically(
            target_parent.descriptor,
            target.name,
            prior,
            byte_limit=MAX_REGULAR_FILE_OVERLAY_BYTES,
            label="personal AGENTS source",
        )
        prior_data = prior_snapshot.data
        prior_mode = prior_snapshot.access_policy[1]
        if prior_mode != 0o644:
            raise SyncError("personal AGENTS source mode must be 0644")
        _assert_bound_plain_file(
            target_parent.descriptor,
            target.name,
            prior,
            prior_data,
            prior_mode,
            label="personal AGENTS pre-migration source",
        )
        migrated_data = _migrated_personal_agents_bytes(prior_data)
        if migrated_data == prior_data:
            _assert_bound_plain_file(
                target_parent.descriptor,
                target.name,
                prior,
                prior_data,
                prior_mode,
                label="current personal AGENTS no-op state",
            )
            if installed_receipt is not None:
                _assert_installed_regular_file_overlay_receipt(
                    installed_receipt,
                    label="post-AGENTS no-op migration",
                )
            return None
        if len(migrated_data) > MAX_REGULAR_FILE_OVERLAY_BYTES:
            raise SyncError(
                "migrated personal AGENTS exceeds the bounded migration size"
            )

        primitive = _load_regular_file_overlay_noreplace_primitive()
        with _regular_file_overlay_staging_directory(
            repo_binding,
            PERSONAL_AGENTS_TARGET,
        ) as staging_scope:
            staging_name = target.name
            create_flags = (
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0)
            )
            try:
                descriptor = os.open(
                    staging_name,
                    create_flags,
                    0o600,
                    dir_fd=staging_scope.container.descriptor,
                )
            except OSError as exc:
                raise SyncError(
                    f"cannot create personal AGENTS migration candidate: {exc}"
                ) from exc
            try:
                _write_all(
                    descriptor,
                    migrated_data,
                    label="personal AGENTS migration candidate",
                )
                os.fchmod(descriptor, prior_mode)
                os.fsync(descriptor)
            except BaseException:
                os.close(descriptor)
                raise
            else:
                os.close(descriptor)

            with contextlib.ExitStack() as candidate_stack:
                candidate = _pin_regular_file_overlay_entry(
                    candidate_stack,
                    staging_scope.container.descriptor,
                    staging_name,
                    label="personal AGENTS migration candidate",
                )
                _assert_regular_file_overlay_scope_binding(
                    staging_scope,
                    operation="personal AGENTS migration preparation",
                )
                _assert_regular_file_overlay_retained_entries(
                    staging_scope,
                    exact_names={staging_name},
                )
                _assert_bound_plain_file(
                    staging_scope.container.descriptor,
                    staging_name,
                    candidate,
                    migrated_data,
                    prior_mode,
                    label="personal AGENTS migration candidate",
                )
                _assert_bound_plain_file(
                    target_parent.descriptor,
                    target.name,
                    prior,
                    prior_data,
                    prior_mode,
                    label="personal AGENTS pre-publish source",
                )
                backup_name = _regular_file_overlay_absent_name(
                    staging_scope.container.descriptor,
                    prefix=REGULAR_FILE_OVERLAY_BACKUP_PREFIX,
                )
                if installed_receipt is not None:
                    _assert_installed_regular_file_overlay_receipt(
                        installed_receipt,
                        label="immediate pre-AGENTS publication",
                    )
                _rename_regular_file_overlay_noreplace(
                    primitive,
                    target_parent.descriptor,
                    target.name,
                    staging_scope.container.descriptor,
                    backup_name,
                )
                _assert_bound_plain_file(
                    staging_scope.container.descriptor,
                    backup_name,
                    prior,
                    prior_data,
                    prior_mode,
                    label="retained personal AGENTS prior state",
                )
                _register_regular_file_overlay_retained_entry(
                    staging_scope,
                    backup_name,
                    prior,
                )
                _assert_regular_file_overlay_scope_binding(
                    staging_scope,
                    operation="personal AGENTS candidate publication",
                )
                _assert_regular_file_overlay_retained_entries(
                    staging_scope,
                    exact_names={staging_name, backup_name},
                )
                _assert_bound_plain_file(
                    staging_scope.container.descriptor,
                    staging_name,
                    candidate,
                    migrated_data,
                    prior_mode,
                    label="personal AGENTS migration candidate before publication",
                )
                if installed_receipt is not None:
                    _assert_installed_regular_file_overlay_receipt(
                        installed_receipt,
                        label="final pre-AGENTS publication",
                    )
                _rename_regular_file_overlay_noreplace(
                    primitive,
                    staging_scope.container.descriptor,
                    staging_name,
                    target_parent.descriptor,
                    target.name,
                )
                _assert_bound_plain_file(
                    target_parent.descriptor,
                    target.name,
                    candidate,
                    migrated_data,
                    prior_mode,
                    label="installed personal AGENTS migration",
                )
                if installed_receipt is not None:
                    _assert_installed_regular_file_overlay_receipt(
                        installed_receipt,
                        label="post-AGENTS publication",
                    )
                _assert_bound_plain_file(
                    staging_scope.container.descriptor,
                    backup_name,
                    prior,
                    prior_data,
                    prior_mode,
                    label="retained personal AGENTS prior state",
                )
                _assert_regular_file_overlay_directory_binding(
                    target_parent,
                    label="personal AGENTS parent",
                )
                _assert_regular_file_overlay_retained_entries(
                    staging_scope,
                    exact_names={backup_name},
                )
            staging_scope.completed = True
            return staging_scope.recovery_path


def _migrate_personal_agents_after_canonical_review_sync(
    repo_binding: _PinnedRegularFileOverlayDirectory,
    rule: SyncRule,
    locked_source: _LockedRuleSource,
    installed_migration_receipt: _CanonicalReviewInstalledMigrationReceipt,
) -> Path | None:
    if installed_migration_receipt.expected_target != (
        repo_binding.path / CANONICAL_REVIEW_TARGET
    ):
        raise SyncError(
            "canonical review migration installed receipt targets another tree"
        )
    _revalidate_complete_checkout_verification(
        locked_source.prewrite_checkout_verification
    )
    _assert_canonical_review_installed_migration_receipt(
        installed_migration_receipt,
        rule,
        locked_source,
        label="pre-AGENTS exact target",
    )
    _assert_regular_file_overlay_directory_binding(
        repo_binding,
        label="canonical review migration repository root",
    )
    recovery_path = _migrate_personal_agents_guidance(
        repo_binding,
        installed_receipt=installed_migration_receipt.installed_receipt,
    )
    _assert_canonical_review_installed_migration_receipt(
        installed_migration_receipt,
        rule,
        locked_source,
        label="post-AGENTS exact target",
    )
    _revalidate_complete_checkout_verification(
        locked_source.prewrite_checkout_verification
    )
    return recovery_path


def _install_locked_plain_file(
    repo_binding: _PinnedRegularFileOverlayDirectory,
    target: Path,
    rule: SyncRule,
    locked_source: _LockedRuleSource,
) -> Path | None:
    manifest = locked_source.manifest
    if getattr(manifest, "root_kind", None) != "file":
        raise SyncError(f"locked plain-file source kind differs: {rule.source}")
    source_data = locked_source.read_blob(
        locked_source.checkout,
        getattr(manifest, "root_object_id", ""),
    )
    found_replacements: dict[int, int] = {}
    output_data = _apply_regular_file_overlay_rule_to_bytes(
        source_data,
        Path(target.name),
        rule,
        found_replacements,
    )
    _validate_replacement_counts(rule, found_replacements)
    _validate_regular_file_overlay_policy_bytes(
        output_data,
        Path(target.name),
        rule.target,
        surface="locked staged source",
    )
    expected_mode = getattr(manifest, "root_mode", None)
    if expected_mode not in {0o644, 0o755}:
        raise SyncError(f"locked plain-file source mode differs: {rule.source}")

    with _regular_file_overlay_staging_directory(
        repo_binding,
        rule.target,
    ) as staging_scope:
        staging = staging_scope.path / target.name
        create_flags = (
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            descriptor = os.open(
                staging.name,
                create_flags,
                0o600,
                dir_fd=staging_scope.container.descriptor,
            )
        except OSError as exc:
            raise SyncError(f"cannot create locked plain-file staging: {exc}") from exc
        try:
            _write_all(descriptor, output_data, label="locked plain-file staging")
            os.fchmod(descriptor, expected_mode)
            os.fsync(descriptor)
        except BaseException:
            os.close(descriptor)
            raise
        else:
            os.close(descriptor)

        with contextlib.ExitStack() as stack:
            candidate = _pin_regular_file_overlay_entry(
                stack,
                staging_scope.container.descriptor,
                staging.name,
                label="locked plain-file candidate",
            )
            _assert_regular_file_overlay_scope_binding(
                staging_scope,
                operation="locked plain-file install preparation",
            )
            _assert_regular_file_overlay_retained_entries(
                staging_scope,
                exact_names={staging.name},
            )
            _assert_bound_plain_file(
                staging_scope.container.descriptor,
                staging.name,
                candidate,
                output_data,
                expected_mode,
                label="locked plain-file candidate",
            )
            try:
                os.replace(
                    staging.name,
                    target.name,
                    src_dir_fd=staging_scope.container.descriptor,
                    dst_dir_fd=staging_scope.target_parent.descriptor,
                )
            except OSError as exc:
                raise SyncError(
                    f"cannot install locked plain-file candidate: {exc}"
                ) from exc
            _assert_bound_plain_file(
                staging_scope.target_parent.descriptor,
                target.name,
                candidate,
                output_data,
                expected_mode,
                label="installed locked plain file",
            )
            _assert_regular_file_overlay_directory_binding(
                staging_scope.target_parent,
                label="target parent",
            )
            _assert_regular_file_overlay_retained_entries(
                staging_scope,
                exact_names=set(),
            )
        staging_scope.completed = True
        try:
            os.rmdir(
                staging_scope.container.path.name,
                dir_fd=staging_scope.recovery_root.descriptor,
            )
        except OSError as exc:
            raise SyncError(
                "installed locked plain file but cannot remove its empty staging "
                f"container: {exc}"
            ) from exc
    return None


def _sync_sources_with_repo_binding(
    repo_root: Path,
    source_root: Path,
    rules: tuple[SyncRule, ...],
    repo_binding: _PinnedRegularFileOverlayDirectory | None,
    locked_sources: dict[tuple[str, Path], _LockedRuleSource] | None = None,
) -> tuple[Path, ...]:
    recovery_paths: list[Path] = []
    for rule in rules:
        if repo_binding is not None:
            _assert_regular_file_overlay_directory_binding(
                repo_binding,
                label="repository root",
            )
        source_repo_root = source_root / rule.repo
        source = source_repo_root / rule.source
        target = repo_root / rule.target
        locked_source = (
            None
            if locked_sources is None
            else locked_sources.get((rule.repo, rule.source))
        )
        migrate_personal_agents = _canonical_review_personal_agents_migration_required(
            rule,
            locked_source,
        )
        if not source.exists():
            raise SyncError(f"sync source missing for {rule.repo}: {source}")
        _ensure_safe_source(source_repo_root, source)
        _ensure_safe_target(repo_root, target)
        if (
            rule.target == PRIVATE_BUG_TRIAGE_TARGET
            and locked_source is not None
            and getattr(locked_source.manifest, "root_kind", None) != "tree"
        ):
            raise SyncError("private bug-triage locked source must be a tree")
        if (
            locked_source is not None
            and getattr(locked_source.manifest, "root_kind", None) == "file"
        ):
            if repo_binding is None:
                raise SyncError("locked plain-file sync requires a pinned repository")
            recovery_path = _install_locked_plain_file(
                repo_binding,
                target,
                rule,
                locked_source,
            )
            if recovery_path is not None:
                recovery_paths.append(recovery_path)
            continue
        if (
            rule.regular_file_overlays
            or rule.target == PRIVATE_BUG_TRIAGE_TARGET
            or locked_source is not None
        ):
            if repo_binding is None:
                raise SyncError("secure sync requires a pinned repository root")
            if (
                locked_source is not None
                and getattr(locked_source.manifest, "root_kind", None) != "tree"
            ):
                raise SyncError(f"locked directory source kind differs: {rule.source}")
            with contextlib.ExitStack() as prepared_stack:
                prepared_parent, prepared_container = (
                    _create_external_prepared_regular_file_overlay_container(
                        prepared_stack,
                        target_name=target.name,
                    )
                )
                prepared_directory = prepared_container.path
                prepared = prepared_directory / target.name
                prepared_root: _PinnedRegularFileOverlayDirectory | None = None
                prepared_source_manifest: _RegularFileOverlayTreeManifest | None = None
                try:
                    try:
                        os.mkdir(
                            prepared.name,
                            0o700,
                            dir_fd=prepared_container.descriptor,
                        )
                    except OSError as exc:
                        raise SyncError(
                            f"cannot create bounded prepared public tree: {exc}"
                        ) from exc
                    prepared_root = _pin_regular_file_overlay_child_directory(
                        prepared_stack,
                        prepared_container,
                        prepared.name,
                        path=prepared,
                        label="prepared public root",
                    )
                    initial_prepared_manifest = (
                        _capture_regular_file_overlay_tree_manifest(
                            prepared_root.descriptor,
                            label="initial empty external prepared root",
                        )
                    )
                    if (
                        initial_prepared_manifest.entries
                        or initial_prepared_manifest.total_bytes != 0
                    ):
                        raise SyncError(
                            "initial external prepared root is not empty; retaining "
                            f"last-known path {prepared_directory}"
                        )
                    copy_keywords = {}
                    if locked_source is not None:
                        copy_keywords["locked_source"] = locked_source
                    prepared_source_manifest = (
                        _copy_regular_file_overlay_public_source_to_prepared(
                            source,
                            prepared,
                            prepared_root=prepared_root,
                            rule=rule,
                            **copy_keywords,
                        )
                    )
                    prepared_root = _PinnedRegularFileOverlayDirectory(
                        path=prepared_root.path,
                        descriptor=prepared_root.descriptor,
                        identity=_regular_file_overlay_directory_identity(
                            prepared_root.descriptor,
                            label="validated external prepared source",
                            path=prepared_root.path,
                        ),
                    )
                    if (
                        _capture_regular_file_overlay_tree_manifest(
                            prepared_root.descriptor,
                            label="validated external prepared source",
                        )
                        != prepared_source_manifest
                    ):
                        raise SyncError(
                            "validated external prepared source exact tree manifest "
                            "changed"
                        )
                    overlay_data = _load_regular_file_overlay_data(
                        repo_root,
                        rule,
                        repo_binding=repo_binding,
                    )
                    with _regular_file_overlay_staging_directory(
                        repo_binding,
                        rule.target,
                    ) as staging_scope:
                        staging = staging_scope.path / target.name
                        with contextlib.ExitStack() as binding_stack:
                            candidate = _copy_prepared_regular_file_overlay_staging(
                                binding_stack,
                                prepared,
                                staging,
                                source_root=prepared_root,
                                staging_scope=staging_scope,
                                policy_target=rule.target,
                                overlay_data=overlay_data,
                                expected_source_manifest=prepared_source_manifest,
                            )
                            _assert_regular_file_overlay_tree_manifest(
                                staging_scope.container.descriptor,
                                staging.name,
                                candidate.manifest,
                                label="validated staged target",
                            )
                            bindings = _pin_regular_file_overlay_targets(
                                binding_stack,
                                staging,
                                candidate.root,
                                overlay_data,
                                candidate.manifest,
                            )
                            _assert_regular_file_overlay_directory_binding(
                                prepared_parent,
                                label="retained external prepared parent",
                            )
                            _assert_regular_file_overlay_directory_binding(
                                prepared_container,
                                label="retained external prepared container",
                            )
                            _assert_regular_file_overlay_tree_manifest(
                                prepared_container.descriptor,
                                prepared.name,
                                prepared_source_manifest,
                                label="retained external prepared source",
                            )
                            if migrate_personal_agents:
                                _revalidate_complete_checkout_verification(
                                    locked_source.prewrite_checkout_verification
                                    if locked_source is not None
                                    else None
                                )
                            install_result = _replace_target_with_regular_file_overlays(
                                target,
                                staging,
                                bindings,
                                staging_scope=staging_scope,
                                candidate_root=candidate.root,
                                candidate_manifest=candidate.manifest,
                            )
                            agents_recovery_path = None
                            if migrate_personal_agents:
                                if not (
                                    _canonical_review_personal_agents_migration_required(
                                        rule,
                                        locked_source,
                                    )
                                ):
                                    raise SyncError(
                                        "canonical review migration receipt changed "
                                        "after tree install"
                                    )
                                if locked_source is None:
                                    raise SyncError(
                                        "canonical review locked source disappeared "
                                        "after tree install"
                                    )
                                installed_migration_receipt = (
                                    _bind_canonical_review_installed_migration_receipt(
                                        rule,
                                        locked_source,
                                        target,
                                        prepared_source_manifest,
                                        candidate.manifest,
                                        install_result.installed_receipt,
                                    )
                                )
                                agents_recovery_path = _migrate_personal_agents_after_canonical_review_sync(
                                    repo_binding,
                                    rule,
                                    locked_source,
                                    installed_migration_receipt,
                                )
                    if install_result.recovery_path is not None:
                        recovery_paths.append(install_result.recovery_path)
                    if agents_recovery_path is not None:
                        recovery_paths.append(agents_recovery_path)
                    recovery_paths.append(prepared_directory)
                except BaseException as primary_error:
                    detail = f"external prepared tree retained at {prepared_directory}"
                    if isinstance(primary_error, SyncError):
                        if detail not in str(primary_error):
                            raise SyncError(
                                f"{primary_error}; {detail}"
                            ) from primary_error
                    elif isinstance(primary_error, Exception):
                        raise SyncError(
                            f"{type(primary_error).__name__}: {primary_error}; {detail}"
                        ) from primary_error
                    else:
                        _attach_base_exception_detail(primary_error, detail)
                    raise
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f".{target.name}.staging.", dir=target.parent
        ) as temp_directory:
            staging = Path(temp_directory) / target.name
            _copy_source_to_staging(source, staging, exclude_names=rule.exclude_names)
            _apply_rule_replacements(staging, rule)
            _reject_forbidden_residuals(staging, rule)
            if rule.target == PRIVATE_BUG_TRIAGE_TARGET:
                _validate_private_bug_triage_target_contents(staging)
            if rule.target == CANONICAL_REVIEW_TARGET:
                _validate_canonical_review_target_contents(staging)
            _replace_target(target, staging)
    return tuple(recovery_paths)


def sync_sources(
    repo_root: Path,
    source_root: Path,
    rules: tuple[SyncRule, ...] = SYNC_RULES,
    *,
    locked_sources: dict[tuple[str, Path], _LockedRuleSource] | None = None,
) -> tuple[Path, ...]:
    repo_root = repo_root.resolve()
    source_root = source_root.resolve()
    _validate_regular_file_overlay_targets(rules)
    _validate_replacement_excluded_paths(rules)
    secure_rule_count = sum(bool(rule.regular_file_overlays) for rule in rules)
    if secure_rule_count > 1:
        raise SyncError("private overlay sync permits exactly one secure rule")
    if locked_sources is not None:
        expected_keys = {(rule.repo, rule.source) for rule in rules}
        if set(locked_sources) != expected_keys:
            raise SyncError("locked source manifests do not match the sync rules")
        checkout_verifications = {
            id(locked_source.prewrite_checkout_verification): (
                locked_source.prewrite_checkout_verification
            )
            for locked_source in locked_sources.values()
        }
        if None in checkout_verifications.values():
            raise SyncError(
                "locked source is missing its prewrite checkout verification"
            )
        for verification in checkout_verifications.values():
            _revalidate_complete_checkout_verification(verification)
    for rule in rules:
        locked_source = (
            None
            if locked_sources is None
            else locked_sources.get((rule.repo, rule.source))
        )
        _canonical_review_personal_agents_migration_required(
            rule,
            locked_source,
        )
    if locked_sources is not None:
        with contextlib.ExitStack() as stack:
            repo_binding = _pin_regular_file_overlay_directory(
                stack,
                repo_root,
                label="repository root",
            )
            _require_retired_targets_absent(stack, repo_binding)
            recovery_paths = _sync_sources_with_repo_binding(
                repo_root,
                source_root,
                rules,
                repo_binding,
                locked_sources,
            )
            _validate_canonical_review_target(repo_root)
            _validate_no_retired_review_references(repo_root)
            _assert_regular_file_overlay_directory_binding(
                repo_binding,
                label="repository root",
            )
        return recovery_paths
    plain_rules = tuple(
        rule
        for rule in rules
        if not rule.regular_file_overlays and rule.target != PRIVATE_BUG_TRIAGE_TARGET
    )
    secure_rules = tuple(
        rule
        for rule in rules
        if rule.regular_file_overlays or rule.target == PRIVATE_BUG_TRIAGE_TARGET
    )
    recovery_paths = _sync_sources_with_repo_binding(
        repo_root,
        source_root,
        plain_rules,
        None,
        None,
    )
    _remove_retired_targets(repo_root)
    if secure_rules:
        secure_targets = tuple(rule.target for rule in secure_rules)
        if CANONICAL_REVIEW_TARGET not in secure_targets:
            _validate_canonical_review_target(repo_root)
        _validate_no_retired_review_references(
            repo_root,
            excluded_targets=secure_targets,
        )
        with contextlib.ExitStack() as stack:
            repo_binding = _pin_regular_file_overlay_directory(
                stack,
                repo_root,
                label="repository root",
            )
            recovery_paths += _sync_sources_with_repo_binding(
                repo_root,
                source_root,
                secure_rules,
                repo_binding,
                None,
            )
    else:
        _validate_canonical_review_target(repo_root)
        _validate_no_retired_review_references(repo_root)
    return recovery_paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sync canonical Joey-Tools sources into the private overlay tree."
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--source-root", default=".source")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(os.path.abspath(args.repo_root))
    source_root = Path(os.path.abspath(args.source_root))
    source_lock_module = _load_source_lock_module()
    try:
        source_lock = source_lock_module.load_source_lock(repo_root)
        source_lock_module.validate_base_release_binding(repo_root, source_lock)
        initial_checkout_verification = _verify_complete_checkouts(
            source_lock_module,
            source_root,
            source_lock,
            repo_root=repo_root,
        )
        source_lock_module.validate_generated_provenance(
            repo_root,
            source_lock,
            toolbox_checkout=source_root / source_lock.pins[0].name,
            require_private_receipt=False,
        )
        pins_by_name = {pin.name: pin for pin in source_lock.pins}
        locked_sources: dict[tuple[str, Path], _LockedRuleSource] = {}
        for rule in SYNC_RULES:
            pin = pins_by_name.get(rule.repo)
            if pin is None:
                raise SyncError(f"source lock is missing sync repository: {rule.repo}")
            key = (rule.repo, rule.source)
            if key in locked_sources:
                raise SyncError(
                    f"duplicate locked sync source: {rule.repo}:{rule.source}"
                )
            checkout = source_root / rule.repo
            manifest = source_lock_module.load_locked_source_manifest(
                checkout,
                pin.sha,
                rule.source,
                exclude_names=tuple(
                    sorted(EXCLUDED_NAMES | frozenset(rule.exclude_names))
                ),
                exclude_suffixes=EXCLUDED_SUFFIXES,
            )
            source_pin = None
            migration_receipt = None
            if _is_authoritative_canonical_review_rule(rule):
                source_pin, migration_receipt = _bind_canonical_review_migration_source(
                    source_lock_module,
                    checkout,
                    pin,
                    manifest,
                    rule,
                    complete_checkout_verification=(initial_checkout_verification),
                )
            locked_sources[key] = _LockedRuleSource(
                checkout=checkout,
                manifest=manifest,
                read_blob=source_lock_module.read_locked_source_blob,
                source_pin=source_pin,
                canonical_review_migration_receipt=migration_receipt,
            )
        prewrite_checkout_verification = _verify_complete_checkouts(
            source_lock_module,
            source_root,
            source_lock,
            repo_root=repo_root,
        )
        _assert_matching_checkout_verification_scope(
            initial_checkout_verification,
            prewrite_checkout_verification,
        )
        locked_sources = {
            key: _LockedRuleSource(
                checkout=locked_source.checkout,
                manifest=locked_source.manifest,
                read_blob=locked_source.read_blob,
                source_pin=locked_source.source_pin,
                canonical_review_migration_receipt=(
                    locked_source.canonical_review_migration_receipt
                ),
                prewrite_checkout_verification=prewrite_checkout_verification,
            )
            for key, locked_source in locked_sources.items()
        }
        recovery_paths = sync_sources(
            repo_root,
            source_root,
            locked_sources=locked_sources,
        )
        _verify_complete_checkouts(
            source_lock_module,
            source_root,
            source_lock,
            repo_root=repo_root,
            validate_generated_provenance=True,
        )
    except (SyncError, source_lock_module.SourceLockError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for recovery_path in recovery_paths:
        try:
            relative = recovery_path.relative_to(repo_root)
        except ValueError:
            print(f"external prepared tree retained: {recovery_path}")
        else:
            print(f"regular-file overlay recovery: {relative}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
