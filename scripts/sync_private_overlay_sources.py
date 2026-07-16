#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
from collections.abc import Callable, Iterator
import ctypes
from dataclasses import dataclass, field
import errno
import os
from pathlib import Path
import secrets
import shutil
import stat
import sys
import tempfile


class SyncError(RuntimeError):
    pass


class _RegularFileOverlayBackupRetentionError(SyncError):
    pass


@dataclass(frozen=True)
class Replacement:
    old: str
    new: str
    required: bool = True


@dataclass(frozen=True)
class RegularFileOverlay:
    source: Path
    target: Path


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


def _rule(
    repo: str,
    source: str,
    target: str,
    replacements: tuple[Replacement, ...] = (),
    *,
    common_joey_text: bool = False,
    exclude_names: tuple[str, ...] = (),
    forbidden_residuals: tuple[str, ...] = (),
    regular_file_overlays: tuple[RegularFileOverlay, ...] = (),
) -> SyncRule:
    if common_joey_text:
        replacements = replacements + COMMON_JOEY_TEXT_REPLACEMENTS
    return SyncRule(
        repo=repo,
        source=_path(source),
        target=_path(target),
        replacements=replacements,
        exclude_names=exclude_names,
        forbidden_residuals=forbidden_residuals,
        regular_file_overlays=regular_file_overlays,
    )


SYNC_RULES = (
    _rule(
        "codex-toolbox",
        "scripts/codex_personal_sync.py",
        "scripts/codex_personal_sync.py",
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
                "tracker issue metadata or forge PR/commit metadata",
                "Cisco Jira issue metadata or Cisco GHE PR/commit metadata",
            ),
            Replacement(
                "fetch that tracker metadata first with a tracker-specific lookup skill",
                "fetch that tracker metadata first with [$cisco-trackers-lookup](../cisco-trackers-lookup/SKILL.md)",
            ),
            Replacement(
                "remote URL subcommands only allow `https://jenkins.example.com/...`",
                "remote URL subcommands only allow `https://engci-private-sjc.cisco.com/...`",
            ),
            Replacement(
                "`cisco-trackers-lookup` already covers that read-only tracker step.",
                "`cisco-trackers-lookup` already covers that read-only tracker step.",
                required=False,
            ),
            Replacement(
                "tracker metadata lookup into this skill when `cisco-trackers-lookup`",
                "Cisco Jira / Cisco GHE metadata lookup into this skill when `cisco-trackers-lookup`",
            ),
            Replacement("jenkins.example.com", "engci-private-sjc.cisco.com"),
            Replacement("JENKINS_ARTIFACT_USER", "wme_jenkins_jobs_artifact_user"),
            Replacement("JENKINS_ARTIFACT_TOKEN", "wme_jenkins_jobs_artifact_token"),
            Replacement(
                "--auth-profile default", "--auth-profile wme_jenkins_jobs_artifact"
            ),
            Replacement(
                'DEFAULT_ALLOWED_HOSTS = frozenset({"engci-private-sjc.cisco.com"})\nAUTH_PROFILES = {\n    "default": (\n        "wme_jenkins_jobs_artifact_user",\n        "wme_jenkins_jobs_artifact_token",\n    ),\n}\n\n\ndef _allowed_hosts() -> frozenset[str]:\n    raw_hosts = os.getenv("JENKINS_ARTIFACT_ALLOWED_HOSTS")\n    if not raw_hosts:\n        return DEFAULT_ALLOWED_HOSTS\n    hosts = frozenset(host.strip() for host in raw_hosts.split(",") if host.strip())\n    return hosts or DEFAULT_ALLOWED_HOSTS',
                'ALLOWED_HOSTS = frozenset({"engci-private-sjc.cisco.com"})\nAUTH_PROFILES = {\n    "jenkins_mbpm2_codex": (\n        "Jenkins_mbpM2_codex_username",\n        "Jenkins_mbpM2_codex_token",\n    ),\n    "jenkins_webex_teams": (\n        "Jenkins_webex_teams_username",\n        "Jenkins_webex_teams_token",\n    ),\n    "wme_jenkins_jobs_artifact": (\n        "wme_jenkins_jobs_artifact_user",\n        "wme_jenkins_jobs_artifact_token",\n    ),\n}',
            ),
            Replacement(
                "if parsed.hostname not in _allowed_hosts():",
                "if parsed.hostname not in ALLOWED_HOSTS:",
            ),
        ),
        common_joey_text=True,
        forbidden_residuals=(
            "jenkins.example.com",
            "JENKINS_ARTIFACT_USER",
            "JENKINS_ARTIFACT_TOKEN",
            "--auth-profile default",
            "DEFAULT_ALLOWED_HOSTS",
            "_allowed_hosts()",
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
        "skills/codex-rules-hygiene",
        "personal_codex/skills/codex-rules-hygiene",
        (
            Replacement(
                "[$codex-skill-authoring](../codex-skill-authoring/SKILL.md)",
                "[$joey-skill-authoring](../joey-skill-authoring/SKILL.md)",
            ),
            Replacement(
                "[$codex-skill-authoring](../../codex-skill-authoring/SKILL.md)",
                "[$joey-skill-authoring](../../joey-skill-authoring/SKILL.md)",
            ),
            Replacement(
                "Repeated tracker issue metadata fetches before a dedicated tracker helper",
                "Repeated Jira issue metadata fetches before `jira_issue_probe.py`",
            ),
            Replacement("Concrete tracker issue URLs", "Concrete Jira issue URLs"),
        ),
        common_joey_text=True,
        forbidden_residuals=(
            "environment-specific remote evidence workflow",
            "environment-specific workflow",
        ),
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
        "skills/codex-session-retrospective",
        "personal_codex/skills/codex-session-retrospective",
        (
            Replacement(
                "Default host scope follows `$remote-host-context`: local machine, `miku-bot-dev`, and `hoteng-srv-01`.",
                "Default host scope follows `$remote-host-context`: local machine, `BL-mac-mini-m4-hoteng`, `miku-bot-dev`, `hoteng-srv-01`, and `codex-hoteng-srv-01`.",
            ),
            Replacement(
                'DEFAULT_REMOTE_HOSTS = ("miku-bot-dev", "hoteng-srv-01")',
                'DEFAULT_REMOTE_HOSTS = ("BL-mac-mini-m4-hoteng", "miku-bot-dev", "hoteng-srv-01", "codex-hoteng-srv-01")',
            ),
            Replacement(
                'help="Source in HOST=PATH form. Defaults to local=~/.codex plus materialized miku-bot-dev and hoteng-srv-01 sources."',
                'help="Source in HOST=PATH form. Defaults to local=~/.codex plus materialized BL-mac-mini-m4-hoteng, miku-bot-dev, hoteng-srv-01, and codex-hoteng-srv-01 sources."',
            ),
            Replacement(
                "Retained host labels are restricted to `local`, the two default remote hosts, and `custom_source`",
                "Retained host labels are restricted to `local`, the four default remote hosts, and `custom_source`",
            ),
            Replacement(
                '    "local": {"kind": "local", "label": "local", "codex_root": "~/.codex"},\n'
                '    "miku-bot-dev": {',
                '    "local": {"kind": "local", "label": "local", "codex_root": "~/.codex"},\n'
                '    "BL-mac-mini-m4-hoteng": {\n'
                '        "kind": "ssh",\n'
                '        "label": "BL-mac-mini-m4-hoteng",\n'
                '        "ssh_target": "BL-mac-mini-m4-hoteng",\n'
                '        "codex_root": "/Users/hoteng/.codex",\n'
                "    },\n"
                '    "miku-bot-dev": {',
            ),
            Replacement(
                '    "hoteng-srv-01": {\n'
                '        "kind": "ssh",\n'
                '        "label": "hoteng-srv-01",\n'
                '        "ssh_target": "hoteng-srv-01",\n'
                '        "codex_root": "/home/hoteng/.codex",\n'
                "    },",
                '    "hoteng-srv-01": {\n'
                '        "kind": "ssh",\n'
                '        "label": "hoteng-srv-01",\n'
                '        "ssh_target": "hoteng-srv-01",\n'
                '        "codex_root": "/home/hoteng/.codex",\n'
                "    },\n"
                '    "codex-hoteng-srv-01": {\n'
                '        "kind": "ssh",\n'
                '        "label": "codex-hoteng-srv-01",\n'
                '        "ssh_target": "codex-hoteng-srv-01",\n'
                '        "codex_root": "/home/codex/.codex",\n'
                "    },",
            ),
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
                "Manage repository project journals",
                "Manage Joey repo project journals",
            ),
            Replacement("For repositories", "For Joey repos"),
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
        exclude_names=("README.md",),
    ),
    _rule(
        "codex-review-workflows",
        "skills/review-orchestration-playbook",
        "personal_codex/skills/review-orchestration-playbook",
        (
            Replacement(
                "REPO_ROOT = SKILL_ROOT.parents[1]",
                "OVERLAY_ROOT = SKILL_ROOT.parents[1]\nREPO_ROOT = OVERLAY_ROOT.parent",
            ),
            Replacement(
                "(REPO_ROOT / relative).exists()",
                "(OVERLAY_ROOT / relative).exists()",
            ),
            Replacement(
                'with (REPO_ROOT / "agents/reviewer.toml").open("rb") as handle:',
                'with (OVERLAY_ROOT / "agents/reviewer.toml").open("rb") as handle:',
            ),
        ),
        common_joey_text=True,
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
    _rule(
        "codex-waited-delivery",
        "skills/waited-delivery",
        "personal_codex/skills/waited-delivery",
        common_joey_text=True,
    ),
)


RETIRED_TARGETS = tuple(
    _path(path)
    for path in (
        "personal_codex/skills/copilot-review-playbook",
        "personal_codex/skills/external-review-playbook",
        "personal_codex/skills/pr-readiness-review-workflow",
    )
)

CANONICAL_REVIEW_TARGET = _path("personal_codex/skills/review-orchestration-playbook")
CANONICAL_REVIEW_REQUIRED_FILES = tuple(
    _path(path)
    for path in (
        "SKILL.md",
        "agents/openai.yaml",
        "references/cbth-agent-delivery.md",
        "references/helper-contract.md",
        "references/claude-runtime-trust.md",
        "references/egress-consent.md",
        "references/github-pr-probes.md",
        "references/pr-readiness.md",
        "references/review-lane-contracts.md",
        "references/review-prompt-templates.md",
        "references/synthetic-token-fixtures.md",
        "scripts/isolated_review",
        "scripts/review_runtime/__init__.py",
        "scripts/review_runtime/claude_capabilities.py",
        "scripts/review_runtime/claude_code_release.asc",
        "scripts/review_runtime/claude_keychain_broker.c",
        "scripts/review_runtime/claude_linux.py",
        "scripts/review_runtime/claude_linux_launcher.c",
        "scripts/review_runtime/claude_provenance.py",
        "scripts/review_runtime/cleanup_worker.py",
        "scripts/review_runtime/cli.py",
        "scripts/review_runtime/common.py",
        "scripts/review_runtime/prompt.py",
        "scripts/review_runtime/providers.py",
        "scripts/review_runtime/state.py",
        "scripts/review_runtime/synthetic-token-catalog.json",
        "scripts/review_runtime/synthetic_tokens.py",
        "scripts/review_runtime/workspace.py",
        "tests/test_claude_capabilities.py",
        "tests/test_claude_linux.py",
        "tests/test_claude_provenance.py",
        "tests/test_cli.py",
        "tests/test_common.py",
        "tests/test_contracts.py",
        "tests/test_providers.py",
        "tests/test_state.py",
        "tests/test_synthetic_tokens.py",
        "tests/test_workspace.py",
    )
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
        return
    if source.is_file():
        staging.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, staging)
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


def _validate_canonical_review_target_contents(target: Path) -> None:
    if not target.exists():
        return
    for relative in CANONICAL_REVIEW_REQUIRED_FILES:
        if not (target / relative).is_file():
            raise SyncError(
                f"canonical review target missing required file: {relative}"
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


def _validate_no_retired_review_references_in_staging(
    staging: Path,
    target: Path,
) -> None:
    for path in sorted(staging.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        for reference in RETIRED_REVIEW_REFERENCES:
            if reference in text:
                relative = target / path.relative_to(staging)
                raise SyncError(
                    "private overlay retains retired review reference "
                    f"{reference!r} in {relative}"
                )


def _apply_replacements(path: Path, replacements: tuple[Replacement, ...]) -> set[int]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return set()
    changed = False
    found: set[int] = set()
    for index, replacement in enumerate(replacements):
        if replacement.old not in text:
            continue
        text = text.replace(replacement.old, replacement.new)
        changed = True
        found.add(index)
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


def _apply_rule_replacements(target: Path, rule: SyncRule) -> None:
    if not rule.replacements:
        return
    found: set[int] = set()
    for path in _text_candidate_paths(target, rule):
        found.update(_apply_replacements(path, rule.replacements))
    for index, replacement in enumerate(rule.replacements):
        if replacement.required and index not in found:
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


def _overlay_file_content_identity(
    metadata: os.stat_result,
) -> tuple[int, int, int, int, int, int, int, int]:
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
class _PinnedRegularFileOverlayTarget:
    chain: _PinnedRegularFileOverlayDirectoryChain
    file_descriptor: int
    expected_data: bytes
    expected_identity: tuple[int, int, int, int, int, int, int, int]


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
            chunks: list[bytes] = []
            remaining = MAX_REGULAR_FILE_OVERLAY_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 64 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            after = os.fstat(descriptor)
        except OSError as exc:
            raise SyncError(
                f"cannot read regular-file overlay source: {source}: {exc}"
            ) from exc
        finally:
            os.close(descriptor)

        identity_before = _overlay_file_content_identity(before)
        identity_after = _overlay_file_content_identity(after)
        if identity_before != identity_after or len(data) != after.st_size:
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
) -> tuple[_PinnedRegularFileOverlayTarget, ...]:
    bindings: list[_PinnedRegularFileOverlayTarget] = []
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
            )
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
        try:
            actual_names = set(os.listdir(scope.container.descriptor))
        except OSError as exc:
            raise SyncError(
                f"cannot inspect regular-file overlay recovery scope: {exc}"
            ) from exc
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
            # rebound; retain the verified prior target for manual recovery.
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
) -> Path | None:
    if not bindings:
        raise SyncError("secure regular-file overlay install requires a binding")
    primitive = _load_regular_file_overlay_noreplace_primitive()
    expected_root_identity = bindings[0].chain.identities[0]
    if any(
        binding.chain.root != staging
        or binding.chain.identities[0] != expected_root_identity
        for binding in bindings
    ):
        raise SyncError("regular-file overlay staging bindings disagree")
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
            try:
                actual_staging_entries = sorted(os.listdir(staging_parent.descriptor))
            except OSError as exc:
                raise SyncError(
                    f"cannot inspect regular-file overlay staging after install: {exc}"
                ) from exc
            if actual_staging_entries != expected_staging_entries:
                raise SyncError(
                    "regular-file overlay staging gained an unknown entry after install"
                )
            if backup_name is not None and backup is not None:
                _assert_regular_file_overlay_entry_binding(
                    staging_parent.descriptor,
                    backup,
                    label="verified recovery backup",
                    name=backup_name,
                )
            _assert_regular_file_overlay_directory_binding(
                target_parent,
                label="target parent",
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
                candidate_detail = f"installed candidate left live at {target}"
            elif staging_is_candidate and not target_is_candidate:
                candidate_detail = (
                    f"candidate retained in recovery scope {staging_scope.path}"
                )
            else:
                candidate_detail = (
                    "candidate binding is ambiguous between live target and "
                    f"recovery scope {staging_scope.path}"
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
                        "regular-file overlay transaction failed; "
                        f"{candidate_detail}; prior target binding is unknown; "
                        f"inspect {staging_scope.path}: {recovery_error}"
                    ) from transaction_error
                if retained is None:
                    prior_detail = f"prior target remains live at {target}"
                else:
                    prior_detail = f"prior target retained at {retained}"
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
                    live_detail = "live target is the pinned candidate"
                elif retained is None and backup is not None:
                    live_detail = "live target is the pinned prior target"
                elif target_exists:
                    live_detail = f"untrusted live target remains at {target}"
                else:
                    live_detail = "live target is absent"

            raise SyncError(
                "regular-file overlay transaction failed; "
                f"{candidate_detail}; {prior_detail}; {live_detail}"
            ) from transaction_error
        staging_scope.completed = True
        return staging_scope.recovery_path


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


def _copy_prepared_regular_file_overlay_file(
    source: Path,
    destination_parent: _PinnedRegularFileOverlayDirectory,
    destination_name: str,
    *,
    staging_scope: _RegularFileOverlayStagingScope,
) -> None:
    try:
        source_before = source.lstat()
    except OSError as exc:
        raise SyncError(
            f"cannot inspect prepared overlay source: {source}: {exc}"
        ) from exc
    if not stat.S_ISREG(source_before.st_mode):
        raise SyncError(f"prepared overlay source is not a regular file: {source}")
    source_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    destination_flags = (
        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        source_descriptor = os.open(source, source_flags)
    except OSError as exc:
        raise SyncError(
            f"cannot open prepared overlay source: {source}: {exc}"
        ) from exc
    try:
        opened_source = os.fstat(source_descriptor)
        if _overlay_file_content_identity(
            opened_source
        ) != _overlay_file_content_identity(source_before):
            raise SyncError(f"prepared overlay source changed while opening: {source}")
        _assert_regular_file_overlay_scope_binding(
            staging_scope,
            operation="prepared file creation",
        )
        _assert_regular_file_overlay_directory_binding(
            destination_parent,
            label="prepared file parent",
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
            while True:
                chunk = os.read(source_descriptor, 1024 * 1024)
                if not chunk:
                    break
                _write_regular_file_overlay_descriptor(destination_descriptor, chunk)
            os.fchmod(destination_descriptor, stat.S_IMODE(opened_source.st_mode))
            copied = os.fstat(destination_descriptor)
        finally:
            os.close(destination_descriptor)
        source_after = os.fstat(source_descriptor)
        if (
            _overlay_file_content_identity(source_after)
            != _overlay_file_content_identity(opened_source)
            or copied.st_size != opened_source.st_size
        ):
            raise SyncError(f"prepared overlay source changed while copying: {source}")
        _assert_regular_file_overlay_scope_binding(
            staging_scope,
            operation="prepared file validation",
        )
    finally:
        os.close(source_descriptor)


def _create_prepared_regular_file_overlay_value(
    data: bytes,
    destination_parent: _PinnedRegularFileOverlayDirectory,
    destination_name: str,
    *,
    staging_scope: _RegularFileOverlayStagingScope,
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
    finally:
        os.close(descriptor)
    _assert_regular_file_overlay_scope_binding(
        staging_scope,
        operation="private overlay target validation",
    )


def _copy_prepared_regular_file_overlay_directory(
    stack: contextlib.ExitStack,
    source: Path,
    destination: _PinnedRegularFileOverlayDirectory,
    *,
    staging_scope: _RegularFileOverlayStagingScope,
    ignored_names: frozenset[str],
    relative: Path,
    overlay_data: dict[Path, bytes],
    applied_overlays: set[Path],
) -> None:
    for child in sorted(source.iterdir(), key=lambda path: path.name):
        if _is_ignored_name(child.name, ignored_names):
            continue
        child_relative = relative / child.name
        try:
            metadata = child.lstat()
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
            _assert_regular_file_overlay_scope_binding(
                staging_scope,
                operation="prepared directory creation",
            )
            _assert_regular_file_overlay_directory_binding(
                destination,
                label="prepared directory parent",
            )
            try:
                os.mkdir(child.name, 0o700, dir_fd=destination.descriptor)
            except OSError as exc:
                raise SyncError(
                    "cannot create prepared regular-file overlay directory: "
                    f"{destination.path / child.name}: {exc}"
                ) from exc
            pinned_child = _pin_regular_file_overlay_child_directory(
                stack,
                destination,
                child.name,
                path=destination.path / child.name,
                label="prepared directory",
            )
            _copy_prepared_regular_file_overlay_directory(
                stack,
                child,
                pinned_child,
                staging_scope=staging_scope,
                ignored_names=ignored_names,
                relative=child_relative,
                overlay_data=overlay_data,
                applied_overlays=applied_overlays,
            )
            _assert_regular_file_overlay_scope_binding(
                staging_scope,
                operation="prepared directory mode update",
            )
            os.fchmod(pinned_child.descriptor, stat.S_IMODE(metadata.st_mode))
            continue
        if stat.S_ISREG(metadata.st_mode):
            if child_relative in overlay_data:
                _create_prepared_regular_file_overlay_value(
                    overlay_data[child_relative],
                    destination,
                    child.name,
                    staging_scope=staging_scope,
                )
                applied_overlays.add(child_relative)
            else:
                _copy_prepared_regular_file_overlay_file(
                    child,
                    destination,
                    child.name,
                    staging_scope=staging_scope,
                )
            continue
        raise SyncError(f"unsupported prepared overlay source type: {child}")


def _copy_prepared_regular_file_overlay_staging(
    stack: contextlib.ExitStack,
    source: Path,
    staging: Path,
    *,
    staging_scope: _RegularFileOverlayStagingScope,
    exclude_names: tuple[str, ...],
    overlay_data: dict[Path, bytes],
) -> _PinnedRegularFileOverlayDirectory:
    try:
        source_metadata = source.lstat()
    except OSError as exc:
        raise SyncError(
            f"cannot inspect prepared overlay source: {source}: {exc}"
        ) from exc
    if not stat.S_ISDIR(source_metadata.st_mode):
        raise SyncError("regular-file overlay sync requires a directory source")
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
    _copy_prepared_regular_file_overlay_directory(
        stack,
        source,
        staging_root,
        staging_scope=staging_scope,
        ignored_names=EXCLUDED_NAMES | frozenset(exclude_names),
        relative=Path(),
        overlay_data=overlay_data,
        applied_overlays=applied_overlays,
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
    return _PinnedRegularFileOverlayDirectory(
        path=staging_root.path,
        descriptor=staging_root.descriptor,
        identity=_regular_file_overlay_directory_identity(
            staging_root.descriptor,
            label="staged target",
            path=staging_root.path,
        ),
    )


@contextlib.contextmanager
def _regular_file_overlay_staging_directory(
    repo_binding: _PinnedRegularFileOverlayDirectory,
    target_relative: Path,
) -> Iterator[_RegularFileOverlayStagingScope]:
    if os.mkdir not in os.supports_dir_fd or os.listdir not in os.supports_fd:
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
        try:
            existing_recoveries = os.listdir(recovery_root.descriptor)
        except OSError as exc:
            raise SyncError(
                f"cannot inspect regular-file overlay recovery root: {exc}"
            ) from exc
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
        try:
            os.mkdir(container_name, 0o700, dir_fd=recovery_root.descriptor)
        except OSError as exc:
            raise SyncError(
                f"cannot create regular-file overlay staging container: {exc}"
            ) from exc
        container_path = recovery_root.path / container_name
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
        yield scope
        if not scope.completed:
            raise SyncError(
                f"regular-file overlay staging retained for inspection: {container_path}"
            )


def _sync_sources_with_repo_binding(
    repo_root: Path,
    source_root: Path,
    rules: tuple[SyncRule, ...],
    repo_binding: _PinnedRegularFileOverlayDirectory | None,
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
        if not source.exists():
            raise SyncError(f"sync source missing for {rule.repo}: {source}")
        _ensure_safe_source(source_repo_root, source)
        _ensure_safe_target(repo_root, target)
        if rule.regular_file_overlays:
            if repo_binding is None:
                raise SyncError("secure sync requires a pinned repository root")
            prepared_directory = Path(
                tempfile.mkdtemp(prefix=f".{target.name}.prepared.")
            )
            prepared_cleanup_pending = True
            prepared_recovery_path: Path | None = None
            try:
                prepared = prepared_directory / target.name
                _copy_source_to_staging(
                    source,
                    prepared,
                    exclude_names=rule.exclude_names,
                )
                _apply_rule_replacements(prepared, rule)
                _reject_forbidden_residuals(prepared, rule)
                if rule.target == CANONICAL_REVIEW_TARGET:
                    _validate_canonical_review_target_contents(prepared)
                overlay_data = _load_regular_file_overlay_data(
                    repo_root,
                    rule,
                    repo_binding=repo_binding,
                )
                with _regular_file_overlay_staging_directory(
                    repo_binding,
                    rule.target,
                ) as staging_scope:
                    prepared_recovery_path = staging_scope.path
                    staging = staging_scope.path / target.name
                    with contextlib.ExitStack() as binding_stack:
                        staging_root = _copy_prepared_regular_file_overlay_staging(
                            binding_stack,
                            prepared,
                            staging,
                            staging_scope=staging_scope,
                            exclude_names=rule.exclude_names,
                            overlay_data=overlay_data,
                        )
                        if rule.target == CANONICAL_REVIEW_TARGET:
                            _validate_canonical_review_target_contents(staging)
                        else:
                            _validate_no_retired_review_references_in_staging(
                                staging,
                                rule.target,
                            )
                        bindings = _pin_regular_file_overlay_targets(
                            binding_stack,
                            staging,
                            staging_root,
                            overlay_data,
                        )
                        # Remove the external public candidate before the first
                        # live no-replace mutation. No fallible prepared-tree
                        # cleanup remains after the secure commit boundary.
                        try:
                            shutil.rmtree(prepared_directory)
                        except OSError as exc:
                            recovery_detail = (
                                "; candidate retained in recovery scope "
                                f"{prepared_recovery_path}"
                                if prepared_recovery_path is not None
                                else ""
                            )
                            raise SyncError(
                                "cannot remove external prepared tree before "
                                f"secure commit{recovery_detail}: {exc}"
                            ) from exc
                        prepared_cleanup_pending = False
                        recovery_path = _replace_target_with_regular_file_overlays(
                            target,
                            staging,
                            bindings,
                            staging_scope=staging_scope,
                        )
                if recovery_path is not None:
                    recovery_paths.append(recovery_path)
            except BaseException as primary_error:
                if prepared_cleanup_pending:
                    try:
                        shutil.rmtree(prepared_directory)
                    except OSError as cleanup_error:
                        detail = (
                            "external prepared-tree cleanup also failed at "
                            f"{prepared_directory}: {cleanup_error}"
                        )
                        if isinstance(primary_error, SyncError):
                            raise SyncError(
                                f"{primary_error}; {detail}"
                            ) from primary_error
                        if hasattr(primary_error, "add_note"):
                            primary_error.add_note(detail)
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
            if rule.target == CANONICAL_REVIEW_TARGET:
                _validate_canonical_review_target_contents(staging)
            _replace_target(target, staging)
    return tuple(recovery_paths)


def sync_sources(
    repo_root: Path, source_root: Path, rules: tuple[SyncRule, ...] = SYNC_RULES
) -> tuple[Path, ...]:
    repo_root = repo_root.resolve()
    _validate_regular_file_overlay_targets(rules)
    secure_rule_count = sum(bool(rule.regular_file_overlays) for rule in rules)
    if secure_rule_count > 1:
        raise SyncError("private overlay sync permits exactly one secure rule")
    plain_rules = tuple(rule for rule in rules if not rule.regular_file_overlays)
    secure_rules = tuple(rule for rule in rules if rule.regular_file_overlays)
    recovery_paths = _sync_sources_with_repo_binding(
        repo_root,
        source_root,
        plain_rules,
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
    repo_root = Path(args.repo_root).resolve()
    try:
        recovery_paths = sync_sources(
            repo_root,
            Path(args.source_root).resolve(),
        )
    except SyncError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for recovery_path in recovery_paths:
        print(f"regular-file overlay recovery: {recovery_path.relative_to(repo_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
