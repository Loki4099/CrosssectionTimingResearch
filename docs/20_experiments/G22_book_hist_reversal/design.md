# G22：裸动量账簿历史波动率严格 Q4 切换反转——实验设计

状态：**设计已冻结；尚未运行。** 建议首个不可变运行 ID 为 `g22-frozen-v3-v1`。本文在任何 G22 冻结输出、正式 bundle 或结果报告产生前，冻结研究问题、风险状态、反转动作、判定规则、会计与输出合同。运行后不得原地修改本设计以迁就结果；失败修复必须使用新 run ID。

## 研究问题与假设

G21 表明：用 SPY RV21 的严格 Q4 直接切换反转，long-only 是稳定失败的负对照，而周频 WML 存在 loser/short 腿修复机制。G22 只把风险源换成匹配 G00 裸账簿的 RV126，检验路径级风险状态能否更准确地识别动量组合自身的崩溃区间；反转窗口、成本、信号、股票池和执行全部保持不变。

G11–G13 连续缩放阶段已结束。本次在任何 G22 结果产生前按编号选择 G22，是 G22/G23 阶段的前瞻执行决定；不得追溯声称旧计划已冻结两组内部顺序，也不得根据 G21、G32 或 G12 的结果改变以下参数与门槛。

预注册假设：

1. **H1-LO**：相对同键 G00，G22 应在 long-only 主场景中形成跨参数的 CAGR、T-bill 超额 Sharpe 与最大回撤联合改善；否则继续记为 direct-reversal 负对照。
2. **H1-LS**：相同规则应在 dollar-neutral WML 中形成跨参数三项联合改善，支持“路径级高风险状态下切换 loser/short 腿”的机制。
3. **H2**：book RV126 与 SPY RV21 的进入时点不同；completed 后可与 G21 作描述性同键比较，但 G21 不是 G22 runtime reference。
4. **H3**：Q4 中纯反转相对裸动量必须改善均值和左尾；若只提高均值却恶化 ES10/ES5/最差持有期，不构成稳健机制。

每个模式单独判定：36 个主场景中至少 24 个同时严格满足 `ΔCAGR>0`、`ΔSharpe>0`、`ΔMDD>0`，周/月各至少 10/18，且三项全体中位 delta 均严格为正，才称为跨参数平台支持。MDD 以负数保存，正 delta 表示改善。不得用 rev5/rev20、TopK、信号、频率或最佳单点事后挑赢家；LS 成功不能挽救 LO 失败。

## 冻结输入与范围

- 数据版本：`sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate`；状态永久保留 `review / free_research_candidate / formal_run_eligible=false`。
- 冻结记录 SHA256：`a3ef9ee72cd3d535c2e5bf06b3d1f520c54667a8552891543ee0f9ca50488296`；dataset manifest SHA256：`65b628d604f7e2f456e8d1d43a3c3e88b6bd3e86cc1c9455cdcfe28b856a3ec7`。
- G22 运行时配置 SHA256：`ce332c96ab5907ba72bf825b18271678234552e08c5b1de30d62e42fc9cd71dd`；程序配置 SHA256：`5d10ab208eec672f0258893391e3c58af402cab64834653570ccee12996a7bf9`。
- 唯一正式 reference：`g00-frozen-v3-v1`，manifest SHA256 `8b875d4bcbb7b178b309c7b1edaa7dce9bbb15090e68b619fb045cec35411c66`。G12/G21/G32 只允许在 G22 completed 后进入报告解释，不得成为 runtime 输入或 completed 门禁。
- 评价期：2018-01-02 开盘至 2026-06-30 收盘，共 2,134 个 XNYS sessions；风险历史起点 2014-06-30。
- 基线信号：`mom_255_0`、`mom_255_21`、`mom_12_1`；K=10/20/50；周/月；LO/LS；反转窗口 L=5/20。
- 股票池、canonical SID、PIT、公司行动、缺价、成本、借券、现金与执行规则完全复用 G00；策略排除清单为空。
- 唯一参数：`book_realized_vol_window=126`、`state_history_sessions=756`、`high_vol_quantile=.75`、`state_rule=strict_q4_no_hysteresis`、`book_source=matching_G00_portfolio_mode`。不得搜索其他窗口、阈值、确认、hysteresis 或反转窗口。

## 匹配 G00 的裸账簿风险源

每个 `signal × K × frequency × portfolio_mode` 基础路径只生成一次匹配 G00 的裸账簿收益 `r_book(j,t)`；rev5/rev20 必须共享完全相同的裸收益、RV126、quartile 和 Q4 标签。

- LO 裸目标 gross=net=1；LS winner `+0.5`、loser `-0.5`、gross=1、net=0；
- 固定 0bps 成本、0% 借券、0 现金息；不读取 G12/G21/G22/G32 的收益、状态或压力场景；
- 风险输入严格覆盖 2014-06-30..2026-06-30 的 3,018 个权威 sessions；周频正常从历史起点运行；月频 2014-06-30 尚未执行首个评价内计划，允许该日为零息全现金收益 0，随后必须连续；
- 任何缺失、非有限或 `<=-100%` 收益必须 fail closed。

RV126 使用当前收盘可得的 126 个日简单收益、样本标准差 `ddof=1`、年化 `sqrt(252)`：

\[
RV_{j,t}=\sqrt{252}\;sd(r_{j,t-125},\ldots,r_{j,t}).
\]

当前 `RV(j,t)` 与严格排除当前值的过去 756 个 RV 比较：

\[
q_{p,j,t}=Q_p(RV_{j,t-756},\ldots,RV_{j,t-1}),\ p\in\{.25,.50,.75\}.
\]

必须恰有 756 个严格此前、有限且正的 RV，采用 pandas 线性分位。Q1 `RV<=q25`，Q2 `q25<RV<=q50`，Q3 `q50<RV<=q75`，Q4 `RV>q75`；等于 q75 不进 Q4。状态在信号收盘计算，下一 XNYS 开盘执行；不得读取未来收益或把状态滞后一日。

## Q4 直接切换反转

对每个计划信号日 t：

- Q1–Q3：使用该路径原始动量得分；
- Q4：用 `rev_L(i,t)=-log(TR_i(t)/TR_i(t-L))` 完全替换动量得分，L 固定为 5 或 20；
- 高分为近期 loser；LO 买 TopK loser，LS 做多 TopK loser、做空 BottomK recent winner；
- 不混合、加权或平滑动量与反转；不设确认期、退出滞后带或仓位缩放；Q4 以外不得读取反转分数影响选择。

时点严格为信号收盘计算、下一开盘成交。因选择可能改变，G22 不要求 Q4 holdings 等于 G00；但 Q1–Q3 的请求 SID/目标权重必须与同键 G00 一致，Q4 的请求 SID 必须与同 lookback 的纯反转排名一致。相同 `K/frequency/mode/lookback` 的三个动量定义在 Q4 必须产生相同反转选择。rev5/rev20 的风险状态必须逐位相同。

## 会计、成本与执行

- LO：TopK 等权、gross=net=1，延续 `leave_cash`；
- LS：TopK loser `+0.5`、BottomK recent winner `-0.5`，gross=1、net=0；会计现金为 `1-sum(w)=1` 并按冻结 T-bill 计息；
- 双边 `L1=sum|w*-w-|`，成本 `pretrade_NAV × bps/10000 × L1`；先生成零成本事件路径，再精确回放 0/5/10/20bps；
- 年借券费 0/1/3%，按 `(1+b)^(1/252)-1` 从空头收盘市值扣除；
- LO 主成本周10/月5bps；LS 主成本相同且主借券1%；
- LS 必需开盘价缺失时整笔双腿跳过并维持持仓；pending corporate action、terminal last-close 与恢复要求同 G00；任何 skip 必须零换手、零成本。

每日 P&L 必须闭合为 long risk、short risk、T-bill、交易成本、借券费、action/execution bridge 与 unexplained bridge。无冻结证据的 unexplained bridge <=1e-10，closure <=1e-12。

## 路径、场景与正式比较

G22 恰有 `3 signals × 3 K × 2 frequencies × 2 modes × 2 reversal lookbacks = 72` 核心路径：

| 模式 | 核心路径 | 成本 | 借券 | 报告场景 |
|---|---:|---|---|---:|
| LO | 36 | 0/5/10/20bps | 0% | 144 |
| LS | 36 | 0/5/10/20bps | 0/1/3% | 432 |
| 合计 | 72 | — | — | 576 |

主场景 72 个；NAV 固定 `576×2,134=1,229,184` 行。`comparison.csv` 对每场景报告 G22-G00 的 CAGR、T-bill 超额 Sharpe、MDD、年化波动、年化 L1，共 2,880 行。正式 G00 parent 忽略 reversal variant，只匹配 `signal/K/frequency/mode/cost/borrow`。

## 预注册诊断

- 完整 72 主场景及 signal/K/frequency/rev5-rev20 分组；
- 按 risk Q1–Q4 报告同键 G00 momentum、纯反转和实际 G22 的非重叠持有期 n、均值、中位、胜率、ES10、ES5、最差；skip 单列；
- 四个固定危机窗口：2018-09-21..2018-12-24、2020-02-19..2020-03-23、2020-03-24..2020-06-30、2022-01-03..2022-10-12；报告收益、vol、MDD、最差日、beta、Q1–Q4 日/调仓、L1、成本、借券与逐腿贡献；
- 报告 Q4 episode 进入/退出/持续期、rev5/rev20 选择重合和 winner/loser 腿；
- G22 completed 后才可与 G21、G12、G32 比较状态重合、危机时点与经济结果；不得改写 G22-G00 判定。

部署诊断继续严格报告 LO 的 `CAGR>SPY CAGR`、`Sharpe>1`、`MDD>-25%` 联合条件；即使通过也不改变 `formal_run_eligible=false`。

## 输出合同与验收

completed bundle 固定 9 文件：`summary.csv`、`comparison.csv`、`config_resolved.toml`、`manifest.json` 与五个 Parquet：`nav`、`rebalances`、`holdings`、`trades`、`diagnostics`。大型文件只留本地 runtime；验收后 Git/OneDrive 只发布前四个精简文件和小型文档。

写出 completed 前必须全部满足：

1. 本地 runtime、dataset/FROZEN、G00 manifest 及其 8 个 records 的 bytes/SHA 全通过；bundle 不写入 repo。
2. program、G22 config、本设计、FROZEN、dataset manifest、G00 manifest 与运行代码哈希写入 provenance；G12/G21/G32 不得成为 runtime reference。
3. 36 个唯一裸账簿各 3,018 权威日；72 策略持久化 `72×3,018=217,296` daily risk rows，rev5/rev20 状态逐位相同。
4. RV126、严格 shift(1)-rolling756 quartile、当前收盘因果和未来扰动测试逐位通过；正式信号零缺失。
5. score switch 在 Q1–Q3 逐位等于 momentum、Q4 逐位等于对应 pure reversal；Q1–Q3 G00 identity 与 Q4 跨 signal reversal identity 通过。
6. 同策略跨成本/借券的状态、选择、执行身份与目标逐位相同；成本、借券、T-bill、cash、gross/net、L1 和 P&L 闭合。
7. 恰 72 core、144/432 场景、576 valid、72 primary、2,880 comparison、1,229,184 NAV；每场景恰 2,134 日。
8. 9 文件 schema、相对路径、bytes、SHA 全通过；同 run ID 必须在加载大数据前以 `FileExistsError` 拒绝。

任一输入、因果、选择、会计、计数或哈希失败，运行无效且不得创建报告/发布目录；修复必须使用新 immutable run ID。运行有效但模式未达平台门槛，必须如实记为失败或局部机制，不能事后挑参数。
