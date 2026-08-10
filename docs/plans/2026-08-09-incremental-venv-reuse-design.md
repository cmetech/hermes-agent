# Incremental venv reuse on upgrade — design (not yet implemented)

**Status:** design only. No code change ships with this document.
**Motivation:** every desktop-bootstrap upgrade recreates the venv
(`install.ps1 Install-Venv`: "Virtual environment already exists,
recreating...") and reinstalls all ~250 locked packages via
`uv sync --extra all --locked` into the empty environment. On Windows
servers the resulting small-file churn (amplified by AV real-time
scanning and small filesystem caches) dominates upgrade time. The
`dependencies` stage is ALREADY incremental — `uv sync --locked`
converges an existing venv in place — so the only change needed is for
the `venv` stage to *keep* a trustworthy venv instead of recreating it.

## Why recreation exists today (what we must not lose)

1. **No drift, ever.** A from-scratch, hash-verified sync cannot carry
   forward a corrupted or interrupted previous state.
2. **Windows DLL locks.** In-place package upgrades must replace `.pyd`
   files that running hermes processes hold open. The recreate path's
   process-kill sweep + gateway-task disarm exists for exactly this.
3. **Interpreter provenance.** Old venvs may be rooted at a system
   Python an earlier install adopted (the "SRE module mismatch" class
   the python-isolation work closed). Recreation guarantees the
   uv-managed interpreter.

## The reuse ladder (venv stage decision)

Keep the existing venv and skip recreation ONLY when ALL of:

1. `venv\Scripts\python.exe` (posix: `venv/bin/python`) exists and runs
   (`python -c "import sys; print(sys.version_info[:2])"` exits 0).
2. Its major.minor equals the target `$PythonVersion` after
   `Resolve-AvailablePythonVersion` (a Python bump → recreate).
3. `pyvenv.cfg`'s `home` points inside the uv-managed interpreter root
   (`Get-ManagedPythonPath` / `find_managed_python` prefix match) — a
   venv rooted at a discovered system interpreter is NOT trusted
   (recreate; that is the SRE-mismatch class).
4. The baseline import probe passes in the existing venv:
   `python -c "import dotenv, openai, rich, prompt_toolkit"` exits 0.

Any check failing → today's full recreate path, unchanged. The
process-kill sweep and gateway-task disarm run in BOTH paths (in-place
upgrades hit the same DLL locks).

## Self-heal fallback (dependencies stage)

`uv sync --extra all --locked` failing against a REUSED venv must not
surface as an install failure before one recovery attempt:

- The venv stage records its decision in a breadcrumb file
  (`$InstallDir\venv\.reused-venv`, deleted on recreate) so the
  dependencies stage — possibly a separate process under the stage
  protocol — knows reuse happened.
- On sync failure (or baseline-import failure after sync) WITH the
  breadcrumb present: delete the venv (same rename-aside machinery),
  recreate it from the managed interpreter, re-run
  `uv sync --extra all --locked` once, then continue with the existing
  tier cascade. Without the breadcrumb: today's behavior.

This preserves the "an interrupted upgrade cannot poison the next one"
property: the poisoned state is detected and destroyed within the same
run, not trusted forever.

## Stage-protocol impact

None structural. The venv stage may emit `skipped=true` with
`reason="existing venv reused (interpreter + provenance verified)"` so
GUI drivers show the fast path honestly. No manifest change, no
protocol-version bump.

## Test matrix (the real cost of this change)

Automated (pytest, following tests/test_install_npm_deps_gate.py
patterns — extract functions, run against stub interpreters):
- ladder accepts a healthy managed venv; rejects on each individual
  check (missing python, wrong version, foreign `home`, failing probe)
- breadcrumb written on reuse, absent after recreate
- wiring: kill sweep runs on both paths; fallback recreates exactly once

Manual, on a real Windows VM before shipping:
- release upgrade over a healthy venv (fast path)
- upgrade with the gateway running (DLL locks → sweep → in-place sync)
- upgrade after killing the previous upgrade mid-sync (fallback fires)
- upgrade across a Python minor bump in the repo (ladder check 2 →
  recreate)
- both brands (OTTO + LOOP24), per the paired branded-release rule

## Non-goals

- No change to `uv.lock` handling, tiers, or the `--extra all` policy.
- No reuse across Python version changes, ever.
- No attempt to keep a venv whose provenance cannot be proven.
