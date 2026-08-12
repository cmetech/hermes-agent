---
name: gitlab
description: Routes users to qualified Ericsson GitLab guidance. Use when a user asks to enable, configure, or discover GitLab connector capabilities.
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
When the plugin is configured and ready, route repository questions to ericsson-gitlab:repository-research, reviews to ericsson-gitlab:merge-request-review, and CI questions to ericsson-gitlab:ci-investigation.
</routing>

<boundaries>
Do not claim the plugin is ready until its readiness check succeeds. Never claim that this router performs GitLab reads or writes itself.
</boundaries>

<success_criteria>
The user knows whether the plugin is disabled, what must be configured, why a fresh conversation is needed, and which qualified skill owns the requested work.
</success_criteria>
