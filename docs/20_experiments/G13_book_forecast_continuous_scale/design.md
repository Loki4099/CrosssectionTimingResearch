# G13：裸动量账簿 EWMA 预测波动率连续 15% 目标缩放——实验设计

状态：**设计已冻结；尚未运行。** 建议首个不可变运行 ID 为 `g13-frozen-v3-v1`。本文在任何冻结 v3 的 G13 输出、正式 bundle 或结果报告产生前，冻结研究问题、预测器、连续风险动作、因果边界、判定规则、会计与输出合同；运行后不得原地修改本设计以迁就结果。机器参数以运行内 `config_resolved.toml` 为准。G13 完成并通过全部验收前不得创建 `report.md` 或精简发布目录。

## 研究问题与预注册假设

G11 使用 SPY RV21 连续缩放，G12 使用匹配裸账簿 RV126 连续缩放，两组 long-only H1 均失败；G33 使用同一裸账簿的因果 EWMA 预测波动率、但只在严格 Q4 减仓。G13 固定 G33 的风险源和 G12 的连续动作，检验预测风险源能否避免历史 RV126 连续动作的长期过度保险。

系统计划只冻结下一阶段为 **G11–G13**，没有冻结三组内部顺序。G11、G12 完成后，本次在任何 G13 结果产生前选择 G13，是当前阶段的前瞻执行决定；不得追溯声称旧计划早已冻结内部顺序，也不得根据 G11/G12/G33 的结果改变以下模型、参数或门槛。

预注册假设：

1. **H1（主要假设）**：相对同键 G00，匹配裸账簿 EWMA 预测波动率的连续 15% 目标缩放应在 long-only 主场景中共同改善最大回撤与 T-bill 超额 Sharpe。
2. **H2（保险成本）**：连续动作可能降低 beta、波动和回撤，同时牺牲 CAGR 与反弹收益；只有 Sharpe 与回撤共同改善才构成平台证据。
3. **H3（横截面机制）**：long-short/WML 同步缩放用于区分市场 beta 与 winner/loser 腿风险。WML 改善只能记为机制证据，不能挽救失败的 LO H1。
4. **H4（风险源与动作解释）**：completed bundle 验收后，才可把 G13 与 G11（SPY 历史风险源）、G12（book 历史风险源）和 G33（同预测风险源、尾部动作）作描述性同键比较。它们都不是 G13 的正式 reference，也不能替代 G13-G00 H1。

H1 只有在 18 个 LO 主场景中至少 12 个同时满足 `delta Sharpe > 0` 与 `delta MDD > 0`，周、月各至少 5/9，且两项 delta 的全体中位数均严格为正时，才称为跨参数平台支持。正式 delta 始终为 `G13-G00`；MDD 以负数保存，因此正 delta 表示改善。未达到标准即记为失败或局部证据，不能用最佳单点、WML、危机窗口或 completed 后比较改写。

## 冻结输入与范围

- 数据版本：`sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate`。
- 冻结记录 SHA256：`a3ef9ee72cd3d535c2e5bf06b3d1f520c54667a8552891543ee0f9ca50488296`；数据 manifest SHA256：`65b628d604f7e2f456e8d1d43a3c3e88b6bd3e86cc1c9455cdcfe28b856a3ec7`。
- 数据状态永久保留 `review / free_research_candidate / formal_run_eligible=false`；运行必须显式允许 review 数据。
- 运行时 G13 配置 SHA256：`1a66e1b2dfacfccad7d90c3780d4c7dd8bb71931e2c77380c0a39acbb8386654`；程序配置 SHA256：`11394af02fa028abe4a11434874be31e33e692f55feb73e9236da9bf8d07d413`。
- 唯一正式 reference 是 `g00-frozen-v3-v1`，manifest SHA256 `8b875d4bcbb7b178b309c7b1edaa7dce9bbb15090e68b619fb045cec35411c66`。G11/G12/G33 只在 G13 completed 后进入报告解释，不得成为运行输入或 completed 门禁。
- 股票池、canonical SID、PIT、动量排名、成本、借券、公司行动、缺价与执行规则完全复用 G00；策略排除清单为空。
- 风险历史起点 2014-06-30；正式评价期 2018-01-02 开盘至 2026-06-30 收盘，预期 2,134 个 XNYS sessions。
- 信号 `mom_255_0`、`mom_255_21`、`mom_12_1`；K=10/20/50；频率周/月；模式 LO/LS。
- 唯一参数为 `forecast_model=ewma`、`ewma_decay=.94`、`forecast_horizon_sessions=21`、`annual_target_volatility=.15`、`maximum_scale=1`、`book_source=matching_G00_portfolio_mode`。不得搜索其他 decay、horizon、target、floor、cap、确认期、hysteresis、预测器或阈值。

## 每条路径的裸账簿

每个 `signal × K × frequency × portfolio_mode` 核心路径先独立生成匹配 G00 的未缩放裸账簿日收益 `r_book(j,t)`：

- 选股、权重、下一开盘执行、PIT、公司行动与缺价规则与 G00 相同；两次成功调仓之间自然漂移；
- LO 裸目标 gross=net=1；LS winner `+0.5`、loser `-0.5`、gross=1、net=0；
- 裸账簿固定 0bps 成本、0% 借券费、0 现金利息，不读取 T-bill、SPY、G11/G12/G13/G33 缩放收益或任一压力场景；
- 同一核心路径的所有成本/借券场景必须共享完全相同的裸收益、EWMA、预测状态与 allocation；
- 风险输入严格切片为 2014-06-30 至 2026-06-30 的 3,018 个权威 XNYS sessions，不允许补零、填充或缺日。

周频从 2014-06-30 正常账簿开始。月频为使 2014-06-30 具有真实持仓，只允许从该日前最近一次权威月频计划执行建立存量：2014-05-30 信号、2014-06-02 开盘；2014-06-02 至 2014-06-27 的收益、方差或预测不得进入 EWMA、分位数、状态或绩效。EWMA 输入仍严格从 2014-06-30 起。两种频率的首值必须有限、严格大于 -100%、绝对值非零；不得沿用 G12 月频首日零现金点，也不得加 epsilon 或默认 a=1。

## 因果 EWMA 预测

令 `t0=2014-06-30`。唯一预测器为 `lambda=.94`、pandas `adjust=False`：

\[
v_{j,t0}=r_{j,t0}^{2},\qquad
v_{j,t}=.94v_{j,t-1}+.06r_{j,t}^{2}.
\]

预测未来 21 个 sessions 条件日方差恒定：

\[
\widehat V^{(21)}_{j,t}=21v_{j,t},\qquad
\widehat\sigma_{j,t}=\sqrt{(252/21)\widehat V^{(21)}_{j,t}}=\sqrt{252v_{j,t}}.
\]

当前收盘的 `r(j,t)` 可以进入 `v(j,t)`，因为信号在该收盘后计算、下一 XNYS 开盘执行。任何未来真实收益不得进入预测、状态、仓位、模型选择或验收。实现不得读取或计算 `actual_future_volatility` 作为策略输入；未来 21 日 realized volatility 只允许在 completed 后作描述性 calibration，不能改变经济判定。

任一输入收益、递推方差或预测波动率缺失、非有限或非正时必须 fail closed。直接递推与 pandas `ewm(alpha=.06, adjust=False)` 必须逐位一致；扰动未来收益不得改变既往预测。

## 连续 15% 目标动作与诊断 quartile

正式动作只由当前预测波动率决定：

\[
a_{j,t}=\min\left(1,\frac{0.15}{\widehat\sigma_{j,t}}\right),\qquad 0<a_{j,t}\le1.
\]

当预测波动率不高于 15% 时满仓；高于 15% 时严格按比例缩放。不存在 quartile 触发、未来波动、滞后一日、仓位下限、杠杆、确认期或日内交易。所有正式计划信号必须有有限正预测和 allocation；不能默认 a=1、沿用旧状态或设为 0。

为与 G33 统一诊断，对每条路径计算严格排除当前预测值的 756 日 quartile：

\[
q_{p,j,t}=Q_p(\widehat\sigma_{j,t-756},\ldots,\widehat\sigma_{j,t-1}),\quad p\in\{.25,.50,.75\}.
\]

必须有完整 756 个严格此前、有限且正的预测值，采用 pandas 线性插值。边界为 Q1 `sigma<=q25`、Q2 `q25<sigma<=q50`、Q3 `q50<sigma<=q75`、Q4 `sigma>q75`。Quartile **绝不进入 allocation**；修改 q25/q50/q75 或标签不得改变 a。所有正式信号 quartile 必须完整。

状态每日生成供审计，但只在周/月计划信号收盘取样，于下一开盘随正常调仓生效，并保持到下一次成功调仓。skip 时继续持有此前实际账簿与此前成功状态。

## 持仓、现金与执行会计

设匹配 G00 基础权重为 `w_G00(i,j,t)`，唯一目标变化为 `w_G13=a*w_G00`。

- LO：目标 long/gross/net=a，short=0，现金 `1-a` 并赚冻结 T-bill；
- LS：winner long=a/2、loser short=a/2、gross=a、net=0；会计现金仍为 `1-sum(w)=1`，包含卖空抵押并全额赚 T-bill，不得额外叠加 `1-a`；
- 权重相对于扣除当次成本后的 NAV 建仓，随后随价格自然漂移；
- gross=2 WML 只派生非 NAV 算术诊断，不生成 CAGR、NAV 或额外场景。

信号收盘、下一开盘执行。双边 L1 与成本：

\[
L1_u=\sum_i|w^*_{i,u}-w^-_{i,u}|,\qquad Cost_u=NAV^-_u(bps/10000)L1_u.
\]

每路径先生成零成本正式事件路径，再精确回放 0/5/10/20bps；成本和借券不得改变选股、预测状态、目标或执行身份。年借券费按 `(1+b)^(1/252)-1` 的日费率从空头收盘市值扣除。

LO 延续 `leave_cash`；LS 任一必需开盘价缺失时整笔双腿调仓跳过并保持完整账簿，等待公司行动时记录 pending，退出 PIT 的缺价仓位只允许冻结 terminal last-close 处理。任何 skip 必须零换手、零成本并在后续计划调仓恢复。

## 路径、场景与正式判定

G13 恰有 `3 signals × 3 K × 2 frequencies × 2 modes = 36` 条核心路径：

| 模式 | 核心路径 | 成本场景 | 借券场景 | 报告场景 |
|---|---:|---:|---:|---:|
| LO | 18 | 0/5/10/20bps | 0% | 72 |
| LS | 18 | 0/5/10/20bps | 0/1/3% | 216 |
| 合计 | 36 | — | — | 288 |

主场景为周频 10bps、月频 5bps，LS 主借券费 1%，恰有 36 个。`comparison.csv` 固定报告全部 288 场景相对同键 G00 的 CAGR、T-bill 超额 Sharpe、MDD、年化波动、年化 L1，共 1,440 行。正文判断只用 36 个主场景，并完整展示 18 LO、18 LS 及信号/K/频率分组。

判定分三层：

1. 运行有效：输入、因果、identity、会计、计数与哈希全部通过；
2. 机制成立：LO 满足预注册 H1；WML、危机、事后比较不能替代；
3. 数值部署候选：某 LO 同时严格满足 `CAGR>SPY CAGR`、`Sharpe_excess_rf>1`、`MDD>-25%`。即使通过，`formal_run_eligible=false` 仍禁止部署表述。

## 预注册诊断

每场景报告 total return、CAGR、波动、zero-RF/T-bill Sharpe、Sortino、MDD/持续期、Calmar、beta、alpha、tracking error、IR、超额收益、敞口、a、换手、成本、借券、现金与执行事件。

主场景条件诊断：

- 按信号日 Q1–Q4，对 G13 与 G00 计算本次成功执行 postcost NAV 到下一计划调仓前 pretrade NAV 的非重叠事件收益；报告 n、均值、中位、胜率、ES10、ES5、最差，skip 单列；
- 每个 quartile 报告预测 sigma、a、`a×sigma` 的均值、分位、最小/最大，以及 G13-G00 的 long、short、T-bill、成本、借券和 action bridge P&L；active-state P&L 与事件收益不得混称；
- 报告 `a<1` 的日度/调仓 episode、进入退出与持续长度；
- completed 后描述性 calibration：信号日 forecast 与随后 21 日 realized sigma 的 bias、MAE、RMSE、Pearson/Spearman、variance ratio、QLIKE、coverage 与带截距 OLS；窗口重叠，不报告独立样本 p 值，预测质量不能替代经济 H1；
- 四个固定窗口：2018-09-21..2018-12-24、2020-02-19..2020-03-23、2020-03-24..2020-06-30、2022-01-03..2022-10-12。每窗报告 G13/G00 收益、vol、MDD、最差日、beta、a、低于满仓日/调仓、Q1–Q4 日/调仓、L1、成本、借券和逐腿贡献；
- G13 completed 后，报告 G13-G11、G13-G12、G13-G33 的 36 主场景指标、共同信号 allocation/state、危机时点与腿归因。它们不得写入正式 comparison 或改变 H1。

G00/G11/G12/G33 若未原生持久化某项腿贡献，允许用与 G13 完全相同的 balance-flow identity 只读重建，但必须标注并以冻结证据日约束 bridge。

## 输出合同与运行验收

completed bundle 必须包含 9 个文件：`summary.csv`、`comparison.csv`、`config_resolved.toml`、`manifest.json`，以及 `artifacts/nav.parquet`、`rebalances.parquet`、`holdings.parquet`、`trades.parquet`、`diagnostics.parquet`。大型 Parquet 只留本地运行区；验收后 Git/OneDrive 只发布前四个精简文件与小型文档。

写出 completed 前必须同时满足：

1. 本地 runtime、冻结数据、FROZEN、G00 manifest 及其 8 个记录全部 bytes/SHA 通过；G13 full bundle 不得写入 repo。
2. program、G13 config、本设计、FROZEN、dataset manifest、G00 manifest 与运行代码哈希写入 provenance；G11/G12/G33 manifest 不得成为 runtime reference。
3. 每路径裸账簿连续覆盖 3,018 个权威日历日、0bps/0借券/0现金息/未缩放；月频 seed 只建立 t0 存量，不污染 EWMA 输入。
4. `v0=r0²>0`、EWMA 递推、21v、sqrt(252v)、严格 shift(1)-rolling756 quartile 与未来扰动测试逐位通过；源码不得读取 `actual_future_volatility`。
5. 每个正式信号满足 `a=min(1,.15/forecast_sigma)`；0<a<=1；quartile 改动不得改变 a；`a×sigma<=15%` 只是预测风险算术，不保证实现波动。
6. G13/G00 信号、执行日期/状态、选股 SID、缺价、公司行动和 holding keys 逐位相同；权重仅为 G00×a。skip、leave_cash、terminal 均通过同等级审计。
7. 恰有 36 核心、72 事件路径、288 有效场景、36 primary、1,440 comparison；LO/LS=72/216；每场景 2,134 日，NAV=614,592。
8. 同一路径跨成本/借券的裸收益、EWMA、状态、目标和执行身份逐位相同；成本回放、借券、T-bill、cash、gross/net、L1 与 daily P&L 闭合。无冻结证据日的 unexplained bridge <=1e-10，closure <=1e-12。
9. 每路径 daily forecast regime 恰 3,018 行；stored book return、EWMA variance、forecast variance/vol、quartile、a、scaled forecast 与 cap 可独立复算，正式信号零缺失。
10. 九文件 schema、相对路径、bytes、SHA 全通过；相同 run ID 必须在加载大数据前以 `FileExistsError` 拒绝覆盖。只有全部通过才能标 completed。

## 失败纪律与输出治理

- 数据、哈希、因果、预测、状态、identity、会计、计数或产物任一失败，运行无效；修复必须使用新 immutable run ID。
- 运行有效但 LO 未达到 H1，即记为失败或局部证据。WML、forecast calibration、最佳单点、危机或相对 G11/G12/G33 改善都不能改写结论。
- 若风险下降但 CAGR/Sharpe/反弹代价过大，明确写保险成本过高；若零成本有效而主成本失效，写实施失败。
- G13 相对 G11/G12/G33 的优势只能解释风险源或动作；不能把完成后的信息回填本设计。
- 完整 bundle 写入本地 `results/experiments/G13/runs/g13-frozen-v3-v1/`。只有 completed bundle 全部验收后才能创建 `report.md` 与 `results/published/G13/`；bundle 不得覆盖，重跑必须使用新 ID。
