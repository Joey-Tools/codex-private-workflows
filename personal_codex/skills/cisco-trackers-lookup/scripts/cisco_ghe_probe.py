#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

GH_HOST = "sqbu-github.cisco.com"
MAX_LIMIT = 20
GH_COMMAND_TIMEOUT_SECONDS = 60
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
QUERY_SCOPE_OVERRIDE_RE = re.compile(r"(?i)(?:^|[\s(])(?:repo|org|user):\S")


def _error(message: str) -> int:
    print(f"error={message}", file=sys.stderr)
    return 2


def _validate_repo(value: str) -> str:
    if not REPO_RE.fullmatch(value):
        raise ValueError("repo must match ORG/REPO")
    segments = value.split("/", 1)
    if any(segment in {".", ".."} for segment in segments):
        raise ValueError("repo must not contain dot-only path segments")
    return value


def _validate_limit(value: int) -> int:
    if value < 1 or value > MAX_LIMIT:
        raise ValueError(f"--limit must stay between 1 and {MAX_LIMIT}")
    return value


def _validate_commit(value: str) -> str:
    if not COMMIT_RE.fullmatch(value):
        raise ValueError("commit must be a 7-64 hex SHA")
    return value.lower()


def _normalize_pull_item(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {
            "number": "",
            "title": "",
            "url": "",
            "state": "",
            "author": "",
            "createdAt": "",
            "updatedAt": "",
            "mergedAt": "",
            "baseRefName": "",
            "headRefName": "",
        }
    user = value.get("user")
    base = value.get("base")
    head = value.get("head")
    merged_at = value.get("merged_at", value.get("mergedAt", ""))
    return {
        "number": value.get("number", ""),
        "title": str(value.get("title", "")),
        "url": str(value.get("html_url", value.get("url", ""))),
        "state": str(value.get("state", "")),
        "author": str(user.get("login", "")) if isinstance(user, dict) else "",
        "createdAt": str(value.get("created_at", value.get("createdAt", ""))),
        "updatedAt": str(value.get("updated_at", value.get("updatedAt", ""))),
        "mergedAt": "" if merged_at is None else str(merged_at),
        "baseRefName": str(base.get("ref", "")) if isinstance(base, dict) else str(value.get("baseRefName", "")),
        "headRefName": str(head.get("ref", "")) if isinstance(head, dict) else str(value.get("headRefName", "")),
    }


def _run_gh_json(argv: list[str]) -> tuple[int, object]:
    env = os.environ.copy()
    env["GH_HOST"] = GH_HOST
    try:
        result = subprocess.run(
            argv,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=GH_COMMAND_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        print("error=gh command not found", file=sys.stderr)
        return 1, None
    except subprocess.TimeoutExpired:
        print(
            f"error=gh command timed out after {GH_COMMAND_TIMEOUT_SECONDS}s",
            file=sys.stderr,
        )
        return 1, None
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "gh command failed"
        print(f"error={message}", file=sys.stderr)
        return 1, None
    try:
        return 0, json.loads(result.stdout)
    except json.JSONDecodeError as error:
        print(f"error=invalid gh JSON output: {error}", file=sys.stderr)
        return 1, None


def cmd_pr_view(args: argparse.Namespace) -> int:
    try:
        repo = _validate_repo(args.repo)
        pr = int(args.pr)
        if pr < 1:
            raise ValueError("--pr must be positive")
    except (ValueError, TypeError) as error:
        return _error(str(error))

    rc, payload = _run_gh_json(
        [
            "gh",
            "pr",
            "view",
            str(pr),
            "--repo",
            repo,
            "--json",
            "number,title,url,state,author,createdAt,updatedAt,mergedAt,baseRefName,headRefName,labels",
        ]
    )
    if rc != 0:
        return rc
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_search_prs(args: argparse.Namespace) -> int:
    try:
        repo = _validate_repo(args.repo)
        limit = _validate_limit(args.limit)
        query = args.query.strip()
        if not query:
            raise ValueError("--query must not be empty")
        if "\n" in query:
            raise ValueError("--query must stay on one line")
        if QUERY_SCOPE_OVERRIDE_RE.search(query):
            raise ValueError("--query must not override repo scope with repo:, org:, or user:")
    except ValueError as error:
        return _error(str(error))

    rc, payload = _run_gh_json(
        [
            "gh",
            "search",
            "prs",
            "--repo",
            repo,
            "--limit",
            str(limit),
            "--json",
            "number,title,url,state,author,createdAt,updatedAt",
            "--",
            query,
        ]
    )
    if rc != 0:
        return rc
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_commit_pulls(args: argparse.Namespace) -> int:
    try:
        repo = _validate_repo(args.repo)
        commit = _validate_commit(args.commit)
    except ValueError as error:
        return _error(str(error))

    rc, payload = _run_gh_json(
        [
            "gh",
            "api",
            f"repos/{repo}/commits/{commit}/pulls",
        ]
    )
    if rc != 0:
        return rc
    if not isinstance(payload, list):
        print("error=unexpected gh api payload for commit-pulls", file=sys.stderr)
        return 1
    print(json.dumps([_normalize_pull_item(item) for item in payload], indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read narrow Cisco GHE metadata without shell-wrapped GH_HOST literals."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    pr_view = subparsers.add_parser("pr-view", help="Read one PR from Cisco GHE.")
    pr_view.add_argument("--repo", required=True)
    pr_view.add_argument("--pr", required=True)
    pr_view.set_defaults(func=cmd_pr_view)

    search_prs = subparsers.add_parser(
        "search-prs",
        help="Search PRs inside one repo on Cisco GHE.",
    )
    search_prs.add_argument("--repo", required=True)
    search_prs.add_argument("--query", required=True)
    search_prs.add_argument("--limit", type=int, default=10)
    search_prs.set_defaults(func=cmd_search_prs)

    commit_pulls = subparsers.add_parser(
        "commit-pulls",
        help="Map one commit SHA to its PRs on Cisco GHE.",
    )
    commit_pulls.add_argument("--repo", required=True)
    commit_pulls.add_argument("--commit", required=True)
    commit_pulls.set_defaults(func=cmd_commit_pulls)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
