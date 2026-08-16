# CrosssectionTimingResearch

An auditable research framework for S&P 500 point-in-time cross-sectional momentum, reversal, and volatility-based timing. The project keeps long-only portfolios and dollar-neutral WML as separate research arms so market beta and winner-minus-loser mechanisms are not conflated.

## Current evidence

All accepted results use one frozen free-research dataset and the common evaluation window from 2018-01-02 open through 2026-06-30 close.

- [G00 naked momentum](docs/20_experiments/G00_baseline/report.md): valid common control. Long-only momentum earns attractive returns but retains large drawdowns; WML has weak absolute performance.
- [G11 SPY-volatility continuous scaling](docs/20_experiments/G11_spy_continuous_scale/report.md) ([design](docs/20_experiments/G11_spy_continuous_scale/design.md)): failed long-only H1 at 0/18; CAGR and Sharpe fell while maximum drawdown improved in all 18 main scenarios. Long-short CAGR, Sharpe, and drawdown improved in all 18 main scenarios and all 216 registered cost/borrow scenarios, but absolute performance remained weak.
- [G12 naked-book historical-volatility continuous scaling](docs/20_experiments/G12_book_hist_continuous_scale/report.md) ([design](docs/20_experiments/G12_book_hist_continuous_scale/design.md)): failed long-only H1 at 0/18; CAGR and Sharpe fell in all 18 main scenarios, drawdown improved in 16/18, and the continuous rule over-insured the long-only book. Long-short jointly improved Sharpe and drawdown in 17/18 main and 204/216 stress scenarios, but absolute performance remained weak.
- [G13 naked-book forecast-volatility continuous scaling](docs/20_experiments/G13_book_forecast_continuous_scale/report.md) ([design](docs/20_experiments/G13_book_forecast_continuous_scale/design.md)): failed long-only H1 at 0/18; drawdown improved in all 18 main scenarios, but CAGR and Sharpe fell in all 18 as the continuous rule over-insured the book. Long-short jointly improved Sharpe and drawdown in 12/18 main and 132/216 stress scenarios, with cost/borrow sensitivity and weak absolute performance.
- [G21 SPY high-volatility reversal](docs/20_experiments/G21_spy_reversal/report.md): failed long-only negative control, but a stable left-tail improvement for weekly WML.
- [G22 naked-book historical-volatility reversal](docs/20_experiments/G22_book_hist_reversal/report.md) ([design](docs/20_experiments/G22_book_hist_reversal/design.md)): failed both preregistered platform hypotheses. Long-only jointly improved CAGR, Sharpe, and drawdown in only 4/36 main paths; long-short reached 23/36 but missed the 24/36 total and 10/18 monthly thresholds. Weekly WML retained a partial, cost-sensitive mechanism with weak absolute performance.
- [G23 naked-book forecast-volatility reversal](docs/20_experiments/G23_book_forecast_reversal/report.md) ([design](docs/20_experiments/G23_book_forecast_reversal/design.md)): completed the final main-grid cell. Long-only failed at 0/36 and worsened Sharpe and drawdown throughout; long-short passed the preregistered platform threshold at 33/36, including 15/18 monthly and 18/18 weekly paths, but remained cost/borrow-sensitive and weak in absolute performance.
- [G31 SPY high-volatility derisking](docs/20_experiments/G31_spy_derisk/report.md) ([design](docs/20_experiments/G31_spy_derisk/design.md)): improved maximum drawdown in all 18 long-only main scenarios but failed H1; the long-short mechanism was positive, with weak absolute performance.
- [G32 naked-book historical-volatility derisking](docs/20_experiments/G32_book_hist_derisk/report.md) ([design](docs/20_experiments/G32_book_hist_derisk/design.md)): failed long-only H1; CAGR and Sharpe fell in all 18 main scenarios and drawdown results were mixed. The long-short mechanism improved Sharpe and drawdown in 17/18 scenarios and survived the registered cost/borrow stresses, but absolute performance remained weak.
- [G33 naked-book forecast-volatility derisking](docs/20_experiments/G33_book_forecast_derisk/report.md) ([design](docs/20_experiments/G33_book_forecast_derisk/design.md)): failed long-only H1 at 0/18; CAGR and Sharpe fell while maximum drawdown improved in all 18 main scenarios. Long-short drawdown improvement was robust, but only 10/18 main scenarios jointly improved Sharpe and drawdown; Sharpe gains were cost/borrow-sensitive and absolute performance remained weak.
- [Systematic experiment program](docs/21_systematic_experiment_program_v2.md): the action × risk-source grid and frozen stage boundaries.
- [Round-one main-grid synthesis](docs/22_round1_main_grid_synthesis.md): consolidated economic conclusions, paper-alignment limits, and implications after completing all nine cells.
- [Round-two R2B signal diagnostics](docs/20_experiments/R2B_signal_diagnostics/report.md) and [R2C simple-stage report](docs/20_experiments/R2C_spy_tbill_timing/report.md): the free 1993–2026 core line was built and the 2005–2021 pre-lockbox walk-forward completed. Volatility/trend variables retained four-week path-risk information, but no sentinel, Ridge, or additive GAM process passed both the probability-signal and exposure-matched economic gates. Complex models, the 2022–2026 mechanical lockbox, and `mom_255_0` transfer were therefore not opened.

The full nine-cell action × risk-source main grid is complete. G22 v1 remains unpublished invalid audit evidence; its valid rerun is `g22-frozen-v3-v2`. G23 provides the first preregistered platform-level long-short result, without rescuing long-only or satisfying deployment criteria. All runs remain free-research evidence with `formal_run_eligible=false`; XS01 is a separate supplemental experiment, not an automatic continuation.

## Round-two status

[Round-two defense-timing plan v1](docs/23_round2_defense_timing_signal_program_v1.md) remains the frozen historical plan. Execution stopped at its preregistered simple-stage gate with no candidate; any further defense-timing work requires a new preregistration rather than reopening the sealed lockbox or promoting a runner-up.

## Round-three development plan

[Round-three asymmetric defense/re-entry plan v1](docs/24_round3_asymmetric_defense_reentry_program_v1.md) is the new, separate development-only preregistration. It fixes RV21 as the defense-entry sentinel and tests one causal price-recovery exit, without parameter search, complex models, or access to the sealed 2022–2026 outcomes.

Compact machine-readable results are committed under [`results/published/`](results/published/). Daily NAV, trades, holdings, provider payloads, and price Parquet files are intentionally excluded from Git.

## Local runtime storage

The repository may live in OneDrive, but large and frequently changing artifacts should not. Copy `config/runtime.example.toml` to the Git-ignored `config/runtime.local.toml` and point it at a fast local runtime root. The CLI will then default to `<runtime>/data`, `<runtime>/results`, `<runtime>/cache`, and `<runtime>/logs`; explicit CLI paths still take precedence.

```powershell
python -m momentum_reversal runtime-status --create
```

Frozen datasets and complete experiment bundles remain immutable in the local runtime. Git stores only code, hashes, quality summaries, and compact accepted results. See the [runtime storage policy](docs/10_data/runtime_storage_policy.md).

## Data status

Dataset version: `sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate`.

It is frozen for free research, with point-in-time membership reconstruction, canonical security identities, total-return-adjusted OHLC, tradability overrides, and audited terminal events. It remains `review`, uses SPY as a total-return proxy for the S&P 500, and is `formal_eligible=false`. See [`metadata/frozen_dataset/`](metadata/frozen_dataset/) and the [data report](docs/10_data/sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate_implementation_report.md).

## Install and test

```powershell
python -m pip install -e ".[data]"
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

Tests are offline. API credentials belong only in `.env`, which is ignored by Git.

## Experiment CLI

Validate a registered experiment without writing results:

```powershell
python -m momentum_reversal run-experiment `
  --spec config/experiments/G21.toml `
  --dataset-version sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate `
  --run-id g21-dry-run `
  --allow-review-dataset `
  --dry-run
```

The CLI resolves G00/G31 references from the configured local runtime by default;
only pass explicit reference paths when intentionally overriding that runtime.

Real G21 execution supports `--workers N`; the frozen run used four worker processes. Scenario costs are replayed exactly from shared event paths, reducing 576 reported scenarios to 144 full event-loop simulations.

## Repository layout

- `src/momentum_reversal/`: data contracts, factors, portfolio accounting, backtest engine, analytics, and experiment runners.
- `config/experiments/`: frozen program and group specifications.
- `docs/`: research governance, data contract, experiment designs/reports, and paper references.
- `experiments/`: machine-readable group and run registry.
- `input/data_repair_v3/`: small audited identity, action, tradability, and terminal-event ledgers.
- `metadata/frozen_dataset/`: hashes and quality gates, without market data.
- `results/published/`: compact accepted summaries and manifests.
- `config/runtime.example.toml`: portable template for keeping large runtime artifacts outside the synced repository.
- `tests/`: deterministic offline unit and pipeline tests.

This repository is a research record, not investment advice or a claim of production-grade data quality.
