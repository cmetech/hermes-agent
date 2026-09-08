# GitLab+ICM Agent Handoff Stage 4 Implementation Plan

> **Status:** proposed; stop for review before production implementation
>
> **Architecture authority:** `docs/proposals/2026-09-01-shared-agent-handoff-facade-workflow-bot-mode-consolidated.md`
>
> **Readiness authority:** `docs/assessments/2026-09-03-agent-handoff-stage-4-implementation-readiness.md`
>
> **Core baseline:** `c9532d3fbc17acefdffa3f00bba4f51c3f9e04da` on `base`

## Goal

Deliver one separately installed GitLab+ICM handoff channel and its profile-owned inbox service through the smallest generic core registration operation. Workflow, Bot Mode, TUI gateway, and Desktop select and operate the endpoint without importing GitLab code or credentials. The initiating profile's existing HandoffStore remains orchestration truth.

This plan does not authorize implementation, repository creation, GitLab provisioning, publishing, release work, upstream merging, or brand propagation.

## Approval gate

Do not execute Task 1 until review records all of the following:

- the standalone repository's exact owner/name/local path/license/default branch;
- its `AGENTS.md` and actual build/test commands;
- confirmation that Stage 4 includes transport-neutral input artifacts and free-form correlated questions/answers;
- the accepted town-hall/inbox slug grammar;
- the initial supported GitLab version/offering/tier;
- the disposable project ID/origin and two service-account identities, supplied through existing secret mechanisms only;
- acceptance of direct HTTPS/system trust/no ambient proxy/no redirects for protocol v1, or a replacement network requirement;
- whether Desktop/TUI should expose a validated safe issue link or only an issue reference label.

The provisional standalone identity used below is:

```text
repository:    cmetech/hermes-gitlab-icm
checkout:      /Users/coreyellis/code/github.com/cmetech/hermes-gitlab-icm
distribution:  hermes-gitlab-icm
package:       hermes_gitlab_icm
entry point:   gitlab-icm = hermes_gitlab_icm [hermes_agent.plugins]
default branch: <to be approved>
```

If review chooses another identity, replace these literals before implementation. Do not create the placeholder automatically.

## Execution method

Use `superpowers:test-driven-development` for every behavior task, `superpowers:verification-before-completion` before every completion claim, and `superpowers:requesting-code-review` at the final gate.

For every task:

1. write the smallest behavior test first;
2. run the exact focused command and record the expected failure;
3. implement only enough to pass;
4. rerun the focused command;
5. refactor only duplication introduced by the task and keep the test green;
6. run the stated neighboring regression command;
7. inspect `git diff --check`, `git status --short`, and the exact staged paths;
8. commit only task-owned files in the named repository;
9. do not merge or publish core seam commits until the installed plugin proof passes.

One task produces at most one commit in one repository. If a task reveals a cross-repository contract change, stop, amend this plan, and review it before changing both repositories.

## Repository boundaries

### Core repository

Path: `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`

Core may change only:

- consumer-neutral handoff models/store/service/directory/projection/supervisor;
- `PluginManager` registration and existing background-service lifecycle;
- Workflow's transport-neutral assignment/admission/executor/interaction path;
- Bot and TUI gateway transport-neutral service resolution/commands;
- Desktop `hermes-bots` endpoint and input projection;
- tests and documentation for those contracts.

Core must contain no GitLab import, credential name, API route, issue/note model, protocol marker, or GitLab HTTP fixture.

### Standalone repository

The plugin owns all GitLab configuration, authentication, HTTP, protocol, polling, and tests. It may depend on the installed Hermes distribution but may not vendor or patch core. It must not be copied under the core repository's `plugins/` directory.

Provisional plugin files are intentionally few:

```text
pyproject.toml
README.md
LICENSE
src/hermes_gitlab_icm/__init__.py
src/hermes_gitlab_icm/plugin.py
src/hermes_gitlab_icm/config.py
src/hermes_gitlab_icm/client.py
src/hermes_gitlab_icm/protocol.py
src/hermes_gitlab_icm/channel.py
src/hermes_gitlab_icm/inbox.py
tests/...
```

Do not add a generic source-control layer, plugin database, scheduler, manager, supervisor, webhook framework, cache abstraction, or provider interface.

## Locked v1 contract

Subject to the approval gate, implementation follows these decisions.

### Endpoint

`gitlab+icm://<townhall>/<inbox>` is canonical only when both names match `^[a-z0-9][a-z0-9_-]{0,63}$` and the URI has no percent encoding, userinfo, port, raw IP/host override, extra path segment, query, fragment, control, backslash, dot segment, or trailing slash. Re-encoding must equal the input byte-for-byte.

### Core registration

The one new operation is:

```python
ctx.register_handoff_channel("gitlab+icm", factory)
```

It is profile-local, plugin-owned, duplicate-rejecting, unloadable, and absent unless the external plugin is enabled. A profile-aware core helper constructs `AgentHandoffService` with built-ins plus the stable registered-channel snapshot for that profile.

### Generic envelopes

- Registered endpoints carry canonical URI, scheme, and opaque channel-owned location segments; local/peer compatibility properties remain intact.
- A registered binding/checkpoint/admission proof uses one closed versioned JSON envelope with bounded identifiers, SHA-256 values, integers/timestamps, cursor, and optional validated external reference. Unknown and credential-like keys fail.
- Input artifacts are bounded immutable records: logical name, `application/json` or `text/plain`, canonical UTF-8 content, byte size, and SHA-256. Aggregate Stage 4 limit is 500 KiB. Workflow output references are resolved before `create()`; canonical content and hash are persisted in the existing spec so a restart can submit identical bytes. Larger/binary transfer remains out of scope.
- An input interaction is either existing approval choices or one bounded UTF-8 question accepting one bounded UTF-8 answer. It carries an exact remote request/correlation ID.
- `question_answer` and `artifact_input` are explicit neutral channel capabilities. Existing `approval` remains approval-only; `structured_output` remains an initiator-side result contract.
- A safe external reference is display label plus HTTPS URL; the channel must validate it against configured origin/project/issue, and the core projection strips query/fragment/userinfo and enforces byte limits.

### Plugin state

- Per-handoff issue/branch/commit/note/correlation facts live in the initiating HandoffStore's registered-channel checkpoint.
- Inbox polling watermarks and a bounded issue-to-local-handoff index live in `PluginContext.state`.
- Credentials are resolved lazily from the profile secret scope and never persisted.

### GitLab protocol

- one issue = external identity and human inbox;
- one deterministic branch plus immutable request/result commits = bytes;
- versioned machine notes = claim/progress/question/answer/cancel/terminal facts;
- label/assignee/state = repairable projections;
- API `author.id` = actor evidence; Git author and note-declared actor are never authority;
- optimistic repository claim = claim fence, subject to the live two-poller gate;
- any conflicting valid claim/terminal = core `indeterminate`;
- any ambiguous write = reconcile by deterministic ID/hash before retry; never blind duplicate.

## Verification conventions

### Core

All Python test commands use the repository runner and disable file retries:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh <test paths> -q
```

### Standalone plugin

If the new repository has no conflicting instructions, use Python 3.11+, setuptools, pytest, httpx, and build. The exact provisional commands are:

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
.venv/bin/python -m build
.venv/bin/python -m pip install --force-reinstall dist/*.whl
```

These commands become authoritative only after the actual repository and its `AGENTS.md` exist. If its tooling differs, update this plan before code.

Environment variables in live tests may point to secrets/resources because they are credentials/test harness inputs, not user-facing product configuration. They must never be logged or written:

```text
GITLAB_ICM_TEST_ORIGIN
GITLAB_ICM_TEST_PROJECT_ID
GITLAB_ICM_TEST_REQUESTER_TOKEN
GITLAB_ICM_TEST_RESPONDER_A_TOKEN
GITLAB_ICM_TEST_RESPONDER_B_TOKEN
```

Product behavior remains in profile-local `config.yaml`; credentials are inserted into the temporary test profile through existing secret facilities.

## Task 1 — Core: close registered endpoint and artifact value contracts

**Repository:** core
**Owns:**

- `hermes_cli/handoff/models.py`
- `hermes_cli/handoff/store.py`
- `tests/hermes_cli/handoff/test_models.py`
- `tests/hermes_cli/handoff/test_store.py`

### RED

Add tests proving:

- canonical `gitlab+icm://townhall/inbox` parses as a registered endpoint while all raw URL/port/token/userinfo/query/fragment/encoding/control variants fail;
- existing local/peer endpoints serialize identically;
- a bounded `HandoffInputArtifact` canonicalizes JSON/text bytes, checks size/hash, survives store reload, and participates in the spec fingerprint;
- aggregate overflow, binary media, duplicate names, path-like names, and hash mismatch fail;
- versioned registered binding/checkpoint/admission-proof envelopes accept only the closed generic fact types and reject secrets, auth-like values, unknown keys, oversized JSON, unsafe external URLs, and arbitrary nested data;
- `question_answer` and `artifact_input` are accepted neutral capabilities while unknown capability names still fail;
- old schema-v3 rows reopen without mutation.

Run:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_models.py \
  tests/hermes_cli/handoff/test_store.py -q
```

Expected RED: registered endpoint/artifact/envelope APIs do not exist.

### GREEN

- Extend `HandoffEndpoint` with a registered-scheme shape while preserving `profile`, `peer`, and `kind` behavior for local/peer callers.
- Add the immutable artifact record and closed serializers.
- Add only a registered-channel branch to existing normalizers; do not make them arbitrary dictionaries.
- Reuse the existing JSON columns. Do not bump SQLite schema unless the RED test proves a query/index requirement.

Rerun the focused command and:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_service.py \
  tests/hermes_cli/handoff/test_directory.py \
  tests/hermes_cli/handoff/test_supervisor.py -q
```

**Atomic commit:** `feat(handoff): admit registered channel values`

## Task 2 — Core: add the profile-local channel registration operation

**Repository:** core
**Owns:**

- `hermes_cli/plugins.py`
- `hermes_cli/handoff/channels.py` (new; registration snapshot and service factory only)
- `hermes_cli/handoff/service.py`
- `hermes_cli/handoff/__init__.py`
- `tests/hermes_cli/test_plugin_handoff_channels.py` (new)
- `tests/hermes_cli/handoff/test_service.py`
- `tests/hermes_cli/test_plugin_ownership_ledger.py`

### RED

Add tests proving:

- `PluginContext.register_handoff_channel(scheme, factory)` registers exactly one scheme in that profile;
- disabled/unloaded plugins expose nothing;
- invalid/reserved schemes and duplicate owners fail;
- reload replaces only the same plugin's registration and rollback restores the previous owner on failed reload;
- two profile managers can own independent factories for the same scheme;
- a service created for a profile uses built-in local/peer plus a stable snapshot of registered channels;
- an existing service does not gain or swap a channel mid-conversation;
- channel capability mismatch fails before `submit`;
- no new model tool schema appears.

Run:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/test_plugin_handoff_channels.py \
  tests/hermes_cli/test_plugin_ownership_ledger.py \
  tests/hermes_cli/handoff/test_service.py -q
```

Expected RED: registration and profile-aware service factory do not exist.

### GREEN

- Mirror the concrete approval-transport ownership implementation.
- Keep `_BuiltinHandoffChannels` behavior behind the new stable resolver.
- Expose one `create_handoff_service(home, ...)` helper and replace internal fixed construction only in later consumer tasks.
- Keep the factory contract structural; do not create an abstract base class or channel catalog.

**Atomic commit:** `feat(plugins): register profile handoff channels`

## Task 3 — Core: host profile-owned plugin services in the existing lifecycle

**Repository:** core
**Owns:**

- `hermes_cli/plugin_services.py`
- `hermes_cli/plugins.py`
- `gateway/run.py`
- `hermes_cli/web_server.py`
- `tests/hermes_cli/test_plugin_background_services.py`
- `tests/gateway/test_plugin_background_services.py`

### RED

Add real lifecycle tests proving:

- an enabled service for the active profile starts once on web/gateway and stops/joins once;
- an enabled service for a served secondary profile also starts once without the plugin scanning profiles;
- an unconfigured/disabled profile starts nothing;
- factory creation, run, health, reload, and shutdown execute under the owning Hermes home and secret scope;
- two profiles with the same secret reference resolve different credentials without either value entering health/error output;
- failed reload preserves the old healthy services and registrations;
- restart does not duplicate a poller.

Run:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/test_plugin_background_services.py \
  tests/gateway/test_plugin_background_services.py -q
```

Expected RED: secondary profile service is absent or observes the wrong profile scope.

### GREEN

- Extend the existing host's service descriptors with resolved owner home.
- Materialize enabled registered services from the already known served-profile managers.
- Apply profile home and `build_profile_secret_scope(home)` around factory/run/health calls.
- Preserve current host thread, stop, join, health, reload, and timeout semantics.

**Atomic commit:** `fix(plugins): host background services by profile`

## Task 4 — Plugin: establish the separately installed proof consumer

**Repository:** standalone plugin
**Owns:**

- `pyproject.toml`
- `README.md`
- `LICENSE`
- `src/hermes_gitlab_icm/__init__.py`
- `src/hermes_gitlab_icm/plugin.py`
- `tests/test_plugin_registration.py`

### RED

Build/install a skeletal wheel in a clean venv against the core Task 1–3 checkout. Add tests proving discovery metadata alone has no side effect and opt-in load attempts to register exactly:

- scheme `gitlab+icm`;
- one service named `gitlab-icm-inbox-v1` for `gateway` and `web` hosts.

Run:

```bash
.venv/bin/python -m pytest tests/test_plugin_registration.py -q
.venv/bin/python -m build
```

Expected RED: package/entry point does not exist.

### GREEN

- Add the entry point and minimal manifest/registration function.
- Register the real channel/inbox factories by import, even though their behavior remains test-stubbed until later plugin tasks.
- Declare only runtime dependencies actually used (`httpx` plus the compatible Hermes distribution); test/build dependencies remain optional extras.
- Document external installation and profile-local enablement without a token in `.env` or raw endpoint.

Do not merge/publish the core seam based on this skeleton. The paired proof gate is Task 14.

**Atomic commit:** `feat(plugin): register GitLab ICM handoff channel`

## Task 5 — Plugin: validate endpoint, town-hall configuration, and credentials

**Repository:** standalone plugin
**Owns:**

- `src/hermes_gitlab_icm/config.py`
- `src/hermes_gitlab_icm/plugin.py`
- `tests/test_config.py`
- `tests/test_plugin_registration.py`

### RED

Add table-driven tests for:

- closed endpoint and town-hall/inbox grammars;
- exact HTTPS origin, numeric project ID, protocol v1, bounded poll/retry/retention values, and explicit numeric requester/responder actor IDs;
- rejected HTTP, origin path/query/fragment/userinfo, raw endpoint URL/port/token, unknown keys, duplicate names, empty allowlists, invalid protocol, and unsafe policy limits;
- lazy secret lookup inside the owning profile;
- missing/blank credential and cross-profile credential isolation;
- no credential in repr, validation error, registered descriptor, checkpoint, state, or health.

Run:

```bash
.venv/bin/python -m pytest tests/test_config.py tests/test_plugin_registration.py -q
```

Expected RED: validators and credential resolver do not exist.

### GREEN

- Use frozen dataclasses and stdlib URL parsing; no configuration framework.
- Read only `PluginContext.configuration()`/`get_config()` and existing secret scope.
- Return stable safe failure codes.

**Atomic commit:** `feat(plugin): validate GitLab ICM town halls`

## Task 6 — Plugin: implement the safe narrow GitLab client

**Repository:** standalone plugin
**Owns:**

- `src/hermes_gitlab_icm/client.py`
- `tests/test_client.py`

### RED

Using a scripted local HTTP server, add tests proving:

- every request remains on the configured exact origin/API v4 path;
- `PRIVATE-TOKEN` is sent only to that origin;
- `trust_env=False`, TLS verification, no automatic redirect, and per-operation deadlines;
- all 3xx responses fail without contacting the target;
- `/user` verifies the authenticated numeric actor against the required role;
- paginated issue/note iteration follows validated `Link`/page metadata with a 100-item page ceiling and bounded total pages;
- malformed pagination, cycles, cross-origin links, oversized bodies, invalid/deep JSON, and unsafe content types fail closed;
- 429 honors a bounded parsed `Retry-After`; transport/5xx retry only idempotent reads;
- non-idempotent transport/5xx/timeout is reported `write_ambiguous` and is never replayed;
- errors/log-safe diagnostics omit token, headers, raw bodies, URL query, and credential-shaped data.

Run:

```bash
.venv/bin/python -m pytest tests/test_client.py -q
```

Expected RED: client does not exist.

### GREEN

- Implement only the GitLab calls Stage 4 uses: metadata/version, current user, list/create/update issues, list/create notes, get branch/commit/file, and create commit.
- Port safe ideas from the in-tree Ericsson transport without importing it.
- Centralize request construction and redaction in this file; no generic REST client.

**Atomic commit:** `feat(plugin): add bounded GitLab client`

## Task 7 — Plugin: implement the closed repository and note protocol codec

**Repository:** standalone plugin
**Owns:**

- `src/hermes_gitlab_icm/protocol.py`
- `tests/test_protocol.py`

### RED

Add exhaustive table tests for:

- deterministic branch, marker, root path, event ID, and correlation ID generation;
- canonical request/context/input/result manifests and hashes;
- closed v1 note headers for `claimed`, `started`, `progress`, `question`, `answer`, `cancel-requested`, `cancel-acknowledged`, `completed`, `failed`, and `verification-failed`;
- identical replay equality;
- rejection of traversal, absolute paths, backslashes, dot segments, symlink/submodule modes, unknown keys/version/kind, oversized/deep JSON, duplicate manifest paths, count/size overflow, invalid SHA, mismatched handoff/generation/correlation, edited event content, and control text;
- authenticated API author supplied separately from untrusted body/Git author;
- bounded redacted evidence for malformed input.

Run:

```bash
.venv/bin/python -m pytest tests/test_protocol.py -q
```

Expected RED: protocol codec does not exist.

### GREEN

- Use canonical JSON, hashlib, dataclasses, and closed parsers.
- Keep Markdown human text derived from verified data; never parse free-form comments as commands.
- Do not add protocol extensibility beyond rejecting non-v1 input.

**Atomic commit:** `feat(plugin): define GitLab ICM protocol v1`

## Task 8 — Plugin: submit and reconcile external admission

**Repository:** standalone plugin
**Owns:**

- `src/hermes_gitlab_icm/channel.py`
- `tests/test_channel_submission.py`

### RED

Use a real temporary HandoffStore and scripted GitLab client to test:

- `assess` and `bind` seal only safe town-hall/inbox/capability/version digests;
- request artifacts are created on one deterministic branch/immutable commit before the issue;
- issue marker and request SHA are deterministic;
- lost branch-create response reconciles exact branch/start/request bytes before proceeding;
- lost request-commit response reconciles exact commit/file hashes before proceeding;
- lost issue-create response paginates marker search and verifies issue/project/branch/path/handoff/generation/request SHA;
- authoritative absence permits one retry; ambiguity never causes a blind duplicate;
- duplicate identical marker converges; two valid matches, conflicting payload/SHA, or incomplete bounded search enters `indeterminate`;
- label/assignee/state update failures do not erase authoritative identity;
- each outward effect is fenced by the existing core operation journal and restart resumes reconciliation.

Run:

```bash
.venv/bin/python -m pytest tests/test_channel_submission.py -q
```

Expected RED: adapter does not submit.

### GREEN

- Implement `assess`, `bind`, `submit`, and `reconcile` against the closed client/codec.
- Persist only versioned safe checkpoint facts returned to core.
- Make projection repair separate and idempotent.

**Atomic commit:** `feat(plugin): submit GitLab ICM handoffs safely`

## Task 9 — Plugin: claim, observe, and reconcile machine events

**Repository:** standalone plugin
**Owns:**

- `src/hermes_gitlab_icm/channel.py`
- `src/hermes_gitlab_icm/inbox.py`
- `tests/test_claims.py`
- `tests/test_observation.py`

### RED

Add tests proving:

- claim commit uses the exact expected request/sentinel `last_commit_id` and then reads back the branch/file/history;
- two pollers from the same precondition yield at most one verified claimant in the scripted concurrency model;
- lost claim-commit response reconciles before retry;
- labels/assignee never establish a claim;
- note pages process in stable ascending order with overlap and durable cursor advancement only after event application;
- unauthorized API authors, forged Git authors, system/free-form notes, malformed/oversized notes, stale generation, wrong inbox/request SHA, and mismatched payload hash cannot advance state;
- duplicate identical events collapse;
- same event ID/different payload, conflicting valid claims, or conflicting valid terminals yields `indeterminate` with bounded evidence;
- label/assignee/state drift is repaired from verified facts;
- issue polling handles pagination, equal timestamps, clock overlap, 429, and restart cursor replay without loss.

Run:

```bash
.venv/bin/python -m pytest tests/test_claims.py tests/test_observation.py -q
```

Expected RED: claim and event observation are absent.

### GREEN

- Implement one optimistic claim path; do not add label locks.
- Separate parse, authorize, reduce, and projection repair so untrusted data cannot change state before verification.
- Persist the smallest cursor/index sufficient for replay.

**Atomic commit:** `feat(plugin): reconcile GitLab ICM events`

## Task 10 — Core: resolve registered channels in every handoff consumer

**Repository:** core
**Owns:**

- `hermes_cli/handoff/directory.py`
- `hermes_cli/handoff/supervisor.py`
- `hermes_cli/handoff/cli.py`
- `plugins/workflow/admission.py`
- `plugins/workflow/scheduler.py`
- `tools/bot_mode_dm.py`
- `tui_gateway/methods_agent_handoff.py`
- `tests/hermes_cli/handoff/test_directory.py`
- `tests/hermes_cli/handoff/test_supervisor.py`
- `tests/hermes_cli/handoff/test_bot_conversation_e2e.py`
- `tests/plugins/workflow/test_handoff_executor.py`
- `tests/plugins/workflow/test_remote_handoff_e2e.py`
- `tests/tui_gateway/test_agent_handoff_methods.py`

### RED

With a synthetic registered channel, add tests proving:

- explicit and directory targets validate through the initiating profile's registration/config;
- task capability requirements differ from controlled conversation requirements;
- Workflow admission rejects absent/unconfigured/mismatched channel before trust and scheduler effects;
- Workflow execution, supervisor recovery, Bot creation/follow-up, CLI operations, and TUI gateway selected-profile methods all obtain the same profile-aware service factory;
- a second profile cannot use the first profile's registration or credential scope;
- local, peer, relay, and legacy Bot paths are byte/behavior compatible;
- `message_agent` model schema is unchanged.

Run:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_directory.py \
  tests/hermes_cli/handoff/test_supervisor.py \
  tests/hermes_cli/handoff/test_bot_conversation_e2e.py \
  tests/plugins/workflow/test_handoff_executor.py \
  tests/plugins/workflow/test_remote_handoff_e2e.py \
  tests/tui_gateway/test_agent_handoff_methods.py -q
```

Expected RED: consumers construct fixed built-ins or reject the registered scheme.

### GREEN

- Route construction through Task 2's profile-aware service helper.
- Ask a registered channel to validate its opaque endpoint; core checks registration/ownership only.
- Derive required capabilities from mode and interaction policy.
- Do not import or branch on `gitlab+icm` outside generic parser/registry tests.

**Atomic commit:** `feat(handoff): route registered channels across consumers`

## Task 11 — Core: admit immutable Workflow input artifacts

**Repository:** core
**Owns:**

- `plugins/workflow/language.py`
- `plugins/workflow/language_schema.py`
- `plugins/workflow/admission.py`
- `plugins/workflow/executors/handoff.py`
- `plugins/workflow/machine_contract.py`
- `tests/plugins/workflow/test_language_schema.py`
- `tests/plugins/workflow/test_handoff_executor.py`
- `tests/plugins/workflow/test_remote_handoff_e2e.py`
- Workflow fixtures changed by the tests only

### RED

Add tests proving:

- `assignments.<node>.input_artifacts` accepts a bounded unique list of resolvable upstream output references only;
- graph/reference/trust validation rejects missing, future, cyclic, secret, non-outward, too-large, or unsupported/binary inputs;
- admission requires the channel's `artifact_input` capability;
- executor resolves each value once through normal Workflow output resolution, canonicalizes it, hashes it, and creates the immutable core artifact records;
- retry/restart/reclaim of one semantic generation produces the identical spec fingerprint and bytes;
- a new semantic retry generation may produce a new handoff;
- local/peer assignments without artifacts remain unchanged.

Run:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_language_schema.py \
  tests/plugins/workflow/test_handoff_executor.py \
  tests/plugins/workflow/test_remote_handoff_e2e.py -q
```

Expected RED: the assignment key is rejected or ignored.

### GREEN

- Extend the existing assignment model/schema; do not add a file-transfer service.
- Reuse Workflow's existing reference and canonical structured-output resolution.
- Seal artifact hashes into admission/machine identity and handoff fingerprint.

**Atomic commit:** `feat(workflow): seal handoff input artifacts`

## Task 12 — Core: support transport-neutral questions and answers

**Repository:** core
**Owns:**

- `hermes_cli/handoff/models.py`
- `hermes_cli/handoff/store.py`
- `hermes_cli/handoff/service.py`
- `hermes_cli/handoff/projection.py`
- `plugins/workflow/models.py`
- `plugins/workflow/store.py`
- `plugins/workflow/scheduler.py`
- `plugins/workflow/actions.py`
- `plugins/workflow/cli.py`
- `plugins/workflow/gateway_command.py`
- `plugins/workflow/dashboard/plugin_api.py`
- `plugins/workflow/notifications.py`
- `plugins/workflow/coordinator.py`
- `tools/bot_mode_dm.py`
- `tui_gateway/methods_agent_handoff.py`
- `tests/hermes_cli/handoff/test_models.py`
- `tests/hermes_cli/handoff/test_store.py`
- `tests/hermes_cli/handoff/test_service.py`
- `tests/hermes_cli/handoff/test_bot_conversation_e2e.py`
- `tests/plugins/workflow/test_phase4_loop_interactions.py`
- `tests/plugins/workflow/test_phase4_surfaces.py`
- `tests/plugins/workflow/test_desktop_api.py`
- `tests/plugins/workflow/test_notifications.py`
- `tests/plugins/workflow/test_notification_delivery.py`
- `tests/plugins/workflow/test_coordinator.py`
- `tests/tui_gateway/test_agent_handoff_methods.py`
- `tests/tui_gateway/test_protocol.py`

### RED

Add tests proving:

- a channel observation can create exactly one bounded question interaction with correlation ID and optional finite choices;
- Workflow `pause` creates durable Needs Attention and `provide-input` routes text to exact `command(respond)`;
- existing approval once/session/always/deny mapping is unchanged;
- `deny` and `auto_cancel` policies remain explicit;
- identical answer replay is idempotent; changed answer, stale/mismatched request, unauthorized actor, zero/multiple pending Bot questions, or terminal handoff fails safely;
- Bot follow-up resolves the sole pending question server-side without exposing transport IDs or changing the model schema;
- TUI gateway codecs accept bounded text and derive operator authority server-side;
- restart between durable answer recording and external delivery resumes once;
- projected prompt/answer/evidence is bounded and redacted.

Run:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_models.py \
  tests/hermes_cli/handoff/test_store.py \
  tests/hermes_cli/handoff/test_service.py \
  tests/hermes_cli/handoff/test_bot_conversation_e2e.py \
  tests/plugins/workflow/test_phase4_loop_interactions.py \
  tests/plugins/workflow/test_phase4_surfaces.py \
  tests/plugins/workflow/test_desktop_api.py \
  tests/plugins/workflow/test_notifications.py \
  tests/plugins/workflow/test_notification_delivery.py \
  tests/plugins/workflow/test_coordinator.py \
  tests/tui_gateway/test_agent_handoff_methods.py \
  tests/tui_gateway/test_protocol.py -q
```

Expected RED: response payload accepts approval choices only.

### GREEN

- Add one generic question variant alongside approval, not a new interaction subsystem.
- Reuse existing command IDs, Workflow interaction storage, Needs Attention, notification outbox, and Bot authorization.
- Keep prompt/message history untouched; no synthetic mid-loop user message.

**Atomic commit:** `feat(handoff): carry correlated question answers`

## Task 13 — Plugin: deliver questions, commands, cancellation, and verified results

**Repository:** standalone plugin
**Owns:**

- `src/hermes_gitlab_icm/channel.py`
- `src/hermes_gitlab_icm/inbox.py`
- `tests/test_commands.py`
- `tests/test_results.py`

### RED

Add tests proving:

- message/respond/cancel commands produce one deterministic correlated note;
- lost note-create responses reconcile across all pages by event ID/payload hash/authorized author before any retry;
- same correlation plus conflicting answer is `indeterminate`;
- question → core `needs_input` → answer → resumed progress survives restart;
- cancellation is cooperative and remains `cancelling` until verified acknowledgement/terminal;
- completion racing cancel follows verified event order; contradictory valid terminals are `indeterminate`;
- result fetch uses the exact event-named immutable commit, never branch HEAD;
- manifest/path/count/size/media/hash/request/generation/result-correlation validation runs before the existing structured-output contract;
- hash/schema/malformed/unauthorized failures cannot become success and expose only redacted evidence;
- lost result-commit response and lost completion-note response reconcile without a second terminal publication;
- failed label/close projection is repaired without changing terminal truth.

Run:

```bash
.venv/bin/python -m pytest tests/test_commands.py tests/test_results.py -q
```

Expected RED: command/result behavior is absent.

### GREEN

- Implement `deliver_command`, remaining `observe`, and result verification through existing client/codec.
- Return bounded validated core observations only.
- Never persist raw note/result/error bodies outside the bounded core value allowed by policy.

**Atomic commit:** `feat(plugin): complete GitLab ICM interactions`

## Task 14 — Plugin: run the profile-owned inbox through the existing local handoff service

**Repository:** standalone plugin
**Owns:**

- `src/hermes_gitlab_icm/inbox.py`
- `src/hermes_gitlab_icm/plugin.py`
- `tests/test_inbox.py`
- `tests/test_inbox_restart_e2e.py`

### RED

With a real temp profile, HandoffStore, plugin state, and deterministic fake local Runs service, add tests proving:

- only the explicitly configured destination profile polls its inbox;
- authenticated/authorized, unclaimed, valid requests can be claimed;
- each external handoff/generation maps to one stable local handoff key and `hermes://local/<own-profile>` task;
- crash after claim but before local `create`, after local `create` but before index save, during local execution, and after result commit but before completion note converges without duplicate execution/publication;
- local questions/approvals/cancellation map to exact protocol events/commands;
- destination restart reconstructs from GitLab facts + HandoffStore + bounded plugin state;
- malformed/unauthorized input never reaches the local agent;
- profile tools, memory, approvals, and credentials remain those of the receiving profile;
- retention removes only expired plugin cursor/index entries after their core handoffs are terminal; it never deletes core handoff evidence, GitLab protocol facts, or active correlations;
- stop/reload/shutdown interrupts polling and joins cleanly.

Run:

```bash
.venv/bin/python -m pytest tests/test_inbox.py tests/test_inbox_restart_e2e.py -q
```

Expected RED: inbox service does not execute requests.

### GREEN

- Implement one bounded polling step per interval using the existing background-service stop event.
- Create/reopen deterministic local handoffs through the installed core service factory.
- Store only cursor/index facts in `PluginContext.state`; HandoffStore remains execution truth.

**Atomic commit:** `feat(plugin): run profile GitLab ICM inboxes`

## Task 15 — Core: project generic endpoint strategy and question UI in Desktop

**Repository:** core
**Owns:**

- `apps/desktop/src/plugins/hermes-bots/handoffs.tsx`
- `apps/desktop/src/plugins/hermes-bots/handoffs.test.tsx`
- `apps/desktop/src/plugins/hermes-bots/plugin-panes.test.tsx`

### RED

Add component tests proving:

- directory entries containing `gitlab+icm://townhall/inbox` can be selected and are sent unchanged to `agent_handoff.create`;
- the UI shows a generic channel badge and safe server-provided issue reference without parsing GitLab data;
- a valid safe link opens through the existing external-link policy; unsafe/missing links render as text only;
- finite approvals retain existing buttons;
- free-form questions render a bounded text control and send exact request ID/text only to `agent_handoff.command`;
- submit is disabled for blank/oversized input and duplicate clicks do not duplicate a command;
- endpoint/question failures do not damage the embedded TUI or other Bot panes.

Run:

```bash
cd apps/desktop
npx vitest run \
  src/plugins/hermes-bots/handoffs.test.tsx \
  src/plugins/hermes-bots/plugin-panes.test.tsx
npx tsc -p . --noEmit
```

Expected RED: the generic question control/reference projection is absent.

### GREEN

- Extend only transport-neutral TypeScript interfaces and existing pane.
- Keep state local unless shared behavior already has a nanostore owner.
- Do not import GitLab packages, call GitLab, or rebuild the chat surface.

**Atomic commit:** `feat(desktop): operate external handoff endpoints`

## Task 16 — Core: prove a separately installed channel and background service

**Repository:** core
**Owns:**

- `tests/providers/test_entry_point_discovery.py`
- `tests/hermes_cli/test_plugin_background_services.py`
- `tests/hermes_cli/test_plugin_handoff_channels.py`
- `tests/hermes_cli/handoff/test_installed_channel_e2e.py` (new)
- minimal fixture distribution under the existing test fixture convention

### RED

Build/install a tiny fixture distribution outside the source import path. Prove:

- entry-point metadata discovery does not import it;
- enabling it registers one synthetic scheme and one profile-owned background service;
- the installed channel completes one handoff through create/advance/get/evidence;
- restart reloads its bounded checkpoint and service cursor;
- a secondary profile remains isolated;
- disabling/uninstalling it leaves local/peer behavior intact;
- the core wheel contains no fixture/plugin implementation.

Run:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/providers/test_entry_point_discovery.py \
  tests/hermes_cli/test_plugin_background_services.py \
  tests/hermes_cli/test_plugin_handoff_channels.py \
  tests/hermes_cli/handoff/test_installed_channel_e2e.py -m integration -q
```

Expected RED: installed plugin cannot register/host/resolve a handoff channel.

### GREEN

- Reuse existing installed-distribution fixture/build helpers.
- Keep the fixture synthetic and protocol-neutral; it is contract proof, not a second product.

**Atomic commit:** `test(handoff): prove installed channel lifecycle`

## Task 17 — Plugin: exhaustive ambiguity and security regression matrix

**Repository:** standalone plugin
**Owns:**

- `tests/test_failure_matrix.py`
- production files only if a failing case reveals a missing behavior

### RED

Table-drive failure injection at every outward-effect boundary:

| Boundary | Inject after remote effect | Required recovery |
|---|---|---|
| branch/request commit create | response dropped | fetch exact ref/files/hash; no second conflicting commit |
| issue create | response dropped | paginate marker search; verify immutable request SHA |
| claim commit | response dropped | read exact claim fact/history; never claim again blindly |
| claimed/started/progress note | response dropped | find deterministic event ID/hash/author |
| question/answer note | response dropped | find exact correlation/event; reject conflicting replay |
| cancel request/ack note | response dropped | reconcile exact command/event; remain cooperative |
| result commit | response dropped | fetch exact result files/manifest/hash |
| completion/failure note | response dropped | reconcile terminal event; never publish a second terminal |
| label/assignee/state projection | response dropped | recompute/repair from authoritative facts |
| local destination create/index save | crash between steps | stable key reopens one local handoff |

Also cover duplicate markers, conflicting request payloads, pagination limits/cycles, Retry-After/date parsing, 429 storms, unauthorized actors, forged authors, malformed notes, cursor corruption/restart, label drift, two-poller claims, immutable-ref substitution, hash/schema failure, conflicting terminals, unsafe redirects, ambient proxy variables, credential isolation, and redacted evidence.

Run:

```bash
.venv/bin/python -m pytest tests/test_failure_matrix.py -q
.venv/bin/python -m pytest -q
```

Expected RED: each new injection first demonstrates an unsafe retry, leak, or incomplete reconciliation.

### GREEN

Fix each failure at the shared operation boundary. Do not add per-test special cases or a retry framework.

**Atomic commit:** `test(plugin): close GitLab ICM ambiguity boundaries`

## Task 18 — Cross-repository: installed artifact compatibility gate

This is two atomic commits, one per repository; do not combine histories.

### Task 18A — Plugin installed-artifact test

**Repository:** standalone plugin
**Owns:**

- `tests/test_installed_distribution.py`
- build metadata only if required

Build core wheel/sdist from the exact candidate commit, then build the plugin wheel/sdist. In a clean venv install both artifacts—not editable checkouts—and prove:

- metadata-only discovery;
- opt-in registration of one channel and one service;
- profile config/secret resolution;
- synthetic submit/reconcile/observe/command/restart;
- clean unload/shutdown;
- no source-tree imports.

Run:

```bash
.venv/bin/python -m build
.venv/bin/python -m pytest tests/test_installed_distribution.py -q
```

**Atomic commit:** `test(plugin): verify installed Hermes integration`

### Task 18B — Core artifact boundary test

**Repository:** core
**Owns:**

- `tests/hermes_cli/handoff/test_installed_channel_e2e.py`
- existing packaging test only if needed

Run the fixture test against the core wheel and inspect artifact members to prove no GitLab+ICM code is bundled.

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_installed_channel_e2e.py -m integration -q
```

**Atomic commit:** `test(packaging): keep handoff plugins external`

Neither repository is releasable until both commits pass against one another's exact candidate SHA and record those SHAs in the final review evidence.

## Task 19 — Plugin: real authenticated disposable GitLab project gate

**Repository:** standalone plugin
**Prerequisite:** provisioned by an authorized human after plan approval; never provision automatically in this planning session.
**Owns:**

- `tests/test_gitlab_live.py`
- `tests/README.md` or existing test-run documentation

### RED

Against the exact supported GitLab deployment, first prove the test harness creates isolated branch/issue prefixes, authenticates three tokens through `/user`, verifies numeric identities/permissions/version, and cleans only its own resources.

Then run live scenarios for:

1. submit → claim → execute → immutable result → structured success;
2. dropped branch/commit/issue/note responses through a fault proxy;
3. duplicate marker and conflicting request SHA;
4. enough issues/notes to force pagination;
5. controlled 429/Retry-After or a rate-test fixture approved for the deployment;
6. label/assignee/state drift and repair;
7. two independent pollers racing the same `last_commit_id` claim precondition;
8. unauthorized note author and forged Git author;
9. malformed/oversized/stale machine notes;
10. restart from saved issue/note cursors;
11. question/answer and approval;
12. cancellation before claim, during execution, and racing completion;
13. branch-head movement after a terminal note while exact result SHA remains valid;
14. hash, manifest, and structured-schema failures;
15. conflicting terminal notes;
16. unsafe redirect with proof that no credential reaches the sink;
17. two profiles using distinct credentials with no cross-observation;
18. bounded redacted evidence.

Run:

```bash
GITLAB_ICM_LIVE=1 \
.venv/bin/python -m pytest tests/test_gitlab_live.py -m gitlab_live -q
```

Expected RED at the claim subtest until the deployment demonstrates that one contender wins. If both claims can become valid, stop implementation and return to architecture review. Do not substitute labels or timing locks.

### GREEN

Make only compatibility corrections supported by official documentation and observed behavior. Record:

- GitLab version, offering, tier, and API v4;
- project visibility and token type;
- numeric actor IDs only in the private test record;
- exact candidate core/plugin SHAs;
- scenario counts/timings and cleanup result;
- no token or raw sensitive payload.

**Atomic commit:** `test(plugin): gate real GitLab ICM protocol`

## Task 20 — Core: full focused and regression verification

**Repository:** core
**Owns:** no production changes; fix only a demonstrated Stage 4 regression in a separately reviewed task.

Run the original planning baseline exactly:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_models.py \
  tests/hermes_cli/handoff/test_store.py \
  tests/hermes_cli/handoff/test_service.py \
  tests/hermes_cli/handoff/test_directory.py \
  tests/hermes_cli/handoff/test_supervisor.py \
  tests/hermes_cli/handoff/test_bot_conversation_e2e.py \
  tests/hermes_cli/handoff/test_bot_return_recovery_e2e.py \
  tests/hermes_cli/test_plugin_background_services.py \
  tests/gateway/test_plugin_background_services.py \
  tests/hermes_cli/test_plugin_ownership_ledger.py \
  tests/providers/test_entry_point_discovery.py \
  tests/plugins/workflow/test_handoff_executor.py \
  tests/plugins/workflow/test_remote_handoff_e2e.py \
  tests/tui_gateway/test_agent_handoff_methods.py \
  tests/tui_gateway/test_protocol.py -q
```

Run installed Workflow distribution verification:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_installed_distribution_e2e.py \
  -m integration -q
```

Run all new/adjacent core suites:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff \
  tests/hermes_cli/test_plugin_handoff_channels.py \
  tests/hermes_cli/test_plugin_background_services.py \
  tests/gateway/test_plugin_background_services.py \
  tests/hermes_cli/test_plugin_ownership_ledger.py \
  tests/providers/test_entry_point_discovery.py \
  tests/plugins/workflow/test_language_schema.py \
  tests/plugins/workflow/test_handoff_executor.py \
  tests/plugins/workflow/test_remote_handoff_e2e.py \
  tests/plugins/workflow/test_phase4_loop_interactions.py \
  tests/plugins/workflow/test_phase4_surfaces.py \
  tests/plugins/workflow/test_desktop_api.py \
  tests/tui_gateway/test_agent_handoff_methods.py \
  tests/tui_gateway/test_protocol.py -q
```

Run Desktop verification:

```bash
cd apps/desktop
npx vitest run \
  src/plugins/hermes-bots/handoffs.test.tsx \
  src/plugins/hermes-bots/plugin-panes.test.tsx
npx tsc -p . --noEmit
```

Run the Workflow release gate if Stage 4 changes any file selected by it. Do not dismiss the known macOS bus-error diagnostic unless Stage 4 reproduces or worsens it.

No commit is expected for a clean verification task.

## Task 21 — Plugin: full verification and artifact inspection

**Repository:** standalone plugin
**Owns:** no production changes.

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m build
python -m venv /tmp/hermes-gitlab-icm-verify-venv
/tmp/hermes-gitlab-icm-verify-venv/bin/python -m pip install \
  <exact-core-wheel> <exact-plugin-wheel>
/tmp/hermes-gitlab-icm-verify-venv/bin/python -m pytest \
  tests/test_installed_distribution.py -q
GITLAB_ICM_LIVE=1 \
.venv/bin/python -m pytest tests/test_gitlab_live.py -m gitlab_live -q
```

Use a unique `mktemp -d` path in execution rather than the illustrative `/tmp` path if collision is possible. Inspect wheel/sdist member lists and package metadata. Record exact dependency versions and candidate SHAs. No commit is expected for a clean verification task.

## Task 22 — Independent adversarial review gate

**Repositories:** both, read-only unless a finding is accepted into a new TDD task.

Create a review packet containing:

- accepted proposal, readiness assessment, and this plan;
- exact core/plugin commit ranges and `git diff --stat`/`git diff`;
- all focused, installed-artifact, full, Desktop, and live GitLab test evidence;
- GitLab deployment/version/tier and the documented-vs-inferred claim analysis;
- known inherited diagnostics and every waived/non-waived risk.

Submit the same prompt independently to Claude and Codex:

```text
Perform an adversarial, evidence-based code review of Stage 4 Shared Agent
Handoff across the Hermes core candidate and the separately installed
GitLab+ICM plugin candidate. The consolidated proposal is the architecture
authority; the Stage 4 readiness assessment and implementation plan define
the accepted scope.

Trace live call paths. Do not infer correctness from green mocks. Verify:

1. the core exposes only the minimum profile-local channel registration and
   existing background-service lifecycle integration;
2. no GitLab type, credential, API call, protocol marker, or product policy
   entered core, Workflow, Bot, TUI, or Desktop;
3. the external distribution really registers one channel and one
   profile-owned service after opt-in, including secondary served profiles,
   reload, restart, shutdown, and credential isolation;
4. endpoint/config validation prevents raw URLs, hosts, ports, credentials,
   userinfo, query strings, fragments, traversal, and cross-origin routing;
5. submission never blindly duplicates after a lost branch, commit, issue,
   note, projection, or terminal response;
6. the claim fence is repository-level and the real two-poller GitLab test
   proves at most one valid claimant on the declared deployment;
7. API author IDs—not Git authors or note claims—govern authority;
8. malformed, unauthorized, replayed, paginated, rate-limited, drifted, and
   conflicting facts reduce deterministically, with valid claim/terminal
   conflicts becoming indeterminate;
9. questions/answers, cancellation races, semantic retry generations,
   restart cursors, immutable result retrieval, manifest/hash/schema checks,
   Workflow Needs Attention, Bot return delivery, and Desktop/TUI operations
   preserve the Stage 1–3 invariants;
10. HTTP disables ambient proxies and redirects, preserves TLS validation,
    never forwards credentials, and produces bounded redacted evidence;
11. prompt caching, message-role alternation, model tool schema, profile
    isolation, operation fencing, and legacy local/peer/relay behavior remain
    unchanged;
12. installed core and plugin artifacts prove the repository boundary.

Classify findings BLOCKER/HIGH/MEDIUM/LOW with exact file:line evidence,
runtime consequence, reproduction, and smallest complete fix. Distinguish
documented GitLab guarantees from implementation inference and version/tier
variability. A PASS requires zero unresolved BLOCKER or HIGH findings and no
missing real-path evidence for an ambiguity/security boundary.
```

Reconcile both reviews in a new dated document. Every accepted BLOCKER/HIGH finding becomes a new failing test and atomic fix commit in its owning repository, followed by both full verification tasks and another independent review. Do not close a finding by prose alone.

**Exit gate:** both reviewers independently PASS, reconciliation has no unresolved BLOCKER/HIGH, all artifact/live gates pass, and the checkout returns to core `base`. No release or publication is part of Stage 4 implementation unless separately authorized.

## Required completion report for the future implementation session

Report:

- final readiness and review verdicts;
- exact core and plugin repositories, branches, and commit SHAs;
- task-to-commit map;
- every test command, collected count, pass/fail/skip result, and duration;
- exact GitLab version/offering/tier and live-gate result without secrets;
- installed artifact names/hashes and compatibility pair;
- unresolved risks, inherited diagnostics, and platform gaps;
- proof unrelated worktree files were untouched;
- confirmation core checkout is on `base` and plugin checkout is on its approved default branch.
