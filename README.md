# CrosssectionTimingResearch

An auditable research framework for S&P 500 point-in-time cross-sectional momentum, reversal, and volatility-based timing. The project keeps long-only portfolios and dollar-neutral WML as separate research arms so market beta and winner-minus-loser mechanisms are not conflated.

## Current evidence

All accepted results use one frozen free-research dataset and the common evaluation window from 2018-01-02 open through 2026-06-30 close.

- [G00 naked momentum](docs/20_experiments/G00_baseline/report.md): valid common control. Long-only momentum earns attractive returns but retains large drawdowns; WML has weak absolute performance.
- [G21 SPY high-volatility reversal](docs/20_experiments/G21_spy_reversal/report.md): failed long-only negative control, but a stable left-tail improvement for weekly WML. The next long-only priority is G31 high-volatility derisking, not direct loser buying.
- [Systematic experiment program](docs/21_systematic_experiment_program_v2.md): the action × risk-source grid and frozen research order.

Compact machine-readable results are committed under [`results/published/`](results/published/). Daily NAV, trades, holdings, provider payloads, and price Parquet files are intentionally excluded from Git.

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
  --reference-g00-root results/experiments/G00/runs/g00-frozen-v3-v1 `
  --allow-review-dataset `
  --dry-run
```

Real G21 execution supports `--workers N`; the frozen run used four worker processes. Scenario costs are replayed exactly from shared event paths, reducing 576 reported scenarios to 144 full event-loop simulations.

## Repository layout

- `src/momentum_reversal/`: data contracts, factors, portfolio accounting, backtest engine, analytics, and experiment runners.
- `config/experiments/`: frozen program and group specifications.
- `docs/`: research governance, data contract, experiment designs/reports, and paper references.
- `experiments/`: machine-readable group and run registry.
- `input/data_repair_v3/`: small audited identity, action, tradability, and terminal-event ledgers.
- `metadata/frozen_dataset/`: hashes and quality gates, without market data.
- `results/published/`: compact accepted summaries and manifests.
- `tests/`: deterministic offline unit and pipeline tests.

This repository is a research record, not investment advice or a claim of production-grade data quality.
