# Data repair v3 ledgers

This directory is a bounded input patch set over `data_repair_v2`. It does not alter the central builder or a backtest result.

## Executable inputs

- `security_identity_overrides.csv` and `outlier_allowlist.csv` are content copies of the v2 inputs.
- `corporate_action_ledger.csv` contains only action types accepted by the current `CorporateActionLedger`: `cash_merger`, `stock_merger`, and `cash_and_stock_merger`.
- `VIAB_PARA_20191204` and `KSU_CP_20211214` are deliberately absent because their target securities have no verified price series in the candidate dataset. Their bounded research fallbacks are recorded separately.
- `DISCK_WBD_20220408` converts one adjusted DISCK unit into one WBD unit before the 2022-04-11 open.

## Boundary override contract

`membership_boundary_overrides.csv` is an exact-match patch table. A consumer must match `(canonical_sid, match_effective_from, match_effective_to)` before replacing the half-open interval. A missing or multiple match must fail closed. The only v3 patch clips TSS to `[2008-01-02, 2019-09-19)` so it cannot be ranked after its pre-open conversion into GPN.

## Minimal terminal-resolution schema proposal

The current `CorporateActionLedger` cannot represent a liquidation with no fixed corporate consideration. `terminal_resolution_overrides.csv` is therefore a proposed input contract and is **not wired into the builder or engine yet**.

A consumer needs only four operations:

1. Match one `canonical_sid` and its half-open `membership_effective_to` exactly.
2. For `last_close_cash_liquidation`, replace adjusted source units with cash on `resolution_session` at the stated strictly prior `price_sid` / `price_date` / `price_field`; require `stale_sessions <= max_stale_sessions`.
3. For `first_tradable_otc_open_liquidation`, ignore the explicitly bounded stale-price interval and replace adjusted source units with cash at the stated first tradable open.
4. Verify `expected_price` against the frozen `price_evidence_ref`; any mismatch, missing session, duplicate override, or still-present `removed_action_id` must fail closed.

The VIAB and KSU rows are research approximations, not reconstructions of the unavailable target holdings. The SIVB and SBNY rows realize the observed OTC loss and prevent carried-forward halt prices from becoming executable exits.
