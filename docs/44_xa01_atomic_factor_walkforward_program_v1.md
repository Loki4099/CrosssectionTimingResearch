# XA01 atomic-factor walk-forward program v1

This program moves the active research track from data construction to direct
single-factor evidence. It uses the certified point-in-time S&P 500 market and
SEC source layer and the 14 readiness-selected factors. The experiment is a
full-history causal/prequential exploration, not an independent holdout or
formal external confirmation.

Canonical machine settings live in
`config/experiments/xa01/program.toml`; the exact factor universe lives in
`config/experiments/xa01/factor_registry.csv`; batch mechanics and gates live in
`docs/20_experiments/XA01_atomic_factor_walkforward/design.md`.

The experiment deliberately separates atomic factor discovery from model
selection. Its result will determine the evidence-qualified pool, the optional
dimension representatives, and the empirical correlation clusters used to
design the next aggregation round.

