# Claude Repository Memory

Read and follow `AGENTS.md` for the complete repository development guide.

## Branch Terminology — `base` Is Our Development Main

In this fork's normal development workflow, a developer saying **"main"**
means the `base` branch unless they explicitly say **literal `main`**,
`origin/main`, or **upstream main**. Start work from `base`, merge completed
feature work back into `base`, and use `base` for release and branch-cleanup
decisions.

The literal `main` branch is synchronization-only. Its sole purpose is to pull
changes from the Hermes fork and merge those upstream updates into `base`.
Never develop, commit, start feature branches, target pull requests, build
releases, or merge feature work on literal `main`. When wording is ambiguous,
prefer `base` and verify the relevant refs before changing Git state.

After any release build or publication workflow—successful or aborted—always
switch the working checkout back to `base` and verify the current branch before
ending the task. Release completion includes this checkout reset; do not leave
the repository on `otto`, `loop24`, or another brand branch.
