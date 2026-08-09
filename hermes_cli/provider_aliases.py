"""Pure, shared provider compatibility aliases.

The provider plugin registry remains authoritative for registered canonical
names and aliases. This immutable fallback preserves public spellings that do
not have a loader registration without consulting configuration or credentials.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal


PUBLIC_PROVIDER_COMPATIBILITY_ALIASES: Mapping[str, str] = MappingProxyType({
    "glm": "zai",
    "z-ai": "zai",
    "z.ai": "zai",
    "zhipu": "zai",
    "google": "gemini",
    "google-gemini": "gemini",
    "google-ai-studio": "gemini",
    "x-ai": "xai",
    "x.ai": "xai",
    "grok": "xai",
    "xai-oauth": "xai-oauth",
    "x-ai-oauth": "xai-oauth",
    "grok-oauth": "xai-oauth",
    "xai-grok-oauth": "xai-oauth",
    "kimi": "kimi-coding",
    "kimi-for-coding": "kimi-coding",
    "moonshot": "kimi-coding",
    "kimi-cn": "kimi-coding-cn",
    "moonshot-cn": "kimi-coding-cn",
    "step": "stepfun",
    "stepfun-coding-plan": "stepfun",
    "arcee-ai": "arcee",
    "arceeai": "arcee",
    "gmi-cloud": "gmi",
    "gmicloud": "gmi",
    "minimax-china": "minimax-cn",
    "minimax_cn": "minimax-cn",
    "minimax-portal": "minimax-oauth",
    "minimax-global": "minimax-oauth",
    "minimax_oauth": "minimax-oauth",
    "alibaba_coding": "alibaba-coding-plan",
    "alibaba-coding": "alibaba-coding-plan",
    "alibaba_coding_plan": "alibaba-coding-plan",
    "claude": "anthropic",
    "claude-code": "anthropic",
    "github": "copilot",
    "github-copilot": "copilot",
    "github-models": "copilot",
    "github-model": "copilot",
    "github-copilot-acp": "copilot-acp",
    "copilot-acp-agent": "copilot-acp",
    "aigateway": "ai-gateway",
    "vercel": "ai-gateway",
    "vercel-ai-gateway": "ai-gateway",
    "opencode": "opencode-zen",
    "zen": "opencode-zen",
    "qwen-portal": "qwen-oauth",
    "qwen-cli": "qwen-oauth",
    "qwen-oauth": "qwen-oauth",
    "hf": "huggingface",
    "hugging-face": "huggingface",
    "huggingface-hub": "huggingface",
    "mimo": "xiaomi",
    "xiaomi-mimo": "xiaomi",
    "tencent": "tencent-tokenhub",
    "tokenhub": "tencent-tokenhub",
    "tencent-cloud": "tencent-tokenhub",
    "tencentmaas": "tencent-tokenhub",
    "aws": "bedrock",
    "aws-bedrock": "bedrock",
    "amazon-bedrock": "bedrock",
    "amazon": "bedrock",
    "go": "opencode-go",
    "opencode-go-sub": "opencode-go",
    "kilo": "kilocode",
    "kilo-code": "kilocode",
    "kilo-gateway": "kilocode",
    "lmstudio": "lmstudio",
    "lm-studio": "lmstudio",
    "lm_studio": "lmstudio",
    "ollama": "custom",
    "ollama_cloud": "ollama-cloud",
    "vllm": "custom",
    "llamacpp": "custom",
    "llama.cpp": "custom",
    "llama-cpp": "custom",
})


@dataclass(frozen=True, slots=True)
class ProviderSelectorResolution:
    """Credential- and config-free owner of one provider selector token."""

    token: str
    provider: str
    source: Literal["registry", "public_compatibility", "unresolved"]

    @property
    def recognized(self) -> bool:
        return self.source != "unresolved"


def resolve_provider_selector(provider: object) -> ProviderSelectorResolution:
    """Resolve registry ownership before the immutable compatibility fallback."""

    token = provider.strip().lower() if isinstance(provider, str) else ""
    if not token:
        return ProviderSelectorResolution("", "", "unresolved")
    # Keep this import lazy so providers can finish initializing before the
    # resolver consults its registry. Registry discovery failures are not an
    # alias miss: callers must see them instead of silently falling through to
    # a compatibility spelling with potentially different authority.
    from providers import get_provider_profile

    profile = get_provider_profile(token)
    if profile is not None:
        canonical = str(getattr(profile, "name", "") or "").strip().lower()
        if canonical:
            return ProviderSelectorResolution(token, canonical, "registry")
    compatibility = PUBLIC_PROVIDER_COMPATIBILITY_ALIASES.get(token)
    if compatibility is not None:
        return ProviderSelectorResolution(
            token,
            compatibility,
            "public_compatibility",
        )
    return ProviderSelectorResolution(token, token, "unresolved")
