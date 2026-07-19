<required_reading>
Read `../references/showcase-contract.md` and
`../references/safety-and-interpretation.md` before admission.
</required_reading>

<process>
1. Resolve `PRODUCT_CLI` once and run `PRODUCT_CLI workflow showcase preflight
   ID --json`.
2. Read `result.command_contract`; it is the authority for general workflow
   commands after admission. Never probe alternative mutating syntax.
3. Explain synthetic inputs and the expected outcome. For Laptop Diagnostic,
   request one short symptom. For resilience, request retry, timeout, or cancel.
4. Derive one stable intent key from the durable user request. Reuse it after
   transport errors or retries.
5. For AI or scheduling, show the disclosed scope and get explicit consent.
   Pass only the exact digest-bound confirmation token from preflight.
6. Execute one `PRODUCT_CLI workflow showcase run ID ... --idempotency-key
   INTENT_KEY --json` command. Add only inputs and tokens the preflight requires.
7. If the response returns `coordinator_unavailable`, report that background
   admission was refused. Do not poll a run that was not accepted.
8. If the run pauses, present the interaction and stop for the user. Never
   approve, reject, or supply outward-action consent for the user.
</process>

<success_criteria>
- Preflight preceded mutation and the same intent key survived every retry.
- Exactly one mutating command ran at a time.
- The returned run ID, not the showcase ID, is retained for lifecycle actions.
- Human gates and coordinator unavailability stop autonomous execution.
</success_criteria>
