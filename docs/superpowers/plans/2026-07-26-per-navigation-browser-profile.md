# Per-Navigation Browser Profile Selection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Choose the browser per navigation — trusted origins reach the user's real enrolled browser on their own session key, everything else stays on the throwaway browser — so untrusted content can never touch corporate SSO cookies or client certificates.

**Architecture:** Reuse the existing hybrid-routing machinery. `_navigation_session_key` already splits one task across two backends by URL (`task_id::local`); this adds a `task_id::enrolled` branch driven by the enrolled profile's `trusted_origins`. Enrolled acquisition is restricted to that key, the process-global `BROWSER_CDP_URL` side effect is removed in favour of per-session endpoint records, and the SSRF guard is explicitly forced on for enrolled keys so removing the global cannot silently disable origin-scoping.

**Tech Stack:** Python 3.11, pytest via `scripts/run_tests.sh`, agent-browser CLI over CDP.

**Design of record:** `docs/plans/2026-07-26-per-navigation-browser-profile-design.md`
**Review that forced it:** `docs/reviews/2026-07-26-enrolled-browser-launch-adversarial-review.md`

## Global Constraints

- **Always run tests with `scripts/run_tests.sh`, never `pytest` directly.** AGENTS.md mandates this; direct pytest lacks the subprocess-isolation plugin and produces phantom cross-file failures.
- **These suites must pass UNEDITED at every task:** `tests/tools/test_browser_ssrf_local.py`, `test_browser_eval_ssrf.py`, `test_browser_console_ssrf.py`, `test_browser_snapshot_ssrf.py`, `test_browser_get_images_ssrf.py`, `test_browser_private_page_action_guard.py`, `test_browser_hybrid_routing.py`, `test_browser_camofox_private_page_guard.py`. If one needs editing, the change altered a boundary it must not touch.
- **`tests/tools/test_browser_default_profile.py` and `test_browser_profile_trust_seam.py` are OTTO-owned** and may be edited where a task says so.
- **Never fall back to the bundled browser.** An unresolvable enrolled browser raises `ProfileError`. Silent fallback is the failure mode this whole feature exists to prevent.
- **The always-blocked cloud-metadata floor is evaluated FIRST at every guard site and is never trusted**, under any profile.
- `_is_local_sidecar_key` keeps its exact meaning: "force local Chromium". An enrolled key is never a local sidecar.
- Commit after every task.

---

### Task 1: Enrolled session-key predicate, and force the SSRF guard on

Removing the global (Task 5) makes `_is_local_backend()` return `True` on an ordinary local install, which switches the SSRF guard **off** and would let the enrolled browser reach any private origin instead of only its trusted ones. This task lands the protection first, while it is still inert.

**Files:**
- Modify: `tools/browser_tool.py` (add predicate near `_is_local_sidecar_key:1447`; add one disjunct at each of 6 guard sites: `2980`, `3007`, `3087`, `3203`, `3601`, `4265`)
- Test: `tests/tools/test_browser_enrolled_routing.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `_ENROLLED_SUFFIX: str = "::enrolled"`; `_is_enrolled_session_key(session_key: str) -> bool`.

- [ ] **Step 1: Write the failing test**

Create `tests/tools/test_browser_enrolled_routing.py`:

```python
"""Per-navigation profile routing: which browser drives which URL.

Design: docs/plans/2026-07-26-per-navigation-browser-profile-design.md
"""

import pytest

from tools import browser_profiles, browser_session_registry, browser_tool


@pytest.fixture(autouse=True)
def _clean():
    browser_session_registry.clear()
    yield
    browser_session_registry.clear()


class TestEnrolledSessionKey:
    def test_enrolled_key_is_recognised(self):
        assert browser_tool._is_enrolled_session_key("task-1::enrolled")

    def test_bare_key_is_not_enrolled(self):
        assert not browser_tool._is_enrolled_session_key("task-1")

    def test_local_sidecar_is_not_enrolled(self):
        assert not browser_tool._is_enrolled_session_key("task-1::local")

    def test_enrolled_key_is_not_a_local_sidecar(self):
        """::local means 'force local Chromium'; an enrolled key must not match."""
        assert not browser_tool._is_local_sidecar_key("task-1::enrolled")


class TestGuardForcedOnForEnrolled:
    """Removing the BROWSER_CDP_URL global must not silently disable the guard."""

    @pytest.fixture(autouse=True)
    def _local_backend(self, monkeypatch):
        # An ordinary local install: upstream considers the guard unnecessary.
        monkeypatch.setattr(browser_tool, "_is_local_backend", lambda: True)
        monkeypatch.setattr(browser_tool, "_allow_private_urls", lambda: False)

    def test_guard_is_forced_on_for_an_enrolled_key(self):
        assert browser_tool._eval_ssrf_guard_active("task-1::enrolled") is True

    def test_bare_key_keeps_upstream_behaviour(self):
        assert browser_tool._eval_ssrf_guard_active("task-1") is False

    def test_local_sidecar_keeps_upstream_behaviour(self):
        assert browser_tool._eval_ssrf_guard_active("task-1::local") is False

    def test_allow_private_urls_still_overrides(self, monkeypatch):
        """The operator's blunt global switch keeps its meaning."""
        monkeypatch.setattr(browser_tool, "_allow_private_urls", lambda: True)
        assert browser_tool._eval_ssrf_guard_active("task-1::enrolled") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `scripts/run_tests.sh tests/tools/test_browser_enrolled_routing.py -q`
Expected: FAIL — `AttributeError: module 'tools.browser_tool' has no attribute '_is_enrolled_session_key'`

- [ ] **Step 3: Add the predicate**

In `tools/browser_tool.py`, immediately after `_is_local_sidecar_key` (line 1447-1449):

```python
_ENROLLED_SUFFIX = "::enrolled"


def _is_enrolled_session_key(session_key: str) -> bool:
    """OTTO: True when this key drives the user's real enrolled browser.

    Pure suffix test, mirroring _is_local_sidecar_key. Must not read config or
    launch anything: guard sites call it on every action.
    """
    return str(session_key).endswith(_ENROLLED_SUFFIX)
```

- [ ] **Step 4: Force the guard on at all 6 sites**

In `_eval_ssrf_guard_active` (line 3591), replace the return expression:

```python
    return (
        (not _is_local_backend() or _is_enrolled_session_key(effective_task_id))
        and not _is_local_sidecar_key(effective_task_id)
        and not _allow_private_urls()
    )
```

At the five remaining sites, replace `not _is_local_backend()` with the disjunct, using the session key already in scope at each:

- line 2980 (`browser_navigate` sensitive-query-param), key `nav_session_key`:
  `if sensitive_query_key and (not _is_local_backend() or _is_enrolled_session_key(nav_session_key)) and not auto_local_this_nav:`
- line 3007 (`browser_navigate` pre-navigation private block), key `nav_session_key`:
  `(not _is_local_backend() or _is_enrolled_session_key(nav_session_key))`
- line 3087 (`browser_navigate` post-redirect block), key `nav_session_key`: same form.
- line 3203 (`browser_snapshot` current-URL recheck), key `effective_task_id`: same form.
- line 4265 (`browser_vision` screenshot recheck), key `effective_task_id`: same form.

- [ ] **Step 5: Run tests to verify they pass**

```bash
scripts/run_tests.sh tests/tools/test_browser_enrolled_routing.py -q
scripts/run_tests.sh tests/tools/ -k browser -q
```
Expected: new file PASS; browser-selected total still 654 passed + the new tests, 0 failed.

- [ ] **Step 6: Commit**

```bash
git add tools/browser_tool.py tests/tools/test_browser_enrolled_routing.py
git commit -m "fix(browser): force the SSRF guard on for enrolled session keys"
```

---

### Task 2: Only enrolled keys drive the enrolled browser

Kills EBL-002 immediately: the bare task key can no longer be enrolled, so no unbound session drives the corporate browser. The feature is unreachable for the agent until Task 3 restores it, correctly scoped.

**Files:**
- Modify: `tools/browser_tool.py` (`_session_browser_profile`, ~line 508)
- Test: `tests/tools/test_browser_enrolled_routing.py` (append)

**Interfaces:**
- Consumes: `_is_enrolled_session_key` (Task 1).
- Produces: `_session_browser_profile(session_key)` returns an enrolled profile **only** for an enrolled-suffixed key or a key explicitly bound to an enrolled profile.

- [ ] **Step 1: Write the failing test**

Append to `tests/tools/test_browser_enrolled_routing.py`:

```python
ENROLLED = browser_profiles.BrowserProfile(
    name="corp",
    kind=browser_profiles.KIND_ENROLLED,
    trusted_origins=("https://wiki.corp.example",),
)


@pytest.fixture()
def _default_enrolled(monkeypatch):
    monkeypatch.setattr(
        browser_profiles, "get_profile",
        lambda n: ENROLLED if n == "corp" else (
            browser_profiles.BrowserProfile(name="default") if n == "default" else None
        ),
    )
    monkeypatch.setattr(browser_session_registry, "default_profile_name", lambda: "corp")


class TestOnlyEnrolledKeysDrive:
    def test_bare_key_never_drives_enrolled(self, _default_enrolled):
        """EBL-002: an unbound session must not get the corporate browser."""
        assert browser_tool._session_browser_profile("task-1") is None
        assert browser_tool._session_uses_enrolled_browser("task-1") is False

    def test_enrolled_key_drives_enrolled(self, _default_enrolled):
        profile = browser_tool._session_browser_profile("task-1::enrolled")
        assert profile is not None and profile.is_enrolled
        assert browser_tool._session_uses_enrolled_browser("task-1::enrolled") is True

    def test_explicit_bind_still_drives(self, _default_enrolled):
        """Scripted callers (confluence CLI, workflow script nodes) bind their key."""
        browser_session_registry.bind("scripted", "corp")
        assert browser_tool._session_uses_enrolled_browser("scripted") is True

    def test_explicit_ephemeral_bind_never_drives_enrolled(self, _default_enrolled):
        browser_session_registry.bind("task-1::enrolled", "default")
        assert browser_tool._session_uses_enrolled_browser("task-1::enrolled") is False

    def test_local_sidecar_never_drives_enrolled(self, _default_enrolled):
        assert browser_tool._session_uses_enrolled_browser("task-1::local") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `scripts/run_tests.sh tests/tools/test_browser_enrolled_routing.py::TestOnlyEnrolledKeysDrive -q`
Expected: FAIL on `test_bare_key_never_drives_enrolled` — the bare key currently resolves through `default_profile_name()` and returns the enrolled profile.

- [ ] **Step 3: Restrict the resolver**

Replace the body of `_session_browser_profile` in `tools/browser_tool.py`:

```python
def _session_browser_profile(session_key: Optional[str]):
    """OTTO: return the ``BrowserProfile`` this session drives, or None.

    PURE — resolves config only and launches nothing, so guards may consult it
    without starting a browser.

    An explicit ``bind()`` always wins, so scripted callers keep driving the
    profile they bound. ``browser.default_profile`` applies ONLY to an
    enrolled-suffixed key, which routing creates for an origin the profile
    explicitly trusts. The bare task key is therefore always ephemeral: an
    untrusted page can never be loaded by the corporate browser.
    """
    if not session_key:
        return None
    try:
        from tools import browser_session_registry
        from tools.browser_profiles import get_profile

        bound = browser_session_registry.profile_for(session_key)
        if bound:
            return get_profile(bound)
        if not _is_enrolled_session_key(session_key):
            return None
        name = browser_session_registry.default_profile_name()
        return get_profile(name) if name else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("browser profile lookup failed for %s: %s", session_key, exc)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
scripts/run_tests.sh tests/tools/test_browser_enrolled_routing.py -q
scripts/run_tests.sh tests/tools/test_browser_enrolled_launch.py -q
```
Expected: routing file PASS. `test_browser_enrolled_launch.py` will now FAIL where it asserts a bare key drives enrolled — that is correct and Task 3 updates it.

- [ ] **Step 5: Update the superseded launch tests**

In `tests/tools/test_browser_enrolled_launch.py`, change every `_session_cdp_url("task-1")` / `_get_session_info("task-1")` assertion that expects the ENROLLED endpoint to use `"task-1::enrolled"`, and add one test pinning the new rule:

```python
    def test_bare_key_no_longer_drives_enrolled(self, monkeypatch, _default_enrolled):
        """Superseded by per-navigation routing: see EBL-002."""
        spy = _AcquireSpy()
        monkeypatch.setattr(browser_session_manager, "acquire", spy)
        assert browser_tool._session_cdp_url("task-1") == OVERRIDE
        assert spy.calls == []
```

- [ ] **Step 6: Run the full browser selection**

Run: `scripts/run_tests.sh tests/tools/ -k browser -q`
Expected: 0 failed.

- [ ] **Step 7: Commit**

```bash
git add tools/browser_tool.py tests/tools/test_browser_enrolled_routing.py tests/tools/test_browser_enrolled_launch.py
git commit -m "fix(browser): only an enrolled session key may drive the enrolled browser"
```

---

### Task 3: Route trusted origins to the enrolled key

**Files:**
- Modify: `tools/browser_session_registry.py` (add `default_profile_trusts_url`)
- Modify: `tools/browser_tool.py` (`_navigation_session_key`, line 1417)
- Test: `tests/tools/test_browser_enrolled_routing.py` (append)

**Interfaces:**
- Consumes: `_ENROLLED_SUFFIX` (Task 1).
- Produces: `browser_session_registry.default_profile_trusts_url(url: str) -> bool`; `_navigation_session_key` may now return `f"{task_id}::enrolled"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/tools/test_browser_enrolled_routing.py`:

```python
TRUSTED = "https://wiki.corp.example/display/TEAM/Onboarding"
UNTRUSTED_PRIVATE = "https://intranet.other.example/secret"
PUBLIC = "https://example.com/page"


class TestNavigationRouting:
    @pytest.fixture(autouse=True)
    def _plain_local(self, monkeypatch, _default_enrolled):
        monkeypatch.setattr(browser_tool, "_get_cdp_override", lambda: "")
        monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: False)
        monkeypatch.setattr(browser_tool, "_get_cloud_provider", lambda: None)

    def test_trusted_origin_routes_to_enrolled(self):
        assert browser_tool._navigation_session_key("t", TRUSTED) == "t::enrolled"

    def test_public_origin_stays_on_the_bare_key(self):
        assert browser_tool._navigation_session_key("t", PUBLIC) == "t"

    def test_untrusted_private_origin_stays_on_the_bare_key(self):
        assert browser_tool._navigation_session_key("t", UNTRUSTED_PRIVATE) == "t"

    def test_cdp_override_suppresses_enrolled_routing(self, monkeypatch):
        """/browser connect owns the whole session."""
        monkeypatch.setattr(browser_tool, "_get_cdp_override", lambda: "ws://x")
        assert browser_tool._navigation_session_key("t", TRUSTED) == "t"

    def test_camofox_suppresses_enrolled_routing(self, monkeypatch):
        monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: True)
        assert browser_tool._navigation_session_key("t", TRUSTED) == "t"

    def test_enrolled_outranks_the_local_sidecar(self, monkeypatch):
        """A trusted origin under a cloud provider gets the corporate browser."""
        monkeypatch.setattr(browser_tool, "_get_cloud_provider", lambda: object())
        monkeypatch.setattr(browser_tool, "_auto_local_for_private_urls", lambda: True)
        monkeypatch.setattr(browser_tool, "_url_is_private", lambda u: True)
        assert browser_tool._navigation_session_key("t", TRUSTED) == "t::enrolled"

    def test_untrusted_private_still_gets_the_local_sidecar(self, monkeypatch):
        monkeypatch.setattr(browser_tool, "_get_cloud_provider", lambda: object())
        monkeypatch.setattr(browser_tool, "_auto_local_for_private_urls", lambda: True)
        monkeypatch.setattr(browser_tool, "_url_is_private", lambda u: True)
        assert browser_tool._navigation_session_key("t", UNTRUSTED_PRIVATE) == "t::local"

    def test_no_default_profile_never_routes_enrolled(self, monkeypatch):
        monkeypatch.setattr(browser_session_registry, "default_profile_name", lambda: None)
        assert browser_tool._navigation_session_key("t", TRUSTED) == "t"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `scripts/run_tests.sh tests/tools/test_browser_enrolled_routing.py::TestNavigationRouting -q`
Expected: FAIL — `_navigation_session_key` returns `"t"` for the trusted origin.

- [ ] **Step 3: Add the routing predicate**

In `tools/browser_session_registry.py`, after `default_profile_name`:

```python
def default_profile_trusts_url(url: str) -> bool:
    """Return True when ``browser.default_profile`` is an enrolled profile that
    explicitly trusts ``url``'s origin.

    This is the ROUTING question — "should this navigation use the real
    installed browser?" — and is deliberately separate from
    ``session_trusts_url``, which answers the GUARD question for an existing
    session. Both consult the same ``is_origin_trusted`` matcher, so routing and
    trust can never disagree about an origin. Fails CLOSED.
    """
    name = default_profile_name()
    if not name:
        return False
    try:
        from tools.browser_profiles import get_profile, is_origin_trusted

        return is_origin_trusted(get_profile(name), url)
    except Exception as exc:  # noqa: BLE001
        logger.debug("default_profile_trusts_url: denying after error: %s", exc)
        return False
```

- [ ] **Step 4: Add the routing branch**

In `tools/browser_tool.py` `_navigation_session_key`, after the Camofox check and **before** the cloud-provider check:

```python
    if _is_camofox_mode():
        return task_id
    # OTTO: an origin the enrolled profile explicitly trusts is driven by the
    # user's REAL installed browser on its own session key. Everything else --
    # public pages, untrusted private addresses -- stays on the bare key and the
    # throwaway browser, so untrusted content never touches corporate SSO
    # cookies or client certificates. Ordered after the CDP-override and Camofox
    # checks (those backends own their session) and before the cloud/hybrid
    # split (an explicitly trusted origin outranks a local sidecar).
    # No filesystem probe here: an unresolvable executable surfaces at acquire
    # time as ProfileError, never as a silent downgrade to the bundled browser.
    try:
        from tools.browser_session_registry import default_profile_trusts_url

        if default_profile_trusts_url(url):
            return f"{task_id}{_ENROLLED_SUFFIX}"
    except Exception as exc:  # noqa: BLE001
        logger.debug("enrolled routing check failed for %s: %s", task_id, exc)
    if _get_cloud_provider() is None:
        return task_id
```

Also update the docstring's numbered list to note the enrolled branch.

- [ ] **Step 5: Run tests to verify they pass**

```bash
scripts/run_tests.sh tests/tools/test_browser_enrolled_routing.py -q
scripts/run_tests.sh tests/tools/test_browser_hybrid_routing.py -q
scripts/run_tests.sh tests/tools/ -k browser -q
```
Expected: all PASS; hybrid-routing suite passes **unedited**.

- [ ] **Step 6: Commit**

```bash
git add tools/browser_tool.py tools/browser_session_registry.py tests/tools/test_browser_enrolled_routing.py
git commit -m "feat(browser): route trusted origins to the enrolled browser per navigation"
```

---

### Task 4: Stop bare keys inheriting trust at guard sites

With routing in place a trusted origin is always on the enrolled key, so the bare key no longer needs the `default_profile` trust fallback. Removing it means an ephemeral session cannot reach internal origins at all.

**Files:**
- Modify: `tools/browser_session_registry.py` (`session_trusts_url`)
- Test: `tests/tools/test_browser_default_profile.py` (OTTO-owned — edit permitted)

**Interfaces:**
- Consumes: `_ENROLLED_SUFFIX` semantics (Task 1).
- Produces: `session_trusts_url` grants trust only to an explicitly bound key or an enrolled-suffixed key.

- [ ] **Step 1: Write the failing test**

Append to `tests/tools/test_browser_enrolled_routing.py`:

```python
class TestGuardTrustScoping:
    def test_enrolled_key_is_trusted(self, _default_enrolled):
        assert browser_session_registry.session_trusts_url("t::enrolled", TRUSTED)

    def test_bare_key_is_not_trusted(self, _default_enrolled):
        """Routing sends trusted origins to the enrolled key, so the ephemeral
        session has no reason to reach internal origins."""
        assert not browser_session_registry.session_trusts_url("t", TRUSTED)

    def test_enrolled_key_is_still_origin_scoped(self, _default_enrolled):
        assert not browser_session_registry.session_trusts_url("t::enrolled", UNTRUSTED_PRIVATE)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `scripts/run_tests.sh tests/tools/test_browser_enrolled_routing.py::TestGuardTrustScoping -q`
Expected: FAIL on `test_bare_key_is_not_trusted`.

- [ ] **Step 3: Scope the fallback**

In `tools/browser_session_registry.py`, replace the first lines of `session_trusts_url`:

```python
    name = profile_for(session_key)
    if not name:
        # The default profile applies only to a key routing created for a
        # trusted origin. A bare (ephemeral) key inherits nothing.
        if not str(session_key).endswith("::enrolled"):
            return False
        name = default_profile_name()
    if not name:
        return False
```

Update the docstring's "Resolution order" paragraph to match.

- [ ] **Step 4: Update the superseded default-profile tests**

In `tests/tools/test_browser_default_profile.py`, change `session_trusts_url("default", ...)` and `_snapshot_blocked_url("default", ...)` call sites that expect trust to use `"default::enrolled"`, and retitle `test_unbound_session_uses_default_profile` to `test_enrolled_key_uses_default_profile`. Add:

```python
    def test_bare_key_no_longer_inherits_trust(self, _default_enrolled):
        """Superseded by per-navigation routing: trusted origins get their own key."""
        assert not browser_session_registry.session_trusts_url("default", INTERNAL)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
scripts/run_tests.sh tests/tools/test_browser_enrolled_routing.py tests/tools/test_browser_default_profile.py tests/tools/test_browser_profile_trust_seam.py -q
scripts/run_tests.sh tests/tools/ -k browser -q
```
Expected: 0 failed; the upstream SSRF suites still unedited.

- [ ] **Step 6: Commit**

```bash
git add tools/browser_session_registry.py tests/tools/test_browser_enrolled_routing.py tests/tools/test_browser_default_profile.py
git commit -m "fix(browser): scope profile trust to keys routing actually created"
```

---

### Task 5: Remove the process-global CDP endpoint (EBL-001)

**Files:**
- Modify: `tools/browser_session_manager.py` (`acquire`, ~line 253)
- Modify: `tools/browser_tool.py` (`_session_cdp_url`)
- Test: `tests/tools/test_browser_enrolled_routing.py` (append)

**Interfaces:**
- Consumes: `_session_cdp_url` (existing).
- Produces: `acquire(profile, headless=None, session_key=None, attach_global=True)`. When `attach_global` is False, `os.environ["BROWSER_CDP_URL"]` is not written.

- [ ] **Step 1: Write the failing test**

Append to `tests/tools/test_browser_enrolled_routing.py`:

```python
import os


class TestNoGlobalEndpointLeak:
    """EBL-001: an ephemeral task must never inherit the corporate endpoint."""

    def test_agent_acquire_does_not_write_the_global(self, monkeypatch, _default_enrolled):
        monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
        monkeypatch.setattr(browser_tool, "_resolve_cdp_override", lambda u: u)

        def _fake_acquire(profile, headless=None, session_key=None, attach_global=True):
            assert attach_global is False, "agent path must not mutate os.environ"
            return browser_session_manager.BrowserSession(
                session_key=session_key, profile=ENROLLED, cdp_url="http://127.0.0.1:9222"
            )

        monkeypatch.setattr(browser_session_manager, "acquire", _fake_acquire)
        assert browser_tool._session_cdp_url("t::enrolled") == "http://127.0.0.1:9222"
        assert os.environ.get("BROWSER_CDP_URL") is None

    def test_ephemeral_task_never_inherits_the_corporate_endpoint(
        self, monkeypatch, _default_enrolled
    ):
        """The exact EBL-001 reproduction. This test must fail if the global returns.

        An enrolled task acquires; an explicitly ephemeral task must then still
        resolve to its own override, not to the corporate endpoint -- before AND
        after the enrolled task is cleaned up.
        """
        monkeypatch.setattr(browser_tool, "_resolve_cdp_override", lambda u: u)
        monkeypatch.setattr(browser_tool, "_get_cdp_override", lambda: "")
        monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: False)
        monkeypatch.setattr(browser_tool, "_stop_cdp_supervisor", lambda t: None)
        browser_tool._reset_session_cdp_cache()

        def _acq(profile, headless=None, session_key=None, attach_global=True):
            return browser_session_manager.BrowserSession(
                session_key=session_key, profile=ENROLLED,
                cdp_url="http://127.0.0.1:9222/corporate",
            )

        monkeypatch.setattr(browser_session_manager, "acquire", _acq)
        browser_session_registry.bind("external", "default")

        assert browser_tool._session_cdp_url("corp-task::enrolled") == \
            "http://127.0.0.1:9222/corporate"
        assert browser_tool._session_cdp_url("external") == ""

        browser_tool._cleanup_single_browser_session("corp-task::enrolled")
        assert browser_tool._session_cdp_url("external") == ""

    def test_scripted_callers_keep_the_global(self, monkeypatch):
        """The confluence CLI and workflow script nodes rely on _attach_cdp."""
        monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
        monkeypatch.setattr(browser_profiles, "get_profile", lambda n: ENROLLED)
        monkeypatch.setattr(
            browser_session_manager, "_ensure_enrolled_cdp",
            lambda p, h: "http://127.0.0.1:9333",
        )
        monkeypatch.setattr(browser_session_manager, "_run_daemon_hygiene", lambda: None)
        browser_session_manager.acquire("corp", session_key="scripted")
        assert os.environ["BROWSER_CDP_URL"] == "http://127.0.0.1:9333"
```

Add `from tools import browser_session_manager` to the file's imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `scripts/run_tests.sh tests/tools/test_browser_enrolled_routing.py::TestNoGlobalEndpointLeak -q`
Expected: FAIL — `acquire()` has no `attach_global` parameter.

- [ ] **Step 3: Add the opt-out to acquire**

In `tools/browser_session_manager.py`, change the signature and the attach call:

```python
def acquire(profile: str = browser_profiles.DEFAULT_PROFILE_NAME,
            headless: Optional[bool] = None,
            session_key: Optional[str] = None,
            attach_global: bool = True) -> BrowserSession:
```

Append to the docstring:

```
    ``attach_global`` controls whether the resolved endpoint is exported to the
    process-global ``BROWSER_CDP_URL``. Scripted callers default to True and rely
    on it. The AGENT passes False: a process-global endpoint cannot model
    concurrent per-task state, and an explicitly ephemeral task would otherwise
    read it back through ``_get_cdp_override()`` and drive the corporate browser
    (review finding EBL-001).
```

And in the body:

```python
    if prof.is_enrolled:
        cdp_url = _ensure_enrolled_cdp(prof, effective_headless)
        if attach_global:
            _attach_cdp(cdp_url)
```

- [ ] **Step 4: Pass False from the agent path**

In `tools/browser_tool.py` `_session_cdp_url`, change the acquire call:

```python
    session = acquire(profile.name, session_key=key, attach_global=False)
```

Replace the docstring paragraph beginning "``acquire()`` also exports" with:

```
    The endpoint is returned to the caller and stored in the session's own
    record; it is NOT exported to ``os.environ``. A process-global endpoint
    cannot model concurrent per-task state — see review finding EBL-001 and
    ``docs/plans/2026-07-26-per-navigation-browser-profile-design.md``.
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
scripts/run_tests.sh tests/tools/test_browser_enrolled_routing.py tests/tools/test_browser_session_manager.py tests/tools/test_browser_session_signin.py -q
scripts/run_tests.sh tests/tools/ -k browser -q
```
Expected: 0 failed.

- [ ] **Step 6: Commit**

```bash
git add tools/browser_session_manager.py tools/browser_tool.py tests/tools/test_browser_enrolled_routing.py
git commit -m "fix(browser): keep the enrolled CDP endpoint per session, not process-global"
```

---

### Task 6: Single-flight acquire, and release the session handle (EBL-003, EBL-005)

**Files:**
- Modify: `tools/browser_tool.py` (`_session_cdp_url`, `_forget_session_cdp_url`, `_cleanup_single_browser_session:4623`)
- Test: `tests/tools/test_browser_enrolled_routing.py` (append)

**Interfaces:**
- Consumes: `_session_cdp_url` (Task 5).
- Produces: `_session_handles: Dict[str, BrowserSession]`; `_release_session_handle(session_key: str) -> None`.

- [ ] **Step 1: Write the failing test**

```python
import threading


class TestAcquireLifecycle:
    def test_concurrent_misses_acquire_once(self, monkeypatch, _default_enrolled):
        """EBL-003: acquire() runs `close --all`; a second one tears down the first."""
        monkeypatch.setattr(browser_tool, "_resolve_cdp_override", lambda u: u)
        browser_tool._reset_session_cdp_cache()
        barrier, calls, lock = threading.Barrier(2, timeout=5), [], threading.Lock()

        def _slow_acquire(profile, headless=None, session_key=None, attach_global=True):
            with lock:
                calls.append(session_key)
                n = len(calls)
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass
            return browser_session_manager.BrowserSession(
                session_key=session_key, profile=ENROLLED,
                cdp_url=f"http://127.0.0.1:922{n}",
            )

        monkeypatch.setattr(browser_session_manager, "acquire", _slow_acquire)
        results = []
        threads = [
            threading.Thread(target=lambda: results.append(
                browser_tool._session_cdp_url("t::enrolled")))
            for _ in range(2)
        ]
        for t in threads:
            t.start()
        barrier.abort()
        for t in threads:
            t.join(timeout=5)

        assert len(calls) == 1, f"acquire ran {len(calls)} times"
        assert len(set(results)) == 1, f"threads got different endpoints: {results}"

    def test_cleanup_releases_the_binding(self, monkeypatch, _default_enrolled):
        """EBL-005: acquire() binds the registry; nothing released it."""
        monkeypatch.setattr(browser_tool, "_resolve_cdp_override", lambda u: u)
        monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: False)
        monkeypatch.setattr(browser_tool, "_stop_cdp_supervisor", lambda t: None)

        def _binding_acquire(profile, headless=None, session_key=None, attach_global=True):
            browser_session_registry.bind(session_key, profile)
            return browser_session_manager.BrowserSession(
                session_key=session_key, profile=ENROLLED, cdp_url="http://127.0.0.1:9222"
            )

        monkeypatch.setattr(browser_session_manager, "acquire", _binding_acquire)
        browser_tool._session_cdp_url("t::enrolled")
        assert browser_session_registry.profile_for("t::enrolled") == "corp"

        browser_tool._cleanup_single_browser_session("t::enrolled")
        assert browser_session_registry.profile_for("t::enrolled") is None

        monkeypatch.setattr(browser_session_registry, "default_profile_name", lambda: None)
        assert not browser_session_registry.session_trusts_url("t::enrolled", TRUSTED)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `scripts/run_tests.sh tests/tools/test_browser_enrolled_routing.py::TestAcquireLifecycle -q`
Expected: FAIL — two acquires and two endpoints; binding survives cleanup.

- [ ] **Step 3: Add per-key locking and handle retention**

Beside `_session_cdp_urls` in `tools/browser_tool.py`:

```python
# Per-key locks so exactly one thread acquires a given session's browser.
# The dict lock alone is not enough: acquire() runs `close --all` hygiene, so a
# second concurrent acquire tears down the first session mid-navigation (EBL-003).
_session_cdp_keylocks: Dict[str, threading.Lock] = {}
# Live BrowserSession handles, so cleanup can release() exactly once (EBL-005).
_session_handles: Dict[str, Any] = {}


def _session_cdp_keylock(session_key: str) -> threading.Lock:
    with _session_cdp_lock:
        return _session_cdp_keylocks.setdefault(session_key, threading.Lock())


def _release_session_handle(session_key: str) -> None:
    """Release this session's browser handle, unbinding its profile. Idempotent."""
    with _session_cdp_lock:
        handle = _session_handles.pop(str(session_key), None)
    if handle is None:
        return
    try:
        handle.release()
    except Exception as exc:  # noqa: BLE001
        logger.debug("releasing browser session %s failed: %s", session_key, exc)
```

Extend `_reset_session_cdp_cache` to clear both new dicts.

- [ ] **Step 4: Serialize the acquire and retain the handle**

Replace the tail of `_session_cdp_url` (from `from tools.browser_session_manager import` onward):

```python
    from tools.browser_session_manager import ProfileError, acquire

    with _session_cdp_keylock(key):
        # Re-check under the key lock: another thread may have published while
        # we waited. Without this the loser acquires a second browser and its
        # `close --all` hygiene tears down the winner's session.
        with _session_cdp_lock:
            cached = _session_cdp_urls.get(key)
        if cached:
            return cached

        session = acquire(profile.name, session_key=key, attach_global=False)
        cdp_url = _resolve_cdp_override(str(session.cdp_url or ""))
        if not cdp_url:
            raise ProfileError(
                f"browser profile {profile.name!r} exposed no CDP endpoint"
            )
        with _session_cdp_lock:
            _session_cdp_urls[key] = cdp_url
            _session_handles[key] = session
        return cdp_url
```

- [ ] **Step 5: Release on cleanup**

In `_cleanup_single_browser_session`, replace the trailing `_forget_session_cdp_url(task_id)` block:

```python
    # OTTO: drop this session's memoized endpoint AND release its browser handle.
    # release() unbinds the registry; without it the key keeps internal-origin
    # trust after cleanup and even after browser.default_profile is turned off
    # (review finding EBL-005). Outside the branch above: an acquire that
    # succeeded before session registration failed leaves state with no
    # _active_sessions entry.
    _release_session_handle(task_id)
    _forget_session_cdp_url(task_id)
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
scripts/run_tests.sh tests/tools/test_browser_enrolled_routing.py -q
scripts/run_tests.sh tests/tools/ -k browser -q
```
Expected: 0 failed.

- [ ] **Step 7: Commit**

```bash
git add tools/browser_tool.py tests/tools/test_browser_enrolled_routing.py
git commit -m "fix(browser): single-flight enrolled acquire and release on cleanup"
```

---

### Task 7: Availability gate validates the whole chain (EBL-006)

**Files:**
- Modify: `tools/browser_profiles.py` (`resolve_executable`)
- Modify: `tools/browser_session_registry.py` (`default_profile_launchable`)
- Modify: `tools/browser_tool.py` (`check_browser_requirements:4923`)
- Test: `tests/tools/test_browser_enrolled_launch.py` (append)

**Interfaces:**
- Produces: `resolve_executable` returns None for a non-regular or non-executable file; `default_profile_launchable()` additionally requires a usable `user_data_dir` and a valid `cdp_port`.

- [ ] **Step 1: Write the failing test**

Append to `TestAvailabilityGate` in `tests/tools/test_browser_enrolled_launch.py`:

```python
    def test_unavailable_without_the_agent_browser_cli(self, monkeypatch, tmp_path):
        """The enrolled path still drives the browser THROUGH agent-browser."""
        exe = tmp_path / "chrome"
        exe.write_text("")
        exe.chmod(0o755)
        monkeypatch.setattr(browser_profiles, "get_profile", lambda n: ENROLLED)
        monkeypatch.setattr(browser_profiles, "resolve_executable", lambda p: str(exe))
        monkeypatch.setattr(
            browser_session_registry, "default_profile_name", lambda: "enrolled"
        )

        def _missing(**kw):
            raise FileNotFoundError("agent-browser CLI not found")

        monkeypatch.setattr(browser_tool, "_find_agent_browser", _missing)
        assert browser_tool.check_browser_requirements() is False

    def test_non_executable_file_does_not_resolve(self, tmp_path):
        exe = tmp_path / "chrome"
        exe.write_text("")
        exe.chmod(0o644)
        profile = browser_profiles.BrowserProfile(
            name="enrolled", kind=browser_profiles.KIND_ENROLLED, executable=str(exe)
        )
        assert browser_profiles.resolve_executable(profile) is None

    def test_directory_does_not_resolve(self, tmp_path):
        profile = browser_profiles.BrowserProfile(
            name="enrolled", kind=browser_profiles.KIND_ENROLLED, executable=str(tmp_path)
        )
        assert browser_profiles.resolve_executable(profile) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `scripts/run_tests.sh tests/tools/test_browser_enrolled_launch.py::TestAvailabilityGate -q`
Expected: FAIL on all three.

- [ ] **Step 3: Harden executable resolution**

In `tools/browser_profiles.py`, add above `resolve_executable`:

```python
def _is_runnable(path: str) -> bool:
    """True when ``path`` is a regular file we could actually execute.

    ``os.path.exists`` alone accepts a directory or a mode-0644 file, which then
    passes the availability gate and fails at first launch with PermissionError
    (review finding EBL-006). Windows has no execute bit, so the X_OK check is
    POSIX-only.
    """
    if not os.path.isfile(path):
        return False
    if sys.platform == "win32":
        return True
    return os.access(path, os.X_OK)
```

Replace both `os.path.exists(...)` uses in `resolve_executable` with `_is_runnable(...)`.

- [ ] **Step 4: Validate the rest of the chain**

In `tools/browser_session_registry.py` `default_profile_launchable`, after the enrolled check:

```python
        if not resolve_executable(profile):
            return False
        # A launch that cannot create its user-data-dir, or whose port is out of
        # range, fails on every acquire -- do not advertise the tools for it.
        data_dir = os.path.expandvars(profile.user_data_dir or "")
        if not data_dir:
            return False
        if not (1 <= int(profile.cdp_port) <= 65535):
            return False
        return True
```

Add `import os` to the module imports.

- [ ] **Step 5: Require the CLI in the gate**

In `tools/browser_tool.py` `check_browser_requirements`, move the enrolled early return to **after** the `_find_agent_browser` / Termux block, and change it to:

```python
    # OTTO: an enrolled default profile drives the user's REAL installed browser
    # over CDP, so it needs no bundled Chromium -- but it still drives that
    # browser THROUGH agent-browser (`--cdp <url>`), so the CLI check above
    # still applies and this return sits after it (review finding EBL-006).
    if _default_profile_launchable():
        return True
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
scripts/run_tests.sh tests/tools/test_browser_enrolled_launch.py tests/tools/test_browser_profiles.py -q
scripts/run_tests.sh tests/tools/ -k browser -q
```
Expected: 0 failed.

- [ ] **Step 7: Commit**

```bash
git add tools/browser_profiles.py tools/browser_session_registry.py tools/browser_tool.py tests/tools/test_browser_enrolled_launch.py
git commit -m "fix(browser): availability gate validates the whole enrolled chain"
```

---

### Task 8: Trusted redirect parity (EBL-008)

**Files:**
- Modify: `tools/browser_tool.py` (post-redirect block, line 3087)
- Test: `tests/tools/test_browser_enrolled_routing.py` (append)

- [ ] **Step 1: Write the failing test**

```python
class TestRedirectParity:
    @pytest.fixture(autouse=True)
    def _nav(self, monkeypatch, _default_enrolled):
        monkeypatch.setattr(browser_tool, "_is_local_backend", lambda: False)
        monkeypatch.setattr(browser_tool, "_allow_private_urls", lambda: False)
        monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: False)
        monkeypatch.setattr(browser_tool, "_is_safe_url", lambda u: "corp.example" not in u)
        monkeypatch.setattr(browser_tool, "_is_always_blocked_url", lambda u: "169.254" in u)
        monkeypatch.setattr(browser_tool, "check_website_access", lambda u: None)
        monkeypatch.setattr(
            browser_tool, "_get_session_info",
            lambda k: {"session_name": "s", "bb_session_id": None, "cdp_url": None,
                       "features": {}, "_first_nav": False},
        )

    def _navigate_landing_on(self, monkeypatch, final_url):
        monkeypatch.setattr(
            browser_tool, "_run_browser_command",
            lambda *a, **kw: {"success": True, "data": {"title": "T", "url": final_url}},
        )
        return browser_tool.browser_navigate(TRUSTED, task_id="t")

    def test_redirect_to_a_trusted_origin_is_permitted(self, monkeypatch):
        out = self._navigate_landing_on(monkeypatch, "https://wiki.corp.example/home")
        assert "private or internal address" not in out

    def test_redirect_to_an_unlisted_private_origin_is_blocked(self, monkeypatch):
        out = self._navigate_landing_on(monkeypatch, "https://other.corp.example/x")
        assert "private or internal address" in out

    def test_redirect_to_cloud_metadata_is_blocked(self, monkeypatch):
        out = self._navigate_landing_on(monkeypatch, "http://169.254.169.254/latest")
        assert "cloud metadata endpoint" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `scripts/run_tests.sh tests/tools/test_browser_enrolled_routing.py::TestRedirectParity -q`
Expected: FAIL on `test_redirect_to_a_trusted_origin_is_permitted` — blocked despite trust.

- [ ] **Step 3: Add the trust check**

At line 3087, add the trust term as the final condition (metadata floor already precedes this block):

```python
        if (
            (not _is_local_backend() or _is_enrolled_session_key(nav_session_key))
            and not auto_local_this_nav
            and not _allow_private_urls()
            and final_url and final_url != url and not _is_safe_url(final_url)
            # OTTO: mirror the pre-navigation guard -- an origin this session's
            # profile explicitly trusts is a legitimate redirect target (SSO and
            # CF Access land here). Denies for ephemeral keys, which trust
            # nothing. The metadata floor above is checked first and never
            # trusted (review finding EBL-008).
            and not _session_trusts_url(nav_session_key, final_url)
        ):
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
scripts/run_tests.sh tests/tools/test_browser_enrolled_routing.py -q
scripts/run_tests.sh tests/tools/ -k browser -q
```
Expected: 0 failed.

- [ ] **Step 5: Commit**

```bash
git add tools/browser_tool.py tests/tools/test_browser_enrolled_routing.py
git commit -m "fix(browser): permit a trusted redirect target in an enrolled session"
```

---

### Task 9: Reject port collisions and verify endpoint identity (EBL-004)

**Files:**
- Modify: `tools/browser_profiles.py` (`load_profiles`)
- Modify: `tools/browser_session_manager.py` (`_cdp_alive` → identity-aware)
- Test: `tests/tools/test_browser_profiles.py`, `tests/tools/test_browser_session_manager.py`

**Interfaces:**
- Produces: `_cdp_browser_identity(cdp_url: str) -> Optional[str]` returning the `/json/version` `Browser` string; `load_profiles` drops a later enrolled profile that reuses an earlier one's `cdp_port`.

- [ ] **Step 1: Write the failing test**

In `tests/tools/test_browser_profiles.py`:

```python
class TestPortCollision:
    def test_duplicate_enrolled_ports_are_rejected(self, monkeypatch, caplog):
        """Two profiles on one port bind one profile's trust to another's browser."""
        monkeypatch.setattr(
            browser_profiles, "_read_config",
            lambda: {"browser": {"profiles": {
                "a": {"kind": "enrolled", "cdp_port": 9222},
                "b": {"kind": "enrolled", "cdp_port": 9222},
            }}},
        )
        with caplog.at_level("WARNING"):
            profiles = browser_profiles.load_profiles()
        assert "a" in profiles
        assert "b" not in profiles
        assert any("cdp_port" in r.message for r in caplog.records)

    def test_distinct_ports_both_load(self, monkeypatch):
        monkeypatch.setattr(
            browser_profiles, "_read_config",
            lambda: {"browser": {"profiles": {
                "a": {"kind": "enrolled", "cdp_port": 9222},
                "b": {"kind": "enrolled", "cdp_port": 9333},
            }}},
        )
        profiles = browser_profiles.load_profiles()
        assert {"a", "b"} <= set(profiles)
```

In `tests/tools/test_browser_session_manager.py`:

```python
class TestEndpointIdentity:
    def test_foreign_listener_is_not_reused(self, monkeypatch):
        """Any HTTP responder on the port must not be trusted as our browser."""
        monkeypatch.setattr(
            browser_session_manager, "_cdp_browser_identity", lambda url: None
        )
        monkeypatch.setattr(browser_session_manager, "_run_daemon_hygiene", lambda: None)
        monkeypatch.setattr(
            browser_profiles, "resolve_executable", lambda p: "/nonexistent/chrome"
        )
        profile = browser_profiles.BrowserProfile(
            name="corp", kind=browser_profiles.KIND_ENROLLED, cdp_port=9222
        )
        with pytest.raises(browser_session_manager.ProfileError):
            browser_session_manager._ensure_enrolled_cdp(profile, headless=True)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
scripts/run_tests.sh tests/tools/test_browser_profiles.py::TestPortCollision -q
scripts/run_tests.sh tests/tools/test_browser_session_manager.py::TestEndpointIdentity -q
```
Expected: FAIL — duplicates load; `_cdp_browser_identity` does not exist.

- [ ] **Step 3: Reject duplicate ports**

In `tools/browser_profiles.py` `load_profiles`, track used ports inside the loop, after `parsed` is final:

```python
        if parsed.is_enrolled:
            if parsed.cdp_port in used_ports:
                logger.warning(
                    "browser profile %r reuses cdp_port %d (already used by %r); "
                    "ignoring it. Two profiles on one port make one profile's "
                    "trust apply to the other's browser. Give each a unique port.",
                    key, parsed.cdp_port, used_ports[parsed.cdp_port],
                )
                continue
            used_ports[parsed.cdp_port] = key
```

Initialise `used_ports: Dict[int, str] = {}` beside `profiles`.

- [ ] **Step 4: Verify endpoint identity before reuse**

In `tools/browser_session_manager.py`, replace `_cdp_alive` usage in `_ensure_enrolled_cdp` with an identity check:

```python
def _cdp_browser_identity(cdp_url: str) -> Optional[str]:
    """Return the ``Browser`` string from /json/version, or None.

    Reusing whatever answers on the port lets an unrelated listener -- or
    another profile's browser -- inherit this profile's trust (EBL-004).
    """
    try:
        with urllib.request.urlopen(
            f"{cdp_url}/json/version", timeout=CDP_PROBE_TIMEOUT_S
        ) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
        browser = str(payload.get("Browser") or "").strip()
        return browser or None
    except Exception:  # noqa: BLE001
        return None
```

Add `import json`. In `_ensure_enrolled_cdp`, replace the `if _cdp_alive(cdp_url):` block:

```python
    identity = _cdp_browser_identity(cdp_url)
    if identity:
        logger.info(
            "browser profile %r: reusing CDP listener on %s (%s)",
            profile.name, cdp_url, identity,
        )
        return cdp_url
```

Keep `_cdp_alive` for the post-launch readiness poll.

- [ ] **Step 5: Run tests to verify they pass**

```bash
scripts/run_tests.sh tests/tools/test_browser_profiles.py tests/tools/test_browser_session_manager.py -q
scripts/run_tests.sh tests/tools/ -k browser -q
```
Expected: 0 failed.

- [ ] **Step 6: Commit**

```bash
git add tools/browser_profiles.py tools/browser_session_manager.py tests/tools/test_browser_profiles.py tests/tools/test_browser_session_manager.py
git commit -m "fix(browser): reject enrolled port collisions and verify endpoint identity"
```

---

### Task 10: Recover from a dead enrolled endpoint (EBL-009)

**Files:**
- Modify: `tools/browser_tool.py` (`_run_browser_command`, after the command result)
- Test: `tests/tools/test_browser_enrolled_routing.py` (append)

**Interfaces:**
- Produces: `_evict_dead_enrolled_session(session_key: str) -> None`.

- [ ] **Step 1: Write the failing test**

```python
class TestDeadEndpointRecovery:
    def test_connection_failure_permits_one_reacquire(self, monkeypatch, _default_enrolled):
        monkeypatch.setattr(browser_tool, "_resolve_cdp_override", lambda u: u)
        browser_tool._reset_session_cdp_cache()
        calls = []

        def _acq(profile, headless=None, session_key=None, attach_global=True):
            calls.append(session_key)
            return browser_session_manager.BrowserSession(
                session_key=session_key, profile=ENROLLED,
                cdp_url=f"http://127.0.0.1:922{len(calls)}",
            )

        monkeypatch.setattr(browser_session_manager, "acquire", _acq)
        first = browser_tool._session_cdp_url("t::enrolled")
        browser_tool._evict_dead_enrolled_session("t::enrolled")
        second = browser_tool._session_cdp_url("t::enrolled")
        assert len(calls) == 2
        assert first != second

    def test_eviction_is_a_noop_for_a_bare_key(self, _default_enrolled):
        browser_tool._evict_dead_enrolled_session("t")  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `scripts/run_tests.sh tests/tools/test_browser_enrolled_routing.py::TestDeadEndpointRecovery -q`
Expected: FAIL — `_evict_dead_enrolled_session` does not exist.

- [ ] **Step 3: Add the eviction helper**

Beside `_release_session_handle` in `tools/browser_tool.py`:

```python
_DEAD_CDP_MARKERS = (
    "econnrefused", "connection refused", "websocket", "target closed",
    "browser has disconnected", "connect econn",
)


def _evict_dead_enrolled_session(session_key: str) -> None:
    """Drop an enrolled session whose browser is gone, so the next call relaunches.

    The memo and _active_sessions have no liveness notion, so a closed or
    crashed browser would keep being driven at a dead endpoint while activity
    refreshes hold off the idle reaper (review finding EBL-009).
    """
    if not _is_enrolled_session_key(session_key):
        return
    _release_session_handle(session_key)
    _forget_session_cdp_url(session_key)
    with _cleanup_lock:
        _active_sessions.pop(session_key, None)
```

- [ ] **Step 4: Call it on a connection-class failure**

In `_run_browser_command`, immediately before the successful return of the parsed result, add:

```python
    # OTTO: an enrolled session whose browser died must not keep being driven at
    # a dead endpoint. Evict so the NEXT call relaunches once; do not retry here,
    # because the command may not be idempotent.
    if not result.get("success") and _is_enrolled_session_key(task_id):
        error_text = str(result.get("error", "")).lower()
        if any(marker in error_text for marker in _DEAD_CDP_MARKERS):
            logger.warning(
                "enrolled browser for %s appears gone (%s); evicting so the next "
                "command relaunches", task_id, result.get("error"),
            )
            _evict_dead_enrolled_session(task_id)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
scripts/run_tests.sh tests/tools/test_browser_enrolled_routing.py -q
scripts/run_tests.sh tests/tools/ -k browser -q
```
Expected: 0 failed.

- [ ] **Step 6: Commit**

```bash
git add tools/browser_tool.py tests/tools/test_browser_enrolled_routing.py
git commit -m "fix(browser): recover from a dead enrolled browser endpoint"
```

---

### Task 11: Rewrite the ledger and the surface table (EBL-007)

**Files:**
- Modify: `docs/upstream-customizations/browser-profiles.yaml` (`enrolled-browser-launch-wiring`)
- Modify: `../CLAUDE.md` and `../AGENTS.md` (workspace root — must stay `cmp`-identical)

- [ ] **Step 1: Rewrite the ledger entry**

Replace the `enrolled-browser-launch-wiring` entry's `files`, `owned_symbols`, `tests`, and `merge_guidance`. `owned_symbols` must list every symbol carrying new behaviour:

```yaml
  owned_symbols:
  - _ENROLLED_SUFFIX
  - _is_enrolled_session_key
  - _session_cdp_url
  - _session_browser_profile
  - _session_uses_enrolled_browser
  - _default_profile_launchable
  - _session_cdp_lock
  - _session_cdp_urls
  - _session_cdp_keylocks
  - _session_handles
  - _session_cdp_keylock
  - _release_session_handle
  - _forget_session_cdp_url
  - _reset_session_cdp_cache
  - _evict_dead_enrolled_session
  - _navigation_session_key
  - _get_session_info
  - _run_browser_command
  - _cleanup_single_browser_session
  - check_browser_requirements
  - _eval_ssrf_guard_active
  - default_profile_trusts_url
  - default_profile_launchable
  - session_trusts_url
  - _enrolled_candidates
  - _is_runnable
  - resolve_executable
  - load_profiles
  - acquire
  - _cdp_browser_identity
  - _ensure_enrolled_cdp
```

Add `tools/browser_session_manager.py` and `tests/tools/test_browser_enrolled_routing.py` to `files` and `tests`. Delete the paragraph claiming the `BROWSER_CDP_URL` side effect keeps unswapped sites correct — it is the defect. Replace with the per-session contract, and state explicitly that the six guard disjuncts are the silent-revert risk: dropping one loses origin-scoping with no build error and no failing test other than `TestGuardForcedOnForEnrolled`.

- [ ] **Step 2: Validate the ledger**

Run: `./venv/bin/python scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/browser-profiles.yaml`
Expected: exit 0.

- [ ] **Step 3: Update the workspace surface table**

Replace the `enrolled-browser LAUNCH wiring` row added for `3a458156c` with one describing per-navigation routing: the `::enrolled` key, the guard-forcing disjuncts, the per-session endpoint, single-flight acquire and release, and that `browser.default_profile` now means "available for trusted origins" rather than "this session is enrolled". Apply the identical edit to `../AGENTS.md`.

- [ ] **Step 4: Verify the pair is identical**

Run: `cd .. && cmp CLAUDE.md AGENTS.md && echo identical`
Expected: `identical`

- [ ] **Step 5: Final full verification**

```bash
scripts/run_tests.sh tests/tools/ -k browser -q
scripts/run_tests.sh tests/tools/ -q
./venv/bin/python scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/browser-profiles.yaml
git diff --check 619ef6ea4..HEAD
```
Expected: browser selection 0 failed; full `tests/tools/` shows only the three known timing-sensitive failures present at the parent ref; ledger exit 0; no whitespace errors.

- [ ] **Step 6: Commit**

```bash
git add docs/upstream-customizations/browser-profiles.yaml
git commit -m "docs(upstream): record the per-navigation browser profile contract"
```

---

## Out of scope

- **Model-mediated exfiltration.** Per-navigation selection stops the corporate browser loading untrusted content; it does not stop a prompt-injected model reading an internal page and navigating the ephemeral browser to a public collector. Recorded as a known residual risk in the design.
- **Redirect re-routing.** A public URL that redirects into a trusted origin lands in the ephemeral browser and is correctly blocked there. The real flow starts at a trusted internal URL.
- **Hardware verification.** No managed corporate Windows machine is available. The design's verification protocol must run before release.
