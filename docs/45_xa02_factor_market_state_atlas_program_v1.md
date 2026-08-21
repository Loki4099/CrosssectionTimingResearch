# XA02 factor performance and market-state atlas program v1

XA02 is the second experiment in the cross-sectional-alpha track. It closes the
gap between XA01 atomic-factor summaries and a later conditional aggregation
model. The experiment reconstructs complete causal single-factor paths,
materializes a small mechanism-driven set of market context variables, and
measures how each factor's next-period cross-sectional information and Top20
active return vary with those contexts.

XA02 does **not** build a market-state classifier. State variables remain
continuous causal inputs; low/mid/high bins are an interpretation device with
thresholds computed only from strictly prior history. Calendar years are
stability slices, never market states.

The experiment reuses all fourteen XA01 factors, both weekly and monthly
calendars, Top5/10/20/50, and the existing cost grid. Top20 at 10 bps weekly or
5 bps monthly is the only primary state-analysis path. Other widths and costs
are robustness diagnostics.

The output is an evidence ledger for a later user decision. It may identify
broadly useful factors, conditional sign changes, conditional strength changes,
unexplained time breaks, and empirical redundancy. Those labels do not
authorize a model input, a factor combination, or a trading rule.

The only two-dimensional relationships inspected are trend x volatility,
breadth x volatility, and cross-sectional dispersion x average member
correlation. Their 3x3 grids are descriptive; their formal tests use only the
predefined low/high 2x2 corners. No state pair is selected after results are
seen.

Detailed machine settings live in `config/experiments/xa02/program.toml`;
factor and state universes live beside it; batch mechanics and output contracts
live in `docs/20_experiments/XA02_factor_market_state_atlas/design.md`.

After XA02D the program hard-stops. A later XA03 plan may compare factor-only
and factor-plus-context rolling models, with a next-rebalance cross-sectional
return-rank target, but XA02 neither freezes nor runs that model design.
