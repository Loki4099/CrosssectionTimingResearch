# XA02 factor performance and market-state atlas design

## Purpose and evidence boundary

XA02 reconstructs the complete performance history of every XA01 factor and
describes factor performance conditional on market information known at each
signal close. It is a post-XA01, full-history causal/prequential exploration.
It is not an independent confirmation and does not open a lockbox.

The program forbids fitted prediction models, factor aggregation, strategy
selection, a discrete market-state classifier, target revision, P00 transfer,
external acquisition, and any state window or threshold search. Historical
XA01 artifacts are immutable parents and must not be overwritten.

## Batch sequence

### XA02A: complete factor-path ledger

XA02A verifies the XA01 lock, runtime manifest, published manifest, factor
table, target ledger, the certified market/SEC runtime bundle and its direct
price/calendar/membership/benchmark hashes, plus the accepted R10A feature
artifact. It deterministically
replays all 112 signal paths and 448 factor cost paths, plus their
factor-specific eligible-EW controls.

For every holding period it records signal, entry and exit timestamps; factor
and control returns; arithmetic active return; relative log return; RankIC;
turnover; cost; eligible count; tie rate; selected names and weights. It stores
daily NAV and holdings only in the runtime bundle. Replayed summaries and
metrics must reproduce XA01 before later batches may open. XA01 did not publish
a complete parent ranking ledger, so new rankings receive deterministic and
nested-TopK audits rather than a false parent-identity claim.

Factor and eligible-EW holding-period returns are both net of the same cost
scenario. RankIC uses cost-free next-period stock returns and average ranks on
at least 100 common finite names. Weekly/monthly annualization constants are
52/12; active IR is period-mean divided by sample standard deviation and then
annualized. Relative wealth is `exp(cumsum(relative_log_return))`. A state's
wealth contribution is the raw sum of its relative log returns, not a ratio to
a potentially near-zero full-path result. Tail mean uses the full path's fixed
10% active-return quantile.

The G00 Top50 identity exception is carried as parent provenance. Top50 cannot
be primary XA02 evidence. The missing XA01 XS056 twelve-month diagnostic may be
recomputed only as an additive `xa02_repair` artifact; XA01 files remain
unchanged.

### XA02B: causal market-context features

XA02B materializes the six primary and three shadow states in the frozen state
registry. SPY inputs come from the certified cross-sectional market bundle.
RSP comes only from the accepted Round10 R10A daily artifact and is used as a
continuous investable breadth proxy; no P00 threshold or state is inherited.

Every raw state is observable at the signal close. Its causal percentile and
tercile use only strictly earlier daily state observations, with 756 valid
history sessions. No full-sample threshold, future return, future drawdown,
named crisis, or calendar period may define a state.

The reference distribution expands from 2013 and contains every finite daily
raw-state value strictly before the signal close; 756 is a minimum count, not
a rolling-window length. The causal percentile is the empirical midrank
`(count_less + 0.5 * count_equal) / n`. Low includes values at or below 1/3,
mid is above 1/3 through 2/3, and high is above 2/3. A missing current value or
fewer than 756 finite prior observations produces a missing bin, never a
backfill.

`MKT_XS_DISP21` requires at least 200 current PIT members and uses
`1.4826 * MAD` of their trailing 21-session total returns. `MKT_AVG_CORR63`
requires at least 200 valid members, 50 common return observations per pair,
and 10,000 valid pairs. Current membership is applied at each historical date;
today's constituents may not be backfilled.

Shadow states receive the same causality and coverage audit but cannot create
qualification p-values or automatic XA03 input suggestions.

The shadow share-above-SMA200 state reconstructs a causal ex-dividend,
split-adjusted close from frozen `raw_close` and split events known through the
signal close. For session `u`,
`Csa_u = raw_close_u * product(s_v for history_start <= v <= u)`, where `s_v`
is the explicitly recorded positive split ratio and an explicit no-split event
contributes one. A missing split field beside a non-missing close invalidates
the causal series from that point; it is never guessed. SMA200 requires 200
consecutive finite XNYS-session values through `t`. No nonexistent pre-adjusted
field or future split may be used. A required causality test perturbs every
split event after a historical state date and proves that date's causal close,
SMA and bin are unchanged.

An episode is a maximal consecutive run of the same bin on that frequency's
scheduled signal sequence. Missing bins break an episode. Joint-state episodes
use the same rule, and a middle/missing state breaks a formal 2x2 corner
episode. State-year counts use the signal-close year; the state governs the
following next-open-to-next-scheduled-open holding period.

### XA02C: one- and two-dimensional atlas

The one-dimensional atlas reports every one of the
`14 factors x 2 frequencies x 6 primary states` cells. It must publish failed,
null and insufficient-sample relationships alongside positive ones.

Within each causal tercile it reports observations, years and contiguous state
episodes; factor and control returns; active return and IR; tail outcomes;
RankIC; hit rate; turnover, cost, universe size and contribution to relative
log wealth. Conditional MDD is calculated only inside real contiguous episodes,
never by concatenating disjoint state dates.

The primary state heterogeneity test is a low/mid/high regression with mid as
reference and a joint HAC Wald test that both state coefficients are zero. It
is run separately for next-period active return and RankIC. Weekly HAC lag is
four and monthly lag is two. A 5,000-draw moving-block bootstrap with seed
20260821, block 13 weekly periods or three monthly periods, supplies confidence
intervals. BH correction is separate by frequency, outcome, and atlas
dimension; q<=0.10 is discovery evidence and q<=0.05 is stronger evidence.

The statistical unit is one scheduled date per factor and frequency: one
active return and one cross-sectional RankIC. The roughly 500 stock rows used
inside a date's RankIC are never treated as independent market-state samples.
The one-dimensional regression also preserves the complete signal calendar;
missing state or outcome dates receive no inferential weight rather than
compressing time gaps.

HAC uses a deterministic Newey-West sandwich with Bartlett weights, no
finite-sample correction, NumPy least-squares/pseudoinverse, and a chi-square
Wald reference distribution. A rank-deficient design fails its sample gate and
enters the fixed BH family at p=1. Bootstrap intervals are 2.5/97.5 percentile
intervals from NumPy `default_rng(20260821)` (PCG64). No unregistered package
installation is part of XA02.

Only three two-dimensional atlases are authorized:

1. SPY 126-session trend x SPY 21-session volatility;
2. RSP/SPY63 breadth x SPY 21-session volatility;
3. cross-sectional return dispersion x average member correlation.

Each pair publishes the full 3x3 causal-tercile grid for interpretation. Its
formal test excludes both mid terciles and uses the four fixed low/high corner
cells in a 2x2 difference-in-differences regression. The single interaction
coefficient is tested against zero with the same HAC and block-bootstrap
controls. The regression stays on the complete signal calendar with middle
terciles assigned zero formal-test weight, preserving actual HAC spacing. Each
bootstrap draw resamples complete contiguous calendar blocks before filtering
to the four corners. This keeps the monthly test estimable without turning one sparse
nine-cell winner into a hypothesis. No other pair, three-way atlas, cell
winner, bin threshold, direction or window may be added after seeing results.

Formal one-dimensional interpretation requires every tercile to contain at
least 52 weekly or 18 monthly observations, four calendar years, and three
separate episodes. Two-dimensional tables require seven of nine supported
descriptive cells; the four formal low/high corners must each contain at least
24 weekly or eight monthly observations, with at least 120 weekly or 36
monthly corner observations in total. Every corner must span three calendar
years and three separate episodes, and no year may supply more than half a
corner. Unsupported results remain visible but cannot influence a role.

A descriptive 3x3 cell is supported at 20 weekly or six monthly observations;
this display gate produces no p-value. A formal two-dimensional relation uses
the DiD interaction coefficient: active-return qualification requires
`abs(beta_AB) * 52` weekly or `* 12` monthly to reach 3%, while RankIC requires
`abs(beta_AB) >= 0.02`; either route also requires
`abs(beta_AB) / full_path_sample_std(outcome) >= 0.25`. Leave-one-year-out,
TopK and 20 bps checks retain the original interaction sign. Replication means
the same state pair, outcome route and interaction sign appears either for the
same factor in both frequencies or in two empirically non-redundant factor
clusters.

A two-dimensional LOYO run is estimable only when every corner retains at
least 12 weekly or four monthly observations and the four corners retain at
least 60 weekly or 18 monthly observations in total. Year `y` contributes to
the DiD as `sum(HH_y)/n_HH - sum(HL_y)/n_HL - sum(LH_y)/n_LH +
sum(LL_y)/n_LL`, using full-sample cell denominators; its absolute share uses
the sum of absolute yearly contributions.

The fixed BH family never shrinks when a relationship is unsupported. A failed
sample gate is entered as p=1, leaving 84 one-dimensional or 42
two-dimensional hypotheses in every frequency-by-outcome family. The 3x3
descriptive cells never produce p-values.

### XA02D: stability, redundancy and advisory roles

XA02D evaluates two fixed time slices, leave-one-year-out direction,
single-year contribution, TopK and cost robustness. A state relationship may
be called repeatable only when at least 75% of estimable leave-one-year-out
paths retain its direction, no year supplies more than half its absolute
conditional difference, at least two widths including Top20 agree, and the
primary and 20 bps results agree in direction.

For one-dimensional roles, the qualifying outcome selects its best and worst
full-sample terciles by conditional mean, with exact ties resolved low, then
mid, then high. Those two bin identities are frozen for every leave-one-year-
out and robustness calculation; they are never reselected. An LOYO run is
estimable only if both fixed bins retain at least 26 weekly or nine monthly
observations. Its direction is the fixed-best mean minus fixed-worst mean.
Year `y` contributes
`sum(y in best)/n_best_full - sum(y in worst)/n_worst_full`; the absolute
contribution fraction divides by the sum of absolute yearly contributions.
This decomposition sums exactly to the full best-minus-worst contrast.

Factor similarity is measured in three distinct ways on common dates:

- date-level cross-sectional score Spearman correlation;
- actual TopK Jaccard and overlap, with Top20 primary;
- Pearson and Spearman correlation of next-period Top20 active returns.

The review thresholds are 0.80, 0.60 and 0.70 respectively. At least two must
be exceeded before an empirical redundancy cluster is recorded. Mechanism
labels remain visible and no factor is automatically deleted.

Advisory labels are assigned at the factor-by-frequency level and are
`broad_static`, `conditional_sign_switch`,
`conditional_strength`, `exploratory_state_candidate`,
`time_break_unexplained`, and `no_state_evidence`. Their numerical effect and
stability rules are frozen in the program. Labels summarize evidence; they do
not authorize XA03 inputs or models.

`broad_static` requires positive full-path primary relative wealth and active
IR, at least three of four TopK widths and all four registered cost scenarios
to remain positive, and no stable sign-reversing state. A conditional label
may qualify through active return or RankIC: the chosen outcome must pass its
q-value, effect floor and standardized-range floor, every stability gate, and
the other outcome may not reverse the best-versus-worst state contrast. The
standardized range divides by the full-path sample standard deviation of the
same outcome. `conditional_sign_switch` additionally requires a positive and a
negative state mean; `conditional_strength` requires the state means to retain
one sign with at least one positive state. A raw p-value at or below 0.05 that misses FDR, or an
FDR result missing one stability gate, is only `exploratory_state_candidate`.
`time_break_unexplained` requires a registered subperiod sign change that none
of the six primary states explains. A time break means strict opposite signs
between the two fixed subperiod means for active return or mean RankIC;
relative terminal wealth is not substituted. `broad_static` is vetoed only by
a one-dimensional `conditional_sign_switch` that passes every gate. RankIC evidence may support a role only
when the primary-cost active-return evidence does not point in the opposite
direction.

The `broad_static` tag also requires positive relative log wealth in both fixed
subperiods. No single calendar year may supply more than half the sum of
absolute yearly relative-log contributions.

Role tags are non-exclusive: for example, a broad factor may also have a
repeatable strength interaction. The ledger also emits one deterministic
primary role with priority `conditional_sign_switch`, `conditional_strength`,
`broad_static`, `time_break_unexplained`, `exploratory_state_candidate`, then
`no_state_evidence`.

A two-dimensional relationship is `robust_2d_context` only after its q-value,
effect-size and stability gates pass and the same relation repeats across both
frequencies or across two empirically non-redundant factor clusters. Otherwise
an FDR-positive single factor/frequency result remains
`exploratory_2d_context`; neither label authorizes a model.

## Rolling and full-path views

Backward-looking diagnostic windows are 26/52/104 completed holding periods
for weekly paths and 12/24/36 for monthly paths. Partial and centered windows
are forbidden. Rolling results are anchored to outcome availability and are
never used as contemporaneous state inputs. Calendar summaries are fixed to
calendar year and calendar quarter; they are descriptive stability views, not
market-state definitions.

Runtime outputs include at least:

- `holding_period_ledger.parquet`;
- `daily_nav_paths.parquet`;
- `topk_holdings.parquet`;
- `market_state_features.parquet`;
- rolling and calendar-period performance tables;
- one- and two-dimensional atlas and test tables;
- state-episode contributions;
- score, holdings and active-return similarity tables;
- `factor_state_role_assessment.csv`;
- immutable manifests, summaries, audit evidence and figures.

Published Git artifacts must remain compact. Daily NAV, holdings and large
Parquet ledgers stay in the runtime bundle; Git receives summaries, roles,
manifests, reports and figures with hashes back to runtime evidence.

Execution must begin from a clean committed Git state. The runner must verify
the preregistration lock byte-for-byte, refuse an existing run directory, and
record the commit, dirty flag, package versions, code hashes and direct input
hashes. Publishing likewise refuses any existing destination.
Batch roots use
`results/experiments/xa02/{batch_id}/runs/{run_id}`. Each later batch verifies
the SHA256 of every required earlier batch manifest before it starts.

## Hard stop and interface to XA03

XA02D always hard-stops. It may say which continuous contexts and interactions
deserve review, but it may not construct a combined score, train Ridge or
LightGBM, select a strategy, materialize a new model target, or invoke P00.

After user review, a new XA03 preregistration must separately freeze the exact
factor bundles, context bundle, next-rebalance cross-sectional rank target,
training memory, refit cadence, model capacity, baselines and promotion gates.

## Medium implementation handoff

Medium should implement new, non-overwriting files
`src/momentum_reversal/pipelines/xa02_experiments.py`,
`scripts/run_xa02.py`, `scripts/audit_xa02.py`, `scripts/publish_xa02.py`, and
`tests/test_xa02_experiments.py`. It must not edit any member of the XA02
preregistration lock. After implementation is committed and the worktree is
clean, execution order is XA02A, XA02B, XA02C, XA02D, read-only audit, then
compact publication. Any design defect discovered during implementation stops
the run and requires an explicit versioned amendment; it is not fixed by
silently changing a locked definition.
