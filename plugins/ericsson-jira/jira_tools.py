"""Compatibility imports for the original Ericsson Jira module name."""

from __future__ import annotations

if __package__:
    from .models import JiraAuth, JiraError
    from .operations import JiraOperations, adf_to_text, extract_gitlab_urls
    from .tools import SCHEMAS, check_available, client_from_configuration, invoke
else:
    from models import JiraAuth, JiraError
    from operations import JiraOperations, adf_to_text, extract_gitlab_urls
    from tools import SCHEMAS, check_available, client_from_configuration, invoke


def my_tickets(max_results=None, *, client):
    return JiraOperations(client).my_tickets(max_results=max_results)


def search_issues(*, client, **kwargs):
    return JiraOperations(client).search_issues(**kwargs)


def get_issue(key, *, client):
    return JiraOperations(client).get_issue(key)


def add_comment(key, body, *, client):
    return JiraOperations(client).add_comment(key, body)
