<required_reading>
Read `../references/showcase-contract.md` before changing showcase-owned state.
</required_reading>

<process>
1. Resolve `PRODUCT_CLI` once and inspect the target showcase or retained runs.
2. `PRODUCT_CLI workflow showcase reset ID --json` may remove only
   ownership-tagged temporary staging. It reports an owned schedule but never
   deletes it; schedule removal uses the ordinary explicitly confirmed cron
   contract.
3. Run `PRODUCT_CLI workflow showcase cleanup --json` first. This is an impact
   report and must not delete evidence.
4. Present the impact summary. Only after explicit confirmation, execute one
   cleanup command with `--execute --confirmation-token TOKEN --json`, using
   the exact token bound to that impact report.
5. Never infer deletion authority from a missing, empty, replaced, corrupt, or
   inconsistent admission index.
</process>

<success_criteria>
- Dry-run preceded execution and reported the exact affected records.
- Execution used a fresh bound token after explicit confirmation.
- Retained evidence and unrelated schedules/staging remained untouched.
</success_criteria>
