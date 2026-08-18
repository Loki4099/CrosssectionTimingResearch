# R10C mechanical outcome reveal report

R10C completed the frozen 2022-01-03 through 2026-06-30 outcome reveal over 72 paths (six cells × three controls × four costs). The mechanical lockbox gate **failed**.

For the primary `mom_255_0 / Top20 / monthly / long-only` path at 10bp, P00 overlay terminal NAV is 3.4922 versus 4.0927 naked and 3.0767 matched-static. Thus the overlay trails naked by 14.67%, while retaining +13.50% timing value relative to the same-average-exposure static control. CAGR is 32.30% versus 37.08%; Sharpe is 1.288 versus 1.202; MDD is -27.21% versus -23.80%. The four-metric gate fails on terminal wealth and MDD.

All six cells have positive timing value and positive Sharpe change at 10bp, but all trail their naked paths in terminal wealth; four of six also have worse MDD. Therefore zero of six pass the frozen four-metric family gate. The primary 13-week moving-block lower bound is -0.000259 and one-sided p=0.1176. Removing 2024 makes total timing value negative (-3.79%), so the leave-one-year gate also fails. Timing remains positive for all six cells at 20bp, but this pressure result cannot override the failed primary and family gates.

All 24 naked-to-G00 bridge identity checks pass. A pandas `Index.ne` incompatibility stopped the original program only after NAV, events, metrics, and comparisons had been written. Those artifacts were immediately hashed; the original reveal code and lock were preserved, and an independently locked read-only summary repair wrote only the leave-year table, assessment, decision, and manifest. No targets, holdings, costs, NAV, thresholds, or gates were recalculated or revised.
