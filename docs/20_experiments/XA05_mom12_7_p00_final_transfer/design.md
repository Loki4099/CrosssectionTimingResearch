# XA05 final P00 transfer design

## Fixed inputs

The sole base signal is XA04's `RAW_XS003_CORE10`. All weekly/monthly Top-K holdings are byte-anchored to XA04. P00 is byte-anchored to the R8A and R10B state schedules and stitched at the registered boundary. No factor, model, threshold, allocation, exit rule, frequency, Top-K width, or cost is selected from XA05 outcomes.

## Paths

There are eight transfer cells: two frequencies by four Top-K widths. Each cell has naked, P00 overlay, and matched-static paths at 0/5/10/20 bps, for 96 economic paths. Matched-static allocation is solved once at zero cost to match P00 mean realized long exposure and is reused unchanged at every cost.

Monthly holdings change only at monthly base events. Weekly P00 events between monthly rebalances scale the current book without reranking. On dates where base and overlay events coincide, the engine forms one final target and charges one L1 cost.

## Drawdown evidence

Daily metrics include maximum drawdown depth, peak/trough/recovery dates, duration and recovery sessions, underwater fraction, average drawdown, Pain Index, Ulcer Index, 95% conditional drawdown at risk, worst daily/weekly/monthly return, downside risk, and exposure. The ten worst distinct drawdown episodes are retained per path. Rolling 63/126/252-session drawdown ledgers and calendar-year results are mandatory.

Primary reporting is monthly Top20 at 5 bps. Weekly Top20 at 10 bps is the frequency replication. All Top-K/cost cells remain visible. The primary four-metric gate requires P00 to beat naked terminal wealth, beat matched-static timing value, improve Sharpe, and improve maximum drawdown. Family and 20-bps direction gates are reported without changing the underlying paths.

## Evidence label and stop

The interval has already informed earlier research. Results are full-history causal prequential evidence, not a new independent holdout. XA05C is a hard stop and never authorizes automatic deployment.
