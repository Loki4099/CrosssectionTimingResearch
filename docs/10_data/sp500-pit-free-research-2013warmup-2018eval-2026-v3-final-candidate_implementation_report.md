# sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate 有限返修报告

状态：`READY_FOR_FINAL_REVIEW`  
来源：`sp500-pit-free-research-2013warmup-2018eval-2026-v2-candidate`  
本次仅处理已冻结的执行级阻塞项；未补新证券、未扩数据源、未运行策略。

## 固定返修范围

- tradability override：24 条，命中 45 个价格行；
- membership boundary override：1 条；
- corporate actions：58 条，其中研究级 cash liquidation 50 条；
- corporate-action valuation：58/58 通过；
- terminal execution rows：324/324 通过；
- bank halt gap audit：2 条；
- double build reproducible：true。

## 覆盖率

- signal close minimum / average：0.980159 / 0.991495
- MOM 255-0 minimum / average：0.980080 / 0.991008
- MOM 255-21 minimum / average：0.980080 / 0.991040
- MOM 12-1 minimum / average：0.980080 / 0.991060
- eligible next-open minimum：0.997976

## Gates

- reproducible_double_build: PASS — {"hashes_match": true}
- unique_price_key: PASS — {"duplicates": 0}
- price_structure: PASS — {"partial_ohlc": 0, "invalid_numeric": 0}
- membership_valid: PASS — {"intervals": 754}
- all_signal_dates_present: PASS — {"missing": 0, "extra": 0}
- boundary_dates: PASS — {}
- benchmark_and_rf: PASS — {"benchmark_missing": 0, "rf_missing": 0}
- coverage_signal_close: PASS — {"minimum_signal_close": 0.9801587301587301, "average_signal_close": 0.9914949098270646, "minimum_mom_255_0": 0.9800796812749004, "average_mom_255_0": 0.9910075891432797, "minimum_mom_255_21": 0.9800796812749004, "average_mom_255_21": 0.9910400818173689, "minimum_mom_12_1": 0.9800796812749004, "average_mom_12_1": 0.99106035240579, "minimum_next_open": 0.9979757085020243}
- coverage_factor_endpoints: PASS — {"minimum_signal_close": 0.9801587301587301, "average_signal_close": 0.9914949098270646, "minimum_mom_255_0": 0.9800796812749004, "average_mom_255_0": 0.9910075891432797, "minimum_mom_255_21": 0.9800796812749004, "average_mom_255_21": 0.9910400818173689, "minimum_mom_12_1": 0.9800796812749004, "average_mom_12_1": 0.99106035240579, "minimum_next_open": 0.9979757085020243}
- coverage_next_open: PASS — {"minimum_signal_close": 0.9801587301587301, "average_signal_close": 0.9914949098270646, "minimum_mom_255_0": 0.9800796812749004, "average_mom_255_0": 0.9910075891432797, "minimum_mom_255_21": 0.9800796812749004, "average_mom_255_21": 0.9910400818173689, "minimum_mom_12_1": 0.9800796812749004, "average_mom_12_1": 0.99106035240579, "minimum_next_open": 0.9979757085020243}
- tradability_overrides: PASS — {"override_rows": 24, "masked_price_rows": 45}
- membership_boundary_overrides: PASS — {"applied_rows": 1}
- corporate_action_valuation: PASS — {"action_count": 58, "invalid_action_ids": [], "duplicate_source_actions": 0}
- terminal_classified: PASS — {"exits": 162, "unresolved": 0}
- terminal_execution_weekly_monthly: PASS — {"evaluated_rows": 324, "fallback_over_5_sessions": 0, "unresolved_rows": 0, "failed_rows": 0, "maximum_accepted_stale_sessions": 6}
- bank_halt_resolution: PASS — {"gap_outlier_rows": 2}
- provider_lineage: PASS — {"missing": 0, "phantom_cp_rows": 0}

失败门槛：无。

## 固定限制

- 免费公开 PIT 名单与 Yahoo/Tiingo 数据，只批准研究用途；
- `cash_liquidation` 是有审计证据的研究近似，不冒充法律对价；
- SPY 为可投资总回报代理，不是官方 SPXTR；
- 未继续追补其余 unavailable securities。
