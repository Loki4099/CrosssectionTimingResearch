# 风控九宫格：当前覆盖与下一阶段

最后更新：2026-08-16

有效数据：`sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate`

## 研究矩阵

九宫格把“采取什么风险动作”与“用什么风险变量”严格拆开。每格同时运行 long-only 与 dollar-neutral WML，且始终保留三个动量定义、Top10/20/50 和周/月频。

| 风险动作 | SPY 历史波动率 | 动量组合历史波动率 | 动量组合未来预测波动率 |
|---|---|---|---|
| 连续缩放 | **G11：已完成** | **G12：已完成** | **G13：已完成** |
| 高波切反转 | **G21：已完成** | **G22：已完成** | **G23：已完成** |
| 高波减仓/T-bill | **G31：已完成** | **G32：已完成** | **G33：已完成** |

个股自身波动率改变横截面排名，属于 `XS01`，不混入组合择时九宫格。

## 已确认的机制

- G00：long-only 裸动量具有较高收益，但回撤仍大；WML beta 接近零，但绝对 Sharpe 较弱。
- [G11](./20_experiments/G11_spy_continuous_scale/report.md)（[设计](./20_experiments/G11_spy_continuous_scale/design.md)）：long-only CAGR 与 Sharpe 18/18 下降、最大回撤 18/18 改善，H1 以 0/18 失败；long-short 在 18/18 个主场景和全部 216 个成本/借券压力场景中同时改善 CAGR、Sharpe 与最大回撤，但绝对表现仍弱。
- [G12](./20_experiments/G12_book_hist_continuous_scale/report.md)（[设计](./20_experiments/G12_book_hist_continuous_scale/design.md)）：long-only CAGR/Sharpe 18/18 下降、MDD 16/18 改善，H1 以 0/18 失败；连续 RV126 长期压低风险敞口，属于过度保险。Long-short 在 17/18 主场景和 204/216 压力场景同时改善 Sharpe/MDD，但绝对表现仍弱。
- [G13](./20_experiments/G13_book_forecast_continuous_scale/report.md)（[设计](./20_experiments/G13_book_forecast_continuous_scale/design.md)）：long-only CAGR/Sharpe 18/18 下降、MDD 18/18 改善，H1 以 0/18 失败；连续因果 EWMA 预测目标同样过度保险。Long-short 在 12/18 主场景和 132/216 压力场景同时改善 Sharpe/MDD，但成本/借券敏感且绝对表现弱。
- G21：SPY RV21 进入滚动 Q4 后直接切 5/20 日反转，对 long-only 全平台恶化左尾；对周频 WML 则稳定改善收益、Sharpe 和最大回撤。
- [G22](./20_experiments/G22_book_hist_reversal/report.md)（[设计](./20_experiments/G22_book_hist_reversal/design.md)）：book RV126 Q4 反转的 LO 仅 4/36 联合改善且 MDD 中位恶化，H1 失败；LS 为 23/36、月频 8/18，未过平台门槛。周频 15/18 与 Q4 左尾改善支持成本敏感的局部 WML 机制。
- [G23](./20_experiments/G23_book_forecast_reversal/report.md)（[设计](./20_experiments/G23_book_forecast_reversal/design.md)）：因果 EWMA forecast Q4 反转的 LO 0/36 且 Sharpe/MDD 全部恶化；LS 以 33/36、月频 15/18、周频 18/18 通过平台门槛，Q4 左尾同步改善，但最高成本/借券压力跌至 23/36且绝对表现仍弱。
- [G31](./20_experiments/G31_spy_derisk/report.md)（[设计](./20_experiments/G31_spy_derisk/design.md)）：long-only 最大回撤 18/18 改善但 H1 失败；long-short 减仓机制为正，但绝对表现弱。
- [G32](./20_experiments/G32_book_hist_derisk/report.md)（[设计](./20_experiments/G32_book_hist_derisk/design.md)）：long-only H1 失败，CAGR 与 Sharpe 18/18 下降，最大回撤改善结果混合；long-short 在 17/18 个主场景同时改善 Sharpe 与最大回撤，并通过预注册成本/借券压力检验，但绝对表现仍弱。
- [G33](./20_experiments/G33_book_forecast_derisk/report.md)（[设计](./20_experiments/G33_book_forecast_derisk/design.md)）：long-only H1 以 0/18 失败，CAGR 与 Sharpe 18/18 下降，最大回撤 18/18 改善；long-short 的回撤改善稳健，但仅 10/18 个主场景同时改善 Sharpe 与最大回撤，Sharpe 改善对成本/借券费敏感且绝对表现仍弱。
- G31–G33 的 long-only 均未通过 H1；三个冻结风险源都没有为严格 Q4 减仓提供跨参数收益—风险调整支持。
- 因此论文中的反转切换主要修复 loser/short 腿，不等价于适合保留美股 beta 的 long-only 风控。

## 后续顺序

1. **主网格已闭合**：九格均完成；G23 首次给出 LS 平台级支持，但所有 long-only 假设仍失败，不能把 WML 结果外推为个人多头配置。
2. **XS01 另行决策**：个股波动率横截面调整、指数轮动、杠杆和机器学习均属于补充研究，须重新预注册，不能作为本轮自动延续。

用户完成第一轮复盘后作出新的前瞻决策：XS01 暂缓，下一研究程序先分离检验“何时防御”。[第二轮防御时点信号筛选与模型比较计划 v1](./23_round2_defense_timing_signal_program_v1.md)将扩长 SPY/T-bill 数据，以统一周频 100%/50% SPY 政策筛选低维信号和受限模型，只有唯一晋级信号才迁移到 `mom_255_0`。该计划当前仍为 planning，不改变本文件的第一轮历史判定。

## 紧凑扩展纪律

若后续诊断极端阈值，只允许预登记的 q85/q90/q95 和一种确认/滞后规则，并同时报告全部结果。不得在完整历史上连续搜索最佳阈值。当前免费数据结论属于研究级且 `formal_run_eligible=false`；实盘前使用 Norgate 或等价永久证券标识数据重新验收。
