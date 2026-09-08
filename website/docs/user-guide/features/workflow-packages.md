---
sidebar_position: 26
title: Workflow packages
description: Publish, install, review, trust, update, and recover Git-distributed workflows.
---

# Workflow packages

Marketplace distributes workflow YAML and companion files with their commands,
scripts, MCP definitions, and other resources. Installation verifies bytes; it
does **not** execute package content or grant trust. A publisher's digest is a
claim Hermes recomputes, not an authorization to run code.

## Choose the backend and profile first

In Desktop, select the connection and profile before opening **Workflows →
Marketplace**. Sources, Git authentication, installed bytes, provenance, trust,
and recovery journals belong to that backend/profile. A remote connection uses
the remote machine's Git setup, not the laptop's credentials or filesystem.
Changing the selection does not transfer an in-flight action to the new profile.
The **Installed** tab remains useful when a source is removed or unavailable.

CLI examples below target the `support` profile on the machine running Hermes:

```bash
hermes --profile support plugins enable workflow
hermes --profile support workflow source add company https://git.example.test/team/workflows.git --ref main
hermes --profile support workflow source refresh company
hermes --profile support workflow search laptop --source company
hermes --profile support workflow inspect company/laptop-support --json
```

Configure private-repository access on that same backend using its existing Git
credential helper, SSH agent, or supported Git authentication environment. Test
that setup with your organization's normal Git tooling. Hermes fetches
noninteractively and does not store another copy of credentials. Never embed a
password or access token in a source URL, package, index, screenshot, or bug
report. A source authentication failure is not permission to install stale bytes.

## Review, install, then decide whether to trust

Select a package, inspect its workflows, resources, external requirements, and
compatibility advisories, then choose **Install package**. The review identifies
the exact resolved Git commit and distribution digest. Only **Confirm install**
applies that review. Closing the review is not confirmation.

```bash
hermes --profile support workflow install company/laptop-support
hermes --profile support workflow installed --json
hermes --profile support workflow package-state company/laptop-support --json
hermes --profile support workflow trust company/laptop-support --installed-package --workflow diagnostic
hermes --profile support workflow trust company/laptop-support --installed-package
```

Interactive mutations ask for confirmation. For automation, `--prepare-only`
prints an exact review and an opaque confirmation token; a subsequent invocation
with `--confirmation-token` confirms that review. Treat this token as sensitive
and short-lived; do not place it in shared logs or shell history. `--yes` prepares
and confirms noninteractively: use it only when that explicit authorization is
intended. Install, update, uninstall, and installed-package trust support these
confirmation modes.

Trust review is separate from installation. Select one workflow or all reviewed
workflows and read the risks before granting trust. The selected grant result
and current full-package trust map are separate facts: granting one does not
grant its neighbors. Untrusted execution without a suitable configured isolation
backend is refused with `workflow_trust_required`; trust does not bypass runtime,
credential, approval, or hardline requirements.

```bash
hermes --profile support workflow untrust company/laptop-support --installed-package --workflow diagnostic
```

Revocation removes that package-origin grant. An independent manual or other
package-origin grant can still make the exact workflow digest trusted. Inspect
the returned current trust state; do not infer it solely from the action label.

## Updates and removal

```bash
hermes --profile support workflow check company/laptop-support
hermes --profile support workflow update company/laptop-support
hermes --profile support workflow uninstall company/laptop-support
```

Check is read-only. Review changed versions, files, and risks before confirming an
update. A changed package-owned byte changes the distribution identity and
invalidates package-associated trust; review trust again. The package version
must advance for a changed distribution. An unchanged check is not an update.
Removing a source definition does not remove its installed packages. Removal is
local and still available from verified installed state when remote inspection
is unavailable. An already-running inspection does not reserve the mutation
slot; another lifecycle action does. A late inspection is historical metadata,
not evidence that a removed installation has returned.

## Progress, lost responses, and recovery

Closing a dialog or moving to another tab does not cancel backend work. Return
to the same backend/profile to observe it. Cancellation is a request: only a
terminal `cancelled_before_commit` outcome proves no commit occurred. Never
interpret a timeout, closed window, or missing response as successful cancellation.

Desktop retains previously observed metadata while reconciling, visibly marked
**Last observed**. Install/update/trust/run actions stay gated until their exact
current-state and projection requirements are satisfied. A failed refetch does
not make retained cache authoritative. Use **Refresh state** and resolve any
reported recovery problem before retrying a mutation.

For API integrations, lifecycle V2 binds requests to the exact connection,
native generation, profile, registry epoch, and opaque principal binding.
Preserve the original request ID, operation kind, subject, selection, and body
when replaying an uncertain POST; do not generate a replacement request as a
retry. Exact admission lookup/replay can recover the original operation without
applying the mutation twice. Confirmation tokens are retrieved separately and
never belong in operation history, query keys, or persisted renderer storage.

Unseen request IDs have a five-minute admission window (at most 30 seconds of
future clock skew). Accepted receipts are retained at least 24 hours and while
active; the per-profile capacity is 4096 across actors. Capacity rejects a new
admission rather than forgetting a protected receipt. Full terminal operation
results have a separate bounded retention policy (128 results, one hour); an
admission receipt can outlive its full result. Expired/evicted history requires
current-state reconciliation, not a guessed success or an automatic new mutation.

A renderer restart can reconnect to the still-running backend registry using
its public identities. A backend restart rotates the registry epoch and loses
in-memory operation/receipt history. Durable provenance, trust, and transaction
journals remain the authority; an old request cannot be replayed into a new epoch.

Run recovery on the affected backend and profile:

```bash
hermes --profile support workflow package-state company/laptop-support --json
hermes --profile support workflow recover-packages
hermes --profile support workflow recover-packages --yes --json
```

`package-state` is a locked, read-only observation. `recover-packages` is an
explicit **profile-wide** recovery action, not just recovery of the package last
viewed. It checks owned journals/staging, refuses an active writer or live review
that could change the recovery scope, and revalidates the scope before acting.
Complete outstanding reviews or let them expire and inspect again. Preserve
ambiguous artifacts for operator investigation; do not delete journals or choose
a version by inspecting directory names. Doctor reports diagnostics and
compatibility; it does not perform marketplace transaction recovery.

Only verified rollback evidence supports “version remains installed.” A failed
rollback or ambiguous recovery leaves package state **unconfirmed** with no
certified installed version or trust map, even if candidate files are visible on
disk. A historical successful install/update is not proof of current state after
a later mutation. After recovery, refresh locked state again.

## Publisher checklist

Use the versioned Hermes authoring contract, not an independently maintained
schema. Canonical artifacts live in `plugins/workflow/contracts/`:
`workflow-package-v1.json` and `workflow-package-v1-vectors.json`. Validate with
the same contract version consumers support. A typical repository has
`packages/laptop-support/workflow-package.json`, `workflows/diagnostic.yaml`,
optional companions/resources, `digests.json`, and a repository-level
`.well-known/hermes-workflows/index.json`.

The manifest declares `schemaVersion`, stable `id`, semantic `version`,
`displayName`, `description`, `license`, `publisher`, `tags`, and `workflows`
members with a `definition` and optional `companion`. `externalRequirements`
names tools, runtimes, providers, services, and required secrets—never values.
Do not include installed paths, trust flags, credentials, or editor state.

Generate `digests.json` from exact bytes of **all** regular files below the
package root except the root-level `digests.json` itself. Include nested resources
also named `digests.json`, unreferenced resources, and the manifest. Paths must
satisfy the contract's portable path, collision, symlink,
and size limits. Sort records by normalized relative path; each contains `path`,
byte `size`, and lowercase `sha256`. The document also contains
`contractVersion: 1`, `algorithm: "sha256"`, and `packageDigest`.

The distribution composite is SHA-256 over the domain bytes
`hermes.workflow-package.v1\0`, followed for each sorted file by its UTF-8 path
length (unsigned 8-byte big-endian), path bytes, content length (same encoding),
and raw SHA-256 content digest. Do not normalize line endings or reserialize YAML
before hashing. The canonical vectors are the cross-language acceptance tests;
Hermes' `scan_package_files` and `compute_distribution_digest` implement this
contract. The contract generator checks authoring artifacts; it is not a package
publishing CLI.

Generate the repository index as `{"schemaVersion":1,"packages":[...]}`, sorted
by package ID. Each entry repeats manifest discovery metadata and adds
`contractVersion: 1`, repository-relative `packagePath`, and `packageDigest`.
IDs are unique. No timestamp, self-referential commit SHA, local path, or trusted
state belongs in the index. Validate the complete checkout with Hermes'
`load_repository_index` before publishing; it verifies indexed distributions,
not just JSON syntax.

Commit the manifest, workflow/resource bytes, digests, and index together. Any
included byte change requires regenerating both digest and index and advancing
the package version. Push using your own reviewed Git workflow and credentials;
Hermes Marketplace and Workflow Studio do not push a publication for you.
Consumers resolve the selected ref to an exact commit and independently verify
the downloaded bytes before installation.
