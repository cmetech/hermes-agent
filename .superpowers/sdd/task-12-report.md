# Task 12 Report — Atomic Snapshot Hardening, Correction Round 4

## Outcome

Snapshot rejection can no longer be authenticated by a nonce exposed in
`BASH_EXECUTION_STRING`. The bootstrap is delivered to the login shell only
after profile evaluation through stdin. When a rejected snapshot requires
per-command login-shell fallback, the requested working directory is now set by
the backend process boundary and the wrapper emits no shell-level `cd`, so a
profile-defined `cd()` function cannot intercept Hermes internals.

The generic `_run_bash` contract now carries two narrowly scoped controls:

- `script_stdin=True` executes `cmd_string` with `bash [-l] -s` and is mutually
  exclusive with user `stdin_data`.
- `cwd=...` asks the concrete backend to set the spawned process working
  directory before Bash starts.

Local, Docker, SSH, Singularity, Modal, and Daytona implement both controls.
Modal streams and closes the SDK stdin explicitly; Daytona uses a uniquely
delimited heredoc consumed by its inner login Bash process.

## Regression Coverage

- A malicious login profile extracts any visible fallback nonce from
  `BASH_EXECUTION_STRING`, forges the exact readonly marker, and exits 78. The
  regression proves that attack succeeds against the former `bash -c` shape
  but is never triggered by the stdin bootstrap.
- The rejected-profile fallback is exercised with and without an EXIT trap.
- Arbitrary `builtin()`, `set()`, `unset()`, and `cd()` functions survive for
  the user's command, while snapshot internals invoke none of them.
- Fallback execution preserves profile environment/functions, requested cwd,
  command status, and `env.cwd` without publishing a lossy snapshot.
- An early profile exit 78 remains unauthenticated and takes the broken-login
  non-login fallback path.
- Focused adapter tests assert argv/SDK boundaries for every concrete backend,
  including Docker option ordering, SSH's sanitized cwd launcher, Modal stdin
  writes/drains/EOF, and Daytona's inner `bash -l -s` command.

## Verification

- Base contract, real-shell regressions, init-session cwd, and Windows/MSYS:
  `93 passed`.
- Existing Docker, SSH, Daytona, Modal, and Singularity sibling suites:
  `157 passed, 11 skipped` (the skips require a configured live SSH target).
- Focused base and backend contract rerun after strengthening the forgery
  oracle: `49 passed`.
- Ruff on all changed Python files: passed.
- `git diff --check`: passed.

## Scope

Production changes are limited to the environment base interface and its six
concrete backends. Tests were updated only where `_run_bash` fakes implement the
expanded contract, plus the focused cross-backend contract suite. The unrelated
untracked `does-not-matter` fixture was left untouched.
