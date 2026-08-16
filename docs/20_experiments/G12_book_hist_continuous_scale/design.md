# G12：裸动量账簿历史波动率连续 15% 目标缩放——实验设计

状态：**设计已冻结；尚未运行。** 建议首个不可变运行 ID 为 `g12-frozen-v3-v1`。本文在任何冻结 v3 的 G12 输出、正式 bundle 或结果报告产生前，冻结研究问题、唯一风险动作、因果口径、判定规则、会计与输出合同；运行后不得原地修改本设计以迁就结果。机器参数以运行内的 `config_resolved.toml` 为准。G12 完成并通过全部验收前不得创建 `report.md`，也不得把 G11、G32、legacy 或迁移中的文件当作 G12 结果。

## 研究问题与预注册假设

G11 使用 SPY RV21 做连续 15% 目标缩放，long-only 的最大回撤 18/18 改善，但 CAGR 与 Sharpe 18/18 下降，预注册 H1 失败；long-short 则出现稳定但绝对表现弱的改善。G32 使用每条裸动量账簿的 RV126，只在严格 Q4 中减仓，long-only 同样未通过 H1。G12 固定 G11 的连续动作与 G32 的裸账簿历史风险源，检验失败来自风险源、动作形状，还是连续保险本身。

系统计划只冻结下一阶段为 **G11–G13**，没有冻结三组内部顺序。G11 完成后，本次在任何 G12 冻结 v3 结果产生前按组号选择 G12，是当前阶段的前瞻执行决定；不得追溯声称旧计划早已冻结 G11→G12→G13，也不得据 G11/G12 的结果改变 G13 的规则。

预注册假设如下：

1. **H1（主要假设）**：相对同键 G00，裸账簿 RV126 的连续 15% 目标缩放应在 long-only 主场景中形成最大回撤与 T-bill 超额 Sharpe 的跨参数共同改善。
2. **H2（保险代价）**：连续缩放可能降低 beta、波动和回撤，同时牺牲 CAGR 与反弹收益；只有 Sharpe 与回撤共同改善才构成平台证据。
3. **H3（横截面机制）**：long-short/WML 同步缩放用于区分市场 beta 风险与 winner/loser 腿风险。WML 改善是机制证据，不能挽救失败的 long-only H1。
4. **H4（风险源与动作解释）**：completed bundle 验收后，才可把 G12 与 G11（同动作、不同风险源）及 G32（同风险源、不同动作）作描述性同键比较。它们都不是 G12 的正式 reference，也不能替代 G12-G00 H1。

H1 只有在 18 个 long-only 主场景中至少 12 个同时满足 `delta Sharpe > 0` 与 `delta MDD > 0`，且周频、月频各至少 5/9 个同时满足，并且两项 delta 的全体中位数均严格为正时，才称为“跨参数平台支持”。正式 delta 始终为 `G12 - G00`；MDD 以负数保存，因此 `delta MDD > 0` 表示改善。未达到标准即记为失败或局部证据，不能改用最佳单点、G11/G32 差值、成本档或危机窗口改写结论。

## 冻结范围

- 数据版本：`sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate`；冻结记录 SHA256 为 `a3ef9ee72cd3d535c2e5bf06b3d1f520c54667a8552891543ee0f9ca50488296`，数据 manifest SHA256 为 `65b628d604f7e2f456e8d1d43a3c3e88b6bd3e86cc1c9455cdcfe28b856a3ec7`。
- 数据状态永久保留 `review / free_research_candidate / formal_run_eligible=false`；运行必须显式允许 review 数据。
- 股票池、canonical SID、PIT 过滤、动量排名、公司行动、缺价和执行规则与 G00 完全一致；策略排除清单为空。
- 风险历史从 `strategy_forecast_history_start = 2014-06-30` 开始；正式评价期为 2018-01-02 开盘至 2026-06-30 收盘，预期 2,134 个 XNYS session。
- 基准为冻结 SPY 总回报代理与冻结日度 T-bill；SPY 不能冒充官方 S&P 500 Total Return 指数。
- 动量信号为 `mom_255_0`、`mom_255_21`、`mom_12_1`；K 为 10、20、50；频率为周、月；模式为 long-only、long-short。
- G12 没有 variant 轴。唯一参数为 `book_realized_vol_window=126`、`annual_target_volatility=0.15`、`maximum_scale=1.0`、`book_source=matching_G00_portfolio_mode`。不得搜索其他窗口、目标、上限、仓位下限、杠杆、确认期或 hysteresis。
- 唯一正式运行 reference 是 `g00-frozen-v3-v1`，其 manifest SHA256 为 `8b875d4bcbb7b178b309c7b1edaa7dce9bbb15090e68b619fb045cec35411c66`。G11/G32 bundles 只在 G12 completed 后用于报告解释，不得成为运行输入或 completed 门禁。

## 每条核心路径的裸账簿

令 $j$ 标识唯一的 `signal × K × frequency × portfolio_mode` 核心路径。每条路径先独立生成与其匹配的未缩放 G00 裸账簿日收益 $r^{book}_{j,t}$：

- 使用 G00 的选股、权重、下一开盘执行、PIT、公司行动与缺价规则；两次成功调仓之间自然漂移；
- long-only 的裸目标 gross=net=1；long-short 为 winner `+0.5`、loser `-0.5`、gross=1、net=0；
- 裸账簿固定 0bps 成本、0% 借券费、0 现金利息，不得读取 T-bill、SPY、G12 缩放后收益或任一压力场景；
- 同一核心路径的所有成本/借券场景必须共享同一裸收益、状态与 allocation；
- 从 2014-06-30 至 2026-06-30 按权威 XNYS 日历连续生成。月频路径在 2014-06-30 尚未执行首笔 2014-07-01 开盘组合，因此该日是可验证的零息全现金收益 0；除此之外不允许补零、填充或缺日；
- 裸账簿只是因果风险输入，不是额外正式场景，也不计入 36 条核心路径或 288 个报告场景。

## 严格因果风险状态与连续动作

对路径 $j$，RV126 使用截至当前收盘的恰好 126 个裸账簿日收益、样本标准差 `ddof=1` 与 `sqrt(252)` 年化：

\[
\sigma_{j,t}=\sqrt{252}\;sd(r^{book}_{j,t-125},\ldots,r^{book}_{j,t}).
\]

正式连续缩放只由当前可见的 $\sigma_{j,t}$ 决定：

\[
a_{j,t}=\min\left(1,\frac{0.15}{\sigma_{j,t}}\right).
\]

因此 $0<a_{j,t}\le1$；当 $\sigma\le15\%$ 时满仓，当 $\sigma>15\%$ 时严格按 `0.15/sigma` 缩放。不存在 quartile 触发、未来波动、预测器、滞后一日、仓位下限、杠杆、确认期或日内交易。任一正式计划信号的 RV 或 allocation 缺失、非有限或非正时，整次运行必须 fail closed；不能默认 $a=1$ 或沿用旧值。

状态在每日收盘生成供审计，但只在周/月计划信号收盘取样，下一 XNYS 开盘随正常调仓生效，并保持到下一次成功调仓。调仓跳过时继续持有此前实际账簿和此前成功状态，不因当日新状态产生部分交易。

为与 G32 统一诊断，对每条路径额外计算严格滞后 756 日 quartile：

\[
q_{p,j,t}=Q_p(\sigma_{j,t-756},\ldots,\sigma_{j,t-1}),\quad p\in\{.25,.50,.75\}.
\]

它必须有完整 756 个严格此前、有限且正的 RV，并使用 pandas 线性插值。边界为 `Q1: sigma<=q25`、`Q2: q25<sigma<=q50`、`Q3: q50<sigma<=q75`、`Q4: sigma>q75`。Quartile **绝不进入 allocation**；修改 q25/q50/q75 或标签不得改变 $a$。2014 年早期 RV126、allocation 及 quartile 可按各自 warm-up 为空，但所有正式信号必须完整；不得把 warm-up 空值改成满仓。

## 持仓、现金与执行会计

设 G00 基础目标权重为 $w^{G00}_{i,j,t}$，G12 唯一允许的目标变化为

\[
w^{G12}_{i,j,t}=a_{j,t}w^{G00}_{i,j,t}.
\]

- long-only：目标 long/gross/net 均为 $a$，short=0，现金为 $1-a$ 并赚取冻结 T-bill；
- long-short：winner long=$a/2$、loser short=$a/2$、gross=$a$、net=0；会计现金权重仍为 $1-\sum_iw_i=1$，包含卖空抵押并全额赚取 T-bill，不得额外叠加 `1-a`；
- 权重相对于扣除当次成本后的 NAV 建仓，随后随价格自然漂移；
- gross=2 WML 只按 `2*(gross1 return - rf)` 派生非 NAV 算术诊断，不生成 CAGR 或新场景。

信号收盘、下一开盘执行。双边 L1 换手与成本为

\[
L1_u=\sum_i|w^*_{i,u}-w^-_{i,u}|,\qquad Cost_u=NAV^-_u\,(bps/10000)L1_u.
\]

每条核心路径先生成零成本事件路径，再精确回放 0/5/10/20bps；成本和借券费不得反向改变选股、状态、目标或执行身份。年化借券费使用 `(1+b)^(1/252)-1` 的日费率按空头收盘市值扣除。

Long-only 延续 `leave_cash`；long-short 任一必需开盘价缺失时整笔双腿调仓跳过并保持完整账簿，等待公司行动时记录 pending，退出 PIT 的缺价仓位只允许冻结的 terminal last-close 处理。任何跳过必须零换手、零成本并在后续计划调仓恢复。

## 路径、场景与主口径

G12 恰有 `3 signals × 3 K × 2 frequencies × 2 modes = 36` 条核心路径：

| 模式 | 核心路径 | 成本场景 | 借券费场景 | 报告场景 |
|---|---:|---:|---:|---:|
| Long-only | 18 | 0/5/10/20bps | 0% | 72 |
| Long-short | 18 | 0/5/10/20bps | 0%/1%/3% | 216 |
| 合计 | 36 | — | — | 288 |

主场景固定为周频 10bps、月频 5bps；long-short 主借券费 1%，因此恰有 36 个主场景。所有 288 个场景必须保留且身份唯一。

`comparison.csv` 固定报告全部 288 场景相对同键 G00 的 CAGR、T-bill 超额 Sharpe、最大回撤、年化波动和年化 L1，共 `288×5=1,440` 行。正式正文判断只用 36 个主场景，并完整展示 18 个 LO 的全体、按信号、K、频率及 18 个 LS。

判定分三层：

1. **运行有效**：全部输入、因果、identity、会计、计数与哈希验收通过；
2. **机制成立**：LO 满足预注册 H1；单点、WML、危机或事后比较不替代；
3. **数值部署候选**：某个 LO 主场景同时严格满足 `CAGR>SPY CAGR`、`Sharpe_excess_rf>1`、`MDD>-25%`。即使通过，`formal_run_eligible=false` 仍禁止部署表述。

## 预注册诊断与解释层

每个场景报告 total return、CAGR、波动、zero-RF/T-bill Sharpe、Sortino、MDD、回撤持续期、Calmar、beta、alpha、tracking error、IR、算术/几何超额、目标与实际敞口、allocation、换手、成本、借券、现金及执行事件。

主场景条件诊断固定为：

- 按信号日 Q1–Q4，对 G12 与 G00 计算本次成功执行 postcost NAV 到下一计划调仓前 pretrade NAV 的非重叠事件收益；报告 n、均值、中位、胜率、ES10、ES5、最差；skip 单列；
- 每个 quartile 报告 RV126、a、`a×RV126` 的均值、分位数、最小/最大，以及 G12-G00 的 long、short、T-bill、成本、借券和 action/execution bridge P&L；active-state P&L 与事件收益口径不得混称；
- 报告 `a<1` 的日度与调仓 episode、进入/退出、持续长度；
- 四个固定窗口：2018-09-21..2018-12-24、2020-02-19..2020-03-23、2020-03-24..2020-06-30、2022-01-03..2022-10-12。每窗完整报告 G12/G00 收益、波动、MDD、最差日、beta、a、低于满仓日/调仓、Q1–Q4 日/调仓、L1、成本、借券及各腿贡献；
- G12 completed 后，报告 G12-G11（同动作风险源差异）与 G12-G32（同风险源动作差异）的 36 主场景指标、共同信号 allocation/state 关系、危机时点和腿归因。两者不得写入正式 comparison 或改变 H1。

G00/G11/G32 若未原生持久化某项腿贡献，允许用与 G12 完全相同的 balance-flow identity 只读重建，但必须标注为重建并以冻结证据日约束 bridge。

## 输出合同

completed bundle 必须包含：

- `summary.csv`：288 个唯一场景；
- `comparison.csv`：1,440 行正式 G12-G00 五指标比较；
- `config_resolved.toml`：展开 program、G12、运行参数、数据与 G00 reference 锚；
- `manifest.json`：状态、formal blockers、输入/代码哈希、计数、schema、限制和全部文件 bytes/SHA；
- `artifacts/nav.parquet`：614,592 行，含完整 daily P&L 组件与 closure；
- `rebalances.parquet`、`holdings.parquet`、`trades.parquet`：36 个主场景的完整信号、状态、目标和执行审计；
- `diagnostics.parquet`：场景、P&L、gross=2、裸账簿和每日风险状态。`daily_naked_regime` 恰有 `36×3,018=108,648` 行，保存 date、book return、RV126、lagged q25/q50/q75、quartile、a、`a×RV`、cap；每路径独立，不要求跨路径相同。

早期 warm-up 空值必须与滚动窗口逐位一致；所有正式信号行必须完整。大型 Parquet 只留本地运行区，Git/OneDrive 只允许在验收后发布 summary、comparison、resolved config、manifest 与最终小型文档。

## 运行验收

正式写出 completed bundle 前必须同时满足：

1. 本地 runtime、冻结数据、FROZEN、G00 manifest 及其 8 个记录全部 bytes/SHA 通过；G12 full bundle 不得写入 repo。
2. `program.toml`、`G12.toml`、本设计、冻结记录、数据 manifest、G00 manifest 与运行代码哈希写入 provenance；G11/G32 manifest 不得成为 runtime reference。
3. 每路径裸账簿为 2014-06-30 起的连续权威日历、0bps/0借券/0现金息/未缩放；除冻结的月频首日全现金 0 外不得补值。
4. RV126 必须与直接 rolling(126).std(ddof=1)*sqrt(252) 逐位一致；严格 shift(1)-rolling756 quartile 逐位一致；未来收益扰动不得改变既往状态。
5. 每个正式信号满足 `a=min(1,.15/RV126)`，`0<a<=1`；quartile 改动不得改变 a；`a×RV<=15%` 仅为风险源算术，不保证组合实现波动。
6. G12/G00 的信号、执行日期/状态、选股 SID、缺价、公司行动和 holding keys 逐位相同；G12 权重仅为 G00×a。skip、leave_cash 与 terminal 状态通过同等级审计。
7. 恰有 36 核心、72 事件路径、288 有效场景、36 primary、1,440 comparison；LO/LS 为 72/216；每场景 2,134 日，NAV 共 614,592 行。
8. 同一核心路径跨成本/借券费的状态、目标与执行身份逐位相同；成本回放、借券、T-bill、cash、gross/net、L1 和 daily P&L 闭合。无冻结证据日的 unexplained bridge 不得超过 `1e-10`，closure 不得超过 `1e-12`。
9. 每路径 daily regime 恰 3,018 行；stored book return、RV、quartile、a、scaled RV 与 cap 均可独立复算，warm-up 缺失模式精确，正式信号零缺失。
10. 九个文件齐全，schema、相对路径、bytes、SHA 全通过；相同 run id 必须在加载大数据前以 `FileExistsError` 拒绝覆盖。只有全部通过才能标记 completed。

## 失败解释纪律与输出治理

- 数据、哈希、因果、状态、identity、会计、计数或产物任一失败，运行无效；修复必须使用新 immutable run id。
- 运行有效但 LO 未达到 H1，即记为失败或局部证据。WML、G11/G32、最佳单点或危机窗口不能改写结论。
- 若回撤/波动改善但 CAGR、Sharpe、beta 或反弹代价过大，明确写保险成本过高；若零成本有效而主成本失效，写实施失败。
- G12 相对 G11/G32 的优势只能解释风险源或动作；不能把完成后的信息回填到本设计，也不能改变 G13。
- 完整 bundle 写入本地 `results/experiments/G12/runs/g12-frozen-v3-v1/`。只有 completed bundle 通过全部验收后才能创建 `report.md` 和 `results/published/G12/` 四个精简文件；bundle 不得覆盖，重跑必须新 run id。
