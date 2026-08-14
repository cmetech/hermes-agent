"""Bounded same-origin SharePoint permission collection."""

from __future__ import annotations

import json
import time
from typing import Any, Collection, Mapping
from urllib.parse import urlsplit

from .models import (
    SharePointAuditError,
    SharePointCancelledError,
    SharePointDeadlineError,
)


_ENDPOINTS = {
    "metadata": "/_api/web?$select=Title,Description,Url,Created,LastItemModifiedDate,WebTemplate,Language",
    "users": "/_api/web/siteusers?$select=Id,Title,LoginName,Email,IsSiteAdmin,PrincipalType&$top=200",
    "roles": "/_api/web/roleassignments?$expand=Member,RoleDefinitionBindings&$top=200",
    "lists": "/_api/web/lists?$select=Id,Title,Description,BaseType,ItemCount,Created,LastItemModifiedDate&$filter=Hidden%20eq%20false&$top=200",
    "subsites": "/_api/web/webs?$select=Title,Url,Created,WebTemplate&$top=200",
}


def _expression(category: str, max_pages: int, groups: list[tuple[Any, str]]) -> str:
    """Return bounded JS using only relative, same-origin SharePoint REST URLs."""
    marker = json.dumps(
        {"category": category, "max_pages": max_pages}, separators=(",", ":")
    )
    preamble = f"""
const MAX_PAGES={int(max_pages)};
async function getOne(url) {{
  const absolute=new URL(url,location.origin);
  if(absolute.origin!==location.origin) throw new Error('cross_origin');
  const response=await fetch(absolute.href,{{credentials:'same-origin',headers:{{Accept:'application/json;odata=nometadata'}}}});
  if(!response.ok) throw new Error('remote_error');
  return await response.json();
}}
async function getAll(url) {{
  const rows=[]; let next=url; let pages=0; let truncated=false;
  while(next) {{
    if(pages>=MAX_PAGES) {{ truncated=true; break; }}
    const payload=await getOne(next); pages+=1;
    const data=payload.d||payload;
    const values=Array.isArray(data.value)?data.value:(Array.isArray(data.results)?data.results:[]);
    rows.push(...values);
    next=payload['@odata.nextLink']||payload['odata.nextLink']||data.__next||null;
  }}
  return {{value:rows,truncated,pages}};
}}
"""
    if category == "metadata":
        body = f"return await getOne({json.dumps(_ENDPOINTS[category])});"
    elif category == "roles":
        body = f"""
const batch=await getAll({json.dumps(_ENDPOINTS[category])}); const rows=[];
for(const assignment of batch.value) {{
  const member=assignment.Member||{{}};
  const roles=Array.isArray(assignment.RoleDefinitionBindings)?assignment.RoleDefinitionBindings:((assignment.RoleDefinitionBindings||{{}}).results||[]);
  for(const role of roles) rows.push({{principal_id:member.Id,principal_type:member.PrincipalType,principal_name:member.Title||'',principal_login:member.LoginName||'',principal_email:member.Email||'',role:role.Name||'',group_id:member.PrincipalType===8?member.Id:null,group_name:member.PrincipalType===8?(member.Title||''):''}});
}}
return {{value:rows,truncated:batch.truncated,pages:batch.pages}};
"""
    elif category == "members":
        body = f"""
const groups={json.dumps(groups, ensure_ascii=False)}; const rows=[]; let truncated=false; let pages=0;
for(const group of groups) {{
  const batch=await getAll(`/_api/web/sitegroups(${{Number(group[0])}})/users?$select=Id,Title,LoginName,Email&$top=200`);
  pages+=batch.pages; truncated=truncated||batch.truncated;
  for(const user of batch.value) rows.push({{group_id:group[0],group_name:group[1],member_id:user.Id,member_name:user.Title||'',member_email:user.Email||'',member_login:user.LoginName||''}});
}}
return {{value:rows,truncated,pages}};
"""
    else:
        body = f"return await getAll({json.dumps(_ENDPOINTS[category])});"
    return f"/*{marker}*/(async()=>{{{preamble}{body}}})()"


def _control(*, deadline, cancel_check, clock=time.monotonic):
    if cancel_check is not None and cancel_check():
        raise SharePointCancelledError("SharePoint audit was cancelled")
    if deadline is not None and clock() >= deadline:
        raise SharePointDeadlineError("SharePoint audit deadline exceeded")


def _safe_site(
    site: Mapping[str, Any], allowed_hosts: Collection[str]
) -> dict[str, str]:
    name = str(site.get("name") or "").strip()
    url = str(site.get("url") or "").strip().rstrip("/")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise SharePointAuditError("site is outside configured tenant origin") from None
    if (
        not name
        or len(name) > 512
        or parts.scheme != "https"
        or (parts.hostname or "").lower().rstrip(".") not in allowed_hosts
        or parts.username is not None
        or parts.password is not None
        or port not in {None, 443}
        or parts.query
        or parts.fragment
    ):
        raise SharePointAuditError("site is outside configured tenant origin")
    return {"name": name, "url": url}


def _text(value, maximum=4096):
    value = str(value or "")
    if len(value) > maximum or any(ord(c) < 32 for c in value):
        return ""
    return value


def _normalized(category: str, raw: Any) -> Any:
    if category == "metadata":
        row = raw if isinstance(raw, Mapping) else {}
        return {
            "title": _text(row.get("Title")),
            "description": _text(row.get("Description")),
            "url": _text(row.get("Url"), 8192),
            "created": _text(row.get("Created"), 128),
            "modified": _text(row.get("LastItemModifiedDate"), 128),
            "template": _text(row.get("WebTemplate"), 128),
            "language": row.get("Language"),
        }
    rows = raw if isinstance(raw, list) else []
    projections = {
        "users": lambda r: {
            "id": r.get("Id"),
            "name": _text(r.get("Title")),
            "login": _text(r.get("LoginName")),
            "email": _text(r.get("Email")),
            "site_admin": r.get("IsSiteAdmin") is True,
            "principal_type": r.get("PrincipalType"),
        },
        "roles": lambda r: {
            "principal_id": r.get("principal_id"),
            "principal_type": r.get("principal_type"),
            "principal_name": _text(r.get("principal_name")),
            "principal_login": _text(r.get("principal_login")),
            "principal_email": _text(r.get("principal_email")),
            "role": _text(r.get("role")),
            "group_id": r.get("group_id"),
            "group_name": _text(r.get("group_name")),
        },
        "members": lambda r: {
            "group_id": r.get("group_id"),
            "group_name": _text(r.get("group_name")),
            "member_id": r.get("member_id"),
            "member_name": _text(r.get("member_name")),
            "member_email": _text(r.get("member_email")),
            "member_login": _text(r.get("member_login")),
        },
        "lists": lambda r: {
            "id": _text(r.get("Id")),
            "title": _text(r.get("Title")),
            "description": _text(r.get("Description")),
            "base_type": r.get("BaseType"),
            "item_count": r.get("ItemCount"),
            "created": _text(r.get("Created"), 128),
            "modified": _text(r.get("LastItemModifiedDate"), 128),
        },
        "subsites": lambda r: {
            "title": _text(r.get("Title")),
            "url": _text(r.get("Url"), 8192),
            "created": _text(r.get("Created"), 128),
            "template": _text(r.get("WebTemplate"), 128),
        },
    }
    projection = projections[category]
    return [projection(row) for row in rows if isinstance(row, Mapping)]


async def audit_sites_with_session(
    session,
    *,
    sites,
    selected,
    allowed_hosts,
    max_sites,
    max_pages_per_category,
    max_rows_per_category,
    max_total_rows,
    max_total_bytes,
    deadline=None,
    cancel_check=None,
    clock=time.monotonic,
):
    allowed = {str(host).lower().rstrip(".") for host in allowed_hosts}
    normalized_sites = [_safe_site(site, allowed) for site in sites]
    wanted = {str(value).strip().casefold() for value in selected if str(value).strip()}
    if wanted:
        normalized_sites = [
            site
            for site in normalized_sites
            if site["name"].casefold() in wanted or site["url"].casefold() in wanted
        ]
    truncated_reasons = []
    if len(normalized_sites) > max_sites:
        normalized_sites = normalized_sites[:max_sites]
        truncated_reasons.append("site limit reached")
    output = []
    total_rows = 0
    total_bytes = 0
    overall_partial = False
    overall_truncated = bool(truncated_reasons)
    for site in normalized_sites:
        _control(deadline=deadline, cancel_check=cancel_check, clock=clock)
        navigation = session.navigate(site["url"])
        site_result = {
            "name": site["name"],
            "url": site["url"],
            "status": "complete",
            "warnings": [],
        }
        if not isinstance(navigation, Mapping) or navigation.get("success") is not True:
            site_result["status"] = "unreachable"
            site_result["warnings"].append(
                {"category": "navigation", "status": "unreachable"}
            )
            output.append(site_result)
            overall_partial = True
            continue
        categories = ("metadata", "users", "roles", "members", "lists", "subsites")
        groups: list[tuple[Any, str]] = []
        for category in categories:
            _control(deadline=deadline, cancel_check=cancel_check, clock=clock)
            expression = _expression(category, max_pages_per_category, groups)
            evaluated = session.eval(expression)
            if (
                not isinstance(evaluated, Mapping)
                or evaluated.get("success") is not True
            ):
                site_result[category if category != "roles" else "permissions"] = (
                    [] if category != "metadata" else {}
                )
                site_result["warnings"].append(
                    {"category": category, "status": "remote_unavailable"}
                )
                site_result["status"] = "partial"
                overall_partial = True
                continue
            raw_result = evaluated.get("result")
            category_truncated = False
            if category != "metadata" and isinstance(raw_result, Mapping):
                category_truncated = raw_result.get("truncated") is True
                raw_result = raw_result.get("value")
            normalized = _normalized(category, raw_result)
            if category == "metadata" and normalized.get("url"):
                _safe_site(
                    {
                        "name": normalized.get("title") or site["name"],
                        "url": normalized["url"],
                    },
                    allowed,
                )
            key = {"roles": "permissions", "members": "group_members"}.get(
                category, category
            )
            if isinstance(normalized, list):
                available = min(max_rows_per_category, max_total_rows - total_rows)
                if len(normalized) > available:
                    normalized = normalized[: max(0, available)]
                    reason = f"{category} row limit reached"
                    site_result["warnings"].append(
                        {"category": category, "status": "truncated"}
                    )
                    if reason not in truncated_reasons:
                        truncated_reasons.append(reason)
                    site_result["status"] = "truncated"
                    overall_truncated = True
                total_rows += len(normalized)
                if category == "roles":
                    groups = [
                        (row["group_id"], row["group_name"])
                        for row in normalized
                        if row.get("group_id") is not None
                    ]
                if category_truncated:
                    site_result["warnings"].append(
                        {"category": category, "status": "truncated"}
                    )
                    reason = f"{category} page limit reached"
                    if reason not in truncated_reasons:
                        truncated_reasons.append(reason)
                    site_result["status"] = "truncated"
                    overall_truncated = True
            encoded = len(
                json.dumps(
                    normalized, ensure_ascii=False, separators=(",", ":")
                ).encode()
            )
            if total_bytes + encoded > max_total_bytes:
                normalized = [] if isinstance(normalized, list) else {}
                site_result["warnings"].append(
                    {"category": category, "status": "truncated"}
                )
                if "aggregate byte limit reached" not in truncated_reasons:
                    truncated_reasons.append("aggregate byte limit reached")
                site_result["status"] = "truncated"
                overall_truncated = True
            else:
                total_bytes += encoded
            site_result[key] = normalized
        output.append(site_result)
    status = (
        "truncated"
        if overall_truncated
        else "partial"
        if overall_partial
        else "complete"
    )
    return {
        "status": status,
        "sites": output,
        "warnings": [],
        "truncated": overall_truncated,
        "truncation_reasons": truncated_reasons,
        "counts": {"sites": len(output), "rows": total_rows, "bytes": total_bytes},
    }
