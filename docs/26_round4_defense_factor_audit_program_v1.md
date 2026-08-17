# Round 4：原目标单因子扩展、target 审计与熊市事件图谱计划 v1

- 状态：`plan_frozen_r4a_data_only`
- 程序 ID：`defense_factor_audit_round4_v1`
- 冻结日期：2026-08-17
- 证据等级：免费数据、开发期研究，`formal_eligible = false`

## 1. 本轮决策

Round 4 是 Round 2/3 停止后的独立研究程序，不重开旧实验，也不改写其失败结论。本轮按用户在结果前作出的决定依次完成：

1. 以原始一周二分类 target 为统一基准，把有限、预先登记的单因子全部跑完；
2. 对该 target 本身做 candidate-independent sanity-check；
3. 建立 1993–2021 SPY 大回撤与反例事件图谱，观察因子在事件前后的因果轨迹；
4. 强制停止并形成 decision memo，再由用户决定是否另立新 target、多因子模型或其他研究。

本轮不研究 `mom_255_0`、横截面选股、债券品种、最优现金比例、复杂模型、多 target 投票或 2022–2026 锁箱。50% 防守只是统一测量尺，不是本轮要优化的策略参数。

## 2. 两道冻结锁与授权

本计划采用两阶段冻结，防止先看到 target 或事件结果再改因子：

### 2.1 当前 `PLAN_LOCK`

当前锁固定研究问题、20 个候选 arm、数据分支、原 target、统一动作、target 审计和事件算法，只授权 `R4A_FREE_FACTOR_DATA`：

- 可以获取、缓存、清洗、对齐和 QA 免费数据；
- 可以构造不含未来 outcome 的因子输入序列和 availability 表；
- 可以决定某个已登记数据分支是 `eligible`、`descriptive_only` 或 `invalid_data`；
- 不得计算或读取 R4 的 T1/T2/T3、信号能力、策略 NAV、事件 outcome 或因子排名。

当前授权矩阵为：`data=true`，`target_materialization=false`，`signal_evaluation=false`，`event_outcomes=false`，`lockbox=false`，`mom255_transfer=false`，`models=false`。

### 2.2 后续 `PREREG_LOCK`

R4A 完成并冻结数据 manifest 后，必须在不知道 R4B–R4D 结果的前提下生成第二道锁，至少包含：

- 精确 source snapshot、许可、下载请求、原始与整理后哈希；
- 每个 arm 的 resolved eligibility、有效区间与缺失周；
- 公共周历、R2 fold 锚及可比较的共同周 mask；
- R4B、R4C、R4D 的独立 `design.md` 与机器配置；
- 所有 quantile、ties、成本、输出 schema 和失败状态。

只有第二道锁逐字节验收后，R4B–R4D 才一次性获得 development 授权；三批都要完成，不因 R4B 有无正结果提前停止。

## 3. 历史边界与不可变输入

R4 只读复用下列已冻结锚，不得原地修改：

- R2A snapshot：`r2a-long-free-20260816-v1`；
- R2A manifest SHA256：`071055016268d83f60a03b70be498d85da07897d290b049e4ed7524d1b9e674c`；
- R2A tree SHA256：`6985176ea1088d70c0191d6e24527dc7117e66ce81a1c0ece7ad7f539ed061ce`；
- R2 folds：`config/experiments/round2/folds.json`；
- folds SHA256：`e0a18efcd533bd1e836cde4a8e9e9bc3dd0c343eb690b5a7ccc384093bf7c53c`；
- R2B design SHA256：`adb5c56bd793e9a5b747ebd5ce5eff5f0c855fa8a1341d71c4ced47214b8129c`。

开发期沿用 execution-year 2005–2021。最大可评分信号日为 2021-12-23；锁箱从 2021-12-31 signal / 2022-01-03 execution 开始。R4 不生成 2022-01-03 以后信号对应的 target、预测、状态、NAV 或事件结论。跨越该边界的 4/13/26 周诊断标签一律右删失。

这仍是被先前研究观察过的开发证据，不是纯净外部 OOS；锁箱继续保持 `closed`。

## 4. 固定周历、执行与原 target

令 `s_t` 为 W-FRI 周期内最后一个 XNYS session 的收盘信号时点，`e_t` 为紧随其后的下一 XNYS session 开盘，`e_(t+1)` 为下一周计划执行开盘。节假日由冻结 XNYS 日历决定，不硬编码周五或周一。

一周 SPY 相对现金对数超额收益为：

```text
T1_t = log(SPY_TR_open[e_(t+1)] / SPY_TR_open[e_t])
       - sum(log(1 + RF_d), d in [e_t, e_(t+1)))
```

唯一正式筛选 target 为：

```text
T2_t = 1[T1_t < 0]
```

`T1_t = 0` 归入 risk-on。成本不进入标签。R4B 不得改成收益阈值、分位数标签、MAE、MDD 或多期限投票；T1 只报告幅度和排序，不产生独立赢家。

原 R2B 的四周路径 target 仅作为 target 冲突诊断之一，不能补救 T2 失败。13/26 周路径也只能在 R4C 使用，不能用来重新训练或重跑因子。

## 5. 有限候选因子目录

“把所有单因子跑完”在本计划中只指 [`factor_catalog_plan.csv`](../config/experiments/round4/factor_catalog_plan.csv) 的 20 个 arm：旧 10 个锚 + 新 10 个正交候选。每个 arm 只有一个方向、窗口和变换；不可替补、扫窗、组合或事后新增。

### 5.1 旧 10 个锚

1. `R4B__RV21`：`log(SPY RV21)`；
2. `R4B__RV126`：`log(SPY RV126)`；
3. `R4B__RV_RATIO`：`log(RV21 / RV126)`；
4. `R4B__RET21`：`-(P_t / P_(t-21) - 1)`；
5. `R4B__RET126`：`-(P_t / P_(t-126) - 1)`；
6. `R4B__SMA_GAP`：`-(SMA50 / SMA200 - 1)`；
7. `R4B__DRAWDOWN252`：`-(P_t / max(P_[t-251:t]) - 1)`，252-session 窗口包含当前 signal close；
8. `R4B__DOWNSIDE_VAR63`：63-session downside-variance share；
9. `R4B__SKEW63`：负的 63-session return skew；
10. `R4B__KURT126`：126-session excess kurtosis。

旧 arm 必须用统一 R4 pipeline 重跑；重叠日期的原始 feature、T1 和 T2 要与 R2B 冻结证据逐行一致，否则 R4B 为 `fatal_anchor_mismatch`。

### 5.2 新 10 个候选

1. `R4B__VIX_LEVEL`：`log(VIX / 100)`；
2. `R4B__VIX_RV_GAP`：`(VIX / 100)^2 - RV21^2`，只称 implied–realized variance-gap proxy；
3. `R4B__DOWN_VOLUME21`：21 日下跌方向 dollar-volume / absolute-return-dollar-volume share；
4. `R4B__VOLUME_SHOCK`：`log(mean(DV,21) / median(DV,252))`；
5. `R4B__HY_OAS_LEVEL`：`log(HY OAS)`；
6. `R4B__HY_OAS_CHANGE21`：HY OAS 的 21-session 变化；
7. `R4B__YC_10Y3M`：`-(10Y - 3M)`；
8. `R4B__YC_10Y2Y`：`-(10Y - 2Y)`；
9. `R4B__NFCI`：有历史 vintage 的 NFCI；
10. `R4B__RSP_SPY63`：负的 RSP 相对 SPY 63-session 总回报，只称可投资 participation proxy。

真实 PIT 成分股 breadth 在免费历史数据与成分资格未通过前维持 `blocked_not_an_arm`，不得用当前成分回填。若某个新 arm 数据失败，它仍占登记位置并记为 `invalid_data`，不得换入新因子。

## 6. R4A：免费因子数据门禁

R4A 的详细授权见 [`R4A design`](20_experiments/R4A_factor_data/design.md)。核心规则为：

- 只用免费、可重复获取且许可可记录的来源；暂不考虑 Norgate；
- VIX 优先复用官方 Cboe 历史文件；HY OAS、Treasury 与 NFCI 使用 FRED/ALFRED 可复现记录；SPY volume 与 RSP 必须冻结实际 provider snapshot；
- VIX/RSP/volume 不填补。有效期内缺失不超过 2%，且不得连续超过 4 个计划周；缺分周经济路径沿用上一 overlay 状态，paired control 同步处理；
- FRED daily series 统一保守滞后一个 XNYS session再 as-of，最多 5 sessions staleness；
- NFCI 必须使用 release/vintage-as-of 值，最多 carry 14 calendar days；
- pre-inception 不算缺失，但不能回填；
- 任何未来数据改变过去 feature、两次独立构建哈希不同、来源/调整/available-at 不闭合，均使对应 arm `invalid_data`；
- target 与 event outcome 不得在 R4A 物化。

R4A 结束时只输出数据 manifest、源与许可清单、QA 表、因子 eligibility 和共同周 mask 候选，不输出收益能力或排名。

## 7. R4B：原 T2 单因子统一参考实验

### 7.1 信号映射

所有 score 预先定向为“越高越应防守”。在每个 outer execution year 开始前，只使用此前已经成熟且有效的历史 score，按 empirical quantile `linear` 方法计算 q75，全年冻结：

```text
score > q75  -> 50% SPY / 50% RF
score <= q75 -> 100% SPY
```

等号 risk-on。每个 arm 至少要有 260 个过去有效周才能开始；缺失周不产生新信号和 overlay 交易，持有上次状态。首个可执行状态之前为 100% SPY。禁止因子专属阈值搜索或二值化变体。

### 7.2 会计与成本

- 信号在 `s_t` 收盘计算，`e_t` 开盘执行；
- 每个有效周在执行开盘恢复目标股票权重；
- 无 short、无杠杆；现金按同一 RF 路径计息；
- 主成本 10 bps / 每美元实际 SPY risky turnover；0/5/20 bps 仅作敏感性；
- 全 OOS 路径只在首个执行开盘从现金启动一次，年度边界 carry NAV 与仓位，不重置；
- 主经济对照为完全相同日历、成本和有效周规则下的 ex-post 同平均股票暴露静态 SPY/RF replay；always-SPY 与 always-cash 为辅助。

### 7.3 评价与参考标签

无概率模型，因此不使用 Brier、Platt 或 calibration gate。每个 arm 报告：

- raw-score T2 ROC-AUC、PR-AUC、alert precision/recall、cash-wins lift；
- score 与 T1 的 time-series Spearman、score quintile 的 T1/T2 单调性；
- 被告警周捕获的负超额收益金额占比；
- 10 bps 动态策略的 CAGR、Sharpe、MDD、turnover；
- 对 same-average-exposure control 的净 timing wealth、逐年贡献与 block interval；
- arm-native 区间与所有合格 arm 的 common-intersection 两套表。

`reference_positive` 只是描述标签，不是冠军或晋级：AUC > 0.5、T1 rho < 0、10 bps 对同暴露终值 > 0、且至少 60% 完整 OOS 年 active contribution > 0。需要至少 8 个完整 OOS 年和 400 个有效周，否则只能 `descriptive_only`。共同交集少于 520 周时只报告 arm-native，不强行排名。

`robust_reference_positive` 还需在第二道锁中冻结的 13-week block interval、FDR 与危机 leave-one-out 门；无论是否出现该标签，都不自动打开模型或 target-v2。

## 8. R4C：candidate-independent target sanity-check

R4C 不按因子分组、不选择赢家，也不改变 T2。固定审计包括：

- 阈值扰动：`T1 < -40/-20/-10/0/+10/+20/+40 bps` 的标签率、年度分布与翻转率；
- T2 的持续串长度、状态转移、自相关和年份稳定性；
- 负收益的金额、尾部集中度，以及 near-zero 周对分类损失的贡献；
- 50% sign-oracle、固定 25% alert-budget oracle 与随机 25% 告警基线在统一成本下的上限；
- T2 与 4/13/26 周 terminal return、forward path MAE 的冲突矩阵；
- 下跌后反弹、上涨中高波和慢熊等典型冲突类型的数量与经济权重。

预定义错位格至少包括 `T2=0 且 MAE13>=10%` 与 `T2=1 且 MAE13<2%`；它们只诊断“下一周方向”和“中期可避免下行”是否错位，不构成替代 target 的通过门。

所有 4/13/26 周标签仅在终点早于 2022-01-03 时生成，其他行右删失。任何 Q25、MAE13、连续损失或多期限投票只可进入最终 decision memo 的“未来候选”，不得在 R4 内重跑因子。

## 9. R4D：SPY 熊市与反例事件图谱

图谱只用 SPY total-return index 的 1993 起始日至 2021-12-31。事件按前向链式、机械规则生成：

1. peak 为当时已知 running high；
2. 从该 peak 到首次恢复该 peak 只算一个 episode；
3. 主事件为 episode 最大回撤不高于 -10%；-15% 与 -20% 是同一 episode 的严重度层级；
4. 保存 peak、first -5/-10/-15/-20 breach、trough、recovery；截至 cutoff 未恢复则右删失；
5. anchor 映射为不晚于该日期的最近 scheduled signal close。

预冻结 sanity expectation 是 1993–2021 约 10 个 -10% episodes、5 个 -15% episodes、3 个 -20% episodes；最终以同一公式独立复算，若不一致必须在运行前解决，不能按图形手工改事件。

每个 eligible factor 画 `[-52,-26,-13,-8,-4,-1,0]` 周的因果 expanding percentile/z-score，以及 breach、trough 和 recovery 附近轨迹。反例固定为：

- 最大回撤介于 -5% 与 -10% 的 shallow episodes；
- record high 后 26 周 MAE < 5% 的 calm peaks；
- 从非事件区机械抽取的 matched normal weeks。

报告事件覆盖率、第一次 alert 相对 peak/first-10 的 lead、非事件区 alert episodes/year、trough 后 clearance lag、误报和 leave-one-event-out。事件而不是重叠周是推断单位；样本太少时只做描述，不做“显著冠军”。2022 熊市不得进入本图谱。

## 10. 输出、状态与硬停止

R4A–R4D 各自产出不可变 bundle、manifest、QA/report；Git 只发布精简 CSV、配置、manifest 和图表，不提交大型市场数据。

R4D 后程序状态必须变为：

```text
completed_pending_user_target_decision
```

并生成唯一 decision memo，区分：

- 哪些因子方向和经济参考一致；
- 哪些只识别中期路径风险、不能识别下一周方向；
- T2 的主要标签缺陷是否来自 near-zero noise、期限错位或反弹；
- 下一份预注册应保持 T2、采用连续 MAE13 / dead-zone、还是采用固定多期限模型。

任何结果都不得自动授权：复杂模型、因子组合、多 target/vote、仓位档位搜索、债券替代、`mom_255_0` 迁移或 2022–2026 锁箱。下一步必须由用户选择并另立计划与哈希锁。

## 11. 明确禁止的研究者自由度

- 不因免费数据失败替换 arm；
- 不看结果后改窗口、方向、变换、阈值或成本；
- 不把多个高度重叠周当独立样本宣称显著性；
- 不用全样本 quantile、z-score 或 event peak 生成可交易信号；
- 不把 RSP/SPY 称为真正 PIT 成分 breadth；
- 不把 VIX–RV proxy 称为严格可交易 variance risk premium；
- 不因某个 factor 在事件图中好看而提升为冠军；
- 不把开发证据描述为正式、外部或未观察 OOS。

本计划是研究治理记录，不构成投资建议。
