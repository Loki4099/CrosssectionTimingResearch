# Frozen dataset metadata

These files describe the local dataset
`sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate` without redistributing its price history.

- `FROZEN.json`: immutable freeze decision and manifest binding.
- `gate_results.json`: data-quality gate outcomes.
- `test_results.json`: bounded repair acceptance-test record.

`FROZEN.json` records the SHA256 of the full local manifest. The manifest itself is not published because its immutable provenance records contain workstation-specific absolute paths and refer to licensed/large local data. The dataset is `review / free_research_candidate`, not formal or production grade. A local run must possess the separately stored files whose hashes are named in the manifest and must explicitly allow review data.
