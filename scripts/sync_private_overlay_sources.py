#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile


class SyncError(RuntimeError):
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


def _validate_no_retired_review_references(repo_root: Path) -> None:
    overlay_root = repo_root / "personal_codex"
    if not overlay_root.exists():
        return
    for path in sorted(overlay_root.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        for reference in RETIRED_REVIEW_REFERENCES:
            if reference in text:
                raise SyncError(
                    "private overlay retains retired review reference "
                    f"{reference!r} in {path.relative_to(repo_root)}"
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


def _assert_regular_file_overlay_root_binding(
    root_descriptor: int,
    root: Path,
    *,
    label: str,
) -> None:
    try:
        pinned = os.fstat(root_descriptor)
    except OSError as exc:
        raise SyncError(
            f"cannot inspect pinned regular-file overlay {label} root: {root}: {exc}"
        ) from exc
    try:
        visible_descriptor = _open_regular_file_overlay_root(root, label=label)
    except SyncError as exc:
        raise SyncError(
            f"regular-file overlay {label} root binding changed: {root}"
        ) from exc
    try:
        visible = os.fstat(visible_descriptor)
    except OSError as exc:
        raise SyncError(
            f"cannot inspect visible regular-file overlay {label} root: {root}: {exc}"
        ) from exc
    finally:
        os.close(visible_descriptor)
    if _overlay_root_identity(pinned) != _overlay_root_identity(visible):
        raise SyncError(f"regular-file overlay {label} root binding changed: {root}")


def _open_regular_file_overlay_parent(
    root_descriptor: int,
    relative: Path,
    *,
    label: str,
) -> tuple[int, str]:
    _require_overlay_relative_path(relative, field=label)
    flags = _regular_file_overlay_directory_flags(label=label)
    descriptor: int | None = None
    try:
        descriptor = os.dup(root_descriptor)
        for component in relative.parts[:-1]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            previous_descriptor = descriptor
            descriptor = next_descriptor
            os.close(previous_descriptor)
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise SyncError(
            "cannot securely open regular-file overlay "
            f"{label} parent: {relative}: {exc}"
        ) from exc
    return descriptor, relative.name


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


def _read_regular_file_overlay_source(repo_root: Path, relative: Path) -> bytes:
    source = repo_root / relative
    root_descriptor = _open_regular_file_overlay_root(repo_root, label="source")
    try:
        if source.is_symlink():
            raise SyncError(f"refusing regular-file overlay source symlink: {source}")
        if not source.exists():
            raise SyncError(f"regular-file overlay source missing: {source}")
        _ensure_safe_source(repo_root, source)
        _assert_regular_file_overlay_root_binding(
            root_descriptor,
            repo_root,
            label="source",
        )
        parent_descriptor, name = _open_regular_file_overlay_parent(
            root_descriptor,
            relative,
            label="source",
        )
    except BaseException:
        os.close(root_descriptor)
        raise

    try:
        initial = _stat_regular_file_overlay_entry(
            parent_descriptor,
            name,
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
            descriptor = os.open(name, flags, dir_fd=parent_descriptor)
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
            parent_descriptor,
            name,
            label="source",
            path=source,
        )
        if _overlay_file_content_identity(final) != identity_after:
            raise SyncError(
                f"regular-file overlay source changed after reading: {source}"
            )
        _assert_regular_file_overlay_root_binding(
            root_descriptor,
            repo_root,
            label="source",
        )
    finally:
        try:
            os.close(parent_descriptor)
        finally:
            os.close(root_descriptor)
    if len(data) > MAX_REGULAR_FILE_OVERLAY_BYTES:
        raise SyncError(
            "regular-file overlay source exceeds "
            f"{MAX_REGULAR_FILE_OVERLAY_BYTES} bytes: {source}"
        )
    return data


def _write_regular_file_overlay_target(
    staging: Path, relative: Path, data: bytes
) -> None:
    target = staging / relative
    root_descriptor = _open_regular_file_overlay_root(staging, label="target")
    try:
        _ensure_safe_target(staging, target)
        _assert_regular_file_overlay_root_binding(
            root_descriptor,
            staging,
            label="target",
        )
        parent_descriptor, name = _open_regular_file_overlay_parent(
            root_descriptor,
            relative,
            label="target",
        )
    except BaseException:
        os.close(root_descriptor)
        raise

    try:
        target_stat = _stat_regular_file_overlay_entry(
            parent_descriptor,
            name,
            label="target",
            path=relative,
        )
        flags = os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(name, flags, dir_fd=parent_descriptor)
        except OSError as exc:
            raise SyncError(
                f"cannot open regular-file overlay target: {relative}: {exc}"
            ) from exc
        try:
            opened = os.fstat(descriptor)
            _validate_overlay_regular_file(opened, label="target", path=relative)
            if _overlay_file_identity(opened) != _overlay_file_identity(target_stat):
                raise SyncError(
                    f"regular-file overlay target changed before writing: {relative}"
                )
            os.ftruncate(descriptor, 0)
            offset = 0
            while offset < len(data):
                written = os.write(descriptor, data[offset:])
                if written <= 0:
                    raise SyncError(
                        f"short write for regular-file overlay target: {relative}"
                    )
                offset += written
            os.lseek(descriptor, 0, os.SEEK_SET)
            chunks: list[bytes] = []
            remaining = len(data) + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 64 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            written_data = b"".join(chunks)
            after = os.fstat(descriptor)
        except OSError as exc:
            raise SyncError(
                f"cannot write regular-file overlay target: {relative}: {exc}"
            ) from exc
        finally:
            os.close(descriptor)

        final = _stat_regular_file_overlay_entry(
            parent_descriptor,
            name,
            label="target",
            path=relative,
        )
        _assert_regular_file_overlay_root_binding(
            root_descriptor,
            staging,
            label="target",
        )
    finally:
        try:
            os.close(parent_descriptor)
        finally:
            os.close(root_descriptor)
    if (
        written_data != data
        or _overlay_file_identity(after) != _overlay_file_identity(target_stat)
        or after.st_size != len(data)
        or _overlay_file_content_identity(final)
        != _overlay_file_content_identity(after)
    ):
        raise SyncError(
            f"regular-file overlay target byte verification failed: {relative}"
        )


def _apply_regular_file_overlays(
    repo_root: Path, staging: Path, rule: SyncRule
) -> None:
    for overlay in rule.regular_file_overlays:
        data = _read_regular_file_overlay_source(repo_root, overlay.source)
        _write_regular_file_overlay_target(staging, overlay.target, data)


def sync_sources(
    repo_root: Path, source_root: Path, rules: tuple[SyncRule, ...] = SYNC_RULES
) -> None:
    repo_root = repo_root.resolve()
    _validate_regular_file_overlay_targets(rules)
    for rule in rules:
        source_repo_root = source_root / rule.repo
        source = source_repo_root / rule.source
        target = repo_root / rule.target
        if not source.exists():
            raise SyncError(f"sync source missing for {rule.repo}: {source}")
        _ensure_safe_source(source_repo_root, source)
        _ensure_safe_target(repo_root, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f".{target.name}.staging.", dir=target.parent
        ) as temp_dir:
            staging = Path(temp_dir) / target.name
            _copy_source_to_staging(source, staging, exclude_names=rule.exclude_names)
            _apply_rule_replacements(staging, rule)
            _apply_regular_file_overlays(repo_root, staging, rule)
            _reject_forbidden_residuals(staging, rule)
            if rule.target == CANONICAL_REVIEW_TARGET:
                _validate_canonical_review_target_contents(staging)
            _replace_target(target, staging)
    _validate_canonical_review_target(repo_root)
    _remove_retired_targets(repo_root)
    _validate_no_retired_review_references(repo_root)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sync canonical Joey-Tools sources into the private overlay tree."
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--source-root", default=".source")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        sync_sources(Path(args.repo_root).resolve(), Path(args.source_root).resolve())
    except SyncError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
