# Published Round 10 results

Audited 2022-01-03 through 2026-06-30 mechanical outcome reveal for frozen P00 on six long-only `mom_255_0` cells.

- `status=completed_mechanical_lockbox`
- `mechanical_lockbox_passed=false`
- six-cell joint gate: `0/6`
- `formal_eligible=false`

The primary retained positive timing value versus matched static and improved Sharpe, but trailed naked terminal wealth and worsened maximum drawdown. See [`docs/41_round10_p00_mom255_mechanical_lockbox_decision_memo.md`](../../../docs/41_round10_p00_mom255_mechanical_lockbox_decision_memo.md).

The original reveal wrote all NAV and economic paths before a pandas `Index.ne` compatibility error in the leave-one-year summary. Those partial outputs were hashed immediately; the original reveal code and lock were preserved. An independently frozen read-only repair wrote only the leave-year table, assessment, decision, and manifest. No target, state, holding, cost, NAV, threshold, or gate was revised.
