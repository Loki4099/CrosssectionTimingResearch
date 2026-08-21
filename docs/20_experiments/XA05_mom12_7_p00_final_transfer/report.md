# XA05 MOM12-7 × P00 final transfer report

## 结论

XA05 已完成并在 XA05C 后硬停。唯一裸 Alpha 是统一 CORE10 样本上的 `XS003_MOM_12_7`；P00 的风险分数、年度因果阈值与 100%/50% 暴露规则完全沿用既有冻结定义。本轮没有重训模型、重选因子或调整 P00。

主要结论是一个清晰的风险—收益取舍，而非全面支配：月频 Top20、5 bps 下，P00 将 Sharpe 从 0.982 提高到 1.097，将最大回撤从 -34.45% 改善到 -22.26%，同时改善 Ulcer、Pain、CDaR95、最差日/周和回撤持续时间；但期末财富从 7.131 降至 6.066，约损失 14.93%。因此 primary 四指标门失败，八个周/月 × Top-K 单元仅 2 个通过，整体 family gate 失败，且不授权自动部署。

P00 的择时信息本身仍然很强。相对于具有相同平均股票暴露的静态路径，月频 Top20 的期末财富提高 24.67%；这说明结果不是单纯少持有股票带来的低波动。与此同时，P00 的作用明显依赖持仓宽度：Top5 在周频和月频均通过期末财富、择时价值、Sharpe、最大回撤四项门，而 Top10/20/50 没有全部通过。

## Primary：月频 Top20，5 bps

| 指标 | Naked | P00 overlay | Matched static |
|---|---:|---:|---:|
| 期末财富 | 7.131 | 6.066 | 4.866 |
| CAGR | 25.98% | 23.60% | 20.45% |
| 年化波动 | 27.48% | 21.52% | 20.47% |
| Sharpe | 0.982 | 1.097 | 1.016 |
| Sortino | 1.583 | 1.768 | 1.637 |
| Calmar | 0.754 | 1.060 | 0.767 |
| 最大回撤 | -34.45% | -22.26% | -26.68% |
| 最大回撤持续期 | 305日 | 272日 | 294日 |
| Ulcer Index | 7.67% | 6.03% | 5.59% |
| Pain Index | 5.48% | 4.19% | 3.87% |
| CDaR95 | 20.65% | 16.23% | 15.49% |
| 最差单日 | -11.74% | -7.51% | -8.72% |
| 最差一周 | -15.92% | -10.77% | -11.91% |
| 平均股票暴露 | 99.97% | 74.53% | 74.53% |

Primary 差值为：`overlay/naked terminal = 0.8507`、`overlay/static terminal = 1.2467`、`ΔSharpe = +0.1149`、`ΔMDD = +12.18pp`、`ΔUlcer = -1.64pp`、`ΔPain = -1.28pp`、`ΔCDaR95 = -4.42pp`。P00 最深回撤发生在 2022 年，而 naked 与 matched-static 的最深回撤均发生在 COVID 冲击。

## 周频复制与横截面宽度

周频 Top20、10 bps 得到相同方向：P00 相对 naked 期末财富比 0.8575，相对 matched-static 的择时价值为 +24.18%，Sharpe 提高 0.1117，最大回撤改善 11.08pp。两种频率的 Top20 都显示“少赚一些、明显少回撤、风险调整后更好”。

四项门只在以下两个主成本单元同时通过：

- 月频 Top5：overlay/naked 期末财富比 1.2132，择时价值 +63.85%，Sharpe +0.1969，MDD +11.56pp。
- 周频 Top5：overlay/naked 期末财富比 1.2141，择时价值 +45.23%，Sharpe +0.1483，MDD +9.15pp。

这支持“P00 与集中型 MOM12-7 组合存在更强协同”的研究解释，但 2/8 的 family 结果不足以把它写成普适结论。

## 回撤图表

- [月频 Top20 NAV](../../../results/published/cross_sectional_alpha/XA05/figures/monthly_top20_nav.png)
- [月频 Top20 underwater](../../../results/published/cross_sectional_alpha/XA05/figures/monthly_top20_underwater.png)
- [月频 Top20 rolling drawdown](../../../results/published/cross_sectional_alpha/XA05/figures/monthly_top20_rolling_drawdown.png)
- [月频 Top20 十大回撤](../../../results/published/cross_sectional_alpha/XA05/figures/monthly_top20_drawdown_episodes.png)
- [月频 Top20 年度收益](../../../results/published/cross_sectional_alpha/XA05/figures/monthly_top20_annual_returns.png)
- [P00 暴露与状态](../../../results/published/cross_sectional_alpha/XA05/figures/monthly_top20_p00_exposure_state.png)
- [周频 Top20 NAV](../../../results/published/cross_sectional_alpha/XA05/figures/weekly_top20_nav.png)
- [周频 Top20 underwater](../../../results/published/cross_sectional_alpha/XA05/figures/weekly_top20_underwater.png)
- [周频 Top20 rolling drawdown](../../../results/published/cross_sectional_alpha/XA05/figures/weekly_top20_rolling_drawdown.png)
- [跨频率与持仓宽度热图](../../../results/published/cross_sectional_alpha/XA05/figures/cross_cell_robustness_heatmaps.png)

## 解释与使用边界

若目标是最大化本段历史的最终财富，裸 `MOM12-7` Top20 更优；若目标是降低尾部风险并改善持有体验，P00 overlay 提供了显著改善。最诚实的成果表达是两个风险档位：裸策略是增长档，P00 是风险控制档，而不是宣称后者无条件优于前者。

本区间已参与多轮研究选择，因此结果是 full-history causal/prequential 研究证据，不是新的独立样本外确认。`formal_eligible=false`、`automatic_deployment=false` 保持不变；真正新增证据应来自 2026-07 之后的 paper/live forward 记录。

机器结果见[紧凑发布包](../../../results/published/cross_sectional_alpha/XA05/)；完整 daily NAV、事件账簿、持仓和 rolling ledgers 保留在本地 runtime。
