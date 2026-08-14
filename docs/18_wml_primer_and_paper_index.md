# 多空 WML、当前 long-only 横截面策略与参考论文

最后更新：2026-08-14  
用途：在冻结 V5–V7 前统一研究对象、收益口径与论文映射

## 1. 一句话定义

WML 是 **Winner Minus Loser**：买入过去表现最强的股票组合，同时做空过去表现最弱的股票组合。

它与当前策略都属于横截面动量，因为两者都在同一时点比较一批股票的相对动量并排序。区别不是“横截面 versus WML”，而是同一个横截面信号被映射成了不同的投资组合：

- 当前策略：只持有排序最前面的赢家；
- WML：持有赢家，同时做空排序最后面的输家。

## 2. 从同一组排名到两种组合

设信号日股票按动量从高到低排序，赢家集合为 `W_t`，输家集合为 `L_t`。

### 2.1 当前项目的 long-only TopK

当前基线只买入前 K 名，并在每次调仓恢复等权：

`w_i,t = 1/K`，若 `i` 属于 `W_t`；否则为 0。

组合收益为：

`R_long-only,t = R_W,t`

其股票权重合计为 100%，因此：

- 净股票敞口约为 +100%；
- 总敞口约为 100%；
- 通常保留较高的市场 beta；
- 收益同时包含美股市场上涨和赢家相对强势两部分。

### 2.2 简化 WML

学术因子常写成：

`R_WML,t = R_W,t - R_L,t`

若赢家端权重合计 +100%、输家端权重合计 -100%，则：

- 净美元敞口为 0%；
- 总敞口为 200%；
- 名义上是 dollar-neutral，但不保证 beta-neutral、行业中性或风险中性；
- 收益主要描述赢家相对输家的表现。

“零成本组合”不代表不需要资金。真实实施仍需要保证金、抵押品、融资、借券额度和空头回补能力。

为了让总敞口与 long-only 的 100%一致，可以报告 gross=1 版本：赢家 +50%、输家 -50%，其收益为：

`R_WML,gross1,t = 0.5 × (R_W,t - R_L,t)`

在不考虑成本和融资时，固定乘以 0.5 不改变 Sharpe，却会改变波动率、回撤金额和成本占 NAV 的解释。因此后续必须同时注明 gross、net 和收益分母，不能只写“WML”。

## 3. 一个直观例子

假设下一期赢家组合上涨 10%，输家组合上涨 4%：

- long-only TopK：`+10%`；
- 学术原尺度 WML：`10% - 4% = +6%`；
- gross=1 WML：`0.5 × 6% = +3%`。

再假设市场从恐慌中猛烈反弹，防御型赢家上涨 5%，此前暴跌的输家上涨 30%：

- long-only TopK 仍盈利 `+5%`；
- 原尺度 WML 为 `5% - 30% = -25%`；
- gross=1 WML 为 `-12.5%`。

这说明同一个横截面排名下，long-only 可以赚钱，而 WML 同期发生“动量崩溃”。崩溃来自输家空头的亏损，不一定来自赢家股票下跌。

反过来，在市场继续下跌、赢家跌 15%、输家跌 30%时：

- long-only 为 `-15%`；
- 原尺度 WML 反而为 `+15%`。

所以两者可能在同一天方向相反，不能依据同一个“动量”名称直接比较 CAGR 或 MDD。

## 4. 为什么经典 WML 的崩溃比我们的策略严重

在经历大幅熊市后，横截面通常呈现：

- 赢家端：防御股、低 beta 股票或此前跌得较少的股票；
- 输家端：高 beta、周期股、金融股或濒临困境的股票。

WML 会买入前者并做空后者。当市场仍在下跌时，这种相对交易可能表现很好；一旦政策或流动性推动市场猛烈反弹，输家端具有类似期权的凸性，可能远快于赢家上涨。WML 因此在熊市后的反弹阶段遭遇巨大亏损。

Daniel–Moskowitz 将典型动量崩溃描述为：此前市场下跌、市场波动率高，而崩溃与随后市场反弹同时发生。这主要解释了 loser 空头为什么危险，而不是说所有 long-only 强势股组合都会以相同方式崩溃。

当前 TopK long-only 没有输家空头：

- 它可能在市场下跌时因 +100%净多头敞口而回撤；
- 它可能在反弹时跑输突然暴涨的输家；
- 但“跑输输家”只形成机会成本，不会像空头那样直接产生亏损。

这也是为什么经典 WML 可以出现极高波动和极深回撤，而我们的裸动量虽然 MDD 较大，却没有同等程度的动量 crash 结构。

## 5. 为什么这会改变波动率缩放结论

Barroso–Santa-Clara 缩放的是 WML 自身的风险。高风险时同步压低赢家多头和输家空头，可以显著减少熊市反弹中 loser 空头造成的巨亏。

对当前 long-only TopK 做同样缩放则不同：

- 减少的是整个股票多头和市场 beta；
- 高波后若出现强劲市场反弹，策略会因低仓位错过收益；
- 原论文规避的 loser 空头亏损在我们这里原本就不存在；
- 因此能够降波动和 MDD，并不保证提高 Sharpe。

这与 V4 的结果一致：波动率预测有效，但 15%目标使平均股票仓位约为 60%–63%，而低仓位后经常出现很高的 long-only 动量收益，最终收益下降快于风险。

因此，V4 没有反驳论文；它说明论文机制不能从 WML 原封不动移植到一个风险结构不同的 long-only 组合。

## 6. 两者的系统性区别

| 维度 | 当前 S&P 500 TopK long-only | 多空 WML |
|---|---|---|
| 信号类型 | 横截面动量 | 横截面动量 |
| 排名后动作 | 只买赢家 | 买赢家、做空输家 |
| 净敞口 | 约 +100% | 通常 0% |
| 总敞口 | 100% | 原尺度通常 200%；gross=1版本为100% |
| 市场 beta | 通常较高且为正 | dollar-neutral，不保证 beta-neutral；beta可随状态变化 |
| 主要收益来源 | 市场上涨 + 赢家选择 | 赢家相对输家的价差 |
| 典型危机风险 | 股票市场下跌、集中持仓 | 熊市反弹时输家空头暴涨 |
| 防守后的机会成本 | 少持有股票、错过市场反弹 | 同时减少多空两腿，规避空头挤压 |
| 实施难点 | 交易、集中度、换手 | 另加借券、空头费、保证金、融资和无限亏损风险 |
| 合理基准 | SPY/S&P 500 TR | T-bill/零收益及因子回归；不能简单以 SPY CAGR 为唯一基准 |
| MDD 可比性 | 基于全额投资 NAV | 取决于 gross、抵押现金与融资会计；不注明口径就不可比 |

## 7. “经典 French WML”也不等于简单 Top50 − Bottom50

Kenneth French 官方美国 Mom 因子采用：

1. NYSE、AMEX、NASDAQ 股票；
2. prior (2–12) return，即跳过最近一个月；
3. 按 NYSE 市值中位数分为 Small/Big；
4. 按 NYSE 动量 30/70 分位分为 Low/Neutral/High；
5. 六个交叉组合内部市值加权；
6. `Mom = 1/2(Small High + Big High) − 1/2(Small Low + Big Low)`。

Barroso–Santa-Clara 使用的是 Kenneth French 的长历史 WML/Mom 收益，不是当前项目的 S&P 500、TopK、等权、next-open 可投资组合。

如果我们构造 `S&P 500 Top50 − Bottom50`，它只能称为 **本项目的 WML bridge**：可以帮助定位 long leg 与 short leg 的风险来源，但不能冒充 French Mom 的严格复现。

## 8. WML 与反转策略也要区分

`−WML` 是把同一个中期动量排名完全翻转：买中期输家、空中期赢家。短期反转因子通常使用最近一周或一个月的收益重新排名，因此不一定等于 `−WML`。

本项目 V3 的 5/20 日反转则是：

- 只买最近 5/20 日跌幅最大的股票；
- 不做空近期赢家；
- 仍保持约 +100%市场敞口。

所以 V3 从 TopK 动量切到 long-only 反转，并不是论文中多空 MOM 与多空 REV 的精确切换。它会在恐慌初期集中买入高 beta 输家，却没有空头腿对冲市场，这也是 MDD 恶化的重要原因之一。

## 9. 后续研究中 WML 的正确角色

本项目仍以 long-only 为主。WML 只承担“机制显微镜”的角色：

1. 用相同 PIT S&P 500 股票池构造 winner、loser 和 winner-minus-loser 三条收益序列；
2. 分别计算 126 日波动率，判断可预测性主要来自哪一腿；
3. 比较缩放前后 winner long、loser short 与 WML 的危机贡献；
4. 明确使用月频、Top50/Bottom50 等权；
5. 同时报原尺度 `R_W-R_L` 和 gross=1 的 `0.5(R_W-R_L)`；
6. WML 仅做研究诊断，不进入个人 long-only 部署候选，也不与 SPY CAGR直接决胜。

这项桥接实验能够回答一个决定后续方向的问题：

> 若 126 日波动率缩放只明显改善 loser short 或 WML，而不改善 winner long，那么原论文机制成立，但不适合直接作为当前 long-only 策略的收益增强层。

## 10. 核心参考论文与入口

### A. 风险管理动量

Pedro Barroso and Pedro Santa-Clara, **Momentum Has Its Moments**, Journal of Financial Economics 116(1), 2015, 111–120.

- [DOI / 期刊入口](https://doi.org/10.1016/j.jfineco.2014.11.010)
- [作者所在机构的论文信息页](https://ciencia.ucp.pt/en/publications/momentum-has-its-moments/)
- [作者主页：论文、复现包与 errata 入口](https://sites.google.com/site/pedromsbarroso/)

与本项目的关系：使用动量 WML 自身过去约六个月的实现波动率管理下一期 WML 敞口，是 V4 和拟议 V5 的理论来源。

### B. 高波动状态下的动量/反转切换

Hilal Anwar Butt, James W. Kolari and Mohsin Sadaqat, **Market Volatility, Momentum, and Reversal: A Switching Strategy**, Journal of Asset Management 25, 2024, 460–478.

- [期刊全文入口与附录概要](https://link.springer.com/article/10.1057/s41260-024-00372-1)
- [SSRN 工作论文入口](https://ssrn.com/abstract=4342008)

与本项目的关系：用市场实现波动率的历史分位区分状态，仅在高波状态由动量转向反转，是 V3 与拟议 V6/V7 的主要灵感。

### C. 动量崩溃的状态机制

Kent Daniel and Tobias J. Moskowitz, **Momentum Crashes**, Journal of Financial Economics 122(2), 2016, 221–247.

- [NBER 论文页与 PDF 入口](https://www.nber.org/papers/w20439)

与本项目的关系：解释为什么动量崩溃往往出现在市场已经下跌、高波动、随后强劲反弹的组合状态；支持将“恐慌阶段”和“反弹阶段”分开。

### D. 官方 Mom/WML 构造与公开收益序列

- [Kenneth French：美国月度 Momentum Factor 构造](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/Data_Library/det_mom_factor.html)
- [Kenneth French：美国日度 Momentum Factor 构造](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/Data_Library/det_mom_factor_daily.html)
- [Kenneth French Data Library](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html)

用途：作为 WML 口径和长历史公开因子收益的权威对照，不替代当前项目的 PIT S&P 500 可投资回测。

## 11. 当前待确认事项

在冻结 V5–V7 前，需要确定 WML bridge 的定位，而不是决定是否实盘做空：

- 建议定位：仅用两条月频 Top50/Bottom50 组合做机制诊断；
- 不建议定位：把 Top10/20/50、周/月、三个动量定义全部扩成新的多空策略网格；
- long-only 的 18 条主路径保持不变；
- WML 结论单独报告，不与 long-only 混在同一冠军排名中。

