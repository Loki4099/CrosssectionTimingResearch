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

The human-readable rationale and family map are in
[the literature and factor registry note](../../../docs/43_cross_sectional_alpha_literature_and_factor_registry_v1.md).
