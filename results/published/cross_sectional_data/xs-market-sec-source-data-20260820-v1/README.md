# Cross-sectional source-data certification

This compact directory certifies only the frozen market, identifier, and SEC fundamental source layers. It does not require or include the factor database, portfolio results, or experiment authorization.

- Status: `source_data_certified`
- Market dataset: `sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate`
- SID-to-CIK coverage: 100.000000%
- SEC CIKs completed: 753/753
- Aggregate fetch failures: 0
- Source applicability states: `{"available": 751, "resolved_not_applicable": 2}`
- Accounting identity: recomputed and value-equal; gate passed.
- Historical entity support: recomputed and value-equal; gate passed.

Runtime Parquet tables, immutable SEC payloads, the SEC fetch ledger, and DuckDB remain outside Git. Their SHA256 anchors are recorded in `source_data_certification.json`.
