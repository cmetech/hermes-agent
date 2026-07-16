"""LM Studio local model-server provider profile."""

from providers import register_provider
from providers.base import ProviderProfile


class LMStudioProfile(ProviderProfile):
    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 8.0,
    ) -> list[str] | None:
        from hermes_cli.models import fetch_lmstudio_models

        return fetch_lmstudio_models(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )


lmstudio = LMStudioProfile(
    name="lmstudio",
    aliases=("lm-studio", "lm_studio"),
    display_name="LM Studio",
    description="Local LM Studio model server",
    env_vars=("LM_API_KEY", "LM_BASE_URL"),
    base_url="http://127.0.0.1:1234/v1",
    auth_type="api_key",
    supports_unauthenticated=True,
)

register_provider(lmstudio)
