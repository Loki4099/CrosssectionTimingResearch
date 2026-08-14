# Published experiment summaries

This directory contains the compact, reviewable outputs committed to Git. Full daily NAV, holdings, trades, rebalances, frozen market data, and provider payloads remain local because of size and data-license constraints.

- `G00/`: naked momentum control, frozen v3 dataset.
- `G21/`: SPY RV21 strict-Q4 direct-reversal experiment, frozen v3 dataset.

Each folder includes `summary.csv`, `comparison.csv`, the resolved TOML config, and the run manifest. Economic interpretation lives in `docs/20_experiments/`; the CSV files remain the machine-readable source of truth.
