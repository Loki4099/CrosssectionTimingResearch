# 动量—反转系统化实验计划 v2

最后更新：2026-08-14  
状态：数据、G00 与 G21 已完成；下一组为 G31。

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
| G11 | 连续缩放 | SPY 历史波动率 | 待运行 | 待运行 |
| G12 | 连续缩放 | 动量组合历史波动率 | 待运行 | 待运行 |
| G13 | 连续缩放 | 动量组合未来预测波动率 | 待运行 | 待运行 |
| G21 | 高波切反转 | SPY 历史波动率 | 已完成：失败负对照 | 已完成：机制成立、绝对收益弱 |
| G22 | 高波切反转 | 动量组合历史波动率 | 待运行 | 待运行 |
| G23 | 高波切反转 | 动量组合未来预测波动率 | 待运行 | 待运行 |
| G31 | 高波减仓 | SPY 历史波动率 | **下一优先级** | **下一优先级** |
| G32 | 高波减仓 | 动量组合历史波动率 | 待运行 | 待运行 |
| G33 | 高波减仓 | 动量组合未来预测波动率 | 待运行 | 待运行 |

`XS01` 单独研究个股历史波动率参与横截面选股，不与上述组合择时混合。

## 3. 已完成结果的作用

### G00

G00 是所有风控组唯一共同对照。代表性的 `mom_255_0 / Top20 / monthly` long-only 收益高于 SPY，但回撤与 Sharpe未达到部署门槛。WML 的 beta 接近零，却在本样本和成本下绝对收益较弱。

### G21

严格 Q4 direct reversal 对 long-only 的 36 个主场景没有任何最大回撤改善，且左尾普遍恶化；不再对 rev5/rev20、TopK 做事后寻优。相同机制对周频 WML 的 18/18 个场景同时改善 CAGR、Sharpe 和 MDD，证明论文机制主要作用于 loser/short 腿。

## 4. 当前执行顺序

1. **G31**：用 SPY RV21 的同一严格 Q4 状态做减仓/T-bill，保持普通状态满仓；
2. **G32、G33**：固定动作，只替换风险源；
3. **G11–G13**：比较全程连续缩放与仅在尾部行动的 beta 成本；
4. **G22、G23**：作为 WML 风险源诊断，不优先用于 long-only；
5. 只有通过机制门槛的组才进入 q85/q90/q95、确认规则、杠杆或预测器稳健性。

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
