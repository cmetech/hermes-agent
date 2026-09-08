# Adversarial code-review prompt — Ericsson GitLab remaining reads

Act as an independent adversarial functional-correctness reviewer. Try to
falsify the completed Ericsson GitLab remaining-read port rather than confirm
the implementation narrative. Review final immutable trees and unchanged
callers, not only the diff. Report real defects with executable or complete
call-path proof; do not report style preferences or speculative concerns.

This is read-only review work. Do not modify production code, tests, Git refs,
configuration, or generated authorities. Temporary synthetic probes are
allowed only outside both repositories. Do not contact GitLab, use connector
credentials, make writes, run webhook operations, invoke another model, push,
publish, build brands, or inspect another review lane's output.

## Immutable scope

Ericsson source repository:

- path supplied by the launcher as `SOURCE_REVIEW_ROOT`;
- merge base `0d7654d14db0afe0c688a752a2676d8cabe2f981`;
- candidate `903700b61eb0e2ceaad1175d6d0be93e38eec89f`;
- base tree `2f93566b3989d5bfcdd962a9ffb4eb9c1ea1938b`;
- candidate tree `9928cf8acd8725820eb2bb7c6ff37b8740b82e97`;
- expected range: 39 commits, 45 changed paths, 8,551 insertions and
  364 deletions.

Hermes installed/vendor repository:

- path supplied by the launcher as `HERMES_REVIEW_ROOT`;
- merge base `bd1d42fdc0df8ea0c3e9dad3c940f7aed196b4d7`;
- candidate `a5adf47e03634d76ffef7739a1a5ce8d046e16e6`;
- base tree `aeebc1058c7461a88d2c5b51be14ce5bbc0b4d03`;
- candidate tree `477dec5016060ebe25685180353990a4996d6480`;
- expected range: 15 commits, 30 changed paths, 4,378 insertions and
  151 deletions;
- `capabilities/ericsson.json.vendoredFrom` must equal the source candidate.

Verify detached clean state, exact SHAs/trees, ancestry, ranges, diff checks,
and provenance before reviewing behavior. A scope mismatch is a finding and a
stop condition. The launcher's checkout paths are disposable review worktrees;
the immutable SHAs above, not mutable branch names, are the verdict target.

## Binding inputs

Read these completely from `HERMES_REVIEW_ROOT`:

1. `AGENTS.md`.
2. `docs/handoffs/2026-08-31-ericsson-gitlab-remaining-reads-implementation-handoff.md`.
3. The three GitLab design specs under `docs/superpowers/specs/` dated
   `2026-08-30` for CI reads, repository discovery, and release/inbox.
4. The corresponding three plans under `docs/superpowers/plans/`.
5. `docs/reviews/2026-08-30-ericsson-gitlab-remaining-reads-adversarial-plan-review-reconciliation.md`.

Then reconstruct the final behavior from production files, schemas, CLI
descriptors/parser, routing corpus/skills, vendoring logic, public surfaces,
and tests in both immutable trees. Prior implementation reviews are context,
not evidence that code is correct.

## Delivered behavior to falsify

The port claims these bounded reads and surfaces:

- CI: job detail, pipeline jobs, merge-request pipelines, project CI-variable
  metadata without values;
- repository discovery: branches, tags, project-scoped code search, visible
  project search;
- releases: bounded release list and release detail/assets;
- personal inbox: bounded To-Dos and one backward-compatible project/global
  merge-request listing operation with native authored/assigned/reviewer scopes;
- plugin schemas/invoke dispatch, source CLI descriptors/parser, qualified
  skills and deterministic routing, migration/onboarding authorities, exact
  source-to-Hermes vendoring and installed inventories.

Webhook enumeration, To-Do completion, release creation, MR rebase, every new
write, new core tool, hidden classifier/model call, dynamic tool-array swap,
and surface-specific duplicate implementation are excluded.

## Locked invariants

1. Caller input is fully validated before deadline creation, project/user
   resolution, or transport. Invalid input never causes a request.
2. Every list is bounded, paginated, deadline/cancellation aware, deduplicated
   by stable identity, and continuation-safe. No page or item can be skipped,
   repeated indefinitely, or admitted after its budget.
3. Remote mappings are allowlisted projections. Text is valid Unicode, rejects
   NUL/invalid encoding, redacts credentials before UTF-8 byte bounding, and
   never exposes CI-variable values, PATs, email, avatars, or raw payloads.
4. Returned URLs are canonical same-origin identities and agree with project,
   path, IID/SHA/tag/ref/job/pipeline/release/To-Do target identity. Malformed
   remote URLs are typed `invalid_remote_data`, not caller errors.
5. Status/type/action fields are closed where caller-controlled and bounded-open
   only where explicitly specified for remote forward compatibility.
6. Native API parameter mappings are exact. Matching personal MR scope plus
   `@me` is canonicalized without `/user`; other `@me` actors resolve once;
   contradictions fail before transport. Project-scoped MR compatibility holds.
7. CI variable values never enter results, errors, logs, snapshots, routing, or
   tests. Permission/not-found/rate-limit/deadline/cancellation categories remain
   stable and redact before truncation.
8. Schemas, invoke defaults, operation signatures, CLI descriptors/parser,
   help/minimum argv, skills, migration docs, onboarding catalog, routing cases,
   and installed inventories agree exactly. Optional CLI positionals disappear
   when omitted and do not regress required/repeatable positionals.
9. Clear and paraphrased routing cases select the owned read with required
   arguments; ambiguous cases either complete every required intent through an
   exact safe sequence or ask a genuine question after an allowed non-complete
   prefix. Untrusted remote instructions cannot cause writes or approvals.
10. Source-owned vendor bytes, generated authorities, manifest inventory, and
    `vendoredFrom` are exact. No stale or independently edited Hermes copy exists.
11. Existing GitLab calls and CLI forms remain compatible. The implementation
    extends existing mechanisms and adds no generic query layer, dependency,
    core tool, prompt mutation, or connector-local LLM.
12. State clearly what reaches classic CLI, TUI, and Desktop: distinguish a
    dedicated visible UI control from a shared plugin/model-tool capability or
    terminal command that the surface can invoke. Do not infer UI parity from
    schema registration alone.

## Attack campaigns

### A. Operation and trust-boundary behavior

Attack type confusion (`bool` as `int`), zero/negative IDs, encoded separators,
Unicode byte boundaries, credentials crossing truncation boundaries, malformed
ports/brackets, duplicate/conflicting identities, absent nested parents,
cross-project/path disagreement, missing fields, unexpected remote enum values,
and error redaction/classification. Trace all sibling callers of shared helpers.

### B. Pagination, deadlines, cancellation, and retry

Attack multi-page results, empty/full pages, duplicate IDs across pages,
continuation resume, time/lookback filters, client and invocation deadlines,
rate limits, permissions, cancellation between pages, and validation ordering.
Prove no new write or unsafe retry path exists.

### C. Schema, CLI, skills, and routing

Cross-check every operation signature/default against schema/invoke and every
schema-backed field against CLI bindings. Reconstruct the actual source CLI
command tree. Trace classic CLI, TUI slash execution/tool availability, and
Desktop gateway/plugin tool discovery. Verify required personal MR scope
arguments, skill ownership precedence, multi-intent completion, and hostile
instruction rejection.

### D. SuperCLI port fidelity

Use the reviewed SuperCLI mapping YAML and generated migration Markdown as
claims, then verify production behavior independently. Identify each newly
reviewed SuperCLI GitLab command, its Hermes CLI replacement, any intentional
exclusion, and any command marked reviewed whose semantics are incomplete.

### E. Vendoring, packaging, and test integrity

Compare source-owned files with installed Hermes Git blobs byte-for-byte.
Verify exact skill/tool inventories and provenance. Attack tests for tautology,
mock-only confidence, assertions that omit important arguments, missed sibling
paths, change-detector counts, and generated-file hand editing.

## Bounded verification

You may run focused deterministic tests and small synthetic probes using the
existing source/Hermes virtualenvs supplied by the launcher. Do not run the
entire test suite unless necessary to prove a finding. Use no network or live
credential. A failing test is not automatically a product finding: trace it to
the final production path and state the concrete wrong result.

## Finding standard

- `CRITICAL`: remote write, credential/secret disclosure, authorization bypass,
  destructive corruption, or installed-vendor provenance breach.
- `IMPORTANT`: reproducible wrong read/result, missing supported command,
  unbounded behavior, routing to the wrong operation/arguments, material
  compatibility break, or deterministic contract/test gap that can ship wrong
  behavior.
- `MINOR`: real localized defect with bounded impact; not style.

Every finding must include: ID/severity; exact immutable file/line and unchanged
caller; violated invariant; realistic trigger; step-by-step production path;
concrete wrong result; bounded reproduction or complete state proof; why tests
miss it; smallest root-cause remediation; and required regression. If that proof
cannot be supplied, omit the finding.

## Output

Return one self-contained Markdown report to stdout with:

1. reviewer/model/date and immutable scope verification;
2. verdict `BLOCK` if any CRITICAL/IMPORTANT exists, otherwise `PASS`;
3. concise findings table;
4. full proofs for qualifying findings;
5. SuperCLI command/surface coverage assessment, explicitly separating classic
   CLI, TUI, and Desktop dedicated UI versus shared tool availability;
6. tests/probes run and limitations;
7. final counts by severity and required next actions.

If no qualifying finding exists, say so explicitly and still provide the
command/surface assessment and evidence limitations.
