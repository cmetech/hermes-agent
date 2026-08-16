# Credential Storage Parity — Platform Verification

**Verification date:** 2026-08-16

**Hermes branch:** `feat/hermes-credential-storage-parity`

**Verified commit:** `3c85ae48b3669c04e2ed479831c5635e528801f2`

**Status:** macOS PASS; Windows, Linux, and Container OUTSTANDING

## Platform matrix

| Platform | Status | Verification date | Owner | Evidence |
|---|---|---|---|---|
| macOS 26.5 (Darwin 25.5.0, arm64) | **PASS** | 2026-08-16 | Hermes maintainers | OS Keychain backend, CLI and live Electron reads, detached built-in scheduler read, startup environment regression, and full repository suite all passed. |
| Windows | **OUTSTANDING** | 2026-08-16 | Hermes maintainers — platform verification follow-up | Not run: backend, prompt behavior, CLI/desktop interpreters, environment isolation, and full suite remain outstanding. |
| Linux | **OUTSTANDING** | 2026-08-16 | Hermes maintainers — platform verification follow-up | Not run: backend, prompt behavior, CLI/desktop interpreters, environment isolation, and full suite remain outstanding. |
| Container | **OUTSTANDING** | 2026-08-16 | Hermes maintainers — platform verification follow-up | Not run: backend, prompt behavior, CLI/desktop interpreters, environment isolation, and full suite remain outstanding. |

The outstanding rows are not inferred passes. Each requires execution on its
named platform.

## macOS credential evidence

The gate used a newly generated disposable `HERMES_HOME` and the
`ericsson-gitlab` plugin's `pat` field. Before writing anything, a direct
Keychain lookup proved that the derived account
`hermes-profile-9cf7b8febc6d3b40086949418d8cfeb8327653c104a5ec2dbb019ab80a18942e`
did not exist. The dummy value was generated in-process, was never printed or
passed on a command line, and did not overwrite a user credential.

- The production backend resolved to `os`.
- The CLI invocation interpreter was
  `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python`.
- Production plugin configuration wrote and read the disposable value back
  successfully. The detail API reported the field set without returning its
  value.
- The logical key
  `HERMES_PLUGIN_C1E0736797ECCC74564A133264A91695_PAT` was absent from both the
  disposable `.env` and `os.environ` after configuration and readback.

An observer-only formatting error (`TypeError`) occurred after the first
production write. No result was claimed from that attempt: the exact account
was immediately cleared, direct lookup proved it absent, and the complete
write/read/isolation check was rerun successfully from a clean precondition.

## Desktop and prompt behavior

A fresh current-commit desktop build was launched as the actual Electron app
against the same disposable profile. In the live Plugins settings UI,
`ericsson-gitlab` displayed `Personal access token`, `Required`, and
`Secret set`. A full-screen screenshot taken after that live read showed no
macOS Keychain prompt or other credential dialog. Prompt count was zero, so no
initial or second prompt was observed during the read.

The desktop spawned exactly one headless `hermes serve` backend. Its Python
argv interpreter was the same repository interpreter used by the CLI:

```text
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python
```

The resolved executable behind that interpreter was:

```text
/Users/coreyellis/.local/share/uv/python/cpython-3.11.15-macos-aarch64-none/bin/python3.11
```

The backend command was `-m hermes_cli.main serve --host 127.0.0.1 --port 0`
and its environment contained the exact disposable `HERMES_HOME`. The app,
Vite process, serve backend, and the profile-scoped gateway restart process
were stopped before the scheduler gate.

## Detached scheduler evidence

With zero profile-owned desktop, backend, gateway, or ticker processes
running, the production CLI created one past-due, one-shot, no-agent job and
an actual built-in scheduler tick executed it:

- Job: `credential-storage-final-scheduler-gate` (`3af069346051`)
- Execution: `8e3fb89d493a448aaff826ec4d1bcccf`
- Durable history source: `builtin`
- Completion time: `2026-08-16T11:55:07.497685-04:00`
- Result marker:
  `CREDENTIAL_GATE_OK backend=os interpreter=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python`

The scheduled script resolved the disposable credential through the
production `PluginConfigurationService.detail` path. It did not print the
credential and ran without a GUI.

## Environment regression and repository suite

The required startup regression was executed through the repository runner:

```text
scripts/run_tests.sh tests/hermes_cli/test_env_loader.py::test_startup_scrubs_legacy_plugin_secrets_but_load_env_still_reads_them -v
1 file, 1 test passed, 0 failed in 0.5s
```

The final canonical suite was then run against the verified commit:

```text
scripts/run_tests.sh
2830 files, 33598 tests passed, 0 failed in 842.7s (14 workers)
```

Baseline `bce1f704` was 2,827 files, 0 failures, and 865.8 seconds. The final
delta is **+3 files, 0 additional failures, and -23.1 seconds**.

## Prior blocker and fixes

The earlier gate at `511bf205edc4fd2935eb10cc26ab68a1dcfa02a2`
was not accepted: the repository suite exposed eight workflow-detail file
failures caused by speculative configuration reads expanding and persisting
profile defaults. No platform verification record was committed from that
run.

The final candidate includes the reviewed corrections:

- `6021ad3c0` — non-mutating mode reads
- `80ebda865` — malformed-config read side-effect suppression
- `3c85ae48b` — speculative-expansion warning suppression

All macOS gates and the complete repository suite were rerun on
`3c85ae48b3669c04e2ed479831c5635e528801f2`; the prior blocker area was
traversed with zero failures.

## Cleanup

After evidence capture, only gate-owned artifacts were removed:

- the exact scheduler job was removed and the disposable registry reported no
  scheduled jobs;
- the production service cleared the exact Keychain account, followed by a
  direct absent lookup;
- the logical key was absent from the disposable `.env` and process
  environment;
- no process retained the exact disposable `HERMES_HOME` and no open file was
  found under it;
- the disposable profile, screenshot, scheduler output, and helper script were
  moved to macOS Trash, so they remain recoverable until Trash is emptied;
- generated desktop build changes were restored and the generated untracked
  `plugins/model-providers/otto` directory was removed.

An initial `cron remove 3af069346051` accidentally omitted the disposable
`HERMES_HOME`, returned `job not found`, and only listed default-profile jobs
without mutation; the corrected exact-scoped command then removed only the
disposable job and confirmed its registry empty.

Negative verification confirmed the original disposable profile path absent,
the exact Keychain account absent, zero exact-profile processes, no generated
provider directory, and a tracked-clean checkout. Pre-existing unrelated
untracked paths were not modified.
