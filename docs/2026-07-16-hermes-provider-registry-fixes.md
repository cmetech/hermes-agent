# Hermes fix-spec: `loop24` provider registry + MoA eligibility (Symptoms A & B)

**Status:** hand-off spec for the **Hermes** repo — not a gateway change.
**Target repo/checkout:** `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`
(active: git HEAD `3b24b7741`, 2026-07-16. The `otto_app/hermes-agent` copy is
stale — last commit 2026-06-14 — and `otto_hermes/` is just the parent folder.)

These two symptoms were reported alongside the gateway model-capability bug.
They live in Hermes, not the gateway. Gateway **FIX #1** (this same branch —
`Enrich` now marks every live kiro model `completion: "supported"`) is expected
to clear **Symptom B** on its own; **Symptom A** is an independent Hermes code
bug that still needs the change below.

---

## Root cause (shared): four provider registries, one is blind to `loop24`

`loop24` is a **plugin** provider. Its routable profile is generated at
brand-build time by `scripts/brand/emitters/provider.mjs` into
`plugins/model-providers/loop24/__init__.py`, which calls
`register_provider(ProviderProfile(name="loop24", …))`. That plugin registry is
consulted by three code paths but **not** by the fourth:

| Registry / path | Knows `loop24`? | Where |
|---|---|---|
| Plugin registry `providers.get_provider_profile` | ✅ | `providers/__init__.py:53-73` |
| Runtime chat routing → `PROVIDER_REGISTRY` | ✅ | `hermes_cli/auth.py:446-476` auto-extends it from plugin providers |
| Model picker / dropdown | ✅ | `hermes_cli/model_switch.py:1631-1636` + live `/v1/models` |
| **`resolve_provider_full`** (in-chat `/model` selector, CLI setup) | ❌ | `hermes_cli/providers.py:709-779` — config.yaml + `HERMES_OVERLAYS` + models.dev only |

The dropdown lists `loop24` (plugin registry) but the selector's resolver
(`resolve_provider_full`) only looks at config.yaml `providers:` /
`custom_providers:` + built-ins — so it can't find `loop24`. That single
non-unified resolver is the root cause of **Symptom A**.

---

## Symptom A — "Unknown provider 'loop24'" when picking a loop24 model

**Error thrown at** `hermes_cli/model_switch.py:870-874`:
```python
_switch_err = (
    f"Unknown provider '{explicit_provider}'. "
    f"Check 'hermes model' for available providers, or define it "
    f"in config.yaml under 'providers:'."
)
```
Reached because the guard above returns `None`:
```python
pdef = resolve_provider_full(explicit_provider, user_providers, custom_providers)  # :862
...
if pdef is None:  # :869
```
`user_providers` is literally `cfg.get("providers")` from config.yaml
(`hermes_cli/web_server.py:1043`; `hermes_cli/main.py:2987`), and
`resolve_provider_full` (`hermes_cli/providers.py:709-779`) resolves only
against config.yaml `providers:` (`resolve_user_provider`, `providers.py:586-622`),
`custom_providers:` (`resolve_custom_provider`), and `HERMES_OVERLAYS` +
models.dev (`get_provider`, `providers.py:410-477`). The `loop24` **plugin**
profile appears in none of them. The identical failure also exists on the CLI
setup path at `hermes_cli/main.py:2985-2998`.

### Fix (real): unify `resolve_provider_full` with the plugin registry
Make `resolve_provider_full` (`hermes_cli/providers.py:709`) consult the plugin
registry as a resolution source, after the existing config/custom/built-in
lookups fail and before returning `None`. Two equivalent options:

- **Preferred:** fall back to the already-merged `PROVIDER_REGISTRY` (which
  `hermes_cli/auth.py:446-476` populates from every plugin provider), or
- call `providers.get_provider_profile(name)` directly and adapt its
  `ProviderProfile` into the shape `resolve_provider_full` returns.

Whichever path, the profile must be mapped into the same return contract as the
config-provider branch (base_url, auth_type, env_vars, fallback_models,
`model_capabilities_path`, etc.) so downstream routing is unchanged. Add a
regression test: `resolve_provider_full("loop24", {}, {})` must return a non-None
profile with `base_url` and `model_capabilities_path` set, using only the plugin
registry (no config.yaml `providers:` entry).

### Workaround (not the fix)
A user can add a `providers: { loop24: { … } }` block to `config.yaml` to make
`resolve_provider_full` see it — but that duplicates the plugin profile and
drifts. Ship the registry unification instead.

---

## Symptom B — MoA preset selection of a loop24 model snaps back to `gpt-5.5`

The `gpt-5.5` it reverts to is the MoA default reference model
(`hermes_cli/moa_config.py:13-16` `DEFAULT_MOA_REFERENCE_MODELS`). The reset is
**server-driven**, not a bare client control:

- `PUT /api/model/moa` → `set_moa_models` (`hermes_cli/web_server.py:5634`) →
  `_validate_changed_moa_slots` (`web_server.py:5528-5631`).
- For any changed slot whose provider **has a capability contract**
  (`_has_capability_contract`, `web_server.py:5549-5551` — true iff
  `get_provider_profile(provider).model_capabilities_path` is set), it calls
  `validate_provider_model_selection` (`hermes_cli/model_eligibility.py:168-227`)
  → `evaluate_model_eligibility` (`model_eligibility.py:91-165`), and on
  ineligibility raises **HTTP 409** (`web_server.py:5629-5630`). The UI then
  reverts the slot to the preset default (`gpt-5.5`). The main-chat equivalent
  gate is `web_server.py:5784-5801`.
- `loop24` trips this because the current brand emitter attaches a contract:
  `scripts/brand/emitters/provider.mjs:46-47` sets
  `model_capabilities_path="model-capabilities"`. `evaluate_model_eligibility`
  returns **ineligible** if the gateway catalog status != `"ready"`
  (`:119-127`), the model isn't in the live list (`:136-141`), or a required
  capability isn't verified `"supported"` (`:143-159`).

### Primary resolution: gateway FIX #1 (already in this branch)
The most common trip was the **required-capability** check
(`model_eligibility.py:143-159`) failing because the gateway returned
`completion: "unknown"` for live-but-unregistered models. Gateway `Enrich` now
returns `completion: "supported"` for every live model, so a loop24 chat/
reference model should pass the gate once the gateway change is deployed.
**Verify B after deploying the gateway build** before doing anything in Hermes.

### Fallback (only if B persists after the gateway deploy)
If B still occurs — e.g. the gateway `/v1/model-capabilities` catalog status
isn't `"ready"`, or the model isn't in Hermes's live-model fetch — relax the
gate for gateway-live models at `model_eligibility.py:136-159`: treat a model
that is present-and-live but lacks *verified* metadata as **eligible** for
`completion` (mirror the legacy short-circuit at `model_eligibility.py:103-110`),
rather than ineligible. Scope this to the completion capability only; keep
tools/vision/reasoning gating intact.

### Housekeeping note (recent regression signal)
The stale compiled profile on disk
(`plugins/model-providers/loop24/__pycache__/__init__.cpython-311.pyc`, built
2026-07-16) predates the `model_capabilities_path` kwarg — under it there was no
contract and no reset. The emitter was updated to add the contract but the
plugin was **not regenerated** in the working tree (`plugins/model-providers/
loop24/` and `.../otto/` contain no `__init__.py`/`plugin.yaml`; neither is
git-tracked). Regenerate the brand plugins so the on-disk profile matches the
emitter before testing B.

---

## Suggested order
1. Deploy the gateway build carrying FIX #1; **re-test Symptom B** — expect it
   cleared.
2. Implement **Symptom A** (unify `resolve_provider_full`) — required regardless.
3. Only if B persists: apply the `model_eligibility.py` fallback.
