# Owner-Bound Plugin Application Command Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task by task. Use `superpowers:test-driven-development` for every behavior change and `superpowers:verification-before-completion` before reporting success.

**Goal:** Let one trusted plugin expose an always-visible shell command that invokes an enabled provider plugin through a host-owned, profile-bound local command authority, without importing provider internals or weakening Hermes' private model-tool admission.

**Architecture:** Add a small synchronous application-command port beside, not inside, the tool registry. An enabled provider registers its canonical operations and their read/write classification against its own plugin identity, plus an explicit caller allowlist. A caller invokes the provider through its own `PluginContext`; Hermes derives both identities, snapshots and canonicalizes the arguments, binds the active connector capability fingerprint, creates an unforgeable single-use invocation, calls the provider once, and bounds the JSON-native result. The existing `PluginToolAdmission`, `pre_tool_call` hook, tool registry, and model path remain unchanged. Top-level plugin CLI command names also become collision-safe so a second plugin cannot replace an already registered command tree.

**Tech Stack:** Python 3.11+, existing `PluginContext`/`PluginManager`, existing connector capability fingerprints, argparse plugin CLI registration, pytest through `scripts/run_tests.sh` only.

**Design:** `ericsson-capabilities:docs/superpowers/specs/2026-08-16-ericsson-connector-cli-design.md`, especially §§3, 5.1, 5.3, and 6.

**Repo:** `hermes-agent` (this repo), neutral `base` branch.

## Global Constraints

- **Test runner:** use `scripts/run_tests.sh` only. Never invoke pytest directly.
- **Neutral branch:** branch from and target `base`; never branch from or commit to literal `main` or a brand branch.
- **Generic host surface:** no Ericsson, Jira, GitLab, Confluence, ARM, OTTO, LOOP24, or SuperCLI-specific logic in `hermes_cli/`.
- **Separate authorities:** do not make `PluginToolAdmission` constructible, reusable, or accepted by the new port. Do not route the local command through `tools.registry.dispatch()` or `resolve_pre_tool_admission()`.
- **Owner-bound registration:** a provider registers only for its own canonical plugin id (`manifest.key or manifest.name`). A caller identity is also derived from its context, never passed as a string by plugin code.
- **Explicit consumers:** only a provider-declared caller id may invoke it. Missing provider, disallowed caller, operation mismatch, and mode mismatch fail before the handler runs.
- **Exact mutation mode:** reads accept only `read`; writes accept only `dry_run` or `confirm`. There is no boolean `approved`, implicit default, environment override, or interactive prompt in the port.
- **Fresh profile binding:** compute a fresh credential-free connector capability fingerprint immediately before each invocation. Never cache configuration, credentials, clients, or the fingerprint in a registration.
- **Argument binding:** accept JSON-native mappings only, reject NaN/infinity and unsupported objects, canonicalize with sorted compact JSON, copy by decode, and bind SHA-256 before dispatch. Caller mutation after invocation starts cannot alter the provider's copy.
- **One invocation:** the host invocation object is unconstructible outside Hermes, immutable, active only for one synchronous handler call, and invalid after that call returns or raises.
- **Bounded result:** accept a JSON-native mapping only, reject NaN/infinity and unsupported objects, and cap canonical UTF-8 output at 1 MiB. Do not stringify arbitrary objects or leak exception messages across the port.
- **Atomic CLI families:** if a plugin's `register()` raises after adding one or more CLI commands, the manager removes only that plugin's additions. A four-command facade cannot remain partially registered after a later collision.
- **No startup regression:** `hermes --help` and known built-in commands keep their current lazy-discovery behavior. This plan adds no eager connector import.
- **No connector implementation:** this wave lands only the generic host port and its documentation. Ericsson providers and command trees are Wave 4B.

## Decisions Taken

| # | Decision | Rationale |
|---|---|---|
| D1 | Add `hermes_cli/plugin_application_commands.py` rather than growing `tools.registry` | Shell commands are deterministic local adapters, not model tools. Combining the paths would either fabricate model admission or accidentally run model hooks. |
| D2 | One provider registration per canonical plugin id | Ownership is then structural. A facade cannot register an executor on behalf of a disabled connector. |
| D3 | Provider declares `operation -> read/write` and `allowed_callers` | The host can reject mode confusion and cross-plugin confused-deputy calls before provider code runs. |
| D4 | Invocation is synchronous | Current plugin CLI handlers and all four planned connector operations are synchronous. Async support would add lifecycle ambiguity without a Wave 4 consumer. |
| D5 | Use `connector_capability_snapshot().scoped_fingerprint(...)` | It already creates a credential-free identity of current profile enablement, settings identities, secret presence, readiness, and registered tools. |
| D6 | Duplicate top-level CLI names raise | Last-writer-wins lets a plugin shadow another plugin's command surface. The Ericsson facade needs names to be reserved atomically. |
| D7 | Provider exceptions become a stable generic execution error | Raw exception text may contain URLs, paths, or vendor payloads. Connector providers return their own safe classified envelopes for expected failures. |

## Public Interface Delivered

`hermes_cli.plugin_application_commands` exports:

```python
class PluginApplicationCommandError(RuntimeError):
    category: str

class PluginApplicationCommandRegistrationError(PluginApplicationCommandError): ...
class PluginApplicationCommandUnavailable(PluginApplicationCommandError): ...
class PluginApplicationCommandDenied(PluginApplicationCommandError): ...
class PluginApplicationCommandInvalid(PluginApplicationCommandError): ...
class PluginApplicationCommandExecutionError(PluginApplicationCommandError): ...

class PluginApplicationCommandMode(str, Enum):
    READ = "read"
    DRY_RUN = "dry_run"
    CONFIRM = "confirm"

class PluginApplicationCommandInvocation:
    provider_id: str
    caller_id: str
    operation: str
    arguments: Mapping[str, Any]
    mode: PluginApplicationCommandMode
    invocation_id: str
    arguments_sha256: str
    profile_fingerprint: str
    active: bool

ApplicationCommandHandler = Callable[
    [PluginApplicationCommandInvocation], Mapping[str, Any]
]
```

`PluginContext` exports:

```python
def register_application_commands(
    self,
    *,
    operations: Mapping[str, str],       # values are exactly "read" or "write"
    allowed_callers: Collection[str],
    handler: ApplicationCommandHandler,
) -> None: ...

def invoke_application_command(
    self,
    provider_id: str,
    operation: str,
    arguments: Mapping[str, Any],
    *,
    mode: str,
    invocation_id: str,
) -> Mapping[str, Any]: ...
```

The invocation constructor is guarded by a module-private mint key. Its public representation contains identities and digests but never arguments, result data, secrets, or a registration token.

## File Structure

| File | Responsibility |
|---|---|
| **Create** `hermes_cli/plugin_application_commands.py` | Validation, canonicalization, immutable invocation, registration record, dispatch, stable errors, result bound. |
| **Modify** `hermes_cli/plugins.py` | `PluginContext` public methods, manager registry/lifecycle integration, collision-safe CLI command registration. |
| **Create** `tests/hermes_cli/test_plugin_application_commands.py` | Port behavior, authorization, profile binding, single-use lifetime, bounds, exception safety. |
| **Modify** `tests/hermes_cli/test_plugin_cli_registration.py` | Duplicate command collision and canonical owner behavior. |
| **Modify** `tests/hermes_cli/test_plugins.py` | Force-discovery cleanup for application-command registrations. |
| **Modify** `tests/hermes_cli/test_plugin_tool_admission.py` | Independence regression: neither authority is accepted by the other path. |
| **Modify** `website/docs/developer-guide/plugins/index.md` | Public API, security model, provider/caller example. |
| **Modify** `website/docs/developer-guide/extending-the-cli.md` | Point shell subcommands to plugin registration and application-command port; retain TUI wrapper guidance. |

---

### Task 1: Make top-level plugin CLI registration collision-safe

**Files:**
- Modify: `hermes_cli/plugins.py:634-656`
- Modify: `tests/hermes_cli/test_plugin_cli_registration.py`
- Modify: `tests/hermes_cli/test_plugins.py`

**Produces:**
- `class PluginCliCommandCollisionError(RuntimeError)`
- duplicate command names fail closed without replacing the first entry
- failed plugin registration rolls back CLI commands added during that one registration attempt

- [ ] **Step 1: Write the failing collision tests**

Replace `TestRegisterCliCommand.test_overwrites_on_duplicate` with three tests:

```python
def test_duplicate_from_other_plugin_raises_without_replacement(self):
    ctx, mgr = self._make_ctx()
    first = MagicMock()
    ctx.register_cli_command("x", "first", first)
    other = PluginContext(PluginManifest(name="other"), mgr)

    with pytest.raises(PluginCliCommandCollisionError, match="already registered"):
        other.register_cli_command("x", "second", MagicMock())

    assert mgr._cli_commands["x"]["setup_fn"] is first
    assert mgr._cli_commands["x"]["plugin"] == "test-plugin"

def test_duplicate_from_same_plugin_also_raises(self):
    ctx, mgr = self._make_ctx()
    ctx.register_cli_command("x", "first", MagicMock())
    with pytest.raises(PluginCliCommandCollisionError):
        ctx.register_cli_command("x", "second", MagicMock())

def test_command_owner_uses_canonical_manifest_key(self):
    mgr = PluginManager()
    ctx = PluginContext(PluginManifest(name="Display Name", key="stable-id"), mgr)
    ctx.register_cli_command("x", "help", MagicMock())
    assert mgr._cli_commands["x"]["plugin"] == "stable-id"
```

Add a manager-load regression fixture whose plugin `register()` successfully adds
`alpha`, then attempts a colliding `occupied` command and raises. Pre-register
`occupied` from another plugin. Assert `occupied` remains owned by the first plugin
and `alpha` is absent after the failed load. Also assert an unrelated pre-existing
command remains untouched.

Import `pytest` and `PluginCliCommandCollisionError` in the test module.

- [ ] **Step 2: Run the focused test and confirm the intended failure**

Run:

```bash
scripts/run_tests.sh tests/hermes_cli/test_plugin_cli_registration.py -q
```

Expected: FAIL because the exception does not exist and duplicate registration currently overwrites.

- [ ] **Step 3: Implement collision-safe registration**

In `hermes_cli/plugins.py`, define `PluginCliCommandCollisionError` beside the other plugin registration errors. In `PluginContext.register_cli_command`, validate `name` is a non-empty string, derive `plugin_id = self.manifest.key or self.manifest.name`, check `_cli_commands` before assignment, and raise without mutation if the name exists. Store the canonical `plugin_id` in the entry.

In the directory/entry-point plugin load path, snapshot `_cli_commands` before
calling `register_fn(ctx)`. If registration raises, remove only command names that
were absent from the snapshot and are owned by the failing canonical plugin id.
Never restore the whole dictionary because another thread/plugin may have registered
an unrelated command. Apply the same cleanup discipline already used there for
background services and setup actions.

Do not add an override flag. Force discovery already clears `_cli_commands` before re-registration.

- [ ] **Step 4: Run the focused test**

Run the same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hermes_cli/plugins.py tests/hermes_cli/test_plugin_cli_registration.py tests/hermes_cli/test_plugins.py
git commit -m "fix: reject plugin CLI command collisions"
```

---

### Task 2: Build the immutable command model and validation primitives

**Files:**
- Create: `hermes_cli/plugin_application_commands.py`
- Create: `tests/hermes_cli/test_plugin_application_commands.py`

**Produces:** the errors, mode enum, invocation type, registration type, argument canonicalizer, and bounded result validator described in **Public Interface Delivered**.

- [ ] **Step 1: Write failing unit tests for the pure model**

Create `tests/hermes_cli/test_plugin_application_commands.py` with a `TestCommandModel` group proving:

1. `PluginApplicationCommandMode` has only `read`, `dry_run`, and `confirm`.
2. Direct construction of `PluginApplicationCommandInvocation` without the private mint key raises `TypeError`.
3. A helper used only by this test module through the module's private test seam mints an invocation whose attributes are immutable.
4. `repr(invocation)` includes provider, caller, operation, mode, invocation id, argument digest, and profile fingerprint, but excludes an argument value such as `distinctive-secret-marker`.
5. Canonical dictionaries with different insertion order produce the same digest.
6. Mutating the caller's nested dictionary after canonicalization does not alter the invocation arguments.
7. Unsupported objects, non-string mapping keys, NaN, infinity, an empty invocation id, and an invocation id longer than 128 characters raise `PluginApplicationCommandInvalid`.
8. A result must be a mapping and canonical JSON at or below `1_048_576` UTF-8 bytes; one byte over raises `PluginApplicationCommandExecutionError`.

Use a module-private `_mint_invocation_for_test(...)` only if needed to test the value object without exposing a production constructor. The helper must be prefixed `_` and omitted from `__all__`.

- [ ] **Step 2: Confirm the module is absent**

Run:

```bash
scripts/run_tests.sh tests/hermes_cli/test_plugin_application_commands.py -q
```

Expected: FAIL with `ModuleNotFoundError: hermes_cli.plugin_application_commands`.

- [ ] **Step 3: Implement the pure model**

Create `hermes_cli/plugin_application_commands.py` with:

- a private `_APPLICATION_COMMAND_MINT_KEY = object()`;
- constants `_MAX_ARGUMENT_BYTES = 1_048_576`, `_MAX_RESULT_BYTES = 1_048_576`, `_MAX_ID_LENGTH = 128`;
- stable error classes whose constructors set only a constant safe message and `.category` (`registration`, `unavailable`, `permission`, `invalid_input`, `transient`);
- exact enum parsing, with booleans rejected;
- `_canonical_json_copy(mapping, maximum)` using `json.dumps(... sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)` followed by UTF-8 sizing and `json.loads`;
- SHA-256 over those exact canonical bytes;
- a manually guarded immutable invocation (`__slots__`, guarded constructor, blocked `__setattr__`) whose arguments are the decoded private copy exposed through `MappingProxyType`;
- a private active-state closure protected by `threading.Lock`; dispatch activates exactly once and closes in `finally`;
- a frozen `_ApplicationCommandRegistration` holding provider id, immutable operation classification, immutable allowed callers, handler, and a private registration token;
- validation functions for registration and result mappings.

Reject operation/provider/caller ids unless they match `[a-z0-9][a-z0-9._-]{0,127}`. Reject operations not declared exactly as `read` or `write`. Reject an empty caller allowlist.

- [ ] **Step 4: Run the model tests**

Run the same focused command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hermes_cli/plugin_application_commands.py tests/hermes_cli/test_plugin_application_commands.py
git commit -m "feat: add immutable plugin application command model"
```

---

### Task 3: Register and invoke providers through `PluginContext`

**Files:**
- Modify: `hermes_cli/plugin_application_commands.py`
- Modify: `hermes_cli/plugins.py`
- Modify: `tests/hermes_cli/test_plugin_application_commands.py`
- Modify: `tests/hermes_cli/test_plugins.py`

**Produces:** `PluginContext.register_application_commands(...)` and `PluginContext.invoke_application_command(...)`.

- [ ] **Step 1: Write failing integration tests**

Add tests covering this complete matrix:

| Case | Expected result |
|---|---|
| provider registers read and write operations | immutable registration stored under provider's canonical id |
| same provider registers twice | registration error; first registration preserved |
| a different context attempts to register for provider id | impossible because no provider-id parameter exists |
| allowed caller invokes read with `read` | handler receives one valid invocation and result returns |
| write with `dry_run` | handler receives `DRY_RUN` |
| write with `confirm` | handler receives `CONFIRM` |
| read with either write mode | invalid before handler |
| write with `read` | denied before handler |
| unlisted operation | invalid before handler |
| unlisted caller | denied before handler |
| absent provider | unavailable before configuration snapshot |
| caller mutates arguments concurrently | provider observes only canonical copied arguments |
| handler raises with a secret marker in its message | caller receives stable transient error without marker |
| handler returns a list, object, NaN, or oversized mapping | stable execution error |
| handler captures invocation and reads it after return | invocation reports inactive/closed |
| two threads try to dispatch the same internally captured invocation | handler can be entered only once |
| manager force-clear | provider registrations are removed |

Patch `connector_capability_snapshot` to return a fake snapshot and assert `scoped_fingerprint` is called with `frozenset({provider_id})` and `frozenset({operation})`. Assert the produced fingerprint reaches the invocation.

- [ ] **Step 2: Run and confirm failures**

```bash
scripts/run_tests.sh tests/hermes_cli/test_plugin_application_commands.py tests/hermes_cli/test_plugins.py -q
```

Expected: FAIL because `PluginContext` and `PluginManager` do not yet expose the port.

- [ ] **Step 3: Add the manager registry and context methods**

In `PluginManager.__init__`, add:

```python
self._application_command_providers: Dict[str, Any] = {}
```

Clear it in `_clear_plugin_registries()`.

In `PluginContext`, implement the two public methods exactly as declared above. Both derive the current plugin id from `manifest.key or manifest.name`. Delegate registration and invocation to module-level functions in `plugin_application_commands.py`; do not duplicate validation in `plugins.py`.

The dispatch function must perform these steps in order:

1. validate basic input and canonicalize/copy arguments;
2. look up the provider registration;
3. verify caller allowlist, operation declaration, and read/write mode compatibility;
4. call `connector_capability_snapshot().scoped_fingerprint(frozenset({provider_id}), frozenset({operation}))`;
5. mint the invocation with the current registration token and argument digest;
6. atomically activate the invocation;
7. call the synchronous handler exactly once;
8. validate and copy the bounded result;
9. close the invocation in `finally`;
10. translate unexpected exceptions into `PluginApplicationCommandExecutionError` without embedding `str(exc)`.

Never call `ctx.configuration()` from the host port. The owning provider resolves it inside its handler, which is the existing opaque ownership boundary.

- [ ] **Step 4: Run the focused integration tests**

Run the same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hermes_cli/plugin_application_commands.py hermes_cli/plugins.py tests/hermes_cli/test_plugin_application_commands.py tests/hermes_cli/test_plugins.py
git commit -m "feat: add owner-bound plugin application command dispatch"
```

---

### Task 4: Prove separation from model-tool admission and startup behavior

**Files:**
- Modify: `tests/hermes_cli/test_plugin_application_commands.py`
- Modify: `tests/hermes_cli/test_plugin_tool_admission.py`
- Modify: `tests/hermes_cli/test_startup_plugin_gating.py`

**Produces:** regression evidence for the two authority paths and lazy discovery.

- [ ] **Step 1: Add cross-authority and startup tests**

Add tests proving:

- passing `PluginToolAdmission` as application-command arguments or mode is rejected and never reaches the provider;
- passing a command invocation or command-mode lookalike as `tool_admission` is rejected by `tools.registry.dispatch()`;
- `PluginApplicationCommandInvocation` cannot be passed to or mistaken for `_claim_plugin_tool_admission`;
- invoking an application command does not invoke `pre_tool_call`, `request_tool_approval`, model middleware, or `tools.registry.dispatch`;
- the provider receives no model `PluginToolAdmission` and the caller receives no application invocation object in the result;
- `hermes --help` with plugin discovery suppressed remains unchanged;
- a known built-in command still skips plugin discovery;
- an unknown/plugin CLI command still takes the existing discovery slow path, after which its registered handler can use the application-command port.

The cross-authority tests must use real instances minted through each genuine host path; do not test only dict lookalikes.

- [ ] **Step 2: Run and confirm the tests expose any missing guard**

```bash
scripts/run_tests.sh tests/hermes_cli/test_plugin_application_commands.py tests/hermes_cli/test_plugin_tool_admission.py tests/hermes_cli/test_startup_plugin_gating.py -q
```

Expected: PASS if Tasks 2-3 already established every guard. These are regression-only tests over behavior introduced test-first in the preceding tasks, so do not weaken or temporarily revert correct code merely to manufacture another red phase.

- [ ] **Step 3: Add only the hardening required by the failures**

Keep `PluginToolAdmission` and `_claim_plugin_tool_admission` otherwise byte-for-byte behaviorally compatible. The intended fix, if needed, belongs in the new port's type/mode validation or the existing registry's rejection of a non-`PluginToolAdmission` object—not in a shared permissive adapter.

- [ ] **Step 4: Run the focused security regression set**

Run the command from Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hermes_cli/plugin_application_commands.py hermes_cli/plugins.py tools/registry.py tests/hermes_cli/test_plugin_application_commands.py tests/hermes_cli/test_plugin_tool_admission.py tests/hermes_cli/test_startup_plugin_gating.py
git commit -m "test: isolate local command authority from model admission"
```

Only stage production files that actually changed.

---

### Task 5: Document the shell-command provider/caller pattern

**Files:**
- Modify: `website/docs/developer-guide/plugins/index.md`
- Modify: `website/docs/developer-guide/extending-the-cli.md`

**Produces:** implementation-ready public guidance with explicit non-goals.

- [ ] **Step 1: Add a documentation contract test**

Add `test_application_command_port_is_documented` to `tests/hermes_cli/test_plugin_application_commands.py`. Read both docs and assert they contain:

- `register_application_commands`
- `invoke_application_command`
- `register_cli_command`
- `dry_run`
- `confirm`
- a warning that application commands do not mint or replace model-tool approval
- a warning that a caller must not import another plugin's implementation

Run the focused file. Expected: FAIL because the docs lack the new API.

- [ ] **Step 2: Update the plugin developer guide**

Add an “Application command providers” section after the existing CLI command registration section. Include a generic two-plugin example:

```python
# provider plugin
def register(ctx):
    def execute(invocation):
        configuration = ctx.configuration()  # fresh and owner-only
        return run_operation(
            invocation.operation,
            dict(invocation.arguments),
            configuration,
            mode=invocation.mode.value,
        )

    ctx.register_application_commands(
        operations={"records_get": "read", "records_update": "write"},
        allowed_callers={"records-cli"},
        handler=execute,
    )

# always-loaded caller plugin
result = ctx.invoke_application_command(
    "records-provider",
    "records_update",
    {"record_id": args.record_id, "value": args.value},
    mode="dry_run" if args.dry_run else "confirm",
    invocation_id=str(uuid.uuid4()),
)
```

State that the caller owns parsing/rendering; the provider owns fresh configuration, secrets, and operations; disabled providers are not imported; callers must handle the stable port errors; writes must obtain explicit user intent before requesting `confirm`; and the host port does not invoke model approval hooks.

- [ ] **Step 3: Clarify the extending-CLI guide**

Near the opening, distinguish:

- wrapper-TUI widgets/keybindings/layout/slash processing covered by that page;
- terminal `hermes <command>` trees via `ctx.register_cli_command`;
- cross-plugin deterministic execution via the application-command port.

Do not rewrite or weaken the page's instruction not to override `run()`.

- [ ] **Step 4: Run the documentation contract and markdown hygiene**

```bash
scripts/run_tests.sh tests/hermes_cli/test_plugin_application_commands.py -q
git diff --check
```

Expected: PASS and no whitespace errors.

- [ ] **Step 5: Commit**

```bash
git add website/docs/developer-guide/plugins/index.md website/docs/developer-guide/extending-the-cli.md tests/hermes_cli/test_plugin_application_commands.py
git commit -m "docs: describe owner-bound plugin command providers"
```

---

### Task 6: Full verification and branch handoff

**Files:** no planned production changes.

- [ ] **Step 1: Run the focused port and plugin regressions**

```bash
scripts/run_tests.sh \
  tests/hermes_cli/test_plugin_application_commands.py \
  tests/hermes_cli/test_plugin_cli_registration.py \
  tests/hermes_cli/test_plugin_tool_admission.py \
  tests/hermes_cli/test_plugins.py \
  tests/hermes_cli/test_startup_plugin_gating.py -q
```

Expected: PASS.

- [ ] **Step 2: Run the complete Hermes suite**

```bash
scripts/run_tests.sh
```

Expected: PASS. Any failure must be investigated against the parent `base` commit before it is called pre-existing. Do not inherit a broken test into the baseline without proving it by running the same test at `HEAD~N` where `N` is this plan's commit count.

- [ ] **Step 3: Run static hygiene checks**

```bash
git diff base...HEAD --check
git status --short
git log --oneline base..HEAD
```

Expected: no whitespace errors; only intentional committed changes; one focused commit per task.

- [ ] **Step 4: Push and report; do not merge**

Push `feat/hermes-plugin-application-command-port`. Report:

- commit list;
- focused and full-suite results;
- the exact public API delivered;
- any deviation from this plan;
- confirmation that model-tool admission behavior did not change;
- confirmation that Wave 4B remains blocked until this branch is reviewed and merged to `base`.

Do not merge to `base`, vendor Ericsson content, merge brand branches, or start Wave 4B.

## Self-Review

- The port is intentionally narrower than a general RPC bus: synchronous, in-process, JSON-native, bounded, one provider per plugin, explicit callers.
- The provider id is never caller-supplied at registration, closing the easiest ownership spoof.
- The caller id is never caller-supplied at invocation, closing a confused-deputy bypass.
- The model path remains the sole minter and claimant of `PluginToolAdmission`; the CLI path receives a different unforgeable invocation type.
- Profile binding uses the existing credential-free capability fingerprint, so no secret value enters authority metadata or logs.
- A facade bug can request `confirm` only because the provider explicitly trusts that facade. Wave 4B therefore pins every write parser with required mutually exclusive flags and pre-dispatch side-effect tests.
- Disabled connectors register no provider. The always-visible facade remains loaded and can render enablement guidance without importing disabled code.
- Duplicate top-level CLI registration changes one existing last-writer-wins behavior deliberately; tests pin preservation of the first owner.
