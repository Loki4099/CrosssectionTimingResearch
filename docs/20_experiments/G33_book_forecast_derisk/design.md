# G33：裸动量账簿 EWMA 预测波动率严格 Q4 减仓——实验设计

状态：**设计已冻结；尚未运行。** 本设计在任何 G32 结果产生前冻结，建议首个不可变运行 ID 为 `g33-frozen-v3-v1`。本文冻结研究问题、裸账簿定义、EWMA 预测公式、因果口径、判定规则与输出契约；运行后不得原地修改本设计以迁就 G32 或 G33 结果。机器参数以该运行内的 `config_resolved.toml` 为准。G33 完成并通过全部验收前不得创建 `report.md`，也不得把 G31、G32、legacy 结果或迁移中的文件当作 G33 结果。

## 研究问题与预注册假设

G31 使用 SPY RV21，G32 使用匹配裸动量账簿的 RV126；两者保持同一个严格 Q4 减仓动作。G33 再保持减仓动作、缩放函数、横截面排序、执行和会计不变，只把风险源替换为同一匹配裸账簿收益的因果 EWMA 21-session 波动率预测。这样，三组之间唯一计划变化是风险源；G33 的模型、参数和结论门槛不允许根据 G32 结果选择或修改。

预注册假设如下：

1. **H1（主要假设）**：相对同信号、同 K、同频率、同模式、同成本和同借券费的 G00，基于裸账簿 EWMA 预测波动率的 Q4 减仓应在 long-only 主场景中形成最大回撤和 T-bill 超额 Sharpe 的跨参数改善，而不是只改善一个事后挑选的组合。
2. **H2（收益代价）**：减仓可能牺牲 CAGR 和市场 beta；是否值得由 Sharpe、左尾、回撤及部署联合门槛共同判断，不能只凭回撤变小判定成功。
3. **H3（机制区分）**：long-short/WML 同步缩放用于区分市场 beta 风险与 winner/loser 腿风险。若改善主要出现在 long-only，证据偏向市场 beta 管理；若 WML 也稳定改善，说明风险动作还作用于横截面价差。WML 不能用来挽救失败的 long-only 主要假设。
4. **H4（风险源解释）**：G33 完成后，可以把 G31、G32、G33 的同场景结果作描述性风险源比较；但 G33 的模型、状态、仓位、参数和正式判断绝不使用 G32 结果。只有某一组相对 G00 通过相同 H1，才可称为该风险源对冻结减仓动作提供跨参数支持；任意组间相对改善但未通过 H1 只能记为局部风险源证据。

H1 只有在 18 个 long-only 主场景中至少 12 个同时满足 `delta Sharpe > 0` 与 `delta MDD > 0`，且周频、月频各至少 5/9 个同时满足，并且两项 delta 的全体中位数均严格为正时，才称为“跨参数平台支持”。这里正式 delta 始终为 `G33 - G00`；MDD 以负数保存，所以 `delta MDD = MDD_G33 - MDD_G00 > 0` 表示回撤改善。未达到该标准即记为失败或局部证据，不能改用最佳单点、组间差值或某个危机窗口改写结论。

## 冻结范围

- 数据版本：`sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate`；冻结记录 SHA256 为 `a3ef9ee72cd3d535c2e5bf06b3d1f520c54667a8552891543ee0f9ca50488296`，其锁定的数据 manifest SHA256 为 `65b628d604f7e2f456e8d1d43a3c3e88b6bd3e86cc1c9455cdcfe28b856a3ec7`。
- 数据状态仍为 `review / free_research_candidate`，运行必须显式允许 review 数据，并永久保留 `formal_run_eligible=false` 及全部 formal blockers。
- 股票池：历史时点 S&P 500 成分股，使用冻结 v3 canonical SID；策略层排除清单为空。
- 裸账簿收益与 EWMA 从 `program.toml` 冻结的 `strategy_forecast_history_start = 2014-06-30` 开始生成和初始化；不得用评价期开始后的数据反填此前历史，也不得缩短历史后默认满仓。
- 正式评价期与 G00 完全一致：2018-01-02 开盘至 2026-06-30 收盘，预期 2,134 个 XNYS 交易日；首日从现金在开盘部署，首日开盘至收盘收益和初始交易成本都进入正式绩效统计。2014-06-30 至 2017-12-29 只服务于严格因果的风险源 warm-up，不进入 G33 正式绩效。
- 基准：SPY 总回报代理与冻结日度 T-bill。SPY 不能冒充官方 S&P 500 Total Return 指数。
- 动量：`mom_255_0`、`mom_255_21`、`mom_12_1`；K 为 10、20、50；频率为周、月；模式为 long-only、long-short。
- 排名、PIT 过滤、计划调仓日、公司行动和缺价口径与对应 G00 完全一致。G33 不加入反转窗口、阈值搜索、确认期、滞后带、仓位下限、杠杆、机器学习或模型选择。
- G33 没有 legacy alias。G31/G32 仅可在 G33 完成后进入结果报告的风险源解释层，不得复用其状态、仓位、NAV、参数或结论。

## 每条核心路径的裸账簿定义

令 $j$ 唯一标识一个 `signal × K × frequency × portfolio_mode` 核心路径。每条 G33 核心路径必须先独立生成一条与其匹配的、未叠加任何风控的 G00 裸账簿日收益序列 $r^{book}_{j,t}$：

- 使用该路径对应的 G00 动量排名、TopK/BottomK、调仓频率、目标权重、下一开盘执行、PIT 过滤、公司行动及缺价/跳过规则；两次成功调仓之间按实际证券收益自然漂移；
- long-only 的未缩放目标为 TopK 等权、gross=net=1；long-short 的未缩放目标为 winner long `+0.5`、loser short `-0.5`、gross=1、net=0；
- 交易成本固定为 0bps，借券费固定为 0%，现金与卖空抵押的利息固定为 0；未成交或暂留现金的日收益也固定为 0；
- 不允许使用 T-bill 收益、SPY 收益、G31/G32/G33 缩放后收益、任一非零成本或非零借券费场景来构造 $r^{book}_{j,t}$；
- 同一路径的 4 个成本场景及 long-short 的 3 个借券费场景必须共享完全相同的 $r^{book}_{j,t}$、EWMA 状态与 $a_{j,t}$；成本或借券费不得反向改变风险信号；
- 从 2014-06-30 起按权威 XNYS 日历生成连续有限收益。若冻结数据、PIT 账簿或执行规则无法在首个正式信号收盘前提供完整 EWMA 及 756 个严格滞后预测波动率，运行必须失败，不能补零、前向填充、改用 SPY、使用未来数据或默认 $a=1$。

裸账簿历史是 G33 风险信号的因果输入，不是额外报告场景，也不计入 36 条核心路径或 288 个正式场景。正式 G00 bundle 仍用于哈希锚定、执行一致性和绩效比较；由于其 NAV 从 2018-01-02 开始，不能单独替代上述从 2014-06-30 开始的风险历史生成。

## 因果 EWMA 21-session 预测

对核心路径 $j$，令 $r^{book}_{j,t}$ 为交易日 $t$ 收盘时已完全可见的裸账簿日收益。唯一允许的预测器是 `forecast_model = ewma`、衰减系数 $\lambda=0.94$、`adjust=False`。令 $t_0$ 为 2014-06-30，递推方差严格冻结为

\[
v_{j,t_0}=(r^{book}_{j,t_0})^2,
\]

\[
v_{j,t}=\lambda v_{j,t-1}+(1-\lambda)(r^{book}_{j,t})^2,
\qquad \lambda=0.94,
\]

这与对日平方收益调用 pandas `ewm(alpha=0.06, adjust=False).mean()` 等价。初始化不得使用 2014-06-30 之前的估计、全样本方差或回看优化；任一输入收益、递推方差缺失、非有限或方差非正时必须拒绝运行。

预测期固定为下一 21 个 XNYS sessions，并冻结为条件日方差恒定：

\[
\widehat{Var}_{t}(r^{book}_{j,t+h})=v_{j,t},
\qquad h=1,\ldots,21.
\]

因此 21-session 累计预测方差和用于分位数状态的年化预测波动率分别为

\[
\widehat V^{(21)}_{j,t}=21v_{j,t},
\qquad
\widehat\sigma^{(21)}_{j,t}
=\sqrt{\frac{252}{21}\widehat V^{(21)}_{j,t}}
=\sqrt{252v_{j,t}}.
\]

当前收盘收益可以进入 $v_{j,t}$，因为 G33 只在该收盘后计算信号并于下一交易日开盘执行。未来 21 日真实收益不进入预测、分位数、状态、缩放、模型选择或验收。

G33 **绝不读取、计算或使用 `actual_future_volatility` 作为任何策略输入、训练标签、模型选择指标或状态诊断**。禁止 persistence/HAR、其他 $\lambda$、其他 horizon、滚动拟合、损失函数比较、挑选最优预测器或根据 G32 结果切换规则。EWMA(0.94)、`adjust=False`、21-session 恒定方差预测是唯一预注册模型。

## 严格滞后分位数与 Q4 缩放

阈值历史严格排除当前预测值。对 $p\in\{0.25,0.50,0.75\}$，

\[
q_{p,j,t}=Q_p(\widehat\sigma^{(21)}_{j,t-756},\ldots,
\widehat\sigma^{(21)}_{j,t-1}),
\]

其中必须有完整 756 个历史交易日的有限、严格为正预测波动率。分位数采用与 pandas rolling quantile 一致的线性插值，不做填充、四舍五入或扩大窗口。四分位边界冻结为：

\[
Q1:\widehat\sigma^{(21)}_{j,t}\le q_{.25,j,t};\quad
Q2:q_{.25,j,t}<\widehat\sigma^{(21)}_{j,t}\le q_{.50,j,t};
\]

\[
Q3:q_{.50,j,t}<\widehat\sigma^{(21)}_{j,t}\le q_{.75,j,t};\quad
Q4:\widehat\sigma^{(21)}_{j,t}>q_{.75,j,t}.
\]

在该路径计划信号收盘 $t$ 计算下一开盘使用的风险缩放：

\[
a_{j,t}=
\begin{cases}
1, & \widehat\sigma^{(21)}_{j,t}\le q_{.75,j,t},\\
\min(1,q_{.75,j,t}/\widehat\sigma^{(21)}_{j,t}),
& \widehat\sigma^{(21)}_{j,t}>q_{.75,j,t}.
\end{cases}
\]

因此 Q4 中必有 $0<a_{j,t}<1$，Q1–Q3 中 $a_{j,t}=1$。不存在日内数据、未来收益、未来分位数、进入/退出确认或 hysteresis。若任一计划信号日的裸账簿历史、EWMA、预测波动率、分位数或比例缺失、非有限或非正，整次运行必须 fail closed；这里的 fail closed 指拒绝生成 completed bundle，而不是把风险仓位设为 0、默认满仓或沿用旧状态。

状态可以逐日生成用于审计，但**只在该核心路径的周/月计划信号收盘取样**；$a_{j,t}$ 于下一 XNYS 交易日开盘随正常调仓生效，并保持到下一次成功调仓。两次调仓之间不因每日状态变化而盘中或日末再缩放。G33 不要求不同信号、K、频率或模式具有相同状态；只要求每条路径因果可复算，并在其全部成本/借券费场景中逐位相同。

## 持仓与现金公式

令 $W_{j,t}$ 为信号日 PIT 股票池内按冻结动量得分选出的 TopK，$L_{j,t}$ 为 BottomK；两者必须各有 K 个有效 SID 且无重叠。基础权重与同路径 G00 相同。

Long-only 在下一开盘的目标权重为

\[
w_{i,j,t}^{LO}=\begin{cases}a_{j,t}/K,&i\in W_{j,t},\\0,&\text{otherwise},\end{cases}
\qquad c_{j,t}^{LO}=1-a_{j,t}.
\]

目标 long、short、gross、net 分别为 $a_{j,t},0,a_{j,t},a_{j,t}$。正式 G33 中未配置现金按冻结日度 T-bill 收益复利；这与生成风险信号时冻结为零现金利息的裸账簿严格区分。

Long-short/WML 在下一开盘同时、同比例缩放两腿：

\[
w_{i,j,t}^{LS}=\begin{cases}
+a_{j,t}/(2K),&i\in W_{j,t},\\
-a_{j,t}/(2K),&i\in L_{j,t},\\
0,&\text{otherwise}.
\end{cases}
\]

其目标 long、short、gross、net 分别为 $a_{j,t}/2,a_{j,t}/2,a_{j,t},0$。在正式 G33 的 collateralized accounting 下，目标会计现金权重为 $1-\sum_iw_i=1$，包含卖空抵押并全额赚取 T-bill；降低 gross 不得错误地再叠加一份 `1-a` 现金。所有目标权重相对于扣除当次成本后的 NAV 建仓，实际敞口随后随价格漂移，直到下次成功调仓。

long-short 的 gross=1 collateralized NAV 是唯一可投资样式主路径。传统 gross=2 只按

\[
R^{factor,gross2}_d=2\bigl(R^{collateralized,gross1}_d-r^f_d\bigr)
\]

派生算术均值、波动率和 Sharpe 诊断；它不生成 NAV、CAGR 或额外策略/场景，也不允许据此宣称可部署。

## 路径、场景与主口径

G33 没有组内 variant 轴，共

\[
3\text{ signals}\times3\text{ K}\times2\text{ frequencies}\times2\text{ modes}=36
\]

条核心持仓路径。

| 模式 | 核心路径 | 成本场景 | 借券费场景 | 报告场景 |
|---|---:|---:|---:|---:|
| Long-only | 18 | 0/5/10/20bps | 0% | 72 |
| Long-short | 18 | 0/5/10/20bps | 0%/1%/3%年化 | 216 |
| 合计 | 36 | — | — | 288 |

主场景固定为周频 10bps、月频 5bps；long-only 借券费为 0%，long-short 借券费为 1% 年化。因此恰有 36 个主场景。所有 288 个场景必须保留且身份唯一，成本或借券费只是同一信号/目标路径的会计压力场景，不能增加核心路径数或改变选股、裸账簿、EWMA、状态与风险缩放。

## 执行、成本、借券与公司行动

- 信号在计划期末最后交易日收盘生成，下一 XNYS 交易日开盘成交。公司行动先在开盘前按冻结账本处理，再进行策略调仓。
- 开盘前权重记为 $w^-_{i,u}$，目标为 $w^*_{i,u}$。双边 L1 换手与成本为

  \[
  L1_u=\sum_i|w^*_{i,u}-w^-_{i,u}|,\qquad
  Cost_u=NAV^-_u\,(bps/10000)\,L1_u.
  \]

  `one_way_turnover=L1/2`；年化 L1 换手为 `sum(L1) * 252 / NAV observations`。成本必须覆盖选股变化和 $a_{j,t}$ 变化，不能只对证券替换收费。
- 每条核心路径必须先生成一条零交易成本正式事件路径，再以同一目标、状态和换手路径精确回放 0/5/10/20bps；回放后 NAV、成本金额和 pretrade NAV 必须自洽。
- 年化借券费 $b\in\{0,0.01,0.03\}$ 转为日费率 $f=(1+b)^{1/252}-1$，每天按各空头收盘市值绝对值扣除。主 WML 使用 1%；免费数据不提供真实逐券费率、可借量或召回，必须作为限制报告。
- 正式 G33 的现金（含卖空抵押）每天按冻结 T-bill 序列计息；借券费在收盘空头市值上扣除。首日开盘部署成本不得从收益序列中遗漏。风险源裸账簿的现金利息与借券费仍固定为零。
- Long-only 延续冻结 G00 的 `leave_cash` 缺开盘价口径：可成交目标按原目标权重执行，未成交目标额度留在现金，并完整记录 SID 与数量。
- Long-short 任一目标或仍在当期 PIT 股票池的已有仓位缺开盘价时，整次双腿调仓跳过、保持完整原账簿、L1 换手为零，并记录 `skipped_signed_missing_open`。已有仓位若等待冻结账本中未来不超过 25 个权威交易日的公司行动，记录 `skipped_pending_corporate_action` 并保留到行动日。
- 已退出 PIT 股票池的已有仓位缺开盘价，只能按冻结公司行动处理，或用此前不超过冻结上限 5 个权威交易日的最后有效收盘显式清算，记录 `executed_with_terminal_last_close`、SID 与回退日期。不得单腿、部分或静默成交；任何跳过后必须在后续计划调仓恢复。

## G00 正式对照与风险源解释层

唯一正式对照是同一冻结数据上已完成且产物哈希全部通过的 `g00-frozen-v3-v1`。G33 启动前必须验证 G00 manifest 状态、group id、dataset version、dataset manifest SHA256 与每个列入 manifest 的文件哈希。每个 G33 场景按 `signal × K × frequency × portfolio_mode × cost_bps × borrow_fee_annual` 一一映射到 G00；除风险缩放外不得改变任何正式绩效规则。

`comparison.csv` 对全部 288 个场景固定报告以下五项 `G33 - G00`：CAGR、T-bill 超额 Sharpe、最大回撤、年化波动和年化 L1 换手，共应有 `288 × 5 = 1,440` 行。正文判断只用 36 个预注册主场景，并同时给出 18 个 long-only 的全体、按信号、K 与频率分组结果；不得先看结果再删参数。

G31/G32 不属于 G33 的运行输入或正式 reference。G33 completed bundle 验收后，最终报告可以对三种风险源的同键主场景固定展示 CAGR、T-bill 超额 Sharpe、最大回撤、年化波动、beta，以及共同信号日 Q4 重叠、$a$ 相关性和危机窗口进入/退出时点。该解释表不写入正式 `comparison.csv`，不改变其 1,440 行契约，不生成新的成本场景，也不设立替代 H1 的第二通过门槛。

判定分为三层：

1. **运行有效**：满足下文所有数据、因果、会计和产物验收；否则没有经济结论。
2. **机制成立**：long-only 满足 H1 的跨参数平台规则；若只在单一危机、单一频率或少数组合改善，只能记为局部证据。
3. **数值部署候选**：某个有效 long-only 主场景必须同时严格满足 `CAGR > SPY CAGR`、`Sharpe_excess_rf > 1`、`max_drawdown > -0.25`（即回撤绝对值小于 25%）。逐项列出全部通过与未通过场景；单点通过不等于平台成立。

即使数值门槛通过，本数据的 `formal_eligible=false` 仍禁止把它称为实盘可部署策略；只有使用机构级 PIT 永久证券标识、官方总回报基准、真实交易成本与借券数据重新验收后，才可能解除该治理门禁。WML 不要求 CAGR 超过 SPY，但必须报告绝对收益、Sharpe、借券费、换手、容量和 beta；它始终是机制诊断。

## 指标与预注册诊断

每个场景必须报告完整样本的 total return、CAGR、年化波动、zero-RF 与 T-bill 超额 Sharpe、Sortino、最大回撤、最长回撤持续期和 Calmar；相对 SPY 报告 beta、zero-RF/超额 alpha、tracking error、information ratio、算术与几何超额收益。还必须报告目标与实际 long/short/gross/net、平均/最小/最大 $a_{j,t}$、低于满仓的调仓比例、Q4 调仓次数、L1 换手、总成本、总借券费、现金收益、公司行动、估值回退与缺价执行事件。

条件诊断只使用主场景，并冻结如下口径：

- 按信号日 Q1–Q4 分组，同时对 G33 和配对 G00 计算从本次成功执行到下一计划执行前 pretrade NAV 的非重叠持有期收益；报告事件数、均值、中位数、胜率、ES10、ES5 和最差值。跳过执行的事件不混入收益，但必须单列数量、日期和原因。
- 对 Q4 报告 $\widehat\sigma^{(21)}_{j,t}/q_{.75,j,t}$ 与 $a_{j,t}$ 的最小值、分位数、均值，连续 Q4 episode 的进入/退出日期和长度，以及 G33 相对 G00 的风险资产 P&L、T-bill、成本和借券费差异。归因总和必须与 NAV 差异在数值容差内一致。
- Long-short 额外分解 winner long、loser short、抵押现金与借券费贡献；不得只展示合成 WML。
- 危机窗口固定为：2018 Q4 抛售 `2018-09-21` 至 `2018-12-24`；COVID 下跌 `2020-02-19` 至 `2020-03-23`；COVID 反弹 `2020-03-24` 至 `2020-06-30`；2022 紧缩熊市 `2022-01-03` 至 `2022-10-12`。每个窗口对 G33 与 G00 报告累计收益、年化波动、最大回撤、最差日、beta、平均/最小 $a_{j,t}$、Q4 日数与调仓数、换手、成本、借券费及各腿贡献；G33 完成后才可按上一节冻结的字段与 G31/G32 比较风险源时点。

这些危机日期是完整样本内的描述性切片，不参与信号，也不作为独立通过门槛。必须完整报告全部窗口；若总体改善只来自一个危机，不能包装成普遍机制。

## 运行验收

建议首个运行 ID 为 `g33-frozen-v3-v1`。正式写出 completed bundle 前必须同时满足：

1. 本地运行区、逐文件 SHA256 验收、`runtime-status`、冻结数据加载门禁及 G00 reference 完整性检查均通过；G33 的大型输入和输出只使用本地运行根，不回写 OneDrive。
2. `program.toml`、`G33.toml`、本设计、冻结记录、数据 manifest 和 G00 manifest 哈希均写入 provenance；任何数据、裸账簿定义、EWMA、horizon 或规则变化都要求新 dataset version 或新预注册设计，不能沿用本 run id。
3. 每条匹配核心路径从 2014-06-30 起生成的裸账簿均为零成本、零借券费、零现金利息且未缩放；与成本/借券费场景完全解耦。首个正式信号收盘必须具有已从冻结起点递推的有限 EWMA 和严格排除当前值的 756 个预测波动率历史。
4. `lambda=0.94`、`adjust=False` 初始化、平方收益递推、21-session 恒定方差预测、`sqrt(252)` 年化、严格滞后 756 日分位数、Q1–Q4 与 $a_{j,t}$ 有独立因果测试；逐日结果必须与直接递推及 pandas `ewm` 在数值容差内一致。
5. 扰动信号收盘后的任意收益不得改变该信号的 EWMA、预测、状态或目标；实现不得读取 `actual_future_volatility`、G32 结果或候选模型表现。任何缺失、非有限或非正值必须拒绝 completed bundle。
6. Q1–Q3 的目标权重逐位等于 G00；Q4 只允许乘以同一标量 $a_{j,t}$。在数值容差内，long-only 的 gross 和 net 均为 $a_{j,t}$，long-short 的 long/short/gross/net 为 $a_{j,t}/2,a_{j,t}/2,a_{j,t},0$，TopK/BottomK 无重叠且无杠杆。
7. 恰有 36 条核心路径、288 个唯一有效场景、36 个主场景和 1,440 行配对 G00 比较；long-only/long-short 场景分别为 72/216。同一核心路径跨成本/借券费的裸账簿、EWMA、状态与目标必须逐位一致，不得因负收益或门槛失败删除场景。
8. 每个场景覆盖完全相同的 2,134 个评价交易日，预计 NAV 共 `288 × 2,134 = 614,592` 行；日期严格递增、NAV 正且有限、日收益可由 NAV 复算，首日成本已包含。风险 warm-up 不得混入正式 NAV 或绩效。
9. 成本回放与零成本正式事件路径一致；T-bill、借券费、现金、gross/net、L1 换手及 P&L 归因逐日闭合。所有公司行动、缺价、跳过、terminal last-close 和后续恢复通过 G00 同等级审计。
10. `summary.csv`、`comparison.csv`、`config_resolved.toml`、`manifest.json` 以及 `artifacts/nav.parquet`、`rebalances.parquet`、`holdings.parquet`、`trades.parquet`、`diagnostics.parquet` 齐全，schema 合法；manifest 列出的相对路径、字节数和 SHA256 全部复核一致。只有满足这些条件，manifest 才能标记 `status=completed`。

## 失败解释纪律

- **数据/实现失败**：哈希、裸账簿 warm-up、EWMA 初始化/递推、因果历史、状态一致性、权重、场景数、NAV、会计闭合或执行审计任一失败，都只能记为无效运行；修复后用新 immutable run 重跑，不能解释经济机制。
- **机制失败**：运行有效但未达到 H1 平台规则，G33 long-only 即为失败或局部证据。WML 成功、相对 G31/G32 改善、最佳单点通过或某次危机改善都不能改写该结论。
- **处理强度弱**：若 Q4 的 $a_{j,t}$ 大多接近 1，应解释为冻结 `q75/forecast_sigma` 映射产生的弱 treatment，而不是据此声称“所有预测减仓规则无效”。仍不得在本运行中事后加入仓位下限或改阈值。
- **时点失败**：若 EWMA 预测在主要跌幅发生后才进入 Q4，或退出/再进入造成损失，应报告严格因果的识别滞后和 episode 路径，不能用未来信息重标状态。
- **预测边界**：EWMA 是冻结的规则型条件方差预测，不因其名为 forecast 而获得额外因果地位。禁止用真实未来 21 日波动率筛选日期、调参、替换模型或重写结论。
- **风险源解释边界**：组间相对改善但未通过 H1，只能称为风险源局部改善；不得用 G32 结果事后更改 G33，也不得删去任一已冻结风险源的失败。
- **经济失败**：若回撤改善但 CAGR、Sharpe、成本或 beta 代价过大，应明确为保险成本过高；若零成本有效而主成本失效，应明确为换手/实施失败。
- q85/q90/q95、确认/滞后规则、其他 $\lambda$、其他 horizon、其他预测模型或仓位下限只能在 G33 报告完成后另行预注册并全部报告，不得覆盖或删除 G33 原结果。

## 输出治理

完整 immutable bundle 应写入本地运行区的 `results/experiments/G33/runs/g33-frozen-v3-v1/`。运行前不得创建空 `report.md`；只有 completed bundle 通过全部验收后，才能创建 `docs/20_experiments/G33_book_forecast_derisk/report.md` 并据此撰写结果。报告必须同时保留成功、失败、异常和全部压力场景，并明确研究层级。

Git/OneDrive 只允许在验收后发布精简物：`results/published/G33/` 下的 `summary.csv`、`comparison.csv`、`config_resolved.toml` 与 `manifest.json`，以及最终小型文档。日度 NAV、裸账簿历史、EWMA/状态缓存、持仓、交易、完整数据和其他大型产物只留在本地运行区，不得复制回 OneDrive 或提交 Git。bundle 不得原地覆盖；重跑必须使用新 run id，并保留旧 run 及其哈希链。
