# R4B：原 T2 单因子统一参考实验——冻结设计

- program：`defense_factor_audit_round4_v1`
- 状态：`preregistered_development_authorized`
- 输入：R4A manifest `1b0b27f689bb3966a34ca94076467be7dad209afa8910a516827b0419514dd7f`
- 证据边界：2005–2021 development；2022+ lockbox 不读

## 1. 候选与目标

只运行 `factor_registry_resolved.csv` 中 17 条 `reference_eligible` arm。三条 `invalid_data` arm 保留失败记录且不得替补。唯一筛选目标为下一执行开盘到再下一执行开盘的 SPY 对现金对数超额收益 `T1`，以及 `T2=1[T1<0]`。成本不进入标签；不生成替代 target。

## 2. 因果信号与动作

每个 execution year 开始前，仅用此前成熟且有效的 score 历史；至少 260 周。以 pandas/numpy `linear` empirical q75 冻结全年阈值。`score>q75` 配置 50% SPY/50% RF，否则 100% SPY；等号 risk-on。缺值周不更新状态、不做 overlay 交易，首个状态前 risk-on。信号周末收盘计算，下一 XNYS session 开盘执行。

每条拼接 OOS 路径只在首次 OOS execution open 从现金启动一次；年度边界携带 NAV 与持仓。主成本 10bp/美元实际 SPY turnover，0/5/20bp 为固定敏感性。无 short、无杠杆。主控制为相同起止、缺值日历、成本和周频恢复规则下的 ex-post 同平均目标股票暴露静态 SPY/RF replay。

## 3. 输出与判定

逐 arm 保存 raw-score AUC/PR-AUC、T1 time-series Spearman、precision/recall、负超额收益金额捕获、五分位表、年度贡献、动态与静态 NAV、CAGR/Sharpe/MDD/turnover、13 周 moving-block 区间。另生成 17 arm 的 961 周共同交集表。

`reference_positive` 同时要求 AUC>0.5、rho(T1)<0、10bp 相对同暴露终值>0、正 active 完整年度占比>=60%。`robust_reference_positive` 另要求 one-sided 13周block下界>0、BH-FDR 10%及 dot-com/GFC/COVID leave-one-out 不翻负。标签只作参考，不选冠军、不授权模型。

输出 bundle 至少包含 `targets_weekly.parquet`、`annual_thresholds.parquet`、`signals_weekly.parquet`、`nav_daily.parquet`、`arm_summary.csv`、`yearly_contributions.csv`、`quintiles.csv`、`manifest.json`。不得保存 2022+ target/NAV。
