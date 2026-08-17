---
id: artifactory-arm-tools
display_name: Ericsson Artifactory/ARM Tools
aliases: [Ericsson ARM, Ericsson Artifactory, Artifactory connector, release artefact research]
goals:
  - List visible Artifactory repositories and inspect bounded artefact or folder metadata.
  - Search for release artefacts with bounded AQL and preserve truncation warnings.
  - Trace an artefact through build properties to the GitLab pipeline and commit that produced it.
  - Preview and perform an explicitly approved checksum-first deployment of one local file.
  - Preview and perform an explicitly approved deletion of one Artifactory path.
maturity: available
recommendation_eligible: true
source_flows: []
implementation:
  skills: []
  plugins: [plugins/ericsson-arm]
  mcp_servers: []
  workflows: []
  tools:
    - arm_list_repositories
    - arm_artifact_info
    - arm_get_properties
    - arm_search_artifacts
    - arm_deploy
    - arm_delete
platforms: [macos, linux, windows]
configuration:
  - name: base_url
    kind: static-setting
    required: true
    guidance: Configure the exact HTTP(S) Artifactory origin, without a path, in the product plugin settings.
  - name: auth_mode
    kind: static-setting
    required: true
    guidance: Choose bearer for Authorization Bearer tokens or api_key for the legacy X-JFrog-Art-Api header; bearer is the default.
  - name: token
    kind: static-secret
    required: true
    guidance: Store the JFrog access or reference token only through the product's protected secret surface.
  - name: client_cert_path
    kind: static-setting
    required: false
    guidance: When the instance requires mTLS, configure a bounded regular-file client certificate path together with its key.
  - name: client_key_path
    kind: static-setting
    required: false
    guidance: When the instance requires mTLS, configure a bounded regular-file private-key path together with its certificate.
  - name: deploy_root
    kind: static-setting
    required: false
    guidance: Optionally confine upload sources to one local root with POSIX descriptor traversal; configured confinement fails closed on Windows and unsupported platforms.
  - name: max_deploy_megabytes
    kind: static-setting
    required: false
    guidance: Set the bounded maximum upload size from 1 through 16384 MB; the default is 2048 MB.
  - name: request_timeout_seconds
    kind: static-setting
    required: false
    guidance: Set the per-request deadline from 1 through 300 seconds; the default is 60 seconds.
  - name: default_max_results
    kind: static-setting
    required: false
    guidance: Set the default repository-list and AQL result bound from 1 through 100; the default is 25.
reads: [bounded repository listings, bounded artefact and folder metadata including sha256, bounded build properties, bounded AQL search results with permission fields and truncation warnings]
writes: [explicitly previewed and host-approved checksum-first deployment of one local file, explicitly previewed and host-approved deletion of one Artifactory path]
artifacts: [redacted repository evidence, artefact identity and checksums, build-to-pipeline join properties, dry-run write previews, verified deployment and deletion results]
demonstrations: [read-only-live, approved-live]
troubleshooting: [plugin disabled, missing or invalid configuration, expired or rejected mTLS certificate, Artifactory authentication or permission denial, bounded or truncated evidence, unsafe upload source, uncertain write result]
---

# Ericsson Artifactory/ARM Tools

## What it solves

The standalone Ericsson ARM plugin provides bounded Artifactory reads for
repository discovery, artefact and folder metadata, AQL search, and build
properties. Those properties connect a release artefact to the GitLab pipeline
and commit that produced it. The plugin also provides approval-gated deployment
and deletion of one exact target.

## Try saying

- “List the visible generic-local repositories.”
- “Find release ZIPs under this repository path, returning at most 25 results.”
- “Show the sha256 and build properties for this exact artefact.”
- “Trace this artefact to the GitLab pipeline and commit that produced it.”
- “Preview deploying this local file to this repository and path.”
- “Preview deleting this exact Artifactory folder and everything beneath it.”

Specify a repository or AQL filter, an explicit result limit, the requested
answer format and artifact destination, and any exclusions. Preserve every
truncation or permission warning, and rerun only a narrowed read or a newly
reviewed write.

## Questions

For reads, expect the repository, path or bounded AQL query, relevant property
keys, and the maximum results or children needed. For writes, require the exact
repository and destination path, plus the absolute local source file for a
deployment. Do not provide a token, certificate, or private key in chat.

## Reads and writes

`arm_list_repositories`, `arm_artifact_info`, `arm_get_properties`, and
`arm_search_artifacts` are bounded reads. AQL queries cannot supply their own
`.limit()` clause; use `max_results`, and let the connector add the required
permission fields. Remote strings are redacted before they are bounded or
returned.

`arm_deploy` and `arm_delete` require explicit intent (`dry_run: true` for a
preview or `confirm: true` to execute) and current-invocation host approval bound
to the exact arguments. Deployment tries checksum-only publication first and
uploads the complete file only when Artifactory does not already hold the blob.
Deletion targets one path; a folder removes its whole subtree in one request and
may be unrecoverable unless Artifactory trash is enabled.

## Readiness

The `ericsson-arm` standalone plugin is disabled by default. Enable it, configure
`base_url`, `auth_mode`, and the protected `token`, then pass readiness. If the
Artifactory edge requires mTLS, configure both certificate paths; readiness
checks certificate expiry before any remote call. An edge certificate failure is
reported separately from Artifactory token authentication so the correct
credential can be renewed.

The connector supports bearer and API-key header modes. If `deploy_root` is set,
upload confinement uses POSIX descriptor traversal and fails closed on Windows
or another unsupported platform. Leave it empty to permit an approved absolute
source path without that root confinement. Start a fresh conversation after
changing plugin state or configuration. Qualified research guidance appears as
`ericsson-arm:artifact-research` while the plugin is enabled.

## Demonstration

Prefer a bounded read-only live demonstration using repository metadata, sha256,
or build properties. A live deployment or deletion is never merely a demo: the
user must choose the exact action, review its dry-run preview, and grant host
approval for those arguments.

## Artifacts

Inspect the exact repository and path, sha256, properties, truncation and warning
facts, dry-run action, and verified write result at the user-selected destination.
The connector deliberately does not download artefact bytes; sha256 is the
identity used to compare release artefacts.

## Troubleshooting

Separate disabled-plugin, missing configuration, certificate expiry or edge
authentication, Artifactory token authentication, permission, not-found,
capacity, bounded-result, and transient failures. A configured `deploy_root` on
Windows is an intentional fail-closed limitation. Correct the cause, narrow a
read when appropriate, and never blindly rerun a write whose outcome is
uncertain.
