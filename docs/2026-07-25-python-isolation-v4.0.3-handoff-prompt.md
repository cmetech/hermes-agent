# Handoff — deliver our own Python, ignore the user's; ship as v4.0.3

> Copy everything below the horizontal rule into a fresh session whose working
> directory is `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`.
> The diagnosis is complete and evidence-backed. This is an implementation +
> release handoff. Re-verify anything load-bearing, but you should not need to
> re-investigate.

---

## 1. The failure

First install of **LOOP24 v4.0.2** on a corporate Windows laptop
(`C:\Users\ecorell`) failed at 7-of-8 stages. `uv`, Python, Git, Node, ripgrep/
ffmpeg, clone and venv all succeeded; the `dependencies` stage died with:

```
File "C:\Python\Python310\lib\site-packages\setuptools\__init__.py", line 5
File "C:\Python\Python310\lib\re.py", line 125
File "C:\Python\Python310\lib\sre_compile.py", line 17
    assert _sre.MAGIC == MAGIC, "SRE module mismatch"
AssertionError: SRE module mismatch
```

Then: `LOOP24 bootstrap failed at stage 'dependencies'`.

Logs (user's machine, copies were at `~/Downloads` on the dev Mac):
`%LOCALAPPDATA%\loop24\logs\desktop.log` and
`bootstrap-2026-07-25T17-42-37-559Z.log`.

## 2. Root cause — verified

The machine has **two+ Python installations** and the build mixed them:

- venv correctly created from **`C:\Python311\python.exe`** (3.11.0) — bootstrap log line 66
- build backend loaded stdlib from **`C:\Python\Python310\lib\`** — lines 97–103

`SRE module mismatch` is the canonical signature of an interpreter loading a
*different version's* stdlib: the compiled `_sre` extension's `MAGIC` disagrees
with the pure-Python `sre_compile.py`. The mechanism is almost certainly
`PYTHONHOME` (and/or `PYTHONPATH`) set at **Machine scope** on the corporate
baseline, pointing at `C:\Python\Python310`. `PYTHONHOME` overrides an
interpreter's own stdlib location, so *every* Python subprocess is dragged onto
the 3.10 stdlib regardless of which interpreter launched it.

Corroborating: setuptools was read from a **system** `site-packages`, not uv's
isolated build env — consistent with `PYTHONHOME`/`PYTHONPATH` defeating build
isolation. The `C:\Python311` vs `C:\Python\Python310` naming difference suggests
one user install and one IT-managed baseline.

**Why a shell-level workaround did not help:** the user cleared
`$env:PYTHONHOME`/`$env:PYTHONPATH` in PowerShell and re-ran, and hit the same
error. The bootstrap is spawned by the **Electron app**, not their shell — the
app inherits Machine/User-scope environment, so session-scoped edits never reach
it. Any fix must live in the installer and the app's spawn env, not in guidance.

Not the cause (all behaved correctly): 177 Windows CA certs trusted; SSH clone
failed → HTTPS fallback worked; Node v18.12.1 correctly replaced with portable
Node 22.

## 3. Requirement (from Corey, explicit)

> Ensure that if a user has Python on their PATH, we use the Python **we
> deliver** for install and runtime. We shouldn't pull theirs for any phase —
> install, runtime, etc.

So this is **not** just "scrub two env vars". Two independent changes:

- **(A) Own the interpreter.** Always use a uv-**managed** Python, never a
  discovered system one, in every phase.
- **(B) Scrub the inherited Python environment**, because even a managed
  interpreter is poisoned by `PYTHONHOME`/`PYTHONPATH` from the parent process.

Both are required. (A) alone still breaks — a managed 3.11 with
`PYTHONHOME=C:\Python\Python310` fails identically.

## 4. Where the current code chooses the user's Python

**`scripts/install.ps1`** — `Test-Python` (~line 607):

```powershell
$pythonPath = & $UvCmd python find $PythonVersion 2>$null   # ← prefers SYSTEM python
if ($pythonPath) { Write-Success "Python found: $ver"; return $true }
# only installs a managed Python if find fails
```

**`scripts/install.sh`** — `check_python` (~line 635): same shape
(`"$UV_CMD" python find "$PYTHON_VERSION"` → succeeds on system Python).
Note the Termux branch above it (~615) deliberately uses `pkg` Python — **leave
Termux alone**, it has no managed-Python option.

That is precisely why `C:\Python311\python.exe` was chosen.

## 5. Existing precedent — mirror these, don't invent

The codebase already solves this class of problem in three places. The installers
just never got it.

1. **`scripts/install.ps1:1988-1998`** — neutralizes an inherited `UV_PYTHON`
   after creating the venv, with a comment explaining that otherwise uv would
   silently rebuild the venv at the wrong version. Same hazard, same shape.
2. **`hermes_cli/main.py:7048` and `:8002`** — already do
   `uv_env.pop("PYTHONHOME", None)` / `pop("PYTHONPATH", None)` before invoking
   uv (currently gated behind `_is_termux_env`). The knowledge exists; it is just
   scoped too narrowly.
3. **`hermes_cli/config.py:198-210`** — `_ENV_VAR_NAME_DENYLIST` already lists
   `PYTHONPATH`, `PYTHONHOME`, `PYTHONSTARTUP`, `PYTHONUSERBASE`,
   `PYTHONEXECUTABLE`, `PYTHONNOUSERSITE`. Reuse this list rather than writing a
   fourth one.

## 6. The runtime hole — do not miss this

**`apps/desktop/electron/main.ts:3458`** *appends* to the inherited value:

```ts
PYTHONPATH: [ACTIVE_HERMES_ROOT, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter)
```

So a corporate `PYTHONPATH` is **propagated into the backend at runtime**, and
`PYTHONHOME` is never cleared anywhere in `main.ts` (only `PYTHONUNBUFFERED` is
set, ~3142/3148). Even with the install fixed, the running backend stays exposed.

Fix: set `PYTHONPATH` to `ACTIVE_HERMES_ROOT` only (drop the inherited part),
explicitly `delete` `PYTHONHOME`, and set `PYTHONNOUSERSITE=1`. Check every
backend/CLI spawn site in `main.ts`, not just 3458.

## 7. Implementation plan

**(A) Managed Python in both installers**

- `install.ps1` / `install.sh`: replace "find, else install" with **install-then-use-managed**:
  - `uv python install <version>` (idempotent)
  - resolve the managed interpreter explicitly (`uv python find --managed-python <version>`, or set `UV_PYTHON_PREFERENCE=only-managed` for the whole run — verify the flag/env spelling against the pinned uv, `0.11.32` on the failing machine)
  - `uv venv --python <managed-path>`
- Keep the existing `UV_PYTHON=<venv python>` pin after venv creation (install.ps1:1996).
- Log which interpreter was chosen and that it is managed — the current
  `Python found: Python 3.11.0` message is what made this hard to spot.
- **Termux exception:** leave `install.sh`'s Termux branch on `pkg` Python.

**(B) Scrub inherited Python env**

- Both installers: clear `PYTHONHOME`, `PYTHONPATH`, `PYTHONSTARTUP`,
  `PYTHONEXECUTABLE`, `PYTHONUSERBASE` and set `PYTHONNOUSERSITE=1` for the whole
  install (not per-command — the failure was in a uv **child** process).
- `main.ts`: fix the spawn env per §6.
- Consider widening `hermes_cli/main.py`'s existing `pop` calls beyond the Termux
  gate, since the hazard is not Termux-specific.

**(C) Make the failure self-diagnosing**

The error gave no hint. Add a preflight that warns when `PYTHONHOME`/`PYTHONPATH`
are set, naming them and stating they will be ignored. Cheap, and turns a
20-minute investigation into a log line.

**(D) Tests**

- `tests/test_install_sh_*.py` is the existing pattern for installer assertions.
- Assert: the managed-Python path is used even when a system 3.11 is present; the
  scrub happens; `main.ts` no longer appends inherited `PYTHONPATH` (a
  `main.ts`-level unit test or a bundle grep in the `test:desktop:platforms`
  project).
- Regression-shaped test worth having: simulate `PYTHONHOME` set to a different
  minor version and assert the install still completes.

## 8. Release v4.0.3 — paired, full releases

Follow `docs/otto-desktop-release-install.md`. Condensed, with this session's
verified specifics:

```bash
# 1. land the fix on base, then bump 5 files: apps/desktop/package.json,
#    hermes_cli/__init__.py (version + __release_date__), pyproject.toml,
#    package-lock.json (apps/desktop self-entry), uv.lock (hermes-agent entry)
git checkout base && git push origin base

# 2. base -> each brand (discover, never hardcode)
BRANDS=$(ls brands/*.json | xargs -n1 basename | sed 's/\.json$//' | grep -vE '^(_|schema$)')
for BR in $BRANDS; do
  git checkout $BR && git merge base
  # expect conflicts in apps/desktop/package.json (version+description) and
  # package-lock.json. Resolve: take base's VERSION, keep the brand's
  # productName/description/appId and BOTH url schemes. For the lock, do NOT
  # hand-edit: git checkout --ours package-lock.json && npm install --package-lock-only --ignore-scripts
  node scripts/brand/generate.mjs $BR --write
  node scripts/brand/generate.mjs $BR --check     # GATE: 8/8 OK
  OTTO_BRAND=$BR .venv/bin/python -c "from hermes_cli.plugins import discover_plugins as d; d(); from hermes_cli.web_server import _messaging_platform_catalog as c; print(len(c()))"   # expect 11
  git commit && git push origin $BR
done
git checkout otto   # end state
```

Then dispatch **both** brands — a single-brand build is not a completed delivery
(paired branded-release rule):

```bash
gh workflow run release.yml --repo cmetech/otto   -f ref=<otto-sha>   -f version=4.0.3 -f stamp_branch=otto   -f prerelease=false
gh workflow run release.yml --repo cmetech/loop24 -f ref=<loop24-sha> -f version=4.0.3 -f stamp_branch=loop24 -f prerelease=false
```

**`prerelease=false` always** — Corey is the only tester and wants full releases
installable via the `irm` one-liner. Both repos' `release.yml` now default to
`false` (changed 2026-07-25), but pass it explicitly anyway.

Verify each release: `prerelease=false`, `draft=false`, 7 assets, and the body
naming the exact source commit.

## 9. Gotchas

- **`generate <brand> --check` FAILS on `base` by design** — base is neutral, so
  the 8 emitter-covered files hold upstream Hermes values. Do not "fix" it; it
  runs after `base → <brand>`.
- **Local test env:** `scripts/run_tests.sh` probes `.venv` before `venv`. A
  3.13-without-extras `.venv` silently skips ~1309 tests. Verify
  `.venv/bin/python -V` is 3.11.x and `import acp` succeeds; re-provision with
  `uv sync --locked --python 3.11 --extra all --extra dev --python-preference managed`.
  Never use bare `pytest` for a verdict.
- **Ledger:** `main.ts` is a UNION seam. Register the spawn-env change in
  `docs/upstream-customizations/` (a new manifest, or extend an existing one) so
  a future merge cannot silently restore the `process.env.PYTHONPATH` append.
  Validate with `check_upstream_customizations.py --manifest <path>` (exit 0).
- The user's existing `%LOCALAPPDATA%\loop24\hermes-agent` clone is intact and
  will be reused, so a retry resumes near the venv stage.

## 10. Deliberately NOT in v4.0.3

`docs/2026-07-25-kanban-mutation-conflict-handoff-prompt.md` — the
`TaskMutationConflict` caller gap, the 3 mechanical kanban tests, and the 2
dashboard rewrites. Corey's instruction: **that is v4.0.4.** Also already on
`base` but unreleased: `abc033a29` (outlook-mcp `stdin=`, a real Windows bug) —
it will ride along in v4.0.3, which is fine and desirable.

## 11. State at handoff

- Branch `base`, HEAD `1ced4d7c9`, clean, pushed.
- v4.0.2 released for both brands (otto `257bebfb0`, loop24 `34f480181`) — the
  build that exposed this.
- Desktop suite green: 319 files, 2964 passed, plus 34 `node:test` assertions.
- The enrolled-browser checkpoint
  (`docs/plans/2026-07-25-enrolled-browser-checkpoint-runbook.md`) is **blocked
  on this fix** — v4.0.2 cannot install on the corporate laptop. Retarget the
  runbook's version references to v4.0.3 when it ships (§1/§3 and Step 10 name
  v4.0.2).
