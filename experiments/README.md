# 实验台账说明

本目录同时保存“运行前计划快照”和“运行后结果台账”。二者用途不同，不能只看registry中的历史状态判断实验是否已经完成。

状态来源优先级：

1. `results/published/**/manifest.json`、decision JSON和对应 `round*_results.csv`：已经执行并发布的机器事实；
2. `docs/20_experiments/**/report.md` 与决策备忘录：经济解释和轮次结论；
3. `*_groups.csv`、`*_registry.csv`：冻结前的计划、授权和硬停止快照。

因此，部分Round 4、6–10 registry仍保留 `plan_locked_authorized_not_run` 等运行前文字，这是历史provenance，不是今天的全局状态，不应原地改写。当前主线状态见[横截面Alpha](../docs/research_tracks/cross_sectional_alpha.md)和[防御择时](../docs/research_tracks/defensive_timing.md)。

| 台账 | 范围 |
|---|---|
| `groups.csv`、`registry.csv` | G00与G11–G33主网格，以及XS01历史登记 |
| `round2_*`、`round3_registry.csv` | 早期防御信号和恢复实验；Round 2没有Git内 `results.csv` |
| `round4_*` 至 `round10_*` | 各轮计划快照；带 `results.csv` 的轮次以结果表记录执行状态 |

失败、停止、无效和被替代路径均保留，不复用ID，不覆盖旧行。
