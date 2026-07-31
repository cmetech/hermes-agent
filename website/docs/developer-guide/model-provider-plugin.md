---
sidebar_position: 10
title: "Model Provider Plugins"
description: "How to build a model provider (inference backend) plugin for Hermes Agent"
---

# Building a Model Provider Plugin

Model provider plugins declare an inference backend — an OpenAI-compatible endpoint, an Anthropic Messages server, a Codex-style Responses API, or a Bedrock-native surface — that Hermes can route `AIAgent` calls through. Every built-in provider (OpenRouter, Anthropic, GMI, DeepSeek, Nvidia, …) ships as one of these plugins. Third parties can add their own by dropping a directory under `$HERMES_HOME/plugins/model-providers/` with zero changes to the repo.

:::tip
Model provider plugins are the third kind of **provider plugin**. The others are [Memory Provider Plugins](/developer-guide/memory-provider-plugin) (cross-session knowledge) and [Context Engine Plugins](/developer-guide/context-engine-plugin) (context compression strategies). All three follow the same "drop a directory, declare a profile, no repo edits" pattern.
:::

## How discovery works

`providers/__init__.py._discover_providers()` runs lazily the first time any code calls `get_provider_profile()` or `list_providers()`. Discovery order:

1. **Bundled plugins** — `<repo>/plugins/model-providers/<name>/` — ship with Hermes
2. **User plugins** — `$HERMES_HOME/plugins/model-providers/<name>/` — drop in any directory; no restart required for subsequent sessions
3. **Legacy single-file** — `<repo>/providers/<name>.py` — back-compat for out-of-tree editable installs

**User plugins override bundled plugins of the same name** because `register_provider()` is last-writer-wins. Drop a `$HERMES_HOME/plugins/model-providers/gmi/` directory to replace the built-in GMI profile without touching the repo.

## Directory structure

```
plugins/model-providers/my-provider/
├── __init__.py       # Calls register_provider(profile) at module-level
├── plugin.yaml       # kind: model-provider + metadata (optional but recommended)
└── README.md         # Setup instructions (optional)
```

The only required file is `__init__.py`. `plugin.yaml` is used by `hermes plugins` for introspection and by the general PluginManager to route the plugin to the right loader; without it, the general loader falls back to a source-text heuristic.

## Minimal example — a simple API-key provider

```python
# plugins/model-providers/acme-inference/__init__.py
from providers import register_provider
from providers.base import ProviderProfile

acme = ProviderProfile(
    name="acme-inference",
    aliases=("acme",),
    display_name="Acme Inference",
    description="Acme — OpenAI-compatible direct API",
    signup_url="https://acme.example.com/keys",
    env_vars=("ACME_API_KEY", "ACME_BASE_URL"),
    base_url="https://api.acme.example.com/v1",
    auth_type="api_key",
    default_aux_model="acme-small-fast",
    fallback_models=(
        "acme-large-v3",
        "acme-medium-v3",
        "acme-small-fast",
    ),
)

register_provider(acme)
```

```yaml
# plugins/model-providers/acme-inference/plugin.yaml
name: acme-inference
kind: model-provider
version: 1.0.0
description: Acme Inference — OpenAI-compatible direct API
author: Your Name
```

That's it. After dropping these two files, the following **auto-wire** with no other edits:

| Integration | Where | What it gets |
|---|---|---|
| Credential resolution | `hermes_cli/auth.py` | `PROVIDER_REGISTRY["acme-inference"]` populated from profile |
| `--provider` CLI flag | `hermes_cli/main.py` | Accepts `acme-inference` |
| `hermes model` picker | `hermes_cli/models.py` | Appears in `CANONICAL_PROVIDERS`, model list fetched from `{base_url}/models` |
| `hermes doctor` | `hermes_cli/doctor.py` | Health check for `ACME_API_KEY` + `{base_url}/models` probe |
| `hermes setup` | `hermes_cli/config.py` | `ACME_API_KEY` appears in `OPTIONAL_ENV_VARS` and the setup wizard |
| URL reverse-mapping | `agent/model_metadata.py` | Hostname → provider name for auto-detection |
| Auxiliary model | `agent/auxiliary_client.py` | Uses `default_aux_model` for compression / summarization |
| Runtime resolution | `hermes_cli/runtime_provider.py` | Returns correct `base_url`, `api_key`, `api_mode` |
| Transport | `agent/transports/chat_completions.py` | Profile path generates kwargs via `prepare_messages` / `build_extra_body` / `build_api_kwargs_extras` |

## ProviderProfile fields

Full definition in `providers/base.py`. The most useful ones:

| Field | Type | Purpose |
|---|---|---|
| `name` | str | Canonical id — matches `model.provider` in `config.yaml` and the `--provider` flag |
| `aliases` | `tuple[str, ...]` | Alternative names resolved by `get_provider_profile()` (e.g. `grok` → `xai`) |
| `api_mode` | str | `chat_completions` \| `codex_responses` \| `anthropic_messages` \| `bedrock_converse` |
| `display_name` | str | Human label shown in `hermes model` picker |
| `description` | str | Picker subtitle |
| `signup_url` | str | Shown during first-run setup ("get an API key here") |
| `env_vars` | `tuple[str, ...]` | API-key env vars in priority order; a final `*_BASE_URL` entry is used as the user base-URL override |
| `base_url` | str | Default inference endpoint |
| `models_url` | str | Explicit catalog URL (falls back to `{base_url}/models`) |
| `auth_type` | str | `api_key` \| `oauth_device_code` \| `oauth_external` \| `copilot` \| `aws_sdk` \| `external_process` |
| `supports_unauthenticated` | bool | Permits use of a non-secret SDK placeholder when no credential is stored; does not prove the endpoint is reachable or no-auth |
| `model_capabilities_path` | str | Provider-relative verified-capability endpoint; empty keeps legacy selection behavior |
| `structured_output_strategy` | `str \| None` | Provider-owned structured-output declaration. Leave unset for bounded prompt adaptation; declare a native mode only with direct transport proof; use `unsupported` to forbid adaptation. |
| `fallback_models` | `tuple[str, ...]` | Curated list shown when live catalog fetch fails |
| `default_headers` | `dict[str, str]` | Sent on every request (e.g. Copilot's `Editor-Version`) |
| `fixed_temperature` | Any | `None` = use caller's value; `OMIT_TEMPERATURE` sentinel = don't send temperature at all (Kimi) |
| `default_max_tokens` | `int \| None` | Provider-level max_tokens cap (Nvidia: 16384) |
| `default_aux_model` | str | Cheap model for auxiliary tasks (compression, vision, summarization) |

## Overridable hooks

Subclass `ProviderProfile` for non-trivial quirks:

```python
from typing import Any
from providers.base import ProviderProfile

class AcmeProfile(ProviderProfile):
    def prepare_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Provider-specific message preprocessing. Runs after codex
        sanitization, before developer-role swap. Default: pass-through."""
        # Example: Qwen normalizes plain-text content to a list-of-parts
        # array and injects cache_control; Kimi rewrites tool-call JSON
        return messages

    def build_extra_body(self, *, session_id=None, **context) -> dict:
        """Provider-specific extra_body fields merged into the API call.
        Context includes: session_id, provider_preferences, model, base_url,
        reasoning_config. Default: empty dict."""
        # Example: OpenRouter's provider-preferences block,
        # Gemini's thinking_config translation.
        return {}

    def build_api_kwargs_extras(self, *, reasoning_config=None, **context):
        """Returns (extra_body_additions, top_level_kwargs). Needed when some
        fields go top-level (Kimi's reasoning_effort, OpenRouter's verbosity for
        adaptive Anthropic models) and some go in extra_body (OpenRouter's
        reasoning dict). Default: ({}, {})."""
        return {}, {}

    def fetch_models(self, *, api_key=None, timeout=8.0) -> list[str] | None:
        """Live catalog fetch. Default hits {models_url or base_url}/models with
        Bearer auth. Override for: custom auth (Anthropic), no REST endpoint
        (Bedrock → None), or public/unauthenticated catalogs (OpenRouter)."""
        return super().fetch_models(api_key=api_key, timeout=timeout)
```

## Hook reference examples

Look at these bundled plugins for idioms:

| Plugin | Why look |
|---|---|
| `plugins/model-providers/openrouter/` | Aggregator with provider preferences, public model catalog |
| `plugins/model-providers/gemini/` | `thinking_config` translation (native + OpenAI-compat nested forms) |
| `plugins/model-providers/kimi-coding/` | `OMIT_TEMPERATURE`, `extra_body.thinking`, top-level `reasoning_effort` |
| `plugins/model-providers/qwen-oauth/` | Message normalization, `cache_control` injection, VL high-res |
| `plugins/model-providers/nous/` | Attribution tags, "omit reasoning when disabled" |
| `plugins/model-providers/custom/` | Ollama `num_ctx` + `think: false` quirks |
| `plugins/model-providers/bedrock/` | `api_mode="bedrock_converse"`, `fetch_models` returns None (no REST endpoint) |

## Unauthenticated providers and verified capability catalogs

Use the two provider declarations together when a delivered provider can run
without a literal API key and exposes verified per-model capability evidence:

```python
gateway = ProviderProfile(
    name="example-gateway",
    env_vars=("EXAMPLE_GATEWAY_API_KEY", "EXAMPLE_GATEWAY_BASE_URL"),
    base_url="http://127.0.0.1:18080/v1",
    auth_type="api_key",
    supports_unauthenticated=True,
    model_capabilities_path="model-capabilities",
    fallback_models=("auto",),
)
```

`supports_unauthenticated=True` is a credential-resolution declaration, not a
health or readiness claim. It lets the SDK receive a non-secret placeholder
when no key is stored. The client must still probe the live service:

- `401` or `403` means authentication is required and overrides the no-auth
  declaration.
- Connection failures and timeouts mean the provider is unreachable.
- Do not document a provider key as optional unless that provider's server can
  actually be launched without authentication.

`model_capabilities_path` is resolved relative to the effective inference base
URL. An opted-in provider's live model endpoint owns explicit model
availability, while this endpoint owns verified capability evidence. The
client exact-joins model IDs; it does not infer support from names, models.dev,
agent-wide protocol capabilities, or previous successful calls. A provider may
define a routing sentinel such as main-slot `auto` as an explicit policy
exception before catalog checks; do not treat that sentinel as live model
membership.

The capability response must represent `supported`, `unsupported`, and
`unknown` separately. Unknown means evidence is absent or insufficient. Never
convert it to supported, and never advertise every listed model as capable of
tools, vision, or reasoning.

Providers that omit `model_capabilities_path` retain legacy behavior. Their
existing catalogs, fallbacks, and picker eligibility are not subjected to the
verified Gateway selection policy.

## Declaring structured output truthfully

Archon workflows can request a bounded Draft 2020-12 `output_format`. Hermes
selects the enforcement strategy centrally from the resolved runtime; a model
name, API-mode string, successful past request, or community capability record
is not sufficient evidence of native support.

Leave `structured_output_strategy=None` unless you own transport-level proof.
When the request remains inside Hermes' complete agent loop, an undeclared
runtime uses the bounded `prompt_json_schema` adapter and Hermes validates the
result. Use an explicit declaration only for one of these contracts:

| Declaration | Contract you must prove |
| --- | --- |
| `native_json_schema` | The direct transport grammar-constrains the supplied schema, and its wire shape is covered by transport tests. |
| `native_json_mode` | The direct transport guarantees JSON syntax; Hermes still validates the portable schema. |
| `unsupported` | The runtime cannot safely constrain and validate output; do not use prompt adaptation. |

For a route the central runtime already classifies as trusted direct, declare
the strategy after the transport test proves the contract:

```python
direct_profile = ProviderProfile(
    name="direct-provider",
    base_url="https://api.example.com/v1",
    structured_output_strategy="native_json_schema",
)
```

Do not copy this declaration into an untested provider merely because its API
is OpenAI-compatible. The declaration does not itself make a new or custom URL
a trusted-direct route; absent that independent runtime classification, Hermes
uses prompt adaptation.

Native declarations are honored only on a trusted direct provider route.
Custom base URLs and aggregator routes fall back to prompt adaptation even if
they reuse an API mode or hostname pattern associated with a native transport.
Community model metadata can narrow or describe a route, but cannot promote it
to native support. Do not advertise native support for ChatGPT subscription or
OAuth routes from support observed on the direct OpenAI API-key route; those
are separate runtime authorities.

The prospective decision is sealed when a workflow is admitted. Immediately
before its first provider request, the isolated worker resolves the actual
credential, provider, URL, and API mode again. A mismatch that cannot honor the
sealed strategy fails with `structured_output_capability_drift`; it never
silently downgrades from native enforcement to prompt adaptation.

For a native declaration, add tests that exercise the real adapter boundary
and assert the exact schema field sent on the wire. Also cover custom and
aggregator URLs, an undeclared managed-loop route, explicit unsupported mode,
and admission/runtime drift before any provider call. Native responses still
undergo local parse, schema validation, and canonicalization, so transport
grammar is not a substitute for the optional validator.

## User overrides — replace a built-in without editing the repo

Say you want to point `gmi` at your private staging endpoint for testing. Create `~/.hermes/plugins/model-providers/gmi/__init__.py`:

```python
from providers import register_provider
from providers.base import ProviderProfile

register_provider(ProviderProfile(
    name="gmi",
    aliases=("gmi-cloud", "gmicloud"),
    env_vars=("GMI_API_KEY",),
    base_url="https://gmi-staging.internal.example.com/v1",
    auth_type="api_key",
    default_aux_model="google/gemini-3.1-flash-lite-preview",
))
```

Next session, `get_provider_profile("gmi").base_url` returns the staging URL. No repo patch, no rebuild. Because user plugins are discovered after bundled ones, the user `register_provider()` call wins.

## api_mode selection

Four values are recognized. Hermes picks one based on:

1. User explicit override (`config.yaml` `model.api_mode` when set)
2. OpenCode's per-model dispatch (`opencode_model_api_mode` for Zen and Go)
3. URL auto-detection — `/anthropic` suffix → `anthropic_messages`, `api.openai.com` → `codex_responses`, `api.x.ai` → `codex_responses`, `/coding` on Kimi domains → `chat_completions`
4. **Profile `api_mode`** as a fallback when URL detection finds nothing
5. Default `chat_completions`

Set `profile.api_mode` to match the default your provider ships — it acts as a hint. User URL overrides still win.

## Auth types

| `auth_type` | Meaning | Who uses it |
|---|---|---|
| `api_key` | Single env var carries a static API key | Most providers |
| `oauth_device_code` | Device-code OAuth flow | — |
| `oauth_external` | User signs in elsewhere, tokens land in `auth.json` | Anthropic OAuth, MiniMax OAuth, Qwen Portal, Nous Portal |
| `copilot` | GitHub Copilot token refresh cycle | `copilot` plugin only |
| `aws_sdk` | AWS SDK credential chain (IAM role, profile, env) | `bedrock` plugin only |
| `external_process` | Auth handled by a subprocess the agent spawns | `copilot-acp` plugin only |

`auth_type` gates which codepaths treat your provider as a "simple api-key provider" — if it's not `api_key`, the PluginManager still records the manifest but Hermes' CLI-level automation (doctor checks, `--provider` flag, setup wizard delegation) may skip over it.

## Discovery timing

Provider discovery is **lazy** — triggered by the first `get_provider_profile()` or `list_providers()` call in the process. In practice this happens early at startup (`auth.py` module load extends `PROVIDER_REGISTRY` eagerly). If you need to verify your plugin loaded, run:

```bash
hermes doctor
```

— a successful `auth_type="api_key"` profile appears under the Provider Connectivity section with a `/models` probe.

For programmatic inspection:

```python
from providers import list_providers
for p in list_providers():
    print(p.name, p.base_url, p.api_mode)
```

## Testing your plugin

Point `HERMES_HOME` at a temp directory so you don't pollute your real config:

```bash
export HERMES_HOME=/tmp/hermes-plugin-test
mkdir -p $HERMES_HOME/plugins/model-providers/my-provider
cat > $HERMES_HOME/plugins/model-providers/my-provider/__init__.py <<'EOF'
from providers import register_provider
from providers.base import ProviderProfile
register_provider(ProviderProfile(
    name="my-provider",
    env_vars=("MY_API_KEY",),
    base_url="https://api.my-provider.example.com/v1",
    auth_type="api_key",
))
EOF

export MY_API_KEY=your-test-key
hermes -z "hello" --provider my-provider -m some-model
```

## General PluginManager integration

The general `PluginManager` (the thing `hermes plugins` operates on) **sees** model-provider plugins but does not import them — `providers/__init__.py` owns their lifecycle. The manager records the manifest for introspection and categorizes by `kind: model-provider`. When you drop an unlabeled user plugin into `$HERMES_HOME/plugins/` that happens to call `register_provider` with a `ProviderProfile`, the manager auto-coerces it to `kind: model-provider` via a source-text heuristic — so the plugin still routes correctly even without `plugin.yaml`.

## Distribute via pip

Like any Hermes plugin, model providers can ship as a pip package. Add an entry point to your `pyproject.toml`:

```toml
[project.entry-points."hermes_agent.plugins"]
acme-inference = "acme_hermes_plugin:register"
```

…where `acme_hermes_plugin:register` is a function that calls `register_provider(profile)`. The general PluginManager picks up entry-point plugins during `discover_and_load()`. For `kind: model-provider` pip plugins, you still need to declare the kind in your manifest (or rely on the source-text heuristic).

See [Building a Hermes Plugin](/developer-guide/plugins#distribute-via-pip) for the full entry-points setup.

## Related pages

- [Provider Runtime](/developer-guide/provider-runtime) — resolution precedence + where each layer reads the profile
- [Adding Providers](/developer-guide/adding-providers) — end-to-end checklist for new inference backends (covers both the fast plugin path and the full CLI/auth integration)
- [Gateway Internals](/developer-guide/gateway-internals#inference-gateway-model-inventory) — live availability, verified evidence, and failure-state ownership
- [Memory Provider Plugins](/developer-guide/memory-provider-plugin)
- [Context Engine Plugins](/developer-guide/context-engine-plugin)
- [Building a Hermes Plugin](/developer-guide/plugins) — general plugin authoring
