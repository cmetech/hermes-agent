#!/usr/bin/env bash
# Canonical test runner for hermes-agent. Run this instead of calling
# `pytest` directly to guarantee your local run matches CI behavior.
#
# What this script enforces:
#   * Per-file isolation via scripts/run_tests_parallel.py — each test
#     file runs in its own freshly-spawned `python -m pytest <file>`
#     subprocess. No xdist, no shared workers, no module-level leakage
#     between files.
#   * TZ=UTC, LANG=C.UTF-8, PYTHONHASHSEED=0 (deterministic)
#   * Env vars blanked (conftest.py also does this, but this
#     is belt-and-suspenders for anyone running pytest outside our
#     conftest path — e.g. on a single file)
#   * Proper venv activation (probes .venv, venv, then ~/.hermes/...)
#
# Usage:
#   scripts/run_tests.sh                            # full suite
#   scripts/run_tests.sh -j 4                       # cap parallelism
#   scripts/run_tests.sh tests/agent/               # discover only here
#   scripts/run_tests.sh tests/agent/ tests/acp/    # multiple roots
#   scripts/run_tests.sh tests/foo.py               # single file
#   scripts/run_tests.sh tests/foo.py -q            # path + bare pytest flag
#   scripts/run_tests.sh tests/foo.py -v --tb=long  # bare flags "just work"
#   scripts/run_tests.sh -k 'pattern'               # value flags pass through too
#   scripts/run_tests.sh tests/foo.py -- --tb=long  # explicit '--' still works
#
# Bare pytest flags (anything starting with '-' that isn't one of this
# runner's own options: -j/--jobs, --paths, --slice, --file-timeout, etc.)
# are forwarded to each per-file pytest invocation automatically — no '--'
# separator required. The explicit '--' form still works and stacks with
# bare flags. Positional path arguments override the default discovery
# root (tests/).

set -euo pipefail

# ── Locate repo root ────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Locate python ───────────────────────────────────────────────────────────
# Probe local venvs first; fall back to the Nix devShell's editable venv
# (HERMES_PYTHON is exported by the devShell hook and ships [dev] extras:
# pytest, pytest-asyncio, pytest-timeout, ruff, ty).
PYTHON=""
for candidate in "$REPO_ROOT/.venv" "$REPO_ROOT/venv" "$HOME/.hermes/hermes-agent/venv"; do
  if [ -x "$candidate/bin/python" ] \
    && "$candidate/bin/python" -c 'import pytest' 2>/dev/null; then
    PYTHON="$candidate/bin/python"
    break
  fi
  # uv creates the standard ``Scripts`` layout on native Windows.  The
  # portability matrix invokes this script through Git Bash, so probe the
  # interpreter itself instead of assuming a POSIX ``bin/activate`` file.
  if [ -x "$candidate/Scripts/python.exe" ] \
    && "$candidate/Scripts/python.exe" -c 'import pytest' 2>/dev/null; then
    PYTHON="$candidate/Scripts/python.exe"
    break
  fi
done

if [ -n "$PYTHON" ]; then
  :
elif [ -n "${HERMES_PYTHON:-}" ] && [ -x "$HERMES_PYTHON" ] \
    && "$HERMES_PYTHON" -c 'import pytest' 2>/dev/null; then
  # Guard with an import check: HERMES_PYTHON may point at the RELEASE
  # venv (no pytest) when inherited from a wrapped `hermes` binary rather
  # than the devShell hook.
  PYTHON="$HERMES_PYTHON"
  echo "▶ no local venv — using Nix dev venv via HERMES_PYTHON: $PYTHON"
else
  echo "error: no virtualenv found in $REPO_ROOT/.venv or $REPO_ROOT/venv," >&2
  echo "       and HERMES_PYTHON is not a python with pytest (enter the Nix devShell or create a venv)" >&2
  exit 1
fi


# ── Live-gateway plugin (computed before we drop env) ───────────────────────
EXTRA_PYTHONPATH=""
EXTRA_PYTEST_PLUGINS=""
if [ -f "$HOME/.hermes/pytest_live_guard.py" ]; then
  EXTRA_PYTHONPATH="$HOME/.hermes"
  EXTRA_PYTEST_PLUGINS="pytest_live_guard"
fi


# ── Run in hermetic env ──────────────────────────────────────────────────────
# env -i: start with empty environment, opt-in only what we need.
# No credential var can leak — you'd have to explicitly add it here.
echo "▶ running per-file parallel test suite via run_tests_parallel.py"
echo "  (TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, .pyc redirected out of tree; clean env)"

cd "$REPO_ROOT"

# ── Keep bytecode OUT of the source tree ────────────────────────────────────
# `git ls-files '*.py'` below matches every tracked .py, which includes the
# scripts inside workflow showcase bundles
# (plugins/workflow/showcases/packages/*/scripts/*.py). Writing __pycache__
# next to those puts a non-UTF-8 file INSIDE a bundle whose safety contract
# rejects binary resources, so the whole bundle fails to load:
#
#   ShowcaseCatalogError: showcase safety contract rejects binary resource
#   UnicodeDecodeError: 'utf-8' codec can't decode byte 0xa7   (the .pyc magic)
#
# That single side effect accounted for ~101 failures across all 8 CI slices
# the first time CI ran on this fork's development branch, and made a focused
# `run_tests.sh tests/plugins/workflow/` run unreproducible locally unless you
# manually deleted __pycache__ between runs.
#
# PYTHONPYCACHEPREFIX (3.8+) redirects every .pyc into a mirrored tree outside
# the repo, so we keep the caching AND stop the pollution. Deliberately not
# PYTHONDONTWRITEBYTECODE: that would fix the pollution by making each of the
# ~2000 subprocesses recompile from source, which is exactly the cost the
# pre-compile step below exists to avoid. The prefix is absolute and stable so
# the cache stays warm across runs; cache_from_source mirrors the absolute
# source path underneath it, so separate checkouts and worktrees cannot collide.
PYCACHE_PREFIX="${HERMES_TEST_PYCACHE_DIR:-${TMPDIR:-/tmp}/hermes-test-pycache}"
export PYTHONPYCACHEPREFIX="$PYCACHE_PREFIX"

# Self-heal a checkout polluted by an earlier run (or by anyone invoking
# compileall / pytest by hand). The redirect above only prevents NEW writes, so
# without this an existing tree keeps failing exactly as before and the fix
# looks like it did nothing. Scoped to the showcase bundles: they are the trees
# under a byte-level safety contract, and they contain no bytecode we want.
if [ -d "$REPO_ROOT/plugins/workflow/showcases" ]; then
  find "$REPO_ROOT/plugins/workflow/showcases" -name '__pycache__' -type d \
    -exec rm -rf {} + 2>/dev/null || true
fi

# ── Pre-compile .pyc bytecode cache ─────────────────────────────────────────
# Each test file runs in its own subprocess via run_tests_parallel.py.
# Pre-building the bytecode cache once here (instead of each subprocess
# compiling on first import) avoids redundant work across ~2000 processes.
# Uses git to list tracked .py files (skips venv, node_modules, etc).
echo "▶ pre-compiling bytecode cache"
"$PYTHON" -m compileall -q -j 0 -- $(git ls-files '*.py') >/dev/null 2>&1 || true

# Ledger invariants are already isolated, supervised, captured, and retried by
# run_workflow_ledger_invariants.py.  Keep this wrapper authoritative for
# interpreter selection and the clean environment, but preserve pytest's native
# exit/signal status so the ledger can distinguish test failures from
# infrastructure failures.  The controller-only option is accepted solely to
# enforce that its retry layer is disabled.
if [ "${WORKFLOW_LEDGER_EXECUTION_ACTIVE:-}" = "1" ]; then
  invalid_ledger_command() {
    echo "error: invalid ledger wrapper command" >&2
    exit 2
  }
  [ "$#" -eq 5 ] || [ "$#" -eq 7 ] || invalid_ledger_command
  [ "$1" = "--workflow-ledger-single-file" ] || invalid_ledger_command
  LEDGER_TEST_PATH="$2"
  case "$LEDGER_TEST_PATH" in
    tests/*.py) ;;
    *) invalid_ledger_command ;;
  esac
  case "/$LEDGER_TEST_PATH/" in
    *"//"*|*"/./"*|*"/../"*) invalid_ledger_command ;;
  esac
  [ ! -L "$REPO_ROOT/$LEDGER_TEST_PATH" ] \
    && [ -f "$REPO_ROOT/$LEDGER_TEST_PATH" ] \
    && git -C "$REPO_ROOT" ls-files --error-unmatch -- "$LEDGER_TEST_PATH" \
      >/dev/null 2>&1 \
    || invalid_ledger_command
  [ "$3" = "--file-retries" ] && [ "$4" = "0" ] && [ "$5" = "-q" ] \
    || invalid_ledger_command
  LEDGER_PYTEST_ARGS=("$LEDGER_TEST_PATH" "-q")
  if [ "$#" -eq 7 ]; then
    [ "$LEDGER_TEST_PATH" = \
        "tests/plugins/workflow/test_installed_distribution_e2e.py" ] \
      && [ "$6" = "-m" ] && [ "$7" = "integration" ] \
      || invalid_ledger_command
    LEDGER_PYTEST_ARGS+=("-m" "integration")
  elif [ "$LEDGER_TEST_PATH" = \
      "tests/plugins/workflow/test_installed_distribution_e2e.py" ]; then
    invalid_ledger_command
  fi
  echo "▶ launching isolated ledger invariant"
  exec env -i \
    PATH="$PATH" \
    HOME="$HOME" \
    TZ=UTC \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUTF8="${PYTHONUTF8:-1}" \
    PYTHONHASHSEED=0 \
    PYTHONPYCACHEPREFIX="$PYCACHE_PREFIX" \
    HERMES_TEST_FILE_RETRIES=0 \
    HERMES_OFFLINE="${HERMES_OFFLINE:-1}" \
    WORKFLOW_LEDGER_EXECUTION_ACTIVE=1 \
    "$PYTHON" -m pytest "${LEDGER_PYTEST_ARGS[@]}"
fi

echo "▶ launching test runner"
exec env -i \
  PATH="$PATH" \
  HOME="$HOME" \
  TZ=UTC \
  LANG=C.UTF-8 \
  LC_ALL=C.UTF-8 \
  PYTHONUTF8="${PYTHONUTF8:-1}" \
  PYTHONHASHSEED=0 \
  PYTHONPYCACHEPREFIX="$PYCACHE_PREFIX" \
  ${HERMES_TEST_WORKERS:+HERMES_TEST_WORKERS="$HERMES_TEST_WORKERS"} \
  ${HERMES_TEST_FILE_RETRIES:+HERMES_TEST_FILE_RETRIES="$HERMES_TEST_FILE_RETRIES"} \
  ${HERMES_RUN_SLOW_PET_TESTS:+HERMES_RUN_SLOW_PET_TESTS="$HERMES_RUN_SLOW_PET_TESTS"} \
  ${EXTRA_PYTHONPATH:+PYTHONPATH="$EXTRA_PYTHONPATH"} \
  ${EXTRA_PYTEST_PLUGINS:+PYTEST_PLUGINS="$EXTRA_PYTEST_PLUGINS"} \
  "$PYTHON" "$SCRIPT_DIR/run_tests_parallel.py" "$@"
