"""Recognition of legacy per-plugin secret storage keys."""

from __future__ import annotations

import re

__all__ = ["is_plugin_secret_key"]

# Keys minted by plugin_configuration._secret_storage_key:
# HERMES_PLUGIN_<sha256 prefix: 32 uppercase hex>_<field slug>.
_PLUGIN_SECRET_KEY = re.compile(r"^HERMES_PLUGIN_[0-9A-F]{32}_[A-Z0-9_]+$")


def is_plugin_secret_key(name: str) -> bool:
    """True only for the persisted per-plugin secret-key namespace."""
    return _PLUGIN_SECRET_KEY.fullmatch(name) is not None
