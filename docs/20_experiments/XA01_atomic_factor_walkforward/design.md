# XA01 atomic-factor walk-forward design

XA01 is the first experiment in the cross-sectional-alpha track. It tests the
14 data-ready market and SEC characteristics as direct, deterministic
cross-sectional ranks. No fitted model, factor aggregation, P00 transfer,
lockbox, biweekly cadence, or parameter search is authorized.

The common history begins 2013-01-02. Every path first observes a signal at the
2017-12-29 close, executes at the 2018-01-02 open, and is valued through the
2026-06-30 close. Weekly signals use the last XNYS session of each trading week;
monthly signals use the last XNYS session of each calendar month. Both trade at
the next XNYS open. The portfolio is equal-weight, long-only and selects the
highest score, with SID ascending as the deterministic tie-break.

The registered grid contains 14 factors, Top5/10/20/50 and weekly/monthly
cadences: 112 signal paths. Reporting costs are 0/5/10/20 bps on actual L1
turnover. Top20 is the primary width; 5 bps is the monthly primary cost and 10
bps is the weekly primary cost. Cost scenarios do not create new signal paths.

Before results, the weekly-plus-monthly derived factor bundle must reproduce
the frozen v1 month-end panel exactly. Future market prices or SEC filings may
not alter an earlier weekly cross-section. XS008 carries the score formed for a
target calendar month across the weekly decisions inside that target month.
Fundamental factors use the latest filing available at each weekly close.

Weekly and monthly labels are separate executable open-to-open outcomes. The
label is unavailable until the exit execution timestamp. The terminal partial
holding interval can contribute to NAV but never to a complete label. Costs are
applied only at portfolio level. Paper-native 6-month, 12-month and 20-session
horizons are diagnostics and cannot replace either primary result.

The primary active control is the equal-weight portfolio of the factor's own
eligible universe on the same calendar. Full PIT equal weight, SPY, T-bill and
the G00 momentum path are secondary controls. XS001 Top10/20/50 must reproduce
the existing G00 weekly/monthly paths before new results may be interpreted.

Evidence-qualified factors all proceed without a count cap. If a broad
mechanism dimension has no evidence-qualified member, one member may be kept as
a dimension representative only after passing the weak information and
execution gates. That label authorizes a later aggregation ablation; it is not
a standalone-factor success. A dimension may remain empty.

XA01A integrates and audits data, calendar, labels and G00 identity. XA01B
produces signal diagnostics. XA01C runs all portfolio paths. XA01D performs
robustness, correlation, concentration and decision classification, then hard
stops. Later model aggregation and P00 transfer require new plans.

