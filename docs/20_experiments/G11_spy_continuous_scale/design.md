# G11：SPY 历史波动率连续 15% 目标缩放——实验设计

状态：**设计已冻结；尚未运行。** 建议首个不可变运行 ID 为 `g11-frozen-v3-v1`。本文在任何冻结 v3 的 G11 输出、正式 bundle 或结果报告产生前，冻结研究问题、唯一风险动作、因果口径、判定规则、会计与输出合同；运行后不得原地修改本设计以迁就结果。机器参数以运行内的 `config_resolved.toml` 为准。G11 完成并通过全部验收前不得创建 `report.md`，也不得把 legacy alias、旧数据结果或 G31 结果当作 G11 结果。

## 研究问题与预注册假设

G31–G33 只在严格 Q4 尾部状态降低风险仓位，三种冻结风险源都没有让 long-only 通过相同的平台门槛。G11 保持 G31 的 SPY RV21 风险源、横截面排序、执行与会计不变，只把“严格 Q4 才减仓”替换为无杠杆的连续 15% 目标缩放，用来检验全程控制风险是否能以更平滑的仓位路径改善风险调整收益，以及这种动作会付出多少市场 beta 与反弹收益。

系统计划只冻结了下一阶段为 **G11–G13**，没有冻结三组内部顺序。本次在任何 G11 冻结 v3 结果产生前按组号先执行 G11，是当前阶段的前瞻执行决定；不得追溯声称旧计划早已冻结 G11 优先，也不得据本次结果改变 G12/G13 的规则。

预注册假设如下：

1. **H1（主要假设）**：相对同信号、同 K、同频率、同模式、同成本和同借券费的 G00，SPY RV21 连续缩放应在 long-only 主场景中形成最大回撤和 T-bill 超额 Sharpe 的跨参数改善，而不是只改善一个事后挑选的组合。
2. **H2（beta 与反弹代价）**：连续缩放会在严格 Q4 之外也降低股票仓位，可能进一步压低年化波动、回撤和 beta，也可能牺牲 CAGR 与高波后的反弹收益。是否值得必须由 Sharpe、左尾、回撤、beta、危机归因与部署联合门槛共同判断，不能只凭实现波动率更低判定成功。
3. **H3（模式区分）**：long-short/WML 同步缩放用于区分市场 beta 风险与 winner/loser 腿风险。若改善主要出现在 long-only，证据偏向市场 beta 管理；若 WML 也稳定改善，说明连续动作还作用于横截面价差。WML 不能用来挽救失败的 long-only H1。
4. **H4（动作解释）**：G11 completed bundle 通过验收后，才可把 G11 与已完成 G31 的同键主场景作“同一 SPY RV21 风险源、不同风险动作”的描述性比较。只有 G11 相对 G00 通过 H1，才可称为连续动作对 long-only 提供跨参数支持；`G11 - G31` 更好但 G11 未通过 H1，只能记为动作层的局部改善。

H1 只有在 18 个 long-only 主场景中至少 12 个同时满足 `delta Sharpe > 0` 与 `delta MDD > 0`，且周频、月频各至少 5/9 个同时满足，并且两项 delta 的全体中位数均严格为正时，才称为“跨参数平台支持”。正式 delta 始终为 `G11 - G00`；MDD 以负数保存，所以 `delta MDD = MDD_G11 - MDD_G00 > 0` 表示回撤改善。未达到该标准即记为失败或局部证据，不能改用最佳单点、G31 差值、某个成本档或某次危机窗口改写结论。

## 冻结范围

- 数据版本：`sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate`；冻结记录 SHA256 为 `a3ef9ee72cd3d535c2e5bf06b3d1f520c54667a8552891543ee0f9ca50488296`，其锁定的数据 manifest SHA256 为 `65b628d604f7e2f456e8d1d43a3c3e88b6bd3e86cc1c9455cdcfe28b856a3ec7`。
- 数据状态仍为 `review / free_research_candidate`，运行必须显式允许 review 数据，并永久保留 `formal_run_eligible=false` 及全部 formal blockers。
- 股票池：历史时点 S&P 500 成分股，使用冻结 v3 canonical SID；策略层排除清单为空。
- 正式评价期与 G00 完全一致：2018-01-02 开盘至 2026-06-30 收盘，预期 2,134 个 XNYS 交易日；2017-12-29 信号收盘决定 2018-01-02 开盘的首个目标。首日从现金部署，首日开盘至收盘收益和初始交易成本都进入统计。
- 每日风险审计区间冻结为 2014-06-30 至 2026-06-30，恰有 3,018 个连续权威 XNYS 交易日。RV21 必须从该区间之前已有的完整冻结 SPY 历史计算，不能在 2014-06-30 重新初始化、补零或缩短窗口。
- 基准：冻结 SPY 总回报代理与日度 T-bill。SPY 不能冒充官方 S&P 500 Total Return 指数。
- 动量：`mom_255_0`、`mom_255_21`、`mom_12_1`；K 为 10、20、50；频率为周、月；模式为 long-only、long-short。
- 排名、PIT 过滤、计划调仓日、目标股票集合、公司行动和缺价口径与对应 G00 完全一致。G11 不改变选股，不加入反转、阈值搜索、确认期、滞后带、仓位下限、杠杆或机器学习。
- G11 没有组内 variant 轴。`realized_vol_window=21`、`annual_target_volatility=0.15`、`maximum_scale=1.0` 是唯一允许参数。旧 V1 的 20/60 日窗口、10%/20% 目标、1.5 倍上限及其他组合全部退出本次证据集。
- `v1-volatility-scaling-prototype-v1` 与 `v1-volatility-scaling-unified-start-v2` 只作为 provenance alias；不得复用其数据、NAV、参数扩展或结论。

## 唯一因果风险源与连续动作

令 $P_t^{SPY}$ 为交易日 $t$ 收盘时已可见的冻结 SPY 总回报代理价格，简单收益为

\[
r_t^{SPY}=P_t^{SPY}/P_{t-1}^{SPY}-1.
\]

SPY RV21 使用截至当前信号收盘的恰好 21 个 close-to-close 简单收益，以样本标准差 `ddof=1` 年化：

\[
\sigma_t=\sqrt{252}\;sd(r_{t-20}^{SPY},\ldots,r_t^{SPY}).
\]

当前收盘收益可以进入 $\sigma_t$，因为风险目标只在该收盘后计算并于下一 XNYS 开盘执行。完整冻结 SPY 历史只用于向后滚动计算；未来价格、日内数据、前向填充、全样本估计和缩放后策略收益均不得进入风险信号。

在每个计划信号收盘 $t$，下一开盘使用的唯一风险缩放为

\[
a_t=\min\left(1,\frac{0.15}{\sigma_t}\right).
\]

因此 $0<a_t\le1$；当 $\sigma_t\le15\%$ 时因无杠杆上限而满仓，当 $\sigma_t>15\%$ 时按 `0.15 / sigma` 连续缩放。这里的 15% 是 **SPY 风险源的目标尺度**，不是对动量组合未来实现波动率的保证。不得加仓超过 1、不得设置仓位下限、不得四舍五入、不得按分位数改变公式，也不存在 hysteresis、进入/退出确认、不同目标或动态参数。

“连续”描述的是 $a_t$ 对高于 15% 的 $\sigma_t$ 连续响应，不代表每日交易。每日可生成 $\sigma_t$ 与公式 $a_t$ 用于审计，但策略只在周/月计划信号收盘取当前值；它在下一交易日开盘随正常调仓生效，并保持到下一次成功调仓。两次调仓之间不因每日 RV 变化而盘中或日末再缩放。若计划信号日的 SPY 收盘、21 个收益、$\sigma_t$ 或 $a_t$ 缺失、非有限或非正，整次运行必须拒绝 completed bundle，不能默认满仓、设为零仓位或沿用旧状态。

## 仅用于诊断的严格四分位

为遵守九宫格统一的 Q1–Q4 报告合同并与 G31 比较动作，G11 额外生成与 G31 完全相同的严格滞后标签。对 $p\in\{0.25,0.50,0.75\}$，

\[
q_{p,t}=Q_p(\sigma_{t-756},\ldots,\sigma_{t-1}),
\]

分位数必须有完整 756 个此前交易日的有限 RV，严格排除当前值，并采用 pandas rolling quantile 的线性插值。边界为 `Q1: sigma<=q25`、`Q2: q25<sigma<=q50`、`Q3: q50<sigma<=q75`、`Q4: sigma>q75`。

这些 quartile **绝不进入正式风险动作**：无论处于 Q1、Q2、Q3 还是 Q4，正式仓位始终只由 `min(1, 0.15 / sigma)` 决定。2014 年审计区间早期在尚无 756 个历史 RV 时，quartile 与 q25/q50/q75 可以为空；按冻结价格起点预计约到 2016-02-03 才出现首个完整标签，该日期只作为权威日历复算的 sanity check。所有正式信号日则必须完整。G11 与 G31/G21 在所有共同日期的 RV、滞后分位数和 quartile 必须逐位一致，任何偏差均属于无效实现。

## 持仓与现金公式

令 $W_t$ 为信号日 PIT 股票池内按冻结动量得分选出的 TopK，$L_t$ 为 BottomK；两者必须各有 K 个有效 SID 且无重叠。基础权重与同路径 G00 相同。

Long-only 在下一开盘的目标权重为

\[
w_{i,t}^{LO}=\begin{cases}a_t/K,&i\in W_t,\\0,&\text{otherwise},\end{cases}
\qquad c_t^{LO}=1-a_t.
\]

目标 long、short、gross、net 分别为 $a_t,0,a_t,a_t$。未配置现金按冻结日度 T-bill 收益复利。

Long-short/WML 在下一开盘同时、同比例缩放两腿：

\[
w_{i,t}^{LS}=\begin{cases}
+a_t/(2K),&i\in W_t,\\
-a_t/(2K),&i\in L_t,\\
0,&\text{otherwise}.
\end{cases}
\]

目标 long、short、gross、net 分别为 $a_t/2,a_t/2,a_t,0$。在 collateralized accounting 下，目标会计现金权重为 $1-\sum_iw_i=1$，包含卖空抵押并全额赚取 T-bill；降低 gross 不得错误地再叠加一份 `1-a_t` 现金。所有目标权重相对于扣除当次成本后的 NAV 建仓，实际敞口随后随价格漂移，直到下次成功调仓。

long-short 的 gross=1 collateralized NAV 是唯一可投资样式主路径。传统 gross=2 只按

\[
R^{factor,gross2}_d=2\bigl(R^{collateralized,gross1}_d-r^f_d\bigr)
\]

派生算术均值、波动率和 Sharpe 诊断；它不生成 NAV、CAGR 或额外策略/场景，也不允许据此宣称可部署。

## 路径、场景与主口径

G11 共

\[
3\text{ signals}\times3\text{ K}\times2\text{ frequencies}\times2\text{ modes}=36
\]

条核心持仓路径。

| 模式 | 核心路径 | 成本场景 | 借券费场景 | 报告场景 |
|---|---:|---:|---:|---:|
| Long-only | 18 | 0/5/10/20bps | 0% | 72 |
| Long-short | 18 | 0/5/10/20bps | 0%/1%/3%年化 | 216 |
| 合计 | 36 | — | — | 288 |

主场景固定为周频 10bps、月频 5bps；long-only 借券费为 0%，long-short 借券费为 1% 年化。因此恰有 36 个主场景。引擎应生成 72 条零成本事件路径（18 条 long-only，加 18 条 long-short × 3 个借券费档），再精确回放成本形成 288 个场景。成本或借券费只是同一选股、RV21 和目标仓位路径的会计压力场景，不得反向改变风险信号或增加核心路径数。

## 执行、成本、借券与公司行动

- 信号在计划期末最后交易日收盘生成，下一 XNYS 交易日开盘成交。公司行动先在开盘前按冻结账本处理，再进行策略调仓。
- 开盘前权重为 $w^-_{i,u}$，目标为 $w^*_{i,u}$。双边 L1 换手与成本为

  \[
  L1_u=\sum_i|w^*_{i,u}-w^-_{i,u}|,\qquad
  Cost_u=NAV^-_u\,(bps/10000)\,L1_u.
  \]

  `one_way_turnover=L1/2`；年化 L1 换手为 `sum(L1) * 252 / NAV observations`。成本必须覆盖选股变化和 $a_t$ 变化，不能只对证券替换收费。
- 每条事件路径以相同目标和换手精确回放 0/5/10/20bps；回放后 NAV、成本金额和 pretrade NAV 必须自洽。
- 年化借券费 $b\in\{0,0.01,0.03\}$ 转为日费率 $f=(1+b)^{1/252}-1$，每天按各空头收盘市值绝对值扣除。主 WML 使用 1%；免费数据不提供真实逐券费率、可借量或召回，必须作为限制报告。
- 现金（含卖空抵押）每天按冻结 T-bill 序列计息；借券费在收盘空头市值上扣除。首日开盘部署成本不得遗漏。
- Long-only 延续 G00 的 `leave_cash` 缺开盘价口径：可成交目标按原目标权重执行，未成交目标额度留在现金，并完整记录 SID 与数量。
- Long-short 任一目标或仍在当期 PIT 股票池的已有仓位缺开盘价时，整次双腿调仓跳过、保持完整原账簿、L1 换手为零，并记录 `skipped_signed_missing_open`。等待冻结账本中未来不超过 25 个权威交易日公司行动的已有仓位，记录 `skipped_pending_corporate_action` 并保留到行动日。
- 已退出 PIT 股票池的已有仓位缺开盘价，只能按冻结公司行动处理，或用此前不超过 5 个权威交易日的最后有效收盘显式清算，记录 `executed_with_terminal_last_close`、SID 与回退日期。不得单腿、部分或静默成交；任何跳过后必须在后续计划调仓恢复。

## G00 正式对照与 G31 动作解释层

唯一正式对照是同一冻结数据上已完成且产物哈希全部通过的 `g00-frozen-v3-v1`。G11 启动前必须验证 G00 manifest 状态、group id、dataset version、dataset manifest SHA256 与每个列入 manifest 的文件哈希。每个 G11 场景按 `signal × K × frequency × portfolio_mode × cost_bps × borrow_fee_annual` 一一映射到 G00；除连续风险缩放外不得改变任何正式绩效规则。

`comparison.csv` 对全部 288 个场景固定报告以下五项 `G11 - G00`：CAGR、T-bill 超额 Sharpe、最大回撤、年化波动和年化 L1 换手，共应有 `288 × 5 = 1,440` 行。正文判断只用 36 个预注册主场景，并同时给出 18 个 long-only 的全体、按信号、K 与频率分组结果；不得先看结果再删参数。

G31 不属于 G11 的运行输入或正式 reference。G11 completed bundle 验收后，最终报告才可对 36 个同键主场景固定展示 `G11 - G31` 的 CAGR、T-bill 超额 Sharpe、最大回撤、年化波动、beta 和年化 L1 换手；另报告共同信号日 allocation 相关、G11/G31 平均与最低 $a$、G11 低于/高于/等于 G31 的比例、按 Q1–Q4 的仓位差，以及四个危机窗口的仓位与 P&L 差异。该解释层不写入正式 `comparison.csv`，不改变 1,440 行合同，不生成新场景，也不设替代 H1。

判定分为三层：

1. **运行有效**：满足下文所有数据、因果、会计和产物验收；否则没有经济结论。
2. **机制成立**：long-only 满足 H1 的跨参数平台规则；若只在少数组合、单一频率、成本档或危机改善，只能记为局部证据。
3. **数值部署候选**：某个有效 long-only 主场景必须同时严格满足 `CAGR > SPY CAGR`、`Sharpe_excess_rf > 1`、`max_drawdown > -0.25`。逐项列出全部通过与未通过场景；单点通过不等于平台成立。

即使数值门槛通过，本数据的 `formal_eligible=false` 仍禁止称为实盘可部署策略；只有使用机构级 PIT 永久证券标识、官方总回报基准、真实交易成本与借券数据重新验收后，才可能解除治理门禁。WML 不要求 CAGR 超过 SPY，但必须报告绝对收益、Sharpe、借券费、换手、容量和 beta；它始终是机制诊断。

## 指标与预注册诊断

每个场景必须报告 total return、CAGR、年化波动、zero-RF 与 T-bill 超额 Sharpe、Sortino、最大回撤、最长回撤持续期和 Calmar；相对 SPY 报告 beta、zero-RF/超额 alpha、tracking error、information ratio、算术与几何超额收益。还必须报告目标与实际 long/short/gross/net、平均/最小/最大 $a_t$、低于满仓的调仓比例、SPY RV21、`a_t sigma_t`、L1 换手、总成本、总借券费、现金收益、公司行动、估值回退与缺价执行事件。

条件诊断只使用主场景，并冻结如下口径：

- 按信号日诊断 quartile Q1–Q4 分组，同时对 G11 和配对 G00 计算从本次成功执行到下一计划执行前 pretrade NAV 的非重叠持有期收益；报告事件数、均值、中位数、胜率、ES10、ES5 和最差值。跳过执行的事件不混入收益，但必须单列数量、日期和原因。
- 对每个 quartile 报告 $\sigma_t$、$a_t$、`a_t sigma_t` 的均值、分位数与最小/最大值，以及 G11 相对 G00 的风险资产、T-bill、成本、借券费和行动/执行桥 P&L 差异。必须分别汇总 Q1–Q3 与 Q4，直接量化“严格尾部之外的连续动作”贡献；归因总和须与 NAV 差异在数值容差内一致。
- Long-short 额外分解 winner long、loser short、抵押现金与借券费贡献；不得只展示合成 WML。
- 危机窗口固定为：2018 Q4 抛售 `2018-09-21` 至 `2018-12-24`；COVID 下跌 `2020-02-19` 至 `2020-03-23`；COVID 反弹 `2020-03-24` 至 `2020-06-30`；2022 紧缩熊市 `2022-01-03` 至 `2022-10-12`。每个窗口对 G11 与 G00 报告累计收益、年化波动、最大回撤、最差日、beta、平均/最低 $a_t$、低于满仓日数与调仓数、Q1–Q4 日数与调仓数、换手、成本、借券费和各腿贡献；G11 完成后才可按上一节字段与 G31 比较动作。

危机日期是完整样本内的描述性切片，不参与信号，也不作为独立通过门槛。必须完整报告全部窗口；若总体改善只来自一个危机，不能包装成普遍机制。

## 输出合同

completed bundle 必须包含：

- `summary.csv`：288 个唯一场景，每行保留完整绩效、敞口、换手、成本、借券、风险缩放和执行审计字段；
- `comparison.csv`：恰有 1,440 行正式 `G11 - G00` 五指标比较；
- `config_resolved.toml`：展开后的 program、G11、运行参数、数据与 reference 锚；
- `manifest.json`：状态、formal blockers、输入/代码哈希、计数、schema、限制及所有产物的相对路径、字节数和 SHA256；
- `artifacts/nav.parquet`：全部 288 场景 × 2,134 日，即 614,592 行；除 NAV、收益和敞口外，必须持久化 `pnl_total`、long/short 风险腿、T-bill、交易成本、借券费、action/execution bridge、unexplained bridge、冻结证据标记、attributed P&L 与 closure error；
- `artifacts/rebalances.parquet`、`holdings.parquet`、`trades.parquet`：36 个主场景的完整信号、目标、执行、持仓与交易审计，并持久化信号日 RV21、诊断 quartile 和正式 $a_t$；
- `artifacts/diagnostics.parquet`：场景级审计、P&L 汇总、gross=2 非 NAV 诊断，以及每条 36 核心路径复制的共享每日 SPY 风险状态。`daily_spy_risk_state` 子集必须覆盖 3,018 日且恰有 `36 × 3,018 = 108,648` 行，至少包含日期、RV21、lagged q25/q50/q75、quartile、公式 $a_t$、cap 是否绑定；同一日期跨 36 路径的共享字段必须逐位相同。该子集只按主成本/借券身份存一份每核心路径记录，不因压力场景重复。

2014 年早期只有诊断分位字段允许按冻结 warm-up 规则为空；RV21 与公式 $a_t$ 必须在全部 3,018 日有限且严格为正。所有正式信号行的分位数、quartile 与 allocation 都必须完整。

## 运行验收

正式写出 completed bundle 前必须同时满足：

1. 本地运行区、逐文件 SHA256、`runtime-status`、冻结数据加载门禁及 G00 reference 完整性检查均通过；大型输入和输出只使用本地运行根，不回写 OneDrive。
2. `program.toml`、`G11.toml`、本设计、冻结记录、数据 manifest 和 G00 manifest 哈希写入 provenance；运行代码及共享会计/日历 helper 哈希写入 manifest。G31 manifest 不得成为运行 reference。任何数据、窗口、目标、上限或规则变化都要求新预注册设计或新 dataset version，不能沿用本 run id。
3. RV21 必须与对完整冻结 SPY 历史直接调用 21 日、`ddof=1`、`sqrt(252)` 的结果逐位一致；扰动任一信号收盘之后的价格不得改变该信号 RV、allocation、目标或交易。正式诊断 quartile 必须与 G31/G21 的严格 shift(1)、756 日状态逐位一致。
4. 每个计划信号日必须满足 `a=min(1,0.15/sigma)`，且 $0<a\le1$；当 `sigma<=0.15` 时 $a=1$，当 `sigma>0.15` 时 $a=0.15/sigma$。实现不得读取 quartile 决定仓位。
5. 每条核心路径相对 G00 只能把同一基础目标乘以标量 $a_t$。long-only 的目标 gross/net 均为 $a_t$；long-short 的 long/short/gross/net 为 $a_t/2,a_t/2,a_t,0$；TopK/BottomK 无重叠且无杠杆。
6. 恰有 36 条核心路径、72 条事件路径、288 个唯一有效场景、36 个主场景和 1,440 行比较；long-only/long-short 场景为 72/216。同一核心路径跨成本/借券费的 RV、quartile、allocation、选股和目标必须逐位一致。
7. 每个场景覆盖相同 2,134 个评价交易日，NAV 共 614,592 行；日期严格递增，NAV 正且有限，日收益可由 NAV 复算，首日成本已包含。每日共享状态子集恰有 108,648 行且日期范围、复制一致性和正式信号完整性通过。
8. 成本回放与零成本事件路径一致；T-bill、借券费、现金、gross/net、L1 换手及 P&L 逐日闭合。`pnl_unexplained_bridge` 在无冻结证据日的绝对值不得超过 `1e-10`；实质 action/execution bridge 必须逐日匹配冻结公司行动、估值回退或 terminal 证据。
9. 所有公司行动、缺价、跳过、terminal last-close 和后续恢复通过 G00 同等级审计；任何跳过必须零换手、零成本并保留原因，不能删除受影响场景。
10. 上述 9 个 bundle 文件齐全、schema 合法，manifest 列出的相对路径、字节数和 SHA256 全部复核一致；只有满足全部条件，manifest 才能标记 `status=completed`。相同 run id 的再次运行必须在加载大数据前拒绝覆盖。

## 失败解释纪律

- **数据/实现失败**：哈希、RV21、因果时点、quartile 诊断一致性、allocation、权重、场景数、NAV、会计闭合或执行审计任一失败，都只能记为无效运行；修复后用新 immutable run 重跑，不能解释经济机制。
- **机制失败**：运行有效但未达到 H1，G11 long-only 即为失败或局部证据。WML 成功、相对 G31 改善、最佳单点或单次危机改善都不能改写结论。
- **目标解释边界**：`a_t sigma_t<=15%` 只描述 SPY 风险源的缩放算术，不保证动量组合实现或预测波动为 15%。不得因组合波动偏离 15% 而事后改变目标或替换风险源。
- **cap 平台**：当 SPY RV21 不高于 15% 时 $a=1$ 是冻结的无杠杆上限，不代表规则失效；不得为增加处理强度而事后加入杠杆或降低目标。
- **beta/反弹代价**：若波动和回撤改善但 CAGR、Sharpe、beta 或反弹收益代价过大，应明确为保险成本过高；若零成本有效而主成本失效，应明确为实施失败。
- **动作解释边界**：G11 相对 G31 更好但仍未通过 H1，只能称为连续动作局部改善；G31 更好也不能删除 G11 或把 Q4 规则回填进 G11。
- 其他窗口、目标波动率、仓位上/下限、杠杆、确认/滞后、预测器或阈值只能在本报告完成后另行预注册并全部报告，不得覆盖本结果。

## 输出治理

完整 immutable bundle 应写入本地运行区的 `results/experiments/G11/runs/g11-frozen-v3-v1/`。运行前不得创建空 `report.md`；只有 completed bundle 通过全部验收后，才能创建 `docs/20_experiments/G11_spy_continuous_scale/report.md` 并据此撰写结果。报告必须同时保留成功、失败、异常和全部压力场景，并明确研究层级。

Git/OneDrive 只允许在验收后发布精简物：`results/published/G11/` 下的 `summary.csv`、`comparison.csv`、`config_resolved.toml` 与 `manifest.json`，以及最终小型文档。日度 NAV、SPY 风险状态、持仓、交易、完整数据和其他大型产物只留在本地运行区，不得复制回 OneDrive 或提交 Git。bundle 不得原地覆盖；重跑必须使用新 run id，并保留旧 run 及其哈希链。
