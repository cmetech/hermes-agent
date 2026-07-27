"""Session/eval backends: Playwright (Python) and agent-browser.

Both drive the SAME enrolled corporate browser (Edge) over CDP. The auth is
the browser's job; a backend only injects the same-origin fetch and returns
the result. Only this ~2-class layer differs between engines -- the injected
JS, REST logic, Markdown conversion and artifacts are all engine-agnostic.

Engine trade-offs (see docs/plans/2026-07-19-confluence-research-skill-plan.md):
  * PlaywrightBackend  -- one long-lived process, holds a `page`, evaluates
    in-process. Proven in loop24; no daemon to wedge. Default.
  * AgentBrowserBackend -- shells to the `agent-browser` CLI (already a repo
    dependency), cross-platform prebuilt binaries. Verified against corporate
    Confluence in attach mode. Its client-daemon can wedge on Windows, so it
    resets stale sessions on start and never uses `--stdin` (PowerShell hangs).

Contract: `eval(fn_js)` where `fn_js` is a string `async () => { ... }`.
Returns the resolved value (already JSON-parsed). Raises on engine error.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

# 9333, not 9222: 9222 is `/browser connect`'s default port and the
# conventional ad-hoc remote-debugging port. An enrolled corporate browser
# parked there is discoverable by anything that probes the default, which
# would hand untrusted pages this browser's live SSO session.
CDP_PORT = int(os.environ.get("CONFLUENCE_CDP_PORT", "9333"))
CDP_URL = f"http://localhost:{CDP_PORT}"

# Chromium-family browsers, in preference order per platform. Edge first: on a
# corporate build it is the one enrolled for SSO / Conditional Access and holds
# the mTLS client cert in the OS store, so it authenticates where a downloaded
# Chrome for Testing would fail.
_BROWSER_CANDIDATES: dict[str, tuple[str, ...]] = {
    "win32": (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ),
    "darwin": (
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ),
    "linux": (
        "microsoft-edge-stable", "microsoft-edge",
        "google-chrome-stable", "google-chrome", "chromium",
    ),
}


def log(*args) -> None:
    """Diagnostics -> stderr. stdout stays a clean JSON payload."""
    print(*args, file=sys.stderr, flush=True)


def default_profile_dir() -> Path:
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(home) / "browser-profiles" / "confluence"


def resolve_browser() -> str | None:
    """Locate a Chromium-family browser, honouring CONFLUENCE_BROWSER."""
    override = os.environ.get("CONFLUENCE_BROWSER", "").strip().strip('"')
    if override:
        return override if (os.path.isfile(override) or shutil.which(override)) else None
    for cand in _BROWSER_CANDIDATES.get(sys.platform, ()):
        if os.path.isfile(cand):
            return cand
        found = shutil.which(cand)
        if found:
            return found
    return None


def _cdp_alive() -> bool:
    try:
        urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=2)
        return True
    except Exception:
        return False


def ensure_edge_cdp(profile_dir: Path, headless: bool) -> None:
    """Start enrolled Edge with a debug port if nothing is listening yet.

    Reuses an already-running debug browser (covers the case where the user
    opened Edge themselves) exactly like the original worker's _edge_alive().
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    if _cdp_alive():
        log("CDP already listening - reusing that browser.")
        return
    exe = resolve_browser()
    if not exe:
        raise RuntimeError(
            "No Chromium-family browser found. Install Microsoft Edge or Chrome, "
            "or set CONFLUENCE_BROWSER to the executable path."
        )
    log(f"Launching {Path(exe).name} ({'headless' if headless else 'visible'}) ...")
    cmd = [exe, f"--remote-debugging-port={CDP_PORT}",
           f"--user-data-dir={profile_dir}", "--no-first-run",
           "--no-default-browser-check"]
    if headless:
        cmd.append("--headless=new")
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
    for _ in range(30):
        time.sleep(0.5)
        if _cdp_alive():
            log("Browser ready.")
            return
    raise RuntimeError(f"Browser did not expose CDP on {CDP_URL} in time.")


# ── backend interface ────────────────────────────────────────────────────────

class Backend:
    name = "base"

    def start(self, headless: bool) -> None: ...
    def navigate(self, url: str) -> dict: ...
    def eval(self, fn_js: str): ...
    def shutdown(self) -> None: ...

    @staticmethod
    def available() -> bool:
        return False


# ── Playwright backend ───────────────────────────────────────────────────────

class PlaywrightBackend(Backend):
    name = "playwright"

    def __init__(self, profile_dir: Path | None = None) -> None:
        self._profile = profile_dir or default_profile_dir()
        self._pw = None
        self._browser = None
        self._page = None

    @staticmethod
    def available() -> bool:
        try:
            import playwright  # noqa: F401
            return True
        except Exception:
            return False

    def start(self, headless: bool) -> None:
        ensure_edge_cdp(self._profile, headless=headless)
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(CDP_URL)
        if not self._browser.contexts:
            raise RuntimeError("Browser exposed no context over CDP.")

    def _ensure_page(self, url: str):
        ctx = self._browser.contexts[0]
        host = urlparse(url).netloc
        page = next((pg for pg in ctx.pages if host and host in pg.url), None)
        if page is None:
            page = ctx.new_page()
            page.goto(url, wait_until="load", timeout=60_000)
            log(f"Opened {url}")
        else:
            log(f"Reusing tab: {page.url}")
        self._page = page
        return page

    def navigate(self, url: str) -> dict:
        page = self._page or self._ensure_page(url)
        page.goto(url, wait_until="load", timeout=60_000)
        return {"title": page.title(), "url": page.url}

    def eval(self, fn_js: str):
        # Playwright auto-invokes a function-shaped string and awaits the result.
        return self._page.evaluate(fn_js)

    def shutdown(self) -> None:
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        time.sleep(1)


# ── agent-browser backend ────────────────────────────────────────────────────

class AgentBrowserBackend(Backend):
    name = "agent-browser"

    def __init__(self, profile_dir: Path | None = None) -> None:
        self._profile = profile_dir or default_profile_dir()
        self._cli = self._resolve_cli()

    @staticmethod
    def _resolve_cli() -> list[str] | None:
        """agent-browser on PATH, else the copy vendored in node_modules."""
        found = shutil.which("agent-browser")
        if found:
            return [found]
        # repo root is 5 levels up from scripts/ (skills/ericsson/<skill>/scripts)
        here = Path(__file__).resolve()
        for root in list(here.parents)[:8]:
            js = root / "node_modules" / "agent-browser" / "bin" / "agent-browser.js"
            if js.is_file():
                node = shutil.which("node")
                if node:
                    return [node, str(js)]
        return None

    @staticmethod
    def available() -> bool:
        return AgentBrowserBackend._resolve_cli() is not None

    def _run(self, args: list[str], timeout: int = 60) -> dict:
        proc = subprocess.run(
            self._cli + args, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        out = (proc.stdout or "").strip()
        if not out:
            raise RuntimeError(
                f"agent-browser returned nothing (rc={proc.returncode}): "
                f"{(proc.stderr or '').strip()[-300:]}"
            )
        return json.loads(out)

    def start(self, headless: bool) -> None:
        if not self._cli:
            raise RuntimeError("agent-browser CLI not found.")
        ensure_edge_cdp(self._profile, headless=headless)
        # Reset any wedged daemon session from a prior run before attaching.
        try:
            self._run(["close", "--all", "--json"], timeout=20)
        except Exception:
            pass

    def navigate(self, url: str) -> dict:
        d = self._run([f"--cdp", str(CDP_PORT), "open", url, "--json"], timeout=90)
        data = d.get("data") or {}
        return {"title": data.get("title", ""), "url": data.get("url", url)}

    def eval(self, fn_js: str):
        # agent-browser evaluates an expression: self-invoke the arrow fn.
        # Base64 (never --stdin: PowerShell stdin piping hangs).
        iife = f"({fn_js})()"
        b64 = base64.b64encode(iife.encode("utf-8")).decode("ascii")
        d = self._run(["--cdp", str(CDP_PORT), "eval", "-b", b64, "--json"], timeout=90)
        data = d.get("data")
        # agent-browser wraps eval output as {origin, result}.
        if isinstance(data, dict) and "result" in data:
            return data["result"]
        return data

    def shutdown(self) -> None:
        try:
            self._run(["close", "--all", "--json"], timeout=20)
        except Exception:
            pass


# ── selection ────────────────────────────────────────────────────────────────

_ENGINES = {
    "playwright": PlaywrightBackend,
    "agent-browser": AgentBrowserBackend,
}
# Preference order for `auto`: Playwright first (proven, no daemon), then
# agent-browser. Flip here once agent-browser is validated against corp Edge.
_AUTO_ORDER = ("playwright", "agent-browser")


def select_backend(engine: str, profile_dir: Path | None = None) -> Backend:
    """Return a backend for `engine` in {auto, playwright, agent-browser}."""
    if engine and engine != "auto":
        cls = _ENGINES.get(engine)
        if not cls:
            raise RuntimeError(f"Unknown engine '{engine}'. "
                               f"Choose from: auto, {', '.join(_ENGINES)}.")
        if not cls.available():
            raise RuntimeError(f"Engine '{engine}' is not available in this environment.")
        log(f"Engine: {engine}")
        return cls(profile_dir)
    for name in _AUTO_ORDER:
        cls = _ENGINES[name]
        if cls.available():
            log(f"Engine: {name} (auto-selected)")
            return cls(profile_dir)
    raise RuntimeError(
        "No usable engine. Install Playwright (`pip install playwright`) or "
        "ensure the agent-browser CLI is available."
    )
