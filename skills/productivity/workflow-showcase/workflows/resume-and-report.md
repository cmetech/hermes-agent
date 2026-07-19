<required_reading>
Read `../references/showcase-contract.md` and
`../references/safety-and-interpretation.md` before interpreting evidence.
</required_reading>

<process>
1. Use `PRODUCT_CLI workflow showcase status RUN_ID --json`; never substitute
   the showcase catalog ID.
2. Read current state, health, pending interaction, state version, coordinator
   health, retry time, and `next_actions` from the envelope.
3. If a user decides a gate, refresh status and render exactly the applicable
   approve, reject, or input argv from preflight's `command_contract`, using
   the current run ID, interaction ID, and state version. Execute it once.
4. For stalled, interrupted, failed, or reconciliation-required work, offer
   only the returned authoritative actions. Do not automatically replay an
   uncertain outward effect.
5. Poll only while semantic progress or a valid future wake exists. Stop on
   no progress, coordinator unavailability, conflict, a human gate, or a
   terminal state.
6. Run `PRODUCT_CLI workflow showcase report RUN_ID --json`. Interpret each
   claim's actual outcome and evidence references independently of lifecycle.
</process>

<success_criteria>
- Every mutation used current CAS and interaction identifiers.
- Uncertain outward effects reached reconciliation instead of replay.
- The report preserves the actual terminal state; expected failure is not
  relabeled success merely because a resilience claim passed.
</success_criteria>
