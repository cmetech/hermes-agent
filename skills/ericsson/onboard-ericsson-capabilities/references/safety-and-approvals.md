# Safety and Approvals

## Readiness ladder

Validate in this exact increasing-risk order and stop before any step that lacks
the required user consent/authorization or a safe prerequisite:

1. Packaged and discoverable.
2. Enabled and supported on the current platform.
3. Dependency or server startup.
4. Authentication.
5. Read-only list or retrieval.
6. Draft, preview, or synthetic execution.
7. Explicit authorization to execute the previewed write through the underlying capability.

An environment-variable name or protected-key presence is evidence only that a
value may be configured; it is never sufficient evidence for `ready`. Authentication
must be validated without exposing the value, and permissions may still be missing.

## Consent boundaries

Ask before each materially different action: installing software, starting a
dependency server, opening authentication, changing configuration, writing an
artifact, persisting onboarding state, or invoking a live side effect. Explain the
target, intended effect, data read, possible changes, and validation before asking.

Authorization is scoped to the displayed action. Acknowledging the preview, approving its content, or approving a draft alone is not write authorization.
Only explicit authorization to execute the previewed action permits the write. A
changed target, payload, destination, or effect requires a new preview and a new
authorization to execute.

## Prohibited configuration tests

An email, Teams message, Jira comment, commit, branch, or merge request is never used merely to test configuration.
Prefer discovery, authentication validation, a bounded read-only list/get, then
draft or preview.

## Secrets and diagnostics

Never request, reveal, echo, log, or summarize credentials, cookies, certificate
contents, private keys, device codes, authentication responses, or sensitive source
payloads. Report configured/not configured and successful/rejected/unchecked only.
Redact diagnostics before display or persistence; omission is safer than reproducing
a suspicious value.

If a user already pasted or offered a secret, do not repeat, use, validate, or persist the value.
Refuse to accept it in ordinary chat, direct replacement entry to protected Tools & Keys,
and advise following the documented rotation or revocation path when
applicable. Do not invent an Ericsson-specific owner, URL, approval, or revocation
process. Never place the exposed value in a command, file, onboarding state,
diagnostic, or summary.

## Partial side effects

After interruption, timeout, or uncertain success, assume a side effect may have
occurred. Inspect the target system or artifact safely before a rerun. Do not repeat
a message, comment, commit, branch, merge request, delete, download, or other change
until prior state is known and the user authorizes execution of the revised action.
