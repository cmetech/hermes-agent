# Workflow Showcase Desktop Run Design

**Date:** 2026-07-20

**Release:** v3.0.2, paired OTTO and LOOP24 release

**Branch:** `feat/workflow-showcase-desktop-run` from `origin/base`

**Target branch:** `base`

**Status:** Proposed; implementation is blocked on maintainer approval

## Reader and outcome

This design is for the engineer implementing and reviewing v3.0.2. After
reading it, they should be able to add bundled showcases to the Desktop
Workflows catalog and admit a verified showcase through the normal background
REST path without weakening digest verification, authentication, redaction,
idempotency, or coordinator ownership.

## Grounded baseline

The v3.0.1 catalog feature is present on current `origin/base` through merge
commit `67474ae11` and release commit `8eb084137`. GitHub has no pull-request
object for the feature branch, but the merge commit, all nine feature commits,
the catalog API, and the final adversarial review are ancestors of
`origin/base`.

The unmodified v3.0.1 merge gate passes at `8eb084137` with:

- 745 Python tests passed and 1 skipped;
- 1 installed-distribution integration test passed;
- 51 Desktop merge-gate tests passed across 9 files;
- renderer TypeScript type-check exit 0.

The v3.0.1 review found no Critical, High, or Important issue. This design
preserves all of its verified contracts and closes only its recorded L-A and
L-B limitations. It also closes CF-1 for the newly combined catalog by taking
list descriptions from the existing complete redacted definition projection.

## Scope

### In scope

1. Add digest-verified bundled showcases to the authenticated workflow list
   and detail projections.
2. Give every visible catalog row an unambiguous `(source, name)` identity.
3. Admit a verified bundled showcase through the existing authenticated,
   idempotent, coordinator-gated, background-only `POST /runs` path.
4. Ship a no-input approval showcase that can pause in Attention and complete
   after approval.
5. Show honest Bundled/Verified bundle UI labels and keep legacy-input
   `laptop-diagnostic` disabled with a CLI instruction.
6. Preserve read-only catalog/detail behavior, shared redaction, CLI showcase
   behavior, and the foreground showcase tour.

### Out of scope

- No raw scan of `plugins/workflow/showcases`.
- No import/copy/install operation for showcases.
- No new trust-store record or trust action for bundled content.
- No broadening of Desktop input support beyond the existing flat v1 types.
- No inline showcase execution from HTTP and no call to
  `RunScheduler.advance` from admission.
- No caller-controlled trigger provenance.
- No store schema change, migration, core model tool, configuration key, or
  `HERMES_*` environment variable.
- No change to generic plugin host files.
- No release, tag, push, merge, or publish action.

## Approaches considered

### A. Explicit `(catalog_source, name)` targeting — recommended

The list returns the existing `source` plus `name`. Detail accepts an optional
`catalog_source` query parameter, and `POST /runs` accepts an optional
`catalog_source` request field. Desktop always sends the row's source. Legacy
callers may omit it and retain the current user-catalog precedence behavior;
omission never opts a caller into bundled-showcase resolution.

Advantages:

- collisions are visible and targetable without inventing a selector grammar;
- `catalog_source` cannot be confused with server-derived `trigger_source`;
- old request bodies and old idempotency digests remain unchanged;
- Pydantic and TypeScript can express the source vocabulary directly.

Cost: one additive field must be carried through the detail query, query cache,
review dialog, and admission call.

### B. Opaque selector such as `showcase:<id>`

The list could return a new selector and reuse the current name-shaped route
and request field. This is compact, but workflow names currently allow `:`, so
the selector can collide with a valid existing name. Escaping/versioning the
grammar adds complexity and still hides source semantics from API consumers.

### C. Name-only precedence with showcases last

Assign showcase precedence 3 and let project/profile rows hide a colliding
showcase. This is the smallest code change but fails the product requirement:
the bundled row disappears, View/Run cannot target it, and copied/tampered
content is difficult to distinguish from the authenticated distribution.

**Decision:** use approach A.

## Decision 1: catalog keying, precedence, and collisions

### Public identity

Every valid visible row is keyed by:

```text
(source, name)
```

where `source` is `project`, `profile`, or `showcase`. The server and Desktop
call the request field `catalog_source`/`catalogSource`; they do not call it
`source` in admission because provenance source remains server-owned.

For the showcase source, the public `name` is the digest-authenticated scenario
ID from `catalog.yaml`. This matters for `scheduling`, whose internal workflow
definition is named `scheduled-check`: View and Run resolve the authenticated
scenario ID, while the redacted definition continues to show the workflow's
own name. Using the scenario ID keeps catalog, detail, CLI terminology, and
the rootless verified-loader key identical.

### Precedence

The existing user-workflow resolution policy remains unchanged:

```text
explicit (CLI only) > project > profile
```

The Desktop catalog continues to collapse a project/profile name collision to
the existing winner. Verified showcases are a separate source set with display
precedence 3; they do not participate in user-workflow shadowing. Therefore a
project workflow and a showcase with the same definition name both appear.

Rows are returned in a deterministic `(name, precedence, source)` order. A
successful verified showcase load reserves its bounded number of rows inside
the 500-row response before user rows are truncated. This makes the shipped
showcases visible even in a full user catalog while preserving a truthful
`truncated=true` result. If showcase verification fails, no slots are reserved
and no showcase row is emitted.

### Detail and run targeting

- `GET /workflows/{name}` with no `catalog_source` preserves v3.0.1
  project/profile name-precedence resolution and does not select a showcase.
- `GET /workflows/{name}?catalog_source=project|profile|showcase` resolves only
  the selected visible source.
- `POST /runs` accepts additive optional `catalog_source` with the same
  vocabulary. Desktop always supplies it from the selected row. An omitted
  source preserves the existing project/profile-only admission path.
- A colliding user row and showcase row therefore have different query keys,
  different detail requests, and different admission targets.

The profile half of a project/profile collision stays hidden and cannot be
selected merely by forging `catalog_source=profile`; explicit source selection
filters the already precedence-resolved user catalog.

## Decision 2: admission from a verified bundle

### Reusable verified record

The showcase module will expose a narrow immutable record:

```python
@dataclass(frozen=True, slots=True)
class VerifiedShowcasePackage:
    scenario: ShowcaseScenario
    package: WorkflowPackage
    risk: WorkflowRiskSummary
    bundle_digest: str
```

Admission never accepts this record, a `ShowcaseScenario`, or a bundle path
from its caller. The showcase module exposes
`load_verified_showcase_package(showcase_id, ...)` and
`load_verified_showcase_packages(...)`; neither accepts a filesystem root.
They construct records only through the existing showcase verification path:

1. `load_showcase_catalog()` reads the authenticated distribution resource
   with request-time repair disabled.
2. It verifies `catalog.yaml` against `digests.json`.
3. It validates paths, symlinks, package safety, scenario limits, and package
   tree digests.
4. The selected scenario must have `verified_bundled_provenance=True`.
5. An internal `_scenario_package()` call loads the selected workflow from the
   same verified materialized root, projected as `source="showcase"`,
   `precedence=3` for catalog use.
6. `_verified_distribution_risk()` rechecks the package tree after catalog
   verification, assesses ordinary compatibility, builds the normal risk
   summary, and runs `preflight_execution(..., trusted=True)`. Catalog loading
   retains an incompatible scenario for honest projection; CLI execution and
   API admission additionally require `compatibility.runnable`.
7. The record captures the verified bundle digest and cannot carry an
   arbitrary filesystem root.

An explicit copied catalog still receives
`verified_bundled_provenance=False`, and the existing
`_verified_distribution_risk()` guard continues to refuse it. The public
verified loaders cannot be pointed at that copy at all. The copy may still be
treated as an ordinary user workflow if separately discovered and trusted,
but it can never claim showcase identity.

### Bounded authenticated reads

The current showcase loader is safe for its CLI purpose but reads package
trees without an explicit aggregate budget and may repair an authenticated
source checkout after a catalog mismatch. Exposing it to a repeatable HTTP
read route must not regress v3.0.1's bounded-work or read-only contracts. The
existing loader therefore gains an optional `WorkflowResourceReadBudget` and
an `allow_repair` switch. Catalog/detail/admission supply fixed per-file,
file-count, and aggregate limits and set `allow_repair=False`; they fail closed
without writing the checkout. CLI callers retain the current defaults and
repair behavior.

The budget is threaded through metadata reads, package safety reads,
`_tree_digest`, `_bundle_digest`, and distribution-risk construction. Cached
bytes avoid double charging within one verification pass. For the unchanged
CLI repair path, successful managed-checkout repair restarts verification with
a fresh budget (when one was supplied) so stale cached bytes cannot
authenticate the repaired tree.

Catalog list charges the verified-loader budget's actual `bytes_read` against
the existing `CATALOG_MAX_RESOURCE_REQUEST_BYTES` aggregate before projecting
user packages. Showcases therefore do not create a second unaccounted request
budget.

### Process-lifetime verification cache

The list/detail hot path memoizes successful verified-bundle records for the
process lifetime. The cache is keyed first by `_bundle_digest()` (the digest of
the authenticated catalog and digest manifest) and stores a bounded tree
signature for every expected package file: relative path, file type,
device/inode where available, size, `mtime_ns`, and `ctime_ns`. A cache hit must
match both the bundle digest and the complete current signature. Missing,
added, replaced, resized, retimed, or symlinked content invalidates the entry
and runs the full digest/safety verification again. Platforms unable to supply
the full signature disable the fast path and reverify.

The signature is only an invalidation mechanism; it never establishes trust.
The first load and every invalidated load still use the existing SHA-256
catalog/package verification. Metadata reads and signature enumeration remain
bounded. A lock coalesces concurrent cache misses so two tab refetches do not
digest the bundle simultaneously. Failures are not cached.

Admission bypasses the memoized fast path and performs a fresh full verified
load before sealing the executable-resource cache. This keeps the execution
boundary independent of list latency optimization. Tests count full-tree
digest/read calls across repeated list requests, mutate a package after a cache
hit to prove invalidation and fail-closed omission, and prove showcase bytes do
not incorrectly consume the user-catalog aggregate on cache hits.

### Approved plan deviation: compatibility projection

The initial plan applied execution-strict `compatibility.runnable` rejection
while constructing every verified catalog record. That conflicts with the
established v3.0.1 user-workflow behavior: visibility is permissive and honest,
while execution is fail-closed. It would also let one optional environment
dependency, such as unavailable MCP support for `ai-extensions`, suppress the
entire otherwise-authentic bundle.

The corrected boundary is:

- bundle integrity remains atomic and strict: catalog digest, every package
  tree digest, path/symlink safety, and bundled provenance must all verify or
  no showcase is trusted/listed;
- per-scenario environment compatibility is projected honestly through the
  existing `qualify_workflow_catalog_package(package, compatibility=...)`
  path and never suppresses sibling scenarios;
- View remains available for incompatible scenarios;
- CLI execution and API admission retain strict compatibility and ordinary
  execution preflight before any run is persisted.

### Standard admission flow

`start_api_run` gains optional `catalog_source`. Its user-workflow branch is
kept byte-for-byte semantically equivalent. For `catalog_source="showcase"`:

1. Resolve by showcase ID through `load_verified_showcase_package`; any digest,
   safety, copied-bundle, or post-verification change failure maps to typed
   `workflow_showcase_verification_failed` and persists nothing.
2. Reuse the normal bounded executable-resource budget to compute
   `WorkflowPackageDigest` and `WorkflowRiskSummary`; require the two package
   digests to match and seal the cache.
3. Treat the package as trusted only because the verified record exists. Do
   not read or write a user trust grant for this decision.
4. Require ordinary compatibility and `preflight_execution` exactly as for a
   trusted user package.
5. Require healthy coordinator state before snapshot preparation.
6. Prepare the immutable snapshot from the sealed exact bytes and compare its
   definition digest with `risk.package_digest` before admission.
7. Call `store.start_run` with the same API idempotency namespace,
   `execution_mode="background"`, the server-derived provenance, and showcase
   metadata.
8. Return the existing 202 envelope. The coordinator wake/poll path owns all
   execution after the response.

No scheduler or foreground-tour helper is imported into `api_admission.py`.
`run_showcase` and `_advance_until_wait` remain the CLI tour path only.

### Persisted metadata

Showcase identity is distinct from trigger provenance. A Desktop showcase run
persists:

```json
{
  "trigger": "desktop",
  "provenance": {
    "source": "desktop",
    "assurance": "local_admin_claim"
  },
  "run_metadata": {
    "showcase_id": "approval-gate",
    "showcase_version": "1",
    "bundle_digest": "sha256-of-verified-bundle-metadata",
    "risk_digest": "sha256-of-ordinary-risk-summary",
    "showcase_provenance": "verified_bundled"
  },
  "execution_mode": "background"
}
```

OAuth Desktop sessions retain `verified_adapter`; local Desktop sessions retain
`local_admin_claim`. Remote bearer-token callers remain `trigger_source="api"`
even when they explicitly select a showcase.

## Decision 3: trust badge and Run semantics

The public trust-state vocabulary becomes:

```text
trusted | untrusted | verified_bundled
```

For showcases:

- source badge/text: **Bundled showcase**;
- trust badge: **Verified bundle**;
- no trust-store action or trust-store record is offered;
- Run is trust-eligible only when detail returns `verified_bundled` from the
  verified record.

For user workflows, Trusted and Untrusted behavior remains unchanged.

All three Desktop authorization points—catalog row, View dialog, and Review &
Run—use one pure trust predicate accepting `trusted` or `verified_bundled`.
They also require the server-derived `run_support.supported` field. They still
fail closed on unsupported inputs, incompatible detail, unhealthy coordinator,
missing enum choices, or failed detail fetch.

`run_support` is projected for every row/detail as:

```text
supported=true,  reason=supported
supported=false, reason=unsupported_inputs
supported=false, reason=showcase_cli_required
```

User-workflow support continues to mirror the existing flat-input contract.
For a verified showcase, the server derives background API eligibility from
authenticated scenario metadata: the scenario must be `guided`, offline,
non-AI, non-networked, and have supported flat/no inputs. Admission re-derives
and enforces the same policy; it never trusts the client field.

A forged admission for unsupported inputs returns typed nonretryable 422
`workflow_inputs_unsupported`. An AI- or architecture-only showcase returns
typed nonretryable 409 `workflow_showcase_cli_required`. Neither prepares a
snapshot or persists admission state.

This keeps `ai-extensions` CLI-only because AI remains the hard consent gate.
It also keeps `scheduling` CLI-only for an architectural reason: cron creation,
`schedule_at`, and exact-ID/nonce ownership live in the `run_showcase` CLI
wrapper, while its workflow package contains only the scheduled checkpoint.
Ordinary background `POST /runs` admission therefore cannot reproduce the
showcase's scheduling contract. `approval-gate` and `resilience` are Desktop-
runnable; `ai-extensions`, `scheduling`, and `laptop-diagnostic` remain
CLI-only.

`laptop-diagnostic` remains `verified_bundled` but has unsupported legacy
`file`/`text` inputs. View stays enabled; Run stays disabled with
`unsupported_inputs`. Its localized showcase-specific reason explicitly says
to run from the CLI. The new approval showcase is parameterless and meets the
scenario policy, so it is Run-enabled.

## Decision 4: new flat-input approval showcase

Add a new package rather than converting `laptop-diagnostic`.

Converting the existing package would weaken a showcase that demonstrates
immutable file input, parallel fan-in, artifact verification, and approval
rework. It would also risk the CLI tour, reports, fixtures, and already-verified
capability claims. A new package is additive and isolates the Desktop marquee
contract.

The new scenario is `approval-gate`, displayed as **Approval Gate Tour**. It is
offline, network-free, AI-free, and parameterless. The workflow has one
approval node with a deterministic, non-secret message. A single node is
intentional: it is executor-independent and portable, yet still demonstrates
the full durable lifecycle:

```text
ready -> paused/Attention -> approved -> succeeded/Completed
```

Its sidecar sets queue overlap and conservative worker/resource limits but has
no `delivery_defaults.inputs`. The scenario adds an honest
`operator-approval` capability claim. Showcase reporting marks that claim
passed only when durable approval evidence exists; while paused it reports the
claim as skipped/awaiting decision.

The package is registered in `catalog.yaml`; the top-level and per-scenario
bundle versions advance together to `2.1.0`, while unchanged package versions
remain `1`. Both catalog and package SHA-256 values are regenerated in
`digests.json`. Tests assert relationships—every catalog scenario has a digest,
every package verifies, the approval scenario is parameterless and contains an
approval gate—rather than freezing a scenario count.

## Decision 5: provenance and idempotency stability

`trigger_source` remains entirely server-derived through `_verified_operator`
and `ApiAdmissionAuthority`:

- local Desktop session token -> `desktop` + `local_admin_claim`;
- OAuth Desktop session -> `desktop` + `verified_adapter`;
- authenticated remote token -> `api` + `verified_adapter`.

`catalog_source` selects package bytes only. It is never copied into
`TriggerProvenance.source`, and forged headers/body fields still cannot choose
provenance.

Existing user admissions continue to construct the same
`RunAdmissionRequest`: no new run metadata, the same workflow/concurrency keys,
the same provenance semantic record, and the same idempotency namespace. The
existing golden start-digest fixtures must remain byte-identical.

Showcase admissions intentionally add verified showcase metadata to the start
digest. This distinguishes a bundled showcase from an identically named and
byte-identical trusted user copy without changing existing-source digests.

## Catalog and detail projection

Showcase packages use the same `qualify_workflow_catalog_package`,
`show_package`, `_complete_projection`, topology, compatibility, input
classification, and risk projection as user packages. There is no second
redactor or diagram generator. A small shared showcase-policy helper derives
`run_support` for both projection and admission so consent-sensitive scenarios
cannot drift between UI and server behavior.

List descriptions for all valid rows come from
`shown["definition"]["description"]`, not the summary description. This closes
CF-1 because `_complete_projection` already redacts absolute, Windows, and home
paths before `sanitize_projection` applies the generic output bound.

Secret defaults, node bodies, absolute paths, internal digests, and hostile
Mermaid labels retain the v3.0.1 redaction and sanitization behavior.

## Failure behavior

| Condition | List | Detail with `catalog_source=showcase` | Admission | Mutation |
|---|---|---|---|---|
| Valid authenticated bundle | verified rows | 200 detail | 202 | run only on POST |
| Catalog/package digest mismatch | omit all showcase rows | typed 409 | typed 409 | none |
| Copied explicit bundle | never used by list | typed 409 if injected/tested | typed 409 | none |
| Bundle read budget exhausted | omit showcases, `truncated=true` | typed retryable 503 | typed retryable 503 | none |
| Unsupported showcase inputs | visible | 200, unsupported reason | typed 422 | none |
| AI/architecture-only showcase | visible, CLI-only | 200, CLI-only reason | typed 409 | none |
| Unhealthy coordinator | visible | 200, unhealthy | typed retryable 503 | none |
| Missing read/write capability | 401/403 before discovery | 401/403 before discovery | 401/403 before resolution | none |

Catalog and detail keep `require("read")`; POST keeps `require("write")`.
Read tests byte-snapshot the workflow store and trust store before and after
valid, tampered, and snapshot-race requests.

## Desktop data flow

```text
GET /workflows
  -> row {source, name, trust_state, ...}
  -> View(row)
  -> query key [profile, source, name]
  -> GET /workflows/{name}?catalog_source={source}
  -> redacted Diagram | Definition
  -> Review & Run(row)
  -> POST /runs {workflow: name, catalog_source: source, ...}
  -> 202 created/existing
  -> Active board / Attention / History from existing run APIs
```

The row's captured profile and source travel together. A profile change or a
same-name row cannot reuse another source's cached detail. The existing modal
idempotency key, submit coalescing, retry behavior, focus restoration, and
post-admission profile activation remain unchanged.

## Schema, cache, and host impact

- `_STORE_SCHEMA_VERSION` remains 13. Showcase identity fits bounded
  `run_metadata`; no database column or migration is needed.
- No generic host file gains a workflow import.
- No prompt, model tool, context, or conversation behavior changes.
- No new environment variable or user setting is introduced.
- Existing CLI showcase commands and foreground advancement retain their
  signatures and default behavior.

## Known and accepted constraints

- Showcase runs use `concurrency_key=f"showcase:{scenario.id}"`. A deliberately
  authored user sidecar can choose the same string and contend on that lane.
  This creates contention only, not identity or execution confusion, and is
  accepted for v3.0.2.
- After v3.0.2, two of five showcases run from Desktop. `ai-extensions` awaits
  a reviewed AI-consent/architecture pass; `scheduling` awaits a background
  schedule-creation design; `laptop-diagnostic` awaits rich file/text inputs.

## Verification strategy

Strict TDD applies to every behavior change: write one focused failing test,
run it through the repository's prescribed command, confirm the expected
failure, implement the smallest passing change, then refactor under green.

Coverage includes:

1. digest/catalog/package safety, copied-bundle rejection, bounded reads, and
   unchanged CLI list/preflight/run/report behavior;
2. list/detail auth, `(source, name)` collisions, total row bounds, CF-1
   description redaction, shared definition/topology projection, and read-only
   byte snapshots;
3. admission trust and run-support branching (including AI and architectural
   scheduling refusal),
   sealed snapshot bytes, typed failures, unchanged golden idempotency digests,
   202/background/coordinator behavior, and no scheduler reachability;
4. Desktop transport/query identity, badges, disabled reasons, Review & Run
   request shape, cache isolation, accessibility, focus, and all four locales;
5. a real-middleware catalog -> detail -> POST flow for `approval-gate`, with
   202, verified-bundled metadata, server-derived Desktop provenance, ready
   nodes at response time, and board visibility;
6. a real Desktop UAT through View diagram -> Review & Run -> Attention ->
   Approve -> Completed;
7. both brand gates, installed-distribution verification, broader workflow UI,
   native three-OS matrix membership, type-check, lint, customization checker,
   and exact baseline/delta reconciliation;
8. a fresh adversarial review with zero Critical or High findings.

## Stop conditions

Implementation stops and returns to the maintainer if any of these occurs:

- admission cannot consume a package produced by the verified showcase path
  without bypassing or duplicating digest verification;
- the optional loader budget changes the authenticated bytes or weakens CLI
  repair/fail-closed behavior;
- source targeting changes an existing idempotency digest;
- a request handler or showcase-admission helper can reach scheduler advance;
- a store migration appears necessary;
- a design/code contradiction requires a different API, trust, provenance, or
  package format than approved here.
