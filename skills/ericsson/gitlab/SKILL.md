---
name: gitlab
description: Use when a user asks to enable or configure Ericsson GitLab, explore groups or repositories, inspect commits or merge requests, investigate CI, or schedule an activity digest.
metadata:
  hermes:
    tags: [Ericsson, GitLab, Onboarding]
---
<objective>
Explain Ericsson GitLab availability and route a ready conversation to the connector-owned qualified skills without duplicating their tool instructions.
</objective>

<quick_start>
Check whether the ericsson-gitlab plugin is disabled. Guide the user through enablement and the product configuration surface for origin, personal access token, and optional client certificate paths. A fresh conversation is required after enablement so the tool and skill surface remains stable.
</quick_start>

<routing>
When the plugin is configured and ready, route group, subgroup, project, file, “latest commit,” commit-comment, and commit-discussion questions to `ericsson-gitlab:repository-research`. Route merge-request discovery, “recent merge request,” commits, discussions, and reviews to `ericsson-gitlab:merge-request-review`. Route pipeline and CI questions to `ericsson-gitlab:ci-investigation`. Route one-time summaries and a recurring or daily digest of commits or merge requests to `ericsson-gitlab:gitlab-activity-digest`.
</routing>

<boundaries>
Do not claim the plugin is ready until its readiness check succeeds. Never claim that this router performs GitLab reads or writes itself.
</boundaries>

<success_criteria>
The user knows whether the plugin is disabled, what must be configured, why a fresh conversation is needed, and which qualified skill owns the requested work.
</success_criteria>
