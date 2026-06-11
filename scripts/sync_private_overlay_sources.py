#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import shutil
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
class SyncRule:
    repo: str
    source: Path
    target: Path
    replacements: tuple[Replacement, ...] = ()
    text_extensions: tuple[str, ...] = (".md", ".yaml", ".yml", ".py", ".toml", ".json")
    exclude_names: tuple[str, ...] = ()
    forbidden_residuals: tuple[str, ...] = ()


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
        (Replacement('DEFAULT_MANIFEST = Path("personal_codex/public-sync-manifest.json")',
                     'DEFAULT_MANIFEST = Path("personal_codex/private-sync-manifest.json")'),),
    ),
    _rule(
        "codex-review-workflows",
        "agents/reviewer.toml",
        "personal_codex/agents/reviewer.toml",
    ),
    _rule(
        "codex-review-workflows",
        "skills/copilot-review-playbook",
        "personal_codex/skills/copilot-review-playbook",
    ),
    _rule(
        "codex-review-workflows",
        "skills/agile-delivery-workflow",
        "personal_codex/skills/agile-delivery-workflow",
        (
            Replacement("user-visible", "Joey-visible", required=False),
        ),
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
            Replacement("--auth-profile default", "--auth-profile wme_jenkins_jobs_artifact"),
            Replacement(
                'DEFAULT_ALLOWED_HOSTS = frozenset({"engci-private-sjc.cisco.com"})\nAUTH_PROFILES = {\n    "default": (\n        "wme_jenkins_jobs_artifact_user",\n        "wme_jenkins_jobs_artifact_token",\n    ),\n}\n\n\ndef _allowed_hosts() -> frozenset[str]:\n    raw_hosts = os.getenv("JENKINS_ARTIFACT_ALLOWED_HOSTS")\n    if not raw_hosts:\n        return DEFAULT_ALLOWED_HOSTS\n    hosts = frozenset(host.strip() for host in raw_hosts.split(",") if host.strip())\n    return hosts or DEFAULT_ALLOWED_HOSTS',
                'ALLOWED_HOSTS = frozenset({"engci-private-sjc.cisco.com"})\nAUTH_PROFILES = {\n    "jenkins_mbpm2_codex": (\n        "Jenkins_mbpM2_codex_username",\n        "Jenkins_mbpM2_codex_token",\n    ),\n    "jenkins_webex_teams": (\n        "Jenkins_webex_teams_username",\n        "Jenkins_webex_teams_token",\n    ),\n    "wme_jenkins_jobs_artifact": (\n        "wme_jenkins_jobs_artifact_user",\n        "wme_jenkins_jobs_artifact_token",\n    ),\n}',
            ),
            Replacement("if parsed.hostname not in _allowed_hosts():", "if parsed.hostname not in ALLOWED_HOSTS:"),
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
        (Replacement("Run a local pre-commit delivery gate", "Run Joey's local pre-commit delivery gate"),),
        common_joey_text=True,
    ),
    _rule(
        "codex-workflow-hygiene",
        "skills/codex-rules-hygiene",
        "personal_codex/skills/codex-rules-hygiene",
        (
            Replacement("[$codex-skill-authoring](../codex-skill-authoring/SKILL.md)",
                        "[$joey-skill-authoring](../joey-skill-authoring/SKILL.md)"),
            Replacement("[$codex-skill-authoring](../../codex-skill-authoring/SKILL.md)",
                        "[$joey-skill-authoring](../../joey-skill-authoring/SKILL.md)"),
            Replacement("Repeated tracker issue metadata fetches before a dedicated tracker helper",
                        "Repeated Jira issue metadata fetches before `jira_issue_probe.py`"),
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
                "If the task might depend on work done on `miku-bot-dev` or `hoteng-srv-01`, use `$remote-host-context` before concluding the local machine is complete.\n"
                "- When remote-host coverage is needed, let `remote-host-context` own the remote access step. Use its helper to materialize remote rollout candidates locally, then continue the actual mining here.",
            ),
            Replacement(
                "If the task might depend on remote-host evidence",
                "If the task might depend on work done on `miku-bot-dev` or `hoteng-srv-01`",
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
                "If the user is asking for a work summary, activity audit, or session recovery that may include `miku-bot-dev` or `hoteng-srv-01`, use `$remote-host-context` before concluding that the local `~/.codex` tree is complete.",
            ),
            Replacement("remote hosts", "`miku-bot-dev` or `hoteng-srv-01`", required=False),
        ),
        common_joey_text=True,
    ),
    _rule(
        "codex-workflow-hygiene",
        "skills/codex-session-retrospective",
        "personal_codex/skills/codex-session-retrospective",
    ),
    _rule(
        "codex-review-workflows",
        "skills/external-review-playbook",
        "personal_codex/skills/external-review-playbook",
        common_joey_text=True,
    ),
    _rule(
        "codex-workflow-hygiene",
        "skills/codex-skill-authoring",
        "personal_codex/skills/joey-skill-authoring",
        (
            Replacement("codex-skill-authoring", "joey-skill-authoring"),
            Replacement("Codex Skill Authoring", "Joey Skill Authoring"),
            Replacement("Create concise concise Codex skills.", "Create concise Joey-style Codex skills."),
        ),
        common_joey_text=True,
    ),
    _rule(
        "codex-review-workflows",
        "skills/pr-readiness-review-workflow",
        "personal_codex/skills/pr-readiness-review-workflow",
        common_joey_text=True,
    ),
    _rule(
        "codex-project-journal",
        ".",
        "personal_codex/skills/project-journal",
        (
            Replacement("Manage repository project journals", "Manage Joey repo project journals"),
            Replacement("For repositories", "For Joey repos"),
            Replacement("repositories recently touched", "Joey repos recently touched"),
            Replacement("existing repositories", "existing Joey repos"),
            Replacement("cross-repo project journal indexes for Codex workflows", "cross-repo project journal indexes for Joey's Codex workflows"),
            Replacement("Do not batch-install hooks across repositories", "Do not batch-install hooks across Joey repos"),
        ),
        common_joey_text=True,
        exclude_names=("README.md",),
    ),
    _rule(
        "codex-review-workflows",
        "skills/review-orchestration-playbook",
        "personal_codex/skills/review-orchestration-playbook",
        common_joey_text=True,
    ),
    _rule(
        "codex-waited-delivery",
        "skills/waited-delivery",
        "personal_codex/skills/waited-delivery",
        common_joey_text=True,
    ),
)


EXCLUDED_NAMES = frozenset({".git", ".github", "__pycache__"})
EXCLUDED_SUFFIXES = (".pyc",)


def _is_text_candidate(path: Path, extensions: tuple[str, ...]) -> bool:
    return path.suffix in extensions or path.name in {"SKILL.md", "README.md"}


def _is_ignored_name(name: str, ignored_names: frozenset[str]) -> bool:
    return name in ignored_names or any(name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES)


def _is_ignored_relative(path: Path, root: Path, ignored_names: frozenset[str]) -> bool:
    return any(_is_ignored_name(part, ignored_names) for part in path.relative_to(root).parts)


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
        raise SyncError(f"sync source escapes source repository root: {source}") from exc

    if source_repo_root_raw.is_symlink():
        raise SyncError(f"refusing source repository root symlink: {source_repo_root_raw}")
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
        raise SyncError(f"sync source resolves outside source repository root: {source}") from exc


def _copy_source_to_staging(source: Path, staging: Path, *, exclude_names: tuple[str, ...] = ()) -> None:
    ignored_names = EXCLUDED_NAMES | frozenset(exclude_names)
    _reject_unignored_symlinks(source, ignored_names)
    if source.is_dir():
        shutil.copytree(
            source,
            staging,
            ignore=lambda _dir, names: [
                name
                for name in names
                if _is_ignored_name(name, ignored_names)
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
        backup = Path(tempfile.mkdtemp(prefix=f".{target.name}.backup.", dir=target.parent))
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


def _replacement_new_text_present(target: Path, rule: SyncRule, replacement: Replacement) -> bool:
    for path in _text_candidate_paths(target, rule):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if replacement.new in text:
            return True
    return False


def _text_candidate_paths(target: Path, rule: SyncRule) -> list[Path]:
    paths = [target] if target.is_file() else sorted(path for path in target.rglob("*") if path.is_file())
    return [path for path in paths if _is_text_candidate(path, rule.text_extensions)]


def _apply_rule_replacements(target: Path, rule: SyncRule) -> None:
    if not rule.replacements:
        return
    found: set[int] = set()
    for path in _text_candidate_paths(target, rule):
        found.update(_apply_replacements(path, rule.replacements))
    for index, replacement in enumerate(rule.replacements):
        if (
            replacement.required
            and index not in found
            and not _replacement_new_text_present(target, rule, replacement)
        ):
            raise SyncError(f"required replacement did not match for {rule.target}: {replacement.old!r}")


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


def sync_sources(repo_root: Path, source_root: Path, rules: tuple[SyncRule, ...] = SYNC_RULES) -> None:
    repo_root = repo_root.resolve()
    for rule in rules:
        source_repo_root = source_root / rule.repo
        source = source_repo_root / rule.source
        target = repo_root / rule.target
        if not source.exists():
            raise SyncError(f"sync source missing for {rule.repo}: {source}")
        _ensure_safe_source(source_repo_root, source)
        _ensure_safe_target(repo_root, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f".{target.name}.staging.", dir=target.parent) as temp_dir:
            staging = Path(temp_dir) / target.name
            _copy_source_to_staging(source, staging, exclude_names=rule.exclude_names)
            _apply_rule_replacements(staging, rule)
            _reject_forbidden_residuals(staging, rule)
            _replace_target(target, staging)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync canonical Joey-Tools sources into the private overlay tree.")
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
