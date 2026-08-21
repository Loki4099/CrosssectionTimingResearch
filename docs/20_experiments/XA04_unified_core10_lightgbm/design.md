# XA04 unified CORE10 model comparison design

## Question

Can low-capacity Ridge or LightGBM aggregation improve a common-universe long-only S&P 500 strategy when every model receives exactly the same information and is tested under exactly the same backtest contract?

## Data gate and sample

The gate is computed without returns over 2014-01-31 through 2017-12-29. A factor must have at least 95% finite PIT coverage on every scheduled weekly and monthly signal date. The retained set is `CORE10`; the four excluded XA03 fields are excluded only for coverage. All formal paths use members for which all ten values are finite. Missing-value imputation, native missing branches, coverage flags, model-specific universes, delayed starts, fallbacks, and recipe substitution are forbidden.

The formal path starts at the 2017-12-29 signal for execution at the 2018-01-02 open and ends at 2026-06-30. Weekly and monthly targets are next-rebalance open-to-open total returns converted once, inside the shared stock-date universe, to centered cross-sectional ranks. A target may train a fit only after its ending open has occurred.

## Models

Each frequency contains 34 registered paths: two transparent static aggregates, four fixed Ridge factor-only paths, four fixed LightGBM factor-only paths, sixteen Ridge factor-state paths, and eight LightGBM factor-state paths. Every hyperparameter tuple is a permanent path; there is no annual recipe selection.

State Ridge uses all factor-by-state interactions and no rank-invariant state main effect. State LightGBM receives the same ten factors plus the registered train-only transformed states. The RSP state is always paired with an otherwise identical no-RSP twin.

For all LightGBM paths, date-balanced sample weights and the same leaf-support contract apply. A failed scheduled refit invalidates that complete process-frequency cell. Nothing falls back to a static model or carries an old fit.

## Portfolio and inference

All scores are replayed as Top 5/10/20/50 equal-weight long-only portfolios. Top 20 is primary. Weekly primary cost is 10 bps and monthly primary cost is 5 bps; 20 bps is the stress gate. The common equal-weight universe and raw `XS003_MOM_12_7` are replayed on the identical panel.

Paired circular moving-block bootstrap uses 5,000 shared draws and blocks of 13 weekly or 3 monthly dates. BH is applied separately by frequency to the fixed absolute, learned-parent, and RSP-ablation families. Invalid paths remain in their families with p=1.

Qualification requires the exact machine gates in `program.toml`. All qualified paths survive. `beats_raw_XS003` is an additional label, not a substitute qualification rule. Results remain full-history causal prequential evidence, not independent holdout evidence.

## Hard stop

XA04D is a hard stop. It may emit a mechanical candidate ledger for review, but it cannot start P00 transfer, retune any factor/model, or authorize XA05.
