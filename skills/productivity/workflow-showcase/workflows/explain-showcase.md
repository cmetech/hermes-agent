<required_reading>
Read `../references/showcase-contract.md` and
`../references/safety-and-interpretation.md` before making safety or outcome
claims.
</required_reading>

<process>
1. Resolve `PRODUCT_CLI` once as directed by the parent skill.
2. Run `PRODUCT_CLI workflow showcase list --json`, then
   `PRODUCT_CLI workflow showcase describe ID --json` for the selected ID.
3. Explain the returned offline/AI/network flags, expected interaction,
   possible terminal outcomes, artifacts, limits, and safety class.
4. Do not run preflight or initialize execution when the request is only an
   explanation.
</process>

<success_criteria>
- Every claim comes from the current catalog response.
- The explanation distinguishes showcase ID from a future run ID.
- No workflow state, trust state, schedule, or staging path was created.
</success_criteria>
