# Cross-sectional market + SEC data bundle

- Data bundle: `xs-market-sec-bundle-20260820-v1`
- Status: `data_ready_for_experiment_planning`
- Formal eligibility: `false`
- Experiment authorization: `false`
- Performance results used for readiness: `false`

This directory is a compact review layer. Full market/SEC Parquet, DuckDB, raw provider payloads, fetch ledgers, portfolio returns and holdings remain outside Git. `evidence_index.json` anchors those runtime files by SHA256 and row count.

## Data gates

- SID coverage: 745/745 securities; member-session coverage 100.000000%.
- SEC issuers: 753/753 completed; failures 0.
- SEC Company Facts applicability: 2 reviewed issuer(s) resolved not applicable; imputed fact rows 0.
- SEC rows: 2386818 filings, 1679666 registered facts, 358303 canonical annual facts.
- Market factors: 11 factors across 162 signal dates; volume QA passed.
- Unified factor database: 1380944 rows, 17 factors, 974498 eligible observations; primary-key duplicates 0.

- Accounting identity, historical SID-to-CIK temporal support, actual future-input truncation and independent deterministic rebuild gates all passed.

## Data-only factor readiness

Ready selected factor IDs: `XS001_MOM_255_0`, `XS002_MOM_12_1`, `XS003_MOM_12_7`, `XS004_HIGH_52W`, `XS007_ST_REV_21`, `XS008_SAME_MONTH_5Y`, `XS013_LOW_BETA_FP`, `XS015_MAX_21`, `XS018_AMIHUD_252`, `XS019_PRICE_DELAY_52W`, `XS020_VOLUME_SHOCK_50D`, `XS032_GROSS_PROFIT_AT`, `XS041_ASSET_GROWTH`, `XS056_CFO_ACCRUALS_PT`.

Registered first-round factors blocked by data gates: `XS026_VALUE_BM`, `XS030_NET_PAYOUT_YIELD`.

These decisions use coverage and availability only. They contain no forward returns, Top-K performance, model selection or P00 transfer result, and therefore do not authorize a numbered experiment.
