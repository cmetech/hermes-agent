"""One-way migration of plugin secrets out of ~/.hermes/.env.

Plugin PATs were historically stored as HERMES_PLUGIN_<digest>_<SLUG>
entries in ~/.hermes/.env. That file is exported into os.environ at
startup by load_dotenv, so every child process Hermes spawns inherited a
copy of every PAT. This moves them into the keystore and removes the
plaintext line.

Ordering is deliberate: write, read back, compare, and only then remove.
Removing first and failing to write would destroy the credential.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal, cast

from hermes_cli.config import (
    load_env,
    remove_env_value,
)
from hermes_cli.plugin_secret_keys import is_plugin_secret_key

__all__ = ["MigrationReport", "find_legacy_secrets", "migrate_secrets"]

_MODE_KEY = "secret_keystore"
_MODE_ENV = "HERMES_SECRET_KEYSTORE"
_VALID_MODES = frozenset({"auto", "os", "file", "off"})


@dataclass
class MigrationReport:
    migrated: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    dry_run: bool = False


def _get_configured_mode_readonly() -> Literal["auto", "os", "file", "off"]:
    """Read configured policy without importing keystore backend machinery."""
    raw = os.environ.get(_MODE_ENV)
    if raw is None:
        try:
            from hermes_cli.config import get_config_path, load_config_readonly

            config = load_config_readonly(
                config_path=get_config_path(),
                suppress_parse_failure_side_effects=True,
            )
            raw = config.get(_MODE_KEY) if isinstance(config, dict) else None
        except Exception:
            raw = None
    mode = str(raw or "auto").strip().lower()
    if mode not in _VALID_MODES:
        return "auto"
    return cast(Literal["auto", "os", "file", "off"], mode)


def find_legacy_secrets() -> dict[str, str]:
    """Return plugin secret entries still present in .env."""
    return {
        key: value
        for key, value in load_env().items()
        if is_plugin_secret_key(key) and value
    }


def migrate_secrets(dry_run: bool = False) -> MigrationReport:
    """Move plugin secrets from .env into the keystore.

    Idempotent: keys already migrated are simply absent from .env and are
    therefore skipped on subsequent runs.
    """
    report = MigrationReport(dry_run=dry_run)
    legacy_secrets = find_legacy_secrets()
    if dry_run:
        report.migrated.extend(legacy_secrets)
        return report
    if not legacy_secrets:
        return report

    # Operational migration imports the keystore only after the dry-run path
    # has returned. Importing this module alone must never load keyring or the
    # cryptographic backend graph.
    from hermes_cli import secret_keystore

    for key, value in legacy_secrets.items():
        try:
            secret_keystore.set_secret(key, value)
        except secret_keystore.KeystoreError:
            report.failed.append(key)
            continue
        if secret_keystore.get_secret(key) != value:
            report.failed.append(key)
            continue
        try:
            removed = remove_env_value(
                key,
                mirror_process_env=False,
                strict=True,
            )
        except Exception:
            removed = False
        if not removed:
            report.failed.append(key)
            continue
        report.migrated.append(key)
    return report


def _handle_secrets_migrate(args) -> int:
    from hermes_cli.secrets_migrate import migrate_secrets

    report = migrate_secrets(dry_run=args.dry_run)
    if report.dry_run:
        mode = _get_configured_mode_readonly()
        print(
            f"Would migrate {len(report.migrated)} secret(s) "
            f"(configured {mode} mode; backend not probed)."
        )
    elif not report.migrated and not report.failed:
        print("No plugin secrets found in .env — nothing to migrate.")
        return 0
    else:
        print(
            f"Migrated {len(report.migrated)} secret(s) "
            "to protected credential storage."
        )
    verb = "Would migrate" if report.dry_run else "Migrated"
    for key in report.migrated:
        print(f"  {verb.lower()}: {key}")
    if report.failed:
        print(
            f"\n{len(report.failed)} secret(s) could NOT be migrated "
            "and remain in .env:"
        )
        for key in report.failed:
            print(f"  failed: {key}")
        print("\nRe-run after resolving the keystore problem. Nothing was lost.")
        return 1
    return 0
