# Round 3：非对称防守与重新进攻计划 v1

状态：**开发期机器预注册已冻结；只授权 2005–2021 development，锁箱与动量迁移未授权。**

## 1. 为什么另立新计划

Round 2 已按预注册停止：没有 simple benchmark，RF、XGBoost、HMM、2022–2026 mechanical lockbox 和 `mom_255_0` 迁移均未开放。不能提升 runner-up，也不能把四周风险诊断冒充下一周现金胜出模型的成功。

但已完成证据给出一个更窄的可检验方向：波动率与回撤能描述随后四周的路径危险，却不能稳定预测下一周 SPY 是否跑输现金。Round 3 不重开模型竞赛，而是把一个对称开关拆成两项职责：

1. 风险信号只负责 **何时进入防守**；
2. 独立的价格恢复信号只负责 **何时重新进攻**。

这是受到 Round 1/2 结果启发的开发期研究，不是独立样本确认。

## 2. 唯一研究问题

固定 SPY/RF、周频执行和 50% 防守仓位后，在 RV21 高风险状态中加入一个非对称价格恢复出口，能否：

- 比对称 RV21 防守更少错过上涨；
- 保留大部分回撤保护；
- 在同平均暴露和同实现波动控制下产生正的择时价值？

本计划不研究最佳风险源、最佳均线、最佳确认天数、最佳现金比例或最佳模型。

## 3. 数据与不可见区间

- 唯一数据锚为免费 R2A L 线 `r2a-long-free-20260816-v1`。
- 只在 2005–2021 development outer 区间生成状态、交易、NAV 与结论。
- 2021-12-31 signal / 2022-01-03 execution 起的 235 周仍为 mechanical lockbox；development runner 必须在读入后立即裁掉，并在 manifest 声明没有 lockbox state、prediction、target、NAV 或 metric。
- 原始 R2A 文件物理上含后续价格，不构成授权。代码必须以绝对日期硬门禁，测试必须证明修改锁箱价格不会改变 development 产物。
- 数据仍为免费研究 candidate，`formal_eligible=false`。

## 4. 两个信号的冻结定义

所有状态在每个 `W-FRI` 周期最后一个 XNYS session 收盘后计算，下一 XNYS session 开盘执行；不硬编码周五或周一。

### 4.1 防守进入：SPY RV21 尾部

```text
r_d = TR_close[d] / TR_close[d-1] - 1
RV21_d = sample_std(r_{d-20:d}, ddof=1) * sqrt(252)
q75_d = linear_quantile_0.75(RV21 over the 756 sessions ending d-1)
defense_entry_d = RV21_d > q75_d
```

`q75` 严格排除当前 session；等号不进入；缺失、非有限或非正状态 fail closed。它只负责进入防守，不负责退出。

### 4.2 重新进攻：两日价格恢复

```text
SMA21_d = mean(TR_close[d-20:d])
recovery_d = (TR_close[d-1] > SMA21[d-1]) and
             (TR_close[d]   > SMA21[d])
```

均线包含当日已知收盘，严格大于；等号不通过。只在已处于 `DEFENSE` 时使用。均线长度 21 和两日确认均固定，不搜索。

## 5. 唯一状态机

状态为 `FULL_ARMED`、`DEFENSE`、`RECOVERY_UNARMED`，初始为 `FULL_ARMED`。

1. `FULL_ARMED`：目标 SPY 权重 1.0。若当周 `defense_entry=true`，下一开盘进入 `DEFENSE`，目标权重 0.5。
2. `DEFENSE`：目标权重 0.5。若当周 `recovery=true`，下一开盘进入 `RECOVERY_UNARMED`，目标权重 1.0；否则继续防守。
3. `RECOVERY_UNARMED`：目标权重 1.0。同一高波 episode 内禁止再次减仓；只有当某周 `RV21<=q75` 后才在下一开盘恢复为 `FULL_ARMED`。该次 re-arm 本身不交易。

若防守进入时价格已经在 SMA21 上方，仍先执行一周防守；下一信号满足两日确认时退出，并标记 `vol_only_false_alarm_exit=true`。冲突时进入防守优先，不能同一信号日同时进入和退出。

## 6. 三条经济路径

同一周历、同一会计、同一成本下只生成：

- `ALWAYS_SPY`：100% SPY；
- `SYMMETRIC_RV21`：当周 `RV21>q75` 则 50% SPY，否则 100%；
- `ASYMMETRIC_REENTRY`：第 5 节状态机。

所有路径从 development 首个执行开盘的全现金状态启动一次；年度边界携带 NAV 与持仓，不清仓。每周按目标恢复权重。开盘前按旧持仓估值，随后：

```text
turnover = abs(target_spy_weight - pretrade_spy_weight)
cost = pretrade_nav * cost_bps / 10000 * turnover
```

现金不进 L1，开盘后现金获得当日 RF。主成本 10bp，压力为 0/5/20bp。禁止做空和杠杆。

## 7. 暴露控制与机制归因

对 `ASYMMETRIC_REENTRY` 完整重放两个静态控制：

- development 全段同平均实际 SPY 暴露；
- development 全段同实现 SPY-minus-RF 波动。

控制权重限定 `[0,1]`，使用相同周历、成本和 NAV 引擎。它们是事后诊断，不冒充实时策略。

对每日已执行权重 `a_d` 和 SPY 相对现金的简单收益 `x_d=r_spy,d-rf_d`，报告：

```text
defense_benefit = sum((1-a_d) * max(-x_d, 0))
missed_upside   = sum((1-a_d) * max( x_d, 0))
gross_timing    = defense_benefit - missed_upside
net_timing      = gross_timing - incremental_cost_vs_ALWAYS_SPY
```

同时给出 upside/downside capture、进入/退出/re-arm 日期、防守持续周数、额外换手和逐危机贡献。简单收益和不是复合终值，必须报告复利 reconciliation，不能混称。

## 8. Development 硬门与停止

唯一候选必须同时满足：

### H1：重新进攻有增量价值

- 10bp 下 `ASYMMETRIC_REENTRY / SYMMETRIC_RV21 - 1 > 0`；
- 相对 `SYMMETRIC_RV21` 的 CAGR 改善为正；
- `missed_upside` 至少降低 25%；
- 17 个完整执行年度中至少 60% 的 active log-return contribution 为正。

### H2：不是只提高平均 beta

- 10bp 下相对同平均暴露静态控制终值 >0；
- 相对同波动静态控制终值 >=0；
- `net_timing>0` 且 `defense_benefit/missed_upside>1`；若分母为0则仅在 benefit>0 时记为通过。

### H3：保留防守能力

令 `MDD_gain(strategy)=MDD(strategy)-MDD(ALWAYS_SPY)`，正数表示回撤改善：

- `MDD_gain(ASYMMETRIC_REENTRY)>0`；
- 若 `MDD_gain(SYMMETRIC_RV21)>0`，则前者至少保留后者的 75%；
- COVID selloff 与 GFC 两个预先指定窗口的最差一日和 MDD 不得同时比 `ALWAYS_SPY` 更差。

### H4：不由单一危机或终点制造

- 正年度贡献最大值 / 正年度贡献之和 <=50%；
- 逐一删除 dot-com、GFC、COVID selloff 后，相对同平均暴露 timing value 仍 >0；
- 截止 2021-06-30 的终点敏感性不翻为负。

任一必要门失败即 `completed_no_reentry_candidate`，禁止解封锁箱、改均线、改确认天数、改仓位、追加成交量、替换信号或直接迁移到 `mom_255_0`。

## 9. 单次 mechanical lockbox

只有 development 四组硬门全部通过后，才允许另建 candidate-freeze manifest，并单次运行 2022–2026 锁箱。锁箱使用完全相同的固定规则，不拟合或重选参数。

锁箱点估计必须同时满足：相对对称规则、同平均暴露控制和同波动控制终值不小于0；MDD 仍优于 always-SPY；`net_timing>0`。点估计任一失败即 `failed/no_transfer`；不得提升替代方案。

即使通过，因 2018–2026 已在第一轮研究中被观察，它仍只叫 mechanical/adaptive OOS，不是纯净外部确认。

## 10. 后续边界

- 本批通过后，下一份独立预注册才可把不可变的锁箱/forward 状态序列迁移到 `mom_255_0`。
- 成交量、breadth、VIX、HMM、RF、XGB 和深度模型均不在本批；任何一项都是新的研究臂。
- 不修改 Round 1/2 的冻结设计、bundle 或历史结论。
