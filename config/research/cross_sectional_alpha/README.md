# Cross-sectional alpha research registries

This directory is the append-only research catalog for the next long-only S&P 500
cross-sectional alpha program.  It is deliberately separate from
`config/experiments/`: the files here describe literature and candidate signals,
but do not authorize a numbered experiment, a parameter search, a model run, or a
P00 transfer test.

Files:

- `paper_registry.csv` records primary papers, replication dictionaries, and
  model/aggregation papers.
- `factor_definition_registry.csv` records one canonical candidate definition per
  row, including direction, original parameters, PIT availability, data tier,
  expected correlation cluster, and adaptation status.
- `active_factor_registry.csv` is the compact implementation view for the current
  data program. Every `factor_id` and `source_definition_id` in this file must be
  identical and resolve to exactly one row in `factor_definition_registry.csv`.
  It is not an experiment authorization.

Registry rules:

1. A factor ID is never reused for a changed formula.  A material change in window,
   transform, data vendor, or availability rule receives a new ID.
2. `paper_canonical` means the row is intended to reproduce the cited construction.
   `project_translation` means a project-specific version and must not inherit the
   paper's evidence automatically.  `definition_resolution_required` cannot be run
   until the cited appendix or code has been resolved into a deterministic formula.
3. The catalog may be broad.  Correlated variants remain visible, but
   `expected_corr_cluster` and `redundancy_group` prevent them from being counted as
   independent economic ideas.
4. `T0_PRICE_PIT` candidates use the frozen PIT membership, price OHLC,
   total-return prices, and corporate-action layer. `T1_VOLUME_QUALIFIED` requires
   volume/dollar-volume split, unit, and cross-provider QA before use.
   `T2_REFERENCE_CLASSIFICATION_PIT` adds historical classifications, PIT shares or
   market capitalisation, and reference factor series. `T3_ACCOUNTING_PIT` requires
   financial-statement vintages with actual release times.
   `T4_EVENT_EXPECTATION_PIT` covers timestamped announcements, estimates,
   ownership, and short-interest records. `T5_DERIVATIVE_ALTERNATIVE` covers
   options, text, search, patent, and network data.
5. Accounting, analyst, ownership, options, text, and network records are usable
   only after their actual publication/release timestamp is represented.  Fiscal
   period end dates, current classifications, and revised historical snapshots are
   not acceptable substitutes.
6. Long-short evidence in a paper does not imply that the long leg works in this
   project's Top-K long-only portfolio.  The `long_only_evidence` field makes that
   boundary explicit.
7. Registry defects are corrected append-only. `XS039_ACCRUALS` is retained as a
   historical record of the original row, whose formula omitted depreciation; it
   must not be executed as a Sloan replication. `XS039_ACCRUALS_V2` is the strict
   executable successor. `XS056_CFO_ACCRUALS_PT` is a separately identified
   project translation, not a fallback that may inherit the Sloan claim.

## Deferred candidate factor roster

The source-data program is complete, but the factor stage is deferred. No
`factor_id` is currently claimed to be calculated, ready, coverage-qualified, or
authorized for a numbered experiment. `first_round_eligible=true` is design
metadata that still requires a future factor build and its stated `data_gate`; it
is not a current machine status.

The certified source-v1 certificate anchors the complete `data_program.toml`.
Future factor work must create a new program/version that references this source
layer instead of editing the certified v1 configuration in place.

| Role | Factor IDs |
|---|---|
| Market controls | `XS001_MOM_255_0`, `XS002_MOM_12_1` |
| New market atoms | `XS003_MOM_12_7`, `XS004_HIGH_52W`, `XS007_ST_REV_21`, `XS008_SAME_MONTH_5Y`, `XS013_LOW_BETA_FP`, `XS015_MAX_21`, `XS018_AMIHUD_252`, `XS019_PRICE_DELAY_52W`, `XS020_VOLUME_SHOCK_50D` |
| SEC atoms | `XS032_GROSS_PROFIT_AT`, `XS041_ASSET_GROWTH`, `XS039_ACCRUALS_V2` |
| Conditional SEC atoms | `XS026_VALUE_BM`, `XS030_NET_PAYOUT_YIELD` |

The conditional SEC atoms remain missing unless issuer-level market equity has
passed the all-common-share-class audit. Book-to-market requires current issuer
market equity; net payout yield requires common-only dividends, repurchases and
issuance plus audited fiscal-year-end issuer market equity. Missing components
are never treated as zero or approximated from one listed share class.

`XS018_AMIHUD_252` and `XS020_VOLUME_SHOCK_50D` remain candidate definitions. The
frozen market source passed the pre-return volume gate:
all 1,701,149 expected member-session keys exist, price-conditional volume
coverage is 100%, positive dollar-volume coverage is 99.997%, negative volume
rows are zero, and 191 split events have no missing volume. This is source-data
evidence, not factor readiness or return-based selection. `XS056_CFO_ACCRUALS_PT` remains a
mechanical coverage alternative for strict Sloan accruals and has
`first_round_eligible=false` in its own right.

The human-readable rationale and family map are in
[the literature and factor registry note](../../../docs/43_cross_sectional_alpha_literature_and_factor_registry_v1.md).
