# 动量—反转系统化实验计划 v2

最后更新：2026-08-16

状态：数据、G00 与 G11–G13、G21–G23、G31–G33 九宫格主网格均已完成。

## 1. 共同研究契约

- 股票池：历史时点 S&P 500 成分股；
- 数据：冻结免费研究数据 v3，评价期统一为 2018-01-02 开盘至 2026-06-30 收盘；
- 信号：`mom_255_0`、`mom_255_21`、`mom_12_1`；
- 宽度：Top/Bottom 10、20、50；频率：周、月；
- 执行：信号期最后交易日收盘计算，下一交易日开盘成交；
- long-only：TopK 等权，保留市场 beta；
- long-short：TopK +50%、BottomK -50%，gross=1、net=0，并派生 gross=2 学术 WML；
- 成本：周频主 10bps、月频主 5bps，压力情景 0/5/10/20bps；
- 借券费：WML 主场景 1%，压力情景 0%/1%/3%；
- 基准：SPY 总回报代理和 T-bill；不把 SPY 冒充官方 SPXTR；
- 数据状态：`review / free_research_candidate`，所有运行必须保留 `formal_eligible=false`。

成本和借券费是同一交易路径的情景，不增加策略路径数。所有场景必须保留，不能只展示赢家。

## 2. 实验矩阵

| 组 | 风险动作 | 风险变量 | Long-only | Long-short/WML |
|---|---|---|---|---|
| G00 | 无风控 | 无 | 已完成：共同基线 | 已完成：机制对照 |
| G11 | 连续缩放 | SPY 历史波动率 | 已完成：H1 0/18 失败、CAGR/Sharpe 18/18 下降、MDD 18/18 改善 | 已完成：216/216 压力场景三项改善、绝对表现弱 |
| G12 | 连续缩放 | 动量组合历史波动率 | 已完成：H1 0/18 失败、CAGR/Sharpe 18/18 下降、MDD 16/18 改善且过度保险 | 已完成：17/18 主场景、204/216 压力场景联合改善，绝对表现弱 |
| G13 | 连续缩放 | 动量组合未来预测波动率 | 已完成：H1 0/18 失败、CAGR/Sharpe 18/18 下降、MDD 18/18 改善且过度保险 | 已完成：12/18 主场景、132/216 压力场景联合改善，成本/借券敏感、绝对表现弱 |
| G21 | 高波切反转 | SPY 历史波动率 | 已完成：失败负对照 | 已完成：机制成立、绝对收益弱 |
| G22 | 高波切反转 | 动量组合历史波动率 | 已完成：4/36 联合改善，H1 失败、MDD 中位恶化 | 已完成：23/36，月频 8/18 未过门槛；周频局部机制 |
| G23 | 高波切反转 | 动量组合未来预测波动率 | 已完成：0/36，Sharpe/MDD 全部恶化 | 已完成：33/36、月15/18、周18/18 通过平台门槛；压力敏感、绝对表现弱 |
| G31 | 高波减仓 | SPY 历史波动率 | 已完成：回撤 18/18 改善、H1 失败 | 已完成：机制为正、绝对表现弱 |
| G32 | 高波减仓 | 动量组合历史波动率 | 已完成：H1 失败、CAGR/Sharpe 18/18 下降、MDD 混合 | 已完成：17/18 同时改善 Sharpe/MDD，压力下机制稳健、绝对表现弱 |
| G33 | 高波减仓 | 动量组合未来预测波动率 | 已完成：H1 0/18 失败、CAGR/Sharpe 18/18 下降、MDD 18/18 改善 | 已完成：10/18 同时改善 Sharpe/MDD，MDD 改善稳健，Sharpe 对成本/借券费敏感、绝对表现弱 |

`XS01` 单独研究个股历史波动率参与横截面选股，不与上述组合择时混合。

## 3. 已完成结果的作用

### G00

G00 是所有风控组唯一共同对照。代表性的 `mom_255_0 / Top20 / monthly` long-only 收益高于 SPY，但回撤与 Sharpe未达到部署门槛。WML 的 beta 接近零，却在本样本和成本下绝对收益较弱。

### G11

[G11 设计](./20_experiments/G11_spy_continuous_scale/design.md)与[报告](./20_experiments/G11_spy_continuous_scale/report.md)记录了用信号收盘 SPY RV21 驱动、下一开盘执行的无杠杆连续 15% 目标缩放。long-only 的 CAGR 与 Sharpe 在 18/18 个主场景下降、最大回撤在 18/18 改善，预注册 H1 以 0/18 失败；long-short 的 CAGR、Sharpe 与最大回撤在 18/18 个主场景及全部 216 个注册成本/借券压力场景中同时改善，机制稳健但绝对表现仍弱。本组仍为 `formal_run_eligible=false` 的免费研究证据。

### G12

[G12 设计](./20_experiments/G12_book_hist_continuous_scale/design.md)与[报告](./20_experiments/G12_book_hist_continuous_scale/report.md)记录了匹配裸账簿 RV126 驱动的无杠杆连续 15% 目标缩放。long-only 的 CAGR 与 Sharpe 在 18/18 个主场景下降、最大回撤在 16/18 改善，预注册 H1 以 0/18 失败；长期持续缩放构成过度保险。Long-short 在 17/18 个主场景、204/216 个压力场景同时改善 Sharpe 与最大回撤，但绝对 CAGR/Sharpe 仍弱。本组仍为 `formal_run_eligible=false` 的免费研究证据。

### G13

[G13 设计](./20_experiments/G13_book_forecast_continuous_scale/design.md)与[报告](./20_experiments/G13_book_forecast_continuous_scale/report.md)记录了匹配裸账簿因果 EWMA 21-session 预测波动率驱动的无杠杆连续 15% 目标缩放。long-only 的 CAGR 与 Sharpe 在 18/18 个主场景下降、最大回撤在 18/18 改善，预注册 H1 以 0/18 失败；连续预测目标同样构成过度保险。Long-short 在 12/18 个主场景、132/216 个压力场景同时改善 Sharpe 与最大回撤，但成本/借券敏感且绝对表现弱。本组仍为 `formal_run_eligible=false` 的免费研究证据。

### G21

严格 Q4 direct reversal 对 long-only 的 36 个主场景没有任何最大回撤改善，且左尾普遍恶化；不再对 rev5/rev20、TopK 做事后寻优。相同机制对周频 WML 的 18/18 个场景同时改善 CAGR、Sharpe 和 MDD，证明论文机制主要作用于 loser/short 腿。

### G22

[G22 设计](./20_experiments/G22_book_hist_reversal/design.md)、[v2 勘误](./20_experiments/G22_book_hist_reversal/implementation_note.md)与[报告](./20_experiments/G22_book_hist_reversal/report.md)记录了匹配裸账簿 RV126 严格 Q4 切换 5/20 日反转的结果。Long-only 只有 4/36 个主场景同时改善 CAGR、Sharpe 与 MDD，周/月各 2/18，H1 明确失败；long-short 为 23/36，月频 8/18，未越过预注册平台门槛。周频 15/18 和全体 MDD 改善支持局部 WML 机制，但高成本/借券下衰减且绝对表现弱。v1 因设计文字中的 program SHA 录入错误被判治理无效；有效证据是从冻结输入完整重跑的 `g22-frozen-v3-v2`。

### G23

[G23 设计](./20_experiments/G23_book_forecast_reversal/design.md)与[报告](./20_experiments/G23_book_forecast_reversal/report.md)记录了匹配裸账簿因果 EWMA 预测波动率严格 Q4 切换 5/20 日反转的结果。Long-only 0/36 联合改善，Sharpe 与 MDD 无一改善；long-short 为 33/36，月频 15/18、周频 18/18，三项中位 delta 全正，首次通过反转组平台门槛。最高 20bps+3% 压力组合降至 23/36，且绝对 CAGR/Sharpe 仍弱，因此只确认 LS 机制，不形成部署或 long-only 支持。

### G31

[G31 设计](./20_experiments/G31_spy_derisk/design.md)与[报告](./20_experiments/G31_spy_derisk/report.md)记录了严格 Q4 减仓结果。long-only 的最大回撤在 18/18 个主场景改善，但未满足同时改善 Sharpe 的 H1 平台门槛；long-short 减仓机制为正，但绝对表现仍弱。

### G32

[G32 设计](./20_experiments/G32_book_hist_derisk/design.md)与[报告](./20_experiments/G32_book_hist_derisk/report.md)记录了用裸 book RV126 替换 SPY 风险源的严格 Q4 减仓结果。long-only 的 CAGR 与 Sharpe 在 18/18 个主场景下降，最大回撤改善结果混合，预注册 H1 以 0/18 失败；long-short 在 17/18 个主场景同时改善 Sharpe 与最大回撤，且成本和借券费压力下机制稳健，但绝对表现仍弱。

### G33

[G33 设计](./20_experiments/G33_book_forecast_derisk/design.md)与[报告](./20_experiments/G33_book_forecast_derisk/report.md)记录了用匹配裸 book 收益的因果 EWMA 21-session 预测波动率驱动严格 Q4 减仓的结果。long-only 的 CAGR 与 Sharpe 在 18/18 个主场景下降，最大回撤在 18/18 个场景改善，预注册 H1 以 0/18 失败；long-short 的回撤改善稳健，但仅 10/18 个主场景同时改善 Sharpe 与最大回撤，Sharpe 改善对成本和借券费敏感，绝对表现仍弱。

## 4. 当前执行顺序

1. **九宫格主网格结束**：不继续自动追加主网格；保留 G23 LS 为平台级机制证据，并保留全部 long-only 失败结论。
2. **补充实验另行预注册**：XS01、q85/q90/q95、确认规则、杠杆或预测器稳健性必须成为新的明确任务，不由本轮结果自动触发。

暂不使用机器学习。当前 2,134 个交易日与有限 Q4 事件不足以支撑大规模模型选择；先完成规则型九宫格。

## 5. 统一评价

每组必须报告：

- CAGR、年化波动、T-bill 超额 Sharpe、Sortino、Calmar；
- 最大回撤、回撤持续期、ES10/ES5、最差持有期；
- SPY beta、alpha、tracking error、information ratio；
- gross/net、风险仓位、换手、成本、借券费与公司行动；
- 相对同场景 G00 的 CAGR、Sharpe、MDD 和波动率增量；
- Q1–Q4 条件收益与 2020 等危机窗口。

Long-only 部署联合门槛继续为 `CAGR > SPY、Sharpe > 1、MDD < 25%`；失败结果不删除。WML 不要求 CAGR 超过 SPY，但必须说明绝对收益、借券和容量限制。

## 6. 目录与治理

- 设计与报告：`docs/20_experiments/<group>/`；
- 机器配置：`config/experiments/<group>.toml`；
- 台账：`experiments/groups.csv` 与 `experiments/registry.csv`；
- 完整本地结果：`results/experiments/<group>/runs/<run_id>/`；
- Git 精简结果：`results/published/<group>/`；
- 冻结数据元数据：`metadata/frozen_dataset/`。

任何数据变化必须发布新 dataset version；任何规则扩展必须先更新设计和参数预算。完整行情与日度大产物不进入公开 GitHub。
