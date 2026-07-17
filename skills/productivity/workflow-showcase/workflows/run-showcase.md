# Run a showcase

1. Run `hermes workflow showcase preflight ID --json`.
2. Explain synthetic inputs and the expected outcome. For Laptop Diagnostic, ask for one short symptom. For resilience, ask for retry, timeout, or cancel.
3. AI or scheduling: show the disclosed scope and obtain explicit user consent. Only then pass the exact preflight confirmation token.
4. Run `hermes workflow showcase run ID ... --json`.
5. If paused, report the interaction verbatim and wait. Never approve or reject for the user.
