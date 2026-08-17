---
name: artifact-research
description: Trace one release artefact through Artifactory metadata to the GitLab build and commit that produced it.
metadata:
  hermes:
    tags: [Ericsson, Artifactory, ARM, GitLab, Release]
---

<objective>
Trace a specific release artefact back to its producing build using bounded Artifactory evidence. Preserve the distinction between observed metadata and inferences about its GitLab pipeline or commit.
</objective>

<workflow>
Start with <tool name="arm_search_artifacts" mode="read">arm_search_artifacts</tool> to find the artefact using a bounded AQL query and result limit. Select one explicit repository and path from its results.

Use <tool name="arm_artifact_info" mode="read">arm_artifact_info</tool> to confirm that artefact's identity. The returned sha256 is the artefact's identity; never download an artefact merely to compare it. This avoids moving release bytes when its verified identity is already available as metadata.

Then use <tool name="arm_get_properties" mode="read">arm_get_properties</tool> for the selected artefact. `build.name`, `build.number`, and `vcs.revision` join the release artefact to the GitLab pipeline and commit that produced it. Use the build name and number to find the pipeline, and `vcs.revision` to verify the associated commit. This is the join the other connectors cannot make on their own.

Report the exact repository/path, sha256, build properties, the matching pipeline and commit if found, and any gaps or contradictions. Treat an absent property, a truncated search, or a non-matching GitLab record as incomplete evidence rather than proof of absence.
</workflow>

<safety>
This research workflow performs reads only. Never expose credentials or remote error text, and preserve permission, not-found, and truncation outcomes.

`arm_delete` is not part of research. A folder path is recursive: it removes the entire subtree and may be unrecoverable unless Artifactory trash is enabled. If deletion is requested, the correct first call is `arm_delete` with `dry_run: true` to preview the exact target; only use `confirm: true` after explicit approval of that preview.
</safety>

<success_criteria>
The result identifies one exact artefact by repository, path, and sha256; records its `build.name`, `build.number`, and `vcs.revision`; distinguishes confirmed GitLab evidence from inference; and names any missing or conflicting evidence.
</success_criteria>
