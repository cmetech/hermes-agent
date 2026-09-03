# Workflow Package Marketplace Design

**Date:** 2026-09-03

**Status:** Review requested

**Audience:** Engineers implementing, reviewing, testing, documenting, or maintaining Hermes workflow packaging and marketplace support

## 1. Summary

Hermes will add a native workflow-package marketplace for Git repositories. A repository may publish multiple independently versioned workflow packages, and one package may contain multiple workflows plus their command Markdown, scripts, MCP definitions, fixtures, and other package-owned resources.

Both the Hermes CLI and co-worker Desktop will use one workflow-specific marketplace service. The service owns source catalogs, Git fetching, package validation, digest verification, installation provenance, update and removal transactions, and integration with the existing workflow trust system. It supports public and authenticated private Git repositories by using the operator's existing Git credential helpers, SSH agent, `gh` authentication, or supported environment credentials. Hermes does not add a second repository credential store.

Installation and trust are separate decisions. A package may be installed and inspected while untrusted. Running its workflows requires an explicit review and trust grant bound to the exact package and workflow risk digests. Updates that change any package-owned byte invalidate package-associated trust.

This work also publishes the canonical versioned package contract and cross-language test vectors that Workflow Studio will consume byte-for-byte. Workflow Studio remains responsible for creation and local package preparation; Hermes remains responsible for remote discovery, installation, trust, and execution.

## 2. Goals

The release must:

- publish one canonical Hermes-owned workflow-package contract and conformance vectors;
- support multiple non-overlapping packages in one Git repository;
- support multiple workflow definition/companion pairs in one package;
- discover packages through a deterministic repository index;
- support direct installation from a repository URL, optional ref, and package path;
- support public and authenticated private HTTPS and SSH Git sources;
- provide matching source, search, inspect, install, update, remove, and trust behavior in the CLI and Desktop;
- install packages into the selected Hermes profile on the backend that owns that profile;
- preserve existing loose, project, profile, showcase, and bundled workflow behavior;
- verify package bytes, workflow structure, resource references, compatibility, and risk before installation;
- distinguish structural blockers from destination-dependent advisories;
- record exact Git and package provenance outside the installed package;
- make installation, update, and removal transactional and recoverable;
- keep package installation separate from trust;
- invalidate marketplace trust when any package-owned byte changes;
- redact credentials from errors, logs, API projections, and persisted state;
- work without adding a model-facing tool or changing conversation prompts; and
- document publisher, operator, authentication, recovery, and migration workflows.

## 3. Non-goals

This release does not:

- turn Workflow Studio into an execution or deployment service;
- push, pull, merge, tag, or manage remotes from Workflow Studio;
- introduce a hosted registry, artifact server, or Hermes-operated marketplace service;
- store Git passwords, access tokens, private keys, or credential-helper results;
- automatically trust an installed or updated package;
- execute package scripts, commands, MCP servers, or workflows during installation validation;
- install destination runtimes, providers, system tools, services, or secrets;
- replace Ericsson or other build-time bundled-capability seeding;
- convert existing loose workflows into packages automatically;
- expose marketplace installation as an LLM tool; or
- guarantee that an arbitrary Git server honors partial-clone filtering.

## 4. Architecture and ownership

The Hermes backend is the single authority for marketplace state and package mutations.

```text
CLI ------------------+
                       +--> Workflow Marketplace Service
Desktop UI --> API ----+          |
                                  +-- canonical contract
                                  +-- source catalogs and cache
                                  +-- Git fetch and authentication
                                  +-- package validation and digests
                                  +-- compatibility and risk analysis
                                  +-- installation and provenance
                                  +-- update and removal transactions
                                  +-- workflow trust integration
```

The service is a presentation-independent Python domain layer under the workflow plugin. The existing CLI registers thin command adapters. The authenticated workflow plugin API exposes bounded, typed projections for Desktop. The renderer never reads repositories, invokes Git, resolves credentials, or writes profile files directly.

The marketplace implementation must be split into focused modules rather than extending the already-large `plugins/workflow/cli.py`. The detailed plan will finalize names, but the intended boundaries are:

- contract loading and validation;
- manifest, index, and digest projection;
- normalized source identity and persisted source configuration;
- Git resolution and credential-safe fetch;
- catalog refresh and cache;
- installed-package provenance;
- prepare/confirm transactions;
- compatibility and risk projection; and
- CLI/API adapters.

Reusable Git URL parsing, executable discovery, noninteractive environment handling, URL sanitization, and credential cleanup should be extracted from the existing plugin installer where doing so preserves current plugin behavior. Workflow manifest validation, package digests, admission, and trust remain workflow-specific.

No model-facing tool, system-prompt content, or conversation-time package catalog is added. This feature follows Hermes's CLI plus Desktop surface rung.

## 5. Canonical package contract

Hermes publishes these immutable version-one artifacts:

```text
plugins/workflow/contracts/workflow-package-v1.json
plugins/workflow/contracts/workflow-package-v1-vectors.json
```

`workflow-package-v1.json` is a contract envelope, not merely a hand-maintained field list. At minimum it contains:

- the contract-envelope version;
- the `workflow-package.json` JSON Schema;
- the repository marketplace-index JSON Schema;
- the generated `digests.json` JSON Schema;
- normalized path and package-root rules;
- included and excluded digest paths;
- file-count, per-file, total-size, index-size, and catalog-entry limits;
- the package composite-hash algorithm;
- compatibility vocabulary and comparison rules; and
- stable diagnostic codes required by consumers.

`workflow-package-v1-vectors.json` contains positive and negative examples plus exact expected normalized paths, per-file hashes, composite digests, and diagnostic codes. Vectors include UTF-8 and binary bytes, empty files, line-ending distinctions, Unicode paths, ordering, traversal, backslashes, NULs, case collisions, symlinks, oversized content, nested roots, malformed manifests, malformed indexes, and digest mismatches.

Workflow Studio copies these two files byte-for-byte from an immutable reviewed Hermes commit and verifies their digests. Studio must not maintain a second schema or digest implementation by field inventory.

Unsupported newer contract versions may be listed as unsupported metadata, but install, update, trust, and structured editing fail closed. Neither Hermes nor Studio guesses how to reinterpret an unknown version.

## 6. Repository and package layout

The recommended layout is:

```text
repository/
|-- packages/
|   |-- laptop-support/
|   |   |-- workflow-package.json
|   |   |-- workflows/
|   |   |   |-- laptop-diagnostic.yaml
|   |   |   |-- laptop-diagnostic.hermes.yaml
|   |   |   `-- collect-support-bundle.yaml
|   |   |-- commands/
|   |   |   `-- interpret-report.md
|   |   |-- scripts/
|   |   |   |-- analyze-snapshot.py
|   |   |   `-- render-report.py
|   |   |-- mcp/
|   |   |-- fixtures/
|   |   `-- digests.json
|   `-- inbox-productivity/
|       `-- workflow-package.json
`-- .well-known/
    `-- hermes-workflows/
        `-- index.json
```

`packages/` is a convention, not a required root. A package root is identified by `workflow-package.json`. Package roots may not overlap or nest. All canonical member paths use forward slashes and resolve inside exactly one package root. Absolute paths, traversal, NUL bytes, ambiguous normalization, case-fold collisions, nested repository metadata, and all distributable symlinks are rejected.

All regular files below a package root are package-owned unless the canonical contract explicitly excludes a generated self-reference. Version one excludes `digests.json` from its own package composite digest. The manifest, workflow definitions, companions, commands, scripts, MCP definitions, fixtures, and other supporting resources are included. File bytes are not reformatted or line-ending-normalized for hashing.

Version-one package limits start from the workflow plugin's existing bounded-resource policy:

- at most 512 included files;
- at most 1 MiB per included file; and
- at most 8 MiB of included file bytes.

The canonical artifact, not duplicated source constants, is authoritative. A later contract version may revise limits explicitly.

## 7. Package manifest

`workflow-package.json` is the source of truth for package identity, membership, publishing metadata, and declared external requirements. It cannot define nodes, dependencies, execution policy, editor state, or trust.

Version one contains this shape, subject to the exact canonical schema:

```json
{
  "schemaVersion": 1,
  "id": "laptop-support",
  "version": "1.1.0",
  "displayName": "Laptop Support",
  "description": "Diagnostic and support-bundle workflows for laptops.",
  "license": "MIT",
  "publisher": "example-company",
  "tags": ["diagnostics", "support"],
  "workflows": [
    {
      "definition": "workflows/laptop-diagnostic.yaml",
      "companion": "workflows/laptop-diagnostic.hermes.yaml"
    },
    {
      "definition": "workflows/collect-support-bundle.yaml"
    }
  ],
  "externalRequirements": {
    "runtimes": ["uv"],
    "tools": [],
    "providers": [],
    "services": [],
    "secrets": []
  }
}
```

Package IDs are unique within a repository index. Versions are semantic versions. List order is stable and meaningful only where the contract declares it. A manifest cannot carry a trusted flag, admitted digest, local install path, remote credential, editor-only state, resolved commit, or mutable update channel.

Workflow YAML remains the sole authority for graph and node behavior. Each command, script, MCP definition, fixture, or supporting file is authoritative as its own file.

## 8. Digest and index outputs

`digests.json` is deterministic generated output. It contains the contract version, hash algorithm, sorted file records with path, byte size, and SHA-256, plus the composite package digest. The composite encoding is specified byte-for-byte in the canonical contract and cross-language vectors.

The repository index lives at:

```text
.well-known/hermes-workflows/index.json
```

It is deterministic and derived from the current package manifests and digests. Entries are sorted by package ID and include only bounded discovery metadata, the repository-relative package path, contract version, package version, and expected digest. The index contains no timestamp, credentials, trusted state, local paths, or self-referential Git commit SHA.

The co-worker records the exact fetched commit independently during installation and recomputes every digest from fetched bytes. Publisher-provided digest files and indexes are claims to verify, not trust anchors.

Any included package byte change changes the distribution package digest. Marketplace workflow trust incorporates that distribution digest, so a change to any workflow, companion, command, script, MCP definition, fixture, manifest, or other included resource requires renewed marketplace trust.

## 9. Sources and Git authentication

A registered source stores only:

- a user-defined source name;
- a normalized repository URL suitable for display and invocation;
- an optional branch, tag, or commit ref; and
- enabled/disabled state.

When no ref is configured, Git resolves the repository's default branch. Each refresh records the resolved commit and sanitized status in cache, but the configured source remains the URL plus optional ref. Source names are profile-local and unique after canonical normalization.

Hermes supports two discovery paths:

1. A registered source reads `.well-known/hermes-workflows/index.json`, enabling search and installation by `source/package-id`.
2. A direct install accepts a supported HTTPS or SSH Git URL, optional ref, and package subdirectory. Recognized GitHub-style browser/tree URLs may be normalized through the existing Git URL parser.

Private repositories use the credentials already available to Git on the target backend:

- system Git credential helpers;
- SSH configuration and agent;
- `gh auth`/GitHub credential integration; or
- supported environment-token behavior already implemented by Hermes Git installers.

Hermes never persists credential-bearing URLs, helper output, access tokens, passwords, private keys, or authorization headers. Before display or persistence, repository identities and Git errors pass through the shared credential sanitizer. Temporary repository configuration is inspected and credential-bearing remote URLs are removed or rewritten before any retained diagnostic material is produced.

Desktop provides setup guidance and Retry when authentication fails; it does not add a credential-entry or secret-storage form.

## 10. Source refresh and catalog cache

Refreshing a source resolves its configured ref to an exact commit and fetches a temporary, shallow, no-checkout clone. Hermes requests blob filtering and sparse checkout for the index and selected package paths when the Git server supports them. Servers that ignore filtering remain supported, but operations retain time, checked-out file-count, checked-out byte, and temporary-storage safeguards.

The fetched index is schema-validated and bounded before its entries are exposed. A failed refresh does not replace the most recent valid cached catalog. Results distinguish fresh, stale, disabled, authentication-failed, malformed, incompatible, and unavailable states.

Catalog cache is backend/profile state, never workflow-package content. Search may use the last verified cache while showing its resolved commit and freshness. Installation always fetches and verifies a candidate again; a cached listing alone is never sufficient for installation.

## 11. Installed identity, layout, and provenance

Installed identity is the pair `source identity + package ID`. Different sources may publish the same package ID without overwriting one another. Direct installations receive a deterministic source key derived from the sanitized normalized repository identity, never from credentials.

Packages are installed under a dedicated namespaced subtree of the active profile workflow directory:

```text
$HERMES_HOME/workflows/marketplace/<source-key>/<package-id>/
```

Marketplace configuration, cache, transaction state, and provenance live outside the discoverable workflow tree:

```text
$HERMES_HOME/marketplace/workflows/
```

Exact filenames and persisted schema versions will be fixed in the implementation plan. Persistence uses bounded, versioned JSON, file locking, same-directory temporary files, flush, and atomic replacement.

Provenance records at least:

- installed identity;
- sanitized normalized repository identity;
- configured ref, if any;
- exact resolved commit SHA;
- repository-relative package path;
- manifest package ID and semantic version;
- contract version;
- verified distribution package digest;
- installation timestamp and actor class; and
- workflow trust-binding identities created for this installation.

Provenance is local authority and is not copied from package metadata.

Current discovery must become manifest-aware. Under a valid package root it enumerates only definition paths declared by `workflow-package.json`; MCP YAML, fixtures, companions, and unrelated resource YAML must never be mistaken for workflow definitions. Loose project and profile workflow discovery remains unchanged outside package roots.

## 12. Installation transaction

Installation is a two-phase prepare/confirm transaction:

```text
resolve source and ref
  -> fetch into private temporary staging
  -> resolve exact commit
  -> locate one package root
  -> validate paths, limits, manifest, workflows, index, and digests
  -> compute compatibility and risk
  -> return bounded review plus expiring confirmation token
  -> confirm the exact candidate
  -> recheck identities and candidate bytes
  -> atomically place the package in the active profile
  -> atomically record provenance
  -> expose it as installed but untrusted
```

Preparation blocks on:

- unsupported or invalid package contracts;
- invalid manifest or index structure;
- source/package identity conflicts;
- missing declared workflow definitions or companions;
- invalid workflow YAML or structural contract violations;
- missing package-owned resources required by workflow resolution;
- unsafe, escaping, overlapping, nested, ambiguous, or symlinked paths;
- file-count or byte-limit violations;
- publisher digest or index mismatch;
- repository/package mutation during preparation;
- an installation destination that cannot be proven contained and safe; or
- an inability to make the replacement transaction recoverable.

Missing runtimes, tools, providers, services, credentials, MCP reachability, and execution outcomes are destination-dependent advisories. They are displayed but do not make an otherwise intact package structurally invalid.

Preparation does not execute any package content. Static parsers may inspect recognized text formats without importing or running them.

The confirmation token is single-use, expires, is scoped to the authenticated operator and target profile, and is bound to the source, exact commit, package path, manifest identity/version, distribution digest, destination identity, and review projection. If any bound input changes, confirmation fails and requires a new preparation.

A failed installation leaves any existing installed package and provenance unchanged. Successful installation never grants trust automatically.

## 13. Update, removal, and recovery

Update repeats the complete fetch, validation, digest, compatibility, and risk pipeline. The review shows:

- old and candidate version;
- old and candidate exact commit;
- added, modified, removed, and renamed files;
- workflow membership changes;
- external requirement changes;
- risk changes; and
- compatibility changes.

Update uses the same prepare/confirm token binding as installation. Replacement occurs through a same-filesystem staged directory and recoverable atomic swap. Provenance changes only after the candidate is installed. If replacement or provenance persistence fails, Hermes restores the prior package and reports an explicit recovery diagnostic.

Any distribution digest change removes the marketplace installation's prior trust grants. The newly installed workflows remain untrusted until separately reviewed. An invalid update leaves the current installed and trusted version intact.

Removal has a preview and confirmation step bound to the installed identity and current provenance. It removes exactly one namespaced package, its provenance, and trust grants associated with that installation. It cannot remove a source definition, another source's package, loose workflows, bundled workflows, or trust granted independently for identical content.

Interrupted transaction state is bounded and self-identifying. Startup/doctor recovery removes abandoned staging directories only after proving ownership, and either completes or rolls back a recorded swap. It never guesses based solely on a directory name.

## 14. Trust integration

Installation and trust are distinct authenticated operations.

Trust review displays, per workflow:

- the workflow definition and companion identity;
- shell and script nodes, including named script resources;
- command resources;
- local and remote MCP declarations;
- requested tools and skills;
- outward-action and approval-related risk;
- package-owned executable/resource paths;
- external runtimes, tools, providers, services, and secrets;
- compatibility findings;
- verified package version, source, commit, and distribution digest; and
- the exact workflow package and risk digests to be trusted.

For marketplace workflows, the runtime trust key derives from the distribution package digest, workflow-relative identity, and the workflow's existing executable-resource closure digest. This preserves per-workflow risk decisions while ensuring that any included distribution byte change produces new marketplace trust material.

The trust store remains content-bound but gains origin-aware grants. A record may be granted manually or by a specific marketplace installation identity. Runtime classification succeeds only when an applicable grant matches both the effective workflow package digest and risk digest. Removing one installation deletes only that installation's grants; an independent manual grant or a grant from another identical installation remains intact. Existing trust records migrate read-compatibly.

CLI may review/trust one installed workflow or all workflows in one installed package. Desktop presents the package and each included workflow explicitly before applying the selected grants. Noninteractive installation never grants trust.

## 15. CLI surface

The workflow CLI adds:

```text
hermes workflow source add <name> <git-url> [--ref <ref>]
hermes workflow source list
hermes workflow source refresh [name]
hermes workflow source remove <name>

hermes workflow search [query] [--source <name>]
hermes workflow inspect <source/package>
hermes workflow install <source/package-or-git-url> [--ref <ref>] [--path <path>]
hermes workflow installed
hermes workflow check [source/package]
hermes workflow update [source/package]
hermes workflow uninstall <source/package>

hermes workflow trust <installed-package-or-workflow>
hermes workflow untrust <installed-package-or-workflow>
```

Exact option spelling will be locked by CLI tests before implementation. Existing command meanings remain backward compatible. Human-readable output is default. Read and mutation commands that participate in automation provide bounded stable JSON output and stable diagnostic codes.

Interactive commands show review content before confirmation. Noninteractive mutations require explicit confirmation inputs and never weaken trust separation.

## 16. Backend API

Desktop uses authenticated, profile-scoped routes under:

```text
/api/plugins/workflow/marketplace/...
```

The route groups cover:

- sources and refresh status;
- package search and detail;
- installed packages and provenance;
- prepare/confirm installation;
- update checks and prepare/confirm update;
- prepare/confirm removal;
- operation status and cancellation; and
- trust review, grant, and revocation.

Read operations require workflow read authority. Source mutations, install, update, removal, and trust require administrative authority. Responses use strict schemas, bounded collections, sanitized repository identities, and stable error codes.

Git-bound work runs as background operations with structured status, progress phases, cancellation, and terminal results. Cancellation is cooperative and cannot interrupt an atomic replacement halfway through. Operation identity, cache keys, and every Desktop query key include both backend connection and profile.

A Desktop connected to a remote co-worker calls that remote backend's marketplace API. The Electron process and local renderer do not silently perform a local installation for a remote target.

Older backends continue serving current workflow catalog/run routes. A newer Desktop feature-detects marketplace support and hides or disables unsupported actions with upgrade guidance.

## 17. Desktop experience

The Workflows area becomes:

```text
[ Installed ] [ Marketplace ] [ Active ] [ History ] [ Archive ]
```

Marketplace uses a responsive list/detail layout:

```text
+---------------------------------------------------------------------+
| Search packages...   Source: All v   Refresh   Manage Sources       |
+------------------------------+--------------------------------------+
| Laptop Support       v1.1.0 | Laptop Support                       |
| example/laptop-support      | Diagnostics and support workflows    |
| 3 workflows - compatible   |                                      |
|                            | Workflows 3  Scripts 2  Commands 1    |
| Inbox Productivity   v2.0  | Requirements: uv                     |
| example/inbox              | Source: company-workflows            |
| Update available           |                     [Install package]|
+------------------------------+--------------------------------------+
```

Package detail shows publisher, source, version, tags, license, workflows, commands, scripts, MCP definitions, other resources, external requirements, compatibility, installed/update status, and sanitized provenance.

The install flow is:

1. **Install package** starts preparation with visible progress and cancellation.
2. **Review installation** shows exact identity, changed files, compatibility, advisories, and risk.
3. **Confirm install** performs the atomic installation.
4. Success clearly states **Installed - trust required to run**.
5. **Review trust** opens a distinct trust screen covering the package and each workflow.
6. Trust confirmation applies only to the reviewed installed bytes.

Installed extends the current workflow catalog, grouping workflows by marketplace package when provenance exists. It shows package version, source, trust, compatibility, and update state. Loose project/profile workflows remain visible without fabricated package provenance.

Manage Sources supports add, edit, enable/disable, refresh, and remove. It shows last verified refresh, resolved commit, cache freshness, and sanitized failures. Authentication failures provide setup guidance and Retry, never credential entry.

Renderer state follows backend truth. Operations survive navigation, use generation guards, reconcile on profile/connection changes, and do not announce success until the backend reports the committed transaction.

## 18. Compatibility and migration

- Loose explicit, project, and profile workflows retain current discovery and precedence.
- Existing workflow validation, doctor, run, scheduling, and trust commands remain available.
- Current showcase and verified bundled distributions remain separate and retain their verification rules.
- Ericsson build-time vendoring and profile seeding remain unchanged.
- Marketplace package discovery is additive and manifest-aware.
- Older Desktop clients continue using existing endpoints.
- Existing trust-store data remains readable and migrates without broadening trust.
- A publisher may adopt loose workflows into a package deliberately, but Hermes does not move or rewrite them automatically.
- Removing a source does not implicitly uninstall its packages; the UI explains orphaned-source update implications.

## 19. Failure model and diagnostics

Diagnostics are stable, redacted, and grouped as:

- source/Git/authentication;
- contract/manifest/index;
- path/resource limits;
- workflow structure and resource resolution;
- digest/integrity;
- compatibility advisories;
- risk/trust;
- transaction/concurrency; and
- backend/profile authority.

Structural and integrity failures block prepare or confirm. Destination capability findings remain advisories unless the canonical workflow contract already classifies them as structural. Failures always identify whether the current installed package remains usable and whether recovery is automatic or requires an operator action.

No diagnostic includes a credential-bearing URL, token, authorization header, private key body, secret value, or raw environment dump.

## 20. Testing strategy

Implementation is test-driven and covers the real boundaries, not only mocks.

### 20.1 Contract conformance

- canonical artifact schema and self-validation;
- every positive and negative shared vector;
- deterministic output across ordering and platforms;
- exact Studio copy/check compatibility;
- path normalization, Unicode, binary bytes, and line endings;
- file counts and byte limits; and
- unsupported-version fail-closed behavior.

### 20.2 Git and sources

- local fixture remotes representing public HTTPS, private HTTPS, and SSH behavior;
- credential-helper, SSH-agent, `gh`, and supported token resolution seams;
- branch, tag, exact commit, default branch, and subdirectory selection;
- GitHub-style browser/tree URL normalization;
- malformed refs and repository identity collisions;
- shallow/filter/sparse fallback behavior;
- timeout and cancellation; and
- credential redaction in process arguments, config, logs, persistence, and API errors.

### 20.3 Package security and recovery

- multiple packages per repository and multiple workflows per package;
- missing, stale, malformed, malicious, or oversized indexes;
- traversal, absolute paths, NULs, backslashes, case collisions, nested roots, and symlinks;
- invalid workflow definitions and missing package resources;
- MCP/fixture/companion YAML excluded from workflow discovery;
- publisher digest mismatch and independently recomputed digests;
- repository or destination mutation between prepare and confirm;
- expired, replayed, cross-user, cross-profile, and changed-candidate confirmation tokens;
- interrupted install/update/remove and atomic rollback;
- concurrent operations and duplicate identities; and
- abandoned transaction recovery without deleting unrelated paths.

### 20.4 Trust

- install never trusts;
- noninteractive install never trusts;
- risk review covers every executable/resource surface;
- any distribution byte change invalidates marketplace grants;
- per-workflow risks remain distinct inside a multi-workflow package;
- uninstall removes only its origin-aware grants;
- independent manual or identical-install grants remain valid; and
- legacy trust-store migration never broadens authorization.

### 20.5 CLI, API, and Desktop

- stable human and JSON CLI output;
- authenticated read/admin API boundaries;
- background progress, cancellation, and terminal results;
- local, remote, cloud, and switched profile routing;
- query and operation scope includes connection plus profile;
- Desktop loading, empty, partial, stale-cache, auth-failure, install, update, trust, rollback, and unsupported-backend states;
- focus return, keyboard access, screen-reader names, and responsive layout; and
- no optimistic success before backend commitment.

### 20.6 Regression

- existing loose discovery and precedence;
- existing workflow compilation, trust, admission, execution, and scheduling;
- current workflow Desktop catalog and run views;
- existing plugin Git installer and skills hub;
- verified showcase distribution; and
- Ericsson bundled deployment.

## 21. Documentation

Hermes documentation will add:

- repository and multi-package layout;
- manifest, digest, and marketplace-index references;
- the publisher handoff from Workflow Studio through user-managed Git push;
- public and private source setup;
- CLI and Desktop search/install/update/remove instructions;
- Git credential-helper, SSH, and `gh` authentication examples;
- installation-versus-trust explanation;
- compatibility and external requirement semantics;
- update diff and trust-renewal behavior;
- troubleshooting, cache, rollback, and recovery guidance; and
- deliberate migration guidance for loose workflows.

Workflow Studio documentation will consume the approved canonical contract and describe creation, editing, validation, preparation, local Git versioning, and user-managed push. It will not claim to install, trust, or execute packages.

## 22. Delivery order

Hermes is completed first on the isolated `feat/workflow-package-marketplace` worktree. The implementation must publish and verify the canonical contract before Workflow Studio consumes it. The Hermes branch is reviewed and explicitly approved before merge to `base`.

After the Hermes commit containing the canonical artifacts is approved, Workflow Studio pins and copies those exact artifacts and proceeds with its separate approved implementation plan. No Workflow Studio product code changes are part of this Hermes branch.
