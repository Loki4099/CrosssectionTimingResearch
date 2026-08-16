# G23：裸账簿预测波动率严格 Q4 切换反转——实验设计

状态：**设计已冻结；尚未运行。** 首个不可变运行 ID 固定为 `g23-frozen-v3-v1`。本文在任何 G23 输出、正式 bundle 或结果报告产生前冻结研究问题、预测状态、反转动作、判定、会计与输出合同；运行后不得为结果修改设计。

## 研究问题与预注册假设

G23 是动作 × 风险源九宫格的最后一格。它保持 G21/G22 的严格 Q4 direct reversal 动作，只把风险源换成与每条 G00 裸账簿匹配的因果 EWMA 21-session 预测波动率，检验更快的路径级预测状态能否在反转前及时识别 momentum crash。

1. **H1-LO**：相对同键 G00，long-only 应跨参数同时改善 CAGR、T-bill 超额 Sharpe 与最大回撤；否则记为 direct-reversal 负对照。
2. **H1-LS**：相同规则应在 dollar-neutral WML 中形成跨参数三项联合改善。
3. **H2**：预测状态与 G21/G22 的历史状态进入时点不同；只允许 completed 后作描述性比较。
4. **H3**：Q4 direct reversal 必须同时改善持有期均值和 ES10/ES5/最差值；只改善均值不构成稳健机制。

每个模式单独判定：36 个主场景中至少 24 个严格满足 `ΔCAGR>0`、`ΔSharpe>0`、`ΔMDD>0`，周/月各至少 10/18，且三项全体中位 delta 均严格为正，才称平台支持。LS 不能挽救 LO；不得事后挑 rev5/rev20、频率、K 或信号。

## 冻结输入

- 数据：`sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate`；dataset manifest `65b628d604f7e2f456e8d1d43a3c3e88b6bd3e86cc1c9455cdcfe28b856a3ec7`；FROZEN `a3ef9ee72cd3d535c2e5bf06b3d1f520c54667a8552891543ee0f9ca50488296`。
- G23 config SHA256：`29a79c3e57a15ce73dda3c6ebe5efc39b4b50ec210ea6c0d3ff85456499146c8`；program SHA256：`11394af02fa028abe4a11434874be31e33e692f55feb73e9236da9bf8d07d413`。
- 唯一 runtime reference：`g00-frozen-v3-v1`，manifest `8b875d4bcbb7b178b309c7b1edaa7dce9bbb15090e68b619fb045cec35411c66`。G13/G21/G22/G33 只允许 completed 后解释，不得成为输入或 completed 门禁。
- 评价期 2018-01-02 open..2026-06-30 close，2,134 XNYS sessions；预测风险输入严格从 2014-06-30 开始，共 3,018 sessions。
- `3 signals × 3 K × weekly/monthly × LO/LS × rev5/rev20 = 72` 核心路径；成本、借券、现金、公司行动、缺价与执行全部复用 G00。

## 匹配 G00 的裸账簿预测状态

每个 `signal × K × frequency × mode` 基础路径只生成一次零成本、零借券、零现金息裸账簿；rev5/rev20 必须共享完全相同的 returns、EWMA、forecast sigma、quartile 与 Q4。

- LO gross=net=1；LS winner `+0.5`、loser `-0.5`、gross=1、net=0。
- weekly 在 2014-06-30 正常起始；monthly 必须从最近的 pre-t0 计划执行建立真实持仓，再严格切片 2014-06-30..2026-06-30。不得用人工零收益填充 t0。
- `r(t0)` 必须有限、`>-1` 且非零；`v(t0)=r(t0)^2>0`。之后 `v(t)=0.94 v(t-1)+0.06 r(t)^2`。
- 21-session 条件方差为 `21v(t)`，年化预测 sigma 为 `sqrt((252/21)×21v(t))=sqrt(252v(t))`。
- 任何缺失、非有限、`<=-100%` return 或非正 variance/sigma 必须 fail closed；改变 t0 前收益不得改变 v(t0)，改变未来收益不得回写过去状态。

当前 forecast sigma 与严格排除当前值的过去 756 个 sigma 比较，pandas 线性分位：Q1 `sigma<=q25`，Q2 `q25<sigma<=q50`，Q3 `q50<sigma<=q75`，Q4 `sigma>q75`。必须恰有 756 个严格此前、有限且正的状态；等于 q75 不进 Q4。状态在信号收盘计算，下一 XNYS open 执行。

## Q4 direct reversal

- Q1–Q3 完整使用原始 momentum score；
- Q4 完整替换为 `rev_L(i,t)=-log(TR_i(t)/TR_i(t-L))`，L=5 或 20；
- LO 买 TopK recent losers；LS 多 TopK losers、空 BottomK recent winners；
- 不混合、缩放、平滑、确认或滞后；Q4 以外反转分数不得影响选择。

Q1–Q3 请求 SID 与请求敞口必须逐位等于 G00。Q4 score 必须逐位等于对应 pure reversal；同 `K/frequency/mode/lookback` 的三个 momentum definitions 在共同 Q4 日必须有相同请求选择。成本/借券变化不得改变状态、选择或目标。

## 会计与场景

- LO TopK 等权，gross=net=1，缺开盘按 `leave_cash`；
- LS loser long +0.5、winner short -0.5，gross=1、net=0，cash accounting=1 并赚冻结 T-bill；
- 成本 0/5/10/20bps，周主10/月主5；LS 借券 0/1/3%，主1%；先生成零成本事件路径，再精确回放成本；
- signed missing open 整笔跳过并维持持仓；skip 必须零换手、零成本；
- 每日 P&L 闭合 long/short/T-bill/cost/borrow/action bridge/unexplained bridge；无证据 unexplained <=1e-10，closure <=1e-12。

| 模式 | 核心路径 | 场景 |
|---|---:|---:|
| LO | 36 | 144 |
| LS | 36 | 432 |
| 合计 | 72 | 576 |

主场景 72；NAV `576×2,134=1,229,184` 行；comparison 对每场景报告 5 个 G23-G00 指标，共 2,880 行。正式 parent 忽略 reversal variant。

## 预注册诊断

- 72 主场景以及 signal/K/frequency/rev5-rev20 完整分组；
- Q1–Q4 持有期 n、均值、中位、胜率、ES10、ES5、最差与 skip；
- 固定窗口：2018-09-21..2018-12-24、2020-02-19..2020-03-23、2020-03-24..2020-06-30、2022-01-03..2022-10-12；报告 return/vol/MDD/worst/beta/Q4 日与调仓/L1/cost/borrow/各腿；
- Q4 episodes、rev5/rev20 选择重合、forecast calibration（只作 completed 后描述，严禁成为输入）；
- completed 后与 G13/G21/G22/G33 比较状态重合和经济结果，不改变 G23-G00 判定；
- LO 部署联合条件：`CAGR>SPY CAGR`、`Sharpe>1`、`MDD>-25%`。

## 输出与验收

completed bundle 恰 9 文件：两张 CSV、resolved TOML、manifest 与 nav/rebalances/holdings/trades/diagnostics 五个 Parquet。大型结果只留本地 runtime；验收后仓库仅发布前四个精简文件。

写出前必须同时通过：dataset/FROZEN/G00/design/config/program 与 runtime code hashes；G00 为唯一 prior runtime input；36 个唯一裸账簿、72×3,018 daily states、rev5/rev20 state identity；EWMA direct recursion、current-close causality、strict shift(1)-rolling756；score switch、G00 non-Q4 identity、Q4 cross-signal identity；跨成本/借券 identity 与 P&L closure；72 core、576 valid、72 primary、2,880 comparison、1,229,184 NAV；9 文件 bytes/SHA；同 run ID 在加载大数据前拒绝。

任一门禁失败不得发布或写报告；若 bundle 已存在则保留审计证据并使用新 run ID。结果有效但未达平台门槛，必须如实记为失败或局部机制。
