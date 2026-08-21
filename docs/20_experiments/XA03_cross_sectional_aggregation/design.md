# XA03 cross-sectional rolling aggregation design

Program charter: [XA03 cross-sectional aggregation program v1](../../46_xa03_cross_sectional_aggregation_program_v1.md).

## 1. Purpose and evidence boundary

XA03 compares five increasingly flexible cross-sectional ranking layers on a
single causal contract:

- D0 direct atomic ranks;
- S1 fitted single-factor models;
- A0 transparent factor aggregates;
- A1 fitted factor-only aggregates;
- A2 fitted factor-plus-state aggregates.

XA01 and XA02 are observed research parents, not clean holdouts. XA03 is a
full-history causal/prequential experiment. It asks whether a prediction made
at each historical signal close by a model trained only on already-matured
labels would have improved the subsequent cross-sectional ranking and
long-only portfolio. It does not claim that later human selection among the
complete paths is external validation.

The experiment authorizes target warm-up construction, common-universe direct
signals, deterministic aggregation, Ridge, restricted LightGBM, market-state
interactions, and long-only backtests. It forbids new factor acquisition,
factor formula or window revision, target search, model-family additions,
state-window search, sector neutralization, covariance optimization, position
weight optimization, bagging, stacking, P00, exposure timing, a lockbox, and
automatic continuation after XA03E.

Before a formal run, a machine program, exact registries, dependency hashes,
run IDs, and an immutable preregistration lock must encode this design. Any
ambiguity discovered during implementation stops the run and is resolved in a
versioned amendment before results are generated.

## 2. Immutable parents

The runner must verify, at minimum:

- the XA01 preregistration lock, runtime and publication manifests;
- the XA01 weekly/monthly factor-value artifact and existing target ledger;
- the XA02 preregistration lock and XA02A-D manifests;
- the XA02 daily raw market-state artifact and state registry;
- the certified cross-sectional market/SEC bundle and its direct price,
  membership, calendar, corporate-action, benchmark, risk-free, and factor
  hashes;
- the accepted R10A RSP daily artifact and its manifests;
- the Git commit, dirty flag, code hashes, Python/package versions, and
  deterministic LightGBM identity.

XA01 and XA02 files are immutable. New warm-up labels, common-universe paths,
and predictions are additive XA03 artifacts and must never repair or overwrite
their parents.

## 3. Sample, calendars, and label extension

### 3.1 History and evaluation

- factor/state history begins from the existing certified 2013 history;
- XA03 adds weekly and monthly target rows beginning at the first eligible
  2014 signal and continuing through the last pre-evaluation signal;
- first evaluated signal close remains 2017-12-29;
- first evaluated execution remains 2018-01-02 open;
- final valuation remains 2026-06-30 close;
- the fixed mature-model view begins 2022-01-01 and ends with the same terminal
  date.

The 2022 view is a slice of predictions that have been generated continuously
since 2018. Models may not be restarted, reselected, or retrospectively
retrained at 2022. It is a stability view, not a lockbox or a replacement
evaluation period.

Weekly signals are the last XNYS session of each trading week. Monthly signals
are the last XNYS session of each calendar month. Both execute at the next
XNYS open. Weekly and monthly labels, models, paths, inference families, and
decisions remain separate.

### 3.2 Next-rebalance target

For stock `i`, signal date `t`, and frequency `f`, first reproduce XA01's
total-return-open execution return and half-open cash accrual:

`R(i,t,f) = tr_open(i,label_end_execution) / tr_open(i,execution) - 1`,

`C(t,f) = product(1 + rf_return[d], execution <= d < label_end_execution) - 1`.

Cash subtraction is constant inside a date and does not alter a cross-
sectional order. The model target is therefore

`Y_RANK(i,t,f) = 2 * (average_rank_t(R(i,t,f)) - 1) / (n_t - 1) - 1`.

Ranks are ascending in realized return, so the lowest return maps toward -1
and the highest toward +1. They use only target-valid members of the XA03
common universe on that signal date. Ties use average ranks. Target costs are
zero. A terminal partial holding
interval may contribute to terminal NAV under the existing engine but cannot
form a training or evaluation target.

Every label records signal close, first execution, label-end execution, and
`target_available_at`. A label may train a model only when
`target_available_at <= prediction_signal_close` under explicit timestamps.
An outcome available at the execution open is available by the same session's
close; date-only storage must reconstruct that ordering rather than guess it.
Future membership, future delistings, unavailable corporate actions, and the
current model's OOS target are forbidden.

On dates overlapping XA01, the new ledger must reproduce XA01's raw forward
return, cash return, excess return, validity and timestamp fields exactly before
any model batch opens. XA03 then recomputes the rank inside `COMMON10_OF_14`, so
its rank target is allowed—and expected—to differ from XA01's factor-specific
rank. The old target ledger remains byte-identical.

## 4. Common universe and feature representation

### 4.1 Primary universe

At signal close, the primary universe contains securities that:

1. are point-in-time S&P 500 members;
2. have a stable SID;
3. have at least 10 of the 14 registered rows with `eligible=true` and finite
   `percentile` at that close.

The `10/14` gate is known at the signal close. It may not use target validity,
the next-open price, or future execution success to decide whether a stock
receives a prediction. Ex-post target validity affects training/evaluation
rows only. Entry/exit failures follow the frozen XA01 execution accounting and
are recorded in the execution ledger; they never trigger retrospective
re-ranking or deletion from the prediction universe.

A strict fourteen-factor complete-case universe is a published coverage
diagnostic but may not become the primary universe. It begins too small and
changes materially with SEC coverage, which would mix a factor/model result
with a changing-issuer-coverage result.

All new D0-S1-A0-A1-A2 paths use the equal-weight portfolio of the same common
eligible universe as the primary active control. The control is rebalanced on
the same execution calendar and pays the same registered cost scenario on its
own actual L1 turnover. Full PIT equal weight, SPY, T-bill/cash, G00, and the
immutable XA01 factor-specific paths are secondary context only.

### 4.2 Factor representation and missingness

Each finite XA01 factor percentile `p` becomes `x = 2p - 1`. A missing
aggregate feature becomes the neutral value zero. Missingness indicators,
number of available factors, filing coverage, CIK coverage, membership age,
and data-quality reason codes are published diagnostics and may not enter a
candidate model.

D0 ranks only finite observations of its focal factor; an unavailable focal
score is never promoted by an arbitrary neutral-value tie. S1 trains and
predicts only stocks with its focal factor finite. D0 and its S1 children still
use the common-universe EW control, so their incremental comparison has the
same benchmark and the same focal-factor availability.

A0, A1, and A2 use the common universe with neutral missing-factor values. The
runner must report selected-stock factor coverage, factor-specific missing
shares, and performance by coverage count. A process driven primarily by
coverage drift is visible but cannot be relabeled as factor alpha.

## 5. Frozen factor and state bundles

### 5.1 Factor bundles

`ROLE5` is:

1. `XS002_MOM_12_1`;
2. `XS003_MOM_12_7`;
3. `XS008_SAME_MONTH_5Y`;
4. `XS041_ASSET_GROWTH`;
5. `XS056_CFO_ACCRUALS_PT`.

The bundle contains the two XA02 conditional-sign candidates and the three
factors represented among XA02 broad-static roles. ROLE5 is a result-informed
research bundle and must be described as such; it is not independently
selected inside the walk-forward.

`ALL14` is the exact XA01 factor registry. The empirically redundant
`XS001_MOM_255_0` and `XS002_MOM_12_1` both remain. Regularization and tree
capacity, not an unregistered post-result deletion, determine their joint use.

### 5.2 State bundles

`S2_WITH_RSP` contains:

- `MKT_BREADTH_RSP63`;
- `MKT_TREND126`.

`S2_NO_RSP` contains only `MKT_TREND126` and is referred to as the
`trend-only` control.

`S6_WITH_RSP` contains all XA02 primary states:

- `MKT_TREND126`;
- `MKT_LOG_RV21`;
- `MKT_DD252_SEVERITY`;
- `MKT_BREADTH_RSP63`;
- `MKT_XS_DISP21`;
- `MKT_AVG_CORR63`.

`S5_NO_RSP` removes only `MKT_BREADTH_RSP63` from S6. Every with-RSP process has
one exact no-RSP twin with the same factor bundle, algorithm, frequency,
training dates, refit timestamps, recipe selection protocol, portfolio grid,
and costs.

XA02 raw state formulas are reused. Raw state values are observable at signal
close and jointly finite from the pre-evaluation warm-up. Within every fit,
each raw state uses linear-interpolation 1st/99th percentiles from unique
training dates. Training, validation, and prediction values are clipped to
those training bounds, then standardized by the arithmetic mean and population
standard deviation (`ddof=0`) of clipped unique training dates, not repeated
stock rows. A zero or non-finite training standard deviation makes that
process-fit invalid. Every inner fold refits this transform from only its own
training dates.

XA02 shadow states and registered two-dimensional state pairs do not enter
XA03. No robust XA02 two-dimensional context existed, so a state-state-factor
interaction is outside scope.

## 6. Exact process matrix

The matrix contains exactly 57 prediction processes per frequency.

### 6.1 D0: direct factors, 14 per frequency

One common-universe direct-rank process is run for every factor in the XA01
registry. These are new harmonized baselines. Their rankings are audited
against XA01 wherever the eligible sets coincide, while their common-universe
portfolios are not falsely claimed to be XA01-identical.

### 6.2 S1: fitted single-factor models, 28 per frequency

Each of the fourteen factors receives:

- one rolling Ridge process;
- one rolling shallow-LightGBM process.

Its fixed parent is the matching D0 factor at the same frequency.

### 6.3 A0: transparent aggregation, 3 per frequency

- `A0_ROLE5_EQUAL`: arithmetic mean of the five centered ROLE5 ranks;
- `A0_ALL14_EQUAL`: arithmetic mean of all fourteen centered ranks;
- `A0_ALL14_FAMILY_BALANCED`: mean within each of the six XA01 mechanism
  dimensions followed by an equal mean across dimensions.

Fixed counts, not available-factor denominators, are used after neutral zero
imputation. Thus missingness does not mechanically increase the weight of a
remaining factor or dimension.

### 6.4 A1: factor-only rolling aggregation, 4 per frequency

- ROLE5 Ridge;
- ROLE5 LightGBM;
- ALL14 Ridge;
- ALL14 LightGBM.

The fixed A0 parent is ROLE5 equal for ROLE5 children and ALL14
family-balanced for ALL14 children. ALL14 equal remains a secondary transparent
comparison.

### 6.5 A2: factor-plus-state rolling aggregation, 8 per frequency

The authorized matrix is exactly:

| Factor bundle | State bundle | RSP role | Algorithms |
|---|---|---|---|
| ROLE5 | S2_WITH_RSP | candidate | Ridge, LightGBM |
| ROLE5 | S2_NO_RSP/trend-only | mechanism control | Ridge, LightGBM |
| ALL14 | S6_WITH_RSP | candidate | Ridge, LightGBM |
| ALL14 | S5_NO_RSP | mechanism control | Ridge, LightGBM |

ROLE5 is not crossed with S6 and ALL14 is not crossed with S2. Any such run is
an unregistered path. The four no-RSP paths per frequency are controls: they
cannot become champions or advance alone.

### 6.6 Counts

Per frequency:

- D0: 14;
- S1: 28;
- A0: 3;
- A1: 4;
- A2 with RSP: 4;
- A2 no RSP: 4;
- total: 57.

Across weekly and monthly frequencies there are 114 prediction processes.
Top5/10/20/50 create 456 signal paths. Reporting 0/5/10/20 bps creates 1,824
path-cost results without changing predictions or creating extra model paths.

## 7. Walk-forward training and refits

### 7.1 Outer prequential fit

For every prediction signal:

1. determine the latest scheduled monthly-signal-close refit at or before that
   signal;
2. collect completed labels whose availability precedes that refit signal
   close;
3. retain at most the most recent 260 weekly or 60 monthly scheduled label
   dates;
4. require at least 156 weekly or 36 monthly usable dates and at least 100
   finite target/prediction names per date;
5. fit all preprocessing and the selected recipe on those dates only;
6. freeze that fit until the next monthly refit;
7. apply it to current causal factor/state inputs and rank predictions inside
   the current common universe.

Both frequency-specific models refit at every scheduled monthly signal close.
The monthly process is scored at that anchor. If the anchor is also a scheduled
weekly signal, the weekly process uses the new fit on the same close; otherwise
the new weekly fit first scores the next scheduled weekly signal. Weekly
decisions then carry that fit until the next monthly-close refit. A fit may
score its anchor because that signal's target is not included; all training
labels were already available by the close. Weekly inputs continue to update
at every weekly decision even when model parameters remain frozen.

Before 260/60 complete dates first exist, the window expands from all available
complete dates once the 156/36 minimum is met. Thereafter it contains exactly
the most recent 260/60 complete scheduled-label dates. Calendar-year or
calendar-day lookbacks, a permanently expanding alternative, three-year
memory, random split, window search, refit-frequency search, and frequency-
pooled models are not authorized.

### 7.2 Date weights and pseudo-replication firewall

Model estimation may use stock rows, because the prediction is cross-
sectional, but every training signal date receives total sample weight one:
each usable stock for that process/date receives `1/n_t`. State transforms are fitted on
unique dates. Inner selection is evaluated from one cross-sectional RankIC per
validation date. Final inference uses one RankIC and one realized portfolio
return per signal date.

No stock-level standard error, random stock fold, pooled all-row Spearman, or
nominal sample size of `dates x stocks` may support qualification. A state
split containing thousands of stock rows but only a few dates is not treated
as a well-supported market regime.

### 7.3 Inner recipe selection

Once per execution calendar year, before that year's first OOS signal, recipe
selection is run inside the then-available trailing training window:

- the year key is the calendar year of the first execution open, so the
  2017-12-29 signal belongs to execution year 2018 and does not trigger a
  separate 2017 selection;
- validation blocks are chronological and contain 26 weekly or 6 monthly
  signal dates;
- the earliest inner training segment contains at least 104 weekly or 24
  monthly dates;
- at least three estimable validation blocks are required;
- at each boundary, inner training labels must be available before the first
  validation signal close;
- all imputation, state transforms, interaction construction, and model fitting
  are repeated from the inner training segment;
- the selection score is the mean of validation-date Spearman RankIC values;
- one-SE compares each recipe with the highest-mean-IC recipe using the
  chronological date series `best IC - candidate IC`; a non-circular
  moving-block bootstrap uses 13 weekly or 3 monthly dates, 5,000 draws, and
  the stable uint32 seed formed from the first eight SHA256 hex digits of
  `20260821|inner_one_se|process_id|frequency|execution_year`;
- an exact tie for highest mean IC chooses the lower `capacity_rank`, then
  lexicographically smaller `recipe_id`, as the reference best;
- each draw samples all possible contiguous blocks with replacement,
  concatenates and truncates to the original number of validation dates, and
  the sample standard deviation (`ddof=1`) of bootstrap mean differences is
  the SE;
- a candidate enters the one-SE set when
  `mean(best - candidate) <= SE(best - candidate)`; the selected member has
  the lowest `capacity_rank`, then lexicographically smallest `recipe_id`.

The selected recipe is frozen for the full execution year while its model
parameters continue to refit monthly. Every A2 state process inherits the
selected recipe of its matching A1 factor-only process; it may not run a second
state-informed recipe selection. Its no-RSP twin inherits the same recipe.

If the selector is not estimable, that process/year is invalid. It is not
silently filled by a different family, an older winner, a full-sample choice,
or a future selection.

## 8. Model families and state interactions

### 8.1 Ridge

Ridge uses a squared-error objective, a fitted intercept, signed coefficients,
and scikit-learn's deterministic `cholesky` solver (`tol=1e-8`,
`max_iter=None`). The registered alpha grid is `{0.1, 1, 10, 100}` under the date-
normalized sample weights. Capacity ordering treats stronger regularization as
simpler: `100, 10, 1, 0.1`.

S1 and A1 use centered factor ranks. A2 Ridge models add the following frozen
explicit interactions to the factor main effects:

- ROLE5+S2 uses linear and quadratic
  `XS002_MOM_12_1 x MKT_BREADTH_RSP63` terms and linear and quadratic
  `XS008_SAME_MONTH_5Y x MKT_TREND126` terms;
- its trend-only no-RSP twin removes both breadth interaction terms and keeps
  both seasonality-by-trend terms;
- ALL14+S6 uses one linear factor-by-state interaction for every registered
  factor/state pair;
- its S5_NO_RSP twin removes every interaction involving
  `MKT_BREADTH_RSP63` and changes nothing else.

For a quadratic ROLE5 term, the standardized state's squared value is centered
by its training-date mean before multiplication. No tercile dummy, hinge,
spline, state main effect, factor-factor term, or state-state term is authorized
in Ridge. State main effects are omitted because they are identical for all
stocks on a date and cannot affect the cross-sectional order.

No positivity constraint is used. XA02 found conditional sign changes, so a
global positive-coefficient constraint would prejudge the A2 question. Ridge
coefficients, interaction coefficients, selected alpha, and refit-to-refit
coefficient drift are published.

### 8.2 LightGBM

LightGBM uses continuous centered factor ranks and the train-only standardized
continuous state values. Missing factors are neutral zeros before fitting, so
native missing-value branches cannot become an undeclared coverage model.

The recipe grid is restricted to:

- regression L2 objective;
- `max_depth=2`, `num_leaves=4`;
- `n_estimators in {50, 100}`;
- `learning_rate=0.05`;
- `reg_lambda=1`;
- `subsample=1`, `colsample_bytree=1`;
- no monotone constraints;
- fixed seed, one thread, deterministic/force-column-wise execution.

Fifty trees are simpler than one hundred under the one-SE selector. The exact
LightGBM version, wheel/metadata and binary hashes, and deterministic prediction
hash must be locked before execution.

Depth two permits a factor-factor split or a state split followed by a factor
split. It cannot encode a three-way state-state-factor rule. Every terminal
leaf must be audited for distinct training-date and calendar-year support; the
machine configuration must fail a recipe whose leaf support is below 26 weekly
or 12 monthly dates or below two calendar years. Row-count support alone is
insufficient.

If the annually selected recipe fails fitting or leaf-support audit at any
outer monthly refit, the entire process-frequency path is `invalid` and enters
its registered inferential families with `p=1`. The runner may not switch to a
different recipe, carry an older fit, delete the interval, or reinterpret the
annual selection.

### 8.3 Meaning of an A2 model

A market state is constant across stocks at a given signal date. Appending a
state main effect to a linear cross-sectional score cannot change any rank.
XA03 therefore defines state value strictly as a change in the mapping from
stock factors to expected rank:

`prediction(i,t) = base_factor_mapping(x_i,t) + factor_state_interactions(x_i,t, s_t)`.

Ridge implements the interaction explicitly. LightGBM may implement it through
a two-level tree. A fitted state coefficient, feature importance, or split is
not sufficient evidence; only prequential predictions and their paired A1
increment establish incremental value.

## 9. Portfolio construction and metrics

Every process ranks higher predictions first and resolves exact ties by SID
ascending. Top5, Top10, Top20, and Top50 are equal-weight long-only. There is no
short leg, beta hedge, industry neutrality, score-proportional weight, risk
parity, or cash timing.

The cost grid is 0, 5, 10, and 20 bps on actual L1 turnover at execution. Costs
do not train the target or change a prediction. Top20 is primary; 10 bps is the
weekly primary cost and 5 bps the monthly primary cost. Top50 retains the XA01
G00 provenance warning where relevant, but new XA03 rankings receive their own
deterministic and nested-TopK audits.

For every signal and portfolio period, the runtime ledger records at least:

- process, class, factor/state bundle, algorithm, frequency, recipe and refit;
- signal, execution, label-end and target-availability timestamps;
- training start/end, date count, stock-row count and latest mature label;
- prediction rank, holdings, weights, eligible count and factor coverage;
- factor and common-EW returns, arithmetic active return and relative log
  return;
- turnover, transaction cost, RankIC, hit rate and concentration;
- selected names, sectors when available, and individual contribution.

Path summaries include CAGR/annualized mean, volatility, Sharpe, maximum
drawdown, active IR, active hit rate, relative wealth, tail return, turnover,
cost, effective number of holdings, maximum name/sector weight and contribution,
and RankIC mean/median/distribution.

Stability views are fixed to:

- 2018-2021;
- 2022-2026H1;
- the complete 2018-2026H1 path;
- calendar year and calendar quarter;
- trailing 26/52/104 completed weekly periods;
- trailing 12/24/36 completed monthly periods;
- the fixed 2022 mature-model slice.

Rolling and calendar views are diagnostics and may not become model inputs or
post-result strategy selectors.

## 10. Inference families and fixed comparisons

The statistical unit is one scheduled date. Inference uses NumPy
`default_rng`/PCG64 and 5,000 circular moving-block draws with blocks of 13
weekly or 3 monthly periods. For each frequency/outcome/family, the uint32 seed
is the first eight SHA256 hex digits of
`20260821|outer_inference|frequency|outcome|family`; every registered member of
that family shares the same sampled indices. Each draw samples circular
contiguous blocks with replacement, concatenates and truncates to the complete
scheduled-calendar length. Missing outcomes remain in place with zero weight;
an all-zero-weight draw has statistic zero. The one-sided p-value is
`(1 + count(bootstrap weighted mean <= 0)) / (5000 + 1)`.

BH uses the standard step-up rule with the registered denominator, stable ties
by comparison/process ID, and
`q_i = min_{j>=i}(min(1, m*p_(j)/j))`. A registered but statistically
inestimable member receives `p=1`; an input or causality failure stops the
batch rather than receiving a convenient p-value.

BH correction at `q <= 0.10` is separate by frequency, outcome, and comparison
family. Thus each frequency has six fixed ledgers: economic and RankIC versions
of the 53-member absolute, 39-member parent-child, and 4-member RSP families.
The economic families control qualification; RankIC families are diagnostic
and cannot rescue an economic failure. Stronger `q <= 0.05` evidence is
reported but does not create a new path. The fixed family membership is:

### 10.1 Main absolute family: 53 per frequency

- 14 D0;
- 28 S1;
- 3 A0;
- 4 A1;
- 4 with-RSP A2.

Each is assessed versus zero RankIC and versus the common-universe EW control
on its primary-cost Top20 active/relative outcome. The four no-RSP A2 controls
are not members of this champion family and cannot qualify for advancement.
They are not inserted as artificial `p=1` hypotheses. A `p=1` placeholder is
used only for a path that belongs to the registered family but fails a required
sample/estimability gate.

### 10.2 Parent-child incremental family: 39 per frequency

- 28 S1 minus its matching D0;
- 3 A0 minus D0 `XS003_MOM_12_7` at the same frequency;
- 4 A1 minus its fixed A0 parent;
- 4 with-RSP A2 minus its matching A1 factor-only parent.

All differences are paired on common signal dates. The three A0 comparisons
use the frozen XA01/XA02 trend representative rather than an ex-post best D0.
ROLE5 A1 uses ROLE5 equal as parent. ALL14 A1 uses ALL14 family-balanced as
parent. An A2 parent has the same factor bundle, algorithm and frequency in A1.

### 10.3 RSP incremental family: 4 per frequency

- ROLE5+S2 Ridge minus ROLE5+trend-only Ridge;
- ROLE5+S2 LightGBM minus ROLE5+trend-only LightGBM;
- ALL14+S6 Ridge minus ALL14+S5_NO_RSP Ridge;
- ALL14+S6 LightGBM minus ALL14+S5_NO_RSP LightGBM.

This family answers whether RSP breadth adds information after holding model
capacity and all other inputs fixed. It does not decide whether P00 will add
value; P00 is not run.

Primary predictive inference uses paired differences in date-level RankIC.
Primary economic inference uses paired differences in Top20 primary-cost
relative log return. TopK, 20-bps, mature-slice, subperiod, concentration, and
coefficient/importance views are registered stability checks, not extra
winner-selecting p-values.

For any candidate/reference pair, the period endpoint is
`log1p(candidate net return) - log1p(reference net return)`. Its annualized
increment is the mean times 52 weekly or 12 monthly; terminal relative wealth
is `exp(sum(period increments))-1`. Active IR uses arithmetic candidate-minus-
reference returns, sample standard deviation (`ddof=1`), and the same square-
root annualization. A return at or below -100% makes the path invalid. Calendar
years and the two fixed subperiods are keyed by execution open. Leave-one-year-
out covers every evaluation execution year with at least one complete period;
year concentration is `abs(year log sum)/sum(abs(all year log sums))`, and a
zero denominator fails that gate.

## 11. Qualification tags and roles

Qualification has no count cap. Every path receives exactly one primary status
with precedence `invalid`, `qualified_incremental`,
`qualified_absolute_only`, `not_qualified`, plus zero or more non-exclusive
evidence tags.

The absolute economic gate requires all of: annualized Top20 primary-cost
relative-log return versus common EW of at least 2%, one-sided economic
`q <= 0.10`, terminal candidate/common-EW wealth ratio strictly above 1 and
positive active IR, positive mean
date RankIC, matching direction at 20 bps, positive direction in at least two
of Top10/20/50 including Top20, leave-one-year-out positive direction in at
least 75% of years, and no year contributing more than half of total absolute
yearly relative-log contribution. RankIC `q <= 0.10` is reported separately
but is not required for this economic status.

An incremental child must first pass that absolute gate and then pass the same
width, cost, year and concentration checks on its paired increment, with
annualized increment of at least 2%, paired economic `q <= 0.10`, and mean
RankIC increment no worse than -0.005. This is `qualified_incremental`; an
absolute-qualified path without a passing registered parent comparison is
`qualified_absolute_only`.

- `absolute_qualified`: positive RankIC and positive primary-cost Top20
  relative performance satisfy the registered absolute evidence/effect gates;
- `incremental_qualified`: an S1, A0 or A1 child passes its paired fixed-parent
  gate without a material reversal in the other primary outcome;
- `state_incremental_qualified`: a with-RSP A2 process passes its paired A1
  factor-only gate;
- `rsp_incremental_supported`: a with-RSP A2 process passes its exact no-RSP
  paired gate;
- `broadly_robust`: the absolute gate passes, both fixed subperiod relative-log
  increments are strictly positive, and the yearly contribution gate passes;
- `conditional_specialist`: the absolute gate passes and the two fixed
  subperiod relative-log increments have strictly opposite signs; state
  concentration remains a description and cannot automatically assign this
  tag;
- `predictive_only`: RankIC qualifies but the primary economic gate does not;
- `exploratory_unstable`: nominal evidence exists but FDR, cost, TopK,
  concentration, or year-stability checks fail;
- `not_qualified`: no registered qualification route passes;
- `invalid`: input, causality, target, fold, selector, model, prediction,
  ranking, accounting, or sample gates fail.

`predictive_only` requires positive RankIC with diagnostic RankIC
`q <= 0.10`; it cannot change a failed economic primary status. For the RSP
ablation, `rsp_incremental_supported` requires positive economic and RankIC
increments, economic `q <= 0.10`, matching 20-bps direction, and positive
direction in at least two of Top10/20/50 including Top20. A negative Top20
primary-cost annualized RSP increment receives the descriptive
`rsp_harm_point_estimate` tag; the tag itself is not a significance claim.

`exploratory_unstable` requires raw one-sided economic or RankIC `p <= 0.05`
while the relevant BH or registered stability gate fails; it is not assigned
from visual interest alone.

`broadly_robust` is a label, not a universal hard gate. A conditional strategy
is scientifically meaningful in this program and is not discarded solely
because 2018-2021 and 2022-2026H1 have opposite signs. It must remain visibly
conditional and cannot be promoted under a broad-stability claim.

For an economic advancement role, Top20 primary relative wealth and active IR
must be positive. Top10/20/50 are all evaluated at that frequency's primary
cost; at least two widths including Top20 must retain the direction. The
separate 20-bps gate compares Top20 at 20 bps with Top20 at primary cost, and
leave-one-year-out uses Top20 at primary cost.
Top5 remains a concentration stress test and is not counted in this width gate.
No single year may contribute more than half of the sum of absolute yearly
relative-log contributions. A model that improves one primary outcome but
materially worsens the other is a trade-off, not an incremental winner.

No-RSP A2 controls may receive descriptive absolute/stability metrics and an
RSP-parent role, but cannot receive an advancement role on their own in XA03.
They are preserved for mechanism attribution and a later P00 factorial test.

## 12. Batch sequence and products

### XA03A: target, common panel, calendars, and controls

XA03A verifies every parent and lock, builds the additive 2014-2017 weekly and
monthly target extension, verifies overlap with XA01, freezes the common
universe and centered feature matrix, joins raw causal states, and materializes
monthly refit/inner-block ledgers.

Required runtime outputs include:

- `extended_target_ledger.parquet`;
- `common_universe_ledger.parquet`;
- `model_feature_panel.parquet`;
- `state_feature_panel.parquet`;
- `refit_ledger.parquet`;
- `inner_fold_ledger.parquet`;
- target, universe, feature, state and future-perturbation causality audits;
- parent/hash/overlap identity tables;
- manifest and summary.

XA03A hard-fails if the first 2018 predictions cannot meet the minimum training
history, if common-universe coverage falls below the registered gate, or if a
future price, filing, membership change, split, state, or label can alter an
earlier feature/fold.

### XA03B: D0 and S1

XA03B produces all 14 D0 and 28 S1 processes per frequency, including
prequential predictions, monthly model selections/refits, coefficient/tree
audits, Top-K holdings, period returns, daily NAV, and fixed parent-child
comparisons. D0 must be complete before S1 results are interpreted.

### XA03C: A0 and A1

XA03C produces the three A0 and four A1 processes per frequency. It verifies
ROLE5/ALL14 membership, family-balanced weights, neutral missing treatment,
factor-only input exclusion of every market state, and exact A1-to-A0 parent
mapping.

### XA03D: A2 and mandatory RSP ablations

XA03D produces exactly eight A2 processes per frequency. It verifies explicit
Ridge interactions, LightGBM depth/leaf/date support, state observability,
identical with/no-RSP twins, and exclusion of shadow/two-dimensional states.
It emits paired A2-A1 and with-RSP/no-RSP ledgers before any role is assigned.

### XA03E: unified assessment and hard stop

XA03E assembles all 114 prediction processes and 456 Top-K paths, verifies
1,824 cost results, runs the fixed inference families, produces stability and
concentration views, assigns evidence roles, and writes a decision ledger.

Required closure outputs include at least:

- `process_registry_resolved.csv`;
- `path_summary.csv` and `path_cost_summary.csv`;
- `absolute_assessment.csv`;
- `parent_child_incremental_assessment.csv`;
- `rsp_incremental_assessment.csv`;
- `subperiod_and_mature_slice.csv`;
- `calendar_and_rolling_performance.csv`;
- `coefficient_and_importance_stability.csv`;
- `coverage_and_concentration_audit.csv`;
- `qualification_role_ledger.csv`;
- `decision.json`, manifest, audit summary, report and figures.

Daily predictions, target panels, holdings, NAV and large Parquet ledgers stay
in the runtime bundle. Git publication contains compact summaries, roles,
manifests, reports and figures with hashes back to runtime evidence.

## 13. Mandatory audits

The read-only auditor must independently prove:

- all parent and preregistration hashes match;
- Git was clean at execution and code/config hashes match the manifest;
- exactly 57 processes per frequency and 114 total exist;
- D0/S1/A0/A1/A2 counts and parent links match the frozen registry;
- there are exactly four with-RSP/no-RSP pairs per frequency;
- exactly 456 Top-K paths and 1,824 path-cost rows exist, subject only to
  explicitly invalid paths that remain represented;
- no 2014-2017 target overwrote XA01 and overlap identities pass;
- no training target is unavailable at the refit signal close;
- future perturbations cannot change earlier universe, features, states,
  transforms, folds, recipes, predictions, holdings or NAV;
- missingness indicators and coverage counts are absent from model matrices;
- frequency calendars, nested Top-K, tie-breaks, turnover, costs and daily P&L
  close exactly;
- statistical tests use dates, not stock rows, and BH family counts are 53,
  39 and 4 per frequency as registered;
- no-RSP controls are outside the champion family and cannot advance;
- P00, exposure timing, bagging, stacking, strategy selection and lockbox reads
  are all false.

Any failed audit makes the affected process or batch invalid. Counts are not
silently repaired by dropping failures.

## 14. Hard stop and future P00 interface

XA03E always hard-stops. It may identify zero, one, or many direct,
single-factor, transparent, factor-only, state-aware, broad, or conditional
candidates. It may not select a final production strategy or combine candidate
predictions.

If a later user decision authorizes P00, the new plan must preserve an exact
2x2 factorial comparison for every selected A2 family:

| Model information | P00 off | P00 on |
|---|---|---|
| no-RSP A2 twin | required | required |
| with-RSP A2 candidate | required | required |

This separates alpha-model use of RSP breadth, P00 exposure timing, and their
interaction. The later plan must freeze exposure rules, matched-static
controls, cost/accounting treatment and candidate count before any cell runs.
XA03 creates no P00 prediction, holding, exposure, threshold, NAV, or result.
