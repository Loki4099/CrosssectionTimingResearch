# XA04 unified CORE10 model comparison report

## Decision

XA04 completed all four batches and stopped. The independent audit passed. No registered model-frequency cell met the full `qualified_incremental` gate, so no tree advances. Best-loser substitution is forbidden. The mechanical next branch is `RAW_XS003_ONLY`.

## Data and execution

- The same ten-factor complete-case panel was used by every model.
- Formal-period coverage never fell below 476 stocks or 96.19% of the parent common universe.
- 68 formal process-frequency cells were registered. Sixty-two were valid; six weekly state-LightGBM cells failed the common leaf-support gate. Both raw `XS003` frequency benchmarks were also replayed, giving 64 valid scored cells.
- The event-driven replay covered 256 Top-K paths and 1,024 cost paths. Portfolio accounting identity passed.

## Main evidence

At Top20 primary cost, raw `XS003_MOM_12_7` achieved terminal wealth relative to the same-universe common-EW control of 2.599 weekly and 2.742 monthly. Its annualized relative-log advantages were 11.21% and 11.99%, with active IR 0.75 and 0.83.

The strongest monthly model by absolute relative-log return was factor-only Ridge with alpha 100: relative wealth 1.478, annualized relative-log 4.64%, and active IR 0.48. Factor-only LightGBM depth2/50 reached relative wealth 1.198 and annualized relative-log 2.15%. Weekly versions did not beat common-EW after primary cost. No registered model beat raw `XS003` on the paired primary comparison; all `beats_raw_XS003` labels are false.

Some monthly Ridge and LightGBM paths improved materially over the weak DIM5 static parent, but fixed-family BH-adjusted economic q-values did not pass 0.10. This is useful diagnostic evidence that learning can repair a poor static blend; it is not evidence that the learned blend improves on the established momentum anchor.

## Interpretation and next step

XA03's missing aggregate-tree result was successfully repaired: factor-only LightGBM and monthly state-LightGBM produced valid walk-forward paths under a common sample. Their failure to advance is therefore an economic/statistical result, not a missing experiment. Weekly state trees remained capacity-invalid under the same rule applied to all trees.

XA05 may use only the same-universe raw `XS003_MOM_12_7` anchor. It may test naked, frozen P00, and matched-static exposure under the previously agreed transfer design. No XA04 model may be rescued by P00 performance, and P00 parameters may not be retuned.
