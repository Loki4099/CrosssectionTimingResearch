# R4C：原 target candidate-independent sanity-check——冻结设计

- 状态：`preregistered_development_authorized`
- 不按因子分组、不选择赢家、不改变 R4B

## 固定审计

1. 对 `T1 < {-40,-20,-10,0,+10,+20,+40}bp` 报标签率、年度率及相对零阈值翻转率；
2. 报 T2 串长度、周状态转移、自相关、年度稳定性、负收益金额与 near-zero `|T1|<=20bp` 占比；
3. 在 0/5/10/20bp 下比较 50% sign-oracle、固定25% alert-budget oracle、seed=20260817 随机25%基线；
4. 构造 4/13/26 周 terminal excess return 与从执行开盘起的 forward excess MAE；终点不早于 2022-01-03 的行右删失；
5. 固定错位格：`T2=0 & MAE13>=10%`、`T2=1 & MAE13<2%`，另报未来期限方向冲突矩阵。

输出 `target_sanity_summary.csv`、`threshold_sensitivity.csv`、`state_runs.csv`、`horizon_conflicts.csv`、`oracle_paths.csv`、`manifest.json`。所有替代标签只作诊断，禁止回跑任何因子。
