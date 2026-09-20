# Install/update Python bytecode preparation

## Purpose and scope

Prepare Python's normal import caches once during installation/update, moving
compilation work out of the first `hermes serve` launch. The same runtime serves
Desktop, CLI, and TUI, so this preparation can help all three. It does not change
agent reasoning, prompts, tools, RPC methods, or session behavior. No dependency,
user-facing environment variable, or normal-launch preparation hook is added.

Source code remains authoritative. Missing or unwritable caches fall back to
Python's normal source loading. Installation takes longer and uses more disk
space in exchange for less compilation on first use; this cannot eliminate
module execution, skill discovery, antivirus scanning, or provider latency.

## Measured motivation

On 2026-09-20, before implementation, three native Windows paired `hermes serve`
probes against neutral base used fresh temporary profiles and redirected caches.
External network connections were blocked in the probe process; loopback was
allowed for Windows asyncio and the local backend. Readiness was the actual
`HERMES_BACKEND_READY` sentinel, not the descriptive startup banner.

| Pair | Empty cache readiness | Populated cache readiness | Source compilation in empty-cache run |
| --- | ---: | ---: | ---: |
| 1 | 27.116 s | 16.506 s | 4.904 s |
| 2 | 27.640 s | 16.288 s | 4.937 s |
| 3 | 34.488 s | 18.696 s | 6.388 s |

Each empty-cache run compiled 1,619 modules; each populated-cache run compiled
zero through the instrumented source loader. These are diagnostic comparisons,
not release benchmarks. They include standard-library and filesystem-cache
effects; the product helper deliberately does not write into a shared standard
library. Do not promise the whole timing difference as the feature's speedup.

## Load-bearing integration

- `hermes_cli/bytecode_cache.py` derives source roots from setuptools metadata,
  force-refreshes source caches, and incrementally compiles the current venv's
  dependency directories. Compilation does not execute target modules.
- Its install-only entry clears old caches before any application imports, then
  records the revision fingerprint before compiling. Existing fingerprint and
  cleanup primitives are shared with `main.py` through compatibility wrappers;
  normal launches still use the existing checkout guard, not precompilation.
- Windows calls it from `Stage-BootstrapMarker`, before the completion marker.
- POSIX calls it from both `main` and `run_stage_body complete`; Desktop executes
  installer stages in separate processes, so neither path may be omitted.
- Git and ZIP updates launch the new checkout's helper with the installation Python
  after Python repair/cache cleanup, not through a potentially stale import.
- A current-checkout update backfills missing preparation once, keyed by checkout
  revision, Python cache tag, interpreter path, and cache prefix. Dependency
  repairs force preparation. Failed/interrupted attempts remain retryable.
- ZIP replacement preserves the exact installer/update marker allowlist (cache
  preparation/fingerprint, bootstrap completion, and interrupted-update state).
  Those files must not block a future ZIP update; ignored user data still does.
- Optional failures warn. Critical dependency/import verification remains
  authoritative and is not replaced by successful bytecode compilation.
- Do not add caches to resource bundles, especially workflow showcases, or to
  user profiles. Do not follow links outside selected runtime roots. System
  Python and user-site dependencies are not preparation targets.

An updater already running from an older release cannot run newly added code in
that same invocation. For that first upgrade, rerun `hermes update` after it
finishes to backfill preparation, or use the new release installer. This is an
optimization rollout limitation, not a correctness requirement: ordinary Python
loading and the existing stale-cache guard remain available.

## Verification and upstream merges

Run through the canonical hermetic runner:

```bash
scripts/run_tests.sh tests/hermes_cli/test_bytecode_precompile.py tests/test_install_bytecode_precompile.py tests/hermes_cli/test_bytecode_sweep.py tests/hermes_cli/test_uv_subprocess_env.py tests/test_install_ps1_ascii_only.py tests/hermes_cli/test_update_head_moved_gate.py tests/hermes_cli/test_update_zip_fallback_guards.py -q
scripts/run_tests.sh tests/hermes_cli/test_cmd_update.py -k git_failure_zip_fallback -q
```

Native installer cases execute a real temporary venv and the real completion
stage, including a child failure and installation paths containing spaces.
Windows fixtures supply PATHEXT to PowerShell because the hermetic runner strips
it; without it a new PowerShell process treats an executable as a document.
They cover both venv selection and a staged no-venv fallback interpreter.
macOS/Linux cases must run on their respective hosts; Windows skips are not
evidence that they passed. Packaged Windows UAT and deferred macOS/Linux UAT
remain release acceptance steps.

Fresh focused results on Windows: **50 passed, 8 skipped** across the seven
files in the first command; the separately selected real ZIP-update call-site
test passed (**1 passed**). The eight skips are native Linux/macOS installer
cases. Ruff on the new helper/tests, Bash syntax validation, and `git diff
--check` also passed. These are scoped results, not a full-suite claim.

The complete legacy `test_cmd_update.py` file was also attempted, then stopped
because existing test isolation let gateway lifecycle code spawn test-owned
children. Only the verified test process trees were stopped; the user's two
original gateway processes remained running with their original start times.
The focused ZIP case is verified; the full legacy updater file is not. Its
isolation cleanup is a separate follow-up before broader acceptance.

### Native Windows preparation probe (2026-09-20)

The installation interpreter checked 6,649 source/dependency files with zero
unavailable caches in 80.070 seconds. The probe used a short, isolated cache
prefix and temporary profile; it did not modify live installed dependency
caches. This is installation work, not recurring launch work. An earlier long
temporary cache prefix exceeded Windows path limits and produced 288 unavailable
caches; direct failing-file probes identified the path-length issue. Both
results reinforce warning-only failure handling. This is not a packaged-launch
benchmark or Windows UAT acceptance.

The full Python suite was attempted (3,775 files / approximately 42,706 tests)
and stopped after failures. These two failures were independently reproduced
on unchanged neutral base `199a16f353`:

- `tests/agent/lsp/test_diagnostics_field.py::test_patch_replace_propagates_lsp_diagnostics`
- `tests/agent/test_codex_app_server_persist.py::test_codex_turn_persists_each_message_exactly_once`

The full suite is therefore **not green or complete**. Focused checks cannot
replace that gate; integration/release needs an explicit disposition of these
baseline failures, plus the remaining native acceptance checks.

The machine-readable ownership, tests, merge strategy, and equivalence/removal
conditions live in `docs/upstream-customizations/python-bytecode-precompile.yaml`.
The upstream-merge skill discovers every committed manifest automatically. This
downstream change must not advance the shared upstream baseline. Literal `main`
remains sync-only; implementation is integrated into `base` before regenerating
branded release candidates. No official release is published before Windows UAT.
