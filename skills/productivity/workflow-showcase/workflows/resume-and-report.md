# Resume and report

Use `hermes workflow showcase status RUN_ID --json` to explain the durable lifecycle. User approval/rejection goes through the ordinary `hermes workflow approve|reject ... --continue --json` contract. Then run `hermes workflow showcase report RUN_ID --json`. Interpret each claim's actual outcome and evidence refs; an expected timeout can prove resilience while the workflow remains truthfully failed.
