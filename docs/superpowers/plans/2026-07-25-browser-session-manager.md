# Browser Session Manager (persistent enrolled profiles) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Hermes one consistent way to manage browser sessions — including a
persistent, corporate-enrolled browser profile that holds a live SSO/mTLS session
— so `/browser` and internal-site skills stop reimplementing browser lifecycle.

**Architecture:** Two new brand-agnostic modules (`tools/browser_profiles.py`,
`tools/browser_session_manager.py`) hold all new logic, so they cannot conflict
with upstream. `tools/browser_tool.py` receives exactly **one** additive hook: an
origin-scoped trust check threaded into the two existing SSRF guard predicates,
mirroring upstream's per-session `_is_local_sidecar_key` pattern. The
`confluence-research` skill gains a third, opt-in backend that consumes the
manager; its existing backends stay as fallback.

**Tech Stack:** Python 3.11, `agent-browser` CLI (the existing OOB driver), raw
CDP via the existing `browser_supervisor`, `pytest` via `scripts/run_tests.sh`.

## Global Constraints

- **Spec:** `docs/plans/2026-07-20-persistent-enrolled-browser-session-design.md`. Read §0 (Revision 2026-07-25) FIRST — it supersedes §5 and records a blocker the original design did not know about.
- **Branch:** `base` only. Brand-agnostic. Never commit this on `otto`, `loop24`, or any brand branch (workspace `CLAUDE.md`, shared-capability placement invariant).
- **Never** call `pytest` directly. Always `scripts/run_tests.sh` (enforces CI parity: unset credential vars, `TZ=UTC`, `LANG=C.UTF-8`, `-n auto`, subprocess-per-test-file isolation).
- **Engine:** agent-browser only. Do NOT add Playwright as a driver. Playwright appears in `browser_tool.py` solely as Chromium install/cache plumbing.
- **Every test file runs in a fresh subprocess** — module-level dicts/ContextVars do not leak between files. Do not rely on cross-file state.
- **Fail-safe/fail-open:** every config read defaults to today's behaviour when the key is absent. A missing or malformed `browser.profiles` block must leave `/browser` byte-identical to current behaviour.
- **Enrolled-browser rule (spec §5):** the launcher MUST resolve the *enrolled* browser (OS cert store) and MUST NOT use agent-browser's bundled Chrome for Testing for an enrolled profile. That distinction is the entire reason mTLS works.
- **Hard isolation rule (spec §5):** profile is fixed at `acquire()`. An untrusted external site must never be driven through the `enrolled` profile.
- **Governance (spec §6a):** Task 4 is the FIRST OTTO edit to `tools/browser_tool.py`. Its surface-table rows, paired `AGENTS.md`, and merge-skill greps ship in the SAME commit as the hook. Non-negotiable.
- **Brand gate — does NOT run on `base`.** Corrected 2026-07-25 during execution: `generate <brand> --check` FAILS on `base` **by design** (base is the neutral branch; the 8 emitter-covered files hold upstream Hermes values, so brand-config/intro/home report Hermes). Verified: `base` reports `name: expected "OTTO", got "Hermes"` etc. with a clean tree. This work touches none of the 8 emitters (`providerEmitter`, `pyprojectScriptsEmitter`, `skinEmitter`, `packageJsonEmitter`, `mainIdentityEmitter`, `brandConfigEmitter`, `introEmitter`, `homeEmitter`), so the gate is simply **not applicable here** — run it after merging `base → otto` / `base → loop24`, per the merge procedure. Do not "fix" a base-branch `--check` failure.
- **Governance ledger:** the authoritative, versioned record for this work is `docs/upstream-customizations/browser-profiles.yaml`, validated with `venv/bin/python scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/browser-profiles.yaml`. The workspace `CLAUDE.md`/`AGENTS.md` surface table and the `otto-upstream-merge` grep list live **outside any git repo** (the workspace root is not a repository), so they are a convenience layer only — keep them updated, but the ledger is what travels with the branch.

---

### Task 1: Profile registry — config parsing and defaults

**Files:**
- Create: `tools/browser_profiles.py`
- Test: `tests/tools/test_browser_profiles.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `@dataclass(frozen=True) BrowserProfile` with fields `name: str`, `kind: str`, `executable: str`, `user_data_dir: str`, `cdp_port: int`, `trusted_origins: tuple[str, ...]`, `headed: bool`.
  - `DEFAULT_PROFILE_NAME: str = "default"`
  - `get_profile(name: str) -> BrowserProfile | None`
  - `load_profiles() -> dict[str, BrowserProfile]`

- [ ] **Step 1: Write the failing test**

Create `tests/tools/test_browser_profiles.py`:

```python
"""Tests for the browser profile registry (config parsing + defaults)."""

import pytest

from tools import browser_profiles


class TestLoadProfiles:
    def test_absent_config_yields_only_builtin_default(self, monkeypatch):
        """No browser.profiles block → exactly one ephemeral 'default' profile."""
        monkeypatch.setattr(browser_profiles, "_read_config", lambda: {})
        profiles = browser_profiles.load_profiles()
        assert list(profiles) == ["default"]
        assert profiles["default"].kind == "ephemeral"
        assert profiles["default"].trusted_origins == ()

    def test_enrolled_profile_is_parsed(self, monkeypatch):
        monkeypatch.setattr(
            browser_profiles,
            "_read_config",
            lambda: {
                "browser": {
                    "profiles": {
                        "enrolled": {
                            "kind": "enrolled",
                            "executable": "auto",
                            "user_data_dir": "/tmp/enrolled",
                            "cdp_port": 9222,
                            "headed": True,
                            "trusted_origins": ["https://wiki.corp.example"],
                        }
                    }
                }
            },
        )
        prof = browser_profiles.get_profile("enrolled")
        assert prof is not None
        assert prof.kind == "enrolled"
        assert prof.cdp_port == 9222
        assert prof.headed is True
        assert prof.trusted_origins == ("https://wiki.corp.example",)

    def test_unknown_profile_returns_none(self, monkeypatch):
        monkeypatch.setattr(browser_profiles, "_read_config", lambda: {})
        assert browser_profiles.get_profile("nope") is None

    def test_malformed_profiles_block_falls_back_to_default(self, monkeypatch):
        """A non-dict profiles value must not raise — fail open to default."""
        monkeypatch.setattr(
            browser_profiles, "_read_config", lambda: {"browser": {"profiles": "garbage"}}
        )
        profiles = browser_profiles.load_profiles()
        assert list(profiles) == ["default"]

    def test_unknown_kind_is_rejected(self, monkeypatch):
        """An unrecognized kind is dropped rather than trusted."""
        monkeypatch.setattr(
            browser_profiles,
            "_read_config",
            lambda: {"browser": {"profiles": {"weird": {"kind": "wat"}}}},
        )
        assert browser_profiles.get_profile("weird") is None

    def test_builtin_default_cannot_be_given_trusted_origins(self, monkeypatch):
        """Hard rule: the ephemeral default profile is never origin-trusted."""
        monkeypatch.setattr(
            browser_profiles,
            "_read_config",
            lambda: {
                "browser": {
                    "profiles": {
                        "default": {"kind": "ephemeral",
                                    "trusted_origins": ["https://evil.example"]}
                    }
                }
            },
        )
        assert browser_profiles.get_profile("default").trusted_origins == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `scripts/run_tests.sh tests/tools/test_browser_profiles.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.browser_profiles'`

- [ ] **Step 3: Write minimal implementation**

Create `tools/browser_profiles.py`:

```python
"""Named browser profiles — a pure registry over ``browser.profiles`` config.

A *profile* is a named browser identity. Two kinds exist:

``ephemeral``
    Today's behaviour: agent-browser's bundled Chrome for Testing, disposable,
    no persistent user-data-dir. This is the ``default`` profile and is never
    origin-trusted.

``enrolled``
    A corporate-enrolled browser (OS cert store) launched with a persistent
    user-data-dir so it can hold a live SSO/mTLS session, attached over CDP.

This module is intentionally pure: it parses and validates config and resolves
executables. It launches nothing and holds no session state — that is
``tools.browser_session_manager``. Keeping it pure keeps it unit-testable
without a browser.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_PROFILE_NAME = "default"

KIND_EPHEMERAL = "ephemeral"
KIND_ENROLLED = "enrolled"
_VALID_KINDS = frozenset({KIND_EPHEMERAL, KIND_ENROLLED})

DEFAULT_CDP_PORT = 9222


@dataclass(frozen=True)
class BrowserProfile:
    """An immutable, validated browser identity."""

    name: str
    kind: str = KIND_EPHEMERAL
    executable: str = "auto"
    user_data_dir: str = ""
    cdp_port: int = DEFAULT_CDP_PORT
    trusted_origins: Tuple[str, ...] = field(default_factory=tuple)
    headed: bool = False

    @property
    def is_enrolled(self) -> bool:
        return self.kind == KIND_ENROLLED


def _read_config() -> Dict[str, Any]:
    """Read the raw config. Separate function so tests can monkeypatch it."""
    try:
        from hermes_cli.config import read_raw_config

        return read_raw_config() or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("browser_profiles: could not read config: %s", exc)
        return {}


def _builtin_default() -> BrowserProfile:
    """The always-present ephemeral profile — today's exact OOB behaviour."""
    return BrowserProfile(name=DEFAULT_PROFILE_NAME, kind=KIND_EPHEMERAL)


def _parse_one(name: str, raw: Any) -> Optional[BrowserProfile]:
    """Parse a single profile entry, returning None when unusable."""
    if not isinstance(raw, dict):
        logger.debug("browser_profiles: profile %r is not a mapping; ignoring", name)
        return None

    kind = str(raw.get("kind", KIND_EPHEMERAL)).strip().lower()
    if kind not in _VALID_KINDS:
        logger.debug("browser_profiles: profile %r has unknown kind %r; ignoring", name, kind)
        return None

    # Hard rule: the ephemeral default profile is NEVER origin-trusted. A
    # hostile external page must not be able to reach private addresses just
    # because someone added trusted_origins to the disposable profile.
    origins: Tuple[str, ...] = ()
    if kind == KIND_ENROLLED:
        raw_origins = raw.get("trusted_origins") or []
        if isinstance(raw_origins, (list, tuple)):
            origins = tuple(str(o).strip() for o in raw_origins if str(o).strip())

    try:
        port = int(raw.get("cdp_port", DEFAULT_CDP_PORT))
    except (TypeError, ValueError):
        port = DEFAULT_CDP_PORT

    return BrowserProfile(
        name=name,
        kind=kind,
        executable=str(raw.get("executable", "auto") or "auto"),
        user_data_dir=str(raw.get("user_data_dir", "") or ""),
        cdp_port=port,
        trusted_origins=origins,
        headed=bool(raw.get("headed", False)),
    )


def load_profiles() -> Dict[str, BrowserProfile]:
    """Return all valid profiles, always including the builtin ``default``.

    Fail-open: any malformed input degrades to the builtin default so a bad
    config can never break ``/browser``.
    """
    profiles: Dict[str, BrowserProfile] = {DEFAULT_PROFILE_NAME: _builtin_default()}

    cfg = _read_config()
    browser_cfg = cfg.get("browser") if isinstance(cfg, dict) else None
    raw_profiles = browser_cfg.get("profiles") if isinstance(browser_cfg, dict) else None
    if not isinstance(raw_profiles, dict):
        return profiles

    for name, raw in raw_profiles.items():
        key = str(name).strip()
        if not key:
            continue
        parsed = _parse_one(key, raw)
        if parsed is None:
            continue
        if key == DEFAULT_PROFILE_NAME:
            # Config may tune the default profile but never grant it trust.
            parsed = BrowserProfile(
                name=DEFAULT_PROFILE_NAME,
                kind=KIND_EPHEMERAL,
                executable=parsed.executable,
                user_data_dir=parsed.user_data_dir,
                cdp_port=parsed.cdp_port,
                trusted_origins=(),
                headed=parsed.headed,
            )
        profiles[key] = parsed
    return profiles


def get_profile(name: str) -> Optional[BrowserProfile]:
    """Return a profile by name, or None when it does not exist."""
    return load_profiles().get(str(name).strip())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `scripts/run_tests.sh tests/tools/test_browser_profiles.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/browser_profiles.py tests/tools/test_browser_profiles.py
git commit -m "feat(browser): add named browser profile registry

Pure config parser for browser.profiles with an always-present ephemeral
'default'. Fail-open on malformed input; the default profile can never be
granted trusted_origins.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Origin trust matching

**Files:**
- Modify: `tools/browser_profiles.py` (append)
- Test: `tests/tools/test_browser_profiles.py` (append)

**Interfaces:**
- Consumes: `BrowserProfile` from Task 1.
- Produces: `is_origin_trusted(profile: BrowserProfile, url: str) -> bool`

This is the security-critical predicate. It decides whether a private/internal
URL is allowed for a given profile, so its failure mode must be **deny**.

- [ ] **Step 1: Write the failing test**

Append to `tests/tools/test_browser_profiles.py`:

```python
def _enrolled(*origins):
    return browser_profiles.BrowserProfile(
        name="enrolled",
        kind=browser_profiles.KIND_ENROLLED,
        trusted_origins=tuple(origins),
    )


class TestIsOriginTrusted:
    def test_exact_origin_match(self):
        p = _enrolled("https://wiki.corp.example")
        assert browser_profiles.is_origin_trusted(p, "https://wiki.corp.example/page/123")

    def test_scheme_must_match(self):
        """http:// must not inherit trust granted to https://."""
        p = _enrolled("https://wiki.corp.example")
        assert not browser_profiles.is_origin_trusted(p, "http://wiki.corp.example/x")

    def test_wildcard_matches_subdomain(self):
        p = _enrolled("https://*.corp.example")
        assert browser_profiles.is_origin_trusted(p, "https://wiki.corp.example/x")

    def test_wildcard_does_not_match_apex(self):
        """'*.corp.example' grants subdomains only, not corp.example itself."""
        p = _enrolled("https://*.corp.example")
        assert not browser_profiles.is_origin_trusted(p, "https://corp.example/x")

    def test_wildcard_does_not_match_suffix_lookalike(self):
        """The classic bypass: evilcorp.example must not match *.corp.example."""
        p = _enrolled("https://*.corp.example")
        assert not browser_profiles.is_origin_trusted(p, "https://evilcorp.example/x")

    def test_wildcard_is_not_a_bare_substring(self):
        p = _enrolled("https://*.corp.example")
        assert not browser_profiles.is_origin_trusted(p, "https://corp.example.evil.test/x")

    def test_port_mismatch_is_untrusted(self):
        p = _enrolled("https://wiki.corp.example")
        assert not browser_profiles.is_origin_trusted(p, "https://wiki.corp.example:8443/x")

    def test_ephemeral_profile_is_never_trusted(self):
        p = browser_profiles.BrowserProfile(name="default", kind=browser_profiles.KIND_EPHEMERAL)
        assert not browser_profiles.is_origin_trusted(p, "https://wiki.corp.example/x")

    def test_no_origins_is_never_trusted(self):
        assert not browser_profiles.is_origin_trusted(_enrolled(), "https://wiki.corp.example/x")

    def test_garbage_url_denies(self):
        p = _enrolled("https://wiki.corp.example")
        assert not browser_profiles.is_origin_trusted(p, "not a url")
        assert not browser_profiles.is_origin_trusted(p, "")

    def test_none_profile_denies(self):
        assert not browser_profiles.is_origin_trusted(None, "https://wiki.corp.example/x")

    def test_userinfo_spoof_denies(self):
        """https://wiki.corp.example@evil.test must resolve to evil.test → deny."""
        p = _enrolled("https://wiki.corp.example")
        assert not browser_profiles.is_origin_trusted(
            p, "https://wiki.corp.example@evil.test/x"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `scripts/run_tests.sh tests/tools/test_browser_profiles.py -v -k IsOriginTrusted`
Expected: FAIL — `AttributeError: module 'tools.browser_profiles' has no attribute 'is_origin_trusted'`

- [ ] **Step 3: Write minimal implementation**

Append to `tools/browser_profiles.py`:

```python
def _normalize_origin(url: str) -> Optional[Tuple[str, str, Optional[int]]]:
    """Return ``(scheme, hostname, port)`` for a URL, or None when unparseable.

    Uses ``urlsplit`` so userinfo spoofs (``https://good.example@evil.test``)
    resolve to the REAL host (``evil.test``) rather than the decoy.
    """
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url.strip())
    except (ValueError, AttributeError):
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    try:
        port = parts.port
    except ValueError:
        return None  # malformed port → deny
    return (parts.scheme.lower(), parts.hostname.lower(), port)


def is_origin_trusted(profile: Optional[BrowserProfile], url: str) -> bool:
    """Return True when ``url``'s origin is explicitly trusted by ``profile``.

    SECURITY: this predicate gates private/internal network access, so every
    unexpected input must DENY. Only ``enrolled`` profiles can grant trust.

    A pattern is an origin string, optionally with a leading ``*.`` wildcard on
    the host: ``https://wiki.corp.example`` or ``https://*.corp.example``. The
    wildcard matches strict subdomains only — never the apex, never a
    suffix-lookalike (``evilcorp.example``). Scheme and port must match exactly.
    """
    if profile is None or not profile.is_enrolled or not profile.trusted_origins:
        return False

    target = _normalize_origin(url)
    if target is None:
        return False
    scheme, host, port = target

    for pattern in profile.trusted_origins:
        allowed = _normalize_origin(pattern.replace("*.", "wildcard-placeholder.", 1))
        if allowed is None:
            continue
        a_scheme, a_host, a_port = allowed
        if a_scheme != scheme or a_port != port:
            continue
        if a_host.startswith("wildcard-placeholder."):
            suffix = a_host[len("wildcard-placeholder") :]  # ".corp.example"
            # Strict subdomain: must END with ".corp.example" AND have a label
            # before it. Excludes the apex and "evilcorp.example".
            if host.endswith(suffix) and len(host) > len(suffix):
                return True
            continue
        if a_host == host:
            return True
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `scripts/run_tests.sh tests/tools/test_browser_profiles.py -v`
Expected: PASS (18 tests total)

- [ ] **Step 5: Commit**

```bash
git add tools/browser_profiles.py tests/tools/test_browser_profiles.py
git commit -m "feat(browser): add origin-trust predicate for browser profiles

is_origin_trusted() gates private-network access per profile. Denies on every
unexpected input: ephemeral profiles, scheme/port mismatch, apex and
suffix-lookalike wildcard bypasses, and userinfo spoofs.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Enrolled browser executable resolution

**Files:**
- Modify: `tools/browser_profiles.py` (append)
- Test: `tests/tools/test_browser_profiles.py` (append)

**Interfaces:**
- Consumes: `BrowserProfile`.
- Produces: `resolve_executable(profile: BrowserProfile) -> str | None`

- [ ] **Step 1: Write the failing test**

Append to `tests/tools/test_browser_profiles.py`:

```python
class TestResolveExecutable:
    def test_explicit_path_is_used_when_it_exists(self, monkeypatch, tmp_path):
        exe = tmp_path / "msedge"
        exe.write_text("")
        p = browser_profiles.BrowserProfile(
            name="enrolled", kind=browser_profiles.KIND_ENROLLED, executable=str(exe)
        )
        assert browser_profiles.resolve_executable(p) == str(exe)

    def test_explicit_missing_path_returns_none(self):
        p = browser_profiles.BrowserProfile(
            name="enrolled",
            kind=browser_profiles.KIND_ENROLLED,
            executable="/nonexistent/msedge",
        )
        assert browser_profiles.resolve_executable(p) is None

    def test_auto_probes_platform_candidates(self, monkeypatch, tmp_path):
        found = tmp_path / "Microsoft Edge"
        found.write_text("")
        monkeypatch.setattr(
            browser_profiles, "_enrolled_candidates", lambda: [str(tmp_path / "nope"), str(found)]
        )
        p = browser_profiles.BrowserProfile(
            name="enrolled", kind=browser_profiles.KIND_ENROLLED, executable="auto"
        )
        assert browser_profiles.resolve_executable(p) == str(found)

    def test_auto_with_no_candidate_returns_none(self, monkeypatch):
        monkeypatch.setattr(browser_profiles, "_enrolled_candidates", lambda: [])
        p = browser_profiles.BrowserProfile(
            name="enrolled", kind=browser_profiles.KIND_ENROLLED, executable="auto"
        )
        assert browser_profiles.resolve_executable(p) is None

    def test_ephemeral_profile_resolves_to_none(self):
        """Ephemeral profiles use agent-browser's bundled Chrome, not an OS browser."""
        p = browser_profiles.BrowserProfile(name="default", kind=browser_profiles.KIND_EPHEMERAL)
        assert browser_profiles.resolve_executable(p) is None

    def test_candidates_are_platform_specific(self, monkeypatch):
        monkeypatch.setattr(browser_profiles.sys, "platform", "win32")
        assert any("Edge" in c for c in browser_profiles._enrolled_candidates())
        monkeypatch.setattr(browser_profiles.sys, "platform", "darwin")
        assert any("Microsoft Edge" in c for c in browser_profiles._enrolled_candidates())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `scripts/run_tests.sh tests/tools/test_browser_profiles.py -v -k ResolveExecutable`
Expected: FAIL — no attribute `resolve_executable`

- [ ] **Step 3: Write minimal implementation**

Append to `tools/browser_profiles.py` (and add `import os`, `import sys` to the
existing import block at the top of the file):

```python
def _enrolled_candidates() -> list:
    """Return likely enrolled-browser executable paths for this platform.

    Edge is listed first: on a managed corporate device it is the browser
    enrolled with the OS certificate store, which is what makes mTLS work.
    We deliberately do NOT include agent-browser's bundled Chrome for Testing —
    it is the UNMANAGED browser that fails corporate mTLS/SSO.
    """
    if sys.platform == "win32":
        program_files = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        program_files64 = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        return [
            os.path.join(program_files, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(program_files64, "Microsoft", "Edge", "Application", "msedge.exe"),
        ]
    if sys.platform == "darwin":
        return [
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
    return [
        "/usr/bin/microsoft-edge",
        "/usr/bin/microsoft-edge-stable",
        "/usr/bin/google-chrome",
    ]


def resolve_executable(profile: BrowserProfile) -> Optional[str]:
    """Return the enrolled browser executable for ``profile``, or None.

    Ephemeral profiles always return None — they use agent-browser's bundled
    Chrome for Testing via the normal ``--session`` path.
    """
    if not profile.is_enrolled:
        return None

    configured = (profile.executable or "auto").strip()
    if configured and configured.lower() != "auto":
        return configured if os.path.exists(configured) else None

    for candidate in _enrolled_candidates():
        if os.path.exists(candidate):
            return candidate
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `scripts/run_tests.sh tests/tools/test_browser_profiles.py -v`
Expected: PASS (24 tests total)

- [ ] **Step 5: Commit**

```bash
git add tools/browser_profiles.py tests/tools/test_browser_profiles.py
git commit -m "feat(browser): resolve enrolled browser executable per platform

Edge-first candidate probing (OS cert store = working mTLS). Never falls back
to agent-browser's bundled Chrome for Testing for an enrolled profile.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: The SSRF trust seam + MANDATORY governance artifacts

**This is the highest-risk task in the plan.** It is the first OTTO edit to
`tools/browser_tool.py`, which upstream changed by 188 lines in v0.19.0. Per spec
§6a, the governance artifacts ship in the SAME commit — a hook without a grep
gets silently reverted by a future `main → base` merge with no test failure and
no build error.

**Files:**
- Create: `tools/browser_session_registry.py`
- Modify: `tools/browser_tool.py` (`_expression_targets_private_url` ~line 3481, `_current_page_private_url` ~line 3498, and the call site in `_browser_eval` ~line 3683)
- Modify: `/Users/coreyellis/code/github.com/cmetech/otto_hermes/CLAUDE.md` (surface table)
- Modify: `/Users/coreyellis/code/github.com/cmetech/otto_hermes/AGENTS.md` (byte-identical)
- Modify: `/Users/coreyellis/code/github.com/cmetech/otto_hermes/.claude/skills/otto-upstream-merge/SKILL.md` (silent-revert greps, near line 171)
- Test: `tests/tools/test_browser_profile_trust_seam.py`

**Interfaces:**
- Consumes: `is_origin_trusted`, `get_profile` (Tasks 1–2).
- Produces:
  - `browser_session_registry.bind(session_key: str, profile_name: str) -> None`
  - `browser_session_registry.unbind(session_key: str) -> None`
  - `browser_session_registry.profile_for(session_key: str) -> str | None`
  - `browser_session_registry.session_trusts_url(session_key: str, url: str) -> bool`
  - `browser_tool._session_trusts_url(session_key, url) -> bool` (thin delegating hook)

Why a separate registry module: it keeps the mutable session→profile map out of
`browser_tool.py`, so the footprint in that shared upstream file is a single
small function plus two one-line predicate guards.

- [ ] **Step 1: Write the failing test**

Create `tests/tools/test_browser_profile_trust_seam.py`:

```python
"""Tests for the per-session, origin-scoped SSRF trust seam.

Upstream v0.19.0 gates private-network access via _eval_ssrf_guard_active(),
which returns False for any CDP override — and an enrolled profile attaches
over CDP. These tests pin the narrow exemption: a private URL is permitted
ONLY when the session's bound profile explicitly trusts that origin.
"""

import pytest

from tools import browser_profiles, browser_session_registry, browser_tool

PRIVATE_TRUSTED = "https://wiki.corp.example/rest/api/content"
PRIVATE_UNTRUSTED = "https://intranet.other.example/secret"

ENROLLED = browser_profiles.BrowserProfile(
    name="enrolled",
    kind=browser_profiles.KIND_ENROLLED,
    trusted_origins=("https://wiki.corp.example",),
)


@pytest.fixture(autouse=True)
def _clean_registry():
    browser_session_registry.clear()
    yield
    browser_session_registry.clear()


@pytest.fixture()
def _enrolled_bound(monkeypatch):
    monkeypatch.setattr(
        browser_profiles, "get_profile", lambda name: ENROLLED if name == "enrolled" else None
    )
    browser_session_registry.bind("task-1", "enrolled")


class TestRegistry:
    def test_bind_and_lookup(self):
        browser_session_registry.bind("task-1", "enrolled")
        assert browser_session_registry.profile_for("task-1") == "enrolled"

    def test_unbind_removes(self):
        browser_session_registry.bind("task-1", "enrolled")
        browser_session_registry.unbind("task-1")
        assert browser_session_registry.profile_for("task-1") is None

    def test_unbound_session_trusts_nothing(self):
        assert not browser_session_registry.session_trusts_url("task-x", PRIVATE_TRUSTED)


class TestSessionTrustsUrl:
    def test_trusted_origin_is_permitted(self, _enrolled_bound):
        assert browser_session_registry.session_trusts_url("task-1", PRIVATE_TRUSTED)

    def test_untrusted_origin_is_denied(self, _enrolled_bound):
        assert not browser_session_registry.session_trusts_url("task-1", PRIVATE_UNTRUSTED)

    def test_other_session_does_not_inherit_trust(self, _enrolled_bound):
        """Trust is per-session; a sibling task must not borrow it."""
        assert not browser_session_registry.session_trusts_url("task-2", PRIVATE_TRUSTED)


class TestExpressionPreScan:
    """browser_tool._expression_targets_private_url must honour session trust."""

    @pytest.fixture(autouse=True)
    def _force_private(self, monkeypatch):
        # Treat every URL as private so we isolate the trust decision.
        monkeypatch.setattr(browser_tool, "_is_safe_url", lambda url: False)
        monkeypatch.setattr(browser_tool, "_is_always_blocked_url", lambda url: False)

    def test_blocks_private_literal_without_trust(self):
        expr = f"fetch('{PRIVATE_TRUSTED}')"
        assert browser_tool._expression_targets_private_url(expr) == PRIVATE_TRUSTED

    def test_permits_private_literal_for_trusted_session(self, _enrolled_bound):
        expr = f"fetch('{PRIVATE_TRUSTED}')"
        assert browser_tool._expression_targets_private_url(expr, session_key="task-1") is None

    def test_still_blocks_untrusted_origin_for_trusted_session(self, _enrolled_bound):
        expr = f"fetch('{PRIVATE_UNTRUSTED}')"
        assert (
            browser_tool._expression_targets_private_url(expr, session_key="task-1")
            == PRIVATE_UNTRUSTED
        )

    def test_always_blocked_url_is_never_trusted(self, monkeypatch, _enrolled_bound):
        """Cloud-metadata floor must survive profile trust."""
        monkeypatch.setattr(browser_tool, "_is_always_blocked_url", lambda url: True)
        expr = f"fetch('{PRIVATE_TRUSTED}')"
        assert (
            browser_tool._expression_targets_private_url(expr, session_key="task-1")
            == PRIVATE_TRUSTED
        )

    def test_default_arg_preserves_upstream_behaviour(self):
        """Calling without session_key must behave exactly as before."""
        expr = f"fetch('{PRIVATE_TRUSTED}')"
        assert browser_tool._expression_targets_private_url(expr) == PRIVATE_TRUSTED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `scripts/run_tests.sh tests/tools/test_browser_profile_trust_seam.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.browser_session_registry'`

- [ ] **Step 3a: Create the registry module**

Create `tools/browser_session_registry.py`:

```python
"""Session → profile bindings and the origin-scoped trust decision.

Lives outside ``browser_tool.py`` on purpose: this module holds the mutable
session state so the footprint inside that large shared upstream file stays a
single delegating helper plus two one-line predicate guards (see spec §6a —
every line we add there is a line a future upstream merge can silently revert).

Trust is evaluated per ``(session_key, url)``. There is no blanket session
exemption: binding a session to an enrolled profile grants access ONLY to the
origins that profile explicitly lists.
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_session_profiles: Dict[str, str] = {}  # session_key -> profile name


def bind(session_key: str, profile_name: str) -> None:
    """Record that ``session_key`` is driving ``profile_name``."""
    with _lock:
        _session_profiles[str(session_key)] = str(profile_name)


def unbind(session_key: str) -> None:
    """Forget a session's profile binding (call on release)."""
    with _lock:
        _session_profiles.pop(str(session_key), None)


def profile_for(session_key: str) -> Optional[str]:
    """Return the profile name bound to ``session_key``, or None."""
    with _lock:
        return _session_profiles.get(str(session_key))


def clear() -> None:
    """Drop all bindings. Test helper; also used on interpreter teardown."""
    with _lock:
        _session_profiles.clear()


def session_trusts_url(session_key: str, url: str) -> bool:
    """Return True when ``session_key``'s profile explicitly trusts ``url``.

    SECURITY: gates private-network access. Denies on any unexpected input —
    unbound session, unknown profile, ephemeral profile, or unparseable URL.
    """
    name = profile_for(session_key)
    if not name:
        return False
    try:
        from tools.browser_profiles import get_profile, is_origin_trusted

        return is_origin_trusted(get_profile(name), url)
    except Exception as exc:  # noqa: BLE001
        logger.debug("session_trusts_url: denying after error: %s", exc)
        return False
```

- [ ] **Step 3b: Add the hook to `browser_tool.py`**

Add this helper immediately **above** `_expression_targets_private_url`
(~line 3481):

```python
def _session_trusts_url(session_key: Optional[str], url: str) -> bool:
    """OTTO: return True when this session's browser profile trusts ``url``.

    Origin-scoped escape hatch for the private-network guards. An ``enrolled``
    profile (a corporate browser holding a live SSO/mTLS session, attached over
    CDP) must be able to reach the internal origins it explicitly lists — but
    ``_is_local_backend()`` returns False for any CDP override, so without this
    the guards block the entire enrolled use case.

    Mirrors upstream's per-session ``_is_local_sidecar_key`` exemption, but
    scoped to (session, origin) rather than the whole session. Fails CLOSED.

    See docs/plans/2026-07-20-persistent-enrolled-browser-session-design.md §0.
    """
    if not session_key:
        return False
    try:
        from tools.browser_session_registry import session_trusts_url as _trusts

        return _trusts(session_key, url)
    except Exception:  # noqa: BLE001
        return False
```

Then change the two predicates. `_expression_targets_private_url` gains an
optional `session_key` parameter and one guard line:

```python
def _expression_targets_private_url(
    expression: str, session_key: Optional[str] = None
) -> Optional[str]:
    """Return the first private/always-blocked URL literal in a JS expression.

    Best-effort: scans for ``http(s)://...`` literals (fetch/XHR/navigation
    targets the agent may have embedded) and returns the first one that targets
    a private/internal address or the always-blocked cloud-metadata floor.
    Returns ``None`` when no such literal is found.

    ``session_key`` is OTTO's origin-scoped trust hook: a literal whose origin
    the session's browser profile explicitly trusts is not reported. The
    always-blocked cloud-metadata floor is checked FIRST and is never trusted.
    """
    if not isinstance(expression, str):
        return None
    for match in _JS_URL_LITERAL_RE.findall(expression):
        candidate = match.rstrip(".,;")
        if _is_always_blocked_url(candidate):
            return candidate
        if not _is_safe_url(candidate):
            if _session_trusts_url(session_key, candidate):
                continue
            return candidate
    return None
```

`_current_page_private_url` gains the same treatment — replace its final
condition (~line 3517) with:

```python
            if current_url and _is_always_blocked_url(current_url):
                return current_url
            if (
                current_url
                and not _is_safe_url(current_url)
                and not _session_trusts_url(effective_task_id, current_url)
            ):
                return current_url
```

Finally, in `_browser_eval` (~line 3683) pass the session key through:

```python
        blocked_literal = _expression_targets_private_url(
            expression, session_key=effective_task_id
        )
```

- [ ] **Step 4: Run tests to verify they pass — including the full existing browser suite**

```bash
scripts/run_tests.sh tests/tools/test_browser_profile_trust_seam.py -v
scripts/run_tests.sh tests/tools/test_browser_console_ssrf.py \
                     tests/tools/test_browser_ssrf_local.py \
                     tests/tools/test_browser_snapshot_ssrf.py \
                     tests/tools/test_browser_get_images_ssrf.py \
                     tests/tools/test_browser_private_page_action_guard.py \
                     tests/tools/test_browser_hybrid_routing.py \
                     tests/tools/test_browser_camofox_private_page_guard.py -v
```
Expected: PASS. The existing suite must be green **unchanged** — the new
parameter defaults to `None`, which reproduces upstream behaviour exactly. If
any existing test needed editing, the hook is wrong: revisit it rather than
relaxing the test.

- [ ] **Step 5: Add the governance artifacts (spec §6a) — SAME commit**

Add this row to the surface table in **both** `../CLAUDE.md` and `../AGENTS.md`
(the workspace-root files, not the repo ones):

```markdown
| `hermes-agent/tools/browser_tool.py` (`_session_trusts_url`, `_expression_targets_private_url`, `_current_page_private_url`) + `hermes-agent/tools/browser_profiles.py` + `hermes-agent/tools/browser_session_registry.py` (**new**) | **Per-profile browser sessions — origin-scoped SSRF trust seam (2026-07-25, brand-agnostic on `base`; NOT emitter-owned).** FIRST OTTO edit to `browser_tool.py`. A named-profile registry (`browser.profiles`) lets an `enrolled` corporate browser hold a live SSO/mTLS session over CDP; because upstream's `_is_local_backend()` returns False for ANY CDP override, `_eval_ssrf_guard_active` would otherwise block every internal origin. The hook is one delegating helper plus one guard line in each of the two private-URL predicates, scoped to (session, origin) — NOT a blanket session exemption, and the always-blocked cloud-metadata floor is never trusted. Mirrors upstream's `_is_local_sidecar_key` pattern. Mutable state lives in the new registry module to keep the shared-file footprint minimal. Upstream churns this file hard (v0.19.0 changed 188 lines) → **UNION on merge, NEVER `--theirs`**. Design: `hermes-agent/docs/plans/2026-07-20-persistent-enrolled-browser-session-design.md` §0 | Medium (shared `browser_tool.py`, heavy upstream churn) |
```

Add these greps to `.claude/skills/otto-upstream-merge/SKILL.md` alongside the
existing silent-revert block (~line 171):

```bash
git grep -c '_session_trusts_url' -- tools/browser_tool.py                       # >=3 — helper def + both predicate guards (per-profile SSRF trust seam)
git grep -c 'session_key' -- tools/browser_tool.py                               # >=2 — _expression_targets_private_url param + _browser_eval call site
ls tools/browser_profiles.py tools/browser_session_registry.py                    # both modules must exist
```

Verify the pair is still byte-identical:

```bash
cd /Users/coreyellis/code/github.com/cmetech/otto_hermes && cmp CLAUDE.md AGENTS.md && echo "pair OK"
```
Expected: `pair OK`

- [ ] **Step 6: Validate the in-repo governance ledger**

The versioned record is `docs/upstream-customizations/browser-profiles.yaml`
(see Global Constraints — the workspace surface table is not version
controlled). Validate it and its coverage:

```bash
venv/bin/python scripts/check_upstream_customizations.py \
  --manifest docs/upstream-customizations/browser-profiles.yaml
venv/bin/python scripts/check_upstream_customizations.py \
  --manifest docs/upstream-customizations/browser-profiles.yaml \
  --diff 507a53c8348fe52cee101e05907b7045cb6cfbe0..HEAD
```
Expected: exit 0 from both. Do **not** run `generate <brand> --check` here — it
fails on `base` by design (see Global Constraints).

- [ ] **Step 7: Commit (code + governance together — never split)**

```bash
git add tools/browser_session_registry.py tools/browser_tool.py \
        tests/tools/test_browser_profile_trust_seam.py
git commit -m "feat(browser): origin-scoped SSRF trust seam for browser profiles

Upstream's _is_local_backend() returns False for any CDP override, so an
enrolled corporate browser attached over CDP could not reach the internal
origins it is meant to serve. Adds a per-(session, origin) trust check to the
two private-URL predicates, mirroring the _is_local_sidecar_key pattern.

Fails closed: unbound sessions, ephemeral profiles, and the always-blocked
cloud-metadata floor are never trusted. Default arg preserves upstream
behaviour exactly; existing browser SSRF suite passes unchanged.

First OTTO edit to tools/browser_tool.py — surface-table rows and merge-skill
greps land in the paired workspace commit (spec §6a).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

Then commit the workspace governance files (separate repo, same logical change):

```bash
cd /Users/coreyellis/code/github.com/cmetech/otto_hermes
git add CLAUDE.md AGENTS.md .claude/skills/otto-upstream-merge/SKILL.md
git commit -m "docs(governance): register browser profile SSRF seam as OTTO surface

First OTTO edit to tools/browser_tool.py needs a surface-table row plus
silent-revert greps, else a future main -> base merge reverts it with no
conflict, no test failure, and no build error.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Session manager — acquire / release with daemon hygiene

**Files:**
- Create: `tools/browser_session_manager.py`
- Test: `tests/tools/test_browser_session_manager.py`

**Interfaces:**
- Consumes: `get_profile`, `resolve_executable` (Tasks 1, 3); `bind`/`unbind` (Task 4).
- Produces:
  - `acquire(profile: str = "default", headless: bool | None = None, session_key: str | None = None) -> BrowserSession`
  - `class BrowserSession` with `.session_key`, `.profile`, `.cdp_url`, `.release()`
  - `ProfileError(Exception)`

Spec §2 makes daemon hygiene a **first-class requirement**, not an afterthought:
agent-browser's client-daemon wedged on Windows during testing (`eval` hung
until a full restart). `acquire()` therefore runs `close --all` first and uses
bounded timeouts.

- [ ] **Step 1: Write the failing test**

Create `tests/tools/test_browser_session_manager.py`:

```python
"""Tests for acquire()/release() lifecycle and daemon hygiene."""

import pytest

from tools import browser_profiles, browser_session_manager, browser_session_registry

ENROLLED = browser_profiles.BrowserProfile(
    name="enrolled",
    kind=browser_profiles.KIND_ENROLLED,
    user_data_dir="/tmp/enrolled-profile",
    cdp_port=9222,
    headed=True,
    trusted_origins=("https://wiki.corp.example",),
)


@pytest.fixture(autouse=True)
def _clean():
    browser_session_registry.clear()
    yield
    browser_session_registry.clear()


@pytest.fixture()
def _stub_env(monkeypatch):
    """Stub every external effect; record calls for assertions."""
    calls = {"hygiene": 0, "launched": [], "attached": []}
    monkeypatch.setattr(
        browser_profiles, "get_profile",
        lambda n: ENROLLED if n == "enrolled" else browser_profiles.BrowserProfile(name="default"),
    )
    monkeypatch.setattr(browser_profiles, "resolve_executable", lambda p: "/usr/bin/msedge")
    monkeypatch.setattr(
        browser_session_manager, "_run_daemon_hygiene",
        lambda: calls.__setitem__("hygiene", calls["hygiene"] + 1),
    )
    monkeypatch.setattr(
        browser_session_manager, "_launch_enrolled",
        lambda prof, exe, headless: (calls["launched"].append((prof.name, headless)),
                                     f"http://127.0.0.1:{prof.cdp_port}")[1],
    )
    monkeypatch.setattr(
        browser_session_manager, "_attach_cdp",
        lambda url: calls["attached"].append(url),
    )
    return calls


class TestAcquire:
    def test_enrolled_launches_and_binds_registry(self, _stub_env):
        sess = browser_session_manager.acquire(profile="enrolled")
        assert sess.profile.name == "enrolled"
        assert sess.cdp_url == "http://127.0.0.1:9222"
        assert browser_session_registry.profile_for(sess.session_key) == "enrolled"

    def test_daemon_hygiene_runs_before_launch(self, _stub_env):
        browser_session_manager.acquire(profile="enrolled")
        assert _stub_env["hygiene"] == 1

    def test_headless_defaults_to_profile_headed_inverse(self, _stub_env):
        """profile.headed=True → launch visible (headless=False)."""
        browser_session_manager.acquire(profile="enrolled")
        assert _stub_env["launched"] == [("enrolled", False)]

    def test_explicit_headless_overrides_profile(self, _stub_env):
        browser_session_manager.acquire(profile="enrolled", headless=True)
        assert _stub_env["launched"] == [("enrolled", True)]

    def test_unknown_profile_raises(self, monkeypatch):
        monkeypatch.setattr(browser_profiles, "get_profile", lambda n: None)
        with pytest.raises(browser_session_manager.ProfileError, match="unknown browser profile"):
            browser_session_manager.acquire(profile="ghost")

    def test_enrolled_without_executable_raises(self, monkeypatch, _stub_env):
        monkeypatch.setattr(browser_profiles, "resolve_executable", lambda p: None)
        with pytest.raises(browser_session_manager.ProfileError, match="enrolled browser"):
            browser_session_manager.acquire(profile="enrolled")

    def test_ephemeral_profile_does_not_launch_enrolled(self, _stub_env):
        sess = browser_session_manager.acquire(profile="default")
        assert _stub_env["launched"] == []
        assert sess.cdp_url is None


class TestRelease:
    def test_release_unbinds_registry(self, _stub_env):
        sess = browser_session_manager.acquire(profile="enrolled")
        key = sess.session_key
        sess.release()
        assert browser_session_registry.profile_for(key) is None

    def test_release_is_idempotent(self, _stub_env):
        sess = browser_session_manager.acquire(profile="enrolled")
        sess.release()
        sess.release()  # must not raise

    def test_context_manager_releases(self, _stub_env):
        with browser_session_manager.acquire(profile="enrolled") as sess:
            key = sess.session_key
            assert browser_session_registry.profile_for(key) == "enrolled"
        assert browser_session_registry.profile_for(key) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `scripts/run_tests.sh tests/tools/test_browser_session_manager.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.browser_session_manager'`

- [ ] **Step 3: Write minimal implementation**

Create `tools/browser_session_manager.py`:

```python
"""Acquire/release browser sessions for a named profile.

The importable seam (spec §3): a workflow ``script`` node, the confluence CLI,
and the ``/browser`` command all drive this same code, so there is one
consistent way to manage browsers.

Daemon hygiene is a first-class requirement, not an afterthought — spec §2
records agent-browser's client-daemon wedging on Windows (``eval`` hung until a
full restart). ``acquire()`` always runs ``close --all`` first and every
subprocess call is bounded.
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Optional

from tools import browser_profiles, browser_session_registry

logger = logging.getLogger(__name__)

# Bounded so a wedged daemon surfaces as an error instead of hanging the agent.
HYGIENE_TIMEOUT_S = 15
LAUNCH_TIMEOUT_S = 60


class ProfileError(RuntimeError):
    """Raised when a profile is unknown or cannot be launched."""


def _agent_browser_cmd() -> list:
    """Return the agent-browser CLI invocation, reusing browser_tool's resolver.

    ``_find_agent_browser()`` raises FileNotFoundError when the CLI is missing.
    The ``npx agent-browser`` special case mirrors browser_tool's own idiom: on
    Windows npx is ``npx.cmd``, so ``shutil.which`` is required for
    CreateProcessW to execute the batch shim.
    """
    import shutil

    from tools.browser_tool import _find_agent_browser

    browser_cmd = _find_agent_browser()
    if browser_cmd == "npx agent-browser":
        return [shutil.which("npx") or "npx", "agent-browser"]
    return [browser_cmd]


def _run_daemon_hygiene() -> None:
    """Close every agent-browser session so a wedged daemon can't poison us."""
    try:
        subprocess.run(
            _agent_browser_cmd() + ["close", "--all"],
            capture_output=True, timeout=HYGIENE_TIMEOUT_S, check=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("daemon hygiene skipped: %s", exc)


def _launch_enrolled(profile: browser_profiles.BrowserProfile, executable: str,
                     headless: bool) -> str:
    """Launch the enrolled browser with a persistent profile; return its CDP URL.

    Uses the OS-installed browser (cert store → working mTLS), NOT
    agent-browser's bundled Chrome for Testing.
    """
    user_data_dir = os.path.expandvars(profile.user_data_dir)
    os.makedirs(user_data_dir, exist_ok=True)
    args = [
        executable,
        f"--remote-debugging-port={profile.cdp_port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if headless:
        args.append("--headless=new")
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return f"http://127.0.0.1:{profile.cdp_port}"


def _attach_cdp(cdp_url: str) -> None:
    """Point the browser tool at ``cdp_url`` for subsequent commands."""
    os.environ["BROWSER_CDP_URL"] = cdp_url


class BrowserSession:
    """A live session bound to one profile. Profile is fixed at acquire time.

    The hard isolation rule (spec §5): an untrusted external site must never be
    driven through an enrolled profile, so there is deliberately no API to
    change ``profile`` after construction.
    """

    def __init__(self, session_key: str, profile: browser_profiles.BrowserProfile,
                 cdp_url: Optional[str]):
        self.session_key = session_key
        self.profile = profile
        self.cdp_url = cdp_url
        self._released = False

    def __enter__(self) -> "BrowserSession":
        return self

    def __exit__(self, *_exc) -> None:
        self.release()

    def release(self) -> None:
        """Detach. The on-disk profile persists so SSO survives. Idempotent."""
        if self._released:
            return
        self._released = True
        browser_session_registry.unbind(self.session_key)


def acquire(profile: str = browser_profiles.DEFAULT_PROFILE_NAME,
            headless: Optional[bool] = None,
            session_key: Optional[str] = None) -> BrowserSession:
    """Acquire a session for ``profile``.

    ``headless`` defaults to the inverse of the profile's ``headed`` flag.
    Raises ``ProfileError`` for an unknown profile or an unresolvable enrolled
    browser — never silently falls back to the unmanaged bundled Chrome, which
    would fail corporate mTLS in a confusing way.
    """
    prof = browser_profiles.get_profile(profile)
    if prof is None:
        raise ProfileError(f"unknown browser profile: {profile!r}")

    key = session_key or f"profile::{prof.name}"
    effective_headless = (not prof.headed) if headless is None else bool(headless)

    _run_daemon_hygiene()

    cdp_url: Optional[str] = None
    if prof.is_enrolled:
        executable = browser_profiles.resolve_executable(prof)
        if not executable:
            raise ProfileError(
                f"could not resolve the enrolled browser for profile {prof.name!r}. "
                "Set browser.profiles.<name>.executable to an absolute path."
            )
        cdp_url = _launch_enrolled(prof, executable, effective_headless)
        _attach_cdp(cdp_url)

    browser_session_registry.bind(key, prof.name)
    return BrowserSession(session_key=key, profile=prof, cdp_url=cdp_url)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
scripts/run_tests.sh tests/tools/test_browser_session_manager.py -v
```
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/browser_session_manager.py tests/tools/test_browser_session_manager.py
git commit -m "feat(browser): add profile-aware session acquire/release

Importable seam so /browser, skills, and workflow scripts share one browser
lifecycle. Enrolled profiles launch the OS browser with a persistent
user-data-dir and attach over CDP; ephemeral profiles are unchanged.

Daemon hygiene (close --all + bounded timeouts) runs on every acquire per
design §2 — agent-browser's daemon wedged on Windows during testing. Refuses
to fall back to bundled Chrome for an enrolled profile: silent fallback would
fail corporate mTLS confusingly.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Authenticated-session probe and interactive sign-in

**Files:**
- Modify: `tools/browser_session_manager.py` (append methods to `BrowserSession`)
- Test: `tests/tools/test_browser_session_signin.py`

**Interfaces:**
- Consumes: `BrowserSession` (Task 5).
- Produces:
  - `BrowserSession.navigate(url: str) -> dict`
  - `BrowserSession.eval(expression: str) -> dict`
  - `BrowserSession.is_authenticated(probe_js: str) -> bool`
  - `BrowserSession.signin(url: str, probe_js: str, timeout: int = 300, poll_s: float = 2.0) -> bool`

- [ ] **Step 1: Write the failing test**

Create `tests/tools/test_browser_session_signin.py`:

```python
"""Tests for the auth probe and the visible sign-in wait loop."""

import pytest

from tools import browser_profiles, browser_session_manager, browser_session_registry

ENROLLED = browser_profiles.BrowserProfile(
    name="enrolled", kind=browser_profiles.KIND_ENROLLED,
    user_data_dir="/tmp/p", cdp_port=9222, headed=True,
    trusted_origins=("https://wiki.corp.example",),
)
PROBE = "document.querySelector('#user-menu') !== null"


@pytest.fixture(autouse=True)
def _clean():
    browser_session_registry.clear()
    yield
    browser_session_registry.clear()


def _session():
    return browser_session_manager.BrowserSession(
        session_key="task-1", profile=ENROLLED, cdp_url="http://127.0.0.1:9222"
    )


class TestIsAuthenticated:
    def test_true_when_probe_returns_true(self, monkeypatch):
        monkeypatch.setattr(browser_session_manager.BrowserSession, "eval",
                            lambda self, expr: {"success": True, "result": True})
        assert _session().is_authenticated(PROBE) is True

    def test_false_when_probe_returns_false(self, monkeypatch):
        monkeypatch.setattr(browser_session_manager.BrowserSession, "eval",
                            lambda self, expr: {"success": True, "result": False})
        assert _session().is_authenticated(PROBE) is False

    def test_false_when_eval_fails(self, monkeypatch):
        """A failed probe must read as NOT authenticated, never as success."""
        monkeypatch.setattr(browser_session_manager.BrowserSession, "eval",
                            lambda self, expr: {"success": False, "error": "boom"})
        assert _session().is_authenticated(PROBE) is False

    def test_truthy_string_is_not_authenticated(self, monkeypatch):
        """Only a real boolean True counts — avoid 'false' string truthiness."""
        monkeypatch.setattr(browser_session_manager.BrowserSession, "eval",
                            lambda self, expr: {"success": True, "result": "false"})
        assert _session().is_authenticated(PROBE) is False


class TestSignin:
    def test_returns_immediately_when_already_authenticated(self, monkeypatch):
        navs, sleeps = [], []
        monkeypatch.setattr(browser_session_manager.BrowserSession, "navigate",
                            lambda self, url: navs.append(url))
        monkeypatch.setattr(browser_session_manager.BrowserSession, "is_authenticated",
                            lambda self, probe: True)
        monkeypatch.setattr(browser_session_manager.time, "sleep", lambda s: sleeps.append(s))
        assert _session().signin("https://wiki.corp.example", PROBE) is True
        assert navs == ["https://wiki.corp.example"]
        assert sleeps == []  # no polling needed

    def test_polls_until_authenticated(self, monkeypatch):
        states = [False, False, True]
        monkeypatch.setattr(browser_session_manager.BrowserSession, "navigate",
                            lambda self, url: None)
        monkeypatch.setattr(browser_session_manager.BrowserSession, "is_authenticated",
                            lambda self, probe: states.pop(0))
        monkeypatch.setattr(browser_session_manager.time, "sleep", lambda s: None)
        monkeypatch.setattr(browser_session_manager.time, "monotonic", lambda: 0.0)
        assert _session().signin("https://wiki.corp.example", PROBE) is True
        assert states == []

    def test_times_out_and_returns_false(self, monkeypatch):
        clock = {"t": 0.0}
        monkeypatch.setattr(browser_session_manager.BrowserSession, "navigate",
                            lambda self, url: None)
        monkeypatch.setattr(browser_session_manager.BrowserSession, "is_authenticated",
                            lambda self, probe: False)
        monkeypatch.setattr(browser_session_manager.time, "sleep",
                            lambda s: clock.__setitem__("t", clock["t"] + 10))
        monkeypatch.setattr(browser_session_manager.time, "monotonic", lambda: clock["t"])
        assert _session().signin("https://wiki.corp.example", PROBE, timeout=30) is False


class TestEvalEncoding:
    def test_eval_uses_base64_and_never_stdin(self, monkeypatch):
        """Design §2: base64 IIFE, never --stdin (that path wedged the daemon)."""
        seen = {}

        def fake_run(task_id, command, args, **kw):
            seen["command"], seen["args"] = command, args
            return {"success": True, "data": {"result": 42}}

        monkeypatch.setattr(browser_session_manager, "_run_browser_command", fake_run)
        result = _session().eval("1 + 41")
        assert result["success"] is True
        assert result["result"] == 42
        assert "--stdin" not in seen["args"]
        assert any("base64" in str(a) for a in seen["args"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `scripts/run_tests.sh tests/tools/test_browser_session_signin.py -v`
Expected: FAIL — `AttributeError: 'BrowserSession' object has no attribute 'is_authenticated'`

- [ ] **Step 3: Write minimal implementation**

Add `import base64`, `import time` to `tools/browser_session_manager.py`'s
imports, add this module-level indirection so tests can patch it:

```python
def _run_browser_command(task_id, command, args, **kwargs):
    """Indirection over browser_tool's command runner (patchable in tests)."""
    from tools.browser_tool import _run_browser_command as _run

    return _run(task_id, command, args, **kwargs)
```

and append these methods to `BrowserSession`:

```python
    def navigate(self, url: str) -> dict:
        """Navigate this session's browser to ``url``."""
        return _run_browser_command(self.session_key, "navigate", [url])

    def eval(self, expression: str) -> dict:
        """Evaluate JS in the page and return ``{"success", "result"|"error"}``.

        The expression is wrapped in a base64-encoded IIFE and passed as an
        ARGUMENT — never over ``--stdin``. Spec §2: the stdin path is what
        wedged agent-browser's daemon on Windows.
        """
        payload = base64.b64encode(f"(() => ({expression}))()".encode()).decode()
        wrapped = f"eval(atob('{payload}'))"
        result = _run_browser_command(self.session_key, "eval", [wrapped])
        if not result.get("success"):
            return {"success": False, "error": result.get("error", "eval failed")}
        return {"success": True, "result": result.get("data", {}).get("result")}

    def is_authenticated(self, probe_js: str) -> bool:
        """Return True only when ``probe_js`` evaluates to boolean ``True``.

        Fails CLOSED: an eval error, a missing result, or a truthy non-boolean
        (e.g. the string ``"false"``) all read as NOT authenticated.
        """
        result = self.eval(probe_js)
        return result.get("success") is True and result.get("result") is True

    def signin(self, url: str, probe_js: str, timeout: int = 300,
               poll_s: float = 2.0) -> bool:
        """Navigate to ``url`` and wait for the user to complete sign-in.

        Returns True as soon as ``probe_js`` reports an authenticated session,
        or False on timeout. Cookies land in the profile's persistent
        user-data-dir, so a later headless acquire can reuse the session.

        NOTE (spec §8 open question): enrolled-browser headless reuse vs
        Conditional Access re-checks and cookie lifetime are UNMEASURED.
        Validate before relying on this unattended.
        """
        self.navigate(url)
        if self.is_authenticated(probe_js):
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(poll_s)
            if self.is_authenticated(probe_js):
                return True
        return False
```

- [ ] **Step 4: Run test to verify it passes**

```bash
scripts/run_tests.sh tests/tools/test_browser_session_signin.py -v
scripts/run_tests.sh tests/tools/test_browser_session_manager.py -v
```
Expected: PASS (both files)

- [ ] **Step 5: Commit**

```bash
git add tools/browser_session_manager.py tests/tools/test_browser_session_signin.py
git commit -m "feat(browser): add auth probe, sign-in wait, and base64 eval

is_authenticated() fails closed on eval error or truthy non-boolean. signin()
navigates visibly and polls until the probe passes so cookies flush to the
persistent profile. eval() wraps in a base64 IIFE passed as an argument, never
--stdin (design §2: the stdin path wedged agent-browser's daemon on Windows).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Confluence skill — opt-in SessionManagerBackend

Spec §7 stage 2: add a third backend. It is **opt-in, not default** — the skill
keeps working unchanged until the manager is proven in the real environment.

**Files:**
- Modify: `skills/ericsson/confluence-research/scripts/backends.py` (`_ENGINES` ~line 282)
- Modify: `skills/ericsson/confluence-research/scripts/confluence.py` (two `--engine` `choices` lists, ~lines 302 and 305)
- Test: `tests/tools/test_confluence_session_manager_backend.py`

**Interfaces:**
- Consumes: `acquire`, `BrowserSession` (Tasks 5–6).
- Produces: `SessionManagerBackend(Backend)` registered in `_ENGINES` under key `"session-manager"`.

**The existing contract (verified 2026-07-25 — implement exactly this, do not
invent a new interface).** `backends.py` defines `class Backend` with:

```python
def start(self, headless: bool) -> None: ...
def navigate(self, url: str) -> dict: ...
def eval(self, fn_js: str): ...
def shutdown(self) -> None: ...
@staticmethod
def available() -> bool: ...
```

Registration is `_ENGINES` (underscore-prefixed) mapping name → class, and
`_AUTO_ORDER = ("playwright", "agent-browser")` decides the preference for
`--engine auto`. There is **no `DEFAULT_ENGINE` constant** — the CLI default is
`auto`, so "keep Playwright the default" means **leave `_AUTO_ORDER` unchanged
and do NOT add `session-manager` to it**. The engine stays reachable only by
explicit `--engine session-manager`. Note the method is `shutdown()`, not
`stop()`.

- [ ] **Step 1: Write the failing test**

Create `tests/tools/test_confluence_session_manager_backend.py`:

```python
"""The session-manager backend is registered but NOT default (design §7 stage 2)."""

import importlib.util
import pathlib

import pytest

_BACKENDS = pathlib.Path("skills/ericsson/confluence-research/scripts/backends.py")


def _load():
    spec = importlib.util.spec_from_file_location("cr_backends", _BACKENDS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestRegistration:
    def test_session_manager_engine_is_selectable(self):
        mod = _load()
        assert "session-manager" in mod._ENGINES

    def test_auto_order_is_unchanged_so_playwright_stays_default(self):
        """Opt-in only: --engine auto must not pick the new backend yet."""
        mod = _load()
        assert mod._AUTO_ORDER == ("playwright", "agent-browser")
        assert "session-manager" not in mod._AUTO_ORDER

    def test_implements_the_backend_protocol(self):
        mod = _load()
        cls = mod._ENGINES["session-manager"]
        for method in ("start", "navigate", "eval", "shutdown", "available"):
            assert callable(getattr(cls, method)), f"missing {method}"

    def test_backend_acquires_the_enrolled_profile(self, monkeypatch):
        mod = _load()
        acquired = {}

        class _FakeSession:
            session_key = "k"
            def release(self): acquired["released"] = True

        monkeypatch.setattr(
            mod, "acquire",
            lambda profile, headless=None: (
                acquired.update(profile=profile, headless=headless), _FakeSession()
            )[1],
        )
        backend = mod._ENGINES["session-manager"]()
        backend.start(headless=True)
        assert acquired["profile"] == "enrolled"
        assert acquired["headless"] is True
        backend.shutdown()
        assert acquired["released"] is True

    def test_unavailable_when_manager_missing(self, monkeypatch):
        """available() must be False (not raise) where the manager can't import."""
        mod = _load()
        monkeypatch.setattr(mod, "acquire", None)
        assert mod._ENGINES["session-manager"].available() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `scripts/run_tests.sh tests/tools/test_confluence_session_manager_backend.py -v`
Expected: FAIL — `KeyError: 'session-manager'`

- [ ] **Step 3: Implement the backend**

Add the guarded import near the top of
`skills/ericsson/confluence-research/scripts/backends.py`, so the skill still
loads where the manager is unavailable (the skill's scripts also run standalone
outside the Hermes package):

```python
try:
    from tools.browser_session_manager import acquire
except Exception:  # noqa: BLE001
    acquire = None  # session-manager engine unavailable; other engines still work
```

Then add the backend, implementing the verified `Backend` protocol:

```python
class SessionManagerBackend(Backend):
    """Drive the shared Hermes browser session manager (design §7 stage 2).

    Opt-in via ``--engine session-manager``. Consumes the shared ``enrolled``
    profile so this skill stops carrying its own private Edge launcher. The
    Playwright and agent-browser backends remain the fallback (and ``auto``
    still prefers Playwright) until this is proven against corporate Edge.
    """

    name = "session-manager"

    def __init__(self, profile_dir: Path | None = None) -> None:
        # profile_dir is part of the constructor contract but unused: the
        # profile's user_data_dir comes from browser.profiles config, which is
        # the whole point of centralizing lifecycle.
        self._profile = "enrolled"
        self._session = None

    @staticmethod
    def available() -> bool:
        return acquire is not None

    def start(self, headless: bool) -> None:
        self._session = acquire(profile=self._profile, headless=headless)

    def navigate(self, url: str) -> dict:
        return self._session.navigate(url)

    def eval(self, fn_js: str):
        return self._session.eval(fn_js)

    def shutdown(self) -> None:
        if self._session is not None:
            self._session.release()
            self._session = None
```

Register it **without** touching `_AUTO_ORDER`:

```python
_ENGINES = {
    "playwright": PlaywrightBackend,
    "agent-browser": AgentBrowserBackend,
    "session-manager": SessionManagerBackend,
}
# _AUTO_ORDER deliberately UNCHANGED — `auto` must not pick session-manager
# until it is proven against corporate Edge (design §7 stage 2 → 3).
```

Add `"session-manager"` to **both** `--engine` `choices` lists in
`skills/ericsson/confluence-research/scripts/confluence.py` (~lines 302 and 305)
— missing either one makes the engine unselectable from that subcommand:

```python
choices=["auto", "playwright", "agent-browser", "session-manager"],
```

- [ ] **Step 4: Run test to verify it passes**

Run: `scripts/run_tests.sh tests/tools/test_confluence_session_manager_backend.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Verify the skill's own selftest still passes**

```bash
venv/bin/python skills/ericsson/confluence-research/scripts/confluence.py --help
```
Expected: help text lists `session-manager` in the `--engine` choices.

- [ ] **Step 6: Commit**

```bash
git add skills/ericsson/confluence-research/scripts/backends.py \
        skills/ericsson/confluence-research/scripts/confluence.py \
        tests/tools/test_confluence_session_manager_backend.py
git commit -m "feat(confluence-research): add opt-in session-manager backend

Third engine consuming the shared browser session manager, per design §7
stage 2. Playwright stays the default — the skill is unchanged until the
manager is proven in the real corporate environment.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Documentation, config example, and full-suite verification

**Files:**
- Modify: `cli-config.yaml.example` (document `browser.profiles`)
- Modify: `website/docs/user-guide/features/browser.md`
- Test: full suite

- [ ] **Step 1: Document the config surface**

Add to `cli-config.yaml.example` under the existing `browser:` block. This file
is **hand-union on merge, NOT emitter-covered** (workspace `CLAUDE.md`) — keep
the addition additive and commented:

```yaml
  # Named browser profiles. Absent → exactly today's behaviour.
  # profiles:
  #   default:                    # EXTERNAL, disposable (agent-browser's Chrome)
  #     kind: ephemeral           # never origin-trusted, by design
  #   enrolled:                   # INTERNAL: corporate browser, live SSO/mTLS
  #     kind: enrolled
  #     executable: auto          # auto-resolves OS-installed Edge
  #     user_data_dir: "${HERMES_HOME}/browser-profiles/enrolled"
  #     cdp_port: 9222
  #     headed: true              # visible for interactive sign-in
  #     trusted_origins:          # ONLY these private origins are reachable
  #       - "https://wiki.corp.example"
  #       - "https://*.corp.example"
```

- [ ] **Step 2: Document the feature**

Add a section to `website/docs/user-guide/features/browser.md` covering: what a
profile is, the two kinds, how `trusted_origins` gates private-network access,
the hard isolation rule (never drive untrusted sites through `enrolled`), and
the unmeasured headless-reuse caveat from spec §8.

- [ ] **Step 3: Run the full suite**

```bash
scripts/run_tests.sh
```
Expected: PASS, and **no `⚠ FLAKY` section**. A FLAKY report is a bug to fix,
not noise (AGENTS.md flake policy).

- [ ] **Step 4: Verify governance survived and brands gate green**

```bash
git grep -c '_session_trusts_url' -- tools/browser_tool.py     # expect >=6 after Task 9
ls tools/browser_profiles.py tools/browser_session_registry.py tools/browser_session_manager.py
venv/bin/python scripts/check_upstream_customizations.py \
  --manifest docs/upstream-customizations/browser-profiles.yaml   # exit 0
cd /Users/coreyellis/code/github.com/cmetech/otto_hermes && cmp CLAUDE.md AGENTS.md && echo "pair OK"
```

The brand gate belongs to the per-brand merge, not to this branch: after
`base → otto` / `base → loop24`, `generate <brand> --check` must print 8/8 OK
there. It fails on `base` by design.

- [ ] **Step 5: Commit**

```bash
git add cli-config.yaml.example website/docs/user-guide/features/browser.md
git commit -m "docs(browser): document named browser profiles

Config example plus user-guide section covering profile kinds, trusted_origins
as the private-network gate, the enrolled-profile isolation rule, and the
unmeasured headless-reuse caveat.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Wire the agent's own tools to the enrolled profile (enterprise pages + forms)

**This is the task that makes the priority use case work.** Tasks 1–8 give
*scripts and skills* the enrolled browser. This task gives it to **the agent
answering you in natural language**, so "open this internal page and fill in the
form" works.

Two problems to close:

1. **Binding.** The agent's browser tools key their session on `task_id`
   (default `"default"`), but `acquire()` binds under `profile::<name>`. Those
   keys never match, so the Task 4 trust check never fires for agent tool calls.
2. **Guard coverage.** Task 4 covered the eval paths. Verified 2026-07-25, that
   also covers `click`/`type`/`press` for free — `_blocked_private_page_action`
   (line 3364) delegates to `_current_page_private_url`, which Task 4 makes
   profile-aware. **Forms therefore work once binding is fixed.** Still
   uncovered: `browser_navigate`, `browser_snapshot`, `browser_vision`.

**Files:**
- Modify: `tools/browser_session_registry.py` (default-profile fallback)
- Modify: `tools/browser_tool.py` — navigate guard (~line 2877), snapshot guard (~line 3084), vision guard (~line 4070 block)
- Modify: `/Users/coreyellis/code/github.com/cmetech/otto_hermes/.claude/skills/otto-upstream-merge/SKILL.md` (grep counts change)
- Modify: `cli-config.yaml.example`
- Test: `tests/tools/test_browser_default_profile.py`

**Interfaces:**
- Consumes: `session_trusts_url` (Task 4), `get_profile` (Task 1).
- Produces: `browser_session_registry.default_profile_name() -> str | None`; `session_trusts_url` gains a config fallback for unbound sessions.

**Design note — why the fallback lives in the registry.** Binding at session
creation would mean another hook inside `_get_session_info` (line ~2079), a
heavily-churned upstream function. Instead, an **unbound** session falls back to
`browser.default_profile` from config. All new logic stays in our own module and
`browser_tool.py` gains no additional hook for binding — only the three guard
lines. Smaller §6a surface, same result.

Trust remains origin-scoped: setting `default_profile: enrolled` grants access
**only** to that profile's `trusted_origins`, never to private addresses at
large.

- [ ] **Step 1: Write the failing test**

Create `tests/tools/test_browser_default_profile.py`:

```python
"""The agent's own browser tools inherit the configured default profile.

Without this, only scripts calling acquire() get the enrolled browser; the
agent answering in natural language stays blocked on internal pages.
"""

import pytest

from tools import browser_profiles, browser_session_registry, browser_tool

INTERNAL = "https://wiki.corp.example/display/TEAM/Onboarding"
OTHER_INTERNAL = "https://intranet.other.example/secret"

ENROLLED = browser_profiles.BrowserProfile(
    name="enrolled",
    kind=browser_profiles.KIND_ENROLLED,
    trusted_origins=("https://wiki.corp.example",),
)


@pytest.fixture(autouse=True)
def _clean():
    browser_session_registry.clear()
    yield
    browser_session_registry.clear()


@pytest.fixture()
def _default_enrolled(monkeypatch):
    monkeypatch.setattr(
        browser_profiles, "get_profile", lambda n: ENROLLED if n == "enrolled" else None
    )
    monkeypatch.setattr(
        browser_session_registry, "default_profile_name", lambda: "enrolled"
    )


class TestDefaultProfileFallback:
    def test_unbound_session_uses_default_profile(self, _default_enrolled):
        """The agent's bare task_id has no binding — config must supply it."""
        assert browser_session_registry.session_trusts_url("default", INTERNAL)

    def test_default_profile_is_still_origin_scoped(self, _default_enrolled):
        """Falling back must not grant blanket private-network access."""
        assert not browser_session_registry.session_trusts_url("default", OTHER_INTERNAL)

    def test_no_default_configured_trusts_nothing(self, monkeypatch):
        monkeypatch.setattr(browser_session_registry, "default_profile_name", lambda: None)
        assert not browser_session_registry.session_trusts_url("default", INTERNAL)

    def test_explicit_binding_wins_over_default(self, monkeypatch, _default_enrolled):
        """An acquired session keeps its own profile, ignoring the default."""
        monkeypatch.setattr(
            browser_profiles, "get_profile",
            lambda n: ENROLLED if n == "enrolled"
            else browser_profiles.BrowserProfile(name="default"),
        )
        browser_session_registry.bind("default", "default")  # ephemeral
        assert not browser_session_registry.session_trusts_url("default", INTERNAL)

    def test_default_profile_name_reads_config(self, monkeypatch):
        monkeypatch.setattr(
            browser_session_registry, "_read_config",
            lambda: {"browser": {"default_profile": "enrolled"}},
        )
        assert browser_session_registry.default_profile_name() == "enrolled"

    def test_default_profile_name_absent_is_none(self, monkeypatch):
        monkeypatch.setattr(browser_session_registry, "_read_config", lambda: {})
        assert browser_session_registry.default_profile_name() is None


class TestNavigateGuard:
    """browser_navigate must permit a trusted internal origin."""

    @pytest.fixture(autouse=True)
    def _patches(self, monkeypatch, _default_enrolled):
        monkeypatch.setattr(browser_tool, "_is_local_backend", lambda: False)
        monkeypatch.setattr(browser_tool, "_allow_private_urls", lambda: False)
        monkeypatch.setattr(browser_tool, "_is_safe_url", lambda url: False)
        monkeypatch.setattr(browser_tool, "_is_always_blocked_url", lambda url: False)
        monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: False)
        monkeypatch.setattr(browser_tool, "check_website_access", lambda url: None)
        monkeypatch.setattr(
            browser_tool, "_get_session_info",
            lambda task_id: {"session_name": "s", "bb_session_id": None,
                             "cdp_url": None, "features": {}, "_first_nav": False},
        )
        monkeypatch.setattr(
            browser_tool, "_run_browser_command",
            lambda *a, **kw: {"success": True, "data": {"title": "OK", "url": INTERNAL}},
        )

    def test_trusted_internal_url_is_permitted(self):
        out = browser_tool.browser_navigate(INTERNAL, task_id="default")
        assert "private or internal address" not in out

    def test_untrusted_internal_url_is_still_blocked(self):
        out = browser_tool.browser_navigate(OTHER_INTERNAL, task_id="default")
        assert "private or internal address" in out

    def test_metadata_floor_still_fires(self, monkeypatch):
        """The always-blocked cloud-metadata floor outranks profile trust."""
        monkeypatch.setattr(browser_tool, "_is_always_blocked_url", lambda url: True)
        out = browser_tool.browser_navigate(INTERNAL, task_id="default")
        assert "cloud metadata endpoint" in out


class TestSnapshotGuard:
    def test_trusted_internal_page_snapshot_is_permitted(self, monkeypatch, _default_enrolled):
        monkeypatch.setattr(browser_tool, "_is_local_backend", lambda: False)
        monkeypatch.setattr(browser_tool, "_allow_private_urls", lambda: False)
        monkeypatch.setattr(browser_tool, "_is_safe_url", lambda url: False)
        assert browser_tool._snapshot_blocked_url("default", INTERNAL) is None

    def test_untrusted_internal_page_snapshot_is_blocked(self, monkeypatch, _default_enrolled):
        monkeypatch.setattr(browser_tool, "_is_local_backend", lambda: False)
        monkeypatch.setattr(browser_tool, "_allow_private_urls", lambda: False)
        monkeypatch.setattr(browser_tool, "_is_safe_url", lambda url: False)
        assert browser_tool._snapshot_blocked_url("default", OTHER_INTERNAL) == OTHER_INTERNAL
```

- [ ] **Step 2: Run test to verify it fails**

Run: `scripts/run_tests.sh tests/tools/test_browser_default_profile.py -v`
Expected: FAIL — no attribute `default_profile_name`

- [ ] **Step 3a: Add the config fallback to the registry**

In `tools/browser_session_registry.py`, add:

```python
def _read_config() -> dict:
    """Read raw config. Separate function so tests can monkeypatch it."""
    try:
        from hermes_cli.config import read_raw_config

        return read_raw_config() or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("browser_session_registry: could not read config: %s", exc)
        return {}


def default_profile_name() -> Optional[str]:
    """Return ``browser.default_profile``, or None when unset.

    This is what lets the AGENT's own browser tools reach enterprise pages: the
    agent keys sessions on its bare ``task_id``, which nothing binds explicitly.
    Rather than hooking session creation inside the heavily-churned
    ``_get_session_info``, an unbound session falls back to this config value.

    Trust stays origin-scoped — the named profile's ``trusted_origins`` still
    decide which hosts are reachable.
    """
    cfg = _read_config()
    browser_cfg = cfg.get("browser") if isinstance(cfg, dict) else None
    if not isinstance(browser_cfg, dict):
        return None
    name = str(browser_cfg.get("default_profile", "") or "").strip()
    return name or None
```

and change `session_trusts_url` to fall back:

```python
def session_trusts_url(session_key: str, url: str) -> bool:
    """Return True when ``session_key``'s profile explicitly trusts ``url``.

    Resolution order: an explicit ``bind()`` wins; otherwise
    ``browser.default_profile`` applies. An explicitly-bound ephemeral session
    therefore does NOT silently inherit enrolled trust.

    SECURITY: gates private-network access. Denies on any unexpected input.
    """
    name = profile_for(session_key) or default_profile_name()
    if not name:
        return False
    try:
        from tools.browser_profiles import get_profile, is_origin_trusted

        return is_origin_trusted(get_profile(name), url)
    except Exception as exc:  # noqa: BLE001
        logger.debug("session_trusts_url: denying after error: %s", exc)
        return False
```

- [ ] **Step 3b: Thread trust into the navigate guard**

In `tools/browser_tool.py`, the private-address block (~line 2877). The
always-blocked metadata floor immediately above it stays **untouched and
unconditional**:

```python
    if (
        not _is_local_backend()
        and not auto_local_this_nav
        and not _allow_private_urls()
        and not _is_safe_url(url)
        # OTTO: an enrolled profile may reach the internal origins it lists.
        and not _session_trusts_url(nav_session_key, url)
    ):
```

- [ ] **Step 3c: Extract and reuse a snapshot/vision guard helper**

`browser_snapshot` and `browser_vision` each inline the same probe-and-compare
block. Add one shared helper next to `_session_trusts_url` so the trust check
exists in a single place rather than being duplicated twice:

```python
def _snapshot_blocked_url(effective_task_id: str, current_url: str) -> Optional[str]:
    """Return ``current_url`` when a snapshot/screenshot of it must be withheld.

    Shared by browser_snapshot and browser_vision. OTTO: a URL whose origin the
    session's browser profile trusts is NOT withheld — that is the whole point
    of the enrolled profile (reading internal pages). The always-blocked
    cloud-metadata floor is checked first and is never trusted.
    """
    if not current_url:
        return None
    if _is_always_blocked_url(current_url):
        return current_url
    if not _is_safe_url(current_url) and not _session_trusts_url(
        effective_task_id, current_url
    ):
        return current_url
    return None
```

Then in **both** guards replace the inline comparison. In `browser_snapshot`
(~line 3084) change:

```python
                    if _current_url and not _is_safe_url(_current_url):
```

to:

```python
                    if _snapshot_blocked_url(effective_task_id, _current_url):
```

Apply the identical replacement in `browser_vision`'s block (~line 4070). Read
each block first — the local variable names differ between the two.

- [ ] **Step 4: Run tests to verify they pass, and that nothing regressed**

```bash
scripts/run_tests.sh tests/tools/test_browser_default_profile.py -v
scripts/run_tests.sh tests/tools/test_browser_profile_trust_seam.py \
                     tests/tools/test_browser_console_ssrf.py \
                     tests/tools/test_browser_ssrf_local.py \
                     tests/tools/test_browser_snapshot_ssrf.py \
                     tests/tools/test_browser_get_images_ssrf.py \
                     tests/tools/test_browser_private_page_action_guard.py \
                     tests/tools/test_browser_hybrid_routing.py \
                     tests/tools/test_browser_camofox_private_page_guard.py -v
```
Expected: PASS. The existing suite must stay green **unedited** — with no
`browser.default_profile` configured, `default_profile_name()` returns None and
every guard behaves exactly as upstream.

- [ ] **Step 5: Confirm form controls are actually reachable**

The click/type/press path is covered transitively via
`_blocked_private_page_action` → `_current_page_private_url`. Prove it rather
than assuming:

```bash
scripts/run_tests.sh tests/tools/test_browser_private_page_action_guard.py -v
git grep -n '_current_page_private_url' -- tools/browser_tool.py
```
Expected: the action-guard suite passes, and `_blocked_private_page_action` is
visibly among the callers of the now-profile-aware `_current_page_private_url`.

- [ ] **Step 6: Document the config and update the §6a greps**

Add to `cli-config.yaml.example` under `browser:`:

```yaml
  # Profile the agent's own browser tools use when nothing else is bound.
  # Set this to reach enterprise/intranet pages and forms by natural language.
  # Grants access ONLY to the named profile's trusted_origins.
  # default_profile: enrolled
```

Update the grep in `.claude/skills/otto-upstream-merge/SKILL.md` — the count
rises now that the seam covers navigate, snapshot, and vision:

```bash
git grep -c '_session_trusts_url' -- tools/browser_tool.py                       # >=6 — helper + eval x2 + navigate + _snapshot_blocked_url (shared by snapshot & vision)
git grep -c '_snapshot_blocked_url' -- tools/browser_tool.py                     # >=3 — helper def + snapshot guard + vision guard
git grep -c 'default_profile_name' -- tools/browser_session_registry.py          # >=2 — def + session_trusts_url fallback
```

- [ ] **Step 7: Commit**

```bash
git add tools/browser_session_registry.py tools/browser_tool.py \
        cli-config.yaml.example tests/tools/test_browser_default_profile.py
git commit -m "feat(browser): let the agent's own tools use the enrolled profile

Tasks 1-8 gave scripts and skills the enrolled browser; the agent answering in
natural language stayed blocked, because it keys sessions on a bare task_id
that nothing binds. Unbound sessions now fall back to browser.default_profile,
so 'open this internal page and fill the form' works.

Extends the trust seam to navigate, snapshot, and vision. click/type/press were
already covered transitively via _blocked_private_page_action ->
_current_page_private_url. Trust stays origin-scoped and the always-blocked
cloud-metadata floor outranks it everywhere.

Fallback lives in our own registry module so no hook is added to the
heavily-churned _get_session_info.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Deferred / out of scope

These are recorded so they are not silently lost.

- **Desktop half.** Decided 2026-07-25: extend upstream `computer_use`, do not fork. `docs/2026-07-23-hermes-computer-use-reliability-planning-prompt.md` is superseded. If a downstream layer is wanted later (natural-language skill guidance, brand curation), that is a separate small plan — no `cua_desktop` tool, no hiding upstream `computer_use`.
- **`/browser --profile enrolled` chat-command flag** (spec §8 q4). Still excluded — but Task 9 makes it unnecessary for the priority use case: `browser.default_profile` gives the agent enrolled access by config, so a per-call flag is only needed to switch profiles mid-conversation. Revisit if that need appears.
- **Flipping the confluence default to `session-manager`** (spec §7 stage 3). Requires real-environment proof first.
- **Retiring the skill's private Edge launcher** (spec §7 stage 3). Not before `/browser` is trusted for internal *and* external.
- **Ephemeral-profile eval tightening.** Spec §5 assumed the denylist was on by default; v0.19.0 made it opt-in (`browser.restrict_evaluate`). Whether to tighten the ephemeral default is now a separate product decision, not part of this plan.
- **Measurements owed (spec §8).** Enrolled-Edge headless reuse vs Conditional Access re-checks; cookie lifetime; whether `close --all` on acquire is sufficient or a health-probe + relaunch loop is needed on Windows. All need the real corporate environment.

## Verification criteria (from spec §9, updated for v0.19.0)

- `/browser` external flow byte-unchanged — the existing browser SSRF/hybrid-routing suite passes **without edits**.
- `acquire("enrolled")` launches the enrolled browser, persists the profile, and a same-origin fetch to a `trusted_origins` host returns 200 in the real corporate environment.
- Eval `fetch` to a NON-trusted origin under `enrolled` is DENIED.
- Eval `fetch` under `default`/ephemeral is DENIED.
- The always-blocked cloud-metadata floor is denied even for a trusted enrolled session — at every guard site (navigate, eval, snapshot, vision, page actions).
- **Enterprise priority:** with `browser.default_profile: enrolled`, a natural-language request navigates to a listed internal origin, snapshots it, and completes a form (`browser_type` + `browser_click`) without hitting a guard.
- Confluence `SessionManagerBackend` produces byte-identical Markdown to the Playwright backend for the same page.
- `generate <brand> --check` 8/8 on otto and loop24; `cmp CLAUDE.md AGENTS.md` clean; the three §6a greps all return their expected counts.
