# Agent Handoff Stage 4 Implementation Readiness

**Date:** 2026-09-03

**Scope:** standalone GitLab+ICM handoff plugin and the smallest concrete core channel-registration seam

**Authority:** `docs/proposals/2026-09-01-shared-agent-handoff-facade-workflow-bot-mode-consolidated.md`

**Foundation:** Stages 1–3 through `c9532d3fbc17acefdffa3f00bba4f51c3f9e04da`

## Verdict

**HOLD — the accepted architecture fits the live code, but Stage 4 is not yet implementation-ready.** The core and plugin split is concrete and no architectural rewrite is needed. Implementation may begin only after review resolves the four gates below:

1. Name the standalone repository, owner, local checkout location, license, and default branch. No intended repository was found locally or among the configured GitHub account's repositories.
2. Confirm that Stage 4 includes the proposal's two presently absent transport-neutral contracts: immutable input-artifact descriptors and free-form correlated question/answer interactions.
3. Supply a disposable GitLab project, exact GitLab version/offering/tier, and two distinct authenticated service-account identities with Developer-or-higher project access for the real protocol gate.
4. Confirm the v1 network policy: direct HTTPS using system trust, no ambient proxies, and no redirects. A required explicit proxy, custom CA, or mutual TLS would change the plugin-only plan and must be stated before implementation.

The remaining uncertainty is empirical rather than architectural. GitLab documents `last_commit_id` on repository-file update actions, but does not document it as a linearizable compare-and-swap guarantee. The planned two-poller live test must pass on the supported GitLab deployment before the claim protocol can be released.

No production code, external repository, GitLab project, release, upstream merge, or brand propagation was created in this assessment session.

## Verified starting state

- Checkout: `base`.
- `HEAD`: `c9532d3fbc17acefdffa3f00bba4f51c3f9e04da`.
- `origin/base`: the same commit.
- The only initial worktree entries were the user-owned untracked paths listed in the task; they were not read as Stage 4 authority and were not modified.
- The required Stage 1–3 foundation, final reconciliation, and upstream-customization manifest were read in the requested order.

### Focused baseline

All commands ran with retries disabled and collected real tests.

| Command | Result |
|---|---|
| Required 15-file handoff/plugin/Workflow/TUI command through `scripts/run_tests.sh` | 15 files, 464 passed, 0 failed in 10.7s |
| `tests/plugins/workflow/test_installed_distribution_e2e.py -m integration` through `scripts/run_tests.sh` | 4 selected integration tests passed, 0 failed in 18.9s |
| Required Desktop Vitest command | 2 files, 15 tests passed, 0 failed; expected Vite native/local-storage warnings only |
| `cd apps/desktop && npx tsc -p . --noEmit` | exit 0 |

## Live architecture trace

### Exact registration and lifecycle seams

Line references are against the assessed core commit.

| Seam | Current authority | Stage 4 delta |
|---|---|---|
| Plugin-facing registration | `PluginContext.register_approval_transport()` in `hermes_cli/plugins.py:1876` | Add the analogous single `register_handoff_channel(scheme, factory)` operation. |
| Manager ownership/replacement | `PluginManager.register_approval_transport()` in `hermes_cli/plugins.py:5124` and the existing registration ledger | Add one profile-local scheme map with identical duplicate, unload, reload, and rollback ownership behavior. |
| External discovery | `ENTRY_POINTS_GROUP = "hermes_agent.plugins"` at `hermes_cli/plugins.py:518`; `get_plugin_manager()` at `:6941` | No new discovery system. Load the external distribution only through the existing entry point/filesystem mechanisms and profile opt-in. |
| Channel selection | `_BuiltinHandoffChannels` in `hermes_cli/handoff/service.py:129`; `AgentHandoffService` at `:181` | Resolve built-ins plus the profile's immutable registered-channel snapshot. |
| Background registration | `PluginContext.register_background_service()` at `hermes_cli/plugins.py:1799`; `_register_background_service()` at `:4713` | Retain the operation; seal owning profile home into hosted registrations. |
| Background host | `PluginManager._make_background_host_locked()` at `hermes_cli/plugins.py:4803`; `BackgroundServiceHost` in `hermes_cli/plugin_services.py:178` | Materialize enabled services for every served profile under explicit home/secret scope; preserve the existing host generation, reload, stop, join, and health rules. |
| Served profiles | `_served_profile_homes()` in `hermes_cli/handoff/supervisor.py:85` | Reuse the same host-kind profile set for plugin services; do not let the plugin enumerate profiles. |
| Supervisor service construction | `_default_service()` in `hermes_cli/handoff/supervisor.py:109` | Use the profile-aware service factory so restart advancement can resolve the registered channel. |
| Directory validation | `_validate_endpoint()` in `hermes_cli/handoff/directory.py:93` and `resolve_agent_target()` at `:168` | Delegate registered endpoint validation/capabilities to the owning profile's channel without product-specific branches. |
| Workflow admission/execution | `validate_assignment_admission()` in `plugins/workflow/admission.py:37`; `HandoffPromptExecutor` in `plugins/workflow/executors/handoff.py:41`; scheduler construction at `plugins/workflow/scheduler.py:1130` | Resolve the same profile channel snapshot and add neutral artifact/question contracts. |
| Bot | `_handoff_service()` in `tools/bot_mode_dm.py:469` | Use the profile-aware service factory; keep the model-visible schema unchanged. |
| TUI/Desktop RPC | service construction in `tui_gateway/methods_agent_handoff.py:125`; Desktop calls in `apps/desktop/src/plugins/hermes-bots/handoffs.tsx` | Keep existing RPC operations and extend only bounded transport-neutral codecs/projections. |

### Core handoff service and ledger

`hermes_cli/handoff/` is already the correct narrow waist.

- `models.py` owns the immutable spec, canonical endpoint, bounded binding/checkpoint envelopes, capabilities, interactions, and terminal result validation.
- `store.py` owns the profile-local SQLite ledger (schema v3), operation journal, events, commands, and Bot deliveries.
- `service.py` owns creation, binding, submission, reconciliation, observation, cancellation, commands, operation fencing, and semantic retry generations.
- `supervisor.py` owns restart recovery and advances due handoffs for the profile homes served by a gateway/web process.
- `directory.py` owns friendly-name and canonical-target resolution from profile-local configuration.
- `projection.py` owns bounded, redacted transport-neutral views consumed by TUI/Desktop.

The only channel selector is `_BuiltinHandoffChannels` in `service.py`. It is deliberately a fixed local/peer switch and every direct `AgentHandoffService(...)` construction receives it implicitly. Stage 4 must replace that fixed selection point with registered scheme resolution while keeping the existing service and store authoritative.

No new core ledger is needed. The current handoff row already persists a bounded channel binding and checkpoint, and the operation journal already fences ambiguous outward effects. The existing allowlists are local/peer-shaped, however, so the envelope validators need one bounded versioned registered-channel variant rather than GitLab-specific columns.

### Plugin discovery and ownership

`hermes_cli/plugins.py` is the authoritative discovery and ownership path.

- Filesystem plugins are discovered from bundled plugins and the active profile's external plugin directory.
- Python distributions are discovered through the `hermes_agent.plugins` entry-point group.
- External and entry-point plugins are opt-in through `plugins.enabled`.
- The `PluginManager` is cached by resolved Hermes home, so registrations are profile-owned.
- `PluginContext.get_config()` reads `plugins.entries.<plugin-id>.settings` from that profile.
- `PluginContext.state` is bounded, locked, profile-local JSON at `plugin-data/<namespace>/state.json` with mode `0600`; it is sufficient for plugin polling cursors and compact correlation indexes.
- Duplicate approval-transport registrations already fail and ownership is tracked for safe replacement/unload. A handoff-channel registration should copy this concrete pattern rather than introduce a general extension framework.

Entry-point discovery is metadata-only until the enabled plugin is loaded. The distribution entry point must expose a module or object with `register(ctx)`. The plugin's manifest remains the authority for plugin ID, version, declared capabilities, configuration schema, and background-service declaration.

### Background-service lifecycle and profile isolation

The existing host in `hermes_cli/plugin_services.py` and `PluginManager._make_background_host_locked()` starts registered factories, requests stop on reload/shutdown, joins them, and reports health. Gateway and headless web lifecycles already use this host.

One live gap matters for Stage 4: `BackgroundServiceContext` contains host identity and delivery-port data but not the owning profile home. The active manager's services are hosted, while secondary served-profile managers are discovered separately and their plugin services are not folded into that host. Calling `PluginContext.get_config()` or secret resolution on a background thread without an explicit profile scope can therefore select the wrong profile.

The minimum correction is to retain the existing single background-service host but materialize one registered service instance per served profile, with its owning resolved home sealed into the service registration and both `set_hermes_home_override(home)` and `set_secret_scope(build_profile_secret_scope(home))` active around factory creation and execution. The plugin must not scan all profiles itself. Only a profile that enables/configures the plugin may poll its inbox.

This is a concrete lifecycle seam with a working consumer, not speculative infrastructure.

### Workflow

The live path is:

`language_schema.py` / Workflow source → `admission.py` → scheduler → `executors/handoff.py` → shared `AgentHandoffService` → Workflow store/coordinator/actions/notification outbox.

The executor already derives a stable handoff key from workflow/run/node/attempt, lets the shared service own the attempt, waits on authoritative snapshots, and reuses the normal structured-output validator. GitLab must remain absent from Workflow.

Required neutral changes are:

- allow the registered endpoint grammar in authoring/admission;
- resolve the initiating profile's registered channel set at admission and execution instead of constructing the fixed local/peer switch;
- carry immutable input-artifact descriptors into `HandoffSpec` and its fingerprint;
- project arbitrary correlated questions into existing Needs Attention and route `provide-input` text back through `AgentHandoffService.command()`;
- preserve current approval-choice behavior unchanged.

### Bot Mode

`tools/bot_mode_dm.py` already accepts a friendly target or canonical endpoint as a string, resolves it through the profile directory, creates a conversation handoff through the shared service, and uses the durable Bot return-delivery path. The model-visible `message_agent` schema does not need to change: `gitlab+icm://townhall/inbox` fits the existing target field.

Bot Mode does need registered-channel resolution for the initiating profile and a transport-neutral free-form answer path when the remote event is a question rather than an approval. Legacy Peer DM and relay fallback remain untouched.

### TUI gateway and Desktop

`tui_gateway/methods_agent_handoff.py` is the transport-neutral RPC surface. It validates profile selection, opens that profile's store, and exposes directory/create/get/list/evidence/command. It currently constructs a service with the fixed built-ins and assumes `needs_input` is a finite approval choice.

The Desktop `hermes-bots` pane calls only those `agent_handoff.*` methods. Its endpoint selector already treats endpoint strings generically. It has no GitLab dependency and must stay that way. Stage 4 requires only generic scheme display, endpoint-selection coverage, and a bounded text-answer control for question interactions. No dashboard chat or Bot/Desktop rewrite is needed. There is no separate Ink handoff UI to extend; the TUI requirement is satisfied at the `tui_gateway` operation/codec boundary.

## Standalone repository discovery

Read-only discovery covered:

- local Git repositories under `/Users/coreyellis/code/github.com`;
- repositories visible to the authenticated `cmetech` GitHub account through `gh`;
- global GitHub repository searches for GitLab/ICM/Hermes-handoff combinations.

No intended Stage 4 plugin repository was found. `cmetech/Interpretable-Context-Methodology` is the ICM research repository, not a Hermes plugin. The nearby `cmetech/otto_hermes/ericsson-capabilities` checkout is a shared Ericsson-capabilities project and contains no GitLab+ICM handoff plugin or protocol authority.

**Required clarification:** identify the actual repository. If none has been reserved, the implementation plan uses the reviewable placeholder below but does not authorize creation:

- repository: `cmetech/hermes-gitlab-icm`;
- distribution: `hermes-gitlab-icm`;
- Python package: `hermes_gitlab_icm`;
- entry point: `gitlab-icm = hermes_gitlab_icm` in `hermes_agent.plugins`.

Before any work in that repository, its own `AGENTS.md` and build configuration must be read. If they conflict with the provisional paths or commands in the plan, amend the plan and stop for review.

## Exact repository split

### Core repository: `cmetech/otto_hermes` / `hermes-agent`

Core owns only:

1. parsing a strict registered handoff scheme without understanding its product;
2. profile-local registration/unregistration and duplicate ownership checks for a channel factory;
3. resolving registered channels in the existing service, directory, supervisor, Workflow, Bot, and TUI gateway paths;
4. hosting a plugin's profile-owned background service under the existing lifecycle;
5. bounded/versioned generic binding, checkpoint, interaction, input-artifact, admission-proof, and projection envelopes;
6. transport-neutral question/answer and endpoint selection in Workflow, Bot, TUI, and Desktop;
7. contract tests using a tiny external fixture distribution, not GitLab.

Core must not own GitLab imports, API models, credentials, HTTP calls, issue URLs, event codecs, polling policy, repository layout, or GitLab errors.

### Standalone GitLab+ICM repository

The plugin owns:

1. its manifest, entry point, packaging, install instructions, and config validation;
2. the `gitlab+icm` channel factory and adapter;
3. strict town-hall lookup and inbox authorization;
4. lazy credential resolution and authenticated GitLab client;
5. submission, reconciliation, observation, command delivery, and cancellation;
6. the issue/branch/commit/file/note protocol and all untrusted-input parsers;
7. the profile-scoped inbox polling service;
8. compact polling cursors/correlation state in `PluginContext.state`;
9. translating inbound work into the same profile's existing local handoff service, then publishing its interactions and result;
10. unit, failure-injection, contract, installed-wheel, and live disposable-project tests.

The inbox worker must not create a second executor. It creates or reopens a deterministic local task handoff in the existing profile HandoffStore, using the external handoff/generation as its stable key, and lets the existing supervisor/Runs path execute it. The plugin then projects that local snapshot back to GitLab.

## Minimal cross-repository contract

The contract should expose one operation, not a catalog:

```text
ctx.register_handoff_channel("gitlab+icm", factory)
```

Required semantics:

- registration is profile-local and owned by the registering plugin;
- a scheme has one owner per profile; duplicate registration fails deterministically;
- unloading/reloading the plugin removes/replaces only its own registration;
- the scheme and factory are absent when the plugin is not enabled;
- the factory receives the owning profile context and returns the existing channel operations: `assess`, `bind`, `submit`, `reconcile`, `observe`, `cancel`, and `deliver_command`;
- the returned channel communicates only through bounded core handoff value objects and versioned JSON-safe binding/checkpoint/event data;
- endpoint selection asks the registered channel to validate its opaque path; core never imports its configuration model;
- service creation for a profile resolves a stable snapshot of registered factories for that profile and retains the built-in local/peer channels;
- `structured_output` remains a spec-level contract validated by the initiator, not a remote capability claim.

The one registered-channel data envelope should contain a version, scheme, bounded identifiers, bounded SHA-256 values, bounded nonnegative integers, bounded timestamps, and a bounded safe external reference. It must reject unknown keys, credential-like key names or values, URLs containing userinfo, and over-limit JSON. Do not loosen the existing local/peer envelope into arbitrary JSON.

The core admission proof needs a versioned registered-channel variant because the current proof accepts only local `run_id`/`process_pid`. The GitLab variant should carry only stable external identifiers and verified immutable request SHA, never a token or raw response.

## Endpoint and configuration validation

### Endpoint

The only accepted external form is:

```text
gitlab+icm://<townhall>/<inbox>
```

Recommended closed grammar, pending review:

- the value must already be canonical UTF-8 text;
- scheme exactly lowercase `gitlab+icm`;
- town hall and inbox each match `^[a-z0-9][a-z0-9_-]{0,63}$`;
- exactly one nonempty path segment after the authority;
- no percent encoding, userinfo, port, raw host, IP literal, query, fragment, backslash, dot segment, control character, or trailing slash;
- reserialization must byte-equal the input.

The endpoint resolves only names. `townhall` selects a validated plugin configuration object; `inbox` must appear in that town hall's explicit inbox map. It never controls an HTTP origin directly.

### Town-hall configuration

Non-secret configuration belongs below `plugins.entries.gitlab-icm.settings.townhalls.<name>` in profile-local `config.yaml`. Each entry must be closed and include:

- exact HTTPS `origin` (scheme, host, optional explicit port; no path/query/fragment/userinfo);
- numeric `project_id`;
- a secret reference name, not a credential;
- `protocol_version: 1`;
- bounded polling interval, jitter, page/item ceilings, and retry/backoff policy;
- bounded retention policy for plugin cursor/index cleanup;
- an inbox map with allowed requester and responder authenticated GitLab numeric user IDs.

Use numeric project and actor IDs as identity. Names/usernames are display-only. Reject unknown keys and duplicate/ambiguous normalized names. Do not add a `HERMES_*` setting.

## Credential, HTTP, TLS, proxy, and redirect boundaries

- Resolve the credential lazily from the existing profile secret scope using the configured reference. Never copy it into config, endpoint, HandoffStore, plugin state, logs, exceptions, events, bindings, checkpoints, or test snapshots.
- Call `GET /user` at startup/refresh and verify the returned numeric actor ID is allowed for that inbox role. Git author name/email and note payload identity never grant authority.
- Send authentication only to the configured exact origin. Build relative API paths locally and reject absolute or scheme-relative paths.
- Disable ambient proxy discovery (`trust_env=False`) and automatic redirects. Treat every 3xx as an unsafe failure/ambiguity; never forward credentials to a redirect target.
- Require HTTPS, hostname verification, and certificate verification. Under the proposed v1 policy, use system trust and no user-supplied CA/mTLS/proxy fields.
- Apply connect/read/write/pool deadlines, response-byte ceilings, JSON depth/item/string ceilings, and bounded pagination.
- Redact token-shaped values and unsafe GitLab response bodies. Persist only stable codes, IDs, digests, retry times, and a bounded safe diagnostic.

The in-tree Ericsson GitLab transport demonstrates useful local patterns—`httpx`, `follow_redirects=False`, `trust_env=False`, origin/path validation, bounded responses, Retry-After handling, and redacted errors. The standalone plugin may port those patterns with attribution but must not import Ericsson-specific modules or types.

## GitLab documented behavior and assumptions

Only official GitLab documentation was used for this assessment.

### Documented guarantees used

- REST API v4, authentication headers, pagination, and response semantics: [REST API](https://docs.gitlab.com/api/rest/) and [API authentication](https://docs.gitlab.com/api/rest/authentication/).
- Issues can be created, listed/searched, updated, assigned, labeled, and closed; responses include `iid`, `author`, labels, state, and `web_url`: [Issues API](https://docs.gitlab.com/api/issues/).
- Issue notes are paginated and include stable note ID, author, body, creation time, and system flag; notes can be created: [Notes API](https://docs.gitlab.com/api/notes/).
- Repository-file reads at an exact ref return commit and content hashes. Update/delete accept `last_commit_id`: [Repository Files API](https://docs.gitlab.com/api/repository_files/).
- Commit creation supports a start ref and multiple file actions; update/move/delete actions accept `last_commit_id`: [Commits API](https://docs.gitlab.com/api/commits/).
- `GET /user` returns the authenticated user's stable numeric ID: [Users API](https://docs.gitlab.com/api/users/).
- API metadata/version can identify a deployment: [Metadata API](https://docs.gitlab.com/api/metadata/).
- API/content/repository-file rates may differ and Self-Managed administrators can configure limits: [Rate limits](https://docs.gitlab.com/rate_limits/), [API limits](https://docs.gitlab.com/administration/settings/rate_limit_on_issues_api/), and [content-creation limits](https://docs.gitlab.com/administration/settings/rate_limit_on_notes_creation/).

The Issues, Notes, Repository Files, Commits, and Users APIs used here are generally documented for all tiers. Account provisioning, service-account features, multi-assignee behavior, administrator rate policy, and deployed API behavior remain offering/version/tier dependent. The protocol therefore uses one numeric assignee only and does not require a premium multi-assignee feature.

### Implementation inferences requiring proof

- `last_commit_id` is intended as an optimistic stale-write guard, but the docs do not state a linearizability guarantee. The two-poller claim race is a mandatory live compatibility gate.
- A lost successful issue-create response can be reconciled by paginating title search and verifying the exact marker/request SHA. Search completeness and latency must be tested on the target deployment; a bounded inconclusive search yields `indeterminate`, never a duplicate POST.
- A lost note-create response can be reconciled only by paginating and matching a deterministic event ID plus payload hash and authenticated author ID.
- `updated_after` can reduce issue polling, but cannot be the sole cursor. Clock skew and equal timestamps require an overlap window plus stable issue/note ID deduplication.
- Notes do not expose an `updated_after` filter in the documented list API. Per-issue note polling must paginate in stable ascending order and retain the highest fully processed note ID, with overlap/deduplication.

Initial support must be declared only for the exact GitLab version/offering exercised by the live gate. Broader compatibility is earned by CI or documented manual matrices, not assumed.

## Channel capabilities and negotiation

The channel advertises capabilities from validated town-hall/inbox configuration and live authenticated reachability. Stage 4 adds the neutral capability names `question_answer` and `artifact_input`; it does not disguise them as approval or structured-output support:

- always required: `durable_admission`, `authoritative_status`;
- supported by protocol v1: `cancellation`, `approval`, `question_answer`, and `artifact_input`;
- `follow_up` and `steering` only if the plugin implements correlated post-admission command notes in the same task generation;
- `structured_output` stays local to the initiating spec and result validator.

The service must reject a handoff before submission if its required capabilities are not a subset of the channel assessment. Directory resolution must derive requirements from the requested mode/interaction policy, not automatically demand conversation controls merely because an endpoint was explicit. This corrects a live assumption in `directory.py` that every explicit/directory endpoint is a controlled conversation.

Capability assessment failures must distinguish invalid/unconfigured endpoint, unauthenticated actor, unauthorized inbox, incompatible protocol, temporarily unreachable service, and capability mismatch without exposing credentials or raw HTTP bodies.

## GitLab+ICM protocol

### External identity and repository layout

- External identity: one issue in the configured project.
- Deterministic marker: a versioned marker containing core `handoff_id`, semantic generation, and request SHA-256. Exact encoding is owned by the plugin codec and appears in the issue body/title search token.
- Dedicated branch: deterministic, length-bounded, derived from handoff ID/generation, and verified against the expected base/start SHA.
- Immutable request commit: creates `.hermes-handoffs/v1/<handoff-id>/request.json`, prompt/context bytes, and declared input artifact files/manifests.
- Immutable result commit: creates result files and a bounded manifest under the same root.
- Commits are append-only protocol facts. The plugin never force-pushes, rewrites, or trusts Git author identity.
- Labels, assignee, and issue state are repairable projections only.

The request manifest seals protocol version, handoff/generation, endpoint, prompt/content hashes, structured-output schema fingerprint, deadline, capability request, artifact paths/hashes/sizes/media types, and request correlation. Paths are fixed relative POSIX paths; traversal, symlinks, submodules, executable semantics, and arbitrary repository paths are rejected.

### Submission and ambiguity

1. Resolve and authenticate the configured town hall.
2. Create or verify the dedicated branch and immutable request commit.
3. Create the issue containing the deterministic marker and verified request SHA.
4. Persist the issue IID/ID, branch, request commit SHA, marker, and cursor in the initiating HandoffStore checkpoint.
5. Repair labels/assignee/state projections after identity is authoritative.

Every write uses the existing operation journal boundary. A transport timeout or lost response invokes read-after-write reconciliation. Conflicting marker matches, duplicate valid issues, same marker with another request SHA, or inability to prove one identity becomes `indeterminate`. It never causes a blind duplicate branch, commit, issue, note, or terminal event.

### Claims

The requester places an unclaimed sentinel in the request commit. A responder claims by committing a versioned claim fact against the expected request/sentinel commit with `last_commit_id` as the optimistic precondition. After any success, error, or timeout, each poller reads the exact branch/file/commit and verifies the claim payload and authenticated event author.

One verified claim becomes authoritative. Two different valid claims, an overwritten valid claim, or ambiguous repository history becomes `indeterminate`; labels/assignee cannot break the tie. The live two-poller test must prove exactly one contender can commit from the same precondition. If the target GitLab cannot provide that property, Stage 4 returns to design review; labels are not an acceptable fallback fence.

### Versioned machine notes

Every machine note starts with a closed, versioned header and bounded canonical JSON payload. It seals:

- protocol version;
- event ID, handoff ID, semantic generation, kind, and correlation ID;
- request/claim/result commit SHA as applicable;
- payload SHA-256;
- declared inbox/agent display identity.

Authority still comes exclusively from the GitLab API response's authenticated `author.id`. Declared identities and Git commit authors are untrusted annotations.

Kinds are limited to claimed, started, progress, question, answer, cancellation request/acknowledgement, completion, failure, and verification failure. Unknown versions/kinds, malformed bodies, excessive sizes, mismatched correlations/hashes, unauthorized authors, system notes, and edited/conflicting facts are retained only as bounded redacted evidence and do not advance state.

Identical duplicate events collapse by event ID and payload hash. The same event ID with different content, conflicting valid claims, or conflicting valid terminal events makes the handoff `indeterminate`.

### Cursor and restart protocol

- Store outward per-handoff issue/note cursors and immutable SHAs in the HandoffStore checkpoint.
- Store only bounded inbox polling watermarks and a compact issue-to-local-handoff index in profile-scoped `PluginContext.state`.
- Poll issues with an overlap window and deterministic ID deduplication; repair drifted labels/assignee/state from authoritative facts.
- Poll each relevant issue's notes in stable ascending order, page fully through the bounded window, and advance a cursor only after durable application.
- On restart, recreate no identity. Reconcile branch/request/issue/claim/events first, reopen the deterministic local handoff, then resume polling.
- Cursor or page limits that cannot prove completeness yield a retryable/indeterminate state, not silent skipping.

### Questions, answers, and cancellation

A valid question note creates a durable `needs_input` interaction with a bounded redacted prompt, exact request/correlation ID, and either finite choices or a free-form text contract. One answer command may satisfy that exact open request. Replays with identical command/payload are idempotent; a different answer for the same request is a conflict. Unmatched, stale, or unauthorized answers do not advance the handoff.

Cancellation is cooperative. The initiator posts one deterministic cancellation command and reconciles a lost response. The responder stops admitting new work, forwards cancellation to the existing local handoff, and publishes an acknowledgement or terminal event. A verified completion racing cancellation remains completion; a verified cancellation terminal remains cancellation; contradictory valid terminals become `indeterminate`. Deadlines reuse core deadline policy and do not invent a plugin scheduler.

### Completion

A completion note names an immutable result commit SHA and manifest hash. The initiator fetches files only at that SHA and verifies:

- repository/project/branch identity;
- closed manifest version and keys;
- path, count, per-file size, aggregate size, media type, and SHA-256 limits;
- handoff ID, generation, request SHA, and result correlation;
- no traversal, links, unexpected files, or mutable branch-head substitution;
- the initiating `output_schema` through the existing structured-output validator.

Hash, schema, manifest, authorization, or immutable-ref failures never become success. The bounded validated terminal value is persisted in the initiating HandoffStore; unrestricted repository content is not.

## Durable state changes

### Core

- Add immutable input-artifact descriptors to `HandoffSpec`, serialization, and fingerprinting. The descriptors contain name/path/media type/size/SHA and bounded content only where the accepted facade requires direct bytes.
- Add a bounded registered-channel endpoint shape and versioned binding/checkpoint/admission-proof variants to existing handoff serialization.
- Add a generic question interaction variant and answer command payload while retaining legacy approval fields.
- Bump the core ledger schema only if a new indexed/queryable column is genuinely needed. The current JSON columns should hold these closed values, so a schema bump is not presently justified.

### Plugin

- Use HandoffStore checkpoint data for per-handoff GitLab identity and reconciliation facts.
- Use `PluginContext.state` for polling watermarks and a bounded external-to-local correlation index.
- Do not add a plugin SQLite database, credential cache, scheduler, manager, or supervisor.

## Consumer projections

- Workflow: show channel/scheme, bounded external reference, progress, question, answer status, cancellation, terminal result, and redacted evidence through existing Needs Attention/notification mechanisms.
- Bot: preserve legacy target behavior; allow directory/canonical GitLab selection and answer a free-form current question through the existing handoff command route.
- TUI gateway: keep the existing RPC names and add only versioned transport-neutral fields to directory/summary/evidence/command codecs.
- Desktop: endpoint strings remain opaque; render a generic scheme badge/external link and finite-choice or text-answer UI without GitLab imports.

An external reference may contain only a bounded display label and an HTTPS URL previously validated by the owning channel against its configured origin/project/issue identity. Core redaction must strip userinfo, query, and fragment and reject a non-HTTPS or mismatched value. Raw GitLab errors and note bodies never enter projections.

## Test and distribution strategy

### Core contract proof

A test-only external distribution must be built and installed into an isolated environment. It registers exactly one synthetic scheme and one profile-owned background service through `hermes_agent.plugins`. Tests prove enable/disable, duplicate ownership, unload/reload, profile isolation, served-secondary-profile hosting, secret scope, service construction, directory/admission, restart/shutdown, and absence from the core model-tool schema.

### Plugin test layers

1. Pure codec/config tests for closed grammars, manifests, markers, notes, identities, paths, limits, and redaction.
2. Scripted HTTP tests for pagination, rate limiting, retry classes, redirects, TLS/proxy behavior, actor authorization, and every ambiguous write boundary.
3. Adapter/inbox tests with a real temporary HandoffStore and plugin state, no mocked store/service semantics.
4. Installed-wheel tests against a built core artifact and external plugin artifact.
5. A real authenticated disposable GitLab project gate with two pollers and two service accounts.

The live project gate must cover lost create responses, duplicate markers, conflicting payloads, paginated issues/notes, 429/Retry-After, label/assignee/state drift, two-poller claim races, unauthorized authors, forged Git commit authors, malformed notes, restart cursors, question/answer, cancellation races, immutable result retrieval, hash/schema failures, conflicting terminals, and cleanup. Where the real service cannot inject a lost response, a transport proxy may drop the response after forwarding while preserving the real GitLab write.

### Artifact verification

- Build the core wheel/sdist and install into a clean venv.
- Build the plugin wheel/sdist separately and install it into the same clean venv.
- Verify discovery metadata before enablement, opt-in loading after config, one registered channel, one profile-owned background service, an end-to-end synthetic handoff, and clean unload/shutdown.
- Inspect both artifacts to prove no plugin package is bundled in core and no core source is vendored in the plugin.

## Live-code clarifications to the accepted proposal

These are not architecture changes; they are concrete gaps the Stage 4 plan must close or explicitly defer by changing the accepted scope.

1. **Free-form questions are absent.** Core checkpoints, Workflow `HandoffInputProjection`/`HandoffResponseIntent`, Bot command handling, TUI gateway, and Desktop currently support approval-choice input only. The proposal explicitly requires questions and answers. Recommendation: include a bounded generic question/text-answer variant in Stage 4.
2. **Input artifacts are absent from `HandoffSpec`.** Workflow assignment schema currently carries endpoint, interaction policy, deadline policy, and `on_deadline`, but not the proposal's immutable input artifacts. Recommendation: include descriptors and bounded bytes in the neutral spec/fingerprint, then let the GitLab plugin materialize them.
3. **Explicit targets currently imply conversation controls.** Directory resolution requires cancellation/follow-up for every explicit or named-directory endpoint. Recommendation: derive requirements from mode and interaction policy so task-only GitLab endpoints are valid without falsely advertising follow-up.
4. **Direct service construction bypasses plugins.** Workflow, Bot/TUI helpers, and the supervisor rely on the fixed default switch. Recommendation: one profile-aware service factory becomes the single construction path; do not mutate an existing conversation's channel snapshot mid-flight.
5. **Secondary profile plugin services are not hosted.** Recommendation: extend the current host to materialize enabled registrations for served profiles under explicit home/secret scope. Do not put profile enumeration in the plugin.
6. **GitLab optimistic claim strength is not documented.** Recommendation: release-gate the exact target deployment with the two-poller live test and declare a supported version/offering.

No evidence contradicts the accepted issue/branch/commit/note architecture. The only potential contradiction is claim atomicity on a target GitLab deployment; the live gate decides it.

## Decisions requiring user approval

1. Exact standalone repository identity and ownership, or approval of the placeholder `cmetech/hermes-gitlab-icm`.
2. Whether the two missing accepted contracts—input artifacts and free-form question/answer—are confirmed Stage 4 scope. The plan assumes yes.
3. The initial supported GitLab deployment/version/offering/tier and who supplies/administers the disposable project and service-account credentials.
4. Whether the direct-system-TLS/no-proxy/no-redirect v1 policy is acceptable. If not, specify the required network model.
5. The canonical slug grammar proposed for town hall and inbox names.
6. Whether the safe external issue link is required in Stage 4 UI projections or the issue IID/display label alone is sufficient. The plan includes the safe link because the issue is the human inbox.

## Risks and platform gaps

- GitLab optimistic update semantics may fail the required claim fence on some versions or repository states. This is a release blocker, not a reason to fall back to labels.
- Search/index latency can make ambiguous issue creation temporarily unreconciled. The safe state is `indeterminate`/retry, not duplicate creation.
- Note pagination grows with long-lived issues. Stage 4 uses bounded paging/cursors; retention and compaction tuning remain Stage 5.
- GitLab.com and Self-Managed rate policies differ. Backoff is bounded and deployment-configurable; operational tuning remains Stage 5.
- Native Windows destination locking remains Stage 5 and is unchanged.
- The known macOS Workflow/background-thread SQLite shutdown diagnostic predates Stage 4. It becomes in-scope only if the new lifecycle tests reproduce or worsen it.
- No literal GitLab credentials or external resources exist in the current planning environment, so the empirical protocol gate has not run.

## Readiness exit criteria

Stage 4 may move from HOLD to READY when:

- all six decisions above are reviewed;
- the standalone repository is identified and its instructions/tooling are incorporated into the plan;
- disposable GitLab prerequisites are available without persisting credentials;
- the cross-repository contract and task ownership in the companion plan are accepted;
- production implementation is explicitly authorized in a later session.
