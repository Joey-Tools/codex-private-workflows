#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

JIRA_HOST = "jira-eng-gpk2.cisco.com"
JIRA_BASE_URL = f"https://{JIRA_HOST}/jira"
ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")
AUTH_PROFILES = {
    "jira_eng_gpk2_default": ("Jira_email", "Jira_token"),
}
ISSUE_FIELDS = (
    "summary,description,status,priority,labels,created,updated,"
    "reporter,assignee,issuetype,issuelinks"
)
ISSUE_EXTRA_FIELDS = ISSUE_FIELDS + ",comment,attachment"
MAX_JIRA_RESPONSE_BYTES = 16 * 1024 * 1024


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def http_error_301(self, req, fp, code, msg, headers):
        raise urllib.error.HTTPError(req.full_url, code, "redirects are not allowed", headers, fp)

    http_error_302 = http_error_301
    http_error_303 = http_error_301
    http_error_307 = http_error_301
    http_error_308 = http_error_301


def _join_non_empty(parts: list[str], separator: str) -> str:
    return separator.join(part for part in parts if part)


def _usage_error(issue_ref: str, error: ValueError) -> int:
    print(f"issue={_safe_issue_ref(issue_ref)}", file=sys.stderr)
    print(f"error={error}", file=sys.stderr)
    return 2


def _safe_issue_ref(issue_ref: str) -> str:
    raw_ref = issue_ref.strip()
    parsed = urllib.parse.urlparse(raw_ref)
    if parsed.username or parsed.password:
        hostname = parsed.hostname or ""
        try:
            port = parsed.port
        except ValueError:
            port = None
        netloc = f"{hostname}:{port}" if port is not None else hostname
        return urllib.parse.urlunparse(parsed._replace(netloc=netloc))
    return raw_ref


def _normalize_issue_ref(issue_ref: str) -> str:
    raw_ref = issue_ref.strip()
    candidate = raw_ref.upper()
    if ISSUE_KEY_RE.fullmatch(candidate):
        return candidate

    parsed = urllib.parse.urlparse(raw_ref)
    if parsed.scheme != "https":
        raise ValueError("only https Jira URLs are allowed")
    if parsed.hostname != JIRA_HOST:
        raise ValueError(f"host not allowed: {parsed.hostname or ''}")
    if parsed.username or parsed.password:
        raise ValueError("inline URL credentials are not allowed")
    match = re.search(r"/browse/([A-Z][A-Z0-9]+-\d+)/?$", parsed.path, re.IGNORECASE)
    if not match:
        raise ValueError("issue ref must be an issue key or a /browse/KEY URL")
    return match.group(1).upper()


def _add_basic_auth(
    request: urllib.request.Request,
    auth_profile: str,
) -> str:
    try:
        user_env, token_env = AUTH_PROFILES[auth_profile]
    except KeyError as exc:
        raise ValueError(f"unknown auth profile: {auth_profile}") from exc
    user = os.getenv(user_env)
    token = os.getenv(token_env)
    if not user or not token:
        raise ValueError(
            f"missing auth env for profile {auth_profile}: expected {user_env} and {token_env}"
        )
    raw = f"{user}:{token}".encode("utf-8")
    header = base64.b64encode(raw).decode("ascii")
    request.add_header("Authorization", f"Basic {header}")
    return "present"


def _build_issue_request(issue_key: str, fields: str, auth_profile: str) -> tuple[urllib.request.Request, str]:
    url = f"{JIRA_BASE_URL}/rest/api/2/issue/{issue_key}?fields={urllib.parse.quote(fields, safe=',')}"
    request = urllib.request.Request(url, method="GET")
    request.add_header("Accept", "application/json")
    auth_state = _add_basic_auth(request, auth_profile)
    return request, auth_state


def _field_name(value: object) -> str:
    if isinstance(value, dict):
        name = value.get("displayName")
        if isinstance(name, str):
            return name
        name = value.get("name")
        if isinstance(name, str):
            return name
    return ""


def _field_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return _join_non_empty([_field_text(item) for item in value], "\n")
    if isinstance(value, dict):
        for key in ("name", "value", "description"):
            text = value.get(key)
            if isinstance(text, str):
                return text
        attrs = value.get("attrs")
        if isinstance(attrs, dict):
            for key in ("text", "shortName", "url", "title", "name"):
                text = attrs.get(key)
                if isinstance(text, str):
                    return text
        if value.get("type") == "hardBreak":
            return "\n"
        text = value.get("text")
        if isinstance(text, str):
            return text
        attrs = value.get("attrs")
        if isinstance(attrs, dict):
            for key in ("text", "shortName", "url", "title"):
                text = attrs.get(key)
                if isinstance(text, str):
                    return text
        content = value.get("content")
        if isinstance(content, list):
            child_text = [_field_text(item) for item in content]
            if value.get("type") in {"paragraph", "heading", "tableCell", "tableHeader"}:
                return "".join(child_text)
            return _join_non_empty(child_text, "\n")
    return ""


def _normalize_issue_links(links: object) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    if not isinstance(links, list):
        return result
    for link in links:
        if not isinstance(link, dict):
            continue
        link_type = link.get("type") or {}
        outward = link.get("outwardIssue") or {}
        inward = link.get("inwardIssue") or {}
        result.append(
            {
                "type": _field_text(link_type),
                "outward_issue": str(outward.get("key", "")),
                "outward_summary": _field_text((outward.get("fields") or {}).get("summary")),
                "inward_issue": str(inward.get("key", "")),
                "inward_summary": _field_text((inward.get("fields") or {}).get("summary")),
            }
        )
    return result


def _normalize_comments(field: object) -> list[dict[str, str]]:
    if not isinstance(field, dict):
        return []
    comments = field.get("comments")
    if not isinstance(comments, list):
        return []
    result: list[dict[str, str]] = []
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        result.append(
            {
                "author": _field_name(comment.get("author")),
                "created": str(comment.get("created", "")),
                "updated": str(comment.get("updated", "")),
                "body": _field_text(comment.get("body")),
            }
        )
    return result


def _normalize_attachments(field: object) -> list[dict[str, str]]:
    if not isinstance(field, list):
        return []
    result: list[dict[str, str]] = []
    for attachment in field:
        if not isinstance(attachment, dict):
            continue
        result.append(
            {
                "filename": str(attachment.get("filename", "")),
                "size": str(attachment.get("size", "")),
                "mime_type": str(attachment.get("mimeType", "")),
                "created": str(attachment.get("created", "")),
                "author": _field_name(attachment.get("author")),
                "content": str(attachment.get("content", "")),
            }
        )
    return result


def _normalize_issue(payload: dict[str, object], *, include_extra: bool) -> dict[str, object]:
    fields = payload.get("fields")
    if not isinstance(fields, dict):
        fields = {}
    data: dict[str, object] = {
        "key": str(payload.get("key", "")),
        "summary": _field_text(fields.get("summary")),
        "description": _field_text(fields.get("description")),
        "status": _field_text(fields.get("status")),
        "priority": _field_text(fields.get("priority")),
        "labels": fields.get("labels") if isinstance(fields.get("labels"), list) else [],
        "created": str(fields.get("created", "")),
        "updated": str(fields.get("updated", "")),
        "reporter": _field_name(fields.get("reporter")),
        "assignee": _field_name(fields.get("assignee")),
        "issuetype": _field_text(fields.get("issuetype")),
        "issuelinks": _normalize_issue_links(fields.get("issuelinks")),
    }
    if include_extra:
        data["comments"] = _normalize_comments(fields.get("comment"))
        data["attachments"] = _normalize_attachments(fields.get("attachment"))
    return data


def _read_json_response(response: object) -> dict[str, object]:
    if not hasattr(response, "read"):
        raise ValueError("invalid Jira HTTP response object")
    body = response.read(MAX_JIRA_RESPONSE_BYTES + 1)
    if len(body) > MAX_JIRA_RESPONSE_BYTES:
        raise ValueError(
            f"Jira response too large: > {MAX_JIRA_RESPONSE_BYTES} bytes"
        )
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Jira JSON response: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid Jira JSON response: expected object")
    return payload


def _cmd_issue(args: argparse.Namespace, *, include_extra: bool) -> int:
    try:
        issue_key = _normalize_issue_ref(args.issue_ref)
        request, auth_state = _build_issue_request(
            issue_key,
            ISSUE_EXTRA_FIELDS if include_extra else ISSUE_FIELDS,
            args.auth_profile,
        )
    except ValueError as error:
        return _usage_error(args.issue_ref, error)

    try:
        opener = urllib.request.build_opener(_NoRedirectHandler())
        with opener.open(request, timeout=args.timeout) as response:
            payload = _read_json_response(response)
    except urllib.error.HTTPError as error:
        print(f"issue={issue_key}", file=sys.stderr)
        print(f"auth={auth_state}", file=sys.stderr)
        print(f"status={error.code}", file=sys.stderr)
        print(f"error={error.reason}", file=sys.stderr)
        return 1
    except urllib.error.URLError as error:
        print(f"issue={issue_key}", file=sys.stderr)
        print(f"auth={auth_state}", file=sys.stderr)
        print(f"error={error.reason}", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"issue={issue_key}", file=sys.stderr)
        print(f"auth={auth_state}", file=sys.stderr)
        print(f"error={error}", file=sys.stderr)
        return 1

    print(json.dumps(_normalize_issue(payload, include_extra=include_extra), indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read narrow Cisco Jira issue metadata without ad hoc curl literals."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    issue = subparsers.add_parser("issue", help="Read one Jira issue's stable metadata.")
    issue.add_argument("issue_ref")
    issue.add_argument("--auth-profile", choices=sorted(AUTH_PROFILES), required=True)
    issue.add_argument("--timeout", type=int, default=30)
    issue.set_defaults(func=lambda args: _cmd_issue(args, include_extra=False))

    issue_extra = subparsers.add_parser(
        "issue-extra",
        help="Read one Jira issue plus comments and attachments.",
    )
    issue_extra.add_argument("issue_ref")
    issue_extra.add_argument("--auth-profile", choices=sorted(AUTH_PROFILES), required=True)
    issue_extra.add_argument("--timeout", type=int, default=30)
    issue_extra.set_defaults(func=lambda args: _cmd_issue(args, include_extra=True))

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
