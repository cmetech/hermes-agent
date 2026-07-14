"""Pure Jira REST helpers for the ericsson-jira plugin.

No Hermes imports — unit-testable standalone. __init__.py wires these into
the Hermes tool registry. Auth: JIRA_BASE_URL + JIRA_PAT (Bearer) env vars.
"""
from __future__ import annotations

import json
import os
import re

import httpx


class JiraError(RuntimeError):
    pass


GITLAB_URL_RE = re.compile(r"https?://[^\s|\]>)\"',]*gitlab[^\s|\]>)\"',]*", re.I)

MY_TICKETS_JQL = ("assignee = currentUser() AND resolution = Unresolved "
                  "ORDER BY priority DESC, updated DESC")


def _clean_urls(urls):
    """Strip trailing punctuation from extracted URLs."""
    return [u.rstrip(".,;:!?") for u in urls]


def check_available() -> bool:
    return bool(os.environ.get("JIRA_BASE_URL")) and bool(os.environ.get("JIRA_PAT"))


def _client() -> httpx.Client:
    base = (os.environ.get("JIRA_BASE_URL") or "").rstrip("/")
    if not base:
        raise JiraError("JIRA_BASE_URL is not set — add it on the Keys page")
    pat = os.environ.get("JIRA_PAT")
    if not pat:
        raise JiraError("JIRA_PAT is not set — add it on the Keys page")
    return httpx.Client(base_url=base, timeout=30,
                        headers={"Authorization": f"Bearer {pat}"})


def _check(r: httpx.Response) -> None:
    if r.status_code == 401:
        raise JiraError("Jira authentication failed (401) — check JIRA_PAT")
    if r.status_code >= 400:
        raise JiraError(f"Jira API error {r.status_code}: {r.text[:300]}")


def _text(desc) -> str:
    return json.dumps(desc) if isinstance(desc, dict) else (desc or "")


def my_tickets(max_results: int = 25) -> list[dict]:
    with _client() as c:
        r = c.get("/rest/api/2/search", params={
            "jql": MY_TICKETS_JQL, "maxResults": max_results,
            "fields": "summary,status,priority,updated,description"})
        _check(r)
        out = []
        for issue in r.json().get("issues", []):
            f = issue.get("fields", {})
            desc = _text(f.get("description"))
            out.append({
                "key": issue.get("key"),
                "summary": f.get("summary"),
                "status": (f.get("status") or {}).get("name"),
                "priority": (f.get("priority") or {}).get("name"),
                "updated": f.get("updated"),
                "gitlab_urls": _clean_urls(GITLAB_URL_RE.findall(desc)),
            })
        return out


def get_issue(key: str) -> dict:
    with _client() as c:
        r = c.get(f"/rest/api/2/issue/{key}", params={
            "fields": "summary,status,priority,description,comment"})
        _check(r)
        f = r.json().get("fields", {})
        comments = [{"author": (cm.get("author") or {}).get("displayName"),
                     "body": _text(cm.get("body")), "created": cm.get("created")}
                    for cm in (f.get("comment") or {}).get("comments", [])[-5:]]
        return {"key": r.json().get("key"), "summary": f.get("summary"),
                "status": (f.get("status") or {}).get("name"),
                "priority": (f.get("priority") or {}).get("name"),
                "description": _text(f.get("description")),
                "gitlab_urls": _clean_urls(GITLAB_URL_RE.findall(_text(f.get("description")))),
                "comments": comments}


def add_comment(key: str, body: str) -> dict:
    with _client() as c:
        r = c.post(f"/rest/api/2/issue/{key}/comment", json={"body": body})
        _check(r)
        return {"ok": True, "id": r.json().get("id")}


_STR = {"type": "string"}
SCHEMAS = {
    "jira_my_tickets": {
        "name": "jira_my_tickets",
        "description": "List open Jira tickets assigned to the current user, with any GitLab URLs found in their descriptions.",
        "parameters": {"type": "object", "properties": {
            "max_results": {"type": "integer", "description": "Max tickets (default 25)"}}},
    },
    "jira_get_issue": {
        "name": "jira_get_issue",
        "description": "Fetch one Jira issue: summary, status, priority, description, last 5 comments, GitLab URLs.",
        "parameters": {"type": "object", "properties": {"key": _STR}, "required": ["key"]},
    },
    "jira_add_comment": {
        "name": "jira_add_comment",
        "description": "Add a comment to a Jira issue.",
        "parameters": {"type": "object",
                        "properties": {"key": _STR, "body": _STR},
                        "required": ["key", "body"]},
    },
}
