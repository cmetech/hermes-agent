"""Shared transport and result-shaping code for the Ericsson connectors.

This directory is the ONLY place to edit this code.  It is copied verbatim
into each connector as ``plugins/<name>/_common/`` by
``scripts/sync_shared.py``, because the Hermes plugin loader roots a plugin
package at its own directory and sibling packages are not importable.

``vendor-ericsson.mjs`` copies whole plugin trees recursively, so the
``_common/`` copies ship to hermes-agent with no vendor-script change.
"""

SHARED_VERSION = "1.0.0"

__all__ = ["SHARED_VERSION"]
