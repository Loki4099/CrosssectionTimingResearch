# G21：SPY 历史波动率 Q4 切换反转——实验设计

状态：设计已冻结，`g21-frozen-v3-v1` 已在冻结 v3 数据上完成。结果见 [report.md](./report.md)。

## 研究问题

在 S&P 500 历史成分股横截面中，当 SPY 的短期已实现波动率进入自身历史分布的最高四分位时，把裸动量排序切换为短期反转，是否能够改善收益、Sharpe 和最大回撤？该问题分别在保留市场 beta 的 long-only 和 dollar-neutral WML 上检验。

## 冻结规则

- 数据：`sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate`；
- 评价期：2018-01-02 开盘至 2026-06-30 收盘，共 2,134 个 XNYS 交易日；
- 基线信号：`mom_255_0`、`mom_255_21`、`mom_12_1`；
- 组合宽度：Top/Bottom 10、20、50；频率：周、月；
- 状态变量：SPY 过去 21 个交易日的年化已实现波动率；
- 状态阈值：当前波动率与严格滞后一期的过去 756 个交易日分布比较，超过滚动 75% 分位数记为 Q4；
- Q1–Q3：继续使用原动量排序；Q4：切换为 5 日或 20 日反转得分 `-log(TR(t)/TR(t-L))`；
- 不设置滞后带、进入确认或退出确认；
- long-only：TopK 等权；long-short：TopK +50%、BottomK -50%，gross=1、net=0；
- 主成本：周频 10bps、月频 5bps；long-short 主场景另计 1% 年化借券费；
- 基准：SPY 总回报代理，不冒充官方 SPXTR。

共 `3 × 3 × 2 × 2 × 2 = 72` 条核心路径，其中最后两个维度分别为组合模式与反转窗口。计入成本及借券费压力场景后生成 576 个有效场景；72 个预先冻结的主场景用于正文判断。

## 事前判定原则

G21 不是在 rev5、rev20、TopK 中挑冠军。机制是否成立，主要看：

1. 相对同信号、同 K、同频率、同成本的 G00，CAGR、T-bill 超额 Sharpe 与最大回撤是否形成跨参数平台；
2. Q4 中反转相对裸动量是否同时改善均值和左尾，而非仅靠一次危机反弹抬高平均收益；
3. long-only 与 WML 的差异是否符合“市场 beta 与 loser/short 腿风险不同”的经济解释；
4. 成本和借券费压力下，增量机制是否仍存在。

部署门槛 `CAGR > SPY、Sharpe > 1、MDD < 25%` 继续报告，但机制失败的场景也完整保留。

## 产物

- 运行目录：`results/experiments/G21/runs/g21-frozen-v3-v1/`
- 机器配置：`config/experiments/G21.toml`
- 精简 Git 发布物：`results/published/G21/`

该实验是免费数据研究级证据，`formal_run_eligible=false`，不能作为实盘可成交性或机构级数据质量证明。
