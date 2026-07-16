#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
from collections.abc import Callable, Iterator
import ctypes
from dataclasses import dataclass, field
import errno
import hashlib
import os
from pathlib import Path
import secrets
import shutil
import stat
import sys
import tempfile


class SyncError(RuntimeError):
    pass


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
            if _is_ignored_name(name, ignored_names):
                continue
            if len(entries) >= MAX_REGULAR_FILE_OVERLAY_TREE_ENTRIES:
                raise SyncError(
                    f"regular-file overlay {label} tree exceeds "
                    f"{MAX_REGULAR_FILE_OVERLAY_TREE_ENTRIES} entries"
                )
            child_parts = (*relative_parts, name)
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
) -> Path | None:
    if not bindings:
        raise SyncError("secure regular-file overlay install requires a binding")
    primitive = _load_regular_file_overlay_noreplace_primitive()
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
                raise SyncError(
                    f"{type(exc).__name__}: {exc}; {detail}"
                ) from exc
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
        if (
            _overlay_file_identity(os.fstat(container.descriptor))
            != _overlay_file_identity(created)
        ):
            raise SyncError(
                "external prepared container changed after descriptor-relative "
                "creation"
            )
    except BaseException as exc:
        detail = f"external prepared tree retained at {container_path}"
        if isinstance(exc, SyncError):
            raise SyncError(f"{exc}; {detail}") from exc
        if isinstance(exc, Exception):
            raise SyncError(
                f"{type(exc).__name__}: {exc}; {detail}"
            ) from exc
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
        raise SyncError(
            f"regular-file overlay {label} entry type changed: {relative}"
        )
    return entry


def _apply_regular_file_overlay_rule_to_bytes(
    data: bytes,
    relative: Path,
    rule: SyncRule,
    found_replacements: set[int],
) -> bytes:
    if not _is_text_candidate(relative, rule.text_extensions):
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    for index, replacement in enumerate(rule.replacements):
        if replacement.old not in text:
            continue
        text = text.replace(replacement.old, replacement.new)
        found_replacements.add(index)
    for residual in rule.forbidden_residuals:
        if residual in text:
            raise SyncError(
                f"forbidden residual {residual!r} remains in {relative}"
            )
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
    files = {
        entry.relative_parts
        for entry in manifest.entries
        if entry.kind == "file"
    }
    for relative in CANONICAL_REVIEW_REQUIRED_FILES:
        if relative.parts not in files:
            raise SyncError(
                "canonical review target missing required file at "
                f"{surface}: {relative}"
            )


def _copy_regular_file_overlay_public_source_to_prepared(
    source: Path,
    prepared: Path,
    *,
    prepared_root: _PinnedRegularFileOverlayDirectory,
    rule: SyncRule,
) -> _RegularFileOverlayTreeManifest:
    ignored_names = EXCLUDED_NAMES | frozenset(rule.exclude_names)
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
        )
        if _overlay_file_identity(source_root_metadata) != source_manifest.root_identity:
            raise SyncError("regular-file overlay public source root changed")
        expected_entries = _regular_file_overlay_manifest_index(
            source_manifest,
            label="initial public source",
        )
        visited_entries: set[tuple[str, ...]] = set()
        found_replacements: set[int] = set()
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
                if _is_ignored_name(name, ignored_names):
                    continue
                child_relative = relative / name
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
                        or destination_digest
                        != hashlib.sha256(output_data).hexdigest()
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
        for index, replacement in enumerate(rule.replacements):
            if replacement.required and index not in found_replacements:
                raise SyncError(
                    "required replacement did not match for "
                    f"{rule.target}: {replacement.old!r}"
                )
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
            or opened_content_identity
            != _overlay_file_content_identity(source_before)
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
            or _overlay_file_content_identity(source_after)
            != opened_content_identity
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
    source_data, source_metadata = (
        _read_expected_prepared_regular_file_overlay_file(
            source_parent,
            source_name,
            relative=relative,
            expected=expected,
        )
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
        maximum=(
            MAX_REGULAR_FILE_OVERLAY_TREE_ENTRIES - copy_budget.scanned_entries
        ),
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
    if _overlay_file_identity(source_metadata) != expected_source_manifest.root_identity:
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
    return f"regular-file overlay recovery scope retained for inspection at {scope.path}"


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
                    prepared_source_manifest = (
                        _copy_regular_file_overlay_public_source_to_prepared(
                            source,
                            prepared,
                            prepared_root=prepared_root,
                            rule=rule,
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
                            recovery_path = (
                                _replace_target_with_regular_file_overlays(
                                    target,
                                    staging,
                                    bindings,
                                    staging_scope=staging_scope,
                                )
                            )
                    if recovery_path is not None:
                        recovery_paths.append(recovery_path)
                    recovery_paths.append(prepared_directory)
                except BaseException as primary_error:
                    detail = (
                        f"external prepared tree retained at {prepared_directory}"
                    )
                    if isinstance(primary_error, SyncError):
                        if detail not in str(primary_error):
                            raise SyncError(
                                f"{primary_error}; {detail}"
                            ) from primary_error
                    elif isinstance(primary_error, Exception):
                        raise SyncError(
                            f"{type(primary_error).__name__}: {primary_error}; "
                            f"{detail}"
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
            if rule.target == CANONICAL_REVIEW_TARGET:
                _validate_canonical_review_target_contents(staging)
            _replace_target(target, staging)
    return tuple(recovery_paths)


def sync_sources(
    repo_root: Path, source_root: Path, rules: tuple[SyncRule, ...] = SYNC_RULES
) -> tuple[Path, ...]:
    repo_root = repo_root.resolve()
    source_root = source_root.resolve()
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
        try:
            relative = recovery_path.relative_to(repo_root)
        except ValueError:
            print(f"external prepared tree retained: {recovery_path}")
        else:
            print(f"regular-file overlay recovery: {relative}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
