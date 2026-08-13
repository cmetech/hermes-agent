---
name: defect-triage
description: Triage one selected Jira defect and prepare an optional comment preview. Use when the user asks whether a ticket is suitable for an automated fix or needs more information.
metadata:
  hermes:
    tags: [Ericsson, Jira, Triage]
---

<objective>
Classify one selected Jira ticket as auto-fix, needs-info, manual-review, or not-a-code-fix, explain the evidence, and prepare—not silently post—any requested Jira comment.
</objective>

<workflow>
Read the selected ticket with <tool name="jira_get_issue" mode="read">jira_get_issue</tool>. Judge whether the problem is a code defect, specific enough to locate, bounded to a safe change, and supported by the returned evidence. Use confidence from 0 to 100. Below 40, use needs-info. An auto-fix assessment below 70 becomes manual-review. These thresholds shape guidance only.

If the user wants Jira updated, compose the exact issue key and final comment text, show both for review, and use <tool name="jira_add_comment" mode="write">jira_add_comment</tool> only through visible current-action host approval. Triage does not grant write authority, workflow authority, repository authority, or permission to skip approval. Reconcile an uncertain comment result read-only before considering another attempt.
</workflow>

<boundaries>
This is single-ticket guidance. Exact multi-ticket batching and aggregation are outside this skill. Do not hide model prompts, invoke transport commands, or claim that confidence makes a change safe.
</boundaries>

<success_criteria>
The result contains one issue key, one category, bounded confidence, evidence-based reasoning, missing information, warnings, and a separately identified comment preview when requested.
</success_criteria>
