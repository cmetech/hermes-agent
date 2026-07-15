# Discover and Recommend

## Entry

Use when the user is new, asks what can help, or gives a goal without selecting a
capability. If the goal is already known, do not ask for role as a formality.

## Load

Load only `../references/catalog.json`.

## Procedure

1. Welcome the user in one sentence and ask one missing role-, goal-, or
   outcome-oriented question.
2. Match the answer against catalog goals and aliases. Exclude entries whose
   `recommendationEligible` value is false.
3. Recommend at most two candidates. For each, state the goal match and product
   maturity; do not imply that catalog presence proves live readiness.
4. If a partial, planned, or unsupported entry directly matches, explain its
   maturity separately and do not recommend or execute it as runnable.
5. Ask the user to select a candidate or refine the goal. Ask only this one
   question.

After selection, load only
`../references/capabilities/{selected-capability-id}.md` and route to the requested
depth workflow. Do not load other capability entries.

## Checkpoint

Record the sanitized goal, candidate IDs, reported maturity, selection, and one
suggested next prompt. Do not record source-system content.

## Exit

Exit when one capability is selected, the user asks to pause, or no runnable match
exists. When no runnable match exists, state that honestly and offer only a real,
available alternative.
