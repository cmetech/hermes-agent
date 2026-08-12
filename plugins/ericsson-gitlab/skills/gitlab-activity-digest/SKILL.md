---
name: gitlab-activity-digest
description: Use when a user asks for a one-time or recurring daily GitLab digest of recent commits or merge requests, including activity from the last 24 hours.
metadata:
  hermes:
    tags: [Ericsson, GitLab, Digest, Cron]
---
<objective>
Produce a bounded read-only GitLab activity digest now, or configure a local recurring schedule that produces the same digest later.
</objective>

<quick_start>
Resolve the project with <tool name="gitlab_resolve_project" mode="read">gitlab_resolve_project</tool>. For commits, call <tool name="gitlab_list_commits" mode="read">gitlab_list_commits</tool> with `lookback_hours=24`. For new merge requests, call <tool name="gitlab_list_merge_requests" mode="read">gitlab_list_merge_requests</tool> with `lookback_hours=24`; this means created in the rolling window. If the user explicitly asks for recently active merge requests, use `updated_after` instead.
</quick_start>

<interactive_scheduling>
When the user asks to run daily or on another recurrence, call <tool name="cronjob" mode="write">cronjob</tool> once to create the schedule. The stored prompt is self-contained and includes:

- the canonical project path or ID;
- a rolling “last 24 hours” window, not a fixed date;
- the qualified skill `ericsson-gitlab:gitlab-activity-digest`;
- the connector toolset `ericsson-gitlab`;
- the requested summary contents and origin delivery destination;
- the exact no-activity contract: return `[SILENT]` and nothing else.
</interactive_scheduling>

<scheduled_execution>
When cron invokes this skill, execute the GitLab reads and summarize only returned activity. Do not call `cronjob`, create another schedule, or reschedule the current job. If both commit and merge-request collections are empty, return exactly `[SILENT]`. Preserve authentication, permission, capacity, warning, truncation, and continuation facts instead of converting a failed or incomplete read into “no activity.”
</scheduled_execution>

<boundaries>
GitLab access is read-only. Scheduling is the only local mutation and requires the user's explicit recurring intent. Never expose the PAT, certificate material, or remote response bodies. A Cloudflare or client-certificate authentication failure is a failed run, not an empty digest.
</boundaries>

<success_criteria>
The digest identifies the canonical project and rolling window, distinguishes newly created merge requests from updated activity, summarizes bounded commits and merge requests with canonical links, and either delivers useful activity or uses exact `[SILENT]` behavior without recursive scheduling.
</success_criteria>
