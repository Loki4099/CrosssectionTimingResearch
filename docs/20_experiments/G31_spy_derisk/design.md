# G31：SPY 历史波动率严格 Q4 减仓——实验设计

状态：**设计已执行；冻结运行 `g31-frozen-v3-v1` 已完成，预注册 H1 未通过。** 结果与审计边界见[结果报告](./report.md)。本文保留运行前冻结的研究问题、因果口径、判定规则与输出契约；机器参数以该运行内的 `config_resolved.toml` 为准。不得把 legacy alias、G21 结果或迁移中的文件当作 G31 结果。

## 研究问题与假设

G21 已表明，在 SPY 短期历史波动率进入严格 Q4 后直接切到近期 loser，会系统性恶化 long-only 左尾。G31 保持同一风险变量与同一严格 Q4 状态，但不改变横截面排序，只降低原动量组合的风险仓位并让未配置资本留在 T-bill。

预注册假设如下：

1. **H1（主要假设）**：相对同信号、同 K、同频率、同成本的 G00，Q4 减仓应在 long-only 主场景中形成最大回撤和 T-bill 超额 Sharpe 的跨参数改善，而不是只改善一个事后挑选的组合。
2. **H2（收益代价）**：减仓可能牺牲 CAGR 和市场 beta；是否值得由 Sharpe、左尾、回撤及部署联合门槛共同判断，不能只凭回撤变小判定成功。
3. **H3（机制区分）**：long-short/WML 同步缩放用于区分市场 beta 风险与 winner/loser 腿风险。若改善主要出现在 long-only，证据偏向市场 beta 管理；若 WML 也稳定改善，说明风险动作还作用于横截面价差。WML 不能用来挽救失败的 long-only 主要假设。

H1 只有在 18 个 long-only 主场景中至少 12 个同时满足 `delta Sharpe > 0` 与 `delta MDD > 0`，且周频、月频各至少 5/9 个同时满足，并且两项 delta 的全体中位数均严格为正时，才称为“跨参数平台支持”。这里 MDD 以负数保存，所以 `delta MDD = MDD_G31 - MDD_G00 > 0` 表示回撤改善。未达到该标准即记为失败或局部证据，不能改用最佳单点叙事。

## 冻结范围

- 数据版本：`sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate`；冻结记录 SHA256 为 `a3ef9ee72cd3d535c2e5bf06b3d1f520c54667a8552891543ee0f9ca50488296`，其锁定的数据 manifest SHA256 为 `65b628d604f7e2f456e8d1d43a3c3e88b6bd3e86cc1c9455cdcfe28b856a3ec7`。
- 数据状态仍为 `review / free_research_candidate`，运行必须显式允许 review 数据，并永久保留 `formal_run_eligible=false` 及全部 formal blockers。
- 股票池：历史时点 S&P 500 成分股，使用冻结 v3 canonical SID；策略层排除清单为空。
- 评价期：2018-01-02 开盘至 2026-06-30 收盘，预期 2,134 个 XNYS 交易日；首日从现金在开盘部署，首日开盘至收盘收益和初始交易成本都进入统计。
- 基准：SPY 总回报代理与冻结日度 T-bill。SPY 不能冒充官方 S&P 500 Total Return 指数。
- 动量：`mom_255_0`、`mom_255_21`、`mom_12_1`；K 为 10、20、50；频率为周、月；模式为 long-only、long-short。
- 排名、PIT 过滤和计划调仓日与 G00 完全一致。G31 不加入反转窗口、阈值搜索、确认期、滞后带、仓位下限、杠杆或机器学习。
- `v3-regime-switch-prototype-v1` 与 `v3-regime-switch-unified-start-v2` 仅作为 provenance alias。它们不是冻结 v3 的有效对照，也不得复用其 NAV、参数或结论。

## 严格因果状态公式

令 $P_t^{SPY}$ 为交易日 $t$ 收盘时已可见的 SPY 总回报价格，简单收益为

\[
r_t^{SPY}=P_t^{SPY}/P_{t-1}^{SPY}-1.
\]

SPY RV21 使用截至当前收盘的恰好 21 个 close-to-close 收益，以样本标准差 `ddof=1` 年化：

\[
\sigma_t=\sqrt{252}\;sd(r_{t-20}^{SPY},\ldots,r_t^{SPY}).
\]

阈值历史严格排除当前值。对 $p\in\{0.25,0.50,0.75\}$，

\[
q_{p,t}=Q_p(\sigma_{t-756},\ldots,\sigma_{t-1}),
\]

其中必须有完整 756 个历史交易日的有限 RV，分位数采用与 pandas rolling quantile 一致的线性插值，不做填充、四舍五入或扩大窗口。四分位边界冻结为：

\[
Q1:\sigma_t\le q_{.25,t};\quad
Q2:q_{.25,t}<\sigma_t\le q_{.50,t};\quad
Q3:q_{.50,t}<\sigma_t\le q_{.75,t};\quad
Q4:\sigma_t>q_{.75,t}.
\]

在计划信号收盘 $t$ 计算下一开盘使用的风险缩放：

\[
a_t=
\begin{cases}
1, & \sigma_t\le q_{.75,t},\\
\min(1,q_{.75,t}/\sigma_t), & \sigma_t>q_{.75,t}.
\end{cases}
\]

因此 Q4 中必有 $0<a_t<1$，Q1–Q3 中 $a_t=1$。不存在日内数据、未来收益、未来分位数、进入/退出确认或 hysteresis。若任一计划信号日的 $\sigma_t$、分位数或比例缺失、非有限或非正，运行必须失败，不能默认满仓或沿用旧状态。

状态可以逐日生成用于审计，但**只在周/月计划信号收盘取样**；$a_t$ 于下一 XNYS 交易日开盘随正常调仓生效，并保持到下一次成功调仓。两次调仓之间不因每日状态变化而盘中或日末再缩放。G31 的 Q1–Q4 标签必须与 G21 在所有共同信号日逐位一致；尤其要复核 G21 已审计的 2020-02-21 周频首次 Q4 信号及 2020-02-24 执行，不把该事实当作 G31 结果。

## 持仓与现金公式

令 $W_t$ 为信号日 PIT 股票池内按冻结动量得分选出的 TopK，$L_t$ 为 BottomK；两者必须各有 K 个有效 SID 且无重叠。基础权重与 G00 相同。

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

其目标 long、short、gross、net 分别为 $a_t/2,a_t/2,a_t,0$。在当前 collateralized accounting 下，目标会计现金权重为 $1-\sum_iw_i=1$，包含卖空抵押并全额赚取 T-bill；降低 gross 不得错误地再叠加一份 `1-a_t` 现金。所有目标权重相对于扣除当次成本后的 NAV 建仓，实际敞口随后随价格漂移，直到下次成功调仓。

long-short 的 gross=1 collateralized NAV 是唯一可投资样式主路径。传统 gross=2 只按

\[
R^{factor,gross2}_d=2\bigl(R^{collateralized,gross1}_d-r^f_d\bigr)
\]

派生算术均值、波动率和 Sharpe 诊断；它不生成 NAV、CAGR 或额外策略/场景，也不允许据此宣称可部署。

## 路径、场景与主口径

G31 没有组内 variant 轴，共

\[
3\text{ signals}\times3\text{ K}\times2\text{ frequencies}\times2\text{ modes}=36
\]

条核心持仓路径。

| 模式 | 核心路径 | 成本场景 | 借券费场景 | 报告场景 |
|---|---:|---:|---:|---:|
| Long-only | 18 | 0/5/10/20bps | 0% | 72 |
| Long-short | 18 | 0/5/10/20bps | 0%/1%/3%年化 | 216 |
| 合计 | 36 | — | — | 288 |

主场景固定为周频 10bps、月频 5bps；long-only 借券费为 0%，long-short 借券费为 1% 年化。因此恰有 36 个主场景。所有 288 个场景必须保留且身份唯一，成本或借券费只是同一信号/目标路径的会计压力场景，不能增加核心路径数或改变选股与状态。

## 执行、成本、借券与公司行动

- 信号在计划期末最后交易日收盘生成，下一 XNYS 交易日开盘成交。公司行动先在开盘前按冻结账本处理，再进行策略调仓。
- 开盘前权重记为 $w^-_{i,u}$，目标为 $w^*_{i,u}$。双边 L1 换手与成本为

  \[
  L1_u=\sum_i|w^*_{i,u}-w^-_{i,u}|,\qquad
  Cost_u=NAV^-_u\,(bps/10000)\,L1_u.
  \]

  `one_way_turnover=L1/2`；年化 L1 换手为 `sum(L1) * 252 / NAV observations`。成本必须覆盖选股变化和 $a_t$ 变化，不能只对证券替换收费。
- 每个借券费场景必须先生成一条零交易成本事件路径，再以同一换手路径精确回放 0/5/10/20bps；回放后 NAV、成本金额和 pretrade NAV 必须自洽。
- 年化借券费 $b\in\{0,0.01,0.03\}$ 转为日费率 $f=(1+b)^{1/252}-1$，每天按各空头收盘市值绝对值扣除。主 WML 使用 1%；免费数据不提供真实逐券费率、可借量或召回，必须作为限制报告。
- 现金（含卖空抵押）每天按冻结 T-bill 序列计息；借券费在收盘空头市值上扣除。首日开盘部署成本不得从收益序列中遗漏。
- Long-only 延续冻结 G00 的 `leave_cash` 缺开盘价口径：可成交目标按原目标权重执行，未成交目标额度留在现金，并完整记录 SID 与数量。
- Long-short 任一目标或仍在当期 PIT 股票池的已有仓位缺开盘价时，整次双腿调仓跳过、保持完整原账簿、L1 换手为零，并记录 `skipped_signed_missing_open`。已有仓位若等待冻结账本中未来不超过 25 个权威交易日的公司行动，记录 `skipped_pending_corporate_action` 并保留到行动日。
- 已退出 PIT 股票池的已有仓位缺开盘价，只能按冻结公司行动处理，或用此前不超过冻结上限 5 个权威交易日的最后有效收盘显式清算，记录 `executed_with_terminal_last_close`、SID 与回退日期。不得单腿、部分或静默成交；任何跳过后必须在后续计划调仓恢复。

## G00 对照与判定层级

唯一有效对照是同一冻结数据上已完成且产物哈希全部通过的 `g00-frozen-v3-v1`。G31 启动前必须验证 G00 manifest 状态、group id、dataset version、dataset manifest SHA256 与每个列入 manifest 的文件哈希。每个 G31 场景按 `signal × K × frequency × portfolio_mode × cost_bps × borrow_fee_annual` 一一映射到 G00；除风险缩放外不得改变任何规则。

`comparison.csv` 对全部 288 个场景固定报告以下五项 `G31 - G00`：CAGR、T-bill 超额 Sharpe、最大回撤、年化波动和年化 L1 换手，共应有 `288 × 5 = 1,440` 行。正文判断只用 36 个预注册主场景，并同时给出 18 个 long-only 的全体、按信号、K 与频率分组结果；不得先看结果再删参数。

判定分为三层：

1. **运行有效**：满足下文所有数据、因果、会计和产物验收；否则没有经济结论。
2. **机制成立**：long-only 满足 H1 的跨参数平台规则；若只在单一危机、单一频率或少数组合改善，只能记为局部证据。
3. **数值部署候选**：某个有效 long-only 主场景必须同时严格满足 `CAGR > SPY CAGR`、`Sharpe_excess_rf > 1`、`max_drawdown > -0.25`（即回撤绝对值小于 25%）。逐项列出全部通过与未通过场景；单点通过不等于平台成立。

即使数值门槛通过，本数据的 `formal_eligible=false` 仍禁止把它称为实盘可部署策略；只有使用机构级 PIT 永久证券标识、官方总回报基准、真实交易成本与借券数据重新验收后，才可能解除该治理门禁。WML 不要求 CAGR 超过 SPY，但必须报告绝对收益、Sharpe、借券费、换手、容量和 beta；它始终是机制诊断。

## 指标与预注册诊断

每个场景必须报告完整样本的 total return、CAGR、年化波动、zero-RF 与 T-bill 超额 Sharpe、Sortino、最大回撤、最长回撤持续期和 Calmar；相对 SPY 报告 beta、zero-RF/超额 alpha、tracking error、information ratio、算术与几何超额收益。还必须报告目标与实际 long/short/gross/net、平均/最小/最大 $a_t$、低于满仓的调仓比例、Q4 调仓次数、L1 换手、总成本、总借券费、现金收益、公司行动、估值回退与缺价执行事件。

条件诊断只使用主场景，并冻结如下口径：

- 按信号日 Q1–Q4 分组，同时对 G31 和配对 G00 计算从本次成功执行到下一计划执行前 pretrade NAV 的非重叠持有期收益；报告事件数、均值、中位数、胜率、ES10、ES5 和最差值。跳过执行的事件不混入收益，但必须单列数量、日期和原因。
- 对 Q4 报告 $\sigma_t/q_{.75,t}$ 与 $a_t$ 的最小值、分位数、均值，连续 Q4 episode 的进入/退出日期和长度，以及 G31 相对 G00 的风险资产 P&L、T-bill、成本和借券费差异。归因总和必须与 NAV 差异在数值容差内一致。
- Long-short 额外分解 winner long、loser short、抵押现金与借券费贡献；不得只展示合成 WML。
- 危机窗口固定为：2018 Q4 抛售 `2018-09-21` 至 `2018-12-24`；COVID 下跌 `2020-02-19` 至 `2020-03-23`；COVID 反弹 `2020-03-24` 至 `2020-06-30`；2022 紧缩熊市 `2022-01-03` 至 `2022-10-12`。每个窗口对 G31 与 G00 报告累计收益、年化波动、最大回撤、最差日、beta、平均/最小 $a_t$、Q4 日数与调仓数、换手、成本、借券费及各腿贡献。

这些危机日期是完整样本内的描述性切片，不参与信号，也不作为独立通过门槛。必须完整报告全部窗口；若总体改善只来自 COVID，一个危机不能被包装成普遍机制。

## 运行验收

建议新运行 ID 为 `g31-frozen-v3-v1`。正式写出 completed bundle 前必须同时满足：

1. 本地运行区迁移、逐文件 SHA256 验收、`runtime-status`、冻结数据加载门禁及 G21 dry-run 已通过；G31 的大型输入和输出只使用本地运行根，不回写 OneDrive。
2. `program.toml`、`G31.toml`、冻结记录、数据 manifest 和 G00 manifest 哈希均写入 provenance；任何数据或规则变化都要求新 dataset version 或新预注册设计，不能沿用本 run id。
3. RV21、严格滞后 756 日分位数、Q1–Q4 和 $a_t$ 有独立因果测试；所有共同日期的状态与 G21 完全一致，且每个计划信号日均有限可用。
4. Q1–Q3 的目标权重逐位等于 G00；Q4 只允许乘以同一标量 $a_t$。在数值容差内，long-only 的 gross 和 net 均为 $a_t$，long-short 的 long/short/gross/net 为 $a_t/2,a_t/2,a_t,0$，TopK/BottomK 无重叠且无杠杆。
5. 恰有 36 条核心路径、288 个唯一有效场景、36 个主场景和 1,440 行配对 G00 比较；long-only/long-short 场景分别为 72/216。不得因负收益、门槛失败或机制失败删除场景。
6. 每个场景覆盖完全相同的 2,134 个评价交易日，预计 NAV 共 `288 × 2,134 = 614,592` 行；日期严格递增、NAV 正且有限、日收益可由 NAV 复算，首日成本已包含。
7. 成本回放与零成本事件路径一致；T-bill、借券费、现金、gross/net、L1 换手及 P&L 归因逐日闭合。所有公司行动、缺价、跳过、terminal last-close 和后续恢复通过 G00 同等级审计。
8. `summary.csv`、`comparison.csv`、`config_resolved.toml`、`manifest.json` 以及 `artifacts/nav.parquet`、`rebalances.parquet`、`holdings.parquet`、`trades.parquet`、`diagnostics.parquet` 齐全，schema 合法；manifest 列出的相对路径、字节数和 SHA256 全部复核一致。只有满足这些条件，manifest 才能标记 `status=completed`。

## 失败解释纪律

- **数据/实现失败**：哈希、因果历史、状态一致性、权重、场景数、NAV、会计闭合或执行审计任一失败，都只能记为无效运行；修复后用新 immutable run 重跑，不能解释经济机制。
- **机制失败**：运行有效但未达到 H1 平台规则，G31 long-only 即为失败或局部负对照。WML 成功、最佳单点通过或某次危机改善都不能改写该结论。
- **处理强度弱**：若 Q4 的 $a_t$ 大多接近 1，应解释为冻结 `q75/sigma` 映射产生的弱 treatment，而不是据此声称“所有减仓规则无效”。仍不得在本运行中事后加入仓位下限或改阈值。
- **时点失败**：若 RV21 在主要跌幅发生后才进入 Q4，或退出/再进入造成损失，应报告严格因果的识别滞后和 episode 路径，不能用未来信息重标状态。
- **经济失败**：若回撤改善但 CAGR、Sharpe、成本或 beta 代价过大，应明确为保险成本过高；若零成本有效而主成本失效，应明确为换手/实施失败。
- q85/q90/q95、确认/滞后规则、不同缩放函数或仓位下限只能在本报告完成后另行预注册并全部报告，不得覆盖或删除 G31 原结果。

## 输出治理

完整 immutable bundle 应写入本地运行区的 `results/experiments/G31/runs/g31-frozen-v3-v1/`。运行前不得创建空 `report.md`；只有 completed bundle 通过全部验收后，才能据此撰写结果报告。报告必须同时保留成功、失败、异常和全部压力场景，并明确研究层级。

Git/OneDrive 只允许在验收后发布精简物：`results/published/G31/` 下的 `summary.csv`、`comparison.csv`、`config_resolved.toml` 与 manifest，以及最终小型文档。日度 NAV、持仓、交易、缓存、完整数据和其他大型产物只留在本地运行区，不得复制回 OneDrive 或提交 Git。bundle 不得原地覆盖；重跑必须使用新 run id，并保留旧 run 及其哈希链。
