# XA03 cross-sectional aggregation program v1

XA03 is the first fitted-model experiment in the cross-sectional-alpha track.
It compares direct atomic signals, fitted single-factor models, transparent
aggregates, factor-only rolling models, and factor-plus-state rolling models
on one causal execution and evaluation contract. It follows XA01 atomic-factor
testing and the XA02 factor/state atlas; it does not reinterpret either parent
as an independent holdout.

This document is the human-readable program charter. Detailed mechanics and
output contracts live in the
[XA03 execution design](20_experiments/XA03_cross_sectional_aggregation/design.md). Machine
configuration, registries, run IDs, dependency hashes, and a preregistration
lock must be created and reviewed before execution. Until that lock exists,
this program is a design freeze candidate and does not authorize a run.

## Questions

The observed parents are the [XA01 atomic-factor design](20_experiments/XA01_atomic_factor_walkforward/design.md)
and [XA02 factor/state-atlas design](20_experiments/XA02_factor_market_state_atlas/design.md).

XA03 answers five ordered questions:

1. **D0:** how do the fourteen direct factor ranks perform on a common,
   missing-aware stock universe?
2. **S1:** does a low-capacity rolling model fitted to one factor improve on
   that factor's direct rank?
3. **A0:** how much is gained by transparent equal-weight or family-balanced
   rank aggregation without fitting a model?
4. **A1:** do Ridge or shallow LightGBM improve on the matching transparent
   aggregate when they receive factors but no market state?
5. **A2:** do the two XA02-supported context axes or all six primary contexts
   improve the matching factor-only model, and is any improvement specifically
   dependent on RSP/SPY63 breadth?

The process classes are fixed as follows:

| Class | Inputs | Method | Role |
|---|---|---|---|
| D0 | one of all 14 factors | direct rank | common-universe atomic baseline |
| S1 | one of all 14 factors | rolling Ridge or shallow LightGBM | learned single-factor mapping |
| A0 | ROLE5 or ALL14 | deterministic rank mean or family-balanced rank | transparent aggregation control |
| A1 | ROLE5 or ALL14 | rolling Ridge or shallow LightGBM | factor-only aggregation model |
| A2 | ROLE5+S2 or ALL14+S6, with mandatory no-RSP twins | rolling Ridge interactions or shallow LightGBM | conditional aggregation model |

There are 57 prediction processes per frequency and 114 across weekly and
monthly frequencies. The four Top-K widths produce 456 signal paths; the four
cost views produce 1,824 path-cost results. Inner model recipes are selected
causally inside a process and never become post-result strategy paths.

## Sample, target, and common universe

The economic evaluation remains 2018-01-02 through 2026-06-30, matching XA01
and XA02. A fixed 2022-01-01 through 2026-06-30 view is reported from the same
already-running OOS predictions as a mature-model slice; it is not a second
start date or a selection sample.

XA01's existing target ledger begins at the first evaluation signal and cannot
train a 2018 model. XA03A therefore builds a new, additive 2014-2017 warm-up
target ledger from the certified market/calendar inputs. It does not overwrite
the XA01 parent. Weekly and monthly targets remain separate next-rebalance
open-to-open outcomes. The fitted target is the within-date cross-sectional
average rank of forward return, transformed as
`2 * (rank - 1) / (finite_count - 1) - 1` to span `[-1, 1]`.

The primary universe is the point-in-time S&P 500 membership at the signal
close with at least 10 of the 14 factor percentiles finite and with the frozen
execution/data gates satisfied. A strict ALL14 complete-case universe is
forbidden as the primary comparison because it is small, time-varying, and
would confound model performance with SEC coverage. Missing aggregate inputs
are assigned the neutral centered percentile zero; missing flags and factor
coverage counts are diagnostics, not predictors.

All new economic comparisons use the equal-weight portfolio of this common
eligible universe as the primary control. Historical XA01 paths remain visible
as immutable legacy evidence, but their factor-specific eligible controls are
not substituted for the common-universe parent in incremental tests.

## Input bundles

`ROLE5` contains the two XA02 conditional factors and the three factors with a
broad-static monthly or cross-frequency role:

- `XS002_MOM_12_1`;
- `XS003_MOM_12_7`;
- `XS008_SAME_MONTH_5Y`;
- `XS041_ASSET_GROWTH`;
- `XS056_CFO_ACCRUALS_PT`.

`ALL14` contains every XA01 factor. No XA01 failure is silently deleted from
ALL14, because weak atomic information may still be useful jointly and the
model is responsible for down-weighting it.

`S2` contains `MKT_BREADTH_RSP63` and `MKT_TREND126`. Its forced no-RSP twin
contains trend only. `S6` contains all six XA02 primary raw contexts. Its
forced `S5_NO_RSP` twin removes only `MKT_BREADTH_RSP63`. Shadow states,
calendar-year identifiers, future drawdowns, named crises, and unregistered
context windows are forbidden.

The A2 matrix is deliberately not a full bundle cross-product. Per frequency
it contains exactly:

- ROLE5+S2 Ridge and LightGBM;
- ROLE5+trend-only Ridge and LightGBM as no-RSP controls;
- ALL14+S6 Ridge and LightGBM;
- ALL14+S5_NO_RSP Ridge and LightGBM as no-RSP controls.

## Walk-forward and models

Models use the most recent 260 complete weekly or 60 complete monthly
scheduled-label dates, with minimum histories of 156 weekly or 36 monthly
dates. Before the cap is first reached, all complete dates after the minimum
form an early expanding window; after the cap, the count rolls. Every training
row must have a target availability timestamp no
later than the prediction signal close under explicit open/close timestamps.
Splits, transforms, recipe
selection, and inference are grouped by whole signal dates; stock rows are
never random-split or treated as independent time observations.

Models refit monthly at every scheduled monthly signal close. The monthly
process scores that anchor; the weekly process uses the new fit on the same
date only when it is also a scheduled weekly signal, otherwise at the first
subsequent weekly signal. Later weekly decisions carry the most recent fit
until the next monthly-close refit. Current weekly factor and state inputs
still change at every weekly decision.

Model recipes are selected once before the first OOS signal of each execution
year, keyed by first execution-open year, using only prior chronological
validation blocks and are frozen for that year. The one-SE rule uses a locked
moving-block bootstrap of best-minus-candidate date RankIC, not an IID
stock-row error. An A2 process inherits the recipe of its matching A1
factor-only parent; state inputs may not trigger a second recipe search.

Ridge uses a squared-error objective and is the transparent regularized linear
family. LightGBM is restricted to
deterministic depth-two trees so it may learn factor-factor or factor-state
relationships but not an unregistered state-state-factor rule. ROLE5 Ridge A2
uses only the XA02-targeted momentum-by-breadth and seasonality-by-trend linear
and quadratic interactions; ALL14 Ridge A2 uses all factor-by-state linear
interactions. Tercile interactions are forbidden. A state main effect is
constant across all stocks on a date and therefore cannot alter a cross-
sectional rank. LightGBM receives the matching factor and state sets and can
form a two-way interaction by a state split followed by a factor split.

## Economic paths and comparisons

Every process produces Top5, Top10, Top20, and Top50 equal-weight long-only
portfolios. Costs are 0, 5, 10, and 20 bps on actual L1 turnover. Top20 is the
primary width; 10 bps is primary weekly and 5 bps primary monthly. Signal and
portfolio results are reported separately by frequency. Weekly and monthly
models are never pooled or chosen against each other inside training.

Fixed parent comparisons are:

- S1 versus its own D0 factor;
- each A0 transparent aggregate versus D0 `XS003_MOM_12_7`;
- ROLE5 A1 versus ROLE5 equal rank;
- ALL14 A1 versus ALL14 family-balanced rank, with ALL14 equal rank secondary;
- each with-RSP A2 versus the same algorithm and factor bundle in A1;
- each with-RSP A2 versus its exact no-RSP A2 twin.

No-RSP A2 processes are mechanism controls. They do not enter the main
champion family and cannot advance alone, but they remain essential for a
later P00 interaction test.

## Evidence roles and hard stop

Qualification never imposes a maximum candidate count. Absolute, parent-child
incremental, state incremental, RSP incremental, broad robustness, conditional
specialist, predictive-only, unstable, non-qualified, and invalid roles are
reported separately. Opposite fixed-subperiod signs do not automatically
delete a process: a full-path-qualified process may be retained as a
`conditional_specialist`, consistent with the purpose of XA02.

XA03E hard-stops after producing the complete prediction, portfolio,
incremental-comparison, robustness, concentration, and role ledgers. It may
recommend candidates for a new plan but cannot run P00, alter market exposure,
select a final strategy, bag or stack models, search a new factor/state/target,
or open a lockbox.

A later P00 experiment, if authorized, must use a frozen 2x2 comparison:
`with-RSP model / no-RSP twin` by `P00 off / P00 on`. That future design keeps
RSP reuse identifiable. XA03 records the required no-RSP twins but keeps all
four P00 cells closed.
