"""OTTO gateway provider profile.

Routes chat completions through the OTTO gateway (an OpenAI-compatible Go
service) which forwards to Kilo / ``kiro-cli`` via ACP. See the otto-gateway
repo for the server side.

Endpoint / auth:
  - OpenAI-compatible. Default base URL ``http://127.0.0.1:18080/v1``
    (override with ``OTTO_BASE_URL``).
  - Bearer auth via ``OTTO_API_KEY``. The gateway may run WITHOUT ``AUTH_TOKEN``
    (no auth). The provider declaration opts into the SDK's non-secret
    placeholder behavior; set a real ``OTTO_API_KEY`` only if the gateway was
    launched with ``AUTH_TOKEN``.
  - Model ``auto`` (the safe default) lets the gateway use kiro's current model;
    ``fetch_models`` lists the live catalog from ``/v1/models``.
  - Verified model metadata comes from the gateway's
    ``/v1/model-capabilities`` endpoint.

Adding this directory is the entire wiring: the loader enumerates
plugins/model-providers/, ``register_provider`` self-registers the profile, and
hermes_cli/auth.py auto-extends PROVIDER_REGISTRY from any api_key provider
(deriving the key var from ``OTTO_API_KEY`` and the base-URL override from
``OTTO_BASE_URL``). No edits to the shared provider machinery required.
"""

from providers import register_provider
from providers.base import ProviderProfile


otto = ProviderProfile(
    name="otto",
    aliases=("otto-gateway",),
    display_name="OTTO Gateway",
    description="OTTO gateway → Kilo (kiro-cli)",
    # OTTO_API_KEY → bearer token; OTTO_BASE_URL → base URL override.
    # auth.py's registry auto-merge splits *_URL/*_BASE_URL vars out of the key
    # list, so OTTO_BASE_URL becomes base_url_env_var automatically.
    env_vars=("OTTO_API_KEY", "OTTO_BASE_URL"),
    base_url="http://127.0.0.1:18080/v1",
    auth_type="api_key",
    supports_unauthenticated=True,
    model_capabilities_path="model-capabilities",
    otto_tool_contract_version="v1",
    # Safe default; the picker also shows live ids from GET /v1/models.
    fallback_models=("auto",),
    # The gateway reports honest-zero usage and does not cap output itself;
    # give a generous floor so responses aren't truncated when the user hasn't
    # set model.max_tokens (mirrors the custom/local provider).
    default_max_tokens=65536,
)

register_provider(otto)
